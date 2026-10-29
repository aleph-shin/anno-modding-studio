"""Time what somebody using the Studio waits for, against a large mods folder.

    python tools/bench.py --census C:/census --work C:/bench
    python tools/bench.py ... --mods 150 --repeat 5 --out before.json
    python tools/bench.py ... --profile truth      cProfile one step, worst 40 by cumulative time
    python tools/bench.py ... --profile "an edit" --profile-nth 5      the Nth such step, not the first
    python tools/bench.py --compare before.json after.json

Stage 4 of docs/proposals/consolidation-proposal.md.  Every performance
candidate there was found by reading the code and none had been timed; this
times them, so a fix is judged by a number taken the same way before and
after.

**The folder.**  The first ``--mods`` Anno 1800 mods of the census (by mod.io
id), each unit's folders copied into one mods folder under ``--work``; built
once and reused, and rebuilt when ``--mods`` changes.  Every mod in it is
on, as the census has them.  A run works in a copy of it in the sandbox
(:func:`working_copy`), never in it: Play switches a mod off by renaming its
folder, and writes the mod of your own there.  Runs once shared the folder,
and the loadout read from it on 12 October 2026 had 416 of its 417 mods
switched off by runs before.

**What it does not touch.**  The real window runs, but in a sandbox: its own
settings file, library, Studio home, Documents (so no real ``activation.json``
or mod.io cache is read or written), and its own copy of the game files'
index.  The only real files it reads are the extracted game files the copy of
the index points at -- found the way a launch finds them -- and the game's
loader, when ``--loader`` asks for it.  Nothing it does reaches a real mods
folder.

**What a number is.**  Each step reports two times, in milliseconds:

* *call*: the step itself, start to finish, on the UI thread -- the time the
  window cannot answer;
* *after*: what the window then does on its own as the event loop runs --
  debounced refreshes, a deferred rebuild -- measured as time spent inside
  ``processEvents`` over a settling window, so the idle wait between events
  is not counted;
* *held*: the longest the window went without answering -- the call, or
  one turn of the event loop after it.  Work done a piece at a time is all
  of it in *after* and one piece of it in *held*.

Steps that repeat report the median of ``--repeat`` runs.  Background
threads are not counted unless the window waits on them.

**What else is running.**  An edit starts a reading of the mods it leaves in
doubt, on a thread, and on a long mod list that reading goes on for a minute
or two.  It is Python, so it shares the interpreter with the window: a step
taken meanwhile is slower, by how much depending on what the reading is
doing.  That is what somebody working sees, and it is what the steps time by
default.  ``--alone`` waits for the reading before each step, and times the
step with nothing else running: slower to run, and steadier.  Play waits for
that reading itself, in a dialog; the wait is let finish before Play is
timed, and is printed.

A profile (``--profile``) takes in every thread, the reading with the step,
on Python 3.12 and later: use ``--alone`` with it.
"""

from __future__ import annotations

import argparse
import cProfile
import faulthandler
import io
import json
import os
import pstats
import shutil
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
GAME = "anno1800"
CENSUS_GAME = "anno-1800"


# -- the folder --------------------------------------------------------------------
def saved_loadout(work: Path, count: int) -> Path:
    """The loadout the bench folder of ``count`` units makes, kept once read."""
    return work / ("bench-%d.annoload" % count)


def _as_fetched(unit: Path) -> list:
    """What says a unit's files are the ones copied: how many, how big, and
    the newest of them."""
    files = [path for path in (unit / "files").rglob("*") if path.is_file()]
    stats = [path.stat() for path in files]
    return [len(files), sum(stat.st_size for stat in stats),
            max((stat.st_mtime_ns for stat in stats), default=0)]


def build_folder(census: Path, work: Path, count: int) -> Path:
    """``work/mods-<count>``: the first ``count`` census mods in one folder.

    Built once and used again while the census units it was copied from are
    the ones there now, by their ids and by their files (:func:`_as_fetched`):
    a census fetched again is copied again.  What a run left changed in the
    folder itself is not looked for.  Built again, the loadout read from the
    old folder goes too, so the next run reads the new one.

    A unit whose zip held the mod's own contents at the top is one mod, under
    the unit's id; one holding a box of mods is those mods, as the census
    reads it (``modio_roundtrip.folders_of``).  A folder whose name an earlier unit took, compared as
    Windows compares names, is left out and said to be, here and in the stamp.
    """
    import modio_roundtrip

    target = work / ("mods-%d" % count)
    stamp = target / ".bench.json"
    units = modio_roundtrip.first_units(census / CENSUS_GAME, count)
    wanted = [p.name for p in units]
    fetched = {unit.name: _as_fetched(unit) for unit in units}
    if stamp.is_file():
        held = json.loads(stamp.read_text(encoding="utf-8"))
        # A stamp from before the files were noted says nothing of them.  One
        # from before the folders were noted is of a folder runs changed: a
        # folder switched off, or a mod Play wrote, is built again.
        if (
            held.get("units") == wanted and held.get("fetched", fetched) == fetched
            and held.get("names") == _folders_in(target)
        ):
            if "fetched" not in held:
                stamp.write_text(json.dumps({**held, "fetched": fetched}), encoding="utf-8")
            return target
    if target.exists():
        shutil.rmtree(target)
    saved_loadout(work, count).unlink(missing_ok=True)
    target.mkdir(parents=True)
    taken = set()
    copied = 0
    left_out = []
    for unit in units:
        files = unit / "files"
        # The mods the census reads the unit as, a box of them taken out of
        # it; a mod bundled inside another goes with that one.
        mods, _roots = modio_roundtrip.folders_of(files)
        tops = [
            (mod, "unit-%s" % unit.name if mod == files else mod.name) for mod in mods
            if not any(other in mod.parents for other in mods)
        ]
        for top, name in tops:
            if name.lower() in taken:
                left_out.append("%s/%s" % (unit.name, name))
                continue
            taken.add(name.lower())
            shutil.copytree(top, target / name)
            copied += 1
    if left_out:
        print("left out, a folder of that name being there from an earlier unit: %s"
              % ", ".join(left_out))
    stamp.write_text(json.dumps({"units": wanted, "folders": copied, "fetched": fetched,
                                 "left_out": left_out, "names": _folders_in(target)}),
                     encoding="utf-8")
    return target


def _folders_in(folder: Path) -> list:
    return sorted(path.name for path in folder.iterdir() if path.is_dir()) if folder.is_dir() else []


def working_copy(folder: Path, box: Path) -> Path:
    """``box/mods``: the bench folder copied for one run to change.  At the
    same place every run, as the loadout read from it names its folders."""
    copy = box / "mods"
    if copy.exists():
        shutil.rmtree(copy)
    start = time.perf_counter()
    shutil.copytree(folder, copy, ignore=shutil.ignore_patterns(".bench.json"))
    print("mods copied for this run: %.1f s" % (time.perf_counter() - start))
    return copy


def real_game_files():
    """The extracted Anno 1800 files a launch would restore, and their index.

    Read from the settings a launch reads, by the launch's own rule: worked
    out here as the file under ``APPDATA``, they were not the ones in use
    when everything is kept in one folder."""
    from anno_modding_studio.core import gamefiles
    from anno_modding_studio.core import index as index_mod
    from anno_modding_studio.ui.main_window import open_settings

    root = str(open_settings().value("last_read_root:%s" % GAME) or "")
    stamp = gamefiles.read_stamp(root) if root else None
    if stamp is None:
        root = str(gamefiles.default_extract_root(GAME))
        stamp = gamefiles.read_stamp(root)
    if stamp is None:
        sys.exit("No extracted Anno 1800 files to restore: load them in the Studio once first.")
    assets = Path(stamp["assets"])
    db = index_mod.cache_path_for(assets)
    if not db.is_file():
        sys.exit("The Anno 1800 files are extracted but not indexed: open them in the Studio once first.")
    return root, assets, db


def sandbox(work: Path, loader: bool) -> dict:
    """Point everything the Studio writes, or reads per user, inside ``work``.

    Must run before anything imports the Studio's settings."""
    root, assets, db = real_game_files()
    box = work / "sandbox"
    if box.exists():
        shutil.rmtree(box)
    for name in ("home", "cache", "library", "modio", "modio-users", "profile"):
        (box / name).mkdir(parents=True)
    # The same index, under the same name, in a cache of the bench's own: a
    # launch writes texts into the index it opens.
    shutil.copy2(db, box / "cache" / db.name)
    settings = box / "settings.ini"
    os.environ.update({
        "ANNO_STUDIO_SETTINGS": str(settings),
        "ANNO_STUDIO_HOME": str(box / "home"),
        "ANNO_STUDIO_CACHE": str(box / "cache"),
        "ANNO_STUDIO_LIBRARY": str(box / "library"),
        "ANNO_STUDIO_MODIO": str(box / "modio"),
        "ANNO_STUDIO_MODIO_USERS": str(box / "modio-users"),
        # Documents, for the game's own folder there: its activation.json
        # and logs are the real game's, and Play would write the former.
        "USERPROFILE": str(box / "profile"),
        "HOME": str(box / "profile"),
        # The scan of every other mod on this PC reads the real mods folders,
        # off the UI thread; it is not what is being timed.
        "ANNO_STUDIO_ELSEWHERE": "none",
    })
    if not loader:
        os.environ["ANNO_STUDIO_LOADER"] = "none"
    return {"settings": settings, "root": root, "assets": assets, "box": box}


# -- timing ------------------------------------------------------------------------
class Clock:
    def __init__(self, app, profile: str = "", sort: str = "cumulative", nth: int = 1):
        self.app = app
        self.results = {}
        # A step whose name holds this is profiled, once: the ``nth`` time
        # one runs, as a round can leave the next one's step slower.
        self.profile = profile
        self.nth = nth
        self.matched = 0
        self.sort = sort
        # Called before each step, where one is set: see --alone.
        self.before = None

    def settle(self, seconds: float, until=None) -> float:
        """Run the event loop for ``seconds``, and on until ``until()`` is
        so where one is given; the milliseconds spent handling events, not
        waiting for them.  The longest single stretch of that is kept in
        ``self.longest``, in seconds."""
        busy = 0.0
        self.longest = 0.0
        start_all = time.perf_counter()
        end = start_all + seconds
        while time.perf_counter() < end or (
            until is not None and not until() and time.perf_counter() - start_all < 120
        ):
            start = time.perf_counter()
            self.app.processEvents()
            spent = time.perf_counter() - start
            busy += spent
            self.longest = max(self.longest, spent)
            time.sleep(0.002)
        return busy * 1000

    def step(self, name: str, action, settle: float = 2.0, until=None) -> None:
        if self.before is not None:
            self.before()
        profiling = False
        if self.profile and self.profile in name:
            self.matched += 1
            profiling = self.matched == self.nth
        if profiling:
            profiler = cProfile.Profile()
            profiler.enable()
        start = time.perf_counter()
        action()
        call = (time.perf_counter() - start) * 1000
        after = self.settle(settle, until)
        # The longest the window went without answering: the call, or one
        # turn of the event loop after it.
        held = max(call, self.longest * 1000)
        if profiling:
            profiler.disable()
            out = io.StringIO()
            pstats.Stats(profiler, stream=out).strip_dirs().sort_stats(self.sort).print_stats(45)
            print(out.getvalue())
        self.results.setdefault(name, []).append(
            {"call": round(call, 1), "after": round(after, 1), "held": round(held, 1)}
        )
        print("  %-28s call %9.1f ms   after %8.1f ms   held %8.1f ms" % (name, call, after, held),
              flush=True)


def summary(results: dict) -> dict:
    return {
        name: {
            "call": statistics.median(r["call"] for r in runs),
            "after": statistics.median(r["after"] for r in runs),
            "held": statistics.median(r["held"] for r in runs),
            "runs": len(runs),
            "each": runs,
        }
        for name, runs in results.items()
    }


# -- the window --------------------------------------------------------------------
def quiet_dialogs():
    """Answer every message box as its default would be, and say it was asked."""
    from PySide6.QtWidgets import QMessageBox

    def told(kind):
        def answer(_parent, title, text="", *args, **kwargs):
            print("    [%s] %s: %s" % (kind, title, str(text).splitlines()[0][:100] if text else ""))
            return QMessageBox.StandardButton.Yes
        return answer

    for kind in ("information", "warning", "critical", "question"):
        setattr(QMessageBox, kind, staticmethod(told(kind)))

    # And any other dialog, which would otherwise wait for a click forever.
    from PySide6.QtWidgets import QApplication, QDialog

    def refused(dialog, *_args):
        from anno_modding_studio.ui.background import is_waiting

        if is_waiting(dialog):
            # The one dialog that is not a question: Play and Export wait in
            # it for what the window is still reading, and it shuts itself.
            # Refused, Play gave up at once and was timed doing nothing.
            start = time.perf_counter()
            accepted = int(QDialog.DialogCode.Accepted)
            app = QApplication.instance()
            while dialog.result() != accepted and time.perf_counter() - start < 600:
                app.processEvents()
                time.sleep(0.01)
            print("    waited for background work: %.1f s" % (time.perf_counter() - start))
            return dialog.result()
        print("    [dialog] %s: %s" % (type(dialog).__name__, dialog.windowTitle()))
        return 0

    QDialog.exec = refused
    QMessageBox.exec = refused

    # The static questions run their own event loop in Qt's C++, past the
    # exec above: offscreen, one waited for a click all night ("Who are
    # you?", at the first edit to a loadout's own mod).  Answered with
    # what they were given, as OK would.
    from PySide6.QtWidgets import QFileDialog, QInputDialog

    def asked(kind, answer):
        def ask(_parent, title, *args, **kwargs):
            print("    [%s] %s" % (kind, title))
            return answer(*args, **kwargs)
        return ask

    # The ones the Studio calls: (label, echo, text) and (label, items, current).
    QInputDialog.getText = staticmethod(asked(
        "getText", lambda *args, text="", **_k: (args[2] if len(args) > 2 else text, True)))
    QInputDialog.getItem = staticmethod(asked(
        "getItem", lambda _label="", items=(), current=0, *_a, **_k: (
            items[current] if items else "", bool(items))))
    # A file dialog is cancelled: nothing here picks a file.
    for name, nothing in (("getOpenFileName", ("", "")), ("getSaveFileName", ("", "")),
                          ("getOpenFileNames", ([], "")), ("getExistingDirectory", "")):
        setattr(QFileDialog, name, staticmethod(asked(name, lambda *_a, nothing=nothing, **_k: nothing)))


def let_background_finish(window, clock: Clock) -> None:
    """Run the event loop until the window has nothing running behind it.

    Before a step that waits for that itself, so the step is timed and the
    wait is not: Play after an edit stands behind the reading the edit
    started, which is the edit's and takes as long as it takes.
    """
    start = time.perf_counter()
    while window.background.busy and time.perf_counter() - start < 600:
        clock.app.processEvents()
        time.sleep(0.01)
    waited = time.perf_counter() - start
    if waited > 0.05:
        print("    background work finished after %.1f s" % waited)


def read_folder_in(window, folder: Path, clock: Clock) -> None:
    """Read the whole folder into the loadout, through the dialog a user
    would use, with every mod ticked as it comes."""
    from PySide6.QtWidgets import QDialog

    from anno_modding_studio.ui import mods_folder_dialog

    def run_and_accept(dialog):
        start = time.perf_counter()
        # The dialog reads when Read mods is pressed, and no longer as it
        # opens: left alone it was accepted with nothing read.
        dialog.reload()
        while dialog._worker is not None:
            clock.app.processEvents()
            time.sleep(0.01)
        print("    reading the folder (worker): %.1f s" % (time.perf_counter() - start))
        dialog._accept()
        return int(QDialog.DialogCode.Accepted)

    window.settings.setValue(window.mods_key("last_mods_folder"), str(folder))
    original = mods_folder_dialog.ModsFolderDialog.exec
    mods_folder_dialog.ModsFolderDialog.exec = run_and_accept
    try:
        clock.step("read the folder in", window.load_installed_mods, settle=5.0)
    finally:
        mods_folder_dialog.ModsFolderDialog.exec = original


def busiest_asset(window) -> str:
    counts = {}
    for change in window.loadout:
        if change.guid:
            counts[change.guid] = counts.get(change.guid, 0) + 1
    return max(counts, key=counts.get) if counts else ""


def longest_mod(window) -> str:
    """The loaded mod with the most changes: the longest file there is to
    draw again."""
    counts = {}
    for change in window.loadout:
        if change.origin:
            counts[change.origin] = counts.get(change.origin, 0) + 1
    return max(counts, key=counts.get) if counts else ""


def an_untouched_asset(window) -> str:
    """A production building no mod changes: a page of numbers to edit."""
    touched = {change.guid for change in window.loadout}
    rows = window.index._con.execute(
        "SELECT guid FROM assets WHERE template = 'FactoryBuilding7' ORDER BY guid").fetchall()
    for (guid,) in rows:
        if str(guid) not in touched:
            return str(guid)
    return ""


def an_editable_leaf(window):
    """A number field nobody has edited yet: a first edit, which is the usual one."""
    for item in window.editor._iter_items():
        text, original = item.text(1), item.data(0, 258)  # ORIG_ROLE
        if original is not None and text == original and text.strip().lstrip("-").isdigit():
            return item
    return None


def a_mod_row(window, origin: str = ""):
    """The first mod row with a switch that is on, or the row of ``origin``.
    Not the first with a switch: one that was off, switching it "off" did
    nothing and switching it back on left it on for the rounds after.

    A plain walk: a QTreeWidgetItemIterator registers with the tree's model,
    and one left alive across a rebuild is not safe to hold."""
    from PySide6.QtCore import Qt

    from anno_modding_studio.ui.changes_dock import KIND_ROLE, ORIGIN_ROLE

    tree = window.changes_panel.tree
    for n in range(tree.topLevelItemCount()):
        item = tree.topLevelItem(n)
        if item.data(0, KIND_ROLE) != "mod" or not item.flags() & Qt.ItemFlag.ItemIsUserCheckable:
            continue
        if item.data(0, ORIGIN_ROLE) == origin if origin else item.checkState(0) == Qt.CheckState.Checked:
            return item
    return None


def run(args) -> dict:
    work = Path(args.work)
    folder = build_folder(Path(args.census), work, args.mods)
    print("mods folder: %s (%s folders)" % (
        folder, json.loads((folder / ".bench.json").read_text(encoding="utf-8"))["folders"]))
    box = sandbox(work, args.loader)
    mods = working_copy(folder, box["box"])
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

    from PySide6.QtCore import QSettings, Qt
    from PySide6.QtWidgets import QApplication

    settings = QSettings(str(box["settings"]), QSettings.Format.IniFormat)
    settings.setValue("last_read_game", GAME)
    settings.setValue("last_read_root:%s" % GAME, box["root"])
    settings.sync()

    app = QApplication.instance() or QApplication([])
    quiet_dialogs()
    clock = Clock(app, args.profile, args.sort, args.profile_nth)

    from anno_modding_studio.ui.main_window import MainWindow
    from anno_modding_studio.ui.navigation import TAB_FILE, TAB_GENERATE, TAB_SOURCE

    holder = {}

    def start():
        holder["window"] = MainWindow()
        holder["window"].show()

    print("startup")
    clock.step("startup (window and index)", start, settle=3.0)
    window = holder["window"]
    if window.index is None:
        sys.exit("The game files did not come back at launch.")
    print("  index: %s assets" % window.index.asset_count)

    from anno_modding_studio.core.changes import AIM_MANAGE
    from anno_modding_studio.ui.changes_dock import ORIGIN_ROLE

    # The loadout the folder makes, kept once read: reading it is minutes,
    # and opening it again is a step worth timing in its own right.
    saved = saved_loadout(work, args.mods)
    if args.read or not saved.is_file():
        window.loadout.aim = AIM_MANAGE
        print("read")
        read_folder_in(window, mods, clock)
        window.loadout.save(saved)
    else:
        print("open")
        clock.step("open the loadout", lambda: window.load_loadout(saved), settle=3.0)
    print("  loadout: %d mods, %d changes" % (len(window.loadout.layers), sum(1 for _ in window.loadout)))

    if args.alone:
        clock.before = lambda: let_background_finish(window, clock)
    busy, plain = busiest_asset(window), an_untouched_asset(window)
    print("repeated steps (%d each)" % args.repeat)
    for n in range(args.repeat):
        clock.step("refresh what loads (disk)", lambda: window.refresh_truth(say=False))
        clock.step("open the busiest asset", lambda: window.jump_to_change(busy, ""))
        clock.step("open an untouched asset", lambda: window.jump_to_change(plain, ""))
        leaf = an_editable_leaf(window)
        if leaf is not None:
            value = str(int(leaf.text(1)) + 1)
            clock.step("an edit", lambda leaf=leaf, value=value: leaf.setText(1, value))
        box_ = window.changes_panel.filter_box
        typed = "a" if n % 2 == 0 else "ar"
        # Not over until the filter has looked in every mod: it makes their
        # rows a few at a time, between the window's other work.
        panel = window.changes_panel
        clock.step(
            "a filter keystroke", lambda box_=box_, typed=typed: box_.setText(typed),
            until=lambda panel=panel: not (
                panel._filter_timer.isActive() or panel._filter_step.isActive()
            ),
        )
        clock.step("clear the filter", lambda box_=box_: box_.setText(""))
        clock.step("show the Export tab", lambda: window.tabs.setCurrentIndex(TAB_GENERATE))
        leaf = an_editable_leaf(window)
        if leaf is not None:
            value = str(int(leaf.text(1)) + 1)
            clock.step("an edit, Export tab shown", lambda leaf=leaf, value=value: leaf.setText(1, value))
        # The Mod file tab draws its file again at every change while it
        # shows, which in a window of its own is always: what an edit made
        # beside it costs, for the file changes go to and for the longest.
        clock.step("show the Mod file tab", lambda: window.tabs.setCurrentIndex(TAB_FILE))
        leaf = an_editable_leaf(window)
        if leaf is not None:
            value = str(int(leaf.text(1)) + 1)
            clock.step("an edit, Mod file shown", lambda leaf=leaf, value=value: leaf.setText(1, value))
        longest = longest_mod(window)
        if longest and window.file_panel.look_at(longest):
            clock.step("draw the longest mod's file", window.file_panel.refresh)
            leaf = an_editable_leaf(window)
            if leaf is not None:
                value = str(int(leaf.text(1)) + 1)
                clock.step(
                    "an edit, the longest mod's file shown",
                    lambda leaf=leaf, value=value: leaf.setText(1, value),
                )
            window.file_panel.look_at(window.loadout.editing_origin())
        window.tabs.setCurrentIndex(TAB_SOURCE)
        clock.settle(0.5)
        let_background_finish(window, clock)
        clock.step("Play, nothing to change",
                   lambda: window.live.make_live(confirm=False, launch=False, quiet=True))
        row = a_mod_row(window)
        if row is not None:
            origin = row.data(0, ORIGIN_ROLE)
            clock.step("switch a mod off", lambda row=row: row.setCheckState(0, Qt.CheckState.Unchecked))
            let_background_finish(window, clock)
            clock.step("Play, one mod off",
                       lambda: window.live.make_live(confirm=False, launch=False, quiet=True))
            row = a_mod_row(window, origin)
            row.setCheckState(0, Qt.CheckState.Checked)
            clock.settle(1.0)
            let_background_finish(window, clock)
            window.live.make_live(confirm=False, launch=False, quiet=True)
            clock.settle(1.0)
    return summary(clock.results)


def compare(before: Path, after: Path) -> None:
    old = json.loads(before.read_text(encoding="utf-8"))["steps"]
    new = json.loads(after.read_text(encoding="utf-8"))["steps"]
    print("%-30s %22s %22s %22s" % (
        "", "call ms (before > after)", "after ms (before > after)", "held ms (before > after)"))
    for name in old:
        if name not in new:
            continue
        a, b = old[name], new[name]
        # A run from before the longest hold was kept has none: the call or
        # everything after it, whichever was longer, is the most it was.
        held = [figures.get("held", max(figures["call"], figures["after"])) for figures in (a, b)]
        print("%-30s %10.0f > %-10.0f %10.0f > %-10.0f %10.0f > %-10.0f" % (
            name, a["call"], b["call"], a["after"], b["after"], held[0], held[1]))


def main() -> None:
    faulthandler.enable()
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--census", help="the census folder (holding anno-1800/)")
    parser.add_argument("--work", help="where the mods folder and the sandbox go")
    parser.add_argument("--mods", type=int, default=150, help="how many census mods (default 150)")
    parser.add_argument("--repeat", type=int, default=5)
    parser.add_argument("--loader", action="store_true", help="let the window run the game's loader")
    parser.add_argument("--read", action="store_true",
                        help="read the folder in again, rather than open the loadout read last time")
    parser.add_argument("--alone", action="store_true",
                        help="let the window's background work finish before each step, so each is "
                             "timed with nothing else running; slow, as an edit starts a reading")
    parser.add_argument("--profile", default="", help="cProfile the first step whose name holds this")
    parser.add_argument("--profile-nth", type=int, default=1, metavar="N",
                        help="profile the Nth step whose name holds it, not the first: "
                             "--profile \"an edit\" matches four steps a round, so round 2's first is 5")
    parser.add_argument("--sort", default="cumulative", help="how to order the profile (cumulative, tottime)")
    parser.add_argument("--out", help="write the medians here, as JSON")
    parser.add_argument("--compare", nargs=2, metavar=("BEFORE", "AFTER"))
    args = parser.parse_args()
    if args.compare:
        compare(Path(args.compare[0]), Path(args.compare[1]))
        return
    if not (args.census and args.work):
        parser.error("--census and --work are needed to run")
    steps = run(args)
    print()
    for name, figures in steps.items():
        print("%-30s call %9.1f ms   after %8.1f ms   held %8.1f ms   (%d)" % (
            name, figures["call"], figures["after"], figures["held"], figures["runs"]))
    if args.out:
        Path(args.out).write_text(json.dumps({"args": vars(args), "steps": steps}, indent=1), encoding="utf-8")
    sys.stdout.flush()
    # Closing the window would ask about unsaved work; nothing here is kept.
    # Ending the process under Qt's threads is not a fault worth a traceback.
    faulthandler.disable()
    leave()


def leave() -> None:
    """End the process at once, answering 0.

    ``os._exit`` is ExitProcess on Windows, which still unloads every DLL,
    and Qt taking itself down under a window never closed and threads still
    running is an access violation: every run ended in one, after its
    figures were out, and answered 139 to whatever had started it.
    TerminateProcess unloads nothing.
    """
    sys.stdout.flush()
    sys.stderr.flush()
    if sys.platform == "win32":
        import ctypes

        kernel = ctypes.windll.kernel32
        kernel.GetCurrentProcess.restype = ctypes.c_void_p
        kernel.TerminateProcess.argtypes = (ctypes.c_void_p, ctypes.c_uint)
        kernel.TerminateProcess(kernel.GetCurrentProcess(), 0)
    os._exit(0)


if __name__ == "__main__":
    main()
