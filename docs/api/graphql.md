# POST /account/api/graphql

GraphQL endpoint for PostNL account data. The integration uses two queries: `profile` (token validation) and `trackedShipments` (shipment list).

## Request

**URL:** `https://jouw.postnl.nl/account/api/graphql`  
**Method:** `POST`  
**Content-Type:** `application/json`

### Headers

| Header | Value |
|--------|-------|
| `Authorization` | `Bearer <access_token>` |

---

## Query: `profile`

Used to validate that the access token is still accepted by the data API.

### Body

```graphql
query {
  profile {
    ...ProfileData
    __typename
  }
}

fragment ProfileData on Profile {
  username
  __typename
}
```

### Response

```json
{
  "data": {
    "profile": {
      "username": "user@example.com",
      "__typename": "Profile"
    }
  }
}
```

| Field | Description |
|-------|-------------|
| `username` | Account email address |

---

## Query: `trackedShipments`

Returns all active and recently delivered shipments for the account, split into incoming (receiver) and outgoing (sender) lists.

### Body

```graphql
query {
  trackedShipments {
    receiverShipments {
      ...shipment
      __typename
    }
    senderShipments {
      ...shipment
      __typename
    }
    __typename
  }
}

fragment shipment on TrackedShipmentResultType {
  key
  creationDateTime
  title
  barcode
  delivered
  deliveredTimeStamp
  deliveryWindowFrom
  deliveryWindowTo
  deliveryWindowType
  detailsUrl
  shipmentType
  receiverTitle
  deliveryAddressType
  sourceAccountId
  sourceDisplayName
  __typename
}
```

### Response

```json
{
  "data": {
    "trackedShipments": {
      "receiverShipments": [
        {
          "key": "3SABCD1234567890-NL-1234AB",
          "creationDateTime": "2026-05-28T08:17:22+02:00",
          "barcode": "3SABCD1234567890",
          "title": "Online Retailer",
          "delivered": false,
          "deliveredTimeStamp": null,
          "deliveryWindowFrom": "2026-05-29T12:00:00",
          "deliveryWindowTo": "2026-05-29T14:00:00",
          "deliveryWindowType": null,
          "shipmentType": "Parcel",
          "receiverTitle": "Jane Doe",
          "deliveryAddressType": "Recipient",
          "detailsUrl": "https://jouw.postnl.nl/track-and-trace/3SABCD1234567890-NL-1234AB",
          "sourceAccountId": null,
          "sourceDisplayName": null,
          "__typename": "TrackedShipmentResultType"
        }
      ],
      "senderShipments": [
        {
          "key": "3SEFGH9876543210-NL-5678CD",
          "creationDateTime": "2026-05-20T10:00:00+02:00",
          "barcode": "3SEFGH9876543210",
          "title": "Retailer Aftersales",
          "delivered": true,
          "deliveredTimeStamp": "2026-05-21T12:14:29",
          "deliveryWindowFrom": "2026-05-21T00:00:00",
          "deliveryWindowTo": "2026-05-21T23:59:59",
          "deliveryWindowType": null,
          "shipmentType": "Parcel",
          "receiverTitle": "Retailer Aftersales",
          "deliveryAddressType": "Rerouted",
          "detailsUrl": "https://jouw.postnl.nl/track-and-trace/3SEFGH9876543210-NL-5678CD",
          "sourceAccountId": null,
          "sourceDisplayName": null,
          "__typename": "TrackedShipmentResultType"
        }
      ],
      "__typename": "GetTrackedShipmentsResultType"
    }
  }
}
```

### Shipment fields

| Field | Type | Description |
|-------|------|-------------|
| `key` | string | Full shipment identifier in `{barcode}-{country}-{postalcode}` format, e.g. `3SABCD1234567890-NL-1234AB`. Used as the identifier for Track & Trace lookups and the `detailsUrl`. |
| `creationDateTime` | string (ISO 8601 with TZ offset) | When the shipment was registered, e.g. `2026-05-28T08:17:22+02:00` |
| `barcode` | string | The bare barcode without country/postcode suffix, e.g. `3SABCD1234567890`. Used to look up the matching entry in the Track & Trace `colli` response. |
| `title` | string | Display name — typically the sender name. May have a leading space. The integration uses this as the parcel's sender because `sourceDisplayName` is always `null`. |
| `delivered` | boolean | `true` when the parcel has been delivered. When `true`, no Track & Trace call is made. |
| `deliveredTimeStamp` | string\|null | Actual delivery timestamp when `delivered` is `true`, e.g. `2026-05-29T14:34:26` (no TZ offset) |
| `deliveryWindowFrom` | string\|null | Start of the estimated delivery window |
| `deliveryWindowTo` | string\|null | End of the estimated delivery window. When the window spans a full day, `From` is `00:00:00` and `To` is `23:59:59`. |
| `deliveryWindowType` | null | Always `null` in observed data |
| `shipmentType` | string | Parcel type. Observed values: `Parcel`, `LetterboxParcel` |
| `receiverTitle` | string\|null | Name of the recipient as printed on the label, e.g. `Jane Doe`. May have a leading space — the integration strips it. |
| `deliveryAddressType` | string\|null | Delivery destination type. Observed values: `Recipient` (home address), `ServicePoint` (pickup point), `Rerouted` (return shipment) |
| `detailsUrl` | string | Deep link to the shipment detail page on jouw.postnl.nl |
| `sourceAccountId` | null | Always `null` in observed data |
| `sourceDisplayName` | null | Always `null` in observed data |

## How the integration uses this endpoint

- `receiverShipments` → incoming parcel sensors (`PostNLIncomingParcelsSensor`, `PostNLParcelSensor`, `PostNLDeliveredParcelsSensor`)
- `senderShipments` → outgoing parcel sensor (`PostNLOutgoingParcelsSensor`)
- `delivered: true` → parcel routed to the delivered list; no Track & Trace call is made
- `delivered: false` → parcel routed to the active list; a [Track & Trace](track_and_trace.md) call is made for live status
- `barcode` → key for the `colli` lookup in the Track & Trace response
- `title` → `sender` attribute on the HA sensor entity (with a fallback chain through `sourceDisplayName` for forward compatibility)
- `receiverTitle` → `receiver_title` attribute on the HA sensor entity

## Error handling

A `TransportQueryError` from the `gql` library (e.g. a GraphQL-level error response) causes the integration to force-expire the access token and retry with a refreshed token.
