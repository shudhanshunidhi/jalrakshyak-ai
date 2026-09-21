"""
Step 2: Severity scoring.

Deliberately rule-based and fully auditable, per the proposal's "Novel
Techniques" section — no black-box scoring on a life-or-death decision.
The LLM (see explain.py) only turns the reasons list into a sentence; it
never touches the score itself.

Uses precise per-category counts (injured_count, trapped_count,
children_count) rather than yes/no flags — "5 injured, 3 trapped" scores
and reads differently from "1 injured, 1 trapped", and now it can.
"""

from .models import Report, Severity


def score_report(report: Report) -> tuple[Severity, list[str]]:
    """
    Returns (severity_level, reasons) where reasons is a list of short
    plain-language tags, e.g. ["3 trapped", "water level rising"].
    """
    score = 0
    reasons: list[str] = []

    trapped = report.trapped_count or 0
    injured = report.injured_count or 0
    children = report.children_count or 0

    if trapped > 0:
        # each trapped person adds weight, capped so a huge number doesn't
        # single-handedly dominate the score
        score += 3 + min(trapped - 1, 3)
        reasons.append(f"{trapped} trapped")

    if report.water_rising:
        score += 3
        reasons.append("water level actively rising")

    if injured > 0:
        score += 2 + min(injured - 1, 3)
        reasons.append(f"{injured} injured")

    if children > 0:
        score += 2
        reasons.append(f"{children} {'child' if children == 1 else 'children'} present")

    # total victim count contributes a small amount on top — capped so it
    # doesn't dominate over the specific, more urgent categories above
    vc_contribution = min(report.victim_count or 0, 5)
    if vc_contribution > 0:
        score += vc_contribution
        reasons.append(f"{report.victim_count} people reported total")

    if score >= 8:
        level = Severity.CRITICAL
    elif score >= 4:
        level = Severity.HIGH
    else:
        level = Severity.MODERATE

    return level, reasons
