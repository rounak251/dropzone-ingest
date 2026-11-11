#!/usr/bin/env bash
# Line count. Uses find and wc because cloc is not installed here.
# The brief counts "source and tests": dropzone plus tests is the figure it asks for.
# scripts/ is counted separately and added to the total.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

count() {
  find "$1" -type f \( -name '*.py' -o -name '*.sql' -o -name '*.sh' \) \
    -not -path '*/__pycache__/*' -exec cat {} + 2>/dev/null | wc -l | tr -d ' '
}

dropzone=$(count dropzone)
tests=$(count tests)
scripts=$(count scripts)
printf '%-12s %6s\n' dropzone "$dropzone"
printf '%-12s %6s\n' tests "$tests"
printf '%-12s %6s\n' scripts "$scripts"
printf '%-12s %6s\n' source+tests "$((dropzone + tests))"
printf '%-12s %6s\n' total "$((dropzone + tests + scripts))"
