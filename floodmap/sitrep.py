"""One-page situation reports (English + Nepali), written from facts.json only."""
from __future__ import annotations

import html
import json
from datetime import datetime, timezone
from pathlib import Path

import geopandas as gpd
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402

from . import copilot  # noqa: E402
from .names import to_nepali_digits  # noqa: E402

LABELS = {
    "en": {"title": "Flood situation report", "area": "Area", "date": "Flood date", "made": "Made",
           "flood": "Flooded / debris", "bld": "Buildings hit", "road": "Road under flood", "bridge": "Bridges hit",
           "cut": "Settlements cut off", "detour": "Long detour", "summary": "Summary",
           "cutlist": "Cut-off settlements (most urgent first)", "name": "Settlement", "bnear": "Buildings nearby", "prio": "Priority",
           "why": "What broke the road", "before": "Road km to help (before)", "none": "No settlement lost its road connection.",
           "images": "Images used", "radar": "Radar (Sentinel-1)", "optical": "Photo (Sentinel-2)",
           "limits": "Limits: 10 m pixels miss small houses and footbridges; radar can't see some steep slopes; "
                     "OpenStreetMap may miss buildings and roads; 'hit' means touched by the flood, not destroyed. "
                     "Educational prototype - not an operational tool.",
           "numbers": "Every number on this page was computed by code from the satellite maps.",
           "inserted": "values inserted by code, 0 digits written by the AI",
           "bridge_word": "bridge", "road_word": "road", "of": "of", "km2": "km²", "km": "km",
           "legend": "Map: orange = debris/mud, blue = new water, red line = cut road, red dot = cut-off settlement"},
    "ne": {"title": "बाढी स्थिति प्रतिवेदन", "area": "क्षेत्र", "date": "बाढी मिति", "made": "तयार मिति",
           "flood": "बाढी / लेदो क्षेत्र", "bld": "प्रभावित भवन", "road": "बाढीमुनि सडक", "bridge": "प्रभावित पुल",
           "cut": "सम्पर्कविहीन बस्ती", "detour": "लामो घुमाउरो बाटो", "summary": "सारांश",
           "cutlist": "सम्पर्कविहीन बस्ती (सबैभन्दा जरुरी पहिले)", "name": "बस्ती", "bnear": "नजिकका भवन", "prio": "प्राथमिकता",
           "why": "सडक कहाँ टुट्यो", "before": "सहयोगसम्म सडक (किमी, पहिले)", "none": "कुनै बस्तीको सडक सम्पर्क टुटेको छैन।",
           "images": "प्रयोग गरिएका तस्बिर", "radar": "राडार (सेन्टिनेल-१)", "optical": "तस्बिर (सेन्टिनेल-२)",
           "limits": "सीमा: १० मिटर पिक्सेलले साना घर र झोलुङ्गे पुल छुटाउन सक्छ; राडारले केही ठाडा भिर देख्दैन; "
                     "ओपनस्ट्रिटम्यापमा केही भवन र सडक नहुन सक्छन्; 'प्रभावित' भनेको बाढीले छोएको हो, ध्वस्त भएको होइन। "
                     "शैक्षिक नमुना मात्र - आधिकारिक प्रयोगका लागि होइन।",
           "numbers": "यस पानाका सबै अंक उपग्रह नक्साबाट कोडले निकालेका हुन्।",
           "inserted": "मान कोडले राखेको, एआईले कुनै अंक लेखेन",
           "bridge_word": "पुल", "road_word": "सडक", "of": "मध्ये", "km2": "वर्ग किमी", "km": "किमी",
           "legend": "नक्सा: सुन्तला = लेदो/ढुंगा, नीलो = नयाँ पानी, रातो रेखा = टुटेको सडक, रातो थोप्लो = सम्पर्कविहीन बस्ती"},
}


def _n(v, lang, nd=1):
    s = f"{v:,.{nd}f}" if isinstance(v, float) else f"{v:,}"
    if isinstance(v, float) and nd == 1 and s.endswith(".0"):
        s = s[:-2]
    return to_nepali_digits(s) if lang == "ne" else s


def static_map(run_dir: Path) -> Path:
    """Overview map for the report (PNG)."""
    path = run_dir / "sitrep_map.png"
    lay = run_dir / "layers"
    ov = json.loads((run_dir / "overlays" / "overlays.json").read_text())
    from PIL import Image
    fig, ax = plt.subplots(figsize=(6.4, 4.6), dpi=150)
    for key in ("hillshade", "flood"):
        if key in ov:
            (s, w), (n, e) = ov[key]
            img = Image.open(run_dir / "overlays" / f"{key}.png")
            # PNGs are in Web Mercator; plot in mercator-ish coordinates by stretching to lon/lat (fine for small areas)
            ax.imshow(np.asarray(img), extent=(w, e, s, n), interpolation="nearest", zorder=1 if key == "hillshade" else 2)
    try:
        rc = gpd.read_file(lay / "roads_cut.geojson")
        if len(rc):
            rc.plot(ax=ax, color="#d7191c", linewidth=1.6, zorder=4)
    except Exception:  # noqa: BLE001
        pass
    st = gpd.read_file(lay / "settlements.geojson") if (lay / "settlements.geojson").exists() else None
    if st is not None and len(st):
        colors = {"cut_off": "#d7191c", "detour": "#fdae61", "ok": "#1a9641", "no_road": "#999999"}
        for status, c in colors.items():
            sub = st[st["status"] == status]
            if len(sub):
                sub.plot(ax=ax, color=c, markersize=18 if status == "cut_off" else 8, edgecolor="white", linewidth=0.5, zorder=5)
        big = st[st["status"] == "cut_off"].sort_values("buildings_nearby", ascending=False).head(6)
        for _, r in big.iterrows():
            ax.annotate(r["name"], (r.geometry.x, r.geometry.y), xytext=(4, 3), textcoords="offset points",
                        fontsize=6.5, color="#7f0000", zorder=6,
                        bbox=dict(boxstyle="round,pad=0.15", fc="white", ec="none", alpha=0.75))
    aoi = gpd.read_file(lay / "aoi.geojson")
    w, s, e, n = aoi.total_bounds
    ax.set_xlim(w, e)
    ax.set_ylim(s, n)
    ax.set_aspect(1 / np.cos(np.radians((s + n) / 2)))
    ax.tick_params(labelsize=6)
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)
    return path


def render_html(facts: dict, lang: str, summary: dict, map_file: str) -> str:
    L = LABELS[lang]
    m, f, b, r, br, s = (facts[k] for k in ("meta", "flood", "buildings", "roads", "bridges", "settlements"))
    esc = html.escape
    tiles = [
        (L["flood"], f'{_n(float(f["flood_km2"]), lang, 2)} {L["km2"]}'),
        (L["bld"], f'{_n(b["hit"], lang)} <small>{L["of"]} {_n(b["total"], lang)}</small>'),
        (L["road"], f'{_n(float(r["flooded_km"]), lang)} {L["km"]}'),
        (L["bridge"], f'{_n(br["hit"], lang)} <small>{L["of"]} {_n(br["total"], lang)}</small>'),
        (L["cut"], f'{_n(s["cut_off"], lang)}'),
        (L["detour"], f'{_n(s["detour"], lang)}'),
    ]
    rows = []
    for a in sorted(s["cut_off_list"], key=lambda a: (-a.get("priority", 0), -a.get("buildings_nearby", 0)))[:9]:
        nm = a.get("name_ne") or a["name"] if lang == "ne" else a["name"]
        why = []
        for k in a.get("breaks", [])[:2]:
            what = L["bridge_word"] if k["bridge"] else L["road_word"]
            why.append(f'{what} {esc(k.get("road_name") or "")} ({_n(int(k["length_m"]), lang)} m)'.replace(" ()", ""))
        rows.append(f"<tr><td>{esc(nm)}</td><td><b>{_n(int(a['priority']), lang) if a.get('priority') is not None else '-'}</b></td><td>{_n(int(a.get('buildings_nearby', 0)), lang)}</td>"
                    f"<td>{'; '.join(why) or '-'}</td><td>{_n(float(a['dist_before_km']), lang) if a.get('dist_before_km') is not None else '-'}</td></tr>")
    table = (f"<table><tr><th>{L['name']}</th><th>{L['prio']}</th><th>{L['bnear']}</th><th>{L['why']}</th><th>{L['before']}</th></tr>"
             + "".join(rows) + "</table>") if rows else f"<p>{L['none']}</p>"
    radar = "; ".join(f'T{t["track"]} {t["direction"][:4]}. {", ".join(t["before"])} → {", ".join(t["after"])}'
                      for t in facts["images"]["radar"]) or "-"
    opt = facts["images"].get("optical")
    optical = f'{opt["before"]} → {opt["after"]}' if opt else "-"
    made = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    font = "'Noto Sans Devanagari', 'Nirmala UI', 'Mangal', sans-serif" if lang == "ne" else "'Segoe UI', Roboto, Arial, sans-serif"
    return f"""<!doctype html><html lang="{lang}"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{esc(L['title'])} - {esc(m.get('area_name') or '')}</title>
<link href="https://fonts.googleapis.com/css2?family=Noto+Sans+Devanagari:wght@400;600;700&display=swap" rel="stylesheet">
<style>
:root {{ --ink:#1b1f24; --muted:#5b6470; --line:#d9dee5; --accent:#b4471a; --bg:#ffffff; }}
@page {{ size: A4; margin: 10mm; }}
* {{ box-sizing: border-box; }}
body {{ font-family: {font}; color: var(--ink); background: var(--bg); margin: 0; font-size: 11.5px; line-height: 1.45; }}
.page {{ max-width: 190mm; margin: 0 auto; padding: 8px 12px; }}
header {{ display:flex; justify-content:space-between; align-items:flex-end; border-bottom: 3px solid var(--accent); padding-bottom: 6px; }}
h1 {{ margin: 0; font-size: 20px; }}
.meta {{ color: var(--muted); font-size: 11px; text-align: right; }}
.tiles {{ display:grid; grid-template-columns: repeat(6, 1fr); gap: 6px; margin: 8px 0; }}
.tile {{ border:1px solid var(--line); border-radius:6px; padding:6px 7px; }}
.tile b {{ display:block; font-size: 17px; }}
.tile span {{ color: var(--muted); font-size: 10px; }}
.tile small {{ font-size: 10px; color: var(--muted); font-weight: 400; }}
.grid {{ display:grid; grid-template-columns: 1.05fr 1fr; gap: 10px; }}
h2 {{ font-size: 12.5px; margin: 6px 0 4px; color: var(--accent); }}
img.map {{ width: 100%; border:1px solid var(--line); border-radius: 4px; }}
table {{ width:100%; border-collapse: collapse; font-size: 10.5px; }}
th, td {{ border-bottom:1px solid var(--line); padding: 3px 4px; text-align:left; vertical-align: top; }}
th {{ color: var(--muted); font-weight: 600; }}
.note {{ color: var(--muted); font-size: 9.5px; }}
.badge {{ display:inline-block; background:#eef6ee; color:#1d6b2a; border:1px solid #bfe0c3; border-radius: 10px; padding: 0 7px; font-size: 9.5px; }}
footer {{ margin-top: 8px; border-top:1px solid var(--line); padding-top: 5px; color: var(--muted); font-size: 8.8px; }}
.noprint {{ margin: 8px 0; }}
@media print {{ .noprint {{ display:none; }} }}
@media (max-width: 700px) {{ .tiles {{ grid-template-columns: repeat(2, 1fr); }} .grid {{ grid-template-columns: 1fr; }} }}
</style></head><body><div class="page">
<div class="noprint"><button onclick="window.print()">Print / Save as PDF</button></div>
<header><div><h1>{esc(L['title'])}</h1><div>{esc(L['area'])}: <b>{esc(m.get('area_name') or '')}</b> · {esc(L['date'])}: <b>{esc(m['flood_date'])}</b></div></div>
<div class="meta">{esc(L['made'])}: {made}<br>{esc(m['data_source'])}</div></header>
<div class="tiles">{''.join(f'<div class="tile"><b>{v}</b><span>{esc(k)}</span></div>' for k, v in tiles)}</div>
<div class="grid"><div><img class="map" src="{map_file}" alt="map"><div class="note">{esc(L['legend'])}</div></div>
<div><h2>{esc(L['summary'])}</h2><p>{esc(summary['text'])}</p>
<p class="note"><span class="badge">✓ {esc(L['numbers'])}</span><br>{esc(summary.get('by', ''))} · {summary.get('proof', {}).get('values_inserted_by_code', 0)} {esc(L['inserted'])}</p></div></div>
<h2>{esc(L['cutlist'])}</h2>{table}
<p class="note"><b>{esc(L['images'])}:</b> {esc(L['radar'])}: {esc(radar)} · {esc(L['optical'])}: {esc(optical)} · OSM: {esc(facts['osm']['snapshot_date'])}</p>
<p class="note">{esc(L['limits'])}</p>
<footer>{'<br>'.join(esc(a) for a in facts['attribution'])}<br>Flood Damage from Space · made by Suraj Mishra</footer>
</div></body></html>"""


def write_reports(run_dir: Path, progress=None) -> dict:
    run_dir = Path(run_dir)
    facts = json.loads((run_dir / "facts.json").read_text(encoding="utf-8"))
    if progress:
        progress("drawing the report map")
    static_map(run_dir)
    out = {}
    for lang in ("en", "ne"):
        if progress:
            progress(f"writing the {lang} summary")
        summary = copilot.sitrep_text(facts, lang)
        (run_dir / f"sitrep_{lang}.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
        page = render_html(facts, lang, summary, "sitrep_map.png")
        (run_dir / f"sitrep_{lang}.html").write_text(page, encoding="utf-8")
        out[lang] = summary
    return out
