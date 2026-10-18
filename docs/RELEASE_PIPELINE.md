# Validated release pipeline

Every PR builds both IPKs and all three desktop clients with a read-only token.
The build resolves one immutable upstream PM3 commit, applies required patches
fail-closed, checks the client/firmware/FPGA contract, packages companion archives,
and uploads a `release-candidate` with SHA-256 identities for all four assets.
Iceman and factory Lua stay in their separate companion archives. Missing client
data, binaries or Iceman build metadata fails packaging rather than shipping a
partial archive. The candidate bundle is built on PRs, not first discovered at
release time.

Publication follows this chain:

1. **Build iCopy-X IPK** completes successfully. It cannot create tags/releases.
2. **Critical QEMU validation** downloads those exact IPKs into an offline worker
   and requires all current critical cases in both profiles. A trusted job posts
   `QEMU critical flows` against the still-current contributor head.
3. **Publish validated release** reads the successful QEMU run's bounded evidence,
   follows it back to the original build ID/attempt/artifact IDs, and checks the
   release IPK hashes equal the tested hashes. It also requires the latest host
   run for that exact source and all three matrix jobs to pass without skips.
4. For a build with release intent, a separate job in `validated-release`
   repeats validation with release-writing permission. Source must already be in
   `main`; an existing tag must resolve to precisely that commit. It creates a
   draft, uploads the exact tested assets, verifies GitHub's asset digests and
   only then publishes. It never rebuilds or replaces existing assets.

This is a three-workflow chain, within GitHub's supported `workflow_run` depth.
PR code and downloaded artifacts are never executed by the release publisher.
The build/host jobs are ordinary unprivileged contributor jobs; QEMU's private
runtime credential stays in its existing restricted environment.

## Cutting a release

Merge the reviewed PRs and wait for main's host tests. Either push a version tag
on the intended main commit, or dispatch **Build iCopy-X IPK** at that exact
revision with `release_tag=vMAJOR.MINOR.PATCH`. A `-suffix` creates a prerelease.
An empty release tag is build-only. If the requested tag already exists on an
older commit, dispatch at that tag; the workflow refuses to silently build a
different tree from its reported SHA. New manual tags are created by the release
API only after validation, not by a privileged preliminary build job.

The automatic QEMU run is mandatory for publication. Manually running an altered
QEMU harness is useful during a harness review but cannot authorize a release.
The host/QEMU checks do not replace any physical acceptance required by the
change. Document outstanding hardware limitations in the release decision.

Configure the GitHub environment **validated-release** to allow only the `main`
branch. No additional token or secret is required: the publishing job uses its
scoped GitHub token. Keep the existing main branch requirements for the three
host jobs and `QEMU critical flows`, with GitHub Actions as the expected source.

## Testing and recovering the pipeline

Dispatch **Publish validated release** with a successful `qemu_run_id` to run the
complete validation path without publishing anything. Manual dispatch never
enters the write job, even for a real release candidate. During workflow review,
this dry run can use the reviewed maintainer branch. The corresponding build
must contain the new candidate bundle; historical builds did not upload one.

`tests/release_ci` runs on every PR. Negative controls reject stale build attempts,
replaced artifacts, wrong IPKs, missing/skipped QEMU cases, old profiles/runtime
pins, incomplete companions, failed/missing host jobs, unmerged source and tag
mismatches. Publication tests require every digest to match before a draft is
made public. Live dry runs validate GitHub integration; no test release is needed.

If a job fails, inspect its failed check and retained `release-validation` record.
An interrupted upload leaves a draft. Rerun the **same publisher run**: it may
resume a draft owned by the same build/manifest, but only if every existing asset
already matches. A conflicting or manually modified draft/release requires
maintainer inspection; the automation refuses overwrite. A failure before the
validation record is complete leaves the exception in the job log, not a PASS.

Artifacts/evidence expire after 14 days. Expiration blocks publication; build and
validate again. A build rerun changes artifact identities and requires fresh QEMU
evidence. A superseded QEMU case profile requires fresh validation too. Concurrent
different releases do not share a cancellation group; writes for one tag are
serialized. No passing result is manufactured for a failed upstream build.

The four public assets are accompanied by `release-manifest.json` and
`validation.json`, which link source, PM3 source, build, host/QEMU runs and hashes.
These records demonstrate what CI validated. They are not a claim that macOS/
Windows companion clients, physical RF, installation, flashing or ICS Decoder
hardware were exercised by QEMU.
