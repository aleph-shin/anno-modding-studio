"""Generated copies: a mod as its author shipped it, with the Studio's own
files written over it.

The library keeps every mod exactly as it was installed, and nothing the
Studio does to a mod is written into that copy.  Where the game is to load a
mod with something of the Studio's in it -- the player's iModYourAnno
settings, for now -- Play writes a **generated copy** beside the library and
points the mod's link in the mods folder at it in place of the library copy.
The copy is the author's mod and the Studio's files, nothing else, and it is
built again from the library copy whenever what goes into it changes, so it
can be deleted at any time and nobody loses anything.  See
docs/proposals/patches-proposal.md, phase 1.

What a copy is made of, file by file:

* a file the Studio writes is written;
* a text file of the author's is copied: small, and what a tool that writes
  into a mod folder in place writes into -- iModYourAnno among them -- so a
  hard link would carry that write through into the library's copy;
* any other file is a hard link to the library's, which costs nothing, or a
  copy where a hard link cannot be made.

Every folder is a real folder.  A junction inside a junctioned mod would save
the links, but whether each game's loader follows one when it walks a mod is
not known, and a hard link is an ordinary file to anything that reads it.

Each copy holds :data:`MANIFEST`, saying what it was made from and which of
its files are not the author's, so anybody -- another tool included -- can
tell what it is looking at, and :func:`check` can say whether it is still
exactly that.

No other core module is imported but :mod:`core.disk`, so the library can
ask what a link points at without a cycle.
"""

from __future__ import annotations

import hashlib
import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

from . import disk

# Beside the library's folder for each game, under the same root: one volume,
# so a hard link can always be made, and apart from the copies people wrote.
FOLDER = "generated"
MANIFEST = "generated.json"
# Which version of what the manifest says, for anybody reading one.
CONVENTION = 1
TOOL = "Anno Modding Studio"
# Copied rather than linked; see the module's docstring.
TEXT = frozenset({".xml", ".json", ".jsonc", ".txt", ".lua", ".md", ".ini", ".cfg", ".fc"})


def root_for(library) -> Path:
    """Where the generated copies of the mods in ``library`` -- one game's
    folder in the library -- are written."""
    library = Path(library)
    return library.parent / FOLDER / library.name


def folder_for(library, name: str) -> Path:
    """The generated copy of the library's copy called ``name``."""
    return root_for(library) / name


# -- what a copy says of itself ------------------------------------------------------
def manifest(folder) -> dict:
    """What a generated copy says it is, or {} for a folder that is not one."""
    found = disk.read_json(Path(folder) / MANIFEST, {})
    return found if isinstance(found, dict) and found.get("source") else {}


def source_of(folder) -> Optional[Path]:
    """The library copy a generated copy was made from, or None for a folder
    that is not one."""
    found = manifest(folder)
    return Path(found["source"]) if found else None


def author_folder(path) -> Path:
    """``path`` as its author shipped it: where ``path`` is, or is inside, a
    generated copy -- a mod's link in the mods folder points at one once Play
    has written into it, and a mod bundled in it sits below -- the same place
    in the library copy that copy was made from; any other path as it is.

    What a mod is read from.  A copy holds the Studio's own files -- Skip on
    the ops an edit switched off, iModYourAnno's settings -- and read as the
    mod, those are taken for the author's: an op switched off by a patch
    reads as one Kurila shipped off, and the patch, with nothing left to
    switch off, loses its edit at the next save (R3-27)."""
    path = Path(path)
    try:
        resolved = path.resolve()
    except (OSError, ValueError):
        return path
    for top in (resolved, *resolved.parents):
        found = manifest(top)
        if found:
            return Path(found["source"]).joinpath(*resolved.relative_to(top).parts)
    return path


def is_generated(folder, library) -> bool:
    """Whether ``folder`` is a generated copy of one of ``library``'s mods."""
    return disk.inside(folder, root_for(library)) and bool(manifest(folder))


# -- making one ----------------------------------------------------------------------
@dataclass
class Made:
    """What building a copy did: where it is, whether it was built again or
    was already what was asked for, and how its files came to be there."""

    folder: Path
    built: bool = False
    written: int = 0
    copied: int = 0
    linked: int = 0
    # Files that are not as the manifest says, after it was built: see check.
    wrong: List[str] = field(default_factory=list)


def _sha1(data: bytes) -> str:
    return hashlib.sha1(data, usedforsecurity=False).hexdigest()


def _files(source: Path) -> List[str]:
    """Every file under ``source``, by its path from it with forward slashes."""
    found = []
    for top, _dirs, files in os.walk(source):
        for name in files:
            found.append((Path(top) / name).relative_to(source).as_posix())
    return sorted(found)


def fingerprint(source, written: Dict[str, bytes]) -> str:
    """What a copy of ``source`` with ``written`` over it is made from: each
    of the author's files by name, size and time, and each written file by
    its contents.  The same fingerprint, the same copy."""
    source = Path(source)
    digest = hashlib.sha1(usedforsecurity=False)
    digest.update(b"%d\n" % CONVENTION)
    for rel in _files(source):
        found = (source / rel).stat()
        digest.update(("%s\0%d\0%d\n" % (rel, found.st_size, found.st_mtime_ns)).encode("utf-8"))
    for rel in sorted(written):
        digest.update(("%s\0%s\n" % (rel, _sha1(written[rel]))).encode("utf-8"))
    return digest.hexdigest()


def is_current(folder, source, written: Dict[str, bytes]) -> bool:
    """Whether ``folder`` is already the copy :func:`build` would make."""
    found = manifest(folder)
    return bool(found) and found.get("fingerprint") == fingerprint(source, written)


def build(source, folder, written: Dict[str, bytes], edits=(), meta=None) -> Made:
    """Make ``folder`` a copy of ``source`` with ``written`` over it: each
    written file by its path from the top of the mod, with forward slashes.

    ``edits`` say what put the written files there, for the manifest;
    ``meta`` is what the mod is (ModID, version).  A copy already made of the
    same is left as it is.  Made beside the old one and moved into its place,
    so a link to it never points at half a copy.  The new copy is checked
    against what it was made of before it is moved in: one that does not
    match is left aside and said, in :attr:`Made.wrong`.
    """
    source, folder = Path(source), Path(folder)
    made_of = fingerprint(source, written)
    done = Made(folder=folder)
    if manifest(folder).get("fingerprint") == made_of and not check(folder):
        return done
    folder.parent.mkdir(parents=True, exist_ok=True)
    making = folder.with_name(".%s.%d.part" % (folder.name, os.getpid()))
    if making.exists():
        shutil.rmtree(making)
    try:
        for rel in _files(source):
            into = making / rel
            into.parent.mkdir(parents=True, exist_ok=True)
            if rel in written:
                into.write_bytes(written[rel])
                done.written += 1
            elif Path(rel).suffix.lower() in TEXT:
                shutil.copy2(source / rel, into)
                done.copied += 1
            else:
                try:
                    os.link(source / rel, into)
                    done.linked += 1
                except OSError:
                    shutil.copy2(source / rel, into)
                    done.copied += 1
        for rel, data in written.items():
            into = making / rel
            if not into.exists():
                into.parent.mkdir(parents=True, exist_ok=True)
                into.write_bytes(data)
                done.written += 1
        disk.write_json(making / MANIFEST, {
            "convention": CONVENTION,
            "tool": TOOL,
            "source": str(source),
            "mod": dict(meta or {}),
            "fingerprint": made_of,
            "written": {rel: _sha1(written[rel]) for rel in sorted(written)},
            "edits": list(edits),
        })
        done.wrong = check(making)
        if done.wrong:
            return done
        if folder.exists():
            old = folder.with_name(".%s.%d.old" % (folder.name, os.getpid()))
            os.replace(folder, old)
            os.replace(making, folder)
            shutil.rmtree(old, ignore_errors=True)
        else:
            os.replace(making, folder)
    finally:
        if making.exists():
            shutil.rmtree(making, ignore_errors=True)
    done.built = True
    return done


def check(folder) -> List[str]:
    """What in a generated copy is not as its manifest says, by path from the
    top of the mod: a file of the author's missing or different from the
    library's, a written file not as written, or a file nothing put there.
    Empty for a copy that is exactly what it says.  The check made after
    every build, so that what the game reads is what the Studio said it
    would be, and not only what it meant to write."""
    folder = Path(folder)
    found = manifest(folder)
    if not found:
        return [MANIFEST]
    source = Path(found["source"])
    written = dict(found.get("written") or {})
    wrong = []
    theirs = set(_files(source)) if source.is_dir() else set()
    for rel in sorted(theirs | set(written)):
        here = folder / rel
        if not here.is_file():
            wrong.append(rel)
            continue
        if rel in written:
            if _sha1(here.read_bytes()) != written[rel]:
                wrong.append(rel)
            continue
        there = source / rel
        try:
            if os.path.samefile(here, there):
                continue
            if here.stat().st_size != there.stat().st_size or here.read_bytes() != there.read_bytes():
                wrong.append(rel)
        except OSError:
            wrong.append(rel)
    known = theirs | set(written) | {MANIFEST}
    wrong.extend(rel for rel in _files(folder) if rel not in known)
    return wrong


def remove(folder) -> None:
    """Delete a generated copy.  Only one: anything else is left alone."""
    folder = Path(folder)
    if manifest(folder):
        shutil.rmtree(folder, ignore_errors=True)


def materialise(folder) -> None:
    """Make a generated copy a folder that stands on its own: its manifest
    goes, and with it everything that says it is generated.  Its files are
    already its own -- a hard link is a file, whichever name is deleted."""
    with_manifest = Path(folder) / MANIFEST
    if with_manifest.is_file():
        with_manifest.unlink()
