"""Command line.

  python -m floodmap run --bbox 85.30,28.13,85.40,28.30 --date 2026-08-26 --name "Upper Trishuli"
  python -m floodmap sitrep <run_id>
  python -m floodmap floodpath --lon 85.37 --lat 28.27 --date 2026-08-26
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date


def main(argv=None):
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")  # Nepali names in the Windows console
    p = argparse.ArgumentParser(prog="floodmap", description="Flood damage mapping from space")
    sub = p.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("run", help="map one area + flood date")
    r.add_argument("--bbox", required=True, help="west,south,east,north in degrees")
    r.add_argument("--date", required=True, help="flood date YYYY-MM-DD")
    r.add_argument("--time", help="flood time HH:MM in UTC (optional)")
    r.add_argument("--source", choices=["cdse", "pc"], help="image source (default from .env)")
    r.add_argument("--res", type=float, help="pixel size in metres (default: automatic)")
    r.add_argument("--name", help="a name for this area")
    r.add_argument("--force", action="store_true", help="ignore a saved result and run again")
    r.add_argument("--no-report", action="store_true", help="skip the situation reports")

    s = sub.add_parser("sitrep", help="(re)write the situation reports of a run")
    s.add_argument("run_id")

    f = sub.add_parser("floodpath", help="bonus: trace the flood path downhill from a point")
    f.add_argument("--lon", type=float, required=True)
    f.add_argument("--lat", type=float, required=True)
    f.add_argument("--date", help="use OpenStreetMap from before this date (default: latest)")

    a = p.parse_args(argv)
    if a.cmd == "run":
        from .pipeline import run
        bbox = [float(v) for v in a.bbox.split(",")]
        out = run(bbox, date.fromisoformat(a.date), source=a.source, res=a.res, time_utc=a.time,
                  name=a.name, force=a.force, progress=lambda pct, msg: print(f"[{pct:3d}%] {msg}", flush=True))
        if not a.no_report:
            from .sitrep import write_reports
            write_reports(out, progress=lambda msg: print(f"[sitrep] {msg}", flush=True))
        facts = json.loads((out / "facts.json").read_text(encoding="utf-8"))
        print(json.dumps({k: facts[k] for k in ("flood", "buildings", "roads")}, indent=1))
        print("cut off:", facts["settlements"]["cut_off"], "| detour:", facts["settlements"]["detour"],
              "| bridges hit:", facts["bridges"]["hit"], "of", facts["bridges"]["total"])
        print("run folder:", out)
    elif a.cmd == "sitrep":
        from .config import RUNS_DIR
        from .sitrep import write_reports
        write_reports(RUNS_DIR / a.run_id, progress=print)
    elif a.cmd == "floodpath":
        from .floodpath import trace
        res = trace(a.lon, a.lat, date.fromisoformat(a.date) if a.date else None)
        print(json.dumps({k: v for k, v in res.items() if k != "geojson"}, indent=1, ensure_ascii=False))


if __name__ == "__main__":
    main()
