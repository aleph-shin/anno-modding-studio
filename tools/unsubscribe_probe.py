"""What does the game's mod.io client do when the account unsubscribes elsewhere?

The Studio's last conversion step unsubscribes the player's account from the
game's mod browser's copies of mods the library now has, through mod.io's API
-- outside the game.  Whether that frees the cache's folder is the game's
client's business, and nobody has watched it happen.  Three questions:

1. Does the client remove its copy -- the folder, and its state file's entry
   -- the next time the game starts, signed in as that account?
2. The state file lists every account that subscribed to a mod on this
   machine (``SubscriptionCount``).  When another account also holds the mod,
   does the client keep the folder for it?
3. Does the client put the subscription back, because its own state still
   says the account has it?

This unsubscribes the Studio's signed-in account from **one** mod, chosen by
you, writes down what the cache looked like first, and afterwards says what
the client did.  Choose a mod the game does not load from the cache -- one a
folder of your own or the library shadows -- and nothing changes in game;
the status listing marks those.  ``--resubscribe`` puts the subscription back.

    python tools/unsubscribe_probe.py                      # read-only: accounts, cache, candidates
    python tools/unsubscribe_probe.py --unsubscribe 123456 # then start Anno 1800 to the main
                                                           # menu, open the mod browser once, quit
    python tools/unsubscribe_probe.py --in-game 123456     # or: writes down the before, and you
                                                           # unsubscribe in the game's browser
    python tools/unsubscribe_probe.py --check              # what the client did
    python tools/unsubscribe_probe.py --resubscribe [ID]   # put the subscription back

    python tools/unsubscribe_probe.py --reset              # set the game's whole mod.io folder
                                                           # aside; start the game, open the mod
                                                           # browser, let it download, quit
    python tools/unsubscribe_probe.py --check              # what the client rebuilt
    python tools/unsubscribe_probe.py --restore            # put the set-aside folder back

``--reset`` is for copies held by an account that no longer exists -- merged
into another or closed -- which can never sign in to let go of them.  The
client keeps a folder while any account on its list holds it, so those stay
for good; setting its folder aside whole (moved into the Studio's backups,
never deleted) should make it start afresh from the signed-in account's
subscriptions alone.  It refuses while the game loads any mod from the cache,
since those would be out of the game until downloaded again -- or for good,
for a mod the account is not subscribed to: run the conversion first, so the
library's copies are what the game loads.

What it found, 26 Sep, Anno 1800: through the API, the client fetched the
account's subscriptions, saw the mod gone -- it was on the client's own saved
list before and not after -- and still kept the copy held for the account,
folder and all.  It does not act on an unsubscribe made outside the game.
Then ``--in-game``: unsubscribed in the game's own browser, the client dropped
the account from the mod's holders at once, and kept the folder for the
second account that held it.  Not yet seen: a copy only one account held
going when that account unsubscribes in game.  And the client's saved list
of the account's subscriptions is only filled after a start that fetched
them; it is written back empty otherwise.

Then ``--reset``, the same evening, after the conversion had put library
copies in front of all 21 of the cache's: with its folder set aside, the
client rebuilt it from the signed-in account's subscriptions alone -- 17
mods, every one held by that account only -- and left out the 4 it had kept
only for the merged and closed account.  A reset clears a dead account's
holds; nothing else does.

Run it twice to answer question 2: once with a mod only your account holds,
once with one another account on this machine holds too.  The Studio's own
sign-in is used; the game's sign-in, which sits in the same folder as the
account's profile, is never read.  Anno 1800 only: 117's in-game browser has
installed nothing yet.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from anno_modding_studio.core import (
    discovery,
    games,
    launch,
    loaderlog,
    modio,
    modio_cache,
    places,
    secrets,
    truth,
)
from anno_modding_studio.core.changes import Loadout
from anno_modding_studio.core.games import ANNO_1800

GAME = ANNO_1800
RECORD = places.studio_home() / "unsubscribe-probe.json"
RESET = places.studio_home() / "modio-reset-probe.json"


def mods_folder(chosen: str) -> Path:
    if chosen:
        return Path(chosen)
    for install in discovery.find_game_installs():
        if "1800" in str(install.path):
            candidate = Path(install.path) / "mods"
            if candidate.is_dir():
                return candidate
    raise SystemExit("no Anno 1800 mods folder found; pass --mods")


def client() -> modio.Client:
    credentials = secrets.Store().load()
    if not (credentials.api_key and credentials.token):
        raise SystemExit("the Studio is not signed in to mod.io: Settings -> Key and sign-in…")
    return modio.Client(credentials.api_key, token=credentials.token, game_id=games.get(GAME).modio_game_id)


def shadowed(mods: Path) -> dict:
    """mod.io id -> whether the game loads another copy of that mod than the
    cache's, as the Studio's own reading of the folders decides it."""
    found = truth.read(Loadout(game=GAME), str(mods))
    loaded = {mod.key for mod in found.mods if mod.loads}
    return {
        mod.installed.modio_id: mod.key in loaded and not mod.loads
        for mod in found.mods if mod.installed.in_cache and mod.installed.modio_id
    }


def entry(modio_id: int):
    return next((one for one in modio_cache.installed(GAME) if one.mod_id == modio_id), None)


def log_stamp() -> float:
    log = loaderlog.log_file(GAME)
    try:
        return log.stat().st_mtime if log is not None else 0.0
    except OSError:
        return 0.0


def who(holder: int, me: int, game_user: int) -> str:
    marks = []
    if holder == me:
        marks.append("the Studio's")
    if holder == game_user:
        marks.append("the game's")
    return "%d (%s)" % (holder, " and ".join(marks)) if marks else "%d (another account)" % holder


def status(mods: Path) -> None:
    game_user = modio_cache.game_account(GAME)
    talk = client()
    me = talk.me()
    subscribed = talk.subscriptions(games.get(GAME).modio_game_id)
    print("the game is signed in as:   %s" % (
        "%d (%s)" % (game_user.id, game_user.username) if game_user else "nobody it remembers"))
    print("the Studio is signed in as: %d (%s)%s" % (
        me.id, me.username, "" if not game_user or game_user.id == me.id else "  -- a DIFFERENT account"))
    print("subscribed, as the Studio's account: %d mods" % len(subscribed))
    print()
    shadows = shadowed(mods)
    rows = sorted(modio_cache.installed(GAME), key=lambda one: one.name.lower())
    for one in rows:
        held = ", ".join(who(h, me.id, game_user.id if game_user else 0) for h in one.holders) or "nobody"
        print("%9d  %-40s" % (one.mod_id, one.name[:40]))
        print("           held by %s; %s; %s%s" % (
            held,
            "subscribed as you" if one.mod_id in subscribed else "NOT subscribed as you",
            "shadowed (the game loads another copy)" if shadows.get(one.mod_id) else "the game LOADS this copy",
            "" if one.path.exists() else "; its folder is missing",
        ))
    alone = [one for one in rows if one.mod_id in subscribed and shadows.get(one.mod_id) and one.holders == (me.id,)]
    shared = [one for one in rows if one.mod_id in subscribed and shadows.get(one.mod_id) and len(one.holders) > 1]
    known = modio_cache.game_subscriptions(GAME)
    print()
    print("the game's client's own saved list of your subscriptions: %s" % (
        "none kept" if known is None else "%d mods" % len(known)))
    if known is not None:
        alone = [one for one in alone if one.mod_id in known]
        shared = [one for one in shared if one.mod_id in known]
        print("(candidates below are only mods on that list, so the client can tell they went)")
    print()
    print("Good first probe (only your account holds it, the game loads another copy):")
    print("  " + (", ".join("%d %s" % (one.mod_id, one.name) for one in alone) or "none"))
    print("For question 2 (another account on this machine holds it too):")
    print("  " + (", ".join("%d %s" % (one.mod_id, one.name) for one in shared[:5]) or "none"))


def unsubscribe(mods: Path, modio_id: int, even_if_loaded: bool, in_game: bool = False) -> None:
    if launch.running(GAME) and not in_game:
        raise SystemExit("Anno 1800 is running; quit it first, so its client cannot race this.")
    if RECORD.is_file():
        raise SystemExit("a probe is already under way (%s): --check it, or --resubscribe to end it." % RECORD)
    before = entry(modio_id)
    if before is None:
        raise SystemExit("mod %d is not in the game's mod browser's folder" % modio_id)
    if not shadowed(mods).get(modio_id) and not even_if_loaded:
        raise SystemExit(
            "the game loads %s from the cache; unsubscribing would take it out of the game. "
            "Pick a shadowed one, or pass --even-if-loaded." % before.name)
    talk = client()
    me = talk.me()
    game_id = games.get(GAME).modio_game_id
    if modio_id not in talk.subscriptions(game_id):
        raise SystemExit("the Studio's account (%d) is not subscribed to %s" % (me.id, before.name))
    known = modio_cache.game_subscriptions(GAME)
    if not in_game:
        talk.unsubscribe(game_id, modio_id)
    after = talk.subscriptions(game_id)
    record = {
        "mod": modio_id, "name": before.name, "folder": str(before.path),
        "folder_existed": before.path.exists(), "holders": list(before.holders),
        "account": me.id, "at": time.time(), "log": log_stamp(),
        "client_listed": None if known is None else modio_id in known,
        "client_list": None if known is None else len(known),
        "how": "in game" if in_game else "api",
    }
    RECORD.parent.mkdir(parents=True, exist_ok=True)
    RECORD.write_text(json.dumps(record, indent=2), encoding="utf-8")
    if in_game:
        print("written down %s (%d) as it is now; nothing asked of mod.io." % (before.name, modio_id))
    else:
        print("unsubscribed account %d from %s (%d)%s" % (
            me.id, before.name, modio_id, "" if modio_id not in after else " -- but mod.io still lists it!"))
    print("held here by: %s" % ", ".join(str(h) for h in before.holders))
    print("on the client's own saved list of your subscriptions: %s" % (
        "it keeps none" if known is None else "yes" if modio_id in known else "NO"))
    print("written down: %s" % RECORD)
    print()
    if in_game:
        print("Now start Anno 1800, open the mod browser, find it under your subscriptions,")
        print("unsubscribe, quit, start the game once more to the main menu, quit, and run")
        print("with --check.")
    else:
        print("Now start Anno 1800, wait for the main menu, open the mod browser once,")
        print("quit, and run with --check.")


def check() -> None:
    if not RECORD.is_file():
        raise SystemExit("no probe under way; start one with --unsubscribe")
    record = json.loads(RECORD.read_text(encoding="utf-8"))
    modio_id, name = int(record["mod"]), record["name"]
    folder = Path(record["folder"])
    now = entry(modio_id)
    started = log_stamp() > float(record["log"]) + 1
    subscribed = modio_id in client().subscriptions(games.get(GAME).modio_game_id)
    others = [h for h in record["holders"] if h != record["account"]]
    in_game = record.get("how") == "in game"
    print("%s (%d), %s %s as %d" % (
        name, modio_id, "written down, to unsubscribe in game," if in_game else "unsubscribed",
        time.strftime("%d %b %H:%M", time.localtime(record["at"])), record["account"]))
    print("  held before by:  %s" % ", ".join(str(h) for h in record["holders"]))
    print("  game started since: %s" % ("yes" if started else "NO"))
    print("  folder now:      %s" % ("there" if folder.exists() else "gone"))
    print("  state entry now: %s" % (
        "gone" if now is None else "there, held by %s" % (", ".join(str(h) for h in now.holders) or "nobody")))
    print("  mod.io says the account is %s" % ("SUBSCRIBED again" if subscribed else "still unsubscribed"))
    known = modio_cache.game_subscriptions(GAME)
    listed_before = record.get("client_listed")
    listed_now = None if known is None else modio_id in known
    print("  on the client's saved list before: %s; now: %s" % (
        {None: "not recorded", True: "yes", False: "no"}[listed_before],
        {None: "it keeps none", True: "yes", False: "no (it lists %d)" % len(known or ())}[listed_now]))
    print()
    if not started:
        print("RESULT: the game has not started since; start it, then check again.")
    elif in_game and subscribed and listed_now:
        print("RESULT: mod.io still has the account subscribed -- was it unsubscribed in the")
        print("        game's browser? Do that, quit, and check again.")
    elif in_game and not folder.exists() and now is None:
        print("RESULT: unsubscribing IN GAME removes the copy%s." % (
            ", even though another account here held it" if others else ""))
        print("        The Studio's list is the right way round: unsubscribe in game.")
    elif in_game and now is not None and record["account"] not in now.holders and now.holders:
        print("RESULT: unsubscribing in game dropped this account, and the client KEEPS the")
        print("        folder for the other account(s) that hold it here.")
    elif in_game and now is not None and record["account"] in now.holders:
        print("RESULT: even unsubscribed in game, the client still holds its copy for you.")
        print("        Start the game once more and check again; if it still does, the")
        print("        client lets go of nothing short of deleting the mod in its browser.")
    elif subscribed:
        print("RESULT: the client put the subscription back from its own state. An unsubscribe")
        print("        made outside the game does not stick; the Studio must say to do it in game.")
    elif not folder.exists() and now is None:
        print("RESULT: the client REMOVES its copy after an unsubscribe made elsewhere%s." % (
            ", even though another account here held it" if others else ""))
        print("        The Studio can say the copy goes when the game next starts.")
    elif now is not None and record["account"] not in now.holders and now.holders:
        print("RESULT: the client dropped this account and KEEPS the folder for the other")
        print("        account(s) that hold it here. The Studio must say so for shared copies.")
    elif now is not None and record["account"] in now.holders and listed_now is None:
        # Written back empty after a start that fetched nothing: no list is
        # not a list without the mod, which the two verdicts below take it for.
        print("RESULT: the client keeps no list of your subscriptions now, so nothing says it")
        print("        has fetched them since. Open the mod browser in game, quit, and")
        print("        check again.")
    elif now is not None and record["account"] in now.holders and listed_now:
        print("RESULT: the client has not fetched your subscriptions since. Open the mod")
        print("        browser in game, quit, and check again.")
    elif now is not None and record["account"] in now.holders and listed_before:
        print("RESULT: the client fetched your subscriptions, knew this one, saw it go, and still")
        print("        holds its copy for you. It does NOT act on an unsubscribe made outside the")
        print("        game; the Studio must send people to the in-game browser to do it.")
    elif now is not None and record["account"] in now.holders:
        print("RESULT: the client fetched your subscriptions (its list no longer has this one)")
        print("        but still holds its copy for you -- and its list did not have it before,")
        print("        so it may only act on mods it saw go. Run again with a mod on its list:")
        print("        --done, then --unsubscribe with one the status listing offers.")
    else:
        print("RESULT: something in between -- read the lines above.")


def reset(mods: Path, even_if_loaded: bool) -> None:
    if launch.running(GAME):
        raise SystemExit("Anno 1800 is running; quit it first.")
    if RESET.is_file():
        raise SystemExit("a reset is already under way (%s): --check it, or --restore." % RESET)
    home = modio_cache.game_dir(GAME)
    if not home.is_dir():
        raise SystemExit("there is no mod.io folder for Anno 1800 at %s" % home)
    installed = modio_cache.installed(GAME)
    shadows = shadowed(mods)
    loaded = [one for one in installed if one.path.exists() and not shadows.get(one.mod_id)]
    if loaded and not even_if_loaded:
        raise SystemExit(
            "the game loads %d mod%s from the cache (%s); set aside, they would be out of the "
            "game until the client downloads them again, or for good if your account is not "
            "subscribed. Run the conversion first, or pass --even-if-loaded."
            % (len(loaded), "" if len(loaded) == 1 else "s", ", ".join(one.name for one in loaded[:6])))
    talk = client()
    me = talk.me()
    subscribed = talk.subscriptions(games.get(GAME).modio_game_id)
    aside = places.backups_dir() / ("modio-reset-%s" % time.strftime("%Y%m%d-%H%M%S")) / home.name
    aside.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "at": time.time(), "log": log_stamp(), "home": str(home), "aside": str(aside),
        "account": me.id, "subscribed": sorted(subscribed),
        "before": {str(one.mod_id): {"name": one.name, "holders": list(one.holders)} for one in installed},
    }
    shutil.move(str(home), str(aside))
    RESET.parent.mkdir(parents=True, exist_ok=True)
    RESET.write_text(json.dumps(record, indent=2), encoding="utf-8")
    others = [one for one in installed if any(h != me.id for h in one.holders)]
    alone = [one for one in installed if one.holders and me.id not in one.holders]
    print("set aside: %s" % home)
    print("       to: %s" % aside)
    print("%d copies were in it; %d held by another account, %d of them by it alone." % (
        len(installed), len(others), len(alone)))
    print("your account (%d) is subscribed to %d, %d of them among those copies." % (
        me.id, len(subscribed), len(subscribed & {one.mod_id for one in installed})))
    print()
    print("Now start Anno 1800, open the mod browser, let it download whatever it wants to,")
    print("quit, and run with --check. --restore puts the old folder back.")


def check_reset() -> None:
    record = json.loads(RESET.read_text(encoding="utf-8"))
    home = Path(record["home"])
    account = int(record["account"])
    before = {int(key): value for key, value in record["before"].items()}
    subscribed = set(record["subscribed"])
    started = log_stamp() > float(record["log"]) + 1
    now = {one.mod_id: one for one in modio_cache.installed(GAME)}
    print("reset %s; the old folder is at %s" % (
        time.strftime("%d %b %H:%M", time.localtime(record["at"])), record["aside"]))
    print("  game started since: %s" % ("yes" if started else "NO"))
    print("  mod.io folder now:  %s, %d mods in its state file" % (
        "there" if home.is_dir() else "not made yet", len(now)))
    for modio_id, was in sorted(before.items(), key=lambda pair: pair[1]["name"].lower()):
        one = now.get(modio_id)
        print("  %9d  %-40s held before by %-20s now: %s" % (
            modio_id, was["name"][:40], ",".join(str(h) for h in was["holders"]) or "-",
            "not back" if one is None else "back, held by %s%s" % (
                ",".join(str(h) for h in one.holders) or "nobody",
                "" if one.path.exists() else " (no folder)")))
    for modio_id in sorted(set(now) - set(before)):
        print("  %9d  %-40s new" % (modio_id, now[modio_id].name[:40]))
    ghosts = sorted({h for one in now.values() for h in one.holders if h != account})
    wanted = subscribed & set(before)
    missing = sorted(wanted - set(now))
    print()
    if not started:
        print("RESULT: the game has not started since; start it, open the mod browser, then check.")
    elif not now:
        print("RESULT: the client has not rebuilt its folder yet. Open the mod browser in game,")
        print("        let it download, quit, and check again.")
    elif ghosts:
        print("RESULT: the client brought other accounts' holds back (%s): it keeps them" % (
            ", ".join(str(h) for h in ghosts)))
        print("        somewhere besides this folder, and a reset does not clear them.")
    else:
        gone = [was["name"] for modio_id, was in before.items() if modio_id not in now and modio_id not in subscribed]
        print("RESULT: a reset CLEARS the other accounts' holds: the client rebuilt its folder")
        print("        with %d mods, every one held by your account alone, and left out %d it" % (
            len(now), len(gone)))
        print("        had kept only for accounts that are not yours.")
        if missing:
            print("        %d of your subscriptions are not back yet: open the mod browser again" % len(missing))
            print("        and let it finish.")


def restore() -> None:
    if launch.running(GAME):
        raise SystemExit("Anno 1800 is running; quit it first.")
    if not RESET.is_file():
        raise SystemExit("no reset under way")
    record = json.loads(RESET.read_text(encoding="utf-8"))
    home, aside = Path(record["home"]), Path(record["aside"])
    if not aside.is_dir():
        raise SystemExit("the set-aside folder is not at %s any more" % aside)
    if home.exists():
        after = aside.parent / (home.name + "-after-reset")
        shutil.move(str(home), str(after))
        print("what the client rebuilt is at %s" % after)
    shutil.move(str(aside), str(home))
    RESET.unlink()
    print("put back: %s" % home)


def resubscribe(modio_id: int = 0) -> None:
    record = json.loads(RECORD.read_text(encoding="utf-8")) if RECORD.is_file() else {}
    modio_id = modio_id or int(record.get("mod") or 0)
    if not modio_id:
        raise SystemExit("no probe under way; name the mod: --resubscribe 123456")
    client().subscribe(games.get(GAME).modio_game_id, modio_id)
    if int(record.get("mod") or 0) == modio_id:
        RECORD.unlink()
    found = entry(modio_id)
    print("subscribed again to %s; the game's mod browser fetches it when it next starts." % (
        found.name if found else modio_id))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--mods", default="", help="Anno 1800's mods folder, if it is not found")
    parser.add_argument("--unsubscribe", type=int, default=0, metavar="MODIO_ID")
    parser.add_argument("--even-if-loaded", action="store_true")
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--in-game", type=int, default=0, metavar="MODIO_ID")
    parser.add_argument("--resubscribe", type=int, nargs="?", const=-1, default=0, metavar="MODIO_ID")
    parser.add_argument("--reset", action="store_true")
    parser.add_argument("--restore", action="store_true")
    parser.add_argument("--done", action="store_true",
                        help="forget the record, keeping the unsubscribe or the reset")
    args = parser.parse_args()
    if args.check and RESET.is_file():
        check_reset()
    elif args.check:
        check()
    elif args.reset:
        reset(mods_folder(args.mods), args.even_if_loaded)
    elif args.restore:
        restore()
    elif args.resubscribe:
        resubscribe(max(0, args.resubscribe))
    elif args.done and RESET.is_file():
        aside = json.loads(RESET.read_text(encoding="utf-8"))["aside"]
        RESET.unlink()
        print("forgotten; the rebuilt folder stays. The old one is still at %s -- delete it" % aside)
        print("when you are sure.")
    elif args.done:
        RECORD.unlink(missing_ok=True)
        print("forgotten; the mod stays unsubscribed.")
    elif args.unsubscribe:
        unsubscribe(mods_folder(args.mods), args.unsubscribe, args.even_if_loaded)
    elif args.in_game:
        unsubscribe(mods_folder(args.mods), args.in_game, args.even_if_loaded, in_game=True)
    else:
        status(mods_folder(args.mods))
    return 0


if __name__ == "__main__":
    sys.exit(main())
