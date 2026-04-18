"""HostWatch summary notifications."""

from __future__ import annotations

from datetime import datetime

from homeassistant.components import persistent_notification
from homeassistant.core import HomeAssistant

from .storage import get_storage


def async_send_apt_summary(hass: HomeAssistant) -> None:
    """Create the HostWatch APT summary notification immediately."""
    storage = get_storage(hass)
    active_node_ids = {entry.data.get("node_id") for entry in hass.config_entries.async_entries("hostwatch")}
    nodes = [node for node in storage.iter_nodes() if node.get("node_id") in active_node_ids]
    if not nodes:
        return

    apt_lines: list[str] = []

    for node in nodes:
        node_name = node.get("node_name", node.get("node_id", "unknown"))
        metrics = node.get("metrics", {})
        updates = metrics.get("updates", {}).get("apt", {})
        upgradable = updates.get("upgradable_count")
        checked_at = _format_timestamp(updates.get("checked_at"))
        apt_lines.append(
            f"- {node_name}: {upgradable if upgradable is not None else 'unknown'} updates (last check: {checked_at})"
        )

    persistent_notification.async_create(
        hass,
        "## HostWatch APT Summary\n" + "\n".join(apt_lines),
        title="HostWatch APT Summary",
        notification_id="hostwatch_apt_summary",
    )

def async_send_bootloader_summary(hass: HomeAssistant) -> None:
    """Create the HostWatch Raspberry Pi bootloader summary notification immediately."""
    storage = get_storage(hass)
    active_node_ids = {entry.data.get("node_id") for entry in hass.config_entries.async_entries("hostwatch")}
    nodes = [node for node in storage.iter_nodes() if node.get("node_id") in active_node_ids]
    if not nodes:
        return

    bootloader_sections: list[str] = []

    for node in nodes:
        node_name = node.get("node_name", node.get("node_id", "unknown"))
        metrics = node.get("metrics", {})
        bootloader = metrics.get("bootloader", {})
        if bootloader.get("pending_count", 0) > 0:
            bootloader_sections.append(
                "\n".join(
                    [
                        f"### {node_name}",
                        f"- Track: {bootloader.get('track') or 'unknown'}",
                        f"- Pending updates: {bootloader.get('pending_count')}",
                        f"- Latest release: {bootloader.get('version') or 'unknown'}",
                        f"- Notes:\n{bootloader.get('notes') or 'No release notes available.'}",
                    ]
                )
            )

    if bootloader_sections:
        persistent_notification.async_create(
            hass,
            "## HostWatch Raspberry Pi Bootloader Updates\n\n" + "\n\n".join(bootloader_sections),
            title="HostWatch Raspberry Pi Bootloader Updates",
            notification_id="hostwatch_bootloader_summary",
        )


def _format_timestamp(value: str | None) -> str:
    if not value:
        return "unknown"
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return value
    return dt.strftime("%Y-%m-%d %H:%M")
