import copy
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import shutil
import sys
import zipfile

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'tools/release_ci'))
import bundle
import publish


def run(number, path, event='workflow_run'):
    return dict(id=number, repository=dict(id=7, full_name=publish.REPO), path=path,
                status='completed', conclusion='success', event=event, head_sha='a' * 40, run_attempt=1)


def clients(variant, missing='', bad_commit=False):
    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w') as archive:
        for platform in ('linux', 'windows', 'macos'):
            binary = platform + (('/proxmark3.exe' if variant == 'flash' else '/client.exe')
                                 if platform == 'windows' else '/proxmark3')
            archive.writestr(binary, b'binary')
            if variant == 'flash':
                archive.writestr(platform + '/pm3_client_build.json', json.dumps(dict(
                    source_commit=('d' if bad_commit else 'c') * 40, build_platform='PM3ICOPYX',
                    client_sha256=hashlib.sha256(b'binary').hexdigest())))
            directories = ('lualibs', 'luascripts', 'resources', 'dictionaries') if variant == 'flash' else ('lualibs', 'luascripts')
            for directory in directories:
                if directory != missing:
                    archive.writestr(platform + '/' + directory + '/data', b'data')
    return output.getvalue()


@pytest.mark.parametrize('event,ref,requested,expected', [
    ('pull_request', 'refs/pull/9/merge', 'v1.2.3', ''),
    ('push', 'refs/tags/v1.2.3', '', 'v1.2.3'),
    ('workflow_dispatch', 'refs/heads/main', 'v1.2.3-rc.1', 'v1.2.3-rc.1'),
    ('schedule', 'refs/heads/main', 'v1.2.3', ''),
    ('workflow_dispatch', 'refs/heads/main', '', '')])
def test_release_intent(event, ref, requested, expected):
    assert bundle.release_tag(event, ref, requested) == expected


@pytest.mark.parametrize('tag', ['main', 'v1', 'v1.2.3\nrelease_tag=oops', 'v1.2.3/other', 'v1.2.3; touch file'])
def test_unsafe_or_ambiguous_tag_rejected(tag):
    with pytest.raises(ValueError):
        bundle.release_tag('workflow_dispatch', 'refs/heads/main', tag)


@pytest.fixture
def candidate(tmp_path):
    build = run(42, '.github/workflows/build-ipk.yml', 'pull_request')
    data = {name: name.encode() for name in bundle.ASSETS}
    for variant in ('flash', 'noflash'):
        data['clients-' + variant + '.zip'] = clients(variant)
    manifest = dict(schema=1, build_run_id=42, build_attempt=1, event='pull_request', head_sha='a' * 40,
                    checkout_sha='b' * 40, pm3_commit='c' * 40, pm3_tag='v4.1', release_tag='', assets={})
    tested = dict(build_run_id=42, build_attempt=1, head_sha='a' * 40, event='pull_request',
                  build_conclusion='success', artifacts={})
    for name, value in data.items():
        manifest['assets'][name] = dict(size=len(value), sha256=hashlib.sha256(value).hexdigest())
    for index, variant in enumerate(('flash', 'noflash')):
        tested['artifacts'][variant] = dict(id=100 + index,
            sha256=manifest['assets']['icopy-x-' + variant + '.ipk']['sha256'])
    return dict(build=build, data=data, manifest=manifest, tested=tested)


def archive_candidate(path, candidate):
    with zipfile.ZipFile(path, 'w') as archive:
        for name, data in candidate['data'].items():
            archive.writestr(name, data)
        archive.writestr('release-manifest.json', json.dumps(candidate['manifest']))
    return path


def test_exact_candidate_unpack(candidate, tmp_path):
    path = archive_candidate(tmp_path / 'wrapper.zip', candidate)
    manifest = publish.unpack_candidate(path, tmp_path, candidate['build'], candidate['tested'])
    for name, value in candidate['data'].items():
        assert (tmp_path / name).read_bytes() == value
    assert manifest == candidate['manifest']


@pytest.mark.parametrize('field,value', [('head_sha', 'd' * 40), ('build_attempt', 2),
    ('build_run_id', 43), ('event', 'push'), ('release_tag', 'v1.2.3'), ('checkout_sha', '../bad'),
    ('pm3_commit', 'main'), ('pm3_tag', 'tag\noutput=bad')])
def test_wrong_candidate_identity_rejected(candidate, tmp_path, field, value):
    candidate['manifest'][field] = value
    path = archive_candidate(tmp_path / 'wrapper.zip', candidate)
    with pytest.raises(ValueError):
        publish.unpack_candidate(path, tmp_path, candidate['build'], candidate['tested'])


@pytest.mark.parametrize('change', ['bytes', 'qemu_hash', 'extra_path', 'missing', 'release_checkout'])
def test_artifact_substitution_and_source_mismatch_rejected(candidate, tmp_path, change):
    if change == 'bytes':
        candidate['data']['icopy-x-flash.ipk'] = b'untested'
    elif change == 'qemu_hash':
        candidate['tested']['artifacts']['flash']['sha256'] = 'd' * 64
    elif change == 'extra_path':
        candidate['data']['../outside'] = b'escape'
    elif change == 'missing':
        del candidate['data']['clients-flash.zip']
    else:
        candidate['build']['event'] = candidate['manifest']['event'] = 'workflow_dispatch'
        candidate['manifest']['release_tag'] = 'v1.2.3'
    path = archive_candidate(tmp_path / 'wrapper.zip', candidate)
    with pytest.raises(ValueError):
        publish.unpack_candidate(path, tmp_path, candidate['build'], candidate['tested'])
    assert not (tmp_path.parent / 'outside').exists()


def qemu_records(candidate):
    pin = json.loads((ROOT / 'tools/qemu_ci/runtime_pin.json').read_text())
    expected = json.loads((ROOT / 'tests/qemu_artifact/critical_cases.json').read_text())
    summary = dict(status='PASS', returncode=0, runtime_archive_sha256=pin['archive_sha256'],
                   runtime_manifest_sha256=pin['manifest_sha256'], artifact_sha256={})
    records = {'ipks/build.json': candidate['tested'], 'qemu-run/evidence/summary.json': summary}
    for variant in ('flash', 'noflash'):
        digest = candidate['tested']['artifacts'][variant]['sha256']
        summary['artifact_sha256'][variant] = digest
        records['qemu-run/evidence/' + variant + '.json'] = dict(status='PASS', ipk_sha256=digest,
            guest=dict(critical=dict(profile=expected['version'], status='PASS',
                cases=[dict(id=name, status='PASS') for name in expected['cases']])))
    return records


@pytest.mark.parametrize('change', [None, 'missing_case', 'failed_case', 'old_profile', 'runtime', 'hash', 'worker'])
def test_qemu_requires_complete_current_profiles(candidate, tmp_path, change):
    records = qemu_records(candidate)
    critical = records['qemu-run/evidence/noflash.json']['guest']['critical']
    summary = records['qemu-run/evidence/summary.json']
    if change == 'missing_case':
        critical['cases'].pop()
    elif change == 'failed_case':
        critical['cases'][0]['status'] = 'SKIP'
    elif change == 'old_profile':
        critical['profile'] = 'critical-v1'
    elif change == 'runtime':
        summary['runtime_archive_sha256'] = 'd' * 64
    elif change == 'hash':
        summary['artifact_sha256']['flash'] = 'd' * 64
    elif change == 'worker':
        summary['returncode'] = 1
    path = tmp_path / 'qemu.zip'
    with zipfile.ZipFile(path, 'w') as archive:
        for name, value in records.items():
            archive.writestr(name, json.dumps(value))
    if change:
        with pytest.raises(ValueError):
            publish.evidence(path)
    else:
        assert publish.evidence(path) == candidate['tested']


@pytest.mark.parametrize('change', [None, 'latest_failure', 'missing_job', 'skipped', 'wrong_workflow'])
def test_all_latest_host_jobs_required(change):
    host = run(51, '.github/workflows/test-host.yml', 'push')
    rows = [dict(name=name, status='completed', conclusion='success') for name in publish.HOST_JOBS]
    runs = [host]
    if change == 'latest_failure':
        runs.append(dict(host, id=52, conclusion='failure'))
    elif change == 'missing_job':
        rows.pop()
    elif change == 'skipped':
        rows[0]['conclusion'] = 'skipped'
    elif change == 'wrong_workflow':
        host['path'] = '.github/workflows/other.yml'
    def fetch(path):
        if '/jobs?' in path:
            return dict(total_count=len(rows), jobs=rows)
        return dict(total_count=len(runs), workflow_runs=runs)
    if change:
        with pytest.raises(ValueError):
            publish.host_tests('a' * 40, fetch)
    else:
        assert publish.host_tests('a' * 40, fetch) == 51


@pytest.mark.parametrize('unmerged,wrong_tag', [(False, False), (True, False), (False, True)])
def test_release_requires_merged_source_and_matching_tag(unmerged, wrong_tag):
    def fetch(path, **kwargs):
        if path.startswith('compare/'):
            return dict(status='ahead', merge_base_commit=dict(sha=('b' if unmerged else 'a') * 40))
        if path.startswith('commits/'):
            return dict(sha=('b' if wrong_tag else 'a') * 40)
        return {'ref': 'tag'}
    if unmerged or wrong_tag:
        with pytest.raises(ValueError):
            publish.release_source(dict(head_sha='a' * 40, release_tag='v1.2.3'), fetch)
    else:
        publish.release_source(dict(head_sha='a' * 40, release_tag='v1.2.3'), fetch)


@pytest.mark.parametrize('change', [dict(expired=True), dict(workflow_run={'id': 999}), dict(size_in_bytes=0)])
def test_expired_or_wrong_artifact_refused(change):
    build = run(42, '.github/workflows/build-ipk.yml')
    item = dict(name='release-candidate', id=4, size_in_bytes=100, expired=False,
                workflow_run=dict(id=42, head_sha='a' * 40, repository_id=7))
    item.update(change)
    with pytest.raises(ValueError):
        publish.artifact(build, 'release-candidate', lambda _: dict(total_count=1, artifacts=[item]))


def test_manual_dispatch_cannot_publish_even_with_flag(monkeypatch, tmp_path):
    monkeypatch.setenv('GITHUB_EVENT_NAME', 'workflow_dispatch')
    monkeypatch.setenv('GITHUB_REF', 'refs/heads/main')
    with pytest.raises(ValueError, match='automatic trusted-main'):
        publish.publish({}, {}, tmp_path)


@pytest.mark.parametrize('variant,missing,bad_commit', [('flash', '', False), ('noflash', '', False),
    ('flash', 'resources', False), ('flash', 'dictionaries', False), ('noflash', 'lualibs', False),
    ('flash', '', True)])
def test_companion_completeness_and_shared_source(tmp_path, variant, missing, bad_commit):
    path = tmp_path / 'clients.zip'
    path.write_bytes(clients(variant, missing, bad_commit))
    if missing or bad_commit:
        with pytest.raises(ValueError):
            bundle.check_clients(path, variant, 'c' * 40)
    else:
        bundle.check_clients(path, variant, 'c' * 40)


@pytest.mark.parametrize('upload_bad', [False, True])
def test_draft_published_only_after_all_asset_hashes_verified(candidate, tmp_path, monkeypatch, upload_bad):
    manifest = candidate['manifest']
    manifest.update(release_tag='v1.2.3', event='push', checkout_sha='a' * 40)
    for name, value in candidate['data'].items():
        (tmp_path / name).write_bytes(value)
    (tmp_path / 'release-manifest.json').write_text(json.dumps(manifest))
    report = dict(build_run_id=42, qemu_run_id=88, host_run_id=51,
                  manifest_sha256=publish.sha256(tmp_path / 'release-manifest.json'))
    (tmp_path / 'validation.json').write_text(json.dumps(report))
    monkeypatch.setenv('GITHUB_EVENT_NAME', 'workflow_run')
    monkeypatch.setenv('GITHUB_REF', 'refs/heads/main')
    monkeypatch.setattr(publish, 'release_source', lambda _: None)
    calls, assets = [], []
    def api(path, payload=None, **kwargs):
        calls.append((path, payload))
        if path.startswith('releases/tags/'):
            return None
        if path.endswith('/assets?per_page=100'):
            return assets
        if path == 'releases':
            assert payload['draft']
            return dict(id=7, draft=True)
        assert path == 'releases/7' and payload == dict(draft=False)
    def upload(args, **kwargs):
        path = Path(args[4])
        assets.append(dict(name=path.name, size=path.stat().st_size,
                           digest='bad' if upload_bad else 'sha256:' + publish.sha256(path)))
    monkeypatch.setattr(publish, 'api', api)
    monkeypatch.setattr(publish.subprocess, 'run', upload)
    if upload_bad:
        with pytest.raises(ValueError, match='draft retained'):
            publish.publish(manifest, report, tmp_path)
        assert not any(path == 'releases/7' for path, _ in calls)
    else:
        publish.publish(manifest, report, tmp_path)
        assert calls[-1] == ('releases/7', dict(draft=False))


def test_build_workflow_has_no_write_token_or_release_action():
    source = (ROOT / '.github/workflows/build-ipk.yml').read_text()
    assert 'contents: write' not in source
    assert 'softprops/action-gh-release' not in source
    assert 'ref: ${{ inputs.release_tag }}' not in source
    assert 'ref: ${{ github.sha }}' in source
    assert 'name: release-candidate' in source


@pytest.mark.parametrize('change', [None, 'rerun', 'replaced_ipk', 'failed_build', 'failed_qemu'])
def test_complete_gate_against_archives_and_api_identity(candidate, tmp_path, monkeypatch, change):
    qemu = run(88, '.github/workflows/qemu-critical.yml')
    host = run(51, '.github/workflows/test-host.yml', 'push')
    candidate_zip = archive_candidate(tmp_path / 'candidate-wrapper.zip', candidate)
    qemu_zip = tmp_path / 'evidence-wrapper.zip'
    with zipfile.ZipFile(qemu_zip, 'w') as archive:
        for name, value in qemu_records(candidate).items():
            archive.writestr(name, json.dumps(value))
    build = candidate['build']
    if change == 'rerun':
        build['run_attempt'] = 2
    if change == 'failed_build':
        build['conclusion'] = 'failure'
    if change == 'failed_qemu':
        qemu['conclusion'] = 'failure'
    def item(name, number, owner):
        return dict(name=name, id=number, size_in_bytes=100, expired=False,
                    workflow_run=dict(id=owner['id'], head_sha=owner['head_sha'], repository_id=7))
    rows = [item('icopy-x-flash-ipk', 199 if change == 'replaced_ipk' else 100, build),
            item('icopy-x-noflash-ipk', 101, build), item('release-candidate', 103, build)]
    def fetch(path):
        if path == 'actions/runs/88':
            return qemu
        if path == 'actions/runs/42':
            return build
        if path == 'actions/runs/88/artifacts?per_page=100':
            return dict(total_count=1, artifacts=[item('critical-qemu-evidence', 102, qemu)])
        if path == 'actions/runs/42/artifacts?per_page=100':
            return dict(total_count=3, artifacts=rows)
        if path.startswith('actions/workflows/test-host.yml/runs?'):
            return dict(total_count=1, workflow_runs=[host])
        if path == 'actions/runs/51/attempts/1/jobs?per_page=100':
            return dict(total_count=3, jobs=[dict(name=name, status='completed', conclusion='success')
                                            for name in publish.HOST_JOBS])
        pytest.fail('Unexpected API call: ' + path)
    def download(item, path):
        shutil.copyfile(qemu_zip if item['id'] == 102 else candidate_zip, path)
        return path
    monkeypatch.setattr(publish, 'api', fetch)
    monkeypatch.setattr(publish, 'download', download)
    if change:
        with pytest.raises(ValueError):
            publish.validate(88, tmp_path / 'out')
        assert not (tmp_path / 'out/validation.json').exists()
    else:
        manifest, report = publish.validate(88, tmp_path / 'out')
        assert report['status'] == 'PASS'
        assert report['build_run_id'] == 42 and report['host_run_id'] == 51
        assert not manifest['release_tag']
