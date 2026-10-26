"""Audio helpers for the telephony bridge.

Sarvam returns WAV (often 22.05 kHz). Exotel wants raw PCM16 little-endian,
mono, 8 kHz, base64-encoded, in chunks of 3.2 KB-100 KB that are a multiple
of 320 bytes. Everything in between lives here so the bridge stays readable.
"""
from __future__ import annotations

import io
import wave

import numpy as np

from . import config


class AudioError(RuntimeError):
    pass


def pcm16_to_array(pcm: bytes) -> np.ndarray:
    if len(pcm) % 2:
        pcm = pcm[:-1]  # drop a dangling byte rather than crash mid-call
    return np.frombuffer(pcm, dtype="<i2")


def array_to_pcm16(samples: np.ndarray) -> bytes:
    clipped = np.clip(samples, -32768, 32767)
    return clipped.astype("<i2").tobytes()


def resample_pcm16(pcm: bytes, src_rate: int, dst_rate: int) -> bytes:
    """Linear-interpolation resample of 16-bit mono PCM."""
    if src_rate == dst_rate:
        return pcm
    samples = pcm16_to_array(pcm)
    if samples.size == 0:
        return b""
    duration = samples.size / float(src_rate)
    target_len = max(1, int(round(duration * dst_rate)))
    src_idx = np.linspace(0, samples.size - 1, num=target_len, dtype=np.float64)
    out = np.interp(src_idx, np.arange(samples.size), samples.astype(np.float64))
    return array_to_pcm16(np.round(out))


def wav_to_pcm16(wav_bytes: bytes) -> tuple[bytes, int, int]:
    """Return (pcm16_mono, sample_rate, channels) from a WAV container."""
    if not wav_bytes:
        raise AudioError("Empty audio payload from TTS.")
    try:
        with wave.open(io.BytesIO(wav_bytes), "rb") as wf:
            channels = wf.getnchannels()
            sampwidth = wf.getsampwidth()
            rate = wf.getframerate()
            frames = wf.readframes(wf.getnframes())
    except (wave.Error, EOFError) as exc:
        raise AudioError(
            "TTS audio was not a readable WAV file. Sarvam returns base64 WAV by "
            "default - check that the response 'audios' field was decoded."
        ) from exc
    if sampwidth != 2:
        raise AudioError(f"Expected 16-bit PCM, got {sampwidth * 8}-bit.")
    if channels == 1:
        return frames, rate, channels
    samples = pcm16_to_array(frames).reshape(-1, channels)
    mono = samples.mean(axis=1)
    return array_to_pcm16(np.round(mono)), rate, 1


def pcm16_to_wav(pcm: bytes, rate: int, channels: int = 1) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(channels)
        wf.setsampwidth(2)
        wf.setframerate(rate)
        wf.writeframes(pcm)
    return buf.getvalue()


def rms(pcm: bytes) -> float:
    samples = pcm16_to_array(pcm)
    if samples.size == 0:
        return 0.0
    return float(np.sqrt(np.mean(samples.astype(np.float64) ** 2)))


def chunk_for_exotel(pcm: bytes, chunk_bytes: int | None = None) -> list[bytes]:
    """Split PCM into Exotel-legal chunks.

    Pads the final chunk with silence so every chunk is >= 3.2 KB and a
    multiple of 320 bytes. An empty utterance returns an empty list.
    """
    chunk_bytes = chunk_bytes or config.TELEPHONY_CHUNK_BYTES
    chunk_bytes -= chunk_bytes % config.TELEPHONY_CHUNK_MULTIPLE
    if chunk_bytes < config.TELEPHONY_MIN_CHUNK:
        chunk_bytes = config.TELEPHONY_MIN_CHUNK
    if chunk_bytes > config.TELEPHONY_MAX_CHUNK:
        chunk_bytes = config.TELEPHONY_MAX_CHUNK
    if not pcm:
        return []

    chunks = [pcm[i:i + chunk_bytes] for i in range(0, len(pcm), chunk_bytes)]
    last = chunks[-1]
    if len(last) < chunk_bytes:
        pad = chunk_bytes - len(last)
        if pad % 2:
            pad += 1
        chunks[-1] = last + b"\x00" * pad
    return chunks
