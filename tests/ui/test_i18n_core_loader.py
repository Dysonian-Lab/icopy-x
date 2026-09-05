# -*- coding: utf-8 -*-
"""Behavioural tests for the i18n core loader (workstream: core-loader).

Spec under test (src/lib/resources.py + data/lang/en.json):

  * UI strings load from ``data/lang/<code>.json`` (one file per language,
    keyed by the filename stem), not only from the hardcoded ``StringEN``.
  * ``data/lang/en.json`` is the extraction of every category dict from
    ``StringEN`` (title, button, toastmsg, tipsmsg, procbarmsg, itemmsg)
    into ``{_name, _font, <categories...>}`` plus an empty ``system`` category.
  * On import every ``data/lang/*.json`` is loaded into an in-memory registry
    keyed by code; ``StringEN`` remains a built-in fallback for missing files
    or absent keys.
  * ``get_str(keys)`` resolves against the ACTIVE language; a key missing in
    the active language falls back to the English value (never the raw key
    when English can resolve it); single-key and list/tuple behaviour kept.
  * Category iteration is generalised to ALL categories present in a file, so
    a new category such as ``system`` resolves (no fixed 6-name tuple).
  * ``setLanguage(code)`` takes a code string, with back-compat 0->en, 1->zh;
    ``getLanguage()`` returns the active code; ``list_languages()`` reports
    code/name/font discovered from ``data/lang/*.json``.
  * ``get_font(size)`` uses the active language ``_font`` (default mononoki),
    with existing EN/ZH behaviour intact.
  * Call signatures of get_str/get_font/setLanguage/getLanguage are unchanged.

These expectations are derived from the spec, not from the current
implementation: each test fails if the behaviour is wrong.
"""

import inspect
import json
import os

import pytest

import resources


# ---------------------------------------------------------------------------
# Locations / constants derived from the spec (independent of the module)
# ---------------------------------------------------------------------------

# tests/ui/test_i18n_core_loader.py -> repo root is three levels up.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_EN_JSON_PATH = os.path.join(_REPO_ROOT, "data", "lang", "en.json")

# The six string categories the spec says must be extracted from StringEN.
_EXPECTED_CATEGORIES = ("title", "button", "toastmsg", "tipsmsg", "procbarmsg", "itemmsg")

# Existing EN / ZH fonts whose behaviour the spec says to keep intact.
_EN_FONT = "mononoki"
_ZH_FONT = "文泉驿等宽正黑"  # 文泉驿等宽正黑


# ---------------------------------------------------------------------------
# Isolation: never let one test's language/registry mutation leak into another
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _restore_module_state():
    """Save and restore the mutable module globals around every test."""
    saved_lang = resources._current_language
    saved_registry = resources._LANG_REGISTRY
    try:
        yield
    finally:
        resources._current_language = saved_lang
        resources._LANG_REGISTRY = saved_registry


@pytest.fixture
def build_lang_dir(tmp_path, monkeypatch):
    """Return build(files) -> path.

    ``files`` maps a language code to the JSON-serialisable dict that becomes
    ``<code>.json`` in an isolated temp directory.  The loader is re-pointed at
    that directory (the same discovery hook the app uses to locate data/ files)
    and the in-memory registry is rebuilt via the public reload entry point.
    """
    def build(files):
        lang_dir = tmp_path / "lang"
        lang_dir.mkdir(exist_ok=True)
        for code, data in files.items():
            (lang_dir / (code + ".json")).write_text(
                json.dumps(data, ensure_ascii=False), encoding="utf-8"
            )
        monkeypatch.setattr(resources, "_find_lang_dir", lambda: str(lang_dir))
        resources.force_check_str_res()
        return str(lang_dir)

    return build


def _stringen_categories():
    """The category dicts declared on StringEN (name -> dict)."""
    cats = {}
    for name in vars(resources.StringEN):
        if name.startswith("_"):
            continue
        val = getattr(resources.StringEN, name)
        if isinstance(val, dict):
            cats[name] = val
    return cats


# ===========================================================================
# Group A — data/lang/en.json is the extraction of StringEN
# ===========================================================================

class TestEnJsonFile:
    def test_en_json_exists_and_is_valid_json(self):
        assert os.path.isfile(_EN_JSON_PATH), "data/lang/en.json must exist"
        with open(_EN_JSON_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        assert isinstance(data, dict)

    def _load(self):
        with open(_EN_JSON_PATH, "r", encoding="utf-8") as f:
            return json.load(f)

    def test_meta_name_and_font(self):
        data = self._load()
        assert data.get("_name") == "English"
        assert data.get("_font") == _EN_FONT

    def test_every_stringen_category_extracted_verbatim(self):
        """Each StringEN category dict must appear in en.json with equal content."""
        data = self._load()
        for name, expected in _stringen_categories().items():
            assert name in data, "en.json missing category %r" % name
            assert data[name] == expected, (
                "en.json category %r does not match StringEN" % name
            )

    def test_all_six_named_categories_present(self):
        data = self._load()
        for name in _EXPECTED_CATEGORIES:
            assert name in data and isinstance(data[name], dict)

    def test_empty_system_category_included(self):
        data = self._load()
        assert "system" in data, "en.json must include a 'system' category"
        assert data["system"] == {}, "system category must start empty"

    def test_no_unexpected_top_level_categories(self):
        """Category set is exactly the StringEN categories plus 'system'."""
        data = self._load()
        cats = {k for k, v in data.items() if not k.startswith("_") and isinstance(v, dict)}
        assert cats == set(_stringen_categories()) | {"system"}


# ===========================================================================
# Group B — registry loading & discovery
# ===========================================================================

class TestRegistryDiscovery:
    def test_lang_dir_resolves_under_app_root(self):
        """The loader locates data/lang the same way other data/ files are found."""
        found = resources._find_lang_dir()
        assert found is not None
        assert os.path.isdir(found)
        assert found.replace("\\", "/").endswith("data/lang")
        assert os.path.isfile(os.path.join(found, "en.json"))

    def test_import_time_registry_contains_en(self):
        langs = {entry["code"]: entry for entry in resources.list_languages()}
        assert "en" in langs
        assert langs["en"]["name"] == "English"
        assert langs["en"]["font"] == _EN_FONT

    def test_list_languages_reports_discovered_files(self, build_lang_dir):
        build_lang_dir({
            "en": {"_name": "English", "_font": _EN_FONT, "title": {"a": "A"}},
            "fr": {"_name": "Francais", "_font": "notosans", "title": {"a": "Ah"}},
        })
        by_code = {e["code"]: e for e in resources.list_languages()}
        assert set(by_code) == {"en", "fr"}
        assert by_code["fr"]["name"] == "Francais"
        assert by_code["fr"]["font"] == "notosans"

    def test_registry_keyed_by_filename_stem(self, build_lang_dir):
        """A file named <code>.json is discoverable under that exact code."""
        build_lang_dir({
            "en": {"_name": "English", "_font": _EN_FONT, "title": {"read_tag": "Read Tag"}},
            "de": {"_name": "Deutsch", "_font": _EN_FONT, "title": {"read_tag": "Tag Lesen"}},
        })
        codes = {e["code"] for e in resources.list_languages()}
        assert "de" in codes
        resources.setLanguage("de")
        assert resources.get_str("read_tag") == "Tag Lesen"

    def test_list_languages_font_defaults_to_mononoki(self, build_lang_dir):
        build_lang_dir({"xx": {"_name": "NoFont", "title": {}}})
        by_code = {e["code"]: e for e in resources.list_languages()}
        assert by_code["xx"]["font"] == _EN_FONT

    def test_list_languages_name_defaults_to_code(self, build_lang_dir):
        build_lang_dir({"yy": {"_font": _EN_FONT, "title": {}}})
        by_code = {e["code"]: e for e in resources.list_languages()}
        assert by_code["yy"]["name"] == "yy"


# ===========================================================================
# Group C — get_str resolution against the active language
# ===========================================================================

class TestGetStrResolution:
    def test_single_key_active_language(self, build_lang_dir):
        build_lang_dir({"en": {"_font": _EN_FONT, "title": {"read_tag": "Read Tag"}}})
        resources.setLanguage("en")
        assert resources.get_str("read_tag") == "Read Tag"

    def test_active_language_value_wins_over_english(self, build_lang_dir):
        build_lang_dir({
            "en": {"_font": _EN_FONT, "title": {"read_tag": "Read Tag"}},
            "fr": {"_font": "notosans", "title": {"read_tag": "Lire le tag"}},
        })
        resources.setLanguage("fr")
        assert resources.get_str("read_tag") == "Lire le tag"

    def test_missing_key_falls_back_to_english_not_raw_key(self, build_lang_dir):
        """A key absent from the active language resolves to the English value."""
        build_lang_dir({
            "en": {"_font": _EN_FONT, "title": {"write_tag": "Write Tag"}},
            "fr": {"_font": "notosans", "title": {"read_tag": "Lire le tag"}},
        })
        resources.setLanguage("fr")
        # 'write_tag' exists only in English -> must return the English value.
        assert resources.get_str("write_tag") == "Write Tag"

    def test_unknown_key_returns_raw_key(self, build_lang_dir):
        build_lang_dir({"en": {"_font": _EN_FONT, "title": {"read_tag": "Read Tag"}}})
        resources.setLanguage("en")
        assert resources.get_str("totally_unknown_key_xyz") == "totally_unknown_key_xyz"

    def test_list_input_returns_tuple_in_order(self, build_lang_dir):
        build_lang_dir({
            "en": {"_font": _EN_FONT, "title": {"read_tag": "Read Tag", "write_tag": "Write Tag"}},
        })
        resources.setLanguage("en")
        out = resources.get_str(["read_tag", "write_tag"])
        assert isinstance(out, tuple)
        assert out == ("Read Tag", "Write Tag")

    def test_tuple_input_returns_tuple(self, build_lang_dir):
        build_lang_dir({
            "en": {"_font": _EN_FONT, "title": {"read_tag": "Read Tag", "write_tag": "Write Tag"}},
        })
        resources.setLanguage("en")
        out = resources.get_str(("write_tag", "read_tag"))
        assert isinstance(out, tuple)
        assert out == ("Write Tag", "Read Tag")

    def test_list_input_mixes_translation_and_fallback(self, build_lang_dir):
        build_lang_dir({
            "en": {"_font": _EN_FONT, "title": {"read_tag": "Read Tag", "write_tag": "Write Tag"}},
            "fr": {"_font": "notosans", "title": {"read_tag": "Lire le tag"}},
        })
        resources.setLanguage("fr")
        assert resources.get_str(["read_tag", "write_tag"]) == ("Lire le tag", "Write Tag")


# ===========================================================================
# Group D — generalised category iteration (drop the fixed 6-name tuple)
# ===========================================================================

class TestGeneralisedCategories:
    def test_system_category_resolves_in_active_language(self, build_lang_dir):
        """A key in the 'system' category (a 7th category) must resolve."""
        build_lang_dir({
            "en": {"_font": _EN_FONT, "system": {"pm3_ready": "PM3 Ready"}},
        })
        resources.setLanguage("en")
        assert resources.get_str("pm3_ready") == "PM3 Ready"

    def test_system_category_falls_back_to_english(self, build_lang_dir):
        build_lang_dir({
            "en": {"_font": _EN_FONT, "system": {"pm3_ready": "PM3 Ready"}},
            "fr": {"_font": "notosans", "title": {"read_tag": "Lire le tag"}},
        })
        resources.setLanguage("fr")
        # 'pm3_ready' lives only in en's system category.
        assert resources.get_str("pm3_ready") == "PM3 Ready"

    def test_arbitrary_new_category_resolves(self, build_lang_dir):
        """Any dict category (name unknown to the old fixed list) is iterated."""
        build_lang_dir({
            "en": {"_font": _EN_FONT, "brandnewcat": {"greet": "Hello"}},
        })
        resources.setLanguage("en")
        assert resources.get_str("greet") == "Hello"


# ===========================================================================
# Group E — setLanguage / getLanguage (code strings + integer back-compat)
# ===========================================================================

class TestLanguageSelection:
    def test_set_and_get_code_string(self):
        resources.setLanguage("zh")
        assert resources.getLanguage() == "zh"
        resources.setLanguage("en")
        assert resources.getLanguage() == "en"

    def test_arbitrary_code_string_accepted(self):
        resources.setLanguage("fr")
        assert resources.getLanguage() == "fr"

    def test_integer_zero_maps_to_en(self):
        resources.setLanguage(0)
        assert resources.getLanguage() == "en"

    def test_integer_one_maps_to_zh(self):
        resources.setLanguage(1)
        assert resources.getLanguage() == "zh"


# ===========================================================================
# Group F — get_font uses the active language font
# ===========================================================================

class TestGetFont:
    def test_english_font_default_size(self):
        resources.setLanguage("en")
        assert resources.get_font() == "%s %d" % (_EN_FONT, 13)

    def test_english_font_explicit_size(self):
        resources.setLanguage("en")
        assert resources.get_font(20) == "%s %d" % (_EN_FONT, 20)

    def test_chinese_font_behaviour_intact(self):
        resources.setLanguage("zh")
        assert resources.get_font(13) == "%s %d" % (_ZH_FONT, 13)

    def test_font_taken_from_active_language_file(self, build_lang_dir):
        build_lang_dir({
            "en": {"_font": _EN_FONT, "title": {}},
            "fr": {"_font": "notosans", "title": {}},
        })
        resources.setLanguage("fr")
        assert resources.get_font(18) == "notosans 18"

    def test_font_defaults_to_mononoki_when_file_omits_it(self, build_lang_dir):
        build_lang_dir({"xx": {"_name": "NoFont", "title": {}}})
        resources.setLanguage("xx")
        assert resources.get_font(13) == "%s %d" % (_EN_FONT, 13)


# ===========================================================================
# Group G — StringEN remains a built-in fallback when files are missing
# ===========================================================================

class TestBuiltinFallback:
    def test_get_str_uses_stringen_when_no_files(self, tmp_path, monkeypatch):
        empty = tmp_path / "empty"
        empty.mkdir()
        monkeypatch.setattr(resources, "_find_lang_dir", lambda: str(empty))
        resources.force_check_str_res()
        resources.setLanguage("en")
        # No JSON files at all -> resolution must fall back to hardcoded StringEN.
        assert resources.get_str("read_tag") == resources.StringEN.title["read_tag"]
        assert resources.get_str("read_tag") == "Read Tag"

    def test_list_languages_empty_when_no_files(self, tmp_path, monkeypatch):
        empty = tmp_path / "empty"
        empty.mkdir()
        monkeypatch.setattr(resources, "_find_lang_dir", lambda: str(empty))
        resources.force_check_str_res()
        assert resources.list_languages() == []

    def test_absent_key_in_file_falls_back_to_builtin(self, build_lang_dir):
        """A file present but missing a key still resolves via StringEN."""
        build_lang_dir({"en": {"_font": _EN_FONT, "title": {"read_tag": "Read Tag"}}})
        resources.setLanguage("en")
        # 'auto_copy' is not in this minimal file but is in StringEN.
        assert resources.get_str("auto_copy") == resources.StringEN.title["auto_copy"]


# ===========================================================================
# Group H — call signatures are unchanged (hundreds of call sites depend on them)
# ===========================================================================

class TestCallSignatures:
    def test_get_str_signature(self):
        params = list(inspect.signature(resources.get_str).parameters)
        assert params == ["keys"]

    def test_get_font_signature_size_default_13(self):
        sig = inspect.signature(resources.get_font)
        assert "size" in sig.parameters
        assert sig.parameters["size"].default == 13
        # Callable with no args (default) and with a positional size.
        assert isinstance(resources.get_font(), str)
        assert isinstance(resources.get_font(10), str)

    def test_setlanguage_takes_single_positional(self):
        params = list(inspect.signature(resources.setLanguage).parameters)
        assert params == ["lang"]

    def test_getlanguage_takes_no_required_args(self):
        params = [
            p for p in inspect.signature(resources.getLanguage).parameters.values()
            if p.default is inspect.Parameter.empty
            and p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)
        ]
        assert params == []
        assert isinstance(resources.getLanguage(), str)

    def test_list_languages_callable_no_args(self):
        assert isinstance(resources.list_languages(), list)
