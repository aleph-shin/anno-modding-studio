"""The Studio's own words, in the language of the person reading them.

Every sentence the Studio shows goes through :func:`tr` (or :func:`trn`, when it
counts something), written in English at the call site.  The English is the key
the translation is looked up by, so an untranslated sentence simply shows in
English and nothing is ever blank.  ``tools/i18n.py`` finds every call and keeps
the catalogs in ``anno_modding_studio/locale`` in step with the code; see
``docs/standards/translation.md`` for how, and for the rules below at more length.

Three rules make a sentence translatable at all, and the extractor enforces
them because each one is easy to break without noticing:

* **Whole sentences.**  German puts its words in a different order, so a
  sentence glued together from fragments cannot be translated -- the fragments
  can, but not the order.  Pass the variable parts in instead, and let the
  translation put them where they belong.
* **Named placeholders once there are two.**  ``%s`` can only stay where it
  is; ``%(mod)s`` can move.  One positional placeholder is fine.
* **Counts through trn.**  ``"s" if n != 1`` is an English rule, and not every
  language has only two forms; :func:`trn` asks the catalog.

This module is deliberately free of Qt, like the rest of ``core``: core's own
messages -- the ones it raises -- are translated here too, and the tests of
core run without a QApplication.

No translation is installed until the application starts
(:func:`install`), so anything evaluated at import time would stay English.
That is why ``tr`` is never called at module or class level -- the extractor
refuses it -- and why a table of labels is marked with :func:`tr_noop` and
translated where it is shown.
"""

from __future__ import annotations

import gettext
import io
import re
import struct
import time
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

from . import po

LOCALE_DIR = Path(__file__).resolve().parent.parent / "locale"

# The language the Studio is written in, and so the one needing no catalog.
SOURCE = "en"
SOURCE_LABEL = "English"

_translations: gettext.NullTranslations = gettext.NullTranslations()
_language = SOURCE

# The context of English that is kept as it is -- in a loadout file, or in a
# comment of an exported mod -- and translated only where it is shown.  See
# :func:`tr_stored`.
STORED = "stored"


# -- choosing a language ------------------------------------------------------------
def available(directory: Optional[Path] = None) -> List[Tuple[str, str]]:
    """``(code, label)`` for every language there is a catalog for, English first.

    The label is the catalog's own ``X-Language-Label`` -- the language's name in
    that language, and whether a person or a machine translated it -- so a
    German reader looking for German finds "Deutsch", not "German".
    """
    found = [(SOURCE, SOURCE_LABEL)]
    folder = Path(directory) if directory is not None else LOCALE_DIR
    for path in sorted(folder.glob("*.po")):
        try:
            header = po.read(path).header
        except (OSError, ValueError):
            continue
        found.append((path.stem, header.get("X-Language-Label") or path.stem))
    return found


def install(code: str, directory: Optional[Path] = None) -> bool:
    """Show the Studio in ``code`` from now on; False (and English) if it can't.

    Nothing already on screen changes: every sentence is looked up as it is
    made, so a language takes effect for whatever is built after this.
    """
    global _translations, _language
    code = (code or SOURCE).strip()
    path = (Path(directory) if directory is not None else LOCALE_DIR) / ("%s.po" % code)
    _learn_stored(None)
    if code == SOURCE or not path.is_file():
        _translations, _language = gettext.NullTranslations(), SOURCE
        return code == SOURCE
    try:
        catalog = po.read(path)
    except (OSError, ValueError):
        _translations, _language = gettext.NullTranslations(), SOURCE
        return False
    _translations = gettext.GNUTranslations(io.BytesIO(compile_catalog(catalog)))
    _language = code
    _learn_stored(catalog)
    return True


def language() -> str:
    """The code of the language in use: ``"en"`` unless one was installed."""
    return _language


def compile_catalog(catalog: po.Catalog) -> bytes:
    """``catalog`` as the binary ``.mo`` that :mod:`gettext` reads.

    Untranslated and fuzzy entries are left out, so they fall back to English
    rather than showing an empty string or a translation of an older sentence.
    """
    header = dict(catalog.header)
    # gettext reads every string in the charset the header names, and ASCII
    # when it names none; the catalogs are UTF-8 whatever they say.
    header["Content-Type"] = "text/plain; charset=UTF-8"
    pairs: Dict[bytes, bytes] = {
        b"": "".join("%s: %s\n" % item for item in header.items()).encode()
    }
    for entry in catalog.entries:
        if not entry.translated:
            continue
        said = entry.msgid if not entry.plural else entry.msgid + "\0" + entry.plural
        if entry.context:
            said = entry.context + "\x04" + said
        pairs[said.encode()] = "\0".join(entry.strings).encode()
    keys = sorted(pairs)
    ids = b"".join(key + b"\0" for key in keys)
    strs = b"".join(pairs[key] + b"\0" for key in keys)
    start = 7 * 4 + 16 * len(keys)
    offsets = []
    at = 0
    for key in keys:
        offsets.append((len(key), start + at))
        at += len(key) + 1
    ids_end = start + len(ids)
    at = 0
    for key in keys:
        offsets.append((len(pairs[key]), ids_end + at))
        at += len(pairs[key]) + 1
    head = struct.pack("<7I", 0x950412DE, 0, len(keys), 7 * 4, 7 * 4 + 8 * len(keys), 0, 0)
    table = b"".join(struct.pack("<2I", *pair) for pair in offsets)
    return head + table + ids + strs


# -- keywords ---------------------------------------------------------------------------
# A term the Studio uses in one sense only (``core.keywords``) is marked where
# a sentence says it: ``[[patch]]``, or ``[[patch|patches]]`` where the words
# shown are another form of it. A translation keeps the marks, round its own
# words: ``[[patch|Patch]]``. ``tr`` shows the words alone. ``tr_kw`` keeps
# each mark, as the term's key and its words between the characters below,
# for a box or a help panel to show as a term that can be pointed at for its
# meaning. Characters of Unicode's private use area, as ``widgets.named``'s,
# so no sentence and no name holds one.
TERM_START, TERM_WORDS, TERM_END = "", "", ""
_TERM = re.compile(r"\[\[([^\[\]|]+)(?:\|([^\[\]]+))?\]\]")


def terms_in(text: str) -> List[str]:
    """The keys of the keywords marked in ``text``, in order."""
    return [match.group(1).strip() for match in _TERM.finditer(text)]


def _terms(text: str, keep: bool) -> str:
    """``text`` with each keyword's mark taken off, or kept as characters."""
    if "[[" not in text:
        return text

    def put(match) -> str:
        key, words = match.group(1).strip(), match.group(2) or match.group(1)
        return TERM_START + key + TERM_WORDS + words + TERM_END if keep else words

    return _TERM.sub(put, text)


def unmarked(text: str) -> str:
    """``text`` from :func:`tr_kw` as plain words: the keywords' marks gone."""
    return re.sub(TERM_START + "[^" + TERM_WORDS + "]*" + TERM_WORDS, "", text).replace(
        TERM_END, "")


# -- the words themselves ---------------------------------------------------------------
def tr(text: str, *args, context: str = "", **values) -> str:
    """``text`` in the reader's language, filled in with ``args`` or ``values``.

    ``tr("Saved %s.", name)`` or ``tr("%(mod)s overrides %(other)s.", mod=a,
    other=b)`` -- never ``tr(...) % x``: formatting here is what lets a broken
    translation fall back to the English instead of raising.  With nothing to
    fill in, the text is returned as it is, so a lone ``%`` needs no doubling.

    ``context`` tells apart two uses of the same English that another language
    says differently -- "Open" the verb and "Open" the state -- and is part of
    what the translation is looked up by.
    """
    return _said(text, args, context, values, keep=False)


def tr_kw(text: str, *args, context: str = "", **values) -> str:
    """:func:`tr`, with the keywords marked in ``text`` kept for a box or a
    help panel to show as terms (``widgets.show_box_text``, ``HelpTip``).
    Shown anywhere else, the marks would show too."""
    return _said(text, args, context, values, keep=True)


def _said(text: str, args: tuple, context: str, values: dict, keep: bool) -> str:
    said = _translations.pgettext(context, text) if context else _translations.gettext(text)
    # The marks come off the sentence before its values go in: a mod's name
    # with "[[" in it is a name, and no mark.
    return _fill(_terms(said, keep), _terms(text, keep), args, values)


def trn(singular: str, plural: str, n: int, *, context: str = "", **values) -> str:
    """The form of a sentence that goes with the count ``n``.

    ``%(n)s`` in either form is filled with ``n``, written the reader's way
    (:func:`number`); name any other placeholders.  The English singular can
    leave the number out ("One mod") -- a language whose "singular" also covers
    21 and 31 will simply put it back.
    """
    return _said_n(singular, plural, n, context, values, keep=False)


def trn_kw(singular: str, plural: str, n: int, *, context: str = "", **values) -> str:
    """:func:`trn`, with the keywords kept as :func:`tr_kw` keeps them."""
    return _said_n(singular, plural, n, context, values, keep=True)


def _said_n(singular: str, plural: str, n: int, context: str, values: dict,
            keep: bool) -> str:
    said = (
        _translations.npgettext(context, singular, plural, n) if context
        else _translations.ngettext(singular, plural, n)
    )
    values.setdefault("n", number(n))
    return _fill(_terms(said, keep), _terms(singular if n == 1 else plural, keep), (), values)


def tr_noop(text: str, *, context: str = "") -> str:
    """Mark ``text`` for translation without translating it yet.

    For labels in a module-level table, which exists before any language is
    installed.  The table keeps the English -- it is often a key as well -- and
    the place that shows it says ``tr(label)``.  ``context`` is only for the
    extractor; pass the same one to that later ``tr``.
    """
    del context
    return text


# -- English that was kept, translated where it is shown ------------------------------------
# Some of the Studio's sentences are written down as they are: why a ModOp
# could not be read, what a change does, what a rule keeps.  They live in the
# loadout file and in the comments of an exported mod, where they must be the
# same on every machine -- and two copies of a loadout are compared by them.
# So they stay English there, and the reader's language is put on them only
# on the way to the screen.
#
# Each shape such a sentence can take is marked where it is made, with
# ``tr_noop(..., context=STORED) % values``.  :func:`tr_stored` recognises the
# shape again in the finished English, takes the values back out of it, and
# fills them into the translation -- values that are themselves such a shape
# ("kept as written: <a reason>") translated the same way.  Nothing is stored
# beside the English for this, so a loadout saved before there was a German
# catalog is read in German too; a wording that has changed since simply stays
# English.
_PLACEHOLDER = re.compile(
    r"%(?:\((?P<name>[^)]*)\))?[#0\- +]*(?:\*|\d+)?(?:\.(?:\*|\d+))?(?P<kind>[diouxXeEfFgGcrsa%])"
)


class _Shape:
    """One marked sentence: a pattern for its English, and its translation."""

    def __init__(self, source: str, said: str):
        pattern: List[str] = []
        self.keys: List[object] = []
        seen: Dict[object, str] = {}
        at = 0
        for match in _PLACEHOLDER.finditer(source):
            pattern.append(re.escape(source[at:match.start()]))
            at = match.end()
            if match.group("kind") == "%":
                pattern.append("%")
                continue
            key = match.group("name")
            if key is None:
                key = len([k for k in self.keys if isinstance(k, int)])
            if key in seen:
                pattern.append("(?P=%s)" % seen[key])
                continue
            group = seen[key] = "v%d" % len(self.keys)
            self.keys.append(key)
            number = match.group("kind") in "diouxX"
            pattern.append("(?P<%s>%s)" % (group, r"-?\d+" if number else ".*?"))
        pattern.append(re.escape(source[at:]))
        self.regex = re.compile("".join(pattern), re.S)
        self.said = said
        # The more of a sentence is its own words, the surer a match is.
        self.weight = len(_PLACEHOLDER.sub("", source))

    def fill(self, values: Dict[object, str]) -> str:
        positional = iter(values[key] for key in self.keys if isinstance(key, int))

        def one(match):
            if match.group("kind") == "%":
                return "%"
            name = match.group("name")
            return values.get(name, match.group(0)) if name is not None else next(positional, "")

        return _PLACEHOLDER.sub(one, self.said)


_stored_exact: Dict[str, str] = {}
_stored_shapes: List[_Shape] = []
_stored_seen: Dict[str, str] = {}


def _learn_stored(catalog: Optional[po.Catalog]) -> None:
    _stored_exact.clear()
    _stored_seen.clear()
    del _stored_shapes[:]
    for entry in catalog.entries if catalog is not None else ():
        if entry.context != STORED or entry.plural or not entry.translated:
            continue
        if _PLACEHOLDER.search(entry.msgid.replace("%%", "")):
            _stored_shapes.append(_Shape(entry.msgid, entry.strings[0]))
        else:
            _stored_exact[entry.msgid] = entry.strings[0]
    _stored_shapes.sort(key=lambda shape: -shape.weight)


def tr_stored(text: str) -> str:
    """Kept English, in the reader's language where its shape is known.

    ``text`` is returned as it is when nothing recognises it: a wording older
    than the catalog, or words that were never the Studio's (a mod's own
    comment).  A list of reasons joined with "; " is translated reason by
    reason.
    """
    if not text or not (_stored_exact or _stored_shapes):
        return text
    said = _stored_seen.get(text)
    if said is None:
        if len(_stored_seen) > 20000:
            _stored_seen.clear()
        said = _recognise(text, 0)
        if said is None:
            # Not one known sentence, nor a list of them: translate what of
            # the list is known, and leave the rest as it was written.
            said = "; ".join(_recognise(part, 0) or part for part in text.split("; "))
        _stored_seen[text] = said
    return said


def _recognise(text: str, depth: int) -> Optional[str]:
    """``text`` translated, or None if it is not a shape the catalog knows."""
    exact = _stored_exact.get(text)
    if exact is not None:
        return exact
    # Reasons joined with "; " first, and only if every one of them is known:
    # a shape that begins or ends with a value would otherwise take in the
    # reasons beside it as part of that value, and leave them English.
    if "; " in text:
        parts = [_recognise(part, depth) for part in text.split("; ")]
        if all(part is not None for part in parts):
            return "; ".join(parts)  # type: ignore[arg-type]
    for shape in _stored_shapes:
        match = shape.regex.fullmatch(text)
        if match is None:
            continue
        values = {}
        for number, key in enumerate(shape.keys):
            value = match.group("v%d" % number)
            inner = _recognise(value, depth + 1) if depth < 4 and value else None
            values[key] = inner if inner is not None else value
        return shape.fill(values)
    return None


def _fill(said: str, source: str, args: tuple, values: dict) -> str:
    if args and values:
        raise TypeError("tr() takes positional or named values, not both")
    if not args and not values:
        return said
    filling = values if values else args
    try:
        return said % filling
    except (KeyError, TypeError, ValueError):
        # A translation whose placeholders do not match its English. The
        # catalog check exists to stop one being committed; this is so that one
        # that got through costs a sentence of English, not an exception.
        if said == source:
            raise
        return source % filling


# -- numbers, lists and dates, written the reader's way -----------------------------------
def number(value: float, decimals: int = 0) -> str:
    """``value`` with the reader's digit grouping and decimal mark.

    "1,234.5" in English, "1.234,5" in German -- both marks come from the
    catalog, like every other word.  Not for identifiers such as GUIDs, which
    are names that happen to be digits and are never grouped.
    """
    text = "{:,.{}f}".format(value, max(0, decimals))
    thousands = tr(",", context="digit grouping mark")
    point = tr(".", context="decimal mark")
    return text.translate({ord(","): thousands, ord("."): point})


def join_list(items: Iterable[object], conjunction: str = "and") -> str:
    """"A", "A and B", "A, B and C" -- or with ``conjunction="or"``.

    For naming things inside a sentence.  A bare list, one per line or after a
    colon, is not a sentence, and needs none of this.
    """
    names = [str(item) for item in items]
    if len(names) <= 1:
        return names[0] if names else ""
    rest = tr(", ", context="between items of a list").join(names[:-1])
    if conjunction == "and":
        return tr("%(rest)s and %(last)s", rest=rest, last=names[-1])
    if conjunction == "or":
        return tr("%(rest)s or %(last)s", rest=rest, last=names[-1])
    raise ValueError("conjunction is 'and' or 'or', not %r" % conjunction)


def bullets(items: Iterable[object], most: int = 0) -> str:
    """``items`` one to a line, each after "  • ".

    With ``most``, the first ``most`` of them and a last line saying how many
    more there are, so a long list in a message box neither runs off the
    screen nor stops without saying it stopped.
    """
    names = [str(item) for item in items]
    shown = names[:most] if most else names
    lines = ["  • " + name for name in shown]
    if len(names) > len(shown):
        lines.append(trn("  • and %(n)s more", "  • and %(n)s more", len(names) - len(shown)))
    return "\n".join(lines)


def size_text(count: int) -> str:
    """A size in bytes as people read one: "12 KB", "4.2 MB", "1.3 GB"."""
    if count < 1024 * 1024:
        return tr("%s KB", number(max(1, count // 1024) if count else 0))
    if count < 1024 ** 3:
        return tr("%s MB", number(count / (1024 * 1024), 1))
    return tr("%s GB", number(count / (1024 ** 3), 1))


_MONTHS = (
    tr_noop("Jan", context="month"), tr_noop("Feb", context="month"),
    tr_noop("Mar", context="month"), tr_noop("Apr", context="month"),
    tr_noop("May", context="month"), tr_noop("Jun", context="month"),
    tr_noop("Jul", context="month"), tr_noop("Aug", context="month"),
    tr_noop("Sep", context="month"), tr_noop("Oct", context="month"),
    tr_noop("Nov", context="month"), tr_noop("Dec", context="month"),
)


def date_text(timestamp: float, year: bool = True, clock: bool = False) -> str:
    """A day as people write it: "05 Sep 2026", or "05. Sep. 2026".

    ``year=False`` leaves the year off, for dates that are obviously recent;
    ``clock`` adds the time.  Not for anything a file name or a record keeps:
    those stay ISO, the same in every language.
    """
    moment = time.localtime(timestamp)
    values = {
        "day": "%02d" % moment.tm_mday,
        "month": tr(_MONTHS[moment.tm_mon - 1], context="month"),
        "year": "%d" % moment.tm_year,
        "time": time.strftime("%H:%M", moment),
    }
    if year and clock:
        return tr("%(day)s %(month)s %(year)s %(time)s", context="date", **values)
    if year:
        return tr("%(day)s %(month)s %(year)s", context="date", **values)
    if clock:
        return tr("%(day)s %(month)s %(time)s", context="date", **values)
    return tr("%(day)s %(month)s", context="date", **values)
