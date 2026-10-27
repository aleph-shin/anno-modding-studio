"""What the game will load right now, beside what the loadout says.

Three things can each claim to be "your mods".  The loadout says which mods
were read in and which of them are on; the mods folders say which are
installed; the game, tonight, loads the installed ones that are switched on --
1800 by a mark on the folder name, 117 by its live profile -- one copy per
ModID.  Install a collection, read another folder in, try three sets in three
loadouts, and the three drift apart: the loadout holds twenty-six mods and the
disk sixty-three, and nothing has asked what should be *off*.

This is the comparison, computed from disk every time it is asked for and
never remembered, as a grid: what the loadout says of each installed mod (on,
off, or nothing) against what the disk says (it loads, or it does not).  The
diagonal is agreement; the three cells off it are the mismatches, and the
sentence for the status bar counts them.  The things to do about each already
exist -- reading the folder in makes the loadout say what the disk does, and
writing the game's switches makes the disk say what the loadout does -- so
this only names which mods each would touch.

One source the Studio can report on but not switch: mods the game's own
browser installed for Anno 1800.  Its download cache is keyed by path, so a
folder renamed there is one the client no longer knows about; and 1800 has no
profile to list them in.  Those are said to be on, and said to be the
browser's to switch.  117's profile names mods by ID wherever they sit, so
there they are switched like any other.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

from . import dependencies, installplan, modfolder, switches
from .games import ANNO_117
from .i18n import join_list, number, tr, trn


@dataclass
class Mod:
    """One mod the game would consider, and what each side says of it."""

    installed: installplan.FolderMod
    on: bool
    # Whether this is the copy the loader keeps, among copies of one ModID:
    # the switched-on one with the highest version.
    loads: bool = False
    # The loadout's layer for it, if the loadout mentions it at all.
    origin: str = ""
    wanted: Optional[bool] = None  # the loadout's switch, when it has one
    # Whether a profile decides its switch, which reaches into the cache;
    # otherwise the folder name does, and the cache's folders are not ours.
    profile_governed: bool = False

    @property
    def key(self) -> str:
        return self.installed.key

    @property
    def name(self) -> str:
        return self.installed.name or self.installed.folder_name

    @property
    def unswitchable(self) -> bool:
        return self.installed.in_cache and not self.profile_governed


@dataclass
class Truth:
    game: str
    folder: Path
    mods: List[Mod] = field(default_factory=list)
    # Which loadout the Studio last made live, by name ("" for none), and
    # whether that is this one.  Set by whoever read the truth, from
    # :func:`core.library.live`: it is the one fact here the disk alone
    # cannot say.
    live: str = ""
    is_live: bool = False
    # The chosen profile, by name, when the loadout has more than one: what
    # the loadout's side of the grid is, and what Play would make live. ""
    # for a loadout of one profile, which is simply the loadout.
    playing: str = ""
    # Folders the loader lists as mods and loads nothing from; no part of the
    # grid, since nothing they hold reaches the game.
    inert: List[installplan.FolderMod] = field(default_factory=list)
    # For the live loadout: what the game's loader wrote down the last time
    # it started since then -- see :mod:`core.loaderlog` -- or None.
    log_check: object = None
    # And whether it has started since at all.
    started_since: bool = False
    # Copies in the game's mod browser's folder of mods the library holds --
    # see :mod:`core.unsubscribe` -- set by whoever read this, since what was
    # unsubscribed already is written in the library.
    left_in_cache: list = field(default_factory=list)
    # 117's live profile, when there is one; what the switches were read from.
    profile: Optional[Path] = None
    # The inventory this was read from, so the loadout's side can be asked
    # again without the disk: a switch flipped in the changes panel moves the
    # counts, and the folders have not changed.
    installed: List[installplan.FolderMod] = field(default_factory=list)
    # What the mods Play would leave loading say about each other -- one
    # needing another that will not load, two that clash, one replacing
    # another -- see :mod:`core.dependencies`.
    needs: Optional[dependencies.Check] = None
    # GUIDs two of the loadout's loading mods create, or one and the base
    # game, one sentence each -- see :func:`core.repeats.before_play`. Set by
    # the window, which has the index that knows the shipped assets.
    repeats: List[str] = field(default_factory=list)
    # How many GUIDs those sentences are about, for the card's count.
    repeated: int = 0
    # Names of the loadout's mods that are nowhere in the mods folder yet --
    # one of the user's own, not built until Play -- by the loadout's switch.
    # Shown in the grid's loadout rows, in the "does not load" column, and
    # in no other count: Play builds them rather than switching them.
    unbuilt_on: List[str] = field(default_factory=list)
    unbuilt_off: List[str] = field(default_factory=list)

    # -- the sets ------------------------------------------------------------
    @property
    def loading(self) -> List[Mod]:
        """What the game will load: one copy per ModID, switched on."""
        return [mod for mod in self.mods if mod.loads]

    @property
    def in_loadout(self) -> List[Mod]:
        return [mod for mod in self.loading if mod.origin and mod.wanted]

    @property
    def others(self) -> List[Mod]:
        """Loading, and the loadout either does not mention it or has it off."""
        return [mod for mod in self.loading if not (mod.origin and mod.wanted)]

    @property
    def switchable_others(self) -> List[Mod]:
        return [mod for mod in self.others if not mod.unswitchable]

    @property
    def unswitchable_others(self) -> List[Mod]:
        return [mod for mod in self.others if mod.unswitchable]

    @property
    def wanted_but_off(self) -> List[Mod]:
        """The loadout has it on; every installed copy is switched off."""
        keys_on = {mod.key for mod in self.loading}
        seen = set()
        found = []
        for mod in self.mods:
            if mod.origin and mod.wanted and mod.key not in keys_on and mod.key not in seen:
                seen.add(mod.key)
                found.append(mod)
        return found

    # -- the grid ------------------------------------------------------------
    # Rows are what the loadout says; columns are what the disk says.  The
    # "loads" column is one copy per ModID, the one the loader keeps; the
    # "off" column is one entry per ModID with no copy loading.
    @property
    def off_loads(self) -> List[Mod]:
        """The loadout has it off; the game will load it."""
        return [mod for mod in self.loading if mod.origin and not mod.wanted]

    @property
    def none_loads(self) -> List[Mod]:
        """The loadout does not mention it; the game will load it."""
        return [mod for mod in self.loading if not mod.origin]

    @property
    def off_off(self) -> List[Mod]:
        """The loadout has it off, and so is every installed copy."""
        return self._off(lambda mod: bool(mod.origin) and not mod.wanted)

    @property
    def none_off(self) -> List[Mod]:
        """The loadout does not mention it, and it is switched off."""
        return self._off(lambda mod: not mod.origin)

    @property
    def strangers(self) -> List[Mod]:
        """Installed, and the loadout does not mention it, loading or not: a
        mod put into the mods folder since the loadout was read."""
        return self.none_loads + self.none_off

    def _off(self, pick) -> List[Mod]:
        keys_on = {mod.key for mod in self.loading}
        seen = set()
        found = []
        for mod in self.mods:
            if pick(mod) and mod.key not in keys_on and mod.key not in seen:
                seen.add(mod.key)
                found.append(mod)
        return found

    @property
    def differ(self) -> int:
        """How many mods the two sides disagree about: the off-diagonal."""
        return len(self.others) + len(self.wanted_but_off)

    @property
    def changes(self) -> int:
        """How many mods making this loadout live would switch: the
        off-diagonal, less what the game's own browser holds and the Studio
        cannot switch."""
        return len(self.switchable_others) + len(self.wanted_but_off)

    @property
    def drift(self) -> bool:
        """The live loadout, no longer what the game loads: something was
        installed or switched since it was made live, in the mods folder or
        in the loadout itself -- which of the two the grid cannot say.
        The one mismatch on the card that is a fault -- for any other loadout
        the off-diagonal is a preview of what Play would change."""
        return self.is_live and self.changes > 0

    @property
    def subject(self) -> str:
        """What the loadout's side is, in a sentence: the chosen profile's
        name, or "this loadout"."""
        if self.playing:
            return self.playing
        # Translators: stands in for a profile's name in "Matches …" and
        # "Playing … changes 3".
        return tr("this loadout")

    # -- the sentence --------------------------------------------------------
    def describe(self) -> str:
        """The grid's mismatches in one line, for the status bar."""
        if self.matches:
            return tr("Matches %(subject)s: the game will load %(mods)s and "
                      "nothing else.", subject=self.subject,
                      mods=_mods(len(self.loading)))
        if not self.drift:
            return self._describe_play()
        # Clauses after a colon, each whole: a list, not one sentence.
        bits = []
        held = self.wanted_but_off
        if held:
            # Translators: a count of mods the loadout has switched on.
            bits.append(trn("%(n)s it has on is off on disk",
                            "%(n)s it has on are off on disk", len(held)))
        held = self.off_loads
        if held:
            # Translators: a count of mods the loadout has switched off.
            bits.append(trn("%(n)s it has off will load",
                            "%(n)s it has off will load", len(held)))
        held = self.none_loads
        if held:
            # Translators: a count of mods the loadout does not mention.
            bits.append(trn("%(n)s it does not mention will load",
                            "%(n)s it does not mention will load", len(held)))
        return trn("%(n)s changed since %(subject)s was made live: %(what)s.",
                   "%(n)s changed since %(subject)s was made live: %(what)s.",
                   self.differ, subject=self.subject, what="; ".join(bits))

    def _describe_play(self) -> str:
        """What Play would do to each mod the two sides disagree about.

        It read "Playing this loadout changes 68: 68 it does not mention will
        load", which says what the game loads as things stand in words that
        read as what Play brings about, and leaves out which way the 68 would
        go.  Every loadout is complete when played (:mod:`core.play`): what
        it has on is switched on, and everything else that loads is switched
        off, whether the loadout has it off or does not have it at all.  The
        game's own mod browser's mods are the exception -- see
        :attr:`Mod.unswitchable` -- and are said to stay as they are.
        """
        # Clauses after a colon, each whole: a list, not one sentence.
        bits = []
        held = self.wanted_but_off
        if held:
            # Translators: a count of mods the loadout has switched on.
            bits.append(trn("%(n)s mod it has on would be switched on",
                            "%(n)s mods it has on would be switched on", len(held)))
        held = [mod for mod in self.off_loads if not mod.unswitchable]
        if held:
            # Translators: a count of mods the loadout has switched off.
            bits.append(trn("%(n)s mod it has off would be switched off",
                            "%(n)s mods it has off would be switched off", len(held)))
        held = [mod for mod in self.none_loads if not mod.unswitchable]
        if held:
            # Translators: a count of mods the loadout does not have at all.
            bits.append(trn("%(n)s mod that is not in it would be switched off",
                            "%(n)s mods that are not in it would be switched off",
                            len(held)))
        held = self.unswitchable_others
        if held:
            # Translators: a count of mods that load and the Studio cannot
            # switch off.
            bits.append(trn("%(n)s mod the game's own mod browser installed would stay on",
                            "%(n)s mods the game's own mod browser installed would stay on",
                            len(held)))
        # Translators: "Play" is the Studio's button that makes the loadout
        # what the game loads and starts it; %(subject)s is "this loadout" or
        # a profile's name.
        return tr("Play would make %(subject)s what the game loads: %(what)s.",
                  subject=self.subject, what="; ".join(bits))

    def status(self) -> tuple:
        """The card's header: a few words, and "ok", "todo" or "".

        Only drift is something to fix.  Anything else off the diagonal is
        what Play would change -- a preview, and it is not orange: a
        collection being tried is not wrong for not being what the game
        loads."""
        if self.matches:
            # Translators: the Play card's header when what the loadout has
            # on is exactly what the game loads.
            return tr("Matches", context="the game loads it"), "ok"
        if not self.changes:
            # Translators: a count of mods, on the Play card's header.
            return trn("%(n)s the Studio cannot switch",
                       "%(n)s the Studio cannot switch",
                       len(self.unswitchable_others)), ""
        if self.drift:
            # Translators: a count of mods, on the Play card's header.
            return trn("%(n)s changed since it was made live",
                       "%(n)s changed since it was made live", self.changes), "todo"
        if self.playing:
            # Translators: a profile's name, and a count of mods.
            return trn("Playing %(profile)s changes %(n)s",
                       "Playing %(profile)s changes %(n)s",
                       self.changes, profile=self.playing), ""
        # Translators: "Play" is the Studio's launch tab; the count is of mods.
        return trn("Play changes %(n)s", "Play changes %(n)s", self.changes), ""

    def describe_live(self) -> tuple:
        """Which loadout the game loads, and what that means for this one:
        a headline and a sentence under it."""
        here = (tr("Live: this loadout (%s)", self.playing) if self.playing
                else tr("Live: this loadout"))
        # What loads beside what it has on, and Play leaves loading: said
        # wherever the sentence says what the game loads, as the header and
        # the status bar say it.
        kept = len(self.unswitchable_others)
        if self.is_live:
            if self.drift:
                # Not "outside the Studio", and not "Play puts it back": a
                # switch flipped in the live loadout is drift as well, and
                # Play then takes the game to the loadout.
                if kept:
                    # Two sentences, a count each.  Neither says how many
                    # differ nor that Play makes the two match: what the
                    # game's own mod browser installed differs too, and goes
                    # on loading after Play.
                    return here, " ".join((trn(
                        "The game no longer loads what it has on: Play "
                        "switches %(n)s mod that differs.",
                        "The game no longer loads what it has on: Play "
                        "switches %(n)s mods that differ.",
                        self.changes,
                    ), trn(
                        "%(n)s mod the game's own mod browser installed "
                        "loads as well, and Play leaves it on.",
                        "%(n)s mods the game's own mod browser installed "
                        "load as well, and Play leaves them on.",
                        kept,
                    )))
                return here, trn(
                    "The game no longer loads what it has on: %(n)s mod "
                    "differs. Play switches that mod to match.",
                    "The game no longer loads what it has on: %(n)s mods "
                    "differ. Play switches those mods to match.",
                    self.changes,
                )
            if kept:
                return here, trn(
                    "The game loads what it has on, and %(n)s mod the game's "
                    "own mod browser installed.",
                    "The game loads what it has on, and %(n)s mods the game's "
                    "own mod browser installed.", kept,
                )
            return here, tr("The game loads what it has on, and nothing else.")
        head = tr("Live: %s", self.live) if self.live else tr("Live: nothing the Studio made")
        # Translators: stands in for a profile's name in "Play makes … live".
        # Said to be the profile: the first is Main, and "Play makes Main
        # live" read as another loadout called Main (R3-33, hands-on run 3).
        which = tr("its profile %s", self.playing) if self.playing else tr("this one")
        mods = _mods(len(self.loading))
        if not self.changes and kept and self.live:
            return head, trn(
                "Play makes %(which)s live and changes nothing: the game loads "
                "%(live)s now (%(mods)s), which is what it has on and %(n)s "
                "mod the game's own mod browser installed.",
                "Play makes %(which)s live and changes nothing: the game loads "
                "%(live)s now (%(mods)s), which is what it has on and %(n)s "
                "mods the game's own mod browser installed.",
                kept, which=which, live=self.live, mods=mods,
            )
        if not self.changes and kept:
            return head, trn(
                "Play makes %(which)s live and changes nothing: the game loads "
                "a set the Studio did not make now (%(mods)s), which is what it "
                "has on and %(n)s mod the game's own mod browser installed.",
                "Play makes %(which)s live and changes nothing: the game loads "
                "a set the Studio did not make now (%(mods)s), which is what it "
                "has on and %(n)s mods the game's own mod browser installed.",
                kept, which=which, mods=mods,
            )
        if not self.changes and self.live:
            return head, tr(
                "Play makes %(which)s live and changes nothing: the game loads "
                "%(live)s now (%(mods)s), which is what it has on.",
                which=which, live=self.live, mods=mods,
            )
        if not self.changes:
            return head, tr(
                "Play makes %(which)s live and changes nothing: the game loads "
                "a set the Studio did not make now (%(mods)s), which is what it "
                "has on.", which=which, mods=mods,
            )
        if self.live:
            return head, trn(
                "Play makes %(which)s live, changing %(n)s mod. The game "
                "loads %(live)s now (%(mods)s).",
                "Play makes %(which)s live, changing %(n)s mods. The game "
                "loads %(live)s now (%(mods)s).",
                self.changes, which=which, live=self.live, mods=mods,
            )
        return head, trn(
            "Play makes %(which)s live, changing %(n)s mod. The game now "
            "loads a set the Studio did not make (%(mods)s).",
            "Play makes %(which)s live, changing %(n)s mods. The game now "
            "loads a set the Studio did not make (%(mods)s).",
            self.changes, which=which, mods=mods,
        )

    @property
    def library_off(self) -> List[Mod]:
        """Mods the Studio installed that are switched off -- one a ModID,
        and none that another copy has on.  Not the loadout's view but the
        library's: a mod the Studio put there and the game is ignoring is
        worth a line whatever the loadout says of it."""
        keys_on = {mod.key for mod in self.loading}
        seen = set()
        found = []
        for mod in self.mods:
            if mod.installed.kind and not mod.on and mod.key not in keys_on and mod.key not in seen:
                seen.add(mod.key)
                found.append(mod)
        return found

    def describe_needs(self) -> str:
        """What the mods Play would leave loading say about each other, or ""
        when they say nothing wrong."""
        return self.needs.describe() if self.needs is not None else ""

    def describe_repeats(self) -> str:
        """The GUIDs taken twice among what loads, for the card, or "": the
        one sentence, or a count. The list is in “Mods reaching for the same
        thing”, where each GUID is a row."""
        if not self.repeats:
            return ""
        if len(self.repeats) == 1:
            return tr("GUIDs taken twice by what loads:") + "\n• " + self.repeats[0]
        # A sentence is a set of mods, which can share any number of GUIDs.
        return trn(
            "%(n)s GUID is taken twice by what loads. The game finds only one "
            "of the assets under it.",
            "%(n)s GUIDs are taken twice by what loads. The game finds only "
            "one of the assets under each.",
            max(self.repeated, len(self.repeats)),
        )

    def describe_cache(self) -> str:
        held = self.unswitchable_others
        if not held:
            return ""
        return trn(
            "The game's own mod browser installed %(n)s of the mods that "
            "load, outside this loadout. The Studio cannot switch those "
            "until they are copied into the library and unsubscribed from.",
            "The game's own mod browser installed %(n)s of the mods that "
            "load, outside this loadout. The Studio cannot switch those "
            "until they are copied into the library and unsubscribed from.",
            len(held),
        )

    @property
    def adoptable(self) -> List[Mod]:
        """Installed mods that are nobody's but the user's: not the library's,
        not the game browser's cache, not Ubisoft's ``.ubi``.  What *Move
        them into the library* is about -- every one is a mod the Studio is
        not managing yet, which is the nudge, and none of it moves until
        asked."""
        return [
            mod for mod in self.mods
            if not mod.installed.kind and not mod.installed.in_cache
            and mod.installed.folder.parent.name.lower() != ".ubi"
        ]

    def describe_adoptable(self) -> str:
        held = len(self.adoptable)
        if not held:
            return ""
        return trn("%(n)s mod in your folders is not in the library.",
                   "%(n)s mods in your folders are not in the library.", held)

    def describe_inert(self) -> str:
        """Folders the game lists as mods and loads nothing from."""
        # Translators: a folder's name in quotation marks.
        loose = [tr("“%s”", mod.folder.name, context="quoted name")
                 for mod in self.inert if mod.loose]
        # Translators: a folder's name in quotation marks.
        other = [tr("“%s”", mod.folder.name, context="quoted name")
                 for mod in self.inert if not mod.loose]
        said = []
        if loose:
            said.append(trn(
                "%(names)s in your mods folder is a mod's contents without a "
                "folder of its own: a zip extracted straight into the mods "
                "folder. The game lists it as a mod and loads nothing from "
                "it. Put it in a folder named for the mod if you want it to "
                "work.",
                "%(names)s in your mods folder are a mod's contents without "
                "a folder of their own: a zip extracted straight into the "
                "mods folder. The game lists them as a mod and loads nothing "
                "from them. Put them in a folder named for the mod if you "
                "want it to work.",
                len(loose), names=join_list(loose),
            ))
        if other:
            said.append(trn(
                "%(names)s in your mods folder is no mod: the game lists it and "
                "loads nothing from it.",
                "%(names)s in your mods folder are no mod: the game lists them "
                "and loads nothing from them.",
                len(other), names=join_list(other),
            ))
        return " ".join(said)

    @property
    def in_library(self) -> List[Mod]:
        """What loads that the Studio's library holds or stands for."""
        return [mod for mod in self.loading if mod.installed.kind]

    def describe_managed(self) -> str:
        """The third axis, in one line: of everything installed, what the
        Studio's library holds, what is the user's own, and what the game's
        browser put there.  A nudge, not a mismatch -- so it sits under the
        grid rather than in it, and says how it overlaps."""
        total = len(self.mods)
        if not total:
            return tr("No mods are installed.")
        library = [mod for mod in self.mods if mod.installed.kind]
        cache = [mod for mod in self.mods if mod.installed.in_cache]
        yours = self.adoptable
        # Counts after a colon, each whole: a list, not one sentence.
        bits = []
        if library:
            off = len(self.library_off)
            if off:
                bits.append(trn(
                    "%(n)s in the Studio's library (%(off)s switched off)",
                    "%(n)s in the Studio's library (%(off)s switched off)",
                    len(library), off=number(off),
                ))
            else:
                bits.append(trn("%(n)s in the Studio's library",
                                "%(n)s in the Studio's library", len(library)))
        if yours:
            bits.append(trn("%(n)s of your own", "%(n)s of your own", len(yours)))
        if cache:
            bits.append(trn("%(n)s the game's own mod browser installed",
                            "%(n)s the game's own mod browser installed", len(cache)))
        rest = total - len(library) - len(yours) - len(cache)
        if rest > 0:
            # Translators: a count of mods Ubisoft Connect installed.
            bits.append(trn("%(n)s Ubisoft's", "%(n)s Ubisoft's", rest))
        return trn("%(n)s installed: %(what)s.", "%(n)s installed: %(what)s.",
                   total, what=tr(", ", context="between items of a list").join(bits))

    @property
    def matches(self) -> bool:
        return not self.others and not self.wanted_but_off


def fingerprint(found: Truth) -> str:
    """What the mods folder says, in one short string: whether each mod loads,
    by its switches as the game reads them -- links, marks and the live
    profile alike -- and, for Anno 117, the options file.  Taken when Play has
    written the folder and again later, it says whether anything has changed
    it since.

    One entry per mod, on if any copy of it is: a copy another one shadows
    is not what the game loads.  It used to be one entry per copy, and the
    mod browser dropping copies the library shadows -- after a fresh start of
    its folder, with the game loading exactly what it did -- read as the
    folder changed outside the Studio."""
    on: dict = {}
    for mod in found.mods:
        on[mod.key] = on.get(mod.key, False) or bool(mod.on)
    return _hashed(found, sorted("%s=%d" % (key, int(value)) for key, value in on.items()))


def fingerprint_before(found: Truth) -> str:
    """The fingerprint as it was taken before -- one entry per copy -- so a
    loadout made live then is not said to have changed for the new way of
    counting alone."""
    return _hashed(found, sorted("%s=%d" % (mod.key, int(mod.on)) for mod in found.mods))


def _hashed(found: Truth, parts) -> str:
    import hashlib

    parts = list(parts)
    if found.game == ANNO_117:
        path = switches.options_file(found.game)
        try:
            parts.append(path.read_text(encoding="utf-8-sig") if path is not None and path.is_file() else "")
        except OSError:
            parts.append("?")
    return hashlib.sha1("\n".join(parts).encode("utf-8")).hexdigest()


def _own_mod(loadout):
    """``(the keys it is found by, whether it should be on)`` for the
    loadout's own mod, or None for a loadout without one."""
    has = getattr(loadout, "has_own_mod", None)
    if has is None or not has():
        return None
    from . import generator, modinfo

    view = generator.for_mod(loadout, "")
    keys = {generator.folder_for(loadout, "").lower()}
    mod_id = modinfo.written_mod_id(view).strip().lower()
    if mod_id:
        keys.add(mod_id)
    return keys, generator.writes_anything(view)


def _mods(count: int) -> str:
    """"no mods", "1 mod", "41 mods"."""
    return trn("%(n)s mod", "%(n)s mods", count) if count else tr("no mods")


def read(loadout, folder, in_folders: Optional[List[installplan.FolderMod]] = None, library_dir=None) -> Truth:
    """The truth for ``loadout`` against ``folder`` and every folder loaded
    beside it.  ``in_folders`` is the inventory, if the caller has just taken
    one; otherwise it is taken here, against ``library_dir``."""
    game = loadout.game
    folder = Path(folder)
    if in_folders is None:
        links = {
            layer.mod_id.lower(): layer.modio_id
            for layer in loadout.layers if layer.mod_id and layer.modio_id
        }
        in_folders = installplan.inventory(game, folder, links, library_dir=library_dir)
    # A bundled mod switches with its host and the host answers for it.
    tops = [mod for mod in in_folders if not mod.bundled and not mod.inert]

    truth = Truth(
        game=game, folder=folder,
        installed=list(in_folders),
        inert=[mod for mod in in_folders if mod.inert],
    )
    profile = None
    if game == ANNO_117:
        path = switches.profile_for(folder, game)
        if path is not None:
            profile = switches.read_profile(path)
            truth.profile = path

    by_key: Dict[str, List] = {}
    # Anno 1800's other switch, an activation file listing mods to skip.
    listed = switches.disabled_ids(game, folder)
    for one in tops:
        if not one.linked:
            # A library mod with no link: the game does not see it at all.
            on = False
        elif profile is not None:
            key = one.mod_id.lower()
            on = profile.on.get(key, profile.new_mods_on) if key else profile.new_mods_on
        else:
            on = (
                not modfolder.is_marked_off(one.folder.name)
                and switches.activation_key(one.folder.name, one.mod_id) not in listed
            )
        mod = Mod(installed=one, on=on, profile_governed=profile is not None)
        truth.mods.append(mod)
        # Grouped as the loader keeps one copy: by ModID as written.
        by_key.setdefault(modfolder.loader_key(one.mod_id, one.folder_name), []).append(mod)

    # What the loadout says, matched the way the plan matches: ModID first.
    layers: dict = {}
    bundled = []
    for layer in loadout.layers:
        if modfolder.is_bundled(layer.origin):
            bundled.append(layer)
            continue  # bundled: its host answers
        own = modfolder.folder_name(layer.origin)
        layers.setdefault((layer.mod_id or own).lower(), layer)
        layers.setdefault(modfolder.bare_name(own).lower(), layer)
    # A host with nothing of its own to read -- a folder holding its modinfo
    # and, one level down, the mod that does the work -- comes in as that
    # mod alone, and the host is what the game lists.  The mod answers for it,
    # or the host reads as one the loadout never mentioned.
    for layer in bundled:
        host = modfolder.bare_name(modfolder.folder_name(modfolder.host_of(layer.origin))).lower()
        layers.setdefault(host, layer)
    met = set()
    for mod in truth.mods:
        layer = layers.get(mod.key) or layers.get(mod.installed.folder_name.lower())
        if layer is not None:
            mod.origin, mod.wanted = layer.origin, bool(layer.active)
            met.add(id(layer))
    # The loadout's mods with nothing installed to answer for them: the card
    # counts them, or a loadout of one mod of the user's own, not built yet,
    # has nothing on.
    for layer in loadout.layers:
        if id(layer) not in met and not modfolder.is_bundled(layer.origin):
            name = layer.name or modfolder.folder_name(layer.origin)
            (truth.unbuilt_on if layer.active else truth.unbuilt_off).append(name)
    # The loadout's own mod is no layer -- it is the loadout -- but once Play
    # has built it, it is a folder the game loads, and it is this loadout's:
    # on while it has anything to say.
    own = _own_mod(loadout)
    if own is not None:
        keys, wanted = own
        built = False
        for mod in truth.mods:
            if not mod.origin and (mod.key in keys or mod.installed.folder_name.lower() in keys):
                mod.origin, mod.wanted = mod.installed.folder_name, wanted
                built = True
        if not built:
            (truth.unbuilt_on if wanted else truth.unbuilt_off).append(
                getattr(loadout, "name", "") or tr("Your own mod"))

    # The loader keeps one copy per ModID, of those switched on.
    for copies in by_key.values():
        loads = modfolder.loading_copy(
            [mod for mod in copies if mod.on],
            version=lambda mod: mod.installed.version,
            in_cache=lambda mod: mod.installed.in_cache,
        )
        if loads is not None:
            loads.loads = True
    truth.mods.sort(key=lambda mod: mod.name.lower())
    truth.needs = dependencies.for_loadout(loadout, truth)
    return truth


def _by_link(truth: Truth, mods: List[Mod], library_dir) -> tuple:
    """Split mods into those whose switch is a junction of the library's and
    the rest, whose switch is the game's own."""
    from . import library

    held = library.links(library_dir) if library_dir else {}
    linked, rest = [], []
    for mod in mods:
        if mod.installed.kind and mod.installed.folder.name in held:
            linked.append(mod)
        else:
            rest.append(mod)
    return linked, rest


def switch_off(truth: Truth, library_dir=None, mods: Optional[List[Mod]] = None) -> switches.Written:
    """Switch every stray the Studio can switch off, the way the game reads
    it -- or just ``mods``, one row of the grid.

    A library mod's link is taken away -- the library keeps the folder, and
    the game no longer sees it.  Anything else gets the game's own switch:
    renames for 1800, the profile for 117.  Nothing deleted, nothing rebuilt,
    and nothing in the mod.io cache touched.  The caller confirms first and
    backs the profile up, as the sync does.
    """
    from . import library

    chosen = truth.switchable_others if mods is None else [mod for mod in mods if not mod.unswitchable]
    by_link, strays = _by_link(truth, chosen, library_dir)
    written = switches.Written()
    for mod in by_link:
        if library.unlink(library_dir, mod.installed.folder):
            written.linked.append((mod.installed.folder_name, mod.installed.folder))
        else:
            written.failed.append((mod.name, tr("its link could not be taken away")))
    if not strays:
        return written
    names = [mod.installed.folder_name for mod in strays]
    ids = {mod.installed.folder_name: mod.installed.mod_id for mod in strays}
    # What was read above is the loadout, stray by stray: each is on, each is
    # where the inventory found it -- which for a 117 mod the profile governs
    # may be the download cache, a place ``loadout.read`` does not look by name.
    current = switches.FolderSwitches(
        game=truth.game,
        folder=truth.folder,
        on={name: True for name in names},
        folders={mod.installed.folder_name: mod.installed.folder for mod in strays},
        profile=truth.profile,
    )
    return written.merge(switches.write(current, {name: False for name in names}, ids=ids))
