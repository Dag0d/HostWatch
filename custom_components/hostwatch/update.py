"""Update platform for HostWatch agent software updates."""

from __future__ import annotations

from typing import Any

from homeassistant.components.update import UpdateDeviceClass, UpdateEntity, UpdateEntityFeature
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback

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
    """Set up the HostWatch update entity for one node."""
    node = get_storage(hass).get_node(entry.data["node_id"])
    if node is None:
        return
    async_add_entities([HostWatchAgentUpdateEntity(hass, entry, node)])


class HostWatchAgentUpdateEntity(UpdateEntity):
    """Expose signed agent releases through Home Assistant's update model."""

    _attr_has_entity_name = True
    _attr_translation_key = "agent"
    _attr_device_class = UpdateDeviceClass.FIRMWARE
    _attr_supported_features = UpdateEntityFeature.INSTALL

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, node: dict[str, Any]) -> None:
        self.hass = hass
        self._node_id = entry.data["node_id"]
        self._node = node
        self._state = get_runtime(hass).get_state(self._node_id) or node
        self._pending_install = False
        self._pending_target_version: str | None = None
        self._attr_unique_id = f"{self._node_id}_agent"
        self._attr_device_info = hostwatch_device_info(hass, node)

    @property
    def suggested_object_id(self) -> str | None:
        """Return the object ID used for initial and reset entity ID generation."""
        return suggested_object_id(self._node, "agent")

    @property
    def installed_version(self) -> str | None:
        """Return the currently installed agent version reported by the node."""
        return self._state.get("agent_version")

    @property
    def latest_version(self) -> str | None:
        """Return the latest signed agent release version."""
        release = get_release_manager(self.hass).release
        return release.get("version") if release else None

    @property
    def release_summary(self) -> str | None:
        """Return release notes for the latest signed agent release."""
        release = get_release_manager(self.hass).release
        if not release:
            return None
        notes = release.get("release_notes")
        return notes if isinstance(notes, str) and notes.strip() else None

    @property
    def release_url(self) -> str | None:
        """Return the release page URL."""
        release = get_release_manager(self.hass).release
        if not release:
            return None
        url = release.get("release_url")
        return url if isinstance(url, str) else None

    @property
    def in_progress(self) -> bool:
        """Return whether an agent update command is currently queued or running."""
        if self._pending_install:
            return True
        return self._has_running_update_command()

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
        if not self._pending_install:
            return
        if self._has_running_update_command():
            return
        if (
            self._pending_target_version
            and self.installed_version
            and compare_versions(self.installed_version, self._pending_target_version) >= 0
        ):
            self._pending_install = False
            self._pending_target_version = None
            return
        self._pending_install = False
        self._pending_target_version = None

    @property
    def available(self) -> bool:
        """Return whether the update entity itself is available."""
        return True

    @property
    def latest_version_is_skipped(self) -> bool:
        """HostWatch does not support per-version skip state."""
        return False

    @property
    def installed_version_is_latest(self) -> bool | None:
        """Return whether the installed agent is already current."""
        if not self.installed_version or not self.latest_version:
            return None
        return compare_versions(self.installed_version, self.latest_version) >= 0

    async def async_install(self, version: str | None, backup: bool, **kwargs: Any) -> None:
        """Queue a signed agent update for this node."""
        target_version = version or self.latest_version
        if not target_version:
            return
        self._pending_install = True
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
            self._refresh_pending_install_state()
            self.async_write_ha_state()

        @callback
        def handle_release_update() -> None:
            self._refresh_pending_install_state()
            self.async_write_ha_state()

        @callback
        def handle_command_update() -> None:
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
