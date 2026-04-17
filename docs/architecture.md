# HostWatch Architecture

## Goal

HostWatch consists of two components:

- a Home Assistant custom integration
- a Python agent for Linux hosts

The agent collects local system state, executes a small fixed set of maintenance actions, and pairs with Home Assistant through a one-time pairing flow.

## System Overview

### Agent

The agent runs as a local process or as a systemd service on the target host. It:

- collects CPU, memory, filesystem, temperature, and update data
- detects Raspberry Pi-specific capabilities such as bootloader checks
- actively sends heartbeats and metrics to Home Assistant
- actively polls for pending commands
- stores only the last two command outputs per command type locally on the node

The agent uses only the Python standard library plus local system programs such as `python3`, `openssl`, `systemctl`, `apt-get`, and on Raspberry Pi, `rpi-eeprom-update`.

### Home Assistant Integration

The custom integration manages:

- the pairing flow
- SSDP discovery
- manual add by IP address or hostname
- webhooks for heartbeat, metrics, command polling, and command results
- sensors, binary sensors, and the maintenance mode button
- update entities for signed agent releases
- the proxied maintenance panel inside Home Assistant
- weekly APT and Raspberry Pi bootloader summaries

## Discovery and Pairing

### Discovery Paths

HostWatch supports two entry points:

1. SSDP discovery for automatic detection in Home Assistant
2. manual add by IP address or hostname

Avahi or mDNS can be added optionally, but it is not required. SSDP is the primary discovery mechanism.

### Pairing Flow

1. The node starts `hostwatch-agent pair` or `python3 hostwatch_agent.py pair` locally.
2. The agent starts a temporary HTTPS pairing server for 5 minutes.
3. During that time, the agent sends SSDP announcements and exposes a small HTTPS API for pairing.
4. Home Assistant calls `/api/hostwatch/pairing/info` and reads the node's basic information.
5. The agent can also report a preferred Home Assistant URL mode: `local` or `external`.
6. Home Assistant sends a concrete pairing request to `/api/hostwatch/pairing/request`, including the Home Assistant name and URL.
7. The node displays the pairing code, Home Assistant name, requested URL, and source IP locally.
8. The user confirms pairing locally on the node.
9. Home Assistant creates `node_id`, `node_secret`, and the four webhook URLs and sends them to `/api/hostwatch/pairing/complete`.
10. The node stores the configuration in `agent.json`, exits pairing mode, and closes the temporary pairing port.

## Communication Model During Normal Runtime

HostWatch does not use an inbound management connection from Home Assistant to the node during normal runtime. Instead, the agent actively opens the connection to Home Assistant.

### Direction: Node to Home Assistant

The agent periodically sends:

- heartbeats
- metric updates
- command status and command output

### Direction: Home Assistant to Node

Home Assistant exposes webhook endpoints only. The agent actively polls the command-poll webhook and retrieves exactly one pending command at a time.

Runtime communication uses:

- `node_id` for identification
- `node_secret` for authentication

There is no arbitrary remote shell, no arbitrary command submission, and no generic file manipulation.

## Maintenance Model

### Maintenance Mode

Critical actions are available only during time-limited maintenance mode. Maintenance mode:

- is enabled per node in Home Assistant
- automatically expires after 30 minutes
- is required for the maintenance panel and output access

### Maintenance Panel

The maintenance panel is hosted by Home Assistant and served through the existing Home Assistant web server. It uses:

- normal HTTP endpoints for state, command start, and output requests
- a Home Assistant proxied WebSocket for live updates
- admin authentication through the current Home Assistant session

Command output is not stored permanently in Home Assistant. Home Assistant keeps only run metadata in storage and optional temporary output in memory. The last two outputs per command type stay local on the node.

### Command Allowlist

The agent executes only a fixed set of commands, including:

- `refresh_apt_check`
- `apt_upgrade`
- `refresh_bootloader_check`
- `bootloader_upgrade`
- `set_eeprom_track`
- `set_eeprom_flashrom`
- `agent_update`
- `reboot`
- `shutdown`

Not every node exposes every command. Raspberry Pi-specific features appear only on matching nodes, and `RPI_EEPROM_USE_FLASHROM` is offered only on Raspberry Pi 5.

## Data Model in Home Assistant

The integration creates entities only for data that the node actually provides. That includes:

- sensors for CPU, memory, root filesystem, uptime, APT status, and optional temperatures
- binary sensors for online state, APT updates, and Raspberry Pi bootloader updates
- a button to enable maintenance mode
- an update entity for the signed HostWatch agent release channel

Entity IDs follow the pattern `hostwatch_<node_name>_<entity>`.

## Signed Agent Updates

Agent updates use Home Assistant's built-in update entity instead of the maintenance panel.

The flow is:

1. Home Assistant checks the latest GitHub release that contains a signed agent tarball, signed manifest, and detached signature.
2. Home Assistant exposes that as an update entity for every paired node.
3. When the user starts the update, Home Assistant queues only the allowlisted `agent_update` command with the target version.
4. The node downloads the signed manifest and tarball itself from the official GitHub release.
5. The node verifies the manifest signature with its built-in public key.
6. The node verifies the tarball SHA256 against the signed manifest.
7. The node replaces only the expected HostWatch files in its install directory and restarts its own service.

This keeps Home Assistant out of the code-delivery path. Home Assistant can request an update, but the node makes the final trust decision locally.

## Security Model

- Pairing is locally confirmed and time-limited.
- The pairing port is open only while pairing mode is active.
- Every node has its own `node_secret`.
- Runtime communication happens only through known webhook endpoints.
- The agent does not accept arbitrary commands from Home Assistant.
- Agent updates are accepted only from signed release manifests and verified tarballs.
- Critical maintenance actions require both maintenance mode and Home Assistant admin rights.
- Command output stays primarily on the node and is fetched only while maintenance mode is active.

This model is intended for trusted private networks, not for hostile internet exposure without additional controls such as reverse proxy hardening, WAF rules, or separate access paths.

## Runtime and Operations

### Agent

- no third-party Python dependencies
- compatible with systemd service operation
- preferably runs with root privileges as a service so maintenance actions like `apt-get`, `systemctl`, and `rpi-eeprom-update` work reliably
- falls back to `sudo -n` for privileged commands when not running as root and when `sudo` is available

### Home Assistant

The integration uses:

- Config Flow
- SSDP discovery
- webhooks
- the device registry
- the entity registry
- weekly notifications through `persistent_notification`
- an internal maintenance endpoint at `/hostwatch/maintenance/<node_id>`

## Current Release Readiness

The current state is release-ready for the implemented feature set:

- pairing through SSDP or manual add
- regular heartbeats and metrics
- maintenance panel with proxied live updates
- weekly APT and Raspberry Pi bootloader summaries
- APT and Raspberry Pi bootloader maintenance
- installer for local and remote installation

## Open Topics for Later Releases

1. External Home Assistant URLs behind reverse proxies or Cloudflare need dedicated operating documentation and troubleshooting notes.
2. Additional platform-specific commands should be added only when they remain clearly allowlisted and tightly constrained.
