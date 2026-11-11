import subprocess

from helpers import ROOT


def _row(out: str, label: str) -> int:
    for line in out.splitlines():
        if line.split()[0] == label:
            return int(line.split()[-1])
    raise AssertionError(f"no {label} row in loc output:\n{out}")


def test_source_and_tests_are_within_the_line_budget():
    out = subprocess.run(["bash", str(ROOT / "scripts" / "loc.sh")], capture_output=True, text=True,
                         check=True).stdout
    # The brief's figure is dropzone plus tests. The total with scripts/ is checked too.
    assert 1500 <= _row(out, "source+tests") <= 3000, out
    assert 1500 <= _row(out, "total") <= 3000, out
