# Host tests and validation boundaries

The default pytest suite (`tests/ui`) runs on a development computer. It does
not require an iCopy-XS, a USB decoder, or QEMU. The separate `Host tests`
workflow runs it for PRs and main pushes on Linux/Python 3.8 and 3.12, and
Windows/Python 3.8. The IPK build/release workflow is unchanged.

## Running the suite

Use an isolated environment; Python 3.8 matches the device interpreter:

```text
python -m venv ../icopy-x-host-tests-env
# Activate the environment using your shell's activation script.
python -m pip install -r tests/requirements-host.txt
python -m pytest tests/ui
```

Alternatively, with uv installed:

```text
uv run --no-project --python 3.8 --with-requirements tests/requirements-host.txt python -m pytest tests/ui
```

CI disables automatic loading of unrelated pytest plugins. Use
`PYTEST_DISABLE_PLUGIN_AUTOLOAD=1` locally if your environment injects plugins.
The declared dependencies are pinned to versions supporting Python 3.8.

## What these tests exercise

- UI widgets, key dispatch, activity navigation and callback-driven transitions
  on an in-memory `MockCanvas`.
- Factory command translation, factory response normalization to the canonical
  Iceman format, and Iceman bypass. Raw Iceman trace fixtures are retained.
- Focused decoder, launch, packaging-contract, plugin and process-cleanup tests.

Activity and host UI integration tests use a deferred worker scheduler. A test
must explicitly run a worker when checking its output, with device dependencies
faked first. Those directories reject real shell/process launches. Read/Auto
Copy tests use spec-checked Scanner/Reader doubles and deliver results through
the callbacks the real activities register. No fixture installs substitute
application decisions or revives removed activity methods.

`MockCanvas.advance(milliseconds)` runs queued UI callbacks in virtual time.
It does not sleep, and preserves callback ordering and delays. This lets tests
check the real progress-completion animations before asserting later states.

The state-machine tests use small checked-in JSON fixtures for the retained
generic state-machine API. They do not claim the current presentation-only
screen JSON files define the live application's navigation.

## What a pass does not establish

- Deferred UI workers do not validate concurrency, cancellation races or reader
  fallback timeouts. Separate process-cleanup tests do exercise real Python
  threads with fake child processes.
- MockCanvas does not validate real Tk rendering, display timing or screenshots.
- Tests with controlled serial/PM3 responses do not validate real USB discovery,
  RFID reads/writes, accessory firmware, or physical flash/no-flash behavior.
- The host suite does not build an ARM package, install an update, validate the
  installed service chain or test restart/power-loss recovery.
- `tests/ui/integration/test_qemu_parity.py` is a host test of bridge/registry
  contracts. Its name does not mean a QEMU run occurred.
- Other scripts elsewhere under `tests/` are not part of this pytest command.

The generic PWR-exit test excludes ButtonTestActivity because PWR is a diagnostic
input there. Its button behavior has separate activity tests. No failing test is
marked expected-failure to obtain a green host suite.

## Next validation layers

1. Keep the IPK build/package checks, including separate flash/no-flash contents.
2. Run `tests/flows` in the project's Linux/QEMU environment with the correct
   ARM root filesystem, Python 3.8, Xvfb and PM3 response fixtures. Review the
   environment-specific paths in `tools/setup_qemu_env.sh` before using it.
3. Perform device acceptance on both firmware configurations: USB attach/detach,
   decoder read/write/readback, ordinary PM3 operation afterward, and update and
   restart paths. Preserve observed results and firmware identities.

Host CI is a regression control, not a whole-project health certificate. The
proposed model reviewer and repository branch-protection policy are separate
work; this workflow neither deploys that reviewer nor enables automatic merging.
