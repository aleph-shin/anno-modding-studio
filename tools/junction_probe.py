"""Does the game load a mod through a directory junction?

The one question the loadouts proposal's phase 4 -- a library of mods outside
the mods folder, junctioned in per loadout -- waits on.  Nothing in the API
answers it; the game has to be started.  So this puts a tiny, obviously-named
mod in a library folder of the Studio's own, makes a junction to it inside the
game's mods folder, and afterwards reads the loader's own log to see whether
the game took it, and by which path.

    python tools/junction_probe.py anno1800            # set up, then start the game
    python tools/junction_probe.py anno1800 --check    # after the game reached the menu
    python tools/junction_probe.py anno1800 --clean    # take it all back out

The mod does nothing: a modinfo and an empty data folder, enough to be listed.
A junction needs no administrator rights, unlike a symbolic link, which is why
it is the link tried.  For Anno 117 the live profile has to list the mod's id
if it has EnableNewMods commented out; the setup says so and does it.

Nothing else in the mods folder is touched, and --clean removes exactly what
setup made: the junction, the library folder, and the profile line.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from anno_modding_studio.core import (  # noqa: E402
    discovery,
    games,
    loaderlog,
    places,
    switches,
)
from anno_modding_studio.core.games import ANNO_117, ANNO_1800  # noqa: E402

# Dashes, not underscores: 117 refuses any other ModID outright.
MOD_ID = "studio-junction-probe"
FOLDER = "[Test] Junction Probe (Studio)"


def mods_folder(game: str, chosen: str) -> Path:
    if chosen:
        return Path(chosen)
    for install in discovery.find_game_installs():
        if (game == ANNO_1800) == ("1800" in str(install.path)):
            candidate = Path(install.path) / "mods"
            if candidate.is_dir():
                return candidate
    documents = switches.documents_mods(game)
    if documents is not None:
        return documents
    raise SystemExit("no mods folder found; pass --mods")


def library(game: str) -> Path:
    return places.studio_home() / "library" / game / FOLDER


def probe_modinfo(game: str) -> str:
    """The modinfo the Studio itself would write for this game -- 117 refuses
    one without its "Anno": 8, which a hand-written one once lacked."""
    from anno_modding_studio.core import modinfo
    from anno_modding_studio.core.changes import Loadout, own_record

    loadout = Loadout(
        game=game,
        own=own_record(
            name="Junction Probe (Studio)",
            mod_id=MOD_ID,
            category="Misc",
            creator="Anno Modding Studio",
            description="Loaded through a directory junction, if you can read this.",
        ),
    )
    return modinfo.build(loadout)


def make_junction(link: Path, target: Path) -> None:
    import _winapi

    _winapi.CreateJunction(str(target), str(link))


def is_junction(path: Path) -> bool:
    try:
        return path.is_junction()
    except AttributeError:  # Python < 3.12
        import os

        return bool(os.lstat(path).st_file_attributes & 0x400)


def setup(game: str, mods: Path) -> None:
    home = library(game)
    (home / "data").mkdir(parents=True, exist_ok=True)
    (home / "modinfo.json").write_text(probe_modinfo(game), encoding="utf-8")
    link = mods / FOLDER
    if link.exists() or is_junction(link):
        print("already there:", link)
    else:
        make_junction(link, home)
        print("junction:", link, "->", home)
    if game == ANNO_117:
        profile = switches.profile_for(mods, game)
        if profile is not None:
            read = switches.read_profile(profile)
            if not read.new_mods_on and read.on.get(MOD_ID) is None:
                profile.write_text(
                    switches.rewrite_profile(read, {MOD_ID: True}), encoding="utf-8"
                )
                print("listed %s in %s (EnableNewMods is off there)" % (MOD_ID, profile))
            elif read.on.get(MOD_ID) is False:
                print("NOTE: %s is switched off in %s; switch it on first" % (MOD_ID, profile))
    print()
    print("Now start %s, wait for the main menu, quit, and run with --check."
          % games.get(game).label)


def check(game: str, mods: Path) -> None:
    home = games.documents_dir(game)
    for relative in loaderlog.LOG_FILES:
        log = home / relative
        if not log.is_file():
            continue
        text = log.read_text(encoding="utf-8", errors="replace")
        needles = (MOD_ID, MOD_ID.replace("-", "_"), FOLDER)
        lines = [line for line in text.splitlines() if any(n in line for n in needles)]
        print("log:", log)
        if not lines:
            print("RESULT: the log does not mention the probe at all -- the game did NOT")
            print("        load it through the junction (or the log is from before setup).")
            mtime = log.stat().st_mtime
            import time

            print("        log written:", time.strftime("%d %b %H:%M", time.localtime(mtime)))
            return
        for line in lines[:8]:
            print("  ", line.strip())
        loaded = [line for line in lines if re.search(r"\bLoad\b", line)]
        if loaded:
            by_target = any(str(library(game)).lower() in line.lower() for line in loaded)
            print("RESULT: loaded through the junction.")
            print("        The log names the %s." % ("target folder (the library)" if by_target else "junction's own path"))
        else:
            print("RESULT: mentioned but not marked as loaded -- read the lines above.")
        return
    print("no loader log found under", home)


def clean(game: str, mods: Path) -> None:
    link = mods / FOLDER
    if is_junction(link):
        link.rmdir()  # removes the junction, never the target
        print("removed junction:", link)
    home = library(game)
    if home.is_dir():
        import shutil

        shutil.rmtree(home)
        print("removed library folder:", home)
    if game == ANNO_117:
        profile = switches.profile_for(mods, game)
        if profile is not None:
            kept = [
                line for line in profile.read_text(encoding="utf-8").splitlines()
                # The game itself appends "# not installed" to a line it
                # could not resolve, so the id is matched at the front.
                if line.strip().lstrip("#").strip().lower().split()[:1]
                not in ([MOD_ID], [MOD_ID.replace("-", "_")])
            ]
            profile.write_text("\n".join(kept) + "\n", encoding="utf-8")
            print("unlisted %s in %s" % (MOD_ID, profile))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("game", choices=list(games.GAMES))
    parser.add_argument("--mods", default="", help="the mods folder to junction into")
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--clean", action="store_true")
    args = parser.parse_args()
    mods = mods_folder(args.game, args.mods)
    print("mods folder:", mods)
    if args.clean:
        clean(args.game, mods)
    elif args.check:
        check(args.game, mods)
    else:
        setup(args.game, mods)
    return 0


if __name__ == "__main__":
    sys.exit(main())
