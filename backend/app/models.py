"""
Step 1: Data models for JALRAKSHYAK AI

These map directly onto the "Data Sources" section of the proposal:
- Emergency reports (structured intake)
- Rescue units (type, location, availability)
- Shelters (capacity, occupancy)

Using SQLAlchemy + SQLite as stated in the proposal ("local JSON / SQLite").
"""

from datetime import datetime
import enum
import uuid

from sqlalchemy import Column, String, Integer, Boolean, DateTime, Float, Enum as SAEnum
from sqlalchemy.orm import declarative_base

Base = declarative_base()


def gen_id() -> str:
    return str(uuid.uuid4())[:8]


class DisasterType(str, enum.Enum):
    FLOOD = "flood"
    LANDSLIDE = "landslide"
    EARTHQUAKE = "earthquake"
    FIRE = "fire"
    OTHER = "other"


class Channel(str, enum.Enum):
    APP = "App"
    SMS = "SMS"
    RADIO = "Radio"


class Severity(str, enum.Enum):
    CRITICAL = "CRITICAL"
    HIGH = "HIGH"
    MODERATE = "MODERATE"


class IncidentStatus(str, enum.Enum):
    """
    The OVERALL incident status — set by the commander (Approve/Modify/Reject,
    or the lifecycle dropdown). This is separate from each individual unit's
    own dispatch status (see DispatchStatus below) — a report can show
    "Assigned" overall while one of its units is already "En Route" and
    another is still "Assigned".
    """
    SOS = "SOS"
    ASSIGNED = "Assigned"
    EN_ROUTE = "En Route"
    RESCUED = "Rescued"
    REJECTED = "Rejected"


class DispatchStatus(str, enum.Enum):
    """
    A single UNIT's own progress on a single incident — a real 4-stage
    journey, not a single "en route" that conflates two very different
    things:
      Assigned      -> the unit has been tasked, hasn't left yet
      En Route      -> driver is heading TO THE VICTIM (no headcount yet —
                        nobody's been picked up)
      Transporting  -> driver has loaded people and is now heading to the
                        hospital/shelter — THIS is the moment the headcount
                        is set and the hospital/shelter actually gets notified
      Complete      -> delivered / done, unit freed
    Set by the DRIVER (driver.html), not the commander.
    """
    ASSIGNED = "Assigned"
    EN_ROUTE = "En Route"
    TRANSPORTING = "Transporting"
    COMPLETE = "Complete"


class UnitType(str, enum.Enum):
    BOAT = "boat"
    AMBULANCE = "ambulance"
    RELIEF_TEAM = "relief_team"


class UnitCondition(str, enum.Enum):
    OPERATIONAL = "operational"
    UNDER_MAINTENANCE = "under_maintenance"
    OUT_OF_SERVICE = "out_of_service"


class Report(Base):
    """
    A single emergency report — this is what the intake form (Step 6 in the
    proposal's feature list) creates. Kept deliberately flat/structured so
    the scoring engine can read it directly with no parsing step.
    """
    __tablename__ = "reports"

    id = Column(String, primary_key=True, default=gen_id)
    created_at = Column(DateTime, default=datetime.utcnow)

    location_label = Column(String, nullable=False)
    lat = Column(Float, nullable=False)
    lng = Column(Float, nullable=False)

    disaster_type = Column(SAEnum(DisasterType), default=DisasterType.FLOOD)
    channel = Column(SAEnum(Channel), default=Channel.APP)

    # Total people at the scene, plus a precise breakdown by category —
    # e.g. 9 total, 5 injured, 3 trapped is now captured exactly, not
    # collapsed into a single "injured: yes/no" flag.
    victim_count = Column(Integer, default=1)
    injured_count = Column(Integer, default=0)
    trapped_count = Column(Integer, default=0)
    children_count = Column(Integer, default=0)
    water_rising = Column(Boolean, default=False)  # situational, not a headcount
    notes = Column(String, default="")

    severity = Column(SAEnum(Severity), nullable=True)
    severity_reasons = Column(String, default="")
    severity_explanation = Column(String, default="")

    duplicate_of_id = Column(String, nullable=True)

    destination_hospital_id = Column(String, nullable=True)
    destination_shelter_id = Column(String, nullable=True)

    status = Column(SAEnum(IncidentStatus), default=IncidentStatus.SOS)
    commander_message = Column(String, default="")
    synced = Column(Boolean, default=True)


class Dispatch(Base):
    """
    One unit's assignment to one report. This is the join table that makes
    multi-unit dispatch and PER-UNIT status tracking possible: a report can
    have several Dispatch rows (one boat, one ambulance), each progressing
    through Assigned -> En Route -> Transporting -> Complete independently,
    driven by that unit's own driver in driver.html.

    headcount: set by the driver when marking Transporting — "bringing 2
    patients" — this is what powers the real headcount notification on
    hospital.html / shelter.html.

    destination_hospital_id / destination_shelter_id: WHERE THIS SPECIFIC
    UNIT is actually headed, chosen by its own driver at the moment they
    mark Transporting. This is deliberately per-dispatch, not per-report —
    an ambulance heading to a hospital and a relief team heading to a
    shelter on the SAME incident must not show up on each other's boards
    just because the report has both destinations set somewhere.
    """
    __tablename__ = "dispatches"

    id = Column(String, primary_key=True, default=gen_id)
    report_id = Column(String, nullable=False)
    unit_id = Column(String, nullable=False)
    status = Column(SAEnum(DispatchStatus), default=DispatchStatus.ASSIGNED)
    headcount = Column(Integer, nullable=True)
    destination_hospital_id = Column(String, nullable=True)
    destination_shelter_id = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)


class Unit(Base):
    """A rescue unit available for dispatch — GPS-trackable, condition-aware."""
    __tablename__ = "units"

    id = Column(String, primary_key=True, default=gen_id)
    name = Column(String, nullable=False)
    type = Column(SAEnum(UnitType), nullable=False)
    lat = Column(Float, nullable=False)
    lng = Column(Float, nullable=False)
    available = Column(Boolean, default=True)
    condition = Column(SAEnum(UnitCondition), default=UnitCondition.OPERATIONAL)
    last_gps_update = Column(DateTime, default=datetime.utcnow)


class Shelter(Base):
    """A relief shelter — capacity/occupancy panel."""
    __tablename__ = "shelters"

    id = Column(String, primary_key=True, default=gen_id)
    name = Column(String, nullable=False)
    lat = Column(Float, nullable=False)
    lng = Column(Float, nullable=False)
    capacity = Column(Integer, nullable=False)
    current_occupancy = Column(Integer, default=0)
    operational = Column(Boolean, default=True)


class Hospital(Base):
    """A hospital — bed capacity and whether it can currently take patients."""
    __tablename__ = "hospitals"

    id = Column(String, primary_key=True, default=gen_id)
    name = Column(String, nullable=False)
    lat = Column(Float, nullable=False)
    lng = Column(Float, nullable=False)
    total_beds = Column(Integer, nullable=False)
    available_beds = Column(Integer, default=0)
    has_er = Column(Boolean, default=True)
    operational = Column(Boolean, default=True)
