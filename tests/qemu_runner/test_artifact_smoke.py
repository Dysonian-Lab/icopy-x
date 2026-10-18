"""Package input contracts; these do not replace the actual ARM smoke run."""
import importlib.util
import json
from pathlib import Path
import stat
import subprocess
import sys
import zipfile

import pytest


TOOL = Path(__file__).resolve().parents[2] / 'tools' / 'qemu_artifact_smoke.py'
spec = importlib.util.spec_from_file_location('artifact_smoke', TOOL)
artifact = importlib.util.module_from_spec(spec)
spec.loader.exec_module(artifact)


def make_ipk(path, extra=(), omitted=(), flash=False):
    with zipfile.ZipFile(path, 'w') as archive:
        for name in artifact.REQUIRED:
            if name not in omitted:
                archive.writestr(name, 'fixture')
        if flash:
            archive.writestr(artifact.FIRMWARE, json.dumps({'version': 'fixture'}))
            archive.writestr('res/firmware/pm3/fullimage.elf', 'firmware-fixture')
        for name, value in extra:
            archive.writestr(name, value)


@pytest.mark.parametrize('variant', ['flash', 'noflash'])
def test_extracts_zip_and_records_exact_bytes(tmp_path, variant):
    ipk = tmp_path / 'input.ipk'
    make_ipk(ipk, flash=variant == 'flash')
    destination = tmp_path / 'app'
    files = artifact.extract_ipk(ipk, destination, variant)
    assert set(artifact.REQUIRED) <= set(files)
    assert files['lib/activity_read.py'] == artifact.sha256(destination / 'lib' / 'activity_read.py')
    assert (destination / 'lib' / 'activity_read.py').read_bytes() == b'fixture'


@pytest.mark.parametrize('name', ['../escape', '/escape', 'a/../../escape',
                                  r'a\escape', 'C:/escape', './escape', 'a//escape', '.'])
def test_rejects_unsafe_paths_before_extracting(tmp_path, name):
    ipk = tmp_path / 'input.ipk'
    info = zipfile.ZipInfo('placeholder')
    # Preserve the raw hostile name even on Windows, where ZipInfo's
    # constructor otherwise normalizes backslashes before writing.
    info.filename = name
    make_ipk(ipk, extra=[(info, b'bad')])
    with pytest.raises(ValueError, match='Unsafe'):
        artifact.extract_ipk(ipk, tmp_path / 'app', 'noflash')
    assert not (tmp_path / 'app').exists()


def test_rejects_duplicate_entries(tmp_path):
    ipk = tmp_path / 'input.ipk'
    with pytest.warns(UserWarning, match='Duplicate'):
        make_ipk(ipk, extra=[('app.py', b'duplicate')])
    with pytest.raises(ValueError, match='duplicate'):
        artifact.extract_ipk(ipk, tmp_path / 'app', 'noflash')


@pytest.mark.parametrize('kind', [stat.S_IFLNK, stat.S_IFCHR, stat.S_IFIFO])
def test_rejects_special_entries(tmp_path, kind):
    ipk = tmp_path / 'input.ipk'
    info = zipfile.ZipInfo('special')
    info.create_system = 3
    info.external_attr = (kind | 0o777) << 16
    make_ipk(ipk, extra=[(info, b'../target')])
    with pytest.raises(ValueError, match='Unsupported'):
        artifact.extract_ipk(ipk, tmp_path / 'app', 'noflash')


def test_missing_required_module_cannot_fall_back_to_checkout(tmp_path):
    ipk = tmp_path / 'input.ipk'
    make_ipk(ipk, omitted=['lib/activity_read.py'])
    with pytest.raises(ValueError, match='Missing.*activity_read'):
        artifact.extract_ipk(ipk, tmp_path / 'app', 'noflash')


def test_existing_output_is_rejected(tmp_path):
    ipk = tmp_path / 'input.ipk'
    make_ipk(ipk)
    destination = tmp_path / 'app'
    destination.mkdir()
    (destination / 'sentinel').write_text('preserve')
    with pytest.raises(FileExistsError):
        artifact.extract_ipk(ipk, destination, 'noflash')
    assert (destination / 'sentinel').read_text() == 'preserve'


@pytest.mark.parametrize('actual,declared', [('flash', 'noflash'), ('noflash', 'flash')])
def test_rejects_wrong_firmware_layout(tmp_path, actual, declared):
    ipk = tmp_path / 'input.ipk'
    make_ipk(ipk, flash=actual == 'flash')
    with pytest.raises(ValueError, match='firmware'):
        artifact.extract_ipk(ipk, tmp_path / 'app', declared)


def test_missing_qemu_is_a_failure_not_a_skip(tmp_path, monkeypatch):
    monkeypatch.delenv('ICOPY_QEMU_RUN', raising=False)
    result = subprocess.run([sys.executable, str(TOOL), str(tmp_path / 'absent.ipk'),
                             '--variant', 'noflash'], capture_output=True, text=True)
    assert result.returncode != 0
    assert 'missing emulation is an error' in result.stderr
