"""Binary sensor platform for TeslaPi."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import TeslaPiCoordinator
from .entity import TeslaPiEntity


@dataclass(frozen=True, kw_only=True)
class TeslaPiBinarySensorDescription(BinarySensorEntityDescription):
    """Describe a TeslaPi binary sensor."""

    value_fn: Callable[[dict[str, Any]], bool | None]
    attr_fn: Callable[[dict[str, Any]], dict[str, Any]] | None = None


BINARY_SENSOR_DESCRIPTIONS: tuple[TeslaPiBinarySensorDescription, ...] = (
    TeslaPiBinarySensorDescription(
        key="online",
        translation_key="online",
        device_class=BinarySensorDeviceClass.CONNECTIVITY,
        value_fn=lambda d: d is not None,
    ),
    TeslaPiBinarySensorDescription(
        key="gadget_active",
        translation_key="gadget_active",
        icon="mdi:usb",
        value_fn=lambda d: d.get("gadget", {}).get("enabled", False),
        attr_fn=lambda d: {
            "state": d.get("gadget", {}).get("state"),
            "drives": d.get("gadget", {}).get("drives", []),
        },
    ),
    TeslaPiBinarySensorDescription(
        key="archive_running",
        translation_key="archive_running",
        icon="mdi:cloud-upload",
        value_fn=lambda d: d.get("state") == "archiving",
    ),
    TeslaPiBinarySensorDescription(
        key="music_syncing",
        translation_key="music_syncing",
        icon="mdi:sync",
        value_fn=lambda d: d.get("music", {}).get("sync_in_progress", False),
    ),
    TeslaPiBinarySensorDescription(
        key="server_reachable",
        translation_key="server_reachable",
        device_class=BinarySensorDeviceClass.CONNECTIVITY,
        value_fn=lambda d: d.get("archive", {}).get("server_reachable", False),
        attr_fn=lambda d: {
            "server_name": d.get("archive", {}).get("server_name"),
        },
    ),
    TeslaPiBinarySensorDescription(
        key="auto_sync_enabled",
        translation_key="auto_sync_enabled",
        icon="mdi:sync-circle",
        value_fn=lambda d: (
            d.get("auto_sync", {}).get("enabled", False)
            if d.get("auto_sync")
            else None
        ),
        attr_fn=lambda d: (
            {
                "check_interval": d.get("auto_sync", {}).get("check_interval"),
                "last_check_at": d.get("auto_sync", {}).get("last_check_at"),
                "last_action": d.get("auto_sync", {}).get("last_action"),
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
    """Set up TeslaPi binary sensors."""
    coordinator: TeslaPiCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        TeslaPiBinarySensor(coordinator, description)
        for description in BINARY_SENSOR_DESCRIPTIONS
    )


class TeslaPiBinarySensor(TeslaPiEntity, BinarySensorEntity):
    """Representation of a TeslaPi binary sensor."""

    entity_description: TeslaPiBinarySensorDescription

    def __init__(
        self,
        coordinator: TeslaPiCoordinator,
        description: TeslaPiBinarySensorDescription,
    ) -> None:
        """Initialize the binary sensor."""
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def is_on(self) -> bool | None:
        """Return true if the binary sensor is on."""
        if self.entity_description.key == "online":
            return self.coordinator.last_update_success
        if not self.coordinator.data:
            return None
        return self.entity_description.value_fn(self.coordinator.data)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Return extra state attributes."""
        if not self.coordinator.data or not self.entity_description.attr_fn:
            return None
        return self.entity_description.attr_fn(self.coordinator.data)
