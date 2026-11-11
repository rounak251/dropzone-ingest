import hashlib
from pathlib import Path

import pytest

from dropzone.contract import DropError, load_contract
from dropzone.readers import file_format, iter_rows, scan, sha256_file
from helpers import DAY1, DAY2, ROOT

CONTRACT = load_contract(ROOT / "contract.toml")

# Hand-counted from fixtures/e2e/day1/orders.csv: row number -> (disposition, reason).
DAY1_EXPECTED = {
    1: ("ok", ""), 2: ("ok", ""), 3: ("rejected", "out_of_range"), 4: ("rejected", "not_allowed"),
    5: ("rejected", "out_of_range"), 6: ("quarantined", "bad_type"), 7: ("quarantined", "missing_field"),
    8: ("quarantined", "parse_error"), 9: ("ok", ""), 10: ("ok", ""), 11: ("quarantined", "bad_type"),
    12: ("quarantined", "empty_line"), 13: ("ok", ""), 14: ("ok", ""),
}


def test_csv_rows_are_numbered_and_classified():
    got = {row.row_no: (row.disposition, row.reason) for row in iter_rows(DAY1, CONTRACT)}
    assert got == DAY1_EXPECTED


def test_jsonl_rows_are_classified(tmp_path):
    sample = ROOT / "tests" / "data" / "sample.jsonl"
    got = {row.row_no: (row.disposition, row.reason) for row in iter_rows(sample, CONTRACT)}
    assert got == {
        1: ("ok", ""), 2: ("ok", ""), 3: ("quarantined", "empty_line"),
        4: ("quarantined", "parse_error"), 5: ("quarantined", "parse_error"),
        6: ("quarantined", "unknown_field"), 7: ("quarantined", "missing_field"),
        8: ("quarantined", "bad_type"), 9: ("rejected", "out_of_range"),
    }


def test_key_is_taken_even_when_row_is_quarantined():
    rows = {row.row_no: row for row in iter_rows(DAY1, CONTRACT)}
    assert rows[7].key == "O-1007"
    assert rows[8].key is None  # a field-count mismatch has no mapping, so no key


def test_content_hash_ignores_whitespace_but_not_values(tmp_path):
    spaced = tmp_path / "orders.csv"
    spaced.write_text("".join(
        line.rstrip("\n") + "   \n" if line.strip() else "\n"
        for line in DAY2.read_text().splitlines(keepends=True)))
    assert scan(spaced, CONTRACT) == scan(DAY2, CONTRACT)
    changed = tmp_path / "changed.csv"
    changed.write_text(DAY2.read_text().replace(",6,1200", ",7,1200"))
    assert scan(changed, CONTRACT)[1] != scan(DAY2, CONTRACT)[1]


def test_only_csv_and_jsonl_are_accepted(tmp_path):
    with pytest.raises(DropError, match="only .csv and .jsonl"):
        file_format(tmp_path / "orders.txt")


def test_suffix_match_ignores_case():
    assert file_format(Path("ORDERS.CSV")) == "csv"
    assert file_format(Path("Orders.JsonL")) == "jsonl"


def test_utf8_byte_order_mark_is_not_part_of_the_first_name(tmp_path):
    bom_csv = tmp_path / "orders.csv"
    bom_csv.write_bytes(b"\xef\xbb\xbf" + DAY1.read_bytes())
    got = {row.row_no: (row.disposition, row.reason) for row in iter_rows(bom_csv, CONTRACT)}
    assert got == DAY1_EXPECTED
    bom_jsonl = tmp_path / "orders.jsonl"
    sample = (ROOT / "tests" / "data" / "sample.jsonl").read_bytes()
    bom_jsonl.write_bytes(b"\xef\xbb\xbf" + sample)
    first = next(iter_rows(bom_jsonl, CONTRACT))
    assert (first.disposition, first.key) == ("ok", "J-1")


def test_nul_byte_in_a_csv_refuses_the_whole_drop(tmp_path):
    bad = tmp_path / "orders.csv"
    bad.write_bytes(b"order_id,customer_id,sku,quantity,unit_price_cents,order_date,status\n"
                    b"O-1,C-\x001,SKU-A,2,1500,2026-10-01,placed\n")
    with pytest.raises(DropError, match="NUL byte"):
        scan(bad, CONTRACT)


def test_file_hash_matches_hashlib():
    assert sha256_file(DAY1) == hashlib.sha256(DAY1.read_bytes()).hexdigest()


def test_csv_without_header_is_refused(tmp_path):
    empty = tmp_path / "orders.csv"
    empty.write_text("")
    with pytest.raises(DropError, match="no header"):
        list(iter_rows(empty, CONTRACT))


def test_non_utf8_file_is_refused(tmp_path):
    bad = tmp_path / "orders.jsonl"
    bad.write_bytes(b'{"order_id": "\xff"}\n')
    with pytest.raises(UnicodeDecodeError):
        list(iter_rows(bad, CONTRACT))
