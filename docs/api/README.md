# PostNL API Reference

This directory contains reference documentation for the PostNL API endpoints used by this integration. Each file documents one endpoint or flow with its URL, authentication requirements, request format, and an annotated example response.

## Endpoints

| File | Endpoint(s) | Description |
|------|-------------|-------------|
| [login.md](login.md) | Multiple — see file | PKCE + Capture login flow; obtain access and refresh tokens |
| [userinfo.md](userinfo.md) | `GET /profiles/oidc/userinfo` | Fetch authenticated user profile |
| [graphql.md](graphql.md) | `POST /account/api/graphql` | `profile` and `trackedShipments` GraphQL queries |
| [track_and_trace.md](track_and_trace.md) | `GET /track-and-trace/api/trackAndTrace/{key}` | Live delivery status for a single shipment |

## Base URLs

| Host | Used for |
|------|----------|
| `https://login.postnl.nl` | Authentication (login, token refresh, userinfo) |
| `https://jouw.postnl.nl` | GraphQL and Track & Trace data |

## Authentication

All data endpoints require a valid OIDC access token obtained via the [login flow](login.md).

The token is passed as a `Bearer` token in the `Authorization` header:

```
Authorization: Bearer <access_token>
```

Access tokens expire after a short TTL. The integration uses the stored `refresh_token` to obtain a new access token automatically. If the refresh token is also expired, HA triggers a reauth notification.
