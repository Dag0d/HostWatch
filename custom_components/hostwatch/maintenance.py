"""Admin-only maintenance UI and API for HostWatch nodes."""

from __future__ import annotations

import asyncio
import html
import inspect
import json
from pathlib import Path
import uuid
from datetime import UTC, datetime
from typing import Any

from aiohttp import web
from homeassistant.components.http import HomeAssistantView
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect, async_dispatcher_send

from .const import DOMAIN, MAINTENANCE_MODE_SECONDS, SIGNAL_COMMAND_RUN_UPDATED
from .runtime import get_runtime
from .storage import get_storage

PANEL_TRANSLATIONS_DIR = Path(__file__).with_name("panel_translations")

COMMANDS: dict[str, dict[str, Any]] = {
    "refresh_apt_check": {
        "section": "apt",
        "label_key": "command_refresh_apt_check",
        "critical": False,
    },
    "apt_upgrade": {
        "section": "apt",
        "label_key": "command_apt_upgrade",
        "critical": True,
    },
    "refresh_bootloader_check": {
        "section": "eeprom",
        "label_key": "command_refresh_bootloader_check",
        "critical": False,
        "requires_raspberry_pi_bootloader": True,
    },
    "set_eeprom_track": {
        "section": "eeprom",
        "label_key": "command_set_eeprom_track",
        "confirmation_key": "confirm_set_eeprom_track",
        "critical": True,
        "requires_raspberry_pi_bootloader": True,
        "input": {
            "name": "track",
            "type": "select",
            "label_key": "input_release_track",
            "options": [
                {"value": "default", "label_key": "option_default"},
                {"value": "latest", "label_key": "option_latest"},
            ],
        },
    },
    "set_eeprom_flashrom": {
        "section": "eeprom",
        "label_key": "command_set_eeprom_flashrom",
        "confirmation_key": "confirm_set_eeprom_flashrom",
        "critical": True,
        "requires_raspberry_pi_5": True,
        "input": {
            "name": "use_flashrom",
            "type": "select",
            "label_key": "input_live_flashing",
            "options": [
                {"value": "0", "label_key": "option_disabled"},
                {"value": "1", "label_key": "option_enabled"},
            ],
        },
    },
    "bootloader_upgrade": {
        "section": "eeprom",
        "label_key": "command_bootloader_upgrade",
        "confirmation_key": "confirm_bootloader_upgrade",
        "critical": True,
        "requires_raspberry_pi_bootloader": True,
    },
    "show_vpn_recovery_history": {
        "section": "vpn",
        "label_key": "command_show_vpn_recovery_history",
        "critical": False,
        "requires_vpn": True,
    },
    "reboot": {
        "section": "power",
        "label_key": "command_reboot",
        "critical": True,
    },
    "shutdown": {
        "section": "power",
        "label_key": "command_shutdown",
        "critical": True,
    },
}

SECTIONS: dict[str, dict[str, str]] = {
    "apt": {"label_key": "section_apt"},
    "eeprom": {"label_key": "section_eeprom"},
    "vpn": {"label_key": "section_vpn"},
    "power": {"label_key": "section_power"},
}


async def async_setup_maintenance(hass: HomeAssistant) -> None:
    """Register maintenance views once."""
    hass.data.setdefault(DOMAIN, {})
    if hass.data[DOMAIN].get("maintenance_registered"):
        return
    hass.http.register_view(HostWatchMaintenancePageView())
    hass.http.register_view(HostWatchMaintenanceStateView())
    hass.http.register_view(HostWatchMaintenanceCommandView())
    hass.http.register_view(HostWatchMaintenanceOutputView())
    hass.http.register_view(HostWatchMaintenanceEventsView())
    hass.http.register_view(HostWatchMaintenanceTranslationsView())
    hass.data[DOMAIN]["maintenance_registered"] = True


def async_notify_command_run_updated(hass: HomeAssistant, node_id: str) -> None:
    """Notify open maintenance pages that command state changed."""
    async_dispatcher_send(hass, SIGNAL_COMMAND_RUN_UPDATED.format(node_id=node_id))


def get_available_commands(node: dict[str, Any]) -> list[dict[str, Any]]:
    """Return commands available for this node."""
    capabilities = node.get("capabilities", {})
    commands: list[dict[str, Any]] = []
    for command_type, description in COMMANDS.items():
        if description.get("requires_raspberry_pi_bootloader") and not _has_raspberry_pi_bootloader(node):
            continue
        if description.get("requires_raspberry_pi_5") and not _is_raspberry_pi_5(node):
            continue
        if description.get("requires_vpn") and not _is_vpn_node(node):
            continue
        commands.append(
            {
                "type": command_type,
                "section": description["section"],
                "label_key": description["label_key"],
                "confirmation_key": description.get("confirmation_key"),
                "critical": description["critical"],
                "input": _command_input_payload(description.get("input")),
            }
        )
    return commands


def _command_input_payload(input_config: dict[str, Any] | None) -> dict[str, Any] | None:
    if input_config is None:
        return None
    return {
        "name": input_config["name"],
        "type": input_config["type"],
        "label_key": input_config["label_key"],
        "options": [
            {"value": option["value"], "label_key": option["label_key"]}
            for option in input_config.get("options", [])
        ],
    }


def _is_raspberry_pi_5(node: dict[str, Any]) -> bool:
    capabilities = node.get("capabilities", {})
    if capabilities.get("raspberryPi5") is True:
        return True
    platform = node.get("platform", {})
    model = str(platform.get("raspberry_model") or "").lower()
    if "raspberry pi 5" in model:
        return True
    metrics = node.get("metrics", {})
    bootloader = metrics.get("bootloader", {}) if isinstance(metrics, dict) else {}
    return bootloader.get("chip") == "2712"


def _has_raspberry_pi_bootloader(node: dict[str, Any]) -> bool:
    capabilities = node.get("capabilities", {})
    if capabilities.get("raspberryPiBootloader") is True:
        return True
    metrics = node.get("metrics", {})
    bootloader = metrics.get("bootloader", {}) if isinstance(metrics, dict) else {}
    chip = bootloader.get("chip")
    return chip in {"2711", "2712"}


def _is_vpn_node(node: dict[str, Any]) -> bool:
    platform = node.get("platform", {})
    return platform.get("connectionStyle") == "vpn"


def _command_params(command: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any] | None:
    input_config = command.get("input")
    if not input_config:
        return {}
    name = input_config["name"]
    value = payload.get(name)
    allowed = {str(option["value"]) for option in input_config.get("options", [])}
    if str(value) not in allowed:
        return None
    return {name: str(value)}


class _HostWatchAdminView(HomeAssistantView):
    requires_auth = True
    requires_admin = True


class HostWatchMaintenancePageView(_HostWatchAdminView):
    """Serve the maintenance page linked from the device registry."""

    url = "/hostwatch/maintenance/{node_id}"
    name = "api:hostwatch:maintenance"
    requires_auth = False
    requires_admin = False

    async def get(self, request: web.Request, node_id: str) -> web.Response:
        hass: HomeAssistant = request.app["hass"]
        node = get_storage(hass).get_node(node_id)
        if node is None:
            raise web.HTTPNotFound()
        title = f"HostWatch Maintenance - {node.get('node_name', node_id)}"
        return web.Response(
            text=_maintenance_html(node_id, title),
            content_type="text/html",
            headers={"Cache-Control": "no-store"},
        )


class HostWatchMaintenanceStateView(_HostWatchAdminView):
    """Return current maintenance state and recent command runs."""

    url = "/api/hostwatch/maintenance/{node_id}/state"
    name = "api:hostwatch:maintenance:state"

    async def get(self, request: web.Request, node_id: str) -> web.Response:
        hass: HomeAssistant = request.app["hass"]
        storage = get_storage(hass)
        node = storage.get_node(node_id)
        if node is None:
            raise web.HTTPNotFound()
        return web.json_response(_state_payload(hass, storage, node))


class HostWatchMaintenanceCommandView(_HostWatchAdminView):
    """Queue an allowlisted command while maintenance mode is active."""

    url = "/api/hostwatch/maintenance/{node_id}/command"
    name = "api:hostwatch:maintenance:command"

    async def post(self, request: web.Request, node_id: str) -> web.Response:
        hass: HomeAssistant = request.app["hass"]
        storage = get_storage(hass)
        node = storage.get_node(node_id)
        if node is None:
            raise web.HTTPNotFound()
        if not storage.is_maintenance_enabled(node_id):
            return web.json_response({"error": "maintenance_mode_required"}, status=403)

        payload = await request.json()
        command_type = payload.get("command_type")
        confirmation = payload.get("confirmation")
        available = {command["type"]: command for command in get_available_commands(node)}
        command = available.get(command_type)
        if command is None:
            return web.json_response({"error": "unsupported_command"}, status=400)
        if command["critical"] and confirmation != command_type:
            return web.json_response({"error": "confirmation_required"}, status=400)
        params = _command_params(command, payload)
        if params is None:
            return web.json_response({"error": "invalid_command_parameters"}, status=400)

        run = await storage.async_create_command_run(node_id, command_type, params=params)
        if run is None:
            raise web.HTTPNotFound()
        async_notify_command_run_updated(hass, node_id)
        return web.json_response({"run": run})


class HostWatchMaintenanceOutputView(_HostWatchAdminView):
    """Request command output from the node while maintenance mode is active."""

    url = "/api/hostwatch/maintenance/{node_id}/output/{run_id}"
    name = "api:hostwatch:maintenance:output"

    async def post(self, request: web.Request, node_id: str, run_id: str) -> web.Response:
        hass: HomeAssistant = request.app["hass"]
        storage = get_storage(hass)
        node = storage.get_node(node_id)
        if node is None:
            raise web.HTTPNotFound()
        if not storage.is_maintenance_enabled(node_id):
            return web.json_response({"error": "maintenance_mode_required"}, status=403)
        run = storage.get_command_run(node_id, run_id)
        if run is None:
            return web.json_response({"error": "unknown_run"}, status=404)
        cached_output = get_runtime(hass).get_command_output(node_id, run_id)
        if cached_output is not None:
            return web.json_response({"status": "available", "output": cached_output})
        await storage.async_enqueue_command(
            node_id,
            {
                "id": uuid.uuid4().hex,
                "type": "fetch_command_output",
                "target_run_id": run_id,
                "target_command_type": run["command_type"],
                "requested_at": datetime.now(UTC).isoformat(),
            },
        )
        return web.json_response({"status": "requested"})


class HostWatchMaintenanceEventsView(_HostWatchAdminView):
    """Stream command run updates to the browser with a HA-proxied WebSocket."""

    url = "/api/hostwatch/maintenance/{node_id}/events"
    name = "api:hostwatch:maintenance:events"
    requires_auth = False
    requires_admin = False

    async def get(self, request: web.Request, node_id: str) -> web.WebSocketResponse:
        hass: HomeAssistant = request.app["hass"]
        storage = get_storage(hass)
        if storage.get_node(node_id) is None:
            raise web.HTTPNotFound()

        ws = web.WebSocketResponse(heartbeat=30)
        await ws.prepare(request)
        if not await _websocket_authenticate_admin(hass, ws):
            await ws.close(code=1008, message=b"unauthorized")
            return ws

        event = asyncio.Event()

        @callback
        def _handle_update() -> None:
            event.set()

        unsubscribe = async_dispatcher_connect(
            hass,
            SIGNAL_COMMAND_RUN_UPDATED.format(node_id=node_id),
            _handle_update,
        )
        try:
            await _send_ws_runs(ws, _runs_payload(hass, storage, node_id))
            while True:
                try:
                    await asyncio.wait_for(event.wait(), timeout=30)
                except asyncio.TimeoutError:
                    if ws.closed:
                        break
                    await ws.send_json({"type": "keepalive"})
                    continue
                event.clear()
                if ws.closed:
                    break
                await _send_ws_runs(ws, _runs_payload(hass, storage, node_id))
        except (asyncio.CancelledError, ConnectionResetError):
            raise
        finally:
            unsubscribe()
            await ws.close()
        return ws


class HostWatchMaintenanceTranslationsView(_HostWatchAdminView):
    """Return maintenance panel translations for one language."""

    url = "/api/hostwatch/maintenance/i18n/{language}"
    name = "api:hostwatch:maintenance:i18n"
    requires_auth = False
    requires_admin = False

    async def get(self, request: web.Request, language: str) -> web.Response:
        return web.json_response(_load_panel_translations(_normalize_language_tag(language)))


def _state_payload(hass: HomeAssistant, storage: Any, node: dict[str, Any]) -> dict[str, Any]:
    node_id = node["node_id"]
    enabled_until = storage.get_maintenance_enabled_until(node_id)
    return {
        "node": {
            "id": node_id,
            "name": node.get("node_name", node_id),
            "online": node.get("online", False),
        },
        "metrics": node.get("metrics", {}),
        "maintenance": {
            "enabled": storage.is_maintenance_enabled(node_id),
            "enabled_until": enabled_until,
            "duration_seconds": MAINTENANCE_MODE_SECONDS,
        },
        "commands": get_available_commands(node),
        "sections": SECTIONS,
        "runs": _runs_payload(hass, storage, node_id),
        "server_time": datetime.now(UTC).isoformat(),
    }


def _runs_payload(hass: HomeAssistant, storage: Any, node_id: str) -> list[dict[str, Any]]:
    runs = storage.get_recent_command_runs(node_id)
    if not storage.is_maintenance_enabled(node_id):
        return runs
    runtime = get_runtime(hass)
    for run in runs:
        output = runtime.get_command_output(node_id, run["id"])
        if output is not None:
            run["output"] = output
    return runs


async def _send_ws_runs(ws: web.WebSocketResponse, runs: list[dict[str, Any]]) -> None:
    await ws.send_str(json.dumps({"type": "runs", "runs": runs}))


async def _websocket_authenticate_admin(hass: HomeAssistant, ws: web.WebSocketResponse) -> bool:
    try:
        message = await asyncio.wait_for(ws.receive(), timeout=10)
    except asyncio.TimeoutError:
        return False
    if message.type != web.WSMsgType.TEXT:
        return False
    try:
        payload = json.loads(message.data)
    except ValueError:
        return False
    if payload.get("type") != "auth":
        return False
    return await _token_is_admin(hass, payload.get("access_token"))


async def _token_is_admin(hass: HomeAssistant, token: Any) -> bool:
    if not isinstance(token, str) or not token:
        return False
    refresh_token_result = hass.auth.async_validate_access_token(token)
    if inspect.isawaitable(refresh_token_result):
        refresh_token = await refresh_token_result
    else:
        refresh_token = refresh_token_result
    user = getattr(refresh_token, "user", None) if refresh_token else None
    return bool(user and user.is_admin)


def _maintenance_html(node_id: str, title: str) -> str:
    escaped_title = html.escape(title)
    escaped_node_id = html.escape(node_id)
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="color-scheme" content="light dark">
  <title>{escaped_title}</title>
  <style>
    :root {{
      color-scheme: light;
      --ha-default-bg: #fafafa;
      --ha-default-card: #ffffff;
      --ha-default-line: rgba(0, 0, 0, .12);
      --ha-default-text: #212121;
      --ha-default-muted: #727272;
      --ha-default-primary: #03a9f4;
      --ha-default-danger: #db4437;
      --ha-default-hover: rgba(3, 169, 244, .08);
      --ha-default-field: #f5f5f5;
      --ha-dark-bg: #111111;
      --ha-dark-card: #1c1c1c;
      --ha-dark-line: rgba(255, 255, 255, .12);
      --ha-dark-text: #e1e1e1;
      --ha-dark-muted: #9e9e9e;
      --ha-dark-primary: #03a9f4;
      --ha-dark-danger: #ff6f60;
      --ha-dark-hover: rgba(3, 169, 244, .14);
      --ha-dark-field: #242424;
      --bg: var(--primary-background-color, var(--ha-default-bg));
      --panel: var(--card-background-color, var(--ha-default-card));
      --line: var(--divider-color, var(--ha-default-line));
      --text: var(--primary-text-color, var(--ha-default-text));
      --muted: var(--secondary-text-color, var(--ha-default-muted));
      --accent: var(--primary-color, var(--ha-default-primary));
      --danger: var(--error-color, var(--ha-default-danger));
      --button-bg: color-mix(in srgb, var(--accent) 10%, var(--panel));
      --button-border: color-mix(in srgb, var(--accent) 38%, var(--line));
      --button-hover: var(--ha-default-hover);
      --input-bg: var(--input-fill-color, var(--ha-default-field));
      --shadow: rgba(0, 0, 0, .12);
      --terminal-bg: #080b0f;
      --terminal-line: #313244;
      --terminal-text: #cdd6f4;
    }}
    @media (prefers-color-scheme: dark) {{
      :root {{
        color-scheme: dark;
        --bg: var(--primary-background-color, var(--ha-dark-bg));
        --panel: var(--card-background-color, var(--ha-dark-card));
        --line: var(--divider-color, var(--ha-dark-line));
        --text: var(--primary-text-color, var(--ha-dark-text));
        --muted: var(--secondary-text-color, var(--ha-dark-muted));
        --accent: var(--primary-color, var(--ha-dark-primary));
        --danger: var(--error-color, var(--ha-dark-danger));
        --button-bg: color-mix(in srgb, var(--accent) 14%, var(--panel));
        --button-border: color-mix(in srgb, var(--accent) 44%, var(--line));
        --button-hover: var(--ha-dark-hover);
        --input-bg: var(--input-fill-color, var(--ha-dark-field));
        --shadow: rgba(0, 0, 0, .32);
      }}
    }}
    body {{
      margin: 0;
      font-family: var(--paper-font-body1_-_font-family, Roboto, "Noto Sans", sans-serif);
      font-size: 14px;
      background: var(--bg);
      color: var(--text);
    }}
    main {{
      max-width: 1100px;
      margin: 0 auto;
      padding: 24px;
    }}
    header {{
      border-bottom: 1px solid var(--line);
      margin-bottom: 16px;
      padding-bottom: 16px;
    }}
    h1 {{
      font-size: 24px;
      font-weight: 400;
      line-height: 32px;
      margin: 0 0 4px;
    }}
    h2 {{
      font-size: 20px;
      font-weight: 400;
      line-height: 28px;
      margin: 0 0 12px;
    }}
    .muted {{ color: var(--muted); }}
    .grid {{
      display: grid;
      grid-template-columns: minmax(240px, 320px) 1fr;
      gap: 1rem;
    }}
    .card {{
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 12px;
      padding: 16px;
      box-shadow: 0 2px 6px var(--shadow);
    }}
    button {{
      width: 100%;
      border: 1px solid var(--button-border);
      border-radius: 4px;
      background: var(--button-bg);
      color: var(--text);
      cursor: pointer;
      font: inherit;
      min-height: 40px;
      margin: 4px 0;
      padding: 0 16px;
      text-align: left;
      transition: background-color .12s ease, border-color .12s ease, color .12s ease;
    }}
    button:hover {{ background: color-mix(in srgb, var(--accent) 16%, var(--panel)); border-color: var(--accent); color: var(--accent); }}
    button.danger {{
      border-color: color-mix(in srgb, var(--danger) 54%, var(--line));
      box-shadow: inset 3px 0 0 var(--danger);
    }}
    button.danger:hover {{
      background: color-mix(in srgb, var(--danger) 6%, var(--panel));
      border-color: var(--danger);
      color: var(--danger);
    }}
    button:disabled {{ cursor: not-allowed; opacity: .45; }}
    .command-section {{
      border-top: 1px solid var(--line);
      margin-top: 1rem;
      padding-top: .9rem;
    }}
    .command-section:first-child {{
      border-top: 0;
      margin-top: 0;
      padding-top: 0;
    }}
    .command-section h3 {{
      color: var(--muted);
      font-size: 13px;
      font-weight: 500;
      letter-spacing: .04em;
      margin: 4px 0 8px;
      text-transform: uppercase;
    }}
    .command-input {{
      display: grid;
      gap: .45rem;
      margin: .45rem 0 .8rem;
    }}
    label {{
      color: var(--muted);
      font-size: 12px;
    }}
    select {{
      width: 100%;
      border: 1px solid var(--line);
      border-radius: 4px;
      background: var(--input-bg);
      color: var(--text);
      font: inherit;
      min-height: 40px;
      padding: 0 12px;
    }}
    pre {{
      min-height: 420px;
      max-height: 70vh;
      overflow: auto;
      white-space: pre-wrap;
      word-break: break-word;
      background: var(--terminal-bg);
      border: 1px solid var(--terminal-line);
      border-radius: 16px;
      color: var(--terminal-text);
      padding: 1rem;
      line-height: 1.45;
    }}
    .run {{
      border-bottom: 1px solid var(--line);
      cursor: pointer;
      padding: .7rem 0;
    }}
    .run:last-child {{ border-bottom: 0; }}
    .status-running, .status-queued {{ color: var(--accent); }}
    .status-error {{ color: var(--danger); }}
    @media (max-width: 760px) {{
      main {{ padding: 1rem; }}
      .grid {{ grid-template-columns: 1fr; }}
    }}
  </style>
</head>
<body>
<main>
  <header>
    <h1>{escaped_title}</h1>
    <div id="status" class="muted">Loading...</div>
  </header>
  <div class="grid">
    <section class="card">
      <h2 id="commandsTitle">Commands</h2>
      <div id="commands"></div>
      <p id="hint" class="muted">Loading...</p>
      <h2 id="runsTitle">Runs</h2>
      <div id="runs"></div>
    </section>
    <section class="card">
      <h2 id="outputTitle">Output</h2>
      <pre id="output">Loading...</pre>
    </section>
  </div>
</main>
<script>
const nodeId = "{escaped_node_id}";
let state = null;
let selectedRunId = null;
const requestedOutputs = new Set();
let accessToken = null;
let texts = {{}};

function t(key, ...args) {{
  const value = texts[key];
  if (typeof value !== "string") return key;
  return value.replace(/{{{{([^}}]+)}}}}/g, (match, token) => {{
    const index = Number(token.trim());
    return Number.isInteger(index) && index < args.length ? String(args[index]) : match;
  }});
}}

function commandLabel(command) {{
  return t(command.label_key);
}}

function sectionLabel(sectionKey) {{
  const section = state?.sections?.[sectionKey];
  if (!section) return sectionKey;
  return t(section.label_key);
}}

function optionLabel(option) {{
  return t(option.label_key);
}}

function inputLabel(input) {{
  return input.label_key ? t(input.label_key) : input.name;
}}

function commandInputDefault(command) {{
  const eeprom = state?.metrics?.bootloader?.eeprom_config || {{}};
  if (command.type === "set_eeprom_track") return eeprom.track || state?.metrics?.bootloader?.track || "default";
  if (command.type === "set_eeprom_flashrom") return eeprom.flashrom || "0";
  return null;
}}

function confirmationText(command) {{
  if (command.confirmation_key) return t(command.confirmation_key, state.node.name);
  return t("confirm_generic", commandLabel(command), state.node.name);
}}

function getAuthTokens() {{
  const raw = window.localStorage.getItem("hassTokens");
  if (!raw) return null;
  try {{
    return JSON.parse(raw);
  }} catch (_err) {{
    return null;
  }}
}}

function saveAuthTokens(tokens) {{
  window.localStorage.setItem("hassTokens", JSON.stringify(tokens));
}}

async function ensureAccessToken(forceRefresh = false) {{
  const tokens = getAuthTokens();
  if (!tokens?.refresh_token) return null;
  const expires = Number(tokens.expires || 0);
  if (!forceRefresh && tokens.access_token && expires > Date.now() + 60000) {{
    accessToken = tokens.access_token;
    return accessToken;
  }}
  const body = new URLSearchParams();
  body.set("grant_type", "refresh_token");
  body.set("refresh_token", tokens.refresh_token);
  body.set("client_id", tokens.clientId || `${{window.location.origin}}/`);
  const response = await fetch("/auth/token", {{
    method: "POST",
    headers: {{"Content-Type": "application/x-www-form-urlencoded"}},
    body,
  }});
  if (!response.ok) return null;
  const refreshed = await response.json();
  const nextTokens = {{
    ...tokens,
    access_token: refreshed.access_token,
    expires_in: refreshed.expires_in,
    token_type: refreshed.token_type || tokens.token_type || "Bearer",
    expires: Date.now() + (Number(refreshed.expires_in || 1800) * 1000),
  }};
  saveAuthTokens(nextTokens);
  accessToken = nextTokens.access_token;
  return accessToken;
}}

async function apiFetch(path, options = {{}}, retry = true) {{
  const token = await ensureAccessToken(false);
  if (!token) throw new Error(t("no_token"));
  const headers = new Headers(options.headers || {{}});
  headers.set("Authorization", `Bearer ${{token}}`);
  const response = await fetch(path, {{...options, headers}});
  if (response.status === 401 && retry) {{
    const refreshed = await ensureAccessToken(true);
    if (!refreshed) throw new Error(t("no_token"));
    headers.set("Authorization", `Bearer ${{refreshed}}`);
    return fetch(path, {{...options, headers}});
  }}
  return response;
}}

function normalizeLanguageTag(language) {{
  const raw = String(language || "").trim().replace(/_/g, "-").toLowerCase();
  if (!raw) return "en";
  const parts = raw.split("-").filter(Boolean);
  return parts.length ? parts[0] : "en";
}}

async function fetchPanelTranslations(language) {{
  const response = await apiFetch(`/api/hostwatch/maintenance/i18n/${{encodeURIComponent(language)}}`, {{
    cache: "no-store",
  }});
  if (!response.ok) throw new Error(`translations failed: ${{response.status}}`);
  return response.json();
}}

async function detectLanguage() {{
  try {{
    const response = await apiFetch("/api/config", {{cache: "no-store"}});
    if (response.ok) {{
      const config = await response.json();
      if (typeof config.language === "string" && config.language) {{
        return normalizeLanguageTag(config.language);
      }}
    }}
  }} catch (_err) {{
  }}
  return normalizeLanguageTag(navigator.language);
}}

async function loadTranslations() {{
  const english = await fetchPanelTranslations("en");
  const selected = await detectLanguage();
  if (selected === "en") {{
    texts = english;
    return;
  }}
  try {{
    const localized = await fetchPanelTranslations(selected);
    texts = {{...english, ...localized}};
  }} catch (_err) {{
    texts = english;
  }}
}}

async function refreshState() {{
  const response = await apiFetch(`/api/hostwatch/maintenance/${{nodeId}}/state`, {{
    cache: "no-store",
  }});
  if (!response.ok) throw new Error(`state failed: ${{response.status}}`);
  state = await response.json();
  render();
}}

function render() {{
  const status = document.getElementById("status");
  const until = state.maintenance.enabled_until ? new Date(state.maintenance.enabled_until).toLocaleString() : t("not_active");
  status.textContent = t("status", state.node.name, state.node.online ? t("online_yes") : t("online_no"), until);

  const commands = document.getElementById("commands");
  commands.innerHTML = "";
  const grouped = new Map();
  for (const command of state.commands) {{
    if (!grouped.has(command.section)) grouped.set(command.section, []);
    grouped.get(command.section).push(command);
  }}
  for (const [sectionKey, sectionCommands] of grouped.entries()) {{
    const section = document.createElement("div");
    section.className = "command-section";
    const heading = document.createElement("h3");
    heading.textContent = sectionLabel(sectionKey);
    section.appendChild(heading);
    for (const command of sectionCommands) {{
      section.appendChild(renderCommand(command));
    }}
    commands.appendChild(section);
  }}

  const runs = document.getElementById("runs");
  runs.innerHTML = "";
  for (const run of state.runs) {{
    const row = document.createElement("div");
    row.className = "run";
    row.onclick = () => {{
      selectedRunId = run.id;
      renderOutput();
    }};
    row.innerHTML = `<strong>${{escapeHtml(run.command_type)}}</strong><br><span class="status-${{escapeHtml(run.status)}}">${{escapeHtml(run.status)}}</span> <span class="muted">${{new Date(run.created_at).toLocaleString()}}</span>`;
    runs.appendChild(row);
  }}
  if ((!selectedRunId || !state.runs.some((run) => run.id === selectedRunId)) && state.runs.length) {{
    selectedRunId = state.runs[0].id;
  }}
  renderOutput();
}}

function renderCommand(command) {{
  if (!command.input) {{
    const button = document.createElement("button");
    button.textContent = commandLabel(command);
    button.className = command.critical ? "danger" : "";
    button.disabled = !state.maintenance.enabled;
    button.onclick = () => runCommand(command);
    return button;
  }}

  const wrapper = document.createElement("div");
  wrapper.className = "command-input";
  const label = document.createElement("label");
  label.textContent = inputLabel(command.input);
  const select = document.createElement("select");
  select.disabled = !state.maintenance.enabled;
  for (const option of command.input.options || []) {{
    const entry = document.createElement("option");
    entry.value = option.value;
    entry.textContent = optionLabel(option);
    select.appendChild(entry);
  }}
  const defaultValue = commandInputDefault(command);
  if (defaultValue !== null) select.value = defaultValue;
  const button = document.createElement("button");
  button.textContent = commandLabel(command);
  button.className = command.critical ? "danger" : "";
  button.disabled = !state.maintenance.enabled;
  button.onclick = () => runCommand(command, {{[command.input.name]: select.value}});
  wrapper.appendChild(label);
  wrapper.appendChild(select);
  wrapper.appendChild(button);
  return wrapper;
}}

function renderOutput() {{
  const output = document.getElementById("output");
  const run = state?.runs?.find((item) => item.id === selectedRunId);
  if (!run) {{
    output.textContent = t("no_command");
    return;
  }}
  if (!state.maintenance.enabled) {{
    output.textContent = t("maintenance_required");
    return;
  }}
  if (typeof run.output !== "string") {{
    requestOutput(run.id);
    output.textContent = t("output_requested");
    return;
  }}
  output.textContent = `[${{run.status}}] ${{run.command_type}}\\nstarted: ${{run.created_at}}\\nfinished: ${{run.finished_at || "-"}}\\nreturncode: ${{run.returncode ?? "-"}}\\n\\n${{run.output || ""}}`;
  output.scrollTop = output.scrollHeight;
}}

async function requestOutput(runId) {{
  if (requestedOutputs.has(runId)) return;
  requestedOutputs.add(runId);
  const response = await apiFetch(`/api/hostwatch/maintenance/${{nodeId}}/output/${{runId}}`, {{
    method: "POST",
    cache: "no-store",
  }});
  if (!response.ok) return;
  const payload = await response.json();
  if (payload.status === "available") {{
    const run = state?.runs?.find((item) => item.id === runId);
    if (run) run.output = payload.output;
    renderOutput();
  }}
}}

async function runCommand(command, params = {{}}) {{
  let confirmation = null;
  if (command.critical) {{
    if (!window.confirm(confirmationText(command))) return;
    confirmation = command.type;
  }}
  const response = await apiFetch(`/api/hostwatch/maintenance/${{nodeId}}/command`, {{
    method: "POST",
    headers: {{
      "Content-Type": "application/json",
    }},
    body: JSON.stringify({{command_type: command.type, confirmation, ...params}}),
  }});
  if (!response.ok) {{
    const payload = await response.json().catch(() => ({{error: response.statusText}}));
    window.alert(payload.error || t("command_failed"));
    return;
  }}
  const payload = await response.json();
  if (payload.run?.id) {{
    selectedRunId = payload.run.id;
  }}
  await refreshState();
}}

function escapeHtml(value) {{
  return String(value).replace(/[&<>"']/g, (char) => {{
    if (char === "&") return "&amp;";
    if (char === "<") return "&lt;";
    if (char === ">") return "&gt;";
    if (char === '"') return "&quot;";
    return "&#039;";
  }});
}}

function cssVariableName(name) {{
  return name.startsWith("--") ? name : `--${{name}}`;
}}

function applyThemeVariables(theme, dark) {{
  if (!theme || typeof theme !== "object") return;
  const root = document.documentElement;
  for (const [key, value] of Object.entries(theme)) {{
    if (key === "modes" || value === null || typeof value === "object") continue;
    root.style.setProperty(cssVariableName(key), String(value));
  }}
  const mode = dark ? theme.modes?.dark : theme.modes?.light;
  if (mode && typeof mode === "object") {{
    for (const [key, value] of Object.entries(mode)) {{
      if (value === null || typeof value === "object") continue;
      root.style.setProperty(cssVariableName(key), String(value));
    }}
  }}
}}

function pickThemeName(themesPayload, dark) {{
  const candidates = [
    themesPayload?.theme,
    dark ? themesPayload?.default_dark_theme : themesPayload?.default_theme,
    themesPayload?.default_theme,
  ];
  for (const candidate of candidates) {{
    if (candidate && candidate !== "default" && themesPayload?.themes?.[candidate]) return candidate;
  }}
  const names = Object.keys(themesPayload?.themes || {{}});
  return names.length === 1 ? names[0] : null;
}}

async function loadHomeAssistantTheme(token) {{
  return new Promise((resolve) => {{
    const socket = new WebSocket(`${{wsProtocol}}//${{window.location.host}}/api/websocket`);
    const timer = window.setTimeout(() => {{
      socket.close();
      resolve();
    }}, 5000);
    socket.addEventListener("message", (event) => {{
      let payload;
      try {{
        payload = JSON.parse(event.data);
      }} catch (_err) {{
        return;
      }}
      if (payload.type === "auth_required") {{
        socket.send(JSON.stringify({{type: "auth", access_token: token}}));
        return;
      }}
      if (payload.type === "auth_ok") {{
        socket.send(JSON.stringify({{id: 1, type: "frontend/get_themes"}}));
        return;
      }}
      if (payload.id !== 1) return;
      window.clearTimeout(timer);
      if (payload.success && payload.result) {{
        const darkMode = payload.result.darkMode;
        const dark = typeof darkMode === "boolean"
          ? darkMode
          : window.matchMedia("(prefers-color-scheme: dark)").matches;
        const themeName = pickThemeName(payload.result, dark);
        applyThemeVariables(payload.result.themes?.[themeName], dark);
      }}
      socket.close();
      resolve();
    }});
    socket.addEventListener("error", () => {{
      window.clearTimeout(timer);
      resolve();
    }});
  }});
}}

const wsProtocol = window.location.protocol === "https:" ? "wss:" : "ws:";
(async () => {{
  accessToken = await ensureAccessToken(false);
  if (!accessToken) {{
    document.getElementById("status").textContent = "No Home Assistant access token found. Open this page from an authenticated Home Assistant session.";
    return;
  }}
  await loadTranslations();
  document.getElementById("commandsTitle").textContent = t("commands_title");
  document.getElementById("hint").textContent = t("hint");
  document.getElementById("runsTitle").textContent = t("runs_title");
  document.getElementById("outputTitle").textContent = t("output_title");
  document.getElementById("output").textContent = t("no_command");
  loadHomeAssistantTheme(accessToken);
  refreshState().catch((err) => {{
    document.getElementById("status").textContent = err.message;
  }});
  const events = new WebSocket(`${{wsProtocol}}//${{window.location.host}}/api/hostwatch/maintenance/${{nodeId}}/events`);
  events.addEventListener("open", () => {{
    events.send(JSON.stringify({{type: "auth", access_token: accessToken}}));
  }});
  events.addEventListener("message", (event) => {{
    if (!state) return;
    const payload = JSON.parse(event.data);
    if (payload.type !== "runs") return;
    state.runs = payload.runs;
    if ((!selectedRunId || !state.runs.some((run) => run.id === selectedRunId)) && state.runs.length) {{
      selectedRunId = state.runs[0].id;
    }}
    render();
  }});
  setInterval(() => {{
    if (!state?.runs?.some((run) => run.status === "queued" || run.status === "running")) return;
    refreshState().catch((err) => {{
      document.getElementById("status").textContent = err.message;
    }});
  }}, 2000);
}})();
</script>
</body>
</html>"""


def _normalize_language_tag(language: str) -> str:
    normalized = language.strip().replace("_", "-").lower()
    if not normalized:
        return "en"
    return normalized.split("-", 1)[0]


def _load_panel_translations(language: str) -> dict[str, str]:
    path = PANEL_TRANSLATIONS_DIR / f"{language}.json"
    if not path.exists():
        path = PANEL_TRANSLATIONS_DIR / "en.json"
    with path.open(encoding="utf-8") as handle:
        data = json.load(handle)
    return {str(key): str(value) for key, value in data.items()}
