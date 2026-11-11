"""Throughput, recovery and no-op timing. Writes results/throughput.json.

Postgres runs in Docker on macOS, so these numbers are not native-disk numbers.
QUICK mode caps the row count and is a smoke test of the measurement, not a benchmark.
"""

import argparse
import hashlib
import json
import os
import platform
import resource
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from gen_drill import write_clean_csv  # noqa: E402
from reset_db import reset  # noqa: E402
from dropzone import db  # noqa: E402
from dropzone.contract import load_contract  # noqa: E402
from dropzone.ingest import Ingester  # noqa: E402

QUICK_CAP = 20000
BATCH_SIZES = (500, 5000)
BUILD = ROOT / "build" / "throughput"
OUT = ROOT / "results" / "throughput.json"


def peak_rss_bytes() -> int:
    # macOS reports bytes, Linux reports kilobytes.
    value = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return value if sys.platform == "darwin" else value * 1024


def child(csv_path: Path, batch_size: int) -> dict:
    """Run in its own process so the peak RSS belongs to one batch size only."""
    reset()
    contract = load_contract(ROOT / "contract.toml")
    conn = db.connect(db.database_url())
    try:
        db.ensure_schema(conn)
        ingester = Ingester(conn, contract, batch_size)
        start = time.perf_counter()
        ingester.ingest_file(csv_path)
        seconds = time.perf_counter() - start
        start = time.perf_counter()
        noop = ingester.ingest_file(csv_path)
        noop_seconds = time.perf_counter() - start
    finally:
        conn.close()
    with open(csv_path, encoding="utf-8") as handle:
        rows = sum(1 for _ in handle) - 1
    return {"batch_size": batch_size, "rows": rows, "seconds": round(seconds, 3),
            "rows_per_second": round(rows / seconds, 1), "peak_rss_bytes": peak_rss_bytes(),
            "noop_action": noop.action, "noop_seconds": round(noop_seconds, 6)}


def recovery_seconds(csv_path: Path) -> float:
    """From the moment the killed process has exited to a clean reconcile."""
    env = {k: v for k, v in os.environ.items() if k != "DZ_KILL_AT"}
    cli = [sys.executable, "-m", "dropzone"]
    reset()
    killed = subprocess.run(cli + ["ingest", str(csv_path)], cwd=ROOT,
                            env={**env, "DZ_KILL_AT": "mid_batch:10"}, capture_output=True)
    if killed.returncode != -9:
        raise SystemExit(f"recovery run did not die by SIGKILL: {killed.returncode}")
    start = time.perf_counter()
    subprocess.run(cli + ["ingest", str(csv_path)], cwd=ROOT, env=env, check=True, capture_output=True)
    subprocess.run(cli + ["reconcile", "--out-dir", str(BUILD / "reconcile")], cwd=ROOT,
                   env=env, check=True, capture_output=True)
    return round(time.perf_counter() - start, 3)


def source_tree_sha256() -> str:
    """Stands in for a git SHA, because this copy of the project is not a git repository."""
    digest = hashlib.sha256()
    files = sorted(p for d in ("dropzone", "tests", "scripts") for p in (ROOT / d).rglob("*")
                   if p.is_file() and "__pycache__" not in p.parts)
    files += [ROOT / "contract.toml", ROOT / "pyproject.toml", ROOT / "uv.lock"]
    for path in files:
        digest.update(str(path.relative_to(ROOT)).encode() + b"\0" + path.read_bytes())
    return digest.hexdigest()


def hardware() -> dict:
    if sys.platform != "darwin":
        return {"platform": platform.platform(), "cpu": platform.processor()}
    keys = ["hw.model", "hw.memsize", "hw.ncpu", "machdep.cpu.brand_string"]
    out = subprocess.run(["sysctl", "-n", *keys], capture_output=True, text=True, check=True).stdout
    return dict(zip(keys, out.strip().split("\n")))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rows", type=int, default=500000)
    parser.add_argument("--quick", action="store_true", help="smoke mode: cap the row count")
    parser.add_argument("--child", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--batch-size", type=int, help=argparse.SUPPRESS)
    parser.add_argument("--file", help=argparse.SUPPRESS)
    args = parser.parse_args()

    if args.child:
        print(json.dumps(child(Path(args.file), args.batch_size)))
        return 0

    rows = min(args.rows, QUICK_CAP) if args.quick else args.rows
    BUILD.mkdir(parents=True, exist_ok=True)
    csv_path = BUILD / f"orders_{rows}.csv"
    write_clean_csv(csv_path, rows)
    runs = []
    for size in BATCH_SIZES:
        out = subprocess.run([sys.executable, __file__, "--child", "--batch-size", str(size),
                              "--file", str(csv_path)], cwd=ROOT, capture_output=True, text=True, check=True)
        runs.append(json.loads(out.stdout.strip().splitlines()[-1]))
        print(f"batch {size}: {runs[-1]['rows_per_second']} rows/s")
    result = {
        "mode": "smoke" if args.quick else "full",
        "rows_requested": args.rows,
        "rows_measured": rows,
        "postgres": "postgres:17-alpine in Docker on macOS (not native disk)",
        "runs": runs,
        "recovery_seconds_after_sigkill": recovery_seconds(csv_path),
        "hardware": hardware(),
        "source_tree_sha256": source_tree_sha256(),
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(result, indent=2) + "\n")
    csv_path.unlink()
    print(f"wrote {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
