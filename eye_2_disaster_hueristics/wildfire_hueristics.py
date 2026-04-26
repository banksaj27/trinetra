"""
Wildfire damage heuristic — ground sensor classifier.

Dataset: WiDS 2026 External Seasonal Wildfire Risk Features.csv

Note on dataset granularity: rows represent monthly regional aggregates
(Country / Region / month / year). When matching a disaster event to this
dataset the nearest matching region + month is used, so burned_area_ha and
fires_count reflect the full fire activity for that period.

Key columns selected:
  burned_area_ha   — Total hectares burned in the region-month. Primary damage
                     proxy: larger burned area → higher probability that any
                     specific infrastructure node within the region was destroyed.
                     Dataset range: 118–99,987 ha; used as the dominant weight.
  fires_count      — Number of distinct fire events. Used to compute per-fire
                     intensity (burned_area_ha / fires_count): a single large
                     fire is more destructive to a specific node than many small
                     scattered fires of the same aggregate area.
  temperature_c    — Ambient temperature (°C). High temp dries fuels and increases
                     flame intensity; above 40 °C fires spread significantly faster.
  humidity_percent — Relative humidity. Low humidity (<25%) dramatically increases
                     fire intensity and rate of spread.
  wind_speed_kmh   — Wind speed (km/h). Wind drives flame spread and ember
                     transport; above 50 km/h fire becomes difficult to contain.

Columns not used:
  Cause — Ignition source (Lightning, Human, etc.); does not directly affect
           damage severity once fire is burning at scale.
  Country / Region / year / month — Geographic/temporal filters; not severity signals.

Score construction (0–3):
  Burned area sub-score (weight 0.50):
    normalized as burned_area_ha / 100,000 (dataset max ≈ 100k ha) → 0–1

  Per-fire intensity sub-score (weight 0.20):
    intensity = burned_area_ha / max(fires_count, 1)
    normalized with ceiling 500 ha/fire → 0–1
    Captures whether a single large fire swept the area vs. many small ones.

  Environmental severity sub-score (weight 0.30):
    temp_score     = max(0, (temperature_c − 20) / 30)     clipped 0–1
                     (20 °C = neutral; 50 °C = fully dangerous)
    humidity_score = max(0, (60 − humidity_percent) / 60)  clipped 0–1
                     (60% = neutral; 0% = fully dangerous)
    wind_score     = min(wind_speed_kmh / 70, 1.0)
    env_score = (temp_score + humidity_score + wind_score) / 3

  composite = 0.50 * area_score + 0.20 * intensity_score + 0.30 * env_score
  score = composite * 3.0  → clipped to [0, 3]

Damage thresholds (approximate, given score distribution):
  [0.0, 0.9)  → no-damage
  [0.9, 1.8)  → minor-damage
  [1.8, 2.6)  → major-damage
  [2.6, 3.0]  → destroyed
"""

import math

CLASSES = ["no-damage", "minor-damage", "major-damage", "destroyed"]

_AREA_MAX       = 100_000.0   # ha — dataset ceiling
_INTENSITY_MAX  =    500.0    # ha/fire — above this, one large fire dominates
_TEMP_BASE      =     20.0    # °C — below this, minimal fire weather contribution
_TEMP_RANGE     =     30.0    # °C span to max danger (20 → 50 °C)
_HUMIDITY_BASE  =     60.0    # % — above this, humidity suppresses fire
_WIND_MAX       =     70.0    # km/h — dataset max


def _score_to_log_probs(score: float, sigma: float = 0.75) -> dict[str, float]:
    centers = [0.0, 1.0, 2.0, 3.0]
    raw = [math.exp(-0.5 * ((score - c) / sigma) ** 2) for c in centers]
    total = sum(raw)
    probs = [r / total for r in raw]
    return {cls: math.log(max(p, 1e-9)) for cls, p in zip(CLASSES, probs)}


def classify_wildfire(row: dict) -> tuple[str, dict[str, float]]:
    """
    Classify a single wildfire sensor/aggregate row into a damage category.

    Parameters
    ----------
    row : dict-like
        One row from the WiDS wildfire dataset (or equivalent sensor payload).

    Returns
    -------
    label : str
        Predicted damage class.
    log_probs : dict[str, float]
        Log-probability for each class.
    """
    burned_area = float(row.get("burned_area_ha") or 0.0)
    fires_count = max(1.0, float(row.get("fires_count") or 1.0))
    temperature = float(row.get("temperature_c") or 25.0)
    humidity = float(row.get("humidity_percent") or 50.0)
    wind_speed = float(row.get("wind_speed_kmh") or 20.0)

    # ── 1. Burned area sub-score ──────────────────────────────────────────────
    area_score = max(0.0, min(1.0, burned_area / _AREA_MAX))

    # ── 2. Per-fire intensity sub-score ───────────────────────────────────────
    # A single large fire sweeping through is more damaging to a specific node
    # than the same aggregate area spread across hundreds of small fires.
    intensity = burned_area / fires_count
    intensity_score = max(0.0, min(1.0, intensity / _INTENSITY_MAX))

    # ── 3. Environmental severity sub-score ───────────────────────────────────
    temp_score = max(0.0, min(1.0, (temperature - _TEMP_BASE) / _TEMP_RANGE))
    humidity_score = max(0.0, min(1.0, (_HUMIDITY_BASE - humidity) / _HUMIDITY_BASE))
    wind_score = max(0.0, min(1.0, wind_speed / _WIND_MAX))
    env_score = (temp_score + humidity_score + wind_score) / 3.0

    # ── Composite ─────────────────────────────────────────────────────────────
    composite = (
        0.50 * area_score
        + 0.20 * intensity_score
        + 0.30 * env_score
    )
    score = max(0.0, min(3.0, composite * 3.0))

    log_probs = _score_to_log_probs(score)
    label = max(log_probs, key=log_probs.get)
    return label, log_probs
