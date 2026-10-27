"""The game's own loader, asked as a second opinion: where it is, what it
says about the Studio's reading rules, whether a newer one is out, and what it
makes of the whole loadout.

The loader comes with the Anno Modding Tools extension for VS Code; see
``core.xmltest`` for running it and ``core.loadercheck`` for what is asked
of it.  This is the window's side -- the settings it keeps, the background
work it starts, and what it says when that work is done -- held as
``window.loader_check`` (Stage 3.3 of
docs/proposals/consolidation-proposal.md).  The Export tab asks the same
loader through ``game_loader``; Play asks it for a verdict on each mod it
builds.
"""

from __future__ import annotations

import datetime
import os
import time

from PySide6.QtWidgets import QMessageBox

from ..core.i18n import STORED, date_text, tr, tr_noop, tr_stored, trn
from ..core.reader import READER_VERSION
from .widgets import busy_cursor


class LoaderController:
    """The window's loader_check, moved off it; ``window`` is the MainWindow."""

    # A folder chosen by hand to find the extension in, when it is somewhere
    # none of the editors keep it; "" to look only where they do.
    LOADER_KEY = "loader_folder"
    # Per game, which loader the probes last ran against, and under which of
    # the Studio's readers, and what they said: "version|banner|reader" and
    # "N of M agree".  The probes put the Studio's reading rules to the
    # loader, so a Studio that reads mods differently asks again.
    PROBED_KEY = "loader_probed:%s"
    PROBES_SAID_KEY = "loader_probes_said:%s"
    # The newest Anno Modding Tools the Marketplace has, as "version|date", and
    # the day it was last asked: asked at most once a day, and only with the
    # extension installed -- without it there is nothing to update.
    NEWEST_KEY = "loader_newest"
    NEWEST_ASKED_KEY = "loader_newest_asked"

    def __init__(self, window) -> None:
        self.window = window
        # The probes, and the question to the Marketplace, while they run.
        self.probe_worker = None
        self.newest_worker = None
        # Whether the run going now was asked for from the Check menu, and so
        # says how it came out in the status bar whatever that is.
        self._say_probes = False
        self._say_newest = False
        # Play's checks while they run, and any skipped and not yet ended.
        self.play_workers: list = []
        # The last whole-loadout check: (when, its verdict), or None.
        self.last_loadout_check = None

    def loader_folder(self) -> str:
        return str(self.window.settings.value(self.LOADER_KEY, "") or "")

    def set_loader_folder(self, path: str) -> None:
        self.window.settings.setValue(self.LOADER_KEY, (path or "").strip())
        self.window.output.set_loader_finder(self.game_loader)
        self.probe_loader()
        self._say_newest_loader()

    def game_loader(self, game: str = ""):
        """The game's loader in the newest installed Anno Modding Tools, or
        None -- a second opinion the Studio uses when it is there.

        ``ANNO_STUDIO_LOADER=none`` turns the looking off, which is how the
        test suite keeps a machine with the extension from running the real
        loader behind every window it opens."""
        from ..core import xmltest

        if os.environ.get("ANNO_STUDIO_LOADER", "").strip().lower() == "none":
            return None
        return xmltest.find(game or self.window.loadout.game, extra=self.loader_folder() or None)

    @staticmethod
    def probe_stamp(loader) -> str:
        """What the probes' verdict is kept under: the loader they were put
        to, and the reader whose rules they put."""
        return "%s|%s|%d" % (loader.version, loader.banner, READER_VERSION)

    def probe_loader(self, force: bool = False, say: bool = False) -> bool:
        """Run the drift probes against this game's loader, in the
        background, when it is one they have not run against -- or always,
        ``force``d. Whether they were started.  ``say``: the outcome goes to
        the status bar even when every rule agrees."""
        from .workers import ProbeWorker

        loader = self.game_loader()
        if loader is None:
            return False
        if self.probe_worker is not None:
            # A run is going (one starts with every game-files load): it
            # answers a menu click too, which otherwise ended in silence.
            self._say_probes = self._say_probes or say
            return False
        stamp = self.probe_stamp(loader)
        if not force and str(self.window.settings.value(self.PROBED_KEY % loader.game, "") or "") == stamp:
            return False
        self._say_probes = say
        worker = ProbeWorker(loader, self.window)
        worker.succeeded.connect(self.on_probed)
        worker.finished.connect(lambda: self._let_go("probe_worker", worker))
        worker.finished.connect(worker.deleteLater)
        self.probe_worker = worker
        self.window.background.begin(
            "probe", tr("Trying what the game's loader does…"), worker
        )
        worker.start()
        return True

    def on_probed(self, loader, results) -> None:
        self.probe_worker = None
        agreed = [result for result in results if result.agrees]
        others = [result for result in results if not result.agrees]
        values = {"agreed": len(agreed), "total": len(results), "loader": loader.said,
                  "rules": "; ".join(result.probe.name for result in others)}
        said = (
            tr_noop("%(agreed)d of %(total)d of the Studio's reading rules "
                    "agree with %(loader)s. These do not: %(rules)s", context=STORED) % values
            if others else
            tr_noop("%(agreed)d of %(total)d of the Studio's reading rules agree with "
                    "%(loader)s", context=STORED) % values
        )
        self.window.settings.setValue(self.PROBED_KEY % loader.game, self.probe_stamp(loader))
        self.window.settings.setValue(self.PROBES_SAID_KEY % loader.game, said)
        if self._say_probes and not others:
            self.window.status_label.setText(tr_stored(said))
        self._say_probes = False
        if others:
            # Worth saying out loud: the loader may have changed, and the
            # Studio's reading of these constructs with it.
            self.window.status_label.setText(trn(
                "The game's loader (%(loader)s) reads %(n)s of the Studio's reading "
                "rules differently: %(rules)s. Settings has the details.",
                "The game's loader (%(loader)s) reads %(n)s of the Studio's reading "
                "rules differently: %(rules)s. Settings has the details.",
                len(others), loader=tr_stored(loader.said),
                rules=", ".join(tr_stored(result.probe.name) for result in others[:3]),
            ))

    def probes_said(self, game: str = "") -> str:
        """What the probes last said of ``game``'s loader, or ""."""
        return str(self.window.settings.value(self.PROBES_SAID_KEY % (game or self.window.loadout.game), "") or "")

    def ask_newest_loader(self, force: bool = False, say: bool = False) -> bool:
        """Ask, in the background, whether a newer extension is out than the
        one the loader check runs on. Whether it was asked.  ``say``: the
        answer goes to the status bar even when there is nothing newer."""
        from .workers import NewestLoaderWorker

        loader = self.game_loader()
        if loader is None:
            return False
        if self.newest_worker is not None:
            self._say_newest = self._say_newest or say
            return False
        today = datetime.date.today().isoformat()
        if not force and str(self.window.settings.value(self.NEWEST_ASKED_KEY, "") or "") == today:
            self._say_newest_loader()
            return False
        self._say_newest = say
        if say:
            self.window.status_label.setText(tr("Asking the Marketplace…"))
        worker = NewestLoaderWorker(self.window)
        worker.succeeded.connect(self.on_newest_loader)
        worker.finished.connect(lambda: self._let_go("newest_worker", worker))
        worker.finished.connect(worker.deleteLater)
        self.newest_worker = worker
        worker.start()
        return True

    def _let_go(self, name: str, worker) -> None:
        """Drops the reference to a worker whose thread has ended, answered or
        not: one left behind is a deleted thread that closing the window then
        waits on, and that keeps the question from being asked again."""
        if getattr(self, name) is worker:
            setattr(self, name, None)

    def on_newest_loader(self, published) -> None:
        self.newest_worker = None
        said, self._say_newest = self._say_newest, False
        if published is None:
            if said:
                self.window.status_label.setText(tr("The Marketplace could not be reached."))
            return
        self.window.settings.setValue(self.NEWEST_KEY, "%s|%s" % (published.version, published.date))
        self.window.settings.setValue(self.NEWEST_ASKED_KEY, datetime.date.today().isoformat())
        self._say_newest_loader(announce=True)
        if said and not self.loader_update():
            self.window.status_label.setText(self.newest_said())

    def newest_loader(self):
        """What the Marketplace last said is newest (an xmltest.Published), or None."""
        from ..core import xmltest

        version, _, date = str(self.window.settings.value(self.NEWEST_KEY, "") or "").partition("|")
        return xmltest.Published(version, date) if version else None

    def loader_update(self, loader=None) -> str:
        """A sentence when a newer extension is out than ``loader``'s, or ""."""
        loader = loader or self.game_loader()
        newest = self.newest_loader()
        if loader is None or newest is None or not newest.is_newer_than(loader.version):
            return ""
        when = self.published_on(newest.date)
        if when:
            return tr("Anno Modding Tools %(version)s is out (%(date)s). The "
                      "check uses %(installed)s. VS Code updates it from its "
                      "Extensions view.",
                      version=newest.version, date=when, installed=loader.version)
        return tr("Anno Modding Tools %(version)s is out. The check uses "
                  "%(installed)s. VS Code updates it from its Extensions "
                  "view.", version=newest.version, installed=loader.version)

    def newest_said(self) -> str:
        """What the Marketplace last said of the extension the check runs
        on, as a sentence; "" with no loader to say it of."""
        loader = self.game_loader()
        if loader is None:
            return ""
        update = self.loader_update(loader)
        if update:
            return update
        newest = self.newest_loader()
        if newest is None:
            return tr("Not checked for a newer version yet.")
        when = self.published_on(newest.date)
        if when:
            return tr("%(version)s is the newest version published (%(date)s).", version=loader.version,
                      date=when)
        return tr("%s is the newest version published.", loader.version)

    @staticmethod
    def published_on(day: str) -> str:
        """The Marketplace's date, YYYY-MM-DD, as the reader writes a date; ""."""
        try:
            return date_text(time.mktime(datetime.date.fromisoformat(day).timetuple())) if day else ""
        except ValueError:
            return day

    def _say_newest_loader(self, announce: bool = False) -> None:
        update = self.loader_update()
        self.window.output.set_loader_update(update)
        if update and announce:
            self.window.status_label.setText(update)

    def check_whole_loadout(self) -> bool:
        """Every mod that loads through the game's loader at once, compared
        with what the Studio shows -- after saying how long that takes.
        Whether it was run."""
        from ..core import loadercheck

        loader = self.game_loader()
        if loader is None or self.window.index is None:
            QMessageBox.information(
                self.window, tr("The game's loader is not here"),
                tr("Checking the loadout asks the game's own mod loader, which the Studio finds "
                   "in the Anno Modding Tools extension for VS Code. Install it there, and this "
                   "check and the Export tab's are both available."),
            )
            return False
        with busy_cursor():
            job = loadercheck.prepare_loadout(self.window.loadout, self.window.output.mine_to_export(), self.window.index)
        lines = [
            trn("The one mod that loads goes through the game's loader (%(loader)s), and every "
                "asset it changes is compared with what the Studio shows.",
                "All %(n)s mods that load go through the game's loader (%(loader)s) in one run, and "
                "every asset they change is compared with what the Studio shows.",
                len(job.stack), loader=tr_stored(loader.said)),
            tr("This can take a while: about a minute for a hundred-odd Anno 1800 mods, longer "
               "on a slower machine. It runs in the background, and Stop ends it."),
        ]
        if job.updated or job.missing:
            lines.append(
                trn("%(n)s mod will be left out, updated on disk since the loadout read it or no "
                    "longer where it read it from. Reading the mods folder again first checks it too.",
                    "%(n)s mods will be left out, updated on disk since the loadout read them or no "
                    "longer where it read them from. Reading the mods folder again first checks them "
                    "too.", len(job.updated) + len(job.missing)))
        last = self.last_loadout_check
        box = QMessageBox(QMessageBox.Icon.Question, tr("Check the whole loadout?"), "\n\n".join(lines),
                          QMessageBox.StandardButton.Cancel, self.window)
        check = box.addButton(tr("&Check again") if last is not None else tr("&Check the loadout"),
                              QMessageBox.ButtonRole.AcceptRole)
        box.setDefaultButton(check)
        again = None
        if last is not None:
            # Translators: %s is the time it was made, such as 20:14.
            again = box.addButton(tr("Show the last result (%s)", last[0]), QMessageBox.ButtonRole.AcceptRole)
        answer = self.window.prompts.answer(box)
        if again is not None and answer is again:
            self._show_loadout_check(job, loader, last[1])
            return False
        if answer is not check:
            return False
        self._show_loadout_check(job, loader)
        return True

    def _show_loadout_check(self, job, loader, verdict=None) -> None:
        """The check's dialog: running it now, or showing ``verdict`` again.
        What it finds is kept, since a double-click on a row closes it on the
        way to the change -- but not a check stopped by hand, which found
        nothing and would put out the result before it."""
        from ..core import loadercheck
        from .loadout_check_dialog import LoadoutCheckDialog

        dialog = LoadoutCheckDialog(job, getattr(self.window.index, "index", self.window.index), loader, self.window)
        dialog.changeActivated.connect(self.window.jump_to_change)
        if verdict is not None:
            dialog.show_verdict(verdict)
        else:
            dialog.start()
        try:
            dialog.exec()
        finally:
            found = dialog.verdict
            stopped = found is not None and found.state == loadercheck.FAILED and found.why == tr("stopped")
            if verdict is None and found is not None and not stopped:
                self.last_loadout_check = (time.strftime("%H:%M"), found)
            dialog.deleteLater()

    def loader_verdicts(self, origins) -> list:
        """What the game's loader makes of each mod Play builds: the Export
        tab's verdict where it is fresh, else asked now.

        Asked in the background, one mod after another, under a wait that
        says so and whose button skips the check (H2-15): it ran on this
        thread, with no way out, for as long as the loader took. A check
        that would run more than ``loadercheck.AUTOMATIC_LIMIT`` mods is not
        started, as on the Export tab. A check not run, either way, is a
        verdict that says so and how to run it. None of it when the
        extension is not there."""
        from ..core import loadercheck

        loader = self.game_loader()
        output = self.window.output
        if loader is None or self.window.index is None:
            return []
        how = tr("Run it with “Ask the loader again” on the Export tab, or check the whole loadout "
                 "from the Check menu.")
        found = {}
        asking, awaiting = [], []
        with output.checks_held():
            with busy_cursor():
                running = output.loader_check_running()
                for origin in origins:
                    job, key = output.loader_job(origin)
                    verdict = output.fresh_loader_verdict(origin, key)
                    if verdict is not None and verdict.state != loadercheck.NOT_RUN:
                        found[origin] = verdict
                    elif not job.writes:
                        continue  # no ModOps: nothing for the loader to check
                    elif not loadercheck.runs_by_itself(job):
                        found[origin] = loadercheck.not_run(job, loader, loadercheck.too_many(job) + " " + how)
                    elif key == running:
                        # The Export tab is asking already: its answer is waited for.
                        awaiting.append((job, key))
                    else:
                        asking.append((job, key))
            answered = self._ask_for_play(asking, loader, found, bool(awaiting))
            for job, key in awaiting:
                verdict = output.fresh_loader_verdict(job.origin, key)
                if verdict is not None:
                    found[job.origin] = verdict
            if not answered:
                for job, _key in asking + awaiting:
                    if job.origin not in found:
                        found[job.origin] = loadercheck.not_run(
                            job, loader, tr("You skipped the check.") + " " + how)
        return [found[origin] for origin in origins if origin in found and found[origin].state in (
            loadercheck.AGREES, loadercheck.DIFFERS, loadercheck.FAILED, loadercheck.NOT_RUN)]

    # Play's own checks, in the list of what runs in the background: apart
    # from the Export tab's "loader", which ends when its own check does.
    PLAY_KEY = "play-loader"

    def _ask_for_play(self, asking, loader, found: dict, with_the_tab: bool) -> bool:
        """Run ``asking``, ``(job, key)`` pairs, through the loader one after
        another on threads of their own, and wait for them -- and for the
        Export tab's check, ``with_the_tab`` -- under a dialog. Each answer
        goes into ``found`` by mod, and to the Export tab. Whether every one
        was answered: False when the check was skipped."""
        from .workers import LoaderCheckWorker

        background = self.window.background
        index = getattr(self.window.index, "index", self.window.index)
        pending = list(asking)
        skipped = []

        def answered(key, verdict) -> None:
            # Not after a skip: the loadout may be another by the time a
            # skipped check ends, and its mod "" is the same key's.
            if not skipped:
                found[key[0]] = verdict
                self.window.output.keep_loader_verdict(key, verdict)

        def next_one() -> None:
            if skipped:
                return
            if not pending:
                background.end(self.PLAY_KEY)
                return
            job, key = pending.pop(0)
            worker = LoaderCheckWorker(key, job, index, loader, parent=self.window)
            worker.succeeded.connect(answered)
            worker.finished.connect(lambda: self.play_workers.remove(worker))
            worker.finished.connect(next_one)
            worker.finished.connect(worker.deleteLater)
            self.play_workers.append(worker)
            background.begin(self.PLAY_KEY, tr("Asking the game's loader about %s for Play…", job.name))
            worker.start()

        next_one()
        waited = (self.PLAY_KEY, "loader") if with_the_tab else (self.PLAY_KEY,)
        if background.wait(self.window, only=waited, title=tr("Waiting for the game's loader"),
                           cancel=tr("&Skip the check"),
                           note=tr("Skip the check to go on without it. It is then listed as not run.")):
            return True
        skipped.append(True)
        background.end(self.PLAY_KEY)
        for worker in self.play_workers:
            worker.requestInterruption()
        return False

    def stop_play_checks(self) -> None:
        """Stop Play's checks still running, skipped: the window is closing."""
        for worker in list(self.play_workers):
            worker.requestInterruption()
            worker.wait(30000)
