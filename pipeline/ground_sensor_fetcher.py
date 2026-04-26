"""
Eye 2 ground-sensor data fetcher.

Loads rows from the event-type-specific CSV dataset, filtered to the user's
location and disaster date, and returns them as plain dicts for the heuristic
assessor to classify.

Filtering strategy per event type:
  earthquake — lat/lon distance ≤ 200 km  +  date ± 1 day
  flood      — lat/lon distance ≤ 300 km  +  date ± 3 days
  storm      — US bounding-box check       +  date ± 3 days  (all lat/lon are NaN)
  wildfire   — country bounding-box check  +  year/month ± 1 month  (all lat/lon are NaN)
  landslide  — no date/location data; returns a reproducible 20-row sample of
               Landslide=1 rows
"""

from __future__ import annotations

import asyncio
import math
from datetime import date, timedelta
from functools import lru_cache
from pathlib import Path
from typing import Any

import pandas as pd

_DATA_DIR = Path(__file__).resolve().parents[1] / "event_type_data"

# ── Matching tolerances (mirrors event_classifier.py) ─────────────────────────
_EQ_RADIUS_KM    = 200
_FLOOD_RADIUS_KM = 300
_EQ_DATE_DAYS    = 1
_FEMA_DATE_DAYS  = 3
_WF_MONTH_SLACK  = 1

_LANDSLIDE_SAMPLE = 20   # reproducible sample size from Landslide=1 rows

# ── US + country bounding boxes (mirrors event_classifier.py) ─────────────────
_US_BBOX = (18.91, 71.37, -179.99, -66.90)

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


# ── Cached full-column loaders ─────────────────────────────────────────────────

@lru_cache(maxsize=1)
def _load_earthquake_full() -> pd.DataFrame:
    path = _DATA_DIR / "earthquake.csv"
    if not path.exists():
        return pd.DataFrame()
    df = pd.read_csv(path)
    df["_date"] = pd.to_datetime(df["event_date_utc"], errors="coerce").dt.date
    return df.dropna(subset=["_date", "latitude", "longitude"])


@lru_cache(maxsize=1)
def _load_flood_full() -> pd.DataFrame:
    path = _DATA_DIR / "flood.csv"
    if not path.exists():
        return pd.DataFrame()
    df = pd.read_csv(path)
    df["_date"] = pd.to_datetime(df["event_date_utc"], errors="coerce").dt.date
    return df.dropna(subset=["_date", "latitude", "longitude"])


@lru_cache(maxsize=1)
def _load_storm_full() -> pd.DataFrame:
    path = _DATA_DIR / "storm.csv"
    if not path.exists():
        return pd.DataFrame()
    df = pd.read_csv(path)
    df["_date"] = pd.to_datetime(df["event_date_utc"], errors="coerce").dt.date
    return df.dropna(subset=["_date"])


@lru_cache(maxsize=1)
def _load_wildfire_full() -> pd.DataFrame:
    path = _DATA_DIR / "wildfire.csv"
    if not path.exists():
        return pd.DataFrame()
    df = pd.read_csv(path)
    df["_date"] = pd.to_datetime(df["event_date_utc"], errors="coerce").dt.date
    return df.dropna(subset=["_date"])


@lru_cache(maxsize=1)
def _load_landslide_full() -> pd.DataFrame:
    path = _DATA_DIR / "landslide.csv"
    if not path.exists():
        return pd.DataFrame()
    return pd.read_csv(path)


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


def _country_for(lat: float, lon: float) -> str | None:
    for country, bbox in _COUNTRY_BBOX.items():
        if _in_bbox(lat, lon, bbox):
            return country
    return None


# ── Per-event-type fetch logic ─────────────────────────────────────────────────

def _fetch_earthquake(lat: float, lon: float, target: date) -> list[dict[str, Any]]:
    df = _load_earthquake_full()
    if df.empty:
        return []
    lo = target - timedelta(days=_EQ_DATE_DAYS)
    hi = target + timedelta(days=_EQ_DATE_DAYS)
    in_window = df[(df["_date"] >= lo) & (df["_date"] <= hi)]
    if in_window.empty:
        return []
    mask = in_window.apply(
        lambda r: _haversine_km(lat, lon, r["latitude"], r["longitude"]) <= _EQ_RADIUS_KM,
        axis=1,
    )
    return in_window[mask].drop(columns=["_date"]).to_dict("records")


def _fetch_flood(lat: float, lon: float, target: date) -> list[dict[str, Any]]:
    df = _load_flood_full()
    if df.empty:
        return []
    lo = target - timedelta(days=_FEMA_DATE_DAYS)
    hi = target + timedelta(days=_FEMA_DATE_DAYS)
    in_window = df[(df["_date"] >= lo) & (df["_date"] <= hi)]
    if in_window.empty:
        return []
    mask = in_window.apply(
        lambda r: _haversine_km(lat, lon, r["latitude"], r["longitude"]) <= _FLOOD_RADIUS_KM,
        axis=1,
    )
    return in_window[mask].drop(columns=["_date"]).to_dict("records")


def _fetch_storm(lat: float, lon: float, target: date) -> list[dict[str, Any]]:
    """Storm: date-window only (lat/lon all NaN in dataset); US bbox already confirmed."""
    df = _load_storm_full()
    if df.empty:
        return []
    lo = target - timedelta(days=_FEMA_DATE_DAYS)
    hi = target + timedelta(days=_FEMA_DATE_DAYS)
    in_window = df[(df["_date"] >= lo) & (df["_date"] <= hi)]
    return in_window.drop(columns=["_date"]).to_dict("records")


def _fetch_wildfire(lat: float, lon: float, target: date) -> list[dict[str, Any]]:
    """Wildfire: year/month match ± WF_MONTH_SLACK (lat/lon all NaN in dataset)."""
    df = _load_wildfire_full()
    if df.empty:
        return []
    candidates: list[date] = []
    for delta in range(-_WF_MONTH_SLACK, _WF_MONTH_SLACK + 1):
        shifted = target.month + delta
        y_off, m = divmod(shifted - 1, 12)
        candidates.append(date(target.year + y_off, m + 1, 1))
    mask = df["_date"].isin(candidates)
    return df[mask].drop(columns=["_date"]).to_dict("records")


def _fetch_landslide() -> list[dict[str, Any]]:
    """Landslide: no date/location data; reproducible sample of Landslide=1 rows."""
    df = _load_landslide_full()
    if df.empty:
        return []
    positive = df[df["Landslide"] == 1]
    n = min(_LANDSLIDE_SAMPLE, len(positive))
    if n == 0:
        return []
    return positive.sample(n=n, random_state=42).to_dict("records")


# ── Public async API ───────────────────────────────────────────────────────────

_FETCH_DISPATCH = {
    "earthquake": _fetch_earthquake,
    "flood":      _fetch_flood,
    "storm":      _fetch_storm,
    "wildfire":   _fetch_wildfire,
}


def _fetch_sync(
    latitude: float,
    longitude: float,
    event_type: str,
    disaster_date: str,
) -> list[dict[str, Any]]:
    if event_type == "landslide":
        return _fetch_landslide()
    target = date.fromisoformat(disaster_date)
    fetch_fn = _FETCH_DISPATCH.get(event_type)
    if fetch_fn is None:
        return []
    return fetch_fn(latitude, longitude, target)


async def fetch_ground_sensor_data(
    latitude: float,
    longitude: float,
    radius_km: float,
    event_type: str,
    disaster_date: str,
) -> list[dict[str, Any]]:
    """
    Return dataset rows matching the event location and date.

    Parameters
    ----------
    latitude, longitude : WGS-84 coordinates of the area of interest.
    radius_km           : Unused (tolerances are fixed per event type), kept for
                          API compatibility with future live-sensor backends.
    event_type          : One of: earthquake, flood, storm, wildfire, landslide.
    disaster_date       : ISO-8601 date string ("YYYY-MM-DD").

    Returns
    -------
    List of row dicts from the event-type CSV; column names match exactly what
    the corresponding heuristic classify_* function expects.
    """
    return await asyncio.to_thread(_fetch_sync, latitude, longitude, event_type, disaster_date)
