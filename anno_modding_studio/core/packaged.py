"""Whether Windows keeps the Studio's own files somewhere else than it asked.

A program run inside a packaged Windows app -- the Microsoft Store's Python,
or anything started from a shell of an app installed as an MSIX package --
has its writes to a new folder under ``%LOCALAPPDATA%`` sent to that
package's private copy, ``%LOCALAPPDATA%\\Packages\\<package>\\LocalCache\\Local``.
Inside the package everything looks in place.  Outside it, the folder is not
there: the game reads the library's mods through links in its mods folder,
and every link the Studio made pointed at nothing, with no word from
anything (R3-30, hands-on run 3, where the Studio was started from a shell
inside the Claude desktop app's package).

Told by where the Studio's folder really is: Windows answers that through
the redirection, as :func:`os.path.realpath` asks it.  Not by whether the
process has a package identity: one started from a packaged app's shell has
none, and is redirected all the same.  Only a folder under the package's
private copy counts, since a folder moved elsewhere by a junction of the
user's own resolves to another path too.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Optional

from . import places
from .i18n import tr


def private_copy(folder, local: Optional[Path] = None) -> Optional[Path]:
    """Where ``folder`` really is, where that is a package's private copy of
    the local app data (``local``, :func:`places.local_app_data` unless
    named); None where it is where it was asked for, or is not there."""
    if sys.platform != "win32" or not os.path.isdir(folder):
        return None
    real = os.path.realpath(folder)
    packages = os.path.join(os.path.realpath(local if local is not None else places.local_app_data()), "Packages")
    # Compared as Windows names a file, without regard to case.
    root = os.path.normcase(packages) + os.sep
    if not os.path.normcase(real).startswith(root):
        return None
    parts = Path(real[len(root):]).parts
    return Path(real) if len(parts) > 1 and parts[1].lower() == "localcache" else None


def note(folder: Optional[Path] = None) -> str:
    """What to say where the Studio's own folder (``folder``, its local
    home unless named) is kept in a package's private copy, or ""."""
    folder = places.local_home() if folder is None else Path(folder)
    there = private_copy(folder)
    if there is None:
        return ""
    return tr(
        "Windows is keeping the Studio's own files in a private folder of the app "
        "it was started from:\n\n%(there)s\n\nand not in %(asked)s, where the game "
        "and every other program look. The links Play and the library put in a "
        "mods folder would point at nothing for the game.\n\nThis happens with the "
        "Microsoft Store's Python, and with anything started from inside an app "
        "installed from a package. Close the Studio, and start it with a Python "
        "installed from python.org, from a terminal of its own.",
        there=str(there), asked=str(folder),
    )
