"""Run QEMU scenarios with fresh results and checked process outcomes.

Linux only. Run inside an isolated QEMU environment: existing scenario helpers
still use shared /mnt and /tmp paths. This runner does not establish RF coverage.
"""
import argparse
import fnmatch
import json
import math
import os
from pathlib import Path
import re
import select
import shutil
import signal
import subprocess
import sys
import tempfile
import time

FLOWS = ('scan', 'read', 'write', 'auto-copy', 'erase', 'simulate', 'backlight',
         'volume', 'dump_files', 'diagnosis', 'sniff', 'lua-script', 'about',
         'time_settings', 'pc_mode')
TARGETS = ('original', 'current')


def inventory(project, flows, patterns=()):
    """Freeze case IDs, rejecting absent scripts, empty flows and bad filters."""
    cases = []
    for flow in flows:
        root = project / 'tests' / 'flows' / flow / 'scenarios'
        dirs = sorted(p for p in root.iterdir() if p.is_dir()) if root.is_dir() else []
        if not dirs:
            raise ValueError('No scenario inventory for ' + flow)
        for directory in dirs:
            case_id = flow + '/' + directory.name
            script = directory / (directory.name + '.sh')
            if not script.is_file():
                raise ValueError('Missing scenario entrypoint: ' + str(script))
            if not patterns or any(fnmatch.fnmatchcase(case_id, p) for p in patterns):
                cases.append({'id': case_id, 'flow': flow, 'name': directory.name,
                              'script': str(script)})
    if not cases:
        raise ValueError('Selection contains zero scenarios')
    for pattern in patterns:
        if not any(fnmatch.fnmatchcase(c['id'], pattern) for c in cases):
            raise ValueError('Selection pattern matches no scenarios: ' + pattern)
    return cases


def stop_process_group(process):
    """Reap only the owned group, including children left by an exited shell."""
    if process is None:
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    try:
        process.wait(timeout=2)
    except subprocess.TimeoutExpired:
        pass
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    process.wait(timeout=2)


def start_display(log):
    """Xvfb allocates a free display and signals readiness through a pipe."""
    reader, writer = os.pipe()
    process = None
    try:
        process = subprocess.Popen(
            ['Xvfb', '-displayfd', str(writer), '-screen', '0', '240x240x24',
             '-nolisten', 'tcp', '-noreset'], pass_fds=(writer,), start_new_session=True,
            stdout=log, stderr=subprocess.STDOUT)
        os.close(writer)
        writer = None
        if not select.select([reader], [], [], 10)[0]:
            raise RuntimeError('Xvfb readiness timed out')
        number = os.read(reader, 64).strip()
        if not number.isdigit() or process.poll() is not None:
            raise RuntimeError('Xvfb did not provide a live display')
        return process, ':' + number.decode('ascii')
    except BaseException:
        stop_process_group(process)
        raise
    finally:
        os.close(reader)
        if writer is not None:
            os.close(writer)


def classify(returncode, result_file, timed_out=False):
    if timed_out:
        return 'INCOMPLETE', 'Scenario timed out'
    try:
        text = result_file.read_text(encoding='utf-8').strip()
    except (OSError, UnicodeError) as error:
        if returncode != 0:
            return 'ERROR', 'Scenario process exited with status ' + str(returncode)
        return 'INCOMPLETE', 'Missing or unreadable result: ' + str(error)
    match = re.fullmatch(r'(PASS|FAIL): ([^\r\n]+)', text)
    if match and match.group(1) == 'FAIL':
        return 'FAIL', match.group(2)
    if returncode != 0:
        return 'ERROR', 'Scenario process exited with status ' + str(returncode)
    if not match:
        return 'INCOMPLETE', 'Malformed scenario result: ' + repr(text[:200])
    return match.group(1), match.group(2)


def execute_case(case, project, output, target, timeout, display_factory=start_display):
    result = dict(case, status='INCOMPLETE', detail='Not started', returncode=None)
    case_dir = output / target / case['flow'] / 'scenarios' / case['name']
    case_dir.mkdir(parents=True, exist_ok=False)
    log_path = output / 'logs' / (case['flow'] + '--' + case['name'] + '.log')
    result['log'] = str(log_path)
    started = time.monotonic()
    display_process = scenario_process = None
    with log_path.open('wb') as log:
        try:
            display_process, display = display_factory(log)
            env = dict(os.environ, PROJECT=str(project), TEST_TARGET=target,
                       RESULTS_DIR=str(output / target), TEST_DISPLAY=display,
                       NO_CLEAN='1')
            scenario_process = subprocess.Popen(
                ['bash', case['script']], cwd=str(project), env=env,
                start_new_session=True, stdout=log, stderr=subprocess.STDOUT)
            timed_out = False
            try:
                result['returncode'] = scenario_process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                timed_out = True
            finally:
                stop_process_group(scenario_process)
                scenario_process = None
            result['status'], result['detail'] = classify(
                result['returncode'], case_dir / 'result.txt', timed_out)
            if display_process.poll() is not None:
                result.update(status='ERROR', detail='Xvfb exited during the scenario')
        except (OSError, RuntimeError, subprocess.SubprocessError) as error:
            result.update(status='ERROR', detail=str(error))
        finally:
            stop_process_group(scenario_process)
            stop_process_group(display_process)
            result['seconds'] = round(time.monotonic() - started, 3)
    return result


def write_report(output, target, cases, results, interrupted=False):
    by_id = {r['id']: r for r in results}
    rows = [by_id.get(c['id'], dict(c, status='INCOMPLETE', detail='Not executed'))
            for c in cases]
    counts = {status: sum(r['status'] == status for r in rows)
              for status in ('PASS', 'FAIL', 'ERROR', 'INCOMPLETE')}
    success = bool(rows) and counts['PASS'] == len(rows) and not interrupted
    report = {'target': target, 'planned': len(cases), 'counts': counts,
              'success': success, 'interrupted': interrupted, 'cases': rows}
    temporary = output / 'master_summary.json.tmp'
    temporary.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    temporary.replace(output / 'master_summary.json')
    summary = 'TOTAL: {0}  PASS: {PASS}  FAIL: {FAIL}  ERROR: {ERROR}  INCOMPLETE: {INCOMPLETE}\n'.format(len(rows), **counts)
    (output / 'master_summary.txt').write_text(summary, encoding='utf-8')
    return success


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('workers', nargs='?', type=int, default=1,
                        help='Use 1: existing scenarios share filesystem paths')
    parser.add_argument('--project', type=Path, default=Path(os.environ.get('PROJECT', Path(__file__).resolve().parents[2])))
    parser.add_argument('--target', choices=TARGETS, default=os.environ.get('TEST_TARGET'))
    parser.add_argument('--flow', action='append', choices=FLOWS)
    parser.add_argument('--case', action='append', default=[], help='Case ID glob, e.g. read/*console*')
    parser.add_argument('--timeout', type=float, default=600, help='Maximum seconds per scenario')
    parser.add_argument('--results', type=Path, help='New output directory; must not already exist')
    parser.add_argument('--list', action='store_true', help='Print inventory without execution')
    args = parser.parse_args(argv)
    if args.target not in TARGETS:
        parser.error('Set --target or TEST_TARGET explicitly to original or current')
    if args.workers != 1:
        parser.error('Use one worker: existing scenarios share device filesystem paths')
    if not math.isfinite(args.timeout) or args.timeout <= 0:
        parser.error('--timeout must be a positive finite number')
    project = args.project.resolve()
    try:
        cases = inventory(project, list(dict.fromkeys(args.flow or FLOWS)), args.case)
    except (ValueError, OSError) as error:
        parser.error(str(error))
    if args.list:
        print(json.dumps(cases, indent=2))
        return 0
    if sys.platform != 'linux' or not shutil.which('bash') or not shutil.which('Xvfb'):
        parser.error('Execution requires Linux, bash and Xvfb')
    if args.results:
        output = args.results.resolve()
        output.mkdir(parents=True, exist_ok=False)
    else:
        parent = project / 'tests' / 'flows' / '_results'
        parent.mkdir(parents=True, exist_ok=True)
        output = Path(tempfile.mkdtemp(prefix='run-', dir=str(parent)))
    (output / 'logs').mkdir()
    results = []
    write_report(output, args.target, cases, results)
    print('Results: ' + str(output), flush=True)
    interrupted = False

    def interrupt(_signum, _frame):
        raise KeyboardInterrupt

    previous = signal.signal(signal.SIGTERM, interrupt)
    try:
        for index, case in enumerate(cases, 1):
            print('[{}/{}] {}'.format(index, len(cases), case['id']), flush=True)
            result = execute_case(case, project, output, args.target, args.timeout)
            results.append(result)
            write_report(output, args.target, cases, results)
            print(result['status'] + ': ' + result['detail'], flush=True)
    except KeyboardInterrupt:
        interrupted = True
    finally:
        signal.signal(signal.SIGTERM, previous)
        success = write_report(output, args.target, cases, results, interrupted)
    print((output / 'master_summary.txt').read_text(), end='')
    return 0 if success else 1


if __name__ == '__main__':
    raise SystemExit(main())
