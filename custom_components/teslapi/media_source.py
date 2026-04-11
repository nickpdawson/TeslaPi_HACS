"""Media source for TeslaPi dashcam clips."""

from __future__ import annotations

from homeassistant.components.media_player import MediaClass, MediaType
from homeassistant.components.media_source import (
    BrowseMediaSource,
    MediaSource,
    MediaSourceItem,
    PlayMedia,
    Unresolvable,
)
from homeassistant.core import HomeAssistant

from .const import DOMAIN, LOGGER
from .coordinator import TeslaPiCoordinator

SUPPORTED_EVENT_TYPES = ("SentryClips", "SavedClips", "RecentClips")

CAMERA_LABELS = {
    "front": "Front",
    "back": "Back",
    "left_repeater": "Left Repeater",
    "right_repeater": "Right Repeater",
    "left_pillar": "Left Pillar",
    "right_pillar": "Right Pillar",
}


async def async_get_media_source(hass: HomeAssistant) -> TeslaPiMediaSource:
    """Set up TeslaPi media source."""
    return TeslaPiMediaSource(hass)


class TeslaPiMediaSource(MediaSource):
    """Provide TeslaPi dashcam clips as a media source."""

    name = "TeslaPi Dashcam"

    def __init__(self, hass: HomeAssistant) -> None:
        """Initialize the media source."""
        super().__init__(DOMAIN)
        self.hass = hass

    def _get_coordinators(self) -> dict[str, TeslaPiCoordinator]:
        """Get all TeslaPi coordinators."""
        return self.hass.data.get(DOMAIN, {})

    async def async_resolve_media(self, item: MediaSourceItem) -> PlayMedia:
        """Resolve a media item to a playable URL."""
        # identifier format: {entry_id}/{event_type}/{event_dir}/{clip_file}
        parts = item.identifier.split("/", 3)
        if len(parts) != 4:
            raise Unresolvable(f"Invalid media identifier: {item.identifier}")

        entry_id, event_type, event_dir, clip_file = parts
        coordinators = self._get_coordinators()
        coordinator = coordinators.get(entry_id)
        if not coordinator:
            raise Unresolvable(f"TeslaPi device not found: {entry_id}")

        url = f"{coordinator.base_url}/api/dashcam/video/{event_type}/{event_dir}/{clip_file}"
        return PlayMedia(url=url, mime_type="video/mp4")

    async def async_browse_media(
        self,
        item: MediaSourceItem,
    ) -> BrowseMediaSource:
        """Browse media."""
        coordinators = self._get_coordinators()

        if not coordinators:
            raise BrowseError("No TeslaPi devices configured")

        # Parse identifier: empty=root, {entry_id}, {entry_id}/{type}, {entry_id}/{type}/{dir}
        identifier = item.identifier or ""
        parts = identifier.split("/") if identifier else []

        if len(parts) == 0:
            return self._browse_root(coordinators)
        if len(parts) == 1:
            return self._browse_device(parts[0], coordinators)
        if len(parts) == 2:
            return await self._browse_event_type(parts[0], parts[1], coordinators)
        if len(parts) == 3:
            return await self._browse_event_dir(
                parts[0], parts[1], parts[2], coordinators
            )

        raise Unresolvable(f"Invalid browse path: {identifier}")

    def _browse_root(
        self, coordinators: dict[str, TeslaPiCoordinator]
    ) -> BrowseMediaSource:
        """Browse root — list devices or go straight to event types if single device."""
        if len(coordinators) == 1:
            entry_id = next(iter(coordinators))
            return self._browse_device(entry_id, coordinators)

        children = []
        for entry_id, coordinator in coordinators.items():
            hostname = "TeslaPi"
            if coordinator.data:
                hostname = coordinator.data.get("system", {}).get(
                    "hostname", "TeslaPi"
                )
            children.append(
                BrowseMediaSource(
                    domain=DOMAIN,
                    identifier=entry_id,
                    media_class=MediaClass.DIRECTORY,
                    media_content_type=MediaType.VIDEO,
                    title=f"{hostname} Dashcam",
                    can_play=False,
                    can_expand=True,
                    thumbnail=None,
                )
            )

        return BrowseMediaSource(
            domain=DOMAIN,
            identifier="",
            media_class=MediaClass.DIRECTORY,
            media_content_type=MediaType.VIDEO,
            title="TeslaPi Dashcam",
            can_play=False,
            can_expand=True,
            children=children,
        )

    def _browse_device(
        self, entry_id: str, coordinators: dict[str, TeslaPiCoordinator]
    ) -> BrowseMediaSource:
        """Browse a device — list event types."""
        coordinator = coordinators.get(entry_id)
        hostname = "TeslaPi"
        if coordinator and coordinator.data:
            hostname = coordinator.data.get("system", {}).get("hostname", "TeslaPi")

        children = []
        for event_type in SUPPORTED_EVENT_TYPES:
            label = event_type.replace("Clips", " Clips")
            children.append(
                BrowseMediaSource(
                    domain=DOMAIN,
                    identifier=f"{entry_id}/{event_type}",
                    media_class=MediaClass.DIRECTORY,
                    media_content_type=MediaType.VIDEO,
                    title=label,
                    can_play=False,
                    can_expand=True,
                    thumbnail=None,
                )
            )

        return BrowseMediaSource(
            domain=DOMAIN,
            identifier=entry_id,
            media_class=MediaClass.DIRECTORY,
            media_content_type=MediaType.VIDEO,
            title=f"{hostname} Dashcam",
            can_play=False,
            can_expand=True,
            children=children,
        )

    async def _browse_event_type(
        self,
        entry_id: str,
        event_type: str,
        coordinators: dict[str, TeslaPiCoordinator],
    ) -> BrowseMediaSource:
        """Browse an event type — list event directories (grouped by timestamp)."""
        coordinator = coordinators.get(entry_id)
        if not coordinator:
            raise Unresolvable(f"TeslaPi device not found: {entry_id}")

        # Fetch clips filtered by event type
        try:
            data = await coordinator._api_get(
                f"/api/archive/clips?event_type={event_type}&limit=200"
            )
        except Exception as err:
            LOGGER.error("Failed to fetch clips: %s", err)
            data = {"clips": []}

        # Group by event_dir
        event_dirs: dict[str, dict] = {}
        for clip in data.get("clips", []):
            edir = clip["event_dir"]
            if edir not in event_dirs:
                event_dirs[edir] = {"count": 0, "total_size": 0}
            event_dirs[edir]["count"] += 1
            event_dirs[edir]["total_size"] += clip.get("size_bytes", 0)

        # Sort by event dir (timestamp) descending
        sorted_dirs = sorted(event_dirs.keys(), reverse=True)

        children = []
        for edir in sorted_dirs:
            info = event_dirs[edir]
            size_mb = round(info["total_size"] / 1048576)
            # Format: "2026-04-10 14:19" from "2026-04-10_14-19-54"
            display_time = edir.replace("_", " ").rsplit("-", 1)[0] if "_" in edir else edir
            children.append(
                BrowseMediaSource(
                    domain=DOMAIN,
                    identifier=f"{entry_id}/{event_type}/{edir}",
                    media_class=MediaClass.DIRECTORY,
                    media_content_type=MediaType.VIDEO,
                    title=f"{display_time} ({info['count']} clips, {size_mb} MB)",
                    can_play=False,
                    can_expand=True,
                    thumbnail=None,
                )
            )

        label = event_type.replace("Clips", " Clips")
        return BrowseMediaSource(
            domain=DOMAIN,
            identifier=f"{entry_id}/{event_type}",
            media_class=MediaClass.DIRECTORY,
            media_content_type=MediaType.VIDEO,
            title=label,
            can_play=False,
            can_expand=True,
            children=children,
        )

    async def _browse_event_dir(
        self,
        entry_id: str,
        event_type: str,
        event_dir: str,
        coordinators: dict[str, TeslaPiCoordinator],
    ) -> BrowseMediaSource:
        """Browse an event directory — list individual camera clips."""
        coordinator = coordinators.get(entry_id)
        if not coordinator:
            raise Unresolvable(f"TeslaPi device not found: {entry_id}")

        # Fetch all clips, filter to this event dir
        try:
            data = await coordinator._api_get(
                f"/api/archive/clips?event_type={event_type}&limit=200"
            )
        except Exception as err:
            LOGGER.error("Failed to fetch clips: %s", err)
            data = {"clips": []}

        clips = [c for c in data.get("clips", []) if c["event_dir"] == event_dir]

        children = []
        for clip in clips:
            clip_file = clip["clip_file"]
            size_mb = round(clip.get("size_bytes", 0) / 1048576, 1)

            # Extract camera name from filename like "2026-04-10_14-16-30-front.mp4"
            camera = "Unknown"
            name_no_ext = clip_file.rsplit(".", 1)[0] if "." in clip_file else clip_file
            for cam_key, cam_label in CAMERA_LABELS.items():
                if name_no_ext.endswith(cam_key):
                    camera = cam_label
                    break

            children.append(
                BrowseMediaSource(
                    domain=DOMAIN,
                    identifier=f"{entry_id}/{event_type}/{event_dir}/{clip_file}",
                    media_class=MediaClass.VIDEO,
                    media_content_type="video/mp4",
                    title=f"{camera} ({size_mb} MB)",
                    can_play=True,
                    can_expand=False,
                    thumbnail=None,
                )
            )

        display_time = event_dir.replace("_", " ").rsplit("-", 1)[0] if "_" in event_dir else event_dir
        label = event_type.replace("Clips", " Clips")
        return BrowseMediaSource(
            domain=DOMAIN,
            identifier=f"{entry_id}/{event_type}/{event_dir}",
            media_class=MediaClass.DIRECTORY,
            media_content_type=MediaType.VIDEO,
            title=f"{label} - {display_time}",
            can_play=False,
            can_expand=True,
            children=children,
        )


class BrowseError(Exception):
    """Error browsing media."""
