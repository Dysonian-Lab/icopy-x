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

"""T55xx Block Writer plugin.

Workflow
--------
1. warning       -- on_enter: do_init resets all state to defaults
                    M1 -> finish, M2 -> page_picker
2. page_picker   -- UP/RIGHT cycles page 0/1, OK/M2 confirm -> idle
                    (mirrors the reader's page picker)
3. idle          -- block picker, PAGE-AWARE:
                       page 0 -> blocks 0-7, page 1 -> blocks 0-3
                    UP/RIGHT cycles up, DOWN/LEFT cycles down (both wrap)
                    OK/M2 confirms -> stores selected_block -> input_data
                    M1 -> page_picker
4. input_data    -- enter 8 hex char data value
                    OK/M2 captures data into state -> input_pwd_choice
                    M1 -> idle
5. input_pwd_choice -- shows selected_block and data
                       No Pwd (M1/OK) -> writing
                       With Pwd (M2)  -> input_pwd
6. input_pwd     -- enter 8 hex char password
                    OK/M2 -> do_capture_pwd (stores pwd) -> writing_pwd
                    M1 -> input_pwd_choice
7. writing /     -- progress screen; on_enter runs the write+verify flow
   writing_pwd      which drives set_progress between steps
8. done          -- write verified OK (read-back matched)
   verify_mismatch  read-back decoded but value differs
   verify_fail      write sent but read-back could not be decoded
   error            hard write failure (device/comms)

Write + verify strategy (ground truth: cmdlft55xx.c)
    A T55xx write is BLIND -- the tag never ACKs a write, so the write
    command returns success as long as the field was modulated.  The only
    reliable confirmation is to read the block back and compare:

        1. lf t55xx write -b <block> [--pg1] -d <data> [-p <pwd>]
        2. lf t55xx detect [-p <pwd>]          -- configure modulation
        3. lf t55xx read -b <block> [--pg1] [-p <pwd> --override]
        4. parse " NN | HEXVALUE | ..." and compare to <data>

    --pg1      selects page 1 (write accepts blocks 0-7 for either page;
               --pg1 is what chooses the page)          -- cmdlft55xx.c:1869
    --override on a password read-back skips the safety check that would
               otherwise silently drop the password AND switch to page 0
               (T55xxReadBlockEx line 922-924)          -- cmdlft55xx.c:924

    printT55xxBlock prints nothing when it cannot decode (line 1595-1596),
    so "no line" cleanly means "could not verify" while "line with a
    different value" means "mismatch".

Note on label placeholder:
    input_hex labels render before variable resolution, so we keep labels
    static and surface confirmed values via {selected_block} / {data} in
    text-type screens which resolve correctly.
"""

import re

# Block number bounds per page
_BLOCK_MAX_PAGE0 = 7   # page 0 -> blocks 0-7 (8 blocks)
_BLOCK_MAX_PAGE1 = 3   # page 1 -> blocks 0-3 (4 blocks)

# Timeout -- 60 seconds is ample for a single write / detect / read
_TIMEOUT_MS = 60000

# Detect failure keyword -- same ground truth as the reader / detect plugins
_KW_COULD_NOT_DETECT = 'Could not detect modulation automatically'

# Single-block read output regex -- printT55xxBlock (cmdlft55xx.c:1603)
# Format: " NN | HEXVALUE | binary | ascii"
_RE_READ_BLOCK = r'\d+\s*\|\s*([A-Fa-f0-9]{8})'

# Progress checkpoints (0-100)
_PROG_WRITE    = 10
_PROG_DETECT   = 40
_PROG_READBACK = 70
_PROG_VERIFY   = 95

# Page 1 traceability note shown on the two non-OK verify outcomes
_NOTE_TRACEABILITY = 'Page 1 is traceability;\nnot always writable'


class T55xxBlockWriterPlugin(object):
    """Entry class for the T55xx Block Writer plugin."""

    def __init__(self, host=None):
        self.host = host
        # host is not yet injected here -- initialisation is done via
        # do_init which is called by on_enter on the warning state once
        # host is fully live.

    # ------------------------------------------------------------------
    # Initialisation -- called via on_enter once host is injected
    # ------------------------------------------------------------------

    def do_init(self):
        """Reset all state variables. Called via on_enter on warning state."""
        self.host.set_var('page_num',      '0')
        self.host.set_var('block_num',     '0')
        self.host.set_var('selected_block', '0')
        self.host.set_var('data',          '')
        self.host.set_var('pwd',           '')
        self.host.set_var('readback',      '')
        self.host.set_var('verify_note',   '')
        self.host.set_progress(0, '')
        # Return None -- no transition, stay on warning
        return None

    # ------------------------------------------------------------------
    # Page number cycling (0 or 1 only) -- mirrors the reader
    # ------------------------------------------------------------------

    def do_cycle_page(self):
        """Cycle page_num between 0 and 1.

        Resets block_num to 0 so a block selected on page 0 (e.g. 7) can
        never be left out of range when switching to page 1 (max 3).
        """
        current = int(self.host.get_var('page_num', '0'))
        self.host.set_var('page_num', '1' if current == 0 else '0')
        self.host.set_var('block_num', '0')
        self.host.update_screen()
        return None

    def do_confirm_page(self):
        """Confirm page selection.

        Page 1 routes through a traceability notice first; page 0 goes
        straight to the block picker.
        """
        page = int(self.host.get_var('page_num', '0'))
        if page == 1:
            return {'status': 'page1'}
        return {'status': 'confirmed'}

    # ------------------------------------------------------------------
    # Block number cycling -- page-aware, wraps in both directions
    # ------------------------------------------------------------------

    def _block_max(self):
        """Return the highest valid block index for the selected page."""
        page = int(self.host.get_var('page_num', '0'))
        return _BLOCK_MAX_PAGE1 if page == 1 else _BLOCK_MAX_PAGE0

    def do_cycle(self):
        """Cycle block number upward, wrapping to 0 after the page max."""
        span    = self._block_max() + 1
        current = int(self.host.get_var('block_num', '0'))
        self.host.set_var('block_num', str((current + 1) % span))
        self.host.update_screen()
        return None

    def do_cycle_down(self):
        """Cycle block number downward, wrapping to the page max below 0."""
        span    = self._block_max() + 1
        current = int(self.host.get_var('block_num', '0'))
        self.host.set_var('block_num', str((current - 1) % span))
        self.host.update_screen()
        return None

    def do_confirm(self):
        """Store current block_num as selected_block and move to input_data."""
        block = self.host.get_var('block_num', '0')
        self.host.set_var('selected_block', block)
        return {'status': 'confirmed'}

    # ------------------------------------------------------------------
    # Data capture -- must read input widget before state transition
    # destroys it
    # ------------------------------------------------------------------

    def do_capture_data(self):
        """Capture hex data from input widget and store in state.

        Called directly from input_data keys so the input widget is
        still alive when get_input() is called. Validates 8 hex chars
        then stores in state for the write flow to use.
        """
        data = self.host.get_input().strip().upper()
        if len(data) != 8:
            return {'status': 'error'}
        try:
            int(data, 16)
        except ValueError:
            return {'status': 'error'}
        self.host.set_var('data', data)
        return {'status': 'captured'}

    # ------------------------------------------------------------------
    # Password capture -- must read the input widget BEFORE we transition
    # to the progress screen, which destroys the widget
    # ------------------------------------------------------------------

    def do_capture_pwd(self):
        """Capture and validate the password, store it for the write flow.

        Called directly from input_pwd keys so the input widget is still
        alive. The actual write runs later on the writing_pwd state, which
        reads the stored password back from state.
        """
        pwd = self.host.get_input().strip().upper()
        if len(pwd) != 8:
            return {'status': 'error'}
        try:
            int(pwd, 16)
        except ValueError:
            return {'status': 'error'}
        self.host.set_var('pwd', pwd)
        return {'status': 'captured'}

    # ------------------------------------------------------------------
    # Core write + read-back verify -- shared by both writing states
    # ------------------------------------------------------------------

    def _verify_fail(self, page):
        """Populate the traceability note (page 1 only) and return noverify."""
        if page == 1:
            self.host.set_var('verify_note', _NOTE_TRACEABILITY)
        return {'status': 'noverify'}

    def _run_write(self, pwd=None):
        """Write the block, then verify by reading it back and comparing.

        Runs in a background thread (via on_enter run:) so set_progress
        updates render live on the progress screen.

        Returns a status dict for the state machine:
            done      -- read-back matched the written data
            mismatch  -- read-back decoded but value differed
            noverify  -- write sent but read-back could not be decoded
            error     -- hard write failure (device/comms) or bad state
        """
        block = self.host.get_var('selected_block', '0')
        data  = self.host.get_var('data', '')
        page  = int(self.host.get_var('page_num', '0'))

        # Clear any prior verify results before we start
        self.host.set_var('readback', '')
        self.host.set_var('verify_note', '')

        if not data or len(data) != 8:
            return {'status': 'error'}

        try:
            import executor
        except ImportError:
            return {'status': 'error'}

        pg1 = ' --pg1' if page == 1 else ''

        # Step 1 -- write (blind; no tag ACK)
        self.host.set_progress(_PROG_WRITE, 'Writing block %s...' % block)
        cmd = 'lf t55xx write -b %s%s -d %s' % (block, pg1, data)
        if pwd:
            cmd += ' -p %s' % pwd
        if executor.startPM3Task(cmd, _TIMEOUT_MS) == -1:
            return {'status': 'error'}

        # Step 2 -- detect to configure modulation for the read-back
        self.host.set_progress(_PROG_DETECT, 'Configuring (detect)...')
        dcmd = 'lf t55xx detect'
        if pwd:
            dcmd += ' -p %s' % pwd
        if executor.startPM3Task(dcmd, _TIMEOUT_MS) == -1:
            return self._verify_fail(page)
        if executor.hasKeyword(_KW_COULD_NOT_DETECT):
            return self._verify_fail(page)

        # Step 3 -- read the single block back
        self.host.set_progress(_PROG_READBACK, 'Reading back...')
        rcmd = 'lf t55xx read -b %s%s' % (block, pg1)
        if pwd:
            # --override avoids the safety check that would otherwise drop
            # the password and silently switch the read to page 0
            rcmd += ' -p %s --override' % pwd
        if executor.startPM3Task(rcmd, _TIMEOUT_MS) == -1:
            return self._verify_fail(page)

        # Step 4 -- parse and compare
        self.host.set_progress(_PROG_VERIFY, 'Verifying...')
        readback = executor.getContentFromRegex(_RE_READ_BLOCK)
        if not readback:
            # No decodable block line -> could not verify (tag moved / locked)
            return self._verify_fail(page)

        readback = readback.upper()
        self.host.set_var('readback', readback)

        if readback == data:
            return {'status': 'done'}

        # Decoded a value, but it does not match what we wrote
        if page == 1:
            self.host.set_var('verify_note', _NOTE_TRACEABILITY)
        return {'status': 'mismatch'}

    # ------------------------------------------------------------------
    # Write entry points -- one per writing state
    # ------------------------------------------------------------------

    def do_write(self):
        """Write + verify without a password (writing state on_enter)."""
        return self._run_write(pwd=None)

    def do_write_pwd(self):
        """Write + verify with the previously captured password.

        writing_pwd state on_enter. The password was validated and stored
        by do_capture_pwd while the input widget was still alive.
        """
        pwd = self.host.get_var('pwd', '')
        if len(pwd) != 8:
            return {'status': 'error'}
        return self._run_write(pwd=pwd)
