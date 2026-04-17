"""Device registry helpers for HostWatch."""

from __future__ import annotations

from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.network import NoURLAvailableError, get_url

from .const import DOMAIN


def hostwatch_device_info(hass: HomeAssistant, node: dict[str, Any]) -> DeviceInfo:
    """Build shared HostWatch device metadata."""
    node_id = node["node_id"]
    platform = node.get("platform", {})
    raspberry_model = platform.get("raspberry_model")
    cpu_model = platform.get("cpuModel")
    cpu_cores = platform.get("cpuCores")
    if raspberry_model:
        model = "Raspberry Pi"
        hw_version = raspberry_model
    elif cpu_model and cpu_cores:
        model = platform.get("os", "Linux Host")
        hw_version = f"{cpu_model} ({cpu_cores} cores)"
    else:
        model = platform.get("os", "Linux Host")
        hw_version = cpu_model
    return DeviceInfo(
        identifiers={(DOMAIN, node_id)},
        name=f"HostWatch {node.get('node_name', node_id)}",
        manufacturer="HostWatch",
        model=model,
        sw_version=platform.get("osRelease"),
        hw_version=hw_version,
        configuration_url=_maintenance_url(hass, node_id),
    )


def _maintenance_url(hass: HomeAssistant, node_id: str) -> str:
    path = f"/hostwatch/maintenance/{node_id}"
    try:
        return f"{get_url(hass, allow_internal=True, allow_external=True, prefer_external=True)}{path}"
    except NoURLAvailableError:
        return path
