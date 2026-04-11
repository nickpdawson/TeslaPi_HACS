"""The TeslaPi integration."""

from __future__ import annotations

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers import config_validation as cv

from .const import (
    API_ARCHIVE_CANCEL,
    API_ARCHIVE_START,
    API_MUSIC_SYNC,
    API_MUSIC_SYNC_CANCEL,
    CONF_SCAN_INTERVAL,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    LOGGER,
    PLATFORMS,
    SERVICE_CANCEL_ARCHIVE,
    SERVICE_CANCEL_MUSIC_SYNC,
    SERVICE_START_ARCHIVE,
    SERVICE_START_MUSIC_SYNC,
)
from .coordinator import TeslaPiApiError, TeslaPiCoordinator

SERVICE_ARCHIVE_SCHEMA = vol.Schema(
    {
        vol.Optional("trigger", default="ha"): cv.string,
        vol.Optional("delete_after", default=False): cv.boolean,
    }
)

SERVICE_MUSIC_SYNC_SCHEMA = vol.Schema(
    {
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


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up TeslaPi from a config entry."""
    coordinator = TeslaPiCoordinator(hass, entry)
    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Register services (once, for the domain)
    if not hass.services.has_service(DOMAIN, SERVICE_START_ARCHIVE):
        _register_services(hass)

    entry.async_on_unload(entry.add_update_listener(_async_update_listener))

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unforward_entry_setups(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id)

    # Unregister services if no more entries
    if not hass.data.get(DOMAIN):
        for service in (
            SERVICE_START_ARCHIVE,
            SERVICE_CANCEL_ARCHIVE,
            SERVICE_START_MUSIC_SYNC,
            SERVICE_CANCEL_MUSIC_SYNC,
        ):
            hass.services.async_remove(DOMAIN, service)

    return unload_ok


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Handle options update — reload the entry to pick up new scan interval."""
    await hass.config_entries.async_reload(entry.entry_id)


def _get_coordinator(hass: HomeAssistant, entry_id: str | None = None) -> TeslaPiCoordinator:
    """Get the coordinator, optionally for a specific entry."""
    entries = hass.data.get(DOMAIN, {})
    if entry_id:
        return entries[entry_id]
    # Default to first entry
    if entries:
        return next(iter(entries.values()))
    raise ValueError("No TeslaPi devices configured")


def _register_services(hass: HomeAssistant) -> None:
    """Register TeslaPi services."""

    async def handle_start_archive(call: ServiceCall) -> None:
        """Handle the start_archive service call."""
        coordinator = _get_coordinator(hass, call.data.get("entry_id"))
        try:
            await coordinator.api_post(
                API_ARCHIVE_START,
                {
                    "trigger": call.data.get("trigger", "ha"),
                    "delete_after": call.data.get("delete_after", False),
                },
            )
            await coordinator.async_request_refresh()
        except TeslaPiApiError as err:
            LOGGER.error("start_archive failed: %s", err)
            raise

    async def handle_cancel_archive(call: ServiceCall) -> None:
        """Handle the cancel_archive service call."""
        coordinator = _get_coordinator(hass, call.data.get("entry_id"))
        try:
            await coordinator.api_delete(API_ARCHIVE_CANCEL)
            await coordinator.async_request_refresh()
        except TeslaPiApiError as err:
            LOGGER.error("cancel_archive failed: %s", err)
            raise

    async def handle_start_music_sync(call: ServiceCall) -> None:
        """Handle the start_music_sync service call."""
        coordinator = _get_coordinator(hass, call.data.get("entry_id"))
        payload: dict = {"mode": call.data.get("mode", "full")}
        if call.data.get("paths"):
            payload["paths"] = call.data["paths"]
        if call.data.get("count"):
            payload["count"] = call.data["count"]
        if call.data.get("type"):
            payload["type"] = call.data["type"]
        try:
            await coordinator.api_post(API_MUSIC_SYNC, payload)
            await coordinator.async_request_refresh()
        except TeslaPiApiError as err:
            LOGGER.error("start_music_sync failed: %s", err)
            raise

    async def handle_cancel_music_sync(call: ServiceCall) -> None:
        """Handle the cancel_music_sync service call."""
        coordinator = _get_coordinator(hass, call.data.get("entry_id"))
        try:
            await coordinator.api_delete(API_MUSIC_SYNC_CANCEL)
            await coordinator.async_request_refresh()
        except TeslaPiApiError as err:
            LOGGER.error("cancel_music_sync failed: %s", err)
            raise

    hass.services.async_register(
        DOMAIN, SERVICE_START_ARCHIVE, handle_start_archive, schema=SERVICE_ARCHIVE_SCHEMA
    )
    hass.services.async_register(
        DOMAIN, SERVICE_CANCEL_ARCHIVE, handle_cancel_archive
    )
    hass.services.async_register(
        DOMAIN, SERVICE_START_MUSIC_SYNC, handle_start_music_sync, schema=SERVICE_MUSIC_SYNC_SCHEMA
    )
    hass.services.async_register(
        DOMAIN, SERVICE_CANCEL_MUSIC_SYNC, handle_cancel_music_sync
    )
