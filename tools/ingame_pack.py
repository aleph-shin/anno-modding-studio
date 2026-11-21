r"""Make, install, judge and take away an in-game test pack.

    python tools/ingame_pack.py new <folder> --game anno117 --title "..."
    python tools/ingame_pack.py readme <folder>
    python tools/ingame_pack.py install <folder>
    python tools/ingame_pack.py check <folder> [--log <mod-loader.log>]
    python tools/ingame_pack.py remove <folder>

Some changes are only known to work once the game has loaded what the Studio
wrote. The maintainer runs those on the PC the game is played on, so a pack is
made to take minutes: ``docs/standards/pr-checklist.md``, "Verified in the
game", says when one is worth making.

A pack is a folder, kept in the companion repository's ``in-game/``:

- ``pack.json``: the game, the title, the PR, what to do past the main menu,
  and the questions, each with the answer expected (:data:`KINDS`);
- ``mods/``: the probe mods, made by the pack's own ``make_test.py`` with
  :func:`add_mod` and :func:`marker` / :func:`pair`;
- ``README.txt``: what to do, written from ``pack.json`` by ``readme``;
- after a run, ``RESULT.txt`` (``check``), and while installed,
  ``installed.json`` and the Anno 117 profile as it was.

Questions the loader's log answers are preferred: a marker is a ModOp that
matches nothing, so the loader logs it under the mod that ran it, and reaching
the main menu is the whole run. A question only play can answer is ``eye``:
``check`` leaves a line for what was seen.

``install`` copies the mods into the game's own ``mods`` folder and, for Anno
117, adds their ModIDs to the live profile, keeping it as it was beside the
pack; ``remove`` takes away only what ``install`` put there and puts the
profile back. Start the game from Steam, not the Studio's Play, which rewrites
the profile. Run from a checkout of the Studio: it finds the game as the
Studio does.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

PACK = "pack.json"
INSTALLED = "installed.json"
PROFILE_BACKUP = "profile.before.txt"
# What a question expects, and how it is answered:
#   "ran" / "not run"  one marker, logged or not;
#   "yes" / "no"       a pair (<case>_yes, <case>_no), of which one is logged;
#   "eye"              seen in play, written down by hand.
KINDS = ("ran", "not run", "yes", "no", "eye")


# -- making one -------------------------------------------------------------------
def prefix(pack: dict) -> str:
    """What every marker of the pack starts with: ``Pack_`` and its name."""
    return "Pack_" + re.sub(r"\W+", "_", pack["name"]).strip("_")


def marker(pack: dict, case: str, condition: str = "", guid: str = "") -> str:
    """A ModOp that matches nothing, so that the loader logs it, under the mod
    that ran it, as ``No matching node for Path "...<prefix>_<case>"``. With
    ``condition``, only where the condition holds; with ``guid``, on that
    asset, whose ``Values`` it is then under."""
    path = ("/Values/%s_%s" if guid else "/AssetList/%s_%s") % (prefix(pack), case)
    attributes = ' Type="merge"%s Path="%s"%s' % (
        ' GUID="%s"' % guid if guid else "", path,
        ' Condition="%s"' % condition.replace('"', "&quot;") if condition else "")
    return "  <ModOp%s>\n    <Marker>1</Marker>\n  </ModOp>\n" % attributes


def pair(pack: dict, case: str, path: str) -> str:
    """Two markers, of which the loader runs one: ``<case>_yes`` where
    ``path`` finds something, ``<case>_no`` where it does not."""
    return marker(pack, case + "_yes", path) + marker(pack, case + "_no", "!" + path)


def add_mod(folder: Path, pack: dict, name: str, mod_id: str, patch: str,
            category: str = "Test", load_after: Optional[List[str]] = None) -> Path:
    """A probe mod under ``mods/``: ``patch`` as its assets file, and the
    modinfo an export of the Studio writes."""
    from anno_modding_studio.core import games, generator, modinfo
    from anno_modding_studio.core.changes import Loadout, own_record

    loadout = Loadout(game=pack["game"], own=own_record(
        name=name, mod_id=mod_id, category=category, creator="Anno Modding Studio",
        description="An Anno Modding Studio test pack. Safe to delete.",
        load_after=list(load_after or [])))
    root = Path(folder) / "mods" / generator.mod_folder_name(loadout)
    target = root / games.get(pack["game"]).asset_relpath
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(patch, encoding="utf-8")
    (root / "modinfo.json").write_text(modinfo.build(loadout), encoding="utf-8")
    return root


def new(folder: Path, game: str, title: str, pr: str = "") -> None:
    folder.mkdir(parents=True, exist_ok=False)
    pack = {
        "name": folder.name, "title": title, "game": game, "pr": pr, "minutes": 3,
        "play": "",
        "questions": [{"case": "a_ran", "asks": "Did the probe mod run?", "expect": "ran"}],
    }
    (folder / PACK).write_text(json.dumps(pack, indent=2) + "\n", encoding="utf-8")
    (folder / "make_test.py").write_text(_MAKE_TEST, encoding="utf-8")
    readme(folder)


_MAKE_TEST = '''"""Build this pack's probe mods. Run from a checkout of the Studio:

    python <this folder>/make_test.py
"""

import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, "tools")
import ingame_pack as kit  # noqa: E402

HERE = Path(__file__).resolve().parent
PACK = json.loads((HERE / kit.PACK).read_text(encoding="utf-8"))

shutil.rmtree(HERE / "mods", ignore_errors=True)
patch = "<ModOps>\\n" + kit.marker(PACK, "a_ran") + "</ModOps>\\n"
print(kit.add_mod(HERE, PACK, "Probe A", "studio-probe-a", patch))
'''


def readme(folder: Path) -> str:
    """Write ``README.txt`` from ``pack.json``, and return it."""
    pack = _pack(folder)
    # Where the pack is on the PC it is run on: the companion's in-game/.
    here = "<companion>\\in-game\\%s" % Path(folder).name
    lines = [
        "%s" % pack["title"],
        "=" * len(pack["title"]),
        "",
        "Game: %s. About %d minutes.%s" % (_label(pack["game"]), pack.get("minutes", 3),
                                          " For %s." % pack["pr"] if pack.get("pr") else ""),
        "",
        "From a checkout of the Studio (any recent main), in a terminal, where",
        "<companion> is your checkout of the companion repository:",
        "",
        '1. python tools/ingame_pack.py install "%s"' % here,
        "2. Start the game from Steam (not the Studio's Play, which rewrites the",
        "   profile) and wait for the main menu.",
    ]
    step = 3
    if pack.get("play"):
        lines += ["%d. %s" % (step, pack["play"])]
        step += 1
    lines += [
        "%d. Quit the game." % step,
        '%d. python tools/ingame_pack.py check "%s"' % (step + 1, here),
        "   It writes RESULT.txt beside this file. Fill in any line marked ____.",
        '%d. python tools/ingame_pack.py remove "%s"' % (step + 2, here),
        "%d. Commit RESULT.txt in the companion repository." % (step + 3),
        "",
        "What it asks, and the answer expected:",
        "",
    ]
    lines += ["- %s  (%s)" % (question["asks"], question["expect"]) for question in pack["questions"]]
    text = "\n".join(lines) + "\n"
    (folder / "README.txt").write_text(text, encoding="utf-8")
    return text


# -- judging a run ----------------------------------------------------------------
def judge(pack: dict, log: str) -> List[Dict[str, str]]:
    """Each question with what the log says and whether it is as expected:
    ``as expected``, ``NOT as expected``, or ``by hand`` for ``eye``."""
    seen = set(re.findall(re.escape(prefix(pack)) + r'_(\w+)"', log))
    judged = []
    for question in pack["questions"]:
        case, expect = question["case"], question["expect"]
        if expect not in KINDS:
            raise ValueError("%s expects %r, not one of %s" % (case, expect, ", ".join(KINDS)))
        if expect == "eye":
            answer, verdict = "____", "by hand"
        elif expect in ("ran", "not run"):
            answer = "ran" if case in seen else "not run"
            verdict = "as expected" if answer == expect else "NOT as expected"
        else:
            yes, no = case + "_yes" in seen, case + "_no" in seen
            answer = {(True, False): "yes", (False, True): "no", (True, True): "both"}.get(
                (yes, no), "neither")
            verdict = "as expected" if answer == expect else "NOT as expected"
        judged.append({"case": case, "asks": question["asks"], "expect": expect,
                       "answer": answer, "verdict": verdict})
    return judged


def check(folder: Path, log_path: Optional[Path] = None) -> int:
    pack = _pack(folder)
    if log_path is None:
        from anno_modding_studio.core import loaderlog

        log_path = loaderlog.log_file(pack["game"])
    if log_path is None or not Path(log_path).is_file():
        print("no loader log found: start the game once, or pass --log")
        return 2
    installed = _installed(folder)
    stale = bool(installed) and Path(log_path).stat().st_mtime < installed.get("at", 0)
    judged = judge(pack, Path(log_path).read_text(encoding="utf-8", errors="replace"))
    lines = ["%s: result" % pack["title"], "",
             "Log: %s, written %s%s" % (log_path, time.strftime(
                 "%Y-%m-%d %H:%M", time.localtime(Path(log_path).stat().st_mtime)),
                 "  -- OLDER THAN THE INSTALL: the game has not run since" if stale else ""), ""]
    lines += ["- %s: %s (expected %s) -- %s" % (row["asks"], row["answer"], row["expect"], row["verdict"])
              for row in judged]
    text = "\n".join(lines) + "\n"
    (folder / "RESULT.txt").write_text(text, encoding="utf-8")
    print(text)
    return 1 if stale or any(row["verdict"] == "NOT as expected" for row in judged) else 0


# -- installing -------------------------------------------------------------------
def install(folder: Path) -> int:
    from anno_modding_studio.core import launch

    pack = _pack(folder)
    if _installed(folder):
        print("installed already: remove it first")
        return 1
    game_folder = launch.install_for(pack["game"])
    if game_folder is None:
        print("%s is not installed here" % _label(pack["game"]))
        return 2
    mods = game_folder / "mods"
    record: Dict[str, object] = {"at": time.time(), "mods": [], "profile": ""}
    for mod in sorted((folder / "mods").iterdir()):
        target = mods / mod.name
        if target.exists():
            print("%s is there already; nothing was installed" % target)
            return 1
    for mod in sorted((folder / "mods").iterdir()):
        shutil.copytree(mod, mods / mod.name)
        record["mods"].append(str(mods / mod.name))  # type: ignore[union-attr]
    profile = _profile(pack["game"])
    if profile is not None:
        shutil.copy2(profile, folder / PROFILE_BACKUP)
        ids = [_mod_id(mod) for mod in sorted((folder / "mods").iterdir())]
        # Added to in the line endings the file has, whichever they are.
        raw = profile.read_bytes()
        end = b"\r\n" if b"\r\n" in raw else b"\n"
        added = end.join(i.encode("utf-8") for i in ids if i)
        profile.write_bytes(raw.rstrip(b"\r\n") + end + added + end)
        record["profile"] = str(profile)
    (folder / INSTALLED).write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    print("installed %d mods into %s%s" % (len(record["mods"]), mods,  # type: ignore[arg-type]
                                           ", and listed in %s" % profile if profile else ""))
    return 0


def remove(folder: Path) -> int:
    record = _installed(folder)
    if not record:
        print("not installed")
        return 1
    for path in record["mods"]:
        if Path(path).is_dir():
            shutil.rmtree(path)
    if record.get("profile") and (folder / PROFILE_BACKUP).is_file():
        shutil.copy2(folder / PROFILE_BACKUP, record["profile"])
        (folder / PROFILE_BACKUP).unlink()
    (folder / INSTALLED).unlink()
    print("removed %d mods%s" % (len(record["mods"]), ", and the profile put back" if record.get("profile") else ""))
    return 0


# -- details ----------------------------------------------------------------------
def _pack(folder: Path) -> dict:
    return json.loads((Path(folder) / PACK).read_text(encoding="utf-8"))


def _installed(folder: Path) -> dict:
    path = Path(folder) / INSTALLED
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}


def _label(game: str) -> str:
    from anno_modding_studio.core import games

    return games.get(game).label


def _profile(game: str) -> Optional[Path]:
    """Anno 117's live profile, which lists the mods that load; None for Anno 1800."""
    from anno_modding_studio.core import games, switches

    if games.get(game).key != games.ANNO_117:
        return None
    documents = switches.documents_mods(game)
    return switches.active_profile(documents) if documents else None


def _mod_id(mod: Path) -> str:
    try:
        return str(json.loads((mod / "modinfo.json").read_text(encoding="utf-8")).get("ModID") or "")
    except (OSError, ValueError):
        return ""


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    commands = parser.add_subparsers(dest="command", required=True)
    made = commands.add_parser("new", help="start a pack")
    made.add_argument("folder", type=Path)
    made.add_argument("--game", required=True, choices=("anno117", "anno1800"))
    made.add_argument("--title", required=True)
    made.add_argument("--pr", default="")
    for name in ("readme", "install", "remove"):
        commands.add_parser(name).add_argument("folder", type=Path)
    checked = commands.add_parser("check", help="judge the loader's log after a run")
    checked.add_argument("folder", type=Path)
    checked.add_argument("--log", type=Path)
    args = parser.parse_args(argv)
    if args.command == "new":
        new(args.folder, args.game, args.title, args.pr)
        print("started %s: edit pack.json and make_test.py, run make_test.py, then readme" % args.folder)
        return 0
    if args.command == "readme":
        print(readme(args.folder))
        return 0
    if args.command == "check":
        return check(args.folder, args.log)
    return install(args.folder) if args.command == "install" else remove(args.folder)


if __name__ == "__main__":
    sys.exit(main())
