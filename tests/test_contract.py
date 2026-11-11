import pytest

from dropzone.contract import DropError, check_fields, check_header, load_contract
from helpers import ROOT, contract_without_quantity_max

CONTRACT = load_contract(ROOT / "contract.toml")
GOOD = {"order_id": "O-1", "customer_id": "C-1", "sku": "SKU-A", "quantity": "2",
        "unit_price_cents": "10", "order_date": "2026-10-01", "status": "placed"}


def test_contract_has_natural_key_and_seven_columns():
    assert CONTRACT.natural_key == "order_id"
    assert len(CONTRACT.columns) == 7


def test_good_row_is_typed():
    verdict = check_fields(CONTRACT, GOOD)
    assert verdict.disposition == "ok"
    assert verdict.payload["quantity"] == 2
    assert verdict.payload["order_date"].year == 2026


def test_whitespace_is_trimmed_before_rules():
    verdict = check_fields(CONTRACT, {**GOOD, "sku": "  SKU-A \t"})
    assert verdict.payload["sku"] == "SKU-A"


@pytest.mark.parametrize("quantity", ["1", "1000"])
def test_range_edges_are_inclusive(quantity):
    assert check_fields(CONTRACT, {**GOOD, "quantity": quantity}).disposition == "ok"


@pytest.mark.parametrize("change, disposition, reason", [
    ({"quantity": "0"}, "rejected", "out_of_range"),
    ({"quantity": 1001}, "rejected", "out_of_range"),
    ({"unit_price_cents": "-5"}, "rejected", "out_of_range"),
    ({"status": "lost"}, "rejected", "not_allowed"),
    ({"sku": "   "}, "quarantined", "missing_field"),
    ({"unit_price_cents": "abc"}, "quarantined", "bad_type"),
    ({"unit_price_cents": 1.5}, "quarantined", "bad_type"),
    ({"quantity": True}, "quarantined", "bad_type"),
    ({"order_date": "2026-02-30"}, "quarantined", "bad_type"),
    ({"order_date": "30/10/2026"}, "quarantined", "bad_type"),
    ({"extra": "x"}, "quarantined", "unknown_field"),
    ({"quantity": "\u0663"}, "quarantined", "bad_type"),       # Arabic-Indic 3
    ({"quantity": "\uff12"}, "quarantined", "bad_type"),       # fullwidth 2
    ({"order_date": "\uff12\uff10\uff12\uff16-10-01"}, "quarantined", "bad_type"),
    ({"quantity": "9" * 5000}, "quarantined", "bad_type"),      # longer than Python's int limit
    ({"sku": "SKU\x00A"}, "quarantined", "bad_type"),          # NUL cannot be stored in text
    ({"sku": "SKU\ud800"}, "quarantined", "bad_type"),         # lone surrogate cannot be encoded
])
def test_value_failures(change, disposition, reason):
    verdict = check_fields(CONTRACT, {**GOOD, **change})
    assert (verdict.disposition, verdict.reason) == (disposition, reason)
    assert verdict.payload is None


@pytest.mark.parametrize("quantity", ["3000000000", "-3000000000", str(2**31)])
def test_values_outside_the_integer_column_are_rejected_without_a_contract_max(quantity):
    verdict = check_fields(contract_without_quantity_max(), {**GOOD, "quantity": quantity})
    assert (verdict.disposition, verdict.reason) == ("rejected", "out_of_range")


def test_largest_integer_column_value_is_accepted_without_a_contract_max():
    verdict = check_fields(contract_without_quantity_max(), {**GOOD, "quantity": str(2**31 - 1)})
    assert verdict.disposition == "ok"


def test_absent_required_key_is_missing_field():
    fields = {k: v for k, v in GOOD.items() if k != "status"}
    assert check_fields(CONTRACT, fields).reason == "missing_field"


def test_header_must_match_exactly():
    check_header(CONTRACT, list(CONTRACT.column_names))
    with pytest.raises(DropError, match="missing=\\['status'\\]"):
        check_header(CONTRACT, list(CONTRACT.column_names)[:-1])
    with pytest.raises(DropError, match="extra=\\['note'\\]"):
        check_header(CONTRACT, [*CONTRACT.column_names, "note"])
    with pytest.raises(DropError, match="duplicate"):
        check_header(CONTRACT, [*CONTRACT.column_names, "sku"])


def test_contract_without_text_natural_key_is_refused(tmp_path):
    bad = tmp_path / "contract.toml"
    bad.write_text('[contract]\nname = "x"\nnatural_key = "id"\n\n[[columns]]\nname = "id"\ntype = "int"\n')
    with pytest.raises(DropError, match="natural_key"):
        load_contract(bad)
