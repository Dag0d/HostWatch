"""HostWatch summary notifications."""

from __future__ import annotations

import json
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Any

from homeassistant.components import persistent_notification
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from .storage import get_storage

TRANSLATIONS_DIR = Path(__file__).with_name("notification_translations")


def async_send_apt_summary(hass: HomeAssistant) -> None:
    """Create the HostWatch APT summary notification immediately."""
    t = _translator(hass)
    storage = get_storage(hass)
    active_node_ids = {entry.data.get("node_id") for entry in hass.config_entries.async_entries("hostwatch")}
    nodes = [node for node in storage.iter_nodes() if node.get("node_id") in active_node_ids]
    if not nodes:
        return
    nodes = sorted(nodes, key=_node_sort_key)

    apt_lines: list[str] = [f"## {t('apt_summary_heading')}", ""]

    for node in nodes:
        node_name = node.get("node_name", node.get("node_id", t("unknown")))
        metrics = node.get("metrics", {})
        updates = metrics.get("updates", {}).get("apt", {})
        upgradable = updates.get("upgradable_count")
        checked_at = _format_timestamp(hass, updates.get("checked_at"))
        update_count = upgradable if upgradable is not None else t("unknown")
        apt_lines.append(
            "\n".join(
                [
                    f"### {node_name}",
                    f"- **{t('updates_label')}**: {update_count}",
                    f"- **{t('last_check_label')}**: {checked_at}",
                ]
            )
        )
        apt_lines.append("")

    persistent_notification.async_create(
        hass,
        "\n".join(apt_lines).strip(),
        title=t("apt_summary_title"),
        notification_id="hostwatch_apt_summary",
    )


def async_send_bootloader_summary(hass: HomeAssistant) -> None:
    """Create the HostWatch Raspberry Pi bootloader summary notification immediately."""
    t = _translator(hass)
    storage = get_storage(hass)
    active_node_ids = {entry.data.get("node_id") for entry in hass.config_entries.async_entries("hostwatch")}
    nodes = [node for node in storage.iter_nodes() if node.get("node_id") in active_node_ids]
    if not nodes:
        return
    nodes = sorted(nodes, key=_node_sort_key)

    bootloader_sections: list[str] = [f"## {t('bootloader_summary_heading')}", ""]

    for node in nodes:
        node_name = node.get("node_name", node.get("node_id", t("unknown")))
        metrics = node.get("metrics", {})
        bootloader = metrics.get("bootloader", {})
        if bootloader.get("pending_count", 0) > 0:
            bootloader_sections.append(
                "\n".join(
                    [
                        f"### {node_name}",
                        f"- **{t('track_label')}**: {bootloader.get('track') or t('unknown')}",
                        f"- **{t('pending_updates_label')}**: {bootloader.get('pending_count')}",
                        f"- **{t('latest_release_label')}**: {bootloader.get('version') or t('unknown')}",
                        f"- **{t('notes_label')}**:",
                        bootloader.get("notes") or t("no_release_notes"),
                    ]
                )
            )
            bootloader_sections.append("")

    if len(bootloader_sections) > 2:
        persistent_notification.async_create(
            hass,
            "\n".join(bootloader_sections).strip(),
            title=t("bootloader_summary_title"),
            notification_id="hostwatch_bootloader_summary",
        )


def _format_timestamp(hass: HomeAssistant, value: str | None) -> str:
    t = _translator(hass)
    if not value:
        return t("unknown")
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return value
    local_dt = dt_util.as_local(dt)
    language = _language(hass)
    if language == "de":
        return local_dt.strftime("%d.%m.%Y %H:%M")
    return local_dt.strftime("%Y-%m-%d %H:%M")


def _language(hass: HomeAssistant) -> str:
    language = getattr(hass.config, "language", None) or "en"
    normalized = str(language).replace("-", "_").lower()
    if normalized.startswith("de"):
        return "de"
    return "en"


def _translator(hass: HomeAssistant):
    merged = dict(_load_notification_translations("en"))
    language = _language(hass)
    if language != "en":
        merged.update(_load_notification_translations(language))

    def translate(key: str) -> str:
        return merged.get(key, key)

    return translate


def _node_sort_key(node: dict[str, Any]) -> str:
    node_name = node.get("node_name") or node.get("node_id") or ""
    return str(node_name).lower()


@lru_cache(maxsize=8)
def _load_notification_translations(language: str) -> dict[str, str]:
    path = TRANSLATIONS_DIR / f"{language}.json"
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    notifications = data.get("notifications", {})
    return notifications if isinstance(notifications, dict) else {}
