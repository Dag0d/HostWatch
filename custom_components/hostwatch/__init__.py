"""HostWatch integration setup."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import ATTR_DEVICE_ID
from homeassistant.core import HomeAssistant, ServiceCall, ServiceResponse, SupportsResponse
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.event import async_track_time_interval

from .const import (
    DOMAIN,
    NODE_OFFLINE_AFTER_SECONDS,
    PLATFORMS,
    SERVICE_REFRESH_AGENT_UPDATES,
    SERVICE_GET_APT_SUMMARY,
    SERVICE_GET_BOOTLOADER_SUMMARY,
)
from .maintenance import async_setup_maintenance
from .notifications import (
    async_validate_notification_translations,
    get_apt_summary,
    get_bootloader_summary,
)
from .release import async_setup_release_manager, get_release_manager
from .runtime import HostWatchRuntime, get_runtime
from .storage import async_ensure_storage, get_storage
from .webhooks import async_register_node_webhooks, async_setup_webhooks, async_unregister_node_webhooks

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)
SUMMARY_SERVICE_SCHEMA = vol.Schema(
    {
        vol.Optional(ATTR_DEVICE_ID): vol.Any(cv.ensure_list, [str]),
        vol.Optional("create_notification", default=True): cv.boolean,
        vol.Optional("include_raw", default=False): cv.boolean,
    }
)


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    """Set up HostWatch domain."""
    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN].setdefault("runtime", HostWatchRuntime(hass))
    await async_ensure_storage(hass)
    await async_setup_webhooks(hass)
    await async_setup_maintenance(hass)
    await async_setup_release_manager(hass)
    await async_validate_notification_translations(hass)
    if "stale_nodes_unsub" not in hass.data[DOMAIN]:
        async def handle_stale_nodes(now: datetime) -> None:
            await _async_mark_stale_nodes(hass, now)

        hass.data[DOMAIN]["stale_nodes_unsub"] = async_track_time_interval(
            hass,
            handle_stale_nodes,
            timedelta(seconds=15),
        )
    if not hass.services.has_service(DOMAIN, SERVICE_GET_APT_SUMMARY):
        async def handle_get_apt_summary(call: ServiceCall) -> ServiceResponse:
            response = get_apt_summary(
                hass,
                device_ids=_device_ids_from_call(call),
                include_raw=bool(call.data.get("include_raw", False)),
                create_notification=bool(call.data.get("create_notification", True)),
            )
            return response if call.return_response else None

        hass.services.async_register(
            DOMAIN,
            SERVICE_GET_APT_SUMMARY,
            handle_get_apt_summary,
            schema=SUMMARY_SERVICE_SCHEMA,
            supports_response=SupportsResponse.OPTIONAL,
        )
    if not hass.services.has_service(DOMAIN, SERVICE_GET_BOOTLOADER_SUMMARY):
        async def handle_get_bootloader_summary(call: ServiceCall) -> ServiceResponse:
            response = get_bootloader_summary(
                hass,
                device_ids=_device_ids_from_call(call),
                include_raw=bool(call.data.get("include_raw", False)),
                create_notification=bool(call.data.get("create_notification", True)),
            )
            return response if call.return_response else None

        hass.services.async_register(
            DOMAIN,
            SERVICE_GET_BOOTLOADER_SUMMARY,
            handle_get_bootloader_summary,
            schema=SUMMARY_SERVICE_SCHEMA,
            supports_response=SupportsResponse.OPTIONAL,
        )
    if not hass.services.has_service(DOMAIN, SERVICE_REFRESH_AGENT_UPDATES):
        async def handle_refresh_agent_updates(_call) -> None:
            await get_release_manager(hass).async_refresh()

        hass.services.async_register(
            DOMAIN,
            SERVICE_REFRESH_AGENT_UPDATES,
            handle_refresh_agent_updates,
        )
    return True


def _device_ids_from_call(call: ServiceCall) -> list[str] | None:
    device_ids = call.data.get(ATTR_DEVICE_ID)
    if not device_ids:
        return None
    if isinstance(device_ids, str):
        return [device_ids]
    return [str(device_id) for device_id in device_ids]


async def _async_mark_stale_nodes(hass: HomeAssistant, now: datetime) -> None:
    """Mark nodes offline if no data has arrived within the expected window."""
    storage = get_storage(hass)
    cutoff = now.astimezone(UTC) - timedelta(seconds=NODE_OFFLINE_AFTER_SECONDS)
    for node in storage.iter_nodes():
        if node.get("online") is not True:
            continue
        if _parse_timestamp(node.get("last_seen")) >= cutoff:
            continue
        await storage.async_update_status(node["node_id"], online=False)
        get_runtime(hass).update_node(node["node_id"], {"online": False})


def _parse_timestamp(value: Any) -> datetime:
    if not isinstance(value, str):
        return datetime.fromtimestamp(0, UTC)
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return datetime.fromtimestamp(0, UTC)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up a HostWatch config entry."""
    node = get_storage(hass).get_node(entry.data["node_id"])
    if node is not None:
        get_runtime(hass).update_node(entry.data["node_id"], node)
        await async_register_node_webhooks(hass, node)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a HostWatch config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        node = get_storage(hass).get_node(entry.data["node_id"])
        if node is not None:
            await async_unregister_node_webhooks(hass, node)
    return unload_ok


async def async_remove_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Remove a HostWatch config entry and its stored node data."""
    node = get_storage(hass).get_node(entry.data["node_id"])
    if node is not None:
        await async_unregister_node_webhooks(hass, node)
    await get_storage(hass).async_delete_node(entry.data["node_id"])
    get_runtime(hass).remove_node(entry.data["node_id"])
