# Test dependencies and the Python 3.8 boundary

The device interpreter remains Python 3.8. CI deliberately retains Linux and
Windows 3.8 jobs to catch syntax/import/runtime incompatibility. Their pytest
8.3.5 and Pillow 10.4.0 are the compatible legacy pins. They are **not free of
known advisories** and should not be the default general-purpose tools on a
maintainer's workstation. The factory QEMU image also remains an old, pinned
runtime; changing its libraries would change the compatibility target.

Python 3.10+ host environments use pytest 9.0.3 and Pillow 12.3.0. These include
the fixes identified in the repository's October 2026 dependency alerts. The
maintainer physical station requires Python 3.10+ and the patched Pillow pin.
None of these test requirements are installed into the IPK or onto the device.

The 3.8 jobs run on disposable GitHub-hosted machines with read-only repository
permission and no physical-device or private-QEMU credential. Their pytest base
directory is under the runner's temporary directory instead of the predictable
shared `/tmp/pytest-of-user` namespace. The candidate QEMU process has its own
offline namespace and bounded resources; see [QEMU CI](QEMU_CI.md).

Prefer Python 3.12 for local development. If reproducing a 3.8-only failure, use
an isolated disposable environment and trusted fixtures, without unrelated
credentials or data mounted into it. Do not feed arbitrary images into a legacy
Pillow environment as a general image-processing service. These restrictions
reduce exposure; they do not fix the legacy library vulnerabilities.

Dependabot checks workflow and test/station requirements weekly. Dependency PRs
must pass the same host/build/QEMU gates and preserve the interpreter markers.
Do not dismiss the remaining legacy alerts as fixed: removing them requires
reviewed backports or a separate device-runtime migration. Neither is part of a
test-tool upgrade. New renderer results must be assessed on both the modern host
and the factory runtime, not blessed merely because a newer library changed them.

Upstream references: [Pillow 12.3 security fixes](https://pillow.readthedocs.io/en/stable/releasenotes/12.3.0.html),
[Pillow Python support](https://pillow.readthedocs.io/en/stable/installation/python-support.html),
and [pytest 9.0.3](https://docs.pytest.org/en/stable/announce/release-9.0.3.html).
