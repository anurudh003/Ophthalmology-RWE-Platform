"""
Centralised analytics query functions.

All functions accept optional filter kwargs and return flat DataFrames
ready for Plotly or export. Called by pages/03_Analytics.py and
pages/04_Data_Export.py so logic lives in exactly one place.

Filter kwargs accepted by most functions
----------------------------------------
conditions : list[str]  — condition_name values to include  (None = all)
drugs      : list[str]  — drug_name values to include       (None = all)
eyes       : list[str]  — eye codes to include              (None = all)
age_groups : list[str]  — age_group values to include       (None = all)
visit_range: tuple[int,int] — (min_visit_number, max_visit_number)
"""

from __future__ import annotations

import pandas as pd
import streamlit as st
from sqlalchemy import func
from sqlalchemy.orm import joinedload

from database.db import get_session
from database.models import (
    AdverseEvent,
    Diagnosis,
    Outcome,
    Patient,
    Treatment,
    Visit,
)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _base_visit_query(session, conditions, drugs, eyes, age_groups, visit_range):
    """Return a Query of Visit joined to Patient, Diagnosis, Outcome, Treatment."""
    q = (
        session.query(Visit)
        .join(Patient, Visit.patient_id == Patient.id)
        .join(Diagnosis, Diagnosis.patient_id == Patient.id)
        .join(Outcome, Outcome.visit_id == Visit.id)
        .outerjoin(Treatment, Treatment.visit_id == Visit.id)
        .options(
            joinedload(Visit.outcomes),
            joinedload(Visit.treatments),
            joinedload(Visit.patient).joinedload(Patient.diagnoses),
        )
    )
    if conditions:
        q = q.filter(Diagnosis.condition_name.in_(conditions))
    if eyes:
        q = q.filter(Visit.eye.in_(eyes))
    if age_groups:
        q = q.filter(Patient.age_group.in_(age_groups))
    if drugs:
        q = q.filter(Treatment.drug_name.in_(drugs))
    if visit_range:
        lo, hi = visit_range
        q = q.filter(Visit.visit_number >= lo, Visit.visit_number <= hi)
    return q


# ---------------------------------------------------------------------------
# 1. BCVA Trajectory
# ---------------------------------------------------------------------------

@st.cache_data(ttl=300, show_spinner=False)
def get_bcva_trajectory_df(
    conditions: tuple[str, ...] | None = None,
    drugs: tuple[str, ...] | None = None,
    eyes: tuple[str, ...] | None = None,
    age_groups: tuple[str, ...] | None = None,
    visit_range: tuple[int, int] | None = None,
) -> pd.DataFrame:
    """
    Returns one row per (condition, visit_number) with:
      mean_bcva   — mean ETDRS letters
      sd_bcva     — standard deviation
      n           — patient count at that visit number

    Suitable for a line chart with ±1 SD shaded band.
    """
    rows = []
    with get_session() as session:
        q = _base_visit_query(session, conditions, drugs, eyes, age_groups, visit_range)
        for visit in q.all():
            oc = visit.outcomes[0] if visit.outcomes else None
            dx = visit.patient.diagnoses[0] if visit.patient.diagnoses else None
            if oc and oc.bcva_etdrs_letters is not None and dx:
                rows.append({
                    "condition":    dx.condition_name,
                    "visit_number": visit.visit_number,
                    "bcva_letters": oc.bcva_etdrs_letters,
                })

    if not rows:
        return pd.DataFrame(columns=["condition", "visit_number", "mean_bcva", "sd_bcva", "n"])

    df = pd.DataFrame(rows)
    agg = (
        df.groupby(["condition", "visit_number"])["bcva_letters"]
        .agg(mean_bcva="mean", sd_bcva="std", n="count")
        .reset_index()
    )
    agg["mean_bcva"] = agg["mean_bcva"].round(1)
    agg["sd_bcva"]   = agg["sd_bcva"].round(1).fillna(0)
    agg = agg.sort_values(["condition", "visit_number"])
    return agg


# ---------------------------------------------------------------------------
# 1b. BCVA by cumulative injection number
# ---------------------------------------------------------------------------

@st.cache_data(ttl=300, show_spinner=False)
def get_bcva_by_injection_df(
    conditions: tuple[str, ...] | None = None,
    drugs: tuple[str, ...] | None = None,
    eyes: tuple[str, ...] | None = None,
    age_groups: tuple[str, ...] | None = None,
    injection_range: tuple[int, int] | None = None,
) -> pd.DataFrame:
    """
    Returns one row per (condition, injection_number) with:
      mean_bcva   — mean ETDRS letters at that cumulative injection count
      sd_bcva     — standard deviation
      n           — eye count contributing to that injection number

    Suitable for a line chart of visual response to treatment burden.
    Only visits where both injection_number and bcva_etdrs_letters are
    recorded are included.
    """
    rows = []
    with get_session() as session:
        q = (
            session.query(Visit)
            .join(Patient, Visit.patient_id == Patient.id)
            .join(Diagnosis, Diagnosis.patient_id == Patient.id)
            .join(Outcome, Outcome.visit_id == Visit.id)
            .join(Treatment, Treatment.visit_id == Visit.id)
            .options(
                joinedload(Visit.outcomes),
                joinedload(Visit.treatments),
                joinedload(Visit.patient).joinedload(Patient.diagnoses),
            )
        )
        if conditions:
            q = q.filter(Diagnosis.condition_name.in_(conditions))
        if eyes:
            q = q.filter(Visit.eye.in_(eyes))
        if age_groups:
            q = q.filter(Patient.age_group.in_(age_groups))
        if drugs:
            q = q.filter(Treatment.drug_name.in_(drugs))
        if injection_range:
            lo, hi = injection_range
            q = q.filter(Treatment.injection_number >= lo, Treatment.injection_number <= hi)

        for visit in q.all():
            oc = visit.outcomes[0] if visit.outcomes else None
            dx = visit.patient.diagnoses[0] if visit.patient.diagnoses else None
            tr = next(
                (t for t in visit.treatments if t.injection_number is not None), None
            )
            if oc and oc.bcva_etdrs_letters is not None and dx and tr:
                rows.append({
                    "condition":        dx.condition_name,
                    "injection_number": tr.injection_number,
                    "bcva_letters":     oc.bcva_etdrs_letters,
                })

    if not rows:
        return pd.DataFrame(
            columns=["condition", "injection_number", "mean_bcva", "sd_bcva", "n"]
        )

    df = pd.DataFrame(rows)
    agg = (
        df.groupby(["condition", "injection_number"])["bcva_letters"]
        .agg(mean_bcva="mean", sd_bcva="std", n="count")
        .reset_index()
    )
    agg["mean_bcva"] = agg["mean_bcva"].round(1)
    agg["sd_bcva"]   = agg["sd_bcva"].round(1).fillna(0)
    return agg.sort_values(["condition", "injection_number"])


# ---------------------------------------------------------------------------
# 2. Waterfall — BCVA change at last recorded visit
# ---------------------------------------------------------------------------

@st.cache_data(ttl=300, show_spinner=False)
def get_waterfall_df(
    conditions: tuple[str, ...] | None = None,
    drugs: tuple[str, ...] | None = None,
    eyes: tuple[str, ...] | None = None,
    age_groups: tuple[str, ...] | None = None,
) -> pd.DataFrame:
    """
    Returns one row per patient with:
      patient_token — truncated hash
      condition     — primary diagnosis
      bcva_change   — letters gained at last recorded visit (positive = better)
      gainer        — bool (change >= 0)
    Sorted ascending by bcva_change.
    """
    rows = []
    with get_session() as session:
        patients = (
            session.query(Patient)
            .options(
                joinedload(Patient.diagnoses),
                joinedload(Patient.visits).joinedload(Visit.outcomes),
                joinedload(Patient.visits).joinedload(Visit.treatments),
            )
            .all()
        )
        for p in patients:
            dx = p.diagnoses[0] if p.diagnoses else None
            if conditions and (not dx or dx.condition_name not in conditions):
                continue
            if age_groups and p.age_group not in age_groups:
                continue

            visits_sorted = sorted(p.visits, key=lambda v: (v.visit_date, v.visit_number))
            if not visits_sorted:
                continue

            # Filter by eye
            if eyes:
                visits_sorted = [v for v in visits_sorted if v.eye in eyes]
            # Filter by drug (any visit with that drug)
            if drugs:
                patient_drugs = {
                    t.drug_name
                    for v in visits_sorted
                    for t in v.treatments
                }
                if not patient_drugs.intersection(set(drugs)):
                    continue

            last_visit = visits_sorted[-1]
            oc = last_visit.outcomes[0] if last_visit.outcomes else None
            if oc and oc.bcva_change_from_baseline is not None:
                rows.append({
                    "patient_token": p.patient_hash[:12] + "…",
                    "condition":     dx.condition_name if dx else "Unknown",
                    "age_group":     p.age_group,
                    "bcva_change":   oc.bcva_change_from_baseline,
                    "gainer":        oc.bcva_change_from_baseline >= 0,
                })

    if not rows:
        return pd.DataFrame(columns=["patient_token", "condition", "age_group", "bcva_change", "gainer"])

    df = pd.DataFrame(rows).sort_values("bcva_change").reset_index(drop=True)
    return df


# ---------------------------------------------------------------------------
# 3. IRF / SRF fluid prevalence by visit number
# ---------------------------------------------------------------------------

@st.cache_data(ttl=300, show_spinner=False)
def get_fluid_prevalence_df(
    conditions: tuple[str, ...] | None = None,
    drugs: tuple[str, ...] | None = None,
    eyes: tuple[str, ...] | None = None,
    age_groups: tuple[str, ...] | None = None,
    visit_range: tuple[int, int] | None = None,
) -> pd.DataFrame:
    """
    Returns one row per visit_number with:
      visit_number  — 1-based visit index
      pct_irf       — % of patients with IRF present
      pct_srf       — % of patients with SRF present
      n             — denominator
    """
    rows = []
    with get_session() as session:
        q = _base_visit_query(session, conditions, drugs, eyes, age_groups, visit_range)
        for visit in q.all():
            oc = visit.outcomes[0] if visit.outcomes else None
            if oc and oc.irf_present is not None:
                rows.append({
                    "visit_number": visit.visit_number,
                    "irf":          int(bool(oc.irf_present)),
                    "srf":          int(bool(oc.srf_present)),
                })

    if not rows:
        return pd.DataFrame(columns=["visit_number", "pct_irf", "pct_srf", "n"])

    df = pd.DataFrame(rows)
    agg = (
        df.groupby("visit_number")
        .agg(pct_irf=("irf", "mean"), pct_srf=("srf", "mean"), n=("irf", "count"))
        .reset_index()
    )
    agg["pct_irf"] = (agg["pct_irf"] * 100).round(1)
    agg["pct_srf"] = (agg["pct_srf"] * 100).round(1)
    return agg.sort_values("visit_number")


# ---------------------------------------------------------------------------
# 4. Injection interval distribution (maintenance visits)
# ---------------------------------------------------------------------------

@st.cache_data(ttl=300, show_spinner=False)
def get_injection_interval_df(
    conditions: tuple[str, ...] | None = None,
    drugs: tuple[str, ...] | None = None,
    eyes: tuple[str, ...] | None = None,
    age_groups: tuple[str, ...] | None = None,
) -> pd.DataFrame:
    """
    Returns one row per consecutive visit pair where the later visit is
    a maintenance-phase visit (visit_number >= 4) and an injection was given.
    Columns:
      patient_token — truncated hash
      condition     — primary diagnosis
      drug          — drug name at the later visit
      visit_type    — Maintenance / PRN / T&E
      interval_days — days between consecutive visits
    """
    rows = []
    with get_session() as session:
        patients = (
            session.query(Patient)
            .options(
                joinedload(Patient.diagnoses),
                joinedload(Patient.visits).joinedload(Visit.treatments),
            )
            .all()
        )
        for p in patients:
            dx = p.diagnoses[0] if p.diagnoses else None
            if conditions and (not dx or dx.condition_name not in conditions):
                continue
            if age_groups and p.age_group not in age_groups:
                continue

            visits_sorted = sorted(p.visits, key=lambda v: (v.visit_date, v.visit_number))
            if eyes:
                visits_sorted = [v for v in visits_sorted if v.eye in eyes]

            for i in range(1, len(visits_sorted)):
                curr = visits_sorted[i]
                prev = visits_sorted[i - 1]

                # Only maintenance-phase visits with an injection
                if curr.visit_number < 4:
                    continue
                drug_names = [t.drug_name for t in curr.treatments]
                if not drug_names:
                    continue
                drug = drug_names[0]
                if drugs and drug not in drugs:
                    continue

                delta = (curr.visit_date - prev.visit_date).days
                if delta <= 0 or delta > 365:
                    continue

                rows.append({
                    "patient_token": p.patient_hash[:12] + "…",
                    "condition":     dx.condition_name if dx else "Unknown",
                    "drug":          drug,
                    "visit_type":    curr.visit_type,
                    "interval_days": delta,
                })

    if not rows:
        return pd.DataFrame(columns=["patient_token", "condition", "drug", "visit_type", "interval_days"])

    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# 5. Adverse event summary
# ---------------------------------------------------------------------------

@st.cache_data(ttl=300, show_spinner=False)
def get_ae_summary_df(
    conditions: tuple[str, ...] | None = None,
    drugs: tuple[str, ...] | None = None,
    eyes: tuple[str, ...] | None = None,
    age_groups: tuple[str, ...] | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """
    Returns a tuple of two DataFrames:

    ae_counts  — one row per (ae_type, ae_category) with:
        count, incidence_per_1000  (injections denominator)

    sae_detail — one row per SAE with:
        ae_type, ae_category, severity_grade, count
    """
    with get_session() as session:
        # Denominator: total injections matching filters
        inj_q = (
            session.query(func.count(Treatment.id))
            .join(Visit, Treatment.visit_id == Visit.id)
            .join(Patient, Visit.patient_id == Patient.id)
            .join(Diagnosis, Diagnosis.patient_id == Patient.id)
        )
        if conditions:
            inj_q = inj_q.filter(Diagnosis.condition_name.in_(conditions))
        if drugs:
            inj_q = inj_q.filter(Treatment.drug_name.in_(drugs))
        if eyes:
            inj_q = inj_q.filter(Visit.eye.in_(eyes))
        if age_groups:
            inj_q = inj_q.filter(Patient.age_group.in_(age_groups))
        total_injections = inj_q.scalar() or 1

        # AE records
        ae_q = (
            session.query(AdverseEvent)
            .join(Visit, AdverseEvent.visit_id == Visit.id)
            .join(Patient, Visit.patient_id == Patient.id)
            .join(Diagnosis, Diagnosis.patient_id == Patient.id)
        )
        if conditions:
            ae_q = ae_q.filter(Diagnosis.condition_name.in_(conditions))
        if eyes:
            ae_q = ae_q.filter(Visit.eye.in_(eyes))
        if age_groups:
            ae_q = ae_q.filter(Patient.age_group.in_(age_groups))

        ae_rows = []
        for ae in ae_q.all():
            ae_rows.append({
                "ae_classification": ae.ae_classification or "Other",
                "ae_type":           ae.ae_type,
                "ae_category":       ae.ae_category or "Unknown",
                "severity_grade":    ae.severity_grade,
                "serious":           ae.serious,
            })

    if not ae_rows:
        empty_counts = pd.DataFrame(
            columns=["ae_classification", "ae_type", "ae_category", "count", "incidence_per_1000"]
        )
        empty_sae = pd.DataFrame(
            columns=["ae_classification", "ae_type", "ae_category", "severity_grade", "count"]
        )
        return empty_counts, empty_sae

    df = pd.DataFrame(ae_rows)

    # AE counts + incidence — grouped by classification for clean deduplication,
    # keeping ae_type as the human-readable display label (first seen per classification)
    ae_counts = (
        df.groupby(["ae_classification", "ae_category"])
        .agg(count=("ae_type", "size"), ae_type=("ae_type", "first"))
        .reset_index()
    )
    ae_counts["incidence_per_1000"] = (
        (ae_counts["count"] / total_injections * 1000).round(2)
    )
    ae_counts = ae_counts[
        ["ae_classification", "ae_type", "ae_category", "count", "incidence_per_1000"]
    ].sort_values("count", ascending=False)

    # SAE breakdown by classification + grade
    sae_df = df[df["serious"]].copy()
    if not sae_df.empty:
        sae_detail = (
            sae_df.groupby(["ae_classification", "ae_type", "ae_category", "severity_grade"])
            .size()
            .reset_index(name="count")
            .sort_values(["ae_category", "count"], ascending=[True, False])
        )
    else:
        sae_detail = pd.DataFrame(
            columns=["ae_classification", "ae_type", "ae_category", "severity_grade", "count"]
        )

    return ae_counts, sae_detail


# ---------------------------------------------------------------------------
# 5b. CTCAE grade distribution across all AEs
# ---------------------------------------------------------------------------

@st.cache_data(ttl=300, show_spinner=False)
def get_ae_grade_distribution_df(
    conditions: tuple[str, ...] | None = None,
    drugs: tuple[str, ...] | None = None,
    eyes: tuple[str, ...] | None = None,
    age_groups: tuple[str, ...] | None = None,
) -> pd.DataFrame:
    """
    Returns one row per (ae_classification, severity_grade) with count.
    Suitable for a stacked bar showing grade profile per AE type.
    Only rows where severity_grade is recorded (non-null) are included.
    """
    rows = []
    with get_session() as session:
        ae_q = (
            session.query(AdverseEvent)
            .join(Visit, AdverseEvent.visit_id == Visit.id)
            .join(Patient, Visit.patient_id == Patient.id)
            .join(Diagnosis, Diagnosis.patient_id == Patient.id)
        )
        if conditions:
            ae_q = ae_q.filter(Diagnosis.condition_name.in_(conditions))
        if eyes:
            ae_q = ae_q.filter(Visit.eye.in_(eyes))
        if age_groups:
            ae_q = ae_q.filter(Patient.age_group.in_(age_groups))

        for ae in ae_q.all():
            if ae.severity_grade is None:
                continue
            rows.append({
                "ae_classification": ae.ae_classification or "Other",
                "ae_category":       ae.ae_category or "Unknown",
                "severity_grade":    ae.severity_grade,
            })

    if not rows:
        return pd.DataFrame(
            columns=["ae_classification", "ae_category", "severity_grade", "count"]
        )

    df = pd.DataFrame(rows)
    agg = (
        df.groupby(["ae_classification", "ae_category", "severity_grade"])
        .size()
        .reset_index(name="count")
        .sort_values(["ae_classification", "severity_grade"])
    )
    return agg


# ---------------------------------------------------------------------------
# 6b. Drug cohort BCVA comparison
# ---------------------------------------------------------------------------

@st.cache_data(ttl=300, show_spinner=False)
def get_bcva_cohort_comparison_df(
    drug_a: str,
    drug_b: str,
    conditions: tuple[str, ...] | None = None,
    eyes: tuple[str, ...] | None = None,
    age_groups: tuple[str, ...] | None = None,
    visit_range: tuple[int, int] | None = None,
) -> pd.DataFrame:
    """
    Returns one row per (drug, visit_number) with mean_bcva, sd_bcva, n.
    Only visits where the patient received drug_a or drug_b are included;
    a patient is assigned to a drug cohort if any treatment at that visit
    matches.  Used for the head-to-head mean BCVA trajectory chart.

    Returns an empty DataFrame if neither drug has data.
    """
    rows = []
    target_drugs = {drug_a, drug_b}

    with get_session() as session:
        q = (
            session.query(Visit)
            .join(Patient, Visit.patient_id == Patient.id)
            .join(Outcome, Outcome.visit_id == Visit.id)
            .join(Treatment, Treatment.visit_id == Visit.id)
            .join(Diagnosis, Diagnosis.patient_id == Patient.id)
            .options(
                joinedload(Visit.outcomes),
                joinedload(Visit.treatments),
                joinedload(Visit.patient).joinedload(Patient.diagnoses),
            )
        )
        if conditions:
            q = q.filter(Diagnosis.condition_name.in_(conditions))
        if eyes:
            q = q.filter(Visit.eye.in_(eyes))
        if age_groups:
            q = q.filter(Patient.age_group.in_(age_groups))
        if visit_range:
            lo, hi = visit_range
            q = q.filter(Visit.visit_number >= lo, Visit.visit_number <= hi)
        q = q.filter(Treatment.drug_name.in_(target_drugs))

        for visit in q.all():
            oc = visit.outcomes[0] if visit.outcomes else None
            if oc is None or oc.bcva_etdrs_letters is None:
                continue
            # Assign to the cohort of the first matching drug at this visit
            drug = next(
                (t.drug_name for t in visit.treatments if t.drug_name in target_drugs),
                None,
            )
            if drug is None:
                continue
            rows.append({
                "drug":         drug,
                "visit_number": visit.visit_number,
                "bcva_letters": oc.bcva_etdrs_letters,
            })

    if not rows:
        return pd.DataFrame(
            columns=["drug", "visit_number", "mean_bcva", "sd_bcva", "n"]
        )

    df = pd.DataFrame(rows)
    agg = (
        df.groupby(["drug", "visit_number"])["bcva_letters"]
        .agg(mean_bcva="mean", sd_bcva="std", n="count")
        .reset_index()
    )
    agg["mean_bcva"] = agg["mean_bcva"].round(1)
    agg["sd_bcva"]   = agg["sd_bcva"].round(1).fillna(0)
    return agg.sort_values(["drug", "visit_number"])


# ---------------------------------------------------------------------------
# 6. Full flat visit export
# ---------------------------------------------------------------------------

@st.cache_data(ttl=300, show_spinner=False)
def get_full_visit_df(
    conditions: tuple[str, ...] | None = None,
    drugs: tuple[str, ...] | None = None,
    eyes: tuple[str, ...] | None = None,
    age_groups: tuple[str, ...] | None = None,
    visit_range: tuple[int, int] | None = None,
) -> pd.DataFrame:
    """
    One row per visit, all outcomes, suitable for patient-level or
    visit-level export. No direct PII — patient_hash deliberately omitted;
    call sanitise_export_df() before writing to file for belt-and-braces.
    """
    rows = []
    with get_session() as session:
        q = _base_visit_query(session, conditions, drugs, eyes, age_groups, visit_range)
        for visit in q.distinct().all():
            p  = visit.patient
            dx = p.diagnoses[0] if p.diagnoses else None
            oc = visit.outcomes[0] if visit.outcomes else None
            tr = visit.treatments[0] if visit.treatments else None

            rows.append({
                "age_group":               p.age_group,
                "sex":                     p.sex,
                "ethnicity":               p.ethnicity,
                "smoking_status":          p.smoking_status,
                "diabetes":                p.diabetes,
                "hypertension":            p.hypertension,
                "condition":               dx.condition_name if dx else None,
                "condition_code":          dx.condition_code if dx else None,
                "icd10_code":              dx.icd10_code if dx else None,
                "eye":                     visit.eye,
                "visit_number":            visit.visit_number,
                "visit_date":              visit.visit_date.strftime("%Y-%m-%d"),
                "visit_type":              visit.visit_type,
                "site_code":               visit.site_code,
                "clinician_code":          visit.clinician_code,
                "drug":                    tr.drug_name if tr else None,
                "dose_mg":                 tr.drug_dose_mg if tr else None,
                "injection_number":        tr.injection_number if tr else None,
                "injection_site":          tr.injection_site if tr else None,
                "bcva_logmar":             oc.bcva_logmar if oc else None,
                "bcva_etdrs_letters":      oc.bcva_etdrs_letters if oc else None,
                "bcva_snellen":            oc.bcva_snellen if oc else None,
                "bcva_change_from_baseline": oc.bcva_change_from_baseline if oc else None,
                "crt_um":                  oc.crt_um if oc else None,
                "crt_change_from_baseline": oc.crt_change_from_baseline if oc else None,
                "irf_present":             oc.irf_present if oc else None,
                "srf_present":             oc.srf_present if oc else None,
                "ped_height_um":           oc.ped_height_um if oc else None,
                "iop_mmhg":               oc.iop_mmhg if oc else None,
                "n_aes":                   len(visit.adverse_events),
                "n_saes":                  sum(1 for ae in visit.adverse_events if ae.serious),
            })

    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# 7. Patient-level summary (one row per patient, last-visit outcomes)
# ---------------------------------------------------------------------------

@st.cache_data(ttl=300, show_spinner=False)
def get_patient_summary_df(
    conditions: tuple[str, ...] | None = None,
    drugs: tuple[str, ...] | None = None,
    eyes: tuple[str, ...] | None = None,
    age_groups: tuple[str, ...] | None = None,
) -> pd.DataFrame:
    """
    One row per patient: baseline + last-visit outcomes only.
    Used for the patient-level CSV export.
    """
    rows = []
    with get_session() as session:
        patients = (
            session.query(Patient)
            .options(
                joinedload(Patient.diagnoses),
                joinedload(Patient.visits).joinedload(Visit.outcomes),
                joinedload(Patient.visits).joinedload(Visit.treatments),
                joinedload(Patient.visits).joinedload(Visit.adverse_events),
            )
            .all()
        )
        for p in patients:
            dx = p.diagnoses[0] if p.diagnoses else None
            if conditions and (not dx or dx.condition_name not in conditions):
                continue
            if age_groups and p.age_group not in age_groups:
                continue

            visits_sorted = sorted(p.visits, key=lambda v: (v.visit_date, v.visit_number))
            if eyes:
                visits_sorted = [v for v in visits_sorted if v.eye in eyes]
            if not visits_sorted:
                continue

            # Drug filter: patient must have had at least one injection of the drug
            if drugs:
                patient_drugs = {t.drug_name for v in visits_sorted for t in v.treatments}
                if not patient_drugs.intersection(set(drugs)):
                    continue

            first_v = visits_sorted[0]
            last_v  = visits_sorted[-1]
            oc_bl   = first_v.outcomes[0] if first_v.outcomes else None
            oc_last = last_v.outcomes[0]  if last_v.outcomes  else None
            drugs_used = list({t.drug_name for v in visits_sorted for t in v.treatments})

            rows.append({
                "age_group":                   p.age_group,
                "sex":                         p.sex,
                "ethnicity":                   p.ethnicity,
                "smoking_status":              p.smoking_status,
                "diabetes":                    p.diabetes,
                "hypertension":                p.hypertension,
                "condition":                   dx.condition_name if dx else None,
                "eye":                         first_v.eye,
                "n_visits":                    len(visits_sorted),
                "n_injections":                sum(len(v.treatments) for v in visits_sorted),
                "drugs_used":                  "; ".join(sorted(drugs_used)),
                "baseline_bcva_letters":       oc_bl.bcva_etdrs_letters if oc_bl else None,
                "baseline_bcva_logmar":        oc_bl.bcva_logmar if oc_bl else None,
                "baseline_crt_um":             oc_bl.crt_um if oc_bl else None,
                "last_bcva_letters":           oc_last.bcva_etdrs_letters if oc_last else None,
                "last_bcva_logmar":            oc_last.bcva_logmar if oc_last else None,
                "last_bcva_change_from_baseline": oc_last.bcva_change_from_baseline if oc_last else None,
                "last_crt_um":                 oc_last.crt_um if oc_last else None,
                "last_irf_present":            oc_last.irf_present if oc_last else None,
                "last_srf_present":            oc_last.srf_present if oc_last else None,
                "total_aes":                   sum(len(v.adverse_events) for v in visits_sorted),
                "total_saes":                  sum(
                    sum(1 for ae in v.adverse_events if ae.serious)
                    for v in visits_sorted
                ),
            })

    return pd.DataFrame(rows)
