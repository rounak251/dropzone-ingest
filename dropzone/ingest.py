"""Apply drops to Postgres. Each batch is one transaction; restart skips ledgered rows."""

import os
from dataclasses import dataclass
from pathlib import Path

import psycopg

from dropzone import killpoint
from dropzone.contract import Contract, DropError
from dropzone.readers import Row, file_format, iter_rows, scan, sha256_file

TARGET_COLUMNS = ("natural_key", "customer_id", "sku", "quantity", "unit_price_cents",
                  "order_date", "status", "payload_hash", "source_file_hash", "source_row_no")


@dataclass(frozen=True)
class FileResult:
    file_name: str
    file_hash: str
    action: str  # "noop", "ingested" or "resumed"
    revision: int
    row_count: int


class Ingester:
    def __init__(self, conn: psycopg.Connection, contract: Contract, batch_size: int):
        if batch_size < 1:
            raise DropError("batch size must be at least 1")
        self.conn = conn
        self.contract = contract
        self.batch_size = batch_size
        self.kill = killpoint.parse(os.environ.get("DZ_KILL_AT"))
        self.batches_started = 0

    def ingest_file(self, path: Path) -> FileResult:
        file_format(path)  # rejects unknown extensions before any work
        file_hash = sha256_file(path)
        name = path.name
        known = self.conn.execute(
            "SELECT status, revision, row_count FROM files WHERE file_hash = %s", (file_hash,)
        ).fetchone()
        if known and known[0] == "complete":
            return FileResult(name, file_hash, "noop", known[1], known[2])
        if known:
            action, revision, row_count = "resumed", known[1], known[2]
        else:
            row_count, content_hash = scan(path, self.contract)
            revision = self._new_revision(name, file_hash, content_hash, row_count)
            action = "ingested"
        self._apply_pending(path, file_hash)
        with self.conn.transaction():
            self.conn.execute("UPDATE files SET status = 'complete' WHERE file_hash = %s", (file_hash,))
        return FileResult(name, file_hash, action, revision, row_count)

    def _new_revision(self, name: str, file_hash: str, content_hash: str, row_count: int) -> int:
        open_rev = self.conn.execute(
            "SELECT file_hash FROM files WHERE file_name = %s AND status = 'in_progress'", (name,)
        ).fetchone()
        if open_rev:
            raise DropError(f"{name}: an earlier revision is still in progress; re-run that drop first")
        previous = self.conn.execute(
            "SELECT revision, content_hash FROM files WHERE file_name = %s ORDER BY revision DESC LIMIT 1",
            (name,),
        ).fetchone()
        revision = previous[0] + 1 if previous else 1
        note = "same_content_as_previous" if previous and previous[1] == content_hash else ""
        with self.conn.transaction():
            self.conn.execute(
                "INSERT INTO files (file_hash, file_name, revision, content_hash, row_count, status, note)"
                " VALUES (%s, %s, %s, %s, %s, 'in_progress', %s)",
                (file_hash, name, revision, content_hash, row_count, note),
            )
        return revision

    def _apply_pending(self, path: Path, file_hash: str) -> None:
        done = {row[0] for row in self.conn.execute(
            "SELECT row_no FROM ledger WHERE file_hash = %s", (file_hash,))}
        batch: list[Row] = []
        for row in iter_rows(path, self.contract):
            if row.row_no in done:
                continue
            batch.append(row)
            if len(batch) == self.batch_size:
                self._apply_batch(file_hash, batch)
                batch = []
        if batch:
            self._apply_batch(file_hash, batch)

    def _apply_batch(self, file_hash: str, rows: list[Row]) -> None:
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

    def _decide(self, rows: list[Row]) -> list[tuple[Row, str, str]]:
        """Give every row a disposition. Only the decisions in design.md section 4 apply."""
        keys = sorted({row.key for row in rows if row.disposition == "ok"})
        existing = {}
        if keys:
            existing = dict(self.conn.execute(
                "SELECT natural_key, payload_hash FROM target WHERE natural_key = ANY(%s)", (keys,)
            ).fetchall())
        seen: dict[str, str] = {}
        out = []
        for row in rows:
            if row.disposition != "ok":
                out.append((row, row.disposition, row.reason))
            elif row.key in seen or row.key in existing:
                # The first applied copy may come from this batch or an earlier one. The
                # reason must not depend on where the batch boundary falls.
                previous = seen.get(row.key) or existing[row.key]
                same = previous == row.payload_hash
                out.append((row, "duplicate" if same else "rejected", "" if same else "changed_since_applied"))
            else:
                seen[row.key] = row.payload_hash
                out.append((row, "applied", ""))
        return out


def _target_values(row: Row, file_hash: str) -> tuple:
    p = row.payload
    return (row.key, p["customer_id"], p["sku"], p["quantity"], p["unit_price_cents"],
            p["order_date"], p["status"], row.payload_hash, file_hash, row.row_no)
