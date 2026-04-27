# Nocturne for Home Assistant

A [HACS](https://hacs.xyz/) custom integration that connects [Home Assistant](https://www.home-assistant.io/) to [Nocturne](https://github.com/nightscout/nocturne), a diabetes management API.

## Features

- Real-time glucose, IOB, COB, and loop status sensors
- Device status sensors (pump reservoir, battery, CGM signal)
- Report sensors (time in range)
- Inbound services for logging carbs, insulin, glucose, and activity from HA automations
- OAuth 2.0 authentication with PKCE

## Installation

1. Install [HACS](https://hacs.xyz/) if you haven't already
2. Add this repository as a custom repository in HACS (Integration category)
3. Install "Nocturne" from HACS
4. Restart Home Assistant
5. Go to Settings > Devices & Services > Add Integration > Nocturne
6. Enter your Nocturne instance URL and complete the OAuth flow

## Sensors

Sensors are registered dynamically based on available data. CGM-only users will see glucose sensors; loop users will see the full set.

| Sensor | Unit | Updates |
|--------|------|---------|
| Current Glucose | mg/dL | 60s |
| Glucose Trend | -- | 60s |
| IOB | U | 60s |
| COB | g | 60s |
| Predicted BG | mg/dL | 60s |
| Loop Status | -- | 60s |
| Active Basal Rate | U/hr | 60s |
| Pump Reservoir | U | 5m |
| Pump Battery | % | 5m |
| CGM Battery | % | 5m |
| CGM Signal | dB | 5m |
| Sensor Age | days | 5m |
| Active Profile | -- | 5m |
| Time in Range | % | 5m |

## Services

| Service | Fields |
|---------|--------|
| `nocturne.log_carbs` | `carbs` (g), `notes` (optional) |
| `nocturne.log_insulin` | `insulin` (U), `notes` (optional) |
| `nocturne.log_glucose` | `value` (mg/dL), `type` (sgv/mbg) |
| `nocturne.log_activity` | `duration` (min), `activity_type`, `notes` (optional) |

## License

AGPL-3.0
