"""Base entity for TeslaPi."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import TeslaPiCoordinator


class TeslaPiEntity(CoordinatorEntity[TeslaPiCoordinator]):
    """Base class for TeslaPi entities."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: TeslaPiCoordinator, key: str) -> None:
        """Initialize the entity."""
        super().__init__(coordinator)
        self._key = key
        self._attr_unique_id = f"{coordinator.host}_{key}"

    @property
    def device_info(self) -> DeviceInfo:
        """Return device info."""
        system = {}
        if self.coordinator.data:
            system = self.coordinator.data.get("system", {})

        return DeviceInfo(
            identifiers={(DOMAIN, self.coordinator.host)},
            name=system.get("hostname", f"TeslaPi ({self.coordinator.host})"),
            manufacturer="TeslaPi",
            model="Raspberry Pi USB Drive",
            sw_version=system.get("teslausb_version"),
            configuration_url=f"http://{self.coordinator.host}:{self.coordinator.port}",
        )
