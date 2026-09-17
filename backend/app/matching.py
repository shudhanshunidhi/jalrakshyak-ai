"""
Step 3: Unit matching.

Picks the nearest AVAILABLE, OPERATIONAL unit of the right type for the
situation:
  - trapped / water rising -> boat
  - injuries -> ambulance
  - otherwise -> relief team

A unit that's marked under_maintenance or out_of_service is never matched,
even if it's technically "free" — bad-condition vehicles shouldn't be sent
just because nothing else is busy.

Distance is plain Euclidean on lat/lng, which is fine at municipal scale
for a demo (no need for haversine precision here).
"""

from math import hypot

from sqlalchemy.orm import Session

from .models import Report, Unit, UnitType, UnitCondition


def _preferred_unit_type(report: Report) -> UnitType:
    if report.trapped or report.water_rising:
        return UnitType.BOAT
    if report.injuries:
        return UnitType.AMBULANCE
    return UnitType.RELIEF_TEAM


def _distance(a_lat, a_lng, b_lat, b_lng) -> float:
    return hypot(a_lat - b_lat, a_lng - b_lng)


def find_best_unit(db: Session, report: Report) -> Unit | None:
    preferred_type = _preferred_unit_type(report)

    base_query = db.query(Unit).filter(
        Unit.available == True,  # noqa: E712
        Unit.condition == UnitCondition.OPERATIONAL,
    )

    candidates = base_query.filter(Unit.type == preferred_type).all()

    # Fall back to any available + operational unit if none of the preferred type
    if not candidates:
        candidates = base_query.all()

    if not candidates:
        return None

    return min(
        candidates,
        key=lambda u: _distance(report.lat, report.lng, u.lat, u.lng),
    )
