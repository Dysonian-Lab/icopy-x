#!/bin/bash
# Run inside tools/qemu_env.sh with an explicit IPK and --variant.
set -euo pipefail
project="$(cd -- "$(dirname -- "$0")/.." && pwd)"
exec python3 "$project/tools/qemu_artifact_smoke.py" "$@"
