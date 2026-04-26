"""
Flood damage heuristic — ground sensor classifier.

Dataset: flood.csv (FEMA disaster declarations, incident_type == "Flood")

Key columns selected:
  ih_program_declared  — Individual Housing: only activated in ~7% of flood
                         declarations — when people are displaced and need
                         temporary shelter. Strongest indicator that residential
                         buildings were destroyed or rendered uninhabitable.
  ia_program_declared  — Individual Assistance: activated in ~58% of flood
                         declarations. People directly need financial/personal
                         aid, meaning flood water entered and damaged homes.
  pa_program_declared  — Public Assistance: activated in ~93% of cases;
                         indicates public infrastructure (roads, utilities,
                         public buildings) was damaged. Near-universal, so it
                         mainly differentiates "any impact" from a near-miss.
  hm_program_declared  — Hazard Mitigation: declared in ~31% of cases; implies
                         recurring vulnerability and long-term remediation need,
                         used as a small secondary signal.
  incident_begin/end_date — Duration of flooding. Floods are uniquely time-
                         sensitive: prolonged inundation (>14 days) destroys
                         foundations, causes mold, and undermines structures far
                         beyond the initial impact. Dataset median = 11 days;
                         90th percentile = 87 days.

Columns not used:
  state / fips / designated_area — Geographic identifiers; not severity signals.
  declaration_type / declaration_date — Administrative timing; not severity.
  designated_incident_types — Too sparse/inconsistent for reliable parsing.

Damage score construction (0–3):

  Aid-program hierarchy (primary):
    ih = 1                  → base 2.75  (people displaced → building destruction)
    ia = 1, ih = 0          → base 2.00  (people need aid → significant home damage)
    pa = 1, ia = 0, ih = 0  → base 0.75  (infrastructure hit, buildings mostly ok)
    none activated          → base 0.25  (minimal impact despite declaration)

  Duration boost (flood-specific; prolonged inundation multiplies damage):
    > 60 days → +0.50
    > 30 days → +0.35
    > 14 days → +0.20
    >  7 days → +0.10
    ≤  7 days → +0.00

  Hazard Mitigation boost:
    hm = 1  → +0.15  (long-term remediation needed → higher baseline damage)

  Final score clipped to [0, 3].
"""

import math
from datetime import datetime

CLASSES = ["no-damage", "minor-damage", "major-damage", "destroyed"]


def _score_to_log_probs(score: float, sigma: float = 0.75) -> dict[str, float]:
    centers = [0.0, 1.0, 2.0, 3.0]
    raw = [math.exp(-0.5 * ((score - c) / sigma) ** 2) for c in centers]
    total = sum(raw)
    probs = [r / total for r in raw]
    return {cls: math.log(max(p, 1e-9)) for cls, p in zip(CLASSES, probs)}


def _parse_duration(row: dict) -> float:
    """Return incident duration in days; defaults to 0 if dates unavailable."""
    try:
        begin = datetime.fromisoformat(
            str(row.get("incident_begin_date") or "").replace("Z", "+00:00")
        )
        end = datetime.fromisoformat(
            str(row.get("incident_end_date") or "").replace("Z", "+00:00")
        )
        return max(0.0, (end - begin).total_seconds() / 86400)
    except Exception:
        return 0.0


def classify_flood(row: dict) -> tuple[str, dict[str, float]]:
    """
    Classify a single flood FEMA declaration row into a damage category.

    Parameters
    ----------
    row : dict-like
        One row from the flood dataset (flood.csv).

    Returns
    -------
    label : str
        Predicted damage class.
    log_probs : dict[str, float]
        Log-probability for each class.
    """
    ih = int(float(row.get("ih_program_declared") or 0))
    ia = int(float(row.get("ia_program_declared") or 0))
    pa = int(float(row.get("pa_program_declared") or 0))
    hm = int(float(row.get("hm_program_declared") or 0))

    # ── 1. Aid-program base score ─────────────────────────────────────────────
    if ih:
        base_score = 2.75
    elif ia:
        base_score = 2.00
    elif pa:
        base_score = 0.75
    else:
        base_score = 0.25

    # ── 2. Duration boost (prolonged inundation uniquely compounds damage) ────
    duration = _parse_duration(row)
    if duration > 60:
        dur_boost = 0.50
    elif duration > 30:
        dur_boost = 0.35
    elif duration > 14:
        dur_boost = 0.20
    elif duration > 7:
        dur_boost = 0.10
    else:
        dur_boost = 0.00

    # ── 3. Hazard Mitigation boost ────────────────────────────────────────────
    hm_boost = 0.15 if hm else 0.00

    score = max(0.0, min(3.0, base_score + dur_boost + hm_boost))
    log_probs = _score_to_log_probs(score)
    label = max(log_probs, key=log_probs.get)
    return label, log_probs
