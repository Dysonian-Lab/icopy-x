"""Card Notes plugin — note store, ui.json wiring, text input, delegation."""

import json
import os

import pytest

import actstack
from _constants import (
    KEY_UP, KEY_DOWN, KEY_LEFT, KEY_RIGHT, KEY_OK, KEY_M1, KEY_M2,
    TAG_BTN_RIGHT,
)
from lib import card_notes as store
from plugin_activity import PluginActivity
from plugin_loader import lint_ui_json, load_plugin_class
from tests.ui.conftest import MockCanvas

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PLUGIN_DIR = os.path.join(REPO, 'plugins', 'card_notes')


def _load():
    cls, err = load_plugin_class(PLUGIN_DIR, 'CardNotesPlugin')
    assert cls is not None, err
    ui, _warnings = lint_ui_json(os.path.join(PLUGIN_DIR, 'ui.json'),
                                 PLUGIN_DIR, activity_class=cls)
    assert ui is not None
    return cls, ui


class FakeHost(object):
    def __init__(self):
        self._screens = {'list': {'screen': {'content': {'type': 'list',
                                                         'items': []}}}}
        self._list_state = {}
        self.vars = {}
        self.toasts = []
        self.input = ''

    def set_var(self, key, value):
        self.vars[key] = value

    def get_var(self, key, default=None):
        return self.vars.get(key, default)

    def tr(self, text):
        return text

    def show_toast(self, text, timeout=None, icon=None):
        self.toasts.append(text)

    def get_input(self):
        return self.input


@pytest.fixture
def env(monkeypatch, tmp_path):
    actstack._reset()
    actstack._canvas_factory = lambda: MockCanvas()
    root = tmp_path / 'dump'
    (root / 'mf1').mkdir(parents=True)
    (root / 'mfu').mkdir(parents=True)
    (root / 'mf1' / 'M1-1K-4B_DAEFB416_1.bin').write_bytes(b'\x00' * 1024)
    (root / 'mfu' / 'NTAG213_1D32320E950000_1.bin').write_bytes(b'\x00' * 236)
    monkeypatch.setenv('CARD_NOTES_DUMP_DIR', str(root))
    monkeypatch.setenv('CARD_NOTES_FILE', str(tmp_path / 'card_notes.json'))
    yield {'root': root, 'notes': tmp_path / 'card_notes.json'}
    actstack._reset()


def _plugin():
    cls, _ui = _load()
    host = FakeHost()
    return cls(host), host


def _items(host):
    return [i['label'] for i in
            host._screens['list']['screen']['content']['items']]


# ----------------------------------------------------------------------
# Shared store (lib.card_notes) — the interface other plugins consume
# ----------------------------------------------------------------------

def test_store_name_uid():
    assert store.name_uid('M1-1K-4B_DAEFB416_1') == 'DAEFB416'
    assert store.name_uid('M1-1K-4B_DAEFB416_1.bin') == 'DAEFB416'
    assert store.name_uid('NTAG213_1D32320E950000_1') == '1D32320E950000'
    assert store.name_uid('EM410x-ID_0000BC614E_1') == '0000BC614E'
    assert store.name_uid('T55xx_00148040_00000000_00000000_1') == '00148040'
    assert store.name_uid('random') == ''


def test_store_roundtrip(tmp_path):
    path = str(tmp_path / 'n.json')
    notes = store.load(path)
    store.set_note(notes, 'mf1', 'DAEFB416', 'Flat')
    store.save(notes, path)
    assert store.lookup('mf1', 'daefb416', path=path) == 'Flat'
    assert store.lookup('mfu', 'DAEFB416', path=path) == ''
    store.clear_note(notes, 'mf1', 'DAEFB416')
    store.save(notes, path)
    assert store.lookup('mf1', 'DAEFB416', path=path) == ''


def test_store_missing_or_bad_file_is_empty(tmp_path):
    assert store.load(str(tmp_path / 'nope.json')) == {}
    bad = tmp_path / 'bad.json'
    bad.write_text('{not json', encoding='utf-8')
    assert store.load(str(bad)) == {}
    assert store.lookup('mf1', 'X', path=str(bad)) == ''


# ----------------------------------------------------------------------
# Note store / plugin methods
# ----------------------------------------------------------------------

def test_load_lists_cards_deduped_by_uid(env):
    plugin, host = _plugin()
    plugin.load()
    assert plugin._keys == ['mfu:1D32320E950000', 'mf1:DAEFB416']
    assert all('(no note)' in x for x in _items(host))


def test_edit_save_keys_by_uid_and_reflects_in_list(env):
    plugin, host = _plugin()
    plugin.load()
    host._list_state['list'] = {'selected': plugin._keys.index('mf1:DAEFB416')}
    assert plugin.edit()['status'] == 'edit'
    assert host.vars['edit_note'] == ''
    host.input = 'Flat door'
    assert plugin.save()['status'] == 'saved'
    doc = json.loads(env['notes'].read_text(encoding='utf-8'))
    assert doc['notes']['mf1:DAEFB416']['note'] == 'Flat door'
    plugin.load()
    assert any('Flat door' in x for x in _items(host))


def test_save_empty_removes_note(env):
    plugin, host = _plugin()
    plugin.load()
    host._list_state['list'] = {'selected': plugin._keys.index('mf1:DAEFB416')}
    plugin.edit()
    host.input = 'Temp'
    plugin.save()
    plugin.edit()
    host.input = '   '
    plugin.save()
    doc = json.loads(env['notes'].read_text(encoding='utf-8'))
    assert 'mf1:DAEFB416' not in doc['notes']


def test_clear_removes_selected_note(env):
    plugin, host = _plugin()
    plugin.load()
    key = 'mf1:DAEFB416'
    host._list_state['list'] = {'selected': plugin._keys.index(key)}
    plugin.edit()
    host.input = 'Temp'
    plugin.save()
    host._list_state['list'] = {'selected': plugin._keys.index(key)}
    assert plugin.clear()['status'] == 'cleared'
    doc = json.loads(env['notes'].read_text(encoding='utf-8'))
    assert key not in doc['notes']


def test_note_follows_reread_of_same_card(env):
    (env['root'] / 'mfu' / 'NTAG213_1D32320E950000_2.bin').write_bytes(b'\x00' * 236)
    plugin, host = _plugin()
    plugin.load()
    assert plugin._keys == ['mfu:1D32320E950000', 'mf1:DAEFB416']


def test_empty_dump_dir_shows_placeholder(env):
    import shutil
    shutil.rmtree(str(env['root']))
    plugin, host = _plugin()
    plugin.load()
    assert 'No cards found' in _items(host)[0]


# ----------------------------------------------------------------------
# ui.json has no ui.json-less custom activity, so it must load as a normal
# plugin; the editor uses the framework input_text field.
# ----------------------------------------------------------------------

def test_plugin_is_a_plain_ui_plugin(env):
    cls, ui = _load()
    states = ui['states']
    assert states['edit']['screen']['content']['type'] == 'input_text'
    assert states['edit']['screen']['keys']['M1'] == 'input:delete'
    assert states['edit']['screen']['keys']['M2'] == 'input:charset'
    assert states['list']['screen']['keys']['M2'] == 'run:edit'


# ----------------------------------------------------------------------
# Framework: input_text field + input:delete / input:charset actions
# ----------------------------------------------------------------------

INPUT_UI = {
    'initial_state': 'edit',
    'states': {
        'edit': {
            'screen': {
                'title': 'Note',
                'content': {'type': 'input_text', 'length': 16,
                            'value': '{note}'},
                'buttons': {'left': 'Del', 'right': 'ABC'},
                'keys': {'M1': 'input:delete', 'M2': 'input:charset'},
            },
        },
    },
}


def _start_input(bundle_extra=None):
    bundle = {'ui_definition': INPUT_UI, 'entry_class': None,
              'manifest': {'name': 'T'}, 'translations': {}}
    bundle.update(bundle_extra or {})
    return actstack.start_activity(PluginActivity, bundle)


def test_input_text_delete_clears_current_cell(env):
    act = _start_input()
    widget = act._input_widget
    assert widget is not None
    assert widget.getCharsetName() == 'ABC'
    widget.setValue('AB')
    widget.setFocus(1)
    act.callKeyEvent(KEY_M1)            # input:delete, no re-render
    assert widget.getValue().strip() == 'A'
    assert widget.getFocus() == 1
    assert act.get_input().strip() == 'A'


def _right_button_text(act):
    items = act.getCanvas().find_withtag(TAG_BTN_RIGHT)
    return act.getCanvas().itemcget(items[0], 'text') if items else None


def test_input_text_charset_cycle(env):
    act = _start_input()
    widget = act._input_widget
    widget.setValue('A')
    widget.setFocus(0)
    assert _right_button_text(act) == 'ABC'
    act.callKeyEvent(KEY_M2)            # ABC -> abc
    assert widget.getCharsetName() == 'abc'
    assert widget.getValue()[0] == 'a'
    assert _right_button_text(act) == 'abc'   # button label follows
    widget.rollUp()                     # a -> b
    assert widget.getValue()[0] == 'b'
    act.callKeyEvent(KEY_M2)            # abc -> 123
    assert widget.getCharsetName() == '123'
    assert _right_button_text(act) == '123'
    act.callKeyEvent(KEY_M2)            # 123 -> !@#
    assert widget.getCharsetName() == '!@#'
    assert _right_button_text(act) == '!@#'
    act.callKeyEvent(KEY_M2)            # !@# -> ABC (wrap)
    assert widget.getCharsetName() == 'ABC'
    assert _right_button_text(act) == 'ABC'


def test_input_text_initial_value_from_state(env):
    act = _start_input()
    act.set_var('note', 'Hi')
    act._render_current_screen()
    assert act.get_input().strip() == 'Hi'
