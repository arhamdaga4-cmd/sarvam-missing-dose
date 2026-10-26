"""Shared fixtures: a throwaway SQLite database seeded from patients.json."""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from companion import patient_db  # noqa: E402


@pytest.fixture()
def db_path(tmp_path, monkeypatch):
    path = tmp_path / "test.db"
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    patient_db.load_patients(db_path=path)
    return path


@pytest.fixture()
def patient(db_path):
    return patient_db.get_patient("P001", db_path=db_path)
