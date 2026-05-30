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

"""LF Clone & Pwd plugin.

Workflow
--------
1. do_scan()         -- lf sea -> lfsearch.parser() -> identify tag type
                        KERI: extracts decimal internal ID via REGEX_KERI_ID
                        from executor cache before parser() runs
2. do_dump()         -- lfread.READ[typ](save=True) -> /mnt/upan/dump/
3. do_write()        -- lfwrite.write() no password, all types
4. do_write_pwd()    -- capture password from input widget while still alive,
                        store in write_pwd var, transition to writing_pwd
5. do_write_actual() -- type-specific clone sequence + PWD bit set + verify

Password lock supported types
------------------------------
write_raw() path — no re-detect needed after clone:
    AWID(11)      -- lf t55xx write block by block via write_raw() [CONFIRMED]
    IOProx(12)    -- lf t55xx write block by block via write_raw() [CONFIRMED]
    GProxII(13)   -- lf t55xx write block by block via write_raw() [CONFIRMED]
    Viking(15)    -- lf t55xx write block by block via write_raw() [CONFIRMED]
    Pyramid(16)   -- lf t55xx write block by block via write_raw() [CONFIRMED]
    Jablotron(30) -- lf t55xx write block by block via write_raw() [CONFIRMED]
    Noralsy(33)   -- lf t55xx write block by block via write_raw() [CONFIRMED]
    PAC(34)       -- lf t55xx write block by block via write_raw() [CONFIRMED]
    
    NOTE:
    Noralsy might be a litte strange, testing the plugin on 4 different branded T5577's only KSEC wrote fine
    Ali, ID1 and Proxgrind t5577 failed the B0 check, this is because it gets detected with a leading zero reference
    downlink mode instead of the default/fixed bit length (specified in detect with --r0) as a workaround i added detect to use r0 downlink mode.
    the tag itself is set correctly and the underlying data is intact, unsure of the issue but noralsy is confirmed working on 2 other rdv4's.

PAR_CLONE_MAP path — re-detect needed after clone:
    HID(9)        -- lf hid clone -r <raw>            [CONFIRMED]
    Indala(10)    -- lf indala clone -r <raw>
    FDX-B(28)     -- lf fdxb clone --country <c> --national <nc> [CONFIRMED ANIMAL ONLY]
    KERI(31)      -- lf keri clone -t i --cn <decimal_id> [CONFIRMED]

RAW_CLONE_MAP path — re-detect needed after clone:
    Securakey(14) -- lf securakey clone -r <raw>      [CONFIRMED]
    Gallagher(29) -- lf gallagher clone -r <raw>      [CONFIRMED]
    Paradox(35)   -- lf paradox clone -r <raw>        [CONFIRMED]
    NexWatch(45)  -- lf nexwatch clone -r <raw>       [CONFIRMED QUADRA KEY ONLY]

Clone only — no password support:
    EM410x(8)     -- raw not available from lfsearch
    NEDAP(32)     -- extended mode B0 (903F0082, bit 31 set)
    Presco(36)    -- raw not available from lfsearch
    Visa2000(37)  -- raw not available from lfsearch

Password lock sequence (all supported types):
    1. lf t55xx detect                       -- sync PM3 config
    2. <type-specific clone command>         -- write clone data
    3. lf t55xx detect                       -- re-sync [PAR/RAW_CLONE only]
    4. lf t55xx write -b 7 -d <pwd>         -- set password (tag still unlocked)
    5. lf t55xx read -b 7                   -- verify password written
    6. lf t55xx write -b 0 -d <locked_b0>  -- set PWD bit (tag now pwd protected)
    7. lf t55xx detect -p <pwd>  x2        -- first may misread
    8. verify Password set == Yes            -- hard fail if not confirmed
    9. verify Block0 == <expected_b0>        -- hard fail if mismatch
"""

import re as _re

# T55x7_PWD bit (bit 28 from MSB) — from cmdlft55xx.h
T55X7_PWD = 0x00000010

# Per-type B0 config words from cmdlft55xx.h
B0_MAP = {
    9:  '00107060',  # HID Prox
    10: '00081040',  # Indala 64
    11: '00107060',  # AWID
    12: '00147040',  # IO Prox
    13: '00150060',  # GProx II
    15: '00088040',  # Viking
    16: '00107080',  # Pyramid
    28: '00098080',  # FDX-B (T55X7_FDXB_2_CONFIG_BLOCK)
    30: '00158040',  # Jablotron
    31: '603E1040',  # KERI
    33: '00088068',  # Noralsy (RDV4 confirmed config word)
    34: '00080080',  # PAC
    14: '000C8060',  # Securakey
    29: '00088060',  # Gallagher
    35: '00107060',  # Paradox
    45: '00081060',  # NexWatch
}

# Types using write_raw() — no re-detect needed after clone
WRITE_RAW_TYPES = {11, 12, 13, 15, 16, 30, 33, 34}

# Types using dedicated clone command — re-detect needed after clone
PAR_CLONE_TYPES = {9, 10, 28, 31}

# RAW_CLONE_MAP types — lf <type> clone -r <raw>, re-detect needed after clone
RAW_CLONE_TYPES = {14, 29, 35, 45}

# All types that support password writing
LOCK_SUPPORTED = WRITE_RAW_TYPES | PAR_CLONE_TYPES | RAW_CLONE_TYPES

# Types not supported — shown as clone only
CLONE_ONLY = {8, 32, 36, 37}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_tag_name(typ):
    try:
        import tagtypes
        return tagtypes.getName(typ)
    except Exception:
        return 'Unknown'


def _is_writable(typ):
    try:
        import tagtypes
        return tagtypes.isTagCanWrite(typ)
    except Exception:
        return False


def _locked_b0(typ):
    """Return B0 config word with PWD bit set for the given type."""
    b0 = B0_MAP.get(typ, '000880E0')
    return '%08X' % (int(b0, 16) | T55X7_PWD)


def _run_clone(typ, raw, infos, executor):
    """Run the correct clone command for the given type.

    Returns (ret, needs_redetect) where ret is the PM3 return code
    and needs_redetect is True for PAR_CLONE_MAP types.
    """
    if typ == 9:   # HID
        ret = executor.startPM3Task('lf hid clone -r %s' % raw, 15000)
        return ret, True

    elif typ == 10:  # Indala
        ret = executor.startPM3Task('lf indala clone -r %s' % raw, 15000)
        return ret, True

    elif typ == 28:  # FDX-B
        country = infos.get('country', '')
        nc      = infos.get('nc', '')
        if not country or not nc:
            return -1, True
        ret = executor.startPM3Task(
            'lf fdxb clone --country %s --national %s' % (country, nc), 15000)
        return ret, True

    elif typ == 31:  # KERI — needs decimal internal ID
        keri_id = infos.get('keri_id', '')
        if not keri_id:
            return -1, True
        ret = executor.startPM3Task(
            'lf keri clone -t i --cn %s' % keri_id, 15000)
        return ret, True

    elif typ in RAW_CLONE_TYPES:
        # RAW_CLONE_MAP types — lf <type> clone -r <raw>
        raw_clone_cmds = {
            14: 'lf securakey clone -r %s',
            29: 'lf gallagher clone -r %s',
            35: 'lf paradox clone -r %s',
            45: 'lf nexwatch clone -r %s',
        }
        cmd = raw_clone_cmds.get(typ, '')
        if not cmd:
            return -1, True
        ret = executor.startPM3Task(cmd % raw, 15000)
        return ret, True

    else:
        # write_raw() path
        try:
            import lfwrite
        except ImportError:
            return -1, False
        ret = lfwrite.write_raw(typ, raw, key=None)
        return ret, False


# ---------------------------------------------------------------------------
# Plugin class
# ---------------------------------------------------------------------------

class LFClonePwdPlugin(object):
    """Entry class for the LF Clone & Pwd plugin."""

    def __init__(self, host=None):
        self.host = host

    # ------------------------------------------------------------------
    # Step 1: Scan
    # ------------------------------------------------------------------

    def do_scan(self):
        """Run lf sea, parse with lfsearch.parser(), store results."""
        self.host.set_var('error_msg', '')
        self.host.set_var('tag_name',  '')
        self.host.set_var('tag_id',    '')
        self.host.set_var('tag_nc',    '')
        self.host.set_var('tag_typ',   '')
        self.host.set_var('tag_raw',   '')
        self.host.set_var('tag_infos', '')

        try:
            import executor
            import lfsearch
        except ImportError:
            self.host.set_var('error_msg', 'Import error')
            return {'status': 'error'}

        executor.startPM3Task(lfsearch.CMD, lfsearch.TIMEOUT)

        # Extract KERI decimal internal ID BEFORE parser() is called
        # lfsearch Check 14 only captures FC/CN, not the decimal Internal ID
        # REGEX_KERI_ID = r'Internal ID:\s+(\d+)'
        keri_id = ''
        cache = executor.getPrintContent() or ''
        m = _re.search(r'Internal ID:\s+(\d+)', cache)
        if m:
            keri_id = m.group(1)

        result = lfsearch.parser()

        if not result.get('found'):
            self.host.set_var('error_msg', 'No LF tag found')
            return {'status': 'error'}

        if result.get('isT55XX'):
            self.host.set_var('error_msg', 'T55xx detected\nScan source tag')
            return {'status': 'error'}

        typ = result.get('type')
        if typ is None:
            self.host.set_var('error_msg', 'Could not identify tag')
            return {'status': 'error'}

        if not _is_writable(typ):
            self.host.set_var('error_msg', '%s\nnot writable' % _get_tag_name(typ))
            return {'status': 'error'}

        tag_name = _get_tag_name(typ)
        tag_raw  = result.get('raw') or ''

        # FDX-B: format as CC: XXX / NC: YYYYYY for display
        # All other types: show data/raw as single ID line
        if typ == 28:
            country = result.get('country', '')
            nc      = result.get('nc', '')
            tag_id  = 'CC: %s' % country + '\n' + 'NC: %s' % nc
            tag_nc  = ''
        else:
            tag_id = result.get('data') or result.get('raw') or ''
            tag_nc = ''

        # Store KERI decimal ID in infos so do_write_actual can use it
        if typ == 31 and keri_id:
            result['keri_id'] = keri_id

        lock_ok = typ in LOCK_SUPPORTED

        self.host.set_var('tag_name',  tag_name)
        self.host.set_var('tag_id',    tag_id[:20])
        self.host.set_var('tag_nc',    tag_nc[:20])
        self.host.set_var('tag_typ',   str(typ))
        self.host.set_var('tag_raw',   tag_raw)
        self.host.set_var('tag_infos', repr(result))

        return {'status': 'found' if lock_ok else 'found_no_lock'}

    # ------------------------------------------------------------------
    # Step 2: Dump
    # ------------------------------------------------------------------

    def do_dump(self):
        """Save dump to /mnt/upan/dump/ via lfread.READ[typ](save=True)."""
        self.host.set_var('error_msg', '')

        try:
            import lfread
        except ImportError:
            self.host.set_var('error_msg', 'Import error')
            return {'status': 'error'}

        typ_str = self.host.get_var('tag_typ', '')
        try:
            typ = int(typ_str)
        except ValueError:
            self.host.set_var('error_msg', 'Bad tag type')
            return {'status': 'error'}

        read_fn = lfread.READ.get(typ)
        if read_fn is None:
            return {'status': 'done'}

        ret = read_fn(save=True)
        if isinstance(ret, dict) and ret.get('return') == -1:
            self.host.set_var('error_msg', 'Dump failed')
            return {'status': 'error'}

        return {'status': 'done'}

    # ------------------------------------------------------------------
    # Step 3a: Write without password
    # ------------------------------------------------------------------

    def do_write(self):
        """Write clone using lfwrite.write() — handles all types correctly."""
        self.host.set_var('error_msg', '')
        self.host.set_var('done_msg', 'Written without password')

        try:
            import lfwrite
        except ImportError:
            self.host.set_var('error_msg', 'Import error')
            return {'status': 'error'}

        typ_str   = self.host.get_var('tag_typ', '')
        infos_str = self.host.get_var('tag_infos', '')
        raw       = self.host.get_var('tag_raw', '')

        try:
            typ   = int(typ_str)
            infos = eval(infos_str)
        except Exception:
            self.host.set_var('error_msg', 'Bad tag data')
            return {'status': 'error'}

        ret = lfwrite.write(None, typ, infos, raw, key=None)

        if ret == -9:
            self.host.set_var('error_msg', 'Tag locked\nRun Chk T55xx Pwds')
            return {'status': 'error'}

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
    # Step 3c: Write with password
    # ------------------------------------------------------------------

    def do_write_actual(self):
        """Clone and set password using type-specific sequence."""
        self.host.set_var('error_msg', '')
        self.host.set_var('done_msg', '')

        pwd = self.host.get_var('write_pwd', '')
        if len(pwd) != 8:
            self.host.set_var('error_msg', 'No password stored')
            return {'status': 'error'}

        raw       = self.host.get_var('tag_raw', '')
        typ_str   = self.host.get_var('tag_typ', '')
        infos_str = self.host.get_var('tag_infos', '')

        try:
            typ   = int(typ_str)
            infos = eval(infos_str)
        except Exception:
            self.host.set_var('error_msg', 'Bad tag data')
            return {'status': 'error'}

        try:
            import executor
            import lfwrite
        except ImportError:
            self.host.set_var('error_msg', 'Import error')
            return {'status': 'error'}

        if typ not in LOCK_SUPPORTED:
            self.host.set_var('error_msg', 'Pwd not supported')
            return {'status': 'error'}

        expected_b0 = _locked_b0(typ)

        # Step 1: detect — sync PM3 config knowledge
        ret = executor.startPM3Task('lf t55xx detect', 10000)
        if ret == -1:
            self.host.set_var('error_msg', 'T55xx not detected')
            return {'status': 'error'}

        # Step 2: clone using type-specific command
        ret, needs_redetect = _run_clone(typ, raw, infos, executor)
        if ret == -1:
            self.host.set_var('error_msg', 'Clone failed')
            return {'status': 'error'}

        # Step 3: re-detect for PAR_CLONE_MAP types — they write their
        # own B0 internally so PM3 needs to re-sync before block writes
        if needs_redetect:
            executor.startPM3Task('lf t55xx detect', 10000)

        # Step 4: write password to block 7 — tag still unlocked
        ret = executor.startPM3Task(
            'lf t55xx write -b 7 -d %s' % pwd, 15000)
        if ret == -1:
            self.host.set_var('error_msg', 'Set pwd failed')
            return {'status': 'error'}

        # Step 5: read block 7 to verify password written correctly
        executor.startPM3Task('lf t55xx read -b 7', 10000)

        # Step 6: write block 0 with PWD bit — tag now pwd protected
        ret = executor.startPM3Task(
            'lf t55xx write -b 0 -d %s' % expected_b0, 15000)
        if ret == -1:
            self.host.set_var('error_msg', 'PWD bit failed')
            return {'status': 'error'}

        # Steps 7+8: detect twice with password — first may misread on
        # extended mode tags, second settles correctly.
        # Noralsy(33): --r0 forces fixed bit length downlink as some hardware
        # detects post-lock Noralsy in leading zero mode without it.
        detect_cmd = ('lf t55xx detect --r0 -p %s' % pwd
                      if typ == 33 else
                      'lf t55xx detect -p %s' % pwd)
        executor.startPM3Task(detect_cmd, 10000)
        executor.startPM3Task(detect_cmd, 10000)

        # Step 9: verify B0 matches expected locked value
        # Block0 == expected_b0 confirms PWD bit is set
        content = executor.getPrintContent() or ''
        b0_match = _re.search(r'Block0[. ]+([A-Fa-f0-9]{8})', content)

        if not b0_match:
            self.host.set_var('error_msg', 'B0 not found')
            return {'status': 'error'}

        if b0_match.group(1).upper() != expected_b0.upper():
            self.host.set_var('error_msg', 'B0 mismatch\n%s' % b0_match.group(1))
            return {'status': 'error'}
        self.host.set_var('done_msg', 'Written & pwd set\n%s' % pwd)
        return {'status': 'done'}
