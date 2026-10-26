"""Central configuration for the missing-dose check-in agent.

Everything is driven by environment variables so the same code runs locally,
in CI (offline mode) and on a server next to the Exotel webhook.
"""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")

# --------------------------------------------------------------------------
# Storage
# --------------------------------------------------------------------------
DATA_DIR = Path(os.getenv("DATA_DIR", BASE_DIR / "data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = DATA_DIR / "checkins.db"
ESCALATION_LOG = DATA_DIR / "escalations.jsonl"
TRANSCRIPT_DIR = DATA_DIR / "transcripts"
AUDIO_DIR = DATA_DIR / "audio"

# --------------------------------------------------------------------------
# Sarvam
# --------------------------------------------------------------------------
SARVAM_API_KEY = os.getenv("SARVAM_API_KEY", "").strip()
SARVAM_BASE_URL = os.getenv("SARVAM_BASE_URL", "https://api.sarvam.ai").rstrip("/")
SARVAM_TIMEOUT = float(os.getenv("SARVAM_TIMEOUT", "30"))

CHAT_MODEL = os.getenv("SARVAM_CHAT_MODEL", "sarvam-105b-conversations")
CHAT_TEMPERATURE = float(os.getenv("SARVAM_CHAT_TEMPERATURE", "0.4"))
CHAT_MAX_TOKENS = int(os.getenv("SARVAM_CHAT_MAX_TOKENS", "300"))

TTS_MODEL = os.getenv("SARVAM_TTS_MODEL", "bulbul:v3")
TTS_DEFAULT_SPEAKER = os.getenv("SARVAM_TTS_SPEAKER", "anushka")
STT_MODEL = os.getenv("SARVAM_STT_MODEL", "saaras:v4")

# --------------------------------------------------------------------------
# Languages / voices
# --------------------------------------------------------------------------
LANGUAGE_NAMES = {
    "hi-IN": "Hindi",
    "ta-IN": "Tamil",
    "gu-IN": "Gujarati",
    "mr-IN": "Marathi",
    "bn-IN": "Bengali",
    "te-IN": "Telugu",
    "kn-IN": "Kannada",
    "ml-IN": "Malayalam",
    "pa-IN": "Punjabi",
    "od-IN": "Odia",
    "en-IN": "Indian English",
}

# Speaker IDs are model-specific. These are safe defaults for bulbul:v3; the
# full catalogue is at https://docs.sarvam.ai (Voices page). Override per
# patient in patients.json ("voice") or globally with SARVAM_TTS_SPEAKER.
VOICE_BY_LANGUAGE = {
    "hi-IN": TTS_DEFAULT_SPEAKER,
    "ta-IN": TTS_DEFAULT_SPEAKER,
    "gu-IN": TTS_DEFAULT_SPEAKER,
    "mr-IN": TTS_DEFAULT_SPEAKER,
    "bn-IN": TTS_DEFAULT_SPEAKER,
    "te-IN": TTS_DEFAULT_SPEAKER,
    "kn-IN": TTS_DEFAULT_SPEAKER,
    "ml-IN": TTS_DEFAULT_SPEAKER,
    "pa-IN": TTS_DEFAULT_SPEAKER,
    "od-IN": TTS_DEFAULT_SPEAKER,
    "en-IN": TTS_DEFAULT_SPEAKER,
}

# --------------------------------------------------------------------------
# Telephony (Exotel AgentStream)
# --------------------------------------------------------------------------
# Exotel streams raw PCM16 little-endian, mono, 8 kHz by default.
TELEPHONY_SAMPLE_RATE = int(os.getenv("TELEPHONY_SAMPLE_RATE", "8000"))
TELEPHONY_CHUNK_BYTES = 3200      # 100 ms at 8 kHz/16-bit -> Exotel minimum
TELEPHONY_MIN_CHUNK = 3200        # Exotel: >= 3.2 KB
TELEPHONY_MAX_CHUNK = 100_000     # Exotel: <= 100 KB
TELEPHONY_CHUNK_MULTIPLE = 320    # Exotel: multiple of 320 bytes
TELEPHONY_CHUNK_PACE_SECONDS = float(os.getenv("TELEPHONY_CHUNK_PACE", "0.02"))

# Voice-activity detection on the inbound stream.
VAD_RMS_THRESHOLD = int(os.getenv("VAD_RMS_THRESHOLD", "450"))
VAD_SILENCE_FRAMES = int(os.getenv("VAD_SILENCE_FRAMES", "35"))   # ~700 ms
VAD_MIN_SPEECH_FRAMES = int(os.getenv("VAD_MIN_SPEECH_FRAMES", "8"))  # ~160 ms
MAX_UTTERANCE_SECONDS = int(os.getenv("MAX_UTTERANCE_SECONDS", "30"))

EXOTEL_ACCOUNT_SID = os.getenv("EXOTEL_ACCOUNT_SID", "").strip()
EXOTEL_API_KEY = os.getenv("EXOTEL_API_KEY", "").strip()
EXOTEL_API_TOKEN = os.getenv("EXOTEL_API_TOKEN", "").strip()
EXOTEL_SUBDOMAIN = os.getenv("EXOTEL_SUBDOMAIN", "api.exotel.com").strip()
EXOTEL_CALLER_ID = os.getenv("EXOTEL_CALLER_ID", "").strip()   # ExoPhone
EXOTEL_BRIDGE_NUMBER = os.getenv("EXOTEL_BRIDGE_NUMBER", "").strip()
EXOTEL_FLOW_URL = os.getenv("EXOTEL_FLOW_URL", "").strip()
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "").strip()     # https://xyz.ngrok.app
PUBLIC_WS_URL = os.getenv("PUBLIC_WS_URL", "").strip()         # wss://xyz.ngrok.app/stream

# --------------------------------------------------------------------------
# Conversation + escalation policy
# --------------------------------------------------------------------------
MAX_CONVERSATION_TURNS = int(os.getenv("MAX_CONVERSATION_TURNS", "6"))

URGENT_SYMPTOM_KEYWORDS = [
    "chest pain", "chest pressure", "breathless", "shortness of breath",
    "cannot breathe", "can't breathe", "unconscious", "fainted", "fainting",
    "bleeding", "blood", "stroke", "paralysis", "severe pain", "suicidal",
    "सीने में दर्द", "सांस", "बेहोश", "खून",
    "மார்பில் வலி", "மூச்சு", "ரத்தம்", "மயக்கம்",
    "છાતીમાં દુખાવો", "શ્વાસ", "લોહી", "બેહોશ",
]
MISSED_DOSE_ESCALATION_THRESHOLD = int(os.getenv("MISSED_DOSE_ESCALATION_THRESHOLD", "2"))

# --------------------------------------------------------------------------
# Notifications
# --------------------------------------------------------------------------
COORDINATOR_WEBHOOK_URL = os.getenv("COORDINATOR_WEBHOOK_URL", "").strip()
SMTP_HOST = os.getenv("SMTP_HOST", "").strip()
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER", "").strip()
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "").strip()
ALERT_EMAIL_FROM = os.getenv("ALERT_EMAIL_FROM", SMTP_USER)
ALERT_EMAIL_TO = [a.strip() for a in os.getenv("ALERT_EMAIL_TO", "").split(",") if a.strip()]

# --------------------------------------------------------------------------
# Scheduler
# --------------------------------------------------------------------------
SCHEDULE_TIME = os.getenv("SCHEDULE_TIME", "09:30")      # local HH:MM, IST assumed
SCHEDULE_TIMEZONE = os.getenv("SCHEDULE_TIMEZONE", "Asia/Kolkata")
SCHEDULER_POLL_SECONDS = int(os.getenv("SCHEDULER_POLL_SECONDS", "60"))
SCHEDULER_RETRY_HOURS = int(os.getenv("SCHEDULER_RETRY_HOURS", "3"))


class ConfigError(RuntimeError):
    """Raised when a required setting is missing."""


def require_sarvam_key() -> str:
    if not SARVAM_API_KEY:
        raise ConfigError(
            "SARVAM_API_KEY is not set. Create a key at https://dashboard.sarvam.ai "
            "and put it in .env (the sk_... subscription key is the only key this "
            "project needs)."
        )
    return SARVAM_API_KEY


def have_sarvam_key() -> bool:
    return bool(SARVAM_API_KEY)


def stream_url_for_patient(patient_id: str) -> str:
    """Public WSS URL Exotel should connect to, with the patient id attached."""
    base = PUBLIC_WS_URL
    if not base:
        if not PUBLIC_BASE_URL:
            raise ConfigError("Set PUBLIC_WS_URL (or PUBLIC_BASE_URL) to your public server URL.")
        base = PUBLIC_BASE_URL.replace("https://", "wss://").replace("http://", "ws://").rstrip("/") + "/stream"
    sep = "&" if "?" in base else "?"
    return f"{base}{sep}patient_id={patient_id}"
