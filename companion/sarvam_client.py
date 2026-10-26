"""Thin HTTP client for the three Sarvam endpoints this project uses.

Only the subscription key (`api-subscription-key` header, the sk_... value
from dashboard.sarvam.ai) is required. The separate X-API-Key belongs to the
hosted voice-agent product and is not used here.

Endpoints (all under https://api.sarvam.ai):
  POST /v1/chat/completions   sarvam-105b-conversations
  POST /text-to-speech        bulbul:v3        -> base64 WAV in "audios"
  POST /speech-to-text        saaras:v4        -> multipart, <= 30 s audio
"""
from __future__ import annotations

import base64
import time
from typing import Any

import requests

from . import config


class SarvamError(RuntimeError):
    def __init__(self, message: str, status_code: int | None = None, payload: Any = None):
        super().__init__(message)
        self.status_code = status_code
        self.payload = payload


_RETRYABLE = {429, 500, 502, 503, 504}


class SarvamClient:
    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        timeout: float | None = None,
        max_retries: int = 2,
    ):
        self.api_key = (api_key or config.SARVAM_API_KEY or "").strip()
        self.base_url = (base_url or config.SARVAM_BASE_URL).rstrip("/")
        self.timeout = timeout or config.SARVAM_TIMEOUT
        self.max_retries = max_retries
        self.session = requests.Session()

    # -- plumbing ---------------------------------------------------------
    def _headers(self) -> dict[str, str]:
        if not self.api_key:
            raise config.ConfigError(
                "SARVAM_API_KEY is not set. Create a key at https://dashboard.sarvam.ai"
            )
        return {"api-subscription-key": self.api_key}

    def _post(self, path: str, **kwargs) -> requests.Response:
        url = f"{self.base_url}{path}"
        last_exc: Exception | None = None
        for attempt in range(self.max_retries + 1):
            try:
                resp = self.session.post(url, headers=self._headers(), timeout=self.timeout, **kwargs)
            except requests.RequestException as exc:
                last_exc = exc
                if attempt == self.max_retries:
                    raise SarvamError(f"Could not reach Sarvam at {url}: {exc}") from exc
                time.sleep(1.5 * (attempt + 1))
                continue

            if resp.status_code == 200:
                return resp
            if resp.status_code in _RETRYABLE and attempt < self.max_retries:
                time.sleep(1.5 * (attempt + 1))
                continue
            raise self._error_from(resp)

        raise SarvamError(f"Request to {url} failed: {last_exc}")

    @staticmethod
    def _error_from(resp: requests.Response) -> SarvamError:
        try:
            payload = resp.json()
        except ValueError:
            payload = resp.text[:500]
        code = ""
        if isinstance(payload, dict):
            code = str(payload.get("error", {}).get("code", "") or payload.get("code", ""))
        if resp.status_code == 403:
            msg = (
                "Sarvam rejected the request with 403. If code is "
                "invalid_api_key_error, the SARVAM_API_KEY is wrong or missing - "
                "generate a fresh key at https://dashboard.sarvam.ai and copy it "
                "immediately (the value is only shown once)."
            )
        elif resp.status_code == 429:
            msg = "Sarvam rate limit or credit limit hit (429). Check usage on the dashboard."
        else:
            msg = f"Sarvam returned {resp.status_code}."
        return SarvamError(f"{msg} {code} {payload}", status_code=resp.status_code, payload=payload)

    # -- chat -------------------------------------------------------------
    def chat(
        self,
        messages: list[dict[str, str]],
        model: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> str:
        body = {
            "model": model or config.CHAT_MODEL,
            "messages": messages,
            "temperature": config.CHAT_TEMPERATURE if temperature is None else temperature,
            "max_tokens": config.CHAT_MAX_TOKENS if max_tokens is None else max_tokens,
        }
        resp = self._post("/v1/chat/completions", json=body)
        data = resp.json()
        try:
            content = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError) as exc:
            raise SarvamError(f"Unexpected chat response shape: {data}") from exc
        if isinstance(content, list):  # content-parts variant
            content = "".join(part.get("text", "") for part in content if isinstance(part, dict))
        return (content or "").strip()

    # -- text to speech ---------------------------------------------------
    def tts(
        self,
        text: str,
        language_code: str,
        speaker: str | None = None,
        model: str | None = None,
    ) -> bytes:
        """Return WAV bytes for `text`."""
        body = {
            "text": text,
            "language_code": language_code,
            "model": model or config.TTS_MODEL,
            "speaker": speaker or config.VOICE_BY_LANGUAGE.get(language_code, config.TTS_DEFAULT_SPEAKER),
        }
        try:
            resp = self._post("/text-to-speech", json=body)
        except SarvamError as exc:
            # A bad speaker id for this model is a 400; retry once on the default.
            if exc.status_code == 400 and body["speaker"] != config.TTS_DEFAULT_SPEAKER:
                body["speaker"] = config.TTS_DEFAULT_SPEAKER
                resp = self._post("/text-to-speech", json=body)
            else:
                raise
        data = resp.json()
        audios = data.get("audios") or []
        if not audios:
            raise SarvamError(f"TTS returned no audio: {data}")
        return base64.b64decode("".join(audios))

    # -- speech to text ---------------------------------------------------
    def stt(self, wav_bytes: bytes, language_code: str, model: str | None = None) -> str:
        files = {"file": ("utterance.wav", wav_bytes, "audio/wav")}
        data = {
            "model": model or config.STT_MODEL,
            "mode": "transcribe",
            "language_code": language_code,
        }
        resp = self._post("/speech-to-text", files=files, data=data)
        payload = resp.json()
        return (payload.get("transcript") or "").strip()


_default_client: SarvamClient | None = None


def get_client() -> SarvamClient:
    global _default_client
    if _default_client is None:
        _default_client = SarvamClient()
    return _default_client
