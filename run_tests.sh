#!/usr/bin/env bash
set -Eeuo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
test_python="${PYTHON:-python3}"
"$test_python" -m unittest discover -s tests -t . -v
"$test_python" -m compileall -q main.py ripcars_coordination.py ripcars_verifier scripts examples suite_tests
"$test_python" -m pip check
if "$test_python" -c 'import pyflakes' >/dev/null 2>&1; then
    "$test_python" -m pyflakes main.py ripcars_coordination.py ripcars_verifier scripts examples tests suite_tests
fi
for script in run_tests.sh scripts/*.sh; do bash -n "$script"; done
if command -v node >/dev/null; then node --check web/connect.js; fi
