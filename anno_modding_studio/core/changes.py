"""The edit model: changes, the ops and mods they belong to, and the loadout.

A change is one future ModOp -- except where a :class:`Op` stands for many:
an op read in from a mod is kept as it was written, with the changes it came
to hanging off it, and goes back out as that one op.

A loadout is the unit of work: mod metadata, the current change set, and a list
of checkpoints recording what the change set looked like each time the author
exported or explicitly saved.  Checkpoints hold the whole change set rather than
a delta -- a change is six short strings, so even a hundred checkpoints of a large
mod stays a small file, and reconstruction can never go wrong.

A loadout holds more than one mod.  Every change names the mod it belongs to in
``Change.origin`` -- empty for the one being authored here -- and every mod read
in from elsewhere gets a :class:`ModLayer` recording what it is and where it
came from.  That is what lets the whole of a mods folder be opened at once: the
effective asset is then what the game will really load, not just what this one
mod does to it, and each mod can still be written back out on its own.  A
loadout holding a mods folder is a loadout, and its :class:`Profile` objects
are named sets of switches over the same mods.
"""

from __future__ import annotations

import copy
import json
import os
import re
import time
import uuid
from dataclasses import asdict, dataclass, field, fields, replace
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Optional, Sequence, Set, Tuple
from xml.etree import ElementTree
from xml.sax.saxutils import unescape

from .. import __version__
from . import disk, modfolder, modop, owners, records, saving, settings
from . import projects as project_files
from .i18n import tr, tr_stored, trn

# The ModOp kinds the Studio can generate: what each is, is :mod:`.modop`'s
# to say.
from .modop import (
    OP_ADD,
    OP_ADD_NEXT,
    OP_ADD_PREV,
    OP_MERGE,
    OP_REMOVE,
    OP_REPLACE,
)

STRUCTURAL_OPS = (OP_ADD, OP_ADD_NEXT, OP_ADD_PREV, OP_REMOVE, OP_MERGE)

# ``…/Item[GoodsOrPool='174519']/GoodsOrPool``: a field named by the predicate
# that finds its own list entry. See :attr:`Change.replaces_its_own_key`.
_KEYED_FIELD = re.compile(r"\[\s*([\w.-]+)\s*=\s*(['\"])(.*?)\2\s*\]/([\w.-]+)$")
# A payload that is one field holding a value and nothing else.
_ONE_VALUE = re.compile(r"\s*<([\w.-]+)\s*(?:/>|>([^<]*)</\1\s*>)\s*$")

# 4: the mods of yours are kept in projects of their own, which the loadout
# names under "projects" (core/projects.py); a build that reads 3 would open
# such a loadout without them.
# 5: two histories (core/histories.py): "history" is the loadout's list at
# each checkpoint, and each mod of yours' work is under "mod_history" or in
# its project. A build that reads 4 would take the list's checkpoints for
# the mods' work, and restoring one would wipe it.
# 6: the loadout's own mod is one record, "own", shaped as a layer is, in
# place of its details at the top (docs/proposals/own-mod-layer-proposal.md,
# phase 5).  A build that reads 5 would open any loadout as one whose own mod
# is new, and write that back over it: so every file says 6, where one
# holding a single profile used to say 2 for the builds that read that.
LOADOUT_FORMAT = 6
# The oldest format this build reads: 0.1.0's, the first release's.  A file
# of an older one is a test build's, which no release has to read (code
# standards, section 11), and is refused rather than read by shape.
OLDEST_LOADOUT_FORMAT = 6
LOADOUT_SUFFIX = ".annoload"
# The origin of the loadout's own mod (:attr:`Loadout.own`), which every
# change and op made here carries unless it says otherwise.  Asked as
# :func:`is_own_mod`, never as an origin that is false: "" is false, and
# so is None, which says no origin was given, and a check written for the
# mods read in from elsewhere passed over the own mod without saying so.
OWN = ""


def is_own_mod(origin: Optional[str]) -> bool:
    """Whether ``origin`` is the loadout's own mod's.  None, no origin
    given, is not: it is not any mod's."""
    return origin == OWN
# What a loadout's first profile is called: short, and not the loadout's own
# name -- named after the file, it read "Anno 1800 loadout -- Anno 1800
# loadout" everywhere the two stand side by side.
DEFAULT_PROFILE = "Main"


def list_patch_name(loadout: str) -> str:
    """What a loadout's own mod is called where it is the patch over the
    loadout's whole list (``Loadout.own.loads_last``) and nobody has named
    it: after the loadout, so that two loadouts never write their
    patches under one folder and one ModID, which are made out of the name
    (:func:`.generator.mod_folder_name`, :func:`.modinfo.own_mod_id`).
    English, as every name a mod is exported under is."""
    return "%s patch" % loadout


def loadout_name(path) -> str:
    """What a loadout file is called, without the suffix naming its kind."""
    path = Path(path)
    return (
        path.name[: -len(LOADOUT_SUFFIX)]
        if path.name.endswith(LOADOUT_SUFFIX)
        else path.stem
    )


class LoadoutError(Exception):
    """A file that cannot be read as a loadout.

    Distinct from the low-level errors underneath it -- a modder opening the
    wrong file is owed "this is not a loadout file", not
    ``'NoneType' object is not iterable``.
    """


def _listed(data: dict, key: str, where: str) -> list:
    """One of the lists a loadout is made of.

    Absent is fine and means none. *Present but not a list* is not: these hold
    the work, and quietly reading none of it would invite saving over what was
    there.
    """
    found = data.get(key)
    if found is None:
        return []
    if not isinstance(found, list):
        raise LoadoutError(tr(
            "%(file)s is damaged: its %(key)s should be a list and is not.",
            file=where, key=key,
        ))
    return found


def _unknown(raw: dict, known) -> Optional[dict]:
    """What ``raw`` holds that this build doesn't read, or None for nothing.

    From 0.1.0 on, a loadout goes back and forth between versions of the
    Studio: a newer one adds a field, an older one opens the file, and saves
    it at the next autosave.  Every key it doesn't know -- at the top of the
    file and in each entry of it -- is kept here and written back as it was,
    so going back a version loses nothing a newer one wrote
    (``docs/standards/code-standards.md``, section 11).  A field whose
    meaning an older build's edits would make wrong is a change of meaning,
    and bumps LOADOUT_FORMAT instead.
    """
    found = {key: value for key, value in raw.items() if key not in known}
    return found or None


def _with_unknown(out: dict, extra: Optional[dict]) -> dict:
    """``out`` with what :func:`_unknown` kept back in it, under what this
    build writes itself."""
    for key, value in (extra or {}).items():
        out.setdefault(key, value)
    return out


def _number(raw, fallback, cast):
    """A count or a timestamp, or the default when it is not one.

    Unlike the lists, these are bookkeeping rather than the user's work, so a
    bad one is not worth refusing the whole loadout over.
    """
    try:
        return cast(raw) if raw not in (None, "") else fallback
    except (TypeError, ValueError):
        return fallback


def _read_by(data: dict) -> Optional[int]:
    """The reader a saved loadout's readings were made under.

    A loadout saved before any look at its readings says None, which is
    this very version's reader where this very version wrote the file: one
    version of the Studio has one reader.  Written by another version, or
    saying nothing at all, it is 0, another reader's.
    """
    raw = data.get("read_by")
    if raw is None:
        return None if data.get("written_by") == __version__ else 0
    return _number(raw, 0, int)


def _builds(raw) -> Dict[str, dict]:
    """The build record, keeping only entries shaped as one.

    Bookkeeping like the numbers above: a damaged entry costs a build number
    starting again, which is not worth refusing the loadout over.
    """
    if not isinstance(raw, dict):
        return {}
    out = {}
    for origin, held in raw.items():
        if not isinstance(held, dict):
            continue
        build = _number(held.get("build"), 0, int)
        if build > 0:
            out[str(origin)] = {
                **(_unknown(held, _BUILD_KEYS) or {}),
                "build": build,
                "digest": str(held.get("digest") or ""),
                "assets": str(held.get("assets") or ""),
                "update": str(held.get("update") or ""),
            }
    return out


_BUILD_KEYS = frozenset({"build", "digest", "assets", "update"})
_PUBLISHED_KEYS = frozenset({"version", "build", "when", "modfile_id", "checkpoint"})


# The Studio's name, as it signs what it writes.
APP_NAME = "Anno Modding Studio"

# How a commented-out op of the Studio's own begins, which is how it is told
# from a comment the author wrote: see :func:`switched_off`.
SWITCHED_OFF_MARK = "switched off in Anno Modding Studio"

# How a comment the Studio wrote began, in its own words -- in files written
# before, a heading over an asset's ModOps or a line over each op -- or in the
# author's: see ``generator._notes`` and ``modops._comment_before``.  A comment
# that begins with neither is one somebody wrote by hand, and is read as the
# note the Studio would have written under the second.
STUDIO_SAYS_MARK = ":AMS:"
NOTE_MARK = "note:"


# A comment inside the comment an op is switched off in, as it is held and as
# it is written there.  A processing instruction is matched so as not to be
# gone through for comments: what it holds is not one, whatever it looks
# like.  It reads no ``&#45;`` either, so its own ``--`` is put back by the
# rule a comment's words are.
_HELD_COMMENT = re.compile(r"<\?.*?\?>|<!--(.*?)-->", re.S)
_HELD_COMMENT_WRITTEN = re.compile(r"<\?.*?\?>|<!&#45;&#45;(.*?)&#45;&#45;>", re.S)


def switched_off(op: List[str]) -> List[str]:
    """An op's lines, commented out: there to be read, and not by the game.

    What the Studio says above the op stays above it, and the op itself goes
    inside one comment that opens with :data:`changes.SWITCHED_OFF_MARK` --
    which is how reading the file back tells it from a comment the author
    wrote.  Taking that line and the closing one away is the op again, but
    for two things that cannot be said inside a comment.

    One is ``--``, which is written ``-&#45;``: the same text to anything
    that reads the op once it is uncommented.  That is rare, 37 ops of
    137,000: prose in texts, but for one ``--20`` in *Rebalancing Items* that
    is the value it sets.

    The other is another comment, and one in six of the ops in the mods on
    mod.io holds its own.  Its two ends are written the same way, each dash
    as ``&#45;``: ``<!&#45;&#45; why &#45;&#45;>``.  Nothing else an op is
    written with can read so, because a ``<`` in a text or an attribute is
    always ``&lt;``.  And every ``&`` in what the comment says is written
    ``&amp;``, so words in it that look like its end are not its end.
    :func:`switched_on` undoes both.  That much of the op is not plain XML:
    it is there for the Studio, and for a person who puts the dashes back.
    """
    said = 0
    while said < len(op) and op[said].startswith("  <!-- ") and op[said].endswith(" -->"):
        said += 1
    body = "\n".join(op[said:]) + "\n"
    body = _HELD_COMMENT.sub(
        lambda found: found.group(0).replace("&", "&amp;")
        if found.group(1) is None
        else "<!&#45;&#45;%s&#45;&#45;>" % found.group(1).replace("&", "&amp;"),
        body,
    )
    body = re.sub(r"(?<=-)-", "&#45;", body)
    return [
        *op[:said],
        "  <!-- %s:" % SWITCHED_OFF_MARK,
        *body.splitlines(),
        "  -->",
    ]


def switched_on(body: str) -> str:
    """What :func:`switched_off` put inside its comment, as XML to read: the
    op's own comments are comments again, and say what they said."""
    def said(words: str) -> str:
        return words.replace("&#45;", "-").replace("&amp;", "&")

    return _HELD_COMMENT_WRITTEN.sub(
        lambda found: said(found.group(0))
        if found.group(1) is None
        else "<!--%s-->" % said(found.group(1)),
        body,
    )


def _published(raw) -> dict:
    """The record of the last publish, or none if it is not shaped as one."""
    if not isinstance(raw, dict) or not str(raw.get("version") or "").strip():
        return {}
    return {
        **(_unknown(raw, _PUBLISHED_KEYS) or {}),
        "version": str(raw["version"]).strip(),
        "build": _number(raw.get("build"), 0, int),
        "when": _number(raw.get("when"), 0.0, float),
        "modfile_id": _number(raw.get("modfile_id"), 0, int),
        "checkpoint": _number(raw.get("checkpoint"), 0.0, float),
    }

# What a loadout is for -- see :attr:`Loadout.aim`. Three quite different
# jobs share this one window, and which one you are doing changes what should
# already be loaded, where a change should go, and what "export" ought to mean.
AIM_MAKE = "make"       # a mod of your own, to publish
AIM_TWEAK = "tweak"     # somebody else's mod, adjusted for you
AIM_MANAGE = "manage"   # a set of mods to play: a loadout

# What a change is filed under: ``(origin, guid, path, op, uid)``. Named
# because five bare strings in an annotation say nothing, and because the
# shape has grown -- origin and uid joined it when per-mod patch mods arrived.
ChangeKey = Tuple[str, str, str, str, str]


@lru_cache(maxsize=4096)
def _one_value(payload: str) -> Optional[Tuple[Tuple[str, ...], str]]:
    """``(the tags down to it, the value)`` for a payload that holds one value.

    The field itself, or the field inside sections that hold nothing else --
    which is how an ensure reaches into a section another mod made.  ``None``
    for a payload that sets more than one thing.  Remembered, because the
    conflicts are asked pair by pair and a payload is the same every time.
    """
    held = _ONE_VALUE.match(payload or "")
    if held is not None:
        return (held.group(1),), unescape(held.group(2) or "").strip()
    try:
        node = ElementTree.fromstring(payload)
    except ElementTree.ParseError:
        return None
    tags = [node.tag]
    while len(node) == 1:
        node = node[0]
        tags.append(node.tag)
    if len(node):
        return None
    return tuple(tags), (node.text or "").strip()


# How an asset a mod creates came to stand at the end of the file
# (:attr:`Change.at_the_end`): in a group the mod added there, or written
# straight under ``<ModOps>``.  See :func:`.reader.end_groups`.
IN_A_BLOCK = "block"
STRAIGHT = "straight"


@dataclass
class Change:
    """A single value replacement, which becomes exactly one ``<ModOp>``."""

    guid: str
    asset_label: str
    path: str
    tag: str
    old_value: str = ""
    new_value: str = ""
    positional: bool = False
    note: str = ""
    op: str = OP_REPLACE
    # XML fragment for the structural ops; unused by "replace".
    payload: str = ""
    # Short human description, e.g. "add entry to Costs".
    summary: str = ""
    # Stable identity for a structural change. Deliberately *not* derived from
    # the payload: a payload gets edited in place, and hashing it would silently
    # move the change to a new key, orphaning every reference already held to it.
    uid: str = ""
    # Switched off changes stay in the loadout, and in its saved file, but are
    # left out of the mod. Importing someone else's mod can land a hundred edits
    # at once, and "I do not want this one" should not have to mean deleting it.
    active: bool = True
    # Whether this change was made here or read out of a mod folder. The
    # distinction is what a loadout is *for*: reading a mods folder lands
    # thousands of changes that already exist on disk and can be read again,
    # while the handful you made exist nowhere else. History, the unsaved-work
    # warning and a re-read of the folder are all about the second kind.
    authored: bool = True
    # Where the op it came from sat in its mod's file. ModOps patch a document
    # that is changing underneath them, so writing them back out in a different
    # order can mean something different -- most visibly where a search and a
    # named op both reach for the same asset.
    order: int = 0
    # Where this change's own node sat among the ones its op carried. The
    # changes list is shown in the order a reader wants -- by asset, by name --
    # and that is not the order the op wrote them in. It matters for the one op
    # that puts several nodes side by side: written back in any other order,
    # they sit in the file in that order, and *Harborlife* adds ninety triggers
    # in one op with two of them sharing a GUID. See ``generator._side_by_side``.
    spot: int = 0
    # Which mod this change belongs to, "" for the one being authored here.
    # Two mods setting the same field are two changes, not one -- that is the
    # whole substance of "these two mods conflict".
    origin: str = ""
    # The search a change came out of, when it came out of one. A mod may say
    # "every asset shaped like this" rather than name them, and the assets that
    # answers to are not fixed: another mod can add one later. Keeping the
    # question next to the answer is what lets that be noticed.
    search: str = ""
    # Which stored :class:`Op` produced it. The expression above is what a
    # reader wants to see; this is what the code needs, because one mod
    # routinely targets by the same search twice for two different fields.
    op_uid: str = ""
    # True when this change's payload carries work from another op of the same
    # mod -- that op edited what this one had just added, so the edit lives in
    # here rather than in a change of its own. Whether that stops an op from
    # standing in for the change is decided by replaying the mod, not by this;
    # what this is for is being able to say *why* when it does.
    absorbs: bool = False
    # The kept op this change is an exception to: it sets, on one asset, a
    # field one of that op's answers also sets. The answer stays -- it is what
    # the op does -- and this is the edit after it that puts the one asset back,
    # which is what makes it a change of its own with a uid in its key.
    exception_of: str = ""
    # Where inside its op it sits, when the op is a group that goes back out
    # whole and holds groups of its own: the names of the groups it is in,
    # outermost first -- ("the group Aaden",).  A tweak tool's switches are those
    # groups, so the list draws them as rows under the op.
    within: tuple = ()
    # For a change that creates an asset: what players see it called, written
    # into every language's texts file when the mod is exported -- see
    # :func:`core.texts.write_names`. "" for none, and for every other change.
    in_game_name: str = ""
    # True for a replace that sets a field another mod made to the value that
    # mod gave it. In the stack it changes nothing, and typed in the editor it
    # would be an edit taken back -- but it is an op the mod has, and a mod
    # written back without it is not the mod that was read.
    restated: bool = False
    # For a change the Studio writes the ModOp for -- the loadout's own mod's,
    # made in Fields -- the uid of that ModOp in :attr:`Loadout.narrow`.  Not
    # ``op_uid``: that is an op kept as written, whose changes are its answers
    # and are not switched or typed over one at a time.  See :mod:`.narrow`.
    written_as: str = ""
    # For a created asset the reader has at the end of the file,
    # :data:`IN_A_BLOCK` or :data:`STRAIGHT`; "" for any other change.  Which
    # of the groups there it is in depends on the blocks of the mods that
    # load before it, so that is worked out when asked (:func:`.reader.end_groups`).
    at_the_end: str = ""
    # For an edit of yours to somebody else's mod, the mod of yours that
    # keeps it: a patch's origin, or "" for the loadout's own mod.  None
    # where nobody has said yet, which the end of the step settles
    # (:func:`.inside.assign`).  See :mod:`.inside`.
    kept_by: Optional[str] = None
    # What the file held for it that this build doesn't read: a newer
    # Studio's fields, written back as they were (see ``_unknown``).
    extra: Optional[dict] = field(default=None, compare=False, repr=False)

    def __post_init__(self):
        if self.is_structural and not self.uid:
            self.uid = uuid.uuid4().hex[:12]

    @property
    def key(self) -> ChangeKey:
        """Identity. Two adds to the same list are distinct edits; two replaces
        of the same value are the same edit -- unless they come from different
        mods, which is exactly the case worth being able to see."""
        return (self.origin, self.guid, self.path, self.op, self.uid)

    @property
    def is_structural(self) -> bool:
        return self.op in STRUCTURAL_OPS

    @property
    def is_noop(self) -> bool:
        # Only a replace can be typed back to its original; a structural op is
        # always a real change. And not one a mod wrote that way: see restated.
        return (
            self.op == OP_REPLACE
            and self.old_value == self.new_value
            and not self.restated
        )

    @property
    def creates_asset(self) -> bool:
        """True for the op that inserts a whole new asset into the game:
        beside the asset it names, after it or before it as the mod put it."""
        return self.op in (OP_ADD_NEXT, OP_ADD_PREV) and not self.path

    @property
    def replaces_its_own_key(self) -> bool:
        """True for a replace of the field its own list entry is found by.

        ``…/Item[GoodsOrPool='174519']/GoodsOrPool``: every other change to that
        entry finds it by the value this one replaces, so it has to go after
        them -- in the file written out and in the asset as the Studio shows it.
        """
        return self.keyed_entry is not None

    @property
    def keyed_entry(self) -> Optional[str]:
        """The list entry a key replacement finds, ``…/Item[GoodsOrPool='174519']``.

        ``None`` for anything that is not a replace of its own entry's key.
        """
        if self.op != OP_REPLACE:
            return None
        path = self.path.rstrip("/")
        keyed = _KEYED_FIELD.search(path)
        if keyed is None or keyed.group(1) != keyed.group(4):
            return None
        return path[: keyed.start(4) - 1]

    @property
    def ensured(self) -> str:
        """The path of the field an ensure makes sure of, "" for any other op.

        An ensure is a ``merge`` aimed at the node a field belongs in, carrying
        that one field: the game makes the field where it is absent and sets
        it where something already put it there.  An ``add`` says only the
        first half, and two mods that each add ``<SlotCount>`` leave the asset
        with two -- which is why a field the asset does not spell out is
        ensured rather than added, here and where a mod's own merge is read.
        """
        if self.op != OP_MERGE or not self.tag:
            return ""
        return "%s/%s" % (self.path.rstrip("/"), self.tag)

    @property
    def sets(self) -> Optional[Tuple[str, str]]:
        """``(path, value)`` when this change gives one field one value.

        A replace does, and so does an ensure of a value -- by different ops at
        different paths, which is why two mods setting one field are asked
        about here rather than by comparing their paths.  ``None`` for
        everything else, an ensure of several fields at once included: that is
        merged into what is there, and claims only the fields it names.
        """
        if self.op == OP_REPLACE:
            return self.path, self.new_value
        if self.ensured:
            held = _one_value(self.payload or "")
            if held is not None and held[0][0] == self.tag:
                return "%s/%s" % (self.path.rstrip("/"), "/".join(held[0])), held[1]
        return None

    def describe(self) -> Tuple[str, str]:
        """``(from, to)`` for the changes list."""
        if self.op == OP_REPLACE:
            return (self.old_value or "(empty)", self.new_value or "(empty)")
        if self.op == OP_REMOVE:
            return (self.old_value or "present", "removed")
        if self.op == OP_ADD:
            return ("absent", self.summary or "added")
        if self.op in (OP_ADD_NEXT, OP_ADD_PREV):
            return ("absent", self.summary or "inserted")
        return ("varies", self.summary or "set")

    def describe_change(self) -> str:
        """One line saying what this op does, for the changes list.

        Three ops, three shapes: a replace shows both values, an addition names
        what it puts there, a removal says only that.  Splitting these across
        "From"/"To" columns wasted most of the width on ops that have no "from".
        """
        if self.op == OP_REPLACE:
            if self.restated:
                # Translators: a change in the changes list that sets a field
                # to the value an earlier mod already gave it; %s is the value.
                # Short: the column is narrow and the value has to stay in it.
                return tr("%s (restated)", self.new_value or tr("(empty)"))
            return "%s → %s" % (
                self.old_value or tr("(empty)"),
                self.new_value or tr("(empty)"),
            )
        if self.op == OP_REMOVE:
            # Translators: what a change does, in the changes list.
            return tr("removed", context="change")
        if self.creates_asset:
            return tr("+ new asset")
        if self.op in (OP_ADD, OP_ADD_NEXT, OP_ADD_PREV, OP_MERGE):
            # Translators: an addition in the changes list, of a node whose
            # tag is not known.
            return "+ %s" % self.tag if self.tag else tr("+ node")
        return tr_stored(self.summary) or self.op

    def describe_target(self) -> str:
        """What the op is aimed at: a path, or the asset itself."""
        return self.path or tr("(the asset itself)")


def _names_alone(source: str, guid: str, game: str) -> bool:
    """Whether the ModOp ``source`` names the asset ``guid`` and no other,
    as ``game`` reads it: in ``GUID``, or at the head of its path as
    ``@guid/...``."""
    try:
        node = ElementTree.fromstring(source or "")
    except ElementTree.ParseError:
        return False
    return node.tag == "ModOp" and modop.assets_named(dict(node.attrib), game) == [guid]


def change_to_dict(change: Change) -> dict:
    """A change as a loadout file holds it."""
    out = asdict(change)
    out.pop("extra", None)
    # Only where there is one: a change no ModOp of the Studio's is written
    # for yet reads the same without it, and goes back out as it came.
    if not change.written_as:
        out.pop("written_as")
    if not change.at_the_end:
        out.pop("at_the_end")
    if change.kept_by is None:
        out.pop("kept_by")
    return _with_unknown(out, change.extra)


def change_from_dict(raw: dict) -> Optional[Change]:
    change = Change(
        guid=str(raw.get("guid", "")),
        asset_label=raw.get("asset_label", ""),
        path=raw.get("path", ""),
        tag=raw.get("tag", ""),
        old_value=raw.get("old_value", ""),
        new_value=raw.get("new_value", ""),
        positional=bool(raw.get("positional", False)),
        note=raw.get("note", ""),
        op=raw.get("op", OP_REPLACE),
        active=bool(raw.get("active", True)),
        origin=raw.get("origin", ""),
        order=int(raw.get("order", 0) or 0),
        spot=int(raw.get("spot", 0) or 0),
        authored=bool(raw.get("authored", True)),
        search=raw.get("search", ""),
        op_uid=raw.get("op_uid", ""),
        absorbs=bool(raw.get("absorbs", False)),
        exception_of=str(raw.get("exception_of", "") or ""),
        within=tuple(str(name) for name in (raw.get("within") or ())),
        payload=raw.get("payload", ""),
        summary=raw.get("summary", ""),
        uid=raw.get("uid", ""),
        in_game_name=str(raw.get("in_game_name", "") or ""),
        restated=bool(raw.get("restated", False)),
        written_as=str(raw.get("written_as", "") or ""),
        at_the_end=str(raw.get("at_the_end", "") or ""),
        kept_by=_keeper(raw),
        extra=_unknown(raw, _CHANGE_KEYS),
    )
    # A structural op may have no Path when it anchors on the asset itself
    # (creating a new asset next to an existing one).
    return change if change.guid and (change.path or change.is_structural) else None


@dataclass
class Op:
    """A ModOp kept as it was written, with the changes it comes to under it.

    It was called a rule while the only op kept was one aimed at a shape and
    not at assets it names: ``<ModOp Type="merge" Path="//Values/Trader">`` is
    one op about every trader in the game, and which traders those are is not
    fixed -- install a mod that adds one and the op covers it too.  A change
    cannot say that, being one edit to one node.  That is still the plainest
    reason to keep an op.  Every op of a mod is kept as written now, and every
    group.

    ``source`` is the ModOp verbatim, which is what makes it more than a
    label.  It can be run again when a new asset turns up, and it is written
    back out as itself, so a mod survives a trip through the Studio saying
    what it said.  One read from a loadout that predates all this has no
    source and is a label only.

    The op is the fact and its changes are today's answer, never the other
    way round.  An op that is on goes out as it is, whatever the Studio
    managed to make of it per asset: the changes underneath are what it comes
    to against the assets loaded now, shown for reading and for the conflicts
    they take part in, and they are not switched off one at a time.  "All of
    these, except that one" is the op followed by one that puts the one back,
    which is how the game reads it too.
    """

    expression: str = ""
    # The ModOp as written: ``<ModOp Type="merge" Path="//Values/Trader">…``.
    source: str = ""
    # One line for the list: "merge Elevation = 3".
    summary: str = ""
    # Which mod it belongs to, "" for the one being authored here.
    origin: str = ""
    note: str = ""
    # Where its op sat in the mod's file, so it can be written back there.
    order: int = 0
    # Which of the mod's files it is in: "" for its patch, and for a file an
    # ``<Include>`` names its path from the top of the mod.
    file: str = ""
    # Set on what stands for an ``<Include>`` line: the file it names, as
    # ``file`` says it.  ``source`` is the line, and ``scope`` is set, since
    # like a group's tag it is no ModOp.  The ops of the file are the ones
    # whose ``file`` it is, and no part of this.
    includes: str = ""
    # Set on what stands for a comment of the mod's file that is no op's
    # note: ``source`` is the comment, kept where it stands, and ``scope`` is
    # set, since it is no ModOp.  It is in no list of the mod's ops
    # (:func:`.keptops.of`) and has no row.
    aside: bool = False
    # How many blank lines stand before it in its file.
    gap: int = 1
    uid: str = ""
    active: bool = True
    # The assets it answered to when it was last asked, and how many changes it
    # came to.  The count is how the list notices an answer has been reverted
    # from under it, which is a caveat on what is shown, never on what is
    # written.
    matched: List[str] = field(default_factory=list)
    made: int = 0
    # What the Studio's reading of it could not follow, if anything: the op
    # matched assets it could not say the effect on, say. Information for the
    # reader, never a reason to write the op out any other way than as itself.
    caveat: str = ""
    # For a group that goes out whole: the assets the ops inside it that the
    # Studio could not read are aimed at, by GUID. What those ops do to them is
    # not shown, and the caveat says so; this says where.
    unread_at: List[str] = field(default_factory=list)
    # The changes whose payloads this op's edits also landed in, because it
    # edited something the mod had already added. Kept for the record; the
    # op goes out as itself regardless, and the payload carries the work too.
    folded: List[list] = field(default_factory=list)
    # Empty for a search, and for an op with no reason of its own to be kept.
    # Otherwise why an op that names its assets could not be settled into its
    # changes: it works its value out while patching -- a copy of another
    # asset's field, or arithmetic on the mod's own settings -- and settling
    # that into a fixed number would lose the link that was the point of
    # writing it that way. The changes underneath show what it comes to now;
    # the op itself is what goes back out.
    kept: str = ""
    # The groups inside it, when it is a group kept whole: each as
    # ``{"path": [names, outermost first], "off": bool}``, in file order.  The
    # ones switched off are here too -- they come to no changes, and are still
    # something the mod has and a tweak tool can switch on.  See Change.within.
    parts: List[dict] = field(default_factory=list)
    # A group with ops in it, kept as that: ``source`` is the group's own
    # tag and nothing inside it, and the ops and groups that are inside say
    # so themselves (``within``).  Not set for a group kept whole, whose
    # ``source`` is all of it and whose changes hang off it directly.
    scope: bool = False
    # For such a group: it does not run as things stand, so nothing in it
    # comes to anything today.
    idle: bool = False
    # The groups it sits in, by the uid of each one's op, outermost first.
    within: List[str] = field(default_factory=list)
    # For a group a tweak tool finds by name: ``{"id", "title", "also",
    # "unswitched"}``, as a part of a group kept whole has them.
    tool: dict = field(default_factory=dict)
    # Its target is a search across assets and not assets it names.  Said
    # outright: ``kept`` empty used to say it, while only an op that could
    # not be written narrow was kept and every such op had a reason.
    searches: bool = False
    # It comes to one change, at the very node it names in the one asset it
    # names: the op and the change say the same thing, so a list draws them
    # as one row.  Set as it is read; whether it is still so of the changes
    # under it now is for whoever draws it to check (keptops.stands_alone).
    plain: bool = False
    # It was typed here, or composed in the pattern form, and is in no mod's
    # folder to be read again: work, as an authored change is.
    authored: bool = False
    # Why the changes under it are not a narrow form of it, where the reader
    # knows they are not: what is shown swaps a whole node, or it missed in
    # some of the assets it names and its author allows that.  ``kept`` may
    # say the same, and says something else where the op has a first reason
    # of another kind; this is what *Write as narrow ModOps* goes by.
    wide: str = ""
    # For an op typed among somebody else's, or one of theirs switched off
    # here, the mod of yours that keeps that edit: as :attr:`Change.kept_by`.
    kept_by: Optional[str] = None
    # What the file held for it that this build doesn't read: a newer
    # Studio's fields, written back as they were (see ``_unknown``).
    extra: Optional[dict] = field(default=None, compare=False, repr=False)

    def __post_init__(self):
        if not self.uid:
            self.uid = uuid.uuid4().hex[:12]

    @property
    def key(self) -> Tuple[str, str]:
        return (self.origin, self.uid)

    @property
    def can_expand(self) -> bool:
        """Whether it can be asked again, rather than only remembered."""
        return bool(self.source)

    @property
    def can_write(self) -> bool:
        """Whether it can go back out as the op it came from."""
        return bool(self.source)

    def label(self) -> str:
        return "%s  %s" % (self.expression, self.summary) if self.summary else self.expression

    def to_dict(self) -> dict:
        return _as_saved(self)

    @classmethod
    def from_dict(cls, raw: dict) -> Op:
        return cls(
            expression=str(raw.get("expression", "")),
            source=raw.get("source", ""),
            summary=raw.get("summary", ""),
            origin=raw.get("origin", ""),
            note=raw.get("note", ""),
            order=int(raw.get("order", 0) or 0),
            file=str(raw.get("file", "") or ""),
            includes=str(raw.get("includes", "") or ""),
            aside=bool(raw.get("aside", False)),
            gap=int(raw["gap"]) if "gap" in raw else 1,
            uid=raw.get("uid", ""),
            active=bool(raw.get("active", True)),
            matched=[str(guid) for guid in (raw.get("matched") or ())],
            made=int(raw.get("made", 0) or 0),
            caveat=raw.get("caveat", ""),
            unread_at=[str(guid) for guid in (raw.get("unread_at") or ())],
            folded=[list(key) for key in (raw.get("folded") or ())],
            kept=raw.get("kept", ""),
            parts=[dict(part) for part in (raw.get("parts") or ()) if isinstance(part, dict)],
            searches=bool(raw.get("searches", False)),
            plain=bool(raw.get("plain", False)),
            scope=bool(raw.get("scope", False)),
            idle=bool(raw.get("idle", False)),
            within=[str(uid) for uid in (raw.get("within") or ())],
            tool=dict(raw.get("tool") or {}),
            authored=bool(raw.get("authored", False)),
            wide=str(raw.get("wide", "") or ""),
            kept_by=_keeper(raw),
            extra=_unknown(raw, _OP_KEYS),
        )


# What a tweak tool reads out of a mod's file.  The loader passes over it.
TOOL_MARKUP = ("imyaexpose", "imyatweaks")
_OPENING_TAG = re.compile(r"\s*<\s*([A-Za-z_][\w.-]*)")


@dataclass
class Unread:
    """One ModOp the Studio could not translate, kept so it can be looked at.

    A mod that is only nine-tenths read is not the same thing as one that is
    fully read, and a count saying so is not much help on its own: the useful
    question is *which* op, out of which mod, and what stopped it.  So the op
    is kept as written, with the reason it was refused.

    Kept for writing back, too, not only for looking at.  An export of the mod
    carries the op exactly as it arrived, at the place in the file it came
    from, so a mod the Studio could read only nine tenths of still does in
    game what it did before it was opened here.  That is what ``order`` is
    for: the op's position among the mod's own, the same numbering its
    changes and kept ops carry.
    """

    origin: str = ""
    # The op as the mod wrote it, and the short form for a list.
    source: str = ""
    label: str = ""
    why: str = ""
    where: str = ""
    # The comment above it in the mod, which is usually what it is for.
    note: str = ""
    # Where it sat among the mod's ops, so an export can put it back there.
    order: int = 0
    # Which of the mod's files it is in: see :attr:`Op.file`.
    file: str = ""
    # How many blank lines stand before it in its file.
    gap: int = 1
    # The groups it sits in, as a kept op says it: see :attr:`Op.within`.
    within: List[str] = field(default_factory=list)
    # What the file held for it that this build doesn't read: a newer
    # Studio's fields, written back as they were (see ``_unknown``).
    extra: Optional[dict] = field(default=None, compare=False, repr=False)

    @property
    def markup(self) -> bool:
        """Whether this is a tweak tool's markup and no op at all.

        It is held here because an export has to put it back where it was,
        and for nothing else: the game does nothing with it, so it is not
        something the Studio failed to read, and no list or count of those
        has it.  What it offers is the mod's options.
        """
        tag = _OPENING_TAG.match(self.source or "")
        return tag is not None and tag.group(1).lower() in TOOL_MARKUP

    def to_dict(self) -> dict:
        return _as_saved(self)

    @classmethod
    def from_dict(cls, raw: dict) -> Unread:
        return cls(
            origin=str(raw.get("origin", "")),
            source=raw.get("source", ""),
            label=raw.get("label", ""),
            why=raw.get("why", ""),
            where=raw.get("where", ""),
            note=raw.get("note", ""),
            order=int(raw.get("order", 0) or 0),
            file=str(raw.get("file", "") or ""),
            gap=int(raw["gap"]) if "gap" in raw else 1,
            within=[str(uid) for uid in (raw.get("within") or ())],
            extra=_unknown(raw, _UNREAD_KEYS),
        )


@owners.schema("layer")
@dataclass
class ModLayer:
    """One mod read in from elsewhere, and what is known about it.

    ``origin`` is the identity every change of that mod carries.  It is the
    folder name rather than the mod's declared ID: the folder is what the game
    itself keys on, it is unique within a mods directory, and plenty of mods
    declare no ID at all.
    """

    origin: str = field(metadata=owners.LOADOUT)
    name: str = field(default="", metadata=owners.DETAIL)
    folder: str = field(default="", metadata=owners.LOADOUT)
    category: str = field(default="", metadata=owners.DETAIL)
    version: str = field(default="", metadata=owners.DETAIL)
    creator: str = field(default="", metadata=owners.DETAIL)
    description: str = field(default="", metadata=owners.DETAIL)
    mod_id: str = field(default="", metadata=owners.DETAIL)
    # Which mod on mod.io this is, once something has said so: the loader's
    # log, the game's own download cache, a name the user confirmed.  Zero
    # until then.  It belongs to the mod rather than to the folder -- the same
    # ModID is often installed twice, once by the in-game browser and once by
    # hand -- so it is kept here, with the mod, rather than looked up by path.
    modio_id: int = field(default=0, metadata=owners.LOADOUT)
    # Where the game would load it: the position of its folder in load order.
    order: int = field(default=0, metadata=owners.LOADOUT)
    # ModOps the Studio could not translate. Kept because a mod that is only
    # nine-tenths read is not the same thing as one that is fully read, and the
    # effective asset should not pretend otherwise.
    unread: int = field(default=0, metadata=owners.LOADOUT)
    # What the mod's own settings are set to, and what it offers. A configurable
    # mod does different things at different settings, so which settings it was
    # read at is part of what these changes are.
    options: dict = field(default_factory=dict, metadata=owners.LOADOUT)
    offered: dict = field(default_factory=dict, metadata=owners.LOADOUT)
    # And how it was read: ``keep_inert``, from the dialog it came through
    # (:meth:`~.modops.Reading.recorded`). It decides what its changes are as
    # much as its settings do, so reading it again has to ask the same way;
    # left unrecorded, changing one of a mod's settings quietly re-read it at
    # the defaults. Empty means the defaults.
    read_as: dict = field(default_factory=dict, metadata=owners.LOADOUT)
    # The collection on mod.io that brought this mod, by name, when the
    # Studio installed it as part of one; "" otherwise -- including for a mod
    # the collection lists that was installed already: that one is yours, and
    # also in it (see :attr:`Loadout.listed`). So that what came in
    # together can be switched off together without taking what you had with
    # it. Kept by the loadout by folder name at install time and attached
    # here when the folder is read in -- see :attr:`Loadout.provenance`.
    from_collection: str = field(default="", metadata=owners.LOADOUT)
    # The mod this one exists to change, when it is a patch. A patch is an
    # ordinary mod named so that it loads straight after the one it is about,
    # holding your changes to it -- so that updating that mod does not take
    # them with it. See :mod:`core.patching`.
    patches: str = field(default="", metadata=owners.LOADOUT)
    # For a patch, the other mods it needs besides the one it was made for, by
    # origin: it is coupled to all of them -- inert unless every one is in
    # effect -- and loads after all of them.  A buff that is only fair while
    # two nerfs are on is a patch of both (docs/proposals/patches-proposal.md,
    # section 4).
    needs: List[str] = field(default_factory=list, metadata=owners.LOADOUT)
    # When this is a fork -- somebody else's mod made into one of yours, with
    # a ModID of its own -- the ModID of the mod it was made of, which its
    # modinfo deprecates.  See :mod:`core.forking`.
    fork_of: str = field(default="", metadata=owners.DETAIL)
    # Whether this is a mod of yours read from a folder that the loadout
    # keeps in a project of its own, made one here: edited directly from then
    # on.  A mod with your name on it and no project is read like anybody
    # else's until it is (section 5.5 of the projects and loadouts proposal).
    kept: bool = field(default=False, metadata=owners.LOADOUT)
    # Whether the game is loading this mod at all. Installed and switched off is
    # an ordinary state -- both games have their own way of saying it, and the
    # mods folder this was written against had seven of them -- and it is not the
    # same as uninstalled: the mod is still there, still in the list, still one
    # tick away. See :mod:`core.switches`.
    active: bool = field(default=True, metadata=owners.LOADOUT)
    # Mods this one declares it has to load after, by their ModID. Both games
    # let a mod say so and the loader honours it over its own sort order, so it
    # is part of where this mod sits rather than a note about it.
    load_after: List[str] = field(default_factory=list, metadata=owners.DETAIL)
    # The mod's own modinfo.json, exactly as its author wrote it. The Studio
    # models six of its fields; the file carries a dozen, and the ones it does
    # not model are the ones it must not throw away -- what this mod is
    # incompatible with, what it deprecates, what it warns about. Writing a
    # loaded mod back out starts from this and overwrites only what the Studio
    # owns. See :func:`core.modinfo.build`.
    declared: dict = field(default_factory=dict, metadata=owners.DETAIL)
    # What only a mod written here has, a mod read from a folder never: so
    # far only the loadout's own mod (:attr:`Loadout.own`).  Where its Lua
    # lives on this machine -- its module paths are read from here, and
    # export copies them into the mod from here: the author's own folder,
    # since the Lua is written in an editor made for it.
    scripts_folder: str = field(default="", metadata=owners.DETAIL)
    # The community GUID range its new assets draw from, named as the
    # register lists its owner -- only when it is not the one the creator
    # name already picks, so a mod stays with the range it was started in
    # even when the name it publishes under is spelled differently from the
    # listing.
    guid_range: str = field(default="", metadata=owners.DETAIL)
    # Whether it loads after everything else in the loadout, as the own mod
    # of a loadout made to play does: it is the patch for the whole list.
    # Written by the Start tab's third card, and carried by a fork. See
    # :meth:`Loadout.own_load_after`.
    loads_last: bool = field(default=False, metadata=owners.DETAIL)
    # Mods bundled inside this one that the game does not load from here,
    # because another mod ships a copy it prefers: the passed-over copy's
    # origin -> the origin of the copy that loads. A [Shared] piece is loaded
    # once however many mods carry it, so this host's copy is not a layer --
    # and without this the list could not say it was ever in here.
    copies: Dict[str, str] = field(default_factory=dict, metadata=owners.LOADOUT)
    # The version each of those copies declares, by the same origin.  Which
    # copy loads moves with the switches -- the one read can be in a host
    # that is now off, while another host's copy is on -- and the version is
    # what says whether the copy that loads is the one these changes are of.
    copy_versions: Dict[str, str] = field(default_factory=dict, metadata=owners.LOADOUT)
    # Why its reading may be out of date, each a kind and the mod it is about:
    # what loads before it is not what did when it was read.  Empty where the
    # reading is fresh.  See :mod:`core.stale`.  A mod edited here is marked
    # for itself with a third part: the place in its file it is read again
    # from.
    stale: List[List[str]] = field(default_factory=list, metadata=owners.LOADOUT)
    # Whether its ModOps were edited here: its file is then what the loadout
    # writes for it and not what its folder holds, and that is what a
    # reading of the stack reads it from.
    edited: bool = field(default=False, metadata=owners.LOADOUT)
    # What the file held for it that this build doesn't read: a newer
    # Studio's fields, written back as they were (see ``_unknown``).
    extra: Optional[dict] = field(default=None, compare=False, repr=False, metadata=owners.LOADOUT)
    # Whether the Studio writes this mod's modinfo whole, there being none of
    # an author's to write over: the loadout's own mod's.  Its ``declared``
    # then holds only the blocks the Studio writes (:attr:`Loadout.scripts`
    # and the rest), not a modinfo as written (:func:`.modinfo.build`).
    # Always so for :attr:`Loadout.own`, so never saved.
    written_whole: bool = field(default=False, metadata=owners.MEMORY)
    # Whether the name it goes by is its folder's, its modinfo giving it
    # none: not a name to write into that modinfo.  Worked out for the view
    # an export makes of it (:func:`.generator.for_mod`), never saved.
    named_after_folder: bool = field(default=False, metadata=owners.MEMORY)

    @property
    def label(self) -> str:
        return self.name or self.origin

    def to_dict(self) -> dict:
        out = _as_saved(self)
        # Only a fork says what it was made of, and only a mod of yours that a
        # project keeps says so.
        if not self.fork_of:
            out.pop("fork_of", None)
        if not self.kept:
            out.pop("kept", None)
        # Nor does a mod read from a folder have what a mod written here has.
        for name in ("scripts_folder", "guid_range", "loads_last"):
            if not out.get(name):
                out.pop(name, None)
        for name in owners.names("layer", owners.MEMORY):
            out.pop(name, None)
        return out

    @classmethod
    def from_dict(cls, raw: dict) -> ModLayer:
        return cls(
            origin=str(raw.get("origin", "")),
            fork_of=str(raw.get("fork_of") or ""),
            kept=bool(raw.get("kept", False)),
            name=raw.get("name", ""),
            folder=raw.get("folder", ""),
            category=raw.get("category", ""),
            version=raw.get("version", ""),
            creator=raw.get("creator", ""),
            description=raw.get("description", ""),
            mod_id=raw.get("mod_id", ""),
            modio_id=int(raw.get("modio_id", 0) or 0),
            order=int(raw.get("order", 0) or 0),
            unread=int(raw.get("unread", 0) or 0),
            options=dict(raw.get("options") or {}),
            offered=dict(raw.get("offered") or {}),
            read_as={str(k): bool(v) for k, v in (raw.get("read_as") or {}).items()},
            patches=raw.get("patches", ""),
            needs=[str(v) for v in (raw.get("needs") or []) if v],
            from_collection=str(raw.get("from_collection") or ""),
            active=bool(raw.get("active", True)),
            load_after=[str(v) for v in (raw.get("load_after") or [])],
            declared=dict(raw.get("declared") or {}),
            scripts_folder=str(raw.get("scripts_folder") or ""),
            guid_range=str(raw.get("guid_range") or ""),
            loads_last=bool(raw.get("loads_last", False)),
            copies={str(k): str(v) for k, v in (raw.get("copies") or {}).items()},
            copy_versions={str(k): str(v) for k, v in (raw.get("copy_versions") or {}).items()},
            stale=[
                [str(part) for part in why]
                for why in (raw.get("stale") or [])
                if isinstance(why, (list, tuple)) and len(why) in (2, 3)
            ],
            edited=bool(raw.get("edited", False)),
            extra=_unknown(raw, _LAYER_KEYS),
        )


def own_record(**details) -> ModLayer:
    """A record of a loadout's own mod (:attr:`Loadout.own`): origin "",
    with what a new mod starts as, and ``details`` set on it."""
    own = ModLayer(origin="", name="My Anno Mod", category="Gameplay", version="1.0", written_whole=True)
    return replace(own, **details)


def _as_saved(entry) -> dict:
    """An op, an unread op or a layer as a loadout file holds it."""
    out = asdict(entry)
    out.pop("extra", None)
    # Only an edit of yours to somebody else's mod has a keeper.
    if out.get("kept_by", "") is None:
        out.pop("kept_by")
    return _with_unknown(out, entry.extra)


def _keeper(raw: dict) -> Optional[str]:
    """Which mod of yours keeps an edit, as a file holds it: None for none."""
    kept = raw.get("kept_by")
    return kept if isinstance(kept, str) else None


# What each entry of a loadout file reads: its fields, and nothing else.
# Anything else is a newer Studio's.  No name is kept back for a file of
# before 0.1.0: one a later build gave a field would be dropped here.
_CHANGE_KEYS = frozenset(f.name for f in fields(Change))
_OP_KEYS = frozenset(f.name for f in fields(Op))
_UNREAD_KEYS = frozenset(f.name for f in fields(Unread))
_LAYER_KEYS = frozenset(f.name for f in fields(ModLayer))
_CHECKPOINT_KEYS = frozenset({
    "timestamp", "label", "version", "changes", "kind", "ops", "narrow", "details", "loadout",
    "inside",
})
_LOADOUT_CHECKPOINT_KEYS = frozenset({
    "timestamp", "label", "kind", "mods", "profile", "profiles", "projects",
})
_PROFILE_KEYS = frozenset({"name", "switches"})


# What a checkpoint marks, when it marks a release: the mod written out for
# somebody else, or sent to mod.io. See :mod:`core.releases`.
KIND_EXPORT = "export"
KIND_PUBLISH = "publish"

# Where a checkpoint notes, on each change and op in it, the loadout that
# edit was made in.
MADE_IN = "made_in"
# A mod's details a checkpoint leaves out, and a restore leaves as they are:
# its ModID is which project it is, and a carried-on mod's modinfo as read
# is a reading of its folder.
_DETAILS_KEPT_OUT = ("mod_id", "declared")


def _kept_out(record: ModLayer) -> Tuple[str, ...]:
    """What a checkpoint leaves out of ``record``'s details: its ModID, and
    its modinfo as read -- but not the blocks of a mod the Studio writes
    whole, which are its work like its name is."""
    return ("mod_id",) if record.written_whole else _DETAILS_KEPT_OUT


def _without_made_in(raw: dict) -> dict:
    return {key: value for key, value in raw.items() if key != MADE_IN}


# What a change or an op in a checkpoint is known by, and what it says: an
# entry saying what it said at the checkpoint before keeps the loadout it was
# noted as made in there.  Not where it stands, which moves as others do.
_CHANGE_SAYS = (("origin", "guid", "path", "op", "uid"), ("new_value", "payload", "active", "note"))
_OP_SAYS = (("origin", "uid"), ("source", "active", "note"))
# The same of a mod's own work, which is all of one mod: not by the name a
# loadout has for it, which is another in another loadout and after a
# rename (Checkpoint.as_changes).  By it, work read back unchanged was work
# moved, and every edit was noted as made in the loadout looking at it.
_OWN_CHANGE_SAYS = (_CHANGE_SAYS[0][1:], _CHANGE_SAYS[1])
_OWN_OP_SAYS = (_OP_SAYS[0][1:], _OP_SAYS[1])


def _says(raw: dict, keys) -> tuple:
    return tuple(json.dumps(raw.get(key), sort_keys=True) for key in keys)


def _noted(entries: List[dict], before: Sequence[dict], loadout: str, says) -> List[dict]:
    """``entries`` each noted with the loadout it was made in: as noted at
    the checkpoint ``before`` where it says what it said there, else
    ``loadout``."""
    known, said = says
    was = {_says(raw, known): raw for raw in before if isinstance(raw, dict)}
    for raw in entries:
        prior = was.get(_says(raw, known))
        if prior is not None and _says(prior, said) == _says(raw, said):
            made_in = str(prior.get(MADE_IN) or "")
        else:
            made_in = loadout
        if made_in:
            raw[MADE_IN] = made_in
    return entries


def _work_in(checkpoint: Checkpoint) -> tuple:
    """What a checkpoint holds of a mod's work, to compare with another:
    what each change and op says, the details, and what it keeps inside
    somebody else's ops."""
    def each(raws, says):
        return sorted(_says(raw, says[0] + says[1]) for raw in raws if isinstance(raw, dict))

    return (
        each(checkpoint.changes, _OWN_CHANGE_SAYS),
        each(checkpoint.ops, _OWN_OP_SAYS),
        each(checkpoint.narrow, _OWN_OP_SAYS),
        json.dumps(checkpoint.details, sort_keys=True),
        inside_says(checkpoint.inside),
    )


# What an edit kept inside somebody else's ops says, by the key a project
# keeps it under (:data:`.projects.INSIDE`): an op of theirs switched off or
# taken out by its text, as the uid their mod's reading gives it moves with
# an update.  Not where any of it stands, which moves as their ops do.
_INSIDE_SAYS = {
    "ops": _OP_SAYS[0] + _OP_SAYS[1],
    "changes": _CHANGE_SAYS[0] + _CHANGE_SAYS[1],
    "off": ("origin", "source"),
}


def kept_changes(kept: dict) -> List[Change]:
    """The changes in ``kept``, the edits a mod of yours keeps inside
    somebody else's ops as :meth:`Loadout.kept_inside` and a checkpoint hold
    them: each under the origin of the mod it is in."""
    out = []
    for raw in kept.get("changes") or ():
        change = change_from_dict(_without_made_in(raw)) if isinstance(raw, dict) else None
        if change is not None:
            out.append(change)
    return out


def inside_says(kept: dict) -> tuple:
    """What ``kept`` -- the edits a mod of yours keeps inside somebody
    else's ops, as :meth:`Loadout.kept_inside` and a checkpoint hold them
    -- says, to compare with another."""
    said = []
    for key in sorted(kept):
        for entry in kept[key] if isinstance(kept[key], list) else ():
            if isinstance(entry, list):
                said.append((key, json.dumps(entry[:2])))
            elif isinstance(entry, dict):
                says = _INSIDE_SAYS.get(key) or tuple(
                    name for name in entry if name not in ("at", "kept_by", MADE_IN))
                said.append((key, json.dumps(_says(entry, sorted(says)))))
    return tuple(sorted(said))


@dataclass
class Checkpoint:
    """One mod of yours as it stood at one moment, with a note about why:
    a moment of its project's history (``docs/proposals/
    projects-and-loadouts-proposal.md``, section 9).

    Its changes, its ModOps and its details, each change and op with the
    loadout it was made in.  Never its builds, its mod.io link or the record
    of its last publish: those are facts about what happened outside the
    file, and a restore to before build 12 must not hand out build 12 again.
    """

    timestamp: float
    label: str
    version: str
    changes: List[dict] = field(default_factory=list)
    # What the checkpoint marks: KIND_EXPORT or KIND_PUBLISH, which are
    # releases and so what "since the last release" is measured from, or ""
    # for one taken by hand or around a restore.
    kind: str = ""
    # The ModOps made here as they stood -- typed in the XML view, or
    # composed as a pattern -- which some of the changes are what they come
    # to.  Without them a restore put those changes back under whatever ops
    # were there now, and an op made since the checkpoint stayed in the mod.
    ops: List[dict] = field(default_factory=list)
    # And the ModOps the Studio wrote for the changes made in Fields, which
    # are those changes' record (:attr:`Loadout.narrow`).
    narrow: List[dict] = field(default_factory=list)
    # The mod's details as they stood: its name, version, description and
    # the rest its project keeps (:func:`.projects.layer_details`), less
    # what :func:`_kept_out` leaves out.
    details: dict = field(default_factory=dict)
    # The loadout it was taken in, by name.
    loadout: str = ""
    # The edits the mod kept inside somebody else's ops (:mod:`.inside`),
    # as its project keeps them (:data:`.projects.INSIDE`): ops of theirs
    # taken out or switched off, ops of yours among theirs, changes, held
    # edits.  Without them a restore left whatever was there now.
    inside: dict = field(default_factory=dict)
    # What the file held for it that this build doesn't read: a newer
    # Studio's fields, written back as they were (see ``_unknown``), and
    # what its ``inside`` held that is not a list of edits.
    extra: Optional[dict] = field(default=None, compare=False, repr=False)
    inside_kept: dict = field(default_factory=dict, compare=False, repr=False)

    @property
    def when(self) -> str:
        return time.strftime("%Y-%m-%d %H:%M", time.localtime(self.timestamp))

    def as_changes(self, origin: Optional[str] = None) -> List[Change]:
        """Its changes; with ``origin``, each as the mod at ``origin``'s.

        A checkpoint is one mod's, and keeps each change under the name the
        loadout it was taken in had for the mod -- "" where it was that
        loadout's own, its folder's name where it was loaded beside others.
        Read in another loadout, or after a rename, under that name, the
        same changes were another mod's: listed as taken out and made
        again, and restored as a second mod beside the first (R3-36, R3-38,
        hands-on run 3)."""
        out = []
        for raw in self.changes:
            change = change_from_dict(_without_made_in(raw))
            if change is not None:
                if origin is not None:
                    change.origin = origin
                out.append(change)
        return out

    def as_ops(self, which: str = "ops", origin: Optional[str] = None) -> List[Op]:
        """Its ModOps made here, or with ``which`` "narrow" the ones the
        Studio wrote, as ops; with ``origin``, as :meth:`as_changes` has it."""
        ops = [Op.from_dict(_without_made_in(raw)) for raw in getattr(self, which)]
        if origin is not None:
            for op in ops:
                op.origin = origin
        return ops

    def made_in(self) -> Dict[ChangeKey, str]:
        """The loadout each of its changes was made in, by the change's key;
        a change it has no note of is left out."""
        out: Dict[ChangeKey, str] = {}
        for raw in [*self.changes, *(self.inside.get("changes") or ())]:
            said = raw.get(MADE_IN) if isinstance(raw, dict) else None
            change = change_from_dict(_without_made_in(raw)) if said else None
            if change is not None:
                out[change.key] = str(said)
        return out

    def to_dict(self) -> dict:
        return _with_unknown({
            "timestamp": self.timestamp,
            "label": self.label,
            "version": self.version,
            "changes": list(self.changes),
            "kind": self.kind,
            "ops": list(self.ops),
            **({"narrow": list(self.narrow)} if self.narrow else {}),
            **({"details": dict(self.details)} if self.details else {}),
            **({"loadout": self.loadout} if self.loadout else {}),
            **({"inside": {**self.inside_kept, **copy.deepcopy(self.inside)}}
               if self.inside or self.inside_kept else {}),
        }, self.extra)

    @classmethod
    def from_dict(cls, raw: dict) -> Checkpoint:
        details = raw.get("details")
        found = raw.get("inside")
        inside: dict = found if isinstance(found, dict) else {}
        return cls(
            timestamp=float(raw.get("timestamp", 0.0)),
            label=raw.get("label", ""),
            version=raw.get("version", ""),
            changes=list(raw.get("changes", [])),
            kind=str(raw.get("kind") or ""),
            ops=list(raw.get("ops", [])),
            narrow=list(raw.get("narrow") or []),
            details=dict(details) if isinstance(details, dict) else {},
            loadout=str(raw.get("loadout") or ""),
            inside={str(key): list(entries) for key, entries in inside.items() if isinstance(entries, list)},
            inside_kept={str(key): value for key, value in inside.items() if not isinstance(value, list)},
            extra=_unknown(raw, _CHECKPOINT_KEYS),
        )


@dataclass
class LoadoutCheckpoint:
    """The loadout's list as it stood at one moment: a moment of the
    loadout's history (``docs/proposals/projects-and-loadouts-proposal.md``,
    section 9).  What it holds and how it is restored is
    :mod:`core.histories`.

    Its mods, in their order, each with whether it is on and the settings it
    is read at; its profiles; and which revision of each project of its mods
    it had, with the checkpoint of that project it stood at then.  None of
    the mods' own work: that is each project's history.
    """

    timestamp: float
    label: str
    # KIND_EXPORT or KIND_PUBLISH where it was taken with a release of one of
    # its mods; "" by hand or around a restore.
    kind: str = ""
    # Each mod as :func:`.histories.entry_of` keeps it: enough of its layer to
    # put it back and read it again.
    mods: List[dict] = field(default_factory=list)
    profile: str = ""
    profiles: List[dict] = field(default_factory=list)
    # By origin: ``{"path", "mod_id", "revision", "checkpoint"}``, the last
    # being the timestamp of the project's checkpoint it stood at.
    projects: Dict[str, dict] = field(default_factory=dict)
    extra: Optional[dict] = field(default=None, compare=False, repr=False)

    @property
    def when(self) -> str:
        return time.strftime("%Y-%m-%d %H:%M", time.localtime(self.timestamp))

    def to_dict(self) -> dict:
        return _with_unknown({
            "timestamp": self.timestamp,
            "label": self.label,
            "kind": self.kind,
            "mods": [dict(mod) for mod in self.mods],
            "profile": self.profile,
            "profiles": [dict(profile) for profile in self.profiles],
            "projects": {origin: dict(held) for origin, held in self.projects.items()},
        }, self.extra)

    @classmethod
    def from_dict(cls, raw: dict) -> LoadoutCheckpoint:
        projects = raw.get("projects")
        return cls(
            timestamp=_number(raw.get("timestamp"), 0.0, float),
            label=str(raw.get("label") or ""),
            kind=str(raw.get("kind") or ""),
            mods=[dict(mod) for mod in raw.get("mods") or () if isinstance(mod, dict)],
            profile=str(raw.get("profile") or ""),
            profiles=[dict(p) for p in raw.get("profiles") or () if isinstance(p, dict)],
            projects={
                str(origin): dict(held) for origin, held in projects.items() if isinstance(held, dict)
            } if isinstance(projects, dict) else {},
            extra=_unknown(raw, _LOADOUT_CHECKPOINT_KEYS),
        )


@dataclass
class ChangeDiff:
    """What moved between two change sets."""

    added: List[Change] = field(default_factory=list)
    removed: List[Change] = field(default_factory=list)
    modified: List[Tuple[Change, Change]] = field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        return not (self.added or self.removed or self.modified)

    @property
    def total(self) -> int:
        return len(self.added) + len(self.removed) + len(self.modified)

    def summary(self) -> str:
        if self.is_empty:
            return tr("no changes")
        # A tally rather than a sentence: each count stands on its own.
        bits = []
        if self.added:
            # Translators: a count of changes, as in "3 added, 1 removed".
            bits.append(trn("%(n)s added", "%(n)s added", len(self.added)))
        if self.removed:
            # Translators: a count of changes, as in "3 added, 1 removed".
            bits.append(trn("%(n)s removed", "%(n)s removed", len(self.removed)))
        if self.modified:
            # Translators: a count of changes, as in "3 added, 1 edited".
            bits.append(trn("%(n)s edited", "%(n)s edited", len(self.modified)))
        return tr(", ", context="between items of a list").join(bits)


def diff_changes(before: Sequence[Change], after: Sequence[Change]) -> ChangeDiff:
    """Compare two change sets by their (guid, path) identity."""
    before_map = {c.key: c for c in before}
    after_map = {c.key: c for c in after}
    diff = ChangeDiff()
    for key, change in after_map.items():
        previous = before_map.get(key)
        if previous is None:
            diff.added.append(change)
        elif (previous.new_value, previous.active) != (change.new_value, change.active):
            # Switching a change off is an edit to the mod, not a deletion from
            # the loadout -- so history has to see it, or restoring a version
            # would silently leave today's on/off state behind.
            diff.modified.append((previous, change))
    for key, change in before_map.items():
        if key not in after_map:
            diff.removed.append(change)
    diff.added.sort(key=lambda c: (c.asset_label.lower(), c.path))
    diff.removed.sort(key=lambda c: (c.asset_label.lower(), c.path))
    diff.modified.sort(key=lambda pair: (pair[1].asset_label.lower(), pair[1].path))
    return diff


def _after_what_they_name(
    game: str, ranked: List[str], mods: Dict[str, dict], placed: set
) -> List[str]:
    """``ranked`` with each mod not in ``placed`` that says it loads after
    some of the others moved to straight after the last of them, as the
    loader puts a mod that names others.

    :func:`core.switches.order` slots a mod among ones that have a place by
    its name alone, and reads no load-after list: a mod read in on its own
    that loads after another, or a patch named to sort further on than its
    mod, landed in front of what it loads after, or with mods between.

    Who comes before whom is the loader's own sort, :func:`core.switches.order`
    without a log, not a second copy of it here: a late mod -- a ``*`` --
    loads after every mod without one and after the late mods that sort
    before it, which go by ModID; a mod without a star loads after the mods
    it names by what the loader knows them by, and gets nothing from naming
    a late one.  One naming nothing that is here stays where its name put it.
    """
    from . import switches

    star = switches.AFTER_EVERYTHING
    loader = switches.order(game, mods)
    wants: Dict[str, set] = {}
    by_key: Dict[str, List[str]] = {}
    for origin in ranked:
        meta = mods[origin]
        wants[origin] = {
            value.strip().lower() for value in switches.declared_after(meta) if value.strip()
        }
        key = switches.loader_id(game, origin, meta).lower()
        if key:
            by_key.setdefault(key, []).append(origin)
    late = {origin for origin in ranked if star in wants[origin]}
    after: Dict[str, set] = {}
    for origin in ranked:
        if origin in placed:
            continue
        if origin in late:
            after[origin] = set(loader[:loader.index(origin)])
        else:
            after[origin] = {
                other
                for key in wants[origin]
                for other in by_key.get(key, ())
                if other not in late
            }
        after[origin].discard(origin)

    out = list(ranked)
    # Again while anything moves, so a mod after one that moved moves too;
    # no more often than there are mods, so a cycle ends.
    for _ in range(len(out)):
        before = list(out)
        # Last first, so two that name the same mod keep their order.
        for origin in reversed(before):
            named = after.get(origin)
            if not named:
                continue
            out.remove(origin)
            out.insert(max(out.index(other) for other in named) + 1, origin)
        if out == before:
            break
    return out


@dataclass(eq=False)
class Profile:
    """One named set of switches over a loadout's mods: which of them are on.

    Nothing else. The mods, their changes, their edits and their options are
    the loadout's and exist once, so that a mod read in again is read in for
    every profile -- the copies the conversion used to make, one loadout per
    profile, each held its own reading, and drifted.

    ``switches`` is by origin; a mod it does not mention is off. It is what
    the profile says while another profile is chosen: the chosen one's
    switches are the layers' own :attr:`ModLayer.active`, and this map is
    only brought up to date with them when it is left (see
    :meth:`Loadout.choose_profile`), so ask :meth:`Loadout.switches_of`
    rather than read it.

    Compared by identity, not by contents: two profiles switched alike are
    still two profiles, and an undo step remembers the one it was made in
    through a rename (see :mod:`core.undo`).
    """

    name: str
    switches: Dict[str, bool] = field(default_factory=dict)
    # What the file held for it that this build doesn't read: a newer
    # Studio's fields, written back as they were (see ``_unknown``).
    extra: Optional[dict] = field(default=None, compare=False, repr=False)


@owners.schema("loadout")
@dataclass
class Loadout:
    """Everything the user has built up: mod metadata, changes, and history."""

    # The loadout's own mod, origin "", described as a loaded mod is: its
    # name, version, ModID and the rest, what it loads after and why its
    # reading is in doubt.  Always there, made or not (:meth:`has_own_mod`),
    # and never in :attr:`layers`, which are the mods read in from elsewhere;
    # :meth:`mod` and :meth:`mods` ask for any mod, this one among them.  A
    # loadout file keeps it as "own", written as a layer is, and its project
    # takes its details as a layer's project does.
    own: ModLayer = field(default_factory=own_record, metadata=owners.LOADOUT)
    # The other author names you have published under, from the settings: a
    # mod by any of them is yours as one by ``own.creator`` is
    # (:func:`.patching.by_you`).  A fact about you, not about this loadout,
    # so never saved: the window hands them over (ui.preferences).
    aliases: List[str] = field(default_factory=list, metadata=owners.MEMORY)
    source_path: str = field(default="", metadata=owners.LOADOUT)
    game: str = field(default="anno1800", metadata=owners.LOADOUT)
    # Where the loadout's own mod sits among the loaded ones, and what that
    # was worked out from -- see :meth:`_own_rank`. Never saved.
    _own_rank_memo: tuple = field(default=(), init=False, repr=False, compare=False, metadata=owners.MEMORY)
    # Where each op is in :attr:`ops`, and the list that was worked out
    # from -- see :meth:`_op_at`. Never saved.
    _op_memo: tuple = field(default=(), init=False, repr=False, compare=False, metadata=owners.MEMORY)
    # What each block of the own mod's changes was last written as, for the
    # game it was written for -- see :func:`.narrow.write`.  Never saved.
    _narrow_memo: tuple = field(default=(), init=False, repr=False, compare=False, metadata=owners.MEMORY)
    # Which mod an edit made in the editor lands in. "" is the mod being
    # authored here, which is nearly always what is wanted; naming a loaded one
    # is how you fix somebody else's mod rather than laying your own on top of
    # it. See :meth:`editing_origin`.
    editing: str = field(default="", metadata=owners.LOADOUT)
    # The loaded mod this loadout *is*, when it is one of yours read back in to
    # carry on with. Writing a mod of your own is a matter of whose mod it is,
    # not of whether it was started here: a mod you published last month and
    # opened again today is the same job as one you began this morning. It
    # stays a loaded mod -- its own folder, its author's modinfo, the ops the
    # Studio could not read -- rather than being folded into the loadout's own
    # mod, which would export it to a second folder beside the first.
    home: str = field(default="", metadata=owners.LOADOUT)
    # How this loadout was set up, as the user said on the way in: to MAKE a
    # mod to publish, to TWEAK somebody else's for yourself, or to MANAGE the
    # whole installed list. A recollection, not a mode: the click that chose
    # it wrote the fact below and set the workspace up, and nothing
    # afterwards consults it except the Start tab, which says which card was
    # taken. What you do next can carry the loadout anywhere -- loading other
    # mods to check against, patching one of them, turning a patch into a
    # mod of your own -- and every place that used to ask "which aim" asks
    # instead for the one fact it needs, which the loadout holds outright.
    aim: str = field(default="", metadata=owners.LOADOUT)
    # Whether the loadout *is* its own mod, so that naming one names the
    # other. Writing a mod of your own says yes; on a mod list the loadout is
    # a workspace whose name is deliberately not the mod's. Carrying on with a
    # mod of yours that was read back in is the same fact about that mod, and
    # is held by :attr:`home` instead -- see :meth:`stands_for`.
    is_the_mod: bool = field(default=False, metadata=owners.LOADOUT)
    # Whether the loadout's own mod -- origin "" -- has been made. It is not
    # there from the start: a loadout that has done nothing holds nothing,
    # and a "My Anno Mod" standing in it before anybody asked for one, and
    # still standing beside the patch once a downloaded mod was read in to
    # change, was a mod the Studio made up. Something the user does makes
    # it: starting a new mod, reading a mod list in to lay a patch over,
    # picking "a new mod of your own" where edits go, or simply writing
    # into it -- see :meth:`has_own_mod`. Once made it is listed wherever
    # mods are, empty or not.
    own_mod: bool = field(default=False, metadata=owners.LOADOUT)
    # The mods folder this loadout's loaded mods came from, for an aim that has
    # one. Where the whole list is the point, that folder is half of what the
    # loadout is about, and remembering it is what lets the Studio offer to put
    # the list back the way the loadout now says it should be.
    mods_folder: str = field(default="", metadata=owners.LOADOUT)
    # Which mod on mod.io this loadout publishes to, once it has been linked;
    # zero until then. Not a secret, and the one thing that lets an update be
    # sent from the Export tab rather than uploaded by hand.
    modio_mod_id: int = field(default=0, metadata=owners.RELEASE)
    # Which collection each folder the Studio installed came from: the
    # folder's bare name to the collection's name. A collection's mods land
    # as folders and become layers only when the folder is read in, so this
    # is what is known in between, and what :meth:`set_layer` reads off.
    provenance: Dict[str, str] = field(default_factory=dict, metadata=owners.LOADOUT)
    # Every collection that lists each folder's mod, whether it brought it or
    # found it already installed: the folder's bare name to the collections'
    # names. Loadouts from before this kept one name per folder, for both
    # kinds, in ``provenance``.
    listed: Dict[str, List[str]] = field(default_factory=dict, metadata=owners.LOADOUT)
    # The loadout's profiles: named sets of switches over the one mod list,
    # in the order they were made. There is always at least one, and the
    # first is DEFAULT_PROFILE until somebody renames it -- a loadout from
    # before profiles too.
    profiles: List[Profile] = field(default_factory=lambda: [Profile(DEFAULT_PROFILE)], metadata=owners.LOADOUT)
    # Which of them is chosen, by name: the one whose switches the layers
    # hold, and so the one the card compares, Play makes live and a switch
    # flipped anywhere edits. Every place that reads ``layer.active`` reads
    # it, and none of them is asked which profile.
    profile: str = field(default=DEFAULT_PROFILE, metadata=owners.LOADOUT)
    # Provenance of the base file, safe to publish: no local paths.
    base_archive: str = field(default="", metadata=owners.LOADOUT)
    base_assets: int = field(default=0, metadata=owners.LOADOUT)
    # And which bytes it was: the assets.xml's SHA-1, as the index cache keys
    # it. Exact where the archive name is only a hint, so it is what a mod's
    # modinfo records it was built against.
    base_fingerprint: str = field(default="", metadata=owners.LOADOUT)
    # Which game update that data came from, in the patch notes' words
    # ("2.1.0"), and the whole version the game logged; "" when the game's
    # log could not say. See :mod:`core.gameversion`.
    base_update: str = field(default="", metadata=owners.LOADOUT)
    base_game_version: str = field(default="", metadata=owners.LOADOUT)
    # What loaded, in order and at which settings, when the readings here were
    # last looked at: what :func:`core.stale.check` measures a change in what
    # loads against.  None until it has looked once.
    read_stack: Optional[List[List[str]]] = field(default=None, metadata=owners.LOADOUT)
    # The ``reader.READER_VERSION`` the readings here were made under, which
    # :func:`core.stale.check` compares with this Studio's: another number
    # has every mod read again.  None for a loadout made in this run, whose
    # readings are this Studio's own; 0 for a file that does not say.
    read_by: Optional[int] = field(default=None, metadata=owners.LOADOUT)
    # How many times what is written for a mod has changed since the loadout
    # was opened: a reading of the stack answers for one count of it.  Not
    # saved.
    edits: int = field(default=0, metadata=owners.MEMORY)
    # The assets the edits made to a mod here are about, by mod, for as long
    # as a reading is in doubt for them; None for a mod where that cannot be
    # said.  Which rows say they are being read again goes by it
    # (:func:`core.stale.reached`).  Not saved.
    edit_reach: Dict[str, Optional[Set[str]]] = field(default_factory=dict, metadata=owners.MEMORY)
    # The last build of each of your mods that was written out: origin ("" for
    # the one authored here) -> {"build": n, "digest": what its output hashed
    # to}. See :mod:`core.builds`.
    builds: Dict[str, dict] = field(default_factory=dict, metadata=owners.PER_MOD)
    # What was last sent to mod.io from here: {"version", "build", "when",
    # "modfile_id", "checkpoint"} -- the last being the timestamp of the
    # checkpoint taken as it went, which is what "since the last release"
    # is measured from. Empty until something is published.
    published: dict = field(default_factory=dict, metadata=owners.RELEASE)
    created_at: float = field(default_factory=time.time, metadata=owners.LOADOUT)
    changes: Dict[ChangeKey, Change] = field(default_factory=dict, metadata=owners.INSIDE)
    # The two histories (section 9 of the proposal). The loadout's own: its
    # list at each checkpoint -- see :mod:`core.histories`.
    history: List[LoadoutCheckpoint] = field(default_factory=list, metadata=owners.LOADOUT)
    # And each mod of yours', by origin: its work at each checkpoint.  A
    # project's is kept in the project file (:func:`.projects.split`); the
    # rest, your edits to a mod no project of yours keeps, in the loadout's.
    mod_history: Dict[str, List[Checkpoint]] = field(default_factory=dict, metadata=owners.PER_MOD)
    # Other people's mods, loaded alongside the one being authored.
    layers: List[ModLayer] = field(default_factory=list, metadata=owners.LOADOUT)
    # The ops kept as written, the loaded mods' and any made here. Kept beside the
    # changes rather than among them: an op is one thing that produces many
    # changes, and the two are not the same kind of object.
    ops: List[Op] = field(default_factory=list, metadata=owners.PER_MOD_AND_INSIDE)
    # The ModOps the Studio writes for the loadout's own mod: one for each
    # that a value set in Fields, or anything else done to a change there,
    # comes to.  They are that mod's record, and what is exported; its
    # changes are what each comes to, tied to it by ``Change.written_as``.
    # Apart from :attr:`ops`, which are ops as somebody wrote them: these are
    # the Studio's, rewritten whenever what they are written for moves, and
    # nothing that reads a kept op is to take one for that.  See :mod:`.narrow`.
    narrow: List[Op] = field(default_factory=list, metadata=owners.PER_MOD)
    # And the ops that could not be read at all, which are the other thing a
    # change list cannot say: they are part of what a loaded mod does and no
    # part of what the Studio knows about it.
    unread: List[Unread] = field(default_factory=list, metadata=owners.PER_MOD)
    # The ops of loaded mods that were taken out here, each as ``[origin,
    # the op as the mod's file has it, the uids of the groups it sat in]``:
    # reverted, typed out of the XML view or typed over there, changed in
    # the pattern editor, written as narrow ModOps.  The mod's file still
    # has each, so the mod read again brings it back; this is what says it
    # is not to land (:meth:`as_before`).  Forgotten with the mod when it is
    # unloaded, so loading it again starts from its file.  Each is
    # ``[origin, source, within, order]``: ``order`` is where the op stood,
    # which is where whatever was typed in its place stands too, and how an
    # update finds what of yours an op of theirs it changed takes with it
    # (:mod:`core.held`).
    taken_out: List[list] = field(default_factory=list, metadata=owners.INSIDE)
    # Edits of yours to somebody else's mod that an update left without the
    # op they were made to, held back until you say what becomes of them:
    # see :mod:`core.held`.
    held_edits: List[dict] = field(default_factory=list, metadata=owners.INSIDE)
    # The mods dropped with nothing left in them, as the Mod changes panel
    # drops one whose every change was reverted there.  What such a mod held
    # was taken out before it went, which is work to save, where a mod
    # unloaded with its changes is not -- see :meth:`taken_since`.  Not saved.
    emptied: Set[str] = field(default_factory=set, metadata=owners.MEMORY)
    # The project each mod of yours is kept in, by origin: "" for the
    # loadout's own mod, and a patch's or a carried-on mod's origin -- see
    # :mod:`core.projects` and :meth:`project_origins`.  Saved as references;
    # what each holds is in its own file.
    projects: Dict[str, project_files.Ref] = field(default_factory=dict, repr=False, metadata=owners.LOADOUT)
    # What each patch keeps inside a mod of somebody else's that this loadout
    # does not have loaded -- unloaded here, or never loaded -- by the
    # patch's origin, as its project holds it (:mod:`.inside`).  The project
    # goes on keeping it (:func:`.projects.split`), and reading the mod in
    # again puts it back (:func:`.inside.take_back`).  Not saved in the
    # loadout's file.
    kept_aside: Dict[str, dict] = field(default_factory=dict, repr=False, compare=False, metadata=owners.INSIDE)
    # What the file held at its top that this build doesn't read: a newer
    # Studio's fields, written back as they were (see ``_unknown``).
    extra: Dict[str, Any] = field(default_factory=dict, repr=False, metadata=owners.LOADOUT)

    # Its modinfo blocks (Anno 117), in its record's ``declared`` as a loaded
    # mod's are in its modinfo: what :mod:`.scripts`, :mod:`.settings` and
    # :func:`.modinfo.setup_of` read and set for any mod.  Each is held as
    # it was when it was the loadout's field, and left out while empty, so
    # that a file reads and writes back as it did.
    def _block(self, key: str, empty):
        held = self.own.declared.get(key)
        return held if isinstance(held, type(empty)) else empty

    def _set_block(self, key: str, value) -> None:
        declared = dict(self.own.declared)
        if value:
            declared[key] = value
        else:
            declared.pop(key, None)
        self.own.declared = declared

    @property
    def scripts(self) -> dict:
        """The own mod's ``Scripts`` block, as written: its modules and the
        Lua each hook runs (:mod:`core.scripts`).  Where the Lua lives is
        :attr:`ModLayer.scripts_folder`."""
        return self._block("Scripts", {})

    @scripts.setter
    def scripts(self, value: dict) -> None:
        self._set_block("Scripts", value)

    @property
    def options(self) -> dict:
        """The own mod's ``Options`` block, as written: what the mod lets its
        players decide, which its ModOps read back as ``$name``
        (:func:`core.settings.block_of`)."""
        return self._block("Options", {})

    @options.setter
    def options(self, value: dict) -> None:
        self._set_block("Options", value)
        # What it offers, as a loaded mod's is held: worked out from the
        # block whenever it is set.
        self.own.offered = settings.read(value).to_dict() if value else {}

    @property
    def difficulty(self) -> str:
        """The own mod's Difficulty: "" for the one a new mod starts with
        (:func:`core.modinfo.setup_of`)."""
        return self._block("Difficulty", "")

    @difficulty.setter
    def difficulty(self, value: str) -> None:
        self._set_block("Difficulty", value)

    @property
    def game_setup(self) -> dict:
        """Those of the own mod's GameSetup keys its author has set."""
        return self._block("GameSetup", {})

    @game_setup.setter
    def game_setup(self, value: dict) -> None:
        self._set_block("GameSetup", value)

    # -- change bookkeeping -------------------------------------------------
    def set_change(self, change: Change) -> None:
        """Record an edit, or drop it if the user typed the original back in."""
        if change.is_noop:
            self.changes.pop(change.key, None)
        else:
            self.changes[change.key] = change

    def after_last_op(self, origin: str) -> int:
        """Where an op written here goes in ``origin``'s file: after all of its.

        A mod's ops patch a document that is changing as they go, so where an
        edit made in the Studio sits among the ops read from the mod is part
        of what it does.  After them is the only place it can mean what the
        person editing saw: the asset as the mod's own ops leave it.  Most
        visibly, an edit to an asset one of the mod's patterns covers is the
        exception to that op, and written before the op it would be
        overwritten by it.

        Counted off the ops read from the mod -- its kept ops, its unread ops and
        the changes that came out of its file -- and not off edits made here,
        which all share the position this answers with.  Unless the last of
        those edits already stands after everything read, as it does once
        the mod's file has been typed in: then that is the position, and an
        edit made now joins the ones there.
        """
        read = [
            change.order
            for change in self.changes.values()
            if change.origin == origin and not change.authored
        ]
        read.extend(op.order for op in self.ops if op.origin == origin)
        read.extend(entry.order for entry in self.unread if entry.origin == origin)
        after = max(read, default=-1) + 1
        made = [
            change.order
            for change in self.changes.values()
            if change.origin == origin and change.authored and not change.op_uid
        ]
        return max(after, max(made, default=after))

    def make_room(self, origin: str, after: int) -> Dict[int, Unread]:
        """Open a place in ``origin``'s file straight after position
        ``after``: everything of the mod's that stands later moves one on,
        and ``after + 1`` is free.

        An op carried unread is known to a step of undo by which entry it
        is, so one that moves is another entry in the first one's place:
        ``{id(the entry it was): the entry it is}`` comes back for whoever
        holds one.
        """
        for op in self.ops:
            if op.origin == origin and op.order > after:
                op.order += 1
        for op in self.narrow:
            if op.origin == origin and op.order > after:
                op.order += 1
        for change in self.changes.values():
            if change.origin == origin and change.order > after:
                change.order += 1
        moved: Dict[int, Unread] = {}
        entries = []
        for entry in self.unread:
            if entry.origin == origin and entry.order > after:
                moved[id(entry)] = replace(entry, order=entry.order + 1)
                entry = moved[id(entry)]
            entries.append(entry)
        if moved:
            self.unread = entries
        return moved

    # -- which of a mod's files each op is in ---------------------------------
    def files_of(self, origin: str) -> Dict[str, str]:
        """The files ``origin`` keeps beside its patch, each by the file
        whose ``<Include>`` line names it ("" for the patch)."""
        return {op.includes: op.file for op in self.ops if op.origin == origin and op.includes}

    def file_new_things(self, origin: str, new) -> None:
        """Say which file each of ``new`` is in -- ops and ops carried
        unread just put among ``origin``'s -- by where it stands: the file
        of what is before it, and at the top of a file where that is the
        line that names one.  For what is typed where the files are not in
        front of whoever types: the XML view, which shows one asset's ops.
        """
        new = {id(thing) for thing in new}
        if not new:
            return
        things: List[Any] = sorted(
            [op for op in self.ops if op.origin == origin]
            + [entry for entry in self.unread if entry.origin == origin],
            key=lambda thing: thing.order,
        )
        before: Any = None
        for thing in things:
            if id(thing) in new:
                thing.file = (
                    "" if before is None
                    else before.includes if getattr(before, "includes", "") else before.file
                )
            before = thing

    def place(self, change: Change) -> Change:
        """Give an edit made here its position in its mod's file, and hand it back.

        An edit that retunes something already in the list keeps that thing's
        place, whether it came out of the mod's file or was made here before.
        Anything new goes after every op read from the mod.

        Typing over one of an op's answers is not retuning it.  The answer is
        what the op does to that asset and stays; the edit is the exception
        -- "all of these, except this one" -- an op of its own after that one,
        so it takes a uid to keep its key apart from the answer's.  Typing
        over the same field again finds that exception and retunes it.
        """
        prior = self.changes.get(change.key)
        if prior is not None and prior.op_uid and self._retuned_in_place(prior, change):
            return change
        if prior is not None and prior.op_uid and self.op(prior.op_uid):
            existing = self.exception_to(prior)
            if existing is not None:
                change.uid = existing.uid
                change.exception_of = existing.exception_of
                change.order = existing.order
                return change
            change.uid = uuid.uuid4().hex[:12]
            change.exception_of = prior.op_uid
            change.order = self.after_last_op(change.origin)
            if prior.sets is not None and change.sets is not None:
                # What it is typed over is what the op left, not what was
                # there before the op.
                change.old_value = prior.new_value
            return change
        change.order = (
            prior.order if prior is not None else self.after_last_op(change.origin)
        )
        return change

    def _retuned_in_place(self, prior: Change, change: Change) -> bool:
        """Retune a plain op where it stands, and say whether that was done.

        An op that is one change -- one value, at the node it names, in the
        one asset it names -- says nothing its change does not.  Typing over
        that value is retuning the op, so the op is let go of for the edit,
        which takes its place in the file and its note: a narrow change the
        Studio writes, where the op was.  No exception is put after it.
        Otherwise a mod carried on with from its folder would grow by an op
        for every value changed twice.  Typing the game's own value back
        takes the op out, since the edit is then no change at all.

        One in a group is retuned as itself: its value is typed over in the
        op, which keeps its place, its uid and the groups it sits in.  A
        narrow change has no group to go in, and written where the op was it
        would stand after the group's closing tag and run whatever the
        group's condition said.

        Any other kept op is left as its author wrote it, and the edit is the
        exception :meth:`place` makes of it.
        """
        op = self.op(prior.op_uid)
        if (
            op is None
            or not op.plain
            or change.op != OP_REPLACE
            or prior.op != OP_REPLACE
            or sum(1 for held in self.changes.values() if held.op_uid == op.uid) != 1
        ):
            return False
        if op.within and not change.is_noop:
            source = modop.typed_over(op.source, (0,), prior.new_value, change.new_value)
            if source is None:
                return False
            self.typed_into(op, source, modop.summary_typed_over(
                op.summary, prior.new_value, change.new_value
            ))
            change.op_uid = op.uid
            change.order = prior.order
            change.spot = prior.spot
            change.active = op.active
            if not change.note:
                change.note = prior.note
            return True
        change.order = prior.order
        change.active = op.active
        if not change.note:
            change.note = op.note
        self.drop_op(op.uid, with_changes=False)
        return True

    def typed_into(self, op: Op, source: str, summary: str = "") -> Op:
        """Put ``op`` back as ``source``, a value typed over in its text, and
        hand it back: in its place in the file, the groups it sits in, its
        uid and its switch, as :func:`.typedops.store_one` puts an op typed
        over in the place of the one it was.  It is yours now, and one of
        somebody else's mod is taken out of it, as any op typed over is."""
        typed = replace(op, source=source, summary=summary or op.summary, authored=True)
        self._taken_out(op)
        self.set_op(typed)
        return typed

    def exception_to(self, answer: Change) -> Optional[Change]:
        """The exception already made to one of an op's answers, if any."""
        for change in self.changes.values():
            if (
                change.exception_of == answer.op_uid
                and change.origin == answer.origin
                and change.guid == answer.guid
                and change.path == answer.path
                and change.op == answer.op
            ):
                return change
        return None

    def find(
        self, guid: str, path: str, op: str = OP_REPLACE, origin: str = ""
    ) -> Optional[Change]:
        """Look a change up by what it targets, within one mod.

        Searches rather than rebuilding a key: a structural change's identity
        includes a uid that the caller has no way to know.

        Within one mod, because this answers "is this already something *I*
        changed?" -- the question the editor asks before it writes a value.  A
        field another mod sets is not an edit of yours to update or undo; typing
        over it is a new change of your own, laid on top.
        """
        for change in self.changes.values():
            if (
                change.origin == origin
                and change.guid == guid
                and change.path == path
                and change.op == op
            ):
                return change
        return None

    def equivalent(self, change: Change) -> Optional[Change]:
        """A change already here that does the same thing as ``change``.

        A structural change carries a uid so that two additions to the same list
        stay distinct edits -- which is right when a user makes them one after
        another, and wrong when the same mod is imported twice: the same payload
        inserted at the same place is the same edit, not a second one.

        Only within one mod.  Two mods that make the same edit are still two
        changes: collapsing them would quietly hide that both of them claim it,
        which is the one thing worth knowing about a pair of mods.
        """
        for existing in self.changes.values():
            if (existing.origin, existing.guid, existing.path, existing.op) != (
                change.origin,
                change.guid,
                change.path,
                change.op,
            ):
                continue
            if not change.is_structural or existing.payload == change.payload:
                return existing
        return None

    def remove(
        self, guid: str, path: str, op: str = OP_REPLACE, origin: str = ""
    ) -> None:
        change = self.find(guid, path, op, origin)
        if change is not None:
            self.changes.pop(change.key, None)

    def remove_key(self, key) -> None:
        self.changes.pop(tuple(key), None)

    def remove_asset(self, guid: str, origin: str = "") -> None:
        """Take out everything ``origin`` does to the asset.

        An op that names this asset and no other goes with the changes it
        came to: the op is what is written.  Left behind, it was listed as
        changing nothing and went out with the mod all the same, so the asset
        was not reverted at all.  An op that names others too, or searches
        for its assets, is one thing its mod does to more than this one, and
        stays; so does a group, whose ops each answer for themselves.

        The same goes for an op the Studio could not read: it comes to no
        change to go with, is written as it stands, and where it names this
        asset alone it is something the mod does to it.
        """
        self.drop_unread(
            entry for entry in self.unread
            if entry.origin == origin and not entry.within and _names_alone(entry.source, guid, self.game)
        )
        gone = [c for c in self.changes.values() if c.guid == guid and c.origin == origin]
        for change in gone:
            self.changes.pop(change.key, None)
        still = {c.op_uid for c in self.changes.values() if c.op_uid}
        for uid in {c.op_uid for c in gone if c.op_uid} - still:
            op = self.op(uid)
            if op is not None and not op.scope and _names_alone(op.source, guid, self.game):
                self.drop_op(uid, with_changes=False)

    def clear(self) -> None:
        self.changes.clear()
        self.layers.clear()
        self.ops.clear()
        self.narrow = []
        self.unread.clear()
        self.taken_out = []
        self.held_edits = []
        self.own.stale = []

    def yours(self, layer: ModLayer) -> bool:
        """Whether a loaded mod is one of yours: a patch, a fork, a mod of
        yours made a project here, or the mod the loadout carries on with."""
        return bool(layer.patches or layer.fork_of or layer.kept) or layer.origin == self.home

    def revert_all(self) -> None:
        """Take out everything every mod here does.

        Changes, ops and the ops that could not be read, all of them: an op
        with no change under it is still written, so nothing may be left
        that the changes list no longer shows.  Somebody else's mod with
        nothing left of it is no longer loaded.  A mod of yours -- a patch,
        or the mod the loadout carries on with -- is where your edits go and
        stays, empty, as it does when its last change is reverted by itself:
        :meth:`clear` took it with the rest, and the loadout stopped being
        the mod it was.
        """
        yours = [
            # None of its ops is left unread: there is none left at all.
            replace(layer, unread=0) if layer.unread else layer
            for layer in self.layers
            if self.yours(layer)
        ]
        self.clear()
        self.layers.extend(yours)

    def editing_origin(self) -> str:
        """The mod an edit lands in, checked against what is actually loaded.

        Asked for rather than read off the field so that a loadout remembering
        a mod that is no longer open falls back instead of quietly writing
        changes into a layer that is not there: to the mod of yours the
        loadout carries on with, where that is loaded, and otherwise to the
        loadout's own mod.  Which mod was chosen is not forgotten, so undoing
        an unload points changes at the mod again.
        """
        for origin in (self.editing, self.home):
            if is_own_mod(origin):
                break
            if self.layer(origin) is not None and not self.held(origin):
                return origin
        return ""

    def project_origins(self) -> List[str]:
        """The mods of this loadout that are kept in projects: its own mod,
        once it is made, each patch, each fork, each mod of yours made a
        project here, and the mod of yours it carries on with.  Everybody
        else's mod is the library's."""
        origins = [""] if self.has_own_mod() else []
        origins += [layer.origin for layer in self.layers if self.yours(layer)]
        return origins

    def projects_to_save(self) -> List[str]:
        """The names of the projects a save would write: those whose part
        has moved since it was read or written, and those not made yet."""
        parts = project_files.split(self.to_dict(), self.project_origins(), self.kept_aside)
        return [
            project_files.name_of(part, origin) for origin, part in parts.items()
            if origin not in self.projects
            or self.projects[origin].digest != project_files.digest(part)
        ]

    def held_projects(self) -> List[Tuple[str, str, str]]:
        """Each project that cannot be written from here, as ``(origin, why,
        where)``: why is :data:`.projects.MOVED`, ``MISSING`` or
        ``UNREADABLE``, and where is the file."""
        return [
            (origin, ref.held, str(project_files.where(ref.path)))
            for origin, ref in self.projects.items() if ref.held
        ]

    def own_project_anew(self) -> None:
        """Let go of the project the loadout's own mod is kept in, so the
        next save makes one of its own.  For a loadout about to be written
        as another file -- saved as, forked, copied -- whose own mod is
        that loadout's alone (section 5 of the proposal), where the file it
        came from goes on referring to the first."""
        self.projects.pop("", None)

    def own_mod_anew(self, loadout_name: str, came_from: str = "") -> dict:
        """Make the loadout's own mod a mod of its own, for a loadout about to
        be written as another file that the one it came from goes on having:
        a fork, a copy of the live loadout.  What it was, for
        :meth:`own_mod_back` where writing the copy fails.

        A loadout's own mod is used by that loadout alone (section 5 of the
        projects and loadouts proposal), so the copy's is another mod: a
        project of its own (:meth:`own_project_anew`), a name and a ModID of
        its own -- where it builds and what the game knows it by, so the two
        are not one mod built by two projects -- and none of the first one's
        builds, mod.io page or record of what was published, which are that
        mod's.  Its name is the copy's, ``loadout_name``, where the loadout
        stands for its own mod; the copy's patch name where it was named after
        the loadout it ``came_from`` (:func:`list_patch_name`); and its old
        name with the copy's after it otherwise.  Its ModID is made out of
        that name (:func:`.modinfo.own_mod_id`).
        """
        was = {
            "project": self.projects.get(""), "mod_name": self.own.name, "mod_id": self.own.mod_id,
            "build": self.builds.get(""), "modio_mod_id": self.modio_mod_id,
            "published": dict(self.published),
        }
        self.own_project_anew()
        if not self.has_own_mod():
            return was
        if self.stands_for() == "":
            self.own.name = loadout_name
        elif came_from and self.own.name == list_patch_name(came_from):
            self.own.name = list_patch_name(loadout_name)
        else:
            self.own.name = "%s (%s)" % (self.own.name, loadout_name)
        self.own.mod_id = ""
        self.builds.pop("", None)
        if not self.home:
            # The mod.io page and what was published are the own mod's where
            # no carried-on mod's are: the first mod's, not the copy's.
            self.modio_mod_id = 0
            self.published = {}
        return was

    def own_mod_back(self, was: dict) -> None:
        """Undo :meth:`own_mod_anew`, with what it said the own mod was."""
        if was.get("project") is not None:
            self.projects[""] = was["project"]
        self.own.name, self.own.mod_id = was["mod_name"], was["mod_id"]
        if was.get("build") is not None:
            self.builds[""] = was["build"]
        self.modio_mod_id, self.published = was["modio_mod_id"], was["published"]

    def held(self, origin: str) -> str:
        """Why the project of ``origin`` cannot be written from here, or ""
        where it can: changed by another loadout since this one read it,
        missing, or unreadable (:attr:`.projects.Ref.held`)."""
        ref = self.projects.get(origin)
        return ref.held if ref is not None else ""

    def home_layer(self) -> Optional[ModLayer]:
        """The loaded mod this loadout is, if it is one -- see :attr:`home`.

        ``None`` when that mod is not loaded, without forgetting it: unloading
        it and then undoing the unload should find the loadout as it was.
        """
        return self.layer(self.home)

    def set_up_as(self, aim: str) -> None:
        """Write the facts the Start tab's card for ``aim`` sets a loadout up with.

        This is all a card does to the loadout itself; the rest of a click is
        reading things in.  Kept in one place so that a saved loadout from
        before these facts were written, and a test standing in for the
        click, set up exactly what the click would have.
        """
        self.aim = aim
        self.is_the_mod = aim == AIM_MAKE
        self.own.loads_last = aim == AIM_MANAGE

    def make_own_mod(self) -> None:
        """Bring the loadout's own mod into being -- see :attr:`own_mod`."""
        self.own_mod = True

    def has_own_mod(self) -> bool:
        """Whether the loadout's own mod exists: made, or written into.

        Not a rule about when to *show* it. Whatever exists is listed,
        empty or not; this is only whether there is anything to list.
        """
        return (
            self.own_mod
            or any(is_own_mod(change.origin) for change in self.changes.values())
            or any(is_own_mod(op.origin) for op in self.ops)
        )

    def stands_for(self) -> Optional[str]:
        """The origin of the mod this loadout *is*, or ``None`` for a workspace.

        Naming that mod and naming the loadout are the same act.  It is the
        mod of yours the loadout carries on with when there is one, and the
        loadout's own mod when the loadout was set up as writing it; on a mod
        list, or changing somebody else's mod, the loadout is a workspace and
        its name says nothing about the mods inside it.
        """
        if self.home:
            return self.home
        return "" if self.is_the_mod else None

    def unread_of(self, origin: str) -> List[Unread]:
        return [entry for entry in self.unread if entry.origin == origin]

    def not_read(self, origin: Optional[str] = None) -> List[Unread]:
        """The ops that could not be read, of ``origin`` or of every mod:
        :attr:`unread` without what is only carried -- see
        :attr:`Unread.markup`."""
        return [
            entry for entry in self.unread
            if not entry.markup and (origin is None or entry.origin == origin)
        ]

    # -- ops -----------------------------------------------------------------
    def _op_at(self, uid: str) -> int:
        """Where the op with this uid is in :attr:`ops`, or -1.

        Asked once for every change an op produced each time the Mod changes
        panel is drawn, so it is looked up and not searched for: walking the
        list was most of a redraw once a loadout held a few thousand ops.

        :attr:`ops` is a plain list that anything may append to or assign,
        so what is remembered is never taken on trust.  An answer is checked
        against the list as it is now, and the positions are worked out again
        when the list is another one or another length.
        """
        ops = self.ops
        memo = self._op_memo
        if memo and memo[0] is ops and memo[1] == len(ops):
            position = memo[2].get(uid, -1)
            if position < 0 or ops[position].uid == uid:
                return position
        where: Dict[str, int] = {}
        for position, stored in enumerate(ops):
            where.setdefault(stored.uid, position)
        self._op_memo = (ops, len(ops), where)
        return where.get(uid, -1)

    def op(self, uid: str) -> Optional[Op]:
        position = self._op_at(uid)
        return self.ops[position] if position >= 0 else None

    def set_op(self, op: Op) -> None:
        position = self._op_at(op.uid)
        if position >= 0:
            self.ops[position] = op
            return
        # _op_at has just made what is remembered true of the list as it
        # is, so the one added here is added to it: working the positions out
        # again for every op of a loadout being opened is the square of them.
        ops, _length, where = self._op_memo
        where[op.uid] = len(ops)
        ops.append(op)
        self._op_memo = (ops, len(ops), where)

    def ops_of(self, origin: str) -> List[Op]:
        return [op for op in self.ops if op.origin == origin]

    def as_before(self, ops: List[Op], changes=(), unread=()) -> int:
        """Give ops read again the uids they have here already; how many had.

        What :meth:`equivalent` is to a change.  A mod imported a second time
        brings each op again under a new uid, and set beside the first it is
        the mod's op twice, written out twice.  An op is the one here already
        where it is the same mod's, says the same and sits in the same
        groups; each one here is matched once, so an op a mod really does
        write twice stays two.  ``changes`` and ``unread`` are what came with
        ``ops``, and follow them.

        And an op that was taken out here (:attr:`taken_out`) is taken out
        of ``ops`` or ``unread``, with what it came to out of ``changes``:
        the mod's file still has it, and landed again it undid the revert,
        or stood beside the op typed over it and was written out after it.
        Each taking out answers for one op, so an op the mod writes twice
        that was taken out once comes back once.

        A group taken out is in the records of the ops that were inside it
        only by its uid, which no op here has any more: the group read again
        is taken to be the one under that uid, or its ops, matched against
        their records by a uid of its own, came back outside any group.
        """
        here: Dict[tuple, List[str]] = {}
        for held in self.ops:
            here.setdefault((held.origin, held.source, tuple(held.within)), []).append(held.uid)
        taken: Dict[tuple, int] = {}
        for origin, source, within, *_order in self.taken_out:
            key = (origin, source, tuple(within))
            taken[key] = taken.get(key, 0) + 1
        now: Dict[str, str] = {}
        # A group of ``ops`` taken out -> the uid it had here.
        was: Dict[str, str] = {}

        def uid_here(uid: str) -> str:
            return now.get(uid) or was.get(uid, uid)

        def group_was(op: Op, within: tuple) -> None:
            """Have ``op``, a group taken out, stand for the gone group
            whose ops' records say most nearly what is inside it."""
            alive = {held.uid for held in self.ops}
            depth = len(within)
            inside: Dict[str, List[str]] = {}
            for origin, source, held_within in (
                *((entry[0], entry[1], tuple(entry[2])) for entry in self.taken_out),
                *((held.origin, held.source, tuple(held.within)) for held in self.ops),
            ):
                if (origin == op.origin and len(held_within) > depth
                        and held_within[:depth] == within):
                    uid = held_within[depth]
                    if uid not in alive and uid not in was.values():
                        inside.setdefault(uid, []).append(source)
            if not inside:
                return
            read = sorted(other.source for other in ops if other.within == [*op.within, op.uid])
            was[op.uid] = max(inside, key=lambda uid: sorted(inside[uid]) == read)

        groups = {uid for op in ops for uid in op.within}
        gone = set()
        for op in ops:
            within = tuple(uid_here(uid) for uid in op.within)
            key = (op.origin, op.source, within)
            waiting = here.get(key)
            if waiting and op.uid not in waiting:
                now[op.uid] = waiting.pop(0)
            elif not waiting and taken.get(key):
                taken[key] -= 1
                gone.add(op.uid)
                if op.uid in groups:
                    group_was(op, within)
        if gone:
            ops[:] = [op for op in ops if op.uid not in gone]
            if isinstance(changes, list):
                changes[:] = [change for change in changes if change.op_uid not in gone]
        for op in ops:
            op.uid = now.get(op.uid, op.uid)
            op.within = [uid_here(uid) for uid in op.within]
        for change in changes:
            change.op_uid = now.get(change.op_uid, change.op_uid)
        for entry in unread:
            entry.within = [uid_here(uid) for uid in entry.within]
        if taken and isinstance(unread, list):
            left = []
            for entry in unread:
                key = (entry.origin, entry.source, tuple(entry.within))
                if taken.get(key):
                    taken[key] -= 1
                else:
                    left.append(entry)
            unread[:] = left
        return len(now)

    def count_unread(self, origins=None) -> None:
        """Have each loaded mod, or each of ``origins``, say how many of its
        ops could not be read as the loadout holds them now."""
        held: Dict[str, int] = {}
        for entry in self.not_read():
            held[entry.origin] = held.get(entry.origin, 0) + 1
        for layer in self.layers:
            if origins is None or layer.origin in origins:
                layer.unread = held.get(layer.origin, 0)

    def _taken_out(self, held) -> None:
        """Remember that ``held``, an op or one that could not be read, was
        taken out here, where it is a loaded mod's own and so still in that
        mod's file.  One typed here is in no file to come back from."""
        if not is_own_mod(held.origin) and held.source and not getattr(held, "authored", False):
            self.taken_out = [
                *self.taken_out,
                [held.origin, held.source, list(held.within), int(getattr(held, "order", 0) or 0)],
            ]

    def drop_unread(self, entries) -> None:
        """Take ops that could not be read out of their mods, for good:
        see :meth:`drop_op`."""
        entries = list(entries)
        if not entries:
            return
        for entry in entries:
            self._taken_out(entry)
        self.unread = [entry for entry in self.unread if entry not in entries]

    def drop_op(self, uid: str, with_changes: bool = True) -> int:
        """Forget an op, and by default everything it produced.

        For good: a loaded mod's own op is still in the mod's file, and is
        remembered as taken out so that the mod read again does not bring
        it back (:attr:`taken_out`).
        """
        op = self.op(uid)
        if op is not None:
            self._taken_out(op)
        gone = 0
        if with_changes:
            for change in [c for c in self.changes.values() if c.op_uid == uid]:
                self.changes.pop(change.key, None)
                gone += 1
        self.ops = [op for op in self.ops if op.uid != uid]
        return gone

    def drop_ops(self, uids) -> None:
        """:meth:`drop_op` for several at once, with everything they produced:
        one pass over the loadout, where one at a time is a pass apiece."""
        uids = set(uids)
        if not uids:
            return
        for op in self.ops:
            if op.uid in uids:
                self._taken_out(op)
        for change in [c for c in self.changes.values() if c.op_uid in uids]:
            self.changes.pop(change.key, None)
        self.ops = [op for op in self.ops if op.uid not in uids]

    def changes_by_op(self, uid: str) -> List[Change]:
        return [change for change in self.ordered() if change.op_uid == uid]

    # -- the mods loaded alongside this one ---------------------------------
    def layer(self, origin: str) -> Optional[ModLayer]:
        for layer in self.layers:
            if layer.origin == origin:
                return layer
        return None

    def mod(self, origin: str) -> Optional[ModLayer]:
        """The record of the mod at ``origin``, whichever mod it is: the own
        mod's (:attr:`own`) for "", a loaded one's otherwise, and None for
        a mod not loaded here.  Unlike :meth:`layer`, which is the loaded
        mods alone."""
        return self.own if is_own_mod(origin) else self.layer(origin)

    def __copy__(self) -> Loadout:
        """A shallow copy, as :func:`copy.copy` makes one, with a record of
        the own mod of its own: its details were the loadout's own fields,
        which a copy never shared, and a view made so sets them without
        touching the loadout it was made from."""
        twin = self.__class__.__new__(self.__class__)
        twin.__dict__.update(self.__dict__)
        twin.own = copy.copy(self.own)
        return twin

    def label_of(self, origin: str) -> str:
        """What the mod at ``origin`` is called here: its record's
        :attr:`~ModLayer.label`, and a mod not loaded here by its origin.
        "" for an own mod nobody has named, which each place that shows it
        says its own way."""
        record = self.mod(origin)
        return record.label if record is not None else origin

    def mods(self) -> List[ModLayer]:
        """Every mod's record, the own mod's among them where it is made
        (:meth:`has_own_mod`), in load order (:meth:`layer_rank`)."""
        records = [*self.layers, self.own] if self.has_own_mod() else list(self.layers)
        return sorted(records, key=lambda record: self.layer_rank(record.origin))

    def host_layer(self, origin: str) -> Optional[ModLayer]:
        """The loaded mod that ``origin`` came bundled inside, if any -- the
        nearest one, for a mod inside a mod inside a mod.

        Not the folder straight above it, which need not be a mod at all: a
        mod bundled two folders down is inside the mod above that folder.
        """
        best = None
        for layer in self.layers:
            if (
                layer.origin and origin.startswith(layer.origin + "/")
                and (best is None or len(layer.origin) > len(best.origin))
            ):
                best = layer
        return best

    def set_layer(self, layer: ModLayer) -> None:
        """Record a mod, replacing what was known about it before."""
        if not layer.from_collection and self.provenance:
            top = layer.origin.split("/")[0]
            layer.from_collection = self.provenance.get(top, "")
        for position, existing in enumerate(self.layers):
            if existing.origin == layer.origin:
                self.layers[position] = layer
                break
        else:
            self.layers.append(layer)
        self.renumber_layers()

    def renumber_layers(self) -> None:
        """Put the loaded mods back in the order the game would load them.

        Which is *not* simply folder-name order, and is not even the same
        question for the two games -- see :func:`core.switches.order`.  A mod that
        already has a place keeps it, because that place came from reading the
        whole folder (or from the loader's own log) and is better than anything
        rederivable from one layer.  A mod arriving without one -- imported on
        its own, or a patch just created -- is slotted in beside the mod it
        would sort next to, rather than landing on top of everything -- or,
        where it says it loads after one of them, straight after that one.
        """
        from . import switches

        anchored = [
            layer.origin
            for layer in sorted(self.layers, key=lambda layer: layer.order)
            if layer.order
        ]
        mods = self._as_ordered()
        ranked = switches.order(self.game, mods, logged=anchored)
        if anchored:
            ranked = _after_what_they_name(self.game, ranked, mods, set(anchored))
        at = {origin: index for index, origin in enumerate(ranked)}
        self.layers.sort(key=lambda layer: at.get(layer.origin, 0))
        for position, layer in enumerate(self.layers, start=1):
            layer.order = position

    def _as_ordered(self) -> Dict[str, dict]:
        """The loaded mods as :func:`core.switches.order` takes them: by
        origin, what each is sorted by and what it loads after."""
        return {
            layer.origin: {
                "category": layer.category,
                "mod_name": layer.name,
                "mod_id": layer.mod_id,
                "load_after": self.load_after_of(layer),
            }
            for layer in self.layers
        }

    def drop_layer(self, origin: str, keep_authored: bool = False) -> int:
        """Forget a mod: its record and every change it brought.

        ``keep_authored`` leaves behind the changes *you* made to it, which is
        what reading the mods folder again wants -- those exist nowhere but
        here, and re-reading the folder cannot produce them a second time.
        """
        if is_own_mod(origin):
            return 0
        if not keep_authored:
            # What your patches keep inside it is theirs, not this
            # loadout's to forget: set aside before anything of it goes.
            self.set_aside_kept(origin)
        if any(c.origin == origin for c in self.changes.values()) or any(
            op.origin == origin for op in self.ops
        ):
            self.emptied.discard(origin)
        else:
            self.emptied.add(origin)
        gone = [
            c
            for c in self.changes.values()
            if c.origin == origin and not (keep_authored and c.authored)
        ]
        for change in gone:
            self.changes.pop(change.key, None)
        self.layers = [layer for layer in self.layers if layer.origin != origin]
        self.ops = [
            op for op in self.ops if op.origin != origin or (keep_authored and op.authored)
        ]
        self.unread = [entry for entry in self.unread if entry.origin != origin]
        if not keep_authored:
            # The mod is forgotten, and what was taken out of it with it.
            self.taken_out = [taken for taken in self.taken_out if taken[0] != origin]
            self.held_edits = [held for held in self.held_edits if held.get("origin") != origin]
            self._let_go_of_kept(origin)
        return len(gone)

    def set_aside_kept(self, origin: str) -> None:
        """Before somebody else's mod ``origin`` is unloaded: set aside what
        each patch keeps inside it (:mod:`.inside`), as its project holds
        it, so the next save writes it into the project as before
        (:attr:`kept_aside`) and reading the mod in again puts it back
        (:func:`.inside.take_back`)."""
        aside = self.kept_aside
        # Unloaded once already and taken back by an undo: what was set
        # aside then is what is here now, so it is set aside once.
        for keeper in list(aside):
            for key in list(aside[keeper]):
                aside[keeper][key] = [
                    entry for entry in aside[keeper][key] if project_files.target_of(entry) != origin
                ]
                if not aside[keeper][key]:
                    del aside[keeper][key]
            if not aside[keeper]:
                del aside[keeper]

        def add(keeper: str, key: str, entry: Any) -> None:
            aside.setdefault(keeper, {}).setdefault(key, []).append(entry)

        for at, op in enumerate(self.ops):
            if op.origin != origin or not op.kept_by:
                continue
            if op.authored:
                add(op.kept_by, "ops", {**op.to_dict(), "at": at,
                                        **project_files.placed_in(self.ops, at)})
            elif not op.active:
                add(op.kept_by, "off", {"origin": origin, "uid": op.uid, "source": op.source})
        for change in self.changes.values():
            if change.origin == origin and change.kept_by:
                add(change.kept_by, "changes", change_to_dict(change))
        for entry in self.taken_out:
            if entry[0] == origin and len(entry) > 4 and isinstance(entry[4], str) and entry[4]:
                add(entry[4], "taken_out", copy.deepcopy(list(entry)))
        for held in self.held_edits:
            whose = held.get("kept_by")
            if held.get("origin") == origin and isinstance(whose, str) and whose:
                add(whose, "held_edits", copy.deepcopy(held))

    def _let_go_of_kept(self, keeper: str) -> None:
        """Undo, here, the edits ``keeper`` -- a patch unloaded -- kept inside
        somebody else's ops (:mod:`.inside`): its ops among theirs go, their
        ops it switched off are on again, and the ones it took out are back
        where they stood.  Its project keeps them, for the loadouts that
        still have it."""
        if keeper:
            self.undo_kept(keeper)

    def undo_kept(self, keeper: str) -> None:
        """Undo, here, every edit ``keeper`` -- a mod of yours, ``""`` for
        the loadout's own -- keeps inside somebody else's ops, and forget
        what it set aside: what :meth:`_let_go_of_kept` does to a patch
        unloaded, and a restore of its history before it puts back the
        checkpoint's (:func:`.inside.restore`)."""
        self.kept_aside.pop(keeper, None)
        gone = {op.uid for op in self.ops if op.kept_by == keeper and op.authored}
        ops = []
        for op in self.ops:
            if op.uid in gone:
                continue
            if op.kept_by == keeper:
                op = replace(op, active=True, kept_by=None)
                for change in self.changes.values():
                    if change.op_uid == op.uid:
                        change.active = True
            ops.append(op)
        taken = []
        for entry in self.taken_out:
            if len(entry) > 4 and entry[4] == keeper:
                ops.append(Op(source=entry[1], origin=entry[0], within=list(entry[2]),
                              order=int(entry[3] or 0)))
                continue
            taken.append(entry)
        self.ops = ops
        self.taken_out = taken
        for change in [c for c in self.changes.values()
                       if c.kept_by == keeper or c.op_uid in gone]:
            self.changes.pop(change.key, None)
        self.held_edits = [held for held in self.held_edits if held.get("kept_by") != keeper]

    def patch_for(self, origin: str) -> Optional[ModLayer]:
        """The mod that exists to change ``origin``, if one has been made.
        None for the mod being written: a mod that patches nothing says so
        with the same empty name, and is not a patch of yours."""
        if is_own_mod(origin):
            return None
        for layer in self.layers:
            if layer.patches == origin:
                return layer
        return None

    def own_load_after(self) -> List[str]:
        """What this loadout's own mod declares it loads after.

        Everything, on a loadout: its own mod is the patch for the whole list,
        and only loads after the list if it says so.  Left to its name it is
        sorted in among the others -- ``[Gameplay] Anno 1800 loadout patch``
        in front of every ``[Gameplay]`` mod from B onwards -- and loses each
        field it was written to win.  See :func:`core.switches.last_of_all`.

        Nothing otherwise: a mod written to publish has no business loading
        after mods it has never heard of.
        """
        if not self.own.loads_last:
            return []
        from . import switches

        return switches.last_of_all(
            self.game,
            {
                layer.origin: {"mod_id": layer.mod_id, "load_after": self.load_after_of(layer)}
                for layer in self.layers
            },
        )

    def load_after_of(self, layer: ModLayer) -> List[str]:
        """What a loaded mod loads after, as its modinfo says once written.

        Its own list -- and for a patch the mod it patches, with the star
        where that mod has one: "a mod without a ``*`` cannot load after a mod
        with a ``*``", so a patch that named a late mod and nothing else would
        be sorted into the phase before it and run first.
        """
        from . import switches

        named = [str(value) for value in layer.load_after]
        late = switches.AFTER_EVERYTHING
        for origin in self.needed(layer):
            host = self.layer(origin)
            if host is None:
                continue
            key = host.mod_id or host.origin
            if key.strip().lower() not in {value.strip().lower() for value in named}:
                named.append(key)
            if late in (str(value).strip() for value in host.load_after) and late not in (
                value.strip() for value in named
            ):
                named.insert(0, late)
        return named

    @staticmethod
    def needed(layer: ModLayer) -> List[str]:
        """Every mod a patch needs, by origin: the one it was made for first,
        then the rest.  Nothing for a mod that is not a patch."""
        if not layer.patches:
            return []
        return [layer.patches, *(origin for origin in layer.needs if origin != layer.patches)]

    def patches_needing(self, origin: str) -> List[ModLayer]:
        """The patches that need ``origin`` besides the one made for it."""
        return [layer for layer in self.layers if layer.patches and origin in layer.needs
                and layer.patches != origin]

    def own_layer(self) -> ModLayer:
        """This loadout's own mod, described the same way an imported one is:
        a copy of :attr:`own`, with where it loads and what it offers worked
        out -- for what wants the mod as it stands now and those worked out
        with it (an export, a reading of the stack).  :attr:`own` itself, or
        :meth:`mod`, for the record."""
        layer = copy.deepcopy(self.own)
        layer.name = layer.name or "My Anno Mod"
        layer.order = len(self.layers) + 1
        layer.load_after = self.own_load_after()
        # What it offers, as a loaded mod's is held: a reading of the stack
        # reads its ops at the defaults.  Worked out again, since a block
        # changed in place says nothing.
        layer.offered = settings.read(self.options).to_dict() if self.options else {}
        # And no modinfo as written: the Studio writes this one, and
        # whatever reads a layer's ``declared`` takes it for the author's.
        layer.declared = {}
        return layer

    def layer_rank(self, origin: str) -> Tuple[int, str]:
        """Where a mod sits in load order.

        A loadout's own mod goes last: it declares that it loads after
        everything (:meth:`own_load_after`). Any other loadout's own mod goes
        where the game would sort it among the mods open beside it, which is
        the order they will load in once it is published -- see
        :meth:`_own_rank`.
        """
        if is_own_mod(origin):
            return self._own_rank()
        layer = self.layer(origin)
        return (layer.order if layer is not None else 0, origin.lower())

    # Sorts after any origin at the same place in load order.
    _AFTER_THAT_MOD = "\U0010ffff"

    def _own_rank(self) -> Tuple[int, str]:
        """Where the loadout's own mod loads among the loaded ones.

        Last on a loadout, and with nothing loaded. Otherwise straight after
        the mod the loader's own sort puts in front of it
        (:func:`core.switches.order`, with no log: no log has this mod in it),
        or in front of them all. It used to be last whatever the loadout, which
        for a mod written to publish promised a load order nothing in its
        modinfo asks for: ``[Gameplay] My Mod`` showed as overriding a
        ``[Gameplay] Other Mod`` that in game loads after it and wins.
        """
        last = (1 << 30, "")
        if self.own.loads_last or not self.layers:
            return last
        told = (
            self.game, self.own.category, self.own.name, self.own.mod_id,
            tuple(
                (layer.origin, layer.order, layer.mod_id, layer.name, layer.category,
                 layer.patches, tuple(layer.needs), tuple(layer.load_after))
                for layer in self.layers
            ),
        )
        if self._own_rank_memo and self._own_rank_memo[0] == told:
            return self._own_rank_memo[1]
        from . import switches

        # The folder an export gives it: ``generator.mod_folder_name``.
        category = (self.own.category or "").strip() or "Gameplay"
        name = self.own.name or "My Anno Mod"
        own = disk.safe_folder_name("[%s] %s" % (category, name), "[Mod] Anno Mod")
        there = self.layer(own)
        if there is not None:
            # Built, and read back in beside the loadout it was built from.
            rank = (there.order, self._AFTER_THAT_MOD)
        else:
            mods = self._as_ordered()
            mods[own] ={"category": category, "mod_name": name, "mod_id": self.own.mod_id}
            ranked = switches.order(self.game, mods)
            at = ranked.index(own)
            before = self.layer(ranked[at - 1]) if at else None
            rank = (before.order, self._AFTER_THAT_MOD) if before is not None else (0, "")
        self._own_rank_memo = (told, rank)
        return rank

    def origins(self, loaded: bool = False) -> List[str]:
        """Every mod with changes here, in load order.

        ``loaded`` adds the mods that are open but have changed nothing yet --
        one whose ops could all not be read, or a patch mod the moment it is
        made.  They are still mods in this loadout, with a load order and
        something that can be done to them, so anything listing the mods rather
        than the changes wants them.  Origin ``""`` is never added: an empty
        heading for the mod being written says nothing.
        """
        found = {change.origin for change in self.changes.values()}
        if loaded:
            found |= {layer.origin for layer in self.layers}
        # layer_rank's answer for each, with the layers gone through once
        # and not once for every mod sorted.
        order: Dict[str, int] = {}
        for layer in self.layers:
            order.setdefault(layer.origin, layer.order)

        def rank(origin: str) -> Tuple[int, str]:
            return (order.get(origin, 0), origin.lower()) if not is_own_mod(origin) else self._own_rank()

        return sorted(found, key=rank)

    def changes_of(self, origin: str) -> List[Change]:
        return self.ordered(c for c in self.changes.values() if c.origin == origin)

    def authored(self) -> List[Change]:
        """The changes made here, as opposed to read out of a mod folder.

        These are the ones a loadout is really about.  Opening a mods folder
        lands thousands that already exist on disk; what you did to them is a
        dozen, and every question about unsaved work, history and what to keep
        when the folder is read again is about those.
        """
        return self.ordered(self._made_here())

    def _made_here(self) -> List[Change]:
        """Every change that is work done here: one made by hand, or one an
        op made here comes to.

        The op's word is taken for its changes.  They are marked as it is
        where it is made, but each way of making an op has to remember to,
        and composing a pattern did not: its changes were in the list and in
        no count of yours, which is how one loadout came to be 22 changes in
        one place and 18 in another.
        """
        mine = {op.uid for op in self.ops if op.authored}
        return [
            change for change in self.changes.values()
            if change.authored or (change.op_uid and change.op_uid in mine)
        ]

    @property
    def authored_count(self) -> int:
        """How much work was done here, for asking whether there is any.

        Not a number of changes, and not to be shown as one: an op typed
        here is work too, and may come to no change at all, so it is counted
        beside the changes it does come to.  The title bar said this number
        was the loadout's changes, and ran ahead of the changes list by one
        for every op typed.  What is shown is :attr:`changes_made`.
        """
        return self.changes_made + sum(1 for op in self.ops if op.authored)

    @property
    def changes_made(self) -> int:
        """How many changes were made here: what "N changes" counts wherever
        it is said of your own work -- the title bar, the question before
        unsaved work is left, a checkpoint.  The same things the changes
        list counts, less the ones read out of a mod's folder: with no mod
        loaded beside yours the two numbers are one."""
        return len(self._made_here())

    def typed_details(self) -> tuple:
        """The things about this loadout somebody sat and typed.

        The change set is one kind of work and this is the other: the mod's
        name, what it says it is, who it is by, which of your mods an edit
        lands in, and the mod.io mods its layers have been tied to.  All of it
        is written to the loadout file and none of it is a change, so
        ``authored_count`` says nothing about whether any of it has been saved
        -- which is how editing a mod's description and closing the window
        used to lose the description without a word.  See
        ``MainWindow.unsaved_work``.

        And which mods are on, in every profile. A profile is nothing but
        its switches, so a switch flipped is as much somebody deciding as a
        profile renamed; switched and switched back is nothing.

        Deliberately not everything ``to_dict`` writes.  What was *read* off
        disk -- which mods folder, which layers, their changes and unread ops
        -- is on disk already and can be read again, so it is not work in this
        sense.  Compare two of these with :meth:`details_moved`, which knows
        that a layer appearing is a mod being read and a layer *changing* is
        somebody having said something about it.

        In three kinds, since the question on the way out can say one of
        them and not the others.  What the Export tab calls a mod's details,
        typed there and gone with a Discard.  Which mods are on.  And what
        follows from doing something else, such as which mod an edit lands
        in, which moves whenever a change is made in another mod: said
        beside that change, it was on most questions and told nobody
        anything.  See :meth:`mod_details_moved`.
        """
        switches = [self.switches_of(name) for name in self.profile_names()]
        return (
            # The loadout's own mod, as its modinfo will have it.
            (
                self.own.name,
                self.own.creator,
                self.own.description,
                self.own.category,
                self.own.version,
                self.own.mod_id,
                self.modio_mod_id,
                # The Lua its own mod lists, and where that Lua lives.
                json.dumps(self.scripts, sort_keys=True),
                self.own.scripts_folder,
                # And the options it offers.
                json.dumps(self.options, sort_keys=True),
                self.difficulty,
                json.dumps(self.game_setup, sort_keys=True),
            ),
            # Each loaded mod: its details, and then its switches.
            {
                layer.origin: (
                    (
                        layer.name, layer.modio_id,
                        # A loaded mod's Scripts block, Options, Difficulty
                        # and GameSetup, edited on the Export tab.
                        json.dumps(
                            {k: v for k, v in (layer.declared or {}).items()
                             if isinstance(k, str)
                             and k.lower() in ("scripts", "options", "difficulty", "gamesetup")},
                            sort_keys=True,
                        ),
                        # What the Export tab's other fields say of a mod of
                        # yours.  Typed there they are on the mod and nowhere
                        # else: a description typed for the mod a loadout
                        # carries on with was no detail at all, and went
                        # with the window without a word.
                        *((layer.version, layer.creator, layer.mod_id, layer.category,
                           layer.description) if self.yours(layer) else ()),
                    ),
                    tuple(on[layer.origin] for on in switches),
                )
                for layer in self.layers
            },
            # A loadout's profiles by name, and which is chosen: made,
            # renamed, deleted or chosen, each is somebody deciding.
            (tuple(self.profile_names()), self.chosen_profile().name),
            # What follows from something else that was done.
            (self.own.loads_last, self.own.guid_range, self.editing, self.home, self.is_the_mod),
        )

    def _details_moved(self, before) -> tuple:
        """Which kinds of :meth:`typed_details` have changed since
        ``before``: a mod's details, which mods are on, the rest.

        Layers are compared only where both sides have them.  Reading a mods
        folder adds sixty of them and is not work; learning one's mod.io id,
        or renaming the mod a loadout carries on with, changes one that was
        already there and is.
        """
        if before is None:
            return (False, False, False)
        was_mod, was_layers, was_profiles, was_rest = before
        mod, layers, profiles, rest = self.typed_details()
        both = set(layers) & set(was_layers)
        return (
            mod != was_mod or any(layers[o][0] != was_layers[o][0] for o in both),
            profiles != was_profiles or any(layers[o][1] != was_layers[o][1] for o in both),
            rest != was_rest,
        )

    def details_moved(self, before) -> bool:
        """Whether :meth:`typed_details` has changed since ``before``, in
        any of its kinds: whether there is something of it to save."""
        return any(self._details_moved(before))

    def mod_details_moved(self, before) -> bool:
        """Whether a mod's details have changed since ``before``, a
        :meth:`typed_details`: its name, version, creator, category and
        description, its mod.io link, its scripts and game setup.  The
        loadout's own mod or one it has loaded."""
        return self._details_moved(before)[0]

    def switches_moved(self, before) -> bool:
        """Whether which mods are on, in any profile, or the profiles
        themselves have changed since ``before``, a :meth:`typed_details`."""
        return self._details_moved(before)[1]

    @staticmethod
    def details_named(before, origin: str, name: str):
        """``before`` -- a :meth:`typed_details` -- with the mod at ``origin``
        called ``name``: what was saved, once :func:`name_in_file` has
        written a rename into the file by itself."""
        if before is None:
            return None
        mod, layers, profiles, rest = before
        if is_own_mod(origin):
            return ((name, *mod[1:]), layers, profiles, rest)
        if origin in layers:
            layers = dict(layers)
            details, switches = layers[origin]
            layers[origin] = ((name, *details[1:]), switches)
        return (mod, layers, profiles, rest)

    def standing(self) -> dict:
        """Every change and op the loadout holds, each with whether it is on,
        the note on it and what it says: by its key, which starts with the
        mod it is of.

        The third kind of work, after the changes made here and
        :meth:`typed_details`: what was *done to* something the loadout
        already held.  A change read out of a mod's folder is not work, and
        taking it out is: the folder still has it and the loadout is meant
        not to.  Compare two of these with :meth:`taken_since`.

        What it says -- the value a change sets or the nodes it carries, an
        op as written -- is for :meth:`made_since`: a change made here and
        saved keeps its key when its value is typed again, and is unsaved
        again all the same.  The note is for :meth:`made_since` too, on
        something made here, and for :meth:`noted_since` on the rest.
        """
        held: dict = {
            change.key: (change.active, change.note, (change.new_value, change.payload))
            for change in self.changes.values()
        }
        held.update((op.key, (op.active, op.note, op.source)) for op in self.ops)
        return held

    def made_since(self, before) -> int:
        """How many changes made here are new since ``before`` -- a
        :meth:`standing` -- or say something else than they did, in what
        they set or in the note on them: what "N changes you have made are
        not saved" counts.

        The changes, as :attr:`changes_made` counts them.  An op typed here
        that comes to none is still something made, so where no change is
        new or different the ops made here that are are counted instead, as
        :attr:`authored_count` has it.  Against nothing, it is everything
        made here.
        """
        before = before or {}

        def moved(key, note, says) -> bool:
            was = before.get(key)
            return was is None or was[1:] != (note, says)

        changes = sum(
            1 for change in self._made_here()
            if moved(change.key, change.note, (change.new_value, change.payload))
        )
        return changes or sum(
            1 for op in self.ops if op.authored and moved(op.key, op.note, op.source)
        )

    def _loaded_origins(self) -> set:
        """The mods whose changes and ops there are to compare: the loaded
        ones, and the loadout's own, which has no layer and is never
        unloaded.  And a mod that was dropped once everything in it had been
        taken out (:attr:`emptied`): that is reverting, not unloading."""
        return {layer.origin for layer in self.layers} | {""} | self.emptied

    def taken_since(self, before) -> int:
        """How many of the changes and ops in ``before`` -- a
        :meth:`standing` -- have been taken out or switched since.

        One that has arrived is not counted: that is a folder read, or a
        change made here, which :meth:`made_since` answers for, as it does
        for one that says something else than it did.  Nor is
        anything of a mod that is no longer loaded: unloading a mod is undone
        by reading it in again.  Unless it went with nothing left in it:
        reverting every change of a mod in the Mod changes panel drops the
        mod as well, and that is a revert like any other.

        Nor one whose note alone is another: that is an edit and no revert,
        and was counted here as "reverted or switched".  See
        :meth:`noted_since`.
        """
        if not before:
            return 0
        loaded = self._loaded_origins()
        now = self.standing()
        return sum(
            1 for key, was in before.items()
            if key[0] in loaded and (key not in now or now[key][0] != was[0])
        )

    def noted_since(self, before) -> int:
        """How many of the changes and ops in ``before`` -- a
        :meth:`standing` -- that were not made here have another note than
        they had, and nothing else about them moved.

        Each is counted in one place.  The note on something made here is
        part of what was made, and :meth:`made_since` counts it.  One
        switched as well is in :meth:`taken_since` already.
        """
        if not before:
            return 0
        loaded = self._loaded_origins()
        mine: set = {change.key for change in self._made_here()}
        mine.update(op.key for op in self.ops if op.authored)
        now = self.standing()
        return sum(
            1 for key, was in before.items()
            if key[0] in loaded and key in now and key not in mine
            and now[key][0] == was[0] and now[key][1] != was[1]
        )

    def take_read(self, changes) -> int:
        """Land changes read from a mod folder, keeping anything you authored.

        Retuning one of a mod's own ModOps produces a change with that op's
        identity -- it *is* that op, with a different value -- so reading the
        folder again would put the mod's own value back and quietly undo you.
        Yours wins, and the caller says how many were kept.
        """
        kept = 0
        for change in changes:
            standing = self.changes.get(change.key)
            if standing is not None and standing.authored:
                kept += 1
                continue
            self.set_change(change)
        return kept

    def authored_of(self, origin: str) -> List[Change]:
        return self.ordered(
            change for change in self.changes.values()
            if change.authored and change.origin == origin
        )

    @property
    def is_multi_mod(self) -> bool:
        """Whether the change list wants a level for which mod each row is.

        Counted over the mods that are *loaded* as well as the ones that have
        changed something: a second mod sitting there having done nothing is
        still a second mod, and the row saying so is where it is unloaded or
        re-read.  With one mod a heading over the whole list says nothing
        nobody already knows.

        Counted, and no further than two.  It was asked of :meth:`origins`,
        which puts every mod in load order first, and the Mod changes panel
        asks it for every mod's row: with 417 mods loaded that was 2.3 of
        the 4.5 seconds an edit held the window.
        """
        seen = {layer.origin for layer in self.layers}
        if len(seen) > 1:
            return True
        for change in self.changes.values():
            if change.origin not in seen:
                seen.add(change.origin)
                if len(seen) > 1:
                    return True
        return False

    def reindex(self) -> None:
        """Re-file every change under its current key.

        Identities are stable now, so this is a cheap safety net rather than a
        requirement after an in-place edit.
        """
        self.changes = {change.key: change for change in self.changes.values()}

    def get(
        self, guid: str, path: str, op: str = OP_REPLACE, origin: str = ""
    ) -> Optional[Change]:
        return self.find(guid, path, op, origin)

    def for_asset(self, guid: str, origin=None) -> List[Change]:
        return [
            c
            for c in self.changes.values()
            if c.guid == guid and (origin is None or c.origin == origin)
        ]

    def guids(self) -> List[str]:
        seen: List[str] = []
        for change in self.ordered():
            if change.guid not in seen:
                seen.append(change.guid)
        return seen

    def ordered(self, changes: Optional[Iterable[Change]] = None) -> List[Change]:
        """Stable ordering: by mod in load order, then asset label, then path.

        Load order leads because this is also the order the changes are applied
        in.  With one mod it makes no difference; with a mods folder loaded it
        is the difference between showing what the game will do and showing an
        alphabetical average of what every mod wants.

        ``changes`` puts only those in that order: the same list as sorting
        them all and keeping those, since the sort is stable.  Asking one mod
        for its own by sorting everything was 31,000 changes sorted again for
        each of 400 mods, wherever a question went round the mods.
        """
        chosen = list(self.changes.values() if changes is None else changes)
        # The rank per mod, not per change: a sort of a few thousand changes
        # asks its key function a hundred thousand times, and each answer was a
        # scan of the loaded mods.
        ranks = {origin: self.layer_rank(origin) for origin in {change.origin for change in chosen}}
        return sorted(
            chosen,
            key=lambda c: (ranks[c.origin], c.asset_label.lower(), c.path, c.op),
        )

    def active(self) -> List[Change]:
        """The changes the mod actually makes.

        Everything that asks what this mod *does* -- the generated ModOps, the
        verifier, the asset as the editor shows it -- goes through here, so a
        switched-off change is inert everywhere at once rather than being
        remembered to skip in each place.  So is a replaced mod's: see
        :meth:`not_loading`.
        """
        off = self.not_loading()
        return [
            change
            for change in self.ordered()
            if change.active and change.origin not in off
        ]

    def collections(self) -> List[str]:
        """The collections any loaded mod came from or is in, first seen first."""
        seen: List[str] = []
        for layer in self.layers:
            for name in [layer.from_collection, *self.listed_in(layer.origin)]:
                if name and name not in seen:
                    seen.append(name)
        return seen

    def listed_in(self, origin: str) -> List[str]:
        """The collections that list the mod at ``origin``."""
        return list(self.listed.get(origin.split("/")[0], ()))

    def note_collection(self, name: str, present, brought=None) -> None:
        """A collection has been installed: ``present`` is every one of its
        mods installed now, by folder name, and ``brought`` the ones it put
        there -- the rest were installed already, and are yours.  ``brought``
        left out is every one of them, which is what a caller that cannot
        tell says."""
        present = list(present)
        for folder in present if brought is None else brought:
            self.provenance[folder] = name
        for folder in present:
            held = self.listed.setdefault(folder, [])
            if name not in held:
                held.append(name)

    def forget_collection(self, name: str) -> None:
        """A collection taken out: nothing is said to have come with it, or
        to be in it, any more."""
        self.provenance = {folder: by for folder, by in self.provenance.items() if by != name}
        for folder in list(self.listed):
            held = [other for other in self.listed[folder] if other != name]
            if held:
                self.listed[folder] = held
            else:
                del self.listed[folder]
        for layer in self.layers:
            if layer.from_collection == name:
                layer.from_collection = ""

    def switched_off(self) -> set:
        """Origins of mods the game is not loading.

        Switching a whole mod off leaves its changes ticked underneath: a mod is
        off or on as a mod, and coming back to it should find your tweaks to it
        exactly as you left them rather than four hundred boxes to re-tick.

        A bundled mod is off when no copy of it is on: see
        :meth:`loading_copies`.
        """
        return {origin for origin, copy in self.loading_copies().items() if not copy}

    def switch_of(self, origin: str) -> Optional[ModLayer]:
        """The mod whose switch ``origin`` answers to: the outermost loaded mod
        it is inside, or its own where it is inside none.

        The game skips a host's whole folder, what is bundled in it with it,
        so a bundled mod has no switch of its own that Play could set: Anno
        1800 reads it off its host's folder, and 117 off its host's line in
        the profile, never a line of the bundled mod's own.  A host with nothing of its own to read comes in
        as the mod inside it alone, and that one answers for itself.
        """
        if is_own_mod(origin):
            return None
        by_origin = {layer.origin: layer for layer in self.layers}
        return self._switch_of(origin, by_origin)

    def switch_holders(self) -> Dict[str, Optional[ModLayer]]:
        """:meth:`switch_of` for every mod at once, by its origin."""
        by_origin = {layer.origin: layer for layer in self.layers}
        return {layer.origin: self._switch_of(layer.origin, by_origin) for layer in self.layers}

    @staticmethod
    def _switch_of(origin: str, by_origin: dict) -> Optional[ModLayer]:
        return by_origin.get(modfolder.switch_holder(origin, by_origin))

    def loading_copies(self) -> Dict[str, str]:
        """Each mod's origin -> the origin of the copy of it the game loads
        with the switches as they are now, or "" where none of them is on.

        A shared piece is read once however many mods carry it (see
        :attr:`ModLayer.copies`), from the copy that loaded when the folder was
        read.  Switch that copy's host off and another host's copy loads in its
        place -- by the loader's rule, :func:`modfolder.loading_copy`, among
        the copies that are on -- so the mod still loads, and its patch still
        follows it, while the changes listed are those of the copy that was
        read.  Between equal versions the one read is kept: the Studio's
        reading already said the loader came to it last.
        """
        by_origin = {layer.origin: layer for layer in self.layers}
        others: Dict[str, List[Tuple[str, str]]] = {}
        for layer in self.layers:
            for passed, kept in (layer.copies or {}).items():
                if kept in by_origin:
                    others.setdefault(kept, []).append(
                        (passed, (layer.copy_versions or {}).get(passed, ""))
                    )

        def on(origin: str) -> bool:
            holder = self._switch_of(origin, by_origin)
            return holder is None or holder.active

        found: Dict[str, str] = {}
        for layer in self.layers:
            copies = [copy for copy in sorted(others.get(layer.origin, ())) if on(copy[0])]
            if on(layer.origin):
                copies.append((layer.origin, layer.version or ""))
            best = modfolder.loading_copy(
                copies,
                version=lambda copy: copy[1],
                in_cache=lambda copy: modfolder.cache_id(copy[0]) != 0,
            ) if copies else None
            found[layer.origin] = best[0] if best is not None else ""
        return found

    def carrier_of(self, origin: str) -> Optional[ModLayer]:
        """The loaded mod a copy at ``origin`` is bundled in, for a copy that
        is not itself a layer -- one :attr:`ModLayer.copies` names."""
        for layer in self.layers:
            if origin in (layer.copies or {}):
                return layer
        return self.host_layer(origin)

    def copy_version(self, origin: str) -> str:
        """The version the copy at ``origin`` declares, as far as is known."""
        layer = self.layer(origin)
        if layer is not None:
            return layer.version or ""
        carrier = self.carrier_of(origin)
        return (carrier.copy_versions or {}).get(origin, "") if carrier is not None else ""

    def replaced(self) -> Dict[str, str]:
        """Mods that are on and still not loaded, because another mod that is
        on lists them as deprecated: ``{origin: the replacing mod's origin}``.

        Both games' loaders drop such a mod outright -- ``HandleDeprecation``
        in their source -- so nothing it does reaches the game.  Derived from
        the switches rather than stored: switch the replacing mod off and the
        old one loads again, here as in game.  Not a switch of its own either,
        so it is not in :meth:`switched_off`, which is what writing a mods
        folder back out asks.
        """
        from . import dependencies

        return dependencies.replaced_layers(self)

    def not_loading(self) -> set:
        """Origins of mods whose changes do not reach the game: switched off,
        or replaced by another mod."""
        return self.switched_off() | set(self.replaced())

    @property
    def active_count(self) -> int:
        off = self.not_loading()
        return sum(
            1
            for change in self.changes.values()
            if change.active and change.origin not in off
        )

    def active_guids(self) -> List[str]:
        seen: List[str] = []
        for change in self.active():
            if change.guid not in seen:
                seen.append(change.guid)
        return seen

    def set_active(self, keys, on: bool) -> int:
        """Switch changes on or off by key.  Returns how many actually moved."""
        wanted = {tuple(key) for key in keys}
        moved = 0
        for key, change in self.changes.items():
            if key in wanted and change.active != on:
                change.active = on
                moved += 1
        return moved

    # -- profiles -------------------------------------------------------------
    def chosen_profile(self) -> Profile:
        """The profile whose switches the layers hold -- see :attr:`profile`."""
        if not self.profiles:
            self.profiles.append(Profile(self.profile))
        for profile in self.profiles:
            if profile.name == self.profile:
                return profile
        # A name that is not there is a file somebody edited; the first is
        # as good an answer as any, and better than none.
        self.profile = self.profiles[0].name
        return self.profiles[0]

    def profile_names(self) -> List[str]:
        return [profile.name for profile in self.profiles]

    def find_profile(self, name: str) -> Optional[Profile]:
        for profile in self.profiles:
            if profile.name == name:
                return profile
        return None

    def live_switches(self) -> Dict[str, bool]:
        """The chosen profile's switches as they stand: the layers'."""
        return {layer.origin: layer.active for layer in self.layers}

    def switches_of(self, name: str) -> Dict[str, bool]:
        """Every loaded mod, and whether profile ``name`` has it on."""
        profile = self.find_profile(name)
        if profile is None:
            raise KeyError(name)
        if profile is self.chosen_profile():
            return self.live_switches()
        return {layer.origin: profile.switches.get(layer.origin, False) for layer in self.layers}

    def choose_profile(self, name: str) -> bool:
        """Make ``name`` the chosen profile.  Whether anything changed.

        The layers' switches are written back into the profile being left
        and the new one's read into them -- the whole of it.  A mod the new
        one does not mention is off in it.  Nothing else about a mod is the
        profile's, so nothing else moves.

        Written back by merging rather than replacing, so a mod unloaded
        while this profile was chosen keeps its switch here for an undo that
        brings it back; the file only ever holds loaded mods' switches.
        """
        target = self.find_profile(name)
        if target is None:
            raise KeyError(name)
        leaving = self.chosen_profile()
        if target is leaving:
            return False
        leaving.switches.update(self.live_switches())
        for layer in self.layers:
            layer.active = target.switches.get(layer.origin, False)
        self.profile = target.name
        return True

    def _check_profile_name(self, name: str, ignoring: Optional[Profile] = None) -> str:
        name = str(name).strip()
        if not name:
            raise ValueError(tr("A profile needs a name."))
        for profile in self.profiles:
            if profile is not ignoring and profile.name.casefold() == name.casefold():
                raise ValueError(tr("There is a profile called %s already.", profile.name))
        return name

    def add_profile(self, name: str, like: Optional[str] = None) -> Profile:
        """A new profile switched as ``like`` is -- the chosen one when left
        out -- added last and not chosen.  Raises ValueError for a name that
        is empty or taken."""
        name = self._check_profile_name(name)
        switches = self.switches_of(self.chosen_profile().name if like is None else like)
        profile = Profile(name, dict(switches))
        self.profiles.append(profile)
        return profile

    def rename_profile(self, old: str, new: str) -> None:
        """Raises KeyError for a profile that is not there, and ValueError for
        a name that is empty or taken by another."""
        profile = self.find_profile(old)
        if profile is None:
            raise KeyError(old)
        new = self._check_profile_name(new, ignoring=profile)
        if profile is self.chosen_profile():
            self.profile = new
        profile.name = new

    def delete_profile(self, name: str) -> None:
        """Forget a profile.  The last one cannot be; the chosen one can, and
        the first of the rest is chosen in its place."""
        profile = self.find_profile(name)
        if profile is None:
            raise KeyError(name)
        if len(self.profiles) == 1:
            raise ValueError(
                tr("%s is this loadout's only profile.", name) if name
                else tr("This is this loadout's only profile.")
            )
        if profile is self.chosen_profile():
            self.choose_profile(next(p.name for p in self.profiles if p is not profile))
        self.profiles.remove(profile)

    def read_into(self, live: Optional[str], before: Dict[str, bool]) -> None:
        """The mods folder has just been read in, and the layers hold its
        switches.  They are the switches of the profile the game loads, which
        is not always the chosen one.

        ``live`` is which of this loadout's profiles the game loads, or None
        when none of them is; ``before`` is the chosen profile's switches as
        they were before the read.  With another profile live, what the
        folder says goes to that one, and the chosen one keeps its own for
        every mod it had -- a mod read for the first time takes the folder's
        word in it.  With the chosen one live, or none, the folder is the
        chosen one's say, as it always was.  The other profiles are not
        touched either way; a mod new to them is off in them, and a mod gone
        from the folder is gone from all of them when the file is written.
        """
        chosen = self.chosen_profile()
        target = self.find_profile(live) if live is not None else None
        if target is None or target is chosen:
            return
        target.switches.update(self.live_switches())
        for layer in self.layers:
            if layer.origin in before:
                layer.active = before[layer.origin]

    def name_first_profile(self, name: str = DEFAULT_PROFILE) -> None:
        """Name a loadout's one profile, if it has no name -- which only a
        file edited by hand, or a test, leaves it with."""
        if len(self.profiles) == 1 and not self.profiles[0].name and name:
            self.profiles[0].name = name
            self.profile = name

    def _profiles_to_save(self) -> List[dict]:
        """Every profile, the chosen one as the layers have it, each holding
        loaded mods only: a mod gone from the loadout is gone from all of
        them."""
        loaded = {layer.origin for layer in self.layers}
        chosen = self.chosen_profile()
        return [
            _with_unknown({
                "name": profile.name,
                "switches": {
                    origin: on
                    for origin, on in (
                        self.live_switches() if profile is chosen else profile.switches
                    ).items()
                    if origin in loaded
                },
            }, profile.extra)
            for profile in self.profiles
        ]

    def __len__(self) -> int:
        return len(self.changes)

    def __iter__(self) -> Iterator[Change]:
        return iter(self.ordered())

    # -- history ------------------------------------------------------------
    # Two histories (section 9 of the proposal): each mod of yours' here, and
    # the loadout's list in :mod:`core.histories`.
    def history_of(self, origin: str = "") -> List[Checkpoint]:
        """The history of ``origin``, a mod of yours: its project's, oldest
        first.  Empty before its first checkpoint."""
        return self.mod_history.get(origin, [])

    def checkpoint(self, label: str, kind: str = "", origin: str = "",
                   loadout: str = "") -> Checkpoint:
        """Record the work on ``origin`` -- the loadout's own mod unless
        named -- as a new checkpoint of its history.  ``loadout`` names the
        loadout it is taken in, which each edit new since the last
        checkpoint is noted as made in.

        The changes you made, not the ones you opened: a checkpoint of seven
        thousand changes read off disk says nothing anybody wants to read, and
        restoring one would put somebody else's mod back to a state it was
        never in.
        """
        entry = self._as_checkpoint(origin, label, kind, loadout)
        self.mod_history.setdefault(origin, []).append(entry)
        return entry

    def kept_inside(self, keeper: str = "") -> dict:
        """The edits ``keeper`` -- a mod of yours, the loadout's own unless
        named -- keeps inside somebody else's ops, as its project keeps them
        (:data:`.projects.INSIDE`): here, and for a patch set aside inside a
        mod not loaded here (:attr:`kept_aside`).  Only its own edits are
        written out to find them."""
        mine = [op.kept_by == keeper for op in self.ops]
        held = {
            "layers": [{"origin": layer.origin} for layer in self.layers],
            # The rest as places, so that each op of its keeps where it stands.
            "ops": [op.to_dict() if kept else {} for op, kept in zip(self.ops, mine, strict=True)],
            "changes": [change_to_dict(c) for c in self.changes.values() if c.kept_by == keeper],
            "taken_out": [list(entry) for entry in self.taken_out
                          if len(entry) > 4 and entry[4] == keeper],
            "held_edits": [dict(record) for record in self.held_edits
                           if record.get("kept_by") == keeper],
        }
        return project_files.inside_of(held, keeper, self.kept_aside)

    def _as_checkpoint(self, origin: str, label: str = "", kind: str = "",
                       loadout: str = "") -> Checkpoint:
        """The work on ``origin`` as a checkpoint would hold it now."""
        before = self.latest_checkpoint_of(origin)
        inside = self.kept_inside(origin)
        for key, says in (("ops", _OP_SAYS), ("changes", _CHANGE_SAYS)):
            if key in inside:
                _noted(inside[key], (before.inside.get(key) or []) if before is not None else [],
                       loadout, says)
        return Checkpoint(
            timestamp=time.time(),
            label=label,
            version=self.version_of(origin),
            changes=_noted(
                [change_to_dict(c) for c in self.authored() if c.origin == origin],
                before.changes if before is not None else [], loadout, _OWN_CHANGE_SAYS,
            ),
            kind=kind,
            ops=_noted(
                [op.to_dict() for op in self.ops if op.authored and op.origin == origin],
                before.ops if before is not None else [], loadout, _OWN_OP_SAYS,
            ),
            narrow=[op.to_dict() for op in self.narrow if op.origin == origin],
            details=self.details_of(origin),
            loadout=loadout,
            inside=inside,
        )

    def version_of(self, origin: str = "") -> str:
        """The version of ``origin``, a mod of this loadout: the loadout's
        own mod's for "", or a loaded mod's -- for the mod the loadout
        carries on with, its own mod's where it says none.

        A carried-on mod's is the one in its modinfo and on mod.io.  The
        loadout's own field goes on saying "1.0" there, and a checkpoint
        labelled with it would be labelled with a number nobody shipped.
        """
        record = self.mod(origin)
        if record is not None and (record.version or is_own_mod(origin)):
            return record.version
        return self.own.version if origin == self.home else ""

    def details_of(self, origin: str = "") -> dict:
        """The details of ``origin`` its project keeps, as a checkpoint
        holds them: its name, version, description and the rest, less its
        ModID and its modinfo as read (:data:`_DETAILS_KEPT_OUT`)."""
        record = self.mod(origin)
        if record is None:
            return {}
        return {
            name: json.loads(json.dumps(getattr(record, name)))
            for name in project_files.layer_details() if name not in _kept_out(record)
        }

    def _set_details(self, origin: str, details: dict) -> None:
        record = self.mod(origin)
        if record is None:
            return
        for name in project_files.layer_details():
            if name in details and name not in _kept_out(record):
                setattr(record, name, json.loads(json.dumps(details[name])))
        if record is self.own:
            # What it offers follows its Options block, put back with the rest.
            self.own.offered = settings.read(self.options).to_dict() if self.options else {}
        else:
            # Its name, category and what it loads after are where it sorts.
            self.renumber_layers()

    def latest_checkpoint_of(self, origin: str = "") -> Optional[Checkpoint]:
        history = self.history_of(origin)
        return history[-1] if history else None

    @property
    def latest_checkpoint(self) -> Optional[Checkpoint]:
        """The loadout's own mod's last checkpoint."""
        return self.latest_checkpoint_of("")

    def diff_since(self, checkpoint: Optional[Checkpoint], origin: str = "") -> ChangeDiff:
        """What you have done to ``origin`` since ``checkpoint``."""
        before = checkpoint.as_changes(origin) if checkpoint is not None else []
        return diff_changes(before, [c for c in self.authored() if c.origin == origin])

    def changed_since_checkpoint(self, origin: str = "") -> bool:
        """Whether the work on ``origin`` -- its changes, its ModOps or its
        details -- has moved since its last checkpoint, or it has none."""
        latest = self.latest_checkpoint_of(origin)
        return latest is None or _work_in(latest) != _work_in(self._as_checkpoint(origin))

    def worth_a_checkpoint(self, origin: str = "") -> bool:
        """Whether a checkpoint of ``origin`` taken by hand now would record
        anything: its work moved since its last, or before its first, it has
        a change, a ModOp or an edit inside somebody else's ops to record.

        Not what :meth:`changed_since_checkpoint` says before the first: a
        loadout's checkpoint takes one of each project with none, empty or
        not, as it is what restoring the loadout puts that project back to.
        """
        if self.latest_checkpoint_of(origin) is not None:
            return self.changed_since_checkpoint(origin)
        changes, ops, narrow, _details, kept = _work_in(self._as_checkpoint(origin))
        return bool(changes or ops or narrow or kept)

    def release_checkpoint(self, label: str, kind: str, origin: str = "",
                           loadout: str = "") -> Optional[Checkpoint]:
        """The checkpoint an export or a publish (``kind``) of ``origin``
        stands at: a new one where its work moved since the last, and
        otherwise the last, where that marks a release already.

        Exporting the same work again, or publishing what was just exported,
        recorded a checkpoint holding nothing the one before it did not.  A
        publish of what an export recorded makes that one the publish: it is
        the same release, sent on.  Work last checkpointed by hand still
        takes one, as the next release is measured from the last checkpoint
        that marks one (:func:`.releases.last_release`).  None where there
        is no work to record at all.
        """
        if self.changed_since_checkpoint(origin):
            return self.checkpoint(label, kind, origin, loadout)
        latest = self.latest_checkpoint_of(origin)
        if latest is None:
            return None
        if latest.kind == KIND_EXPORT and kind == KIND_PUBLISH:
            latest.kind, latest.label = kind, label
            return latest
        if latest.kind in (KIND_EXPORT, KIND_PUBLISH):
            return latest
        return self.checkpoint(label, kind, origin, loadout)

    def restore(self, checkpoint: Checkpoint, origin: str = "") -> None:
        """Put your work on ``origin`` back to what it was at ``checkpoint``
        of its history, leaving every other mod alone.

        A checkpoint records what you did, so restoring one undoes what you
        have done since -- it does not unload the mods you had open at the
        time.  That is the loadout's history (:mod:`core.histories`).

        Your ModOps with your changes: an op typed or composed here goes, and
        the ones the checkpoint holds come back as they were, so the changes
        it puts back hang off the ops they hung off then.  And the ModOps the
        Studio wrote for them, which are all your own work: a checkpoint from
        before it kept them has none, and they are written again at the next
        step (:func:`.narrow.write`).  And the mod's details.  Never its
        builds, its mod.io link or what was published: those happened.

        What it keeps inside somebody else's ops goes back with
        :func:`.inside.restore`, which :func:`.histories.restore_mod` does
        as well.
        """
        for change in self._made_here():
            if change.origin == origin:
                self.changes.pop(change.key, None)
        self.ops = [op for op in self.ops if not (op.authored and op.origin == origin)]
        # Each under the name this loadout has for the mod, whichever it was
        # taken under (Checkpoint.as_changes).
        for op in checkpoint.as_ops(origin=origin):
            self.set_op(op)
        self.narrow = [op for op in self.narrow if op.origin != origin] + checkpoint.as_ops("narrow", origin)
        for change in checkpoint.as_changes(origin):
            change.authored = True
            self.changes[change.key] = change
        if checkpoint.details:
            self._set_details(origin, checkpoint.details)

    # -- persistence --------------------------------------------------------
    def to_dict(self) -> dict:
        profiles = self._profiles_to_save()
        return _with_unknown({
            "format": LOADOUT_FORMAT,
            # Which Studio wrote the file, for whoever reads it: nothing goes
            # by it.  The format is what a build checks.
            "written_by": __version__,
            # The own mod's record, as a layer's is written; its project
            # takes its details, as a layer's project does.
            "own": self.own.to_dict(),
            "source_path": self.source_path,
            "game": self.game,
            "editing": self.editing,
            "home": self.home,
            "aim": self.aim,
            "is_the_mod": self.is_the_mod,
            "own_mod": self.own_mod,
            "mods_folder": self.mods_folder,
            "modio_mod_id": self.modio_mod_id,
            "provenance": dict(self.provenance),
            "listed": {key: list(value) for key, value in self.listed.items()},
            "profile": self.chosen_profile().name,
            "profiles": profiles,
            "base_archive": self.base_archive,
            "base_assets": self.base_assets,
            "base_fingerprint": self.base_fingerprint,
            "base_update": self.base_update,
            "base_game_version": self.base_game_version,
            "read_stack": self.read_stack,
            "read_by": self.read_by,
            "builds": {origin: dict(held) for origin, held in self.builds.items()},
            "published": dict(self.published),
            "created_at": self.created_at,
            "changes": [change_to_dict(c) for c in self.ordered()],
            "layers": [layer.to_dict() for layer in self.layers],
            "ops": [op.to_dict() for op in self.ops],
            # Only where there are some, as a file from before them has none.
            **({"narrow": [op.to_dict() for op in self.narrow]} if self.narrow else {}),
            "unread": [entry.to_dict() for entry in self.unread],
            "taken_out": [
                [origin, source, list(within), *order] for origin, source, within, *order in self.taken_out
            ],
            "held_edits": [dict(held) for held in self.held_edits],
            "history": [s.to_dict() for s in self.history],
            # Only where there is some: each project's goes to its file.
            **({"mod_history": {
                origin: [s.to_dict() for s in history]
                for origin, history in self.mod_history.items() if history
            }} if any(self.mod_history.values()) else {}),
        }, self.extra)

    def save(self, path: Path) -> Dict[str, str]:
        """Write the loadout to ``path``, and each project of a mod of yours
        whose part moved, all or none of them (:mod:`.saving`).  A loadout
        file there that another version of the Studio wrote is kept beside
        it first (:func:`keep_other_version`), and so is each such project
        file it writes.

        A project whose file is not named for its mod any more goes to a
        file that is, in the same save, and every other loadout that refers
        to it is pointed there (:func:`_followed_in`).  Each that moved, as
        the paths loadouts write: for what else names a project by its
        path, the library's record of what it built.

        Raises LoadoutError, writing nothing, where a project that changed
        here cannot be written: another loadout changed it since this one
        read it, or it is missing (:meth:`held`).
        """
        pending = self.to_save(path)
        saving.write_all(pending.files)
        return pending.written()

    def to_save(self, path: Path) -> Pending:
        """What :meth:`save` writes, without writing it: for several
        loadouts saved all or none of them, in one journal.  Raises
        LoadoutError as :meth:`save` does."""
        path = Path(path)
        data = self.to_dict()
        # A profile with no name is written with the default one, in the file
        # only.
        if len(data["profiles"]) == 1 and not data["profiles"][0]["name"]:
            data["profiles"][0]["name"] = data["profile"] = DEFAULT_PROFILE
        files, refs, moves = self._projects_to_write(data)
        if refs:
            data["format"] = LOADOUT_FORMAT
            data["projects"] = [ref.to_dict(origin) for origin, ref in refs.items()]
        # Its checkpoints bring a mod back from its project, where it is now.
        project_files.refer(data, moves)
        path.parent.mkdir(parents=True, exist_ok=True)
        keep_other_version(path)
        files[path] = disk.json_bytes(data)
        files.update(_followed_in(moves, self.game, path))
        for written in files:
            Path(written).parent.mkdir(parents=True, exist_ok=True)
        return Pending(self, path, files, refs, data["format"], moves)

    def name_saved(self, origin: str, was: str, move: project_files.Move) -> None:
        """Take the project of ``origin`` as holding the name
        :func:`name_in_file` wrote into it, ``move``, where its mod was
        called ``was`` until then: at the file and the revision written.
        Where nothing else of it had moved here, nothing has now, so the
        next save does not write it again -- a revision of nothing but the
        same name, which every other loadout would have to read again."""
        ref = self.projects.get(origin)
        if ref is None or move.renamed is None:
            return
        part = project_files.split(self.to_dict(), self.project_origins(), self.kept_aside).get(origin, {})
        unchanged = ref.digest == project_files.digest(project_files.named(part, was))
        ref.read_at = ref.revision = move.renamed[1]
        ref.digest = project_files.digest(part) if unchanged else ""
        self.followed([move])

    def followed(self, moves: Iterable[project_files.Move]) -> None:
        """Refer to each project in ``moves`` where it is now: in the
        loadout's references and in its history's checkpoints, as
        :func:`.projects.refer` points a loadout file there."""
        by_key = {project_files.key_of(move.before): move.after for move in moves}
        if not by_key:
            return
        for ref in self.projects.values():
            ref.path = by_key.get(project_files.key_of(ref.path), ref.path)
        for checkpoint in self.history:
            for held in checkpoint.projects.values():
                path = held.get("path")
                if isinstance(path, str) and path:
                    held["path"] = by_key.get(project_files.key_of(path), path)

    def _projects_to_write(self, data: dict) -> Tuple[Dict[Path, Optional[bytes]], Dict[str, project_files.Ref],
                                                      List[project_files.Move]]:
        """Take each project's part out of ``data``, and say which project
        files a save writes -- with None for one it takes away -- what the
        loadout then refers to, and which projects go to another file to
        follow their mod's name (:func:`.projects.path_for`)."""
        parts = project_files.split(data, self.project_origins(), self.kept_aside)
        files: Dict[Path, Optional[bytes]] = {}
        refs: Dict[str, project_files.Ref] = {}
        moves: List[project_files.Move] = []
        stuck: List[str] = []
        taken: List[Path] = []
        for origin, part in parts.items():
            name = project_files.name_of(part, origin)
            before = self.projects.get(origin)
            ref = replace(before) if before is not None else project_files.Ref(
                project_files.as_written(project_files.new_path(name, taken)),
            )
            where = project_files.where(ref.path)
            taken.append(where)
            ref.mod_id = str((part.get("details") or {}).get("mod_id") or "")
            digest = project_files.digest(part)
            refs[origin] = ref
            # Nothing of it moved here: not written, and a project that is
            # held is no reason not to save the rest.  One named for another
            # name than its mod's still goes to a file named for it, as it
            # is, at the revision it is at.
            if before is not None and digest == ref.digest and (where.exists() or ref.held):
                after = project_files.path_for(where, name, taken)
                if not ref.held and after != where:
                    try:
                        files[after] = where.read_bytes()
                    except OSError:
                        continue
                    files[where] = None
                    taken.append(after)
                    moves.append(project_files.Move(ref.path, project_files.as_written(after)))
                    ref.path = project_files.as_written(after)
                continue
            on_disk = None
            if where.exists():
                try:
                    on_disk = project_files.Project.read(where)
                except project_files.ProjectError:
                    on_disk = None
            if ref.held or (on_disk is not None and on_disk.revision != ref.read_at):
                stuck.append(name)
                continue
            # Its name alone moved since it was read or written here: a
            # loadout that read it where this one did has nothing to read
            # again (:attr:`.projects.Move.renamed`).
            renamed = None
            if (on_disk is not None and ref.digest and ref.digest
                    == project_files.digest(project_files.named(part, on_disk.details.get("name")))):
                renamed = (ref.read_at, ref.read_at + 1)
            ref.read_at += 1
            ref.revision = ref.read_at
            ref.digest = digest
            ref.held = ""
            after = project_files.path_for(where, name, taken) if before is not None else where
            if on_disk is not None:
                # Another version's: kept beside it before this one's
                # shape replaces it, as a loadout file is.
                records.keep_other_version(where)
            files[after] = project_files.Project(
                after, part, mod_id=ref.mod_id, game=self.game, revision=ref.read_at,
                created_at=on_disk.created_at if on_disk is not None else None, kept=ref.kept,
            ).as_bytes()
            if after != where:
                if where.exists():
                    files[where] = None
                taken.append(after)
            if after != where or renamed is not None:
                moves.append(project_files.Move(ref.path, project_files.as_written(after), renamed))
                ref.path = project_files.as_written(after)
        if stuck:
            raise LoadoutError(trn(
                "%(names)s was changed by another loadout since this one read it, or is "
                "missing, so this loadout's changes to it cannot be saved over it.",
                "%(names)s were changed by another loadout since this one read them, or "
                "are missing, so this loadout's changes to them cannot be saved over them.",
                len(stuck), names=", ".join(stuck),
            ))
        return files, refs, moves

    @classmethod
    def load(cls, path: Path) -> Loadout:
        """Read a loadout back, or say plainly why it cannot be read.

        Every failure here reaches the user as a dialog, so "could not be read"
        has to be a sentence rather than whichever Python error surfaced first.
        """
        where = Path(path).name
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
        except OSError as exc:
            raise LoadoutError(tr(
                "%(file)s could not be opened: %(error)s", file=where, error=exc,
            )) from None
        except ValueError:
            raise LoadoutError(tr(
                "%s is not readable as a loadout: the file is not valid JSON, "
                "which usually means it was edited by hand or never finished "
                "saving.", where
            )) from None
        if not isinstance(data, dict):
            raise LoadoutError(tr("%s is not a loadout file.", where))

        # Every loadout the Studio saves says its format.  One that doesn't
        # is not one of its files, and one of an older format than 0.1.0's
        # is a test build's: neither is read by shape, since a guess would
        # be written back over the original.  Nor is a newer one.
        saved = data.get("format")
        if not isinstance(saved, int) or isinstance(saved, bool):
            raise LoadoutError(tr(
                "%s is not readable as a loadout: it does not say which format "
                "it is in, as every loadout the Studio saves does.", where,
            ))
        _ON_DISK[_place(Path(path))] = (str(data.get("written_by") or ""), saved)
        if saved > LOADOUT_FORMAT:
            raise LoadoutError(tr(
                "%(file)s was saved by a newer version of the Studio "
                "(loadout format %(saved)s, and this build reads "
                "%(reads)s). Opening it here would risk writing back "
                "less than it holds.", file=where, saved=saved, reads=LOADOUT_FORMAT,
            ))
        if saved < OLDEST_LOADOUT_FORMAT:
            raise LoadoutError(tr(
                "%(file)s was saved by a test build of the Studio from before "
                "its first release (loadout format %(saved)s), which this "
                "version does not read.", file=where, saved=saved,
            ))

        if not isinstance(data.get("own"), dict):
            data["own"] = own_record().to_dict()
        refs, aside = _read_projects(data)
        loadout = cls(
            own=_own_from(data["own"]),
            source_path=data.get("source_path", ""),
            game=data.get("game", "anno1800"),
            editing=data.get("editing", ""),
            home=str(data.get("home") or ""),
            aim=data.get("aim", ""),
            is_the_mod=bool(data.get("is_the_mod", False)),
            own_mod=bool(data.get("own_mod", False)),
            mods_folder=data.get("mods_folder", ""),
            modio_mod_id=_number(data.get("modio_mod_id"), 0, int),
            provenance={
                str(k): str(v) for k, v in (data.get("provenance") or {}).items()
                if isinstance(data.get("provenance"), dict)
            },
            listed={
                str(k): [str(name) for name in v]
                for k, v in (data.get("listed") or {}).items()
                if isinstance(data.get("listed"), dict) and isinstance(v, list)
            },
            base_archive=data.get("base_archive", ""),
            base_assets=_number(data.get("base_assets"), 0, int),
            base_fingerprint=str(data.get("base_fingerprint") or ""),
            base_update=str(data.get("base_update") or ""),
            base_game_version=str(data.get("base_game_version") or ""),
            read_stack=[
                [str(part) for part in entry] for entry in data["read_stack"]
                if isinstance(entry, (list, tuple))
            ] if isinstance(data.get("read_stack"), list) else None,
            read_by=_read_by(data),
            builds=_builds(data.get("builds")),
            published=_published(data.get("published")),
            created_at=_number(data.get("created_at"), time.time(), float),
            extra=_unknown(data, _loadout_keys()) or {},
        )
        # Layers come first: the ordering of changes is read off them.
        for raw in _listed(data, "layers", where):
            layer = ModLayer.from_dict(raw)
            if not is_own_mod(layer.origin):
                loadout.set_layer(layer)
        for raw in _listed(data, "ops", where):
            op = Op.from_dict(raw)
            # An op that names its asset and has no Path -- ``<ModOp
            # Type="addNextSibling" GUID="601470">`` kept as written -- has
            # no expression, and its source is the whole of it. Dropped here,
            # its changes went out one field at a time, the very form it was
            # kept to avoid. Only an op with neither is nothing at all.
            if op.expression or op.source:
                loadout.set_op(op)
        loadout.narrow = [Op.from_dict(raw) for raw in _listed(data, "narrow", where)]
        for raw in _listed(data, "unread", where):
            loadout.unread.append(Unread.from_dict(raw))
        # What a newer Studio wrote after the order is kept after it.
        loadout.taken_out = [
            [str(taken[0]), str(taken[1]), [str(uid) for uid in taken[2]],
             int(taken[3]) if len(taken) > 3 and str(taken[3]).lstrip("-").isdigit() else 0,
             *taken[4:]]
            for taken in data.get("taken_out") or ()
            if isinstance(taken, (list, tuple)) and len(taken) >= 3
            and isinstance(taken[2], (list, tuple))
        ]
        loadout.held_edits = [
            dict(held) for held in data.get("held_edits") or () if isinstance(held, dict)
        ]
        for raw in _listed(data, "changes", where):
            change = change_from_dict(raw)
            if change is not None:
                loadout.changes[change.key] = change
        for raw in _listed(data, "history", where):
            if isinstance(raw, dict):
                loadout.history.append(LoadoutCheckpoint.from_dict(raw))
        histories = data.get("mod_history")
        for origin, raws in (histories.items() if isinstance(histories, dict) else ()):
            if isinstance(raws, list):
                loadout.mod_history[str(origin)] = [
                    Checkpoint.from_dict(raw) for raw in raws if isinstance(raw, dict)
                ]
        loadout._read_profiles(data, where)
        _check_paths(loadout, where)
        if loadout.narrow and str(data.get("written_by") or "") != __version__:
            # Another version of the Studio saved it last, and may have edited
            # the changes without writing these again: an older one keeps them
            # as it found them.  Set aside, they are written again from the
            # changes at the first step (:func:`.narrow.write`), and until
            # then the changes are what is exported.  Not a project's: there
            # they are the record, and its changes are read out of them.
            loadout.narrow = [op for op in loadout.narrow if op.origin in refs]
            for change in loadout.changes.values():
                if change.origin not in refs:
                    change.written_as = ""
        if refs:
            # What each project's part is as read, so a save writes only the
            # projects whose part moved since.
            loadout.kept_aside = aside
            parts = project_files.split(loadout.to_dict(), list(refs), aside)
            for origin, ref in refs.items():
                ref.digest = project_files.digest(parts.get(origin, {}))
            loadout.projects = refs
        return loadout

    def _read_profiles(self, data: dict, where: str) -> None:
        """The profiles a saved loadout holds, or the one it is when it is
        from before profiles: its layers' switches, named DEFAULT_PROFILE.

        The chosen profile's switches are taken from the layers, not from
        its entry: they are written the same, and a build that reads format
        2 may since have switched a mod and written the layers alone.
        """
        profiles: List[Profile] = []
        for raw in _listed(data, "profiles", where):
            if not isinstance(raw, dict):
                continue
            name = str(raw.get("name") or "").strip()
            switches = raw.get("switches")
            if not name or any(p.name.casefold() == name.casefold() for p in profiles):
                continue
            profiles.append(Profile(name, {
                str(origin): bool(on)
                for origin, on in (switches.items() if isinstance(switches, dict) else ())
            }, extra=_unknown(raw, _PROFILE_KEYS)))
        if not profiles:
            self.profiles = [Profile(DEFAULT_PROFILE)]
            self.profile = DEFAULT_PROFILE
            return
        self.profiles = profiles
        self.profile = str(data.get("profile") or "")
        chosen = self.chosen_profile()
        chosen.switches = self.live_switches()


@lru_cache(maxsize=1)
def _loadout_keys() -> frozenset:
    """The keys at the top of a loadout file that this build reads: what it
    writes, those it writes only where it has some, and nothing else."""
    return frozenset(Loadout().to_dict()) | {"narrow", "projects", "mod_history"}


def _own_from(raw: dict) -> ModLayer:
    """The own mod's record as a loadout file holds it."""
    own = replace(ModLayer.from_dict(raw), origin="", written_whole=True)
    # What it offers is worked out from its Options block, which its
    # project may have changed since the loadout file was written.
    block = own.declared.get("Options")
    own.offered = settings.read(block).to_dict() if isinstance(block, dict) and block else {}
    return own


def _read_projects(data: dict) -> Tuple[Dict[str, project_files.Ref], Dict[str, dict]]:
    """Read the project of each mod of yours that the loadout file ``data``
    names, and put its part back into ``data``; the references, by origin,
    and what each patch keeps inside a mod ``data`` does not have loaded
    (:attr:`Loadout.kept_aside`).

    A project another loadout has changed since this one read it is put
    back as it is now, and held (:attr:`.projects.Ref.held`) until it is
    read again (:mod:`.readback`): the loadout's readings of it are older
    than what it holds.  One that is not where it was is looked for by its
    ModID (section 4.3 of the proposal), and one found is held so as well,
    since what this loadout read was another file.  One that cannot be
    read is held with nothing put back.
    """
    refs: Dict[str, project_files.Ref] = {}
    parts: Dict[str, dict] = {}
    for raw in data.get("projects") or ():
        if not isinstance(raw, dict):
            continue
        origin = str(raw.get("origin") or "")
        ref = refs[origin] = project_files.Ref.from_dict(raw)
        # Read where the file says only inside the Studio's folder: one
        # somebody sent can name any path, a share's among them, and one
        # anywhere else is looked for by its ModID, as one moved is.
        where = project_files.trusted_path(ref.path)
        try:
            if where is None:
                raise project_files.ProjectError(ref.path)
            project = project_files.Project.read(where)
        except project_files.ProjectError:
            there = where is not None and where.exists()
            found = None if there else _found_again(ref, str(data.get("game") or ""))
            if found is None:
                ref.held = project_files.UNREADABLE if there else project_files.MISSING
                continue
            ref.lost = str(where) if where is not None else ref.path
            ref.path = project_files.as_written(found.path)
            project = found
        ref.revision = project.revision
        if ref.lost or ref.revision != ref.read_at:
            ref.held = project_files.MOVED
        parts[origin] = project.part
        ref.kept = project.kept
    aside = project_files.join(data, parts)
    return refs, aside


def _followed_in(moves: List[project_files.Move], game: str, saving_to) -> Dict[Path, Optional[bytes]]:
    """Every other loadout for ``game`` that refers to a project in
    ``moves``, pointed at where it is now (:func:`.projects.refer`), as it
    is to be written in the save that moves it: those in the loadouts
    folder and beside ``saving_to``, the loadout being saved, which the
    cache knows (:func:`.filecache.loadouts_with_project`).

    One that cannot be read, or that a newer Studio wrote, is left as it
    is, and so is one the cache does not look in: opening it finds the
    project by its ModID, as one moved by hand (:func:`_read_projects`)."""
    if not moves:
        return {}
    # Not at the top: the cache reads loadout files with this module.
    from . import filecache, places

    found: Dict[str, Path] = {}
    for folder in dict.fromkeys((places.loadouts_dir(), Path(saving_to).parent)):
        for move in moves:
            for other in filecache.loadouts_with_project(
                    project_files.where(move.before), game, folder=folder, excluding=saving_to):
                found.setdefault(os.path.normcase(os.path.abspath(other)), other)
    files: Dict[Path, Optional[bytes]] = {}
    for other in found.values():
        try:
            data = json.loads(other.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        saved = data.get("format") if isinstance(data, dict) else None
        if (not isinstance(saved, int) or isinstance(saved, bool)
                or not OLDEST_LOADOUT_FORMAT <= saved <= LOADOUT_FORMAT):
            continue
        if project_files.refer(data, moves):
            files[other] = disk.json_bytes(data)
    return files


def _found_again(ref: project_files.Ref, game: str) -> Optional[project_files.Project]:
    """The project that keeps ``ref``'s mod, by its ModID, where it is not
    where the loadout last saw it.  None for none."""
    # Not at the top: the cache reads loadout files with this module.
    from . import filecache

    found = filecache.project_keeping(ref.mod_id, game) if ref.mod_id else None
    if found is None:
        return None
    try:
        return project_files.Project.read(found)
    except project_files.ProjectError:
        return None


def _check_paths(loadout: Loadout, where: str) -> None:
    """Check the paths in a loadout file as it is read, since it may be one
    somebody sent (security review N1).  One on another machine, or a
    device's, is forgotten before anything asks Windows about it.  A mod
    named by anything but a folder's name at each step is refused, file and
    all: Play switches it by that name in the mods folder, and ``..`` or a
    drive there would reach outside it.  Which mods folder, and which
    folders a mod may be read from, are the window's to check
    (:func:`keep_folders_inside`): only it knows the folders of this PC."""
    for layer in (loadout.own, *loadout.layers):
        for name in ("folder", "scripts_folder"):
            if disk.remote_or_device(getattr(layer, name)):
                setattr(layer, name, "")
    for name in ("mods_folder", "source_path"):
        if disk.remote_or_device(getattr(loadout, name)):
            setattr(loadout, name, "")
    for layer in loadout.layers:
        if not is_folder_path(layer.origin):
            raise LoadoutError(tr(
                "%(file)s names a mod %(origin)s, which is not the name of a "
                "folder, so it was not opened. A loadout the Studio saved "
                "never does: the file was edited by hand or made elsewhere.",
                file=where, origin=layer.origin,
            ))


def is_folder_path(origin: str) -> bool:
    """Whether ``origin`` names a folder one Windows name at each step, as
    every mod read from a mods folder is named: no step empty, ``.`` or
    ``..``, and none a drive, a stream or a device."""
    return all(
        part not in ("", ".", "..") and disk.windows_name(part) == part
        for part in str(origin).split("/")
    )


def keep_folders_inside(loadout: Loadout, roots) -> List[str]:
    """Forget the folder of each mod of ``loadout`` that is not in one of
    ``roots`` -- the mods folders this PC knows, the library and the rest
    the window names -- and say which mods those were.  A loadout file can
    name any folder for a mod, and a build copies everything in the folder
    into the mod it writes (:func:`.build.carry_folder`), into an upload to
    mod.io too (security review N1)."""
    roots = [root for root in roots if root]
    forgotten: List[str] = []
    for layer in loadout.layers:
        if layer.folder and not any(disk.spelt_inside(layer.folder, root) for root in roots):
            layer.folder = ""
            forgotten.append(layer.label)
    return forgotten


# What each loadout file this run has read or written says wrote it, by
# where it is: ``(written_by, format)``.  So a save knows, without reading
# the file again, whether it is about to write over another version's.
_ON_DISK: Dict[str, Tuple[str, int]] = {}


def _place(path: Path) -> str:
    try:
        return os.path.normcase(str(path.resolve()))
    except OSError:
        return os.path.normcase(str(path.absolute()))


@dataclass
class Pending:
    """A save not written yet (:meth:`Loadout.to_save`): the files, by
    where they go, and what the loadout refers to once they are."""

    loadout: Loadout
    path: Path
    files: Dict[Path, Optional[bytes]]
    refs: Dict[str, project_files.Ref]
    format: int
    moves: List[project_files.Move]

    def written(self) -> Dict[str, str]:
        """Take the loadout as saved, its files written: each project that
        moved, as :meth:`Loadout.save` returns them."""
        self.loadout.projects = self.refs
        self.loadout.followed(self.moves)
        _ON_DISK[_place(self.path)] = (__version__, self.format)
        return {move.before: move.after for move in self.moves if move.before != move.after}


def keep_other_version(path) -> Optional[Path]:
    """Keep the loadout file at ``path`` beside itself, before this build
    writes over it, where another version of the Studio wrote it, as
    ``<name>.annoload.<version>.bak`` (:func:`.records.keep_other_version`).
    The first save of a loadout an older version wrote rewrites it in this
    version's shape.  What wrote it is known without reading it again where
    this run read or wrote it already (:data:`_ON_DISK`)."""
    return records.keep_other_version(path, _ON_DISK.get(_place(Path(path))))


def name_in_file(path, origin: str, name: str) -> Optional[project_files.Move]:
    """Call the mod at ``origin`` ``name`` in the loadout file at ``path``,
    or in its project where it has one, and change nothing else.  Where it
    went into the project, how: the file the project went to, named for
    the new name (:func:`.projects.path_for`), and its new revision, which
    the loadout in hand is to take as read (:attr:`.projects.Ref.read_at`,
    the second of :attr:`.projects.Move.renamed`).  None where the name
    went into the loadout's file.

    For a rename, which is complete in itself: the file is moved to its new
    name the moment it is asked for, and the mod's name has to go with it.
    Saving the loadout would do that and write every other unsaved edit too,
    which nobody asked for by renaming.  Every other loadout that refers to
    the project is pointed at its new file in the same save, and one that
    had read it where this one had takes the new revision as read: only the
    name moved (:func:`_followed_in`).  Raises OSError or ValueError where
    a file cannot be read or written, or the project was changed by another
    loadout since this one read it.
    """
    path = Path(path)
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("not a loadout file")
    raw = next((
        raw for raw in data.get("projects") or ()
        if isinstance(raw, dict) and str(raw.get("origin") or "") == origin
    ), None)
    if raw is not None:
        ref = project_files.Ref.from_dict(raw)
        where = project_files.where(ref.path)
        try:
            project = project_files.Project.read(where)
        except project_files.ProjectError as exc:
            raise ValueError(str(exc)) from None
        if project.revision != ref.read_at:
            raise ValueError("changed by another loadout")
        project.part["details"] = {**project.details, "name": name}
        project.revision = raw["read_at"] = ref.read_at + 1
        taken =[project_files.where(other["path"]) for other in data.get("projects") or ()
                 if isinstance(other, dict) and other is not raw and isinstance(other.get("path"), str)]
        after = project_files.path_for(where, name, taken)
        move = project_files.Move(ref.path, project_files.as_written(after), (ref.read_at, project.revision))
        project_files.refer(data, [move])
        files: Dict[Path, Optional[bytes]] = {after: project.as_bytes()}
        if after != where:
            files[where] = None
        files[path] = disk.json_bytes(data)
        records.keep_other_version(where)
        files.update(_followed_in([move], str(data.get("game", "anno1800")), path))
        saving.write_all(files)
        return move
    if not is_own_mod(origin):
        for layer in data.get("layers") or ():
            if isinstance(layer, dict) and layer.get("origin") == origin:
                layer["name"] = name
    elif isinstance(data.get("own"), dict):
        data["own"]["name"] = name
    else:
        raise ValueError("not a loadout file")
    disk.write_json(path, data)
    return None


def after_their_entry(ordered: List[Change]) -> List[Change]:
    """``ordered`` with each key replacement moved behind its own entry's changes.

    Only behind the changes that find that entry by the key -- not behind every
    change to the asset. Pushed past all of them, a key replacement in one list
    landed after unrelated removals and inserts elsewhere, and a re-read export
    of the *shared pools and definitions* bundle came out a different asset.

    And only behind changes of the same op, which is what shares the key it was
    found by: a later op finds the entry by the key it has by then, and is
    written that way (see ``recording.keyed_as_it_stands``). Within the op, a
    change to the entry itself counts as much as one under it -- *Stronger
    Pirates* gives a trigger entry a new key and a new field in one merge, and
    with the key first the field found nothing.

    Innermost first, so an entry inside another is settled before the outer key
    moves.

    And behind the op's changes to the entry the new key names: once the key
    is replaced, a path by that key finds this entry too.  A wide replace of
    every ``Item`` with ``<Item><Product>2002</Product><X>1</X></Item>``, read
    field by field, gave ``Item[Product='2001']`` its new key before the add
    to ``Item[Product='2002']``, and that add put a second ``<X>`` in it.
    """
    out = list(ordered)
    moving = sorted(
        ((change, entry) for change in out if (entry := change.keyed_entry) is not None),
        key=lambda pair: -pair[1].count("/"),
    )
    for change, entry in moving:
        was = out.index(change)
        out.remove(change)
        entries = [entry]
        keyed = _KEYED_FIELD.search(change.path.rstrip("/"))
        new = str(change.new_value or "")
        if keyed is not None and keyed.group(2) not in new:
            became = "%s%s%s" % (entry[: keyed.start(3)], new, entry[keyed.end(3):])
            if became != entry:
                entries.append(became)
        last = -1
        for position, other in enumerate(out):
            if (other.origin, other.order) != (change.origin, change.order):
                continue
            path = other.path.rstrip("/")
            if any(path == one or path.startswith(one + "/") for one in entries):
                last = position
        out.insert(last + 1 if last >= was else was, change)
    return out


# The path to an asset's GUID, and every node on the way to it.
IDENTITY_PATHS = frozenset({"/Values", "/Values/Standard", "/Values/Standard/GUID"})


def emit_order(changes) -> List[Change]:
    """One op's changes to one asset, in the order they are written and so
    the order the game applies them.

    The one order there is: the writer writes a block in it, the reader replays
    a block in it to check its reading, and :func:`effective.build` applies a
    block in it for the asset that is shown.  The last of those had an order of
    its own, replacements before removals, and showed an asset the mod does not
    make: with ``Item[W='a']/V`` set to 2 and ``Item[V='2']`` removed, the
    removal came second, found two entries and was passed over.

    The game applies ModOps in file order, so a removal written after an
    insertion can match what that insertion just put there -- a list entry
    moved by removing it and re-inserting it elsewhere would be removed twice
    and end up gone.  Removals first, then value replacements, then anything
    that adds: each step only ever shrinks the set of nodes a later path can
    match.  Within a step the order the user sees in the changes list is kept.

    Except a removal of what holds the asset's GUID, which goes after everything
    else.  Every narrow ModOp finds its asset by ``Values/Standard/GUID``, so once
    that is gone the rest of the asset's ops match nothing.  *Enbesa Quest Skip*
    empties eighty quests by replacing their ``Values`` with nothing; written in
    path order, ``/Values/Standard`` went before ``/Values/Text``, and the game's
    loader missed all eighty ``Text`` removals.

    Unless the same changes put that node back: *New Docklands* merges into a
    wharf's ``Values``, read narrowly as removing ``/Values`` and inserting a new
    one, and a removal moved after the insert matched both.

    And a replacement of the field a list entry is found by goes after the other
    changes found through that entry -- only those -- innermost first.  *Pizza Margherita* swaps a
    Colosseum ingredient for its own tomato by replacing
    ``Item[GoodsOrPool='174519']``: key first, and every later field's path
    named an entry that no longer existed.
    """
    rank = {OP_REMOVE: 0, OP_REPLACE: 1}

    changes = list(changes)

    def position(change: Change) -> int:
        path = change.path.rstrip("/")
        if (
            change.op == OP_REMOVE
            and path in IDENTITY_PATHS
            and not _put_back(path, changes)
        ):
            return 4
        return rank.get(change.op, 2)

    return after_their_entry(sorted(changes, key=position))


def _put_back(path: str, changes) -> bool:
    """Whether one of ``changes`` inserts a node where ``path`` names one."""
    return any(puts_back(path, change) for change in changes)


def puts_back(path: str, change: Change) -> bool:
    parent, _, tag = path.rpartition("/")
    if not re.match(r"\s*<" + re.escape(tag) + r"[\s/>]", change.payload or ""):
        return False
    where = change.path.rstrip("/")
    if change.op in (OP_ADD_NEXT, OP_ADD_PREV):
        return where.rpartition("/")[0] == parent
    return change.op in (OP_ADD, OP_MERGE) and where == parent
