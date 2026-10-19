"""Step 01 - fetch the pedestrian network from OpenStreetMap via Overpass.

The raw response is frozen to disk together with the exact query and the
server's data timestamp, so that every number in the final report can be
traced back to a specific snapshot of OSM. OSM changes daily; a report that
says "the network" without a date is not reproducible.

Usage:
    python src/fetch_osm.py            # fetch and freeze a new snapshot
    python src/fetch_osm.py --list     # list existing snapshots
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

import config


def build_query(bbox=config.BBOX) -> str:
    s, w, n, e = bbox
    area = f"{s},{w},{n},{e}"
    return f"""[out:json][timeout:{config.REQUEST_TIMEOUT}];
(
  way({area})["highway"];
  way({area})["conveying"];
  node({area})["highway"="elevator"];
  node({area})["elevator"];
)->.sel;
.sel out body;
node(w.sel)->.nds;
.nds out skel qt;
"""


def make_opener():
    handler = urllib.request.ProxyHandler({})
    if config.PROXY:
        handler = urllib.request.ProxyHandler(
            {"http": config.PROXY, "https": config.PROXY}
        )
    return urllib.request.build_opener(handler)


def post(endpoint: str, query: str, opener) -> bytes:
    """The French mirror answers GET only; try GET first, fall back to POST."""
    url = endpoint + "?" + urllib.parse.urlencode({"data": query})
    req = urllib.request.Request(url, headers={"User-Agent": config.USER_AGENT})
    try:
        with opener.open(req, timeout=config.REQUEST_TIMEOUT + 60) as resp:
            return resp.read()
    except urllib.error.HTTPError as exc:
        if exc.code not in (403, 414, 405):
            raise
        body = urllib.parse.urlencode({"data": query}).encode()
        req = urllib.request.Request(
            endpoint, data=body, headers={"User-Agent": config.USER_AGENT}
        )
        with opener.open(req, timeout=config.REQUEST_TIMEOUT + 60) as resp:
            return resp.read()


def fetch(query: str):
    opener = make_opener()
    last_error = None
    for endpoint in config.ENDPOINTS:
        for attempt in range(1, config.MAX_RETRIES + 1):
            try:
                t0 = time.time()
                print(f"  -> {endpoint} (attempt {attempt}) ...", flush=True)
                payload = post(endpoint, query, opener)
                elapsed = time.time() - t0
                # A reachable but regional mirror answers 200 with zero ways
                # for Hong Kong. Treat an empty payload as a failure.
                if len(json.loads(payload).get("elements", [])) == 0:
                    last_error = "endpoint returned 0 elements (regional extract?)"
                    print(f"     useless: {last_error}")
                    break
                print(f"     ok: {len(payload) / 1024:.1f} KiB in {elapsed:.1f}s")
                return payload, endpoint
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                last_error = f"{type(exc).__name__}: {exc}"
                print(f"     failed: {last_error[:120]}")
    raise RuntimeError(f"all Overpass endpoints failed; last error = {last_error}")


def summarise(payload: bytes) -> dict:
    doc = json.loads(payload)
    elements = doc.get("elements", [])
    ways = [e for e in elements if e.get("type") == "way"]
    nodes = [e for e in elements if e.get("type") == "node"]
    highway_counts: dict[str, int] = {}
    for way in ways:
        hw = way.get("tags", {}).get("highway", "(conveying only)")
        highway_counts[hw] = highway_counts.get(hw, 0) + 1
    return {
        "total_elements": len(elements),
        "ways": len(ways),
        "nodes": len(nodes),
        "osm_base": doc.get("osm3s", {}).get("timestamp_osm_base"),
        "highway_counts": dict(
            sorted(highway_counts.items(), key=lambda kv: -kv[1])
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--list", action="store_true", help="list frozen snapshots")
    args = parser.parse_args()

    if args.list:
        for path in sorted(config.RAW_DIR.glob("overpass_*.json")):
            meta_path = path.with_suffix(".meta.json")
            stamp = "(no meta)"
            if meta_path.exists():
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
                stamp = meta.get("osm_base", "?")
            print(f"{path.name}  osm_base={stamp}  {path.stat().st_size / 1024:.1f} KiB")
        return 0

    query = build_query()
    print("Fetching bbox", config.BBOX)
    payload, endpoint = fetch(query)

    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    raw_path = config.RAW_DIR / f"overpass_{ts}.json"
    raw_path.write_bytes(payload)

    stats = summarise(payload)
    meta = {
        "fetched_at_utc": datetime.now(timezone.utc).isoformat(),
        "endpoint": endpoint,
        "bbox": list(config.BBOX),
        "proxy": config.PROXY,
        "query": query,
        "sha256": hashlib.sha256(payload).hexdigest(),
        "bytes": len(payload),
        **stats,
    }
    meta_path = raw_path.with_suffix(".meta.json")
    meta_path.write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")

    print("\nSaved :", raw_path.relative_to(config.PROJECT_DIR))
    print("Meta  :", meta_path.relative_to(config.PROJECT_DIR))
    print("OSM data timestamp:", stats["osm_base"])
    print("Ways :", stats["ways"], " Nodes:", stats["nodes"])
    print("\nTop highway values:")
    for hw, count in list(stats["highway_counts"].items())[:14]:
        print(f"  {hw:<22} {count}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
