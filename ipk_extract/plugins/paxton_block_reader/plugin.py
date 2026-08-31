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

"""Paxton Block Reader plugin.

Workflow (mirrors lfread.readPaxton ground truth, diagnostic-only)
------------------------------------------------------------------
1. Pre-flight 1: lf search
                 -> must emit 'Valid Paxton ID found!' to confirm the
                    card is actually Paxton before proceeding.
2. Pre-flight 2: lf hitag read --ht2 -k BDF5E846
                 -> authenticated block read (Paxton default key baked in;
                    this is why there is no password option in the UI).

From the pre-flight 2 output it extracts blocks 4-7 (32-char hex payload),
derives the product family (Net2 / Switch2) from the block nibbles, and
reads the Paxton hex ID (unpadded, display only).

NOTHING is saved -- this is a read/diagnostic plugin only.

Ground-truth reuse
------------------
Commands, regex patterns (lfsearch.REGEX_HITAG2_BLOCK /
lfsearch.REGEX_PAXTON_HEX) and the Net2/Switch2 nibble logic are the same
ones used by lfread.readPaxton(). The regexes are imported from lfsearch
(single source of truth) rather than copied, so they can never drift.

    Net2:    block 5, nibble 4 (payload[11]) == 'F'
    Switch2: block 5, nibble 4 != 'F'  AND
             block 7, nibble 5 (payload[28]) == '1'  AND
             block 7, nibble 4 (payload[27]) is even
"""

import re

# Progress checkpoints
_PROG_START   =  0
_PROG_SEARCH  = 15
_PROG_READ    = 45
_PROG_PARSE   = 80
_PROG_DONE    = 100

# Paxton authenticated read -- default key, same as lfread.readPaxton
_CMD_SEARCH = 'lf search'
_CMD_READ   = 'lf hitag read --ht2 -k BDF5E846'
_KW_PAXTON  = 'Valid Paxton ID found!'

# Timeout -- 30 s, identical to lfread.readPaxton
_TIMEOUT_MS = 30000


class PaxtonBlockReaderPlugin(object):
    """Entry class for the Paxton Block Reader plugin."""

    def __init__(self, host=None):
        self.host = host

    def _clear_vars(self):
        """Reset all display variables to safe defaults."""
        for key in ('blk0', 'blk1', 'blk2', 'blk3',
                    'blk4', 'blk5', 'blk6', 'blk7',
                    'pax_type', 'uid', 'pax_id_line', 'error_msg'):
            self.host.set_var(key, '')

    def _fail(self, message):
        """Populate error_msg and return the plugin error status."""
        self.host.set_var('error_msg', message)
        return {'status': 'error'}

    # ------------------------------------------------------------------
    # Read (no password, no save)
    # ------------------------------------------------------------------

    def do_read(self):
        """Run the two Paxton pre-flight checks and display blocks 0-7.

        Blocks 0-3 are extracted for display only; blocks 4-7 remain the
        Paxton user payload that drives the Net2/Switch2 type logic below.
        """
        self._clear_vars()
        self.host.set_progress(_PROG_START, 'Starting...')

        try:
            import executor
            import lfsearch
        except ImportError:
            return self._fail('Import error')

        # -- Pre-flight 1: confirm this is a Paxton card ----------------
        self.host.set_progress(_PROG_SEARCH, 'Searching...')
        ret = executor.startPM3Task(_CMD_SEARCH, _TIMEOUT_MS)
        if ret == -1 or not executor.hasKeyword(_KW_PAXTON):
            return self._fail('Not a Paxton tag')

        # -- Pre-flight 2: authenticated block read ---------------------
        self.host.set_progress(_PROG_READ, 'Reading blocks...')
        ret = executor.startPM3Task(_CMD_READ, _TIMEOUT_MS)
        if ret == -1:
            return self._fail('Read failed')

        content = executor.getPrintContent()
        if not content or executor.isEmptyContent():
            return self._fail('Read failed')

        # -- Extract blocks 0-3 (display only — NOT Paxton payload) -----
        # Block 0 = Hitag2 UID, 1 = Paxton signature/password area,
        # 2 = reserved, 3 = config. Kept in a separate loop so they never
        # enter `payload` and cannot shift the nibble indices the type
        # logic below relies on.
        self.host.set_progress(_PROG_PARSE, 'Parsing results...')
        display = {}
        for blk in range(0, 4):
            pattern = lfsearch.REGEX_HITAG2_BLOCK.format(b=blk)
            m = re.search(pattern, content)
            if not m:
                return self._fail('Parse failed')
            display[blk] = m.group(1)

        # -- Extract blocks 4-7 (Paxton user payload — drives type) -----
        payload = ''
        for blk in range(4, 8):
            pattern = lfsearch.REGEX_HITAG2_BLOCK.format(b=blk)
            m = re.search(pattern, content)
            if not m:
                return self._fail('Parse failed')
            grouped = m.group(1)                    # e.g. "39 04 21 1C"
            display[blk] = grouped
            payload += grouped.replace(' ', '')     # e.g. "3904211C"

        if len(payload) != 32:
            return self._fail('Parse failed')

        # -- Paxton hex ID (unpadded, display only) ---------------------
        pax_hex = executor.getContentFromRegexG(lfsearch.REGEX_PAXTON_HEX, 1)
        pax_id = pax_hex.strip() if pax_hex else '?'

        # -- Hitag2 UID -- same regex as lfread.readPaxton -------------
        # print_hitag2_configuration() emits "UID...... <8 hex>" for the
        # `lf hitag read --ht2` output we parse above.
        uid = executor.getContentFromRegexG(r'UID\.{3,}\s+([0-9A-Fa-f]+)', 1)
        uid = uid.strip() if uid else '?'

        # -- Determine product family (Net2 / Switch2) ------------------
        # Same nibble logic as lfread.readPaxton.
        #
        # The Paxton ID is only shown for Net2: Iceman's print_hitag2_paxton()
        # de-scramble (cmdlfhitag.c) has a single Net2-only path with no
        # Switch2 branch, so the decoded ID is invalid for a Switch2 card.
        # pax_id_line follows the t55xx-detect pwd_line pattern -- an empty
        # string blanks the line in the static UI.
        if payload[11].upper() == 'F':
            pax_type = 'Net2'
            pax_id_line = 'PaxID: %s' % pax_id
        else:
            nib4 = payload[27].upper()
            nib5 = payload[28].upper()
            is_switch2 = (nib5 == '1' and int(nib4, 16) % 2 == 0)
            if not is_switch2:
                return self._fail('Unknown Paxton type')
            pax_type = 'Switch2'
            pax_id_line = ''

        # -- Publish results --------------------------------------------
        self.host.set_var('blk0', display[0])
        self.host.set_var('blk1', display[1])
        self.host.set_var('blk2', display[2])
        self.host.set_var('blk3', display[3])
        self.host.set_var('blk4', display[4])
        self.host.set_var('blk5', display[5])
        self.host.set_var('blk6', display[6])
        self.host.set_var('blk7', display[7])
        self.host.set_var('pax_type', pax_type)
        self.host.set_var('uid', uid)
        self.host.set_var('pax_id_line', pax_id_line)

        self.host.set_progress(_PROG_DONE, 'Done')
        return {'status': 'done'}
