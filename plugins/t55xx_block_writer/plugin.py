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
1. warning       -- on_enter: do_init sets block_num/selected_block/data to defaults
                    M1 -> finish, M2 -> idle
2. idle          -- UP/RIGHT cycles block_num 0-7 (wraps), DOWN/LEFT noop
                    OK/M2 confirms -> stores selected_block -> input_data
                    M1 -> finish
3. input_data    -- enter 8 hex char data value, label is static (no placeholder)
                    OK/M2 captures data into state -> input_pwd_choice
                    M1 -> idle
4. input_pwd_choice -- shows selected_block and data from state vars
                       No Pwd (M1/OK) -> do_write
                       With Pwd (M2)  -> input_pwd
5. input_pwd     -- enter 8 hex char password
                    OK/M2 -> do_write_pwd
                    M1 -> input_pwd_choice
6. done / error  -- result screen shows selected_block and data from state vars

PM3 command:
    lf t55xx write -b <block> -d <data>
    lf t55xx write -b <block> -d <data> -p <pwd>

Ground truth: lft55xx.py set_key_block / lock — confirmed syntax.

Note on label placeholder:
    input_hex label is rendered before variable resolution in plugin_activity.py
    so placeholders in labels do not resolve. We avoid this entirely by using
    a static label and storing the confirmed block in selected_block, displayed
    via {selected_block} in text-type screens which resolve correctly.
"""

# Block number bounds
_BLOCK_MAX = 7

# Timeout — 60 seconds is ample for a single block write
_TIMEOUT_MS = 60000


class T55xxBlockWriterPlugin(object):
    """Entry class for the T55xx Block Writer plugin."""

    def __init__(self, host=None):
        self.host = host
        # host is not yet injected here — initialisation is done via
        # do_init which is called by on_enter on the warning state once
        # host is fully live.

    # ------------------------------------------------------------------
    # Initialisation — called via on_enter once host is injected
    # ------------------------------------------------------------------

    def do_init(self):
        """Initialise state variables. Called via on_enter on warning state."""
        self.host.set_var('block_num',      '0')
        self.host.set_var('selected_block', '0')
        self.host.set_var('data',           '')
        # Return None — no transition, stay on warning
        return None

    # ------------------------------------------------------------------
    # Block number cycling
    # ------------------------------------------------------------------

    def do_cycle(self):
        """Cycle block number upward 0-7, wrapping back to 0 after 7."""
        current    = int(self.host.get_var('block_num', '0'))
        next_block = (current + 1) % (_BLOCK_MAX + 1)
        self.host.set_var('block_num', str(next_block))
        self.host.update_screen()
        # Return None — no transition, stay on idle
        return None

    def do_confirm(self):
        """Store current block_num as selected_block and move to input_data."""
        block = self.host.get_var('block_num', '0')
        self.host.set_var('selected_block', block)
        return {'status': 'confirmed'}

    # ------------------------------------------------------------------
    # Data capture — must read input widget before state transition
    # destroys it
    # ------------------------------------------------------------------

    def do_capture_data(self):
        """Capture hex data from input widget and store in state.

        Called directly from input_data keys so the input widget is
        still alive when get_input() is called. Validates 8 hex chars
        then stores in state for do_write / do_write_pwd to use.
        """
        data = self.host.get_input().strip().upper()
        if len(data) != 8:
            return {'status': 'error'}

        # Validate all characters are hex
        try:
            int(data, 16)
        except ValueError:
            return {'status': 'error'}

        self.host.set_var('data', data)
        return {'status': 'captured'}

    # ------------------------------------------------------------------
    # Write without password
    # ------------------------------------------------------------------

    def do_write(self):
        """Write stored data to selected block without password."""
        block = self.host.get_var('selected_block', '0')
        data  = self.host.get_var('data', '')

        if not data or len(data) != 8:
            return {'status': 'error'}

        try:
            import executor
        except ImportError:
            return {'status': 'error'}

        cmd = 'lf t55xx write -b %s -d %s' % (block, data)
        ret = executor.startPM3Task(cmd, _TIMEOUT_MS)

        if ret == -1:
            return {'status': 'error'}

        return {'status': 'done'}

    # ------------------------------------------------------------------
    # Write with password
    # ------------------------------------------------------------------

    def do_write_pwd(self):
        """Capture password from input widget then write block with password.

        Called directly from input_pwd keys so the input widget is
        still alive when get_input() is called.
        """
        block = self.host.get_var('selected_block', '0')
        data  = self.host.get_var('data', '')

        if not data or len(data) != 8:
            return {'status': 'error'}

        pwd = self.host.get_input().strip().upper()
        if len(pwd) != 8:
            return {'status': 'error'}

        try:
            import executor
        except ImportError:
            return {'status': 'error'}

        cmd = 'lf t55xx write -b %s -d %s -p %s' % (block, data, pwd)
        ret = executor.startPM3Task(cmd, _TIMEOUT_MS)

        if ret == -1:
            return {'status': 'error'}

        return {'status': 'done'}
