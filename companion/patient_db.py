"""SQLite-backed patient registry and check-in history.

Replaces the earlier in-memory store: a restart no longer loses adherence
history, which is the whole point of a daily check-in programme.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import config

SEED_FILE = config.BASE_DIR / "patients.json"

SCHEMA = """
CREATE TABLE IF NOT EXISTS patients (
    id            TEXT PRIMARY KEY,
    name          TEXT NOT NULL,
    language_code TEXT NOT NULL,
    phone         TEXT NOT NULL,
    age           INTEGER,
    condition     TEXT,
    medications   TEXT,
    appointment   TEXT,
    voice         TEXT,
    coordinator   TEXT,
    active        INTEGER DEFAULT 1
);

CREATE TABLE IF NOT EXISTS checkins (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    patient_id    TEXT NOT NULL,
    started_at    TEXT NOT NULL,
    finished_at   TEXT,
    channel       TEXT NOT NULL,
    engine        TEXT,
    completed     INTEGER DEFAULT 0,
    report_json   TEXT,
    transcript_json TEXT,
    escalation_level TEXT,
    FOREIGN KEY (patient_id) REFERENCES patients(id)
);

CREATE TABLE IF NOT EXISTS escalations (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    patient_id    TEXT NOT NULL,
    checkin_id    INTEGER,
    created_at    TEXT NOT NULL,
    level         TEXT NOT NULL,
    reasons_json  TEXT,
    delivered_json TEXT
);

CREATE INDEX IF NOT EXISTS idx_checkins_patient ON checkins(patient_id, started_at);
CREATE INDEX IF NOT EXISTS idx_escalations_patient ON escalations(patient_id, created_at);
"""


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect(db_path: Path | None = None) -> sqlite3.Connection:
    path = Path(db_path or config.DB_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(SCHEMA)
    return conn


def load_patients(seed_file: Path | None = None, db_path: Path | None = None) -> list[dict]:
    """Upsert patients.json into the DB and return all active patients."""
    seed = Path(seed_file or SEED_FILE)
    rows: list[dict] = []
    if seed.exists():
        payload = json.loads(seed.read_text(encoding="utf-8"))
        rows = payload.get("patients", payload if isinstance(payload, list) else [])
    with connect(db_path) as conn:
        for p in rows:
            conn.execute(
                """INSERT INTO patients (id, name, language_code, phone, age, condition,
                                         medications, appointment, voice, coordinator)
                   VALUES (?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(id) DO UPDATE SET
                     name=excluded.name,
                     language_code=excluded.language_code,
                     phone=excluded.phone,
                     age=excluded.age,
                     condition=excluded.condition,
                     medications=excluded.medications,
                     appointment=excluded.appointment,
                     voice=excluded.voice,
                     coordinator=excluded.coordinator""",
                (
                    p["id"], p["name"], p.get("language_code", "hi-IN"), p.get("phone", ""),
                    p.get("age"), p.get("condition"),
                    json.dumps(p.get("medications", []), ensure_ascii=False),
                    p.get("appointment"), p.get("voice"),
                    json.dumps(p.get("coordinator", {}), ensure_ascii=False),
                ),
            )
    return list_patients(db_path)


def _row_to_patient(row: sqlite3.Row) -> dict:
    patient = dict(row)
    for key in ("medications", "coordinator"):
        if isinstance(patient.get(key), str):
            try:
                patient[key] = json.loads(patient[key])
            except json.JSONDecodeError:
                patient[key] = [] if key == "medications" else {}
    patient["active"] = bool(patient.get("active", 1))
    return patient


def list_patients(db_path: Path | None = None, include_inactive: bool = False) -> list[dict]:
    with connect(db_path) as conn:
        sql = "SELECT * FROM patients"
        if not include_inactive:
            sql += " WHERE active = 1"
        sql += " ORDER BY id"
        return [_row_to_patient(r) for r in conn.execute(sql)]


def get_patient(patient_id: str, db_path: Path | None = None) -> dict | None:
    with connect(db_path) as conn:
        row = conn.execute("SELECT * FROM patients WHERE id = ?", (patient_id,)).fetchone()
        return _row_to_patient(row) if row else None


def get_patient_by_phone(phone: str, db_path: Path | None = None) -> dict | None:
    digits = "".join(ch for ch in (phone or "") if ch.isdigit())[-10:]
    if not digits:
        return None
    for patient in list_patients(db_path):
        if "".join(c for c in patient.get("phone", "") if c.isdigit()).endswith(digits):
            return patient
    return None


def start_checkin(patient_id: str, channel: str, engine: str | None = None,
                  db_path: Path | None = None) -> int:
    with connect(db_path) as conn:
        cur = conn.execute(
            "INSERT INTO checkins (patient_id, started_at, channel, engine) VALUES (?,?,?,?)",
            (patient_id, now_iso(), channel, engine),
        )
        return int(cur.lastrowid)


def finish_checkin(checkin_id: int, report: dict | None, transcript: list[dict],
                   escalation_level: str | None = None, completed: bool = True,
                   db_path: Path | None = None) -> None:
    with connect(db_path) as conn:
        conn.execute(
            """UPDATE checkins SET finished_at=?, completed=?, report_json=?,
                                   transcript_json=?, escalation_level=?
               WHERE id=?""",
            (
                now_iso(), 1 if completed else 0,
                json.dumps(report or {}, ensure_ascii=False),
                json.dumps(transcript or [], ensure_ascii=False),
                escalation_level, checkin_id,
            ),
        )


def record_escalation(patient_id: str, level: str, reasons: list[str],
                      checkin_id: int | None = None, delivered: dict | None = None,
                      db_path: Path | None = None) -> int:
    with connect(db_path) as conn:
        cur = conn.execute(
            """INSERT INTO escalations (patient_id, checkin_id, created_at, level,
                                        reasons_json, delivered_json)
               VALUES (?,?,?,?,?,?)""",
            (patient_id, checkin_id, now_iso(), level,
             json.dumps(reasons, ensure_ascii=False),
             json.dumps(delivered or {}, ensure_ascii=False)),
        )
        return int(cur.lastrowid)


def history(patient_id: str, limit: int = 10, db_path: Path | None = None) -> list[dict]:
    with connect(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM checkins WHERE patient_id = ? ORDER BY started_at DESC LIMIT ?",
            (patient_id, limit),
        ).fetchall()
    out = []
    for row in rows:
        item = dict(row)
        item["report"] = json.loads(item.pop("report_json") or "{}")
        item["transcript"] = json.loads(item.pop("transcript_json") or "[]")
        out.append(item)
    return out


def last_checkin(patient_id: str, db_path: Path | None = None) -> dict | None:
    items = history(patient_id, limit=1, db_path=db_path)
    return items[0] if items else None


def recent_escalations(limit: int = 50, db_path: Path | None = None) -> list[dict]:
    with connect(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM escalations ORDER BY created_at DESC LIMIT ?", (limit,)
        ).fetchall()
    out = []
    for row in rows:
        item = dict(row)
        item["reasons"] = json.loads(item.pop("reasons_json") or "[]")
        item["delivered"] = json.loads(item.pop("delivered_json") or "{}")
        out.append(item)
    return out


def checkin_dates(patient_id: str, db_path: Path | None = None) -> list[str]:
    with connect(db_path) as conn:
        rows = conn.execute(
            "SELECT started_at FROM checkins WHERE patient_id = ? AND completed = 1",
            (patient_id,),
        ).fetchall()
    return [r["started_at"][:10] for r in rows]


def missed_checkin_streak(patient_id: str, today: str, db_path: Path | None = None) -> int:
    """How many consecutive days (ending yesterday) have no completed check-in."""
    dates = set(checkin_dates(patient_id, db_path))
    if not dates:
        return 0
    from datetime import date, timedelta

    y, m, d = (int(x) for x in today.split("-"))
    day = date(y, m, d) - timedelta(days=1)
    streak = 0
    while streak < 30 and day.isoformat() not in dates:
        streak += 1
        day -= timedelta(days=1)
    return streak


def as_dict(patient: dict) -> dict[str, Any]:
    """Public-facing patient payload (no internal columns)."""
    return {k: patient[k] for k in
            ("id", "name", "language_code", "phone", "age", "condition",
             "medications", "appointment", "coordinator") if k in patient}
