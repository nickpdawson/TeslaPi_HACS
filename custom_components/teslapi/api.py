"""HTTP client for the TeslaPi server API."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from typing import Any

import aiohttp

from .const import API_AUTH_LOGIN, API_HEALTH, LOGGER, SESSION_COOKIE

REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=15)
WRITE_TIMEOUT = aiohttp.ClientTimeout(total=30)
# Clips can be large and the reader may pause; only bound the connect and
# the gap between reads.
STREAM_TIMEOUT = aiohttp.ClientTimeout(total=None, sock_connect=10, sock_read=60)


class TeslaPiError(Exception):
    """Base error for the TeslaPi client."""


class TeslaPiConnectionError(TeslaPiError):
    """No host answered."""


class TeslaPiAuthError(TeslaPiError):
    """The server wants a password, or rejected the one we have."""


class TeslaPiApiError(TeslaPiError):
    """The server answered with an error status."""

    def __init__(self, status: int, message: str) -> None:
        """Keep the HTTP status for callers that branch on it."""
        super().__init__(f"HTTP {status}: {message}")
        self.status = status


class TeslaPiClient:
    """Talk to one TeslaPi device that may be reachable at several addresses.

    The server's auth gate is off until the owner sets a password on the Pi.
    Once it is on, every /api call except /api/health and /api/auth/* needs
    the session cookie that POST /api/auth/login returns. We hold that token
    ourselves and send it as a header: aiohttp's default cookie jar drops
    cookies set by bare IP addresses, and the same token has to follow us to
    a fallback host.
    """

    def __init__(
        self,
        session: aiohttp.ClientSession,
        hosts: list[str],
        port: int,
        password: str | None = None,
    ) -> None:
        """Set up the client. hosts[0] is the primary host."""
        self._session = session
        self._hosts = hosts
        self._port = port
        self._password = password or None
        self._token: str | None = None
        self._active = 0
        self._login_lock = asyncio.Lock()

    @property
    def hosts(self) -> list[str]:
        """All configured hosts, primary first."""
        return list(self._hosts)

    @property
    def host(self) -> str:
        """The host that last answered."""
        return self._hosts[self._active]

    @property
    def port(self) -> int:
        """The HTTP port."""
        return self._port

    @property
    def base_url(self) -> str:
        """Base URL of the active host."""
        return self._url_for(self.host)

    def _url_for(self, host: str) -> str:
        if ":" in host and not host.startswith("["):
            host = f"[{host}]"
        return f"http://{host}:{self._port}"

    def _headers(self, extra: Mapping[str, str] | None = None) -> dict[str, str]:
        headers = dict(extra or {})
        if self._token:
            headers["Cookie"] = f"{SESSION_COOKIE}={self._token}"
        return headers

    async def login(self) -> None:
        """Log in to the active host and keep the session token."""
        await self._login(self.base_url)

    async def _login(self, base_url: str) -> None:
        if not self._password:
            raise TeslaPiAuthError("The TeslaPi requires a password")
        async with self._login_lock:
            try:
                async with self._session.post(
                    f"{base_url}{API_AUTH_LOGIN}",
                    json={"password": self._password},
                    timeout=REQUEST_TIMEOUT,
                ) as resp:
                    if resp.status == 401:
                        self._token = None
                        raise TeslaPiAuthError("The TeslaPi rejected the password")
                    if resp.status == 400:
                        # The Pi has no password set, so no cookie is needed.
                        self._token = None
                        return
                    if resp.status != 200:
                        raise TeslaPiApiError(resp.status, "login failed")
                    morsel = resp.cookies.get(SESSION_COOKIE)
                    if morsel is None or not morsel.value:
                        raise TeslaPiApiError(resp.status, "login set no session cookie")
                    self._token = morsel.value
            except (aiohttp.ClientError, TimeoutError) as err:
                raise TeslaPiConnectionError(str(err)) from err
        LOGGER.debug("Logged in to TeslaPi at %s", base_url)

    async def _request(
        self,
        base_url: str,
        method: str,
        path: str,
        *,
        json: Any = None,
        params: Mapping[str, Any] | None = None,
        ok: tuple[int, ...] = (200,),
        timeout: aiohttp.ClientTimeout = REQUEST_TIMEOUT,
    ) -> Any:
        """Send one request, logging in again once if the session expired."""
        if self._password and not self._token and path != API_HEALTH:
            await self._login(base_url)
        for attempt in range(2):
            async with self._session.request(
                method,
                f"{base_url}{path}",
                json=json,
                params=params,
                headers=self._headers(),
                timeout=timeout,
            ) as resp:
                if resp.status == 401:
                    if attempt == 0 and self._password:
                        await self._login(base_url)
                        continue
                    raise TeslaPiAuthError("The TeslaPi requires a password")
                if resp.status not in ok:
                    text = await resp.text()
                    raise TeslaPiApiError(resp.status, text[:200])
                if resp.status == 204:
                    return {}
                return await resp.json(content_type=None)
        raise TeslaPiAuthError("The TeslaPi requires a password")  # pragma: no cover

    async def get(
        self, path: str, params: Mapping[str, Any] | None = None
    ) -> Any:
        """GET with host fallback. Safe to repeat, so other hosts are tried."""
        order = [self._active] + [
            i for i in range(len(self._hosts)) if i != self._active
        ]
        last_error: Exception | None = None
        for idx in order:
            host = self._hosts[idx]
            try:
                result = await self._request(
                    self._url_for(host), "GET", path, params=params
                )
            except (aiohttp.ClientError, TimeoutError, TeslaPiConnectionError) as err:
                LOGGER.debug("TeslaPi host %s unreachable: %s", host, err)
                last_error = err
                continue
            if idx != self._active:
                LOGGER.info(
                    "TeslaPi switched from %s to %s", self._hosts[self._active], host
                )
                self._active = idx
            return result
        raise TeslaPiConnectionError(str(last_error)) from last_error

    async def _write(
        self, method: str, path: str, data: Any, ok: tuple[int, ...]
    ) -> Any:
        # Writes go to the active host only. A timeout does not prove the
        # first host ignored the request, so repeating it elsewhere could
        # start a second archive or sync.
        try:
            return await self._request(
                self.base_url, method, path, json=data, ok=ok, timeout=WRITE_TIMEOUT
            )
        except (aiohttp.ClientError, TimeoutError) as err:
            raise TeslaPiConnectionError(str(err)) from err

    async def post(self, path: str, data: Any = None) -> Any:
        """POST to the active host."""
        return await self._write("POST", path, data or {}, (200, 201))

    async def put(self, path: str, data: Any = None) -> Any:
        """PUT to the active host."""
        return await self._write("PUT", path, data or {}, (200,))

    async def delete(self, path: str) -> Any:
        """DELETE on the active host. 404 means nothing was running."""
        return await self._write("DELETE", path, None, (200, 204, 404))

    async def health(self) -> dict[str, Any]:
        """Read /api/health from the active host. It never needs a password."""
        try:
            return await self._request(self.base_url, "GET", API_HEALTH)
        except (aiohttp.ClientError, TimeoutError) as err:
            raise TeslaPiConnectionError(str(err)) from err

    async def open_stream(
        self, path: str, headers: Mapping[str, str] | None = None
    ) -> aiohttp.ClientResponse:
        """Open a GET for streaming from the active host.

        The caller must release the response. Status codes other than 401
        are returned as-is so a proxy can pass 206 and 416 through.
        """
        if self._password and not self._token:
            await self._login(self.base_url)
        for attempt in range(2):
            try:
                resp = await self._session.get(
                    f"{self.base_url}{path}",
                    headers=self._headers(headers),
                    timeout=STREAM_TIMEOUT,
                )
            except (aiohttp.ClientError, TimeoutError) as err:
                raise TeslaPiConnectionError(str(err)) from err
            if resp.status != 401:
                return resp
            resp.release()
            if attempt == 0 and self._password:
                await self._login(self.base_url)
                continue
            break
        raise TeslaPiAuthError("The TeslaPi requires a password")
