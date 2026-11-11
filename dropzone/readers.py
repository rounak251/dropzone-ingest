"""Read CSV and JSONL drops and turn every physical row into a classified Row."""

import csv
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

from dropzone.contract import Contract, DropError, Verdict, check_fields, check_header, storable_text

CHUNK = 1 << 20


@dataclass(frozen=True)
class Row:
    row_no: int
    disposition: str  # "ok", "quarantined" or "rejected"
    reason: str
    key: str | None
    payload: dict | None
    payload_hash: str | None
    content: str  # canonical text, used for the drop content hash
    raw: str  # kept in quarantine so a person can read the row


def file_format(path: Path) -> str:
    suffix = path.suffix.lower()  # ORDERS.CSV is a CSV drop too
    if suffix == ".csv":
        return "csv"
    if suffix == ".jsonl":
        return "jsonl"
    raise DropError(f"{path.name}: only .csv and .jsonl drops are accepted")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(CHUNK), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _make_row(row_no: int, fields: dict | None, raw: str, contract: Contract, error: str | None) -> Row:
    verdict = Verdict(None, "quarantined", error) if error else check_fields(contract, fields or {})
    key = None
    if fields and isinstance(fields.get(contract.natural_key), str):
        key = fields[contract.natural_key].strip() or None
        if key is not None and not storable_text(key):
            key = None
    payload_hash = None
    if verdict.payload is not None:
        payload_json = json.dumps(verdict.payload, sort_keys=True, default=str)
        payload_hash = hashlib.sha256(payload_json.encode()).hexdigest()
        content = payload_json
    else:
        content = "RAW:" + raw
    return Row(row_no, verdict.disposition, verdict.reason, key, verdict.payload,
               payload_hash, content, raw)


def _csv_rows(path: Path, contract: Contract):
    # utf-8-sig drops a byte order mark, which Excel writes at the start of a UTF-8 file.
    with open(path, encoding="utf-8-sig", newline="") as handle:
        reader = csv.reader(handle)
        header = next(reader, None)
        if header is None:
            raise DropError(f"{path.name}: empty file, no header row")
        header = [name.strip() for name in header]
        check_header(contract, header)
        for row_no, record in enumerate(reader, start=1):
            raw = json.dumps([field.strip() for field in record])
            if not record:
                yield _make_row(row_no, None, raw, contract, "empty_line")
            elif len(record) != len(header):
                yield _make_row(row_no, None, raw, contract, "parse_error")
            else:
                fields = dict(zip(header, record))
                yield _make_row(row_no, fields, raw, contract, None)


def _jsonl_rows(path: Path, contract: Contract):
    with open(path, encoding="utf-8-sig", newline="") as handle:
        for row_no, line in enumerate(handle, start=1):
            text = line.strip()
            if not text:
                yield _make_row(row_no, None, text, contract, "empty_line")
                continue
            try:
                obj = json.loads(text)
            except ValueError:
                yield _make_row(row_no, None, text, contract, "parse_error")
                continue
            if not isinstance(obj, dict):
                yield _make_row(row_no, None, text, contract, "parse_error")
            else:
                yield _make_row(row_no, obj, text, contract, None)


def iter_rows(path: Path, contract: Contract):
    """Yield one Row per record (CSV) or per line (JSONL), numbered from 1."""
    if file_format(path) == "csv":
        return _csv_rows(path, contract)
    return _jsonl_rows(path, contract)


def _refuse_nul(path: Path) -> None:
    """Postgres text cannot hold a NUL byte. The CSV reader accepts one, so check the bytes here."""
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(CHUNK), b""):
            if b"\x00" in chunk:
                raise DropError(f"{path.name}: contains a NUL byte; the whole drop is refused")


def scan(path: Path, contract: Contract) -> tuple[int, str]:
    """Return (row_count, content_hash). Reads the whole drop once, writes nothing."""
    _refuse_nul(path)
    digest = hashlib.sha256()
    count = 0
    for row in iter_rows(path, contract):
        digest.update(row.content.encode() + b"\n")
        count += 1
    return count, digest.hexdigest()
