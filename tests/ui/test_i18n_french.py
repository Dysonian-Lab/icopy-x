"""Validation tests for the French (fr) language pack and any future locale.

The french-content workstream produced data/lang/fr.json but could not return
its structured result, so its independent test step never ran. These tests
fill that gap: they pin French completeness and, more generally, assert that
every shipped language file fully covers the English key set so nothing falls
back silently to English by accident.
"""

import json
import os

import pytest

import resources


LANG_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "data", "lang"
)


def _load(code):
    with open(os.path.join(LANG_DIR, code + ".json"), encoding="utf-8") as f:
        return json.load(f)


def _keys(pack):
    """Set of (category, key) pairs, ignoring _meta fields."""
    out = set()
    for cat, val in pack.items():
        if cat.startswith("_") or not isinstance(val, dict):
            continue
        for k in val:
            out.add((cat, k))
    return out


@pytest.fixture(autouse=True)
def _restore_language():
    before = resources.getLanguage()
    yield
    resources.setLanguage(before)


def test_fr_file_is_valid_and_has_metadata():
    fr = _load("fr")
    assert fr.get("_name"), "fr.json must declare a _name"
    assert fr.get("_font"), "fr.json must declare a _font"


def test_fr_covers_every_english_key():
    en, fr = _load("en"), _load("fr")
    missing = _keys(en) - _keys(fr)
    assert not missing, "French is missing translations for: %s" % sorted(missing)


def test_fr_translations_are_actually_french():
    """A sample of keys must differ from English (i.e. be translated)."""
    resources.setLanguage("en")
    en_vals = {k: resources.get_str(k) for k in ("read_tag", "about", "settings")}
    resources.setLanguage("fr")
    for k, en_val in en_vals.items():
        fr_val = resources.get_str(k)
        assert fr_val and fr_val != en_val, "%r not translated to French" % k


def test_system_phrases_are_translated():
    fr = _load("fr")
    system = fr.get("system", {})
    for phrase in ("No card found", "Auth failed", "Reading..."):
        assert system.get(phrase), "system phrase %r not translated" % phrase


def test_language_label_present_in_all_packs():
    """The Settings 'Language' item needs a real label, not the raw key."""
    for code in ("en", "fr"):
        resources.setLanguage(code)
        label = resources.get_str("language")
        assert label and label != "language", (
            "%s.json must define a 'language' label" % code
        )


def test_every_listed_language_covers_english():
    """Future-proofing: any language offered in the menu must be complete."""
    en_keys = _keys(_load("en"))
    for lang in resources.list_languages():
        code = lang["code"]
        if code == "en":
            continue
        missing = en_keys - _keys(_load(code))
        assert not missing, "%s is incomplete: missing %s" % (code, sorted(missing))
