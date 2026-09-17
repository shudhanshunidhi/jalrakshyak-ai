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
    SOS = "SOS"
    ASSIGNED = "Assigned"
    EN_ROUTE = "En Route"
    RESCUED = "Rescued"


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

    # Location — lat/lng for distance calc against units; free-text label for the dashboard
    location_label = Column(String, nullable=False)
    lat = Column(Float, nullable=False)
    lng = Column(Float, nullable=False)

    disaster_type = Column(SAEnum(DisasterType), default=DisasterType.FLOOD)
    channel = Column(SAEnum(Channel), default=Channel.APP)

    # Inputs the scoring engine actually uses
    victim_count = Column(Integer, default=1)
    injuries = Column(Boolean, default=False)
    children_present = Column(Boolean, default=False)
    trapped = Column(Boolean, default=False)
    water_rising = Column(Boolean, default=False)
    notes = Column(String, default="")  # free text, e.g. from voice input

    # Filled in by the scoring engine (Step 2) — nullable until scored
    severity = Column(SAEnum(Severity), nullable=True)
    severity_reasons = Column(String, default="")  # comma-joined reason tags
    severity_explanation = Column(String, default="")  # LLM plain-language version

    # Filled in by matching (Step 3)
    assigned_unit_id = Column(String, nullable=True)

    status = Column(SAEnum(IncidentStatus), default=IncidentStatus.SOS)

    # What the commander tells the citizen back — set on approve/modify.
    # This is what the citizen's report.html polls for and displays.
    commander_message = Column(String, default="")

    # Offline queue support — client sets this locally, server just echoes it back
    synced = Column(Boolean, default=True)


class Unit(Base):
    """A rescue unit available for dispatch — GPS-trackable, condition-aware."""
    __tablename__ = "units"

    id = Column(String, primary_key=True, default=gen_id)
    name = Column(String, nullable=False)
    type = Column(SAEnum(UnitType), nullable=False)
    lat = Column(Float, nullable=False)
    lng = Column(Float, nullable=False)
    available = Column(Boolean, default=True)  # busy/free (dispatched or not)
    condition = Column(SAEnum(UnitCondition), default=UnitCondition.OPERATIONAL)  # vehicle fit for use?
    last_gps_update = Column(DateTime, default=datetime.utcnow)  # when location was last reported


class Shelter(Base):
    """A relief shelter — capacity/occupancy panel."""
    __tablename__ = "shelters"

    id = Column(String, primary_key=True, default=gen_id)
    name = Column(String, nullable=False)
    lat = Column(Float, nullable=False)
    lng = Column(Float, nullable=False)
    capacity = Column(Integer, nullable=False)
    current_occupancy = Column(Integer, default=0)
    operational = Column(Boolean, default=True)  # can be marked closed/unusable


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
    operational = Column(Boolean, default=True)  # can be marked overwhelmed/closed
