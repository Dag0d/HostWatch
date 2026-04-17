"""GitHub-backed HostWatch agent release tracking."""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import async_track_time_interval

from .const import (
    AGENT_RELEASE_LATEST_URL,
    AGENT_RELEASE_MANIFEST_PREFIX,
    AGENT_RELEASE_REFRESH_SECONDS,
    AGENT_RELEASE_TARBALL_PREFIX,
    DOMAIN,
    SIGNAL_AGENT_RELEASE_UPDATED,
)

_LOGGER = logging.getLogger(__name__)


class AgentReleaseManager:
    """Fetch and cache the latest signed HostWatch agent release."""

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass
        self._release: dict[str, Any] | None = None
        self._last_error: str | None = None

    @property
    def release(self) -> dict[str, Any] | None:
        """Return the latest known release payload."""
        return self._release

    @property
    def last_error(self) -> str | None:
        """Return the last refresh error, if any."""
        return self._last_error

    async def async_refresh(self) -> None:
        """Refresh the cached release information from GitHub."""
        session = async_get_clientsession(self.hass)
        try:
            async with session.get(
                AGENT_RELEASE_LATEST_URL,
                headers={
                    "Accept": "application/vnd.github+json",
                    "User-Agent": "HostWatch-Home-Assistant",
                },
                raise_for_status=True,
            ) as response:
                payload = await response.json()
        except Exception as exc:  # noqa: BLE001
            self._last_error = str(exc)
            _LOGGER.warning("Failed to refresh HostWatch agent release metadata: %s", exc)
            return

        release = _parse_release(payload)
        if release is None:
            self._last_error = "latest GitHub release does not contain signed agent assets"
            _LOGGER.warning("Latest GitHub release does not contain complete signed HostWatch agent assets")
            return
        self._release = release
        self._last_error = None
        async_dispatcher_send(self.hass, SIGNAL_AGENT_RELEASE_UPDATED)


def _normalize_version(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    if normalized.startswith("v"):
        normalized = normalized[1:]
    return normalized or None


def compare_versions(left: str | None, right: str | None) -> int:
    """Compare simple dotted integer versions."""
    left_norm = _normalize_version(left)
    right_norm = _normalize_version(right)
    if not left_norm or not right_norm:
        left_text = left_norm or ""
        right_text = right_norm or ""
        return (left_text > right_text) - (left_text < right_text)
    try:
        left_parts = tuple(int(part) for part in left_norm.split("."))
        right_parts = tuple(int(part) for part in right_norm.split("."))
    except ValueError:
        return (left_norm > right_norm) - (left_norm < right_norm)
    length = max(len(left_parts), len(right_parts))
    padded_left = left_parts + (0,) * (length - len(left_parts))
    padded_right = right_parts + (0,) * (length - len(right_parts))
    return (padded_left > padded_right) - (padded_left < padded_right)


def _parse_release(payload: dict[str, Any]) -> dict[str, Any] | None:
    version = _normalize_version(payload.get("tag_name") or payload.get("name"))
    if not version:
        return None
    assets = payload.get("assets", [])
    manifest = any(
        isinstance(asset.get("name"), str)
        and asset["name"].startswith(AGENT_RELEASE_MANIFEST_PREFIX)
        and asset["name"].endswith(".json")
        for asset in assets
    )
    signature = any(
        isinstance(asset.get("name"), str)
        and asset["name"].startswith(AGENT_RELEASE_MANIFEST_PREFIX)
        and asset["name"].endswith(".sig")
        for asset in assets
    )
    tarball = any(
        isinstance(asset.get("name"), str)
        and asset["name"].startswith(AGENT_RELEASE_TARBALL_PREFIX)
        and asset["name"].endswith(".tar.gz")
        for asset in assets
    )
    if not (manifest and signature and tarball):
        return None
    return {
        "version": version,
        "release_notes": payload.get("body") or "",
        "release_url": payload.get("html_url"),
        "published_at": payload.get("published_at"),
    }


async def async_setup_release_manager(hass: HomeAssistant) -> None:
    """Create and start the shared release manager."""
    hass.data.setdefault(DOMAIN, {})
    if hass.data[DOMAIN].get("release_manager") is not None:
        return
    manager = AgentReleaseManager(hass)
    hass.data[DOMAIN]["release_manager"] = manager
    hass.async_create_task(manager.async_refresh())

    async def handle_refresh(_now) -> None:
        await manager.async_refresh()

    hass.data[DOMAIN]["release_manager_unsub"] = async_track_time_interval(
        hass,
        handle_refresh,
        timedelta(seconds=AGENT_RELEASE_REFRESH_SECONDS),
    )


def get_release_manager(hass: HomeAssistant) -> AgentReleaseManager:
    """Return the shared release manager."""
    return hass.data[DOMAIN]["release_manager"]
