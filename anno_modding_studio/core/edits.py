"""Your edits to somebody else's mod, as its generated copy holds them.

You can change any mod's ops here: type over one, take one out, switch one
off, put one of yours among them.  For a mod you wrote, the edit goes into
its file.  For somebody else's, the loadout holds their mod with your edits
in it, and the library's copy of it stays as its author shipped it: Play
writes a generated copy (:mod:`core.generated`) in which their files carry
your edits as :mod:`core.splicing` writes them -- their op switched off
with ``Skip``, yours after it -- and links that in.  When they update the
mod, the loadout reads the new version and keeps your edits as it always
has (:meth:`core.changes.Loadout.as_before`), and the next Play writes
them into a copy of the new version.  See docs/proposals/patches-proposal.md,
phase 2.

What the edits are is worked out by comparing two writings of the mod:
the loadout's, and the same mod read afresh from the library's copy, with
nothing of yours in it.  Each is compared with the author's file; what the
fresh reading differs by is only how the Studio writes a mod -- an
``<Include>`` it writes out in full, say -- and is left out, so what is
written into their file is what you changed and nothing else.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Dict, List, Optional, Set

from . import build, disk, games, generator, inside, modops, reader, splicing
from .changes import OWN, Loadout, is_own_mod
from .i18n import tr


@dataclass
class Edited:
    """What a mod's generated copy holds of your edits: each file whose bytes
    they change, by its path from the top of the mod, and each run of them,
    for saying where (``{"file", "line", "within": [...], "skipped": [...],
    "lines": [...], "offsets": [...], "inserted": [...]}``, ``within`` the
    opening tag of each group of theirs it stands in, outermost first,
    ``lines`` the line each skipped op starts on and ``offsets`` where in
    the file, or for none where what is put in goes).  ``files`` are the
    mod's files of ModOps, every one compared, and ``shipped`` the mod read
    afresh with nothing of yours in it (:func:`as_shipped`).  ``trouble`` is
    why there are none where there should be."""

    written: Dict[str, bytes] = field(default_factory=dict)
    hunks: List[dict] = field(default_factory=list)
    files: List[str] = field(default_factory=list)
    shipped: Optional[Loadout] = None
    trouble: str = ""


def is_edit(one) -> bool:
    """Whether an op or a change a mod holds is an edit of yours to it:
    typed here, or one of its own switched off."""
    return one.authored or not one.active


def has_edits(loadout: Loadout, origin: str, keeper: Optional[str] = None) -> bool:
    """Whether the loadout holds ``origin`` other than as its files have it:
    an op of yours in it, a change of yours, one of its ops or changes
    switched off, one of its ops taken out.  Of those ``keeper`` keeps
    (:mod:`.inside`), given one.  Cheap: what is asked before working
    anything out."""
    if is_own_mod(origin):
        return False
    undecided = inside.keeper_for(loadout, origin) if keeper is not None else OWN
    if any(entry[0] == origin and inside.kept(entry, keeper, undecided) for entry in loadout.taken_out):
        return True
    return any(
        one.origin == origin and is_edit(one) and inside.kept(one, keeper, undecided)
        for held in (loadout.ops, loadout.changes.values()) for one in held
    )


def edited(loadout: Loadout) -> Set[str]:
    """Every mod the loadout holds other than as its files have it, as
    :func:`has_edits` has it, in one pass over the loadout."""
    found = {entry[0] for entry in loadout.taken_out}
    for held in (loadout.ops, loadout.changes.values()):
        found.update(one.origin for one in held if is_edit(one))
    found.discard("")
    return found


def count(loadout: Loadout, origin: str, keeper: Optional[str] = None) -> int:
    """How many edits of yours the loadout holds to ``origin``: its ops and
    changes you typed, and those of its own you switched off or took out.
    Of those ``keeper`` keeps, given one."""
    if is_own_mod(origin):
        return 0
    undecided = inside.keeper_for(loadout, origin) if keeper is not None else OWN
    return (
        sum(1 for entry in loadout.taken_out
            if entry[0] == origin and inside.kept(entry, keeper, undecided))
        + sum(1 for op in loadout.ops
              if op.origin == origin and not op.scope and (op.authored or not op.active)
              and inside.kept(op, keeper, undecided))
        + sum(1 for change in loadout.changes.values()
              if change.origin == origin and change.authored and not change.op_uid
              and inside.kept(change, keeper, undecided))
    )


def kept_in(loadout: Loadout, keeper: str) -> Dict[str, int]:
    """``{mod: how many}`` of the edits ``keeper`` keeps inside somebody
    else's ops (:mod:`.inside`), for each mod it keeps any to."""
    counted = {origin: count(loadout, origin, keeper) for origin in sorted(inside.theirs(loadout))}
    return {origin: n for origin, n in counted.items() if n}


def files_of(loadout: Loadout, origin: str) -> Dict[str, str]:
    """The mod as the loadout writes it, file by file from the top of it:
    its patch, and every file of ModOps the patch's ``<Include>`` names.
    Where a patch and the loadout's own mod both put ops of theirs at one
    place, the patch's come first (:func:`.inside.in_load_order`)."""
    view = generator.for_mod(loadout, origin)
    view.ops = inside.in_load_order(view.ops)
    whole = build.as_files(view)
    return {games.get(loadout.game).asset_relpath.as_posix(): whole.text, **whole.files}


def as_shipped(loadout: Loadout, origin: str, folder, index) -> Optional[Loadout]:
    """``origin`` read afresh from ``folder``, alone, at the settings the
    loadout reads it at: the mod with nothing of yours in it.  None where it
    cannot be read."""
    layer = loadout.layer(origin)
    if layer is None or index is None:
        return None
    report = reader.analyse(
        Path(folder), index, origin=origin,
        reading=modops.Reading(options={origin: dict(layer.options)}),
    )
    if report.error:
        return None
    alone = Loadout(game=loadout.game)
    alone.set_layer(replace(layer))
    for op in report.ops:
        alone.set_op(op)
    for change in report.changes:
        alone.set_change(change)
    alone.unread = list(report.unread(origin))
    return alone


def _runs(texts) -> bool:
    """Whether any of ``texts`` has a ModOp, group or include in it."""
    return any((splicing.scan(text) or splicing.Scanned(text="")).nodes for text in texts)


def work_out(loadout: Loadout, origin: str, folder, index, base=None,
             keeper: Optional[str] = None) -> Edited:
    """Your edits to ``origin`` as its generated copy holds them.

    ``folder`` is the mod in the library's copy -- for a mod bundled inside
    another, its folder inside the host -- and ``base`` what is already
    written over its files before the edits are, by path from the top of the
    mod: iModYourAnno's settings (:func:`core.imya.changed_files`).  Their
    files are read from ``base`` where it has them.  ``keeper``, given one,
    is the mod of yours whose edits alone are worked out
    (:func:`.inside.only_kept_by`); every keeper's otherwise, the patch's
    and then the loadout's own mod's.
    """
    if keeper is not None:
        loadout = inside.only_kept_by(loadout, origin, keeper)
    folder = Path(folder)
    base = dict(base or {})
    done = Edited()
    if index is None:
        done.trouble = tr("no game files are loaded to read it against")
        return done
    # By the paths the author's folder spells: the Studio writes a mod's
    # files by its own, and on a disk that tells case apart the author's are
    # the only ones there.
    ours = {disk.as_spelled(folder, rel): text for rel, text in files_of(loadout, origin).items()}
    plain = as_shipped(loadout, origin, folder, index)
    if plain is None:
        # A mod with nothing that runs, in its files or in the loadout -- a
        # box of mods with no patch of its own -- has nothing to splice.
        if not _runs(ours.values()) and not _runs(
            splicing.decode(base[rel] if rel in base else (folder / rel).read_bytes())[0]
            for rel in ours if rel in base or (folder / rel).is_file()
        ):
            return done
        done.trouble = tr("its folder in the library could not be read")
        return done
    done.shipped = plain
    unedited = {disk.as_spelled(folder, rel): text for rel, text in files_of(plain, origin).items()}
    done.files = list(ours)
    for rel, text in ours.items():
        raw = base.get(rel)
        if raw is None:
            path = folder / rel
            raw = path.read_bytes() if path.is_file() else None
        if raw is None:
            # A file of ModOps the mod does not ship: one of yours, written
            # whole, where there is anything in it to run.
            scanned = splicing.scan(text)
            if scanned is not None and scanned.nodes and text != unedited.get(rel):
                done.written[rel] = splicing.encode(text)
            continue
        theirs_text, bom = splicing.decode(raw)
        theirs, wanted = splicing.scan(theirs_text), splicing.scan(text)
        if theirs is None or wanted is None:
            continue
        before = splicing.scan(unedited[rel]) if rel in unedited else None
        kept = (splicing.edit_hunks(theirs, before, wanted) if before is not None
                else splicing.hunks(theirs, wanted))
        if not kept:
            continue
        done.written[rel] = splicing.encode(
            splicing.apply(theirs_text, [splice for hunk in kept for splice in hunk]), bom
        )
        for hunk in kept:
            skipped = [splice.node for splice in hunk if splice.kind == "skip" and splice.node is not None]
            first = skipped[0].start if skipped else hunk[0].at
            done.hunks.append({
                "file": rel,
                "line": splicing.line_of(theirs_text, first),
                "within": [theirs_text[group.start:group.head_end]
                           for group in splicing.around(theirs, first)],
                "skipped": [theirs_text[node.start:node.end] for node in skipped],
                "lines": [splicing.line_of(theirs_text, node.start) for node in skipped],
                "offsets": [node.start for node in skipped] or [first],
                "inserted": [splice.text for splice in hunk if splice.kind == "insert"],
            })
    return done
