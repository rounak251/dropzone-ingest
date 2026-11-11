# dropzone-ingest

A Python command line tool that loads CSV and JSONL file drops into Postgres so
that every source row is applied once, and every source row ends with a
recorded disposition. Run it with Docker and uv:

```sh
docker compose up -d && make e2e
```

`make e2e` starts from an empty database, ingests three fixture drops, writes a
reconciliation report to `results/e2e/report.md`, and diffs that report against
`tests/golden/report.md`. The diff must be empty and the command exits 0.

Requirements: Docker, [uv](https://docs.astral.sh/uv/), and Python 3.12 (uv
reads `.python-version`). The tool was built and tested on macOS (Apple M2).

## What it does

- Reads CSV and JSONL drops against one data contract, `contract.toml`.
- Records each file by its SHA-256 and its content hash in a manifest. The same
  bytes are a no-op. The same file name with different bytes is a new revision.
- Applies rows in Postgres transactions, one per batch. Each batch writes its
  target rows and its ledger rows in the same transaction.
- Gives every source row one disposition: `applied`, `duplicate`, `rejected` or
  `quarantined`, with a reason code. Quarantined rows keep their raw text.
- Reconciles each file: applied + duplicate + rejected + quarantined must equal
  the manifest row count. Output is JSON and Markdown, without timestamps.
- Has a seeded drill that sends SIGKILL in the middle of a batch, restarts, and
  checks that the final state matches a clean run.

Commands: `dropzone ingest`, `dropzone reconcile`, `dropzone verify`.

Exit codes:

- 0: success.
- 1: a check ran and failed (`reconcile` or `verify`).
- 2: a drop was refused. The refused drop wrote nothing. Drops given earlier in
  the same command stay applied.
- 3: a database or file error. A re-run is safe, because committed batches are
  in the ledger.

An uncaught Python exception also exits 1 with a traceback. Treat that as a bug.

## Commands

| command | what it does |
|---|---|
| `docker compose up -d` | starts Postgres 17 on `127.0.0.1:55432` |
| `make test` | runs the test suite (88 tests) |
| `make e2e` | three fixture drops, report, golden diff, verify |
| `make drill N=50` | 50 seeded SIGKILL runs, each compared with a clean run |
| `make throughput QUICK=1` | smoke run of the timing code, writes `results/throughput.json` |
| `make mutants` | plants 21 bugs in copies of the tree and checks that the tests fail |
| `make hygiene` | hygiene check on imports (see below) |
| `make loc` | line count for dropzone, tests and scripts |
| `make footprint` | appends a `docker stats` section to `docs/footprint.txt` |
| `make down` | stops the stack and removes its volume |

The Postgres credentials in `.env.example` are for local development only.

## Design

The design is in [docs/design.md](docs/design.md). The body was written before
the code and is not edited to match it. Changes made after the checker ran are
listed at the end of that file, including a correction to section 4 about NUL
bytes. The data contract and a sign-off template are in
[docs/discovery.md](docs/discovery.md). The worked example there is a fictional
scenario with generated data. The runbook is [docs/runbook.md](docs/runbook.md).
Each `sh runbook` block in it is executed by `tests/test_runbook.py`.

Two deviations from a literal reading of the brief are recorded in the design.
The natural key constraint is a partial unique index on applied rows, because
duplicate rows must also be in the ledger. The dump used for comparison leaves
out `applied_at`, because it is a wall-clock time.

## What is not proven

- The drill covers one kill point, mid-batch, in one seeded set of 50 runs. The
  tests add kills at the pre-commit and batch-boundary points, in batch 1 and in
  batch 2 at batch size 3, and they compare final states at batch sizes 1, 5 and
  500. This is not a proof that no other interleaving breaks the design.
- Power loss, fsync, and a crash of the Postgres server are not tested. Every
  kill in the tests and the drill is a SIGKILL of the client process; the Postgres
  container is never killed. Durability of committed batches depends on Postgres
  and on the Docker Desktop VM on macOS. Neither was tested.
- Only one ingest process at a time is supported. A session advisory lock
  enforces that. Concurrent writers are not tested.
- Natural keys are compared exactly after trimming, and the comparison is
  case-sensitive. `o-9001` and `O-9001` are two orders. This is a decision,
  recorded in design.md, not a tested business rule.
- The benchmarks run Postgres in Docker on macOS. The numbers are not native-disk
  numbers and are not a comparison with any other system.
- There is no authentication, HTTP interface, metrics or deployment. The tool
  does not run anywhere except on the developer's machine.
- Business rules beyond the contract, such as cross-row or referential rules, are
  not checked.
- Footprint. `docs/footprint.txt` holds `docker stats` output for the whole
  compose stack, which is one container (`postgres:17-alpine`). The file has
  samples from an earlier session and eight samples taken while `make drill N=50`
  ran. During the drill the container used 125.6 to 149.1 MiB of memory and 20 to
  53 percent CPU, against a limit of 7.748 GiB. The Docker VM has 8319770624 bytes
  (7.75 GiB), a little under the 8 GB the brief names. Nothing was sampled during a
  500,000-row run.
- Line count. `make loc` gives dropzone 790, tests 722, scripts 687, so
  source and tests together are 1512 lines, 12 above the 1,500 floor. The total
  with scripts is 2199. The check in `tests/test_budget.py` uses the source and
  tests figure, as the brief does.

## Benchmarks

Rows per second at batch sizes 500 and 5,000, peak RSS, recovery after SIGKILL,
and the time for a duplicate file to be recognised as a no-op are measured by
`scripts/throughput.py`. Postgres runs in Docker on macOS, so these numbers are not
native-disk numbers. The figures below are from two `make throughput ROWS=500000` runs on an Apple M2 with
16 GB RAM. The machine was busy, with a load average of 5 to 14.

| measurement | batch 500 | batch 5000 |
|---|---|---|
| rows per second | 13,185 and 12,665 | 13,535 and 12,433 |
| peak RSS | 42.2 and 42.5 MiB | 49.4 and 44.5 MiB |
| duplicate file no-op | 13.8 and 14.8 ms | 14.1 and 14.8 ms |

Batch size made no clear difference, so commit cost does not look like the limit at
this size. Per-row work in Python is the likely cause, but that was not profiled.

Restart after SIGKILL took 33.9 and 34.7 s in this benchmark. That number mostly
measures finishing the file: the kill lands at batch 10, so nearly all of the 500,000
rows are still to ingest when the restart begins. It is not a recovery overhead. The
drill's recovery numbers use a much smaller file.

`results/throughput.json` and `results/throughput.run1.json` hold the raw data.

The drill records recovery time in `results/drill.json`. Recovery is measured
from the kill to the end of the restart and the reconcile, before the dump. In
the last drill run (50 runs, batch size 500, 44 batches) the median was 0.663
seconds, with a minimum of 0.355 and a maximum of 1.393. The runs before it, in
this round, gave 0.405 to 2.344 seconds. This is a few runs on a shared machine
and is not a benchmark.

## Hygiene check

`scripts/check_imports.sh` fails when project code imports an ML, similarity or
n-gram library from a fixed list. It reads each file with Python's `ast` module.
It sees `import` and `from ... import` statements, so `import os, numpy` and
`from numpy import x` are both caught. It also sees `importlib.import_module` and
`__import__` when the name is a string literal. It does not see a name built at
run time, or `exec`. A planted test in `tests/test_hygiene.py` checks that a
run-time name passes, so the limit is stated in the tests as well as here. The
check catches an import and not an approach. A pass is not proof that no such
method is used.

## Independent review

The reviewer was a separate AI agent that had not seen how the code was written. No person has reviewed this project.

The reviewer read the project, wrote eight bugs as mutants (M1 to M8 in
`scripts/mutants.py`), and reported findings. The findings were checked one by
one against the code. This section gives the numbers and the limits.

**The reviewer's eight blind mutants.**

- First run, on the reviewed snapshot (51 tests), before any fix: **3 of 8
  caught** (M2, M3, M5). M1, M4, M6, M7 and M8 survived. This round I reran the
  reviewer's own mutant copies against the reviewed snapshot, and the result was
  the same 3 of 8.
- After the fixes, on the current tree (88 tests): **8 of 8 caught**. This is a
  regression result, not a blind result. The tests for M1, M4, M6, M7 and M8 were
  written after the review, against these eight mutants. The number shows that the
  checker catches the bugs it was shown. It does not show what it would catch in
  bugs it was not shown.

**Six mutants written by the author after reading the code (not blind).**

- S1 to S6, first run before any test change: **4 of 6 caught**. S1 (the dump
  leaves out the ledger rows) and S5 (the dump is not sorted) survived. The kill
  and batch-size tests compare two dumps made by the same function, so an
  incomplete or unsorted dump still matches itself. A test now checks the contents
  and the order of the dump directly. After that fix: 6 of 6.

**Seven regression mutants.**

- R1 to R7 revert fixes from this review: the NUL check, the 4-byte integer bound,
  ASCII-only digits, the case-insensitive suffix, the byte order mark, the exit
  code for an unreadable file, and the literal dynamic-import check. Each was
  caught. These mutants test that the fixes stay in place. They do not show how
  the checker does on new bugs.

**The full set.** `scripts/mutants.py` holds 21 mutants (8 blind, 7 regression, 6
second round). On the final tree, `make mutants` gave **21 of 21 caught**, with the
control run passing.

**Findings.**

- Already fixed before this round, and re-checked this round: NUL bytes in a CSV or
  JSONL drop are refused before the manifest is written; a NUL written as the JSON
  escape `\u0000` is quarantined; an upper-case suffix in a directory is taken; a
  byte order mark is stripped; an integer above the 4-byte range is rejected; non-ASCII
  digits are `bad_type`; `reconcile` and `verify` on an empty database do not crash
  and `verify` fails; the hygiene check sees `import os, numpy`; the batch-size
  test asserts the exit code of its reference run; a kill in batch 1 at
  `pre_commit` is tested.
- Fixed in this round: an unreadable drop gave a traceback and exit 1; it now
  gives one line and exit 3. The hygiene check now sees literal
  `importlib.import_module` and `__import__` names. The dump checks are direct
  (S1 and S5). The source-and-tests line count was 1495 at the start of this round,
  below the 1,500 floor the brief names, because the earlier count included
  `scripts/`. A test for the run-time-name limit and a `make loc` row for source
  and tests bring it to 1512. Design section 2 now states the case rule, and
  section 12 names power loss.
- Not fixed, and stated as limits: power loss, fsync, a Postgres server crash, and
  Docker VM durability on macOS; concurrent writers; computed import names and
  `exec` in the hygiene check; a drill with one kill point only; no full-size
  throughput run (smoke only, per the amendment); the case-sensitive key, which is a
  decision and not a business rule. The eight blind mutants are one reviewer's
  selection, not a survey of possible bugs.

## Layout

```
contract.toml          data contract (column names, types, ranges, allowed values)
schema.sql (dropzone/) Postgres tables, applied at the start of each ingest
dropzone/              the tool: contract, readers, ingest, report, verify, cli, db
scripts/               drill, throughput, data generation, reset, line count, hygiene, mutants
tests/                 unit and integration tests; golden report; runbook test
fixtures/e2e/          three hand-written fixture drops for make e2e
docs/                  design, discovery, runbook, footprint
```

Generated data goes under `build/` and results under `results/`. Neither is
committed.
