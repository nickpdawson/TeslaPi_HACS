"""Service actions for TeslaPi."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import ATTR_DEVICE_ID
from homeassistant.core import HomeAssistant, ServiceCall, callback
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv, device_registry as dr

from .const import (
    API_ARCHIVE_CANCEL,
    API_ARCHIVE_START,
    API_MUSIC_SYNC,
    API_MUSIC_SYNC_CANCEL,
    ATTR_CONFIG_ENTRY_ID,
    DOMAIN,
    SERVICE_CANCEL_ARCHIVE,
    SERVICE_CANCEL_MUSIC_SYNC,
    SERVICE_START_ARCHIVE,
    SERVICE_START_MUSIC_SYNC,
)
from .coordinator import TeslaPiConfigEntry, TeslaPiCoordinator

TARGET_SCHEMA = {
    vol.Optional(ATTR_CONFIG_ENTRY_ID): cv.string,
    vol.Optional(ATTR_DEVICE_ID): vol.All(cv.ensure_list, [cv.string]),
}

SERVICE_TARGET_SCHEMA = vol.Schema(TARGET_SCHEMA)

SERVICE_ARCHIVE_SCHEMA = vol.Schema(
    {
        **TARGET_SCHEMA,
        vol.Optional("trigger", default="ha"): vol.In(["ha", "manual"]),
        vol.Optional("delete_after", default=False): cv.boolean,
    }
)

SERVICE_MUSIC_SYNC_SCHEMA = vol.Schema(
    {
        **TARGET_SCHEMA,
        vol.Optional("mode", default="full"): vol.In(
            ["selected", "random", "recent", "full"]
        ),
        vol.Optional("paths"): vol.All(cv.ensure_list, [cv.string]),
        vol.Optional("count", default=20): vol.All(
            vol.Coerce(int), vol.Range(min=1, max=100)
        ),
        vol.Optional("type", default="artist"): vol.In(["artist", "album"]),
    }
)


@callback
def _resolve_entry(hass: HomeAssistant, call: ServiceCall) -> TeslaPiCoordinator:
    """Pick the TeslaPi the call is meant for.

    With one device the target is optional. With more than one, guessing
    could archive or sync the wrong car, so the caller must say which.
    """
    entry_id: str | None = call.data.get(ATTR_CONFIG_ENTRY_ID)
    device_ids: list[str] = call.data.get(ATTR_DEVICE_ID) or []

    if device_ids:
        if len(device_ids) > 1:
            raise ServiceValidationError(
                translation_domain=DOMAIN, translation_key="one_device_only"
            )
        device = dr.async_get(hass).async_get(device_ids[0])
        matches = [
            eid
            for eid in (device.config_entries if device else ())
            if (e := hass.config_entries.async_get_entry(eid)) and e.domain == DOMAIN
        ]
        if not matches:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="device_not_found",
                translation_placeholders={"device_id": device_ids[0]},
            )
        if entry_id and entry_id != matches[0]:
            raise ServiceValidationError(
                translation_domain=DOMAIN, translation_key="target_mismatch"
            )
        entry_id = matches[0]

    if entry_id:
        entry: TeslaPiConfigEntry | None = hass.config_entries.async_get_entry(
            entry_id
        )
        if entry is None or entry.domain != DOMAIN:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="entry_not_found",
                translation_placeholders={"entry_id": entry_id},
            )
        if entry.state is not ConfigEntryState.LOADED:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="entry_not_loaded",
                translation_placeholders={"title": entry.title},
            )
        return entry.runtime_data

    loaded = hass.config_entries.async_loaded_entries(DOMAIN)
    if not loaded:
        raise ServiceValidationError(
            translation_domain=DOMAIN, translation_key="no_entries"
        )
    if len(loaded) > 1:
        raise ServiceValidationError(
            translation_domain=DOMAIN, translation_key="entry_required"
        )
    return loaded[0].runtime_data


@callback
def async_setup_services(hass: HomeAssistant) -> None:
    """Register TeslaPi services. Called once from async_setup."""

    async def start_archive(call: ServiceCall) -> None:
        coordinator = _resolve_entry(hass, call)
        await coordinator.api_post(
            API_ARCHIVE_START,
            {
                "trigger": call.data["trigger"],
                "delete_after": call.data["delete_after"],
            },
        )
        await coordinator.async_request_refresh()

    async def cancel_archive(call: ServiceCall) -> None:
        coordinator = _resolve_entry(hass, call)
        await coordinator.api_delete(API_ARCHIVE_CANCEL)
        await coordinator.async_request_refresh()

    async def start_music_sync(call: ServiceCall) -> None:
        coordinator = _resolve_entry(hass, call)
        payload: dict[str, Any] = {
            "mode": call.data["mode"],
            "count": call.data["count"],
            "type": call.data["type"],
        }
        if call.data.get("paths"):
            payload["paths"] = call.data["paths"]
        await coordinator.api_post(API_MUSIC_SYNC, payload)
        await coordinator.async_request_refresh()

    async def cancel_music_sync(call: ServiceCall) -> None:
        coordinator = _resolve_entry(hass, call)
        await coordinator.api_delete(API_MUSIC_SYNC_CANCEL)
        await coordinator.async_request_refresh()

    hass.services.async_register(
        DOMAIN, SERVICE_START_ARCHIVE, start_archive, schema=SERVICE_ARCHIVE_SCHEMA
    )
    hass.services.async_register(
        DOMAIN, SERVICE_CANCEL_ARCHIVE, cancel_archive, schema=SERVICE_TARGET_SCHEMA
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_START_MUSIC_SYNC,
        start_music_sync,
        schema=SERVICE_MUSIC_SYNC_SCHEMA,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_CANCEL_MUSIC_SYNC,
        cancel_music_sync,
        schema=SERVICE_TARGET_SCHEMA,
    )
