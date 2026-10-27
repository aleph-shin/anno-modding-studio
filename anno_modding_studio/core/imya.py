"""iModYourAnno's tweaks: an Anno 1800 mod's own options, and the player's answers.

Anno 1800's loader has no variables, so a mod cannot ask the game a question.
iModYourAnno -- the mod manager most of the 1800 community uses -- made one possible
anyway.  The author tags ModOps with a ``ModOpID`` and describes each knob with
an ``<ImyaExpose>`` beside them: an ID, a description, a kind (a free value, a
list, a slider, a toggle, or a toggle that switches an op on and off), and a
path inside the op to the value it sets.  Setting one **rewrites the mod**: every
tagged op gets ``Skip="1"`` in its own file (a switch that is on stays
unskipped), the edited copies go to a sibling ``<file>.imyatweak.include.xml``,
and an ``<Include>`` for it is appended to the original.  The loader then reads
ordinary ModOps and never knows.

It is 117's mod options by another route -- 117's loader has the variables
built in, and its documentation calls them comparable to these -- so the Studio
offers both through the same dialog, as a :class:`core.settings.ModOptions`.

Three things are done with a mod that has them:

* :func:`read` -- what it declares, and what the folder is set to now.  The
  folder, not iModYourAnno's own record of it, because the folder is what the
  game runs: its Skip flags and the generated copies are read back into
  values.  The original ops are never edited by iModYourAnno, only switched,
  so the author's defaults are always still there to read.
* :func:`render` -- the files iModYourAnno would write for a set of answers.
  The Studio reads a mod at chosen values by reading these in place of the
  files on disk, and nothing is written.
* :func:`write` and :func:`carry` -- the same files, written.  An update
  installs the author's fresh folder, which knows nothing of the player's
  answers; carrying them across is what iModYourAnno does from its own record
  and what the Studio has to do too, or the game quietly goes back to the
  author's defaults.

The export is a port of iModYourAnno's own (``TweakerFile.Export``,
``TweakExporter``, ``XmlPatchParser``), quirks included, so the two tools write
the same thing and a folder one of them set reads the same to the other.
MIT-licensed upstream (anno-mods/iModYourAnno); see THIRD-PARTY-NOTICES.md.
One difference, on purpose: iModYourAnno re-saves the original file whole and
reformats it; this changes the Skip flags and the one ``<Include>`` and leaves
the author's layout alone.  iModYourAnno's own record in ``.imya\\tweaks`` is
another tool's state and is never read or written here.
"""

from __future__ import annotations

import copy
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from lxml import etree

from . import modfolder, modop, settings
from .i18n import join_list, tr, trn

# What an author writes, and where iModYourAnno puts what it generates.
EXPOSE = "ImyaExpose"
TWEAKS = "ImyaTweaks"
TWEAK_SUFFIX = ".imyatweak.include.xml"

# The kinds an <ImyaExpose> can be, spelt as iModYourAnno parses them: exactly,
# since it uses a case-sensitive enum parse and anything else is a plain value.
SIMPLE = "SimpleValue"
ENUM = "Enum"
SLIDER = "Slider"
TOGGLE = "Toggle"
SKIP_TOGGLE = "SkipToggle"

# Between a file and an expose's own ID, in the name the Studio keeps an answer
# under.  An ExposeID is unique within a file, not within a mod.
SEPARATOR = "::"

# Larger than any ModOps file anybody writes by hand; a texts file or a whole
# assets.xml is not somewhere an author puts an expose.
MAX_BYTES = 20 * 1024 * 1024

_BOM = b"\xef\xbb\xbf"
_DECLARATION = re.compile(rb"^\s*<\?xml[^>]*\?>")

# iModYourAnno's "every ModOp node": a ModOp, an Include or a Group, sitting
# straight under <ModOps> or under a <Group>.
_MODOP_NODES = (
    "//*[self::ModOps or self::Group]"
    "/*[name()='ModOp' or name()='Include' or name()='Group']"
)
# What a generated copy never carries, and what is never generated at all.
_ARGUMENT_BLACKLIST = ("ModOpID", "Skip")
_EXPORT_BLACKLIST = ("include", "group")


@dataclass
class Expose:
    """One knob an author offers, as its ``<ImyaExpose>`` declares it."""

    file: str  # the file it sits in, relative to the mod, with forward slashes
    expose_id: str
    mod_op_id: str
    path: str
    kind: str = SIMPLE
    description: str = ""
    values: Tuple[str, ...] = ()  # an Enum's own list
    slider: Tuple[float, float, float] = ()  # type: ignore[assignment]
    false_value: str = ""  # a Toggle's AltValue, as XML
    true_value: str = ""  # what the op holds at the path, as XML
    inverted: bool = False
    init_state: bool = False
    default: str = ""
    # Whether the export writes the same thing whatever the answer, so the
    # Studio does not offer it; see _mark_inert.
    inert: bool = False

    @property
    def name(self) -> str:
        return self.file + SEPARATOR + self.expose_id

    @property
    def is_toggle(self) -> bool:
        return self.kind in (TOGGLE, SKIP_TOGGLE)

    @property
    def replaces_xml(self) -> bool:
        """Whether the value is a node to put in place, rather than text."""
        return self.is_toggle

    def option(self) -> settings.Option:
        """The same knob as the Studio's options dialog asks it."""
        common: dict = {
            "name": self.name, "label": self.expose_id, "default": self.default,
            "description": self.description,
        }
        if self.is_toggle:
            return settings.Option(kind=settings.TOGGLE, **common)
        if self.kind == SLIDER:
            return settings.Option(
                kind=settings.SLIDER, values=tuple(_number(value) for value in self.slider),
                **common,
            )
        if self.kind == ENUM:
            return settings.Option(kind=settings.ENUM, values=self.values, **common)
        return settings.Option(kind=settings.TEXT, **common)

    def value_for(self, answer: str) -> str:
        """What iModYourAnno would put in the op for the Studio's ``answer``.

        A toggle's answer is on or off; what that means in the file is one of
        two values, and which one is flipped by ``Invert``.  A skip toggle's two
        values are "0" and "1", which are not XML -- so putting one in place
        removes the node it lands on, exactly as it does in iModYourAnno.
        """
        if not self.is_toggle:
            return str(answer)
        if self.kind == SKIP_TOGGLE:
            true_value, false_value = "0", "1"
        else:
            true_value, false_value = self.true_value, self.false_value
        on = settings.is_on(answer)
        if on:
            return false_value if self.inverted else true_value
        return true_value if self.inverted else false_value

    def skips(self, answer: str) -> bool:
        """Whether the original op is switched off for ``answer``, when this is
        the first knob on its op and it is a toggle."""
        return (not settings.is_on(answer)) ^ self.inverted


@dataclass
class TweakFile:
    """One file of a mod that offers knobs, and the knobs it offers."""

    path: str  # relative to the mod, forward slashes
    title: str = ""
    description: str = ""
    exposes: List[Expose] = field(default_factory=list)

    @property
    def edit_name(self) -> str:
        """The file iModYourAnno writes the edited copies to, beside this one."""
        stem = self.path.rsplit("/", 1)[-1]
        if stem.lower().endswith(".xml"):
            stem = stem[: -len(".xml")]
        return stem + TWEAK_SUFFIX


@dataclass
class Tweaks:
    """Everything a mod offers through iModYourAnno, and what it is set to."""

    folder: Path
    files: List[TweakFile] = field(default_factory=list)
    # What the folder is set to now, by the Studio's name for each knob.
    current: Dict[str, str] = field(default_factory=dict)
    # Files whose generated copies did not line up with their ops, so what
    # they are set to could not be read back and is given as the defaults.
    unreadable: List[str] = field(default_factory=list)

    def __bool__(self) -> bool:
        return any(tweak.exposes for tweak in self.files)

    @property
    def exposes(self) -> List[Expose]:
        return [expose for tweak in self.files for expose in tweak.exposes]

    def get(self, name: str) -> Optional[Expose]:
        for expose in self.exposes:
            if expose.name == name:
                return expose
        return None

    def defaults(self) -> Dict[str, str]:
        return {expose.name: expose.default for expose in self.exposes}

    def is_set_to(self, answers) -> bool:
        """Whether the folder is already set to ``answers`` -- a knob they do
        not name at the author's default -- whatever bytes writing them would
        give: iModYourAnno's files at the author's defaults are not the
        author's own files, and load the same."""
        wanted = self.defaults()
        wanted.update({name: value for name, value in dict(answers or {}).items() if name in wanted})
        defaults = self.defaults()
        return all(
            _same(value, self.current.get(name, defaults[name]), self.get(name))
            for name, value in wanted.items()
        )

    def chosen(self) -> Dict[str, str]:
        """What the folder is set to, where it is not the author's default --
        for the knobs that are offered."""
        defaults = self.defaults()
        chosen = {}
        for name, value in self.current.items():
            expose = self.get(name)
            if expose is not None and expose.inert:
                continue
            if not _same(value, defaults.get(name, ""), expose):
                chosen[name] = value
        return chosen

    def options(self) -> settings.ModOptions:
        """The knobs as the Studio's options dialog shows them: a heading per
        file, in the author's own title, and the file's knobs under it.  A knob
        whose answer changes nothing is left out."""
        found: List[settings.Option] = []
        for tweak in self.files:
            offered = [expose for expose in tweak.exposes if not expose.inert]
            if not offered:
                continue
            found.append(
                settings.Option(
                    name="%s%s" % (tweak.path, SEPARATOR),
                    label=tweak.title or tweak.path.rsplit("/", 1)[-1],
                )
            )
            found.extend(expose.option() for expose in offered)
        return settings.ModOptions(found)


def is_tweak_name(name: str) -> bool:
    """Whether an option name is one of these rather than a 117 option."""
    return SEPARATOR in str(name)


# -- reading ---------------------------------------------------------------------
def read(folder, bundled: bool = False) -> Optional[Tweaks]:
    """What a mod folder offers through iModYourAnno, and what it is set to.

    None for a mod that offers nothing, which is nearly all of them; that is
    decided on the bytes before anything is parsed.

    A mod bundled inside this one -- a folder with a modinfo of its own -- is a
    mod in its own right to the loader and to the Studio's reading, so its
    knobs are its own and are left out.  ``bundled`` takes them in: an update
    replaces the whole folder, bundled mods and all, and carrying the answers
    across has to carry all of them.
    """
    folder = Path(folder)
    if not folder.is_dir():
        return None
    inner = [] if bundled else modfolder.bundled_in(folder)
    found = Tweaks(folder=folder)
    for path in sorted(folder.rglob("*.xml"), key=lambda p: str(p).lower()):
        if path.name.lower().endswith(TWEAK_SUFFIX):
            continue
        if any(path.is_relative_to(nested) for nested in inner):
            continue
        try:
            if path.stat().st_size > MAX_BYTES:
                continue
            raw = path.read_bytes()
        except OSError:
            continue
        if EXPOSE.encode() not in raw:
            continue
        relative = path.relative_to(folder).as_posix()
        tweak, current, readable = _read_file(folder, relative, raw)
        if tweak is None or not tweak.exposes:
            continue
        found.files.append(tweak)
        found.current.update(current)
        if not readable:
            found.unreadable.append(relative)
    return found if found else None


def _parse(raw: bytes, lean: bool):
    """A strict parse, as iModYourAnno's XmlDocument.Load is: a file it could
    not load is a file it never tweaked.  ``lean`` drops whitespace-only text,
    which is how its XmlDocument sees the ops it copies."""
    parser = etree.XMLParser(remove_blank_text=lean, resolve_entities=False, huge_tree=True)
    try:
        return etree.fromstring(raw, parser)
    except etree.XMLSyntaxError:
        return None


def _read_file(folder: Path, relative: str, raw: bytes):
    """``(TweakFile, current values, whether the current values could be read)``."""
    root = _parse(raw, lean=True)
    if root is None or root.tag != "ModOps":
        return None, {}, True
    tweak = TweakFile(path=relative)
    head = root.find(TWEAKS)
    if head is not None:
        tweak.title = head.get("Title") or ""
        tweak.description = head.get("Description") or ""
    ops = _modops(root)
    for node in root.iter(EXPOSE):
        expose = _expose(node, relative, ops)
        if expose is not None:
            tweak.exposes.append(expose)
    if not tweak.exposes:
        return tweak, {}, True
    _mark_inert(tweak.exposes, ops)
    current, readable = _current(folder, tweak, root, ops)
    return tweak, current, readable


def _expose(node, relative: str, ops) -> Optional[Expose]:
    """An ``<ImyaExpose>`` read the way ``ExposedModValueFactory`` reads it."""
    path, mod_op_id, expose_id = node.get("Path"), node.get("ModOpID"), node.get("ExposeID")
    if path is None or mod_op_id is None or expose_id is None:
        return None
    try:
        etree.XPath(path)
    except etree.XPathSyntaxError:
        return None
    # A knob is matched to its ops by the exact ID, in its own file only -- an
    # op whose ModOpID is " X" is not knob "X"'s.  With no op it sets nothing
    # in either tool (iModYourAnno still shows it), so it is not offered: an
    # answer to it could never be written, nor read back.
    if _first_op(ops, mod_op_id) is None:
        return None
    expose = Expose(
        file=relative, expose_id=expose_id, mod_op_id=mod_op_id, path=path,
        description=node.get("Description") or "",
        inverted=node.get("Invert") == "True",
    )
    kind = node.get("Kind") or SIMPLE
    # A kind whose definition is missing falls through to a plain value, as it
    # does in iModYourAnno: its chain of cases only returns when it succeeds.
    if kind == ENUM:
        values = tuple("".join(value.itertext()) for value in node.findall("FixedValues/Value"))
        if values:
            expose.kind, expose.values = ENUM, values
    elif kind == SLIDER:
        definition = node.find("SliderDefinition")
        if definition is not None:
            try:
                expose.slider = (
                    float(definition.get("Min")), float(definition.get("Max")),
                    float(definition.get("Stepping")),
                )
                expose.kind = SLIDER
            except (TypeError, ValueError):
                pass
    elif kind == TOGGLE:
        alternative = node.find("AltValue")
        if alternative is not None:
            expose.kind = TOGGLE
            expose.false_value = _inner_xml(alternative)
            expose.true_value = _true_value(expose, ops)
    elif kind == SKIP_TOGGLE:
        expose.kind = SKIP_TOGGLE
        expose.init_state = node.get("InitState") == "True"

    # The author's default: for a toggle, what the op is when it is left as
    # written -- on, or for a skip toggle whatever InitState says -- and for
    # anything else the text at the path, as GetDefaultNodeValue reads it.
    if expose.kind == SKIP_TOGGLE:
        expose.default = "true" if expose.init_state else "false"
    elif expose.kind == TOGGLE:
        expose.default = "false" if expose.inverted else "true"
    else:
        expose.default = _default_text(expose, ops)
    return expose


def _mark_inert(exposes: List[Expose], ops) -> None:
    """Mark the knobs whose answer the export ignores.

    A knob reaches the mod two ways.  The first knob on an op, if it is a
    toggle, decides the original's Skip flag.  Any knob is applied to the
    generated copies -- but a group or an include gets none, and a skip
    toggle's two values are "0" and "1", which are not XML, so on a copy it
    removes the node its path lands on whatever the answer.  Nor does a path
    that lands on nothing change a copy -- known only for the first knob on
    the op, as one before it may have put there what a later path finds.  A
    knob with no way in is still applied, removal and all, as iModYourAnno
    applies it; it is only not offered, as an answer to it could never be
    written, nor read back.  *Trade Union Rebalance* has one: a skip toggle
    labelled for Angela, its ModOpID pasted from Aaden's, which comes second
    on that group.
    """
    first: Dict[str, Expose] = {}
    for expose in exposes:
        first.setdefault(expose.mod_op_id, expose)
    for expose in exposes:
        is_first = first[expose.mod_op_id] is expose
        if expose.is_toggle and is_first:
            continue
        copied = [
            op for op in ops
            if op.get("ModOpID") == expose.mod_op_id
            and (_type_of(op) or "").lower() not in _EXPORT_BLACKLIST
        ]
        lands = not is_first or any(
            _select(item, expose.path) for op in copied for item in _code(op)
        )
        expose.inert = not copied or not lands or expose.kind == SKIP_TOGGLE


def _modops(root) -> List:
    """Every node iModYourAnno treats as a ModOp -- typed, and carrying an ID --
    in document order."""
    found = []
    for node in root.xpath(_MODOP_NODES):
        if _type_of(node) is not None and node.get("ModOpID") is not None:
            found.append(node)
    return found


def _type_of(node) -> Optional[str]:
    tag = node.tag.lower() if isinstance(node.tag, str) else ""
    if tag in _EXPORT_BLACKLIST:
        return tag
    return node.get("Type")


def _code(node) -> List:
    """An op's children the way XmlDocument lists them: text that is not
    whitespace, elements and comments, in order -- elements copied, and the
    text between them as strings."""
    items: List = []
    if node.text and node.text.strip():
        items.append(node.text)
    for child in node:
        item = copy.deepcopy(child)
        item.tail = None
        items.append(item)
        if child.tail and child.tail.strip():
            items.append(child.tail)
    return items


def _select(item, path: str) -> List:
    if not isinstance(item, etree._Element) or not isinstance(item.tag, str):
        return []
    try:
        found = item.xpath(path)
    except (etree.XPathEvalError, etree.XPathSyntaxError):
        return []
    return found if isinstance(found, list) else []


def reached(op, expose: Expose) -> List:
    """The elements of ``op``'s body that ``expose`` sets, found as
    iModYourAnno finds them: text or an attribute it sets stands for the
    element that holds it."""
    found = []
    for child in op:
        for node in _select(child, expose.path):
            holder = node.getparent() if isinstance(node, str) and hasattr(node, "getparent") else node
            if holder is not None and isinstance(getattr(holder, "tag", None), str):
                found.append(holder)
    return found


def _inner_text(node) -> str:
    if isinstance(node, str):
        return str(node)
    return "".join(node.itertext())


def _inner_xml(node) -> str:
    text = node.text or ""
    return text + "".join(etree.tostring(child, encoding=str) for child in node)


def _outer_xml(node) -> str:
    return etree.tostring(node, encoding=str, with_tail=False)


def _first_op(ops, mod_op_id: str):
    return next((op for op in ops if op.get("ModOpID") == mod_op_id), None)


def _default_text(expose: Expose, ops) -> str:
    op = _first_op(ops, expose.mod_op_id)
    if op is None:
        return ""
    for item in _code(op):
        found = _select(item, expose.path)
        if found:
            return _inner_text(found[0])
    return ""


def _true_value(expose: Expose, ops) -> str:
    """A toggle's "on" value: what the op holds at its path.  Only the op's
    first child is asked, as ``InitTrueValue`` asks it."""
    op = _first_op(ops, expose.mod_op_id)
    if op is None:
        return ""
    code = _code(op)
    if not code:
        return ""
    found = _select(code[0], expose.path)
    node = found[0] if found else None
    if node is None or isinstance(node, str):
        return ""
    return _outer_xml(node)


def _current(folder: Path, tweak: TweakFile, root, ops) -> Tuple[Dict[str, str], bool]:
    """What the folder is set to, read back from what iModYourAnno left there.

    The first toggle on an op is read off the original's Skip flag, which is
    the half iModYourAnno changes in place.  Anything else is read out of the generated
    copies: they are written in the order of the tagged ops, groups and
    includes left out, so the n-th copy is the n-th such op.  A folder with no
    generated file has never been tweaked, and is at the author's defaults.
    """
    values = {expose.name: expose.default for expose in tweak.exposes}
    copies = None
    edit = (folder / tweak.path).parent / tweak.edit_name
    if edit.is_file():
        try:
            generated = _parse(edit.read_bytes(), lean=True)
        except OSError:
            generated = None
        expected = [op for op in ops if _type_of(op) not in _EXPORT_BLACKLIST]
        if generated is not None:
            made = [node for node in generated if node.tag == "ModOp"]
            if len(made) == len(expected):
                copies = dict(zip((id(op) for op in expected), made, strict=True))
            else:
                return values, False
    first_toggle: Dict[str, Expose] = {}
    for expose in tweak.exposes:
        first_toggle.setdefault(expose.mod_op_id, expose)
    for expose in tweak.exposes:
        op = _first_op(ops, expose.mod_op_id)
        if op is None:
            continue
        if expose.is_toggle and first_toggle.get(expose.mod_op_id) is expose:
            # The export switches the original by this knob alone, so the
            # flag says what it is set to -- the copy may not: an AltValue
            # the path no longer selects, or one that is not XML and so
            # removed the node, leaves nothing at the path to compare.
            skipped = modop.is_skipped(op)
            # skip = (not on) xor inverted, so on = skip xor (not inverted).
            values[expose.name] = "true" if skipped != (not expose.inverted) else "false"
            continue
        if copies is None:
            continue
        copied = copies.get(id(op))
        if copied is None:
            continue
        found = next(
            (hit for hit in (_select(item, expose.path) for item in _code(copied)) if hit), None
        )
        if expose.kind == TOGGLE:
            if found:
                here = found[0]
                shown = here if isinstance(here, str) else _outer_xml(here)
                on = _same_xml(shown, expose.value_for("true"))
            elif expose.true_value:
                # The op's own value was found at the path, and put back it
                # is found there again; what the path no longer finds was
                # replaced by the AltValue, or removed for it -- which is
                # "off", or "on" when the knob is inverted.
                on = expose.inverted
            else:
                continue
            values[expose.name] = "true" if on else "false"
        elif found and not expose.is_toggle:
            values[expose.name] = _inner_text(found[0])
    return values, True


# -- the export -------------------------------------------------------------------
def render(folder, tweaks: Tweaks, answers=None) -> Dict[Path, bytes]:
    """The files iModYourAnno would write for ``answers``, by resolved path.

    ``answers`` is by the Studio's name for each knob; a knob it does not name
    is at the author's default.  Every file of the mod that offers knobs is
    rendered, as iModYourAnno saves them all at once.  Nothing is written.
    """
    folder = Path(folder)
    answers = dict(answers or {})
    out: Dict[Path, bytes] = {}
    for tweak in tweaks.files:
        if not tweak.exposes:
            continue
        source = folder / tweak.path
        try:
            raw = source.read_bytes()
        except OSError:
            continue
        original, generated = _export(raw, tweak, answers)
        if original is None:
            continue
        out[source.resolve()] = original
        if generated is not None:
            out[(source.parent / tweak.edit_name).resolve()] = generated
    return out


def changed_files(folder, tweaks: Tweaks, answers=None) -> Dict[str, bytes]:
    """:func:`render`, by each file's path from the top of ``folder`` with
    forward slashes, and only the files whose bytes it changes: what a
    generated copy of the mod writes over the author's files to be set to
    ``answers`` (see :mod:`core.generated`).  Nothing at all where the folder
    is set to them already (:meth:`Tweaks.is_set_to`)."""
    if tweaks.is_set_to(answers):
        return {}
    folder = Path(folder)
    top = folder.resolve()
    found: Dict[str, bytes] = {}
    for path, data in render(folder, tweaks, answers).items():
        try:
            if path.is_file() and path.read_bytes() == data:
                continue
        except OSError:
            pass
        found[path.relative_to(top).as_posix()] = data
    return found


def _export(raw: bytes, tweak: TweakFile, answers: Dict[str, str]):
    """``(original, generated)`` for one file: the original with its Skip flags
    and its ``<Include>``, and the generated copies (None when there are none
    to write)."""
    lean = _parse(raw, lean=True)
    kept = _parse(raw, lean=False)
    if lean is None or kept is None:
        return None, None
    ops = _modops(lean)
    kept_nodes = kept.xpath(_MODOP_NODES)
    values = {
        expose.name: expose.value_for(answers.get(expose.name, expose.default))
        for expose in tweak.exposes
    }

    # TweakExporter: every tagged op, cloned, each knob applied to the ops it
    # names.  A copy left with nothing in it that was a replace becomes a remove.
    clones = [(op, _code(op)) for op in ops]
    for expose in tweak.exposes:
        value = values[expose.name]
        for op, code in clones:
            if op.get("ModOpID") != expose.mod_op_id:
                continue
            code[:] = [
                edited for edited in (_apply(item, expose, value) for item in code)
                if edited is not None
            ]
    types = {id(op): _type_of(op) for op, _code_ in clones}
    for op, code in clones:
        if not code and types[id(op)] == "replace":
            types[id(op)] = "remove"

    # TweakerFile.Export: the originals switched off -- all of them, except
    # where the op's first knob is a toggle and says otherwise -- and a copy of
    # each generated, groups and includes aside.
    first: Dict[str, Expose] = {}
    for expose in tweak.exposes:
        first.setdefault(expose.mod_op_id, expose)
    target = etree.Element("ModOps")
    for op, code in clones:
        mod_op_id = op.get("ModOpID")
        skip = True
        knob = first.get(mod_op_id)
        if knob is not None and knob.is_toggle:
            skip = knob.skips(answers.get(knob.name, knob.default))
        for node in kept_nodes:
            if node.get("ModOpID") == mod_op_id:
                _set_skip(node, skip)
        kind = types[id(op)]
        if kind is None or kind.lower() in _EXPORT_BLACKLIST:
            continue
        target.append(_generate(op, code, kind))

    generated = None
    if len(target):
        _ensure_include(kept, "./" + tweak.edit_name)
        generated = etree.tostring(target, encoding="utf-8", pretty_print=True)
    return _serialise(raw, kept), generated


def _apply(item, expose: Expose, value: str):
    """One child of a cloned op with one knob applied, or None if it goes."""
    found = _select(item, expose.path)
    if not found:
        return item
    if not expose.replaces_xml:
        for node in found:
            _set_inner_text(node, value)
        return item
    new = _xml_node(value)
    for node in found:
        if isinstance(node, str):
            continue
        if node is item:
            return copy.deepcopy(new) if new is not None else None
        _replace(node, new)
    return item


def _set_inner_text(node, value: str) -> None:
    if isinstance(node, str):
        parent = node.getparent() if hasattr(node, "getparent") else None
        if parent is None:
            return
        if getattr(node, "is_attribute", False):
            parent.set(node.attrname, value)  # type: ignore[attr-defined]
        elif getattr(node, "is_tail", False):
            parent.tail = value
        else:
            parent.text = value
        return
    for child in list(node):
        node.remove(child)
    node.text = value


def _xml_node(value: str):
    """``LoadXmlNodeFrom``: the value as a node, or None when it is not XML."""
    try:
        return etree.fromstring(value.encode("utf-8"))
    except (etree.XMLSyntaxError, ValueError):
        return None


def _replace(node, new) -> None:
    parent = node.getparent()
    if parent is None:
        return
    if new is None:
        _remove_keeping_tail(node)
        return
    made = copy.deepcopy(new)
    made.tail = node.tail
    parent.replace(node, made)


def _remove_keeping_tail(node) -> None:
    parent = node.getparent()
    if node.tail:
        previous = node.getprevious()
        if previous is not None:
            previous.tail = (previous.tail or "") + node.tail
        else:
            parent.text = (parent.text or "") + node.tail
    parent.remove(node)


def _generate(op, code: List, kind: str):
    """The copy of an op iModYourAnno writes: its attributes but the ID and
    the switch, then Path, GUID and Type moved to the end, then its body."""
    made = etree.Element("ModOp")
    moved = ("Path", "GUID", "Type")
    for name, value in op.attrib.items():
        if name in _ARGUMENT_BLACKLIST or name in moved:
            continue
        made.set(name, value)
    for name in ("Path", "GUID"):
        if op.get(name) is not None:
            made.set(name, op.get(name))
    made.set("Type", kind)
    last = None
    for item in code:
        if isinstance(item, str):
            if last is None:
                made.text = (made.text or "") + item
            else:
                last.tail = (last.tail or "") + item
            continue
        made.append(copy.deepcopy(item))
        last = made[-1]
    return made


def _set_skip(node, skip: bool) -> None:
    if skip:
        node.set("Skip", "1")
    elif "Skip" in node.attrib:
        del node.attrib["Skip"]


def _ensure_include(root, file: str) -> None:
    for node in root.findall("Include"):
        if node.get("File") == file:
            return
    include = etree.Element("Include")
    include.set("File", file)
    if len(root):
        last = root[-1]
        include.tail = last.tail
        last.tail = root.text if root.text and not root.text.strip() else "\n"
    root.append(include)


def _serialise(raw: bytes, root) -> bytes:
    """The original written back as it was, bar what changed: its byte-order
    mark and its declaration exactly as they were, and lxml's rendering of the
    rest, which keeps the author's own whitespace and comments -- an empty
    element comes out as ``<X/>`` where the author may have written ``<X />``,
    which no loader tells apart."""
    body = etree.tostring(root.getroottree(), encoding="utf-8", xml_declaration=False)
    head = b""
    rest = raw
    if rest.startswith(_BOM):
        head += _BOM
        rest = rest[len(_BOM):]
    declared = _DECLARATION.match(rest)
    if declared:
        head += declared.group(0).lstrip() + b"\n"
    return head + body.lstrip()


# -- writing, and carrying across an update ---------------------------------------
def write(folder, answers=None, tweaks: Optional[Tweaks] = None) -> List[Path]:
    """Write iModYourAnno's files for ``answers`` into ``folder``; the files
    that changed.  Only ever into a folder the caller owns."""
    folder = Path(folder)
    tweaks = tweaks if tweaks is not None else read(folder)
    if not tweaks:
        return []
    written = []
    for path, data in render(folder, tweaks, answers).items():
        try:
            if path.is_file() and path.read_bytes() == data:
                continue
        except OSError:
            pass
        path.write_bytes(data)
        written.append(path)
    return written


@dataclass
class Snapshot:
    """What a folder was set to, taken before it is replaced."""

    chosen: Dict[str, str] = field(default_factory=dict)
    labels: Dict[str, str] = field(default_factory=dict)

    def __bool__(self) -> bool:
        return bool(self.chosen)


def snapshot(folder) -> Snapshot:
    """The answers in ``folder`` that are not the author's defaults."""
    try:
        tweaks = read(folder, bundled=True)
    except OSError:
        tweaks = None
    if not tweaks:
        return Snapshot()
    chosen = tweaks.chosen()
    labels = {}
    for name in chosen:
        expose = tweaks.get(name)
        if expose is not None:
            labels[name] = expose.expose_id
    return Snapshot(chosen=chosen, labels=labels)


@dataclass
class Carried:
    """What carrying a mod's answers across a replacement did."""

    kept: Dict[str, str] = field(default_factory=dict)
    dropped: List[str] = field(default_factory=list)  # the knobs' own names
    error: str = ""

    def __bool__(self) -> bool:
        return bool(self.kept or self.dropped or self.error)

    def describe(self, mod: str = "") -> str:
        if self.error and mod:
            return tr(
                "The iModYourAnno settings of %(mod)s could not be kept: %(error)s.",
                mod=mod, error=self.error,
            )
        if self.error:
            return tr("The iModYourAnno settings could not be kept: %s.", self.error)
        said = []
        if self.kept and mod:
            said.append(trn(
                "Kept %(n)s iModYourAnno setting of %(mod)s.",
                "Kept %(n)s iModYourAnno settings of %(mod)s.",
                len(self.kept), mod=mod,
            ))
        elif self.kept:
            said.append(trn(
                "Kept %(n)s iModYourAnno setting.", "Kept %(n)s iModYourAnno settings.", len(self.kept),
            ))
        if self.dropped:
            said.append(trn(
                "The new version no longer offers %(names)s, so that is back to what the author wrote.",
                "The new version no longer offers %(names)s, so those are back to what the author wrote.",
                len(self.dropped), names=join_list(sorted(self.dropped)),
            ))
        return " ".join(said)


def carry(before: Snapshot, folder, write_them: bool = True) -> Carried:
    """Write ``before``'s answers into the freshly installed ``folder``.

    A knob the new version still offers keeps its answer; one it no longer
    offers, or whose answer is no longer one of its choices, is named rather
    than dropped in silence.  ``write_them`` off works out what is kept and
    writes nothing: the library's copy stays as shipped, and the answers go
    into a generated copy of it (:func:`core.library.generate`).
    """
    done = Carried()
    if not before:
        return done
    try:
        tweaks = read(folder, bundled=True)
    except OSError as exc:
        done.error = str(exc)
        return done
    if not tweaks:
        done.dropped = sorted(before.labels.get(name, name) for name in before.chosen)
        return done
    for name, value in before.chosen.items():
        expose = tweaks.get(name)
        if (
            expose is None or expose.inert
            or (expose.kind == ENUM and value not in expose.values)
        ):
            done.dropped.append(before.labels.get(name, name))
            continue
        done.kept[name] = value
    if done.kept and write_them:
        try:
            write(folder, done.kept, tweaks)
        except OSError as exc:
            done.error = str(exc)
    return done


# -- small helpers -----------------------------------------------------------------
def _number(value: float) -> str:
    return str(int(value)) if float(value).is_integer() else "%g" % value


def _same_xml(first: str, second: str) -> bool:
    if first == second:
        return True
    one, two = _xml_node(first), _xml_node(second)
    if one is None or two is None:
        return first.strip() == second.strip()
    return etree.tostring(one, method="c14n") == etree.tostring(two, method="c14n")


def _same(value: str, default: str, expose: Optional[Expose]) -> bool:
    if expose is not None and expose.is_toggle:
        return settings.is_on(value) == settings.is_on(default)
    if value == default:
        return True
    try:
        return float(value) == float(default)
    except (TypeError, ValueError):
        return False
