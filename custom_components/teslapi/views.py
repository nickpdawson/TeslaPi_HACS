"""Authenticated proxy for TeslaPi dashcam clips."""

from __future__ import annotations

from http import HTTPStatus
import re

from aiohttp import web

from homeassistant.components.http import HomeAssistantView
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant

from .api import TeslaPiAuthError, TeslaPiError
from .const import API_DASHCAM_VIDEO, DOMAIN, LOGGER, SUPPORTED_EVENT_TYPES

CLIP_URL = "/api/teslapi/clip/{entry_id}/{event_type}/{event_dir}/{clip_file}"

# Tesla names event folders and clips like 2026-04-10_14-19-54 and
# 2026-04-10_14-16-30-front.mp4. Allow that alphabet only, so nothing that
# reaches the Pi can climb out of the clip folder.
EVENT_DIR_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
CLIP_FILE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}\.mp4$")

_PASS_HEADERS = (
    "Content-Type",
    "Content-Length",
    "Content-Range",
    "Accept-Ranges",
    "Last-Modified",
    "ETag",
)
CHUNK_SIZE = 64 * 1024


def clip_path_ok(event_type: str, event_dir: str, clip_file: str) -> bool:
    """True if the parts name a clip we are willing to fetch."""
    return (
        event_type in SUPPORTED_EVENT_TYPES
        and bool(EVENT_DIR_RE.match(event_dir))
        and bool(CLIP_FILE_RE.match(clip_file))
    )


def clip_url(entry_id: str, event_type: str, event_dir: str, clip_file: str) -> str:
    """HA-relative URL of a clip. Media source signs it for the player."""
    return CLIP_URL.format(
        entry_id=entry_id,
        event_type=event_type,
        event_dir=event_dir,
        clip_file=clip_file,
    )


class TeslaPiClipView(HomeAssistantView):
    """Stream a clip from the Pi to a logged-in HA user.

    Players never talk to the Pi directly, so the Pi's address and session
    cookie stay inside HA, and HA's own auth guards the clips.
    """

    url = CLIP_URL
    name = "api:teslapi:clip"
    requires_auth = True

    def __init__(self, hass: HomeAssistant) -> None:
        """Keep a reference to hass."""
        self.hass = hass

    async def get(
        self,
        request: web.Request,
        entry_id: str,
        event_type: str,
        event_dir: str,
        clip_file: str,
    ) -> web.StreamResponse:
        """Proxy one clip, passing Range through for seeking."""
        if not clip_path_ok(event_type, event_dir, clip_file):
            return web.Response(status=HTTPStatus.NOT_FOUND)

        entry = self.hass.config_entries.async_get_entry(entry_id)
        if (
            entry is None
            or entry.domain != DOMAIN
            or entry.state is not ConfigEntryState.LOADED
        ):
            return web.Response(status=HTTPStatus.NOT_FOUND)

        headers: dict[str, str] = {}
        if range_header := request.headers.get("Range"):
            headers["Range"] = range_header

        path = f"{API_DASHCAM_VIDEO}/{event_type}/{event_dir}/{clip_file}"
        try:
            upstream = await entry.runtime_data.client.open_stream(path, headers)
        except TeslaPiAuthError:
            entry.async_start_reauth(self.hass)
            return web.Response(status=HTTPStatus.BAD_GATEWAY)
        except TeslaPiError as err:
            LOGGER.debug("Clip fetch failed: %s", err)
            return web.Response(status=HTTPStatus.BAD_GATEWAY)

        try:
            if upstream.status not in (HTTPStatus.OK, HTTPStatus.PARTIAL_CONTENT):
                status = (
                    upstream.status
                    if upstream.status
                    in (HTTPStatus.NOT_FOUND, HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE)
                    else HTTPStatus.BAD_GATEWAY
                )
                return web.Response(status=status)

            response = web.StreamResponse(status=upstream.status)
            for name in _PASS_HEADERS:
                if name in upstream.headers:
                    response.headers[name] = upstream.headers[name]
            response.headers.setdefault("Content-Type", "video/mp4")
            await response.prepare(request)
            async for chunk in upstream.content.iter_chunked(CHUNK_SIZE):
                await response.write(chunk)
            await response.write_eof()
            return response
        finally:
            upstream.release()
