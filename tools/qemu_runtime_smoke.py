"""Exercise the real image's ARM Python, Tcl/Tk and fonts inside qemu_env.sh.

This checks the emulation environment, not application or RF behavior.
"""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys


PROGRAM = r'''
import json, os, struct, sys, tkinter
from tkinter import font
assert struct.calcsize('P') == 4, 'Expected 32-bit ARM Python'
assert sys.version_info[:2] == (3, 8), 'Expected device Python 3.8'
root = tkinter.Tk()
root.geometry('240x240')
errors = []
root.report_callback_exception = lambda *args: errors.append(str(args))
root.tk.createcommand('bgerror', lambda message: errors.append(message))
families = font.families(root)
assert 'mononoki' in [f.lower() for f in families], 'Device font unavailable'
face = font.Font(root, family='mononoki', size=12)
assert face.actual('family').lower() == 'mononoki', 'Unexpected font substitution'
width = face.measure('Settings')
assert width > 0, 'Font measurement failed'
canvas = tkinter.Canvas(root, width=240, height=240)
canvas.pack()
item = canvas.create_text(120, 120, text='iCopy-XS', font=face)
root.update()
assert canvas.bbox(item), 'Text did not produce a bounding box'
result = dict(python=sys.version, tkinter=tkinter.__file__,
              tcl=root.tk.call('info', 'patchlevel'),
              tk=root.tk.call('package', 'require', 'Tk'),
              pointer_bits=32, font=face.actual(), measured_width=width,
              status='PASS', boundary='ARM Python/Tk environment only')
root.destroy()
assert not errors, errors
with open(os.path.join(os.environ['ICOPY_QEMU_RUN'], 'results', 'runtime.json'), 'w') as f:
    json.dump(result, f, indent=2)
print(json.dumps(result), flush=True)
'''


def main():
    project = Path(__file__).resolve().parents[1]
    spec = importlib.util.spec_from_file_location(
        'flow_runner', project / 'tests' / 'flows' / 'run_all_flows.py')
    runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)
    run = Path(os.environ['ICOPY_QEMU_RUN'])
    output = run / 'results' / 'runtime.json'
    if output.exists():
        raise RuntimeError('Refusing existing runtime result: ' + str(output))
    with (run / 'results' / 'xvfb.log').open('wb') as log:
        display, number = runner.start_display(log)
        try:
            completed = subprocess.run(
                [os.environ['QEMU_BIN'], os.environ['QEMU_PYTHON'], '-u', '-c', PROGRAM],
                env=dict(os.environ, DISPLAY=number), timeout=60)
            if completed.returncode:
                return 1
            if display.poll() is not None:
                raise RuntimeError('Display exited during the smoke test')
            data = json.loads(output.read_text())
            if data.get('status') != 'PASS':
                raise RuntimeError('Missing successful runtime result')
        finally:
            runner.stop_process_group(display)
    return 0


if __name__ == '__main__':
    sys.exit(main())
