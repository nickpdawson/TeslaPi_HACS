"""Run the charge-cable blueprint against a mocked TeslaPi."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path
import shutil

from freezegun.api import FrozenDateTimeFactory

from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import (
    async_fire_time_changed,
    async_mock_service,
)
from pytest_homeassistant_custom_component.test_util.aiohttp import (
    AiohttpClientMocker,
)

from .conftest import HOST, make_entry, mock_pi

BLUEPRINT = (
    Path(__file__).parent.parent
    / "blueprints"
    / "automation"
    / "teslapi"
    / "sync_on_charge_cable.yaml"
)

CABLE = "binary_sensor.car_charge_cable"
DOOR = "binary_sensor.car_driver_door"
HOME = "device_tracker.car"


def _calls(aioclient_mock: AiohttpClientMocker, method: str) -> list:
    return [
        c
        for c in aioclient_mock.mock_calls
        if c[0].lower() == method and c[1].path == "/api/music/sync"
    ]


async def _setup(hass: HomeAssistant, aioclient_mock, tmp_path: Path) -> None:
    hass.config.config_dir = str(tmp_path)
    target = tmp_path / "blueprints" / "automation" / "teslapi"
    target.mkdir(parents=True)
    shutil.copy(BLUEPRINT, target / BLUEPRINT.name)

    mock_pi(aioclient_mock)
    aioclient_mock.post(f"http://{HOST}:80/api/music/sync", json={"job_id": 1})
    aioclient_mock.delete(f"http://{HOST}:80/api/music/sync", json={"cancelled": True})
    entry = make_entry()
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    device = dr.async_entries_for_config_entry(dr.async_get(hass), entry.entry_id)[0]

    hass.states.async_set(CABLE, "off")
    hass.states.async_set(DOOR, "off")
    hass.states.async_set(HOME, "home")
    hass.states.async_set("person.nick", "home")

    assert await async_setup_component(
        hass,
        "automation",
        {
            "automation": {
                "use_blueprint": {
                    "path": "teslapi/sync_on_charge_cable.yaml",
                    "input": {
                        "teslapi_device": device.id,
                        "charge_cable": CABLE,
                        "driver_door": DOOR,
                        "car_home": HOME,
                        "people": ["person.nick"],
                        "start_delay": 0,
                        "notify_action": "notify.phone",
                    },
                }
            }
        },
    )
    await hass.async_block_till_done()


async def test_plug_in_starts_and_door_cancels(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, tmp_path: Path
) -> None:
    """Plug in starts a sync; door soon after unplug cancels it."""
    notes = async_mock_service(hass, "notify", "phone")
    await _setup(hass, aioclient_mock, tmp_path)

    hass.states.async_set(CABLE, "on")
    await hass.async_block_till_done()
    starts = _calls(aioclient_mock, "post")
    assert len(starts) == 1
    assert starts[0][2]["mode"] == "full"
    assert notes[-1].data["title"] == "TeslaPi sync started"

    hass.states.async_set(CABLE, "off")
    await hass.async_block_till_done()
    assert _calls(aioclient_mock, "delete") == []

    hass.states.async_set(DOOR, "on")
    await hass.async_block_till_done()
    assert len(_calls(aioclient_mock, "delete")) == 1
    assert notes[-1].data["title"] == "TeslaPi sync cancelled"


async def test_door_long_after_unplug_does_nothing(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    tmp_path: Path,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Opening the door well after unplugging is not leaving."""
    async_mock_service(hass, "notify", "phone")
    await _setup(hass, aioclient_mock, tmp_path)
    hass.states.async_set(CABLE, "on")
    await hass.async_block_till_done()
    hass.states.async_set(CABLE, "off")
    await hass.async_block_till_done()

    freezer.tick(timedelta(seconds=600))
    async_fire_time_changed(hass)
    hass.states.async_set(DOOR, "on")
    await hass.async_block_till_done()
    assert _calls(aioclient_mock, "delete") == []


async def test_away_from_home_does_nothing(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, tmp_path: Path
) -> None:
    """No sync starts when the car is not at home."""
    async_mock_service(hass, "notify", "phone")
    await _setup(hass, aioclient_mock, tmp_path)
    hass.states.async_set(HOME, "not_home")
    hass.states.async_set(CABLE, "on")
    await hass.async_block_till_done()
    assert _calls(aioclient_mock, "post") == []


async def test_notification_button_stops(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, tmp_path: Path
) -> None:
    """The Stop button on the notification cancels the sync."""
    async_mock_service(hass, "notify", "phone")
    await _setup(hass, aioclient_mock, tmp_path)
    hass.bus.async_fire("mobile_app_notification_action", {"action": "TESLAPI_SYNC_STOP"})
    await hass.async_block_till_done()
    assert len(_calls(aioclient_mock, "delete")) == 1
