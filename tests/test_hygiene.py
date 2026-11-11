import subprocess

import pytest

from helpers import ROOT

CHECK = ROOT / "scripts" / "check_imports.sh"


def test_project_has_no_blocked_imports():
    result = subprocess.run(["bash", str(CHECK)], capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("line", [
    "import sklearn.feature_extraction",
    "import os, numpy",
    "import json,pandas as pd",
    "from scipy import sparse",
    "from nltk.corpus import stopwords",
    'importlib.import_module("numpy")',
    '__import__("sklearn.svm")',
])
def test_check_fails_on_a_planted_import(tmp_path, line):
    (tmp_path / "dropzone").mkdir()
    (tmp_path / "dropzone" / "planted.py").write_text(line + "\n")
    result = subprocess.run(["bash", str(CHECK), str(tmp_path)], capture_output=True, text=True)
    assert result.returncode == 1
    assert "planted.py" in result.stdout


def test_a_name_built_at_run_time_is_not_seen(tmp_path):
    """The limit, stated as a test: the check reads literal names only, so this passes on purpose."""
    (tmp_path / "dropzone").mkdir()
    (tmp_path / "dropzone" / "planted.py").write_text(
        "import importlib\nname = 'numpy'\nimportlib.import_module(name)\n")
    result = subprocess.run(["bash", str(CHECK), str(tmp_path)], capture_output=True, text=True)
    assert result.returncode == 0, result.stdout
