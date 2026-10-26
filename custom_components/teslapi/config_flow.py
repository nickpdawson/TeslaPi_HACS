"""Config flow for TeslaPi.

Entries can come from manual setup, Zeroconf (_teslapi._tcp) or DHCP
(hostname teslapi*).
"""

from __future__ import annotations

from collections.abc import Mapping
import ipaddress
import re
from typing import Any

import aiohttp
import voluptuous as vol

from homeassistant.config_entries import (
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlowWithReload,
)
from homeassistant.const import CONF_MAC, CONF_PASSWORD
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.aiohttp_client import async_create_clientsession
from homeassistant.helpers.device_registry import format_mac
from homeassistant.helpers.selector import (
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)
from homeassistant.helpers.service_info.dhcp import DhcpServiceInfo
from homeassistant.helpers.service_info.zeroconf import ZeroconfServiceInfo

from .api import TeslaPiAuthError, TeslaPiClient, TeslaPiError
from .const import (
    API_STATUS,
    CONF_EXTRA_HOSTS,
    CONF_HOST,
    CONF_PORT,
    CONF_SCAN_INTERVAL,
    CONFIG_ENTRY_VERSION,
    DEFAULT_NAME,
    DEFAULT_PORT,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    LOGGER,
    MAX_SCAN_INTERVAL,
    MIN_SCAN_INTERVAL,
)
from .coordinator import TeslaPiConfigEntry, extra_hosts_from_options

PASSWORD_SELECTOR = TextSelector(
    TextSelectorConfig(type=TextSelectorType.PASSWORD, autocomplete="current-password")
)
HOSTS_SELECTOR = TextSelector(TextSelectorConfig(multiple=True))
PORT_VALIDATOR = vol.All(vol.Coerce(int), vol.Range(min=1, max=65535))
SCAN_VALIDATOR = vol.All(
    vol.Coerce(int), vol.Range(min=MIN_SCAN_INTERVAL, max=MAX_SCAN_INTERVAL)
)

_HOSTNAME_RE = re.compile(
    r"^(?=.{1,253}$)[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
    r"(\.[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?)*$"
)


class CannotConnect(Exception):
    """Error to indicate we cannot connect."""


class InvalidAuth(Exception):
    """The TeslaPi rejected the password, or needs one."""


class InvalidHost(Exception):
    """The host is not an IP address or hostname."""


def parse_host(value: str) -> tuple[str, int | None]:
    """Turn what a user pastes into (host, port).

    Accepts "10.0.0.5", "http://teslapi.local/", "teslapi:8080" or
    "[fd00::5]:80". Raises InvalidHost for anything else.
    """
    text = value.strip()
    text = re.sub(r"^[A-Za-z][A-Za-z0-9+.-]*://", "", text)
    text = text.split("/", 1)[0].split("?", 1)[0]
    port_text = ""
    if text.startswith("["):
        host, _, rest = text[1:].partition("]")
        if rest.startswith(":"):
            port_text = rest[1:]
    elif text.count(":") == 1:
        host, port_text = text.split(":")
    else:
        host = text
    host = host.strip().rstrip(".")

    port: int | None = None
    if port_text:
        if not port_text.isdigit() or not 1 <= int(port_text) <= 65535:
            raise InvalidHost(value)
        port = int(port_text)

    try:
        ipaddress.ip_address(host)
    except ValueError:
        if not _HOSTNAME_RE.match(host):
            raise InvalidHost(value) from None
    return host, port


async def validate_connection(
    hass: HomeAssistant, host: str, port: int, password: str | None = None
) -> dict[str, Any]:
    """Check the device answers and accepts our credentials.

    Returns the system block of /api/status.
    """
    session = async_create_clientsession(
        hass, auto_cleanup=False, cookie_jar=aiohttp.DummyCookieJar()
    )
    client = TeslaPiClient(session, [host], port, password)
    try:
        # /api/health is open even when the Pi has a password, so a failure
        # here means the host is wrong or offline, not that auth failed.
        await client.health()
        if password:
            await client.login()
        data = await client.get(API_STATUS)
    except TeslaPiAuthError as err:
        raise InvalidAuth from err
    except TeslaPiError as err:
        raise CannotConnect(str(err)) from err
    finally:
        await session.close()
    return data.get("system") or {}


class TeslaPiConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for TeslaPi."""

    VERSION = CONFIG_ENTRY_VERSION

    def __init__(self) -> None:
        """Initialize the config flow."""
        self._discovered_host: str | None = None
        self._discovered_port: int = DEFAULT_PORT
        self._discovered_mac: str | None = None

    async def _validate(
        self, host: str, port: int, password: str | None, errors: dict[str, str]
    ) -> dict[str, Any] | None:
        """Run validate_connection and fill errors. Returns None on failure."""
        try:
            return await validate_connection(self.hass, host, port, password)
        except CannotConnect:
            errors["base"] = "cannot_connect"
        except InvalidAuth:
            errors["base"] = "invalid_auth"
        except Exception:
            LOGGER.exception("Unexpected error while checking the TeslaPi")
            errors["base"] = "unknown"
        return None

    @callback
    def _entry_for_host(self, host: str) -> TeslaPiConfigEntry | None:
        for entry in self._async_current_entries(include_ignore=False):
            hosts = [entry.data.get(CONF_HOST)] + extra_hosts_from_options(
                entry.options
            )
            if host in hosts:
                return entry
        return None

    async def async_step_zeroconf(
        self, discovery_info: ZeroconfServiceInfo
    ) -> ConfigFlowResult:
        """Handle Zeroconf discovery of a TeslaPi."""
        host = str(discovery_info.ip_address)
        port = discovery_info.port or DEFAULT_PORT
        LOGGER.debug("TeslaPi discovered via Zeroconf at %s:%s", host, port)

        mdns_name = discovery_info.hostname.rstrip(".")
        if self._entry_for_host(host) or self._entry_for_host(mdns_name):
            return self.async_abort(reason="already_configured")

        await self.async_set_unique_id(f"{host}:{port}")
        self._abort_if_unique_id_configured(updates={CONF_HOST: host, CONF_PORT: port})

        self._discovered_host = host
        self._discovered_port = port
        self.context["title_placeholders"] = {"name": f"TeslaPi ({host})"}
        return await self.async_step_discovery_confirm()

    async def async_step_dhcp(
        self, discovery_info: DhcpServiceInfo
    ) -> ConfigFlowResult:
        """Handle DHCP discovery, and follow a known Pi to a new address."""
        host = discovery_info.ip
        mac = format_mac(discovery_info.macaddress)
        LOGGER.debug("TeslaPi discovered via DHCP at %s", host)

        for entry in self._async_current_entries(include_ignore=False):
            if entry.data.get(CONF_MAC) == mac or entry.data.get(CONF_HOST) == host:
                if entry.data.get(CONF_MAC) != mac or entry.data.get(CONF_HOST) != host:
                    moved = entry.data.get(CONF_HOST) != host
                    port = entry.data.get(CONF_PORT, DEFAULT_PORT)
                    self.hass.config_entries.async_update_entry(
                        entry,
                        data={**entry.data, CONF_HOST: host, CONF_MAC: mac},
                        unique_id=f"{host}:{port}",
                    )
                    if moved:
                        self.hass.config_entries.async_schedule_reload(entry.entry_id)
                return self.async_abort(reason="already_configured")

        if self._entry_for_host(host):
            return self.async_abort(reason="already_configured")

        await self.async_set_unique_id(f"{host}:{DEFAULT_PORT}")
        self._abort_if_unique_id_configured()

        self._discovered_host = host
        self._discovered_port = DEFAULT_PORT
        self._discovered_mac = mac
        self.context["title_placeholders"] = {"name": f"TeslaPi ({host})"}
        return await self.async_step_discovery_confirm()

    async def async_step_discovery_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Confirm a discovered TeslaPi, with an optional password."""
        assert self._discovered_host is not None
        errors: dict[str, str] = {}
        if user_input is not None:
            password = user_input.get(CONF_PASSWORD) or None
            info = await self._validate(
                self._discovered_host, self._discovered_port, password, errors
            )
            if info is not None:
                data: dict[str, Any] = {
                    CONF_HOST: self._discovered_host,
                    CONF_PORT: self._discovered_port,
                }
                if password:
                    data[CONF_PASSWORD] = password
                if self._discovered_mac:
                    data[CONF_MAC] = self._discovered_mac
                return self.async_create_entry(
                    title=f"{DEFAULT_NAME} ({info.get('hostname') or self._discovered_host})",
                    data=data,
                    options={CONF_SCAN_INTERVAL: DEFAULT_SCAN_INTERVAL},
                )

        return self.async_show_form(
            step_id="discovery_confirm",
            data_schema=vol.Schema({vol.Optional(CONF_PASSWORD): PASSWORD_SELECTOR}),
            description_placeholders={
                "host": self._discovered_host,
                "port": str(self._discovered_port),
            },
            errors=errors,
        )

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle manual setup."""
        errors: dict[str, str] = {}

        if user_input is not None:
            try:
                host, embedded_port = parse_host(user_input[CONF_HOST])
            except InvalidHost:
                errors[CONF_HOST] = "invalid_host"
            else:
                port = embedded_port or user_input.get(CONF_PORT, DEFAULT_PORT)
                await self.async_set_unique_id(f"{host}:{port}")
                self._abort_if_unique_id_configured()

                password = user_input.get(CONF_PASSWORD) or None
                info = await self._validate(host, port, password, errors)
                if info is not None:
                    data: dict[str, Any] = {CONF_HOST: host, CONF_PORT: port}
                    if password:
                        data[CONF_PASSWORD] = password
                    return self.async_create_entry(
                        title=f"{DEFAULT_NAME} ({info.get('hostname') or host})",
                        data=data,
                        options={
                            CONF_SCAN_INTERVAL: user_input.get(
                                CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL
                            ),
                        },
                    )

        schema = vol.Schema(
            {
                vol.Required(CONF_HOST): str,
                vol.Optional(CONF_PORT, default=DEFAULT_PORT): PORT_VALIDATOR,
                vol.Optional(CONF_PASSWORD): PASSWORD_SELECTOR,
                vol.Optional(
                    CONF_SCAN_INTERVAL, default=DEFAULT_SCAN_INTERVAL
                ): SCAN_VALIDATOR,
            }
        )
        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(
                schema, {k: v for k, v in (user_input or {}).items() if k != CONF_PASSWORD}
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
            info = await self._validate(
                entry.data[CONF_HOST],
                entry.data.get(CONF_PORT, DEFAULT_PORT),
                password,
                errors,
            )
            if info is not None:
                return self.async_update_reload_and_abort(
                    entry, data_updates={CONF_PASSWORD: password}
                )

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema({vol.Required(CONF_PASSWORD): PASSWORD_SELECTOR}),
            description_placeholders={"host": entry.data[CONF_HOST]},
            errors=errors,
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Change the primary host or port."""
        entry = self._get_reconfigure_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                host, embedded_port = parse_host(user_input[CONF_HOST])
            except InvalidHost:
                errors[CONF_HOST] = "invalid_host"
            else:
                port = embedded_port or user_input[CONF_PORT]
                unique_id = f"{host}:{port}"
                clash = any(
                    e.unique_id == unique_id and e.entry_id != entry.entry_id
                    for e in self._async_current_entries()
                )
                if clash:
                    return self.async_abort(reason="already_configured")
                info = await self._validate(
                    host, port, entry.data.get(CONF_PASSWORD), errors
                )
                if info is not None:
                    data_updates: dict[str, Any] = {CONF_HOST: host, CONF_PORT: port}
                    return self.async_update_reload_and_abort(
                        entry, unique_id=unique_id, data_updates=data_updates
                    )

        current = user_input or {
            CONF_HOST: entry.data[CONF_HOST],
            CONF_PORT: entry.data.get(CONF_PORT, DEFAULT_PORT),
        }
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=self.add_suggested_values_to_schema(
                vol.Schema(
                    {
                        vol.Required(CONF_HOST): str,
                        vol.Required(CONF_PORT): PORT_VALIDATOR,
                    }
                ),
                current,
            ),
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: TeslaPiConfigEntry) -> TeslaPiOptionsFlow:
        """Get the options flow."""
        return TeslaPiOptionsFlow()


class TeslaPiOptionsFlow(OptionsFlowWithReload):
    """Polling interval, fallback hosts and password."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Manage the options."""
        entry = self.config_entry
        errors: dict[str, str] = {}

        if user_input is not None:
            port = entry.data.get(CONF_PORT, DEFAULT_PORT)
            hosts: list[str] = []
            for raw in user_input.get(CONF_EXTRA_HOSTS) or []:
                if not raw.strip():
                    continue
                try:
                    host, embedded_port = parse_host(raw)
                except InvalidHost:
                    errors[CONF_EXTRA_HOSTS] = "invalid_host"
                    break
                if embedded_port not in (None, port):
                    errors[CONF_EXTRA_HOSTS] = "fallback_port"
                    break
                if host != entry.data[CONF_HOST] and host not in hosts:
                    hosts.append(host)

            password = user_input.get(CONF_PASSWORD) or None
            if not errors and password:
                try:
                    await validate_connection(
                        self.hass, entry.data[CONF_HOST], port, password
                    )
                except InvalidAuth:
                    errors[CONF_PASSWORD] = "invalid_auth"
                except CannotConnect:
                    errors["base"] = "cannot_connect"

            if not errors:
                if password and password != entry.data.get(CONF_PASSWORD):
                    self.hass.config_entries.async_update_entry(
                        entry, data={**entry.data, CONF_PASSWORD: password}
                    )
                    # OptionsFlowWithReload only reloads when options change.
                    if (
                        hosts == extra_hosts_from_options(entry.options)
                        and user_input[CONF_SCAN_INTERVAL]
                        == entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
                    ):
                        self.hass.config_entries.async_schedule_reload(entry.entry_id)
                return self.async_create_entry(
                    data={
                        CONF_SCAN_INTERVAL: user_input[CONF_SCAN_INTERVAL],
                        CONF_EXTRA_HOSTS: hosts,
                    }
                )

        schema = vol.Schema(
            {
                vol.Required(CONF_SCAN_INTERVAL): SCAN_VALIDATOR,
                vol.Optional(CONF_EXTRA_HOSTS): HOSTS_SELECTOR,
                vol.Optional(CONF_PASSWORD): PASSWORD_SELECTOR,
            }
        )
        suggested = {
            CONF_SCAN_INTERVAL: entry.options.get(
                CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL
            ),
            CONF_EXTRA_HOSTS: extra_hosts_from_options(entry.options),
        }
        if user_input is not None:
            suggested.update(
                {k: v for k, v in user_input.items() if k != CONF_PASSWORD}
            )
        return self.async_show_form(
            step_id="init",
            data_schema=self.add_suggested_values_to_schema(schema, suggested),
            errors=errors,
        )
