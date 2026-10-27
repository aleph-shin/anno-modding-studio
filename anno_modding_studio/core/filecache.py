"""The cache that answers questions across the Studio's files.

Loadouts are files, and stay the truth: they can be read, compared and
handed to somebody else.  A question across them -- which loadouts have
this mod, which of the library's mods no loadout names -- was answered by
reading every loadout file each time it was asked
(``LibraryController.mentioned_keys``), remembering what it read only for
the window's life.  Here what each file says is kept in SQLite, the way
:mod:`core.index` keeps ``assets.xml``, and a file is read again only when
its time or size changes.

The cache is in the local folder (:func:`places.files_cache`), never in
Documents, which OneDrive syncs.  It can be deleted at any time and is
built again from the files.  Nothing in it is anything but a reading of a
file, so whatever is wrong with it -- not a database, an older or newer
schema, ruined while it was open -- is answered the same way: the file is
deleted and built again, and where it cannot be (another Studio has it
open, the folder will not take it) the question is answered from the files
in memory.  A connection is held only while a question is answered:
Windows will not delete a database somebody has open.

Projects are read too (the projects-and-loadouts proposal, 4b-3): each
file's row says what kind of file it is, a project's mentions are its
ModID, and a loadout's include each project it refers to, by
:func:`.projects.key_of`.  So the cache says which project keeps a ModID
(:func:`project_keeping`) and which loadouts have that project
(:func:`loadouts_with_project`), and a mod is kept in one project only.
"""

from __future__ import annotations

import contextlib
import os
import sqlite3
from pathlib import Path
from typing import Callable, Dict, List, Optional, Set, Tuple

from . import disk, modfolder, places
from . import projects as project_files
from .changes import AIM_MANAGE, LOADOUT_SUFFIX

# Bumped whenever a cache built by an older build stops being usable; an
# older one is deleted and built again, and a newer one left alone for the
# Studio that made it.
# 2: a loadout's mentions include its projects, and projects are read.
# 3: a patch's project mentions the mod it patches.
# 4: a loadout mentions what it was set up for (AIM_OF).
# 5: a project with a blank Mod ID mentions the one made of its name.
SCHEMA_VERSION = 5
# What a patch's project mentions the mod it patches by, before each of
# that mod's names: apart from the ModID the project keeps itself.
PATCH_OF = "patches:"
# What a loadout mentions the way of working it was set up for by
# (Loadout.aim), before it: a set of mods to play, or a mod of yours.  No
# mod's name has a colon in it.
AIM_OF = "aim:"

# What kind of file a row is.
LOADOUT = "loadout"
PROJECT = "project"

# How long a question waits for another Studio's write before answering
# from the files instead: a window is waiting on it.
BUSY_SECONDS = 1.0
# SQLite's own codes for a database another connection holds.
_BUSY_CODES = {5, 6}

_SCHEMA_SQL = """
create table meta (key text primary key, value text not null);
-- One row per file, read again when its time or size changes. ``folder``
-- is the folder it was found in, as the caller spelt it; ``game`` is the
-- game the file names, null where it names none.
create table files (
    path text primary key,
    kind text not null,
    folder text not null,
    mtime_ns integer not null,
    size integer not null,
    game text
);
create index files_by_folder on files(kind, folder);
-- Every name a file matches a mod by: modfolder.match_keys.
create table mentions (
    path text not null,
    key text not null,
    primary key (path, key)
) without rowid;
create index mentions_by_key on mentions(key);
"""

# What a file of one kind says: its game, and the names of the mods in it.
Reading = Tuple[Optional[str], Set[str]]


def read_loadout(path) -> Reading:
    """The game a loadout file names and every name its mods are matched
    by; a file that cannot be read names no game and no mods."""
    data = disk.read_json(path, {})
    # A file that names no game is taken for any; one that names something
    # other than a game's name, for none.
    game = data.get("game")
    if game is not None and not isinstance(game, str):
        game = ""
    found: Set[str] = set()
    layers = data.get("layers")
    for layer in layers if isinstance(layers, list) else []:
        if isinstance(layer, dict):
            found |= modfolder.match_keys(
                layer.get("mod_id"), layer.get("origin"))
    # A mod of yours is kept in a project, which holds its ModID: the
    # reference to it says the ModID too, and which project it is.
    projects = data.get("projects")
    for ref in projects if isinstance(projects, list) else []:
        if isinstance(ref, dict) and isinstance(ref.get("path"), str):
            found |= modfolder.match_keys(ref.get("mod_id"), ref.get("origin"))
            found.add(project_files.key_of(ref["path"]))
    aim = data.get("aim")
    if isinstance(aim, str) and aim:
        found.add(AIM_OF + aim)
    return game, found


def read_project(path) -> Reading:
    """The game a project file is for and the ModID of its mod; a file that
    cannot be read is for no game and keeps no mod."""
    try:
        project = project_files.Project.read(path)
    except project_files.ProjectError:
        return "", set()
    mod_id = project.goes_out_as.lower()
    found = {mod_id} if mod_id else set()
    found |= {PATCH_OF + key.lower() for key in project.patches}
    return (project.game or None), found


def project_keeping(mod_id: str, game: str, folder=None) -> Optional[Path]:
    """The project that keeps the mod with ``mod_id`` for ``game``, in
    ``folder`` (the projects folder unless named); None for none.  Where
    there are several -- made before a mod was kept in one only -- the one
    written last."""
    wanted = str(mod_id or "").strip().lower()
    if not wanted:
        return None
    where = Path(folder) if folder is not None else places.projects_dir()

    def ask(con: sqlite3.Connection) -> Answer:
        _refresh(con, PROJECT, where, project_files.PROJECT_SUFFIX, read_project)
        return {path: set() for (path,) in con.execute(
            "select f.path from files f join mentions m using (path)"
            " where f.kind = ? and f.folder = ? and m.key = ?"
            " and (f.game = ? or f.game is null) order by f.mtime_ns desc",
            (PROJECT, str(where), wanted, game),
        )}

    found = list(_answer(ask))
    return Path(found[0]) if found else None


def patch_project_for(keys, game: str, folder=None) -> Optional[Path]:
    """The project of your patch of the mod known by ``keys`` -- its
    :func:`modfolder.match_keys` -- for ``game``, in ``folder`` (the projects
    folder unless named); None for none.  Where there are several, made
    before a mod had one patch only, the one written last."""
    wanted = {PATCH_OF + str(key).lower() for key in keys if key}
    if not wanted:
        return None
    where = Path(folder) if folder is not None else places.projects_dir()

    def ask(con: sqlite3.Connection) -> Answer:
        _refresh(con, PROJECT, where, project_files.PROJECT_SUFFIX, read_project)
        marks = ",".join("?" * len(wanted))
        return {path: set() for (path,) in con.execute(
            "select distinct f.path from files f join mentions m using (path)"
            " where f.kind = ? and f.folder = ? and m.key in (%s)"
            " and (f.game = ? or f.game is null) order by f.mtime_ns desc" % marks,
            (PROJECT, str(where), *sorted(wanted), game),
        )}

    found = list(_answer(ask))
    return Path(found[0]) if found else None


def loadouts_with_project(project, game: str, folder=None, excluding=None) -> List[Path]:
    """The loadouts for ``game`` in ``folder`` (the loadouts folder unless
    named) that refer to the project at ``project``, by their names."""
    return loadouts_naming({project_files.key_of(project)}, game, folder, excluding)


def loadouts_to_play(game: str, folder=None, excluding=None) -> List[Path]:
    """The loadouts for ``game`` in ``folder`` (the loadouts folder unless
    named) set up as a set of mods to play (:data:`.changes.AIM_MANAGE`),
    ``excluding`` one of them, by their names: not a mod of yours, nor a
    patch's workspace."""
    return loadouts_naming({AIM_OF + AIM_MANAGE}, game, folder, excluding)


def loadouts(game: str, folder=None, excluding=None) -> List[Path]:
    """The loadouts for ``game`` in ``folder`` (the loadouts folder unless
    named), ``excluding`` one of them, in the order of their names."""
    return sorted((Path(path) for path in _loadouts(game, folder, excluding)),
                  key=lambda path: path.name.lower())


def mentioned_keys(game: str, folder=None, excluding=None) -> Set[str]:
    """Every name a mod is matched by in the loadouts for ``game`` in
    ``folder`` (the loadouts folder unless named), ``excluding`` one of
    them.  A loadout that names no game is taken to be for this one."""
    found: Set[str] = set()
    for _path, keys in _loadouts(game, folder, excluding).items():
        found |= {key for key in keys if not key.startswith(AIM_OF)}
    return found


def loadouts_naming(keys, game: str, folder=None,
                    excluding=None) -> List[Path]:
    """The loadouts for ``game`` in ``folder`` (the loadouts folder unless
    named) that have a mod known by any of ``keys`` -- a set of
    :func:`modfolder.match_keys` -- in the order of their names."""
    wanted = {str(key).lower() for key in keys}
    named = _loadouts(game, folder, excluding)
    return sorted(
        (Path(path) for path, mods in named.items() if mods & wanted),
        key=lambda path: path.name.lower())


def _loadouts(game: str, folder, excluding) -> Dict[str, Set[str]]:
    """What each loadout for ``game`` in ``folder`` names, by its path."""
    where = Path(folder) if folder is not None else places.loadouts_dir()

    def ask(con: sqlite3.Connection) -> Dict[str, Set[str]]:
        _refresh(con, LOADOUT, where, LOADOUT_SUFFIX, read_loadout)
        found: Dict[str, Set[str]] = {}
        for path, key in con.execute(
            "select f.path, m.key from files f left join mentions m"
            " using (path) where f.kind = ? and f.folder = ?"
            " and (f.game = ? or f.game is null)",
            (LOADOUT, str(where), game),
        ):
            found.setdefault(path, set())
            if key is not None:
                found[path].add(key)
        if excluding is not None:
            name = Path(excluding).name.lower()
            for path in [path for path in found
                         if Path(path).name.lower() == name
                         and disk.same_file(path, excluding)]:
                del found[path]
        return found

    return _answer(ask)


def _refresh(con: sqlite3.Connection, kind: str, folder: Path, suffix: str,
             read: Callable[[Path], Reading]) -> None:
    """Bring the rows for ``kind``'s files in ``folder`` up to date: a file
    whose time or size moved is read again, a new one read, and one that
    has gone forgotten."""
    listed: Dict[str, Tuple[int, int]] = {}
    try:
        paths = list(folder.glob("*" + suffix))
    except OSError:
        paths = []
    for found in paths:
        try:
            stat = found.stat()
        except OSError:
            continue
        listed[str(found)] = (stat.st_mtime_ns, stat.st_size)
    held = {
        path: (mtime_ns, size)
        for path, mtime_ns, size in con.execute(
            "select path, mtime_ns, size from files"
            " where kind = ? and folder = ?",
            (kind, str(folder)),
        )
    }
    with con:
        for path in held.keys() - listed.keys():
            _forget(con, path)
        for path, stamp in listed.items():
            if held.get(path) == stamp:
                continue
            # Read after the stamp was taken: a write between the two is
            # seen as a change next time.
            game, keys = read(Path(path))
            _forget(con, path)
            con.execute(
                "insert into files (path, kind, folder, mtime_ns, size, game)"
                " values (?, ?, ?, ?, ?, ?)",
                (path, kind, str(folder), stamp[0], stamp[1], game),
            )
            con.executemany(
                "insert into mentions (path, key) values (?, ?)",
                [(path, key) for key in sorted(keys)],
            )


def _forget(con: sqlite3.Connection, path: str) -> None:
    con.execute("delete from mentions where path = ?", (path,))
    con.execute("delete from files where path = ?", (path,))


# -- the database ------------------------------------------------------------
Answer = Dict[str, Set[str]]


def _answer(ask: Callable[[sqlite3.Connection], Answer]) -> Answer:
    """``ask`` put to the cache, and where the cache will not take it, to
    one built in memory from the files."""
    where = places.files_cache()
    for _attempt in range(2):
        try:
            con = _open(where)
        except _Newer:
            break
        except (sqlite3.Error, OSError) as exc:
            if _busy(exc) or not _discard(where):
                break
            continue
        try:
            with contextlib.closing(con):
                return ask(con)
        except sqlite3.Error as exc:
            if _busy(exc) or not _discard(where):
                break
    with contextlib.closing(_built(sqlite3.connect(":memory:"))) as con:
        return ask(con)


class _Newer(Exception):
    """The cache was built by a newer Studio, whose it is to change."""


def _open(where: Path) -> sqlite3.Connection:
    """The cache at ``where``, made where there is none.  Raises
    :class:`sqlite3.Error` for one that is not a cache this build reads,
    and :class:`_Newer` for one a newer build made."""
    where.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(where), timeout=BUSY_SECONDS)
    try:
        if not con.execute(
            "select 1 from sqlite_master where name = 'meta'"
        ).fetchone():
            if con.execute("select count(*) from sqlite_master").fetchone()[0]:
                raise sqlite3.DatabaseError("not the Studio's files cache")
            return _built(con)
        row = con.execute(
            "select value from meta where key = 'schema_version'"
        ).fetchone()
        try:
            schema = int(row[0]) if row else 0
        except (TypeError, ValueError):
            schema = 0
        if schema > SCHEMA_VERSION:
            raise _Newer()
        if schema < SCHEMA_VERSION:
            raise sqlite3.DatabaseError("an older files cache")
    except BaseException:
        con.close()
        raise
    return con


def _built(con: sqlite3.Connection) -> sqlite3.Connection:
    with con:
        con.executescript(_SCHEMA_SQL)
        con.execute(
            "insert into meta (key, value) values ('schema_version', ?)",
            (str(SCHEMA_VERSION),),
        )
    return con


def _busy(exc: Exception) -> bool:
    """Whether ``exc`` says another Studio is writing the cache, which is
    not this one's to delete."""
    return getattr(exc, "sqlite_errorcode", None) in _BUSY_CODES


def _discard(where: Path) -> bool:
    """Delete the cache and its journal, so it is built again; whether it
    went.  A cache somebody has open cannot be deleted on Windows."""
    for path in (where, Path(str(where) + "-journal")):
        try:
            os.remove(path)
        except FileNotFoundError:
            continue
        except OSError:
            return False
    return True
