"""What the game's mod loader says it did, beside what Play asked for.

Play makes the mods folder say what the loadout says; whether the game then
loads that is a different claim, and the loader writes down the answer.  Both
games keep a log -- ``log/mod-loader.log`` under Anno 1800's documents
folder, ``mods/mod-loader.log`` under Anno 117's -- rewritten every time the
game starts, each line stamped with the time:

    Anno 1800:  Load <name> (<version>, <ModID>) from mods\\<folder>[\\<nested>]
                ... Loading complete
    Anno 117:   Enabled mod: mods/<folder>  (<modid>@<version>)
                Enabled mod: mods/<folder>/<nested> [SubMod] (<modid>@<version>)
                Option: <modid>.<option>: <value>
                Load "<name>" <modid>@<version> (from "mods/<folder>")
                -- Load <modid>@<version> (from <host's modid>)
                <modid>: No matching node for Path "..." (<file>:<line>)
                ... Patching completed in 2s

Both write the junction's own path for a library mod, so a mod the Studio
manages reads as its folder name like any other.  Reading this turns "what you
see is what the game gets" from a claim into a check.

Which log is the one to believe: the first line's time says when the game
started, and one that started after the loadout was made live is an answer
about it.  One without its closing line is a game still loading.
"""

from __future__ import annotations

import contextlib
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

from . import games, modfolder, modio_cache
from .games import ANNO_117
from .i18n import date_text, tr, trn

# Where each loader writes its log, relative to the game's own folder under
# Documents: Anno 1800's under log/, Anno 117's beside the mods it loads.
LOG_FILES = ("log/mod-loader.log", "mods/mod-loader.log")

_STAMP = re.compile(r"^\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})(?:\.\d+)?\]\s*\[(\w+)\]\s*(.*)$")
# Anno 1800: the name is greedy, so the last "(version, id)" before "from" is
# the one taken -- mod names are full of brackets.
# The ModID may hold brackets too: a mod with no modinfo is logged under its
# folder name as both -- "Load X (Studio) (unversioned, X (Studio)) from ...".
_LOAD_1800 = re.compile(r"^Load (?P<name>.*) \((?P<version>[^(),]*), (?P<id>.*?)\) from (?P<path>.+?)\s*$")
_LOAD_117 = re.compile(r'^Load "(?P<name>[^"]*)" (?P<id>\S+?)@(?P<version>\S*) \(from "(?P<path>[^"]*)"\)')
_ENABLED_117 = re.compile(r"^Enabled mod: (?P<path>.+?)\s+\((?P<id>[^@()]+)@(?P<version>[^()]*)\)\s*$")
# 117's mod bundled inside another -- the loader's [SubMod] -- has its path on
# its Enabled line, tagged, and a Load line of its own that names only its
# host, by ModID: "-- Load <id>@<version> (from <host id>)".  Its place among
# the Load lines is the loader's sort, not beside its host (D1, 16 Oct 2026).
_SUBMOD_TAG = re.compile(r"\s+\[SubMod\]$")
_LOAD_117_NESTED = re.compile(r"^-- Load (?P<id>\S+?)@(?P<version>\S*) \(from (?P<host>[^()]*)\)\s*$")
_OPTION_117 = re.compile(r"^Option: (?P<id>[^.:\s]+)\.(?P<name>[^:]+): ?(?P<value>.*)$")
_COMPLETE = ("Loading complete", "Patching completed")
# Warnings about one op of a mod that loaded: not about whether it loaded.
_ABOUT_AN_OP = ("No matching node", "Include file missing")
# ``<mod name>: No matching node for Path "<path>" (<file>:<line>)`` and
# ``<mod name>: Include file missing or empty: <file> (<file>:<line>)``.
_OP_WARNING = re.compile(
    r'^(?P<mod>.+?): (?:No matching node for Path "(?P<path>.*)"'
    r"|Include file missing or empty: (?P<include>.+?))"
    r"(?: \((?P<where>[^()]*:\d+)\))?\s*$"
)
NO_MATCH = "no match"
NO_INCLUDE = "include missing"


@dataclass
class Logged:
    """One mod the loader loaded (or, for 117, enabled)."""

    mod_id: str
    name: str
    folder: str       # its top-level folder, the switch's unit
    bundled: bool     # inside another mod's folder
    path: str = ""    # where it was loaded from, as the log writes it

    @property
    def keys(self) -> set:
        return {key for key in (self.mod_id.strip().lower(), self.folder.lower()) if key}


@dataclass(frozen=True)
class OpWarning:
    """One op of a loaded mod the loader said did nothing: its path matched no
    node, or a file it includes is missing or empty."""

    mod: str          # the mod's name, as the log writes it
    kind: str         # NO_MATCH or NO_INCLUDE
    what: str         # the path that matched nothing, or the file not found
    where: str = ""   # "<file>:<line>" in the mod
    mod_id: str = ""  # from the mod's own Load line, where the log has one
    folder: str = ""
    bundled: bool = False

    @property
    def keys(self) -> set:
        """What the mod is matched to a loadout's mod by: its ModID, and its
        folder only when it is a folder of its own."""
        found = {self.mod_id.strip().lower()} if self.mod_id.strip() else set()
        if self.folder and not self.bundled:
            found.add(self.folder.lower())
        return found


@dataclass
class LoaderLog:
    path: Path
    started: float = 0.0       # the first line's time, seconds since the epoch
    complete: bool = False
    mods: List[Logged] = field(default_factory=list)
    # Every mod the loader loaded, in the order it ran them, bundled ones
    # included: its Load lines as they come.
    loaded: List[Logged] = field(default_factory=list)
    # 117: lower-case ModID -> option -> value, as the loader applied them.
    options: Dict[str, Dict[str, str]] = field(default_factory=dict)
    # (level, text) for every error and warning that is not about one op --
    # or is, but not in a shape that says which mod's.
    problems: List[tuple] = field(default_factory=list)
    # And those that are, each once: the loader runs its patching more than
    # once a start, and says so each time.
    op_warnings: List[OpWarning] = field(default_factory=list)

    def warnings_for(self, keys) -> List[OpWarning]:
        """The op warnings of the mod known by any of ``keys`` (lower case)."""
        keys = {key for key in keys if key}
        return [warning for warning in self.op_warnings if warning.keys & keys]

    def warnings_for_layer(self, layer) -> List[OpWarning]:
        """The op warnings of one of a loadout's mods: see :func:`layer_keys`."""
        return self.warnings_for(layer_keys(layer))

    @property
    def top_level(self) -> List[Logged]:
        return [mod for mod in self.mods if not mod.bundled]

    def reason_for(self, *names) -> str:
        """What the loader said about a mod, by any of its names, or ""."""
        wanted = [name.lower() for name in names if name]
        for _level, text in self.problems:
            lowered = text.lower()
            if any(name in lowered for name in wanted):
                return text
        return ""


def layer_keys(layer) -> set:
    """What a loadout's mod is matched to the log's warnings by: its key --
    its ModID, or its folder's name -- and, for a mod in a folder of its own,
    that folder's name as well.  One rule for the changes panel's tooltip,
    which lists a mod's warnings, and for the play check, which says apart
    those no tooltip lists."""
    keys = {modfolder.mod_key(layer.mod_id, modfolder.folder_name(layer.origin))}
    if not modfolder.is_bundled(layer.origin):
        keys.add(modfolder.folder_name(layer.origin).lower())
    return keys


def log_file(game: str) -> Optional[Path]:
    """The loader's log for ``game``, where there is one."""
    home = games.documents_dir(game)
    if home is None:
        return None
    for relative in LOG_FILES:
        candidate = home / relative
        if candidate.is_file():
            return candidate
    return None


def read(game: str, path: Optional[Path] = None) -> Optional[LoaderLog]:
    """The log, read; None when there is none to read."""
    path = Path(path) if path is not None else log_file(game)
    if path is None or not path.is_file():
        return None
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    log = LoaderLog(path=path)
    cache = _cache_parts(game)
    enabled: List[Logged] = []
    loaded: List[Logged] = []
    by_name: Dict[str, Logged] = {}
    seen_warnings = set()
    for line in text.splitlines():
        stamped = _STAMP.match(line)
        if not stamped:
            continue
        when, level, body = stamped.groups()
        if not log.started:
            with contextlib.suppress(ValueError):
                log.started = time.mktime(time.strptime(when, "%Y-%m-%d %H:%M:%S"))
        if any(body.startswith(marker) for marker in _COMPLETE):
            log.complete = True
            continue
        if level.lower() in ("error", "warning", "critical"):
            if not any(marker in body for marker in _ABOUT_AN_OP):
                log.problems.append((level.lower(), body))
                continue
            warned = _OP_WARNING.match(body)
            if warned is None:
                # About an op, but not saying whose: kept as said, not dropped.
                log.problems.append((level.lower(), body))
                continue
            mod = warned.group("mod").strip()
            known = by_name.get(mod)
            warning = OpWarning(
                mod=mod,
                kind=NO_MATCH if warned.group("path") is not None else NO_INCLUDE,
                what=(warned.group("path") if warned.group("path") is not None
                      else warned.group("include")).strip(),
                where=(warned.group("where") or "").strip(),
                mod_id=known.mod_id if known else "",
                folder=known.folder if known else "",
                bundled=known.bundled if known else False,
            )
            if warning not in seen_warnings:
                seen_warnings.add(warning)
                log.op_warnings.append(warning)
            continue
        found = _ENABLED_117.match(body)
        if found:
            enabled.append(_logged(found.group("id"), "", _SUBMOD_TAG.sub("", found.group("path")), cache))
            continue
        found = _OPTION_117.match(body)
        if found:
            log.options.setdefault(found.group("id").strip().lower(), {})[found.group("name").strip()] = (
                found.group("value").strip()
            )
            continue
        found = _LOAD_117_NESTED.match(body)
        if found:
            # Named by its ModID, which is what 117 names it by in a warning,
            # and found where its Enabled line put it.
            mod_id = found.group("id").strip()
            where = next((mod.path for mod in enabled if mod.mod_id.lower() == mod_id.lower()), "")
            nested = _logged(mod_id, mod_id, where, cache) if where else Logged(mod_id, mod_id, "", True)
            loaded.append(nested)
            by_name[mod_id] = nested
            continue
        found = _LOAD_117.match(body)
        if found:
            loaded.append(_logged(found.group("id"), found.group("name"), found.group("path"), cache))
            # 117 starts a warning about one of a mod's ops with its ModID.
            by_name[found.group("name").strip()] = by_name[found.group("id").strip()] = loaded[-1]
            continue
        found = _LOAD_1800.match(body)
        if found:
            loaded.append(_logged(found.group("id"), found.group("name"), found.group("path"), cache))
            by_name[found.group("name").strip()] = loaded[-1]
    # 117 says which mods it switched on before it loads any, and that is the
    # set Play set; a mod with nothing to patch may have no Load line at all.
    if enabled:
        names = {mod.mod_id.lower(): mod.name for mod in loaded}
        for mod in enabled:
            mod.name = names.get(mod.mod_id.lower(), "") or mod.folder
        log.mods = enabled + [mod for mod in loaded if mod.bundled and mod.mod_id.lower() not in
                              {one.mod_id.lower() for one in enabled}]
    else:
        log.mods = loaded
    log.loaded = loaded
    return log


_READ: Dict[tuple, Optional[LoaderLog]] = {}


def read_cached(game: str, path: Optional[Path] = None) -> Optional[LoaderLog]:
    """:func:`read`, held until the file changes: asked for every mod row each
    time the changes list is drawn."""
    path = Path(path) if path is not None else log_file(game)
    if path is None:
        return None
    try:
        stat = path.stat()
    except OSError:
        return None
    key = (str(path), stat.st_mtime, stat.st_size, tuple(_cache_parts(game)))
    if key not in _READ:
        _READ.clear()
        _READ[key] = read(game, path)
    return _READ[key]


def describe_warnings(warnings: List[OpWarning], started: float = 0.0, most: int = 4) -> List[str]:
    """Lines for one mod's tooltip: what the loader said its ops did not do."""
    if not warnings:
        return []
    when = date_text(started, year=False, clock=True) if started else ""
    missed = [warning for warning in warnings if warning.kind == NO_MATCH]
    lost = [warning for warning in warnings if warning.kind == NO_INCLUDE]
    lines = []
    lead = True
    if missed:
        if when:
            lines.append(trn(
                "When the game last started (%(when)s), one of its ops "
                "matched nothing. This is usually a mod it expects that is "
                "not there, or a game update that moved what it patches:",
                "When the game last started (%(when)s), %(n)s of its ops "
                "matched nothing. This is usually a mod it expects that is "
                "not there, or a game update that moved what it patches:",
                len(missed), when=when,
            ))
        else:
            lines.append(trn(
                "When the game last started, one of its ops matched nothing. "
                "This is usually a mod it expects that is not there, or a "
                "game update that moved what it patches:",
                "When the game last started, %(n)s of its ops matched "
                "nothing. This is usually a mod it expects that is not "
                "there, or a game update that moved what it patches:",
                len(missed),
            ))
        for warning in missed[:most]:
            lines.append("  %s%s" % (_short(warning.what), "  (%s)" % warning.where if warning.where else ""))
        if len(missed) > most:
            lines.append("  " + trn("and %(n)s more", "and %(n)s more", len(missed) - most))
        lead = False
    for warning in lost:
        if lead and when:
            lines.append(tr(
                "When the game last started (%(when)s), a file it includes was missing "
                "or empty: %(file)s", when=when, file=warning.what,
            ))
        elif lead:
            lines.append(tr(
                "When the game last started, a file it includes was missing or empty: %s",
                warning.what,
            ))
        else:
            lines.append(tr("a file it includes was missing or empty: %s", warning.what))
        lead = False
    return lines


def _short(path: str, most: int = 90) -> str:
    return path if len(path) <= most else path[: most - 1] + "…"


def _logged(mod_id: str, name: str, path: str, cache: List[str]) -> Logged:
    parts = _parts(path)
    below = _below_cache(parts, cache)
    if below is None:
        lowered = [part.lower() for part in parts]
        below = parts[lowered.index("mods") + 1:] if "mods" in lowered else parts[-1:]
    # A container that is not a mod: mod.io's numbered folder, Ubisoft's .ubi.
    while len(below) > 1 and (below[0].isdigit() or below[0].lower() == ".ubi"):
        below = below[1:]
    folder = modfolder.bare_name(below[0]) if below else ""
    return Logged(mod_id=mod_id.strip(), name=name.strip(), folder=folder, bundled=len(below) > 1, path=path)


# -- the comparison ------------------------------------------------------------------
@dataclass
class Check:
    """The log beside what the loadout has on."""

    log: LoaderLog
    loaded: int = 0
    # (name, what the loader said about it, or "") -- on here, not loaded.
    missing: List[tuple] = field(default_factory=list)
    # Loaded, and not on here.
    extra: List[str] = field(default_factory=list)
    # Loaded, and put there by the game's own browser, which Play cannot switch.
    browser: List[str] = field(default_factory=list)
    # 117: (mod, option, what the loadout says, what the game ran).
    options: List[tuple] = field(default_factory=list)
    # Op warnings of mods the loadout does not have, which no tooltip in the
    # changes panel lists: said by name instead.
    elsewhere: List[OpWarning] = field(default_factory=list)
    # Where the log came from: True for a game Play started, False for one
    # started some other way, None when that is not known.
    by_play: Optional[bool] = None
    # Whether the mods folder has changed since Play wrote it -- then what the
    # game loaded is not a check of what Play set.
    changed: bool = False

    @property
    def ok(self) -> bool:
        return not (self.missing or self.extra or self.options)

    @property
    def fault(self) -> bool:
        """A difference that is Play's to answer for: the folder is as Play
        left it, and the game still did something else."""
        return not self.ok and not self.changed

    def describe(self) -> str:
        said = [self._opening()]
        if self.changed:
            said.append(tr(
                "The mods folder has been changed outside the Studio since "
                "Play wrote it, so that is not a check of what Play set. "
                "Play puts it back."
            ))
            return " ".join(said)
        if not self.ok:
            bits = []
            if self.missing:
                names = _named([
                    "%s (%s)" % (name, why) if why else name
                    for name, why in self.missing
                ])
                bits.append(trn(
                    "%(n)s mod this loadout has on did not load: %(names)s",
                    "%(n)s mods this loadout has on did not load: %(names)s",
                    len(self.missing), names=names,
                ))
            if self.extra:
                bits.append(trn(
                    "%(n)s mod it does not have on loaded: %(names)s",
                    "%(n)s mods it does not have on loaded: %(names)s",
                    len(self.extra), names=_named(self.extra),
                ))
            if self.options:
                bits.append(trn(
                    "%(n)s option ran differently: %(names)s",
                    "%(n)s options ran differently: %(names)s",
                    len(self.options), names=_named([
                        tr("%(mod)s %(option)s at %(got)s, not %(wanted)s",
                           mod=mod, option=name, got=got, wanted=wanted)
                        for mod, name, wanted, got in self.options
                    ]),
                ))
            said.append("; ".join(bits) + ".")
        if self.browser:
            # Counted in what it loaded, and not the loadout's: one of them,
            # not one more.
            if self.ok and self.loaded > len(self.browser):
                said.append(trn(
                    "The game's own browser installed %(n)s of them, and every "
                    "other one is this loadout's.",
                    "The game's own browser installed %(n)s of them, and every "
                    "other one is this loadout's.",
                    len(self.browser),
                ))
            else:
                said.append(trn(
                    "The game's own browser installed %(n)s of the mods it loaded.",
                    "The game's own browser installed %(n)s of the mods it loaded.",
                    len(self.browser),
                ))
        if self.log.op_warnings and not self.changed:
            # Not Play's to answer for: the mods loaded, and some of what they
            # patch was not there. Said, with where to look -- and those of
            # mods with no tooltip to look in, by name.
            shown = [warning for warning in self.log.op_warnings if warning not in self.elsewhere]
            if shown:
                said.append(trn(
                    "%(n)s op in %(mods)s did nothing: a path that matched "
                    "nothing, or a file it includes that was not there. Each "
                    "mod's tooltip in the changes panel lists its own.",
                    "%(n)s ops in %(mods)s did nothing: a path that matched "
                    "nothing, or a file it includes that was not there. Each "
                    "mod's tooltip in the changes panel lists its own.",
                    len(shown), mods=_mods(shown),
                ))
            if self.elsewhere:
                names = _named(list(dict.fromkeys(warning.mod for warning in self.elsewhere)))
                if shown:
                    said.append(trn(
                        "%(n)s more did nothing in %(mods)s this loadout does "
                        "not have: %(names)s.",
                        "%(n)s more did nothing in %(mods)s this loadout does "
                        "not have: %(names)s.",
                        len(self.elsewhere), mods=_mods(self.elsewhere), names=names,
                    ))
                else:
                    said.append(trn(
                        "%(n)s op in %(mods)s this loadout does not have "
                        "(%(names)s) did nothing: a path that matched nothing, "
                        "or a file it includes that was not there.",
                        "%(n)s ops in %(mods)s this loadout does not have "
                        "(%(names)s) did nothing: a path that matched nothing, "
                        "or a file it includes that was not there.",
                        len(self.elsewhere), mods=_mods(self.elsewhere), names=names,
                    ))
        return " ".join(said)

    def _opening(self) -> str:
        """When the game started and how many mods it loaded -- and, when that
        is all there is to say, that they were all this loadout's -- not when
        the game's own browser put some of them there."""
        n = self.loaded
        when = date_text(self.log.started, year=False, clock=True) if self.log.started else ""
        whose = n and self.ok and not self.changed and not self.browser
        if self.by_play and when:
            if not n:
                return tr("When Play started the game (%s), it loaded no mods.", when)
            if whose:
                return trn(
                    "When Play started the game (%(when)s), it loaded %(n)s mod, this loadout's.",
                    "When Play started the game (%(when)s), it loaded %(n)s mods, "
                    "all of them this loadout's.", n, when=when,
                )
            return trn(
                "When Play started the game (%(when)s), it loaded %(n)s mod.",
                "When Play started the game (%(when)s), it loaded %(n)s mods.", n, when=when,
            )
        if self.by_play:
            if not n:
                return tr("When Play started the game, it loaded no mods.")
            if whose:
                return trn(
                    "When Play started the game, it loaded %(n)s mod, this loadout's.",
                    "When Play started the game, it loaded %(n)s mods, all of them this loadout's.", n,
                )
            return trn(
                "When Play started the game, it loaded %(n)s mod.",
                "When Play started the game, it loaded %(n)s mods.", n,
            )
        if self.by_play is False and when:
            if not n:
                return tr("When the game was started outside the Studio (%s), it loaded no mods.", when)
            if whose:
                return trn(
                    "When the game was started outside the Studio (%(when)s), it loaded %(n)s mod, "
                    "this loadout's.",
                    "When the game was started outside the Studio (%(when)s), it loaded %(n)s mods, "
                    "all of them this loadout's.", n, when=when,
                )
            return trn(
                "When the game was started outside the Studio (%(when)s), it loaded %(n)s mod.",
                "When the game was started outside the Studio (%(when)s), it loaded %(n)s mods.",
                n, when=when,
            )
        if self.by_play is False:
            if not n:
                return tr("When the game was started outside the Studio, it loaded no mods.")
            if whose:
                return trn(
                    "When the game was started outside the Studio, it loaded %(n)s mod, this loadout's.",
                    "When the game was started outside the Studio, it loaded %(n)s mods, "
                    "all of them this loadout's.", n,
                )
            return trn(
                "When the game was started outside the Studio, it loaded %(n)s mod.",
                "When the game was started outside the Studio, it loaded %(n)s mods.", n,
            )
        if when:
            if not n:
                return tr("When the game started (%s), it loaded no mods.", when)
            if whose:
                return trn(
                    "When the game started (%(when)s), it loaded %(n)s mod, this loadout's.",
                    "When the game started (%(when)s), it loaded %(n)s mods, all of them this loadout's.",
                    n, when=when,
                )
            return trn(
                "When the game started (%(when)s), it loaded %(n)s mod.",
                "When the game started (%(when)s), it loaded %(n)s mods.", n, when=when,
            )
        if not n:
            return tr("When the game started, it loaded no mods.")
        if whose:
            return trn(
                "When the game started, it loaded %(n)s mod, this loadout's.",
                "When the game started, it loaded %(n)s mods, all of them this loadout's.", n,
            )
        return trn(
            "When the game started, it loaded %(n)s mod.",
            "When the game started, it loaded %(n)s mods.", n,
        )


def check(log: LoaderLog, found, loadout) -> Check:
    """``log`` beside ``found`` (the :class:`core.truth.Truth` for ``loadout``)."""
    result = Check(log=log)
    wanted: dict = {}
    for mod in found.mods:
        if mod.origin and mod.wanted:
            wanted.setdefault(mod.key, mod)
    browser = {mod.key for mod in found.mods if mod.unswitchable}
    folders = {mod.installed.folder_name.lower(): key for key, mod in wanted.items()}
    # Listed by the loader and loading nothing: not a mod the loadout could
    # have on, so not one it failed to switch off.
    inert = {mod.folder_name.lower() for mod in getattr(found, "inert", ())}
    seen = set()
    for logged in log.mods:
        key = next((k for k in logged.keys if k in wanted), None) or next(
            (folders[k] for k in logged.keys if k in folders), None)
        if key is not None:
            # Nested lines count too, by ModID: the loader keeps one copy per
            # ModID, and where a host ships a copy of itself inside its own
            # folder -- *Vanilla Riverslot Buildings* does -- the copy it
            # keeps is the nested one.
            seen.add(key)
        elif logged.bundled or logged.folder.lower() in inert:
            continue  # a piece of a host, which answers for it; or nothing at all
        elif logged.keys & browser:
            result.browser.append(logged.name or logged.folder)
        else:
            result.extra.append(logged.name or logged.folder)
    result.loaded = len(seen) + len(result.extra) + len(result.browser)
    # A mod another one replaces is on and still not loaded -- the loader
    # drops it, and the card says so already. Not missing: expected.
    replaced = getattr(getattr(found, "needs", None), "dropped", None) or set()
    for key, mod in wanted.items():
        if key not in seen and key not in replaced:
            result.missing.append(
                (mod.name, log.reason_for(mod.installed.folder_name, mod.installed.mod_id, mod.name))
            )
    if found.game == ANNO_117:
        result.options = _options_differ(log, loadout)
    # Every one of its mods has a row in the changes panel, whose tooltip
    # lists the mod's warnings; those of any other mod are said on the card.
    tipped = [layer for layer in loadout.layers if layer.origin]
    result.elsewhere = [
        warning for warning in log.op_warnings
        if not any(warning.keys & layer_keys(layer) for layer in tipped)
    ]
    return result


def _options_differ(log: LoaderLog, loadout) -> List[tuple]:
    from . import imya
    from . import settings as settings_mod

    differ = []
    off = loadout.switched_off()
    for layer in loadout.layers:
        if layer.origin in off or not layer.offered or not layer.mod_id:
            continue
        ran = log.options.get(layer.mod_id.strip().lower())
        if not ran:
            continue
        offered = settings_mod.from_dict(layer.offered)
        settled = offered.settled(layer.options)
        for option in offered.settable:
            if imya.is_tweak_name(option.name) or option.name not in ran:
                continue
            if not _same(settled.get(option.name, option.default), ran[option.name]):
                differ.append((layer.label, option.label or option.name, settled.get(option.name), ran[option.name]))
    return differ


def _same(one, other) -> bool:
    one, other = str(one).strip().lower(), str(other).strip().lower()
    if one == other:
        return True
    try:
        return float(one) == float(other)
    except ValueError:
        return False


def _mods(warnings: List[OpWarning]) -> str:
    """How many mods ``warnings`` are about, by the name the log uses, said."""
    return trn("%(n)s mod", "%(n)s mods", len({warning.mod for warning in warnings}))


def _named(names: List[str], most: int = 4) -> str:
    names = list(names)
    if len(names) <= most:
        return ", ".join(names)
    return trn(
        "%(names)s and %(n)s more", "%(names)s and %(n)s more",
        len(names) - most, names=", ".join(names[:most]),
    )


# -- the order it ran them in -------------------------------------------------------
def _below_mods(parts: List[str]) -> List[str]:
    """The part of a logged path under its mods directory, or all of it."""
    lowered = [part.lower() for part in parts]
    if "mods" in lowered and lowered.index("mods") + 1 < len(parts):
        return parts[lowered.index("mods") + 1:]
    return parts


def _parts(path: str) -> List[str]:
    return [part for part in path.replace("/", "\\").split("\\") if part]


def _cache_parts(game: str) -> List[str]:
    """The mods folder of the game's mod.io client, split as a logged path is:
    wherever its settings put it, as :mod:`core.modio_cache` finds it."""
    return _parts(str(modio_cache.game_dir(game) / "mods"))


def _below_cache(parts: List[str], cache: List[str]) -> Optional[List[str]]:
    """The part of a logged path below the mod.io client's mods folder; None
    when it is not in that folder.

    Compared with the folder itself, not guessed from the names along the
    path: a folder called mod.io somewhere else is not the cache, and the
    client's own may sit under a folder called Mods, or anywhere at all.
    """
    if len(parts) <= len(cache):
        return None
    if [part.lower() for part in parts[:len(cache)]] != [part.lower() for part in cache]:
        return None
    return parts[len(cache):]


def _studio_name(path: str, cache: List[str]) -> str:
    """What the Studio calls the mod a logged path points at.

    Everything below the mods directory, because a mod bundled inside another
    is a mod of its own and the log gives it its own line -- often a long way
    from where its host runs.  The "switched off" mark comes off the top.
    """
    parts = _parts(path)
    below = _below_cache(parts, cache)
    if below is None:
        below = _below_mods(parts)
    return "/".join([modfolder.bare_name(below[0]), *below[1:]]) if below else ""


def logged_order(game: str, path: Optional[Path] = None) -> List[str]:
    """The order the mod loader itself recorded, last time the game started.

    Worth more than any rule worked out here.  Anno 1800's loader does not
    apply mods in folder-name order -- it resolves what mods declare about each
    other, which :func:`core.switches.order` follows from the loader's source
    -- but what it wrote down is what happened, and a rule is only ever what
    should.

    The mods by what the Studio calls them, in the order their own ModOps ran;
    empty with no log to read.  A mod bundled inside another has a line and a
    place of its own.  ``path`` is the log, for a test; otherwise it is found.
    """
    log = read_cached(game, path)
    cache = _cache_parts(game)
    found: List[str] = []
    for mod in log.loaded if log is not None else []:
        name = _studio_name(mod.path, cache)
        if name and name not in found:
            found.append(name)
    return found


def logged_cache_ids(game: str, path: Optional[Path] = None) -> Dict[str, int]:
    """Which mod.io mod each mod the loader took from the mod.io cache is.

    The cache is ``<root>/<game>/mods/<mod id>/<mod folder>`` -- or the mod's
    files straight in ``<mod id>`` -- under the root the client's settings
    name, and the 1800 log records a mod loaded from there with that whole
    absolute path.  So the id is right there in the line, for nothing: no
    request, no state file.  Keyed by the name :func:`logged_order` gives the
    same mod, which for a cache mod starts with that numeric folder.
    """
    log = read_cached(game, path)
    cache = _cache_parts(game)
    found: Dict[str, int] = {}
    for mod in log.loaded if log is not None else []:
        below = _below_cache(_parts(mod.path), cache)
        if not below or not below[0].isdigit():
            continue
        found.setdefault(_studio_name(mod.path, cache), int(below[0]))
    return found
