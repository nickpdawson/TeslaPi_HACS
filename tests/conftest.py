"""Fixtures for TeslaPi tests."""

from __future__ import annotations

from http import HTTPStatus
from typing import Any

import pytest

from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import (
    AiohttpClientMocker,
)

from custom_components.teslapi.const import DOMAIN

HOST = "10.0.0.5"
HOST2 = "10.0.0.6"
TOKEN = "signed-session-token"


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations: None) -> None:
    """Load custom_components/ in every test."""


def status_payload(hostname: str = "teslapi") -> dict[str, Any]:
    """A /api/status body shaped like the server's TeslaPiStatus model."""
    return {
        "state": "idle",
        "system": {
            "hostname": hostname,
            "os_version": "Raspberry Pi OS 12 (bookworm)",
            "teslausb_version": "",
            "uptime_seconds": 3600,
            "cpu_temp_celsius": 41.2,
            "cpu_usage": 3.0,
            "ram_used_bytes": 432013312,
            "ram_total_bytes": 4147483648,
            "wifi_ssid": "HomeWiFi",
            "wifi_signal_dbm": -52,
            "ip_address": HOST,
        },
        "storage": [
            {
                "total_bytes": 100,
                "used_bytes": 40,
                "free_bytes": 60,
                "percent_used": 40.0,
                "mount_point": "/mnt/cam",
                "label": "Dashcam",
            }
        ],
        "gadget": {"enabled": True, "state": "active", "drives": ["cam", "music"]},
        "dashcam": [],
        "archive": {
            "server_reachable": True,
            "server_name": "nas",
            "last_archive_at": "2026-10-20T06:00:00",
            "last_archive_clips": 4,
            "last_archive_bytes": 1000,
        },
        "music": {
            "total_artists": 10,
            "total_tracks": 100,
            "last_sync_at": None,
            "sync_in_progress": False,
        },
        "timestamp": "2026-10-26T12:00:00+00:00",
    }


AUTO_SYNC = {
    "enabled": True,
    "check_interval": 300,
    "running": False,
    "last_check_at": None,
    "last_action": None,
    "last_action_at": None,
}


def mock_pi(
    aioclient_mock: AiohttpClientMocker,
    host: str = HOST,
    hostname: str = "teslapi",
    status: HTTPStatus = HTTPStatus.OK,
) -> None:
    """Register an open (no password) TeslaPi at host."""
    base = f"http://{host}:80"
    aioclient_mock.get(f"{base}/api/health", json={"status": "ok", "version": "0.4.1"})
    aioclient_mock.get(
        f"{base}/api/status",
        status=status,
        json=status_payload(hostname) if status == HTTPStatus.OK else {"detail": "x"},
    )
    aioclient_mock.get(f"{base}/api/auto-sync/status", json=AUTO_SYNC)


def make_entry(
    host: str = HOST,
    password: str | None = None,
    version: int = 2,
    entry_id: str | None = None,
    options: dict[str, Any] | None = None,
) -> MockConfigEntry:
    """A TeslaPi config entry."""
    data: dict[str, Any] = {"host": host, "port": 80}
    if password:
        data["password"] = password
    kwargs: dict[str, Any] = {}
    if entry_id:
        kwargs["entry_id"] = entry_id
    return MockConfigEntry(
        domain=DOMAIN,
        title=f"TeslaPi ({host})",
        unique_id=f"{host}:80",
        data=data,
        options=options if options is not None else {"scan_interval": 30},
        version=version,
        **kwargs,
    )
