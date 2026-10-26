"""Setup, unload, migration and auth tests."""

from __future__ import annotations

from http import HTTPStatus

import aiohttp
import pytest

from homeassistant.config_entries import SOURCE_REAUTH, ConfigEntryState
from homeassistant.const import STATE_OFF, STATE_ON
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr, entity_registry as er
from pytest_homeassistant_custom_component.test_util.aiohttp import (
    AiohttpClientMocker,
    AiohttpClientMockResponse,
)

from custom_components.teslapi.const import DOMAIN

from .conftest import AUTO_SYNC, HOST, HOST2, TOKEN, make_entry, mock_pi, status_payload


async def _setup(hass: HomeAssistant, entry) -> None:
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


async def test_setup_unload_reload(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """Entry loads, unloads and reloads cleanly."""
    mock_pi(aioclient_mock)
    entry = make_entry()
    await _setup(hass, entry)
    assert entry.state is ConfigEntryState.LOADED
    assert hass.states.get("sensor.teslapi_status").state == "idle"
    assert hass.states.get("sensor.teslapi_active_host").state == HOST
    assert hass.services.has_service(DOMAIN, "start_archive")

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.NOT_LOADED
    # Services stay registered; they report no loaded entry instead.
    assert hass.services.has_service(DOMAIN, "start_archive")

    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED


async def test_device_info_fallbacks(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """Empty hostname uses the entry title; version comes from /api/health."""
    mock_pi(aioclient_mock, hostname="")
    entry = make_entry()
    await _setup(hass, entry)
    devices = dr.async_entries_for_config_entry(dr.async_get(hass), entry.entry_id)
    assert len(devices) == 1
    device = devices[0]
    assert device.identifiers == {(DOMAIN, entry.entry_id)}
    assert device.name == entry.title
    assert device.sw_version == "0.4.1"


async def test_auto_sync_missing_does_not_fail_poll(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """Older servers without /api/auto-sync/status still load."""
    aioclient_mock.get(f"http://{HOST}:80/api/health", json={"version": "0.1"})
    aioclient_mock.get(f"http://{HOST}:80/api/status", json=status_payload())
    aioclient_mock.get(
        f"http://{HOST}:80/api/auto-sync/status", status=HTTPStatus.NOT_FOUND
    )
    entry = make_entry()
    await _setup(hass, entry)
    assert entry.state is ConfigEntryState.LOADED
    assert hass.states.get("switch.teslapi_auto_sync").state == STATE_OFF


async def test_online_sensor_stays_available(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """Online reports off, not unavailable, when the Pi drops."""
    mock_pi(aioclient_mock)
    entry = make_entry()
    await _setup(hass, entry)
    assert hass.states.get("binary_sensor.teslapi_online").state == STATE_ON

    aioclient_mock.clear_requests()
    aioclient_mock.get(
        f"http://{HOST}:80/api/status", exc=aiohttp.ClientConnectionError()
    )
    await entry.runtime_data.async_refresh()
    await hass.async_block_till_done()
    assert hass.states.get("binary_sensor.teslapi_online").state == STATE_OFF
    assert hass.states.get("sensor.teslapi_status").state == "unavailable"


async def test_relogin_on_401(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """An expired session gets one fresh login, then the request succeeds."""
    responses = iter(
        [
            AiohttpClientMockResponse("get", None, status=HTTPStatus.UNAUTHORIZED),
            AiohttpClientMockResponse("get", None, json=status_payload()),
        ]
    )

    async def status_side_effect(method, url, data):
        return next(responses)

    aioclient_mock.get(f"http://{HOST}:80/api/health", json={"version": "0.4.1"})
    aioclient_mock.get(f"http://{HOST}:80/api/status", side_effect=status_side_effect)
    aioclient_mock.get(f"http://{HOST}:80/api/auto-sync/status", json=AUTO_SYNC)
    aioclient_mock.post(
        f"http://{HOST}:80/api/auth/login",
        json={"ok": True},
        cookies={"teslapi_session": TOKEN},
    )
    entry = make_entry(password="hunter2")
    await _setup(hass, entry)
    assert entry.state is ConfigEntryState.LOADED

    logins = [c for c in aioclient_mock.mock_calls if c[1].path == "/api/auth/login"]
    # One login up front, one after the 401.
    assert len(logins) == 2
    assert logins[0][2] == {"password": "hunter2"}
    status_calls = [c for c in aioclient_mock.mock_calls if c[1].path == "/api/status"]
    assert status_calls[-1][3]["Cookie"] == f"teslapi_session={TOKEN}"


async def test_wrong_password_starts_reauth(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """A rejected password fails setup and opens a reauth flow."""
    mock_pi(aioclient_mock, status=HTTPStatus.UNAUTHORIZED)
    aioclient_mock.post(
        f"http://{HOST}:80/api/auth/login", status=HTTPStatus.UNAUTHORIZED
    )
    entry = make_entry(password="old")
    await _setup(hass, entry)
    assert entry.state is ConfigEntryState.SETUP_ERROR
    flows = hass.config_entries.flow.async_progress_by_handler(DOMAIN)
    assert [f["context"]["source"] for f in flows] == [SOURCE_REAUTH]


async def test_password_never_logged(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Neither the password nor the session token reach the log."""
    caplog.set_level("DEBUG")
    mock_pi(aioclient_mock)
    aioclient_mock.post(
        f"http://{HOST}:80/api/auth/login",
        json={"ok": True},
        cookies={"teslapi_session": TOKEN},
    )
    entry = make_entry(password="s3cret-pw")
    await _setup(hass, entry)
    assert "s3cret-pw" not in caplog.text
    assert TOKEN not in caplog.text


async def test_writes_not_retried_on_fallback(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """GETs fall back to the next host; POSTs go to the active host only."""
    aioclient_mock.get(
        f"http://{HOST}:80/api/status", exc=aiohttp.ClientConnectionError()
    )
    mock_pi(aioclient_mock, host=HOST2)
    aioclient_mock.post(
        f"http://{HOST2}:80/api/archive/start", exc=aiohttp.ClientConnectionError()
    )
    aioclient_mock.post(f"http://{HOST}:80/api/archive/start", json={"job_id": 1})
    entry = make_entry(options={"scan_interval": 30, "extra_hosts": [HOST2]})
    await _setup(hass, entry)
    assert entry.state is ConfigEntryState.LOADED
    assert hass.states.get("sensor.teslapi_active_host").state == HOST2

    with pytest.raises(HomeAssistantError):
        await hass.services.async_call(
            "button", "press", {"entity_id": "button.teslapi_archive_now"}, blocking=True
        )
    posts = [c for c in aioclient_mock.mock_calls if c[0].lower() == "post"]
    assert [c[1].host for c in posts] == [HOST2]


async def test_migrate_v1_registry(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Host-based IDs move to entry-ID IDs without changing entity IDs."""
    update_kwargs: list[dict] = []
    real_update = dr.DeviceRegistry.async_update_device

    def spy_update(self, device_id, **kwargs):
        update_kwargs.append(kwargs)
        return real_update(self, device_id, **kwargs)

    monkeypatch.setattr(dr.DeviceRegistry, "async_update_device", spy_update)
    mock_pi(aioclient_mock)
    entry = make_entry(
        version=1, options={"scan_interval": 30, "extra_hosts": " teslapi.local , "}
    )
    entry.add_to_hass(hass)

    dev_reg = dr.async_get(hass)
    ent_reg = er.async_get(hass)
    device = dev_reg.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, HOST)},
        name="teslapi",
    )
    # A stray device from a fallback host answering first under version 1.
    stray = dev_reg.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, "teslapi.local")},
        name="teslapi",
    )
    cpu = ent_reg.async_get_or_create(
        "sensor",
        DOMAIN,
        f"{HOST}_cpu_temp",
        config_entry=entry,
        device_id=device.id,
        suggested_object_id="my_pi_cpu",
    )
    gadget = ent_reg.async_get_or_create(
        "switch", DOMAIN, f"{HOST}_gadget", config_entry=entry, device_id=device.id
    )
    dup_cpu = ent_reg.async_get_or_create(
        "sensor",
        DOMAIN,
        "teslapi.local_cpu_temp",
        config_entry=entry,
        device_id=stray.id,
    )
    removed = [
        ent_reg.async_get_or_create(
            "binary_sensor", DOMAIN, f"{HOST}_{key}", config_entry=entry
        )
        for key in ("gadget_active", "auto_sync_enabled")
    ]

    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.LOADED
    assert entry.version == 2
    assert entry.options["extra_hosts"] == ["teslapi.local"]

    cpu_after = ent_reg.async_get(cpu.entity_id)
    assert cpu_after is not None
    assert cpu_after.entity_id == "sensor.my_pi_cpu"
    assert cpu_after.unique_id == f"{entry.entry_id}_cpu_temp"
    assert ent_reg.async_get(gadget.entity_id).unique_id == f"{entry.entry_id}_gadget"
    assert ent_reg.async_get(dup_cpu.entity_id) is None
    for reg_entry in removed:
        assert ent_reg.async_get(reg_entry.entity_id) is None

    device_after = dev_reg.async_get(device.id)
    assert device_after.identifiers == {(DOMAIN, entry.entry_id)}
    assert dev_reg.async_get(stray.id) is None
    # The stray device is removed, not detached with the call HA 2027.8 drops.
    assert not any("remove_config_entry_id" in kw for kw in update_kwargs)
    assert "remove_config_entry_id" not in caplog.text
    assert hass.states.get("sensor.my_pi_cpu").state == "41.2"
    # No second set of entities appeared under new names.
    assert hass.states.get("sensor.teslapi_cpu_temperature") is None


async def test_future_version_refused(hass: HomeAssistant) -> None:
    """An entry from a newer release is not touched."""
    entry = make_entry(version=3)
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    assert entry.state is ConfigEntryState.MIGRATION_ERROR
