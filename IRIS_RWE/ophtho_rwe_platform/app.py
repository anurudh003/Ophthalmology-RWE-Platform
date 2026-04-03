"""
Ophthalmology RWE Platform — Main Entry Point / Home Dashboard

KPI summary cards, diagnosis distribution, drug usage, recent visits.
Auto-seeds synthetic data on first run.
"""

import streamlit as st
import pandas as pd
from sqlalchemy import func, case

from database.db import get_session, init_db
from database.models import AdverseEvent, Diagnosis, Outcome, Patient, Treatment, Visit
from utils.seed_data import seed_database
from auth.auth import require_auth, render_sidebar_user_info, render_sidebar_logout, log_audit_event, get_role, has_page_access
from components.styles import inject_styles

# ---------------------------------------------------------------------------
# Page config
# ---------------------------------------------------------------------------
st.set_page_config(
    page_title="Ophthalmology RWE Platform",
    layout="wide",
    initial_sidebar_state="expanded",
)

inject_styles()

# ---------------------------------------------------------------------------
# Auth guard
# ---------------------------------------------------------------------------
require_auth(page_id="home")
log_audit_event("PAGE_ACCESS", detail="home/dashboard")

# ---------------------------------------------------------------------------
# Init DB and seed on first run
# ---------------------------------------------------------------------------
init_db()

if "seeded" not in st.session_state:
    n = seed_database(n_patients=500, force=False)
    st.session_state["seeded"] = True
    if n > 0:
        st.toast(f"Database initialised with {n} synthetic patients.")

# ---------------------------------------------------------------------------
# Sidebar — navigation + user info; logout pinned to bottom
# ---------------------------------------------------------------------------
with st.sidebar:
    st.page_link("app.py", label="Home / Dashboard")
    if has_page_access("patient_entry"):
        st.page_link("pages/01_Patient_Entry.py", label="Patient Registration")
    if has_page_access("visit_entry"):
        st.page_link("pages/02_Visit_Entry.py",  label="Visit & Treatment Entry")
    if has_page_access("analytics"):
        st.page_link("pages/03_Analytics.py",    label="Analytics & Outcomes")
    if has_page_access("data_export"):
        st.page_link("pages/04_Data_Export.py",  label="Data Export")
    # Spacer before footer
    st.markdown("<div style='flex:1'></div>", unsafe_allow_html=True)
    render_sidebar_user_info()
    render_sidebar_logout()
    st.caption("All patient data is anonymised at point of entry.")

# ---------------------------------------------------------------------------
# Page header
# ---------------------------------------------------------------------------
st.title("Home Dashboard")
st.markdown(
    "Integrated Real-World Evidence Capture & Analytics — "
    "recording treatment outcomes, visual acuity trajectories, "
    "and safety data for retinal disease patients."
)
st.markdown("---")

# ---------------------------------------------------------------------------
# KPI Cards
# ---------------------------------------------------------------------------
with get_session() as session:
    n_patients   = session.query(Patient).count()
    n_visits     = session.query(Visit).count()
    n_injections = session.query(Treatment).count()
    n_aes        = session.query(AdverseEvent).count()
    n_sae        = session.query(AdverseEvent).filter_by(serious=True).count()

    outcomes = session.query(Outcome).filter(
        Outcome.bcva_change_from_baseline.isnot(None)
    ).all()
    mean_bcva_change = (
        round(sum(o.bcva_change_from_baseline for o in outcomes) / len(outcomes), 1)
        if outcomes else 0.0
    )

    # Subquery: latest visit_id per patient (patients with ≥2 visits only)
    latest_visit_sq = (
        session.query(
            Visit.patient_id,
            func.max(Visit.visit_date).label("max_date"),
        )
        .group_by(Visit.patient_id)
        .having(func.count(Visit.id) >= 2)
        .subquery()
    )
    irf_counts = (
        session.query(
            func.count(Outcome.id).label("total"),
            func.sum(case((Outcome.irf_present == False, 1), else_=0)).label("irf_free"),
        )
        .join(Visit, Outcome.visit_id == Visit.id)
        .join(
            latest_visit_sq,
            (Visit.patient_id == latest_visit_sq.c.patient_id)
            & (Visit.visit_date == latest_visit_sq.c.max_date),
        )
        .one()
    )
    irf_free_pct = (
        round(100 * (irf_counts.irf_free or 0) / irf_counts.total, 1)
        if irf_counts.total else 0.0
    )

col1, col2, col3, col4, col5, col6 = st.columns(6)

def kpi_card(col, label, value, delta=None, delta_color="normal"):
    with col:
        st.metric(label=label, value=value, delta=delta, delta_color=delta_color)

kpi_card(col1, "Patients",              n_patients)
kpi_card(col2, "Total Visits",          n_visits)
kpi_card(col3, "Injections",            n_injections)
kpi_card(col4, "Mean BCVA Change",
         f"+{mean_bcva_change} letters" if mean_bcva_change >= 0 else f"{mean_bcva_change} letters")
kpi_card(col5, "IRF-free (last visit)", f"{irf_free_pct}%")
kpi_card(col6, "Adverse Events",        f"{n_aes} ({n_sae} SAE)")

st.markdown("---")

# ---------------------------------------------------------------------------
# Two-column summary
# ---------------------------------------------------------------------------
col_l, col_r = st.columns(2)

with get_session() as session:
    dx_rows = [
        name for (name,) in session.query(Diagnosis.condition_name).all()
    ]
    drug_rows = [
        name for (name,) in session.query(Treatment.drug_name).all()
    ]

with col_l:
    st.markdown("<div class='export-card'>", unsafe_allow_html=True)
    st.subheader("Diagnosis Distribution")
    if dx_rows:
        dx_series = pd.Series(dx_rows).value_counts().reset_index()
        dx_series.columns = ["Condition", "Patients"]
        st.dataframe(dx_series, use_container_width=True, hide_index=True)
    else:
        st.info("No diagnosis data yet.")
    st.markdown("</div>", unsafe_allow_html=True)

with col_r:
    st.markdown("<div class='export-card'>", unsafe_allow_html=True)
    st.subheader("Drug Usage")
    if drug_rows:
        drug_series = pd.Series(drug_rows).value_counts().reset_index()
        drug_series.columns = ["Drug", "Injections"]
        st.dataframe(drug_series, use_container_width=True, hide_index=True)
    else:
        st.info("No treatment data yet.")
    st.markdown("</div>", unsafe_allow_html=True)

st.markdown("---")

# ---------------------------------------------------------------------------
# Recent activity
# ---------------------------------------------------------------------------
st.markdown("<div class='export-card'>", unsafe_allow_html=True)
st.subheader("Recent Visits (last 10)")
with get_session() as session:
    recent_visits = (
        session.query(Visit)
        .order_by(Visit.visit_date.desc())
        .limit(10)
        .all()
    )
    rows = []
    for v in recent_visits:
        oc = v.outcomes[0] if v.outcomes else None
        tr = v.treatments[0] if v.treatments else None
        rows.append({
            "Patient token":  v.patient.patient_hash[:12] + "…",
            "Visit date":     v.visit_date.strftime("%Y-%m-%d"),
            "Eye":            v.eye,
            "Type":           v.visit_type,
            "Drug":           tr.drug_name if tr else "—",
            "BCVA (Snellen)": oc.bcva_snellen if oc else "—",
            "BCVA change (letters)": (
                f"{oc.bcva_change_from_baseline:+.1f}" if oc and oc.bcva_change_from_baseline is not None else "—"
            ),
            "CRT (µm)":       oc.crt_um if oc else "—",
            "IRF":            ("Yes" if oc.irf_present else "No") if oc else "—",
            "SRF":            ("Yes" if oc.srf_present else "No") if oc else "—",
        })

if rows:
    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
else:
    st.info("No visits recorded yet.")
st.markdown("</div>", unsafe_allow_html=True)

st.markdown("---")
st.markdown(
    "**Quick actions:** "
    "[Register a new patient](./01_Patient_Entry) · "
    "[Record a visit](./02_Visit_Entry) · "
    "[View analytics](./03_Analytics) · "
    "[Export data](./04_Data_Export)"
)
