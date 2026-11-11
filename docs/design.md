# Design: dropzone-ingest

This document is written before the code. It states the model, the transaction
boundaries and the failure cases. If the checker or the tests force a change,
the change is recorded in the section "Changes after the checker" at the end,
with the reason. The body of this document is not edited to match the code.

## 1. Problem

A customer drops order files into a folder. The same file can arrive twice.
A corrected file can arrive later under the same name. Some rows are broken.
The ingest process can be killed at any point, for example by an out-of-memory
kill or a power cut. After any of these, the target table must hold each
source row exactly once, and every source row must be accounted for.

## 2. Data model

One contract is used for both CSV and JSONL input (see `contract.toml`). Each
source row has one natural key, `order_id`. The target table holds one row per
natural key. The key is compared exactly after trimming. It is case-sensitive:
`o-9001` and `O-9001` are two keys (change 10 at the end of this file).

Tables:

| table | purpose | key |
|---|---|---|
| `files` | manifest: one row per file revision | `file_hash` (PK), unique `(file_name, revision)` |
| `ledger` | one row per source row, with its disposition | PK `(file_hash, row_no)`; partial unique index on `natural_key` where disposition is `applied` |
| `target` | the applied business rows | PK `natural_key` |
| `quarantine` | raw text of rows that could not be read | PK `(file_hash, row_no)` |

Dispositions, one per source row:

- `applied`: the row was inserted into `target`.
- `duplicate`: the row is identical to a row already applied (in `target`, or
  earlier in the same batch). Identical means the same normalised payload hash.
- `rejected`: the row could be read but breaks a value rule or conflicts with
  an applied row (see section 4).
- `quarantined`: the row could not be read into the contract shape at all.

Every source row gets exactly one ledger row. The identity
`applied + duplicate + rejected + quarantined == manifest row_count` holds per
file when the file status is `complete`.

### 2.1 Deviation from the brief: the natural key constraint

The brief asks for a UNIQUE constraint on `natural_key` in the ledger. A plain
unique constraint would reject the ledger row of a duplicate, because the
duplicate has the same natural key as the applied row. Duplicates must be in the
ledger so that every source row is counted. The constraint is therefore a
partial unique index: `UNIQUE (natural_key) WHERE disposition = 'applied'`. It
still guarantees that at most one applied ledger row exists per natural key.
This is the decision recorded here before the code was written.

### 2.2 Deviation: applied_at is not part of the dump

The brief asks that the dump of the target and ledger tables be byte-identical
between a clean run and a killed-and-restarted run. `applied_at` is a wall-clock
timestamp, so it differs between any two runs. The dump used for comparison
therefore excludes `applied_at`. Every other column is included.

## 3. Ingest of one file

Steps, in order:

1. **Hash the bytes.** `file_hash` is the SHA-256 of the raw file bytes.
2. **Manifest check.** If `files` already has this `file_hash` with status
   `complete`, the file is a no-op. Nothing else is read. The no-op is decided
   from the hash alone, so it is cheap.
3. **Scan.** Read every row once with the same classifier used for applying.
   This gives `row_count` and `content_hash`. A file whose header does not
   match the contract is refused here with an error, and nothing is written.
4. **Manifest write.** Insert the `files` row with status `in_progress`, in its
   own committed transaction. The revision number is the previous revision for
   the same file name plus one. The note is `same_content_as_previous` when the new
   `content_hash` equals the previous revision's `content_hash`. That is the
   case for a resend that differs only in whitespace, or in format (CSV against
   JSONL) with the same rows.
5. **Batches.** Read the set of row numbers already in the ledger for this file
   hash. Skip those rows. Group the remaining rows into batches of
   `batch_size` (500 or 5000). Each batch is one transaction (section 5).
6. **Complete.** After the last batch, set the `files` status to `complete` in
   its own transaction.

A file revision whose predecessor is still `in_progress` is refused. The
operator must re-run the earlier drop first, so that a revision never lands
on top of a half-applied file.

## 4. Row rules

Validation runs in this order. The first failing rule gives the reason code.

Quarantine (the row cannot be read into the contract shape):

| code | cause |
|---|---|
| `empty_line` | a CSV record with no fields, or a blank JSONL line |
| `parse_error` | invalid JSON, a JSON value that is not an object, or a CSV record whose field count differs from the header |
| `unknown_field` | a JSONL object has a key that is not in the contract |
| `missing_field` | a required key is absent, or its value is empty after trimming |
| `bad_type` | an integer or date value does not parse |

Rejected (the row is readable, but a value rule or a state rule fails):

| code | cause |
|---|---|
| `out_of_range` | an integer is below `min` or above `max` |
| `not_allowed` | a text value is not in the `allowed` list |
| `changed_since_applied` | the natural key is already applied with a different payload. The applied copy may be in an earlier batch or earlier in the same batch; the code is the same either way |

Duplicate: the natural key is already applied, or appeared earlier in the same
batch, with the same payload hash.

Trimming: text values are trimmed of leading and trailing whitespace before any
rule runs. The payload hash is computed after trimming and after type casting,
so a resend that differs only in whitespace gives the same payload hash.

Header rules are file-level, not row-level. A CSV header must contain exactly
the contract columns, each once. Any missing or extra column refuses the file.
A file that is not valid UTF-8, or that the CSV reader cannot parse (for example
a NUL byte), is also refused. Refusing a file writes nothing.

## 5. Transactions and the exactly-once argument

Each batch runs in one Postgres transaction, in READ COMMITTED (the Postgres
default). Inside the transaction:

1. Read `target` rows for the natural keys in the batch.
2. Decide the disposition of every row in Python (section 4).
3. Insert the applied rows into `target`.
4. Kill point `mid_batch` (test hook only).
5. Insert every ledger row, and any quarantine rows.
6. Kill point `pre_commit` (test hook only).
7. COMMIT.

Kill point `boundary` runs after the COMMIT of batch n, before batch n+1.

Why this gives exactly-once:

- A batch is atomic. Either all of its target rows and all of its ledger rows
  are visible, or none are. A SIGKILL before COMMIT rolls back the whole batch
  on the server side when the connection closes.
- So the ledger never names a row that is missing from `target`, and `target`
  never holds a row that the ledger does not name.
- Restart reads the ledger, so committed batches are skipped. Uncommitted
  batches are redone from the start of the batch.
- Two constraints back this up even if the logic were wrong: the primary key
  `(file_hash, row_no)` stops the same source row being ledgered twice, and
  the primary key on `target.natural_key` stops a natural key being applied
  twice.

## 6. Why key idempotency on both the file hash and the row natural key

- The file hash decides whether a whole drop was already seen. It makes a
  re-sent file cheap and exact: same bytes, same hash, no work.
- The natural key decides whether a single business row is already applied.
  A file hash alone cannot do this, because a new file can contain old rows
  (a revised file, or the same orders in a different drop). The natural key
  is the business identity; the file hash is only the delivery identity.

## 7. Isolation level and anomalies

Batches run in READ COMMITTED. The reason is that every write is an insert
guarded by a unique constraint, and a single ingest process runs at a time,
enforced by a session advisory lock on a fixed integer key. With one writer, the
anomalies that READ COMMITTED allows (non-repeatable reads, phantom reads)
cannot change a decision made in the same batch.

A weaker level would be a problem. Postgres does not implement READ UNCOMMITTED
(it behaves as READ COMMITTED). The anomaly the brief asks about is a dirty
read: a batch that reads a target row written by another transaction that later
rolls back. That would make a duplicate look already applied when it was never
committed, and the row would then be marked `duplicate` and never applied.
READ COMMITTED never returns uncommitted rows, so this cannot happen.

If two ingest processes ever ran at once, the design would still not
double-apply. The second writer's insert raises a unique violation, its batch
rolls back, and the process fails loudly. It does not merge decisions. This is
a backstop, not a supported mode; the advisory lock prevents it in normal use.

## 8. Reconciliation

`reconcile` reads the `files` and `ledger` tables and writes a JSON report and a
Markdown report. For each file it shows the manifest row count, the four
disposition counts, the count of each reason code, and whether the identity
holds. A file that is `in_progress` is reported as not reconciled, because its
ledger does not yet cover the manifest. The command exits 1 if any file fails.

The Markdown output has no timestamps and no machine paths, so it can be
compared with a golden file.

## 9. Failure cases

| event | state left behind | what restart does |
|---|---|---|
| kill during a batch, before COMMIT | file `in_progress`, batch rolled back | redoes the batch from its first row |
| kill after COMMIT, before the next batch | file `in_progress`, ledger complete up to the batch | skips the committed rows |
| kill after the last batch, before `complete` | all rows in ledger, status `in_progress` | skips every row, sets `complete` |
| kill during the manifest write | either no `files` row, or a complete one | restart starts the file again |
| kill while `in_progress` for name X, a new revision of X arrives | unchanged | refused until the earlier drop is finished |
| file with a bad header | nothing written | fix the drop and re-send |
| file that is re-sent unchanged | nothing written | no-op |

## 10. Hooks used by the drill

`DZ_KILL_AT=<point>[:<batch>]` makes the process send itself SIGKILL at a kill
point. `<batch>` counts batches processed in this process, starting at 1. The
default is 1. The points are `mid_batch`, `pre_commit` and `boundary`. The hook
is read from the environment and does nothing when the variable is unset.

The drill (section 11) uses only `mid_batch`, as the brief asks. The tests
cover `pre_commit` and `boundary` as well.

## 11. Drill and verification

- `drill` generates seeded drops, runs a clean ingest and dumps the target and
  ledger tables (without `applied_at`). Then, for each run, it resets the
  database, starts an ingest with `DZ_KILL_AT=mid_batch:<n>` where `n` depends
  on the run number, checks that the process died by SIGKILL, runs the ingest again
  without a kill, reconciles, and dumps again. The dump must match the clean one
  byte for byte, and no natural key may appear twice in `target`.
- `verify` runs integrity queries: no duplicate natural keys in `target`,
  applied ledger rows and `target` rows match one to one, and every applied row
  names a known file.

## 12. What this design does not handle

- Concurrent ingest processes (refused by the advisory lock).
- Files larger than the disk, or files that are not UTF-8.
- Business rules beyond the contract (cross-row rules, referential checks).
- Corrections to already applied rows. A changed row is rejected, not updated.
  Applying a correction needs a decision by a person, which the runbook covers.
- Scale tests on native disk. The benchmarks run Postgres in Docker on macOS.
- Power loss, fsync, and a crash of the Postgres server. Section 1 names a power cut as a
  failure, but every test and the drill kill only the client process (change 14).

## 13. Performance model (for the benchmark section)

The work per batch is one read of `target`, two or three `executemany` calls,
and one COMMIT. Batch size trades commit overhead against the length of a
transaction and the memory held by one batch. The smoke run only checks that the
measurement works. Final numbers are measured later on a quiet machine.

## 14. Interview questions and where they are answered

| question | answer in this document |
|---|---|
| Why key idempotency on both the file hash and the row natural key? | section 6 |
| What if the process dies between the ledger insert and COMMIT? | section 5, and the kill points in section 10 |
| How do you tell a revised file from a whitespace-only re-send? | section 3 (revision and `same_content_as_previous`) and section 4 (trimming before hashing) |
| A customer says the totals differ. What does the report show, and what do you ask? | section 8, and `docs/runbook.md` and `docs/discovery.md` section 3.4 |
| Which isolation level, and what anomaly would a weaker level allow? | section 7 |
| What would you change first if drop volume grew 100 times? | the answer below |

Answer to the volume question (not measured; a reasoned answer). The first
change is the resume step in section 3. It loads every ledgered row number of a
file into a Python set. At 100 times the volume, a file of about 50 million rows
makes that set several gigabytes. Since batches are contiguous and each batch is
atomic, the resume point can be read as `max(row_no)` from the ledger. The next
changes are the row writes: `COPY` into a staging table, then one set-based
`INSERT ... SELECT` that resolves duplicates in SQL, in place of per-row Python
decisions. After that, partition the ledger by `file_hash`, and put the drop
folder behind a queue so that ingest runs are scheduled and not started by hand.

## Changes after the checker

1. **Reason code merged (found by `test_batch_size_does_not_change_the_final_state`).**
   The first version had two codes: `conflict_in_file` when the first applied copy
   was in the same batch, and `changed_since_applied` when it was in an earlier
   batch. The ledger therefore depended on the batch size: the same drop at batch
   size 500 and at batch size 5 gave different `reason_code` values for the same
   row. The target rows were identical, so the difference was only visible in the
   ledger dump. The two codes are merged into `changed_since_applied`. The drill
   did not catch this, because it compares runs that use the same batch size.
   This is the reason the batch-size test exists.
2. Wording corrections, no change to the model. Section 7 said the advisory lock
   was named `dropzone-ingest`; it is keyed by a fixed integer. Section 11 said the
   kill batch depends on the seed; the drill derives it from the run number, and
   the generated data uses one fixed seed.
3. Addition, no change to the model: section 14 maps the interview questions to
   sections of this document and answers the volume question.
4. **Correction to section 4: NUL bytes.** Section 4 says a file with a NUL byte is
   refused by the CSV reader. That was false on Python 3.12. The reader accepted the
   NUL, Postgres rejected it inside a batch, the process exited 1 with a traceback,
   and the file was left `in_progress`, which blocked later revisions of its name
   (found by the independent review). Now `scan` checks the bytes first. A drop that
   contains a NUL byte is refused with exit 2 before its manifest row is written. A
   NUL that appears as the JSON escape `\u0000`, or a lone surrogate `\ud800`, is
   stored as text Postgres cannot hold. Such a value is `bad_type` (quarantined), and
   such a natural key is treated as no key.
5. **Integer bounds (found by the independent review).** The contract bounds an
   integer only when it has `min` or `max`. The database columns are INTEGER, so a
   value above 2147483647 in a column without `max` failed inside the batch with
   NumericValueOutOfRange. The file was left `in_progress` and every restart failed.
   Now every integer is also checked against the 4-byte range, and a value outside it
   is `rejected` with `out_of_range`, whatever the contract says.
6. **Digits (found by the independent review).** `\d` in the integer and date patterns
   also matches other scripts' digits. The patterns now use `[0-9]`, so a non-ASCII
   digit is `bad_type`, as section 4 says.
7. **Exit codes (found by the independent review).** Exit 1 is only for a check that ran
   and failed. A database error exits 3. `reconcile` and `verify` on a database with no
   dropzone tables exit 2 with a message, and do not crash. `verify` also fails when no
   drop has been recorded: its other checks pass on empty tables, so an empty database
   would otherwise pass.
8. **Directories and suffixes (found by the independent review).** A directory is read
   by the suffix in any case, so `ORDERS.CSV` is taken. A file in a directory that is
   not a `.csv` or `.jsonl` drop is named on stderr. Hidden files such as `.DS_Store`
   are not named. Before this change, an upper-case file was dropped with exit 0 and
   no ledger row.
9. **Byte order mark (found by the independent review).** Files are read with
   `utf-8-sig`, so a BOM at the start of a CSV or JSONL file is not part of the first
   header name or the first JSON line. The file hash is still taken over the raw bytes.
10. **Natural key case (decision, no code change).** Keys are compared exactly after
   trimming. `o-9001` and `O-9001` are two orders. Folding case would merge two keys
   that the source distinguishes, and whether they are the same order is a business
   decision for a person, not a default in code. The test
   `test_natural_key_is_case_sensitive` records the decision. It is not in section 2.
11. **Hygiene check.** `scripts/check_imports.sh` now reads each file with `ast`, through
   `scripts/check_imports.py`. The grep it replaces missed `import os, numpy`. The AST
   check does not see `importlib.import_module("numpy")` or `exec`. It is still a
   hygiene check.
12. **Tests added for the constraints and branches the first mutant run showed were
   untested.** A kill in batch 1 at `pre_commit` and `mid_batch`, where batch 1 holds
   applied rows. A direct check that the batch is atomic (target rows equal applied
   ledger rows after a kill). The partial unique index, the target primary key (forced
   through `_decide`), reconcile of a file killed after its last batch, and the trimmed
   natural key. The mutant set in `scripts/mutants.py` runs these checks.
13. **Runbook test.** `tests/test_runbook.py` runs the runbook commands with the
   interpreter that runs the tests, instead of `.venv/bin/dropzone`. The venv's script
   imports the installed package, so a mutant in the tree under test would not be seen.
14. **Not changed, recorded as not proven.** Power loss and a Postgres server crash are
   not tested. Only the client process is killed. See the README section "What is not
   proven".

15. **File errors exit 3 (independent review, second round).** An unreadable drop, such as a
   file with mode 000, raised PermissionError and exited 1 with a traceback. Exit 1 is for a
   check that ran and failed. Now an OSError is printed as `file error:` and exits 3. A re-run
   is safe: committed batches are ledgered, and a file left `in_progress` is finished by
   re-running it. Test: `test_unreadable_drop_exits_3_with_one_line_not_a_traceback`.
16. **Dump checks (second round, found by a mutant written after the review).** The kill and
   batch-size tests compare two dumps made by the same function. A dump that left out the
   ledger, or that was not sorted, would still match itself. Test
   `test_dump_holds_every_target_and_ledger_row_in_sorted_order` checks the row counts and the
   sort order directly.
17. **Literal dynamic imports (independent review, minor finding).** The hygiene check now
   also sees `importlib.import_module("name")` and `__import__("name")` when the name is a
   string literal. A name built at run time, and `exec`, are still not seen.
18. **Mutant set.** `scripts/mutants.py` holds 21 mutants: M1 to M8 are the independent
   review's blind set; R1 to R7 revert the review's fixes; S1 to S6 were written by the author
   after reading the code. The blind set was run twice: 3 of 8 on the reviewed 51-test snapshot
   (reproduced), and 8 of 8 after the fixes. That second number is a regression result, because
   the tests were written against these eight.
