"""
Event-type classifier for the TriNetra pipeline.

Given a user-supplied latitude, longitude, and disaster date, queries each of the
five ground-sensor datasets in the order:
    flood → earthquake → storm → wildfire → (fallback) landslide

Returns the first disaster type whose dataset contains a matching record.
Landslide has no date/location data and is always the fallback.

Dataset schemas (all files live in <repo_root>/event_type_data/):
  flood.csv      — FEMA flood declarations; event_date_utc (YYYY-MM-DD),
                   latitude, longitude (county/state centroids, ~99.9% coverage)
  earthquake.csv — USGS catalog; event_date_utc (YYYY-MM-DD),
                   latitude, longitude (epicentre, precise)
  storm.csv      — FEMA storm declarations (Severe Storm / Hurricane / Tornado);
                   event_date_utc (YYYY-MM-DD); latitude/longitude all NaN
                   → matched by date + US bounding-box check
  wildfire.csv   — WiDS monthly aggregate; event_date_utc is always YYYY-MM-01;
                   latitude/longitude all NaN
                   → matched by year/month + country bounding-box check
  landslide.csv  — no date/location columns → fallback only
"""

from __future__ import annotations

import math
from datetime import date, datetime, timedelta
from functools import lru_cache
from pathlib import Path
from typing import Literal

import pandas as pd

EventType = Literal["earthquake", "flood", "storm", "wildfire", "landslide"]

_DATA_DIR = Path(__file__).resolve().parents[1] / "event_type_data"

# ── Matching tolerances ────────────────────────────────────────────────────────
_FLOOD_RADIUS_KM  = 300   # FEMA coords are county/state centroids, not exact location
_EQ_RADIUS_KM     = 200   # USGS epicentre; large quakes cause damage far from source
_FEMA_DATE_DAYS   = 3     # ± days for flood / storm date matching
_EQ_DATE_DAYS     = 1     # ± days for earthquake date matching
_WF_MONTH_SLACK   = 1     # ± months for wildfire monthly-aggregate matching

# ── Continental US + territories bounding box (for storm matching) ─────────────
_US_BBOX = (18.91, 71.37, -179.99, -66.90)   # (min_lat, max_lat, min_lon, max_lon)

# ── Country bounding boxes for wildfire matching (min_lat, max_lat, min_lon, max_lon)
_COUNTRY_BBOX: dict[str, tuple[float, float, float, float]] = {
    "USA":          (18.91, 71.37, -179.99,  -66.90),
    "Turkey":       (35.81, 42.11,   25.66,   44.83),
    "Spain":        (36.00, 43.79,   -9.30,    4.34),
    "Portugal":     (36.96, 42.15,   -9.50,   -6.19),
    "Greece":       (34.80, 41.75,   19.37,   28.23),
    "Italy":        (35.49, 47.09,    6.63,   18.52),
    "France":       (42.33, 51.09,   -4.79,    8.23),
    "Australia":    (-43.64, -10.68, 113.34,  153.57),
    "Canada":       (41.68, 83.11,  -141.00,  -52.62),
    "Mexico":       (14.53, 32.72,  -117.13,  -86.71),
    "Brazil":       (-33.75,  5.27,  -73.99,  -28.85),
    "Argentina":    (-55.05, -21.78, -73.58,  -53.64),
    "Chile":        (-55.00, -17.49, -75.64,  -66.42),
    "South Africa": (-34.84, -22.12,  16.46,   32.89),
    "India":        (  8.08,  37.10,  68.11,   97.40),
    "China":        ( 18.15,  53.56,  73.62,  134.77),
    "Indonesia":    (-10.94,   5.91,  95.01,  141.02),
    "Japan":        ( 24.25,  45.55, 123.00,  145.82),
}


# ── Data loaders (module-level cache; loaded once per server process) ──────────

@lru_cache(maxsize=1)
def _load_flood() -> pd.DataFrame:
    path = _DATA_DIR / "flood.csv"
    if not path.exists():
        return pd.DataFrame(columns=["event_date_utc", "latitude", "longitude"])
    df = pd.read_csv(path, usecols=["event_date_utc", "latitude", "longitude"])
    df["_date"] = pd.to_datetime(df["event_date_utc"], errors="coerce").dt.date
    return df.dropna(subset=["_date", "latitude", "longitude"])


@lru_cache(maxsize=1)
def _load_earthquake() -> pd.DataFrame:
    path = _DATA_DIR / "earthquake.csv"
    if not path.exists():
        return pd.DataFrame(columns=["event_date_utc", "latitude", "longitude"])
    df = pd.read_csv(path, usecols=["event_date_utc", "latitude", "longitude"])
    df["_date"] = pd.to_datetime(df["event_date_utc"], errors="coerce").dt.date
    return df.dropna(subset=["_date", "latitude", "longitude"])


@lru_cache(maxsize=1)
def _load_storm() -> pd.DataFrame:
    path = _DATA_DIR / "storm.csv"
    if not path.exists():
        return pd.DataFrame(columns=["event_date_utc"])
    df = pd.read_csv(path, usecols=["event_date_utc"])
    df["_date"] = pd.to_datetime(df["event_date_utc"], errors="coerce").dt.date
    return df.dropna(subset=["_date"])


@lru_cache(maxsize=1)
def _load_wildfire() -> pd.DataFrame:
    path = _DATA_DIR / "wildfire.csv"
    if not path.exists():
        return pd.DataFrame(columns=["event_date_utc"])
    df = pd.read_csv(path, usecols=["event_date_utc"])
    df["_date"] = pd.to_datetime(df["event_date_utc"], errors="coerce").dt.date
    return df.dropna(subset=["_date"])


# ── Spatial helpers ────────────────────────────────────────────────────────────

def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    R = 6_371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (
        math.sin(dlat / 2) ** 2
        + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2) ** 2
    )
    return 2 * R * math.asin(math.sqrt(max(0.0, min(1.0, a))))


def _in_bbox(lat: float, lon: float, bbox: tuple[float, float, float, float]) -> bool:
    min_lat, max_lat, min_lon, max_lon = bbox
    return min_lat <= lat <= max_lat and min_lon <= lon <= max_lon


def _lat_lon_to_country(lat: float, lon: float) -> str | None:
    for country, bbox in _COUNTRY_BBOX.items():
        if _in_bbox(lat, lon, bbox):
            return country
    return None


# ── Per-dataset match functions ────────────────────────────────────────────────

def _match_flood(lat: float, lon: float, target: date) -> bool:
    """Flood: precise lat/lon centroid + date within ±FEMA_DATE_DAYS."""
    df = _load_flood()
    if df.empty:
        return False
    lo, hi = target - timedelta(days=_FEMA_DATE_DAYS), target + timedelta(days=_FEMA_DATE_DAYS)
    in_window = df[(df["_date"] >= lo) & (df["_date"] <= hi)]
    if in_window.empty:
        return False
    distances = in_window.apply(
        lambda r: _haversine_km(lat, lon, r["latitude"], r["longitude"]), axis=1
    )
    return bool((distances <= _FLOOD_RADIUS_KM).any())


def _match_earthquake(lat: float, lon: float, target: date) -> bool:
    """Earthquake: precise epicentre lat/lon + date within ±EQ_DATE_DAYS."""
    df = _load_earthquake()
    if df.empty:
        return False
    lo, hi = target - timedelta(days=_EQ_DATE_DAYS), target + timedelta(days=_EQ_DATE_DAYS)
    in_window = df[(df["_date"] >= lo) & (df["_date"] <= hi)]
    if in_window.empty:
        return False
    distances = in_window.apply(
        lambda r: _haversine_km(lat, lon, r["latitude"], r["longitude"]), axis=1
    )
    return bool((distances <= _EQ_RADIUS_KM).any())


def _match_storm(lat: float, lon: float, target: date) -> bool:
    """Storm: date within ±FEMA_DATE_DAYS + user coordinates within US/territories."""
    if not _in_bbox(lat, lon, _US_BBOX):
        return False
    df = _load_storm()
    if df.empty:
        return False
    lo, hi = target - timedelta(days=_FEMA_DATE_DAYS), target + timedelta(days=_FEMA_DATE_DAYS)
    return bool(((df["_date"] >= lo) & (df["_date"] <= hi)).any())


def _match_wildfire(lat: float, lon: float, target: date) -> bool:
    """Wildfire: year/month (±WF_MONTH_SLACK) + country bounding-box check."""
    country = _lat_lon_to_country(lat, lon)
    if country is None:
        return False
    df = _load_wildfire()
    if df.empty:
        return False
    # Build (year, month) candidates within ±WF_MONTH_SLACK months
    candidates: list[tuple[int, int]] = []
    for delta in range(-_WF_MONTH_SLACK, _WF_MONTH_SLACK + 1):
        shifted = target.month + delta
        y_off, m = divmod(shifted - 1, 12)
        candidates.append((target.year + y_off, m + 1))
    for yr, mo in candidates:
        match_date = date(yr, mo, 1)
        if (df["_date"] == match_date).any():
            return True
    return False


# ── Public API ─────────────────────────────────────────────────────────────────

def classify_event(lat: float, lon: float, disaster_date_str: str) -> EventType:
    """
    Identify the disaster event type for a given location and date.

    Searches datasets in priority order: flood → earthquake → storm → wildfire.
    Falls back to 'landslide' if no dataset has a matching record.

    Parameters
    ----------
    lat, lon          : WGS-84 coordinates of the area of interest.
    disaster_date_str : ISO-8601 date string ("YYYY-MM-DD").

    Returns
    -------
    One of: "flood", "earthquake", "storm", "wildfire", "landslide"
    """
    target = date.fromisoformat(disaster_date_str)

    if _match_flood(lat, lon, target):
        return "flood"
    if _match_earthquake(lat, lon, target):
        return "earthquake"
    if _match_storm(lat, lon, target):
        return "storm"
    if _match_wildfire(lat, lon, target):
        return "wildfire"
    return "landslide"
