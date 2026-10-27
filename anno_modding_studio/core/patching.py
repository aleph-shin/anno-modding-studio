"""Changing somebody else's mod without editing their file.

The Studio can edit a loaded mod directly, and for a mod you wrote yourself that
is exactly right.  For somebody else's it is a trap: the author releases version
1.7, you drop it in, and everything you did is gone -- because what you edited
was their file, and you have just replaced it.

The way the modding community solves this is a **patch mod**: a mod of your own,
named so that it loads immediately after the one it is about, holding your
changes as its own ModOps.  Update the original and your patch is still there.
It may need a tweak where the author moved something you were changing, but that
is a tweak rather than doing the work again.

Loading *immediately* after is the whole point, and two things make it happen.
The one that does the work is the patch's modinfo, which names the mod it loads
after by ModID -- ``LoadAfterIds`` for 1800, ``Dependencies.LoadAfter`` for 117 --
because that declaration is what the loader resolves, over whatever order it
would otherwise have sorted the two into.  See :func:`core.switches.order`.

The second is the name.  A patch called exactly the original's name plus a suffix
sorts directly after it and before anything else, under either game's sorting
rule, so it still lands in the right place if the declaration is ever ignored --
and it is what the mod reads as to anyone looking at the folder.
"""

from __future__ import annotations

from dataclasses import replace
from typing import List, Tuple

from . import modfolder
from .changes import Loadout, ModLayer
from .games import ANNO_117, ANNO_1800
from .i18n import join_list, tr

# Appended to the mod's folder name. Starts with a space, which is the lowest
# character anything is likely to be named with, so nothing sorts between them.
SUFFIX = " (my patch)"


def suggest_name(origin: str) -> str:
    """What to call the patch for ``origin``.

    A mod bundled inside another is named by where it lives, and a patch must
    not be: put inside that host's folder, updating the host would replace the
    folder and take the patch with it, which is the whole thing a patch exists
    to avoid.  So it takes the bundled mod's own name and stands on its own,
    with the declaration in its modinfo doing the ordering.
    """
    return modfolder.folder_name(origin or "mod") + SUFFIX


def loads_after(name: str, origin: str, game: str = ANNO_1800) -> bool:
    """Whether a folder called ``name`` would sort after ``origin``.

    What a patch falls back on if the declaration in its modinfo is ever
    ignored: named wrongly it still holds your changes and the mod it is
    about simply overwrites them again.

    Compared as written, as Anno 1800's loader does: capitals sort before
    ``[`` and ``[`` before lower case, so ``Zed`` is in front of ``[Gameplay]
    ...`` and lower-cased it looked as though it were behind. Anno 117 does
    not sort by folder at all, so no folder name is wrong there -- see
    :func:`sorts_before_its_mod` for what it does sort by.
    """
    if game == ANNO_117:
        return bool(name)
    return bool(name) and name > modfolder.folder_name(origin)


def sorts_before_its_mod(loadout: Loadout, layer: ModLayer) -> bool:
    """Whether a patch has come to sort in front of the mod it patches.

    By what the game sorts on before anything a mod declares
    (:func:`core.switches.base_key`): the folder's name in Anno 1800, which a
    rename here does not touch, and ``[Category] ModName`` in Anno 117, which
    it does. The patch still declares that it loads after its mod, and that
    is what the loader goes by -- measured in both games. This is the
    fallback going, said so that it is not lost without anyone knowing.
    """
    from . import switches

    host = loadout.layer(layer.patches)
    if host is None:
        return False

    def key(one: ModLayer) -> str:
        return switches.base_key(
            loadout.game, one.origin,
            {"category": one.category, "mod_name": one.name, "mod_id": one.mod_id},
        )

    return key(layer) < key(host)


def is_mine(loadout: Loadout, layer: ModLayer) -> bool:
    """Whether a loaded mod is one of yours that a project keeps, and so is
    edited directly: the mod the loadout carries on with
    (:attr:`Loadout.home`), a fork (:mod:`core.forking`), or a mod of yours
    made a project here (:attr:`ModLayer.kept`).  A patch is yours too, and
    asked about apart.

    A mod with your name on it that no project keeps is read like anybody
    else's until one does (section 5.5 of the projects and loadouts
    proposal): :func:`looks_mine` says it looks like yours, and
    :func:`without_a_project` that it has no project yet.
    """
    if loadout.home and layer.origin == loadout.home:
        return True
    return bool(layer.fork_of or layer.kept)


def looks_mine(loadout: Loadout, layer: ModLayer) -> bool:
    """Whether a loaded mod looks like one this user wrote.

    Two ways of telling, because neither is sufficient alone.  **By ModID**
    first: the Studio writes its own, so a mod that came back out of here is
    identifiable by something it controls rather than by a display name.  **By
    creator name** otherwise, which is what recognises a mod you wrote before
    you ever opened the Studio.

    Both need the answer to "who are you" to have been given -- see
    :meth:`ui.MainWindow.ensure_creator`.  With nothing to compare against this
    says no, which is the safe way round: a mod wrongly taken for somebody
    else's gets offered a patch it did not need, and a mod wrongly taken for
    yours gets edited in place and overwritten by its author's next release.

    A mod of yours a project keeps (:func:`is_mine`) is yours by that,
    whatever name is on it -- you said so when you opened it.
    """
    if is_mine(loadout, layer):
        return True
    mine_id = (loadout.own.mod_id or "").strip().lower()
    if mine_id and (layer.mod_id or "").strip().lower() == mine_id:
        return True
    return by_you(loadout, layer.creator)


def by_you(loadout: Loadout, creator: str) -> bool:
    """Whether ``creator``, a mod's author name, is one of yours: the one the
    loadout's own mod is published under, or another you have published
    under (:attr:`Loadout.aliases`).  Ignoring case, as a name typed again
    differently is still the same person."""
    said = (creator or "").strip().casefold()
    return bool(said) and said in {
        (name or "").strip().casefold() for name in (loadout.own.creator, *loadout.aliases)
    }


def without_a_project(loadout: Loadout, layer: ModLayer) -> bool:
    """Whether a loaded mod is yours by its name or ModID and no project
    keeps it: one you wrote before the Studio, or whose project is gone
    (section 5.5 of the projects and loadouts proposal).  Read like anybody
    else's until it is made one (:attr:`ModLayer.kept`)."""
    return not layer.patches and not is_mine(loadout, layer) and looks_mine(loadout, layer)


def edits_need_a_patch(loadout: Loadout, layer: ModLayer) -> bool:
    """Whether changes aimed at ``layer`` must go to a patch rather than into it.

    A mod bundled inside another that is not yours.  Its file is a copy inside
    somebody's host -- and for a shared piece, the one copy of several that the
    game happens to load.  A change written into it goes when that host is
    updated, stops loading without a word when another mod ships a newer copy,
    and may stop loading when an unrelated mod is added and the tie between
    equal copies falls the other way.  A patch loads after it by its ModID, so
    it follows whichever copy runs and outlives every host.  Where editing
    somebody's top-level mod in place is a choice worth offering, this is not.
    """
    return modfolder.is_bundled(layer.origin) and not layer.patches and not is_mine(loadout, layer)


def changed_directly(loadout: Loadout, origin: str) -> bool:
    """Whether a change to one of ``origin``'s ModOps is made to it in place.

    Yours, and your patches, always: into its own file.  Somebody else's
    only where a mod of yours is there to keep the edit (section 5.3 of the
    projects and loadouts proposal, :mod:`.inside`): while your patch for it
    is what changes go to -- and never a bundled one.  The window asks the
    rest (:meth:`ui.patches_controller.PatchesController.changed_directly`).  Their file is never written: the
    edit goes into the generated copy Play writes of it.  Anything that
    rewrites one of a mod's own ops asks this first: *Edit pattern* used to
    write into the mod the op came from whatever changes went to, so a patch
    made and picked was no protection (docs/proposals/patches-proposal.md,
    section 1).
    """
    if not origin:
        return True
    layer = loadout.layer(origin)
    if layer is None or layer.patches or is_mine(loadout, layer):
        return True
    if edits_need_a_patch(loadout, layer):
        return False
    patch = loadout.patch_for(origin)
    return patch is not None and loadout.editing_origin() == patch.origin


def free_origin(loadout: Loadout, wanted: str) -> str:
    """``wanted``, or it numbered where a loaded mod has it already, ignoring
    case as the folders do: the origin a mod made here -- a patch, or a fork
    (:func:`core.forking.make`) -- is given, since one put in under a loaded
    mod's origin takes that mod's place in the loadout."""
    taken = {layer.origin.lower() for layer in loadout.layers}
    if wanted.lower() not in taken:
        return wanted
    n = 2
    while ("%s %d" % (wanted, n)).lower() in taken:
        n += 1
    return "%s %d" % (wanted, n)


def make(loadout: Loadout, origin: str, name: str = "") -> ModLayer:
    """Create the patch mod for ``origin`` and put it in the loadout.

    It is an ordinary loaded mod from then on: its own row in the changes list,
    its own place in load order, its own folder when exported.  What makes it a
    patch is only that it is named to land just after the mod it is about, and
    says so in its modinfo.

    A ``name`` some loaded mod has already is numbered, as a fork's is: put
    in as it was, the patch took that mod's place in the loadout.
    """
    patched = loadout.layer(origin)
    if patched is None:
        raise ValueError("there is no loaded mod called %r" % origin)
    name = free_origin(loadout, name or suggest_name(origin))
    layer = ModLayer(
        origin=name,
        name=(patched.name or origin) + SUFFIX,
        category=patched.category,
        version="1.0",
        creator=loadout.own.creator,
        description="Personal changes to %s. Loads straight after it, so "
        "updating that mod does not take them with it."
        % (patched.name or origin),
        patches=origin,
    )
    loadout.set_layer(layer)
    return layer


def reattach(loadout: Loadout) -> int:
    """Work out which loaded mods are patches, and of what.

    ``ModLayer.patches`` is set when a patch is made and saved with the loadout,
    but a *folder* read builds layers out of modinfo files and there is nothing
    in one that the Studio recognises as "I patch that".  So opening your mods
    folder in a fresh loadout used to lose it: the patch came back as an
    ordinary mod sitting next to the one it patches, and -- because nothing then
    claimed to patch that mod -- the Studio would offer to build it a second
    patch.

    Two ways of telling, in order of confidence.  The **marker** the Studio
    writes into its own patches (:data:`core.modinfo.STUDIO_KEY`) is definite.
    For patches made before that existed there is an **inference**: it is yours,
    and it declares that it loads after exactly one mod, which is here.  That is
    good evidence and not proof -- two mods of your own where one really does
    depend on the other look the same -- so it is only used where the marker is
    absent.

    Returns how many layers were newly attached.
    """
    from . import modinfo

    by_id: dict = {}
    for layer in loadout.layers:
        for key in (layer.mod_id, layer.origin):
            if key:
                by_id.setdefault(str(key).strip().lower(), layer)

    found = 0
    for layer in loadout.layers:
        if layer.patches:
            continue
        wanted = modinfo.declared_patch_of(layer.declared)
        if not wanted and looks_mine(loadout, layer) and len(layer.load_after) == 1:
            wanted = layer.load_after[0]
        host = by_id.get(str(wanted).strip().lower()) if wanted else None
        if host is None or host.origin == layer.origin:
            continue
        layer.patches = host.origin
        layer.needs = [
            other.origin for other in (
                by_id.get(value.strip().lower()) for value in modinfo.declared_needs_of(layer.declared)
            )
            if other is not None and other.origin not in (layer.origin, host.origin)
        ]
        found += 1
    return found


def held_over_a_read(loadout: Loadout) -> List[Tuple[ModLayer, str]]:
    """The patches loaded now, each with the ModID of the mod it patches:
    taken before a folder read drops every layer, for :func:`put_back`.

    A patch made here is in no mods folder until Play builds it, so the read
    cannot give it back.  It used to lose its layer: its changes stayed,
    under a mod nothing was loaded as, and Changes go to went back to your
    own mod (H2-1).
    """
    held = []
    for layer in loadout.layers:
        if layer.patches:
            host = loadout.layer(layer.patches)
            held.append((layer, host.mod_id if host is not None else ""))
    return held


def put_back(loadout: Loadout, held: List[Tuple[ModLayer, str]]) -> int:
    """Put back each patch in ``held`` that the folder read did not bring
    back itself; how many were.

    Its mod is found by the origin it was patching or, where the read has
    the copy that loads somewhere else now -- a bundled piece several mods
    ship, and another mod added carries a newer one -- by its ModID.  A mod
    that is no longer loaded at all leaves the patch greyed, as unloading
    it does.  Each is slotted in again straight after its mod, the place it
    had being a place in the order before the read.
    """
    from dataclasses import replace

    count = 0
    for patch, mod_id in held:
        if loadout.layer(patch.origin) is not None:
            # Built by Play and read back out of the folder: reattach() knows it.
            continue
        layer = replace(patch, order=0)
        wanted = (mod_id or "").strip().lower()
        if loadout.layer(layer.patches) is None and wanted:
            for other in loadout.layers:
                if not other.patches and (other.mod_id or "").strip().lower() == wanted:
                    layer.patches = other.origin
                    break
        loadout.set_layer(layer)
        count += 1
    return count


def mine_over_a_read(loadout: Loadout) -> Tuple[List[ModLayer], list]:
    """The forks loaded now, and the mods of yours made projects here, with
    the ops of the forks the Studio could not read: taken before a folder
    read drops every layer, for :func:`put_mine_back`.  A fork is in no mods
    folder until Play builds it, and what makes either yours is not in any
    folder."""
    layers = [layer for layer in loadout.layers if layer.fork_of or layer.kept]
    forks = {layer.origin for layer in layers if layer.fork_of}
    return layers, [entry for entry in loadout.unread if entry.origin in forks]


def unread_of_forks(held: Tuple[List[ModLayer], list]) -> list:
    """The ops of the forks :func:`mine_over_a_read` took that could not be
    read: carried still, since no folder read brings them back."""
    return list(held[1])


def put_mine_back(loadout: Loadout, held: Tuple[List[ModLayer], list]) -> int:
    """Put back what :func:`mine_over_a_read` took; how many were.  A fork
    is put back as it was, where the read put it if it brought it back --
    built by Play, and read out of the folder -- and where it was otherwise;
    its files stay its own copy (and what of it could not be read is
    :func:`unread_of_forks`).  A mod of yours made a project here is that
    still, where the read brought it back."""
    layers, _unread = held
    count = 0
    for was in layers:
        now = loadout.layer(was.origin)
        if was.fork_of:
            loadout.set_layer(replace(
                was, order=now.order if now is not None else was.order,
                active=now.active if now is not None else was.active,
            ))
            count += 1
        elif now is not None and not now.kept:
            loadout.set_layer(replace(now, kept=True))
            count += 1
    return count


def find_lost(loadout: Loadout) -> int:
    """Make again the patches a folder read lost before :func:`put_back` was
    there to keep them; how many were.

    What is left of one is changes of yours under the patch's name and no mod
    loaded by it.  Where exactly one loaded mod has the folder name it was
    named after, and nothing patches that mod, it is that mod's patch.
    """
    loaded = {layer.origin for layer in loadout.layers}
    lost = sorted({
        change.origin for change in loadout.changes.values()
        if change.authored and change.origin.endswith(SUFFIX) and change.origin not in loaded
    })
    found = 0
    for origin in lost:
        named = origin[: -len(SUFFIX)]
        mods = [
            layer for layer in loadout.layers
            if not layer.patches and modfolder.folder_name(layer.origin) == named
            and loadout.patch_for(layer.origin) is None
        ]
        if len(mods) != 1:
            continue
        make(loadout, mods[0].origin, origin)
        found += 1
    return found


def is_inert(loadout: Loadout, layer: ModLayer) -> bool:
    """Whether a patch has nothing left to patch.

    A patch is written against the document the mod it patches leaves behind.
    Switch that mod off -- or unload it -- and the patch is still its own folder
    and still loads, but now against a game that never had the mod its values
    were measured from.  What it does then is not "nothing"; it is unpredictable,
    which is worse.

    Derived rather than stored, and derived from the host's own switch: a second
    checkbox to keep in step is a second checkbox to get out of step, and
    switching a mod off should not have to ask a question about a different one.
    """
    if not layer.patches:
        return False
    off = loadout.not_loading()
    return any(_not_in_effect(loadout, origin, off) for origin in loadout.needed(layer))


def _not_in_effect(loadout: Loadout, origin: str, off: set) -> bool:
    """Whether a mod a patch needs is out of the game: not loaded, switched
    off, or replaced by another mod -- the loader drops a mod replaced, so
    the game the patch loads against never had it either.  Off is every copy
    of it off: a patch names the mod by its ModID, so it follows whichever
    host's copy loads.  ``off`` is :meth:`Loadout.not_loading`, worked out
    once by the caller rather than again for every mod a patch needs."""
    host = loadout.layer(origin)
    return host is None or host.origin in off


def patched_names(loadout: Loadout, layer: ModLayer) -> str:
    """The mods a patch is for, by name, as one: every mod it needs, the one
    it was made for first.  The Mod changes list and the Generate tab both
    name it by this: worked out apart, the Generate tab named it by its
    first mod alone, though it is left out while any of them is off."""
    return join_list([
        other.label if other is not None else origin
        for origin in loadout.needed(layer)
        for other in (loadout.layer(origin),)
    ])


def why_inert(loadout: Loadout, layer: ModLayer) -> str:
    """One line saying why a patch has nothing to patch, or ""."""
    if not is_inert(loadout, layer):
        return ""
    off = loadout.not_loading()
    missing = next(origin for origin in loadout.needed(layer) if _not_in_effect(loadout, origin, off))
    if missing != layer.patches:
        other = loadout.layer(missing)
        return tr("%s, which it also needs, is not loaded, switched off or replaced",
                  other.label if other is not None else missing)
    host = loadout.layer(layer.patches)
    if host is None:
        return tr("the mod it patches is not loaded")
    if host.origin not in loadout.switched_off():
        instead = loadout.layer(loadout.replaced().get(host.origin, ""))
        if instead is None:
            return tr("the mod it patches is replaced by another mod, so the game does not load it")
        return tr(
            "the mod it patches is replaced by %s, so the game does not load it", instead.label
        )
    holder = loadout.switch_of(host.origin)
    if holder is not None and holder is not host:
        return tr("the mod it patches is switched off with %s, which it came bundled in",
                  holder.label)
    return tr("the mod it patches is switched off")


def can_need(loadout: Loadout, patch: ModLayer) -> list:
    """The loaded mods a patch could be made to need as well: any but
    itself, the mods it needs already, and other patches."""
    taken = {patch.origin, *loadout.needed(patch)}
    return [layer for layer in loadout.layers if layer.origin not in taken and not layer.patches]


def need(loadout: Loadout, patch: ModLayer, origin: str) -> bool:
    """Make ``patch`` need ``origin`` too: coupled to it, and loading after
    it.  Whether anything changed.  The patch's layer is put in again, a new
    one, as undo sees a layer change."""
    other = loadout.layer(origin)
    if (not patch.patches or origin in loadout.needed(patch) or other is None
            or origin == patch.origin or other.patches):
        return False
    loadout.set_layer(replace(patch, needs=[*patch.needs, origin]))
    return True


def unneed(loadout: Loadout, patch: ModLayer, origin: str) -> bool:
    """Make ``patch`` stop needing ``origin``, one of the mods it needs
    besides the one it was made for.  Whether anything changed."""
    if origin not in patch.needs:
        return False
    loadout.set_layer(replace(patch, needs=[one for one in patch.needs if one != origin]))
    return True


def patched_by(loadout: Loadout) -> dict:
    """``{mod: the layer patching it}`` for every patch in the loadout."""
    return {layer.patches: layer for layer in loadout.layers if layer.patches}

