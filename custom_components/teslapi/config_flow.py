"""Config flow for TeslaPi integration.

Supports three discovery methods:
- Manual setup (user enters host/port)
- Zeroconf/mDNS auto-discovery (_teslapi._tcp.local)
- DHCP discovery (hostname starting with "teslapi")
"""

from __future__ import annotations

from typing import Any

import aiohttp
import voluptuous as vol

from homeassistant.components import zeroconf as zc
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import (
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
        self, discovery_info: zc.ZeroconfServiceInfo
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
        self, discovery_info: Any
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

            # Test connection
            try:
                info = await self._test_connection(host, port)
            except CannotConnect:
                errors["base"] = "cannot_connect"
            except Exception:
                LOGGER.exception("Unexpected error during config flow")
                errors["base"] = "unknown"
            else:
                title = f"{DEFAULT_NAME} ({info.get('hostname', host)})"
                return self.async_create_entry(
                    title=title,
                    data={
                        CONF_HOST: host,
                        CONF_PORT: port,
                    },
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

    async def _test_connection(self, host: str, port: int) -> dict[str, Any]:
        """Test connectivity to the TeslaPi device and return status info."""
        session = async_get_clientsession(self.hass)
        url = f"http://{host}:{port}/api/status"
        try:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status != 200:
                    raise CannotConnect(f"HTTP {resp.status}")
                data = await resp.json()
                return data.get("system", {})
        except (aiohttp.ClientError, TimeoutError) as err:
            raise CannotConnect(str(err)) from err

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


class CannotConnect(Exception):
    """Error to indicate we cannot connect."""
