"""The Studio's library, as the window works with it: bringing the mods
the game loads into it (the conversion) and taking them back out,
managing a folder of your own in place,
installing a mod zip dropped on the window, adding mods to the loadout
open now, and which of the library's mods no loadout mentions any more.

``core.library`` holds the copies and the links and ``core.convert`` plans
the move; this is the window's side -- the questions it asks, the full copy
first if you want one, what it says afterwards -- held as
``window.library_controller`` (Stage 3.3 of
docs/proposals/consolidation-proposal.md).
"""

from __future__ import annotations

import contextlib
import time
from pathlib import Path

from PySide6.QtCore import QEventLoop, Qt
from PySide6.QtWidgets import QApplication, QMessageBox, QProgressDialog

from ..core import (
    disk,
    filecache,
    games,
    library,
    modfolder,
    modzip,
    places,
    switches,
    truth,
)
from ..core.changes import (
    AIM_MAKE,
    AIM_MANAGE,
    AIM_TWEAK,
    DEFAULT_PROFILE,
    Loadout,
)
from ..core.i18n import join_list, size_text, tr, trn
from .prompts import OTHER_GAME_SKIP, ZIP_INSTALL, ZIP_OPEN
from .widgets import busy_cursor, named, open_on_disk, wrappable_path
from .workers import ZipInstallWorker


def is_mod_zip(path) -> bool:
    """Whether a file dropped on the window is one to try installing."""
    path = Path(path)
    return path.suffix.lower() == ".zip" and path.is_file()


class LibraryController:
    """The window's library_controller, moved off it; ``window`` is the MainWindow."""

    def __init__(self, window) -> None:
        self.window = window
        # True while a conversion runs, so the offer of one is not made
        # again in the middle of it.
        self._converting = False

    def open_folder(self) -> None:
        """The library's folder, in Explorer: every game's mods are in it."""
        where = library.root(self.window.preferences.library_root())
        if where.is_dir():
            open_on_disk(where)
        else:
            QMessageBox.information(
                self.window, tr("No library yet"),
                tr("The library will be %s.\n"
                   "\n"
                   "The Studio makes the folder the first time it installs a mod.",
                   wrappable_path(where)),
            )

    def move_into_library(self) -> bool:
        """Move the user's own mod folders into the library: the conversion,
        narrowed to them -- there is one flow, not two."""
        from ..core import convert

        return self.convert_to_library(only={convert.YOURS})

    def convert_to_library(self, only=None) -> bool:
        """Bring what the game loads into the library, once, without losing a
        mod, a switch or a setting.  See :mod:`core.convert`.

        Starts from a loadout of what the game loads now, which is the
        snapshot: every mod, on or off, as it is.  Then the survey, and
        nothing moves until it is answered.  Then the move, other tools'
        profiles as the loadout's own, and the loadout made live -- after
        which the next Play's loader log is the proof.
        """
        from ..core import convert
        from ..core import launch as launch_mod

        if self.window.index is None:
            return False
        if self.window.prompts.game_running(
            self.window.loadout.game, tr("Close the game first: its mods are about to move."),
        ):
            return False
        folder = self.window.where.mods_folder()
        if not folder:
            return False
        # The "before": a loadout of what the game loads now.
        if not (self.window.loadout.own.loads_last and self.window.loadout.layers):
            if not self.window.prompts.ask_start_from_what_i_play():
                return False
            self._converting = True
            try:
                self.window.start.choose_aim(AIM_MANAGE)
            finally:
                self._converting = False
            if not (self.window.loadout.own.loads_last and self.window.loadout.layers):
                return False
        found = truth.read(self.window.loadout, folder, library_dir=self.window.where.library_dir())
        if not found.matches:
            # The snapshot has to be what the game loads now, or making the
            # loadout live afterwards would switch things -- and the
            # conversion is meant to change where the bytes are, nothing else.
            if not self.window.prompts.ask_bring_in_first(found):
                return False
            if not self.window.load_installed_mods():
                return False
            found = truth.read(self.window.loadout, folder, library_dir=self.window.where.library_dir())
        with busy_cursor():
            options = switches.game_options(self.window.loadout.game)
            surveyed = convert.survey(found, convert.settings_of(found, options))
            install = launch_mod.install_for(self.window.loadout.game, self.window.game_folder)
            managers = convert.other_managers(self.window.loadout.game, install)
            found_profiles = convert.profiles(self.window.loadout.game, install, Path(folder))
            # A second conversion finds the profiles the first one made
            # profiles of this loadout. Offered ticked again, each came in
            # once more under its name with a number.
            taken = {name.casefold() for name in self.window.loadout.profile_names()}
            for profile in found_profiles:
                profile.here = profile.name.casefold() in taken
        answer = self.window.prompts.ask_convert(
            surveyed, managers, found_profiles, only,
            library_dir=self.window.where.library_dir(), library_used=self.library_used(),
        )
        if answer is None:
            return False
        rows, chosen_profiles = answer[0], answer[1]
        full = None
        if len(answer) > 2 and answer[2]:
            full = self._make_full_copy(convert.folders_to_copy(rows))
            if full is None:
                return False

        record = convert.snapshot(found, options)
        try:
            # Nothing moves into a library whose records could not say so.
            library.check_records(self.window.where.library_dir())
            kept = convert.save_snapshot(record, self.window.where.library_dir())
            # Before anything moves: what undoing it will need put back.
            pinned = convert.pin_before(self.window.loadout.game, Path(folder), rows)
        except OSError as exc:
            QMessageBox.warning(
                self.window, tr("Could not write down what the game loads now"), str(exc))
            return False
        self.window.files.ensure_loadout_file()
        if self.window.on_disk and self.window.dirty:
            self.window.files.autosave()

        with busy_cursor():
            done = convert.convert(rows, self.window.where.library_dir(), Path(folder), self.window.loadout.game)
        # The loadout follows its folders: a moved one is its link now, a
        # switched-off one the link Play will put back, a cache copy the
        # library's link in the mods folder.
        for layer in self.window.loadout.layers:
            moved = done.paths.get(str(layer.folder)) if layer.folder else None
            if moved:
                layer.folder = moved
                self.window.files.mark_unsaved()
        made = self._profiles_from(chosen_profiles)
        if self.window.dirty and self.window.on_disk:
            # Where its mods are now, and the profiles made from yours, are
            # what the conversion did to this loadout, and undoing it reads
            # both back out of the file. Neither is typed work, so neither
            # would stop the window closing without them: saved here. It
            # used to be left unsaved, and a loadout reopened after the
            # conversion still named the folders it had moved.
            self.window.files.autosave()
        record["journal"] = {
            "mods_folder": str(folder), "steps": done.steps, "backups": pinned,
            "loadout": str(self.window.loadout_path or ""), "paths": done.paths,
            "profiles": made, "full_copy": str(full or ""),
        }
        with contextlib.suppress(OSError):
            convert.write_record(kept, record)
        if done.failed:
            QMessageBox.warning(
                self.window, tr("Some mods could not be converted"),
                "\n".join("%s: %s" % pair for pair in done.failed[:12]),
            )
        # Made live, which for a converted folder moves little: the links are
        # where the folders were, and an off one is already no link.
        self.window.live.make_live(confirm=False, launch=False, quiet=True)
        self.window.refresh_truth(say=False)
        same, gained, lost = convert.compare(record, self.window.truth) if self.window.truth is not None else ([], [], [])
        lines = [done.describe()]
        if not gained and not lost:
            lines.append(trn(
                "Before, the game loaded %(n)s mod. It loads the same one now, "
                "from the library.",
                "Before, the game loaded %(n)s mods. It loads the same ones now, "
                "from the library.", len(same),
            ))
        else:
            if lost:
                lines.append(tr("No longer loading: %s.", ", ".join(lost[:8])))
            if gained:
                lines.append(tr("Loading now and not before: %s.", ", ".join(gained[:8])))
        if len(made) == 1:
            lines.append(tr(
                "Your profile %s is now a profile of this loadout. The picker "
                "beside its name on the loadout card chooses one.", named(made[0]),
            ))
        elif made:
            lines.append(trn(
                "Your %(n)s profile is now a profile of this loadout: "
                "%(names)s. The picker beside its name on the loadout card "
                "chooses one.",
                "Your %(n)s profiles are now profiles of this loadout: "
                "%(names)s. The picker beside its name on the loadout card "
                "chooses one.", len(made), names=", ".join(named(name) for name in made),
            ))
        left = self.window.truth.left_in_cache if self.window.truth is not None else []
        if left:
            lines.append(trn(
                "The game's mod browser keeps its own copies of %(n)s of them, "
                "and keeps them up to date, until you unsubscribe from them in "
                "its own mod browser: Unsubscribe in game… on the fourth card "
                "says which.",
                "The game's mod browser keeps its own copies of %(n)s of them, "
                "and keeps them up to date, until you unsubscribe from them in "
                "its own mod browser: Unsubscribe in game… on the fourth card "
                "says which.", len(left),
            ))
        lines.append(tr(
            "What the game loaded before is kept at %s. Press Play to prove "
            "it: the loader's log is checked against this loadout.", wrappable_path(kept),
        ))
        if full:
            lines.append(tr(
                "To take all of it back, Game \u25b8 Library \u25b8 Undo the "
                "conversion to the library moves every folder back where it "
                "was. A full copy of your mods folder is at %s.", wrappable_path(full),
            ))
        else:
            lines.append(tr(
                "To take all of it back, Game \u25b8 Library \u25b8 Undo the "
                "conversion to the library moves every folder back where it "
                "was."
            ))
        self.window.status_label.say(" ".join(lines[:2]), self.window.where.library_dir())
        self.window.prompts.report_conversion("\n\n".join(lines))
        return bool(done.moved or done.copied or done.set_aside or made)

    def waiting(self) -> list:
        """The survey's rows that converting would do something to, for what
        the game loads as last read."""
        from ..core import convert

        if self.window.truth is None:
            return []
        return convert.survey(self.window.truth).to_do

    def offer_conversion(self) -> bool:
        """What the game loads has been read into a new loadout, with mods
        outside the library: the moment to offer converting them.  Whether it
        was offered.

        Each time, while there is anything to convert.  It is asked only as
        a loadout is set up from what the game loads, which is seldom, and
        never on reading a mod into a loadout there already is.  It used to
        be offered only while the library had never been used, so one mod
        installed from mod.io beforehand meant it never was.

        Asked with the fourth card in view: the question names it as where
        the offer stays, and the answer either way leaves the reader there.
        """
        if self._converting:
            return False
        waiting = self.waiting()
        if not waiting:
            return False
        self.window.source.show_truth_card()
        if self.window.prompts.ask_convert_now(len(waiting), self.window.loadout.game):
            self.convert_to_library()
        return True

    def library_used(self) -> bool:
        """Whether this game's library has ever held anything linked in."""
        return bool(library.links(self.window.where.library_dir()) or library.references(self.window.where.library_dir()))

    def _make_full_copy(self, folders):
        """The whole of ``folders`` into the backups, with a progress dialog
        that can stop it.  Where it went, or None when it did not finish."""
        from PySide6.QtWidgets import QProgressDialog

        from ..core import convert

        if not folders:
            return ""
        progress = QProgressDialog(
            tr("Copying your mods folder…"),
            # Translators: a button that stops the copy.
            tr("Stop"), 0, 1000, self.window,
        )
        progress.setWindowTitle(tr("Keeping a full copy"))
        progress.setWindowModality(Qt.WindowModality.WindowModal)
        progress.setMinimumDuration(0)
        progress.show()

        def said(done, total, name):
            progress.setValue(int(1000 * done / total) if total else 0)
            progress.setLabelText(tr(
                "Copying your mods folder… %(done)s of %(total)s\n%(name)s",
                done=size_text(done),
                total=size_text(total), name=name,
            ))
            QApplication.processEvents()

        try:
            return convert.full_copy(folders, progress=said, stopped=progress.wasCanceled)
        except convert.Stopped:
            self.window.status_label.setText(
                tr("Stopped keeping a full copy. No mod was converted."))
            return None
        except OSError as exc:
            QMessageBox.warning(
                self.window, tr("Could not keep a full copy"),
                tr("%s\n"
                   "\n"
                   "No mod was converted.", exc),
            )
            return None
        finally:
            progress.close()
            progress.deleteLater()

    def _profiles_from(self, found_profiles) -> list:
        """Each of another tool's profiles as a profile of this loadout: the
        same mods, switched as the profile says.  The names they were made
        under, which are theirs unless one is taken here."""
        from ..core import convert

        made = []
        if not found_profiles:
            return made
        # With a second profile, the first needs a name of its own.
        self.window.loadout.name_first_profile()
        for found in found_profiles:
            name = self._free_name_like(found.name)
            switched, _missing = convert.profile_switches(self.window.loadout, found)
            self.window.loadout.add_profile(name).switches = dict(switched)
            made.append(name)
        self.window.files.mark_unsaved()
        self.window.start.refresh_source_panel()
        return made

    def _free_name_like(self, name: str) -> str:
        """``name``, or ``name (2)`` and on when a profile here has it: a name
        the loadout will take as it is.  Trimmed first, as the loadout trims
        it, or a name that is only taken once trimmed would pass here and be
        refused there."""
        name = name.strip() or DEFAULT_PROFILE
        taken = {other.casefold() for other in self.window.loadout.profile_names()}
        tried, number = name, 2
        while tried.casefold() in taken:
            tried = "%s (%d)" % (name, number)
            number += 1
        return tried

    def follow_conversions(self) -> int:
        """Bring this loadout's mods up to where a conversion moved them, if
        its file still names where they were -- a loadout converted before
        the conversion saved it, and closed.  Saved at once, since where a
        mod is is no typed work; how many moved."""
        from ..core import convert

        if self.window.loadout_path is None:
            return 0
        try:
            moved = convert.moved_folders(self.window.where.library_dir(), self.window.loadout_path)
        except OSError:
            return 0
        count = 0
        for layer in self.window.loadout.layers:
            if layer.folder and moved.get(str(layer.folder)):
                layer.folder = moved[str(layer.folder)]
                count += 1
        if count and self.window.on_disk:
            self.window.files.save_when_free()
            self.window.status_label.setText(trn(
                "%(n)s of this loadout's mods still pointed to where they "
                "were before the conversion to the library. They now point "
                "to where it moved them, and the loadout is saved.",
                "%(n)s of this loadout's mods still pointed to where they "
                "were before the conversion to the library. They now point "
                "to where it moved them, and the loadout is saved.", count,
            ))
        return count

    def follow_renames(self) -> int:
        """Bring this loadout's mods under the name a rename moved their
        built copy to, matched by ModID (:func:`core.builtby.follow`).
        Saved at once, as :meth:`follow_conversions` saves; how many moved."""
        from ..core import builtby

        try:
            moved = builtby.follow(self.window.loadout, self.window.where.library_dir())
        except OSError:
            return 0
        if moved and self.window.on_disk:
            self.window.files.save_when_free()
        if moved:
            self.window.status_label.setText(trn(
                "%(n)s of this loadout's mods was renamed since it was last "
                "opened, and its folder moved with the name: %(names)s. The "
                "loadout follows it.",
                "%(n)s of this loadout's mods were renamed since it was last "
                "opened, and their folders moved with the names: %(names)s. "
                "The loadout follows them.", len(moved),
                names=", ".join("%s → %s" % pair for pair in moved),
            ))
        return len(moved)

    def give_own_folders(self) -> list:
        """Move each loaded mod of yours given a new ModID to a folder of its
        own (:func:`core.builtby.own_folders`), before anything is built:
        each as ``(the name it had, the name it has)``.  The old folder stays
        the old mod's."""
        from ..core import builtby

        moved = builtby.own_folders(self.window.loadout)
        if not moved:
            return moved
        self.window.patches.mod_renamed()
        self.window.status_label.setText(trn(
            "%(names)s has a new ModID, so it is another mod to the game: it goes into a "
            "folder of its own, and the old one stays the old mod's.",
            "%(names)s have new ModIDs, so they are other mods to the game: each goes into "
            "a folder of its own, and the old ones stay the old mods'.", len(moved),
            names=", ".join("%s → %s" % pair for pair in moved),
        ))
        return moved

    def undo_conversion(self) -> bool:
        """Take the last conversion back: every folder where it was, the
        library's copies of the game's mods gone, the files making it live
        rewrote put back, and the loadouts it made from profiles set aside.
        See :func:`core.convert.undo`."""
        import shutil

        from ..core import convert

        found = convert.last_undoable(self.window.where.library_dir())
        if found is None:
            QMessageBox.information(
                self.window, tr("No conversion to undo"),
                tr("No conversion to the library for %s is waiting to be "
                   "undone.", games.get(self.window.loadout.game).label),
            )
            return False
        if self.window.prompts.game_running(
            self.window.loadout.game, tr("Close the game first: its mods are about to move."),
        ):
            return False
        kept = convert.read_record(found)
        why = convert.cannot_undo(kept)
        if why:
            QMessageBox.warning(self.window, tr("Could not read the conversion's record"), why)
            return False
        record = kept.data
        journal = record.get("journal") or {}
        steps = journal.get("steps") or []
        count = {op: sum(1 for step in steps if step.get("op") == op) for op in ("move", "copy", "aside")}
        # Loadouts made one per profile, by conversions from before profiles;
        # and the profiles made since.
        made = [Path(path) for path in journal.get("made") or [] if Path(path).is_file()]
        profiles = [str(name) for name in journal.get("profiles") or []]
        if not self.window.prompts.ask_undo_conversion(record, count, made, profiles):
            return False
        try:
            with busy_cursor():
                undone = convert.undo(found, self.window.where.library_dir())
        except OSError as exc:
            QMessageBox.warning(self.window, tr("Could not read the conversion's record"), str(exc))
            return False
        # The loadout the conversion started from follows its folders back.
        back = {new: old for old, new in (journal.get("paths") or {}).items()}
        converted = journal.get("loadout") or ""
        if converted and disk.same_file(converted, self.window.loadout_path):
            for layer in self.window.loadout.layers:
                if layer.folder and str(layer.folder) in back:
                    layer.folder = back[str(layer.folder)]
                    self.window.files.mark_unsaved()
            if self.window.dirty and self.window.on_disk:
                self.window.files.autosave()
        elif converted and Path(converted).is_file():
            with contextlib.suppress(OSError, ValueError):
                other = Loadout.load(Path(converted))
                for layer in other.layers:
                    if layer.folder and str(layer.folder) in back:
                        layer.folder = back[str(layer.folder)]
                self._drop_profiles(other, profiles)
                other.save(Path(converted))
        dropped = []
        if converted and disk.same_file(converted, self.window.loadout_path):
            dropped = self._drop_profiles(self.window.loadout, profiles)
            if dropped:
                self.window.files.mark_unsaved()
                if self.window.on_disk:
                    self.window.files.autosave()
                self.window.start.refresh_source_panel()
        elif profiles:
            dropped = list(profiles)
        # The loadouts it made from profiles did not exist before it: set
        # aside, not deleted.
        aside = []
        if made:
            home = places.backups_dir() / "undone-loadouts" / time.strftime("%Y-%m-%d_%H-%M-%S")
            for path in made:
                if disk.same_file(path, self.window.loadout_path):
                    continue
                try:
                    home.mkdir(parents=True, exist_ok=True)
                    shutil.move(str(path), str(home / path.name))
                    aside.append(self.window.files.name_of(path))
                except OSError as exc:
                    undone.failed.append((path.name, str(exc)))
        library.clear_live(self.window.where.library_dir())
        self.window.refresh_truth(say=False)
        lines = [undone.describe()]
        if aside:
            lines.append(tr("Loadouts it made from your profiles, set aside: %s.",
                            ", ".join(named(name) for name in aside)))
        if dropped:
            lines.append(tr("Profiles it made from yours, deleted: %s.",
                            ", ".join(named(name) for name in dropped)))
        if journal.get("full_copy"):
            lines.append(tr("The full copy kept before it is still at %s.",
                            wrappable_path(journal["full_copy"])))
        lines.append(tr("Your mods folders are as they were before the conversion."))
        self.window.status_label.say(lines[0], self.window.where.mods_folder())
        self.window.prompts.report_undo("\n\n".join(lines))
        return not undone.failed

    def _drop_profiles(self, loadout, names) -> list:
        """Delete the profiles ``names`` of ``loadout`` that are still there,
        never its last; the ones deleted."""
        dropped = []
        for name in names:
            if loadout.find_profile(name) is not None and len(loadout.profiles) > 1:
                loadout.delete_profile(name)
                dropped.append(name)
        return dropped

    def put_library_back(self) -> bool:
        """Make the mods folder whole without the library: every copy's
        junction becomes the folder again, every reference's goes.

        The way out.  The Studio can be uninstalled after this without the
        game losing a mod, which is what makes it safe to have put them in
        the library in the first place.
        """
        folder = self.window.where.mods_folder()
        if not folder:
            QMessageBox.information(
                self.window, tr("No mods folder"),
                tr("The Studio does not know which mods folder to look in."),
            )
            return False
        if self.window.prompts.game_running(
            self.window.loadout.game, tr("Close the game first: its mods are about to move."),
        ):
            return False
        folders = switches.mod_paths(self.window.loadout.game, folder)
        held = library.linked_in(folders, self.window.where.library_dir())
        if not held:
            QMessageBox.information(
                self.window, tr("No mods to put back"),
                tr("No mod in your mods folder came from the library."),
            )
            return False
        if not self.window.prompts.confirm_put_back(held):
            return False
        try:
            done = library.put_back(folders, self.window.where.library_dir())
        except OSError as exc:
            QMessageBox.warning(self.window, tr("Could not put the mods back"), str(exc))
            return False
        if done.failed:
            QMessageBox.warning(
                self.window, tr("Some mods could not be put back"),
                "\n".join("%s: %s" % pair for pair in done.failed[:12]),
            )
        self.window.status_label.say(tr("Put back: %s", done.describe()),
                                     self.window.where.mods_folder())
        self.window.refresh_truth(say=False)
        return bool(done.materialised or done.unlinked)

    def bring_strangers_in(self) -> bool:
        """The fourth card's first row: the mods this loadout does not
        mention, in the mods folder or the library, read into it -- on or off as the folder
        has them, so the loadout still says what the game loads, with this
        loadout's own switches kept for the rest."""
        found = self.window.truth
        if found is None or not found.strangers:
            return False
        names = {modfolder.bare_name(mod.installed.folder_name) for mod in found.strangers}
        tops = {layer.origin.split("/")[0] for layer in self.window.loadout.layers}
        # The library's have no link for the folder's read to find.
        return self.window.load_installed_mods(
            focus=names | tops, keep_switches=True,
            adding=library.shelved(self.window.where.library_dir(), names),
        )

    def add_mods(self) -> bool:
        """More mods, read into the loadout open now beside the ones it has,
        which keep their switches.  Whether anything landed.

        Into a loadout that has read a mods folder, the folder's list opens
        on this loadout's mods as they were read, ticked, and the folder's
        and the library's other mods listed by name: it is read again once
        one is ticked, as a mod installed into it is, so they take their
        places in load order -- once, whatever is ticked after (D3).  Into
        an empty loadout, the library's list, as a loadout from the library starts.
        Into anything else, the one-mod import, which lists the library's
        mods too.

        Whichever it is, mod.io and a zip are a button away on it: the mods
        that are not on this PC yet are added the same way as the ones that
        are, rather than from menu entries of their own.
        """
        window = self.window
        if window.index is None:
            return False
        loadout = window.loadout
        detours = (
            (tr("&From mod.io…"),
             tr("Find a mod on mod.io, install it into the library and add it here."),
             window.modio.get_mod_from_modio),
            (tr("From a &zip…"),
             tr("Install a mod zip from anywhere on this PC into the library, and add it here."),
             self.add_zips),
        )
        if loadout.mods_folder and loadout.layers:
            tops = {layer.origin.split("/")[0] for layer in loadout.layers}
            # The folder this loadout was read from, not the one read last.
            window.settings.setValue(window.mods_key("last_mods_folder"), loadout.mods_folder)
            landed = window.load_installed_mods(
                focus=tops, keep_switches=True, standing=True, detours=detours,
            )
        elif loadout.own.loads_last and not loadout.layers:
            landed = window.load_installed_mods(picked=[], detours=detours)
            if landed and window.loadout_path is None:
                # Named as the Start tab's way to it names it, with its patch
                # after it: it was "New loadout" holding "My Anno Mod", and
                # saved as that (R3-21, hands-on run 3).
                window.files.name_loadout(window.start.list_loadout_name())
        else:
            landed = window.import_mod(separate=True, detours=detours)
        if landed:
            # Closed with nothing imported, nothing changed to look at again.
            window.refresh_truth(say=False)
        return landed

    def add_zips(self) -> bool:
        """Mod zips picked from anywhere, installed as a zip dropped on the
        window is -- see :meth:`install_dropped`.  Whether anything landed."""
        chosen = self.window.prompts.pick_mod_zips()
        return bool(chosen) and self.install_dropped(chosen)

    def manage_own_folder(self) -> bool:
        """Link a mod folder of the user's own into the mods folder and manage
        it in place -- a checkout edited elsewhere, never copied."""
        folder = self.window.where.mods_folder()
        if not folder:
            QMessageBox.information(
                self.window, tr("No mods folder"),
                tr("The Studio does not know which mods folder to link into."),
            )
            return False
        chosen = self.window.prompts.pick_own_folder()
        if not chosen:
            return False
        if not modzip.is_mod_folder(chosen):
            QMessageBox.warning(
                self.window, tr("Not a mod folder"),
                tr("%s has neither a data folder nor a modinfo at the top, so "
                   "the game would not load it as a mod.", wrappable_path(chosen)),
            )
            return False
        try:
            linked = library.refer(self.window.where.library_dir(), Path(chosen), Path(folder))
        except OSError as exc:
            QMessageBox.warning(self.window, tr("Could not link the folder"), str(exc))
            return False
        if linked.set_aside is not None:
            note = tr(
                "Linked %(mod)s into %(folder)s as %(name)s. The Studio "
                "manages it where it is. What was at that name before is at "
                "%(aside)s.",
                mod=chosen, folder=folder, name=linked.link.name,
                aside=linked.set_aside,
            )
        else:
            note = tr(
                "Linked %(mod)s into %(folder)s as %(name)s. The Studio "
                "manages it where it is.", mod=chosen, folder=folder, name=linked.link.name,
            )
        self.window.status_label.say(note, folder)
        self.window.refresh_truth(say=False)
        return True

    def install_dropped(self, paths) -> bool:
        """Mod zips dropped on the window: installed into the library, once
        you have seen what they hold, and read into the loadout open now --
        or, for one mod alone, opened as a loadout of its own; or, with no
        loadout to read them into, only installed.

        Refused with no game files loaded.  A zip does not say which game it
        is for, so it goes into the library of the game that is open, and
        with none open there is no library to name; nor could it be read in.

        Returns whether anything landed in the loadout.
        """
        window = self.window
        archives = [Path(path) for path in paths if is_mod_zip(path)]
        if not archives:
            return False
        if window.index is None:
            window.prompts.report_dropped(
                tr("Load a game's files first"),
                tr("A dropped mod goes into the library of the game whose "
                   "files are loaded. No game files are loaded yet.\n"
                   "\n"
                   "Load a game's files on the Start tab, then drop the zip "
                   "again."),
            )
            return False
        folder = window.where.install_folder()
        if not folder:
            window.prompts.report_dropped(
                tr("No mods folder"),
                tr("The Studio does not know which mods folder to link into."),
            )
            return False
        library_dir = window.where.library_dir()
        arriving, refused = [], []
        with busy_cursor():
            for archive in archives:
                try:
                    arriving.extend(library.arriving(archive, library_dir))
                except modzip.InstallError as err:
                    refused.append(tr("%(zip)s could not be installed: %(why)s",
                                      zip=archive.name, why=err))
        if refused:
            window.prompts.report_dropped(tr("Not installed"), "\n".join(refused))
        if not arriving:
            return False
        # A mod for the other game is asked about before anything is
        # unpacked. It used to be said only by the import that follows the
        # install, by when the mod was in this game's library and its mods
        # folder, whatever was answered there.
        mismatched = [mod for mod in arriving if mod.game and mod.game != window.loadout.game]
        if mismatched:
            # By the zip: a zip is installed whole, so leaving a mod out
            # leaves out a mod for this game beside it in its zip. Counted by
            # the mod, a zip holding one of each was offered Leave it out,
            # which then installed nothing and said nothing.
            left_out = {mod.archive for mod in mismatched}
            others = [mod for mod in arriving if mod.archive not in left_out]
            answer = window.prompts.ask_dropped_other_game(
                mismatched, window.loadout.game, len(others),
            )
            if not answer:
                return False
            if answer == OTHER_GAME_SKIP:
                arriving = others
        loaded = bool(window.loadout.layers or window.loadout.authored())
        # With no loadout and no way of working chosen there is nothing to
        # add to. A mod read in then left the Start tab saying "None yet"
        # over a window full of its changes.
        working = bool(loaded or window.loadout.aim or window.loadout_path is not None)
        action = window.prompts.ask_dropped_zips(
            arriving, window.loadout.game, folder, loaded, working,
        )
        if not action:
            return False
        # A loadout of its own starts clean, and what is open is asked about
        # before anything is unpacked: declining then leaves nothing behind.
        # Whatever is open, not only one holding something: a loadout with
        # a file and a name and nothing in it yet took the mod as its own.
        if action == ZIP_OPEN and working and not window.new_loadout():
            return False
        done = self._unpack(list(dict.fromkeys(mod.archive for mod in arriving)), library_dir, folder)
        placed, aside, failed = [], None, []
        for archive, result in done:
            if isinstance(result, str):
                failed.append(tr("%(zip)s could not be installed: %(why)s",
                                 zip=archive.name, why=result))
                continue
            placed.extend(linked.link for linked in result.linked)
            aside = aside or result.set_aside
        if failed:
            window.prompts.report_dropped(tr("Not installed"), "\n".join(failed))
        if not placed:
            return False
        note = tr("Installed %(mods)s into the library, linked in %(folder)s.",
                  mods=join_list(link.name for link in placed), folder=placed[0].parent)
        if aside is not None:
            note = tr("%(said)s What was there before is at %(aside)s.", said=note, aside=aside)
        window.status_label.say(note, library_dir)
        if action == ZIP_INSTALL:
            # In the mods folder now, which the Play card reads.
            window.refresh_truth()
            return False
        if action != ZIP_OPEN:
            # Every one of them, each imported in turn when no folder is
            # read, as the question promised: mod.io reads only the first,
            # one mod having been asked for there (A11-84).
            return window.read_installed(placed)
        # Yours when it carries one of your author names, and then it is
        # carried on with directly; anybody else's gets a patch in front of it.
        if window.preferences.is_your_name(arriving[0].creator):
            window.start.choose_aim(AIM_MAKE, existing=True, start_dir=str(placed[0]))
        else:
            window.start.choose_aim(AIM_TWEAK, start_dir=str(placed[0]))
        window.refresh_truth()
        return bool(window.loadout.layers)

    def _unpack(self, archives, library_dir, folder) -> list:
        """Each of ``archives`` installed, off the window's thread, beside
        what became of it: see :class:`ui.workers.ZipInstallWorker`."""
        progress = QProgressDialog(tr("Unpacking…"), "", 0, 0, self.window)
        progress.setCancelButton(None)
        progress.setWindowTitle(tr("Installing"))
        progress.setWindowModality(Qt.WindowModality.WindowModal)
        progress.setMinimumDuration(0)
        worker = ZipInstallWorker(archives, library_dir, folder, self.window)
        done: list = []
        waiting = QEventLoop()
        worker.progress.connect(
            lambda _position, _total, name: progress.setLabelText(tr("Unpacking %s…", name))
        )
        worker.succeeded.connect(done.extend)
        worker.finished.connect(waiting.quit)
        progress.show()
        worker.start()
        try:
            waiting.exec()
            worker.wait()
        finally:
            progress.close()
            progress.deleteLater()
            worker.deleteLater()
        return done

    def mentioned_keys(self, excluding=None) -> set:
        """Every mod any loadout for this game names -- by ModID and by
        folder -- this window's own as it stands, saved or not, and the
        loadout files as the cache has them (:mod:`core.filecache`),
        ``excluding`` one of the files."""
        keys = set()
        for layer in self.window.loadout.layers:
            keys.update(modfolder.match_keys(layer.mod_id, layer.origin))
        return keys | filecache.mentioned_keys(
            self.window.loadout.game, self.window.files.loadouts_dir(), excluding,
        )
