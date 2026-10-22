"""Every user-facing string the integration can show has a translation."""

from __future__ import annotations

import inspect
import json
import re
from pathlib import Path

from homeassistant.helpers import config_entry_oauth2_flow

from custom_components.nocturne import config_flow
from custom_components.nocturne.sensor import ALL_SENSORS

COMPONENT = Path(config_flow.__file__).parent
STRINGS = json.loads((COMPONENT / "strings.json").read_text(encoding="utf-8"))
FLOW_SOURCE = inspect.getsource(config_flow)
OAUTH_SOURCE = inspect.getsource(config_entry_oauth2_flow.AbstractOAuth2FlowHandler)


HELPER_ABORT_REASONS = {
    "already_configured",
    "already_in_progress",
    "reauth_successful",
    "oauth_error",
    "oauth_failed",
    "oauth_timeout",
    "oauth_unauthorized",
    "missing_configuration",
    "missing_credentials",
    "authorize_url_timeout",
    "no_url_available",
    "oauth_implementation_unavailable",
    "user_rejected_authorize",
}


def _literals(pattern: str, source: str) -> set[str]:
    return set(re.findall(pattern, source))


def test_english_translation_equals_strings():
    translated = json.loads((COMPONENT / "translations" / "en.json").read_text(encoding="utf-8"))
    assert translated == STRINGS


def test_every_sensor_has_a_translated_name():
    names = STRINGS["entity"]["sensor"]
    for description in ALL_SENSORS:
        assert description.translation_key, description.key
        assert names[description.translation_key]["name"], description.key


def test_every_abort_reason_has_a_string():
    reasons = _literals(r'reason="(\w+)"', FLOW_SOURCE) | _literals(r'reason="(\w+)"', OAUTH_SOURCE)
    assert reasons
    assert reasons <= set(STRINGS["config"]["abort"])


def test_every_abort_reason_home_assistant_raises_for_this_flow_has_a_string():
    assert HELPER_ABORT_REASONS <= set(STRINGS["config"]["abort"])


def test_every_flow_error_has_a_string():
    errors = _literals(r'"base"(?:\]\s*=|:)\s*"(\w+)"', FLOW_SOURCE)
    assert errors
    assert errors <= set(STRINGS["config"]["error"])


def test_every_form_step_has_a_string():
    steps = _literals(r'step_id="(\w+)"', FLOW_SOURCE)
    assert steps
    assert steps <= set(STRINGS["config"]["step"]) | set(STRINGS["options"]["step"])
