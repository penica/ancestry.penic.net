#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "$0")/.."
python_command=${1:-python3}
"$python_command" -m unittest discover -s tests -v
if command -v node >/dev/null; then
    for script in ./*.js; do
        node --check "$script"
    done
fi
