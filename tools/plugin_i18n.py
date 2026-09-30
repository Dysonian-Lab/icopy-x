#!/usr/bin/env python3
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

"""Plugin translation packs: extract, check and scaffold.

A plugin's user-visible text is written in English in manifest.json
(``name``), ui.json (titles, buttons, text lines, list labels, progress
messages, toasts) and plugin.py (literals handed to the host API:
``set_var``, ``show_toast``, ``set_progress``, ``tr``).  Translations
live next to the plugin, one file per language, keyed by the exact
English string::

    plugins/<name>/lang/en.json   # template: the strings this plugin must translate itself
    plugins/<name>/lang/fr.json   # {"Place source tag on reader": "Placez le tag ...", ...}
    plugins/<name>/lang/zh.json

At runtime a string is looked up in the plugin's pack for the active
language first, then in the core pack (data/lang/<code>.json, by English
value), and otherwise shown as-is.  Strings the core pack already knows
("Back", "Again", "Retry", ...) are therefore left out of the template and
are not required in a plugin pack; a plugin may still list one to
override the core wording.  Placeholders such as ``{tag_type}`` and
``%s`` are part of the key and must be kept verbatim in the translation;
text is translated before the placeholders are filled in.

Usage:
    python tools/plugin_i18n.py extract [PLUGIN ...]
        (Re)write lang/en.json for the given plugins (default: all).

    python tools/plugin_i18n.py check [PLUGIN ...] [--lang fr,zh] [--require]
        Verify the templates are current and every pack covers every
        required string with intact placeholders.  --require makes a
        missing pack an error (the CI contract for bundled plugins).
        Exit status 1 on any error.

    python tools/plugin_i18n.py fill [PLUGIN ...] --lang fr
        Create or update lang/<code>.json, adding every untranslated
        string with an empty value for a translator (or a machine
        translation pass) to fill in.

This tool is also imported by the test-suite; keep it standard-library
only.
"""

import argparse
import ast
import io
import json
import os
import re
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PLUGINS_DIR = os.path.join(REPO_ROOT, 'plugins')
CORE_LANG_DIR = os.path.join(REPO_ROOT, 'data', 'lang')

# ui.json keys whose string values are shown to the user.
TEXT_KEYS = ('title', 'text', 'label', 'message', 'header', 'subheader',
             'page', 'value', 'hint')

# Host API calls whose string argument reaches the screen:
#   method -> (positional index, keyword name or None)
HOST_CALLS = {
    'set_var': (1, None),
    'show_toast': (0, None),
    'set_progress': (1, 'message'),
    'tr': (0, None),
    # Common plugin-local wrapper: ``self._fail('message')``.
    '_fail': (0, None),
}

_PLACEHOLDER_RE = re.compile(r'\{[^{}]*\}')
_PERCENT_RE = re.compile(r'%(?:\([^)]*\))?[-+ 0#]*\d*(?:\.\d+)?[sdiuxXfeEgGc%]')
_LETTER_RE = re.compile(r'[^\W\d_]', re.UNICODE)


# ----------------------------------------------------------------------
# Extraction
# ----------------------------------------------------------------------

def is_translatable(text):
    """True when *text* carries words a translator can act on.

    Strings that are only placeholders, numbers, punctuation or
    whitespace (``"{error_msg}"``, ``"1/4"``, ``"\\n"``) are skipped, and
    so are token-only lines with a single letter outside the placeholders
    (``"B0: {blk0}"``, ``"A"``, ``"1k"``, ``"B%d: %s"``).
    """
    if not isinstance(text, str):
        return False
    stripped = _PERCENT_RE.sub('', _PLACEHOLDER_RE.sub('', text))
    return len(_LETTER_RE.findall(stripped)) >= 2


def _walk_ui(node, out):
    if isinstance(node, dict):
        for key, value in node.items():
            if key == 'buttons' and isinstance(value, dict):
                for btn in value.values():
                    if isinstance(btn, str):
                        out.append(btn)
                    elif isinstance(btn, dict):
                        out.append(btn.get('text', ''))
            elif key in TEXT_KEYS and isinstance(value, str):
                out.append(value)
            else:
                _walk_ui(value, out)
    elif isinstance(node, list):
        for item in node:
            _walk_ui(item, out)


def _module_constants(tree):
    """Map module-level ``NAME = 'literal'`` assignments."""
    consts = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target = node.targets[0]
            if (isinstance(target, ast.Name)
                    and isinstance(node.value, ast.Constant)
                    and isinstance(node.value.value, str)):
                consts[target.id] = node.value.value
    return consts


def _call_message_arg(call):
    """Return the AST node carrying the display text of a host call."""
    func = call.func
    name = func.attr if isinstance(func, ast.Attribute) else None
    if name not in HOST_CALLS:
        return None
    index, keyword = HOST_CALLS[name]
    if len(call.args) > index:
        return call.args[index]
    if keyword is not None:
        for kw in call.keywords:
            if kw.arg == keyword:
                return kw.value
    return None


def _is_tr_call(node):
    return (isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == 'tr')


def _literals(node, consts):
    """String literals reachable from *node* (constants, names, if-else)."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return [node.value]
    if isinstance(node, ast.Name) and node.id in consts:
        return [consts[node.id]]
    if isinstance(node, ast.IfExp):
        return _literals(node.body, consts) + _literals(node.orelse, consts)
    return []


def _is_untranslatable_composition(node):
    """A string built at runtime whose template never went through tr()."""
    if isinstance(node, ast.JoinedStr):
        return True
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Mod):
        return not _is_tr_call(node.left)
    if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
            and node.func.attr == 'format'):
        return not _is_tr_call(node.func.value)
    return False


def _walk_py(source, out, warnings, filename):
    try:
        tree = ast.parse(source, filename=filename)
    except SyntaxError as exc:
        warnings.append('%s: cannot parse: %s' % (filename, exc))
        return
    consts = _module_constants(tree)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        arg = _call_message_arg(node)
        if arg is None:
            continue
        literals = _literals(arg, consts)
        if literals:
            out.extend(literals)
        elif _is_untranslatable_composition(arg):
            warnings.append(
                '%s:%d: string composed at runtime is passed to %s(); wrap '
                'the template in host.tr() so it can be translated' % (
                    filename, node.lineno, node.func.attr))


def extract_strings(plugin_dir):
    """Return (strings, warnings) for one plugin directory.

    ``strings`` is every translatable English string the plugin shows,
    de-duplicated and ordered by first occurrence: manifest name, then
    ui.json, then plugin.py.  Core-pack coverage is not applied here;
    see ``required_strings``.
    """
    found = []
    warnings = []

    manifest_path = os.path.join(plugin_dir, 'manifest.json')
    if os.path.isfile(manifest_path):
        with io.open(manifest_path, encoding='utf-8') as f:
            manifest = json.load(f)
        found.append(manifest.get('name', ''))

    ui_path = os.path.join(plugin_dir, 'ui.json')
    if os.path.isfile(ui_path):
        with io.open(ui_path, encoding='utf-8') as f:
            _walk_ui(json.load(f), found)

    py_path = os.path.join(plugin_dir, 'plugin.py')
    if os.path.isfile(py_path):
        with io.open(py_path, encoding='utf-8') as f:
            _walk_py(f.read(), found, warnings,
                     os.path.relpath(py_path, REPO_ROOT))

    seen = set()
    strings = []
    for text in found:
        if text in seen or not is_translatable(text):
            continue
        seen.add(text)
        strings.append(text)
    return strings, warnings


def core_english_values():
    """Every English display string the core pack can translate.

    These resolve through the core pack at runtime (``resources.tr``), so a
    plugin does not need to translate them itself.
    """
    path = os.path.join(CORE_LANG_DIR, 'en.json')
    values = set()
    if os.path.isfile(path):
        with io.open(path, encoding='utf-8') as f:
            data = json.load(f)
        for key, category in data.items():
            if key.startswith('_') or not isinstance(category, dict):
                continue
            for v in category.values():
                if isinstance(v, str):
                    values.add(v)
    return values


def required_strings(plugin_dir, core=None):
    """(strings the plugin must translate itself, all strings, warnings)."""
    strings, warnings = extract_strings(plugin_dir)
    if core is None:
        core = core_english_values()
    own = [s for s in strings if s not in core]
    return own, strings, warnings


# ----------------------------------------------------------------------
# Packs
# ----------------------------------------------------------------------

def pack_path(plugin_dir, code):
    return os.path.join(plugin_dir, 'lang', '%s.json' % code)


def load_pack(path):
    """Return the {english: translation} map of a pack file (metadata dropped)."""
    with io.open(path, encoding='utf-8') as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError('%s: pack must be a JSON object' % path)
    return dict((k, v) for k, v in data.items() if not k.startswith('_'))


def write_pack(path, mapping, comment=None):
    data = {}
    if comment:
        data['_comment'] = comment
    data.update(mapping)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with io.open(path, 'w', encoding='utf-8', newline='\n') as f:
        json.dump(data, f, ensure_ascii=False, indent=4)
        f.write('\n')


def placeholders(text):
    """The placeholder signature a translation must preserve."""
    return (sorted(_PLACEHOLDER_RE.findall(text)), _PERCENT_RE.findall(text))


def core_language_codes():
    """Language codes shipped in data/lang/, English excluded."""
    codes = []
    if os.path.isdir(CORE_LANG_DIR):
        for fn in sorted(os.listdir(CORE_LANG_DIR)):
            if fn.endswith('.json') and fn != 'en.json':
                codes.append(fn[:-5])
    return codes


def bundled_plugin_dirs(plugins_dir=PLUGINS_DIR):
    dirs = []
    for entry in sorted(os.listdir(plugins_dir)):
        if entry.startswith('.') or entry.startswith('_'):
            continue
        path = os.path.join(plugins_dir, entry)
        if os.path.isfile(os.path.join(path, 'manifest.json')):
            dirs.append(path)
    return dirs


TEMPLATE_COMMENT = (
    'Generated by tools/plugin_i18n.py extract: the English strings this '
    'plugin must translate itself, mapped to themselves. Copy to '
    '<code>.json and translate the values, keeping {placeholders} and %s '
    'markers exactly. Labels the core pack already translates (Back, '
    'Again, ...) are omitted; list one only to override the core wording.')


def extract(plugin_dir):
    own, strings, warnings = required_strings(plugin_dir)
    write_pack(pack_path(plugin_dir, 'en'),
               dict((s, s) for s in own), TEMPLATE_COMMENT)
    return own, strings, warnings


def fill(plugin_dir, code):
    """Add every missing required string to lang/<code>.json, value empty."""
    own, _strings, warnings = required_strings(plugin_dir)
    path = pack_path(plugin_dir, code)
    existing = load_pack(path) if os.path.isfile(path) else {}
    mapping = {}
    for s in own:
        mapping[s] = existing.get(s, '')
    for s, v in existing.items():
        mapping.setdefault(s, v)
    write_pack(path, mapping)
    added = [s for s in own if s not in existing]
    return added, warnings


def check(plugin_dir, codes, require=False):
    """Return (errors, warnings) for one plugin."""
    name = os.path.basename(plugin_dir)
    own, strings, warnings = required_strings(plugin_dir)
    errors = []
    expected = set(own)
    shown = set(strings)

    en_path = pack_path(plugin_dir, 'en')
    if os.path.isfile(en_path):
        template = set(load_pack(en_path))
        for s in sorted(expected - template):
            errors.append('%s: lang/en.json is out of date, missing %r '
                          '(run: plugin_i18n.py extract %s)' % (name, s, name))
        for s in sorted(template - expected):
            errors.append('%s: lang/en.json lists %r which the plugin does '
                          'not need to translate (run: plugin_i18n.py '
                          'extract %s)' % (name, s, name))
    elif require:
        errors.append('%s: lang/en.json missing (run: plugin_i18n.py extract %s)'
                      % (name, name))

    for code in codes:
        path = pack_path(plugin_dir, code)
        if not os.path.isfile(path):
            msg = '%s: no %s pack (lang/%s.json)' % (name, code, code)
            (errors if require else warnings).append(msg)
            continue
        try:
            pack = load_pack(path)
        except (ValueError, OSError) as exc:
            errors.append('%s: lang/%s.json unreadable: %s' % (name, code, exc))
            continue
        for s in strings:
            if s not in pack:
                if s in expected:
                    errors.append('%s: lang/%s.json missing %r' % (name, code, s))
                continue
            value = pack[s]
            if not isinstance(value, str) or not value.strip():
                errors.append('%s: lang/%s.json has no translation for %r'
                              % (name, code, s))
                continue
            if placeholders(value) != placeholders(s):
                errors.append('%s: lang/%s.json placeholder mismatch for %r '
                              '-> %r' % (name, code, s, value))
        for s in sorted(set(pack) - shown):
            warnings.append('%s: lang/%s.json has unused entry %r'
                            % (name, code, s))
    return errors, warnings


# ----------------------------------------------------------------------
# CLI
# ----------------------------------------------------------------------

def _resolve_plugins(names, plugins_dir):
    if not names:
        return bundled_plugin_dirs(plugins_dir)
    dirs = []
    for n in names:
        path = n if os.path.isdir(n) else os.path.join(plugins_dir, n)
        if not os.path.isfile(os.path.join(path, 'manifest.json')):
            sys.exit('not a plugin directory: %s' % n)
        dirs.append(os.path.abspath(path))
    return dirs


def main(argv=None):
    parser = argparse.ArgumentParser(
        description='Extract, check and scaffold plugin translation packs.')
    parser.add_argument('command', choices=('extract', 'check', 'fill'))
    parser.add_argument('plugins', nargs='*',
                        help='plugin names or directories (default: all bundled)')
    parser.add_argument('--plugins-dir', default=PLUGINS_DIR)
    parser.add_argument('--lang', default=None,
                        help='comma-separated language codes '
                             '(default: every non-English core pack)')
    parser.add_argument('--require', action='store_true',
                        help='check: a missing pack is an error')
    args = parser.parse_args(argv)

    codes = args.lang.split(',') if args.lang else core_language_codes()
    plugin_dirs = _resolve_plugins(args.plugins, args.plugins_dir)
    exit_code = 0

    for plugin_dir in plugin_dirs:
        name = os.path.basename(plugin_dir)
        if args.command == 'extract':
            own, strings, warnings = extract(plugin_dir)
            print('%-22s %3d strings to translate (%d covered by the core '
                  'pack) -> lang/en.json'
                  % (name, len(own), len(strings) - len(own)))
        elif args.command == 'fill':
            if not codes:
                sys.exit('fill: --lang is required')
            warnings = []
            for code in codes:
                added, w = fill(plugin_dir, code)
                warnings += w
                print('%-22s lang/%s.json: %d string(s) added'
                      % (name, code, len(added)))
        else:
            errors, warnings = check(plugin_dir, codes, require=args.require)
            for e in errors:
                print('ERROR   ' + e)
            if errors:
                exit_code = 1
            else:
                print('%-22s ok (%s)' % (name, ', '.join(codes) or 'no languages'))
        for w in warnings:
            print('WARNING ' + w)
    return exit_code


if __name__ == '__main__':
    sys.exit(main())
