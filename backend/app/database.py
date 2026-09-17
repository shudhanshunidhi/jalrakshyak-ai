"""
DB setup. SQLite file lives at backend/jalrakshyak.db — nothing to install,
nothing to configure. Good enough for a demo dataset per the proposal.
"""

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from .models import Base, Unit, Shelter, Hospital, UnitType, UnitCondition

DATABASE_URL = "sqlite:///./jalrakshyak.db"

engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)


def init_db():
    Base.metadata.create_all(bind=engine)
    _seed_demo_data()


def _seed_demo_data():
    """Populate a small realistic dataset around Kathmandu — only if empty."""
    session = SessionLocal()
    try:
        if session.query(Unit).count() > 0:
            return  # already seeded

        demo_units = [
            Unit(name="Boat Unit 1 - Thapathali Ghat", type=UnitType.BOAT, lat=27.6944, lng=85.3197,
                 available=True, condition=UnitCondition.OPERATIONAL),
            Unit(name="Boat Unit 2 - Teku Confluence", type=UnitType.BOAT, lat=27.6947, lng=85.3082,
                 available=True, condition=UnitCondition.OPERATIONAL),
            Unit(name="Ambulance 1 - Kathmandu HQ", type=UnitType.AMBULANCE, lat=27.7040, lng=85.3145,
                 available=True, condition=UnitCondition.OPERATIONAL),
            Unit(name="Ambulance 2 - Koteshwor", type=UnitType.AMBULANCE, lat=27.6789, lng=85.3487,
                 available=False, condition=UnitCondition.UNDER_MAINTENANCE),
            Unit(name="Relief Team Alpha", type=UnitType.RELIEF_TEAM, lat=27.7000, lng=85.3200,
                 available=True, condition=UnitCondition.OPERATIONAL),
        ]
        demo_shelters = [
            Shelter(name="Kathmandu Model School Shelter", lat=27.6980, lng=85.3160, capacity=200, current_occupancy=40),
            Shelter(name="Kupondole Community Hall", lat=27.6890, lng=85.3160, capacity=80, current_occupancy=75),
        ]
        demo_hospitals = [
            Hospital(name="Tribhuvan University Teaching Hospital", lat=27.7333, lng=85.3167,
                     total_beds=500, available_beds=120, has_er=True, operational=True),
            Hospital(name="Bir Hospital", lat=27.7040, lng=85.3132,
                     total_beds=350, available_beds=40, has_er=True, operational=True),
            Hospital(name="Patan Hospital", lat=27.6720, lng=85.3247,
                     total_beds=300, available_beds=65, has_er=True, operational=True),
        ]
        session.add_all(demo_units + demo_shelters + demo_hospitals)
        session.commit()
    finally:
        session.close()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
