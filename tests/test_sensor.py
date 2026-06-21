"""Tests for the PostNL sensor properties.

Sensors read the carrier-agnostic parcel shape produced by
:func:`normalize_parcel`. The ``_parcel`` helper here builds that shape
directly so we can hit edge cases without going through the full
transform_shipment / API flow.
"""
from datetime import datetime, timezone
from unittest.mock import MagicMock

from custom_components.postnl.const import ParcelStatus
from custom_components.postnl.sensor import (
    PostNLDeliveredParcelsSensor,
    PostNLEnRouteToServicePointSensor,
    PostNLIncomingParcelsSensor,
    PostNLLettersSensor,
    PostNLNextDeliverySensor,
    PostNLOutgoingParcelsSensor,
    PostNLParcelSensor,
)

_USERINFO = {"account_id": "abc-123", "email": "user@example.com"}


def _coordinator(
    *,
    receiver: list[dict] | None = None,
    sender: list[dict] | None = None,
    delivered_receiver: list[dict] | None = None,
    letters: list[dict] | None = None,
) -> MagicMock:
    coordinator = MagicMock()
    coordinator.data = {"receiver": receiver or [], "sender": sender or []}
    coordinator.delivered_receiver = delivered_receiver or []
    coordinator.letters = letters or []
    coordinator.last_update_success = True
    return coordinator


def _parcel(
    *,
    barcode: str = "3SABC",
    delivered: bool = False,
    status: ParcelStatus = ParcelStatus.IN_TRANSIT,
    raw_status: str = "Pakket is onderweg",
    sender: str = "Brand",
    pickup: bool = False,
    planned_from: str | None = None,
    planned_to: str | None = None,
    delivered_at: str | None = None,
) -> dict:
    return {
        "carrier": "PostNL",
        "barcode": barcode,
        "sender": sender,
        "status": status,
        "raw_status": raw_status,
        "delivered": delivered,
        "delivered_at": delivered_at,
        "planned_from": planned_from,
        "planned_to": planned_to,
        "pickup": pickup,
        "pickup_point": None,
        "url": None,
        "raw": {},
    }


# ---------------------------------------------------------------------------
# Summary sensors
# ---------------------------------------------------------------------------


def test_incoming_sensor_counts_only_active_receiver():
    parcels = [_parcel(barcode="A"), _parcel(barcode="B", delivered=True)]
    sensor = PostNLIncomingParcelsSensor(
        _coordinator(receiver=parcels),
        _USERINFO,
        async_add_entities=lambda *_a, **_k: None,
    )
    assert sensor.native_value == 1
    assert sensor.extra_state_attributes["parcels"][0]["barcode"] == "A"


def test_parcel_sensor_returns_status_for_known_barcode():
    parcel = _parcel(barcode="X", status=ParcelStatus.OUT_FOR_DELIVERY)
    sensor = PostNLParcelSensor(_coordinator(receiver=[parcel]), _USERINFO, "X")
    assert sensor.native_value == ParcelStatus.OUT_FOR_DELIVERY
    assert sensor.native_value == "out_for_delivery"
    assert sensor.extra_state_attributes["barcode"] == "X"


def test_parcel_sensor_returns_none_when_barcode_missing():
    sensor = PostNLParcelSensor(_coordinator(receiver=[]), _USERINFO, "Y")
    assert sensor.native_value is None
    assert sensor.extra_state_attributes == {}


# ---------------------------------------------------------------------------
# Next delivery sensor
# ---------------------------------------------------------------------------


def test_next_delivery_picks_earliest_planned_from():
    parcels = [
        _parcel(barcode="A", planned_from="2026-06-18T10:00:00Z"),
        _parcel(barcode="B", planned_from="2026-06-17T09:00:00Z"),
    ]
    sensor = PostNLNextDeliverySensor(_coordinator(receiver=parcels), _USERINFO)
    assert sensor.native_value == datetime(2026, 6, 17, 9, 0, tzinfo=timezone.utc)
    assert sensor.extra_state_attributes["barcode"] == "B"


def test_next_delivery_none_when_no_parcels_have_dates():
    sensor = PostNLNextDeliverySensor(_coordinator(receiver=[_parcel()]), _USERINFO)
    assert sensor.native_value is None
    assert sensor.extra_state_attributes == {}


def test_next_delivery_skips_invalid_date_strings():
    parcels = [_parcel(barcode="A", planned_from="not a date")]
    sensor = PostNLNextDeliverySensor(_coordinator(receiver=parcels), _USERINFO)
    assert sensor.native_value is None


# ---------------------------------------------------------------------------
# En route to service point sensor
# ---------------------------------------------------------------------------


def test_en_route_counts_only_service_point_parcels():
    parcels = [
        _parcel(barcode="A", pickup=True),
        _parcel(barcode="B", pickup=False),
    ]
    sensor = PostNLEnRouteToServicePointSensor(_coordinator(receiver=parcels), _USERINFO)
    assert sensor.native_value == 1
    summary = sensor.extra_state_attributes["parcels"]
    assert len(summary) == 1
    assert summary[0]["barcode"] == "A"


def test_en_route_excludes_delivered():
    parcels = [_parcel(barcode="A", pickup=True, delivered=True)]
    sensor = PostNLEnRouteToServicePointSensor(_coordinator(receiver=parcels), _USERINFO)
    assert sensor.native_value == 0


# ---------------------------------------------------------------------------
# Outgoing sensor
# ---------------------------------------------------------------------------


def test_outgoing_sensor_lists_active_sender_parcels():
    shipments = [
        _parcel(barcode="S1", delivered=False),
        _parcel(barcode="S2", delivered=True, status=ParcelStatus.DELIVERED),
    ]
    sensor = PostNLOutgoingParcelsSensor(_coordinator(sender=shipments), _USERINFO)
    assert sensor.native_value == 1
    attrs = sensor.extra_state_attributes
    assert len(attrs["parcels"]) == 1
    assert attrs["parcels"][0]["barcode"] == "S1"


# ---------------------------------------------------------------------------
# Delivered parcels sensor
# ---------------------------------------------------------------------------


def test_delivered_sensor_reads_from_coordinator_delivered_receiver():
    delivered = [_parcel(
        barcode="D1",
        sender="Sender",
        delivered=True,
        status=ParcelStatus.DELIVERED,
        raw_status="Pakket is bezorgd",
        delivered_at="2026-06-15T10:00:00Z",
    )]
    sensor = PostNLDeliveredParcelsSensor(
        _coordinator(delivered_receiver=delivered), _USERINFO
    )
    assert sensor.native_value == 1
    parcels = sensor.extra_state_attributes["parcels"]
    assert parcels[0]["barcode"] == "D1"
    assert parcels[0]["sender"] == "Sender"
    assert parcels[0]["delivered_at"] == "2026-06-15T10:00:00Z"


# ---------------------------------------------------------------------------
# Letters sensor
# ---------------------------------------------------------------------------


def test_letters_sensor_reports_total_and_unread_count():
    letters = [
        {"id": "A", "unread": False, "title": "16 juni"},
        {"id": "B", "unread": True, "title": "15 juni"},
    ]
    sensor = PostNLLettersSensor(_coordinator(letters=letters), _USERINFO)
    assert sensor.native_value == 2
    attrs = sensor.extra_state_attributes
    assert attrs["unread"] == 1
    assert len(attrs["letters"]) == 2
