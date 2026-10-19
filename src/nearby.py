"""List named / transport features around a point, to identify an edge on the ground.

Usage:
    python nearby.py 22.28450 114.13470 [radius_m]
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

RAW = Path(__file__).resolve().parents[1] / "data" / "raw"
_C = [p for p in RAW.glob("overpass_*.json") if not p.name.endswith(".meta.json")]
PAYLOAD = sorted(_C)[-1]


def hav(a, b):
    la1, lo1, la2, lo2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    d = (math.sin((la2 - la1) / 2) ** 2
         + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2)
    return 2 * 6371008.8 * math.asin(math.sqrt(d))


def main(argv):
    lat, lon = float(argv[0]), float(argv[1])
    rad = float(argv[2]) if len(argv) > 2 else 250.0
    els = json.loads(PAYLOAD.read_text(encoding="utf-8"))["elements"]
    node_pos = {e["id"]: (e["lat"], e["lon"]) for e in els if e["type"] == "node"}

    rows = []
    for e in els:
        t = e["tags"] = e.get("tags", {})
        if not t:
            continue
        if e["type"] == "node" and "lat" in e:
            p = (e["lat"], e["lon"])
        elif e["type"] == "way" and e.get("nodes"):
            pts = [node_pos[n] for n in e["nodes"] if n in node_pos]
            if not pts:
                continue
            p = (sum(q[0] for q in pts) / len(pts), sum(q[1] for q in pts) / len(pts))
        else:
            continue
        d = hav((lat, lon), p)
        if d <= rad:
            rows.append((d, e["type"], e["id"], t, p))

    rows.sort()
    for d, typ, i, t, p in rows:
        keep = {k: v for k, v in t.items() if k in (
            "name", "name:en", "name:zh", "railway", "public_transport", "station",
            "subway", "highway", "building", "building:levels", "level", "indoor",
            "conveying", "incline", "tunnel", "layer", "amenity", "shop", "office",
            "entrance", "ref", "access", "wheelchair", "elevator", "description")}
        if not keep:
            continue
        tag = " ".join(f"{k}={v}" for k, v in sorted(keep.items()))
        print(f"{d:7.1f} m  {typ[:3]}:{i:<12} ({p[0]:.6f},{p[1]:.6f})  {tag}")


if __name__ == "__main__":
    main(sys.argv[1:])
