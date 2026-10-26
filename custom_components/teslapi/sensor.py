"""Sensor platform for TeslaPi."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import (
    PERCENTAGE,
    SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
    EntityCategory,
    UnitOfInformation,
    UnitOfTemperature,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util

from .coordinator import TeslaPiConfigEntry, TeslaPiCoordinator
from .entity import TeslaPiEntity

STATUS_OPTIONS = ["connected", "archiving", "syncing", "idle", "error", "offline"]

# Boot time is derived from uptime at poll time, so it drifts by a second
# or so between polls. Ignore drift below this to keep the state steady.
BOOT_TIME_TOLERANCE = timedelta(seconds=60)


def parse_timestamp(value: Any) -> datetime | None:
    """Parse a server timestamp. The server stores UTC, sometimes without a zone."""
    if not value or not isinstance(value, str):
        return None
    parsed = dt_util.parse_datetime(value.replace(" ", "T", 1))
    if parsed is None:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt_util.UTC)
    return parsed


def _storage(data: dict[str, Any], label: str) -> dict[str, Any] | None:
    for item in data.get("storage") or []:
        if item.get("label") == label:
            return item
    return None


def _storage_attrs(data: dict[str, Any], label: str) -> dict[str, Any]:
    if (item := _storage(data, label)) is None:
        return {}
    return {
        "total_bytes": item.get("total_bytes"),
        "used_bytes": item.get("used_bytes"),
        "free_bytes": item.get("free_bytes"),
        "mount_point": item.get("mount_point"),
    }


def _system(data: dict[str, Any]) -> dict[str, Any]:
    return data.get("system") or {}


def _status(data: dict[str, Any]) -> str | None:
    state = data.get("state")
    return state if state in STATUS_OPTIONS else None


def _ram_mib(data: dict[str, Any]) -> float | None:
    used = _system(data).get("ram_used_bytes")
    return round(used / 1048576, 1) if used is not None else None


def _boot_time(data: dict[str, Any]) -> datetime | None:
    uptime = _system(data).get("uptime_seconds")
    if not uptime:
        return None
    return (dt_util.utcnow() - timedelta(seconds=uptime)).replace(microsecond=0)


@dataclass(frozen=True, kw_only=True)
class TeslaPiSensorDescription(SensorEntityDescription):
    """Describe a TeslaPi sensor."""

    value_fn: Callable[[dict[str, Any]], Any]
    attr_fn: Callable[[dict[str, Any]], dict[str, Any]] | None = None


SENSOR_DESCRIPTIONS: tuple[TeslaPiSensorDescription, ...] = (
    TeslaPiSensorDescription(
        key="status",
        translation_key="status",
        device_class=SensorDeviceClass.ENUM,
        options=STATUS_OPTIONS,
        value_fn=_status,
    ),
    TeslaPiSensorDescription(
        key="cpu_temp",
        translation_key="cpu_temp",
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: _system(d).get("cpu_temp_celsius"),
    ),
    TeslaPiSensorDescription(
        key="cam_storage_pct",
        translation_key="cam_storage_pct",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda d: (_storage(d, "Dashcam") or {}).get("percent_used"),
        attr_fn=lambda d: _storage_attrs(d, "Dashcam"),
    ),
    TeslaPiSensorDescription(
        key="music_storage_pct",
        translation_key="music_storage_pct",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda d: (_storage(d, "Music") or {}).get("percent_used"),
        attr_fn=lambda d: _storage_attrs(d, "Music"),
    ),
    TeslaPiSensorDescription(
        key="last_archive",
        translation_key="last_archive",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=lambda d: parse_timestamp((d.get("archive") or {}).get("last_archive_at")),
        attr_fn=lambda d: {
            "clips": (d.get("archive") or {}).get("last_archive_clips"),
            "bytes": (d.get("archive") or {}).get("last_archive_bytes"),
            "server": (d.get("archive") or {}).get("server_name"),
        },
    ),
    TeslaPiSensorDescription(
        key="last_music_sync",
        translation_key="last_music_sync",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=lambda d: parse_timestamp((d.get("music") or {}).get("last_sync_at")),
    ),
    TeslaPiSensorDescription(
        key="artists_synced",
        translation_key="artists_synced",
        state_class=SensorStateClass.TOTAL,
        value_fn=lambda d: (d.get("music") or {}).get("total_artists"),
        attr_fn=lambda d: {
            "total_tracks": (d.get("music") or {}).get("total_tracks"),
        },
    ),
    TeslaPiSensorDescription(
        key="wifi_signal",
        translation_key="wifi_signal",
        native_unit_of_measurement=SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
        device_class=SensorDeviceClass.SIGNAL_STRENGTH,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        # The server reports 0 when it cannot read the signal.
        value_fn=lambda d: _system(d).get("wifi_signal_dbm") or None,
        attr_fn=lambda d: {
            "ssid": _system(d).get("wifi_ssid"),
            "ip_address": _system(d).get("ip_address"),
        },
    ),
    TeslaPiSensorDescription(
        key="uptime",
        translation_key="uptime",
        device_class=SensorDeviceClass.TIMESTAMP,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=_boot_time,
    ),
    TeslaPiSensorDescription(
        key="ram_usage",
        translation_key="ram_usage",
        native_unit_of_measurement=UnitOfInformation.MEBIBYTES,
        device_class=SensorDeviceClass.DATA_SIZE,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=_ram_mib,
        attr_fn=lambda d: {
            "total_mib": round((_system(d).get("ram_total_bytes") or 0) / 1048576, 1),
        },
    ),
)

ACTIVE_HOST_DESCRIPTION = SensorEntityDescription(
    key="active_host",
    translation_key="active_host",
    entity_category=EntityCategory.DIAGNOSTIC,
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: TeslaPiConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up TeslaPi sensors."""
    coordinator = entry.runtime_data
    entities: list[SensorEntity] = [
        TeslaPiSensor(coordinator, description) for description in SENSOR_DESCRIPTIONS
    ]
    entities.append(TeslaPiActiveHostSensor(coordinator, ACTIVE_HOST_DESCRIPTION))
    async_add_entities(entities)


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
        self._update_value()

    @callback
    def _update_value(self) -> None:
        data = self.coordinator.data
        if not data:
            self._attr_native_value = None
            self._attr_extra_state_attributes = None
            return
        value = self.entity_description.value_fn(data)
        old = self._attr_native_value
        if (
            self.entity_description.key == "uptime"
            and isinstance(value, datetime)
            and isinstance(old, datetime)
            and abs(value - old) < BOOT_TIME_TOLERANCE
        ):
            value = old
        self._attr_native_value = value
        self._attr_extra_state_attributes = (
            self.entity_description.attr_fn(data)
            if self.entity_description.attr_fn
            else None
        )

    @callback
    def _handle_coordinator_update(self) -> None:
        self._update_value()
        super()._handle_coordinator_update()


class TeslaPiActiveHostSensor(TeslaPiEntity, SensorEntity):
    """The address the integration is using right now."""

    def __init__(
        self, coordinator: TeslaPiCoordinator, description: SensorEntityDescription
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> str:
        """Return the active host."""
        return self.coordinator.host
