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

## Isolated factory-image environment

`tools/qemu_env.sh` is a maintainer-operated Linux wrapper for **trusted** test
commands. It needs sudo, util-linux (`unshare`, `mount`, `findmnt`, `sfdisk`),
Python 3, overlayfs, a factory SD image and an explicitly selected static ARM
QEMU binary. Runtime/flow tests also need Xvfb; flow screenshots use ImageMagick.
It is ARM user-mode emulation: no device kernel, USB hardware or RF is emulated.
This is not a security sandbox for contributor code: the non-root test account
still has its normal host filesystem and network access. CI needs a disposable
worker without credentials or physical-device access.

Use a clean checkout (a Git worktree is supported), with inputs and a new output
directory outside `/mnt` and `/tmp`. The output parent must already exist.
Paths containing mount-option separators (comma, colon, backslash or newline)
are rejected. Run from the non-root account that will own test results:

```bash
sudo bash tools/qemu_env.sh /srv/images/factory.img /usr/bin/qemu-arm-static \
  "$PWD" /srv/runs/runtime-001 -- /usr/bin/python3 "$PWD/tools/qemu_runtime_smoke.py"

sudo bash tools/qemu_env.sh /srv/images/factory.img /usr/bin/qemu-arm-static \
  "$PWD" /srv/runs/scan-001 -- /usr/bin/python3 "$PWD/tests/flows/run_all_flows.py" \
  --target current --flow scan --case scan/scan_no_tag \
  --results /srv/runs/scan-001/results/flows
```

The wrapper validates the image's Linux root partitions, mounts them using
read-only auto-clearing loop devices with journal replay disabled, then creates
separate writable overlays. Its mount/PID namespaces hide the host's `/mnt`
and `/tmp`; `/mnt/upan` is new scratch data, not the host's shared directory.
Only the command runs as the original sudo user. Namespace termination kills
remaining descendants; normal cleanup unmounts the owned mounts in reverse order.
There are no package installations or edits to the original image/checkout.

The overlays deliberately use `xino=off`: with encoded 64-bit overlay inodes,
the old 32-bit ARM font stack failed to enumerate device fonts and could crash
Tk font measurement. A private Fontconfig file selects the image's real fonts.
The runtime smoke requires actual mononoki enumeration, measurement and canvas
rendering, and rejects Python/Tcl callback errors. It does not mock fonts.

Each run retains `environment.json` (image/QEMU hashes, versions, source commit,
dirty status/diff hash, wrapper hash, command and exit status), guest input hashes,
`command.log`, writable overlay state and `results/`. A command exit of zero is
reported as `COMMAND_COMPLETED`, not an application test pass: inspect its test
report. A dirty checkout is visible but is not fully reproducible from its commit;
untracked inputs must be retained separately. Use clean revisions for acceptance.
Image hashing can take several minutes before the command starts.

Validated lab inputs: factory image SHA-256
`345114e012aa5e6fedd9d33821dd864ff3800cb657665247760770c0215369e6`,
QEMU 7.2.0 SHA-256
`9f07762a3cd0f8a199cb5471a92402a4765f8e2fcb7fe91a87ee75da9616a806`,
ARM Python 3.8.0 and Tcl/Tk 8.6.5. The image was verified against a streaming
extraction of the supplied factory archive. Image assets are not redistributed
by this repository. Record your own image and emulator identities; the example
`/usr/bin` path is not a version guarantee.

Run the runtime smoke twice with new directories before diagnosing application
failures. The legacy `tools/setup_qemu_env.sh` changes shared machine state and
is not part of this isolated workflow. Existing flow fixtures still need audit;
the new environment does not make their expectations authoritative.
