import json

import pytest

from companion.conversation import TOPICS, ConversationSession, extract_json


class FakeClient:
    """Returns agent lines in order, then a JSON report for the extraction call."""

    engine = "fake"

    def __init__(self, report: dict | None = None, raw_report: str | None = None):
        self.report = report if report is not None else {
            "medication_taken": True,
            "missed_doses": 1,
            "side_effects": ["headache"],
            "symptoms": [{"name": "headache", "severity": "mild"}],
            "urgent_flags": [],
            "appointment_ok": True,
            "summary": "Took most medicines, mild headache.",
            "patient_language_summary": "दवा ली, हल्का सिरदर्द।",
        }
        self.raw_report = raw_report
        self.calls = []

    def chat(self, messages, model=None, temperature=None, max_tokens=None):
        self.calls.append(messages)
        system = messages[0]["content"]
        if "extract a structured clinical-admin report" in system:
            return self.raw_report or json.dumps(self.report)
        topic = system.split("NEXT_TOPIC:")[1].split("\n")[0].strip()
        return f"agent turn for {topic}"


def test_opening_and_topic_order(patient):
    client = FakeClient()
    session = ConversationSession(patient, client=client)
    opening = session.opening()
    assert "medication adherence" in opening

    replies = ["took them", "no side effects", "mild headache", "yes I will come"]
    for reply in replies:
        line = session.respond(reply)
        assert line
    assert session.finished is True

    topics = [t["topic"] for t in session.transcript if t["role"] == "assistant"]
    assert topics == TOPICS + ["closing"]


def test_system_prompt_carries_patient_context_and_language(patient):
    client = FakeClient()
    session = ConversationSession(patient, client=client)
    session.opening()
    system = client.calls[0][0]["content"]
    assert "Ramesh Kumar" in system
    assert "hi-IN" in system
    assert "Hindi" in system
    assert "Metformin" in system


def test_report_roundtrip_and_meta(patient):
    client = FakeClient()
    session = ConversationSession(patient, client=client)
    session.run_turn_loop(["took them", "no", "none", "yes"])
    report = session.build_report()
    assert report["medication_taken"] is True
    assert report["missed_doses"] == 1
    assert report["_meta"]["patient_id"] == "P001"
    assert report["engine"] == "fake"


def test_unparseable_report_is_flagged_not_invented(patient):
    client = FakeClient(raw_report="Sorry, I cannot help with that.")
    session = ConversationSession(patient, client=client)
    session.run_turn_loop(["yes", "no", "none", "yes"])
    report = session.build_report()
    assert report["parse_error"] is True
    assert "raw_extraction" in report
    assert "medication_taken" not in report


def test_blank_reply_does_not_advance_topic(patient):
    client = FakeClient()
    session = ConversationSession(patient, client=client)
    session.opening()
    session.respond("   ")
    assert session._patient_turns == 0
    assert session.finished is False


def test_clean_strips_markdown_and_role_prefix(patient):
    assert ConversationSession._clean("**Agent:** *नमस्ते*") == "नमस्ते"


@pytest.mark.parametrize("raw,expected", [
    ('{"a": 1}', {"a": 1}),
    ('```json\n{"a": 2}\n```', {"a": 2}),
    ('Here you go: {"a": 3} hope that helps', {"a": 3}),
    ("no json here", None),
    ("", None),
    ("[1, 2, 3]", None),
])
def test_extract_json_tolerance(raw, expected):
    assert extract_json(raw) == expected
