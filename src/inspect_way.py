"""Inspect one OSM way's raw tags + any relation that references it.

Usage:
    python inspect_way.py 407891119 [407891117 407891120 ...]

Purely diagnostic: reads the frozen Overpass payload, prints tags of the way,
its nodes, and every relation whose members include it.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

RAW = Path(__file__).resolve().parents[1] / "data" / "raw"
_CANDIDATES = [p for p in RAW.glob("overpass_*.json") if not p.name.endswith(".meta.json")]
DEFAULT = sorted(_CANDIDATES)[-1] if _CANDIDATES else None


def load(path: Path):
    payload = json.loads(path.read_text(encoding="utf-8"))
    elements = payload.get("elements", payload)
    return elements


def by_id(elements):
    ways, nodes, rels = {}, {}, {}
    for el in elements:
        t, i = el.get("type"), el.get("id")
        if t == "way":
            ways[i] = el
        elif t == "node":
            nodes[i] = el
        elif t == "relation":
            rels[i] = el
    return ways, nodes, rels


def main(argv):
    path = DEFAULT
    if "--file" in argv:
        k = argv.index("--file")
        path = Path(argv[k + 1])
        argv = argv[:k] + argv[k + 2:]
    targets = [int(a) for a in argv if a.isdigit()]
    elements = load(path)
    ways, nodes, rels = by_id(elements)
    print(f"payload: {path.name}  ways={len(ways)} nodes={len(nodes)} relations={len(rels)}")

    for wid in targets:
        w = ways.get(wid)
        print("\n" + "=" * 78)
        if not w:
            print(f"way {wid}: NOT IN PAYLOAD")
            continue
        print(f"way {wid}  ({len(w.get('nodes', []))} nodes)")
        for k, v in sorted(w.get("tags", {}).items()):
            print(f"    {k:24s} = {v}")
        # geometry span
        pts = [(nodes[n]["lat"], nodes[n]["lon"]) for n in w.get("nodes", []) if n in nodes]
        if len(pts) >= 2:
            import math
            def hav(a, b):
                la1, lo1, la2, lo2 = map(math.radians, (a[0], a[1], b[0], b[1]))
                d = (math.sin((la2 - la1) / 2) ** 2
                     + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2)
                return 2 * 6371008.8 * math.asin(math.sqrt(d))
            L = sum(hav(pts[i], pts[i + 1]) for i in range(len(pts) - 1))
            print(f"    geometry: {len(pts)} pts, length {L:.1f} m, "
                  f"start ({pts[0][0]:.7f},{pts[0][1]:.7f}) end ({pts[-1][0]:.7f},{pts[-1][1]:.7f})")
        # relations referencing it
        hit = [r for r in rels.values()
               if any(m.get("type") == "way" and m.get("ref") == wid for m in r.get("members", []))]
        if hit:
            for r in hit:
                print(f"    REL {r['id']}  tags={r.get('tags', {})}")
        else:
            print("    (no relation memberships in payload)")

    # any other way carrying same name / same level nearby
    print("\n" + "=" * 78)
    print("Ways sharing a 'name' with the targets:")
    names = {ways[w].get("tags", {}).get("name") for w in targets if w in ways} - {None}
    for wid, w in sorted(ways.items()):
        nm = w.get("tags", {}).get("name")
        if nm and nm in names:
            print(f"    w{wid:<12} {nm:28s} hw={w.get('tags', {}).get('highway')} "
                  f"conv={w.get('tags', {}).get('conveying')} level={w.get('tags', {}).get('level')} "
                  f"incline={w.get('tags', {}).get('incline')}")


if __name__ == "__main__":
    main(sys.argv[1:])
