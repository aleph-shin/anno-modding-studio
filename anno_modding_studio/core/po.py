"""Reading and writing gettext ``.po`` catalogs.

A ``.po`` file is the standard home for an application's translations: plain
text, one entry per sentence, the English beside what it becomes.  Poedit and
every translation service read it, and it diffs like any other text -- which
is why the Studio keeps its translations in ``.po`` alone and never commits
the compiled ``.mo`` form.  :mod:`.i18n` compiles in memory at start-up
instead, so there is no second file to fall out of step with the first.

Only as much of the format as the Studio writes is understood: contexts,
plurals, flags, and the three kinds of comment.  Obsolete (``#~``) entries are
skipped on reading, and never written.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple


@dataclass
class Entry:
    """One sentence and its translation."""

    msgid: str
    context: str = ""
    plural: str = ""
    # One string, or one per plural form. Empty strings mean untranslated.
    strings: List[str] = field(default_factory=lambda: [""])
    flags: List[str] = field(default_factory=list)
    # "#." -- written by the extractor: what the code said about the text.
    notes: List[str] = field(default_factory=list)
    # "#:" -- where in the source the text is used.
    references: List[str] = field(default_factory=list)
    # "# " -- written by whoever translated it, kept across every update.
    comments: List[str] = field(default_factory=list)

    @property
    def key(self) -> Tuple[str, str]:
        return (self.context, self.msgid)

    @property
    def fuzzy(self) -> bool:
        return "fuzzy" in self.flags

    @property
    def translated(self) -> bool:
        """Whether this entry would be used: every form filled, none stale."""
        return not self.fuzzy and all(self.strings)


@dataclass
class Catalog:
    header: Dict[str, str] = field(default_factory=dict)
    # Comments above the header entry, kept as written.
    preamble: List[str] = field(default_factory=list)
    entries: List[Entry] = field(default_factory=list)

    def by_key(self) -> Dict[Tuple[str, str], Entry]:
        return {entry.key: entry for entry in self.entries}


# -- reading ----------------------------------------------------------------------
_ESCAPES = {"n": "\n", "t": "\t", "r": "\r", '"': '"', "\\": "\\"}
_KEYWORD = re.compile(r'^(msgctxt|msgid_plural|msgid|msgstr(?:\[(\d+)\])?)\s+(".*")\s*$')


def _unquote(text: str) -> str:
    body = text.strip()
    if len(body) < 2 or body[0] != '"' or body[-1] != '"':
        raise ValueError("not a quoted string: %s" % text)
    out = []
    chars = iter(body[1:-1])
    for char in chars:
        if char == "\\":
            follow = next(chars, "")
            out.append(_ESCAPES.get(follow, "\\" + follow))
        else:
            out.append(char)
    return "".join(out)


def parse(text: str) -> Catalog:
    catalog = Catalog()
    blocks: List[List[str]] = [[]]
    for line in text.splitlines():
        if line.strip():
            blocks[-1].append(line.rstrip())
        elif blocks[-1]:
            blocks.append([])
    for block in blocks:
        entry = _entry(block)
        if entry is None:
            continue
        if entry.msgid == "" and not entry.context:
            catalog.preamble = [line[2:] if line.startswith("# ") else line[1:]
                                for line in block if line.startswith("#")]
            catalog.header = _header(entry.strings[0])
        else:
            catalog.entries.append(entry)
    return catalog


def _entry(block: List[str]) -> Optional[Entry]:
    fields: Dict[str, List[str]] = {}
    current: Optional[str] = None
    entry = Entry(msgid="", strings=[])
    for line in block:
        if line.startswith("#~"):
            return None
        if line.startswith("#,"):
            entry.flags += [flag.strip() for flag in line[2:].split(",") if flag.strip()]
        elif line.startswith("#."):
            entry.notes.append(line[2:].strip())
        elif line.startswith("#:"):
            entry.references += line[2:].split()
        elif line.startswith("#"):
            entry.comments.append(line[2:] if line.startswith("# ") else line[1:])
        elif line.startswith('"'):
            if current is None:
                raise ValueError("continuation with nothing to continue: %s" % line)
            fields[current].append(_unquote(line))
        else:
            match = _KEYWORD.match(line)
            if not match:
                raise ValueError("cannot read this .po line: %s" % line)
            keyword, index, value = match.groups()
            current = keyword if index is None else "msgstr[%s]" % index
            fields[current] = [_unquote(value)]
    if "msgid" not in fields:
        return None
    joined = {name: "".join(parts) for name, parts in fields.items()}
    entry.msgid = joined["msgid"]
    entry.context = joined.get("msgctxt", "")
    entry.plural = joined.get("msgid_plural", "")
    if entry.plural:
        forms = sorted(
            (int(name[7:-1]), value) for name, value in joined.items()
            if name.startswith("msgstr[")
        )
        entry.strings = [value for _index, value in forms] or ["", ""]
    else:
        entry.strings = [joined.get("msgstr", "")]
    return entry


def _header(text: str) -> Dict[str, str]:
    header = {}
    for line in text.splitlines():
        name, sep, value = line.partition(":")
        if sep:
            header[name.strip()] = value.strip()
    return header


def read(path: Path) -> Catalog:
    return parse(Path(path).read_text(encoding="utf-8"))


# -- writing ----------------------------------------------------------------------
def _quote(text: str) -> str:
    return '"%s"' % (
        text.replace("\\", "\\\\").replace('"', '\\"')
        .replace("\t", "\\t").replace("\r", "\\r").replace("\n", "\\n")
    )


def _field(keyword: str, value: str) -> List[str]:
    """A keyword and its string, split after each newline as gettext does."""
    if "\n" not in value.rstrip("\n"):
        return ["%s %s" % (keyword, _quote(value))]
    pieces = value.splitlines(keepends=True)
    return ["%s \"\"" % keyword] + [_quote(piece) for piece in pieces]


def format_entry(entry: Entry) -> str:
    lines = ["# " + comment if comment else "#" for comment in entry.comments]
    lines += ["#. " + note for note in entry.notes]
    if entry.references:
        lines.append("#: " + " ".join(entry.references))
    if entry.flags:
        lines.append("#, " + ", ".join(entry.flags))
    if entry.context:
        lines += _field("msgctxt", entry.context)
    lines += _field("msgid", entry.msgid)
    if entry.plural:
        lines += _field("msgid_plural", entry.plural)
        for index, value in enumerate(entry.strings):
            lines += _field("msgstr[%d]" % index, value)
    else:
        lines += _field("msgstr", entry.strings[0] if entry.strings else "")
    return "\n".join(lines)


def format_catalog(catalog: Catalog) -> str:
    head = ["# " + line if line else "#" for line in catalog.preamble]
    head += ['msgid ""', 'msgstr ""']
    head += [_quote("%s: %s\n" % item) for item in catalog.header.items()]
    parts = ["\n".join(head)] + [format_entry(entry) for entry in catalog.entries]
    return "\n\n".join(parts) + "\n"


def write(path: Path, catalog: Catalog) -> None:
    # newline="\n": the repository keeps text files LF, and Windows would not.
    Path(path).write_text(format_catalog(catalog), encoding="utf-8", newline="\n")


def plural_count(header: Dict[str, str]) -> int:
    """How many plural forms the catalog's language has: 2 unless it says."""
    match = re.search(r"nplurals\s*=\s*(\d+)", header.get("Plural-Forms", ""))
    return int(match.group(1)) if match else 2
