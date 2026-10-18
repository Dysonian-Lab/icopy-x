"""Contract tests for the Linux orchestrator; no QEMU image is required."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import time

import pytest

REPO = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('flow_runner', REPO / 'tests/flows/run_all_flows.py')
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)


def scenario(project, name='scan_example', body='exit 0\n'):
    directory = project / 'tests/flows/scan/scenarios' / name
    directory.mkdir(parents=True)
    (directory / (name + '.sh')).write_text(body, encoding='utf-8')
    return runner.inventory(project, ['scan'])[0]


def test_checked_in_inventory_counts_each_console_case_once():
    cases = runner.inventory(REPO, runner.FLOWS)
    ids = [case['id'] for case in cases]
    assert len(ids) == len(set(ids))
    assert ids.count('read/read_mf1k_console_during_read') == 1
    assert all(case['flow'] != 'install' for case in cases)


@pytest.mark.parametrize('mode', ['absent_flow', 'empty_flow', 'missing_script', 'unmatched_filter'])
def test_incomplete_inventory_cannot_pass(tmp_path, mode):
    root = tmp_path / 'tests/flows/scan/scenarios'
    if mode == 'empty_flow':
        root.mkdir(parents=True)
    elif mode == 'missing_script':
        (root / 'scan_example').mkdir(parents=True)
    elif mode == 'unmatched_filter':
        scenario(tmp_path)
    with pytest.raises(ValueError):
        runner.inventory(tmp_path, ['scan'], ['scan/missing'] if mode == 'unmatched_filter' else [])


def test_one_valid_filter_does_not_hide_a_typo(tmp_path):
    scenario(tmp_path)
    with pytest.raises(ValueError, match='matches no scenarios'):
        runner.inventory(tmp_path, ['scan'], ['scan/*', 'read/typo'])


@pytest.mark.parametrize('body,code,timeout,status', [
    ('PASS: observed\n', 0, False, 'PASS'),
    ('FAIL: wrong screen\n', 0, False, 'FAIL'),
    ('FAIL: wrong screen\n', 1, False, 'FAIL'),
    ('PASS: observed\n', 7, False, 'ERROR'),
    ('PASS: observed\n', 0, True, 'INCOMPLETE'),
    ('PASSING: observed\n', 0, False, 'INCOMPLETE'),
    ('PASS: yes\nFAIL: no\n', 0, False, 'INCOMPLETE'),
    ('PASS:\n', 0, False, 'INCOMPLETE'),
    ('', 0, False, 'INCOMPLETE'),
    (None, 0, False, 'INCOMPLETE'),
])
def test_process_outcome_and_result_are_both_required(tmp_path, body, code, timeout, status):
    path = tmp_path / 'result.txt'
    if body is not None:
        path.write_text(body)
    assert runner.classify(code, path, timeout)[0] == status


def test_unexecuted_cases_are_reported_as_incomplete(tmp_path):
    cases = [{'id': 'scan/a'}, {'id': 'scan/b'}]
    assert not runner.write_report(tmp_path, 'current', cases, [dict(cases[0], status='PASS')])
    report = json.loads((tmp_path / 'master_summary.json').read_text())
    assert report['planned'] == 2
    assert report['counts']['INCOMPLETE'] == 1
    assert not report['success']


def test_empty_or_interrupted_run_cannot_pass(tmp_path):
    assert not runner.write_report(tmp_path, 'current', [], [])
    case = {'id': 'scan/a'}
    assert not runner.write_report(tmp_path, 'current', [case], [dict(case, status='PASS')], interrupted=True)


@pytest.mark.parametrize('value', ['0', '-1', 'nan', 'inf'])
def test_invalid_timeout_is_rejected(value):
    with pytest.raises(SystemExit) as error:
        runner.main(['--target', 'current', '--timeout', value, '--list'])
    assert error.value.code == 2


def test_target_is_explicit(monkeypatch):
    monkeypatch.delenv('TEST_TARGET', raising=False)
    with pytest.raises(SystemExit) as error:
        runner.main(['--list'])
    assert error.value.code == 2


@pytest.mark.skipif(sys.platform != 'linux', reason='Linux process-group execution contract')
@pytest.mark.parametrize('tail,expected', [
    ('exit 0', 'PASS'), ('exit 7', 'ERROR'), ('sleep 5', 'INCOMPLETE'),
])
def test_real_child_exit_and_timeout(tmp_path, tail, expected):
    body = 'printf "PASS: checked\\n" > "$RESULTS_DIR/scan/scenarios/scan_example/result.txt"\n' + tail + '\n'
    case = scenario(tmp_path, body=body)
    output = tmp_path / 'output'
    (output / 'logs').mkdir(parents=True)
    displays = []

    def display(log):
        process = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'],
                                   start_new_session=True, stdout=log, stderr=log)
        displays.append(process)
        return process, ':999'

    result = runner.execute_case(case, tmp_path, output, 'current', 0.1, display)
    assert result['status'] == expected
    assert displays[0].poll() is not None


@pytest.mark.skipif(sys.platform != 'linux', reason='Linux process-group execution contract')
def test_cleanup_kills_only_owned_descendants(tmp_path):
    late = tmp_path / 'late-output'
    child_pid = tmp_path / 'child.pid'
    unrelated = subprocess.Popen(['sleep', '30'], start_new_session=True)
    process = subprocess.Popen(
        ['bash', '-c', '(sleep .3; touch "$1") & echo $! > "$2"',
         'scenario', str(late), str(child_pid)], start_new_session=True)
    try:
        process.wait(timeout=5)
        runner.stop_process_group(process)
        time.sleep(0.4)
        assert not late.exists()
        assert unrelated.poll() is None
    finally:
        runner.stop_process_group(unrelated)


def test_existing_output_cannot_supply_a_stale_pass(tmp_path, monkeypatch):
    scenario(tmp_path)
    output = tmp_path / 'old-output'
    output.mkdir()
    (output / 'result.txt').write_text('PASS: old\n')
    monkeypatch.setattr(runner.sys, 'platform', 'linux')
    monkeypatch.setattr(runner.shutil, 'which', lambda name: name)
    with pytest.raises(FileExistsError):
        runner.main(['--project', str(tmp_path), '--target', 'current', '--flow', 'scan',
                     '--results', str(output)])


@pytest.mark.skipif(sys.platform != 'linux', reason='Linux executable/display pipe contract')
@pytest.mark.parametrize('body,exit_code,status', [
    ('printf "PASS: observed\\n" > "$RESULTS_DIR/scan/scenarios/scan_example/result.txt"\n', 0, 'PASS'),
    ('exit 0\n', 1, 'INCOMPLETE'),
    ('printf "PASS: observed\\n" > "$RESULTS_DIR/scan/scenarios/scan_example/result.txt"\nexit 8\n', 1, 'ERROR'),
])
def test_cli_uses_fresh_results_and_propagates_failure(tmp_path, body, exit_code, status):
    scenario(tmp_path, body=body)
    binaries = tmp_path / 'bin'
    binaries.mkdir()
    # Only the display server is replaced; the CLI, shell, process groups,
    # readiness pipe, result writes and exit reporting execute normally.
    xvfb = binaries / 'Xvfb'
    xvfb.write_text('#!/usr/bin/env python3\nimport os,sys,time\n'
                    'os.write(int(sys.argv[sys.argv.index("-displayfd")+1]), b"987\\n")\n'
                    'time.sleep(30)\n')
    xvfb.chmod(0o755)
    output = tmp_path / 'result-output'
    result = subprocess.run(
        [sys.executable, str(REPO / 'tests/flows/run_all_flows.py'),
         '--project', str(tmp_path), '--target', 'current', '--flow', 'scan',
         '--results', str(output)],
        env=dict(os.environ, PATH=str(binaries) + os.pathsep + os.environ['PATH']),
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=15)
    assert result.returncode == exit_code, result.stdout
    report = json.loads((output / 'master_summary.json').read_text())
    assert report['planned'] == 1
    assert report['cases'][0]['status'] == status
    assert report['success'] == (exit_code == 0)


@pytest.mark.skipif(sys.platform != 'linux', reason='Linux display pipe contract')
def test_display_waits_for_separately_written_newline(tmp_path, monkeypatch):
    marker = tmp_path / 'newline-ready'
    xvfb = tmp_path / 'Xvfb'
    xvfb.write_text('#!/usr/bin/env python3\nimport os,sys,time,pathlib\n'
                    'fd=int(sys.argv[sys.argv.index("-displayfd")+1])\n'
                    'os.write(fd, b"987")\ntime.sleep(0.2)\n'
                    'pathlib.Path(' + repr(str(marker)) + ').touch()\n'
                    'os.write(fd, b"\\n")\ntime.sleep(30)\n')
    xvfb.chmod(0o755)
    monkeypatch.setenv('PATH', str(tmp_path) + os.pathsep + os.environ['PATH'])
    with (tmp_path / 'display.log').open('wb') as log:
        process, number = runner.start_display(log)
        try:
            assert number == ':987'
            assert marker.exists(), 'Returned before the readiness line was complete'
            assert process.poll() is None
        finally:
            runner.stop_process_group(process)


@pytest.mark.skipif(sys.platform != 'linux', reason='Linux display pipe contract')
@pytest.mark.parametrize('payload', [b'987', b'bad\n', b'9' * 65 + b'\n'])
def test_display_rejects_incomplete_or_invalid_messages(tmp_path, monkeypatch, payload):
    xvfb = tmp_path / 'Xvfb'
    xvfb.write_text('#!/usr/bin/env python3\nimport os,sys\n'
                    'os.write(int(sys.argv[sys.argv.index("-displayfd")+1]), ' + repr(payload) + ')\n')
    xvfb.chmod(0o755)
    monkeypatch.setenv('PATH', str(tmp_path) + os.pathsep + os.environ['PATH'])
    with (tmp_path / 'display.log').open('wb') as log:
        with pytest.raises(RuntimeError):
            runner.start_display(log)
