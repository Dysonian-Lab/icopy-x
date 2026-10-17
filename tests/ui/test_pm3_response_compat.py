"""Public PM3 response contracts after the Phase 4 compatibility inversion.

Factory responses become Iceman-shaped; Iceman responses stay unchanged.
The previous tests imported removed helpers and asserted the old direction.
Literal examples follow tests/phase4_inversion/test_legacy_path.py (source
citations) and tools/ground_truth/phase6_response_audit.md. Recorded Iceman
responses from the previous tests are preserved in the adjacent fixtures.
"""
import json
from pathlib import Path

import pytest

import executor
import pm3_compat


@pytest.fixture(autouse=True)
def factory_mode(monkeypatch):
    monkeypatch.setattr(pm3_compat, '_current_version', pm3_compat.PM3_VERSION_ORIGINAL)
    monkeypatch.setattr(pm3_compat, 'LEGACY_COMPAT', True)


DATA = '00 11 22 33 44 55 66 77 88 99 AA BB CC DD EE FF'
HID = 'HID Prox - 2006ec0c86 (1603)'
RESPONSE_CASES = [
    ('hf mf wrbl', 'isOk:01', 'Write ( ok )'),
    ('hf mf wrbl', 'isOk:00', 'Write ( fail )'),
    ('hf mf restore', '1 | isOk:01\n2 | isOk:00', '1 | Write ( ok )\n2 | Write ( fail )'),
    ('hf mf rdbl', '  7 | ' + DATA, 'data: ' + DATA),
    ('hf mf rdsc', '  0 | ' + DATA + '\n  1 | ' + DATA, 'data: ' + DATA + '\ndata: ' + DATA),
    ('hf mf darkside', 'found valid key: 000000000041', 'Found valid key [ 000000000041 ]'),
    ('hf mf nested', 'found valid key [ 48 45 58 41 43 54 ]', 'found valid key [ 484558414354 ]'),
    ('hf mfu restore', 'Finish restore', 'Done!'),
    ('hf 15 restore', 'done\n', 'done\n\nDone!'),
    ('hf 15 csetuid', 'setting new UID (ok)', 'Setting new UID ( ok )'),
    ('hf 15 csetuid', 'setting new UID (failed)', 'Setting new UID ( fail )'),
    ('hf 15 csetuid', "can't read card UID", 'no tag found'),
    ('hf iclass wrbl', 'Wrote block 07 successful', 'Wrote block   7 / 0x07 ( ok )'),
    ('hf iclass wrbl', 'Wrote block   6/0x06 successful', 'Wrote block   6 / 0x06 ( ok )'),
    ('hf iclass rdbl', ' block 07 : 12 FF 00 11 22 33 44 55', ' block   7/0x07 : 12 FF 00 11 22 33 44 55'),
    ('hf iclass rdbl', 'block 0A : 12 FF 00 11 22 33 44 55', 'block  10/0x0A : 12 FF 00 11 22 33 44 55'),
    ('hf felica reader', 'IDm  01 02 03 04 05 06 07 08', 'IDm: 01 02 03 04 05 06 07 08'),
    ('lf sea', 'EM TAG ID      : 0100000058', 'EM 410x ID 0100000058'),
    ('lf search', 'Chipset detection: T55xx', 'Chipset... T55xx'),
    ('lf em 410x reader', 'EM TAG ID : AABBCCDDEE', 'EM 410x ID AABBCCDDEE'),
    ('lf em 410x_read', 'EM TAG ID : AABBCCDDEE', 'EM 410x ID AABBCCDDEE'),
    ('lf sea', 'Animal ID:     0060-030207938416', 'Animal ID........... 0060-030207938416'),
    ('lf fdxb reader', 'Animal ID   0060-030207938416\nRaw   25 02 69 60', 'Animal ID........... 0060-030207938416\nRaw: 25 02 69 60'),
    ('lf hid reader', HID, HID + '\nraw: 2006ec0c86\n'),
    ('lf hid read', HID, HID + '\nraw: 2006ec0c86\n'),
    ('lf sea', HID, HID + '\nraw: 2006ec0c86\n'),
    ('lf t55xx detect', 'Chip Type      : T55x7\nBlock0         : 0x000880E0', 'Chip type......... T55x7\nBlock0............ 000880E0'),
    ('lf t55xx detect', 'Modulation     : ASK\nPassword Set   : Yes\nPassword       : ABCD1234', 'Modulation........ ASK\nPassword set...... Yes\nPassword.......... ABCD1234'),
    ('lf t55xx dump', 'Chip Type      : T55x7\nsaved 64 bytes to binary file dump.bin', 'Chip type......... T55x7\nSaved 64 bytes to binary file dump.bin'),
    ('lf t55xx chk', 'Found valid password: 51243648', 'Found valid password: [ 51243648 ]'),
    ('lf em 4x05 info', 'Chip Type: 9 | EM4305\nSerial #: 01234567\nConfigWord: 00080040 (Word 4)', 'Chip type..... EM4305\nSerialno...... 01234567\nBlock0........ 00080040'),
    ('lf em 4x05_info', 'ConfigWord: 00080040 (Word 4)', 'Block0........ 00080040'),
    ('lf em 4x05 dump', 'ConfigWord: 00080040 (Word 4)\nsaved 128 bytes to binary file dump.bin', 'Block0........ 00080040\nsaved 128 bytes to binary file dump.bin'),
    ('lf em 4x05_dump', 'Serial #: AABBCCDD', 'Serialno...... AABBCCDD'),
    ('hf 14a info', 'Prng detection: weak\nStatic nonce: yes\nMagic capabilities : Gen 1a', 'Prng detection..... weak\nStatic nonce....... yes\nMagic capabilities... Gen 1a'),
    ('hf iclass calcnewkey', 'Xor div key : ABCDEF0123456789', 'Xor div key.... ABCDEF0123456789'),
    ('hf search', 'Valid ISO15693 tag found', 'Valid ISO 15693 tag found'),
    ('hf sea', 'UID: E0 04 01 3C F9 43 B3 02\nTYPE: NXP\n\nValid ISO15693 tag found', 'UID.... E0 04 01 3C F9 43 B3 02\nTYPE: NXP\n\nValid ISO 15693 tag found'),
]


@pytest.mark.parametrize('command,factory,canonical', RESPONSE_CASES)
def test_factory_response_contract(command, factory, canonical):
    assert pm3_compat.translate_response(factory, command) == canonical


@pytest.mark.parametrize('command,factory,canonical', RESPONSE_CASES)
def test_canonical_response_is_idempotent(command, factory, canonical):
    assert pm3_compat.translate_response(canonical, command) == canonical


@pytest.mark.parametrize('version', [pm3_compat.PM3_VERSION_ICEMAN, None])
@pytest.mark.parametrize('command,factory,canonical', RESPONSE_CASES)
def test_non_factory_version_never_rewrites_responses(monkeypatch, version, command, factory, canonical):
    monkeypatch.setattr(pm3_compat, '_current_version', version)
    assert pm3_compat.translate_response(factory, command) == factory
    assert pm3_compat.translate_response(canonical, command) == canonical


@pytest.mark.parametrize('command,factory,canonical', RESPONSE_CASES)
def test_disabled_compatibility_is_inert(monkeypatch, command, factory, canonical):
    monkeypatch.setattr(pm3_compat, 'LEGACY_COMPAT', False)
    assert pm3_compat.translate_response(factory, command) == factory


@pytest.mark.parametrize('command,text', [
    ('hf mf wrbl', 'isOk:02'), ('hf mf wrbl', 'Writing failed'),
    ('hf mf rdbl', '7 | AA BB CC DD'), ('hf mf rdbl', "Can't read block. error=-1"),
    ('hf mf darkside', 'found valid key: bad'), ('hf mf nested', 'No valid key found'),
    ('hf mfu restore', 'Restore failed'), ('hf 15 restore', 'restore failed. Too many retries.'),
    ('hf 15 restore', 'undone'), ('hf iclass wrbl', 'Write failed'),
    ('hf iclass rdbl', 'block ZZ : 00 11 22 33 44 55 66 77'),
    ('hf iclass rdbl', 'Authentication failed'), ('lf t55xx chk', 'Found valid password: bad'),
    ('lf t55xx chk', 'Password not found'), ('lf hid reader', 'HID Prox - invalid'),
    ('lf sea', 'No known 125/134 kHz tags found!'),
    ('hf felica reader', 'Timeout while waiting for reply'),
    ('hw version', 'isOk:01'), ('hw version', 'EM TAG ID : 0100000058'),
])
def test_unrelated_and_failure_output_is_preserved(command, text):
    assert pm3_compat.translate_response(text, command) == text


@pytest.mark.parametrize('command', ['hf mf fchk', 'hf mf chk', 'hf mf staticnested'])
def test_fchk_tables_are_no_longer_rewritten(command):
    # hfmfkeys accepts both shapes; do not restore the deleted reverse adapter.
    for text in ('| 000 | ffffffffffff | 1 | aabbccddeeff | 1 |\n',
                 '000 | 003 | FFFFFFFFFFFF | 1 | AABBCCDDEEFF | 1\n'):
        assert pm3_compat.translate_response(text, command) == text


@pytest.mark.parametrize('text', [None, '', '\n\n'])
@pytest.mark.parametrize('version', [None, pm3_compat.PM3_VERSION_ORIGINAL, pm3_compat.PM3_VERSION_ICEMAN])
def test_empty_inputs(monkeypatch, text, version):
    monkeypatch.setattr(pm3_compat, '_current_version', version)
    assert pm3_compat.translate_response(text, 'hf 14a info') == text


def test_command_whitespace_and_arguments():
    assert pm3_compat.translate_response('isOk:01', '  hf mf wrbl 7 A FFFFFFFFFFFF 00  ') == 'Write ( ok )'


def test_generic_normalization_without_command():
    assert pm3_compat.translate_response('Prng detection: weak') == 'Prng detection..... weak'
    assert pm3_compat.translate_response('isOk:01') == 'isOk:01'


def test_hid_existing_raw_frame_is_not_duplicated():
    text = HID + '\nraw: 2006ec0c86\n'
    assert pm3_compat.translate_response(text, 'lf sea') == text


def test_iso15693_uid_rewrite_requires_context():
    text = 'UID: AA BB CC DD\nATQA: 00 04\nValid ISO14443-A tag found'
    result = pm3_compat.translate_response(text, 'hf sea')
    assert 'UID: AA BB CC DD' in result
    assert 'UID....' not in result


@pytest.mark.parametrize('prefix', ['[+]', '[=]', '[#]', '[!!]', '[-]', '[/]', '[\\]', '[|]'])
def test_executor_owns_prefix_cleanup(prefix):
    assert executor._clean_pm3_output(prefix + ' payload\n') == 'payload\n'


def test_executor_cleanup_preserves_protocol_fields_and_errors():
    raw = ('[usb|script] pm3 --> hf 14a info\n'
           '[=] ---------- ISO14443-A Information ----------\n[=]\n'
           '[+] \x1b[32mUID: AA BB CC DD ( ONUID, re-used )\x1b[0m\n'
           '[+] Prng detection..... weak\n[!!] Authentication failed\npm3 -->\n')
    assert executor._clean_pm3_output(raw) == (
        'UID: AA BB CC DD ( ONUID, re-used )\n'
        'Prng detection..... weak\nAuthentication failed\n')


@pytest.mark.parametrize('version', [pm3_compat.PM3_VERSION_ORIGINAL, pm3_compat.PM3_VERSION_ICEMAN])
def test_cleanup_and_compatibility_have_separate_roles(monkeypatch, version):
    monkeypatch.setattr(pm3_compat, '_current_version', version)
    clean = executor._clean_pm3_output('[+] isOk:00\npm3 -->\n')
    expected = 'Write ( fail )\n' if version == pm3_compat.PM3_VERSION_ORIGINAL else 'isOk:00\n'
    assert pm3_compat.translate_response(clean, 'hf mf wrbl') == expected


def test_preserved_real_iceman_traces(monkeypatch):
    path = Path(__file__).parent / 'fixtures' / 'pm3_iceman_responses.json'
    traces = json.loads(path.read_text(encoding='utf-8'))['responses']
    monkeypatch.setattr(pm3_compat, '_current_version', pm3_compat.PM3_VERSION_ICEMAN)
    for name, command in [('HF14A_INFO_NEW', 'hf 14a info'), ('FCHK_NEW', 'hf mf fchk'),
                          ('CGETBLK_NEW_FAIL', 'hf mf cgetblk')]:
        clean = executor._clean_pm3_output(traces[name])
        assert pm3_compat.translate_response(clean, command) == clean
        assert 'pm3 -->' not in clean
    info = executor._clean_pm3_output(traces['HF14A_INFO_NEW'])
    assert 'Prng detection..... weak' in info
    assert 'ONUID, re-used' in info
    table = executor._clean_pm3_output(traces['FCHK_NEW'])
    assert '4A6352684677' in table
    assert 'Sec | Blk' in table
