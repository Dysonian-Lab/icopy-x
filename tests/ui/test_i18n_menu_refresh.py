# -*- coding: utf-8 -*-
"""The root main menu re-localizes its cached labels when the language changes.

The main menu is the root activity — created once, only ever resumed — so its
ListView labels are cached. A language switch made in Settings must be picked
up when the menu is next resumed, without a reboot, and without losing the
highlighted row. Regression guard for: switching French -> English left the
menu in French.
"""

import pytest

import actstack
import resources
from tests.ui.conftest import MockCanvas

import actmain


@pytest.fixture(autouse=True)
def _env():
    actstack._reset()
    actstack._canvas_factory = lambda: MockCanvas()
    before = resources.getLanguage()
    yield
    resources.setLanguage(before)
    actstack._reset()


def _start_in(lang):
    resources.setLanguage(lang)
    return actstack.start_activity(actmain.MainActivity)


def test_menu_relocalizes_en_to_fr_on_resume():
    act = _start_in("en")
    assert act.lv_main_page._items[0] == "Auto Copy"

    resources.setLanguage("fr")
    act.onResume()

    items = act.lv_main_page._items
    assert items[0] == "Copie automatique"
    assert "Lire le tag" in items
    assert "Auto Copy" not in items


def test_menu_relocalizes_fr_to_en_on_resume():
    act = _start_in("fr")
    assert act.lv_main_page._items[0] == "Copie automatique"

    resources.setLanguage("en")
    act.onResume()

    items = act.lv_main_page._items
    assert items[0] == "Auto Copy"
    assert "Copie automatique" not in items


def test_selection_preserved_across_relocalize():
    act = _start_in("en")
    last = len(act.lv_main_page._items) - 1
    act.lv_main_page.setSelection(last)

    resources.setLanguage("fr")
    act.onResume()

    assert act.lv_main_page.selection() == last


def test_onresume_is_noop_when_language_unchanged():
    act = _start_in("fr")
    before = list(act.lv_main_page._items)

    act.onResume()  # same language — must not rebuild or change anything

    assert act.lv_main_page._items == before
    assert act._labels_lang == "fr"
