"""Diagnostics for TeslaPi."""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.const import CONF_MAC, CONF_PASSWORD
from homeassistant.core import HomeAssistant

from .const import CONF_EXTRA_HOSTS, CONF_HOST
from .coordinator import TeslaPiConfigEntry

TO_REDACT = {
    CONF_PASSWORD,
    CONF_HOST,
    CONF_EXTRA_HOSTS,
    CONF_MAC,
    "wifi_ssid",
    "ip_address",
    "hostname",
    "server_name",
    "mount_point",
}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: TeslaPiConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    coordinator = entry.runtime_data
    client = coordinator.client
    return {
        "entry": {
            "version": entry.version,
            "data": async_redact_data(dict(entry.data), TO_REDACT),
            "options": async_redact_data(dict(entry.options), TO_REDACT),
        },
        "client": {
            "host_count": len(client.hosts),
            "active_host_is_primary": client.host == client.hosts[0],
            "port": client.port,
            "password_set": bool(entry.data.get(CONF_PASSWORD)),
            "server_version": coordinator.server_version,
        },
        "last_update_success": coordinator.last_update_success,
        "data": async_redact_data(coordinator.data or {}, TO_REDACT),
    }
