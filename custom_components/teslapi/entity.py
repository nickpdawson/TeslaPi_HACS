"""Base entity for TeslaPi."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import CONF_HOST, CONF_PORT, DEFAULT_PORT, DOMAIN
from .coordinator import TeslaPiCoordinator


def device_info(coordinator: TeslaPiCoordinator) -> DeviceInfo:
    """Device info for a TeslaPi. Keyed on the entry ID, not the address."""
    entry = coordinator.config_entry
    system = (coordinator.data or {}).get("system") or {}
    host = entry.data[CONF_HOST]
    if ":" in host:
        host = f"[{host}]"
    return DeviceInfo(
        identifiers={(DOMAIN, entry.entry_id)},
        # The server sends "" when it cannot read these.
        name=system.get("hostname") or entry.title,
        manufacturer="TeslaPi",
        model="Raspberry Pi USB Drive",
        sw_version=system.get("teslausb_version") or coordinator.server_version,
        configuration_url=f"http://{host}:{entry.data.get(CONF_PORT, DEFAULT_PORT)}",
    )


class TeslaPiEntity(CoordinatorEntity[TeslaPiCoordinator]):
    """Base class for TeslaPi entities."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: TeslaPiCoordinator, key: str) -> None:
        """Initialize the entity."""
        super().__init__(coordinator)
        self._key = key
        self._attr_unique_id = f"{coordinator.config_entry.entry_id}_{key}"
        self._attr_device_info = device_info(coordinator)
