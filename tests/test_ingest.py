from dataclasses import replace

import psycopg
import pytest

from dropzone import db
from dropzone.contract import DropError, load_contract
from dropzone.ingest import Ingester
from dropzone.readers import iter_rows, sha256_file
from dropzone.verify import dump_lines, run_checks
from dropzone.report import build_report
from helpers import DAY1, DAY2, DAY3, ROOT, cli, contract_without_quantity_max
from reset_db import reset

CONTRACT = load_contract(ROOT / "contract.toml")


def ledger_counts(conn):
    rows = conn.execute("SELECT f.revision, l.disposition, count(*) FROM ledger l"
                        " JOIN files f USING (file_hash) GROUP BY 1, 2").fetchall()
    return {(rev, disp): n for rev, disp, n in rows}


def test_three_drops_match_hand_counts(conn):
    ingester = Ingester(conn, CONTRACT, 500)
    results = [ingester.ingest_file(p) for p in (DAY1, DAY2, DAY3)]
    assert [r.action for r in results] == ["ingested", "ingested", "noop"]
    assert ledger_counts(conn) == {
        (1, "applied"): 4, (1, "duplicate"): 1, (1, "rejected"): 4, (1, "quarantined"): 5,
        (2, "applied"): 3, (2, "duplicate"): 4, (2, "rejected"): 2, (2, "quarantined"): 1,
    }
    assert conn.execute("SELECT count(*) FROM target").fetchone()[0] == 7


def test_identity_holds_for_every_file(conn):
    ingester = Ingester(conn, CONTRACT, 500)
    for path in (DAY1, DAY2, DAY3):
        ingester.ingest_file(path)
    files = build_report(conn)["files"]
    assert len(files) == 2  # DAY3 is the same bytes as DAY2, so it adds no manifest row
    for f in files:
        assert f["applied"] + f["duplicate"] + f["rejected"] + f["quarantined"] == f["manifest_rows"]
    for _, ok, detail in run_checks(conn):
        assert ok, detail


def test_repeat_of_same_bytes_is_a_noop(conn):
    ingester = Ingester(conn, CONTRACT, 500)
    ingester.ingest_file(DAY1)
    before = conn.execute("SELECT count(*) FROM ledger").fetchone()[0]
    again = ingester.ingest_file(DAY1)
    assert again.action == "noop"
    assert conn.execute("SELECT count(*) FROM ledger").fetchone()[0] == before


def test_whitespace_resend_is_a_revision_with_no_new_applied_rows(conn, tmp_path):
    ingester = Ingester(conn, CONTRACT, 500)
    ingester.ingest_file(DAY1)
    spaced = tmp_path / "orders.csv"
    spaced.write_text("".join(
        line.rstrip("\n") + " \n" if line.strip() else "\n"
        for line in DAY1.read_text().splitlines(keepends=True)))
    result = ingester.ingest_file(spaced)
    assert result.action == "ingested" and result.revision == 2
    note = conn.execute("SELECT note FROM files WHERE revision = 2").fetchone()[0]
    assert note == "same_content_as_previous"
    assert ledger_counts(conn).get((2, "applied"), 0) == 0


def test_second_writer_is_refused(conn):
    other = db.connect(db.database_url())
    try:
        db.take_single_writer_lock(conn)
        with pytest.raises(DropError, match="holds the lock"):
            db.take_single_writer_lock(other)
    finally:
        other.close()


# Batch 1 of DAY1 at batch size 3 holds two applied rows (rows 1 and 2). Batch 2 holds only
# rejected and quarantined rows, so a kill in batch 2 cannot show a target row without its ledger row.
@pytest.mark.parametrize("point, batch_no, committed_rows", [
    ("mid_batch", 2, 3),    # batch 2 rolled back: only batch 1 (3 rows) is ledgered
    ("pre_commit", 2, 3),   # ledger rows of batch 2 written but not committed: rolled back
    ("boundary", 2, 6),     # batch 2 committed before the kill
    ("mid_batch", 1, 0),    # batch 1 rolled back: nothing ledgered, no target rows
    ("pre_commit", 1, 0),   # batch 1 target rows written, ledger not committed: all rolled back
])
def test_kill_points_restart_to_the_clean_state(empty_db, tmp_path, point, batch_no, committed_rows):
    """Kill in one batch of 3-row batches, then restart. The result must equal a clean run."""
    clean = cli("ingest", "--batch-size", "3", str(DAY1), str(DAY2))
    assert clean.returncode == 0, clean.stderr
    clean_file = tmp_path / "clean.txt"
    assert cli("verify", "--dump", str(clean_file)).returncode == 0
    expected = clean_file.read_text()

    reset()
    killed = cli("ingest", "--batch-size", "3", str(DAY1), str(DAY2), kill_at=f"{point}:{batch_no}")
    assert killed.returncode == -9, killed.stderr
    conn = db.connect(db.database_url())
    try:
        ledgered = conn.execute("SELECT count(*) FROM ledger").fetchone()[0]
        # A batch is atomic: after a kill, every target row has its applied ledger row, and back.
        applied = conn.execute("SELECT count(*) FROM ledger WHERE disposition = 'applied'").fetchone()[0]
        targets = conn.execute("SELECT count(*) FROM target").fetchone()[0]
    finally:
        conn.close()
    assert ledgered == committed_rows
    assert targets == applied

    restart = cli("ingest", "--batch-size", "3", str(DAY1), str(DAY2))
    assert restart.returncode == 0, restart.stderr
    restarted = tmp_path / "restart.txt"
    assert cli("verify", "--dump", str(restarted)).returncode == 0
    assert restarted.read_text() == expected


def test_reconcile_flags_a_file_killed_after_its_last_batch(empty_db):
    """Every row is ledgered but the file is not complete. It must not count as reconciled."""
    killed = cli("ingest", "--batch-size", "3", str(DAY1), kill_at="boundary:5")
    assert killed.returncode == -9
    conn = db.connect(db.database_url())
    try:
        ledgered = conn.execute("SELECT count(*) FROM ledger").fetchone()[0]
        report = build_report(conn)
    finally:
        conn.close()
    f = report["files"][0]
    assert ledgered == f["manifest_rows"] == 14
    assert (f["status"], f["reconciled"]) == ("in_progress", False)
    assert report["all_reconciled"] is False

    assert cli("ingest", "--batch-size", "3", str(DAY1)).returncode == 0
    conn = db.connect(db.database_url())
    try:
        assert build_report(conn)["all_reconciled"] is True
    finally:
        conn.close()


def test_partial_unique_index_stops_a_second_applied_row_for_one_key(conn):
    Ingester(conn, CONTRACT, 500).ingest_file(DAY1)
    file_hash = conn.execute("SELECT file_hash FROM files").fetchone()[0]
    # A duplicate or rejected row for an applied key is allowed; a second applied row is not.
    conn.execute("INSERT INTO ledger (file_hash, row_no, natural_key, disposition, reason_code)"
                 " VALUES (%s, 99, 'O-1001', 'duplicate', '')", (file_hash,))
    with pytest.raises(psycopg.errors.UniqueViolation) as err:
        conn.execute("INSERT INTO ledger (file_hash, row_no, natural_key, disposition, reason_code)"
                     " VALUES (%s, 100, 'O-1001', 'applied', '')", (file_hash,))
    assert err.value.diag.constraint_name == "ledger_one_applied_per_key"


def test_target_primary_key_refuses_a_second_apply_of_one_key(conn):
    """The decision code never asks for this insert, so force it. The key must still stop it."""
    ingester = Ingester(conn, CONTRACT, 500)
    ingester.ingest_file(DAY1)
    before = conn.execute("SELECT count(*) FROM target").fetchone()[0]
    # Row number 999 is not in the ledger, so the ledger primary key cannot be the one that fails.
    row = replace(next(r for r in iter_rows(DAY1, CONTRACT) if r.key == "O-1001"), row_no=999)
    ingester._decide = lambda rows: [(r, "applied", "") for r in rows]
    with pytest.raises(psycopg.errors.UniqueViolation) as err:
        ingester._apply_batch(sha256_file(DAY1), [row])
    assert err.value.diag.constraint_name == "target_pkey"
    assert conn.execute("SELECT count(*) FROM target").fetchone()[0] == before


def test_padded_and_plain_key_are_one_order(conn, tmp_path):
    drop = tmp_path / "orders.csv"
    drop.write_text(",".join(CONTRACT.column_names) + "\n"
                    " O-9001 ,C-1,SKU-A,1,100,2026-10-01,placed\n"
                    "O-9001,C-1,SKU-A,1,100,2026-10-01,placed\n")
    Ingester(conn, CONTRACT, 500).ingest_file(drop)
    assert ledger_counts(conn) == {(1, "applied"): 1, (1, "duplicate"): 1}
    assert conn.execute("SELECT count(*) FROM target").fetchone()[0] == 1


def test_natural_key_is_case_sensitive(conn, tmp_path):
    """Documented decision: 'o-9001' and 'O-9001' are two orders. design.md, changes after the checker."""
    drop = tmp_path / "orders.csv"
    drop.write_text(",".join(CONTRACT.column_names) + "\n"
                    "o-9001,C-1,SKU-A,1,100,2026-10-01,placed\n"
                    "O-9001,C-1,SKU-A,1,100,2026-10-01,placed\n")
    Ingester(conn, CONTRACT, 500).ingest_file(drop)
    assert ledger_counts(conn) == {(1, "applied"): 2}


def test_value_too_big_for_the_integer_column_is_rejected_and_the_file_completes(conn, tmp_path):
    drop = tmp_path / "orders.csv"
    drop.write_text(",".join(CONTRACT.column_names) + "\n"
                    "O-1,C-1,SKU-A,3000000000,1500,2026-10-01,placed\n"
                    "O-2,C-1,SKU-A,2,1500,2026-10-01,placed\n")
    result = Ingester(conn, contract_without_quantity_max(), 500).ingest_file(drop)
    assert result.action == "ingested"
    assert ledger_counts(conn) == {(1, "applied"): 1, (1, "rejected"): 1}
    assert [f["reconciled"] for f in build_report(conn)["files"]] == [True]


def test_escaped_nul_and_lone_surrogate_in_jsonl_are_quarantined_not_fatal(conn, tmp_path):
    drop = tmp_path / "orders.jsonl"
    drop.write_text(
        r'{"order_id": "J-1", "customer_id": "C-\u0000", "sku": "SKU-A", "quantity": 1, '
        r'"unit_price_cents": 1, "order_date": "2026-10-01", "status": "placed"}' + "\n"
        r'{"order_id": "J-2\ud800", "customer_id": "C-2", "sku": "SKU-A", "quantity": 1, '
        r'"unit_price_cents": 1, "order_date": "2026-10-01", "status": "placed"}' + "\n"
        r'{"order_id": "J-3", "customer_id": "C-3", "sku": "SKU-A", "quantity": 1, '
        r'"unit_price_cents": 1, "order_date": "2026-10-01", "status": "placed"}' + "\n")
    Ingester(conn, CONTRACT, 500).ingest_file(drop)
    assert ledger_counts(conn) == {(1, "applied"): 1, (1, "quarantined"): 2}
    assert dict(conn.execute("SELECT row_no, reason_code FROM quarantine").fetchall()) == {1: "bad_type", 2: "bad_type"}


def test_nul_byte_refuses_the_drop_before_anything_is_written(empty_db, tmp_path):
    bad = tmp_path / "orders.csv"
    bad.write_bytes(b"order_id,customer_id,sku,quantity,unit_price_cents,order_date,status\n"
                    b"O-1,C-\x001,SKU-A,2,1500,2026-10-01,placed\n")
    result = cli("ingest", str(bad))
    assert result.returncode == 2
    assert "NUL byte" in result.stderr
    conn = db.connect(db.database_url())
    try:
        assert conn.execute("SELECT count(*) FROM files").fetchone()[0] == 0
    finally:
        conn.close()


def test_refused_while_earlier_revision_is_in_progress(empty_db, tmp_path):
    killed = cli("ingest", "--batch-size", "3", str(DAY1), kill_at="boundary:1")
    assert killed.returncode == -9
    other = tmp_path / "orders.csv"
    other.write_text(DAY2.read_text() + "O-9999,C-1,SKU-A,1,1,2026-10-01,placed\n")
    refused = cli("ingest", str(other))
    assert refused.returncode == 2
    assert "earlier revision is still in progress" in refused.stderr


def test_bad_header_writes_nothing(empty_db, tmp_path):
    bad = tmp_path / "orders.csv"
    bad.write_text("order_id,customer_id\nO-1,C-1\n")
    result = cli("ingest", str(bad))
    assert result.returncode == 2
    assert "header mismatch" in result.stderr
    conn = db.connect(db.database_url())
    try:
        assert conn.execute("SELECT count(*) FROM files").fetchone()[0] == 0
    finally:
        conn.close()


def test_jsonl_drop_is_ingested_and_counted(conn):
    sample = ROOT / "tests" / "data" / "sample.jsonl"
    Ingester(conn, CONTRACT, 500).ingest_file(sample)
    assert ledger_counts(conn) == {(1, "applied"): 2, (1, "quarantined"): 6, (1, "rejected"): 1}


def test_quarantined_rows_keep_their_raw_text(conn):
    Ingester(conn, CONTRACT, 500).ingest_file(DAY1)
    raw = dict(conn.execute("SELECT row_no, raw FROM quarantine").fetchall())
    assert sorted(raw) == [6, 7, 8, 11, 12]
    assert raw[12] == "[]"  # a blank CSV record, kept as the empty list it was read as


@pytest.mark.parametrize("batch_size", [1, 5, 500])
def test_batch_size_does_not_change_the_final_state(empty_db, tmp_path, batch_size):
    reference = tmp_path / "reference.txt"
    assert cli("ingest", str(DAY1), str(DAY2)).returncode == 0
    assert cli("verify", "--dump", str(reference)).returncode == 0
    reset()
    assert cli("ingest", "--batch-size", str(batch_size), str(DAY1), str(DAY2)).returncode == 0
    other = tmp_path / "other.txt"
    assert cli("verify", "--dump", str(other)).returncode == 0
    assert other.read_text() == reference.read_text()


def test_verify_catches_a_target_row_removed_by_hand(conn):
    Ingester(conn, CONTRACT, 500).ingest_file(DAY1)
    conn.execute("DELETE FROM target WHERE natural_key = 'O-1001'")
    failed = [name for name, ok, _ in run_checks(conn) if not ok]
    assert failed == ["every applied ledger row has its target row"]


def test_reconcile_flags_a_file_that_is_still_in_progress(empty_db):
    cli("ingest", "--batch-size", "3", str(DAY1), kill_at="boundary:1")
    conn = db.connect(db.database_url())
    try:
        report = build_report(conn)
    finally:
        conn.close()
    assert report["files"][0]["status"] == "in_progress"
    assert report["all_reconciled"] is False


def test_dump_holds_every_target_and_ledger_row_in_sorted_order(conn):
    """The drill and the kill tests compare dumps. A dump that left out the ledger would still
    match itself, so check its contents and its order directly."""
    Ingester(conn, CONTRACT, 500).ingest_file(DAY1)
    lines = dump_lines(conn)
    targets = conn.execute("SELECT count(*) FROM target").fetchone()[0]
    ledger = conn.execute("SELECT count(*) FROM ledger").fetchone()[0]
    assert sum(line.startswith("target\t") for line in lines) == targets == 4
    assert sum(line.startswith("ledger\t") for line in lines) == ledger == 14
    assert lines == sorted(lines)
