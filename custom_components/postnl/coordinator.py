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
)
from .graphql import PostNLGraphql
from .jouw_api import PostNLJouwAPI

_LOGGER = logging.getLogger(__name__)


def _delivery_dt(parcel: dict) -> datetime | None:
    """Parse the delivery datetime from a transformed parcel dict."""
    date_str = parcel.get("delivery_date")
    if not date_str:
        return None
    try:
        dt = datetime.fromisoformat(str(date_str).replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


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
            name="PostNL",
            update_interval=timedelta(seconds=POLL_INTERVAL),
        )
        self.config_entry = entry
        self.delivered_receiver: list[dict] = []
        self.letters: list[dict] = []
        _LOGGER.debug("PostNLCoordinator initialized with update interval: %s", self.update_interval)
        
    async def _async_update_data(self) -> dict[str, list[dict]]:
        _LOGGER.debug("Starting data update for PostNL.")
        try:
            auth: AsyncConfigEntryAuth = self.hass.data[DOMAIN][self.config_entry.entry_id]['auth']
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

            delivered_receiver = [p for p in data['receiver'] if p.get('delivered')]
            self.delivered_receiver = self._apply_delivered_filter(delivered_receiver)

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
            _LOGGER.error("Network error during PostNL data update: %s", exception, exc_info=True)
            raise UpdateFailed("Unable to update PostNL data") from exception

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

                return {
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
                }

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

            return {
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
            }
        except requests.exceptions.RequestException as exception:
            _LOGGER.error("Error fetching Track and Trace details for shipment %s: %s", shipment.get('key'), exception, exc_info=True)
            raise UpdateFailed("Unable to update PostNL data") from exception
