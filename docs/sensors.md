# Sensors

Full reference for all sensors provided by the PostNL integration.

> **Friendly name pattern:** the integration creates one device per
> PostNL account, named `PostNL (<your-email>)`. Each sensor's
> friendly name is `<device-name> <entity-name>`, e.g.
> `PostNL (account@example.com) Incoming parcels`.

> **Parcel shape:** every parcel exposed on a sensor attribute carries
> the carrier-agnostic top-level keys `carrier`, `barcode`, `sender`,
> `status` (the canonical [`ParcelStatus`](#parcel-status-reference)
> value), `raw_status` (PostNL's Dutch `statusPhase.message` string),
> `delivered`, `delivered_at`, `planned_from`, `planned_to`, `pickup`,
> `pickup_point`, `url`, plus the original transformed PostNL payload
> (GraphQL shipment fields + Track & Trace `colli` data) under `raw`.

## Incoming parcels

### `PostNL (account) Incoming parcels`

Summary sensor showing how many parcels are currently on their way to
you.

**State:** number of active incoming parcels (unit: `parcels`,
translated as `pakketten` in Dutch HA).

| Attribute | Description |
|-----------|-------------|
| `parcels` | List of all active incoming parcel objects (normalised carrier-agnostic shape). Not recorded long-term to keep the recorder DB lean. |

### `PostNL (account) Parcel <barcode>`

One sensor per active incoming shipment. Created automatically when a
new parcel appears and removed once it is delivered.

**State:** the canonical [`ParcelStatus`](#parcel-status-reference)
value (e.g. `out_for_delivery`, `in_transit`).

**Attributes:** the full normalised parcel dict — top-level fields
plus `raw_status` (the original Dutch `statusPhase.message`) and
`raw` (the full transformed PostNL payload, kept out of the recorder
long-term).

### `PostNL (account) Next delivery`

Earliest expected delivery datetime across all active incoming
parcels. Uses device class `timestamp` so Home Assistant treats it as
a proper datetime — useful for time-based automations.

The value is the `planned_from` of the earliest active parcel — on
the day of delivery this is usually the start of the announced
delivery window; before that day it is typically midnight of the
planned date.

**State:** datetime of the next expected delivery, or `unavailable`
if no parcels have a known delivery time.

| Attribute | Description |
|-----------|-------------|
| `barcode` | Barcode of the parcel arriving soonest |
| `sender` | Name of the sender of that parcel |

### `PostNL (account) En route to PostNL Point`

Active incoming parcels destined for a PostNL Point pickup location.
The integration also distinguishes parcels that have *arrived* at the
PostNL Point via the `at_pickup_point` status; the sensor itself
counts both in-transit-to-Point and awaiting-collection parcels.

**State:** number of PostNL Point-bound parcels (unit: `parcels`).

| Attribute | Description |
|-----------|-------------|
| `parcels` | List of normalised PostNL Point-bound parcels |

### `PostNL (account) Delivered parcels`

Recently delivered incoming parcels. The window is controlled by the
integration options (see [Options](#options)).

**State:** number of delivered parcels shown (unit: `parcels`).

| Attribute | Description |
|-----------|-------------|
| `parcels` | List of normalised delivered parcels |

---

## Outgoing parcels

### `PostNL (account) Outgoing parcels`

Summary sensor showing how many parcels you have sent that are still
in transit. Shipments with `delivered == true` are excluded. No
per-shipment sensors are created — all data is available as
attributes on this single sensor.

**State:** number of active outgoing parcels (unit: `parcels`).

| Attribute | Description |
|-----------|-------------|
| `parcels` | List of normalised active outgoing parcels |

---

## Letters (MyMail)

### `PostNL (account) Letters`

Letters announced by PostNL's MyMail service. PostNL retains roughly
two weeks of mail; the sensor reflects whatever the API currently
returns.

**State:** number of announced letters (unit: `letters`, translated as
`brieven` in Dutch HA).

| Attribute | Description |
|-----------|-------------|
| `unread` | Count of letters that have not yet been marked as read in MyMail |
| `letters` | List of letter dicts — `id`, `title` (Dutch date string, e.g. `"16 juni"`), `date` (ISO date inferred from `title`), `unread` flag, and `image_url` (PostNL CDN URL, requires the bearer token — see the image entity below) |

### `PostNL (account) Letter <title>` (image entity)

One image entity per announced letter, exposing the scanned photo of
that letter. The image URL returned by MyMail requires the bearer
token, so the integration fetches the bytes itself and serves them
through Home Assistant rather than letting the dashboard try to load
the URL directly.

Show one with the built-in image card:

```yaml
type: image
entity: image.postnl_letter_<editId>
```

The entity ID derives from the MyMail `editId` of the letter (the
opaque identifier PostNL uses to address the scan). Stale image
entities are automatically removed once the letter falls out of the
2-week MyMail window.

---

## Parcel status reference

`status` on every parcel is one of these canonical
[`ParcelStatus`](../custom_components/postnl/const.py) values. Use
these in automations rather than PostNL's raw Dutch status string —
`raw_status` keeps the original value available for power users.

| `status` | Meaning | PostNL signal that maps here |
|---|---|---|
| `registered` | PostNL knows about the label but the parcel is not yet in transit | `statusPhase.message` containing "aangemeld" or "verwacht" |
| `in_transit` | Picked up; somewhere in PostNL's network | `statusPhase.message` containing "onderweg", "ontvangen" or "gesorteerd" |
| `out_for_delivery` | On the delivery vehicle today | `statusPhase.message` containing "wordt vandaag bezorgd", "onderweg naar het bezorgadres" or "onderweg naar de bezorger" |
| `at_pickup_point` | Arrived at the chosen PostNL Point, ready to collect | `statusPhase.message` containing "ligt klaar bij postnl punt" or similar |
| `delivered` | Handed over (mailbox, recipient, neighbour, picked up) | `shipment.delivered == true` (authoritative); fallback `statusPhase.message` containing "bezorgd" |
| `returning` | Failed delivery, on the way back to the sender | `statusPhase.message` containing "retour" or "teruggestuurd" |
| `unknown` | Raw description we have not mapped yet | anything else — logged once per HA session at info level |

Because `statusPhase.message` is a human-readable Dutch string (not a
stable API enum), the mapping uses ordered substring matching — more
specific patterns first. Wording variants you encounter that don't
land on the expected status are bugs worth filing.

---

## Events

The coordinator fires events on the HA event bus when something
changes:

| Event | When | Payload |
|---|---|---|
| `postnl_parcel_registered` | A new barcode appears in the active list | Full normalised parcel dict |
| `postnl_parcel_status_changed` | A known barcode's canonical `status` changes | Normalised parcel dict plus `old_status` and `new_status` |
| `postnl_letter_announced` | A new letter id appears in the MyMail feed | Letter dict (`id`, `title`, `date`, `unread`, `image_url`) plus `carrier: "PostNL"` |

Events are suppressed on the very first refresh after start-up to
avoid a flood of "registered" or "announced" events for parcels and
letters that already existed.

Because events fire on the canonical `status`, intra-`in_transit`
churn (e.g. `statusPhase.message` flipping from "Pakket is onderweg"
to "Pakket is gesorteerd in het sorteercentrum") yields **no** event
— both map to `ParcelStatus.IN_TRANSIT`.

See [`examples/automations/`](../examples/automations/) for
ready-to-paste event-driven automations.

---

## Options

After setup, click **Configure** on the integration card to change the
delivered-parcels filter:

| Option | Description |
|--------|-------------|
| **Filter by** | `Days` — show parcels delivered in the last N days. `Number of parcels` — show the N most recent deliveries. |
| **Amount** | The number of days or parcels (1–365). Default: **7 days**. |

Changes take effect on the next data refresh without requiring a
reload.

---

## Poll interval

Data is refreshed every **5 minutes**. You can trigger a manual
refresh from the integration's device page using the **Reload**
option.

---

## Debug logging

Add the following to `configuration.yaml` to enable verbose logging:

```yaml
logger:
  logs:
    custom_components.postnl: debug
```

Debug-level logs include the GraphQL `shipments` payload, the Track &
Trace `colli` lookup result, and the MyMail letters payload — useful
when reporting a bug or helping extend the status map.
