#!/bin/bash
# Run scenarios with checked process outcomes and fresh result files.
# Usage: TEST_TARGET=current bash tests/flows/run_all_flows.sh [options]
set -eu
HERE="$(cd "$(dirname "$0")" && pwd)"
exec python3 "${HERE}/run_all_flows.py" "$@"
