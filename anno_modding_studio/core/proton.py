"""Whether a game Steam runs through Proton can see the Studio's library.

On Linux, Steam runs a Windows game through Proton inside its runtime
container, which shares only some of the disk with the game: the home
folder, Steam's own folders and its library folders, and whatever the
game's launch options add with ``STEAM_COMPAT_MOUNTS`` or
``PRESSURE_VESSEL_FILESYSTEMS_RW``.  A link in the mods folder to a library
anywhere else is, from inside, a folder that is not there: the game loads
without the mod and says nothing.  So Play says so first
(docs/proposals/patches-proposal.md, section 7).

The same holds for a Studio run under Wine, in the game's prefix or beside
it, where the paths are Windows' and the folders Linux's: there the question
is asked of the Linux paths Wine gives (:func:`unseen_from_wine`).  It has
one more way to go wrong.  A junction keeps its target as a Windows path,
which the game reads in its own prefix, so a library on the C: drive of a
prefix that is not the game's is a folder the game does not have.

Worked out from the folders and Steam's files as they stand.  None of it has
been tried with the game running under Proton yet.
"""

from __future__ import annotations

import os
import re
from pathlib import Path, PurePosixPath
from typing import Iterable, List, Optional

from . import discovery, disk, launch, wine
from .i18n import tr

# What a game's launch options set to give the container more of the disk:
# each a list of folders, separated by colons.
LAUNCH_VARIABLES = ("STEAM_COMPAT_MOUNTS", "PRESSURE_VESSEL_FILESYSTEMS_RW")
_COMPATDATA = re.compile(r"/steamapps/compatdata/(\d+)(?:/|$)", re.I)


def steamapps_of(path) -> Optional[Path]:
    """The ``steamapps`` folder ``path`` is in, or None."""
    for folder in (Path(path), *Path(path).parents):
        if folder.name.lower() == "steamapps":
            return folder
    return None


def app_of(mods_folder) -> int:
    """The Steam app a mods folder is of, or 0: by the prefix it is in, for
    one under the game's Documents, or by the install it is in."""
    text = Path(mods_folder).as_posix()
    found = _COMPATDATA.search(text)
    if found:
        return int(found.group(1))
    for folder in (Path(mods_folder), *Path(mods_folder).parents):
        if folder.parent.name.lower() == "common":
            return launch.steam_app_id(folder)
    return 0


def launch_folders(roots: Iterable[Path], app: int) -> List[Path]:
    """The folders the game's launch options give the container, as each
    Steam user's ``localconfig.vdf`` has them."""
    found: List[Path] = []
    if not app:
        return found
    for root in roots:
        try:
            configs = list(Path(root).glob("userdata/*/config/localconfig.vdf"))
        except OSError:
            continue
        for config in configs:
            try:
                text = config.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            options = _launch_options(text, app)
            for variable in LAUNCH_VARIABLES:
                said = re.search(r"\b%s=(?:\"([^\"]*)\"|'([^']*)'|(\S+))" % variable, options)
                if said is None:
                    continue
                value = next(group for group in said.groups() if group is not None)
                found.extend(Path(part) for part in value.split(":") if part.strip())
    return found


def _launch_options(text: str, app: int) -> str:
    """The launch options ``localconfig.vdf`` keeps for ``app``, or ""."""
    for opening in re.finditer(r'"%d"\s*\{' % app, text):
        depth, at = 1, opening.end()
        while depth and at < len(text):
            char = text[at]
            if char == '"':
                at = text.find('"', at + 1)
                while at > 0 and text[at - 1] == "\\":
                    at = text.find('"', at + 1)
                if at < 0:
                    break
            elif char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
            at += 1
        said = re.search(r'"LaunchOptions"\s*"((?:[^"\\]|\\.)*)"', text[opening.end():at])
        if said is not None:
            return said.group(1).replace('\\"', '"').replace("\\\\", "\\")
    return ""


def shared_with_the_game(roots: Iterable[Path], libraries: Iterable[Path], home: Path,
                         added: Iterable[Path] = ()) -> List[Path]:
    """The folders the container shares with the game: the home folder,
    Steam's own, each Steam library and what the launch options add."""
    shared = [Path(home), *(Path(root) for root in roots), *(Path(folder) for folder in added)]
    for library in libraries:
        # steam_libraries gives each library's steamapps/common.
        steamapps = steamapps_of(library)
        shared.append(steamapps.parent if steamapps is not None else Path(library))
    return shared


def unseen(library_dir, mods_folder, roots=None, libraries=None, home=None,
           windows: Optional[bool] = None) -> List[str]:
    """What Play says when the game, run through Proton, cannot see the
    Studio's library -- the library's own folder, with the generated copies
    and the versions kept beside it.  Nothing on Windows itself, for a mods
    folder Steam does not hold, or where the library is somewhere the
    container shares."""
    if (os.name == "nt") if windows is None else windows:
        return unseen_from_wine(library_dir, mods_folder) if wine.version() else []
    if library_dir is None or steamapps_of(mods_folder) is None:
        return []
    roots = list(discovery.steam_roots() if roots is None else roots)
    libraries = list(discovery.steam_libraries() if libraries is None else libraries)
    places = shared_with_the_game(roots, libraries, Path.home() if home is None else home,
                                  launch_folders(roots, app_of(mods_folder)))
    where = Path(library_dir).parent
    if any(disk.inside(_real(where), _real(place)) for place in places):
        return []
    return [_unseen_line(str(where))]


def _unseen_line(where: str) -> str:
    # The second %(where)s is what to type into Steam.
    return tr(  # design: the path is part of the setting to type
        "The game runs in Proton, whose container cannot see the Studio's library in "
        "%(where)s, so the mods linked from it would be missing in game. Move the library "
        "into your home folder, or add STEAM_COMPAT_MOUNTS=%(where)s to the game's launch "
        "options in Steam.", where=where,
    )


def unseen_from_wine(library_dir, mods_folder, host: Optional[wine.Host] = None) -> List[str]:
    """:func:`unseen` for a Studio run under Wine, asked of the Linux paths
    Wine gives.  The game runs in this prefix where its mods folder is on
    this prefix's C: drive, and otherwise in the one Steam made for it; it
    runs in Proton where that prefix is one of Steam's."""
    host = wine.host() if host is None else host
    if host is None or library_dir is None:
        return []
    where = Path(library_dir).parent
    where_unix, mods_unix = host.unix(where), host.unix(mods_folder)
    drive_c = host.unix(host.drive_c)
    if not where_unix or not mods_unix or not drive_c:
        return []
    prefix = PurePosixPath(drive_c).parent
    if wine.within(mods_unix, drive_c):
        game_prefix = prefix
    else:
        steamapps = _steamapps_of_unix(mods_unix)
        app = app_of(mods_folder)
        if steamapps is None or not app:
            return []
        game_prefix = steamapps / "compatdata" / str(app) / "pfx"
    steamapps = _steamapps_of_unix(str(game_prefix))
    if steamapps is None:
        return []
    app = app_of(str(game_prefix))
    if wine.within(where_unix, drive_c) and PurePosixPath(host.real(str(game_prefix))) != prefix:
        return [tr(
            "The Studio runs in another Wine prefix than the game, and its library is on "
            "that prefix's C: drive, which the game doesn't have: the mods linked from it "
            "would be missing in game. Start the Studio in the game's prefix (with "
            "protontricks-launch --appid %(app)s), or move the library into your home folder.",
            app=app,
        )]
    # Every Steam install there is reason to think of, each read where Wine
    # puts it, and the libraries each lists.
    roots: List[str] = [*host.steam, str(steamapps.parent)]
    if host.home:
        roots.extend(str(PurePosixPath(host.home) / hint[2:]) for hint in discovery.LINUX_STEAM_HINTS)
    roots = [host.real(root) for root in roots]
    readable = [folder for folder in (host.dos(root) for root in roots) if folder is not None]
    libraries = [host.real(found) for found in _libraries_listed(readable)]
    added = [host.real(Path(folder).as_posix()) for folder in launch_folders(readable, app)]
    shared = [host.real(host.home)] if host.home else []
    shared += roots + libraries + added
    if any(wine.within(where_unix, place) for place in shared):
        return []
    return [_unseen_line(where_unix)]


def _steamapps_of_unix(unix: str) -> Optional[PurePosixPath]:
    """:func:`steamapps_of` for a Linux path, which stays one."""
    path = PurePosixPath(unix)
    for folder in (path, *path.parents):
        if folder.name.lower() == "steamapps":
            return folder
    return None


def _libraries_listed(roots: Iterable[Path]) -> List[str]:
    """The Steam libraries each install's ``libraryfolders.vdf`` lists, as
    the Linux paths it writes."""
    found: List[str] = []
    for root in roots:
        try:
            text = (Path(root) / "steamapps" / "libraryfolders.vdf").read_text(
                encoding="utf-8", errors="replace")
        except OSError:
            continue
        found.extend(match.group(1).replace("\\\\", "\\")
                     for match in re.finditer(r'"path"\s*"([^"]+)"', text))
    return found


def _real(path: Path) -> Path:
    try:
        return Path(path).resolve()
    except OSError:
        return Path(path)
