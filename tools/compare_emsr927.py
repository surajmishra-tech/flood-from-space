"""Case study: compare our results with the official Copernicus EMS maps (EMSR927).

CHECKING ONLY. This script reads validation/ (EMS files). The system itself
never reads that folder - its only inputs are a box, a date, Sentinel-1/2,
Copernicus DEM and OpenStreetMap from before the flood.

Run after the case-study runs exist:
    .venv\\Scripts\\python tools\\compare_emsr927.py

Writes report/case_study_emsr927.json, report/case_study_emsr927.md and
report/figures/emsr927_<AOI>.png
"""
from __future__ import annotations

import glob
import json
import sys
from pathlib import Path

import geopandas as gpd
import matplotlib
import numpy as np
import pyogrio
from rasterio import features
from scipy.ndimage import distance_transform_edt

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from floodmap.rio import read_tif  # noqa: E402

EMS_DIR = ROOT / "validation" / "EMSR927"
OUT = ROOT / "report"
TOLERANCE_M = 20.0   # EMS drew on 0.3-0.7 m images, we use 10 m pixels: allow ~2 pixels of position difference
AFFECTED = ("Destroyed", "Damaged", "Possibly damaged")

# EMS area -> our run of the system (input: a box + the flood date, nothing else)
CASES = {
    "AOI01": {"place": "Syabrubesi", "run": "fb7507902568"},
    "AOI02": {"place": "Timure", "run": "fb7507902568"},
    "AOI03": {"place": "Bidur", "run": "e600e350435e"},
    "AOI05": {"place": "Phosretar", "run": "5f7177780bb4"},
}


def ems_layers(aoi: str) -> dict[str, gpd.GeoDataFrame]:
    gpkg = glob.glob(str(EMS_DIR / f"EMSR927_{aoi}_*" / "*.gpkg"))
    if not gpkg:
        raise FileNotFoundError(f"EMS file for {aoi} not found in {EMS_DIR}")
    names = [n for n, _ in pyogrio.list_layers(gpkg[0])]
    out = {}
    for key in ("areaOfInterestA", "observedEventA", "builtUpP", "transportationL", "transportationP", "source"):
        layer = next((n for n in names if n.startswith(key)), None)
        if layer:
            out[key] = gpd.read_file(gpkg[0], layer=layer)
    return out


def scores(ours: np.ndarray, ref: np.ndarray, area: np.ndarray, res: float) -> dict:
    o, r = ours & area, ref & area
    tp, fp, fn = int((o & r).sum()), int((o & ~r).sum()), int((~o & r).sum())
    # tolerant version: a pixel counts as matching if the other map is within TOLERANCE_M
    d_ref = distance_transform_edt(~r) * res if r.any() else np.full(r.shape, np.inf)
    d_our = distance_transform_edt(~o) * res if o.any() else np.full(o.shape, np.inf)
    prec_tol = float((d_ref[o] <= TOLERANCE_M).mean()) if o.any() else 0.0
    rec_tol = float((d_our[r] <= TOLERANCE_M).mean()) if r.any() else 0.0
    px = res * res / 1e6
    prec = tp / (tp + fp) if tp + fp else 0.0
    rec = tp / (tp + fn) if tp + fn else 0.0
    return {"ours_km2": round(o.sum() * px, 3), "ems_km2": round(r.sum() * px, 3),
            "iou": round(tp / (tp + fp + fn), 3) if tp + fp + fn else 0.0,
            "precision": round(prec, 3), "recall": round(rec, 3),
            "f1": round(2 * prec * rec / (prec + rec), 3) if prec + rec else 0.0,
            "precision_20m": round(prec_tol, 3), "recall_20m": round(rec_tol, 3),
            "f1_20m": round(2 * prec_tol * rec_tol / (prec_tol + rec_tol), 3) if prec_tol + rec_tol else 0.0}


def sample(arr, grid, xs, ys, fill=np.inf):
    r, c = grid.rowcol(np.asarray(xs), np.asarray(ys))
    ok = grid.inside(r, c)
    out = np.full(len(r), fill, dtype="float64")
    out[ok] = arr[r[ok], c[ok]]
    return out


def compare(aoi: str, case: dict) -> dict:
    run = ROOT / "runs" / case["run"]
    conf, grid = read_tif(run / "rasters" / "flood_conf.tif")
    ems = {k: v.to_crs(grid.crs) for k, v in ems_layers(aoi).items() if k != "source"}
    area = features.rasterize([(g, 1) for g in ems["areaOfInterestA"].geometry], out_shape=grid.shape,
                              transform=grid.transform).astype(bool)
    aoi_km2 = float(ems["areaOfInterestA"].area.sum() / 1e6)
    covered = float(area.sum() * grid.res ** 2 / 1e6 / aoi_km2)
    ref = features.rasterize([(g, 1) for g in ems["observedEventA"].geometry], out_shape=grid.shape,
                             transform=grid.transform).astype(bool)
    ours = conf >= 2   # medium + high confidence = what we report
    ours_all = conf >= 1  # including low confidence
    res = {"aoi": aoi, "place": case["place"], "run": case["run"], "aoi_km2": round(aoi_km2, 2),
           "aoi_covered_by_our_box": round(covered, 3),
           "flood_extent": scores(ours, ref, area, grid.res),
           "flood_extent_incl_low": scores(ours_all, ref, area, grid.res)}

    # buildings: how many of the buildings EMS marks as affected sit on/next to our flood map?
    dist_ours = distance_transform_edt(~ours) * grid.res if ours.any() else np.full(ours.shape, np.inf)
    b = ems.get("builtUpP")
    facts = json.loads((run / "facts.json").read_text(encoding="utf-8"))
    our_b = gpd.read_file(run / "layers" / "buildings_hit.geojson").to_crs(grid.crs)
    aoi_geom = ems["areaOfInterestA"].geometry.union_all()
    our_b_in = our_b[our_b.geometry.representative_point().within(aoi_geom)]
    bres = {"ems_affected": 0, "ours_hit_osm": int(len(our_b_in))}
    if b is not None and len(b):
        b = b[b.geometry.within(aoi_geom)]
        d = sample(dist_ours, grid, b.geometry.x, b.geometry.y)
        bres["ems_affected"] = int(len(b))
        bres["found_within_20m_pct"] = round(100 * float((d <= TOLERANCE_M).mean()), 1)
        for grade in AFFECTED:
            m = (b["damage_gra"] == grade).to_numpy()
            if m.any():
                bres[f"found_{grade.lower().replace(' ', '_')}_pct"] = round(100 * float((d[m] <= TOLERANCE_M).mean()), 1)
                bres[f"ems_{grade.lower().replace(' ', '_')}"] = int(m.sum())
    res["buildings"] = bres

    # roads: EMS affected road length that lies on/next to our flood map
    rd = ems.get("transportationL")
    if rd is not None and len(rd):
        rd = gpd.clip(rd, aoi_geom)
        aff = rd[rd["damage_gra"].isin(AFFECTED)]
        pts = []
        for geom in aff.geometry:
            for line in getattr(geom, "geoms", [geom]):
                n = max(2, int(line.length // 5))
                pts += [line.interpolate(i / (n - 1), normalized=True) for i in range(n)]
        d = sample(dist_ours, grid, [p.x for p in pts], [p.y for p in pts]) if pts else np.array([])
        our_cut = gpd.read_file(run / "layers" / "roads_cut.geojson").to_crs(grid.crs)
        our_cut_km = float(gpd.clip(our_cut, aoi_geom).length.sum() / 1000) if len(our_cut) else 0.0
        res["roads"] = {"ems_affected_km": round(float(aff.length.sum() / 1000), 2),
                        "ems_total_km": round(float(rd.length.sum() / 1000), 2),
                        "ours_cut_km": round(our_cut_km, 2),
                        "ems_affected_found_within_20m_pct": round(100 * float((d <= TOLERANCE_M).mean()), 1) if len(d) else None}

    # bridges: EMS destroyed/damaged bridges vs our bridges reached by the flood
    br = ems.get("transportationP")
    our_br = gpd.read_file(run / "layers" / "bridges.geojson").to_crs(grid.crs)
    our_br_in = our_br[our_br.geometry.within(aoi_geom)] if len(our_br) else our_br
    our_hit = our_br_in[our_br_in["hit"].astype(bool)] if len(our_br_in) else our_br_in
    if br is not None and len(br):
        br = br[br.geometry.within(aoi_geom) & br["damage_gra"].isin(AFFECTED)]
        matched = 0
        for p in br.geometry:
            near_hit = len(our_hit) and our_hit.distance(p).min() <= 150
            near_flood = sample(dist_ours, grid, [p.x], [p.y])[0] <= 50
            matched += bool(near_hit or near_flood)
        res["bridges"] = {"ems_affected": int(len(br)), "ours_hit_osm": int(len(our_hit)),
                          "ems_bridges_on_our_flood_pct": round(100 * matched / len(br), 1) if len(br) else None}
    res["image_dates"] = {"ours_radar": facts["images"]["radar"], "ours_optical": facts["images"].get("optical")}
    figure(aoi, case["place"], grid, ours, ref, area, ems)
    return res


def figure(aoi, place, grid, ours, ref, area, ems):
    rows, cols = np.where(area)
    if rows.size == 0:
        return
    pad = 20
    r0, r1 = max(0, rows.min() - pad), min(grid.height, rows.max() + pad)
    c0, c1 = max(0, cols.min() - pad), min(grid.width, cols.max() + pad)
    img = np.ones((r1 - r0, c1 - c0, 3))
    o, r = ours[r0:r1, c0:c1], ref[r0:r1, c0:c1]
    img[o & r] = (0.13, 0.63, 0.30)   # both: green
    img[o & ~r] = (0.95, 0.55, 0.10)  # only ours: orange
    img[~o & r] = (0.45, 0.25, 0.75)  # only EMS: purple
    left = grid.transform.c + c0 * grid.res
    top = grid.transform.f - r0 * grid.res
    ext = (left, left + (c1 - c0) * grid.res, top - (r1 - r0) * grid.res, top)
    fig, ax = plt.subplots(figsize=(5.2, 5.2), dpi=150)
    ax.imshow(img, extent=ext, interpolation="nearest")
    ems["areaOfInterestA"].boundary.plot(ax=ax, color="black", linewidth=0.8, linestyle="--")
    b = ems.get("builtUpP")
    if b is not None and len(b):
        b[b["damage_gra"] == "Destroyed"].plot(ax=ax, color="black", markersize=0.6)
    ax.set_xlim(ext[0], ext[1])
    ax.set_ylim(ext[2], ext[3])
    ax.set_xticks([])
    ax.set_yticks([])
    ax.set_title(f"{place} ({aoi}): green = both, orange = only ours,\npurple = only EMS, black dots = EMS destroyed buildings",
                 fontsize=7.5)
    (OUT / "figures").mkdir(parents=True, exist_ok=True)
    fig.tight_layout()
    fig.savefig(OUT / "figures" / f"emsr927_{aoi}.png")
    plt.close(fig)


def markdown(results: list[dict]) -> str:
    lines = ["# Case study: our system vs Copernicus EMS EMSR927", "",
             "Checking only - EMS data was never an input. Our results use medium + high confidence.",
             f"'20 m' scores accept up to {TOLERANCE_M:.0f} m position difference (EMS used 0.3-0.7 m images, we use 10 m pixels).", "",
             "| Area | EMS km² | Ours km² | IoU | Precision | Recall | F1 | F1 (20 m) | EMS hit buildings | found (20 m) | EMS bridges | on our flood |",
             "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in results:
        f, b, br = r["flood_extent"], r["buildings"], r.get("bridges", {})
        lines.append(f"| {r['place']} ({r['aoi']}) | {f['ems_km2']} | {f['ours_km2']} | {f['iou']} | {f['precision']} | "
                     f"{f['recall']} | {f['f1']} | {f['f1_20m']} | {b['ems_affected']} | {b.get('found_within_20m_pct', '-')}% | "
                     f"{br.get('ems_affected', '-')} | {br.get('ems_bridges_on_our_flood_pct', '-')}% |")
    lines += ["", "Credit: European Union, Copernicus Emergency Management Service data (EMSR927)."]
    return "\n".join(lines)


def main():
    results = []
    for aoi, case in CASES.items():
        if not (ROOT / "runs" / case["run"] / "facts.json").exists():
            print(f"skip {aoi}: run {case['run']} not finished")
            continue
        r = compare(aoi, case)
        results.append(r)
        f = r["flood_extent"]
        print(f"{aoi} {case['place']:11s} EMS {f['ems_km2']:.2f} km² ours {f['ours_km2']:.2f} | IoU {f['iou']:.2f} "
              f"P {f['precision']:.2f} R {f['recall']:.2f} F1 {f['f1']:.2f} | F1@20m {f['f1_20m']:.2f} | "
              f"buildings found {r['buildings'].get('found_within_20m_pct')}% of {r['buildings']['ems_affected']}")
    OUT.mkdir(exist_ok=True)
    (OUT / "case_study_emsr927.json").write_text(json.dumps(results, indent=1, default=str), encoding="utf-8")
    (OUT / "case_study_emsr927.md").write_text(markdown(results), encoding="utf-8")
    print("written:", OUT / "case_study_emsr927.md")


if __name__ == "__main__":
    main()
