"""Where the window's things are: the game, its mods folder, the library.

One answer to each, which the window, its panels and the dialogs it opens all
ask.  There were five answers to "which mods folder", and they did not agree:
Play preferred the folder last read for the game, the card comparing the
loadout against the disk never looked at it, and fetching from mod.io took the
folder a loadout names even when the loadout was made on another machine and
the folder is not on this one.  So with no folder of the loadout's own, Play
could switch one folder while the card described another.  The library's
folder was asked for with the chosen root in most places and without it in
three (P8-3 and P8-42).

The rule, for a mods folder: the one this loadout's mods came from, else the
one last read for this game, else the game's own beside its installation, else
the one under Documents -- the first of those that is there.  Only a fetch may
name the last one before it exists, since fetching is what creates it.  The
loadout's own is one this PC knows, or one you agreed to as the loadout
opened: a loadout file can come from anybody, and Play builds into its folder
(:meth:`Where.knows_mods_folder`).

The window's own state -- the loadout, the settings, where the game is
installed -- stays the window's; this reads it each time it is asked, so a
loadout opened or a game switched is answered for at once.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import List, NamedTuple, Optional

from ..core import disk, games, library, modio_cache, places

# The settings key the library's chosen root is kept under.
LIBRARY_KEY = "library_dir"
# The settings key, per game, of the mods folder last read.
LAST_MODS_FOLDER = "last_mods_folder"
# And of every other mods folder read in, or named by a loadout and agreed
# to: the folders a loadout may name without being asked about.
AGREED_MODS_FOLDERS = "known_mods_folders"

# What each place :meth:`Where.places` lists is.
LOADOUTS, PROJECTS, EXPORTS, BACKUPS = "loadouts", "projects", "exports", "backups"
LIBRARY, MODS = "library", "mods"
EXTRACTED, CACHE = "extracted", "cache"
SETTINGS, PROGRAM, SOURCE = "settings", "program", "source"


class Place(NamedTuple):
    """One of the places the Studio uses that somebody may come looking
    for: what it is (one of the keys above) and where it is now."""

    key: str
    path: Path
    # Chosen by somebody, where it is not the Studio's own place for it:
    # the library in Settings, an export folder, game files read elsewhere.
    chosen: bool = False
    # A file, shown in its folder, where the rest are folders.
    file: bool = False


class Where:
    """The window's answers to where things are.  Tests replace a method on
    the one instance to put the window somewhere of their own."""

    def __init__(self, window) -> None:
        self._window = window

    # -- the game ------------------------------------------------------------
    @property
    def game(self) -> str:
        """The game the open loadout is for."""
        return self._window.loadout.game

    def per_game(self, key: str) -> str:
        """A settings key kept apart per game: 1800's and 117's mods live in
        different folders, and one remembered path for both opened dialogs on
        the wrong game's folder with nothing to say so."""
        return "%s:%s" % (key, self.game)

    # -- the library ---------------------------------------------------------
    def library_root(self) -> str:
        """The library's root as chosen in the settings, or "" for the default."""
        return str(self._window.settings.value(LIBRARY_KEY, "") or "")

    def library_dir(self, game: str = "") -> Path:
        """A game's folder in the library, wherever the library is: the open
        loadout's game unless another is named."""
        return library.game_dir(game or self.game, self.library_root())

    # -- mods folders ----------------------------------------------------------
    def game_mods(self) -> Optional[Path]:
        """The ``mods`` folder beside the game's installation, when the Studio
        knows where the game is installed and the folder is there."""
        installed = self._window.game_folder
        if installed is None:
            return None
        candidate = Path(installed) / "mods"
        return candidate if candidate.is_dir() else None

    def mods_folder(self) -> str:
        """The mods folder this window is about: what Play switches, what the
        card compares against, what the library links into.  "" when none of
        the places it could be is there."""
        window = self._window
        own = self.game_mods()
        home = games.documents_dir(self.game)
        for folder in (
            (window.loadout.mods_folder or "").strip(),
            str(window.settings.value(self.per_game(LAST_MODS_FOLDER), "") or ""),
            str(own) if own is not None else "",
            str(home / "mods") if home is not None else "",
        ):
            if folder and Path(folder).is_dir():
                return folder
        return ""

    def known_mods_folders(self) -> List[str]:
        """The mods folders this PC is known to have: the game's, the one
        under Documents, the one last read, and every one read or agreed to
        since (:meth:`know_mods_folder`).  A loadout naming any other was
        made elsewhere, or by somebody else (:meth:`knows_mods_folder`)."""
        window = self._window
        own = self.game_mods()
        home = games.documents_dir(self.game)
        found = [
            str(own) if own is not None else "",
            str(home / "mods") if home is not None else "",
            str(window.settings.value(self.per_game(LAST_MODS_FOLDER), "") or ""),
            *self._agreed(),
        ]
        return [folder for folder in found if folder]

    def knows_mods_folder(self, folder: str) -> bool:
        """Whether ``folder`` is one of :meth:`known_mods_folders`, by its
        spelling: a loadout's folder is not looked at before it is known,
        since one on another machine is asked for over the network."""
        spelt = _spelt(folder)
        return bool(folder) and any(_spelt(known) == spelt for known in self.known_mods_folders())

    def know_mods_folder(self, folder: str) -> None:
        """Remember ``folder`` as a mods folder of this PC's: one read in,
        or one a loadout named that you agreed to use."""
        if not folder or self.knows_mods_folder(folder):
            return
        self._window.settings.setValue(self.per_game(AGREED_MODS_FOLDERS), [*self._agreed(), folder])

    def _agreed(self) -> List[str]:
        raw = self._window.settings.value(self.per_game(AGREED_MODS_FOLDERS)) or []
        return [str(entry) for entry in ([raw] if isinstance(raw, str) else raw) if entry]

    def folder_roots(self) -> List[str]:
        """Where a loaded mod's folder may be: the mods folders this PC
        knows, the loadout's own among them once it is known; the library,
        with its forks and generated copies; mod.io's download cache; and
        the Studio's cache, which holds the copies of the zips Anno 1800
        reads (:func:`.changes.keep_folders_inside`)."""
        return [
            *self.known_mods_folders(),
            # One of those, once the loadout's own is checked.
            self.mods_folder(),
            str(self.library_dir().parent),
            str(modio_cache.game_dir(self.game)),
            str(places.cache_dir()),
        ]

    def install_folder(self) -> str:
        """Where a mod fetched for this loadout goes: :meth:`mods_folder`,
        else the game's mods folder under Documents, which the fetch creates.
        Never a folder named by a loadout made on another machine."""
        found = self.mods_folder()
        if found:
            return found
        home = games.documents_dir(self.game)
        return str(home / "mods") if home is not None else ""

    # -- everything ------------------------------------------------------------
    def places(self) -> List[Place]:
        """Every place the Studio uses that somebody may come looking for,
        in the order *Help > Studio folders* lists them.

        There were four folders of the same name under three parents, and
        each was said only where it was used: the library on the Start tab,
        the export folder on the Export tab.  Somebody looking afterwards
        for where something went had no one place to look.  Each is asked
        of its owner here, so one moved in Settings, or by an
        ``ANNO_STUDIO_*`` variable, is listed where it is now."""
        window = self._window
        exports = window.output.export_dir()
        chosen_library = self.library_root()
        found = [
            Place(LOADOUTS, places.loadouts_dir()),
            Place(PROJECTS, places.projects_dir()),
            Place(EXPORTS, exports, not _same(exports, places.exports_dir())),
            Place(BACKUPS, places.backups_dir()),
            Place(LIBRARY, library.root(chosen_library), bool(chosen_library)),
        ]
        mods = self.install_folder()
        if mods:
            found.append(Place(MODS, Path(mods)))
        read = self._read_into()
        found += [
            Place(EXTRACTED, read or places.extracted_dir(),
                  read is not None and not disk.inside(read, places.extracted_dir())),
            Place(CACHE, places.cache_dir()),
            Place(SETTINGS, Path(window.settings.fileName()), file=True),
            Place(PROGRAM if places.built() else SOURCE, places.app_dir()),
        ]
        return found

    def _read_into(self) -> Optional[Path]:
        """The folder this game's files were last read into, while it is
        there."""
        window = self._window
        key = "%s:%s" % (window.game_files.READ_ROOT_KEY, self.game)
        remembered = str(window.settings.value(key, "") or "")
        return Path(remembered) if remembered and Path(remembered).is_dir() else None


def _spelt(folder: str) -> str:
    """``folder`` as it is compared, without looking at the disk."""
    return os.path.normcase(os.path.abspath(str(folder).strip())) if str(folder).strip() else ""


def _same(one: Path, other: Path) -> bool:
    """Whether two paths are spelled as the same place: neither need be
    there yet."""
    return os.path.normcase(os.path.abspath(str(one))) == os.path.normcase(os.path.abspath(str(other)))
