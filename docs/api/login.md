# Login flow

PostNL uses a multi-step PKCE + Janrain Capture widget flow. There is no simple username/password endpoint — credentials are submitted through Capture's widget API, which returns a short-lived token that is then exchanged for a standard OIDC authorization code.

## Constants

| Name | Value |
|------|-------|
| Tenant ID | `101112a0-4a0f-4bbb-8176-2f1b2d370d7c` |
| OIDC client ID | `bd9f1610-b56d-4e05-a09b-f696f05ddade` |
| Capture client ID | `dkyxkt9x888ye422mawmf769yfm9y44j` |
| Redirect URI | `https://www.postnl.nl/` |
| Scope | `openid poa-profiles-api offline_access` |
| Flow version | `20250910094830574377` *(timestamp-based, may go stale)* |

---

## Step 1 — Open the OIDC authorize endpoint

**URL:** `GET https://login.postnl.nl/{tenant}/login/authorize`  
**Follow redirects:** yes

### Query parameters

| Parameter | Value |
|-----------|-------|
| `client_id` | OIDC client ID |
| `response_type` | `code` |
| `scope` | `openid poa-profiles-api offline_access` |
| `redirect_uri` | `https://www.postnl.nl/` |
| `state` | Random base64url string (24 bytes) |
| `nonce` | Same value as `state` |
| `code_challenge` | SHA-256 of the PKCE verifier, base64url-encoded, no padding |
| `code_challenge_method` | `S256` |

### Result

The server sets a `_csrf_token` cookie and returns the Hosted Login page HTML. The final URL after redirects becomes `login_url`, used as the `Referer` in subsequent requests.

The CSRF token must be extracted from the cookie jar. If it is absent from the jar, it can be found in the page HTML as `aicCsrf: '<value>'`.

---

## Step 2 — Submit credentials to the Capture widget

**URL:** `POST https://login.postnl.nl/widget/traditional_signin.jsonp`  
**Content-Type:** `application/x-www-form-urlencoded`  
**Origin:** `https://login.postnl.nl`  
**Referer:** `login_url` (from step 1)

### Body

| Field | Value |
|-------|-------|
| `utf8` | `✓` |
| `signInEmailAddress` | User's email address |
| `currentPassword` | User's password |
| `capture_screen` | `signIn` |
| `js_version` | `d445bf4` |
| `capture_transactionId` | Random base64url string (30 bytes) — used to poll in step 3 |
| `form` | `signInForm` |
| `flow` | `standard` |
| `client_id` | Capture client ID |
| `redirect_uri` | `{login_url}&socialRedirect=True` |
| `response_type` | `token` |
| `flow_version` | `20250910094830574377` |
| `settings_version` | *(empty string)* |
| `locale` | `en-US` |
| `recaptchaVersion` | `2` |

### Result

An empty or minimal response body. The transaction is processed asynchronously — poll step 3 for the result.

---

## Step 3 — Poll for the Capture result

**URL:** `GET https://login.postnl.nl/widget/get_result.jsonp`

### Query parameters

| Parameter | Value |
|-----------|-------|
| `transactionId` | The `capture_transactionId` used in step 2 |
| `cache` | Current Unix timestamp in milliseconds (cache-buster) |

### Response body (success)

```json
{
  "accessToken": "<capture_access_token>",
  "status": "success"
}
```

| Field | Description |
|-------|-------------|
| `accessToken` | Short-lived Capture access token. Used in step 4. |

If `accessToken` is absent the credentials were incorrect.

---

## Step 4 — Exchange the Capture token for an OIDC auth code

**URL:** `POST https://login.postnl.nl/{tenant}/auth-ui/v2/token-url?{query_string_from_login_url}`  
**Content-Type:** `application/x-www-form-urlencoded`  
**Follow redirects:** no

The query string is copied verbatim from the `login_url` obtained in step 1.

### Body

| Field | Value |
|-------|-------|
| `screen` | `signIn` |
| `authenticated` | `True` |
| `registering` | `False` |
| `accessToken` | Capture access token from step 3 |
| `_csrf_token` | CSRF token from step 1 |

### Result — redirect (most cases)

The server responds with a `302` redirect. The `Location` header is the OIDC `authorize` redirect URL that contains the auth code. Continue to step 5.

### Result — no redirect (loginSuccess screen)

Sometimes the server returns `200` with a page body instead of redirecting. The body contains embedded JavaScript variables:

| JS variable | Description |
|-------------|-------------|
| `screenToRender: '<value>'` | Must be `loginSuccess` |
| `existingToken: '<value>'` | A second Capture token for the loginSuccess screen |
| `aicCsrf: '<value>'` | A refreshed CSRF token |

In this case, make a second POST to the same URL with:

| Field | Value |
|-------|-------|
| `screen` | `loginSuccess` |
| `accessToken` | `existingToken` value |
| `_csrf_token` | Refreshed CSRF token |

This second POST returns a `302` redirect with the auth URL.

---

## Step 5 — Follow the authorize redirect

**URL:** `GET {auth_url}` (Location from step 4)  
**Follow redirects:** no

The server responds with a `302` redirect whose `Location` is the `redirect_uri` with the authorization code appended:

```
https://www.postnl.nl/?code=<auth_code>&state=<state>
```

Validate that the returned `state` matches the value generated in step 1.

---

## Step 6 — Exchange the code for tokens

**URL:** `POST https://login.postnl.nl/{tenant}/login/token`  
**Content-Type:** `application/x-www-form-urlencoded`

### Body

| Field | Value |
|-------|-------|
| `grant_type` | `authorization_code` |
| `client_id` | OIDC client ID |
| `code` | Authorization code from step 5 |
| `redirect_uri` | `https://www.postnl.nl/` |
| `code_verifier` | The PKCE verifier generated before step 1 |

### Response body

```json
{
  "access_token": "<jwt>",
  "refresh_token": "<opaque_token>",
  "expires_in": 3600,
  "token_type": "Bearer"
}
```

| Field | Description |
|-------|-------------|
| `access_token` | JWT used as `Bearer` token for all data API calls |
| `refresh_token` | Opaque token used to obtain a new access token without re-entering credentials |
| `expires_in` | Seconds until the access token expires (typically `3600`) |
| `token_type` | Always `Bearer` |

---

## Token refresh

When the access token expires the integration calls this endpoint directly, skipping the Capture flow.

**URL:** `POST https://login.postnl.nl/{tenant}/login/token`  
**Content-Type:** `application/x-www-form-urlencoded`

### Body

| Field | Value |
|-------|-------|
| `grant_type` | `refresh_token` |
| `client_id` | OIDC client ID |
| `refresh_token` | Stored refresh token |

The response shape is identical to step 6. The integration stores the new `access_token` and `refresh_token` back into the config entry. If the refresh fails, HA triggers a reauth notification.

---

## PKCE verifier and challenge

The verifier is 96 random bytes encoded as base64url without padding (≈ 128 characters). The challenge is the SHA-256 digest of the verifier, also base64url-encoded without padding.

```
verifier  = base64url(random_bytes(96))
challenge = base64url(sha256(verifier))
```
