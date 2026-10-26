"""End-to-end-ish checks: simulator, storage, scheduler, HTTP surface."""
from __future__ import annotations

import os
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient

from companion import patient_db
from companion.call_simulator import simulate_call
from companion.scheduler import is_due


def test_offline_simulation_runs_end_to_end(db_path):
    result = simulate_call("P001", offline=True, verbose=False, db_path=db_path)
    assert result.engine == "offline-heuristic"
    assert result.report["engine"] == "offline-heuristic"
    assert result.transcript
    assert result.checkin_id is not None

    stored = patient_db.last_checkin("P001", db_path=db_path)
    assert stored["completed"] == 1
    assert stored["transcript"]


def test_history_accumulates(db_path):
    simulate_call("P001", offline=True, verbose=False, db_path=db_path)
    simulate_call("P001", offline=True, verbose=False, db_path=db_path)
    assert len(patient_db.history("P001", db_path=db_path)) == 2


def test_escalation_is_recorded_for_missed_doses(db_path):
    simulate_call("P002", offline=True, verbose=False, db_path=db_path)
    rows = patient_db.recent_escalations(db_path=db_path)
    assert rows, "expected an escalation row for the Tamil patient with missed doses"


def test_unknown_patient_raises(db_path):
    import pytest

    with pytest.raises(KeyError):
        simulate_call("P999", offline=True, verbose=False, db_path=db_path)


def test_scheduler_due_logic(db_path):
    tz = ZoneInfo("Asia/Kolkata")
    late = datetime.now(tz).replace(hour=23, minute=0, second=0, microsecond=0)
    p001 = patient_db.get_patient("P001", db_path=db_path)

    assert is_due(p001, now=late, db_path=db_path) is True      # never called
    simulate_call("P001", offline=True, verbose=False, db_path=db_path)
    assert is_due(p001, now=late, db_path=db_path) is False     # already done today


def test_health_endpoint(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    from companion import config

    monkeypatch.setattr(config, "DB_PATH", tmp_path / "api.db")
    monkeypatch.setattr(config, "ESCALATION_LOG", tmp_path / "esc.jsonl")
    patient_db.load_patients(db_path=tmp_path / "api.db")

    from companion.server import app

    with TestClient(app) as client:
        health = client.get("/health")
        assert health.status_code == 200
        assert health.json()["status"] == "ok"

        patients = client.get("/api/patients").json()
        assert {p["id"] for p in patients} >= {"P001", "P002", "P003"}

        assert client.get("/api/checkins/P001").json() == []
        assert client.post("/exotel/stream-url", json={}).status_code == 400
