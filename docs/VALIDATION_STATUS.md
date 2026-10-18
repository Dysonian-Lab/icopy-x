# Validation status and remaining acceptance

Updated 19 October 2026. This is the current acceptance boundary; historical
captures and planning notes are not passes for the latest application.

## Contributor CI

Every PR must pass the three Linux/Windows host jobs and **QEMU critical flows**
with an up-to-date main branch. The build compiles the PM3 client and fullimage,
checks required patches and matching source/platform/FPGA metadata, builds both
IPKs and all desktop companions, and packages complete release candidates.
Required build failures prevent QEMU success. No physical device or private
factory image is needed on a contributor's computer.

The trusted QEMU harness runs the exact built flash and no-flash IPKs under ARM
Python 3.8.0 and Tcl/Tk 8.6.5. Its `critical-v5` profile has 22 cases per variant,
44 combinations. It exercises packaged application code, real Tk/threads,
translation/parsing, Reader behavior, cancellation and selected transport faults.
PM3 responses and hardware boundaries are controlled fixtures. It does not run
the physical PM3, boot the device kernel or prove RF/USB/flashing behavior.

These tests have caught application defects. In [run 39169035713](https://github.com/lab-401/icopy-x/actions/runs/39169035713),
both profiles rejected a late Auto Copy animation after its canvas was destroyed.
[PR #70](https://github.com/lab-401/icopy-x/pull/70) disposes timers, stops owned
workers and rejects late UI callbacks. Eight focused host regressions also cover
the warning that could reopen after exit. The QEMU test was not weakened.

The required profile is a maintained subset. The larger legacy flow inventory
still contains stale navigation and expectation problems and is not certified
as passing. Promote a useful case only after validating its assertions and
proving a relevant negative control. Ordinary contributors should add the
smallest useful host regression; maintainers own shared QEMU fixtures and pins.

## Delivery and physical testing

| Area | Implemented evidence/control | Still required |
| --- | --- | --- |
| PM3 build integrity | [#65](https://github.com/lab-401/icopy-x/pull/65): one source revision, required patches fail closed, client/fullimage/FPGA identity checks | Physical client/firmware acceptance. FPGA bitstreams are verified upstream inputs, not newly synthesized HDL. |
| Ultralight dumps | [#66](https://github.com/lab-401/icopy-x/pull/66): header, UID, length/capacity and partial-data assertions on host/QEMU | Retest the supplied compatible tag; genuine Ultralight/NTAG coverage is separate. An earlier intermittent empty reply remains unexplained. |
| Timeout/cancellation | [#67](https://github.com/lab-401/icopy-x/pull/67): bounded drain, explicit daemon synchronization, no implicit replay; real host TCP and synthetic QEMU faults | Charged-device LF search/read cancellation and successful reuse; hardware abort timing. |
| Auto Copy exit | [#70](https://github.com/lab-401/icopy-x/pull/70): timer disposal and destroyed-activity callback guards | Representative physical read/exit/re-entry acceptance. |
| Maintainer station | [#68](https://github.com/lab-401/icopy-x/pull/68): pinned SSH, snapshots, guarded buttons, exact reference dumps, cancellation/re-entry and durable USB handover | First supervised acceptance of the maintained controller; enroll USB identity, UI strings and client output. |
| Releases | [#69](https://github.com/lab-401/icopy-x/pull/69): complete candidate archives; publication requires exact build/QEMU/host evidence and asset hashes | Maintainer release decision and change-specific physical evidence. A passing release gate is not hardware certification. |
| ICS Decoder | Host protocol regressions and QEMU accessory-absent flow | Accessory unavailable: actual discovery, capture, disconnect and write/readback remain blocked. |
| Factory/no-flash | Separate package/Lua/client family and QEMU command fixtures | Real factory-firmware device acceptance; the current physical device does not establish it. |
| Installation/flashing | Package/build checks and earlier reversible staging observations | Standard update/restart/recovery and fullimage flashing on stable power. Not covered by read or PC-mode passes. |

Earlier experimental sessions read ordinary Classic 1K, EM410x and one
USCUID-UL-compatible tag. Those observations informed regressions; they do not
certify the later runtime changes. No physical test is performed automatically
on a public PR runner. No source badge writes are part of the read cases.

The physical station operates a reviewed, already installed application. It is
not an installer, a full filesystem attestation or a wire-level RF analyzer.
UI-phase cancellation is distinguished from precise PM3 command timing. Missing
hardware and an interrupted session remain explicit gaps, never implicit passes.

## Next supervised session

1. Charge the iCopy-XS and connect its Ethernet hub. Inspect actual installed
   software before choosing an exact reviewed candidate; preserve a verified
   baseline and keep installation/staging separate from the station's tests.
2. Enroll the private machine/profile and Classic 1K reference fixture. Calibrate
   visible text and framebuffer capture; perform one ordinary read.
3. Enter PC mode, replace the hub with a direct USB cable, identify the new port,
   and run the matching hash-pinned host client's `hw version` and `hw status`.
   The serial port speaks PM3 protocol, not a Linux shell. Reject FPGA or client/
   firmware mismatch output even if the command exits zero.
4. Exit PC mode physically, restore the hub, collect logs and perform another
   known-card read. Both USB probes and this return read are required for PASS.
5. If power and time permit, test EM410x timeout/cancel recovery and the supplied
   Ultralight-compatible tag. Record the exact application and firmware for each
   result. Do not flash merely to accumulate test coverage.

Follow [PHYSICAL_TESTING.md](PHYSICAL_TESTING.md) for commands and interruption
recovery. Raw UID/dump/log evidence stays outside Git.

## Maintenance

The weekly build checks upstream PM3 drift even without an open PR. A failure
requires investigation; it must not be bypassed by dropping a patch or accepting
partial assets. The release gate keeps draft assets private until every digest
matches. See [RELEASE_PIPELINE.md](RELEASE_PIPELINE.md) for dry runs and recovery.

The device's old interpreter and pinned runtime remain compatibility constraints;
passing tests do not remove their dependency advisories. Modern test tooling
and dependency maintenance are separate from a device-runtime migration.
The proposed model reviewer remains advisory future work and is not an active
CI check. Human scope review is still required, especially for boot/update,
translation, firmware, Lua selection, USB ownership and build changes.
