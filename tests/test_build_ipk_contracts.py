"""Check package variant boundaries with tiny, distinguishable fixtures."""
import importlib.util
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
import zipfile

import pytest


@pytest.fixture
def builder():
    path = Path(__file__).resolve().parents[1] / 'tools' / 'build_ipk.py'
    spec = importlib.util.spec_from_file_location('build_ipk_contract', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_build_version_keeps_release_overrides_and_distinguishes_local_builds(builder, monkeypatch):
    monkeypatch.setenv('ICOPYX_VERSION', 'v2.0-env')
    assert builder._generate_build_version('v2.1-release') == 'v2.1-release'
    assert builder._generate_build_version() == 'v2.0-env'
    monkeypatch.delenv('ICOPYX_VERSION')
    for minute in (1, 2):
        instant = datetime(2026, 10, 18, 12, minute, tzinfo=timezone.utc)
        monkeypatch.setattr(builder, 'datetime', SimpleNamespace(now=lambda tz: instant))
        assert builder._generate_build_version() == '261018-120{}'.format(minute)


@pytest.mark.parametrize('flash', [False, True])
@pytest.mark.parametrize('separator', ['/', '\\'])
def test_package_keeps_matching_client_lua_and_firmware_boundary(builder, monkeypatch, tmp_path, flash, separator):
    # All collection paths point to empty directories except our fixture inputs.
    for name in ('REPO_ROOT', 'SRC_LIB', 'SRC_MIDDLEWARE', 'SRC_SCREENS',
                 'ORIG_SO_LIB', 'ORIG_SO_MAIN', 'BUILD_DIR', 'RES_DIR',
                 'DATA_DIR', 'PLUGINS_DIR'):
        directory = tmp_path / name
        directory.mkdir()
        monkeypatch.setattr(builder, name, str(directory))
    for name in ('proxmark3', 'factory_proxmark3', 'lua.zip', 'factory_lua.zip'):
        (Path(builder.BUILD_DIR) / name).write_bytes(name.encode())
    resource = tmp_path / 'fixture'
    resource.write_bytes(b'fixture')
    resource_names = ['res/firmware/pm3/fullimage.elf', 'res/firmware/pm3/manifest.json',
                      'res/firmware-notes.txt', 'res/img/47.png']
    monkeypatch.setattr(builder, 'collect_resources', lambda directory: [
        (str(resource), name.replace('/', separator)) for name in resource_names])
    output = tmp_path / 'test.ipk'
    assert builder.build_ipk(str(output), include_flash=flash)
    with zipfile.ZipFile(output) as package:
        names = {name.replace('\\', '/') for name in package.namelist()}
        assert ('res/firmware/pm3/fullimage.elf' in names) is flash
        assert ('res/firmware/pm3/manifest.json' in names) is flash
        assert 'res/firmware-notes.txt' in names
        assert 'res/img/47.png' in names
        prefix = '' if flash else 'factory_'
        assert package.read('pm3/proxmark3') == (prefix + 'proxmark3').encode()
        assert package.read('pm3/lua.zip') == (prefix + 'lua.zip').encode()
