"""Question 3: who is cut off?

1. Turn the road map (before the flood) into a network: junctions are nodes,
   road pieces are edges with a length.
2. Remove every road piece the flood covers, and every bridge the flood
   reaches (within 30 m).
3. For each village, find the shortest road route to a "safe destination":
   a town, a hospital, or a road leaving the study area (it leads to the
   rest of the country). Do this before and after the flood.
4. Reachable before but not after  -> CUT OFF.
   Still reachable but much longer  -> DETOUR.
For every cut-off village we also report WHICH broken road / bridge on its
old route caused it.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

import numpy as np
import geopandas as gpd
from scipy.sparse import coo_matrix
from scipy.sparse.csgraph import dijkstra
from scipy.spatial import cKDTree
from shapely.geometry import LineString

from .grid import Grid


@dataclass
class RoadGraph:
    xy: np.ndarray        # (N, 2) node coordinates (UTM metres)
    u: np.ndarray         # (E,) edge start node
    v: np.ndarray         # (E,) edge end node
    length: np.ndarray    # (E,) metres
    way: np.ndarray       # (E,) row in the roads table
    bridge: np.ndarray    # (E,) bool

    @property
    def n(self) -> int:
        return len(self.xy)


def _lines(geom) -> list:
    """All line pieces of a road geometry (closed road loops can arrive as polygons)."""
    if geom is None or geom.is_empty:
        return []
    kind = geom.geom_type
    if kind == "LineString":
        return [geom]
    if kind == "MultiLineString":
        return list(geom.geoms)
    if kind in ("Polygon", "MultiPolygon"):
        return _lines(geom.boundary)
    if kind == "GeometryCollection":
        return [line for part in geom.geoms for line in _lines(part)]
    return []  # points: not part of the network


def build_graph(roads: gpd.GeoDataFrame) -> RoadGraph:
    """Roads must be in a metric CRS (our UTM grid CRS)."""
    index: dict[tuple, int] = {}
    us, vs, ls, ways = [], [], [], []
    is_bridge = roads.get("bridge")
    for i, geom in enumerate(roads.geometry):
        for line in _lines(geom):
            pts = np.asarray(line.coords)[:, :2]
            ids = [index.setdefault((round(x, 1), round(y, 1)), len(index)) for x, y in pts]
            seg = np.hypot(*(pts[1:] - pts[:-1]).T)
            for a, b, length in zip(ids[:-1], ids[1:], seg):
                if a != b:
                    us.append(a)
                    vs.append(b)
                    ls.append(length)
                    ways.append(i)
    xy = np.array(list(index.keys()), dtype="float64").reshape(-1, 2)
    ways = np.array(ways, dtype="int64")
    bridge = np.zeros(len(ways), bool)
    if is_bridge is not None and len(ways):
        bvals = (roads["bridge"].astype(str).str.lower().isin(["yes", "viaduct", "true", "1"])).to_numpy()
        bridge = bvals[ways]
    return RoadGraph(xy, np.array(us, "int64"), np.array(vs, "int64"), np.array(ls, "float64"), ways, bridge)


def edge_flooding(g: RoadGraph, dist: np.ndarray, grid: Grid, bridge_hit_m: float):
    """Which edges does the flood cut? Returns (cut, flooded_m, inside_grid)."""
    E = len(g.u)
    if E == 0:
        return np.zeros(0, bool), np.zeros(0), np.zeros(0, bool)
    step = grid.res / 2.0
    n = np.maximum(2, np.ceil(g.length / step).astype("int64") + 1)
    starts = np.concatenate([[0], np.cumsum(n)[:-1]])
    eid = np.repeat(np.arange(E), n)
    t = (np.arange(n.sum()) - np.repeat(starts, n)) / np.repeat(n - 1, n)
    p, q = g.xy[g.u][eid], g.xy[g.v][eid]
    x = p[:, 0] + t * (q[:, 0] - p[:, 0])
    y = p[:, 1] + t * (q[:, 1] - p[:, 1])
    r, c = grid.rowcol(x, y)
    ok = grid.inside(r, c)
    d = np.full(len(x), np.inf)
    d[ok] = dist[r[ok], c[ok]]
    wet = d <= grid.res  # on or right next to a flooded pixel (allows ~1 pixel position error)
    frac = np.add.reduceat(wet.astype("float64"), starts) / n
    dmin = np.minimum.reduceat(d, starts)
    inside = np.maximum.reduceat(ok.astype("int8"), starts) > 0
    cut = np.where(g.bridge, dmin <= bridge_hit_m, frac > 0)
    return cut, frac * g.length, inside


def _matrix(g: RoadGraph, keep: np.ndarray):
    a, b = np.minimum(g.u, g.v)[keep], np.maximum(g.u, g.v)[keep]
    w = g.length[keep]
    order = np.lexsort((w, b, a))  # parallel edges: keep the shortest
    a, b, w = a[order], b[order], w[order]
    first = np.ones(len(a), bool)
    first[1:] = (a[1:] != a[:-1]) | (b[1:] != b[:-1])
    return coo_matrix((np.maximum(w[first], 1e-3), (a[first], b[first])), shape=(g.n, g.n)).tocsr()


def shortest(g: RoadGraph, keep: np.ndarray, sources: np.ndarray):
    if len(sources) == 0:
        inf = np.full(g.n, np.inf)
        return inf, np.full(g.n, -9999), np.full(g.n, -9999)
    dist, pred, src = dijkstra(_matrix(g, keep), directed=False, indices=np.unique(sources),
                               min_only=True, return_predecessors=True)
    return dist, pred, src


def snap(g: RoadGraph, xy: np.ndarray, max_m: float, allowed: np.ndarray | None = None) -> np.ndarray:
    """Nearest graph node (optionally among `allowed` nodes) within max_m, else -1."""
    nodes = np.arange(g.n) if allowed is None else np.flatnonzero(allowed)
    if len(nodes) == 0 or len(xy) == 0:
        return np.full(len(xy), -1)
    d, i = cKDTree(g.xy[nodes]).query(xy, distance_upper_bound=max_m)
    out = np.full(len(xy), -1)
    found = np.isfinite(d)
    out[found] = nodes[i[found]]
    return out


def boundary_exits(g: RoadGraph, ext_bounds, tol_m: float = 300.0) -> np.ndarray:
    """Dead-end nodes at the edge of the loaded road area: roads that continue beyond it."""
    deg = np.bincount(np.concatenate([g.u, g.v]), minlength=g.n)
    left, bottom, right, top = ext_bounds
    x, y = g.xy[:, 0], g.xy[:, 1]
    near = (x - left < tol_m) | (right - x < tol_m) | (y - bottom < tol_m) | (top - y < tol_m)
    return np.flatnonzero((deg == 1) & near)


def path_edges(g: RoadGraph, pred: np.ndarray, node: int) -> list[tuple[int, int]]:
    out, cur, guard = [], node, 0
    while pred[cur] >= 0 and guard < 200_000:
        out.append((pred[cur], cur))
        cur = pred[cur]
        guard += 1
    return out


def _clean(v) -> str:
    """Tag value as text ("" for missing). Nepali names are spelled in Latin letters."""
    from .names import romanize
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return ""
    t = str(v).strip()
    return "" if t.lower() in ("nan", "none") else romanize(t)


def find_breaks(g: RoadGraph, roads: gpd.GeoDataFrame, cut: np.ndarray, pred: np.ndarray,
                node: int, lookup: dict) -> list[dict]:
    """Broken stretches on a village's old (pre-flood) route, nearest first."""
    breaks, current = [], None
    for a, b in path_edges(g, pred, node):
        e = lookup.get((min(a, b), max(a, b)))
        if e is not None and cut[e]:
            if current is None:
                current = {"edges": [], "bridge": False}
            current["edges"].append(e)
            current["bridge"] |= bool(g.bridge[e])
        elif current is not None:
            breaks.append(current)
            current = None
    if current is not None:
        breaks.append(current)
    out = []
    for br in breaks:
        es = br["edges"]
        mid = es[len(es) // 2]
        names = Counter(_clean(roads.iloc[g.way[e]].get("name")) for e in es).most_common(1)[0][0]
        hw = Counter(_clean(roads.iloc[g.way[e]].get("highway")) for e in es).most_common(1)[0][0]
        out.append({"x": float((g.xy[g.u[mid], 0] + g.xy[g.v[mid], 0]) / 2),
                    "y": float((g.xy[g.u[mid], 1] + g.xy[g.v[mid], 1]) / 2),
                    "length_m": round(float(g.length[es].sum()), 0), "bridge": br["bridge"],
                    "road_name": names, "road_type": hw})
    return out


def _direction(dx: float, dy: float) -> str:
    ang = (np.degrees(np.arctan2(dx, dy)) + 360) % 360
    return ("north", "north-east", "east", "south-east", "south", "south-west", "west", "north-west")[int((ang + 22.5) // 45) % 8]


def analyse(g: RoadGraph, roads: gpd.GeoDataFrame, cut: np.ndarray, settlements: list[dict],
            targets: list[dict], ext_bounds, center_xy, params) -> list[dict]:
    """Road access of every settlement before and after the flood.

    settlements: dicts with "x", "y" (+ any other info, passed through)
    targets: dicts with "x", "y", "label", "usable_after" (False e.g. for a flooded hospital)
    """
    labels: dict[int, list[str]] = {}
    t_xy = np.array([[t["x"], t["y"]] for t in targets]).reshape(-1, 2)
    t_nodes = snap(g, t_xy, params.snap_target_m)
    before_src, after_src = [], []
    for t, node in zip(targets, t_nodes):
        if node < 0:
            continue
        labels.setdefault(int(node), []).append(t["label"])
        before_src.append(node)
        if t.get("usable_after", True):
            after_src.append(node)
    for node in boundary_exits(g, ext_bounds):
        dx, dy = g.xy[node, 0] - center_xy[0], g.xy[node, 1] - center_xy[1]
        labels.setdefault(int(node), []).append(f"road out of the area ({_direction(dx, dy)})")
        before_src.append(node)
        after_src.append(node)
    keep_all = np.ones(len(g.u), bool)
    dist_b, pred_b, src_b = shortest(g, keep_all, np.array(before_src, "int64"))
    dist_a, _pred_a, src_a = shortest(g, ~cut, np.array(after_src, "int64"))

    lookup: dict[tuple, int] = {}
    for e in np.argsort(g.length)[::-1]:  # shortest edge wins for parallel edges
        lookup[(int(min(g.u[e], g.v[e])), int(max(g.u[e], g.v[e])))] = int(e)

    s_xy = np.array([[s["x"], s["y"]] for s in settlements]).reshape(-1, 2)
    s_nodes = snap(g, s_xy, params.snap_settlement_m, allowed=np.isfinite(dist_b))
    out = []
    for i, (s, node) in enumerate(zip(settlements, s_nodes)):
        r = dict(s)
        if node < 0:
            r.update(status="no_road", dist_before_km=None, dist_after_km=None, reach_before=None,
                     reach_after=None, breaks=[])
            out.append(r)
            continue
        db, da = dist_b[node] / 1000.0, dist_a[node] / 1000.0
        r["road_km_from_center"] = round(float(np.hypot(*(g.xy[node] - s_xy[i]))) / 1000.0, 2)
        r["dist_before_km"] = round(db, 1)
        r["reach_before"] = " / ".join(labels.get(int(src_b[node]), ["?"]))
        if not np.isfinite(da):
            r.update(status="cut_off", dist_after_km=None, reach_after=None,
                     breaks=find_breaks(g, roads, cut, pred_b, int(node), lookup))
        else:
            r["dist_after_km"] = round(da, 1)
            r["reach_after"] = " / ".join(labels.get(int(src_a[node]), ["?"]))
            detour = da > db * params.detour_ratio and (da - db) > params.detour_min_km
            r.update(status="detour" if detour else "ok", breaks=[])
        out.append(r)
    return out


def cut_lines(g: RoadGraph, cut: np.ndarray, crs) -> gpd.GeoDataFrame:
    es = np.flatnonzero(cut)
    geoms = [LineString([g.xy[g.u[e]], g.xy[g.v[e]]]) for e in es]
    return gpd.GeoDataFrame({"bridge": g.bridge[es], "way": g.way[es]}, geometry=geoms, crs=crs)
