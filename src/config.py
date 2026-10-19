"""Central configuration for the M2 data pipeline.

Everything that a reader might want to change lives here: the bounding box,
the mirror list, the OSM tag -> edge type mapping, and the facility facts that
come from primary documents rather than from OpenStreetMap.
"""

from __future__ import annotations

import os
from pathlib import Path

# --------------------------------------------------------------------------
# Paths
# --------------------------------------------------------------------------
PROJECT_DIR = Path(__file__).resolve().parent.parent
RAW_DIR = PROJECT_DIR / "data" / "raw"
OUT_DIR = PROJECT_DIR / "data" / "out"
DERIVED_DIR = PROJECT_DIR / "data" / "derived"

for _d in (RAW_DIR, OUT_DIR, DERIVED_DIR):
    _d.mkdir(parents=True, exist_ok=True)

# --------------------------------------------------------------------------
# Study area
# --------------------------------------------------------------------------
# Plan section 2.3. (south, west, north, east)
BBOX = (22.276, 114.124, 22.294, 114.152)

# Rough corridor extent used for the sanity check on total rise.
CORRIDOR_LABEL = "Third Street -> Centre Street Escalator -> Bonham Road -> HKU East Gate"

# --------------------------------------------------------------------------
# Network
# --------------------------------------------------------------------------
# Connectivity notes (checked 2026-10-19 from this network):
#   overpass-api.de and its mirrors  -> HTTP 502 at the proxy
#   overpass.osm.ch                  -> reachable but only carries a Swiss
#                                       extract, returns 0 ways for Hong Kong
#   overpass.openstreetmap.fr        -> works, global data, but rejects POST
#                                       and generic user agents with 403;
#                                       needs GET + UA "overpass-turbo"
# So the French mirror comes first and the request is sent as GET.
ENDPOINTS = [
    "https://overpass.openstreetmap.fr/api/interpreter",
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.osm.ch/api/interpreter",
]

# The corporate proxy is dynamic: never hardcode the port.
PROXY = os.environ.get("HTTPS_PROXY") or os.environ.get("HTTP_PROXY")
USER_AGENT = "overpass-turbo"

REQUEST_TIMEOUT = 300
MAX_RETRIES = 2

# --------------------------------------------------------------------------
# OSM tag -> edge class
# --------------------------------------------------------------------------
# Three classes are required by the plan (level/slope, stairs, escalator/lift).
# Road classes are kept as a fourth bucket because they are needed for graph
# connectivity between the pedestrian links; they are flagged separately so
# that the cost model can decide what to do with them.
HIGHWAY_QUERY_VALUES = [
    "footway", "path", "steps", "pedestrian", "living_street", "corridor",
    "residential", "service", "unclassified", "tertiary", "secondary",
    "primary", "track", "elevator",
]

# highway values that count as pedestrian infrastructure on their own.
PEDESTRIAN_HIGHWAYS = {
    "footway", "path", "steps", "pedestrian", "living_street", "corridor",
}

# highway values that are carriageways (may still carry a footway).
ROAD_HIGHWAYS = {
    "residential", "service", "unclassified", "tertiary", "secondary",
    "primary", "track",
}

# A non-stair edge steeper than this is typed "slope" rather than "level".
SLOPE_GRADIENT_THRESHOLD = 0.03  # 3 %

# Stairs and moving devices keep their geometric turning points as graph
# nodes, but not every OSM vertex: a turn smaller than this is just a
# digitising artefact, and a segment longer than MAX_SEGMENT_M is split so
# that the cost model never sees a stair flight as one 90 m edge.
STAIR_TURN_DEG = 20.0
MAX_SEGMENT_M = 25.0

# Default riser height used only when OSM gives no step_count.
DEFAULT_RISER_M = 0.165

# Conveying devices are structures, not terrain. The 5 m DTM returns the
# ground surface *above* the shaft, so a device that runs inside a station
# box, under a bridge or in a tunnel comes back flat: checked on the C1
# escalator pair at HKU station, both ends of a 22.6 m flight sample the same
# 59.0 m cell, giving 3 cm of rise. Where the terrain claims less than
# DEVICE_MIN_GRADE over more than DEVICE_MIN_LENGTH_M the device is rebuilt
# from its design inclination instead (EN 115-1: 30 deg standard, 35 deg for
# high-speed units). Documented facilities still win over this assumption.
ESCALATOR_DESIGN_INCLINE_DEG = 30.0
DEVICE_MIN_GRADE = 0.10
DEVICE_MIN_LENGTH_M = 4.0
# Where the rebuild is allowed. Only structures that are known to sit under
# or inside something: the DTM sampled there is about the ground beside the
# device, not about the device. An escalator that is simply steep hillside
# (the open-air flights) is left to the terrain, and so is one that crosses
# above a road, because the ground under a bridge is a different surface.
DEVICE_FACILITIES = ("F_MTR_HKU", "F_HKU_EAST_GATE", "F_MTR_SYP")
# The interior end of a rebuilt device is the only free end, so it is given
# extra weight against its noisy (and equally blind) neighbours.
DEVICE_WEIGHT_FACTOR = 6.0

# --------------------------------------------------------------------------
# Facility facts taken from primary documents (NOT from OSM)
# --------------------------------------------------------------------------
# These values are the authority of record for the plan's structural claim.
# OSM is allowed to disagree; the override table wins and the disagreement is
# logged to the quality report.
FACILITY_OVERRIDES = [
    {
        "facility_id": "F_CENTRE_ST_ESC",
        "name": "Centre Street Escalator Link",
        "class": "escalator",
        "total_length_m": 85.0,
        "total_rise_m": 27.0,
        # Up: all 85 m assisted. Down: only 25 m assisted (one bidirectional
        # section). Source: Central & Western District Council TTC paper
        # 2018-02-01, annex items 40-41.
        "direction": "asymmetric",
        "direction_note": "uphill fully assisted; downhill only ~25 m assisted",
        "source_doc": "DC-TTC-2018-02-01 (annex items 40-41)",
        # Match on the OSM name, not only on a box. Every conveying way of the
        # link is tagged name=正街 Centre Street, and the MTR Sai Ying Pun
        # station escalators that share the same block are not -- the box alone
        # swallowed 118 m of station circulators and charged them the published
        # gradient. The box is kept as a guard so an unrelated 正街 way further
        # along the street cannot be claimed.
        "match_name": "正街",
        "match_bbox": (22.2838, 114.1405, 22.2880, 114.1435),
        "match_classes": ("escalator", "travelator"),
    },
    {
        # The station circulators the old box was catching. Kept as their own
        # facility because they are a separate thing to a traveller -- a ride
        # inside the station, on the station's hours -- and because they sit at
        # the corridor's western end (exits B1 First Street, B2 Second Street,
        # B3 Ki Ling Lane).
        "facility_id": "F_MTR_SYP",
        "name": "MTR Sai Ying Pun Station (exits B1/B2/B3)",
        "class": "access_point",
        "total_length_m": None,
        "total_rise_m": None,
        "direction": "both",
        "direction_note": "concourse escalators to exits B1/B2/B3",
        "source_doc": "MTR Corporation",
        "match_bbox": (22.2844, 114.1412, 22.2880, 114.1436),
        "match_classes": ("escalator", "travelator", "lift"),
    },
    {
        "facility_id": "F_HKU_EAST_GATE",
        "name": "HKU East Gate (Bonham Road)",
        "class": "mixed_escalator_lift",
        "total_length_m": None,
        "total_rise_m": None,
        "direction": "both",
        "direction_note": "2 escalators + 3 lifts; reopened 2025-03-25",
        "source_doc": "HKU news release 2025-03-25",
        "match_bbox": (22.2810, 114.1350, 22.2860, 114.1420),
        "match_classes": ("escalator", "travelator", "lift"),
    },
    {
        # Not a facility in the engineering sense: it is an access point that
        # bounds the corridor. It carries an operating window, so it needs a
        # row here or windows.csv would point at nothing.
        "facility_id": "F_MTR_HKU",
        "name": "MTR HKU Station (exits A1/A2/C1)",
        "class": "access_point",
        "total_length_m": None,
        "total_rise_m": None,
        "direction": "both",
        "direction_note": "8 lifts at A1/A2, 4 at C1; one lift open 24 h for students; "
                          "station escalators run within station hours",
        "source_doc": "MTR Corporation",
        "match_bbox": (22.2815, 114.1325, 22.2865, 114.1390),
        # Only the station's own vertical circulation. Wider classes would
        # sweep in the East Gate devices, which are a separate facility.
        "match_classes": ("lift", "escalator", "travelator"),
    },
]

# Operating windows, expressed as rows so that the plan's three scenarios are
# just selections over the table. Times are local (HKT).
WINDOWS = [
    # facility_id, scenario, day_of_week, start, end, status, source
    ("F_CENTRE_ST_ESC", "normal", "*", "06:30", "00:15", "open", "HK Road Wiki / TD"),
    ("F_CENTRE_ST_ESC", "maintenance", "tue", "12:00", "15:00", "closed_maintenance", "HK Road Wiki"),
    ("F_CENTRE_ST_ESC", "closed_night", "*", "00:15", "06:30", "closed", "HK Road Wiki"),
    ("F_HKU_EAST_GATE", "normal", "*", None, None, "open", "field check pending"),
    ("F_MTR_HKU", "normal", "*", "05:51", "01:20", "open", "MTR"),
    # Station hours are published as first/last train, not as a door time, and
    # the circulators stop with the station. Left open-ended until checked.
    ("F_MTR_SYP", "normal", "*", None, None, "open", "MTR - hours to confirm"),
]
