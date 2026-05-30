from __future__ import annotations

import logging

import requests
import urllib3
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady, HomeAssistantError
from homeassistant.helpers import device_registry as dr, entity_registry as er

from .auth import AsyncConfigEntryAuth
from .const import DOMAIN, PLATFORMS
from .coordinator import PostNLCoordinator
from .login_api import PostNLLoginAPI

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up PostNL from config entry."""
    _LOGGER.debug("Setup Entry PostNL")

    hass.data.setdefault(DOMAIN, {})

    auth = AsyncConfigEntryAuth(hass, entry)

    try:
        await auth.check_and_refresh_token()
    except HomeAssistantError as exception:
        raise ConfigEntryAuthFailed("Unable to authenticate with PostNL") from exception

    hass.data[DOMAIN][entry.entry_id] = {"auth": auth}

    postnl_login_api = PostNLLoginAPI(auth.access_token)

    try:
        userinfo = await hass.async_add_executor_job(postnl_login_api.userinfo)
    except (requests.exceptions.RequestException, urllib3.exceptions.MaxRetryError) as exception:
        raise ConfigEntryNotReady("Unable to retrieve user information from PostNL.") from exception

    if "error" in userinfo:
        raise ConfigEntryNotReady("Error in retrieving user information from PostNL.")

    hass.data[DOMAIN][entry.entry_id]["userinfo"] = userinfo

    coordinator = PostNLCoordinator(hass, entry)
    hass.data[DOMAIN][entry.entry_id]["coordinator"] = coordinator

    device_registry = dr.async_get(hass)
    entity_registry = er.async_get(hass)

    for device_entry in dr.async_entries_for_config_entry(device_registry, entry.entry_id):
        if device_entry.identifiers == {(DOMAIN, userinfo.get("account_id"))}:
            _LOGGER.debug("Migrating entry %s", device_entry.identifiers)
            for entity_entry in er.async_entries_for_device(entity_registry, device_entry.id, True):
                _LOGGER.debug("Migrating entity: %s", entity_entry.unique_id)
                if entity_entry.unique_id.startswith(userinfo.get("account_id")):
                    continue
                unique_id_parts = entity_entry.unique_id.split("_")
                entity_new_unique_id = userinfo.get("account_id") + "_" + (
                    unique_id_parts[1] if len(unique_id_parts) > 1 else unique_id_parts[0]
                )
                _LOGGER.debug("New unique ID for entity: %s", entity_new_unique_id)
                entity_registry.async_update_entity(
                    entity_id=entity_entry.entity_id, new_unique_id=entity_new_unique_id
                )

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload PostNL config entry."""
    _LOGGER.debug("Unloading PostNL integration")
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)

    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id)

    return unload_ok
