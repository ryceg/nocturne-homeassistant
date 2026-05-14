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
    "entries.readwrite",
    "treatments.readwrite",
    "devicestatus.read",
    "profile.read",
    "heartrate.readwrite",
    "stepcount.readwrite",
    "alerts.read",
]

CONF_NOTIFY_SERVICES = "notify_services"

EVENT_NOCTURNE_ALERT = "nocturne_alert"
EVENT_NOCTURNE_ALERT_RESOLVED = "nocturne_alert_resolved"
EVENT_NOCTURNE_ALERT_ACKNOWLEDGED = "nocturne_alert_acknowledged"

SIGNALR_HUB_PATH = "/hubs/home-assistant"

DATA_SOURCE_HOME_ASSISTANT = "home-assistant-connector"
