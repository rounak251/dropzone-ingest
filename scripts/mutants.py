"""Mutation set: each entry plants one bug in a copy of the project and runs the tests.

A mutant counts as caught when the test suite fails on the copy. The copy is built under
build/mutants/ and deleted at the end. The control run (no bug planted) must pass, or the
numbers mean nothing. Run with: make mutants
"""

import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WORK = ROOT / "build" / "mutants"
SKIP = {".venv", "build", "results", "__pycache__", ".pytest_cache"}


@dataclass(frozen=True)
class Edit:
    path: str
    old: str = ""
    new: str = ""
    # Replace everything from the start marker up to (not including) the end marker.
    region: tuple[str, str] | None = None


@dataclass(frozen=True)
class Mutant:
    name: str
    bug: str
    edits: tuple[Edit, ...]


M1_APPLY_BATCH = '''    def _apply_batch(self, file_hash: str, rows: list[Row]) -> None:
        self.batches_started += 1
        batch_no = self.batches_started
        with self.conn.transaction():
            decided = self._decide(rows)
            applied = [(row, disp) for row, disp, _ in decided if disp == "applied"]
            if applied:
                self.conn.cursor().executemany(
                    "INSERT INTO target (" + ", ".join(TARGET_COLUMNS) + ") VALUES ("
                    + ", ".join(["%s"] * len(TARGET_COLUMNS)) + ")",
                    [_target_values(row, file_hash) for row, _ in applied],
                )
            killpoint.fire(self.kill, "mid_batch", batch_no)
        with self.conn.transaction():
            self.conn.cursor().executemany(
                "INSERT INTO ledger (file_hash, row_no, natural_key, disposition, reason_code)"
                " VALUES (%s, %s, %s, %s, %s)",
                [(file_hash, row.row_no, row.key, disp, reason) for row, disp, reason in decided],
            )
            quarantined = [(file_hash, row.row_no, reason, row.raw)
                           for row, disp, reason in decided if disp == "quarantined"]
            if quarantined:
                self.conn.cursor().executemany(
                    "INSERT INTO quarantine (file_hash, row_no, reason_code, raw) VALUES (%s, %s, %s, %s)",
                    quarantined,
                )
            killpoint.fire(self.kill, "pre_commit", batch_no)
        killpoint.fire(self.kill, "boundary", batch_no)

'''

MUTANTS = (
    Mutant("M1", "ledger insert moves out of the batch transaction into its own later commit",
           (Edit("dropzone/ingest.py", region=("    def _apply_batch", "    def _decide"),
                 new=M1_APPLY_BATCH),),
           ),
    Mutant("M2", "file marked complete before its batches are applied",
           (Edit("dropzone/ingest.py",
                 old="        self._apply_pending(path, file_hash)\n"
                     "        with self.conn.transaction():\n"
                     "            self.conn.execute(\"UPDATE files SET status = 'complete' WHERE file_hash = %s\", (file_hash,))\n",
                 new="        with self.conn.transaction():\n"
                     "            self.conn.execute(\"UPDATE files SET status = 'complete' WHERE file_hash = %s\", (file_hash,))\n"
                     "        self._apply_pending(path, file_hash)\n"),),
           ),
    Mutant("M3", "restart no longer skips ledgered rows",
           (Edit("dropzone/ingest.py",
                 old="            if row.row_no in done:\n                continue\n", new=""),),
           ),
    Mutant("M4", "partial unique index on applied natural keys removed",
           (Edit("dropzone/schema.sql",
                 old="CREATE UNIQUE INDEX IF NOT EXISTS ledger_one_applied_per_key\n"
                     "    ON ledger (natural_key) WHERE disposition = 'applied';\n", new=""),),
           ),
    Mutant("M5", "changed row whose key is already in target counted as duplicate",
           (Edit("dropzone/ingest.py",
                 old="                same = previous == row.payload_hash\n",
                 new="                same = previous == row.payload_hash or row.key in existing\n"),),
           ),
    Mutant("M6", "reconciliation ignores file status",
           (Edit("dropzone/report.py",
                 old='            "reconciled": status == "complete" and ledgered == rows,',
                 new='            "reconciled": ledgered == rows,'),),
           ),
    Mutant("M7", "natural key stored untrimmed while the payload is trimmed",
           (Edit("dropzone/readers.py",
                 old="        key = fields[contract.natural_key].strip() or None",
                 new="        key = fields[contract.natural_key] or None"),),
           ),
    Mutant("M8", "target INSERT gets ON CONFLICT DO NOTHING, hiding key conflicts",
           (Edit("dropzone/ingest.py",
                 old='                    + ", ".join(["%s"] * len(TARGET_COLUMNS)) + ")",',
                 new='                    + ", ".join(["%s"] * len(TARGET_COLUMNS)) + ") ON CONFLICT DO NOTHING",'),),
           ),
    # Regression mutants: each reverts one fix from the independent review. They are not blind.
    Mutant("R1", "NUL byte check removed from the drop scan",
           (Edit("dropzone/readers.py", old="    _refuse_nul(path)\n", new=""),),
           ),
    Mutant("R2", "integer range check against the 4-byte column removed",
           (Edit("dropzone/contract.py",
                 old="    if not INT4_MIN <= value <= INT4_MAX:\n        return False\n", new=""),),
           ),
    Mutant("R3", "integer pattern uses \\d, which matches other scripts' digits",
           (Edit("dropzone/contract.py", old='INT_RE = re.compile(r"-?[0-9]+")',
                 new='INT_RE = re.compile(r"-?\\d+")'),),
           ),
    Mutant("R4", "file suffix match is case-sensitive",
           (Edit("dropzone/readers.py", old="    suffix = path.suffix.lower()  # ORDERS.CSV is a CSV drop too",
                 new="    suffix = path.suffix"),),
           ),
    Mutant("R5", "byte order mark not stripped from CSV",
           (Edit("dropzone/readers.py",
                 old='    with open(path, encoding="utf-8-sig", newline="") as handle:\n        reader = csv.reader(handle)',
                 new='    with open(path, encoding="utf-8", newline="") as handle:\n        reader = csv.reader(handle)'),),
           ),
    Mutant("R6", "unreadable file no longer exits 3 with a one-line message",
           (Edit("dropzone/cli.py",
                 old='    except OSError as err:\n        # An unreadable file or a disk error. A re-run is safe because committed batches are ledgered.\n'
                     '        print(f"file error: {err}", file=sys.stderr)\n        return 3\n',
                 new=""),),
           ),
    Mutant("R7", "hygiene check no longer sees importlib or __import__ with a literal name",
           (Edit("scripts/check_imports.py",
                 old="        elif isinstance(node, ast.Call) and _is_literal_dynamic_import(node):\n"
                     "            names = [node.args[0].value]\n",
                 new=""),),
           ),
    # Second-round mutants, written by reading the code after the independent review. They are
    # not blind in the review's sense: the author knew the code when writing them.
    Mutant("S1", "dump leaves out the ledger rows",
           (Edit("dropzone/verify.py",
                 old="    for row in conn.execute(\n"
                     "        \"SELECT file_hash, row_no, natural_key, disposition, reason_code FROM ledger\"\n"
                     "    ):",
                 new="    for row in []:"),),
           ),
    Mutant("S2", "final partial batch of a file is never applied",
           (Edit("dropzone/ingest.py",
                 old="        if batch:\n            self._apply_batch(file_hash, batch)\n", new=""),),
           ),
    Mutant("S3", "reconciliation identity leaves out quarantined rows",
           (Edit("dropzone/report.py",
                 old="        ledgered = sum(entry[d] for d in DISPOSITIONS)",
                 new='        ledgered = sum(entry[d] for d in DISPOSITIONS if d != "quarantined")'),),
           ),
    Mutant("S4", "a complete file sent again is re-applied instead of being a no-op",
           (Edit("dropzone/ingest.py",
                 old='        if known and known[0] == "complete":\n'
                     '            return FileResult(name, file_hash, "noop", known[1], known[2])\n', new=""),),
           ),
    Mutant("S5", "dump is not sorted",
           (Edit("dropzone/verify.py", old="    return sorted(lines)", new="    return lines"),),
           ),
    Mutant("S6", "header names are not trimmed",
           (Edit("dropzone/readers.py", old="        header = [name.strip() for name in header]\n",
                 new="        header = list(header)\n"),),
           ),
)


def _copy_tree(dst: Path) -> None:
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(ROOT, dst, ignore=shutil.ignore_patterns(*SKIP))


def _apply(dst: Path, edit: Edit) -> None:
    path = dst / edit.path
    text = path.read_text()
    if edit.region is not None:
        start_marker, end_marker = edit.region
        if text.count(start_marker) != 1 or text.count(end_marker) != 1:
            raise SystemExit(f"{edit.path}: region markers not unique")
        start = text.index(start_marker)
        end = text.index(end_marker)
        text = text[:start] + edit.new + text[end:]
    else:
        count = text.count(edit.old)
        if count != 1:
            raise SystemExit(f"{edit.path}: expected one match, found {count}")
        text = text.replace(edit.old, edit.new)
    path.write_text(text)


def _run_tests(tree: Path) -> tuple[bool, list[str]]:
    result = subprocess.run([sys.executable, "-m", "pytest", "-q", "-rf", "-p", "no:cacheprovider"],
                            cwd=tree, capture_output=True, text=True)
    failed = [line.split(" - ")[0] for line in result.stdout.splitlines() if line.startswith("FAILED")]
    return result.returncode == 0, failed


def main() -> int:
    WORK.mkdir(parents=True, exist_ok=True)
    control_dir = WORK / "control"
    _copy_tree(control_dir)
    passed, failed = _run_tests(control_dir)
    print(f"control (no bug planted): {'pass' if passed else 'FAIL'}")
    if not passed:
        print("\n".join(failed))
        return 1

    caught_count = 0
    for mutant in MUTANTS:
        tree = WORK / mutant.name
        _copy_tree(tree)
        for edit in mutant.edits:
            _apply(tree, edit)
        passed, failed = _run_tests(tree)
        caught = not passed
        caught_count += caught
        print(f"{mutant.name} {'CAUGHT  ' if caught else 'SURVIVED'} {mutant.bug}")
        for name in failed:
            print(f"    failed: {name}")
        shutil.rmtree(tree)
    shutil.rmtree(control_dir)
    print(f"caught {caught_count} of {len(MUTANTS)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
