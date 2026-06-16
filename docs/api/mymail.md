# GET /services/serverdrivenui/api/MyMail/letter

Server-driven UI endpoint backing the PostNL app's "Mijn Post" / "My Mail" screen.
Returns the user's announced letter photos for the past ~2 weeks.

## Request

**URL:** `https://jouw.postnl.nl/services/serverdrivenui/api/MyMail/letter`
**Method:** `GET`

### Headers

| Header | Value |
|--------|-------|
| `Authorization` | `Bearer <access_token>` |
| `api-version` | `1.37.0` |
| `os-version` | `35` |
| `app-platform` | `Android` |
| `app-version` | `11.0.1` |
| `content-type` | `application/json` |
| `device-token` | `00000000-0000-0000-0000-000000000000` |

All app-identification headers appear to be required; the endpoint refuses requests that only carry the bearer token.

---

## Response

Server-driven UI payload: a `screen` with `sections`, each containing `items` of different `type`s. The integration looks for items of type `Letter` inside a `Grid` section.

### Example (trimmed)

```json
{
  "screen": {
    "sections": [
      {
        "type": "Grid",
        "items": [
          {
            "type": "Letter",
            "editId": "3SPLGL787437748",
            "title": "16 juni",
            "isUnread": false,
            "image": {
              "url": "https://jouw.postnl.nl/services/mymail/api/image/3F867C7E4F50404A8EFC72EA9C8F971A",
              "loginRequired": true
            },
            "action": {
              "selectedScreenUrl": "https://jouw.postnl.nl/services/serverdrivenui/api/MyMail/letter/3SPLGL787437748"
            }
          }
        ]
      }
    ]
  }
}
```

### Fields used

| Path | Description |
|------|-------------|
| `screen.sections[].items[]` | Walked to find entries where `type == "Letter"` |
| `editId` | Letter identifier (looks like a PostNL-style barcode) |
| `title` | Receipt date in Dutch day-month form, e.g. `16 juni`. No year is provided — the integration infers it from the current date and PostNL's ~2-week retention window. |
| `isUnread` | Whether the user has opened this letter in the PostNL app |
| `image.url` | Photo of the letter. Fetching the URL requires the same bearer token (and MyMail app headers), so it cannot be loaded directly in a dashboard. The integration fetches the bytes itself and exposes them through an `image` entity per letter. |

---

## How the integration exposes letters

The MyMail payload is collected on every coordinator refresh and surfaced through `sensor.<account>_postnl_letters`. State is the total letter count; attributes carry `unread` (int) and `letters` (list).

| Letter field | Source |
|--------------|--------|
| `id` | `editId` |
| `title` | `title` (original Dutch day-month string) |
| `date` | `title` parsed to ISO date (`YYYY-MM-DD`); year inferred from "today" minus 31 days |
| `unread` | `isUnread` |
| `image_url` | `image.url` |

Each letter with an `image_url` also gets its own `image.<account>_postnl_letter_<title>` entity. The entity calls `PostNLJouwAPI.image()` (bearer + MyMail headers) on demand and serves the bytes through Home Assistant's authenticated image proxy, so dashboards never need the raw token. Entities are added and removed together with their letters on each coordinator refresh.
