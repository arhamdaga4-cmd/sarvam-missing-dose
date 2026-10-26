"""Protocol-level tests for the live bridge: no phone, no key, no WebSocket."""
from __future__ import annotations

import asyncio
import base64

import numpy as np
import pytest

from companion import config, patient_db
from companion.telephony.bridge import CallSession


def loud_frames(count: int, amp: int = 4000) -> bytes:
    frame = (amp * np.sin(2 * np.pi * 220 * np.arange(160) / 8000)).astype("<i2").tobytes()
    return frame * count


SILENCE_FRAME = b"\x00" * 320


class FakeConversation:
    engine = "fake"

    def __init__(self):
        self.replies = []
        self.finished = False
        self.report_calls = 0

    def opening(self):
        return "नमस्ते, आज दवा ली?"

    def respond(self, text):
        self.replies.append(text)
        return "समझ गई।"

    def build_report(self):
        self.report_calls += 1
        return {"medication_taken": True, "missed_doses": 2, "summary": "two doses missed"}

    @property
    def transcript(self):
        return [{"role": "assistant", "content": "नमस्ते, आज दवा ली?", "topic": "medication_adherence"}]


def make_session(db_path, stt_text="हाँ ली है", chunks=None):
    sent: list[dict] = []

    async def send(message):
        sent.append(message)

    session = CallSession(
        send=send,
        stt_fn=lambda pcm, lang: stt_text,
        tts_fn=lambda text, lang, speaker: chunks or [b"\x00" * 3200, b"\x00" * 3200],
        conversation_factory=lambda p: FakeConversation(),
        pace=0,
        db_path=db_path,
    )
    return session, sent


def start_event(patient_id="P001", from_number="+919000000001"):
    return {
        "event": "start",
        "sequence_number": "1",
        "stream_sid": "MZstream123",
        "start": {
            "stream_sid": "MZstream123",
            "call_sid": "CAcall123",
            "account_sid": "ACaccount123",
            "from": from_number,
            "to": "+918047491899",
            "custom_parameters": {"patient_id": patient_id},
            "media_format": {"encoding": "raw", "sample_rate": "8000", "bit_rate": "16"},
        },
    }


def media_event(pcm: bytes) -> dict:
    return {
        "event": "media",
        "sequence_number": "2",
        "stream_sid": "MZstream123",
        "media": {"chunk": "1", "timestamp": "200",
                  "payload": base64.b64encode(pcm).decode("ascii")},
    }


def test_start_resolves_patient_and_speaks_opening(db_path):
    session, sent = make_session(db_path)
    asyncio.run(session.handle_event({"event": "connected"}))
    asyncio.run(session.handle_event(start_event()))

    assert session.patient["id"] == "P001"
    assert session.checkin_id is not None
    media = [m for m in sent if m["event"] == "media"]
    assert len(media) == 2
    assert media[0]["stream_sid"] == "MZstream123"
    for message in media:
        pcm = base64.b64decode(message["media"]["payload"])
        assert len(pcm) % 320 == 0
        assert len(pcm) >= 3200


def test_start_falls_back_to_phone_lookup(db_path):
    session, _sent = make_session(db_path)
    event = start_event()
    event["start"]["custom_parameters"] = {}
    asyncio.run(session.handle_event(event))
    assert session.patient["id"] == "P001"


def test_unknown_patient_closes_stream(db_path):
    session, sent = make_session(db_path)
    event = start_event(patient_id="P999", from_number="+910000000000")
    asyncio.run(session.handle_event(event))
    assert session.closed is True
    assert session.patient is None


def test_utterance_triggers_stt_then_reply(db_path):
    session, sent = make_session(db_path)
    asyncio.run(session.handle_event(start_event()))
    before = len([m for m in sent if m["event"] == "media"])

    speech = loud_frames(config.VAD_MIN_SPEECH_FRAMES + 5)
    silence = SILENCE_FRAME * (config.VAD_SILENCE_FRAMES + 2)
    asyncio.run(session.handle_event(media_event(speech + silence)))

    assert session.conversation.replies == ["हाँ ली है"]
    assert session.turns == 1
    assert len([m for m in sent if m["event"] == "media"]) > before


def test_pure_silence_is_not_transcribed(db_path):
    session, sent = make_session(db_path)
    asyncio.run(session.handle_event(start_event()))
    asyncio.run(session.handle_event(media_event(SILENCE_FRAME * 60)))
    assert session.conversation.replies == []


def test_barge_in_sends_clear(db_path):
    session, sent = make_session(db_path)
    asyncio.run(session.handle_event(start_event()))
    session.speaking = True
    asyncio.run(session._ingest(loud_frames(4)))
    assert any(m["event"] == "clear" for m in sent)
    assert session.speaking is False


def test_stop_finalizes_and_persists(db_path):
    session, _sent = make_session(db_path)
    asyncio.run(session.handle_event(start_event()))
    asyncio.run(session.handle_event(media_event(loud_frames(20) + SILENCE_FRAME * 40)))
    asyncio.run(session.handle_event({"event": "stop", "stop": {"reason": "callended"}}))

    assert session.finalized is True
    last = patient_db.last_checkin("P001", db_path=db_path)
    assert last["report"]["missed_doses"] == 2
    assert last["escalation_level"] == "soon"


def test_finalize_is_idempotent(db_path):
    session, _sent = make_session(db_path)
    asyncio.run(session.handle_event(start_event()))
    asyncio.run(session.finalize())
    asyncio.run(session.finalize())
    assert session.conversation.report_calls == 1


def test_bad_base64_is_dropped_not_fatal(db_path):
    session, _sent = make_session(db_path)
    asyncio.run(session.handle_event(start_event()))
    asyncio.run(session.handle_event({
        "event": "media", "stream_sid": "MZstream123",
        "media": {"payload": "!!!not base64!!!"},
    }))
    assert session.closed is False
