"""Map each station to its area: a Paris arrondissement or a suburban commune.

Communes come from a point-in-polygon test against official commune boundaries
(data/reference/communes.geojson, from github.com/gregoiredavid/france-geojson,
based on IGN / INSEE open data). Inside Paris, the arrondissement is read from the
Vélib' station code: its leading digits are the arrondissement (16107 -> 16e).
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

import numpy as np
import pandas as pd
from shapely import STRtree
from shapely.geometry import Point, shape

REFERENCE = Path(__file__).resolve().parents[2] / "data" / "reference" / "communes.geojson"
PARIS_INSEE = "75056"


@lru_cache(maxsize=1)
def _communes() -> tuple[list[dict], list, STRtree]:
    features = json.loads(REFERENCE.read_text(encoding="utf-8"))["features"]
    geoms = [shape(f["geometry"]) for f in features]
    return [f["properties"] for f in features], geoms, STRtree(geoms)


def _commune_for(lon: float, lat: float) -> dict | None:
    props, geoms, tree = _communes()
    point = Point(lon, lat)
    hits = tree.query(point, predicate="within")
    if len(hits):
        return props[int(hits[0])]
    # Station just outside a (simplified) boundary: take the nearest commune.
    nearest = tree.nearest(point)
    return props[int(nearest)] if nearest is not None else None


def _arrondissement(code: str | None) -> int | None:
    try:
        arr = int(str(code)) // 1000
    except (TypeError, ValueError):
        return None
    return arr if 1 <= arr <= 20 else None


def add_area(stations: pd.DataFrame) -> pd.DataFrame:
    """Add insee_code, commune, department, arrondissement and a readable `area` label."""
    out = stations.copy()
    rows = []
    for st in out.itertuples(index=False):
        commune = _commune_for(st.lon, st.lat) if pd.notna(st.lat) else None
        insee = commune["code"] if commune else None
        name = commune["nom"] if commune else "Unknown"
        arr = _arrondissement(st.station_code) if insee == PARIS_INSEE else None
        if insee == PARIS_INSEE:
            area = f"Paris {arr}{'er' if arr == 1 else 'e'}" if arr else "Paris (other)"
        else:
            area = name
        rows.append(
            {
                "insee_code": insee,
                "commune": name,
                "department": insee[:2] if insee else None,
                "arrondissement": arr,
                "area": area,
                "in_paris": insee == PARIS_INSEE,
            }
        )
    extra = pd.DataFrame(rows, index=out.index)
    extra["arrondissement"] = extra["arrondissement"].astype("Int8")
    return pd.concat([out, extra], axis=1)


def haversine_km(lat1, lon1, lat2, lon2):
    """Great-circle distance in km (works on scalars or numpy arrays)."""
    lat1, lon1, lat2, lon2 = map(np.radians, (lat1, lon1, lat2, lon2))
    a = np.sin((lat2 - lat1) / 2) ** 2 + np.cos(lat1) * np.cos(lat2) * np.sin((lon2 - lon1) / 2) ** 2
    return 2 * 6371.0 * np.arcsin(np.sqrt(a))
