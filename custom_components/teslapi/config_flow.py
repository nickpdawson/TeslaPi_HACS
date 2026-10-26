"""Config flow for TeslaPi integration.

Supports three discovery methods:
- Manual setup (user enters host/port)
- Zeroconf/mDNS auto-discovery (_teslapi._tcp.local)
- DHCP discovery (hostname starting with "teslapi")
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import aiohttp
import voluptuous as vol

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.const import CONF_PASSWORD
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_create_clientsession
from homeassistant.helpers.selector import (
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)
from homeassistant.helpers.service_info.dhcp import DhcpServiceInfo
from homeassistant.helpers.service_info.zeroconf import ZeroconfServiceInfo

from .api import (
    TeslaPiAuthError,
    TeslaPiClient,
    TeslaPiConnectionError,
    TeslaPiError,
)
from .const import (
    API_STATUS,
    CONF_EXTRA_HOSTS,
    CONF_HOST,
    CONF_PORT,
    CONF_SCAN_INTERVAL,
    DEFAULT_NAME,
    DEFAULT_PORT,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    LOGGER,
    MAX_SCAN_INTERVAL,
    MIN_SCAN_INTERVAL,
)


class TeslaPiConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for TeslaPi."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize the config flow."""
        self._discovered_host: str | None = None
        self._discovered_port: int = DEFAULT_PORT

    async def async_step_zeroconf(
        self, discovery_info: ZeroconfServiceInfo
    ) -> ConfigFlowResult:
        """Handle Zeroconf/mDNS discovery of a TeslaPi device."""
        host = str(discovery_info.host)
        port = discovery_info.port or DEFAULT_PORT

        LOGGER.info("TeslaPi discovered via mDNS at %s:%s", host, port)

        # Set unique ID from host to prevent duplicate entries
        await self.async_set_unique_id(f"{host}:{port}")
        self._abort_if_unique_id_configured(updates={CONF_HOST: host, CONF_PORT: port})

        self._discovered_host = host
        self._discovered_port = port

        # Show confirmation to user
        self.context["title_placeholders"] = {"name": f"TeslaPi ({host})"}
        return await self.async_step_zeroconf_confirm()

    async def async_step_zeroconf_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Confirm Zeroconf discovery."""
        if user_input is not None:
            host = self._discovered_host
            port = self._discovered_port

            try:
                info = await self._test_connection(host, port)
            except CannotConnect:
                return self.async_abort(reason="cannot_connect")
            except InvalidAuth:
                return self.async_abort(reason="invalid_auth")

            title = f"{DEFAULT_NAME} ({info.get('hostname', host)})"
            return self.async_create_entry(
                title=title,
                data={CONF_HOST: host, CONF_PORT: port},
                options={CONF_SCAN_INTERVAL: DEFAULT_SCAN_INTERVAL},
            )

        return self.async_show_form(
            step_id="zeroconf_confirm",
            description_placeholders={
                "host": self._discovered_host,
                "port": str(self._discovered_port),
            },
        )

    async def async_step_dhcp(
        self, discovery_info: DhcpServiceInfo
    ) -> ConfigFlowResult:
        """Handle DHCP discovery."""
        host = discovery_info.ip
        LOGGER.info("TeslaPi discovered via DHCP at %s", host)

        await self.async_set_unique_id(f"{host}:{DEFAULT_PORT}")
        self._abort_if_unique_id_configured(updates={CONF_HOST: host})

        self._discovered_host = host
        self._discovered_port = DEFAULT_PORT

        self.context["title_placeholders"] = {"name": f"TeslaPi ({host})"}
        return await self.async_step_zeroconf_confirm()

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle the initial step."""
        errors: dict[str, str] = {}

        if user_input is not None:
            host = user_input[CONF_HOST].strip()
            port = user_input.get(CONF_PORT, DEFAULT_PORT)

            # Check for duplicate entries
            await self.async_set_unique_id(f"{host}:{port}")
            self._abort_if_unique_id_configured()

            password = user_input.get(CONF_PASSWORD) or None
            try:
                info = await self._test_connection(host, port, password)
            except CannotConnect:
                errors["base"] = "cannot_connect"
            except InvalidAuth:
                errors["base"] = "invalid_auth"
            except Exception:
                LOGGER.exception("Unexpected error during config flow")
                errors["base"] = "unknown"
            else:
                title = f"{DEFAULT_NAME} ({info.get('hostname') or host})"
                data = {CONF_HOST: host, CONF_PORT: port}
                if password:
                    data[CONF_PASSWORD] = password
                return self.async_create_entry(
                    title=title,
                    data=data,
                    options={
                        CONF_SCAN_INTERVAL: user_input.get(
                            CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL
                        ),
                    },
                )

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_HOST): str,
                    vol.Optional(CONF_PORT, default=DEFAULT_PORT): vol.Coerce(int),
                    vol.Optional(CONF_PASSWORD): PASSWORD_SELECTOR,
                    vol.Optional(
                        CONF_SCAN_INTERVAL, default=DEFAULT_SCAN_INTERVAL
                    ): vol.All(
                        vol.Coerce(int),
                        vol.Range(min=MIN_SCAN_INTERVAL, max=MAX_SCAN_INTERVAL),
                    ),
                }
            ),
            errors=errors,
        )

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> ConfigFlowResult:
        """Start reauth when the Pi rejects our password or wants one."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for the TeslaPi password again."""
        entry = self._get_reauth_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            password = user_input[CONF_PASSWORD]
            try:
                await self._test_connection(
                    entry.data[CONF_HOST],
                    entry.data.get(CONF_PORT, DEFAULT_PORT),
                    password,
                )
            except CannotConnect:
                errors["base"] = "cannot_connect"
            except InvalidAuth:
                errors["base"] = "invalid_auth"
            except Exception:
                LOGGER.exception("Unexpected error during reauth")
                errors["base"] = "unknown"
            else:
                return self.async_update_reload_and_abort(
                    entry, data_updates={CONF_PASSWORD: password}
                )

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema({vol.Required(CONF_PASSWORD): PASSWORD_SELECTOR}),
            description_placeholders={"host": entry.data[CONF_HOST]},
            errors=errors,
        )

    async def _test_connection(
        self, host: str, port: int, password: str | None = None
    ) -> dict[str, Any]:
        """Log in if a password is given, read status, return the system block."""
        return await validate_connection(self.hass, host, port, password)

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        """Get the options flow."""
        return TeslaPiOptionsFlow()


class TeslaPiOptionsFlow(OptionsFlow):
    """Handle options for TeslaPi."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Manage the options."""
        if user_input is not None:
            return self.async_create_entry(data=user_input)

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Optional(
                        CONF_SCAN_INTERVAL,
                        default=self.config_entry.options.get(
                            CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL
                        ),
                    ): vol.All(
                        vol.Coerce(int),
                        vol.Range(min=MIN_SCAN_INTERVAL, max=MAX_SCAN_INTERVAL),
                    ),
                    vol.Optional(
                        CONF_EXTRA_HOSTS,
                        default=self.config_entry.options.get(
                            CONF_EXTRA_HOSTS, ""
                        ),
                    ): str,
                }
            ),
        )


async def validate_connection(
    hass, host: str, port: int, password: str | None = None
) -> dict[str, Any]:
    """Check the device answers and accepts our credentials."""
    session = async_create_clientsession(
        hass, auto_cleanup=False, cookie_jar=aiohttp.DummyCookieJar()
    )
    client = TeslaPiClient(session, [host], port, password)
    try:
        if password:
            await client.login()
        data = await client.get(API_STATUS)
    except TeslaPiAuthError as err:
        raise InvalidAuth from err
    except (TeslaPiConnectionError, TeslaPiError) as err:
        raise CannotConnect(str(err)) from err
    finally:
        await session.close()
    return data.get("system") or {}


PASSWORD_SELECTOR = TextSelector(
    TextSelectorConfig(type=TextSelectorType.PASSWORD, autocomplete="current-password")
)


class CannotConnect(Exception):
    """Error to indicate we cannot connect."""


class InvalidAuth(Exception):
    """The TeslaPi rejected the password, or needs one."""
