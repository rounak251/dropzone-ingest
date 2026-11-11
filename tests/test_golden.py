from dropzone.cli import main
from dropzone.report import build_report, to_markdown
from helpers import DAY1, DAY2, DAY3, ROOT


def test_three_drops_match_golden_report(conn, tmp_path):
    assert main(["ingest", str(DAY1), str(DAY2), str(DAY3)]) == 0
    assert main(["reconcile", "--out-dir", str(tmp_path)]) == 0
    golden = (ROOT / "tests" / "golden" / "report.md").read_text()
    assert (tmp_path / "report.md").read_text() == golden


def test_report_has_no_timestamps_or_machine_paths(conn):
    main(["ingest", str(DAY1)])
    markdown = to_markdown(build_report(conn))
    assert str(ROOT) not in markdown
    assert "applied_at" not in markdown
