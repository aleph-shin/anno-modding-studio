"""Files on the disk: writing them whole, reading them back, comparing paths.

Everything the Studio remembers between runs is a small file -- the loadout,
the library's records, which loadout is live, what was extracted -- and so are
the game's own switches it writes into: Anno 117's profiles, the pointer to
the live one, the mods' options.  Each used to be written with a bare
``write_text``, which truncates the file and then fills it, so a crash, a full
disk or the game reading at the wrong moment could leave half of one.  Here a
file is written beside itself and moved over the old one in one step: after
any failure the old file is still whole.

Reading is the other half.  A file the Studio keeps can be missing, unreadable
or not what it expects, and every caller wanted the same answer to all three:
carry on as if it held nothing.

No other core module is imported, so anything can use these.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import shutil
from pathlib import Path
from typing import Any, Callable, Dict, Optional

# What Windows will not have in a file or folder name: the characters it
# refuses, the separators, and the control characters.
FORBIDDEN_IN_A_NAME = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
# Names Windows opens as a device rather than a file, whatever follows the
# first dot and with spaces before it: ``CON .txt`` is the console too.  The
# superscript digits are devices as much as the plain ones.
DEVICE_NAMES = frozenset({"CON", "PRN", "AUX", "NUL", "CONIN$", "CONOUT$"} | {
    "%s%s" % (prefix, digit)
    for prefix in ("COM", "LPT") for digit in "0123456789\u00b9\u00b2\u00b3"
})


def windows_name(text: str) -> str:
    """``text`` as one name Windows will hold, or "" when nothing is left.

    The one rule for every name that comes from outside -- a mod.io mod's
    name, a modinfo field, a zip entry's parts -- before it becomes part of a
    path.  What Windows forbids becomes ``_``, so no separator, drive or
    stream survives.  Dots and spaces at the end go, as Windows drops them
    itself, which also leaves ``.`` and ``..`` as nothing.  A device name gets
    a ``_`` in front.  Spaces in front are kept: in a mod folder's name they
    are what the game sorts by.
    """
    cleaned = FORBIDDEN_IN_A_NAME.sub("_", text or "").rstrip(" .")
    if cleaned.split(".")[0].rstrip(" ").upper() in DEVICE_NAMES:
        cleaned = "_" + cleaned
    return cleaned


def safe_folder_name(text: str, fallback: str = "Anno Mod") -> str:
    """A folder name made out of words someone typed, or a name from outside:
    :func:`windows_name`, with spaces at either end gone, and ``fallback``
    when nothing is left."""
    return windows_name((text or "").strip()).strip() or fallback


def directly_in(path, folder) -> bool:
    """Whether ``path`` names something directly inside ``folder``, once the
    folder is resolved: the check before anything is moved to, or moved out
    of the way of, a path built from a name.  The last step is not resolved,
    so a link sitting at ``path`` is asked about where it is, not where it
    points; a name that is ``..``, a drive or a stream is not inside."""
    path, folder = Path(path), Path(folder)
    name = path.name
    if not name or name in (".", "..") or FORBIDDEN_IN_A_NAME.search(name):
        return False
    try:
        return os.path.normcase(str(path.parent.resolve())) == os.path.normcase(
            str(folder.resolve()))
    except (OSError, ValueError):
        return False


def remote_or_device(text) -> bool:
    """Whether ``text`` is spelt as a path on another machine or a device's:
    ``\\\\host\\share``, ``//host/share``, ``\\\\?\\``, ``\\\\.\\``, or the
    ``\\??\\`` Windows passes straight through.  Asking Windows anything at
    all of such a path -- whether it is there -- can connect to that machine
    and hand it the user's sign-in.  So a path read from a file somebody
    else may have written is asked this first (security review N1)."""
    spelt = str(text or "").replace("/", "\\")
    return spelt.startswith("\\\\") or spelt.startswith("\\??\\")


def spelt_inside(path, folder) -> bool:
    """Whether ``path`` is spelt as ``folder`` or a place under it, once
    each is made absolute and ``..`` taken out, without looking at the disk:
    a link inside ``folder`` that leads elsewhere still counts as inside.
    For a path from a file, where :func:`inside` would look it up."""
    if not path or not folder or remote_or_device(path):
        return False
    one, other = (os.path.normcase(os.path.abspath(str(p))) for p in (path, folder))
    return one == other or one.startswith(other.rstrip(os.sep) + os.sep)


# -- writing whole ----------------------------------------------------------------
def write_bytes(path, data: bytes, durable: bool = False) -> None:
    """Put ``data`` in ``path`` whole, or leave what was there as it was.

    Written to a file beside it, then moved over it; the move is one step on
    the same volume, so no reader ever sees half.  A link is written through,
    as ``write_bytes`` would: what it points at changes, not the link.
    ``durable`` waits for the file to reach the disk before it is moved
    over, for one that must be there after a power cut: a save's journal.
    """
    path = Path(path)
    if path.is_symlink():
        path = path.resolve()
    partial = path.with_name("%s.%d.part" % (path.name, os.getpid()))
    try:
        if durable:
            write_flushed(partial, data)
        else:
            partial.write_bytes(data)
        os.replace(partial, path)
    except BaseException:
        with contextlib.suppress(OSError):
            partial.unlink()
        raise


def write_flushed(path, data: bytes) -> None:
    """Write ``data`` in ``path`` and wait until it is on the disk, not in a
    buffer of the operating system's."""
    with open(path, "wb") as file:
        file.write(data)
        file.flush()
        os.fsync(file.fileno())


def text_bytes(text: str, encoding: str = "utf-8", newline: Optional[str] = None) -> bytes:
    """``text`` as :func:`write_text` writes it, with ``newline`` meaning
    what it means to ``open``: by default each ``\\n`` is written as the
    platform's."""
    if newline is None:
        newline = os.linesep
    if newline not in ("", "\n"):
        text = text.replace("\n", newline)
    return text.encode(encoding)


def json_bytes(data: Any, indent: Optional[int] = 2, sort_keys: bool = False) -> bytes:
    """``data`` as :func:`write_json` writes it: non-ASCII written as
    itself, and a newline at the end."""
    return text_bytes(json.dumps(data, indent=indent, sort_keys=sort_keys, ensure_ascii=False) + "\n")


def write_text(path, text: str, encoding: str = "utf-8", newline: Optional[str] = None,
               durable: bool = False) -> None:
    """:func:`write_bytes` for text (:func:`text_bytes`)."""
    write_bytes(path, text_bytes(text, encoding, newline), durable)


def write_json(path, data: Any, indent: Optional[int] = 2, sort_keys: bool = False,
               durable: bool = False) -> None:
    """``data`` as JSON in ``path``, whole (:func:`json_bytes`)."""
    write_bytes(path, json_bytes(data, indent, sort_keys), durable)


# -- reading JSON -----------------------------------------------------------------
def read_json(path, default: Any = None, comments: bool = False) -> Any:
    """What a JSON file holds, or ``default`` when it is missing, cannot be
    read, is not JSON, or holds another kind of thing than ``default`` is --
    a list where a record was expected.  A ``default`` of None takes anything.

    Read as UTF-8 with or without the byte-order mark some editors put first.
    ``comments`` allows ``//`` and ``/* */`` comments, for the files the
    community's tools write (see :func:`strip_comments`).
    """
    try:
        text = Path(path).read_text(encoding="utf-8-sig")
        found = json.loads(strip_comments(text) if comments else text)
    except (OSError, ValueError):
        return default
    if default is not None and not isinstance(found, type(default)):
        return default
    return found


def strip_comments(text: str) -> str:
    """Take ``//`` and ``/* */`` comments out of a JSON-with-comments file.

    ``modinfo.jsonc`` is what the community's VS Code extension writes, and the
    Anno 117 loader takes it, so a mod that ships one is an ordinary mod -- not
    one with no name, no creator and no ModID, which is what it looks like when
    a plain JSON parser refuses the file and the caller shrugs.  The game's
    own options file may carry a comment block at the top, too.

    String-aware, because ``"Description": "https://example.com"`` is a URL and
    not the start of a comment.
    """
    out = []
    i, n = 0, len(text)
    while i < n:
        ch = text[i]
        if ch == '"':
            j = i + 1
            while j < n:
                if text[j] == "\\":
                    j += 2
                    continue
                if text[j] == '"':
                    j += 1
                    break
                j += 1
            out.append(text[i:j])
            i = j
            continue
        if ch == "/" and i + 1 < n:
            if text[i + 1] == "/":
                i = text.find("\n", i)
                if i < 0:
                    break
                continue
            if text[i + 1] == "*":
                end = text.find("*/", i + 2)
                i = n if end < 0 else end + 2
                continue
        out.append(ch)
        i += 1
    return "".join(out)


# -- paths ------------------------------------------------------------------------
def reachable(path) -> str:
    """A path Windows will open however long it is.

    Windows stops at 260 characters unless a path is spelled with the
    ``\\\\?\\`` prefix, and a mod is deep before an export folder is put in
    front of it: *Tourists Alternativ Buildings* keeps a ``.cfg`` 196
    characters down its own tree, which was enough to fail while being copied
    into the census's working folder.
    """
    text = str(path)
    if os.name != "nt" or text.startswith("\\\\?\\") or not Path(text).is_absolute():
        return text
    return "\\\\?\\" + os.path.normpath(text)


def inside(path, folder) -> bool:
    """Whether ``path`` is ``folder`` or anywhere under it, once both are
    resolved -- through junctions, so a link in a mods folder to a copy in
    the library is inside the library.  False for an empty path, or one that
    cannot be resolved.

    For paths that are only names, and must not be looked up on the disk,
    ``PurePath.is_relative_to`` asks the same thing of the spelling.
    """
    if not path or not folder:
        return False
    try:
        Path(path).resolve().relative_to(Path(folder).resolve())
    except (ValueError, OSError):
        return False
    return True


def as_spelled(folder, rel: str) -> str:
    """``rel``, a path from the top of ``folder`` with forward slashes, as
    ``folder`` spells it: each step matched without regard to case where the
    folder has it under another, as the game finds a file -- on Windows, and
    under Wine on a disk that tells case apart.  From the first step the
    folder does not have, the rest is kept as written."""
    here = Path(folder)
    parts = [part for part in str(rel).replace("\\", "/").split("/") if part]
    spelled = []
    for at, part in enumerate(parts):
        try:
            names = os.listdir(here)
        except OSError:
            spelled.extend(parts[at:])
            break
        if part not in names:
            folded = part.casefold()
            part = next((name for name in names if name.casefold() == folded), part)
        spelled.append(part)
        here = here / part
    return "/".join(spelled)


def same_file(one, other) -> bool:
    """Whether two paths name the same file, however they were spelled."""
    if not one or not other:
        return False
    try:
        return os.path.normcase(str(Path(one).resolve())) == os.path.normcase(str(Path(other).resolve()))
    except (OSError, ValueError):
        return False


def folder_size(folder) -> int:
    """How many bytes the files under ``folder`` come to; a file that cannot
    be asked is left out."""
    total = 0
    for path in Path(folder).rglob("*"):
        try:
            if path.is_file():
                total += path.stat().st_size
        except OSError:
            continue
    return total


def free_space(folder) -> Optional[int]:
    """Bytes free on the drive ``folder`` is on -- or would be on, for one
    not made yet -- or None if that cannot be asked."""
    at = Path(folder)
    while not at.exists():
        if at.parent == at:
            return None
        at = at.parent
    try:
        return shutil.disk_usage(at).free
    except OSError:
        return None


# -- reading once -----------------------------------------------------------------
class ReadCache:
    """What reading a file came to, kept for as long as the file is as it was.

    ``read(path, *also)`` is called once per file, per ``also`` -- whatever
    else the reading depends on, such as the game -- and again only when the
    file's modification time or size has moved.  A switch flipped rebuilds
    what the mods say from these rather than from the disk.  Past ``limit``
    entries the lot is dropped, so it cannot grow without end.
    """

    def __init__(self, read: Callable[..., Any], limit: int = 2000) -> None:
        self._read = read
        self._limit = limit
        self._held: Dict[tuple, Any] = {}

    def get(self, path, *also) -> Any:
        """What ``read`` says of ``path``, or None when the file cannot be
        looked at."""
        try:
            stat = Path(path).stat()
        except OSError:
            return None
        key = (str(path), *also, stat.st_mtime, stat.st_size)
        found = self._held.get(key)
        if found is None:
            if len(self._held) > self._limit:
                self._held.clear()
            found = self._held[key] = self._read(path, *also)
        return found
