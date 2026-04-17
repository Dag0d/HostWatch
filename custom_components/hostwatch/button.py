"""Button platform for HostWatch nodes."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass

from homeassistant.components.button import ButtonEntity, ButtonEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .device import hostwatch_device_info
from .entity_ids import suggested_object_id
from .release import get_release_manager
from .runtime import get_runtime
from .storage import get_storage

_LOGGER = logging.getLogger(__name__)
SHOW_AGENT_RELEASE_REFRESH_BUTTON = os.environ.get("HOSTWATCH_SHOW_AGENT_RELEASE_REFRESH_BUTTON") == "1"


@dataclass(frozen=True, kw_only=True)
class HostWatchButtonDescription(ButtonEntityDescription):
    action: str


BASE_BUTTONS: tuple[HostWatchButtonDescription, ...] = (
    HostWatchButtonDescription(
        key="maintenance_mode",
        translation_key="maintenance_mode",
        name="Enable Maintenance Mode",
        entity_category=EntityCategory.CONFIG,
        action="enable_maintenance",
    ),
)

EXPERIMENTAL_BUTTONS: tuple[HostWatchButtonDescription, ...] = (
    HostWatchButtonDescription(
        key="refresh_agent_release",
        translation_key="refresh_agent_release",
        name="Refresh Agent Updates",
        entity_category=EntityCategory.DIAGNOSTIC,
        action="refresh_agent_release",
    ),
)

BUTTONS: tuple[HostWatchButtonDescription, ...] = (
    BASE_BUTTONS + EXPERIMENTAL_BUTTONS if SHOW_AGENT_RELEASE_REFRESH_BUTTON else BASE_BUTTONS
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    node = get_storage(hass).get_node(entry.data["node_id"])
    if node is None:
        return
    async_add_entities(
        HostWatchButton(hass, entry, description, node)
        for description in BUTTONS
    )


class HostWatchButton(ButtonEntity):
    """Queue a HostWatch command for a node."""

    entity_description: HostWatchButtonDescription
    _attr_has_entity_name = True

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        description: HostWatchButtonDescription,
        node: dict,
    ) -> None:
        self.hass = hass
        self.entity_description = description
        self._node = node
        self._node_id = entry.data["node_id"]
        self._attr_unique_id = f"{self._node_id}_{description.key}"
        self._attr_device_info = hostwatch_device_info(hass, node)

    @property
    def suggested_object_id(self) -> str | None:
        """Return the object ID used for initial and reset entity ID generation."""
        return suggested_object_id(self._node, self.entity_description.key)

    async def async_press(self) -> None:
        if self.entity_description.action == "enable_maintenance":
            enabled_until = await get_storage(self.hass).async_enable_maintenance(self._node_id)
            if enabled_until is not None:
                get_runtime(self.hass).update_node(
                    self._node_id,
                    {"maintenance": {"enabled_until": enabled_until}},
                )
                _LOGGER.info("Maintenance mode enabled for node %s until %s", self._node_id, enabled_until)
            return
        if self.entity_description.action == "refresh_agent_release":
            await get_release_manager(self.hass).async_refresh()
            _LOGGER.info("Agent release metadata refresh triggered for node %s", self._node_id)
