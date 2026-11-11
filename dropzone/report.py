"""Reconciliation: per-file identity, JSON and Markdown output. No timestamps, so output can be diffed."""

import json
from pathlib import Path

import psycopg

DISPOSITIONS = ("applied", "duplicate", "rejected", "quarantined")


def build_report(conn: psycopg.Connection) -> dict:
    counts: dict[str, dict] = {}
    for file_hash, disposition, reason, n in conn.execute(
        "SELECT file_hash, disposition, reason_code, count(*) FROM ledger GROUP BY 1, 2, 3"
    ):
        entry = counts.setdefault(file_hash, {d: 0 for d in DISPOSITIONS} | {"reasons": {}})
        entry[disposition] += n
        if reason:
            entry["reasons"][reason] = entry["reasons"].get(reason, 0) + n
    files = []
    for file_hash, name, revision, content_hash, rows, status, note in conn.execute(
        "SELECT file_hash, file_name, revision, content_hash, row_count, status, note"
        " FROM files ORDER BY file_name, revision, file_hash"
    ):
        entry = counts.get(file_hash, {d: 0 for d in DISPOSITIONS} | {"reasons": {}})
        ledgered = sum(entry[d] for d in DISPOSITIONS)
        files.append({
            "file_name": name, "revision": revision, "file_hash": file_hash,
            "content_hash": content_hash, "status": status, "note": note,
            "manifest_rows": rows, **{d: entry[d] for d in DISPOSITIONS},
            "reasons": dict(sorted(entry["reasons"].items())),
            "reconciled": status == "complete" and ledgered == rows,
        })
    return {"files": files, "all_reconciled": all(f["reconciled"] for f in files)}


def to_markdown(report: dict) -> str:
    lines = [
        "# Reconciliation report",
        "",
        "Identity checked per file: applied + duplicate + rejected + quarantined = manifest rows.",
        "",
        "| file | revision | status | note | manifest rows | applied | duplicate | rejected | quarantined | reconciled |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for f in report["files"]:
        lines.append(
            f"| {f['file_name']} | {f['revision']} | {f['status']} | {f['note'] or '-'} | {f['manifest_rows']}"
            f" | {f['applied']} | {f['duplicate']} | {f['rejected']} | {f['quarantined']}"
            f" | {'yes' if f['reconciled'] else 'NO'} |"
        )
    lines += ["", "## Reason codes", "", "| file | revision | reason | rows |", "|---|---|---|---|"]
    for f in report["files"]:
        for reason, n in f["reasons"].items():
            lines.append(f"| {f['file_name']} | {f['revision']} | {reason} | {n} |")
    lines += ["", "## File hashes", "", "| file | revision | file sha256 | content sha256 |", "|---|---|---|---|"]
    for f in report["files"]:
        lines.append(f"| {f['file_name']} | {f['revision']} | {f['file_hash']} | {f['content_hash']} |")
    lines += ["", f"All files reconciled: {'yes' if report['all_reconciled'] else 'NO'}", ""]
    return "\n".join(lines)


def write_report(report: dict, out_dir: Path) -> str:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "report.json").write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    markdown = to_markdown(report)
    (out_dir / "report.md").write_text(markdown)
    return markdown
