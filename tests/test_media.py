"""Clip proxy, media source and diagnostics tests."""

from __future__ import annotations

from http import HTTPStatus

import pytest

from homeassistant.components.media_source import (
    URI_SCHEME,
    async_browse_media,
    async_resolve_media,
)
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.components.diagnostics import (
    get_diagnostics_for_config_entry,
)
from pytest_homeassistant_custom_component.test_util.aiohttp import (
    AiohttpClientMocker,
)
from pytest_homeassistant_custom_component.typing import ClientSessionGenerator

from custom_components.teslapi.const import DOMAIN

from .conftest import HOST, TOKEN, make_entry, mock_pi

EVENT = "2026-04-10_14-19-54"
CLIP = "2026-04-10_14-16-30-front.mp4"


async def _setup(hass: HomeAssistant, aioclient_mock, password=None):
    mock_pi(aioclient_mock)
    if password:
        aioclient_mock.post(
            f"http://{HOST}:80/api/auth/login",
            json={"ok": True},
            cookies={"teslapi_session": TOKEN},
        )
    assert await async_setup_component(hass, "media_source", {})
    entry = make_entry(password=password)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


@pytest.mark.parametrize(
    "path",
    [
        f"BadClips/{EVENT}/{CLIP}",
        f"SavedClips/{EVENT}/passwd",
        f"SavedClips/{EVENT}/event.json",
        f"SavedClips/.hidden/{CLIP}",
        f"SavedClips/{EVENT}/..%2F..%2Fetc.mp4",
        f"SavedClips/%2E%2E/{CLIP}",
    ],
)
async def test_clip_view_rejects_bad_paths(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    hass_client: ClientSessionGenerator,
    path: str,
) -> None:
    """Anything outside the allowlist is refused and never reaches the Pi.

    HA's own security filter answers 400 for some encoded traversal before
    the view runs; the view answers 404 for the rest.
    """
    entry = await _setup(hass, aioclient_mock)
    client = await hass_client()
    before = len(aioclient_mock.mock_calls)
    resp = await client.get(f"/api/teslapi/clip/{entry.entry_id}/{path}")
    assert resp.status in (HTTPStatus.BAD_REQUEST, HTTPStatus.NOT_FOUND)
    assert len(aioclient_mock.mock_calls) == before


async def test_clip_view_requires_auth(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    hass_client_no_auth: ClientSessionGenerator,
) -> None:
    """Without an HA login the proxy refuses."""
    entry = await _setup(hass, aioclient_mock)
    client = await hass_client_no_auth()
    resp = await client.get(f"/api/teslapi/clip/{entry.entry_id}/SavedClips/{EVENT}/{CLIP}")
    assert resp.status == HTTPStatus.UNAUTHORIZED


async def test_clip_view_streams_with_range_and_cookie(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    hass_client: ClientSessionGenerator,
) -> None:
    """A good request streams from the Pi with Range and the session cookie."""
    entry = await _setup(hass, aioclient_mock, password="pw")
    aioclient_mock.get(
        f"http://{HOST}:80/api/dashcam/video/SavedClips/{EVENT}/{CLIP}",
        status=HTTPStatus.PARTIAL_CONTENT,
        content=b"abcd",
        headers={
            "Content-Type": "video/mp4",
            "Content-Range": "bytes 0-3/100",
            "Accept-Ranges": "bytes",
        },
    )
    client = await hass_client()
    resp = await client.get(
        f"/api/teslapi/clip/{entry.entry_id}/SavedClips/{EVENT}/{CLIP}",
        headers={"Range": "bytes=0-3"},
    )
    assert resp.status == HTTPStatus.PARTIAL_CONTENT
    assert await resp.read() == b"abcd"
    assert resp.headers["Content-Range"] == "bytes 0-3/100"

    call = [c for c in aioclient_mock.mock_calls if "/api/dashcam/video/" in c[1].path][-1]
    assert call[3]["Range"] == "bytes=0-3"
    assert call[3]["Cookie"] == f"teslapi_session={TOKEN}"


async def test_resolve_returns_proxy_url(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """Resolved clips point at HA, not at the Pi."""
    entry = await _setup(hass, aioclient_mock)
    media = await async_resolve_media(
        hass, f"{URI_SCHEME}{DOMAIN}/{entry.entry_id}/SavedClips/{EVENT}/{CLIP}", None
    )
    assert media.url == f"/api/teslapi/clip/{entry.entry_id}/SavedClips/{EVENT}/{CLIP}"
    assert HOST not in media.url


async def test_browse_paginates(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """The browser follows hasMore and shows HH:MM."""
    entry = await _setup(hass, aioclient_mock)
    first = [
        {"event_dir": f"2026-04-1{i}_14-19-54", "clip_file": CLIP, "size_bytes": 1048576}
        for i in range(2)
    ]
    second = [
        {"event_dir": "2026-04-12_08-05-00", "clip_file": CLIP, "size_bytes": 0},
        {"event_dir": "2026-04-12_08-05-00", "clip_file": "event.json", "size_bytes": 0},
    ]
    aioclient_mock.get(
        f"http://{HOST}:80/api/archive/clips",
        params={"event_type": "SavedClips", "offset": 0},
        json={"clips": first, "hasMore": True},
    )
    aioclient_mock.get(
        f"http://{HOST}:80/api/archive/clips",
        params={"event_type": "SavedClips", "offset": 2},
        json={"clips": second, "hasMore": False},
    )
    result = await async_browse_media(
        hass, f"{URI_SCHEME}{DOMAIN}/{entry.entry_id}/SavedClips"
    )
    titles = [child.title for child in result.children]
    assert titles == [
        "2026-04-12 08:05 (1 clips, 0 MB)",
        "2026-04-11 14:19 (1 clips, 1 MB)",
        "2026-04-10 14:19 (1 clips, 1 MB)",
    ]


async def test_diagnostics_redacts(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    hass_client: ClientSessionGenerator,
) -> None:
    """Diagnostics hide the password, addresses and SSID."""
    entry = await _setup(hass, aioclient_mock, password="pw")
    diag = await get_diagnostics_for_config_entry(hass, hass_client, entry)
    text = str(diag)
    for secret in ("pw'", HOST, "HomeWiFi", TOKEN):
        assert secret not in text
    assert diag["client"]["password_set"] is True
