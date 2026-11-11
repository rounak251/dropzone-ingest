import os
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
E2E = ROOT / "fixtures" / "e2e"
DAY1 = E2E / "day1" / "orders.csv"
DAY2 = E2E / "day2" / "orders.csv"
DAY3 = E2E / "day3" / "orders.csv"


def cli(*args: str, kill_at: str | None = None) -> subprocess.CompletedProcess:
    """Run the CLI in a child process, so a SIGKILL hook kills the child and not pytest."""
    env = {k: v for k, v in os.environ.items() if k != "DZ_KILL_AT"}
    if kill_at:
        env["DZ_KILL_AT"] = kill_at
    return subprocess.run([sys.executable, "-m", "dropzone", *args], cwd=ROOT, env=env,
                          capture_output=True, text=True)


def contract_without_quantity_max():
    """The contract with the max removed from quantity. The INTEGER column still bounds it."""
    from dropzone.contract import Contract, load_contract
    contract = load_contract(ROOT / "contract.toml")
    columns = tuple(replace(c, max=None) if c.name == "quantity" else c for c in contract.columns)
    return Contract(contract.name, contract.natural_key, columns)
