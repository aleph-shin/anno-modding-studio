"""The game's mod browser's copies of mods the library has, and how to let go of them.

The last step of bringing everything into the library, and the only one that
touches the player's account.  The conversion *copies* out of the mod.io
cache, since those folders are the game client's; afterwards the cache's copy
is a dormant duplicate the game's browser keeps updating, and one the Studio
cannot switch.

**It has to be done in game.**  ``tools/unsubscribe_probe.py`` unsubscribed
the account from two mods through mod.io's API and started Anno 1800 after
each: the client fetched the account's subscriptions, saw the mod gone from
them, and still kept its copy held for the account, folder and all.  An
unsubscribe made anywhere but the game's own browser drops the subscription
on every machine and frees nothing on this one -- so the Studio does not do
it.  It says, per copy, what the game's own files say:

- **Which accounts hold it here.**  The client's state file lists every
  mod.io account that subscribed to a mod on this machine and keeps the
  folder while it lists any; an account that is not the one the game is
  signed in as can only let go of its copies signed in as itself.
- **Whether the game's account is still subscribed**, by the client's own
  saved list of its subscriptions -- no sign-in needed, and only when the
  client has filled it (see :func:`core.modio_cache.game_subscriptions`).
  A copy held for the account and missing from a filled list was
  unsubscribed from elsewhere, and stays: subscribing again and
  unsubscribing in game is the way out.

An account that held copies and no longer exists -- merged into another, as
happens when linking a platform account makes a second mod.io account, or
closed -- can never sign in to let go of them.  The client keeps those while
its folder stands.  **Starting that folder afresh** clears them: set aside
whole, the probe saw the client rebuild it from the signed-in account's
subscriptions alone -- every copy held by that account only -- and leave out
the ones only the dead account had held.  :func:`afresh` says whether that
can be done now and what it would do; :func:`start_afresh` does it.  Only
while the game loads nothing from the folder -- which the conversion makes
true, since the library's copies are what loads -- and never while the game
runs.  The folder goes into the Studio's backups, kept for good, never
deleted; the game's sign-in is not in it (the client keeps that under the
local app data, see :func:`core.modio_cache.users_root`), so the game stays
signed in.

Unsubscribing in game does free the account's hold: the probe's third run
saw the client drop the account from a mod's holders at once.  That mod was
held by a second account too, and its folder stayed for that one.

None of this is Anno 117's: it has no in-game browser, a subscription puts
nothing on disk there, and :func:`left_in_cache` is empty for it.

Only copies of mods the library holds are offered at all, so letting go of
one loses nothing on this machine: the game loads the library's.  A mod only
the cache has is the checklist's first row -- copy it in first.  And what is
let go of is a subscription, whose folder can hold several mods: none of them
is offered until the library holds them all, since unsubscribing takes the
folder whole.  The row is computed from disk and goes when the cache's
folders do.
"""

from __future__ import annotations

import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, List, Optional, Set

from . import convert, games, modio_cache, records
from .i18n import tr, trn

# Under the Studio's backups: one folder per fresh start, holding the game's
# mod browser's folder as it was and a note of what it held.
AFRESH = "modio-resets"
AFRESH_NOTE = "afresh.json"
# The format of that note, which nothing reads but a person.
AFRESH_FORMAT = 1

# Where one cache copy stands, by the game's own files.
LISTED = "listed"      # the game's account is subscribed: unsubscribe in game
DROPPED = "dropped"    # held for the game's account, which is no longer subscribed
OTHERS = "others"      # held only for other accounts that signed in here
UNKNOWN = "unknown"    # the client names nobody, or has no list to go by


@dataclass
class Left:
    """One copy in the game's mod browser's folder of a mod the library holds."""

    modio_id: int
    name: str
    key: str
    folder: Path
    version: str
    library_version: str
    loads: bool
    holders: tuple = ()
    account: int = 0
    standing: str = UNKNOWN

    @property
    def others(self) -> List[int]:
        """The accounts besides the game's that hold it on this machine."""
        return [holder for holder in self.holders if holder != self.account]

    @property
    def shared(self) -> bool:
        """Held for the game's account and for another as well -- whatever
        the client's saved list says, which it often writes back empty."""
        return bool(self.account) and self.account in self.holders and bool(self.others)

    def describe(self) -> str:
        """Where it stands, in a few words, for the list's last column."""
        if self.standing in (LISTED, UNKNOWN) and self.shared:
            return tr("unsubscribe in game, and another account holds it too")
        if self.standing in (LISTED, UNKNOWN):
            return tr("unsubscribe in game")
        if self.standing == DROPPED and self.shared:
            return tr("unsubscribed elsewhere, kept, and another account holds it too")
        if self.standing == DROPPED:
            return tr("unsubscribed elsewhere, kept")
        return trn(
            "kept for another account (id %(ids)s)", "kept for other accounts (ids %(ids)s)",
            len(self.others), ids=", ".join(str(i) for i in self.others),
        )

    def explain(self) -> str:
        """The same at length, for its tooltip."""
        if self.standing == LISTED:
            said = tr("The account the game is signed in as is subscribed to this. In the "
                      "game's mod browser, unsubscribe from it.")
        elif self.standing == DROPPED:
            said = tr("The game's account is no longer subscribed to this, "
                      "because it was unsubscribed from outside the game. "
                      "The game's client still keeps its copy: it did not "
                      "let go of a copy unsubscribed from elsewhere when "
                      "that was tried. Subscribe to it again in the game's "
                      "mod browser, then unsubscribe there.")
        elif self.standing == OTHERS:
            said = tr("The game's client keeps this for another mod.io "
                      "account that has signed in to the game on this "
                      "machine. Only that account, signed in to the game, "
                      "can let go of it. If that account no longer exists, "
                      "because it was merged into another or closed, it "
                      "never will. Starting the mod browser's folder afresh "
                      "clears it.")
        else:
            said = tr("In the game's mod browser, unsubscribe from it. If it is not among "
                      "your subscriptions there, it was unsubscribed from elsewhere and the "
                      "game still keeps it: subscribe to it again there, then unsubscribe.")
        if self.shared:
            said = " ".join([said, tr(
                "Another mod.io account on this machine holds it too, and the game "
                "keeps its copy while that account does. If that account was merged "
                "into another or closed, starting the mod browser's folder afresh "
                "clears its hold."
            )])
        return said


def left_in_cache(found, account: Optional[int] = None, listed: Optional[Set[int]] = None,
                  installed: Optional[Iterable] = None) -> List[Left]:
    """Every cache copy of a mod the library holds, from ``found`` (a
    :class:`core.truth.Truth` read from disk), with where each stands.

    ``account`` is the game's mod.io account, ``listed`` the client's saved
    list of its subscriptions, ``installed`` the client's state file read;
    each defaults to what the game's own files say.  Nothing for a game with
    no in-game browser: nothing there is the browser's to let go of.
    """
    if not games.get(found.game).in_game_browser:
        return []
    if account is None:
        known = modio_cache.game_account(found.game)
        account = known.id if known is not None else 0
    if listed is None:
        listed = modio_cache.game_subscriptions(found.game)
    holders = {
        entry.mod_id: entry.holders
        for entry in (installed if installed is not None else modio_cache.installed(found.game))
    }
    rows = convert.survey(found).rows
    # A subscription's folder goes whole when it is unsubscribed from, so one
    # that still holds a mod the library does not have is not ready, whichever
    # of its mods the library does.
    waiting = {
        copy.installed.modio_id
        for row in rows if not any(held.where == convert.LIBRARY for held in row.copies)
        for copy in row.copies if copy.where == convert.CACHE
    }
    left: List[Left] = []
    for row in rows:
        held = next((copy for copy in row.copies if copy.where == convert.LIBRARY), None)
        if held is None:
            continue
        for copy in row.copies:
            modio_id = copy.installed.modio_id
            if copy.where != convert.CACHE or not modio_id or modio_id in waiting:
                continue
            one = Left(
                modio_id=modio_id,
                name=copy.installed.name or row.name,
                key=row.key,
                folder=Path(copy.installed.folder),
                version=copy.installed.version,
                library_version=held.installed.version,
                loads=copy.loads,
                holders=tuple(holders.get(modio_id, ())),
                account=account,
            )
            one.standing = _stand(one, listed)
            left.append(one)
    left.sort(key=lambda one: one.name.lower())
    return left


def _stand(one: Left, listed: Optional[Set[int]]) -> str:
    if one.account and one.holders and one.account not in one.holders:
        return OTHERS
    if not one.account or not listed:
        return UNKNOWN
    return LISTED if one.modio_id in listed else DROPPED


# -- starting the mod browser's folder afresh ----------------------------------
@dataclass
class Afresh:
    """Whether the game's mod browser's folder can be started afresh now, and
    what that would do -- see the module's notes."""

    game: str
    folder: Path
    # Mods the game loads from the folder: while there are any, not now --
    # set aside, they would be out of the game until downloaded again, or
    # for good.
    loaded: List[str] = field(default_factory=list)
    running: bool = False
    # The game's mod.io account, 0 when its files do not say: then which
    # copies are its own cannot be told, so not now, and both lists below
    # stay empty.
    account: int = 0
    # What its state file lists: held for the game's account, which the
    # client downloads again from the account's subscriptions; and held only
    # for other accounts, which it does not.
    come_back: List[str] = field(default_factory=list)
    stay_out: List[str] = field(default_factory=list)

    @property
    def can(self) -> bool:
        return self.folder.is_dir() and bool(self.account) and not self.loaded and not self.running

    def why_not(self) -> str:
        """Why not now, in a sentence; "" when it can be done."""
        if not self.folder.is_dir():
            return tr("The game's mod browser has no folder here to start afresh.")
        if self.running:
            return tr("%s is running. Quit it first.", games.get(self.game).label)
        if not self.account:
            return tr("The account the game is signed in as is not known here, so "
                      "which copies would come back cannot be said.")
        if self.loaded:
            names = ", ".join(self.loaded[:4])
            if len(self.loaded) > 4:
                names = tr("%s and more", names)
            return trn(
                "The game loads %(n)s mod from the mod browser's folder (%(names)s): set aside, "
                "it would be out of the game until downloaded again. Bring it into the "
                "library first, on the fourth card.",
                "The game loads %(n)s mods from the mod browser's folder (%(names)s): set aside, "
                "they would be out of the game until downloaded again. Bring them into the "
                "library first, on the fourth card.",
                len(self.loaded), names=names,
            )
        return ""


def afresh(found, running: bool = False, account: Optional[int] = None,
           installed: Optional[Iterable] = None) -> Optional[Afresh]:
    """Whether the game's mod browser's folder can be started afresh, from
    ``found`` (a :class:`core.truth.Truth` read from disk); None for a game
    with no in-game browser.  ``running`` is whether the game is running;
    ``account`` and ``installed`` default to what the game's own files say."""
    game = found.game
    if not games.get(game).in_game_browser:
        return None
    if account is None:
        known = modio_cache.game_account(game)
        account = known.id if known is not None else 0
    entries = list(installed if installed is not None else modio_cache.installed(game))
    loaded = sorted({mod.name for mod in found.loading if mod.installed.in_cache}, key=str.lower)
    come_back, stay_out = [], []
    for entry in entries if account else ():
        name = entry.name or str(entry.mod_id)
        if entry.holders and account and account not in entry.holders:
            stay_out.append(name)
        else:
            come_back.append(name)
    return Afresh(
        game=game, folder=modio_cache.game_dir(game), loaded=loaded, running=running,
        account=account, come_back=sorted(come_back, key=str.lower), stay_out=sorted(stay_out, key=str.lower),
    )


def start_afresh(plan: Afresh, backups: Path) -> Path:
    """Set the game's mod browser's folder aside into ``backups``, with a note
    of what it held, so the client starts it afresh from the signed-in
    account's subscriptions.  Where it went.  Raises ValueError when it cannot
    be done now, and OSError when the move fails -- which leaves the folder
    where it was."""
    why = plan.why_not()
    if why:
        raise ValueError(why)
    home = Path(backups) / AFRESH / time.strftime("%Y%m%d-%H%M%S")
    home.mkdir(parents=True, exist_ok=True)
    aside = home / plan.folder.name
    shutil.move(str(plan.folder), str(aside))
    records.write(home / AFRESH_NOTE, {
        "at": time.time(), "game": plan.game, "from": str(plan.folder), "aside": str(aside),
        "come_back": plan.come_back, "stay_out": plan.stay_out,
    }, AFRESH_FORMAT, keyed=False)
    return aside
