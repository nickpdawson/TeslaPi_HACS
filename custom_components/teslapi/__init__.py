"""The TeslaPi integration."""

from __future__ import annotations

from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.typing import ConfigType

from .const import CONFIG_ENTRY_VERSION, DOMAIN, LOGGER, PLATFORMS
from .coordinator import TeslaPiConfigEntry, TeslaPiCoordinator
from .migration import migrate_v1_to_v2
from .services import async_setup_services

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register services once, so they exist even before an entry loads."""
    async_setup_services(hass)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: TeslaPiConfigEntry) -> bool:
    """Set up TeslaPi from a config entry."""
    coordinator = TeslaPiCoordinator(hass, entry)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: TeslaPiConfigEntry) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_migrate_entry(hass: HomeAssistant, entry: TeslaPiConfigEntry) -> bool:
    """Migrate old config entries."""
    if entry.version > CONFIG_ENTRY_VERSION:
        # Written by a newer release; refuse rather than guess.
        return False
    if entry.version == 1:
        LOGGER.info("Migrating TeslaPi entry %s to version 2", entry.title)
        migrate_v1_to_v2(hass, entry)
    return True
