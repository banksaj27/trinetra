"""
Landslide damage heuristic — ground sensor classifier.

Dataset: landslide_dataset.csv

Key columns selected:
  Landslide           — Binary flag (1 = landslide occurred, 0 = no event).
                        Primary gate: a 0 always yields no-damage.
  Rainfall_mm         — Precipitation that triggered the event (50–300 mm in
                        dataset). Higher sustained rainfall → deeper, faster
                        mass movement → more destruction.
  Slope_Angle         — Terrain inclination in degrees (5–60°). Steeper slopes
                        produce higher velocity debris flows with more kinetic
                        energy at impact.
  Soil_Saturation     — Normalized 0–1. Saturated soils lose cohesion; near-1
                        values indicate full saturation → large, sudden failure.
  Earthquake_Activity — Seismic triggering index (0–6.5). Earthquake-triggered
                        landslides tend to be sudden and large-scale.

Columns not used:
  Vegetation_Cover    — Mitigates erosion but less discriminative once a
                        landslide has been triggered (already in Landslide=1 set).
  Proximity_to_Water  — Modulates deposit spread; too coarse for node damage.
  Soil_Type_*         — Marginally discriminative after Slope+Saturation captured.

Severity heuristic (applies only when Landslide == 1):
  Four features each mapped to 0–1, then weighted:
    Rainfall_mm        weight 0.35  (range 100–300 mm → 0–1)
    Slope_Angle        weight 0.30  (range 15–60° → 0–1)
    Soil_Saturation    weight 0.25  (already 0–1)
    Earthquake_Activity weight 0.10 (range 0–6.5 → 0–1)

  Composite ∈ [0, 1] mapped to severity score ∈ [1.0, 3.0]
  (minimum 1.0 ensures at least minor-damage when landslide confirmed):
    [1.0, 1.67) → minor-damage
    [1.67, 2.33) → major-damage
    [2.33, 3.0] → destroyed
"""

import math

CLASSES = ["no-damage", "minor-damage", "major-damage", "destroyed"]

# Normalization bounds derived from dataset percentiles
_RAIN_LOW  = 100.0   # mm — below this, rain contribution is negligible
_RAIN_HIGH = 300.0   # mm — dataset max
_SLOPE_LOW  = 15.0   # degrees — gentle slopes rarely produce fast flows
_SLOPE_HIGH = 60.0   # degrees — dataset max
_EQ_HIGH    =  6.5   # seismic index max in dataset


def _score_to_log_probs(score: float, sigma: float = 0.75) -> dict[str, float]:
    centers = [0.0, 1.0, 2.0, 3.0]
    raw = [math.exp(-0.5 * ((score - c) / sigma) ** 2) for c in centers]
    total = sum(raw)
    probs = [r / total for r in raw]
    return {cls: math.log(max(p, 1e-9)) for cls, p in zip(CLASSES, probs)}


def classify_landslide(row: dict) -> tuple[str, dict[str, float]]:
    """
    Classify a single landslide sensor row into a damage category.

    Parameters
    ----------
    row : dict-like
        One row from the landslide dataset.

    Returns
    -------
    label : str
        Predicted damage class.
    log_probs : dict[str, float]
        Log-probability for each class.
    """
    landslide_flag = int(float(row.get("Landslide") or 0))

    # No event → no-damage with high confidence
    if landslide_flag == 0:
        score = 0.0
        log_probs = _score_to_log_probs(score, sigma=0.4)
        return "no-damage", log_probs

    # ── Severity scoring when Landslide == 1 ─────────────────────────────────

    # Rainfall sub-score
    rain = float(row.get("Rainfall_mm") or _RAIN_LOW)
    rain_score = max(0.0, min(1.0, (rain - _RAIN_LOW) / (_RAIN_HIGH - _RAIN_LOW)))

    # Slope sub-score
    slope = float(row.get("Slope_Angle") or _SLOPE_LOW)
    slope_score = max(0.0, min(1.0, (slope - _SLOPE_LOW) / (_SLOPE_HIGH - _SLOPE_LOW)))

    # Soil saturation (already 0–1)
    saturation = float(row.get("Soil_Saturation") or 0.5)
    sat_score = max(0.0, min(1.0, saturation))

    # Earthquake activity sub-score
    eq = float(row.get("Earthquake_Activity") or 0.0)
    eq_score = max(0.0, min(1.0, eq / _EQ_HIGH))

    # Weighted composite → 0–1
    composite = (
        0.35 * rain_score
        + 0.30 * slope_score
        + 0.25 * sat_score
        + 0.10 * eq_score
    )

    # Map composite [0, 1] → severity score [1.0, 3.0]
    # Floor at 1.0: confirmed landslide always at least minor-damage.
    score = 1.0 + composite * 2.0
    score = max(1.0, min(3.0, score))

    log_probs = _score_to_log_probs(score)
    label = max(log_probs, key=log_probs.get)
    return label, log_probs
