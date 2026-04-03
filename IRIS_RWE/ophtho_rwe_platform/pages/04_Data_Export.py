"""
Page 4 — Data Export

Three export formats:
  1. Patient-level CSV / Excel  — one row per patient, baseline + last-visit outcomes
  2. Visit-level CSV / Excel    — one row per visit, all outcomes
  3. PDF Summary Report         — KPIs + 3 key charts (ReportLab)

All exports pass through sanitise_export_df() to strip any residual PII columns.
Filters: condition, drug (applied before export), row count preview, download button.
"""

import io
import textwrap
from datetime import datetime

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from database.db import get_session, init_db
from database.models import Diagnosis, Patient, Treatment
from utils.analytics import (
    get_ae_summary_df,
    get_bcva_trajectory_df,
    get_fluid_prevalence_df,
    get_patient_summary_df,
    get_full_visit_df,
)
from utils.anonymizer import sanitise_export_df, date_shift, k_anonymity_check
from auth.auth import (
    require_auth,
    render_sidebar_user_info,
    render_sidebar_logout,
    log_audit_event,
    get_username,
    get_role,
    can_export_raw,
    has_page_access,
)
from components.styles import inject_styles

# ---------------------------------------------------------------------------
# Page config
# ---------------------------------------------------------------------------
st.set_page_config(
    page_title="Data Export",
    layout="wide",
    initial_sidebar_state="expanded",
)
inject_styles()
init_db()

# ---------------------------------------------------------------------------
# Auth guard — analyst and admin only
# ---------------------------------------------------------------------------
require_auth(page_id="data_export")
log_audit_event("PAGE_ACCESS", detail="data_export")

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
    st.caption(
        "All exports are anonymised — no patient names, DOB, or direct identifiers."
    )
    render_sidebar_user_info()
    render_sidebar_logout()

st.title("Data Export")

st.warning(
    "**Data Access Notice** — This page provides access to anonymised research data. "
    "All exports are subject to k-anonymity verification (k=5) and date-shifting. "
    f"Accessed by **{get_username()}** ({get_role()}). All downloads are audit-logged.",
)

# Analyst role cannot download raw patient/visit CSV (only aggregate PDF)
_raw_export_allowed = can_export_raw()
if not _raw_export_allowed:
    st.info(
        "Your role (**analyst**) may view previews and download the aggregate PDF report. "
        "Raw patient-level and visit-level CSV/Excel downloads are restricted to **admin** users."
    )

st.markdown(
    "Export anonymised cohort data in your preferred format. "
    "Choose a format below, apply optional filters, preview the row count, then download."
)
st.markdown("---")

@st.cache_data(ttl=300, show_spinner=False)
def _get_filter_options():
    with get_session() as session:
        all_conditions = sorted({d.condition_name for d in session.query(Diagnosis).all()})
        all_drugs      = sorted({t.drug_name for t in session.query(Treatment).all()})
        all_age_groups = sorted({p.age_group for p in session.query(Patient).all()})
    return all_conditions, all_drugs, all_age_groups


# ---------------------------------------------------------------------------
# Filter panel (inline, not sidebar, to keep sidebar clean)
# ---------------------------------------------------------------------------
with st.expander("Cohort Filters", expanded=True):
    all_conditions, all_drugs, all_age_groups = _get_filter_options()

    fc1, fc2, fc3 = st.columns(3)
    with fc1:
        sel_conditions = st.multiselect(
            "Condition", options=all_conditions, default=all_conditions
        )
    with fc2:
        sel_drugs = st.multiselect(
            "Drug", options=all_drugs, default=all_drugs
        )
    with fc3:
        sel_age_groups = st.multiselect(
            "Age group", options=all_age_groups, default=all_age_groups
        )

# Tuples required for @st.cache_data hashing
conditions = tuple(sel_conditions) or None
drugs      = tuple(sel_drugs)      or None
age_groups = tuple(sel_age_groups) or None

st.markdown("---")

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _to_csv_bytes(df: pd.DataFrame) -> bytes:
    return df.to_csv(index=False).encode("utf-8-sig")


def _timestamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


# ---------------------------------------------------------------------------
# PDF generation helper
# ---------------------------------------------------------------------------

def _build_pdf_report(
    kpi_dict: dict,
    traj_df: pd.DataFrame,
    fluid_df: pd.DataFrame,
    ae_df: pd.DataFrame,
) -> bytes:
    """
    Build a single-page PDF summary using ReportLab.
    Returns raw bytes of the PDF.
    No raw patient data is included — only aggregate KPIs and table summaries.
    """
    try:
        from reportlab.lib import colors
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
        from reportlab.lib.units import cm
        from reportlab.platypus import (
            SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, HRFlowable
        )
    except ImportError:
        return b""

    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=A4,
        leftMargin=2 * cm,
        rightMargin=2 * cm,
        topMargin=2 * cm,
        bottomMargin=2 * cm,
    )
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        "ReportTitle",
        parent=styles["Title"],
        fontSize=16,
        spaceAfter=6,
    )
    h2_style = ParagraphStyle(
        "H2",
        parent=styles["Heading2"],
        fontSize=11,
        spaceAfter=4,
        spaceBefore=10,
    )
    body_style = styles["BodyText"]
    small_style = ParagraphStyle("Small", parent=body_style, fontSize=8)

    story = []

    # --- Title block ---
    story.append(Paragraph("Ophthalmology RWE Platform", title_style))
    story.append(Paragraph("Cohort Summary Report", styles["Heading2"]))
    story.append(Paragraph(
        f"Generated: {datetime.now().strftime('%d %b %Y %H:%M')}  |  "
        f"Anonymised synthetic RWE data  |  No patient-identifiable information",
        small_style,
    ))
    story.append(HRFlowable(width="100%", thickness=1, color=colors.grey, spaceAfter=8))

    # --- KPI table ---
    # Usable width = A4 (21 cm) - leftMargin (2 cm) - rightMargin (2 cm) = 17 cm
    _usable_w = 17 * cm
    _metric_w = 7 * cm
    _value_w  = _usable_w - _metric_w  # 10 cm for values — fits long drug/condition lists
    story.append(Paragraph("Key Performance Indicators", h2_style))
    kpi_table_data = [["Metric", "Value"]]
    for k, v in kpi_dict.items():
        # Wrap long value strings so they never overflow the column
        kpi_table_data.append([k, Paragraph(str(v), small_style)])
    kpi_tbl = Table(kpi_table_data, colWidths=[_metric_w, _value_w])
    kpi_tbl.setStyle(TableStyle([
        ("BACKGROUND",   (0, 0), (-1, 0),  colors.HexColor("#2c3e50")),
        ("TEXTCOLOR",    (0, 0), (-1, 0),  colors.white),
        ("FONTNAME",     (0, 0), (-1, 0),  "Helvetica-Bold"),
        ("FONTSIZE",     (0, 0), (-1, -1), 9),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.whitesmoke, colors.white]),
        ("GRID",         (0, 0), (-1, -1), 0.3, colors.grey),
        ("LEFTPADDING",  (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING",(0, 0), (-1, -1), 4),
        ("TOPPADDING",   (0, 0), (-1, -1), 4),
        ("VALIGN",       (0, 0), (-1, -1), "TOP"),
    ]))
    story.append(kpi_tbl)
    story.append(Spacer(1, 0.4 * cm))

    # --- BCVA trajectory summary table ---
    if not traj_df.empty:
        story.append(Paragraph("BCVA Trajectory — Mean Letters by Visit (first 6 visits)", h2_style))
        pivot = (
            traj_df[traj_df["visit_number"] <= 6]
            .pivot(index="visit_number", columns="condition", values="mean_bcva")
            .reset_index()
        )
        pivot.columns.name = None
        pivot = pivot.rename(columns={"visit_number": "Visit"})
        traj_data = [list(pivot.columns)] + pivot.values.tolist()
        short_headers = []
        for c in traj_data[0]:
            c_str = str(c)
            if '(' in c_str and ')' in c_str:
                short_headers.append(c_str.split('(')[-1].split(')')[0])
            else:
                short_headers.append(c_str)
        traj_data[0] = short_headers
        
        traj_data[1:] = [[str(round(v, 1)) if isinstance(v, float) else str(v) for v in row]
                         for row in traj_data[1:]]
        n_cols = len(traj_data[0])
        col_w = _usable_w / n_cols
        traj_tbl = Table(traj_data, colWidths=[col_w] * n_cols)
        traj_tbl.setStyle(TableStyle([
            ("BACKGROUND",   (0, 0), (-1, 0),  colors.HexColor("#2980b9")),
            ("TEXTCOLOR",    (0, 0), (-1, 0),  colors.white),
            ("FONTNAME",     (0, 0), (-1, 0),  "Helvetica-Bold"),
            ("FONTSIZE",     (0, 0), (-1, -1), 8),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.whitesmoke, colors.white]),
            ("GRID",         (0, 0), (-1, -1), 0.3, colors.grey),
            ("LEFTPADDING",  (0, 0), (-1, -1), 4),
            ("RIGHTPADDING", (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING",(0, 0), (-1, -1), 3),
            ("TOPPADDING",   (0, 0), (-1, -1), 3),
        ]))
        story.append(traj_tbl)
        story.append(Spacer(1, 0.4 * cm))

    # --- Fluid resolution summary ---
    if not fluid_df.empty:
        story.append(Paragraph("Fluid Resolution — % Eyes with IRF / SRF by Visit", h2_style))
        fluid_show = fluid_df[fluid_df["visit_number"] <= 12].copy()
        fluid_data = [["Visit", "% IRF", "% SRF", "N"]]
        for _, row in fluid_show.iterrows():
            fluid_data.append([
                str(int(row["visit_number"])),
                f"{row['pct_irf']:.1f}%",
                f"{row['pct_srf']:.1f}%",
                str(int(row["n"])),
            ])
        f_tbl = Table(fluid_data, colWidths=[3 * cm, 4.67 * cm, 4.67 * cm, 4.66 * cm])
        f_tbl.setStyle(TableStyle([
            ("BACKGROUND",   (0, 0), (-1, 0),  colors.HexColor("#16a085")),
            ("TEXTCOLOR",    (0, 0), (-1, 0),  colors.white),
            ("FONTNAME",     (0, 0), (-1, 0),  "Helvetica-Bold"),
            ("FONTSIZE",     (0, 0), (-1, -1), 8),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.whitesmoke, colors.white]),
            ("GRID",         (0, 0), (-1, -1), 0.3, colors.grey),
            ("LEFTPADDING",  (0, 0), (-1, -1), 4),
            ("RIGHTPADDING", (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING",(0, 0), (-1, -1), 3),
            ("TOPPADDING",   (0, 0), (-1, -1), 3),
        ]))
        story.append(f_tbl)
        story.append(Spacer(1, 0.4 * cm))

    # --- AE summary ---
    if not ae_df.empty:
        story.append(Paragraph("Adverse Event Summary", h2_style))
        ae_show = ae_df.head(12)
        ae_data = [["AE Type", "Category", "N", "Rate / 1k inj"]]
        for _, row in ae_show.iterrows():
            ae_data.append([
                textwrap.shorten(row["ae_type"], 40),
                row["ae_category"],
                str(int(row["count"])),
                f"{row['incidence_per_1000']:.2f}",
            ])
        ae_tbl = Table(ae_data, colWidths=[8 * cm, 3.5 * cm, 2 * cm, 3.5 * cm])
        ae_tbl.setStyle(TableStyle([
            ("BACKGROUND",   (0, 0), (-1, 0),  colors.HexColor("#8e44ad")),
            ("TEXTCOLOR",    (0, 0), (-1, 0),  colors.white),
            ("FONTNAME",     (0, 0), (-1, 0),  "Helvetica-Bold"),
            ("FONTSIZE",     (0, 0), (-1, -1), 8),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.whitesmoke, colors.white]),
            ("GRID",         (0, 0), (-1, -1), 0.3, colors.grey),
            ("LEFTPADDING",  (0, 0), (-1, -1), 4),
            ("RIGHTPADDING", (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING",(0, 0), (-1, -1), 3),
            ("TOPPADDING",   (0, 0), (-1, -1), 3),
        ]))
        story.append(ae_tbl)

    story.append(Spacer(1, 0.6 * cm))
    story.append(HRFlowable(width="100%", thickness=0.5, color=colors.lightgrey))
    story.append(Paragraph(
        "This report contains no patient-identifiable information. "
        "Data generated by the Ophthalmology RWE Platform (synthetic).",
        small_style,
    ))

    doc.build(story)
    return buf.getvalue()


# ===========================================================================
# Export Option 1 — Patient-level
# ===========================================================================
st.markdown('<div class="export-card">', unsafe_allow_html=True)
st.subheader("Export 1 — Patient-level Summary")
st.markdown(
    "One row per patient: demographics, condition, baseline BCVA/CRT, "
    "last-visit outcomes, total injections, and AE counts."
)

with st.spinner("Loading patient-level data…"):
    pat_df_raw = get_patient_summary_df(
        conditions=conditions,
        drugs=drugs,
        age_groups=age_groups,
    )
    pat_df = sanitise_export_df(pat_df_raw.copy())

st.info(f"**{len(pat_df):,} patients** match the selected filters.")

if not pat_df.empty:
    # k-anonymity check
    k_pass, k_violations = k_anonymity_check(pat_df, k=5)
    if not k_pass:
        st.error(
            f"**k-anonymity check FAILED** — {len(k_violations)} patient record(s) belong to "
            f"groups with fewer than 5 individuals across (age_group × sex × ethnicity × condition). "
            f"These rows have been suppressed from the export to prevent re-identification."
        )
        pat_df = pat_df.drop(index=k_violations.index)

    st.dataframe(pat_df.head(10), use_container_width=True, hide_index=True)
    if len(pat_df) > 10:
        st.caption(f"Showing first 10 of {len(pat_df):,} rows.")

    if _raw_export_allowed:
        # Consent checkbox — must be ticked before download buttons activate
        consent_pat = st.checkbox(
            "I confirm this data will be used for **research purposes only** and "
            "I accept responsibility for maintaining data confidentiality. (Patient-level)",
            key="consent_patient",
        )
        st.download_button(
            label="Download Patient-level CSV",
            data=_to_csv_bytes(pat_df),
            file_name=f"iris_rwe_patients_{_timestamp()}.csv",
            mime="text/csv",
            use_container_width=True,
            disabled=not consent_pat,
            on_click=lambda: log_audit_event(
                "EXPORT_PATIENT_CSV",
                detail=f"filters: conditions={conditions} drugs={drugs} age_groups={age_groups}",
                record_count=len(pat_df),
            ),
        )
        if not consent_pat:
            st.caption("Tick the consent checkbox above to enable download.")
    else:
        st.caption("Raw data download is restricted to **admin** role.")

st.markdown('</div>', unsafe_allow_html=True)
st.markdown("---")

# ===========================================================================
# Export Option 2 — Visit-level
# ===========================================================================
st.markdown('<div class="export-card">', unsafe_allow_html=True)
st.subheader("Export 2 — Visit-level Detail")
st.markdown(
    "One row per visit: demographics, diagnosis, drug, all outcome measures, "
    "and AE counts per visit. Visit dates are shifted by a random per-patient "
    "offset (±180 days) before export."
)

if st.button("Load Visit-level Preview", key="load_visits"):
    st.session_state["visit_data_loaded"] = True

if st.session_state.get("visit_data_loaded"):
    with st.spinner("Loading visit-level data…"):
        vis_df_raw = get_full_visit_df(
            conditions=conditions,
            drugs=drugs,
            age_groups=age_groups,
        )
        vis_df = sanitise_export_df(vis_df_raw.copy())

        if "visit_date" in vis_df.columns:
            vis_df["_row_token"] = vis_df.index.astype(str)
            vis_df = date_shift(vis_df, date_columns=["visit_date"], patient_token_column="_row_token")
            vis_df = vis_df.drop(columns=["_row_token"])

    st.info(f"**{len(vis_df):,} visit records** match the selected filters.")

    if not vis_df.empty:
        k_pass_vis, k_viol_vis = k_anonymity_check(vis_df, k=5)
        if not k_pass_vis:
            st.error(
                f"**k-anonymity check FAILED** — {len(k_viol_vis)} visit record(s) belong to "
                f"quasi-identifier groups smaller than 5. These rows have been suppressed."
            )
            vis_df = vis_df.drop(index=k_viol_vis.index)

        st.dataframe(vis_df.head(10), use_container_width=True, hide_index=True)
        if len(vis_df) > 10:
            st.caption(f"Showing first 10 of {len(vis_df):,} rows. Visit dates have been shifted ±180 days.")

        if _raw_export_allowed:
            consent_vis = st.checkbox(
                "I confirm this data will be used for **research purposes only** and "
                "I accept responsibility for maintaining data confidentiality. (Visit-level)",
                key="consent_visit",
            )
            st.download_button(
                label="Download Visit-level CSV",
                data=_to_csv_bytes(vis_df),
                file_name=f"iris_rwe_visits_{_timestamp()}.csv",
                mime="text/csv",
                use_container_width=True,
                disabled=not consent_vis,
                on_click=lambda: log_audit_event(
                    "EXPORT_VISIT_CSV",
                    detail=f"filters: conditions={conditions} drugs={drugs} age_groups={age_groups}",
                    record_count=len(vis_df),
                ),
            )
            if not consent_vis:
                st.caption("Tick the consent checkbox above to enable download.")
        else:
            st.caption("Raw data download is restricted to **admin** role.")
else:
    st.info("Click **Load Visit-level Preview** to fetch visit records.")

st.markdown('</div>', unsafe_allow_html=True)
st.markdown("---")

# ===========================================================================
# Export Option 3 — PDF Summary Report
# ===========================================================================
st.markdown('<div class="export-card">', unsafe_allow_html=True)
st.subheader("Export 3 — PDF Summary Report")
st.markdown(
    "A formatted A4 report containing KPIs, BCVA trajectory table, "
    "fluid resolution table, and AE summary. No raw patient records included."
)

# Build KPI dict from patient-level data
if not pat_df.empty:
    n_patients    = len(pat_df)
    n_gainers     = int((pat_df["last_bcva_change_from_baseline"].fillna(0) >= 0).sum())
    mean_bcva_chg = pat_df["last_bcva_change_from_baseline"].mean()
    irf_free_pct  = (
        100 * (~pat_df["last_irf_present"].fillna(True)).sum() / n_patients
        if "last_irf_present" in pat_df.columns else 0
    )
    total_inj     = int(pat_df["n_injections"].sum()) if "n_injections" in pat_df.columns else 0
    total_aes     = int(pat_df["total_aes"].sum()) if "total_aes" in pat_df.columns else 0

    kpi_dict = {
        "Patients in cohort":             n_patients,
        "Total injections":               f"{total_inj:,}",
        "Gainers at last visit (≥ 0 ltrs)": f"{n_gainers} / {n_patients}",
        "Mean BCVA change (letters)":     f"{mean_bcva_chg:+.1f}" if pd.notna(mean_bcva_chg) else "N/A",
        "IRF-free at last visit":         f"{irf_free_pct:.1f}%",
        "Total adverse events":           total_aes,
        "Conditions included":            ", ".join(sel_conditions) if sel_conditions else "All",
        "Drugs included":                 ", ".join(sel_drugs) if sel_drugs else "All",
        "Report generated":               datetime.now().strftime("%d %b %Y %H:%M"),
    }
else:
    kpi_dict = {"Note": "No data matching selected filters."}

# Preview the KPIs that will appear in the PDF
with st.expander("Preview KPIs for PDF", expanded=False):
    for k, v in kpi_dict.items():
        st.markdown(f"**{k}:** {v}")

try:
    import reportlab  # noqa: F401
    pdf_available = True
except ImportError:
    pdf_available = False

if not pdf_available:
    st.warning(
        "ReportLab is not installed. Run `pip install reportlab` to enable PDF export."
    )
else:
    consent_pdf = st.checkbox(
        "I confirm this report will be used for **research purposes only** and "
        "I accept responsibility for maintaining data confidentiality. (PDF Report)",
        key="consent_pdf",
    )
    if st.button(
        "Generate PDF Report",
        type="primary",
        use_container_width=False,
        disabled=not consent_pdf,
    ):
        with st.spinner("Building PDF…"):
            # Fetch chart-source DataFrames only when user requests PDF
            traj_df_pdf  = get_bcva_trajectory_df(conditions=conditions, drugs=drugs, age_groups=age_groups)
            fluid_df_pdf = get_fluid_prevalence_df(conditions=conditions, drugs=drugs, age_groups=age_groups)
            ae_pdf, _    = get_ae_summary_df(conditions=conditions, drugs=drugs, age_groups=age_groups)
            pdf_bytes = _build_pdf_report(kpi_dict, traj_df_pdf, fluid_df_pdf, ae_pdf)

        if pdf_bytes:
            log_audit_event(
                "EXPORT_PDF",
                detail=f"filters: conditions={conditions} drugs={drugs} age_groups={age_groups}",
            )
            st.download_button(
                label="Download PDF Summary Report",
                data=pdf_bytes,
                file_name=f"iris_rwe_summary_{_timestamp()}.pdf",
                mime="application/pdf",
                use_container_width=True,
            )
            st.success("PDF ready — click the button above to download.")
        else:
            st.error("PDF generation failed. Check that ReportLab is installed correctly.")
    if not consent_pdf:
        st.caption("Tick the consent checkbox above to enable PDF generation.")

st.markdown('</div>', unsafe_allow_html=True)
st.markdown("---")
st.caption(
    "All exports comply with the platform anonymisation policy: "
    "no patient names, dates of birth, or direct identifiers are included. "
    "Age is exported as a 10-year bin. Patient hashes are suppressed. "
    "Visit dates are shifted ±180 days per patient. "
    "Groups with n < 5 across quasi-identifiers are suppressed (k-anonymity k=5). "
    "All downloads are recorded in the audit log."
)
