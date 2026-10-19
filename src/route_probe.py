"""Sanity check: route between two points and report what the network says.

This is not the project's routing experiment - that is M4's job. It exists so
that the data can be validated against the two numbers we already know:
the corridor rises roughly 100 m, and the Centre Street Link itself accounts
for about 27 m of it.

Two criteria are used, mirroring the plan's B1 and B2:
  distance  - what a length-only router does
  ascent    - what a cumulative-ascent router does

Usage:
    python src/route_probe.py --from 22.2872,114.1405 --to 22.2830,114.1370
"""

from __future__ import annotations

import argparse
import csv
import heapq
import math
import sys
from collections import defaultdict

sys.path.insert(0, "src")
import config

EARTH_R = 6371008.8


def haversine(lat1, lon1, lat2, lon2) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_R * math.asin(math.sqrt(a))


def load():
    with open(config.OUT_DIR / "edges.csv", newline="", encoding="utf-8") as fh:
        edges = list(csv.DictReader(fh))
    xy = {}
    graph = defaultdict(list)
    for e in edges:
        u, v = e["u"], e["v"]
        xy.setdefault(u, (float(e["u_lat"]), float(e["u_lon"])))
        xy.setdefault(v, (float(e["v_lat"]), float(e["v_lon"])))
        graph[u].append(e)
    return edges, graph, xy


def nearest(xy: dict, lat: float, lon: float) -> str:
    best, bestd = None, 1e18
    for nid, (nlat, nlon) in xy.items():
        d = haversine(lat, lon, nlat, nlon)
        if d < bestd:
            best, bestd = nid, d
    return best


def dijkstra(graph, src, dst, weight_fn):
    dist = {src: 0.0}
    prev = {}
    pq = [(0.0, src)]
    seen = set()
    while pq:
        d, u = heapq.heappop(pq)
        if u in seen:
            continue
        seen.add(u)
        if u == dst:
            break
        for e in graph.get(u, ()):
            w = weight_fn(e)
            if w is None:
                continue
            nd = d + w
            if nd < dist.get(e["v"], 1e18):
                dist[e["v"]] = nd
                prev[e["v"]] = (u, e)
                heapq.heappush(pq, (nd, e["v"]))
    if dst not in dist:
        return None
    path, cur = [], dst
    while cur != src:
        u, e = prev[cur]
        path.append(e)
        cur = u
    return list(reversed(path))


def summarise(path, label):
    if not path:
        print(f"{label:<10} no route")
        return
    length = sum(float(e["length_m"]) for e in path)
    dh_pos = sum(max(0.0, float(e["dh"] or 0)) for e in path)
    dh_neg = sum(min(0.0, float(e["dh"] or 0)) for e in path)
    by_class = defaultdict(int)
    for e in path:
        by_class[e["edge_class"]] += 1
    print(f"{label:<10} length={length:7.0f} m  ascent={dh_pos:6.1f} m  "
          f"descent={dh_neg:6.1f} m  edges={len(path):<3} "
          f"classes={dict(by_class)}")
    return {"length": length, "ascent": dh_pos, "descent": dh_neg,
            "classes": dict(by_class), "path": path}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="src", default="22.2872,114.1405")
    ap.add_argument("--to", dest="dst", default="22.2830,114.1370")
    args = ap.parse_args()

    slat, slon = [float(x) for x in args.src.split(",")]
    dlat, dlon = [float(x) for x in args.dst.split(",")]

    edges, graph, xy = load()
    s = nearest(xy, slat, slon)
    t = nearest(xy, dlat, dlon)
    print(f"edges={len(edges)} nodes={len(xy)}")
    print(f"start node {s} at {xy[s]}   end node {t} at {xy[t]}")
    print(f"straight-line distance: {haversine(slat, slon, dlat, dlon):.0f} m\n")

    r1 = summarise(dijkstra(graph, s, t, lambda e: float(e["length_m"])), "distance")
    r2 = summarise(
        dijkstra(graph, s, t,
                 lambda e: max(0.0, float(e["dh"] or 0)) + 0.001 * float(e["length_m"])),
        "ascent")

    if r1 and r2:
        a = {e["edge_id"] for e in r1["path"]}
        b = {e["edge_id"] for e in r2["path"]}
        shared = len(a & b)
        print(f"\noverlap: {shared}/{max(len(a), len(b))} edges in common "
              f"({100 * shared / max(len(a), len(b)):.0f} %)")
        print(f"ascent difference: {r2['ascent'] - r1['ascent']:+.1f} m")

    # Centre Street escalator contribution on the ascent route
    if r2:
        esc = [e for e in r2["path"] if e["edge_class"] in ("escalator", "travelator")]
        if esc:
            print(f"\nCentre Street / escalator edges on ascent route: {len(esc)}")
            print(f"  total escalator length: {sum(float(e['length_m']) for e in esc):.0f} m")
            print(f"  total rise carried    : {sum(float(e['dh']) for e in esc):+.1f} m")
    return 0


if __name__ == "__main__":
    sys.exit(main())
