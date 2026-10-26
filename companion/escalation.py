"""Turn a check-in report into an escalation decision.

Three levels:
  urgent - a human must call the patient back today
  soon   - coordinator should follow up within a day
  info   - worth logging, no action needed
  none   - nothing to report
"""
from __future__ import annotations

from dataclasses import dataclass, field

from . import config

LEVEL_ORDER = {"none": 0, "info": 1, "soon": 2, "urgent": 3}


@dataclass
class Escalation:
    level: str = "none"
    reasons: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {"level": self.level, "reasons": self.reasons}

    @property
    def needs_human(self) -> bool:
        return self.level in ("soon", "urgent")


def _symptom_text(report: dict) -> str:
    parts = []
    for item in report.get("symptoms") or []:
        if isinstance(item, dict):
            parts.append(f"{item.get('name', '')} ({item.get('severity', 'unspecified')})")
        else:
            parts.append(str(item))
    parts.extend(str(x) for x in report.get("urgent_flags") or [])
    parts.append(str(report.get("missed_dose_reason") or ""))
    return " | ".join(parts).lower()


def evaluate(report: dict | None, patient: dict | None = None,
             missed_checkin_days: int = 0) -> Escalation:
    if not report:
        return Escalation("none", [])
    if report.get("parse_error"):
        return Escalation("soon", ["Check-in report could not be parsed - read the transcript"])

    reasons: list[str] = []
    level = "none"

    def bump(new_level: str, reason: str) -> None:
        nonlocal level
        if reason not in reasons:
            reasons.append(reason)
        if LEVEL_ORDER[new_level] > LEVEL_ORDER[level]:
            level = new_level

    text = _symptom_text(report)

    # 1. Urgent symptom keywords, in any of the supported languages.
    hits = [kw for kw in config.URGENT_SYMPTOM_KEYWORDS if kw.lower() in text]
    if hits:
        bump("urgent", "Urgent symptom mentioned: " + ", ".join(sorted(set(hits))))

    # 2. Model-flagged severity.
    for item in report.get("symptoms") or []:
        if isinstance(item, dict) and str(item.get("severity", "")).lower() == "severe":
            bump("urgent", f"Severe symptom: {item.get('name', 'unspecified')}")
        elif isinstance(item, dict) and str(item.get("severity", "")).lower() == "moderate":
            bump("soon", f"Moderate symptom: {item.get('name', 'unspecified')}")

    if report.get("urgent_flags"):
        bump("urgent", "Model flagged urgent: " + ", ".join(str(x) for x in report["urgent_flags"]))

    # 3. Adherence.
    missed = report.get("missed_doses")
    try:
        missed = int(missed) if missed is not None else 0
    except (TypeError, ValueError):
        missed = 0
    if missed >= config.MISSED_DOSE_ESCALATION_THRESHOLD:
        bump("soon", f"{missed} doses missed")
    elif missed == 1:
        bump("info", "One dose missed")
    if report.get("medication_taken") is False and missed == 0:
        bump("soon", "Patient said medicines were not taken today")

    # 4. Appointment.
    if report.get("appointment_ok") is False:
        bump("soon", "Patient cannot attend the appointment")
    if report.get("needs_transport_help"):
        bump("info", "Patient needs transport help")

    # 5. Repeated non-response across days.
    if missed_checkin_days >= 3:
        bump("soon", f"No completed check-in for {missed_checkin_days} days")

    # 6. Side effects on their own are informational.
    if report.get("side_effects") and level == "none":
        bump("info", "Side effects reported: " + ", ".join(str(x) for x in report["side_effects"]))

    return Escalation(level, reasons)


def describe(escalation: Escalation, patient: dict | None = None) -> str:
    name = (patient or {}).get("name", "patient")
    if escalation.level == "none":
        return f"No escalation for {name}."
    head = {"urgent": "URGENT", "soon": "FOLLOW-UP", "info": "NOTE"}[escalation.level]
    return f"[{head}] {name}: " + "; ".join(escalation.reasons)
