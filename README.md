# PostNL Home Assistant Integration

A custom Home Assistant integration that tracks your incoming and outgoing PostNL shipments.

## Features

- Incoming and outgoing parcel count sensors
- Per-parcel sensor per active incoming shipment
- Next delivery datetime sensor (device class `timestamp`)
- PostNL point sensor — parcels destined for a PostNL pickup point
- Automatic lifecycle management — sensors are created and removed as parcels move through delivery

## Requirements

- Home Assistant 2024.1 or newer
- A [PostNL](https://jouw.postnl.nl) account

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
4. Click **Submit**

## Sensors

| Entity | Description |
|--------|-------------|
| `sensor.<account>_postnl_incoming_parcels` | Number of active incoming parcels |
| `sensor.<account>_postnl_parcel_<barcode>` | Status of a single incoming shipment |
| `sensor.<account>_postnl_next_delivery` | Earliest expected delivery datetime |
| `sensor.<account>_postnl_en_route_to_postnl_point` | Parcels destined for a PostNL pickup point |
| `sensor.<account>_postnl_outgoing_parcels` | Number of active outgoing shipments |

For full attribute reference and example automations see [docs/sensors.md](docs/sensors.md).

## Troubleshooting

| Symptom | Likely cause |
|---------|--------------|
| `invalid_auth` error during setup | Wrong email or password |
| `cannot_connect` error during setup | PostNL API is unreachable; check your network |
| Sensors disappear after delivery | Expected — delivered shipments are filtered out |
| Sensors not updating | Check **Settings → System → Logs** for `postnl` entries |

## Disclaimer

This is an independent, community-built project with no affiliation, endorsement, or connection to PostNL or any of its subsidiaries. The PostNL API is undocumented and may change without notice.

## Contributing

Pull requests and issues are welcome. Please open an issue before submitting a large change.

## License

MIT
