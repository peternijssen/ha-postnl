from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

import requests
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.update_coordinator import (DataUpdateCoordinator,
                                                      UpdateFailed)

from .auth import AsyncConfigEntryAuth
from .const import (
    CONF_DELIVERED_FILTER_AMOUNT,
    CONF_DELIVERED_FILTER_TYPE,
    CONF_INCLUDE_HISTORY,
    CONF_REFRESH_INTERVAL,
    DEFAULT_DELIVERED_FILTER_AMOUNT,
    DEFAULT_DELIVERED_FILTER_TYPE,
    DEFAULT_INCLUDE_HISTORY,
    DEFAULT_REFRESH_INTERVAL,
    DOMAIN,
    HISTORY_MAX_EVENTS,
    ParcelStatus,
)
from .graphql import PostNLGraphql
from .jouw_api import PostNLJouwAPI

_LOGGER = logging.getLogger(__name__)


_STATUS_PATTERNS: tuple[tuple[str, ParcelStatus], ...] = (
    ("ligt klaar bij postnl punt", ParcelStatus.AT_PICKUP_POINT),
    ("afgeleverd op postnl punt", ParcelStatus.AT_PICKUP_POINT),
    ("klaar bij postnl punt", ParcelStatus.AT_PICKUP_POINT),
    ("teruggestuurd", ParcelStatus.RETURNING),
    ("retour", ParcelStatus.RETURNING),
    ("wordt vandaag bezorgd", ParcelStatus.OUT_FOR_DELIVERY),
    ("onderweg naar het bezorgadres", ParcelStatus.OUT_FOR_DELIVERY),
    ("onderweg naar de bezorger", ParcelStatus.OUT_FOR_DELIVERY),
    ("aangemeld", ParcelStatus.REGISTERED),
    ("verwacht", ParcelStatus.REGISTERED),
    ("ontvangen", ParcelStatus.IN_TRANSIT),
    ("gesorteerd", ParcelStatus.IN_TRANSIT),
    ("onderweg", ParcelStatus.IN_TRANSIT),
    ("bezorgd", ParcelStatus.DELIVERED),
)

_OBSERVATION_CODE_MAP: dict[str, ParcelStatus] = {
    "A01": ParcelStatus.REGISTERED,
    "M02": ParcelStatus.REGISTERED,
    "B01": ParcelStatus.IN_TRANSIT,
    "J01": ParcelStatus.IN_TRANSIT,
    "J04": ParcelStatus.IN_TRANSIT,
    "J21": ParcelStatus.IN_TRANSIT,
    "J31": ParcelStatus.IN_TRANSIT,
    "J32": ParcelStatus.IN_TRANSIT,
    "J40": ParcelStatus.IN_TRANSIT,
    "J44": ParcelStatus.IN_TRANSIT,
    "J55": ParcelStatus.IN_TRANSIT,
    "G01": ParcelStatus.IN_TRANSIT,
    "G05": ParcelStatus.IN_TRANSIT,
    "T04": ParcelStatus.IN_TRANSIT,
    "J05": ParcelStatus.OUT_FOR_DELIVERY,
    "I08": ParcelStatus.AT_PICKUP_POINT,
    "J02": ParcelStatus.AT_PICKUP_POINT,
    "J12": ParcelStatus.AT_PICKUP_POINT,
    "A80": ParcelStatus.DELIVERED,
    "I01": ParcelStatus.DELIVERED,
    "I02": ParcelStatus.DELIVERED,
    "I05": ParcelStatus.DELIVERED,
}

_OBSERVATION_META_CODES: frozenset[str] = frozenset({
    "A04",
    "A18",
    "A19",
    "A25",
    "A65",
    "A94",
    "A95",
    "A96",
    "A98",
    "K33",
})

_NEW_ISSUE_URL = "https://github.com/peternijssen/ha-postnl/issues/new"

_LOGGED_UNKNOWN_STATUSES: set[str] = set()
_LOGGED_UNKNOWN_OBSERVATION_CODES: set[str] = set()


def _refresh_interval(entry: ConfigEntry) -> timedelta:
    minutes = int(entry.options.get(CONF_REFRESH_INTERVAL, DEFAULT_REFRESH_INTERVAL))
    return timedelta(minutes=minutes)


def map_parcel_status(parcel: dict) -> ParcelStatus:
    if parcel.get("delivered"):
        return ParcelStatus.DELIVERED

    raw = (parcel.get("status_message") or "").strip().lower()
    if not raw:
        return ParcelStatus.UNKNOWN

    for pattern, status in _STATUS_PATTERNS:
        if pattern in raw:
            return status

    if raw not in _LOGGED_UNKNOWN_STATUSES:
        _LOGGED_UNKNOWN_STATUSES.add(raw)
        _LOGGER.warning(
            "Unrecognised PostNL status — help us map it. Open an issue and "
            "paste this line: %s\n  [parcel] statusPhase.message=%r "
            "→ reported as 'unknown'",
            _NEW_ISSUE_URL,
            parcel.get("status_message"),
        )
    return ParcelStatus.UNKNOWN


def map_observation_status(code: str | None, description: str | None) -> ParcelStatus | None:
    if code is None:
        return None
    if code in _OBSERVATION_META_CODES:
        return None
    status = _OBSERVATION_CODE_MAP.get(code)
    if status is not None:
        return status
    if code not in _LOGGED_UNKNOWN_OBSERVATION_CODES:
        _LOGGED_UNKNOWN_OBSERVATION_CODES.add(code)
        _LOGGER.warning(
            "Unrecognised PostNL observation code — help us map it. Open an "
            "issue and paste this line: %s\n  [history] observationCode=%r "
            "description=%r → status omitted from timeline",
            _NEW_ISSUE_URL,
            code,
            description,
        )
    return None


def _convert_native_dimensions(
    native: dict | None,
) -> tuple[float | None, dict | None]:
    if not native:
        return None, None
    weight_g = native.get("weight")
    weight_kg = weight_g / 1000 if weight_g is not None else None
    depth_mm = native.get("depth")
    width_mm = native.get("width")
    height_mm = native.get("height")
    if depth_mm is None or width_mm is None or height_mm is None:
        return weight_kg, None
    length_cm = depth_mm / 10
    width_cm = width_mm / 10
    height_cm = height_mm / 10
    text = f"{int(round(length_cm))} x {int(round(width_cm))} x {int(round(height_cm))} cm"
    return weight_kg, {
        "length": length_cm,
        "width": width_cm,
        "height": height_cm,
        "text": text,
    }


def normalize_parcel(parcel: dict, *, history: list[dict] | None = None) -> dict:
    delivered = bool(parcel.get("delivered"))
    weight_kg, canonical_dimensions = _convert_native_dimensions(
        parcel.get("dimensions")
    )
    return {
        "carrier": "PostNL",
        "barcode": parcel.get("barcode"),
        "sender": parcel.get("source_display_name"),
        "receiver": parcel.get("receiver"),
        "status": map_parcel_status(parcel),
        "raw_status": parcel.get("status_message"),
        "delivered": delivered,
        "delivered_at": parcel.get("delivery_date") if delivered else None,
        "planned_from": None if delivered else parcel.get("planned_from"),
        "planned_to": None if delivered else parcel.get("planned_to"),
        "pickup": parcel.get("delivery_address_type") == "ServicePoint",
        "pickup_point": None,
        "url": parcel.get("url"),
        "weight": weight_kg,
        "dimensions": canonical_dimensions,
        "history": history,
        "raw": parcel,
    }


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _delivery_dt(parcel: dict) -> datetime | None:
    return _parse_iso(parcel.get("delivered_at"))


def sort_parcels_by_ts(
    parcels: list[dict], key_field: str, *, descending: bool = False
) -> list[dict]:
    with_ts: list[tuple[datetime, dict]] = []
    without_ts: list[dict] = []
    for parcel in parcels:
        value = parcel.get(key_field)
        if not isinstance(value, str) or not value:
            without_ts.append(parcel)
            continue
        try:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            without_ts.append(parcel)
            continue
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        with_ts.append((dt, parcel))
    with_ts.sort(key=lambda item: item[0], reverse=descending)
    return [p for _, p in with_ts] + without_ts


def _extract_observations(colli: dict) -> list[dict]:
    analytics = colli.get("analyticsInfo") or {}
    return analytics.get("allObservations") or colli.get("observations") or []


def build_history(
    observations: list[dict] | None, *, max_events: int = HISTORY_MAX_EVENTS
) -> list[dict]:
    parseable: list[tuple[datetime, tuple]] = []
    unparseable: list[tuple] = []
    for obs in observations or []:
        timestamp = obs.get("observationDate")
        if not timestamp:
            continue
        record = (timestamp, obs.get("observationCode"), obs.get("description"))
        dt = _parse_iso(timestamp)
        if dt is None:
            unparseable.append(record)
        else:
            parseable.append((dt, record))
    parseable.sort(key=lambda item: item[0])
    ordered = [record for _, record in parseable] + unparseable

    history: list[dict] = []
    last_status: ParcelStatus | None = ParcelStatus.REGISTERED
    for timestamp, code, description in ordered:
        status = map_observation_status(code, description)
        if status is not None:
            last_status = status
        elif code in _OBSERVATION_META_CODES:
            status = last_status
        history.append(
            {"timestamp": timestamp, "status": status, "raw_status": description}
        )
    return history[-max_events:]


_DUTCH_MONTHS = {
    "januari": 1, "februari": 2, "maart": 3, "april": 4,
    "mei": 5, "juni": 6, "juli": 7, "augustus": 8,
    "september": 9, "oktober": 10, "november": 11, "december": 12,
}


def parse_letter_date(title: str, *, today: datetime | None = None) -> str | None:
    if not title:
        return None
    parts = title.strip().lower().split()
    if len(parts) != 2:
        return None
    try:
        day = int(parts[0])
    except ValueError:
        return None
    month = _DUTCH_MONTHS.get(parts[1])
    if month is None:
        return None
    now = (today or datetime.now(timezone.utc)).date()
    try:
        candidate = now.replace(month=month, day=day)
    except ValueError:
        return None
    if (candidate - now).days > 31:
        try:
            candidate = candidate.replace(year=candidate.year - 1)
        except ValueError:
            return None
    return candidate.isoformat()


def extract_letters(payload: dict, *, today: datetime | None = None) -> list[dict]:
    sections = ((payload or {}).get("screen") or {}).get("sections") or []
    letters: list[dict] = []
    for section in sections:
        for item in section.get("items") or []:
            if item.get("type") != "Letter":
                continue
            title = item.get("title")
            letters.append({
                "id": item.get("editId"),
                "title": title,
                "date": parse_letter_date(title, today=today),
                "unread": bool(item.get("isUnread")),
                "image_url": (item.get("image") or {}).get("url"),
            })
    return letters


class PostNLCoordinator(DataUpdateCoordinator):
    data: dict[str, list[dict]]

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        """Initialize PostNL coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name="PostNL",
            update_interval=_refresh_interval(entry),
        )
        self.delivered_receiver: list[dict] = []
        self.delivered_sender: list[dict] = []
        self.letters: list[dict] = []
        self.graphq_api: PostNLGraphql | None = None
        self.jouw_api: PostNLJouwAPI | None = None
        self._api_token: str | None = None
        self._known_state: dict[str, ParcelStatus] | None = None
        self._known_delivery_times: (
            dict[str, tuple[str | None, str | None]] | None
        ) = None
        self._known_letter_ids: set[str] | None = None
        self._parcel_cache: dict[str, dict] = {}
        self._delivered_history_cache: dict[str, list[dict] | None] = {}
        self._cached_device_id: str | None = None
        self.last_success_time: datetime | None = None
        _LOGGER.debug("PostNLCoordinator initialized with update interval: %s", self.update_interval)

    async def _async_update_data(self) -> dict[str, list[dict]]:
        _LOGGER.debug("Starting data update for PostNL.")
        try:
            auth: AsyncConfigEntryAuth = self.config_entry.runtime_data.auth
            _LOGGER.debug("Authenticating with PostNL API.")
            await auth.check_and_refresh_token()

            if self.graphq_api is None or auth.access_token != self._api_token:
                self._api_token = auth.access_token
                self.graphq_api = PostNLGraphql(self._api_token)
                self.jouw_api = PostNLJouwAPI(self._api_token)

            data: dict[str, list[dict]] = {
                'receiver': [],
                'sender': []
            }

            shipments = await self.hass.async_add_executor_job(self.graphq_api.shipments)

            _LOGGER.debug("Shipments fetched: %s", shipments)
            receiver_shipments = [self.transform_shipment(shipment) for shipment in
                                  shipments.get('trackedShipments', {}).get('receiverShipments', [])]
            data['receiver'] = await asyncio.gather(*receiver_shipments)

            sender_shipments = [self.transform_shipment(shipment) for shipment in
                                shipments.get('trackedShipments', {}).get('senderShipments', [])]
            data['sender'] = await asyncio.gather(*sender_shipments)

            data['receiver'] = sort_parcels_by_ts(data['receiver'], 'planned_from')
            data['sender'] = sort_parcels_by_ts(data['sender'], 'planned_from')

            current_barcodes = {
                p.get('barcode') for p in data['receiver'] if p.get('barcode')
            }
            self._parcel_cache = {
                k: v for k, v in self._parcel_cache.items() if k in current_barcodes
            }
            self._delivered_history_cache = {
                k: v
                for k, v in self._delivered_history_cache.items()
                if k in current_barcodes
            }

            active_receiver = [p for p in data['receiver'] if not p.get('delivered')]
            self._fire_change_events(active_receiver)
            self._known_state = {
                p["barcode"]: p["status"]
                for p in active_receiver
                if p.get("barcode")
            }
            self._known_delivery_times = {
                p["barcode"]: (p.get("planned_from"), p.get("planned_to"))
                for p in active_receiver
                if p.get("barcode")
            }

            delivered_receiver = [p for p in data['receiver'] if p.get('delivered')]
            self.delivered_receiver = sort_parcels_by_ts(
                self._apply_delivered_filter(delivered_receiver),
                'delivered_at',
                descending=True,
            )

            delivered_sender = [p for p in data['sender'] if p.get('delivered')]
            self.delivered_sender = sort_parcels_by_ts(
                self._apply_delivered_filter(delivered_sender),
                'delivered_at',
                descending=True,
            )

            try:
                letters_payload = await self.hass.async_add_executor_job(self.jouw_api.letters)
                self.letters = extract_letters(letters_payload)
                self._fire_letter_events(self.letters)
                self._known_letter_ids = {
                    letter["id"] for letter in self.letters if letter.get("id")
                }
            except requests.exceptions.RequestException as exception:
                _LOGGER.warning("PostNL letters fetch failed: %s", exception)

            _LOGGER.info(
                "Updated PostNL data: %d receiver packages, %d sender packages, %d delivered shown, %d letters.",
                len(data['receiver']), len(data['sender']), len(self.delivered_receiver), len(self.letters),
            )

            self.last_success_time = datetime.now(timezone.utc)
            return data
        except ConfigEntryAuthFailed:
            raise
        except HomeAssistantError as exception:
            raise UpdateFailed("Authentication failed") from exception
        except requests.exceptions.RequestException as exception:
            raise UpdateFailed("Unable to update PostNL data") from exception

    def _fire_change_events(self, parcels: list[dict]) -> None:
        if self._known_state is None:
            return

        known_times = self._known_delivery_times or {}
        device_id = self._device_id()

        for parcel in parcels:
            barcode = parcel.get("barcode")
            if not barcode:
                continue
            new_status = parcel["status"]
            if barcode not in self._known_state:
                self.hass.bus.async_fire(
                    f"{DOMAIN}_parcel_registered",
                    {**parcel, "device_id": device_id},
                )
                continue

            if self._known_state[barcode] != new_status:
                self.hass.bus.async_fire(
                    f"{DOMAIN}_parcel_status_changed",
                    {
                        **parcel,
                        "device_id": device_id,
                        "old_status": self._known_state[barcode],
                        "new_status": new_status,
                    },
                )

            old_from, old_to = known_times.get(barcode, (None, None))
            new_from = parcel.get("planned_from")
            new_to = parcel.get("planned_to")
            from_changed = new_from is not None and new_from != old_from
            to_changed = new_to is not None and new_to != old_to
            if from_changed or to_changed:
                self.hass.bus.async_fire(
                    f"{DOMAIN}_parcel_delivery_time_changed",
                    {
                        **parcel,
                        "device_id": device_id,
                        "old_planned_from": old_from,
                        "new_planned_from": new_from,
                        "old_planned_to": old_to,
                        "new_planned_to": new_to,
                    },
                )

    def _fire_letter_events(self, letters: list[dict]) -> None:
        if self._known_letter_ids is None:
            return
        device_id = self._device_id()
        for letter in letters:
            letter_id = letter.get("id")
            if not letter_id or letter_id in self._known_letter_ids:
                continue
            self.hass.bus.async_fire(
                f"{DOMAIN}_letter_announced",
                {**letter, "carrier": "PostNL", "device_id": device_id},
            )

    def _apply_delivered_filter(self, parcels: list[dict]) -> list[dict]:
        options = self.config_entry.options
        filter_type = options.get(CONF_DELIVERED_FILTER_TYPE, DEFAULT_DELIVERED_FILTER_TYPE)
        filter_amount = int(options.get(CONF_DELIVERED_FILTER_AMOUNT, DEFAULT_DELIVERED_FILTER_AMOUNT))

        if filter_type == "days":
            cutoff = datetime.now(timezone.utc) - timedelta(days=filter_amount)
            return [p for p in parcels if (dt := _delivery_dt(p)) is None or dt >= cutoff]

        return parcels[:filter_amount]

    @property
    def _include_history(self) -> bool:
        return bool(
            self.config_entry.options.get(CONF_INCLUDE_HISTORY, DEFAULT_INCLUDE_HISTORY)
        )

    def _delivered_history(self, shipment) -> list[dict] | None:
        if not self._include_history:
            return None
        barcode = shipment.get('barcode')
        if barcode and barcode in self._delivered_history_cache:
            return self._delivered_history_cache[barcode]
        try:
            details = self.jouw_api.track_and_trace(shipment['key'])
        except requests.exceptions.RequestException as exception:
            _LOGGER.warning(
                "History fetch failed for delivered shipment %s: %s",
                shipment.get('key'), exception,
            )
            return None
        colli = details.get('colli', {}).get(shipment['barcode'], {})
        history = build_history(_extract_observations(colli)) if colli else None
        if barcode:
            self._delivered_history_cache[barcode] = history
        return history

    def _device_id(self) -> str | None:
        if self._cached_device_id is not None:
            return self._cached_device_id
        registry = dr.async_get(self.hass)
        device = next(
            iter(dr.async_entries_for_config_entry(registry, self.config_entry.entry_id)),
            None,
        )
        if device is not None:
            self._cached_device_id = device.id
        return self._cached_device_id

    async def transform_shipment(self, shipment) -> dict:
        _LOGGER.debug('Updating %s', shipment.get('key'))

        receiver_title = (shipment.get('receiverTitle') or '').strip() or None

        try:
            if shipment.get('delivered'):
                _LOGGER.debug('%s already delivered, no need to call jouw.postnl.', shipment.get('key'))

                history = await self.hass.async_add_executor_job(
                    self._delivered_history, shipment
                )
                return normalize_parcel({
                    "key": shipment.get('key'),
                    "barcode": shipment.get('barcode'),
                    "name": shipment.get('title'),
                    "url": shipment.get('detailsUrl'),
                    "shipment_type": shipment.get('shipmentType'),
                    "receiver_title": receiver_title,
                    "receiver": receiver_title,
                    "source_display_name": (
                        (shipment.get('sourceDisplayName') or '').strip()
                        or (shipment.get('title') or '').strip()
                        or None
                    ),
                    "status_message": "Pakket is bezorgd",
                    "delivered": shipment.get('delivered'),
                    "delivery_date": shipment.get('deliveredTimeStamp'),
                    "delivery_address_type": shipment.get('deliveryAddressType'),
                    "planned_date": None,
                    "planned_from": None,
                    "planned_to": None,
                    "expected_datetime": None,
                    "dimensions": None,
                }, history=history)

            _LOGGER.debug("Fetching Track and Trace details for shipment %s.", shipment['key'])
            try:
                track_and_trace_details = await self.hass.async_add_executor_job(
                    self.jouw_api.track_and_trace, shipment['key']
                )
            except requests.exceptions.RequestException as exception:
                barcode = shipment.get('barcode')
                cached = self._parcel_cache.get(barcode) if barcode else None
                if cached is not None:
                    _LOGGER.warning(
                        "Track & Trace failed for shipment %s; reusing previous data: %s",
                        shipment.get('key'), exception,
                    )
                    return cached
                _LOGGER.warning(
                    "Track & Trace failed for shipment %s; using shipment fields only: %s",
                    shipment.get('key'), exception,
                )
                track_and_trace_details = {}

            if not track_and_trace_details.get('colli'):
                _LOGGER.warning("No colli found for shipment %s.", shipment['key'])
                _LOGGER.debug("Track and Trace response: %s", track_and_trace_details)

            colli = track_and_trace_details.get('colli', {}).get(shipment['barcode'], {})

            status_message = "Unknown"
            planned_date = planned_from = planned_to = expected_datetime = None
            recipient_name: str | None = None
            native_dimensions: dict | None = None
            history: list[dict] | None = None

            if colli:
                _LOGGER.debug("Colli details found for shipment %s: %s", shipment['key'], colli)
                if colli.get("routeInformation"):
                    route_information = colli.get("routeInformation")
                    planned_date = route_information.get("plannedDeliveryTime")
                    planned_from = route_information.get("plannedDeliveryTimeWindow", {}).get("startDateTime")
                    planned_to = route_information.get("plannedDeliveryTimeWindow", {}).get('endDateTime')
                    expected_datetime = route_information.get('expectedDeliveryTime')
                elif colli.get('eta'):
                    planned_date = colli.get('eta', {}).get('start')
                    planned_from = colli.get('eta', {}).get('start')
                    planned_to = colli.get('eta', {}).get('end')
                else:
                    planned_date = shipment.get('deliveryWindowFrom')
                    planned_from = shipment.get('deliveryWindowFrom')
                    planned_to = shipment.get('deliveryWindowTo')

                status_message = colli.get('statusPhase', {}).get('message', "Unknown")
                recipient_name = (
                    colli.get('recipient', {}).get('names', {}).get('personName')
                )
                native_dimensions = (colli.get('details') or {}).get('dimensions')
                history = (
                    build_history(_extract_observations(colli))
                    if self._include_history
                    else None
                )
            else:
                _LOGGER.warning("Barcode not found in colli details for shipment %s.", shipment['key'])
                planned_date = shipment.get('deliveryWindowFrom')
                planned_from = shipment.get('deliveryWindowFrom')
                planned_to = shipment.get('deliveryWindowTo')

            parcel = normalize_parcel({
                "key": shipment.get('key'),
                "barcode": shipment.get('barcode'),
                "name": shipment.get('title'),
                "url": shipment.get('detailsUrl'),
                "shipment_type": shipment.get('shipmentType'),
                "receiver_title": receiver_title,
                "receiver": recipient_name or receiver_title,
                "source_display_name": (
                        (shipment.get('sourceDisplayName') or '').strip()
                        or (shipment.get('title') or '').strip()
                        or None
                    ),
                "status_message": status_message,
                "delivered": shipment.get('delivered'),
                "delivery_date": shipment.get('deliveredTimeStamp'),
                "delivery_address_type": shipment.get('deliveryAddressType'),
                "planned_date": planned_date,
                "planned_from": planned_from,
                "planned_to": planned_to,
                "expected_datetime": expected_datetime,
                "dimensions": native_dimensions,
            }, history=history)
            barcode = shipment.get('barcode')
            if barcode and colli:
                self._parcel_cache[barcode] = parcel
            return parcel
        except requests.exceptions.RequestException as exception:
            _LOGGER.error("Error fetching Track and Trace details for shipment %s: %s", shipment.get('key'), exception, exc_info=True)
            barcode = shipment.get('barcode')
            cached = self._parcel_cache.get(barcode) if barcode else None
            if cached is not None:
                return cached
            raise UpdateFailed("Unable to update PostNL data") from exception
