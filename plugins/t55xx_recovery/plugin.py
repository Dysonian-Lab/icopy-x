##########################################################################
# Required Notice: Copyright ETOILE401 SAS (http://www.lab401.com)
#
# Copyright (c) 2026: ETOILE401 SAS & https://github.com/quantum-x/
#
# This software is licensed under the PolyForm Noncommercial License 1.0.0.
# You may not use this software for commercial purposes.
#
# A copy of the license is available at:
# https://polyformproject.org/licenses/noncommercial/1.0.0
#
# This entire header "Required Notice" must remain in place.
##########################################################################

"""T55xx Recovery plugin.

Recovers a soft-bricked T55xx tag by trying all downlink modes and
reference clocks, then wiping with the default password, running a
password dictionary check and wiping with any found password before
a final detect to confirm B0.

Sequence
--------
1.  lf t55xx write -b 0 -d 000880E0 --r0
2.  lf t55xx write -b 0 -d 000880E0 --r1
3.  lf t55xx write -b 0 -d 000880E0 --r2
4.  lf t55xx write -b 0 -d 000880E0 --r3
5.  lf t55xx write -b 0 -d 000880E0 --r0 -t
6.  lf t55xx write -b 0 -d 000880E0 --r1 -t
7.  lf t55xx write -b 0 -d 000880E0 --r2 -t
8.  lf t55xx write -b 0 -d 000880E0 --r3 -t
9.  lf t55xx wipe -p 00000000
10. lf t55xx detect -- early exit if B0 already restored
11a. lf t55xx chk -f /tmp/.keys/t55xx_default_pwds.dic  (if SD dic available)
     -- else --
     lf t55xx chk                                        (PM3 hardcoded default)
11b. if password found -> lf t55xx wipe -p <PASS>
12. lf t55xx detect -- confirm B0 == 000880E0
"""

import os
import re
import shutil

_B0           = '000880E0'
_DEF_PWD      = '00000000'
_TIMEOUT      = 10000
_CHK_TIMEOUT  = 180000
_WIPE_TIMEOUT = 30000

_DIC_SRC = '/mnt/upan/keys/t55xx/t55xx_default_pwds.dic'
_DIC_DST = '/tmp/.keys/t55xx_default_pwds.dic'

_RE_FOUND_PWD = re.compile(r'\[ ([A-Fa-f0-9]{8}) \]')


class T55xxRecoveryPlugin(object):
    """Entry class for the T55xx Recovery plugin."""

    def __init__(self, host=None):
        self.host = host

    def do_recover(self):
        """Run full recovery sequence."""
        self.host.set_var('error_msg', '')
        self.host.set_var('result_msg', '')

        try:
            import executor
        except ImportError:
            self.host.set_var('error_msg', 'Import error')
            return {'status': 'error'}

        # Steps 1-8: try all downlink modes and reference clocks
        cmds = [
            'lf t55xx write -b 0 -d %s --r0'    % _B0,
            'lf t55xx write -b 0 -d %s --r1'    % _B0,
            'lf t55xx write -b 0 -d %s --r2'    % _B0,
            'lf t55xx write -b 0 -d %s --r3'    % _B0,
            'lf t55xx write -b 0 -d %s --r0 -t' % _B0,
            'lf t55xx write -b 0 -d %s --r1 -t' % _B0,
            'lf t55xx write -b 0 -d %s --r2 -t' % _B0,
            'lf t55xx write -b 0 -d %s --r3 -t' % _B0,
        ]

        for cmd in cmds:
            executor.startPM3Task(cmd, _TIMEOUT)

        # Step 9: wipe with default password
        executor.startPM3Task('lf t55xx wipe -p %s' % _DEF_PWD, _WIPE_TIMEOUT)

        # Step 10: early detect — skip chk if default wipe already fixed it
        executor.startPM3Task('lf t55xx detect', _TIMEOUT)
        early_content = executor.getPrintContent() or ''
        if _B0 in early_content:
            self.host.set_var('result_msg', 'Tag recovered!')
            return {'status': 'done'}

        # Step 11a: build chk command — prefer SD dic, fall back to PM3 default
        use_file = False
        try:
            os.makedirs('/tmp/.keys', exist_ok=True)
            shutil.copy(_DIC_SRC, _DIC_DST)
            use_file = True
        except Exception:
            pass

        if use_file:
            chk_cmd = 'lf t55xx chk -f %s' % _DIC_DST
        else:
            chk_cmd = 'lf t55xx chk'

        executor.startPM3Task(chk_cmd, _CHK_TIMEOUT)

        # Step 11b: parse chk output for found password
        content = executor.getPrintContent() or ''
        found_pwd = None
        for line in content.splitlines():
            if 'found valid password' in line.lower():
                m = _RE_FOUND_PWD.search(line)
                if m:
                    found_pwd = m.group(1).upper()
                    break

        if found_pwd:
            executor.startPM3Task('lf t55xx wipe -p %s' % found_pwd, _WIPE_TIMEOUT)

        # Step 12: final detect — confirm B0 is restored
        executor.startPM3Task('lf t55xx detect', _TIMEOUT)
        detect_content = executor.getPrintContent() or ''

        if _B0 in detect_content:
            self.host.set_var('result_msg', 'Tag recovered!')
            return {'status': 'done'}

        self.host.set_var('result_msg', 'Could not detect\nT55xx B0')
        return {'status': 'failed'}
