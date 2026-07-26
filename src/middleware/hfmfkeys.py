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

"""hfmfkeys -- MIFARE Classic key management & recovery.

Reimplemented from hfmfkeys.so (iCopy-X v1.0.90, Cython 0.29.21, ARM 32-bit).
Full implementation — all exported functions for read AND write flows.

Ground truth:
    Strings:     docs/v1090_strings/hfmfkeys_strings.txt
    Audit:       docs/V1090_MODULE_AUDIT.txt (lines 454-512)
    Trace:       docs/Real_Hardware_Intel/trace_write_activity_attrs_20260402.txt
"""

import os
import re
import threading
import time

try:
    import executor
except ImportError:
    try:
        from . import executor
    except ImportError:
        executor = None

try:
    import mifare
except ImportError:
    try:
        from . import mifare
    except ImportError:
        mifare = None

# ---------------------------------------------------------------------------
# Module-level state
# ---------------------------------------------------------------------------
KEYS_MAP = {}
progressListener = None
keyInTagMax = 32

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
A = 'A'
B = 'B'
AB = 'AB'

RECOVERY_FCHK = 'ChkDIC'
RECOVERY_DARK = 'Darkside'
RECOVERY_NEST = 'Nested'
RECOVERY_STNE = 'STnested'

TIME_FHCK_ONE = 0.01
TIME_DARKSIDE_ONE = 60
TIME_NESTED_ONE = 11

TMP_KEYS_DIR = '/tmp/.keys'
TMP_KEYS_FILE = '/tmp/.keys/mf_tmp_keys.dic'
TMP_COMBO_FILE = '/tmp/.keys/mf_combo_keys.dic'

# Live key dictionaries on the SD card.  _USER_DIC accumulates keys recovered
# by nested/darkside that were NOT in the dictionary, so fchk finds them
# directly on future reads instead of re-running recovery.
_SD_DIC = '/mnt/upan/keys/mf1/mfc_default_keys.dic'
_USER_DIC = '/mnt/upan/keys/mf1/mfc_users_keys.dic'

# ---------------------------------------------------------------------------
# Default key dictionary (from hfmfkeys_strings.txt lines 3083-3188)
# ---------------------------------------------------------------------------
DEFAULT_KEYS = [
    'FFFFFFFFFFFF', 'E00000000000', '000000000000', '111111111111',
    '010203040506', '0A65CB3EB977', '0000014B5C31', '021209197591',
    '050908080008', '0D258FE90296', '123456789ABC', '123456ABCDEF',
    '160A91D29A9C', '17193709ADF4', '199404281970', '1A2B3C4D5E6F',
    '1A982C7E459A', '1ACC3189578C', '22729A9BD40F', '2A2C13CC242A',
    '2EF720F2AF76', '314B49464956', '34016FAC127D', '369A4663ACD2',
    '38FCF33072E0', '4143532D494E', '414354616374', '414C41524F4E',
    '416363657302', '416363657303', '416363657304', '416363657305',
    '416363657306', '416363657307', '416363657308', '416363657309',
    '41636365730A', '41636365730B', '41636365730C', '41636365730D',
    '41636365730E', '41636365730F', '424C41524F4E', '4338265AFB87',
    '434143445649', '434456495243', '444156494442', '484558414354',
    '48734389EDC3', '49FAE4E3849F', '4A4C474F524D', '4A6352684677',
    '4D3A99C351DD', '4D414C414741', '4D61071B7254', '4F47454C4543',
    '509359F131B1', '533CB6C723F6', '536653644C65', '564C505F4D41',
    '587EE5F9350F', '5E594208EF02', '62D0C424ED8E', '6465706F7420',
    '66B03ACA6EE9', '66D2B7DC39EF', '6A1987C40A21', '6BC1E1AE547D',
    '6C20494E5049', '6C6520706173', '6C697365722E', '6C78928E1317',
    '714C5C886E97', '752FBB5B7B45', '7F33625BC129', '8829DA9DAF76',
    '89347350BD36', '8AD5517B4B18', '8FA1D601D0A2', '8FD0A4F256E9',
    '911E52FD7CE4', '96A301BCE267', 'A0478CC39091', 'A0A1A2A3A4A5',
    'A22AE129C013', 'A6CAC2886412', 'AA0720018738', 'AABBCCDDEEFF',
    'ABCDEF123456', 'AF9E38D36582', 'B0B1B2B3B4B5', 'B27CCAB30DBD',
    'B578F38A5C61', 'B7BF0C13066E', 'BF1F4424AF76', 'C0C1C2C3C4C5',
    'C2B7EC7D4EB1', 'D0D1D2D3D4D5', 'D3F7D3F7D3F7', 'E64A986A5D94',
    'E7D6064C5860', 'EEB420209D0C', 'F1EC94AACD81', 'F7EF6DE261F4',
]

# ---------------------------------------------------------------------------
# Composite key functions
# ---------------------------------------------------------------------------
def createTk(sector, typ):
    return '{}_{}'.format(sector, typ)

def getSectorFromTK(tk):
    return int(tk.split('_')[0])

def getTypeFromTK(tk):
    return tk.split('_')[1]

# ---------------------------------------------------------------------------
# Key map access
# ---------------------------------------------------------------------------
def getKey4Map(sector, typ):
    return KEYS_MAP.get(createTk(sector, typ))

def putKey2Map(sector, typ, key):
    KEYS_MAP[createTk(sector, typ)] = key

def delKey4Map(sector, typ):
    KEYS_MAP.pop(createTk(sector, typ), None)

def getAnyKey():
    for v in KEYS_MAP.values():
        return v
    return None

def hasKeyA(sector):
    return createTk(sector, A) in KEYS_MAP

def hasKeyB(sector):
    return createTk(sector, B) in KEYS_MAP

def hasAllKeys(size):
    sc = mifare.getSectorCount(size) if mifare else 0
    for sector in range(sc):
        if createTk(sector, A) not in KEYS_MAP:
            return False
        if createTk(sector, B) not in KEYS_MAP:
            return False
    return True

def getKeyMax4Size(size):
    sc = mifare.getSectorCount(size) if mifare else 0
    return sc - 1 if sc > 0 else 0

def getLostKeySector(size):
    sc = mifare.getSectorCount(size) if mifare else 0
    lost = 0
    for sector in range(sc):
        if createTk(sector, A) not in KEYS_MAP or createTk(sector, B) not in KEYS_MAP:
            lost += 1
    return lost

def getSizeFromBigSize(size):
    if size >= 4096:
        return 4
    if size >= 2048:
        return 2
    if size >= 1024:
        return 1
    return 0

# ---------------------------------------------------------------------------
# Key file I/O
# ---------------------------------------------------------------------------
def init_m1_key_file():
    try:
        os.makedirs(TMP_KEYS_DIR, exist_ok=True)
    except OSError:
        pass

def read_keys_of_file(file):
    keys = []
    try:
        with open(file, 'r') as f:
            for line in f:
                line = line.strip()
                if len(line) == 12 and re.match(r'^[A-Fa-f0-9]{12}$', line):
                    keys.append(line.upper())
    except (OSError, IOError):
        pass
    return keys

def append_keys_unique(files, key_list):
    seen = set(k.upper() for k in key_list)
    for fpath in (files if isinstance(files, list) else [files]):
        for k in read_keys_of_file(fpath):
            ku = k.upper()
            if ku not in seen:
                key_list.append(ku)
                seen.add(ku)

def genKeyFile(uid, key_list):
    init_m1_key_file()
    try:
        with open(TMP_KEYS_FILE, 'w') as f:
            for key in key_list:
                f.write(key + '\n')
    except (OSError, IOError):
        pass
    return TMP_KEYS_FILE

def list_split(items, n):
    """Split list into chunks of n."""
    return [items[i:i + n] for i in range(0, len(items), n)]

# ---------------------------------------------------------------------------
# fchks — fast dictionary key check
# PM3: hf mf fchk {size_param} {keyfile}  (timeout=600000)
# ---------------------------------------------------------------------------
# Iceman key-table row.  Two formats are emitted depending on PM3
# build vintage; this regex matches both:
#
#   Legacy / older iceman (5 fields, outer `|` borders):
#     `| 000 | 484558414354 | 1 | a22ae129c013 | 1 |`
#     Matrix section `hf mf fchk` (divergence_matrix.md L711-736).
#
#   Iceman v4.21611+ (6 fields incl. new Blk column, no outer `|`):
#     `-----+-----+--------------+---+--------------+----`
#     ` 000 | 003 | 484558414354 | 1 | a22ae129c013 | 1`
#     Source: cmdhfmf.c:4985 (separator) + cmdhfmf.c:5037 row format
#     `" %03d | %03d | %s | %s | %s | %s %s"` where col 2 is the
#     sector-trailer block number from mfSectorTrailerOfSector().
#     Verified live on iceman v4.21611 — without Blk-column tolerance
#     the parser captured 0 keys → middleware gave up the read flow.
#
# Captures stay 5: (sec, keyA, resA, keyB, resB).  The optional
# `(?:\d+\s*\|\s*)?` group absorbs the inserted Blk column when
# present.  Outer `|` is also optional (legacy borders vs iceman bare
# rows).  ANSI colour codes are stripped upstream by
# executor._clean_pm3_output (executor.py:67) so no `\x1b\[…m` here.
_RE_KEY_TABLE = re.compile(
    r'\|?\s*(\d+)\s*\|\s*(?:\d+\s*\|\s*)?'
    r'([A-Fa-f0-9-]{12})\s*\|\s*(\d+)\s*\|\s*'
    r'([A-Fa-f0-9-]{12})\s*\|\s*(\d+)'
)
_RE_HEX_KEY = re.compile(r'^[A-Fa-f0-9]{12}$')

def keysFromPrintParse(size):
    """Parse fchk output to populate KEYS_MAP.

    Only stores keys where res=1 (success) and the key is valid hex.
    The original .so ignores keys with res=0 (failed verification).
    Rows with '------------' (dashes) for a key field are skipped for that key.
    """
    text = executor.CONTENT_OUT_IN__TXT_CACHE if executor else ''
    for m in _RE_KEY_TABLE.finditer(text):
        sector = int(m.group(1))
        key_a = m.group(2).upper()
        res_a = int(m.group(3))
        key_b = m.group(4).upper()
        res_b = int(m.group(5))
        if res_a == 1 and _RE_HEX_KEY.match(key_a):
            putKey2Map(sector, A, key_a)
        if res_b == 1 and _RE_HEX_KEY.match(key_b):
            putKey2Map(sector, B, key_b)

def _writeComboFile(keys):
    """Write a key list to the temporary combined dictionary and return it."""
    init_m1_key_file()
    try:
        with open(TMP_COMBO_FILE, 'w') as f:
            for k in keys:
                f.write(k + '\n')
    except (OSError, IOError):
        pass
    return TMP_COMBO_FILE

def _resolveDicFile(uid):
    """Pick the dictionary file for fchk.

    If the learned user dic (mfc_users_keys.dic) exists, build a fresh combined
    file with the USER keys first (previously-cracked keys hit fast on repeat
    cards) followed by DEFAULT_KEYS, de-duplicated. Otherwise DEFAULT_KEYS.

    The full iceman dic (mfc_default_keys.dic) is deliberately NOT used here:
    crunching ~2500 keys on a problematic card can lock up the device. Normal
    flows use the small user + DEFAULT_KEYS set (plus iceman's hardcoded 61);
    the mfc_default_keys.dic is used ONLY to help with savedLeanredKeys()
    and de-duplicating correctly.
    """
    if os.path.exists(_USER_DIC) and os.path.getsize(_USER_DIC) > 0:
        try:
            combined = read_keys_of_file(_USER_DIC)          # user keys first
            default_keys = [k.upper() for k in DEFAULT_KEYS]
            seen = set(combined)
            for k in default_keys:                           # then default keys
                if k not in seen:
                    combined.append(k)
                    seen.add(k)
            if combined:
                return _writeComboFile(combined)
        except Exception:
            pass
    return genKeyFile(uid, list(DEFAULT_KEYS))

def saveLearnedKeys():
    """Append any recovered key not already in DEFAULT_KEYS or the user dic to
    the user dic, so fchk finds it directly next time. No-op when nothing new.

    De-dup is against the SAME small set recovery actually uses (DEFAULT_KEYS +
    user dic) -- deliberately NOT the full iceman dic (_SD_DIC). That dic is
    not loaded by normal flow, so if a recovered key lived only there we would
    otherwise skip learning it and re-run nested for it on every future card.
    Learning it here lets the user dic grow to match the user's real cards.
    """
    found = set(v.upper() for v in KEYS_MAP.values() if v)
    if not found:
        return
    known = set(k.upper() for k in DEFAULT_KEYS)
    if os.path.exists(_USER_DIC):
        known |= set(read_keys_of_file(_USER_DIC))
    new_keys = sorted(found - known)
    if not new_keys:
        return
    try:
        os.makedirs(os.path.dirname(_USER_DIC), exist_ok=True)
        with open(_USER_DIC, 'a') as f:
            for k in new_keys:
                f.write(k + '\n')
            f.flush()
            os.fsync(f.fileno())
    except (OSError, IOError):
        pass

def fchks(infos, size, with_call=True):
    """Dictionary key check. PM3: hf mf fchk.

    Key file priority (see _resolveDicFile):
        1. mfc_users_keys.dic present -> combined file: user keys first, then
           mfc_default_keys.dic, de-duplicated.
        2. mfc_default_keys.dic on SD (no user dic).
        3. genKeyFile(DEFAULT_KEYS) — hardcoded ~100 key fallback.
    """
    uid = infos.get('uid', '') if isinstance(infos, dict) else ''
    key_file = _resolveDicFile(uid)

    size_flag = {4096: '--4k', 2048: '--2k', 320: '--mini'}.get(size, '--1k')
    cmd = 'hf mf fchk {} -f {}'.format(size_flag, key_file)
    ret = executor.startPM3Task(cmd, 600000)
    if ret == -1:
        return -1
    keysFromPrintParse(size)
    return 1

# ---------------------------------------------------------------------------
# Key recovery — darkside / nested
# These send PM3 commands and parse responses.
# ---------------------------------------------------------------------------
def darkside():
    """Darkside attack. PM3: hf mf darkside.

    Iceman-native key emission: ``Found valid key [ %012X ]`` from
    /tmp/rrg-pm3/client/src/cmdhfmf.c:1275 (capital "Found", bracketed,
    uppercase hex via PRIX64). Matrix section `hf mf darkside`
    (divergence_matrix.md L687-707).
    """
    ret = executor.startPM3Task('hf mf darkside', 120000)
    if ret == -1:
        return -1
    text = executor.CONTENT_OUT_IN__TXT_CACHE or ''
    # Iceman bracketed form (cmdhfmf.c:1275). re.IGNORECASE to tolerate the
    # nested-attack lowercase "found" variant (mifarehost.c:686) when this
    # helper is reused from tests; not needed for darkside proper.
    m = re.search(r'Found valid key\s*\[\s*([A-Fa-f0-9]{12})\s*\]',
                  text, re.IGNORECASE)
    if m:
        key = m.group(1).upper()
        putKey2Map(0, A, key)
        return 1
    return -1

def darksideOneKey():
    """Single darkside attempt."""
    return darkside()

def onNestedCall(lines):
    """Callback for nested attack progress."""
    pass

def _sectorBlock(sector):
    """First block of a sector, correct for 1k/2k/4k (4k sectors 32-39 are
    16 blocks each). Falls back to sector*4 only if mifare is unavailable."""
    return mifare.sectorToBlock(sector) if mifare else sector * 4

# Keys already fanned across the card this run (see _propagate). Cleared at
# the start of each nested() run.
_PROPAGATED = set()

def _propagate(key, size):
    """Fan a freshly-recovered key across ALL sectors (both A and B).

    Keys are frequently reused across sectors, so a single cheap
    ``hf mf fchk -k <key>`` (one call tests the key as A and B on every
    sector) often unlocks more sectors than nested has reached yet, shrinking
    the remaining work. Best-effort: any failure is swallowed and recovery
    continues. Each distinct key is propagated at most once per nested() run.
    """
    if not key or key in _PROPAGATED:
        return
    _PROPAGATED.add(key)
    size_flag = {4096: '--4k', 2048: '--2k', 320: '--mini'}.get(size, '--1k')
    try:
        if executor.startPM3Task('hf mf fchk {} -k {}'.format(size_flag, key),
                                 600000, rework_max=0) == -1:
            return
        keysFromPrintParse(size)
    except Exception:
        pass

def nestedOneKey(known, target, size, retryMax=5):
    """Nested attack for a single key (targeted --tblk form).

    Iceman-native emission: ``Target block %4u key type %c -- found valid
    key [ %012X ]`` from /tmp/rrg-pm3/client/src/mifare/mifarehost.c:686
    (lowercase "found", bracketed hex). Matrix `hf mf nested`
    (divergence_matrix.md L740-759); iceman_output.json samples 3-8
    confirm exact shape on the device.
    """
    known_sector = getSectorFromTK(known)
    known_type = getTypeFromTK(known)
    known_key = getKey4Map(known_sector, known_type)
    if not known_key:
        return -1
    target_sector = getSectorFromTK(target)
    target_type = getTypeFromTK(target)
    size_flag = {4096: '--4k', 2048: '--2k', 320: '--mini'}.get(size, '--1k')
    cmd = 'hf mf nested {} --blk {} {} -k {} --tblk {} {}'.format(
        size_flag, _sectorBlock(known_sector),
        '-a' if known_type == 'A' else '-b', known_key,
        _sectorBlock(target_sector), '--ta' if target_type == 'A' else '--tb')
    ret = executor.startPM3Task(cmd, 30000)
    if ret == -1:
        return -1
    text = executor.CONTENT_OUT_IN__TXT_CACHE or ''
    # Iceman bracketed form (mifarehost.c:686 — lowercase "found"). darkside
    # tail (cmdhfmf.c:1275 — capital "Found") also matches via IGNORECASE.
    m = re.search(r'found valid key\s*\[\s*([A-Fa-f0-9]{12})\s*\]',
                  text, re.IGNORECASE)
    if m:
        key = m.group(1).upper()
        putKey2Map(target_sector, target_type, key)
        _propagate(key, size)
        return 1
    return -1

def nestedFull(known, size):
    """Full-card nested sweep from one known key.

    PM3: hf mf nested <size> --blk N -a/-b -k KEY  (no --tblk -> recovers
    every sector from the one seed). Used when fewer than half the keys are
    known, where a single sweep is cheaper than many targeted runs. Output is
    the standard printKeyTable, parsed straight into KEYS_MAP.
    """
    known_sector = getSectorFromTK(known)
    known_type = getTypeFromTK(known)
    known_key = getKey4Map(known_sector, known_type)
    if not known_key:
        return -1
    size_flag = {4096: '--4k', 2048: '--2k', 320: '--mini'}.get(size, '--1k')
    cmd = 'hf mf nested {} --blk {} {} -k {}'.format(
        size_flag, _sectorBlock(known_sector),
        '-a' if known_type == 'A' else '-b', known_key)
    ret = executor.startPM3Task(cmd, 300000)
    if ret == -1:
        return -1
    keysFromPrintParse(size)
    # Fan each recovered key across all sectors: reuse is common, so this can
    # complete sectors the single sweep missed.
    for k in sorted(set(v.upper() for v in KEYS_MAP.values() if v)):
        _propagate(k, size)
    return 1

def nested(size, infos):
    """Nested attack for missing keys - adaptive (1k/2k/4k).

    >= half the keys known: recover only the missing ones with fast targeted
    single-key nested (--tblk). < half known: one full-card sweep from the
    seed key, cheaper than many individual targeted runs.
    """
    _PROPAGATED.clear()
    known = getAnyKey()
    if not known:
        return -1
    known_tk = None
    for tk, key in KEYS_MAP.items():
        if key:
            known_tk = tk
            break
    if not known_tk:
        return -1

    sc = mifare.getSectorCount(size) if mifare else 16
    total = sc * 2
    found = sum(1 for v in KEYS_MAP.values() if v)

    if found * 2 < total:
        # Fewer than half known -> one full-card sweep from the seed.
        nestedFull(known_tk, size)
    else:
        # Half or more known -> targeted recovery of just the missing keys.
        for sector in range(sc):
            for typ in (A, B):
                if not getKey4Map(sector, typ):
                    nestedOneKey(known_tk, createTk(sector, typ), size)
    return 1

def nestedAllKeys(infos, size):
    """Recover all keys via nested."""
    return nested(size, infos)

# ---------------------------------------------------------------------------
# keys — full recovery pipeline
# ---------------------------------------------------------------------------
def keys(size, infos, listener):
    """Full key recovery pipeline: fchk → darkside → nested."""
    global progressListener
    progressListener = listener
    updateKeyMax(mifare.getSectorCount(size) * 2)

    # Start elapsed-time counter thread — drives the timer display
    # (ground truth: "01'08''" on read_tag_reading_2.png).
    _start_timer()

    try:
        updateRecovery(RECOVERY_FCHK)
        fchks(infos, size, with_call=True)
        updateKeyFound(0)
        if hasAllKeys(size):
            return 1

        # Darkside only when fchk found NOTHING to pivot from.  If we already
        # have >=1 key, nested can seed from it directly, so skip darkside
        # entirely (it is the slow / hang-prone stage and exists only to
        # obtain a first key from scratch).
        if not getAnyKey():
            updateRecovery(RECOVERY_DARK)
            darkside()
            updateKeyFound(0)
            if hasAllKeys(size):
                return 1

            updateRecovery(RECOVERY_FCHK)
            fchks(infos, size, with_call=False)
            updateKeyFound(0)
            if hasAllKeys(size):
                return 1

        updateRecovery(RECOVERY_NEST)
        nested(size, infos)
        updateKeyFound(0)
        if hasAllKeys(size):
            return 1

        updateRecovery(RECOVERY_FCHK)
        fchks(infos, size, with_call=False)
        updateKeyFound(0)
        return 1 if hasAllKeys(size) else -1
    finally:
        _stop_timer()
        # Persist any newly recovered (non-dictionary) keys for next time.
        saveLearnedKeys()

# ---------------------------------------------------------------------------
# Progress callbacks
#
# Ground truth: activity_read.py onReading() expects:
#   {'m1_keys': True, 'seconds': N, 'action': 'ChkDIC'|'Darkside'|'Nested',
#    'keyIndex': N, 'keyCountMax': 32, 'progress': N}
# ---------------------------------------------------------------------------
_current_action = ''
_timer_running = False
_timer_elapsed = 0

def _start_timer():
    """Start the 1-second elapsed-time counter thread.

    Ground truth: original .so count_down drives periodic progress
    callbacks with elapsed seconds so the UI timer ("01'08''") updates
    every second even when no keys are being found.
    """
    global _timer_running, _timer_elapsed
    _timer_elapsed = 0
    _timer_running = True

    def _tick():
        global _timer_elapsed
        while _timer_running:
            time.sleep(1)
            if not _timer_running:
                break
            _timer_elapsed += 1
            callProgress(seconds=_timer_elapsed)

    t = threading.Thread(target=_tick, daemon=True)
    t.start()

def _stop_timer():
    """Stop the elapsed-time counter thread."""
    global _timer_running
    _timer_running = False

def callProgress(seconds=0):
    """Report progress to the listener."""
    if progressListener is not None:
        found = sum(1 for v in KEYS_MAP.values() if v)
        # Use the running timer's elapsed seconds if no explicit value
        secs = seconds if seconds else _timer_elapsed
        try:
            progressListener({
                'm1_keys': True,
                'seconds': int(secs),
                'action': _current_action,
                'keyIndex': found,
                'keyCountMax': keyInTagMax,
                'progress': int(found * 100 / max(keyInTagMax, 1)),
            })
        except Exception:
            pass

def count_down():
    """Legacy entry point — timer is now thread-based via _start_timer."""
    pass

def updateKeyFound(count):
    """Report that keys were found."""
    callProgress()

def updateKeyMax(key_count_max):
    global keyInTagMax
    keyInTagMax = key_count_max

def updateRecovery(rec):
    """Update current recovery action and notify listener."""
    global _current_action
    _current_action = rec
    callProgress()

def is_keys_check_call(call):
    return False
