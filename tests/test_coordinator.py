"""Tests for the PostNL coordinator helpers and transform_shipment."""
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.postnl.const import ParcelStatus
from custom_components.postnl.coordinator import (
    _DUTCH_MONTHS,
    PostNLCoordinator,
    _delivery_dt,
    extract_letters,
    map_parcel_status,
    normalize_parcel,
    parse_letter_date,
)


# ---------------------------------------------------------------------------
# _delivery_dt
# ---------------------------------------------------------------------------


def test_delivery_dt_parses_iso_with_tz():
    parcel = {"delivered_at": "2026-06-12T10:00:00+02:00"}
    dt = _delivery_dt(parcel)
    assert dt is not None
    assert dt.year == 2026 and dt.hour == 10


def test_delivery_dt_assigns_utc_when_naive():
    parcel = {"delivered_at": "2026-06-12T10:00:00"}
    dt = _delivery_dt(parcel)
    assert dt is not None
    assert dt.tzinfo is not None
    assert dt.tzinfo.utcoffset(dt).total_seconds() == 0


def test_delivery_dt_handles_z_suffix():
    parcel = {"delivered_at": "2026-06-12T10:00:00Z"}
    dt = _delivery_dt(parcel)
    assert dt == datetime(2026, 6, 12, 10, 0, tzinfo=timezone.utc)


def test_delivery_dt_returns_none_for_missing():
    assert _delivery_dt({}) is None
    assert _delivery_dt({"delivered_at": None}) is None
    assert _delivery_dt({"delivered_at": ""}) is None


def test_delivery_dt_returns_none_for_garbage():
    assert _delivery_dt({"delivered_at": "not a date"}) is None


# ---------------------------------------------------------------------------
# map_parcel_status
# ---------------------------------------------------------------------------


def test_map_parcel_status_delivered_flag_short_circuits():
    assert map_parcel_status({"delivered": True, "status_message": "anything"}) == ParcelStatus.DELIVERED


def test_map_parcel_status_unknown_when_message_missing():
    assert map_parcel_status({}) == ParcelStatus.UNKNOWN
    assert map_parcel_status({"status_message": ""}) == ParcelStatus.UNKNOWN
    assert map_parcel_status({"status_message": None}) == ParcelStatus.UNKNOWN


def test_map_parcel_status_out_for_delivery_beats_in_transit():
    # "onderweg naar het bezorgadres" contains "onderweg" but must be more specific
    assert map_parcel_status({"status_message": "Pakket is onderweg naar het bezorgadres"}) == ParcelStatus.OUT_FOR_DELIVERY


def test_map_parcel_status_wordt_vandaag_bezorgd_is_out_for_delivery():
    # "wordt vandaag bezorgd" contains "bezorgd" but must NOT match DELIVERED
    assert map_parcel_status({"status_message": "Pakket wordt vandaag bezorgd"}) == ParcelStatus.OUT_FOR_DELIVERY


def test_map_parcel_status_in_transit_for_onderweg():
    assert map_parcel_status({"status_message": "Pakket is onderweg"}) == ParcelStatus.IN_TRANSIT


def test_map_parcel_status_at_pickup_point_for_postnl_punt():
    assert map_parcel_status({"status_message": "Pakket ligt klaar bij PostNL punt"}) == ParcelStatus.AT_PICKUP_POINT


def test_map_parcel_status_registered_for_aangemeld():
    assert map_parcel_status({"status_message": "Pakket is aangemeld"}) == ParcelStatus.REGISTERED


def test_map_parcel_status_unknown_for_unmapped_string():
    assert map_parcel_status({"status_message": "Verstuurd via warpdrive"}) == ParcelStatus.UNKNOWN


# ---------------------------------------------------------------------------
# normalize_parcel
# ---------------------------------------------------------------------------


def test_normalize_parcel_canonical_top_level_keys():
    parcel = normalize_parcel({
        "barcode": "3SXYZ",
        "source_display_name": "Bol.com",
        "url": "https://example.com",
        "delivered": False,
        "status_message": "Pakket is onderweg",
        "delivery_date": None,
        "delivery_address_type": "Recipient",
        "planned_from": "2026-06-20T09:00:00Z",
        "planned_to": "2026-06-20T17:00:00Z",
    })
    assert parcel["carrier"] == "PostNL"
    assert parcel["barcode"] == "3SXYZ"
    assert parcel["sender"] == "Bol.com"
    assert parcel["status"] == ParcelStatus.IN_TRANSIT
    assert parcel["raw_status"] == "Pakket is onderweg"
    assert parcel["delivered"] is False
    assert parcel["delivered_at"] is None
    assert parcel["planned_from"] == "2026-06-20T09:00:00Z"
    assert parcel["planned_to"] == "2026-06-20T17:00:00Z"
    assert parcel["pickup"] is False
    assert parcel["pickup_point"] is None
    assert parcel["url"] == "https://example.com"
    assert "status_message" not in parcel  # original lives only under raw
    assert parcel["raw"]["status_message"] == "Pakket is onderweg"


def test_normalize_parcel_pickup_detected_for_service_point():
    parcel = normalize_parcel({
        "barcode": "X",
        "delivered": False,
        "delivery_address_type": "ServicePoint",
        "status_message": "Pakket is onderweg",
    })
    assert parcel["pickup"] is True


def test_normalize_parcel_delivered_window_cleared():
    parcel = normalize_parcel({
        "barcode": "X",
        "delivered": True,
        "delivery_date": "2026-06-20T10:00:00Z",
        "status_message": "Pakket is bezorgd",
        "planned_from": "2026-06-20T09:00:00Z",
        "planned_to": "2026-06-20T11:00:00Z",
    })
    assert parcel["status"] == ParcelStatus.DELIVERED
    assert parcel["delivered_at"] == "2026-06-20T10:00:00Z"
    assert parcel["planned_from"] is None
    assert parcel["planned_to"] is None


# ---------------------------------------------------------------------------
# parse_letter_date
# ---------------------------------------------------------------------------


def _today(year: int = 2026, month: int = 6, day: int = 16) -> datetime:
    return datetime(year, month, day, tzinfo=timezone.utc)


def test_parse_letter_date_today_uses_current_year():
    assert parse_letter_date("16 juni", today=_today()) == "2026-06-16"


def test_parse_letter_date_future_month_within_window_keeps_year():
    # 1 July is 15 days ahead of 16 June → still this year
    assert parse_letter_date("1 juli", today=_today()) == "2026-07-01"


def test_parse_letter_date_far_future_rolls_back_year():
    # 30 December seen on 16 June must belong to last December
    assert parse_letter_date("30 december", today=_today()) == "2025-12-30"


def test_parse_letter_date_returns_none_for_invalid():
    assert parse_letter_date("", today=_today()) is None
    assert parse_letter_date(None, today=_today()) is None
    assert parse_letter_date("garbage", today=_today()) is None
    assert parse_letter_date("31 februari", today=_today()) is None  # 31 Feb doesn't exist
    assert parse_letter_date("12 unknownmonth", today=_today()) is None
    assert parse_letter_date("notanumber juni", today=_today()) is None


def test_dutch_months_dict_has_twelve_months():
    assert len(_DUTCH_MONTHS) == 12
    assert _DUTCH_MONTHS["januari"] == 1
    assert _DUTCH_MONTHS["december"] == 12


# ---------------------------------------------------------------------------
# extract_letters
# ---------------------------------------------------------------------------


def _sdui_payload(letters: list[dict]) -> dict:
    return {
        "screen": {
            "sections": [
                {"type": "List", "items": [{"type": "Text"}]},  # ignored
                {"type": "Grid", "items": letters},
                {"type": "List", "items": [{"type": "Default"}]},  # ignored
            ]
        }
    }


def test_extract_letters_picks_up_letter_items():
    payload = _sdui_payload([
        {
            "type": "Letter",
            "editId": "ABC1",
            "title": "16 juni",
            "isUnread": False,
            "image": {"url": "https://example.com/a"},
        },
        {
            "type": "Letter",
            "editId": "ABC2",
            "title": "15 juni",
            "isUnread": True,
            "image": {"url": "https://example.com/b"},
        },
    ])
    letters = extract_letters(payload, today=_today())
    assert len(letters) == 2
    assert letters[0]["id"] == "ABC1"
    assert letters[0]["title"] == "16 juni"
    assert letters[0]["date"] == "2026-06-16"
    assert letters[0]["unread"] is False
    assert letters[0]["image_url"] == "https://example.com/a"
    assert letters[1]["unread"] is True


def test_extract_letters_ignores_non_letter_items():
    payload = _sdui_payload([{"type": "TextListItem", "title": "Header"}])
    assert extract_letters(payload, today=_today()) == []


def test_extract_letters_returns_empty_for_missing_screen():
    assert extract_letters({}, today=_today()) == []
    assert extract_letters(None, today=_today()) == []
    assert extract_letters({"screen": {}}, today=_today()) == []


def test_extract_letters_handles_missing_image_block():
    payload = _sdui_payload([
        {"type": "Letter", "editId": "X", "title": "16 juni", "isUnread": False},
    ])
    letters = extract_letters(payload, today=_today())
    assert letters[0]["image_url"] is None


# ---------------------------------------------------------------------------
# transform_shipment
# ---------------------------------------------------------------------------


def _make_coordinator(hass):
    entry = MagicMock()
    entry.options = {}
    coordinator = PostNLCoordinator(hass, entry)
    coordinator.jouw_api = MagicMock()
    return coordinator


async def test_transform_shipment_short_circuits_for_delivered(hass):
    coordinator = _make_coordinator(hass)
    shipment = {
        "key": "K1",
        "barcode": "3SABC",
        "title": "Online Retailer",
        "detailsUrl": "https://example.com",
        "shipmentType": "Parcel",
        "receiverTitle": "Peter ",
        "sourceDisplayName": "Brand",
        "deliveredTimeStamp": "2026-06-15T14:00:00Z",
        "deliveryAddressType": "ADDRESS",
        "delivered": True,
    }
    parcel = await coordinator.transform_shipment(shipment)
    assert parcel["barcode"] == "3SABC"
    assert parcel["delivered"] is True
    assert parcel["status"] == ParcelStatus.DELIVERED
    assert parcel["raw_status"] == "Pakket is bezorgd"
    # No track_and_trace call should be made for delivered shipments
    coordinator.jouw_api.track_and_trace.assert_not_called()


async def test_transform_shipment_fetches_planned_window_from_route_information(hass):
    coordinator = _make_coordinator(hass)
    coordinator.jouw_api.track_and_trace = MagicMock(return_value={
        "colli": {
            "3SABC": {
                "statusPhase": {"message": "OP_WEG_VAN_AFZENDER"},
                "routeInformation": {
                    "plannedDeliveryTime": "2026-06-17T15:00:00Z",
                    "plannedDeliveryTimeWindow": {
                        "startDateTime": "2026-06-17T14:00:00Z",
                        "endDateTime": "2026-06-17T16:00:00Z",
                    },
                    "expectedDeliveryTime": "2026-06-17T15:15:00Z",
                },
            }
        }
    })
    shipment = {
        "key": "K2",
        "barcode": "3SABC",
        "title": "Brand",
        "detailsUrl": None,
        "delivered": False,
    }
    parcel = await coordinator.transform_shipment(shipment)
    assert parcel["raw_status"] == "OP_WEG_VAN_AFZENDER"
    assert parcel["planned_from"] == "2026-06-17T14:00:00Z"
    assert parcel["planned_to"] == "2026-06-17T16:00:00Z"
    assert parcel["raw"]["expected_datetime"] == "2026-06-17T15:15:00Z"


async def test_transform_shipment_falls_back_to_eta_window(hass):
    coordinator = _make_coordinator(hass)
    coordinator.jouw_api.track_and_trace = MagicMock(return_value={
        "colli": {
            "3SXYZ": {
                "statusPhase": {"message": "BEZIG_MET_BEZORGEN"},
                "eta": {
                    "start": "2026-06-17T11:00:00Z",
                    "end": "2026-06-17T13:00:00Z",
                },
            }
        }
    })
    shipment = {
        "key": "K3",
        "barcode": "3SXYZ",
        "title": "Brand",
        "delivered": False,
    }
    parcel = await coordinator.transform_shipment(shipment)
    assert parcel["raw_status"] == "BEZIG_MET_BEZORGEN"
    assert parcel["planned_from"] == "2026-06-17T11:00:00Z"
    assert parcel["planned_to"] == "2026-06-17T13:00:00Z"


async def test_transform_shipment_falls_back_to_delivery_window_strings(hass):
    coordinator = _make_coordinator(hass)
    coordinator.jouw_api.track_and_trace = MagicMock(return_value={
        "colli": {
            "3SDEF": {
                "statusPhase": {"message": "VERWACHT"},
                # No routeInformation, no eta
            }
        }
    })
    shipment = {
        "key": "K4",
        "barcode": "3SDEF",
        "title": "Brand",
        "delivered": False,
        "deliveryWindowFrom": "2026-06-18T09:00:00Z",
        "deliveryWindowTo": "2026-06-18T17:00:00Z",
    }
    parcel = await coordinator.transform_shipment(shipment)
    assert parcel["planned_from"] == "2026-06-18T09:00:00Z"
    assert parcel["planned_to"] == "2026-06-18T17:00:00Z"


async def test_transform_shipment_handles_missing_colli_entry(hass):
    coordinator = _make_coordinator(hass)
    coordinator.jouw_api.track_and_trace = MagicMock(return_value={"colli": {}})
    shipment = {
        "key": "K5",
        "barcode": "3SNOPE",
        "title": "Brand",
        "delivered": False,
        "deliveryWindowFrom": "2026-06-20T09:00:00Z",
        "deliveryWindowTo": "2026-06-20T17:00:00Z",
    }
    parcel = await coordinator.transform_shipment(shipment)
    assert parcel["raw_status"] == "Unknown"
    assert parcel["status"] == ParcelStatus.UNKNOWN
    assert parcel["planned_from"] == "2026-06-20T09:00:00Z"
