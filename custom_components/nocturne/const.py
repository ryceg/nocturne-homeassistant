"""Constants for the Nocturne integration."""

DOMAIN = "nocturne"

CONF_INSTANCE_URL = "instance_url"

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
]

DATA_SOURCE_HOME_ASSISTANT = "home-assistant-connector"
