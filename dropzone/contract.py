"""Load contract.toml and check one row against it."""

import re
import tomllib
from dataclasses import dataclass
from datetime import date

# [0-9] and not \d: \d also matches other scripts' digits, which the design does not accept.
INT_RE = re.compile(r"-?[0-9]+")
DATE_RE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")
TYPES = ("text", "int", "date")
# The database columns are INTEGER (4 bytes). A value outside this range cannot be stored,
# even when the contract has no min or max for its column.
INT4_MIN = -(2**31)
INT4_MAX = 2**31 - 1


class DropError(Exception):
    """A contract or a whole drop is unusable. Nothing from the drop is written."""


@dataclass(frozen=True)
class Column:
    name: str
    type: str
    required: bool = True
    min: int | None = None
    max: int | None = None
    allowed: tuple[str, ...] | None = None


@dataclass(frozen=True)
class Contract:
    name: str
    natural_key: str
    columns: tuple[Column, ...]

    @property
    def column_names(self) -> tuple[str, ...]:
        return tuple(column.name for column in self.columns)


@dataclass(frozen=True)
class Verdict:
    """Outcome for one row. payload is set only when disposition is 'ok'."""

    payload: dict | None
    disposition: str  # "ok", "quarantined" or "rejected"
    reason: str


def load_contract(path) -> Contract:
    with open(path, "rb") as handle:
        data = tomllib.load(handle)
    meta = data.get("contract", {})
    columns = []
    for raw in data.get("columns", []):
        try:
            column = Column(**{**raw, "allowed": tuple(raw["allowed"]) if "allowed" in raw else None})
        except TypeError as err:
            raise DropError(f"bad column entry {raw}: {err}") from err
        if column.type not in TYPES:
            raise DropError(f"column {column.name} has unknown type {column.type}")
        columns.append(column)
    contract = Contract(meta.get("name", ""), meta.get("natural_key", ""), tuple(columns))
    key = [c for c in columns if c.name == contract.natural_key]
    if not key or key[0].type != "text" or not key[0].required:
        raise DropError("natural_key must name a required text column")
    return contract


def check_header(contract: Contract, header: list[str]) -> None:
    """A drop header must list exactly the contract columns, each once."""
    if len(set(header)) != len(header):
        raise DropError(f"duplicate column names in header: {header}")
    missing = sorted(set(contract.column_names) - set(header))
    extra = sorted(set(header) - set(contract.column_names))
    if missing or extra:
        raise DropError(f"header mismatch: missing={missing} extra={extra}")


def storable_text(value: str) -> bool:
    """Postgres text cannot hold a NUL byte, and a lone surrogate cannot be encoded as UTF-8."""
    if "\x00" in value:
        return False
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        return False
    return True


def _in_range(column: Column, value: int) -> bool:
    if not INT4_MIN <= value <= INT4_MAX:
        return False
    if column.min is not None and value < column.min:
        return False
    if column.max is not None and value > column.max:
        return False
    return True


def _cast(column: Column, value):
    """Return the typed value, or raise ValueError if the value has the wrong shape."""
    if column.type == "text":
        if not isinstance(value, str):
            raise ValueError("not text")
        if not storable_text(value):
            raise ValueError("text that Postgres cannot store")
        return value
    if column.type == "int":
        if isinstance(value, bool):
            raise ValueError("bool is not an int")
        if isinstance(value, int):
            return value
        if isinstance(value, str) and INT_RE.fullmatch(value):
            return int(value)
        raise ValueError("not an integer")
    if isinstance(value, str) and DATE_RE.fullmatch(value):
        return date.fromisoformat(value)
    raise ValueError("not a date")


def check_fields(contract: Contract, fields: dict) -> Verdict:
    """Check one row. Quarantine means the shape is wrong; rejected means a value rule failed.

    Rules run in a fixed order and the first failure gives the reason code.
    """
    if set(fields) - set(contract.column_names):
        return Verdict(None, "quarantined", "unknown_field")
    values = {}
    for column in contract.columns:
        raw = fields.get(column.name)
        if isinstance(raw, str):
            raw = raw.strip()
        if raw is None or raw == "":
            if column.required:
                return Verdict(None, "quarantined", "missing_field")
            values[column.name] = None
            continue
        try:
            values[column.name] = _cast(column, raw)
        except ValueError:
            return Verdict(None, "quarantined", "bad_type")
    for column in contract.columns:
        value = values[column.name]
        if value is None:
            continue
        if isinstance(value, int) and not _in_range(column, value):
            return Verdict(None, "rejected", "out_of_range")
        if column.allowed is not None and value not in column.allowed:
            return Verdict(None, "rejected", "not_allowed")
    return Verdict(values, "ok", "")
