"""Write seeded, synthetic drop files for the drill and the throughput run. Output goes under build/."""

import argparse
import json
import random
from pathlib import Path

COLUMNS = ["order_id", "customer_id", "sku", "quantity", "unit_price_cents", "order_date", "status"]
STATUSES = ["placed", "shipped", "cancelled"]


def _order(rng: random.Random, number: int) -> dict:
    return {
        "order_id": f"O-{number:07d}",
        "customer_id": f"C-{rng.randint(1, 5000)}",
        "sku": f"SKU-{rng.randint(1, 300):03d}",
        "quantity": rng.randint(1, 20),
        "unit_price_cents": rng.randint(0, 200000),
        "order_date": f"2026-{rng.randint(1, 10):02d}-{rng.randint(1, 28):02d}",
        "status": rng.choice(STATUSES),
    }


def _csv(order: dict) -> str:
    return ",".join(str(order[c]) for c in COLUMNS)


def write_clean_csv(path: Path, rows: int, seed: int = 1) -> None:
    """Valid rows only. Used by the throughput run."""
    rng = random.Random(seed)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(",".join(COLUMNS) + "\n")
        for number in range(1, rows + 1):
            handle.write(_csv(_order(rng, number)) + "\n")


def write_drill_drops(root: Path, rows: int = 10000, seed: int = 1) -> list[Path]:
    """Three drops with a mix of good, bad, duplicate and changed rows.

    day1/orders.csv        base drop; every 97th row has quantity 0, every 131st a text price,
                           every 211th an empty sku, every 401st repeats the row before it
    day2/orders_extra.jsonl a different name; half new orders, half repeats of day1 rows,
                           every 7th repeat has a changed quantity
    day3/orders.csv        revision of day1; repeats with trailing spaces, and every 50th
                           repeat with a changed quantity
    """
    rng = random.Random(seed)
    day1, day1_orders = [], []
    for number in range(1, rows + 1):
        order = _order(rng, number)
        if number % 97 == 0:
            order["quantity"] = 0
        if number % 131 == 0:
            order["unit_price_cents"] = "abc"
        if number % 211 == 0:
            order["sku"] = ""
        if number % 401 == 0 and day1_orders:
            order = dict(day1_orders[-1])
        day1_orders.append(order)
        day1.append(_csv(order))
    base = root / "day1"
    base.mkdir(parents=True, exist_ok=True)
    with open(base / "orders.csv", "w", encoding="utf-8") as handle:
        handle.write(",".join(COLUMNS) + "\n" + "\n".join(day1) + "\n")

    extra_dir = root / "day2"
    extra_dir.mkdir(parents=True, exist_ok=True)
    with open(extra_dir / "orders_extra.jsonl", "w", encoding="utf-8") as handle:
        for index in range(rows // 5):
            if index % 2:
                order = _order(rng, rows + index + 1)
            else:
                order = dict(day1_orders[rng.randrange(rows)])
                if index % 14 == 0:
                    order["quantity"] = 3
            handle.write(json.dumps(order) + "\n")

    rev_dir = root / "day3"
    rev_dir.mkdir(parents=True, exist_ok=True)
    with open(rev_dir / "orders.csv", "w", encoding="utf-8") as handle:
        handle.write(",".join(COLUMNS) + "\n")
        for number, order in enumerate(day1_orders, start=1):
            line = _csv(order)
            if number % 50 == 0:
                line = _csv({**order, "quantity": order["quantity"] + 1})
            elif number % 3 == 0:
                line = line + "  "
            handle.write(line + "\n")
    return [base / "orders.csv", extra_dir / "orders_extra.jsonl", rev_dir / "orders.csv"]


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default="build/drill")
    parser.add_argument("--rows", type=int, default=10000)
    parser.add_argument("--seed", type=int, default=1)
    args = parser.parse_args()
    for path in write_drill_drops(Path(args.out), args.rows, args.seed):
        print(path)
