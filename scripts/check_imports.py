"""Hygiene check: list imports of blocked ML, similarity or n-gram libraries in project code.

It reads each file with ast, so `import os, numpy` and `from numpy import x` are both seen.
It also sees importlib.import_module("numpy") and __import__("numpy") when the name is a
string literal. It does not see a name built at run time, or exec(). That is why this is a
hygiene check, not proof that no such method is used.
"""

import ast
import sys
from pathlib import Path

BLOCKED = {
    "sklearn", "scikit", "numpy", "pandas", "scipy", "torch", "tensorflow", "keras",
    "transformers", "sentence_transformers", "nltk", "spacy", "gensim", "rapidfuzz",
    "fuzzywuzzy", "Levenshtein", "jellyfish", "textdistance", "difflib", "datasketch",
    "faiss", "annoy", "ngram", "xgboost", "lightgbm",
}
DIRS = ("dropzone", "tests", "scripts")


def _is_literal_dynamic_import(node: ast.Call) -> bool:
    """importlib.import_module("x") or __import__("x") with a string literal as the name."""
    func = node.func
    called = (isinstance(func, ast.Name) and func.id == "__import__") or (
        isinstance(func, ast.Attribute) and func.attr == "import_module")
    return called and bool(node.args) and isinstance(node.args[0], ast.Constant) \
        and isinstance(node.args[0].value, str)


def blocked_imports(path: Path):
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            names = [node.module]
        elif isinstance(node, ast.Call) and _is_literal_dynamic_import(node):
            names = [node.args[0].value]
        else:
            continue
        for name in names:
            if name.split(".")[0] in BLOCKED:
                yield node.lineno, name


def main(root: Path) -> int:
    hits = 0
    for group in DIRS:
        base = root / group
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("*.py")):
            for lineno, name in blocked_imports(path):
                print(f"{path.relative_to(root)}:{lineno}: imports {name}")
                hits += 1
    return 1 if hits else 0


if __name__ == "__main__":
    raise SystemExit(main(Path(sys.argv[1])))
