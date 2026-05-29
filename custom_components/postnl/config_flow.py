import logging
from typing import Any

import aiohttp
import voluptuous as vol
from homeassistant.config_entries import ConfigEntry, ConfigFlow
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.data_entry_flow import FlowResult

from .auth import PostNLAuth, PostNLAuthError
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

_STEP_SCHEMA = vol.Schema({
    vol.Required(CONF_USERNAME): str,
    vol.Required(CONF_PASSWORD): str,
})


class PostNLConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    _reauth_entry: ConfigEntry | None = None

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        errors: dict[str, str] = {}

        if user_input is not None:
            token, errors = await self._do_login(user_input)
            if not errors:
                await self.async_set_unique_id(user_input[CONF_USERNAME].lower())
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=user_input[CONF_USERNAME],
                    data={
                        CONF_USERNAME: user_input[CONF_USERNAME],
                        CONF_PASSWORD: user_input[CONF_PASSWORD],
                        "token": token,
                    },
                )

        return self.async_show_form(
            step_id="user",
            data_schema=_STEP_SCHEMA,
            errors=errors,
        )

    async def async_step_reauth(self, user_input=None) -> FlowResult:
        self._reauth_entry = self.hass.config_entries.async_get_entry(
            self.context["entry_id"]
        )
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        errors: dict[str, str] = {}

        if user_input is not None:
            token, errors = await self._do_login(user_input)
            if not errors:
                self.hass.config_entries.async_update_entry(
                    self._reauth_entry,
                    data={
                        **self._reauth_entry.data,
                        CONF_USERNAME: user_input[CONF_USERNAME],
                        CONF_PASSWORD: user_input[CONF_PASSWORD],
                        "token": token,
                    },
                )
                await self.hass.config_entries.async_reload(self._reauth_entry.entry_id)
                return self.async_abort(reason="reauth_successful")

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=_STEP_SCHEMA,
            errors=errors,
        )

    async def _do_login(self, user_input: dict) -> tuple[dict | None, dict[str, str]]:
        try:
            token = await PostNLAuth(
                user_input[CONF_USERNAME],
                user_input[CONF_PASSWORD],
            ).async_login()
            return token, {}
        except PostNLAuthError as err:
            _LOGGER.debug("PostNL login failed: %s", err)
            return None, {"base": "invalid_auth"}
        except aiohttp.ClientError as err:
            _LOGGER.debug("PostNL connection error: %s", err)
            return None, {"base": "cannot_connect"}
