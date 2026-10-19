"""Digital Terrain Model: crop the territory raster, then build a usable
elevation model for the graph.

Why this is not a one-line lookup
---------------------------------
The 5 m DTM has a stated accuracy of +/-5 m. A stair flight in OSM is broken
into edges 1-8 m long, so a naive per-edge subtraction z_v - z_u puts the
noise (a few metres) on top of the signal (a metre or two) and produces
gradients of 500-1000 %. Checked on this network: a 1.4 m edge came out at
16 m of rise, and the whole Centre Street escalator summed to ~0 m instead of
~27 m.

So elevation is assigned in three tiers, from the least to the most reliable
basis available:

  document  - a published figure for a named facility (Centre Street Link,
              85 m long, 27 m rise). Wins over everything.
  way       - one slope for a whole OSM way, from its two end nodes, with the
              way's total length as the baseline. Reliable once the way is
              longer than ~15 m, because the baseline then exceeds the noise.
  chain     - short ways (individual escalator flights, 2-4 m) are grouped by
              proximity and fitted with one slope by least squares along the
              chain's principal axis. Only the aggregate gradient is trusted.

The result: `dh` is a modelled quantity on the edge, not z_v - z_u. Node
elevations stay raw DTM samples and are for diagnostics only; see the data
dictionary.

Source raster: Lands Department Whole_HK_DTM_5m (Esri ASCII, HK 1980 Grid,
EPSG:2326, 12751 x 9601 cells at 5 m).

Usage:
    python src/dtm.py                 # crop the raster to the study bbox
    python src/dtm.py --attach        # write dh into data/out/edges.csv
    python src/dtm.py --info
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import math
import statistics
import sys
import zipfile
from collections import defaultdict
from pathlib import Path

import numpy as np

import config

DTM_ZIP = config.RAW_DIR / "Whole_HK_DTM_5m.zip"
CROPPED_ASC = config.DERIVED_DIR / "dtm_corridor.asc"
BUFFER_M = 80.0

MIN_WAY_LEN_FOR_OWN_SLOPE = 15.0   # metres; below this the baseline is noise-dominated
CHAIN_LINK_DIST_M = 15.0           # metres between way endpoints to join a chain
MAX_CHAIN_GRADE = 1.20             # sanity clamp: 120 % is already absurd on foot

try:
    from pyproj import Transformer

    _TO_HK80 = Transformer.from_crs("EPSG:4326", "EPSG:2326", always_xy=True)
    _TO_WGS = Transformer.from_crs("EPSG:2326", "EPSG:4326", always_xy=True)
except Exception:  # pragma: no cover
    _TO_HK80 = _TO_WGS = None

EARTH_R = 6371008.8


def lonlat_to_hk80(lon: float, lat: float) -> tuple[float, float]:
    return _TO_HK80.transform(lon, lat)


def haversine(lat1, lon1, lat2, lon2) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_R * math.asin(math.sqrt(a))


# --------------------------------------------------------------------------
# Raster
# --------------------------------------------------------------------------
def read_asc_header(fh) -> dict:
    header = {}
    while True:
        pos = fh.tell()
        line = fh.readline()
        if not line:
            break
        key = line.split()[0].lower()
        if key in ("ncols", "nrows", "xllcorner", "yllcorner", "xllcenter",
                   "yllcenter", "cellsize", "nodata_value"):
            header[key] = float(line.split()[1])
        else:
            fh.seek(pos)
            break
    return header


def crop_from_zip(bbox=config.BBOX, buffer_m: float = BUFFER_M) -> Path:
    if not DTM_ZIP.exists():
        raise SystemExit(
            f"DTM archive missing: {DTM_ZIP}\n"
            "Download: https://www.landsd.gov.hk/landsd_psi_data/SMO/data/Whole_HK_DTM_5m.zip"
        )
    s, w, n, e = bbox
    corners = [lonlat_to_hk80(lon, lat) for lon, lat in ((w, s), (e, s), (e, n), (w, n))]
    x_min = min(c[0] for c in corners) - buffer_m
    x_max = max(c[0] for c in corners) + buffer_m
    y_min = min(c[1] for c in corners) - buffer_m
    y_max = max(c[1] for c in corners) + buffer_m

    with zipfile.ZipFile(DTM_ZIP) as z:
        member = z.infolist()[0].filename
        with z.open(member) as raw:
            text = io.TextIOWrapper(raw, encoding="ascii", newline="\n")
            header = read_asc_header(text)
            ncols, nrows = int(header["ncols"]), int(header["nrows"])
            xll = header.get("xllcorner", header.get("xllcenter"))
            yll = header.get("yllcorner", header.get("yllcenter"))
            cell = header["cellsize"]
            nodata = header.get("nodata_value", -9999.0)

            j0 = max(0, int((x_min - xll) // cell))
            j1 = min(ncols - 1, int((x_max - xll) // cell))
            k0 = max(0, int((y_min - yll) // cell))
            k1 = min(nrows - 1, int((y_max - yll) // cell))
            i0, i1 = nrows - 1 - k1, nrows - 1 - k0

            print(f"source grid : {ncols} x {nrows} @ {cell:g} m")
            print(f"corridor    : cols {j0}-{j1}, rows {i0}-{i1} "
                  f"({j1 - j0 + 1} x {i1 - i0 + 1} cells)")
            out_rows = []
            for idx, line in enumerate(text):
                if idx < i0:
                    continue
                if idx > i1:
                    break
                out_rows.append(" ".join(line.split()[j0:j1 + 1]))

    head = (f"ncols {j1 - j0 + 1}\nnrows {i1 - i0 + 1}\n"
            f"xllcorner {xll + j0 * cell:.1f}\nyllcorner {yll + k0 * cell:.1f}\n"
            f"cellsize {cell:g}\nNODATA_value {nodata:g}\n")
    CROPPED_ASC.write_text(head + "\n".join(out_rows) + "\n", encoding="ascii")
    print("wrote", CROPPED_ASC.relative_to(config.PROJECT_DIR))
    return CROPPED_ASC


class Dtm:
    """Median-window sampler over the cropped raster."""

    def __init__(self, path: Path = CROPPED_ASC):
        if not path.exists():
            raise SystemExit(f"{path} not found. Run: python src/dtm.py")
        with open(path, "r", encoding="ascii") as fh:
            h = read_asc_header(fh)
            rows = [line.split() for line in fh if line.strip()]
        self.ncols, self.nrows = int(h["ncols"]), int(h["nrows"])
        self.xll = h.get("xllcorner", h.get("xllcenter"))
        self.yll = h.get("yllcorner", h.get("yllcenter"))
        self.cell = h["cellsize"]
        self.nodata = h.get("nodata_value", -9999.0)
        self.x0 = self.xll + self.cell / 2
        self.y0 = self.yll + self.cell / 2
        self.grid = np.array(rows, dtype=float)
        if self.grid.shape != (self.nrows, self.ncols):
            raise SystemExit(f"raster shape {self.grid.shape} != header "
                             f"({self.nrows}, {self.ncols})")

    def sample(self, lon: float, lat: float, radius: int = 1):
        x, y = lonlat_to_hk80(lon, lat)
        j = int(round((x - self.x0) / self.cell))
        k = int(round((y - self.y0) / self.cell))
        if not (0 <= j < self.ncols and 0 <= k < self.nrows):
            return None, 0
        i = self.nrows - 1 - k
        window = self.grid[max(0, i - radius):i + radius + 1,
                           max(0, j - radius):j + radius + 1]
        vals = window[window != self.nodata]
        if vals.size == 0:
            return None, 0
        return float(np.median(vals)), int(vals.size)


# --------------------------------------------------------------------------
# Elevation assignment
# --------------------------------------------------------------------------
def _union_find_merge(parent: dict, a, b):
    ra, rb = parent[a], parent[b]
    if ra != rb:
        parent[rb] = ra


def _find(parent: dict, a):
    while parent[a] != a:
        parent[a] = parent[parent[a]]
        a = parent[a]
    return a


def assign_elevation(radius: int = 1) -> dict:
    nodes_path = config.OUT_DIR / "nodes.csv"
    edges_path = config.OUT_DIR / "edges.csv"
    if not nodes_path.exists() or not edges_path.exists():
        raise SystemExit("Run src/build_graph.py first.")

    dtm = Dtm()

    # --- 1. raw node elevations (diagnostic) -------------------------------
    with open(nodes_path, newline="", encoding="utf-8") as fh:
        nodes = list(csv.DictReader(fh))
    zmap, zmiss = {}, 0
    for node in nodes:
        z, used = dtm.sample(float(node["lon"]), float(node["lat"]), radius)
        if z is None:
            zmiss += 1
            node["z_m"], node["z_source"] = "", "missing"
        else:
            zmap[node["node_id"]] = z
            node["z_m"] = f"{z:.2f}"
            node["z_source"] = "dtm5m_median"
    print(f"nodes sampled: {len(nodes) - zmiss}/{len(nodes)}")

    with open(edges_path, newline="", encoding="utf-8") as fh:
        edges = list(csv.DictReader(fh))

    # Every column below is derived here, so clear the previous run's answers
    # before recomputing. Without this the facility tagger sees edges that are
    # already tagged, skips them, and the published gradient silently reverts
    # to the DTM value while `vertical_source` still claims `document_grade`.
    # That is exactly the bug this reset fixes: re-running on the output made
    # the Centre Street Link lose its document override.
    for e in edges:
        e["facility_id"] = ""
        e["vertical_source"] = "none"

    # --- 2. one representative row per (way, segment) ----------------------
    by_way: dict[str, dict[str, dict]] = defaultdict(dict)
    for e in edges:
        by_way[e["osm_way_id"]].setdefault(e["seg_index"], e)

    way_info = {}   # way_id -> (start_node, end_node, total_len)
    for way_id, segs in by_way.items():
        ordered = [segs[k] for k in sorted(segs, key=lambda x: int(x))]
        first, last = ordered[0], ordered[-1]
        if first["direction"] == "backward":
            start, end = first["v"], last["u"]
        else:
            start, end = first["u"], last["v"]
        total_len = sum(float(r["length_m"]) for r in ordered)
        way_info[way_id] = (start, end, total_len)

    # --- 3. tier B: ways long enough to trust their own end-to-end rise ----
    slope_by_way: dict[str, float] = {}
    source_by_way: dict[str, str] = {}
    for way_id, (start, end, total_len) in way_info.items():
        if total_len < MIN_WAY_LEN_FOR_OWN_SLOPE:
            continue
        if start not in zmap or end not in zmap:
            continue
        slope = (zmap[end] - zmap[start]) / total_len
        slope = max(-MAX_CHAIN_GRADE, min(MAX_CHAIN_GRADE, slope))
        slope_by_way[way_id] = slope
        source_by_way[way_id] = "dtm_way_endpoints"

    # --- 4. tier C: cluster the short ways and fit one slope per chain -----
    short = {w: v for w, v in way_info.items() if w not in slope_by_way}
    # Work in metres (HK80) rather than degrees: PCA and the regression slope
    # must be metric so that slope is m/m, not m/degree.
    node_xy: dict[str, tuple[float, float]] = {}
    for e in edges:
        node_xy.setdefault(e["u"], lonlat_to_hk80(float(e["u_lon"]), float(e["u_lat"])))
        node_xy.setdefault(e["v"], lonlat_to_hk80(float(e["v_lon"]), float(e["v_lat"])))

    parent = {n: n for n in node_xy}
    for way_id, (a, b, _len) in short.items():
        if a in node_xy and b in node_xy:
            _union_find_merge(parent, a, b)
    # link chains whose endpoints are within CHAIN_LINK_DIST_M.
    # Sorted, not raw set order: the Gauss-Seidel sweep below is sequential, so
    # the row order of the least-squares fits and the adjacency lists leaks into
    # the last bits of the result. Sorting keeps two runs byte-identical, which
    # the quality report claims.
    endpoints = {n for way_id, (a, b, _l) in short.items() for n in (a, b)}
    endpoints = sorted(n for n in endpoints if n in node_xy)
    for i, a in enumerate(endpoints):
        ax, ay = node_xy[a]
        for b in endpoints[i + 1:]:
            bx, by = node_xy[b]
            if math.hypot(ax - bx, ay - by) <= CHAIN_LINK_DIST_M:
                _union_find_merge(parent, a, b)

    chains: dict[str, list[str]] = defaultdict(list)
    for n in endpoints:
        chains[_find(parent, n)].append(n)

    chain_slope: dict[str, float] = {}
    chain_axis: dict[str, tuple[np.ndarray, np.ndarray]] = {}
    dropped = 0
    for root, members in sorted(chains.items()):
        members = sorted(members)
        pts = [(node_xy[n], zmap.get(n)) for n in members if n in zmap]
        if len(pts) < 2:
            dropped += 1
            continue
        xy = np.array([p[0] for p in pts], dtype=float)
        z = np.array([p[1] for p in pts], dtype=float)
        centroid = xy.mean(axis=0)
        centred = xy - centroid
        try:
            _, _, vt = np.linalg.svd(centred, full_matrices=False)
        except np.linalg.LinAlgError:
            dropped += 1
            continue
        axis = vt[0]
        t = centred @ axis
        if np.ptp(t) < 2.0:  # degenerate: chain shorter than the noise
            dropped += 1
            continue
        slope, _intercept = np.polyfit(t, z, 1)
        slope = float(max(-MAX_CHAIN_GRADE, min(MAX_CHAIN_GRADE, slope)))
        chain_slope[root] = slope
        chain_axis[root] = (centroid, axis)
    print(f"chains: {len(chains)} found, {len(chain_slope)} fitted, {dropped} dropped")

    # --- 5. write dh per edge ---------------------------------------------
    stats = defaultdict(int)
    for e in edges:
        way_id = e["osm_way_id"]
        length = float(e["length_m"])
        if way_id in slope_by_way:
            slope = slope_by_way[way_id]
            # direction is "both" for two-way ways, so it cannot be used to
            # tell the two rows apart. The edge id suffix can: _f / _b.
            sign = -1.0 if e["edge_id"].endswith("_b") else 1.0
            dh = slope * length * sign
            e["vertical_source"] = "dtm_way"
            stats["way"] += 1
        else:
            root = _find(parent, e["u"]) if e["u"] in parent else None
            if root in chain_slope:
                centroid, axis = chain_axis[root]
                pu = np.array(node_xy[e["u"]]) - centroid
                pv = np.array(node_xy[e["v"]]) - centroid
                dt = float(pv @ axis - pu @ axis)
                dh = chain_slope[root] * dt
                e["vertical_source"] = "dtm_chain"
                stats["chain"] += 1
            elif e["u"] in zmap and e["v"] in zmap:
                dh = zmap[e["v"]] - zmap[e["u"]]
                e["vertical_source"] = "dtm_node_pair"
                stats["pair"] += 1
            else:
                dh = 0.0
                e["vertical_source"] = "missing"
                stats["missing"] += 1

        gradient = dh / length if length > 0 else 0.0
        # Physical clamp. A 120 % grade (50 degrees) is already a very steep
        # stair; anything above it on a 2 m edge is DTM noise, not terrain.
        # Very short edges get a tighter limit because a 1 m edge cannot rise
        # more than half a metre on foot.
        cap = length * (0.60 if length < 2.0 else MAX_CHAIN_GRADE)
        if abs(dh) > cap:
            dh = math.copysign(cap, dh)
            e["vertical_source"] += "_clamped"
            stats["clamped"] += 1
        e["_dh_target"] = dh

    # --- 5b. facilities, and the document override for Centre Street ------
    # The DTM simply does not resolve the escalator structure: summed over the
    # OSM conveying ways it gives ~2 m of rise where the operator documents
    # 27 m. The published gradient wins for that one facility; every other
    # facility is only tagged, and keeps its DTM-derived dh.
    for spec in config.FACILITY_OVERRIDES:
        bs, bw, bn, be = spec["match_bbox"]
        classes = spec.get("match_classes", ("escalator", "travelator"))
        need_name = spec.get("match_name")
        graded = spec["facility_id"] == "F_CENTRE_ST_ESC"
        doc_grade = None
        if graded:
            doc_grade = spec["total_rise_m"] / spec["total_length_m"]
        for e in edges:
            if e["facility_id"]:          # already claimed by a tighter facility
                continue
            if e["edge_class"] not in classes:
                continue
            if need_name and need_name not in (e.get("name") or ""):
                continue
            lat = (float(e["u_lat"]) + float(e["v_lat"])) / 2
            lon = (float(e["u_lon"]) + float(e["v_lon"])) / 2
            if not (bs < lat < bn and bw < lon < be):
                continue
            if e["u"] not in zmap or e["v"] not in zmap:
                continue
            e["facility_id"] = spec["facility_id"]
            stats[f"tagged_{spec['facility_id']}"] += 1
            if not graded:
                continue
            # Direction comes from the DTM sampling (reliable in sign even
            # where the magnitude is not); only the gradient is replaced.
            sign = 1.0 if zmap[e["v"]] >= zmap[e["u"]] else -1.0
            e["_dh_target"] = sign * doc_grade * float(e["length_m"])
            e["vertical_source"] = "document_grade"
    stats["document_grade"] = stats.get("tagged_F_CENTRE_ST_ESC", 0)

    # --- 5c. conveying devices: geometry beats the terrain -----------------
    # An escalator is built, not eroded, so the DTM's reading is evidence
    # about the ground beside the structure rather than about the device.
    # Where the terrain reports an implausibly shallow grade the rise is
    # rebuilt from the design inclination and the sign from the OSM incline
    # tag: "up" means the way rises in its own node order, so a _b row (which
    # travels against the way) is negated. A documented facility keeps its
    # published figure -- the operator's number outranks a design assumption.
    #
    # Rebuilding the rise is only half the job. The least-squares field below
    # splits a disagreement between a node and the terrain evenly over its two
    # ends, which would lift the street end of the C1 escalator ~6 m above the
    # surveyed ground. So the upper end -- the street, for anything that drops
    # into a station or a building -- is pinned to its DTM sample and the
    # interior end absorbs the whole rise.
    design_grade = math.tan(math.radians(config.ESCALATOR_DESIGN_INCLINE_DEG))
    device_fixed = 0
    surface_pin: dict[str, float] = {}
    for e in edges:
        if e["edge_class"] != "escalator":
            continue
        if e["vertical_source"].startswith("document"):
            continue
        length = float(e["length_m"])
        if length < config.DEVICE_MIN_LENGTH_M:
            continue
        if abs(e["_dh_target"]) / length >= config.DEVICE_MIN_GRADE:
            continue                       # terrain already shows a real slope
        try:
            layer = int(float(e.get("layer") or 0))
        except ValueError:
            layer = 0
        if not (layer < 0 or e.get("indoor") == "yes"
                or e.get("facility_id") in config.DEVICE_FACILITIES):
            continue                       # open-air: the terrain is the evidence
        base = {"up": 1.0, "down": -1.0}.get(e.get("incline", ""))
        if base is None:
            continue                       # sign unknown: leave it alone
        sign = base * (1.0 if e["edge_id"].endswith("_f") else -1.0)
        e["_dh_target"] = sign * design_grade * length
        e["vertical_source"] = "device_incline"
        device_fixed += 1

        high = e["v"] if sign > 0 else e["u"]
        if high in zmap:
            surface_pin[high] = zmap[high]
    stats["device_incline"] = device_fixed
    print(f"escalator edges rebuilt from design incline "
          f"({config.ESCALATOR_DESIGN_INCLINE_DEG:g} deg): {device_fixed}"
          f"   surface ends pinned: {len(surface_pin)}")

    # --- 6. make the height field conservative -----------------------------
    # dh fitted per way does not satisfy dh(u,v) = -dh(v,u) around a loop, so
    # a route search would be free to "descend" to a higher place. Rebuild
    # node potentials phi that stay as close as possible (weighted least
    # squares) to the fitted dh, then define dh = phi[v] - phi[u].
    # Weight is edge length: a long edge's fitted rise is better evidence
    # than a 1 m edge's.
    adj: dict[str, list[tuple[str, float, float]]] = defaultdict(list)
    for e in edges:
        length = float(e["length_m"])
        target = e["_dh_target"]
        # Weight by length, but never let a sub-metre edge drop to a weight
        # near zero: then its two nodes are pulled apart by their other
        # neighbours and end up metres apart over half a metre of ground.
        w = max(length, 5.0)
        if e["vertical_source"] == "device_incline":
            w *= config.DEVICE_WEIGHT_FACTOR
        adj[e["u"]].append((e["v"], target, w))
        adj[e["v"]].append((e["u"], -target, w))

    phi = {n: zmap.get(n, 0.0) for n in adj}
    # Gauss-Seidel with a weak anchor back to the raw DTM sample. The anchor
    # stops the field from drifting far from observed data in sparsely
    # connected parts of the graph (the harbour edge, dead ends) while still
    # letting neighbouring evidence dominate.
    ANCHOR = 0.05
    for _ in range(1200):
        for n, nbrs in adj.items():
            if n in surface_pin:
                # Dirichlet condition: the ground end of a device is known,
                # the interior end is what has to move.
                phi[n] = surface_pin[n]
                continue
            num = den = 0.0
            for j, dh_ji, w in nbrs:
                num += w * (phi[j] - dh_ji)
                den += w
            if den <= 0:
                continue
            guess = num / den
            anchor = zmap.get(n)
            phi[n] = guess if anchor is None else (1 - ANCHOR) * guess + ANCHOR * anchor

    diffs = [phi[n] - zmap[n] for n in phi if n in zmap]
    if diffs:
        shift = statistics.median(diffs)
        phi = {n: v - shift for n, v in phi.items()}

    residual = 0.0
    field_clamped = 0
    for e in edges:
        dh = phi[e["v"]] - phi[e["u"]]
        residual += (dh - e["_dh_target"]) ** 2
        length = float(e["length_m"])
        # The reconstructed field is path-independent but can still leave a
        # handful of sub-metre edges with a large difference, because two
        # nodes may be tied to opposite sides of a step. Clamp those and say
        # so; 1.5 % of edges end up not exactly conserving the field.
        cap = length * (0.60 if length < 2.0 else MAX_CHAIN_GRADE)
        if abs(dh) > cap:
            dh = math.copysign(cap, dh)
            field_clamped += 1
            e["vertical_source"] += "_field_clamped"
        gradient = dh / length if length > 0 else 0.0
        e["dh"] = f"{dh:+.2f}"
        e["abs_dh"] = f"{abs(dh):.2f}"
        e["gradient"] = f"{gradient:+.4f}"
        e["grade_pct"] = f"{100 * gradient:+.1f}"
        e["z_u"] = f"{phi[e['u']]:.2f}"
        e["z_v"] = f"{phi[e['v']]:.2f}"
        if e["edge_class"] == "level" and abs(gradient) >= config.SLOPE_GRADIENT_THRESHOLD:
            e["edge_class"] = "slope"
        elif e["edge_class"] == "slope" and abs(gradient) < config.SLOPE_GRADIENT_THRESHOLD:
            e["edge_class"] = "level"
        steps = e.get("step_count") or ""
        if steps and abs(dh) > 0:
            try:
                e["step_riser_m"] = f"{abs(dh) / int(steps):.3f}"
            except ValueError:
                pass
        if e["edge_class"] == "stairs" and not steps:
            e["note"] = "step_count missing; riser assumed for cost model"
        del e["_dh_target"]

    for node in nodes:
        nid = node["node_id"]
        if nid in phi:
            node["z_m"] = f"{phi[nid]:.2f}"
            node["z_source"] = "reconstructed_field"

    print(f"field residual: rms={math.sqrt(residual / max(len(edges), 1)):.3f} m/edge"
          f"   field-clamped edges: {field_clamped}")

    with open(edges_path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(edges[0].keys()))
        writer.writeheader()
        writer.writerows(edges)
    with open(nodes_path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(nodes[0].keys()))
        writer.writeheader()
        writer.writerows(nodes)

    print("dh assignment:", dict(stats))
    print("ways with own slope:", len(slope_by_way),
          " short ways:", len(short), " chains fitted:", len(chain_slope))
    return {"stats": dict(stats), "chains": len(chain_slope)}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--info", action="store_true")
    parser.add_argument("--attach", action="store_true")
    parser.add_argument("--radius", type=int, default=1)
    args = parser.parse_args()

    if not CROPPED_ASC.exists():
        crop_from_zip()
    if args.info:
        d = Dtm()
        print(f"{CROPPED_ASC.name}: {d.ncols} x {d.nrows} @ {d.cell:g} m")
        print(f"origin cell centre (HK80): {d.x0:.1f}, {d.y0:.1f}")
        return 0
    if args.attach:
        assign_elevation(args.radius)
    return 0


if __name__ == "__main__":
    sys.exit(main())
