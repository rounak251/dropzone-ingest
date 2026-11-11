"""Seeded SIGKILL drill. Each run kills mid-batch, restarts, and must match a clean run byte for byte."""

import argparse
import json
import math
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from gen_drill import write_drill_drops  # noqa: E402
from reset_db import reset  # noqa: E402
from dropzone.contract import load_contract  # noqa: E402
from dropzone.readers import scan  # noqa: E402

BATCH_SIZE = 500
DROP_DIR = ROOT / "build" / "drill"
RESULTS = ROOT / "results" / "drill.json"


def cli(*args: str, kill_at: str | None = None) -> subprocess.CompletedProcess:
    env = {k: v for k, v in os.environ.items() if k != "DZ_KILL_AT"}
    if kill_at:
        env["DZ_KILL_AT"] = kill_at
    return subprocess.run([sys.executable, "-m", "dropzone", *args],
                          cwd=ROOT, env=env, capture_output=True, text=True)


def count_batches(drops: list[Path]) -> int:
    contract = load_contract(ROOT / "contract.toml")
    return sum(math.ceil(scan(path, contract)[0] / BATCH_SIZE) for path in drops)


def dump_to(path: Path) -> bytes:
    result = cli("verify", "--dump", str(path))
    if result.returncode != 0:
        raise SystemExit(f"verify failed:\n{result.stdout}{result.stderr}")
    return path.read_bytes()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs", type=int, default=50)
    args = parser.parse_args()

    drops = write_drill_drops(DROP_DIR)
    names = [str(p) for p in drops]
    total = count_batches(drops)
    work = DROP_DIR / "dumps"
    work.mkdir(parents=True, exist_ok=True)

    reset()
    clean = cli("ingest", *names)
    if clean.returncode != 0:
        raise SystemExit(f"clean ingest failed:\n{clean.stderr}")
    clean_dump = dump_to(work / "clean.txt")
    print(f"clean run: {total} batches of {BATCH_SIZE}, dump {len(clean_dump)} bytes")

    records = []
    for run in range(1, args.runs + 1):
        # The kill batch depends on the run number, so the runs spread over the whole drill.
        batch = (run * 7919) % total + 1
        reset()
        killed = cli("ingest", *names, kill_at=f"mid_batch:{batch}")
        if killed.returncode != -9:
            raise SystemExit(f"run {run}: expected SIGKILL at batch {batch}, got {killed.returncode}")
        killed_at = time.monotonic()
        restarted = cli("ingest", *names)
        reconciled = cli("reconcile", "--out-dir", str(work / "reconcile"))
        recovery = time.monotonic() - killed_at
        if restarted.returncode != 0 or reconciled.returncode != 0:
            raise SystemExit(f"run {run}: restart or reconcile failed\n{restarted.stderr}{reconciled.stdout}")
        run_file = work / f"run{run:03d}.txt"
        dump = dump_to(run_file)
        identical = dump == clean_dump
        if identical:
            run_file.unlink()  # keep only the dumps that differ, to save disk
        records.append({"run": run, "kill_batch": batch, "identical": identical,
                        "recovery_seconds": round(recovery, 3)})
        print(f"run {run:3d} kill_batch={batch:3d} identical={identical} recovery={recovery:.2f}s")

    matched = sum(r["identical"] for r in records)
    RESULTS.parent.mkdir(parents=True, exist_ok=True)
    RESULTS.write_text(json.dumps({"runs": args.runs, "matched": matched, "batch_size": BATCH_SIZE,
                                   "total_batches": total, "records": records}, indent=2) + "\n")
    print(f"matched {matched}/{args.runs} runs against the clean dump")
    return 0 if matched == args.runs else 1


if __name__ == "__main__":
    raise SystemExit(main())
