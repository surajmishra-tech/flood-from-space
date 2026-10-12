"""Pages open where they should, and the evidence on the website matches the report."""
import json
import re

from conftest import MAIN_RUN, ROOT, needs_main_run


def test_home_page_dashboard_and_old_links(client):
    assert 'id="gl"' in client.get("/").text                      # the intro
    assert 'id="topbar"' in client.get("/app").text               # the dashboard
    assert 'id="topbar"' in client.get("/?mode=help").text        # links made before the intro existed
    assert 'id="topbar"' in client.get("/?run=fb7507902568").text
    assert client.get("/healthz").json() == {"ok": True}
    r = client.get("/report.pdf")
    assert r.status_code == 200 and r.headers["content-type"] == "application/pdf"


def test_evidence_numbers_match_the_report():
    ev = json.loads((ROOT / "web" / "static" / "evidence" / "evidence.json").read_text(encoding="utf-8"))
    pooled = next(line for line in (ROOT / "report" / "report.md").read_text(encoding="utf-8").splitlines()
                  if "All, pooled" in line)
    precision, recall = (float(x) for x in re.findall(r"\b0\.\d\d\b", pooled)[:2])
    assert (ev["ems"]["pooled"]["precision"], ev["ems"]["pooled"]["recall"]) == (precision, recall)


@needs_main_run
def test_evidence_numbers_match_the_saved_result():
    ev = json.loads((ROOT / "web" / "static" / "evidence" / "evidence.json").read_text(encoding="utf-8"))["main"]
    facts = json.loads((MAIN_RUN / "facts.json").read_text(encoding="utf-8"))
    assert ev["flood_km2"] == facts["flood"]["flood_km2"]
    assert ev["buildings_hit"] == facts["buildings"]["hit"]
    assert ev["cut_off"] == facts["settlements"]["cut_off"]
