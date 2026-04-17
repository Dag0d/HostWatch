"""Scheduled HostWatch summary notifications."""

from __future__ import annotations

from datetime import datetime

from homeassistant.components import persistent_notification
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.event import async_track_time_change

from .storage import get_storage


def async_send_weekly_summary(hass: HomeAssistant) -> None:
    """Create the weekly HostWatch summary notifications immediately."""
    storage = get_storage(hass)
    active_node_ids = {entry.data.get("node_id") for entry in hass.config_entries.async_entries("hostwatch")}
    nodes = [node for node in storage.iter_nodes() if node.get("node_id") in active_node_ids]
    if not nodes:
        return

    apt_lines: list[str] = []
    bootloader_sections: list[str] = []

    for node in nodes:
        node_name = node.get("node_name", node.get("node_id", "unknown"))
        metrics = node.get("metrics", {})
        updates = metrics.get("updates", {}).get("apt", {})
        upgradable = updates.get("upgradable_count")
        checked_at = _format_timestamp(updates.get("checked_at"))
        apt_lines.append(
            f"- {node_name}: {upgradable if upgradable is not None else 'unknown'} updates (last check: {checked_at})"
        )

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

    persistent_notification.async_create(
        hass,
        "## HostWatch APT Weekly Summary\n" + "\n".join(apt_lines),
        title="HostWatch Weekly APT Summary",
        notification_id="hostwatch_weekly_apt_summary",
    )

    if bootloader_sections:
        persistent_notification.async_create(
            hass,
            "## HostWatch Raspberry Pi Bootloader Updates\n\n" + "\n\n".join(bootloader_sections),
            title="HostWatch Raspberry Pi Bootloader Updates",
            notification_id="hostwatch_weekly_bootloader_summary",
        )


@callback
def async_setup_notifications(hass: HomeAssistant):
    """Schedule weekly HostWatch summary notifications."""

    @callback
    def _handle_weekly_summary(now: datetime) -> None:
        if now.weekday() != 6:
            return
        async_send_weekly_summary(hass)

    return async_track_time_change(hass, _handle_weekly_summary, hour=3, minute=0, second=0)


def _format_timestamp(value: str | None) -> str:
    if not value:
        return "unknown"
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return value
    return dt.strftime("%Y-%m-%d %H:%M")
