# GET /profiles/oidc/userinfo

Returns profile information for the authenticated user. Called once during integration setup to obtain the account ID used as the HA device identifier.

## Request

**URL:** `https://login.postnl.nl/101112a0-4a0f-4bbb-8176-2f1b2d370d7c/profiles/oidc/userinfo`  
**Method:** `GET`

### Headers

| Header | Value |
|--------|-------|
| `Authorization` | `Bearer <access_token>` |

## Response

**Status:** `200 OK`

### Body

```json
{
  "account_id": "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
  "email": "user@example.com",
  "family_name": "Doe",
  "gender": "Male",
  "given_name": "Jane",
  "global_sub": "capture-v1://eu.janraincapture.com/7g3uvwt64vjz9j8jxmw65nczde/user/xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
  "sub": "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
}
```

| Field | Type | Description |
|-------|------|-------------|
| `account_id` | string (UUID) | PostNL account identifier. Used as the HA device identifier and as the prefix for all sensor unique IDs. |
| `email` | string | Account email address. Used as the HA device name. |
| `family_name` | string | Last name |
| `gender` | string | Gender as stored in the account profile |
| `given_name` | string | First name |
| `global_sub` | string | Janrain Capture-scoped subject identifier, scoped to the Capture tenant. Not used by the integration. |
| `sub` | string (UUID) | OIDC subject identifier — the Capture user ID. Distinct from `account_id`. Not used by the integration. |

## How the integration uses this endpoint

- `account_id` → HA device identifier (`{DOMAIN, account_id}`) and unique ID prefix for sensors
- `email` → device `name` in the HA device registry

## Error handling

| Status | Meaning |
|--------|---------|
| `200` | Success |
| `401` | Access token expired or invalid — the integration triggers a token refresh |
