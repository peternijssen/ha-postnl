from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

import requests
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError
from homeassistant.helpers.update_coordinator import (DataUpdateCoordinator,
                                                      UpdateFailed)

from .auth import AsyncConfigEntryAuth
from .const import (
    CONF_DELIVERED_FILTER_AMOUNT,
    CONF_DELIVERED_FILTER_TYPE,
    DEFAULT_DELIVERED_FILTER_AMOUNT,
    DEFAULT_DELIVERED_FILTER_TYPE,
    DOMAIN,
    POLL_INTERVAL,
    ParcelStatus,
)
from .graphql import PostNLGraphql
from .jouw_api import PostNLJouwAPI

_LOGGER = logging.getLogger(__name__)


# PostNL's status comes from two signals:
#   - shipment.delivered (bool, GraphQL) — terminal indicator
#   - colli.statusPhase.message (Track & Trace) — Dutch human-readable string
#
# statusPhase.message is not under an API contract: PostNL changes the wording
# at will. So we substring-match against the meaningful subphrase rather than
# look up against a fixed enum. Order matters — first match wins, so the more
# specific patterns must come before the broader ones (e.g. "wordt vandaag
# bezorgd" before "bezorgd").
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

_LOGGED_UNKNOWN_STATUSES: set[str] = set()


def map_parcel_status(parcel: dict) -> ParcelStatus:
    """Map a PostNL parcel dict to a canonical :class:`ParcelStatus`.

    ``parcel`` is the intermediate dict built by :meth:`transform_shipment`
    with at least ``delivered`` (bool) and ``status_message`` (str) populated.
    The ``delivered`` flag from GraphQL is authoritative and short-circuits to
    :attr:`ParcelStatus.DELIVERED`. Anything that does not match any pattern
    is reported as :attr:`ParcelStatus.UNKNOWN` and surfaced once at info
    level so users can open an issue to extend the map.
    """
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
        _LOGGER.info(
            "Unmapped PostNL status %r will be reported as ParcelStatus.UNKNOWN. "
            "Please open an issue at "
            "https://github.com/peternijssen/ha-postnl/issues so the map can "
            "be extended.",
            parcel.get("status_message"),
        )
    return ParcelStatus.UNKNOWN


def normalize_parcel(parcel: dict) -> dict:
    """Wrap a transformed PostNL parcel in the canonical carrier-agnostic shape.

    Top-level keys mirror DHL / DPD / parcel-aggregator. The original
    transformed PostNL payload (GraphQL + Track & Trace fields like
    ``status_message``, ``delivery_address_type``, ``planned_date``,
    ``expected_datetime``) is preserved under ``raw`` for power users.
    """
    delivered = bool(parcel.get("delivered"))
    return {
        "carrier": "PostNL",
        "barcode": parcel.get("barcode"),
        "sender": parcel.get("source_display_name"),
        "status": map_parcel_status(parcel),
        "raw_status": parcel.get("status_message"),
        "delivered": delivered,
        "delivered_at": parcel.get("delivery_date") if delivered else None,
        "planned_from": None if delivered else parcel.get("planned_from"),
        "planned_to": None if delivered else parcel.get("planned_to"),
        "pickup": parcel.get("delivery_address_type") == "ServicePoint",
        "pickup_point": None,
        "url": parcel.get("url"),
        "raw": parcel,
    }


def _delivery_dt(parcel: dict) -> datetime | None:
    """Parse the delivery datetime from a normalised parcel dict."""
    date_str = parcel.get("delivered_at")
    if not date_str:
        return None
    try:
        dt = datetime.fromisoformat(str(date_str).replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def sort_parcels_by_ts(
    parcels: list[dict], key_field: str, *, descending: bool = False
) -> list[dict]:
    """Return normalised parcels sorted by the ISO timestamp at ``key_field``.

    Parcels whose value is missing or unparseable always sort to the end,
    regardless of ``descending`` — so freshly registered parcels without
    an ETA stay visible at the bottom instead of jumping to the top.
    """
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
        with_ts.append((dt, parcel))
    with_ts.sort(key=lambda item: item[0], reverse=descending)
    return [p for _, p in with_ts] + without_ts


_DUTCH_MONTHS = {
    "januari": 1, "februari": 2, "maart": 3, "april": 4,
    "mei": 5, "juni": 6, "juli": 7, "augustus": 8,
    "september": 9, "oktober": 10, "november": 11, "december": 12,
}


def parse_letter_date(title: str, *, today: datetime | None = None) -> str | None:
    """Convert a Dutch day-month title like '16 juni' into an ISO date string.

    The MyMail endpoint returns dates without a year. We infer the year from
    ``today``: if the parsed month/day is more than 31 days ahead of today, it
    must belong to the previous year (PostNL only retains 2 weeks of mail).
    """
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
    """Extract letter entries from the server-driven-UI MyMail response."""
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
    graphq_api: PostNLGraphql
    jouw_api: PostNLJouwAPI

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        """Initialize PostNL coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name="PostNL",
            update_interval=timedelta(seconds=POLL_INTERVAL),
        )
        self.delivered_receiver: list[dict] = []
        self.letters: list[dict] = []
        # barcode -> last seen ParcelStatus. ``None`` on the first refresh so
        # we can suppress events for parcels that already existed when the
        # integration started (we do not know their previous state).
        self._known_state: dict[str, ParcelStatus] | None = None
        _LOGGER.debug("PostNLCoordinator initialized with update interval: %s", self.update_interval)
        
    async def _async_update_data(self) -> dict[str, list[dict]]:
        _LOGGER.debug("Starting data update for PostNL.")
        try:
            auth: AsyncConfigEntryAuth = self.config_entry.runtime_data.auth
            _LOGGER.debug("Authenticating with PostNL API.")
            await auth.check_and_refresh_token()

            self.graphq_api = PostNLGraphql(auth.access_token)
            self.jouw_api = PostNLJouwAPI(auth.access_token)

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

            active_receiver = [p for p in data['receiver'] if not p.get('delivered')]
            self._fire_change_events(active_receiver)
            self._known_state = {
                p["barcode"]: p["status"]
                for p in active_receiver
                if p.get("barcode")
            }

            delivered_receiver = [p for p in data['receiver'] if p.get('delivered')]
            self.delivered_receiver = sort_parcels_by_ts(
                self._apply_delivered_filter(delivered_receiver),
                'delivered_at',
                descending=True,
            )

            try:
                letters_payload = await self.hass.async_add_executor_job(self.jouw_api.letters)
                self.letters = extract_letters(letters_payload)
            except requests.exceptions.RequestException as exception:
                _LOGGER.warning("PostNL letters fetch failed: %s", exception)

            _LOGGER.info(
                "Updated PostNL data: %d receiver packages, %d sender packages, %d delivered shown, %d letters.",
                len(data['receiver']), len(data['sender']), len(self.delivered_receiver), len(self.letters),
            )

            return data
        except ConfigEntryAuthFailed:
            raise
        except HomeAssistantError as exception:
            raise UpdateFailed("Authentication failed") from exception
        except requests.exceptions.RequestException as exception:
            raise UpdateFailed("Unable to update PostNL data") from exception

    def _fire_change_events(self, parcels: list[dict]) -> None:
        """Fire events for newly-registered parcels and status transitions.

        Silent on the very first refresh — we cannot reliably know which
        parcels are "new" to the user vs. "already there before HA started".
        From the second refresh onward, every parcel that was not present
        before yields one ``postnl_parcel_registered`` event, and every
        parcel whose normalised status changed yields one
        ``postnl_parcel_status_changed`` event.
        """
        if self._known_state is None:
            return

        for parcel in parcels:
            barcode = parcel.get("barcode")
            if not barcode:
                continue
            new_status = parcel["status"]
            if barcode not in self._known_state:
                self.hass.bus.async_fire(
                    f"{DOMAIN}_parcel_registered",
                    {**parcel},
                )
            elif self._known_state[barcode] != new_status:
                self.hass.bus.async_fire(
                    f"{DOMAIN}_parcel_status_changed",
                    {
                        **parcel,
                        "old_status": self._known_state[barcode],
                        "new_status": new_status,
                    },
                )

    def _apply_delivered_filter(self, parcels: list[dict]) -> list[dict]:
        """Trim the delivered receiver list according to the configured options."""
        options = self.config_entry.options
        filter_type = options.get(CONF_DELIVERED_FILTER_TYPE, DEFAULT_DELIVERED_FILTER_TYPE)
        filter_amount = int(options.get(CONF_DELIVERED_FILTER_AMOUNT, DEFAULT_DELIVERED_FILTER_AMOUNT))

        if filter_type == "days":
            cutoff = datetime.now(timezone.utc) - timedelta(days=filter_amount)
            return [p for p in parcels if (dt := _delivery_dt(p)) is None or dt >= cutoff]

        return parcels[:filter_amount]

    async def transform_shipment(self, shipment) -> dict:
        _LOGGER.debug('Updating %s', shipment.get('key'))

        try:
            if shipment.get('delivered'):
                _LOGGER.debug('%s already delivered, no need to call jouw.postnl.', shipment.get('key'))

                return normalize_parcel({
                    "key": shipment.get('key'),
                    "barcode": shipment.get('barcode'),
                    "name": shipment.get('title'),
                    "url": shipment.get('detailsUrl'),
                    "shipment_type": shipment.get('shipmentType'),
                    "receiver_title": (shipment.get('receiverTitle') or '').strip() or None,
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
                })

            _LOGGER.debug("Fetching Track and Trace details for shipment %s.", shipment['key'])
            track_and_trace_details = await self.hass.async_add_executor_job(self.jouw_api.track_and_trace,
                                                                             shipment['key'])

            if not track_and_trace_details.get('colli'):
                _LOGGER.warning("No colli found for shipment %s.", shipment['key'])
                _LOGGER.debug("Track and Trace response: %s", track_and_trace_details)

            colli = track_and_trace_details.get('colli', {}).get(shipment['barcode'], {})

            status_message = "Unknown"
            planned_date = planned_from = planned_to = expected_datetime = None

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
            else:
                _LOGGER.warning("Barcode not found in colli details for shipment %s.", shipment['key'])
                planned_date = shipment.get('deliveryWindowFrom')
                planned_from = shipment.get('deliveryWindowFrom')
                planned_to = shipment.get('deliveryWindowTo')

            return normalize_parcel({
                "key": shipment.get('key'),
                "barcode": shipment.get('barcode'),
                "name": shipment.get('title'),
                "url": shipment.get('detailsUrl'),
                "shipment_type": shipment.get('shipmentType'),
                "receiver_title": (shipment.get('receiverTitle') or '').strip() or None,
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
            })
        except requests.exceptions.RequestException as exception:
            _LOGGER.error("Error fetching Track and Trace details for shipment %s: %s", shipment.get('key'), exception, exc_info=True)
            raise UpdateFailed("Unable to update PostNL data") from exception
