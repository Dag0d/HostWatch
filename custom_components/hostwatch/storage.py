"""Persistent storage helpers for HostWatch."""

from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime, timedelta
from typing import Any
import uuid

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from .const import (
    DOMAIN,
    MAINTENANCE_MODE_SECONDS,
    MAX_COMMAND_RUNS_PER_COMMAND,
    MAX_COMMAND_RUNS_PER_NODE,
    STORAGE_KEY,
    STORAGE_VERSION,
)


class HostWatchStorage:
    """Store registered nodes and minimal metadata."""

    def __init__(self, hass: HomeAssistant) -> None:
        self._store = Store[dict[str, Any]](hass, STORAGE_VERSION, STORAGE_KEY)
        self._data: dict[str, Any] = {"nodes": {}}

    async def async_load(self) -> None:
        """Load storage into memory."""
        if stored := await self._store.async_load():
            self._data = stored
        if self._sanitize_command_runs():
            await self._store.async_save(self._data)

    def get_node(self, node_id: str) -> dict[str, Any] | None:
        """Return a node by id."""
        return deepcopy(self._data["nodes"].get(node_id))

    def get_node_by_secret(self, node_id: str, secret: str) -> dict[str, Any] | None:
        """Return a node if the shared secret matches."""
        node = self._data["nodes"].get(node_id)
        if not node or node.get("node_secret") != secret:
            return None
        return deepcopy(node)

    def get_node_by_webhook_id(self, webhook_id: str) -> dict[str, Any] | None:
        """Return a node that owns the given webhook id."""
        for node in self._data["nodes"].values():
            if webhook_id in {
                node.get("heartbeat_webhook_id"),
                node.get("metrics_webhook_id"),
                node.get("command_result_webhook_id"),
                node.get("command_poll_webhook_id"),
            }:
                return deepcopy(node)
        return None

    def iter_nodes(self) -> list[dict[str, Any]]:
        """Return all stored nodes."""
        return [deepcopy(node) for node in self._data["nodes"].values()]

    async def async_upsert_node(self, node: dict[str, Any]) -> None:
        """Insert or update a node record."""
        self._data["nodes"][node["node_id"]] = node
        await self._store.async_save(self._data)

    async def async_patch_node(self, node_id: str, patch: dict[str, Any]) -> None:
        """Merge a partial patch into an existing node."""
        node = self._data["nodes"].get(node_id)
        if node is None:
            return
        _deep_merge(node, patch)
        await self._store.async_save(self._data)

    async def async_update_status(
        self, node_id: str, *, online: bool, last_seen: str | None = None
    ) -> None:
        """Update online status metadata."""
        node = self._data["nodes"].get(node_id)
        if node is None:
            return
        node["online"] = online
        if last_seen is not None:
            node["last_seen"] = last_seen
        await self._store.async_save(self._data)

    async def async_enqueue_command(self, node_id: str, command: dict[str, Any]) -> None:
        """Queue a command for a node."""
        node = self._data["nodes"].get(node_id)
        if node is None:
            return
        node.setdefault("pending_commands", []).append(command)
        await self._store.async_save(self._data)

    async def async_pop_next_command(self, node_id: str) -> dict[str, Any] | None:
        """Pop the next queued command for a node."""
        node = self._data["nodes"].get(node_id)
        if node is None:
            return None
        queue = node.setdefault("pending_commands", [])
        if not queue:
            return None
        command = queue.pop(0)
        await self._store.async_save(self._data)
        return deepcopy(command)

    def is_maintenance_enabled(self, node_id: str) -> bool:
        """Return whether maintenance mode is currently enabled for a node."""
        node = self._data["nodes"].get(node_id)
        if node is None:
            return False
        until = node.get("maintenance", {}).get("enabled_until")
        if not isinstance(until, str):
            return False
        return _parse_datetime(until) > datetime.now(UTC)

    def get_maintenance_enabled_until(self, node_id: str) -> str | None:
        """Return the maintenance mode expiration timestamp."""
        node = self._data["nodes"].get(node_id)
        if node is None:
            return None
        until = node.get("maintenance", {}).get("enabled_until")
        return until if isinstance(until, str) else None

    async def async_enable_maintenance(self, node_id: str) -> str | None:
        """Enable maintenance mode for a limited time."""
        node = self._data["nodes"].get(node_id)
        if node is None:
            return None
        enabled_until = (datetime.now(UTC) + timedelta(seconds=MAINTENANCE_MODE_SECONDS)).isoformat()
        node["maintenance"] = {"enabled_until": enabled_until}
        await self._store.async_save(self._data)
        return enabled_until

    def get_recent_command_runs(self, node_id: str) -> list[dict[str, Any]]:
        """Return recent command runs for display in the maintenance UI."""
        node = self._data["nodes"].get(node_id)
        if node is None:
            return []
        runs = list(node.get("command_runs", {}).values())
        runs.sort(key=lambda run: run.get("created_at", ""), reverse=True)
        return deepcopy(runs[:MAX_COMMAND_RUNS_PER_NODE])

    async def async_create_command_run(
        self, node_id: str, command_type: str, *, params: dict[str, Any] | None = None
    ) -> dict[str, Any] | None:
        """Create a command run and enqueue it for the node agent."""
        node = self._data["nodes"].get(node_id)
        if node is None:
            return None
        now = datetime.now(UTC).isoformat()
        run_id = uuid.uuid4().hex
        command_id = uuid.uuid4().hex
        run = {
            "id": run_id,
            "command_id": command_id,
            "command_type": command_type,
            "status": "queued",
            "created_at": now,
            "updated_at": now,
            "finished_at": None,
            "returncode": None,
        }
        node.setdefault("command_runs", {})[run_id] = run
        self._prune_command_runs(node)
        node.setdefault("pending_commands", []).append(
            {
                "id": command_id,
                "run_id": run_id,
                "type": command_type,
                "requested_at": now,
                **(params or {}),
            }
        )
        await self._store.async_save(self._data)
        return deepcopy(run)

    async def async_update_command_run(
        self,
        node_id: str,
        run_id: str,
        *,
        status: str | None = None,
        returncode: int | None = None,
        finished: bool = False,
    ) -> dict[str, Any] | None:
        """Update stored command run metadata from the agent."""
        node = self._data["nodes"].get(node_id)
        if node is None:
            return None
        run = node.setdefault("command_runs", {}).get(run_id)
        if run is None:
            return None
        now = datetime.now(UTC).isoformat()
        if status is not None:
            run["status"] = status
        if returncode is not None:
            run["returncode"] = returncode
        if finished:
            run["finished_at"] = now
        run["updated_at"] = now
        await self._store.async_save(self._data)
        return deepcopy(run)

    def get_command_run(self, node_id: str, run_id: str) -> dict[str, Any] | None:
        """Return a retained command run by id."""
        node = self._data["nodes"].get(node_id)
        if node is None:
            return None
        run = node.setdefault("command_runs", {}).get(run_id)
        return deepcopy(run) if run is not None else None

    async def async_delete_node(self, node_id: str) -> None:
        """Delete a node and all stored state."""
        if node_id in self._data["nodes"]:
            del self._data["nodes"][node_id]
            await self._store.async_save(self._data)

    def _prune_command_runs(self, node: dict[str, Any]) -> None:
        runs = node.setdefault("command_runs", {})
        ordered = sorted(runs.values(), key=lambda run: run.get("created_at", ""), reverse=True)
        keep: set[str] = set()
        per_command: dict[str, int] = {}
        for run in ordered:
            command_type = run.get("command_type")
            count = per_command.get(command_type, 0)
            if count >= MAX_COMMAND_RUNS_PER_COMMAND:
                continue
            if len(keep) >= MAX_COMMAND_RUNS_PER_NODE:
                continue
            keep.add(run["id"])
            per_command[command_type] = count + 1
        for run_id in list(runs):
            if run_id not in keep:
                del runs[run_id]

    def _sanitize_command_runs(self) -> bool:
        changed = False
        for node in self._data.get("nodes", {}).values():
            for run in node.setdefault("command_runs", {}).values():
                if "output" in run:
                    run.pop("output", None)
                    changed = True
            before = set(node.setdefault("command_runs", {}))
            self._prune_command_runs(node)
            if before != set(node.setdefault("command_runs", {})):
                changed = True
        return changed


async def async_ensure_storage(hass: HomeAssistant) -> HostWatchStorage:
    """Create storage lazily when called outside normal setup order."""
    hass.data.setdefault(DOMAIN, {})
    storage = hass.data[DOMAIN].get("storage")
    if storage is None:
        storage = HostWatchStorage(hass)
        await storage.async_load()
        hass.data[DOMAIN]["storage"] = storage
    return storage


def get_storage(hass: HomeAssistant) -> HostWatchStorage:
    """Get the shared storage instance."""
    return hass.data[DOMAIN]["storage"]


def _deep_merge(target: dict[str, Any], patch: dict[str, Any]) -> None:
    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(target.get(key), dict):
            _deep_merge(target[key], value)
            continue
        target[key] = value


def _parse_datetime(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return datetime.fromtimestamp(0, UTC)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)
