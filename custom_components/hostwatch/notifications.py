"""HostWatch summary notifications."""

from __future__ import annotations

import json
import logging
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Any

from homeassistant.components import persistent_notification
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from .storage import get_storage

TRANSLATIONS_DIR = Path(__file__).with_name("notification_translations")
_LOGGER = logging.getLogger(__name__)
DEFAULT_NOTIFICATION_TRANSLATIONS = {
    "unknown": "unknown",
    "apt_summary_title": "HostWatch APT Summary",
    "bootloader_summary_title": "HostWatch Raspberry Pi Bootloader Updates",
    "updates_label": "Updates",
    "last_check_label": "Last check",
    "track_label": "Track",
    "pending_updates_label": "Pending updates",
    "latest_release_label": "Latest release",
    "notes_label": "Notes",
    "no_release_notes": "No release notes available.",
}


def async_send_apt_summary(hass: HomeAssistant) -> None:
    """Create the HostWatch APT summary notification immediately."""
    t = _translator(hass)
    storage = get_storage(hass)
    active_node_ids = {entry.data.get("node_id") for entry in hass.config_entries.async_entries("hostwatch")}
    nodes = [node for node in storage.iter_nodes() if node.get("node_id") in active_node_ids]
    if not nodes:
        return
    nodes = sorted(nodes, key=_node_sort_key)

    apt_lines: list[str] = []

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

    bootloader_sections: list[str] = []

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

    if bootloader_sections:
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
    merged = dict(DEFAULT_NOTIFICATION_TRANSLATIONS)
    merged.update(_load_notification_translations("en"))
    language = _language(hass)
    if language != "en":
        merged.update(_load_notification_translations(language))

    def translate(key: str) -> str:
        return merged.get(key, key)

    return translate


def _node_sort_key(node: dict[str, Any]) -> str:
    node_name = node.get("node_name") or node.get("node_id") or ""
    return str(node_name).lower()


def validate_notification_translations() -> None:
    """Log configuration issues for custom notification translations once during setup."""
    _load_notification_translations.cache_clear()
    en = _load_notification_translations("en")
    if not en:
        _LOGGER.warning(
            "HostWatch notification translations for 'en' could not be loaded from %s; using built-in defaults",
            TRANSLATIONS_DIR / "en.json",
        )


@lru_cache(maxsize=8)
def _load_notification_translations(language: str) -> dict[str, str]:
    path = TRANSLATIONS_DIR / f"{language}.json"
    if not path.exists():
        _LOGGER.warning("HostWatch notification translation file not found: %s", path)
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        _LOGGER.warning("Failed to read HostWatch notification translation file %s: %s", path, exc)
        return {}
    except ValueError as exc:
        _LOGGER.warning("Failed to parse HostWatch notification translation file %s: %s", path, exc)
        return {}
    if not isinstance(data, dict):
        _LOGGER.warning("Invalid HostWatch notification translation structure in %s: expected top-level object", path)
        return {}
    return {str(key): str(value) for key, value in data.items()}
