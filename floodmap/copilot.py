"""AI component: the situation-report copilot (English + Nepali).

Rule from the brief: "Every number must come from your system's maps, never
invented by the language model."  How we guarantee it:

  1. Code builds a FACT TABLE (id -> value) from facts.json.
  2. The language model (Groq) refers to numbers, dates and names ONLY through
     placeholders like {buildings_hit}. It must not write a single digit.
  3. A checker rejects any text that contains a digit, a number word or an
     unknown placeholder. Then code fills in the real values.
  4. No API key, model error, or failed check -> a fixed template is used.
So a number can only ever reach the reader through our own code.
"""
from __future__ import annotations

import json
import re
import time

import requests

from .config import GROQ_API_KEY, GROQ_MODEL, ROOT
from .names import to_nepali_digits

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
PLACEHOLDER = re.compile(r"\{([a-z0-9_]+)\}")
DIGIT = re.compile(r"[0-9०-९]")
NUMBER_WORDS_EN = re.compile(
    r"\b(zero|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|thirteen|fourteen|fifteen|"
    r"sixteen|seventeen|eighteen|nineteen|twenty|thirty|forty|fifty|sixty|seventy|eighty|ninety|hundred|"
    r"hundreds|thousand|thousands|million|lakh|crore|dozen|dozens|twice|double|triple|half)\b", re.I)
NUMBER_WORDS_NE = re.compile(r"(दुई|तीन|चार|पाँच|सात|आठ|नौ|दस|बीस|पचास|सय|हजार|लाख|करोड)")
ALLOWED_PHRASES = re.compile(r"\b(no one|anyone|someone|everyone)\b", re.I)


def _key() -> str:
    from dotenv import dotenv_values
    return (dotenv_values(ROOT / ".env").get("GROQ_API_KEY") or GROQ_API_KEY or "").strip()


def _model() -> str:
    from dotenv import dotenv_values
    return (dotenv_values(ROOT / ".env").get("GROQ_MODEL") or GROQ_MODEL).strip()


def _num(v, nd=1) -> str:
    if isinstance(v, float):
        s = f"{v:,.{nd}f}"
        return s[:-2] if s.endswith(".0") and nd == 1 else s
    return f"{v:,}" if isinstance(v, int) else str(v)


def fact_table(facts: dict) -> dict[str, dict]:
    """id -> {"en": text, "ne": text, "label": what it means}."""
    t: dict[str, dict] = {}

    KM2, PCT, KM = (" km²", " वर्ग किमी"), ("%", "%"), (" km", " किमी")

    def add(fid, value, label, ne=None, nd=1, unit=("", "")):
        # the unit is part of the value, so the language model can't drop or change it
        num = _num(value, nd) if not isinstance(value, str) else value
        t[fid] = {"en": num + unit[0], "ne": (ne if ne is not None else to_nepali_digits(num)) + unit[1],
                  "label": label}

    m, f, b, r, br, s = (facts[k] for k in ("meta", "flood", "buildings", "roads", "bridges", "settlements"))
    add("area_name", m.get("area_name") or "the selected area", "name of the area")
    add("flood_date", m["flood_date"], "flood date")
    add("area_km2", float(m["area_km2"]), "size of the study area in km²", unit=KM2)
    add("flood_km2", float(f["flood_km2"]), "flooded or debris-covered area in km² (medium+high confidence)", nd=2, unit=KM2)
    add("flood_high_km2", float(f["high_km2"]), "of that, high-confidence km²", nd=2, unit=KM2)
    add("new_water_km2", float(f["new_water_km2"]), "new open water km²", nd=2, unit=KM2)
    add("debris_km2", float(f["debris_km2"]), "debris / mud km²", nd=2, unit=KM2)
    add("valley_seen_pct", float(f["valley_seen_pct"]), "share of the valley floor the satellites could see, %", nd=0, unit=PCT)
    add("valley_unseen_pct", max(0.0, 100.0 - float(f["valley_seen_pct"])), "share of the valley floor hidden from the satellites", nd=0, unit=PCT)
    add("not_known", "This map-based system cannot know casualties, missing people, population numbers, damage inside "
        "buildings, or anything outside the study area.", "a full sentence to use when asked about something the maps do not show",
        ne="यो नक्सामा आधारित प्रणालीले मृतक वा बेपत्ता संख्या, जनसंख्या, भवनभित्रको क्षति वा अध्ययन क्षेत्र बाहिरको अवस्था थाहा पाउन सक्दैन।")
    add("limits", "10 m pixels miss small houses and footbridges; steep slopes are partly hidden from radar; only the valley floor is checked",
        "main limits of the satellite maps", ne="१० मिटर पिक्सेलले साना घर र झोलुङ्गे पुल छुटाउन सक्छ; ठाडा भिर राडारबाट आंशिक लुकेका हुन्छन्; नदी किनारको तल्लो भाग मात्र जाँचिन्छ")
    add("buildings_total", int(b["total"]), "mapped buildings in the area (OpenStreetMap before the flood)")
    add("buildings_hit", int(b["hit"]), "buildings in or touching the flood/debris area")
    add("buildings_hit_pct", round(100.0 * b["hit"] / b["total"]) if b["total"] else 0, "buildings hit, % of all", unit=PCT)
    add("roads_total_km", float(r["total_km"]), "km of road in the area", unit=KM)
    add("roads_flooded_km", float(r["flooded_km"]), "km of road under flood or debris", unit=KM)
    add("bridges_total", int(br["total"]), "bridges in the area")
    add("bridges_hit", int(br["hit"]), "bridges reached by the flood")
    add("settlements_total", int(s["total"]), "villages/towns in the area")
    add("cut_off_count", int(s["cut_off"]), "settlements cut off from every town/hospital by road")
    add("detour_count", int(s["detour"]), "settlements reachable only by a long detour")
    add("hospitals_hit", int(facts["hospitals"]["hit"]), "hospitals/health posts reached by the flood")
    radar = facts["images"]["radar"]
    if radar:
        add("radar_after_date", max(d for t_ in radar for d in t_["after"]), "date of the latest radar image used")
        add("radar_first_after_date", min(t_["after"][0] for t_ in radar), "date of the first radar image after the flood")
    opt = facts["images"].get("optical")
    if opt:
        add("optical_after_date", opt["after"], "date of the cloud-free optical image after the flood")
    cut = sorted(s["cut_off_list"], key=lambda a: (-a.get("priority", 0), -a.get("buildings_nearby", 0)))
    add("cut_off_names", ", ".join(a["name"] for a in cut[:8]) or "none",
        "names of cut-off settlements (most urgent first, by rescue priority)", ne=", ".join(a.get("name_ne") or a["name"] for a in cut[:8]) or "छैन")
    for i, a in enumerate(cut[:10], 1):
        add(f"cutoff{i}_name", a["name"], f"cut-off settlement #{i}: name", ne=a.get("name_ne") or a["name"])
        if a.get("priority") is not None:
            add(f"cutoff{i}_priority", int(a["priority"]),
                f"cut-off settlement #{i}: rescue priority score out of 100 (computed from size, damage, isolation and remoteness; higher = help first)")
        add(f"cutoff{i}_buildings", int(a.get("buildings_nearby", 0)), f"cut-off settlement #{i}: buildings nearby")
        add(f"cutoff{i}_breaks", len(a.get("breaks", [])), f"cut-off settlement #{i}: broken stretches on its old route")
        if a.get("dist_before_km") is not None:
            add(f"cutoff{i}_km_before", float(a["dist_before_km"]), f"cut-off settlement #{i}: road distance to help before the flood", unit=KM)
    det = sorted(s["detour_list"], key=lambda a: -(a.get("dist_after_km") or 0) + (a.get("dist_before_km") or 0))
    for i, a in enumerate(det[:6], 1):
        add(f"detour{i}_name", a["name"], f"detour settlement #{i}: name", ne=a.get("name_ne") or a["name"])
        add(f"detour{i}_extra_km", round(float(a["dist_after_km"] - a["dist_before_km"]), 1), f"detour settlement #{i}: extra distance", unit=KM)
    return t


def check(text: str, table: dict) -> list[str]:
    """Problems that make the text unsafe (empty list = OK)."""
    problems = [f"unknown placeholder {{{p}}}" for p in PLACEHOLDER.findall(text) if p not in table]
    bare = PLACEHOLDER.sub("", text)
    if DIGIT.search(bare):
        problems.append("the model wrote a digit itself")
    words = NUMBER_WORDS_EN.findall(ALLOWED_PHRASES.sub("", bare))
    if words:
        problems.append(f"number words: {sorted(set(w.lower() for w in words))}")
    if NUMBER_WORDS_NE.search(bare):
        problems.append("Nepali number words")
    return problems


def fill(text: str, table: dict, lang: str) -> str:
    return PLACEHOLDER.sub(lambda mt: table[mt.group(1)][lang], text)


def call_llm(messages: list[dict], max_tokens: int = 700) -> str:
    key = _key()
    if not key:
        raise RuntimeError("no GROQ_API_KEY in .env")
    model = _model()
    body = {"model": model, "messages": messages, "temperature": 0.2, "max_tokens": max_tokens}
    if "gpt-oss" in model:  # "thinking" model: keep the thinking short, leave room for the answer
        body.update(reasoning_effort="low", max_tokens=max_tokens * 3)
    for attempt in range(3):
        r = requests.post(GROQ_URL, headers={"Authorization": f"Bearer {key}"}, json=body, timeout=60)
        if r.status_code == 429 and attempt < 2:  # free-tier speed limit: wait and try again
            wait = r.headers.get("retry-after") or "5"
            time.sleep(min(20.0, float(wait) if wait.replace(".", "").isdigit() else 5.0))
            continue
        break
    if r.status_code != 200:
        raise RuntimeError(f"Groq {r.status_code}: {r.text[:200]}")
    text = (r.json()["choices"][0]["message"].get("content") or "").strip()
    if not text:
        raise RuntimeError("Groq returned an empty answer")
    return text


RULES = ("STRICT RULES: (1) Every number, date, measurement or place name must be written ONLY as a "
         "placeholder copied exactly from the FACTS list, e.g. {buildings_hit}. (2) Never write any digit "
         "or number word yourself (not even 'two', 'dozens', 'half', 'hundreds'). (3) Use only the facts "
         "given; if something is not in the facts, say it is not known. (4) Plain sentences, no markdown, "
         "no headings, no bullet points.")


def _facts_prompt(table: dict) -> str:
    return "\n".join(f"{{{k}}} = {v['en']}  ({v['label']})" for k, v in table.items())


def _generate(task: str, table: dict, lang: str, max_tokens=700) -> tuple[str | None, list[str], dict]:
    language = ("Write in Nepali (Devanagari script). Keep the placeholders exactly as written, in Latin "
                "letters inside curly braces." if lang == "ne" else "Write in simple English.")
    msgs = [{"role": "system", "content": "You help rescue teams after floods. " + RULES + " " + language},
            {"role": "user", "content": f"FACTS:\n{_facts_prompt(table)}\n\nTASK: {task}"}]
    problems, rejected = [], []
    for _ in range(2):
        try:
            raw = call_llm(msgs, max_tokens)
        except Exception as err:  # noqa: BLE001
            return None, [str(err)], {"rejected_drafts": rejected}
        problems = check(raw, table)
        if not problems:
            return fill(raw, table, lang), [], proof(raw, rejected)
        rejected.append({"text": raw, "problems": problems})
        msgs += [{"role": "assistant", "content": raw},
                 {"role": "user", "content": "Your text broke the rules: " + "; ".join(problems) +
                  ". Rewrite it using ONLY placeholders for numbers, dates and names."}]
    return None, problems, {"rejected_drafts": rejected}


def proof(raw: str, rejected=None) -> dict:
    """What the judges can inspect: the model's own words, with the slots our code filled."""
    slots = PLACEHOLDER.findall(raw)
    return {"model_text": raw, "values_inserted_by_code": len(slots),
            "digits_written_by_model": 0, "rejected_drafts": rejected or []}


TEMPLATE_EN = (
    "Flood situation for {area_name}, flood date {flood_date}. Satellite images show about {flood_km2} of "
    "flooded or debris-covered ground on the valley floor ({flood_high_km2} with high confidence). "
    "{buildings_hit} of {buildings_total} mapped buildings ({buildings_hit_pct}) are inside or touching the "
    "flood area. {roads_flooded_km} of road lies under flood or debris, and the flood reached "
    "{bridges_hit} of {bridges_total} bridges. {cut_off_count} settlements have lost every road connection "
    "to a town or hospital: {cut_off_names}. {detour_count} more can only be reached by a long detour. "
    "Hospitals or health posts reached by the flood: {hospitals_hit}."
)
TEMPLATE_NE = (
    "{area_name} को बाढी स्थिति, बाढी मिति {flood_date}। उपग्रह तस्बिरअनुसार नदी किनारको करिब {flood_km2} "
    "क्षेत्र बाढी वा लेदो-ढुंगाले ढाकिएको छ (उच्च विश्वसनीयता: {flood_high_km2})। "
    "नक्सामा रहेका {buildings_total} भवनमध्ये {buildings_hit} भवन ({buildings_hit_pct}) बाढी क्षेत्रभित्र वा छेउमा छन्। "
    "{roads_flooded_km} सडक बाढी वा लेदोमुनि छ, र {bridges_total} पुलमध्ये {bridges_hit} पुलसम्म बाढी पुगेको छ। "
    "{cut_off_count} बस्तीको सहर वा अस्पतालसँगको सबै सडक सम्पर्क टुटेको छ: {cut_off_names}। "
    "थप {detour_count} बस्तीमा लामो घुमाउरो बाटोबाट मात्र पुग्न सकिन्छ। "
    "बाढी पुगेका अस्पताल वा स्वास्थ्य चौकी: {hospitals_hit}।"
)


def sitrep_text(facts: dict, lang: str = "en") -> dict:
    """Short situation report. Returns {"text", "by", "checked", "note"}."""
    table = fact_table(facts)
    task = ("Write a short situation report (about 120-170 words, one paragraph) for rescue teams: where the "
            "flood hit, what was damaged, which settlements are cut off and need help first, and one sentence "
            "on what the satellites could not see. Use the facts only.")
    text, problems, pr = _generate(task, table, lang)
    if text:
        return {"text": text, "by": f"Groq · {_model()}", "checked": True, "proof": pr,
                "note": "Written by the language model; every number was inserted by our code from the maps."}
    tpl = TEMPLATE_NE if lang == "ne" else TEMPLATE_EN
    return {"text": fill(tpl, table, lang), "by": "template", "checked": True, "proof": proof(tpl) | pr,
            "note": "Fixed template (language model not used: " + ("; ".join(problems) or "unavailable") + ")."}


def answer(facts: dict, question: str, lang: str = "en") -> dict:
    """Answer a rescuer's question from the maps only."""
    table = fact_table(facts)
    q = (question or "").strip()[:500]
    if not q:
        return {"text": "", "by": "none", "checked": True}
    task = ("A rescuer asks: \"" + q.replace("{", "(").replace("}", ")") + "\". Answer in two to four short "
            "sentences using only the facts. If the facts cannot answer it, say so and say what the map can show instead.")
    text, problems, pr = _generate(task, table, lang, max_tokens=350)
    if text:
        return {"text": text, "by": f"Groq · {_model()}", "checked": True, "proof": pr}
    tpl = TEMPLATE_NE if lang == "ne" else TEMPLATE_EN
    return {"text": ("(AI not available - here is the summary from the maps.) " if lang == "en" else
                     "(एआई उपलब्ध छैन - नक्साबाट सारांश:) ") + fill(tpl, table, lang),
            "by": "template", "checked": True, "note": "; ".join(problems), "proof": proof(tpl) | pr}


if __name__ == "__main__":  # quick self-test of the checker
    tbl = {"a": {"en": "5", "ne": "५", "label": ""}}
    print(check("{a} houses hit", tbl), check("5 houses", tbl), check("two bridges {b}", tbl))
    print(json.dumps(fill("{a} houses", tbl, "ne"), ensure_ascii=False))
