import numpy as np
import pytest

from companion import audio_utils as au
from companion import config


def tone(seconds: float, rate: int = 8000, amp: int = 3000) -> bytes:
    t = np.linspace(0, seconds, int(rate * seconds), endpoint=False)
    return (amp * np.sin(2 * np.pi * 220 * t)).astype("<i2").tobytes()


def test_resample_length_matches_duration():
    src = tone(1.0, rate=22050)
    out = au.resample_pcm16(src, 22050, 8000)
    assert abs(len(out) / 2 - 8000) <= 2


def test_resample_is_identity_at_same_rate():
    src = tone(0.5, rate=8000)
    assert au.resample_pcm16(src, 8000, 8000) == src


def test_wav_roundtrip_and_downmix():
    mono = tone(0.25)
    wav = au.pcm16_to_wav(mono, 8000)
    pcm, rate, channels = au.wav_to_pcm16(wav)
    assert rate == 8000 and channels == 1
    assert pcm == mono

    stereo = np.repeat(np.frombuffer(mono, dtype="<i2"), 2).astype("<i2").tobytes()
    import io
    import wave

    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(2)
        wf.setsampwidth(2)
        wf.setframerate(8000)
        wf.writeframes(stereo)
    pcm2, _, channels2 = au.wav_to_pcm16(buf.getvalue())
    assert channels2 == 1
    assert len(pcm2) == len(mono)


def test_wav_to_pcm16_rejects_garbage():
    with pytest.raises(au.AudioError):
        au.wav_to_pcm16(b"not a wav file")


def test_rms_distinguishes_silence_from_speech():
    assert au.rms(b"\x00" * 640) == 0.0
    assert au.rms(tone(0.02)) > config.VAD_RMS_THRESHOLD


def test_chunks_respect_exotel_constraints():
    pcm = tone(1.0)  # 16 000 bytes
    chunks = au.chunk_for_exotel(pcm)
    assert chunks
    for chunk in chunks:
        assert len(chunk) % config.TELEPHONY_CHUNK_MULTIPLE == 0
        assert config.TELEPHONY_MIN_CHUNK <= len(chunk) <= config.TELEPHONY_MAX_CHUNK


def test_short_utterance_is_padded_not_dropped():
    chunks = au.chunk_for_exotel(tone(0.02))  # 320 bytes -> one padded chunk
    assert len(chunks) == 1
    assert len(chunks[0]) == config.TELEPHONY_MIN_CHUNK


def test_empty_audio_yields_no_chunks():
    assert au.chunk_for_exotel(b"") == []
