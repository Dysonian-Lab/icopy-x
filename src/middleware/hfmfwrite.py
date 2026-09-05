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

"""hfmfwrite -- MIFARE Classic writer.

Reimplemented from hfmfwrite.so (iCopy-X v1.0.90, Cython 0.29.21, ARM 32-bit).
DRM gate (tagChk1) is BYPASSED — open-source implementation.

Ground truth:
    Strings:     docs/v1090_strings/hfmfwrite_strings.txt
    Spec:        docs/middleware-integration/6-write_spec.md (section 2)
    Trace:       docs/Real_Hardware_Intel/trace_write_activity_attrs_20260402.txt

API:
    write(listener, infos, bundle) -> int
    verify(infos, bundle) -> int

    Internal:
        write_common, write_with_standard, write_with_gen1a,
        write_block, start_wrbl_cmd, gen1afreeze, call_progress,
        read_blocks_4file, tagChk1 (bypassed)

Return codes:
    1   = success
    -1  = failure
    -9  = DRM failure (never returned — DRM bypassed)
    -10 = critical failure
"""

try:
    import executor
except ImportError:
    try:
        from . import executor
    except ImportError:
        executor = None

try:
    import hfmfkeys
except ImportError:
    try:
        from . import hfmfkeys
    except ImportError:
        hfmfkeys = None

try:
    import hfmfread
except ImportError:
    try:
        from . import hfmfread
    except ImportError:
        hfmfread = None

try:
    import mifare
except ImportError:
    try:
        from . import mifare
    except ImportError:
        mifare = None

try:
    import scan
except ImportError:
    try:
        from . import scan
    except ImportError:
        scan = None

# ---------------------------------------------------------------------------
# DRM bypass — tagChk1
# Original: AES-based license check via /proc/cpuinfo serial.
# Open-source: always passes, returns a no-op tag factory.
# Strings: __pyx_k_tagChk1, __pyx_k_AA55C396, __pyx_k_Crypto_Cipher,
#          __pyx_k_cat_proc_cpuinfo, __pyx_k_VB1v2qvOinVNIlv2
# ---------------------------------------------------------------------------
def tagChk1(infos, file, newinfos):
    """DRM gate — BYPASSED.

    Original checks cpuinfo serial, computes MD5, decrypts AES,
    compares against AA55C396 marker.  Returns init_tag factory on
    success, or causes write_common to return -9 on failure.

    Open-source: always returns a lambda that passes through the
    infos dict unchanged.
    """
    def init_tag(infos_arg):
        return infos_arg
    return init_tag

# ---------------------------------------------------------------------------
# read_blocks_4file — load dump .bin into dict
# Strings: __pyx_k_read_blocks_4file
# Binary format: 16 bytes per block, sequential
# ---------------------------------------------------------------------------
def read_blocks_4file(infos, file):
    """Load blocks from binary dump file.

    Two callers pass different path forms:
        Dump file write: bundle = os.path.splitext(path)[0] — no extension.
        AutoCopy:        bundle = full bin_path from read.py — already has .bin.

    Strip any existing .bin suffix before appending so both paths resolve
    to the correct file without double-extension (path.bin.bin).

    Returns dict: block_num → 32-char uppercase hex string.
    """
    blocks = {}
    try:
        base = file[:-4] if file.lower().endswith('.bin') else file
        with open(base + '.bin', 'rb') as f:
            block_num = 0
            while True:
                data = f.read(16)
                if not data or len(data) < 16:
                    break
                blocks[block_num] = data.hex().upper()
                block_num += 1
    except Exception:
        return {}
    return blocks

# ---------------------------------------------------------------------------
# _read_block0_from_json — JSON fallback for block 0
# ---------------------------------------------------------------------------
def _read_block0_from_json(file):
    """Read block 0 hex from companion .json sidecar file.

    Used as a fallback when block 0 is absent from the .bin dict.
    The .json is the iceman mfc v2 schema saved alongside the .bin —
    same base path, .json extension.  Block 0 is stored as
    blocks["0"] = "<32-char uppercase hex>".

    Returns 32-char uppercase hex string, or None if unavailable.
    """
    try:
        import json as _json
        base = file[:-4] if file.lower().endswith('.bin') else file
        with open(base + '.json', 'r') as f:
            doc = _json.load(f)
        val = doc.get('blocks', {}).get('0')
        if val and len(val) >= 32:
            return val.upper()[:32]
    except Exception:
        pass
    return None

# ---------------------------------------------------------------------------
# write_block / start_wrbl_cmd — per-block write
# Iceman source: /tmp/rrg-pm3/client/src/cmdhfmf.c:1389
#   PrintAndLogEx(SUCCESS, "Write ( " _GREEN_("ok") " )");
#   PrintAndLogEx(FAILED,  "Write ( " _RED_("fail") " )");
# Iceman has ZERO `isOk:` emissions in the write path (grep-verified matrix
# v2 L817). Prior regex alternation `r'isOk:01|Write \( ok \)'` carried the
# legacy sentinel forward for `_normalize_wrbl_response` adapter output.
# Post-flip: target iceman-native `Write ( ok )` substring directly.
# ---------------------------------------------------------------------------
# Iceman-native success keyword for `hf mf wrbl`.
# Matrix: divergence_matrix.md L815/L845 — iceman-form emission.
# Source: /tmp/rrg-pm3/client/src/cmdhfmf.c:1389, :9677, :9760 (three
# `Write ( ok )` emission sites — all three use identical literal).
_KW_WRBL_SUCCESS = r'Write \( ok \)'

def start_wrbl_cmd(block, typ, key, data):
    """Build the wrbl command string.

    Iceman form (cmdhfmf.c:1280 CmdHF14AMfWrBl CLI):
        `hf mf wrbl --blk <N> -a|-b -k <KEY> -d <DATA> [--force]`
    """
    return 'hf mf wrbl --blk {} {} -k {} -d {} --force'.format(
        block, '-a' if typ == 'A' else '-b', key, data)

def write_block(block, typ, key, data):
    """Write a single block.

    Returns 1 on success (iceman `Write ( ok )`), -1 on failure.
    """
    cmd = start_wrbl_cmd(block, typ, key, data)
    ret = executor.startPM3Task(cmd, 10000)
    if ret == -1:
        return -1
    if executor.hasKeyword(_KW_WRBL_SUCCESS):
        return 1
    return -1

# ---------------------------------------------------------------------------
# call_progress — progress reporting
# Strings: __pyx_k_call_progress, __pyx_k_progress, __pyx_k_max_value
# ---------------------------------------------------------------------------
def call_progress(listener, progress, max_val):
    """Report progress to listener: {'max': N, 'progress': M}."""
    if listener is None:
        return
    try:
        listener({'max': max_val, 'progress': progress})
    except Exception:
        pass

# ---------------------------------------------------------------------------
# gen1afreeze — lock Gen1a magic card (5 iceman-native `hf 14a raw` calls)
# Matrix: divergence_matrix.md L172-189 `hf 14a raw`.
# Iceman CLI: /tmp/rrg-pm3/client/src/cmdhf14a.c:1547/1670 — keep-field
#   flag is `-k` (iceman) vs legacy `-p` (pm3_compat.py:243 translator,
#   :550 reverse translator).
# Middleware sends iceman syntax verbatim; response is discarded
# (fire-and-forget sequence, matrix L189).
# ---------------------------------------------------------------------------
def gen1afreeze():
    """Execute Gen1a freeze sequence (5 raw commands, iceman-native `-k`)."""
    commands = [
        'hf 14a raw -k -a -b 7 40',
        'hf 14a raw -c -k -a 43',
        'hf 14a raw -c -k -a e000',
        'hf 14a raw -c -k -a 85000000000000000000000000000008',
        'hf 14a raw -c -a 5000',
    ]
    for cmd in commands:
        executor.startPM3Task(cmd, 10000)

# ---------------------------------------------------------------------------
# write_with_gen1a — bulk load via cload
# Strings: __pyx_k_hf_mf_cload_b, __pyx_k_Card_loaded_d_blocks_from_file,
#          __pyx_k_Can_t_set_magic_card_block
# ---------------------------------------------------------------------------
def _normalize_gen1a_sak(infos, file):
    """Force block-0 SAK to 08 on a just-loaded Gen1a card.

    A dump may carry SAK 0x88 (Infineon / magic 1K sets the high bit); most
    access readers won't select a card advertising 0x88, so rewrite block 0
    with SAK 0x08.  The UID is sourced from a fresh `hf 14a info` (post-cload
    the card carries the dump's UID); ATQA is fixed at 0004 (Gen1a MIFARE
    Classic is always 1K).  `hf mf csetuid` keeps UID/BCC/manufacturer bytes
    and changes only the SAK/ATQA.

    Records the resulting block 0 in infos['gen1a_written_b0'] so verify()
    checks the card against what we actually wrote, not the dump's block 0.

    Gen1a MIFARE Classic is 1K only, so 08 is always the correct SAK and no
    size handling is needed.  No-op if the card UID can't be read.
    """
    import re as _re

    # Source the UID from the live card (post-cload it carries the dump's UID).
    executor.startPM3Task('hf 14a info', 10000)
    text = executor.CONTENT_OUT_IN__TXT_CACHE or ''
    m_uid = _re.search(r'UID:\s*([0-9A-Fa-f ]+)', text)
    if not m_uid:
        return
    uid = m_uid.group(1).replace(' ', '').upper()

    cmd = 'hf mf csetuid -u {} --atqa 0004 --sak 08'.format(uid)
    ret = executor.startPM3Task(cmd, 10000)
    if ret == -1:
        return
    if executor.hasKeyword("Can't set UID"):
        return
    out = executor.CONTENT_OUT_IN__TXT_CACHE or ''
    m_b0 = _re.search(r'new block 0[.\s]*([0-9A-Fa-f]{32})', out)
    if m_b0:
        infos['gen1a_written_b0'] = m_b0.group(1).upper()
    else:
        # csetuid succeeded but the echo shape changed — reconstruct expected
        # block 0 from the dump: SAK forced to 08, ATQA fixed at 0004 (stored
        # little-endian as 0400), UID/BCC/manufacturer bytes preserved.
        dump_b0 = _dump_uid(infos, file)
        if dump_b0 and len(dump_b0) >= 32:
            infos['gen1a_written_b0'] = (dump_b0[:10] + '08' + '0400'
                                         + dump_b0[16:]).upper()



def write_with_gen1a(infos, file):
    """Write entire dump to Gen1a card via cload.

    Spec §2.6:
        1. hf mf cload b {file}
        2. Check 'Card loaded' in response
        3. force block-0 SAK to 08 via csetuid
        4. gen1afreeze()

    Returns 1 on success, -1 on failure.
    """
    cmd = 'hf mf cload -f {}'.format(file)
    ret = executor.startPM3Task(cmd, 10000)
    if ret == -1:
        return -1

    if executor.hasKeyword("Can't set magic"):
        return -1
    if not executor.hasKeyword('Card loaded'):
        return -1

    # Force block-0 SAK to 08 before sealing so readers will select the clone
    # (dumps often carry 0x88); records infos['gen1a_written_b0'] for verify().
    _normalize_gen1a_sak(infos, file)

    gen1afreeze()
    return 1

def write_with_gen1a_only_uid(infos):
    """Write UID-only to Gen1a card.

    Strings: __pyx_k_hf_mf_csetuid_w
    Command: hf mf csetuid {uid} {sak} {atqa} w
    """
    uid = infos.get('uid', '')
    sak = infos.get('sak', '08')
    atqa = infos.get('atqa', '0004')
    cmd = 'hf mf csetuid -u {} -s {} -a {} -w'.format(uid, sak, atqa)
    ret = executor.startPM3Task(cmd, 10000)
    if ret == -1:
        return -1
    # csetuid failure emission (iceman): cmdhfmf.c:5831 emits
    # `PrintAndLogEx(ERR, "Can't set UID. error %d", res)`. This is DISTINCT
    # from cload's `Can't set magic card block: %d` (cmdhfmf.c:6061/6108/9028).
    # Substring `"Can't set UID"` matches the csetuid-specific failure literal.
    if executor.hasKeyword("Can't set UID"):
        return -1
    gen1afreeze()
    return 1

# ---------------------------------------------------------------------------
# write_with_standard — per-block write in reverse sector order
# SHARED by standard (non-magic) AND Gen 2 / CUID cards: both use the exact
# same command sequence (every block via write_block, which always passes
# --force), so a single function serves both. The write path does not branch
# on card type; the only Gen2-specific behaviour lives in verify_gen2, which
# reads the block-success counts this function records in infos for every
# write. (Gen1a is the one genuinely different path — see write_with_gen1a.)
# Trace: blocks 60,61,62, 56,57,58, ..., 4,5,6, 0,1,2, then 63,59,...,3
# ---------------------------------------------------------------------------
def write_with_standard(infos, file, listener):
    """Write to a standard or Gen 2 / CUID MIFARE Classic card.

    Standard and Gen2 are written identically: the per-block writer always
    passes --force, so block 0 is attempted on both. On a standard card the
    manufacturer block is hardware read-only (UID unchanged); on a Gen2 it is
    writable (UID becomes the dump's). Same commands, different card silicon.

    Trace (trace_write_activity_attrs_20260402.txt):
        1. Data blocks: reverse sector order, skip trailers
           Sector 15: 60,61,62 → Sector 14: 56,57,58 → ... → Sector 0: 0,1,2
        2. Trailer blocks: reverse sector order
           63, 59, 55, 51, 47, 43, 39, 35, 31, 27, 23, 19, 15, 11, 7, 3

    All blocks including block 0 use dump file data directly.
    read_blocks_4file appends .bin to the bundle path so blocks[0]
    is the real manufacturer block from the source card dump.
    Block 0 fallback chain: .bin → JSON blocks["0"] → EMPTY_DATA.
    All other blocks fall back to EMPTY_DATA / EMPTY_TRAI.

    Records 'write_block_count' / 'write_block_total' in infos (used by
    verify_gen2) regardless of card type.

    Returns 1 if all blocks succeeded, -1 if any block failed.
    """
    # Load dump file into block dict
    blocks = read_blocks_4file(infos, file)

    # Get card geometry
    typ = infos.get('type', 1)
    size = hfmfread.sizeGuess(typ)
    sector_count = mifare.getSectorCount(size)
    total_blocks = sum(mifare.getBlockCountInSector(s) for s in range(sector_count))

    progress = 0
    write_success_list = []
    write_fail = False

    # --- Phase 1: Write data blocks (reverse sector order) ---
    # Trace (trace_original_full_20260410.txt): 3× Key A retry + 1× Key B fallback
    # per block.  Pattern: wrbl N A key → isOk:00 ×3, wrbl N B key → isOk:01
    for sector in range(sector_count - 1, -1, -1):
        first_block = mifare.sectorToBlock(sector)
        blocks_in_sector = mifare.getBlockCountInSector(sector)

        key_a = hfmfkeys.getKey4Map(sector, 'A') if hfmfkeys else None
        key_b = hfmfkeys.getKey4Map(sector, 'B') if hfmfkeys else None

        # Write data blocks (all blocks except trailer)
        for offset in range(blocks_in_sector - 1):
            block_num = first_block + offset

            # Get block data from dump — block 0 reads directly from the
            # .bin file like all other blocks. read_blocks_4file appends
            # .bin to the bundle path so blocks[0] is the real manufacturer
            # block from the source card.
            # Fallback chain for block 0: .bin → JSON blocks["0"] → EMPTY_DATA.
            # The JSON fallback protects against edge cases where the .bin read
            # succeeded but block 0 was missing (e.g. truncated file).
            if block_num == 0 and 0 not in blocks:
                block_data = _read_block0_from_json(file) or mifare.EMPTY_DATA
            else:
                block_data = blocks.get(block_num, mifare.EMPTY_DATA)

            written = False
            # Try Key A up to 3 times
            use_key_a = key_a or mifare.EMPTY_KEY
            for _attempt in range(3):
                ret = write_block(block_num, 'A', use_key_a, block_data)
                if ret == 1:
                    written = True
                    break
            # Fallback to Key B
            if not written and key_b:
                ret = write_block(block_num, 'B', key_b, block_data)
                if ret == 1:
                    written = True

            if written:
                write_success_list.append(block_num)
            else:
                write_fail = True

            progress += 1
            call_progress(listener, progress, total_blocks)

    # --- Phase 2: Write trailer blocks (reverse sector order) ---
    # Trace: same 3× Key A + 1× Key B pattern for trailers
    for sector in range(sector_count - 1, -1, -1):
        first_block = mifare.sectorToBlock(sector)
        blocks_in_sector = mifare.getBlockCountInSector(sector)
        trailer_block = first_block + blocks_in_sector - 1

        key_a = hfmfkeys.getKey4Map(sector, 'A') if hfmfkeys else None
        key_b = hfmfkeys.getKey4Map(sector, 'B') if hfmfkeys else None

        trailer_data = blocks.get(trailer_block, mifare.EMPTY_TRAI)

        written = False
        use_key_a = key_a or mifare.EMPTY_KEY
        for _attempt in range(3):
            ret = write_block(trailer_block, 'A', use_key_a, trailer_data)
            if ret == 1:
                written = True
                break
        if not written and key_b:
            ret = write_block(trailer_block, 'B', key_b, trailer_data)
            if ret == 1:
                written = True

        if written:
            write_success_list.append(trailer_block)
        else:
            write_fail = True

        progress += 1
        call_progress(listener, progress, total_blocks)

    # Store block counts in infos so verify_gen2 works even when a Gen2 card
    # was written down this (standard) path — i.e. it was not detected as Gen2
    # at write time (its keys were non-default so the FFFF magic probe missed
    # Store block counts in infos so verify_gen2 works for whichever card
    # type took this path — standard OR a Gen2 that reached here. Recorded
    # before the write_fail return so a partial write reports truly.
    infos['write_block_count'] = len(write_success_list)
    infos['write_block_total'] = total_blocks

    # Original .so returns -1 if ANY block failed to write
    if write_fail:
        return -1
    if write_success_list:
        return 1
    return -1


# ---------------------------------------------------------------------------
# write_common — main dispatch (DRM → gen1a detect → write)
# ---------------------------------------------------------------------------
def write_common(listener, infos, bundle):
    """Shared write logic: DRM gate, Gen1a detection, key check, dispatch.

    Trace sequence:
        1. hf 14a info (card present)
        2. hf mf cgetblk 0 (gen1a detect)
        3. key recovery (fchk -> nested) on TARGET card
        4. write_with_gen1a or write_with_standard
        5. hf 14a info (post-write check)
        6. hf mf cgetblk 0 (post-write gen1a check)

    Returns 1 on success, -1 on failure, -9 never (DRM bypassed).
    """
    # DRM gate — BYPASSED
    tagChk1(infos, bundle, {})

    # Step 1: Verify card present
    # Ground truth: on legacy firmware, hf 14a info always detected the card
    # here because the field stayed active.  On iceman, the field may be off
    # and the first probe can fail.  Check the response content — if no UID
    # is found, the card isn't on the reader and we must not proceed to fchk
    # (which would block for 600s on an empty reader).
    ret = executor.startPM3Task('hf 14a info', 10000)
    if ret == -1:
        return -1
    text_14a = executor.CONTENT_OUT_IN__TXT_CACHE or ''
    if not executor.hasKeyword('UID'):
        return -1

    # Capture target card UID from hf 14a info into infos before write.
    # verify() compares this pre-write UID against a fresh hf 14a info
    # post-write — for standard cards the UID never changes so they match.
    # Gen2 routes to verify_gen2() which uses block count not UID comparison,
    # so the pre-write UID capture here does not affect Gen2 verify.
    import re as _re_uid
    _uid_m = _re_uid.search(r'UID:\s*([\dA-Fa-f ]+)', text_14a)
    if _uid_m:
        infos['uid'] = _uid_m.group(1).replace(' ', '').upper()

    # Detect Gen 2 / CUID from hf 14a info output captured above.
    # A Gen2 card must never be routed to write_with_gen1a — the cgetblk
    # probe below returns block data via normal key auth on a Gen2 card
    # with factory keys, producing a false Gen1a positive. Checking
    # is_gen2 from text_14a before the dispatch prevents this regardless
    # of what the cgetblk probe returns.
    is_gen2 = 'Magic capabilities... Gen 2 / CUID' in text_14a
    if is_gen2:
        infos['gen2'] = True

    # Step 2: Gen1a detection
    # Iceman Gen1a probe response shapes (all three are positive
    # detections — block 0 was readable via the wupC1 backdoor):
    #   `Block 0: HEX` — legacy after adapter _normalize_rdbl_response
    #                    (pm3_compat.py:1241-1268).
    #   `data: HEX`    — older iceman (matrix L605) and `data save` shape.
    #   ` 0 | HEX | ascii` — iceman v4.21611 table format from
    #                    mf_print_block_one (cmdhfmf.c:565-606).  Block num
    #                    is `0` for sector 0, ` | ` separator, then 16 hex
    #                    pairs, then ` | ` ascii.
    #
    # Negative shapes (definitive "not Gen1a"):
    #   `wupC1 error` / `Can't read block. error=-1` / `Can't set magic`
    #   from /tmp/rrg-pm3/armsrc/mifarecmd.c:103-116 + cmdhfmf.c:6171.
    import re as _re
    ret = executor.startPM3Task('hf mf cgetblk --blk 0', 10000)
    is_gen1a = False
    probe_conclusive = False
    if ret == 1:
        text = executor.CONTENT_OUT_IN__TXT_CACHE or ''
        has_error = (executor.hasKeyword('wupC1 error') or
                     executor.hasKeyword("Can't read block") or
                     executor.hasKeyword("Can't set magic"))
        # Positive detection: any of three known shapes carrying block 0
        # hex bytes.  re.MULTILINE so `^` anchors to per-line table rows
        # for the iceman v4.21611 ` 0 | HEX | ascii` form.
        has_block_data = bool(_re.search(
            r'(?:Block\s*0\s*:|data:|^\s*0\s*\|)\s*[A-Fa-f0-9 ]{16,}',
            text, _re.MULTILINE
        ))
        if has_block_data and not has_error:
            is_gen1a = True
            probe_conclusive = True
        elif has_error:
            # wupC1 error = definitive "not Gen1a" answer from THIS card.
            # Don't let a stale scan-cache flag (e.g. from an AutoCopy source
            # card) override the target probe below.
            probe_conclusive = True

    # Use infos gen1a flag only when the direct probe was inconclusive.
    # Without this guard an AutoCopy source's gen1a=True leaks into a
    # Gen2/CUID target and the flow tries cload — which requires the
    # wupC1 backdoor and fails with "Can't set magic card block: 0".
    if not probe_conclusive and infos.get('gen1a', False):
        is_gen1a = True

    # Step 3: Key recovery on TARGET card (standard + Gen2 paths)
    # The read-phase keys belong to the SOURCE card, and the TARGET card may
    # carry non-default keys the dictionary alone can't find.  Run the SAME
    # recovery pipeline reads use (fchk -> nested, darkside only if zero keys)
    # rather than fchk alone, so custom-keyed targets can be authenticated and
    # written.  Clear the map first so only TARGET keys are used.
    if not is_gen1a:
        typ = infos.get('type', 1)
        size = hfmfread.sizeGuess(typ)
        if hfmfkeys:
            hfmfkeys.KEYS_MAP.clear()
            hfmfkeys.keys(size, infos, listener)
            # Preflight: every sector must have at least one recovered key
            # (A or B) before we commit to writing.  A sector with zero keys
            # cannot be authenticated, so every block in it fails and the
            # writer would return -1 only after a partial write.  Abort up
            # front instead.  (A single key may still fail the trailer under a
            # default ACL, but that is ACL-dependent and cannot be known
            # without probing the target, so it is not gated here.)
            sc = mifare.getSectorCount(size) if mifare else 0
            if sc and any(not hfmfkeys.hasKeyA(s) and not hfmfkeys.hasKeyB(s)
                          for s in range(sc)):
                return -1

    # Step 4: Dispatch to write path
    file_path = bundle if isinstance(bundle, str) else ''

    if is_gen1a and not is_gen2:
        # Record the write-time path so verify() can detect Gen1a reliably
        # (a post-write cgetblk re-probe can false-negative once the card is
        # sealed by gen1afreeze()).  Cleared on the else branch so a stale
        # True can't leak across AutoCopy iterations into a Gen2/standard card.
        infos['gen1a_written'] = True
        result = write_with_gen1a(infos, file_path)
    else:
        infos['gen1a_written'] = False
        # Standard and Gen2 share one write path (write_with_standard records the
        # block counts verify_gen2 reads).
        result = write_with_standard(infos, file_path, listener)

    # Step 5: Post-write card check
    executor.startPM3Task('hf 14a info', 10000)
    executor.startPM3Task('hf mf cgetblk --blk 0', 10000)

    return result

# ---------------------------------------------------------------------------
# write — main entry point (called from write.py dispatcher)
# ---------------------------------------------------------------------------
def write(listener, infos, bundle):
    """Write MIFARE Classic data to a tag.

    Called from write.py dispatcher for types 0,1,25,26,40,41,42,43,44.

    Args:
        listener: callback receiving progress/result dicts
        infos:    dict from scan cache {'type', 'uid', 'sak', 'atqa', 'gen1a', ...}
        bundle:   str file path to .bin dump

    Returns:
        int: 1=success, -1=failure
    """
    try:
        # Fresh rework budget for this write — previous flows should not
        # pre-brick this one.
        try:
            executor.resetReworkCount()
        except AttributeError:
            pass
        return write_common(listener, infos, bundle)
    except Exception:
        return -1

# ---------------------------------------------------------------------------
# verify_gen2 — block count verify for Gen 2 / CUID cards
# ---------------------------------------------------------------------------
def verify_gen2(infos):
    """Verify Gen 2 / CUID write by checking all blocks returned Write ( ok ).

    write_with_standard stores 'write_block_count' and 'write_block_total' in
    infos during the write. verify_gen2 simply confirms the count matches
    the total — if all 64 blocks (1K) reported Write ( ok ) the write was
    successful. No PM3 commands needed.

    Returns 1 if count == total, -1 otherwise.
    """
    count = infos.get('write_block_count', 0)
    total = infos.get('write_block_total', 0)
    if total > 0 and count == total:
        return 1
    return -1


# ---------------------------------------------------------------------------
# _dump_uid — UID-bearing block 0 hex from the dump
# ---------------------------------------------------------------------------
def _dump_uid(infos, bundle):
    """Return block 0 hex (uppercase) from the dump, or '' if unavailable.

    Block 0 begins with the tag UID, so callers prefix-match the card's
    current UID against it.  Used by verify() to confirm a Gen2 clone
    actually took the source (dump) UID.
    """
    file = bundle if isinstance(bundle, str) else ''
    if not file:
        return ''
    try:
        b0 = read_blocks_4file(infos, file).get(0)
        if not b0:
            b0 = _read_block0_from_json(file)
        if b0 and len(b0) >= 8:
            return b0.upper()
    except Exception:
        pass
    return ''


# ---------------------------------------------------------------------------
# verify — read back and compare
# Trace: hf 14a info → hf mf cgetblk 0 (UID-level verify only)
# ---------------------------------------------------------------------------
def verify(infos, bundle):
    """Verify written card against source dump.

    Ground truth (trace_write_activity_attrs_20260402.txt line 225-231,
    QEMU original trace — no rdbl/rdsc commands after cgetblk 0):
        verify() issues hf 14a info twice — once as a pre-check and once
        to extract the UID for comparison — then hf mf cgetblk 0.
        No per-block comparison — original .so returns success if the
        card is present and UID matches.

    Gen 2 / CUID cards route to verify_gen2() — checks write_block_count
    vs write_block_total stored in infos by write_with_standard.

    Returns 1 on success, -1 on failure.
    """
    try:
        # Pre-check: card still on antenna
        ret = executor.startPM3Task('hf 14a info', 10000)
        if ret == -1:
            return -1

        # Card presence + UID check
        ret = executor.startPM3Task('hf 14a info', 10000)
        if ret == -1:
            return -1

        # Full info text (same source write_common uses) — needed for the
        # Gen2 magic re-detect below.
        text_14a = executor.CONTENT_OUT_IN__TXT_CACHE or ''

        # Extract UID from hf 14a info output
        card_uid = None
        content = executor.getPrintContent() if hasattr(executor, 'getPrintContent') else ''
        if not content:
            content = text_14a
        if not content:
            content = executor.getContentFromRegex(r'UID:\s*([\dA-Fa-f ]+)') or ''
        import re
        m = re.search(r'UID:\s*([\dA-Fa-f ]+)', content)
        if m:
            card_uid = m.group(1).replace(' ', '').upper()

        # Preflight Gen2 re-detect: the write-time probe can miss the magic
        # line, leaving infos['gen2'] unset and routing verify down the
        # standard (UID-unchanged) path.  Re-check the magic string from this
        # fresh info text so a Gen2 is verified as a Gen2 even if the flag
        # was missed at write time.
        is_gen2 = bool(infos.get('gen2', False)) or \
            ('Magic capabilities... Gen 2 / CUID' in text_14a)

        # Gen1a probe (matches original trace exactly)
        executor.startPM3Task('hf mf cgetblk --blk 0', 10000)
        gen1a_probe_text = executor.CONTENT_OUT_IN__TXT_CACHE or ''

        if is_gen2:
            # Gen2 verify: block-count check AND the card must now carry the
            # DUMP's UID.  A Gen2 uses the SAME write commands as standard;
            # the only post-write difference is that block 0 is writable via
            # --force, so a successful clone changes the UID to the source's.
            # BOTH must pass.
            if verify_gen2(infos) != 1:
                return -1
            dump_b0 = _dump_uid(infos, bundle)
            if card_uid and dump_b0 and (dump_b0.startswith(card_uid)
                                         or card_uid.startswith(dump_b0)):
                return 1
            return -1

        # Gen1a verify: cload rewrote block 0 via the backdoor (UID becomes the
        # dump's) and write_with_gen1a's csetuid then forced the block-0 SAK to
        # 08, so the card no longer matches the dump's block 0 — compare against
        # what we actually wrote (infos['gen1a_written_b0']), never the dump.  Prefer a full block-0 read-back from the cgetblk
        # probe above; fall back to a UID-prefix match if the backdoor doesn't
        # answer (e.g. a sealed card).  Detection uses the write-time flag, not
        # a re-probe, because gen1afreeze() can seal a UFUID card.
        if infos.get('gen1a_written', False):
            ref_b0 = (infos.get('gen1a_written_b0')
                      or _dump_uid(infos, bundle) or '').upper()
            if not ref_b0:
                return -1
            m_card_b0 = re.search(
                r'(?:Block\s*0\s*:|data:|^\s*0\s*\|)\s*([0-9A-Fa-f ]{32,})',
                gen1a_probe_text, re.MULTILINE)
            if m_card_b0:
                card_b0 = m_card_b0.group(1).replace(' ', '').upper()[:32]
                if card_b0 == ref_b0:
                    return 1
            if card_uid and (ref_b0.startswith(card_uid)
                             or card_uid.startswith(ref_b0)):
                return 1
            return -1

        # Standard path (unchanged): the UID does not change on a write, so
        # compare the card's UID against the pre-write target UID.
        expected_uid = (infos.get('uid') or '').upper()
        if card_uid and expected_uid and card_uid.startswith(expected_uid):
            return 1
        if card_uid and expected_uid and expected_uid.startswith(card_uid):
            return 1

        return -1

    except Exception:
        return -1
