"""Step 04 - facilities and operating windows.

A facility is the thing a traveller perceives ("the Centre Street escalator"),
not the thing OSM stores (twenty 4 m conveying ways). Aggregating ways into
facilities is what lets the cost model charge one waiting cost for one ride.

The direction and rise of the Centre Street Link come from the District
Council paper, not from OSM: uphill all 85 m are assisted, downhill only
about 25 m. That asymmetry is the project's central structural fact.

Usage:
    python src/build_facilities.py
"""

from __future__ import annotations

import csv
import sys
from collections import defaultdict

sys.path.insert(0, "src")
import config

FIELDS = [
    "facility_id", "name", "class", "total_length_m", "total_rise_m",
    "direction", "direction_source", "source_doc", "osm_way_count", "note",
]


def build() -> list[dict]:
    with open(config.OUT_DIR / "edges.csv", newline="", encoding="utf-8") as fh:
        edges = list(csv.DictReader(fh))

    rows: list[dict] = []
    for spec in config.FACILITY_OVERRIDES:
        s, w, n, e_ = spec["match_bbox"]
        members = [
            ed for ed in edges
            if ed["facility_id"] == spec["facility_id"]
        ]
        ways = {ed["osm_way_id"] for ed in members}
        # Count each physical segment once, and only in the uphill direction.
        seen = set()
        length = rise = 0.0
        down_belt = 0.0
        for ed in members:
            if ed["edge_id"] in seen:
                continue
            rev = ed["rev_edge_id"]
            if rev:
                seen.add(rev)
            seen.add(ed["edge_id"])
            length += float(ed["length_m"])
            rise += max(0.0, float(ed["dh"] or 0))
            # A paired up/down flight is two OSM ways for one physical flight;
            # the descending belt must not count towards the link's length.
            if (ed.get("incline") or "").startswith("-"):
                down_belt += float(ed["length_m"])
        if members:
            total_len = f"{length:.1f}"
            total_rise = f"{rise:.1f}"
            note = spec["direction_note"]
            documented = spec["total_length_m"]
            if documented:
                note += (f"; OSM geometry totals {length - down_belt:.0f} m with "
                         f"one belt per paired flight, against the documented "
                         f"{documented:.0f} m - to be checked on site")
        else:
            total_len = "" if spec["total_length_m"] is None else f"{spec['total_length_m']}"
            total_rise = "" if spec["total_rise_m"] is None else f"{spec['total_rise_m']}"
            note = spec["direction_note"]
        rows.append({
            "facility_id": spec["facility_id"],
            "name": spec["name"],
            "class": spec["class"],
            "total_length_m": total_len,
            "total_rise_m": total_rise,
            "direction": spec["direction"],
            "direction_source": "document",
            "source_doc": spec["source_doc"],
            "osm_way_count": len(ways),
            "note": note,
        })

    # Lifts at the MTR station and the East Gate are counted, not modelled.
    lift_edges = [ed for ed in edges if ed["edge_class"] == "lift"]
    if lift_edges:
        rows.append({
            "facility_id": "F_LIFTS_GENERIC",
            "name": "Lift segments inside the bbox",
            "class": "lift",
            "total_length_m": f"{sum(float(x['length_m']) for x in lift_edges) / 2:.1f}",
            "total_rise_m": "",
            "direction": "both",
            "direction_source": "osm",
            "source_doc": "OSM highway=elevator",
            "osm_way_count": len({x["osm_way_id"] for x in lift_edges}),
            "note": "vertical rise not modelled; treat as a level change",
        })
    return rows


def write_windows() -> None:
    fields = ["facility_id", "scenario", "day_of_week", "start", "end", "status", "source"]
    path = config.OUT_DIR / "windows.csv"
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(fields)
        for row in config.WINDOWS:
            writer.writerow(["" if v is None else v for v in row])
    print(f"  wrote {path.relative_to(config.PROJECT_DIR)}")


def main() -> int:
    rows = build()
    path = config.OUT_DIR / "facilities.csv"
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    print(f"  wrote {path.relative_to(config.PROJECT_DIR)}  ({len(rows)} rows)")
    for r in rows:
        print(f"    {r['facility_id']:<18} {r['class']:<22} "
              f"len={r['total_length_m']:>7} rise={r['total_rise_m']:>6} "
              f"({r['osm_way_count']} ways)")
    write_windows()
    return 0


if __name__ == "__main__":
    sys.exit(main())
