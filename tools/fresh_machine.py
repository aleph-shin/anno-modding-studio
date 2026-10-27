"""Put this machine back to before the Studio, to test a first run on it.

Takes away everything the Studio keeps -- its folders under Documents and the
local app data, the one folder ``ANNO_STUDIO_FOLDER`` names or one called
``AnnoModdingStudioData`` beside a source checkout, a library kept wherever the
settings or ``ANNO_STUDIO_LIBRARY`` put it, the settings file, the registry
keys of the builds that kept their settings there, and every ``ANNO_STUDIO_*``
variable -- and leaves the games' mods folders in one of two starting states:

``clean``
    As if this machine had never been modded: every mods folder emptied, but
    for what the game itself ships there (``.ubi``, ``how-to.txt``).

``mods``
    Mods installed the usual way, by hand, with the Studio never run: each mods
    folder emptied, then filled with a copy of what ``--mods-from`` holds for
    it, which is a folder laid out as::

        mods_1800_main\\mods        the mods folder in Anno 1800's installation
        mods_117_main\\mods         the mods folder in Anno 117's installation
        mods_117_documents\\mods    Documents\\Anno 117 - Pax Romana\\mods

    A folder it has nothing for is left as ``clean`` leaves it.  Copied, never
    linked: a test that writes into a mod must not write into the backup.

Neither touches the games' own mod.io download cache.

It only says what it would do until it is given ``--go``.  And it moves what
it takes away into ``--archive`` (by default ``%USERPROFILE%\\AnnoModdingStudio-
reset``), one folder per run with ``moved.json`` saying where each thing came
from -- registry keys exported to ``.reg`` and variables written down beside
them -- unless it is given ``--delete``.  The first run on a machine where the
Studio has been used holds loadouts, backups and a library that may be the
only copy of something: archive that one.

Links are taken away as links.  Every junction in a mods folder is removed
before anything else moves, and every link found inside a folder that is about
to move or go is removed first, so nothing is ever followed into what it
points at.  It stops before doing anything while a Studio or a game is
running, and never takes away a folder holding ``--mods-from``, the archive,
or this checkout.

    python tools/fresh_machine.py clean                       # what it would do
    python tools/fresh_machine.py clean --go
    python tools/fresh_machine.py mods --mods-from DIR --go
    python tools/fresh_machine.py mods --mods-from DIR --go --delete

Windows only, standard library only: it does not import the Studio, so it
runs the same whichever version is being tested.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import shutil
import stat
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Set

ROOT = Path(__file__).resolve().parent.parent

NAME = "AnnoModdingStudio"
# What the Studio was called before, whose settings may still be about.
OLD_NAME = "AnnoModOpStudio"
BESIDE_NAME = "AnnoModdingStudioData"
ENV_PREFIX = "ANNO_STUDIO_"

# What the Studio keeps in a folder it was given, rather than one of its own:
# only these are taken from it, and anything else there is said and left.
ONE_FOLDER_ENTRIES = {
    "loadouts", "exports", "backups", "extracted", "cache", "library", "modio",
    "settings.ini", "open.lock", "census.lock", "data-version.json",
    "collection-probe-findings.json",
}
LIBRARY_ENTRIES = {"anno117", "anno1800", "generated"}

# What the games put in their mods folders themselves, kept by ``clean``.
GAME_OWN = {".ubi", "how-to.txt"}

GAMES = {
    "anno1800": {"install": "Anno 1800", "exe": "Anno1800.exe", "documents": "Anno 1800"},
    "anno117": {"install": "Anno 117 - Pax Romana", "exe": "Anno117.exe",
                "documents": "Anno 117 - Pax Romana"},
}
PROCESSES = {"anno1800.exe", "anno117.exe", "annomoddingstudio.exe"}

# Each mods folder, and the folder of --mods-from that fills it.
BACKUP_LAYOUT = {
    "anno1800-install": "mods_1800_main",
    "anno117-install": "mods_117_main",
    "anno117-documents": "mods_117_documents",
}

USER_ENV = r"Environment"
MACHINE_ENV = r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment"


# -- long paths -------------------------------------------------------------------------
def extended(path) -> str:
    """``path`` as Windows reads it past MAX_PATH: absolute, with the ``\\\\?\\``
    prefix.  The archive keeps a folder's whole path under its own, which
    doubles the depth, and a mod nested eight deep in the library passed
    260 characters there: the move fell back to copying, failed with WinError
    206, and left the local folder behind (R3-0, hands-on run 3)."""
    text = str(path)
    if os.name != "nt" or text.startswith("\\\\?\\"):
        return text
    text = os.path.abspath(text)
    return "\\\\?\\UNC\\" + text[2:] if text.startswith("\\\\") else "\\\\?\\" + text


def plain(text: str) -> str:
    """A path :func:`extended` made, as it is said."""
    if text.startswith("\\\\?\\UNC\\"):
        return "\\\\" + text[8:]
    return text[4:] if text.startswith("\\\\?\\") else text


# -- links ------------------------------------------------------------------------------
def is_link(path) -> bool:
    """A junction or a symbolic link, asked of the entry itself."""
    try:
        found = os.lstat(extended(path))
    except (OSError, ValueError):
        return False
    if stat.S_ISLNK(found.st_mode):
        return True
    tag = getattr(stat, "IO_REPARSE_TAG_MOUNT_POINT", None)
    return tag is not None and getattr(found, "st_reparse_tag", 0) == tag


def link_target(path) -> str:
    try:
        raw = os.readlink(extended(path))
    except OSError:
        return "?"
    return raw[4:] if raw.startswith("\\\\?\\") else raw


def remove_link(path) -> None:
    """The link alone: a folder's link goes with rmdir, which never touches
    what it points at; a file's with unlink."""
    path = extended(path)
    try:
        os.rmdir(path)
    except NotADirectoryError:
        os.unlink(path)
    except OSError:
        if os.path.isdir(path):
            raise
        os.unlink(path)


def links_inside(root: Path) -> List[Path]:
    """Every link under ``root``, not walking into any of them -- however
    deep: a folder past MAX_PATH not looked in would keep its links for the
    move to take, or the delete to follow."""
    found: List[Path] = []
    stack = [extended(root)]
    while stack:
        folder = stack.pop()
        try:
            entries = list(os.scandir(folder))
        except OSError:
            continue
        for entry in entries:
            if is_link(entry.path):
                found.append(Path(plain(entry.path)))
            elif entry.is_dir(follow_symlinks=False):
                stack.append(entry.path)
    return found


# -- the machine ------------------------------------------------------------------------
def known_documents() -> Path:
    """Documents as Windows has it, which is where the games look -- moved by
    OneDrive or a policy, perhaps.  The Studio itself uses ``~/Documents``."""
    try:
        import ctypes
        from ctypes import wintypes

        class GUID(ctypes.Structure):
            _fields_ = [("a", wintypes.DWORD), ("b", wintypes.WORD), ("c", wintypes.WORD),
                        ("d", ctypes.c_ubyte * 8)]

        documents = GUID(0xFDD39AD0, 0x238F, 0x46AF,
                         (ctypes.c_ubyte * 8)(0xAD, 0xB4, 0x6C, 0x85, 0x48, 0x03, 0x69, 0xC7))
        out = ctypes.c_wchar_p()
        if ctypes.windll.shell32.SHGetKnownFolderPath(ctypes.byref(documents), 0, None,
                                                       ctypes.byref(out)) == 0:
            value = out.value
            ctypes.windll.ole32.CoTaskMemFree(out)
            if value:
                return Path(value)
    except (OSError, AttributeError, ValueError):
        pass
    return Path.home() / "Documents"


def _registry_values(hive, key: str) -> Dict[str, str]:
    import winreg

    found: Dict[str, str] = {}
    try:
        with winreg.OpenKey(hive, key) as handle:
            index = 0
            while True:
                try:
                    name, value, _kind = winreg.EnumValue(handle, index)
                except OSError:
                    break
                found[name] = str(value)
                index += 1
    except OSError:
        pass
    return found


def studio_variables() -> Dict[str, Dict[str, str]]:
    """Every ``ANNO_STUDIO_*`` variable, by where it is set: this process,
    the user's, the machine's."""
    import winreg

    def ours(values) -> Dict[str, str]:
        return {name.upper(): value for name, value in values.items()
                if name.upper().startswith(ENV_PREFIX)}

    return {
        "process": ours(dict(os.environ)),
        "user": ours(_registry_values(winreg.HKEY_CURRENT_USER, USER_ENV)),
        "machine": ours(_registry_values(winreg.HKEY_LOCAL_MACHINE, MACHINE_ENV)),
    }


def values_of(variables: Dict[str, Dict[str, str]], name: str) -> List[str]:
    """Each value ``name`` has anywhere, once, expanded."""
    found: List[str] = []
    for scope in ("process", "user", "machine"):
        value = os.path.expandvars(variables[scope].get(name, "")).strip()
        if value and value.lower() not in [seen.lower() for seen in found]:
            found.append(value)
    return found


def read_ini(path: Path) -> Dict[str, str]:
    """A QSettings INI file's keys, unescaped as far as paths need: ``%3A``
    for a colon in a key, ``\\\\`` for a backslash, quotes around a value."""
    found: Dict[str, str] = {}
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return found
    for line in text.splitlines():
        if "=" not in line or line.startswith(("[", ";", "#")):
            continue
        key, _, value = line.partition("=")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] == '"':
            value = value[1:-1]
        found[key.strip().replace("%3A", ":")] = value.replace("\\\\", "\\")
    return found


def steam_libraries() -> List[Path]:
    import winreg

    roots: List[Path] = []
    for hive, key, value in (
        (winreg.HKEY_CURRENT_USER, r"SOFTWARE\Valve\Steam", "SteamPath"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Valve\Steam", "InstallPath"),
    ):
        named = _registry_values(hive, key).get(value)
        if named:
            roots.append(Path(named))
    found: List[Path] = []
    for steam in roots:
        found.append(steam)
        vdf = steam / "steamapps" / "libraryfolders.vdf"
        try:
            for line in vdf.read_text(encoding="utf-8", errors="replace").splitlines():
                parts = line.strip().split('"')
                if len(parts) >= 4 and parts[1].lower() == "path":
                    found.append(Path(parts[3].replace("\\\\", "\\")))
        except OSError:
            pass
    return found


def ubisoft_installs() -> List[Path]:
    import winreg

    found: List[Path] = []
    key = r"SOFTWARE\WOW6432Node\Ubisoft\Launcher\Installs"
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, key) as root:
            for index in range(winreg.QueryInfoKey(root)[0]):
                with winreg.OpenKey(root, winreg.EnumKey(root, index)) as sub, \
                        contextlib.suppress(OSError):
                    found.append(Path(winreg.QueryValueEx(sub, "InstallDir")[0]))
    except OSError:
        pass
    return found


def find_game(game: str, given: Optional[str], settings: Iterable[Dict[str, str]]) -> Optional[Path]:
    """The game's installation: as given, else Steam's, Ubisoft Connect's, or
    the one the Studio last remembered -- the first that is there."""
    if given:
        return Path(given)
    folder = GAMES[game]["install"]
    candidates = [library / "steamapps" / "common" / folder for library in steam_libraries()]
    candidates += [path for path in ubisoft_installs() if path.name.lower() == folder.lower()]
    candidates += [Path(remembered["last_game_folder:" + game]) for remembered in settings
                   if remembered.get("last_game_folder:" + game)]
    for candidate in candidates:
        if (candidate / "Bin").is_dir() or (candidate / "maindata").is_dir():
            return candidate
    return None


def studio_keys() -> List[str]:
    """The Studio's keys under HKEY_CURRENT_USER: where builds that kept
    their settings in the registry kept them, under either name."""
    import winreg

    found = []
    for name in (NAME, OLD_NAME):
        key = "Software\\" + name
        try:
            winreg.CloseKey(winreg.OpenKey(winreg.HKEY_CURRENT_USER, key))
            found.append(key)
        except OSError:
            pass
    return found


# The byte each lock file has locked while it is held: an open Studio's
# (anno_modding_studio/ui/one_studio.py), a census run's (RunLock in
# tools/modio_roundtrip.py).  Copied, not imported, so tests/test_fresh_machine.py
# holds the real locks to keep these in step.  A .lock of another name is tried
# at each of them.
LOCKED_BYTE = {"open.lock": 0, "census.lock": 1 << 30}


def held_locks(folders: Iterable[Path]) -> List[Path]:
    """The Studio's lock files in ``folders`` that something holds: a Studio,
    or a census run, is working in them."""
    import msvcrt

    held: List[Path] = []
    for folder in folders:
        for lock in folder.glob("*.lock") if folder.is_dir() else ():
            known = LOCKED_BYTE.get(lock.name.lower())
            offsets = sorted(set(LOCKED_BYTE.values())) if known is None else [known]
            try:
                with open(lock, "a+b") as handle:
                    for offset in offsets:
                        handle.seek(offset)
                        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                        handle.seek(offset)
                        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            except OSError:
                held.append(lock)
    return held


def running(names: Set[str]) -> List[str]:
    try:
        listed = subprocess.run(["tasklist", "/FO", "CSV", "/NH"], capture_output=True,
                                text=True, check=False).stdout
    except OSError:
        return []
    found = set()
    for line in listed.splitlines():
        name = line.split('","')[0].strip('"').lower()
        if name in names:
            found.add(name)
    return sorted(found)


# -- the plan ---------------------------------------------------------------------------
@dataclass
class Plan:
    archive: Optional[Path]
    protected: List[Path]
    take: List[tuple] = field(default_factory=list)       # (path, why)
    links: List[tuple] = field(default_factory=list)      # (link, why)
    fill: List[tuple] = field(default_factory=list)       # (from, into)
    keys: List[str] = field(default_factory=list)         # HKCU\Software\...
    variables: Dict[str, Dict[str, str]] = field(default_factory=dict)
    left: List[str] = field(default_factory=list)         # said, not done
    stop: List[str] = field(default_factory=list)         # why it cannot go

    def guard(self, path: Path) -> bool:
        """Whether ``path`` may go: not a folder holding anything protected,
        nor a machine folder rather than one of the Studio's."""
        real = _norm(path)
        for kept in self.protected:
            kept_real = _norm(kept)
            if kept_real == real or kept_real.startswith(real.rstrip("\\") + "\\"):
                self.left.append("%s: holds %s, so it is left" % (path, kept))
                return False
        machine = {_norm(Path.home()), _norm(Path.home() / "Documents"), _norm(known_documents()),
                   _norm(Path(os.environ.get("LOCALAPPDATA", ""))),
                   _norm(Path(os.environ.get("APPDATA", "")))}
        if real in machine or len(Path(real).parts) <= 1:
            self.left.append("%s: not a folder of the Studio's own, so it is left" % path)
            return False
        return True

    def covers(self, path: Path) -> bool:
        """Whether ``path`` is, or is inside, something already going."""
        return any(_norm(path) == _norm(seen) or _norm(path).startswith(_norm(seen) + "\\")
                   for seen, _ in self.take)

    def add(self, path: Path, why: str) -> None:
        if not (path.exists() or is_link(path)):
            return
        if self.covers(path):
            return
        if self.guard(path):
            self.take = [(seen, said) for seen, said in self.take
                         if not _norm(seen).startswith(_norm(path) + "\\")]
            self.take.append((path, why))

    def add_known(self, folder: Path, entries: Set[str], why: str) -> None:
        """Only what the Studio keeps there, from a folder it was given."""
        if not folder.is_dir():
            return
        others = []
        for entry in sorted(folder.iterdir()):
            if entry.name.lower() in entries:
                self.add(entry, why)
            else:
                others.append(entry.name)
        if others:
            self.left.append("%s: not the Studio's, left: %s" % (folder, ", ".join(others)))


def _norm(path: Path) -> str:
    return os.path.normcase(os.path.abspath(str(path))).rstrip("\\")


def source_checkouts() -> List[Path]:
    """This checkout and every worktree of it: a Studio run from any of them
    keeps everything in ``AnnoModdingStudioData`` beside it, where there is one."""
    found = [ROOT]
    try:
        listed = subprocess.run(["git", "-C", str(ROOT), "worktree", "list", "--porcelain"],
                                capture_output=True, text=True, check=False).stdout
    except OSError:
        listed = ""
    for line in listed.splitlines():
        if line.startswith("worktree "):
            path = Path(line[len("worktree "):].strip())
            if _norm(path) not in [_norm(seen) for seen in found]:
                found.append(path)
    return found


def make_plan(args) -> Plan:
    archive = None if args.delete else Path(args.archive)
    protected = [ROOT, Path(__file__).resolve()] + [Path(path) for path in args.keep]
    if archive is not None:
        protected.append(archive)
    if args.mods_from:
        protected.append(Path(args.mods_from))
    plan = Plan(archive=archive, protected=protected)

    variables = studio_variables()
    plan.variables = variables
    home_docs = Path.home() / "Documents"
    local_app = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
    roaming = Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming")

    one_folders = [Path(value) for value in values_of(variables, "ANNO_STUDIO_FOLDER")
                   if value.lower() != "none"]
    one_folders += [checkout / BESIDE_NAME for checkout in source_checkouts()
                    if (checkout / BESIDE_NAME).is_dir()]

    # The settings first: they say where the library and the games are.
    # Beside the library now; under the roaming app data for the builds before.
    settings_files = [local_app / NAME / "settings.ini",
                      roaming / NAME / (NAME + ".ini"), roaming / OLD_NAME / (OLD_NAME + ".ini")]
    settings_files += [folder / "settings.ini" for folder in one_folders]
    settings_files += [Path(value) for value in values_of(variables, "ANNO_STUDIO_SETTINGS")]
    settings = [read_ini(path) for path in settings_files if path.is_file()]

    locals_ = [local_app / NAME, *one_folders]
    held = held_locks(locals_)
    if held:
        plan.stop.append("a Studio or a census run is working: %s is held"
                         % ", ".join(str(lock) for lock in held))
    busy = running(PROCESSES)
    if busy:
        plan.stop.append("close %s first" % ", ".join(busy))

    # -- the games' mods folders ----------------------------------------------
    documents = known_documents()
    mods_folders: Dict[str, Path] = {}
    for game, given in (("anno1800", args.anno1800), ("anno117", args.anno117)):
        installed = find_game(game, given, settings)
        if installed is None:
            plan.left.append("%s: no installation found; pass --%s" % (game, game))
            continue
        mods_folders[game + "-install"] = installed / "mods"
    # Where the game looks; ~/Documents too, where that is somewhere else and
    # the folder is there instead.
    candidates = [docs / GAMES["anno117"]["documents"] / "mods" for docs in (documents, home_docs)]
    mods_folders["anno117-documents"] = next((path for path in candidates if path.is_dir()),
                                             candidates[0])

    libraries = [Path(value) for value in values_of(variables, "ANNO_STUDIO_LIBRARY")]
    libraries += [Path(found["library_dir"]) for found in settings if found.get("library_dir")]
    libraries += [local / "library" for local in locals_]
    # Every junction the libraries made, wherever: links.json says where.
    linked_folders: Dict[str, Path] = {}
    for library in libraries:
        for links in library.glob("*/links.json") if library.is_dir() else ():
            try:
                held_links = json.loads(links.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            # Stamped records keep them under "entries"; older ones were the dict.
            entries = held_links.get("entries", held_links) if isinstance(held_links, dict) else {}
            for entry in (entries.values() if isinstance(entries, dict) else ()):
                if isinstance(entry, dict) and entry.get("mods_folder"):
                    folder = Path(entry["mods_folder"])
                    linked_folders.setdefault(_norm(folder), folder)

    studio_places = [_norm(path) for path in libraries + locals_]
    for label, folder in mods_folders.items():
        for entry in sorted(folder.iterdir()) if folder.is_dir() else ():
            if is_link(entry):
                plan.links.append((entry, "a link in %s, to %s" % (label, link_target(entry))))
    for folder in linked_folders.values():
        if _norm(folder) in [_norm(path) for path in mods_folders.values()]:
            continue
        for entry in sorted(folder.iterdir()) if folder.is_dir() else ():
            target = link_target(entry) if is_link(entry) else ""
            if target and any(_norm(Path(target)).startswith(place) for place in studio_places):
                plan.links.append((entry, "the library's link, to %s" % target))

    from_backup = Path(args.mods_from) if args.mods_from else None
    for label, folder in mods_folders.items():
        source = from_backup / BACKUP_LAYOUT[label] / "mods" if from_backup else None
        filling = args.state == "mods" and source is not None and source.is_dir()
        if args.state == "mods" and not filling:
            plan.left.append("%s: --mods-from has no %s, so it is left clean"
                             % (label, BACKUP_LAYOUT[label] + "\\mods"))
        linked = {_norm(link) for link, _why in plan.links}
        for entry in sorted(folder.iterdir()) if folder.is_dir() else ():
            if _norm(entry) in linked:
                continue
            if not filling and label.endswith("-install") and entry.name.lower() in GAME_OWN:
                continue
            plan.add(entry, "in the %s mods folder" % label)
        if filling:
            plan.fill.append((source, folder))

    # -- the Studio's own -----------------------------------------------------
    for value in values_of(variables, "ANNO_STUDIO_HOME"):
        plan.add(Path(value), "the folder ANNO_STUDIO_HOME names")
    for folder in one_folders:
        plan.add_known(folder, ONE_FOLDER_ENTRIES, "kept by the Studio in its one folder")
        if folder.name == BESIDE_NAME:
            plan.add(folder, "the one folder beside a source checkout")
    plan.add(home_docs / NAME, "what you make with the Studio")
    if _norm(documents) != _norm(home_docs):
        plan.add(documents / NAME, "what you make with the Studio")
    plan.add(local_app / NAME, "what the Studio keeps for itself")
    for library in libraries:
        # One under a folder already going goes with it.
        if not plan.covers(library):
            plan.add_known(library, LIBRARY_ENTRIES, "the library, where it was moved to")
    for value in values_of(variables, "ANNO_STUDIO_CACHE"):
        plan.add(Path(value), "the folder ANNO_STUDIO_CACHE names")
    for value in values_of(variables, "ANNO_STUDIO_SECRETS"):
        plan.add(Path(value), "the mod.io key ANNO_STUDIO_SECRETS names")
    for value in values_of(variables, "ANNO_STUDIO_SETTINGS"):
        plan.add(Path(value), "the settings file ANNO_STUDIO_SETTINGS names")
    plan.add(roaming / NAME, "the settings of the builds that kept them there")
    plan.add(roaming / OLD_NAME, "the settings of the Studio's former name")
    if args.temp:
        temp = Path(os.environ.get("TEMP") or local_app / "Temp")
        count = 0
        for entry in temp.glob("ams-*") if temp.is_dir() else ():
            if entry.is_dir():
                plan.add(entry, "a working folder in %TEMP%")
                count += 1
        if not count:
            plan.left.append("no ams-* working folders in %TEMP%")

    plan.keys = studio_keys()
    if variables["machine"]:
        plan.left.append("machine-wide %s: set for every user, so an administrator "
                         "removes them (System Properties > Environment Variables)"
                         % ", ".join(sorted(variables["machine"])))
    return plan


def describe(plan: Plan, args) -> str:
    lines = []
    going = "deleted" if plan.archive is None else "moved into %s" % plan.archive
    lines.append("Starting state: %s" % args.state)
    if plan.links:
        lines.append("\nLinks removed (only the link, never what it points at):")
        lines += ["  %s  [%s]" % (link, why) for link, why in plan.links]
    if plan.take:
        lines.append("\nTaken away, %s:" % going)
        lines += ["  %s  [%s]" % (path, why) for path, why in plan.take]
    if plan.keys:
        lines.append("\nRegistry keys removed%s:" % ("" if plan.archive is None else ", exported first"))
        lines += ["  HKEY_CURRENT_USER\\%s" % key for key in plan.keys]
    if plan.variables["user"]:
        lines.append("\nUser variables removed:")
        lines += ["  %s=%s" % item for item in sorted(plan.variables["user"].items())]
    if plan.variables["process"]:
        lines.append("\nSet in this shell only (they go when it closes; a Studio "
                     "started from it would still see them):")
        lines += ["  %s=%s" % item for item in sorted(plan.variables["process"].items())]
    if plan.fill:
        lines.append("\nThen copied in:")
        lines += ["  %s  ->  %s" % pair for pair in plan.fill]
    if plan.left:
        lines.append("\nLeft as they are:")
        lines += ["  " + line for line in plan.left]
    if not (plan.links or plan.take or plan.keys or plan.variables["user"] or plan.fill):
        lines.append("\nNothing of the Studio's was found.")
    return "\n".join(lines)


# -- doing it ---------------------------------------------------------------------------
def _writable(function, path, _info) -> None:
    os.chmod(path, stat.S_IWRITE)
    function(path)


def take_away(path: Path, into: Optional[Path], moved: List[dict]) -> None:
    """Move ``path`` into the archive, or delete it, its links removed first."""
    if is_link(path):
        moved.append({"link": str(path), "to": link_target(path)})
        remove_link(path)
        return
    if path.is_dir():
        for link in links_inside(path):
            moved.append({"link": str(link), "to": link_target(link)})
            remove_link(link)
    if into is None:
        if path.is_dir():
            if sys.version_info >= (3, 12):
                shutil.rmtree(extended(path), onexc=_writable)
            else:
                shutil.rmtree(extended(path), onerror=_writable)
        else:
            os.chmod(extended(path), stat.S_IWRITE)
            os.unlink(extended(path))
        moved.append({"deleted": str(path)})
        return
    # The archive keeps the drive and path, so two folders of one name never meet.
    target = into / (path.drive.rstrip(":") or "unc") / Path(*path.parts[1:])
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(extended(path), extended(target))
    moved.append({"from": str(path), "to": str(target)})


def go(plan: Plan) -> int:
    stamp = time.strftime("%Y-%m-%d_%H-%M-%S")
    into = plan.archive / stamp if plan.archive is not None else None
    if into is not None:
        into.mkdir(parents=True, exist_ok=False)
    moved: List[dict] = []
    failed: List[str] = []

    def record() -> None:
        if into is not None:
            (into / "moved.json").write_text(json.dumps(moved, indent=2), encoding="utf-8")

    for link, _why in plan.links:
        try:
            moved.append({"link": str(link), "to": link_target(link)})
            remove_link(link)
        except OSError as exc:
            failed.append("%s: %s" % (link, exc))
    for path, _why in plan.take:
        try:
            take_away(path, into, moved)
        except OSError as exc:
            failed.append("%s: %s" % (path, exc))
        record()
    for key in plan.keys:
        if into is not None:
            exported = into / (key.replace("\\", "_") + ".reg")
            subprocess.run(["reg", "export", "HKCU\\" + key, str(exported), "/y"],
                           capture_output=True, check=False)
        done = subprocess.run(["reg", "delete", "HKCU\\" + key, "/f"], capture_output=True,
                              text=True, check=False)
        if done.returncode:
            failed.append("HKCU\\%s: %s" % (key, done.stderr.strip()))
        else:
            moved.append({"registry": "HKCU\\" + key})
    for name, value in sorted(plan.variables["user"].items()):
        # Through .NET, which also tells running programs the variables changed.
        done = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "[Environment]::SetEnvironmentVariable('%s', $null, 'User')" % name],
            capture_output=True, text=True, check=False,
        )
        if done.returncode:
            failed.append("%s: %s" % (name, done.stderr.strip()))
        else:
            moved.append({"variable": name, "was": value})
    record()
    for source, folder in plan.fill:
        folder.mkdir(parents=True, exist_ok=True)
        entries = sorted(source.iterdir())
        print("Copying %d entries into %s" % (len(entries), folder), flush=True)
        for entry in entries:
            try:
                if entry.is_dir():
                    shutil.copytree(entry, folder / entry.name, symlinks=True)
                else:
                    shutil.copy2(entry, folder / entry.name)
            except OSError as exc:
                failed.append("%s: %s" % (entry, exc))
    record()
    kept = "" if into is None else "  What was taken away is in %s" % into
    if failed:
        # Not "Done.": what is listed is still where it was.
        print("\nNot all of it went." + kept)
        print("\nThese did not go:")
        print("\n".join("  " + line for line in failed))
        return 1
    print("\nDone." + kept)
    return 0


def main(argv=None) -> int:
    if os.name != "nt":
        print("Windows only.")
        return 2
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("state", choices=("clean", "mods"),
                        help="clean: no mods at all; mods: --mods-from copied into each mods folder")
    parser.add_argument("--mods-from", help="the folder of mods folders to start from (state mods)")
    parser.add_argument("--go", action="store_true", help="do it; without, it only says what it would do")
    parser.add_argument("--delete", action="store_true", help="delete rather than archive")
    parser.add_argument("--archive", default=str(Path.home() / (NAME + "-reset")),
                        help="where what is taken away is moved (default: %(default)s)")
    parser.add_argument("--anno117", help="Anno 117's installation, where it is not found")
    parser.add_argument("--anno1800", help="Anno 1800's installation, where it is not found")
    parser.add_argument("--temp", action="store_true",
                        help="also the ams-* working folders in %%TEMP%% (the test suite leaves many)")
    parser.add_argument("--keep", action="append", default=[], metavar="DIR",
                        help="never take away this folder or one holding it (repeatable)")
    args = parser.parse_args(argv)
    if args.state == "mods" and not args.mods_from:
        parser.error("state mods needs --mods-from")
    if args.mods_from and not Path(args.mods_from).is_dir():
        parser.error("--mods-from %s is not a folder" % args.mods_from)

    plan = make_plan(args)
    print(describe(plan, args))
    if plan.stop:
        print("\nStopped: " + "; ".join(plan.stop))
        return 1
    if not args.go:
        print("\nNothing was changed. Run again with --go to do it.")
        return 0
    return go(plan)


if __name__ == "__main__":
    sys.exit(main())
