"""
Database layer — SQLite via SQLAlchemy.
Creates all tables on first run; safe to import multiple times.
"""

import os
from sqlalchemy import (
    create_engine, Column, Text, Integer, Float, Boolean,
    Date, DateTime, ForeignKey, event
)
from sqlalchemy.orm import DeclarativeBase, relationship, sessionmaker
from datetime import datetime

DB_PATH = os.path.join(os.path.dirname(__file__), "iris_rwe.db")
ENGINE = create_engine(f"sqlite:///{DB_PATH}", echo=False)


class Base(DeclarativeBase):
    pass


class Patient(Base):
    __tablename__ = "patients"

    patient_id       = Column(Text, primary_key=True)   # SHA-256 hash
    age_group        = Column(Text, nullable=False)      # bucketed
    gender           = Column(Text)
    ethnicity        = Column(Text)
    eye_laterality   = Column(Text)                     # OD / OS / OU
    diagnosis_code   = Column(Text, nullable=False)     # nAMD, DME, etc.
    baseline_bcva    = Column(Float)                    # ETDRS letters
    baseline_cst     = Column(Integer)                  # µm
    created_at       = Column(DateTime, default=datetime.utcnow)

    sessions         = relationship("TreatmentSession", back_populates="patient",
                                    cascade="all, delete-orphan")
    adverse_events   = relationship("AdverseEvent", back_populates="patient",
                                    cascade="all, delete-orphan")


class TreatmentSession(Base):
    __tablename__ = "treatment_sessions"

    session_id        = Column(Text, primary_key=True)
    patient_id        = Column(Text, ForeignKey("patients.patient_id"), nullable=False)
    visit_number      = Column(Integer, nullable=False)
    visit_date        = Column(Date, nullable=False)
    treating_drug     = Column(Text)
    injection_count   = Column(Integer, default=1)
    treatment_regimen = Column(Text)                    # PRN / T&E / Fixed
    iop_measured      = Column(Float)                   # mmHg

    patient           = relationship("Patient", back_populates="sessions")
    outcome           = relationship("EfficacyOutcome", back_populates="session",
                                     uselist=False, cascade="all, delete-orphan")
    adverse_events    = relationship("AdverseEvent", back_populates="session")


class EfficacyOutcome(Base):
    __tablename__ = "efficacy_outcomes"

    outcome_id         = Column(Text, primary_key=True)
    session_id         = Column(Text, ForeignKey("treatment_sessions.session_id"), nullable=False)
    patient_id         = Column(Text, ForeignKey("patients.patient_id"), nullable=False)
    bcva_letters       = Column(Float)    # ETDRS letters (0–100)
    bcva_logmar        = Column(Float)    # LogMAR (0.0–3.0)
    irf_present        = Column(Boolean)  # Intraretinal Fluid
    srf_present        = Column(Boolean)  # Subretinal Fluid
    irf_volume         = Column(Float)    # nL
    srf_volume         = Column(Float)    # nL
    cst_um             = Column(Integer)  # Central Subfield Thickness µm
    pigment_epi_detach = Column(Boolean)  # PED

    session            = relationship("TreatmentSession", back_populates="outcome")


class AdverseEvent(Base):
    __tablename__ = "adverse_events"

    ae_id          = Column(Text, primary_key=True)
    patient_id     = Column(Text, ForeignKey("patients.patient_id"), nullable=False)
    session_id     = Column(Text, ForeignKey("treatment_sessions.session_id"))
    ae_date        = Column(Date)
    ae_type        = Column(Text)       # Endophthalmitis, IOP spike, etc.
    ae_severity    = Column(Text)       # Mild / Moderate / Severe / SAE
    ae_outcome     = Column(Text)       # Resolved / Ongoing / Fatal
    ae_description = Column(Text)

    patient        = relationship("Patient", back_populates="adverse_events")
    session        = relationship("TreatmentSession", back_populates="adverse_events")


class AuditLog(Base):
    __tablename__ = "audit_log"

    log_id      = Column(Integer, primary_key=True, autoincrement=True)
    timestamp   = Column(DateTime, default=datetime.utcnow)
    action      = Column(Text)   # INSERT / VIEW / EXPORT
    table_name  = Column(Text)
    record_id   = Column(Text)
    user_role   = Column(Text)


Base.metadata.create_all(ENGINE)
SessionLocal = sessionmaker(bind=ENGINE)


def get_session():
    return SessionLocal()


def log_action(action: str, table: str, record_id: str, role: str = "analyst"):
    db = get_session()
    try:
        entry = AuditLog(action=action, table_name=table,
                         record_id=record_id, user_role=role)
        db.add(entry)
        db.commit()
    finally:
        db.close()
