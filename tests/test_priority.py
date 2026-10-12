"""Rescue priority (floodmap/priority.py): 0-100, computed by code, most urgent first."""
import itertools

from floodmap.priority import add_priority, score


def village(**kw):
    v = {"status": "cut_off", "buildings_nearby": 100, "buildings_hit_nearby": 10, "center_flooded": False, "dist_before_km": 10.0}
    v.update(kw)
    return v


def test_score_always_stays_between_0_and_100():
    for n, hit, status, flooded, km in itertools.product(
            [0, 1, 87, 400, 5000], [0, 1, 50, 5000], ["cut_off", "detour", "ok"], [False, True], [None, 0.0, 25.0, 300.0]):
        s = score(village(buildings_nearby=n, buildings_hit_nearby=min(hit, n), status=status, center_flooded=flooded,
                          dist_before_km=km, dist_after_km=(km or 0) + 15))
        assert 0 <= s <= 100


def test_more_damage_and_more_isolation_mean_higher_priority():
    assert score(village(center_flooded=True)) > score(village(buildings_hit_nearby=0))
    assert score(village(status="cut_off")) > score(village(status="detour", dist_after_km=15.0))


def test_known_village_keeps_its_score():
    # Resuo in the Trishuli result: 87 buildings, its centre in the flood, cut off, 25 km from help before
    assert score(village(buildings_nearby=87, buildings_hit_nearby=20, center_flooded=True, dist_before_km=25.0)) == 88


def test_lists_are_sorted_most_urgent_first():
    facts = {"settlements": {"cut_off_list": [village(name="small", buildings_nearby=3, buildings_hit_nearby=0),
                                              village(name="big", buildings_nearby=900, center_flooded=True),
                                              village(name="middle")], "detour_list": []}}
    ranked = add_priority(facts)["settlements"]["cut_off_list"]
    assert [v["name"] for v in ranked] == ["big", "middle", "small"]
    assert all("priority_parts" in v for v in ranked)
