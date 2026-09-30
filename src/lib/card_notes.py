##########################################################################
# Required Notice: Copyright ETOILE401 SAS (http://www.lab401.com)
#
# Copyright (c) 2026: ETOILE401 SAS & https://github.com/quantum-x/
# Copyright (c) 2026: Vost02
#
# This software is licensed under the PolyForm Noncommercial License 1.0.0.
# You may not use this software for commercial purposes.
#
# A copy of the license is available at:
# https://polyformproject.org/licenses/noncommercial/1.0.0
#
# This entire header "Required Notice" must remain in place.
##########################################################################

"""Shared card-notes store: ``<family>:<UID>`` -> note.

A small, dependency-free store any plugin can use to label a card:

    from lib.card_notes import load, lookup, set_note, save

    notes = load()
    label = lookup("mf1", "DAEFB416", notes)     # '' when unknown

The **card_notes plugin** is the producer (it browses and edits notes);
other plugins -- e.g. the Ultra/Tiny writers -- are consumers that show a
note next to the matching dump.  Keeping the file format here means the
producers and consumers cannot drift apart.

File format (``/mnt/upan/card_notes.json``)::

    {"version": 1,
     "notes": {"mf1:DAEFB416":      {"note": "Flat door", "updated": 0},
               "mfu:1D32320E950000": {"note": "Air filter"}}}

The key uses the card's UID: first the one in the dump filename, then -- for
a renamed dump -- the one read from the dump files themselves (see
:func:`uid_from_dump`).  The file lives at the root of the user partition, so
the built-in "disk full -> Clear" (which wipes ``dump/``) does not delete it,
and nothing browses it as a dump.

Reading never raises: a missing or malformed file yields no notes.
"""

import json
import os
import re
import time

NOTES_PATH = '/mnt/upan/card_notes.json'

# Dump filename -> UID.  Mirrors the iCopy-X naming convention used across
# the middleware (src/middleware/appfiles.py et al.).
_MF1_RE = re.compile(r'^M1-\S+-\S+_([0-9A-Fa-f]+)_\d+$')
_MFU_RE = re.compile(
    r'^(?:NTAG213|NTAG215|NTAG216|M0-UL\S*)_([0-9A-Fa-f]+)_\d+$',
    re.IGNORECASE)
_LF_ID_RE = re.compile(r'^.+-ID_([0-9A-Fa-f]+)_\d+$')
_T55XX_RE = re.compile(r'^T55xx_(\S+?)_\S+_\S+_\d+$')


def notes_path(path=None):
    """The active notes file: explicit *path*, else ``$CARD_NOTES_FILE``."""
    return path or os.environ.get('CARD_NOTES_FILE', NOTES_PATH)


def name_uid(name):
    """UID parsed from a dump filename (or stem); '' when it has none."""
    stem = os.path.splitext(os.path.basename(name))[0]
    for pattern in (_MF1_RE, _MFU_RE, _LF_ID_RE, _T55XX_RE):
        match = pattern.match(stem)
        if match:
            return match.group(1).upper()
    return ''


def _is_hex(text, length=None):
    if not text or (length is not None and len(text) != length):
        return False
    return all(c in '0123456789ABCDEFabcdef' for c in text)


def _em410x_uid_from_text(path):
    """10-hex EM410x identity from a saved LF ``.txt`` dump, or ''.

    ``appfiles`` has no EM410x reader (core doesn't need one), so a renamed
    LF dump is resolved here.  The saved line is the raw id (optionally
    ``k=v``), as written by the LF readers.
    """
    try:
        with open(path, 'r', encoding='utf-8', errors='ignore') as fh:
            text = fh.read()
    except OSError:
        return ''
    for line in text.replace('\r', '').split('\n'):
        token = line.strip().replace(' ', '')
        if '=' in token:
            token = token.split('=', 1)[1]
        if _is_hex(token, 10):
            return token.upper()
    return ''


def uid_from_dump(path, family=None):
    """UID of a dump on disk: the filename first, then the dump contents.

    Dumps can be renamed on the device, and the built-in Tag Info then reads
    the values from the files instead of the name; a note keyed by UID must
    do the same.  The content values come from the shared ``appfiles``
    readers (``dump_uid``).  *family* (``em410x``) selects this module's own
    reader, the one family core doesn't cover.  Returns ``''`` when no UID
    can be established.
    """
    uid = name_uid(path)
    if uid:
        return uid
    import appfiles
    uid = appfiles.dump_uid(path)
    if uid:
        return uid
    if not family:
        family = os.path.basename(os.path.dirname(path)).lower()
    if family == 'em410x':
        return _em410x_uid_from_text(path)
    return ''


def key(family, uid):
    """Store key for a card."""
    return '%s:%s' % (family, str(uid).upper())


def note_text(entry):
    """Text of a stored entry (str legacy form or ``{"note": ...}``)."""
    if isinstance(entry, str):
        return entry
    if isinstance(entry, dict):
        value = entry.get('note')
        if isinstance(value, str):
            return value
    return ''


def load(path=None):
    """Return the notes mapping; empty on any read problem."""
    try:
        with open(notes_path(path), 'r', encoding='utf-8', errors='ignore') as fh:
            doc = json.load(fh)
    except (OSError, ValueError):
        return {}
    if not isinstance(doc, dict):
        return {}
    notes = doc.get('notes', doc)
    return notes if isinstance(notes, dict) else {}


def save(notes, path=None):
    """Write the notes mapping atomically (tmp + replace).  Raises OSError."""
    target = notes_path(path)
    tmp = target + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as fh:
        json.dump({'version': 1, 'notes': notes}, fh, ensure_ascii=False, indent=2)
    os.replace(tmp, target)


def lookup(family, uid, notes=None, path=None):
    """Note for one card, or '' (loads the file when *notes* is omitted)."""
    if notes is None:
        notes = load(path)
    return note_text(notes.get(key(family, uid)))


def set_note(notes, family, uid, text):
    """Set (or, with empty *text*, remove) a card's note in *notes*."""
    if text:
        notes[key(family, uid)] = {'note': text, 'updated': int(time.time())}
    else:
        notes.pop(key(family, uid), None)
    return notes


def clear_note(notes, family, uid):
    """Remove a card's note from *notes*."""
    notes.pop(key(family, uid), None)
    return notes
