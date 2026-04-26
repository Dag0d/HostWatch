"""HostWatch summary actions and notifications."""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

from homeassistant.components import persistent_notification
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import device_registry as dr
from homeassistant.util import dt as dt_util

from .const import DOMAIN
from .storage import get_storage

TRANSLATIONS_DIR = Path(__file__).with_name("notification_translations")
_LOGGER = logging.getLogger(__name__)
_NOTIFICATION_TRANSLATIONS: dict[str, dict[str, str]] = {}
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


async def async_validate_notification_translations(hass: HomeAssistant) -> None:
    """Preload notification translations without blocking the event loop."""
    _NOTIFICATION_TRANSLATIONS.clear()
    en = await _async_load_notification_translations(hass, "en")
    if not en:
        _LOGGER.warning(
            "HostWatch notification translations for 'en' could not be loaded from %s; using built-in defaults",
            TRANSLATIONS_DIR / "en.json",
        )
    language = _normalize_language(getattr(hass.config, "language", None) or "en")
    if language != "en":
        await _async_load_notification_translations(hass, language)


def get_apt_summary(
    hass: HomeAssistant,
    *,
    device_ids: list[str] | None = None,
    include_raw: bool = False,
    create_notification: bool = True,
) -> dict[str, Any]:
    """Build an APT summary response and optionally create a translated notification."""
    nodes = _get_target_nodes(hass, device_ids)
    generated_at = dt_util.now()
    response_nodes: list[dict[str, Any]] = []
    total_updates = 0
    nodes_with_updates = 0
    nodes_up_to_date = 0
    nodes_unknown = 0

    for node in nodes:
        node_name = node.get("node_name", node.get("node_id", "unknown"))
        updates = node.get("metrics", {}).get("updates", {}).get("apt", {})
        upgradable = updates.get("upgradable_count")
        has_updates = bool(isinstance(upgradable, int) and upgradable > 0)
        state = "unknown"
        if isinstance(upgradable, int):
            total_updates += upgradable
            if upgradable > 0:
                nodes_with_updates += 1
                state = "updates_available"
            else:
                nodes_up_to_date += 1
                state = "up_to_date"
        else:
            nodes_unknown += 1
        response_nodes.append(
            {
                "node_id": node.get("node_id"),
                "node_name": node_name,
                "state": state,
                "has_updates": has_updates,
                "updates_available": upgradable,
                "last_check": _iso_local_timestamp(updates.get("checked_at")),
            }
        )

    response: dict[str, Any] = {
        "summary_type": "apt",
        "generated_at": generated_at.isoformat(),
        "node_count": len(response_nodes),
        "nodes_with_updates": nodes_with_updates,
        "nodes_up_to_date": nodes_up_to_date,
        "nodes_unknown": nodes_unknown,
        "total_updates": total_updates,
        "nodes": response_nodes,
    }
    if include_raw:
        response["raw"] = _render_apt_raw(response_nodes)
    if create_notification:
        _create_apt_notification(hass, response_nodes)
    return response


def get_bootloader_summary(
    hass: HomeAssistant,
    *,
    device_ids: list[str] | None = None,
    include_raw: bool = False,
    create_notification: bool = True,
) -> dict[str, Any]:
    """Build a bootloader summary response and optionally create a translated notification."""
    nodes = _get_target_nodes(hass, device_ids)
    generated_at = dt_util.now()
    response_nodes: list[dict[str, Any]] = []
    total_pending_updates = 0
    nodes_with_updates = 0
    nodes_up_to_date = 0
    nodes_unknown = 0
    unsupported_nodes = 0

    for node in nodes:
        node_name = node.get("node_name", node.get("node_id", "unknown"))
        bootloader = node.get("metrics", {}).get("bootloader", {})
        supported = bool(bootloader.get("supported"))
        pending_count = bootloader.get("pending_count")
        status = bootloader.get("status") or "unknown"
        has_updates = bool(isinstance(pending_count, int) and pending_count > 0)

        if not supported:
            unsupported_nodes += 1
            state = "unsupported"
        elif isinstance(pending_count, int):
            total_pending_updates += pending_count
            if pending_count > 0:
                nodes_with_updates += 1
                state = "updates_available"
            elif status in {"up_to_date", "reboot_required"}:
                nodes_up_to_date += 1
                state = status
            else:
                nodes_unknown += 1
                state = status or "unknown"
        else:
            nodes_unknown += 1
            state = status or "unknown"

        response_nodes.append(
            {
                "node_id": node.get("node_id"),
                "node_name": node_name,
                "state": state,
                "supported": supported,
                "has_updates": has_updates,
                "pending_count": pending_count,
                "last_check": _iso_local_timestamp(bootloader.get("checked_at")),
                "track": bootloader.get("track"),
                "latest_release": bootloader.get("version"),
                "notes": bootloader.get("notes"),
            }
        )

    response: dict[str, Any] = {
        "summary_type": "bootloader",
        "generated_at": generated_at.isoformat(),
        "node_count": len(response_nodes),
        "supported_node_count": len(response_nodes) - unsupported_nodes,
        "unsupported_nodes": unsupported_nodes,
        "nodes_with_updates": nodes_with_updates,
        "nodes_up_to_date": nodes_up_to_date,
        "nodes_unknown": nodes_unknown,
        "total_pending_updates": total_pending_updates,
        "nodes": response_nodes,
    }
    if include_raw:
        response["raw"] = _render_bootloader_raw(response_nodes)
    if create_notification:
        _create_bootloader_notification(hass, response_nodes)
    return response


def _get_target_nodes(hass: HomeAssistant, device_ids: list[str] | None) -> list[dict[str, Any]]:
    storage = get_storage(hass)
    active_node_ids = {entry.data.get("node_id") for entry in hass.config_entries.async_entries(DOMAIN)}
    all_nodes = [node for node in storage.iter_nodes() if node.get("node_id") in active_node_ids]
    all_nodes = sorted(all_nodes, key=_node_sort_key)

    if not device_ids:
        return all_nodes

    device_registry = dr.async_get(hass)
    selected_node_ids: set[str] = set()
    for device_id in device_ids:
        device = device_registry.async_get(device_id)
        if device is None:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="unknown_device_id",
                translation_placeholders={"device_id": device_id},
            )
        for domain, identifier in device.identifiers:
            if domain == DOMAIN:
                selected_node_ids.add(identifier)
        if not any(domain == DOMAIN for domain, _identifier in device.identifiers):
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="device_not_hostwatch",
                translation_placeholders={"device_id": device_id},
            )

    nodes = [node for node in all_nodes if node.get("node_id") in selected_node_ids]
    if not nodes:
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="no_matching_hostwatch_nodes",
        )
    return nodes


def _create_apt_notification(hass: HomeAssistant, response_nodes: list[dict[str, Any]]) -> None:
    t = _translator(hass)
    lines: list[str] = []
    for node in response_nodes:
        lines.append(
            "\n".join(
                [
                    f"### {node['node_name']}",
                    f"- **{t('updates_label')}**: {_human_value(node.get('updates_available'), t)}",
                    f"- **{t('last_check_label')}**: {_human_value(node.get('last_check'), t)}",
                ]
            )
        )
        lines.append("")

    persistent_notification.async_create(
        hass,
        "\n".join(lines).strip(),
        title=t("apt_summary_title"),
        notification_id="hostwatch_apt_summary",
    )


def _create_bootloader_notification(hass: HomeAssistant, response_nodes: list[dict[str, Any]]) -> None:
    t = _translator(hass)
    sections: list[str] = []
    for node in response_nodes:
        if not node.get("has_updates"):
            continue
        sections.append(
            "\n".join(
                [
                    f"### {node['node_name']}",
                    f"- **{t('track_label')}**: {_human_value(node.get('track'), t)}",
                    f"- **{t('pending_updates_label')}**: {_human_value(node.get('pending_count'), t)}",
                    f"- **{t('latest_release_label')}**: {_human_value(node.get('latest_release'), t)}",
                    f"- **{t('notes_label')}**:",
                    str(node.get("notes") or t("no_release_notes")),
                ]
            )
        )
        sections.append("")

    if sections:
        persistent_notification.async_create(
            hass,
            "\n".join(sections).strip(),
            title=t("bootloader_summary_title"),
            notification_id="hostwatch_bootloader_summary",
        )


def _render_apt_raw(nodes: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    for node in nodes:
        lines.append(
            "\n".join(
                [
                    f"### {node['node_name']}",
                    f"- **Updates**: {_raw_value(node.get('updates_available'))}",
                    f"- **Last check**: {_raw_value(node.get('last_check'))}",
                ]
            )
        )
        lines.append("")
    return "\n".join(lines).strip()


def _render_bootloader_raw(nodes: list[dict[str, Any]]) -> str:
    sections: list[str] = []
    for node in nodes:
        sections.append(
            "\n".join(
                [
                    f"### {node['node_name']}",
                    f"- **State**: {_raw_value(node.get('state'))}",
                    f"- **Supported**: {'true' if node.get('supported') else 'false'}",
                    f"- **Pending updates**: {_raw_value(node.get('pending_count'))}",
                    f"- **Last check**: {_raw_value(node.get('last_check'))}",
                    f"- **Track**: {_raw_value(node.get('track'))}",
                    f"- **Latest release**: {_raw_value(node.get('latest_release'))}",
                    f"- **Notes**:",
                    str(node.get("notes") or "No release notes available."),
                ]
            )
        )
        sections.append("")
    return "\n".join(sections).strip()


def _format_timestamp(hass: HomeAssistant, value: str | None) -> str:
    if not value:
        return _translator(hass)("unknown")
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return value
    local_dt = dt_util.as_local(dt)
    language = _language(hass)
    if language == "de":
        return local_dt.strftime("%d.%m.%Y %H:%M")
    return local_dt.strftime("%Y-%m-%d %H:%M")


def _iso_local_timestamp(value: str | None) -> str | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return value
    return dt_util.as_local(dt).isoformat()


def _human_value(value: Any, translator) -> str:
    if value is None or value == "":
        return translator("unknown")
    return str(value)


def _raw_value(value: Any) -> str:
    if value is None or value == "":
        return "unknown"
    return str(value)


def _language(hass: HomeAssistant) -> str:
    return _normalize_language(getattr(hass.config, "language", None) or "en")


def _translator(hass: HomeAssistant):
    merged = dict(DEFAULT_NOTIFICATION_TRANSLATIONS)
    merged.update(_get_notification_translations("en"))
    language = _language(hass)
    if language != "en":
        merged.update(_get_notification_translations(language))

    def translate(key: str) -> str:
        return merged.get(key, key)

    return translate


def _node_sort_key(node: dict[str, Any]) -> str:
    node_name = node.get("node_name") or node.get("node_id") or ""
    return str(node_name).lower()


def _normalize_language(language: str) -> str:
    normalized = str(language).replace("-", "_").lower()
    if normalized.startswith("de"):
        return "de"
    return "en"


def _get_notification_translations(language: str) -> dict[str, str]:
    return _NOTIFICATION_TRANSLATIONS.get(language, {})


async def _async_load_notification_translations(hass: HomeAssistant, language: str) -> dict[str, str]:
    language = _normalize_language(language)
    cached = _NOTIFICATION_TRANSLATIONS.get(language)
    if cached is not None:
        return cached
    data = await hass.async_add_executor_job(_load_notification_translations_file, language)
    _NOTIFICATION_TRANSLATIONS[language] = data
    return data


def _load_notification_translations_file(language: str) -> dict[str, str]:
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
