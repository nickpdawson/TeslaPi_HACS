"""DataUpdateCoordinator for TeslaPi."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import aiohttp

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_PASSWORD
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError
from homeassistant.helpers.aiohttp_client import async_create_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import (
    TeslaPiApiError,
    TeslaPiAuthError,
    TeslaPiClient,
    TeslaPiConnectionError,
    TeslaPiError,
)
from .const import (
    API_AUTO_SYNC_STATUS,
    API_STATUS,
    CONF_EXTRA_HOSTS,
    CONF_HOST,
    CONF_PORT,
    CONF_SCAN_INTERVAL,
    DEFAULT_PORT,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    LOGGER,
)

__all__ = [
    "TeslaPiApiError",
    "TeslaPiAuthError",
    "TeslaPiConfigEntry",
    "TeslaPiConnectionError",
    "TeslaPiCoordinator",
    "TeslaPiError",
]

type TeslaPiConfigEntry = ConfigEntry[TeslaPiCoordinator]


def extra_hosts_from_options(options: dict[str, Any]) -> list[str]:
    """Read fallback hosts. Version 1 entries stored a comma-separated string."""
    raw = options.get(CONF_EXTRA_HOSTS) or []
    if isinstance(raw, str):
        raw = raw.split(",")
    return [h.strip() for h in raw if h and h.strip()]


class TeslaPiCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Coordinator to poll TeslaPi status."""

    config_entry: TeslaPiConfigEntry

    def __init__(self, hass: HomeAssistant, entry: TeslaPiConfigEntry) -> None:
        """Initialize the coordinator."""
        primary_host = entry.data[CONF_HOST]
        hosts = [primary_host] + [
            h for h in extra_hosts_from_options(entry.options) if h != primary_host
        ]
        # A dedicated session keeps the TeslaPi cookie out of the shared
        # session other integrations use.
        session = async_create_clientsession(
            hass, cookie_jar=aiohttp.DummyCookieJar()
        )
        self.client = TeslaPiClient(
            session,
            hosts,
            entry.data.get(CONF_PORT, DEFAULT_PORT),
            entry.data.get(CONF_PASSWORD),
        )
        self.server_version: str | None = None

        scan_interval = entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)

        super().__init__(
            hass,
            LOGGER,
            config_entry=entry,
            name=f"{DOMAIN}_{primary_host}",
            update_interval=timedelta(seconds=scan_interval),
        )

    @property
    def host(self) -> str:
        """Return the currently active host."""
        return self.client.host

    @property
    def port(self) -> int:
        """Return the HTTP port."""
        return self.client.port

    @property
    def base_url(self) -> str:
        """Return the base URL for the currently active host."""
        return self.client.base_url

    async def _async_update_data(self) -> dict[str, Any]:
        """Fetch data from TeslaPi API."""
        try:
            status = await self.client.get(API_STATUS)
        except TeslaPiAuthError as err:
            raise ConfigEntryAuthFailed(
                translation_domain=DOMAIN, translation_key="auth_failed"
            ) from err
        except TeslaPiError as err:
            raise UpdateFailed(
                translation_domain=DOMAIN,
                translation_key="update_failed",
                translation_placeholders={"error": str(err)},
            ) from err

        # Optional endpoints: older servers lack them, and they must not fail
        # the whole poll.
        try:
            status["auto_sync"] = await self.client.get(API_AUTO_SYNC_STATUS)
        except TeslaPiAuthError as err:
            raise ConfigEntryAuthFailed(
                translation_domain=DOMAIN, translation_key="auth_failed"
            ) from err
        except TeslaPiError as err:
            LOGGER.debug("Auto-sync status unavailable: %s", err)
            status["auto_sync"] = None

        if self.server_version is None:
            try:
                health = await self.client.health()
            except TeslaPiError as err:
                LOGGER.debug("Health check failed: %s", err)
            else:
                self.server_version = health.get("version") or None

        return status

    async def _call(self, coro) -> Any:
        """Run a write call and turn client errors into HA errors."""
        try:
            return await coro
        except TeslaPiAuthError as err:
            self.config_entry.async_start_reauth(self.hass)
            raise HomeAssistantError(
                translation_domain=DOMAIN, translation_key="auth_failed"
            ) from err
        except TeslaPiError as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="api_error",
                translation_placeholders={"error": str(err)},
            ) from err

    async def api_post(self, path: str, data: dict | None = None) -> Any:
        """POST to the active host. Raises HomeAssistantError on failure."""
        return await self._call(self.client.post(path, data))

    async def api_put(self, path: str, data: dict | None = None) -> Any:
        """PUT to the active host. Raises HomeAssistantError on failure."""
        return await self._call(self.client.put(path, data))

    async def api_delete(self, path: str) -> Any:
        """DELETE on the active host. Raises HomeAssistantError on failure."""
        return await self._call(self.client.delete(path))
