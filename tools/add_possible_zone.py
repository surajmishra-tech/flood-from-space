r"""Add the "possible damage" zone to runs that were made before it existed.

Uses only what each run already saved (its confirmed flood map and the height data) - nothing is
downloaded again, and the confirmed map, numbers and reports stay exactly as they were.
    .venv\Scripts\python tools\add_possible_zone.py           (all runs)
    .venv\Scripts\python tools\add_possible_zone.py <run_id>  (one run)
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from floodmap import dem as D  # noqa: E402
from floodmap import flood as F  # noqa: E402
from floodmap import render  # noqa: E402
from floodmap.config import PARAMS, RUNS_DIR  # noqa: E402
from floodmap.rio import read_tif, resample, write_tif  # noqa: E402


def add(run: Path) -> float | None:
    conf_tif, dem_tif = run / "rasters" / "flood_conf.tif", run / "rasters" / "dem.tif"
    if not (conf_tif.exists() and dem_tif.exists() and (run / "facts.json").exists()):
        return None
    conf, grid = read_tif(conf_tif)
    z, dg = read_tif(dem_tif)
    hyd = D.hydrology(z, dg, PARAMS.stream_min_km2)         # the same terrain steps the pipeline uses
    hand = resample(hyd["hand"], dg, grid)
    slope = resample(D.slope_deg(z, dg.res), dg, grid)
    possible = F.possible_zone(conf, hand, slope, grid.res, PARAMS)
    write_tif(run / "rasters" / "flood_possible.tif", possible.astype("uint8"), grid, nodata=None)
    ov_file = run / "overlays" / "overlays.json"
    ov = json.loads(ov_file.read_text()) if ov_file.exists() else {}
    ov["possible"] = render.save_overlay(render.mask_rgba(possible, render.POSSIBLE_RGB, 120), grid, run / "overlays" / "possible.png")
    ov_file.write_text(json.dumps(ov, indent=1))
    facts = json.loads((run / "facts.json").read_text(encoding="utf-8"))
    km2 = round(float(possible.sum()) * grid.res ** 2 / 1e6, 3)
    facts["flood"]["possible_km2"] = km2
    (run / "facts.json").write_text(json.dumps(facts, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    return km2


if __name__ == "__main__":
    runs = [RUNS_DIR / r for r in sys.argv[1:]] or sorted(p for p in RUNS_DIR.iterdir() if p.is_dir())
    for run in runs:
        km2 = add(run)
        if km2 is not None:
            facts = json.loads((run / "facts.json").read_text(encoding="utf-8"))
            print(f"{run.name}  confirmed {facts['flood']['flood_km2']:6.3f} km2  + possible {km2:6.3f} km2  "
                  f"{facts['meta'].get('area_name') or ''}", flush=True)
