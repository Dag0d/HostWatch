# Signed Agent Updates

HostWatch agent updates are distributed through the separate HostWatch agent repository and exposed in Home Assistant through the standard `update` entity.

Agent repository:

- [github.com/Dag0d/HostWatch-Agent](https://github.com/Dag0d/HostWatch-Agent)

## Security Model

- Home Assistant does not upload code directly to a node.
- Home Assistant only queues the allowlisted `agent_update` command with a target version.
- The node downloads release assets itself from the official GitHub release in the agent repository.
- The node verifies a detached signature over the release manifest with the built-in public key in `agent/release_signing_public.pem`.
- The manifest contains the tarball URL and SHA256. The node verifies the tarball against that signed SHA256 before installing anything.

This means a compromised Home Assistant instance cannot turn the agent update path into arbitrary remote code execution unless it also has the release signing key.

## Repository Split

The Home Assistant integration and the HostWatch agent now use separate repositories:

- this repository: Home Assistant integration and HACS releases
- `HostWatch-Agent`: signed agent releases

This keeps HACS integration updates and agent software updates independent.

## Required GitHub Secret

Create this secret in the separate agent repository under:

`GitHub repository -> Settings -> Secrets and variables -> Actions -> New repository secret`

Secret name:

```text
HOSTWATCH_RELEASE_SIGNING_KEY_PEM
```

Secret value:

- the complete PEM-encoded private key
- including the `-----BEGIN PRIVATE KEY-----` and `-----END PRIVATE KEY-----` lines

Example generation commands:

```sh
openssl genpkey -algorithm RSA -pkeyopt rsa_keygen_bits:4096 -out hostwatch-release-signing-private.pem
openssl pkey -in hostwatch-release-signing-private.pem -pubout -out agent/release_signing_public.pem
```

Commit only the public key. Never commit the private key.

## Release Flow

1. Update the agent version in the `HostWatch-Agent` repository.
2. Create and publish a GitHub Release there with the matching tag, for example `2026.4.2`.
3. GitHub Actions builds:
   - `hostwatch-agent-<version>.tar.gz`
   - `hostwatch-agent-manifest-<version>.json`
   - `hostwatch-agent-manifest-<version>.sig`
4. The workflow uploads those three assets to that release.
5. This integration checks the latest GitHub release in `HostWatch-Agent` and exposes the agent update through the built-in update entity.

## Installed Files on the Node

The signed tarball currently contains only the strict allowlist below:

- `hostwatch_agent.py`
- `install.sh`
- `release_signing_public.pem`

The agent installs only those files into its current install directory and then restarts its own systemd service.

## Test-Only Release Refresh Button

The Home Assistant button that forces an immediate agent release refresh is hidden by default.

It can be enabled for testing by setting this environment variable for Home Assistant:

```text
HOSTWATCH_SHOW_AGENT_RELEASE_REFRESH_BUTTON=1
```

Without that variable, only the normal background refresh cycle is used.

## Test-Only Release Refresh Button

The Home Assistant button that forces an immediate agent release refresh is hidden by default.

It can be enabled for testing by setting this environment variable for Home Assistant:

```text
HOSTWATCH_SHOW_AGENT_RELEASE_REFRESH_BUTTON=1
```

Without that variable, only the normal background refresh cycle is used.
