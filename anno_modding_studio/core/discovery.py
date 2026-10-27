"""Best-effort hunting for Anno installs and already-extracted asset files.

``assets.xml`` is not a loose file in the game directory -- it ships inside the
game's ``.rda`` archives -- so this cannot conjure one out of nothing.  What it
can do is find installs (to tell the user where their archives are) and find
copies somebody has already extracted, which is the common case for anyone who
has modded before.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .i18n import tr, tr_noop

# The games of games.GAMES, and only those: an install of any other Anno
# would be offered for scanning as if the Studio could read it.
GAME_PATTERNS = (
    re.compile(r"^anno\s*1800$", re.I),
    re.compile(r"^anno\s*117.*$", re.I),
)

STEAM_HINTS = (
    r"C:\Program Files (x86)\Steam",
    r"C:\Program Files\Steam",
)
# Where Steam is on Linux: its own link to where it is, its usual folder,
# and Flathub's.  Under the home folder, so asked off Windows only.
LINUX_STEAM_HINTS = (
    "~/.steam/steam",
    "~/.local/share/Steam",
    "~/.var/app/com.valvesoftware.Steam/data/Steam",
)

UBISOFT_HINTS = (
    r"C:\Program Files (x86)\Ubisoft\Ubisoft Game Launcher\games",
    r"C:\Program Files\Ubisoft\Ubisoft Game Launcher\games",
    r"C:\Program Files (x86)\Ubisoft",
    r"C:\Ubisoft",
)

# Directories that are never worth descending into during a scan.
SKIP_DIR_NAMES = {
    "$recycle.bin",
    "windows",
    "system volume information",
    "appdata",
    "node_modules",
    ".git",
    "__pycache__",
    "winsxs",
    "program files",
    "program files (x86)",
    "programdata",
}


@dataclass(frozen=True)
class GameInstall:
    name: str
    path: Path
    archive_dir: Optional[Path]
    archive_count: int

    @property
    def has_archives(self) -> bool:
        return self.archive_count > 0


def _exists(path) -> Optional[Path]:
    try:
        p = Path(path)
        return p if p.is_dir() else None
    except OSError:
        return None


def _unique_dirs(paths) -> List[Path]:
    """Drop duplicates that differ only by case or by resolved form."""
    out: List[Path] = []
    seen = set()
    for path in paths:
        if path is None:
            continue
        try:
            key = str(path.resolve()).lower()
        except OSError:
            key = str(path).lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(path)
    return out


def steam_roots() -> List[Path]:
    """Where Steam itself is installed.

    The registry is the only reliable answer: a custom install (``C:/Games/Steam``,
    say) is invisible to the Program Files guesses.
    """
    roots: List[Path] = []
    try:
        import winreg

        for hive, key, value in (
            (winreg.HKEY_CURRENT_USER, r"SOFTWARE\Valve\Steam", "SteamPath"),
            (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Valve\Steam", "InstallPath"),
            (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Valve\Steam", "InstallPath"),
        ):
            try:
                with winreg.OpenKey(hive, key) as handle:
                    found = _exists(winreg.QueryValueEx(handle, value)[0])
                    if found:
                        roots.append(found)
            except OSError:
                continue
    except ImportError:
        pass
    for hint in STEAM_HINTS:
        found = _exists(hint)
        if found:
            roots.append(found)
    if os.name != "nt":
        for hint in LINUX_STEAM_HINTS:
            found = _exists(os.path.expanduser(hint))
            if found:
                roots.append(found)
    return _unique_dirs(roots)


def steam_libraries() -> List[Path]:
    """Steam library roots, read out of libraryfolders.vdf where available."""
    libraries: List[Path] = []
    for root in steam_roots():
        libraries.append(root / "steamapps" / "common")
        vdf = root / "steamapps" / "libraryfolders.vdf"
        if not vdf.is_file():
            continue
        try:
            text = vdf.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for match in re.finditer(r'"path"\s*"([^"]+)"', text):
            candidate = _exists(match.group(1).replace("\\\\", "\\"))
            if candidate:
                libraries.append(candidate / "steamapps" / "common")
    return _unique_dirs(lib for lib in libraries if lib.is_dir())


def ubisoft_installs() -> List[Tuple[str, str]]:
    """``(game id, install folder)`` for every game Ubisoft Connect says it
    installed, as its registry entries say; none off Windows."""
    try:
        import winreg
    except ImportError:
        return []
    found: List[Tuple[str, str]] = []
    for key in (r"SOFTWARE\WOW6432Node\Ubisoft\Launcher\Installs", r"SOFTWARE\Ubisoft\Launcher\Installs"):
        try:
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, key) as root:
                for i in range(winreg.QueryInfoKey(root)[0]):
                    try:
                        name = winreg.EnumKey(root, i)
                        with winreg.OpenKey(root, name) as sub:
                            folder = winreg.QueryValueEx(sub, "InstallDir")[0]
                    except OSError:
                        continue
                    found.append((name, str(folder)))
        except OSError:
            continue
    return found


def ubisoft_dirs() -> List[Path]:
    """Ubisoft Connect install roots, from the registry plus the usual paths."""
    dirs: List[Path] = []
    for _game_id, folder in ubisoft_installs():
        found = _exists(folder)
        if found:
            dirs.append(found)

    for hint in UBISOFT_HINTS:
        found = _exists(hint)
        if found:
            dirs.append(found)
    return _unique_dirs(dirs)


def _looks_like_game_dir(name: str) -> bool:
    return any(pattern.match(name.strip()) for pattern in GAME_PATTERNS)


def _archive_info(path: Path):
    """Locate the folder of .rda archives inside a game install."""
    for candidate in (path / "maindata", path / "data", path):
        try:
            if not candidate.is_dir():
                continue
            count = sum(1 for entry in candidate.iterdir() if entry.suffix.lower() == ".rda")
        except OSError:
            continue
        if count:
            return candidate, count
    return None, 0


def find_game_installs() -> List[GameInstall]:
    """Anno installs we can spot from Steam and Ubisoft Connect."""
    seen = set()
    installs: List[GameInstall] = []

    roots: List[Path] = []
    roots.extend(steam_libraries())
    roots.extend(ubisoft_dirs())

    for root in roots:
        try:
            children = list(root.iterdir())
        except OSError:
            continue
        for child in children:
            try:
                if not child.is_dir() or not _looks_like_game_dir(child.name):
                    continue
            except OSError:
                continue
            resolved = str(child.resolve()).lower()
            if resolved in seen:
                continue
            seen.add(resolved)
            archive_dir, count = _archive_info(child)
            installs.append(GameInstall(child.name, child, archive_dir, count))

    # A Ubisoft InstallDir may itself be the game folder rather than a parent.
    for root in ubisoft_dirs():
        if _looks_like_game_dir(root.name):
            resolved = str(root.resolve()).lower()
            if resolved not in seen:
                seen.add(resolved)
                archive_dir, count = _archive_info(root)
                installs.append(GameInstall(root.name, root, archive_dir, count))

    installs.sort(key=lambda i: i.name.lower())
    return installs


def looks_like_assets_file(path: Path) -> bool:
    """Cheap sniff for a *base game* asset file.

    The important rejection is other people's mods: those live at the very same
    relative path inside a mod folder, but their root element is ``<ModOps>``.
    Offering one as the base file would be actively misleading.
    """
    try:
        with open(path, "rb") as fh:
            head = fh.read(65536)
    except OSError:
        return False
    if b"<ModOps" in head or b"<ModOp " in head:
        return False
    if b"<AssetList" in head or b"<Assets>" in head:
        return True
    # Otherwise require an actual asset definition, not merely the word "Asset".
    return b"<Asset>" in head and b"<Values>" in head


# -- GUIDs other mods on this PC already give out ---------------------------------
def created_guids(root) -> List[str]:
    """GUIDs a piece of ModOps XML brings into existence.

    Every ModOp targets an existing asset by GUID, so a bare ``<GUID>`` element
    is not enough to tell "this mod invents this identity" from "this mod
    happens to edit a field somewhere near a GUID".  What is unambiguous is an
    asset's own identity, which lives at ``Values/Standard/GUID`` and nowhere
    else.  So two shapes count:

    * a ``<GUID>`` inside ``<Standard>`` -- a whole ``<Asset>`` added by any op
      (``addNextSibling``, ``addPrevSibling``, ``add``, ``replace``...), an
      ``<Asset>`` written straight under ``<ModOps>`` the way Anno 117 mods
      add one, or a ``<Standard>`` or ``<Values>`` swapped in whole;
    * a ``<GUID>`` that is the whole payload of an op aimed at
      ``Standard/GUID`` or ``Standard`` -- renumbering an existing asset,
      which makes its new number taken just as surely.

    Ops shipped switched off count too: a tweak tool can switch them on, and
    skipping one number too many costs nothing.
    """
    found: List[str] = []
    for node in root.iter("GUID"):
        parent = node.getparent()
        if parent is None:
            continue
        if parent.tag != "Standard" and not (
            parent.tag == "ModOp" and _aims_at_identity(parent)
        ):
            continue
        guid = (node.text or "").strip()
        if guid:
            found.append(guid)
    return found


def _aims_at_identity(op) -> bool:
    """Whether a ModOp's path ends at an asset's ``Standard`` or its GUID.

    Read as the more willing of the two games reads it, names in any case
    and the short form too: a number taken that is not costs nothing."""
    from . import modop

    path = modop.type_and_path(modop.as_117_reads(dict(op.attrib)))[1]
    steps = [step.strip() for step in path.lower().split("/") if step.strip()]
    return steps[-1:] == ["standard"] or steps[-2:] == ["standard", "guid"]


def created_guids_in_text(text: str) -> List[str]:
    """:func:`created_guids` for one op, group or asset kept as its source."""
    from lxml import etree

    try:
        root = etree.fromstring(
            (text or "").strip().encode("utf-8"), etree.XMLParser(recover=True, resolve_entities="internal", no_network=True)
        )
    except (etree.XMLSyntaxError, ValueError):
        return []
    return created_guids(root) if root is not None else []


# Where each kind of folder is, said the way the dialog says it.
WHERE_MODS = "your mods folder"
WHERE_UBI = "the game's .ubi mods folder"
WHERE_DOCUMENTS = "your Documents mods folder"
WHERE_MODIO = "mod.io's downloads"
WHERE_LIBRARY = "the Studio's library"
# And a mod found in each, as a whole phrase: translated where it is said.
_FOUND_IN = {
    WHERE_MODS: tr_noop("%s, in your mods folder"),
    WHERE_UBI: tr_noop("%s, in the game's .ubi mods folder"),
    WHERE_DOCUMENTS: tr_noop("%s, in your Documents mods folder"),
    WHERE_MODIO: tr_noop("%s, in mod.io's downloads"),
    WHERE_LIBRARY: tr_noop("%s, in the Studio's library"),
}


def mods_in(root, where: str) -> List[tuple]:
    """``(folder, where)`` for each folder directly inside ``root``.

    Each is read whole, so a mod bundled inside another -- or sitting in the
    numbered folder mod.io keeps each download in -- is found with its host.
    Dot-folders are tooling, never mods: ``.ubi`` is a place mods live in its
    own right, and :func:`mods_on_this_pc` asks for it by name.
    """
    try:
        entries = sorted(Path(root).iterdir())
    except OSError:
        return []
    found = []
    for entry in entries:
        try:
            if entry.is_dir() and not entry.name.startswith("."):
                found.append((entry, where))
        except OSError:
            continue
    return found


def mods_on_this_pc(game: str, mods_folders=(), library_dir=None) -> List[tuple]:
    """``(mod folder, where)`` for every mod of ``game`` the Studio can find.

    Every folder the game loads from, as :func:`core.switches.mod_paths` knows
    them -- the installation's, Anno 117's ``.ubi``, the one under Documents
    and mod.io's downloads -- whether or not the mods in them are switched on,
    since a mod that is off today is on tomorrow.  Then the library: its copies,
    linked in or not, and the folders elsewhere it refers to.  A mod reached
    more than one way is read once, under the first.
    """
    from . import library, modio_cache, switches

    documents = switches.documents_mods(game)
    modio = modio_cache.mods_dir(game)
    roots = []
    for folder in [f for f in mods_folders if f] or [None]:
        for root in switches.mod_paths(game, folder):
            if root not in roots:
                roots.append(root)
    found = []
    for root in roots:
        where = (
            WHERE_DOCUMENTS if root == documents
            else WHERE_MODIO if root == modio
            else WHERE_UBI if root.name.lower() == ".ubi"
            else WHERE_MODS
        )
        found.extend(mods_in(root, where))
    if library_dir is not None:
        found.extend(mods_in(library_dir, WHERE_LIBRARY))
        for target in library.references(Path(library_dir)).values():
            found.append((Path(target), WHERE_LIBRARY))
    return found


def _asset_patches(folder: Path, asset_relpath: Path) -> List[Path]:
    """Every file under ``folder`` sitting where a mod's asset patch sits.

    Anywhere down the tree, because the loader finds a mod wherever its
    modinfo is: a mod bundled inside another is read in its own right.
    """
    wanted = [part.lower() for part in Path(asset_relpath).parts]
    found = []
    for here, folders, files in os.walk(folder):
        folders[:] = sorted(name for name in folders if not name.startswith("."))
        for name in files:
            if name.lower() != wanted[-1]:
                continue
            path = Path(here) / name
            try:
                parts = [part.lower() for part in path.relative_to(folder).parts]
            except ValueError:
                continue
            if parts[-len(wanted):] == wanted:
                found.append(path)
    return sorted(found)


# Resolved patch path -> (the stamp of every file read for it, its GUIDs).  A
# mods folder is read every time the dialog opens and changes far less often.
_READ: Dict[str, tuple] = {}


def _stamp(paths) -> tuple:
    out = []
    for path in paths:
        try:
            info = Path(path).stat()
        except OSError:
            return ()
        out.append((str(path), info.st_mtime_ns, info.st_size))
    return tuple(out)


def _patch_guids(patch: Path, mod_root: Path) -> List[str]:
    """The GUIDs one asset patch creates, the files it includes read too."""
    key = str(patch)
    held = _READ.get(key)
    if held is not None and held[0] and _stamp(p for p, _, _ in held[0]) == held[0]:
        return held[1]
    read: List[Path] = [patch]
    guids = _read_with_includes(patch, mod_root, read, set(read), 0)
    _READ[key] = (_stamp(read), guids)
    return guids


def _read_with_includes(path: Path, mod_root: Path, read: List[Path], seen: set, depth: int) -> List[str]:
    """What :func:`created_guids` finds in ``path`` and every file it includes.

    Resolved the way the loader resolves them -- relative to the including
    file, or from the top of the mod for a name starting with a slash -- and
    every ``<Include>`` followed, conditional or switched off: which of them
    runs is a question about somebody else's game.
    """
    from . import modops

    root, _repairs = modops.read_patch(path)
    if root is None:
        return []
    found = created_guids(root)
    if depth >= modops.MAX_INCLUDE_DEPTH:
        return found
    for node in root.iter():
        if not modops.is_include(node):
            continue
        named = modops.include_file(node)
        if not named:
            continue
        try:
            target = modops.include_target(named, path, mod_root)
        except (OSError, ValueError):
            continue
        if target in seen or not target.is_file():
            continue
        seen.add(target)
        read.append(target)
        found.extend(_read_with_includes(target, mod_root, read, seen, depth + 1))
    return found


@dataclass(frozen=True)
class Owner:
    """One mod on this PC that creates a GUID."""

    name: str      # the folder its data sits in, as it goes by where found
    where: str     # WHERE_MODS, WHERE_LIBRARY, ...
    root: Path     # that folder, resolved
    mod_id: str    # its ModID, lower-cased, or ""

    @property
    def label(self) -> str:
        said = _FOUND_IN.get(self.where)
        return tr(said, self.name) if said else "%s, in %s" % (self.name, self.where)


def guid_owners(mods, asset_relpath: Path, exclude: Optional[Path] = None) -> Dict[str, List[Owner]]:
    """Every mod in ``mods`` that creates each GUID, in the order found.

    ``mods`` is ``(mod folder, where)`` pairs -- :func:`mods_on_this_pc`, or
    :func:`mods_in` for one folder.  Each owner is named by the folder its
    ``data`` sits in, so a mod bundled inside another is named as itself, with
    its folder and ModID: a warning has to leave out the mods the loadout
    already has loaded, and a second copy of the same mod -- in the library
    and in mod.io's downloads -- is not another mod (``repeats.outside``).

    Best-effort and silent about anything unreadable: a folder that fails to
    parse should not block the ones that do.  ``exclude`` is normally the
    loadout's own export target, so a previous export of *this* mod -- through
    whatever junction it is reached -- is never reported as someone else's.
    """
    from . import generated, library

    found: Dict[str, List[Owner]] = {}
    exclude_at = _resolved(exclude) if exclude is not None else None
    depth = len(Path(asset_relpath).parts)
    done = set()
    ids: Dict[Path, str] = {}
    for folder, where in mods:
        top = _resolved(folder)
        # A link to a generated copy is the library's copy it was made from,
        # reached a second way: read once, as that.
        made_of = generated.source_of(top)
        if made_of is not None:
            top = _resolved(made_of)
        if top in done or (exclude_at is not None and top.is_relative_to(exclude_at)):
            continue
        done.add(top)
        try:
            patches = _asset_patches(top, asset_relpath)
        except OSError:
            continue
        for patch in patches:
            if exclude_at is not None and patch.is_relative_to(exclude_at):
                continue
            mod_root = patch.parents[depth - 1]
            # The name it goes by where it was found: a library copy is
            # linked in under the mod's name, whatever its own folder is.
            name = Path(folder).name if mod_root == top else mod_root.name
            if mod_root not in ids:
                try:
                    ids[mod_root] = library.mod_id_of(mod_root)
                except (OSError, ValueError):
                    ids[mod_root] = ""
            owner = Owner(name, where, _resolved(mod_root), ids[mod_root])
            for guid in _patch_guids(patch, mod_root):
                held = found.setdefault(guid, [])
                if owner not in held:
                    held.append(owner)
    return found


def _resolved(path) -> Path:
    try:
        return Path(path).resolve()
    except (OSError, ValueError):
        return Path(path)
