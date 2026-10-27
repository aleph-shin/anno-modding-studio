"""Whether a newer Studio is out, and which version the Studio was before an
update: asked in the background and said in the status bar.

``core.studio_release`` asks GitHub; this is the window's side, held as
``window.studio_release``.  The question is asked a few seconds after the
window is up, at most once a day, and not at all once it is switched off in
*Settings ▸ General*.  *Help ▸ Check for updates* asks at once, whatever the
day or the setting, and says how it came out whatever that is.

A newer version is said where the window says things, in the status bar:
a note of its own beside the status line, with a link to the release's page,
for as long as it is not hidden.  It says how to update the way this Studio
runs: a built one unzips the new zip into a new folder, and one run from its
source updates the checkout.  Nothing is downloaded.  *Skip this version*
keeps the note away until a later version is out.

The first start of a version newer than the one that last used the Studio's
folders says so once in the same note, with a link to its release notes.
"""

from __future__ import annotations

import datetime
import html
import sys
import time

from PySide6.QtCore import Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QHBoxLayout, QLabel, QSizePolicy, QWidget

from .. import __version__
from ..core import studio_release
from ..core.changes import APP_NAME
from ..core.i18n import date_text, tr
from .widgets import show_rich

# The links the note can carry, by what following one does.
PAGE = "page"
GUIDE = "guide"
SKIP = "skip"
HIDE = "hide"


class ReleaseNotice(QWidget):
    """The status bar's note of a newer Studio, or of the one just updated
    to: a sentence, then its links.  Hidden until there is something to say,
    and again once it is hidden."""

    # The link followed that is not an address: SKIP or HIDE.
    chosen = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 8, 0)
        row.setSpacing(8)
        self.label = QLabel("")
        self.label.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
        row.addWidget(self.label)
        self.links = QLabel("")
        self.links.setTextInteractionFlags(
            Qt.TextInteractionFlag.LinksAccessibleByMouse
            | Qt.TextInteractionFlag.LinksAccessibleByKeyboard)
        self.links.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        self.links.linkActivated.connect(self._followed)
        row.addWidget(self.links)
        # Where each link that is an address goes.
        self.addresses: dict = {}
        self.hide()

    def say(self, text: str, links, addresses=None, tip: str = "") -> None:
        """Show ``text`` with ``links``, ``(key, words)`` pairs; a key in
        ``addresses`` opens that address, any other is :attr:`chosen`."""
        self.addresses = dict(addresses or {})
        self.label.setText(text)
        self.label.setToolTip(tip)
        show_rich(self.links, " &nbsp;·&nbsp; ".join(
            '<a href="%s">%s</a>' % (key, html.escape(words)) for key, words in links))
        self.links.setAccessibleName(tr("Links for: %s", text))
        self.show()

    def _followed(self, key: str) -> None:
        address = self.addresses.get(key)
        if address:
            QDesktopServices.openUrl(QUrl(address))
            return
        self.chosen.emit(key)


class StudioReleaseController:
    """The window's studio_release; ``window`` is the MainWindow."""

    # "no" once the daily check is switched off in Settings; on otherwise.
    CHECK_KEY = "studio_release_check"
    # When GitHub was last asked, as an ISO time in UTC: asked once a day.
    CHECKED_KEY = "studio_release_checked"
    # The newer release GitHub last named, "version|tag|published", so
    # the note is there again at the next start without asking again.
    NEWEST_KEY = "studio_release_newest"
    # The version "Skip this version" was chosen for: no note for it, or
    # for any before it.
    SKIPPED_KEY = "studio_release_skipped"
    # How long after the window is up the daily check waits: past the
    # start's own work, the game files coming back among it.
    START_DELAY_MS = 5000

    def __init__(self, window) -> None:
        self.window = window
        self.worker = None
        # Whether the check running was asked for from the Help menu, and so
        # says how it came out whatever that is.
        self._say = False
        self.notice = ReleaseNotice()
        self.notice.chosen.connect(self._chosen)
        # The release the note is about, while it is about one.
        self.shown = None

    # -- the setting -------------------------------------------------------
    def is_on(self) -> bool:
        return str(self.window.settings.value(self.CHECK_KEY, "") or "") != "no"

    def set_on(self, on: bool) -> None:
        self.window.settings.setValue(self.CHECK_KEY, "yes" if on else "no")

    def skipped(self) -> str:
        return str(self.window.settings.value(self.SKIPPED_KEY, "") or "")

    # -- when it is asked --------------------------------------------------
    def after_start(self, previous: str = "") -> None:
        """Once the window is up: say which version this was updated from,
        where it was, or else the newer release last found; then ask
        GitHub, in a while, if a day has passed.  ``previous`` is the
        newest version that used the Studio's folders before this one
        (``dataversion.last_used_by``)."""
        was = studio_release.updated_from(previous)
        if was:
            self.say_updated(was)
        else:
            newest = self.remembered()
            if studio_release.wants_saying(newest, self.skipped()):
                self.say_newer(newest)
        QTimer.singleShot(self.START_DELAY_MS, self.check_if_due)

    def check_if_due(self) -> bool:
        """Ask GitHub, in the background, when the check is on and a day has
        passed since it was last asked.  Whether it was asked."""
        if not self.is_on():
            return False
        if not studio_release.is_due(str(self.window.settings.value(self.CHECKED_KEY, "") or "")):
            return False
        return self._ask(say=False)

    def check_now(self) -> bool:
        """Help ▸ Check for updates: ask now, whatever the day and the
        setting, and say how it came out.  Whether it was asked: not while
        a check is already running, whose answer is then said."""
        self.window.status_label.setText(tr("Checking GitHub for a newer version of the Studio…"))
        if self.worker is not None:
            self._say = True
            return False
        return self._ask(say=True)

    def _ask(self, say: bool) -> bool:
        from .workers import StudioReleaseWorker

        if self.worker is not None:
            return False
        # Written as it is asked, answered or not: once a day is once a day
        # offline too.
        self.window.settings.setValue(self.CHECKED_KEY, studio_release.stamp())
        self._say = say
        worker = StudioReleaseWorker(self.window)
        worker.succeeded.connect(self.on_answer)
        worker.finished.connect(lambda: self._let_go(worker))
        worker.finished.connect(worker.deleteLater)
        self.worker = worker
        worker.start()
        return True

    def _let_go(self, worker) -> None:
        if self.worker is worker:
            self.worker = None

    def stop(self) -> None:
        """Wait for a check still running: the window is closing.  It takes
        at most its timeout, and its answer is only kept for next time."""
        if self.worker is not None:
            self.worker.wait(int(studio_release.TIMEOUT * 1000) + 2000)

    # -- what it came to ---------------------------------------------------
    def on_answer(self, answer) -> None:
        said, self._say = self._say, False
        if answer.state == studio_release.NEWER:
            self.remember(answer.release)
            if said or studio_release.wants_saying(answer.release, self.skipped()):
                self.say_newer(answer.release)
            if said:
                # The note beside it says what was found.
                self.window.status_label.setText("")
            return
        if answer.state == studio_release.UP_TO_DATE:
            self.window.settings.remove(self.NEWEST_KEY)
            if self.shown is not None:
                self.hide()
            if said:
                self.window.status_label.setText(tr(
                    "%(app)s %(version)s is the newest version.", app=APP_NAME, version=__version__))
            return
        # A 404 from a repository GitHub does not show, a rate limit,
        # offline: said only when asked for.
        if said:
            self.window.status_label.setText(tr(
                "Couldn't check for a newer version of the Studio. %s", answer.why))

    def remember(self, release) -> None:
        self.window.settings.setValue(self.NEWEST_KEY, "|".join(
            (release.version, release.tag, release.published)))

    def remembered(self):
        """The newer release GitHub last named (a studio_release.Release),
        or None.  Its page is made again from its tag, which is what
        GitHub's own address for it is."""
        parts = str(self.window.settings.value(self.NEWEST_KEY, "") or "").split("|")
        if len(parts) != 3:
            return None
        version, tag, published = parts
        if not version or studio_release.version_of(tag) != version:
            return None
        return studio_release.Release(version=version, tag=tag,
                                      page=studio_release.release_page(tag),
                                      published=published)

    # -- the note ----------------------------------------------------------
    def say_newer(self, release) -> None:
        self.shown = release
        links = [(PAGE, tr("Release page"))]
        addresses = {PAGE: release.page}
        if getattr(sys, "frozen", False):
            text = tr("%(app)s %(version)s is out. Unzip it into a new folder, and your "
                      "data carries over.", app=APP_NAME, version=release.version)
            links.append((GUIDE, tr("How to update")))
            addresses[GUIDE] = studio_release.update_guide()
        else:
            text = tr("%(app)s %(version)s is out. Update your checkout with git pull "
                      "to run it.", app=APP_NAME, version=release.version)
        links += [(SKIP, tr("Skip this version")), (HIDE, tr("Hide"))]
        self.notice.say(text, links, addresses, tip=self._published(release.published))

    def say_updated(self, was: str) -> None:
        """Updated to this version from ``was``: said once, on the first
        start of this version, with a link to its release notes."""
        self.shown = None
        self.notice.say(
            tr("Updated to %(version)s from %(was)s.", version=__version__, was=was),
            [(PAGE, tr("Release notes")), (HIDE, tr("Hide"))],
            {PAGE: studio_release.release_page("v" + __version__)},
        )

    @staticmethod
    def _published(day: str) -> str:
        try:
            when = datetime.date.fromisoformat(day) if day else None
        except ValueError:
            when = None
        return tr("Published %s.", date_text(time.mktime(when.timetuple()))) if when else ""

    def hide(self) -> None:
        self.shown = None
        self.notice.hide()

    def _chosen(self, key: str) -> None:
        if key == SKIP and self.shown is not None:
            version = self.shown.version
            self.window.settings.setValue(self.SKIPPED_KEY, version)
            self.window.status_label.setText(tr(
                "Version %s won't be shown again. A later version will be.", version))
        self.hide()
