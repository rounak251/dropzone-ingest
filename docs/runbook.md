# Runbook

Each block marked `sh runbook` is run by `tests/test_runbook.py`. The first line
of a block gives the exit code the test expects. Run `uv sync` before using these
commands, because they call `.venv/bin/`. The commands work on the dev database
from `.env.example`. They reset that database, so do not point them at data you
want to keep.

## Failed reconciliation

Run `dropzone reconcile`. A file is not reconciled when its ledger does not cover
its manifest row count. The usual cause is an ingest that was killed and has not
been re-run. The report shows status `in_progress` for that file.

1. Re-run the same `dropzone ingest` command with the same drop files. Committed
   batches are skipped and the rest is applied.
2. Run `dropzone reconcile` again. Every file must show `yes`.
3. If a file is still not reconciled after a clean re-run, stop. Keep the
   `results/` output and the drop file, and raise it with the engineer on call.

```sh runbook
# expect-exit 1
.venv/bin/python scripts/reset_db.py
DZ_KILL_AT=boundary:1 .venv/bin/dropzone ingest --batch-size 3 fixtures/e2e/day1
.venv/bin/dropzone reconcile --out-dir build/runbook/before-restart
```

## Re-run after a crash

A SIGKILL leaves the file `in_progress` and rolls back the batch that was open.
The restart redoes that batch from its first row. Exit code 137 means the process
was killed by SIGKILL, which is the signal the crash drill sends.

```sh runbook
# expect-exit 0
.venv/bin/python scripts/reset_db.py
DZ_KILL_AT=mid_batch:2 .venv/bin/dropzone ingest --batch-size 3 fixtures/e2e/day1
test $? -eq 137 || exit 1
.venv/bin/dropzone ingest --batch-size 3 fixtures/e2e/day1
.venv/bin/dropzone reconcile --out-dir build/runbook/after-restart
```

## Quarantine a bad drop

A drop whose header does not match `contract.toml` is refused before anything is
written. The tool never deletes or moves drop files. To quarantine a drop, move
the file by hand into a folder outside the drop folder, and tell the sender why.

```sh runbook
# expect-exit 2
mkdir -p build/runbook/quarantine
printf 'order_id,customer_id\nO-1,C-1\n' > build/runbook/quarantine/orders.csv
.venv/bin/dropzone ingest build/runbook/quarantine/orders.csv
```

## A repeated drop

A drop with the same bytes as one already complete is a no-op. The output says
`noop`. This is expected when a sender re-sends a file.

```sh runbook
# expect-exit 0
.venv/bin/python scripts/reset_db.py
.venv/bin/dropzone ingest fixtures/e2e/day1 > /dev/null
.venv/bin/dropzone ingest fixtures/e2e/day1 | grep -q ' noop '
```

## Integrity check after a restart

`dropzone verify` runs the integrity checks in this order: duplicate keys, the
ledger against the target, incomplete files, and per-file reconciliation.

```sh runbook
# expect-exit 0
.venv/bin/python scripts/reset_db.py
.venv/bin/dropzone ingest --batch-size 3 fixtures/e2e/day1
.venv/bin/dropzone verify
```

## When a customer says the totals differ

Open the report (`results/e2e/report.md` for the sample). For the file in
question, check the file SHA-256 and the content SHA-256, the manifest row count,
and the count per reason code. Then ask the customer these questions:

1. Which file name and which file SHA-256 did you send?
2. How many rows did your export contain, and does that include the header row?
3. Which reason codes do your missing rows match? For example `out_of_range`
   means a quantity or price was outside the contract range.
4. Did the export change between two sends? If so, send the new version as a
   revision under the same file name. A changed row is rejected, not updated, so
   a person must decide what to do with it.

Do not promise a total until the reconciliation for that file shows `yes`.
