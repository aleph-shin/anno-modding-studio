"""The game's own loader as a second opinion on what the Studio says a mod does.

The Studio reads ModOps by its own rules, measured against the loader once.
Two things follow: a user cannot know, before starting the game, that the
loader will do what the Studio shows -- and when the loader changes, nothing
notices.  When the *Anno Modding Tools* extension is installed its copy of the
loader (``xmltest``, see :mod:`core.xmltest`) can answer both, so this asks it:

**The check** (:func:`check`).  The mod being exported or played is written
out the way Export writes it and handed to the loader, on top of the mods it
builds on -- the loaded mods ahead of it that touch the same assets.  What the
loader leaves each asset as is compared with what the Studio shows, asset for
asset, the way the census compares them.  One mod takes a second or two.

**The probes** (:func:`probe`).  A handful of small patches, each pinning one
reading rule -- a loop, a flag merge, a search condition -- read by the Studio
and run by the loader against a small file of their own, and compared the
same way.  Run when a new version of the extension turns up: the loader
changing under the Studio is exactly what they are for.

A second opinion, never a dependency.  Nothing in the Studio needs the
extension; where it is missing the verdict is "not available" and everything
else works as before.  And it is the *extension's* copy of the loader, which
may not be the one the game has -- the verdict says which it asked.
"""

from __future__ import annotations

import contextlib
import copy
import difflib
import hashlib
import os
import re
import shutil
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Set, Tuple

from lxml import etree

from . import (
    build,
    builds,
    games,
    generator,
    modop,
    modops,
    patching,
    reader,
    recording,
    xmltest,
)
from .changes import Loadout, ModLayer
from .i18n import STORED, join_list, number, tr, tr_noop, tr_stored, trn
from .i18n import number as number_text

# An asset nothing can find by its GUID any more: removed, or stripped of it.
GONE = "(no asset by that GUID)"
EXAMPLES = 8
DIFF_LINES = 40

_QUIET = etree.XMLParser(remove_blank_text=True, remove_comments=True, resolve_entities=False)


# ---------------------------------------------------------------- comparing
def canonical(element) -> str:
    """An element as text two equal trees agree on: no comments, no layout."""
    element = copy.deepcopy(element)
    for node in element.iter():
        if not isinstance(node.tag, str):
            continue
        if node.text is not None:
            node.text = node.text.strip() or None
        if node.tail is not None:
            node.tail = node.tail.strip() or None
    for comment in element.xpath("//comment()"):
        parent = comment.getparent()
        if parent is not None:
            parent.remove(comment)
    return etree.tostring(element, method="c14n").decode("utf-8")


def canonical_fragment(text: str) -> str:
    if not text:
        return ""
    try:
        wrapper = etree.fromstring(("<r>%s</r>" % text).encode("utf-8"), _QUIET)
    except etree.XMLSyntaxError:
        return " ".join(text.split())
    return canonical(wrapper)


def pretty(element) -> Optional[str]:
    """An asset as text, laid out and spelled as the game reads it; None for none."""
    if element is None:
        return None
    flat = etree.fromstring(canonical(element).encode("utf-8"), _QUIET)
    _as_the_game_reads(flat)
    return etree.tostring(flat, pretty_print=True).decode("utf-8")


def _as_the_game_reads(element) -> None:
    """Take out what differs between two assets the game reads the same.

    Two things, each found by tracing a difference to its end rather than
    assumed. The order of fields that each appear once: the shipped Anno 1800
    file has ``IsBaseAutoCreateAsset`` before ``Values`` in 829 of its
    ``TriggerCondition``s and after it in one, so the game cannot be reading it
    by position. And text standing between a section's fields, like the stray
    ``>`` *Fast Move-In* leaves inside ``Residence7``. A list keeps its order:
    entries of the same name are read in turn.
    """
    for node in list(element.iter()):
        if not isinstance(node.tag, str) or not len(node):
            continue
        node.text = None
        children = [child for child in node if isinstance(child.tag, str)]
        for child in children:
            child.tail = None
        tags = [child.tag for child in children]
        if len(set(tags)) == len(tags):
            for child in sorted(children, key=lambda c: c.tag):
                node.append(child)


def prepared(element, inert) -> Tuple[Optional[str], Optional[str]]:
    """An asset as text twice: exactly, and with the entries the game reads as absent gone.

    An asset nothing can find by its GUID any more is ``GONE`` either way. The
    loader's diff cannot tell an asset a mod deleted from one it stripped of
    ``Values/Standard`` (see :class:`core.xmltest.Applied`), and the Studio shows
    the second kind as a husk with no GUID in it -- the same state, spelled two
    ways, which read as eighty differences for *Enbesa Quest Skip*.
    """
    if element is None or element.find("Values/Standard/GUID") is None:
        return GONE, GONE
    stripped = copy.deepcopy(element)
    exact = pretty(element)
    # Most assets have nothing the game reads as absent, and laying one out
    # is the cost of a comparison: not twice where the two are one.
    return exact, pretty(stripped) if strip_inert(stripped, inert) else exact


def strip_inert(element, inert) -> bool:
    """:func:`recording.strip_inert`, and whether it took anything out."""
    before = sum(1 for _node in element.iter())
    recording.strip_inert(element, inert)
    return sum(1 for _node in element.iter()) != before


def mark(text: Optional[str]) -> Optional[bytes]:
    """A text as a few bytes two equal texts agree on: what is kept of an
    asset that will be compared again, where the text itself would be a
    gigabyte for a mod touching eighteen thousand of them."""
    return None if text is None else hashlib.blake2b(text.encode("utf-8"), digest_size=16).digest()


class Views:
    """One source's assets as :func:`compare_views` compares them, each
    prepared once however many comparisons ask for it.

    Preparing an asset is nearly all of a comparison's time, and the census
    puts the same source through several: the Studio's reading against the
    loader on the mod and again against the loader on each export.  Only for
    a source that stays as it is while the views are kept.
    """

    def __init__(self, source, inert):
        self.source = source
        self.inert = inert
        self._marks: Dict[str, Tuple[Optional[bytes], Optional[bytes]]] = {}

    def marks(self, guid: str) -> Tuple[Optional[bytes], Optional[bytes]]:
        """The asset exactly and as it is meant, each as its :func:`mark`."""
        if guid not in self._marks:
            exact, meant = prepared(self.source(guid), self.inert)
            self._marks[guid] = mark(exact), mark(meant)
        return self._marks[guid]

    def meant(self, guid: str) -> Optional[str]:
        return prepared(self.source(guid), self.inert)[1]


def compare_views(guids, one, two, inert, labels: Tuple[str, str]) -> Dict[str, Any]:
    """Every asset in ``guids`` as two sources have it, and where they part.

    A source is what gives an asset by its GUID, or the :class:`Views` of one
    where it is compared more than once.
    """
    one = one if isinstance(one, Views) else Views(one, inert)
    two = two if isinstance(two, Views) else Views(two, inert)
    count, strict = 0, 0
    examples: List[Dict[str, Any]] = []
    for guid in sorted(guids, key=lambda g: (len(g), g)):
        exact_one, meant_one = one.marks(guid)
        exact_two, meant_two = two.marks(guid)
        if exact_one != exact_two:
            strict += 1
        if meant_one == meant_two:
            continue
        count += 1
        if len(examples) < EXAMPLES:
            diff = difflib.unified_diff(
                (one.meant(guid) or "").splitlines(), (two.meant(guid) or "").splitlines(),
                labels[0], labels[1], n=1, lineterm="",
            )
            examples.append({"guid": guid, "diff": list(diff)[:DIFF_LINES]})
    return {"assets": count, "assets_strict": strict, "examples": examples}


# ---------------------------------------------------------------- reading
def read_folders(folders, roots, index, game: str, logged=None, keep_inert: bool = False):
    """A session read of mod folders, and the loadout the mods-folder import
    would make of it: ``(session, loadout)``."""
    session = reader.analyse_all(
        folders,
        index,
        keep_inert_removals=keep_inert,
        reading=modops.Reading(),
        game=game,
        roots=roots,
        logged=logged,
    )
    loadout = Loadout(game=game, source_path=str(getattr(index, "source_path", "") or ""))
    for mod in session.reads:
        meta = mod.report.metadata
        loadout.set_layer(
            ModLayer(
                origin=mod.origin,
                name=meta.get("mod_name") or mod.origin,
                folder=str(mod.folder),
                category=meta.get("category", ""),
                version=meta.get("version", ""),
                creator=meta.get("creator", ""),
                description=meta.get("description", ""),
                mod_id=meta.get("mod_id", ""),
                order=mod.order,
                unread=mod.unread,
                offered=(meta["options"].to_dict() if meta.get("options") else {}),
                load_after=list(meta.get("load_after") or []),
                declared=dict(meta.get("declared") or {}),
            )
        )
    patching.reattach(loadout)
    for mod in session.reads:
        for op in mod.report.ops:
            loadout.set_op(op)
    loadout.unread = [entry for mod in session.reads for entry in mod.report.unread(mod.origin)]
    loadout.take_read([change for mod in session.reads for change in mod.changes])
    return session, loadout


# ---------------------------------------------------------------- the check
AGREES = "agrees"
DIFFERS = "differs"
UNAVAILABLE = "unavailable"
FAILED = "failed"
NOTHING = "nothing"
# A check that could run and was not: its stack was past AUTOMATIC_LIMIT, or
# Play went on without it. Its ``why`` says which, and how to run it.
NOT_RUN = "not run"


class _Said:
    """The line of a verdict, one mod's and the whole loadout's: one ladder
    of states and of what follows them, with each verdict's own sentences
    on it.  It was written out for each, and the two had parted: a check of
    a mod that failed was said without its full stop."""

    # What the ladder reads, which both verdicts have.
    state: str
    loader: Optional[xmltest.Loader]
    checked: int
    not_shown: int
    why: str

    # Its own sentences: a check that failed, one with nothing to check, and
    # the three of one that ran, each given what the loader is called.
    def _failed(self) -> str:
        raise NotImplementedError

    def _nothing(self) -> str:
        raise NotImplementedError

    def _not_run(self) -> str:
        raise NotImplementedError

    def _none_left(self, loader: str) -> str:
        raise NotImplementedError

    def _agrees(self, loader: str) -> str:
        raise NotImplementedError

    def _differs(self, loader: str) -> str:
        raise NotImplementedError

    def _extras(self) -> List[str]:
        """What follows the sentence, after the assets not shown."""
        raise NotImplementedError

    def _ended(self, said: str) -> str:
        raise NotImplementedError

    def describe(self) -> str:
        """One line: for the Export tab, or the whole loadout's dialog."""
        if self.state == UNAVAILABLE:
            return tr("Not checked by the game's loader: %s", self.why)
        if self.state == FAILED:
            return self._failed()
        if self.state == NOTHING:
            return self._nothing()
        if self.state == NOT_RUN:
            return self._not_run()
        # A verdict that agrees or differs came from a loader: with none, the
        # check is UNAVAILABLE and said above.
        loader = tr_stored(self.loader.said) if self.loader else tr("the mod loader")
        if not self.checked:
            # Every asset changed was set apart, for a reason said after:
            # "agrees on all 0 assets" is no agreement.
            said = self._none_left(loader)
        elif self.state == AGREES:
            said = self._agrees(loader)
        else:
            said = self._differs(loader)
        extras = []
        if self.not_shown:
            # Translators: more assets, after "sees 1 of the 3 assets My Mod
            # changes differently;".
            extras.append(trn("%(n)s more changed by ops the Studio carries unread",
                              "%(n)s more changed by ops the Studio carries unread", self.not_shown))
        extras.extend(self._extras())
        return self._ended(said + ("; " + "; ".join(extras) if extras else ""))


@dataclass
class Verdict(_Said):
    """What the loader made of one mod, against what the Studio shows."""

    state: str
    origin: str = ""
    name: str = ""
    loader: Optional[xmltest.Loader] = None
    # The mods the check stood this one on, by name, in load order.
    on_top_of: List[str] = field(default_factory=list)
    checked: int = 0
    # Assets the two see differently: {"guid", "label", "diff"}.
    differing: List[dict] = field(default_factory=list)
    differing_count: int = 0
    # Assets the loader changed and the Studio shows nothing for, where the
    # mod has ops the Studio carries unread: what those ops do, not a
    # disagreement.
    not_shown: int = 0
    # What the loader wrote about the mod itself, each line under its level:
    # an op that matches nothing, and whatever else it warned of or failed at.
    warnings: List[str] = field(default_factory=list)
    # Assets the loader was handed differently from how the Studio reads the
    # mods underneath: about those mods, not this one, and not judged here.
    beneath: int = 0
    # Assets it adds under a GUID something else has too -- another loaded
    # mod, the base game, or the mod itself a second time: GUID -> what else.
    shared: Dict[str, List[str]] = field(default_factory=dict)
    why: str = ""
    seconds: float = 0.0
    # When the build crashed and :data:`xmltest.FALLBACK` answered: why that
    # answer is short of something, as a sentence.
    fallback: str = ""

    @property
    def is_agreement(self) -> bool:
        return self.state == AGREES

    def _failed(self) -> str:
        # The full stop is put on here, as _ended puts it on a check that ran:
        # the sentence in the catalog is the one it had, and keeps its German.
        return tr("The game's loader could not check %(mod)s: %(why)s", mod=self.name, why=self.why) + "."

    def _nothing(self) -> str:
        return tr("%s has no ModOps for the game's loader to check.", self.name)

    def _not_run(self) -> str:
        # Translators: %(why)s is one or two sentences, such as "The check
        # would run 312 mods through the loader, …".
        return tr("The game's loader has not checked %(mod)s. %(why)s", mod=self.name, why=self.why)

    def _none_left(self, loader: str) -> str:
        return tr("Checked by the game's loader (%(loader)s): no asset %(mod)s changes "
                  "was left to compare", loader=loader, mod=self.name)

    def _agrees(self, loader: str) -> str:
        return trn("Checked by the game's loader (%(loader)s): agrees on the one asset "
                   "%(mod)s changes",
                   "Checked by the game's loader (%(loader)s): agrees on all %(n)s assets "
                   "%(mod)s changes", self.checked, loader=loader, mod=self.name)

    def _differs(self, loader: str) -> str:
        return trn("Checked by the game's loader (%(loader)s): sees the one asset %(mod)s "
                   "changes differently",
                   "Checked by the game's loader (%(loader)s): sees %(differing)s of the "
                   "%(n)s assets %(mod)s changes differently", self.checked,
                   loader=loader, differing=number(self.differing_count), mod=self.name)

    def _extras(self) -> List[str]:
        extras = []
        if self.beneath:
            # Translators: assets, after "sees 1 of the 3 assets My Mod changes
            # differently;".
            extras.append(trn("%(n)s where the mods under it already differ",
                              "%(n)s where the mods under it already differ", self.beneath))
        if self.shared:
            extras.append(trn("%(n)s asset it adds shares its GUID with another asset",
                              "%(n)s assets it adds share their GUIDs with other assets",
                              len(self.shared)))
        unmatched, other = _sorted_out(self.warnings)
        if unmatched:
            extras.append(trn("%(n)s op matches nothing", "%(n)s ops match nothing", unmatched))
        if other:
            extras.append(_other_lines(other))
        # Which build answered, and what its answer is short of, is in the
        # details: the whole loadout's line has none, and says it all.
        if self.fallback:
            # Translators: after "sees 1 of the 3 assets My Mod changes
            # differently;" -- xmltest3 crashed, and xmltest2 answered.
            extras.append(tr("answered by a second build after the first crashed"))
        return extras

    def _ended(self, said: str) -> str:
        return said + "."

    def details(self) -> List[str]:
        """Everything behind the line, for its tooltip or a dialog."""
        lines = [self.describe()]
        if self.fallback:
            lines.append(self.fallback + ".")
        if self.on_top_of:
            lines.append(tr("Run on top of: %s.", ", ".join(self.on_top_of)))
        for found in self.differing:
            lines.append("")
            lines.append(tr("%(asset)s (GUID %(guid)s):",
                            asset=found.get("label") or found["guid"], guid=found["guid"]))
            if found.get("fields"):
                lines.append("  " + tr("They differ at: %s", ", ".join(found["fields"])))
            if found.get("changes"):
                lines.append("  " + tr("%(mod)s changes there: %(changes)s", mod=self.name,
                                       changes="; ".join(change["said"] for change in found["changes"][:EXAMPLES])))
            elif "changes" in found:
                lines.append("  " + tr("None of %s's changes is to this asset: the loader changed it by "
                                       "a copy or a search the Studio reads another way.", self.name))
            lines.extend("  " + line for line in found["diff"])
        if self.shared:
            lines.append("")
            lines.append(tr("Added under a GUID something else also has, and "
                            "the game finds only one of them:"))
            lines.extend("  " + tr("%(guid)s, also added by %(mods)s", guid=guid, mods=join_list(names))
                         for guid, names in sorted(self.shared.items())[:EXAMPLES])
        if self.warnings:
            lines.append("")
            lines.append(tr("The loader warned:"))
            lines.extend("  " + line for line in self.warnings[:EXAMPLES])
        return lines


# How a ModOp names an asset it reads rather than changes: ``@100586/…`` in a
# Content=, a condition or a ModValue, and ``Standard/GUID='100586'`` in a search.
_READS = re.compile(r"""@(\d{3,})|Standard/GUID\s*=\s*['"](\d+)['"]""")
# Per file: the size and time it was read at, and what it names. One entry a
# file, replaced when the file changes, rather than one for every version of
# it the session has seen.
_READ_CACHE: Dict[str, Tuple[Tuple[int, int], frozenset]] = {}


def _patch_files(folder: str) -> List[Tuple[str, Tuple[int, int]]]:
    """Every ``*.xml`` under ``folder``, with its size and time.

    In one walk of the folder: on Windows the listing of a folder carries
    both, so no file is asked on its own. ``Path.rglob`` and a ``stat`` a
    file were most of a check of *Production Works*' 152 mods. Folders that
    are links are not walked into, as ``rglob`` does not."""
    found = []
    waiting = [str(Path(folder))]
    while waiting:
        here = waiting.pop()
        try:
            with os.scandir(here) as entries:
                for entry in entries:
                    try:
                        if entry.is_dir(follow_symlinks=False):
                            waiting.append(entry.path)
                        elif entry.name.lower().endswith(".xml") and entry.is_file():
                            stat = entry.stat()
                            found.append((entry.path, (stat.st_size, stat.st_mtime_ns)))
                    except OSError:
                        continue
        except OSError:
            continue
    return sorted(found)


def _reads_in(folder: Optional[str]) -> frozenset:
    """Every asset a mod's patch files name as something they read, by GUID.

    Kept per file, by its size and time: a mod's files are read once however
    often it is checked."""
    found: Set[str] = set()
    if not folder or not Path(folder).is_dir():
        return frozenset()
    for name, when in _patch_files(folder):
        held = _READ_CACHE.get(name)
        if held is None or held[0] != when:
            try:
                text = Path(name).read_text(encoding="utf-8", errors="replace")
            except OSError:
                text = ""
            held = _READ_CACHE[name] = (when, frozenset(one or two for one, two in _READS.findall(text)))
        found |= held[1]
    return frozenset(found)


# One op of a mod and what it reads: the assets it is aimed at, or None for
# one that finds them by a search or a template, which may be any of them;
# every GUID it names as a read, with the conditions of the groups and
# includes it stands inside -- and the searches it reads by, ``//`` paths in
# a condition or a ``Content=``, which start with a slash where a GUID does
# not; and the searches it is aimed by.
_Aimed = Tuple[Optional[frozenset], frozenset, frozenset]
# Per patch file, by the game and its size and time as _READ_CACHE: its own
# path, resolved and normcased, its ops, the files its <Include>s name (the
# same) with what the groups around them read, and whether it read as a
# patch at all.
_OP_CACHE: Dict[str, Tuple[Tuple[str, Tuple[int, int]], str, Tuple[_Aimed, ...],
                           Tuple[Tuple[str, frozenset], ...], bool]] = {}
# Per mod folder, by every file's stamp: its ops put together, includes and
# all, since a loadout's stack asks it of hundreds of mods at every check.
_AIMED_CACHE: Dict[str, Tuple[tuple, List[_Aimed]]] = {}
# And per carried op's text, at most so many: a mod's unread ops are asked
# after at every check that stands on it.
_TEXT_CACHE: Dict[Tuple[str, str], List[_Aimed]] = {}
_TEXTS_KEPT = 200_000


def _named_reads(text: str) -> Set[str]:
    return {one or two for one, two in _READS.findall(text or "")}


def _attr_reads(node) -> Set[str]:
    found = _named_reads(" ".join(node.attrib.values()))
    for name, value in node.attrib.items():
        asked = (value or "").strip()
        if name.lower() == "condition":
            asked = asked.lstrip("!").strip()
        elif name.lower() == "content":
            asked = asked[: -len("/text()")] if asked.endswith("/text()") else asked
        else:
            continue
        # As :func:`reader.search_paths` asks it, so the index has the answer.
        if asked.startswith("//"):
            found.add(asked)
    return found


def _all_reads(node) -> Set[str]:
    """What ``node`` and everything in it read, comments left out: an op
    taken out by commenting it reads nothing."""
    found: Set[str] = set()
    for one in node.iter():
        if isinstance(one.tag, str):
            found |= _attr_reads(one)
            found |= _named_reads(one.text)
        if one is not node:
            found |= _named_reads(one.tail)
    return found


def _created_by(node) -> Set[str]:
    """The GUIDs of the assets ``node`` writes out whole."""
    return {(guid.text or "").strip() for standard in node.iter("Standard")
            for guid in standard.findall("GUID") if (guid.text or "").strip()}


def _aimed_at(node, game: str) -> Tuple[Optional[frozenset], frozenset]:
    """The assets a ``<ModOp>`` is about: those it names, with any it nests,
    and the ones it adds by their own GUIDs -- None when one of them searches
    -- and the paths of its searches, as the reader asks the index them."""
    found: Optional[set] = set()
    finds = set()
    for op in node.iter("ModOp"):
        attrs = dict(op.attrib)
        named = [guid.strip() for guid in modop.assets_named(attrs, game) if guid.strip()]
        if named and found is not None:
            found.update(named)
            continue
        if not named:
            found = None
            kind, _guid, path = modop.type_and_target(
                modop.as_117_reads(attrs) if game == games.ANNO_117 else attrs, game)
            if kind and path:
                finds.add(path)
    return (None if found is None else frozenset(found | _created_by(node))), frozenset(finds)


def _aimed_ops(root, game: str, source: Optional[Path] = None) -> Tuple[List[_Aimed], List[Tuple[str, frozenset]]]:
    """Each op under ``root`` with what it reads, as :data:`_Aimed`, and the
    ``<Include>``s under it: (the file, resolved and normcased, what the
    groups around the include read). ``source`` is the file ``root`` is
    read from, to find an include's file by; none for a carried op's text."""
    ops: List[_Aimed] = []
    includes: List[Tuple[str, frozenset]] = []

    def walk(parent, around: frozenset) -> None:
        for node in parent:
            if not isinstance(node.tag, str):
                continue
            if node.tag == "ModOp":
                aimed, finds = _aimed_at(node, game)
                ops.append((aimed, frozenset(_all_reads(node) | around), finds))
            elif modops.is_include(node):
                if source is None:
                    continue
                try:
                    target = modops.include_target(modops.include_file(node), source, modops.mod_root(source))
                except (OSError, ValueError):
                    continue
                includes.append((_file_key(target), frozenset(_attr_reads(node) | around)))
            elif node.tag.lower() == "group" and (
                (read := modops.group_reading(node, game) if node.attrib else ({}, 1)) is not None
            ):
                if "skip" in read[0]:
                    # Any Skip switches the group off: nothing inside runs.
                    continue
                # A condition on a group is asked before every op inside it.
                walk(node, frozenset(_attr_reads(node) | around))
            elif modops.adds_an_asset(node, game):
                made = _created_by(node)
                ops.append((frozenset(made) if made else None, frozenset(_all_reads(node) | around),
                            frozenset()))
            else:
                # Something the Studio does not read: whatever it does, it may
                # do it anywhere.
                ops.append((None, frozenset(_all_reads(node) | around), frozenset()))

    walk(root, frozenset())
    return ops, includes


def _file_key(path: Path) -> str:
    with contextlib.suppress(OSError, ValueError):
        path = Path(path).resolve()
    return os.path.normcase(str(path))


def _aimed_in(folder: Optional[str], game: str) -> List[_Aimed]:
    """Every op of a mod's patch of the game's assets, with what it reads:
    :data:`_Aimed`.

    The patch is the file the loader is handed for the assets and every file
    its ``<Include>``s reach, as the loader follows them: a mod's patches of
    its texts or infotips are other files of the game's, and their searches
    were asked of the assets for nothing. An included file's ops read what the
    groups around its ``<Include>`` ask as well. A file that does not read as
    a patch counts as one op that may be aimed anywhere and reads every GUID
    the file names, as :func:`_reads_in` reads it; and where one the patch
    reaches does not, so that its includes cannot be followed, every file of
    the folder counts."""
    if not folder or not Path(folder).is_dir():
        return []
    stamps = _patch_files(folder)
    signature = (game, tuple(stamps))
    held_folder = _AIMED_CACHE.get(folder)
    if held_folder is not None and held_folder[0] == signature:
        return held_folder[1]

    files: Dict[str, Tuple[Tuple[_Aimed, ...], Tuple[Tuple[str, frozenset], ...], bool]] = {}
    for name, when in stamps:
        held = _OP_CACHE.get(name)
        if held is None or held[0] != (game, when):
            path = Path(name)
            root, _repairs = modops.read_patch(path)
            ops: List[_Aimed]
            includes: List[Tuple[str, frozenset]]
            if root is None:
                try:
                    text = path.read_text(encoding="utf-8", errors="replace")
                except OSError:
                    text = ""
                named = frozenset(_named_reads(text))
                ops, includes = ([(None, named, frozenset())] if named else []), []
            else:
                ops, includes = _aimed_ops(root, game, path)
            held = _OP_CACHE[name] = ((game, when), _file_key(path), tuple(ops), tuple(includes),
                                      root is not None)
        files[held[1]] = (held[2], held[3], held[4])

    # The files the loader reads for the assets.
    start = _file_key(Path(folder) / games.get(game).asset_relpath)
    reached = {start} if start in files else set()
    waiting = list(reached)
    while waiting:
        key = waiting.pop()
        if not files[key][2]:
            reached = set(files)
            break
        for target, _asked in files[key][1]:
            if target in files and target not in reached:
                reached.add(target)
                waiting.append(target)

    # What each file is included under, through includes of includes.
    around: Dict[str, frozenset] = {}
    grew = True
    while grew:
        grew = False
        for key, (_ops, inside, _read) in files.items():
            above = around.get(key, frozenset())
            for target, asked in inside:
                more = asked | above
                if not more <= around.get(target, frozenset()):
                    around[target] = around.get(target, frozenset()) | more
                    grew = True
    found: List[_Aimed] = []
    for key, (kept, _inside, _read) in files.items():
        if key not in reached:
            continue
        under = around.get(key)
        found.extend(kept if not under else [(aimed, read | under, finds) for aimed, read, finds in kept])
    _AIMED_CACHE[folder] = (signature, found)
    return found


def _aimed_in_text(source: str, game: str) -> List[_Aimed]:
    """:func:`_aimed_in` for a carried op's text, which may be one op or a
    group of them: one op aimed anywhere when it does not read as XML."""
    held = _TEXT_CACHE.get((game, source))
    if held is not None:
        return held
    try:
        root = etree.fromstring(("<r>%s</r>" % source).encode("utf-8"), _QUIET)
    except etree.XMLSyntaxError:
        reads = frozenset(_named_reads(source))
        held = [(None, reads, frozenset())] if reads else []
    else:
        held = _aimed_ops(root, game)[0]
    if len(_TEXT_CACHE) > _TEXTS_KEPT:
        _TEXT_CACHE.clear()
    _TEXT_CACHE[(game, source)] = held
    return held


def _stack(loadout: Loadout, origin: str, index) -> List[str]:
    """The mods a check of ``origin`` stands on, then ``origin``.

    The loaded mods ahead of it in load order that are on and that change an
    asset it changes -- or one it *reads*: an asset it copies from, or asks a
    condition of. *Item Sockets* copies each building's socket count from the
    town hall's, which *Item Buildings add additional Slot* raises, ahead of it;
    standing on only the mods that changed the same buildings, the loader
    copied the shipped count and read as disagreeing on 189 of them. And what
    those mods read in turn, since one pulled in may copy from a third. Plus
    whatever it names as the mod it patches or loads after.

    Of a mod pulled in, only what its ops on the assets wanted read, and
    what its searches read, which may reach any: every GUID anywhere in its
    folder took a loadout's one-change mod onto 311 mods (H2-15).

    And what a search finds in the shipped file -- ``index``, the game's --
    is read too: the assets ``origin``'s own searches find, and those a condition or
    a copy of any op counted asks after. *Revival of the Low Tier Goods*
    puts a new description on every asset showing the shipped one; *Orchard
    Rework*, ahead of it, gives seven orchards another, so in the game the
    search passes them by. Standing on the mods without it, the loader found
    them and changed all seven. Asked for, so that no caller can leave it
    out: with None, those mods are not found.
    """
    touching: Dict[str, set] = {}
    for change in loadout.active():
        # A new asset by its own GUID as well: *Easy Seed Search* unlocks
        # seeds other mods add, and standing on nothing, its ops found none.
        touching.setdefault(change.origin, set()).update({change.guid, *_touched(change)})
    # What it declares it loads after, and for a patch what it needs: the
    # own mod declares nothing of the kind itself.
    record = loadout.mod(origin)
    named = {str(value).strip().lower() for value in (record.load_after if record else [])}
    if record is not None:
        named.update(origin.strip().lower() for origin in loadout.needed(record))

    # What each mod's carried ops and kept ops say, for a mod with no folder:
    # gathered once, since a loadout's stack asks it of hundreds of mods.
    carried: Dict[str, List[str]] = {}
    for entry in list(loadout.unread) + list(loadout.ops):
        if getattr(entry, "source", ""):
            carried.setdefault(entry.origin, []).append(entry.source)

    def reads(one: str) -> set:
        held = loadout.layer(one)
        return set(_reads_in(held.folder if held is not None else None)).union(
            *(_named_reads(source) for source in carried.get(one, [])))

    finder = getattr(getattr(index, "index", index), "find_matching", None)

    def found_by(read: set) -> set:
        """``read`` with its searches answered, as the assets they find in
        the shipped file."""
        guids = {one for one in read if not one.startswith("/")}
        asked = sorted(read - guids)
        if asked and finder is not None:
            answers = finder(asked)
            guids.update(guid for path in asked for guid in answers.get(path, ()))
        return guids

    def ops_of(one: str) -> List[_Aimed]:
        held = loadout.layer(one)
        ops = _aimed_in(held.folder if held is not None else None, loadout.game)
        for source in carried.get(one, []):
            ops = ops + _aimed_in_text(source, loadout.game)
        return ops

    # Of each mod pulled in, its ops whose reads are not in ``wanted`` yet.
    waiting: Dict[str, List[_Aimed]] = {}

    def widened(one: str) -> set:
        """What the ops of ``one`` that reach an asset wanted read, and that
        is not wanted yet; each op is counted once."""
        ops = waiting.get(one)
        if ops is None:
            ops = ops_of(one)
        found: set = set()
        left: List[_Aimed] = []
        for aimed, read, finds in ops:
            if aimed is None or aimed & wanted:
                found |= read
            else:
                left.append((aimed, read, finds))
        waiting[one] = left
        return found_by(found) - wanted

    rank = loadout.layer_rank(origin)
    gone = loadout.replaced()
    candidates = []
    for other in loadout.origins(loaded=True):
        if other == origin or loadout.layer_rank(other) >= rank:
            continue
        held = loadout.layer(other)
        if held is None or not held.active or other in gone:
            continue
        candidates.append(other)

    wanted = set(touching.get(origin, set())) | reads(origin)
    wanted |= found_by({path for _aimed, read, finds in ops_of(origin) for path in read | finds
                        if path.startswith("/")})
    chosen: set = set()
    grew = True
    while grew:
        grew = False
        for other in candidates:
            if other not in chosen:
                held = loadout.layer(other)
                if held is None:
                    continue
                keys = {held.origin.lower(), (held.mod_id or "").lower()}
                if not (touching.get(other, set()) & wanted or keys & named):
                    continue
                chosen.add(other)
                grew = True
            # Asked again as ``wanted`` grows: an op of a mod already in
            # may be on an asset only now wanted.
            more = widened(other)
            if more:
                wanted |= more
                grew = True
    return [other for other in candidates if other in chosen] + [origin]


def _only(loadout: Loadout, origins: Sequence[str]) -> Loadout:
    """``loadout`` narrowed to ``origins``' changes, for the Studio's side."""
    view = Loadout(game=loadout.game, source_path=loadout.source_path)
    # What places the loadout's own mod in load order: last on a loadout, and
    # by its name otherwise. Without these the view ranked it as a mod called
    # "My Anno Mod", which put a loadout's own mod under the mods it overrides.
    view.own.loads_last = loadout.own.loads_last
    view.own.name, view.own.category, view.own.mod_id = loadout.own.name, loadout.own.category, loadout.own.mod_id
    view.layers = [layer for layer in loadout.layers if layer.origin in origins]
    view.changes = {key: change for key, change in loadout.changes.items() if change.origin in origins}
    view.ops = [op for op in loadout.ops if op.origin in origins]
    return view


def _as_shipped(view: Loadout, shipped: Dict[str, Loadout]) -> Loadout:
    """``view`` with each mod of ``shipped`` as read there in place of its
    reading in the loadout."""
    for origin, alone in shipped.items():
        if view.layer(origin) is None:
            continue
        view.changes = {key: change for key, change in view.changes.items() if change.origin != origin}
        view.changes.update(
            (key, change) for key, change in alone.changes.items() if change.origin == origin
        )
        view.ops = [op for op in view.ops if op.origin != origin] + [
            op for op in alone.ops if op.origin == origin
        ]
    return view


_CREATED = re.compile(r"<Standard>\s*<GUID>\s*(\d+)\s*</GUID>")
def _ops_in(source: str) -> List[Dict[str, str]]:
    """The attributes of each ModOp in ``source``, which may be one op or a
    group of them; none where it does not read as XML."""
    try:
        root = etree.fromstring(("<r>%s</r>" % source).encode("utf-8"), _QUIET)
    except etree.XMLSyntaxError:
        return []
    return [dict(node.attrib) for node in root.iter("ModOp")]


def _named_in(source: str, game: str) -> List[str]:
    """The GUIDs the ops in ``source`` are aimed at, as ``game`` reads them."""
    return [guid for attrs in _ops_in(source) for guid in modop.assets_named(attrs, game)]


def _touched(change) -> List[str]:
    """The assets a change is about: the new ones it creates by their own
    GUIDs -- a change that adds an asset is filed under the one it goes
    beside, which it does not change -- and any other by its own."""
    if change.creates_asset:
        return _CREATED.findall(change.payload or "") or [change.guid]
    return [change.guid]


def _taken_twice(loadout: Loadout, index) -> List:
    """The GUIDs taken twice that neither side of a check can be compared
    on, as :mod:`repeats` finds them: an identical copy, one a mod tidies up
    after and one a mod removes outright are not among them. Both checks ask
    here, so the one mod's and the whole loadout's set apart the same."""
    from . import repeats

    return repeats.playing(repeats.find(loadout, index))


def _shared(loadout: Loadout, origin: str, index) -> Dict[str, List[str]]:
    """The assets ``origin`` adds under a GUID something else has as well --
    another loaded mod that is on, the base game, or ``origin`` itself a
    second time: GUID -> what else, by name.

    Two assets under one GUID are two things the game cannot tell apart: the
    loader's look-up finds the first, and ``xmltest`` prints one of them, so
    neither side of a check is about the other. *Trade Union Rebalance* and
    *Extended Agriculture Modules*, by one author, both add a 1500301983.
    Every one of :func:`_taken_twice` that ``origin`` has a copy in is
    here, as the whole loadout's check sets each of them apart."""
    found = {}
    for repeat in _taken_twice(loadout, index):
        mine = [copy for copy in repeat.copies if copy.origin == origin]
        if not mine:
            continue
        names = []
        if repeat.shipped:
            names.append(tr("the base game (%s)", repeat.shipped))
        names.extend(
            loadout.label_of(other)
            for other in repeat.origins if other != origin
        )
        if len(mine) > 1:
            names.append(tr("this mod itself"))
        found[repeat.guid] = names
    return found


def _bare(path: str) -> Tuple[str, ...]:
    """A change's path as the fields it walks, without what picks an entry:
    ``/Values/Cost/Costs/Item[Ingredient='1011']/Amount`` is
    ``("Values", "Cost", "Costs", "Item", "Amount")``."""
    flat, depth = [], 0
    for char in path or "":
        if char == "[":
            depth += 1
        elif char == "]":
            depth = max(0, depth - 1)
        elif not depth:
            flat.append(char)
    return tuple(part for part in "".join(flat).split("/") if part and part not in (".", "self::node()"))


def _parted(one, two, here: Tuple[str, ...] = ()) -> List[Tuple[str, ...]]:
    """Where two copies of an asset part, as the fields that differ.

    Entries of one name are a list: when the two hold as many, each pair is
    followed down; when not, the list itself is where they part -- an entry
    added or taken away is about the list, not about any one entry in it."""
    if one.tag != two.tag:
        return [here]
    found: List[Tuple[str, ...]] = []
    kids_one: Dict[str, list] = {}
    kids_two: Dict[str, list] = {}
    for kid in one:
        if isinstance(kid.tag, str):
            kids_one.setdefault(kid.tag, []).append(kid)
    for kid in two:
        if isinstance(kid.tag, str):
            kids_two.setdefault(kid.tag, []).append(kid)
    for tag in list(kids_one) + [tag for tag in kids_two if tag not in kids_one]:
        mine, theirs = kids_one.get(tag, []), kids_two.get(tag, [])
        if len(mine) != len(theirs):
            found.append(here + (tag,))
            continue
        for a, b in zip(mine, theirs):
            if etree.tostring(a) != etree.tostring(b):
                found.extend(_parted(a, b, here + (tag,)))
    if not found and ((one.text or "").strip() != (two.text or "").strip() or dict(one.attrib) != dict(two.attrib)):
        found.append(here)
    return found


def _where_parted(one: Optional[str], two: Optional[str]) -> List[Tuple[str, ...]]:
    """:func:`_parted` on two prepared copies; the whole asset when either is gone."""
    if not one or not two or one == GONE or two == GONE:
        return [()]
    try:
        return _parted(etree.fromstring(one.encode("utf-8"), _QUIET),
                       etree.fromstring(two.encode("utf-8"), _QUIET))
    except etree.XMLSyntaxError:
        return [()]


def _behind(changes: Sequence[dict], parted: Sequence[Tuple[str, ...]]) -> List[dict]:
    """Which of a mod's changes to one asset are about the fields that differ.

    A change is when its path and a differing field are one inside the other:
    a replace of ``…/Item[…]/Amount`` is about a list of items that parted, and
    an addition to ``…/Costs`` about an ``Item`` that differs. One that makes
    the asset is about all of it. When none is -- the field was reached some
    other way, by a copy or a search -- every change to the asset is named,
    since one of them is still where to look."""
    found = []
    for change in changes:
        walked = change["walks"]
        if change.get("creates") or any(
            walked[:len(spot)] == spot or spot[:len(walked)] == walked for spot in parted
        ):
            found.append(change)
    return found or list(changes)


@dataclass
class Job:
    """Everything a check reads from the loadout, taken on the thread that
    owns it, so the loader can be run on another while editing goes on."""

    origin: str
    name: str
    game: str
    # The mod as Export would write it.
    view: Loadout
    # The mods it stands on, in load order: (name, the folder the game loads
    # it from), then its own, with no folder -- it is written from ``view``.
    stack: List[Tuple[str, Optional[Path]]]
    # The Studio's side: those mods' changes, and nothing else loaded -- and
    # the same without this one, which is what the Studio thinks it is handed.
    studio: Loadout
    underneath: Loadout
    mine: frozenset
    # Which mods count as loaded when a condition asks, as the Studio's
    # reading counted them: every mod read, by ModID and by folder name.
    present: frozenset
    # Assets this mod adds under a GUID something else has too -- another
    # loaded mod, the base game, or this mod a second time: GUID -> what
    # else, by name (see :func:`_shared`).
    shared: Dict[str, List[str]]
    carried: bool
    # The assets the mod's unread ops name, by GUID: what they do to them is
    # not in the Studio's picture, so a difference there is theirs.
    carried_guids: frozenset
    writes: bool
    source: Optional[Path] = None
    # Its ModOps, when the caller has built them already.
    xml: str = ""
    # The mod's own changes by the assets they are about, so a difference can
    # name the ones to look at: GUID -> {"guid", "path", "said", "walks",
    # "creates"}, the first two being what opens the change.
    changes_at: Dict[str, List[dict]] = field(default_factory=dict)

    @property
    def replays(self) -> int:
        """How many mods the check hands the loader: the ones it stands on
        that have a folder to hand over, and this one."""
        return sum(1 for _name, folder in self.stack[:-1] if folder is not None) + 1


# The most mods a check runs through the loader without being asked. A
# loadout's one-change mod stood on 311 mods, through what their folders
# read, and the loader took over two minutes on them at every opening
# (H2-15). Past this, the Export tab and Play say how many it would run, and
# Check again runs it.
AUTOMATIC_LIMIT = 40


def runs_by_itself(job: Job) -> bool:
    """Whether a check of ``job`` starts without being asked."""
    return job.replays <= AUTOMATIC_LIMIT


def too_many(job: Job) -> str:
    """Why a check of ``job`` did not start by itself, as a sentence."""
    return trn("The check would run %(n)s mod through the loader, and only checks of up to "
               "%(limit)s mods start on their own.",
               "The check would run %(n)s mods through the loader, and only checks of up to "
               "%(limit)s mods start on their own.", job.replays, limit=number(AUTOMATIC_LIMIT))


def not_run(job: Job, loader: Optional[xmltest.Loader], why: str) -> Verdict:
    """The verdict of a check of ``job`` that was not run, for ``why``."""
    return Verdict(state=NOT_RUN, origin=job.origin, name=job.name, loader=loader, why=why)


def _changes_at(loadout: Loadout, origin: str) -> Dict[str, List[dict]]:
    found: Dict[str, List[dict]] = {}
    for change in loadout.active():
        if change.origin != origin:
            continue
        if change.creates_asset:
            said = tr("adds the asset")
        elif change.path:
            said = "%s %s" % (change.op, change.path)
        else:
            # Translators: %s is a ModOp type, such as replace or remove.
            said = tr("%s the asset", change.op)
        for guid in _touched(change):
            found.setdefault(guid, []).append({
                "guid": change.guid, "path": change.path, "said": said,
                "walks": _bare(change.path), "creates": change.creates_asset,
            })
    return found


def prepare(loadout: Loadout, origin: str, view: Optional[Loadout] = None,
            folders: Optional[Dict[str, Path]] = None,
            shipped: Optional[Dict[str, Loadout]] = None, *, index) -> Job:
    """What a check of ``origin`` needs, read off ``loadout`` now.

    ``view``: the mod as it is to be written, in place of
    :func:`generator.for_mod`'s, and ``folders`` the folder each of the mods
    it stands on is laid out from, by origin, in place of the one the game
    loads it from -- for a patch exported to share, with the edits to its
    mod it carries (:func:`core.sharing.view`), on top of that mod as its
    author shipped it.  ``shipped`` is that mod read so, by origin: what the
    Studio holds is handed the patch underneath it, where the loader hands
    it their mod as shipped.  The Studio's side with the patch is the
    loadout's: their mod with your edits in it, which is what the two must
    come to. ``index``, the game's: what the mod's searches find in it is
    part of what it stands on (:func:`_stack`)."""
    name = loadout.label_of(origin) or tr("your mod")
    if view is None:
        view = generator.for_mod(loadout, origin)
    folders = folders or {}
    order = _stack(loadout, origin, index)
    stack = [
        ((known.label if (known := loadout.layer(other)) else other),
         folders.get(other) or build.source_folder(loadout, other))
        for other in order[:-1]
    ] + [(name, None)]
    return Job(
        origin=origin,
        name=name,
        game=loadout.game,
        view=view,
        stack=stack,
        studio=_only(loadout, order),
        underneath=_as_shipped(_only(loadout, order[:-1]), shipped or {}),
        mine=frozenset(guid for change in loadout.active() if change.origin == origin
                       for guid in _touched(change)),
        present=frozenset(
            value.lower() for layer in loadout.layers for value in (layer.mod_id, layer.origin) if value
        ),
        shared=_shared(loadout, origin, index),
        carried=any(entry.origin == origin for entry in loadout.unread),
        carried_guids=frozenset(
            [guid for entry in loadout.unread if entry.origin == origin
             for guid in _named_in(entry.source or "", loadout.game)]
            # And the ops inside a group that goes out whole, where it keeps them.
            + [guid for op in loadout.ops if op.origin == origin for guid in op.unread_at]
        ),
        writes=generator.writes_anything(view),
        source=build.source_folder(loadout, origin),
        changes_at=_changes_at(loadout, origin),
    )


def _usable(game: str, index, loader: Optional[xmltest.Loader], target) -> Tuple[str, Optional[Path]]:
    """Why the loader cannot be asked about ``game`` -- "" when it can -- and
    the game's assets.xml to ask it against."""
    if loader is None:
        # Translators: follows "Not checked by the game's loader:".
        return tr("the Anno Modding Tools extension for VS Code is not installed"), None
    if loader.game != game:
        # Translators: follows "Not checked by the game's loader:"; %s is a
        # game, such as Anno 117.
        return tr("the loader found is %s's", games.get(loader.game).label), None
    target = Path(target) if target else Path(getattr(index, "source_path", "") or "")
    if not str(target) or not target.is_file():
        # Translators: follows "Not checked by the game's loader:".
        return tr("the game's assets.xml is not where it was read from"), None
    return "", target


@dataclass
class _Laid:
    """One mod of a stack handed to the loader: its own folder, as the game
    loads it today, or a view of the loadout written as Export would."""

    source: Optional[Path] = None
    view: Optional[Loadout] = None
    xml: str = ""
    carry_from: Optional[Path] = None


def _stood_on(job: Job) -> List[_Laid]:
    return [_Laid(source=folder) for _name, folder in job.stack[:-1] if folder is not None]


def _lay_out(root: Path, stack: Sequence[_Laid]) -> List[Path]:
    """Each mod of ``stack`` in a folder of its own under ``root``, numbered
    in load order -- the numbers the loader's warnings name them by."""
    folders = []
    for position, laid in enumerate(stack):
        folder_name = "%03d" % position
        if laid.view is not None:
            written = generator.write_mod_folder(
                laid.view, root, laid.xml or generator.build_modops_xml(laid.view),
                folder_name=folder_name, carry_from=laid.carry_from, trouble=[],
            )
        else:
            assert laid.source is not None, "a mod laid out is a view or a folder"
            written = root / folder_name
            shutil.copytree(laid.source, written, ignore=_not_patchable)
        folders.append(written)
    return folders


def check(loadout: Loadout, origin: str, index, loader: Optional[xmltest.Loader],
          target: Optional[Path] = None) -> Verdict:
    """Ask the loader what ``origin``'s mod does, as the Studio would export it."""
    return run(prepare(loadout, origin, index=index), index, loader, target)


def run(job: Job, index, loader: Optional[xmltest.Loader], target: Optional[Path] = None,
        should_stop: Optional[Callable[[], bool]] = None) -> Verdict:
    """The check itself: safe off the thread that owns the loadout.

    ``target`` is the game's assets.xml the index was built from; without it,
    or without a loader, the verdict is that there is no check."""
    verdict = Verdict(state=UNAVAILABLE, origin=job.origin, name=job.name, loader=loader)
    verdict.why, target = _usable(job.game, index, loader, target)
    if verdict.why or loader is None or target is None:
        return verdict
    if not job.writes:
        verdict.state = NOTHING
        return verdict

    started = time.monotonic()
    verdict.on_top_of = [name for name, _folder in job.stack[:-1]]
    work = Path(tempfile.mkdtemp(prefix="ams-check-"))
    try:
        root = work / "mods"
        folders = _lay_out(root, _stood_on(job) + [_Laid(view=job.view, xml=job.xml, carry_from=job.source)])
        if should_stop is not None and should_stop():
            # Translators: follows "The game's loader could not check My Mod:".
            verdict.state, verdict.why = FAILED, tr("stopped")
            return verdict
        applied = xmltest.apply_stack(loader.exe, target, folders, root, job.game, only_last=True,
                                      present=job.present, should_stop=should_stop)
        verdict.fallback = applied.said_fallback(loader.exe)
        if applied.failures:
            verdict.state, verdict.why = FAILED, "; ".join(applied.failures[:3])
            return verdict
        # What the loader did in the checked mod's own run, and what it warned.
        last = folders[-1].relative_to(root).as_posix()
        loader_changed = set(applied.changed_by.get(last, ()))
        verdict.warnings = _own_warnings(applied.warnings_by.get(last, ()), last)

        compared = set(job.mine) | loader_changed
        # Two assets under one GUID are not something either side can be
        # compared on; said, apart.
        verdict.shared = dict(job.shared)
        compared -= set(job.shared)
        if job.carried:
            # Changed by the loader and shown by nobody: an op the Studio
            # carries unread did it. Said, not counted against the reading.
            unshown = loader_changed - job.mine
            verdict.not_shown = len(unshown)
            compared -= unshown
        _judge(verdict, job, index, applied, compared, loader_changed)
        return verdict
    except (OSError, xmltest.LoaderError) as exc:
        verdict.state, verdict.why = FAILED, str(exc)
        return verdict
    finally:
        shutil.rmtree(work, ignore_errors=True)
        verdict.seconds = round(time.monotonic() - started, 1)


def _judge(verdict: Verdict, job: Job, index, applied, compared, loader_changed) -> None:
    """Compare what the mod does, asset by asset, on both sides.

    The question is about this mod, so each side's answer is what the mod
    does to what it was handed. The loader's run says what it was handed only
    for the assets it changed (``applied.before``); an asset it left alone is
    one the loader says this mod does nothing to, so the Studio's answer for
    it is held against the Studio's own picture of the mods underneath.

    And where the loader changed an asset but was handed something other than
    what the Studio thinks the mods underneath leave, the two cannot agree
    whatever this mod does: that is the Studio's reading of *those* mods,
    counted apart, not this one's. *Pools and Definitions* was said to differ
    on an asset it only puts new ones beside, holding a value another mod
    under it had set.
    """
    from .workspace import Workspace

    studio = Workspace(index, job.studio)
    underneath = Workspace(index, job.underneath)
    inert = recording.Inert(index)
    label = getattr(index, "display_name", None) or (lambda guid: "GUID " + guid)
    checked = differing = beneath = 0
    examples: List[Dict[str, Any]] = []
    with studio.steady(), underneath.steady():
        for guid in sorted(compared, key=lambda g: (len(g), g)):
            handed = underneath.effective_asset(guid).as_the_game_reads_it()
            shown = studio.effective_asset(guid).as_the_game_reads_it()
            if guid in loader_changed:
                done = applied.assets.get(guid)
                if guid in applied.before and prepared(applied.before[guid], inert)[1] != prepared(handed, inert)[1]:
                    # Counted however the two sides end up: where they ended
                    # alike, it was counted nowhere and the line never said it.
                    beneath += 1
                    continue
            else:
                done = handed
            checked += 1
            one, two = prepared(shown, inert)[1], prepared(done, inert)[1]
            if one == two:
                continue
            if guid in job.carried_guids:
                # An op the Studio carries unread is aimed here too: the
                # loader ran it and the Studio shows nothing for it, by design.
                # *Trade Union Rebalance* gives six guild house items the GUID
                # 1, which the Studio will not follow.
                checked -= 1
                verdict.not_shown += 1
                continue
            differing += 1
            if len(examples) < EXAMPLES:
                diff = difflib.unified_diff(
                    (one or "").splitlines(), (two or "").splitlines(),
                    # Translators: the two sides of a diff: what the Studio
                    # shows, and what the game's loader made of the mod.
                    tr("the Studio"), tr("the game's loader"), n=1, lineterm="",
                )
                parted = _where_parted(one, two)
                examples.append({
                    "guid": guid, "label": label(guid), "diff": list(diff)[:DIFF_LINES],
                    # Where they part, and the mod's changes there: what to
                    # open, rather than a diff to read and a change to find.
                    "fields": ["/".join(where) or tr("the whole asset") for where in parted][:EXAMPLES],
                    "changes": [
                        {"guid": one_change["guid"], "path": one_change["path"], "said": one_change["said"]}
                        for one_change in _behind(job.changes_at.get(guid, []), parted)
                    ],
                })
    verdict.checked = checked
    verdict.differing_count = differing
    verdict.differing = examples
    verdict.beneath = beneath
    verdict.state = AGREES if not differing else DIFFERS


_NUMBERED = re.compile(r"^(\d{3}): (.*)$")
# How the loader says an op found nothing where it was aimed.
_UNMATCHED = "No matching node"


def _sorted_out(lines) -> Tuple[int, int]:
    """Of what the loader wrote about a mod, how many lines are an op that
    matches nothing, and how many are something else: an include it could
    not find, a file it could not parse.  Counted together, a missing include
    and a critical line were "2 ops match nothing"."""
    unmatched = sum(1 for line in lines if _UNMATCHED in line)
    return unmatched, len(lines) - unmatched


def _other_lines(count: int) -> str:
    # Translators: after "sees 1 of the 3 assets My Mod changes differently;"
    # -- lines the loader wrote that are not about an op matching nothing.
    return trn("%(n)s other warning or error from the loader",
               "%(n)s other warnings or errors from the loader", count)


def _own_warnings(lines, own: str) -> List[str]:
    """What a run warned about the checked mod itself.

    The run warns about the mods stood on too -- on a real folder, an include
    one of them could not find, three times over -- and names each by its
    folder, which here is its number: ``010: Include file missing``.  The
    checked mod's own come with its number, or with none when nothing was
    stood on.  A bare ``: Failed to open`` only repeats the include error
    beside it, so it goes with that.
    """
    found = []
    for line in lines:
        level, _, message = line.partition(": ")
        numbered = _NUMBERED.match(message)
        if numbered:
            if numbered.group(1) != own:
                continue
            message = numbered.group(2)
        elif message.startswith(":"):
            continue
        found.append("%s: %s" % (level, message))
    return found


def _not_patchable(folder, names):
    """What a copied mod leaves behind: everything the loader will not read
    as a patch -- meshes, textures, sounds, the bulk of a mod."""
    keep = xmltest.COPIED
    return [name for name in names
            if not (Path(folder) / name).is_dir() and not name.lower().endswith(keep)]


# ---------------------------------------------------------------- comparing two runs
def _in(applied, guid: str, index):
    """An asset as a run left it: its own copy when the run changed it, else
    the base game's."""
    return applied.assets[guid] if guid in applied.assets else index.asset_element(guid)


def _parts(one, two, index, inert) -> List[Tuple[str, Optional[str], Optional[str]]]:
    """Every asset two runs of the loader leave differently:
    ``(guid, as the first left it, as the second did)``, prepared for reading.

    Most assets two runs of one stack change come out byte for byte the same,
    so those are passed over before anything is parsed."""
    found = []
    for guid in sorted(set(one.assets) | set(two.assets), key=lambda g: (len(g), g)):
        a, b = one.assets.get(guid, False), two.assets.get(guid, False)
        if a is not False and b is not False and (a is None) == (b is None) and (
                a is None or etree.tostring(a) == etree.tostring(b)):
            continue
        first = prepared(_in(one, guid, index), inert)[1]
        second = prepared(_in(two, guid, index), inert)[1]
        if first != second:
            found.append((guid, first, second))
    return found


def _diff(one: Optional[str], two: Optional[str], labels: Tuple[str, str]) -> List[str]:
    return list(difflib.unified_diff((one or "").splitlines(), (two or "").splitlines(),
                                     labels[0], labels[1], n=1, lineterm=""))[:DIFF_LINES]


# ---------------------------------------------------------------- unread ops


@dataclass
class UnreadEffect:
    """What one op the Studio carries unread does in game, as the loader
    does it: the assets that come out differently with it than without it."""

    order: int
    source: str
    count: int = 0
    # {"guid", "label", "diff"}: the diff runs from without the op to with it.
    assets: List[dict] = field(default_factory=list)
    # What the loader warned that names where the op is aimed.
    warnings: List[str] = field(default_factory=list)
    # Why the loader could not be asked; "" when it was.
    why: str = ""
    # As Verdict.fallback.
    fallback: str = ""

    def describe(self) -> str:
        if self.why:
            return tr("Not asked: %s", self.why)
        if not self.count:
            if self.warnings:
                return tr("Does nothing in game: the loader finds nothing where it is aimed")
            return tr("Does nothing in game, on top of what is loaded")
        names = [found["label"] for found in self.assets[:3]]
        if self.count > len(names):
            return trn("Changes %(total)s assets in game: %(names)s and %(n)s more",
                       "Changes %(total)s assets in game: %(names)s and %(n)s more",
                       self.count - len(names), total=number(self.count), names=", ".join(names))
        return trn("Changes %(n)s asset in game: %(names)s", "Changes %(n)s assets in game: %(names)s",
                   self.count, names=join_list(names))

    def details(self) -> List[str]:
        lines = [self.describe() + "."]
        if self.fallback:
            lines.append(self.fallback + ".")
        for line in self.warnings:
            lines.append(tr("The loader: %s", line))
        for found in self.assets:
            lines.append("")
            lines.append(tr("%(asset)s (GUID %(guid)s):", asset=found["label"], guid=found["guid"]))
            lines.extend("  " + line for line in found["diff"])
        return lines


_AN_OP = re.compile(r"<\s*(?:ModOp|Group|Include|Asset)\b")


def is_an_op(entry) -> bool:
    """Whether a carried entry is something the loader runs: a tweak tool's
    markup rides along with the ops but does nothing in the loader, and
    asking it about 572 of them in one mod would be minutes for nothing."""
    return bool(_AN_OP.search(entry.source or ""))


def explain_unread(job: Job, index, loader: Optional[xmltest.Loader], target: Optional[Path] = None,
                   progress: Optional[Callable[[int, int], None]] = None,
                   should_stop: Optional[Callable[[], bool]] = None) -> List[UnreadEffect]:
    """What each op ``job``'s mod carries unread does, asked of the loader.

    The Studio could not read these, so it shows nothing for them; the
    loader runs them. The mod goes through the loader as Export would write
    it, on the mods it builds on, and then once more for each unread op with
    that op left out: what differs between the two is what the op does. Its
    effect in the game, then, not a reading of it -- an op later ops rely on
    is credited with what they could not do without it. A second or two a
    run; one more run than there are ops.
    """
    entries = [entry for entry in job.view.unread if is_an_op(entry)]
    why, target = _usable(job.game, index, loader, target)
    if why or not entries or loader is None or target is None:
        return [UnreadEffect(entry.order, entry.source, why=why) for entry in entries]
    inert = recording.Inert(index)
    label = getattr(index, "display_name", None) or (lambda guid: "GUID " + guid)
    work = Path(tempfile.mkdtemp(prefix="ams-unread-"))
    effects = []
    try:
        root = work / "mods"
        folders = _lay_out(root, _stood_on(job) + [_Laid(view=job.view, xml=job.xml, carry_from=job.source)])
        patch = folders[-1] / games.get(job.game).asset_relpath
        own = folders[-1].relative_to(root).as_posix()

        def whole():
            return xmltest.apply_whole(loader.exe, target, folders, root, job.game,
                                       timeout=xmltest.TIMEOUT, present=job.present, should_stop=should_stop)

        if progress is not None:
            progress(0, len(entries) + 1)
        with_all = whole()
        if with_all.failures:
            said = "; ".join(with_all.failures[:2])
            return [UnreadEffect(entry.order, entry.source, why=said) for entry in entries]
        warned = _own_warnings(with_all.warnings_by.get(own, ()), own)
        for number, entry in enumerate(entries, 1):
            if should_stop is not None and should_stop():
                effects.append(UnreadEffect(entry.order, entry.source, why=tr("stopped")))
                continue
            without = copy.copy(job.view)
            without.unread = [other for other in job.view.unread if other is not entry]
            patch.write_text(generator.build_modops_xml(without), encoding="utf-8")
            left_out = whole()
            if progress is not None:
                progress(number, len(entries) + 1)
            if left_out.failures:
                effects.append(UnreadEffect(entry.order, entry.source, why="; ".join(left_out.failures[:2])))
                continue
            parted = _parts(left_out, with_all, index, inert)
            aimed = [path for attrs in _ops_in(entry.source or "")
                     if (path := modop.type_and_path(attrs, job.game)[1])]
            effects.append(UnreadEffect(
                entry.order, entry.source, count=len(parted),
                fallback=with_all.said_fallback(loader.exe) or left_out.said_fallback(loader.exe),
                assets=[{"guid": guid, "label": label(guid),
                         # Translators: the two sides of a diff: the game's
                         # loader without an op, and with it.
                         "diff": _diff(before, after, (tr("without it"), tr("with it")))}
                        for guid, before, after in parted[:EXAMPLES]],
                warnings=[line for line in warned if any(path in line for path in aimed)],
            ))
        return effects
    except (OSError, xmltest.LoaderError) as exc:
        return effects + [UnreadEffect(entry.order, entry.source, why=str(exc))
                          for entry in entries[len(effects):]]
    finally:
        shutil.rmtree(work, ignore_errors=True)


# ---------------------------------------------------------------- the whole loadout
@dataclass
class LoadoutJob:
    """What a check of every loaded mod at once needs, read off the loadout
    on the thread that owns it."""

    game: str
    # In load order: (name, how it is handed to the loader).
    stack: List[Tuple[str, _Laid]]
    # Loaded mods with no folder to hand over, and ones whose folder holds a
    # newer version than the one read: said, and left out of both sides.
    missing: List[str]
    updated: List[str]
    studio: Loadout
    # Every asset the loading mods change or create, by GUID.
    touched: frozenset
    present: frozenset
    # Created by two of them, twice by one, or by one and the base game:
    # not comparable.
    shared: frozenset
    # Named by an op the Studio carries unread: its difference is that op's.
    carried: frozenset
    # Every loading mod's changes by the assets they are about -- as
    # Job.changes_at, with the mod's name under "mod".
    changes_at: Dict[str, List[dict]]


@dataclass
class LoadoutVerdict(_Said):
    """What the loader made of the whole loadout, against the Studio."""

    state: str
    loader: Optional[xmltest.Loader] = None
    mods: int = 0
    missing: List[str] = field(default_factory=list)
    updated: List[str] = field(default_factory=list)
    checked: int = 0
    differing_count: int = 0
    # {"guid", "label", "diff", "fields", "mods", "changes"}: the changes
    # each carry "mod" beside "guid" and "path".
    differing: List[dict] = field(default_factory=list)
    not_shown: int = 0
    shared: int = 0
    # Each mod's ops the loader warned about, by the mod's name.
    warnings: Dict[str, List[str]] = field(default_factory=dict)
    why: str = ""
    seconds: float = 0.0
    # As Verdict.fallback.
    fallback: str = ""

    def _failed(self) -> str:
        return tr("The game's loader could not check the loadout: %s.", self.why)

    def _nothing(self) -> str:
        return tr("No mod loads for the game's loader to check.")

    def _none_left(self, loader: str) -> str:
        return tr("Checked by the game's loader (%(loader)s), all %(mods)s mods at once: no asset "
                  "they change was left to compare", loader=loader, mods=number(self.mods))

    def _agrees(self, loader: str) -> str:
        return trn("Checked by the game's loader (%(loader)s), all %(mods)s mods at once: agrees on "
                   "the one asset they change",
                   "Checked by the game's loader (%(loader)s), all %(mods)s mods at once: agrees on "
                   "all %(n)s assets they change", self.checked, loader=loader, mods=number(self.mods))

    def _differs(self, loader: str) -> str:
        return trn("Checked by the game's loader (%(loader)s), all %(mods)s mods at once: sees the one "
                   "asset they change differently",
                   "Checked by the game's loader (%(loader)s), all %(mods)s mods at once: sees "
                   "%(differing)s of the %(n)s assets they change differently", self.checked,
                   loader=loader, mods=number(self.mods), differing=number(self.differing_count))

    def _extras(self) -> List[str]:
        extras = []
        if self.shared:
            # Translators: assets, after "sees 3 of the 10 assets they change differently;".
            extras.append(trn("%(n)s under a GUID taken twice, not compared",
                              "%(n)s under a GUID taken twice, not compared", self.shared))
        sorted_out = [_sorted_out(lines) for lines in self.warnings.values()]
        warned = sum(unmatched for unmatched, _other in sorted_out)
        if warned:
            extras.append(trn("%(n)s op matches nothing, in %(mods)s", "%(n)s ops match nothing, in %(mods)s",
                              warned, mods=trn("%(n)s mod", "%(n)s mods",
                                               sum(1 for unmatched, _other in sorted_out if unmatched))))
        if any(other for _unmatched, other in sorted_out):
            extras.append(_other_lines(sum(other for _unmatched, other in sorted_out)))
        if self.updated:
            extras.append(trn("%(n)s left out, updated on disk since it was read",
                              "%(n)s left out, updated on disk since they were read", len(self.updated)))
        if self.missing:
            extras.append(trn("%(n)s left out, not where it was read from",
                              "%(n)s left out, not where they were read from", len(self.missing)))
        if self.fallback:
            # The whole of it: there are no details behind this line, as
            # there are behind a mod's.
            extras.append(self.fallback)
        return extras

    def _ended(self, said: str) -> str:
        return tr("%(said)s (%(took)s).", said=said, took=_took(self.seconds))


def _took(seconds: float) -> str:
    if seconds < 90:
        # Translators: a time taken, in seconds.
        return tr("%s s", number(round(seconds)))
    minutes, rest = divmod(round(seconds), 60)
    # Translators: a time taken, in minutes and seconds.
    return tr("%(minutes)s min %(seconds)s s", minutes=number(minutes), seconds="%02d" % rest)


# How many differences a whole-loadout check keeps to show; the rest are counted.
LOADOUT_SHOWN = 500


def prepare_loadout(loadout: Loadout, mine: Sequence[str], index=None) -> LoadoutJob:
    """What a check of the whole loadout needs, read off ``loadout`` now.

    ``mine`` are the mods Play would build -- yours and your patches: handed
    over as Export would write them, since their folders (if they have any)
    are not what you have made of them. Every other loading mod goes as its
    own folder, as the game loads it today."""
    gone = loadout.not_loading()
    order = [origin for origin in loadout.origins(loaded=True) if origin not in gone]
    if "" in mine and "" not in order:
        order.append("")
    stack, missing, updated = [], [], []

    def name_of(origin: str) -> str:
        return loadout.label_of(origin) or tr("your mod")

    handed = []
    for origin in order:
        name = name_of(origin)
        if origin in mine:
            view = generator.for_mod(loadout, origin)
            if generator.writes_anything(view):
                stack.append((name, _Laid(view=view, xml=generator.build_modops_xml(view),
                                          carry_from=build.source_folder(loadout, origin))))
            handed.append(origin)
            continue
        folder = build.source_folder(loadout, origin)
        if folder is None:
            # Not where it was read from -- renamed by an update, or moved.
            # Left out of both sides, so the rest is compared like for like.
            missing.append(name)
            continue
        if builds.updated_on_disk(loadout, loadout.layer(origin), folder):
            # The loader would run the new version against the Studio's
            # reading of the old one: *Item Sockets*, read at 1.6.2 and
            # 1.7.1 on disk, read as 230 disagreements. Left out the same
            # way, until the reading opening the loadout asks for is in.
            updated.append(name)
            continue
        stack.append((name, _Laid(source=folder)))
        handed.append(origin)
    order = handed
    changes_at: Dict[str, List[dict]] = {}
    touched = set()
    for origin in order:
        for guid, found in _changes_at(loadout, origin).items():
            touched.add(guid)
            changes_at.setdefault(guid, []).extend(dict(one, mod=name_of(origin)) for one in found)
    carried = {guid for entry in loadout.unread if entry.origin not in gone
               for guid in _named_in(entry.source or "", loadout.game)}
    carried |= {guid for op in loadout.ops if op.origin not in gone for guid in op.unread_at}
    shared = {repeat.guid for repeat in _taken_twice(loadout, index)}
    return LoadoutJob(
        game=loadout.game,
        stack=stack,
        missing=missing,
        updated=updated,
        studio=_only(loadout, order),
        touched=frozenset(touched),
        present=frozenset(value.lower() for layer in loadout.layers
                          for value in (layer.mod_id, layer.origin) if value),
        shared=frozenset(shared),
        carried=frozenset(carried),
        changes_at=changes_at,
    )


def check_loadout(job: LoadoutJob, index, loader: Optional[xmltest.Loader], target: Optional[Path] = None,
                  progress: Optional[Callable[[str], None]] = None,
                  should_stop: Optional[Callable[[], bool]] = None) -> LoadoutVerdict:
    """Every loading mod through the loader in one run, and every asset they
    change compared with what the Studio shows -- the census's own question,
    asked of the loadout in front of you.

    The single-mod check asks about one mod on the mods it builds on; this
    asks about all of them together, so a mod whose reading goes wrong only
    beside another is caught too. It is slow: the loader patches the whole
    file once, which is a minute or so for a large Anno 1800 loadout, and
    then each of the thousands of assets is built on the Studio's side.
    """
    started = time.monotonic()
    verdict = LoadoutVerdict(state=UNAVAILABLE, loader=loader, mods=len(job.stack), missing=list(job.missing),
                             updated=list(job.updated))
    verdict.why, target = _usable(job.game, index, loader, target)
    if verdict.why or loader is None or target is None:
        return verdict
    if not job.stack:
        verdict.state = NOTHING
        return verdict

    def say(text: str) -> None:
        if progress is not None:
            progress(text)

    work = Path(tempfile.mkdtemp(prefix="ams-loadout-"))
    try:
        say(trn("Handing %(n)s mod to the game's loader…", "Handing %(n)s mods to the game's loader…",
                len(job.stack)))
        root = work / "mods"
        folders = _lay_out(root, [laid for _name, laid in job.stack])
        if should_stop is not None and should_stop():
            verdict.state, verdict.why = FAILED, tr("stopped")
            return verdict
        say(trn("The game's loader is patching the game's assets with %(n)s mod…",
                "The game's loader is patching the game's assets with all %(n)s mods…", len(job.stack)))
        applied = xmltest.apply_whole(loader.exe, target, folders, root, job.game,
                                      timeout=3600, present=job.present, should_stop=should_stop)
        verdict.fallback = applied.said_fallback(loader.exe)
        if applied.failures:
            verdict.state, verdict.why = FAILED, "; ".join(applied.failures[:3])
            return verdict
        lines = [line for said in applied.warnings_by.values() for line in said]
        # The loader is handed only the folders with a patch of the assets.
        relpath = games.get(job.game).asset_relpath
        patched = [position for position, folder in enumerate(folders) if (folder / relpath).is_file()]
        for position, (name, _laid) in enumerate(job.stack):
            # With one mod handed over there is no number on its lines; with
            # several, a line with none is about no mod in particular.
            if len(patched) > 1:
                own = _numbered(lines, "%03d" % position)
            else:
                own = _own_warnings(lines, "%03d" % position) if position in patched else []
            if own:
                verdict.warnings[name] = own
        _judge_loadout(verdict, job, index, applied, say, should_stop)
        return verdict
    except (OSError, xmltest.LoaderError) as exc:
        verdict.state, verdict.why = FAILED, str(exc)
        return verdict
    finally:
        shutil.rmtree(work, ignore_errors=True)
        verdict.seconds = round(time.monotonic() - started, 1)


def _numbered(lines, number: str) -> List[str]:
    """The lines about the mod in folder ``number``, as :func:`_own_warnings` words them."""
    found = []
    for line in lines:
        level, _, message = line.partition(": ")
        numbered = _NUMBERED.match(message)
        if numbered and numbered.group(1) == number:
            found.append("%s: %s" % (level, numbered.group(2)))
    return found


def _judge_loadout(verdict: LoadoutVerdict, job: LoadoutJob, index, applied, say, should_stop) -> None:
    from .workspace import Workspace

    studio = Workspace(index, job.studio)
    inert = recording.Inert(index)
    label = getattr(index, "display_name", None) or (lambda guid: "GUID " + guid)
    compared = (set(applied.assets) | set(job.touched)) - set(job.shared)
    verdict.shared = len((set(applied.assets) | set(job.touched)) & set(job.shared))
    total = len(compared)
    with studio.steady():
        for number, guid in enumerate(sorted(compared, key=lambda g: (len(g), g))):
            if not number % 250:
                say(tr("Comparing what the Studio shows with what the loader made: %(done)s of "
                       "%(total)s assets…", done=number_text(number), total=number_text(total)))
                if should_stop is not None and should_stop():
                    verdict.state, verdict.why = FAILED, tr("stopped")
                    return
            shown = prepared(studio.effective_asset(guid).as_the_game_reads_it(), inert)[1]
            done = prepared(_in(applied, guid, index), inert)[1]
            verdict.checked += 1
            if shown == done:
                continue
            if guid in job.carried or (guid not in job.touched and job.carried):
                # An op the Studio carries unread did it, or is aimed here:
                # the Studio shows nothing for those by design.
                verdict.checked -= 1
                verdict.not_shown += 1
                continue
            verdict.differing_count += 1
            if len(verdict.differing) >= LOADOUT_SHOWN:
                continue
            parted = _where_parted(shown, done)
            here = job.changes_at.get(guid, [])
            verdict.differing.append({
                "guid": guid, "label": label(guid),
                "diff": _diff(shown, done, (tr("the Studio"), tr("the game's loader"))),
                "fields": ["/".join(where) or tr("the whole asset") for where in parted][:EXAMPLES],
                "mods": list(dict.fromkeys(one["mod"] for one in here)),
                "changes": [{"guid": one["guid"], "path": one["path"], "said": one["said"], "mod": one["mod"]}
                            for one in _behind(here, parted)],
            })
    verdict.state = AGREES if not verdict.differing_count else DIFFERS


# ---------------------------------------------------------------- the probes
@dataclass
class Probe:
    """One reading rule, pinned: a small file, a patch, the game."""

    name: str
    game: str
    patch: str
    # Extra assets the file holds besides the probe's own two.
    assets: str = ""
    # Whether the loader should change anything: two sides agreeing that
    # nothing happened is only worth something when nothing should.
    changes: bool = True


@dataclass
class ProbeResult:
    probe: Probe
    agrees: Optional[bool]
    said: str = ""


def _file(game: str, extra: str = "") -> str:
    """A small assets.xml, in the shape each game's loader reads."""
    # Two assets that differ everywhere a probe copies from one to the other,
    # so that a copy is a change and not two sides agreeing on nothing.
    asset = (
        "<Asset><Template>Building</Template><Values><Standard><GUID>%s</GUID><Name>%s</Name></Standard>"
        "<Building><Radius>%s</Radius><AssociatedRegions>Moderate</AssociatedRegions></Building>"
        "<Cost><Costs><Item><Ingredient>1010</Ingredient><Amount>%s</Amount></Item>"
        "<Item><Ingredient>1011</Ingredient><Amount>8</Amount></Item>"
        "<Item><Ingredient>1012</Ingredient><Amount>2</Amount></Item></Costs></Cost>"
        "<List/></Values></Asset>"
    )
    body = asset % ("1000", "alpha", "10", "5") + asset % ("1001", "beta", "4", "3") + extra
    return "<AssetList><Groups><Group><Assets>%s</Assets></Group></Groups></AssetList>" % body


# The constructs whose reading was measured against the loader and would be
# most costly to lose track of. Each is compared asset for asset, so a probe
# needs no expected answer of its own: it holds while the Studio and the
# loader agree.
PROBES = (
    # Translators: this and the names below are reading rules of ModOps,
    # listed after "The game's loader reads 2 of the Studio's reading rules
    # differently:".
    Probe(tr_noop("a narrow replace", context=STORED), games.ANNO_1800,
          '<ModOp Type="replace" GUID="1000" Path="/Values/Building/Radius"><Radius>12</Radius></ModOp>'),
    Probe(tr_noop("a merge", context=STORED), games.ANNO_117,
          '<ModOp Merge="@1000/Building"><Radius>13</Radius></ModOp>'),
    Probe(tr_noop("a removal from a list", context=STORED), games.ANNO_117,
          '<ModOp Remove="@1000/Cost/Costs/Item[Ingredient=\'1011\']"/>'),
    Probe(tr_noop("MaxRepeat: again while the condition holds", context=STORED), games.ANNO_117,
          '<Group Condition="@1000/Cost/Costs/Item" MaxRepeat="10">'
          '<ModOp Remove="@1000/Cost/Costs/Item[1]"/></Group>'),
    Probe(tr_noop("MaxRepeat: stops at the cap", context=STORED), games.ANNO_117,
          '<Group Condition="@1000/Cost/Costs/Item" MaxRepeat="2">'
          '<ModOp Remove="@1000/Cost/Costs/Item[1]"/></Group>'),
    Probe(tr_noop("MaxRepeat: Anno 1800 runs it once", context=STORED), games.ANNO_1800,
          '<Group Condition="//Values[Standard/GUID=\'1000\']/Cost/Costs/Item" MaxRepeat="10">'
          '<ModOp Type="remove" GUID="1000" Path="/Values/Cost/Costs/Item[1]"/></Group>'),
    Probe(tr_noop("a search condition that holds", context=STORED), games.ANNO_1800,
          '<ModOp Type="replace" GUID="1000" Path="/Values/Building/Radius" '
          'Condition="//Values[Standard/GUID=\'1001\']"><Radius>14</Radius></ModOp>'),
    Probe(tr_noop("a search condition that does not", context=STORED), games.ANNO_1800,
          '<ModOp Type="replace" GUID="1000" Path="/Values/Building/Radius" '
          'Condition="//Values[Standard/GUID=\'4242\']"><Radius>15</Radius></ModOp>', changes=False),
    Probe(tr_noop("a group any Skip switches off", context=STORED), games.ANNO_1800,
          '<Group Skip="0"><ModOp Type="replace" GUID="1000" Path="/Values/Building/Radius">'
          '<Radius>16</Radius></ModOp></Group>', changes=False),
    Probe(tr_noop("an op any Skip switches off", context=STORED), games.ANNO_117,
          '<ModOp Merge="@1000/Building" Skip="0"><Radius>16</Radius></ModOp>', changes=False),
    Probe(tr_noop("Anno 1800 has no short form", context=STORED), games.ANNO_1800,
          '<ModOp Merge="@1000/Building"><Radius>18</Radius></ModOp>', changes=False),
    Probe(tr_noop("a type neither loader lists", context=STORED), games.ANNO_117,
          '<ModOp Type="append" GUID="1000" Path="/Values/Building/Radius"><Radius>18</Radius></ModOp>',
          changes=False),
    Probe(tr_noop("addAfter: Anno 117's name for addNextSibling", context=STORED), games.ANNO_117,
          '<ModOp Type="addAfter" GUID="1000" Path="/Values/Building/Radius"><Extra>1</Extra></ModOp>'),
    Probe(tr_noop("a name on a group changes nothing", context=STORED), games.ANNO_1800,
          '<Group ModOpID="probe"><ModOp Type="replace" GUID="1001" Path="/Values/Building/Radius">'
          '<Radius>17</Radius></ModOp></Group>'),
    Probe(tr_noop("arithmetic in a ModValue", context=STORED), games.ANNO_117,
          '<ModOp Merge="@1000/Building"><Radius><ModValue Insert="self::node() div 4 + 1"/></Radius></ModOp>'),
    Probe(tr_noop("flags merged in", context=STORED), games.ANNO_117,
          '<ModOp Merge="@1000/Building"><AssociatedRegions><ModValue Merge="Colony01"/>'
          '</AssociatedRegions></ModOp>'),
    Probe(tr_noop("a copy from another asset", context=STORED), games.ANNO_117,
          '<ModOp Merge="@1000/Building"><Radius><ModValue Insert="@1001/Building/Radius/text()"/>'
          '</Radius></ModOp>'),
    Probe(tr_noop("Content= copying a field", context=STORED), games.ANNO_1800,
          '<ModOp Type="replace" GUID="1001" Path="/Values/Cost" Content="@1000/Cost"/>'),
    Probe(tr_noop("an asset added beside another", context=STORED), games.ANNO_1800,
          '<ModOp Type="addNextSibling" GUID="1000"><Asset><Template>Building</Template><Values>'
          '<Standard><GUID>5000</GUID><Name>added</Name></Standard></Values></Asset></ModOp>'),
    Probe(tr_noop("an asset straight under ModOps", context=STORED), games.ANNO_117,
          '<ModOp Add="/AssetList/Groups[last()]"><Group><Assets/></Group></ModOp>'
          '<Asset><Template>Building</Template><Values><Standard><GUID>5001</GUID>'
          '<Name>bare</Name></Standard></Values></Asset>'),
)


def probe(loader: xmltest.Loader, probes: Sequence[Probe] = PROBES) -> List[ProbeResult]:
    """Run each of ``probes`` for the loader's game through the Studio and the
    loader, against a small file of its own, and say which agree."""
    from . import index as index_mod
    from .workspace import Workspace

    results = []
    work = Path(tempfile.mkdtemp(prefix="ams-probe-"))
    try:
        game = games.get(loader.game)
        for position, one in enumerate(probe for probe in probes if probe.game == loader.game):
            here = work / str(position)
            target = here / "assets.xml"
            target.parent.mkdir(parents=True)
            target.write_text(_file(one.game, one.assets), encoding="utf-8")
            db = here / "index.sqlite"
            index_mod.build_index(target, db)
            index = index_mod.AssetIndex(db, target)
            mods = here / "mods"
            patch = mods / "probe" / game.asset_relpath
            patch.parent.mkdir(parents=True)
            patch.write_text("<ModOps>%s</ModOps>" % one.patch, encoding="utf-8")
            (mods / "probe" / "modinfo.json").write_text('{"ModID": "probe"}', encoding="utf-8")
            try:
                _session, loadout = read_folders([mods / "probe"], [mods], index, one.game)
                applied = xmltest.apply_stack(loader.exe, target, [mods / "probe"], mods, one.game)
                if applied.failures:
                    results.append(ProbeResult(one, None, "; ".join(applied.failures[:2])))
                    continue
                if one.changes and not applied.assets:
                    results.append(ProbeResult(one, None, "the loader changed nothing, so the probe did not run"))
                    continue
                studio = Workspace(index, loadout)
                guids = set(applied.assets) | {change.guid for change in loadout.active()}
                with studio.steady():
                    found = compare_views(
                        guids,
                        lambda guid: studio.effective_asset(guid).as_the_game_reads_it(),
                        lambda guid: applied.assets[guid] if guid in applied.assets else index.asset_element(guid),
                        recording.Inert(index),
                        ("the Studio", "the game's loader"),
                    )
                said = ""
                if found["examples"]:
                    said = "\n".join(found["examples"][0]["diff"])
                results.append(ProbeResult(one, not found["assets"], said))
            finally:
                close = getattr(index, "close", None)
                if close is not None:
                    close()
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return results
