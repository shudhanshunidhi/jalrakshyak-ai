"""
Step 5: API layer.

Run with:  uvicorn app.main:app --reload
Docs at:   http://localhost:8000/docs   (FastAPI auto-generates this —
           useful for testing the whole flow before the frontend exists)
"""

from datetime import datetime
from typing import Optional

from fastapi import FastAPI, Depends, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sqlalchemy.orm import Session

from .database import init_db, get_db
from .models import (
    Report, Unit, Shelter, Hospital,
    IncidentStatus, DisasterType, Channel, Severity, UnitType, UnitCondition,
)
from .scoring import score_report
from .matching import find_best_unit
from .explain import explain_severity

app = FastAPI(title="JALRAKSHYAK AI", version="0.2.0")

# Wide open for hackathon demo purposes — tighten before any real deployment
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
    injuries: bool = False
    children_present: bool = False
    trapped: bool = False
    water_rising: bool = False
    notes: str = ""


class StatusUpdate(BaseModel):
    status: IncidentStatus


class ModifyUnit(BaseModel):
    unit_id: str
    message: Optional[str] = None


class ApproveIn(BaseModel):
    message: Optional[str] = None  # commander's note back to the citizen


class UnitUpdate(BaseModel):
    """All fields optional — send only what changed. Covers editing a unit's
    condition (e.g. marking a boat under_maintenance) and GPS position."""
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
    Step 1 (Perceive) + Step 2/3 (Reason) in one call: create the report,
    score it, find a matching unit — then it lands on the dashboard already
    ranked, exactly as the proposal describes.
    """
    report = Report(**payload.model_dump())
    db.add(report)
    db.flush()  # get report.id without committing yet

    severity, reasons = score_report(report)
    report.severity = severity
    report.severity_reasons = ", ".join(reasons)
    report.severity_explanation = explain_severity(severity.value, reasons)

    best_unit = find_best_unit(db, report)
    if best_unit:
        report.assigned_unit_id = best_unit.id

    db.commit()
    db.refresh(report)
    return _serialize_report(report, db)


@app.get("/reports/{report_id}")
def get_report(report_id: str, db: Session = Depends(get_db)):
    """
    Single-report lookup — this is what the CITIZEN's page polls to find out
    whether the commander has approved their report yet, and to pick up any
    message/advice the commander sent back.
    """
    report = _get_report_or_404(db, report_id)
    return _serialize_report(report, db)


# ---------- Dashboard ----------

_SEVERITY_ORDER = {Severity.CRITICAL: 0, Severity.HIGH: 1, Severity.MODERATE: 2}


@app.get("/incidents")
def list_incidents(db: Session = Depends(get_db)):
    """Ranked list for the command dashboard — most severe, most recent first."""
    reports = db.query(Report).all()
    reports.sort(key=lambda r: (_SEVERITY_ORDER.get(r.severity, 3), -r.created_at.timestamp()))
    return [_serialize_report(r, db) for r in reports]


# ---------- Commander actions: Approve / Modify / Reject ----------

@app.post("/incidents/{report_id}/approve")
def approve(report_id: str, payload: ApproveIn, db: Session = Depends(get_db)):
    report = _get_report_or_404(db, report_id)
    if not report.assigned_unit_id:
        raise HTTPException(400, "No unit assigned to approve")
    report.status = IncidentStatus.ASSIGNED
    if payload.message:
        report.commander_message = payload.message
    _set_unit_available(db, report.assigned_unit_id, False)
    db.commit()
    return _serialize_report(report, db)


@app.post("/incidents/{report_id}/modify")
def modify(report_id: str, payload: ModifyUnit, db: Session = Depends(get_db)):
    report = _get_report_or_404(db, report_id)
    unit = db.query(Unit).get(payload.unit_id)
    if not unit or not unit.available or unit.condition != UnitCondition.OPERATIONAL:
        raise HTTPException(400, "Chosen unit is not available")

    # free the previously assigned unit, if any
    if report.assigned_unit_id:
        _set_unit_available(db, report.assigned_unit_id, True)

    report.assigned_unit_id = unit.id
    report.status = IncidentStatus.ASSIGNED
    if payload.message:
        report.commander_message = payload.message
    _set_unit_available(db, unit.id, False)
    db.commit()
    return _serialize_report(report, db)


@app.post("/incidents/{report_id}/reject")
def reject(report_id: str, db: Session = Depends(get_db)):
    report = _get_report_or_404(db, report_id)
    if report.assigned_unit_id:
        _set_unit_available(db, report.assigned_unit_id, True)
    report.assigned_unit_id = None
    report.status = IncidentStatus.SOS  # back to needing a decision
    db.commit()
    return _serialize_report(report, db)


# ---------- Lifecycle tracking ----------

@app.patch("/incidents/{report_id}/status")
def update_status(report_id: str, payload: StatusUpdate, db: Session = Depends(get_db)):
    report = _get_report_or_404(db, report_id)
    report.status = payload.status
    if payload.status == IncidentStatus.RESCUED and report.assigned_unit_id:
        _set_unit_available(db, report.assigned_unit_id, True)
    db.commit()
    return _serialize_report(report, db)


# ---------- Rescue units: list, create, edit (condition + GPS) ----------

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
    """
    Edit a unit — this is the fix for 'a vehicle is in bad condition but
    still being counted as available': the commander sets condition to
    under_maintenance / out_of_service and it's immediately excluded from
    matching (see matching.py). Also how a GPS position update is applied.
    """
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


# ---------- Shelters: list, edit ----------

@app.get("/shelters")
def list_shelters(db: Session = Depends(get_db)):
    return [_serialize_shelter(s) for s in db.query(Shelter).all()]


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


# ---------- Hospitals: list, create, edit ----------

@app.get("/hospitals")
def list_hospitals(db: Session = Depends(get_db)):
    return [_serialize_hospital(h) for h in db.query(Hospital).all()]


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


# ---------- Helpers ----------

def _get_report_or_404(db: Session, report_id: str) -> Report:
    report = db.query(Report).get(report_id)
    if not report:
        raise HTTPException(404, "Report not found")
    return report


def _set_unit_available(db: Session, unit_id: str, available: bool):
    unit = db.query(Unit).get(unit_id)
    if unit:
        unit.available = available


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
        "id": s.id,
        "name": s.name,
        "lat": s.lat,
        "lng": s.lng,
        "capacity": s.capacity,
        "current_occupancy": s.current_occupancy,
        "operational": s.operational,
    }


def _serialize_hospital(h: Hospital) -> dict:
    return {
        "id": h.id,
        "name": h.name,
        "lat": h.lat,
        "lng": h.lng,
        "total_beds": h.total_beds,
        "available_beds": h.available_beds,
        "has_er": h.has_er,
        "operational": h.operational,
    }


def _serialize_report(report: Report, db: Session) -> dict:
    unit = db.query(Unit).get(report.assigned_unit_id) if report.assigned_unit_id else None
    return {
        "id": report.id,
        "created_at": report.created_at.isoformat(),
        "location_label": report.location_label,
        "lat": report.lat,
        "lng": report.lng,
        "disaster_type": report.disaster_type.value if report.disaster_type else None,
        "channel": report.channel.value if report.channel else None,
        "victim_count": report.victim_count,
        "injuries": report.injuries,
        "children_present": report.children_present,
        "trapped": report.trapped,
        "water_rising": report.water_rising,
        "notes": report.notes,
        "severity": report.severity.value if report.severity else None,
        "severity_reasons": report.severity_reasons,
        "severity_explanation": report.severity_explanation,
        "status": report.status.value if report.status else None,
        "commander_message": report.commander_message or "",
        "assigned_unit": _serialize_unit(unit) if unit else None,
    }
