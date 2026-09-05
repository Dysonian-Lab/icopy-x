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

"""MIFARE Classic Recovery plugin (on-device, no PC mode).

Testbed for an adaptive fchk -> nested strategy, kept OUT of the main
middleware so it can be validated on hardware before anything in the
read flow is touched. It does NOT modify or import from the read
pipeline's control flow -- it only reuses the same leaf helpers
(hfmfkeys parsing, hfmfread read/save) so its dumps are byte-identical
to a normal read.

Strategy
--------
    1. `hf 14a info`  -> card present + size + UID/SAK/ATQA.
    2. fchk against the SD dictionary (hfmfkeys.fchks) -> KEYS_MAP.
    3. Count found vs total (32 for 1K):
         - all found          -> skip nested, go straight to read.
         - >= half found      -> TARGETED nested, one shot per missing
                                 (sector,keytype), seeded from a known
                                 key, with retries. Fast when only a few
                                 keys are missing (the common case).
         - 1..<half found     -> FULL one-shot nested from the best seed
                                 key (recovers every sector at once).
         - zero found         -> fall back to MANUAL seed entry, then
                                 full one-shot from that seed.
    4. hfmfread.readAllSector -> save_bin + save_json, exactly
       like nested_recovery_plugin (same dump dir, same layout).

Why targeted-per-key when we already have most keys
    The iceman full one-shot (`hf mf nested --1k --blk N -k KEY`) carries
    a firmware TODO ("single mode broken? can't find keys...",
    cmdhfmf.c:2001) and re-derives every sector. When only a handful of
    keys are missing, the targeted form
    (`... --blk N -k KEY --tblk T --ta`, cmdhfmf.c:2005) is faster and
    the one that reliably works in practice.

Runs entirely on the device via executor -- no PC mode, no host client.

Tunable
    _RETRY_TARGETED   attempts per missing key (nested is probabilistic).
    _TIMEOUT_*        per-command PM3 timeouts (ms).
    _BRACKET_KEY_RE   parses the targeted "found valid key [ XXXX ]" line.
"""

import os
import re
import time

# ---------------------------------------------------------------------------
# Size handling (mirrors nested_recovery_plugin for consistency)
# ---------------------------------------------------------------------------
_SIZE_FLAG = {'mini': '--mini', '1k': '--1k', '2k': '--2k', '4k': '--4k'}
_SIZE_MAX_BLOCK = {'mini': 19, '1k': 63, '2k': 127, '4k': 255}
_SIZE_LABEL = {'mini': 'MINI', '1k': '1K', '2k': '2K', '4k': '4K'}
_SIZE_CONST = {'mini': 320, '1k': 1024, '2k': 2048, '4k': 4096}
_TYPE_INT = {'mini': 25, '1k': 1, '2k': 26, '4k': 0}

# ---------------------------------------------------------------------------
# Tunables
# ---------------------------------------------------------------------------
_RETRY_TARGETED = 3          # attempts per missing key
_TIMEOUT_INFO = 20000
_TIMEOUT_NESTED_ONE = 45000  # one targeted (single-sector) nested
_TIMEOUT_NESTED_FULL = 300000  # full one-shot nested
# fchk ceiling. A full 2-strategy pass over ~2500 keys was 124 s on a PC
# client; the device's internal transport is slower, so give it real room
# to FINISH -- the heartbeat keeps the screen alive so a long run is visible
# rather than looking hung. If it reaches this ceiling with elapsed still
# climbing, that is a genuine stall, not just slowness. Tune as needed.
_TIMEOUT_FCHK = 800000

# Targeted nested prints e.g.:
#   "Target block 7 key type A -- found valid key [ 7BCE9E866751 ]"
# (mifarehost.c:686). Bracketed, IGNORECASE tolerates the capitalised
# darkside-style variant too.
_BRACKET_KEY_RE = re.compile(
    r'found valid key\s*\[\s*([0-9A-Fa-f]{12})\s*\]', re.IGNORECASE)


class MFCRecoveryPlugin(object):
    """Entry class for the MIFARE Classic Recovery plugin."""

    def __init__(self, host=None):
        self.host = host
        self._propagated = set()

    # ------------------------------------------------------------------
    # Init
    # ------------------------------------------------------------------
    def do_init(self):
        self.host.set_var('uid', '')
        self.host.set_var('sak', '08')
        self.host.set_var('atqa', '0004')
        self.host.set_var('size', '1k')
        self.host.set_var('size_label', '1K')
        self.host.set_var('max_block', '63')
        self.host.set_var('block_num', '0')
        self.host.set_var('selected_block', '0')
        self.host.set_var('key_type', 'A')
        self.host.set_var('seed_key', '')
        self.host.set_var('found_count', '0')
        self.host.set_var('total_count', '0')
        self.host.set_var('key_count', '0')
        self.host.set_var('strategy', '')
        self.host.set_var('mode', 'fast')
        self._propagated = set()
        self.host.set_var('elapsed', '0')
        self.host.set_var('dump_file', '')
        self.host.set_progress(0, '')
        return None

    # ------------------------------------------------------------------
    # Detect (identical approach to nested_recovery_plugin.do_detect)
    # ------------------------------------------------------------------
    def do_detect(self):
        try:
            import executor
        except ImportError:
            return {'status': 'error'}

        self.host.set_progress(8, 'Reading card...')
        if executor.startPM3Task('hf 14a info', _TIMEOUT_INFO) == -1:
            return {'status': 'nocard'}

        text = self._text()
        if not text:
            return {'status': 'nocard'}

        m = re.search(r'UID:\s*([0-9A-Fa-f ]+)', text)
        if not m:
            return {'status': 'nocard'}
        uid = re.sub(r'\s+', '', m.group(1)).upper()
        if not uid or len(uid) < 8:
            return {'status': 'nocard'}

        low = text.lower()
        if ('desfire' in low or 'ultralight' in low or 'ntag' in low
                or 'plus sl3' in low):
            return {'status': 'unsupported'}

        size = self._guess_size(text)
        if size is None:
            return {'status': 'unsupported'}

        ma = re.search(r'ATQA:\s*([0-9A-Fa-f ]+)', text)
        atqa = re.sub(r'\s+', '', ma.group(1)).upper()[:4] if ma else '0004'
        ms = re.search(r'SAK:\s*([0-9A-Fa-f]{2})', text)
        sak = ms.group(1).upper() if ms else '08'

        self.host.set_var('uid', uid)
        self.host.set_var('atqa', atqa)
        self.host.set_var('sak', sak)
        self.host.set_var('size', size)
        self.host.set_var('size_label', _SIZE_LABEL[size])
        self.host.set_var('max_block', str(_SIZE_MAX_BLOCK[size]))
        self.host.set_var('block_num', '0')
        return {'status': 'found'}

    def _guess_size(self, text):
        low = text.lower()
        if 'mini' in low:
            return 'mini'
        if '4k' in low:
            return '4k'
        if '2k' in low:
            return '2k'
        if '1k' in low:
            return '1k'
        m = re.search(r'SAK:\s*([0-9A-Fa-f]{2})', text)
        if m:
            sak = int(m.group(1), 16)
            if sak == 0x09:
                return 'mini'
            if sak == 0x18:
                return '4k'
            if sak in (0x08, 0x88, 0x28, 0x38):
                return '1k'
        return '1k'

    # ------------------------------------------------------------------
    # Mode select: the list menu picks a mode, all go through the shared
    # do_detect, then do_route fans out by mode.
    # ------------------------------------------------------------------
    def do_pick_fast(self):
        self.host.set_var('mode', 'fast')
        return {'status': 'picked'}

    def do_pick_manual(self):
        self.host.set_var('mode', 'manual')
        return {'status': 'picked'}

    def do_pick_fulldict(self):
        self.host.set_var('mode', 'fulldict')
        return {'status': 'picked'}

    def do_route(self):
        """Fan out to the chosen recovery mode after detection succeeds."""
        mode = self.host.get_var('mode', 'fast')
        if mode == 'manual':
            return {'status': 'manual'}
        if mode == 'fulldict':
            return {'status': 'fulldict'}
        return {'status': 'fast'}

    # ------------------------------------------------------------------
    # Fast Nested: cheap DEFAULT_KEYS seed probe -> full/targeted nested ->
    # save learned keys -> read + save. Skips the full-dictionary crunch;
    # for cards whose missing keys are not in the dictionary this reaches
    # nested in seconds instead of after a multi-minute doomed chk.
    # ------------------------------------------------------------------
    def do_fast_recover(self):
        try:
            import executor
            import hfmfkeys
            import mifare
        except ImportError:
            return {'status': 'error'}

        size = self.host.get_var('size', '1k')
        size_const = _SIZE_CONST.get(size, 1024)
        size_flag = _SIZE_FLAG.get(size, '--1k')
        total = mifare.getSectorCount(size_const) * 2
        self.host.set_var('total_count', str(total))
        self._propagated = set()

        t0 = time.time()

        # Cheap seed: quick chk against the small hardcoded DEFAULT_KEYS set
        # (~61 keys) rather than the full ~2500-key dictionary.
        hfmfkeys.KEYS_MAP.clear()
        seed_file = hfmfkeys.genKeyFile('', list(hfmfkeys.DEFAULT_KEYS))
        self.host.set_progress(15, 'Seed probe (default keys)...')
        if executor.startPM3Task('hf mf chk %s -f %s' % (size_flag, seed_file),
                                 _TIMEOUT_FCHK, rework_max=0) == -1:
            return {'status': 'error'}
        hfmfkeys.keysFromPrintParse(size_const)

        found = self._found_count(hfmfkeys, mifare, size_const)
        self.host.set_var('found_count', str(found))
        self.host.set_var('key_count', str(found))
        self.host.set_var('strategy', 'dict')
        if found == 0:
            # No default key anywhere -> nothing to seed nested with.
            return {'status': 'seedfail'}

        # Nested from the seed, exactly like the smart path (same helpers).
        if not hfmfkeys.hasAllKeys(size_const):
            seed = self._best_seed(hfmfkeys, mifare, size_const)
            if seed is None:
                return {'status': 'seedfail'}
            if found * 2 >= total:
                self.host.set_var('strategy', 'fast-targeted')
                self._recover_targeted(hfmfkeys, mifare, size_const, seed)
            else:
                self.host.set_var('strategy', 'fast-full')
                self._recover_full(hfmfkeys, size, size_const, seed)

        # Persist recovered keys to the user dic (deduped by the helper).
        try:
            hfmfkeys.saveLearnedKeys()
        except Exception:
            pass

        result = self._read_and_save(size_const)
        if result is None:
            return {'status': 'error'}
        self.host.set_var('elapsed', str(int(time.time() - t0)))
        return {'status': 'done'}

    # ------------------------------------------------------------------
    # Full Dict: chk against combined (user + DEFAULT_KEYS) PREPENDED to the
    # full iceman dic, then the shared nested tail. The only mode that touches
    # the ~2500-key iceman dic. Known-good keys are tried first so problem
    # cards have a better chance of completing before the long tail. Slow by
    # nature (can be minutes) -- this is the deliberate "grind it" option.
    # ------------------------------------------------------------------
    def _full_dict_file(self, hfmfkeys):
        """Build a temp dic: user keys, then DEFAULT_KEYS, then the iceman full
        dic -- de-duplicated, order preserved. Combined keys first = fast hits
        up front. Returns the temp path (falls back to combined-only if the
        iceman dic is absent).
        """
        ordered = []
        seen = set()

        def _add(keys):
            for k in keys:
                u = k.upper()
                if u and u not in seen:
                    seen.add(u)
                    ordered.append(u)

        # 1. user learned keys (fastest, card-specific)
        try:
            if os.path.exists(hfmfkeys._USER_DIC):
                _add(hfmfkeys.read_keys_of_file(hfmfkeys._USER_DIC))
        except Exception:
            pass
        # 2. hardcoded defaults
        _add(hfmfkeys.DEFAULT_KEYS)
        # 3. full iceman dic (the big grind, tried last)
        try:
            if os.path.exists(hfmfkeys._SD_DIC):
                _add(hfmfkeys.read_keys_of_file(hfmfkeys._SD_DIC))
        except Exception:
            pass

        path = '/tmp/.keys/mfc_fulldict.dic'
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, 'w') as f:
                f.write('\n'.join(ordered) + '\n')
        except (OSError, IOError):
            # Fall back to the middleware's combined file if the temp write
            # fails -- still better than nothing, just without the iceman tail.
            return hfmfkeys.genKeyFile('', list(hfmfkeys.DEFAULT_KEYS))
        return path

    def do_fulldict_recover(self):
        try:
            import executor
            import hfmfkeys
            import mifare
        except ImportError:
            return {'status': 'error'}

        size = self.host.get_var('size', '1k')
        size_const = _SIZE_CONST.get(size, 1024)
        size_flag = _SIZE_FLAG.get(size, '--1k')
        total = mifare.getSectorCount(size_const) * 2
        self.host.set_var('total_count', str(total))
        self._propagated = set()

        t0 = time.time()

        hfmfkeys.KEYS_MAP.clear()
        dict_file = self._full_dict_file(hfmfkeys)
        self.host.set_progress(15, 'Full dict (this can be slow)...')
        if executor.startPM3Task('hf mf chk %s -f %s' % (size_flag, dict_file),
                                 _TIMEOUT_FCHK, rework_max=0) == -1:
            return {'status': 'error'}
        hfmfkeys.keysFromPrintParse(size_const)

        found = self._found_count(hfmfkeys, mifare, size_const)
        self.host.set_var('found_count', str(found))
        self.host.set_var('key_count', str(found))
        self.host.set_var('strategy', 'dict')
        if found == 0:
            return {'status': 'seedfail'}

        # Shared nested tail: same helpers as Fast.
        if not hfmfkeys.hasAllKeys(size_const):
            seed = self._best_seed(hfmfkeys, mifare, size_const)
            if seed is None:
                return {'status': 'seedfail'}
            if found * 2 >= total:
                self.host.set_var('strategy', 'fulldict-targeted')
                self._recover_targeted(hfmfkeys, mifare, size_const, seed)
            else:
                self.host.set_var('strategy', 'fulldict-full')
                self._recover_full(hfmfkeys, size, size_const, seed)

        try:
            hfmfkeys.saveLearnedKeys()
        except Exception:
            pass

        result = self._read_and_save(size_const)
        if result is None:
            return {'status': 'error'}
        self.host.set_var('elapsed', str(int(time.time() - t0)))
        return {'status': 'done'}

    def _recover_targeted(self, hfmfkeys, mifare, size_const, seed):
        """One targeted nested per missing (sector, keytype), with retries."""
        sc = mifare.getSectorCount(size_const)
        missing = []
        for sector in range(sc):
            if not hfmfkeys.getKey4Map(sector, 'A'):
                missing.append((sector, 'A'))
            if not hfmfkeys.getKey4Map(sector, 'B'):
                missing.append((sector, 'B'))

        done = 0
        for (sector, ktype) in missing:
            # Propagation from an earlier iteration may have already solved
            # this (sector, keytype) — skip it if so.
            if hfmfkeys.getKey4Map(sector, ktype):
                continue
            done += 1
            self.host.set_progress(
                25 + int(done * 40 / max(len(missing), 1)),
                'Nested %d/%d (S%d %s)' % (done, len(missing), sector, ktype))
            self._targeted_nested(hfmfkeys, mifare, seed, sector, ktype)

    def _recover_full(self, hfmfkeys, size, size_const, seed):
        """One full-card nested sweep from the seed key."""
        try:
            import executor
        except ImportError:
            return
        known_sector, known_type, known_key = seed
        import mifare
        kb = mifare.sectorToBlock(known_sector)
        a_flag = '-a' if known_type == 'A' else '-b'
        size_flag = _SIZE_FLAG.get(size, '--1k')
        self.host.set_progress(35, 'Full nested sweep...')
        cmd = 'hf mf nested %s --blk %d %s -k %s' % (
            size_flag, kb, a_flag, known_key)
        if executor.startPM3Task(cmd, _TIMEOUT_NESTED_FULL) == -1:
            return
        # Full nested prints the same "Sec|Blk|keyA|res|keyB|res" table as
        # fchk, so the validated parser populates KEYS_MAP for us.
        hfmfkeys.keysFromPrintParse(size_const)
        # Fan each recovered key across all sectors: reuse is common, so this
        # can complete sectors the single sweep missed.
        for k in sorted(set(v.upper() for v in hfmfkeys.KEYS_MAP.values() if v)):
            self._propagate(hfmfkeys, mifare, k)

    def _targeted_nested(self, hfmfkeys, mifare, seed, target_sector,
                         target_type):
        """`hf mf nested --blk KB -a|-b -k KEY --tblk TB --ta|--tb`.

        Returns True if the key was recovered and stored.
        """
        try:
            import executor
        except ImportError:
            return False
        known_sector, known_type, known_key = seed
        kb = mifare.sectorToBlock(known_sector)
        tb = mifare.sectorToBlock(target_sector)
        a_flag = '-a' if known_type == 'A' else '-b'
        t_flag = '--ta' if target_type == 'A' else '--tb'
        cmd = 'hf mf nested --blk %d %s -k %s --tblk %d %s' % (
            kb, a_flag, known_key, tb, t_flag)

        for _ in range(_RETRY_TARGETED):
            if executor.startPM3Task(cmd, _TIMEOUT_NESTED_ONE) == -1:
                continue
            m = _BRACKET_KEY_RE.search(self._text())
            if m:
                key = m.group(1).upper()
                hfmfkeys.putKey2Map(target_sector, target_type, key)
                self._propagate(hfmfkeys, mifare, key)
                return True
        return False

    def _propagate(self, hfmfkeys, mifare, key):
        """Fan a freshly-recovered key across ALL sectors (both A and B).

        Keys are frequently reused across sectors, so a single cheap
        `hf mf chk -k <key>` (one call tests the key as A and B on every
        sector) often unlocks more sectors than nested has reached yet,
        shrinking the remaining work. Best-effort: any failure is swallowed
        and recovery continues. Each distinct key is propagated at most once
        per run.
        """
        if not key or key in self._propagated:
            return
        self._propagated.add(key)
        try:
            import executor
        except ImportError:
            return
        size = self.host.get_var('size', '1k')
        size_flag = _SIZE_FLAG.get(size, '--1k')
        size_const = _SIZE_CONST.get(size, 1024)
        try:
            if executor.startPM3Task('hf mf chk %s -k %s' % (size_flag, key),
                                     _TIMEOUT_FCHK, rework_max=0) == -1:
                return
            hfmfkeys.keysFromPrintParse(size_const)
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Manual fallback (only reached when fchk found ZERO keys)
    # ------------------------------------------------------------------
    def _block_max(self):
        return int(self.host.get_var('max_block', '63'))

    def do_cycle_up(self):
        span = self._block_max() + 1
        cur = int(self.host.get_var('block_num', '0'))
        self.host.set_var('block_num', str((cur + 1) % span))
        self.host.update_screen()
        return None

    def do_cycle_down(self):
        span = self._block_max() + 1
        cur = int(self.host.get_var('block_num', '0'))
        self.host.set_var('block_num', str((cur - 1) % span))
        self.host.update_screen()
        return None

    def do_confirm_block(self):
        self.host.set_var('selected_block', self.host.get_var('block_num', '0'))
        return {'status': 'confirmed'}

    def do_pick_key_a(self):
        self.host.set_var('key_type', 'A')
        return {'status': 'picked'}

    def do_pick_key_b(self):
        self.host.set_var('key_type', 'B')
        return {'status': 'picked'}

    def do_capture_key(self):
        key = self.host.get_input().strip().upper()
        if len(key) != 12:
            return {'status': 'error'}
        try:
            int(key, 16)
        except ValueError:
            return {'status': 'error'}
        self.host.set_var('seed_key', key)
        return {'status': 'captured'}

    def do_recover_manual(self):
        """Full one-shot nested from the manually entered seed, then read."""
        try:
            import executor
            import hfmfkeys
            import mifare  # noqa: F401
        except ImportError:
            return {'status': 'error'}

        uid = self.host.get_var('uid', '')
        size = self.host.get_var('size', '1k')
        size_const = _SIZE_CONST.get(size, 1024)
        blk = self.host.get_var('selected_block', '0')
        ktype = self.host.get_var('key_type', 'A')
        key = self.host.get_var('seed_key', '')
        size_flag = _SIZE_FLAG.get(size, '--1k')
        a_flag = '-a' if ktype == 'A' else '-b'

        if len(key) != 12 or not uid:
            return {'status': 'error'}

        t0 = time.time()
        self.host.set_var('strategy', 'manual')
        self.host.set_progress(30, 'Full nested sweep...')
        cmd = 'hf mf nested %s --blk %s %s -k %s' % (
            size_flag, blk, a_flag, key)
        if executor.startPM3Task(cmd, _TIMEOUT_NESTED_FULL) == -1:
            return {'status': 'error'}

        hfmfkeys.KEYS_MAP.clear()
        hfmfkeys.keysFromPrintParse(size_const)
        if not hfmfkeys.getAnyKey():
            return {'status': 'seedfail'}

        # Fan each recovered key across all sectors (reuse is common), then
        # persist to the user dic — same tail as Fast/Darkside.
        self._propagated = set()
        import mifare as _mifare
        for k in sorted(set(v.upper() for v in hfmfkeys.KEYS_MAP.values() if v)):
            self._propagate(hfmfkeys, _mifare, k)
        try:
            hfmfkeys.saveLearnedKeys()
        except Exception:
            pass

        result = self._read_and_save(size_const)
        if result is None:
            return {'status': 'error'}
        self.host.set_var('elapsed', str(int(time.time() - t0)))
        return {'status': 'done'}

    # ------------------------------------------------------------------
    # Shared read + save (same as nested_recovery_plugin)
    # ------------------------------------------------------------------
    def _read_and_save(self, size_const):
        """readAllSector -> save_bin + save_json. Returns bin path."""
        try:
            import hfmfkeys
            import hfmfread
            import mifare
        except ImportError:
            return None

        infos = self._build_infos(self.host.get_var('uid', ''),
                                  self.host.get_var('size', '1k'))
        found = self._found_count(hfmfkeys, mifare, size_const)
        self.host.set_var('key_count', str(found))

        self.host.set_progress(70, 'Reading blocks...')
        data_list = hfmfread.readAllSector(size_const, infos, None)
        if not data_list:
            return None

        self.host.set_progress(90, 'Saving dump...')
        bin_path = hfmfread.save_bin(infos, data_list)
        if not bin_path or not os.path.exists(bin_path):
            return None
        base = bin_path[:-4] if bin_path.endswith('.bin') else bin_path

        try:
            json_path = hfmfread.save_json(infos, data_list)
            if json_path and json_path != base + '.json':
                os.replace(json_path, base + '.json')
        except Exception:
            pass

        self.host.set_var('dump_file', os.path.basename(bin_path))
        self.host.set_progress(100, 'Done')
        return bin_path

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _text(self):
        try:
            import executor
        except ImportError:
            return ''
        txt = getattr(executor, 'CONTENT_OUT_IN__TXT_CACHE', None)
        if txt:
            return txt
        getter = getattr(executor, 'getPrintContent', None)
        if callable(getter):
            try:
                return getter() or ''
            except Exception:
                return ''
        return ''

    def _found_count(self, hfmfkeys, mifare, size_const):
        sc = mifare.getSectorCount(size_const)
        n = 0
        for sector in range(sc):
            if hfmfkeys.getKey4Map(sector, 'A'):
                n += 1
            if hfmfkeys.getKey4Map(sector, 'B'):
                n += 1
        return n

    def _best_seed(self, hfmfkeys, mifare, size_const):
        """Lowest-sector known key -> (sector, type, key). None if no keys."""
        sc = mifare.getSectorCount(size_const)
        for sector in range(sc):
            ka = hfmfkeys.getKey4Map(sector, 'A')
            if ka:
                return (sector, 'A', ka)
            kb = hfmfkeys.getKey4Map(sector, 'B')
            if kb:
                return (sector, 'B', kb)
        return None

    def _build_infos(self, uid, size):
        return {
            'uid': uid,
            'len': len(uid) // 2,
            'type': _TYPE_INT.get(size, 1),
            'sak': self.host.get_var('sak', '08'),
            'atqa': self.host.get_var('atqa', '0004'),
        }
