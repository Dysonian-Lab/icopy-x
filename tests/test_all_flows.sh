#!/bin/bash
# Compatibility entry point for the canonical flow runner.
set -eu
HERE="$(cd "$(dirname "$0")" && pwd)"
exec bash "${HERE}/flows/run_all_flows.sh" "$@"
