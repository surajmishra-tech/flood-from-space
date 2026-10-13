"""Make the pictures and numbers for the evidence section under the dashboard (web/static/evidence/).

Everything comes from saved results and the EMSR927 check (report/case_study_emsr927.json), and is
saved into the repository, so judges see the evidence even before they run an analysis.
If the dashboard server is running, one real copilot answer is saved too.

Usage:  .venv\\Scripts\\python tools\\make_evidence.py
"""
from __future__ import annotations

import json
import math
import urllib.request
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageEnhance, ImageFont

ROOT = Path(__file__).resolve().parents[1]
RUNS = ROOT / "runs"
OUT = ROOT / "web" / "static" / "evidence"
MAIN = "fb7507902568"       # Upper Trishuli, 26 Aug 2026
CONTROL = "fbdf29eca776"    # same box, dry season (15 Apr 2026)
OTHERS = [                  # the same system, unchanged, on other floods
    ("70e0dab8bf4b", "Melamchi, Nepal", "flash flood and debris flow"),
    ("cd38db83e51a", "Teesta, Sikkim (India)", "glacial lake outburst"),
    ("8a4d3f927a62", "Rishiganga, Chamoli (India)", "rock-ice avalanche flood"),
    ("11da0dff186b", "Rasuwa, Nepal", "glacial lake outburst, same valley"),
    ("e600e350435e", "Bidur, lower Trishuli", "same flood, 60 km downstream"),
    ("452a6cfeaf43", "Silchar, Assam (India)", "monsoon river flood on flat land"),
]
BG = (5, 11, 20)


def facts(rid):
    return json.loads((RUNS / rid / "facts.json").read_text(encoding="utf-8"))


def run_info(rid):
    return json.loads((RUNS / rid / "run.json").read_text(encoding="utf-8"))


def layer(rid, name):
    return json.loads((RUNS / rid / "layers" / f"{name}.geojson").read_text(encoding="utf-8"))


def overlay(rid, name):
    return Image.open(RUNS / rid / "overlays" / f"{name}.png").convert("RGBA")


def bounds(rid):
    ov = json.loads((RUNS / rid / "overlays" / "overlays.json").read_text(encoding="utf-8"))
    (s, w), (n, e) = ov.get("photo_before") or ov["hillshade"]   # every picture of a run shares one box
    return s, w, n, e


def has(rid, name):
    return (RUNS / rid / "overlays" / f"{name}.png").exists()


def flat(im, bg=BG):
    out = Image.new("RGBA", im.size, bg + (255,))
    out.alpha_composite(im)
    return out.convert("RGB")


def save(im, name, quality=86):
    path = OUT / name
    im.save(path, quality=quality, optimize=True, progressive=True)
    print(f"  {name}  {im.size[0]}x{im.size[1]}  {path.stat().st_size // 1024} KB")


def font(size):
    for f in ("C:/Windows/Fonts/segoeuib.ttf", "C:/Windows/Fonts/arialbd.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"):
        if Path(f).exists():
            return ImageFont.truetype(f, size)
    return ImageFont.load_default(size)


def to_px(lat, lon, b, size):
    s, w, n, e = b
    return (lon - w) / (e - w) * size[0], (n - lat) / (n - s) * size[1]


def result_map(rid, width, labels=0, roads=True, back_rid=None):
    """The flood map over the before-photo, with cut roads and cut-off villages."""
    b = bounds(rid)
    br = back_rid or rid   # the no-flood test is drawn on the main run's clear photo (same box)
    back = "photo_before" if has(br, "photo_before") else "hillshade"   # no cloud-free photo: terrain shading
    photo = flat(overlay(br, back))
    photo = ImageEnhance.Brightness(photo).enhance(0.62 if back == "photo_before" else 0.45).convert("RGBA")
    photo.alpha_composite(overlay(rid, "flood"))
    img = photo.convert("RGB")
    k = width / img.size[0]
    img = img.resize((width, round(img.size[1] * k)), Image.LANCZOS)
    d = ImageDraw.Draw(img)
    if roads:
        for f in layer(rid, "roads_cut")["features"]:
            g = f["geometry"]
            lines = g["coordinates"] if g["type"] == "MultiLineString" else [g["coordinates"]]
            for line in lines:
                pts = [to_px(lat, lon, b, img.size) for lon, lat in line]
                if len(pts) > 1:
                    d.line(pts, fill=(239, 68, 68), width=3)
    cut = [f["properties"] for f in layer(rid, "settlements")["features"] if f["properties"].get("status") == "cut_off"]
    for p in cut:
        x, y = to_px(p["lat"], p["lon"], b, img.size)
        d.ellipse([x - 6, y - 6, x + 6, y + 6], fill=(215, 25, 28), outline=(255, 255, 255), width=2)
    if labels:
        fnt = font(15)
        for p in sorted(cut, key=lambda q: -(q.get("priority") or 0))[:labels]:
            if not p.get("name"):
                continue
            x, y = to_px(p["lat"], p["lon"], b, img.size)
            text = f"{p['name']}  {p.get('priority', '')}"
            box = d.textbbox((x + 11, y - 9), text, font=fnt)
            d.rounded_rectangle([box[0] - 5, box[1] - 3, box[2] + 5, box[3] + 3], radius=4, fill=(5, 12, 22))
            d.rectangle([box[0] - 5, box[1] - 3, box[0] - 2, box[3] + 3], fill=(239, 68, 68))
            d.text((x + 11, y - 9), text, font=fnt, fill=(238, 246, 255))
    return img


def best_window(rid, win):
    """The square of the image where the radar changed most."""
    ch = np.asarray(overlay(rid, "radar_change")).astype(np.float32)
    score = ch[..., 3] / 255 * (ch[..., 0] / 255)
    ii = np.pad(score.cumsum(0).cumsum(1), ((1, 0), (1, 0)))
    h, w = score.shape
    sums = ii[win:, win:] - ii[:-win, win:] - ii[win:, :-win] + ii[:-win, :-win]
    y, x = np.unravel_index(np.argmax(sums), sums.shape)
    return int(x), int(y), win


def ems_numbers():
    rows = json.loads((ROOT / "report" / "case_study_emsr927.json").read_text(encoding="utf-8"))
    out, tp, ours, ems = [], 0.0, 0.0, 0.0
    tp_w, ours_w = 0.0, 0.0     # confirmed map + the lighter "possible damage" zone
    for r in rows:
        fe, fw = r["flood_extent"], r.get("flood_extent_with_possible") or r["flood_extent"]
        tp += fe["precision"] * fe["ours_km2"]
        ours += fe["ours_km2"]
        ems += fe["ems_km2"]
        tp_w += fw["precision"] * fw["ours_km2"]
        ours_w += fw["ours_km2"]
        out.append({
            "place": r["place"], "aoi": r["aoi"], "ems_km2": fe["ems_km2"], "ours_km2": fe["ours_km2"],
            "precision": fe["precision"], "recall": fe["recall"], "f1": fe["f1"], "f1_20m": fe["f1_20m"],
            "destroyed_found_pct": r["buildings"].get("found_destroyed_pct"), "ems_destroyed": r["buildings"].get("ems_destroyed"),
            "bridges_found": round(r["bridges"]["ems_affected"] * r["bridges"]["ems_bridges_on_our_flood_pct"] / 100),
            "bridges_ems": r["bridges"]["ems_affected"],
            "with_possible": {"ours_km2": fw["ours_km2"], "precision": fw["precision"], "recall": fw["recall"], "f1": fw["f1"],
                              "f1_20m": fw["f1_20m"], "destroyed_found_pct": r["buildings"].get("found_destroyed_pct_with_possible")},
        })
    p, rc = tp / ours, tp / ems
    pw, rw = tp_w / ours_w, tp_w / ems
    return {"rows": out, "pooled": {"precision": round(p, 2), "recall": round(rc, 2), "f1": round(2 * p * rc / (p + rc), 2),
                                     "ems_km2": round(ems, 1), "ours_km2": round(ours, 1)},
            "pooled_with_possible": {"precision": round(pw, 2), "recall": round(rw, 2), "f1": round(2 * pw * rw / (pw + rw), 2),
                                     "ours_km2": round(ours_w, 1)}}


def copilot_example(question):
    try:
        req = urllib.request.Request(f"http://127.0.0.1:8000/api/runs/{MAIN}/ask", method="POST",
                                     data=json.dumps({"question": question, "lang": "en"}).encode(),
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=90) as r:
            j = json.loads(r.read())
        pr = j.get("proof") or {}
        if not pr.get("model_text"):
            return None
        return {"question": question, "answer": j.get("text"), "model_text": pr["model_text"],
                "values": pr.get("values_inserted_by_code"), "by": j.get("by")}
    except Exception as e:  # noqa: BLE001 - the example is optional
        print("  (no copilot example:", e, ")")
        return None


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    f, info = facts(MAIN), run_info(MAIN)
    b = bounds(MAIN)
    print("radar before / after, zoomed where the radar changed most")
    rb, ra = overlay(MAIN, "radar_before"), overlay(MAIN, "radar_after")
    x, y, win = best_window(MAIN, 380)
    crop = (x, y, x + win, y + win)
    zoom = 2
    size = (win * zoom, win * zoom)
    gb = np.asarray(flat(rb.crop(crop)).convert("L")).astype(np.float32)
    ga = np.asarray(flat(ra.crop(crop)).convert("L")).astype(np.float32)
    save(Image.fromarray(gb.astype(np.uint8)).resize(size, Image.LANCZOS), "radar_before.jpg")
    save(Image.fromarray(ga.astype(np.uint8)).resize(size, Image.LANCZOS), "radar_after.jpg")
    # classic radar change colours, made stronger: red = brighter after (rough debris), cyan = darker after (smooth water, mud)
    from PIL import ImageFilter
    soft = lambda a: np.asarray(Image.fromarray(a.astype(np.uint8)).filter(ImageFilter.GaussianBlur(1.2))).astype(np.float32)
    sb, sa = soft(gb), soft(ga)
    base, diff = (sb + sa) / 2 * 0.8, (sa - sb) * 3.0
    rgb = np.clip(np.dstack([base + diff, base - diff * 0.6, base - diff * 0.6]), 0, 255)
    # colours only on the valley floor (the only place the system looks); the mountains stay grey
    valley = np.asarray(overlay(MAIN, "valley").crop(crop))[..., 3].astype(np.float32)
    valley = np.asarray(Image.fromarray((valley > 0).astype(np.uint8) * 255).filter(ImageFilter.GaussianBlur(1.5))).astype(np.float32)[..., None] / 255
    grey = np.repeat((sa * 0.62)[..., None], 3, axis=2)
    rgb = (grey * (1 - valley) + rgb * valley).astype(np.uint8)
    save(Image.fromarray(rgb).resize(size, Image.LANCZOS), "radar_change.jpg")
    pz = flat(overlay(MAIN, "photo_before").crop(crop))
    pz = ImageEnhance.Brightness(pz).enhance(0.7).convert("RGBA")
    pz.alpha_composite(overlay(MAIN, "flood").crop(crop))
    save(pz.convert("RGB").resize(size, Image.LANCZOS), "flood_zoom.jpg")
    W, H = rb.size
    s, w, n, e = b
    clat = n - (y + win / 2) / H * (n - s)
    clon = w + (x + win / 2) / W * (e - w)
    km = win * (e - w) / W * 111.32 * math.cos(math.radians(clat))
    near = min((q["properties"] for q in layer(MAIN, "settlements")["features"] if q["properties"].get("name")),
               key=lambda q: (q["lat"] - clat) ** 2 + ((q["lon"] - clon) * math.cos(math.radians(clat))) ** 2)

    print("photos before / after, and the answer map")
    save(flat(overlay(MAIN, "photo_before")).resize((520, round(H * 520 / W)), Image.LANCZOS), "photo_before.jpg")
    save(flat(overlay(MAIN, "photo_after")).resize((520, round(H * 520 / W)), Image.LANCZOS), "photo_after.jpg")
    save(result_map(MAIN, 640, labels=4), "result.jpg")
    save(result_map(CONTROL, 640, labels=0, back_rid=MAIN), "control.jpg")

    print("other floods")
    floods = []
    for rid, place, kind in OTHERS:
        if not (RUNS / rid / "facts.json").exists():
            continue
        ff, ri = facts(rid), run_info(rid)
        img = result_map(rid, 420, roads=False)
        th = 280
        if img.size[1] > th:   # keep the part with the most flood
            fl = np.asarray(overlay(rid, "flood"))[..., 3].astype(np.float32)
            rows = fl.sum(1)
            cy = int((rows * np.arange(len(rows))).sum() / max(rows.sum(), 1) * img.size[1] / len(rows))
            top = min(max(cy - th // 2, 0), img.size[1] - th)
            img = img.crop((0, top, img.size[0], top + th))
        name = f"flood_{rid}.jpg"
        save(img, name, 82)
        floods.append({
            "run_id": rid, "place": place, "kind": kind, "date": ri["flood_date"], "img": name,
            "flood_km2": ff["flood"]["flood_km2"], "buildings_hit": ff["buildings"]["hit"],
            "bridges_hit": ff["bridges"]["hit"], "cut_off": ff["settlements"]["cut_off"], "detour": ff["settlements"]["detour"],
        })

    print("official map check (EMSR927)")
    Image.open(ROOT / "report" / "figures" / "case_study_maps.png").convert("RGB").save(OUT / "ems_compare.jpg", quality=86, optimize=True)

    fc = facts(CONTROL)
    top = sorted(f["settlements"]["cut_off_list"], key=lambda a: -(a.get("priority") or 0))[:4]
    data = {
        "main": {
            "run_id": MAIN, "name": info.get("name"), "date": info["flood_date"],
            "flood_km2": f["flood"]["flood_km2"], "debris_km2": f["flood"]["debris_km2"], "new_water_km2": f["flood"]["new_water_km2"],
            "buildings_hit": f["buildings"]["hit"], "buildings_total": f["buildings"]["total"],
            "road_km": f["roads"]["flooded_km"], "bridges_hit": f["bridges"]["hit"], "bridges_total": f["bridges"]["total"],
            "cut_off": f["settlements"]["cut_off"], "detour": f["settlements"]["detour"],
            "valley_seen_pct": f["flood"]["valley_seen_pct"], "resolution_m": f["meta"]["resolution_m"],
        },
        "radar": {"tracks": f["images"]["radar"], "zoom_km": round(km, 1), "zoom_near": near["name"]},
        "optical": f["images"].get("optical"),
        "ems": ems_numbers(),
        "control": {
            "run_id": CONTROL, "date": run_info(CONTROL)["flood_date"], "flood_km2": fc["flood"]["flood_km2"],
            "possible_km2": fc["flood"].get("possible_km2"),
            "buildings_hit": fc["buildings"]["hit"], "bridges_hit": fc["bridges"]["hit"], "cut_off": fc["settlements"]["cut_off"],
        },
        "floods": floods,
        "priority": {
            "weights": {"size": 40, "damage": 30, "isolation": 20, "remoteness": 10},
            "top": [{"name": a["name"], "name_ne": a.get("name_ne"), "priority": a.get("priority"), "parts": a.get("priority_parts"),
                     "buildings": a["buildings_nearby"], "hit": a["buildings_hit_nearby"], "breaks": len(a.get("breaks") or []),
                     "km_before": a.get("dist_before_km")} for a in top],
        },
        "ai": copilot_example("Which villages need help first, and why?"),
    }
    (OUT / "evidence.json").write_text(json.dumps(data, indent=1, ensure_ascii=False), encoding="utf-8")
    print("  evidence.json")


if __name__ == "__main__":
    main()
