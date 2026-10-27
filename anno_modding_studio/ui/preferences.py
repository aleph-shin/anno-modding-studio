"""What the Studio remembers about you, and the Settings dialog that changes it.

Who you publish as, how the Studio looks, the language, how each game is started
and where the library is: one controller, asked by the window and by
``ui.settings_dialog``, which used to call some twenty methods of the window
directly -- an interface with no object behind it (Stage 3.3 of
docs/proposals/consolidation-proposal.md).

Everything here is kept in the window's settings, and nothing here holds
state of its own: it reads them when asked.  The game's own loader -- its
folder, its probes, whether a newer extension is out -- is the loader
check's, not this.
"""

from __future__ import annotations

from typing import Optional

from PySide6.QtWidgets import QMessageBox

from ..core import games, guidranges
from ..core.i18n import tr, trn
from . import appearance, language, settings_dialog
from .where import LIBRARY_KEY


class Preferences:
    """The window's preferences, moved off it; ``window`` is the MainWindow."""

    # Kept in the settings rather than in the loadout, because it is a fact
    # about the person and not about the piece of work. It used to live only in
    # the Creator box on the Generate tab, which meant it was blank until
    # somebody found that box -- and blank is what decides whether a loaded mod
    # is "somebody else's". So the Studio could not tell your own mods from
    # anyone's, and nothing ever asked.
    CREATOR_KEY = "creator"
    # The other author names you have published under: a mod by one of them
    # is yours too, and an update to it goes out under the name it has. New
    # mods take the one above.
    ALIASES_KEY = "creator_aliases"
    GAME_START_KEY = "game_start:%s"
    # The register's owner cell of the range you draw new GUIDs from, per
    # game, or nothing for the one your creator name is listed under.
    GUID_RANGE_KEY = "guid_range:%s"
    LIBRARY_KEY = LIBRARY_KEY

    def __init__(self, window) -> None:
        self.window = window

    def show_settings(self, tab: int = 0) -> None:
        """Everything the Studio remembers about you, in one dialog, open on
        ``tab`` -- one of ``settings_dialog``'s ``TAB_`` numbers."""
        dialog = settings_dialog.SettingsDialog(self.window, self.window, tab=tab)
        dialog.exec()
        dialog.deleteLater()

    def creator_name(self) -> str:
        """Who the Studio thinks you are, or "" if nobody has said."""
        return str(self.window.settings.value(self.CREATOR_KEY) or "").strip()

    def set_creator_name(self, name: str) -> bool:
        """Write down who you publish as. Says whether anything moved.

        Apart from the asking, so that Settings -- where the name is a field
        rather than a prompt -- lands it the same way this does, loadout and
        all. See :mod:`ui.settings_dialog`.
        """
        name = (name or "").strip()
        known = self.creator_name()
        if name == known:
            return False
        if name:
            self.window.settings.setValue(self.CREATOR_KEY, name)
        else:
            self.window.settings.remove(self.CREATOR_KEY)
        # One of the other names made this one is no longer among them.
        self.hand_over(self.window.loadout)
        # The open loadout follows along only if it was wearing the old name:
        # one deliberately published under something else stays as it is.
        if not (self.window.loadout.own.creator or "").strip() or self.window.loadout.own.creator == known:
            self.window.loadout.own.creator = name
            self.window.output.load_loadout_fields()
            self.window.files.mark_unsaved()
        self.window.start.refresh_source_panel()
        self.follow_your_names()
        self.window.status_label.setText(
            tr("Publishing as %s.", name) if name else tr("No author name set.")
        )
        return True

    def aliases(self) -> list:
        """The other author names you have published under, as given: none
        of them the one above, and none twice."""
        raw = self.window.settings.value(self.ALIASES_KEY) or []
        if isinstance(raw, str):
            raw = [raw]
        primary = self.creator_name().casefold()
        found, seen = [], {primary} if primary else set()
        for name in (str(entry).strip() for entry in raw):
            if name and name.casefold() not in seen:
                seen.add(name.casefold())
                found.append(name)
        return found

    def set_aliases(self, names) -> bool:
        """Write down the other names you have published under. Says whether
        anything moved."""
        before = self.aliases()
        self.window.settings.setValue(self.ALIASES_KEY, [str(name).strip() for name in names if str(name).strip()])
        now = self.aliases()
        if not now:
            self.window.settings.remove(self.ALIASES_KEY)
        if now == before:
            return False
        self.hand_over(self.window.loadout)
        self.follow_your_names()
        return True

    def add_alias(self, name: str) -> bool:
        """Count ``name`` as one of yours from now on, as a mod's author said
        is you (:meth:`ui.patches_controller.PatchesController.claim`)."""
        name = (name or "").strip()
        if not name or self.is_your_name(name):
            return False
        return self.set_aliases([*self.aliases(), name])

    def your_names(self) -> list:
        """Every author name you publish under: the one new mods take, first."""
        primary = self.creator_name()
        return ([primary] if primary else []) + self.aliases()

    def is_your_name(self, name: str) -> bool:
        said = (name or "").strip().casefold()
        return bool(said) and said in {known.casefold() for known in self.your_names()}

    def hand_over(self, loadout) -> None:
        """Tell ``loadout`` your other names, which it never saves: done for
        every loadout the window takes up."""
        loadout.aliases = self.aliases()

    def follow_your_names(self) -> None:
        """Your names moved with a loadout open: what it says on screen of
        whose its mods are follows -- *Currently editing*, which offers a
        mod with one of your names a project and somebody else's a patch;
        the file tab, which says which of those a mod it only shows is; the
        Mod changes panel's word on clashes yours to put right.  It went on
        saying what it had until something else redrew it, so a name given
        in Settings changed nothing to be seen."""
        self.window.editor.refresh_target()
        self.window.changes_panel.refresh()
        self.window.file_panel.mark_stale()

    def change_creator(self) -> bool:
        """Say who you publish as, at any point rather than only the first.

        The first time it is asked is the worst time to answer it: nobody has
        used the Studio yet, the box is pre-filled with a Windows username, and
        what it decides -- whose mods count as yours, and so which ones get
        edited directly and which get patched -- only becomes clear later.
        """
        name, ok = self.window.prompts.ask_creator_name(self.creator_name())
        return self.set_creator_name(name) if ok else False

    def ensure_creator(self) -> Optional[str]:
        """The name to publish under, asking once if it is not known yet.

        Asked at the moment it first matters -- naming a mod, or making a patch
        -- rather than on a settings page nobody visits. Once given it is
        remembered for every loadout on this machine, and can still be
        overridden per loadout on the Generate tab.

        None where the question was cancelled, which cancels what asked it:
        the questions that action had still to ask came up after it all the
        same.  "" where it was answered with no name, which the action goes
        on without.
        """
        known = self.creator_name()
        if known:
            if not (self.window.loadout.own.creator or "").strip():
                self.window.loadout.own.creator = known
            return known

        import getpass

        try:
            suggested = getpass.getuser()
        except Exception:  # noqa: BLE001 - a username is a nicety
            suggested = ""
        name, ok = self.window.prompts.ask_creator_name(suggested)
        if not ok:
            return None
        name = (name or "").strip()
        if not name:
            return ""
        self.window.settings.setValue(self.CREATOR_KEY, name)
        if not (self.window.loadout.own.creator or "").strip():
            self.window.loadout.own.creator = name
            self.window.output.load_loadout_fields()
        return name

    def appearance(self) -> dict:
        """The Appearance settings as saved: scheme, outlines, density, size."""
        settings = self.window.settings
        return {
            "scheme": appearance.saved_scheme(settings),
            "outlines": appearance.saved_outlines(settings),
            "density": appearance.saved_density(settings),
            "size": appearance.saved_size(settings),
        }

    def set_appearance(self, scheme: str = "", outlines: str = "", density: str = "") -> None:
        """Remember what is given and restyle the whole app at once."""
        from PySide6.QtWidgets import QApplication

        for key, value in ((appearance.SCHEME_KEY, scheme),
                           (appearance.OUTLINES_KEY, outlines),
                           (appearance.DENSITY_KEY, density)):
            if value:
                self.window.settings.setValue(key, value)
        appearance.apply_saved(QApplication.instance(), self.window.settings)

    def set_interface_size(self, percent: int) -> None:
        """Remember the size for the next start. Qt reads it only once."""
        self.window.settings.setValue(appearance.SIZE_KEY, int(percent))

    def language_choice(self) -> str:
        """The language picked in Settings, or "" for the one Windows uses."""
        return language.saved_choice(self.window.settings)

    def set_language_choice(self, code: str) -> None:
        """Remember ``code`` for the next start; nothing on screen changes now."""
        self.window.settings.setValue(language.LANGUAGE_KEY, code)

    def checks_for_studio(self) -> bool:
        """Whether GitHub is asked once a day for a newer version of the
        Studio: the window's ``studio_release`` keeps the answer."""
        return self.window.studio_release.is_on()

    def set_checks_for_studio(self, on: bool) -> None:
        self.window.studio_release.set_on(on)

    def game_start(self, game: str = "") -> str:
        """How ``game`` is started when chosen in the settings -- a program, a
        shortcut or a store address -- or "" for finding its store."""
        return str(self.window.settings.value(self.GAME_START_KEY % (game or self.window.loadout.game), "") or "")

    def set_game_start(self, game: str, how: str) -> None:
        self.window.settings.setValue(self.GAME_START_KEY % game, (how or "").strip())

    def guid_range(self, game: str = "") -> str:
        """The owner of the range chosen in Settings for ``game``'s new
        assets, or "" to go by the creator name."""
        return str(self.window.settings.value(self.GUID_RANGE_KEY % (game or self.window.loadout.game), "") or "")

    def set_guid_range(self, game: str, owner: str) -> None:
        if owner:
            self.window.settings.setValue(self.GUID_RANGE_KEY % game, owner)
        else:
            self.window.settings.remove(self.GUID_RANGE_KEY % game)

    def automatic_game_start(self, game: str) -> str:
        """What Play would start ``game`` with, left to itself: its store's
        address, or "" when neither store has it."""
        from ..core import launch as launch_mod

        return launch_mod.uri(launch_mod.install_for(game, self.window.game_folder if game == self.window.loadout.game else None))

    def library_root(self) -> str:
        """The library's root as chosen in the settings, or "" for the default."""
        return self.window.where.library_root()

    def set_library_root(self, path: str) -> None:
        self.window.settings.setValue(self.LIBRARY_KEY, path or "")
        self.window.refresh_truth(say=False)
        # The Start tab says where it is.
        self.window.start.refresh_games()

    def update_guid_ranges(self) -> None:
        """Fetch the community GUID register afresh, for every game.

        Done here and now rather than on a thread: it is two small files
        from GitHub, asked for by a click, and the result is a sentence.
        """
        self.window.status_label.setText(tr("Fetching the community GUID ranges…"))
        fetched, failed = guidranges.refresh_all()
        lines = []
        for key, register in fetched.items():
            lines.append(trn(
                "%(game)s: %(n)s range listed.", "%(game)s: %(n)s ranges listed.",
                len(register.ranges), game=games.get(key).label,
            ))
        for key, why in failed.items():
            label = games.get(key).label
            if guidranges.load(key).missing:
                lines.append(tr(
                    "%(game)s: could not be fetched (%(why)s). New assets use "
                    "the register's personal-use range until it can be.",
                    game=label, why=why,
                ))
            else:
                lines.append(tr(
                    "%(game)s: could not be fetched (%(why)s). The copy "
                    "fetched before stays in use.", game=label, why=why,
                ))
        if fetched:
            lines.append(tr(
                "\nNew assets pick their GUIDs from these from now on. Any "
                "New asset dialog already open keeps the list it was opened with."
            ))
        if failed:
            lines.append(tr(
                "\nCheck the connection and try again, or see %s.",
                guidranges.REPOSITORY,
            ))
        report = "\n".join(lines)
        if fetched:
            QMessageBox.information(self.window, tr("Community GUID ranges"), report)
            self.window.status_label.setText(tr("Community GUID ranges updated."))
        else:
            QMessageBox.warning(self.window, tr("Community GUID ranges"), report)
            self.window.status_label.setText(tr("Community GUID ranges not updated."))
