"""The check-in conversation: medications -> side effects -> symptoms -> appointment.

The LLM (sarvam-105b-conversations) speaks; this module decides which topic is
next, keeps the transcript, and turns the finished call into a structured
report with a second, separate extraction call. Keeping extraction separate
means a chatty model cannot quietly drop a missed dose.
"""
from __future__ import annotations

import json
import re
from typing import Any

from . import config
from .sarvam_client import get_client

TOPICS = ["medication_adherence", "side_effects", "symptoms", "appointment"]
CLOSING = "closing"

_TOPIC_INSTRUCTIONS = {
    "medication_adherence": (
        "Ask whether the patient has taken today's medicines (name them). "
        "If they say they missed any, ask how many doses and why, briefly."
    ),
    "side_effects": (
        "Ask whether the medicine caused any discomfort or side effects. "
        "Accept a short answer and move on."
    ),
    "symptoms": (
        "Ask how they are feeling today - pain, dizziness, breathlessness, "
        "swelling. If they mention something serious, ask one clarifying "
        "question about how long it has been happening."
    ),
    "appointment": (
        "Remind them of the appointment date and ask whether they can come. "
        "If they say no, ask once whether they need help with transport."
    ),
    CLOSING: (
        "Thank the patient by name, remind them to take tomorrow's medicines, "
        "and say goodbye. This is the last thing you say on the call."
    ),
}

_SYSTEM_TEMPLATE = """You are a warm, unhurried health worker calling a patient at home for a daily medicine check-in. This is a real phone call, not a chat.

PATIENT
- Name: {name}
- Age: {age}
- Conditions: {condition}
- Medicines: {medications}
- Next appointment: {appointment}
- Language: {language_name} ({language_code})

RULES
- Speak ONLY in {language_name}. Do not switch to English.
- One short sentence or two per turn. This is spoken aloud, so no lists, no markdown, no emojis, no stage directions.
- Ask one question at a time and then stop and listen.
- Never diagnose, never change a dose, never promise anything. If they report something alarming, say a health worker will call them back today.
- Be patient and respectful. If they are confused, repeat once, more simply.

NEXT_TOPIC: {topic}
WHAT TO DO NOW: {instruction}
"""

_EXTRACT_SYSTEM = """You extract a structured clinical-admin report from a phone check-in transcript. You are not diagnosing.

Return ONLY a JSON object with exactly these keys:
{
  "medication_taken": true | false | null,
  "missed_doses": integer (0 if none stated),
  "missed_dose_reason": string ("" if not stated),
  "side_effects": [string],
  "symptoms": [{"name": string, "severity": "mild" | "moderate" | "severe"}],
  "urgent_flags": [string],
  "appointment_ok": true | false | null,
  "needs_transport_help": true | false | null,
  "summary": string (one English sentence for the coordinator),
  "patient_language_summary": string (same sentence in the patient's language)

Rules:
- Use null when the transcript does not answer the question. Never guess.
- Put chest pain, breathlessness, fainting, bleeding, severe pain or confusion in urgent_flags, quoting the patient's words.
- Do not invent symptoms that were not mentioned.
"""


def _language_name(code: str) -> str:
    return config.LANGUAGE_NAMES.get(code, code)


class ConversationSession:
    """One phone call with one patient."""

    def __init__(self, patient: dict, client: Any = None, max_turns: int | None = None):
        self.patient = patient
        self.client = client or get_client()
        self.max_turns = max_turns or config.MAX_CONVERSATION_TURNS
        self.transcript: list[dict[str, str]] = []   # {"role","content","topic"}
        self.finished = False
        self._patient_turns = 0
        self._agent_turns = 0

    # -- prompts ----------------------------------------------------------
    def _system(self, topic: str) -> str:
        meds = self.patient.get("medications") or []
        return _SYSTEM_TEMPLATE.format(
            name=self.patient.get("name", ""),
            age=self.patient.get("age", ""),
            condition=self.patient.get("condition", ""),
            medications="; ".join(meds) if isinstance(meds, list) else str(meds),
            appointment=self.patient.get("appointment", ""),
            language_name=_language_name(self.patient.get("language_code", "hi-IN")),
            language_code=self.patient.get("language_code", "hi-IN"),
            topic=topic,
            instruction=_TOPIC_INSTRUCTIONS.get(topic, _TOPIC_INSTRUCTIONS[CLOSING]),
        )

    def _next_topic(self) -> str:
        if self._patient_turns < len(TOPICS):
            return TOPICS[self._patient_turns]
        return CLOSING

    def _messages(self, topic: str) -> list[dict[str, str]]:
        history = [{"role": t["role"], "content": t["content"]} for t in self.transcript]
        return [{"role": "system", "content": self._system(topic)}] + history

    # -- turns ------------------------------------------------------------
    def opening(self) -> str:
        """First thing the agent says. Greets, then asks about medicines."""
        topic = TOPICS[0]
        messages = self._messages(topic) + [
            {"role": "user", "content": "(The call has just connected. Say your first line now.)"}
        ]
        text = self._clean(self.client.chat(messages))
        self.transcript.append({"role": "assistant", "content": text, "topic": topic})
        self._agent_turns += 1
        return text

    def respond(self, patient_text: str) -> str:
        """Feed one patient utterance in, get the agent's next line."""
        if self.finished:
            return ""
        patient_text = (patient_text or "").strip()
        if not patient_text:
            # Silence: nudge once rather than advancing the topic.
            topic = self._next_topic()
            messages = self._messages(topic) + [
                {"role": "user", "content": "(The patient is silent. Ask the same thing again, more simply.)"}
            ]
            text = self._clean(self.client.chat(messages))
            self.transcript.append({"role": "assistant", "content": text, "topic": topic})
            self._agent_turns += 1
            return text

        self.transcript.append({"role": "user", "content": patient_text, "topic": self._next_topic()})
        self._patient_turns += 1
        topic = self._next_topic()
        text = self._clean(self.client.chat(self._messages(topic)))
        self.transcript.append({"role": "assistant", "content": text, "topic": topic})
        self._agent_turns += 1

        if topic == CLOSING or self._patient_turns >= self.max_turns:
            self.finished = True
        return text

    @staticmethod
    def _clean(text: str) -> str:
        text = (text or "").strip()
        text = re.sub(r"[*_#`~]+", " ", text)
        text = re.sub(r"\s+", " ", text).strip()
        text = re.sub(r"^(assistant|agent|bot|ai)\s*:\s*", "", text, flags=re.I)
        return text.strip()

    # -- report -----------------------------------------------------------
    def build_report(self) -> dict:
        """Second, separate call: transcript -> strict JSON."""
        lines = []
        for turn in self.transcript:
            who = "PATIENT" if turn["role"] == "user" else "AGENT"
            lines.append(f"{who}: {turn['content']}")
        transcript_text = "\n".join(lines) or "(empty call)"
        messages = [
            {"role": "system", "content": _EXTRACT_SYSTEM},
            {"role": "user", "content": f"TRANSCRIPT\n{transcript_text}\n\nReturn the JSON object now."},
        ]
        raw = self.client.chat(messages, temperature=0.0, max_tokens=700)
        report = extract_json(raw)
        if report is None:
            return {
                "parse_error": True,
                "raw_extraction": raw,
                "summary": "Report extraction failed - read the transcript manually.",
            }
        report.setdefault("engine", getattr(self.client, "engine", "sarvam"))
        report["_meta"] = {
            "patient_id": self.patient.get("id"),
            "patient_turns": self._patient_turns,
            "agent_turns": self._agent_turns,
            "language_code": self.patient.get("language_code"),
            "model": config.CHAT_MODEL,
        }
        return report

    def run_turn_loop(self, replies: list[str]) -> list[str]:
        """Convenience for scripted runs (simulator, tests, offline demo)."""
        agent_lines = [self.opening()]
        for reply in replies:
            if self.finished:
                break
            line = self.respond(reply)
            if line:
                agent_lines.append(line)
        return agent_lines


def extract_json(text: str) -> dict | None:
    """Tolerant JSON extraction - models like to wrap JSON in prose or fences."""
    if not text:
        return None
    cleaned = text.strip()
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", cleaned, flags=re.I | re.M).strip()
    try:
        parsed = json.loads(cleaned)
        return parsed if isinstance(parsed, dict) else None
    except json.JSONDecodeError:
        pass
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        parsed = json.loads(cleaned[start:end + 1])
        return parsed if isinstance(parsed, dict) else None
    except json.JSONDecodeError:
        return None
