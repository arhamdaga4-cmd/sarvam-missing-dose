"""Text -> telephony-ready PCM for the phone bridge.

Sarvam TTS returns base64 WAV (bulbul:v3). Exotel needs 8 kHz mono PCM16, so we
decode, downmix and resample here, then hand back chunks that already satisfy
Exotel's size rules.
"""
from __future__ import annotations

from typing import Any

from . import config
from .audio_utils import chunk_for_exotel, resample_pcm16, wav_to_pcm16
from .sarvam_client import get_client


def synthesize_wav(text: str, language_code: str, speaker: str | None = None,
                   client: Any = None) -> bytes:
    client = client or get_client()
    return client.tts(text, language_code, speaker=speaker)


def synthesize_pcm(
    text: str,
    language_code: str,
    speaker: str | None = None,
    client: Any = None,
    sample_rate: int | None = None,
) -> bytes:
    """Return mono PCM16 at the telephony sample rate (8 kHz by default)."""
    if not text or not text.strip():
        return b""
    wav = synthesize_wav(text, language_code, speaker=speaker, client=client)
    pcm, rate, _channels = wav_to_pcm16(wav)
    target = sample_rate or config.TELEPHONY_SAMPLE_RATE
    return resample_pcm16(pcm, rate, target)


def synthesize_chunks(
    text: str,
    language_code: str,
    speaker: str | None = None,
    client: Any = None,
) -> list[bytes]:
    """PCM split into Exotel-legal base64-able chunks."""
    return chunk_for_exotel(synthesize_pcm(text, language_code, speaker=speaker, client=client))
