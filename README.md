# HostWatch

HostWatch is the Home Assistant custom integration for Linux hosts monitored by the separate HostWatch agent.

The agent repository lives here:

- Agent: [github.com/Dag0d/HostWatch-Agent](https://github.com/Dag0d/HostWatch-Agent)

## Features

HostWatch currently provides:

- SSDP-based pairing discovery or manual pairing by IP address or hostname
- periodic heartbeats and metric uploads from each node
- sensors for CPU, load, memory, root filesystem, uptime, and optional temperatures
- APT update checks
- Raspberry Pi bootloader checks
- a Home Assistant integrated maintenance panel with live output
- a Home Assistant `update` entity for signed HostWatch agent releases
- weekly summaries for APT and Raspberry Pi bootloader updates
- HACS-ready packaging for the integration repository

## Repository Contents

- [`custom_components/hostwatch`](custom_components/hostwatch): the Home Assistant integration
- [`docs/architecture.md`](docs/architecture.md): architecture and security model
- [`docs/agent_updates.md`](docs/agent_updates.md): how the integration consumes signed agent releases
- [`hacs.json`](hacs.json): HACS metadata

## Installation

Copy `custom_components/hostwatch` into your Home Assistant configuration directory under:

```text
config/custom_components/hostwatch
```

Then:

1. restart Home Assistant
2. add the `HostWatch` integration
3. install and pair the agent on your Linux node

For agent installation, use the separate repository:

- [github.com/Dag0d/HostWatch-Agent](https://github.com/Dag0d/HostWatch-Agent)

## HACS

This repository is structured to work as a HACS custom integration repository:

- `custom_components/hostwatch` contains the integration
- `hacs.json` is present at the repository root
- Hassfest workflow is included
- HACS validation workflow is included

HACS should track only this repository's releases, while HostWatch agent updates are sourced from the separate agent repository.

## Documentation

- Architecture and security model: [`docs/architecture.md`](docs/architecture.md)
- Signed agent update consumption: [`docs/agent_updates.md`](docs/agent_updates.md)
- Agent repository: [github.com/Dag0d/HostWatch-Agent](https://github.com/Dag0d/HostWatch-Agent)
