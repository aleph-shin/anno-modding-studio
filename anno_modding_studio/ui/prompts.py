"""Every question the main window asks you, on one object tests replace.

The window used to ask its questions through methods of its own --
``ask_*``, ``confirm_*``, ``report_*``, ``pick_*`` -- so that a test could
answer one by patching the window.  That made the window the only thing that
could ask, and every test of something the window does reach into it.  Here a
question is asked of ``window.prompts``: the window hands each one what it
needs to say, gets the answer back, and acts on it.  Nothing here changes
anything but the screen.

A test answers with ``mock.patch.object(window.prompts, "ask_...")``; a piece
of the window moved out of it (Stage 3.3) is handed the same object.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional, Tuple

from PySide6.QtWidgets import (
    QDialog,
    QFileDialog,
    QInputDialog,
    QMessageBox,
)

from ..core import disk, edits, games, places, play, switches
from ..core.changes import OWN, is_own_mod
from ..core.i18n import bullets, date_text, join_list, tr, tr_kw, trn, trn_kw
from . import widgets
from .widgets import ask, busy_cursor, choose, named, show_box_text, tip, wrappable_path

# What a new loadout starts from, besides what the game loads now: nothing, or
# a copy of the live loadout.
START_NOTHING, START_COPY = "nothing", "copy"
# A collection on top of what you play, as a new profile of the live loadout
# rather than a copy of it -- see docs/proposals/profiles-proposal.md.
START_PROFILE = "profile"
# What a fork makes: a profile of this loadout, or a loadout of its own.
FORK_PROFILE, FORK_SEPARATE = "profile", "separate"
# What becomes of a mod zip dropped on the window, once it is installed: read
# into the loadout open now, or opened as a loadout of its own.
ZIP_ADD, ZIP_OPEN, ZIP_INSTALL = "add", "open", "install"
# What to do with dropped mods that are for another game than the one loaded.
OTHER_GAME_ANYWAY, OTHER_GAME_SKIP = "anyway", "skip"


def _lost(lost: int, many: bool) -> str:
    """That ``lost`` changes of yours in a mod, or ``many`` mods, would go."""
    if many:
        return trn(
            "%(n)s change you made to these mods would be lost. It "
            "exists nowhere else and cannot be read back.",
            "%(n)s changes you made to these mods would be lost. They "
            "exist nowhere else and cannot be read back.",
            lost,
        )
    return trn(
        "%(n)s change you made to it would be lost. It exists "
        "nowhere else and cannot be read back.",
        "%(n)s changes you made to it would be lost. They exist "
        "nowhere else and cannot be read back.",
        lost,
    )


class Prompts:
    """The questions, asked over ``parent``: the window."""

    def __init__(self, parent) -> None:
        self.parent = parent

    def pick_collection_to_take_out(self, names) -> str:
        """Which collection to take out, or ""."""
        chosen, ok = QInputDialog.getItem(
            self.parent, tr("Take a collection out"),
            tr("The mods the collection brought leave this loadout and every "
               "profile of it. The ones it lists that you had before it stay:"),
            list(names), 0, False,
        )
        return chosen if ok else ""

    def ask_take_out(self, collection: str, names, yours, edits: int) -> bool:
        """Whether to take ``collection`` out, having listed what goes and what
        stays."""
        lines = [trn("These %(n)s mod came with %(collection)s and leave this "
                     "loadout, and every profile of it:",
                     "These %(n)s mods came with %(collection)s and leave this "
                     "loadout, and every profile of it:",
                     len(names), collection=named(collection)), ""]
        lines.append(bullets(names, 15))
        if yours:
            lines += ["", trn("%(n)s it lists that you had before it stays, as yours.",
                              "%(n)s it lists that you had before it stay, as yours.",
                              len(yours))]
        if edits:
            lines += ["", trn("%(n)s change you made to them goes with them.",
                              "%(n)s changes you made to them go with them.", edits)]
        answer = ask(
            self.parent,
            tr("Take the collection out"),
            "\n".join(lines),
            tr("&Take it out"),
            default_action=False,
        )
        return answer

    def ask_delete_copies(self, collection: str, names) -> bool:
        """Whether to delete the library's copies of mods taken out that no
        other loadout uses."""
        lines = [tr("No other loadout uses these, which %s brought:", named(collection)), ""]
        lines.append(bullets(names, 15))
        lines += ["", tr("Delete the library's copies of them?")]
        answer = ask(
            self.parent,
            tr("Delete their library copies"),
            "\n".join(lines),
            tr("&Delete copies"),
            cancel=tr("&Keep them"),
            default_action=False,
            action_tip=tr("They can be installed from mod.io again."),
            cancel_tip=tr("Kept, they are switched off by the next Play."),
        )
        return answer

    def ask_switch_yours_too(self, collection: str, names) -> bool:
        """Whether to switch off, with a collection's mods, the ones it lists
        that were installed before it."""
        answer = ask(
            self.parent,
            tr("Your mods the collection also lists"),
            trn("%(collection)s also lists %(n)s mod you had installed before "
                "it:\n\n%(names)s\n\n"
                "Switch it off too?",
                "%(collection)s also lists %(n)s mods you had installed before "
                "it:\n\n%(names)s\n\n"
                "Switch those off too?",
                len(names), collection=named(collection), names=bullets(names, 12)),
            tr("&Switch those off too"),
            cancel=tr("&Leave them on"),
            default_action=False,
            cancel_tip=tr("Only the mods the collection brought are switched off."),
        )
        return answer

    def pick_collection(self, names) -> str:
        """Which collection's mods to switch off, or "".
        """
        chosen, ok = QInputDialog.getItem(
            self.parent, tr("Switch off a collection's mods"),
            tr("Every mod the collection brought is switched off in this "
               "loadout, and in your mods folder at the next Play:"), list(names), 0, False,
        )
        return chosen if ok else ""

    def confirm_live(self, plan: play.Plan, launch: bool, loadout, name: str) -> bool:
        """Whether to go ahead, having said everything it will do."""
        lines = [tr("In:\n%s\n", wrappable_path(plan.folder))]
        if plan.built:
            lines.append(tr("Build into the Studio's library, linked in:\n%s",
                            bullets(plan.built_names, 12)))
        on = sorted(name for name, want in plan.moving.items() if want)
        off = sorted(name for name, want in plan.moving.items() if not want)
        if on:
            lines.append(tr("Switch on:\n%s", bullets(on, 12)))
        if off:
            lines.append(tr("Switch off:\n%s", bullets(off, 12)))
        if plan.strays:
            names = sorted(mod.name for mod in plan.strays)
            listed = bullets(names, 12)
            lines.append(tr("Switch off, because this loadout does not have them on:\n%s", listed))
        if plan.found is not None and plan.found.describe_cache():
            lines.append(plan.found.describe_cache())
        if plan.own:
            lines.append(tr_kw(
                "Written into the Studio's profile, %s, and made the [[live]] one. "
                "Your own profiles are not touched.", plan.own_name
            ))
        if plan.options:
            lines.append(tr(
                "Mod options written into %(file)s for: %(mods)s. Entries for mods "
                "this loadout does not mention stay as they are.",
                file=switches.OPTIONS_FILE, mods=", ".join(sorted(plan.options)[:8]),
            ))
        if plan.tweaks:
            lines.append(tr_kw(
                "iModYourAnno settings written into [[generated copy|generated copies]] "
                "of: %s.",
                ", ".join(layer.label for layer in plan.tweaks[:8]),
            ))
        if plan.edited:
            lines.append(tr_kw(
                "Your edits to other people's mods written into [[generated copy|generated "
                "copies]] of: %s.",
                ", ".join(layer.label for layer in plan.edited[:8]),
            ))
        if plan.reverting:
            lines.append(tr(
                "Loading the library's own copy again, with no settings or edits of "
                "yours left to write into it: %s.",
                ", ".join(plan.reverting[:8]),
            ))
        if plan.tweaks_held:
            lines.append(tr(
                "Set differently here, but in a folder of your own and not "
                "the library's, so left as the folder has them: %s.",
                ", ".join(plan.tweaks_held[:8]),
            ))
        told, details = plan.told()
        if told:
            listed = told + "".join("\n" + note for note in plan.notes if note)
            lines.append(tr("Worth knowing first:\n%s", listed))
        agreed = [verdict for verdict in plan.loader if verdict.state == "agrees"]
        if agreed:
            lines.append("\n".join(verdict.describe() for verdict in agreed))
        lines.append(tr(
            "No mod is deleted or rewritten. This moves the switches the "
            "game reads, and writes the Studio's own builds."
        ))
        if launch:
            lines.append(tr("Then %s starts.", games.get(loadout.game).label))
        named = name
        if launch:
            title = tr("Play %s?", named) if named else tr("Play this loadout?")
        else:
            title = tr("Make %s live?", named) if named else tr("Make this loadout live?")
        # Translators: "Play" is the Studio's button that builds your mods and
        # starts the game; use the same word as that button.
        action = tr("&Play") if launch else tr("&Make it live")
        return self._ask_to_go(title, "\n\n".join(lines), details, action)

    def _ask_to_go(self, title: str, text: str, details: str = "", action: str = "") -> bool:
        """The action or Cancel, with the whole list of what is worth knowing
        behind Show Details when the question only has room for a count of it."""
        if not details:
            return ask(self.parent, title, text, action)
        box = QMessageBox(QMessageBox.Icon.Question, title, "",
                          QMessageBox.StandardButton.Cancel, self.parent)
        show_box_text(box, text)
        go = box.addButton(action, QMessageBox.ButtonRole.AcceptRole)
        box.setDefaultButton(go)
        box.setDetailedText(details)
        return self.answer(box) is go

    def answer(self, box):
        """The button a question box was answered with -- a method, so a test
        can answer it."""
        return widgets.answer_of(box)

    def game_running(self, game: str, why: str) -> bool:
        """Whether ``game`` is running, having said so and ``why`` that stops
        what was asked for -- the guard before anything that moves or
        switches the mods it read when it started.  A test answers it like
        any question, rather than by what is running on the machine."""
        from ..core import launch

        if not launch.running(game):
            return False
        QMessageBox.information(self.parent, tr("%s is running", games.get(game).label), why)
        return True

    def ask_game_start(self, label: str, tried: str = "") -> str:
        """Play could not start the game: offer to choose how, or ""."""
        box = QMessageBox(self.parent)
        box.setWindowTitle(tr("Start %s yourself", label))
        show_box_text(
            box,
            tr("Its mods are set, but %s could not be started.\n"
               "\n"
               "Start it the way you usually do, or choose what Play starts "
               "from now on.",
               wrappable_path(tried))
            if tried else
            tr("Its mods are set, but no Steam or Ubisoft Connect install "
               "was found to start it through.\n"
               "\n"
               "Start it the way you usually do, or choose what Play starts "
               "from now on.")
        )
        pick = box.addButton(tr("Choose…"), QMessageBox.ButtonRole.AcceptRole)
        tip(pick, tr("The game's program, a shortcut or a launcher. Settings ▸ "
                     "Starting the game changes it later."))
        box.addButton(QMessageBox.StandardButton.Close)
        box.exec()
        if box.clickedButton() is not pick:
            return ""
        chosen, _f = QFileDialog.getOpenFileName(
            self.parent, tr("What should Play start?"), "",
            tr("Programs and shortcuts (*.exe *.lnk *.url *.bat *.cmd);;All files (*)"),
        )
        return chosen

    def confirm_put_back(self, held) -> bool:
        """Whether to turn the library's links into folders again, having
        listed them."""
        names = [entry.name for entry in held]
        lines = [
            trn("%(n)s mod in your mods folder is the library's:",
                "%(n)s mods in your mods folder are the library's:", len(names)),
            "",
        ]
        lines.append(bullets(names, 15))
        lines += [
            "",
            tr("Each becomes an ordinary folder there, moved out of the "
               "library. A link to a folder of your own is removed. "
               "Afterwards the game loads the same mods as now, and none of "
               "them depends on the Studio."),
        ]
        box = QMessageBox(self.parent)
        box.setWindowTitle(tr("Put the library's mods back?"))
        box.setText("\n".join(lines))
        yes = box.addButton(tr("Put them back"), QMessageBox.ButtonRole.AcceptRole)
        box.addButton(QMessageBox.StandardButton.Cancel)
        box.setDefaultButton(yes)
        box.exec()
        return box.clickedButton() is yes

    def ask_profile_name(self, title: str, prompt: str, default: str) -> str:
        """A profile's name, or ""."""
        name, ok = QInputDialog.getText(self.parent, title, prompt, text=default)
        return name.strip() if ok else ""

    def ask_delete_profile(self, name: str) -> bool:
        """Whether to delete profile ``name``."""
        answer = ask(
            self.parent,
            tr("Delete profile"),
            tr("Delete the profile %s?\n\nOnly which mods it has on goes: the "
               "mods, their edits and their options stay in the loadout, for "
               "the other profiles.", named(name)),
            tr("&Delete profile"),
            default_action=False,
        )
        return answer

    def ask_convert_now(self, count: int, game: str) -> bool:
        """Offer the conversion."""
        if games.get(game).in_game_browser:
            said = trn(
                "%(n)s of the mods the game loads are folders of your own or "
                "the game's mod browser's. Once they are in the Studio's "
                "library, Play loads what the loadout shows.\n"
                "\n"
                "Look at the list now?",
                "%(n)s of the mods the game loads are folders of your own or "
                "the game's mod browser's. Once they are in the Studio's "
                "library, Play loads what the loadout shows.\n"
                "\n"
                "Look at the list now?", count,
            )
        else:
            said = trn(
                "%(n)s of the mods the game loads are folders of your own. "
                "Once they are in the Studio's library, Play loads what the "
                "loadout shows.\n"
                "\n"
                "Look at the list now?",
                "%(n)s of the mods the game loads are folders of your own. "
                "Once they are in the Studio's library, Play loads what the "
                "loadout shows.\n"
                "\n"
                "Look at the list now?", count,
            )
        answer = ask(
            self.parent,
            tr("Convert your mods folder to the library?"),
            said,
            tr("&Look at the list"),
            cancel=tr("&Not now"),
            action_tip=tr("No mod, switch or setting is lost, and no folder moves "
                          "until you convert."),
            cancel_tip=tr("The fourth card on the Start tab and Game \u25b8 Library "
                          "offer it any time."),
        )
        return answer

    def ask_start_from_what_i_play(self) -> bool:
        """The conversion starts from a loadout of what the game loads now."""
        answer = ask(
            self.parent,
            tr("Start from what you play now?"),
            tr("Converting your mods folder starts from a loadout of what the "
               "game loads now (every mod, on or off, as it is), so there is "
               "something to check the result against.\n"
               "\n"
               "Read it in first?"),
            tr("&Read it in"),
        )
        return answer

    def ask_bring_in_first(self, found) -> bool:
        """This loadout is not what the game loads now: read the folder in
        first?"""
        answer = ask(
            self.parent,
            tr("Read what the game loads in first?"),
            tr("This loadout is not what the game loads now: %s\n"
               "\n"
               "The conversion only moves where your mods live, so it starts "
               "from a loadout that matches what the game loads. Read the "
               "mods folder in first?", found.describe()),
            tr("&Read it in"),
        )
        return answer

    def ask_convert(self, surveyed, managers, found_profiles, only=None, *, library_dir, library_used: bool):
        """The survey, answered: ``(rows, profiles, keep a full copy)``, or
        None."""
        from ..core import convert
        from .convert_dialog import ConvertDialog

        folders = convert.folders_to_copy(surveyed.to_do)
        with busy_cursor():
            full_size = convert.size_of(folders) if folders else 0
            backups = places.backups_dir()
            full_free = disk.free_space(backups if backups.exists() else backups.parent) or 0
        dialog = ConvertDialog(
            surveyed, self.parent, managers=managers, found_profiles=found_profiles,
            space=convert.space_needed(surveyed.to_do, library_dir),
            free=disk.free_space(library_dir) or 0,
            library_dir=library_dir, only=only,
            full_size=full_size, full_free=full_free, full_folders=folders,
            full_default=not library_used,
        )
        accepted = dialog.exec() == int(QDialog.DialogCode.Accepted)
        answer = (dialog.chosen_rows(), dialog.chosen_profiles(), dialog.keep_full_copy()) if accepted else None
        dialog.deleteLater()
        return answer

    def ask_undo_conversion(self, record, count, made, profiles=()) -> bool:
        """Confirm taking a conversion back."""
        when = date_text(record.get("when") or 0, clock=True)
        # One line for each thing it will do, rather than one sentence
        # assembled from however many of them there are.
        bits = []
        if count["move"]:
            bits.append(trn(
                "move %(n)s folder out of the library, back where it was",
                "move %(n)s folders out of the library, back where they were",
                count["move"]))
        if count["copy"]:
            bits.append(trn(
                "remove the library's %(n)s copy of the game's mod browser's mods",
                "remove the library's %(n)s copies of the game's mod browser's mods",
                count["copy"]))
        if count["aside"]:
            bits.append(trn("put %(n)s set-aside duplicate back",
                            "put %(n)s set-aside duplicates back", count["aside"]))
        if made:
            bits.append(trn("set aside %(n)s loadout made from your profiles",
                            "set aside %(n)s loadouts made from your profiles",
                            len(made)))
        if profiles:
            bits.append(trn("delete the %(n)s profile made from yours (%(names)s)",
                            "delete the %(n)s profiles made from yours (%(names)s)",
                            len(profiles), names=", ".join(profiles)))
        bits.append(tr_kw("put back the files making it [[live]] rewrote"))
        answer = ask(
            self.parent,
            tr("Undo the conversion?"),
            tr("The conversion of %(when)s will be taken back. It will:\n"
               "%(steps)s\n\nYour mods folders will be as they were before "
               "it.\n\nMods installed into the library since stay where they "
               "are.", when=when, steps="\n".join("  • %s" % bit for bit in bits)),
            tr("&Undo the conversion"),
            default_action=False,
        )
        return answer

    def report_undo(self, text: str) -> None:
        """What undoing did."""
        widgets.tell(self.parent, tr("The conversion is undone"), text)

    def report_conversion(self, text: str) -> None:
        """What converting did, with the names in it set apart (:func:`widgets.named`)."""
        widgets.tell(self.parent, tr("Converted to the library"), text)

    def pick_mod_zips(self) -> list:
        """Mod zips to install, from anywhere on this PC, or []."""
        chosen, _f = QFileDialog.getOpenFileNames(
            self.parent, tr("Mod zips to install"), str(Path.home() / "Downloads"),
            tr("Mod zips (*.zip)"),
        )
        return chosen

    def pick_own_folder(self) -> str:
        """The folder to manage in place, or ""."""
        return QFileDialog.getExistingDirectory(
            self.parent, tr("A mod folder of your own to manage where it is"), str(Path.home()),
        )

    def ask_missing_mods(self, wanted, absent) -> bool:
        """Whether to go and see about the mods that are not here."""
        # Three counts, each a clause of its own, put in whole.
        lines = [
            # Translators: e.g. "This loadout uses 3 mods. 1 is installed; 2
            # are not:" -- each part is translated on its own below.
            tr("This loadout uses %(total)s. %(here)s, %(absent)s:",
               total=trn("%(n)s mod", "%(n)s mods", len(wanted)),
               here=trn("%(n)s is installed", "%(n)s are installed",
                        len(wanted) - len(absent)),
               absent=trn("%(n)s is not", "%(n)s are not", len(absent))),
            "",
        ]
        lines.append(bullets([one.name for one in absent], 12))
        lines += [
            "",
            tr("The loadout is open either way: every change those mods make "
               "is in it. They are only missing from the game."),
        ]
        box = QMessageBox(self.parent)
        box.setWindowTitle(tr("Mods this loadout uses are not installed"))
        box.setText("\n".join(lines))
        look = box.addButton(tr("See what installing them takes…"),
                             QMessageBox.ButtonRole.AcceptRole)
        box.addButton(tr("Open it as it is"), QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(look)
        box.exec()
        return box.clickedButton() is look

    def ask_fork_kind(self, was: str, profile: str) -> str:
        """What a fork of ``was`` is: FORK_PROFILE, FORK_SEPARATE or ""."""
        # Only a loadout with a file of its own forks, so it always has a name.
        picked = choose(
            self.parent,
            tr("Fork this loadout"),
            tr("Try a different set of %s's mods as a new profile of it, or as a "
               "separate loadout?", named(was)),
            [
                (tr("A new &profile"),
                 tr("The same mods, switched as %s is now. An update reaches every "
                    "profile.", profile)),
                (tr("A &separate loadout"),
                 tr("A copy that owns its mods, for a list that will grow apart.")),
            ],
        )
        return (FORK_PROFILE, FORK_SEPARATE)[picked] if picked >= 0 else ""

    def ask_fork_name(self, was: str) -> str:
        """What to call a fork of ``was``, or ""."""
        name, ok = QInputDialog.getText(
            self.parent,
            tr("Fork this loadout"),
            tr("A copy to try something in. %s stays as it is, and stays\n"
               "what the game loads until you make the copy live.\n"
               "\n"
               "Call the copy:", was),
            text="%s (fork)" % was,  # i18n: keep - becomes the file's name
        )
        return name if ok else ""

    def ask_start_from(self, live_name: str) -> str:
        """What a new loadout starts from: START_NOTHING, START_PROFILE,
        START_COPY, or "" for none of them."""
        picked = choose(
            self.parent,
            tr("Start from"),
            tr("Try the collection on its own, or on top of %s, the loadout the "
               "game loads now? What the game loads changes only when you Play.",
               named(live_name)),
            [
                (tr("On its &own"), tr("A new loadout of the collection alone.")),
                (tr("As a new &profile"),
                 tr("Its mods join that loadout, on in a new profile and off in "
                    "the others.")),
                (tr("As a separate &copy"),
                 tr("A new loadout copied from that one, kept apart from it.")),
            ],
            default=1,
        )
        return (START_NOTHING, START_PROFILE, START_COPY)[picked] if picked >= 0 else ""

    def ask_dropped_zips(self, arriving, game: str, folder: str, loaded: bool,
                         working: bool = True) -> str:
        """What to do with the mods dropped on the window, having listed them
        and said where they go: ZIP_ADD, ZIP_OPEN, ZIP_INSTALL or "".

        ``arriving`` is :func:`core.library.arriving`'s, of every zip dropped.
        Opening as a loadout is offered for one mod alone; ``loaded`` is that
        the loadout open now holds something it would clear.  ``working`` is
        that there is a loadout to add to: with none, and no way of working
        chosen, one mod can only be opened as a loadout of its own, and
        several only installed (A10-15). Read into a window with no way of
        working, they left the Start tab saying "None yet" over their changes.
        """
        names = []
        for mod in arriving:
            name = "%s %s" % (mod.label, mod.version) if mod.version else mod.label
            if mod.replaces is not None and mod.replaces.version:
                name = tr("%(mod)s (replaces version %(old)s)", mod=name, old=mod.replaces.version)
            elif mod.replaces is not None:
                name = tr("%s (replaces the copy in the library)", name)
            names.append(name)
        count = len(arriving)
        replaced = sum(1 for mod in arriving if mod.replaces is not None)
        lines = [
            trn("You dropped %(n)s mod for %(game)s:", "You dropped %(n)s mods for %(game)s:",
                count, game=games.get(game).label),
            bullets(names, 12),
            trn("It is unpacked into the library and linked in:\n%(folder)s",
                "They are unpacked into the library and linked in:\n%(folder)s",
                count, folder=wrappable_path(folder)),
        ]
        if replaced:
            lines.append(trn(
                "One copy in the library is replaced, and set aside in the backups.",
                "%(n)s copies in the library are replaced, and set aside in the backups.",
                replaced,
            ))
        adding = working
        installing = count > 1 and not working
        if installing:
            # Why there is no Add: there is nothing to add them to.
            lines.append(tr("To read them in, choose a way of working on the Start tab first."))
        box = QMessageBox(self.parent)
        box.setIcon(QMessageBox.Icon.Question)
        box.setWindowTitle(trn("Install the dropped mod?", "Install the dropped mods?", count))
        box.setText("\n\n".join(lines))
        add = (box.addButton(tr("&Add to loadout"), QMessageBox.ButtonRole.AcceptRole)
               if adding else None)
        if add is not None:
            tip(add, trn("Reads it into the loadout open now.",
                         "Reads them into the loadout open now.", count))
        alone = (box.addButton(tr("&Open as loadout"), QMessageBox.ButtonRole.AcceptRole)
                 if count == 1 else None)
        if alone is not None:
            tip(alone, tr("Starts a new loadout for this mod, and clears what is loaded now.")
                if adding and loaded else tr("Starts a new loadout for this mod."))
        install = (box.addButton(tr("&Install only"), QMessageBox.ButtonRole.AcceptRole)
                   if installing else None)
        if install is not None:
            tip(install, tr("Puts them in the library and the mods folder, and reads "
                            "none of them in."))
        stay = box.addButton(QMessageBox.StandardButton.Cancel)
        box.setDefaultButton(add or alone or install)
        box.setEscapeButton(stay)
        clicked = self.answer(box)
        if add is not None and clicked is add:
            return ZIP_ADD
        if install is not None and clicked is install:
            return ZIP_INSTALL
        return ZIP_OPEN if alone is not None and clicked is alone else ""

    def ask_dropped_other_game(self, mismatched, game: str, others: int) -> str:
        """What to do with dropped mods whose own layout says they are for
        another game than the one loaded: OTHER_GAME_ANYWAY, OTHER_GAME_SKIP
        or "" to install nothing.

        Asked before anything is unpacked: the library a dropped mod goes
        into is the loaded game's, and a mod for the other game put there is
        one the game then loads.  ``mismatched`` is the
        :class:`core.library.Arriving` of each; ``others`` is how many mods
        were dropped with them that are not, which leaving these out still
        installs.
        """
        count = len(mismatched)
        names = [
            tr("%(mod)s (%(game)s)", mod=mod.label, game=games.get(mod.game).label)
            for mod in mismatched
        ]
        lines = [
            trn("%(n)s mod you dropped has the folder layout of another game, and "
                "%(game)s is loaded:",
                "%(n)s mods you dropped have the folder layout of another game, and "
                "%(game)s is loaded:",
                count, game=games.get(game).label),
            bullets(names, 12),
            trn("Installed, it goes into this game's library, and this game loads "
                "it where its paths do not match. To install it for its own "
                "game, load that game's files on the Start tab and drop the zip "
                "again.",
                "Installed, they go into this game's library, and this game loads "
                "them where their paths do not match. To install them for their "
                "own game, load that game's files on the Start tab and drop the "
                "zips again.",
                count),
        ]
        box = QMessageBox(self.parent)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle(trn("A mod for another game", "Mods for another game", count))
        box.setText("\n\n".join(lines))
        skip = (
            box.addButton(trn("&Leave it out", "&Leave them out", count),
                          QMessageBox.ButtonRole.AcceptRole)
            if others else None
        )
        if skip is not None:
            tip(skip, tr("Installs the rest of what you dropped."))
        anyway = box.addButton(tr("&Install anyway"), QMessageBox.ButtonRole.DestructiveRole)
        stay = box.addButton(QMessageBox.StandardButton.Cancel)
        box.setDefaultButton(skip or stay)
        box.setEscapeButton(stay)
        clicked = self.answer(box)
        if skip is not None and clicked is skip:
            return OTHER_GAME_SKIP
        return OTHER_GAME_ANYWAY if clicked is anyway else ""

    def report_dropped(self, title: str, text: str) -> None:
        """Why something dropped on the window was not installed."""
        QMessageBox.information(self.parent, title, text)

    def ask_read_folder(self, count: int, name: str) -> bool:
        """Whether to read the mods folder in after a collection landed in it."""
        box = QMessageBox(self.parent)
        box.setWindowTitle(tr("Read your mods in?"))
        show_box_text(box, trn(
            "%(n)s mod of %(collection)s is in your mods folder now. Read the "
            "folder in?",
            "%(n)s mods of %(collection)s are in your mods folder now. Read the "
            "folder in?",
            count, collection=named(name),
        ))
        read = box.addButton(tr("Read my mods in…"), QMessageBox.ButtonRole.AcceptRole)
        tip(read, tr("Shows them as the game loads them, and where two set the same field."))
        box.addButton(tr("Not now"), QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(read)
        box.exec()
        return box.clickedButton() is read

    def ask_use_mods_folder(self, folder: str, instead: str) -> bool:
        """Whether the loadout just opened may use ``folder``, the mods
        folder it names, which this PC has not used: else it uses
        ``instead``, or none where that is ""."""
        text = tr(
            "This loadout names %s as its mods folder. The Studio hasn't used that "
            "folder on this PC, and Play switches the mods in a loadout's mods folder "
            "and builds into it.", wrappable_path(folder),
        )
        text += "\n\n" + (
            tr("If you don't use it, the loadout uses %s.", wrappable_path(instead)) if instead
            else tr("If you don't use it, the loadout has no mods folder until you read one in.")
        )
        return ask(
            self.parent, tr("Use this mods folder?"), text, tr("&Use this folder"),
            cancel=tr("&Don't use it"), default_action=False,
        )

    def confirm_unload(self, origins, loadout, always: bool = False) -> bool:
        """Whether it is OK to unload the mods named in ``origins``.

        ``origins`` never includes ``""`` -- the mod being authored here is not
        one of the loaded mods, it is the loadout itself, and unloading
        somebody else's mods must never touch it.  Declining leaves everything
        exactly as it was, and whatever asked for this does not happen either.

        Silent when there is nothing to lose: ticking mods in the unload dialog
        and clicking its own button is already the deliberate step, and a mod
        with nothing authored into it costs nothing to unload -- it is exactly
        what re-reading the folder would produce again.  It is the changes that
        exist nowhere else this asks about.  ``always`` asks all the same, for
        an unload that is one click in a menu.
        """
        origins = [origin for origin in origins if not is_own_mod(origin)]
        if not origins:
            return True
        # Every edit inside it the loadout's own mod keeps goes with it: a
        # change typed, and an op switched off or taken out, which went
        # unsaid (R3-26, hands-on run 3).  A patch's are set aside for it.
        lost = sum(edits.count(loadout, origin, OWN) for origin in origins)
        # A mod changed through its patch: the changes are not lost, and not
        # used either while the mod is gone. Unasked, that went unsaid (H2-4),
        # and so did what the patch keeps inside it (P5).
        patches = [(origin, loadout.patch_for(origin)) for origin in origins]
        kept = sum(
            len(loadout.changes_of(patch.origin)) + edits.count(loadout, origin, patch.origin)
            for origin, patch in patches
            if patch is not None and patch.origin not in origins
        )
        if not lost and not kept and not always:
            return True
        lines = []
        if not lost and not kept:
            # Translators: Edit ▸ Undo is the menu entry.
            lines.append(
                tr("It leaves the loadout with what it changes. Edit ▸ Undo puts it back.")
                if len(origins) == 1
                else tr("They leave the loadout with what they change. Edit ▸ Undo puts "
                        "them back.")
            )
        if lost:
            lines.append(_lost(lost, len(origins) > 1))
        if kept and len(origins) > 1:
            lines.append(trn_kw(
                "Your [[patch|patches]] for these mods stay, with the %(n)s change "
                "in them. Until the mods are loaded again, they are greyed and "
                "left out of exports and Play.",
                "Your [[patch|patches]] for these mods stay, with the %(n)s changes "
                "in them. Until the mods are loaded again, they are greyed and "
                "left out of exports and Play.",
                kept,
            ))
        elif kept:
            lines.append(trn_kw(
                "Your [[patch]] for it stays, with the %(n)s change in it. Until "
                "the mod is loaded again, it is greyed and left out of exports "
                "and Play.",
                "Your [[patch]] for it stays, with the %(n)s changes in it. Until "
                "the mod is loaded again, it is greyed and left out of exports "
                "and Play.",
                kept,
            ))
        lines.append(trn("Unload %(n)s mod?", "Unload %(n)s mods?", len(origins)))
        return ask(
            self.parent,
            tr("Unload mods?"),
            "\n\n".join(lines),
            tr("&Unload"),
            default_action=False,
        )

    def confirm_read_again(self, layer, loadout) -> bool:
        """Whether to read ``layer`` again from its folder, at other settings.

        Edits of yours folded into what it brought cannot survive that, so it
        is asked when there are any.  It used to be the unload question with
        this put in front, which asked to unload a mod that stays loaded and
        said its patch would be greyed while it was in use.
        """
        lost = len(loadout.authored_of(layer.origin))
        if not lost:
            return True
        lines = [
            tr("Reading %s at other settings means reading its folder again, "
               "which replaces everything it brought.", named(layer.label)),
            _lost(lost, False),
            tr("Read it again?"),
        ]
        return ask(
            self.parent,
            tr("Read it again?"),
            "\n\n".join(lines),
            tr("&Read again"),
            default_action=False,
        )

    def confirm_base_swap(self, loaded, wanted, loadout) -> bool:
        """Whether to open a loadout built against a different base file.

        The case this exists for: 117 files loaded, a 117 loadout saved, then
        1800 files loaded instead, then that loadout reopened from the recents
        list.  The offer to load a loadout's own base file used to be made only
        when *nothing* was indexed, so with the other game's file already open it
        was skipped -- and the loadout came up saying Anno 117 while the index,
        the localisation and every GUID behind it were Anno 1800's.  Nothing said
        so, because the one place the game is written down is a file path that is
        usually elided.

        There is no version of carrying on that is correct, so this is Yes or
        Cancel rather than a suggestion.

        When its own file is no longer where it was saved, Yes means something
        else.  Of the same game, it is opening the loadout against the file
        open now (``load_loadout`` then takes that file up as its base): its
        changes name GUIDs and paths of that game, which is what is open, so
        nothing is mismatched -- refusing it left a loadout you could open
        with nothing indexed but not with its own game indexed (A11-103).  Of
        the other game, there is still no file it would recognise to open it
        against, so that is only said.
        """
        theirs = games.get(loaded.game or games.detect_from_path(str(wanted)))
        mine = games.get(loadout.game)
        same_game = theirs.key == mine.key
        if not wanted.is_file() and same_game:
            return ask(
                self.parent,
                tr("Open it against the file you have open?"),
                tr("This loadout was built against:\n"
                   "%(wanted)s\n"
                   "\n"
                   "That file is not where it was. You have %(mine)s open:\n"
                   "%(have)s\n"
                   "\n"
                   "Both are the same game. Opened against the file you have "
                   "open, it is saved with that file from then on.",
                   wanted=wrappable_path(wanted), mine=mine.label,
                   have=wrappable_path(loadout.source_path)),
                tr("&Open against it"),
                default_action=False,
            )
        if not wanted.is_file():
            # Only the file back at its saved path opens it: the loadout is
            # matched to what is open by that path, so loading the file from
            # anywhere else is no way back (A11-102).
            QMessageBox.warning(
                self.parent,
                tr("Its game files are not there"),
                tr("This loadout was built against:\n"
                   "%(file)s\n"
                   "\n"
                   "That file is not where it was, and %(game)s is open "
                   "instead, so the loadout cannot be opened.\n"
                   "\n"
                   "Put that file back where it was, and open the loadout "
                   "again.", file=wrappable_path(wanted), game=mine.label),
            )
            return False
        values = {
            "theirs": theirs.label, "wanted": wrappable_path(wanted),
            "mine": mine.label, "have": wrappable_path(loadout.source_path),
        }
        answer = ask(
            self.parent,
            tr("Load its game files too?"),
            tr("This loadout is built against %(theirs)s:\n%(wanted)s\n\n"
               "You have %(mine)s open:\n%(have)s\n\n"
               "Opening it will load its own file instead.", **values)
            if same_game else
            tr("This loadout is built against %(theirs)s:\n"
               "%(wanted)s\n"
               "\n"
               "You have %(mine)s open:\n"
               "%(have)s\n"
               "\n"
               "Opening it will load its own file instead.\n"
               "\n"
               "The two games share no GUIDs or paths, so no change carries "
               "over between them.", **values),
            tr("&Load its files"),
        )
        return answer

    def confirm_reach(self, assets: int) -> bool:
        """Whether to apply typed ModOps that change one that finds its
        assets by searching, now that it is known how many it finds."""
        return ask(
            self.parent,
            tr("Apply to a search?"),
            trn(
                "What you typed changes a ModOp that finds its assets by "
                "searching. Applied, it changes %(n)s asset.",
                "What you typed changes a ModOp that finds its assets by "
                "searching. Applied, it changes %(n)s assets.", assets,
            ) + "\n\n" + tr("Apply is one step of Undo."),
            trn("&Apply to %(n)s asset", "&Apply to %(n)s assets", assets),
        )

    def ask_way_on(self, label: str, patch: str = "", in_its_file: bool = False) -> str:
        """Where a change to somebody else's mod, ``label``, goes, since
        their mod is never changed (section 5.2 of the projects and loadouts
        proposal): "patch" for your patch for it -- ``patch``, or one made
        for it where that is "" -- "own" for this loadout's own mod, or ""
        for neither.  ``in_its_file`` is for *Edit this file* on the Mod
        file tab, where the answer says which file the tab goes to."""
        said = tr("%s is somebody else's mod, so your changes go into a mod of "
                  "yours. Which one?", named(label))
        if in_its_file:
            said += "\n\n" + tr("This tab then shows that mod's file.")
        picked = choose(
            self.parent,
            tr("Change somebody else's mod?"),
            said,
            [
                (tr("Use my &patch"),
                 tr("Your patch, %s, keeps your changes wherever you use this mod, "
                    "through its updates.", patch))
                if patch else
                (tr("Make a &patch"),
                 tr("Keeps your changes wherever you use this mod, through its "
                    "updates.")),
                (tr("Use this &loadout's mod"),
                 tr("Keeps your changes in this loadout only.")),
                (tr("Make my own &copy…"),
                 tr("A copy to change directly. Its author's updates no longer "
                    "reach it.")),
            ],
        )
        return ("patch", "own", "fork")[picked] if picked >= 0 else ""

    def ask_fork_a_mod(self, label: str, game: str, patch: str = "", others=(),
                       creator: str = "") -> Optional[Tuple[str, bool]]:
        """Whether to make a fork of somebody else's mod, ``label``, saying
        first what that means (section 5.4 of the projects and loadouts
        proposal): None for no, and otherwise how, and whether to fold your
        patch for it, ``patch``, in.  ``others`` names the other loadouts
        that use the patch, which keep it.

        ``"copy"`` is the fork.  ``"mine"`` is for a mod that was yours all
        along, published under another name, ``creator``: offered where the
        mod names one, it is carried on with as it is, and the name is one
        of yours from then on
        (:meth:`ui.patches_controller.PatchesController.claim`).
        """
        from PySide6.QtWidgets import QCheckBox

        from ..core.games import ANNO_1800

        means = [
            tr("It is your copy now. Its author's updates no longer reach it."),
            tr("You should not share it without its author's permission."),
            tr("The library's copy, as its author shipped it, is kept."),
        ]
        if game == ANNO_1800:
            means.append(tr(
                "Mods that need the original will say it is missing: Anno 1800's "
                "loader does not count your copy as it."
            ))
        paragraphs = [
            tr("Your copy of %s becomes a mod of yours, with you as its author and "
               "a ModID of its own. It takes the original's place in this loadout, "
               "with its settings, and the game leaves the original out wherever "
               "your copy loads.", named(label)),
            bullets(means),
        ]
        choices = [(tr("&Make my copy"), "")]
        if creator:
            paragraphs.append(tr("If you published it as %s, carry on with it as yours "
                                 "instead.", named(creator)))
            choices.append((tr("&Carry on as mine"),
                            tr("It keeps its name and ModID, and %s becomes one of your "
                               "author names.", creator)))
        fold = said = None
        # Followed while the box is up: the tick-box goes with the box, and
        # read after it, it was already deleted (R3-28, hands-on run 3).
        folding = [bool(patch)]
        if patch:
            fold = QCheckBox(tr("&Fold my patch in"))
            fold.setChecked(True)
            fold.toggled.connect(lambda on: folding.__setitem__(0, on))
            said = tr("Ticked, your patch's changes go into the mod, and the patch "
                      "leaves this loadout.")
            if others:
                said += " " + trn("The loadout %(names)s keeps it.",
                                  "The loadouts %(names)s keep it.",
                                  len(others), names=join_list(list(others)))
        picked = choose(
            self.parent, tr("Make your own copy?"), "\n\n".join(paragraphs), choices,
            check=fold, check_said=said or "",
        )
        if picked < 0:
            return None
        return ("copy", "mine")[picked], folding[0]

    def ask_carry_on_as_mine(self, said: str, creator: str = "") -> Optional[bool]:
        """Whether to carry on with a mod that does not look like yours as
        yours anyway, ``said`` saying whose it says it is: None for no, and
        otherwise whether its author name, ``creator``, is to be one of yours
        from now on.  Asked unticked: a mod carried on with is not always one
        you published, and a name made yours makes every mod by it yours."""
        from PySide6.QtWidgets import QCheckBox

        box = QMessageBox(self.parent)
        box.setIcon(QMessageBox.Icon.Question)
        box.setWindowTitle(tr("Is this one yours?"))
        show_box_text(box, tr("%s\n"
                              "\n"
                              "Carry on with it as your own anyway? Your changes then go "
                              "straight into its files, so an update from its author "
                              "would replace them.", said))
        remember = None
        if creator:
            remember = QCheckBox(tr("&Remember %s as one of my author names", creator))  # design: names the name made yours
            box.setCheckBox(remember)
        go = box.addButton(tr("&Carry on as mine"), QMessageBox.ButtonRole.AcceptRole)
        stay = box.addButton(tr("&Unload it"), QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(stay)
        box.setEscapeButton(stay)
        if widgets.answer_of(box) is not go:
            return None
        return bool(remember is not None and remember.isChecked())

    def ask_make_project(self, label: str) -> bool:
        """Whether to make a project for ``label``, a mod with your name on it
        that nothing keeps (section 5.5 of the projects and loadouts
        proposal)."""
        return ask(
            self.parent,
            tr("Make a project?"),
            tr_kw("%s has your name on it, and no [[project]] of yours keeps it. Until "
                  "one does, it is read like anybody else's mod.\n"
               "\n"
                  "Make a [[project]] for it?", named(label)),
            tr("&Make a project"),
            cancel=tr("&Not now"),
            action_tip=tr("The project keeps the mod, and your changes go straight into it."),
        )

    def pick_a_mod_to_need(self, patch: str, mods: list):
        """Which of ``mods`` the patch ``patch`` is to need as well: its
        place in the list, or None for none."""
        from PySide6.QtWidgets import QInputDialog

        chosen, ok = QInputDialog.getItem(
            self.parent, tr("Needs another mod too"),
            tr("%s will load after the mod you pick, and be left out while it is off:", patch),
            mods, 0, False,
        )
        return mods.index(chosen) if ok and chosen in mods else None

    def ask_save_first(
        self, changes: int, taken: int = 0, details: bool = False, mod: bool = False,
        notes: int = 0, projects=(),
    ) -> str:
        """Save, discard or cancel, for a loadout with unsaved work: "save",
        "discard" or "".  ``changes`` is how many of your own edits are not
        saved: new since the save, or saying something else than they did.
        ``taken`` is how many changes and ModOps the loadout held when it
        was saved have been taken out or switched since, and ``notes`` how
        many of those not made here have another note.  ``mod`` is whether
        a mod's details have moved, said beside whatever is counted.
        ``details`` is whether which mods are on has, said where nothing is
        counted.  None of them still means something marked it unsaved.
        ``projects`` names the projects of your mods that saving writes as
        well (section 4.4 of the projects and loadouts proposal)."""
        # One sentence for each that is so, each whole: with both so, the
        # question used to name the changes made here and leave the rest out.
        said = []
        if changes:
            said.append(trn(
                "%(n)s change you have made is not saved.",
                "%(n)s changes you have made are not saved.",
                changes,
            ))
        if taken:
            # Something the loadout held when it was saved, reverted or
            # switched: read from a mod's folder, the folder still has it as
            # it was.
            said.append(trn(
                "%(n)s change or ModOp has been reverted or switched since "
                "this loadout was saved.",
                "%(n)s changes and ModOps have been reverted or switched since "
                "this loadout was saved.",
                taken,
            ))
        if notes:
            # A note typed on something read in is neither of those: nothing
            # was made and nothing reverted, and it is lost all the same.
            said.append(trn(
                "The note on %(n)s change or ModOp has changed since this "
                "loadout was saved.",
                "The notes on %(n)s changes and ModOps have changed since this "
                "loadout was saved.",
                notes,
            ))
        if details and not said:
            # Neither, and still something to lose: a switch or a profile.
            # It speaks for a mod's details as well, where both have moved.
            said.append(tr(
                "Which mods this loadout has on, or its details, have changed "
                "since it was saved."
            ))
        elif mod:
            # A description typed on the Export tab is lost with a Discard
            # like any change, and beside a count it used to go unsaid.
            said.append(tr("A mod's details have changed since this loadout was saved."))
        if not said:
            # Asked all the same, by a loadout with changes made here that
            # was marked unsaved for something none of the three can see: a
            # checkpoint taken, a change made and undone.  It is not known
            # what, so nothing is said to have changed.
            said.append(tr("This loadout has been worked on since it was saved."))
        if projects:
            # Saving writes more than the loadout's file, and which files
            # is said before it does.
            said.append(trn_kw(
                "Saving also saves the [[project]] of %(names)s.",
                "Saving also saves the [[project|projects]] of %(names)s.",
                len(projects), names=", ".join(named(project) for project in projects),
            ))
        answer = self._keep_or_discard(
            tr("Unsaved loadout"),
            "%s\n\n%s" % (" ".join(said), tr("Save before continuing?")),
            QMessageBox.StandardButton.Save,
        )
        if answer == QMessageBox.StandardButton.Save:
            return "save"
        return "discard" if answer == QMessageBox.StandardButton.Discard else ""

    def _keep_or_discard(self, title: str, text: str, keep):
        """Keep, Discard or Cancel, for work that would be lost: the
        standard button clicked, with ``keep`` (Save or Apply) the default
        since it is the answer that loses nothing."""
        box = QMessageBox(self.parent)
        box.setIcon(QMessageBox.Icon.Question)
        box.setWindowTitle(title)
        show_box_text(box, text)
        box.setStandardButtons(keep | QMessageBox.StandardButton.Discard
                               | QMessageBox.StandardButton.Cancel)
        box.setDefaultButton(keep)
        clicked = self.answer(box)
        return box.standardButton(clicked) if clicked is not None else None

    def ask_open_kept(self, mod: str, loadouts: list, older: str = "") -> int:
        """Which of ``loadouts`` to open, for a mod of yours that is kept in
        a project those loadouts have: its index, or -1 to open none.
        ``older`` says the folder read is an older build than the project's."""
        one = len(loadouts) == 1
        text = trn_kw(
            "%(mod)s is kept in a [[project]] already, which the loadout %(names)s "
            "has. Open it to carry on there, so the mod is kept in one [[project]].",
            "%(mod)s is kept in a [[project]] already, which these loadouts have: "
            "%(names)s. Open one to carry on there, so the mod is kept in one "
            "[[project]].",
            len(loadouts), mod=named(mod), names=", ".join(loadouts),
        )
        if older:
            text = "%s\n\n%s" % (older, text)
        if one:
            return 0 if ask(
                self.parent, tr("The mod has a project"), text,
                tr("&Open %s", loadouts[0]), default_action=True,
            ) else -1
        # A picker's label is plain: no name set apart, no keyword marked.
        chosen, picked = QInputDialog.getItem(
            self.parent, tr("The mod has a project"), widgets.plain_text(text), loadouts, 0,
            False,
        )
        return loadouts.index(chosen) if picked and chosen in loadouts else -1

    def report_held_projects(self, held) -> None:
        """Say which projects of the loadout just opened cannot be read nor
        written from it: ``held`` is ``[(name, why, where, standing_in)]``,
        why being :data:`core.projects.MISSING` or ``UNREADABLE``, and
        standing_in the library build read in a missing one's place, or ""
        (:func:`core.readback.stand_in`)."""
        from ..core import projects

        lines = []
        for name, why, where, standing_in in held:
            if why == projects.MISSING and standing_in:
                lines.append(tr_kw(
                    "The [[project]] of %(name)s is not at %(where)s. The library's build "
                    "of it, at %(build)s, is read instead, and nothing in it can be edited "
                    "until its [[project]] is found.", name=named(name), where=where,
                    build=standing_in,
                ))
            elif why == projects.MISSING:
                lines.append(tr_kw("The [[project]] of %(name)s is not at %(where)s.",
                                   name=named(name), where=where))
            else:
                lines.append(tr_kw("The [[project]] of %(name)s at %(where)s could not be "
                                   "read.", name=named(name), where=where))
        widgets.tell(
            self.parent,
            trn("A project cannot be saved from this loadout",
                "Projects cannot be saved from this loadout", len(held)),
            "\n\n".join(lines), warning=True,
        )

    def ask_unapplied_modops(self, asset: str, typed_asset: bool) -> str:
        """Apply, discard or cancel, for what was typed in the asset editor's
        XML view and not applied: "apply", "discard" or "" to stay.  The view
        has two panes to type in, and ``typed_asset`` says it was the asset
        and not its ModOps."""
        # Apply is the answer that loses nothing, as Save is for a loadout
        # with unsaved work. Qt picks that one by itself and not this: with
        # none named, Enter went to Discard.
        answer = self._keep_or_discard(
            tr("Asset not applied") if typed_asset else tr("ModOps not applied"),
            tr("What you typed in the asset %s is not applied.\n\nApply it "
               "before continuing?", named(asset))
            if typed_asset
            else tr("The ModOps you typed for %s are not applied.\n\nApply them "
                    "before continuing?", named(asset)),
            QMessageBox.StandardButton.Apply,
        )
        if answer == QMessageBox.StandardButton.Apply:
            return "apply"
        return "discard" if answer == QMessageBox.StandardButton.Discard else ""

    def ask_unapplied_file(self, mod: str) -> str:
        """Apply, discard or cancel, for what was typed in a mod's file and
        not applied: "apply", "discard" or "" to stay."""
        answer = self._keep_or_discard(
            tr("File not applied"),
            tr("What you typed in the file of %s is not applied.\n\nApply it "
               "before continuing?", named(mod)),
            QMessageBox.StandardButton.Apply,
        )
        if answer == QMessageBox.StandardButton.Apply:
            return "apply"
        return "discard" if answer == QMessageBox.StandardButton.Discard else ""

    def ask_switch_games(self, source_name: str, detected: str, current: str, mods: int) -> bool:
        """Whether to switch the loadout from ``current`` to ``detected``,
        whose files ``source_name`` are; ``mods`` loaded mods would go."""
        lines = [
            tr("%(source)s is built for %(detected)s. This loadout is set up "
               "for %(current)s.",
               source=source_name,
               detected=games.get(detected).label,
               current=games.get(current).label)
        ]
        if mods:
            lines.append(trn(
                "%(n)s loaded mod was read against the other game and cannot "
                "carry over. Switching will unload it.",
                "%(n)s loaded mods were read against the other game and "
                "cannot carry over. Switching will unload them.", mods,
            ))
        lines.append(tr("Switch games and start a new loadout?"))
        answer = ask(
            self.parent,
            tr("Switch games?"),
            "\n\n".join(lines),
            tr("&Switch games"),
            default_action=False,
        )
        return answer

    def ask_creator_name(self, suggested: str):
        """Who you publish as: ``(name, ok)``, the box filled in with
        ``suggested``."""
        return QInputDialog.getText(
            self.parent,
            tr("Who are you?"),
            tr("The name to publish your mods under. It goes in the modinfo "
               "of\n"
               "every mod you export. Mods with this author name are yours, "
               "and\n"
               "the Studio edits them directly. Other mods get a patch."),
            text=suggested,
        )

    def ask_loadout_name(self, current: str, mine: bool, patch_follows: bool = False):
        """A new name for the loadout: ``(name, ok)``.  ``mine`` when the
        loadout stands for a mod of your own, whose name it is too;
        ``patch_follows`` when its patch over the list is named after it."""
        if mine:
            said = tr("The name of your mod, and of the loadout holding it.")
        elif patch_follows:
            said = tr("What to call this loadout. Its patch over the mod list is named "
                      "after it, and every other mod in it keeps its name.")
        else:
            said = tr("What to call this loadout. It does not rename any mod inside it.")
        return QInputDialog.getText(self.parent, tr("Rename loadout"), said, text=current)
