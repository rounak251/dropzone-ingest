"""Drop the tables this project owns, so an e2e or drill run starts empty. Dev database only."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dropzone import db  # noqa: E402


def reset(url: str | None = None) -> None:
    conn = db.connect(db.database_url(url))
    try:
        with conn.transaction():
            conn.execute("DROP TABLE IF EXISTS quarantine, ledger, target, files CASCADE")
    finally:
        conn.close()


if __name__ == "__main__":
    reset()
    print("dropped tables: quarantine, ledger, target, files")
