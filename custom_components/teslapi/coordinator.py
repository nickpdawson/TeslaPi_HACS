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
    CONF_EXTRA_HOSTS,
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
        self.port: int = entry.data.get(CONF_PORT, DEFAULT_PORT)
        self._session = async_get_clientsession(hass)

        # Build ordered list of hosts to try
        primary_host = entry.data[CONF_HOST]
        extra_hosts_raw = entry.options.get(CONF_EXTRA_HOSTS, "")
        extra_hosts = [
            h.strip() for h in extra_hosts_raw.split(",") if h.strip()
        ] if extra_hosts_raw else []

        self._hosts: list[str] = [primary_host] + [
            h for h in extra_hosts if h != primary_host
        ]
        self._active_host_index: int = 0

        scan_interval = entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)

        super().__init__(
            hass,
            LOGGER,
            name=f"{DOMAIN}_{primary_host}",
            update_interval=timedelta(seconds=scan_interval),
        )

    @property
    def host(self) -> str:
        """Return the currently active host."""
        return self._hosts[self._active_host_index]

    @property
    def base_url(self) -> str:
        """Return the base URL for the currently active host."""
        return f"http://{self.host}:{self.port}"

    async def _try_hosts(self, request_fn) -> Any:
        """Try a request against each host in order, starting with the active one.

        On success, update the active host index. On total failure, raise the
        last exception encountered.
        """
        last_error: Exception | None = None
        # Try active host first, then cycle through others
        order = list(range(len(self._hosts)))
        order.sort(key=lambda i: 0 if i == self._active_host_index else 1)

        for idx in order:
            host = self._hosts[idx]
            url_base = f"http://{host}:{self.port}"
            try:
                result = await request_fn(url_base)
                # Success — update active host if it changed
                if idx != self._active_host_index:
                    LOGGER.info(
                        "TeslaPi: switched from %s to %s",
                        self._hosts[self._active_host_index],
                        host,
                    )
                    self._active_host_index = idx
                return result
            except (aiohttp.ClientError, TimeoutError) as err:
                LOGGER.debug(
                    "TeslaPi: host %s unreachable: %s", host, err
                )
                last_error = err
                continue

        raise last_error  # type: ignore[misc]

    async def _async_update_data(self) -> dict[str, Any]:
        """Fetch data from TeslaPi API."""
        try:
            status = await self._try_hosts(
                lambda base: self._raw_get(base, API_STATUS)
            )
        except (aiohttp.ClientError, TimeoutError) as err:
            raise UpdateFailed(f"Error communicating with TeslaPi: {err}") from err

        # Also fetch auto-sync status (not included in /api/status)
        try:
            auto_sync = await self._raw_get(self.base_url, API_AUTO_SYNC_STATUS)
            status["auto_sync"] = auto_sync
        except (aiohttp.ClientError, TimeoutError):
            status["auto_sync"] = None

        return status

    async def _raw_get(self, base_url: str, path: str) -> dict[str, Any]:
        """Make a GET request to a specific base URL."""
        url = f"{base_url}{path}"
        async with self._session.get(
            url, timeout=aiohttp.ClientTimeout(total=15)
        ) as resp:
            if resp.status != 200:
                raise UpdateFailed(f"HTTP {resp.status} from {path}")
            return await resp.json()

    async def _api_get(self, path: str) -> dict[str, Any]:
        """Make a GET request to the active TeslaPi host."""
        return await self._raw_get(self.base_url, path)

    async def api_post(self, path: str, data: dict | None = None) -> dict[str, Any]:
        """Make a POST request to the TeslaPi API (with host fallback)."""

        async def _do_post(base_url: str) -> dict[str, Any]:
            url = f"{base_url}{path}"
            async with self._session.post(
                url,
                json=data or {},
                timeout=aiohttp.ClientTimeout(total=30),
            ) as resp:
                if resp.status not in (200, 201):
                    body = await resp.text()
                    raise TeslaPiApiError(f"HTTP {resp.status}: {body}")
                return await resp.json()

        try:
            return await self._try_hosts(_do_post)
        except (aiohttp.ClientError, TimeoutError) as err:
            raise TeslaPiApiError(f"Cannot reach TeslaPi: {err}") from err

    async def api_put(self, path: str, data: dict | None = None) -> dict[str, Any]:
        """Make a PUT request to the TeslaPi API (with host fallback)."""

        async def _do_put(base_url: str) -> dict[str, Any]:
            url = f"{base_url}{path}"
            async with self._session.put(
                url,
                json=data or {},
                timeout=aiohttp.ClientTimeout(total=30),
            ) as resp:
                if resp.status != 200:
                    body = await resp.text()
                    raise TeslaPiApiError(f"HTTP {resp.status}: {body}")
                return await resp.json()

        try:
            return await self._try_hosts(_do_put)
        except (aiohttp.ClientError, TimeoutError) as err:
            raise TeslaPiApiError(f"Cannot reach TeslaPi: {err}") from err

    async def api_delete(self, path: str) -> dict[str, Any]:
        """Make a DELETE request to the TeslaPi API (with host fallback)."""

        async def _do_delete(base_url: str) -> dict[str, Any]:
            url = f"{base_url}{path}"
            async with self._session.delete(
                url, timeout=aiohttp.ClientTimeout(total=15)
            ) as resp:
                if resp.status not in (200, 404):
                    body = await resp.text()
                    raise TeslaPiApiError(f"HTTP {resp.status}: {body}")
                return await resp.json()

        try:
            return await self._try_hosts(_do_delete)
        except (aiohttp.ClientError, TimeoutError) as err:
            raise TeslaPiApiError(f"Cannot reach TeslaPi: {err}") from err


class TeslaPiApiError(Exception):
    """Error from TeslaPi API."""
