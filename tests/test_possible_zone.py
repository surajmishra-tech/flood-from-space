"""The "possible damage" zone (floodmap/flood.py possible_zone): grows only from real damage, only to land
that is no higher above the river, and never changes the confirmed map."""
import numpy as np

from floodmap.config import PARAMS
from floodmap.flood import HIGH, MEDIUM, possible_zone

RES = 10.0


def valley(width=60, height=40):
    """A flat valley floor (2 m above the river) between two steep walls (100 m above it)."""
    hand = np.full((height, width), 100.0, "float32")
    hand[:, 15:45] = 2.0
    slope = np.where(hand > 50, 60.0, 3.0).astype("float32")
    return hand, slope


def test_a_real_damage_patch_grows_over_the_flat_valley_floor_but_not_up_the_walls():
    hand, slope = valley()
    conf = np.zeros(hand.shape, "uint8")
    conf[10:30, 20:30] = MEDIUM                       # 2 ha of confirmed debris on the valley floor
    zone = possible_zone(conf, hand, slope, RES)
    assert zone[10:30, 31:42].all()                   # same height, connected, within ~150 m: possible
    assert not zone[:, :15].any() and not zone[:, 45:].any()   # the walls: never
    assert not (zone & (conf >= MEDIUM)).any()        # the confirmed map itself is not part of the extra zone


def test_single_specks_never_grow():
    hand, slope = valley()
    conf = np.zeros(hand.shape, "uint8")
    conf[5, 20] = conf[30, 40] = HIGH                 # two 0.01 ha specks (as on the no-flood date)
    assert not possible_zone(conf, hand, slope, RES).any()
    assert PARAMS.possible_min_seed_ha >= 1.0


def test_land_higher_than_the_confirmed_damage_stays_out():
    hand, slope = valley()
    hand[:, 35:45] = 12.0                             # a terrace 12 m above the river, next to the debris
    conf = np.zeros(hand.shape, "uint8")
    conf[10:30, 20:30] = MEDIUM                       # debris only reached 2 m above the river
    zone = possible_zone(conf, hand, slope, RES)
    assert zone[10:30, 31:34].any()                   # the rest of the low floor: possible
    assert not zone[:, 35:45].any()                   # the higher terrace: not


def test_no_confirmed_damage_means_no_possible_zone():
    hand, slope = valley()
    assert not possible_zone(np.zeros(hand.shape, "uint8"), hand, slope, RES).any()
