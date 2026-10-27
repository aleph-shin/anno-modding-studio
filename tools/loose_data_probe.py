"""Does Anno 1800 apply a mod whose folder holds its data tree loose?

On the machine this was written on, Anno 1800's mods folder holds a folder
called ``data`` whose contents are ``config/export/main/asset/assets.xml``: a
mod's own ``data`` tree, extracted straight into ``mods`` from a zip that had
no folder of its own.  The loader's log lists it -- ``Load data (unversioned,
data) from mods\\data`` -- but the loader writes nothing for a ModOp that
works, so the log cannot say whether its ops ran.  A normal mod keeps its
patch at ``<mod>/data/config/...``; this one's is at ``<mod>/config/...``,
one level up from there.

The game has to be started to find out.  This puts three small, obviously
named folders in the mods folder, each a question the log can answer:

    [Test] Loose config (Studio)   config/export/main/asset/assets.xml, no
                                   data folder and no modinfo -- the same
                                   shape as the stray ``data`` folder
    [Test] Proper (Studio)         data/config/export/main/asset/assets.xml
                                   -- a normal mod, the control
    [Test] Empty (Studio)          a text file and nothing else

Each patch holds one ModOp aimed at a GUID that does not exist, so a loader
that runs it writes "No matching node" naming that GUID.  Afterwards:

    loaded and warned     its ops run: the loader applies that shape
    loaded, no warning    listed, but nothing inside it runs
    not in the log        the loader does not see that shape as a mod

and the empty folder says whether the loader lists any folder at all.

It tests the shape, not the name: the stray folder already holds the name
``data``, and nothing here touches it.  If the empty folder is listed, the
loader lists every folder whatever it is called, and the name is unlikely to
matter; if it is not, the answer for the loose one still holds for the shape.

    python tools/loose_data_probe.py              # set up, then start Anno 1800
    python tools/loose_data_probe.py --check      # after the main menu
    python tools/loose_data_probe.py --clean      # take it all back out

Anno 1800 only: that is where the stray folder is, and 117's profiles decide
by ModID, which a folder with no modinfo does not have.  Nothing else in the
mods folder is touched -- the ``data`` folder least of all -- and --clean
removes exactly what setup made.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from anno_modding_studio.core import discovery, loaderlog
from anno_modding_studio.core.games import ANNO_1800

PATCH = Path("config") / "export" / "main" / "asset" / "assets.xml"
# (folder, where its patch goes, a GUID nothing has, what it tests)
PROBES = (
    ("[Test] Loose config (Studio)", PATCH, "1999000101", "the stray data folder's shape"),
    ("[Test] Proper (Studio)", Path("data") / PATCH, "1999000102", "a normal mod, the control"),
    ("[Test] Empty (Studio)", None, "", "a folder with nothing to load"),
)
MARKER = "made-by-loose-data-probe.txt"


def mods_folder(chosen: str) -> Path:
    if chosen:
        return Path(chosen)
    for install in discovery.find_game_installs():
        if "1800" in str(install.path):
            candidate = Path(install.path) / "mods"
            if candidate.is_dir():
                return candidate
    raise SystemExit("no Anno 1800 mods folder found; pass --mods")


def patch(guid: str) -> str:
    return (
        "<ModOps>\n"
        "  <!-- loose_data_probe: a GUID nothing has, so running this op logs a warning -->\n"
        '  <ModOp Type="merge" GUID="%s" Path="/Values/Standard">\n'
        "    <Name>loose data probe</Name>\n"
        "  </ModOp>\n"
        "</ModOps>\n" % guid
    )


def setup(mods: Path) -> None:
    for folder, where, guid, what in PROBES:
        home = mods / folder
        if home.exists():
            print("already there:", home)
            continue
        home.mkdir()
        (home / MARKER).write_text("Made by tools/loose_data_probe.py; --clean removes it.\n", encoding="utf-8")
        if where is not None:
            target = home / where
            target.parent.mkdir(parents=True)
            target.write_text(patch(guid), encoding="utf-8")
        print("made: %s  (%s)" % (home, what))
    log = loaderlog.log_file(ANNO_1800)
    stamp = time.strftime("%d %b %H:%M", time.localtime(log.stat().st_mtime)) if log is not None else "none yet"
    print()
    print("The loader's log was last written %s." % stamp)
    print("Now start Anno 1800, wait for the main menu, quit, and run with --check.")


def check(mods: Path) -> None:
    log = loaderlog.read(ANNO_1800)
    if log is None:
        raise SystemExit("no loader log found; has Anno 1800 been started since setup?")
    text = log.path.read_text(encoding="utf-8", errors="replace")
    made = min((mods / folder).stat().st_mtime for folder, *_rest in PROBES if (mods / folder).exists())
    print("log:", log.path)
    print("game started:", time.strftime("%d %b %H:%M:%S", time.localtime(log.started)),
          "" if log.complete else "(still loading, or it stopped early)")
    if log.started < made - 1:
        print("RESULT: this log is from before setup -- start the game, then check again.")
        return
    loaded = {mod.folder.lower() for mod in log.mods}
    for folder, _where, guid, what in PROBES:
        listed = folder.lower() in loaded
        warned = bool(guid) and guid in text
        if not listed:
            verdict = "not in the log: the loader does not see it as a mod"
        elif warned:
            verdict = "loaded, and its op ran (the log warns about %s)" % guid
        elif guid:
            verdict = "loaded, but its op did not run"
        else:
            verdict = "loaded, though there is nothing in it"
        print("  %-32s %s -- %s" % (folder, verdict, what))
    loose = next(folder for folder, *_rest in PROBES if "Loose" in folder)
    proper = next(folder for folder, *_rest in PROBES if "Proper" in folder)
    loose_ran = PROBES[0][2] in text and loose.lower() in loaded
    proper_ran = PROBES[1][2] in text and proper.lower() in loaded
    print()
    if not proper_ran:
        print("RESULT: the control did not run either, so this start says nothing; is the")
        print("        game loading mods from this folder at all?")
    elif loose_ran:
        print("RESULT: a loose config tree RUNS. The stray `data` folder's changes are")
        print("        in the game. The Studio should read <mod>/config/... as that mod's")
        print("        patch, and the card should say the folder is active, not unknown.")
    else:
        print("RESULT: a loose config tree does NOT run. The stray `data` folder is listed")
        print("        as a mod and does nothing; the card can say it is inert.")


def clean(mods: Path) -> None:
    import shutil

    for folder, *_rest in PROBES:
        home = mods / folder
        if (home / MARKER).is_file():
            shutil.rmtree(home)
            print("removed:", home)
        elif home.exists():
            print("left alone (no probe marker in it):", home)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--mods", default="", help="Anno 1800's mods folder, if it is not found")
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--clean", action="store_true")
    args = parser.parse_args()
    mods = mods_folder(args.mods)
    print("mods folder:", mods)
    if args.clean:
        clean(mods)
    elif args.check:
        check(mods)
    else:
        setup(mods)
    return 0


if __name__ == "__main__":
    sys.exit(main())
