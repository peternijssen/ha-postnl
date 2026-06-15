# GET /track-and-trace/api/trackAndTrace/{key}

Returns live delivery status details for a single shipment. Called for every shipment that has not yet been delivered, to obtain the current status message and ETA.

## Request

**URL:** `https://jouw.postnl.nl/track-and-trace/api/trackAndTrace/{key}?language=nl`  
**Method:** `GET`

### Path parameters

| Parameter | Description |
|-----------|-------------|
| `key` | Shipment barcode / key from the [`trackedShipments` GraphQL query](graphql.md) |

### Query parameters

| Parameter | Value |
|-----------|-------|
| `language` | `nl` |

### Headers

| Header | Value |
|--------|-------|
| `Authorization` | `Bearer <access_token>` |

## Response

**Status:** `200 OK`

### Body

```json
{
  "colli": {
    "3SABCD1234567890": {
      "statusPhase": {
        "message": "Pakket is onderweg"
      },
      "routeInformation": {
        "plannedDeliveryTime": "2026-05-29T13:00:00",
        "plannedDeliveryTimeWindow": {
          "startDateTime": "2026-05-29T12:00:00",
          "endDateTime": "2026-05-29T14:00:00"
        },
        "expectedDeliveryTime": "2026-05-29T13:15:00"
      },
      "eta": null
    }
  }
}
```

### `colli` object

The response is keyed by barcode. The integration looks up `colli[shipment.barcode]` to find the relevant entry.

| Field | Type | Description |
|-------|------|-------------|
| `statusPhase.message` | string | Human-readable current status, e.g. `"Pakket is onderweg"`. Surfaced as the sensor's top-level `status` attribute. |
| `routeInformation` | object\|null | Present when the carrier has live route data |
| `routeInformation.plannedDeliveryTime` | string\|null | Single planned delivery timestamp |
| `routeInformation.plannedDeliveryTimeWindow.startDateTime` | string\|null | Start of the delivery window |
| `routeInformation.plannedDeliveryTimeWindow.endDateTime` | string\|null | End of the delivery window |
| `routeInformation.expectedDeliveryTime` | string\|null | Dynamically updated expected delivery time based on driver progress |
| `eta` | object\|null | Alternative ETA structure used when `routeInformation` is absent |
| `eta.start` | string\|null | ETA window start |
| `eta.end` | string\|null | ETA window end |

### ETA resolution order

The coordinator resolves delivery timing in this order:

1. `routeInformation` — if present, uses `plannedDeliveryTime`, window start/end, and `expectedDeliveryTime`
2. `eta` — if `routeInformation` is absent, uses `eta.start` and `eta.end`
3. GraphQL fallback — uses `deliveryWindowFrom` / `deliveryWindowTo` from the shipment if neither field is present

## How the integration uses this endpoint

These fields are first written to the internal transformed-shipment dict (under the legacy keys below) and then projected onto the carrier-agnostic shape exposed by the sensors. The transformed dict is preserved under each parcel's `raw` attribute.

| API field | Internal key (under `raw`) | Carrier-agnostic top-level key |
|-----------|----------------------------|-------------------------------|
| `statusPhase.message` | `status_message` | `status` |
| `plannedDeliveryTime` / `eta.start` | `planned_date` | — |
| `startDateTime` / `eta.start` | `planned_from` | `planned_from` |
| `endDateTime` / `eta.end` | `planned_to` | `planned_to` |
| `expectedDeliveryTime` | `expected_datetime` | — |

## Error handling

| Condition | Behaviour |
|-----------|-----------|
| `colli` key absent | Warning is logged; GraphQL delivery window values are used as fallback |
| Barcode not found in `colli` | Warning is logged; GraphQL delivery window values are used as fallback |
| `requests.RequestException` | `UpdateFailed` is raised; coordinator retries on next poll interval |
