"""
Page 0 — Login

Standard minimal login page:
  - Centered card layout (no sidebar chrome)
  - Demo credentials shown below the login form
  - bcrypt authentication with rate-limiting / lockout
  - Audit logging on every attempt
"""

import time

import streamlit as st

from auth.auth import (
    is_authenticated,
    log_audit_event,
    login,
    verify_password,
)
from database.db import init_db
from components.styles import inject_styles

# ---------------------------------------------------------------------------
# Page config
# ---------------------------------------------------------------------------
st.set_page_config(
    page_title="Login — Ophthalmology RWE Platform",
    layout="centered",
    initial_sidebar_state="collapsed",
)

inject_styles()
init_db()

# ---------------------------------------------------------------------------
# If already authenticated, redirect to dashboard
# ---------------------------------------------------------------------------
if is_authenticated():
    try:
        st.switch_page("app.py")
    except Exception:
        st.success("You are already logged in. Navigate using the sidebar.")
    st.stop()

# ---------------------------------------------------------------------------
# Minimal sidebar (no nav, no logo)
# ---------------------------------------------------------------------------
with st.sidebar:
    st.markdown("**Ophthalmology RWE Platform**")
    st.caption("Please log in to access the platform.")

# ---------------------------------------------------------------------------
# Rate-limit state
# ---------------------------------------------------------------------------
if "login_attempts" not in st.session_state:
    st.session_state["login_attempts"] = 0
if "lockout_until" not in st.session_state:
    st.session_state["lockout_until"] = 0

_MAX_ATTEMPTS    = 5
_LOCKOUT_SECONDS = 60

lockout_remaining = st.session_state["lockout_until"] - time.time()
if lockout_remaining > 0:
    st.error(
        f"Too many failed attempts. Locked for {int(lockout_remaining)} more seconds."
    )
    st.stop()

# ---------------------------------------------------------------------------
# Login card
# ---------------------------------------------------------------------------
st.markdown('<div class="login-card">', unsafe_allow_html=True)

st.markdown(
    "<h2 style='text-align:center;margin-bottom:4px;font-size:1.5rem;"
    "font-weight:700;'>Ophthalmology RWE Platform</h2>",
    unsafe_allow_html=True,
)
st.markdown(
    "<p style='text-align:center;color:#64748B;font-size:0.88rem;"
    "margin-bottom:24px;'>Sign in to continue. All access is audit-logged.</p>",
    unsafe_allow_html=True,
)

with st.form("login_form", clear_on_submit=False):
    username  = st.text_input("Username", placeholder="e.g. clinician")
    password  = st.text_input("Password", type="password", placeholder="••••••••••••")
    submitted = st.form_submit_button("Sign in", type="primary", use_container_width=True)

if submitted:
    if not username or not password:
        st.error("Please enter both username and password.")
    else:
        success, role = verify_password(username.strip().lower(), password)

        if success:
            login(username.strip().lower(), role)
            st.session_state["login_attempts"] = 0
            log_audit_event("LOGIN_SUCCESS", detail=f"user={username} role={role}")
            st.success(f"Welcome, {username}. Redirecting…")
            time.sleep(0.4)
            try:
                st.switch_page("app.py")
            except Exception:
                st.rerun()
        else:
            st.session_state["login_attempts"] += 1
            log_audit_event(
                "LOGIN_FAIL",
                detail=f"user={username} attempt={st.session_state['login_attempts']}",
            )
            remaining = _MAX_ATTEMPTS - st.session_state["login_attempts"]
            if remaining <= 0:
                st.session_state["lockout_until"] = time.time() + _LOCKOUT_SECONDS
                st.error(f"Too many failed attempts. Locked for {_LOCKOUT_SECONDS} seconds.")
            else:
                st.error(
                    f"Invalid username or password. "
                    f"{remaining} attempt(s) remaining before lockout."
                )

st.markdown('</div>', unsafe_allow_html=True)

st.markdown(
    """
    <div style='text-align: center; margin-top: 20px;'>
        <p style='margin-bottom: 10px;'><b>Demo credentials</b> (prototype — change before deployment)</p>
        <table style='margin: 0 auto; border-collapse: collapse; text-align: left; font-size: 0.9em;'>
            <thead>
                <tr style='border-bottom: 1px solid rgba(128,128,128,0.5);'>
                    <th style='padding: 8px 16px;'>Username</th>
                    <th style='padding: 8px 16px;'>Password</th>
                    <th style='padding: 8px 16px;'>Role</th>
                </tr>
            </thead>
            <tbody>
                <tr style='border-bottom: 1px solid rgba(128,128,128,0.2);'>
                    <td style='padding: 8px 16px;'><code>admin</code></td>
                    <td style='padding: 8px 16px;'><code>Admin@IRIS2024!</code></td>
                    <td style='padding: 8px 16px;'>Admin — all pages</td>
                </tr>
                <tr style='border-bottom: 1px solid rgba(128,128,128,0.2);'>
                    <td style='padding: 8px 16px;'><code>clinician</code></td>
                    <td style='padding: 8px 16px;'><code>Clinic@IRIS2024!</code></td>
                    <td style='padding: 8px 16px;'>Patient & Visit entry</td>
                </tr>
                <tr>
                    <td style='padding: 8px 16px;'><code>analyst</code></td>
                    <td style='padding: 8px 16px;'><code>Analyst@IRIS2024!</code></td>
                    <td style='padding: 8px 16px;'>Analytics & Export</td>
                </tr>
            </tbody>
        </table>
    </div>
    """,
    unsafe_allow_html=True,
)

st.markdown(
    "<p style='text-align:center;color:#94A3B8;font-size:0.76rem;margin-top:20px;'>"
    "Sessions expire after 30 minutes of inactivity. "
    "Passwords stored as bcrypt-12 hashes."
    "</p>",
    unsafe_allow_html=True,
)
