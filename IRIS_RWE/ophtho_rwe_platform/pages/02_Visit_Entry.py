"""
Page 2 — Visit, Treatment, Outcome & Adverse Event Entry

A single page with four collapsible sections:
  1. Visit details (date, type, eye, site)
  2. Treatment (drug, dose, injection count)
  3. Outcome (BCVA, CRT, IRF/SRF, IOP)
  4. Adverse events (optional)
"""

import streamlit as st
from datetime import date, datetime

import pandas as pd

from database.db import get_session, init_db
from database.models import (
    ALWAYS_SAE_CLASSIFICATIONS,
    AEClassification,
    AdverseEvent,
    Diagnosis,
    OCULAR_AE_CLASSIFICATIONS,
    Outcome,
    Patient,
    Treatment,
    Visit,
)
from utils.anonymizer import logmar_to_etdrs, logmar_to_snellen, snellen_to_logmar
from auth.auth import require_auth, render_sidebar_user_info, render_sidebar_logout, log_audit_event, has_page_access
from components.styles import inject_styles

st.set_page_config(page_title="Visit & Treatment Entry", layout="wide")

inject_styles()
init_db()

# ---------------------------------------------------------------------------
# Auth guard — clinician and admin only
# ---------------------------------------------------------------------------
require_auth(page_id="visit_entry")
log_audit_event("PAGE_ACCESS", detail="visit_entry")

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
    st.caption("Record a clinic visit, treatment given, visual/anatomical outcomes, and any adverse events.")
    render_sidebar_user_info()
    render_sidebar_logout()
    
st.title("Visit & Treatment Entry")
st.markdown("---")

# ---------------------------------------------------------------------------
# Patient selector
# ---------------------------------------------------------------------------
st.subheader("1. Select Patient")

with get_session() as session:
    patients = session.query(Patient).order_by(Patient.id).all()
    patient_options = {
        f"{p.patient_hash[:12]}… | {p.age_group} | {p.sex} | "
        f"{', '.join(d.condition_name for d in p.diagnoses)}": p.id
        for p in patients
    }

if not patient_options:
    st.warning("No patients registered. Please register a patient first on the **Patient Registration** page.")
    st.stop()

selected_label = st.selectbox("Patient token", list(patient_options.keys()))
patient_id     = patient_options[selected_label]

# Load existing visit count and baseline for this patient
with get_session() as session:
    patient      = session.query(Patient).get(patient_id)
    visit_count  = len(patient.visits)
    diagnoses    = patient.diagnoses
    primary_dx   = diagnoses[0] if diagnoses else None
    eye_default  = primary_dx.eye if primary_dx else "OD"

    # Get baseline BCVA from first visit outcome (if exists)
    baseline_logmar = None
    baseline_crt    = None
    if patient.visits:
        first_visit = sorted(patient.visits, key=lambda v: v.visit_date)[0]
        if first_visit.outcomes:
            baseline_logmar = first_visit.outcomes[0].bcva_logmar
            baseline_crt    = first_visit.outcomes[0].crt_um

    # Total injections so far
    total_injections = sum(
        len(v.treatments) for v in patient.visits
    )

st.caption(
    f"Patient has **{visit_count}** recorded visit(s) | "
    f"**{total_injections}** injection(s) to date"
)

st.markdown("---")

# ---------------------------------------------------------------------------
# Main form
# ---------------------------------------------------------------------------
with st.form("visit_entry_form", clear_on_submit=False):

    # --- Visit Details ---
    st.markdown("<div class='export-card'>", unsafe_allow_html=True)
    st.subheader("2. Visit Details")
    col1, col2, col3 = st.columns(3)
    with col1:
        visit_date  = st.date_input("Visit date *", value=date.today(), max_value=date.today())
    with col2:
        eye = st.selectbox("Eye treated *", ["OD (Right)", "OS (Left)", "OU (Both)"],
                           index=["OD", "OS", "OU"].index(eye_default) if eye_default in ["OD","OS","OU"] else 0)
        eye_code = eye.split()[0]
    with col3:
        visit_type = st.selectbox("Visit type *",
                                  ["Loading", "Maintenance", "PRN", "T&E", "Monitoring only"])

    col4, col5 = st.columns(2)
    with col4:
        clinician_code = st.text_input("Clinician code (anonymised)", placeholder="e.g. CLIN-01")
    with col5:
        site_code = st.text_input("Site code (anonymised)", placeholder="e.g. SITE-01")

    visit_notes = st.text_area("Visit notes", height=60)
    st.markdown("</div>", unsafe_allow_html=True)

    # --- Treatment ---
    st.markdown("<div class='export-card'>", unsafe_allow_html=True)
    st.subheader("3. Treatment")
    treatment_given = st.checkbox("Injection given at this visit", value=True)

    col6, col7, col8 = st.columns(3)
    with col6:
        drug_name = st.selectbox("Drug *", [
            "Aflibercept (Eylea)",
            "Aflibercept HD (Eylea HD)",
            "Ranibizumab (Lucentis)",
            "Bevacizumab (Avastin — off-label)",
            "Faricimab (Vabysmo)",
            "Brolucizumab (Beovu)",
            "Other",
        ])
        drug_clean = drug_name.split(" (")[0]   # strip brand name
    with col7:
        drug_dose = st.number_input("Dose (mg)", min_value=0.1, max_value=10.0,
                                    value=2.0, step=0.25)
    with col8:
        injection_site = st.selectbox("Injection route", [
            "Intravitreal", "Sub-Tenon", "Subconjunctival", "Periocular",
        ])

    injection_number = st.number_input(
        "Cumulative injection number (this eye)",
        min_value=1, max_value=200,
        value=total_injections + 1,
    )
    concomitant_meds = st.text_input("Concomitant medications (comma-separated)", placeholder="e.g. Latanoprost, Timolol")
    st.markdown("</div>", unsafe_allow_html=True)

    # --- Outcome ---
    st.markdown("<div class='export-card'>", unsafe_allow_html=True)
    st.subheader("4. Outcome Measures")

    col9, col10 = st.columns(2)
    with col9:
        st.markdown("**Visual Acuity**")
        va_method = st.radio("VA input method", ["logMAR", "Snellen", "ETDRS letters"], horizontal=True)

        if va_method == "logMAR":
            bcva_logmar = st.number_input("BCVA (logMAR) *", min_value=0.0, max_value=3.0,
                                          value=0.30, step=0.01, format="%.2f")
            bcva_snellen = logmar_to_snellen(bcva_logmar)
            bcva_etdrs   = logmar_to_etdrs(bcva_logmar)
            st.caption(f"Snellen equivalent: **{bcva_snellen}** | ETDRS: **{bcva_etdrs}** letters")

        elif va_method == "Snellen":
            snellen_opts = ["6/3","6/4","6/5","6/6","6/7.5","6/9","6/12","6/15",
                            "6/18","6/24","6/30","6/36","6/60","3/60","CF","HM","PL","NPL"]
            bcva_snellen_sel = st.selectbox("BCVA (Snellen)", snellen_opts, index=6)
            bcva_logmar  = snellen_to_logmar(bcva_snellen_sel) or 0.30
            bcva_snellen = bcva_snellen_sel
            bcva_etdrs   = logmar_to_etdrs(bcva_logmar)
            st.caption(f"logMAR: **{bcva_logmar:.2f}** | ETDRS: **{bcva_etdrs}** letters")

        else:  # ETDRS
            bcva_etdrs_in = st.number_input("BCVA (ETDRS letters)", min_value=0, max_value=100, value=65)
            bcva_logmar   = round((85 - bcva_etdrs_in) / 50, 2)
            bcva_snellen  = logmar_to_snellen(bcva_logmar)
            bcva_etdrs    = bcva_etdrs_in
            st.caption(f"logMAR: **{bcva_logmar:.2f}** | Snellen: **{bcva_snellen}**")

        # Change from baseline
        if baseline_logmar is not None:
            change_letters = round((baseline_logmar - bcva_logmar) * 50, 1)
            colour = "green" if change_letters >= 0 else "red"
            st.markdown(
                f"Change from baseline: "
                f"<span style='color:{colour};font-weight:bold'>{change_letters:+.1f} letters</span>",
                unsafe_allow_html=True,
            )

    with col10:
        st.markdown("**OCT / Anatomical**")
        crt_um = st.number_input("Central Retinal Thickness (µm)", min_value=100, max_value=800,
                                  value=300, step=5)
        if baseline_crt is not None:
            crt_change = baseline_crt - crt_um
            colour = "green" if crt_change >= 0 else "red"
            st.markdown(
                f"CRT change: <span style='color:{colour};font-weight:bold'>{crt_change:+d} µm</span>",
                unsafe_allow_html=True,
            )

        irf_present = st.checkbox("IRF present (intraretinal fluid)")
        srf_present = st.checkbox("SRF present (subretinal fluid)")
        ped_height  = st.number_input("PED height (µm)", min_value=0, max_value=1000,
                                       value=0, step=10) if srf_present else None
        iop_mmhg    = st.number_input("IOP (mmHg)", min_value=4.0, max_value=70.0,
                                       value=14.0, step=0.5)

    st.markdown("</div>", unsafe_allow_html=True)

    # --- Adverse Events ---
    st.markdown("<div class='export-card'>", unsafe_allow_html=True)
    st.subheader("5. Adverse Events")
    has_ae = st.checkbox("Record an adverse event at this visit")

    ae_classification = ae_type = ae_category = ae_grade = None
    ae_serious = ae_related = ae_resolved = False
    ae_description = ""

    if has_ae:
        col11, col12 = st.columns(2)
        with col11:
            # Classification drives category and SAE pre-fill — single source of truth
            ae_cls_value = st.selectbox(
                "AE classification *",
                options=[c.value for c in AEClassification],
                help="Select the controlled-vocabulary classification. "
                     "Category (Ocular/Systemic) and SAE flag are set automatically "
                     "for endophthalmitis, retinal detachment, and thromboembolic events.",
            )
            ae_classification = AEClassification(ae_cls_value)
            ae_category = (
                "Ocular" if ae_classification in OCULAR_AE_CLASSIFICATIONS else "Systemic"
            )
            # ae_type stores the display label (same as enum value for named types,
            # overridable via free-text for "Other")
            if ae_classification == AEClassification.OTHER:
                ae_type = st.text_input(
                    "Specify AE type *",
                    placeholder="e.g. Corneal abrasion",
                )
            else:
                ae_type = ae_cls_value

            # CTCAE grade definitions (NCI CTCAE v5.0 generic scale)
            _CTCAE_OPTIONS = {
                1: "Grade 1 — Mild: asymptomatic or mild symptoms, no intervention needed",
                2: "Grade 2 — Moderate: minimal local/non-invasive intervention indicated",
                3: "Grade 3 — Severe: hospitalisation or operative intervention indicated",
                4: "Grade 4 — Life-threatening: urgent intervention required",
                5: "Grade 5 — Death related to the adverse event",
            }
            ae_grade_label = st.selectbox(
                "Severity (CTCAE grade) *",
                options=list(_CTCAE_OPTIONS.values()),
                help="NCI CTCAE v5.0 generic grading scale.",
            )
            ae_grade = [k for k, v in _CTCAE_OPTIONS.items() if v == ae_grade_label][0]
            st.caption(f"Category: **{ae_category}** | Grade: **{ae_grade}**")

        with col12:
            # Auto-flag SAE for classifications that are always serious
            auto_sae = ae_classification in ALWAYS_SAE_CLASSIFICATIONS
            ae_serious = st.checkbox(
                "Serious Adverse Event (SAE)",
                value=auto_sae,
                help="Pre-checked for endophthalmitis, retinal detachment, and thromboembolic events.",
            )
            ae_related  = st.checkbox("Related to treatment")
            ae_resolved = st.checkbox("Resolved")
            ae_description = st.text_area("Description / action taken", height=80)
    st.markdown("</div>", unsafe_allow_html=True)

    submitted = st.form_submit_button("Save Visit Record", type="primary", use_container_width=True)

# ---------------------------------------------------------------------------
# Save
# ---------------------------------------------------------------------------
if submitted:
    with get_session() as session:
        new_visit = Visit(
            patient_id=patient_id,
            visit_date=datetime.combine(visit_date, datetime.min.time()),
            visit_number=visit_count + 1,
            eye=eye_code,
            visit_type=visit_type,
            clinician_code=clinician_code or None,
            site_code=site_code or None,
            notes=visit_notes or None,
        )
        session.add(new_visit)
        session.flush()

        if treatment_given:
            treat = Treatment(
                visit_id=new_visit.id,
                drug_name=drug_clean,
                drug_dose_mg=drug_dose,
                injection_number=injection_number,
                injection_site=injection_site,
                concomitant_medications=concomitant_meds or None,
            )
            session.add(treat)

        crt_change_from_bl = (baseline_crt - crt_um) if baseline_crt else None
        bcva_change_from_bl = (
            round((baseline_logmar - bcva_logmar) * 50, 1)
            if baseline_logmar is not None else None
        )

        outcome = Outcome(
            visit_id=new_visit.id,
            bcva_logmar=bcva_logmar,
            bcva_etdrs_letters=bcva_etdrs,
            bcva_snellen=bcva_snellen,
            crt_um=crt_um,
            irf_present=irf_present,
            srf_present=srf_present,
            ped_height_um=ped_height,
            iop_mmhg=iop_mmhg,
            bcva_change_from_baseline=bcva_change_from_bl,
            crt_change_from_baseline=crt_change_from_bl,
        )
        session.add(outcome)

        if has_ae and ae_type:
            ae = AdverseEvent(
                visit_id=new_visit.id,
                ae_classification=ae_classification.value if ae_classification else AEClassification.OTHER.value,
                ae_type=ae_type,
                ae_category=ae_category,
                severity_grade=ae_grade,
                serious=ae_serious,
                related_to_treatment=ae_related,
                onset_date=datetime.combine(visit_date, datetime.min.time()),
                resolved=ae_resolved,
                description=ae_description or None,
            )
            session.add(ae)

    st.success(
        f"Visit {visit_count + 1} saved. "
        f"Drug: **{drug_clean if treatment_given else 'None'}** | "
        f"BCVA: **{bcva_snellen}** ({bcva_logmar:.2f} logMAR) | "
        f"CRT: **{crt_um} µm** | IRF: {'Yes' if irf_present else 'No'} | SRF: {'Yes' if srf_present else 'No'}"
    )
    st.balloons()

# ---------------------------------------------------------------------------
# Visit history for selected patient
# ---------------------------------------------------------------------------
st.markdown("---")
st.subheader("Visit History for Selected Patient")

rows = []
with get_session() as session:
    patient = session.query(Patient).get(patient_id)
    for v in sorted(patient.visits, key=lambda x: x.visit_date):
        oc = v.outcomes[0] if v.outcomes else None
        tr = v.treatments[0] if v.treatments else None
        rows.append({
            "Visit #":    v.visit_number,
            "Date":       v.visit_date.strftime("%Y-%m-%d"),
            "Type":       v.visit_type,
            "Eye":        v.eye,
            "Drug":       tr.drug_name if tr else "—",
            "Inj #":      tr.injection_number if tr else "—",
            "BCVA (logMAR)": f"{oc.bcva_logmar:.2f}" if oc and oc.bcva_logmar is not None else "—",
            "BCVA (Snellen)": oc.bcva_snellen if oc else "—",
            "ETDRS": oc.bcva_etdrs_letters if oc else "—",
            "BCVA Δ (letters)": (
                f"{oc.bcva_change_from_baseline:+.1f}" if oc and oc.bcva_change_from_baseline is not None else "—"
            ),
            "CRT (µm)":   oc.crt_um if oc else "—",
            "CRT Δ (µm)": (
                f"{oc.crt_change_from_baseline:+d}" if oc and oc.crt_change_from_baseline is not None else "—"
            ),
            "IRF":        ("Yes" if oc.irf_present else "No") if oc else "—",
            "SRF":        ("Yes" if oc.srf_present else "No") if oc else "—",
            "IOP (mmHg)": oc.iop_mmhg if oc else "—",
            "AEs":        len(v.adverse_events),
        })

if rows:
    df = pd.DataFrame(rows)
    st.dataframe(df, use_container_width=True, hide_index=True)
else:
    st.info("No visits recorded for this patient yet.")
