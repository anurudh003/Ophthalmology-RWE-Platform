"""
Synthetic data generator for the Ophthalmology RWE Platform.

Generates clinically plausible data for:
  - 50 patients with nAMD, DME, or RVO diagnoses
  - 6-18 visits per patient (loading + maintenance phase)
  - Realistic BCVA trajectories: gain during loading, stable/variable in maintenance
  - IRF/SRF patterns correlated with BCVA
  - Proportional adverse event rates matching published literature
"""

import random
from datetime import datetime, timedelta

import numpy as np
from faker import Faker

from database.db import get_session, init_db
from database.models import AdverseEvent, Diagnosis, Outcome, Patient, Treatment, Visit
from utils.anonymizer import age_to_group, generate_patient_hash, logmar_to_etdrs, logmar_to_snellen

fake = Faker("en_GB")
rng = np.random.default_rng(42)

# ---------------------------------------------------------------------------
# Clinical reference data
# ---------------------------------------------------------------------------

DIAGNOSES = [
    {"icd10": "H35.31", "name": "Neovascular AMD (nAMD)",         "weight": 0.50},
    {"icd10": "H36.03", "name": "Diabetic Macular Oedema (DME)",  "weight": 0.30},
    {"icd10": "H34.83", "name": "Branch Retinal Vein Occlusion (BRVO)", "weight": 0.12},
    {"icd10": "H34.81", "name": "Central Retinal Vein Occlusion (CRVO)", "weight": 0.08},
]

DRUGS = {
    "nAMD": [
        ("Aflibercept",   2.0,  0.45),
        ("Ranibizumab",   0.5,  0.25),
        ("Bevacizumab",   1.25, 0.15),
        ("Faricimab",     6.0,  0.10),
        ("Brolucizumab",  6.0,  0.05),
    ],
    "DME": [
        ("Aflibercept",   2.0,  0.50),
        ("Ranibizumab",   0.5,  0.30),
        ("Bevacizumab",   1.25, 0.10),
        ("Faricimab",     6.0,  0.10),
    ],
    "BRVO": [
        ("Ranibizumab",   0.5,  0.50),
        ("Aflibercept",   2.0,  0.35),
        ("Bevacizumab",   1.25, 0.15),
    ],
    "CRVO": [
        ("Aflibercept",   2.0,  0.55),
        ("Ranibizumab",   0.5,  0.30),
        ("Bevacizumab",   1.25, 0.15),
    ],
}

ETHNICITIES = [
    ("White British",    0.72),
    ("White Other",      0.06),
    ("South Asian",      0.08),
    ("Black/African",    0.04),
    ("East Asian",       0.03),
    ("Mixed",            0.03),
    ("Other",            0.04),
]

VISIT_TYPES_LOADING    = ["Loading"] * 3          # first 3 visits
VISIT_TYPE_MAINTENANCE = ["Maintenance", "PRN", "T&E"]

OCULAR_AES = [
    ("IOP spike (>30 mmHg)",          0.04, 1),
    ("Subconjunctival haemorrhage",    0.08, 1),
    ("Vitreous floaters",             0.03, 1),
    ("Uveitis/inflammation",          0.02, 2),
    ("Retinal pigment epithelium tear",0.01, 2),
    ("Endophthalmitis",               0.003, 4),
    ("Retinal detachment",            0.002, 4),
    ("Arterial thromboembolic event", 0.005, 3),
]

SYSTEMIC_AES = [
    ("Hypertension (worsening)",  0.03, 2),
    ("Stroke/TIA",               0.005, 4),
    ("Myocardial infarction",    0.003, 4),
    ("Nausea/headache",          0.02, 1),
]


# ---------------------------------------------------------------------------
# Helper: weighted random choice
# ---------------------------------------------------------------------------

def _wchoice(options, weights):
    total = sum(weights)
    r = random.random() * total
    cumulative = 0
    for opt, w in zip(options, weights):
        cumulative += w
        if r <= cumulative:
            return opt
    return options[-1]


def _pick_diagnosis():
    names   = [d["name"]   for d in DIAGNOSES]
    weights = [d["weight"] for d in DIAGNOSES]
    idx     = names.index(_wchoice(names, weights))
    return DIAGNOSES[idx]


def _pick_drug(condition_key):
    entries  = DRUGS.get(condition_key, DRUGS["nAMD"])
    names    = [e[0] for e in entries]
    doses    = {e[0]: e[1] for e in entries}
    weights  = [e[2] for e in entries]
    drug     = _wchoice(names, weights)
    return drug, doses[drug]


def _condition_key(condition_name: str) -> str:
    if "AMD" in condition_name:   return "nAMD"
    if "Diabetic" in condition_name: return "DME"
    if "Branch" in condition_name:   return "BRVO"
    return "CRVO"


# ---------------------------------------------------------------------------
# BCVA trajectory simulation
# ---------------------------------------------------------------------------

def _simulate_bcva_trajectory(n_visits: int, baseline_logmar: float) -> list[float]:
    """
    Simulate a realistic logMAR trajectory:
      - Loading phase (visits 1-3): improvement of ~0.05-0.15 logMAR per visit
      - Maintenance: small random walk around achieved level with slow drift
    Lower logMAR = better vision.
    """
    trajectory = [baseline_logmar]
    current = baseline_logmar

    for i in range(1, n_visits):
        if i < 3:
            # Loading: consistent improvement
            delta = rng.normal(-0.07, 0.03)   # negative = improving
        elif i < 6:
            # Early maintenance: continued mild improvement
            delta = rng.normal(-0.02, 0.04)
        else:
            # Late maintenance: slight regression tendency
            delta = rng.normal(0.01, 0.05)

        current = max(0.0, min(2.5, current + delta))
        trajectory.append(round(current, 2))

    return trajectory


def _simulate_crt_trajectory(n_visits: int, baseline_crt: int) -> list[int]:
    """
    CRT typically drops 80-150 µm in loading phase, then stabilises.
    """
    trajectory = [baseline_crt]
    current = float(baseline_crt)

    for i in range(1, n_visits):
        if i < 3:
            delta = rng.normal(-50, 20)
        elif i < 6:
            delta = rng.normal(-15, 15)
        else:
            delta = rng.normal(5, 20)

        current = max(150, min(700, current + delta))
        trajectory.append(int(current))

    return trajectory


def _simulate_fluid(crt: int, baseline_crt: int) -> tuple[bool, bool]:
    """IRF/SRF presence correlated with CRT relative to baseline."""
    ratio = crt / baseline_crt
    irf = random.random() < min(0.95, ratio * 0.6)
    srf = random.random() < min(0.80, ratio * 0.4)
    return irf, srf


# ---------------------------------------------------------------------------
# Main seeder
# ---------------------------------------------------------------------------

def seed_database(n_patients: int = 500, force: bool = False) -> int:
    """
    Populate the database with synthetic patients and visit records.

    Args:
        n_patients: number of synthetic patients to create
        force:      if True, skip if records already exist

    Returns:
        Number of patients created (0 if skipped).
    """
    init_db()

    with get_session() as session:
        existing = session.query(Patient).count()
        if existing > 0 and not force:
            return 0

    created = 0
    for _ in range(n_patients):
        # --- Demographics ---
        given_name  = fake.first_name()
        family_name = fake.last_name()
        age         = int(rng.integers(45, 90))
        dob         = fake.date_of_birth(minimum_age=age, maximum_age=age + 1)
        sex         = _wchoice(["Male", "Female"], [0.48, 0.52])
        ethnicity   = _wchoice(
            [e[0] for e in ETHNICITIES],
            [e[1] for e in ETHNICITIES],
        )
        smoking     = _wchoice(["Never", "Ex-smoker", "Current"], [0.55, 0.30, 0.15])
        diabetic    = random.random() < 0.20
        hypertensive = random.random() < 0.45

        patient_hash = generate_patient_hash(given_name, family_name, dob)
        age_group    = age_to_group(age)

        # --- Diagnosis ---
        diag        = _pick_diagnosis()
        eye         = _wchoice(["OD", "OS", "OU"], [0.42, 0.42, 0.16])
        date_dx     = fake.date_time_between(start_date="-3y", end_date="-6m")
        cond_key    = _condition_key(diag["name"])
        drug, dose  = _pick_drug(cond_key)

        # --- Baseline BCVA & CRT ---
        # nAMD/CRVO tend to present with worse VA than BRVO
        if cond_key in ("nAMD", "CRVO"):
            baseline_logmar = round(float(rng.uniform(0.40, 1.00)), 2)
            baseline_crt    = int(rng.integers(320, 620))
        else:
            baseline_logmar = round(float(rng.uniform(0.20, 0.70)), 2)
            baseline_crt    = int(rng.integers(280, 520))

        # --- Visit count (fixed at 12 per patient) ---
        n_visits = 12

        # Simulate trajectories
        bcva_traj = _simulate_bcva_trajectory(n_visits, baseline_logmar)
        crt_traj  = _simulate_crt_trajectory(n_visits, baseline_crt)

        # First visit date shortly after diagnosis
        first_visit = date_dx + timedelta(days=int(rng.integers(7, 30)))

        with get_session() as session:
            # Patient record
            patient = Patient(
                patient_hash=patient_hash,
                age_group=age_group,
                sex=sex,
                ethnicity=ethnicity,
                smoking_status=smoking,
                diabetes=diabetic,
                hypertension=hypertensive,
            )
            session.add(patient)
            session.flush()  # get patient.id

            # Diagnosis
            dx = Diagnosis(
                patient_id=patient.id,
                eye=eye,
                icd10_code=diag["icd10"],
                condition_name=diag["name"],
                date_diagnosed=date_dx,
            )
            session.add(dx)

            # Visits
            visit_date = first_visit
            injection_count = 0

            for v_idx in range(n_visits):
                # Visit interval: ~4 weeks loading, variable maintenance
                if v_idx > 0:
                    if v_idx < 3:
                        interval_days = int(rng.integers(25, 35))
                    else:
                        interval_days = int(rng.integers(28, 84))
                    visit_date = visit_date + timedelta(days=interval_days)

                if v_idx < 3:
                    v_type = "Loading"
                else:
                    v_type = _wchoice(VISIT_TYPE_MAINTENANCE, [0.40, 0.30, 0.30])

                visit = Visit(
                    patient_id=patient.id,
                    visit_date=visit_date,
                    visit_number=v_idx + 1,
                    eye=eye,
                    visit_type=v_type,
                    clinician_code=f"CLIN-{rng.integers(1, 8):02d}",
                    site_code=f"SITE-{rng.integers(1, 4):02d}",
                )
                session.add(visit)
                session.flush()

                # Treatment (most but not all visits get an injection)
                inject_this_visit = v_idx < 3 or random.random() < 0.75
                if inject_this_visit:
                    injection_count += 1
                    treat = Treatment(
                        visit_id=visit.id,
                        drug_name=drug,
                        drug_dose_mg=dose,
                        injection_number=injection_count,
                        injection_site="Intravitreal",
                    )
                    session.add(treat)

                # Outcome
                logmar_val = bcva_traj[v_idx]
                crt_val    = crt_traj[v_idx]
                irf, srf   = _simulate_fluid(crt_val, baseline_crt)
                iop        = round(float(rng.normal(15, 3)), 1)

                outcome = Outcome(
                    visit_id=visit.id,
                    bcva_logmar=logmar_val,
                    bcva_etdrs_letters=logmar_to_etdrs(logmar_val),
                    bcva_snellen=logmar_to_snellen(logmar_val),
                    crt_um=crt_val,
                    irf_present=irf,
                    srf_present=srf,
                    ped_height_um=int(rng.integers(0, 250)) if srf else None,
                    iop_mmhg=max(8.0, min(30.0, iop)),
                    bcva_change_from_baseline=round(
                        (baseline_logmar - logmar_val) * 50, 1   # letters equivalent
                    ),
                    crt_change_from_baseline=baseline_crt - crt_val,
                )
                session.add(outcome)

                # Adverse events (low probability per visit)
                for ae_name, prob, grade in OCULAR_AES:
                    if random.random() < prob:
                        ae = AdverseEvent(
                            visit_id=visit.id,
                            ae_type=ae_name,
                            ae_category="Ocular",
                            severity_grade=grade,
                            serious=grade >= 3,
                            related_to_treatment=True,
                            onset_date=visit_date,
                            resolved=grade < 3,
                        )
                        session.add(ae)

                for ae_name, prob, grade in SYSTEMIC_AES:
                    if random.random() < prob:
                        ae = AdverseEvent(
                            visit_id=visit.id,
                            ae_type=ae_name,
                            ae_category="Systemic",
                            severity_grade=grade,
                            serious=grade >= 3,
                            related_to_treatment=random.random() < 0.3,
                            onset_date=visit_date,
                            resolved=grade < 3,
                        )
                        session.add(ae)

        created += 1

    return created
