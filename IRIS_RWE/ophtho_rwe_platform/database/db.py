"""
Database engine, session factory, and initialisation helpers.
Uses SQLite for portability; swap DATABASE_URL for PostgreSQL in production.
"""

import os
from contextlib import contextmanager

from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from database.models import Base

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
_BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATABASE_URL = os.getenv("DATABASE_URL", f"sqlite:///{os.path.join(_BASE_DIR, 'ophtho_rwe.db')}")

# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------
engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {},
    echo=False,
)

# Enable WAL mode for SQLite — better concurrent read performance
if DATABASE_URL.startswith("sqlite"):
    @event.listens_for(engine, "connect")
    def set_sqlite_pragma(dbapi_conn, _connection_record):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

# ---------------------------------------------------------------------------
# Session factory
# ---------------------------------------------------------------------------
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


@contextmanager
def get_session():
    """Context manager that yields a DB session and handles commit/rollback."""
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


# ---------------------------------------------------------------------------
# Initialisation
# ---------------------------------------------------------------------------
def init_db():
    """Create all tables if they don't already exist, then run migrations."""
    Base.metadata.create_all(bind=engine)
    _migrate()


def _migrate():
    """
    Additive schema migrations for SQLite.

    SQLite does not support ALTER TABLE … ADD COLUMN IF NOT EXISTS, so we
    inspect the current column list and only issue the DDL when the column
    is absent.  Each block is idempotent — safe to run on every startup.
    """
    with engine.connect() as conn:
        # -- adverse_events.ae_classification (added in v0.2) ---------------
        result = conn.execute(
            __import__("sqlalchemy").text("PRAGMA table_info(adverse_events)")
        )
        existing_cols = {row[1] for row in result}

        if "ae_classification" not in existing_cols:
            conn.execute(
                __import__("sqlalchemy").text(
                    "ALTER TABLE adverse_events "
                    "ADD COLUMN ae_classification VARCHAR(60) NOT NULL DEFAULT 'Other'"
                )
            )
            conn.commit()

        # -- diagnoses.condition_code (added in v0.3) ------------------------
        result = conn.execute(
            __import__("sqlalchemy").text("PRAGMA table_info(diagnoses)")
        )
        diag_cols = {row[1] for row in result}

        if "condition_code" not in diag_cols:
            conn.execute(
                __import__("sqlalchemy").text(
                    "ALTER TABLE diagnoses "
                    "ADD COLUMN condition_code VARCHAR(10)"
                )
            )
            # Back-fill from existing icd10_code for all current rows
            conn.execute(
                __import__("sqlalchemy").text(
                    "UPDATE diagnoses SET condition_code = icd10_code "
                    "WHERE condition_code IS NULL"
                )
            )
            conn.commit()


def drop_all():
    """Drop all tables — use only in tests or dev resets."""
    Base.metadata.drop_all(bind=engine)
