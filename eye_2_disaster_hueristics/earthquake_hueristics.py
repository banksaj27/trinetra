"""
Earthquake damage heuristic — ground sensor classifier.

Dataset: USGS earthquake catalog (earthquake_1995-2023.csv / earthquake_data.csv)

Key columns selected:
  mmi       — Modified Mercalli Intensity (1–10): best proxy for shaking-driven
               structural damage at a specific location. MMI ≤ 4 = no structural
               damage; MMI 10 = catastrophic/total destruction.
  alert     — USGS PAGER alert (green/yellow/orange/red): pre-computed population
               impact estimate, used as a 40% confidence blend with MMI.
  depth     — Hypocentral depth (km): shallow quakes (<30 km) transmit far more
               energy to the surface; deep quakes (>150 km) attenuate severely.
  magnitude — Moment magnitude: energy scale; secondary weight above M 7.5.
  sig       — USGS significance score: composite metric, used as tiebreaker.

Damage classification bins (0–3 continuous score → 4 classes):
  [0.0, 1.0)  → no-damage       (MMI ≤ ~5, green alert)
  [1.0, 2.0)  → minor-damage    (MMI 6–7, yellow alert, moderate shaking)
  [2.0, 3.0)  → major-damage    (MMI 8–9, orange alert, severe shaking)
  [3.0, ∞)    → destroyed       (MMI 10, red alert, extreme shaking)
"""

import math

CLASSES = ["no-damage", "minor-damage", "major-damage", "destroyed"]

_ALERT_SCORE = {"green": 0.0, "yellow": 1.0, "orange": 2.0, "red": 3.0}


def _score_to_log_probs(score: float, sigma: float = 0.75) -> dict[str, float]:
    """Gaussian soft-assignment: continuous 0–3 score → log-probabilities."""
    centers = [0.0, 1.0, 2.0, 3.0]
    raw = [math.exp(-0.5 * ((score - c) / sigma) ** 2) for c in centers]
    total = sum(raw)
    probs = [r / total for r in raw]
    return {cls: math.log(max(p, 1e-9)) for cls, p in zip(CLASSES, probs)}


def classify_earthquake(row: dict) -> tuple[str, dict[str, float]]:
    """
    Classify a single earthquake sensor row into a damage category.

    Parameters
    ----------
    row : dict-like
        One row from the earthquake dataset (or equivalent sensor payload).

    Returns
    -------
    label : str
        Predicted damage class.
    log_probs : dict[str, float]
        Log-probability for each class.
    """
    mmi = float(row.get("mmi") or 5.0)
    depth = float(row.get("depth") or 50.0)
    magnitude = float(row.get("magnitude") or 6.5)
    sig = float(row.get("sig") or 700.0)
    alert_raw = str(row.get("alert") or "").strip().lower()

    # ── 1. Base score from MMI (linear: MMI 4 → 0.0, MMI 10 → 3.0) ──────────
    # Below MMI 4 people feel it but no structural damage occurs.
    # MMI 10 is extreme/catastrophic damage to all building types.
    mmi_score = max(0.0, min(3.0, (mmi - 4.0) / 6.0 * 3.0))

    # ── 2. Blend with USGS alert level when available ─────────────────────────
    if alert_raw in _ALERT_SCORE:
        alert_score = _ALERT_SCORE[alert_raw]
        score = 0.60 * mmi_score + 0.40 * alert_score
    else:
        score = mmi_score

    # ── 3. Depth modifier ─────────────────────────────────────────────────────
    # Shallow foci transmit far more energy to the surface.
    if depth < 10:
        score += 0.50
    elif depth < 30:
        score += 0.25
    elif depth > 150:
        score -= 0.25

    # ── 4. Magnitude modifier ─────────────────────────────────────────────────
    if magnitude >= 8.0:
        score += 0.50
    elif magnitude >= 7.5:
        score += 0.25
    elif magnitude < 6.5:
        score -= 0.25

    # ── 5. Significance tiebreaker ────────────────────────────────────────────
    if sig >= 1500:
        score += 0.15
    elif sig >= 1000:
        score += 0.05

    score = max(0.0, min(3.0, score))
    log_probs = _score_to_log_probs(score)
    label = max(log_probs, key=log_probs.get)
    return label, log_probs
