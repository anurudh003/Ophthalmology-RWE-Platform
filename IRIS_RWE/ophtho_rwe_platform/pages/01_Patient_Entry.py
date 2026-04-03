"""
Page 1 — Patient Registration

Captures anonymised demographics and initial diagnosis.
No PII is stored — a one-way hash is generated from name + DOB
and only that token, plus generalised demographics, is persisted.
"""

import streamlit as st
from datetime import date, datetime

from database.db import get_session, init_db
from database.models import CONDITION_CATALOGUE, Diagnosis, Patient
from utils.anonymizer import dob_to_age_group, generate_patient_hash
from auth.auth import require_auth, render_sidebar_user_info, render_sidebar_logout, log_audit_event, has_page_access
from components.styles import inject_styles

st.set_page_config(page_title="Patient Registration", layout="wide")

inject_styles()
init_db()

# ---------------------------------------------------------------------------
# Auth guard — clinician and admin only
# ---------------------------------------------------------------------------
require_auth(page_id="patient_entry")
log_audit_event("PAGE_ACCESS", detail="patient_entry")

# ---------------------------------------------------------------------------
# Sidebar
# ---------------------------------------------------------------------------
with st.sidebar:
    st.page_link("app.py", label="Home / Dashboard")
    if has_page_access("patient_entry"):
        st.page_link("pages/01_Patient_Entry.py", label="Patient Registration")
    if has_page_access("visit_entry"):
        st.page_link("pages/02_Visit_Entry.py",   label="Visit & Treatment Entry")
    if has_page_access("analytics"):
        st.page_link("pages/03_Analytics.py",     label="Analytics & Outcomes")
    if has_page_access("data_export"):
        st.page_link("pages/04_Data_Export.py",   label="Data Export")
    st.markdown("---")
    st.info(
        "Name and date of birth are used only to generate an anonymous patient token "
        "and are never stored in the database."
    )
    render_sidebar_user_info()
    render_sidebar_logout()

# ---------------------------------------------------------------------------
# Page header
# ---------------------------------------------------------------------------
st.title("Patient Registration")

st.markdown("Register a new patient. All fields marked **\\*** are required.")
st.markdown("---")

# ---------------------------------------------------------------------------
# Form
# ---------------------------------------------------------------------------
with st.form("patient_registration_form", clear_on_submit=True):

    st.markdown("<div class='export-card'>", unsafe_allow_html=True)
    col1, col2 = st.columns(2)

    with col1:
        st.subheader("Identity (not stored)")
        given_name  = st.text_input("Given name *", placeholder="e.g. John")
        family_name = st.text_input("Family name *", placeholder="e.g. Smith")
        dob         = st.date_input(
            "Date of birth *",
            min_value=date(1920, 1, 1),
            max_value=date.today(),
            value=date(1950, 1, 1),
        )

    with col2:
        st.subheader("Demographics (stored as aggregates)")
        sex = st.selectbox("Sex *", ["Male", "Female", "Other / Prefer not to say"])
        ethnicity = st.selectbox("Ethnicity", [
            "White British", "White Other", "South Asian",
            "Black/African", "East Asian", "Mixed", "Other",
            "Prefer not to say",
        ])
        smoking = st.selectbox("Smoking status", [
            "Never", "Ex-smoker", "Current smoker", "Unknown",
        ])

    st.markdown("---")
    st.subheader("Co-morbidities")
    col3, col4 = st.columns(2)
    with col3:
        diabetes     = st.checkbox("Diabetes mellitus")
    with col4:
        hypertension = st.checkbox("Hypertension")
    st.markdown("</div>", unsafe_allow_html=True)

    st.markdown("<div class='export-card'>", unsafe_allow_html=True)
    st.subheader("Primary Ophthalmic Diagnosis")

    col5, col6 = st.columns(2)
    with col5:
        condition_label = st.selectbox(
            "Condition *",
            options=list(CONDITION_CATALOGUE.keys()),
        )
        condition_code, condition_name = CONDITION_CATALOGUE[condition_label]

        # "Other" — allow clinician to enter a free-text name
        if condition_label == "Other":
            condition_name = st.text_input(
                "Specify condition name *",
                placeholder="e.g. Idiopathic CNV",
            ) or "Other"

        st.caption(f"ICD-10 code: **{condition_code}** | Condition: **{condition_name}**")

    with col6:
        eye = st.selectbox("Affected eye *", ["OD (Right)", "OS (Left)", "OU (Both)"])
        eye_code = eye.split()[0]  # "OD", "OS", "OU"
        date_diagnosed = st.date_input(
            "Date of diagnosis",
            value=date.today(),
            max_value=date.today(),
        )

    notes = st.text_area("Clinical notes (optional)", height=80)
    st.markdown("</div>", unsafe_allow_html=True)

    submitted = st.form_submit_button("Register Patient", type="primary", use_container_width=True)

# ---------------------------------------------------------------------------
# On submit
# ---------------------------------------------------------------------------
if submitted:
    errors = []
    if not given_name.strip():
        errors.append("Given name is required.")
    if not family_name.strip():
        errors.append("Family name is required.")
    if dob >= date.today():
        errors.append("Date of birth must be in the past.")

    if errors:
        for e in errors:
            st.error(e)
    else:
        patient_hash = generate_patient_hash(given_name, family_name, dob)
        age_group    = dob_to_age_group(dob)

        with get_session() as session:
            # Check for duplicate
            existing = session.query(Patient).filter_by(patient_hash=patient_hash).first()

            if existing:
                st.warning(
                    f"A patient with this name and date of birth is already registered "
                    f"(token: `{patient_hash[:12]}…`). No duplicate created."
                )
            else:
                patient = Patient(
                    patient_hash=patient_hash,
                    age_group=age_group,
                    sex=sex,
                    ethnicity=ethnicity if ethnicity != "Prefer not to say" else None,
                    smoking_status=smoking,
                    diabetes=diabetes,
                    hypertension=hypertension,
                )
                session.add(patient)
                session.flush()

                dx = Diagnosis(
                    patient_id=patient.id,
                    eye=eye_code,
                    condition_code=condition_code,
                    icd10_code=condition_code,
                    condition_name=condition_name,
                    date_diagnosed=datetime.combine(date_diagnosed, datetime.min.time())
                    if hasattr(date_diagnosed, "year") else None,
                    notes=notes or None,
                )
                session.add(dx)

                st.success(
                    f"Patient registered successfully.\n\n"
                    f"**Anonymous token:** `{patient_hash[:16]}…`\n\n"
                    f"**Age group:** {age_group} | "
                    f"**Diagnosis:** {condition_name} ({condition_code}) | **Eye:** {eye_code}"
                )
                st.info("Proceed to **Visit Entry** to record the first clinic appointment.")


# ---------------------------------------------------------------------------
# Existing patient lookup
# ---------------------------------------------------------------------------
st.markdown("---")
st.subheader("Existing Patients")

with get_session() as session:
    patients = session.query(Patient).order_by(Patient.created_at.desc()).all()
    total = len(patients)

if total == 0:
    st.info("No patients registered yet.")
else:
    st.caption(f"{total} patient(s) registered in the system")

    import pandas as pd
    rows = []
    with get_session() as session:
        for p in session.query(Patient).order_by(Patient.created_at.desc()).all():
            dx_labels = [
                f"{d.condition_name} ({d.condition_code or d.icd10_code})"
                for d in p.diagnoses
            ]
            rows.append({
                "Token (first 12 chars)": p.patient_hash[:12] + "…",
                "Age Group":   p.age_group,
                "Sex":         p.sex,
                "Ethnicity":   p.ethnicity or "—",
                "Diabetes":    "Yes" if p.diabetes else "No",
                "HTN":         "Yes" if p.hypertension else "No",
                "Diagnosis":   ", ".join(dx_labels) if dx_labels else "—",
                "Visits":      len(p.visits),
                "Registered":  p.created_at.strftime("%Y-%m-%d") if p.created_at else "—",
            })

    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
