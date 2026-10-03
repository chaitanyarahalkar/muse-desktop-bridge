#!/bin/sh
set -eu
ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$ROOT"

if [ "$(uname -s)" != Darwin ]; then
    echo "This bridge targets macOS. Use the upstream Muse Gadget SDK for Linux." >&2
    exit 1
fi
if ! command -v uv >/dev/null 2>&1; then
    echo "Install uv first: https://docs.astral.sh/uv/getting-started/installation/" >&2
    exit 1
fi
if [ ! -x muse-executor/.venv/bin/python ]; then
    uv venv --python "${PYTHON:-3.13}" muse-executor/.venv
fi
VENV_PYTHON="$ROOT/muse-executor/.venv/bin/python"
"$VENV_PYTHON" -c 'import sys; sys.exit("Python 3.13 or newer is required.") if sys.version_info < (3, 13) else None'
uv pip install --python "$VENV_PYTHON" -r requirements-dev.txt ./muse-executor/vendor/muse-gadget-sdk/linux

echo "Installed. Sign in with: python3 muse-auth/muse_auth.py setup"
echo "Then connect with: ./muse-executor/muse-executor start"
