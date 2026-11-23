#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"

paxini_python="${PAXINI_PYTHON:-python3}"
if ! command -v "$paxini_python" >/dev/null 2>&1; then
    echo 'Python 3.10+ is required. Install Python and its venv package first.' >&2
    exit 1
fi
"$paxini_python" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else "Python 3.10+ is required.")'

if [[ ! -x .venv/bin/python ]]; then
    "$paxini_python" -m venv .venv
fi
.venv/bin/python -m pip install -r requirements.txt
echo 'Setup complete. Run ./run.sh or ./scripts/install-service.sh.'
