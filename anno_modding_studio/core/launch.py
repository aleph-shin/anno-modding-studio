"""Starting the game, and knowing when it is already running.

Play makes a loadout live and then starts the game.  Two things here make
that safe and make it work.

**Refuse while the game runs.**  Both games read their mods once, at start-up.
Swapping links and profiles under a running game changes nothing it has
loaded, and leaves the folder describing a game that is not the one being
played -- so the fourth card would say "matches" about something false.

**Start it through the store, not the executable.**  Both games ship behind
their launcher's DRM, which routinely refuses a direct start of the exe.  A
Steam install is started by ``steam://rungameid/<appid>``, with the app id
read from the ``appmanifest`` beside the install rather than hard-coded (it
is the one file that says which app a folder is); a Ubisoft Connect install
by ``uplay://launch/<id>/0``, with the id read from the launcher's own
registry entry for that folder.  Which store it is, the install path says.
"""

from __future__ import annotations

import functools
import os
import re
import subprocess
from pathlib import Path
from typing import List, Optional

from . import discovery, disk, games


def running(game: str, listed: Optional[List[str]] = None) -> bool:
    """Whether ``game`` is running now.  ``listed`` is the process names, for
    a test; otherwise Windows is asked.  Unknown is "no": a Play refused on a
    guess would be a Play that never works."""
    exe = _exe(game).lower()
    if not exe:
        return False
    if listed is None:
        listed = _processes()
    return any(name.strip().lower() == exe for name in listed)


def _processes() -> List[str]:
    if os.name != "nt":
        return []
    # Windows' own list of processes, a few milliseconds: the window asks
    # every few seconds, to grey Play while the game is open, and a
    # tasklist started each time would be a program started each time.
    listed = _snapshot()
    if listed is not None:
        return listed
    # By its full path: Windows looks for a bare name in the program's own
    # folder and the working folder before System32, so a tasklist.exe left
    # in either would have run at every Play.
    tasklist = os.path.join(os.environ.get("SYSTEMROOT", r"C:\Windows"), "System32", "tasklist.exe")
    try:
        out = subprocess.run(
            [tasklist, "/FO", "CSV", "/NH"],
            capture_output=True, text=True, timeout=10,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    return [line.split('","')[0].strip('"') for line in out.splitlines() if line.startswith('"')]


def _snapshot() -> Optional[List[str]]:
    """The program names of the processes running now, from a Toolhelp
    snapshot; None where it could not be taken."""
    try:
        import ctypes
        from ctypes import wintypes

        kernel32, Entry = _toolhelp()
        snapshot = kernel32.CreateToolhelp32Snapshot(0x2, 0)  # TH32CS_SNAPPROCESS
        if not snapshot or snapshot == wintypes.HANDLE(-1).value:
            return None
        try:
            entry = Entry(dwSize=ctypes.sizeof(Entry))
            names = []
            more = kernel32.Process32FirstW(snapshot, ctypes.byref(entry))
            while more:
                names.append(entry.szExeFile)
                more = kernel32.Process32NextW(snapshot, ctypes.byref(entry))
            return names
        finally:
            kernel32.CloseHandle(snapshot)
    except (OSError, AttributeError, ValueError):
        return None


@functools.lru_cache(maxsize=1)
def _toolhelp():
    """kernel32's Toolhelp calls, typed, and the entry they fill."""
    import ctypes
    from ctypes import wintypes

    class Entry(ctypes.Structure):
        _fields_ = [
            ("dwSize", wintypes.DWORD),
            ("cntUsage", wintypes.DWORD),
            ("th32ProcessID", wintypes.DWORD),
            ("th32DefaultHeapID", ctypes.c_size_t),
            ("th32ModuleID", wintypes.DWORD),
            ("cntThreads", wintypes.DWORD),
            ("th32ParentProcessID", wintypes.DWORD),
            ("pcPriClassBase", wintypes.LONG),
            ("dwFlags", wintypes.DWORD),
            ("szExeFile", wintypes.WCHAR * 260),
        ]

    # A library of its own, so the argument types set here are set for
    # nobody else's calls into kernel32.
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    for step in (kernel32.Process32FirstW, kernel32.Process32NextW):
        step.argtypes = [wintypes.HANDLE, ctypes.POINTER(Entry)]
        step.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    return kernel32, Entry


def install_for(game: str, known=None) -> Optional[Path]:
    """The game's installation: ``known`` when it is the game's (the folder
    its files were read from), else the one Steam or Ubisoft Connect has."""
    if known and is_install_of(Path(known), game):
        return Path(known)
    for found in discovery.find_game_installs():
        if is_install_of(found.path, game):
            return found.path
    return None


def _exe(game: str) -> str:
    found = games.GAMES.get(game)
    return found.exe if found else ""


def is_install_of(folder: Path, game: str) -> bool:
    """Whether ``folder`` is an installation of ``game``: it holds the
    program the game runs as."""
    exe = _exe(game)
    return bool(exe) and (folder / "Bin" / "Win64" / exe).is_file()


def steam_app_id(install: Path) -> int:
    """The Steam app whose files are ``install``, or 0: the ``appmanifest``
    in the ``steamapps`` folder above it whose ``installdir`` is its name."""
    install = Path(install)
    steamapps = install.parent.parent
    if install.parent.name.lower() != "common" or steamapps.name.lower() != "steamapps":
        return 0
    try:
        manifests = list(steamapps.glob("appmanifest_*.acf"))
    except OSError:
        return 0
    for manifest in manifests:
        try:
            text = manifest.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        folder = re.search(r'"installdir"\s*"([^"]*)"', text)
        app = re.search(r'"appid"\s*"(\d+)"', text)
        if folder and app and folder.group(1).lower() == install.name.lower():
            return int(app.group(1))
    return 0


def ubisoft_game_id(install: Path) -> str:
    """Ubisoft Connect's id for the game installed at ``install``, or "":
    the launcher's registry entry whose ``InstallDir`` is that folder."""
    for game_id, folder in discovery.ubisoft_installs():
        if disk.same_file(folder, install):
            return game_id
    return ""


def uri(install) -> str:
    """How to start the game installed at ``install``, through its store; ""
    when neither store says it is theirs."""
    if install is None:
        return ""
    app = steam_app_id(Path(install))
    if app:
        return "steam://rungameid/%d" % app
    game_id = ubisoft_game_id(Path(install))
    if game_id:
        return "uplay://launch/%s/0" % game_id
    return ""


def start(address: str) -> bool:
    """Start the game from ``address``: a store's address, handed to Windows,
    which gives it to the store; or a program, a shortcut or a launcher the
    player chose instead.  A program starts in its own folder, as it would
    from Explorer -- the games find their data relative to where they run."""
    if not address or os.name != "nt":
        return False
    try:
        if "://" not in address and Path(address).suffix.lower() == ".exe":
            subprocess.Popen(
                [address], cwd=str(Path(address).parent),
                creationflags=getattr(subprocess, "DETACHED_PROCESS", 0),
            )
        else:
            os.startfile(address)
    except OSError:
        return False
    return True
