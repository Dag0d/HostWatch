"""Entity ID helpers for HostWatch."""

from __future__ import annotations

from typing import Any


def suggested_object_id(node: dict[str, Any], key: str) -> str:
    """Return the entity-local object id part.

    Home Assistant prepends the device name for device-bound entities.
    The HostWatch prefix therefore belongs on the device name, not here.
    """
    return key
