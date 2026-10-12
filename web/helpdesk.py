"""Help requests from people in a flood area (the "I need help" side of the dashboard).

Demo only: a request is saved in helpdesk/requests.json on this computer and shown on
the "Rescue team" screen (behind the RESCUE_PIN, if one is set in .env). Nothing is sent
to a real government service.
"""
from __future__ import annotations

import json
import threading
import uuid
from datetime import datetime, timezone

from floodmap.config import ROOT

FILE = ROOT / "helpdesk" / "requests.json"
STATUSES = ("new", "seen", "team_sent", "helped")
_lock = threading.Lock()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _load() -> list[dict]:
    try:
        return json.loads(FILE.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return []


def _save(items: list[dict]) -> None:
    FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(items, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(FILE)


def _safety_fields(safety: dict) -> dict:
    """What we store from a danger check (floodmap.safety.check)."""
    s = safety.get("settlement") or {}
    return {
        "level": safety["level"],
        "rank": safety["rank"],
        "run_id": safety.get("run_id"),
        "area_name": safety.get("area_name"),
        "settlement": s.get("name"),
        "settlement_ne": s.get("name_ne"),
        "settlement_status": s.get("status"),
        "flood_m": safety.get("flood_m"),
        "summary_en": safety["text"]["en"]["title"],
    }


def add(person: dict, safety: dict) -> dict:
    """Save a new request. The danger level comes from our own check, not from the sender."""
    rec = {
        "id": uuid.uuid4().hex[:6].upper(),
        "created_utc": _now(),
        "status": "new",
        "history": [{"status": "new", "utc": _now()}],
        **person,
        **_safety_fields(safety),
        "checked_utc": _now(),
    }
    with _lock:
        items = _load()
        items.append(rec)
        _save(items)
    return rec


def recheck(bbox: list[float] | None = None) -> dict:
    """Run the danger check again for every open request (only those inside bbox, if given),
    with the newest maps. When the level changes, the history says from what to what."""
    from floodmap.safety import check
    inside = lambda r: bbox is None or (bbox[0] <= r["lon"] <= bbox[2] and bbox[1] <= r["lat"] <= bbox[3])  # noqa: E731
    todo = [r for r in _load() if r["status"] != "helped" and inside(r)]
    fresh = {r["id"]: _safety_fields(check(r["lon"], r["lat"])) for r in todo}  # slow part, outside the lock
    changed = 0
    with _lock:
        items = _load()
        for rec in items:
            new = fresh.get(rec["id"])
            if new is None:
                continue
            if new["level"] != rec["level"]:
                rec["history"].append({"status": rec["status"], "utc": _now(), "level_from": rec["level"], "level_to": new["level"]})
                changed += 1
            rec.update(new)
            rec["checked_utc"] = _now()
        _save(items)
    return {"checked": len(todo), "changed": changed}


def summary() -> dict:
    """Counts only (no names, phones or places): safe to show anyone."""
    items = _load()
    stamp = FILE.stat().st_mtime if FILE.exists() else 0  # changes whenever a request is added or updated
    return {"new": sum(r["status"] == "new" for r in items), "open": sum(r["status"] != "helped" for r in items), "stamp": stamp}


def all_requests() -> list[dict]:
    """Rescue-team order: open requests first, then by danger, then not-yet-handled first,
    then more people, then oldest."""
    done = lambda r: r["status"] == "helped"  # noqa: E731
    return sorted(_load(), key=lambda r: (done(r), r["rank"], STATUSES.index(r["status"]),
                                          -(r.get("people") or 1), r["created_utc"]))


def get(rid: str) -> dict | None:
    return next((r for r in _load() if r["id"] == rid), None)


def set_status(rid: str, status: str) -> dict | None:
    with _lock:
        items = _load()
        rec = next((r for r in items if r["id"] == rid), None)
        if rec is None:
            return None
        rec["status"] = status
        rec["history"].append({"status": status, "utc": _now()})
        _save(items)
    return rec
