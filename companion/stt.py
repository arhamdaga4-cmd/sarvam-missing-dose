"""Patient speech -> text, for the phone bridge.

Exotel hands us 8 kHz PCM16. The Sarvam realtime REST endpoint wants a
container it can inspect, so we wrap the utterance in a WAV and upsample to
16 kHz first - that measurably improves recognition over 8 kHz telephony
audio. Utterances are capped at 30 s by the API; the bridge flushes earlier.
"""
from __future__ import annotations

from typing import Any

from . import config
from .audio_utils import pcm16_to_wav, resample_pcm16
from .sarvam_client import get_client

STT_SAMPLE_RATE = 16000


def transcribe_pcm(
    pcm: bytes,
    language_code: str,
    sample_rate: int | None = None,
    client: Any = None,
) -> str:
    if not pcm:
        return ""
    client = client or get_client()
    src = sample_rate or config.TELEPHONY_SAMPLE_RATE
    upsampled = resample_pcm16(pcm, src, STT_SAMPLE_RATE)
    wav = pcm16_to_wav(upsampled, STT_SAMPLE_RATE)
    return client.stt(wav, language_code)


def transcribe_wav_bytes(wav_bytes: bytes, language_code: str, client: Any = None) -> str:
    client = client or get_client()
    return client.stt(wav_bytes, language_code)
