#!/usr/bin/env python3
"""Entry point: run a structured check-in for one or all patients.

Usage:
    python run_checkup.py                  # check in every patient (offline demo if no key)
    python run_checkup.py P001             # one patient
    python run_checkup.py P001 --offline   # force the deterministic offline engine
    python run_checkup.py P001 --audio     # also save the agent's audio to data/audio/
    python run_checkup.py --json           # machine-readable output

The real phone path lives in companion/server.py + companion/scheduler.py; this
script is the text-mode harness you use to sanity-check the dialogue.
"""
from __future__ import annotations

import argparse
import json
import sys

from companion import config, patient_db
from companion.call_simulator import simulate_call
from companion.tts import synthesize_wav


def _print_result(result) -> None:
    report = result.report or {}
    print(f"\npatient   : {result.patient['name']} ({result.patient['id']})")
    print(f"engine    : {result.engine}")
    print(f"medicines : taken={report.get('medication_taken')} missed={report.get('missed_doses')}")
    symptoms = report.get("symptoms") or []
    print(f"symptoms  : {symptoms if symptoms else 'none reported'}")
    print(f"appt ok   : {report.get('appointment_ok')}")
    if result.escalation:
        print(f"escalation: {result.escalation.level} - {'; '.join(result.escalation.reasons) or 'none'}")
    print(f"summary   : {report.get('summary', '')}")


def _save_audio(result) -> None:
    lines = [t for t in result.transcript if t["role"] == "assistant"]
    if not lines:
        return
    config.AUDIO_DIR.mkdir(parents=True, exist_ok=True)
    text = lines[0]["content"]
    wav = synthesize_wav(text, result.patient["language_code"], speaker=result.patient.get("voice"))
    path = config.AUDIO_DIR / f"{result.patient['id']}-opening.wav"
    path.write_bytes(wav)
    print(f"audio     : {path} ({len(wav) // 1024} KB)")


def main() -> int:
    parser = argparse.ArgumentParser(description="Run a medicine check-in")
    parser.add_argument("patient_id", nargs="?", help="e.g. P001 (omit for all patients)")
    parser.add_argument("--offline", action="store_true", help="use the offline engine (no API key)")
    parser.add_argument("--audio", action="store_true", help="save the agent's opening line as WAV")
    parser.add_argument("--json", action="store_true", help="print the full result as JSON")
    args = parser.parse_args()

    patient_db.load_patients()

    if not args.patient_id:
        ids = [p["id"] for p in patient_db.list_patients()]
        if not ids:
            print("No patients in the database. Check patients.json.", file=sys.stderr)
            return 1
    else:
        ids = [args.patient_id]

    offline = args.offline or not config.have_sarvam_key()
    if not offline:
        print(f"Using Sarvam: chat={config.CHAT_MODEL} tts={config.TTS_MODEL} stt={config.STT_MODEL}")
    else:
        print("Offline engine: scripted agent lines + heuristic report (no API calls).")

    results = []
    for patient_id in ids:
        try:
            result = simulate_call(patient_id, offline=offline)
        except KeyError as exc:
            print(f"Patient {patient_id} not found. {exc}", file=sys.stderr)
            return 1
        results.append(result)
        if not args.json:
            _print_result(result)
        if args.audio:
            if offline:
                print("audio     : skipped (offline engine)")
            else:
                _save_audio(result)

    if args.json:
        print(json.dumps([r.as_dict() for r in results], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
