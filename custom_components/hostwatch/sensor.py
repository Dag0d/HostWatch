"""Sensor platform for HostWatch nodes."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    EntityCategory,
    PERCENTAGE,
    UnitOfInformation,
    UnitOfTemperature,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import SIGNAL_NODE_UPDATED
from .device import hostwatch_device_info
from .entity_ids import suggested_object_id
from .runtime import get_runtime
from .storage import get_storage


@dataclass(frozen=True, kw_only=True)
class HostWatchSensorDescription(SensorEntityDescription):
    value_path: tuple[str, ...]
    convert_bytes_to_gb: bool = False


SENSORS: tuple[HostWatchSensorDescription, ...] = (
    HostWatchSensorDescription(
        key="agent_version",
        translation_key="agent_version",
        name="Agent Version",
        value_path=("agent_version",),
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    HostWatchSensorDescription(
        key="cpu_usage_percent",
        translation_key="cpu_usage_percent",
        name="CPU Usage",
        native_unit_of_measurement=PERCENTAGE,
        value_path=("metrics", "cpu", "usage_percent"),
        state_class=SensorStateClass.MEASUREMENT,
    ),
    HostWatchSensorDescription(
        key="cpu_load_1m",
        translation_key="cpu_load_1m",
        name="CPU Load 1m",
        value_path=("metrics", "cpu", "load_1m"),
        state_class=SensorStateClass.MEASUREMENT,
    ),
    HostWatchSensorDescription(
        key="cpu_load_5m",
        translation_key="cpu_load_5m",
        name="CPU Load 5m",
        value_path=("metrics", "cpu", "load_5m"),
        state_class=SensorStateClass.MEASUREMENT,
    ),
    HostWatchSensorDescription(
        key="cpu_load_15m",
        translation_key="cpu_load_15m",
        name="CPU Load 15m",
        value_path=("metrics", "cpu", "load_15m"),
        state_class=SensorStateClass.MEASUREMENT,
    ),
    HostWatchSensorDescription(
        key="cpu_temperature_c",
        translation_key="cpu_temperature_c",
        name="CPU Temperature",
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        value_path=("metrics", "temperature", "cpu_celsius"),
        entity_category=EntityCategory.DIAGNOSTIC,
        suggested_display_precision=1,
    ),
    HostWatchSensorDescription(
        key="memory_used_percent",
        translation_key="memory_used_percent",
        name="Memory Used",
        native_unit_of_measurement=PERCENTAGE,
        value_path=("metrics", "memory", "used_percent"),
    ),
    HostWatchSensorDescription(
        key="memory_total_bytes",
        translation_key="memory_total_bytes",
        name="Memory Total",
        native_unit_of_measurement=UnitOfInformation.GIGABYTES,
        value_path=("metrics", "memory", "total_bytes"),
        entity_category=EntityCategory.DIAGNOSTIC,
        suggested_display_precision=2,
        convert_bytes_to_gb=True,
    ),
    HostWatchSensorDescription(
        key="memory_used_bytes",
        translation_key="memory_used_bytes",
        name="Memory Used Absolute",
        native_unit_of_measurement=UnitOfInformation.GIGABYTES,
        value_path=("metrics", "memory", "used_bytes"),
        entity_category=EntityCategory.DIAGNOSTIC,
        suggested_display_precision=2,
        convert_bytes_to_gb=True,
    ),
    HostWatchSensorDescription(
        key="memory_available_bytes",
        translation_key="memory_available_bytes",
        name="Memory Available",
        native_unit_of_measurement=UnitOfInformation.GIGABYTES,
        value_path=("metrics", "memory", "available_bytes"),
        entity_category=EntityCategory.DIAGNOSTIC,
        suggested_display_precision=2,
        convert_bytes_to_gb=True,
    ),
    HostWatchSensorDescription(
        key="fs_root_used_percent",
        translation_key="fs_root_used_percent",
        name="Root Filesystem Used",
        native_unit_of_measurement=PERCENTAGE,
        value_path=("metrics", "filesystem", "root", "used_percent"),
    ),
    HostWatchSensorDescription(
        key="fs_root_total_bytes",
        translation_key="fs_root_total_bytes",
        name="Root Filesystem Total",
        native_unit_of_measurement=UnitOfInformation.GIGABYTES,
        value_path=("metrics", "filesystem", "root", "total_bytes"),
        entity_category=EntityCategory.DIAGNOSTIC,
        suggested_display_precision=2,
        convert_bytes_to_gb=True,
    ),
    HostWatchSensorDescription(
        key="fs_root_used_bytes",
        translation_key="fs_root_used_bytes",
        name="Root Filesystem Used Absolute",
        native_unit_of_measurement=UnitOfInformation.GIGABYTES,
        value_path=("metrics", "filesystem", "root", "used_bytes"),
        entity_category=EntityCategory.DIAGNOSTIC,
        suggested_display_precision=2,
        convert_bytes_to_gb=True,
    ),
    HostWatchSensorDescription(
        key="fs_root_available_bytes",
        translation_key="fs_root_available_bytes",
        name="Root Filesystem Available",
        native_unit_of_measurement=UnitOfInformation.GIGABYTES,
        value_path=("metrics", "filesystem", "root", "available_bytes"),
        entity_category=EntityCategory.DIAGNOSTIC,
        suggested_display_precision=2,
        convert_bytes_to_gb=True,
    ),
    HostWatchSensorDescription(
        key="uptime_seconds",
        translation_key="uptime_seconds",
        name="Uptime",
        value_path=("metrics", "uptime_seconds"),
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    HostWatchSensorDescription(
        key="apt_upgradable_count",
        translation_key="apt_upgradable_count",
        name="APT Upgradable Packages",
        value_path=("metrics", "updates", "apt", "upgradable_count"),
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    HostWatchSensorDescription(
        key="apt_last_checked",
        translation_key="apt_last_checked",
        name="APT Last Check",
        value_path=("metrics", "updates", "apt", "checked_at"),
        entity_category=EntityCategory.DIAGNOSTIC,
        device_class=SensorDeviceClass.TIMESTAMP,
    ),
    HostWatchSensorDescription(
        key="maintenance_mode",
        translation_key="maintenance_mode",
        name="Maintenance Mode",
        value_path=("maintenance", "enabled_until"),
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up HostWatch sensors from a config entry."""
    node_id = entry.data["node_id"]
    node = get_storage(hass).get_node(node_id)
    if node is None:
        return

    manager = HostWatchSensorManager(hass, entry, async_add_entities)
    await manager.async_initialize(node)
    entry.async_on_unload(manager.async_unload)


class HostWatchSensorManager:
    """Add sensor entities only for values the node actually publishes."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        async_add_entities: AddEntitiesCallback,
    ) -> None:
        self.hass = hass
        self.entry = entry
        self.async_add_entities = async_add_entities
        self.node_id = entry.data["node_id"]
        self._known_keys: set[str] = set()
        self._unsubscribe: Any = None

    async def async_initialize(self, initial_state: dict[str, Any]) -> None:
        self._maybe_add_entities(initial_state)

        @callback
        def handle_update() -> None:
            state = get_runtime(self.hass).get_state(self.node_id)
            self._maybe_add_entities(state)

        self._unsubscribe = async_dispatcher_connect(
            self.hass,
            SIGNAL_NODE_UPDATED.format(node_id=self.node_id),
            handle_update,
        )

    def async_unload(self) -> None:
        if self._unsubscribe is not None:
            self._unsubscribe()

    def _maybe_add_entities(self, state: dict[str, Any]) -> None:
        new_entities: list[SensorEntity] = []
        for description in SENSORS:
            if description.key in self._known_keys:
                continue
            if description.key != "maintenance_mode" and not _path_has_value(state, description.value_path):
                continue
            self._known_keys.add(description.key)
            new_entities.append(HostWatchSensor(self.hass, self.entry, description, state))
        for interface in _ip_interfaces(state):
            key = f"ip_address_{_slugify(interface)}"
            if key in self._known_keys:
                continue
            self._known_keys.add(key)
            new_entities.append(HostWatchInterfaceIpSensor(self.hass, self.entry, state, interface, key))
        if new_entities:
            self.async_add_entities(new_entities)


class HostWatchSensor(SensorEntity):
    """Representation of a push-updated HostWatch sensor."""

    entity_description: HostWatchSensorDescription
    _attr_has_entity_name = True

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        description: HostWatchSensorDescription,
        node: dict[str, Any],
    ) -> None:
        self.hass = hass
        self.entity_description = description
        self._node = node
        self._node_id = entry.data["node_id"]
        self._node_name = node["node_name"]
        self._attr_unique_id = f"{self._node_id}_{description.key}"
        self._attr_device_info = hostwatch_device_info(hass, node)
        self._state = get_runtime(hass).get_state(self._node_id) or node

    @property
    def suggested_object_id(self) -> str | None:
        """Return the object ID used for initial and reset entity ID generation."""
        return suggested_object_id(self._node, self.entity_description.key)

    @property
    def native_value(self) -> Any:
        """Return the sensor state."""
        if self.entity_description.key == "maintenance_mode":
            value = _value_at_path(self._state, self.entity_description.value_path)
            return "on" if _is_future_timestamp(value) else "off"

        value = self._state
        for segment in self.entity_description.value_path:
            if not isinstance(value, dict) or segment not in value:
                return None
            value = value[segment]
        if value is None:
            return None
        if self.entity_description.key == "uptime_seconds" and isinstance(value, (int, float)):
            return _format_uptime(value)
        if self.entity_description.key == "apt_last_checked" and isinstance(value, str):
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        if self.entity_description.convert_bytes_to_gb and isinstance(value, (int, float)):
            return round(value / (1000**3), 2)
        return value

    async def async_added_to_hass(self) -> None:
        """Subscribe to node updates."""

        @callback
        def handle_update() -> None:
            self._state = get_runtime(self.hass).get_state(self._node_id)
            self.async_write_ha_state()

        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                SIGNAL_NODE_UPDATED.format(node_id=self._node_id),
                handle_update,
            )
        )


class HostWatchInterfaceIpSensor(SensorEntity):
    """IP address sensor for one configured network interface."""

    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        node: dict[str, Any],
        interface: str,
        key: str,
    ) -> None:
        self.hass = hass
        self._node = node
        self._node_id = entry.data["node_id"]
        self._interface = interface
        self._attr_name = f"{interface} IP Address"
        self._attr_unique_id = f"{self._node_id}_{key}"
        self._key = key
        self._attr_device_info = hostwatch_device_info(hass, node)
        self._state = get_runtime(hass).get_state(self._node_id) or node

    @property
    def suggested_object_id(self) -> str | None:
        """Return the object ID used for initial and reset entity ID generation."""
        return suggested_object_id(self._node, self._key)

    @property
    def native_value(self) -> str | None:
        for item in _value_at_path(self._state, ("platform", "ipAddresses")) or []:
            if not isinstance(item, dict):
                continue
            if item.get("interface") == self._interface:
                return item.get("address")
        return None

    async def async_added_to_hass(self) -> None:
        """Subscribe to node updates."""

        @callback
        def handle_update() -> None:
            self._state = get_runtime(self.hass).get_state(self._node_id)
            self.async_write_ha_state()

        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                SIGNAL_NODE_UPDATED.format(node_id=self._node_id),
                handle_update,
            )
        )


def _path_has_value(payload: dict[str, Any], path: tuple[str, ...]) -> bool:
    value = _value_at_path(payload, path)
    return value is not None


def _value_at_path(payload: dict[str, Any], path: tuple[str, ...]) -> Any:
    value: Any = payload
    for segment in path:
        if not isinstance(value, dict) or segment not in value:
            return None
        value = value[segment]
    return value


def _ip_interfaces(payload: dict[str, Any]) -> list[str]:
    interfaces: list[str] = []
    for item in _value_at_path(payload, ("platform", "ipAddresses")) or []:
        if not isinstance(item, dict) or not item.get("address"):
            continue
        interface = item.get("interface") or "Primary"
        if interface not in interfaces:
            interfaces.append(interface)
    return interfaces


def _slugify(value: str) -> str:
    return "".join(char.lower() if char.isalnum() else "_" for char in value).strip("_") or "primary"


def _format_uptime(seconds: float) -> str:
    total_minutes = int(seconds // 60)
    days, remainder = divmod(total_minutes, 60 * 24)
    hours, minutes = divmod(remainder, 60)
    parts: list[str] = []
    if days:
        parts.append(f"{days}d")
    if hours:
        parts.append(f"{hours}h")
    if minutes or not parts:
        parts.append(f"{minutes}m")
    return " ".join(parts)


def _is_future_timestamp(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed > datetime.now(UTC)
