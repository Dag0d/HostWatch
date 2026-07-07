"""Update platform for HostWatch software updates."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from homeassistant.components.update import UpdateDeviceClass, UpdateEntity, UpdateEntityFeature
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import dt as dt_util

from .const import SIGNAL_AGENT_RELEASE_UPDATED, SIGNAL_COMMAND_RUN_UPDATED, SIGNAL_NODE_UPDATED
from .device import hostwatch_device_info
from .entity_ids import suggested_object_id
from .maintenance import async_notify_command_run_updated
from .release import compare_versions, get_release_manager
from .runtime import get_runtime
from .storage import get_storage


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up HostWatch update entities for one node."""
    node = get_storage(hass).get_node(entry.data["node_id"])
    if node is None:
        return
    async_add_entities(
        [
            HostWatchAgentUpdateEntity(hass, entry, node),
            HostWatchAptUpdateEntity(hass, entry, node),
        ]
    )


class HostWatchAgentUpdateEntity(UpdateEntity):
    """Expose signed agent releases through Home Assistant's update model."""

    _attr_has_entity_name = True
    _attr_translation_key = "agent"
    _attr_device_class = UpdateDeviceClass.FIRMWARE
    _attr_supported_features = UpdateEntityFeature.INSTALL | UpdateEntityFeature.PROGRESS
    _attr_should_poll = False
    _attr_available = True

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, node: dict[str, Any]) -> None:
        self.hass = hass
        self._node_id = entry.data["node_id"]
        self._node = node
        self._state = get_runtime(hass).get_state(self._node_id) or node
        self._attr_in_progress = False
        self._pending_target_version: str | None = None
        self._attr_unique_id = f"{self._node_id}_agent"
        self._attr_device_info = hostwatch_device_info(hass, node)
        self._sync_attrs()

    @property
    def suggested_object_id(self) -> str | None:
        """Return the object ID used for initial and reset entity ID generation."""
        return suggested_object_id(self._node, "agent")

    def _sync_attrs(self) -> None:
        """Synchronize Home Assistant update attributes from in-memory state."""
        release = get_release_manager(self.hass).release
        self._attr_installed_version = self._state.get("agent_version")
        self._attr_latest_version = release.get("version") if release else None
        notes = release.get("release_notes") if release else None
        self._attr_release_summary = notes if isinstance(notes, str) and notes.strip() else None
        url = release.get("release_url") if release else None
        self._attr_release_url = url if isinstance(url, str) else None

    def _has_running_update_command(self) -> bool:
        """Return whether an agent update command is currently queued or running in storage."""
        runs = get_storage(self.hass).get_recent_command_runs(self._node_id)
        for run in runs:
            if run.get("command_type") != "agent_update":
                continue
            return run.get("status") in {"queued", "running"}
        return False

    def _refresh_pending_install_state(self) -> None:
        """Drop the optimistic install state once storage or node state confirms progress/completion."""
        if self._has_running_update_command():
            self._attr_in_progress = True
            return
        if (
            self._pending_target_version
            and self._attr_installed_version
            and compare_versions(self._attr_installed_version, self._pending_target_version) >= 0
        ):
            self._attr_in_progress = False
            self._pending_target_version = None
            return
        self._attr_in_progress = False
        self._pending_target_version = None

    @property
    def latest_version_is_skipped(self) -> bool:
        """HostWatch does not support per-version skip state."""
        return False

    @property
    def installed_version_is_latest(self) -> bool | None:
        """Return whether the installed agent is already current."""
        if not self._attr_installed_version or not self._attr_latest_version:
            return None
        return compare_versions(self._attr_installed_version, self._attr_latest_version) >= 0

    async def async_install(self, version: str | None, backup: bool, **kwargs: Any) -> None:
        """Queue a signed agent update for this node."""
        target_version = version or self._attr_latest_version
        if not target_version:
            return
        self._attr_in_progress = True
        self._pending_target_version = target_version
        self.async_write_ha_state()
        await get_storage(self.hass).async_create_command_run(
            self._node_id,
            "agent_update",
            params={"version": target_version},
        )
        async_notify_command_run_updated(self.hass, self._node_id)
        self.async_write_ha_state()

    async def async_added_to_hass(self) -> None:
        """Subscribe to node, release, and command-run updates."""

        @callback
        def handle_node_update() -> None:
            self._state = get_runtime(self.hass).get_state(self._node_id)
            self._sync_attrs()
            self._refresh_pending_install_state()
            self.async_write_ha_state()

        @callback
        def handle_release_update() -> None:
            self._sync_attrs()
            self._refresh_pending_install_state()
            self.async_write_ha_state()

        @callback
        def handle_command_update() -> None:
            self._sync_attrs()
            self._refresh_pending_install_state()
            self.async_write_ha_state()

        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                SIGNAL_NODE_UPDATED.format(node_id=self._node_id),
                handle_node_update,
            )
        )
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                SIGNAL_AGENT_RELEASE_UPDATED,
                handle_release_update,
            )
        )
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                SIGNAL_COMMAND_RUN_UPDATED.format(node_id=self._node_id),
                handle_command_update,
            )
        )


class HostWatchAptUpdateEntity(UpdateEntity):
    """Expose explicit APT upgrade snapshots through Home Assistant's update model."""

    _attr_has_entity_name = True
    _attr_translation_key = "apt_packages"
    _attr_supported_features = UpdateEntityFeature.INSTALL | UpdateEntityFeature.PROGRESS
    _attr_should_poll = False

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, node: dict[str, Any]) -> None:
        self.hass = hass
        self._node_id = entry.data["node_id"]
        self._node = node
        self._state = get_runtime(hass).get_state(self._node_id) or node
        self._attr_in_progress = False
        self._pending_target_marker: str | None = None
        self._installed_marker: str | None = None
        self._latest_marker: str | None = None
        self._attr_unique_id = f"{self._node_id}_apt_packages"
        self._attr_device_info = hostwatch_device_info(hass, node)
        self._sync_attrs()

    @property
    def suggested_object_id(self) -> str | None:
        """Return the object ID used for initial and reset entity ID generation."""
        return suggested_object_id(self._node, "apt_packages")

    def _sync_attrs(self) -> None:
        """Synchronize Home Assistant update attributes from the current node state."""
        apt_state = _apt_state(self._state)
        snapshot = _apt_update_snapshot(self._state)
        self._attr_available = apt_state.get("supported") is not False
        self._installed_marker = _apt_installed_marker(apt_state, snapshot)
        self._latest_marker = _apt_latest_marker(apt_state, snapshot, self._installed_marker)
        self._attr_installed_version = _format_marker(self._installed_marker)
        self._attr_latest_version = _format_marker(self._latest_marker)
        preview = snapshot.get("preview")
        self._attr_release_summary = (
            preview
            if _apt_snapshot_has_updates(snapshot, self._installed_marker)
            and isinstance(preview, str)
            and preview.strip()
            else None
        )
        self._attr_release_url = None

    def _has_running_update_command(self) -> bool:
        """Return whether an APT upgrade command is currently queued or running in storage."""
        runs = get_storage(self.hass).get_recent_command_runs(self._node_id)
        for run in runs:
            if run.get("command_type") != "apt_upgrade":
                continue
            return run.get("status") in {"queued", "running"}
        return False

    def _refresh_pending_install_state(self) -> None:
        """Drop the optimistic install state once storage or node state confirms progress/completion."""
        if self._has_running_update_command():
            self._attr_in_progress = True
            return
        if (
            self._pending_target_marker
            and self._installed_marker
            and _compare_markers(self._installed_marker, self._pending_target_marker) >= 0
        ):
            self._attr_in_progress = False
            self._pending_target_marker = None
            return
        self._attr_in_progress = False
        self._pending_target_marker = None

    @property
    def latest_version_is_skipped(self) -> bool:
        """HostWatch does not support per-version skip state for APT snapshots."""
        return False

    @property
    def installed_version_is_latest(self) -> bool | None:
        """Return whether the node is already current according to the latest snapshot."""
        if not self._installed_marker or not self._latest_marker:
            return None
        return _compare_markers(self._installed_marker, self._latest_marker) >= 0

    async def async_install(self, version: str | None, backup: bool, **kwargs: Any) -> None:
        """Queue an APT upgrade for this node when a prepared snapshot shows updates."""
        if not self._latest_marker or not self._installed_marker:
            return
        if _compare_markers(self._installed_marker, self._latest_marker) >= 0:
            return
        self._attr_in_progress = True
        self._pending_target_marker = self._latest_marker
        self.async_write_ha_state()
        await get_storage(self.hass).async_create_command_run(self._node_id, "apt_upgrade")
        async_notify_command_run_updated(self.hass, self._node_id)
        self.async_write_ha_state()

    async def async_added_to_hass(self) -> None:
        """Subscribe to node and command-run updates."""

        @callback
        def handle_node_update() -> None:
            self._state = get_runtime(self.hass).get_state(self._node_id)
            self._sync_attrs()
            self._refresh_pending_install_state()
            self.async_write_ha_state()

        @callback
        def handle_command_update() -> None:
            self._sync_attrs()
            self._refresh_pending_install_state()
            self.async_write_ha_state()

        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                SIGNAL_NODE_UPDATED.format(node_id=self._node_id),
                handle_node_update,
            )
        )
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                SIGNAL_COMMAND_RUN_UPDATED.format(node_id=self._node_id),
                handle_command_update,
            )
        )


def _apt_state(state: dict[str, Any]) -> dict[str, Any]:
    updates = state.get("metrics", {}).get("updates", {})
    apt_state = updates.get("apt", {})
    return apt_state if isinstance(apt_state, dict) else {}


def _apt_update_snapshot(state: dict[str, Any]) -> dict[str, Any]:
    updates = state.get("metrics", {}).get("updates", {})
    snapshot = updates.get("apt_update", {})
    return snapshot if isinstance(snapshot, dict) else {}


def _apt_installed_marker(apt_state: dict[str, Any], snapshot: dict[str, Any]) -> str | None:
    for value in (apt_state.get("last_upgraded_at"), snapshot.get("last_upgraded_at")):
        if isinstance(value, str) and value:
            return value
    return None


def _apt_latest_marker(
    apt_state: dict[str, Any],
    snapshot: dict[str, Any],
    installed_marker: str | None,
) -> str | None:
    snapshot_checked = snapshot.get("checked_at")
    if _apt_snapshot_has_updates(snapshot, installed_marker) and isinstance(snapshot_checked, str) and snapshot_checked:
        return snapshot_checked
    if installed_marker:
        return installed_marker
    if isinstance(snapshot_checked, str) and snapshot_checked:
        return snapshot_checked
    fallback_checked = apt_state.get("checked_at")
    if isinstance(fallback_checked, str) and fallback_checked:
        return fallback_checked
    return None


def _apt_snapshot_has_updates(snapshot: dict[str, Any], installed_marker: str | None) -> bool:
    if snapshot.get("updates_available") is not True:
        return False
    snapshot_checked = snapshot.get("checked_at")
    if not isinstance(snapshot_checked, str) or not snapshot_checked:
        return False
    if installed_marker and _compare_markers(installed_marker, snapshot_checked) >= 0:
        return False
    return True


def _compare_markers(left: str | None, right: str | None) -> int:
    if not left or not right:
        left_text = left or ""
        right_text = right or ""
        return (left_text > right_text) - (left_text < right_text)
    return (_marker_epoch(left) > _marker_epoch(right)) - (_marker_epoch(left) < _marker_epoch(right))


def _marker_epoch(value: str) -> float:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return 0.0
    return parsed.timestamp()


def _format_marker(value: str | None) -> str | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return value
    return dt_util.as_local(parsed).strftime("%Y-%m-%d %H:%M")
