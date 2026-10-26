import pytest

from companion import config
from companion.escalation import describe, evaluate


def test_no_report_means_no_escalation():
    assert evaluate(None).level == "none"
    assert evaluate({"medication_taken": True, "missed_doses": 0}).level == "none"


def test_chest_pain_is_urgent_in_hindi():
    report = {"urgent_flags": [], "symptoms": [{"name": "सीने में दर्द", "severity": "moderate"}]}
    esc = evaluate(report)
    assert esc.level == "urgent"
    assert any("सीने में दर्द" in r for r in esc.reasons)


def test_severe_symptom_is_urgent():
    esc = evaluate({"symptoms": [{"name": "breathlessness", "severity": "severe"}]})
    assert esc.level == "urgent"


def test_two_missed_doses_needs_followup():
    esc = evaluate({"missed_doses": 2})
    assert esc.level == "soon"


def test_one_missed_dose_is_informational():
    esc = evaluate({"missed_doses": 1})
    assert esc.level == "info"


def test_medicines_not_taken_is_followup():
    esc = evaluate({"medication_taken": False, "missed_doses": 0})
    assert esc.level == "soon"


def test_appointment_declined_is_followup():
    esc = evaluate({"appointment_ok": False})
    assert esc.level == "soon"


def test_parse_error_asks_a_human_to_read():
    esc = evaluate({"parse_error": True})
    assert esc.level == "soon"


def test_three_missed_checkins_escalates():
    esc = evaluate({"medication_taken": True, "missed_doses": 0}, missed_checkin_days=4)
    assert esc.level == "soon"


def test_level_never_downgrades():
    esc = evaluate({
        "urgent_flags": ["chest pain"],
        "symptoms": [{"name": "chest pain", "severity": "severe"}],
        "missed_doses": 3,
    })
    assert esc.level == "urgent"
    assert len(esc.reasons) >= 2


def test_describe_is_readable():
    esc = evaluate({"missed_doses": 2})
    text = describe(esc, {"name": "Ramesh Kumar"})
    assert "FOLLOW-UP" in text and "Ramesh Kumar" in text
