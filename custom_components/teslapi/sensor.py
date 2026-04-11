"""Sensor platform for TeslaPi."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    PERCENTAGE,
    SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
    UnitOfInformation,
    UnitOfTemperature,
    UnitOfTime,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import TeslaPiCoordinator
from .entity import TeslaPiEntity


def _get_storage_by_label(data: dict, label: str) -> dict | None:
    """Find a storage entry by label."""
    for s in data.get("storage", []):
        if s.get("label") == label:
            return s
    return None


@dataclass(frozen=True, kw_only=True)
class TeslaPiSensorDescription(SensorEntityDescription):
    """Describe a TeslaPi sensor."""

    value_fn: Callable[[dict[str, Any]], Any]
    attr_fn: Callable[[dict[str, Any]], dict[str, Any]] | None = None


SENSOR_DESCRIPTIONS: tuple[TeslaPiSensorDescription, ...] = (
    TeslaPiSensorDescription(
        key="status",
        translation_key="status",
        value_fn=lambda d: d.get("state"),
        attr_fn=lambda d: {"timestamp": d.get("timestamp")},
    ),
    TeslaPiSensorDescription(
        key="cpu_temp",
        translation_key="cpu_temp",
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda d: d.get("system", {}).get("cpu_temp_celsius"),
    ),
    TeslaPiSensorDescription(
        key="cam_storage_pct",
        translation_key="cam_storage_pct",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:camera",
        value_fn=lambda d: (
            s.get("percent_used") if (s := _get_storage_by_label(d, "Dashcam")) else None
        ),
        attr_fn=lambda d: (
            {
                "total_bytes": s.get("total_bytes"),
                "used_bytes": s.get("used_bytes"),
                "free_bytes": s.get("free_bytes"),
                "mount_point": s.get("mount_point"),
            }
            if (s := _get_storage_by_label(d, "Dashcam"))
            else {}
        ),
    ),
    TeslaPiSensorDescription(
        key="music_storage_pct",
        translation_key="music_storage_pct",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:music",
        value_fn=lambda d: (
            s.get("percent_used") if (s := _get_storage_by_label(d, "Music")) else None
        ),
        attr_fn=lambda d: (
            {
                "total_bytes": s.get("total_bytes"),
                "used_bytes": s.get("used_bytes"),
                "free_bytes": s.get("free_bytes"),
                "mount_point": s.get("mount_point"),
            }
            if (s := _get_storage_by_label(d, "Music"))
            else {}
        ),
    ),
    TeslaPiSensorDescription(
        key="last_archive",
        translation_key="last_archive",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=lambda d: (
            datetime.fromisoformat(ts)
            if (ts := d.get("archive", {}).get("last_archive_at"))
            else None
        ),
        attr_fn=lambda d: {
            "clips": d.get("archive", {}).get("last_archive_clips"),
            "bytes": d.get("archive", {}).get("last_archive_bytes"),
            "server": d.get("archive", {}).get("server_name"),
        },
    ),
    TeslaPiSensorDescription(
        key="last_music_sync",
        translation_key="last_music_sync",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=lambda d: (
            datetime.fromisoformat(ts)
            if (ts := d.get("music", {}).get("last_sync_at"))
            else None
        ),
    ),
    TeslaPiSensorDescription(
        key="artists_synced",
        translation_key="artists_synced",
        icon="mdi:account-music",
        state_class=SensorStateClass.TOTAL,
        value_fn=lambda d: d.get("music", {}).get("total_artists"),
        attr_fn=lambda d: {
            "total_tracks": d.get("music", {}).get("total_tracks"),
        },
    ),
    TeslaPiSensorDescription(
        key="wifi_signal",
        translation_key="wifi_signal",
        native_unit_of_measurement=SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
        device_class=SensorDeviceClass.SIGNAL_STRENGTH,
        state_class=SensorStateClass.MEASUREMENT,
        entity_registry_enabled_default=False,
        value_fn=lambda d: d.get("system", {}).get("wifi_signal_dbm"),
        attr_fn=lambda d: {
            "ssid": d.get("system", {}).get("wifi_ssid"),
            "ip_address": d.get("system", {}).get("ip_address"),
        },
    ),
    TeslaPiSensorDescription(
        key="uptime",
        translation_key="uptime",
        native_unit_of_measurement=UnitOfTime.SECONDS,
        device_class=SensorDeviceClass.DURATION,
        entity_registry_enabled_default=False,
        value_fn=lambda d: d.get("system", {}).get("uptime_seconds"),
    ),
    TeslaPiSensorDescription(
        key="ram_usage",
        translation_key="ram_usage",
        native_unit_of_measurement=UnitOfInformation.MEGABYTES,
        device_class=SensorDeviceClass.DATA_SIZE,
        state_class=SensorStateClass.MEASUREMENT,
        entity_registry_enabled_default=False,
        value_fn=lambda d: (
            round(d.get("system", {}).get("ram_used_bytes", 0) / 1048576, 1)
            if d.get("system", {}).get("ram_used_bytes") is not None
            else None
        ),
        attr_fn=lambda d: {
            "total_mb": round(
                d.get("system", {}).get("ram_total_bytes", 0) / 1048576, 1
            ),
        },
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up TeslaPi sensors."""
    coordinator: TeslaPiCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        TeslaPiSensor(coordinator, description) for description in SENSOR_DESCRIPTIONS
    )


class TeslaPiSensor(TeslaPiEntity, SensorEntity):
    """Representation of a TeslaPi sensor."""

    entity_description: TeslaPiSensorDescription

    def __init__(
        self,
        coordinator: TeslaPiCoordinator,
        description: TeslaPiSensorDescription,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> Any:
        """Return the sensor value."""
        if not self.coordinator.data:
            return None
        return self.entity_description.value_fn(self.coordinator.data)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Return extra state attributes."""
        if not self.coordinator.data or not self.entity_description.attr_fn:
            return None
        return self.entity_description.attr_fn(self.coordinator.data)
