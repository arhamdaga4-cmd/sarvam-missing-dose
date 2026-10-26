"""Run a complete check-in with scripted patient replies.

Used by run_checkup.py for demos and by the tests. Works with the real Sarvam
client (needs a key) or the offline stand-in (no key, deterministic).
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from . import config, patient_db
from .conversation import ConversationSession
from .escalation import Escalation, evaluate
from .notifier import dispatch
from .offline_client import OfflineSarvam
from .sarvam_client import SarvamClient

DEMO_RESPONSES = {
    "P001": [
        "हाँ, मैंने आज सुबह Metformin ली थी।",
        "Amlodipine भी ली, लेकिन कल रात भूल गया था।",
        "थोड़ा सिरदर्द है, बाकी ठीक हूँ।",
        "हाँ, मैं अपॉइंटमेंट पर जरूर आऊंगा।",
    ],
    "P002": [
        "வணக்கம். நான் நேற்று மருந்து சாப்பிடவில்லை.",
        "இரண்டு நாட்களாக மருந்து எடுக்கவில்லை.",
        "நெஞ்சில் கொஞ்சம் அசௌகரியமாக உள்ளது.",
        "Appointment-க்கு வர முயற்சிக்கிறேன்.",
    ],
    "P003": [
        "હા, હું Aspirin દરરોજ સવારે લઉં છું.",
        "કોઈ નવા લક્ષણ નથી, સારું લાગી રહ્યું છે.",
        "હા, ચોક્કસ appointment પર આવીશ.",
    ],
}
DEFAULT_RESPONSES = [
    "Yes, I am taking my medicines regularly.",
    "No new symptoms, I feel okay.",
    "Yes, I will attend the appointment.",
]


@dataclass
class CallResult:
    patient: dict
    transcript: list[dict] = field(default_factory=list)
    report: dict = field(default_factory=dict)
    escalation: Escalation | None = None
    checkin_id: int | None = None
    engine: str = "sarvam"

    def as_dict(self) -> dict:
        return {
            "patient_id": self.patient.get("id"),
            "patient_name": self.patient.get("name"),
            "engine": self.engine,
            "transcript": self.transcript,
            "report": self.report,
            "escalation": (self.escalation.as_dict() if self.escalation else None),
            "checkin_id": self.checkin_id,
        }


def _client_for(patient: dict, offline: bool, client: Any = None):
    if client is not None:
        return client
    if offline or not config.have_sarvam_key():
        return OfflineSarvam(patient)
    return SarvamClient()


def simulate_call(
    patient_id: str,
    responses: list[str] | None = None,
    offline: bool = False,
    client: Any = None,
    verbose: bool = True,
    db_path=None,
    escalate: bool = True,
) -> CallResult:
    patient = patient_db.get_patient(patient_id, db_path=db_path)
    if not patient:
        raise KeyError(f"Patient {patient_id} not found")

    client = _client_for(patient, offline, client)
    engine = getattr(client, "engine", "sarvam")
    session = ConversationSession(patient, client=client)

    if verbose:
        print(f"\n--- Check-in: {patient['name']} ({patient_id}) [{patient['language_code']}] engine={engine} ---")

    checkin_id = patient_db.start_checkin(patient_id, channel="simulator", engine=engine, db_path=db_path)

    lines = [session.opening()]
    if verbose:
        print(f"AGENT: {lines[-1]}")
    for reply in (responses if responses is not None else DEMO_RESPONSES.get(patient_id, DEFAULT_RESPONSES)):
        if session.finished:
            break
        if verbose:
            print(f"PATIENT: {reply}")
        agent_line = session.respond(reply)
        if agent_line:
            lines.append(agent_line)
            if verbose:
                print(f"AGENT: {agent_line}")
    if verbose:
        print("--- call ended ---")

    report = session.build_report()
    escalation = evaluate(report, patient)
    if verbose and report.get("summary"):
        print(f"SUMMARY: {report['summary']}")
    patient_db.finish_checkin(
        checkin_id, report, session.transcript,
        escalation_level=escalation.level, completed=True, db_path=db_path,
    )
    if escalate:
        dispatch(escalation, patient, report, checkin_id, db_path=db_path)

    return CallResult(
        patient=patient,
        transcript=session.transcript,
        report=report,
        escalation=escalation,
        checkin_id=checkin_id,
        engine=engine,
    )


def simulate_all(offline: bool = False, verbose: bool = True, db_path=None) -> list[CallResult]:
    results = []
    for patient in patient_db.list_patients(db_path=db_path):
        results.append(simulate_call(patient["id"], offline=offline, verbose=verbose, db_path=db_path))
    return results


if __name__ == "__main__":  # pragma: no cover
    import sys

    pid = sys.argv[1] if len(sys.argv) > 1 else "P001"
    result = simulate_call(pid, offline="--offline" in sys.argv)
    print(json.dumps(result.report, ensure_ascii=False, indent=2))
