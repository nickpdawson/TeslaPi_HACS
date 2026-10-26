"""Service targeting tests."""

from __future__ import annotations

import pytest

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import device_registry as dr
from pytest_homeassistant_custom_component.test_util.aiohttp import (
    AiohttpClientMocker,
)

from custom_components.teslapi.const import DOMAIN

from .conftest import HOST, HOST2, make_entry, mock_pi


def _archive_posts(aioclient_mock: AiohttpClientMocker) -> list[str]:
    return [
        c[1].host
        for c in aioclient_mock.mock_calls
        if c[0].lower() == "post" and c[1].path == "/api/archive/start"
    ]


async def _setup_two(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker):
    entries = []
    for host, name in ((HOST, "car-one"), (HOST2, "car-two")):
        mock_pi(aioclient_mock, host=host, hostname=name)
        aioclient_mock.post(f"http://{host}:80/api/archive/start", json={"job_id": 1})
        entry = make_entry(host=host)
        entry.add_to_hass(hass)
        assert await hass.config_entries.async_setup(entry.entry_id)
        entries.append(entry)
    await hass.async_block_till_done()
    return entries


async def test_two_entries_need_a_target(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """With two Pis, a call without a target is refused."""
    await _setup_two(hass, aioclient_mock)
    with pytest.raises(ServiceValidationError) as err:
        await hass.services.async_call(DOMAIN, "start_archive", {}, blocking=True)
    assert err.value.translation_key == "entry_required"
    assert _archive_posts(aioclient_mock) == []


async def test_target_by_config_entry(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """config_entry_id picks the right Pi."""
    _, second = await _setup_two(hass, aioclient_mock)
    await hass.services.async_call(
        DOMAIN, "start_archive", {"config_entry_id": second.entry_id}, blocking=True
    )
    assert _archive_posts(aioclient_mock) == [HOST2]


async def test_target_by_device(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """device_id picks the right Pi."""
    first, _ = await _setup_two(hass, aioclient_mock)
    device = dr.async_entries_for_config_entry(dr.async_get(hass), first.entry_id)[0]
    await hass.services.async_call(
        DOMAIN, "start_archive", {"device_id": device.id}, blocking=True
    )
    assert _archive_posts(aioclient_mock) == [HOST]


async def test_unknown_target(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """An unknown entry or device is a validation error."""
    await _setup_two(hass, aioclient_mock)
    with pytest.raises(ServiceValidationError) as err:
        await hass.services.async_call(
            DOMAIN, "start_archive", {"config_entry_id": "nope"}, blocking=True
        )
    assert err.value.translation_key == "entry_not_found"
    with pytest.raises(ServiceValidationError) as err:
        await hass.services.async_call(
            DOMAIN, "start_archive", {"device_id": "nope"}, blocking=True
        )
    assert err.value.translation_key == "device_not_found"


async def test_single_entry_is_default(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """With one Pi the target is optional, and music sync sends a full body."""
    mock_pi(aioclient_mock)
    aioclient_mock.post(f"http://{HOST}:80/api/music/sync", json={"job_id": 2})
    entry = make_entry()
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    await hass.services.async_call(
        DOMAIN,
        "start_music_sync",
        {"mode": "selected", "paths": ["/Dire Straits"]},
        blocking=True,
    )
    body = [c[2] for c in aioclient_mock.mock_calls if c[1].path == "/api/music/sync"]
    assert body == [
        {"mode": "selected", "count": 20, "type": "artist", "paths": ["/Dire Straits"]}
    ]


async def test_no_entries_loaded(hass: HomeAssistant) -> None:
    """Services registered at startup explain that nothing is loaded."""
    from homeassistant.setup import async_setup_component

    assert await async_setup_component(hass, DOMAIN, {})
    with pytest.raises(ServiceValidationError) as err:
        await hass.services.async_call(DOMAIN, "cancel_archive", {}, blocking=True)
    assert err.value.translation_key == "no_entries"
