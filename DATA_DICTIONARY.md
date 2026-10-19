# Data dictionary

Four tables. Every row is either an observation (OSM, DTM, document) or a
model output, and each column that could be mistaken for the other carries a
`*_source` field.

## `nodes.csv`

| Column | Type | Meaning |
|---|---|---|
| `node_id` | text | graph node id, `n<osm_id>` — traceable back to OSM |
| `osm_node_id` | int | raw OSM node id |
| `lon`, `lat` | float | WGS84, 7 decimals |
| `node_type` | text | `junction`, `stair_head`, `vertex` |
| `way_ref_count` | int | how many OSM ways use this node; ≥ 2 means a junction |
| `z_m` | float | elevation, metres, reconstructed field (see below) |
| `z_source` | text | `reconstructed_field`, `missing` |

`z_m` is **not** the raw DTM pixel. It is the node potential that makes `dh`
path-independent, so that walking a loop returns you to the same height. Raw
DTM samples are one input to it, not the output. Use it for maps and for
diagnostics; use `dh` on the edges for any calculation of effort or ascent.

## `edges.csv`

One row per **travel direction**. A two-way pavement segment appears twice,
once as `_f` and once as `_b`, and the two rows point at each other through
`rev_edge_id`.

| Column | Type | Meaning |
|---|---|---|
| `edge_id` | text | `w<way_id>_s<seg>_<f|b>` — suffix is the direction |
| `rev_edge_id` | text | the opposite-direction row, empty if one-way |
| `u`, `v` | text | from-node, to-node |
| `u_lat`,`u_lon`,`v_lat`,`v_lon` | float | endpoint coordinates |
| `osm_way_id` | int | source OSM way |
| `name` | text | OSM `name` verbatim, empty when untagged. Use it to tell facilities apart: inside the Centre Street box the 正街 link is named and the MTR station escalators are not. |
| `seg_index` | int | order of this segment within the way |
| `edge_class` | text | `level`, `slope`, `stairs`, `escalator`, `travelator`, `lift`, `road` |
| `is_pedestrian` | 0/1 | 0 for carriageways kept only for connectivity |
| `direction` | text | `both`, `forward`, `backward` — the *physical* segment's rule |
| `length_m` | float | planar length along the polyline, metres |
| `z_u`, `z_v` | float | reconstructed field at the endpoints |
| `dh` | float | **signed** rise along u→v, metres (+ up, − down) |
| `abs_dh` | float | convenience copy of \|dh\| |
| `gradient` | float | dh / length_m |
| `grade_pct` | float | 100 × gradient |
| `step_count` | int | OSM `step_count` when present, else empty |
| `step_riser_m` | float | \|dh\| / step_count when both exist |
| `step_count_source` | text | `osm` or `missing` |
| `incline` | text | OSM `incline` verbatim — `up` means the way rises in its own node order |
| `surface`, `smoothness`, `wheelchair`, `indoor` | text | OSM tags verbatim, may be empty |
| `layer` | int | OSM `layer` verbatim, empty when untagged. Negative = below grade, positive = deck/bridge. Empty is **not** the same as 0: an untagged escalator inside a station is still underground. |
| `conveying` | text | OSM `conveying` verbatim (`forward`, `backward`, `reversible`). Tells the belt direction, which is what makes an escalator one-way. |
| `facility_id` | text | set when the edge belongs to a named facility |
| `vertical_source` | text | where `dh` came from — **read this before trusting dh** |
| `horizontal_source` | text | always `osm` for now |
| `site_check` | text | `pending` until field-verified |
| `note` | text | free text, e.g. missing step count |
| `geometry_wkt` | text | `LINESTRING(lon lat, ...)` in travel direction |

### `vertical_source` values

| Value | Meaning | Trust |
|---|---|---|
| `document_grade` | published gradient for the Centre Street Link | high |
| `device_incline` | escalator rise rebuilt from a 30° design inclination because the DTM cannot see inside a station or building | **assumed, not measured** — see below |
| `dtm_way` | one slope per OSM way from its two end nodes | medium — good when the way is > 15 m |
| `dtm_chain` | short flights grouped and fitted with one slope | medium — aggregate gradient only |
| `dtm_node_pair` | raw DTM difference between two nodes | **low** — noise dominated |
| `missing` | no elevation available | none |
| `*_clamped` | limited to a physically possible gradient | value is a bound, not a measurement |
| `*_field_clamped` | clamped after reconstruction | does not conserve the field |

### About `device_incline`

The DTM is a bare-earth model: at a station or a building it samples the ground
*beneath or beside* the shaft, so a conveying device comes back flat. Verified
case — the C1 escalator pair at HKU station, `w407891117` / `w407891119`:
a 22.6 m flight whose two ends both land in the same 59.0 m cell, which the
terrain model reports as 3 cm of rise.

A device that claims under 10 % grade over more than 4 m is therefore rebuilt
from the design inclination (30°, `tan` = 0.577), signed from `incline` and the
`_f`/`_b` suffix. The rebuild is deliberately narrow:

- only where a structure is known — a facility row (`F_MTR_HKU`,
  `F_HKU_EAST_GATE`) or `layer < 0`. Open-air flights keep the terrain reading,
  and a device crossing above a road is excluded too, because the ground under
  a bridge is a different surface;
- the **upper** end is pinned to its DTM sample and the interior end takes the
  whole drop, so the street level does not drift;
- `z_u` / `z_v` inside a station are therefore a modelled depth relative to a
  pinned ground surface. Compare routes with them; do not quote a platform
  depth.

## `facilities.csv`

A facility is what a traveller perceives — "the Centre Street escalator" — not
what OSM stores, which is 16 separate conveying ways. Aggregating them is what
lets M3 charge one waiting cost for one ride. Matching uses the OSM `name` where
one exists (`F_CENTRE_ST_ESC` is `name=正街`), because a bounding box alone
cannot tell the street escalator from the station escalators in the same block.

| Column | Meaning |
|---|---|
| `facility_id` | stable id, referenced by `edges.facility_id` |
| `name`, `class` | human-readable |
| `total_length_m`, `total_rise_m` | summed over member edges, one direction only |
| `direction` | `asymmetric`, `both` |
| `direction_source` | `document` or `osm` — the document wins |
| `source_doc` | which document |
| `osm_way_count` | how many OSM ways were aggregated |
| `note` | disagreements between OSM geometry and the document |

## `windows.csv`

The three scenarios are rows, not logic branches: `normal`, `maintenance`,
`closed_night`. Select the scenario, then check the departure time against
`start`/`end`.

| Column | Meaning |
|---|---|
| `facility_id` | which facility |
| `scenario` | `normal`, `maintenance`, `closed_night` |
| `day_of_week` | `*` or `tue` |
| `start`, `end` | local time HH:MM; empty means all day |
| `status` | `open`, `closed`, `closed_maintenance` |
| `source` | where the window came from; `field check pending` is not yet verified |

## Coordinate reference systems

- OSM and all CSV coordinates: WGS84 (EPSG:4326).
- DTM raster: HK 1980 Grid (EPSG:2326). Conversion happens inside `dtm.py`.
- Vertical datum: HKPD. It is never calibrated, because only relative
  differences are used and a common datum cancels.

## Rate of change

`data/raw/overpass_*.json` is frozen. The OSM data timestamp is in the companion
`.meta.json` and in `QUALITY_REPORT.md`. Numbers in the final report should cite
that timestamp, not "OpenStreetMap".
