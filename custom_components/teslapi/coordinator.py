"""DataUpdateCoordinator for TeslaPi."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import aiohttp

from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import (
    API_AUTO_SYNC_STATUS,
    API_STATUS,
    CONF_HOST,
    CONF_PORT,
    CONF_SCAN_INTERVAL,
    DEFAULT_PORT,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    LOGGER,
)


class TeslaPiCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Coordinator to poll TeslaPi status."""

    def __init__(self, hass: HomeAssistant, entry) -> None:
        """Initialize the coordinator."""
        self.host: str = entry.data[CONF_HOST]
        self.port: int = entry.data.get(CONF_PORT, DEFAULT_PORT)
        self.base_url = f"http://{self.host}:{self.port}"
        self._session = async_get_clientsession(hass)

        scan_interval = entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)

        super().__init__(
            hass,
            LOGGER,
            name=f"{DOMAIN}_{self.host}",
            update_interval=timedelta(seconds=scan_interval),
        )

    async def _async_update_data(self) -> dict[str, Any]:
        """Fetch data from TeslaPi API."""
        try:
            status = await self._api_get(API_STATUS)
        except (aiohttp.ClientError, TimeoutError) as err:
            raise UpdateFailed(f"Error communicating with TeslaPi: {err}") from err

        # Also fetch auto-sync status (not included in /api/status)
        try:
            auto_sync = await self._api_get(API_AUTO_SYNC_STATUS)
            status["auto_sync"] = auto_sync
        except (aiohttp.ClientError, TimeoutError):
            status["auto_sync"] = None

        return status

    async def _api_get(self, path: str) -> dict[str, Any]:
        """Make a GET request to the TeslaPi API."""
        url = f"{self.base_url}{path}"
        async with self._session.get(
            url, timeout=aiohttp.ClientTimeout(total=15)
        ) as resp:
            if resp.status != 200:
                raise UpdateFailed(f"HTTP {resp.status} from {path}")
            return await resp.json()

    async def api_post(self, path: str, data: dict | None = None) -> dict[str, Any]:
        """Make a POST request to the TeslaPi API."""
        url = f"{self.base_url}{path}"
        async with self._session.post(
            url,
            json=data or {},
            timeout=aiohttp.ClientTimeout(total=30),
        ) as resp:
            if resp.status not in (200, 201):
                body = await resp.text()
                raise TeslaPiApiError(f"HTTP {resp.status}: {body}")
            return await resp.json()

    async def api_put(self, path: str, data: dict | None = None) -> dict[str, Any]:
        """Make a PUT request to the TeslaPi API."""
        url = f"{self.base_url}{path}"
        async with self._session.put(
            url,
            json=data or {},
            timeout=aiohttp.ClientTimeout(total=30),
        ) as resp:
            if resp.status != 200:
                body = await resp.text()
                raise TeslaPiApiError(f"HTTP {resp.status}: {body}")
            return await resp.json()

    async def api_delete(self, path: str) -> dict[str, Any]:
        """Make a DELETE request to the TeslaPi API."""
        url = f"{self.base_url}{path}"
        async with self._session.delete(
            url, timeout=aiohttp.ClientTimeout(total=15)
        ) as resp:
            if resp.status not in (200, 404):
                body = await resp.text()
                raise TeslaPiApiError(f"HTTP {resp.status}: {body}")
            return await resp.json()


class TeslaPiApiError(Exception):
    """Error from TeslaPi API."""
