"""Render the built network as one self-contained HTML map.

Reads data/out/{nodes,edges,facilities}.csv and writes data/out/map.html.

The point is inspection, not cartography: the map exists so that a wrong
`dh`, a broken stair, or an escalator that is not attached to the pavement is
visible at a glance before anyone builds a cost model on top of it.

Three colour modes, because the dataset has three different things that can go
wrong:

  class   - is the facility typed correctly? (the plan's three edge buckets)
  grade   - is the elevation model sane? (steep = red, flat = pale)
  source  - which edges are model output and which are documents?

Usage:
    python src/make_map.py
    python src/make_map.py --out data/out/map.html --width 1200
"""

from __future__ import annotations

import argparse
import csv
import html
import json
import math
import statistics
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, "src")
import config

EARTH_R = 6371008.8

CLASS_COLOURS = {
    "level": "#B9B7AE",
    "slope": "#D97706",
    "stairs": "#C4342A",
    "escalator": "#1D4ED8",
    "travelator": "#0E7490",
    "lift": "#7E22CE",
    "road": "#D6D3CC",
}

CLASS_LABELS = {
    "level": "平地 level",
    "slope": "坡道 slope",
    "stairs": "楼梯 stairs",
    "escalator": "扶梯 escalator",
    "travelator": "自动人行道",
    "lift": "电梯 lift",
    "road": "车行道 road",
}

# widths in viewBox units, so that a stair does not disappear under a road
CLASS_WIDTH = {
    "level": 1.4,
    "slope": 1.6,
    "stairs": 1.7,
    "escalator": 2.8,
    "travelator": 2.2,
    "lift": 2.6,
    "road": 1.0,
}


def haversine(lat1, lon1, lat2, lon2) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_R * math.asin(math.sqrt(a))


def read_csv(path: Path) -> list[dict]:
    with open(path, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def parse_wkt(wkt: str) -> list[tuple[float, float]]:
    """LINESTRING(lon lat, lon lat, ...) -> [(lon, lat), ...]"""
    if not wkt or "(" not in wkt:
        return []
    body = wkt[wkt.index("(") + 1: wkt.rindex(")")]
    out = []
    for pair in body.split(","):
        bits = pair.strip().split()
        if len(bits) == 2:
            out.append((float(bits[0]), float(bits[1])))
    return out


class Projection:
    """Web Mercator, the same frame the raster tiles are cut in.

    An equirectangular projection would be simpler, but it differs from
    Mercator by ~0.7 % in the latitude direction at this latitude - about
    14 m across this canvas. That is enough to make the edges visibly slide
    off the roads on the base map, which is exactly the comparison the base
    map exists to support.
    """

    def __init__(self, bbox, width: float):
        s, w, n, e = bbox
        self.s, self.w, self.n, self.e = s, w, n, e
        self.width = width
        self._my_n = math.log(math.tan(math.pi / 4 + math.radians(n) / 2))
        self._my_s = math.log(math.tan(math.pi / 4 + math.radians(s) / 2))
        self._lon_span = math.radians(e) - math.radians(w)
        self.height = width * (self._my_n - self._my_s) / self._lon_span
        span_x = (e - w) * math.cos(math.radians((s + n) / 2)) * 111_320
        self.metres_per_unit = span_x / width

    def __call__(self, lon: float, lat: float) -> tuple[float, float]:
        x = (math.radians(lon) - math.radians(self.w)) / self._lon_span * self.width
        my = math.log(math.tan(math.pi / 4 + math.radians(lat) / 2))
        y = (self._my_n - my) / (self._my_n - self._my_s) * self.height
        return x, y


def grade_colour(grade: float) -> str:
    """Diverging: uphill in warm, downhill in cool, pale when nearly flat."""
    g = max(-1.2, min(1.2, grade))
    t = abs(g) / 1.2
    if g >= 0:
        # pale sand -> deep red
        r = 250 - int(60 * t)
        gg = 237 - int(150 * t)
        b = 214 - int(160 * t)
    else:
        # pale sand -> deep blue
        r = 250 - int(160 * t)
        gg = 237 - int(120 * t)
        b = 214 - int(30 * t)
    return f"#{r:02X}{gg:02X}{b:02X}"


SOURCE_COLOURS = {
    "document_grade": "#7E22CE",
    "device_incline": "#DB2777",
    "dtm_way": "#1D4ED8",
    "dtm_chain": "#0E7490",
    "dtm_node_pair": "#D97706",
    "missing": "#C4342A",
}
SOURCE_LABELS = {
    "document_grade": "文档值（正街扶梯）",
    "device_incline": "设备倾角（地下扶梯）",
    "dtm_way": "way 级坡度",
    "dtm_chain": "设施链拟合",
    "dtm_node_pair": "逐节点差分（低可信）",
    "missing": "缺高程",
}


def tile_xy(lon: float, lat: float, z: int) -> tuple[float, float]:
    """Fractional slippy-map tile coordinates."""
    n = 2 ** z
    x = (lon + 180.0) / 360.0 * n
    r = math.radians(lat)
    y = (1 - math.log(math.tan(r) + 1 / math.cos(r)) / math.pi) / 2 * n
    return x, y


def tile_bounds(x: int, y: int, z: int) -> tuple[float, float, float, float]:
    """(lon1, lat1, lon2, lat2) of one tile; lat1 is the northern edge."""
    n = 2 ** z
    lon1 = x / n * 360 - 180
    lon2 = (x + 1) / n * 360 - 180
    lat1 = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * y / n))))
    lat2 = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * (y + 1) / n))))
    return lon1, lat1, lon2, lat2


def plot_tiles(proj: Projection, z: int = 16) -> tuple[str, int]:
    """Base-map tiles positioned with the same projection as the network.

    Uses OpenStreetMap's own raster tiles rather than Google: no API key, and
    the tiles are rendered from the same OSM data the edges came from, so any
    misalignment is our bug rather than a difference between two providers.
    """
    s, w, n, e = config.BBOX
    mid_lat, mid_lon = (s + n) / 2, (w + e) / 2
    x0, _ = tile_xy(w, mid_lat, z)
    x1, _ = tile_xy(e, mid_lat, z)
    _, y0 = tile_xy(mid_lon, n, z)
    _, y1 = tile_xy(mid_lon, s, z)
    parts, count = [], 0
    for x in range(int(x0), int(x1) + 1):
        for y in range(int(y0), int(y1) + 1):
            lon1, lat1, lon2, lat2 = tile_bounds(x, y, z)
            px1, py1 = proj(lon1, lat1)
            px2, py2 = proj(lon2, lat2)
            parts.append(
                f'<image href="https://tile.openstreetmap.org/{z}/{x}/{y}.png" '
                f'x="{px1:.1f}" y="{py1:.1f}" width="{px2 - px1:.1f}" '
                f'height="{py2 - py1:.1f}" preserveAspectRatio="none"/>'
            )
            count += 1
    return '<g id="tiles">' + "".join(parts) + "</g>", count


def build_svg(edges, nodes, projection) -> tuple[str, dict]:
    groups: dict[str, list[str]] = defaultdict(list)
    counts = defaultdict(int)

    for e in edges:
        pts = parse_wkt(e["geometry_wkt"])
        if len(pts) < 2:
            continue
        d = "M" + " L".join(
            f"{x:.1f},{y:.1f}" for x, y in (projection(lon, lat) for lon, lat in pts)
        )
        cls = e["edge_class"]
        grade = float(e["gradient"]) if e["gradient"] else 0.0
        dh = float(e["dh"]) if e["dh"] else 0.0
        length = float(e["length_m"])
        src = e["vertical_source"].replace("_field_clamped", "").replace("_clamped", "")
        pts_attr = " ".join(
            f"{x:.0f},{y:.0f}" for x, y in (projection(lon, lat) for lon, lat in pts)
        )
        groups[cls].append(
            f'<path d="{d}" data-x="{e["edge_id"]}" data-c="{cls}" '
            f'data-l="{length:.1f}" data-h="{dh:+.2f}" data-g="{100 * grade:+.0f}" '
            f'data-s="{src}" data-f="{e["facility_id"]}" data-p="{pts_attr}" '
            f'stroke="{CLASS_COLOURS.get(cls, "#888")}" '
            f'stroke-width="{CLASS_WIDTH.get(cls, 1.2)}"/>'
        )
        counts[cls] += 1

    parts = []
    # draw roads first, moving devices last, so the important things are on top
    order = ["road", "level", "slope", "stairs", "travelator", "lift", "escalator"]
    for cls in order + [c for c in groups if c not in order]:
        if cls not in groups:
            continue
        parts.append(f'<g class="ly" data-cls="{cls}" data-w="{CLASS_WIDTH.get(cls, 1.2)}">')
        parts.append("".join(groups[cls]))
        parts.append("</g>")

    # facility labels at the centroid of their member edges
    fac_by_id = defaultdict(list)
    for e in edges:
        if e["facility_id"]:
            fac_by_id[e["facility_id"]].append(e)
    labels = []
    for fac in read_csv(config.OUT_DIR / "facilities.csv"):
        members = fac_by_id.get(fac["facility_id"], [])
        if not members:
            continue
        xs, ys, n = 0.0, 0.0, 0
        for e in members:
            pts = parse_wkt(e["geometry_wkt"])
            for lon, lat in pts:
                x, y = projection(lon, lat)
                xs += x
                ys += y
                n += 1
        if not n:
            continue
        cx, cy = xs / n, ys / n
        name = html.escape(fac["name"])
        labels.append(
            f'<g class="fac" data-name="{name}" data-id="{fac["facility_id"]}">'
            f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="4"/>'
            f'<text x="{cx + 8:.1f}" y="{cy + 4:.1f}">{name}</text></g>'
        )

    tiles, tile_count = plot_tiles(projection)
    svg = (
        f'<svg id="map" viewBox="0 0 {projection.width:.0f} {projection.height:.0f}" '
        f'preserveAspectRatio="xMidYMid meet">'
        f'<g id="viewport">'
        + tiles
        + "".join(parts)
        + ('<g id="facilities">' + "".join(labels) + "</g>" if labels else "")
        + "</g></svg>"
    )
    return svg, dict(counts), tile_count


PAGE = """<!DOCTYPE html>
<html lang="zh-Hans">
<head>
<meta charset="utf-8">
<title>COMP1110 走廊网络图 · M2</title>
<style>
  :root {
    --ink: #1F1D1A;
    --muted: #6B6862;
    --line: #E2DFD6;
    --paper: #FBFAF7;
  }
  * { box-sizing: border-box; }
  body {
    margin: 0; background: var(--paper); color: var(--ink);
    font: 13px/1.6 -apple-system, "Segoe UI", "Microsoft YaHei", sans-serif;
    display: flex; flex-direction: column; height: 100vh;
  }
  header {
    display: flex; align-items: baseline; gap: 16px; flex-wrap: wrap;
    padding: 10px 16px; border-bottom: 1px solid var(--line);
  }
  h1 { font-size: 14px; font-weight: 500; margin: 0; }
  .meta { color: var(--muted); font-size: 12px; }
  main { flex: 1; display: flex; min-height: 0; }
  #stage {
    flex: 1; position: relative; overflow: hidden; background: #FFFFFF;
    cursor: grab;
  }
  #stage.drag { cursor: grabbing; }
  svg { width: 100%; height: 100%; display: block; }
  svg path { stroke-linecap: round; stroke-linejoin: round; fill: none; opacity: .92; }
  svg path.hot { stroke: #111 !important; stroke-width: 4 !important; opacity: 1; }
  #tiles image { opacity: .42; }
  #tiles.off { display: none; }
  #credit {
    position: absolute; right: 12px; bottom: 10px; font-size: 10.5px;
    color: var(--muted); background: rgba(255,255,255,.82); padding: 1px 5px;
    border-radius: 4px;
  }
  svg .fac circle { fill: #FFFFFF; stroke: #7E22CE; stroke-width: 1.5; }
  svg .fac text {
    font-size: 10px; fill: #4B1528; paint-order: stroke;
    stroke: #FFFFFF; stroke-width: 3px; stroke-linejoin: round;
  }
  aside {
    width: 268px; flex: 0 0 268px; border-left: 1px solid var(--line);
    padding: 12px 14px; overflow-y: auto; background: #FFFFFF;
  }
  aside h2 {
    font-size: 12px; font-weight: 500; color: var(--muted);
    margin: 14px 0 6px; text-transform: uppercase; letter-spacing: .04em;
  }
  aside h2:first-child { margin-top: 0; }
  .row { display: flex; align-items: center; gap: 8px; padding: 2px 0; }
  .sw { width: 18px; height: 3px; border-radius: 2px; flex: 0 0 18px; }
  .row label { flex: 1; cursor: pointer; }
  .row .n { color: var(--muted); font-variant-numeric: tabular-nums; font-size: 12px; }
  .row.off { opacity: .35; }
  button {
    font: inherit; font-size: 12px; padding: 4px 9px; border-radius: 6px;
    border: 1px solid var(--line); background: #FFF; color: var(--ink);
    cursor: pointer; margin: 0 4px 4px 0;
  }
  button.on { background: var(--ink); color: #FFF; border-color: var(--ink); }
  .hint { color: var(--muted); font-size: 11.5px; line-height: 1.5; }
  #tip {
    position: fixed; pointer-events: none; z-index: 9; display: none;
    background: #1F1D1A; color: #FFF; font-size: 11.5px; line-height: 1.5;
    padding: 6px 9px; border-radius: 6px; max-width: 280px;
    font-variant-numeric: tabular-nums;
  }
  #tip b { color: #FFD9A0; font-weight: 500; }
  #scale {
    position: absolute; left: 12px; bottom: 12px; font-size: 11px;
    color: var(--muted); display: flex; align-items: center; gap: 6px;
  }
  #scale .bar { height: 5px; border: 1px solid var(--muted); border-top: none; }
</style>
</head>
<body>
<header>
  <h1>COMP1110 · 西营盘—港大走廊网络</h1>
  <span class="meta">__SUMMARY__</span>
</header>
<main>
  <div id="stage">
    __SVG__
    <div id="scale"><span id="sbar" class="bar" style="width:__SCALEBAR__px"></span><span id="stext">__SCALETEXT__</span></div>
    <div id="credit">底图 © OpenStreetMap contributors</div>
  </div>
  <aside>
    <h2>着色</h2>
    <div>
      <button data-mode="class" class="on">按类型</button>
      <button data-mode="grade">按坡度</button>
      <button data-mode="source">按高程来源</button>
      <button id="reset">重置视图</button>
    </div>

    <h2>底图</h2>
    <div>
      <button id="basemap" class="on">OSM 建筑底图</button>
    </div>
    <p class="hint" id="basemap-note">
      底图是 OpenStreetMap 官方瓦片，与边数据同源——两者错位说明切分或投影有问题，
      不是两家的数据差异。无网络时底图自动消失，其余功能不受影响。
    </p>

    <h2>图层（点击隐藏）</h2>
    <div id="legend"></div>

    <h2>说明</h2>
    <p class="hint">
      颜色越红 = 越陡的上坡，越蓝 = 下坡，浅色约等于平。<br>
      扶梯按 <b>27 m / 85 m</b> 的文档坡度处理，所以它在坡度模式下一定是深红——
      这是文件值，不是 DTM 算出来的。<br>
      悬停看单条边的属性；滚轮缩放，拖拽平移。
    </p>
  </aside>
</main>
<div id="tip"></div>

<script>
const EDGES = document.querySelectorAll('#viewport path');
const stage = document.getElementById('stage');
const tip = document.getElementById('tip');

const CLASS_COLOURS = __CLASS_COLOURS__;
const CLASS_LABELS = __CLASS_LABELS__;
const CLASS_WIDTH = __CLASS_WIDTH__;
const SOURCE_COLOURS = __SOURCE_COLOURS__;
const SOURCE_LABELS = __SOURCE_LABELS__;
const COUNTS = __COUNTS__;

let mode = 'class';
const hidden = new Set();

function gradeColour(pct) {
  const g = Math.max(-120, Math.min(120, pct));
  const t = Math.abs(g) / 120;
  let r, gg, b;
  if (g >= 0) { r = 250 - 60*t; gg = 237 - 150*t; b = 214 - 160*t; }
  else        { r = 250 - 160*t; gg = 237 - 120*t; b = 214 - 30*t; }
  return `rgb(${r|0},${gg|0},${b|0})`;
}

function paint() {
  for (const p of EDGES) {
    const cls = p.dataset.c;
    let colour, width;
    if (mode === 'class') {
      colour = CLASS_COLOURS[cls]; width = CLASS_WIDTH[cls];
    } else if (mode === 'grade') {
      colour = gradeColour(parseFloat(p.dataset.g)); width = CLASS_WIDTH[cls];
    } else {
      colour = SOURCE_COLOURS[p.dataset.s] || '#999'; width = CLASS_WIDTH[cls];
    }
    p.setAttribute('stroke', colour);
    p.setAttribute('stroke-width', width);
    p.style.display = hidden.has(cls) ? 'none' : '';
  }
}

function buildLegend() {
  const box = document.getElementById('legend');
  box.innerHTML = '';
  const order = ['level','slope','stairs','escalator','travelator','lift','road'];
  for (const cls of order) {
    if (!COUNTS[cls]) continue;
    const row = document.createElement('div');
    row.className = 'row';
    row.innerHTML = `<span class="sw" style="background:${CLASS_COLOURS[cls]}"></span>
      <label>${CLASS_LABELS[cls]}</label><span class="n">${COUNTS[cls]}</span>`;
    row.querySelector('label').onclick = () => {
      if (hidden.has(cls)) hidden.delete(cls); else hidden.add(cls);
      row.classList.toggle('off', hidden.has(cls));
      paint();
    };
    box.appendChild(row);
  }
}

for (const b of document.querySelectorAll('button[data-mode]')) {
  b.onclick = () => {
    mode = b.dataset.mode;
    document.querySelectorAll('button[data-mode]').forEach(x => x.classList.toggle('on', x === b));
    paint();
  };
}

stage.addEventListener('mousemove', ev => {
  const t = ev.target;
  if (t.tagName === 'path') {
    if (hot !== t) {
      if (hot) hot.classList.remove('hot');
      t.classList.add('hot');
      hot = t;
    }
    tip.style.display = 'block';
    tip.style.left = Math.min(ev.clientX + 14, innerWidth - 290) + 'px';
    tip.style.top = (ev.clientY + 16) + 'px';
    const g = parseFloat(t.dataset.g);
    tip.innerHTML =
      `<b>${t.dataset.x}</b><br>` +
      `${CLASS_LABELS[t.dataset.c] || t.dataset.c} · ${t.dataset.l} m<br>` +
      `dh ${t.dataset.h} m · 坡度 ${g.toFixed(1)}%<br>` +
      `高程来源 ${SOURCE_LABELS[t.dataset.s] || t.dataset.s}` +
      (t.dataset.f ? `<br>设施 ${t.dataset.f}` : '');
  } else {
    if (hot) { hot.classList.remove('hot'); hot = null; }
    tip.style.display = 'none';
  }
});

// pan + zoom in viewBox coordinates, so dragging stays under the cursor
// even though the SVG is scaled to fit the panel
const svgEl = document.getElementById('map');
const vp = document.getElementById('viewport');
let k = 1, tx = 0, ty = 0, dragging = false, sx = 0, sy = 0, hot = null;

function toSvg(ev) {
  const pt = svgEl.createSVGPoint();
  pt.x = ev.clientX; pt.y = ev.clientY;
  return pt.matrixTransform(svgEl.getScreenCTM().inverse());
}
function apply() { vp.setAttribute('transform', `translate(${tx} ${ty}) scale(${k})`); }

stage.addEventListener('mousedown', e => {
  dragging = true;
  const p = toSvg(e);
  sx = p.x - tx; sy = p.y - ty;
  stage.classList.add('drag');
  tip.style.display = 'none';
});
addEventListener('mouseup', () => { dragging = false; stage.classList.remove('drag'); });
stage.addEventListener('mousemove', e => {
  if (!dragging) return;
  const p = toSvg(e);
  tx = p.x - sx; ty = p.y - sy; apply();
});
stage.addEventListener('wheel', e => {
  e.preventDefault();
  const p = toSvg(e);
  const factor = e.deltaY < 0 ? 1.12 : 1 / 1.12;
  const nk = Math.max(0.5, Math.min(30, k * factor));
  tx = p.x - (p.x - tx) * (nk / k);
  ty = p.y - (p.y - ty) * (nk / k);
  k = nk; apply();
}, { passive: false });

function focusCorridor() {
  // centre on the walking corridor rather than the whole bounding box
  const cx = 536, cy = 364, nk = 1.9;
  k = nk; tx = 500 - nk * cx; ty = 345 - nk * cy;
  apply();
}
document.getElementById('reset').onclick = focusCorridor;

buildLegend();
paint();
focusCorridor();

// base map
const tilesG = document.getElementById('tiles');
const baseBtn = document.getElementById('basemap');
let failed = 0, total = 0;
document.querySelectorAll('#tiles image').forEach(im => {
  total++;
  im.addEventListener('error', () => {
    im.style.display = 'none';
    failed++;
    if (failed === total) {
      document.getElementById('basemap-note').textContent =
        '底图瓦片全部加载失败（当前无网络或代理拦截），线网本身不受影响。';
    }
  });
});
baseBtn.onclick = () => {
  const off = tilesG.classList.toggle('off');
  baseBtn.classList.toggle('on', !off);
};
</script>
</body>
</html>
"""


def facility_centroids(edges) -> list[tuple[str, float, float]]:
    """Mean position of each facility's member edges, in lon/lat."""
    acc = defaultdict(lambda: [0.0, 0.0, 0])
    names = {}
    for fac in read_csv(config.OUT_DIR / "facilities.csv"):
        names[fac["facility_id"]] = fac["name"]
    for e in edges:
        fid = e["facility_id"]
        if not fid:
            continue
        for lon, lat in parse_wkt(e["geometry_wkt"]):
            a = acc[fid]
            a[0] += lon
            a[1] += lat
            a[2] += 1
    return [(names.get(fid, fid), a[0] / a[2], a[1] / a[2])
            for fid, a in acc.items() if a[2]]


def fetch_tile(z: int, x: int, y: int, cache: Path) -> bytes | None:
    """Download one tile with a small on-disk cache."""
    path = cache / f"{z}_{x}_{y}.png"
    if path.exists():
        return path.read_bytes()
    import urllib.request
    handler = urllib.request.ProxyHandler({})
    if config.PROXY:
        handler = urllib.request.ProxyHandler(
            {"http": config.PROXY, "https": config.PROXY}
        )
    opener = urllib.request.build_opener(handler)
    url = f"https://tile.openstreetmap.org/{z}/{x}/{y}.png"
    try:
        req = urllib.request.Request(
            url, headers={"User-Agent": config.USER_AGENT}
        )
        with opener.open(req, timeout=20) as resp:
            data = resp.read()
        cache.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return data
    except Exception:
        return None


def render_png(edges, proj, out_path: Path, scale: int = 2,
               title: str = "西营盘—港大走廊 · 边类型",
               bbox_filter=None, with_tiles: bool = True) -> Path:
    """Static PNG of the same network, for the report and for the slides."""
    from PIL import Image, ImageDraw, ImageFont

    W = int(proj.width * scale)
    H = int(proj.height * scale)
    base = Image.new("RGB", (W, H), "#FBFAF7")

    if with_tiles:
        import io
        cache = config.DERIVED_DIR / "tiles"
        z = 16
        s_, w_, n_, e_ = config.BBOX
        mid_lat, mid_lon = (s_ + n_) / 2, (w_ + e_) / 2
        tx0, _ = tile_xy(w_, mid_lat, z)
        tx1, _ = tile_xy(e_, mid_lat, z)
        _, ty0 = tile_xy(mid_lon, n_, z)
        _, ty1 = tile_xy(mid_lon, s_, z)
        got = 0
        fails = 0
        stop = False
        for tx in range(int(tx0), int(tx1) + 1):
            if stop:
                break
            for ty in range(int(ty0), int(ty1) + 1):
                data = fetch_tile(z, tx, ty, cache)
                if not data:
                    fails += 1
                    if got == 0 and fails >= 3:
                        print("  base map: network unavailable, drawing without it")
                        stop = True
                        break
                    continue
                tile = Image.open(io.BytesIO(data)).convert("RGB")
                lon1, lat1, lon2, lat2 = tile_bounds(tx, ty, z)
                px1, py1 = proj(lon1, lat1)
                px2, py2 = proj(lon2, lat2)
                box = (int(px1 * scale), int(py1 * scale),
                       int(px2 * scale) + 1, int(py2 * scale) + 1)
                tw, th = box[2] - box[0], box[3] - box[1]
                if tw <= 0 or th <= 0:
                    continue
                tile = tile.resize((tw, th), Image.LANCZOS)
                # mute the base map so the edges stay legible on top of it
                tile = Image.blend(tile, Image.new("RGB", tile.size, "#FBFAF7"), 0.34)
                base.paste(tile, (box[0], box[1]))
                got += 1
        print(f"  base map: {got} tiles pasted")

    dr = ImageDraw.Draw(base)

    def font(size):
        for path in (r"C:\Windows\Fonts\msyh.ttc", r"C:\Windows\Fonts\simhei.ttf"):
            try:
                return ImageFont.truetype(path, size)
            except Exception:
                continue
        return ImageFont.load_default()

    if bbox_filter:
        s, w, n, e_ = bbox_filter
        edges = [e for e in edges
                 if s <= float(e["u_lat"]) <= n and w <= float(e["u_lon"]) <= e_]

    order = ["road", "level", "slope", "stairs", "travelator", "lift", "escalator"]
    buckets = defaultdict(list)
    for e in edges:
        pts = parse_wkt(e["geometry_wkt"])
        if len(pts) < 2:
            continue
        buckets[e["edge_class"]].append(
            [(proj(lon, lat)[0] * scale, proj(lon, lat)[1] * scale) for lon, lat in pts]
        )

    for cls in order + [c for c in buckets if c not in order]:
        width = max(1, int(round(CLASS_WIDTH.get(cls, 1.2) * scale * 0.75)))
        for line in buckets.get(cls, []):
            if with_tiles:
                # white casing keeps the line legible over buildings and text
                dr.line(line, fill="#FFFFFF", width=width + 2, joint="curve")
            dr.line(line, fill=CLASS_COLOURS.get(cls, "#888888"), width=width,
                    joint="curve")

    # everything below is expressed at scale 2 and multiplied by s, so the
    # same code produces a readable image at any output size
    s = scale * 0.75

    # facilities
    f_lab = font(int(14 * s))
    for name, lon, lat in facility_centroids(edges):
        x, y = proj(lon, lat)
        x, y = x * scale, y * scale
        r = 5 * s
        dr.ellipse([x - r, y - r, x + r, y + r], fill="#FFFFFF",
                   outline="#7E22CE", width=max(1, int(1.5 * s)))
        dr.text((x + 9 * s, y - 8 * s), name, font=f_lab, fill="#4B1528",
                stroke_width=max(1, int(2.5 * s)), stroke_fill="#FBFAF7")

    # legend
    present = [c for c in order if c in buckets]
    f_title = font(int(20 * s))
    f_body = font(int(14 * s))
    x0, y0 = 20 * s, 20 * s
    box_w = 240 * s
    box_h = 40 * s + 23 * s * len(present)
    dr.rectangle([x0, y0, x0 + box_w, y0 + box_h], fill="#FFFFFF", outline="#DCD8CE")
    dr.text((x0 + 12 * s, y0 + 10 * s), title, font=f_title, fill="#1F1D1A")
    ty = y0 + 40 * s
    for cls in present:
        dr.line([(x0 + 14 * s, ty + 7 * s), (x0 + 48 * s, ty + 7 * s)],
                fill=CLASS_COLOURS[cls], width=max(2, int(3 * s)))
        dr.text((x0 + 56 * s, ty - s), f"{CLASS_LABELS[cls]}  {len(buckets[cls])}",
                font=f_body, fill="#3A372F")
        ty += 23 * s

    # scale bar
    bar_m = 200
    bar_px = bar_m / proj.metres_per_unit * scale
    bx, by = W - bar_px - 40 * s, H - 40 * s
    dr.line([(bx, by), (bx + bar_px, by)], fill="#3A372F", width=max(1, int(2 * s)))
    dr.line([(bx, by - 4 * s), (bx, by + 4 * s)], fill="#3A372F", width=max(1, int(2 * s)))
    dr.line([(bx + bar_px, by - 4 * s), (bx + bar_px, by + 4 * s)], fill="#3A372F",
            width=max(1, int(2 * s)))
    dr.text((bx, by + 6 * s), f"{bar_m} m", font=f_body, fill="#3A372F")

    base.save(out_path)
    return out_path


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(config.OUT_DIR / "map.html"))
    ap.add_argument("--width", type=float, default=1000)
    ap.add_argument("--png", action="store_true", help="also write a static PNG")
    args = ap.parse_args()

    edges = read_csv(config.OUT_DIR / "edges.csv")
    nodes = read_csv(config.OUT_DIR / "nodes.csv")

    proj = Projection(config.BBOX, args.width)
    svg, counts, tile_count = build_svg(edges, nodes, proj)

    meta = json.loads((config.OUT_DIR / "build_stats.json").read_text(encoding="utf-8"))
    summary = (
        f"{len(edges)} 条有向边 · {len(nodes)} 节点 · "
        f"OSM {meta.get('osm_base')} · DTM 5 m"
    )

    scale_m = 200
    scale_px = scale_m / proj.metres_per_unit

    page = (
        PAGE
        .replace("__SVG__", svg)
        .replace("__SUMMARY__", html.escape(summary))
        .replace("__CLASS_COLOURS__", json.dumps(CLASS_COLOURS))
        .replace("__CLASS_LABELS__", json.dumps(CLASS_LABELS, ensure_ascii=False))
        .replace("__CLASS_WIDTH__", json.dumps(CLASS_WIDTH))
        .replace("__SOURCE_COLOURS__", json.dumps(SOURCE_COLOURS))
        .replace("__SOURCE_LABELS__", json.dumps(SOURCE_LABELS, ensure_ascii=False))
        .replace("__COUNTS__", json.dumps(counts))
        .replace("__SCALEBAR__", f"{scale_px:.0f}")
        .replace("__SCALETEXT__", f"{scale_m} m")
    )

    out = Path(args.out)
    out.write_text(page, encoding="utf-8")
    print(f"wrote {out}  ({out.stat().st_size / 1024 / 1024:.1f} MiB)")
    print("  edges drawn:", sum(counts.values()))
    for cls, n in sorted(counts.items(), key=lambda kv: -kv[1]):
        print(f"    {cls:<11}{n}")
    print(f"  scale: 1 unit = {proj.metres_per_unit:.2f} m; "
          f"canvas {proj.width:.0f} x {proj.height:.0f}")
    print(f"  base map: {tile_count} OSM tiles at z=16 (loaded from the network)")

    if args.png:
        try:
            png = config.OUT_DIR / "map.png"
            render_png(edges, proj, png)
            print(f"  wrote {png.relative_to(config.PROJECT_DIR)} "
                  f"({png.stat().st_size / 1024:.0f} KiB)")

            # a second, tighter view: the walking corridor itself
            corridor = (22.2790, 114.1320, 22.2900, 114.1470)
            proj2 = Projection(corridor, args.width)
            png2 = config.OUT_DIR / "map_corridor.png"
            render_png(edges, proj2, png2, scale=3,
                       title="研究走廊 · 正街—东闸", bbox_filter=corridor)
            print(f"  wrote {png2.relative_to(config.PROJECT_DIR)} "
                  f"({png2.stat().st_size / 1024:.0f} KiB)")
        except ImportError:
            print("  Pillow not installed; skipped PNG")
    return 0


if __name__ == "__main__":
    sys.exit(main())
