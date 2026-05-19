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

"""T55xx Pwd Check plugin.

Workflow
--------
1. do_check() -- detect, copy dic, chk, find password.
2. User confirms wipe on found screen.
3. do_wipe()  -- lf t55xx wipe -p <found_pwd>.
"""

import os
import re
import shutil

_DIC_SRC  = '/mnt/upan/keys/t55xx/t55xx_default_pwds.dic'
_DIC_DST  = '/tmp/.keys/t55xx_default_pwds.dic'
_OUT_FILE = '/tmp/t55xx_chk_out.txt'


class T55xxPwdCheckPlugin(object):
    """Entry class for the T55xx Pwd Check plugin."""

    def __init__(self, host=None):
        self.host = host

    def do_check(self):
        """Detect then chk with dic. Called via on_enter:run:do_check."""
        self.host.set_var('error_msg', '')
        self.host.set_var('found_pwds', '')
        self.host.set_var('found_pwd', '')

        # ── Step 1: imports ───────────────────────────────────────────
        try:
            import executor
        except ImportError:
            self.host.set_var('error_msg', 'Import error')
            return {'status': 'error'}

        # ── Step 2: copy dic to /tmp/.keys/ so PM3 can read it ────────
        # No detect step — go straight to chk regardless of tag state.
        # detectT55XX() succeeds on passworded cloned tags (PAC etc)
        # giving a false no_pwd result, so we skip it entirely.
        try:
            os.makedirs('/tmp/.keys', exist_ok=True)
            shutil.copy(_DIC_SRC, _DIC_DST)
        except Exception:
            self.host.set_var('error_msg', 'Copy error')
            return {'status': 'error'}

        # ── Step 4: run chk ───────────────────────────────────────────
        executor.startPM3Task(
            'lf t55xx chk -f %s' % _DIC_DST,
            180000,
        )

        # ── Step 5: get output and find password ──────────────────────
        content = executor.getPrintContent() or ''
        try:
            with open(_OUT_FILE, 'w') as f:
                f.write(content)
        except Exception:
            pass

        found_keys = []
        for line in content.splitlines():
            if 'found valid password' in line.lower():
                m = re.search(r'\[ ([A-Fa-f0-9]{8}) \]', line)
                if m:
                    key = m.group(1).upper()
                    if key not in found_keys:
                        found_keys.append(key)

        # ── Step 6: result ────────────────────────────────────────────
        if found_keys:
            # Store first password for wipe, all for display
            self.host.set_var('found_pwd', found_keys[0])
            self.host.set_var('found_pwds', '\n'.join(found_keys[:4]))
            return {'status': 'found'}

        return {'status': 'no_pwd'}

    def do_wipe(self):
        """Wipe tag using found password. Called via on_enter:run:do_wipe."""
        self.host.set_var('error_msg', '')

        try:
            import executor
        except ImportError:
            self.host.set_var('error_msg', 'Import error')
            return {'status': 'error'}

        pwd = self.host.get_var('found_pwd', '')
        if not pwd:
            self.host.set_var('error_msg', 'No password stored')
            return {'status': 'error'}

        ret = executor.startPM3Task('lf t55xx wipe -p %s' % pwd, 30000)

        if ret == -1:
            self.host.set_var('error_msg', 'Wipe failed')
            return {'status': 'error'}

        return {'status': 'done'}
