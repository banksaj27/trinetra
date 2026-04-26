"""
Event-type classifier for the TriNetra pipeline.

Given a user-supplied latitude, longitude, and disaster date, queries each of the
five ground-sensor datasets in the order:
    flood → earthquake → storm → wildfire → (fallback) landslide

Returns the first disaster type whose dataset contains a matching record.
Landslide has no date/location data and is always the fallback.

Dataset column schemas (all files live in <repo_root>/event_type_data/):
  flood.csv      — FEMA declarations; incident_begin/end_date (ISO-8601), state abbrev
  earthquake.csv — USGS catalog; date_time ("%d-%m-%Y %H:%M"), latitude, longitude
  storm.csv      — FEMA declarations (Severe Storm / Hurricane / Tornado);
                   same schema as flood.csv
  wildfire.csv   — WiDS aggregate; year (int), month (int), Country (str)
  landslide.csv  — no date/location columns → fallback only
"""

from __future__ import annotations

import math
from datetime import date, datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path
from typing import Literal

import pandas as pd

EventType = Literal["earthquake", "flood", "storm", "wildfire", "landslide"]

_DATA_DIR = Path(__file__).resolve().parents[1] / "event_type_data"

# ── Matching tolerances ────────────────────────────────────────────────────────
_EQ_DISTANCE_KM   = 200   # earthquake epicentre search radius
_EQ_DATE_DAYS     = 1     # ± days around disaster_date for earthquake match
_FEMA_DATE_SLACK  = 3     # days of slack around FEMA incident begin/end dates
_WF_MONTH_SLACK   = 1     # ± months for wildfire year/month match

# ── US state bounding boxes (min_lat, max_lat, min_lon, max_lon) ──────────────
_US_STATE_BBOX: dict[str, tuple[float, float, float, float]] = {
    "AL": (30.14, 35.00, -88.47, -84.89),
    "AK": (54.67, 71.37, -168.00, -129.99),
    "AZ": (31.33, 37.00, -114.82, -109.04),
    "AR": (33.00, 36.50, -94.62, -89.64),
    "CA": (32.53, 42.01, -124.41, -114.13),
    "CO": (36.99, 41.00, -109.05, -102.04),
    "CT": (41.00, 42.05, -73.73, -71.79),
    "DE": (38.45, 39.84, -75.79, -75.05),
    "FL": (24.54, 31.00, -87.63, -80.03),
    "GA": (30.36, 35.00, -85.61, -80.84),
    "HI": (18.91, 22.24, -160.25, -154.81),
    "ID": (41.99, 49.00, -117.24, -111.04),
    "IL": (36.97, 42.51, -91.51, -87.50),
    "IN": (37.77, 41.76, -88.10, -84.79),
    "IA": (40.37, 43.50, -96.64, -90.14),
    "KS": (36.99, 40.00, -102.05, -94.59),
    "KY": (36.50, 39.15, -89.57, -81.96),
    "LA": (28.93, 33.02, -94.04, -89.00),
    "ME": (43.06, 47.46, -71.08, -66.95),
    "MD": (37.91, 39.72, -79.49, -74.98),
    "MA": (41.24, 42.88, -73.50, -69.93),
    "MI": (41.70, 48.31, -90.42, -82.41),
    "MN": (43.50, 49.38, -97.24, -89.49),
    "MS": (30.17, 35.00, -91.65, -88.10),
    "MO": (36.00, 40.61, -95.77, -89.10),
    "MT": (44.36, 49.00, -116.05, -104.04),
    "NE": (40.00, 43.00, -104.05, -95.31),
    "NV": (35.00, 42.00, -120.00, -114.04),
    "NH": (42.70, 45.30, -72.56, -70.61),
    "NJ": (38.93, 41.36, -75.56, -73.89),
    "NM": (31.33, 37.00, -109.05, -103.00),
    "NY": (40.50, 45.01, -79.76, -71.86),
    "NC": (33.84, 36.59, -84.32, -75.46),
    "ND": (45.93, 49.00, -104.05, -96.55),
    "OH": (38.40, 42.33, -84.82, -80.52),
    "OK": (33.62, 37.00, -103.00, -94.43),
    "OR": (41.99, 46.26, -124.57, -116.46),
    "PA": (39.72, 42.27, -80.52, -74.69),
    "RI": (41.15, 42.02, -71.89, -71.12),
    "SC": (32.05, 35.22, -83.36, -78.53),
    "SD": (42.49, 45.94, -104.06, -96.44),
    "TN": (34.98, 36.68, -90.31, -81.65),
    "TX": (25.84, 36.50, -106.65, -93.51),
    "UT": (36.99, 42.00, -114.05, -109.04),
    "VT": (42.73, 45.01, -73.44, -71.50),
    "VA": (36.54, 39.47, -83.68, -75.23),
    "WA": (45.54, 49.00, -124.73, -116.92),
    "WV": (37.20, 40.64, -82.64, -77.72),
    "WI": (42.49, 47.08, -92.89, -86.25),
    "WY": (41.00, 45.01, -111.05, -104.05),
    "DC": (38.79, 38.99, -77.12, -76.91),
    "PR": (17.91, 18.52, -67.27, -65.59),
    "VI": (17.68, 18.38, -65.05, -64.60),
    "GU": (13.26, 13.66, 144.62, 144.97),
    "AS": (-14.38, -11.05, -171.09, -168.15),
    "MP": (14.10, 20.56, 144.89, 146.06),
}

# ── Country bounding boxes for wildfire matching (min_lat, max_lat, min_lon, max_lon)
_COUNTRY_BBOX: dict[str, tuple[float, float, float, float]] = {
    "USA":          (18.91, 71.37, -168.00,  -66.90),
    "Turkey":       (35.81, 42.11,   25.66,   44.83),
    "Spain":        (36.00, 43.79,   -9.30,    4.34),
    "Portugal":     (36.96, 42.15,   -9.50,   -6.19),
    "Greece":       (34.80, 41.75,   19.37,   28.23),
    "Italy":        (35.49, 47.09,    6.63,   18.52),
    "France":       (42.33, 51.09,   -4.79,    8.23),
    "Australia":    (-43.64, -10.68, 113.34,  153.57),
    "Canada":       (41.68, 83.11, -140.99,  -52.62),
    "Mexico":       (14.53, 32.72, -117.13,  -86.71),
    "Brazil":       (-33.75, 5.27,  -73.99,  -28.85),
    "Argentina":    (-55.05, -21.78, -73.58,  -53.64),
    "Chile":        (-55.00, -17.49, -75.64,  -66.42),
    "South Africa": (-34.84, -22.12,  16.46,   32.89),
    "India":        ( 8.08,  37.10,   68.11,   97.40),
    "China":        (18.15,  53.56,   73.62,  134.77),
    "Russia":       (41.19,  81.86,   19.64,  -169.00),
    "Indonesia":    (-10.94,   5.91,   95.01,  141.02),
    "Japan":        (24.25,  45.55,  123.00,  145.82),
}


# ── Data loaders (cached for the lifetime of the server process) ───────────────

@lru_cache(maxsize=1)
def _load_flood() -> pd.DataFrame:
    path = _DATA_DIR / "flood.csv"
    if not path.exists():
        return pd.DataFrame()
    df = pd.read_csv(path, usecols=["incident_begin_date", "incident_end_date", "state"])
    df["_begin"] = pd.to_datetime(df["incident_begin_date"], utc=True, errors="coerce")
    df["_end"]   = pd.to_datetime(df["incident_end_date"],   utc=True, errors="coerce")
    return df.dropna(subset=["_begin"])


@lru_cache(maxsize=1)
def _load_earthquake() -> pd.DataFrame:
    path = _DATA_DIR / "earthquake.csv"
    if not path.exists():
        return pd.DataFrame()
    df = pd.read_csv(path, usecols=["date_time", "latitude", "longitude"])
    df["_dt"] = pd.to_datetime(df["date_time"], format="%d-%m-%Y %H:%M", errors="coerce")
    return df.dropna(subset=["_dt", "latitude", "longitude"])


@lru_cache(maxsize=1)
def _load_storm() -> pd.DataFrame:
    path = _DATA_DIR / "storm.csv"
    if not path.exists():
        return pd.DataFrame()
    df = pd.read_csv(path, usecols=["incident_begin_date", "incident_end_date", "state"])
    df["_begin"] = pd.to_datetime(df["incident_begin_date"], utc=True, errors="coerce")
    df["_end"]   = pd.to_datetime(df["incident_end_date"],   utc=True, errors="coerce")
    return df.dropna(subset=["_begin"])


@lru_cache(maxsize=1)
def _load_wildfire() -> pd.DataFrame:
    path = _DATA_DIR / "wildfire.csv"
    if not path.exists():
        return pd.DataFrame()
    return pd.read_csv(path, usecols=["year", "month", "Country"])


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


def _lat_lon_to_us_state(lat: float, lon: float) -> str | None:
    """Return the US state / territory abbreviation for a coordinate, or None."""
    for state, (min_lat, max_lat, min_lon, max_lon) in _US_STATE_BBOX.items():
        if min_lat <= lat <= max_lat and min_lon <= lon <= max_lon:
            return state
    return None


def _lat_lon_to_country(lat: float, lon: float) -> str | None:
    """Return a country name matching the wildfire dataset's Country column, or None."""
    for country, (min_lat, max_lat, min_lon, max_lon) in _COUNTRY_BBOX.items():
        if min_lat <= lat <= max_lat and min_lon <= lon <= max_lon:
            return country
    return None


# ── Per-dataset match functions ────────────────────────────────────────────────

def _match_flood(lat: float, lon: float, target: datetime) -> bool:
    df = _load_flood()
    if df.empty:
        return False
    state = _lat_lon_to_us_state(lat, lon)
    if state is None:
        return False
    slack = timedelta(days=_FEMA_DATE_SLACK)
    target_utc = target.replace(tzinfo=timezone.utc)
    state_rows = df[df["state"] == state]
    if state_rows.empty:
        return False
    begin = state_rows["_begin"]
    end   = state_rows["_end"].fillna(state_rows["_begin"] + timedelta(days=30))
    return bool(((begin - slack) <= target_utc).any() and (target_utc <= (end + slack)).any())


def _match_earthquake(lat: float, lon: float, target: datetime) -> bool:
    df = _load_earthquake()
    if df.empty:
        return False
    window_start = pd.Timestamp(target - timedelta(days=_EQ_DATE_DAYS), tz="UTC")
    window_end   = pd.Timestamp(target + timedelta(days=_EQ_DATE_DAYS), tz="UTC")
    dt_utc = df["_dt"].dt.tz_localize("UTC", ambiguous="NaT", nonexistent="NaT")
    in_window = df[(dt_utc >= window_start) & (dt_utc <= window_end)]
    if in_window.empty:
        return False
    distances = in_window.apply(
        lambda r: _haversine_km(lat, lon, r["latitude"], r["longitude"]), axis=1
    )
    return bool((distances <= _EQ_DISTANCE_KM).any())


def _match_storm(lat: float, lon: float, target: datetime) -> bool:
    df = _load_storm()
    if df.empty:
        return False
    state = _lat_lon_to_us_state(lat, lon)
    if state is None:
        return False
    slack = timedelta(days=_FEMA_DATE_SLACK)
    target_utc = target.replace(tzinfo=timezone.utc)
    state_rows = df[df["state"] == state]
    if state_rows.empty:
        return False
    begin = state_rows["_begin"]
    end   = state_rows["_end"].fillna(state_rows["_begin"] + timedelta(days=7))
    return bool(((begin - slack) <= target_utc).any() and (target_utc <= (end + slack)).any())


def _match_wildfire(lat: float, lon: float, target: datetime) -> bool:
    df = _load_wildfire()
    if df.empty:
        return False
    country = _lat_lon_to_country(lat, lon)
    if country is None:
        return False
    country_rows = df[df["Country"] == country]
    if country_rows.empty:
        return False
    # Allow ±1 month window
    candidates = []
    for delta in range(-_WF_MONTH_SLACK, _WF_MONTH_SLACK + 1):
        shifted = target.month + delta
        y_off, m = divmod(shifted - 1, 12)
        candidates.append((target.year + y_off, m + 1))
    for yr, mo in candidates:
        if not country_rows[(country_rows["year"] == yr) & (country_rows["month"] == mo)].empty:
            return True
    return False


# ── Public API ─────────────────────────────────────────────────────────────────

def classify_event(lat: float, lon: float, disaster_date_str: str) -> EventType:
    """
    Identify the disaster event type for a given location and date.

    Searches datasets in order: flood → earthquake → storm → wildfire.
    Falls back to 'landslide' if no dataset has a matching record.

    Parameters
    ----------
    lat, lon          : WGS-84 coordinates of the area of interest.
    disaster_date_str : ISO-8601 date string ("YYYY-MM-DD").

    Returns
    -------
    One of: "earthquake", "flood", "storm", "wildfire", "landslide"
    """
    d = date.fromisoformat(disaster_date_str)
    target = datetime(d.year, d.month, d.day, 12, 0, 0)  # noon UTC as canonical time

    if _match_flood(lat, lon, target):
        return "flood"
    if _match_earthquake(lat, lon, target):
        return "earthquake"
    if _match_storm(lat, lon, target):
        return "storm"
    if _match_wildfire(lat, lon, target):
        return "wildfire"
    return "landslide"
