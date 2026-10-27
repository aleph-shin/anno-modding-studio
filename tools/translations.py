"""Keep the translation catalogs in step with the code.

Every sentence the Studio shows is marked in the source with ``tr``, ``trn`` or
``tr_noop``, or ``tr_kw`` and ``trn_kw`` for a sentence whose keywords are shown
as terms (see ``anno_modding_studio/core/i18n.py``).  This finds them all,
refuses the ones that break a rule a translation depends on, and keeps each
catalog in ``anno_modding_studio/locale`` holding exactly the sentences the
code still uses -- new ones added blank, changed ones flagged, gone ones
dropped.  ``docs/standards/translation.md`` has the whole workflow.

    python tools/translations.py update        # bring every catalog up to date
    python tools/translations.py check         # what the test suite checks
    python tools/translations.py lint [FILE]   # English handed to Qt unmarked
    python tools/translations.py todo de       # what is left to translate
    python tools/translations.py apply de F    # write translations from a file
    python tools/translations.py apply de F --check   # only check them
    python tools/translations.py new de "Deutsch" "nplurals=2; plural=(n != 1);"

``todo`` prints one JSON object per line; ``apply`` reads the same shape back
with ``strings`` filled in, so a translation pass never has to hand-edit the
quoting of a ``.po`` file.
"""

from __future__ import annotations

import ast
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

ROOT = Path(__file__).resolve().parent.parent
PACKAGE = ROOT / "anno_modding_studio"
sys.path.insert(0, str(ROOT))

from anno_modding_studio.core import po  # noqa: E402
from anno_modding_studio.core.i18n import LOCALE_DIR, STORED, terms_in  # noqa: E402
from anno_modding_studio.core.keywords import KEYWORDS  # noqa: E402

MARKERS = {"tr", "trn", "tr_noop", "tr_kw", "trn_kw"}
# Each marker, as the sentence it reads: tr_kw is tr's, trn_kw trn's.
_READ_AS = {"tr_kw": "tr", "trn_kw": "trn"}
# What a line says to keep English handed to Qt as it is: a brand, a file
# pattern, a symbol.  Followed by the reason, the way `noqa` is.
KEEP = "i18n: keep"

# A %-placeholder: "%s", "%d", "%.1f", "%(name)s", and the "%%" escape.
_PLACEHOLDER = re.compile(
    r"%(?:\((?P<name>[^)]*)\))?[#0\- +]*(?:\*|\d+)?(?:\.(?:\*|\d+))?(?P<kind>[diouxXeEfFgGcrsa%])"
)
# The unambiguous ones, for text with nothing to fill it: "50% sure" is not a
# placeholder, "%s" and "%(mod)s" certainly are.
_PLAIN_PLACEHOLDER = re.compile(r"%(?:\(\w+\))?[sd]")


@dataclass
class Message:
    msgid: str
    context: str = ""
    plural: str = ""
    formatted: bool = False
    references: List[str] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)
    # (path, line, column) of the first use, for ordering the catalog.
    first: Tuple[str, int, int] = ("", 0, 0)

    @property
    def key(self) -> Tuple[str, str]:
        return (self.context, self.msgid)


@dataclass
class Problem:
    path: str
    line: int
    text: str

    def __str__(self) -> str:
        return "%s:%d: %s" % (self.path, self.line, self.text)


def placeholders(text: str) -> Tuple[List[str], List[str]]:
    """``(named, positional)`` placeholders in ``text``, ``%%`` left out."""
    named, positional = [], []
    for match in _PLACEHOLDER.finditer(text):
        if match.group("kind") == "%":
            continue
        if match.group("name") is not None:
            named.append(match.group(0))
        else:
            positional.append(match.group(0))
    return named, positional


def _names(found: Iterable[str]) -> set:
    return {_PLACEHOLDER.match(item).group("name") for item in found}


# -- finding the marked sentences -------------------------------------------------------
def _marker(call: ast.Call) -> Optional[str]:
    func = call.func
    if isinstance(func, ast.Name) and func.id in MARKERS:
        return func.id
    if (isinstance(func, ast.Attribute) and func.attr in MARKERS
            and isinstance(func.value, ast.Name) and func.value.id == "i18n"):
        return func.attr
    return None


def _string(node: Optional[ast.AST]) -> Optional[str]:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


class _Finder:
    def __init__(self, path: str, source: str):
        self.path = path
        self.lines = source.splitlines()
        self.messages: List[Message] = []
        self.problems: List[Problem] = []

    def problem(self, node: ast.AST, text: str) -> None:
        self.problems.append(Problem(self.path, getattr(node, "lineno", 0), text))

    # Walked by hand rather than with ast.walk, to know at each call whether it
    # runs at import time -- before any language can have been installed.
    def visit(self, node: ast.AST, running: bool) -> None:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for part in [*node.decorator_list, node.args, node.returns]:
                if part is not None:
                    self.visit(part, running)
            for statement in node.body:
                self.visit(statement, True)
            return
        if isinstance(node, ast.Lambda):
            self.visit(node.args, running)
            self.visit(node.body, True)
            return
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Mod) \
                and isinstance(node.left, ast.Call) \
                and _marker(node.left) in ("tr", "trn", "tr_kw", "trn_kw"):
            self.problem(node, "format through tr()'s own arguments, not with % afterwards")
        if isinstance(node, ast.Call):
            self.call(node, running)
        for child in ast.iter_child_nodes(node):
            self.visit(child, running)

    def call(self, call: ast.Call, running: bool) -> None:
        func = call.func
        kind = _marker(call)
        kind = _READ_AS.get(kind, kind)
        if kind is None:
            if isinstance(func, ast.Attribute) and func.attr in ("tr", "translate") and (
                    func.attr == "tr" or getattr(func.value, "id", "") == "QCoreApplication"):
                self.problem(call, "Qt's own %s() is not what the catalogs hold; "
                                   "use i18n.tr()" % func.attr)
            return
        if kind != "tr_noop" and not running:
            self.problem(call, "%s() at import time runs before any language is "
                               "installed; mark with tr_noop() and translate where "
                               "it is shown" % kind)
        keywords = {kw.arg: kw.value for kw in call.keywords}
        context = ""
        if "context" in keywords:
            given = keywords.pop("context")
            context = _string(given)
            # The one context named by a constant: English kept as it is.
            if getattr(given, "id", getattr(given, "attr", None)) == "STORED":
                context = STORED
            if context is None:
                self.problem(call, "context must be a literal string")
                return
        if kind == "tr_noop":
            self.noop(call, context)
        elif kind == "tr":
            self.tr(call, context, keywords)
        else:
            self.trn(call, context, keywords)

    def _unreadable(self, call: ast.Call, node: Optional[ast.AST]) -> bool:
        """Complain about a sentence built in a way no catalog can hold."""
        if isinstance(node, ast.JoinedStr):
            self.problem(call, "an f-string cannot be translated; use placeholders")
        elif isinstance(node, (ast.BinOp, ast.IfExp)) or (
                isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr in ("format", "join")):
            self.problem(call, "a sentence built before tr() sees it cannot be "
                               "translated; pass whole sentences")
        else:
            return False
        return True

    def noop(self, call: ast.Call, context: str) -> None:
        text = _string(call.args[0]) if call.args else None
        if text is None:
            self.problem(call, "tr_noop() needs a literal string")
            return
        formatted = False
        if context == STORED:
            # Kept English, filled in with % where it is made and recognised
            # again where it is shown: the same rules as a sentence tr() fills.
            named, positional = placeholders(text)
            formatted = bool(named or positional)
            if len(positional) > 1:
                self.problem(call, "two placeholders need names, so a translation "
                                   "can put them in its own order: %r" % text)
            if named and positional:
                self.problem(call, "mixes named and positional placeholders: %r" % text)
        self.add(call, text, context, formatted=formatted)

    def tr(self, call: ast.Call, context: str, keywords: Dict) -> None:
        if not call.args:
            self.problem(call, "tr() needs the text")
            return
        text = _string(call.args[0])
        if text is None:
            # A variable holding tr_noop()-marked text is looked up as it is.
            self._unreadable(call, call.args[0])
            return
        args = call.args[1:]
        starred = any(isinstance(arg, ast.Starred) for arg in args) or None in keywords
        formatted = bool(args or keywords)
        named, positional = placeholders(text) if formatted else ([], [])
        if not formatted and _PLAIN_PLACEHOLDER.search(text):
            self.problem(call, "has a placeholder but nothing to fill it: %r" % text)
        if len(positional) > 1:
            self.problem(call, "two placeholders need names, so a translation "
                               "can put them in its own order: %r" % text)
        if named and positional:
            self.problem(call, "mixes named and positional placeholders: %r" % text)
        if args and keywords:
            self.problem(call, "fill in with positional or named values, not both")
        if not starred:
            if args and len(args) != len(positional):
                self.problem(call, "%d value(s) for %d placeholder(s): %r"
                             % (len(args), len(positional), text))
            if keywords and set(keywords) != _names(named):
                self.problem(call, "values %s do not match placeholders %s: %r"
                             % (sorted(keywords), sorted(_names(named)), text))
        self.add(call, text, context, formatted=formatted)

    def trn(self, call: ast.Call, context: str, keywords: Dict) -> None:
        texts = [_string(arg) for arg in call.args[:2]]
        if len(call.args) != 3 or None in texts:
            if not any(self._unreadable(call, arg) for arg in call.args[:2]):
                self.problem(call, "trn() takes the two literal forms and the count; "
                                   "anything else goes in by name")
            return
        singular, plural = texts
        names = set()
        for text in (singular, plural):
            named, positional = placeholders(text)
            if positional:
                self.problem(call, "trn() fills in by name only -- the count is "
                                   "%%(n)s: %r" % text)
            for item in named:
                if _names([item]) == {"n"} and item != "%(n)s":
                    self.problem(call, "the count arrives already written out: "
                                       "use %%(n)s, not %s" % item)
            names |= _names(named)
        if None not in keywords and set(keywords) | {"n"} != names | {"n"}:
            self.problem(call, "values %s do not match placeholders %s: %r"
                         % (sorted(keywords), sorted(names - {"n"}), singular))
        self.add(call, singular, context, plural=plural, formatted=True)

    def add(self, call: ast.Call, text: str, context: str, plural: str = "",
            formatted: bool = False) -> None:
        for said in (text, plural):
            unknown = sorted(set(terms_in(said)) - set(KEYWORDS))
            if unknown:
                self.problem(call, "%s is no keyword (core/keywords.py): %r"
                             % (", ".join(unknown), said))
            if context == STORED and terms_in(said):
                self.problem(call, "English kept as it is is written to files, "
                                   "and a keyword's mark would be written with it: %r" % said)
        self.messages.append(Message(
            msgid=text, context=context, plural=plural, formatted=formatted,
            references=[self.path], notes=self.notes_for(call.lineno),
            first=(self.path, call.lineno, call.col_offset),
        ))

    def notes_for(self, line: int) -> List[str]:
        """A ``# Translators:`` comment right above the call, if there is one."""
        block = []
        index = line - 2
        while index >= 0 and self.lines[index].strip().startswith("#"):
            block.insert(0, self.lines[index].strip().lstrip("#").strip())
            index -= 1
        for start, text in enumerate(block):
            if text.startswith("Translators:"):
                return [" ".join(block[start:])]
        return []


def sources(root: Path = PACKAGE) -> List[Path]:
    return sorted(root.rglob("*.py"))


def relative(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT).as_posix()
    except ValueError:
        return path.as_posix()


def extract(paths: Optional[Sequence[Path]] = None) -> Tuple[List[Message], List[Problem]]:
    """Every marked sentence in ``paths`` (the whole package by default)."""
    found: Dict[Tuple[str, str], Message] = {}
    problems: List[Problem] = []
    for path in paths if paths is not None else sources():
        source = Path(path).read_text(encoding="utf-8")
        finder = _Finder(relative(path), source)
        finder.visit(ast.parse(source), False)
        problems += finder.problems
        for message in sorted(finder.messages, key=lambda m: m.first):
            known = found.get(message.key)
            if known is None:
                found[message.key] = message
                continue
            if known.plural != message.plural:
                problems.append(Problem(message.first[0], message.first[1],
                                        "used with two different plurals: %r" % message.msgid))
            known.formatted = known.formatted or message.formatted
            for reference in message.references:
                if reference not in known.references:
                    known.references.append(reference)
            for note in message.notes:
                if note not in known.notes:
                    known.notes.append(note)
    return list(found.values()), problems


# -- keeping the catalogs in step -------------------------------------------------------
def catalogs() -> List[Path]:
    return sorted(LOCALE_DIR.glob("*.po"))


def merged(catalog: po.Catalog, messages: Sequence[Message]) -> po.Catalog:
    """``catalog`` holding exactly ``messages``, keeping what was translated."""
    old = catalog.by_key()
    forms = po.plural_count(catalog.header)
    entries = []
    for message in messages:
        before = old.get(message.key)
        flags = ["python-format"] if message.formatted else []
        strings = [""] * forms if message.plural else [""]
        comments: List[str] = []
        if before is not None:
            comments = before.comments
            if bool(before.plural) == bool(message.plural):
                strings = (before.strings + [""] * len(strings))[:len(strings)]
                if before.fuzzy or before.plural != message.plural:
                    flags.append("fuzzy")
        entries.append(po.Entry(
            msgid=message.msgid, context=message.context, plural=message.plural,
            strings=strings, flags=flags, notes=list(message.notes),
            references=list(message.references), comments=comments,
        ))
    return po.Catalog(header=dict(catalog.header), preamble=list(catalog.preamble),
                      entries=entries)


def update(messages: Optional[Sequence[Message]] = None) -> List[Path]:
    """Rewrite each catalog that is out of step; the ones that changed."""
    if messages is None:
        messages, problems = extract()
        if problems:
            raise SystemExit("\n".join(map(str, problems)))
    changed = []
    for path in catalogs():
        text = path.read_text(encoding="utf-8")
        fresh = po.format_catalog(merged(po.parse(text), messages))
        if fresh != text:
            path.write_text(fresh, encoding="utf-8", newline="\n")
            changed.append(path)
    return changed


def mismatched(entry: po.Entry) -> List[str]:
    """How each translation of ``entry`` fails to fill what its English does."""
    if "python-format" not in entry.flags or not entry.translated:
        return []
    wrong = []
    sources_ = [entry.msgid] + ([entry.plural] if entry.plural else [])
    each = [_names(placeholders(text)[0]) for text in sources_]
    named = set().union(*each)
    positional = max(len(placeholders(text)[1]) for text in sources_)
    for index, text in enumerate(entry.strings):
        has_named, has_positional = placeholders(text)
        got = _names(has_named)
        # A plural form must keep what both English forms say, and may leave
        # out the count ("Ein Mod") or what only one of them says -- it may
        # never add a name.
        wanted = set.intersection(*each) - {"n"} if entry.plural else named
        if not wanted <= got or not got <= named or len(has_positional) != positional:
            wrong.append("form %d %r does not fill what %r does" % (index, text, entry.msgid))
    return wrong


def unkept_terms(entry: po.Entry) -> List[str]:
    """How each translation of ``entry`` marks other keywords than its English.

    Each form marks the same keywords, as many times, as the English does: a
    keyword left unmarked in German would be one the reader cannot point at.
    """
    if not entry.translated:
        return []
    wanted = sorted(terms_in(entry.msgid))
    if entry.plural and not wanted:
        wanted = sorted(terms_in(entry.plural))
    return [
        "form %d %r does not mark the keywords %r does" % (index, text, entry.msgid)
        for index, text in enumerate(entry.strings) if sorted(terms_in(text)) != wanted
    ]


def check() -> List[str]:
    """Everything wrong, as sentences; empty when all is well."""
    messages, problems = extract()
    said = [str(problem) for problem in problems]
    for path in catalogs():
        text = path.read_text(encoding="utf-8")
        catalog = po.parse(text)
        if po.format_catalog(merged(catalog, messages)) != text:
            said.append("%s is out of step with the code: run "
                        "python tools/translations.py update" % relative(path))
        for entry in catalog.entries:
            said += ["%s: %s" % (relative(path), line) for line in mismatched(entry)]
            said += ["%s: %s" % (relative(path), line) for line in unkept_terms(entry)]
    return said


def progress(path: Path) -> Tuple[int, int, int]:
    """``(translated, fuzzy, total)`` for one catalog."""
    entries = po.read(path).entries
    return (sum(e.translated for e in entries), sum(e.fuzzy for e in entries), len(entries))


# -- English handed straight to Qt ------------------------------------------------------------
# Qt calls whose arguments are shown to the reader, and which of them.  None
# means every string argument; the static dialogs take a parent first.
TEXT_TAKERS: Dict[str, Optional[Tuple[int, ...]]] = {
    **dict.fromkeys(("setText", "setToolTip", "setStatusTip", "setWhatsThis", "setWindowTitle", "setPlaceholderText", "setTitle", "setLabelText", "setInformativeText", "setDetailedText", "showMessage", "setHeaderLabels", "setHorizontalHeaderLabels", "setVerticalHeaderLabels", "addItem", "addItems", "insertItem", "setSpecialValueText", "setSuffix", "setPrefix", "setOkButtonText", "setCancelButtonText", "QLabel", "QPushButton", "QToolButton", "QCheckBox", "QRadioButton", "QGroupBox", "QMenu", "QTreeWidgetItem", "QListWidgetItem", "QStandardItem", "QCommandLinkButton"), (0,)),
    **dict.fromkeys(("setTabText", "setTabToolTip", "setItemText", "setItemToolTip", "setButtonText"), (1,)),
    "addTab": (1, 2), "insertTab": (2, 3), "addButton": (0,),
    "addAction": None, "addMenu": None, "addSection": None, "QAction": None,
    **dict.fromkeys(("information", "warning", "critical", "question", "about", "getText", "getItem", "getInt", "getDouble", "getMultiLineText"), (1, 2)),
    **dict.fromkeys(("getOpenFileName", "getOpenFileNames", "getSaveFileName"), (1, 3)),
    "getExistingDirectory": (1,),
}
_WORDS = re.compile(r"[A-Za-z]{2,}")


def _bare(node: ast.AST) -> List[ast.AST]:
    """The literal English inside one argument, looking through ``%`` and ``if``."""
    if isinstance(node, ast.Call) and _marker(node):
        return []
    if isinstance(node, ast.Constant):
        return [node] if isinstance(node.value, str) and _WORDS.search(node.value) else []
    if isinstance(node, ast.JoinedStr):
        return [node] if any(_bare(value) for value in node.values) else []
    if isinstance(node, ast.BinOp):
        return _bare(node.left) + _bare(node.right)
    if isinstance(node, ast.IfExp):
        return _bare(node.body) + _bare(node.orelse)
    if isinstance(node, (ast.List, ast.Tuple)):
        return [found for item in node.elts for found in _bare(item)]
    return []


def lint(paths: Optional[Sequence[Path]] = None) -> List[Problem]:
    """English handed to a Qt text setter without going through ``tr``.

    A safety net, not a proof: a sentence put in a variable first, or built by
    a helper, is not seen.  Mark a line ``# i18n: keep - <reason>`` to say its
    English is meant (a brand name, a file pattern).
    """
    problems = []
    if paths is None:
        paths = [path for path in sources() if "core" not in path.parts]
    for path in paths:
        source = Path(path).read_text(encoding="utf-8")
        lines = source.splitlines()
        for node in ast.walk(ast.parse(source)):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
            if name not in TEXT_TAKERS:
                continue
            spots = TEXT_TAKERS[name]
            args = [arg for index, arg in enumerate(node.args)
                    if spots is None or index in spots]
            args += [kw.value for kw in node.keywords if kw.arg in ("text", "title", "toolTip")]
            span = "\n".join(lines[node.lineno - 1:(node.end_lineno or node.lineno)])
            if KEEP in span:
                continue
            for arg in args:
                for literal in _bare(arg):
                    problems.append(Problem(
                        relative(Path(path)), literal.lineno,
                        "English shown without tr(): %s" % ast.unparse(literal)[:70],
                    ))
    return problems


# -- the command line --------------------------------------------------------------------------
def todo(code: str) -> List[dict]:
    """What is left to translate in ``code``'s catalog, fuzzy ones with their old words."""
    out = []
    for entry in po.read(LOCALE_DIR / ("%s.po" % code)).entries:
        if entry.translated:
            continue
        item = {"context": entry.context, "msgid": entry.msgid}
        if entry.plural:
            item["plural"] = entry.plural
        item["references"] = entry.references
        if entry.notes:
            item["notes"] = entry.notes
        if entry.fuzzy:
            item["previous"] = entry.strings
        item["format"] = "python-format" in entry.flags
        item["strings"] = []
        out.append(item)
    return out


def apply(code: str, items: Iterable[dict], write: bool = True) -> Tuple[int, List[str]]:
    """Write ``items`` (from :func:`todo`, ``strings`` filled) into the catalog.

    ``write=False`` only checks them: what would be written, and what refused.
    """
    path = LOCALE_DIR / ("%s.po" % code)
    catalog = po.read(path)
    entries = catalog.by_key()
    written, refused = 0, []
    for item in items:
        entry = entries.get((item.get("context", ""), item["msgid"]))
        strings = item.get("strings") or []
        if entry is None:
            refused.append("no such sentence: %r" % item["msgid"])
            continue
        if not strings or not all(strings) or len(strings) != len(entry.strings):
            refused.append("needs %d filled string(s): %r" % (len(entry.strings), item["msgid"]))
            continue
        trial = po.Entry(msgid=entry.msgid, plural=entry.plural, strings=list(strings),
                         flags=[flag for flag in entry.flags if flag != "fuzzy"])
        wrong = mismatched(trial) + unkept_terms(trial)
        if wrong:
            refused += wrong
            continue
        entry.strings = list(strings)
        entry.flags = trial.flags
        written += 1
    if write:
        po.write(path, catalog)
    return written, refused


PSEUDO_OPEN, PSEUDO_CLOSE = "⟦", "⟧"
# Marks of punctuation rather than words: bracketing them would only garble
# every number and list, and say nothing about what was missed.
_PSEUDO_SKIP = {"digit grouping mark", "decimal mark", "between items of a list"}


def pseudo(folder: Path, code: str = "qps") -> Path:
    """A catalog that 'translates' every sentence into ⟦itself⟧, in ``folder``.

    Installed, it shows at a glance what the catalogs do not reach: anything on
    screen outside the brackets never went through ``tr``.  Somewhat longer
    than English, like the German it stands in for.
    """
    messages, problems = extract()
    if problems:
        raise SystemExit("\n".join(map(str, problems)))
    entries = []
    for message in messages:
        forms = [message.msgid, message.plural] if message.plural else [message.msgid]
        if message.context not in _PSEUDO_SKIP:
            forms = [PSEUDO_OPEN + form + PSEUDO_CLOSE for form in forms]
        entries.append(po.Entry(msgid=message.msgid, context=message.context,
                                plural=message.plural, strings=forms))
    catalog = po.Catalog(header={
        "Language": code, "Content-Type": "text/plain; charset=UTF-8",
        "Plural-Forms": "nplurals=2; plural=(n != 1);",
        "X-Language-Label": "Pseudo",
    }, entries=entries)
    path = Path(folder) / ("%s.po" % code)
    po.write(path, catalog)
    return path


def new(code: str, label: str, plural_forms: str) -> Path:
    path = LOCALE_DIR / ("%s.po" % code)
    if path.exists():
        raise SystemExit("%s already exists" % relative(path))
    LOCALE_DIR.mkdir(parents=True, exist_ok=True)
    catalog = po.Catalog(
        preamble=["The Studio's own words, in the language coded %s. Kept in step" % code,
                  "with the code by tools/translations.py; see docs/standards/translation.md."],
        header={
            "Project-Id-Version": "Anno Modding Studio",
            "Language": code,
            "MIME-Version": "1.0",
            "Content-Type": "text/plain; charset=UTF-8",
            "Content-Transfer-Encoding": "8bit",
            "Plural-Forms": plural_forms,
            "X-Language-Label": label,
        },
    )
    po.write(path, catalog)
    update()
    return path


def main(argv: Sequence[str]) -> int:
    if not argv:
        print(__doc__)
        return 2
    command, rest = argv[0], list(argv[1:])
    if command == "update":
        for path in update():
            print("updated", relative(path))
        for path in catalogs():
            done, fuzzy, total = progress(path)
            print("%s: %d of %d translated, %d to look at again" % (path.stem, done, total, fuzzy))
        return 0
    if command == "check":
        said = check()
        print("\n".join(said) or "catalogs are in step and every translation fits")
        return 1 if said else 0
    if command == "lint":
        found = lint([Path(item) for item in rest] or None)
        print("\n".join(map(str, found)) or "nothing shown without tr()")
        return 1 if found else 0
    if command == "todo" and len(rest) == 1:
        for item in todo(rest[0]):
            print(json.dumps(item, ensure_ascii=False))
        return 0
    if command == "apply" and len(rest) in (2, 3) and rest[2:] in ([], ["--check"]):
        lines = Path(rest[1]).read_text(encoding="utf-8").splitlines()
        write = rest[2:] != ["--check"]
        written, refused = apply(rest[0], (json.loads(line) for line in lines if line.strip()),
                                 write=write)
        print("%s %d translation(s)" % ("wrote" if write else "would write", written))
        print("\n".join(refused))
        return 1 if refused else 0
    if command == "new" and len(rest) == 3:
        print("made", relative(new(*rest)))
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main(sys.argv[1:]))
