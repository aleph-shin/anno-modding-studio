"""What mods call things: their localisation patches, read for names.

A mod names what it adds, and renames what it changes, in its own
``texts_<language>.xml`` -- a ModOps patch on the game's texts file.  Half the
mods on mod.io ship one (449 of 857 for Anno 1800, 125 of 371 for Anno 117).
Without reading them, every asset a mod adds shows under its internal name,
and a mod that renames something shows the old name.

Only the language the game's own texts were loaded in is read, and only the
shapes mods write in practice -- measured over the census:

======================================================  =====  =====
the op                                                   1800   117
======================================================  =====  =====
add entries to ``/TextExport/Texts``                     1,625   491
replace ``Text[GUID|LineId='…']/Text``                     491   450
add text to the end of ``Text[…]/Text``                    475   141
replace or merge a whole ``Text[…]`` entry                  43     6
======================================================  =====  =====

Anything else -- a ``Content=`` copy, a merge into the list, a condition about
the texts themselves -- is counted as not read and left out.  A condition
asking which mods are loaded is answered, as the loader does, against the mods
that load.

Anno 1800 keys an entry by ``<GUID>``, Anno 117 by ``<LineId>``; an entry keyed
the other way is not one the game looks up, and is left out.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

from lxml import etree

from . import disk, games, modop
from .games import ANNO_117, ANNO_1800
from .i18n import tr

_KEY_TAG = {ANNO_1800: "GUID", ANNO_117: "LineId"}

_LANGUAGE = re.compile(r"texts_([A-Za-z_]+)\.xml$", re.IGNORECASE)
_CONTAINER = re.compile(r"^/{0,2}TextExport/Texts(?:\[1\])?/?$")
_ENTRY = re.compile(r"^(?:/{0,2}TextExport/Texts/|//)Text\[(?P<pred>[^\]]+)\](?P<inner>/Text)?$")
_CLAUSE = re.compile(r"(GUID|LineId)\s*=\s*['\"](-?\d+)['\"]")


def language_of(path) -> str:
    """``english`` for ``…/texts_english.xml``, or "" for anything else."""
    found = _LANGUAGE.search(str(path or ""))
    return found.group(1).lower() if found else ""


def patch_file(folder, game: str, language: str) -> Optional[Path]:
    """Where a mod keeps its patch for ``language``, if it has one."""
    if not folder or not language:
        return None
    path = Path(folder) / games.get(game).gui_relpath / ("texts_%s.xml" % language)
    return path if path.is_file() else None


@dataclass
class Edit:
    """One thing a mod does to one entry: gives it a text, or adds to it."""

    kind: str   # "set" or "append"
    key: str
    text: str
    # Every condition it runs under, its groups' included: all have to hold.
    conditions: Tuple[str, ...] = ()


@dataclass
class ModTexts:
    edits: List[Edit] = field(default_factory=list)
    # ModOps in the file that are not one of the shapes read here.
    unread: int = 0


def _keys(pred: str, tag: str) -> List[str]:
    """The keys a predicate names, when it names nothing else: ``GUID='1'``,
    ``GUID='1' or GUID='2'``.  Anything more is not a lookup by key."""
    clauses = _CLAUSE.findall(pred)
    rest = _CLAUSE.sub("", pred)
    if not clauses or re.sub(r"\bor\b|\s", "", rest):
        return []
    if any(kind != tag for kind, _key in clauses):
        return []
    return [key for _kind, key in clauses]


def _entries(elements, tag: str) -> List[Tuple[str, str]]:
    found = []
    for element in elements:
        if not isinstance(element.tag, str) or element.tag != "Text":
            continue
        key = (element.findtext(tag) or "").strip()
        if key:
            found.append((key, (element.findtext("Text") or "").strip()))
    return found


def read(path: Path, game: str) -> ModTexts:
    """What one texts patch does, as :class:`Edit` s in file order."""
    from . import modops

    result = ModTexts()
    try:
        root = etree.parse(str(path), etree.XMLParser(
            recover=True, huge_tree=True, resolve_entities="internal", no_network=True)).getroot()
    except (OSError, etree.XMLSyntaxError):
        return result
    if root is None:
        return result
    tag = _KEY_TAG.get(game, "GUID")
    try:
        ops = modops.parse_ops(root, [], path.name, source=path, game=game)
    except Exception:  # noqa: BLE001 - a patch that will not parse names nothing
        return result
    scopes: Dict[str, Tuple[str, bool]] = {}
    for op in ops:
        if op.verbatim:
            if op.scope:
                scopes[op.scope] = (op.condition or "", bool(op.attrs.get("skip")))
            else:
                result.unread += 1
            continue
        held = [scopes.get(scope, ("", False)) for scope in op.scopes]
        if any(skip for _condition, skip in held) or modop.is_skipped(op.attrs):
            continue
        conditions = tuple(c for c in [op.condition] + [c for c, _s in held] if c)
        edits = _read_op(op, tag)
        if edits is None:
            result.unread += 1
            continue
        for kind, key, text in edits:
            result.edits.append(Edit(kind, key, text, conditions))
    return result


def _read_op(op, tag: str) -> Optional[List[Tuple[str, str, str]]]:
    """``[(kind, key, text)]`` for an op of one of the shapes read, else None."""
    if op.content or any(name.lower() == "content" for name in op.attrs):
        return None
    path = (op.path or "").strip()
    payload = [element for element in op.payload if isinstance(getattr(element, "tag", None), str)]
    if _CONTAINER.match(path):
        if op.kind != "add":
            return None
        return [("set", key, text) for key, text in _entries(payload, tag)]
    entry = _ENTRY.match(path)
    if entry is None:
        return None
    keys = _keys(entry.group("pred"), tag)
    if not keys:
        return None
    if entry.group("inner"):
        if op.kind == "replace" and len(payload) == 1 and payload[0].tag == "Text" and not len(payload[0]):
            value = (payload[0].text or "").strip()
            return [("set", key, value) for key in keys]
        if op.kind == "add" and not payload and op.text:
            return [("append", key, op.raw_text or op.text) for key in keys]
        return None
    if op.kind in ("replace", "merge") and len(payload) == 1 and payload[0].tag == "Text":
        value = payload[0].findtext("Text")
        if value is None:
            return None
        return [("set", key, value.strip()) for key in keys]
    return None


_READ = disk.ReadCache(read)


def read_cached(path: Path, game: str) -> ModTexts:
    found = _READ.get(path, game)
    return ModTexts() if found is None else found


@dataclass
class Names:
    """Every entry the mods that load give a text, and who gave it."""

    texts: Dict[str, str] = field(default_factory=dict)
    # key -> the origin of the mod whose text it has.
    by: Dict[str, str] = field(default_factory=dict)
    # key -> the other mods that gave it a different text, earlier in order.
    also: Dict[str, List[str]] = field(default_factory=dict)
    # origin -> ModOps in its texts patch that were not read, or whose
    # condition could not be answered.
    unread: Dict[str, int] = field(default_factory=dict)
    language: str = ""


def build(loadout, language: str, base_text: Callable[[str], str] = lambda key: "") -> Names:
    """The names the mods that load give, in ``language``, in load order.

    ``base_text`` is the game's own text for a key, for a mod that adds to
    an entry rather than replacing it.
    """
    from . import dependencies

    names = Names(language=language)
    if not language:
        return names
    gone = loadout.replaced()
    on = [layer for layer in dependencies.switched_on(loadout) if layer.origin not in gone]
    # Every mod that loads answers "is it loaded?", folder or not.
    present = {
        value.lower() for layer in on
        for value in (layer.mod_id, layer.origin) if value
    }
    loading = sorted((layer for layer in on if layer.folder), key=lambda layer: layer.order)
    labels = {layer.origin: layer.label for layer in loadout.layers}
    for layer in loading:
        path = patch_file(layer.folder, loadout.game, language)
        if path is None:
            continue
        read_in = read_cached(path, loadout.game)
        unread = read_in.unread
        for edit in read_in.edits:
            held = [_holds(condition, present, loadout.game) for condition in edit.conditions]
            if any(answer is None for answer in held):
                unread += 1
                continue
            if not all(held):
                continue
            if edit.kind == "append":
                names.texts[edit.key] = names.texts.get(edit.key, base_text(edit.key) or "") + edit.text
                names.by.setdefault(edit.key, layer.origin)
                continue
            earlier = names.by.get(edit.key)
            if earlier and earlier != layer.origin and names.texts.get(edit.key) != edit.text:
                names.also.setdefault(edit.key, []).append(earlier)
            names.texts[edit.key] = edit.text
            names.by[edit.key] = layer.origin
        if unread:
            names.unread[layer.origin] = unread
    names.by = {key: labels.get(origin, origin) for key, origin in names.by.items()}
    # By name, each once, and not the one whose text it has: two copies of a
    # shared piece in two hosts are one mod to whoever reads this.
    also = {}
    for key, origins in names.also.items():
        said = []
        for origin in origins:
            label = labels.get(origin, origin)
            if label != names.by.get(key) and label not in said:
                said.append(label)
        if said:
            also[key] = said
    names.also = also
    return names


def _holds(condition: str, present, game: str) -> Optional[bool]:
    """A condition on a texts op, answered where it asks which mods are
    loaded; None for anything else, which would be a question about the
    texts file the Studio does not hold."""
    from . import modops

    text = condition.strip().lstrip("!").strip()
    if not (text.startswith("#") or modop.combines(text)):
        return None
    return modops.condition_holds(condition, lambda _guid: None, "", present, None, game)


# -- writing: what the assets a mod of the Studio's adds are called -----------
# The block an export writes into a texts file, marked so the next export can
# find and replace it rather than add a second copy -- and so an author's own
# entries in the same file are never touched.
_BEGIN = (
    "<!-- Anno Modding Studio: the in-game names of the assets this mod adds. "
    "Written on every export; edit them in the Studio. -->"
)
_END = "<!-- /Anno Modding Studio names -->"


def names_of(loadout) -> List[Tuple[str, str]]:
    """``(GUID, in-game name)`` for every asset ``loadout`` creates that has one."""
    from .effective import new_asset_guid

    found = []
    for change in loadout.ordered():
        name = (change.in_game_name or "").strip()
        if change.active and change.creates_asset and name:
            guid = new_asset_guid(change)
            if guid:
                found.append((guid, name))
    return found


def names_block(game: str, entries: List[Tuple[str, str]]) -> str:
    """The marked ModOp adding ``entries`` to a texts file.

    Keyed the way each game looks a name up: Anno 1800 by the asset's own
    GUID, Anno 117 by a LineId the asset points at with its ``OasisId`` --
    the asset's GUID again, as the loader's docs advise and 6,007 of the
    census's Anno 117 assets do (see :func:`link_name`).
    """
    from xml.sax.saxutils import escape

    tag = _KEY_TAG.get(game, "GUID")
    rows = "".join(
        "\n    <Text><%s>%s</%s><Text>%s</Text></Text>" % (tag, guid, tag, escape(name))
        for guid, name in entries
    )
    op = (
        '<ModOp Add="/TextExport/Texts">%s\n  </ModOp>' if game == ANNO_117
        else '<ModOp Type="add" Path="/TextExport/Texts">%s\n  </ModOp>'
    ) % rows
    return "  %s\n  %s\n  %s" % (_BEGIN, op, _END)


def merged(existing: Optional[str], block: str) -> Optional[str]:
    """``existing`` (a texts patch, or None for none) with the Studio's block
    in it: an earlier one taken out, ``block`` put at the end. None when the
    file is not a ModOps document to add to."""
    text = existing or ""
    begin, end = text.find(_BEGIN), text.find(_END)
    if begin >= 0 and end > begin:
        start = text.rfind("\n", 0, begin) + 1
        stop = text.find("\n", end)
        text = text[:start] + (text[stop + 1:] if stop >= 0 else "")
    if not text.strip():
        return "<ModOps>\n%s\n</ModOps>\n" % block if block else ""
    if not block:
        return text
    at = text.rfind("</ModOps>")
    if at < 0:
        return None
    return text[:at].rstrip() + "\n" + block + "\n" + text[at:]


def is_texts_file(relative) -> bool:
    """Whether ``relative``, a path inside a mod folder, is a texts patch
    where either game keeps them -- a bundled mod's included."""
    relative = Path(relative)
    if not _LANGUAGE.match(relative.name):
        return False
    parent = tuple(part.lower() for part in relative.parent.parts)
    for game in _KEY_TAG:
        gui = tuple(part.lower() for part in Path(games.get(game).gui_relpath).parts)
        if parent[-len(gui):] == gui:
            return True
    return False


def names_written(root) -> Dict[str, str]:
    """The names the Studio's own block gives in the texts files of the mod
    at ``root``, by GUID: what :func:`write_names` wrote there on an export.

    Every language holds the same block, so the first file that has one
    answers. Whichever game the mod is for: the key is the asset's GUID in
    both, under the tag each game looks a name up by.
    """
    root = Path(root)
    if not root.is_dir():
        return {}
    for game in _KEY_TAG:
        gui = root / games.get(game).gui_relpath
        if not gui.is_dir():
            continue
        for path in sorted(gui.glob("texts_*.xml")):
            try:
                text = path.read_bytes().decode("utf-8-sig", errors="replace")
            except OSError:
                continue
            begin, end = text.find(_BEGIN), text.find(_END)
            if begin < 0 or end < begin:
                continue
            try:
                block = etree.fromstring(
                    ("<Block>%s</Block>" % text[begin + len(_BEGIN):end]).encode("utf-8")
                )
            except etree.XMLSyntaxError:
                continue
            found: Dict[str, str] = {}
            for tag in _KEY_TAG.values():
                for key, name in _entries(block.iter("Text"), tag):
                    found.setdefault(key, name)
            if found:
                return found
    return {}


def adopt_names(changes, root) -> int:
    """Give each asset ``changes`` create the in-game name the Studio wrote
    for it into the texts files of the mod at ``root``; how many took one.

    A mod exported here and read back in is the same mod: its new assets
    keep the names they were given, to show and to change, and the next
    export writes them again. Without this the block was read as any texts
    patch is, belonged to no change, and that export wrote it out empty.
    """
    from .effective import new_asset_guid

    creating = [
        change for change in changes
        if change.creates_asset and not (change.in_game_name or "").strip()
    ]
    if not creating or root is None:
        return 0
    written = names_written(root)
    taken = 0
    for change in creating:
        name = written.get(str(new_asset_guid(change) or ""), "").strip()
        if name:
            change.in_game_name = name
            taken += 1
    return taken


def files_written(loadout, root) -> List[Path]:
    """The texts files, relative to the mod folder ``root``, an export of
    ``loadout`` writes: every language's where it names an asset it adds,
    and otherwise the ones holding a block to take out."""
    from .games import LANGUAGES

    game = loadout.game
    relative = Path(games.get(game).gui_relpath)
    naming = bool(names_of(loadout))
    found = []
    for language in LANGUAGES.get(game, ()):
        inside = relative / ("texts_%s.xml" % language)
        if not naming:
            try:
                held = (Path(root) / inside).read_bytes().decode("utf-8-sig", errors="replace")
            except OSError:
                continue
            if _BEGIN not in held:
                continue
        found.append(inside)
    return found


def write_names(loadout, root, trouble: Optional[List[str]] = None) -> int:
    """Write the in-game names of the assets ``loadout`` (one mod) creates into
    every language's texts file under ``root``; how many files were written.

    One name for every language, by decision: a language with no entry shows
    the raw id in game. A texts file the mod already has keeps everything in
    it; the Studio's block is added at its end, and replaced on the next
    export. A file that is not a ModOps document is left alone and said.
    """
    from .games import LANGUAGES

    game = loadout.game
    entries = names_of(loadout)
    gui = Path(root) / games.get(game).gui_relpath
    written = 0
    for language in LANGUAGES.get(game, ()):
        path = gui / ("texts_%s.xml" % language)
        raw = path.read_bytes() if path.is_file() else None
        existing = raw.decode("utf-8-sig", errors="replace") if raw is not None else None
        if not entries and (existing is None or _BEGIN not in existing):
            continue
        new = merged(existing, names_block(game, entries) if entries else "")
        if new is None:
            if trouble is not None:
                trouble.append(
                    tr("%s: not a ModOps file, so the names were not added to it", path.name)
                )
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        bom = b"\xef\xbb\xbf" if raw is not None and raw.startswith(b"\xef\xbb\xbf") else b""
        path.write_bytes(bom + new.encode("utf-8"))
        written += 1
    return written


def point_at_own_name(asset, guid: str) -> bool:
    """Point ``asset``, an Anno 117 ``<Asset>``, at a name of its own, and say
    whether that changed it.

    Anno 117 looks an asset's name up by ``Values/Text/OasisId``, and the name
    the Studio writes for an asset is written under the asset's own GUID
    (:func:`names_block`), as the loader's docs advise and nearly every mod
    does.  So that is what the OasisId is set to, whether the asset had none
    or had another: a copy made without a name keeps its original's, and shows
    the original's name, until it is given one of its own.
    """
    values = asset.find("Values")
    if values is None:
        return False
    text = values.find("Text")
    if text is None:
        text = etree.Element("Text")
        standard = values.find("Standard")
        if standard is not None:
            standard.addnext(text)
        else:
            values.insert(0, text)
    oasis = text.find("OasisId")
    if oasis is None:
        oasis = etree.SubElement(text, "OasisId")
    elif (oasis.text or "").strip() == guid:
        return False
    oasis.text = guid
    return True


def link_name(payload: str, guid: str, game: str) -> str:
    """An asset's payload, pointed at its name where the game needs telling:
    :func:`point_at_own_name` for Anno 117. Anno 1800 looks the name up by the
    asset's GUID and needs nothing."""
    if game != ANNO_117 or not guid:
        return payload
    try:
        root = etree.fromstring(payload.encode("utf-8"))
    except etree.XMLSyntaxError:
        return payload
    if not point_at_own_name(root, guid):
        return payload
    for element in root.iter():
        element.tail = None
        if len(element):
            element.text = None
    etree.indent(root, space="  ")
    return etree.tostring(root, pretty_print=True).decode().strip()
