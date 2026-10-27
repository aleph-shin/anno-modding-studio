"""Which update of the game is installed, in the words its patch notes use.

The executable does not say -- Anno 117's reads ``26.7155.4.37741`` while the
game is at 2.1 -- and neither do the archives.  The game's own log does: every
run writes a ``BuildString`` line near the top of
``Documents/<game>/log/Anno*_LOGFILE_*.log``, and the leading numbers of the
version on it are the name Ubisoft's patch notes use (``v2.1.0.1711091.299885``
is *Patch 2.1*; ``v18.4.1412158.165273`` is *Game Update 18.4*).

A log is only as current as the last time the game was started.  After an
update and before the next run it still names the build before, so an answer
is only given when the newest run started after the executable and the
archives were last written.  Otherwise there is no answer, rather than an old
one.

And the update names the *install*, while what a mod is built against is the
assets.xml the Studio read -- which may be an extraction from before the
update.  So a label is attached to that file's fingerprint when both are known
to be current together, and remembered: the data a fingerprint names keeps its
name after the game moves on.  See ``docs/proposals/versioning-proposal.md``,
section 4.
"""

from __future__ import annotations

import contextlib
import datetime
import re
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

from . import records

# ``BuildString: [Build:2026-08-28 09:11:47] Anno8 Steam GoldmasterSteam Win64
# Retail v2.1.0.1711091.299885 //Anno8/Anno117_v2_1`` from 117, and from 1800
# ``BuildString: Developer build - for internal use only! [Build: 2024-11-11
# 14:14:36] Anno7 Steam Goldmaster Win64 Release-Static v18.4.1412158.165273
# main`` -- the "developer build" is what every retail 1800 says.
_BUILD_STRING = re.compile(
    r"BuildString:.*?\[Build:\s*(?P<built>[\d-]+ [\d:]+)\]\s*(?P<rest>.*)$"
)
_VERSION = re.compile(r"\bv(\d+(?:\.\d+)+)")
_LOG_DATE = re.compile(r"_(\d{4}-\d{2}-\d{2})-(\d{2})-(\d{2})-(\d{2})\.log$")
# A version component this long is a build or changelist number, not part of
# the name players know: ``2.1.0`` of ``2.1.0.1711091.299885``.
_BUILD_DIGITS = 5
# The line is written at start-up; a log that has not said it by here will not.
_LINES_TO_READ = 400

REMEMBERED_NAME = "game-updates.json"


@dataclass(frozen=True)
class Build:
    """One ``BuildString`` line, taken apart."""

    built: str  # when the build was made, as the line gives it
    version: str  # the whole dotted version: ``2.1.0.1711091.299885``
    update: str  # the part players know: ``2.1.0``
    store: str  # ``Steam``, or whatever else the line names
    branch: str  # the last word: ``//Anno8/Anno117_v2_1``, ``main``, ``cdlc15``
    line: str

    @property
    def key(self) -> tuple:
        return (self.built, self.version)


@dataclass(frozen=True)
class LogBuild:
    when: datetime.datetime  # when the run started, from the file's name
    build: Build


def parse_build_string(line: str) -> Optional[Build]:
    """A game log's ``BuildString`` line, or None if this is not one."""
    found = _BUILD_STRING.search(line.strip())
    if not found:
        return None
    rest = found.group("rest")
    version = _VERSION.search(rest)
    if not version:
        return None
    words = rest.split()
    return Build(
        built=found.group("built"),
        version=version.group(1),
        update=update_of(version.group(1)),
        # The word after the codename. Read rather than assumed, since a
        # Ubisoft Connect build has not been seen yet.
        store=words[1] if len(words) > 1 else "",
        branch=words[-1] if words else "",
        line=line.strip(),
    )


def update_of(version: str) -> str:
    """The leading components of a dotted version, up to its build number."""
    kept = []
    for piece in version.split("."):
        if len(piece) >= _BUILD_DIGITS:
            break
        kept.append(piece)
    return ".".join(kept) or version


def log_dir(game: str) -> Optional[Path]:
    """Where ``game`` writes its logs, whether or not there are any yet."""
    from . import games

    home = games.documents_dir(game)
    return home / "log" if home is not None else None


def logged_builds(folder: Path) -> List[LogBuild]:
    """The build each run recorded, oldest run first."""
    found: List[LogBuild] = []
    try:
        logs = sorted(Path(folder).glob("*_LOGFILE_*.log"))
    except OSError:
        return found
    for log in logs:
        when = _run_time(log)
        build = _first_build(log)
        if when and build:
            found.append(LogBuild(when, build))
    found.sort(key=lambda item: item.when)
    return found


def changes(builds: List[LogBuild]) -> List[LogBuild]:
    """Only the runs whose build differs from the run before."""
    out: List[LogBuild] = []
    for item in builds:
        if not out or out[-1].build.key != item.build.key:
            out.append(item)
    return out


def last_written(install: Path, game: str) -> float:
    """When the game's executable or any of its archives was last written."""
    from . import gamefiles, games

    times = [mark[2] for mark in gamefiles.archive_marks(install)]
    found = games.GAMES.get(game)
    exe = found.exe if found else ""
    if exe:
        with contextlib.suppress(OSError):
            times.append((Path(install) / "Bin" / "Win64" / exe).stat().st_mtime)
    return max(times) if times else 0.0


def installed(install, game: str, logs: Optional[Path] = None) -> Optional[Build]:
    """The update installed at ``install``, or None if the logs cannot say.

    None when there is no log with a ``BuildString``, and when the newest run
    started before the game was last updated: that log names the build before.
    ``logs`` is the log folder, for a test; otherwise the game's own.
    """
    if install is None:
        return None
    folder = logs if logs is not None else log_dir(game)
    if folder is None:
        return None
    builds = logged_builds(folder)
    if not builds:
        return None
    newest = builds[-1]
    written = last_written(Path(install), game)
    if written and newest.when.timestamp() < written:
        return None
    return newest.build


# -- which update each piece of game data came from ---------------------------
def _remembered_path() -> Path:
    from . import places

    return places.extracted_dir() / REMEMBERED_NAME


# The format of what is remembered, stamped on it (see :mod:`core.records`):
# learnt from game logs that rotate away, so not written from a misreading.
REMEMBERED_FORMAT = 1


def remembered(fingerprint: str) -> Optional[dict]:
    """``{"update", "version"}`` for game data seen before, or None."""
    if not fingerprint:
        return None
    entry = records.read(_remembered_path(), REMEMBERED_FORMAT).data.get(fingerprint)
    if not isinstance(entry, dict) or not entry.get("update"):
        return None
    return {"update": str(entry["update"]), "version": str(entry.get("version") or "")}


def remember(fingerprint: str, build: Build) -> None:
    """Keep which update the data with ``fingerprint`` came from."""
    if not fingerprint:
        return
    path = _remembered_path()
    known = records.read(path, REMEMBERED_FORMAT).data
    known[fingerprint] = {"update": build.update, "version": build.version}
    # Worth having, not worth failing a load over.
    with contextlib.suppress(OSError):
        records.write(path, known, REMEMBERED_FORMAT, sort_keys=True)


def _run_time(log: Path) -> Optional[datetime.datetime]:
    match = _LOG_DATE.search(log.name)
    if not match:
        return None
    try:
        return datetime.datetime.strptime("%s %s:%s:%s" % match.groups(), "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None


def _first_build(log: Path) -> Optional[Build]:
    try:
        with open(log, encoding="utf-8", errors="replace") as handle:
            for number, line in enumerate(handle):
                if number > _LINES_TO_READ:
                    return None
                if "BuildString:" in line:
                    return parse_build_string(line)
    except OSError:
        return None
    return None
