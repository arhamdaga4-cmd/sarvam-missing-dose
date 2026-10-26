"""The live audio loop: Exotel AgentStream <-> Sarvam STT / LLM / TTS.

Protocol (Exotel AgentStream, bidirectional "Voicebot" stream):
  inbound  : connected, start, media (base64 PCM16 8k mono), dtmf, mark, stop
  outbound : media (base64 PCM16), mark, clear

Design notes
------------
* `CallSession` is deliberately framework-free: it takes a `send` coroutine and
  plain functions for STT/TTS, so the whole state machine is unit-testable
  without a WebSocket, a phone, or an API key.
* Barge-in: if the patient starts talking while the bot is speaking we send
  `clear` and stop playback. Without this, the agent talks over the patient.
* Silence detection is a cheap RMS gate over 20 ms frames. It is not a real
  VAD model, but on 8 kHz telephony audio it is enough to find turn ends.
"""
from __future__ import annotations

import asyncio
import base64
import logging
from typing import Any, Awaitable, Callable

from .. import config, patient_db
from ..audio_utils import pcm16_to_array, rms
from ..conversation import ConversationSession
from ..escalation import evaluate
from ..notifier import dispatch
from ..stt import transcribe_pcm
from ..tts import synthesize_chunks

log = logging.getLogger(__name__)

FRAME_BYTES = 320  # 20 ms at 8 kHz / 16-bit


class CallSession:
    def __init__(
        self,
        send: Callable[[dict], Awaitable[None]],
        patient_id: str | None = None,
        stt_fn: Callable[[bytes, str], str] | None = None,
        tts_fn: Callable[[str, str, str | None], list[bytes]] | None = None,
        conversation_factory: Callable[[dict], ConversationSession] | None = None,
        pace: float | None = None,
        db_path=None,
    ):
        self.send = send
        self.requested_patient_id = patient_id
        self._stt = stt_fn or (lambda pcm, lang: transcribe_pcm(pcm, lang))
        self._tts = tts_fn or (
            lambda text, lang, speaker: synthesize_chunks(text, lang, speaker=speaker)
        )
        self._conversation_factory = conversation_factory or (lambda p: ConversationSession(p))
        self.pace = config.TELEPHONY_CHUNK_PACE_SECONDS if pace is None else pace
        self.db_path = db_path

        self.patient: dict | None = None
        self.conversation: ConversationSession | None = None
        self.stream_sid: str | None = None
        self.call_sid: str | None = None
        self.checkin_id: int | None = None
        self.closed = False
        self.finalized = False
        self.speaking = False
        self.turns = 0
        self.events_sent: list[dict] = []
        self._errors = 0
        self._finalize_lock = asyncio.Lock()

        self._utterance = bytearray()
        self._speech_frames = 0
        self._silence_frames = 0
        self._barge_in_armed = True

    # -- public -----------------------------------------------------------
    async def handle_event(self, event: dict) -> None:
        kind = (event or {}).get("event")
        if kind == "connected":
            return
        if kind == "start":
            await self._on_start(event)
        elif kind == "media":
            await self._on_media(event)
        elif kind == "dtmf":
            log.info("DTMF %s on stream %s", event.get("dtmf"), self.stream_sid)
        elif kind == "stop":
            log.info("Stream stopped (%s)", (event.get("stop") or {}).get("reason"))
            await self.finalize()
        elif kind == "mark":
            return

    async def finalize(self) -> None:
        """Close the call: report, escalate, persist. Safe to call twice."""
        async with self._finalize_lock:
            if self.finalized:
                return
            self.finalized = True
            self.closed = True
            if not self.conversation or not self.patient:
                return
            try:
                report = await asyncio.to_thread(self.conversation.build_report)
            except Exception as exc:  # noqa: BLE001 - never lose the call log
                log.exception("Report extraction failed: %s", exc)
                report = {"parse_error": True, "raw_extraction": str(exc),
                          "summary": "Report extraction failed - read the transcript."}
            escalation = evaluate(report, self.patient)
            if self.checkin_id:
                patient_db.finish_checkin(
                    self.checkin_id, report, self.conversation.transcript,
                    escalation_level=escalation.level, completed=True, db_path=self.db_path,
                )
                try:
                    dispatch(escalation, self.patient, report, self.checkin_id, db_path=self.db_path)
                except Exception as exc:  # noqa: BLE001
                    log.exception("Escalation dispatch failed: %s", exc)
            self.report = report
            self.escalation = escalation

    # -- inbound events ---------------------------------------------------
    async def _on_start(self, event: dict) -> None:
        start = event.get("start") or {}
        self.stream_sid = event.get("stream_sid") or start.get("stream_sid")
        self.call_sid = start.get("call_sid")
        params = start.get("custom_parameters") or {}
        patient_id = (
            self.requested_patient_id
            or params.get("patient_id")
            or params.get("patientId")
        )
        patient = None
        if patient_id:
            patient = patient_db.get_patient(patient_id, db_path=self.db_path)
        if patient is None:
            for number in (start.get("from"), start.get("to")):
                if number:
                    patient = patient_db.get_patient_by_phone(number, db_path=self.db_path)
                    if patient:
                        break
        if patient is None:
            log.error("No patient matched for call %s (%s -> %s)",
                      self.call_sid, start.get("from"), start.get("to"))
            await self.send({"event": "clear", "stream_sid": self.stream_sid})
            self.closed = True
            return

        self.patient = patient
        self.conversation = self._conversation_factory(patient)
        self.checkin_id = patient_db.start_checkin(
            patient["id"], channel="phone", engine=getattr(
                getattr(self.conversation, "client", None), "engine", "sarvam"
            ), db_path=self.db_path,
        )
        try:
            opening = await asyncio.to_thread(self.conversation.opening)
        except Exception as exc:  # noqa: BLE001
            log.exception("Opening turn failed: %s", exc)
            self.closed = True
            return
        await self._speak(opening)

    async def _on_media(self, event: dict) -> None:
        payload = ((event.get("media") or {}).get("payload")) or ""
        if not payload or not self.patient:
            return
        try:
            pcm = base64.b64decode(payload)
        except (ValueError, TypeError):
            log.warning("Dropping undecodable media chunk")
            return
        await self._ingest(pcm)

    # -- audio plumbing ---------------------------------------------------
    async def _ingest(self, pcm: bytes) -> None:
        threshold = config.VAD_RMS_THRESHOLD
        for offset in range(0, len(pcm), FRAME_BYTES):
            frame = pcm[offset:offset + FRAME_BYTES]
            if not frame:
                continue
            loud = rms(frame) > threshold

            if loud:
                if self.speaking and self._barge_in_armed:
                    # Patient interrupted: drop whatever has not played yet.
                    await self.send({"event": "clear", "stream_sid": self.stream_sid})
                    self.speaking = False
                    self._barge_in_armed = False
                self._speech_frames += 1
                self._silence_frames = 0
                self._utterance.extend(frame)
            elif self._speech_frames:
                self._silence_frames += 1
                self._utterance.extend(frame)
                if self._silence_frames >= config.VAD_SILENCE_FRAMES:
                    await self._end_utterance()

            if len(self._utterance) > config.MAX_UTTERANCE_SECONDS * config.TELEPHONY_SAMPLE_RATE * 2:
                await self._end_utterance()

    async def _end_utterance(self) -> None:
        pcm = bytes(self._utterance)
        had_speech = self._speech_frames >= config.VAD_MIN_SPEECH_FRAMES
        self._utterance.clear()
        self._speech_frames = 0
        self._silence_frames = 0
        self._barge_in_armed = True
        if not had_speech or not self.conversation:
            return
        await self._process_utterance(pcm)

    async def _process_utterance(self, pcm: bytes) -> None:
        lang = self.patient.get("language_code", "hi-IN")
        try:
            text = await asyncio.to_thread(self._stt, pcm, lang)
        except Exception as exc:  # noqa: BLE001
            self._errors += 1
            log.exception("STT failed (%d in a row): %s", self._errors, exc)
            if self._errors >= 3:
                self.closed = True
            return
        self._errors = 0
        log.info("patient said: %s", text)

        try:
            reply = await asyncio.to_thread(self.conversation.respond, text)
        except Exception as exc:  # noqa: BLE001
            log.exception("LLM turn failed: %s", exc)
            return
        self.turns += 1
        await self._speak(reply)
        if self.conversation.finished:
            await self.finalize()

    async def _speak(self, text: str) -> None:
        if not text or not self.patient:
            return
        lang = self.patient.get("language_code", "hi-IN")
        speaker = self.patient.get("voice")
        try:
            chunks = await asyncio.to_thread(self._tts, text, lang, speaker)
        except Exception as exc:  # noqa: BLE001
            self._errors += 1
            log.exception("TTS failed (%d in a row): %s", self._errors, exc)
            if self._errors >= 3:
                self.closed = True
            return
        self._errors = 0
        self.speaking = True
        self._barge_in_armed = True
        for chunk in chunks:
            if self.closed:
                break
            await self.send({
                "event": "media",
                "stream_sid": self.stream_sid,
                "media": {"payload": base64.b64encode(chunk).decode("ascii")},
            })
            if self.pace:
                await asyncio.sleep(self.pace)
        self.speaking = False


async def run_websocket(ws, patient_id: str | None = None, db_path=None) -> None:
    """Adapter for a Starlette/FastAPI WebSocket."""
    import json

    async def send(message: dict) -> None:
        await ws.send_text(json.dumps(message))

    session = CallSession(send=send, patient_id=patient_id, db_path=db_path)
    try:
        while not session.closed:
            raw = await ws.receive_text()
            try:
                event = json.loads(raw)
            except json.JSONDecodeError:
                log.warning("Ignoring non-JSON frame from Exotel")
                continue
            await session.handle_event(event)
    except Exception as exc:  # noqa: BLE001 - disconnect surfaces here
        log.info("WebSocket ended: %s", exc)
    finally:
        await session.finalize()
