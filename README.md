# PostNL Parcel Tracker

A custom Home Assistant integration that tracks your PostNL shipments
and announced MyMail letters.

## Contents

- [Features](#features)
- [Requirements](#requirements)
- [Installation](#installation)
- [Configuration](#configuration)
- [Options](#options)
- [Removal](#removal)
- [Sensors](#sensors)
- [Parcel status reference](#parcel-status-reference)
- [Events](#events)
- [Examples](#examples)
- [Debugging](#debugging)
- [Troubleshooting](#troubleshooting)
- [Related integrations](#related-integrations)
- [Disclaimer](#disclaimer)
- [Contributing](#contributing)
- [License](#license)

## Features

- Incoming and outgoing active-parcel count sensors
- Per-parcel sensor per active incoming shipment, with full status details as attributes
- Configurable delivered-parcels sensor (last N days, or N most recent)
- Next delivery datetime sensor (device class `timestamp`)
- PostNL Punt sensor — parcels destined for a PostNL Point pickup location
- MyMail letters sensor plus a per-letter image entity holding the scanned photo
- Automatic lifecycle management — per-parcel sensors are created and removed as parcels move through delivery
- Re-authentication support — silently refreshes the PostNL token, prompts only when the refresh fails

## Requirements

- Home Assistant 2024.7 or newer
- A [PostNL](https://jouw.postnl.nl) account (the credentials you use on jouw.postnl.nl / the PostNL mobile app)

## Installation

### HACS (recommended)

1. Open HACS → **Integrations** → ⋮ → **Custom repositories**
2. Add this repository URL and select category **Integration**
3. Search for **PostNL** and install it
4. Restart Home Assistant

### Manual

1. Copy the `postnl` folder into your `config/custom_components/` directory
2. Restart Home Assistant

## Configuration

1. Go to **Settings → Devices & Services → Add Integration**
2. Search for **PostNL**
3. Enter your PostNL **email address** and **password**
4. Choose how you want the **delivered parcels** sensor to filter (last N days, or N most recent)
5. Click **Submit**

### Setup parameters

| Field | Description |
|---|---|
| Email | The email address of your PostNL account. |
| Password | The password for that account. Stored in the HA config entry and refreshed automatically when the integration triggers a re-authentication. |

## Options

Click **Configure** on the integration card. The form is split into two
sections:

### Delivered parcels

| Option | Description |
|---|---|
| Filter by | `Days` keeps delivered parcels visible for the last N days. `Number of parcels` keeps only the N most recent regardless of age. |
| Amount | The N used by the filter above. |

### Polling

| Option | Description |
|---|---|
| Refresh every | How often the integration checks PostNL. Choices: **15 / 30 / 60 / 120 / 240 minutes** — default 30. A slower interval is gentler on PostNL's API. Changes take effect immediately, no HA restart needed. |

## Removal

Standard HA removal applies: **Settings → Devices & Services →
PostNL → ⋮ → Delete**. No PostNL-side cleanup is needed; deleting the
config entry stops the polling. To revoke API access entirely, change
your PostNL account password — the integration will trigger a re-auth
notification, which you can then ignore.

## Sensors

The integration creates one device per PostNL account, named
**`PostNL (<your-email>)`**. With multiple accounts each gets its own
device named after its email. The entities below show the
friendly-name pattern; their entity_ids carry the same account suffix:

| Friendly name pattern | Description |
|---|---|
| `PostNL (account) Incoming parcels` | Number of active incoming parcels |
| `PostNL (account) Parcel <barcode>` | Canonical status of a single incoming shipment |
| `PostNL (account) Next delivery` | Earliest expected delivery datetime |
| `PostNL (account) En route to PostNL Point` | Active incoming parcels destined for a PostNL Point pickup location |
| `PostNL (account) Delivered parcels` | Recently delivered parcels (configurable window) |
| `PostNL (account) Outgoing parcels` | Number of active outgoing parcels |
| `PostNL (account) Letters` | Letters announced by PostNL's MyMail service over the last ~2 weeks; `unread` count and `letters` list on attributes |
| `PostNL (account) Letter <title>` (image entity) | Scanned photo of a single announced letter, fetched with your token and served through Home Assistant. Attributes mirror the sensor's letter dict: `id`, `title`, `date`, `unread` |

Every parcel exposed on a sensor attribute uses a carrier-agnostic shape:

| Key | Type | Meaning |
|---|---|---|
| `carrier` | string | `"PostNL"` |
| `barcode` | string | Parcel tracking number |
| `sender` | string \| null | Sender name (e.g. webshop) |
| `receiver` | string \| null | Recipient name. Comes from `colli.recipient.names.personName` for active parcels and from the GraphQL `receiverTitle` field for delivered parcels (since the delivered short-circuit skips Track & Trace). |
| `status` | `ParcelStatus` | Canonical status — see the [status reference](#parcel-status-reference) |
| `raw_status` | string \| null | Original PostNL `statusPhase.message` (a Dutch human-readable string) |
| `delivered` | bool | Whether the parcel has been delivered |
| `delivered_at` | ISO 8601 \| null | Delivery moment, if known |
| `planned_from` | ISO 8601 \| null | Expected delivery window start |
| `planned_to` | ISO 8601 \| null | Expected delivery window end |
| `pickup` | bool | Destined for a PostNL Point rather than a home address |
| `pickup_point` | string \| null | PostNL Point name when `pickup` is true (always `null` for now — PostNL has not yet exposed the field) |
| `url` | string \| null | Deep link to the parcel's tracking page on jouw.postnl.nl |
| `weight` | float \| null | Parcel weight in kilograms (converted from PostNL's native grams). `null` for delivered parcels and for parcels whose Track & Trace response does not carry the field. |
| `dimensions` | dict \| null | Parcel dimensions in centimeters: `{length, width, height, text}` — `text` is a pre-formatted `"L x W x H cm"` string. Same coverage as `weight`. PostNL's native depth (mm) maps to canonical `length`. |
| `raw` | dict | The full transformed PostNL payload (GraphQL shipment fields + Track & Trace `colli` data combined). The native `dimensions` dict (`{height, width, depth, weight}` in mm + g) lives here. |

This is the same shape DHL and DPD use, so the
[parcel aggregator](https://github.com/peternijssen/ha-parcel-aggregator)
and any cross-carrier dashboard can read parcels from all three
integrations the same way.

The image URL returned by PostNL's MyMail service requires the bearer
token, so it cannot be loaded directly — not in a dashboard card and
not as a mobile-notification attachment, since the Home Assistant
companion app has no access to that token either. The integration
fetches the bytes itself and exposes each letter as an `image` entity
instead. Show one with the built-in image card on a dashboard, or
reference its `entity_id` in a notification via Home Assistant's
image proxy (`/api/image_proxy/<entity_id>`) — see
[`examples/automations/notify_when_letter_arrives.yaml`](examples/automations/notify_when_letter_arrives.yaml)
for a ready-to-paste setup.

For full attribute reference and example automations see
[docs/sensors.md](docs/sensors.md) — or the
[examples folder](examples/) for ready-to-paste automation and
dashboard snippets.

## Parcel status reference

`status` on every parcel is one of the canonical `ParcelStatus` values
below. Use these in your automations rather than PostNL's raw Dutch
description — the raw value stays available on `raw_status` for power
users.

| `status` | Meaning | PostNL signal that maps here |
|---|---|---|
| `registered` | PostNL knows about the label but the parcel is not yet in transit | `statusPhase.message` containing "aangemeld" or "verwacht" |
| `in_transit` | Picked up; somewhere in PostNL's network | `statusPhase.message` containing "onderweg", "ontvangen" or "gesorteerd" |
| `out_for_delivery` | On the delivery vehicle today | `statusPhase.message` containing "wordt vandaag bezorgd", "onderweg naar het bezorgadres" or "onderweg naar de bezorger" |
| `at_pickup_point` | Arrived at the chosen PostNL Point, ready to be collected | `statusPhase.message` containing "ligt klaar bij postnl punt" or similar |
| `delivered` | Handed over (mailbox, recipient, neighbour, picked up) | `shipment.delivered == true` (authoritative); fallback `statusPhase.message` containing "bezorgd" |
| `returning` | Failed delivery, on the way back to the sender | `statusPhase.message` containing "retour" or "teruggestuurd" |
| `unknown` | Raw description we have not mapped yet | anything else — logged once at info level so it can be added to the map |

Because PostNL's `statusPhase.message` is a human-readable Dutch string
(not a stable API enum), the mapping uses ordered substring matching
— so minor wording variants still resolve correctly. If you see an
`unknown` for a status the integration ought to recognise, open an
issue with the raw value (visible in the integration debug logs and on
the parcel sensor under `raw_status`).

## Events

The coordinator fires events on the HA event bus when something
interesting happens to a parcel, so automations can react without
polling per-parcel sensors.

| Event | When | Payload |
|---|---|---|
| `postnl_parcel_registered` | A new barcode appears in the active list | The full normalised parcel dict (`carrier`, `barcode`, `sender`, `status`, `raw_status`, `delivered`, `delivered_at`, `planned_from`, `planned_to`, `pickup`, `pickup_point`, `url`, `raw`) |
| `postnl_parcel_status_changed` | A known barcode's canonical `status` value changes | Same payload plus `old_status` and `new_status` |
| `postnl_parcel_delivery_time_changed` | A known barcode's `planned_from` or `planned_to` ends up with a non-null value that differs from the previous one. Value-to-null transitions are intentionally silent. | Same payload plus `old_planned_from`, `new_planned_from`, `old_planned_to`, `new_planned_to` |
| `postnl_letter_announced` | A new letter id appears in the MyMail feed | The letter dict (`id`, `title`, `date`, `unread`, `image_url`) plus `carrier: "PostNL"` |

The coordinator suppresses events on the very first refresh after
start-up so you don't get a stampede of "registered" or "announced"
events for parcels and letters that were already in your account
before HA started.

See [`examples/automations/`](examples/automations/) for ready-to-paste
event-driven automations, or the
[parcel aggregator](https://github.com/peternijssen/ha-parcel-aggregator)
for a carrier-agnostic re-emit layer that fires
`parcel_aggregator_parcel_*` events covering every installed carrier
in one go.

## Examples

Ready-to-paste automations and dashboard cards live in [`examples/`](examples/).

### Community Lovelace cards

If you want a richer UI than the snippets above, third-party cards work
nicely with this integration's sensors:

- [jonisnet/hki-parcels-card](https://github.com/jonisnet/hki-parcels-card) — multi-carrier (PostNL, DHL, DPD) Home Kit-style card with Onderweg/Bezorgd/Verzonden/Post tabs. The "Post" tab matches MyMail letter scans to this integration's `image.*` entities, so the scan stays login-free.
- [klaptafel/ha-package-tracker-card](https://github.com/klaptafel/ha-package-tracker-card) — purpose-built card for parcel integrations; renders each parcel with sender, status and tracking link.
- [jimz011/hki-elements](https://github.com/jimz011/hki-elements) — the original PostNL-only Home Kit-style card that hki-parcels-card was forked from. Still useful if you prefer the original single-carrier layout.

All maintained by their respective authors — please raise UI issues
in those repos.

## Debugging

To capture verbose information about the PostNL API responses (useful
when reporting a bug or helping map a new status value), enable debug
logging for the integration:

1. Add this to your `configuration.yaml`:
   ```yaml
   logger:
     default: warning
     logs:
       custom_components.postnl: debug
   ```
2. Restart Home Assistant.
3. Wait for the next poll cycle (or reload the integration from **Settings → Devices & Services → PostNL → ⋮ → Reload**).
4. Open **Settings → System → Logs**, filter for `postnl`, and copy the relevant log lines (including the `Shipments fetched: ...` summary and any `Track and Trace response: ...` payload) into your bug report or message to the maintainer.

## Troubleshooting

| Symptom | Likely cause |
|---------|--------------|
| `invalid_auth` error during setup | Wrong email or password |
| `cannot_connect` error during setup | PostNL API is unreachable; check your network |
| Re-authentication prompt appears | PostNL session expired and could not be refreshed silently; log in again |
| Sensors disappear after delivery | Expected — delivered parcels move to the delivered sensor (visible window controlled by the options filter) |
| Sensors not updating | Check **Settings → System → Logs** for `postnl` entries |

## Related integrations

Tracking parcels from other Dutch carriers:

- [ha-dhl-nl](https://github.com/peternijssen/ha-dhl-nl) — DHL eCommerce NL parcel tracker
- [ha-dpd](https://github.com/peternijssen/ha-dpd) — DPD parcel tracker
- [ha-parcel-aggregator](https://github.com/peternijssen/ha-parcel-aggregator) — rolls up counts and next-delivery timestamps from all installed carrier integrations into a single set of sensors

## Disclaimer

This is an independent, community-built project with no affiliation,
endorsement, or connection to PostNL or any of its subsidiaries. The
PostNL API used here is undocumented (reverse-engineered from the
mobile app and jouw.postnl.nl) and may change without notice. The
maintainers have not asked PostNL for permission to use this API;
installing this integration may breach PostNL's Terms of Service.
You take any risk that follows — account suspension, service
disruption, etc. No warranty (see [LICENSE](LICENSE.md)).

## Contributing

This fork is maintained by [@peternijssen](https://github.com/peternijssen).
The original integration is by
[@arjenbos](https://github.com/arjenbos) — fixes that apply to both
forks are filed as PRs against the upstream
[`arjenbos/ha-postnl`](https://github.com/arjenbos/ha-postnl).

Pull requests and issues are welcome. Please open an issue before submitting a large change.

## License

MIT
