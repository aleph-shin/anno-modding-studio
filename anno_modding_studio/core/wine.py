"""Whether the Studio runs under Wine, and what that Wine can do.

Somebody playing on Linux through Proton can run the built Studio in the
game's own prefix, where the game's Documents, its mod.io files and its
mods folder are where the Studio looks for them.  ``os.name`` there is
``"nt"``: only Wine's own ntdll says it is Wine, by having
``wine_get_version``.

**Junctions.**  The Studio links each library mod into the mods folder with
a junction (:mod:`core.library`).  Wine makes them from 10.19 on, the version
Proton 11 is built on: the link is kept as an empty folder whose name ends in
``?``, with the target in an extended attribute, and only a Wine that new
follows it.  An older one refuses to make it, and a game run on one sees an
empty folder where the mod should be.  So a Studio under an older Wine says
so as it starts, and again when a link cannot be made.

**The Linux side.**  Wine names a Linux folder ``Z:\\...`` and keeps the
Linux home in ``WINE_HOST_HOME``; ``wine_get_unix_file_name`` and
``wine_get_dos_file_name`` turn one kind of path into the other, through
every link on the way.  :mod:`core.proton` asks these whether the game's
container can see the library.

Worked out from Wine 11.0's source.  None of it has been tried under Wine yet.
"""

from __future__ import annotations

import functools
import os
import re
import sys
from pathlib import Path, PurePosixPath
from typing import List, Optional, Tuple

from .i18n import tr

# The first Wine that makes and follows junctions.
JUNCTIONS_SINCE = (10, 19)


@functools.lru_cache(maxsize=1)
def version() -> str:
    """Wine's version, as its ntdll says it, or "" where this is not Wine."""
    if sys.platform != "win32":
        return ""
    try:
        import ctypes

        asked = ctypes.WinDLL("ntdll").wine_get_version
        asked.argtypes = []
        asked.restype = ctypes.c_char_p
        said = asked()
    except (OSError, AttributeError):
        return ""
    return said.decode("ascii", "replace").strip() if said else ""


def numbers(text: str) -> Optional[Tuple[int, int]]:
    """The major and minor version in ``text`` ("10.0 (Staging)" is 10, 0),
    or None where it names none."""
    found = re.match(r"\s*(\d+)\.(\d+)", text or "")
    return (int(found.group(1)), int(found.group(2))) if found else None


def makes_junctions(text: Optional[str] = None) -> bool:
    """Whether the Wine of ``text`` (this one's where None) makes junctions.
    True off Wine, and for a version that cannot be read: a guess says
    nothing."""
    text = version() if text is None else text
    if not text:
        return True
    found = numbers(text)
    return found is None or found >= JUNCTIONS_SINCE


def junctions_note(text: Optional[str] = None) -> str:
    """What to say where this Wine makes no junctions, or ""."""
    text = version() if text is None else text
    if makes_junctions(text):
        return ""
    return tr(
        "The Studio is running under Wine %(version)s, which can't make the links the "
        "Studio puts in a mods folder. Installing a mod, moving your mods into the "
        "library and switching mods with Play will fail. Wine makes them from version "
        "10.19 on, which Proton 11 is built on. Run the Studio and the game with "
        "Proton 11 or newer.", version=text,
    )


# -- the Linux side -----------------------------------------------------------------
@functools.lru_cache(maxsize=1)
def _kernel32():
    """kernel32 with Wine's two path calls and what frees their answers, typed."""
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.wine_get_unix_file_name.argtypes = [wintypes.LPCWSTR]
    kernel32.wine_get_unix_file_name.restype = ctypes.c_void_p
    kernel32.wine_get_dos_file_name.argtypes = [ctypes.c_char_p]
    kernel32.wine_get_dos_file_name.restype = ctypes.c_void_p
    kernel32.GetProcessHeap.argtypes = []
    kernel32.GetProcessHeap.restype = wintypes.HANDLE
    kernel32.HeapFree.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.c_void_p]
    kernel32.HeapFree.restype = wintypes.BOOL
    return kernel32


class Host:
    """The Linux side of the Wine the Studio runs under: the home folder, the
    Steam install Proton named, this prefix's C: drive, and the two ways
    across.  :func:`host` makes the real one; a test makes its own."""

    def __init__(self, home: str, steam: List[str], drive_c: Path):
        self.home = home
        self.steam = steam
        self.drive_c = drive_c

    def unix(self, path) -> str:
        """``path``'s Linux path, through every link on the way, or ""."""
        try:
            import ctypes

            kernel32 = _kernel32()
            answer = kernel32.wine_get_unix_file_name(str(path))
            if not answer:
                return ""
            try:
                return ctypes.string_at(answer).decode("utf-8", "surrogateescape")
            finally:
                kernel32.HeapFree(kernel32.GetProcessHeap(), 0, answer)
        except (OSError, AttributeError, ValueError):
            return ""

    def dos(self, unix: str) -> Optional[Path]:
        """The path Wine gives the Linux path ``unix``, or None."""
        try:
            import ctypes

            kernel32 = _kernel32()
            answer = kernel32.wine_get_dos_file_name(str(unix).encode("utf-8", "surrogateescape"))
            if not answer:
                return None
            try:
                text = ctypes.wstring_at(answer)
            finally:
                kernel32.HeapFree(kernel32.GetProcessHeap(), 0, answer)
        except (OSError, AttributeError, ValueError):
            return None
        # A drive's path comes as \\?\Z:\..., which reads as Z:\... does.
        if re.match(r"\\\\\?\\[A-Za-z]:", text):
            text = text[4:]
        return Path(text)

    def real(self, unix: str) -> str:
        """``unix`` with every link on the way followed, where Wine can reach
        it; as it is otherwise."""
        there = self.dos(unix)
        return (self.unix(there) if there is not None else "") or unix


def host() -> Optional[Host]:
    """The Linux side of this Wine, or None where this is not Wine."""
    if not version():
        return None
    home = os.environ.get("WINE_HOST_HOME", "").strip()
    # What Proton, and protontricks after it, name the Steam install by.
    steam = os.environ.get("STEAM_COMPAT_CLIENT_INSTALL_PATH", "").strip()
    drive = os.environ.get("SYSTEMDRIVE", "").strip() or "C:"
    return Host(home if home.startswith("/") else "", [steam] if steam.startswith("/") else [],
                Path(drive + "\\"))


def within(path: str, folder: str) -> bool:
    """Whether the Linux path ``path`` is ``folder`` or inside it."""
    if not path or not folder:
        return False
    inner, outer = PurePosixPath(path), PurePosixPath(folder)
    return inner == outer or outer in inner.parents
