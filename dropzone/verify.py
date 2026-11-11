"""Integrity checks over the tables, and a sorted dump used to compare two runs."""

import psycopg

from dropzone.report import build_report

TARGET_COLS = ("natural_key", "customer_id", "sku", "quantity", "unit_price_cents",
               "order_date", "status", "payload_hash", "source_file_hash", "source_row_no")


def run_checks(conn: psycopg.Connection) -> list[tuple[str, bool, str]]:
    def scalar(sql: str) -> int:
        return conn.execute(sql).fetchone()[0]

    duplicate_keys = scalar(
        "SELECT count(*) FROM (SELECT natural_key FROM target GROUP BY 1 HAVING count(*) > 1) d")
    unmatched_applied = scalar(
        "SELECT count(*) FROM ledger l WHERE l.disposition = 'applied' AND NOT EXISTS ("
        " SELECT 1 FROM target t WHERE t.natural_key = l.natural_key"
        " AND t.source_file_hash = l.file_hash AND t.source_row_no = l.row_no)")
    orphan_targets = scalar(
        "SELECT count(*) FROM target t WHERE NOT EXISTS ("
        " SELECT 1 FROM ledger l WHERE l.disposition = 'applied'"
        " AND l.natural_key = t.natural_key AND l.file_hash = t.source_file_hash"
        " AND l.row_no = t.source_row_no)")
    incomplete = scalar("SELECT count(*) FROM files WHERE status <> 'complete'")
    recorded = scalar("SELECT count(*) FROM files")
    unreconciled = [f["file_name"] for f in build_report(conn)["files"] if not f["reconciled"]]
    # The checks above all pass on empty tables, so an empty database must fail here.
    return [
        ("at least one drop is recorded", recorded > 0, f"files={recorded}"),
        ("no natural key appears twice in target", duplicate_keys == 0, f"duplicate keys={duplicate_keys}"),
        ("every applied ledger row has its target row", unmatched_applied == 0,
         f"unmatched applied={unmatched_applied}"),
        ("every target row has an applied ledger row", orphan_targets == 0,
         f"orphan targets={orphan_targets}"),
        ("every file is complete", incomplete == 0, f"incomplete files={incomplete}"),
        ("every file reconciles", not unreconciled, f"unreconciled={unreconciled}"),
    ]


def dump_lines(conn: psycopg.Connection) -> list[str]:
    """Sorted text of target and ledger. applied_at is left out: it is a wall-clock time."""
    lines = []
    for row in conn.execute("SELECT " + ", ".join(TARGET_COLS) + " FROM target"):
        lines.append("target\t" + "\t".join(str(v) for v in row))
    for row in conn.execute(
        "SELECT file_hash, row_no, natural_key, disposition, reason_code FROM ledger"
    ):
        lines.append("ledger\t" + "\t".join(str(v) for v in row))
    return sorted(lines)
