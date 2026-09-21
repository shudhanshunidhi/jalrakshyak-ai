"""
Step 5: API layer.

Run with:  uvicorn app.main:app --reload   (or: python -m uvicorn app.main:app --reload
           if your system blocks the standalone uvicorn.exe)
Docs at:   http://localhost:8000/docs
"""

from datetime import datetime
from typing import Optional, List
import io

from fastapi import FastAPI, Depends, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session
from openpyxl import Workbook

from .database import init_db, get_db
from .models import (
    Report, Unit, Shelter, Hospital, Dispatch,
    IncidentStatus, DispatchStatus, DisasterType, Channel, Severity, UnitType, UnitCondition,
)
from .scoring import score_report
from .matching import find_units_for_report, find_duplicate_incident
from .explain import explain_severity

app = FastAPI(title="JALRAKSHYAK AI", version="0.4.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def on_startup():
    init_db()


# ---------- Request/response schemas ----------

class ReportIn(BaseModel):
    location_label: str
    lat: float
    lng: float
    disaster_type: DisasterType = DisasterType.FLOOD
    channel: Channel = Channel.APP
    victim_count: int = 1
    injured_count: int = 0
    trapped_count: int = 0
    children_count: int = 0
    water_rising: bool = False
    notes: str = ""


class StatusUpdate(BaseModel):
    status: IncidentStatus


class ModifyUnits(BaseModel):
    unit_ids: List[str]
    message: Optional[str] = None
    hospital_id: Optional[str] = None
    shelter_id: Optional[str] = None


class ApproveIn(BaseModel):
    message: Optional[str] = None
    hospital_id: Optional[str] = None
    shelter_id: Optional[str] = None


class RejectIn(BaseModel):
    message: Optional[str] = None


class DispatchStatusUpdate(BaseModel):
    """What the DRIVER sends — updates only their own unit's progress on
    this incident, never the whole report. hospital_id/shelter_id are set
    when the driver marks Transporting — where THIS unit is actually
    headed, independent of any other unit on the same incident."""
    status: DispatchStatus
    headcount: Optional[int] = None  # "bringing 2 patients" — set when relevant
    hospital_id: Optional[str] = None
    shelter_id: Optional[str] = None


class UnitUpdate(BaseModel):
    name: Optional[str] = None
    type: Optional[UnitType] = None
    lat: Optional[float] = None
    lng: Optional[float] = None
    available: Optional[bool] = None
    condition: Optional[UnitCondition] = None


class UnitCreate(BaseModel):
    name: str
    type: UnitType
    lat: float
    lng: float
    available: bool = True
    condition: UnitCondition = UnitCondition.OPERATIONAL


class ShelterUpdate(BaseModel):
    name: Optional[str] = None
    capacity: Optional[int] = None
    current_occupancy: Optional[int] = None
    operational: Optional[bool] = None
    lat: Optional[float] = None
    lng: Optional[float] = None


class ShelterCreate(BaseModel):
    name: str
    lat: float
    lng: float
    capacity: int
    current_occupancy: int = 0


class HospitalUpdate(BaseModel):
    name: Optional[str] = None
    total_beds: Optional[int] = None
    available_beds: Optional[int] = None
    has_er: Optional[bool] = None
    operational: Optional[bool] = None
    lat: Optional[float] = None
    lng: Optional[float] = None


class HospitalCreate(BaseModel):
    name: str
    lat: float
    lng: float
    total_beds: int
    available_beds: int
    has_er: bool = True
    operational: bool = True


# ---------- Intake ----------

@app.post("/reports")
def create_report(payload: ReportIn, db: Session = Depends(get_db)):
    """
    Perceive + Reason + suggest Act: create the report, score it, and either
    (a) flag it as a probable duplicate of an existing nearby report, or
    (b) create a Dispatch row (status=Assigned) for EVERY genuine need the
        report has — a boat for trapped/water-rising, an ambulance for
        injuries, a relief team for children present — not just one unit.
    Nothing is marked busy yet; that only happens once the commander
    approves (see /incidents/{id}/approve).
    """
    report = Report(**payload.model_dump())
    db.add(report)
    db.flush()

    severity, reasons = score_report(report)
    report.severity = severity
    report.severity_reasons = ", ".join(reasons)

    duplicate = find_duplicate_incident(db, report)
    if duplicate:
        report.duplicate_of_id = duplicate.id
        report.severity_explanation = (
            f"{explain_severity(severity.value, reasons)} "
            f"Likely the same incident as an existing report at "
            f"'{duplicate.location_label}' — no new unit auto-assigned."
        )
    else:
        report.severity_explanation = explain_severity(severity.value, reasons)
        suggested_units = find_units_for_report(db, report)
        for unit in suggested_units:
            db.add(Dispatch(report_id=report.id, unit_id=unit.id, status=DispatchStatus.ASSIGNED))

    db.commit()
    db.refresh(report)
    return _serialize_report(report, db)


@app.get("/reports/export")
def export_reports(status: Optional[str] = None, db: Session = Depends(get_db)):
    """
    Exports incident records as an .xlsx file — the 'data keeper' record for
    handing to municipal authorities, donors, or press.

    NOTE: this route must stay ABOVE /reports/{report_id} — FastAPI matches
    routes in registration order, so if the {report_id} route came first,
    a request for /reports/export would be wrongly parsed as report_id="export".
    """
    query = db.query(Report)
    if status:
        query = query.filter(Report.status == status)
    reports = query.order_by(Report.created_at).all()

    wb = Workbook()
    ws = wb.active
    ws.title = "Incidents"
    headers = [
        "Report ID", "Timestamp (UTC)", "Location", "Latitude", "Longitude",
        "Disaster Type", "Channel", "Victim Count", "Injured", "Trapped", "Children",
        "Water Rising", "Severity", "Severity Reasons", "Status",
        "Dispatched Units (unit: status)", "Commander Message", "Notes",
    ]
    ws.append(headers)

    for r in reports:
        dispatches = db.query(Dispatch).filter(Dispatch.report_id == r.id).all()
        dispatch_summary = ", ".join(
            f"{u.name}: {d.status.value}" for d in dispatches if (u := db.query(Unit).get(d.unit_id))
        )
        ws.append([
            r.id,
            r.created_at.strftime("%Y-%m-%d %H:%M:%S"),
            r.location_label, r.lat, r.lng,
            r.disaster_type.value if r.disaster_type else "",
            r.channel.value if r.channel else "",
            r.victim_count, r.injured_count, r.trapped_count, r.children_count,
            r.water_rising,
            r.severity.value if r.severity else "",
            r.severity_reasons,
            r.status.value if r.status else "",
            dispatch_summary,
            r.commander_message or "",
            r.notes or "",
        ])

    for col in ws.columns:
        max_len = max((len(str(c.value)) if c.value is not None else 0) for c in col)
        ws.column_dimensions[col[0].column_letter].width = min(max_len + 2, 45)

    stream = io.BytesIO()
    wb.save(stream)
    stream.seek(0)

    suffix = f"_{status.replace(' ', '_')}" if status else ""
    filename = f"jalrakshyak_incidents{suffix}_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.xlsx"
    return StreamingResponse(
        stream,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


@app.get("/reports/{report_id}")
def get_report(report_id: str, db: Session = Depends(get_db)):
    """Single-report lookup — what the CITIZEN's page polls for status updates."""
    report = _get_report_or_404(db, report_id)
    return _serialize_report(report, db)


# ---------- Dashboard ----------

_SEVERITY_ORDER = {Severity.CRITICAL: 0, Severity.HIGH: 1, Severity.MODERATE: 2}


@app.get("/incidents")
def list_incidents(db: Session = Depends(get_db)):
    reports = db.query(Report).all()
    reports.sort(key=lambda r: (_SEVERITY_ORDER.get(r.severity, 3), -r.created_at.timestamp()))
    return [_serialize_report(r, db) for r in reports]


# ---------- Commander actions: Approve / Modify (multi-unit) / Reject ----------

@app.post("/incidents/{report_id}/approve")
def approve(report_id: str, payload: ApproveIn, db: Session = Depends(get_db)):
    report = _get_report_or_404(db, report_id)
    dispatches = db.query(Dispatch).filter(Dispatch.report_id == report_id).all()
    if not dispatches:
        raise HTTPException(400, "No unit assigned to approve")

    report.status = IncidentStatus.ASSIGNED
    if payload.message:
        report.commander_message = payload.message
    if payload.hospital_id:
        report.destination_hospital_id = payload.hospital_id
    if payload.shelter_id:
        report.destination_shelter_id = payload.shelter_id

    for d in dispatches:
        unit = db.query(Unit).get(d.unit_id)
        if unit:
            unit.available = False

    db.commit()
    return _serialize_report(report, db)


@app.post("/incidents/{report_id}/modify")
def modify(report_id: str, payload: ModifyUnits, db: Session = Depends(get_db)):
    """Replace this incident's dispatched units with a new set — lets the
    commander pick different or additional units than what was auto-suggested,
    or override an auto-flagged duplicate and dispatch anyway."""
    report = _get_report_or_404(db, report_id)
    if not payload.unit_ids:
        raise HTTPException(400, "Select at least one unit")

    # units already dispatched to THIS report are marked unavailable simply
    # because they're busy with this incident — that must not block
    # re-selecting them when the commander is just confirming/adjusting the
    # AI's suggestion, only genuinely-busy-elsewhere units should be blocked
    already_on_this_report = {
        d.unit_id for d in db.query(Dispatch).filter(Dispatch.report_id == report_id).all()
    }

    units = []
    for uid in payload.unit_ids:
        unit = db.query(Unit).get(uid)
        if not unit:
            raise HTTPException(400, f"Unit {uid} not found")
        is_free_or_already_ours = unit.available or uid in already_on_this_report
        if not is_free_or_already_ours or unit.condition != UnitCondition.OPERATIONAL:
            raise HTTPException(400, f"Unit {uid} is not available")
        units.append(unit)

    old_dispatches = db.query(Dispatch).filter(Dispatch.report_id == report_id).all()
    for d in old_dispatches:
        old_unit = db.query(Unit).get(d.unit_id)
        if old_unit:
            old_unit.available = True
        db.delete(d)

    for unit in units:
        db.add(Dispatch(report_id=report.id, unit_id=unit.id, status=DispatchStatus.ASSIGNED))
        unit.available = False

    report.status = IncidentStatus.ASSIGNED
    if payload.message:
        report.commander_message = payload.message
    if payload.hospital_id:
        report.destination_hospital_id = payload.hospital_id
    if payload.shelter_id:
        report.destination_shelter_id = payload.shelter_id

    db.commit()
    return _serialize_report(report, db)


@app.post("/incidents/{report_id}/reject")
def reject(report_id: str, payload: RejectIn, db: Session = Depends(get_db)):
    """Frees any dispatched units and marks the report REJECTED — which the
    citizen's app detects and displays. The commander can still use Modify
    afterwards to assign units if they reconsider."""
    report = _get_report_or_404(db, report_id)
    dispatches = db.query(Dispatch).filter(Dispatch.report_id == report_id).all()
    for d in dispatches:
        unit = db.query(Unit).get(d.unit_id)
        if unit:
            unit.available = True
        db.delete(d)

    report.status = IncidentStatus.REJECTED
    if payload.message:
        report.commander_message = payload.message
    db.commit()
    return _serialize_report(report, db)


@app.post("/incidents/{report_id}/dispatch-anyway")
def dispatch_anyway(report_id: str, db: Session = Depends(get_db)):
    """Clears a duplicate flag so the commander can treat this as its own
    incident and assign units via Modify."""
    report = _get_report_or_404(db, report_id)
    report.duplicate_of_id = None
    db.commit()
    return _serialize_report(report, db)


# ---------- Lifecycle tracking (commander-level overall status) ----------

@app.patch("/incidents/{report_id}/status")
def update_status(report_id: str, payload: StatusUpdate, db: Session = Depends(get_db)):
    report = _get_report_or_404(db, report_id)
    report.status = payload.status
    if payload.status == IncidentStatus.RESCUED:
        dispatches = db.query(Dispatch).filter(Dispatch.report_id == report_id).all()
        for d in dispatches:
            d.status = DispatchStatus.COMPLETE
            d.updated_at = datetime.utcnow()
            unit = db.query(Unit).get(d.unit_id)
            if unit:
                unit.available = True
    db.commit()
    return _serialize_report(report, db)


# ---------- Driver actions: per-unit dispatch status ----------

@app.patch("/dispatches/{dispatch_id}/status")
def update_dispatch_status(dispatch_id: str, payload: DispatchStatusUpdate, db: Session = Depends(get_db)):
    """
    The DRIVER's own action — updates ONLY this one unit's progress on its
    incident. This is the fix for the bug where marking one unit En Route
    used to mark the whole incident (and every other unit on it) En Route.

    If this completes the LAST outstanding dispatch on a report, the report
    itself is automatically marked Rescued and its unit freed — the
    commander dashboard reflects reality without needing a manual step.
    """
    dispatch = db.query(Dispatch).get(dispatch_id)
    if not dispatch:
        raise HTTPException(404, "Dispatch not found")

    dispatch.status = payload.status
    if payload.headcount is not None:
        dispatch.headcount = payload.headcount
    if payload.hospital_id is not None:
        dispatch.destination_hospital_id = payload.hospital_id
    if payload.shelter_id is not None:
        dispatch.destination_shelter_id = payload.shelter_id
    dispatch.updated_at = datetime.utcnow()

    if payload.status == DispatchStatus.COMPLETE:
        unit = db.query(Unit).get(dispatch.unit_id)
        if unit:
            unit.available = True

    db.flush()

    report = db.query(Report).get(dispatch.report_id)
    if report:
        siblings = db.query(Dispatch).filter(Dispatch.report_id == report.id).all()
        if siblings and all(d.status == DispatchStatus.COMPLETE for d in siblings):
            report.status = IncidentStatus.RESCUED
        elif any(d.status in (DispatchStatus.EN_ROUTE, DispatchStatus.TRANSPORTING) for d in siblings):
            if report.status == IncidentStatus.ASSIGNED:
                report.status = IncidentStatus.EN_ROUTE

    db.commit()
    return _serialize_driver_assignment(dispatch, db)


@app.get("/units/{unit_id}/assignment")
def get_unit_assignment(unit_id: str, db: Session = Depends(get_db)):
    """What the DRIVER's view polls: this unit's own current dispatch, if any."""
    unit = db.query(Unit).get(unit_id)
    if not unit:
        raise HTTPException(404, "Unit not found")

    dispatch = (
        db.query(Dispatch)
        .filter(
            Dispatch.unit_id == unit_id,
            Dispatch.status.in_([DispatchStatus.ASSIGNED, DispatchStatus.EN_ROUTE, DispatchStatus.TRANSPORTING]),
        )
        .order_by(Dispatch.created_at.desc())
        .first()
    )
    if not dispatch:
        return None
    return _serialize_driver_assignment(dispatch, db)


# ---------- Rescue units: list, create, edit, delete ----------

@app.get("/units")
def list_units(db: Session = Depends(get_db)):
    return [_serialize_unit(u) for u in db.query(Unit).all()]


@app.post("/units")
def create_unit(payload: UnitCreate, db: Session = Depends(get_db)):
    unit = Unit(**payload.model_dump(), last_gps_update=datetime.utcnow())
    db.add(unit)
    db.commit()
    db.refresh(unit)
    return _serialize_unit(unit)


@app.patch("/units/{unit_id}")
def update_unit(unit_id: str, payload: UnitUpdate, db: Session = Depends(get_db)):
    unit = db.query(Unit).get(unit_id)
    if not unit:
        raise HTTPException(404, "Unit not found")
    data = payload.model_dump(exclude_unset=True)
    position_changed = "lat" in data or "lng" in data
    for field, value in data.items():
        setattr(unit, field, value)
    if position_changed:
        unit.last_gps_update = datetime.utcnow()
    db.commit()
    db.refresh(unit)
    return _serialize_unit(unit)


@app.delete("/units/{unit_id}")
def delete_unit(unit_id: str, db: Session = Depends(get_db)):
    unit = db.query(Unit).get(unit_id)
    if not unit:
        raise HTTPException(404, "Unit not found")
    db.delete(unit)
    db.commit()
    return {"deleted": True}


# ---------- Shelters: list, create, edit, delete, incoming ----------

@app.get("/shelters")
def list_shelters(db: Session = Depends(get_db)):
    return [_serialize_shelter(s) for s in db.query(Shelter).all()]


@app.get("/shelters/{shelter_id}/incoming")
def get_shelter_incoming(shelter_id: str, db: Session = Depends(get_db)):
    """
    What the SHELTER's view polls. Only shows units that have actually
    LOADED people and are now transporting them here (DispatchStatus.
    TRANSPORTING) — a unit still en route to the victim, or just assigned,
    is not yet real news for the shelter, so it stays invisible to them
    until there's a real headcount heading their way.
    """
    shelter = db.query(Shelter).get(shelter_id)
    if not shelter:
        raise HTTPException(404, "Shelter not found")
    return _incoming_for_destination(db, Dispatch.destination_shelter_id, shelter_id)


@app.post("/shelters")
def create_shelter(payload: ShelterCreate, db: Session = Depends(get_db)):
    shelter = Shelter(**payload.model_dump(), operational=True)
    db.add(shelter)
    db.commit()
    db.refresh(shelter)
    return _serialize_shelter(shelter)


@app.patch("/shelters/{shelter_id}")
def update_shelter(shelter_id: str, payload: ShelterUpdate, db: Session = Depends(get_db)):
    shelter = db.query(Shelter).get(shelter_id)
    if not shelter:
        raise HTTPException(404, "Shelter not found")
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(shelter, field, value)
    db.commit()
    db.refresh(shelter)
    return _serialize_shelter(shelter)


@app.delete("/shelters/{shelter_id}")
def delete_shelter(shelter_id: str, db: Session = Depends(get_db)):
    shelter = db.query(Shelter).get(shelter_id)
    if not shelter:
        raise HTTPException(404, "Shelter not found")
    db.delete(shelter)
    db.commit()
    return {"deleted": True}


# ---------- Hospitals: list, create, edit, delete, incoming ----------

@app.get("/hospitals")
def list_hospitals(db: Session = Depends(get_db)):
    return [_serialize_hospital(h) for h in db.query(Hospital).all()]


@app.get("/hospitals/{hospital_id}/incoming")
def get_hospital_incoming(hospital_id: str, db: Session = Depends(get_db)):
    """
    What the HOSPITAL's view polls. Same rule as shelters: only shows units
    actually TRANSPORTING patients here right now, with the exact headcount
    that unit's driver stated the moment they loaded up.
    """
    hospital = db.query(Hospital).get(hospital_id)
    if not hospital:
        raise HTTPException(404, "Hospital not found")
    return _incoming_for_destination(db, Dispatch.destination_hospital_id, hospital_id)


@app.post("/hospitals")
def create_hospital(payload: HospitalCreate, db: Session = Depends(get_db)):
    hospital = Hospital(**payload.model_dump())
    db.add(hospital)
    db.commit()
    db.refresh(hospital)
    return _serialize_hospital(hospital)


@app.patch("/hospitals/{hospital_id}")
def update_hospital(hospital_id: str, payload: HospitalUpdate, db: Session = Depends(get_db)):
    hospital = db.query(Hospital).get(hospital_id)
    if not hospital:
        raise HTTPException(404, "Hospital not found")
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(hospital, field, value)
    db.commit()
    db.refresh(hospital)
    return _serialize_hospital(hospital)


@app.delete("/hospitals/{hospital_id}")
def delete_hospital(hospital_id: str, db: Session = Depends(get_db)):
    hospital = db.query(Hospital).get(hospital_id)
    if not hospital:
        raise HTTPException(404, "Hospital not found")
    db.delete(hospital)
    db.commit()
    return {"deleted": True}


# ---------- Helpers ----------

def _get_report_or_404(db: Session, report_id: str) -> Report:
    report = db.query(Report).get(report_id)
    if not report:
        raise HTTPException(404, "Report not found")
    return report


def _incoming_for_destination(db: Session, dispatch_destination_column, destination_id: str) -> List[dict]:
    """
    Shared logic for hospital/shelter 'incoming' views: find dispatches that
    are actually TRANSPORTING (loaded up, headed to this destination) and
    return one focused entry per dispatch — not the whole report, and not
    units still en route to the victim or just assigned.

    Filters on the DISPATCH's own destination, not the report's — an
    ambulance heading to a hospital and a relief team heading to a shelter
    on the same incident must never both show up on each other's boards.
    """
    rows = (
        db.query(Dispatch, Report)
        .join(Report, Dispatch.report_id == Report.id)
        .filter(
            dispatch_destination_column == destination_id,
            Dispatch.status == DispatchStatus.TRANSPORTING,
        )
        .order_by(Dispatch.updated_at.desc())
        .all()
    )

    result = []
    for dispatch, report in rows:
        unit = db.query(Unit).get(dispatch.unit_id)
        if not unit:
            continue
        result.append({
            "dispatch_id": dispatch.id,
            "report_id": report.id,
            "location_label": report.location_label,
            "severity": report.severity.value if report.severity else None,
            "severity_explanation": report.severity_explanation,
            "disaster_type": report.disaster_type.value if report.disaster_type else None,
            "victim_count": report.victim_count,
            "created_at": report.created_at.isoformat(),
            "unit": _serialize_unit(unit),
            "headcount": dispatch.headcount,
            "updated_at": dispatch.updated_at.isoformat() if dispatch.updated_at else None,
        })
    return result


def _serialize_unit(u: Unit) -> dict:
    return {
        "id": u.id,
        "name": u.name,
        "type": u.type.value,
        "lat": u.lat,
        "lng": u.lng,
        "available": u.available,
        "condition": u.condition.value if u.condition else "operational",
        "last_gps_update": u.last_gps_update.isoformat() if u.last_gps_update else None,
    }


def _serialize_shelter(s: Shelter) -> dict:
    return {
        "id": s.id, "name": s.name, "lat": s.lat, "lng": s.lng,
        "capacity": s.capacity, "current_occupancy": s.current_occupancy,
        "operational": s.operational,
    }


def _serialize_hospital(h: Hospital) -> dict:
    return {
        "id": h.id, "name": h.name, "lat": h.lat, "lng": h.lng,
        "total_beds": h.total_beds, "available_beds": h.available_beds,
        "has_er": h.has_er, "operational": h.operational,
    }


def _serialize_dispatch(d: Dispatch, db: Session) -> Optional[dict]:
    unit = db.query(Unit).get(d.unit_id)
    if not unit:
        return None

    dest_hospital = None
    if d.destination_hospital_id:
        h = db.query(Hospital).get(d.destination_hospital_id)
        if h:
            dest_hospital = {"id": h.id, "name": h.name}

    dest_shelter = None
    if d.destination_shelter_id:
        s = db.query(Shelter).get(d.destination_shelter_id)
        if s:
            dest_shelter = {"id": s.id, "name": s.name}

    return {
        "id": d.id,
        "unit": _serialize_unit(unit),
        "status": d.status.value,
        "headcount": d.headcount,
        "destination_hospital": dest_hospital,
        "destination_shelter": dest_shelter,
        "updated_at": d.updated_at.isoformat() if d.updated_at else None,
    }


def _serialize_report(report: Report, db: Session) -> dict:
    dispatch_rows = (
        db.query(Dispatch)
        .filter(Dispatch.report_id == report.id)
        .order_by(Dispatch.created_at)
        .all()
    )
    dispatches = [d for d in (_serialize_dispatch(row, db) for row in dispatch_rows) if d]

    duplicate_of = None
    if report.duplicate_of_id:
        original = db.query(Report).get(report.duplicate_of_id)
        if original:
            duplicate_of = {"id": original.id, "location_label": original.location_label}

    destination_hospital = None
    if report.destination_hospital_id:
        h = db.query(Hospital).get(report.destination_hospital_id)
        if h:
            destination_hospital = {"id": h.id, "name": h.name}

    destination_shelter = None
    if report.destination_shelter_id:
        s = db.query(Shelter).get(report.destination_shelter_id)
        if s:
            destination_shelter = {"id": s.id, "name": s.name}

    return {
        "id": report.id,
        "created_at": report.created_at.isoformat(),
        "location_label": report.location_label,
        "lat": report.lat,
        "lng": report.lng,
        "disaster_type": report.disaster_type.value if report.disaster_type else None,
        "channel": report.channel.value if report.channel else None,
        "victim_count": report.victim_count,
        "injured_count": report.injured_count,
        "trapped_count": report.trapped_count,
        "children_count": report.children_count,
        "water_rising": report.water_rising,
        "notes": report.notes,
        "severity": report.severity.value if report.severity else None,
        "severity_reasons": report.severity_reasons,
        "severity_explanation": report.severity_explanation,
        "status": report.status.value if report.status else None,
        "commander_message": report.commander_message or "",
        "dispatches": dispatches,
        "duplicate_of": duplicate_of,
        "destination_hospital": destination_hospital,
        "destination_shelter": destination_shelter,
    }


def _serialize_driver_assignment(dispatch: Dispatch, db: Session) -> dict:
    """Combined view for driver.html: the report's details plus THIS unit's
    own dispatch status/headcount, separate from the report's overall status."""
    report = db.query(Report).get(dispatch.report_id)
    base = _serialize_report(report, db) if report else {}
    base["my_dispatch"] = {
        "id": dispatch.id,
        "status": dispatch.status.value,
        "headcount": dispatch.headcount,
    }
    return base
