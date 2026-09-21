"""
Step 3: Unit matching — multi-type suggestion, plus duplicate-incident detection.

find_units_for_report(): a report can need more than one kind of help at
once — trapped people need a boat, injured people need an ambulance,
children present call for a relief team. This returns the nearest
available, operational unit for EACH need the report actually has, not
just a single "best fit" unit.

Mapping is DISASTER-TYPE AWARE — "trapped" alone doesn't mean "send a
boat". A boat only makes sense when the danger is actually water:
  - trapped_count > 0 AND (disaster is flood OR water is rising)  -> BOAT
  - trapped_count > 0 otherwise (earthquake, landslide, fire, etc.)
                                                    -> RELIEF_TEAM (rescue/excavation)
  - water_rising (any disaster type)               -> BOAT
  - injured_count > 0                              -> AMBULANCE (always)
  - children_count > 0                             -> RELIEF_TEAM (always)
  - none of the above (a plain/minor report)        -> RELIEF_TEAM as general aid

If the exact type a need calls for has nothing available, a fallback order
is tried so the need still gets *something* rather than nothing — but a
unit already claimed by an earlier need in this same report is never
handed out twice.

find_duplicate_incident(): unchanged from before — flags likely-same-
incident reports within ~300m of an existing active one.
"""

from math import hypot

from sqlalchemy.orm import Session

from .models import Report, Unit, UnitType, UnitCondition, IncidentStatus, DisasterType

DUPLICATE_DISTANCE_THRESHOLD = 0.003  # ~300m at Kathmandu's latitude

_FALLBACK_ORDER = {
    UnitType.BOAT: [UnitType.BOAT, UnitType.AMBULANCE, UnitType.RELIEF_TEAM],
    UnitType.AMBULANCE: [UnitType.AMBULANCE, UnitType.BOAT, UnitType.RELIEF_TEAM],
    UnitType.RELIEF_TEAM: [UnitType.RELIEF_TEAM, UnitType.AMBULANCE, UnitType.BOAT],
}


def _needed_unit_types(report: Report) -> list[UnitType]:
    needs: list[UnitType] = []
    is_water_scenario = report.disaster_type == DisasterType.FLOOD or report.water_rising

    if report.water_rising:
        needs.append(UnitType.BOAT)

    if (report.trapped_count or 0) > 0:
        if is_water_scenario:
            if UnitType.BOAT not in needs:
                needs.append(UnitType.BOAT)
        else:
            # earthquake, landslide, fire, etc. — trapped means rubble/debris,
            # not water, so a boat is useless here; send a rescue team instead
            if UnitType.RELIEF_TEAM not in needs:
                needs.append(UnitType.RELIEF_TEAM)

    if (report.injured_count or 0) > 0:
        if UnitType.AMBULANCE not in needs:
            needs.append(UnitType.AMBULANCE)
    if (report.children_count or 0) > 0:
        if UnitType.RELIEF_TEAM not in needs:
            needs.append(UnitType.RELIEF_TEAM)
    if not needs:
        needs.append(UnitType.RELIEF_TEAM)  # general aid fallback
    return needs


def _distance(a_lat, a_lng, b_lat, b_lng) -> float:
    return hypot(a_lat - b_lat, a_lng - b_lng)


def _nearest(candidates: list[Unit], report: Report) -> Unit | None:
    if not candidates:
        return None
    return min(candidates, key=lambda u: _distance(report.lat, report.lng, u.lat, u.lng))


def find_units_for_report(db: Session, report: Report) -> list[Unit]:
    """
    Returns one Unit per genuine need on this report — e.g. a trapped +
    injured + child-present report returns up to 3 units: a boat, an
    ambulance, and a relief team, each the nearest available+operational
    unit that could be found for that need. Never returns the same unit
    twice even if two needs would otherwise point at it.
    """
    available_pool = (
        db.query(Unit)
        .filter(Unit.available == True, Unit.condition == UnitCondition.OPERATIONAL)  # noqa: E712
        .all()
    )

    chosen: list[Unit] = []
    chosen_ids: set[str] = set()

    needed_types = _needed_unit_types(report)

    # Pass 1: exact-type match for each need
    for need in needed_types:
        exact = [u for u in available_pool if u.type == need and u.id not in chosen_ids]
        pick = _nearest(exact, report)
        if pick:
            chosen.append(pick)
            chosen_ids.add(pick.id)

    # Pass 2: for any need still unmet (no exact-type unit was available),
    # try the fallback order so the need isn't just silently dropped
    for i, need in enumerate(needed_types):
        # was this need actually fulfilled in pass 1? crude check: was a
        # unit of this exact type chosen for it — since order is preserved,
        # re-derive by checking if any chosen unit has this type
        if any(u.type == need for u in chosen):
            continue
        for fallback_type in _FALLBACK_ORDER[need]:
            candidates = [u for u in available_pool if u.type == fallback_type and u.id not in chosen_ids]
            pick = _nearest(candidates, report)
            if pick:
                chosen.append(pick)
                chosen_ids.add(pick.id)
                break

    return chosen


def find_duplicate_incident(db: Session, report: Report) -> Report | None:
    """
    Look for an existing, still-active report of the same disaster type
    within DUPLICATE_DISTANCE_THRESHOLD of this new one. Returns the
    closest match, or None.
    """
    active = (
        db.query(Report)
        .filter(
            Report.id != report.id,
            Report.disaster_type == report.disaster_type,
            Report.status.notin_([IncidentStatus.RESCUED, IncidentStatus.REJECTED]),
        )
        .all()
    )

    close_matches = [
        r for r in active
        if _distance(report.lat, report.lng, r.lat, r.lng) < DUPLICATE_DISTANCE_THRESHOLD
    ]

    if not close_matches:
        return None

    return min(close_matches, key=lambda r: _distance(report.lat, report.lng, r.lat, r.lng))
