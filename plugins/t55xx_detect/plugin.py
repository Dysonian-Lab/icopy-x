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

"""T55xx Detect plugin.

Workflow
--------
1. do_detect()     -- lf t55xx detect (no password)
2. do_detect_pwd() -- capture password from input widget
                      -> lf t55xx detect -p <pwd>

Both methods parse the PM3 output using the same regex patterns as
lft55xx.py and populate the result screen variables:
    chip, modulation, bitrate, inverted, block0, pwd_set, pwd_line

Regex patterns are iceman-native, sourced directly from lft55xx.py
ground truth (cmdlft55xx.c:1837-1848 printConfiguration output).
"""

import re

# ---------------------------------------------------------------------------
# Regex patterns — iceman-native, identical to lft55xx.py ground truth.
# cmdlft55xx.c:1837-1848 printConfiguration() output format:
#     " Chip type......... T55x7"
#     " Modulation........ ASK"
#     " Bit rate.......... RF/32"
#     " Inverted.......... No"
#     " Block0............ DEADBEEF (auto detect)"
#     " Password set...... No"
#     " Password.......... 00000000"
# ---------------------------------------------------------------------------
_RE_CHIP_TYPE  = r'Chip [Tt]ype\.+\s+(\S+)'
_RE_MODULATE   = r'Modulation\.+\s+(\S+)'
_RE_BITRATE    = r'Bit rate\.+\s+(\S+)'
_RE_INVERTED   = r'Inverted\.+\s+(\S+)'
_RE_BLOCK0     = r'Block0\.+\s+([A-Fa-f0-9]+)'
_RE_PWD_SET    = r'Password set\.+\s+(\S+)'
# {8,} excludes the shorter 6-dot "Password set......" line — see lft55xx.py
_RE_PWD        = r'[Pp]assword\.{8,}\s+([A-Fa-f0-9]+)'

_KW_COULD_NOT_DETECT = 'Could not detect modulation automatically'

# Timeout — 180 seconds, sufficient for all T55xx detect scenarios
_TIMEOUT_MS = 180000


def _parse_detect_output(executor):
    """Parse PM3 detect output from executor cache.

    Returns a dict of display variables, or None if detect failed.

    Fields returned:
        chip        -- e.g. 'T55x7' or 'Q5/T5555'
        modulation  -- e.g. 'ASK'
        bitrate     -- e.g. 'RF/32'
        inverted    -- e.g. 'No'
        block0      -- e.g. 'DEADBEEF'
        pwd_set     -- e.g. 'No' or 'Yes'
        pwd_line    -- e.g. 'Key:  DEADBEEF' if pwd_set==Yes, else ''
    """
    if executor.hasKeyword(_KW_COULD_NOT_DETECT):
        return None

    def _get(pattern):
        val = executor.getContentFromRegex(pattern)
        return val.strip() if val else ''

    chip       = _get(_RE_CHIP_TYPE)
    modulation = _get(_RE_MODULATE)
    bitrate    = _get(_RE_BITRATE)
    inverted   = _get(_RE_INVERTED)
    block0     = _get(_RE_BLOCK0)
    pwd_set    = _get(_RE_PWD_SET)

    # Only populate pwd_line when password is actually set
    pwd_line = ''
    if pwd_set.lower() == 'yes':
        pwd = _get(_RE_PWD)
        if pwd:
            pwd_line = 'Key:  %s' % pwd

    # Require at minimum a chip type to consider detect successful
    if not chip:
        return None

    return {
        'chip':       chip       or '?',
        'modulation': modulation or '?',
        'bitrate':    bitrate    or '?',
        'inverted':   inverted   or '?',
        'block0':     block0     or '?',
        'pwd_set':    pwd_set    or '?',
        'pwd_line':   pwd_line,
    }


class T55xxDetectPlugin(object):
    """Entry class for the T55xx Detect plugin."""

    def __init__(self, host=None):
        self.host = host

    def _clear_vars(self):
        """Reset all display variables to safe defaults."""
        for key in ('chip', 'modulation', 'bitrate', 'inverted',
                    'block0', 'pwd_set', 'pwd_line'):
            self.host.set_var(key, '')

    def _apply_result(self, fields):
        """Push parsed fields into host state for {placeholder} resolution."""
        for key, value in fields.items():
            self.host.set_var(key, value)

    # ------------------------------------------------------------------
    # Detect without password
    # ------------------------------------------------------------------

    def do_detect(self):
        """Run lf t55xx detect without password."""
        self._clear_vars()

        try:
            import executor
        except ImportError:
            return {'status': 'error'}

        executor.startPM3Task('lf t55xx detect', _TIMEOUT_MS)

        fields = _parse_detect_output(executor)
        if fields is None:
            return {'status': 'error'}

        self._apply_result(fields)
        return {'status': 'done'}

    # ------------------------------------------------------------------
    # Detect with password
    # ------------------------------------------------------------------

    def do_detect_pwd(self):
        """Capture password from input widget then run lf t55xx detect -p <pwd>."""
        self._clear_vars()

        pwd = self.host.get_input().strip().upper()
        if len(pwd) != 8:
            return {'status': 'error'}

        try:
            import executor
        except ImportError:
            return {'status': 'error'}

        executor.startPM3Task('lf t55xx detect -p %s' % pwd, _TIMEOUT_MS)

        fields = _parse_detect_output(executor)
        if fields is None:
            return {'status': 'error'}

        self._apply_result(fields)
        return {'status': 'done'}
