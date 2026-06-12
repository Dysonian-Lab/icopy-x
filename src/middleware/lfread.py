##########################################################################
# Required Notice: Copyright ETOILE401 SAS (http://www.lab401.com)
#
# Initial author: ETOILE401 SAS & https://github.com/quantum-x/ as of April 16, 2026
#
# Since this date, each contribution is under the copyright of its respective author.
#
# Copyright of each contribution is tracked by the Git history. See the output of git shortlog -nse for a full list or git log --pretty=short --follow <path/to/sourcefile> |git shortlog -ne to track a specific file.
#
# A mailmap is maintained to map author and committer names and email addresses to canonical names and email addresses.
# If by accident a copyright was removed from a file and is not directly deducible from the Git history, please submit a PR.
#
#
# This software is licensed under the PolyForm Noncommercial License 1.0.0.
# You may not use this software for commercial purposes.
#
# A copy of the license is available at:
# https://polyformproject.org/licenses/noncommercial/1.0.0
#
# This entire header "Required Notice" must remain in place.
##########################################################################

"""lfread -- LF tag reading for 20+ card types.

Reimplemented from lfread.so (iCopy-X v1.0.90).
Ground truth: archive/lib_transliterated/lfread.py

Iceman-native command forms (P3.5 refactor, 2026-04-17):
  - Every per-tag dispatcher uses iceman `lf <tag> reader` spelling
    (matrix L1213-1237 consolidated 19-row section).  Iceman source:
    /tmp/rrg-pm3/client/src/cmdlf<tag>.c dispatch tables — each entry
    `{"reader", Cmd<Tag>Reader, IfPm3Lf, ...}`.  Matrix verifies:
      - lf em 410x reader   cmdlfem410x.c:891    (matrix L1075)
      - lf hid reader       cmdlfhid.c:723       (matrix L1160)
      - lf indala reader    cmdlfindala.c:1102   (matrix L1225)
      - lf awid reader      cmdlfawid.c:605      (matrix L998)
      - lf io reader        cmdlfio.c:373        (matrix L1226)
      - lf gproxii reader   cmdlfguard.c:417     (matrix L1227)
      - lf securakey reader cmdlfsecurakey.c:300 (matrix L1228)
      - lf viking reader    cmdlfviking.c:248    (matrix L1229)
      - lf pyramid reader   cmdlfpyramid.c:451   (matrix L1230)
      - lf fdxb reader      cmdlffdxb.c:908      (matrix L1110)
      - lf gallagher reader cmdlfgallagher.c:386 (matrix L1144)
      - lf jablotron reader cmdlfjablotron.c:317 (matrix L1223)
      - lf keri reader      cmdlfkeri.c:375      (matrix L1231)
      - lf nedap reader     cmdlfnedap.c:569     (matrix L1232)
      - lf noralsy reader   cmdlfnoralsy.c:291   (matrix L1224)
      - lf pac reader       cmdlfpac.c:401       (matrix L1233)
      - lf paradox reader   cmdlfparadox.c:477   (matrix L1234)
      - lf presco reader    cmdlfpresco.c:363    (matrix L1235)
      - lf visa2000 reader  cmdlfvisa2000.c:306  (matrix L1236)
      - lf nexwatch reader  cmdlfnexwatch.c:585  (matrix L1237)
  - Parsers consume `lfsearch.REGEX_*` (refactored to iceman-native in
    P3.1; see lfsearch.py header) via the shared `read()` / `readCardIdAndRaw`
    / `readFCCNAndRaw` helpers.
  - Per-tag FC/CN shape caveats (iceman-native Raw: always present,
    FC/CN sometimes omitted — matrix L1213): Gallagher emits
    `Facility: %u Card No.: %u` not `FC: %u Card: %u` (cmdlfgallagher.c:88),
    KERI emits `Internal ID: %u, Raw:` not `Card:` (cmdlfkeri.c:176),
    NEDAP emits `ID: %05u subtype: %1u customer code:` (cmdlfnedap.c:146),
    Presco emits `Site code:/User code:` (cmdlfpresco.c:114), NexWatch
    emits only `" Raw : <hex>"` with a space before the colon
    (cmdlfnexwatch.c:247).  `lfsearch.REGEX_RAW` now uses `\\s*:` to
    tolerate both the tight `Raw:` and the NexWatch space-before-colon
    form, so raw capture works for every per-tag demod.  Callers accept
    empty FC/CN; fallback to `Raw:` via `lfsearch.REGEX_RAW` keeps
    success status truthy when a raw field is present.  See gap log
    P3.5.
"""

import os
import re

try:
    import executor
except ImportError:
    try:
        from . import executor
    except ImportError:
        executor = None

try:
    import lfsearch
except ImportError:
    try:
        from . import lfsearch
    except ImportError:
        lfsearch = None

try:
    import lft55xx
except ImportError:
    try:
        from . import lft55xx
    except ImportError:
        lft55xx = None

try:
    import lfem4x05
except ImportError:
    try:
        from . import lfem4x05
    except ImportError:
        lfem4x05 = None

TIMEOUT = 10000

# ---------------------------------------------------------------------------
# Dump directory mapping: type ID -> (appfiles dir name, display prefix)
# ---------------------------------------------------------------------------
_DUMP_DIRS = {
    8:  ('em410x',    'EM410x'),
    9:  ('hid',       'HID-Prox'),
    10: ('indala',    'Indala'),
    11: ('awid',      'AWID'),
    12: ('ioprox',    'IOProx'),
    13: ('gproxii',   'GProxII'),
    14: ('securakey', 'Securakey'),
    15: ('viking',    'Viking'),
    16: ('pyramid',   'Pyramid'),
    28: ('fdx',       'FDX'),
    29: ('gallagher', 'Gallagher'),
    30: ('jablotron', 'Jablotron'),
    31: ('keri',      'KERI'),
    32: ('nedap',     'NEDAP'),
    33: ('noralsy',   'Noralsy'),
    34: ('pac',       'PAC'),
    35: ('paradox',   'Paradox'),
    36: ('presco',    'Presco'),
    37: ('visa2000',  'Visa2000'),
    45: ('nexwatch',  'NexWatch'),
}


def createRetObj(uid, raw, ret):
    return {'return': ret, 'data': uid, 'raw': raw}


def _stem_from_identity(ident):
    """Build a clean, filesystem-safe filename stem from an identity string.

    Converts the display identity into a tidy stem with no protocol labels:
      'FC,CN: 128,54641' -> '128-54641'   (FC/CN types — label stripped)
      'XSF(01)6e:01337'  -> '01-6e-01337' (IOProx — vn-fc-cn)
      plain hex/decimal  -> unchanged
      'CN-Year' / 'C-NC' -> unchanged (already dash-joined)

    The filename is purely an identifier under format v2 — display and sim
    data come from the file content, not the filename. So a user may rename
    the file freely and display/sim/write still work.
    """
    if not ident:
        return ''
    if ident.startswith('FC,CN:'):
        # 'FC,CN: 128,54641' -> '128-54641'
        vals = ident.replace(' ', '').split(':', 1)[1]
        return vals.replace(',', '-')
    if ident.startswith('XSF('):
        # 'XSF(01)6e:01337' -> '01-6e-01337'
        m = re.match(r'XSF\(\s*([0-9A-Fa-f]+)\s*\)\s*([0-9A-Fa-f]+)\s*:\s*([0-9]+)',
                     ident)
        if m:
            return '%s-%s-%s' % (m.group(1), m.group(2), m.group(3))
    # plain — strip spaces and neutralise any stray separators
    return ident.replace(' ', '').replace(':', '_').replace(',', '-')


def _save_txt(typ, uid, raw, display=None, extras=None):
    """Save an LF read result as a multi-line .txt dump (format v2).

        line 1  : raw hex  — WRITE payload (the only line the write path reads).

        line 2  : DISPLAY and SIM — feeds cache['data'] for the tag-info /
                  dump-list view, and is also the SIM source for single-field
                  tags (EM410x, Viking, Indala, Jablotron, KERI, PAC, Presco,
                  Visa2000, NexWatch — their one sim field maps to data).
                  Multi-field tags use line 2 for display only and take their
                  sim values from line 3+.

        line 3+ : key=value SIM fields (multi-field tags only) —
                  fc,cn,len (AWID/GProxII/Pyramid/Securakey/Gallagher/Paradox)
                  vn,fc,cn (IOProx) · country,nc (FDX-B) ·
                  subtype,code (NEDAP) · cn,year (Noralsy).

    Note: IOProx line 2 keeps FC in hex (XSF display matches scan), but the
    line-3 fc= is decimal because 'lf io sim --fc' expects decimal.

    Filename <Type>-ID_<identity>_N.txt is only an identifier — all display,
    sim and write data come from the content, so dumps can be renamed freely.
    Old single-line dumps still work (display/sim fall back to the filename,
    write uses the raw line).

    Args:
        typ:     numeric tag type (keys _DUMP_DIRS).
        uid:     identity string (card id, 'FC,CN: x,y', XSF, ...).
        raw:     raw hex write payload (line 1).
        display: display string for line 2 (defaults to uid, then raw); also
                 the sim source for single-field tags.
        extras:  optional dict of per-field sim values for line 3+.
    """
    try:
        import appfiles
        dir_name, prefix = _DUMP_DIRS.get(typ, ('lf', 'LF'))
        dump_dir = os.path.join(appfiles.PATH_DUMP, dir_name, '')
        os.makedirs(dump_dir, exist_ok=True)

        line1 = raw or uid or ''
        line2 = display or uid or raw or ''
        if not (line1 or line2):
            return

        lines = [line1, line2]
        if extras:
            for k, v in extras.items():
                if v is not None and v != '':
                    lines.append('%s=%s' % (k, v))

        ident = display or uid or raw or ''
        safe = _stem_from_identity(ident)
        stem = '%s-ID_%s' % (prefix, safe)

        n = 1
        while os.path.exists(os.path.join(dump_dir, '%s_%d.txt' % (stem, n))):
            n += 1
        with open(os.path.join(dump_dir, '%s_%d.txt' % (stem, n)), 'w') as f:
            f.write('\n'.join(lines))
    except Exception:
        pass


def read(cmd, uid_regex, raw_regex, uid_index=0, raw_index=0, typ=None, save=True):
    """Generic LF per-tag reader driver.

    Sends `cmd` (an iceman-native `lf <tag> reader` string; see module
    docstring citations), parses cached PM3 response with the shared
    iceman-native regex patterns in lfsearch.

    Regex patterns imported via `lfsearch.REGEX_*` are iceman-native as of
    P3.1 refactor (see lfsearch.py module header):
      REGEX_RAW     r'(?:Raw|raw)\\s*:\\s*([xX0-9a-fA-F ]+)' matches iceman
                    `, Raw: <hex>` (cmdlf*.c demod emission), NexWatch's
                    `" Raw : <hex>"` space-before-colon form
                    (cmdlfnexwatch.c:247), and iceman HID lowercase
                    `raw: <hex>` (cmdlfhid.c:235).
      REGEX_CARD_ID r'(?:Card|ID|UID)[\\s:]+([xX0-9a-fA-F ]+)' matches
                    iceman `Card: %u` (Jablotron/Noralsy/Paradox/PAC),
                    `Card %X` (Viking, space-no-colon), `ID: %u` (Paradox
                    Internal ID), `UID... %s` (Indala).
      REGEX_EM410X  r'EM 410x(?:\\s+XL)?\\s+ID\\s+([0-9A-Fa-f]+)' matches
                    iceman `EM 410x ID %010llX` (cmdlfem410x.c:115) and
                    XL variant at :118.
      REGEX_HID     r'raw:\\s+([0-9A-Fa-f]+)' matches iceman
                    `raw: %08x%08x%08x` (cmdlfhid.c:235).
      REGEX_ANIMAL  r'Animal ID\\.+\\s+([0-9\\-]+)' matches iceman
                    `Animal ID........... %03u-%012llu` (cmdlffdxb.c:572/578).

    Args:
        save: If True (default), save a .txt dump on successful read.
              Pass False for inline verify reads (post-write) to avoid
              creating spurious dump files.
    """
    ret = executor.startPM3Task(cmd, TIMEOUT)
    if ret == -1:
        return createRetObj(None, None, -1)
    content = executor.getPrintContent()
    if not content or executor.isEmptyContent():
        return createRetObj(None, None, -1)
    uid_group = uid_index if uid_index else 0
    raw_group = raw_index if raw_index else 0
    uid = executor.getContentFromRegexG(uid_regex, uid_group)
    raw = executor.getContentFromRegexG(raw_regex, raw_group)
    if uid:
        uid = lfsearch.cleanHexStr(uid.strip())
    if raw:
        raw = lfsearch.cleanHexStr(raw.strip())
    if uid or raw:
        if save and typ is not None:
            _save_txt(typ, uid, raw, display=uid)
        return createRetObj(uid, raw, 1)
    return createRetObj(None, None, -1)


def readCardIdAndRaw(cmd, uid_index=0, raw_index=0, typ=None, save=True):
    """Iceman-native per-tag: parse `Card|ID|UID` + `Raw:` from cache.

    Used by: Viking, ProxIO, Jablotron, Nedap, Noralsy, PAC, Presco,
    Visa2000, NexWatch.  Shape spec: lfsearch.REGEX_CARD_ID /
    REGEX_RAW (iceman-native, see lfsearch.py module header).

    Args:
        save: If True (default), save a .txt dump on successful read.
              Pass False for inline verify reads to avoid spurious dumps.
    """
    return read(cmd, lfsearch.REGEX_CARD_ID, lfsearch.REGEX_RAW,
                uid_index=uid_index, raw_index=raw_index, typ=typ, save=save)


def readFCCNAndRaw(cmd, uid_index=0, raw_index=0, typ=None, save=True, write_extras=True):
    """Iceman-native per-tag: parse `FC: %d Card: %u` + `Raw:` from cache.

    Used by: AWID (cmdlfawid.c:248), GProx-II (cmdlfguard.c:186),
    Securakey (cmdlfsecurakey.c:113), Pyramid (cmdlfpyramid.c:161),
    Keri (cmdlfkeri.c:176 — `Internal ID:` only, no FC/CN),
    Gallagher (cmdlfgallagher.c:88 — `Facility:`/`Card No.:` not
    `FC:`/`Card:`), Paradox (cmdlfparadox.c:224).

    Iceman-native FC/CN regex lives in lfsearch.py:
      _RE_FC = r'FC:\\s+([xX0-9a-fA-F]+)'
      _RE_CN = r'(CN|Card(?:\\s+No\\.)?)[\\s:]+([0-9A-Fa-f]+)' (hex-tolerant)

    Per matrix L1213 + iceman source audit: Keri/Gallagher/Nedap/Presco/
    NexWatch emit alternative field labels; lfsearch._RE_FC won't match
    `Facility:` (Gallagher) and `_RE_CN` won't match `Internal ID:`
    (Keri) or `ID:` alone (Nedap, plus subtype/customer).

    Success gate: EITHER `parseFC()`/`parseCN()` extracted something
    (FC/CN regex hit), OR `REGEX_RAW` extracted a hex string.  We
    CANNOT rely on `getFCCN()`'s string truthiness because it returns
    the literal sentinel `'FC,CN: X,X'` when both FC and CN are empty
    (lfsearch.py:267) — a non-empty placeholder that would always
    evaluate truthy and produce spurious success on any non-empty
    response.  Callers still receive the formatted `'FC,CN: ...'`
    string in `data` (callers expect that shape), but only after we've
    verified real FC/CN or Raw data was actually captured.
    """
    ret = executor.startPM3Task(cmd, TIMEOUT)
    if ret == -1:
        return createRetObj(None, None, -1)
    content = executor.getPrintContent()
    if not content or executor.isEmptyContent():
        return createRetObj(None, None, -1)
    # Check FC/CN extraction directly — do NOT rely on getFCCN() truthiness
    # (it returns the 'FC,CN: X,X' sentinel even when both fields missed).
    fc = lfsearch.parseFC()
    cn = lfsearch.parseCN()
    raw = executor.getContentFromRegexG(lfsearch.REGEX_RAW, 1)
    if raw:
        raw = lfsearch.cleanHexStr(raw.strip())
    if fc or cn or raw:
        # At least one of FC/CN/Raw actually parsed — success.
        # Data carries the formatted FC/CN (sentinel X,X if both missed
        # but Raw present), preserving caller-expected 'FC,CN: xxx,yyy'
        # shape.
        uid = lfsearch.getFCCN()
        if save and typ is not None:
            # Capture fc/cn/len as per-field sim values (format v2 line 3+)
            # so simulate prepopulation works from a dump. Derive fc/cn from
            # the SAME formatted string as the display (getFCCN -> %03d/%05d)
            # so the dump's display line and its sim fields are always
            # consistent. len drives the AWID/GProxII 'Format' sim field;
            # absent for types with no format field (simply not written).
            #
            # write_extras=False (Gallagher only): no SIM_MAP entry consumes
            # fc/cn/len for Gallagher (its raw payload on line 1 is
            # self-sufficient), so line 3+ is omitted entirely. Display
            # (line 2, 'FC,CN: x,y' from `uid`) is unaffected either way.
            extras = {}
            if write_extras:
                if uid and uid.startswith('FC,CN:'):
                    vals = uid.replace(' ', '').split(':', 1)[1]   # 'fc,cn'
                    pcs = vals.split(',', 1)
                    if len(pcs) == 2 and pcs[0] != 'X':
                        extras['fc'] = pcs[0]
                    if len(pcs) == 2 and pcs[1] != 'X':
                        extras['cn'] = pcs[1]
                length = lfsearch.parseLen()
                if length:
                    extras['len'] = length
            _save_txt(typ, uid, raw, display=uid, extras=extras)
        return createRetObj(uid, raw, 1)
    return createRetObj(None, None, -1)


def readEM410X(listener=None, infos=None, save=True):
    return read('lf em 410x reader', lfsearch.REGEX_EM410X, lfsearch.REGEX_RAW,
                uid_index=1, raw_index=0, typ=8, save=save)


def readHID(listener=None, infos=None, save=True):
    """Read an HID Prox tag and save the dump (format v2).

    cmdlfhid.c:235 emits "raw: <hex>"; when a Wiegand format is decoded it
    also emits an "FC: %d CN: %d" line. The live scan path (lfsearch
    Check 3) DISPLAYS 'FC,CN: x,y' when FC/CN decode, else the raw hex.
    This reader mirrors that so the dump tag-info view matches scan view.

    HID write path uses RAW (it is in write.py _RAW_CLONE_PAR_TYPES), so the
    FC/CN display string is safe: line 1 is always the raw hex Wiegand
    payload (the write payload), line 2 is the FC/CN display (or raw hex if
    no Wiegand decode). fc/cn are stored as sim extras when present.
    """
    ret = executor.startPM3Task('lf hid reader', TIMEOUT)
    if ret == -1:
        return createRetObj(None, None, -1)
    content = executor.getPrintContent()
    if not content or executor.isEmptyContent():
        return createRetObj(None, None, -1)
    raw = executor.getContentFromRegexG(lfsearch.REGEX_HID, 1)
    if raw:
        raw = lfsearch.cleanHexStr(raw.strip())
    if not raw:
        return createRetObj(None, None, -1)

    fc = lfsearch.parseFC()
    cn = lfsearch.parseCN()
    if fc or cn:
        display = lfsearch.getFCCN()
    else:
        display = raw

    if save:
        extras = {}
        if display.startswith('FC,CN:'):
            vals = display.replace(' ', '').split(':', 1)[1]
            pcs = vals.split(',', 1)
            if len(pcs) == 2 and pcs[0] != 'X':
                extras['fc'] = pcs[0]
            if len(pcs) == 2 and pcs[1] != 'X':
                extras['cn'] = pcs[1]
        # line 1 = raw (write payload); line 2 = display (FC/CN or raw hex).
        _save_txt(9, raw, raw, display=display, extras=extras)
    # Return the display string as the identity (data) so it matches what
    # the dump stores on line 2. This keeps dump-write verification correct:
    # lfverify re-reads HID and compares the re-read data against the dump's
    # data — both must be the same FC/CN display string. raw stays the raw
    # hex so the write path (RAW clone) and raw-compare verify are unaffected.
    return createRetObj(display, raw, 1)


def readIndala(listener=None, infos=None, save=True):
    return read('lf indala reader', lfsearch.REGEX_RAW, lfsearch.REGEX_RAW,
                uid_index=1, raw_index=1, typ=10, save=save)


def readAWID(listener=None, infos=None, save=True):
    return readFCCNAndRaw('lf awid reader', typ=11, save=save)


def readProxIO(listener=None, infos=None, save=True):
    """Read an IO Prox tag and save the dump (format v2).

    cmdlfio.c:156 emits:
        "IO Prox - XSF(%02d)%02x:%05d, Raw: %08x%08x ..."
                       vn     fc   cn
    Note the facility code is printed in HEX (%02x) while version and card
    number are decimal — this matches scan/read view exactly. The XSF
    string is kept verbatim as the display identity (line 2) so dump
    tag-info renders identically to the scan view.

    vn/fc/cn are decomposed into per-field sim values (line 3+) mirroring
    lfsearch Check 5 EXACTLY (fc stored as-captured, i.e. hex). The
    pre-existing IOProx sim FC hex/decimal representation issue is shared
    with the live-scan path and is tracked separately; this function does
    not alter that behaviour.
    """
    ret = executor.startPM3Task('lf io reader', TIMEOUT)
    if ret == -1:
        return createRetObj(None, None, -1)
    content = executor.getPrintContent()
    if not content or executor.isEmptyContent():
        return createRetObj(None, None, -1)
    xsf = executor.getContentFromRegexG(lfsearch.REGEX_PROX_ID_XSF, 1)
    raw = executor.getContentFromRegexG(lfsearch.REGEX_RAW, 1)
    if xsf:
        xsf = xsf.strip()
    if raw:
        raw = lfsearch.cleanHexStr(raw.strip())
    if xsf or raw:
        extras = {}
        if xsf:
            m = re.match(
                r'XSF\(\s*([0-9A-Fa-f]+)\s*\)\s*([0-9A-Fa-f]+)\s*:\s*([0-9]+)',
                xsf)
            if m:
                extras['vn'] = m.group(1)
                # IOProx displays FC in HEX in the XSF string (cmdlfio.c:156
                # "%02x") but `lf io sim --fc` expects DECIMAL (cmdlfio.c:222
                # "<dec>"). Store the decimal conversion as the sim field so
                # simulate from a dump builds a correct command, while line 2
                # (display) keeps the hex XSF string to match scan/read view.
                try:
                    extras['fc'] = str(int(m.group(2), 16))
                except ValueError:
                    extras['fc'] = m.group(2)
                extras['cn'] = m.group(3)
        if save:
            # display = XSF string (matches scan view); raw on line 1.
            _save_txt(12, xsf or raw, raw, display=xsf or raw, extras=extras)
        return createRetObj(xsf or raw, raw, 1)
    return createRetObj(None, None, -1)


def readGProx2(listener=None, infos=None, save=True):
    return readFCCNAndRaw('lf gproxii reader', typ=13, save=save)


def readSecurakey(listener=None, infos=None, save=True):
    return readFCCNAndRaw('lf securakey reader', typ=14, save=save)


def readViking(listener=None, infos=None, save=True):
    return readCardIdAndRaw('lf viking reader', typ=15, save=save)


def readPyramid(listener=None, infos=None, save=True):
    return readFCCNAndRaw('lf pyramid reader', typ=16, save=save)


def readT55XX(listener=None, infos=None, save=True):
    """Read T55XX — detect + chk + dump, return dict for read.so success path."""
    if lft55xx is None:
        return createRetObj(None, None, -1)
    result = lft55xx.chkAndDumpT55xx(listener)
    if isinstance(result, dict):
        return result
    return createRetObj(None, None, -1)


def readEM4X05(listener=None, infos=None, save=True):
    """Read EM4X05 — info + dump, return dict for read.so success path."""
    if lfem4x05 is None:
        return createRetObj(None, None, -1)
    return lfem4x05.infoAndDumpEM4x05ByKey()


def readFDX(listener=None, infos=None, save=True):
    """Read an FDX-B (animal) tag and save the dump (format v2).

    cmdlffdxb.c:572/578 emits:
        "Animal ID........... %03u-%012llu"   (country-national code)
    The animal ID 'country-nc' is BOTH the display identity and the write
    payload for FDX-B (the clone command takes --country/--national, not a
    raw block payload), so line 1 and line 2 are the same value.

    country/nc are also captured as per-field sim values (line 3+) so the
    FDX-B sim/clone fields prepopulate from a dump.
    """
    ret = executor.startPM3Task('lf fdxb reader', TIMEOUT)
    if ret == -1:
        return createRetObj(None, None, -1)
    content = executor.getPrintContent()
    if not content or executor.isEmptyContent():
        return createRetObj(None, None, -1)
    uid = executor.getContentFromRegexG(lfsearch.REGEX_ANIMAL, 1)
    if uid:
        uid = uid.strip()
    if uid:
        extras = {}
        parts = uid.split('-', 1)
        if len(parts) == 2:
            extras['country'] = parts[0]
            extras['nc'] = parts[1]
        if save:
            # FDX-B: animal id is both display and write payload.
            _save_txt(28, uid, uid, display=uid, extras=extras)
        return createRetObj(uid, uid, 1)
    return createRetObj(None, None, -1)


def readGALLAGHER(listener=None, infos=None, save=True):
    """Read a Gallagher tag and save the dump.

    Gallagher's raw payload (line 1, the 12-byte/24-hex demod block) is
    self-sufficient -- fc=/cn= (line 3+) are not consumed by anything for
    Gallagher, so they're omitted here (write_extras=False). Display (line
    2, 'FC,CN: x,y' from getFCCN()) is unaffected.
    """
    return readFCCNAndRaw('lf gallagher reader', typ=29, save=save, write_extras=False)


def readJablotron(listener=None, infos=None, save=True):
    """Read a Jablotron tag and save the dump (format v2).

    cmdlfjablotron.c demodJablotron() emits:
        "Jablotron - Card: %"PRIx64", Raw: %08X%08X"

    Card: is the display id (id = getJablontronCardId(rawid), printed via
    %PRIx64 with no zero-padding, so its hex length varies 1-9 chars).
    Raw: is the full 64-bit demod buffer, always 16 hex chars:
        [0:4]  = 16-bit preamble (FFFF)
        [4:14] = 40-bit fullcode/rawid (10 hex chars)
        [14:16]= 8-bit checksum

    `lf jablotron sim --cn <hex>` (CmdJablotronSim) expects that 40-bit
    fullcode/rawid directly -- it is placed verbatim into the tag bitstream
    by getJablotronBits(). The Card: display id is a different, BCD-style
    transform of that value (getJablontronCardId()) and is NOT the value
    --cn needs; it can also have an odd hex-digit count, which
    CLIGetHexWithReturn rejects ("uneven amount of digits").

    fullcode is extracted here as raw[4:14] -- always exactly 10 hex chars
    (even-length) -- and saved as 'fullcode=' on line 3 for sim
    prepopulation, while line 2 (display=uid) keeps showing the familiar
    Card: id, unchanged.
    """
    ret = executor.startPM3Task('lf jablotron reader', TIMEOUT)
    if ret == -1:
        return createRetObj(None, None, -1)
    content = executor.getPrintContent()
    if not content or executor.isEmptyContent():
        return createRetObj(None, None, -1)
    uid = executor.getContentFromRegexG(lfsearch.REGEX_CARD_ID, 1)
    raw = executor.getContentFromRegexG(lfsearch.REGEX_RAW, 1)
    if uid:
        uid = lfsearch.cleanHexStr(uid.strip())
    if raw:
        raw = lfsearch.cleanHexStr(raw.strip())
    if uid or raw:
        extras = None
        if raw and len(raw) == 16:
            extras = {'fullcode': raw[4:14]}
        if save:
            _save_txt(30, uid, raw, display=uid, extras=extras)
        return createRetObj(uid, raw, 1)
    return createRetObj(None, None, -1)


def readKeri(listener=None, infos=None, save=True):
    """Read a KERI tag and save the dump.

    cmdlfkeri.c:176 emits:
        "KERI - Internal ID: %u, Raw: %08X%08X"
    Internal ID is decimal; Raw is 16 hex chars.

    readFCCNAndRaw cannot be used here because KERI does not emit FC:/CN:
    labels — it emits "Internal ID:" which matches neither _RE_FC nor _RE_CN.
    The result was a sentinel filename KERI-ID_FC,CN=X,X_N.txt.

    This dedicated function captures Internal ID via REGEX_KERI_ID (decimal)
    as uid, and Raw via REGEX_RAW as raw, producing correct filenames like
    KERI-ID_2164260_N.txt with raw hex as file content.
    """
    ret = executor.startPM3Task('lf keri reader', TIMEOUT)
    if ret == -1:
        return createRetObj(None, None, -1)
    content = executor.getPrintContent()
    if not content or executor.isEmptyContent():
        return createRetObj(None, None, -1)
    uid = executor.getContentFromRegexG(lfsearch.REGEX_KERI_ID, 1)
    raw = executor.getContentFromRegexG(lfsearch.REGEX_RAW, 1)
    if raw:
        raw = lfsearch.cleanHexStr(raw.strip())
    if uid or raw:
        if save:
            _save_txt(31, uid, raw, display=uid)
        return createRetObj(uid, raw, 1)
    return createRetObj(None, None, -1)


def readNedap(listener=None, infos=None, save=True):
    """Read a NEDAP tag and save the dump (format v2).

    cmdlfnedap.c:146 emits the card ID plus
    " subtype: %1u customer code: %u / 0x%03X".

    Display identity stays the card ID (unchanged from prior behaviour) so
    the NEDAP write path is unaffected: NEDAP is a PAR_CLONE_MAP type and
    write.py feeds the writer the cache `data` field. We do NOT change what
    `data` holds for NEDAP.

    Additive only: subtype and customer code are captured as per-field sim
    values (format v2 line 3+) so NEDAP simulate prepopulation works from a
    dump — these were previously unrecoverable. Line 1 stays the raw block
    hex (write payload); line 2 stays the card ID (display + PAR_CLONE key).
    """
    ret = executor.startPM3Task('lf nedap reader', TIMEOUT)
    if ret == -1:
        return createRetObj(None, None, -1)
    content = executor.getPrintContent()
    if not content or executor.isEmptyContent():
        return createRetObj(None, None, -1)
    uid = executor.getContentFromRegexG(lfsearch.REGEX_CARD_ID, 1)
    raw = executor.getContentFromRegexG(lfsearch.REGEX_RAW, 1)
    if uid:
        uid = lfsearch.cleanHexStr(uid.strip())
    if raw:
        raw = lfsearch.cleanHexStr(raw.strip())
    if uid or raw:
        extras = {}
        subtype = executor.getContentFromRegexG(lfsearch._RE_SUBTYPE, 1)
        code = executor.getContentFromRegexG(lfsearch._RE_CUSTOMER_CODE, 1)
        if subtype:
            extras['subtype'] = subtype.strip()
        if code:
            # Store as 'cc' so the sim field label 'CC:' -> ('cc', 'code',
            # 'cn') mapping in _LABEL_TO_CACHE_KEY correctly prepopulates
            # the customer code (--cc). This is Nedap-specific; other tag
            # types' 'CN:' -> ('cn',) mapping (e.g. Noralsy) is untouched.
            extras['cc'] = code.strip()
        if save:
            # display = card id (unchanged behaviour, keeps PAR_CLONE write
            # working); raw block hex on line 1.
            _save_txt(32, uid, raw, display=uid, extras=extras)
        return createRetObj(uid, raw, 1)
    return createRetObj(None, None, -1)


def readNoralsy(listener=None, infos=None, save=True):
    """Read a Noralsy tag and save the dump.

    cmdlfnoralsy.c:106 emits:
        "Noralsy - Card: %u, Year: %u, Raw: %08X%08X%08X"
    CN and Year are both decimal; Raw is 24 hex chars.

    readCardIdAndRaw cannot capture Year — REGEX_CARD_ID only extracts
    the card number and has no awareness of the Year field on the same line.
    This dedicated function captures CN via REGEX_NORALSY_CN and Year via
    REGEX_NORALSY_YEAR, then stores them as "CN-Year" in the filename
    (e.g. Noralsy-ID_133778-2026_1.txt) so tag info can recover both
    values without touching the file content, which remains raw hex only
    and is unaffected by the write path.
    """
    ret = executor.startPM3Task('lf noralsy reader', TIMEOUT)
    if ret == -1:
        return createRetObj(None, None, -1)
    content = executor.getPrintContent()
    if not content or executor.isEmptyContent():
        return createRetObj(None, None, -1)
    cn   = executor.getContentFromRegexG(lfsearch.REGEX_NORALSY_CN, 1)
    year = executor.getContentFromRegexG(lfsearch.REGEX_NORALSY_YEAR, 1)
    raw  = executor.getContentFromRegexG(lfsearch.REGEX_RAW, 1)
    if raw:
        raw = lfsearch.cleanHexStr(raw.strip())
    # Build uid as "CN-Year" for filename encoding. Falls back to CN alone
    # if year is missing, and to raw if neither is present.
    if cn and year:
        uid = '%s-%s' % (cn, year)
    elif cn:
        uid = cn
    else:
        uid = None
    if uid or raw:
        if save:
            extras = {}
            if cn:
                extras['cn'] = cn.strip() if hasattr(cn, 'strip') else cn
            if year:
                extras['year'] = year.strip() if hasattr(year, 'strip') else year
            _save_txt(33, uid, raw, display=uid, extras=extras)
        return createRetObj(uid, raw, 1)
    return createRetObj(None, None, -1)


def readPAC(listener=None, infos=None, save=True):
    return readCardIdAndRaw('lf pac reader', typ=34, save=save)


def readParadox(listener=None, infos=None, save=True):
    return readFCCNAndRaw('lf paradox reader', typ=35, save=save)


def readPresco(listener=None, infos=None, save=True):
    return readCardIdAndRaw('lf presco reader', typ=36, save=save)


def readVisa2000(listener=None, infos=None, save=True):
    return readCardIdAndRaw('lf visa2000 reader', typ=37, save=save)


def readNexWatch(listener=None, infos=None, save=True):
    return readCardIdAndRaw('lf nexwatch reader', typ=45, save=save)


READ = {
    8: readEM410X,
    9: readHID,
    10: readIndala,
    11: readAWID,
    12: readProxIO,
    13: readGProx2,
    14: readSecurakey,
    15: readViking,
    16: readPyramid,
    23: readT55XX,
    24: readEM4X05,
    28: readFDX,
    29: readGALLAGHER,
    30: readJablotron,
    31: readKeri,
    32: readNedap,
    33: readNoralsy,
    34: readPAC,
    35: readParadox,
    36: readPresco,
    37: readVisa2000,
    45: readNexWatch,
}
