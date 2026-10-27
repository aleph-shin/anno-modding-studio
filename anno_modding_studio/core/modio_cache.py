"""What the game's own mod.io client has installed, read off its state files.

Anno 1800 ships a mod.io client of its own: the in-game mod browser.  Anno 117
has none (see ``games.Game.in_game_browser``), and its folder here
stays empty.  What the client downloads lands under one root -- ``C:/Users/Public/mod.io`` unless
``%LOCALAPPDATA%/mod.io/globalsettings.json`` says otherwise -- one folder per
game, named by the game's mod.io id, and inside it ``mods/<mod id>/<mod
folder>/`` plus a ``metadata/state.json`` describing every mod it put there:
its mod.io id, its name, which file of it is installed and at what version.

Two things make that worth reading.  The game loads mods from that folder --
Anno 1800's loader log names it with an absolute path, where every other mod is
``from mods\\`` -- so a reading that leaves it out is not the game you play.
And for every mod in it the state file already answers the question an update
check would otherwise have to ask mod.io: *which mod is this, and which file of
it do I have?*

Each entry also lists the mod.io accounts that subscribed to it on this machine
(``SubscriptionCount``, a list of user ids despite its name).  The client keeps
a mod's folder while any of them is still subscribed, so unsubscribing one
account leaves a mod another account on the machine holds.  And the client's
own record of who is signed in -- ``%LOCALAPPDATA%/mod.io/<game>/<someone>/
user.json`` -- names the account the game plays as.  Only its profile is read:
the same file holds that account's sign-in, which is the game's and is never
touched.

Nothing here writes.  The folder belongs to the game's client, and a file put
there that its state file does not know about is a file it may delete.

``ANNO_STUDIO_MODIO`` names a root to use instead of the client's.  That is
for the test suite, which reads mods folders of its own making and must not
find the nineteen real mods sitting in this machine's cache mixed in with them
-- the same protection the Studio's home and cache have, for the same reason.
``ANNO_STUDIO_MODIO_USERS`` does the same for the client's user records.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Set, Tuple

from . import games, places

# Where mod.io's client keeps everything when nobody has moved it.
DEFAULT_ROOT = Path("C:/Users/Public/mod.io")
ENV_ROOT = "ANNO_STUDIO_MODIO"
ENV_USERS = "ANNO_STUDIO_MODIO_USERS"
SETTINGS_FILE = "globalsettings.json"
STATE_FILE = Path("metadata") / "state.json"


@dataclass(frozen=True)
class CachedMod:
    """One mod the game's own client installed, as its state file records it."""

    mod_id: int
    name: str
    path: Path  # the ``mods/<mod id>`` folder; the mod folder sits inside it
    modfile_id: int = 0
    version: str = ""
    date_updated: int = 0
    profile_url: str = ""
    # The mod.io accounts that subscribed to it on this machine, by user id.
    holders: Tuple[int, ...] = ()


def root(local_app_data: Optional[str] = None) -> Path:
    """Where the mod.io client puts its files, as its own settings say.

    ``local_app_data`` is ``%LOCALAPPDATA%`` unless a test says otherwise.  A
    settings file that is missing, unreadable or does not name a folder means
    the default, which is what the client itself falls back to.
    """
    named = os.environ.get(ENV_ROOT, "").strip()
    if named:
        return Path(named)
    if local_app_data is None:
        local_app_data = str(places.local_app_data())
    if local_app_data:
        settings = Path(local_app_data) / "mod.io" / SETTINGS_FILE
        try:
            raw = json.loads(settings.read_text(encoding="utf-8-sig"))
            named = str(raw.get("RootLocalStoragePath") or "").strip()
        except (OSError, ValueError, AttributeError):
            named = ""
        if named:
            return Path(named)
    return DEFAULT_ROOT


def game_dir(game: str, at: Optional[Path] = None) -> Path:
    """The client's folder for one game, whether or not it exists."""
    return (at if at is not None else root()) / str(games.get(game).modio_game_id)


def mods_dir(game: str, at: Optional[Path] = None) -> Optional[Path]:
    """The folder the game loads mod.io-installed mods from, if there is one."""
    candidate = game_dir(game, at) / "mods"
    return candidate if candidate.is_dir() else None


def installed(game: str, at: Optional[Path] = None) -> List[CachedMod]:
    """Every mod the client's state file says it installed for this game.

    An empty or unreadable state file reads as nothing installed: the 117 one on
    the machine this was written against is zero bytes long, because that game's
    in-game browser does not exist yet.
    """
    state = game_dir(game, at) / STATE_FILE
    try:
        raw = json.loads(state.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return []
    found: List[CachedMod] = []
    for entry in (raw.get("Mods") if isinstance(raw, dict) else None) or []:
        if not isinstance(entry, dict):
            continue
        profile = entry.get("Profile") or {}
        modfile = profile.get("modfile") or {}
        try:
            mod_id = int(entry.get("ID") or profile.get("id") or 0)
        except (TypeError, ValueError):
            continue
        if not mod_id:
            continue
        on_disk = str(entry.get("PathOnDisk") or "").strip()
        found.append(
            CachedMod(
                mod_id=mod_id,
                name=str(profile.get("name") or "").strip(),
                path=Path(on_disk) if on_disk else game_dir(game, at) / "mods" / str(mod_id),
                modfile_id=_int(modfile.get("id")),
                version=str(modfile.get("version") or "").strip(),
                date_updated=_int(profile.get("date_updated")),
                profile_url=str(profile.get("profile_url") or "").strip(),
                holders=_holders(entry.get("SubscriptionCount")),
            )
        )
    return found


def _holders(value) -> Tuple[int, ...]:
    if not isinstance(value, list):
        return ()
    return tuple(sorted({_int(user) for user in value} - {0}))


@dataclass(frozen=True)
class Account:
    """A mod.io account, as the game's client remembers it."""

    id: int
    username: str = ""


def users_root(local_app_data: Optional[str] = None) -> Path:
    """Where the client keeps who is signed in, one folder per game."""
    named = os.environ.get(ENV_USERS, "").strip()
    if named:
        return Path(named)
    if local_app_data is None:
        local_app_data = str(places.local_app_data())
    return Path(local_app_data) / "mod.io"


def _user_record(game: str, at: Optional[Path] = None) -> Optional[dict]:
    """The newest ``user.json`` under the game's folder that names somebody,
    without its ``OAuth``: that is the game's sign-in, which is not the
    Studio's to read, let alone use, and is dropped before anything else
    sees the record."""
    folder = (at if at is not None else users_root()) / str(games.get(game).modio_game_id)
    try:
        records = sorted(folder.glob("*/user.json"), key=lambda path: path.stat().st_mtime, reverse=True)
    except OSError:
        return None
    for record in records:
        try:
            raw = json.loads(record.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError):
            continue
        if not isinstance(raw, dict):
            continue
        raw.pop("OAuth", None)
        profile = raw.get("Profile")
        if isinstance(profile, dict) and _int(profile.get("id")):
            return raw
    return None


def game_account(game: str, at: Optional[Path] = None) -> Optional[Account]:
    """The account the game's own client is signed in as, or None."""
    raw = _user_record(game, at)
    if raw is None:
        return None
    profile = raw["Profile"]
    return Account(_int(profile.get("id")), str(profile.get("username") or "").strip())


def game_subscriptions(game: str, at: Optional[Path] = None) -> Optional[Set[int]]:
    """The client's own saved list of what that account is subscribed to, or
    None when it has none to go by.

    Only as good as the client's last look: it fills the list when the game
    fetches the account's subscriptions -- opening the mod browser does --
    and writes it back empty after a start that did not.  On the machine
    this was written on it read 0, then 18, 17, and 0 again the same day,
    while the account had 17 throughout.  So an empty list says nothing,
    and is None here; a full one is what the client knew at its last look.
    """
    raw = _user_record(game, at)
    listed = raw.get("subscriptions") if raw is not None else None
    if not isinstance(listed, list):
        return None
    found = {_int(one) for one in listed} - {0}
    return found or None


def _int(value) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0
