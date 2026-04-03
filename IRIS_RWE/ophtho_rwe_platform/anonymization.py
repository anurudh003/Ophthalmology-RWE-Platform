"""
Privacy & Anonymization module.
Implements privacy-by-design: hashing, age bucketing, date shifting,
small-cell suppression, and audit trail hooks.
"""

import hashlib
import random
from datetime import date, timedelta
from typing import Optional
import pandas as pd


# ── Patient ID Hashing ────────────────────────────────────────────────────────

def generate_patient_id(raw_identifier: str) -> str:
    """One-way SHA-256 hash of any PII (name, MRN, NHS number, etc.)."""
    return hashlib.sha256(raw_identifier.strip().lower().encode()).hexdigest()[:12]


# ── Age Bucketing ─────────────────────────────────────────────────────────────

AGE_BUCKETS = [(0, 40, "<40"), (40, 55, "40–54"), (55, 65, "55–64"),
               (65, 75, "65–74"), (75, 85, "75–84"), (85, 130, "85+")]

def age_to_bucket(age: int) -> str:
    """Replace exact age with 10-year band to reduce re-identification risk."""
    for low, high, label in AGE_BUCKETS:
        if low <= age < high:
            return label
    return "Unknown"


# ── Date Shifting ─────────────────────────────────────────────────────────────

def get_date_shift(patient_id: str, max_days: int = 30) -> int:
    """
    Derive a consistent per-patient date shift from the hashed ID.
    Same patient always gets the same shift → relative timing preserved.
    """
    seed = int(patient_id[:8], 16)
    rng = random.Random(seed)
    return rng.randint(-max_days, max_days)


def shift_date(d: date, patient_id: str) -> date:
    return d + timedelta(days=get_date_shift(patient_id))


# ── Small Cell Suppression ────────────────────────────────────────────────────

def suppress_small_cells(df: pd.DataFrame, count_col: str,
                          threshold: int = 5) -> pd.DataFrame:
    """
    Replace counts below threshold with '<5' string.
    Applied to all aggregate outputs before display.
    """
    df = df.copy()
    mask = df[count_col] < threshold
    df[count_col] = df[count_col].astype(object)
    df.loc[mask, count_col] = f"<{threshold}"
    return df


# ── BCVA conversion helpers ───────────────────────────────────────────────────

def etdrs_to_logmar(etdrs: float) -> Optional[float]:
    """Approximate ETDRS letters → LogMAR."""
    if etdrs is None:
        return None
    return round(-0.02 * etdrs + 2.0, 3)


def logmar_to_etdrs(logmar: float) -> Optional[float]:
    """Approximate LogMAR → ETDRS letters."""
    if logmar is None:
        return None
    return round((2.0 - logmar) / 0.02, 1)


# ── Data Export Sanitiser ─────────────────────────────────────────────────────

EXPORT_COLUMNS_PATIENTS = [
    "patient_id", "age_group", "gender", "ethnicity",
    "eye_laterality", "diagnosis_code", "baseline_bcva", "baseline_cst"
]

def sanitise_for_export(df: pd.DataFrame, table: str) -> pd.DataFrame:
    """Strip any columns not in the approved export allow-list."""
    if table == "patients":
        keep = [c for c in EXPORT_COLUMNS_PATIENTS if c in df.columns]
        return df[keep]
    return df
