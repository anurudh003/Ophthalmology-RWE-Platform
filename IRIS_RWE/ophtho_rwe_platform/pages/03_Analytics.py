"""
Page 3 — Analytics & Outcomes

Five core visualisations:
  1. BCVA Trajectory Over Time        — line + ±1 SD band by condition
  2. Waterfall Plot (BCVA Δ @ last visit) — sorted bar, green/red
  3. IRF / SRF Resolution Over Visits — dual-line % present
  4. Injection Interval Distribution  — histogram + median line
  5. Adverse Event Summary            — horizontal bar + SAE table

Sidebar filters: condition, drug, eye, age group, visit range
"""

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import plotly.express as px
import streamlit as st

from database.db import get_session, init_db
from database.models import Diagnosis, Patient, Treatment
from utils.analytics import (
    get_ae_grade_distribution_df,
    get_ae_summary_df,
    get_bcva_by_injection_df,
    get_bcva_cohort_comparison_df,
    get_bcva_trajectory_df,
    get_fluid_prevalence_df,
    get_injection_interval_df,
    get_waterfall_df,
)
from utils.anonymizer import small_cell_suppress
from auth.auth import require_auth, render_sidebar_user_info, render_sidebar_logout, log_audit_event, get_username, get_role, has_page_access
from components.styles import inject_styles

# ---------------------------------------------------------------------------
# Page config
# ---------------------------------------------------------------------------
st.set_page_config(
    page_title="Analytics & Outcomes",
    layout="wide",
    initial_sidebar_state="expanded",
)
inject_styles()
init_db()

# ---------------------------------------------------------------------------
# Auth guard
# ---------------------------------------------------------------------------
require_auth(page_id="analytics")
log_audit_event("PAGE_ACCESS", detail="analytics")

@st.cache_data(ttl=300, show_spinner=False)
def _get_filter_options():
    with get_session() as session:
        all_conditions = sorted({d.condition_name for d in session.query(Diagnosis).all()})
        all_drugs = sorted({t.drug_name for t in session.query(Treatment).all()})
        all_age_groups = sorted({p.age_group for p in session.query(Patient).all()})
    return all_conditions, all_drugs, all_age_groups


# ---------------------------------------------------------------------------
# Sidebar — navigation + filter controls
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
    st.markdown("**Filters**")

    # Pull distinct values for filter options (cached to avoid re-querying on every interaction)
    all_conditions, all_drugs, all_age_groups = _get_filter_options()

    sel_conditions = st.multiselect(
        "Condition",
        options=all_conditions,
        default=all_conditions,
        help="Filter by primary diagnosis",
    )
    sel_drugs = st.multiselect(
        "Drug",
        options=all_drugs,
        default=all_drugs,
        help="Filter by anti-VEGF agent",
    )
    sel_eyes = st.multiselect(
        "Eye",
        options=["OD", "OS", "OU"],
        default=["OD", "OS", "OU"],
    )
    sel_age_groups = st.multiselect(
        "Age group",
        options=all_age_groups,
        default=all_age_groups,
    )
    visit_min, visit_max = st.slider(
        "Visit range",
        min_value=1, max_value=20,
        value=(1, 12),
        step=1,
        help="Include only visits numbered within this range",
    )
    visit_range = (visit_min, visit_max)

    inj_min, inj_max = st.slider(
        "Injection number range",
        min_value=1, max_value=30,
        value=(1, 12),
        step=1,
        help="Cumulative injection count to include in the BCVA-by-injection chart",
    )
    injection_range = (inj_min, inj_max)

    st.markdown("---")
    st.caption("All patient data is anonymised at point of entry.")
    render_sidebar_user_info()
    render_sidebar_logout()

# Normalise empty multiselects → None (= "all"); tuples required for cache hashing
conditions  = tuple(sel_conditions)  or None
drugs       = tuple(sel_drugs)       or None
eyes        = tuple(sel_eyes)        or None
age_groups  = tuple(sel_age_groups)  or None

# ---------------------------------------------------------------------------
# Header
# ---------------------------------------------------------------------------
st.title("Analytics & Outcomes")

st.warning(
    "**Data Access Notice** — You are viewing anonymised, aggregate real-world evidence data. "
    "Counts below 5 are suppressed to prevent re-identification. "
    f"Access by **{get_username()}** ({get_role()}) is recorded in the audit log.",
)

st.markdown(
    "Population-level visualisations for the cohort matching the sidebar filters. "
    "All values are based on anonymised, synthetic RWE data."
)
st.markdown("---")

# ===========================================================================
# 1. BCVA Trajectory Over Time
# ===========================================================================
st.markdown("<div class='export-card'>", unsafe_allow_html=True)
st.subheader("BCVA Trajectory Over Time")
st.caption(
    "Mean ETDRS letter score by visit number. Shaded band = ±1 SD. "
    "Loading phase (visits 1–3) typically shows rapid gain; "
    "maintenance phase shows plateau or mild regression."
)

traj_df = get_bcva_trajectory_df(
    conditions=conditions,
    drugs=drugs,
    eyes=eyes,
    age_groups=age_groups,
    visit_range=visit_range,
)

condition_colors = px.colors.qualitative.Set2

if traj_df.empty:
    st.info("No data available for the selected filters.")
else:
    fig_traj = go.Figure()

    condition_list = traj_df["condition"].unique()

    for i, cond in enumerate(condition_list):
        cdf   = traj_df[traj_df["condition"] == cond].sort_values("visit_number")
        color = condition_colors[i % len(condition_colors)]

        # SD band
        fig_traj.add_trace(go.Scatter(
            x=pd.concat([cdf["visit_number"], cdf["visit_number"][::-1]]),
            y=pd.concat([
                cdf["mean_bcva"] + cdf["sd_bcva"],
                (cdf["mean_bcva"] - cdf["sd_bcva"])[::-1],
            ]),
            fill="toself",
            fillcolor=color,
            opacity=0.15,
            line=dict(width=0),
            showlegend=False,
            hoverinfo="skip",
            name=f"{cond} ±1 SD",
        ))

        # Mean line
        fig_traj.add_trace(go.Scatter(
            x=cdf["visit_number"],
            y=cdf["mean_bcva"],
            mode="lines+markers",
            name=cond,
            line=dict(color=color, width=2),
            marker=dict(size=5),
            hovertemplate=(
                "<b>%{fullData.name}</b><br>"
                "Visit %{x}<br>"
                "Mean BCVA: %{y:.1f} letters<br>"
                "<extra></extra>"
            ),
        ))

    fig_traj.add_vrect(
        x0=0.5, x1=3.5,
        fillcolor="lightgrey", opacity=0.25,
        annotation_text="Loading phase", annotation_position="top left",
        line_width=0,
    )
    fig_traj.update_layout(
        xaxis_title="Visit Number",
        yaxis_title="Mean BCVA (ETDRS letters)",
        legend_title="Condition",
        height=420,
        margin=dict(l=40, r=20, t=20, b=40),
        hovermode="x unified",
    )
    st.plotly_chart(fig_traj, use_container_width=True)
st.markdown("</div>", unsafe_allow_html=True)

st.markdown("---")

# ===========================================================================
# 1b. BCVA Over Injection Number
# ===========================================================================
st.markdown("<div class='export-card'>", unsafe_allow_html=True)
st.subheader("BCVA Over Injection Number")
st.caption(
    "Mean ETDRS letter score by cumulative injection count. Shaded band = ±1 SD. "
    "Reflects dose-response relationship independently of calendar time or visit spacing. "
    "Useful for comparing loading-phase response across drugs and conditions."
)

inj_df = get_bcva_by_injection_df(
    conditions=conditions,
    drugs=drugs,
    eyes=eyes,
    age_groups=age_groups,
    injection_range=injection_range,
)

if inj_df.empty:
    st.info("No data available for the selected filters (ensure injection numbers are recorded on treatments).")
else:
    fig_inj = go.Figure()

    inj_condition_list = inj_df["condition"].unique()

    for i, cond in enumerate(inj_condition_list):
        cdf   = inj_df[inj_df["condition"] == cond].sort_values("injection_number")
        color = condition_colors[i % len(condition_colors)]

        # SD band
        fig_inj.add_trace(go.Scatter(
            x=pd.concat([cdf["injection_number"], cdf["injection_number"][::-1]]),
            y=pd.concat([
                cdf["mean_bcva"] + cdf["sd_bcva"],
                (cdf["mean_bcva"] - cdf["sd_bcva"])[::-1],
            ]),
            fill="toself",
            fillcolor=color,
            opacity=0.15,
            line=dict(width=0),
            showlegend=False,
            hoverinfo="skip",
            name=f"{cond} ±1 SD",
        ))

        # Mean line
        fig_inj.add_trace(go.Scatter(
            x=cdf["injection_number"],
            y=cdf["mean_bcva"],
            mode="lines+markers",
            name=cond,
            line=dict(color=color, width=2),
            marker=dict(size=5),
            hovertemplate=(
                "<b>%{fullData.name}</b><br>"
                "Injection %{x}<br>"
                "Mean BCVA: %{y:.1f} letters<br>"
                "N eyes: %{customdata}<br>"
                "<extra></extra>"
            ),
            customdata=cdf["n"],
        ))

    # Loading phase annotation (injections 1–3 are canonical loading doses)
    fig_inj.add_vrect(
        x0=0.5, x1=3.5,
        fillcolor="lightgrey", opacity=0.25,
        annotation_text="Loading phase", annotation_position="top left",
        line_width=0,
    )
    fig_inj.update_layout(
        xaxis=dict(title="Cumulative Injection Number", dtick=1),
        yaxis_title="Mean BCVA (ETDRS letters)",
        legend_title="Condition",
        height=420,
        margin=dict(l=40, r=20, t=20, b=40),
        hovermode="x unified",
    )
    st.plotly_chart(fig_inj, use_container_width=True)

    # Summary metrics row
    overall_peak = inj_df.loc[inj_df["mean_bcva"].idxmax()]
    c1, c2, c3 = st.columns(3)
    c1.metric(
        "Peak mean BCVA",
        f"{overall_peak['mean_bcva']:.1f} letters",
        help="Highest mean BCVA across all conditions and injection numbers shown",
    )
    c2.metric(
        "At injection",
        f"#{int(overall_peak['injection_number'])}",
    )
    c3.metric(
        "Conditions shown",
        len(inj_condition_list),
    )
st.markdown("</div>", unsafe_allow_html=True)

st.markdown("---")

# ===========================================================================
# 2. Waterfall Plot — BCVA Change at Last Visit
# ===========================================================================
st.markdown("<div class='export-card'>", unsafe_allow_html=True)
st.subheader("Waterfall Plot — BCVA Change at Last Visit")
st.caption(
    "Each bar = one patient, sorted ascending by BCVA change (letters). "
    "Green = gained ≥ 0 letters; red = lost letters. "
    "Dashed lines mark clinically meaningful thresholds (+5 / +15 letters)."
)

wf_df = get_waterfall_df(
    conditions=conditions,
    drugs=drugs,
    eyes=eyes,
    age_groups=age_groups,
)

if wf_df.empty:
    st.info("No data available for the selected filters.")
else:
    fig_wf = go.Figure()

    bar_colors = ["#2ecc71" if g else "#e74c3c" for g in wf_df["gainer"]]

    fig_wf.add_trace(go.Bar(
        x=list(range(len(wf_df))),
        y=wf_df["bcva_change"],
        marker_color=bar_colors,
        hovertemplate=(
            "<b>%{customdata[0]}</b><br>"
            "Condition: %{customdata[1]}<br>"
            "BCVA change: %{y:+.1f} letters<br>"
            "<extra></extra>"
        ),
        customdata=wf_df[["patient_token", "condition"]].values,
    ))

    # Clinical reference lines
    for threshold, label in [(5, "+5 letters"), (15, "+15 letters")]:
        fig_wf.add_hline(
            y=threshold,
            line_dash="dash",
            line_color="grey",
            annotation_text=label,
            annotation_position="right",
        )
    fig_wf.add_hline(y=0, line_color="black", line_width=1)

    n_gainers    = wf_df["gainer"].sum()
    n_losers     = (~wf_df["gainer"]).sum()
    pct_15_gain  = round(100 * (wf_df["bcva_change"] >= 15).sum() / len(wf_df), 1)

    fig_wf.update_layout(
        xaxis=dict(title="Patient (sorted by BCVA change)", showticklabels=False),
        yaxis_title="BCVA Change from Baseline (letters)",
        height=420,
        margin=dict(l=40, r=20, t=20, b=40),
    )
    st.plotly_chart(fig_wf, use_container_width=True)

    c1, c2, c3 = st.columns(3)
    c1.metric("Gainers (≥ 0 letters)", f"{n_gainers} / {len(wf_df)}")
    c2.metric("Losers (< 0 letters)",  f"{n_losers} / {len(wf_df)}")
    c3.metric("Gained ≥ 15 letters",   f"{pct_15_gain}%")
st.markdown("</div>", unsafe_allow_html=True)

st.markdown("---")

# ===========================================================================
# 3. IRF / SRF Resolution Over Visits
# ===========================================================================
st.markdown("<div class='export-card'>", unsafe_allow_html=True)
st.subheader("IRF / SRF Fluid Resolution Over Visits")
st.caption(
    "Percentage of eyes with intraretinal fluid (IRF) and subretinal fluid (SRF) "
    "at each visit number. Declining rates demonstrate the anatomical drying effect "
    "of anti-VEGF loading."
)

fluid_df = get_fluid_prevalence_df(
    conditions=conditions,
    drugs=drugs,
    eyes=eyes,
    age_groups=age_groups,
    visit_range=visit_range,
)

if fluid_df.empty:
    st.info("No data available for the selected filters.")
else:
    fig_fluid = go.Figure()

    fig_fluid.add_trace(go.Scatter(
        x=fluid_df["visit_number"],
        y=fluid_df["pct_irf"],
        mode="lines+markers",
        name="IRF present",
        line=dict(color="#e74c3c", width=2),
        marker=dict(size=5),
        hovertemplate="Visit %{x}<br>IRF: %{y:.1f}%<extra></extra>",
    ))
    fig_fluid.add_trace(go.Scatter(
        x=fluid_df["visit_number"],
        y=fluid_df["pct_srf"],
        mode="lines+markers",
        name="SRF present",
        line=dict(color="#3498db", width=2),
        marker=dict(size=5),
        hovertemplate="Visit %{x}<br>SRF: %{y:.1f}%<extra></extra>",
    ))
    fig_fluid.add_vrect(
        x0=0.5, x1=3.5,
        fillcolor="lightgrey", opacity=0.20,
        annotation_text="Loading", annotation_position="top left",
        line_width=0,
    )
    fig_fluid.update_layout(
        xaxis_title="Visit Number",
        yaxis=dict(title="% of Eyes with Fluid Present", range=[0, 105]),
        legend_title="Fluid Type",
        height=400,
        margin=dict(l=40, r=20, t=20, b=40),
        hovermode="x unified",
    )
    st.plotly_chart(fig_fluid, use_container_width=True)
st.markdown("</div>", unsafe_allow_html=True)

st.markdown("---")

# ===========================================================================
# 4. Injection Interval Distribution
# ===========================================================================
st.markdown("<div class='export-card'>", unsafe_allow_html=True)
st.subheader("Injection Interval Distribution (Maintenance Phase)")
st.caption(
    "Days between consecutive visits for maintenance-phase injections (visit ≥ 4). "
    "Proxy for real-world treat-and-extend and PRN dosing behaviour."
)

interval_df = get_injection_interval_df(
    conditions=conditions,
    drugs=drugs,
    eyes=eyes,
    age_groups=age_groups,
)

if interval_df.empty:
    st.info("No data available for the selected filters.")
else:
    median_interval = interval_df["interval_days"].median()

    col_hist, col_stats = st.columns([3, 1])

    with col_hist:
        fig_hist = go.Figure()
        fig_hist.add_trace(go.Histogram(
            x=interval_df["interval_days"],
            nbinsx=40,
            marker_color="#5b7fbb",
            opacity=0.8,
            name="Injection interval",
            hovertemplate="Interval: %{x} days<br>Count: %{y}<extra></extra>",
        ))
        fig_hist.add_vline(
            x=median_interval,
            line_dash="dash",
            line_color="#e67e22",
            annotation_text=f"Median: {median_interval:.0f} d",
            annotation_position="top right",
        )
        fig_hist.update_layout(
            xaxis_title="Days Between Visits",
            yaxis_title="Count",
            height=380,
            margin=dict(l=40, r=20, t=20, b=40),
            showlegend=False,
        )
        st.plotly_chart(fig_hist, use_container_width=True)

    with col_stats:
        st.markdown("**Interval Stats**")
        st.metric("Median", f"{median_interval:.0f} days")
        st.metric("Mean",   f"{interval_df['interval_days'].mean():.0f} days")
        st.metric("P25",    f"{interval_df['interval_days'].quantile(0.25):.0f} days")
        st.metric("P75",    f"{interval_df['interval_days'].quantile(0.75):.0f} days")
        st.metric("N intervals", len(interval_df))

        if len(interval_df) > 0:
            visit_type_counts = interval_df["visit_type"].value_counts()
            st.markdown("**By visit type**")
            for vtype, cnt in visit_type_counts.items():
                pct = round(100 * cnt / len(interval_df), 1)
                st.markdown(f"- {vtype}: {cnt} ({pct}%)")
st.markdown("</div>", unsafe_allow_html=True)

st.markdown("---")

# ===========================================================================
# 5. Adverse Event Summary
# ===========================================================================
st.markdown("<div class='export-card'>", unsafe_allow_html=True)
st.subheader("Adverse Event Summary")
st.caption(
    "Incidence of each adverse event type across the cohort. "
    "Incidence rate calculated per 1,000 injections."
)

ae_counts, sae_detail = get_ae_summary_df(
    conditions=conditions,
    drugs=drugs,
    eyes=eyes,
    age_groups=age_groups,
)


if ae_counts.empty:
    st.info("No adverse events recorded for the selected filters.")
else:
    col_ae_chart, col_ae_tbl = st.columns([3, 2])

    with col_ae_chart:
        # Horizontal bar chart
        ae_plot = ae_counts.sort_values("count").copy()
        ae_plot["ae_label"] = ae_plot["ae_classification"] + " — " + ae_plot["ae_category"]
        bar_colors_ae = [
            "#e74c3c" if cat == "Ocular" else "#9b59b6"
            for cat in ae_plot["ae_category"]
        ]

        fig_ae = go.Figure(go.Bar(
            x=ae_plot["count"],
            y=ae_plot["ae_label"],
            orientation="h",
            marker_color=bar_colors_ae,
            hovertemplate=(
                "<b>%{y}</b><br>"
                "Count: %{x}<br>"
                "Incidence: %{customdata:.2f} / 1,000 injections<br>"
                "<extra></extra>"
            ),
            customdata=ae_plot["incidence_per_1000"],
        ))

        # Legend patch via invisible scatter traces
        for cat, col in [("Ocular", "#e74c3c"), ("Systemic", "#9b59b6")]:
            fig_ae.add_trace(go.Scatter(
                x=[None], y=[None],
                mode="markers",
                marker=dict(size=10, color=col, symbol="square"),
                name=cat,
                showlegend=True,
            ))

        fig_ae.update_layout(
            xaxis_title="Number of Events",
            yaxis_title="",
            legend_title="Category",
            height=max(300, len(ae_counts) * 30 + 60),
            margin=dict(l=220, r=20, t=20, b=40),
        )
        st.plotly_chart(fig_ae, use_container_width=True)

    with col_ae_tbl:
        st.markdown("**Incidence rate per 1,000 injections**")
        display_counts = ae_counts[
            ["ae_classification", "ae_category", "count", "incidence_per_1000"]
        ].copy()
        # Apply small-cell suppression before display
        display_counts = small_cell_suppress(display_counts, count_columns=["count"])
        display_counts.columns = ["Classification", "Category", "N", "Rate/1k inj"]
        st.dataframe(display_counts.style.hide(axis="index"), use_container_width=True)

    # SAE table
    if not sae_detail.empty:
        st.markdown("**Serious Adverse Events (SAE) — severity grade breakdown**")
        sae_display = sae_detail[
            ["ae_classification", "ae_category", "severity_grade", "count"]
        ].copy()
        # Apply small-cell suppression before display
        sae_display = small_cell_suppress(sae_display, count_columns=["count"])
        sae_display.columns = ["Classification", "Category", "CTCAE Grade", "Count"]
        # Only style numeric grade cells (suppressed cells are strings)
        numeric_grade_mask = sae_display["CTCAE Grade"].apply(lambda v: isinstance(v, (int, float)))
        if numeric_grade_mask.any():
            sae_display.loc[numeric_grade_mask, "CTCAE Grade"] = (
                sae_display.loc[numeric_grade_mask, "CTCAE Grade"].astype(int)
            )
        st.dataframe(
            sae_display.style.map(
                lambda v: "color: #e74c3c; font-weight: bold" if isinstance(v, int) and v >= 4 else "",
                subset=["CTCAE Grade"],
            ).hide(axis="index"),
            use_container_width=True,
        )
    else:
        st.success("No serious adverse events in this filtered cohort.")

    # --- CTCAE grade distribution stacked bar ---
    grade_df = get_ae_grade_distribution_df(
        conditions=conditions,
        drugs=drugs,
        eyes=eyes,
        age_groups=age_groups,
    )

    if not grade_df.empty:
        st.markdown("**CTCAE grade distribution by AE type**")

        # CTCAE grade colour scale: green→yellow→orange→red→dark-red
        _GRADE_COLORS = {
            1: "#2ecc71",
            2: "#f1c40f",
            3: "#e67e22",
            4: "#e74c3c",
            5: "#7b241c",
        }
        _GRADE_LABELS = {
            1: "G1 Mild",
            2: "G2 Moderate",
            3: "G3 Severe",
            4: "G4 Life-threatening",
            5: "G5 Death",
        }

        fig_grade = go.Figure()
        all_classifications = grade_df["ae_classification"].unique()

        for grade in range(1, 6):
            gdf = grade_df[grade_df["severity_grade"] == grade]
            # Align to full classification list so stacking is consistent
            counts = (
                gdf.groupby("ae_classification")["count"].sum()
                .reindex(all_classifications, fill_value=0)
            )
            fig_grade.add_trace(go.Bar(
                name=_GRADE_LABELS[grade],
                x=all_classifications,
                y=counts.values,
                marker_color=_GRADE_COLORS[grade],
                hovertemplate=(
                    "<b>%{x}</b><br>"
                    f"CTCAE {_GRADE_LABELS[grade]}<br>"
                    "Count: %{y}<br>"
                    "<extra></extra>"
                ),
            ))

        n_bars = len(all_classifications)
        grade_chart_width = max(400, min(n_bars * 120 + 200, 900))
        fig_grade.update_layout(
            barmode="stack",
            xaxis_title="AE Classification",
            yaxis_title="Count",
            legend_title="CTCAE Grade",
            height=400,
            width=grade_chart_width,
            margin=dict(l=60, r=20, t=20, b=100),
            xaxis=dict(tickangle=-30),
            bargap=0.4,
        )
        st.plotly_chart(fig_grade, use_container_width=False)

st.markdown("</div>", unsafe_allow_html=True)

st.markdown("---")

# ===========================================================================
# 6. Drug Cohort Comparison — mean BCVA trajectory
# ===========================================================================
st.markdown("<div class='export-card'>", unsafe_allow_html=True)
st.subheader("Drug Cohort Comparison — Mean BCVA Trajectory")
st.caption(
    "Head-to-head mean ETDRS letter score by visit number for two selected drugs. "
    "Shaded bands = ±1 SD. Both cohorts are filtered by the sidebar conditions, "
    "eyes, and age groups. Use this to compare loading-phase gain and maintenance "
    "plateau between agents (e.g. Aflibercept vs Faricimab)."
)

# Drug selector — outside the cached call so it reacts to user input
_all_drugs_for_comparison = list(drugs) if drugs else []
if not _all_drugs_for_comparison:
    with get_session() as _s:
        from database.models import Treatment as _T
        _all_drugs_for_comparison = sorted({t.drug_name for t in _s.query(_T).all()})

if len(_all_drugs_for_comparison) < 2:
    st.info("At least two distinct drugs must be present in the filtered cohort to run a comparison.")
else:
    col_da, col_db = st.columns(2)
    with col_da:
        drug_a = st.selectbox(
            "Drug A",
            options=_all_drugs_for_comparison,
            index=0,
            key="cohort_drug_a",
        )
    with col_db:
        # Default Drug B to second option; guard if list has only 1 item
        default_b_idx = 1 if len(_all_drugs_for_comparison) > 1 else 0
        drug_b = st.selectbox(
            "Drug B",
            options=_all_drugs_for_comparison,
            index=default_b_idx,
            key="cohort_drug_b",
        )

    if drug_a == drug_b:
        st.warning("Select two different drugs to compare.")
    else:
        comp_df = get_bcva_cohort_comparison_df(
            drug_a=drug_a,
            drug_b=drug_b,
            conditions=conditions,
            eyes=eyes,
            age_groups=age_groups,
            visit_range=visit_range,
        )

        if comp_df.empty:
            st.info("No BCVA data found for either drug under the current filters.")
        else:
            # Two-colour palette: Drug A = teal, Drug B = coral
            _COHORT_COLORS = {drug_a: "#16a085", drug_b: "#c0392b"}

            fig_comp = go.Figure()

            for drug in comp_df["drug"].unique():
                cdf   = comp_df[comp_df["drug"] == drug].sort_values("visit_number")
                color = _COHORT_COLORS.get(drug, "#555555")

                # ±1 SD band
                fig_comp.add_trace(go.Scatter(
                    x=pd.concat([cdf["visit_number"], cdf["visit_number"][::-1]]),
                    y=pd.concat([
                        cdf["mean_bcva"] + cdf["sd_bcva"],
                        (cdf["mean_bcva"] - cdf["sd_bcva"])[::-1],
                    ]),
                    fill="toself",
                    fillcolor=color,
                    opacity=0.12,
                    line=dict(width=0),
                    showlegend=False,
                    hoverinfo="skip",
                ))

                # Mean line
                fig_comp.add_trace(go.Scatter(
                    x=cdf["visit_number"],
                    y=cdf["mean_bcva"],
                    mode="lines+markers",
                    name=drug,
                    line=dict(color=color, width=2.5),
                    marker=dict(size=6),
                    hovertemplate=(
                        f"<b>{drug}</b><br>"
                        "Visit %{x}<br>"
                        "Mean BCVA: %{y:.1f} letters<br>"
                        "N eyes: %{customdata}<br>"
                        "<extra></extra>"
                    ),
                    customdata=cdf["n"],
                ))

            fig_comp.add_vrect(
                x0=0.5, x1=3.5,
                fillcolor="lightgrey", opacity=0.20,
                annotation_text="Loading phase", annotation_position="top left",
                line_width=0,
            )
            fig_comp.update_layout(
                xaxis=dict(title="Visit Number", dtick=1),
                yaxis_title="Mean BCVA (ETDRS letters)",
                legend_title="Drug",
                height=440,
                margin=dict(l=40, r=20, t=20, b=40),
                hovermode="x unified",
            )
            st.plotly_chart(fig_comp, use_container_width=True)

            # Summary metrics — final common visit
            max_shared_visit = (
                comp_df.groupby("drug")["visit_number"].max().min()
            )
            final = comp_df[comp_df["visit_number"] == max_shared_visit]

            if len(final) == 2:
                row_a = final[final["drug"] == drug_a].iloc[0]
                row_b = final[final["drug"] == drug_b].iloc[0]
                diff  = round(row_a["mean_bcva"] - row_b["mean_bcva"], 1)

                mc1, mc2, mc3 = st.columns(3)
                mc1.metric(
                    f"{drug_a} — visit {int(max_shared_visit)}",
                    f"{row_a['mean_bcva']:.1f} letters",
                    help=f"N = {int(row_a['n'])} eyes",
                )
                mc2.metric(
                    f"{drug_b} — visit {int(max_shared_visit)}",
                    f"{row_b['mean_bcva']:.1f} letters",
                    help=f"N = {int(row_b['n'])} eyes",
                )
                mc3.metric(
                    "Difference (A − B)",
                    f"{diff:+.1f} letters",
                    help="Unadjusted mean difference at the last common visit. "
                         "No statistical testing — interpret with caution.",
                )
                st.caption(
                    "Unadjusted comparison. Cohorts may differ in baseline BCVA, "
                    "disease duration, and treatment protocol. "
                    "Formal inference requires covariate adjustment."
                )

st.markdown("</div>", unsafe_allow_html=True)
