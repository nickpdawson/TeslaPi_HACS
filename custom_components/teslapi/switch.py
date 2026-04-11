"""Switch platform for TeslaPi."""

from __future__ import annotations

from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from typing import Any

from homeassistant.components.switch import SwitchEntity, SwitchEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    API_AUTO_SYNC_CONFIG,
    API_GADGET_TOGGLE,
    DOMAIN,
    LOGGER,
)
from .coordinator import TeslaPiApiError, TeslaPiCoordinator
from .entity import TeslaPiEntity


@dataclass(frozen=True, kw_only=True)
class TeslaPiSwitchDescription(SwitchEntityDescription):
    """Describe a TeslaPi switch."""

    is_on_fn: Callable[[dict[str, Any]], bool | None]
    turn_on_fn: Callable[[TeslaPiCoordinator], Coroutine[Any, Any, Any]]
    turn_off_fn: Callable[[TeslaPiCoordinator], Coroutine[Any, Any, Any]]
    attr_fn: Callable[[dict[str, Any]], dict[str, Any]] | None = None


async def _gadget_on(coordinator: TeslaPiCoordinator) -> None:
    await coordinator.api_post(API_GADGET_TOGGLE, {"enabled": True})
    await coordinator.async_request_refresh()


async def _gadget_off(coordinator: TeslaPiCoordinator) -> None:
    await coordinator.api_post(API_GADGET_TOGGLE, {"enabled": False})
    await coordinator.async_request_refresh()


async def _auto_sync_on(coordinator: TeslaPiCoordinator) -> None:
    await coordinator.api_put(API_AUTO_SYNC_CONFIG, {"enabled": True})
    await coordinator.async_request_refresh()


async def _auto_sync_off(coordinator: TeslaPiCoordinator) -> None:
    await coordinator.api_put(API_AUTO_SYNC_CONFIG, {"enabled": False})
    await coordinator.async_request_refresh()


SWITCH_DESCRIPTIONS: tuple[TeslaPiSwitchDescription, ...] = (
    TeslaPiSwitchDescription(
        key="gadget",
        translation_key="gadget",
        icon="mdi:usb",
        is_on_fn=lambda d: d.get("gadget", {}).get("enabled", False),
        turn_on_fn=_gadget_on,
        turn_off_fn=_gadget_off,
        attr_fn=lambda d: {
            "state": d.get("gadget", {}).get("state"),
            "drives": d.get("gadget", {}).get("drives", []),
        },
    ),
    TeslaPiSwitchDescription(
        key="auto_sync",
        translation_key="auto_sync",
        icon="mdi:sync-circle",
        is_on_fn=lambda d: (
            d.get("auto_sync", {}).get("enabled", False)
            if d.get("auto_sync")
            else False
        ),
        turn_on_fn=_auto_sync_on,
        turn_off_fn=_auto_sync_off,
        attr_fn=lambda d: (
            {
                "check_interval": d.get("auto_sync", {}).get("check_interval"),
                "last_check_at": d.get("auto_sync", {}).get("last_check_at"),
            }
            if d.get("auto_sync")
            else {}
        ),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up TeslaPi switches."""
    coordinator: TeslaPiCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        TeslaPiSwitch(coordinator, description) for description in SWITCH_DESCRIPTIONS
    )


class TeslaPiSwitch(TeslaPiEntity, SwitchEntity):
    """Representation of a TeslaPi switch."""

    entity_description: TeslaPiSwitchDescription

    def __init__(
        self,
        coordinator: TeslaPiCoordinator,
        description: TeslaPiSwitchDescription,
    ) -> None:
        """Initialize the switch."""
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def is_on(self) -> bool | None:
        """Return true if the switch is on."""
        if not self.coordinator.data:
            return None
        return self.entity_description.is_on_fn(self.coordinator.data)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Return extra state attributes."""
        if not self.coordinator.data or not self.entity_description.attr_fn:
            return None
        return self.entity_description.attr_fn(self.coordinator.data)

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the switch on."""
        try:
            await self.entity_description.turn_on_fn(self.coordinator)
        except TeslaPiApiError as err:
            LOGGER.error("TeslaPi switch %s turn_on failed: %s", self._key, err)
            raise

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the switch off."""
        try:
            await self.entity_description.turn_off_fn(self.coordinator)
        except TeslaPiApiError as err:
            LOGGER.error("TeslaPi switch %s turn_off failed: %s", self._key, err)
            raise
