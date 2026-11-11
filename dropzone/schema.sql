-- Idempotent schema. Applied at the start of every ingest run.

CREATE TABLE IF NOT EXISTS files (
    file_hash     TEXT PRIMARY KEY,
    file_name     TEXT NOT NULL,
    revision      INTEGER NOT NULL CHECK (revision >= 1),
    content_hash  TEXT NOT NULL,
    row_count     INTEGER NOT NULL CHECK (row_count >= 0),
    status        TEXT NOT NULL CHECK (status IN ('in_progress', 'complete')),
    note          TEXT NOT NULL DEFAULT '',
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (file_name, revision)
);

CREATE TABLE IF NOT EXISTS ledger (
    file_hash    TEXT NOT NULL REFERENCES files (file_hash),
    row_no       INTEGER NOT NULL,
    natural_key  TEXT,
    disposition  TEXT NOT NULL CHECK (disposition IN ('applied', 'duplicate', 'rejected', 'quarantined')),
    reason_code  TEXT NOT NULL DEFAULT '',
    applied_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (file_hash, row_no)
);

-- Partial, because duplicates and rejects must be ledgered with the same key.
CREATE UNIQUE INDEX IF NOT EXISTS ledger_one_applied_per_key
    ON ledger (natural_key) WHERE disposition = 'applied';

CREATE TABLE IF NOT EXISTS target (
    natural_key       TEXT PRIMARY KEY,
    customer_id       TEXT NOT NULL,
    sku               TEXT NOT NULL,
    quantity          INTEGER NOT NULL,
    unit_price_cents  INTEGER NOT NULL,
    order_date        DATE NOT NULL,
    status            TEXT NOT NULL,
    payload_hash      TEXT NOT NULL,
    source_file_hash  TEXT NOT NULL REFERENCES files (file_hash),
    source_row_no     INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS quarantine (
    file_hash    TEXT NOT NULL REFERENCES files (file_hash),
    row_no       INTEGER NOT NULL,
    reason_code  TEXT NOT NULL,
    raw          TEXT NOT NULL,
    PRIMARY KEY (file_hash, row_no)
);
