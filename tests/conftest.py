import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from reset_db import reset  # noqa: E402
from dropzone import db  # noqa: E402


@pytest.fixture
def conn():
    """An empty database with the schema in place. Tests share one dev database, so each one resets it."""
    reset()
    connection = db.connect(db.database_url())
    db.ensure_schema(connection)
    yield connection
    connection.close()


@pytest.fixture
def empty_db():
    reset()
    yield
