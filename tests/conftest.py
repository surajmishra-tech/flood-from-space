"""Shared set-up. The tests never touch the real help requests and never call the language model."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

os.environ.setdefault("FLOOD_TESTS", "1")  # web.app: leave saved results alone

# On GitHub's Linux machines Python can crash while closing down, after every test has passed
# (two of the libraries each bring their own copy of GDAL). There, leave straight after the report.
_exit_code = {"value": 0}


def pytest_sessionfinish(session, exitstatus):
    _exit_code["value"] = int(exitstatus)


def pytest_unconfigure(config):
    if os.getenv("CI"):
        sys.stdout.flush()
        sys.stderr.flush()
        os._exit(_exit_code["value"])

ROOT = Path(__file__).resolve().parents[1]
MAIN_RUN = ROOT / "runs" / "fb7507902568"   # Upper Trishuli, 26 Aug 2026 (the main saved result)
needs_main_run = pytest.mark.skipif(not (MAIN_RUN / "facts.json").exists(), reason="the main saved result is not in this copy")


def small_facts() -> dict:
    """A tiny made-up result in the same shape as a run's facts.json."""
    return {
        "meta": {"area_name": "Test valley", "flood_date": "2026-08-26", "area_km2": 180.0, "resolution_m": 10},
        "flood": {"flood_km2": 1.73, "high_km2": 0.48, "new_water_km2": 0.15, "debris_km2": 1.58, "valley_seen_pct": 99.6},
        "buildings": {"total": 3396, "hit": 358},
        "roads": {"total_km": 120.0, "flooded_km": 14.2},
        "bridges": {"total": 16, "hit": 14},
        "settlements": {
            "total": 40, "cut_off": 1, "detour": 0, "detour_list": [],
            "cut_off_list": [{"name": "Resuo", "name_ne": "रेसुवो", "priority": 88, "buildings_nearby": 87,
                              "breaks": [{}], "dist_before_km": 25.0}],
        },
        "hospitals": {"hit": 0},
        "images": {"radar": [], "optical": None},
    }


@pytest.fixture
def client(tmp_path, monkeypatch):
    """The web app with an empty, temporary help inbox and fresh per-visitor counters."""
    from fastapi.testclient import TestClient

    from web import app as webapp
    from web import helpdesk
    monkeypatch.setattr(helpdesk, "FILE", tmp_path / "requests.json")
    monkeypatch.setattr(webapp, "_hits", {})
    monkeypatch.setattr(webapp, "_pin_fails", {})
    monkeypatch.setattr(webapp, "RESCUE_PIN", "")
    return TestClient(webapp.app)


@pytest.fixture
def fake_run(tmp_path, monkeypatch):
    """A saved result made of small_facts(), in a temporary runs folder."""
    from web import app as webapp
    runs = tmp_path / "runs"
    (runs / "test01").mkdir(parents=True)
    (runs / "test01" / "facts.json").write_text(json.dumps(small_facts()), encoding="utf-8")
    monkeypatch.setattr(webapp, "RUNS_DIR", runs)
    return "test01"
