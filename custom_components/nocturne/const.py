"""Constants for the Nocturne integration."""

DOMAIN = "nocturne"

CONF_INSTANCE_URL = "instance_url"
CONF_CLIENT_ID = "client_id"
CONF_AUTHORIZE_URL = "authorize_url"
CONF_TOKEN_URL = "token_url"

SOFTWARE_ID = "io.home-assistant.nocturne"

GLUCOSE_UPDATE_INTERVAL_SECONDS = 60
DEVICE_UPDATE_INTERVAL_SECONDS = 300
SENSOR_RECHECK_INTERVAL_SECONDS = 86400

DEFAULT_SCOPES = [
    "glucose.readwrite",
    "treatments.readwrite",
    "devices.read",
    "therapy.read",
    "reports.read",
    "heartrate.readwrite",
    "stepcount.readwrite",
    "alerts.readwrite",
]

CONF_NOTIFY_SERVICES = "notify_services"
CONF_REQUESTED_SCOPES = "requested_scopes"

EVENT_NOCTURNE_ALERT = "nocturne_alert"
EVENT_NOCTURNE_ALERT_RESOLVED = "nocturne_alert_resolved"
EVENT_NOCTURNE_ALERT_ACKNOWLEDGED = "nocturne_alert_acknowledged"

SIGNALR_HUB_PATH = "/hubs/home-assistant"

DATA_SOURCE_HOME_ASSISTANT = "home-assistant-connector"


def missing_scopes(entry_data: dict) -> list[str]:
    """Return the scopes in DEFAULT_SCOPES this entry was never asked for.

    Entries made before requested scopes were recorded fall back to what the token was granted.
    """
    requested = entry_data.get(CONF_REQUESTED_SCOPES)
    if requested is None:
        granted = entry_data["token"].get("scope")
        if granted is None:
            return []
        requested = granted.split()
    return [scope for scope in DEFAULT_SCOPES if scope not in requested]
