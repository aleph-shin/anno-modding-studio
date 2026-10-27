"""What the window does with mod.io: getting mods and collections, updating
them, the account, the mods a loadout names that are not here, and leaving
the game's own mod browser.

The dialogs that talk to mod.io (``modio_get_dialog``, ``modio_updates_dialog``,
``install_plan_dialog``, ``unsubscribe_dialog``) were each opened, fed and
read back by the window itself; this is the one place that does it now, held
as ``window.modio`` (Stage 3.3 of docs/proposals/consolidation-proposal.md).

A collection is mod.io's too: reading one into the loadout, taking one out
or switching its mods off, and starting a loadout or a profile from one.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import QDialog, QMessageBox

from ..core import (
    conflicts,
    disk,
    games,
    installplan,
    library,
    modfolder,
    places,
    secrets,
    unsubscribe,
)
from ..core.changes import AIM_MANAGE, Loadout
from ..core.i18n import join_list, tr, trn
from .install_plan_dialog import InstallPlanDialog
from .modio_get_dialog import COLLECTIONS, ModioGetDialog
from .modio_updates_dialog import ModioUpdatesDialog
from .prompts import START_COPY, START_PROFILE
from .widgets import busy_cursor


class ModioController:
    """The window's modio, moved off it; ``window`` is the MainWindow."""

    def __init__(self, window) -> None:
        self.window = window

    def modio_account(self) -> None:
        """Set, replace or forget the mod.io API key, and sign in: Settings,
        on its mod.io tab, which is where the account is."""
        from .settings_dialog import TAB_MODIO

        self.window.preferences.show_settings(tab=TAB_MODIO)

    def get_mod_from_modio(self) -> bool:
        """Install a mod from mod.io into the library, and read it in.

        Linked into the mods folder :meth:`ui.where.Where.install_folder` names
        -- and the dialog lets that be changed.  Read in afterwards as
        :meth:`MainWindow.read_installed` reads anything just installed.
        """
        if self.window.index is None:
            return False
        dialog = self._get_dialog(self.window.where.install_folder())
        if dialog.exec() != int(QDialog.DialogCode.Accepted):
            return False
        if dialog.collection is not None:
            return self.after_collection(dialog)
        if dialog.installed is None:
            return False
        # The junctions in the mods folder, which is what the game loads and
        # what is read in; the library folders behind them are the Studio's.
        placed = [linked.link for linked in dialog.installed.linked]
        if not placed:
            return False
        also = [linked.link.name for _mod, done in dialog.also for linked in done.linked]
        note = tr("Installed %(mods)s into the library, linked in %(folder)s.",
                  mods=join_list(p.name for p in placed), folder=placed[0].parent)
        if also:
            note = tr("%(said)s Installed first, because it needs them: %(also)s.",
                      said=note, also=", ".join(also))
        if dialog.installed.set_aside is not None:
            note = tr("%(said)s What was there before is at %(aside)s.",
                      said=note, aside=dialog.installed.set_aside)
        unchecked = [installplan.unchecked(summary) for summary, _done in [*dialog.also, (dialog.summary, None)]]
        note = " ".join([note, *filter(None, unchecked)])
        self.window.status_label.say(note, self.window.where.library_dir())
        # The first of them alone when no folder is loaded, as before: a box
        # of mods is rare, and one import dialog is what was asked for.
        return self.window.read_installed(placed[:1], set(also) | {p.name for p in placed[1:]})

    def _get_dialog(self, folder: str) -> ModioGetDialog:
        return ModioGetDialog(
            self.window.loadout.game,
            secrets.Store(),
            Path(folder) if folder else None,
            self.window,
            open_account=self.modio_account,
            known_ids={layer.modio_id for layer in self.window.loadout.layers if layer.modio_id},
            links=self._modio_links(),
            library_dir=self.window.where.library_dir(),
        )

    def pick_a_collection(self, folder: str):
        """The Get dialog, open on collections; the dialog once a collection
        has been installed, else None.  A method so a test can stand in."""
        dialog = self._get_dialog(folder)
        dialog.set_kind(COLLECTIONS)
        if dialog.exec() != int(QDialog.DialogCode.Accepted):
            return None
        return dialog

    def after_collection(self, dialog) -> bool:
        """A collection's mods are in the folder: read them, and say where
        they fight.

        The game applies every mod in load order and says nothing when two
        of them set the same field; the Studio has just read all of them
        and can count.
        """
        placed = dialog.placed
        if not placed:
            return False
        # Every mod of the collection that is installed now, fetched or not,
        # is in it; only the ones it fetched came with it. A mod you had
        # already stays yours, so switching the collection off leaves it be.
        present = list(dialog.present) or [modfolder.bare_name(Path(p).name) for p in placed]
        self.window.loadout.note_collection(dialog.collection.name, present, getattr(dialog, "brought", None))
        self.window.files.mark_unsaved()
        note = trn(
            "Installed %(n)s mod of %(collection)s into the library, linked in %(folder)s.",
            "Installed %(n)s mods of %(collection)s into the library, linked in %(folder)s.",
            len(placed), collection=dialog.collection.name, folder=placed[0].parent,
        )
        if dialog.set_aside:
            note = tr("%(said)s What was replaced is at %(aside)s.",
                      said=note, aside=dialog.set_aside[0].parent)
        self.window.status_label.say(note, self.window.where.library_dir())
        loaded = bool(self.window.loadout.mods_folder and self.window.loadout.layers)
        if not loaded and not self.window.prompts.ask_read_folder(len(placed), dialog.collection.name):
            self.window.refresh_truth()
            return True
        # The folder read starts with the collection's mods ticked and says
        # which they are; with a folder already loaded, everything it had is
        # re-read as before and the new mods join it.
        focus = None if loaded else set(present)
        if not self.window.load_installed_mods(focus=focus, arrived=set(present), keep_switches=loaded):
            self.window.refresh_truth()
            return True
        self._say_clashes()
        return True

    def _say_clashes(self) -> None:
        """Add to the status line how many fields the loaded mods fight over."""
        clashes = [clash for clash in conflicts.find(self.window.loadout) if not clash.is_harmless]
        if clashes:
            between = {clash.origins for clash in clashes}
            self.window.status_label.setText(trn(
                "%(status)s  %(n)s field is set by more than one of your "
                "mods, in %(pairings)s. “Mods reaching for the same thing” "
                "shows which mod wins each.",
                "%(status)s  %(n)s fields are set by more than one of your "
                "mods, in %(pairings)s. “Mods reaching for the same thing” "
                "shows which mod wins each.",
                len(clashes), status=self.window.status_label.text(),
                pairings=trn("%(n)s pairing", "%(n)s pairings", len(between)),
            ))
        else:
            self.window.status_label.setText(tr(
                "%s  No two of your mods set the same field.",
                self.window.status_label.text(),
            ))

    def check_modio_updates(self, *, only=None) -> None:
        """Ask mod.io whether any loaded mod has a newer file, and remember
        which mod.io mod each one turned out to be.  ``only`` names the mods
        to ask about, by origin -- a mod row's own entry asks about its mod;
        left out, every loaded mod is asked about."""
        if not self.window.loadout.layers:
            QMessageBox.information(
                self.window,
                tr("No mods to check"),
                tr("No mods are loaded. Add mods first, and this checks each "
                   "of them on mod.io."),
            )
            return
        dialog = ModioUpdatesDialog(
            self.window.loadout,
            self.window.loadout.game,
            secrets.Store(),
            self.window,
            open_account=self.modio_account,
            library_dir=self.window.where.library_dir(),
            only=only,
        )
        dialog.exec()
        learned = 0
        for origin, modio_id in dialog.learned.items():
            layer = self.window.loadout.layer(origin)
            if layer is not None and layer.modio_id != modio_id:
                layer.modio_id = modio_id
                learned += 1
        self._tell_the_library(
            dialog.learned, {verdict.origin: verdict.how for verdict in dialog.verdicts},
            {verdict.origin: verdict.installed_file for verdict in dialog.verdicts},
        )
        if learned:
            self.window.files.mark_unsaved()
            self.window.update_title()
        if dialog.verdicts:
            self.window.status_label.setText(dialog._summarise())
        # A folder on disk changed under the loaded reading; read it again so
        # what is on screen is what the game will load.
        if dialog.installed and self.window.loadout.mods_folder:
            self.window.load_installed_mods(keep_switches=True)
        elif dialog.installed:
            self.window.refresh_truth()

    def _modio_links(self) -> dict:
        """ModID -> mod.io id, for every loaded mod that has both."""
        return {
            layer.mod_id.lower(): layer.modio_id
            for layer in self.window.loadout.layers
            if layer.mod_id and layer.modio_id
        }

    def _tell_the_library(self, learned: dict, how: dict, files: dict) -> None:
        """Which mod.io mod each of these turned out to be, written against
        the library's copy of it and not only into the loadout: the library
        outlives the loadout, and is asked what it could get again.

        ``how`` is how each was decided, by origin; by name where it does
        not say.  A copy the Studio installed by id keeps what it was
        installed as (see :func:`core.library.learn`).  One made sure of by
        the zip it was unpacked from is written as that file of that mod,
        which ``files`` names by origin (see :func:`core.library.confirm`).
        """
        from ..core import updates

        library_dir = self.window.where.library_dir()
        for origin, modio_id in learned.items():
            layer = self.window.loadout.layer(origin)
            if layer is None or not layer.folder or not modio_id:
                continue
            name = library.copy_name(library_dir, layer.folder)
            if not name:
                continue
            if how.get(origin) == updates.HASH:
                library.confirm(library_dir, name, modio_id, files.get(origin) or 0)
            else:
                library.learn(library_dir, name, modio_id, how.get(origin) or updates.NAME)

    def offer_missing_mods(self) -> bool:
        """After opening a loadout: if mods it was built on are not installed,
        say so and offer to fetch them.  Returns whether anything was fetched.

        Nothing is asked of mod.io until the offer is taken -- opening a
        loadout is not a click on anything of mod.io's.  And the loadout is
        open either way: it carries every change its mods make, so what is
        missing is missing from the game, not from here.

        Cheap when there is nothing to say, which is nearly always: a loadout
        opened where it was made finds every mod's folder where it was read
        from, and the mods folders are not looked through at all.
        """
        if not self.window.look_for_missing_mods:
            return False
        self.window.look_for_missing_mods = False
        wanted = installplan.wanted_from_loadout(self.window.loadout)
        folders = {layer.origin: layer.folder for layer in self.window.loadout.layers}
        if all(
            folders.get(one.origin) and Path(folders[one.origin]).is_dir() for one in wanted
        ):
            return False
        folder = self.window.where.install_folder()
        with busy_cursor():
            installed = installplan.inventory(
                self.window.loadout.game, folder, self._modio_links(), library_dir=self.window.where.library_dir(),
            )
        absent = installplan.missing(wanted, installed)
        if not absent or not self.window.prompts.ask_missing_mods(wanted, absent):
            return False
        return self.fetch_missing_mods(folder)

    def fetch_missing_mods(self, folder: str = "") -> bool:
        """The plan for this loadout's mods: which are here, which mod.io has,
        and the button that fetches the rest."""
        dialog = InstallPlanDialog(
            self.window.loadout.game,
            secrets.Store(),
            Path(folder or self.window.where.install_folder()),
            installplan.loadout_source(games.get(self.window.loadout.game).modio_game_id, self.window.loadout),
            title=tr("Mods this loadout uses"),
            intro=tr(
                "The mods this loadout was built on, and whether each is "
                "installed here. Missing ones are looked for on mod.io, by the "
                "id the loadout remembers or by name. You get each mod's "
                "current file, which may be newer than the one the loadout was "
                "built against."
            ),
            links=self._modio_links(),
            library_dir=self.window.where.library_dir(),
            parent=self.window,
            open_account=self.modio_account,
        )
        try:
            dialog.exec()
            learned = 0
            for origin, modio_id in dialog.learned.items():
                layer = self.window.loadout.layer(origin)
                if layer is not None and modio_id and layer.modio_id != modio_id:
                    layer.modio_id = modio_id
                    learned += 1
            # The plan finds a mod it has no id for by its name.
            self._tell_the_library(dialog.learned, {}, {})
            if learned:
                self.window.files.mark_unsaved()
                self.window.update_title()
            if not dialog.placed:
                return False
            self.window.status_label.setText(trn(
                "Installed %(n)s mod into the library, linked in %(folder)s.",
                "Installed %(n)s mods into the library, linked in %(folder)s.",
                len(dialog.placed), folder=dialog.placed[0].parent,
            ))
            self.window.refresh_truth()
            return True
        finally:
            dialog.deleteLater()

    def switch_off_collection(self) -> bool:
        """Switch off every loaded mod that came in with one collection.

        In the loadout: nothing reaches the mods folder until Play, like any
        other switch flipped here."""
        names = self.window.loadout.collections()
        if not names:
            return False
        chosen = self.window.prompts.pick_collection(names)
        if not chosen:
            return False
        brought = [layer for layer in self.window.loadout.layers
                   if layer.from_collection == chosen and layer.active]
        # The ones it lists that you had before it: yours, and only switched
        # off when you say so.
        yours = [layer for layer in self.window.loadout.layers
                 if layer.active and layer.from_collection != chosen
                 and chosen in self.window.loadout.listed_in(layer.origin)]
        if yours and self.window.prompts.ask_switch_yours_too(chosen, [layer.label for layer in yours]):
            brought += yours
            yours = []
        # The picker offers a collection that only lists mods you had: it
        # brought none, and must not be said to have.
        brought_any = any(layer.from_collection == chosen for layer in self.window.loadout.layers)
        if not brought and not brought_any:
            self.window.status_label.setText(
                trn("%(collection)s brought no mods. %(n)s it lists that you "
                    "had before it stays on.",
                    "%(collection)s brought no mods. %(n)s it lists that you "
                    "had before it stay on.",
                    len(yours), collection=chosen)
                if yours
                else tr("Every mod %s lists is off in this loadout already.", chosen)
            )
            return False
        if not brought:
            self.window.status_label.setText(
                trn("Every mod %(collection)s brought is off in this loadout "
                    "already. %(n)s you had before it stays on.",
                    "Every mod %(collection)s brought is off in this loadout "
                    "already. %(n)s you had before it stay on.",
                    len(yours), collection=chosen)
                if yours
                else tr("Every mod %s brought is off in this loadout already.", chosen)
            )
            return False
        for layer in brought:
            layer.active = False
        self.window.files.mark_unsaved()
        self.window.changes_panel.refresh()
        self.window.update_title()
        self.window.on_activation_changed()
        said = trn("Switched off %(n)s mod from %(collection)s in this loadout.",
                   "Switched off %(n)s mods from %(collection)s in this loadout.",
                   len(brought), collection=chosen)
        if yours:
            # Translators: %(status)s is what was said so far.
            said = trn("%(status)s %(n)s it lists that you had before it stays on.",
                       "%(status)s %(n)s it lists that you had before it stay on.",
                       len(yours), status=said)
        self.window.status_label.setText(
            tr("%s Play, or Make this live, puts that in your mods folder.", said)
        )
        return True

    def collections_that_brought(self) -> list:
        """The collections any loaded mod came with, first seen first."""
        seen = []
        for layer in self.window.loadout.layers:
            if layer.from_collection and layer.from_collection not in seen:
                seen.append(layer.from_collection)
        return seen

    def take_collection_out(self, chosen: str = "") -> bool:
        """Mods ▸ Take a collection out: the mods a collection
        brought leave the loadout, and every profile; the ones it lists that
        you had stay, as yours.  Their library copies, when no other loadout
        uses them, are offered for deletion."""
        names = self.collections_that_brought()
        if not names:
            return False
        if not chosen:
            chosen = self.window.prompts.pick_collection_to_take_out(names)
        if not chosen:
            return False
        brought = [layer for layer in self.window.loadout.layers if layer.from_collection == chosen]
        if not brought:
            return False
        yours = [layer.label for layer in self.window.loadout.layers
                 if layer.from_collection != chosen and chosen in self.window.loadout.listed_in(layer.origin)]
        edits = sum(len(self.window.loadout.authored_of(layer.origin)) for layer in brought)
        if not self.window.prompts.ask_take_out(chosen, [layer.label for layer in brought], yours, edits):
            return False
        mods_folder = Path(self.window.loadout.mods_folder) if self.window.loadout.mods_folder else None
        links = []
        for layer in brought:
            top = layer.origin.split("/")[0]
            links.append(Path(layer.folder) if layer.folder else (mods_folder / top if mods_folder else None))
            self.window.loadout.drop_layer(layer.origin)
        self.window.loadout.forget_collection(chosen)
        self.window.files.mark_unsaved()
        # Translators: what Edit ▸ Undo would take back, said after "Undo".
        self.window.record(tr("taking %s out", chosen))
        # A copy another loadout still names is that loadout's too. This
        # loadout's own file is left out: it is about to stop naming them.
        mentioned = self.window.library_controller.mentioned_keys(excluding=self.window.loadout_path)
        spare = []
        for layer, link in zip(brought, links, strict=True):
            if link is None or not self._library_copy(link):
                continue
            if modfolder.match_keys(layer.mod_id, layer.origin) & mentioned:
                continue
            spare.append((layer.label, link))
        from ..core import launch as launch_mod

        deleted = 0
        # Deleting what the running game loaded from waits, as a No would:
        # the next Play switches them off either way.
        if (spare and not launch_mod.running(self.window.loadout.game)
                and self.window.prompts.ask_delete_copies(chosen, [label for label, _link in spare])):
            for _label, link in spare:
                try:
                    deleted += library.remove_copy(self.window.where.library_dir(), link)
                except OSError:
                    continue
        self.window.changes_panel.refresh()
        self.window.editor.refresh_from_loadout()
        self.window.mark_output_dirty()
        self.window.refresh_truth(say=False)
        self.window.start.refresh_source_panel()
        self.window.update_title()
        said = trn("Took %(collection)s out: %(n)s mod it brought left this loadout "
                   "and every profile.",
                   "Took %(collection)s out: %(n)s mods it brought left this loadout "
                   "and every profile.",
                   len(brought), collection=chosen)
        if yours:
            # Translators: %(status)s is what was said so far.
            said = trn("%(status)s %(n)s it lists that you had stays, as yours.",
                       "%(status)s %(n)s it lists that you had stay, as yours.",
                       len(yours), status=said)
        if deleted:
            said = trn("%(status)s Deleted %(n)s library copy nothing else used.",
                       "%(status)s Deleted %(n)s library copies nothing else used.",
                       deleted, status=said)
        elif spare:
            said = tr("%s Their library copies are kept, and Play switches them off.", said)
        self.window.status_label.setText(said)
        return True

    def _library_copy(self, link: Path) -> bool:
        """Whether ``link`` is a copy in the library -- linked, or switched
        off and remembered -- and so the library's to delete."""
        if library.kind_of(link, self.window.where.library_dir()) == library.COPY:
            return True
        return link.name in library.unlinked(self.window.where.library_dir())

    def loadout_from_collection(self, start: str = "") -> bool:
        """Aim 3's second button: a new loadout holding a collection.

        *Start from* is asked first, when something is live: nothing, to try
        the collection on its own, or a copy of the live loadout, to try it on
        top of what you play.  Either way the loadout that is live is not
        touched -- a copy is read from its file, never opened in its place --
        which is the property that lets a collection be tried at all.
        """
        live = self.window.live.live_loadout()
        base = None
        if live:
            # Asked by the card already, before anything was cleared.
            start = start or self.window.prompts.ask_start_from(self.window.files.name_of(live))
            if not start:
                return False
            if start == START_PROFILE:
                return self.collection_as_profile(live)
            if start == START_COPY:
                try:
                    base = Loadout.load(Path(live))
                except Exception as exc:  # noqa: BLE001 - said, not raised
                    QMessageBox.warning(self.window, tr("Could not read the live loadout"), str(exc))
                    return False
        folder = (base.mods_folder if base is not None else "") or self.window.where.install_folder()
        dialog = self.pick_a_collection(folder)
        if dialog is None or dialog.collection is None or not dialog.placed:
            return False
        present = list(dialog.present) or [modfolder.bare_name(Path(p).name) for p in dialog.placed]
        focus = set(present)
        if base is not None:
            # The copy: everything the live loadout holds, carried over as
            # a fork carries it, with the collection added.
            carried = {layer.origin.split("/")[0] for layer in base.layers if layer.active}
            kept = {
                key: getattr(self.window.loadout, key)
                for key in (
                    "source_path", "game", "base_archive", "base_assets",
                    "base_fingerprint", "base_update", "base_game_version",
                )
            }
            self.window.loadout.__dict__.update(base.__dict__)
            self.window.loadout.__dict__.update(kept)
            # A copy: its own mod is its own, not the live loadout's -- named
            # after the copy below, once the copy has its name.
            self.window.loadout.own_project_anew()
            self.window.loadout.own.creator = self.window.preferences.creator_name()
            self.window.preferences.hand_over(self.window.loadout)
            self.window.loadout.set_up_as(AIM_MANAGE)
            focus |= carried
        self.window.loadout.note_collection(dialog.collection.name, present, getattr(dialog, "brought", None))
        if base is not None and base.mods_folder:
            self.window.settings.setValue(self.window.mods_key("last_mods_folder"), base.mods_folder)
        if not self.window.load_installed_mods(focus=focus, arrived=set(present), keep_switches=base is not None):
            if base is not None:
                # The copy was only ever for this; half of one is not a start.
                self.window.reset_loadout()
            return False
        self.window.files.name_loadout(
            "%s + %s" % (self.window.files.name_of(live), dialog.collection.name)
            if base is not None else dialog.collection.name
        )
        if base is not None:
            self.window.files.rename_copied_profile(self.window.files.name_of(live), self.window.files.loadout_name())
            self.window.profile_renames.clear()
            self.window.loadout.own_mod_anew(
                self.window.files.loadout_name(), came_from=self.window.files.name_of(live),
            )
            self.window.output.load_loadout_fields()
        self._say_clashes()
        return True

    def collection_as_profile(self, live) -> bool:
        """A collection on top of what you play, as a new profile of the live
        loadout: the collection's mods join its mod list, on in the new
        profile -- which starts switched as the live one is -- and off in the
        rest.  The live loadout is opened for it, and nothing is live but
        what was until Play."""
        here = disk.same_file(live, self.window.loadout_path)
        if not here and not self.window.files.confirm_discard():
            return False
        try:
            base = Loadout.load(Path(live))
        except Exception as exc:  # noqa: BLE001 - said, not raised
            QMessageBox.warning(self.window, tr("Could not read the live loadout"), str(exc))
            return False
        dialog = self.pick_a_collection(base.mods_folder or self.window.where.install_folder())
        if dialog is None or dialog.collection is None or not dialog.placed:
            return False
        # Unsaved work here was asked about above, before the collection was
        # fetched; opening the live loadout does not ask again.
        opened = here or (
            self.window.load_loadout(Path(live), asked=True)
            and disk.same_file(live, self.window.loadout_path)
        )
        if not opened:
            return False
        loadout = self.window.loadout
        present = list(dialog.present) or [modfolder.bare_name(Path(p).name) for p in dialog.placed]
        loadout.note_collection(dialog.collection.name, present, getattr(dialog, "brought", None))
        like = self.window.live.live_profile_here() or loadout.chosen_profile().name
        name = dialog.collection.name
        number = 2
        while any(other.casefold() == name.casefold() for other in loadout.profile_names()):
            name = "%s (%d)" % (dialog.collection.name, number)
            number += 1
        loadout.add_profile(name, like=like)
        loadout.choose_profile(name)
        # Every mod it lists is on in its profile, the ones you had included;
        # the folder read keeps these and brings the new ones in on.
        wanted = set(present)
        for layer in loadout.layers:
            if layer.origin.split("/")[0] in wanted:
                layer.active = True
        self.window.files.mark_unsaved()
        if loadout.mods_folder:
            self.window.settings.setValue(self.window.mods_key("last_mods_folder"), loadout.mods_folder)
        self.window.load_installed_mods(arrived=wanted, keep_switches=True)
        self.window.start.refresh_source_panel()
        self.window.update_title()
        self.window.status_label.setText(trn(
            "%(profile)s is a new profile of %(loadout)s: the collection's "
            "%(n)s mod is on in it and off in the others. %(like)s stays what "
            "the game loads until you Play the new profile.",
            "%(profile)s is a new profile of %(loadout)s: the collection's "
            "%(n)s mods are on in it and off in the others. %(like)s stays "
            "what the game loads until you Play the new profile.",
            len(present), profile=name, loadout=self.window.files.loadout_name(), like=like,
        ))
        self._say_clashes()
        return True

    def unsubscribe_from_cache(self) -> bool:
        """The checklist's last row: the game's mod browser's copies of mods
        the library has its own of, and which to unsubscribe from in game.
        The Studio does not unsubscribe for you: the game's client did not
        let go of a copy unsubscribed from outside the game."""
        found = self.window.truth
        if found is None or not found.left_in_cache:
            return False
        self.show_unsubscribe(list(found.left_in_cache))
        # The game may have been started meanwhile, and let go of some.
        self.window.refresh_truth(say=False)
        return True

    def show_unsubscribe(self, left) -> None:
        """The list.  A method so a test can stand in for it."""
        from ..core import modio_cache
        from .unsubscribe_dialog import UnsubscribeDialog

        def look_again():
            self.window.refresh_truth(say=False)
            return list(self.window.truth.left_in_cache) if self.window.truth is not None else []

        dialog = UnsubscribeDialog(
            left, self.window, game_account=modio_cache.game_account(self.window.loadout.game), look_again=look_again,
            afresh=self._afresh_plan(), start_afresh=self.start_modio_afresh,
        )
        dialog.exec()
        dialog.deleteLater()

    def _afresh_plan(self):
        """Whether the game's mod browser's folder can be started afresh now
        -- see core.unsubscribe.afresh -- or None."""
        from ..core import launch as launch_mod

        if self.window.truth is None:
            return None
        return unsubscribe.afresh(self.window.truth, running=launch_mod.running(self.window.loadout.game))

    def start_modio_afresh(self):
        """Set the game's mod browser's folder aside, asked again of the disk
        first, so the game rebuilds it from the account's subscriptions; where
        it went.  Raises what core.unsubscribe.start_afresh raises."""
        self.window.refresh_truth(say=False)
        plan = self._afresh_plan()
        if plan is None:
            raise ValueError(tr("This game has no in-game mod browser."))
        aside = unsubscribe.start_afresh(plan, places.backups_dir())
        self.window.refresh_truth(say=False)
        self.window.status_label.setText(tr(
            "The game's mod browser's folder is set aside to %s. The game "
            "starts it afresh from your subscriptions when its mod browser "
            "next opens.", aside))
        return aside

    def on_mod_published(self, text: str) -> None:
        """Publishing is the milestone exporting is, and more: what went out
        is now the baseline the next release is measured from, so it is
        saved at once."""
        self.window.files.autosave()
        self.window.status_label.setText(text)
