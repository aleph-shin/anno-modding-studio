"""Find and extract the handful of files the Studio needs from a game install.

Anno 1800 ships 38 archives and puts a *complete* ``assets.xml`` in most of them:
``data0.rda`` has the launch-day file, and every patch archive up to ``data33.rda``
carries a newer full copy.  Extracting the wrong one silently gives you a game
from years ago -- ``data0`` has 51,260 assets where ``data33`` has 104,190.

So the rule is load order: scan every archive, and for any given path let the
latest archive win.  Anno 117 is simpler (``config.rda``, plus ``zz_patchfiles_*``)
but the same rule applies.
"""

from __future__ import annotations

import fnmatch
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence

from .. import __version__
from . import disk, games
from .i18n import tr
from .rda import RdaArchive, RdaEntry, RdaError, archives_in_load_order

# What we care about, per game. Globs are matched against the in-archive path.
ASSET_PATTERNS = (
    "data/config/export/main/asset/assets.xml",  # Anno 1800
    "data/base/config/export/assets.xml",  # Anno 117
)
# The schema files, which sit next to assets.xml and are tiny.
SCHEMA_PATTERNS = (
    "data/config/export/main/asset/templates.xml",   # Anno 1800
    "data/config/export/main/asset/properties.xml",
    "data/base/config/export/templates.xml",         # Anno 117
    "data/base/config/export/properties.xml",
)

TEXT_PATTERNS = (
    "data/config/gui/texts_*.xml",  # Anno 1800
    "data/base/config/gui/texts_*.xml",  # Anno 117
)

# Matches the texts glob but holds tooling metadata, not translations.
TEXT_EXCLUDES = ("texts_metadata.xml",)


@dataclass
class FoundFile:
    """A file located inside an archive, already resolved for patch order."""

    path: str  # in-archive path
    archive: Path
    entry: RdaEntry

    @property
    def size(self) -> int:
        return self.entry.uncompressed_size

    @property
    def filename(self) -> str:
        return self.path.rsplit("/", 1)[-1]

    @property
    def language(self) -> str:
        match = re.match(r"texts_(.+)\.xml$", self.filename, re.I)
        return match.group(1).replace("_", " ").title() if match else ""

    @property
    def game(self) -> str:
        return games.detect_from_path(self.path)


@dataclass
class ScanResult:
    folder: Path
    assets: List[FoundFile]
    texts: List[FoundFile]
    schema: List[FoundFile]
    archives_scanned: int
    archives_failed: List[str]
    encrypted_blocks: int
    superseded: int = 0  # older copies of a wanted file that later archives replaced

    @property
    def best_assets(self) -> Optional[FoundFile]:
        return self.assets[0] if self.assets else None

    def texts_for(self, language: str) -> Optional[FoundFile]:
        for found in self.texts:
            if found.language.lower() == language.lower():
                return found
        return None

    def schema_file(self, filename: str) -> Optional[FoundFile]:
        """``templates.xml`` or ``properties.xml`` for the winning asset file."""
        for found in self.schema:
            if found.filename.lower() == filename.lower():
                return found
        return None

    @property
    def languages(self) -> List[str]:
        seen: List[str] = []
        for found in self.texts:
            if found.language and found.language not in seen:
                seen.append(found.language)
        return seen


def maindata_folder(game_folder: Path) -> Optional[Path]:
    """Accept either the game folder or its ``maindata`` directly."""
    folder = Path(game_folder)
    for candidate in (folder / "maindata", folder):
        if candidate.is_dir() and any(
            p.suffix.lower() == ".rda" for p in _safe_iterdir(candidate)
        ):
            return candidate
    return None


def _safe_iterdir(folder: Path):
    try:
        return list(folder.iterdir())
    except OSError:
        return []


def _matches(path: str, patterns: Sequence[str]) -> bool:
    lowered = path.lower()
    return any(fnmatch.fnmatch(lowered, pattern) for pattern in patterns)


def scan_game_files(
    folder: Path,
    progress: Optional[Callable[[int, int, str], None]] = None,
    should_cancel: Optional[Callable[[], bool]] = None,
) -> ScanResult:
    """Read every archive's directory and resolve the files we need.

    Only directories are read -- payloads are left alone -- so this stays quick
    even across a couple of hundred gigabytes of archives.
    """
    folder = maindata_folder(folder) or Path(folder)
    archives = archives_in_load_order(folder)

    # Later archives overwrite earlier ones for the same path.
    resolved: Dict[str, FoundFile] = {}
    failed: List[str] = []
    encrypted = 0
    scanned = 0
    superseded = 0

    for position, archive in enumerate(archives):
        if should_cancel is not None and should_cancel():
            break
        if progress is not None:
            progress(position, len(archives), archive.name)
        try:
            with RdaArchive(archive) as handle:
                for entry in handle.entries():
                    path = entry.name.lower()
                    if not (
                        _matches(path, ASSET_PATTERNS)
                        or _matches(path, TEXT_PATTERNS)
                        or _matches(path, SCHEMA_PATTERNS)
                    ):
                        continue
                    if entry.is_deleted:
                        resolved.pop(path, None)
                        continue
                    if path in resolved:
                        superseded += 1
                    resolved[path] = FoundFile(entry.name, archive, entry)
                encrypted += handle.encrypted_blocks
            scanned += 1
        except (RdaError, OSError) as exc:
            failed.append("%s: %s" % (archive.name, exc))

    if progress is not None:
        progress(len(archives), len(archives), "")

    assets = [f for p, f in resolved.items() if _matches(p, ASSET_PATTERNS)]
    texts = [
        f
        for p, f in resolved.items()
        if _matches(p, TEXT_PATTERNS) and f.filename.lower() not in TEXT_EXCLUDES
    ]
    schema = [f for p, f in resolved.items() if _matches(p, SCHEMA_PATTERNS)]
    schema.sort(key=lambda f: f.filename)
    assets.sort(key=lambda f: -f.size)
    texts.sort(key=lambda f: f.language.lower())

    return ScanResult(
        folder=folder,
        assets=assets,
        texts=texts,
        schema=schema,
        archives_scanned=scanned,
        archives_failed=failed,
        encrypted_blocks=encrypted,
        superseded=superseded,
    )


def extract(
    found: FoundFile,
    destination_root: Path,
    progress: Optional[Callable[[str], None]] = None,
) -> Path:
    """Extract one found file, preserving its in-archive path under the root.

    Keeping the original relative path matters: the Studio infers which game a
    file belongs to from that path.
    """
    target = Path(destination_root) / Path(found.path)
    if progress is not None:
        progress(found.path)
    with RdaArchive(found.archive) as handle:
        handle.extract(found.entry, target)
    return target


# ------------------------------------------------------- what was read, and when
#
# Extraction is the slow half of loading Anno 1800: minutes of reading archives,
# after which the asset index is rebuilt because the freshly written file has a
# new timestamp. Almost always it is the same file it was last time -- the game
# is only patched every few weeks -- and the way to know that without reading
# anything is to write down what the archives looked like when the extraction
# was made. A game patch rewrites archives, so their sizes and timestamps
# answer "is what I extracted still what the game has?" in a few stat calls.
STAMP_NAME = ".anno-studio-read.json"
# How an extraction is laid out, written into its stamp as ``version``.  One
# made under another number is read as needing to be made again: bump it in
# the change that extracts differently, or lays the files out differently.
EXTRACTED_VERSION = 1

READ_CURRENT = "current"
READ_PATCHED = "patched"
READ_GONE = "gone"


def archive_marks(game_folder) -> List[list]:
    """``[name, size, mtime]`` for every archive the game loads from."""
    folder = maindata_folder(game_folder) or Path(game_folder)
    marks = []
    for archive in archives_in_load_order(folder):
        try:
            stat = archive.stat()
        except OSError:
            continue
        marks.append([archive.name, stat.st_size, int(stat.st_mtime)])
    return marks


def record_read(
    destination_root,
    game: str,
    game_folder,
    assets,
    texts=None,
    schema=None,
    source_archive: str = "",
) -> Path:
    """Write down what this extraction took, and out of what."""
    stamp = {
        "version": EXTRACTED_VERSION,
        "written_by": __version__,
        "game": game,
        "game_folder": str(game_folder),
        "read_at": time.time(),
        "assets": str(assets),
        "texts": str(texts) if texts else "",
        "schema": {name: str(path) for name, path in (schema or {}).items()},
        "source_archive": source_archive,
        "archives": archive_marks(game_folder),
    }
    path = Path(destination_root) / STAMP_NAME
    path.parent.mkdir(parents=True, exist_ok=True)
    disk.write_json(path, stamp)
    return path


def read_stamp(destination_root) -> Optional[dict]:
    """What :func:`record_read` wrote there, if anything readable."""
    raw = disk.read_json(Path(destination_root) / STAMP_NAME, {})
    return raw if raw.get("assets") else None


def read_state(stamp: Optional[dict]) -> tuple:
    """``(state, why)`` -- whether what was extracted still matches the game.

    Conservative in the direction that costs only time: any change to any
    archive reads as patched, because a patch is exactly what rewrites them,
    and being told to re-read unnecessarily is better than editing against a
    file the game no longer ships.
    """
    if not stamp:
        return READ_GONE, tr("nothing has been read from the game yet")
    if stamp.get("version", EXTRACTED_VERSION) != EXTRACTED_VERSION:
        return READ_PATCHED, tr("another version of the Studio extracted these files")
    if not Path(stamp.get("assets") or "").is_file():
        return READ_GONE, tr("the files it extracted are not there any more")
    folder = stamp.get("game_folder") or ""
    now = archive_marks(folder) if folder else []
    if not now:
        return READ_GONE, tr("the game folder it was read from is not there any more")
    was = [list(mark) for mark in (stamp.get("archives") or [])]
    if now != was:
        return READ_PATCHED, tr("the game's files have changed since this was read")
    return READ_CURRENT, tr("the game has not changed since this was read")


def extract_folder_name(game_key: str) -> str:
    """What a game's folder of extracted files is called, wherever it is."""
    return re.sub(r'[<>:"/\\|?*]', "_", games.get(game_key).label)


def default_extract_root(game_key: str) -> Path:
    from . import places

    return places.extracted_dir() / extract_folder_name(game_key)


def former_extract_root(game_key: str) -> Path:
    """:func:`default_extract_root` as it was before the extracted files left
    Documents: where a read made then still is."""
    from . import places

    return places.former_extracted_dir() / extract_folder_name(game_key)


def suggested_root(current: str, game_key: str) -> str:
    """Where "Extract to" should point for ``game_key``, given what it says.

    What the user chose is left alone.  A folder named for a game is not
    that: it is a suggestion, whichever game's and wherever it is -- and
    never where another game's files go.  Judged by the folder's name, not
    by whether it is today's default: the default has moved once, and a
    field still holding the old one for Anno 1800 was taken for a choice,
    so Anno 117 was read into Anno 1800's folder and remembered as there.

    In a place of the user's own, the suggestion is this game's folder
    beside the one named.  In either place of the Studio's, it is today's
    default.
    """
    from . import places

    default = default_extract_root(game_key)
    if not current:
        return str(default)
    here = Path(current)
    if here.name not in {extract_folder_name(key) for key in games.GAMES}:
        return current
    if here.parent in (places.extracted_dir(), places.former_extracted_dir()):
        return str(default)
    return str(here.parent / default.name)
