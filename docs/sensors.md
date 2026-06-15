# Sensors

Full reference for all sensors provided by the PostNL integration.

> **Parcel shape:** every parcel exposed on a sensor attribute carries the carrier-agnostic top-level keys `carrier`, `barcode`, `sender`, `status`, `delivered`, `delivered_at`, `planned_from`, `planned_to`, `pickup`, `pickup_point`, `url`, plus the original PostNL payload under `raw`. See [docs/api/graphql.md → Carrier-agnostic shape exposed by sensors](api/graphql.md#carrier-agnostic-shape-exposed-by-sensors) for the source mapping.

## Incoming parcels

### `sensor.<account>_postnl_incoming_parcels`

Summary sensor showing how many parcels are currently on their way to you.

**State:** number of active incoming parcels (unit: `parcels`)

| Attribute | Description |
|-----------|-------------|
| `parcels` | List of normalized active incoming parcels (full shape including `raw`) |

### `sensor.<account>_postnl_parcel_<barcode>`

One sensor per active incoming shipment. Created automatically when a new parcel appears and removed once it is delivered.

**State:** status message (e.g. `Pakket is onderweg`, `Pakket wordt bezorgd`)

**Attributes:** the full normalized parcel dict (top-level fields plus `raw`).

### `sensor.<account>_postnl_next_delivery`

Earliest expected delivery datetime across all active incoming parcels. Uses device class `timestamp` so Home Assistant treats it as a proper datetime — useful for time-based automations.

**State:** datetime of the next expected delivery, or unavailable if no parcels have a known delivery time

| Attribute | Description |
|-----------|-------------|
| `barcode` | Barcode of the parcel arriving soonest |
| `sender` | Name of the sender of that parcel |

**Example automation:** notify 1 hour before the next delivery:

```yaml
trigger:
  - platform: template
    value_template: >
      {{ (as_timestamp(states('sensor.<account>_postnl_next_delivery')) - as_timestamp(now())) < 3600 }}
```

### `sensor.<account>_postnl_en_route_to_postnl_point`

Parcels destined for a PostNL pickup point that have not yet been collected.

**State:** number of parcels at or en route to a PostNL point (unit: `parcels`)

| Attribute | Description |
|-----------|-------------|
| `parcels` | List of normalized parcels destined for a PostNL point (full shape including `raw`) |

### `sensor.<account>_postnl_delivered_parcels`

Recently delivered incoming parcels. The number of parcels shown is controlled by the integration options (see [Configuration](#configuration)).

**State:** number of delivered parcels shown (unit: `parcels`)

| Attribute | Description |
|-----------|-------------|
| `parcels` | List of normalized delivered parcels (full shape including `raw`) |

---

## Configuration

After the initial setup you can configure the delivered parcels filter via **Settings → Devices & Services → PostNL → Configure**:

| Option | Description |
|--------|-------------|
| **Filter by** | `Days` — show parcels delivered in the last N days. `Number of parcels` — show the N most recent deliveries. |
| **Amount** | The number of days or parcels (1–365). Default: **7 days**. |

Changes take effect on the next data refresh without requiring a restart.

---

## Outgoing shipments

### `sensor.<account>_postnl_outgoing_parcels`

Summary sensor showing how many packages you have sent that are still in transit. Delivered shipments are automatically excluded. No per-shipment sensors are created — all data is available as attributes on this single sensor.

**State:** number of active outgoing shipments (unit: `parcels`)

| Attribute | Description |
|-----------|-------------|
| `shipments` | List of normalized active outgoing shipments (full shape including `raw` — PostNL-specific fields like `key`, `receiver_title`, `planned_date`, `expected_datetime` live under `raw`) |

---

## Poll interval

Data is refreshed every **5 minutes**. You can trigger a manual refresh from the integration's device page using the **Reload** option.

---

## Debug logging

Add the following to `configuration.yaml` to enable verbose logging:

```yaml
logger:
  logs:
    custom_components.postnl: debug
```
