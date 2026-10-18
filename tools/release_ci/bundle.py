"""Record release intent and exact assets in the read-only build workflow."""
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import zipfile

ASSETS = ('icopy-x-flash.ipk', 'icopy-x-noflash.ipk', 'clients-flash.zip', 'clients-noflash.zip')
TAG = re.compile(r'v[0-9]+\.[0-9]+\.[0-9]+(?:[-+][A-Za-z0-9.-]+)?\Z')


def release_tag(event, ref, requested):
    tag = requested if event == 'workflow_dispatch' else ref[10:] if event == 'push' and ref.startswith('refs/tags/') else ''
    if tag and not TAG.fullmatch(tag):
        raise ValueError('Release tag must be a vMAJOR.MINOR.PATCH version')
    return tag


def sha256(path):
    result = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(65536), b''):
            result.update(block)
    return result.hexdigest()


def check_clients(path, variant, pm3_commit):
    """Fail on incomplete companion packages; keep each Lua family separate."""
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        if len(names) != len(set(names)) or any(name.startswith('/') or '\\' in name or ':' in name or
                '..' in name.split('/') for name in names) or any(
                stat.S_IFMT(item.external_attr >> 16) not in (0, stat.S_IFREG, stat.S_IFDIR)
                for item in archive.infolist()):
            raise ValueError('Unsafe or duplicate companion path')
        for platform in ('linux', 'windows', 'macos'):
            prefix = platform + '/'
            binary = prefix + (('proxmark3.exe' if variant == 'flash' else 'client.exe')
                               if platform == 'windows' else 'proxmark3')
            if binary not in names or not 0 < archive.getinfo(binary).file_size < 64 * 1024**2:
                raise ValueError('Missing/oversized companion binary: ' + binary)
            directories = ('lualibs', 'luascripts', 'resources', 'dictionaries') if variant == 'flash' else ('lualibs', 'luascripts')
            for directory in directories:
                if not any(name.startswith(prefix + directory + '/') and
                           not name.endswith('/') and archive.getinfo(name).file_size > 0 for name in names):
                    raise ValueError('Missing companion data: ' + prefix + directory)
            if variant == 'flash':
                metadata = prefix + 'pm3_client_build.json'
                if metadata not in names or archive.getinfo(metadata).file_size > 65536:
                    raise ValueError('Missing/oversized companion build metadata')
                record = json.loads(archive.read(metadata))
                if (record.get('source_commit') != pm3_commit or record.get('build_platform') != 'PM3ICOPYX' or
                        record.get('client_sha256') != hashlib.sha256(archive.read(binary)).hexdigest()):
                    raise ValueError('Companion binary/source identity mismatch')


def main():
    if sys.argv[1:] == ['intent']:
        tag = release_tag(os.environ['GITHUB_EVENT_NAME'], os.environ['GITHUB_REF'],
                          os.environ.get('RELEASE_TAG_INPUT', ''))
        if tag:
            # An existing tag may never silently select a different source tree.
            result = subprocess.run(['gh', 'api', 'repos/lab-401/icopy-x/git/ref/tags/' + tag],
                                    text=True, capture_output=True, timeout=60)
            if result.returncode:
                if 'HTTP 404' not in result.stderr:
                    raise RuntimeError(result.stderr)
            else:
                resolved = subprocess.run(['gh', 'api', 'repos/lab-401/icopy-x/commits/' + tag,
                                           '--jq', '.sha'], text=True, capture_output=True,
                                          timeout=60, check=True).stdout.strip()
                if resolved != os.environ['GITHUB_SHA']:
                    raise ValueError('Existing release tag differs from selected workflow revision')
        with open(os.environ['GITHUB_OUTPUT'], 'a') as output:
            output.write('release_tag=' + tag + '\n')
        return
    if sys.argv[1:] != ['manifest']:
        raise ValueError('Expected intent or manifest')
    event = json.loads(Path(os.environ['GITHUB_EVENT_PATH']).read_text())
    tag = os.environ.get('RELEASE_TAG', '')
    if tag and not TAG.fullmatch(tag):
        raise ValueError('Invalid release tag')
    source = subprocess.run(['git', 'rev-parse', 'HEAD'], capture_output=True, text=True,
                            check=True, timeout=10).stdout.strip()
    if source != os.environ['GITHUB_SHA']:
        raise ValueError('Checkout differs from workflow revision')
    record = dict(schema=1, build_run_id=int(os.environ['GITHUB_RUN_ID']),
        build_attempt=int(os.environ['GITHUB_RUN_ATTEMPT']), event=os.environ['GITHUB_EVENT_NAME'],
        head_sha=event.get('pull_request', {}).get('head', {}).get('sha', source),
        checkout_sha=source, release_tag=tag, pm3_tag=os.environ['PM3_TAG'],
        pm3_commit=os.environ['PM3_COMMIT'], assets={})
    for variant in ('flash', 'noflash'):
        check_clients(Path('clients-' + variant + '.zip'), variant, record['pm3_commit'])
    for name in ASSETS:
        path = Path(name)
        record['assets'][name] = dict(sha256=sha256(path), size=path.stat().st_size)
    Path('release-manifest.json').write_text(json.dumps(record, indent=2) + '\n')


if __name__ == '__main__':
    main()
