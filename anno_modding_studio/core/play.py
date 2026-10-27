"""Play: making a loadout the one the game loads -- what that would do, and doing it.

One act, in order: build the loadout's own mods and patches into the library;
make the switches match -- a link for every library mod it has on and none for
the rest, the game's own switch for anything else, everything it does not
mention off; for Anno 117 write its profile, point the game at it and write
the mods' options; for Anno 1800 write the iModYourAnno settings into the
library's copies.  :func:`plan` works all of that out first, so the question
before it can say it and nothing happens before the answer; :func:`carry_out`
does it.  Starting the game, and asking, are the window's.

Every loadout is complete when played, not only a loadout: a mod of your own
played with nothing else on is that mod in isolation, which is exactly what
testing it wants.  Nothing is deleted and no mod of anybody's is rewritten;
what moves is the switch the game reads, the Studio's own builds, and the
Studio's own library copies.
"""

from __future__ import annotations

import contextlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

from . import (
    build,
    builds,
    builtby,
    dependencies,
    disk,
    games,
    generator,
    library,
    modfolder,
    modinfo,
    proton,
    switches,
    truth,
)
from .i18n import bullets, number, tr, tr_noop, trn


@dataclass
class Plan:
    """What making a loadout live would do."""

    folder: str
    built: list = field(default_factory=list)        # origins built into the library
    built_names: list = field(default_factory=list)  # their folder names
    # Mods of yours not built, because another project built their folder:
    # [(origin, why), ...].  See core.builtby.
    stopped: list = field(default_factory=list)
    # What is worth knowing first, by kind: [(heading, [sentence, ...]), ...],
    # in the order they are said. Kept apart so a long list can be told as a
    # count per kind with the rest behind *Show Details*.
    worth: list = field(default_factory=list)
    notes: list = field(default_factory=list)       # said once, under the warnings
    # What the game's loader made of each mod Play builds (loadercheck.Verdict).
    loader: list = field(default_factory=list)
    current: Any = None                              # the folder's switches: switches.FolderSwitches
    wanted: dict = field(default_factory=dict)
    moving: dict = field(default_factory=dict)       # switches that would move
    found: Any = None                                # the truth, across every folder: truth.Truth
    strays: list = field(default_factory=list)
    own: bool = False                                # 117: the Studio's profile
    own_name: str = ""
    pointer_folder: Any = None
    options: dict = field(default_factory=dict)      # 117: ModID -> answers
    # What goes into generated copies: iModYourAnno settings (1800) and your
    # edits to other people's mods.  See generated_copies.
    copies: Any = None
    # Once carried out: whether a build number moved on, an edit to the loadout.
    numbered: bool = False

    @property
    def tweaks(self) -> list:
        """1800: the layers whose iModYourAnno settings are written."""
        return self.copies.tweaks if self.copies is not None else []

    @property
    def tweaks_held(self) -> list:
        """1800: the mods set here that are not the library's to write."""
        return self.copies.tweaks_held if self.copies is not None else []

    @property
    def generating(self) -> dict:
        """Each generated copy to build, by its link's name."""
        return self.copies.generating if self.copies is not None else {}

    @property
    def reverting(self) -> list:
        """The links to point back at the library's own copy."""
        return self.copies.reverting if self.copies is not None else []

    @property
    def edited(self) -> list:
        """Other people's mods whose edits of yours go into generated copies."""
        return self.copies.edited if self.copies is not None else []

    @property
    def nothing_to_do(self) -> bool:
        # A mod not built is said, even with nothing else to do.
        return not (self.built or self.moving or self.strays or self.options or self.tweaks
                    or self.generating or self.reverting or self.stopped)

    @property
    def warnings(self) -> list:
        """Everything worth knowing first, as one list."""
        return [line for _heading, lines in self.worth for line in lines]

    def worth_knowing(self, heading: str, lines) -> None:
        lines = [line for line in lines if line]
        if lines:
            self.worth.append((heading, lines))

    # Past this many sentences, each kind is a count and its first few, and
    # the whole list goes behind Show Details.
    SAID_IN_FULL = 8
    SAID_OF_EACH = 2

    def told(self) -> tuple:
        """``(the part for the question, all of it)`` -- the second "" when
        the first already says everything."""
        whole = "\n".join(
            "%s:\n%s" % (heading, bullets(lines)) for heading, lines in self.worth
        )
        if len(self.warnings) <= self.SAID_IN_FULL:
            return whole, ""
        short = []
        for heading, lines in self.worth:
            short.append("%s (%s):\n%s" % (heading, number(len(lines)), bullets(lines, self.SAID_OF_EACH)))
        # Translators: "Show Details" is the button Qt puts on the question.
        return "\n".join(short) + "\n\n" + tr("Show Details has every one."), whole


# -- working it out -----------------------------------------------------------------
def plan(
    loadout,
    folder: str,
    library_dir,
    index=None,
    verdicts: Optional[Callable[[list], list]] = None,
    repeats=(),
) -> Plan:
    """Everything making ``loadout`` live in ``folder`` would do.

    ``index`` checks the builds' changes still apply; ``verdicts`` asks the
    game's loader about the mods it builds, where there is one; ``repeats``
    says which GUIDs the loading mods take twice.  Each is only said, never a
    reason not to go ahead.
    """
    found = Plan(folder=folder)
    # Somebody else's library mod with edits of yours goes into a generated
    # copy of it, worked out below; built, it would be written over the
    # library's copy.
    copies = generated_copies(loadout, library_dir, index)
    found.built = [origin for origin in build.mine(loadout) if origin not in copies.handled]
    # One writer per library folder: a mod whose folder another project
    # built is not built from here, and the game gets that project's build.
    found.stopped = builtby.stopped(loadout, found.built, Path(folder), library_dir)
    yours = [origin for origin in found.built if origin not in dict(found.stopped)]
    # A build the mods folder already loads, of the mod as it is now, is not
    # built again: it would come out the same, and Play would ask about it
    # every time though nothing was to change.
    found.built = [origin for origin in yours
                   if not current_build(loadout, origin, folder, library_dir)]
    found.built_names = [generator.folder_for(loadout, origin) for origin in found.built]
    found.worth_knowing(tr("Built by another project"), [why for _origin, why in found.stopped])
    # Translators: headings over what Play's question says is worth knowing
    # first, each a kind of finding.
    found.worth_knowing(tr("Your changes"), validate(loadout, yours, index))

    # The inventory first, so the switches are read off the folders it found
    # rather than off a listing of their own (P8-29).
    found.found = truth.read(loadout, folder, library_dir=library_dir)
    ids = {layer.origin: layer.mod_id for layer in loadout.layers}
    found.current = switches.read(
        folder, loadout.game, ids=ids, names=[layer.origin for layer in loadout.layers],
        tops=switches.tops_of(loadout.game, folder,
                              [mod.folder for mod in found.found.installed if mod.linked]),
    )
    # A bundled mod is not asked for: it switches with its host, which is
    # (Loadout.switch_of), and asking for it alone is a switch Play would
    # then fail to make.
    holders = loadout.switch_holders()
    found.wanted = {
        layer.origin: layer.active for layer in loadout.layers
        if holders[layer.origin] is None or holders[layer.origin] is layer
    }
    # A library mod switched off by having its link taken away is not in the
    # folder to be read, and is still a mod this loadout may want on.
    for name, entry in library.unlinked(library_dir).items():
        origin = modfolder.bare_name(name)
        if origin in found.wanted and origin not in found.current.on:
            found.current.on[origin] = False
            found.current.folders[origin] = Path(entry.get("mods_folder") or folder) / name
    found.moving = switches.plan(found.current, found.wanted)
    found.strays = strays(found)
    # What the mods left loading say about each other: a mod needing one that
    # will not load, two that clash, one another replaces. Said here, where
    # the answer can still change what the game gets.
    if found.found.needs is not None:
        found.worth_knowing(tr("What these mods say about each other"), found.found.needs.lines())
        found.notes.append(found.found.needs.note())
    # A shared piece loading from another host's copy than the one read: the
    # rows, a patch to it, and your edits in it were all of the copy read.
    found.worth_knowing(tr("Mods loading from another copy"),
                        dependencies.copies_worth_knowing(loadout))
    # Two assets under one GUID: which one the game finds is decided by where
    # each stands in the file, and nothing in game says so.
    found.worth_knowing(tr("GUIDs taken twice"), list(repeats))
    # On Linux, a library the game's container cannot see is a mods folder of
    # links to nothing, from inside.
    found.worth_knowing(tr("What the game can see"), proton.unseen(library_dir, folder))

    # Anno 117 gets the Studio's one profile, written whole from the loadout
    # played and made the live one, rather than edits to whichever profile is
    # live now: the user's profiles stay theirs, and the loadout, not the
    # profile, is the record of what was played (its DisplayName says which).
    if loadout.game == games.ANNO_117:
        found.pointer_folder = switches.pointer_folder(folder, loadout.game)
        if found.pointer_folder is not None:
            found.own = True
            found.own_name = switches.STUDIO_PROFILE
        found.options = options_to_write(loadout)
    found.copies = copies
    found.worth_knowing(tr("Your edits to other people's mods"), copies.edits_held)
    # Whoever's mod it is: an edit to an op of a mod's folder is held when
    # the folder changes under it, and a mod of yours is read from the
    # folder it was carried on from.  Under the heading above, a mod of
    # yours read as somebody else's.
    found.worth_knowing(tr("Edits held after an update"), [
        trn("%(mod)s: %(n)s edit is held after an update and not written until you "
            "decide (right-click the mod in Mod changes)",
            "%(mod)s: %(n)s edits are held after an update and not written until you "
            "decide (right-click the mod in Mod changes)",
            count, mod=label)
        for label, count in _held_by_mod(loadout)
    ])
    found.loader = verdicts(yours) if verdicts is not None else []
    # A disagreement is worth knowing before the game starts; it is said,
    # never a reason not to start it -- the extension's loader may not be the
    # one the game has.  And a check that failed, which is no agreement: kept
    # among the verdicts and said nowhere, a mod the loader could not be run
    # on went to the game as quietly as one it agreed with.  And a check not
    # run, past the size that runs unasked or skipped at Play, for the same
    # reason.
    found.worth_knowing(tr("The game's loader"),
                        [verdict.describe() for verdict in found.loader
                         if verdict.state in ("differs", "failed", "not run")])
    return found


def current_build(loadout, origin: str, folder, library_dir) -> bool:
    """Whether the mods folder already loads the build of ``origin`` that
    building it now would make: its link there points at the library's copy,
    which the Studio built of the mod as it is now (:func:`build.behind`) and
    on the game data loaded now (:func:`builds.stale`).  Anything less -- no
    link, a real folder, a generated copy, a build of before -- is built."""
    if not library_dir:
        return False
    name = generator.folder_for(loadout, origin)
    copy = Path(library_dir) / name
    if not copy.is_dir() or not disk.same_file(library.target_of(Path(folder) / name), copy):
        return False
    return build.behind(loadout, origin, library_dir) is False and builds.stale(loadout, origin) is None


def strays(found: Plan) -> list:
    """What loads that the loadout's own switches will not see to: every mod
    it does not mention.  Its own mods it has off are in ``moving`` already,
    and switching one twice fails the second time."""
    return [mod for mod in found.found.switchable_others if mod.origin not in found.wanted]


def validate(loadout, origins, index) -> list:
    """What Export's check would say about the mods Play builds: changes that
    would not apply, and a Mod ID the game would refuse.  Said, not refused --
    the game is about to show whether it matters."""
    found = []
    for origin in origins:
        view = generator.for_mod(loadout, origin)
        name = view.own.name or origin or tr("your mod")
        failing = sum(
            1 for result in generator.verify_changes(index, view) if not result.ok
        ) if index is not None else 0
        if failing:
            found.append(trn(
                "%(mod)s: %(n)s change would not apply. The Export tab lists it.",
                "%(mod)s: %(n)s changes would not apply. The Export tab lists them.",
                failing, mod=name,
            ))
        why = games.get(loadout.game).mod_id_problem(modinfo.written_mod_id(view))
        if why:
            found.append("%s: %s" % (name, why))
    return found


def options_to_write(loadout) -> dict:
    """117: ModID to the options that differ from the author's defaults, for
    every mod the loadout has on that offers any -- and only where that is not
    already what the game's file says."""
    from . import imya
    from . import settings as settings_mod

    held = switches.game_options(loadout.game)
    wanted = {}
    off = loadout.switched_off()
    for layer in loadout.layers:
        if layer.origin in off or not layer.offered or not layer.mod_id:
            continue
        offered = settings_mod.from_dict(layer.offered)
        settled = offered.settled(layer.options)
        answers = {
            option.name: settled[option.name]
            for option in offered.settable
            if not imya.is_tweak_name(option.name)
            and settled.get(option.name, option.default) != option.default
        }
        if answers != held.get(layer.mod_id.strip().lower(), {}):
            wanted[layer.mod_id] = answers
    return wanted


@dataclass
class Copies:
    """What Play writes into generated copies: see :func:`generated_copies`."""

    tweaks: list = field(default_factory=list)
    tweaks_held: list = field(default_factory=list)
    generating: dict = field(default_factory=dict)
    reverting: list = field(default_factory=list)
    edited: list = field(default_factory=list)
    edits_held: list = field(default_factory=list)
    # The mods whose edits go into a generated copy, or would and could not:
    # never built over the library's copy either way.
    handled: set = field(default_factory=set)


def generated_copies(loadout, library_dir, index=None) -> Copies:
    """What goes into the generated copy of each library mod Play links in:
    the player's iModYourAnno settings (1800), and your edits to somebody
    else's mod (:mod:`core.edits`).  Never written into the library's copy,
    which stays as its author shipped it (:mod:`core.generated`).

    ``tweaks`` are the layers whose settings change what the game loads and
    ``tweaks_held`` the names of those that are not the library's to write --
    a folder of the user's own is theirs, and a mod there set differently
    here is named rather than rewritten.  Only a mod whose settings were
    read, so a loadout from before they were cannot put a folder back to its
    author's defaults by saying nothing.

    ``edited`` are the layers whose edits go into a copy, and ``edits_held``
    one sentence for each mod whose edits could not be worked out -- with
    no game files loaded, say -- which then goes to the game as the library
    has it.  Somebody else's mod that is not in the library is built where
    it is, as it always was, and is in neither.

    ``generating`` is each copy to build, by link name, and ``reverting`` the
    links to point back at the library's own copy, nothing of the Studio's
    being left in them.  A generated copy is of the folder in the mods
    folder, the link, so a mod bundled inside another is written into its
    host's copy.
    """
    from . import edits, generated, imya, patching

    found = Copies()
    held_links = library.links(library_dir) if library_dir else {}
    spoken_for = set()
    off = loadout.switched_off()
    for layer in loadout.layers:
        if layer.origin in off or not layer.folder:
            continue
        tweaked = any(imya.is_tweak_name(name) for name in layer.offered)
        theirs = not layer.patches and not patching.is_mine(loadout, layer)
        editing = theirs and edits.has_edits(loadout, layer.origin)
        link, inside = link_of(layer)
        source = library_copy(link, held_links, library_dir)
        if source is None:
            if tweaked:
                folder = Path(layer.folder)
                tweaks = imya.read(folder) if folder.is_dir() else None
                answers = {name: value for name, value in layer.options.items()
                           if imya.is_tweak_name(name)}
                if tweaks and imya.changed_files(folder, tweaks, answers):
                    found.tweaks_held.append(layer.label)
            continue
        # Every layer's link, not only theirs: a copy made while the mod was
        # somebody else's is stale once it is yours (kept, forked or home).
        spoken_for.add(link.name)
        if not tweaked and not editing:
            continue
        mod = source / inside if inside else source
        written: dict = {}
        notes: list = []
        if tweaked:
            answers = {name: value for name, value in layer.options.items() if imya.is_tweak_name(name)}
            tweaks = imya.read(mod) if mod.is_dir() else None
            written = imya.changed_files(mod, tweaks, answers) if tweaks else {}
            if written:
                notes.append({"kind": "iModYourAnno settings", "mod": layer.origin,
                              "settings": dict(sorted(answers.items()))})
        if editing:
            found.handled.add(layer.origin)
            done = edits.work_out(loadout, layer.origin, mod, index, base=written)
            if done.trouble:
                found.edits_held.append(tr(
                    "%(mod)s: your edits could not be worked out (%(why)s), so it "
                    "loads as the library has it", mod=layer.label, why=done.trouble))
            elif done.written:
                written = {**written, **done.written}
                notes.append({"kind": "edits", "mod": layer.origin, "hunks": len(done.hunks)})
        if not written:
            continue
        host = found.generating.setdefault(link.name, {
            "link": link, "source": source, "written": {}, "edits": [], "layers": [],
            "edited": [],
        })
        prefix = inside + "/" if inside else ""
        host["written"].update({prefix + rel: data for rel, data in written.items()})
        host["edits"].extend(notes)
        for note in notes:
            (host["edited"] if note["kind"] == "edits" else host["layers"]).append(layer)
    # Pointed back where nothing of the Studio's is left to write, and this
    # loadout has the mod the copy was made of.
    found.reverting = sorted(
        name for name in spoken_for
        if (held_links.get(name) or {}).get("generated") and name not in found.generating
    )
    # Already built of the same, and pointed at: nothing to do for those.
    for name in list(found.generating):
        entry = held_links.get(name) or {}
        host = found.generating[name]
        if entry.get("generated") and generated.is_current(
            Path(entry["generated"]), host["source"], host["written"]
        ):
            del found.generating[name]
            continue
        found.tweaks.extend(host["layers"])
        found.edited.extend(host["edited"])
    return found


def library_copy(link: Path, held_links: dict, library_dir) -> Optional[Path]:
    """The library's copy a link in the mods folder stands for, by the
    library's record of it -- or, for a link it has no record of, such as a
    build's, by where the junction leads -- or None for a folder that is not
    a copy in the library."""
    if not library_dir:
        return None
    entry = held_links.get(link.name)
    if entry is not None:
        source = Path(entry["target"]) if entry.get("target") else None
        if entry.get("kind", library.COPY) != library.COPY or source is None:
            return None
    else:
        source = library.source_of(link, library_dir)
        if source is None or not disk.inside(source, library_dir):
            return None
    return source if source.is_dir() else None


def _held_by_mod(loadout) -> list:
    """``(the mod's name, how many)`` for each mod with edits an update held."""
    counted: dict = {}
    for record in loadout.held_edits:
        counted[record.get("origin", "")] = counted.get(record.get("origin", ""), 0) + 1
    found = []
    for origin, count in counted.items():
        layer = loadout.layer(origin)
        found.append((layer.label if layer is not None else origin, count))
    return found


def link_of(layer) -> tuple:
    """``(the folder in the mods folder a layer's mod is reached through,
    the mod's path inside it -- "" for one that is not bundled)``."""
    parts = layer.origin.split("/")
    top = len(modfolder.host_of(layer.origin).split("/"))
    folder = Path(layer.folder)
    for _ in parts[top:]:
        folder = folder.parent
    return folder, "/".join(parts[top:])


# -- doing it -----------------------------------------------------------------------
def carry_out(found: Plan, loadout, library_dir, backup_into=None, display_name: str = "") -> switches.Written:
    """Do what the plan says, in its order; what was done.

    ``backup_into`` is where the copy of what a build writes over is kept;
    ``display_name`` is what Anno 117's own mod menu will call the profile.
    ``found.numbered`` says afterwards whether a build number moved on.
    """
    from . import backup

    written = switches.Written()
    if found.built:
        build.back_up(loadout, found.built, Path(found.folder), into=backup_into,
                      label=tr_noop("before Play wrote your mods"))
        built = build.write(loadout, found.built, Path(found.folder), library_dir)
        written.failed += [(name or tr("your mod"), why) for name, why in built.failed]
        found.numbered = built.numbered
        # The builds are links now, so what the game loads is asked again:
        # the strays are counted over the folder as it is, not as it was.
        found.found = truth.read(loadout, found.folder, library_dir=library_dir)
        found.strays = strays(found)

    # A folder rename undoes itself by renaming back, but Anno 117's profile
    # is a file being rewritten -- and it is the user's, with their comments
    # in it and the mods from their other folders listed in it.
    if found.current.profile is not None and not found.own:
        with contextlib.suppress(OSError):
            backup.take(
                found.folder, [], label=tr_noop("before switching mods"),
                game=loadout.game, extra=[found.current.profile],
            )
    # A library mod's switch is its link: taken away to switch it off, put
    # back to switch it on.  Everything else gets the game's own.
    by_link = switch_links(found.current, found.moving, library_dir)
    switched = {name for name, _path in by_link.linked} | {name for name, _why in by_link.failed}
    if loadout.game == games.ANNO_1800:
        # A mod bundled inside one of those went the same way with its link:
        # Anno 1800 reads its switch off its host, so it is switched, and not
        # one the game's own switches are to be asked about -- they would not
        # see the host among theirs, and say it could not be changed.
        linked = {name for name, _path in by_link.linked}
        switched |= {
            name for name, on in found.moving.items()
            if modfolder.is_bundled(name) and name.split("/")[0] in linked
            and found.moving.get(name.split("/")[0]) == on
        }
    moving = {name: on for name, on in found.moving.items() if name not in switched}
    wanted = {name: on for name, on in found.wanted.items() if name not in switched}
    if found.own and found.pointer_folder is not None:
        also = write_own_profile(loadout, found.pointer_folder, found.own_name, found.found, display_name)
    else:
        also = switches.write(found.current, wanted, ids={
            layer.origin: layer.mod_id for layer in loadout.layers
        }) if moving else switches.Written()
        if found.strays:
            also.merge(truth.switch_off(found.found, library_dir, found.strays))
    for part in (also, by_link):
        written.merge(part)
    # A renamed folder is the same mod in a new place; the loadout holds a
    # path to it, and following the rename is cheaper than making the user
    # read the folder again to fix a path the Studio itself moved.  The mods
    # bundled inside it went with it: an origin is the path below the mods
    # folder, so what an origin has after its host's is where it is in there.
    # Only a path the move left with nothing at it, though, and only for one
    # that is there: a library mod's link moves and its copy does not, so a
    # bundled mod's path into the copy is right whichever way the link went,
    # and the path through a link taken away is one nothing is at.
    for origin, path in written.moved:
        for layer in loadout.layers:
            if layer.origin == origin:
                layer.folder = str(path)
            elif layer.folder and layer.origin.startswith(origin + "/"):
                inside = Path(path, *layer.origin[len(origin) + 1:].split("/"))
                if inside.is_dir() and not Path(layer.folder).is_dir():
                    layer.folder = str(inside)

    if found.options:
        try:
            target = switches.options_file(loadout.game)
            if target is not None and target.is_file():
                with contextlib.suppress(OSError):
                    backup.take(
                        str(target.parent), [], label=tr_noop("before writing mod options"),
                        game=loadout.game, extra=[target],
                    )
            switches.write_game_options(loadout.game, found.options)
        except OSError as exc:
            written.failed.append((switches.OPTIONS_FILE, str(exc)))
    held_links = library.links(library_dir)
    for name, host in found.generating.items():
        try:
            if name not in held_links:
                # A link the library has no record of: recorded now, as the
                # library's, so it can be pointed and put back like the rest.
                library.note_link(library_dir, library.Linked(link=host["link"], target=host["source"]),
                                  library.COPY)
            made = library.generate(library_dir, name, host["written"], edits=host["edits"])
        except OSError as exc:
            written.failed.append((name, str(exc)))
            continue
        if made is not None and made.wrong:
            written.failed.append((name, tr(
                "its generated copy did not come out as it should (%s), so the game "
                "loads the library's copy", ", ".join(made.wrong[:3])
            )))
    for name in found.reverting:
        try:
            library.generate(library_dir, name, {})
        except OSError as exc:
            written.failed.append((name, str(exc)))
    return written


def switch_links(current, moving: dict, library_dir) -> switches.Written:
    """Move the switches that are links of the library's, and say which.

    Returns a :class:`core.switches.Written` whose ``linked`` names each mod
    moved this way (so the caller leaves them out of the game's own
    switches); a name not in it is not the library's to switch.
    """
    done = switches.Written()
    held = library.links(library_dir)
    for name, on in moving.items():
        folder = Path(current.folders.get(name) or (current.folder / name))
        if folder.name not in held and name not in held:
            continue
        link_name = folder.name if folder.name in held else name
        if on and library.is_link(folder):
            # Linked already, and off by the game's own switch -- a "-" on
            # the link's name: that switch is the one to move.
            continue
        if on:
            at = library.relink(library_dir, link_name)
            if at is None:
                done.failed.append((name, tr("its link could not be put back")))
            else:
                done.linked.append((name, at))
        else:
            if library.unlink(library_dir, folder.parent / link_name):
                done.linked.append((name, folder.parent / link_name))
            else:
                done.failed.append((name, tr("its link could not be taken away")))
    return done


def write_own_profile(loadout, pointer_folder, name: str, found, display_name: str = "") -> switches.Written:
    """``loadout`` as a 117 profile: its mods on or off as it says, every
    other installed mod off, unlisted mods off, and the pointer at it."""
    from . import backup

    on = {}
    written = switches.Written()
    built = set(build.mine(loadout))
    if "" in built:
        # The loadout's own mod, built a moment ago: no layer, but on.
        own_id = modinfo.written_mod_id(generator.for_mod(loadout, "")).strip().lower()
        if own_id:
            on[own_id] = True
    for layer in loadout.layers:
        if modfolder.is_bundled(layer.origin):
            continue  # bundled: switches with its host
        mod_id = (layer.mod_id or "").strip().lower()
        if not mod_id and layer.origin in built:
            # A patch of yours names itself only in the modinfo its build wrote.
            mod_id = modinfo.written_mod_id(generator.for_mod(loadout, layer.origin)).strip().lower()
        if not mod_id:
            written.failed.append((layer.origin, tr("declares no ModID for the profile to name")))
            continue
        on[mod_id] = bool(layer.active)
    for mod in found.mods if found is not None else []:
        mod_id = (mod.installed.mod_id or "").strip().lower()
        if mod_id and mod_id not in on:
            on[mod_id] = False
    extra = [pointer_folder / switches.POINTER_FILE]
    live = switches.active_profile(pointer_folder)
    if live is not None:
        extra.append(live)
    with contextlib.suppress(OSError):
        backup.take(
            str(pointer_folder), [], label=tr_noop("before switching mods"),
            game=loadout.game, extra=[p for p in extra if p.is_file()],
        )
    try:
        written.profile = switches.write_profile(
            pointer_folder, name, display_name, on, frozen=True,
        )
        switches.point_at(pointer_folder, written.profile.stem)
    except OSError as exc:
        written.failed.append((name, str(exc)))
        return written
    written.listed = len(on)
    return written
