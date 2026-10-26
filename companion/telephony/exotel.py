"""Outbound calling via Exotel's Connect Two Numbers API.

Docs: https://developer.exotel.com/docs/voice-v1/api-reference/connect-two-numbers

Exotel dials `From` first, then `To`, and bridges the two legs. For a bot call
you attach AgentStream to the customer leg with `StreamUrl` +
`StreamBegin=at Leg2Connect`; our WebSocket then gets the bidirectional media.

Note on `From`: the API requires it and it is dialled first. Two workable
setups:
  1. Set EXOTEL_BRIDGE_NUMBER to a softphone/IVR line that answers instantly.
  2. Put a Voicebot applet (pointing at our /stream URL) in a call flow and
     pass the flow URL as EXOTEL_FLOW_URL - then no bridge leg is needed.
Check which one your Exotel account/plan supports before going live.
"""
from __future__ import annotations

import logging
from typing import Any

import requests

from . import config

log = logging.getLogger(__name__)

CONNECT_PATH = "/v1/Accounts/{sid}/Calls/connect"


class ExotelError(RuntimeError):
    pass


def _auth() -> tuple[str, str]:
    if not (config.EXOTEL_API_KEY and config.EXOTEL_API_TOKEN):
        raise config.ConfigError(
            "EXOTEL_API_KEY / EXOTEL_API_TOKEN are not set. Copy them from the "
            "Exotel dashboard -> Settings -> API credentials."
        )
    if not config.EXOTEL_ACCOUNT_SID:
        raise config.ConfigError("EXOTEL_ACCOUNT_SID is not set.")
    return config.EXOTEL_API_KEY, config.EXOTEL_API_TOKEN


def place_call(
    patient: dict,
    stream_url: str | None = None,
    record: bool = True,
    time_limit: int = 420,
    extra: dict[str, Any] | None = None,
) -> dict:
    """Dial a patient and hand the answered leg to our WebSocket bridge."""
    key, token = _auth()
    if not config.EXOTEL_CALLER_ID:
        raise config.ConfigError("EXOTEL_CALLER_ID (your ExoPhone) is not set.")
    to_number = patient.get("phone")
    if not to_number:
        raise ExotelError(f"Patient {patient.get('id')} has no phone number")

    form: dict[str, Any] = {
        "To": to_number,
        "CallerId": config.EXOTEL_CALLER_ID,
        "CallType": "trans",
        "TimeLimit": time_limit,
        "Record": "true" if record else "false",
        "StatusCallbackContentType": "application/json",
        "CustomField": f"patient_id={patient.get('id')}"[:128],
    }
    if config.EXOTEL_BRIDGE_NUMBER:
        form["From"] = config.EXOTEL_BRIDGE_NUMBER
    if config.EXOTEL_FLOW_URL:
        form["Url"] = config.EXOTEL_FLOW_URL
    if stream_url:
        form["StreamUrl"] = stream_url
        form["StreamBegin"] = "at Leg2Connect"
    if config.PUBLIC_BASE_URL:
        form["StatusCallback"] = config.PUBLIC_BASE_URL.rstrip("/") + "/exotel/status"
        form["StatusCallbackEvents[0]"] = "terminal"
        form["StatusCallbackEvents[1]"] = "answered"
    if extra:
        form.update(extra)
    if "From" not in form:
        raise config.ConfigError(
            "Set EXOTEL_BRIDGE_NUMBER (the number Exotel dials first) or "
            "EXOTEL_FLOW_URL (a call flow containing a Voicebot applet)."
        )

    url = f"https://{config.EXOTEL_SUBDOMAIN}{CONNECT_PATH.format(sid=config.EXOTEL_ACCOUNT_SID)}"
    try:
        resp = requests.post(url, auth=(key, token), data=form, timeout=30,
                             headers={"Accept": "application/json"})
    except requests.RequestException as exc:
        raise ExotelError(f"Could not reach Exotel: {exc}") from exc

    if resp.status_code >= 300:
        raise ExotelError(f"Exotel returned {resp.status_code}: {resp.text[:400]}")
    try:
        payload = resp.json()
    except ValueError:
        payload = {"raw": resp.text[:400]}
    log.info("Exotel call placed for %s: %s", patient.get("id"), payload)
    return payload


def call_patient(patient: dict, **kwargs) -> dict:
    """place_call with the stream URL derived from config."""
    return place_call(patient, stream_url=config.stream_url_for_patient(patient["id"]), **kwargs)
