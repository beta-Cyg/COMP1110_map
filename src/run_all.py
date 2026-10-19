"""Run the whole M2 pipeline in order.

    python src/run_all.py            # fetch new data and rebuild everything
    python src/run_all.py --offline  # rebuild from the frozen snapshot only

The offline mode is what makes the report reproducible: the snapshot in
data/raw/ is the fixed input, so results do not drift when OSM changes.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
PY = sys.executable

STEPS = [
    ("fetch_osm.py", []),
    ("build_graph.py", []),
    ("dtm.py", ["--attach"]),
    ("build_facilities.py", []),
    ("report_quality.py", []),
    ("make_map.py", ["--png"]),
]


def run(script: str, args: list[str]) -> None:
    print(f"\n=== {script} {' '.join(args)} ===", flush=True)
    proc = subprocess.run([PY, str(HERE / script), *args], cwd=HERE.parent)
    if proc.returncode != 0:
        raise SystemExit(f"{script} failed with code {proc.returncode}")


def main() -> int:
    offline = "--offline" in sys.argv
    for script, args in STEPS:
        if offline and script == "fetch_osm.py":
            print("\n=== fetch_osm.py skipped (offline) ===")
            continue
        run(script, args)
    print("\nDone. Outputs in data/out/")
    return 0


if __name__ == "__main__":
    sys.exit(main())
