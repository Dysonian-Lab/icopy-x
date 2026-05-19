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
reference clocks, then falling back to a wipe with default password.

Sequence
--------
1. lf t55xx write -b 0 -d 000880E0 --r0
2. lf t55xx write -b 0 -d 000880E0 --r1
3. lf t55xx write -b 0 -d 000880E0 --r2
4. lf t55xx write -b 0 -d 000880E0 --r3
5. lf t55xx write -b 0 -d 000880E0 --r0 -t
6. lf t55xx write -b 0 -d 000880E0 --r1 -t
7. lf t55xx write -b 0 -d 000880E0 --r2 -t
8. lf t55xx write -b 0 -d 000880E0 --r3 -t
9. lf t55xx wipe -p 00000000
10. lf t55xx detect -- final check
"""

_B0        = '000880E0'
_DEF_PWD   = '00000000'
_TIMEOUT   = 10000


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

        # Step 9: wipe with default password (skip detect — go straight here)
        executor.startPM3Task('lf t55xx wipe -p %s' % _DEF_PWD, _TIMEOUT)

        # Step 10: final detect
        ret = executor.startPM3Task('lf t55xx detect', _TIMEOUT)
        if ret != -1:
            self.host.set_var('result_msg', 'Tag recovered!')
            return {'status': 'done'}

        self.host.set_var('result_msg', 'Recovery failed\nTag unresponsive')
        return {'status': 'failed'}
