# Sensors

Full reference for all sensors provided by the PostNL integration.

## Incoming parcels

### `sensor.<account>_postnl_incoming_parcels`

Summary sensor showing how many parcels are currently on their way to you.

**State:** number of active incoming parcels (unit: `parcels`)

| Attribute | Description |
|-----------|-------------|
| `parcels` | List of all active incoming parcel objects |

### `sensor.<account>_postnl_parcel_<barcode>`

One sensor per active incoming shipment. Created automatically when a new parcel appears and removed once it is delivered.

**State:** status message (e.g. `Pakket is onderweg`, `Pakket wordt bezorgd`)

**Attributes:** all fields from the Package object, including barcode, planned delivery times, status, and shipment type.

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
| `parcels` | List of parcels, each with `barcode`, `sender`, and `status` |

---

## Outgoing shipments

### `sensor.<account>_postnl_outgoing_parcels`

Summary sensor showing how many packages you have sent that are still in transit. Delivered shipments are automatically excluded. No per-shipment sensors are created — all data is available as attributes on this single sensor.

**State:** number of active outgoing shipments (unit: `parcels`)

| Attribute | Description |
|-----------|-------------|
| `shipments` | List of active outgoing shipment objects, each containing `barcode`, `key`, `status`, `shipment_type`, `receiver`, `planned_date`, `planned_from`, and `planned_to` |

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
