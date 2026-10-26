"""FastAPI server: the public endpoint Exotel connects to.

    uvicorn companion.server:app --host 0.0.0.0 --port 8000

Expose it with ngrok or a VPS, then set PUBLIC_WS_URL / PUBLIC_BASE_URL.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse

from . import config, patient_db
from .telephony.bridge import run_websocket

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("server")

@asynccontextmanager
async def lifespan(_app: FastAPI):
    patient_db.load_patients()
    log.info("Loaded %d patients; Sarvam key %s", len(patient_db.list_patients()),
             "configured" if config.have_sarvam_key() else "MISSING (calls will fail)")
    yield


app = FastAPI(title="Missing Dose Check-in Agent", version="1.0.0", lifespan=lifespan)


@app.get("/health")
def health() -> dict:
    return {
        "status": "ok",
        "sarvam_key_configured": config.have_sarvam_key(),
        "chat_model": config.CHAT_MODEL,
        "tts_model": config.TTS_MODEL,
        "stt_model": config.STT_MODEL,
        "patients": len(patient_db.list_patients()),
        "telephony_sample_rate": config.TELEPHONY_SAMPLE_RATE,
    }


@app.get("/api/patients")
def api_patients() -> list[dict]:
    return [patient_db.as_dict(p) for p in patient_db.list_patients()]


@app.get("/api/checkins/{patient_id}")
def api_checkins(patient_id: str, limit: int = 10) -> list[dict]:
    return patient_db.history(patient_id, limit=limit)


@app.get("/api/escalations")
def api_escalations(limit: int = 50) -> list[dict]:
    return patient_db.recent_escalations(limit=limit)


@app.post("/exotel/stream-url")
async def exotel_stream_url(request: Request) -> JSONResponse:
    """Optional dynamic URL resolver for the Voicebot applet.

    Exotel accepts an HTTPS URL here and expects {"url": "wss://..."}.
    """
    try:
        body: dict[str, Any] = await request.json()
    except json.JSONDecodeError:
        body = {}
    patient_id = (body.get("patient_id") or body.get("custom_parameters", {}).get("patient_id")
                  or request.query_params.get("patient_id"))
    if not patient_id:
        return JSONResponse({"error": "patient_id required"}, status_code=400)
    return JSONResponse({"url": config.stream_url_for_patient(str(patient_id))})


@app.post("/exotel/status")
async def exotel_status(request: Request) -> JSONResponse:
    """Call status webhook, and the hook a Passthru applet can use to route.

    Returns escalate=true when the most recent check-in for that patient needs
    a human, so the call flow can switch to an agent or hang up.
    """
    try:
        body: dict[str, Any] = await request.json()
    except json.JSONDecodeError:
        body = dict(request.query_params)
    log.info("Exotel status: %s", json.dumps(body, ensure_ascii=False)[:800])

    patient = None
    for number in (body.get("To"), body.get("From"), body.get("CallTo"), body.get("CallFrom")):
        if number:
            patient = patient_db.get_patient_by_phone(str(number))
            if patient:
                break
    if not patient:
        return JSONResponse({"ok": True, "patient": None, "escalate": False})

    last = patient_db.last_checkin(patient["id"])
    escalate = bool(last and last.get("escalation_level") in ("soon", "urgent"))
    return JSONResponse({
        "ok": True,
        "patient": patient["id"],
        "escalate": escalate,
        "level": (last or {}).get("escalation_level"),
    })


@app.websocket("/stream")
async def stream(ws: WebSocket) -> None:
    await ws.accept()
    patient_id = ws.query_params.get("patient_id")
    log.info("AgentStream connected (patient_id=%s)", patient_id)
    try:
        await run_websocket(ws, patient_id=patient_id)
    except WebSocketDisconnect:
        log.info("AgentStream disconnected")
    finally:
        try:
            await ws.close()
        except RuntimeError:
            pass
