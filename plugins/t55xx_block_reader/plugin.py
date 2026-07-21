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

"""T55xx Block Reader plugin.

Workflow
--------
1. idle            -- on_enter: do_init resets all state
                      M1 -> single block picker
                      M2 -> range start picker
2. block_picker    -- UP/RIGHT cycles 0-7, OK/M2 confirm -> input_pwd_choice
3. range_start     -- UP/RIGHT cycles 0-6, OK/M2 confirm -> range_end
4. range_end       -- UP/RIGHT cycles (start+1)-7, OK/M2 confirm -> input_pwd_choice
5. input_pwd_choice-- M1 No Pwd -> do_read, M2 With Pwd -> input_pwd
6. input_pwd       -- input_hex length 8, OK/M2 -> do_read_pwd
7. result          -- all blocks read OK, shows requested range lines
8. partial_error   -- stopped on first failure, shows partial results
9. error           -- detect or first block failed immediately

Read strategy (ground truth: lft55xx.py, cmdlft55xx.c):
    1. lf t55xx detect [-p <pwd>]   -- configure PM3 modulation/bitrate
    2. lf t55xx dump --ns [-p <pwd>]-- dump all blocks, no file save
       Output format (cmdlft55xx.c:1831):
           "00 | 0xDEADBEEF | 11011110..."
       Regex: r'(\\d+)\\s*\\|\\s*0x([A-Fa-f0-9]{8})'
    3. Extract all 8 blocks into dict, slice to user-selected range
    4. Populate result_l0..result_l7 vars for display
"""

import re

# Block bounds per page
_BLOCK_MAX = 7          # page 0 -> blocks 0-7 (8 blocks)
_BLOCK_MAX_PAGE1 = 3    # page 1 -> blocks 0-3 (4 blocks)

# Timeout — 180 seconds covers detect + dump comfortably
_TIMEOUT_MS = 180000

# Progress checkpoints (0-100)
_PROG_DETECT = 20
_PROG_DUMP   = 60
_PROG_PARSE  = 90

# Detect failure keyword — same as detect plugin ground truth
_KW_COULD_NOT_DETECT = 'Could not detect modulation automatically'

# Dump block output regex — cmdlft55xx.c:1831
# Format: "00 | 0xDEADBEEF | binary..."
# Ground truth: cmdlft55xx.c:1603 printT55xxBlock output
# Format: ' 01 | DEADBEEF | binary | ascii' (no 0x prefix)
_RE_DUMP_BLOCK = re.compile(r'(\d+)\s*\|\s*([A-Fa-f0-9]{8})')

# Number of result line vars
_MAX_RESULT_LINES = 8


def _clear_result_lines(host):
    """Clear all result line vars to empty string."""
    for i in range(_MAX_RESULT_LINES):
        host.set_var('result_l%d' % i, '')


class T55xxBlockReaderPlugin(object):
    """Entry class for the T55xx Block Reader plugin."""

    def __init__(self, host=None):
        self.host = host
        # host not yet injected — initialised via do_init on idle on_enter

    # ------------------------------------------------------------------
    # Initialisation
    # ------------------------------------------------------------------

    def do_init(self):
        """Reset all state vars. Called via on_enter on page_picker state."""
        self.host.set_var('page_num',     '0')
        self.host.set_var('block_num',    '0')
        self.host.set_var('range_start',  '0')
        self.host.set_var('range_end',    '1')
        self.host.set_var('read_mode',    'single')
        self.host.set_var('failed_block', '')
        self.host.set_var('pwd',          '')
        self.host.set_progress(0, '')
        _clear_result_lines(self.host)
        return None

    # ------------------------------------------------------------------
    # Page number cycling (0 or 1 only)
    # ------------------------------------------------------------------

    def do_cycle_page(self):
        """Cycle page_num between 0 and 1 (used for both up and down).

        Resets the block/range selection to 0 so a block chosen on page 0
        (e.g. 7) can never be left out of range when switching to page 1
        (max 3).
        """
        current = int(self.host.get_var('page_num', '0'))
        self.host.set_var('page_num', '1' if current == 0 else '0')
        self.host.set_var('block_num',   '0')
        self.host.set_var('range_start', '0')
        self.host.set_var('range_end',   '1')
        self.host.update_screen()
        return None

    def do_confirm_page(self):
        """Confirm page selection and move to idle (read mode picker)."""
        return {'status': 'confirmed'}

    # ------------------------------------------------------------------
    # Block number cycling — page-aware, wraps in both directions
    # ------------------------------------------------------------------

    def _block_max(self):
        """Return the highest valid block index for the selected page."""
        page = int(self.host.get_var('page_num', '0'))
        return _BLOCK_MAX_PAGE1 if page == 1 else _BLOCK_MAX

    def do_cycle(self):
        """Cycle block_num upward, wrapping to 0 after the page max."""
        span    = self._block_max() + 1
        current = int(self.host.get_var('block_num', '0'))
        self.host.set_var('block_num', str((current + 1) % span))
        self.host.update_screen()
        return None

    def do_cycle_down(self):
        """Cycle block_num downward, wrapping to the page max below 0."""
        span    = self._block_max() + 1
        current = int(self.host.get_var('block_num', '0'))
        self.host.set_var('block_num', str((current - 1) % span))
        self.host.update_screen()
        return None

    def do_cycle_end(self):
        """Cycle range end upward — min is range_start+1, wraps at page max."""
        start      = int(self.host.get_var('range_start', '0'))
        min_end    = start + 1
        current    = int(self.host.get_var('block_num', str(min_end)))
        next_block = current + 1
        if next_block > self._block_max():
            next_block = min_end
        self.host.set_var('block_num', str(next_block))
        self.host.update_screen()
        return None

    def do_cycle_end_down(self):
        """Cycle range end downward — wraps to page max below range_start+1."""
        start     = int(self.host.get_var('range_start', '0'))
        min_end   = start + 1
        current   = int(self.host.get_var('block_num', str(min_end)))
        prev_block = current - 1
        if prev_block < min_end:
            prev_block = self._block_max()
        self.host.set_var('block_num', str(prev_block))
        self.host.update_screen()
        return None

    # ------------------------------------------------------------------
    # Confirm selections
    # ------------------------------------------------------------------

    def do_confirm_single(self):
        """Confirm single block selection."""
        self.host.set_var('read_mode', 'single')
        return {'status': 'confirmed'}

    def do_confirm_start(self):
        """Confirm range start block, seed block_num to start+1 for end picker."""
        start = int(self.host.get_var('block_num', '0'))
        if start > self._block_max() - 1:
            start = self._block_max() - 1
        self.host.set_var('range_start', str(start))
        self.host.set_var('block_num', str(start + 1))
        return {'status': 'confirmed'}

    def do_confirm_end(self):
        """Confirm range end block."""
        end = int(self.host.get_var('block_num', '1'))
        self.host.set_var('range_end', str(end))
        self.host.set_var('read_mode', 'range')
        return {'status': 'confirmed'}

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _detect(self, executor, pwd=None, page=0):
        """Run lf t55xx detect to configure PM3 modulation before dump.

        Same command for both page 0 and page 1 — dump handles both pages.

        Returns True on success, False on failure.
        """
        cmd = 'lf t55xx detect'
        if pwd:
            cmd += ' -p %s' % pwd
        ret = executor.startPM3Task(cmd, _TIMEOUT_MS)
        if ret == -1:
            return False
        if executor.hasKeyword(_KW_COULD_NOT_DETECT):
            return False
        return True

    def _dump_all_blocks(self, executor, pwd=None, page=0):
        """Run lf t55xx detect + dump --ns and extract blocks for the selected page.

        Both page 0 and page 1 use the same commands:
            lf t55xx detect [-p <pwd>]
            lf t55xx dump --ns [-p <pwd>]

        The dump output contains both pages separated by "Page 0" / "Page 1"
        section headers. We locate the correct section and run the existing
        regex only against that portion of the output.

        Returns dict of {block_num: hex_str} for all blocks found in the
        selected page section, or empty dict on failure.
        """
        cmd = 'lf t55xx dump --ns'
        if pwd:
            cmd += ' -p %s --override' % pwd
        ret = executor.startPM3Task(cmd, _TIMEOUT_MS)
        if ret == -1:
            return {}

        content = executor.getPrintContent() or ''

        # Split content on the page section we want
        section_header = 'Page %d' % page
        idx = content.find(section_header)
        if idx == -1:
            # Header not found — fall back to full content for page 0
            if page == 0:
                section = content
            else:
                return {}
        else:
            # Extract from section header to next page header or end
            next_page_header = 'Page %d' % (page + 1)
            next_idx = content.find(next_page_header, idx + len(section_header))
            if next_idx == -1:
                section = content[idx:]
            else:
                section = content[idx:next_idx]

        blocks = {}
        for m in _RE_DUMP_BLOCK.finditer(section):
            b       = int(m.group(1))
            hex_val = m.group(2).upper()
            blocks[b] = hex_val

        return blocks

    def _populate_result_vars(self, blocks, start, end):
        """Slice blocks dict to requested range and populate result line vars.

        Fills result_l0 upward with 'B<n>: HEXVALUE' for each block
        in range. Blocks missing from the dump dict show as '????????'.
        Remaining lines are cleared to empty string.
        """
        _clear_result_lines(self.host)
        line_idx = 0
        for b in range(start, end + 1):
            if line_idx >= _MAX_RESULT_LINES:
                break
            hex_val = blocks.get(b, '????????')
            self.host.set_var('result_l%d' % line_idx,
                              'B%d: %s' % (b, hex_val))
            line_idx += 1

    def _run_read(self, pwd=None):
        """Core read logic shared by do_read and do_read_pwd.

        1. Detect tag
        2. Dump all blocks with --ns
        3. Slice to requested range
        4. Populate result vars

        Returns status dict for state machine transition.
        """
        try:
            import executor
        except ImportError:
            return {'status': 'error'}

        mode = self.host.get_var('read_mode', 'single')

        if mode == 'single':
            block = int(self.host.get_var('block_num', '0'))
            start = block
            end   = block
        else:
            start = int(self.host.get_var('range_start', '0'))
            end   = int(self.host.get_var('range_end',   '1'))

        page = int(self.host.get_var('page_num', '0'))

        # Step 1 — detect
        self.host.set_progress(_PROG_DETECT, 'Configuring (detect)...')
        if not self._detect(executor, pwd=pwd, page=page):
            return {'status': 'error'}

        # Step 2 — dump all blocks, no file save
        self.host.set_progress(_PROG_DUMP, 'Reading blocks...')
        blocks = self._dump_all_blocks(executor, pwd=pwd, page=page)
        if not blocks:
            return {'status': 'error'}

        # Step 3+4 — slice to range and populate vars
        self.host.set_progress(_PROG_PARSE, 'Parsing...')
        self._populate_result_vars(blocks, start, end)

        # Check if any block in the requested range is missing
        missing = [b for b in range(start, end + 1) if b not in blocks]
        if missing:
            self.host.set_var('failed_block', str(missing[0]))
            return {'status': 'done'}

        return {'status': 'done'}

    # ------------------------------------------------------------------
    # Read without password
    # ------------------------------------------------------------------

    def do_read(self):
        """Detect, dump and display requested block(s) without password.

        reading state on_enter — runs on the progress screen so the
        set_progress updates in _run_read render live.
        """
        return self._run_read(pwd=None)

    # ------------------------------------------------------------------
    # Read with password
    # ------------------------------------------------------------------

    def do_capture_pwd(self):
        """Capture and validate the password while the input widget is alive.

        The actual read runs later on the reading_pwd state (a progress
        screen), which destroys the input widget, so we grab the value
        here and store it for do_read_pwd to read back.
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

    def do_read_pwd(self):
        """Detect, dump and display using the previously captured password.

        reading_pwd state on_enter. The password was validated and stored
        by do_capture_pwd while the input widget was still alive.
        """
        pwd = self.host.get_var('pwd', '')
        if len(pwd) != 8:
            return {'status': 'error'}
        return self._run_read(pwd=pwd)
