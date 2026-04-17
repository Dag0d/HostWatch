"""Binary sensor platform for HostWatch nodes."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import SIGNAL_NODE_UPDATED
from .device import hostwatch_device_info
from .entity_ids import suggested_object_id
from .runtime import get_runtime
from .storage import get_storage


@dataclass(frozen=True, kw_only=True)
class HostWatchBinarySensorDescription(BinarySensorEntityDescription):
    value_path: tuple[str, ...]


BINARY_SENSORS: tuple[HostWatchBinarySensorDescription, ...] = (
    HostWatchBinarySensorDescription(
        key="online",
        translation_key="online",
        name="Operating State",
        value_path=("online",),
        device_class=BinarySensorDeviceClass.RUNNING,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    HostWatchBinarySensorDescription(
        key="apt_updates_available",
        translation_key="apt_updates_available",
        name="APT Updates",
        value_path=("metrics", "updates", "apt", "upgradable_count"),
        device_class=BinarySensorDeviceClass.UPDATE,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    HostWatchBinarySensorDescription(
        key="bootloader_update_available",
        translation_key="bootloader_update_available",
        name="Bootloader Update",
        value_path=("metrics", "bootloader", "pending_count"),
        device_class=BinarySensorDeviceClass.UPDATE,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up HostWatch binary sensors from a config entry."""
    node_id = entry.data["node_id"]
    node = get_storage(hass).get_node(node_id)
    if node is None:
        return

    manager = HostWatchBinarySensorManager(hass, entry, async_add_entities)
    await manager.async_initialize(node)
    entry.async_on_unload(manager.async_unload)


class HostWatchBinarySensorManager:
    """Add binary sensors only for values the node actually publishes."""

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
        new_entities: list[BinarySensorEntity] = []
        for description in BINARY_SENSORS:
            if description.key in self._known_keys:
                continue
            if _value_at_path(state, description.value_path) is None:
                continue
            self._known_keys.add(description.key)
            new_entities.append(HostWatchBinarySensor(self.hass, self.entry, description, state))
        if new_entities:
            self.async_add_entities(new_entities)


class HostWatchBinarySensor(BinarySensorEntity):
    """Representation of a push-updated HostWatch binary sensor."""

    entity_description: HostWatchBinarySensorDescription
    _attr_has_entity_name = True

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        description: HostWatchBinarySensorDescription,
        node: dict[str, Any],
    ) -> None:
        self.hass = hass
        self.entity_description = description
        self._node = node
        self._node_id = entry.data["node_id"]
        self._attr_unique_id = f"{self._node_id}_{description.key}"
        self._attr_device_info = hostwatch_device_info(hass, node)
        self._state = get_runtime(hass).get_state(self._node_id) or node

    @property
    def suggested_object_id(self) -> str | None:
        """Return the object ID used for initial and reset entity ID generation."""
        return suggested_object_id(self._node, self.entity_description.key)

    @property
    def is_on(self) -> bool | None:
        """Return true when an update is available."""
        value = _value_at_path(self._state, self.entity_description.value_path)
        if value is None:
            return None
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return value > 0
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


def _value_at_path(payload: dict[str, Any] | None, path: tuple[str, ...]) -> Any:
    value: Any = payload
    for segment in path:
        if not isinstance(value, dict) or segment not in value:
            return None
        value = value[segment]
    return value
