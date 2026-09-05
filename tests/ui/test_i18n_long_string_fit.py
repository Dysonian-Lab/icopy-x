# -*- coding: utf-8 -*-
"""Behavioural tests for long-string fitting (workstream: long-string-fit).

Spec under test (src/lib/widget.py + src/lib/actbase.py):

  * A pure, unit-testable fit helper lives in ``src/lib/widget.py``:

        fit_text(text, max_px, base_font_size, min_font_size, measure_fn)
            -> (fitted_text, font_size)

    Longer-than-English strings (French translations, proxmark/system
    text) overflow the fixed title and button zones on the 240px display.
    The helper copes as follows:

      1. If the text already fits in ``max_px`` at ``base_font_size``,
         return it unchanged.
      2. Otherwise shrink the font one point at a time toward
         ``min_font_size`` and return the LARGEST size that still fits.
      3. If it is STILL too wide at ``min_font_size``, ellipsis-truncate
         from the end using a SINGLE-character ellipsis until it fits.
         Truncation is the last resort — it happens only after shrinking.

    The pixel measurement is injected as ``measure_fn(text, size) -> int``
    so the helper is testable without a live Tk canvas.

  * The helper is applied where titles and left/right buttons render so
    long labels stop overflowing.  In ``src/lib/actbase.py`` those are
    drawn by ``setTitle`` / ``setLeftButton`` / ``setRightButton``; the
    fitting is applied there using the real ``tkinter.font.Font.measure``
    and the actual on-screen geometry, so a drawn label never exceeds the
    space budgeted for its zone.

  * Toasts and list rows are explicitly OUT of scope (they already
    wrap/shrink/scroll) and are not touched here.

These expectations are derived from the spec, not from the current
implementation: each test fails if the behaviour is wrong.
"""

import unittest.mock as mock

import pytest

import widget


# ---------------------------------------------------------------------------
# Measurement doubles — injectable, deterministic, size-sensitive.
# ---------------------------------------------------------------------------

def _linear(text, size):
    """A width model where every glyph is ``size`` pixels wide.

    Width is proportional to BOTH the character count and the font size,
    which lets each expectation be computed exactly and makes shrinking a
    genuinely effective way to reduce width (as it is with a real font).
    """
    return len(text) * size


# ===========================================================================
# Group A — the pure fit_text helper: presence & contract
# ===========================================================================

class TestFitTextContract:
    def test_helper_exists_and_is_callable(self):
        assert hasattr(widget, "fit_text")
        assert callable(widget.fit_text)

    def test_returns_text_and_size_pair(self):
        out = widget.fit_text("Read", 200, 16, 12, _linear)
        assert isinstance(out, tuple) and len(out) == 2
        fitted, size = out
        assert isinstance(fitted, str)
        assert isinstance(size, int)

    def test_measurement_is_injectable_and_consulted(self):
        """The helper must decide using the injected measure_fn only."""
        calls = []

        def recording(text, size):
            calls.append((text, size))
            return _linear(text, size)

        widget.fit_text("A" * 12, 100, 16, 10, recording)
        assert calls, "measure_fn was never consulted"
        # It must never probe a size outside the [floor, base] band.
        sizes = [s for _, s in calls]
        assert min(sizes) >= 10
        assert max(sizes) <= 16

    def test_outcome_is_governed_by_the_measure_fn(self):
        """Same text/params, different measure_fn -> different result."""
        text = "Menu Principal"
        fits_everything = widget.fit_text(text, 300, 16, 10, lambda t, s: 1)
        fits_nothing = widget.fit_text(text, 300, 16, 10, lambda t, s: len(t) * 100)
        # When everything fits, the label is returned verbatim at base size.
        assert fits_everything == (text, 16)
        # When the model reports huge widths, the label cannot survive intact.
        assert fits_nothing != (text, 16)


# ===========================================================================
# Group B — step 1: text that already fits is returned unchanged
# ===========================================================================

class TestFitsUnchanged:
    def test_short_text_kept_at_base_size(self):
        fitted, size = widget.fit_text("OK", 200, 16, 12, _linear)
        assert fitted == "OK"
        assert size == 16

    def test_fitting_text_is_not_ellipsized(self):
        fitted, _ = widget.fit_text("Read Tag", 240, 16, 12, _linear)
        assert widget.TEXT_ELLIPSIS not in fitted

    def test_exact_fit_boundary_is_kept(self):
        # 8 chars * 16 px == 128 px, exactly max_px -> still "fits".
        fitted, size = widget.fit_text("ABCDEFGH", 128, 16, 12, _linear)
        assert fitted == "ABCDEFGH"
        assert size == 16

    def test_empty_text_returned_unchanged(self):
        fitted, size = widget.fit_text("", 50, 16, 12, _linear)
        assert fitted == ""
        assert size == 16


# ===========================================================================
# Group C — step 2: shrink toward the floor, keep the LARGEST fitting size
# ===========================================================================

class TestShrinkToFit:
    def test_shrinks_when_too_wide_at_base(self):
        # 10 chars: 10*16=160 > 120; the largest size s with 10*s<=120 is 12.
        fitted, size = widget.fit_text("A" * 10, 120, 16, 8, _linear)
        assert fitted == "A" * 10          # text preserved — only shrunk
        assert size == 12

    def test_returns_the_largest_size_that_fits(self):
        text = "A" * 10
        max_px = 100
        fitted, size = widget.fit_text(text, max_px, 16, 8, _linear)
        assert fitted == text
        # It is genuinely the largest fitting size: this size fits, next up does not.
        assert _linear(text, size) <= max_px
        assert size == 16 or _linear(text, size + 1) > max_px

    def test_shrink_only_never_truncates(self):
        # Fits after shrinking to the floor -> must not be ellipsized.
        text = "A" * 12
        fitted, size = widget.fit_text(text, 120, 16, 10, _linear)
        assert fitted == text
        assert widget.TEXT_ELLIPSIS not in fitted
        assert size == 10

    def test_size_never_below_the_floor_when_shrinking(self):
        _, size = widget.fit_text("A" * 20, 60, 16, 10, _linear)
        assert size >= 10


# ===========================================================================
# Group D — step 3: ellipsis-truncate at the floor, as a LAST resort
# ===========================================================================

class TestEllipsisTruncation:
    def test_truncates_only_after_reaching_the_floor(self):
        # 20 chars: even at the floor (10px) 200 > 100, so truncation kicks in.
        fitted, size = widget.fit_text("A" * 20, 100, 16, 10, _linear)
        assert fitted.endswith(widget.TEXT_ELLIPSIS)
        # Truncation happens at the floor font size, not the base size.
        assert size == 10

    def test_ellipsis_marker_is_a_single_character(self):
        assert len(widget.TEXT_ELLIPSIS) == 1

    def test_truncated_result_has_exactly_one_ellipsis(self):
        fitted, _ = widget.fit_text("Configuration" * 3, 90, 16, 10, _linear)
        assert fitted.count(widget.TEXT_ELLIPSIS) == 1
        assert fitted.endswith(widget.TEXT_ELLIPSIS)

    def test_truncated_result_actually_fits(self):
        text = "A" * 20
        max_px = 100
        fitted, size = widget.fit_text(text, max_px, 16, 10, _linear)
        assert _linear(fitted, size) <= max_px

    def test_truncation_drops_characters_from_the_end(self):
        # Prefix of the original survives; the tail is what gets clipped.
        text = "ABCDEFGHIJKLMNOPQRST"          # 20 distinct-ish chars
        fitted, _ = widget.fit_text(text, 100, 16, 10, _linear)
        body = fitted[:-1]                       # drop the ellipsis
        assert text.startswith(body)
        assert len(body) < len(text)

    def test_non_empty_input_never_yields_empty_label(self):
        # Even in the pathological case the label is never blanked out.
        fitted, _ = widget.fit_text("X" * 40, 5, 16, 10, _linear)
        assert fitted != ""


# ===========================================================================
# Group E — floor clamping: never shrink UP past the base size
# ===========================================================================

class TestFloorClamping:
    def test_min_above_base_does_not_enlarge_a_fitting_label(self):
        fitted, size = widget.fit_text("Hi", 100, 16, 20, _linear)
        assert fitted == "Hi"
        assert size == 16               # clamped to base, not 20

    def test_min_above_base_truncates_at_base_size(self):
        # Too wide even at base; with min>base the effective floor is base.
        fitted, size = widget.fit_text("A" * 30, 100, 16, 24, _linear)
        assert fitted.endswith(widget.TEXT_ELLIPSIS)
        assert size == 16               # never grew above the base size


# ===========================================================================
# Group F — invariants across a wide range of inputs
# ===========================================================================

class TestFitInvariants:
    @pytest.mark.parametrize("length", list(range(1, 41)))
    def test_result_never_overflows_and_size_in_band(self, length):
        base, floor, max_px = 16, 10, 120
        fitted, size = widget.fit_text("A" * length, max_px, base, floor, _linear)
        # Font size always stays within the [floor, base] band.
        assert floor <= size <= base
        # The fitted label always fits the budget (max_px comfortably
        # exceeds one ellipsis glyph here, so truncation always converges).
        assert _linear(fitted, size) <= max_px

    def test_longer_translation_still_fits_the_same_budget(self):
        """A far longer French label must still be fitted into the budget."""
        max_px = 120
        en_text, en_size = widget.fit_text("Read", max_px, 16, 10, _linear)
        fr_text, fr_size = widget.fit_text(
            "Lire la configuration complete", max_px, 16, 10, _linear)
        assert _linear(en_text, en_size) <= max_px
        assert _linear(fr_text, fr_size) <= max_px


# ===========================================================================
# Group G — application in actbase: titles & buttons stop overflowing
# ===========================================================================
#
# These exercise the real fitting path in actbase (setTitle / setLeftButton /
# setRightButton).  The measurement that the real code funnels through
# ``tkinter.font.Font.measure`` is replaced with a deterministic,
# size-sensitive double so overflow can be forced and the drawn result
# checked against the real on-screen budget helpers.

import actstack                         # noqa: E402
import actbase                          # noqa: E402
from actbase import BaseActivity        # noqa: E402
from tests.ui.conftest import MockCanvas  # noqa: E402
from _constants import (                # noqa: E402
    TAG_TITLE_TEXT,
    TAG_BTN_LEFT,
    TAG_BTN_RIGHT,
)


class _FakeFont:
    """Stand-in for ``tkinter.font.Font`` whose measure() is size-sensitive.

    ``measure(text) == len(text) * size`` — mirrors ``_linear`` above so the
    activity's real fitting path produces deterministic, checkable widths
    without a live Tk root.
    """

    def __init__(self, family=None, size=12, weight="normal", **_):
        self._size = int(size)

    def measure(self, text):
        return len(text) * self._size

    # Some call sites ask a font for its metrics; keep it harmless.
    def metrics(self, *args):
        table = {"linespace": self._size, "ascent": self._size, "descent": 0}
        if args:
            return table.get(args[0], 0)
        return table


def _make_activity():
    """Start a bare BaseActivity backed by a MockCanvas (headless)."""
    actstack._reset()
    actstack._canvas_factory = lambda: MockCanvas()
    act = actstack.start_activity(BaseActivity)
    return act, act.getCanvas()


def _drawn(canvas, tag):
    """Return ``(text, font_size)`` for the canvas item carrying *tag*."""
    ids = canvas.find_withtag(tag)
    assert ids, "expected a canvas item tagged %r" % (tag,)
    item = canvas.get_item(ids[0])
    text = item["options"].get("text", "")
    font = str(item["options"].get("font", ""))
    size = int(font.rsplit(" ", 1)[1])
    return text, size


def _title_base_size():
    return int(str(actbase._get_title_font()).rsplit(" ", 1)[1])


class TestActbaseTitleFitting:
    def teardown_method(self):
        actstack._reset()

    def test_short_title_drawn_verbatim_at_base_size(self):
        act, canvas = _make_activity()
        with mock.patch("tkinter.font.Font", _FakeFont):
            act.setTitle("Read")
        text, size = _drawn(canvas, TAG_TITLE_TEXT)
        assert text == "Read"
        assert size == _title_base_size()

    def test_long_title_does_not_overflow_its_zone(self):
        act, canvas = _make_activity()
        long_title = "Reglages de la configuration avancee"
        base = _title_base_size()
        max_px = actbase._title_max_px()
        # Sanity: drawn raw at the base size this really WOULD overflow,
        # so the assertion below is meaningful.
        assert len(long_title) * base > max_px
        with mock.patch("tkinter.font.Font", _FakeFont):
            act.setTitle(long_title)
        text, size = _drawn(canvas, TAG_TITLE_TEXT)
        assert len(text) * size <= max_px
        # Fitting genuinely engaged (shrunk and/or truncated).
        assert size < base or text != long_title

    def test_very_long_title_truncated_with_single_ellipsis_at_floor(self):
        act, canvas = _make_activity()
        with mock.patch("tkinter.font.Font", _FakeFont):
            act.setTitle("X" * 60)
        text, size = _drawn(canvas, TAG_TITLE_TEXT)
        assert text.endswith(widget.TEXT_ELLIPSIS)
        assert text.count(widget.TEXT_ELLIPSIS) == 1
        assert size == actbase.TITLE_FONT_MIN
        assert len(text) * size <= actbase._title_max_px()


class TestActbaseButtonFitting:
    def teardown_method(self):
        actstack._reset()

    def _btn_base_size(self, act):
        spec = act._getBtnFontAndY()[0]
        return int(str(spec).rsplit(" ", 1)[1])

    def test_short_left_button_drawn_verbatim_at_base_size(self):
        act, canvas = _make_activity()
        with mock.patch("tkinter.font.Font", _FakeFont):
            act.setLeftButton("OK")
        text, size = _drawn(canvas, TAG_BTN_LEFT)
        assert text == "OK"
        assert size == self._btn_base_size(act)

    def test_long_left_button_does_not_overflow_its_zone(self):
        act, canvas = _make_activity()
        base = self._btn_base_size(act)
        max_px = actbase._BTN_LEFT_MAX_PX
        label = "Configuration"
        assert len(label) * base > max_px          # would overflow raw
        with mock.patch("tkinter.font.Font", _FakeFont):
            act.setLeftButton(label)
        text, size = _drawn(canvas, TAG_BTN_LEFT)
        assert len(text) * size <= max_px
        assert size < base or text != label

    def test_long_right_button_does_not_overflow_its_zone(self):
        act, canvas = _make_activity()
        base = self._btn_base_size(act)
        max_px = actbase._BTN_RIGHT_MAX_PX
        label = "Enregistrer"
        assert len(label) * base > max_px          # would overflow raw
        with mock.patch("tkinter.font.Font", _FakeFont):
            act.setRightButton(label)
        text, size = _drawn(canvas, TAG_BTN_RIGHT)
        assert len(text) * size <= max_px
        assert size < base or text != label

    def test_very_long_button_truncated_with_ellipsis_at_floor(self):
        act, canvas = _make_activity()
        with mock.patch("tkinter.font.Font", _FakeFont):
            act.setRightButton("Y" * 40)
        text, size = _drawn(canvas, TAG_BTN_RIGHT)
        assert text.endswith(widget.TEXT_ELLIPSIS)
        assert text.count(widget.TEXT_ELLIPSIS) == 1
        assert size == actbase.BTN_FONT_MIN
        assert len(text) * size <= actbase._BTN_RIGHT_MAX_PX
