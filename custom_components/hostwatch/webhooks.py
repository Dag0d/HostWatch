"""Webhook handlers used by paired HostWatch agents."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from aiohttp import web
from aiohttp.hdrs import METH_POST
from homeassistant.components import webhook
from homeassistant.core import HomeAssistant

from .const import DOMAIN
from .maintenance import async_notify_command_run_updated
from .runtime import get_runtime
from .storage import get_storage


def _iso_now() -> str:
    return datetime.now(UTC).isoformat()


def _authorize(node: dict[str, Any], payload: dict[str, Any]) -> bool:
    secret = payload.get("node_secret")
    return isinstance(secret, str) and secret == node.get("node_secret")


async def _handle_heartbeat(
    hass: HomeAssistant, node: dict[str, Any], payload: dict[str, Any]
) -> web.Response:
    if not _authorize(node, payload):
        return web.json_response({"error": "unauthorized"}, status=401)

    now = _iso_now()
    await get_storage(hass).async_update_status(node["node_id"], online=True, last_seen=now)
    get_runtime(hass).update_node(
        node["node_id"],
        {
            "online": True,
            "last_seen": now,
        },
    )
    return web.json_response({"status": "ok"})


async def _handle_metrics(
    hass: HomeAssistant, node: dict[str, Any], payload: dict[str, Any]
) -> web.Response:
    if not _authorize(node, payload):
        return web.json_response({"error": "unauthorized"}, status=401)

    now = _iso_now()
    patch = {
        "online": True,
        "last_seen": now,
        "metrics": payload.get("metrics", {}),
        "platform": payload.get("platform", {}),
        "agent_version": payload.get("agent_version"),
    }
    await get_storage(hass).async_patch_node(node["node_id"], patch)
    get_runtime(hass).update_node(node["node_id"], patch)
    return web.json_response(
        {
            "status": "accepted",
            "received_metrics": list(payload.get("metrics", {}).keys()),
        }
    )


async def _handle_command_result(
    hass: HomeAssistant, node: dict[str, Any], payload: dict[str, Any]
) -> web.Response:
    if not _authorize(node, payload):
        return web.json_response({"error": "unauthorized"}, status=401)

    now = _iso_now()
    run_id = payload.get("run_id")
    if isinstance(run_id, str):
        status = payload.get("status")
        event = payload.get("event")
        if event == "started":
            status = "running"
        elif event == "chunk":
            status = "running"
        elif event == "finished":
            status = status or "completed"
        output = payload.get("output")
        if not isinstance(output, str):
            output = payload.get("message")
        if isinstance(output, str):
            if event == "output_snapshot":
                get_runtime(hass).set_command_output(node["node_id"], run_id, output)
            else:
                get_runtime(hass).append_command_output(node["node_id"], run_id, output)
        if event != "output_snapshot":
            await get_storage(hass).async_update_command_run(
                node["node_id"],
                run_id,
                status=status if isinstance(status, str) else None,
                returncode=payload.get("returncode") if isinstance(payload.get("returncode"), int) else None,
                finished=event == "finished",
            )
        async_notify_command_run_updated(hass, node["node_id"])
    await get_storage(hass).async_update_status(node["node_id"], online=True, last_seen=now)
    get_runtime(hass).update_node(
        node["node_id"],
        {
            "online": True,
            "last_seen": now,
        },
    )
    return web.json_response({"status": "accepted"})


async def _handle_command_poll(
    hass: HomeAssistant, node: dict[str, Any], payload: dict[str, Any]
) -> web.Response:
    if not _authorize(node, payload):
        return web.json_response({"error": "unauthorized"}, status=401)
    command = await get_storage(hass).async_pop_next_command(node["node_id"])
    return web.json_response({"command": command})


async def _handle_webhook(
    hass: HomeAssistant, webhook_id: str, request: web.Request
) -> web.Response:
    node = get_storage(hass).get_node_by_webhook_id(webhook_id)
    if node is None:
        return web.json_response({"error": "unknown_webhook"}, status=404)

    payload = await request.json()
    if webhook_id == node.get("heartbeat_webhook_id"):
        return await _handle_heartbeat(hass, node, payload)
    if webhook_id == node.get("metrics_webhook_id"):
        return await _handle_metrics(hass, node, payload)
    if webhook_id == node.get("command_result_webhook_id"):
        return await _handle_command_result(hass, node, payload)
    if webhook_id == node.get("command_poll_webhook_id"):
        return await _handle_command_poll(hass, node, payload)
    return web.json_response({"error": "unknown_webhook"}, status=404)


def _ensure_registry(hass: HomeAssistant) -> set[str]:
    hass.data.setdefault(DOMAIN, {})
    return hass.data[DOMAIN].setdefault("registered_webhooks", set())


async def async_register_node_webhooks(hass: HomeAssistant, node: dict[str, Any]) -> None:
    """Register webhook handlers for a node."""
    registered = _ensure_registry(hass)
    for key, name in (
        ("heartbeat_webhook_id", "Heartbeat"),
        ("metrics_webhook_id", "Metrics"),
        ("command_result_webhook_id", "Command Result"),
        ("command_poll_webhook_id", "Command Poll"),
    ):
        webhook_id = node.get(key)
        if not webhook_id or webhook_id in registered:
            continue
        webhook.async_register(
            hass,
            DOMAIN,
            f"HostWatch {name} {node['node_id']}",
            webhook_id,
            _handle_webhook,
            allowed_methods=(METH_POST,),
        )
        registered.add(webhook_id)


async def async_unregister_node_webhooks(hass: HomeAssistant, node: dict[str, Any]) -> None:
    """Unregister webhook handlers for a node."""
    registered = _ensure_registry(hass)
    for key in (
        "heartbeat_webhook_id",
        "metrics_webhook_id",
        "command_result_webhook_id",
        "command_poll_webhook_id",
    ):
        webhook_id = node.get(key)
        if not webhook_id or webhook_id not in registered:
            continue
        webhook.async_unregister(hass, webhook_id)
        registered.discard(webhook_id)


async def async_setup_webhooks(hass: HomeAssistant) -> None:
    """Register webhook handlers for all stored nodes."""
    storage = get_storage(hass)
    for node in storage.iter_nodes():
        await async_register_node_webhooks(hass, node)
