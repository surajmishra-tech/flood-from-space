"""One run of the system: an area + a flood date -> maps, numbers and reports.

Everything a run produces goes to runs/<run_id>/ :
  run.json            inputs, progress log, timings, warnings
  facts.json          EVERY number the system knows (reports + copilot read only this)
  layers/*.geojson    map layers: flood, hit buildings, cut roads, bridges, villages ...
  overlays/*.png      map pictures (+ overlays.json with their positions)
  rasters/*.tif       flood class + confidence (used to check against EMS afterwards)
"""
from __future__ import annotations

import hashlib
import json
import math
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import geopandas as gpd
import numpy as np
from rasterio import features
from rasterio.enums import Resampling
from scipy.spatial import cKDTree
from shapely.geometry import box, shape

from . import damage, network, osm, priority, render
from . import dem as D
from . import flood as F
from . import search as S
from .config import CACHE_DIR, DATA_SOURCE, PARAMS, RUNS_DIR, VERSION
from .grid import Grid, make_grid, to_lonlat
from .names import english_name, nepali_name
from .rio import resample, write_tif
from .sources import get_source

ATTRIBUTION = [
    "Contains modified Copernicus Sentinel data {years}.",
    "Produced using Copernicus WorldDEM-30 © DLR e.V. 2010–2014 and © Airbus Defence and Space GmbH "
    "2014–2018 provided under COPERNICUS by the European Union and ESA; all rights reserved.",
    "© OpenStreetMap contributors.",
]


def run_id(bbox, flood_date: date, source: str, res: float, time_utc: str | None) -> str:
    key = f"{VERSION}|{source}|{[round(v, 4) for v in bbox]}|{flood_date}|{time_utc}|{res}"
    return hashlib.sha1(key.encode()).hexdigest()[:12]


class Log:
    def __init__(self, path: Path, info: dict, progress=None):
        self.path, self.info, self.progress = path, info, progress
        self.t0 = time.time()
        self.info.setdefault("log", [])
        self.info.setdefault("warnings", [])

    def step(self, pct: int, msg: str):
        self.info["progress"] = pct
        self.info["message"] = msg
        self.info["log"].append({"t": round(time.time() - self.t0, 1), "pct": pct, "msg": msg})
        self.save()
        if self.progress:
            self.progress(pct, msg)

    def warn(self, msg: str):
        self.info["warnings"].append(msg)
        self.save()

    def save(self):
        self.path.write_text(json.dumps(self.info, indent=2, default=str), encoding="utf-8")


def _scene_key(src, scene, grid: Grid, extra="") -> str:
    k = f"{src.name}|{scene.sensor}|{scene.platform}|{scene.track}|{scene.time.isoformat()}|{grid.to_dict()}|{extra}"
    return hashlib.sha1(k.encode()).hexdigest()[:20]


def read_s1_cached(src, scene, grid):
    path = CACHE_DIR / "scenes" / f"s1_{_scene_key(src, scene, grid)}.npz"
    if path.exists():
        z = np.load(path)
        return {"vv": z["vv"], "vh": z["vh"]}
    data = src.read_s1(scene, grid)
    path.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(path, vv=data["vv"], vh=data["vh"])
    return data


def read_s2_cached(src, scene, grid, bands):
    path = CACHE_DIR / "scenes" / f"s2_{_scene_key(src, scene, grid, ','.join(bands))}.npz"
    if path.exists():
        z = np.load(path)
        return {b: z[b] for b in bands}
    data = src.read_s2(scene, grid, bands=bands)
    path.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(path, **data)
    return data


def extend_bbox(bbox, km: float):
    w, s, e, n = bbox
    lat = math.radians((s + n) / 2)
    dlat, dlon = km / 111.0, km / (111.0 * max(0.2, math.cos(lat)))
    return (max(-180, w - dlon), max(-85, s - dlat), min(180, e + dlon), min(85, n + dlat))


def to_geojson(gdf: gpd.GeoDataFrame, path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    g = gdf.to_crs("EPSG:4326") if gdf.crs is not None else gdf
    path.write_text(g.to_json(drop_id=True, na="null"), encoding="utf-8")


def run(bbox, flood_date: date, source: str | None = None, res: float | None = None,
        time_utc: str | None = None, name: str | None = None, force: bool = False,
        progress=None, params=PARAMS) -> Path:
    bbox = tuple(float(v) for v in bbox)
    if isinstance(flood_date, str):
        flood_date = date.fromisoformat(flood_date)
    grid = make_grid(bbox, res)
    src_name = (source or DATA_SOURCE or "pc").lower()
    rid = run_id(bbox, flood_date, src_name, grid.res, time_utc)
    out = RUNS_DIR / rid
    if (out / "facts.json").exists() and not force:
        if progress:
            progress(100, "Done (saved result)")
        return out
    out.mkdir(parents=True, exist_ok=True)
    info = {"run_id": rid, "name": name or "", "bbox": list(bbox), "flood_date": flood_date.isoformat(),
            "time_utc": time_utc, "source": src_name, "res_m": grid.res, "status": "running",
            "started": datetime.now(timezone.utc).isoformat(timespec="seconds"), "version": VERSION}
    log = Log(out / "run.json", info, progress)
    try:
        _run(out, bbox, flood_date, src_name, grid, time_utc, log, params)
        info["status"] = "done"
        log.step(100, "Done")
    except Exception as err:  # noqa: BLE001 - report any failure to the user
        info["status"] = "error"
        info["error"] = str(err)
        info["traceback"] = traceback.format_exc()
        log.step(info.get("progress", 0), f"Error: {err}")
        raise
    finally:
        info["finished"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        log.save()
    return out


def _run(out: Path, bbox, flood_date: date, src_name: str, grid: Grid, time_utc, log: Log, params):
    t_start = time.time()
    lon_c, lat_c = (bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2
    start = S.flood_start_utc(flood_date, lon_c, time_utc)
    (out / "layers").mkdir(exist_ok=True)
    (out / "overlays").mkdir(exist_ok=True)
    overlays = {}

    # ---------------- 1. Height data ----------------
    log.step(3, "Loading height data (Copernicus DEM) and finding the rivers")
    dg = grid.coarser(30, margin_m=3000)
    z = D.fetch_dem(dg)
    hyd = D.hydrology(z, dg, params.stream_min_km2)
    hand = resample(hyd["hand"], dg, grid)
    slope = resample(D.slope_deg(z, dg.res), dg, grid)
    valley = np.isfinite(hand) & (hand <= params.hand_max_m) & (slope <= params.slope_max_deg)
    hs = resample(D.hillshade(z, dg.res), dg, grid)
    overlays["hillshade"] = render.save_overlay(render.hillshade_rgba(hs), grid, out / "overlays" / "hillshade.png")
    overlays["valley"] = render.save_overlay(render.mask_rgba(valley, (255, 230, 0), 70), grid, out / "overlays" / "valley.png")
    np.save(out / "hydro_fdir.npy", hyd["fdir"])
    (out / "hydro_grid.json").write_text(json.dumps(dg.to_dict()))
    write_tif(out / "rasters" / "dem.tif", z, dg)

    # ---------------- 2. Radar images ----------------
    src = get_source(src_name)
    log.step(10, f"Searching Sentinel-1 radar images ({src.label})")
    win = timedelta(days=params.s1_window_days)
    scenes = src.find_s1(bbox, start - win, start + win)
    stacks = S.s1_stacks(scenes, start, params)
    if not stacks:
        log.warn("No Sentinel-1 radar images on the same track before AND after this date (within "
                 f"{params.s1_window_days} days). Radar part skipped.")
    todo = {id(s): s for pre, post in stacks for s in (pre + post)}
    log.step(14, f"Downloading {len(todo)} radar images (before + after, same tracks)")
    with ThreadPoolExecutor(max_workers=4) as pool:
        got = dict(zip(todo.keys(), pool.map(lambda s: read_s1_cached(src, s, grid), todo.values())))
    tracks = []
    for pre, post in stacks:
        blind30 = D.radar_blind_mask(z, dg.res, pre[0].direction, lat_c, params)
        blind = resample(blind30.astype("float32"), dg, grid, Resampling.nearest) > 0.5
        ts = F.TrackStack(pre[0].track, pre[0].direction, pre, post,
                          [got[id(s)]["vv"] for s in pre], [got[id(s)]["vh"] for s in pre],
                          [got[id(s)]["vv"] for s in post], [got[id(s)]["vh"] for s in post], blind)
        tracks.append(F.track_change(ts, valley, grid.res, params))
    log.step(40, "Radar change computed")
    if tracks:
        overlays["radar_before"] = render.save_overlay(render.gray(tracks[0]["pre_vv"]), grid, out / "overlays" / "radar_before.png")
        overlays["radar_after"] = render.save_overlay(render.gray(tracks[0]["post_vv"]), grid, out / "overlays" / "radar_after.png")

    # ---------------- 3. Optical images (when clear) ----------------
    optical, opt_info = None, None
    try:
        log.step(42, "Looking for cloud-free Sentinel-2 images")
        s2 = src.find_s2(bbox, start - timedelta(days=params.s2_before_days),
                         start + timedelta(days=params.s2_after_days), max_cloud=90)
        coarse = grid.coarser(60)
        valley_c = resample(valley.astype("float32"), grid, coarse, Resampling.max) > 0.5
        before, after = S.s2_pick(src, s2, start, coarse, valley_c, params) if s2 else (None, None)
        if before and after:
            bands = ("B02", "B03", "B04", "B08", "B11", "SCL")
            log.step(48, f"Downloading Sentinel-2 images {before[0].time:%d %b} and {after[0].time:%d %b}")
            pre_o = read_s2_cached(src, before[0], grid, bands)
            post_o = read_s2_cached(src, after[0], grid, bands)
            optical = F.optical_change(pre_o, post_o, valley)
            opt_info = {"before": before[0].time.strftime("%Y-%m-%d"), "after": after[0].time.strftime("%Y-%m-%d"),
                        "clear_valley_pct": optical["clear_pct"]}
            for key, img in (("photo_before", pre_o), ("photo_after", post_o)):
                rgb = np.clip(np.dstack([img["B04"], img["B03"], img["B02"]]) * 3.5, 0, 1)
                a = np.isfinite(img["B04"]).astype("uint8") * 255
                rgba = np.dstack([(np.nan_to_num(rgb) * 255).astype("uint8"), a])
                overlays[key] = render.save_overlay(rgba, grid, out / "overlays" / f"{key}.png")
        else:
            log.warn("No cloud-free Sentinel-2 image pair over the valley - radar only.")
    except Exception as err:  # noqa: BLE001 - optical is optional
        log.warn(f"Optical step skipped: {err}")

    # ---------------- 4. Flood map ----------------
    log.step(55, "Mapping flooded and debris-covered areas")
    fr = F.map_flood(tracks, optical, valley, grid.res, params)
    write_tif(out / "rasters" / "flood_class.tif", fr.cls, grid, nodata=None)
    write_tif(out / "rasters" / "flood_conf.tif", fr.conf, grid, nodata=None)
    overlays["flood"] = render.save_overlay(render.flood_rgba(fr.cls, fr.conf), grid, out / "overlays" / "flood.png")
    overlays["radar_change"] = render.save_overlay(render.diverging(np.where(valley, fr.score, np.nan)), grid,
                                                   out / "overlays" / "radar_change.png")
    flood_mask = fr.conf >= F.MEDIUM
    polys = []
    code = np.where(fr.conf > 0, fr.cls.astype("int32") * 10 + fr.conf, 0).astype("int32")
    for geom, val in features.shapes(code, mask=code > 0, transform=grid.transform):
        polys.append({"geometry": shape(geom), "kind": "new water" if val // 10 == F.NEW_WATER else "debris / mud",
                      "confidence": {1: "low", 2: "medium", 3: "high"}[val % 10]})
    fl = gpd.GeoDataFrame(polys, geometry="geometry", crs=grid.crs) if polys else \
        gpd.GeoDataFrame({"kind": [], "confidence": []}, geometry=[], crs=grid.crs)
    if len(fl):
        fl["geometry"] = fl.geometry.simplify(grid.res * 0.5)
    to_geojson(fl, out / "layers" / "flood.geojson")

    # ---------------- 5. OpenStreetMap before the flood ----------------
    snap_date = osm.snapshot_date(flood_date)
    ext = extend_bbox(bbox, params.extend_km)
    log.step(60, f"Loading OpenStreetMap as it was on {snap_date} (before the flood)")

    def safe_fetch(kind, box_):
        try:
            return osm.fetch(kind, box_, snap_date).to_crs(grid.crs)
        except Exception as err:  # noqa: BLE001 - keep going with what we have
            log.warn(f"OpenStreetMap {kind} could not be loaded ({str(err)[:120]}). Answers using {kind} are missing.")
            return gpd.GeoDataFrame({"geometry": []}, geometry="geometry", crs=grid.crs)

    bld = safe_fetch("buildings", bbox)
    log.step(66, "Loading roads, villages and hospitals")
    roads = safe_fetch("roads", ext)
    places = safe_fetch("places", ext)
    hosp = safe_fetch("hospitals", ext)
    for col in ("place", "bridge", "highway", "name"):
        for g_ in (places, roads):
            if col not in g_.columns:
                g_[col] = None
    osm_source = osm.source_used("buildings", bbox, snap_date)

    # ---------------- 6. Damage ----------------
    log.step(72, "Checking which buildings, roads and bridges were hit")
    dist, idx = damage.distance_to_flood(flood_mask, grid.res)
    aoi_utm = box(*grid.bounds)
    bld = bld[bld.geometry.intersects(aoi_utm)].reset_index(drop=True)
    bh = damage.building_hits(bld, dist, idx, fr.conf, grid)
    hit_b = bh[bh["hit"]].copy()
    hit_b["confidence"] = hit_b["conf"].map({1: "low", 2: "medium", 3: "high"}).fillna("medium")
    to_geojson(hit_b[["confidence", "geometry"]], out / "layers" / "buildings_hit.geojson")

    g = network.build_graph(roads)
    cut, wet_m, inside = network.edge_flooding(g, dist, grid, params.bridge_hit_m)
    to_geojson(network.cut_lines(g, cut, grid.crs), out / "layers" / "roads_cut.geojson")

    # bridges (one per OSM way) inside the area
    bridges = []
    if len(g.u):
        for w in np.unique(g.way[g.bridge]):
            es = np.flatnonzero(g.way == w)
            if not inside[es].any():
                continue
            row = roads.iloc[int(w)]
            mid = g.xy[g.u[es[len(es) // 2]]]
            lon, lat = to_lonlat(grid.crs, mid[0], mid[1])
            bridges.append({"name": english_name(row) or "", "name_ne": nepali_name(row) or "",
                            "road_type": str(row.get("highway") or ""), "hit": bool(cut[es].any()),
                            "lon": round(lon, 5), "lat": round(lat, 5)})
    br_gdf = gpd.GeoDataFrame(bridges, geometry=gpd.points_from_xy([b["lon"] for b in bridges], [b["lat"] for b in bridges]),
                              crs="EPSG:4326") if bridges else gpd.GeoDataFrame({"hit": []}, geometry=[], crs="EPSG:4326")
    to_geojson(br_gdf, out / "layers" / "bridges.geojson")

    # hospitals
    hosp_list = []
    for _, row in hosp.iterrows():
        x, y = row.geometry.x, row.geometry.y
        d, _r, _c, ok = damage.sample(dist, grid, np.array([x]), np.array([y]))
        lon, lat = to_lonlat(grid.crs, x, y)
        hosp_list.append({"name": english_name(row) or "Hospital / health post", "name_ne": nepali_name(row),
                          "x": x, "y": y, "lon": round(lon, 5), "lat": round(lat, 5),
                          "in_area": bool(ok[0]), "hit": bool(ok[0] and d[0] <= 50.0)})

    # ---------------- 7. Who is cut off ----------------
    log.step(80, "Finding villages that lost their road connection")
    in_area = places.geometry.within(aoi_utm)
    targets = []
    for _, row in places[places["place"].isin(["city", "town"])].iterrows():
        targets.append({"x": row.geometry.x, "y": row.geometry.y, "label": f"town: {english_name(row) or 'unnamed'}"})
    for h in hosp_list:
        targets.append({"x": h["x"], "y": h["y"], "label": f"hospital: {h['name']}", "usable_after": not h["hit"]})
    bc = bh.geometry.representative_point() if len(bh) else None
    btree = cKDTree(np.c_[bc.x, bc.y]) if bc is not None and len(bc) else None
    hit_arr = bh["hit"].to_numpy() if len(bh) else np.zeros(0, bool)
    settlements = []
    for _, row in places[in_area].iterrows():
        x, y = row.geometry.x, row.geometry.y
        lon, lat = to_lonlat(grid.crs, x, y)
        near = btree.query_ball_point([x, y], params.nearby_buildings_m) if btree is not None else []
        d, *_ = damage.sample(dist, grid, np.array([x]), np.array([y]))
        pop = row.get("population")
        settlements.append({"name": english_name(row) or "(unnamed)", "name_ne": nepali_name(row),
                            "place": row.get("place"), "x": x, "y": y, "lon": round(lon, 5), "lat": round(lat, 5),
                            "population_osm": int(pop) if isinstance(pop, str) and pop.isdigit() else None,
                            "buildings_nearby": len(near), "buildings_hit_nearby": int(hit_arr[near].sum()) if near else 0,
                            "center_flooded": bool(d[0] <= 50.0)})
    ext_utm = gpd.GeoSeries([box(*ext)], crs="EPSG:4326").to_crs(grid.crs).total_bounds
    cx, cy = (grid.bounds[0] + grid.bounds[2]) / 2, (grid.bounds[1] + grid.bounds[3]) / 2
    access = network.analyse(g, roads, cut, settlements, targets, ext_utm, (cx, cy), params) if settlements else []
    for a in access:
        for b in a["breaks"]:
            b["lon"], b["lat"] = (round(v, 5) for v in to_lonlat(grid.crs, b.pop("x"), b.pop("y")))
        a.pop("x"), a.pop("y")
        if a["status"] in ("cut_off", "detour"):
            a["priority"] = priority.score(a)  # rescue priority 0-100 (also shown on the map)
    st = gpd.GeoDataFrame([{k: v for k, v in a.items() if k != "breaks"} | {"breaks": json.dumps(a["breaks"])} for a in access],
                          geometry=gpd.points_from_xy([a["lon"] for a in access], [a["lat"] for a in access]), crs="EPSG:4326") \
        if access else gpd.GeoDataFrame({"status": []}, geometry=[], crs="EPSG:4326")
    to_geojson(st, out / "layers" / "settlements.geojson")
    hs_gdf = gpd.GeoDataFrame([{k: v for k, v in h.items() if k not in ("x", "y")} for h in hosp_list],
                              geometry=gpd.points_from_xy([h["lon"] for h in hosp_list], [h["lat"] for h in hosp_list]),
                              crs="EPSG:4326") if hosp_list else gpd.GeoDataFrame({"hit": []}, geometry=[], crs="EPSG:4326")
    to_geojson(hs_gdf, out / "layers" / "hospitals.geojson")
    to_geojson(gpd.GeoDataFrame({"name": ["study area"]}, geometry=[box(*bbox)], crs="EPSG:4326"), out / "layers" / "aoi.geojson")

    # ---------------- 8. Facts ----------------
    log.step(92, "Writing facts.json")
    px_km2 = grid.res ** 2 / 1e6
    road_in = inside
    years = sorted({s.time.year for pre, post in stacks for s in pre + post} |
                   ({int(opt_info["before"][:4]), int(opt_info["after"][:4])} if opt_info else set()))
    by = lambda status: [a for a in access if a["status"] == status]  # noqa: E731
    facts = {
        "meta": {"run_id": out.name, "area_name": log.info.get("name") or "", "bbox": list(bbox),
                 "flood_date": flood_date.isoformat(), "flood_start_utc": start.isoformat(timespec="minutes"),
                 "resolution_m": grid.res, "area_km2": round(grid.width * grid.height * px_km2, 1),
                 "data_source": src.label, "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                 "runtime_s": round(time.time() - t_start, 1), "version": VERSION},
        "images": {"radar": [t["info"] for t in tracks], "optical": opt_info},
        "flood": {k: v for k, v in fr.stats.items() if k not in ("radar_tracks",)},
        "buildings": {"total": int(len(bh)), "hit": int(bh["hit"].sum()) if len(bh) else 0,
                      "hit_high_confidence": int((bh["hit"] & (bh["conf"] == F.HIGH)).sum()) if len(bh) else 0},
        "roads": {"total_km": round(float(g.length[road_in].sum()) / 1000.0, 1),
                  "flooded_km": round(float(wet_m[road_in].sum()) / 1000.0, 1),
                  "cut_pieces": int((cut & road_in).sum())},
        "bridges": {"total": len(bridges), "hit": sum(b["hit"] for b in bridges),
                    "hit_list": [b for b in bridges if b["hit"]]},
        "hospitals": {"in_area": sum(h["in_area"] for h in hosp_list), "hit": sum(h["hit"] for h in hosp_list),
                      "list": [{k: v for k, v in h.items() if k not in ("x", "y")} for h in hosp_list if h["in_area"]]},
        "settlements": {"total": len(access), "cut_off": len(by("cut_off")), "detour": len(by("detour")),
                        "ok": len(by("ok")), "no_road": len(by("no_road")),
                        "center_flooded": sum(a["center_flooded"] for a in access),
                        "cut_off_list": by("cut_off"), "detour_list": by("detour"),
                        "other_list": [a for a in access if a["status"] in ("ok", "no_road")]},
        "osm": {"snapshot_date": snap_date.isoformat(), "source": osm_source,
                "buildings": int(len(bld)), "roads": int(len(roads)), "places": int(len(places))},
        "warnings": log.info["warnings"],
        "attribution": [a.format(years=", ".join(map(str, years)) or str(flood_date.year)) for a in ATTRIBUTION],
    }
    priority.add_priority(facts)  # most urgent cut-off / detour settlements first
    (out / "facts.json").write_text(json.dumps(facts, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    (out / "overlays" / "overlays.json").write_text(json.dumps(overlays, indent=1))
