"""Constants for the TeslaPi integration."""

from __future__ import annotations

from datetime import timedelta
from logging import getLogger

LOGGER = getLogger(__package__)

DOMAIN = "teslapi"
DEFAULT_NAME = "TeslaPi"
DEFAULT_PORT = 80
DEFAULT_SCAN_INTERVAL = 30  # seconds
MIN_SCAN_INTERVAL = 10
MAX_SCAN_INTERVAL = 300

CONF_HOST = "host"
CONF_PORT = "port"
CONF_SCAN_INTERVAL = "scan_interval"
CONF_EXTRA_HOSTS = "extra_hosts"

# API endpoints
API_HEALTH = "/api/health"
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

# Service names
SERVICE_START_ARCHIVE = "start_archive"
SERVICE_CANCEL_ARCHIVE = "cancel_archive"
SERVICE_START_MUSIC_SYNC = "start_music_sync"
SERVICE_CANCEL_MUSIC_SYNC = "cancel_music_sync"

# Platforms
PLATFORMS = ["binary_sensor", "button", "sensor", "switch"]

# Attributes for device info
ATTR_SW_VERSION = "sw_version"
ATTR_HOSTNAME = "hostname"
ATTR_OS_VERSION = "os_version"
