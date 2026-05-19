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

"""T55xx Wipe plugin.

Workflow
--------
1. do_wipe()     -- lf t55xx wipe (no password) -> lf t55xx detect (confirm clean)
2. do_wipe_pwd() -- capture password from input widget -> lf t55xx wipe -p <pwd>
                    -> lf t55xx detect (confirm clean, no pwd needed after wipe)
"""


class WipeT55xxPlugin(object):
    """Entry class for the T55xx Wipe plugin."""

    def __init__(self, host=None):
        self.host = host

    # ------------------------------------------------------------------
    # Wipe without password
    # ------------------------------------------------------------------

    def do_wipe(self):
        """Wipe T55xx without password."""
        self.host.set_var('error_msg', '')
        self.host.set_var('done_msg', '')

        try:
            import executor
        except ImportError:
            self.host.set_var('error_msg', 'Import error')
            return {'status': 'error'}

        ret = executor.startPM3Task('lf t55xx wipe', 15000)
        if ret == -1:
            self.host.set_var('error_msg', 'Wipe failed')
            return {'status': 'error'}

        # Verify tag is back to clean state
        executor.startPM3Task('lf t55xx detect', 10000)
        if executor.hasKeyword('Could not detect modulation automatically'):
            self.host.set_var('error_msg', 'Detect after wipe failed')
            return {'status': 'error'}

        self.host.set_var('done_msg', 'T55xx wiped cleanly')
        return {'status': 'done'}

    # ------------------------------------------------------------------
    # Wipe with password
    # ------------------------------------------------------------------

    def do_wipe_pwd(self):
        """Capture password from input widget then wipe T55xx with it."""
        self.host.set_var('error_msg', '')
        self.host.set_var('done_msg', '')

        pwd = self.host.get_input().strip().upper()
        if len(pwd) != 8:
            self.host.set_var('error_msg', 'Need 8 hex chars')
            return {'status': 'error'}

        try:
            import executor
        except ImportError:
            self.host.set_var('error_msg', 'Import error')
            return {'status': 'error'}

        ret = executor.startPM3Task('lf t55xx wipe -p %s' % pwd, 15000)
        if ret == -1:
            self.host.set_var('error_msg', 'Wipe failed')
            return {'status': 'error'}

        # After a successful wipe the tag is unlocked — detect without password
        executor.startPM3Task('lf t55xx detect', 10000)
        if executor.hasKeyword('Could not detect modulation automatically'):
            self.host.set_var('error_msg', 'Detect after wipe failed')
            return {'status': 'error'}

        self.host.set_var('done_msg', 'T55xx wiped cleanly')
        return {'status': 'done'}
