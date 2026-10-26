"""Media source for TeslaPi dashcam clips."""

from __future__ import annotations

import re
from typing import Any

from homeassistant.components.media_player import BrowseError, MediaClass, MediaType
from homeassistant.components.media_source import (
    BrowseMediaSource,
    MediaSource,
    MediaSourceItem,
    PlayMedia,
    Unresolvable,
)
from homeassistant.core import HomeAssistant

from .api import TeslaPiError
from .const import (
    API_ARCHIVE_CLIPS,
    CLIP_PAGE_SIZE,
    DOMAIN,
    LOGGER,
    MAX_CLIP_PAGES,
    SUPPORTED_EVENT_TYPES,
)
from .coordinator import TeslaPiCoordinator
from .views import CLIP_FILE_RE, EVENT_DIR_RE, clip_path_ok, clip_url

CAMERA_LABELS = {
    "front": "Front",
    "back": "Back",
    "left_repeater": "Left Repeater",
    "right_repeater": "Right Repeater",
    "left_pillar": "Left Pillar",
    "right_pillar": "Right Pillar",
}

_EVENT_TIME_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})_(\d{2})-(\d{2})(?:-\d{2})?")


def display_time(event_dir: str) -> str:
    """Show 2026-04-10_14-19-54 as 2026-04-10 14:19."""
    if match := _EVENT_TIME_RE.match(event_dir):
        return f"{match.group(1)} {match.group(2)}:{match.group(3)}"
    return event_dir


def _event_label(event_type: str) -> str:
    return event_type.replace("Clips", " Clips")


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
        """Get coordinators for loaded TeslaPi entries."""
        return {
            entry.entry_id: entry.runtime_data
            for entry in self.hass.config_entries.async_loaded_entries(DOMAIN)
        }

    async def async_resolve_media(self, item: MediaSourceItem) -> PlayMedia:
        """Resolve a clip to the HA proxy URL, never the Pi's own address."""
        # identifier format: {entry_id}/{event_type}/{event_dir}/{clip_file}
        parts = (item.identifier or "").split("/")
        if len(parts) != 4:
            raise Unresolvable(f"Invalid media identifier: {item.identifier}")

        entry_id, event_type, event_dir, clip_file = parts
        if not clip_path_ok(event_type, event_dir, clip_file):
            raise Unresolvable(f"Invalid media identifier: {item.identifier}")
        if entry_id not in self._get_coordinators():
            raise Unresolvable(f"TeslaPi device not found: {entry_id}")

        return PlayMedia(
            url=clip_url(entry_id, event_type, event_dir, clip_file),
            mime_type="video/mp4",
        )

    async def async_browse_media(
        self,
        item: MediaSourceItem,
    ) -> BrowseMediaSource:
        """Browse media."""
        coordinators = self._get_coordinators()
        if not coordinators:
            raise BrowseError("No TeslaPi devices configured")

        # Identifier levels: "", entry_id, entry_id/type, entry_id/type/dir
        identifier = item.identifier or ""
        parts = identifier.split("/") if identifier else []

        if not parts:
            return self._browse_root(coordinators)

        coordinator = coordinators.get(parts[0])
        if coordinator is None:
            raise BrowseError(f"TeslaPi device not found: {parts[0]}")
        if len(parts) >= 2 and parts[1] not in SUPPORTED_EVENT_TYPES:
            raise BrowseError(f"Unknown clip type: {parts[1]}")
        if len(parts) >= 3 and not EVENT_DIR_RE.match(parts[2]):
            raise BrowseError(f"Invalid event folder: {parts[2]}")

        if len(parts) == 1:
            return self._browse_device(parts[0], coordinator)
        if len(parts) == 2:
            return await self._browse_event_type(parts[0], parts[1], coordinator)
        if len(parts) == 3:
            return await self._browse_event_dir(
                parts[0], parts[1], parts[2], coordinator
            )
        raise BrowseError(f"Invalid browse path: {identifier}")

    @staticmethod
    def _device_name(coordinator: TeslaPiCoordinator) -> str:
        hostname = ""
        if coordinator.data:
            hostname = (coordinator.data.get("system") or {}).get("hostname") or ""
        return hostname or coordinator.config_entry.title

    def _browse_root(
        self, coordinators: dict[str, TeslaPiCoordinator]
    ) -> BrowseMediaSource:
        """List devices, or go straight to clip types with only one."""
        if len(coordinators) == 1:
            entry_id, coordinator = next(iter(coordinators.items()))
            return self._browse_device(entry_id, coordinator)

        children = [
            BrowseMediaSource(
                domain=DOMAIN,
                identifier=entry_id,
                media_class=MediaClass.DIRECTORY,
                media_content_type=MediaType.VIDEO,
                title=f"{self._device_name(coordinator)} Dashcam",
                can_play=False,
                can_expand=True,
            )
            for entry_id, coordinator in coordinators.items()
        ]
        return BrowseMediaSource(
            domain=DOMAIN,
            identifier="",
            media_class=MediaClass.DIRECTORY,
            media_content_type=MediaType.VIDEO,
            title="TeslaPi Dashcam",
            can_play=False,
            can_expand=True,
            children=children,
            children_media_class=MediaClass.DIRECTORY,
        )

    def _browse_device(
        self, entry_id: str, coordinator: TeslaPiCoordinator
    ) -> BrowseMediaSource:
        """List clip types for one device."""
        children = [
            BrowseMediaSource(
                domain=DOMAIN,
                identifier=f"{entry_id}/{event_type}",
                media_class=MediaClass.DIRECTORY,
                media_content_type=MediaType.VIDEO,
                title=_event_label(event_type),
                can_play=False,
                can_expand=True,
            )
            for event_type in SUPPORTED_EVENT_TYPES
        ]
        return BrowseMediaSource(
            domain=DOMAIN,
            identifier=entry_id,
            media_class=MediaClass.DIRECTORY,
            media_content_type=MediaType.VIDEO,
            title=f"{self._device_name(coordinator)} Dashcam",
            can_play=False,
            can_expand=True,
            children=children,
            children_media_class=MediaClass.DIRECTORY,
        )

    async def _fetch_clips(
        self, coordinator: TeslaPiCoordinator, event_type: str
    ) -> list[dict[str, Any]]:
        """Fetch archived clips of one type, following pages."""
        clips: list[dict[str, Any]] = []
        offset = 0
        for _ in range(MAX_CLIP_PAGES):
            try:
                data = await coordinator.client.get(
                    API_ARCHIVE_CLIPS,
                    params={
                        "event_type": event_type,
                        "limit": CLIP_PAGE_SIZE,
                        "offset": offset,
                    },
                )
            except TeslaPiError as err:
                raise BrowseError(f"Cannot list clips: {err}") from err
            page = data.get("clips") or []
            clips.extend(page)
            if not data.get("hasMore") or not page:
                break
            offset += len(page)
        else:
            LOGGER.debug(
                "Stopped listing %s after %s clips", event_type, len(clips)
            )
        return [
            c
            for c in clips
            if EVENT_DIR_RE.match(str(c.get("event_dir", "")))
            and CLIP_FILE_RE.match(str(c.get("clip_file", "")))
        ]

    async def _browse_event_type(
        self, entry_id: str, event_type: str, coordinator: TeslaPiCoordinator
    ) -> BrowseMediaSource:
        """List event folders, newest first."""
        clips = await self._fetch_clips(coordinator, event_type)

        event_dirs: dict[str, dict[str, int]] = {}
        for clip in clips:
            info = event_dirs.setdefault(clip["event_dir"], {"count": 0, "size": 0})
            info["count"] += 1
            info["size"] += clip.get("size_bytes") or 0

        children = []
        for edir in sorted(event_dirs, reverse=True):
            info = event_dirs[edir]
            size_mb = round(info["size"] / 1048576)
            children.append(
                BrowseMediaSource(
                    domain=DOMAIN,
                    identifier=f"{entry_id}/{event_type}/{edir}",
                    media_class=MediaClass.DIRECTORY,
                    media_content_type=MediaType.VIDEO,
                    title=f"{display_time(edir)} ({info['count']} clips, {size_mb} MB)",
                    can_play=False,
                    can_expand=True,
                )
            )

        return BrowseMediaSource(
            domain=DOMAIN,
            identifier=f"{entry_id}/{event_type}",
            media_class=MediaClass.DIRECTORY,
            media_content_type=MediaType.VIDEO,
            title=_event_label(event_type),
            can_play=False,
            can_expand=True,
            children=children,
            children_media_class=MediaClass.DIRECTORY,
        )

    async def _browse_event_dir(
        self,
        entry_id: str,
        event_type: str,
        event_dir: str,
        coordinator: TeslaPiCoordinator,
    ) -> BrowseMediaSource:
        """List the camera clips in one event folder."""
        clips = [
            c
            for c in await self._fetch_clips(coordinator, event_type)
            if c["event_dir"] == event_dir
        ]
        clips.sort(key=lambda c: c["clip_file"])

        children = []
        for clip in clips:
            clip_file = clip["clip_file"]
            size_mb = round((clip.get("size_bytes") or 0) / 1048576, 1)
            stem = clip_file.rsplit(".", 1)[0]
            camera = next(
                (
                    label
                    for key, label in CAMERA_LABELS.items()
                    if stem.endswith(key)
                ),
                "Unknown",
            )
            clip_time = display_time(stem)
            children.append(
                BrowseMediaSource(
                    domain=DOMAIN,
                    identifier=f"{entry_id}/{event_type}/{event_dir}/{clip_file}",
                    media_class=MediaClass.VIDEO,
                    media_content_type="video/mp4",
                    title=f"{clip_time} {camera} ({size_mb} MB)",
                    can_play=True,
                    can_expand=False,
                )
            )

        return BrowseMediaSource(
            domain=DOMAIN,
            identifier=f"{entry_id}/{event_type}/{event_dir}",
            media_class=MediaClass.DIRECTORY,
            media_content_type=MediaType.VIDEO,
            title=f"{_event_label(event_type)} - {display_time(event_dir)}",
            can_play=False,
            can_expand=True,
            children=children,
            children_media_class=MediaClass.VIDEO,
        )
