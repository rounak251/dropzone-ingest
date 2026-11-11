#!/usr/bin/env bash
# Hygiene check only. It fails when project code imports an ML, similarity or
# n-gram library. It catches an import, not an approach, so a pass is not proof
# that no such method is used. The list and the parsing are in check_imports.py.
set -euo pipefail

ROOT="${1:-$(cd "$(dirname "$0")/.." && pwd)}"

if ! python3 "$(dirname "$0")/check_imports.py" "$ROOT"; then
  echo "check_imports: blocked import found (hygiene check only, see README)" >&2
  exit 1
fi
echo "check_imports: no blocked imports in dropzone/, tests/, scripts/ (hygiene check only)"
