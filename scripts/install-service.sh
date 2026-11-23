#!/usr/bin/env bash
set -euo pipefail
project_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
mode="${1:-install}"
if [[ $# -gt 1 || ( "$mode" != install && "$mode" != --print ) ]]; then
    echo 'Usage: ./scripts/install-service.sh [--print]' >&2
    exit 2
fi
if [[ ! -x "$project_dir/.venv/bin/python" ]]; then
    echo 'Python environment missing. Run ./setup.sh first.' >&2
    exit 1
fi

"$project_dir/.venv/bin/python" - "$project_dir" "$mode" <<'PY'
from pathlib import Path
import sys

root = Path(sys.argv[1])
if '\n' in str(root) or '\r' in str(root):
    raise SystemExit('The project path must not contain line breaks.')
working_dir = str(root).replace('%', '%%')
exec_dir = str(root).replace('\\', '\\\\').replace('"', '\\"').replace('%', '%%')
unit = (root / 'systemd/paxini-live.service.in').read_text()
unit = unit.replace('@PROJECT_DIR@', working_dir).replace('@EXEC_PROJECT_DIR@', exec_dir)
if sys.argv[2] == '--print':
    print(unit, end='')
else:
    destination = Path.home() / '.config/systemd/user/paxini-live.service'
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(unit)
    print(f'Installed {destination}')
PY

if [[ "$mode" == --print ]]; then
    exit 0
fi
systemctl --user daemon-reload
systemctl --user enable --now paxini-live.service
echo 'Login autostart enabled. Open http://127.0.0.1:8767/ in your browser.'
echo 'After moving this checkout or updating running server code, reinstall the unit and restart the service.'
