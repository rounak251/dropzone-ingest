import re
import subprocess
import sys

from helpers import ROOT, cli  # noqa: F401

RUNBOOK = ROOT / "docs" / "runbook.md"
BLOCK = re.compile(r"```sh runbook\n(.*?)```", re.DOTALL)


def _blocks():
    return BLOCK.findall(RUNBOOK.read_text())


def _with_this_interpreter(block: str) -> str:
    """The runbook calls .venv/bin/. The test substitutes the interpreter that runs the tests.

    The venv's dropzone script imports the installed package, not the tree under test, so the
    runbook commands would test the wrong code. `python -m dropzone` from the tree does not.
    """
    block = block.replace(".venv/bin/python", sys.executable)  # first: the path below contains it
    return block.replace(".venv/bin/dropzone", f"{sys.executable} -m dropzone")


def test_runbook_has_drill_commands():
    assert len(_blocks()) >= 4


def test_every_runbook_drill_command_runs_as_written(tmp_path):
    """Each block in docs/runbook.md runs in bash. The first line says the expected exit code."""
    for number, block in enumerate(_blocks(), start=1):
        first = block.splitlines()[0]
        expected = int(first.split("expect-exit")[1]) if "expect-exit" in first else 0
        result = subprocess.run(["bash", "-c", _with_this_interpreter(block)], cwd=ROOT,
                                capture_output=True, text=True)
        assert result.returncode == expected, (
            f"runbook block {number} exited {result.returncode}, expected {expected}\n"
            f"{result.stdout}\n{result.stderr}")
