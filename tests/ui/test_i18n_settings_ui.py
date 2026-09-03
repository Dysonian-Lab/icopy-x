# -*- coding: utf-8 -*-
"""Behavioural tests for the i18n Settings UI (workstream: settings-ui).

Spec under test (derived from the workstream spec, NOT from the current
implementation — every test fails if the behaviour is wrong):

  * ``src/middleware/settings.py`` gains ``getLanguage()`` / ``setLanguage(code)``
    that mirror ``getScreenMirror`` / ``setScreenMirror``: the config key is
    ``language``, the default is ``en``, and ``setLanguage`` persists through
    ``config.setKeyValue('language', code)``.

  * ``data/conf.ini`` declares ``language = en`` under ``[DEFAULT]``.

  * The Settings activity in ``src/lib/activity_main.py`` grows a Language item
    ALONGSIDE the existing Screen Mirroring item, following the same
    draw/interaction pattern.  The item shows the active language, and
    selecting it opens a picker over ``resources.list_languages()`` that marks
    the active language.  Choosing a language applies it live
    (``resources.setLanguage(code)``) and persists it (config key ``language``),
    then the Settings UI re-renders so its labels appear in the new language.

  * ``src/lib/application.startApp()`` reads ``config.getValue('language', 'en')``
    and calls ``resources.setLanguage(code)`` BEFORE the first screen builds, so
    the saved language applies at boot.

These expectations come from the spec.  The Settings activity class name is
resolved dynamically (``SettingsActivity`` or ``SettingsMenuActivity``) so the
tests bind to the behaviour rather than a chosen identifier.
"""

import configparser
import inspect
import os
import sys
import types

import pytest

import actstack
import resources
from tests.ui.conftest import MockCanvas
from _constants import KEY_UP, KEY_DOWN, KEY_OK, KEY_PWR


# ---------------------------------------------------------------------------
# Locations derived from the spec (independent of the implementation)
# ---------------------------------------------------------------------------

# tests/ui/test_i18n_settings_ui.py -> repo root is three levels up.
_REPO_ROOT = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_CONF_INI_PATH = os.path.join(_REPO_ROOT, "data", "conf.ini")


# ---------------------------------------------------------------------------
# Isolation: never let one test's language state leak into another
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _restore_active_language():
    """Snapshot/restore the module-level active language."""
    saved = resources.getLanguage()
    yield
    resources.setLanguage(saved)


# ---------------------------------------------------------------------------
# Fake config module — in-memory, records reads and writes
# ---------------------------------------------------------------------------

class FakeConfig:
    """In-memory stand-in for the ``config`` module.

    Mirrors config.getValue(key, default) / config.setKeyValue(key, value)
    semantics: a missing key returns the supplied default.
    """

    def __init__(self, initial=None):
        self.store = dict(initial or {})
        self.reads = []          # list of keys passed to getValue
        self.writes = []         # list of (key, value) passed to setKeyValue

    def getValue(self, key, default=None):
        self.reads.append(key)
        if key in self.store:
            return self.store[key]
        return default

    def setKeyValue(self, key, value):
        self.writes.append((key, value))
        self.store[key] = str(value)


def _settings_activity_cls():
    """Resolve the Settings menu activity class from activity_main.

    The spec calls it ``SettingsActivity``; the implementation may use
    ``SettingsMenuActivity``.  Either satisfies the behaviour under test.
    """
    import activity_main
    for name in ("SettingsActivity", "SettingsMenuActivity"):
        cls = getattr(activity_main, name, None)
        if cls is not None:
            return cls
    raise AssertionError(
        "activity_main exposes no Settings activity "
        "(looked for SettingsActivity / SettingsMenuActivity)")


def _lang_names():
    """{code: display-name} for the installed languages."""
    return {e["code"]: e["name"] for e in resources.list_languages()}


# ===========================================================================
# Group A — settings.py: getLanguage() / setLanguage(code)
# ===========================================================================

class TestSettingsMiddleware:
    """settings.getLanguage / settings.setLanguage mirror the mirror pair."""

    @pytest.fixture
    def settings_mod(self, monkeypatch):
        """Import the real settings module with config redirected in-memory."""
        import settings as settings_mod
        fake = FakeConfig()
        monkeypatch.setattr(settings_mod, "config", fake)
        return settings_mod, fake

    def test_getLanguage_reads_config_language_key(self, settings_mod):
        settings, fake = settings_mod
        fake.store["language"] = "fr"
        assert settings.getLanguage() == "fr"
        assert "language" in fake.reads

    def test_getLanguage_defaults_to_en(self, settings_mod):
        settings, fake = settings_mod
        # No 'language' key present at all.
        assert "language" not in fake.store
        assert settings.getLanguage() == "en"

    def test_setLanguage_persists_through_config(self, settings_mod):
        settings, fake = settings_mod
        settings.setLanguage("fr")
        # Persisted under the 'language' key via config.setKeyValue.
        assert ("language", "fr") in [(k, str(v)) for k, v in fake.writes]
        assert fake.store.get("language") == "fr"

    def test_language_round_trips(self, settings_mod):
        settings, fake = settings_mod
        settings.setLanguage("fr")
        assert settings.getLanguage() == "fr"
        settings.setLanguage("en")
        assert settings.getLanguage() == "en"

    def test_signatures_present(self):
        import settings
        assert callable(settings.getLanguage)
        assert callable(settings.setLanguage)
        # setLanguage takes a single language-code argument.
        params = inspect.signature(settings.setLanguage).parameters
        required = [p for p in params.values()
                    if p.default is inspect.Parameter.empty
                    and p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)]
        assert len(required) == 1


# ===========================================================================
# Group B — data/conf.ini default
# ===========================================================================

class TestConfIniDefault:
    """conf.ini declares language = en under [DEFAULT]."""

    def test_conf_ini_exists(self):
        assert os.path.isfile(_CONF_INI_PATH), _CONF_INI_PATH

    def test_default_language_is_en(self):
        cp = configparser.ConfigParser()
        cp.read(_CONF_INI_PATH)
        # configparser exposes [DEFAULT] via .defaults().
        defaults = cp.defaults()
        assert "language" in defaults, \
            "conf.ini [DEFAULT] is missing the 'language' key"
        assert defaults["language"].strip() == "en"


# ===========================================================================
# Settings-activity harness
# ===========================================================================

class _ActivityEnv:
    """Bundle of the in-memory config + real settings module for a test."""

    def __init__(self, config, settings_mod):
        self.config = config
        self.settings = settings_mod

    @property
    def store(self):
        return self.config.store


@pytest.fixture
def act_env(monkeypatch):
    """Wire a fresh actstack + MockCanvas factory and in-memory settings/config.

    The real ``settings`` module is used but its ``config`` reference is
    redirected to an in-memory store, so the activity persists language and
    mirror state observably and without touching the on-disk conf.ini.
    """
    actstack._reset()
    actstack._canvas_factory = lambda: MockCanvas()

    import settings as settings_mod
    fake = FakeConfig({"language": "en", "screen_mirror": "0"})
    monkeypatch.setattr(settings_mod, "config", fake)
    monkeypatch.setitem(sys.modules, "settings", settings_mod)

    resources.setLanguage("en")

    yield _ActivityEnv(fake, settings_mod)

    actstack._reset()


def _start_settings(act_env):
    """Start a fresh Settings activity with the active language reset to en."""
    act_env.config.store["language"] = "en"
    resources.setLanguage("en")
    return actstack.start_activity(_settings_activity_cls())


def _reach_language_picker(act_env, active="en"):
    """Open the Settings menu and select the Language item.

    Scans downward through the rows (without assuming a fixed row index),
    pressing OK on each candidate.  The Language item is the one whose OK
    pushes a NEW activity (a picker) rather than toggling in place.

    *active* is the language that is active/persisted when the picker opens.

    Returns (settings_activity, picker_activity).  Fails if no row opens a
    picker that lists the installed languages.
    """
    settings_cls = _settings_activity_cls()
    names = set(_lang_names().values())
    last_reason = "no rows tried"

    for down in range(8):
        actstack._reset()
        actstack._canvas_factory = lambda: MockCanvas()
        # Reset persisted + active state for a clean attempt.
        act_env.config.store["language"] = active
        act_env.config.store["screen_mirror"] = "0"
        resources.setLanguage(active)

        settings_act = actstack.start_activity(settings_cls)
        for _ in range(down):
            settings_act.onKeyEvent(KEY_DOWN)
        settings_act.onKeyEvent(KEY_OK)

        top = actstack.get_current_activity()
        if top is not settings_act:
            picker_texts = set(top.getCanvas().get_all_text())
            # The picker must list the installed language names.
            if names & picker_texts:
                return settings_act, top
            last_reason = ("row %d opened an activity that did not list "
                           "languages: %r" % (down, sorted(picker_texts)))

    raise AssertionError(
        "No Settings row opened a language picker listing %r (%s)"
        % (sorted(names), last_reason))


# ===========================================================================
# Group C — Settings menu grows a Language item alongside Screen Mirroring
# ===========================================================================

class TestSettingsMenuLanguageItem:

    def test_screen_mirror_item_still_present(self, act_env):
        """The Language item is added ALONGSIDE Screen Mirroring, not replacing it."""
        act = _start_settings(act_env)
        texts = act.getCanvas().get_all_text()
        mirror_label = resources.get_str("screen_mirroring")
        assert mirror_label in texts

    def test_active_language_name_shown(self, act_env):
        """The Language row displays the active language's human name."""
        act = _start_settings(act_env)
        texts = act.getCanvas().get_all_text()
        en_name = _lang_names()["en"]  # 'English'
        assert en_name in texts

    def test_language_item_opens_picker(self, act_env):
        """Selecting the Language item opens a picker of the installed languages."""
        _settings_act, picker = _reach_language_picker(act_env)
        picker_texts = set(picker.getCanvas().get_all_text())
        names = _lang_names()
        assert names["en"] in picker_texts
        assert names["fr"] in picker_texts


# ===========================================================================
# Group D — LanguageActivity: pick, mark active, apply, persist, re-render
# ===========================================================================

class TestLanguagePicker:

    def _select_code(self, picker, code):
        """Drive the picker's selection onto *code* and confirm with OK."""
        idx = picker._codes.index(code)
        # CheckedListView.selection() reports the highlighted row.
        guard = 0
        while picker._listview.selection() < idx and guard < 16:
            picker.onKeyEvent(KEY_DOWN)
            guard += 1
        while picker._listview.selection() > idx and guard < 32:
            picker.onKeyEvent(KEY_UP)
            guard += 1
        assert picker._listview.selection() == idx
        picker.onKeyEvent(KEY_OK)

    def test_active_language_is_marked(self, act_env):
        """The picker marks whichever language is active (fr, not just row 0)."""
        _settings_act, picker = _reach_language_picker(act_env, active="fr")
        fr_idx = picker._codes.index("fr")
        en_idx = picker._codes.index("en")
        assert fr_idx != en_idx
        checked = set(picker._listview.getCheckPosition())
        # The active language (fr) is marked; the inactive one (en) is not.
        assert fr_idx in checked
        assert en_idx not in checked

    def test_choose_applies_language_live(self, act_env):
        """Choosing 'fr' calls resources.setLanguage so the UI is live-French."""
        _settings_act, picker = _reach_language_picker(act_env)
        self._select_code(picker, "fr")
        assert resources.getLanguage() == "fr"

    def test_choose_persists_language(self, act_env):
        """Choosing 'fr' persists it under config key 'language'."""
        _settings_act, picker = _reach_language_picker(act_env)
        self._select_code(picker, "fr")
        assert act_env.store.get("language") == "fr"

    def test_settings_relabels_after_choice(self, act_env):
        """Returning to Settings re-renders labels in the chosen language."""
        settings_act, picker = _reach_language_picker(act_env)
        self._select_code(picker, "fr")

        # The picker finished; Settings is top-of-stack again and re-rendered.
        current = actstack.get_current_activity()
        assert current is settings_act

        texts = set(current.getCanvas().get_all_text())
        names = _lang_names()
        # The Language row now shows the French name...
        assert names["fr"] in texts
        # ...and the stale English name is gone (labels genuinely re-rendered).
        assert names["en"] not in texts

    def test_cancel_leaves_language_unchanged(self, act_env):
        """PWR out of the picker changes nothing (no apply, no persist)."""
        _settings_act, picker = _reach_language_picker(act_env)
        assert resources.getLanguage() == "en"
        picker.onKeyEvent(KEY_PWR)
        assert resources.getLanguage() == "en"
        assert act_env.store.get("language") == "en"


# ===========================================================================
# Group E — application.startApp applies the saved language at boot
# ===========================================================================

class _FakeRoot:
    """Minimal tkinter.Tk replacement: records nothing, blocks on nothing."""

    class _Tk:
        def call(self, *a, **k):
            pass

    def __init__(self):
        self.tk = self._Tk()

    def geometry(self, *a, **k):
        pass

    def resizable(self, *a, **k):
        pass

    def option_add(self, *a, **k):
        pass

    def configure(self, *a, **k):
        pass

    def after(self, *a, **k):
        return "after#0"

    def mainloop(self):
        pass


class TestStartAppBootLanguage:

    def test_saved_language_applied_before_first_screen(self, monkeypatch):
        """startApp reads config.getValue('language', ...) and calls
        resources.setLanguage(code) BEFORE the first screen is started."""
        import application
        import tkinter

        order = []

        # config returns a non-default saved language.
        fake_config = FakeConfig({"language": "fr"})
        monkeypatch.setitem(sys.modules, "config", fake_config)

        # Record when the language is applied.
        real_setlang = resources.setLanguage

        def rec_setlang(code):
            order.append(("setLanguage", code))
            return real_setlang(code)
        monkeypatch.setattr(resources, "setLanguage", rec_setlang)

        # Intercept the first-screen build. actstack.start_activity is later
        # rebound by startApp itself; setattr to the current value first so
        # monkeypatch restores the genuine function on teardown.
        monkeypatch.setattr(actstack, "start_activity", actstack.start_activity)
        monkeypatch.setattr(actstack, "finish_activity", actstack.finish_activity)

        def rec_start(cls, bundle=None):
            order.append(("start_activity", getattr(cls, "__name__", cls)))
            return None
        monkeypatch.setattr(actstack, "start_activity", rec_start)

        # Headless Tk.
        monkeypatch.setattr(tkinter, "Tk", lambda *a, **k: _FakeRoot())

        application.startApp()

        # The language must have been applied...
        applied = [c for (evt, c) in order if evt == "setLanguage"]
        assert applied, "startApp never called resources.setLanguage"

        # ...with the value read from config (not a hardcoded default)...
        assert "language" in fake_config.reads, \
            "startApp did not read config.getValue('language', ...)"
        assert applied[-1] == "fr", \
            "startApp applied %r, expected the saved 'fr'" % (applied[-1],)

        # ...and BEFORE the first screen was started.
        events = [evt for (evt, _c) in order]
        assert "start_activity" in events, "startApp never built a screen"
        assert events.index("setLanguage") < events.index("start_activity"), \
            "language was applied AFTER the first screen built: %r" % (order,)
