"""
Authentication, session management, and role-based access control.

Design:
  - Credentials stored as bcrypt-12 hashes in config/users.json (never plaintext).
  - Three roles:  admin     → all pages + export + DB reset
                  clinician → pages 1 & 2 (data entry) only
                  analyst   → pages 3 & 4 read-only, no raw-data export
  - Session state keys:
        authenticated   bool
        username        str
        role            str
        last_active     float  (time.time())
  - Session times out after SESSION_TIMEOUT_SECONDS of inactivity.
  - Every login attempt (success or fail) and every page access / export
    is written to the audit_log table via log_audit_event().
"""

from __future__ import annotations

import json
import os
import time
from datetime import datetime

import bcrypt
import streamlit as st

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SESSION_TIMEOUT_SECONDS = 30 * 60  # 30 minutes inactivity

_USERS_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "config", "users.json",
)

# Role permission map
# Keys are roles; values are sets of allowed page identifiers.
# "all" means every page is permitted.
ROLE_PAGES: dict[str, set[str] | str] = {
    "admin":     "all",
    "clinician": {"home", "patient_entry", "visit_entry"},
    "analyst":   {"home", "analytics", "data_export"},
}

# ---------------------------------------------------------------------------
# User store
# ---------------------------------------------------------------------------

def _load_users() -> dict:
    with open(_USERS_PATH, "r", encoding="utf-8") as fh:
        return json.load(fh)


def verify_password(username: str, password: str) -> tuple[bool, str | None]:
    """
    Check username + password against the hashed store.
    Returns (True, role) on success, (False, None) on failure.
    Constant-time comparison via bcrypt prevents timing attacks.
    """
    users = _load_users()
    entry = users.get(username)
    if not entry:
        # Run bcrypt anyway to prevent username-enumeration via timing
        bcrypt.checkpw(b"dummy", bcrypt.hashpw(b"dummy", bcrypt.gensalt(4)))
        return False, None

    stored_hash = entry["password_hash"].encode("utf-8")
    if bcrypt.checkpw(password.encode("utf-8"), stored_hash):
        return True, entry["role"]
    return False, None


# ---------------------------------------------------------------------------
# Session helpers
# ---------------------------------------------------------------------------

def is_authenticated() -> bool:
    """Return True only if session is authenticated AND has not timed out."""
    if not st.session_state.get("authenticated", False):
        return False
    last_active = st.session_state.get("last_active", 0)
    if time.time() - last_active > SESSION_TIMEOUT_SECONDS:
        logout()
        return False
    # Refresh activity timestamp on every check
    st.session_state["last_active"] = time.time()
    return True


def get_role() -> str | None:
    return st.session_state.get("role")


def get_username() -> str | None:
    return st.session_state.get("username")


def login(username: str, role: str) -> None:
    st.session_state["authenticated"] = True
    st.session_state["username"]      = username
    st.session_state["role"]          = role
    st.session_state["last_active"]   = time.time()


def logout() -> None:
    for key in ("authenticated", "username", "role", "last_active"):
        st.session_state.pop(key, None)


# ---------------------------------------------------------------------------
# Role checks
# ---------------------------------------------------------------------------

def has_page_access(page_id: str) -> bool:
    """
    page_id — one of: home, patient_entry, visit_entry, analytics, data_export
    """
    role = get_role()
    if not role:
        return False
    allowed = ROLE_PAGES.get(role, set())
    if allowed == "all":
        return True
    return page_id in allowed


def can_export_raw() -> bool:
    """Analyst may not download raw patient-level / visit-level CSVs."""
    return get_role() in ("admin",)


def can_reset_db() -> bool:
    return get_role() == "admin"


# ---------------------------------------------------------------------------
# Streamlit page guard — call at the top of every protected page
# ---------------------------------------------------------------------------

def require_auth(page_id: str | None = None) -> None:
    """
    Blocks the page if the user is not authenticated or lacks role access.
    Redirects to 00_Login.py via st.switch_page if available (Streamlit ≥1.31),
    otherwise shows an error and calls st.stop().

    Call this as the FIRST statement after st.set_page_config().
    """
    if not is_authenticated():
        st.error("You must log in to access this page.")
        st.info("Please sign in via the Login page.")
        _try_redirect_login()
        st.stop()

    if page_id and not has_page_access(page_id):
        role = get_role()
        st.error(f"Access denied. Your role (**{role}**) does not have permission for this page.")
        st.stop()


def _try_redirect_login() -> None:
    try:
        st.switch_page("pages/00_Login.py")
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Audit logging
# ---------------------------------------------------------------------------

def log_audit_event(
    action: str,
    detail: str = "",
    record_count: int | None = None,
) -> None:
    """
    Write an audit record to the audit_log DB table.
    Silently swallows any DB error so auth failures don't cascade.

    action examples:
        LOGIN_SUCCESS, LOGIN_FAIL, LOGOUT, PAGE_ACCESS,
        EXPORT_PATIENT_CSV, EXPORT_VISIT_CSV, EXPORT_PDF,
        DB_RESET
    """
    try:
        from database.db import get_session
        from database.models import AuditLog
        with get_session() as session:
            entry = AuditLog(
                username=get_username() or "anonymous",
                action=action,
                detail=detail[:500] if detail else "",
                record_count=record_count,
                timestamp=datetime.utcnow(),
            )
            session.add(entry)
    except Exception:
        pass  # Never let audit failure break the app


# ---------------------------------------------------------------------------
# Sidebar logout widget — call inside `with st.sidebar:` on every page
# ---------------------------------------------------------------------------

def render_sidebar_user_info() -> None:
    """Render current user badge in the sidebar (call near top).
    Logout button is rendered separately via render_sidebar_logout() at the bottom."""
    username = get_username()
    role     = get_role()
    if not username:
        return

    role_label = {
        "admin":     "Admin",
        "clinician": "Clinician",
        "analyst":   "Analyst",
    }.get(role, role or "")

    st.sidebar.markdown(
        f'<div class="user-badge">'
        f'<span style="color:#94BFDE;font-size:0.75rem;font-weight:600;'
        f'text-transform:uppercase;letter-spacing:0.06em;">Logged in as</span><br>'
        f'<span style="color:#FFFFFF;font-weight:700;font-size:0.95rem;">{username}</span>'
        f'<span style="color:#94BFDE;font-size:0.78rem;margin-left:8px;">{role_label}</span>'
        f'</div>',
        unsafe_allow_html=True,
    )


def render_sidebar_logout() -> None:
    """Render the logout button — call at the very bottom of the sidebar block."""
    username = get_username()
    if not username:
        return

    st.sidebar.markdown("---")
    if st.sidebar.button("Log out", key="__sidebar_logout__", use_container_width=True):
        log_audit_event("LOGOUT", f"user={username}")
        logout()
        _try_redirect_login()
        st.rerun()
