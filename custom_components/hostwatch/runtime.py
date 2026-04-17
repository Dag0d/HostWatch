"""In-memory runtime state for HostWatch nodes."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.dispatcher import async_dispatcher_send

from .const import DOMAIN, SIGNAL_NODE_UPDATED


class HostWatchRuntime:
    """Keep latest node state in memory for push-updated entities."""

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass
        self._states: dict[str, dict[str, Any]] = {}
        self._command_outputs: dict[str, dict[str, str]] = {}

    def get_state(self, node_id: str) -> dict[str, Any]:
        """Return the latest known state for a node."""
        return deepcopy(self._states.get(node_id, {}))

    def update_node(self, node_id: str, patch: dict[str, Any]) -> None:
        """Merge a patch into node state and notify listeners."""
        current = self._states.setdefault(node_id, {})
        _deep_merge(current, patch)
        async_dispatcher_send(self.hass, SIGNAL_NODE_UPDATED.format(node_id=node_id))

    def remove_node(self, node_id: str) -> None:
        """Remove a node from runtime state."""
        self._states.pop(node_id, None)
        self._command_outputs.pop(node_id, None)

    def append_command_output(self, node_id: str, run_id: str, output: str) -> None:
        """Keep command output only in memory for currently open maintenance pages."""
        node_outputs = self._command_outputs.setdefault(node_id, {})
        node_outputs[run_id] = node_outputs.get(run_id, "") + output

    def set_command_output(self, node_id: str, run_id: str, output: str) -> None:
        """Replace transient command output with a node-provided snapshot."""
        self._command_outputs.setdefault(node_id, {})[run_id] = output

    def get_command_output(self, node_id: str, run_id: str) -> str | None:
        """Return transient command output if available."""
        return self._command_outputs.get(node_id, {}).get(run_id)


def _deep_merge(target: dict[str, Any], patch: dict[str, Any]) -> None:
    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(target.get(key), dict):
            _deep_merge(target[key], value)
            continue
        target[key] = value


def get_runtime(hass: HomeAssistant) -> HostWatchRuntime:
    """Return the shared runtime."""
    return hass.data[DOMAIN]["runtime"]
