"""
Storm damage heuristic — ground sensor classifier.
Covers: Severe Storm, Hurricane, Tornado (filtered from us_disaster_declarations.csv).

Dataset: us_disaster_declarations.csv (filtered to incident_type ∈
         {Severe Storm, Hurricane, Tornado})

Key columns selected:
  ia_program_declared  — Individual Assistance: FEMA activates this when people
                         directly lose housing / need personal aid. Strong signal
                         for residential/commercial building damage.
  ih_program_declared  — Individual Housing: only declared when people are
                         displaced and need temporary shelter. Strongest indicator
                         of near-total building destruction in the declared area.
  pa_program_declared  — Public Assistance: infrastructure and public-facility
                         repair. Active in 92% of storm declarations; differentiates
                         any infrastructure impact from a near-miss.
  hm_program_declared  — Hazard Mitigation: near-universal; used only as a small
                         baseline weight.
  incident_type        — Tornado produces concentrated, catastrophic damage;
                         Hurricane spreads over large area but with sustained high
                         winds; Severe Storm is baseline.
  incident_begin/end_date — Duration proxy: prolonged events compound damage.

Columns not used:
  fips / designated_area — Geographic identifiers; not severity signals.
  declaration_request_number — Administrative; no severity information.

Damage score construction (base 0–3):
  Aid-program hierarchy (primary, 0–2.75):
    ih=1                → base 2.75  (people displaced → major destruction)
    ia=1, ih=0          → base 2.00  (people need aid → significant damage)
    pa=1, ia=0, ih=0    → base 1.00  (infrastructure impacted, buildings mostly ok)
    none activated      → base 0.25  (declaration issued but impact minimal)

  Incident-type boost (secondary, 0–0.40):
    Tornado             → +0.40  (EF1+ tornadoes are highly destructive per event)
    Hurricane / Typhoon → +0.20  (sustained high winds over large area)
    Severe Storm(s)     → +0.00  (baseline)

  Duration boost (tertiary, 0–0.30):
    > 14 days           → +0.30
    7–14 days           → +0.15
    < 7 days            → +0.00

  Final score clipped to [0, 3].
"""

import math
from datetime import datetime

CLASSES = ["no-damage", "minor-damage", "major-damage", "destroyed"]

_TYPE_BOOST = {
    "Tornado":        0.40,
    "Hurricane":      0.20,
    "Typhoon":        0.20,
    "Severe Storm":   0.00,
    "Severe Storms":  0.00,
    "Severe Storm(s)": 0.00,
}

_VALID_INCIDENT_TYPES = set(_TYPE_BOOST.keys())


def _score_to_log_probs(score: float, sigma: float = 0.75) -> dict[str, float]:
    centers = [0.0, 1.0, 2.0, 3.0]
    raw = [math.exp(-0.5 * ((score - c) / sigma) ** 2) for c in centers]
    total = sum(raw)
    probs = [r / total for r in raw]
    return {cls: math.log(max(p, 1e-9)) for cls, p in zip(CLASSES, probs)}


def _parse_duration(row: dict) -> float:
    """Return duration in days between incident begin and end dates."""
    try:
        begin = datetime.fromisoformat(
            str(row.get("incident_begin_date") or "").replace("Z", "+00:00")
        )
        end = datetime.fromisoformat(
            str(row.get("incident_end_date") or "").replace("Z", "+00:00")
        )
        return max(0.0, (end - begin).total_seconds() / 86400)
    except Exception:
        return 7.0  # assume one week when dates unavailable


def classify_storm(row: dict) -> tuple[str, dict[str, float]]:
    """
    Classify a single storm declaration row into a damage category.

    Parameters
    ----------
    row : dict-like
        One row from the filtered storm dataset (incident_type must be one of
        Severe Storm, Hurricane, or Tornado).

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

    incident_type = str(row.get("incident_type") or "Severe Storm").strip()

    # ── 1. Aid-program base score ─────────────────────────────────────────────
    if ih:
        base_score = 2.75
    elif ia:
        base_score = 2.00
    elif pa:
        base_score = 1.00
    else:
        base_score = 0.25

    # ── 2. Incident-type boost ────────────────────────────────────────────────
    type_boost = _TYPE_BOOST.get(incident_type, 0.0)

    # ── 3. Duration boost ─────────────────────────────────────────────────────
    duration = _parse_duration(row)
    if duration > 14:
        dur_boost = 0.30
    elif duration > 7:
        dur_boost = 0.15
    else:
        dur_boost = 0.00

    score = max(0.0, min(3.0, base_score + type_boost + dur_boost))
    log_probs = _score_to_log_probs(score)
    label = max(log_probs, key=log_probs.get)
    return label, log_probs
