"""
Step 2: Severity scoring.

Deliberately rule-based and fully auditable, per the proposal's "Novel
Techniques" section — no black-box scoring on a life-or-death decision.
The LLM (see explain.py) only turns the reasons list into a sentence; it
never touches the score itself.
"""

from .models import Report, Severity


def score_report(report: Report) -> tuple[Severity, list[str]]:
    """
    Returns (severity_level, reasons) where reasons is a list of short
    plain-language tags, e.g. ["6 people reported", "water level rising"].
    """
    score = 0
    reasons: list[str] = []

    if report.trapped:
        score += 3
        reasons.append("victims reported trapped")

    if report.water_rising:
        score += 3
        reasons.append("water level actively rising")

    if report.injuries:
        score += 2
        reasons.append("injuries reported")

    if report.children_present:
        score += 2
        reasons.append("children present")

    # victim count contributes but is capped so one huge number
    # doesn't single-handedly dominate the score
    vc_contribution = min(report.victim_count or 0, 5)
    if vc_contribution > 0:
        score += vc_contribution
        reasons.append(f"{report.victim_count} people reported")

    if score >= 8:
        level = Severity.CRITICAL
    elif score >= 4:
        level = Severity.HIGH
    else:
        level = Severity.MODERATE

    return level, reasons
