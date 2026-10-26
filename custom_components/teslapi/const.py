"""Constants for the TeslaPi integration."""

from __future__ import annotations

from datetime import timedelta
from logging import getLogger

LOGGER = getLogger(__package__)

DOMAIN = "teslapi"
DEFAULT_NAME = "TeslaPi"
CONFIG_ENTRY_VERSION = 2
DEFAULT_PORT = 80
DEFAULT_SCAN_INTERVAL = 30  # seconds
MIN_SCAN_INTERVAL = 10
MAX_SCAN_INTERVAL = 300

CONF_HOST = "host"
CONF_PORT = "port"
CONF_SCAN_INTERVAL = "scan_interval"
CONF_EXTRA_HOSTS = "extra_hosts"

# Cookie name set by POST /api/auth/login on the TeslaPi server.
SESSION_COOKIE = "teslapi_session"

# API endpoints
API_HEALTH = "/api/health"
API_AUTH_LOGIN = "/api/auth/login"
API_STATUS = "/api/status"
API_ARCHIVE_START = "/api/archive/start"
API_ARCHIVE_STATUS = "/api/archive/status"
API_ARCHIVE_CANCEL = "/api/archive"
API_MUSIC_SYNC = "/api/music/sync"
API_MUSIC_SYNC_STATUS = "/api/music/sync/status"
API_MUSIC_SYNC_CANCEL = "/api/music/sync"
API_GADGET_STATUS = "/api/gadget/status"
API_GADGET_TOGGLE = "/api/gadget/toggle"
API_AUTO_SYNC_STATUS = "/api/auto-sync/status"
API_AUTO_SYNC_CONFIG = "/api/auto-sync/config"
API_SYSTEM_REBOOT = "/api/system/reboot"
API_ARCHIVE_CLIPS = "/api/archive/clips"
API_DASHCAM_VIDEO = "/api/dashcam/video"

# Dashcam folders the media browser and clip proxy accept.
SUPPORTED_EVENT_TYPES = ("SentryClips", "SavedClips", "RecentClips")
# The server caps one clips page at 200.
CLIP_PAGE_SIZE = 200
MAX_CLIP_PAGES = 10

# Service names
SERVICE_START_ARCHIVE = "start_archive"
SERVICE_CANCEL_ARCHIVE = "cancel_archive"
SERVICE_START_MUSIC_SYNC = "start_music_sync"
SERVICE_CANCEL_MUSIC_SYNC = "cancel_music_sync"
ATTR_CONFIG_ENTRY_ID = "config_entry_id"

# Platforms
PLATFORMS = ["binary_sensor", "button", "sensor", "switch"]

# Attributes for device info
ATTR_SW_VERSION = "sw_version"
ATTR_HOSTNAME = "hostname"
ATTR_OS_VERSION = "os_version"

# Entity keys that version 1 entries registered, per platform. Version 1
# unique IDs were "<host>_<key>"; the migration maps them by key.
LEGACY_ENTITY_KEYS: dict[str, tuple[str, ...]] = {
    "sensor": (
        "status",
        "cpu_temp",
        "cam_storage_pct",
        "music_storage_pct",
        "last_archive",
        "last_music_sync",
        "artists_synced",
        "wifi_signal",
        "uptime",
        "ram_usage",
    ),
    "binary_sensor": (
        "online",
        "gadget_active",
        "archive_running",
        "music_syncing",
        "server_reachable",
        "auto_sync_enabled",
    ),
    "button": ("archive_now", "sync_music", "reboot"),
    "switch": ("gadget", "auto_sync"),
}

# Binary sensors that duplicated the USB gadget and auto-sync switches.
REMOVED_ENTITY_KEYS: frozenset[tuple[str, str]] = frozenset(
    {("binary_sensor", "gadget_active"), ("binary_sensor", "auto_sync_enabled")}
)
