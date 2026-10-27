"""Where the Studio keeps what it makes.

Two places, by what is kept.  What you make -- loadouts, exports, backups --
hangs off a single home directory under Documents, which is where a backup
tool looks, and that directory answers to ``ANNO_STUDIO_HOME``.  What can be
made again -- the files extracted from the game, the indexes built from them
-- is under the local app data, which is where nothing syncs it.  The library,
the settings and the mod.io key are there too, each for a reason of its own
(see :mod:`core.library`, :func:`settings_file` and :mod:`core.secrets`).

Or one place.  :func:`one_folder` names a folder that holds all of it, the
settings included, for whoever wants to know that everything the Studio
writes is in one folder they chose: any folder ``ANNO_STUDIO_FOLDER`` names,
or, run from its source, a folder called ``AnnoModdingStudioData`` beside
it.  Opted into, never the default: Windows keeps the two kinds apart for
the reasons above, and a public program follows that unless told otherwise.

A built Studio never takes the folder beside it.  It is updated by
unzipping the new version into a new folder, and a choice kept in the old
program folder stayed behind there: the new version started on the usual
places with none of the user's data, and deleting the old folder deleted
the library every game's mods folder links into.  Anyone who could write
into the program folder could also move all of the Studio's data into a
folder of theirs by making one there (security review S4).  The variable is
set outside any program folder, so every version finds it.

``ANNO_STUDIO_HOME`` is not a nicety.  Loadouts are written by ordinary use:
choosing a way of working names one, and the first change writes it out.  A
test suite that drives that path writes real loadout files, and with the home
directory hardcoded it wrote them into the *developer's own* Documents --
fourteen hundred of them, on the machine this was found on.  Settings are
protected the same way, and for the same reason (see
``ui.main_window.open_settings``): an environment variable cannot be forgotten
by the thing it is protecting, because it is set from outside it.  So a
variable that names one of the Studio's folders always wins, over the one
folder too.

Named ``places`` rather than ``paths`` on purpose: :mod:`core.paths` is about
XPath inside an asset, and the two are not the same subject.
"""

from __future__ import annotations

import contextlib
import os
import shutil
import sys
from pathlib import Path
from typing import List, Optional

ENV_HOME = "ANNO_STUDIO_HOME"
ENV_FOLDER = "ANNO_STUDIO_FOLDER"
ENV_DOCUMENTS = "ANNO_STUDIO_DOCUMENTS"
ENV_SETTINGS = "ANNO_STUDIO_SETTINGS"

# What the Studio's folders are called: in Documents, in the local app data
# where indexes are cached, and in the settings.
NAME = "AnnoModdingStudio"
# The folder that, found beside the Studio run from its source, is where it
# keeps everything.
BESIDE_NAME = "AnnoModdingStudioData"
SETTINGS_NAME = "settings.ini"


# -- the machine's own folders ---------------------------------------------------
def documents() -> Path:
    """The user's Documents folder: the Studio's home is under it, and so is
    each game's own folder.

    ``ANNO_STUDIO_DOCUMENTS`` names another, which is for the test suite: a
    game's own mods folder is under Documents, and with nothing to name it
    apart the suite read the developer's, and a script's writes went there
    (audit finding A8-10).
    """
    named = os.environ.get(ENV_DOCUMENTS, "").strip()
    return Path(named) if named else Path.home() / "Documents"


def local_app_data() -> Path:
    """``%LOCALAPPDATA%``, where caches and mod.io's client keep their files;
    its usual place under the home folder where the variable is not set."""
    named = os.environ.get("LOCALAPPDATA", "").strip()
    return Path(named) if named else Path.home() / "AppData" / "Local"


def built() -> bool:
    """Whether this Studio is a built program rather than its source run
    by Python."""
    return bool(getattr(sys, "frozen", False))


def app_dir() -> Path:
    """The folder the Studio itself is in: the program's, once it is built
    into one, and until then the one holding the package and ``run.bat``."""
    if built():
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[2]


# -- the Studio's -----------------------------------------------------------------
def _beside() -> Optional[Path]:
    beside = app_dir() / BESIDE_NAME
    return beside if beside.is_dir() else None


def one_folder() -> Optional[Path]:
    """The one folder everything the Studio writes is kept in, where there is
    one: what you make, what can be made again, the library, the settings and
    the mod.io key.  None is the ordinary case, with each in its usual place.

    ``ANNO_STUDIO_FOLDER`` names it, and ``none`` there says there is none
    whatever is beside the Studio -- which is how the test suite stays out of
    a developer's own.  Otherwise, run from the source, it is the folder
    called ``AnnoModdingStudioData`` beside it, where somebody has made one:
    making it is the whole of opting in, and nothing is moved into it.  A
    built Studio has only the variable (see the module's docstring).
    """
    named = os.environ.get(ENV_FOLDER, "").strip()
    if named.lower() == "none":
        return None
    if named:
        return Path(named)
    return None if built() else _beside()


def unused_beside() -> Optional[Path]:
    """A folder called ``AnnoModdingStudioData`` beside a built Studio,
    which the Studio does not keep anything in and says so as it starts;
    None where there is none, or where it would be used."""
    if not built() or os.environ.get(ENV_FOLDER, "").strip():
        return None
    return _beside()


def studio_home() -> Path:
    """The folder holding what you make with the Studio."""
    named = os.environ.get(ENV_HOME, "").strip()
    if named:
        return Path(named)
    return one_folder() or documents() / NAME


def local_home() -> Path:
    """The folder holding what the Studio keeps for itself: what can be made
    again (the cache, the game's extracted files), what must not leave this
    machine (the mod.io key), and by default the library -- which is not
    made again: a conversion moves the user's own mod folders into it, so
    it can hold the only copy of them.  Under the local app data, which no
    backup tool syncs and where a file is not swapped for a placeholder:
    the game reads the library's mods through links into it."""
    return one_folder() or local_app_data() / NAME


def data_folders() -> List[Path]:
    """The Studio's own folders, each once: the home of what you make and
    the folder of what it keeps for itself, which are one folder where
    there is one folder.  Each says which Studio last used it
    (:mod:`core.dataversion`)."""
    found: List[Path] = []
    for folder in (studio_home(), local_home()):
        spelt = os.path.normcase(str(folder))
        if all(os.path.normcase(str(seen)) != spelt for seen in found):
            found.append(folder)
    return found


def loadouts_dir() -> Path:
    return studio_home() / "loadouts"


def projects_dir() -> Path:
    """Where each mod of yours is kept, apart from the loadouts that have it
    (:mod:`core.projects`)."""
    return studio_home() / "projects"


def exports_dir() -> Path:
    return studio_home() / "exports"


def backups_dir() -> Path:
    """With what you make, though some of it is large: the large part is only
    ever made because somebody asked for it."""
    return studio_home() / "backups"


def cache_dir() -> Path:
    """Where what can be built again is kept between launches: the asset
    indexes, the zips' unpacked mirrors, a refreshed list of GUID ranges.

    In the local app data rather than the home folder, which is for what
    people make. ``ANNO_STUDIO_CACHE`` names somewhere else, which is how the
    test suite keeps its indexes out of the developer's real cache -- the same
    protection settings and the home folder have, for the same reason.
    """
    named = os.environ.get("ANNO_STUDIO_CACHE", "").strip()
    path = Path(named) if named else local_home() / "cache"
    path.mkdir(parents=True, exist_ok=True)
    return path


def files_cache() -> Path:
    """The cache that answers questions across the loadout files
    (:mod:`core.filecache`): in the local folder, because a SQLite file
    that OneDrive syncs while it is written can be ruined, and the home
    folder is under Documents, which OneDrive syncs.  Not in
    :func:`cache_dir`, whose every ``.sqlite`` the asset index prunes as
    one of its own."""
    return local_home() / "files.sqlite"


def extracted_dir() -> Path:
    """Where the files read out of the game's archives go: hundreds of
    megabytes that the game folder can give again, so with the cache and not
    in Documents.  A home named from outside still takes them with it, which
    is what keeps a test's, and a scratch script's, out of the real one."""
    named = os.environ.get(ENV_HOME, "").strip()
    if named:
        return Path(named) / "extracted"
    return local_home() / "extracted"


def former_extracted_dir() -> Path:
    """Where the game's files were extracted to before they left Documents.
    What was read there is still there, and still good: it is looked in
    after :func:`extracted_dir`, and never suggested for a new read."""
    return studio_home() / "extracted"


def settings_file() -> Path:
    """The settings file: with what the Studio keeps for itself, beside the
    library and the cache, and so in the one folder where there is one.
    ``ANNO_STUDIO_SETTINGS`` names another (see
    ``ui.main_window.open_settings``).

    Not where Qt keeps a user's, under the roaming app data.  That made a
    third folder called AnnoModdingStudio, under a third parent, for
    somebody to find; and what the settings hold -- the library's root,
    each game's mods folder -- is true of this machine alone, which a
    roaming copy is not."""
    named = os.environ.get(ENV_SETTINGS, "").strip()
    if named:
        return Path(named)
    return local_home() / SETTINGS_NAME


def roaming_app_data() -> Path:
    """``%APPDATA%``, where the settings were kept before they moved; its
    usual place under the home folder where the variable is not set."""
    named = os.environ.get("APPDATA", "").strip()
    return Path(named) if named else Path.home() / "AppData" / "Roaming"


def former_settings_file() -> Path:
    """Where the settings were kept until they moved beside the library:
    the file Qt names for a user, under the roaming app data."""
    return roaming_app_data() / NAME / (NAME + ".ini")


def adopt_former_settings() -> bool:
    """Move the settings from where they were kept before, once: where the
    settings file is not there yet and the former one is.  Its folder goes
    too, where nothing else is left in it.  Whether anything moved.

    Never into a file named from outside, or into the one folder, which
    took nothing from the usual places when it was chosen."""
    if os.environ.get(ENV_SETTINGS, "").strip() or one_folder() is not None:
        return False
    former, settings = former_settings_file(), settings_file()
    if settings.exists() or not former.is_file():
        return False
    try:
        settings.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(former), str(settings))
    except OSError:
        return False
    with contextlib.suppress(OSError):
        former.parent.rmdir()
    return True
