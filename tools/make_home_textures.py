"""Build the Earth pictures for the home page intro (run once; the results are in web/static/home/).

Sources (free to use with credit, see web/static/home/CREDITS.md):
  - Sentinel-2 cloudless 2020 by EOX IT Services GmbH (contains modified Copernicus Sentinel data 2020), CC BY-NC-SA 4.0
  - Terrain Tiles (Mapzen, AWS Open Data): relief shading and the sea mask
  - NASA Earth Observatory: Blue Marble clouds and Black Marble 2016 night lights (public domain)

The home page only shows these pictures; the flood analysis never reads them.

Usage:  .venv\\Scripts\\python tools\\make_home_textures.py
"""
from __future__ import annotations

import io
import json
import math
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
from PIL import Image, ImageFilter

Image.MAX_IMAGE_PIXELS = None

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "web" / "static" / "home"
CACHE = ROOT / "cache" / "home_tiles"
UA = {"User-Agent": "flood-from-space home page texture builder (Multimodal AI Hackathon 2026)"}

EOX = "https://tiles.maps.eox.at/wmts/1.0.0/s2cloudless-2020_3857/default/g/{z}/{y}/{x}.jpg"
TERRARIUM = "https://s3.amazonaws.com/elevation-tiles-prod/terrarium/{z}/{x}/{y}.png"
CLOUDS = "https://eoimages.gsfc.nasa.gov/images/imagerecords/57000/57747/cloud_combined_8192.tif"
NIGHT = "https://eoimages.gsfc.nasa.gov/images/imagerecords/144000/144898/BlackMarble_2016_01deg.jpg"

GLOBAL_Z = 5          # 32 x 32 tiles -> 8192 px around the equator (saved at 4096, sharper than z=4)
RELIEF_Z = 4          # 4096 px is enough for shading the whole globe
PATCH = {"lon0": 79.0, "lon1": 92.0, "lat0": 22.0, "lat1": 33.0, "z": 8}  # sharp close-up: Nepal, Sikkim, north India
MAX_LAT = 85.0511287798


def fetch(url: str, path: Path) -> bytes:
    if path.exists() and path.stat().st_size > 0:
        return path.read_bytes()
    path.parent.mkdir(parents=True, exist_ok=True)
    for attempt in range(5):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=120) as r:
                data = r.read()
            path.write_bytes(data)
            return data
        except Exception as e:  # noqa: BLE001 - retry any network error
            if attempt == 4:
                raise RuntimeError(f"download failed: {url}: {e}") from e
            time.sleep(2 + 3 * attempt)
    raise AssertionError


def mosaic(tpl: str, name: str, z: int, xs: range, ys: range, decode) -> np.ndarray:
    jobs = [(x, y) for y in ys for x in xs]

    def one(xy):
        x, y = xy
        ext = tpl.rsplit(".", 1)[1]
        data = fetch(tpl.format(z=z, x=x, y=y), CACHE / name / str(z) / f"{x}_{y}.{ext}")
        return xy, decode(Image.open(io.BytesIO(data)))

    first = None
    with ThreadPoolExecutor(8) as pool:
        for n, ((x, y), tile) in enumerate(pool.map(one, jobs), 1):
            if first is None:
                first = np.zeros((len(ys) * 256, len(xs) * 256) + tile.shape[2:], tile.dtype)
            i, j = (y - ys.start) * 256, (x - xs.start) * 256
            first[i:i + 256, j:j + 256] = tile
            if n % 100 == 0 or n == len(jobs):
                print(f"  {name} z{z}: {n}/{len(jobs)} tiles", flush=True)
    return first


def rgb(im: Image.Image) -> np.ndarray:
    return np.asarray(im.convert("RGB"))


def terrarium(im: Image.Image) -> np.ndarray:
    a = np.asarray(im.convert("RGB")).astype(np.float32)
    return (a[..., 0] * 256 + a[..., 1] + a[..., 2] / 256 - 32768)[..., None]


def merc_y(lat, z):
    phi = np.radians(np.clip(lat, -MAX_LAT, MAX_LAT))
    return (1 - np.log(np.tan(phi) + 1 / np.cos(phi)) / math.pi) / 2 * 256 * 2 ** z


def tile_x(lon, z):
    return (lon + 180) / 360 * 2 ** z


def to_equirect(img, z, x_off, y_off, w, h, lon0=-180.0, lon1=180.0, lat1=90.0, lat0=-90.0):
    """Re-project a Web Mercator mosaic (whose top-left is global pixel x_off, y_off) to a lon/lat grid."""
    lats = lat1 - (np.arange(h) + 0.5) * (lat1 - lat0) / h
    lons = lon0 + (np.arange(w) + 0.5) * (lon1 - lon0) / w
    ys = merc_y(lats, z) - y_off - 0.5
    xs = (lons + 180) / 360 * 256 * 2 ** z - x_off - 0.5
    y0 = np.clip(np.floor(ys).astype(int), 0, img.shape[0] - 2)
    fy = np.clip(ys - y0, 0, 1).astype(np.float32)
    x0 = np.clip(np.floor(xs).astype(int), 0, img.shape[1] - 2)
    fx = np.clip(xs - x0, 0, 1).astype(np.float32)
    is8 = img.dtype == np.uint8  # photos stay 8-bit to save memory; heights stay float
    out = np.empty((h, w, img.shape[2]), np.uint8 if is8 else np.float32)
    for j in range(0, h, 128):
        s = slice(j, min(h, j + 128))
        a = img[y0[s]].astype(np.float32)
        b = img[y0[s] + 1].astype(np.float32)
        rows = a + (b - a) * fy[s, None, None]
        c0, c1 = rows[:, x0], rows[:, x0 + 1]
        v = c0 + (c1 - c0) * fx[None, :, None]
        out[s] = np.clip(v + 0.5, 0, 255) if is8 else v
    return out


def slopes(hgt: np.ndarray, lat1: float, lat0: float, lon0: float, lon1: float):
    """East and north slope (rise / run) of a lon/lat height grid in metres."""
    h, w = hgt.shape
    r = 6371000.0
    lats = np.radians(lat1 - (np.arange(h) + 0.5) * (lat1 - lat0) / h)
    dlon = math.radians((lon1 - lon0) / w)
    dlat = math.radians((lat1 - lat0) / h)
    gy, gx = np.gradient(hgt)
    dx = np.maximum(r * np.cos(lats)[:, None] * dlon, 50.0)
    return gx / dx, -gy / (r * dlat)


def enc_slope(s: np.ndarray) -> np.ndarray:
    """Square-root encoding: fine steps for gentle slopes, still room for cliffs (decoded in home.js)."""
    return np.clip(0.5 + 0.5 * np.sign(s) * np.sqrt(np.minimum(np.abs(s) / 2.0, 1.0)), 0, 1)


def save(arr, name: str, quality: int = 90, size: tuple[int, int] | None = None):
    im = Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8))
    if size:
        im = im.resize(size, Image.LANCZOS)
    path = OUT / name
    im.save(path, quality=quality, subsampling=0, optimize=True, progressive=True)
    print(f"  wrote {path.relative_to(ROOT)}  {im.size[0]}x{im.size[1]}  {path.stat().st_size / 1e6:.1f} MB", flush=True)


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    n = 2 ** GLOBAL_Z

    print("1/5 Sentinel-2 cloudless Earth", flush=True)
    merc = mosaic(EOX, "eox", GLOBAL_Z, range(n), range(n), rgb)
    day = to_equirect(merc, GLOBAL_Z, 0, 0, 256 * n, 128 * n)
    del merc
    save(day, "earth_day_4k.jpg", 90, (4096, 2048))
    save(day, "earth_day_2k.jpg", 90, (2048, 1024))
    day4 = np.asarray(Image.fromarray(day).resize((4096, 2048), Image.LANCZOS)).astype(np.float32)
    del day

    print("2/5 relief and sea mask", flush=True)
    m = 2 ** RELIEF_Z
    hm = mosaic(TERRARIUM, "terrarium", RELIEF_Z, range(m), range(m), terrarium)
    hgt = to_equirect(hm, RELIEF_Z, 0, 0, 4096, 2048)[..., 0]
    del hm
    sx, sn = slopes(np.maximum(hgt, 0), 90, -90, -180, 180)
    water = (hgt <= 0.5) & (day4[..., 2] > day4[..., 0] * 1.05)
    water = np.asarray(Image.fromarray((water * 255).astype(np.uint8)).filter(ImageFilter.GaussianBlur(1.2)))
    relief = np.dstack([enc_slope(sx) * 255, enc_slope(sn) * 255, water])
    save(relief, "earth_relief_2k.jpg", 95, (2048, 1024))

    print("3/5 clouds and night lights (NASA)", flush=True)
    clouds = Image.open(io.BytesIO(fetch(CLOUDS, CACHE / "nasa" / "cloud_combined_8192.tif"))).convert("L")
    clouds = np.asarray(clouds.resize((4096, 2048), Image.LANCZOS)).astype(np.float32)
    night = Image.open(io.BytesIO(fetch(NIGHT, CACHE / "nasa" / "BlackMarble_2016_01deg.jpg"))).convert("L")
    night = np.asarray(night.resize((4096, 2048), Image.BICUBIC)).astype(np.float32)
    aux = np.dstack([clouds, night, np.zeros_like(clouds)])
    save(aux, "earth_aux_2k.jpg", 92, (2048, 1024))

    print("4/5 sharp close-up over Nepal", flush=True)
    p, z = PATCH, PATCH["z"]
    xs = range(int(tile_x(p["lon0"], z)), int(tile_x(p["lon1"], z)) + 1)
    ys = range(int(merc_y(p["lat1"], z) // 256), int(merc_y(p["lat0"], z) // 256) + 1)
    w = round((p["lon1"] - p["lon0"]) / 360 * 256 * 2 ** z)
    h = round(w * (p["lat1"] - p["lat0"]) / (p["lon1"] - p["lon0"]))
    args = (z, xs.start * 256, ys.start * 256, w, h, p["lon0"], p["lon1"], p["lat1"], p["lat0"])
    pday = to_equirect(mosaic(EOX, "eox", z, xs, ys, rgb), *args)
    save(pday, "nepal_day.jpg", 90)
    save(pday, "nepal_day_1k.jpg", 90, (w // 2, h // 2))
    phgt = to_equirect(mosaic(TERRARIUM, "terrarium", z, xs, ys, terrarium), *args)[..., 0]
    psx, psn = slopes(np.maximum(phgt, 0), p["lat1"], p["lat0"], p["lon0"], p["lon1"])
    prel = np.dstack([enc_slope(psx) * 255, enc_slope(psn) * 255, np.zeros_like(psx)])
    save(prel, "nepal_relief_1k.jpg", 95, (w // 2, h // 2))

    print("5/5 metadata", flush=True)
    meta = {"patch": {k: p[k] for k in ("lon0", "lon1", "lat0", "lat1")}, "patch_px": [w, h]}
    (OUT / "textures.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    print("done", flush=True)


if __name__ == "__main__":
    main()
