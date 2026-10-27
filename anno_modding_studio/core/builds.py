"""Build numbers: which output of a mod this is.

The version is the author's to set, and says what a release means.  The build
number is the Studio's, and says only which set of files this is: it goes up
by one each time a mod of yours is written out *and what is written differs
from the last time*.  So two copies of 1.2.0 in a mods folder, one from before
lunch and one from after, can be told apart -- and exporting the same thing
twice does not pretend to be a new build.

What counts as different is what the game would do differently, not every
byte.  Comments are left out: a note, a heading, and what is switched off,
which is written as a comment and changes nothing in game.  The
modinfo counts, less the build number itself, which is decided after the
comparison -- and so does what it says the mod was built against, since a
mod rebuilt against a patched game is a different build of it.  Files the mod
ships besides the two the Studio writes count by their contents.

Only mods that are yours are numbered: the one being authored here, one of
yours read back in to carry on with, and your patches.  Somebody else's mod,
written back out as part of a stack, keeps its modinfo as its author left it.

See ``docs/proposals/versioning-proposal.md``, section 2.2.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from . import disk, games, modfolder, modinfo

_COMMENT = re.compile(r"<!--.*?-->", re.S)
# Read in chunks: a mod can ship textures of a few hundred megabytes.
_CHUNK = 4 * 1024 * 1024
# Each file's digest, by its path, size and time written: the Export tab
# asks whether the library's build is behind each time it is drawn, and a
# mod's textures are read once, not each time.
_FILE_DIGESTS: dict = {}


@dataclass(frozen=True)
class Build:
    """What writing a mod out now would be: its number, and what it hashed to."""

    origin: str
    number: int
    digest: str
    # Whether this is a new build or the last one written again.
    new: bool
    # The game data it is built against: the assets.xml's fingerprint and the
    # update it came from, so a later look can tell the game has moved on.
    assets: str = ""
    update: str = ""


def is_numbered(loadout, origin: str) -> bool:
    """Whether writing out ``origin`` gives it a build number: any mod of
    yours a project keeps, in whichever loadout it is built.  Built from a
    loadout that has it beside others, a mod of yours went into the library
    with no number and no Studio block, and every later build wrote it
    again (R3-25, hands-on run 3)."""
    if not origin:
        return True
    if loadout.home and origin == loadout.home:
        return True
    if origin in loadout.projects:
        return True
    layer = loadout.layer(origin)
    return layer is not None and bool(layer.patches)


def number(loadout, origin: str = "") -> int:
    """The last build of ``origin`` written out, or 0 if it has none yet.

    A mod of yours read back in with no record of its builds here is the
    build its own modinfo says it is: the one :func:`plan` carries on from.
    Read off the loadout alone, the Export tab said "no builds yet" over a
    mod whose modinfo said build 1, and the next export wrote build 2
    (H2-14)."""
    held = loadout.builds.get(origin) or {}
    last = int(held.get("build") or 0)
    if last or not origin:
        return last
    layer = loadout.layer(origin)
    return declared_build(layer.declared if layer is not None else None)


def plan(loadout, origin: str, exported, xml_text: str, carry_from=None) -> Optional[Build]:
    """The build writing ``exported`` out would be, without recording it.

    ``exported`` is the view :func:`generator.for_mod` made for ``origin``;
    its ``build`` is set to the answer, so the modinfo written from it says
    so.  None for a mod that is not numbered.
    """
    if not is_numbered(loadout, origin):
        exported.build = 0
        return None
    exported.build = 0
    digest = content_digest(exported, xml_text, carry_from)
    held = loadout.builds.get(origin) or {}
    last = int(held.get("build") or 0) or declared_build(modinfo.as_written(exported))
    against = (
        getattr(exported, "base_fingerprint", ""),
        getattr(exported, "base_update", ""),
    )
    if held.get("digest") == digest and last:
        build = Build(origin, last, digest, False, *against)
    else:
        build = Build(origin, last + 1, digest, True, *against)
    exported.build = build.number
    return build


def record(loadout, build: Optional[Build]) -> bool:
    """Keep ``build`` as the last one written; whether its number moved.

    The same build written again still says what it was written against: a
    mod with no modinfo can come out byte for byte the same on patched game
    data, and it has been checked against that data all the same.
    """
    if build is None:
        return False
    held = loadout.builds.get(build.origin)
    if not build.new and held is not None:
        held["assets"], held["update"] = build.assets, build.update
        return False
    loadout.builds[build.origin] = {
        "build": build.number,
        "digest": build.digest,
        "assets": build.assets,
        "update": build.update,
    }
    return build.new


def stale(loadout, origin: str = "") -> Optional[dict]:
    """The last build of ``origin``, if it was made on other game data than now.

    None when it was made on this data, when either side is unknown, and
    before the first build.  The game being patched is what this catches: a
    mod's patches find their nodes by paths into the game's own assets, and a
    patch can move them.
    """
    held = loadout.builds.get(origin) or {}
    was, now = held.get("assets") or "", getattr(loadout, "base_fingerprint", "")
    if held.get("build") and was and now and was != now:
        return held
    return None


def content_digest(exported, xml_text: str, carry_from=None) -> str:
    """What identifies a mod's output, everything that is not a comment."""
    digest = hashlib.sha1()
    body = _COMMENT.sub("", xml_text or "")
    body = "\n".join(line.rstrip() for line in body.splitlines() if line.strip())
    digest.update(b"patch\0")
    digest.update(body.encode("utf-8"))
    # Asked with no build, which leaves what it was built against out of the
    # modinfo too -- so that goes in by itself.  And with the Studio's block
    # as it is with no build: with nothing to say, the modinfo kept the one a
    # mod of yours was read back in with, its old build and Studio and all
    # (A11-33).
    info = json.loads(modinfo.build(exported))
    info.pop(modinfo.STUDIO_KEY, None)
    block = modinfo.studio_block(exported)
    if block:
        info[modinfo.STUDIO_KEY] = block
    digest.update(b"\0modinfo\0")
    digest.update(json.dumps(info, indent=2, ensure_ascii=False).encode("utf-8"))
    digest.update(b"\0against\0")
    digest.update(json.dumps(modinfo.built_against(exported), sort_keys=True).encode("utf-8"))
    if carry_from:
        from . import generator

        source = Path(carry_from)
        for inside in generator.carried_files(source, exported.game):
            digest.update(b"\0file\0")
            digest.update(inside.as_posix().lower().encode("utf-8"))
            digest.update(b"\0")
            digest.update(_file_digest(source / inside))
    return digest.hexdigest()


def _file_digest(path: Path) -> bytes:
    try:
        stat = os.stat(disk.reachable(path))
        key = (str(path).lower(), stat.st_size, stat.st_mtime_ns)
    except OSError:
        key = None
    if key is not None and key in _FILE_DIGESTS:
        return _FILE_DIGESTS[key]
    inner = hashlib.sha1()
    try:
        with open(disk.reachable(path), "rb") as handle:
            for chunk in iter(lambda: handle.read(_CHUNK), b""):
                inner.update(chunk)
    except OSError:
        # Unreadable here is uncopyable there, and the export reports it; what
        # the build number needs is only that it counts as something.
        return b"unreadable"
    if key is not None:
        _FILE_DIGESTS[key] = inner.digest()
    return inner.digest()


def declared_against(declared) -> dict:
    """What a mod's own modinfo says it was built against, or {}.

    Any mod the Studio built says so, whoever built it -- which is what lets a
    mods folder be asked which of its mods were built on game data older than
    what is installed now.
    """
    block = (declared or {}).get(modinfo.STUDIO_KEY)
    against = block.get(modinfo.BUILT_AGAINST_KEY) if isinstance(block, dict) else None
    return dict(against) if isinstance(against, dict) else {}


def built_on_other_data(declared, game: str, fingerprint: str) -> Optional[bool]:
    """Whether a mod was built on other data of ``game`` than ``fingerprint``.

    None when that cannot be said: the mod does not record it, records it for
    another game, or there is no fingerprint to compare with.
    """
    against = declared_against(declared)
    recorded = str(against.get(modinfo.AGAINST_ASSETS) or "")
    if not recorded or not fingerprint or against.get(modinfo.AGAINST_GAME) != games.get(game).label:
        return None
    return recorded != modinfo.ASSETS_HASH + fingerprint


def updated_on_disk(loadout, layer, folder) -> bool:
    """Whether the mod in ``folder`` is no longer the copy ``layer`` was read
    from, by what its modinfo says now.

    A copy the Studio built says its build (:func:`declared_build`), and
    where the folder says one, that decides: a rebuild under the same
    version is caught, and a build ``loadout`` wrote itself is no update.
    Otherwise the version, where the folder and the reading both say one: a
    mod that never names its version cannot be told apart this way.
    """
    if layer is None:
        return False
    now = modfolder.metadata_of(folder)
    built = declared_build(now.get("declared"))
    if built:
        return built not in {declared_build(layer.declared), number(loadout, layer.origin)}
    version = str(now.get("version") or "").strip()
    # What its modinfo said, and not the version shown: that one is yours to
    # edit on the Export tab where the mod is yours.
    was = modfolder.declared_version(layer.declared) if layer.declared else layer.version.strip()
    return bool(version) and bool(was) and version != was


def declared_build(declared) -> int:
    """The build number a mod's own modinfo says it is, or 0.

    Read when a mod of yours comes back in without a record of its builds --
    a loadout started afresh around a mod you exported before -- so that its
    numbering carries on rather than starting again at one.
    """
    block = (declared or {}).get(modinfo.STUDIO_KEY)
    if not isinstance(block, dict):
        return 0
    try:
        return max(0, int(block.get(modinfo.BUILD_KEY) or 0))
    except (TypeError, ValueError):
        return 0
