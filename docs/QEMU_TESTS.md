# QEMU flow runner

The canonical runner executes the existing scenario scripts directly. The old
suite summaries could hide failures, omit missing results and count console
cases twice. A pass now requires a zero scenario exit status and exactly one
fresh `PASS: ...` result. Timeouts, missing/malformed results and unexecuted
cases cannot pass. JSON results distinguish application FAIL, infrastructure
ERROR and INCOMPLETE.

Run in an isolated Linux QEMU environment with the expected ARM rootfs, QEMU,
Xvfb and ImageMagick. The runner does not provision these dependencies.

```bash
TEST_TARGET=current bash tests/flows/run_all_flows.sh --list
TEST_TARGET=current bash tests/flows/run_all_flows.sh --flow read --case 'read/*console*'
TEST_TARGET=current bash tests/flows/run_all_flows.sh --timeout 600
```

`tests/test_all_flows.sh` delegates to the same runner. Specify `current` or
`original` explicitly; there is no implicit original-firmware target. These
are source-mode profiles, not proof of executing a built IPK. The mixed
`original_current_ui` launcher mode is not an accepted package validation mode.

Runs use a new directory under `tests/flows/_results/run-*`, or a new path
supplied with `--results`. Existing output directories are rejected. The frozen
case inventory, results and remaining incomplete cases appear in
`master_summary.json`; individual scenarios retain screenshots and logs.
Missing scenario entrypoints, empty selected flows and unmatched filters are
errors. Install scenarios are not in this general-purpose suite; they need a
separate, disposable installer environment and acceptance profile.

Scenarios run serially because their helpers share device filesystem paths.
The former worker-count argument accepts only `1`; greater values fail rather
than introducing concurrent filesystem interference. Xvfb allocates a free
display for each case. Cleanup targets owned process groups, not other QEMU or
Xvfb jobs by process name. The existing per-flow parallel scripts remain legacy
development tools and are not used as the authoritative aggregate gate.

This runner fixes execution/accounting, not every scenario assertion or the
PM3 mocking boundary. A green result still needs the scenario and fixture audit
required by the boundaries in [HOST_TESTS.md](HOST_TESTS.md). Run harness contracts
without a device or rootfs using `python -m pytest tests/qemu_runner`.
