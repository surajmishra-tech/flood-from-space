"""Main image source: Copernicus Data Space Ecosystem (dataspace.copernicus.eu).

Search: the public OData catalogue (no login).
Pixels: the Sentinel Hub Process API (needs a free OAuth client in .env).

Quota tips (free account = 10,000 processing units / month):
  * images are requested as 16-bit integers (32-bit float would cost x2)
  * speckle filtering is done by us, not by the API (that would cost x2)
"""
from __future__ import annotations

from datetime import datetime, timedelta

import numpy as np
import requests
from shapely.geometry import box, shape
from shapely.ops import unary_union

from ..config import CDSE_CLIENT_ID, CDSE_CLIENT_SECRET
from ..grid import Grid
from . import Scene, group_passes

ODATA = "https://catalogue.dataspace.copernicus.eu/odata/v1/Products"
SH_URL = "https://sh.dataspace.copernicus.eu"
TOKEN_URL = "https://identity.dataspace.copernicus.eu/auth/realms/CDSE/protocol/openid-connect/token"
MAX_PX = 2500  # Process API limit per request side

S1_EVALSCRIPT = """
//VERSION=3
function setup() {
  return {input: [{bands: ["VV", "VH", "dataMask"]}],
          output: {bands: 2, sampleType: "UINT16"}};
}
function enc(v) {
  if (!(v > 0)) return 0;
  var db = 10 * Math.log(v) / Math.LN10;
  return Math.max(1, Math.min(65535, Math.round((db + 50) * 1000)));
}
function evaluatePixel(s) {
  if (s.dataMask == 0) return [0, 0];
  return [enc(s.VV), enc(s.VH)];
}
"""

S2_EVALSCRIPT = """
//VERSION=3
function setup() {
  return {input: [{bands: ["B02", "B03", "B04", "B08", "B11", "SCL", "dataMask"]}],
          output: {bands: 6, sampleType: "UINT16"}};
}
function evaluatePixel(s) {
  if (s.dataMask == 0) return [0, 0, 0, 0, 0, 0];
  return [s.B02 * 10000, s.B03 * 10000, s.B04 * 10000, s.B08 * 10000, s.B11 * 10000, s.SCL];
}
"""


S2_SCL_EVALSCRIPT = """
//VERSION=3
function setup() {
  return {input: [{bands: ["SCL", "dataMask"]}], output: {bands: 1, sampleType: "UINT8"}};
}
function evaluatePixel(s) { return [s.dataMask == 0 ? 0 : s.SCL]; }
"""


def _odata(filter_: str, top: int = 300) -> list[dict]:
    r = requests.get(ODATA, params={"$filter": filter_, "$expand": "Attributes",
                                    "$orderby": "ContentDate/Start", "$top": str(top)}, timeout=120)
    r.raise_for_status()
    return r.json().get("value", [])


def _attrs(p: dict) -> dict:
    return {a["Name"]: a["Value"] for a in p.get("Attributes", [])}


def _wkt(bbox) -> str:
    w, s, e, n = bbox
    return f"POLYGON(({w} {s},{e} {s},{e} {n},{w} {n},{w} {s}))"


def _t(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


class CdseSource:
    name = "cdse"
    label = "Copernicus Data Space Ecosystem (Sentinel Hub API)"

    def __init__(self):
        from sentinelhub import DataCollection, SHConfig

        self.config = SHConfig()
        self.config.sh_client_id = CDSE_CLIENT_ID
        self.config.sh_client_secret = CDSE_CLIENT_SECRET
        self.config.sh_base_url = SH_URL
        self.config.sh_token_url = TOKEN_URL
        try:
            self.s1 = DataCollection.SENTINEL1_IW.define_from("cdse-s1-iw", service_url=SH_URL)
            self.s2 = DataCollection.SENTINEL2_L2A.define_from("cdse-s2-l2a", service_url=SH_URL)
        except ValueError:  # already defined in this Python process
            self.s1 = DataCollection["cdse-s1-iw"] if hasattr(DataCollection, "__getitem__") else DataCollection.SENTINEL1_IW
            self.s2 = DataCollection["cdse-s2-l2a"] if hasattr(DataCollection, "__getitem__") else DataCollection.SENTINEL2_L2A

    # ---------- search (public catalogue) ----------
    def find_s1(self, bbox, start: datetime, end: datetime) -> list[Scene]:
        f = (f"Collection/Name eq 'SENTINEL-1' and contains(Name,'IW_GRDH') "
             f"and OData.CSC.Intersects(area=geography'SRID=4326;{_wkt(bbox)}') "
             f"and ContentDate/Start gt {start:%Y-%m-%dT%H:%M:%S.000Z} and ContentDate/Start lt {end:%Y-%m-%dT%H:%M:%S.000Z}")
        seen, entries = set(), []
        for p in _odata(f):
            base = p["Name"].replace("_COG", "")
            if base in seen:
                continue
            seen.add(base)
            a = _attrs(p)
            key = (int(a.get("relativeOrbitNumber", -1)), str(a.get("orbitDirection", "")).lower())
            entries.append((_t(p["ContentDate"]["Start"]), key, p))
        scenes = []
        for g in group_passes(entries):
            items = g["items"]
            cov = unary_union([shape(it["GeoFootprint"]) for it in items]).intersection(box(*bbox)).area / box(*bbox).area
            t0 = min(_t(it["ContentDate"]["Start"]) for it in items)
            t1 = max(_t(it["ContentDate"]["End"]) for it in items)
            scenes.append(Scene("S1", g["first"], items[0]["Name"][:3].replace("S1", "sentinel-1").lower(),
                                track=g["key"][0], direction=g["key"][1], coverage=float(cov),
                                refs=[(t0, t1)]))
        return sorted(scenes, key=lambda s: s.time)

    def find_s2(self, bbox, start: datetime, end: datetime, max_cloud: float = 95) -> list[Scene]:
        f = (f"Collection/Name eq 'SENTINEL-2' and contains(Name,'MSIL2A') "
             f"and OData.CSC.Intersects(area=geography'SRID=4326;{_wkt(bbox)}') "
             f"and ContentDate/Start gt {start:%Y-%m-%dT%H:%M:%S.000Z} and ContentDate/Start lt {end:%Y-%m-%dT%H:%M:%S.000Z} "
             f"and Attributes/OData.CSC.DoubleAttribute/any(att:att/Name eq 'cloudCover' and att/OData.CSC.DoubleAttribute/Value lt {max_cloud})")
        entries = []
        for p in _odata(f):
            a = _attrs(p)
            entries.append((_t(p["ContentDate"]["Start"]), int(a.get("relativeOrbitNumber", -1)), (p, a)))
        scenes = []
        for g in group_passes(entries, minutes=30):
            items = [p for p, _ in g["items"]]
            cov = unary_union([shape(it["GeoFootprint"]) for it in items]).intersection(box(*bbox)).area / box(*bbox).area
            cloud = float(np.mean([a.get("cloudCover", 100) for _, a in g["items"]]))
            day = g["first"].replace(hour=0, minute=0, second=0, microsecond=0)
            scenes.append(Scene("S2", g["first"], items[0]["Name"][:3].lower(), track=g["key"],
                                coverage=float(cov), cloud=cloud, refs=[(day, day + timedelta(days=1))]))
        return sorted(scenes, key=lambda s: s.time)

    # ---------- pixels (Sentinel Hub Process API) ----------
    def _request(self, evalscript, collection, time_range, grid: Grid, bands: int, other_args=None):
        from sentinelhub import CRS, BBox, MimeType, MosaickingOrder, SentinelHubRequest

        epsg = int(grid.crs.split(":")[1])
        out = np.zeros((bands, grid.height, grid.width), dtype="uint16")  # (also fine for UINT8 output)
        left, bottom, right, top = grid.bounds
        res = grid.res
        for r0 in range(0, grid.height, MAX_PX):
            for c0 in range(0, grid.width, MAX_PX):
                h = min(MAX_PX, grid.height - r0)
                w = min(MAX_PX, grid.width - c0)
                tb = (left + c0 * res, top - (r0 + h) * res, left + (c0 + w) * res, top - r0 * res)
                req = SentinelHubRequest(
                    evalscript=evalscript,
                    input_data=[SentinelHubRequest.input_data(
                        data_collection=collection, time_interval=time_range,
                        mosaicking_order=MosaickingOrder.MOST_RECENT, other_args=other_args)],
                    responses=[SentinelHubRequest.output_response("default", MimeType.TIFF)],
                    bbox=BBox(tb, crs=CRS(epsg)), size=(w, h), config=self.config)
                data = req.get_data()[0]
                if data.ndim == 2:
                    data = data[None]
                else:
                    data = np.moveaxis(data, -1, 0)
                out[:, r0:r0 + h, c0:c0 + w] = data
        return out

    def read_s1(self, scene: Scene, grid: Grid) -> dict[str, np.ndarray]:
        t0, t1 = scene.refs[0]
        rng = (t0 - timedelta(minutes=1), t1 + timedelta(minutes=1))
        args = {"dataFilter": {"orbitDirection": scene.direction.upper()},
                "processing": {"backCoeff": "GAMMA0_TERRAIN", "orthorectify": True,
                               "demInstance": "COPERNICUS_30"}}
        raw = self._request(S1_EVALSCRIPT, self.s1, rng, grid, 2, args)
        out = {}
        for i, pol in enumerate(("vv", "vh")):
            db = raw[i].astype("float32") / 1000.0 - 50.0
            lin = np.power(10.0, db / 10.0, dtype="float32")
            lin[raw[i] == 0] = np.nan
            out[pol] = lin
        return out

    def read_s2(self, scene: Scene, grid: Grid, bands=("B02", "B03", "B04", "B08", "B11", "SCL")) -> dict[str, np.ndarray]:
        if tuple(bands) == ("SCL",):  # cloud check only: ask for the one band (cheaper)
            raw = self._request(S2_SCL_EVALSCRIPT, self.s2, scene.refs[0], grid, 1)
            scl = raw[0].astype("float32")
            scl[raw[0] == 0] = np.nan
            return {"SCL": scl}
        raw = self._request(S2_EVALSCRIPT, self.s2, scene.refs[0], grid, 6)
        names = ("B02", "B03", "B04", "B08", "B11", "SCL")
        out = {}
        for i, b in enumerate(names):
            a = raw[i].astype("float32")
            nodata = raw[5] == 0
            a = a if b == "SCL" else a / 10000.0
            a[nodata] = np.nan
            out[b] = a
        return {b: out[b] for b in bands}
