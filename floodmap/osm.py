"""OpenStreetMap AS IT WAS BEFORE THE FLOOD.

Organisers' rule: only OSM from before the event may be used as input, so
every query asks for a snapshot date BEFORE the flood date.
(Example: around Syabrubesi OSM had 392 buildings on 27 Jul 2026 but only 225
in October, because mappers deleted the destroyed ones after the flood.)

Main source:   ohsome API v2 (needs a free key, OHSOME_API_KEY in .env).
Backup source: Overpass API with a [date:...] "as it was then" query.
We never use current-map downloads (osmnx, Overpass without a date, Geofabrik).
"""
from __future__ import annotations

import hashlib
import io
import json
import math
import os
import time
from datetime import date, datetime, timedelta

import geopandas as gpd
import pandas as pd
import requests
from shapely.geometry import LineString, Point, Polygon

from .config import CACHE_DIR

OHSOME_V1 = "https://api.ohsome.org/v1"
OHSOME_V2 = "https://api.heigit.org/ohsome-api/v2-rc"
OVERPASS = ("https://overpass-api.de/api/interpreter",)
USER_AGENT = "flood-from-space/0.1 (Multimodal AI Hackathon 2026 prototype)"

ROAD_TYPES = ("motorway", "motorway_link", "trunk", "trunk_link", "primary", "primary_link",
              "secondary", "secondary_link", "tertiary", "tertiary_link", "unclassified",
              "residential", "living_street", "service", "track", "road")
PLACE_TYPES = ("city", "town", "village", "hamlet")

OHSOME_FILTERS = {
    "buildings": "building=* and building!=no and geometry:polygon",
    "roads": f"type:way and highway in ({', '.join(ROAD_TYPES)})",
    "places": f"place in ({', '.join(PLACE_TYPES)})",
    "hospitals": "amenity=hospital or healthcare=hospital",
}
KEEP = ("osm_id", "name", "name:en", "name:ne", "place", "population", "highway", "bridge",
        "building", "amenity", "healthcare", "geometry")
POINT_KINDS = {"places", "hospitals"}  # we only need one point each


def ohsome_key() -> str:
    from dotenv import load_dotenv

    from .config import ROOT
    load_dotenv(ROOT / ".env", override=True)
    return os.getenv("OHSOME_API_KEY", "").strip()


def latest_snapshot() -> date:
    """Newest date the ohsome database knows (2026-07-27 when checked on 3 Oct 2026)."""
    path = CACHE_DIR / "osm" / "metadata.json"
    try:
        if path.exists() and time.time() - path.stat().st_mtime < 86400:
            meta = json.loads(path.read_text())
        else:
            meta = requests.get(f"{OHSOME_V1}/metadata", timeout=60).json()
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(meta))
        ts = meta["extractRegion"]["temporalExtent"]["toTimestamp"]
        return datetime.fromisoformat(ts.replace("Z", "+00:00")).date()
    except Exception:  # noqa: BLE001 - metadata is only a convenience
        return date(2026, 7, 27)


def snapshot_date(flood_date: date) -> date:
    """OSM snapshot = the day before the flood, or the newest data if that is older."""
    return min(flood_date - timedelta(days=1), latest_snapshot())


def _covering_cache(kind: str, bbox, when: date):
    """A cached download of the same layer and date whose box contains `bbox`."""
    w, s, e, n = bbox
    for meta in (CACHE_DIR / "osm").glob(f"{kind}_*.json"):
        try:
            m = json.loads(meta.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        bw, bs, be, bn = m["bbox"]
        if m["date"] == when.isoformat() and bw <= w and bs <= s and be >= e and bn >= n:
            gdf = gpd.read_file(meta.with_suffix(".geojson"))
            from shapely.geometry import box as _box
            gdf = gdf[gdf.geometry.intersects(_box(w, s, e, n))].reset_index(drop=True)
            return gdf, m.get("source", "unknown")
    return None, None


def fetch(kind: str, bbox, when: date) -> gpd.GeoDataFrame:
    """One OSM layer for a box, as it was on `when` (cached on disk)."""
    w, s, e, n = (round(v, 5) for v in bbox)
    key = hashlib.sha1(f"{kind}|{w},{s},{e},{n}|{when}".encode()).hexdigest()[:16]
    path = CACHE_DIR / "osm" / f"{kind}_{key}.geojson"
    if path.exists():
        return gpd.read_file(path)
    cached, source = _covering_cache(kind, (w, s, e, n), when)
    if cached is not None:
        _SOURCES[(kind, (w, s, e, n), when)] = source
        return cached
    api_key = ohsome_key()
    if api_key:
        gdf = _ohsome_v2(kind, (w, s, e, n), when, api_key)
        source = "ohsome API v2"
    else:
        gdf = _overpass_tiled(kind, (w, s, e, n), when)
        source = "Overpass API (dated query)"
    if kind in POINT_KINDS and len(gdf):
        gdf["geometry"] = gdf.geometry.representative_point()
    gdf = gdf[[c for c in KEEP if c in gdf.columns]].copy()
    for c in gdf.columns:
        if c != "geometry":
            gdf[c] = gdf[c].astype("object").where(gdf[c].notna(), None)
    gdf.attrs["source"] = source
    path.parent.mkdir(parents=True, exist_ok=True)
    gdf.to_file(path, driver="GeoJSON")
    path.with_suffix(".json").write_text(json.dumps({"bbox": [w, s, e, n], "date": when.isoformat(), "source": source}))
    _SOURCES[(kind, (w, s, e, n), when)] = source
    return gdf


_SOURCES: dict = {}


def source_used(kind: str, bbox, when: date) -> str:
    w, s, e, n = (round(v, 5) for v in bbox)
    if (kind, (w, s, e, n), when) in _SOURCES:
        return _SOURCES[(kind, (w, s, e, n), when)]
    key = hashlib.sha1(f"{kind}|{w},{s},{e},{n}|{when}".encode()).hexdigest()[:16]
    p = CACHE_DIR / "osm" / f"{kind}_{key}.json"
    return json.loads(p.read_text()).get("source", "unknown") if p.exists() else "unknown"


# ---------------- ohsome API v2 ----------------
def _ohsome_v2(kind, bbox, when, api_key) -> gpd.GeoDataFrame:
    w, s, e, n = bbox
    body = {"aoi": {"type": "Polygon", "coordinates": [[[w, s], [e, s], [e, n], [w, n], [w, s]]]},
            "filter": OHSOME_FILTERS[kind], "time": f"{when.isoformat()}T00:00:00Z", "clip": True}
    last = None
    for attempt in range(3):
        try:
            r = requests.post(f"{OHSOME_V2}/extraction/features.parquet", json=body,
                              headers={"authorization": api_key, "User-Agent": USER_AGENT}, timeout=600)
            if r.status_code == 200:
                break
            last = RuntimeError(f"ohsome v2 {r.status_code}: {r.text[:300]}")
            if r.status_code in (400, 401, 403):
                raise last
        except requests.RequestException as err:
            last = err
        time.sleep(5 * (attempt + 1))
    else:
        raise RuntimeError(f"Could not download OSM {kind} from ohsome: {last}")
    feats = gpd.read_parquet(io.BytesIO(r.content), to_pandas_kwargs={"maps_as_pydicts": "strict"})
    if feats.crs is None:
        feats = feats.set_crs("EPSG:4326")
    tags = pd.json_normalize(feats["tags"].tolist()) if len(feats) else pd.DataFrame()
    out = gpd.GeoDataFrame(pd.concat([feats[["osm_id"]].reset_index(drop=True), tags.reset_index(drop=True)], axis=1),
                           geometry=feats.geometry.reset_index(drop=True), crs="EPSG:4326")
    return out[~out.geometry.is_empty & out.geometry.notna()].reset_index(drop=True)


# ---------------- Overpass API, dated query (backup) ----------------
def _overpass_query(kind, bbox, when) -> str:
    w, s, e, n = bbox
    b = f"({s},{w},{n},{e})"
    head = f'[out:json][timeout:300][maxsize:1073741824][date:"{when.isoformat()}T00:00:00Z"];'
    if kind == "buildings":
        return head + f'way["building"]["building"!="no"]{b};out tags geom;'
    if kind == "roads":
        return head + f'way["highway"~"^({"|".join(ROAD_TYPES)})$"]{b};out tags geom;'
    if kind == "places":
        return head + f'nwr["place"~"^({"|".join(PLACE_TYPES)})$"]{b};out tags center;'
    if kind == "hospitals":
        return head + f'(nwr["amenity"="hospital"]{b};nwr["healthcare"="hospital"]{b};);out tags center;'
    raise ValueError(kind)


def _overpass_tiled(kind, bbox, when, tile_deg: float = 0.2) -> gpd.GeoDataFrame:
    """Big boxes are split into tiles: small dated queries succeed far more often."""
    w, s, e, n = bbox
    nx, ny = max(1, math.ceil((e - w) / tile_deg)), max(1, math.ceil((n - s) / tile_deg))
    if nx * ny == 1:
        return _overpass(kind, bbox, when)
    parts = []
    for i in range(nx):
        for j in range(ny):
            tb = (w + (e - w) * i / nx, s + (n - s) * j / ny, w + (e - w) * (i + 1) / nx, s + (n - s) * (j + 1) / ny)
            parts.append(_overpass(kind, tb, when))
    parts = [p for p in parts if len(p)]
    if not parts:
        return gpd.GeoDataFrame({"geometry": []}, geometry="geometry", crs="EPSG:4326")
    gdf = gpd.GeoDataFrame(pd.concat(parts, ignore_index=True), geometry="geometry", crs="EPSG:4326")
    return gdf.drop_duplicates(subset="osm_id").reset_index(drop=True)


def _overpass(kind, bbox, when) -> gpd.GeoDataFrame:
    q = _overpass_query(kind, bbox, when)
    last = None
    for attempt in range(6):
        url = OVERPASS[attempt % len(OVERPASS)]
        try:
            r = requests.post(url, data={"data": q}, headers={"User-Agent": USER_AGENT}, timeout=400)
            if r.status_code == 200 and r.headers.get("content-type", "").startswith("application/json"):
                break
            last = RuntimeError(f"Overpass {r.status_code}: {r.text[:200]}")
        except requests.RequestException as err:
            last = err
        time.sleep(10 * (attempt + 1))
    else:
        raise RuntimeError(f"Could not download OSM {kind} (Overpass busy). Add OHSOME_API_KEY to .env. {last}")
    rows = []
    for el in r.json().get("elements", []):
        tags = el.get("tags", {})
        geom = None
        if "geometry" in el:
            pts = [(p["lon"], p["lat"]) for p in el["geometry"] if p]
            if kind == "buildings":
                if len(pts) >= 4 and pts[0] == pts[-1]:
                    geom = Polygon(pts)
            elif len(pts) >= 2:
                geom = LineString(pts)
        elif "center" in el:
            geom = Point(el["center"]["lon"], el["center"]["lat"])
        elif "lat" in el:
            geom = Point(el["lon"], el["lat"])
        if geom is not None and not geom.is_empty:
            rows.append({"osm_id": f'{el["type"]}/{el["id"]}', **tags, "geometry": geom})
    if not rows:
        return gpd.GeoDataFrame({"geometry": []}, geometry="geometry", crs="EPSG:4326")
    gdf = gpd.GeoDataFrame(rows, geometry="geometry", crs="EPSG:4326")
    if kind == "buildings":
        gdf = gdf[gdf.geometry.is_valid | gdf.geometry.buffer(0).is_valid]
    return gdf.reset_index(drop=True)


def display_name(row) -> str:
    for k in ("name:en", "name"):
        v = row.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return ""


def nepali_name(row) -> str:
    v = row.get("name:ne")
    return v.strip() if isinstance(v, str) and v.strip() else ""
