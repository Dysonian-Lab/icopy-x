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

"""pm3_localize — localize user-facing PM3/system status phrases.

Translates the small, curated set of human-readable status phrases that
proxmark/system output puts in front of the user (e.g. "No card found",
"Writing...") into the active UI language.  Everything else — hex dumps,
structured lines, dynamic counters, and any output not in the recognized
phrase set — is passed through untouched.

Called by executor._send_and_cache() after _clean_pm3_output() and
pm3_compat.translate_response(), i.e. once the response text has been
stripped to its human-readable shape.

Translation mechanism:
    Each recognized English phrase is itself the resource key.  Translations
    live in the active language's ``system`` category (data/lang/<code>.json);
    resources.get_str(phrase) returns the localized string, or the English
    phrase itself when the active language has no entry for it.  English is
    therefore a strict no-op — short-circuited before any work is done.

Safety contract:
    - Strict no-op when resources.getLanguage() == 'en'.
    - Only whole lines that (once surrounding whitespace is removed) exactly
      equal a recognized phrase are translated.  A line carrying a hex dump,
      a structured field, or dynamic/unknown text never matches, so it is
      never altered.  Leading/trailing whitespace and line endings are
      preserved.

Extending:
    Add the English phrase to USER_FACING_PHRASES below (or call
    register_phrases() at runtime), then add its translation under the
    ``system`` category of each data/lang/<code>.json.  No other change is
    required.
"""

import logging

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# String resources — provides get_str()/getLanguage().  Absent under some
# test harnesses; localize() degrades to a no-op when so.
# ---------------------------------------------------------------------------
try:
    import resources
except ImportError:
    try:
        from lib import resources
    except ImportError:
        resources = None

# ---------------------------------------------------------------------------
# Recognized user-facing PM3/system phrases.
#
# Each entry is BOTH the English text and the resource key looked up in the
# active language's ``system`` category.  Keep entries to complete, standalone
# human-readable status phrases — never fragments that could occur inside a
# hex dump or a structured line.  Adding a phrase here (and its translation in
# data/lang/<code>.json) is all that is needed to localize it.
# ---------------------------------------------------------------------------
USER_FACING_PHRASES = frozenset({
    'No card found',
    'No tag found',
    'Card detected',
    'Auth failed',
    'Read failed',
    'Write failed',
    'Unknown card',
    'No valid key',
    'Wrong key',
    'Reading...',
    'Writing...',
    'Scanning...',
    'Done',
    'Success',
    'Failed',
    'Please wait...',
})

# Mutable working set — seeded from the frozenset above; register_phrases()
# extends it at runtime without touching the canonical seed.
_phrases = set(USER_FACING_PHRASES)


def register_phrases(*phrases):
    """Add one or more phrases to the recognized set at runtime.

    Accepts individual phrase strings and/or iterables of them.  Useful for
    plugins that emit their own status phrases; the corresponding translation
    still has to be added to the ``system`` category of each language file.
    """
    for item in phrases:
        if isinstance(item, str):
            _phrases.add(item)
        else:
            for phrase in item:
                if isinstance(phrase, str):
                    _phrases.add(phrase)


def _translate_phrase(phrase):
    """Return the active-language translation of *phrase*, else *phrase*.

    resources.get_str() resolves the English phrase (used as the key) through
    the active language's categories and falls back to English on its own, so
    an untranslated phrase round-trips unchanged.
    """
    if resources is None:
        return phrase
    try:
        value = resources.get_str(phrase)
    except Exception:
        return phrase
    return value if isinstance(value, str) else phrase


def _localize_line(line):
    """Localize a single line if it is exactly a recognized phrase.

    Surrounding whitespace and the line ending (as produced by
    splitlines(keepends=True)) are preserved; a non-matching line — a hex
    dump, a structured field, dynamic/unknown output — is returned verbatim.
    """
    core = line.strip()
    if not core or core not in _phrases:
        return line

    translated = _translate_phrase(core)
    if translated == core:
        return line

    lead = line[:len(line) - len(line.lstrip())]
    trail = line[len(line.rstrip()):]
    return lead + translated + trail


def localize(text):
    """Localize recognized user-facing status phrases in PM3 output text.

    Strict no-op for English (or when resources is unavailable).  Only whole
    lines that exactly match a recognized phrase are translated; every other
    line — hex dumps, structured lines, unknown or dynamic output — is left
    byte-for-byte unchanged.
    """
    if not text or resources is None:
        return text

    try:
        if resources.getLanguage() == 'en':
            return text
    except Exception:
        return text

    return ''.join(_localize_line(line) for line in text.splitlines(keepends=True))
