"""Command line: ingest, reconcile, verify.

Exit codes: 0 ok, 1 a check failed, 2 refused (the refused drop wrote nothing), 3 database or
file error. An uncaught Python exception still exits 1 with a traceback; that is a bug.
"""

import argparse
import csv
import sys
from pathlib import Path

import psycopg

from dropzone import db
from dropzone.contract import DropError, load_contract
from dropzone.ingest import Ingester
from dropzone.readers import file_format
from dropzone.report import build_report, write_report
from dropzone.verify import dump_lines, run_checks


def _expand(paths: list[str]) -> list[Path]:
    """A directory stands for its .csv and .jsonl files, sorted by name.

    Other files are named on stderr, so a file is never dropped without a message.
    Hidden files such as .DS_Store are not reported.
    """
    files = []
    for raw in paths:
        path = Path(raw)
        if not path.exists():
            raise DropError(f"{raw}: no such file or directory")
        if not path.is_dir():
            files.append(path)
            continue
        for child in sorted(path.iterdir()):
            if child.name.startswith(".") or not child.is_file():
                continue
            if child.suffix.lower() in (".csv", ".jsonl"):
                files.append(child)
            else:
                print(f"skipped {child.name}: not a .csv or .jsonl drop", file=sys.stderr)
    return files


def cmd_ingest(args) -> int:
    contract = load_contract(args.contract)
    files = _expand(args.paths)
    if not files:
        raise DropError("no drop files given")
    for path in files:
        file_format(path)
    conn = db.connect(db.database_url(args.database_url))
    try:
        db.ensure_schema(conn)
        db.take_single_writer_lock(conn)
        ingester = Ingester(conn, contract, args.batch_size)
        for path in files:
            result = ingester.ingest_file(path)
            print(f"{result.file_name} {result.action} revision={result.revision} "
                  f"rows={result.row_count} sha256={result.file_hash[:12]}")
    finally:
        conn.close()
    return 0


def cmd_reconcile(args) -> int:
    conn = db.connect(db.database_url(args.database_url))
    try:
        db.require_schema(conn)
        report = build_report(conn)
    finally:
        conn.close()
    print(write_report(report, Path(args.out_dir)), end="")
    return 0 if report["all_reconciled"] else 1


def cmd_verify(args) -> int:
    conn = db.connect(db.database_url(args.database_url))
    try:
        db.require_schema(conn)
        checks = run_checks(conn)
        if args.dump:
            lines = dump_lines(conn)
            Path(args.dump).write_text("\n".join(lines) + "\n")
    finally:
        conn.close()
    for name, ok, detail in checks:
        print(f"{'PASS' if ok else 'FAIL'} {name} ({detail})")
    return 0 if all(ok for _, ok, _ in checks) else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="dropzone")
    sub = parser.add_subparsers(dest="command", required=True)

    ingest = sub.add_parser("ingest", help="apply drop files in the order given")
    ingest.add_argument("paths", nargs="+", help="drop files, or directories of drops")
    ingest.add_argument("--contract", default="contract.toml")
    ingest.add_argument("--batch-size", type=int, default=500)
    ingest.add_argument("--database-url", default=None)
    ingest.set_defaults(func=cmd_ingest)

    reconcile = sub.add_parser("reconcile", help="check the per-file identity; write JSON and Markdown")
    reconcile.add_argument("--out-dir", default="results/reconcile")
    reconcile.add_argument("--database-url", default=None)
    reconcile.set_defaults(func=cmd_reconcile)

    verify = sub.add_parser("verify", help="run integrity checks; optionally write a sorted dump")
    verify.add_argument("--dump", default=None, help="file to write the sorted target and ledger dump to")
    verify.add_argument("--database-url", default=None)
    verify.set_defaults(func=cmd_verify)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except (DropError, UnicodeError, csv.Error) as err:
        print(f"refused: {err}", file=sys.stderr)
        return 2
    except psycopg.Error as err:
        # A database error is not a failed check. Exit 1 is reserved for checks that ran and failed.
        print(f"database error: {err}", file=sys.stderr)
        return 3
    except OSError as err:
        # An unreadable file or a disk error. A re-run is safe because committed batches are ledgered.
        print(f"file error: {err}", file=sys.stderr)
        return 3
