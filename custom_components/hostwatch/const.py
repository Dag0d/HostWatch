"""Constants for the HostWatch integration."""

DOMAIN = "hostwatch"
PLATFORMS: list[str] = ["sensor", "binary_sensor", "button", "update"]

DEFAULT_PORT = 48221
PAIRING_TIMEOUT_SECONDS = 300
PAIRING_APPROVAL_WAIT_SECONDS = 120
MAINTENANCE_MODE_SECONDS = 30 * 60
NODE_OFFLINE_AFTER_SECONDS = 70
AGENT_RELEASE_REFRESH_SECONDS = 6 * 60 * 60
MAX_COMMAND_RUNS_PER_NODE = 10
MAX_COMMAND_RUNS_PER_COMMAND = 2
AGENT_RELEASE_REPOSITORY = "Dag0d/HostWatch-Agent"
AGENT_RELEASE_LATEST_URL = f"https://api.github.com/repos/{AGENT_RELEASE_REPOSITORY}/releases/latest"
AGENT_RELEASE_MANIFEST_PREFIX = "hostwatch-agent-manifest-"
AGENT_RELEASE_TARBALL_PREFIX = "hostwatch-agent-"

CONF_NODE_ID = "node_id"
CONF_NODE_UID = "node_uid"
CONF_NODE_NAME = "node_name"
CONF_NODE_SECRET = "node_secret"
CONF_HA_NAME = "ha_name"
CONF_HA_URL = "ha_url"
CONF_HA_URL_MODE = "ha_url_mode"
CONF_HA_INSTANCE_ID = "ha_instance_id"
CONF_HEARTBEAT_WEBHOOK_ID = "heartbeat_webhook_id"
CONF_METRICS_WEBHOOK_ID = "metrics_webhook_id"
CONF_COMMAND_RESULT_WEBHOOK_ID = "command_result_webhook_id"
CONF_COMMAND_POLL_WEBHOOK_ID = "command_poll_webhook_id"
CONF_HEARTBEAT_WEBHOOK_URL = "heartbeat_webhook_url"
CONF_METRICS_WEBHOOK_URL = "metrics_webhook_url"
CONF_COMMAND_RESULT_WEBHOOK_URL = "command_result_webhook_url"
CONF_COMMAND_POLL_WEBHOOK_URL = "command_poll_webhook_url"

SERVICE_GET_APT_SUMMARY = "get_apt_summary"
SERVICE_GET_BOOTLOADER_SUMMARY = "get_bootloader_summary"
SERVICE_REFRESH_AGENT_UPDATES = "refresh_agent_updates"

STORAGE_VERSION = 1
STORAGE_KEY = f"{DOMAIN}_nodes"

PAIRING_ROUTE_INFO = "/api/hostwatch/pairing/info"
PAIRING_ROUTE_REQUEST = "/api/hostwatch/pairing/request"
PAIRING_ROUTE_COMPLETE = "/api/hostwatch/pairing/complete"

SIGNAL_NODE_UPDATED = f"{DOMAIN}_node_updated_{{node_id}}"
SIGNAL_COMMAND_RUN_UPDATED = f"{DOMAIN}_command_run_updated_{{node_id}}"
SIGNAL_AGENT_RELEASE_UPDATED = f"{DOMAIN}_agent_release_updated"
