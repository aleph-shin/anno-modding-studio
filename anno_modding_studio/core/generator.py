"""Render pending changes into ModOp XML and write a loadable mod folder."""

from __future__ import annotations

import copy
import os
import posixpath
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple
from xml.sax.saxutils import escape, unescape

from . import changes as changes_mod
from . import disk, games, modfolder, modinfo, scripts, texts
from .changes import Change, Loadout, is_own_mod, own_record, switched_off
from .paths import resolve, verify

# How far in the ModOps the Studio writes itself stand: inside ``<ModOps>``.
INDENT = "  "

# Kept for callers that just want the Anno 1800 default; per-game values live on
# its games.Game.
MOD_ASSET_RELPATH = games.GAMES[games.ANNO_1800].asset_relpath


@dataclass
class VerifyResult:
    change: Change
    ok: bool
    message: str


def _comment_safe(text: str) -> str:
    """XML comments may not contain '--' or end with '-'."""
    cleaned = re.sub(r"-{2,}", "-", text.replace("\r", " ").replace("\n", " "))
    return cleaned.rstrip("-").strip()


def _notes(note: str) -> List[str]:
    """A note as the comments it goes out as, one to each of its lines; no
    line for no note.

    Each on one line with single spaces, which is how a comment is read
    back: a note written any other way would come back as a different one.
    And the comments the Studio writes stacked directly above an op are
    read back as the lines of its note (``modops._notes_above``): a ModOp
    for several changes is written under the note of each, one to a line.
    """
    found = []
    for line in (note or "").split("\n"):
        said = _comment_safe(" ".join(line.split()))
        if said:
            found.append("  <!-- %s %s -->" % (changes_mod.NOTE_MARK, said))
    return found


# Both live in :mod:`core.disk`, where a loadout can ask what its own mod's
# folder would be called without importing the exporter.
safe_folder_name = disk.safe_folder_name


def kept_folder_name(name: str, fallback: str = "[Mod] Anno Mod") -> str:
    """A mod's own folder name, written back as near to itself as Windows allows.

    Not tidied the way :func:`safe_folder_name` tidies typed words: the folder
    name is what the game knows the mod by and orders it by.  A switched-off
    ``- [Hadley] …`` comes back on as `` [Hadley] …``, and that space in front
    sorts it ahead of every name that starts with a letter or a bracket;
    trimmed, the export would be a second copy beside the mod rather than the
    mod, somewhere else in the stack.  What changes is only what Windows cannot
    hold: the characters it forbids, spaces and dots at the end, which it
    drops from a name without saying so, and a device name such as ``CON``
    (:func:`core.disk.windows_name`).
    """
    cleaned = disk.windows_name(name or "")
    return cleaned if cleaned.strip() else fallback


def mod_folder_name(loadout: Loadout) -> str:
    """``[Gameplay] My Anno Mod`` -- the convention published mods follow."""
    category = (loadout.own.category or "").strip() or "Mod"
    return safe_folder_name(
        "[%s] %s" % (category, loadout.own.name or "My Anno Mod"), "[Mod] Anno Mod"
    )


def for_mod(loadout: Loadout, origin: str) -> Loadout:
    """One loaded mod, on its own, ready to be written out.

    A loadout holds a stack of mods; a mod folder holds one.  This is the seam
    between the two: everything downstream -- the ModOps document, the
    modinfo.json, the folder name -- asks a loadout what it is, and gets the
    answer for the mod being exported rather than for the stack it sits in:
    a loadout whose own mod (:attr:`Loadout.own`) is that mod.

    A mod this loadout does not have is refused (:class:`LookupError`),
    rather than taken for the loadout's own mod and written out as that.
    """
    layer = loadout.own_layer() if is_own_mod(origin) else loadout.layer(origin)
    if layer is None:
        raise LookupError("no mod %r in this loadout to write out" % origin)
    # A mod with a modinfo of its own keeps the gaps in it: the stand-ins are
    # for a mod that has none, and written into an author's file they are
    # claims the author never made.
    own = not layer.declared
    record = own_record(
        name=layer.name or loadout.own.name,
        creator=layer.creator,
        description=layer.description,
        category=layer.category or ("Gameplay" if own else ""),
        version=layer.version or ("1.0" if own else ""),
        mod_id=layer.mod_id,
        # The author's own modinfo, so writing their mod back does not
        # delete the half of it the Studio does not model; or the loadout's
        # own mod's blocks, which the Studio writes whole.
        declared=copy.deepcopy(loadout.own.declared if is_own_mod(origin) else layer.declared or {}),
        written_whole=is_own_mod(origin),
        scripts_folder=loadout.own.scripts_folder if is_own_mod(origin) else "",
        # A mod whose modinfo gives it no name is shown under its folder's,
        # which is not a name to write into that modinfo.
        named_after_folder=bool(
            not own and layer.name == layer.origin and not modinfo.declares_a_name(layer.declared)
        ),
    )
    view = Loadout(
        own=record,
        source_path=loadout.source_path,
        game=loadout.game,
        base_archive=loadout.base_archive,
        base_assets=loadout.base_assets,
        base_fingerprint=loadout.base_fingerprint,
        base_update=loadout.base_update,
        base_game_version=loadout.base_game_version,
        created_at=loadout.created_at,
    )
    # A patch says, in its own modinfo, which mod it loads after -- by that
    # mod's declared ID where it has one, since that is what a loader matches.
    if layer.patches:
        patched = loadout.layer(layer.patches)
        record.patches = (patched.mod_id if patched is not None else "") or layer.patches
        record.needs = [
            ((other.mod_id if other is not None else "") or origin)
            for origin in loadout.needed(layer)[1:]
            for other in (loadout.layer(origin),)
        ]
        # And the star, where that mod has one: without it the patch is
        # sorted into the phase before the mod it patches.
        late = [value for value in loadout.load_after_of(layer) if value.strip() == "*"]
        if late and "*" not in (str(value).strip() for value in layer.load_after):
            record.load_after = late[:1]
    if is_own_mod(origin):
        # And what it loads after, which on a loadout is everything. A
        # loaded mod's is in its modinfo already.
        record.load_after = list(layer.load_after)
    for change in loadout.changes_of(origin):
        view.changes[change.key] = change
    # The ops travel with it: a mod that targets by search is written back
    # out targeting by search, which is the only form that goes on covering
    # assets from mods nobody here has heard of.
    view.ops = [op for op in loadout.ops if op.origin == origin]
    # And the ModOps the Studio writes for it, which are what it writes.
    view.narrow = [op for op in loadout.narrow if op.origin == origin]
    # And so do the ops the Studio could not read. They produced no changes,
    # so nothing else in this view stands for them -- and a mod written back
    # without them would do less in game than the one that was opened.
    view.unread = [entry for entry in loadout.unread if entry.origin == origin]
    return view


def writes_anything(loadout: Loadout) -> bool:
    """Whether a mod has anything to put in a file.

    A change that is switched on, or an op that goes out as itself: a kept
    op, or one the Studio could not read.  Asking only about changes left a mod that
    was *all* carried ops -- every op computing its value in a way the Studio
    cannot follow, say -- out of an export altogether, which is the one way of
    carrying an op that loses it.

    Or one that is switched off, which is written as a comment: a mod with
    everything in it off is a file of comments, which the game reads as
    nothing and its author reads as all of it.
    """
    return writes_for_the_game(loadout) or writes_only_comments(loadout)


def writes_only_comments(loadout: Loadout) -> bool:
    """Whether all a mod has to write is what is switched off in it."""
    if writes_for_the_game(loadout):
        return False
    # Or a comment of its file's that is no op's note: a mod whose patch
    # says only what the mod is for has that to write.
    return any(shelved(loadout)) or any(op.aside for op in loadout.ops)


def writes_for_the_game(loadout: Loadout) -> bool:
    """Whether a mod has anything to put in a file that the game would read."""
    if loadout.active_count or loadout.unread:
        return True
    # A mod of your own that is only Lua is a mod all the same: its modinfo
    # lists the modules, and the export copies them in.  Not a loaded one's:
    # its Lua is in its folder already, and comes across with the rest.
    if loadout.own.written_whole and scripts.declared({"Scripts": loadout.scripts}) is not None:
        return True
    from . import keptops

    return bool(keptops.exportable(loadout)[0])


def shelved(loadout: Loadout, covered=None) -> Tuple[list, List[Change]]:
    """``(ops, changes)`` that are switched off, each written as a comment.

    A kept op that is off is still the op it was, and stands for its changes
    the way one that is on does.  A change is off by its own switch, and not because
    its whole mod is: a mod that is not loading is left out as a folder, not
    line by line.  ``covered`` is the changes the ops that are on stand for.
    """
    if covered is None:
        from . import keptops

        covered = keptops.exportable(loadout)[1]
    ops = [op for op in loadout.ops if not op.active and op.can_expand]
    theirs = {op.uid for op in ops}
    not_loading = loadout.not_loading()
    return ops, [
        change
        for change in loadout.ordered()
        if not change.active
        and change.key not in covered
        and change.op_uid not in theirs
        and change.origin not in not_loading
    ]


def folder_for(loadout: Loadout, origin: str) -> str:
    """Where a loaded mod should be written back to.

    Its own folder name, when it has one: that is the name the game keys on and
    the name its load order position comes from, so putting it back under a
    tidied-up ``[Category] Name`` would quietly move it in the stack.
    """
    layer = loadout.layer(origin)
    if layer is not None:
        # Possibly a path: a mod bundled inside another goes back inside it,
        # because that is where the game found it and where its host expects it.
        return "/".join(kept_folder_name(part) for part in layer.origin.split("/"))
    return mod_folder_name(for_mod(loadout, origin))


def renamed(loadout: Loadout, origin: str) -> bool:
    """Whether a loaded mod has been given another name here, or another
    category, than the one it was read in with.

    Against its own modinfo, where it has one -- a category it never declared
    is not one it was given here -- and against its folder name, where it has
    none, since that is then what it is called.
    """
    layer = loadout.layer(origin)
    if layer is None:
        return False
    name = (layer.name or "").strip()
    if not name:
        return False
    if not layer.declared:
        return name != modfolder.bare_name(layer.origin.split("/")[-1])
    if name != layer.origin and name != modinfo.declared_field(layer.declared, "ModName"):
        return True
    category = modinfo.declared_field(layer.declared, "Category")
    return bool(category) and (layer.category or "").strip() != category


def given_a_new_mod_id(loadout: Loadout, origin: str) -> bool:
    """Whether a loaded mod of yours builds with another ModID than the one
    its folder declared: carried on and given a new one, by hand or by a
    new name where the ModID is made from the name.  To the game that is
    another mod, and it goes into a folder of its own
    (:func:`.builtby.own_folder`).  Never a patch, a fork or a mod bundled
    inside another, and never a mod whose folder declared no ModID."""
    layer = loadout.layer(origin)
    if layer is None or layer.patches or layer.fork_of or "/" in origin:
        return False
    if not loadout.yours(layer):
        return False
    declared = str((layer.declared or {}).get("ModID") or "").strip().lower()
    if not declared:
        return False
    return modinfo.written_mod_id(for_mod(loadout, origin)).strip().lower() != declared


def export_folder_for(loadout: Loadout, origin: str) -> str:
    """Where an export writes a mod, which is not always where Play does.

    :func:`folder_for` keeps a loaded mod's own folder name, and Play has to:
    the mods folder holds it under that name, and its switches and its link
    are keyed on it.  An export goes somewhere else, to be shared or put on
    mod.io, and a mod renamed here is exported under its new name -- its
    folder, and the zip, which is named after the folder.  The name was the
    one thing that set neither, and nothing else on the Export tab could.
    Not renamed, it keeps the folder it came in, which its author may have
    numbered to place it in the load order.  A mod bundled inside another
    keeps its place in it.
    """
    if "/" in (origin or "") or not (renamed(loadout, origin) or given_a_new_mod_id(loadout, origin)):
        return folder_for(loadout, origin)
    return mod_folder_name(for_mod(loadout, origin))


def kept_by_mods_inside(loadout: Loadout, origin: str) -> frozenset:
    """The files of ModOps that the mods bundled inside ``origin`` keep
    beside their patches, each by its path from the top of ``origin``'s
    folder, in lower case.

    What a mod ships besides its patch is copied across when it is written,
    and a mod bundled inside it is part of what it ships: all but that mod's
    own patch, which that mod's export writes.  The same has to hold for the
    files that patch includes, which that export writes too.  Copied, the
    file as the mod shipped it went over the one just written; and that one
    is read, where before an export nothing read it.
    """
    if is_own_mod(origin):
        return frozenset()
    here = folder_for(loadout, origin).lower() + "/"
    found: set = set()
    for layer in loadout.layers:
        if layer.origin == origin:
            continue
        inside = folder_for(loadout, layer.origin).lower() + "/"
        if inside.startswith(here):
            found.update(
                inside[len(here):] + named.lower() for named in loadout.files_of(layer.origin)
            )
    return frozenset(found)


def verify_changes(index, loadout: Loadout) -> List[VerifyResult]:
    """Re-check every pending change against the source asset.

    Catches paths that no longer resolve, that match more than one node, or whose
    original value has drifted -- all of which would produce a ModOp that silently
    does nothing in game.
    """
    results: List[VerifyResult] = []
    cache: dict = {}
    # Deliberately the shipped asset, never the effective one: a workspace's
    # asset_element already has this mod applied, and checking a change against
    # its own result makes every replace look like it has drifted.
    source = getattr(index, "base_asset_element", None) or getattr(
        index, "asset_element", None
    )
    layered = getattr(index, "asset_before", None)
    for change in loadout.active():
        root = cache.get(change.guid)
        if root is None:
            root = source(change.guid) if source is not None else None
            cache[change.guid] = root
        if root is None:
            results.append(
                VerifyResult(change, False, "asset %s not found in the index" % change.guid)
            )
            continue
        ok, message = _verify_one(root, change, index)
        if not ok and layered is not None:
            # A mod that edits what an earlier mod added is not broken; it is
            # only broken on its own.  Ask again against the document that mod
            # will really be handed.  Second, not first: this costs a rebuild
            # per asset, and almost every change passes on the shipped file.
            # The loadout's own mod as well: on a loadout it is written over
            # the values the mods in it leave, and checked against the shipped
            # file alone every such change read as one that would not apply.
            stacked = layered(change.guid, change.origin)
            if stacked is not None:
                ok, message = _verify_one(stacked, change, index)
                if not ok:
                    message += " (checked against the mods loaded before it)"
        results.append(VerifyResult(change, ok, message))
    return results


def _verify_one(root, change: Change, index):
    """Check a single change against the base asset, per op.

    Each op has a different notion of correct: a replace must find the value it
    expects, an add must find exactly one parent to append to, and a new asset
    must not collide with a GUID that already exists.
    """
    if change.op == changes_mod.OP_REPLACE:
        return verify(root, change.path, change.old_value)

    if change.creates_asset:
        # Creating a new asset alongside an existing one.
        new_guid = payload_guid(change.payload)
        if not new_guid:
            return False, "the new asset has no GUID"
        # Deliberately the base game only: the workspace also knows about assets
        # this mod creates, and a new asset obviously "exists" in that sense.
        in_base = getattr(index, "guid_in_base_game", None)
        taken = in_base(new_guid) if in_base else (
            index.guid_exists(new_guid) if index is not None else False
        )
        if taken:
            return False, "GUID %s already belongs to a base game asset" % new_guid
        return True, "ok"

    # Everything else has to resolve to exactly one node.
    matches = resolve(root, change.path)
    if len(matches) == 0:
        return False, "path matches nothing in the source asset"
    if len(matches) > 1:
        return (
            False,
            "path matches %d nodes; the ModOp would apply to all of them" % len(matches),
        )
    return True, "ok"


def payload_guid(payload: str) -> str:
    """The GUID a new asset's payload gives itself; empty when it names none
    or does not parse."""
    try:
        from lxml import etree

        root = etree.fromstring(payload.encode("utf-8"))
    except Exception:  # noqa: BLE001 - malformed payloads are reported by the caller
        return ""
    return (root.findtext("Values/Standard/GUID") or "").strip()


# What a line of a written document is part of: see :class:`Span`.
SPAN_OP = "op"            # an op kept as written, with its note
SPAN_GROUP = "group"      # the tag that opens a group kept as a group
SPAN_GROUP_END = "end"    # and the tag that closes it
SPAN_CHANGES = "changes"  # a ModOp the Studio writes for changes of its own
SPAN_UNREAD = "unread"    # an op carried because it could not be read
SPAN_INCLUDE = "include"  # an <Include> line, in the file it stands in
SPAN_ASIDE = "aside"      # a comment that is no op's note, where it stands


@dataclass
class Span:
    """One thing a written document holds, and the lines it is on.

    ``first`` and ``last`` are counted from 1, and take in the note above an
    op and the comment an op that is switched off is written in.  The blank
    line before each is nobody's.
    """

    kind: str
    first: int = 0
    last: int = 0
    # Which of the loadout's it is: an op or a group, an op that could not
    # be read, or the changes one ModOp of the Studio's is written for.
    op: Optional[changes_mod.Op] = None
    unread: Optional[changes_mod.Unread] = None
    changes: Tuple[Change, ...] = ()
    # Written inside a comment, switched off.
    off: bool = False
    # Which file of the mod it is in: "" for its patch.  See Written.files.
    file: str = ""


@dataclass
class Region:
    """The lines of a text that are one of the mod's other files: what the
    ``<Include>`` on ``line`` names, from ``first`` to ``last``, counted
    from 1.  ``first`` is a blank line of the file's own, for whoever shows
    the text to say on it which file this is.  ``depth`` is how many files
    it is inside, and ``within`` the file its line stands in."""

    file: str
    line: int = 0
    first: int = 0
    last: int = 0
    depth: int = 0
    within: str = ""


# For ``written(files=)``: one text, each file's ops after its line.
INLINE = "inline"


@dataclass
class Written:
    """A ModOps document, and what is where in it."""

    text: str = ""
    spans: List[Span] = field(default_factory=list)
    # The files the document's ``<Include>`` lines name, where the mod was
    # written as its files: each by its path from the top of the mod.
    files: Dict[str, str] = field(default_factory=dict)
    # Or, written as one text with each file's ops after the line that names
    # it (``INLINE``): which lines are which file's.
    regions: List[Region] = field(default_factory=list)


def build_modops_xml(loadout: Loadout) -> str:
    """:func:`written`, the text alone: the mod's ModOps in one file."""
    return written(loadout).text


_QUOTES_WRITTEN = {'"': "&quot;", "'": "&apos;"}
_QUOTES_READ = {"&quot;": '"', "&apos;": "'"}
_FILE_NAMED = re.compile(r"""(\bFile\s*=\s*)(["'])(.*?)\2""", re.S | re.I)


def file_named(line: str, stands_in: str, game: games.Game) -> str:
    """The file an ``<Include>`` line names where it stands in the file
    ``stands_in`` ("" for the patch), by its path from the top of the mod;
    "" where it names none.  Worked out from the two paths alone, as the
    game's loader does: from the top of the mod where the name starts with
    a slash, and from beside the file it stands in where it does not."""
    found = _FILE_NAMED.search(line)
    if found is None:
        return ""
    # What the attribute says, which is not how it is spelled: a file
    # called ``Items&Specialists.xml`` is written ``Items&amp;Specialists``.
    named = unescape(found.group(3), _QUOTES_READ).replace("\\", "/").strip()
    if not named:
        return ""
    here = stands_in or Path(game.asset_relpath).as_posix()
    if named.startswith("/"):
        return posixpath.normpath(named.lstrip("/"))
    return posixpath.normpath(posixpath.join(posixpath.dirname(here), named))


def _include_line(record, stands_in: str, game: games.Game) -> str:
    """The ``<Include>`` line ``record`` is, as it has to be written in the
    file ``stands_in`` to name the file it names: as its mod wrote it where
    that still does, and from the top of the mod where it does not.

    The patch is written at the game's own path whatever the mod called it,
    so a name written from where the mod's patch was may name nothing from
    there.
    """
    found = _FILE_NAMED.search(record.source)
    if found is None:
        return record.source
    if file_named(record.source, stands_in, game).lower() == record.includes.lower():
        return record.source
    return (
        record.source[:found.start(3)] + escape("/" + record.includes, _QUOTES_WRITTEN)
        + record.source[found.end(3):]
    )


def _laid_in_files(ordered, patch: str) -> Optional[List[str]]:
    """Which file of the mod each entry of ``ordered`` is written in, or
    None where the mod cannot be written as its files and goes in one.

    A file is written from the ops that say they are in it, and the game
    reads it where the line that names it stands.  So the two agree only
    while each file's ops stand together, straight after its line: this
    checks that they do, and nothing is written apart that would run in
    another order than the one the loadout holds.

    An op whose file no line names any more is written where it stands, in
    the file of what is before it.  A ModOp of the Studio's own has no file
    of its own: an exception to an op is in that op's, one between two ops
    of a file is in theirs, and one after a file's last op is in the file
    above, so that what is set in Fields is not written at the end of
    whichever file was named last.
    """
    lines = [record for _o, _k, _g, _b, record, _r in ordered
             if record is not None and record.includes]
    named = [record.includes for record in lines]
    if not lines:
        return None
    if len({name.lower() for name in named}) != len(named) or patch.lower() in (
        name.lower() for name in named
    ):
        return None
    known = set(named)

    def said(entry) -> Optional[str]:
        _order, _kind, _guid, block, record, raw = entry
        if block is not None:
            return None
        file = record.file if record is not None else raw.file
        return file if file in known else ("" if not file else None)

    # The last thing each file holds itself, by its place.
    last: Dict[str, int] = {}
    for entry in ordered:
        file = said(entry)
        if file is not None:
            last[file] = max(last.get(file, -1), entry[0])
    found: List[str] = []
    opened = [""]
    closed: set = set()

    def back_to(file: str) -> bool:
        if file not in opened:
            return False
        while opened[-1] != file:
            closed.add(opened.pop())
        return True

    before = None
    for entry in ordered:
        order, _kind, _guid, block, record, _raw = entry
        file = said(entry)
        if block is not None:
            if before != order:
                # Not an exception to the op before it: in the innermost
                # open file that has something still to come.
                at = next(
                    (name for name in reversed(opened) if last.get(name, -1) > order), ""
                )
                back_to(at)
        elif file is None:
            pass
        elif not back_to(file):
            return None
        found.append(opened[-1])
        if record is not None and record.includes:
            if record.includes in opened or record.includes in closed:
                return None
            opened.append(record.includes)
        before = order
    return found


def written(loadout: Loadout, files=False, layout: bool = True, as_held=frozenset()) -> Written:
    """Render the whole change set as a ModOps document for the loadout's game.

    ``files``: written as the mod's files, where it has more than its
    patch.  Each ``<Include>`` line kept as one (:attr:`Op.includes`) is in
    the document, and the ops of the file it names are in
    :attr:`Written.files` and not here.  Without it, and where the files
    cannot be told apart (:func:`_laid_in_files`), every op is in the one
    document and no such line is: the same ops in the same order.

    ``layout`` off: one blank line before each thing, whatever its file
    had.  For a few of a mod's ops shown on their own, which are no file.

    ``as_held``: the uids of ops that are ModOps the Studio wrote, standing
    as ops in a text of a mod's project (:func:`.readback.shown`).  Each is
    written as it is held, as :func:`_as_kept` writes one, and not laid out
    again, so that what is read is what was written.

    ``files=INLINE``: one document that says which of its lines are each
    file's (:attr:`Written.regions`).  Each such line is in it and the ops
    of the file it names follow it, which is the order they run in.  What
    the Mod file tab shows, and what is read again.

    With the text, which lines each op, group and ModOp of the Studio's own
    is on (:class:`Span`): what a view of the file goes by to
    say which op the caret is in, and what reading the text again goes by to
    know each op for the one the loadout holds.

    Every change becomes its own ``<ModOp>`` aimed at a single node -- a value to
    replace, a node to add to, a node to remove, or an asset to insert.  That is
    deliberately more verbose than the hand-written style of bundling an asset's
    edits into one big replace: narrow ModOps only touch what the mod actually
    changes, so two mods editing different parts of the same asset still stack.

    What is switched off is written as well, commented out where it would
    have been: see :func:`switched_off`.  Read back, it is the op it was,
    switched off; left out, it would be lost from the folder the mod is read
    back from.

    The comments are the author's notes, and nothing else is said: the
    modinfo has the mod's details and what it was built against, and what
    the Studio makes of each op is on the screen, where it is not out of
    date the moment the file is typed in.  So is the asset each ModOp is
    aimed at, which a heading of the Studio's own over each asset's ModOps
    once said here too.
    """
    game = games.get(loadout.game)
    lines: List[str] = ['<?xml version="1.0" encoding="UTF-8"?>']

    # Imported here rather than at the top: keptops reads the translator, the
    # translator reads this module, and only one of the three can go first.
    from . import keptops

    kept, covered = keptops.exportable(loadout)
    # The ops the Studio could not read go out exactly as they arrived. The
    # change list shows nothing for them because they produced no changes,
    # which is a fact about the Studio's reading and not about the mod: the
    # game still applies them, and the mod written back has to do in game
    # what the one that was opened did.
    unread = list(loadout.unread)
    # What is switched off, which is written too.
    shelved_ops, shelved_changes = shelved(loadout, covered)
    shelved_uids = {record.uid for record in shelved_ops}
    lines.append("<ModOps>")

    # Switched-off changes are kept by the loadout and left out of the mod, and
    # so are the ones an op is standing in for -- writing both would make the
    # edit twice.
    changes = [
        change for change in loadout.active() if change.key not in covered
    ]
    # The ModOps the Studio wrote for changes made in Fields, by uid, and how
    # many changes each one is written for now: every one of those is among
    # what is written, on or off.
    narrow = {record.uid: record for record in loadout.narrow}
    written_as: Dict[str, int] = {}
    if narrow:
        for change in [*changes, *shelved_changes]:
            if change.written_as:
                written_as[change.written_as] = written_as.get(change.written_as, 0) + 1
    guids = []
    for change in [*changes, *shelved_changes]:
        if change.guid not in guids:
            guids.append(change.guid)

    # Grouped by the op each change came from and then by asset, rather than by
    # asset alone. With one mod being authored every change came from nowhere in
    # particular and this is exactly the old grouping; with a mod that was read
    # in it keeps its ops apart, which is what lets a kept op sit between two of
    # them. Removals still lead within a block -- that is a property of one op's
    # own changes, and it is the same reading :meth:`Translator._reproduces`
    # verifies.
    seen: dict = {}
    for change in changes:
        seen.setdefault((change.order, change.guid), []).append(change)
    # The switched-off ones of each block, beside it and never in it: what is
    # written for the game is worked out from the changes that are on alone,
    # so it is the file it would be with nothing switched off in it.
    aside: dict = {}
    for change in shelved_changes:
        aside.setdefault((change.order, change.guid), []).append(change)
        seen.setdefault((change.order, change.guid), [])
    aside = {where: changes_mod.emit_order(block) for where, block in aside.items()}
    place = {guid: position for position, guid in enumerate(guids)}
    blocks: List[Tuple[int, str, List[Change]]] = sorted(
        (
            (order, guid, changes_mod.emit_order(block))
            for (order, guid), block in seen.items()
        ),
        key=lambda entry: (entry[0], place.get(entry[1], 0)),
    )
    # Always a block per asset.  *Group ModOps by asset*, unticked, ran an
    # op's blocks into one, which wrote the same ModOps in the same order
    # but for the headings it left out -- and two assets' adds at the same
    # path as one ModOp, aimed at the first of them.

    # And a kept op goes back where it was, not at the front. Writing every
    # search first was tidy and wrong: a search that ran after a named op saw
    # that op's work and did less because of it, so hoisting it made the edit
    # twice.
    # Three shapes share this list: an asset block carries `block`, a kept op
    # carries `record`, an op the Studio could not read carries `raw` -- each
    # with the other two empty. Spelled out because inference takes its type
    # from the first line alone and then calls the others a mistake. The
    # unread ops sit where they sat too: they patch the same changing
    # document as everything around them, and moving one would change what
    # the ops after it find.
    ordered: List[
        Tuple[
            int,
            int,
            str,
            Optional[List[Change]],
            Optional[changes_mod.Op],
            Optional[changes_mod.Unread],
        ]
    ] = [
        (order, 1, guid, block, None, None)
        for order, guid, block in blocks
        if block or aside.get((order, guid))
    ]
    ordered.extend(
        (record.order, 0, "", None, record, None) for record in kept if not record.includes
    )
    ordered.extend(
        (record.order, 0, "", None, record, None) for record in shelved_ops
        if not record.includes
    )
    ordered.extend((entry.order, 0, "", None, None, entry) for entry in unread)
    # The comments that are no op's note, each where it stands.
    ordered.extend(
        (record.order, 0, "", None, record, None) for record in loadout.ops if record.aside
    )
    if files:
        # The lines that name the mod's other files, each in its place.  A
        # line is not something to switch off: its file's ops are the mod's
        # whether or not it is.
        ordered.extend(
            (record.order, 0, "", None, record, None) for record in loadout.ops
            if record.includes
        )
    ordered.sort(key=lambda entry: (entry[0], entry[1]))
    patch = Path(game.asset_relpath).as_posix()
    laid = _laid_in_files(ordered, patch) if files else None
    if files and laid is None:
        ordered = [entry for entry in ordered if entry[4] is None or not entry[4].includes]
    # Each file as it is written, the patch under "", and the one being
    # written now.
    docs: Dict[str, List[str]] = {"": lines}
    at_file = ""

    # The groups that are open where the writing has got to, outermost first:
    # a group kept as a group with ops in it is its own tag, and each thing
    # inside it says which groups it sits in.  See Op.scope.
    scopes = {held.uid: held for held in loadout.ops if held.scope}
    opened: List[str] = []
    # The groups switched off, whose two tags are written switched off too,
    # each in a comment of its own: so the file says the group is off, and
    # reads back so (see modops._switched_off_groups).  Not one with
    # anything inside it that is written live, which only runs as the game
    # has it inside the group: that one is written as a group that is on.
    live_in: set = set()
    for entry in ordered:
        one, unread_one = entry[4], entry[5]
        if unread_one is not None:
            live_in.update(unread_one.within)
        elif one is not None and not (
            one.aside or one.scope or one.includes or one.uid in shelved_uids
        ):
            live_in.update(one.within)
    off_groups = {
        uid for uid, group in scopes.items()
        if not group.active and not group.aside and not group.includes and uid not in live_in
    }

    # Each span as it is written, by where in ``lines`` it starts and ends:
    # made lines of the document once all of it is there.
    spans: List[Span] = []

    def spanned(kind: str, start: int, **what) -> None:
        """What was put in ``lines`` from ``start`` on is one ``kind``."""
        if len(lines) > start:
            spans.append(Span(kind, start, len(lines) - 1, file=at_file, **what))

    def turn_to(file: str) -> None:
        """Go on writing in ``file``, with every group here closed first:
        a group is its tags, and they are in one file."""
        nonlocal lines, at_file
        if file == at_file:
            return
        step_into(())
        if files != INLINE:
            lines = docs.setdefault(
                file, ['<?xml version="1.0" encoding="UTF-8"?>', "<ModOps>"]
            )
        at_file = file

    def step_into(chain) -> str:
        """Close and open groups until the open ones are ``chain``; the
        indent of what is written there."""
        chain = [uid for uid in chain if uid in scopes]
        while opened and opened != chain[: len(opened)]:
            gone = scopes[opened.pop()]
            start = len(lines)
            tag = ["  " * (len(opened) + 1) + _closing(gone.source)]
            off = gone.uid in off_groups
            lines.extend(switched_off(tag) if off else tag)
            spanned(SPAN_GROUP_END, start, op=gone, off=off)
        for uid in chain[len(opened):]:
            # Opened here only when its own entry has not been: a group whose
            # op is out of place still has to be round what is in it.
            start = len(lines)
            tag = ["  " * (len(opened) + 1) + _opening(scopes[uid].source)]
            off = uid in off_groups
            lines.extend(switched_off(tag) if off else tag)
            spanned(SPAN_GROUP, start, op=scopes[uid], off=off)
            opened.append(uid)
        return "  " * (len(opened) + 1)

    def deeper(written: List[str], indent: str) -> List[str]:
        """One-line comments, moved in to where they stand."""
        return [indent + line.lstrip() for line in written]

    for number, (position, _kind, guid, asset, record, raw) in enumerate(ordered):
        if laid is not None:
            turn_to(laid[number])
        # As many blank lines before it as its file had; one before what
        # the Studio writes itself.
        blank = [""] * (
            1 if not layout
            else record.gap if record is not None else raw.gap if raw is not None else 1
        )
        if record is not None and record.aside:
            indent = step_into(record.within)
            lines.extend(blank)
            start = len(lines)
            # As it was written, lines and all, moved in to where it stands.
            lines.append(indent + record.source.strip())
            spanned(SPAN_ASIDE, start, op=record)
            continue
        if record is not None and record.includes:
            indent = step_into(())
            lines.extend(blank)
            start = len(lines)
            lines.extend(deeper(_notes(record.note), indent))
            lines.extend(_verbatim_lines(_include_line(record, at_file, game), indent))
            spanned(SPAN_INCLUDE, start, op=record)
            if files == INLINE:
                # The first line of what it names: see Region.
                lines.append("")
                continue
            # Written, though nothing may be in it: the line names it.
            docs.setdefault(
                record.includes, ['<?xml version="1.0" encoding="UTF-8"?>', "<ModOps>"]
            )
            continue
        if record is not None and record.scope:
            indent = step_into(record.within)
            lines.extend(blank)
            start = len(lines)
            if record.uid in off_groups:
                lines.extend(switched_off([*_notes(record.note), indent + _opening(record.source)]))
            else:
                lines.extend(deeper(_notes(record.note), indent))
                lines.append(indent + _opening(record.source))
            spanned(SPAN_GROUP, start, op=record, off=record.uid in off_groups)
            opened.append(record.uid)
            continue
        indent = step_into(record.within if record is not None else raw.within if raw is not None else ())
        lines.extend(blank)
        start = len(lines)
        if record is not None:
            off = record.uid in shelved_uids
            if record.uid in as_held:
                # A ModOp of the Studio's, as the export writes one.
                own = _as_held(record)
            elif off:
                own = [*_notes(record.note), *_verbatim_lines(record.source)]
            else:
                own = [*deeper(_notes(record.note), indent), *_verbatim_lines(record.source, indent)]
            lines.extend(switched_off(own) if off else own)
            spanned(SPAN_OP, start, op=record, off=off)
            continue
        if raw is not None:
            lines.extend(deeper(_notes(raw.note), indent))
            lines.extend(_verbatim_lines(raw.source, indent))
            spanned(SPAN_UNREAD, start, unread=raw)
            continue
        # Past the `continue` this entry is an asset block, so it has one.
        assert asset is not None
        beside = aside.get((position, guid), [])
        for held, op in _ops_of(asset, game, True):
            start = len(lines)
            lines.extend(_as_kept(held, op, narrow, written_as))
            spanned(SPAN_CHANGES, start, changes=tuple(held))
        # After the block's own, as ops of their own: each is the op it would
        # be were it on, among the others that are off.
        for held, op in _ops_of(beside, game, True):
            start = len(lines)
            lines.extend(switched_off(_as_kept(held, op, narrow, written_as)))
            spanned(SPAN_CHANGES, start, changes=tuple(held), off=True)

    step_into(())
    # Where in its document each entry of a file's lines starts, counted
    # from 1: an entry is a line but for an op's own text where that runs
    # over lines.
    starts: Dict[str, List[int]] = {}
    for name, of_file in docs.items():
        of_file.append("")
        of_file.append("</ModOps>")
        starts[name], at = [], 1
        for line in of_file:
            starts[name].append(at)
            at += line.count("\n") + 1
    for span in spans:
        where = "" if files == INLINE else span.file
        ends = docs[where][span.last]
        span.first, span.last = (
            starts[where][span.first], starts[where][span.last] + ends.count("\n")
        )
    return Written(
        "\n".join(docs[""]) + "\n", spans,
        {name: "\n".join(of_file) + "\n" for name, of_file in docs.items() if name},
        _regions(spans) if files == INLINE else [],
    )


def _regions(spans: List[Span]) -> List[Region]:
    """Which lines of a text written ``INLINE`` are each file's: from the
    line after the one that names it to the last line of anything in it,
    or in a file it names in its turn."""
    named_in = {
        span.op.includes: span.file for span in spans
        if span.kind == SPAN_INCLUDE and span.op is not None
    }

    def chain(file: str) -> List[str]:
        found: List[str] = []
        while file and file not in found:
            found.append(file)
            file = named_in.get(file, "")
        return found

    ends: Dict[str, int] = {}
    for span in spans:
        for file in chain(span.file):
            ends[file] = max(ends.get(file, 0), span.last)
    return [
        Region(
            file=span.op.includes, line=span.last, first=span.last + 1,
            last=max(ends.get(span.op.includes, 0), span.last + 1),
            depth=len(chain(span.op.includes)) - 1, within=span.file,
        )
        for span in spans if span.kind == SPAN_INCLUDE and span.op is not None
    ]


def _opening(group: str) -> str:
    """The tag that opens a group held as its own tag with nothing in it."""
    text = (group or "").strip()
    if text.endswith("/>"):
        return text[:-2].rstrip() + ">"
    closing = text.rfind("</")
    return text[:closing] if closing > 0 else text


def _closing(group: str) -> str:
    """And the tag that closes it, spelled as its author spelled the group."""
    named = re.match(r"<\s*([^\s/>]+)", (group or "").strip())
    return "</%s>" % (named.group(1) if named else "Group")


def ops_of(block: List[Change], game: games.Game) -> List[List[str]]:
    """One block's changes as the ModOps they are written as, bare: what
    reading asks, to know whether changes it made are the op they came from."""
    return [op for _held, op in _ops_of(block, game, False)]


def _ops_of(
    asset: List[Change], game: games.Game, noted: bool
) -> List[Tuple[List[Change], List[str]]]:
    """One block's changes as the ModOps they are written as, an op apiece
    with the changes it is written for; ``noted``, each under its note."""
    return [
        (held, [*(line for note in notes for line in _notes(note)), *op] if noted else op)
        for held, notes, op in written_for(asset, game)
    ]


def _as_kept(
    held: List[Change], lines: List[str], narrow: Dict[str, changes_mod.Op],
    written_as: Dict[str, int],
) -> List[str]:
    """The lines of one ModOp of the Studio's, ``lines`` as its changes
    render: as its op in :attr:`Loadout.narrow` has it, where there is one
    written for exactly these changes, and otherwise as they render.

    The op is the record (:mod:`.narrow`), so where there is one it is what
    is written.  Where its changes have moved since it was written, and no
    step has been taken to write it again, it is not what they say, and they
    are what is written: a loadout read by a tool, or made by a test, has
    changes and none of these.  So is one written in another game's form
    than the loadout's, whose game was changed since: its opening tag says.
    """
    record = narrow.get(held[0].written_as) if held else None
    if (
        record is None
        or record.made != len(held)
        or written_as.get(record.uid) != len(held)
        or any(change.written_as != record.uid for change in held)
    ):
        return lines
    # Its opening tag, which says which game's form it is in.
    if (INDENT + record.source).split("\n")[0] not in lines:
        return lines
    return _as_held(record)


def _as_held(record: changes_mod.Op) -> List[str]:
    """A ModOp of the Studio's (:attr:`Loadout.narrow`) as it is written:
    under each of its notes, a comment apiece, as the changes it is written
    for put each one's above it (:func:`_ops_of`), and then its text as it
    is held.  The one way such a ModOp is written: in the export
    (:func:`_as_kept`), and in a text of a mod's project (``as_held`` in
    :func:`written`), which is read back as what the export wrote."""
    return [*_notes(record.note), *(INDENT + record.source).split("\n")]


def written_for(
    asset: List[Change], game: games.Game
) -> List[Tuple[List[Change], List[str], List[str]]]:
    """One block's changes as the ModOps the Studio writes for them: for
    each, the changes it is written for, the notes it is written under, in
    order and each as it is written, and its lines.  What :mod:`.narrow`
    keeps of each, and what :func:`written` writes."""
    ops: List[Tuple[List[Change], List[str], List[str]]] = []
    swaps = _swaps(asset)
    inserts = {insert.key for insert in swaps.values()}
    together = _side_by_side(asset)
    written: set = set()
    for change in asset:
        if change.key in inserts or change.key in written:
            continue  # written as the replace its removal stands for
        if change.key in swaps:
            insert = swaps[change.key]
            ops.append(([change, insert], [insert.note], _render_swap(change, insert, game)))
            continue
        run = together.get(change.key)
        if run is not None:
            written.update(other.key for other in run)
            ops.append((list(run), [one.note for one in run], _render_together(run, game)))
            continue
        ops.append(([change], [change.note], _render_modop(change, game)))
    return [(held, _as_noted(notes), op) for held, notes, op in ops]


def _as_noted(notes: List[str]) -> List[str]:
    """Notes as each is written above its ModOp: on one line with single
    spaces, and none that writes nothing."""
    said = [" ".join((note or "").split()) for note in notes]
    return [one for one in said if _comment_safe(one)]


def _side_by_side(block: List[Change]) -> dict:
    """``{the first one's key: the run}`` for nodes one op put beside another.

    An ``addNextSibling`` puts what it carries immediately after the node it
    names, so two of them aimed at the same node land the second one *first*:
    written back as one ModOp each, a pair of assets comes out in the file
    backwards.  *Harborlife* adds ninety triggers in a single op, two of them
    sharing a GUID, which is how the reversal showed at all -- and measured
    with xmltest for both games.

    A block is one op's changes, so a run of them here was one op in the mod
    and goes back out as the one op it was -- in the order the op had them,
    which is not the order the changes list shows them in.  ``addPrevSibling``
    keeps its order either way and is written together too: it is the same op,
    and one is what the mod had.
    """
    beside = (changes_mod.OP_ADD_NEXT, changes_mod.OP_ADD_PREV)
    runs: dict = {}
    run: List[Change] = []
    for change in [*block, None]:
        starts: List[Change] = []
        if change is not None and change.op in beside:
            if not run or (run[0].op, run[0].path) == (change.op, change.path):
                run.append(change)
                continue
            # One that does not join starts a run of its own: it could hold one.
            starts = [change]
        if len(run) > 1:
            # Filed under the one the block reaches first, written in the order
            # the op had them: the two are not the same change.
            runs[run[0].key] = sorted(run, key=lambda one: one.spot)
        run = starts
    return runs


def _swaps(block: List[Change]) -> dict:
    """``{removal key: insert}`` for each node of an asset's identity swapped whole.

    Written as a removal and an insertion, the game loses the asset in either
    order: removal first, nothing finds it by its GUID to insert into; insertion
    first, the removal's path matches the old node and the new one. One
    ``replace`` does both, measured with xmltest for both games -- *New
    Docklands* merges a positional ``<Item/>`` into a wharf's ``Values``, which
    only a swap of the whole node can say.
    """
    swaps: dict = {}
    for removal in block:
        path = removal.path.rstrip("/")
        if removal.op != changes_mod.OP_REMOVE or path not in changes_mod.IDENTITY_PATHS:
            continue
        taken = {insert.key for insert in swaps.values()}
        insert = next(
            (c for c in block if c.key not in taken and c is not removal and changes_mod.puts_back(path, c)),
            None,
        )
        if insert is not None:
            swaps[removal.key] = insert
    return swaps


def _render_swap(removal: Change, insert: Change, game: games.Game) -> List[str]:
    out: List[str] = []
    out.append("  " + game.modop_open_tag(removal.guid, removal.path, changes_mod.OP_REPLACE))
    out.extend(_indent_payload(insert.payload))
    out.append("  </ModOp>")
    return out


def _verbatim_lines(source: str, indent: str = "  ") -> List[str]:
    """A stored ModOp -- a kept op's, or one the Studio could not read -- laid out.

    Its own text, only re-indented: the point of keeping the op verbatim is
    that what comes out is what the mod said, so this reformats and never
    rewrites.  A body that will not re-parse goes out on one line rather than
    not at all.
    """
    if _has_a_value_over_lines(source):
        # Indenting moves the line breaks inside the value with the rest: it
        # goes out as it is held.
        return _laid_out(source, indent)
    try:
        from lxml import etree

        node = etree.fromstring(source.encode("utf-8"))
        etree.indent(node, space="  ")
        text = etree.tostring(node, encoding="unicode", pretty_print=True)
    except Exception:  # noqa: BLE001 - an op is never worth losing to layout
        text = source
    return _laid_out(text, indent)


def laid_out_op(source: str) -> str:
    """A stored ModOp as the mod file lays it out, at no indent: its own
    text, a line for each element.  For a box that shows one op to type
    over, where the op as stored is often all on one line."""
    return "\n".join(_verbatim_lines(source, ""))


def _indent_payload(payload: str, indent: str = "    ") -> List[str]:
    """Re-indent a stored XML fragment to sit inside a ModOp."""
    return _laid_out(payload, indent)


def _laid_out(text: str, indent: str) -> List[str]:
    """XML indented line by line -- unless one of its values runs over lines.

    Indenting every line is layout only while every line break is layout. A
    value written over several lines -- *Genshin cards* binds a key to thirty
    console commands, one to a line -- has its line breaks *inside* the text,
    and indenting those lines, or dropping a blank one, rewrites the value.
    Such a fragment is put in place by its first line and otherwise left as
    it was.

    So is one whose value only starts or ends on a line of its own: the
    loader keeps a value's whitespace, and appends an op's own text as it
    stands. *Extended Enbesa* adds ``;Africa`` on a line of its own, and
    indented again, the region list in game came out with two more spaces.

    And one holding a comment that runs over lines: the comment is the
    author's, and goes out line for line as it is held.
    """
    lines = text.strip().splitlines()
    if not lines:
        return []
    if _has_a_value_over_lines(text):
        return [indent + lines[0], *lines[1:]]
    return [indent + line for line in lines if line.strip()]


def _has_a_value_over_lines(text: str) -> bool:
    from lxml import etree

    try:
        root = etree.fromstring(("<r>%s</r>" % text).encode("utf-8"))
    except etree.XMLSyntaxError:
        return False
    # What follows a comment is a value as much as what follows an element: a
    # value written with a comment on each line is in the tails of those
    # comments.  A comment's own text is nobody's value, but it is carried
    # with the op as its author wrote it, and one that runs over lines has
    # its line breaks inside what it says: indented again, or a blank line of
    # it dropped, the op written is not the op held.
    return any(
        "\n" in (words or "") and (words or "").strip()
        for node in root.iter()
        for words in ((node.text, node.tail) if isinstance(node.tag, str) else (node.tail,))
    ) or any(
        "\n" in (node.text or "") for node in root.iter() if not isinstance(node.tag, str)
    )


def _render_together(run: List[Change], game: games.Game) -> List[str]:
    """One ModOp for a run of changes the mod wrote as one.  See :func:`_side_by_side`."""
    out: List[str] = []
    out.append("  " + game.modop_open_tag(run[0].guid, run[0].path, run[0].op))
    for change in run:
        out.extend(_indent_payload(change.payload))
    out.append("  </ModOp>")
    return out


def _summary(change: Change) -> str:
    """What a change is, as an export once said in a line above its ModOp:
    see :func:`said_before_the_mark`."""
    if change.op == changes_mod.OP_REPLACE:
        return "%s: %s -> %s" % (
            change.tag,
            change.old_value if change.old_value else "(empty)",
            change.new_value if change.new_value else "(empty)",
        )
    return "%s: %s" % (change.op, change.summary or change.tag or change.path)


def _swap_summary(removal: Change) -> str:
    return "replace: %s" % (removal.tag or removal.path)


def said_before_the_mark(change: Change) -> Tuple[str, ...]:
    """The lines an export made before :data:`changes.STUDIO_SAYS_MARK` put
    above ``change``, as a comment is read back: on one line, single spaces.

    Nothing told those from an author's, so one of them is the note such a
    file's change comes back with -- and is known for the Studio's by being,
    word for word, what the Studio says of that change.
    """
    return tuple(
        " ".join(_comment_safe(said).split())
        for said in (_summary(change), _swap_summary(change))
    )


def _render_modop(change: Change, game: games.Game) -> List[str]:
    out: List[str] = []
    open_tag = game.modop_open_tag(change.guid, change.path, change.op)

    if change.op == changes_mod.OP_REMOVE:
        # A remove carries no body; the game deletes whatever Path matched.
        out.append("  " + open_tag + "</ModOp>")
        return out

    out.append("  " + open_tag)
    if change.op == changes_mod.OP_REPLACE:
        out.append("    <%s>%s</%s>" % (change.tag, escape(change.new_value), change.tag))
    else:
        out.extend(_indent_payload(change.payload))
    out.append("  </ModOp>")
    return out


def build_modinfo(loadout: Loadout) -> str:
    """A modinfo.json in the shape the loadout's target game expects."""
    return modinfo.build(loadout)


def carried_files(source: Path, game: str) -> List[Path]:
    """Everything a mod ships that an export does not write for itself.

    A mod is more than its ``assets.xml``.  It has its eleven ``texts_*.xml``,
    it may patch ``templates.xml`` or replace a ``.cfg``, an ``.ifo``, a
    ``.fc``, and a mod bundled inside it brings its own.  534 of the 854 Anno
    1800 mods on mod.io ship something besides the two files an export writes.

    The two it writes are left out at any depth, which is also what makes a
    bundled mod come out right: its own ``assets.xml`` is skipped here and
    written by its own export, while everything else of its travels with the
    host.  Dot-folders are tooling and never part of a mod -- the same rule
    the publisher's zip follows.
    """
    source = Path(source)
    if not source.is_dir():
        return []
    # Compared without case, because the game reads these names without case
    # and mods are written accordingly: *Additional powerplants* calls its
    # patch `Assets.xml`, and matching exactly meant copying it over the one
    # the export had just written -- 2,405 lines of mod replaced by 17.
    patch = tuple(part.lower() for part in Path(games.get(game).asset_relpath).parts)
    # Walked the long way round, because a mod is deep enough on its own to
    # put a file past what Windows will open by its plain name -- and a file
    # this cannot see is a file the export would quietly leave behind.
    top = disk.reachable(source)
    found = []
    for here, folders, names in os.walk(top):
        folders[:] = sorted(f for f in folders if not f.startswith("."))
        spot = os.path.relpath(here, top)
        for name in sorted(names):
            inside = Path(name) if spot == "." else Path(spot) / name
            lowered = tuple(part.lower() for part in inside.parts)
            if any(part.startswith(".") for part in inside.parts):
                continue
            if lowered[-len(patch):] == patch:
                continue
            if lowered[-1] in modfolder.MODINFO:
                continue
            found.append(inside)
    return sorted(found)


def write_mod_folder(
    loadout: Loadout,
    parent_dir: Path,
    xml_text: str,
    folder_name: Optional[str] = None,
    carry_from=None,
    trouble: Optional[List[str]] = None,
    carry_only: Optional[Sequence[str]] = None,
    files: Optional[Dict[str, str]] = None,
    leave=(),
) -> Path:
    """Create ``<parent>/[Mod] <name>/`` with the assets patch and modinfo.json.

    ``carry_from`` is the folder this mod was read out of, when it was read
    out of one.  What it ships besides the patch is copied across, so that an
    export is the mod rather than the part of it the Studio models -- see
    :func:`carried_files`.  Writing back over the mod's own folder, where
    those files already sit, copies nothing: it is the same file.

    ``carry_only``, a list of suffixes, copies only the files that end in one
    of them.  The app never passes it; the census harness does, because it
    keeps an export of every mod that did not round-trip, and the textures and
    models a mod ships are most of its size and nothing it compares.

    ``trouble`` collects a line per file that could not be copied.  A mod's
    hundred-odd side files are not worth losing the export over, and losing
    one quietly is not worth it either, so the caller is handed both the
    export and the list.  Without one, a failure is raised as it always was.

    ``files`` are the mod's other files of ModOps, each by its path from
    the top of the mod: what the patch's ``<Include>`` lines name
    (:attr:`Written.files`).  Written after the copies, over the file each
    was read from.  ``leave`` are files not to copy, as
    :func:`kept_by_mods_inside` has them: another mod's export writes them.

    Returns the mod folder that was written.
    """
    game = games.get(loadout.game)
    name = folder_name or mod_folder_name(loadout)
    root = Path(parent_dir)
    for part in str(name).replace("\\", "/").split("/"):
        root = root / kept_folder_name(part)
    asset_file = root / game.asset_relpath
    asset_file.parent.mkdir(parents=True, exist_ok=True)
    asset_file.write_text(xml_text, encoding="utf-8")
    (root / "modinfo.json").write_text(build_modinfo(loadout), encoding="utf-8")
    if carry_from:
        source = Path(carry_from)
        for inside in carried_files(source, loadout.game):
            if carry_only is not None and inside.suffix.lower() not in carry_only:
                continue
            if inside.as_posix().lower() in leave:
                continue
            came_from, goes_to = source / inside, root / inside
            try:
                if came_from.resolve() == goes_to.resolve():
                    continue
            except OSError:
                pass
            try:
                os.makedirs(disk.reachable(goes_to.parent), exist_ok=True)
                shutil.copy2(disk.reachable(came_from), disk.reachable(goes_to))
            except OSError as exc:
                if trouble is None:
                    raise
                trouble.append("%s: %s" % (inside, exc))
    for named, text in (files or {}).items():
        its_file = root / named
        if root.resolve() not in its_file.resolve().parents:
            # A path that climbs out of the mod names nothing of its own.
            continue
        os.makedirs(disk.reachable(its_file.parent), exist_ok=True)
        Path(disk.reachable(its_file)).write_text(text, encoding="utf-8")
    # What the assets it adds are called in game, into every language's texts
    # file -- after the copies, so a texts file the mod already ships keeps
    # its own entries and gets the Studio's at its end.
    texts.write_names(loadout, root, trouble)
    # And the Lua of a mod written here, from wherever its author keeps it.
    scripts.carry(loadout, root, trouble)
    return root
