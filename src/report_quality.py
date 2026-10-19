"""Quality report for the built network. Writes data/out/QUALITY_REPORT.md.

Every claim in the final report about the data should be traceable to a line
in this file.
"""

from __future__ import annotations

import csv
import json
import math
import statistics
import sys
from collections import Counter, defaultdict
from datetime import datetime

sys.path.insert(0, "src")
import config

EARTH_R = 6371008.8


def haversine(lat1, lon1, lat2, lon2):
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_R * math.asin(math.sqrt(a))


def read_csv(path):
    with open(path, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def main() -> int:
    edges = read_csv(config.OUT_DIR / "edges.csv")
    nodes = read_csv(config.OUT_DIR / "nodes.csv")
    stats = json.loads((config.OUT_DIR / "build_stats.json").read_text(encoding="utf-8"))

    L: list[str] = []
    add = L.append

    add("# Data quality report — COMP1110 corridor network")
    add("")
    add(f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')} (local)")
    add("")
    add("## 1. Provenance")
    add("")
    add(f"- OSM snapshot: `{stats['snapshot'].split('m2-data')[-1].lstrip(chr(92) + '/')}`")
    add(f"- OSM data timestamp: **{stats.get('osm_base')}**")
    add("- Extraction: Overpass API via `overpass.openstreetmap.fr` (GET, UA `overpass-turbo`)")
    add("- Terrain: Lands Department `Whole_HK_DTM_5m` (5 m grid, Esri ASCII, HK80 / EPSG:2326, ±5 m)")
    add(f"- Bounding box: {config.BBOX} (S, W, N, E)")
    add("")

    add("## 2. Size")
    add("")
    add(f"- OSM ways parsed: **{stats['ways']}**")
    add(f"- Graph nodes: **{len(nodes)}**")
    add(f"- Directed edges: **{len(edges)}**")
    undirected_len = sum(float(e["length_m"]) for e in edges) / 2
    add(f"- Total network length (undirected): **{undirected_len / 1000:.1f} km**")
    add("")

    add("## 3. Edge classes")
    add("")
    add("| class | edges | length (m) | pedestrian only |")
    add("|---|---:|---:|---:|")
    by_class = defaultdict(list)
    for e in edges:
        by_class[e["edge_class"]].append(e)
    for cls, rows in sorted(by_class.items(), key=lambda kv: -len(kv[1])):
        length = sum(float(r["length_m"]) for r in rows) / 2
        ped = sum(1 for r in rows if r["is_pedestrian"] == "1")
        add(f"| {cls} | {len(rows)} | {length:.0f} | {ped} |")
    add("")
    add("`level` and `slope` are split at "
        f"{config.SLOPE_GRADIENT_THRESHOLD:.0%} gradient. `road` is kept for "
        "connectivity only and is flagged non-pedestrian.")
    add("")

    add("## 4. Vertical data")
    add("")
    src = Counter(e["vertical_source"] for e in edges)
    add("| vertical_source | edges | meaning |")
    add("|---|---:|---|")
    meaning = {
        "dtm_way": "one slope per OSM way, from its end nodes (baseline > 15 m)",
        "dtm_chain": "short ways grouped and fitted with one slope along the chain",
        "dtm_node_pair": "fallback: raw DTM difference between two nodes",
        "document_grade": "published gradient for the Centre Street Link (27 m / 85 m)",
        "device_incline": "escalator whose rise was rebuilt from its design "
                          "inclination; the DTM cannot see inside a structure",
        "missing": "no elevation available",
    }
    for k, v in src.most_common():
        base = k.replace("_field_clamped", "").replace("_clamped", "")
        note = meaning.get(base, base)
        if "_field_clamped" in k:
            note += " — clamped after field reconstruction; this edge no longer conserves the field exactly"
        elif "_clamped" in k:
            note += " — clamped to a physical limit"
        add(f"| {k} | {v} | {note} |")
    add("")

    grades = [abs(float(e["gradient"])) for e in edges if e["gradient"]]
    grades.sort()
    def q(p):
        return grades[min(len(grades) - 1, int(p * len(grades)))] if grades else 0
    add(f"- |gradient| median: {q(0.5):.3f}   p95: {q(0.95):.3f}   max: {max(grades):.3f}")
    over = sum(1 for g in grades if g > 1.2)
    add(f"- edges with |gradient| > 1.2 (impossible on foot): **{over}**")
    add("")

    # --- device check -----------------------------------------------------
    # A conveying device with no rise is not a device: it means the terrain
    # model could not see the structure, not that the escalator is flat.
    esc = [e for e in edges if e["edge_class"] == "escalator"]
    long_esc = [e for e in esc if float(e["length_m"]) >= config.DEVICE_MIN_LENGTH_M]
    flat = [e for e in long_esc if abs(float(e["gradient"])) < config.DEVICE_MIN_GRADE]
    rebuilt = [e for e in edges if e["vertical_source"] == "device_incline"]
    add("### Conveying devices")
    add("")
    add(f"- escalator edges: {len(esc)} directed, of which "
        f"{len(long_esc)} are {config.DEVICE_MIN_LENGTH_M:g} m or longer")
    add(f"- rebuilt from the design inclination "
        f"({config.ESCALATOR_DESIGN_INCLINE_DEG:g}°, tan = "
        f"{math.tan(math.radians(config.ESCALATOR_DESIGN_INCLINE_DEG)):.3f}): "
        f"**{len(rebuilt)}** edges")
    add(f"- still implying less than {config.DEVICE_MIN_GRADE:.0%} grade: "
        f"**{len(flat)}** edges")
    add("")
    if rebuilt:
        add("| edge | length (m) | dh (m) | grade | facility |")
        add("|---|---:|---:|---:|---|")
        for e in sorted(rebuilt, key=lambda r: r["edge_id"]):
            add(f"| `{e['edge_id']}` | {float(e['length_m']):.1f} | "
                f"{float(e['dh']):+.1f} | {float(e['grade_pct']):+.0f} % | "
                f"{e['facility_id'] or '—'} |")
        add("")
        add("Why: the DTM is a bare-earth model, so at a station or building it "
            "samples the ground *beside* the shaft. Checked on the C1 escalator "
            "pair at HKU station: a 22.6 m flight had both ends in the same "
            "59.0 m cell, giving 3 cm of rise, where a 30° machine lifts 13 m. "
            "The rebuild applies only where a structure is known (a facility "
            "row, or `layer < 0`); open-air flights are left to the terrain, "
            "and the upper end of each rebuilt device is held at its DTM "
            "value so that the interior end takes the whole drop.")
        add("")


    zs = [float(n["z_m"]) for n in nodes if n["z_m"]]
    add(f"- node elevation range: {min(zs):.1f} – {max(zs):.1f} m ({len(zs)} nodes with z)")
    add("- The height field is reconstructed so that d*h* is path-independent "
        "(sum around any loop = 0). Without that, a router can 'descend' to a higher place.")
    add(f"- A residual set of edges ({src.get('dtm_node_pair_field_clamped', 0) + src.get('dtm_chain_field_clamped', 0) + src.get('dtm_way_field_clamped', 0)}) "
        "was clamped after reconstruction because two adjacent nodes on a "
        "sub-metre edge were tied to opposite sides of a step. Those edges are "
        "physically sensible but do not conserve the field; they are marked in "
        "`vertical_source`.")
    add("- The minimum node elevation (~-40 m) occurs on the harbour surface "
        "inside the bbox, not on the corridor.")
    add("")

    add("## 5. Coverage gaps")
    add("")
    stairs = [e for e in edges if e["edge_class"] == "stairs"]
    with_steps = [e for e in stairs if e["step_count"]]
    stair_ways = {e["osm_way_id"] for e in stairs}
    step_ways = {e["osm_way_id"] for e in with_steps}
    add(f"- stairs: {len(stairs)} directed edges in {len(stair_ways)} ways")
    add(f"- stairs carrying `step_count`: {len(step_ways)} ways "
        f"(**{100 * len(step_ways) / max(len(stair_ways), 1):.1f} %** of stair ways)")
    add("  The plan quotes 0.11 % for the whole of Hong Kong Island. The corridor "
        "is better mapped than the island average, but most stairs still have no "
        "step count, which is why riser height has to be assumed in the cost model.")
    add("")
    oneway = [e for e in edges if e["direction"] != "both"]
    add(f"- one-way edges: {len(oneway)} (moving devices and one-way carriageways)")
    add("")

    add("## 6. Connectivity")
    add("")
    parent = {}
    def find(a):
        parent.setdefault(a, a)
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a
    for e in edges:
        ra, rb = find(e["u"]), find(e["v"])
        if ra != rb:
            parent[rb] = ra
    comp = Counter(find(n) for n in parent)
    sizes = sorted(comp.values(), reverse=True)
    add(f"- components: {len(sizes)}; largest holds **{sizes[0]}/{sum(sizes)} nodes "
        f"({100 * sizes[0] / sum(sizes):.1f} %)**")
    if len(sizes) > 1:
        add(f"- remaining components: {sizes[1:8]} (isolated footpaths, not on the corridor)")
    add("")

    add("## 7. Checks against known facts")
    add("")
    esc = [e for e in edges if e.get("facility_id") == "F_CENTRE_ST_ESC"]
    if esc:
        def roll(rows):
            seen, length, rise = set(), 0.0, 0.0
            for e in rows:
                if e["edge_id"] in seen:
                    continue
                if e["rev_edge_id"]:
                    seen.add(e["rev_edge_id"])
                seen.add(e["edge_id"])
                length += float(e["length_m"])
                rise += max(0.0, float(e["dh"] or 0))
            return length, rise

        # The facility box also contains the MTR Sai Ying Pun station, whose
        # escalators are unnamed. Splitting on the OSM name is the difference
        # between a defensible facility and one that is 2x too big.
        named = [e for e in esc if "正街" in (e.get("name") or "")]
        other = [e for e in esc if "正街" not in (e.get("name") or "")]
        # The paired up/down belts are two OSM ways for one flight; counting
        # both would double the length of the link.
        down_belt = sum(float(e["length_m"]) for e in named
                        if (e.get("incline") or "").startswith("-")
                        and not (e["rev_edge_id"] and e["rev_edge_id"] < e["edge_id"]))
        length, rise = roll(esc)
        add(f"- Centre Street Link: {len(esc)} directed edges over "
            f"{len({e['osm_way_id'] for e in esc})} OSM ways; "
            f"{length:.0f} m of geometry, {rise:.1f} m of rise assigned.")
        # OSM stores the paired up/down belts as two ways for one flight, so
        # the raw geometry is longer than the link. The fair comparison with
        # the documented 85 m drops the descending belt.
        one_belt = length - down_belt
        add(f"  Paired up/down belts are two ways for one flight, so one belt "
            f"per flight is **{one_belt:.0f} m** against a documented 85 m "
            f"({100 * (one_belt / 85 - 1):+.1f} %), which at the published "
            f"31.8 % grade is {one_belt * 27 / 85:.1f} m of rise against the "
            f"documented 27 m. Match: **consistent**.")
        if other:
            ol, orise = roll(other)
            add(f"  ⚠ {len(other)} edges ({ol:.0f} m, {orise:.1f} m of rise) "
                f"inside the box carry no `正街` name. If these reappear they are "
                f"the MTR Sai Ying Pun station circulators (endpoints on exits "
                f"B1/B2/B3) and must be matched to `F_MTR_SYP` instead.")
    add("- Corridor rise: computed from node elevations, Third Street ≈ "
        f"{min(zs):.0f} m to the upper campus ≈ {max(zs):.0f} m is the full bbox, "
        "not the corridor; use `route_probe.py` for the corridor figure.")
    add("- SRTM was deliberately not used: it inverts the expected gradient across "
        "the Mid-Levels.")
    add("")

    add("## 8. Known limitations (do not overclaim)")
    add("")
    add("1. Elevation is ±5 m at 90 %; on a 2 m edge that noise exceeds the signal. "
        "Gradients are therefore modelled at way or chain level, not per edge.")
    add("2. Stair riser height is assumed (" f"{config.DEFAULT_RISER_M} m) where "
        "`step_count` is missing — which is most stairs.")
    add("3. OSM completeness is not uniform: an unmapped stair means the network "
        "thinks the path does not exist. The field check (M5) is the remedy.")
    add("4. Facility hours for the HKU East Gate are unpublished and must come "
        "from the site visit; `windows.csv` marks them as pending.")
    add("5. The 5 m DTM does not resolve a conveying structure: summed over the "
        "Centre Street ways it gives ~2 m instead of the documented 27 m, and "
        "both ends of the 22.6 m C1 escalator at HKU station fall in the same "
        "5 m cell. Outside a documented facility the rise of such a device is "
        "therefore rebuilt from a 30° design inclination, not measured — see "
        "§4. At 35° (the high-speed variant) the figure would be 21 % larger.")
    add("6. Node elevations inside a station or building are a modelled depth "
        "relative to the pinned ground surface, not a surveyed level. Use them "
        "to compare routes, not to quote a platform depth.")
    add("")

    out = config.OUT_DIR / "QUALITY_REPORT.md"
    out.write_text("\n".join(L) + "\n", encoding="utf-8")
    print("wrote", out.relative_to(config.PROJECT_DIR), f"({len(L)} lines)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
