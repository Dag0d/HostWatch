"""Config flow for HostWatch."""

from __future__ import annotations

import asyncio
import logging
import ssl
import uuid
from typing import Any
from urllib.parse import urlparse

import aiohttp
import voluptuous as vol
from homeassistant import config_entries
from homeassistant.components import webhook
from homeassistant.components import zeroconf
from homeassistant.const import CONF_HOST, CONF_NAME, CONF_PORT
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers import aiohttp_client
from homeassistant.helpers.network import NoURLAvailableError, get_url
from homeassistant.helpers.service_info.ssdp import SsdpServiceInfo

from .const import (
    CONF_HA_INSTANCE_ID,
    CONF_HA_NAME,
    CONF_HA_URL,
    CONF_HA_URL_MODE,
    CONF_HEARTBEAT_WEBHOOK_ID,
    CONF_HEARTBEAT_WEBHOOK_URL,
    CONF_METRICS_WEBHOOK_ID,
    CONF_METRICS_WEBHOOK_URL,
    CONF_COMMAND_RESULT_WEBHOOK_ID,
    CONF_COMMAND_RESULT_WEBHOOK_URL,
    CONF_COMMAND_POLL_WEBHOOK_ID,
    CONF_COMMAND_POLL_WEBHOOK_URL,
    CONF_NODE_ID,
    CONF_NODE_NAME,
    CONF_NODE_SECRET,
    CONF_NODE_UID,
    DEFAULT_PORT,
    DOMAIN,
    PAIRING_APPROVAL_WAIT_SECONDS,
    PAIRING_ROUTE_COMPLETE,
    PAIRING_ROUTE_INFO,
    PAIRING_ROUTE_REQUEST,
)
from .storage import async_ensure_storage
from .webhooks import async_register_node_webhooks

_LOGGER = logging.getLogger(__name__)


class PairingError(Exception):
    """Raised when pairing fails."""


class PairingApprovalPending(PairingError):
    """Raised when the node is still waiting for local approval."""


class HostWatchConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for HostWatch."""

    VERSION = 1

    def __init__(self) -> None:
        self._pairing_info: dict[str, Any] = {}

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Start manual pairing flow from the integration UI."""
        return await self.async_step_manual(user_input)

    async def async_step_zeroconf(self, discovery_info: zeroconf.ZeroconfServiceInfo) -> FlowResult:
        """Handle zeroconf discovery."""
        host = discovery_info.host.rstrip(".")
        port = discovery_info.port or DEFAULT_PORT
        self._pairing_info = {
            CONF_HOST: host,
            CONF_PORT: port,
            CONF_NAME: discovery_info.name,
        }
        self.context["title_placeholders"] = {"name": discovery_info.name}
        return await self.async_step_discovered()

    async def async_step_ssdp(self, discovery_info: SsdpServiceInfo) -> FlowResult:
        """Handle SSDP discovery."""
        location = getattr(discovery_info, "ssdp_location", None) or getattr(
            discovery_info, "location", None
        )
        usn = getattr(discovery_info, "ssdp_usn", None) or getattr(
            discovery_info, "usn", None
        )
        if not location:
            return self.async_abort(reason="cannot_connect")

        parsed = urlparse(location)
        host = parsed.hostname
        port = parsed.port or DEFAULT_PORT
        if host is None:
            return self.async_abort(reason="cannot_connect")

        await self.async_set_unique_id(usn or f"{host}:{port}")
        self._abort_if_unique_id_configured()

        upnp = getattr(discovery_info, "upnp", None)
        friendly_name = None
        presentation_url = None
        if upnp and hasattr(upnp, "get"):
            friendly_name = upnp.get("friendlyName")
            presentation_url = upnp.get("presentationURL")

        pairing_port = port
        if presentation_url:
            presentation_parsed = urlparse(presentation_url)
            if presentation_parsed.hostname:
                host = presentation_parsed.hostname
            pairing_port = presentation_parsed.port or DEFAULT_PORT
        elif parsed.path == "/description.xml" and parsed.scheme == "http":
            pairing_port = DEFAULT_PORT

        self._pairing_info = {
            CONF_HOST: host,
            CONF_PORT: pairing_port,
            CONF_NAME: friendly_name or host,
        }
        self.context["title_placeholders"] = {"name": friendly_name or host}
        return await self.async_step_discovered()

    async def async_step_discovered(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Confirm a discovered node."""
        errors: dict[str, str] = {}
        if user_input is None and "pairing_code" not in self._pairing_info:
            self._pairing_info.update(
                await self._async_fetch_pairing_preview(
                    self._pairing_info[CONF_HOST], self._pairing_info[CONF_PORT]
                )
            )
        if user_input is not None:
            try:
                result = await self._async_pair(
                    host=self._pairing_info[CONF_HOST],
                    port=self._pairing_info[CONF_PORT],
                )
            except PairingApprovalPending:
                errors["base"] = "unknown"
            except PairingError as err:
                _LOGGER.warning("Pairing failed: %s", err)
                errors["base"] = "cannot_connect"
            else:
                return await self._async_create_entry(result)

        placeholders = {
            "host": self._pairing_info.get(CONF_HOST, "unknown"),
            "port": str(self._pairing_info.get(CONF_PORT, DEFAULT_PORT)),
            "pairing_code": self._pairing_info.get("pairing_code", "unknown"),
        }
        return self.async_show_form(
            step_id="discovered",
            description_placeholders=placeholders,
            data_schema=vol.Schema({}),
            errors=errors,
        )

    async def async_step_manual(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Pair manually with a node by host and port."""
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                result = await self._async_pair(
                    host=user_input[CONF_HOST],
                    port=user_input[CONF_PORT],
                )
            except PairingApprovalPending:
                errors["base"] = "unknown"
            except PairingError as err:
                _LOGGER.warning("Manual pairing failed: %s", err)
                errors["base"] = "cannot_connect"
            else:
                await self.async_set_unique_id(result[CONF_NODE_UID])
                self._abort_if_unique_id_configured()
                return await self._async_create_entry(result)

        return self.async_show_form(
            step_id="manual",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_HOST): str,
                    vol.Required(CONF_PORT, default=DEFAULT_PORT): int,
                }
            ),
            errors=errors,
        )

    async def _async_pair(self, host: str, port: int) -> dict[str, Any]:
        """Execute the shared pairing flow for discovery and manual setup."""
        session = aiohttp_client.async_get_clientsession(self.hass)
        ssl_context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ssl_context.check_hostname = False
        ssl_context.verify_mode = ssl.CERT_NONE

        base_url = f"https://{host}:{port}"
        ha_name = self.hass.config.location_name
        pairing_info: dict[str, Any]

        try:
            async with asyncio.timeout(10):
                info_response = await session.get(
                    f"{base_url}{PAIRING_ROUTE_INFO}", ssl=ssl_context
                )
                info_response.raise_for_status()
                pairing_info = await info_response.json()
                self._pairing_info.update(
                    {
                        CONF_HOST: host,
                        CONF_PORT: port,
                        CONF_HA_URL_MODE: pairing_info.get(CONF_HA_URL_MODE, "local"),
                    }
                )
                node_uid = pairing_info.get("node_uid")
                if node_uid:
                    await self.async_set_unique_id(node_uid)
                    self._abort_if_unique_id_configured()

            ha_url_mode = self._pairing_info.get(CONF_HA_URL_MODE, "local")
            use_external_url = ha_url_mode == "external"
            try:
                ha_url = get_url(
                    self.hass,
                    allow_internal=not use_external_url,
                    allow_external=use_external_url,
                    allow_ip=True,
                    prefer_external=use_external_url,
                )
            except NoURLAvailableError as err:
                raise PairingError(
                    "no matching Home Assistant URL available; configure the requested local/external URL first"
                ) from err

            heartbeat_webhook_id = uuid.uuid4().hex
            metrics_webhook_id = uuid.uuid4().hex
            command_result_webhook_id = uuid.uuid4().hex
            command_poll_webhook_id = uuid.uuid4().hex
            request_payload = {
                CONF_HA_NAME: ha_name,
                CONF_HA_URL: ha_url,
                CONF_HA_INSTANCE_ID: DOMAIN,
            }

            async with asyncio.timeout(10):
                request_response = await session.post(
                    f"{base_url}{PAIRING_ROUTE_REQUEST}",
                    json=request_payload,
                    ssl=ssl_context,
                )
                request_response.raise_for_status()
                request_result = await request_response.json()

            complete_payload = {
                CONF_NODE_ID: str(uuid.uuid4()),
                CONF_NODE_SECRET: uuid.uuid4().hex + uuid.uuid4().hex,
                CONF_HA_NAME: ha_name,
                CONF_HA_URL: ha_url,
                CONF_HEARTBEAT_WEBHOOK_URL: f"{ha_url}{webhook.async_generate_path(heartbeat_webhook_id)}",
                CONF_METRICS_WEBHOOK_URL: f"{ha_url}{webhook.async_generate_path(metrics_webhook_id)}",
                CONF_COMMAND_RESULT_WEBHOOK_URL: f"{ha_url}{webhook.async_generate_path(command_result_webhook_id)}",
                CONF_COMMAND_POLL_WEBHOOK_URL: f"{ha_url}{webhook.async_generate_path(command_poll_webhook_id)}",
                "request_id": request_result["request_id"],
            }
            async with asyncio.timeout(PAIRING_APPROVAL_WAIT_SECONDS + 10):
                complete_result = await self._async_complete_pairing(
                    session=session,
                    base_url=base_url,
                    payload=complete_payload,
                    ssl_context=ssl_context,
                )
        except asyncio.TimeoutError as err:
            raise PairingError(
                f"timed out while waiting for node approval after {PAIRING_APPROVAL_WAIT_SECONDS} seconds"
            ) from err
        except (aiohttp.ClientError, KeyError, ValueError) as err:
            raise PairingError(str(err)) from err

        return {
            CONF_HOST: host,
            CONF_PORT: port,
            CONF_NODE_ID: complete_payload[CONF_NODE_ID],
            CONF_NODE_UID: pairing_info.get("node_uid", complete_payload[CONF_NODE_ID]),
            CONF_NODE_NAME: pairing_info.get("node_name", host),
            CONF_NODE_SECRET: complete_payload[CONF_NODE_SECRET],
            CONF_HEARTBEAT_WEBHOOK_ID: heartbeat_webhook_id,
            CONF_METRICS_WEBHOOK_ID: metrics_webhook_id,
            CONF_COMMAND_RESULT_WEBHOOK_ID: command_result_webhook_id,
            CONF_COMMAND_POLL_WEBHOOK_ID: command_poll_webhook_id,
            "capabilities": complete_result.get("capabilities", {}),
            "platform": complete_result.get("platform", {}),
        }

    async def _async_complete_pairing(
        self,
        *,
        session: aiohttp.ClientSession,
        base_url: str,
        payload: dict[str, Any],
        ssl_context: ssl.SSLContext,
    ) -> dict[str, Any]:
        """Wait briefly for local approval, then complete pairing."""
        last_error: aiohttp.ClientResponseError | None = None
        for _attempt in range(PAIRING_APPROVAL_WAIT_SECONDS):
            try:
                complete_response = await session.post(
                    f"{base_url}{PAIRING_ROUTE_COMPLETE}",
                    json=payload,
                    ssl=ssl_context,
                )
                complete_response.raise_for_status()
                return await complete_response.json()
            except aiohttp.ClientResponseError as err:
                if err.status != 409:
                    raise
                last_error = err
                await asyncio.sleep(1)

        if last_error is not None:
            raise PairingApprovalPending(str(last_error))
        raise PairingError("pairing completion failed")

    async def _async_fetch_pairing_preview(self, host: str, port: int) -> dict[str, Any]:
        """Fetch minimal pairing preview data for the discovery dialog."""
        session = aiohttp_client.async_get_clientsession(self.hass)
        ssl_context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ssl_context.check_hostname = False
        ssl_context.verify_mode = ssl.CERT_NONE
        base_url = f"https://{host}:{port}"
        try:
            async with asyncio.timeout(10):
                response = await session.get(f"{base_url}{PAIRING_ROUTE_INFO}", ssl=ssl_context)
                response.raise_for_status()
                payload = await response.json()
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError):
            return {}

        node_uid = payload.get("node_uid")
        if node_uid:
            await self.async_set_unique_id(node_uid)
            self._abort_if_unique_id_configured()
        return {
            "pairing_code": payload.get("pairing_code", "unknown"),
            "node_uid": node_uid,
            CONF_HA_URL_MODE: payload.get(CONF_HA_URL_MODE, "local"),
        }

    async def _async_create_entry(self, result: dict[str, Any]) -> FlowResult:
        """Persist config entry and storage metadata."""
        await self.async_set_unique_id(result[CONF_NODE_UID])
        self._abort_if_unique_id_configured()
        storage = await async_ensure_storage(self.hass)
        await storage.async_upsert_node(
            {
                "node_id": result[CONF_NODE_ID],
                "node_uid": result[CONF_NODE_UID],
                "node_name": result[CONF_NODE_NAME],
                "node_secret": result[CONF_NODE_SECRET],
                "heartbeat_webhook_id": result[CONF_HEARTBEAT_WEBHOOK_ID],
                "metrics_webhook_id": result[CONF_METRICS_WEBHOOK_ID],
                "command_result_webhook_id": result[CONF_COMMAND_RESULT_WEBHOOK_ID],
                "command_poll_webhook_id": result[CONF_COMMAND_POLL_WEBHOOK_ID],
                "host": result[CONF_HOST],
                "port": result[CONF_PORT],
                "capabilities": result["capabilities"],
                "platform": result["platform"],
                "metrics": {},
                "online": False,
            }
        )
        node = storage.get_node(result[CONF_NODE_ID])
        if node is not None:
            await async_register_node_webhooks(self.hass, node)
        return self.async_create_entry(
            title=result[CONF_NODE_NAME],
            data={
                CONF_NODE_ID: result[CONF_NODE_ID],
                CONF_NODE_UID: result[CONF_NODE_UID],
                CONF_NODE_NAME: result[CONF_NODE_NAME],
                CONF_NODE_SECRET: result[CONF_NODE_SECRET],
                CONF_HEARTBEAT_WEBHOOK_ID: result[CONF_HEARTBEAT_WEBHOOK_ID],
                CONF_METRICS_WEBHOOK_ID: result[CONF_METRICS_WEBHOOK_ID],
                CONF_COMMAND_RESULT_WEBHOOK_ID: result[CONF_COMMAND_RESULT_WEBHOOK_ID],
                CONF_COMMAND_POLL_WEBHOOK_ID: result[CONF_COMMAND_POLL_WEBHOOK_ID],
            },
        )
