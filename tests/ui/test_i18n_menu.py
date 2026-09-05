# -*- coding: utf-8 -*-
"""Menu-label translation via resources.tr (reverse English -> active lookup).

The main-menu items in actmain.py are hard-coded English display strings, so
they localize through resources.tr (which looks up by English *value*) rather
than get_str (which looks up by *key*).  Regression guard for: main-menu items
were not translated when the language was set to French.
"""

import resources
import pytest


# The static main-menu labels from actmain.MainActivity.MENU_ITEMS, plus the
# "Plugins" and "Settings" chrome entries appended at runtime.
MAIN_MENU_LABELS = [
    "Auto Copy", "Dump Files", "Scan Tag", "Read Tag", "Sniff TRF",
    "Simulation", "PC-Mode", "Diagnosis", "Backlight", "Volume", "About",
    "Erase Tag", "Time Settings", "LUA Script", "Plugins", "Settings",
]

# A sample of labels whose French differs from English, with exact expectations.
EXPECTED_FR = {
    "Auto Copy": "Copie automatique",
    "Read Tag": "Lire le tag",
    "About": "À propos",       # À propos
    "Settings": "Paramètres",   # Paramètres
    "Plugins": "Extensions",
    "Erase Tag": "Effacer le tag",
}


@pytest.fixture(autouse=True)
def _restore_language():
    before = resources.getLanguage()
    yield
    resources.setLanguage(before)


def test_tr_is_noop_in_english():
    resources.setLanguage("en")
    for label in MAIN_MENU_LABELS:
        assert resources.tr(label) == label


def test_tr_gives_exact_french_for_known_labels():
    resources.setLanguage("fr")
    for label, expected in EXPECTED_FR.items():
        assert resources.tr(label) == expected, (
            "%r -> %r, expected %r" % (label, resources.tr(label), expected)
        )


def test_every_menu_label_resolves_nonempty_and_mostly_translated():
    resources.setLanguage("fr")
    translated = 0
    for label in MAIN_MENU_LABELS:
        out = resources.tr(label)
        assert isinstance(out, str) and out, "%r produced empty output" % label
        if out != label:
            translated += 1
    # A few labels are legitimately identical across languages (Simulation,
    # Volume, PC-Mode); the large majority must actually translate.
    assert translated >= 12, "only %d/%d menu labels translated" % (
        translated, len(MAIN_MENU_LABELS)
    )


def test_tr_passes_unknown_and_non_string_through():
    resources.setLanguage("fr")
    assert resources.tr("SomeCommunityPluginName") == "SomeCommunityPluginName"
    assert resources.tr("") == ""
    assert resources.tr(None) is None
