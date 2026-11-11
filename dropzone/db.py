"""Connection, schema and the single-writer lock."""

import os
from pathlib import Path

import psycopg

from dropzone.contract import DropError

DEFAULT_URL = "postgresql://dropzone:dropzone@127.0.0.1:55432/dropzone"
# Any fixed number works; it only has to be the same in every ingest process.
LOCK_KEY = 5520190
SCHEMA_SQL = (Path(__file__).parent / "schema.sql").read_text()


def database_url(override: str | None = None) -> str:
    return override or os.environ.get("DZ_DATABASE_URL", DEFAULT_URL)


def connect(url: str) -> psycopg.Connection:
    # Autocommit keeps the advisory lock out of any open transaction. Batches
    # open their own transaction with conn.transaction().
    return psycopg.connect(url, autocommit=True)


def ensure_schema(conn: psycopg.Connection) -> None:
    with conn.transaction():
        conn.execute(SCHEMA_SQL)


REQUIRED_TABLES = ("files", "ledger", "target", "quarantine")


def require_schema(conn: psycopg.Connection) -> None:
    """Reports and verify need the tables. Without them they would fail with a traceback."""
    missing = [name for name in REQUIRED_TABLES
               if conn.execute("SELECT to_regclass(%s)", (name,)).fetchone()[0] is None]
    if missing:
        raise DropError(f"database has no dropzone tables ({', '.join(missing)}); run dropzone ingest first")


def take_single_writer_lock(conn: psycopg.Connection) -> None:
    """Refuse to run if another ingest holds the lock. The lock ends with the connection."""
    if not conn.execute("SELECT pg_try_advisory_lock(%s)", (LOCK_KEY,)).fetchone()[0]:
        raise DropError("another ingest process holds the lock")
