"""Find what the interface's source does against the design standards.

    python tools/design_check.py           the count for every rule, 1 if any is not 0
    python tools/design_check.py <rule>    that rule's findings, path:line: text

``docs/standards/design-standards.md`` says how the Studio's interface is written and
drawn. This tool checks the parts of it that can be read off the source. It
parses the files with ``ast`` and never imports the application, so it runs in
a second and needs no Qt. ``tools/check.py`` runs it and allows no finding.
The rules began as counts held where they were, 648 findings in all. The
design review brought each to zero (review pass 9), and since its close a
single finding fails.

Code rules read ``anno_modding_studio/ui/*.py``. The voice rules read the
strings marked for translation in ``ui/`` and ``core/``, because core writes
sentences the interface shows.

The rules, by key:

``colour-literal``
    A colour written down outside ``ui/theme.py``: a ``"#rgb"`` or
    ``"#rrggbb"`` string, ``QColor`` called with literal arguments, or a
    ``Qt.GlobalColor`` other than ``transparent``. Colours belong to a scheme.
``stylesheet``
    A ``setStyleSheet`` call outside ``ui/theme.py``.
``double-click-only``
    ``itemDoubleClicked`` or ``doubleClicked`` connected on an object that has
    no ``itemActivated`` or ``activated`` connected in the same file. Such a
    list cannot be used from the keyboard.
``yes-no-box``
    A ``QMessageBox`` call that offers Yes and No, or OK and Cancel. A box
    with a single OK is not counted.
``long-label``
    A ``QPushButton``, ``QCheckBox`` or ``QRadioButton`` built with literal
    text over 4 words.
``long-help``
    A ``HelpTip`` built with literal text over 80 words.
``long-tooltip``
    ``setToolTip`` with literal text over 15 words, and the same for a
    button's tip in a question: ``tip(button, …)``, a ``choose`` choice's
    tip, and ``ask``'s ``action_tip`` and ``cancel_tip``.
``long-question``
    The text of a message box over 40 words, where the source spells it
    out: what ``ask``, ``choose`` and ``tell`` are given, ``show_box_text``,
    and ``QMessageBox.information``, ``warning``, ``critical`` and
    ``question``. What each answer does goes on its button's tip.
``box-quotes-button``
    A marked string that quotes, in curly quotes, a button the same function
    makes: “Add to loadout” reads… in the text of the box that has *Add to
    loadout*. Say it on the button's tip instead.
``repeated-value``
    A marked string that fills the same named value in twice, as
    ``%(mod)s … %(mod)s``. A name says itself once a sentence or two, and
    "it" after that. A value inside markup, such as a link's address, is
    not counted.
``voice-dash``, ``voice-semicolon``, ``voice-rather-than``, ``voice-nothing``,
``voice-says``, ``voice-exactly``, ``voice-simply``
    A marked string that has an em-dash or a spaced double hyphen, a
    semicolon, "rather than", a sentence opening with "Nothing", the Studio,
    the loader or a hint as the subject of says, asks, knows or tells,
    "exactly", or "simply".
``label-no-buddy``
    A ``QLabel`` whose text ends in a colon and that never has ``setBuddy``
    called on it: a screen reader cannot tell which field it names. Also an
    ``addRow`` whose string label has no mnemonic. Qt gives that label its
    buddy, and the mnemonic is what the keyboard reaches it by.
``raster-image``
    A raster image under ``resources/`` other than the application icon. This
    one is read off the folder and not off the source.

A ``long-label``, ``long-help``, ``long-question`` or ``repeated-value``
finding is waived by a comment on the same statement that starts
``# design:`` and gives the reason. So is a
``label-no-buddy`` finding on a ``QLabel`` whose value is only read, which
has no field to be the buddy of.

What is approximate:

- Consequence lines have a budget of 20 words and are not checked. Nothing in
  the source tells a consequence line from any other label yet.
- Only literal text is measured. Text built at run time is left to the window
  walk in ``tests/test_design.py``. A box whose text is joined from a list of
  sentences is measured a sentence at a time, if at all.
- ``box-quotes-button`` sees the buttons a function makes with
  ``addButton``, ``QPushButton``, ``choose`` and ``ask``. A button made
  somewhere else and quoted is left alone, since the quote may be pointing
  the reader at it.
- A word is anything between spaces that holds a letter or a digit, so a
  placeholder such as ``%s`` counts as one word.
- ``label-no-buddy`` cannot see whether an ``addRow`` receiver is a form
  layout or whether a label sits beside a field. It counts every ``addRow``
  with a string label that has no ``&`` and every ``QLabel`` ending in a
  colon without a buddy set in the same function.
- ``yes-no-box`` cannot see whether the action has a name. It counts every box
  that offers the generic pair.
"""

from __future__ import annotations

import ast
import re
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "anno_modding_studio"
THEME = "anno_modding_studio/ui/theme.py"

VOICE_RULES = (
    "voice-dash", "voice-semicolon", "voice-rather-than", "voice-nothing",
    "voice-says", "voice-exactly", "voice-simply",
)
# Every rule read off the source, in the order the counts are printed.
RULES = (
    "colour-literal", "stylesheet", "status-colour-alone", "double-click-only", "yes-no-box",
    "long-label", "long-help", "long-tooltip", "long-question", "box-quotes-button",
    *VOICE_RULES, "repeated-value", "label-no-buddy",
)
FILE_RULES = ("raster-image",)

LABEL_WORDS = 4
HELP_WORDS = 80
TOOLTIP_WORDS = 15
QUESTION_WORDS = 40

RASTER_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".ico"}
APP_ICONS = {"icon.ico", "icon.png"}

# The functions of core/i18n.py that mark a string for translation, with how
# many of their leading arguments are text.
MARKERS = {"tr": 1, "tr_noop": 1, "trn": 2, "tr_kw": 1, "trn_kw": 2}
LABEL_WIDGETS = {"QPushButton", "QCheckBox", "QRadioButton"}
DOUBLE_CLICK = {"itemDoubleClicked", "doubleClicked"}
ACTIVATED = {"itemActivated", "activated"}
BOX_CALLS = {"question", "warning", "information", "critical"}
# The calls that put text in a message box, with where the text is among
# their arguments. ``BOX_CALLS`` count only when called on ``QMessageBox``.
BOX_TEXT = {"ask": 2, "choose": 2, "tell": 2, "show_box_text": 1,
            **dict.fromkeys(BOX_CALLS, 2)}
TIP_KEYWORDS = {"action_tip", "cancel_tip"}
_NAMED_VALUE = re.compile(r"%\((\w+)\)[sd]")
_QUOTED = re.compile(r"“([^”]+)”")

_HEX = r"#(?:[0-9a-fA-F]{8}|[0-9a-fA-F]{6}|[0-9a-fA-F]{3})"
_HEX_ALONE = re.compile(_HEX + r"\Z")
_HEX_INSIDE = re.compile(r"(?<![\w&#])" + _HEX + r"(?![\w#])")
_TAG = re.compile(r"<[^<>]+>")
_ENTITY = re.compile(r"&#?\w+;")
_WAIVER = re.compile(r"#\s*design:\s*\S")

_VOICE = {
    "voice-dash": re.compile(r"—|(?<=\s)--(?=\s)"),
    "voice-semicolon": re.compile(r";"),
    "voice-rather-than": re.compile(r"\brather\s+than\b", re.IGNORECASE),
    "voice-nothing": re.compile(r"(?:\A|[.!?:]\s+|\n)\s*Nothing\b"),
    "voice-says": re.compile(
        r"\b(?:studio|loader|hint)\s+(?:says|asks|knows|tells)\b", re.IGNORECASE),
    "voice-exactly": re.compile(r"\bexactly\b", re.IGNORECASE),
    "voice-simply": re.compile(r"\bsimply\b", re.IGNORECASE),
}


class Source:
    """One file's text, parsed once and shared by every rule."""

    def __init__(self, text: str):
        self.text = text
        self.lines = text.splitlines()
        self.tree = ast.parse(text)
        self._parents: dict | None = None

    @property
    def parents(self) -> dict:
        if self._parents is None:
            self._parents = {
                child: node for node in ast.walk(self.tree)
                for child in ast.iter_child_nodes(node)
            }
        return self._parents

    def line(self, node: ast.AST) -> str:
        """The first source line of ``node``, for showing in a finding."""
        number = getattr(node, "lineno", 0)
        return self.lines[number - 1].strip() if 0 < number <= len(self.lines) else ""

    def waived(self, node: ast.AST) -> bool:
        """Whether a ``# design:`` comment with a reason sits on ``node``'s lines."""
        first = getattr(node, "lineno", 0)
        last = getattr(node, "end_lineno", first) or first
        return any(_WAIVER.search(line) for line in self.lines[first - 1:last])


def _source(source) -> Source:
    return source if isinstance(source, Source) else Source(source)


def _called(node: ast.Call) -> str:
    """The last name of what a call calls: ``tr`` for ``tr()`` and ``i18n.tr()``."""
    func = node.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return ""


def literal_text(node: ast.AST | None) -> str | None:
    """The text ``node`` spells out in the source, or None where it has none.

    Reads a string constant, literals joined with ``+``, the left side of a
    ``%`` format, and the first argument of a translation marker. For ``trn``
    it gives the longer of the two forms.
    """
    if isinstance(node, ast.Constant):
        return node.value if isinstance(node.value, str) else None
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left, right = literal_text(node.left), literal_text(node.right)
        return left + right if left is not None and right is not None else None
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Mod):
        return literal_text(node.left)
    if isinstance(node, ast.Call) and _called(node) in MARKERS:
        texts = [literal_text(arg) for arg in node.args[:MARKERS[_called(node)]]]
        if not texts or any(text is None for text in texts):
            return None
        return max(texts, key=word_count)
    return None


def word_count(text: str) -> int:
    """The words in ``text``: markup and mnemonic marks are not words."""
    plain = _ENTITY.sub(" ", _TAG.sub(" ", text)).replace("&", "")
    return sum(1 for word in plain.split() if any(char.isalnum() for char in word))


def _short(text: str, most: int = 90) -> str:
    text = " ".join(text.split())
    return text if len(text) <= most else text[:most - 1] + "…"


def _calls(tree: ast.AST):
    return (node for node in ast.walk(tree) if isinstance(node, ast.Call))


def _docstrings(tree: ast.AST) -> set:
    """The ids of the constants that are docstrings or bare string statements."""
    return {
        id(node.value) for node in ast.walk(tree)
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant)
    }


# -- the rules --------------------------------------------------------------------------
# Each takes a file's source, as text or as a Source, and returns its findings
# as (line number, text to show), in the order they stand in the file.

def colour_literals(source) -> list:
    src = _source(source)
    skip = _docstrings(src.tree)
    found = []
    for node in ast.walk(src.tree):
        if isinstance(node, ast.Call) and _called(node) == "QColor" and node.args:
            texts = [arg for arg in node.args if isinstance(arg, ast.Constant)]
            if len(texts) == len(node.args):
                # QColor("#fff"), QColor("red") and QColor(255, 0, 0) alike.
                skip.update(id(arg) for arg in texts)
                found.append((node.lineno, src.line(node)))
        elif (isinstance(node, ast.Attribute) and node.attr != "transparent"
              and isinstance(node.value, ast.Attribute) and node.value.attr == "GlobalColor"):
            found.append((node.lineno, src.line(node)))
    for node in ast.walk(src.tree):
        if (isinstance(node, ast.Constant) and isinstance(node.value, str)
                and id(node) not in skip
                and (_HEX_ALONE.match(node.value) or _HEX_INSIDE.search(node.value))):
            found.append((node.lineno, src.line(node)))
    return sorted(found)


def stylesheets(source) -> list:
    src = _source(source)
    return sorted(
        (node.lineno, src.line(node)) for node in _calls(src.tree)
        if isinstance(node.func, ast.Attribute) and node.func.attr == "setStyleSheet"
    )


def status_colour_alone(source) -> list:
    """An item given the warning or error colour with ``setForeground``.

    That shows a status by colour alone. ``theme.mark(item, "warn")`` sets
    the colour and the glyph together.
    """
    src = _source(source)
    found = []
    for node in _calls(src.tree):
        if not (isinstance(node.func, ast.Attribute) and node.func.attr == "setForeground"):
            continue
        said = " ".join(ast.unparse(arg) for arg in node.args)
        if re.search(r"\btheme\.(WARN|ERROR)\b", said):
            found.append((node.lineno, src.line(node)))
    return sorted(found)


def double_click_only(source) -> list:
    src = _source(source)
    double, activated = [], set()
    for node in _calls(src.tree):
        func = node.func
        # ``enter_activates(view, slot)`` gives the keyboard its own answer.
        if (isinstance(func, ast.Name) and func.id == "enter_activates"
                and len(node.args) > 1):
            activated.add(ast.unparse(node.args[0]))
            continue
        if not (isinstance(func, ast.Attribute) and func.attr == "connect"
                and isinstance(func.value, ast.Attribute)):
            continue
        receiver = ast.unparse(func.value.value)
        if func.value.attr in DOUBLE_CLICK:
            double.append((node.lineno, src.line(node), receiver))
        elif func.value.attr in ACTIVATED:
            activated.add(receiver)
    return sorted((line, text) for line, text, receiver in double if receiver not in activated)


def _box_buttons(node: ast.Call) -> set:
    """The ``QMessageBox.StandardButton`` names in a call's arguments."""
    names = set()
    for arg in [*node.args, *(keyword.value for keyword in node.keywords)]:
        for part in ast.walk(arg):
            if (isinstance(part, ast.Attribute) and isinstance(part.value, ast.Attribute)
                    and part.value.attr == "StandardButton"
                    and ast.unparse(part.value.value).endswith("QMessageBox")):
                names.add(part.attr)
    return names


def yes_no_boxes(source) -> list:
    src = _source(source)
    found = []
    for node in _calls(src.tree):
        func = node.func
        static = (isinstance(func, ast.Attribute) and func.attr in BOX_CALLS
                  and ast.unparse(func.value).endswith("QMessageBox"))
        built = _called(node) == "QMessageBox"
        setter = isinstance(func, ast.Attribute) and func.attr == "setStandardButtons"
        if not (static or built or setter):
            continue
        buttons = _box_buttons(node)
        generic = bool(buttons & {"Yes", "No"}) or {"Ok", "Cancel"} <= buttons
        # A question box that names no buttons gets Qt's own Yes and No.
        if generic or (static and func.attr == "question" and not buttons):
            found.append((node.lineno, src.line(node)))
    return sorted(found)


def _over_budget(src: Source, names: set, budget: int) -> list:
    found = []
    for node in _calls(src.tree):
        if _called(node) not in names or src.waived(node):
            continue
        values = [*node.args, *(keyword.value for keyword in node.keywords)]
        text = next((t for t in map(literal_text, values) if t is not None), None)
        if text is not None and word_count(text) > budget:
            found.append((node.lineno, _short(text)))
    return sorted(found)


def long_labels(source) -> list:
    return _over_budget(_source(source), LABEL_WIDGETS, LABEL_WORDS)


def long_help(source) -> list:
    return _over_budget(_source(source), {"HelpTip"}, HELP_WORDS)


def _argument(node: ast.Call, at: int, keyword: str = ""):
    """A call's argument by place, or by keyword where it is passed so."""
    if len(node.args) > at:
        return node.args[at]
    return next((k.value for k in node.keywords if keyword and k.arg == keyword), None)


def _choice_tips(node: ast.Call) -> list:
    """The tips in a ``choose`` call's ``[(label, tip), ...]``, as nodes."""
    choices = _argument(node, 3, "choices")
    if choices is None:
        return []
    return [part.elts[1] for part in ast.walk(choices)
            if isinstance(part, ast.Tuple) and len(part.elts) >= 2]


def _tips(node: ast.Call) -> list:
    """The tooltip texts a call sets, as nodes."""
    if isinstance(node.func, ast.Attribute) and node.func.attr == "setToolTip":
        return node.args[:1]
    called = _called(node)
    if called == "tip":
        return node.args[1:2]
    if called == "choose":
        return _choice_tips(node)
    if called == "ask":
        return [k.value for k in node.keywords if k.arg in TIP_KEYWORDS]
    return []


def long_tooltips(source) -> list:
    src = _source(source)
    found = []
    for node in _calls(src.tree):
        for value in _tips(node):
            text = literal_text(value)
            if text is not None and word_count(text) > TOOLTIP_WORDS:
                found.append((value.lineno, _short(text)))
    return sorted(found)


def _box_text(node: ast.Call):
    """The node a call puts in a message box as its text, or None."""
    called = _called(node)
    if called not in BOX_TEXT:
        return None
    if called in BOX_CALLS and not (isinstance(node.func, ast.Attribute)
                                    and ast.unparse(node.func.value).endswith("QMessageBox")):
        return None
    return _argument(node, BOX_TEXT[called], "text")


def long_questions(source) -> list:
    src = _source(source)
    found = []
    for node in _calls(src.tree):
        value = _box_text(node)
        text = literal_text(value) if value is not None else None
        if text is not None and word_count(text) > QUESTION_WORDS and not src.waived(node):
            found.append((node.lineno, _short(text)))
    return sorted(found)


def _button_name(text: str) -> str:
    """A button's label as running text quotes it: no mnemonic, no ellipsis."""
    return re.sub(r"&(?!&)", "", text).replace("&&", "&").rstrip("….").strip().lower()


def _buttons_made(scope: ast.AST) -> set:
    """The labels of the buttons a function makes, as :func:`_button_name`."""
    labels = []
    for node in _calls(scope):
        called = _called(node)
        if called in ("addButton", "QPushButton") and node.args:
            labels.append(node.args[0])
        elif called == "ask":
            labels.extend(v for v in (_argument(node, 3, "action"),
                                      _argument(node, 99, "cancel")) if v is not None)
        elif called == "choose":
            choices = _argument(node, 3, "choices")
            labels.extend(part.elts[0] for part in ast.walk(choices or ast.Tuple(elts=[]))
                          if isinstance(part, ast.Tuple) and part.elts)
            cancel = _argument(node, 99, "cancel")
            if cancel is not None:
                labels.append(cancel)
    return {_button_name(text) for text in map(literal_text, labels) if text}


def boxes_quoting_buttons(source) -> list:
    src = _source(source)
    found = []
    for scope in ast.walk(src.tree):
        if not isinstance(scope, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        made = _buttons_made(scope)
        if not made:
            continue
        for line, text in _marked_in(scope):
            if any(_button_name(quoted) in made for quoted in _QUOTED.findall(text)):
                found.append((line, _short(text)))
    # A function inside another is walked with it, and on its own.
    return sorted(set(found))


def repeated_values(source) -> list:
    """A marked string that fills the same named value in twice."""
    src = _source(source)
    found = []
    for node in _calls(src.tree):
        if _called(node) not in MARKERS or src.waived(node):
            continue
        for arg in node.args[:MARKERS[_called(node)]]:
            text = literal_text(arg)
            if text is None:
                continue
            names = _NAMED_VALUE.findall(_TAG.sub(" ", text))
            if any(names.count(name) > 1 for name in names):
                found.append((arg.lineno, _short(text)))
    return sorted(found)


def marked_strings(source) -> list:
    """Every literal marked for translation, as (line number, text)."""
    return _marked_in(_source(source).tree)


def _marked_in(tree: ast.AST) -> list:
    found = []
    for node in _calls(tree):
        for arg in node.args[:MARKERS.get(_called(node), 0)]:
            text = literal_text(arg)
            if text is not None:
                found.append((arg.lineno, text))
    return sorted(found)


def voice(source) -> list:
    """The voice findings of a file, as (rule, line number, text)."""
    found = []
    for line, text in marked_strings(source):
        # ";;" is how Qt separates the filters of a file dialog, not prose.
        plain = _ENTITY.sub(" ", _TAG.sub(" ", text)).replace(";;", " ")
        for rule in VOICE_RULES:
            if _VOICE[rule].search(plain):
                found.append((rule, line, _short(text)))
    return found


def labels_without_buddy(source) -> list:
    src = _source(source)
    found = []
    for node in _calls(src.tree):
        if (isinstance(node.func, ast.Attribute) and node.func.attr == "addRow"
                and len(node.args) >= 2 and literal_text(node.args[0])):
            # Qt makes the label and sets the field as its buddy. What a
            # string label can still lack is the mnemonic that reaches it.
            if "&" not in ast.unparse(node.args[0]):
                found.append((node.lineno, src.line(node)))
            continue
        if _called(node) != "QLabel" or not node.args:
            continue
        text = literal_text(node.args[0])
        if text is None or not text.rstrip().endswith(":"):
            continue
        parent = src.parents.get(node)
        name = None
        if isinstance(parent, ast.Assign) and parent.value is node and len(parent.targets) == 1:
            name = ast.unparse(parent.targets[0])
        elif isinstance(parent, ast.AnnAssign) and parent.value is node:
            name = ast.unparse(parent.target)
        if src.waived(node):
            continue  # it names a value that is only read, and says so
        if name is None or not _has_buddy(src, node, name):
            found.append((node.lineno, _short(text)))
    return sorted(found)


def _has_buddy(src: Source, node: ast.AST, name: str) -> bool:
    """Whether the function ``node`` is in calls ``<name>.setBuddy``."""
    scope = src.parents.get(node)
    while scope is not None and not isinstance(scope, (ast.FunctionDef, ast.AsyncFunctionDef)):
        scope = src.parents.get(scope)
    return any(
        isinstance(call.func, ast.Attribute) and call.func.attr == "setBuddy"
        and ast.unparse(call.func.value) == name
        for call in _calls(scope if scope is not None else src.tree)
    )


_CODE_RULES = (
    ("double-click-only", double_click_only),
    ("yes-no-box", yes_no_boxes),
    ("long-label", long_labels),
    ("long-help", long_help),
    ("long-tooltip", long_tooltips),
    ("long-question", long_questions),
    ("box-quotes-button", boxes_quoting_buttons),
    ("label-no-buddy", labels_without_buddy),
)
_THEME_RULES = (
    ("colour-literal", colour_literals),
    ("stylesheet", stylesheets),
    ("status-colour-alone", status_colour_alone),
)


def check_source(source, *, code: bool = True, theme: bool = False) -> list:
    """Every finding in one file's source, as (rule, line number, text).

    ``code`` is False for a file outside ``ui/``, which is read for its
    strings only. ``theme`` is True for ``ui/theme.py``, the one place a
    colour or a style sheet may be written.
    """
    src = _source(source)
    found = []
    if code:
        rules = _CODE_RULES if theme else _THEME_RULES + _CODE_RULES
        for rule, check in rules:
            found.extend((rule, line, text) for line, text in check(src))
    found.extend(voice(src))
    found.extend(("repeated-value", line, text) for line, text in repeated_values(src))
    return found


# -- the repository ---------------------------------------------------------------------
def _files() -> list:
    """The files to read, as (path, whether it is interface code)."""
    return [
        (path, layer == "ui")
        for layer in ("ui", "core")
        for path in sorted((PACKAGE / layer).glob("*.py"))
    ]


def findings() -> list:
    """Every finding in the source, as (rule, "path:line", text)."""
    found = []
    for path, code in _files():
        relative = path.relative_to(ROOT).as_posix()
        source = path.read_text(encoding="utf-8")
        for rule, line, text in check_source(source, code=code, theme=relative == THEME):
            found.append((rule, "%s:%d" % (relative, line), text))
    order = {rule: at for at, rule in enumerate(RULES)}
    found.sort(key=lambda item: (order[item[0]], item[1].rsplit(":", 1)[0],
                                 int(item[1].rsplit(":", 1)[1])))
    return found


def counts() -> Counter:
    """How many findings each source rule has today."""
    return Counter(rule for rule, _where, _text in findings())


def raster_images(resources: Path | None = None) -> list:
    """The raster images under ``resources``, bar the application icon."""
    resources = PACKAGE / "resources" if resources is None else resources
    return sorted(
        path for path in resources.rglob("*")
        if path.is_file() and path.suffix.lower() in RASTER_SUFFIXES
        and not (path.parent == resources and path.name in APP_ICONS)
    )


def failures() -> list:
    """Every finding of every rule, as lines to print. None is allowed."""
    return [
        "  %s: %s: %s" % found for found in findings()
    ] + [
        "  raster-image: %s" % path.relative_to(ROOT).as_posix()
        for path in raster_images()
    ]


def main(argv: list | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    # The strings it prints hold arrows and dashes a Windows console cannot encode.
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if args and args[0] in ("-h", "--help"):
        print(__doc__)
        return 0
    if args:
        rule = args[0]
        if rule in FILE_RULES:
            for path in raster_images():
                print(path.relative_to(ROOT).as_posix())
            return 0
        if rule not in RULES:
            print("no such rule: %s\nrules: %s" % (rule, ", ".join(RULES + FILE_RULES)))
            return 2
        for found, where, text in findings():
            if found == rule:
                print("%s: %s" % (where, text))
        return 0
    today = counts()
    for rule in RULES:
        print("%-20s %d" % (rule, today.get(rule, 0)))
    print("%-20s %d" % ("raster-image", len(raster_images())))
    return 1 if failures() else 0


if __name__ == "__main__":
    sys.exit(main())
