#!/bin/sh
set -eu
ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$ROOT"
export PYTHONPATH="$ROOT/muse-auth:$ROOT/muse-executor${PYTHONPATH:+:$PYTHONPATH}"
exec "$ROOT/muse-executor/.venv/bin/python" -m pytest -q \
    muse-auth/test_muse_auth.py \
    muse-executor/test_executor_bridge.py \
    muse-executor/vendor/muse-gadget-sdk/linux/tests "$@"
