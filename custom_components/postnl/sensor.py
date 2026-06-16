"""Sensor platform for the PostNL integration."""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.device_registry import DeviceEntryType
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import PostNLConfigEntry
from .const import DOMAIN
from .coordinator import PostNLCoordinator

_LOGGER = logging.getLogger(__name__)

DELIVERY_ADDRESS_SERVICE_POINT = "ServicePoint"

# The DataUpdateCoordinator handles fan-out; HA's per-entity throttling adds nothing.
PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: PostNLConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up PostNL sensor entities from a config entry."""
    data = entry.runtime_data
    userinfo: dict[str, Any] = data.userinfo
    account_id: str = userinfo.get("account_id", "")

    coordinator = data.coordinator
    await coordinator.async_config_entry_first_refresh()

    receiver_parcels: list[dict] = _active_receiver(coordinator)
    current_barcodes: set[str] = {p["barcode"] for p in receiver_parcels if p.get("barcode")}

    # Remove stale per-parcel sensors that are no longer active.
    registry = er.async_get(hass)
    non_parcel_unique_ids = {
        f"{account_id}_incoming_parcels",
        f"{account_id}_next_delivery",
        f"{account_id}_en_route_to_service_point",
        f"{account_id}_outgoing_parcels",
        f"{account_id}_delivered_parcels",
        f"{account_id}_letters",
    }
    for entity_entry in er.async_entries_for_config_entry(registry, entry.entry_id):
        if (
            entity_entry.unique_id.startswith(f"{account_id}_")
            and entity_entry.unique_id not in non_parcel_unique_ids
        ):
            barcode = entity_entry.unique_id[len(f"{account_id}_"):]
            if barcode not in current_barcodes:
                registry.async_remove(entity_entry.entity_id)

    entities: list[SensorEntity] = [
        PostNLIncomingParcelsSensor(
            coordinator=coordinator,
            userinfo=userinfo,
            async_add_entities=async_add_entities,
            known_barcodes=current_barcodes,
        ),
        PostNLNextDeliverySensor(coordinator=coordinator, userinfo=userinfo),
        PostNLEnRouteToServicePointSensor(coordinator=coordinator, userinfo=userinfo),
        PostNLOutgoingParcelsSensor(coordinator=coordinator, userinfo=userinfo),
        PostNLDeliveredParcelsSensor(coordinator=coordinator, userinfo=userinfo),
        PostNLLettersSensor(coordinator=coordinator, userinfo=userinfo),
    ]

    for parcel in receiver_parcels:
        if parcel.get("barcode"):
            entities.append(PostNLParcelSensor(coordinator=coordinator, userinfo=userinfo, barcode=parcel["barcode"]))

    async_add_entities(entities)


def _build_device_info(userinfo: dict[str, Any]) -> DeviceInfo:
    """Return DeviceInfo shared by all sensors for this account."""
    return DeviceInfo(
        identifiers={(DOMAIN, userinfo.get("account_id", ""))},
        name=userinfo.get("email"),
        manufacturer="PostNL",
        entry_type=DeviceEntryType.SERVICE,
        configuration_url="https://jouw.postnl.nl",
    )


def _active_receiver(coordinator: PostNLCoordinator) -> list[dict]:
    """Return non-delivered receiver parcels."""
    return [p for p in (coordinator.data or {}).get("receiver", []) if not p.get("delivered")]


class PostNLIncomingParcelsSensor(CoordinatorEntity[PostNLCoordinator], SensorEntity):
    """Summary sensor for active incoming PostNL parcels.

    Spawns a per-parcel :class:`PostNLParcelSensor` whenever a new barcode
    appears. Stale per-parcel sensors remove themselves once their barcode
    drops out of the coordinator data — see ``PostNLParcelSensor``.
    """

    _attr_name = "PostNL Incoming Parcels"
    _attr_icon = "mdi:package-variant-closed"
    _attr_native_unit_of_measurement = "parcels"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_attribution = "Data provided by PostNL"
    _unrecorded_attributes = frozenset({"parcels"})

    def __init__(
        self,
        coordinator: PostNLCoordinator,
        userinfo: dict[str, Any],
        async_add_entities: AddEntitiesCallback,
        known_barcodes: set[str] | None = None,
    ) -> None:
        super().__init__(coordinator)
        self._userinfo = userinfo
        self._async_add_entities = async_add_entities
        account_id: str = userinfo.get("account_id", "")
        self._attr_unique_id = f"{account_id}_incoming_parcels"
        self._attr_device_info = _build_device_info(userinfo)
        self._known_barcodes: set[str] = known_barcodes or set()

    @property
    def native_value(self) -> int:
        return len(_active_receiver(self.coordinator))

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {"parcels": _active_receiver(self.coordinator)}

    def _handle_coordinator_update(self) -> None:
        current_barcodes = {
            p["barcode"]
            for p in _active_receiver(self.coordinator)
            if p.get("barcode")
        }

        new_barcodes = current_barcodes - self._known_barcodes
        if new_barcodes:
            self._async_add_entities(
                PostNLParcelSensor(coordinator=self.coordinator, userinfo=self._userinfo, barcode=b)
                for b in new_barcodes
            )

        self._known_barcodes = current_barcodes
        super()._handle_coordinator_update()


class PostNLParcelSensor(CoordinatorEntity[PostNLCoordinator], SensorEntity):
    """Per-parcel sensor for a single active incoming PostNL shipment."""

    _attr_icon = "mdi:package-variant-closed"
    _attr_attribution = "Data provided by PostNL"

    def __init__(
        self,
        coordinator: PostNLCoordinator,
        userinfo: dict[str, Any],
        barcode: str,
    ) -> None:
        super().__init__(coordinator)
        self._barcode = barcode
        account_id: str = userinfo.get("account_id", "")
        self._attr_unique_id = f"{account_id}_{barcode}"
        self._attr_name = f"PostNL Parcel {barcode}"
        self._attr_device_info = _build_device_info(userinfo)

    def _get_parcel(self) -> dict | None:
        for parcel in _active_receiver(self.coordinator):
            if parcel.get("barcode") == self._barcode:
                return parcel
        return None

    @property
    def native_value(self) -> str | None:
        parcel = self._get_parcel()
        return parcel.get("status_message") if parcel else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        parcel = self._get_parcel()
        return dict(parcel) if parcel else {}

    def _handle_coordinator_update(self) -> None:
        """Self-remove once this parcel falls out of the coordinator data."""
        if self._get_parcel() is None and self.hass is not None:
            self.hass.async_create_task(self.async_remove(force_remove=True))
            return
        super()._handle_coordinator_update()


class PostNLNextDeliverySensor(CoordinatorEntity[PostNLCoordinator], SensorEntity):
    """Sensor reporting the earliest expected delivery datetime across all active incoming parcels."""

    _attr_name = "PostNL Next Delivery"
    _attr_icon = "mdi:clock-fast"
    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_attribution = "Data provided by PostNL"

    def __init__(
        self,
        coordinator: PostNLCoordinator,
        userinfo: dict[str, Any],
    ) -> None:
        super().__init__(coordinator)
        account_id: str = userinfo.get("account_id", "")
        self._attr_unique_id = f"{account_id}_next_delivery"
        self._attr_device_info = _build_device_info(userinfo)

    def _delivery_moments(self) -> list[tuple[datetime, dict]]:
        result: list[tuple[datetime, dict]] = []
        for parcel in _active_receiver(self.coordinator):
            moment_str = parcel.get("planned_from") or parcel.get("planned_date")
            if not moment_str:
                continue
            try:
                dt = datetime.fromisoformat(moment_str.replace("Z", "+00:00"))
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                result.append((dt, parcel))
            except ValueError:
                _LOGGER.debug("Could not parse delivery moment: %s", moment_str)
        return result

    @property
    def native_value(self) -> datetime | None:
        moments = self._delivery_moments()
        return min(dt for dt, _ in moments) if moments else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        moments = self._delivery_moments()
        if not moments:
            return {}
        _, earliest = min(moments, key=lambda x: x[0])
        return {
            "barcode": earliest.get("barcode"),
            "sender": earliest.get("source_display_name"),
        }


class PostNLEnRouteToServicePointSensor(CoordinatorEntity[PostNLCoordinator], SensorEntity):
    """Sensor reporting active incoming parcels destined for a PostNL point."""

    _attr_name = "PostNL Parcels En Route to PostNL Point"
    _attr_icon = "mdi:truck-delivery"
    _attr_native_unit_of_measurement = "parcels"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_attribution = "Data provided by PostNL"
    _unrecorded_attributes = frozenset({"parcels"})

    def __init__(
        self,
        coordinator: PostNLCoordinator,
        userinfo: dict[str, Any],
    ) -> None:
        super().__init__(coordinator)
        account_id: str = userinfo.get("account_id", "")
        self._attr_unique_id = f"{account_id}_en_route_to_service_point"
        self._attr_device_info = _build_device_info(userinfo)

    def _get_service_point_parcels(self) -> list[dict]:
        return [
            p for p in _active_receiver(self.coordinator)
            if p.get("delivery_address_type") == DELIVERY_ADDRESS_SERVICE_POINT
        ]

    @property
    def native_value(self) -> int:
        return len(self._get_service_point_parcels())

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {
            "parcels": [
                {
                    "barcode": p.get("barcode"),
                    "sender": p.get("source_display_name"),
                    "status": p.get("status_message"),
                }
                for p in self._get_service_point_parcels()
            ]
        }


class PostNLOutgoingParcelsSensor(CoordinatorEntity[PostNLCoordinator], SensorEntity):
    """Summary sensor for active outgoing PostNL shipments."""

    _attr_name = "PostNL Outgoing Parcels"
    _attr_icon = "mdi:package-variant-closed"
    _attr_native_unit_of_measurement = "parcels"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_attribution = "Data provided by PostNL"
    _unrecorded_attributes = frozenset({"shipments"})

    def __init__(
        self,
        coordinator: PostNLCoordinator,
        userinfo: dict[str, Any],
    ) -> None:
        super().__init__(coordinator)
        account_id: str = userinfo.get("account_id", "")
        self._attr_unique_id = f"{account_id}_outgoing_parcels"
        self._attr_device_info = _build_device_info(userinfo)

    def _active_sender(self) -> list[dict]:
        return [p for p in (self.coordinator.data or {}).get("sender", []) if not p.get("delivered")]

    @property
    def native_value(self) -> int:
        return len(self._active_sender())

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {
            "shipments": [
                {
                    "barcode": p.get("barcode"),
                    "key": p.get("key"),
                    "status": p.get("status_message"),
                    "shipment_type": p.get("shipment_type"),
                    "receiver": p.get("receiver_title"),
                    "planned_date": p.get("planned_date"),
                    "planned_from": p.get("planned_from"),
                    "planned_to": p.get("planned_to"),
                }
                for p in self._active_sender()
            ]
        }


class PostNLDeliveredParcelsSensor(CoordinatorEntity[PostNLCoordinator], SensorEntity):
    """Sensor reporting recently delivered incoming PostNL parcels."""

    _attr_name = "PostNL Delivered Parcels"
    _attr_icon = "mdi:package-variant"
    _attr_native_unit_of_measurement = "parcels"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_attribution = "Data provided by PostNL"
    _unrecorded_attributes = frozenset({"parcels"})

    def __init__(
        self,
        coordinator: PostNLCoordinator,
        userinfo: dict[str, Any],
    ) -> None:
        super().__init__(coordinator)
        account_id: str = userinfo.get("account_id", "")
        self._attr_unique_id = f"{account_id}_delivered_parcels"
        self._attr_device_info = _build_device_info(userinfo)

    @property
    def _parcels(self) -> list[dict]:
        return self.coordinator.delivered_receiver or []

    @property
    def native_value(self) -> int:
        return len(self._parcels)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {
            "parcels": [
                {
                    "barcode": p.get("barcode"),
                    "sender": p.get("source_display_name"),
                    "status": p.get("status_message"),
                    "delivery_date": p.get("delivery_date"),
                }
                for p in self._parcels
            ]
        }


class PostNLLettersSensor(CoordinatorEntity[PostNLCoordinator], SensorEntity):
    """Sensor reporting letters announced by PostNL's MyMail service."""

    _attr_name = "PostNL Letters"
    _attr_icon = "mdi:email"
    _attr_native_unit_of_measurement = "letters"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_attribution = "Data provided by PostNL"
    _unrecorded_attributes = frozenset({"letters"})

    def __init__(
        self,
        coordinator: PostNLCoordinator,
        userinfo: dict[str, Any],
    ) -> None:
        super().__init__(coordinator)
        account_id: str = userinfo.get("account_id", "")
        self._attr_unique_id = f"{account_id}_letters"
        self._attr_device_info = _build_device_info(userinfo)

    @property
    def _letters(self) -> list[dict]:
        return self.coordinator.letters or []

    @property
    def native_value(self) -> int:
        return len(self._letters)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {
            "unread": sum(1 for letter in self._letters if letter.get("unread")),
            "letters": self._letters,
        }
