"""Exercise staged-update failure paths using temporary application trees."""
import importlib.util
import os
from pathlib import Path
import sys

import pytest


@pytest.fixture(params=['app', 'ipk_starter'])
def boot(request, monkeypatch, tmp_path):
    path = Path(__file__).resolve().parents[1] / 'src' / (request.param + '.py')
    spec = importlib.util.spec_from_file_location('test_boot_' + request.param, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    for variable, leaf in [('APP_DIR', 'live'), ('APP_NEW', 'staged'), ('APP_OLD', 'backup')]:
        monkeypatch.setattr(module, variable, str(tmp_path / leaf))
    live = tmp_path / 'live'
    live.mkdir()
    (live / 'known-good').write_text('old application')
    monkeypatch.chdir(tmp_path)
    return module


def stage(module):
    root = Path(module.APP_NEW)
    for relative in ('app.py', 'main/main.py', 'lib/actmain.py'):
        file = root / relative
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text('# staged application')
    return root


def test_normal_boot_does_not_move_live_application(boot):
    assert boot.swap_firmware() is False
    assert (Path(boot.APP_DIR) / 'known-good').exists()


@pytest.mark.parametrize('missing', ['app.py', 'main/main.py', 'lib/actmain.py'])
@pytest.mark.parametrize('empty', [False, True])
def test_incomplete_staging_keeps_live_application(boot, missing, empty):
    root = stage(boot)
    if empty:
        (root / missing).write_bytes(b'')
    else:
        (root / missing).unlink()
    assert boot.swap_firmware() is False
    assert (Path(boot.APP_DIR) / 'known-good').exists()
    assert not Path(boot.APP_OLD).exists()


def test_activation_failure_restores_live_application(boot, monkeypatch):
    stage(boot)
    original = os.rename

    def fail_activation(source, destination):
        if source == boot.APP_NEW:
            raise OSError('activation failed')
        original(source, destination)

    monkeypatch.setattr(os, 'rename', fail_activation)
    assert boot.swap_firmware() is False
    assert (Path(boot.APP_DIR) / 'known-good').exists()
    assert Path(boot.APP_NEW).exists()


def test_failed_rollback_stops_instead_of_launching_a_missing_tree(boot, monkeypatch):
    stage(boot)
    original = os.rename

    def fail_activation_and_rollback(source, destination):
        if source in (boot.APP_NEW, boot.APP_OLD):
            raise OSError('simulated storage failure')
        original(source, destination)

    monkeypatch.setattr(os, 'rename', fail_activation_and_rollback)
    with pytest.raises(RuntimeError, match='rollback failed'):
        boot.swap_firmware()
    assert (Path(boot.APP_OLD) / 'known-good').exists()
    assert Path(boot.APP_NEW).exists()


def test_exec_failure_restores_live_and_preserves_update(boot, monkeypatch):
    if not hasattr(boot, '_validate_staged_firmware'):
        pytest.skip('app.py owns exec fallback')
    stage(boot)
    previous_cwd = os.getcwd()

    def failed_exec(executable, arguments):
        assert os.path.samefile(os.getcwd(), boot.APP_DIR)
        raise OSError('cannot start interpreter')

    monkeypatch.setattr(os, 'execv', failed_exec)
    assert boot.swap_firmware() is False
    assert (Path(boot.APP_DIR) / 'known-good').exists()
    assert (Path(boot.APP_NEW) / 'app.py').exists()
    assert os.path.samefile(os.getcwd(), previous_cwd)


def test_successful_activation_keeps_backup_and_uses_new_cwd(boot, monkeypatch):
    stage(boot)

    class ReplacedProcess(BaseException):
        pass

    def exec_probe(executable, arguments):
        assert os.path.samefile(os.getcwd(), boot.APP_DIR)
        assert arguments == [sys.executable, str(Path(boot.APP_DIR) / 'app.py')]
        raise ReplacedProcess

    monkeypatch.setattr(os, 'execv', exec_probe)
    if hasattr(boot, '_validate_staged_firmware'):
        with pytest.raises(ReplacedProcess):
            boot.swap_firmware()
    else:
        assert boot.swap_firmware() is True
    assert (Path(boot.APP_OLD) / 'known-good').exists()
    assert (Path(boot.APP_DIR) / 'app.py').exists()


def test_packaged_starter_launches_app_in_its_own_namespace(boot, monkeypatch):
    if not hasattr(boot, 'main'):
        pytest.skip('packaged starter owns this entry point')
    live = Path(boot.APP_DIR)
    (live / 'app.py').write_text(
        'from pathlib import Path\n'
        'assert __name__ == "__main__"\n'
        'assert Path(__file__).name == "app.py"\n'
        'Path("started").write_text("ok")\n')
    monkeypatch.setattr(sys, 'path', sys.path.copy())
    boot.main()
    assert (live / 'started').read_text() == 'ok'
