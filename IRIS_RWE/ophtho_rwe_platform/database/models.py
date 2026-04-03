"""
SQLAlchemy ORM models for the Ophthalmology RWE Platform.

Tables:
  patients        — anonymized demographics
  diagnoses       — ICD-10 coded conditions per patient-eye
  visits          — clinic encounter records
  treatments      — anti-VEGF / other injections per visit
  outcomes        — BCVA, CRT, IRF/SRF per visit
  adverse_events  — AE records linked to visit
"""

import enum
from datetime import datetime

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    CheckConstraint,
)
from sqlalchemy.orm import declarative_base, relationship

Base = declarative_base()


# ---------------------------------------------------------------------------
# Diagnosis catalogue — ICD-10 condition codes
# ---------------------------------------------------------------------------

# Ordered list of the 5 most common anti-VEGF indication groups seen in
# intravitreal injection clinics (England/Wales NICE-approved indications).
# Each entry: display_label → (condition_code, condition_name)
#
#   condition_code  — canonical ICD-10 chapter/block code stored in the DB
#                     (VARCHAR 10, e.g. "H35.31" for nAMD)
#   condition_name  — short clinical name used throughout the platform
#
# "Other" is the catch-all; clinicians enter a free-text name and the
# code defaults to H35.99 (Other specified retinal disorders).
#
# ICD-10 references:
#   H35.31 — Age-related macular degeneration, neovascular (wet AMD)
#   H36.0  — Diabetic retinopathy / macular oedema (DME)
#   H34.x  — Retinal vascular occlusions (BRVO H34.83 / CRVO H34.81)
#   H40.x  — Glaucoma (included for completeness; VEGF role in NVG)
#   H25.x  — Age-related cataract (context diagnosis, pre-/post-op VA)
#   H35.30 — Unspecified macular degeneration (PCV / dry AMD)
#   H44.20 — Myopic macular degeneration / myopic CNV
#   H35.99 — Other specified retinal disorders (catch-all)

CONDITION_CATALOGUE: dict[str, tuple[str, str]] = {
    # label                                    : (condition_code, condition_name)
    "Neovascular AMD (nAMD)":                   ("H35.31", "nAMD"),
    "Diabetic Macular Oedema (DME)":            ("H36.0",  "DME"),
    "Branch Retinal Vein Occlusion (BRVO)":    ("H34.83", "BRVO"),
    "Central Retinal Vein Occlusion (CRVO)":   ("H34.81", "CRVO"),
    "Retinal Vein Occlusion — unspecified":     ("H34.9",  "RVO"),
    "Glaucoma (neovascular / other)":           ("H40.9",  "Glaucoma"),
    "Age-related Cataract":                     ("H25.9",  "Cataract"),
    "Polypoidal Choroidal Vasculopathy (PCV)":  ("H35.30", "PCV"),
    "Myopic CNV":                               ("H44.20", "Myopic CNV"),
    "Other":                                    ("H35.99", "Other"),
}


# ---------------------------------------------------------------------------
# AE classification enum
# ---------------------------------------------------------------------------

class AEClassification(str, enum.Enum):
    """
    Controlled vocabulary for adverse event classification in
    intravitreal injection clinics.

    Inherits str so values serialise cleanly to/from the database
    column (stored as VARCHAR, not a native DB enum type — keeps
    SQLite and PostgreSQL compatibility).

    Ocular:
      endophthalmitis         — sight-threatening, always SAE
      iop_spike               — IOP >30 mmHg post-injection
      subconjunctival_hemorrhage — benign but common, patient concern
      retinal_detachment      — rhegmatogenous or tractional
      rpe_tear                — RPE damage, risk of severe VA loss
      uveitis                 — anterior chamber / vitreous inflammation
      vitreous_floaters       — vitreous opacification post-injection

    Systemic:
      thromboembolic_event    — APTC composite: stroke, MI, vascular death
      hypertension_worsening  — new or worsening systemic hypertension

    Catch-all:
      other                   — anything not covered above; use description
    """
    # Ocular
    ENDOPHTHALMITIS             = "Endophthalmitis"
    IOP_SPIKE                   = "IOP spike (>30 mmHg)"
    SUBCONJUNCTIVAL_HEMORRHAGE  = "Subconjunctival haemorrhage"
    RETINAL_DETACHMENT          = "Retinal detachment"
    RPE_TEAR                    = "RPE tear"
    UVEITIS                     = "Uveitis / Anterior chamber inflammation"
    VITREOUS_FLOATERS           = "Vitreous floaters"
    # Systemic
    THROMBOEMBOLIC_EVENT        = "Arterial thromboembolic event"
    HYPERTENSION_WORSENING      = "Hypertension (worsening)"
    # Catch-all
    OTHER                       = "Other"


# Derived lookup tables — used by the form and analytics layer
# so category logic lives in exactly one place.

#: AEClassification values that are ocular in origin
OCULAR_AE_CLASSIFICATIONS: frozenset[AEClassification] = frozenset({
    AEClassification.ENDOPHTHALMITIS,
    AEClassification.IOP_SPIKE,
    AEClassification.SUBCONJUNCTIVAL_HEMORRHAGE,
    AEClassification.RETINAL_DETACHMENT,
    AEClassification.RPE_TEAR,
    AEClassification.UVEITIS,
    AEClassification.VITREOUS_FLOATERS,
})

#: AEClassification values that are always treated as Serious Adverse Events
ALWAYS_SAE_CLASSIFICATIONS: frozenset[AEClassification] = frozenset({
    AEClassification.ENDOPHTHALMITIS,
    AEClassification.RETINAL_DETACHMENT,
    AEClassification.THROMBOEMBOLIC_EVENT,
})


class Patient(Base):
    __tablename__ = "patients"

    id = Column(Integer, primary_key=True, autoincrement=True)
    # One-way hash of name+DOB — no PII stored
    patient_hash = Column(String(64), unique=True, nullable=False, index=True)
    # Generalised demographics (k-anonymity)
    age_group = Column(String(10), nullable=False)          # e.g. "60-69"
    sex = Column(String(10), nullable=False)                 # Male / Female / Other
    ethnicity = Column(String(40), nullable=True)
    smoking_status = Column(String(20), nullable=True)       # Never / Ex / Current
    diabetes = Column(Boolean, default=False)
    hypertension = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    diagnoses = relationship("Diagnosis", back_populates="patient", cascade="all, delete-orphan")
    visits = relationship("Visit", back_populates="patient", cascade="all, delete-orphan")

    def __repr__(self):
        return f"<Patient hash={self.patient_hash[:8]}… age={self.age_group}>"


class Diagnosis(Base):
    __tablename__ = "diagnoses"

    id = Column(Integer, primary_key=True, autoincrement=True)
    patient_id = Column(Integer, ForeignKey("patients.id"), nullable=False)
    eye = Column(String(3), nullable=False)                  # OD / OS / OU
    condition_code = Column(String(10), nullable=True)       # canonical ICD-10 block, e.g. H35.31
    icd10_code = Column(String(10), nullable=False)          # legacy / specific sub-code (same source)
    condition_name = Column(String(80), nullable=False)      # short clinical name, e.g. nAMD
    date_diagnosed = Column(DateTime, nullable=True)
    notes = Column(Text, nullable=True)

    patient = relationship("Patient", back_populates="diagnoses")

    __table_args__ = (
        CheckConstraint("eye IN ('OD','OS','OU')", name="ck_diagnosis_eye"),
    )


class Visit(Base):
    __tablename__ = "visits"

    id = Column(Integer, primary_key=True, autoincrement=True)
    patient_id = Column(Integer, ForeignKey("patients.id"), nullable=False)
    visit_date = Column(DateTime, nullable=False)
    visit_number = Column(Integer, nullable=False)           # sequential per patient
    eye = Column(String(3), nullable=False)                  # OD / OS / OU
    visit_type = Column(String(20), nullable=False)          # Loading / Maintenance / PRN / T&E
    clinician_code = Column(String(20), nullable=True)       # anonymised clinician ref
    site_code = Column(String(20), nullable=True)            # anonymised site ref
    notes = Column(Text, nullable=True)

    patient = relationship("Patient", back_populates="visits")
    treatments = relationship("Treatment", back_populates="visit", cascade="all, delete-orphan")
    outcomes = relationship("Outcome", back_populates="visit", cascade="all, delete-orphan")
    adverse_events = relationship("AdverseEvent", back_populates="visit", cascade="all, delete-orphan")

    __table_args__ = (
        CheckConstraint("eye IN ('OD','OS','OU')", name="ck_visit_eye"),
    )


class Treatment(Base):
    __tablename__ = "treatments"

    id = Column(Integer, primary_key=True, autoincrement=True)
    visit_id = Column(Integer, ForeignKey("visits.id"), nullable=False)
    drug_name = Column(String(60), nullable=False)           # Aflibercept, Ranibizumab, etc.
    drug_dose_mg = Column(Float, nullable=True)              # e.g. 2.0 mg
    injection_number = Column(Integer, nullable=True)        # cumulative injection count
    injection_site = Column(String(20), nullable=True)       # IVT (intravitreal), sub-tenon, etc.
    concomitant_medications = Column(Text, nullable=True)    # CSV of other meds
    reason_for_change = Column(Text, nullable=True)          # if switching drug

    visit = relationship("Visit", back_populates="treatments")


class Outcome(Base):
    __tablename__ = "outcomes"

    id = Column(Integer, primary_key=True, autoincrement=True)
    visit_id = Column(Integer, ForeignKey("visits.id"), nullable=False)

    # Visual acuity
    bcva_logmar = Column(Float, nullable=True)               # e.g. 0.30 = 6/12 Snellen
    bcva_etdrs_letters = Column(Integer, nullable=True)      # 0–100 ETDRS letters
    bcva_snellen = Column(String(10), nullable=True)         # e.g. "6/12"

    # Structural / imaging (OCT)
    crt_um = Column(Integer, nullable=True)                  # Central Retinal Thickness µm
    irf_present = Column(Boolean, nullable=True)             # Intraretinal fluid
    srf_present = Column(Boolean, nullable=True)             # Subretinal fluid
    ped_height_um = Column(Integer, nullable=True)           # Pigment epithelial detachment

    # Pressure
    iop_mmhg = Column(Float, nullable=True)                  # Intraocular pressure

    # Change from baseline (computed at entry time for convenience)
    bcva_change_from_baseline = Column(Float, nullable=True) # letters gained (positive = better)
    crt_change_from_baseline = Column(Integer, nullable=True)

    visit = relationship("Visit", back_populates="outcomes")


class AdverseEvent(Base):
    __tablename__ = "adverse_events"

    id = Column(Integer, primary_key=True, autoincrement=True)
    visit_id = Column(Integer, ForeignKey("visits.id"), nullable=False)

    # Controlled-vocabulary classification (AEClassification enum, stored as VARCHAR)
    ae_classification = Column(
        String(60),
        nullable=False,
        default=AEClassification.OTHER.value,
    )
    ae_type = Column(String(80), nullable=False)             # legacy free-text / display label
    ae_category = Column(String(40), nullable=True)          # Ocular / Systemic (derived)
    severity_grade = Column(Integer, nullable=True)          # CTCAE 1–5
    serious = Column(Boolean, default=False)                 # SAE flag
    related_to_treatment = Column(Boolean, nullable=True)
    onset_date = Column(DateTime, nullable=True)
    resolution_date = Column(DateTime, nullable=True)
    resolved = Column(Boolean, default=False)
    description = Column(Text, nullable=True)

    visit = relationship("Visit", back_populates="adverse_events")

    __table_args__ = (
        CheckConstraint("severity_grade BETWEEN 1 AND 5", name="ck_ae_severity"),
        CheckConstraint(
            "ae_classification IN ({})".format(
                ",".join(f"'{c.value}'" for c in AEClassification)
            ),
            name="ck_ae_classification",
        ),
    )


class AuditLog(Base):
    """
    Immutable audit trail — who did what, when.

    Records: login attempts, page access, data exports, DB resets.
    Rows are INSERT-only; no update/delete paths exist in application code.
    """
    __tablename__ = "audit_log"

    id           = Column(Integer, primary_key=True, autoincrement=True)
    timestamp    = Column(DateTime, nullable=False, default=datetime.utcnow, index=True)
    username     = Column(String(64), nullable=False, index=True)
    action       = Column(String(60), nullable=False)   # LOGIN_SUCCESS, EXPORT_CSV, etc.
    detail       = Column(String(500), nullable=True)   # page, filters, file name, etc.
    record_count = Column(Integer, nullable=True)       # rows exported (if applicable)
    ip_address   = Column(String(45), nullable=True)    # reserved for future use

    def __repr__(self):
        return f"<AuditLog {self.timestamp} {self.username} {self.action}>"
