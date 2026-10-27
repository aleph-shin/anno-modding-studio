"""Which game update is installed, read every way the machine offers.

The question section 4 of ``docs/proposals/versioning-proposal.md`` asks: for
each Anno install, is there a source that can be read without starting the
game, that changes with every update, and that says the update's public name?
This prints every candidate for every install
:func:`discovery.find_game_installs` finds, so the check is one command and
can be repeated after the next patch.

    python tools/game_version_probe.py
    python tools/game_version_probe.py --history   # every build the logs have seen

The candidates, in the order they are printed:

* **The executable's version resource** -- ``Bin/Win64/Anno*.exe``, read with
  ``GetFileVersionInfoW``.
* **Steam's app manifest** -- ``buildid`` and ``LastUpdated`` in the
  ``appmanifest_<appid>.acf`` beside a Steam install.
* **The game's own log** -- every run writes a ``BuildString:`` line to
  ``Documents/<game>/log``, naming the version the patch notes use.
* **The archives** -- how many ``.rda`` files ``maindata`` holds and when the
  newest was written, which is what :func:`gamefiles.read_state` watches.

Nothing is written.  Log file names carry the machine's name, so only their
dates are printed.
"""

from __future__ import annotations

import argparse
import ctypes
import datetime
import re
import sys
from pathlib import Path
from typing import Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from anno_modding_studio.core import discovery, gamefiles, games, gameversion, launch


def exe_version(path: Path) -> Dict[str, str]:
    """The fixed file and product versions from an executable's resource."""
    if sys.platform != "win32":
        return {}
    from ctypes import wintypes

    dll = ctypes.WinDLL("version")
    size = dll.GetFileVersionInfoSizeW(str(path), None)
    if not size:
        return {}
    buffer = ctypes.create_string_buffer(size)
    if not dll.GetFileVersionInfoW(str(path), 0, size, buffer):
        return {}
    pointer, length = ctypes.c_void_p(), wintypes.UINT()
    if not dll.VerQueryValueW(buffer, "\\", ctypes.byref(pointer), ctypes.byref(length)):
        return {}
    fixed = ctypes.cast(pointer, ctypes.POINTER(ctypes.c_uint32 * 13)).contents

    def dotted(high: int, low: int) -> str:
        return "%d.%d.%d.%d" % (high >> 16, high & 0xFFFF, low >> 16, low & 0xFFFF)

    return {"file": dotted(fixed[2], fixed[3]), "product": dotted(fixed[4], fixed[5])}


def steam_manifest(install: Path) -> Dict[str, str]:
    """``buildid`` and ``LastUpdated`` from the manifest of a Steam install."""
    app = launch.steam_app_id(install)
    if not app:
        return {}
    manifest = install.parent.parent / ("appmanifest_%d.acf" % app)
    try:
        text = manifest.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return {}
    out = {"appid": str(app)}
    for key in ("buildid", "LastUpdated", "TargetBuildID"):
        match = re.search(r'"%s"\s*"([^"]*)"' % key, text)
        if match:
            out[key] = match.group(1)
    if out.get("LastUpdated", "").isdigit():
        out["LastUpdated"] += "  (%s)" % _stamp(float(out["LastUpdated"]))
    return out


def _stamp(seconds: float) -> str:
    return datetime.datetime.fromtimestamp(seconds).strftime("%Y-%m-%d %H:%M")


def _game_of(install: Path) -> str:
    for game, exe in ((key, found.exe) for key, found in games.GAMES.items()):
        if (install / "Bin" / "Win64" / exe).is_file():
            return game
    return ""


def describe(install: discovery.GameInstall, history: bool) -> str:
    game = _game_of(install.path)
    lines = ["== %s" % install.name]
    if not game:
        lines.append("   not Anno 1800 or Anno 117; skipped")
        return "\n".join(lines)
    exe = install.path / "Bin" / "Win64" / games.GAMES[game].exe
    try:
        exe_written = exe.stat().st_mtime
    except OSError:
        exe_written = 0.0

    store = "Steam" if launch.steam_app_id(install.path) else (
        "Ubisoft Connect" if launch.ubisoft_game_id(install.path) else "unknown"
    )
    lines.append("   store              %s" % store)

    version = exe_version(exe)
    lines.append(
        "   exe version        %s  (file %s, written %s)"
        % (version.get("product", "none"), version.get("file", "none"),
           _stamp(exe_written) if exe_written else "?")
    )

    for key, value in steam_manifest(install.path).items():
        lines.append("   steam %-12s %s" % (key, value))

    marks = gamefiles.archive_marks(install.path)
    if marks:
        newest = max(mark[2] for mark in marks)
        lines.append("   archives           %d, newest written %s" % (len(marks), _stamp(newest)))

    log_dir = gameversion.log_dir(game)
    builds = gameversion.logged_builds(log_dir) if log_dir else []
    if not builds:
        lines.append("   game log           no BuildString in %s" % log_dir)
        return "\n".join(lines)
    last = builds[-1]
    lines.append(
        "   game log           update %s  (v%s, built %s, %s, branch %s)"
        % (last.build.update, last.build.version, last.build.built,
           last.build.store, last.build.branch)
    )
    lines.append("   last run           %s" % last.when.strftime("%Y-%m-%d %H:%M"))
    # The Studio's own answer, which is only given while the log is current:
    # after an update and before the next run it still names the old build.
    if gameversion.installed(install.path, game) is None:
        lines.append("   STALE              the game was updated after its last run; start it once")
    else:
        lines.append("   current            the last run was after the game was last written")

    seen = gameversion.changes(builds)
    lines.append("   builds seen        %d across %d runs" % (len(seen), len(builds)))
    if history:
        for item in seen:
            lines.append(
                "     %s  %-8s v%s  %s"
                % (item.when.strftime("%Y-%m-%d"), item.build.update,
                   item.build.version, item.build.branch)
            )
    return "\n".join(lines)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--history", action="store_true", help="list every build change the logs recorded"
    )
    args = parser.parse_args(argv)
    installs = [
        found for found in discovery.find_game_installs() if _game_of(found.path)
    ]
    if not installs:
        print("no Anno 1800 or Anno 117 install found", file=sys.stderr)
        return 1
    for install in installs:
        print(describe(install, args.history))
        print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
