"""
Privacy / anonymisation utilities.

Approach:
  - Direct identifiers (name, DOB, NHS/MRN) are NEVER stored in the database.
  - A one-way SHA-256 hash links records across tables without exposing PII.
  - Quasi-identifiers (age, postcode) are generalised to coarser bins.
  - Export functions strip any remaining sensitive fields before writing to file.
"""

import hashlib
import hmac
import os
import struct
from datetime import date, timedelta
from typing import Optional

import pandas as pd


# ---------------------------------------------------------------------------
# Patient hash
# ---------------------------------------------------------------------------

def generate_patient_hash(given_name: str, family_name: str, dob: date) -> str:
    """
    Produce a deterministic, irreversible 64-char hex token from PII.
    The same inputs always yield the same hash, allowing record linkage
    without storing any identifiable data.

    In production add a per-deployment secret salt stored outside the DB.
    """
    raw = f"{given_name.strip().lower()}|{family_name.strip().lower()}|{dob.isoformat()}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Age generalisation
# ---------------------------------------------------------------------------

_AGE_BINS = [
    (0,  39,  "<40"),
    (40, 49,  "40-49"),
    (50, 59,  "50-59"),
    (60, 69,  "60-69"),
    (70, 79,  "70-79"),
    (80, 89,  "80-89"),
    (90, 99, "90+"),
]


def age_to_group(age: int) -> str:
    """Map a numeric age to a generalised 10-year bin string."""
    for lo, hi, label in _AGE_BINS:
        if lo <= age <= hi:
            return label
    return "Unknown"


def dob_to_age_group(dob: date, reference_date: date | None = None) -> str:
    """Derive age group from date-of-birth (no DOB stored after this call)."""
    ref = reference_date or date.today()
    age = ref.year - dob.year - ((ref.month, ref.day) < (dob.month, dob.day))
    return age_to_group(age)


# ---------------------------------------------------------------------------
# Snellen <-> logMAR conversion helpers
# ---------------------------------------------------------------------------

def logmar_to_snellen(logmar: float) -> str:
    """Return nearest standard Snellen string for a logMAR value."""
    mapping = [
        (-0.30, "6/3"),
        (-0.18, "6/4"),
        (-0.10, "6/5"),
        (0.00,  "6/6"),
        (0.10,  "6/7.5"),
        (0.20,  "6/9"),
        (0.30,  "6/12"),
        (0.40,  "6/15"),
        (0.50,  "6/18"),
        (0.60,  "6/24"),
        (0.70,  "6/30"),
        (0.80,  "6/36"),
        (1.00,  "6/60"),
        (1.30,  "3/60"),
        (1.60,  "CF"),
        (2.00,  "HM"),
        (2.30,  "PL"),
        (3.00,  "NPL"),
    ]
    closest = min(mapping, key=lambda x: abs(x[0] - logmar))
    return closest[1]


def snellen_to_logmar(snellen: str) -> float | None:
    """Return logMAR for common Snellen strings."""
    table = {
        "6/3": -0.30, "6/4": -0.18, "6/5": -0.10,
        "6/6": 0.00,  "6/7.5": 0.10, "6/9": 0.20,
        "6/12": 0.30, "6/15": 0.40, "6/18": 0.50,
        "6/24": 0.60, "6/30": 0.70, "6/36": 0.80,
        "6/60": 1.00, "3/60": 1.30, "CF": 1.60,
        "HM": 2.00,   "PL": 2.30,   "NPL": 3.00,
    }
    return table.get(snellen)


def logmar_to_etdrs(logmar: float) -> int:
    """Approximate ETDRS letter score from logMAR (85 − 50×logMAR)."""
    return max(0, min(100, round(85 - 50 * logmar)))


# ---------------------------------------------------------------------------
# DataFrame export sanitiser
# ---------------------------------------------------------------------------

def sanitise_export_df(df: "pd.DataFrame") -> "pd.DataFrame":
    """
    Remove any columns that might contain PII before export.
    Returns a cleaned copy — does not mutate the original.
    """
    pii_columns = {
        "patient_hash", "given_name", "family_name", "dob",
        "date_of_birth", "nhs_number", "mrn", "email",
        "phone", "address", "postcode",
    }
    cols_to_drop = [c for c in df.columns if c.lower() in pii_columns]
    cleaned = df.drop(columns=cols_to_drop, errors="ignore").copy()
    return cleaned


# ---------------------------------------------------------------------------
# Date shifting — per-patient, deterministic, ±180 days
# ---------------------------------------------------------------------------

# A fixed HMAC secret derived from an env var (or a fallback for dev).
# In production, set IRIS_DATE_SHIFT_SECRET to a random 32-byte hex string.
_DATE_SHIFT_SECRET: bytes = os.getenv(
    "IRIS_DATE_SHIFT_SECRET", "iris_rwe_dev_shift_secret_do_not_use_in_prod"
).encode("utf-8")

_DATE_SHIFT_MAX_DAYS = 180


def _patient_shift_days(patient_token: str) -> int:
    """
    Derive a deterministic per-patient date-shift offset in [−180, +180] days.
    Uses HMAC-SHA256 so the offset is unpredictable without the secret.
    The same patient always gets the same shift within a deployment.
    """
    digest = hmac.new(_DATE_SHIFT_SECRET, patient_token.encode("utf-8"), "sha256").digest()
    # Interpret first 4 bytes as unsigned int, map to [0, 360], then offset to [−180, +180]
    raw = struct.unpack(">I", digest[:4])[0]
    shift = (raw % (2 * _DATE_SHIFT_MAX_DAYS + 1)) - _DATE_SHIFT_MAX_DAYS
    return shift


def date_shift(
    df: "pd.DataFrame",
    date_columns: list[str],
    patient_token_column: str = "patient_token",
) -> "pd.DataFrame":
    """
    Shift all date columns in df by a per-patient offset derived from
    patient_token_column.  Returns a new DataFrame; does not mutate the original.

    - If patient_token_column is absent, every row gets a random-ish shift
      based on its index (weaker but still applies noise).
    - Date columns that contain strings in "YYYY-MM-DD" format are shifted
      and returned as strings in the same format.
    - NaN / None values are left untouched.
    """
    out = df.copy()
    has_token = patient_token_column in out.columns

    for col in date_columns:
        if col not in out.columns:
            continue

        def _shift_cell(row):
            val = row[col]
            if pd.isna(val) or val is None or val == "":
                return val
            token = row[patient_token_column] if has_token else str(row.name)
            offset = _patient_shift_days(str(token))
            try:
                dt = pd.to_datetime(val)
                shifted = dt + timedelta(days=offset)
                return shifted.strftime("%Y-%m-%d")
            except Exception:
                return val  # leave unparseable values untouched

        out[col] = out.apply(_shift_cell, axis=1)

    return out


# ---------------------------------------------------------------------------
# Small-cell suppression — replace counts < k with "<k"
# ---------------------------------------------------------------------------

_SMALL_CELL_THRESHOLD = 5


def small_cell_suppress(
    df: "pd.DataFrame",
    count_columns: list[str],
    threshold: int = _SMALL_CELL_THRESHOLD,
) -> "pd.DataFrame":
    """
    For aggregate tables: replace any numeric cell whose value is 1 ≤ n < threshold
    with the string "<{threshold}>" (e.g. "<5>").
    Zero counts are left as 0 (absence is not re-identifying).
    Returns a copy; does not mutate the original.
    """
    out = df.copy()
    for col in count_columns:
        if col not in out.columns:
            continue
        mask = (out[col] > 0) & (out[col] < threshold)
        out[col] = out[col].astype(object)
        out.loc[mask, col] = f"<{threshold}"
    return out


# ---------------------------------------------------------------------------
# k-anonymity check
# ---------------------------------------------------------------------------

_K_ANON_DEFAULT = 5
_K_ANON_QUASI_IDS = ["age_group", "sex", "ethnicity", "condition"]


def k_anonymity_check(
    df: "pd.DataFrame",
    quasi_identifiers: Optional[list[str]] = None,
    k: int = _K_ANON_DEFAULT,
) -> tuple[bool, "pd.DataFrame"]:
    """
    Verify that every combination of quasi-identifiers appears at least k times.

    Parameters
    ----------
    df               : DataFrame to check (patient- or visit-level export)
    quasi_identifiers: columns to treat as quasi-IDs (defaults to age_group,
                       sex, ethnicity, condition)
    k                : minimum group size required (default 5)

    Returns
    -------
    (passes, violations_df)
      passes        — True if all groups have ≥ k rows
      violations_df — subset of df rows that are in groups smaller than k
                      (empty DataFrame if passes=True)
    """
    if quasi_identifiers is None:
        quasi_identifiers = _K_ANON_QUASI_IDS

    # Only use QIs that actually exist in the DataFrame
    present_qis = [c for c in quasi_identifiers if c in df.columns]
    if not present_qis:
        return True, df.iloc[0:0]  # no QIs to check → trivially passes

    # Count group size for each row using a merge (robust when all columns are QIs)
    group_counts = (
        df.groupby(present_qis, dropna=False)
        .size()
        .reset_index(name="_group_size")
    )
    merged = df.merge(group_counts, on=present_qis, how="left")
    violations = df[merged["_group_size"] < k]
    return violations.empty, violations
