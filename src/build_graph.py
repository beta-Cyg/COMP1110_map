"""Step 02 - turn the frozen Overpass snapshot into a directed edge table.

Design decisions that matter downstream:

1. Directed edges, one row per travel direction. `dh` is signed in that
   direction (+ up, - down), so the cost model never has to flip a sign.
2. Stair and escalator ways keep *every* intermediate vertex as a graph node
   (project decision: stair turns matter for geometry and for field checks).
   Ordinary streets are collapsed between junctions to keep the graph small;
   the full polyline is preserved in `geometry_wkt`, so nothing is lost.
3. One physical segment that is walkable both ways becomes two rows linked by
   `rev_edge_id`. A one-way escalator produces a single row.
4. Elevation is *not* attached here. `z_u`/`z_v`/`dh` stay empty until
   attach_elevation.py runs; this keeps the two steps independently testable.

Usage:
    python src/build_graph.py [--snapshot path/to/overpass_*.json]
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import re
import sys
from collections import Counter, defaultdict

import config

EARTH_R = 6371008.8  # mean Earth radius, metres


# --------------------------------------------------------------------------
# Geometry helpers
# --------------------------------------------------------------------------
def haversine(lat1, lon1, lat2, lon2) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_R * math.asin(math.sqrt(a))


def polyline_length(coords: list[tuple[float, float]]) -> float:
    return sum(
        haversine(coords[i][0], coords[i][1], coords[i + 1][0], coords[i + 1][1])
        for i in range(len(coords) - 1)
    )


def to_wkt(coords: list[tuple[float, float]]) -> str:
    """coords are (lat, lon); WKT wants lon lat."""
    return "LINESTRING(" + ", ".join(f"{lon:.7f} {lat:.7f}" for lat, lon in coords) + ")"


# --------------------------------------------------------------------------
# Classification
# --------------------------------------------------------------------------
def classify_way(tags: dict) -> tuple[str, bool]:
    """Return (edge_class, is_pedestrian).

    The class names follow the plan's three buckets, with a fourth bucket for
    carriageways that are only needed to keep the graph connected.
    """
    highway = tags.get("highway", "")
    conveying = tags.get("conveying", "").lower()
    has_conveying = conveying not in ("", "no")

    if highway == "elevator" or tags.get("elevator") == "yes":
        return "lift", True
    if has_conveying and highway == "steps":
        return "escalator", True
    if has_conveying and highway in ("footway", "path", "corridor"):
        return "travelator", True
    if highway == "steps":
        return "stairs", True
    if highway in config.ROAD_HIGHWAYS:
        return "road", False
    if highway in config.PEDESTRIAN_HIGHWAYS:
        return "level", True
    return "level", False


def parsing_step_count(raw: str | None) -> int | None:
    """OSM step_count can be '12', '12;14', 'about 20' - take the digits."""
    if not raw:
        return None
    numbers = [int(n) for n in re.findall(r"\d+", str(raw))]
    if not numbers:
        return None
    # Multi-part values describe separate flights of one stairway: sum them.
    return sum(numbers) if len(numbers) > 1 else numbers[0]


def travel_directions(tags: dict, edge_class: str) -> str:
    """Return 'both', 'forward' or 'backward' (relative to the way's node order).

    OSM's `oneway` applies to vehicles. A pedestrian may walk against a
    one-way street, so it is ignored here unless the map explicitly says
    `oneway:foot=yes`. Moving devices are the real exception: an escalator
    carries you one way only.
    """
    conveying = tags.get("conveying", "").lower()
    if edge_class in ("escalator", "travelator"):
        if conveying == "reversible":
            return "both"
        if conveying == "backward":
            return "backward"
        if conveying == "forward":
            return "forward"
    if edge_class == "lift":
        return "both"
    foot = str(tags.get("oneway:foot", "")).lower()
    if foot in ("yes", "true", "1"):
        return "forward"
    if foot in ("-1", "reverse"):
        return "backward"
    return "both"


# --------------------------------------------------------------------------
# Snapshot loading
# --------------------------------------------------------------------------
def latest_snapshot() -> "object":
    candidates = [
        p for p in config.RAW_DIR.glob("overpass_*.json") if ".meta." not in p.name
    ]
    if not candidates:
        raise SystemExit("No snapshot found. Run src/fetch_osm.py first.")
    return max(candidates, key=lambda p: p.stat().st_mtime)


def load_snapshot(path) -> tuple[dict, list[dict], dict]:
    doc = json.loads(path.read_text(encoding="utf-8"))
    nodes: dict[int, dict] = {}
    ways: dict[int, dict] = {}
    for el in doc.get("elements", []):
        if el["type"] == "node":
            nodes[el["id"]] = el
        elif el["type"] == "way":
            ways[el["id"]] = el  # dict de-duplicates ways matched by two clauses
    meta_path = path.with_suffix(".meta.json")
    meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {}
    return nodes, list(ways.values()), meta


# --------------------------------------------------------------------------
# Segmentation
# --------------------------------------------------------------------------
def count_way_refs(ways: list[dict]) -> Counter:
    refs: Counter = Counter()
    for way in ways:
        for nid in way.get("nodes", []):
            refs[nid] += 1
    return refs


def turn_angle_deg(p0, p1, p2) -> float:
    """Change of direction at p1, in degrees (0 = straight on)."""
    b1 = math.atan2(p1[1] - p0[1], p1[0] - p0[0])
    b2 = math.atan2(p2[1] - p1[1], p2[0] - p1[0])
    d = abs(b2 - b1) % (2 * math.pi)
    return math.degrees(min(d, 2 * math.pi - d))


def segment_way(way: dict, refs: Counter, keep_all: bool,
                coords: dict) -> list[list[int]]:
    """Split a way's node list into segments that become graph edges.

    For stairs and moving devices (`keep_all`) every *geometric* turning
    point is kept as a graph node, plus a hard cap on segment length. Not
    every OSM vertex: a flight mapped with vertices 0.5 m apart would produce
    edges the 5 m terrain model cannot resolve, which is what produced
    1000 % gradients in the first build.
    """
    nds = way.get("nodes", [])
    if len(nds) < 2:
        return []
    segments: list[list[int]] = []
    current = [nds[0]]
    acc = 0.0
    for i in range(1, len(nds)):
        nid = nds[i]
        prev = current[-1]
        if prev in coords and nid in coords:
            acc += haversine(coords[prev][0], coords[prev][1],
                             coords[nid][0], coords[nid][1])
        is_last = i == len(nds) - 1
        is_junction = refs[nid] >= 2
        turning = False
        if keep_all and 0 < i < len(nds) - 1:
            a, b, c = coords.get(nds[i - 1]), coords.get(nid), coords.get(nds[i + 1])
            if a and b and c:
                turning = turn_angle_deg(a, b, c) >= config.STAIR_TURN_DEG
        if is_last or is_junction or (keep_all and (turning or acc >= config.MAX_SEGMENT_M)):
            current.append(nid)
            segments.append(current)
            current = [nid]
            acc = 0.0
        else:
            current.append(nid)
    if len(current) > 1:
        segments.append(current)
    return segments


# --------------------------------------------------------------------------
# Main build
# --------------------------------------------------------------------------
def build(snapshot_path) -> dict:
    nodes, ways, meta = load_snapshot(snapshot_path)
    refs = count_way_refs(ways)

    coords: dict[int, tuple[float, float]] = {
        nid: (el["lat"], el["lon"]) for nid, el in nodes.items() if "lat" in el
    }

    # --- nodes table -------------------------------------------------------
    node_rows: dict[int, dict] = {}

    def touch_node(nid: int, node_type: str):
        if nid not in coords:
            return
        if nid not in node_rows:
            lat, lon = coords[nid]
            node_rows[nid] = {
                "node_id": f"n{nid}",
                "osm_node_id": nid,
                "lon": f"{lon:.7f}",
                "lat": f"{lat:.7f}",
                "node_type": node_type,
                "way_ref_count": refs[nid],
                "z_m": "",
                "z_source": "none",
            }
        else:
            # stair_head / facility_entrance outranks plain vertex
            priority = {"junction": 3, "stair_head": 2, "lift": 2, "vertex": 1}
            if priority.get(node_type, 0) > priority.get(node_rows[nid]["node_type"], 0):
                node_rows[nid]["node_type"] = node_type

    # --- edges -------------------------------------------------------------
    edge_rows: list[dict] = []
    facility_ways: defaultdict[str, list[int]] = defaultdict(list)
    edge_seq = 0

    for way in ways:
        tags = way.get("tags", {})
        edge_class, is_ped = classify_way(tags)
        keep_all = edge_class in ("stairs", "escalator", "travelator", "lift")
        direction = travel_directions(tags, edge_class)

        if direction == "both":
            directions = ("forward", "backward")
        elif direction == "forward":
            directions = ("forward",)
        else:
            directions = ("backward",)

        segments = segment_way(way, refs, keep_all, coords)
        prepared = []
        for seg in segments:
            seg_coords = [coords[n] for n in seg if n in coords]
            if len(seg_coords) >= 2:
                prepared.append((seg, seg_coords, polyline_length(seg_coords)))
        way_len = sum(p[2] for p in prepared)
        # `step_count` in OSM counts the steps of the whole way. A way cut into
        # segments must not hand the full count to every segment - that made a
        # 7 m segment claim 91 steps and a 1.2 cm riser.
        total_steps = parsing_step_count(tags.get("step_count"))

        for seg_index, (seg, seg_coords, length) in enumerate(prepared):
            wkt = to_wkt(seg_coords)
            if total_steps and way_len > 0:
                seg_steps = max(1, int(round(total_steps * length / way_len)))
                steps_source = "osm" if len(prepared) == 1 else "osm_proportional"
            else:
                seg_steps = None
                steps_source = "missing"

            u_osm, v_osm = seg[0], seg[-1]
            if u_osm not in coords or v_osm not in coords:
                continue
            # Register endpoints before the direction loop so that both rows
            # can be emitted; a node shared with another way is a junction.
            touch_node(u_osm, "stair_head" if keep_all else ("junction" if refs[u_osm] >= 2 else "vertex"))
            touch_node(v_osm, "stair_head" if keep_all else ("junction" if refs[v_osm] >= 2 else "vertex"))

            # forward first so that the two rows can reference each other
            ids = {}
            for d in directions:
                edge_seq += 1
                ids[d] = f"w{way['id']}_s{seg_index}_{d[0]}"

            for d in directions:
                if d == "forward":
                    a, b, seg_c = u_osm, v_osm, seg_coords
                else:
                    a, b, seg_c = v_osm, u_osm, list(reversed(seg_coords))
                if a not in node_rows:
                    continue
                row = {
                    "edge_id": ids[d],
                    "rev_edge_id": ids.get("backward" if d == "forward" else "forward", ""),
                    "u": f"n{a}",
                    "v": f"n{b}",
                    "u_lat": f"{coords[a][0]:.7f}",
                    "u_lon": f"{coords[a][1]:.7f}",
                    "v_lat": f"{coords[b][0]:.7f}",
                    "v_lon": f"{coords[b][1]:.7f}",
                    "osm_way_id": way["id"],
                    "name": tags.get("name", ""),
                    "seg_index": seg_index,
                    "edge_class": edge_class,
                    "is_pedestrian": int(is_ped),
                    "direction": direction,
                    "length_m": f"{length:.2f}",
                    "z_u": "",
                    "z_v": "",
                    "dh": "",
                    "abs_dh": "",
                    "gradient": "",
                    "grade_pct": "",
                    "step_count": seg_steps,
                    "step_riser_m": "",
                    "step_count_source": steps_source,
                    "incline": tags.get("incline", ""),
                    "surface": tags.get("surface", ""),
                    "smoothness": tags.get("smoothness", ""),
                    "wheelchair": tags.get("wheelchair", ""),
                    "indoor": tags.get("indoor", ""),
                    "layer": tags.get("layer", ""),
                    "conveying": tags.get("conveying", ""),
                    "facility_id": "",
                    "vertical_source": "none",
                    "horizontal_source": "osm",
                    "site_check": "pending",
                    "note": "",
                    "geometry_wkt": to_wkt(seg_c),
                }
                edge_rows.append(row)

    return {
        "nodes": list(node_rows.values()),
        "edges": edge_rows,
        "meta": meta,
        "way_count": len(ways),
        "snapshot": str(snapshot_path),
    }


# --------------------------------------------------------------------------
# Output
# --------------------------------------------------------------------------
EDGE_FIELDS = [
    "edge_id", "rev_edge_id", "u", "v", "u_lat", "u_lon", "v_lat", "v_lon",
    "osm_way_id", "name", "seg_index", "edge_class", "is_pedestrian", "direction", "length_m",
    "z_u", "z_v", "dh", "abs_dh", "gradient", "grade_pct",
    "step_count", "step_riser_m", "step_count_source",
    "incline", "surface", "smoothness", "wheelchair", "indoor",
    "layer", "conveying",
    "facility_id", "vertical_source", "horizontal_source", "site_check", "note",
    "geometry_wkt",
]

NODE_FIELDS = [
    "node_id", "osm_node_id", "lon", "lat", "node_type", "way_ref_count",
    "z_m", "z_source",
]


def write_csv(path, rows, fields):
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    print(f"  wrote {path.relative_to(config.PROJECT_DIR)}  ({len(rows)} rows)")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--snapshot", help="path to overpass_*.json")
    args = parser.parse_args()

    snapshot = args.snapshot or latest_snapshot()
    print("snapshot:", str(snapshot).split("m2-data")[-1])
    result = build(snapshot)

    write_csv(config.OUT_DIR / "nodes.csv", result["nodes"], NODE_FIELDS)
    write_csv(config.OUT_DIR / "edges.csv", result["edges"], EDGE_FIELDS)

    # --- stats -------------------------------------------------------------
    edges = result["edges"]
    by_class = Counter(e["edge_class"] for e in edges)
    stairs = [e for e in edges if e["edge_class"] == "stairs"]
    esc = [e for e in edges if e["edge_class"] in ("escalator", "travelator")]
    with_count = [e for e in stairs if e["step_count"] is not None]

    print("\n--- summary ---")
    print(f"ways parsed      : {result['way_count']}")
    print(f"graph nodes      : {len(result['nodes'])}")
    print(f"directed edges   : {len(edges)}")
    print(f"total length     : {sum(float(e['length_m']) for e in edges) / 2 / 1000:.1f} km (undirected)")
    print("\nby class:")
    for cls, n in by_class.most_common():
        print(f"  {cls:<12} {n}")
    print("\nstair edges      :", len(stairs))
    print("  with step_count:", len(with_count), f"({100 * len(with_count) / max(len(stairs), 1):.1f}%)")
    print("escalator edges  :", len(esc))
    one_way = [e for e in edges if e["direction"] in ("forward", "backward")]
    print("one-way edges    :", len(one_way))

    stats = {
        "snapshot": str(snapshot),
        "osm_base": result["meta"].get("osm_base"),
        "ways": result["way_count"],
        "nodes": len(result["nodes"]),
        "edges": len(edges),
        "by_class": dict(by_class),
        "stair_edges": len(stairs),
        "stair_with_step_count": len(with_count),
        "one_way_edges": len(one_way),
    }
    (config.OUT_DIR / "build_stats.json").write_text(
        json.dumps(stats, indent=2), encoding="utf-8"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
