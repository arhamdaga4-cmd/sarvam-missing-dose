"""A deterministic stand-in for Sarvam, used for demos and tests without a key.

It is NOT a model. It produces scripted agent lines per topic and derives the
report from keyword rules over what the patient said. Anything it outputs is
labelled `"engine": "offline-heuristic"` so it can never be mistaken for a real
LLM result.
"""
from __future__ import annotations

import json
import re

from . import config

_AGENT_LINES = {
    "hi-IN": {
        "medication_adherence": "नमस्ते {name} जी, मैं स्वास्थ्य केंद्र से बोल रही हूँ। आज आपने अपनी दवाइयाँ ली हैं?",
        "side_effects": "समझ गई। दवा लेने के बाद कोई परेशानी या दिक्कत महसूस हुई?",
        "symptoms": "ठीक है। आज तबीयत कैसी है — कोई दर्द, चक्कर या सांस की तकलीफ़?",
        "appointment": "धन्यवाद। आपकी अगली अपॉइंटमेंट {appointment} को है, क्या आप आ पाएंगे?",
        "closing": "बहुत धन्यवाद {name} जी। अपना ख्याल रखिए, नमस्ते।",
    },
    "ta-IN": {
        "medication_adherence": "வணக்கம் {name} அவர்களே, நான் சுகாதார மையத்திலிருந்து பேசுகிறேன். இன்று மருந்து எடுத்தீர்களா?",
        "side_effects": "சரி. மருந்து எடுத்த பிறகு ஏதேனும் அசௌகரியம் உள்ளதா?",
        "symptoms": "சரி. இன்று உடல்நிலை எப்படி இருக்கிறது — வலி, தலைச்சுற்றல் அல்லது மூச்சுத் திணறல்?",
        "appointment": "நன்றி. அடுத்த சந்திப்பு {appointment} அன்று. வர முடியுமா?",
        "closing": "மிக்க நன்றி {name}. உடம்பை பார்த்துக்கொள்ளுங்கள்.",
    },
    "gu-IN": {
        "medication_adherence": "નમસ્તે {name}, હું આરોગ્ય કેન્દ્રથી બોલું છું. આજે તમે દવા લીધી છે?",
        "side_effects": "સમજી ગઈ. દવા લીધા પછી કોઈ તકલીફ થઈ છે?",
        "symptoms": "ઠીક છે. આજે તબિયત કેવી છે — દુખાવો, ચક્કર કે શ્વાસ લેવામાં તકલીફ?",
        "appointment": "આભાર. તમારી આગળની મુલાકાત {appointment} ના રોજ છે, આવી શકશો?",
        "closing": "ખૂબ ખૂબ આભાર {name}. તમારું ધ્યાન રાખજો.",
    },
    "en-IN": {
        "medication_adherence": "Hello {name}, this is the health centre calling. Have you taken your medicines today?",
        "side_effects": "Thank you. Did the medicine cause any discomfort or side effects?",
        "symptoms": "Alright. How are you feeling today - any pain, dizziness or breathlessness?",
        "appointment": "Thank you. Your next appointment is on {appointment}. Will you be able to come?",
        "closing": "Thank you very much {name}. Please take care, goodbye.",
    },
}

_NEGATIVE_WORDS = [
    "no", "not", "didn't", "did not", "forgot", "missed", "stop",
    "नहीं", "भूल", "छोड़", "இல்லை", "மறந்த", "நிறுத்த", "ના", "ભૂલી",
]
_YES_WORDS = ["yes", "took", "taken", "हाँ", "हां", "லீ", "எடுத்த", "હા", "લીધ"]


def _contains(text: str, needles) -> bool:
    low = text.lower()
    return any(n.lower() in low for n in needles)


class OfflineSarvam:
    """Same surface as SarvamClient, no network."""

    engine = "offline-heuristic"

    def __init__(self, patient: dict | None = None):
        self.patient = patient or {}
        self._turn = 0

    # -- chat -------------------------------------------------------------
    def chat(self, messages, model=None, temperature=None, max_tokens=None) -> str:
        system = messages[0]["content"] if messages else ""
        if "REPORT" in system or "JSON" in system:
            return self._report(messages)
        topic = "medication_adherence"
        match = re.search(r"NEXT_TOPIC:\s*([a-z_]+)", system)
        if match:
            topic = match.group(1)
        lang = self.patient.get("language_code", "hi-IN")
        lines = _AGENT_LINES.get(lang, _AGENT_LINES["en-IN"])
        return lines.get(topic, lines["closing"]).format(
            name=self.patient.get("name", ""),
            appointment=self.patient.get("appointment", ""),
        )

    def _report(self, messages) -> str:
        transcript = "\n".join(m.get("content", "") for m in messages)
        patient_lines = [
            line.split(":", 1)[1].strip()
            for line in transcript.splitlines()
            if line.strip().upper().startswith("PATIENT:")
        ]
        said = " ".join(patient_lines)

        took_meds = None
        if _contains(said, _YES_WORDS):
            took_meds = True
        if _contains(said, _NEGATIVE_WORDS):
            took_meds = False

        missed = 0
        digit = re.search(r"\b([1-9])\b", said)
        if digit:
            missed = int(digit.group(1))
        elif _contains(said, ["two", "दो", "இரண்டு", "બે"]):
            missed = 2
        elif _contains(said, ["three", "तीन", "மூன்று", "ત્રણ"]):
            missed = 3
        elif took_meds is False:
            missed = 1

        urgent = [k for k in config.URGENT_SYMPTOM_KEYWORDS if k.lower() in said.lower()]
        return json.dumps({
            "engine": self.engine,
            "medication_taken": took_meds,
            "missed_doses": missed,
            "missed_dose_reason": "" ,
            "side_effects": (["reported discomfort"] if _contains(
                said, ["discomfort", "दिक्कत", "தலை", "તકલીફ", "सिरदर्द"]) else []),
            "symptoms": ([{"name": urgent[0], "severity": "severe"}] if urgent else []),
            "urgent_flags": urgent,
            "appointment_ok": (None if not (took_meds is not None or _contains(said, _YES_WORDS + _NEGATIVE_WORDS))
                               else _contains(said, _YES_WORDS)),
            "summary": "Offline heuristic summary - not a model output.",
            "patient_language_summary": "",
        }, ensure_ascii=False)

    # -- tts / stt --------------------------------------------------------
    def tts(self, text: str, language_code: str, speaker: str | None = None, model=None) -> bytes:
        from .audio_utils import pcm16_to_wav
        import numpy as np

        # A short tone of the right length, just so the pipeline has audio.
        rate = 22050
        seconds = max(0.6, min(6.0, len(text) / 14.0))
        t = np.linspace(0, seconds, int(rate * seconds), endpoint=False)
        tone = (1200 * np.sin(2 * np.pi * 220 * t)).astype("<i2")
        return pcm16_to_wav(tone.tobytes(), rate)

    def stt(self, wav_bytes: bytes, language_code: str, model=None) -> str:
        return "[offline] speech not actually transcribed"
