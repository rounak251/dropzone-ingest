import os

import pytest

from dropzone import db
from dropzone.cli import main
from helpers import DAY1, DAY2, cli


def test_directory_takes_upper_case_suffixes(empty_db, tmp_path):
    drops = tmp_path / "drops"
    drops.mkdir()
    (drops / "ORDERS.CSV").write_bytes(DAY2.read_bytes())
    (drops / "other.csv").write_bytes(DAY1.read_bytes())
    result = cli("ingest", str(drops))
    assert result.returncode == 0, result.stderr
    assert "ORDERS.CSV ingested" in result.stdout
    assert "other.csv ingested" in result.stdout


def test_directory_names_files_it_does_not_take(empty_db, tmp_path):
    drops = tmp_path / "drops"
    drops.mkdir()
    (drops / "orders.csv").write_bytes(DAY1.read_bytes())
    (drops / "notes.txt").write_text("not a drop\n")
    (drops / ".DS_Store").write_text("")
    result = cli("ingest", str(drops))
    assert result.returncode == 0, result.stderr
    assert "skipped notes.txt" in result.stderr
    assert ".DS_Store" not in result.stderr


def test_reconcile_without_tables_is_refused(empty_db, tmp_path):
    result = cli("reconcile", "--out-dir", str(tmp_path))
    assert result.returncode == 2
    assert "run dropzone ingest first" in result.stderr


def test_verify_on_an_empty_schema_fails(conn):
    result = cli("verify")
    assert result.returncode == 1
    assert "FAIL at least one drop is recorded" in result.stdout


def test_database_error_exits_3_not_1(capsys):
    code = main(["verify", "--database-url", "postgresql://dropzone:dropzone@127.0.0.1:1/dropzone"])
    assert code == 3
    assert "database error" in capsys.readouterr().err


def test_unreadable_drop_exits_3_with_one_line_not_a_traceback(empty_db, tmp_path):
    if os.geteuid() == 0:
        pytest.skip("root can read a file with mode 000")
    drop = tmp_path / "orders.csv"
    drop.write_bytes(DAY1.read_bytes())
    drop.chmod(0)
    try:
        result = cli("ingest", str(drop))
    finally:
        drop.chmod(0o644)
    assert result.returncode == 3
    assert "file error" in result.stderr
    assert "Traceback" not in result.stderr
    conn = db.connect(db.database_url())
    try:
        assert conn.execute("SELECT count(*) FROM files").fetchone()[0] == 0
    finally:
        conn.close()
