"""Trusted release gate. Candidate archives are data, never executable code.

Default operation validates only. --publish is restricted to workflow_run on
main, after a successful automatic QEMU run. Existing assets are never replaced.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import zipfile

from bundle import ASSETS, TAG, check_clients, sha256

REPO = 'lab-401/icopy-x'
ROOT = Path(__file__).resolve().parents[2]
HOST_JOBS = {'Host tests (ubuntu-24.04, Python 3.8)',
             'Host tests (ubuntu-24.04, Python 3.12)',
             'Host tests (windows-latest, Python 3.8)'}
MAX_ARCHIVE = 384 * 1024**2
MAX_ASSET = 128 * 1024**2


def api(path, payload=None, method='POST', missing=False):
    args = ['gh', 'api', 'repos/' + REPO + '/' + path]
    if payload is not None:
        args += ['--method', method, '--input', '-']
    result = subprocess.run(args, input=json.dumps(payload) if payload is not None else None,
                            capture_output=True, text=True, timeout=60)
    if result.returncode:
        if missing and 'HTTP 404' in result.stderr:
            return None
        raise RuntimeError(result.stderr)
    return json.loads(result.stdout)


def successful_run(run, path, events):
    if (run.get('repository', {}).get('full_name') != REPO or run.get('path') != path or
            run.get('status') != 'completed' or run.get('conclusion') != 'success' or
            run.get('event') not in events or not re.fullmatch('[0-9a-f]{40}', run.get('head_sha', ''))):
        raise ValueError('Wrong, failed or incomplete workflow run: ' + path)


def artifact(run, name, fetch=None):
    fetch = fetch or api
    response = fetch('actions/runs/' + str(run['id']) + '/artifacts?per_page=100')
    if response['total_count'] > 100:
        raise ValueError('Unexpected artifact inventory size')
    matches = [item for item in response['artifacts'] if item['name'] == name]
    if len(matches) != 1:
        raise ValueError('Missing or ambiguous artifact: ' + name)
    result = matches[0]
    identity = result.get('workflow_run', {})
    if (result.get('expired') or not 0 < result['size_in_bytes'] < MAX_ARCHIVE or
            identity.get('id') != run['id'] or identity.get('head_sha') != run['head_sha'] or
            identity.get('repository_id') != run['repository']['id']):
        raise ValueError('Artifact identity or availability mismatch: ' + name)
    return result


def download(item, path):
    with path.open('xb') as stream:
        subprocess.run(['gh', 'api', 'repos/' + REPO + '/actions/artifacts/' + str(item['id']) + '/zip'],
                       stdout=stream, check=True, timeout=180)
    if path.stat().st_size > MAX_ARCHIVE:
        raise ValueError('Downloaded archive exceeds limit')
    return path


def entry(archive, name, limit):
    matches = [item for item in archive.infolist() if item.filename == name]
    if len(matches) != 1:
        raise ValueError('Missing or duplicate archive entry: ' + name)
    item = matches[0]
    if (item.is_dir() or stat.S_IFMT(item.external_attr >> 16) not in (0, stat.S_IFREG) or
            not 0 < item.file_size <= limit):
        raise ValueError('Unsafe/oversized archive entry: ' + name)
    return archive.read(item)


def evidence(path):
    with zipfile.ZipFile(path) as archive:
        build = json.loads(entry(archive, 'ipks/build.json', 1024**2))
        summary = json.loads(entry(archive, 'qemu-run/evidence/summary.json', 1024**2))
        expected = json.loads((ROOT / 'tests/qemu_artifact/critical_cases.json').read_text())
        if summary.get('status') != 'PASS' or summary.get('returncode') != 0:
            raise ValueError('QEMU worker did not pass')
        pin = json.loads((ROOT / 'tools/qemu_ci/runtime_pin.json').read_text())
        for field in ('archive', 'manifest'):
            if summary.get('runtime_' + field + '_sha256') != pin[field + '_sha256']:
                raise ValueError('QEMU runtime pin differs from trusted policy')
        for variant in ('flash', 'noflash'):
            package = json.loads(entry(archive, 'qemu-run/evidence/' + variant + '.json', 1024**2))
            critical = package.get('guest', {}).get('critical', {})
            rows = critical.get('cases', [])
            digest = build['artifacts'][variant]['sha256']
            if (not re.fullmatch('[0-9a-f]{64}', digest) or summary['artifact_sha256'][variant] != digest or
                    package.get('ipk_sha256') != digest or package.get('status') != 'PASS' or
                    critical.get('profile') != expected['version'] or critical.get('status') != 'PASS' or
                    [row.get('id') for row in rows] != list(expected['cases']) or
                    any(row.get('status') != 'PASS' for row in rows)):
                raise ValueError('Incomplete, stale or mismatched QEMU evidence: ' + variant)
    return build


def unpack_candidate(path, output, build, tested):
    with zipfile.ZipFile(path) as archive:
        if sorted(archive.namelist()) != sorted(ASSETS + ('release-manifest.json',)):
            raise ValueError('Unexpected candidate archive contents')
        raw = entry(archive, 'release-manifest.json', 65536)
        manifest = json.loads(raw)
        if (manifest.get('schema') != 1 or manifest.get('build_run_id') != build['id'] or
                manifest.get('build_attempt') != build['run_attempt'] or manifest.get('event') != build['event'] or
                manifest.get('head_sha') != build['head_sha'] or
                set(manifest.get('assets', {})) != set(ASSETS) or
                not re.fullmatch('[0-9a-f]{40}', manifest.get('checkout_sha', '')) or
                not re.fullmatch('[0-9a-f]{40}', manifest.get('pm3_commit', '')) or
                not re.fullmatch('[A-Za-z0-9_.+-]{1,80}', manifest.get('pm3_tag', ''))):
            raise ValueError('Candidate manifest identity mismatch')
        tag = manifest.get('release_tag')
        if not isinstance(tag, str) or (tag and not TAG.fullmatch(tag)):
            raise ValueError('Invalid release intent')
        if build['event'] != 'pull_request' and manifest['checkout_sha'] != build['head_sha']:
            raise ValueError('Build checked out another revision')
        if build['event'] == 'pull_request' and tag:
            raise ValueError('PR artifacts cannot request a release')
        for name in ASSETS:
            data = entry(archive, name, MAX_ASSET)
            identity = manifest['assets'][name]
            if len(data) != identity['size'] or hashlib.sha256(data).hexdigest() != identity['sha256']:
                raise ValueError('Asset digest/size mismatch: ' + name)
            if name.endswith('.ipk'):
                variant = 'noflash' if 'noflash' in name else 'flash'
                if identity['sha256'] != tested['artifacts'][variant]['sha256']:
                    raise ValueError('Release IPK was not the QEMU-tested IPK')
            (output / name).write_bytes(data)
        (output / 'release-manifest.json').write_bytes(raw)
    for variant in ('flash', 'noflash'):
        check_clients(output / ('clients-' + variant + '.zip'), variant, manifest['pm3_commit'])
    return manifest


def host_tests(head, fetch=None):
    fetch = fetch or api
    result = fetch('actions/workflows/test-host.yml/runs?head_sha=' + head + '&per_page=100')
    runs = [r for r in result['workflow_runs'] if r['head_sha'] == head and
            r['event'] in ('pull_request', 'push', 'workflow_dispatch')]
    if not runs or result['total_count'] > 100:
        raise ValueError('Missing or ambiguous host-test history for exact source')
    latest = max(runs, key=lambda r: r['id'])
    successful_run(latest, '.github/workflows/test-host.yml', ('pull_request', 'push', 'workflow_dispatch'))
    jobs = fetch('actions/runs/' + str(latest['id']) + '/attempts/' + str(latest['run_attempt']) + '/jobs?per_page=100')
    rows = jobs['jobs']
    if jobs['total_count'] != len(HOST_JOBS) or {j['name'] for j in rows} != HOST_JOBS or any(
            j['status'] != 'completed' or j['conclusion'] != 'success' for j in rows):
        raise ValueError('All required host jobs must pass without skips')
    return latest['id']


def release_source(manifest, fetch=None):
    fetch = fetch or api
    head, tag = manifest['head_sha'], manifest['release_tag']
    comparison = fetch('compare/' + head + '...main')
    if comparison.get('merge_base_commit', {}).get('sha') != head or comparison['status'] not in ('ahead', 'identical'):
        raise ValueError('Release source has not been incorporated into main')
    ref = fetch('git/ref/tags/' + tag, missing=True)
    if ref is not None and fetch('commits/' + tag)['sha'] != head:
        raise ValueError('Release tag points at a different source revision')


def validate(qemu_id, output):
    qemu = api('actions/runs/' + str(qemu_id))
    if qemu['id'] != qemu_id:
        raise ValueError('Wrong QEMU run ID')
    successful_run(qemu, '.github/workflows/qemu-critical.yml', ('workflow_run', 'workflow_dispatch'))
    output.mkdir(parents=True, exist_ok=False)
    tested = evidence(download(artifact(qemu, 'critical-qemu-evidence'), output / 'qemu.zip'))
    if type(tested['build_run_id']) is not int or tested['build_run_id'] < 1:
        raise ValueError('Invalid tested build ID')
    build = api('actions/runs/' + str(tested['build_run_id']))
    successful_run(build, '.github/workflows/build-ipk.yml', ('pull_request', 'push', 'workflow_dispatch'))
    if (tested['build_run_id'] != build['id'] or tested['head_sha'] != build['head_sha'] or tested['build_attempt'] != build['run_attempt'] or
            tested['event'] != build['event'] or tested['build_conclusion'] != 'success'):
        raise ValueError('QEMU evidence belongs to another build/attempt')
    # A rerun replaces artifacts. Even identical bytes cannot reuse stale IDs.
    for variant in ('flash', 'noflash'):
        item = artifact(build, 'icopy-x-' + variant + '-ipk')
        if item['id'] != tested['artifacts'][variant]['id']:
            raise ValueError('QEMU tested a superseded IPK artifact')
    manifest = unpack_candidate(download(artifact(build, 'release-candidate'), output / 'candidate.zip'),
                                output, build, tested)
    host_id = host_tests(build['head_sha'])
    if manifest['release_tag']:
        if qemu['event'] != 'workflow_run' or build['event'] not in ('push', 'workflow_dispatch'):
            raise ValueError('Release requires automatic QEMU validation of a non-PR build')
        release_source(manifest)
    report = dict(status='PASS', build_run_id=build['id'], qemu_run_id=qemu_id,
                  host_run_id=host_id, head_sha=build['head_sha'], release_tag=manifest['release_tag'],
                  manifest_sha256=sha256(output / 'release-manifest.json'))
    (output / 'validation.json').write_text(json.dumps(report, indent=2) + '\n')
    return manifest, report


def publish(manifest, report, output):
    if os.environ.get('GITHUB_EVENT_NAME') != 'workflow_run' or os.environ.get('GITHUB_REF') != 'refs/heads/main':
        raise ValueError('Publication is restricted to the automatic trusted-main workflow')
    tag = manifest['release_tag']
    if not tag:
        raise ValueError('Build has no release intent')
    release_source(manifest)  # recheck immediately before changing GitHub state
    marker = '<!-- icopy-release build={build_run_id} manifest={manifest_sha256} -->'.format(**report)
    notes = (Path(__file__).with_name('notes.md')).read_text(encoding='utf-8').format(
        tag=tag, pm3_tag=manifest['pm3_tag'])
    notes += '\nValidated build [{0}](https://github.com/{1}/actions/runs/{0}), '.format(report['build_run_id'], REPO)
    notes += '[QEMU evidence](https://github.com/{}/actions/runs/{}) and '.format(REPO, report['qemu_run_id'])
    notes += '[host tests](https://github.com/{}/actions/runs/{}).\n'.format(REPO, report['host_run_id'])
    notes += '\nQEMU validates packaged software, not RF, accessory hardware or firmware flashing.\n\n' + marker
    release = api('releases/tags/' + tag, missing=True)
    if release is None:
        release = api('releases', dict(tag_name=tag, target_commitish=manifest['head_sha'],
            name='iCopy-X ' + tag, body=notes, draft=True, prerelease='-' in tag))
    elif marker not in (release.get('body') or '') or release.get('target_commitish') != manifest['head_sha']:
        raise ValueError('Existing release is not owned by this validated build; refusing replacement')
    # A partial draft can be resumed, but no existing asset may be overwritten.
    expected = dict(manifest['assets'])
    for name in ('release-manifest.json', 'validation.json'):
        path = output / name
        expected[name] = dict(sha256=sha256(path), size=path.stat().st_size)
    current = api('releases/' + str(release['id']) + '/assets?per_page=100')
    if len(current) > len(expected) or len({a['name'] for a in current}) != len(current):
        raise ValueError('Unexpected/duplicate existing release assets')
    present = set()
    for asset in current:
        name = asset['name']
        if name not in expected or asset.get('digest') != 'sha256:' + expected[name]['sha256'] or asset['size'] != expected[name]['size']:
            raise ValueError('Existing release asset differs; refusing replacement')
        present.add(name)
    missing = sorted(set(expected) - present)
    if missing and not release['draft']:
        raise ValueError('Published release is incomplete; refusing mutation')
    for name in missing:
        subprocess.run(['gh', 'release', 'upload', tag, str(output / name), '--repo', REPO],
                       check=True, timeout=180)
    # Verify uploaded bytes through GitHub's asset digests before exposing draft.
    current = api('releases/' + str(release['id']) + '/assets?per_page=100')
    if (len(current) != len(expected) or {a['name'] for a in current} != set(expected) or any(
            a.get('digest') != 'sha256:' + expected[a['name']]['sha256'] or
            a['size'] != expected[a['name']]['size'] for a in current)):
        raise ValueError('Uploaded release assets failed digest verification; draft retained')
    release_source(manifest)
    if release['draft']:
        api('releases/' + str(release['id']), dict(draft=False), method='PATCH')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--qemu-run-id', type=int, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--publish', action='store_true')
    args = parser.parse_args()
    if args.qemu_run_id < 1:
        parser.error('Invalid run ID')
    manifest, report = validate(args.qemu_run_id, args.output)
    if os.environ.get('GITHUB_OUTPUT'):
        with open(os.environ['GITHUB_OUTPUT'], 'a') as stream:
            stream.write('release_tag=' + manifest['release_tag'] + '\n')
    if args.publish:
        publish(manifest, report, args.output)
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
