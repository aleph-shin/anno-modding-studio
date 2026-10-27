"""Patches, and a mod of your own.

Somebody else's mod is never edited (section 5 of
docs/proposals/projects-and-loadouts-proposal.md).  What you want different
about it goes into a mod of yours: your patch for it -- a mod of yours that
loads straight after theirs -- wherever you use it, or the loadout's own mod
in this loadout only.  Trying to change one of their ops in place asks which
(:meth:`PatchesController.way_on`), and a patch is made when it is picked in
*Currently editing* or asked for; a bundled copy, which cannot be patched
where it sits, always goes to a patch.  Editing a mod of your own goes
straight into it, and opening one sets the loadout up around it.

``core.patching`` decides what needs a patch and makes it; this is the
window's side -- the questions, what the editor and the changes panel show
afterwards, what the status line says -- held as ``window.patches`` (Stage
3.3 of docs/proposals/consolidation-proposal.md).
"""

from __future__ import annotations

from dataclasses import replace
from typing import Dict, Optional

from PySide6.QtWidgets import QInputDialog

from ..core.changes import AIM_MAKE, OWN
from ..core.i18n import tr, tr_kw, trn
from .widgets import busy_cursor, named, tell


class PatchesController:
    """The window's patches, moved off it; ``window`` is the MainWindow."""

    def __init__(self, window) -> None:
        self.window = window
        # Somebody else's mods whose changes you have said go into this
        # loadout's own mod. Asked once each: it is a question, not a nag.
        self._inside_own: set = set()
        # Whether the mod being pointed at was asked for by its file, on the
        # Mod file tab: the window says so while it does the pointing.
        self.in_its_file = False

    def wants_a_patch(self, origin: str):
        """The loaded mod ``origin``, if putting a patch in front of it is the
        better idea, and ``None`` otherwise.

        Somebody else's mod, with nothing already standing in front, that is not
        itself a patch.  Editing your own file directly is exactly right and a
        patch would be an odd second folder holding half of it.
        """
        from ..core import patching

        layer = self.window.loadout.layer(origin)
        if (
            layer is None
            or layer.patches
            or self.window.loadout.patch_for(origin) is not None
            or patching.looks_mine(self.window.loadout, layer)
        ):
            return None
        return layer

    def patch_for_changes(self, origin: str) -> Optional[str]:
        """Make the patch for ``origin``, picked as "A new patch for…" in
        *Currently editing*, and have changes go to it: the patch's origin,
        or None where none was made.  A bundled mod's is made with the
        reason said once (:meth:`_patch_the_bundled`)."""
        from ..core import patching

        layer = self.window.loadout.layer(origin)
        if layer is None:
            return None
        if patching.edits_need_a_patch(self.window.loadout, layer):
            if not self._patch_the_bundled(origin):
                return None
        elif self._patch_and_say(origin) is None:
            return None
        patch = self.window.loadout.patch_for(origin)
        return patch.origin if patch is not None else None

    def _patch_and_say(self, origin: str):
        """Make the patch for ``origin`` quietly and say so on the status
        line: the patch, or None where a patch is not the right idea."""
        made = self._patch_quietly(origin)
        if made is None:
            return None
        layer, patch = made
        self.window.status_label.setText(tr(
            "Changes now go to %(patch)s, which loads straight after %(mod)s.",
            patch=patch.label, mod=layer.label))
        return patch

    def changed_directly(self, origin: str) -> bool:
        """Whether a change to one of ``origin``'s own ModOps is made to it
        in place without asking: :func:`core.patching.changed_directly` --
        a mod of yours, or somebody else's while your patch for it is where
        changes go -- or one whose edits you have said, or the loadout's own
        mod has said already, this loadout's own mod keeps
        (:mod:`core.inside`).  Their file is never written either way: the
        edit goes into the generated copy Play writes."""
        from ..core import edits, patching

        loadout = self.window.loadout
        if patching.changed_directly(loadout, origin):
            return True
        layer = loadout.layer(origin)
        if patching.edits_need_a_patch(loadout, layer):
            return False
        return origin in self._inside_own or edits.has_edits(loadout, origin, OWN)

    def make_project(self, origin: str) -> Optional[str]:
        """Make a project for ``origin``, a mod with your name on it that
        nothing keeps, so that it is edited directly from then on (section
        5.5 of the projects and loadouts proposal): asked first, and where a
        project for its ModID is there already, taken up without asking and
        read back (:meth:`_take_up`).  Changes go to it after.  Its origin,
        or None where none was made."""
        from ..core import filecache, patching

        loadout = self.window.loadout
        layer = loadout.layer(origin)
        if layer is None or not patching.without_a_project(loadout, layer):
            return None
        found = filecache.project_keeping(layer.mod_id, loadout.game) if layer.mod_id else None
        if found is None and not self.window.prompts.ask_make_project(layer.label):
            return None
        loadout.set_layer(replace(layer, kept=True))
        # Its project, and not a second one beside it (section 4.6).
        given_up = self._take_up(origin, found) if found is not None else None
        self._go_to(origin)
        self.window.changes_panel.refresh()
        self.window.files.mark_unsaved()
        self.window.status_label.setText(
            self._taken_up_said(layer.label, found, given_up)
            if found is not None and given_up is not None else
            tr("%s has a project of its own now, and changes go straight into it.", layer.label)
        )
        return origin

    def _take_up(self, origin: str, found) -> Optional[int]:
        """Take up the project at ``found`` for ``origin``, a mod of yours
        read in from its folder, and read it where the mod stands here at
        once: the project is what the mod is, so the next save writes
        nothing over it that this loadout did not do
        (:func:`core.readback.join`).  How many edits made here to it were
        given up, or None where the project cannot be read."""
        from ..core import projects as project_files
        from ..core import readback, stale

        loadout = self.window.loadout
        try:
            given_up = readback.join(loadout, origin, found)
        except project_files.ProjectError:
            return None
        # Read now, as a patch taken up is: changes go into it next, and one
        # made while it is held would have no ModOp to keep it.
        if self.window.index is not None:
            with busy_cursor():
                stale.read_project_now(loadout, origin, self.window.index)
        self.window.readings.follow()
        return given_up

    def _taken_up_said(self, mod: str, found, given_up: int) -> str:
        """The status line for ``mod``, taken up from its project at
        ``found``: the loadouts that have it, and what was given up."""
        from ..core import filecache

        having = filecache.loadouts_with_project(
            found, self.window.loadout.game, excluding=self.window.loadout_path)
        said = trn(
            "%(mod)s is kept by its project, which the loadout %(names)s has, and is read from "
            "it: changes go straight into it, there too.",
            "%(mod)s is kept by its project, which these loadouts have: %(names)s. It is read "
            "from it, and changes go straight into it, there too.",
            len(having), mod=mod, names=", ".join(self.window.files.name_of(path) for path in having),
        ) if having else tr(
            "%s is kept by its project, and is read from it: changes go straight into it.", mod)
        if given_up:
            said += " " + trn(
                "The %(n)s edit made to it here before is given up: its project is what it is.",
                "The %(n)s edits made to it here before are given up: its project is what it is.",
                given_up,
            )
        return said

    def way_on(self, origin: str) -> Optional[str]:
        """Somebody else's mod is never changed: ask where a change to it
        goes instead (section 5.2 of the projects and loadouts proposal).
        Your patch for it, made where there is none yet, which changes go to
        from then on; or this loadout's own mod, made where there is none
        yet, which keeps that mod's changes from then on without asking
        again; or your own copy of it, made in its place, which everything
        the loadout held of it is now part of.  The patch's origin, "" for
        the loadout's own mod, the copy's origin, or None for none of them
        -- the question was cancelled.  A bundled mod is not
        asked about: its changes always go to its patch
        (:meth:`_patch_the_bundled`)."""
        from ..core import patching

        loadout = self.window.loadout
        layer = loadout.layer(origin)
        if layer is None:
            return None
        if patching.without_a_project(loadout, layer):
            # A mod of yours that nothing keeps: made a project, it is
            # changed directly (section 5.5).
            return self.make_project(origin)
        if patching.edits_need_a_patch(loadout, layer):
            if not self._patch_the_bundled(origin):
                return None
            patch = loadout.patch_for(origin)
            return patch.origin if patch is not None else None
        patch = loadout.patch_for(origin)
        answer = self.window.prompts.ask_way_on(
            layer.label, patch.label if patch is not None else "", self.in_its_file,
        )
        if answer == "patch":
            if patch is None:
                patch = self._patch_and_say(origin)
                return patch.origin if patch is not None else None
            if loadout.held(patch.origin):
                # Changed by another loadout since this one read it: no edit
                # goes into it from here (core.projects).
                self.window.status_label.setText(tr(
                    "%s has changed in another loadout since this one read it, so "
                    "nothing goes into it from here until it is read again.", patch.label))
                return None
            self._go_to(patch.origin)
            return patch.origin
        if answer == "own":
            self._inside_own.add(origin)
            if not loadout.has_own_mod():
                loadout.make_own_mod()
            if loadout.editing_origin() == (patch.origin if patch is not None else None):
                self._go_to("")
            else:
                self.window.editor.refresh_target()
            self.window.status_label.setText(tr(
                "Your changes to %s go into this loadout's own mod, and hold in "
                "this loadout only.", layer.label))
            return ""
        if answer == "fork":
            # Made, and changed directly from now on: what was being done is
            # done again on the copy, which is the mod changes go to -- by
            # the caller, which finds what it was editing there.
            return self.fork(origin)
        return None

    def fork(self, origin: str) -> Optional[str]:
        """Make your own copy of somebody else's mod ``origin`` -- a fork --
        saying first what that means and offering to fold your patch for it
        in (section 5.4 of the projects and loadouts proposal).  The fork's
        origin, which changes go to from then on, or None where none was
        made."""
        from ..core import forking

        loadout = self.window.loadout
        layer = loadout.layer(origin)
        if not forking.can_fork(loadout, layer):
            return None
        if not self.window.file_panel.settle_typed():
            return None
        patch = loadout.patch_for(origin)
        others = [self.window.files.name_of(path) for path in
                  forking.patch_users(loadout, origin, excluding=self.window.loadout_path)]
        answer = self.window.prompts.ask_fork_a_mod(
            layer.label, loadout.game, patch.label if patch is not None else "", others,
            creator=(layer.creator or "").strip(),
        )
        if answer is None:
            return None
        way, fold = answer
        if way == "mine":
            return self.claim(origin, fold)
        if self.window.preferences.ensure_creator() is None:
            return None
        try:
            made = forking.make(loadout, origin, self.window.where.library_dir(), fold=fold)
        except OSError as exc:
            self.window.status_label.setText(tr(
                "Your copy of %(mod)s could not be made: %(why)s", mod=layer.label, why=exc))
            return None
        loadout.editing = made.origin
        self.mod_renamed()
        self.window.status_label.setText(tr(
            "Made %(fork)s, your own copy of %(mod)s, in its place. Changes go "
            "straight into it.", fork=made.label, mod=layer.label))
        return made.origin

    def claim(self, origin: str, fold: bool = True) -> Optional[str]:
        """Carry on with somebody else's mod ``origin`` as yours after all:
        one you published under another author name, which is one of yours
        from now on (Settings ▸ Authoring), so that every mod by it is yours.

        The mod stays itself, with its name, its ModID and its author name,
        which an update of it goes out under.  It is made a project, as a mod
        with your name on it is (:meth:`make_project`), and with ``fold`` your
        patch for it is folded in, as a fork folds it
        (:func:`core.forking.fold_in`).  Where the loadout is about it alone,
        it is the mod the loadout carries on with, as a mod of yours read in
        is, and so the one the Export tab publishes.  Its origin, which
        changes go to from then on, or None where it was not taken on."""
        from ..core import forking, patching

        loadout = self.window.loadout
        layer = loadout.layer(origin)
        if layer is None or not (layer.creator or "").strip() or patching.is_mine(loadout, layer):
            return None
        if self.window.preferences.ensure_creator() is None:
            return None
        self.window.preferences.add_alias(layer.creator)
        kept = self._kept_already(layer)
        if kept is False:
            return None
        patch = loadout.patch_for(origin) if fold else None
        if patch is not None:
            forking.fold_in(loadout, patch.origin, origin)
        loadout.set_layer(replace(loadout.layer(origin), kept=True))
        given_up = self._take_up(origin, kept) if kept is not None else None
        alone = self._about_it_alone(origin)
        if alone:
            self._carry_on_with(origin)
        loadout.editing = origin
        self.mod_renamed()
        if kept is not None and given_up is not None:
            said = self._taken_up_said(layer.label, kept, given_up)
        elif alone:
            said = tr("Carrying on with %(mod)s as yours. %(creator)s is one of your author "
                      "names now, and changes go straight into the mod.",
                      mod=layer.label, creator=layer.creator)
        else:
            said = tr("%(mod)s is yours, with a project of its own. %(creator)s is one of "
                      "your author names now, and changes go straight into the mod.",
                      mod=layer.label, creator=layer.creator)
        self.window.status_label.setText(said)
        return origin

    def _about_it_alone(self, origin: str) -> bool:
        """Whether the loadout is about the mod ``origin`` and nothing else:
        it, and patches of it, with no mod carried on with and nothing
        written into the loadout's own -- a downloaded mod opened to change,
        as one dropped in is."""
        loadout = self.window.loadout
        if loadout.home or loadout.is_the_mod:
            return False
        if any(change.origin == OWN for change in loadout.changes.values()) or any(
                op.origin == OWN for op in loadout.ops):
            return False
        return all(other.origin == origin or other.patches == origin for other in loadout.layers)

    def mod_renamed(self) -> None:
        """Follow a mod known by another origin now -- a fork in its
        original's place, a mod moved to a folder of its own -- on screen:
        the editor's target and asset, the changes panel, and the loadout
        unsaved.  Undo is forgotten: a mod renamed, which undoing halfway
        would leave known by neither name."""
        self.window.editor.refresh_target()
        if self.window.editor.guid is not None:
            self.window.editor.load_asset(self.window.editor.guid)
        self.window.changes_panel.refresh()
        self.window.on_editing_changed()
        self.window.on_changes_updated()
        self.window.forget_undo()
        self.window.files.mark_unsaved()

    def may_edit_inside(self, origins) -> Optional[Dict[str, str]]:
        """Whether an edit inside the ops of each of ``origins`` may go on:
        each is a mod of yours, or one :meth:`changed_directly` says yes
        to, or one the three ways on were asked of and answered.  Where it
        may, which mod each one's ops are in now: itself, or the copy that
        *Make your own copy* put in its place, where the edit is made
        instead; None where it may not."""
        found = {}
        for origin in sorted(set(origins)):
            if self.changed_directly(origin):
                found[origin] = origin
                continue
            went = self.way_on(origin)
            if went is None:
                return None
            # Only a copy takes the mod's place; a patch or the loadout's own
            # mod keeps an edit made inside it where it is.
            found[origin] = went if self.window.loadout.layer(origin) is None else origin
        return found

    def _go_to(self, origin: str) -> None:
        """Have changes go to ``origin``, a mod of yours, from now on."""
        self.window.loadout.editing = origin
        self.window.editor.refresh_target()
        if self.window.editor.guid is not None:
            self.window.editor.load_asset(self.window.editor.guid)
        self.window.on_editing_changed()

    def place_for_a_pattern(self, origin: str):
        """Where a change to one of ``origin``'s patterns is written: ``origin``
        itself, the patch in front of it, the loadout's own mod (""), or None
        for nowhere -- the question was cancelled.

        A pattern of yours is changed where it is.  One of somebody else's
        is never changed: it is written again in a mod of yours, which loads
        after theirs, so theirs runs first and yours has the last word -- your
        patch, while it is where changes go, or this loadout's own mod once
        it keeps that mod's changes, and otherwise whichever the three ways
        on are answered with (:meth:`way_on`).  It used to be changed in
        their file whatever changes went to
        (docs/proposals/patches-proposal.md, section 1).
        """
        from ..core import edits, patching

        loadout = self.window.loadout
        layer = loadout.layer(origin)
        if layer is None or layer.patches or patching.is_mine(loadout, layer):
            return origin
        patch = loadout.patch_for(origin)
        if patch is not None and loadout.editing_origin() == patch.origin:
            return patch.origin
        if not patching.edits_need_a_patch(loadout, layer) and (
            origin in self._inside_own or edits.has_edits(loadout, origin, OWN)
        ):
            return OWN
        return self.way_on(origin)

    def _patch_quietly(self, origin: str):
        """Make the patch for ``origin`` without asking what to call it.

        ``(the mod, its patch)``, or None if a patch is not the right idea
        for it.  The name is the one convention settled on -- see
        :func:`core.patching.suggest_name` -- and asking for it is only
        worth doing when the user went looking for the command themselves.
        Nothing you changed already moves into it: what the loadout's own
        mod keeps of that mod it goes on keeping, in this loadout only
        (section 5.3 of the projects and loadouts proposal).
        """
        from ..core import patching

        layer = self.wants_a_patch(origin)
        if layer is None:
            return None
        made = self.joined(origin) or patching.make(self.window.loadout, origin)
        self.window.loadout.editing = made.origin
        self.window.editor.refresh_target()
        self.window.changes_panel.refresh()
        self.window.on_editing_changed()
        # A mod made, which undoing halfway would leave changes going to a
        # patch that is not there.
        self.window.forget_undo()
        return layer, made

    def _patch_the_bundled(self, origin: str) -> bool:
        """Send changes aimed at a bundled mod of somebody else's to a patch.

        Not asked, as editing a top-level mod in place is: a change written
        into a bundled copy goes when its host is updated, and stops loading
        without a word when another mod ships a newer copy of it -- see
        :func:`core.patching.edits_need_a_patch`.  The patch there already is
        takes them; otherwise one is made, and the reason said once.
        """
        existing = self.window.loadout.patch_for(origin)
        layer = self.window.loadout.layer(origin)
        if existing is not None:
            self.window.loadout.editing = existing.origin
            patch = existing
        else:
            made = self._patch_quietly(origin)
            if made is None:
                return False
            _layer, patch = made
        self.window.editor.refresh_target()
        if self.window.editor.guid is not None:
            # The editor read the asset as the bundled mod leaves it; the patch
            # loads after it, so its document is a different one.
            self.window.editor.load_asset(self.window.editor.guid)
        self.window.on_editing_changed()
        if existing is None:
            self.explain_bundled_patch(layer, patch)
        self.window.status_label.setText(tr(
            "%(mod)s came bundled inside another mod, so changes to it go to "
            "%(patch)s, which loads straight after it.",
            mod=layer.label, patch=patch.label))
        return True

    def forget_the_loadout_before(self) -> None:
        """Another loadout is here: which mods a patch was declined for was
        said of the one before, and a mod of the same name in this one is
        asked about again."""
        self._inside_own.clear()

    def explain_bundled_patch(self, layer, patch) -> None:
        """Say once, when the patch is made, why changes did not go where they
        were aimed.  A method so a test can answer it without a dialog.  The
        status line names the patch."""
        tell(
            self.window,
            tr("Changes go to a patch"),
            tr_kw("%s came bundled inside another mod, so a change written into "
                  "it would be lost with that mod's next update.\n"
                  "\n"
                  "Your changes go to your [[patch]] for it instead, which loads "
                  "straight after whichever copy the game loads.", named(layer.label)),
        )

    def patch_what_just_arrived(self, origin: str) -> None:
        """Set a mod up for changing, having just read it in to change it.

        "Change a mod you downloaded" is the whole aim; a patch mod is how that
        is done without an update to theirs taking your work with it.  Asking
        the user to find *Changes go to*, pick the mod there, and then find
        *Patch this mod…* is asking them to assemble the thing they already
        said they wanted.

        Not for a mod that is already yours -- editing your own file directly is
        exactly right and a patch would be an odd second folder -- and not where
        something already stands in front of it.
        """
        made = self._patch_quietly(origin)
        if made is None:
            self._edit_your_own(origin)
            return
        layer, made = made
        self.window.status_label.setText(tr(
            "Read %(mod)s in, and made %(patch)s to hold your changes to it. It "
            "loads straight after, so updating that mod does not remove them.",
            mod=layer.label, patch=made.label))

    def add_need(self, origin: str) -> bool:
        """Make the patch ``origin`` need another loaded mod as well, picked
        from a list: it then loads after that mod too, and is left out while
        it is off.  Whether one was added."""
        from ..core import patching

        loadout = self.window.loadout
        patch = loadout.layer(origin)
        if patch is None or not patch.patches:
            return False
        choices = patching.can_need(loadout, patch)
        if not choices:
            return False
        picked = self.window.prompts.pick_a_mod_to_need(patch.label, [one.label for one in choices])
        if picked is None:
            return False
        other = choices[picked]
        if not patching.need(loadout, patch, other.origin):
            return False
        self._needs_changed(tr("making %(patch)s need %(mod)s", patch=patch.label, mod=other.label))
        self.window.status_label.setText(tr(
            "%(patch)s now needs %(mod)s as well: it loads after it, and is left out while it is off.",
            patch=patch.label, mod=other.label))
        return True

    def remove_need(self, origin: str, other: str) -> bool:
        """Make the patch ``origin`` stop needing ``other``."""
        from ..core import patching

        loadout = self.window.loadout
        patch = loadout.layer(origin)
        if patch is None or not patching.unneed(loadout, patch, other):
            return False
        gone = loadout.layer(other)
        self._needs_changed(tr("making %(patch)s no longer need %(mod)s", patch=patch.label,
                               mod=gone.label if gone is not None else other))
        return True

    def _needs_changed(self, step: str) -> None:
        self.window.files.mark_unsaved()
        self.window.changes_panel.refresh()
        self.window.on_changes_updated()
        self.window.record(step)

    def joined(self, origin: str):
        """Your patch of the loaded mod ``origin``, where you have made one
        of it in another loadout: that patch's project taken up here
        (:func:`core.readback.join_patch`), and read where it stands in this
        loadout.  None where no project patches it, so a patch is made.

        One patch to a mod: a patch is how the mod should have been wherever
        you use it (section 5.1 of the projects and loadouts proposal).
        """
        from ..core import filecache, library, modfolder, readback, stale
        from ..core import projects as project_files

        loadout = self.window.loadout
        theirs = loadout.layer(origin)
        if theirs is None:
            return None
        found = filecache.patch_project_for(modfolder.match_keys(theirs.mod_id, origin), loadout.game)
        if found is None:
            return None
        # Where it has been built, the folder it was built under is its name
        # here too: one project builds one folder (core.builtby).
        library_dir = self.window.where.library_dir()
        built = library.built_from(library_dir, project_files.as_written(found)) if library_dir else []
        try:
            made = readback.join_patch(loadout, origin, found, built[0] if built else "")
        except project_files.ProjectError:
            return None
        # Read where it stands here now, as a project another loadout changed
        # is read: an edit is about to go into it, and one made while it is
        # held would have no ModOp to keep it.  What loads after it is read
        # again in the background.
        if self.window.index is not None:
            with busy_cursor():
                stale.read_project_now(loadout, made.origin, self.window.index)
        having = filecache.loadouts_with_project(found, loadout.game, excluding=self.window.loadout_path)
        self.window.status_label.setText(trn(
            "Your patch of %(mod)s is the one the loadout %(names)s has: edits to it here go "
            "there too.",
            "Your patch of %(mod)s is the one these loadouts have: %(names)s. Edits to it here "
            "go there too.",
            len(having), mod=theirs.label, names=", ".join(self.window.files.name_of(path) for path in having),
        ) if having else tr("Your patch of %s is the one you made before, from its project.", theirs.label))
        self.window.readings.follow()
        return made

    def make_patch(self, origin: str) -> None:
        """Make a mod of your own that loads straight after ``origin``.

        Somebody else's mod is never changed, and a patch is how what you
        want different about it holds wherever you use it: it is yours, it
        loads immediately after theirs, and an update to theirs leaves it
        standing.
        """
        from ..core import patching

        # Changes go to the patch once it is made: what is typed in the file
        # of the mod they go to now is settled first.
        if not self.window.file_panel.settle_typed():
            return
        layer = self.window.loadout.layer(origin)
        if layer is None or self.window.loadout.patch_for(origin) is not None:
            return
        joined = self.joined(origin)
        if joined is not None:
            self._patching_with(joined)
            return

        suggested = patching.suggest_name(origin)
        name, ok = QInputDialog.getText(
            self.window,
            tr("Patch this mod"),
            tr("A mod of your own, holding your changes to “%s”.\n\n"
               "Its modinfo will say it loads after that mod, which is what the "
               "loader reads.\nThe name should sort after it too, so it still lands "
               "on top if that is ignored:", layer.label or origin),
            text=suggested,
        )
        if not ok:
            return
        name = name.strip()
        if not patching.loads_after(name, origin, self.window.loadout.game):
            tell(
                self.window,
                tr("That would load first"),
                tr("%(name)s sorts before %(mod)s, so the game would apply "
                   "your changes and then let that mod overwrite them.\n"
                   "\n"
                   "Pick a name that sorts after it, such as %(suggested)s.",
                   name=named(name), mod=named(origin), suggested=named(suggested)),
                warning=True,
            )
            return

        if self.window.preferences.ensure_creator() is None:
            return
        made = patching.make(self.window.loadout, origin, name)
        self._patching_with(made)
        self.window.status_label.setText(tr(
            "Changes now go to %(patch)s, which loads straight after %(mod)s.",
            patch=made.label, mod=layer.label))

    def _patching_with(self, made) -> None:
        """Send changes to the patch just made or joined, ``made``."""
        self.window.loadout.editing = made.origin
        self.window.editor.refresh_target()
        if self.window.editor.guid is not None:
            self.window.editor.load_asset(self.window.editor.guid)
        # The patch is a loaded mod from now on, so it wants a row of its own.
        self.window.changes_panel.refresh()
        self.window.on_editing_changed()
        self.window.forget_undo()

    def _edit_your_own(self, origin: str) -> None:
        """Aim the editor straight at a mod that just arrived and is yours.

        A patch is the answer for somebody else's mod.  Yours is edited in
        place -- that is what not making one means -- and leaving the editor
        aimed at the loadout's own empty mod sent the first edit there instead.

        And the way of working becomes *writing a mod of your own*, with the
        loadout standing for that mod.  Which of the two it is turns on whose
        mod it is, and this one is yours: it used to go on saying "Changing a
        downloaded mod", and treating the mod as somebody else's in the places
        that read the aim -- its details on the Export tab among them, locked.

        Where a project keeps it already, as *Open one of yours…* has it:
        the loadouts that have that project are offered instead, and where
        none does it is taken up and read back (:meth:`_take_up`).
        """
        from ..core import patching

        layer = self.window.loadout.layer(origin)
        if (
            layer is None
            or layer.patches
            or self.window.loadout.patch_for(origin) is not None
            or not patching.looks_mine(self.window.loadout, layer)
        ):
            return
        kept = self._kept_already(layer)
        if kept is False:
            return
        self._carry_on_with(origin)
        taken = kept is not None and self._take_up(origin, kept) is not None
        self.window.status_label.setText(tr(
            "Read %s in. It is yours and kept by its project, which it is read from: this "
            "is writing a mod of your own, and changes go straight into it.", layer.label
        ) if taken else tr(
            "Read %s in. It is yours, so this is writing a mod of your own, "
            "and changes go straight into it.", layer.label
        ))

    def _carry_on_with(self, origin: str) -> None:
        """Make the loaded mod ``origin`` the one this loadout is writing."""
        self.window.loadout.home = origin
        self.window.loadout.aim = AIM_MAKE
        self.window.loadout.editing = origin
        self.window.editor.refresh_target()
        self.window.changes_panel.refresh()
        self.window.on_editing_changed()
        self.window.output.load_loadout_fields()

    def _kept_already(self, layer):
        """The project that keeps ``layer``'s mod already, where one does:
        one project per ModID (section 4.6 of the projects and loadouts
        proposal).

        Where loadouts have that project, the mod is not taken on here: one
        of them is offered to open instead, and ``False`` comes back, the
        mod read in dropped again.  Where none has it, the project's path,
        for :meth:`_take_up` to read it back: the project is what the mod
        is, whichever build the folder holds.  None where no project keeps
        it, or it has no ModID to be found by.
        """
        from ..core import builds, filecache
        from ..core import projects as project_files

        game = self.window.loadout.game
        found = filecache.project_keeping(layer.mod_id, game) if layer.mod_id else None
        if found is None:
            return None
        try:
            project = project_files.Project.read(found)
        except project_files.ProjectError:
            return None
        older = ""
        here, there = builds.declared_build(layer.declared), project.build
        if here and there and here < there:
            older = tr(
                "This folder holds build %(here)s of it, and its project has been built "
                "up to %(there)s, so the folder is likely an older export.",
                here=here, there=there,
            )
        having = filecache.loadouts_with_project(found, game, excluding=self.window.loadout_path)
        if having:
            chosen = self.window.prompts.ask_open_kept(
                layer.label, [self.window.files.name_of(path) for path in having], older,
            )
            self._let_go_of(layer.origin)
            if chosen >= 0:
                self.window.load_loadout(having[chosen], asked=True)
            else:
                self.window.status_label.setText(tr(
                    "Left %s out. It is kept in a project already: open a loadout that has "
                    "it to carry on there.", layer.label))
            return False
        return found

    def _let_go_of(self, origin: str) -> None:
        """Take the mod read in a moment ago out again, as if never read."""
        self.window.loadout.drop_layer(origin)
        self.window.on_changes_updated()
        self.window.forget_undo()
        self.window.start.refresh_source_panel()

    def open_your_own_mod(self, start_dir: str = "") -> bool:
        """Read a mod of yours back in, to carry on writing it.

        Whose mod it is makes this *writing a mod of your own*; that it was
        started somewhere else, or last month, does not change the job.  So it
        is set up the way that aim works -- edited directly, its details yours
        to change, the loadout named after it -- while staying the loaded mod
        it is, so exporting writes it out as that mod, under its folder's
        name, rather than as a second one beside it.  ``start_dir`` is the mod, when it has been
        chosen already.
        """
        from ..core import patching

        if self.window.preferences.ensure_creator() is None:
            return False
        before = {layer.origin for layer in self.window.loadout.layers}
        if not self.window.import_mod(separate=True, start_dir=start_dir):
            return False
        arrived = [
            layer.origin for layer in self.window.loadout.layers if layer.origin not in before
        ]
        if not arrived:
            # Folded into the loadout's own mod instead -- "keep it as a
            # separate mod" unticked. That is carrying on too, with the loadout
            # as the mod, under the name it brought.
            if self.window.loadout_path is None:
                self.window.files.name_loadout(self.window.loadout.own.name)
            return True
        origin = arrived[0]
        layer = self.window.loadout.layer(origin)
        looked_mine = patching.looks_mine(self.window.loadout, layer)
        if not looked_mine:
            known = self.window.preferences.creator_name()
            if layer.creator and known:
                said = tr("%(mod)s says it was made by %(creator)s, and you publish "
                          "as %(you)s.", mod=named(layer.label), creator=named(layer.creator),
                          you=named(known))
            elif layer.creator:
                said = tr("%(mod)s says it was made by %(creator)s.",
                          mod=named(layer.label), creator=named(layer.creator))
            elif known:
                said = tr("%(mod)s says it was made by nobody in particular, and you "
                          "publish as %(you)s.", mod=named(layer.label), you=named(known))
            else:
                said = tr("%s says it was made by nobody in particular.", named(layer.label))
            answer = self.window.prompts.ask_carry_on_as_mine(said, (layer.creator or "").strip())
            if answer:
                self.window.preferences.add_alias(layer.creator)
            if answer is None:
                self.window.loadout.drop_layer(origin)
                self.window.on_changes_updated()
                self.window.forget_undo()
                self.window.start.refresh_source_panel()
                self.window.status_label.setText(tr(
                    "Left %s out. To change somebody else's mod, pick “Change a "
                    "mod you downloaded”.", layer.label
                ))
                return False
        kept = self._kept_already(layer)
        if kept is False:
            return False
        if kept is None and looked_mine and not self.window.prompts.ask_make_project(layer.label):
            # Read like anybody else's mod until a project keeps it (5.5).
            self.window.editor.refresh_target()
            self.window.status_label.setText(tr(
                "Read %s in. No project keeps it, so it is read like anybody else's "
                "mod until you make a project for it in Currently editing.", layer.label
            ))
            return False
        self._carry_on_with(origin)
        # Its project, not a second one beside it (section 4.6), read back
        # in place of what the folder holds.
        taken = kept is not None and self._take_up(origin, kept) is not None
        if self.window.loadout_path is None:
            self.window.files.name_loadout(layer.label)
        # Not "writes it back to its own folder": an export goes to the
        # exports folder, as the Export tab's row says (R3-44, hands-on run 3).
        self.window.status_label.setText(tr(
            "Carrying on with %s from its project. Changes go straight into it, and "
            "exporting writes it out as itself, not as a second mod beside it.", layer.label
        ) if taken else tr(
            "Carrying on with %s. Changes go straight into it, and exporting "
            "writes it out as itself, not as a second mod beside it.", layer.label
        ))
        return True

    def off_a_bundled_copy(self) -> None:
        """A loadout saved aiming its edits at somebody's bundled mod aims them
        at that mod's patch now, or at the loadout's own mod -- never into the
        copy, which is where the Studio used to let them go.  Quietly: nothing
        is made on opening, and the status line says where they go instead."""
        from ..core import patching

        loadout = self.window.loadout
        layer = loadout.layer(loadout.editing)
        if layer is None or not patching.edits_need_a_patch(loadout, layer):
            return
        patch = loadout.patch_for(layer.origin)
        loadout.editing = patch.origin if patch is not None else ""
        # Translators: "Currently editing" is the name of the picker above the
        # asset editor.
        self.window.status_label.setText(
            tr("%(mod)s came bundled inside another mod, so changes no longer go "
               "into it: they go to %(patch)s. Pick it in Currently editing for a patch "
               "of its own.", mod=layer.label, patch=patch.label)
            if patch is not None
            else tr("%s came bundled inside another mod, so changes no longer go "
                    "into it: they go to your own mod. Pick it in Currently editing for "
                    "a patch of its own.", layer.label)
        )

    def find_lost_patches(self) -> None:
        """A loadout saved after a read of its mods folder lost a patch it
        had made: the patch is made again for the mod it was named after,
        with the changes that stayed in it.  See
        :func:`core.patching.find_lost`."""
        from ..core import patching

        patching.find_lost(self.window.loadout)

    def settle_home(self) -> None:
        """Recognise a loadout saved before carrying on with your mod had a name.

        One that changed a single mod, with no patch in front of it, straight
        into a mod that is yours: that was writing a mod of your own all along,
        filed under the other aim.
        """
        from ..core import patching
        from ..core.changes import AIM_TWEAK

        loadout = self.window.loadout
        if loadout.home or loadout.aim != AIM_TWEAK:
            return
        target = loadout.layer(loadout.editing)
        if (
            target is None
            or target.patches
            or any(layer.patches for layer in loadout.layers)
            or not patching.looks_mine(loadout, target)
        ):
            return
        loadout.home = target.origin
        loadout.aim = AIM_MAKE
