# Data quality report — COMP1110 corridor network

Generated: 2026-10-20 01:22 (local)

## 1. Provenance

- OSM snapshot: `data\raw\overpass_20261019T160854Z.json`
- OSM data timestamp: **2026-10-19T16:07:50Z**
- Extraction: Overpass API via `overpass.openstreetmap.fr` (GET, UA `overpass-turbo`)
- Terrain: Lands Department `Whole_HK_DTM_5m` (5 m grid, Esri ASCII, HK80 / EPSG:2326, ±5 m)
- Bounding box: (22.276, 114.124, 22.294, 114.152) (S, W, N, E)

## 2. Size

- OSM ways parsed: **4541**
- Graph nodes: **6328**
- Directed edges: **16557**
- Total network length (undirected): **226.8 km**

## 3. Edge classes

| class | edges | length (m) | pedestrian only |
|---|---:|---:|---:|
| slope | 6586 | 65477 | 6388 |
| road | 4784 | 75668 | 0 |
| level | 3406 | 74955 | 3206 |
| stairs | 1694 | 10110 | 1694 |
| escalator | 68 | 611 | 68 |
| lift | 12 | 13 | 12 |
| travelator | 7 | 3 | 7 |

`level` and `slope` are split at 3% gradient. `road` is kept for connectivity only and is flagged non-pedestrian.

## 4. Vertical data

| vertical_source | edges | meaning |
|---|---:|---|
| dtm_way | 10934 | one slope per OSM way, from its end nodes (baseline > 15 m) |
| dtm_chain | 3988 | short ways grouped and fitted with one slope along the chain |
| dtm_node_pair | 1173 | fallback: raw DTM difference between two nodes |
| dtm_chain_field_clamped | 153 | short ways grouped and fitted with one slope along the chain — clamped after field reconstruction; this edge no longer conserves the field exactly |
| dtm_way_field_clamped | 112 | one slope per OSM way, from its end nodes (baseline > 15 m) — clamped after field reconstruction; this edge no longer conserves the field exactly |
| dtm_node_pair_field_clamped | 45 | fallback: raw DTM difference between two nodes — clamped after field reconstruction; this edge no longer conserves the field exactly |
| dtm_node_pair_clamped | 34 | fallback: raw DTM difference between two nodes — clamped to a physical limit |
| dtm_node_pair_clamped_field_clamped | 27 | fallback: raw DTM difference between two nodes — clamped after field reconstruction; this edge no longer conserves the field exactly |
| missing | 25 | no elevation available |
| dtm_chain_clamped_field_clamped | 25 | short ways grouped and fitted with one slope along the chain — clamped after field reconstruction; this edge no longer conserves the field exactly |
| document_grade | 16 | published gradient for the Centre Street Link (27 m / 85 m) |
| dtm_chain_clamped | 15 | short ways grouped and fitted with one slope along the chain — clamped to a physical limit |
| device_incline | 6 | escalator whose rise was rebuilt from its design inclination; the DTM cannot see inside a structure |
| dtm_way_clamped | 4 | one slope per OSM way, from its end nodes (baseline > 15 m) — clamped to a physical limit |

- |gradient| median: 0.066   p95: 0.600   max: 1.200
- edges with |gradient| > 1.2 (impossible on foot): **0**

### Conveying devices

- escalator edges: 68 directed, of which 64 are 4 m or longer
- rebuilt from the design inclination (30°, tan = 0.577): **6** edges
- still implying less than 10% grade: **10** edges

| edge | length (m) | dh (m) | grade | facility |
|---|---:|---:|---:|---|
| `w1275489346_s0_b` | 11.2 | -4.0 | -36 % | F_HKU_EAST_GATE |
| `w1275489348_s0_f` | 11.2 | +4.0 | +36 % | F_HKU_EAST_GATE |
| `w407891117_s0_f` | 22.6 | +12.1 | +54 % | F_MTR_HKU |
| `w407891119_s0_b` | 22.6 | -12.1 | -54 % | F_MTR_HKU |
| `w534044206_s0_b` | 34.4 | +18.0 | +52 % | F_HKU_EAST_GATE |
| `w534044206_s0_f` | 34.4 | -18.0 | -52 % | F_HKU_EAST_GATE |

Why: the DTM is a bare-earth model, so at a station or building it samples the ground *beside* the shaft. Checked on the C1 escalator pair at HKU station: a 22.6 m flight had both ends in the same 59.0 m cell, giving 3 cm of rise, where a 30° machine lifts 13 m. The rebuild applies only where a structure is known (a facility row, or `layer < 0`); open-air flights are left to the terrain, and the upper end of each rebuilt device is held at its DTM value so that the interior end takes the whole drop.

- node elevation range: -39.5 – 539.0 m (6328 nodes with z)
- The height field is reconstructed so that d*h* is path-independent (sum around any loop = 0). Without that, a router can 'descend' to a higher place.
- A residual set of edges (310) was clamped after reconstruction because two adjacent nodes on a sub-metre edge were tied to opposite sides of a step. Those edges are physically sensible but do not conserve the field; they are marked in `vertical_source`.
- The minimum node elevation (~-40 m) occurs on the harbour surface inside the bbox, not on the corridor.

## 5. Coverage gaps

- stairs: 1694 directed edges in 515 ways
- stairs carrying `step_count`: 60 ways (**11.7 %** of stair ways)
  The plan quotes 0.11 % for the whole of Hong Kong Island. The corridor is better mapped than the island average, but most stairs still have no step count, which is why riser height has to be assumed in the cost model.

- one-way edges: 43 (moving devices and one-way carriageways)

## 6. Connectivity

- components: 29; largest holds **6254/6328 nodes (98.8 %)**
- remaining components: [9, 8, 5, 4, 4, 3, 2] (isolated footpaths, not on the corridor)

## 7. Checks against known facts

- Centre Street Link: 16 directed edges over 16 OSM ways; 114 m of geometry, 24.8 m of rise assigned.
  Paired up/down belts are two ways for one flight, so one belt per flight is **89 m** against a documented 85 m (+4.2 %), which at the published 31.8 % grade is 28.1 m of rise against the documented 27 m. Match: **consistent**.
- Corridor rise: computed from node elevations, Third Street ≈ -39 m to the upper campus ≈ 539 m is the full bbox, not the corridor; use `route_probe.py` for the corridor figure.
- SRTM was deliberately not used: it inverts the expected gradient across the Mid-Levels.

## 8. Known limitations (do not overclaim)

1. Elevation is ±5 m at 90 %; on a 2 m edge that noise exceeds the signal. Gradients are therefore modelled at way or chain level, not per edge.
2. Stair riser height is assumed (0.165 m) where `step_count` is missing — which is most stairs.
3. OSM completeness is not uniform: an unmapped stair means the network thinks the path does not exist. The field check (M5) is the remedy.
4. Facility hours for the HKU East Gate are unpublished and must come from the site visit; `windows.csv` marks them as pending.
5. The 5 m DTM does not resolve a conveying structure: summed over the Centre Street ways it gives ~2 m instead of the documented 27 m, and both ends of the 22.6 m C1 escalator at HKU station fall in the same 5 m cell. Outside a documented facility the rise of such a device is therefore rebuilt from a 30° design inclination, not measured — see §4. At 35° (the high-speed variant) the figure would be 21 % larger.
6. Node elevations inside a station or building are a modelled depth relative to the pinned ground surface, not a surveyed level. Use them to compare routes, not to quote a platform depth.

