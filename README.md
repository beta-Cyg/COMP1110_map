# M2 — Data engineering

Turns OpenStreetMap and the Lands Department terrain model into the directed,
typed, time-windowed network that the rest of the project runs on.

Output: `data/out/{nodes,edges,facilities,windows}.csv` + `QUALITY_REPORT.md`.

## Quick start

```bash
# full run: fetch a fresh OSM snapshot, then rebuild everything
python src/run_all.py

# rebuild from the frozen snapshot (what the report cites)
python src/run_all.py --offline

# sanity check: route between two points
python src/route_probe.py --from 22.2873,114.1421 --to 22.2830,114.1370

# just redraw the map
python src/make_map.py --png
```

Dependencies: `numpy`, `pyproj` (everything else is standard library), and
`Pillow` only for the static PNG. Tested with Python 3.13.

### What is in the repository

| Path | Tracked | Why |
|---|---|---|
| `data/raw/overpass_*.json` | yes | the frozen input; `run_all.py --offline` reproduces every output byte for byte from it |
| `data/raw/Whole_HK_DTM_5m.zip` | no | 28.7 MB Lands Department dataset, downloaded rather than copied around |
| `data/derived/dtm_corridor.asc` | yes | the cropped raster, so a clone can rebuild elevations without the download |
| `data/derived/tiles/` | no | base-map cache |
| `data/out/*.csv`, `map.html`, `map.png`, `map_corridor.png`, `QUALITY_REPORT.md` | yes | the delivered data product, including the renders for the report |

## The map

`data/out/map.html` is one self-contained file — no server and no build step.
Open it in a browser: hover an edge for its attributes, scroll to zoom, drag
to pan.

It carries an **OpenStreetMap base map** as a toggleable layer. OSM rather than
Google on purpose: Google's JS API needs an API key, and more importantly the
OSM tiles are rendered from the same data the edges came from, so any
misalignment is our bug rather than a difference between providers. That makes
the base map a check, not decoration.

The base map needs the network. Tiles are fetched at zoom 16 (~24 images) and
if they cannot be reached the layer simply stays empty — nothing else changes.
Tiles for the PNG renders are cached in `data/derived/tiles/`.

Three colour modes, because three different things can go wrong:

| mode | what it exposes |
|---|---|
| by class | a facility typed wrong (a stair mapped as a path, an escalator not attached) |
| by gradient | an elevation model that has gone bad — steep is red, flat is pale |
| by source | which edges are model output (`dtm_*`) and which are documents (`document_grade`) |

`map.png` and `map_corridor.png` are static renders for the report, both with
the base map baked in. The second one is zoomed to the walking corridor itself.

Both the tiles and the vector layer are drawn in **Web Mercator**. An
equirectangular frame would have been simpler but differs by ~0.7 % in the
latitude direction, about 14 m across this canvas — enough to make the edges
slide visibly off the roads.



## Layout

```
data/raw/       overpass_<ts>.json + .meta.json   frozen OSM snapshot
                Whole_HK_DTM_5m.zip               28.7 MB, territory-wide DTM
data/derived/   dtm_corridor.asc                  DTM cropped to the bbox
data/out/       nodes.csv edges.csv facilities.csv windows.csv
                QUALITY_REPORT.md build_stats.json
                map.html map.png map_corridor.png
src/            fetch_osm.py build_graph.py dtm.py build_facilities.py
                report_quality.py route_probe.py make_map.py run_all.py config.py
                inspect_way.py nearby.py        (diagnostics: one OSM way, one point)
```

## Data sources

| What | Source | Access |
|---|---|---|
| Pedestrian network, stairs, escalators | OpenStreetMap via Overpass | `overpass.openstreetmap.fr`, GET, UA `overpass-turbo` |
| Elevation | Lands Department `Whole_HK_DTM_5m` | direct ZIP, 5 m grid, ±5 m, HK80 / EPSG:2326 |
| Facility hours and direction | District Council paper 2018-02-01, HK Road Wiki, MTR | in `config.py` |

**Note on mirrors.** `overpass-api.de` and its subdomains return HTTP 502 from
this network. `overpass.osm.ch` answers but only carries a Swiss extract and
returns zero ways for Hong Kong — reachability alone is not enough, the payload
must be checked. `overpass.openstreetmap.fr` works but rejects POST and generic
user agents with 403.

## Decisions that shaped the output

Each of these was forced by something the data did, not chosen in advance.

**1. Directed edges, one row per travel direction.** `dh` is signed in that
direction. A two-way segment becomes two rows linked by `rev_edge_id`; a
one-way escalator produces one row. M4 can `add_edge` directly, M3 never has to
flip a sign.

**2. Stair turning points are graph nodes; stair vertices are not.** Keeping
every OSM vertex produced edges 0.5 m long. The 5 m DTM cannot resolve that
scale, and the result was 1000 % gradients. Stairs are now cut at junctions,
at geometric turns ≥ 20°, and at least every 25 m. The turn is what matters for
geometry and for field checks; the digitising noise between turns is not.

**3. Elevation is assigned at way or chain level, never per edge.** A 5 m
grid with ±5 m accuracy cannot support differencing two nodes 2 m apart. Tiers:

- `document_grade` — published gradient for the Centre Street Link (27 m / 85 m)
- `dtm_way` — one slope per OSM way from its end nodes (baseline > 15 m)
- `dtm_chain` — short ways grouped by proximity, one slope fitted along the chain
- `dtm_node_pair` — fallback, raw difference between two nodes
- `device_incline` — escalator rise rebuilt from a 30° design inclination where
  the terrain cannot see the structure (see below)

**4. The height field is reconstructed to be path-independent.** Slopes fitted
per way do not satisfy `dh(u,v) = -dh(v,u)` around a loop, which lets a router
"descend" to a higher place. Node potentials are solved by weighted
least squares (weight ∝ edge length, floored at 5 m), then `dh = phi[v] - phi[u]`.
Residual RMS after fitting: ~1.4 m per edge.

**5. Published facts override the map, and the override is recorded.**
Uphill the Centre Street Link assists all 85 m; downhill only about 25 m.
That comes from the District Council paper, not OSM. `direction_source` and
`vertical_source` record which numbers are model output and which are documents.

**6. A conveying device is a structure, not terrain.** The DTM is a bare-earth
model, so at a station or a building it samples the ground *beside* the shaft
and a real escalator comes back flat. The case that exposed it: the C1 escalator
pair at HKU station (`w407891117` / `w407891119`), a 22.6 m flight whose two
ends both land in the same 59.0 m cell — 3 cm of rise. Any device claiming under
10 % grade over 4 m or more is rebuilt from the 30° design inclination, but only
where a structure is known (a facility row, or `layer < 0`); the upper end is
pinned to the surveyed ground and the interior end takes the whole drop. Six
edges carry `device_incline`. Open-air flights keep the terrain reading.

**7. The pipeline is deterministic.** `run_all.py --offline` twice produces a
byte-identical `edges.csv`. That is not free: the Gauss-Seidel sweep is
sequential, so chain membership and adjacency order had to be sorted, or
floating-point residues drifted by ~0.1 m between runs. `dtm.py` is also
idempotent: it clears `facility_id` / `vertical_source` before recomputing, so
running it twice on the same file cannot silently downgrade a document value to
a DTM one.

**8. A facility is matched by name where OSM gives one.** A bounding box cannot
distinguish the Centre Street Link from the MTR Sai Ying Pun station
circulators forty metres away, and for a year of mapping they share a block.
Every conveying way of the link is tagged `name=正街`; the station's are not.
The box is now only a guard on top of the name.

## What the data actually says

- Largest connected component: 6254 / 6328 nodes (98.8 %).
- 11.7 % of stair *ways* carry `step_count` (the plan quotes 0.11 % for all of
  Hong Kong Island — the corridor is better mapped than the island average).
- 364 edges (2.2 %) were clamped after field reconstruction; they are physically
  sensible but do not conserve the field. Marked in `vertical_source`.
- The DTM does **not** resolve the Centre Street escalator: summed over the
  conveying ways it reports ~2 m of rise where the operator documents 27 m.
  This is the concrete justification for the document override.
- Route probe, Third Street → HKU: distance route 811 m / +53.8 m ascent,
  ascent route 1993 m / +47.7 m ascent. Only 9 % of edges are shared, so the two
  criteria really do pick different streets. Run `route_probe.py` with the
  corridor endpoints rather than the defaults to see it.

## Open items

- [ ] HKU East Gate daily hours — unpublished, needs the site visit (M5).
- [x] **The `F_CENTRE_ST_ESC` box was swallowing the MTR Sai Ying Pun station.**
      Of the 232.7 m of conveying geometry inside it, 118.3 m (51 %) carried no
      `正街` name: those ways' endpoints sat exactly on exits B1 第一街, B2
      第二街 and B3 奇靈里. They were being charged the Centre Street gradient,
      which is where the facility's +55.5 m of assigned rise against a
      documented 27 m came from. Fixed 2026-10-20: the matcher now requires
      `name=正街`, and the station has its own `F_MTR_SYP` row. The link is now
      **114.5 m of geometry / 24.8 m of rise**, and one belt per flight is
      **89 m against the documented 85 m** (+4.2 %), i.e. 28.1 m of rise
      against 27 m.
- [ ] `F_MTR_SYP` is now under-modelled in the other direction: its escalators
      give 4–22 % grade (16.5 m of rise over 118.3 m) because four of them carry
      `conveying` but no `incline`, so the sign cannot be read and the design
      inclination cannot be applied. Needs either OSM fixes upstream or a
      connectivity-based sign inference.
- [ ] `document_grade` is a *target*, not a constraint: the conservative field
      still defines `dh = phi[v] - phi[u]`, so ~9 % of the published gradient
      is averaged away (per-edge 72–93 %). Materialising it needs the facility
      endpoints pinned, the way the escalator ground ends now are.
- [ ] Field check the critical edges: every stair / escalator / lift plus every
      edge with |dh| > 3 m.

## Handover

- **M3 (cost model):** use `dh`, `grade_pct` and `edge_class` from `edges.csv`.
  `z_u`/`z_v` are the reconstructed field, consistent with `dh` except on the
  2.3 % clamped edges.
- **M4 (algorithms):** build a `DiGraph` from `edges.csv` directly; every row is
  already directed. `rev_edge_id` links the two directions of a physical
  segment if you need to collapse them.
- **M5 (field, maps):** `site_check` is `pending` on every row; the critical
  edges are the ones to walk first.
