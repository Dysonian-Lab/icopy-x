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

"""PAC Clone & Lock plugin.

Workflow
--------
1. do_scan()         -- lf sea -> lfsearch.parser() -> confirm PAC tag
2. do_dump()         -- lfread.readPAC(save=True) -> saves to /mnt/upan/dump/pac/
3. do_write()        -- lfwrite.write_raw() no password, plain clone
4. do_write_pwd()    -- capture password from input widget, store, transition
5. do_write_actual() -- wipe -> detect -> lf pac clone --raw (writes all
                        blocks + plain B0 00080080, PM3 verifies) -> write
                        block 7 with password (no -p, tag unlocked) ->
                        write block 0 with PWD bit set 00080090 (no -p) ->
                        lf t55xx detect -p <pwd> (real lock verify)

PAC B0 config: 00080080 (NRZ, data rate 32, 4 data blocks)
PAC B0 locked: 00080090 (same + T55x7_PWD bit 28 set)
"""

import tagtypes as _tagtypes

# T55x7_PWD bit (bit 28 from MSB) — from cmdlft55xx.h
T55X7_PWD   = 0x00000010

# PAC B0 config word — from cmdlfpac.c and lfwrite.B0_WRITE_MAP
PAC_B0      = '00080080'
PAC_B0_LOCK = '%08X' % (int(PAC_B0, 16) | T55X7_PWD)  # 00080090


class PACCloneLockPlugin(object):
    """Entry class for the PAC Clone & Lock plugin."""

    def __init__(self, host=None):
        self.host = host

    # ------------------------------------------------------------------
    # Step 1: Scan
    # ------------------------------------------------------------------

    def do_scan(self):
        """Scan for PAC tag only."""
        self.host.set_var('error_msg', '')
        self.host.set_var('tag_id',   '')
        self.host.set_var('tag_raw',  '')

        try:
            import executor
            import lfsearch
        except ImportError:
            self.host.set_var('error_msg', 'Import error')
            return {'status': 'error'}

        executor.startPM3Task(lfsearch.CMD, lfsearch.TIMEOUT)
        result = lfsearch.parser()

        if not result.get('found'):
            self.host.set_var('error_msg', 'No tag found')
            return {'status': 'error'}

        if result.get('type') != _tagtypes.PAC_ID:
            self.host.set_var('error_msg', 'Not a PAC tag')
            return {'status': 'error'}

        tag_id  = result.get('data') or result.get('raw') or ''
        tag_raw = result.get('raw') or ''

        self.host.set_var('tag_id',  tag_id[:20])
        self.host.set_var('tag_raw', tag_raw)

        return {'status': 'found'}

    # ------------------------------------------------------------------
    # Step 2: Dump
    # ------------------------------------------------------------------

    def do_dump(self):
        """Save PAC dump to /mnt/upan/dump/pac/."""
        self.host.set_var('error_msg', '')

        try:
            import lfread
        except ImportError:
            self.host.set_var('error_msg', 'Import error')
            return {'status': 'error'}

        ret = lfread.readPAC(save=True)
        if isinstance(ret, dict) and ret.get('return') == -1:
            self.host.set_var('error_msg', 'Dump failed')
            return {'status': 'error'}

        return {'status': 'done'}

    # ------------------------------------------------------------------
    # Step 3a: Write without password
    # ------------------------------------------------------------------

    def do_write(self):
        """Write PAC clone to T55xx without password."""
        self.host.set_var('error_msg', '')
        self.host.set_var('done_msg', 'Written without password')

        try:
            import lfwrite
            import executor
        except ImportError:
            self.host.set_var('error_msg', 'Import error')
            return {'status': 'error'}

        raw = self.host.get_var('tag_raw', '')
        if not raw:
            self.host.set_var('error_msg', 'No tag data')
            return {'status': 'error'}

        # Wipe to clean state
        executor.startPM3Task('lf t55xx wipe', 15000)
        ret = executor.startPM3Task('lf t55xx detect', 10000)
        if ret == -1:
            self.host.set_var('error_msg', 'T55xx not detected')
            return {'status': 'error'}

        ret = lfwrite.write_raw(_tagtypes.PAC_ID, raw, key=None)
        if ret == -1:
            self.host.set_var('error_msg', 'Write failed')
            return {'status': 'error'}

        return {'status': 'done'}

    # ------------------------------------------------------------------
    # Step 3b: Capture password (widget still alive)
    # ------------------------------------------------------------------

    def do_write_pwd(self):
        """Capture password from input widget while it is still alive."""
        self.host.set_var('error_msg', '')
        self.host.set_var('done_msg', '')
        self.host.set_var('write_pwd', '')

        pwd = self.host.get_input().strip().upper()
        if len(pwd) != 8:
            self.host.set_var('error_msg', 'Need 8 hex chars')
            return {'status': 'error'}

        self.host.set_var('write_pwd', pwd)
        return {'status': 'writing'}

    # ------------------------------------------------------------------
    # Step 3c: Write with password lock
    # ------------------------------------------------------------------

    def do_write_actual(self):
        """Write PAC clone with password lock.

        Sequence:
          1. wipe -> detect (confirm blank T55xx)
          2. lf pac clone --raw <raw> (writes + verifies all data blocks
             with plain PAC B0 00080080)
          3. write block 7 with password — no -p (tag still unlocked)
          4. write block 0 with PWD bit set 00080090 — no -p (tag still
             unlocked; PWD bit activates once block 7 has the password)
          5. lf t55xx detect -p <pwd> — real verify: tag must authenticate
             to respond; absence of modulation error confirms lock is active
        """
        self.host.set_var('error_msg', '')
        self.host.set_var('done_msg', '')

        pwd = self.host.get_var('write_pwd', '')
        if len(pwd) != 8:
            self.host.set_var('error_msg', 'No password stored')
            return {'status': 'error'}

        raw = self.host.get_var('tag_raw', '')
        if not raw:
            self.host.set_var('error_msg', 'No tag data')
            return {'status': 'error'}

        try:
            import executor
        except ImportError:
            self.host.set_var('error_msg', 'Import error')
            return {'status': 'error'}

        # Step 1: wipe to clean state then detect
        executor.startPM3Task('lf t55xx wipe', 15000)
        ret = executor.startPM3Task('lf t55xx detect', 10000)
        if ret == -1:
            self.host.set_var('error_msg', 'T55xx not detected')
            return {'status': 'error'}

        # Step 2: clone all PAC data blocks with plain B0 (writes + verifies)
        ret = executor.startPM3Task('lf pac clone --raw %s' % raw, 15000)
        if ret == -1 or not executor.hasKeyword('Done'):
            self.host.set_var('error_msg', 'Clone failed')
            return {'status': 'error'}

        # Step 3: write password to block 7 (tag still unlocked)
        ret = executor.startPM3Task(
            'lf t55xx write -b 7 -d %s' % pwd, 15000)
        if ret == -1:
            self.host.set_var('error_msg', 'Set pwd failed')
            return {'status': 'error'}

        # Step 4: write block 0 with PWD bit set (tag still unlocked —
        # PWD bit only activates once block 7 has the password value)
        ret = executor.startPM3Task(
            'lf t55xx write -b 0 -d %s' % PAC_B0_LOCK, 15000)
        if ret == -1:
            self.host.set_var('error_msg', 'Lock bit failed')
            return {'status': 'error'}

        # Step 5: verify lock is active — tag must authenticate with password
        # to respond to detect. If wrong/no password: "Could not detect
        # modulation automatically". Correct password: Block0 00080090 +
        # "Password set...... Yes"
        executor.startPM3Task('lf t55xx detect -p %s' % pwd, 10000)
        if executor.hasKeyword('Could not detect modulation automatically'):
            self.host.set_var('error_msg', 'Lock verify failed')
            return {'status': 'error'}

        self.host.set_var('done_msg', 'Written & locked\n%s' % pwd)
        return {'status': 'done'}
