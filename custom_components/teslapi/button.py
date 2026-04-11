"""Button platform for TeslaPi."""

from __future__ import annotations

from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from typing import Any

from homeassistant.components.button import ButtonDeviceClass, ButtonEntity, ButtonEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    API_ARCHIVE_START,
    API_MUSIC_SYNC,
    API_SYSTEM_REBOOT,
    DOMAIN,
    LOGGER,
)
from .coordinator import TeslaPiApiError, TeslaPiCoordinator
from .entity import TeslaPiEntity


@dataclass(frozen=True, kw_only=True)
class TeslaPiButtonDescription(ButtonEntityDescription):
    """Describe a TeslaPi button."""

    press_fn: Callable[[TeslaPiCoordinator], Coroutine[Any, Any, Any]]


async def _press_archive(coordinator: TeslaPiCoordinator) -> None:
    """Start an archive job."""
    await coordinator.api_post(API_ARCHIVE_START, {"trigger": "ha"})
    await coordinator.async_request_refresh()


async def _press_music_sync(coordinator: TeslaPiCoordinator) -> None:
    """Start a full music sync."""
    await coordinator.api_post(API_MUSIC_SYNC, {"mode": "full"})
    await coordinator.async_request_refresh()


async def _press_reboot(coordinator: TeslaPiCoordinator) -> None:
    """Reboot the Pi."""
    await coordinator.api_post(API_SYSTEM_REBOOT, {"confirm": True})


BUTTON_DESCRIPTIONS: tuple[TeslaPiButtonDescription, ...] = (
    TeslaPiButtonDescription(
        key="archive_now",
        translation_key="archive_now",
        icon="mdi:cloud-upload",
        press_fn=_press_archive,
    ),
    TeslaPiButtonDescription(
        key="sync_music",
        translation_key="sync_music",
        icon="mdi:music-box-multiple",
        press_fn=_press_music_sync,
    ),
    TeslaPiButtonDescription(
        key="reboot",
        translation_key="reboot",
        device_class=ButtonDeviceClass.RESTART,
        press_fn=_press_reboot,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up TeslaPi buttons."""
    coordinator: TeslaPiCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        TeslaPiButton(coordinator, description) for description in BUTTON_DESCRIPTIONS
    )


class TeslaPiButton(TeslaPiEntity, ButtonEntity):
    """Representation of a TeslaPi button."""

    entity_description: TeslaPiButtonDescription

    def __init__(
        self,
        coordinator: TeslaPiCoordinator,
        description: TeslaPiButtonDescription,
    ) -> None:
        """Initialize the button."""
        super().__init__(coordinator, description.key)
        self.entity_description = description

    async def async_press(self) -> None:
        """Handle the button press."""
        try:
            await self.entity_description.press_fn(self.coordinator)
        except TeslaPiApiError as err:
            LOGGER.error("TeslaPi button %s failed: %s", self._key, err)
            raise
