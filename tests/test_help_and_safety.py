"""The "I need help" side: anti-fraud rules, the PIN-protected inbox, and limits per visitor."""
import pytest
from conftest import needs_main_run

from web import app as webapp
from web import helpdesk

DELHI = {"lat": 28.6139, "lon": 77.2090}
RASUWA_GADHI = {"lat": 28.27777, "lon": 85.37778}   # inside the Trishuli flood area


def request_from(gps, chosen=None, how="gps"):
    chosen = chosen or gps
    return {"lat": chosen["lat"], "lon": chosen["lon"], "how": how, "people": 3, "consent": True,
            "name": "Test person", "phone": "9800000000", "gps_lat": gps["lat"], "gps_lon": gps["lon"], "gps_accuracy_m": 10}


def test_a_phone_outside_every_mapped_flood_area_is_refused(client):
    r = client.post("/api/help", json=request_from(DELHI))
    assert r.status_code == 403
    assert r.json()["detail"]["code"] == "outside"
    assert helpdesk.all_requests() == []


@needs_main_run
def test_a_place_far_from_the_phone_is_refused_and_a_real_one_is_saved(client):
    far = {"lat": RASUWA_GADHI["lat"] - 0.1, "lon": RASUWA_GADHI["lon"]}        # about 11 km away
    r = client.post("/api/help", json=request_from(RASUWA_GADHI, chosen=far, how="map"))
    assert r.status_code == 403 and r.json()["detail"]["code"] == "far"
    r = client.post("/api/help", json=request_from(RASUWA_GADHI))
    assert r.status_code == 200
    assert r.json()["level"] == "danger"          # the danger level comes from our maps, not from the sender
    assert len(helpdesk.all_requests()) == 1


def test_the_danger_check_uses_the_latest_flood_not_a_dry_season_test():
    from floodmap.safety import best_first
    runs = [{"run_id": "dry", "name": "No-flood test", "flood_date": "2026-04-15", "started": "2026-10-08"},
            {"run_id": "quick", "name": None, "flood_date": "2026-08-26", "started": "2026-10-13"},
            {"run_id": "old", "name": "Rasuwa 2025", "flood_date": "2025-07-08", "started": "2026-10-03"},
            {"run_id": "fb7507902568", "name": "Upper Trishuli", "flood_date": "2026-08-26", "started": "2026-10-03"}]
    assert [r["run_id"] for r in best_first(runs)] == ["fb7507902568", "quick", "dry", "old"]


@needs_main_run
def test_a_phone_in_rasuwa_gadhi_gets_danger_even_with_a_test_map_open():
    from floodmap.safety import check
    r = check(RASUWA_GADHI["lon"], RASUWA_GADHI["lat"], rid="fbdf29eca776")   # the dry-season test, if this copy has it
    assert r["run_id"] == "fb7507902568" and r["level"] == "danger"


def test_the_inbox_needs_the_pin_and_locks_after_ten_wrong_tries(client, monkeypatch):
    monkeypatch.setattr(webapp, "RESCUE_PIN", "2468")
    assert client.get("/api/help").status_code == 401
    assert client.get("/api/help", headers={"X-Rescue-Pin": "2468"}).status_code == 200
    for _ in range(10):
        assert client.get("/api/help", headers={"X-Rescue-Pin": "0000"}).status_code == 401
    assert client.get("/api/help", headers={"X-Rescue-Pin": "2468"}).status_code == 429


def test_the_public_counter_shows_counts_but_no_personal_details(client):
    from floodmap.safety import check
    helpdesk.add({"lat": 28.2, "lon": 85.4, "name": "Sita Tamang", "phone": "9841111111", "people": 2}, check(85.4, 28.2))
    r = client.get("/api/help/summary")
    assert r.status_code == 200 and r.json()["new"] == 1
    assert "Sita" not in r.text and "9841111111" not in r.text and "28.2" not in r.text


def test_copilot_questions_are_limited_per_visitor(client, fake_run, monkeypatch):
    import floodmap.copilot
    monkeypatch.setattr(floodmap.copilot, "answer", lambda facts, q, lang: {"text": "ok"})  # no real language model
    codes = [client.post(f"/api/runs/{fake_run}/ask", json={"question": "Who is cut off?"}).status_code for _ in range(21)]
    assert codes[:20] == [200] * 20 and codes[20] == 429


@pytest.mark.parametrize("path", ["/.env", "/static/..%2F..%2F.env", "/api/runs/..%2F..%2F.env/facts", "/api/runs/fb75/../../.env"])
def test_secret_files_cannot_be_downloaded(client, path):
    r = client.get(path)
    assert r.status_code in (404, 405)
    assert "GROQ" not in r.text and "RESCUE_PIN" not in r.text
