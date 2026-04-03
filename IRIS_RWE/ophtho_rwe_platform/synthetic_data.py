"""
Synthetic Data Generator
Produces clinically realistic ophthalmology patient cohorts
mirroring published trial outcomes (CATT, VIEW, HAWK/HARRIER).

Run directly:  python synthetic_data.py
"""

import uuid
import random
import hashlib
import numpy as np
import pandas as pd
from datetime import date, timedelta
from faker import Faker

from database import get_session, Patient, TreatmentSession, EfficacyOutcome, AdverseEvent
from anonymization import age_to_bucket, get_date_shift, etdrs_to_logmar

fake = Faker()
rng = np.random.default_rng(42)

# ── Clinical Reference Data ───────────────────────────────────────────────────

DRUGS = ["Aflibercept", "Ranibizumab", "Bevacizumab", "Faricimab", "Brolucizumab"]
DIAGNOSES = ["nAMD", "DME", "RVO-ME", "Myopic CNV"]
REGIMENS = ["Fixed Monthly", "PRN", "Treat-and-Extend"]
LATERALITY = ["OD", "OS", "OU"]
ETHNICITIES = ["White British", "South Asian", "Black African", "East Asian",
               "Hispanic", "Mixed", "Other"]
GENDERS = ["Male", "Female", "Non-binary / Other"]

AE_TYPES = [
    "IOP Spike", "Subconjunctival Haemorrhage", "Endophthalmitis",
    "Retinal Detachment", "Uveitis", "Vitreous Floaters", "RPE Tear"
]
AE_SEVERITIES = ["Mild", "Moderate", "Severe", "SAE"]
AE_OUTCOMES   = ["Resolved", "Ongoing", "Resolved with Sequelae"]

# Drug-specific mean BCVA gains (ETDRS letters) — based on published data
DRUG_PROFILES = {
    "Aflibercept":  {"gain_mean": 8.4, "gain_sd": 4.0, "ae_rate": 0.08},
    "Ranibizumab":  {"gain_mean": 7.2, "gain_sd": 4.5, "ae_rate": 0.09},
    "Bevacizumab":  {"gain_mean": 6.9, "gain_sd": 5.0, "ae_rate": 0.10},
    "Faricimab":    {"gain_mean": 9.1, "gain_sd": 3.8, "ae_rate": 0.07},
    "Brolucizumab": {"gain_mean": 8.0, "gain_sd": 4.2, "ae_rate": 0.12},
}

DIAGNOSIS_BCVA_BASELINE = {
    "nAMD":      {"mean": 54, "sd": 12},
    "DME":       {"mean": 60, "sd": 10},
    "RVO-ME":    {"mean": 48, "sd": 15},
    "Myopic CNV":{"mean": 62, "sd": 8},
}

DIAGNOSIS_CST_BASELINE = {
    "nAMD":      {"mean": 380, "sd": 70},
    "DME":       {"mean": 450, "sd": 90},
    "RVO-ME":    {"mean": 520, "sd": 100},
    "Myopic CNV":{"mean": 310, "sd": 50},
}


# ── BCVA Trajectory Simulation ────────────────────────────────────────────────

def simulate_bcva_trajectory(baseline: float, drug: str,
                              n_visits: int = 12) -> list[float]:
    """
    Simulate realistic BCVA (ETDRS letters) response curves.
    Phase 1 (v1–3): loading — rapid gain
    Phase 2 (v4–8): maintenance plateau
    Phase 3 (v9+):  mild regression in some patients
    """
    profile = DRUG_PROFILES[drug]
    trajectory = [baseline]
    responder = rng.random() > 0.25  # 75 % responder rate

    for v in range(1, n_visits):
        if v <= 3:
            mu = profile["gain_mean"] / 3 if responder else -1.5
            delta = rng.normal(mu, 2.0)
        elif v <= 8:
            mu = 0.8 if responder else -0.5
            delta = rng.normal(mu, 2.5)
        else:
            mu = -0.3 if responder else -1.0
            delta = rng.normal(mu, 2.0)

        new_val = float(np.clip(trajectory[-1] + delta, 0, 100))
        trajectory.append(round(new_val, 1))

    return trajectory


def simulate_cst_trajectory(baseline_cst: int, n_visits: int = 12) -> list[int]:
    """CST decreases rapidly after loading, then stabilises."""
    trajectory = [baseline_cst]
    for v in range(1, n_visits):
        if v <= 3:
            delta = rng.normal(-40, 15)
        elif v <= 8:
            delta = rng.normal(-8, 10)
        else:
            delta = rng.normal(-2, 8)
        new_val = int(np.clip(trajectory[-1] + delta, 150, 700))
        trajectory.append(new_val)
    return trajectory


def fluid_status_from_cst(cst: int) -> tuple[bool, bool, float, float]:
    """Derive IRF/SRF presence and volumes from CST."""
    irf = cst > 320
    srf = cst > 380
    irf_vol = round(float(rng.uniform(0.05, 0.8)), 3) if irf else 0.0
    srf_vol = round(float(rng.uniform(0.02, 0.5)), 3) if srf else 0.0
    return irf, srf, irf_vol, srf_vol


# ── Main Generator ────────────────────────────────────────────────────────────

def generate_synthetic_cohort(n_patients: int = 200, n_visits: int = 12,
                               clear_existing: bool = True) -> None:
    db = get_session()

    if clear_existing:
        db.query(AdverseEvent).delete()
        db.query(EfficacyOutcome).delete()
        db.query(TreatmentSession).delete()
        db.query(Patient).delete()
        db.commit()

    for _ in range(n_patients):
        # ── Demographics ──────────────────────────────────────────────────────
        raw_id   = fake.uuid4()
        pid      = hashlib.sha256(raw_id.encode()).hexdigest()[:12]
        age      = int(rng.integers(45, 88))
        age_grp  = age_to_bucket(age)
        gender   = rng.choice(GENDERS, p=[0.48, 0.50, 0.02])
        ethnicity = rng.choice(ETHNICITIES)
        lateral  = rng.choice(LATERALITY, p=[0.45, 0.45, 0.10])
        diagnosis = rng.choice(DIAGNOSES, p=[0.45, 0.30, 0.20, 0.05])
        drug     = rng.choice(DRUGS)
        regimen  = rng.choice(REGIMENS)

        bcva_cfg = DIAGNOSIS_BCVA_BASELINE[diagnosis]
        cst_cfg  = DIAGNOSIS_CST_BASELINE[diagnosis]
        baseline_bcva = float(np.clip(rng.normal(bcva_cfg["mean"], bcva_cfg["sd"]), 10, 90))
        baseline_cst  = int(np.clip(rng.normal(cst_cfg["mean"], cst_cfg["sd"]), 200, 700))

        patient = Patient(
            patient_id=pid,
            age_group=age_grp,
            gender=gender,
            ethnicity=ethnicity,
            eye_laterality=lateral,
            diagnosis_code=diagnosis,
            baseline_bcva=round(baseline_bcva, 1),
            baseline_cst=baseline_cst,
        )
        db.add(patient)

        # ── Visit trajectories ────────────────────────────────────────────────
        bcva_traj = simulate_bcva_trajectory(baseline_bcva, drug, n_visits)
        cst_traj  = simulate_cst_trajectory(baseline_cst, n_visits)

        # Enrolment date shifted per patient for privacy
        shift  = get_date_shift(pid)
        start  = date(2021, 1, 1) + timedelta(days=int(rng.integers(0, 730)))
        start  = start + timedelta(days=shift)

        for v in range(n_visits):
            sid = uuid.uuid4().hex[:14]
            visit_date = start + timedelta(weeks=v * 4)

            session = TreatmentSession(
                session_id=sid,
                patient_id=pid,
                visit_number=v + 1,
                visit_date=visit_date,
                treating_drug=drug,
                injection_count=1,
                treatment_regimen=regimen,
                iop_measured=round(float(rng.normal(14.5, 2.5)), 1),
            )
            db.add(session)

            irf, srf, irf_vol, srf_vol = fluid_status_from_cst(cst_traj[v])
            ped = bool(rng.random() < 0.20)
            bcva_letters = bcva_traj[v]

            outcome = EfficacyOutcome(
                outcome_id=uuid.uuid4().hex[:14],
                session_id=sid,
                patient_id=pid,
                bcva_letters=bcva_letters,
                bcva_logmar=etdrs_to_logmar(bcva_letters),
                irf_present=irf,
                srf_present=srf,
                irf_volume=irf_vol,
                srf_volume=srf_vol,
                cst_um=cst_traj[v],
                pigment_epi_detach=ped,
            )
            db.add(outcome)

        # ── Adverse Events (sparse — per drug AE rate) ────────────────────────
        ae_rate = DRUG_PROFILES[drug]["ae_rate"]
        n_ae = int(rng.poisson(ae_rate * n_visits))
        for _ in range(n_ae):
            ae_visit = int(rng.integers(1, n_visits))
            ae_date  = start + timedelta(weeks=ae_visit * 4)
            severity = rng.choice(AE_SEVERITIES, p=[0.50, 0.30, 0.15, 0.05])
            ae = AdverseEvent(
                ae_id=uuid.uuid4().hex[:14],
                patient_id=pid,
                ae_date=ae_date,
                ae_type=str(rng.choice(AE_TYPES)),
                ae_severity=severity,
                ae_outcome=str(rng.choice(AE_OUTCOMES)),
                ae_description="Observed during routine clinical visit.",
            )
            db.add(ae)

    db.commit()
    db.close()
    print(f"✅  Generated {n_patients} synthetic patients × {n_visits} visits")


if __name__ == "__main__":
    generate_synthetic_cohort(n_patients=500, n_visits=12)
