"""Where you start: the game's files, and what you are here to do.

Two things belong on the first tab and nothing else does.  The first is the game
data everything is built against -- a permanent step rather than a one-time
splash, since swapping to another assets.xml or a different language should work
the same way on the tenth time as on the first, and what is currently loaded
should always be visible.

The second is the part that is easy to get wrong by not asking.  Three quite
different jobs share this one window: writing a mod to publish, adjusting one
you downloaded, and playing a set of mods -- a loadout.  They want the workspace
set up in *opposite* ways -- the first wants nothing else loaded, the last wants
everything loaded -- and somebody who picks the wrong one finds out several
steps later, when their mod exports with another author's changes folded into
it.  So the tab asks, in one click, and each answer is the action that sets the
workspace up for it.

Picking one is not a mode.  It sets the loadout up for the job, opens the other
tabs -- which wait for an answer, see ``MainWindow.update_tab_gates`` -- and
changes which way out the Export tab makes obvious; every menu entry stays
exactly where it was.

The page is read two ways.  A first visit needs the explanations; a return
visit needs to see where things stand and get back to work.  So each step is a
card whose header answers its own question at a glance -- which game, which
loadout, which way of working -- and the step that is done once and rarely
revisited, loading the game files, folds down to that header once it is done.
"""

from __future__ import annotations

import html
import os
from pathlib import Path

from PySide6.QtCore import QEvent, QSize, Qt, Signal
from PySide6.QtGui import QFont, QGuiApplication
from PySide6.QtWidgets import (
    QBoxLayout,
    QComboBox,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ..core.changes import AIM_MAKE, AIM_MANAGE, AIM_TWEAK, loadout_name
from ..core.i18n import number, tr, tr_kw, tr_noop, trn
from . import theme
from .help_tip import CREATOR_HELP, HelpTip
from .loadout_files import LoadoutFiles, recent_rows
from .widgets import (
    ElidingLabel,
    FlowLayout,
    keep_wrapped_text_whole,
    open_on_disk,
    rich_label,
    show_rich,
    tab_after,
    wrappable_path,
)

# The anno-mods copy: the original author's account was renamed, so the old
# link 404s. This one is owned by the modding org rather than a person.
RDA_TOOL_URL = "https://github.com/anno-mods/RDAExplorer"

# How wide a column of prose may get, in body-font pixels: about a hundred
# characters of hint text to a line, so a paragraph is a paragraph.
PAGE_EMS = 70
# Given this much room, the page splits in two: setting up on the left, what you
# are here to do on the right -- so a wide window shows the whole page at once
# instead of a narrow strip with a scrollbar beside a lot of empty space.
SPLIT_EMS = 100
# And how wide the two columns together may get.
WIDE_EMS = 110

# What the second step's header says, once a card has set the loadout up. A
# recollection of the click, not a claim about what the loadout has become
# since: what was loaded and written afterwards is what the cards' status
# lines report, and nothing else in the Studio turns on which card it was.
#
# Marked here and translated where shown, since nothing is installed yet when
# this module loads.
AIM_NAMES = {
    # Translators: the quoted words are the title of the card that was
    # clicked, "Write a mod of your own"; use its translation.
    AIM_MAKE: tr_noop("Set up as “Write a mod of your own”"),
    # Translators: the quoted words are the title of the card that was
    # clicked, "Change a mod you downloaded"; use its translation.
    AIM_TWEAK: tr_noop("Set up as “Change a mod you downloaded”"),
    # Translators: the quoted words are the title of the card that was
    # clicked, "Play a set of mods"; use its translation.
    AIM_MANAGE: tr_noop("Set up as “Play a set of mods”"),
}


def _centred(widget) -> QHBoxLayout:
    """``widget`` in a row that gives it all the width up to its maximum and
    splits whatever is left evenly either side."""
    row = QHBoxLayout()
    row.addStretch(1)
    row.addWidget(widget, 100)
    row.addStretch(1)
    return row


def _repolish(widget) -> None:
    """A dynamic property only reaches the painted widget on a re-polish."""
    widget.style().unpolish(widget)
    widget.style().polish(widget)


# The three ways of working, in the order they get harder to set up. The first
# two differ in whose mod it is, not in whether it was started here: carrying
# on with a mod of yours is writing a mod of your own, so that card opens one
# as well as starting one.
#
# Each is marked here and translated where the card is built.
WAYS = (
    (
        AIM_MAKE,
        # Translators: the title of one of the Studio's three ways of working,
        # a card on its Start tab.
        tr_noop("Write a mod of your own"),
        tr_noop("A mod to publish. Start a new one, or carry on with one of yours."),
        tr_noop(
            "You work against the base game, because you can't know what else "
            "the people who download your mod are running. Start with no other "
            "mods loaded, and keep each mod in its own loadout. You can load "
            "other mods later to check compatibility. That doesn't change what "
            "you export."
        ),
        tr_noop("Start a new mod"),
        tr_noop("Open one of yours…"),
        "",
    ),
    (
        AIM_TWEAK,
        # Translators: the title of one of the Studio's three ways of working,
        # a card on its Start tab.
        tr_noop("Change a mod you downloaded"),
        tr_noop("Someone else's mod, adjusted to your taste, as a patch."),
        tr_noop(
            "Import the mod and make a patch that loads straight after it. "
            "Change what you like and add it to what you play. The patch is "
            "yours, so an update to the original leaves your changes in place. "
            "If the mod turns out to be yours, it gets no patch and you edit "
            "it directly."
        ),
        tr_noop("Import one mod…"),
        "",
        "",
    ),
    # A loadout: the mods you play. Where its list came from is which button
    # started it. That it is a set to play is what the card writes.
    (
        AIM_MANAGE,
        # Translators: the title of one of the Studio's three ways of working,
        # a card on its Start tab.
        tr_noop("Play a set of mods"),
        # Translators: a "loadout" is the Studio's name for a set of mods you
        # play.
        tr_noop("A loadout: the mods you play, in load order, with their conflicts shown."),
        # Translators: "Play" is the Studio's button that builds the mods and
        # starts the game.
        tr_noop(
            "What the loadout has on is what the game loads when you press "
            "Play. You can keep several loadouts, and fork one to try "
            "something without changing it."
        ),
        # Translators: a button that starts a loadout from the mods the game
        # loads today.
        tr_noop("What I play now…"),
        # Translators: a button that starts a loadout from a mod.io collection.
        tr_noop("A collection…"),
        # Translators: a button that starts a loadout from mods picked out of
        # the Studio's library.
        tr_noop("From the library…"),
    ),
)


class StepCard(QFrame):
    """One numbered step: a header that answers it at a glance, and a body.

    The status on the right of the header is the point.  Somebody coming back
    to this tab wants to know which game is loaded, which loadout is open and
    which way they are working -- three answers -- and should not have to read
    three sections of explanation to find them.
    """

    def __init__(self, number: int, title: str, parent=None):
        super().__init__(parent)
        self.setObjectName("StepCard")
        outer = QVBoxLayout(self)
        # Inside the card's own 1px border, so the header's rounded corners sit
        # exactly within the card's.
        outer.setContentsMargins(1, 1, 1, 1)
        outer.setSpacing(0)

        self.head = QFrame()
        self.head.setObjectName("StepHead")
        self.head.setProperty("alone", False)
        row = QHBoxLayout(self.head)
        row.setContentsMargins(14, 10, 14, 10)
        row.setSpacing(10)
        self.badge = QLabel(str(number))
        self.badge.setObjectName("StepBadge")
        self.badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        row.addWidget(self.badge, 0, Qt.AlignmentFlag.AlignVCenter)
        self.title = QLabel(title)
        self.title.setObjectName("StepTitle")
        row.addWidget(self.title, 0, Qt.AlignmentFlag.AlignVCenter)
        # Takes whatever room the title leaves and shortens to fit it, with the
        # whole of it in the tooltip. A plain label here made the longest
        # status the card's minimum width -- and the page's column with it.
        self.status = ElidingLabel(Qt.TextElideMode.ElideRight)
        self.status.setObjectName("StepStatus")
        self.status.setProperty("state", "")
        self.status.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        row.addWidget(self.status, 1, Qt.AlignmentFlag.AlignVCenter)
        self._head_row = row
        self.help = None
        outer.addWidget(self.head)

        self.body = QWidget()
        self.body.setObjectName("StepBody")
        self.body_layout = QVBoxLayout(self.body)
        self.body_layout.setContentsMargins(14, 12, 14, 14)
        self.body_layout.setSpacing(10)
        outer.addWidget(self.body)

    def set_help(self, text: str) -> None:
        """Put a ``?`` beside the title, holding what the step is about."""
        self.help = HelpTip(text, self.title.text())
        self._head_row.insertWidget(2, self.help, 0, Qt.AlignmentFlag.AlignVCenter)

    def add_to_head(self, widget) -> None:
        self._head_row.addWidget(widget, 0, Qt.AlignmentFlag.AlignVCenter)

    def set_status(self, text: str, state: str = "") -> None:
        """``state`` is "ok" (done), "todo" (wants doing) or "" (neither)."""
        self.status.setFullText(text)
        self.status.setToolTip(text)
        self.status.setProperty("state", state)
        _repolish(self.status)

    def set_open(self, open_: bool) -> None:
        """Show the body, or fold the card down to its header."""
        self.body.setVisible(open_)
        # A header on its own rounds its bottom corners too, or they would
        # sit square inside the card's rounded ones.
        self.head.setProperty("alone", not open_)
        _repolish(self.head)


class WayCard(QFrame):
    """One way of working: what it is for, and the button that sets it up."""

    chosen = Signal(str)
    # The card's second button, where it has one: opening an existing mod
    # rather than starting a new one.
    openedExisting = Signal()
    # And its third: another place a loadout's mods can come from.
    thirdChosen = Signal()
    # Back to the editor, from the way already in use.
    continued = Signal()

    def __init__(self, key: str, title: str, blurb: str, help_text: str, action: str,
                 second: str = "", third: str = "", parent=None):
        super().__init__(parent)
        self.key = key
        self.setObjectName("WayCard")
        self.setProperty("current", False)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(6)

        head = QHBoxLayout()
        head.setSpacing(6)
        self.title = rich_label("<b>%s</b>" % html.escape(title))
        head.addWidget(self.title, 0)
        self.help = HelpTip(help_text, title)
        head.addWidget(self.help, 0, Qt.AlignmentFlag.AlignVCenter)
        head.addStretch(1)
        self.mark = QLabel("")
        self.mark.setObjectName("SubHeading")
        head.addWidget(self.mark, 0, Qt.AlignmentFlag.AlignVCenter)
        layout.addLayout(head)

        text = QLabel(blurb)
        text.setObjectName("StepHint")
        text.setWordWrap(True)
        layout.addWidget(text)

        # What to do next, once this is the way you are working. Written from
        # what the loadout actually holds rather than from a script, because
        # "read in one mod" is unhelpful advice to somebody who already has.
        self.status = QLabel("")
        self.status.setWordWrap(True)
        # Brighter than the description above it: one is what this way of
        # working is, the other is what to do about it next, and at a glance
        # they should not look like the same sentence continuing.
        theme.tint(self.status, "text")
        self.status.setVisible(False)
        layout.addWidget(self.status)

        row = QHBoxLayout()
        row.setContentsMargins(0, 2, 0, 0)
        self.button = QPushButton(action)
        self.button.clicked.connect(lambda: self.chosen.emit(self.key))
        row.addWidget(self.button)
        self.second_button = None
        if second:
            self.second_button = QPushButton(second)
            self.second_button.clicked.connect(self.openedExisting.emit)
            row.addWidget(self.second_button)
        self.third_button = None
        if third:
            self.third_button = QPushButton(third)
            self.third_button.clicked.connect(self.thirdChosen.emit)
            row.addWidget(self.third_button)
        # The way back to work, on the card for the work. It used to be a
        # footer under the whole page, which duplicated the Asset editor tab
        # and was only ever useful to somebody returning here mid-way -- who
        # is somebody with a way of working already in use.
        self.continue_button = QPushButton(tr("Continue editing"))
        self.continue_button.clicked.connect(self.continued.emit)
        self.continue_button.setVisible(False)
        row.addWidget(self.continue_button)
        row.addStretch(1)
        layout.addLayout(row)

    def set_state(self, current: bool, status: str = "", enabled: bool = True,
                  pending: bool = False) -> None:
        """``pending`` is a way still being set up: picked, with its questions
        open, and not yet taken -- which is not the same thing, and says so.

        ``current`` is the card that set this loadout up.  Its mark says so
        and no more: it used to say "in use", which claimed the loadout was
        still doing what the card described, and nothing checked that."""
        self.setProperty("current", current or pending)
        self.mark.setText(
            tr("setting up…") if pending else tr("set up here") if current else ""
        )
        self.status.setText(status)
        self.status.setVisible(bool(status))
        self.button.setEnabled(enabled)
        for button in (self.second_button, self.third_button):
            if button is not None:
                button.setEnabled(enabled)
        carry_on = current and not pending and enabled
        self.continue_button.setVisible(carry_on)
        # One obvious next move per card: carrying on, for the way in use;
        # the button that sets it up, while it is being set up.
        self.continue_button.setObjectName("Primary" if carry_on else "")
        self.button.setObjectName("Primary" if pending else "")
        for widget in (self, self.button, self.continue_button):
            _repolish(widget)


class _OneClickList(QListWidget):
    """A list a click opens from, where a double-click is still one click.

    Qt makes the second press of a double-click an activation, and when the
    first click has already opened a loadout the list is rebuilt without it:
    that press then lands on whichever row moved up under the pointer, and
    opened that one too.  So the second press, and its release, do nothing.
    """

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._second_press = False

    def mouseDoubleClickEvent(self, event) -> None:
        self._second_press = True
        event.accept()

    def mouseReleaseEvent(self, event) -> None:
        if self._second_press:
            self._second_press = False
            event.accept()
            return
        super().mouseReleaseEvent(event)


class SourcePanel(QWidget):
    """Shows the loaded base file and texts, and offers ways to change them."""

    importRequested = Signal()
    # Open what was extracted for a game already, without reading the archives
    # again; or go and read them. One row per game, so switching between them
    # is the same gesture as loading one.
    gameChosen = Signal(str)
    readGameRequested = Signal(str)
    # Who you publish as -- a fact the Studio leans on heavily and used to ask
    # once, before anybody had the context to answer it.
    creatorChangeRequested = Signal()
    # Show the library's folder in Explorer.
    # Help > Studio folders.
    foldersRequested = Signal()
    browseAssetsRequested = Signal()
    browseTextsRequested = Signal()
    clearTextsRequested = Signal()
    continueRequested = Signal()
    aimChosen = Signal(str)
    # Writing a mod of your own, starting from one of yours that already exists.
    ownModRequested = Signal()
    # A loadout, starting from a collection on mod.io.
    collectionLoadoutRequested = Signal()
    # A loadout, starting from mods picked out of the library.
    libraryLoadoutRequested = Signal()
    # A copy of this loadout under a new name, to try something in.
    forkRequested = Signal()
    # The loadout's profiles: one chosen, by name; and the three things done
    # to them, each asked by the window.
    profileChosen = Signal(str)
    newProfileRequested = Signal()
    renameProfileRequested = Signal()
    deleteProfileRequested = Signal()
    # A fork as a loadout of its own: the other half of what forking offers,
    # beside New profile in the same list.
    separateCopyRequested = Signal()
    # The loadout this work lives in: which space you are in, asked before what
    # you are doing in it, because a saved loadout already answers the second.
    renameRequested = Signal()
    openLoadoutRequested = Signal()
    recentChosen = Signal(str)
    # What the game will load right now, and the two ways to make it and the
    # loadout agree.
    bringInRequested = Signal()
    # Make the game load this loadout: and start it, or not.
    playRequested = Signal()
    makeLiveRequested = Signal()
    # The conversion checklist's rows: which place's mods to convert ("cache"
    # or "yours" -- see core.convert; "" for every place, the row above
    # them).
    convertRequested = Signal(str)
    # The mods in the mods folder this loadout does not mention, into it.
    addStrangersRequested = Signal()
    # And the last: the game's mod browser's copies the library has its own of.
    unsubscribeRequested = Signal()
    # The two lists behind the card's warnings: what the loading mods say of
    # each other, and the GUIDs they take twice.
    needsRequested = Signal()
    repeatsRequested = Signal()
    truthRefreshRequested = Signal()
    # Whether mod.io has moved on from any loaded mod, and every mod that
    # loads through the game's loader: the menu entries' own buttons.
    updatesRequested = Signal()
    loadoutCheckRequested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._wide = None
        # The game files step folds away once loaded; this is whether somebody
        # opened it again to change something.
        self._files_open = False
        self._loaded_path = None
        # Where the library is, for the line that says where converting puts
        # the mods: told with the truth, by the window that knows.
        self._library_dir = ""
        # Every paragraph on this page wraps, and side by side Qt under-counts
        # how tall some of them are; these keep each one whole -- once there
        # are paragraphs, after the page is built.
        self._wrapped: list = []
        self._build_ui()
        self._wrapped = keep_wrapped_text_whole(self)
        self.set_state(None, 0, None, 0)
        self.set_loadout(None, False)
        # Nothing is loaded yet, so none of the three can be started; the cards
        # are still worth showing, because they are what says the window has
        # three jobs and which one you are about to be doing.
        self.set_aim("", {}, ready=False)

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        outer.addWidget(scroll, 1)
        self._scroll = scroll

        page = QWidget()
        scroll.setWidget(page)
        column = QVBoxLayout(page)
        column.setContentsMargins(16, 14, 16, 16)
        column.setSpacing(0)

        # The page, centred. It is prose and cards, unlike the other tabs --
        # tables, trees and XML that earn every pixel of a fullscreen window --
        # so it keeps to a reading width, and on a wide window it spends the
        # extra on a second column rather than on longer lines.
        body = QWidget()
        body.setMinimumWidth(560)
        self._body = body
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(0, 0, 0, 0)
        body_layout.setSpacing(12)

        heading = QLabel("Anno Modding Studio")  # i18n: keep - a name
        heading.setObjectName("Heading")
        body_layout.addWidget(heading)

        self._heading = heading
        self._intro = QLabel(tr(
            "The Studio reads and writes Anno mods. Load your game files in "
            "step 1, then pick a way of working in step 2."
        ))
        self._intro.setObjectName("StepHint")
        self._intro.setWordWrap(True)
        body_layout.addWidget(self._intro)

        # Setting up (game files, loadout) and the choice (what you are here
        # to do): one column read top to bottom, or two side by side. One box
        # layout whose direction turns, rather than a grid the two are moved
        # around in: a grid under-reckoned how tall the second column's
        # wrapped paragraphs were and cut their last lines off.
        self._split = QBoxLayout(QBoxLayout.Direction.TopToBottom)
        self._split.setContentsMargins(0, 4, 0, 0)
        self._split.setSpacing(16)
        body_layout.addLayout(self._split)

        self._setup = QWidget()
        setup_layout = QVBoxLayout(self._setup)
        setup_layout.setContentsMargins(0, 0, 0, 0)
        setup_layout.setSpacing(16)
        self.files_step = self._build_files()
        setup_layout.addWidget(self.files_step)

        # The loadout comes after the ways of working, not before: a card is
        # what makes a loadout, so the loadout is the answer that click gave,
        # and the card for opening one you had is the other way of arriving at
        # one. It used to sit above them, on the argument that a saved loadout
        # answered the question the cards asked -- but the cards are not a
        # question any more, they are the setting up.
        #
        # Side by side, the first two steps share the left column and the
        # loadout has the right one. The game files fold down to a header
        # once read, so a column holding only them was a strip of nothing
        # beside a column holding everything else.
        self.ways_step = self._build_ways()
        setup_layout.addWidget(self.ways_step)
        setup_layout.addStretch(1)
        self.loadout_step = self._build_loadout()
        self.truth_step = self._build_truth()
        # Held at the top of its cell by a stretch under it, as the setup
        # column is -- not by aligning it there, which sizes the cell from the
        # card's unwrapped height and clips every paragraph that wraps.
        self._choice = QWidget()
        choice_layout = QVBoxLayout(self._choice)
        choice_layout.setContentsMargins(0, 0, 0, 0)
        choice_layout.setSpacing(16)
        choice_layout.addWidget(self.loadout_step)
        choice_layout.addWidget(self.truth_step)
        choice_layout.addStretch(1)
        self._split.addWidget(self._setup, 1)
        self._split.addWidget(self._choice, 1)

        column.addLayout(_centred(body))
        column.addStretch(1)
        self._reflow(force=True)

    # -- the steps ------------------------------------------------------------
    def _build_files(self) -> StepCard:
        card = StepCard(1, tr("Game files"))
        # Folded, the header is the whole step; this is the way back in.
        self.files_toggle = QPushButton(tr("Change…"))
        self.files_toggle.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.files_toggle.clicked.connect(self._toggle_files)
        card.add_to_head(self.files_toggle)
        layout = card.body_layout

        card.set_help(tr(
            "Everything is built against the game's base asset file. The "
            "Studio reads it from the .rda archives in your installation, so "
            "you don't need to extract anything by hand."
        ))

        # -- the one-click path
        import_box = QFrame()
        import_box.setObjectName("WelcomeCard")
        import_layout = QHBoxLayout(import_box)
        import_layout.setContentsMargins(16, 14, 16, 14)
        # Room between the wrapped blurb and the button, so a long line does
        # not run right up against it.
        import_layout.setSpacing(18)
        self.import_blurb = QLabel("")
        self.import_blurb.setWordWrap(True)
        import_layout.addWidget(self.import_blurb, 1)
        self.import_button = QPushButton(tr("Load from game files…"))
        self.import_button.setObjectName("Primary")
        self.import_button.clicked.connect(self.importRequested.emit)
        import_layout.addWidget(self.import_button, 0, Qt.AlignmentFlag.AlignVCenter)
        layout.addWidget(import_box)

        # One row per game the Studio knows, with what has been read for it and
        # the way in. Switching games used to be the un-highlighted second
        # button on a card that is folded away most of the time -- which is to
        # say it was not a pathway at all.
        self.games_box = QWidget()
        self.games_row = QVBoxLayout(self.games_box)
        self.games_row.setContentsMargins(0, 0, 0, 0)
        self.games_row.setSpacing(6)
        layout.addWidget(self.games_box)

        # -- current state
        current = QGroupBox(tr("Currently loaded"))
        grid = QGridLayout(current)
        grid.setColumnStretch(1, 1)
        grid.setVerticalSpacing(8)

        # Which game, said outright. It used to be readable only off the base
        # file's path -- which is long, is elided to fit, and is the last place
        # anybody would look for the answer to "what am I modding".
        grid.addWidget(QLabel(tr("Game:")), 0, 0, Qt.AlignmentFlag.AlignTop)  # design: names a value that is only read
        self.game_label = QLabel("")
        game_font = QFont()
        game_font.setBold(True)
        self.game_label.setFont(game_font)
        grid.addWidget(self.game_label, 0, 1, 1, 2)

        grid.addWidget(QLabel(tr("Base assets:")), 1, 0, Qt.AlignmentFlag.AlignTop)  # design: names a value that is only read
        self.assets_label = ElidingLabel()
        # Long paths shorten to fit; this is only a floor so one stays legible.
        self.assets_label.setMinimumWidth(120)
        theme.monospace(self.assets_label)
        self.assets_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        grid.addWidget(self.assets_label, 1, 1)
        assets_browse = QPushButton(tr("Choose a file…"))
        assets_browse.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        assets_browse.clicked.connect(self.browseAssetsRequested.emit)
        grid.addWidget(assets_browse, 1, 2, Qt.AlignmentFlag.AlignTop)

        self.assets_detail = QLabel("")
        self.assets_detail.setObjectName("SubHeading")
        # Wrapped, so a sentence here is not the narrowest the column can be.
        self.assets_detail.setWordWrap(True)
        grid.addWidget(self.assets_detail, 2, 1, 1, 2)

        # Translators: the game's own texts file -- the names of things in
        # the game, in one language.
        grid.addWidget(QLabel(tr("Localisation:")), 3, 0, Qt.AlignmentFlag.AlignTop)  # design: names a value that is only read
        self.texts_label = ElidingLabel()
        self.texts_label.setMinimumWidth(120)
        theme.monospace(self.texts_label)
        self.texts_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        grid.addWidget(self.texts_label, 3, 1)

        texts_buttons = QHBoxLayout()
        texts_browse = QPushButton(tr("Choose a file…"))
        texts_browse.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        texts_browse.clicked.connect(self.browseTextsRequested.emit)
        texts_buttons.addWidget(texts_browse)
        self.texts_clear = QPushButton(tr("Clear"))
        self.texts_clear.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.texts_clear.clicked.connect(self.clearTextsRequested.emit)
        texts_buttons.addWidget(self.texts_clear)
        holder = QWidget()
        holder.setLayout(texts_buttons)
        texts_buttons.setContentsMargins(0, 0, 0, 0)
        grid.addWidget(holder, 3, 2, Qt.AlignmentFlag.AlignTop)

        self.texts_detail = QLabel("")
        self.texts_detail.setObjectName("SubHeading")
        self.texts_detail.setWordWrap(True)
        grid.addWidget(self.texts_detail, 4, 1, 1, 2)

        layout.addWidget(current)

        # -- manual escape hatch
        self.manual = rich_label(tr(
            "To extract by hand, open the archive that holds "
            "<code>assets.xml</code> in <a href=\"%(url)s\">RDAExplorer</a>, "
            "extract the file, and choose it above.",
            url=RDA_TOOL_URL,
        ))
        self.manual.setObjectName("StepHint")
        self.manual.setWordWrap(True)
        self.manual.setOpenExternalLinks(True)
        layout.addWidget(self.manual)
        return card

    def _build_loadout(self) -> StepCard:
        """Which loadout this is, which is a question before what it is for.

        A saved loadout already knows what it is for, so this comes first and
        can answer the step after it on its own.  A new one has its home
        settled the moment a way of working is chosen -- work that does not
        know where it belongs is work you have not really decided to do -- and
        it is shown here from that moment rather than at the first Save.
        """
        card = StepCard(3, tr("Your loadout"))
        # Folded once there is one, like the game files above: a loadout you
        # are already in is a fact, not a question, and this card is only worth
        # the room when you want to change which one.
        self.loadout_toggle = QPushButton(tr("Change…"))
        self.loadout_toggle.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.loadout_toggle.clicked.connect(self._toggle_loadout)
        # Forking a loadout: a new profile, or a separate copy. Both are at
        # the foot of the profile picker on the header, so this button is in
        # the card with the other things done to the loadout as a whole --
        # on the header it squeezed the loadout's own name down to a letter.
        # Translators: a "loadout" is the Studio's name for a set of mods you
        # play; to fork one is to make a copy of it to try things in.
        self.fork_button = QPushButton(tr("Fork this loadout…"))
        self.fork_button.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.fork_button.setToolTip(tr(
            "Opens a copy of this loadout under a new name. This one stays unchanged."
        ))
        self.fork_button.clicked.connect(self.forkRequested.emit)
        self.fork_button.setVisible(False)
        # Which profile of the loadout is chosen, beside its name on the
        # header -- on the header because the card is folded whenever a
        # loadout is open, and which profile you are in is the first thing
        # every other card on the page depends on. The three things done to
        # profiles are at the foot of the same list, where anybody looking
        # for them is already looking.
        self.profile_box = QComboBox()
        self.profile_box.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
        self.profile_box.setToolTip(tr(
            "The profile you are working in. A profile records which mods are on."
        ))
        self.profile_box.setAccessibleName(tr("Profile"))
        # Its list drops down below it, as a menu does. Fusion opens a combo's
        # list over the box with the chosen row under the pointer, so the
        # acts at its foot moved a row with each profile chosen and were hit
        # by mistake (H2-18). Below the box they are where they were last time.
        # The theme's QComboBox#ProfileBox rule says so.
        self.profile_box.setObjectName("ProfileBox")
        self.profile_box.activated.connect(self._profile_picked)
        self.profile_box.setVisible(False)
        self._profile_chosen = ""
        card.add_to_head(self.profile_box)
        # And playing it, on the same header for the same reason.
        # Translators: "Play" is the Studio's name for building your mods and
        # starting the game, not a verb in the abstract; keep it short.
        self.play_button = QPushButton(tr("▶  Play"))
        self.play_button.setObjectName("Primary")
        self.play_button.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self._play_tip = tr("Makes this loadout what the game loads, then starts the game.")
        self.play_button.setToolTip(self._play_tip)
        self.play_button.clicked.connect(self.playRequested.emit)
        self.play_button.setVisible(False)
        card.add_to_head(self.play_button)
        card.add_to_head(self.loadout_toggle)
        self._loadout_open = False
        self._loadout_settled = False
        self._loadout_status = (tr("None yet"), "")
        self._creator = ""
        layout = card.body_layout

        card.set_help(tr(
            "Everything you do is kept in a loadout: your mod's details, the "
            "mods you have open, every change, and a history of versions. "
            "Picking a way of working starts a loadout. You don't have to "
            "save it first."
        ))

        row = QHBoxLayout()
        row.setSpacing(8)
        caption = QLabel(tr("Working in:"))  # design: names a value that is only read
        row.addWidget(caption, 0, Qt.AlignmentFlag.AlignVCenter)
        self.loadout_label = ElidingLabel()
        self.loadout_label.setMinimumWidth(160)
        font = QFont()
        font.setBold(True)
        self.loadout_label.setFont(font)
        row.addWidget(self.loadout_label, 1)

        self.rename_button = QPushButton(tr("Rename…"))
        self.rename_button.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.rename_button.clicked.connect(self.renameRequested.emit)
        row.addWidget(self.rename_button)
        row.addWidget(self.fork_button)
        # No "New" here. A loadout is made by the step above -- picking a way
        # of working names one and places it -- and a button that
        # makes a nameless, aimless one is a button for reaching the exact state
        # that step exists to stop you being in. File ▸ New loadout is still
        # there for clearing the decks.
        open_button = QPushButton(tr("Open…"))
        open_button.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        open_button.clicked.connect(self.openLoadoutRequested.emit)
        row.addWidget(open_button)
        layout.addLayout(row)

        self.loadout_detail = QLabel("")
        self.loadout_detail.setObjectName("SubHeading")
        self.loadout_detail.setWordWrap(True)
        layout.addWidget(self.loadout_detail)

        # Who you publish as. Asked once, at the moment it first matters --
        # which is before a new user has any idea what it decides: it goes into
        # every mod you export, and it is how the Studio tells your own mods
        # from other people's, which is what decides whether it edits one
        # directly or puts a patch in front of it. So it is on screen, with the
        # way to change it beside it, rather than being a question you answered
        # once and can no longer find.
        who = QHBoxLayout()
        who.setSpacing(8)
        who.addWidget(QLabel(tr("Publishing as:")), 0, Qt.AlignmentFlag.AlignVCenter)  # design: names a value that is only read
        self.creator_label = QLabel("")
        font = QFont()
        font.setBold(True)
        self.creator_label.setFont(font)
        who.addWidget(self.creator_label, 1, Qt.AlignmentFlag.AlignVCenter)
        self.creator_button = QPushButton(tr("Change…"))
        self.creator_button.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.creator_button.clicked.connect(self.creatorChangeRequested.emit)
        who.addWidget(self.creator_button, 0)
        who.addWidget(HelpTip(tr_kw(CREATOR_HELP), tr("Publishing as:")), 0)
        layout.addLayout(who)

        # Where things are kept. This was a line for the library alone,
        # whose default is under AppData, which Explorer hides; it named
        # one of several folders alike in name, and none of the others.
        shelf = QHBoxLayout()
        shelf.setSpacing(8)
        self.folders_note = QLabel(tr(
            "Where your loadouts, exports, backups and the mods library are "
            "on this PC."
        ))
        self.folders_note.setWordWrap(True)
        shelf.addWidget(self.folders_note, 1, Qt.AlignmentFlag.AlignVCenter)
        self.folders_button = QPushButton(tr("Studio folders…"))
        self.folders_button.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.folders_button.clicked.connect(self.foldersRequested.emit)
        shelf.addWidget(self.folders_button, 0)
        layout.addLayout(shelf)

        # The ones you had open before, so coming back to a piece of work does
        # not mean remembering where you filed it.
        # Translators: heads the list of loadouts opened recently.
        self.recent_label = QLabel(tr("Recent loadouts:"))
        self.recent_label.setObjectName("SubHeading")
        layout.addWidget(self.recent_label)
        # A list of files to open, one of which you pick -- which is what these
        # are. As links they read as somewhere to navigate to, and a row of
        # them did not look like a set of alternatives at all.
        self.recent_list = _OneClickList()
        self.recent_list.setObjectName("Recents")
        self.recent_label.setBuddy(self.recent_list)
        self.recent_list.setFrameShape(QFrame.Shape.NoFrame)
        self.recent_list.setSelectionMode(
            QListWidget.SelectionMode.SingleSelection
        )
        self.recent_list.setCursor(Qt.CursorShape.PointingHandCursor)
        self.recent_list.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        # Nor down: it is sized to the rows it holds, so there is never more
        # of it to scroll to.
        self.recent_list.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        # One click opens: there is one thing to do with a loadout you had
        # open before, and asking for two would be asking twice.  Activated
        # is Enter on the row picked with the keyboard: the list takes no
        # double-click, which would open a second time.
        self.recent_list.itemClicked.connect(self._open_recent)
        self.recent_list.itemActivated.connect(self._open_recent)
        # One click opens, which leaves nowhere to ask the other two things
        # anybody wants of a file on a list: where is it, and what is it
        # called on disk.  A name here is a shortened one -- the suffix is
        # taken off and the folder is only in the tooltip.
        self.recent_list.setContextMenuPolicy(
            Qt.ContextMenuPolicy.CustomContextMenu
        )
        self.recent_list.customContextMenuRequested.connect(self._recent_menu)
        layout.addWidget(self.recent_list)
        return card

    def _build_truth(self) -> StepCard:
        """What the game will load right now, beside what the loadout says.

        Three things claim to be "your mods" -- the loadout, the folders, and
        what the game switches on -- and after a collection or a second folder
        read they drift.  This is the grid that compares them, computed from
        disk: what the loadout says of each installed mod, down the side,
        against what the disk says, across the top.  The diagonal is
        agreement; the three cells off it are the mismatches, and each row
        carries the button that fixes its own.  Hidden until there is a
        loadout and a mods folder to compare.
        """
        card = StepCard(4, tr("What the game will load"))
        card.setVisible(False)
        layout = card.body_layout

        # Which loadout the game loads, first: the grid below means "what
        # making this live would change" only once you know what is live.
        self.truth_live = QLabel("")
        self.truth_live.setWordWrap(True)
        layout.addWidget(self.truth_live)
        # And, for the live one, what the game's own loader said it loaded the
        # last time it started: the check that turns the grid from a claim
        # into an observation.
        self.truth_log = QLabel("")
        self.truth_log.setWordWrap(True)
        self.truth_log.setVisible(False)
        layout.addWidget(self.truth_log)
        # What the mods Play would leave loading say about each other, and the
        # GUIDs they take twice: a sentence each and a button to the list. On
        # a big loadout the lists themselves were longer than the rest of the
        # card together.
        self.truth_needs, self.truth_needs_row = self._warning_row(
            tr("Lists what each of these mods says of another, a row each."),
            self.needsRequested,
        )
        layout.addWidget(self.truth_needs_row)
        self.truth_repeats, self.truth_repeats_row = self._warning_row(
            tr("Opens “Mods reaching for the same thing”, which lists each GUID taken twice."),
            self.repeatsRequested,
        )
        layout.addWidget(self.truth_repeats_row)

        grid = QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(18)
        grid.setVerticalSpacing(6)
        right = Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        # Translators: the column heads and row heads of a grid counting the
        # installed mods: whether the game loads each, and whether this
        # loadout has it switched on.
        for column, text in ((1, tr("Game loads")), (2, tr("Game does not load"))):
            head = QLabel(text)
            head.setObjectName("StepHint")
            head.setAlignment(right)
            grid.addWidget(head, 0, column)
        # (row, column) -> the count, keyed the way core.truth names the cells.
        self.truth_cells = {}
        rows = (
            ("on", tr("This loadout has on")),
            ("off", tr("This loadout has off")),
            ("none", tr("Not in this loadout")),
        )
        for index, (key, text) in enumerate(rows, start=1):
            grid.addWidget(QLabel(text), index, 0)
            for column, side in ((1, "loads"), (2, "off")):
                cell = QLabel("0")
                cell.setAlignment(right)
                # Its own height, centred in the row: a cell that is wrong
                # has a glyph, drawn at the top, and stretched to the
                # button's row its text centred a line's worth below it.
                grid.addWidget(cell, index, column, Qt.AlignmentFlag.AlignVCenter)
                self.truth_cells[key, side] = cell
        # The one way the loadout comes to say what the disk does. The other
        # way -- the disk coming to say what the loadout does -- used to be a
        # button a row, and is now Play and Make this live, which do all of it
        # at once: a row switched on its own was a mods folder matching
        # nothing anybody asked for.
        # Named as Game > Match this loadout to the mods folder is, which it
        # is: "Add to loadout" was also the row above's button, which adds
        # the mods the loadout lacks and keeps its switches -- where this
        # takes the game's switches for every mod.
        # Translators: reads the mods folder into the loadout again, with the
        # game's switches, so the loadout says what the game loads.
        self.bring_in_button = QPushButton(tr("Match the mods folder…"))
        self.bring_in_button.setToolTip(tr(
            "Reads the mods folder in again, with the game's switches. Your "
            "own edits stay."
        ))
        self.bring_in_button.clicked.connect(self.bringInRequested.emit)
        grid.addWidget(self.bring_in_button, 3, 3, Qt.AlignmentFlag.AlignLeft)
        grid.setColumnStretch(3, 1)
        layout.addLayout(grid)

        self.truth_cache = QLabel("")
        self.truth_cache.setObjectName("StepHint")
        self.truth_cache.setWordWrap(True)
        self.truth_cache.setVisible(False)
        layout.addWidget(self.truth_cache)
        self.truth_loose = QLabel("")
        self.truth_loose.setWordWrap(True)
        self.truth_loose.setObjectName("StepHint")
        self.truth_loose.setVisible(False)
        layout.addWidget(self.truth_loose)

        # The third axis -- who manages each installed mod -- as a checklist:
        # computed from disk like the grid, a row a place with its count and
        # the one button that empties it, each gone at zero, and one line
        # when every row is. The end state is that the mods folders hold
        # links into the library and nothing else.
        #
        # Before any of it, the mods the loadout does not mention: a mod put
        # into the mods folder by hand was a 1 in the grid and nothing else,
        # and the way to read it in came only after converting it. Named
        # while they are few, so the count is a mod.
        self.strangers_said = QLabel("")
        self.strangers_said.setWordWrap(True)
        # The sentence beside it names them, so the button says "these": it
        # said "Add to loadout…", one word from the button that reads the
        # whole folder in again with the game's switches.
        # Translators: adds the mods the sentence beside it names to the
        # loadout, keeping the loadout's own switches for the rest.
        self.strangers_button = QPushButton(tr("Add these mods…"))
        self.strangers_button.setToolTip(tr(
            "Reads them into this loadout. Its other mods keep their switches."
        ))
        self.strangers_button.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.strangers_button.clicked.connect(self.addStrangersRequested.emit)
        line = QHBoxLayout()
        line.setContentsMargins(0, 0, 0, 0)
        line.addWidget(self.strangers_said, 1)
        line.addWidget(self.strangers_button, 0, Qt.AlignmentFlag.AlignVCenter)
        self.strangers_row = QWidget()
        self.strangers_row.setLayout(line)
        layout.addWidget(self.strangers_row)

        # Then the conversion as a whole: every mod outside the library, in
        # one row with one button. It was only the two rows under it, a place
        # each and worded for the place, and nothing on the card said that
        # converting the mods folder was a step there was to take.
        self.convert_said = QLabel("")
        self.convert_said.setWordWrap(True)
        # It writes outside the loadout, so it shows where before it does.
        self.convert_where = QLabel("")
        self.convert_where.setObjectName("StepHint")
        self.convert_where.setWordWrap(True)
        said = QVBoxLayout()
        said.setContentsMargins(0, 0, 0, 0)
        said.setSpacing(4)
        said.addWidget(self.convert_said)
        said.addWidget(self.convert_where)
        self.convert_button = QPushButton(tr("Convert to library…"))
        self.convert_button.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.convert_button.clicked.connect(lambda: self.convertRequested.emit(""))
        line = QHBoxLayout()
        line.setContentsMargins(0, 0, 0, 0)
        line.addLayout(said, 1)
        line.addWidget(self.convert_button, 0, Qt.AlignmentFlag.AlignVCenter)
        self.convert_row = QWidget()
        self.convert_row.setLayout(line)
        layout.addWidget(self.convert_row)

        self.checklist = {}
        rows = (
            ("cache", tr("Copy to library…"),
             tr("Copies mods from the game's mod browser into the library "
                "and links them in.")),
            ("yours", tr("Move to library…"),
             tr("Moves your mod folders into the library and leaves a link. "
                "Shows a survey first.")),
            ("subscribed", tr("Unsubscribe in game…"),
             tr("Shows which mods the game's mod browser still keeps copies of.")),
        )
        for key, action, tip in rows:
            line = QHBoxLayout()
            said = QLabel("")
            said.setObjectName("StepHint")
            said.setWordWrap(True)
            line.addWidget(said, 1)
            button = QPushButton(action)
            button.setToolTip(tip)
            button.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
            if key == "subscribed":
                button.clicked.connect(self.unsubscribeRequested.emit)
            else:
                button.clicked.connect(lambda _c=False, k=key: self.convertRequested.emit(k))
            line.addWidget(button, 0, Qt.AlignmentFlag.AlignVCenter)
            holder = QWidget()
            holder.setLayout(line)
            line.setContentsMargins(0, 0, 0, 0)
            layout.addWidget(holder)
            self.checklist[key] = (holder, said, button)
        self.checklist_done = QLabel(tr("Everything the game loads comes from the Studio's library."))
        self.checklist_done.setObjectName("StepHint")
        layout.addWidget(self.checklist_done)

        row = FlowLayout(spacing=8)
        # Translators: makes this loadout the one the game loads, without
        # starting the game.
        self.make_live_button = QPushButton(tr("Make this live…"))
        self.make_live_button.setToolTip(tr(
            "Does everything Play does, without starting the game."
        ))
        self.make_live_button.clicked.connect(self.makeLiveRequested.emit)
        row.addWidget(self.make_live_button)
        # It reads the folders from disk again. "Check again" was also the
        # Export tab's button, which asks the game's loader about one mod.
        self.truth_refresh = QPushButton(tr("Re-read the mods folder"))
        self.truth_refresh.setToolTip(tr(
            "Looks at the mods folder again, for a change made outside the Studio."
        ))
        self.truth_refresh.clicked.connect(self.truthRefreshRequested.emit)
        row.addWidget(self.truth_refresh)
        # The two other questions about what the game will load, from the
        # Mods and Check menus: on the card where that is the subject, since
        # nobody looked for them in a menu they had no reason to open.
        self.updates_button = QPushButton(tr("Check mod.io for updates…"))
        self.updates_button.setToolTip(tr(
            "Asks mod.io whether any loaded mod has a newer file."
        ))
        self.updates_button.clicked.connect(self.updatesRequested.emit)
        row.addWidget(self.updates_button)
        self.loadout_check_button = QPushButton(tr("Check the whole loadout…"))
        self.loadout_check_button.setToolTip(tr(
            "Runs every loaded mod through the game's loader. Large loadouts "
            "take over a minute."
        ))
        self.loadout_check_button.clicked.connect(self.loadoutCheckRequested.emit)
        row.addWidget(self.loadout_check_button)
        layout.addLayout(row)
        return card

    @staticmethod
    def _warning_row(tip: str, signal):
        """A sentence of the card's with the button that lists what it counts.

        The sentence is centred in the row at its own height: stretched to
        the button's, a line of it centred and left its glyph, drawn at the
        top, standing above it."""
        said = QLabel("")
        said.setObjectName("StepHint")
        said.setWordWrap(True)
        button = QPushButton(tr("List them…"))
        button.setToolTip(tip)
        button.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        button.clicked.connect(signal.emit)
        line = QHBoxLayout()
        line.setContentsMargins(0, 0, 0, 0)
        line.addWidget(said, 1, Qt.AlignmentFlag.AlignVCenter)
        line.addWidget(button, 0, Qt.AlignmentFlag.AlignVCenter)
        row = QWidget()
        row.setLayout(line)
        row.setVisible(False)
        return said, row

    def show_truth_card(self) -> None:
        """Bring the fourth card into view, for whatever has just named it."""
        self._scroll.ensureWidgetVisible(self.truth_step)

    def set_truth(self, found, library_dir=None) -> None:
        """Show ``found`` (a :class:`core.truth.Truth`), or nothing for None.

        ``library_dir`` is where the library is, when the caller knows; left
        out, it is where it was last said to be."""
        if library_dir is not None:
            self._library_dir = str(library_dir)
        self.truth_step.setVisible(found is not None)
        if found is None:
            return
        cells = {
            ("on", "loads"): found.in_loadout,
            ("on", "off"): found.wanted_but_off,
            ("off", "loads"): found.off_loads,
            ("off", "off"): found.off_off,
            ("none", "loads"): found.none_loads,
            ("none", "off"): found.none_off,
        }
        for key, mods in cells.items():
            cell = self.truth_cells[key]
            extra = {
                ("on", "off"): found.unbuilt_on, ("off", "off"): found.unbuilt_off,
            }.get(key, [])
            cell.setText(str(len(mods) + len(extra)))
            # A mismatch with something in it is the thing to look at; the
            # diagonal, and an empty mismatch, are not.  Orange only on the
            # live loadout, where it is drift: on any other it is what Play
            # would change, which is not a fault.
            wrong = bool(mods) and key in (("on", "off"), ("off", "loads"), ("none", "loads"))
            theme.tint(cell, "warn" if wrong and found.drift else "", strong=wrong)
            names = sorted([mod.name for mod in mods] + list(extra))
            if len(names) > 20:
                # Translators: the last line of a list of twenty mod names.
                names[20:] = [trn("and %(n)s more", "and %(n)s more", len(names) - 20)]
            cell.setToolTip("\n".join(names))
        head, detail = found.describe_live()
        head, detail = html.escape(head), html.escape(detail)
        show_rich(  # i18n: keep - markup around core's sentences
            self.truth_live,
            "<b>%s</b><br>%s" % (head, detail) if detail else "<b>%s</b>" % head,
        )
        checked = found.log_check
        if checked is not None:
            self.truth_log.setText(checked.describe())
            theme.tint(self.truth_log, "warn" if checked.fault else "text-dim")
        elif found.is_live:
            self.truth_log.setText(tr(
                "The game has not started since this was made live. After it "
                "starts, the loader's log is checked here."
            ))
            theme.tint(self.truth_log, "text-dim")
        self.truth_log.setObjectName("StepHint")
        self.truth_log.setVisible(found.is_live)
        for said, row, text in (
            (self.truth_needs, self.truth_needs_row, found.describe_needs()),
            (self.truth_repeats, self.truth_repeats_row, found.describe_repeats()),
        ):
            said.setText(text)
            # Orange on the live loadout, where it is what the game loads now;
            # on any other it is what Play would bring, worth knowing first.
            theme.tint(said, "warn" if found.is_live else "")
            row.setVisible(bool(text))
        cache = found.describe_cache()
        self.truth_cache.setText(cache)
        self.truth_cache.setVisible(bool(cache))
        inert = found.describe_inert()
        self.truth_loose.setText(inert)
        self.truth_loose.setVisible(bool(inert))
        # The row for the mods the loadout does not mention reads them in
        # and names them; this is for what is left, a mod it has off that loads.
        self.bring_in_button.setVisible(bool(found.others) and not found.strangers)
        self.make_live_button.setVisible(not found.is_live or not found.matches)
        outside = self._show_checklist(found)
        text, state = found.status()
        if outside and found.matches:
            # Matching says the loadout and the game agree. It does not say
            # the Studio can switch all of it, and the header was the one
            # place a glance looked for whether a step was left.
            # Translators: the Play card's header. %(status)s is "Matches",
            # and the count is of mods.
            text = trn("%(status)s, %(n)s outside the library",
                       "%(status)s, %(n)s outside the library",
                       outside, status=text)
        self.truth_step.set_status(text, state)
        self._rehold()

    def _show_checklist(self, found) -> int:
        """The rows under the grid. How many mods are outside the library."""
        from ..core import convert

        left = list(getattr(found, "left_in_cache", ()) or ())
        survey = convert.survey(found)
        # Every row converting would do something to is on the card, as it is
        # in the menu. Counted by the place of the copy the game loads, a mod
        # the library holds with a second copy in the mods folder was counted
        # nowhere, and the card said all was done.
        places = (convert.CACHE, convert.YOURS)
        waiting = [row.name for row in survey.to_do if row.where in places]
        second = [row.name for row in survey.to_do if row.where not in places]
        counts = {
            "cache": len([row for row in survey.of(convert.CACHE) if row.to_do]),
            "yours": len([row for row in survey.of(convert.YOURS) if row.to_do]),
            "subscribed": len(left),
        }
        texts = {
            "cache": lambda n: trn(
                "%(n)s installed by the game's mod browser",
                "%(n)s installed by the game's mod browser", n,
            ),
            "yours": lambda n: trn("%(n)s in your folders", "%(n)s in your folders", n),
            "subscribed": lambda n: trn(
                "%(n)s copy the game's mod browser keeps of mods the library has",
                "%(n)s copies the game's mod browser keeps of mods the library has", n,
            ),
        }
        # A place's own row narrows the survey to it, which is a choice only
        # while both places hold mods: with one, the row above is the same click.
        both = all(counts[key] for key in places)
        for key, (holder, said, _button) in self.checklist.items():
            count = counts[key]
            holder.setVisible(bool(count) and (both or key not in places))
            said.setText(texts[key](count))
        outside = len(waiting)
        self.convert_row.setVisible(bool(survey.to_do))
        said = []
        if outside and outside <= self.NAMED:
            said.append(trn(
                "%(n)s of %(total)s mods is outside the library: %(names)s",
                "%(n)s of %(total)s mods are outside the library: %(names)s",
                outside, total=number(len(survey.rows)), names=", ".join(sorted(waiting)),
            ))
        elif outside:
            said.append(trn(
                "%(n)s of %(total)s mods is outside the library",
                "%(n)s of %(total)s mods are outside the library",
                outside, total=number(len(survey.rows)),
            ))
        # A mod that loads from the library, or is Ubisoft's, is not outside
        # it: what is left of it to convert is a copy the game does not load.
        if second and len(second) <= self.NAMED:
            said.append(trn(
                "%(n)s mod has a second copy in your folders that the game does not load: %(names)s",
                "%(n)s mods have a second copy in your folders that the game does not load: %(names)s",
                len(second), names=", ".join(sorted(second)),
            ))
        elif second:
            said.append(trn(
                "%(n)s mod has a second copy in your folders that the game does not load",
                "%(n)s mods have a second copy in your folders that the game does not load",
                len(second),
            ))
        show_rich(self.convert_said, "<b>%s</b>" % "<br>".join(html.escape(one) for one in said))  # i18n: keep - markup around the sentences
        if outside:
            self.convert_where.setText(tr(
                "Shows a survey first, then puts them in %s and leaves links in "
                "the mods folder.", wrappable_path(self._library_dir),
            ))
        else:
            self.convert_where.setText(tr(
                "Shows a survey first, then sets the second copies aside. It deletes nothing."
            ))
        # Said only while it is so: a working copy with .git and a mod of
        # Ubisoft's are left where they are, and the game loads them from there.
        loads = [row for row in survey.rows if row.kept.loads]
        self.checklist_done.setVisible(
            not survey.to_do and not left and bool(found.mods)
            and all(row.where == convert.LIBRARY for row in loads)
        )
        self._show_strangers(found)
        return outside

    # How many mods a row names before it only counts them.
    NAMED = 3

    def _show_strangers(self, found) -> None:
        # Every mod the loadout does not mention, the library's among them.
        # Those had a row of their own with the same button, for the ones no
        # loadout at all mentions: two rows and two buttons for one thing to
        # do, told apart by where a mod's folder is.
        strangers = found.strangers
        count = len(strangers)
        self.strangers_row.setVisible(bool(count))
        names = ", ".join(sorted(mod.name for mod in strangers))
        if count <= self.NAMED:
            said = trn(
                "%(n)s mod in the mods folder is not in this loadout: %(names)s",
                "%(n)s mods in the mods folder are not in this loadout: %(names)s",
                count, names=names,
            )
        else:
            said = trn(
                "%(n)s mod in the mods folder is not in this loadout",
                "%(n)s mods in the mods folder are not in this loadout", count,
            )
        show_rich(self.strangers_said, "<b>%s</b>" % html.escape(said))  # i18n: keep - markup around the sentence

    def _build_ways(self) -> StepCard:
        """The three jobs this window does, and the first move for each."""
        card = StepCard(2, tr("What are you here to do?"))
        layout = card.body_layout

        card.set_help(tr(
            "All three ways do the same editing. They differ in which other "
            "mods are loaded beside your work. Every menu entry stays "
            "available, and you can switch at any time."
        ))

        self.ways = {}
        for key, heading, blurb, help_text, action, second, third in WAYS:
            way = WayCard(
                key, tr(heading), tr(blurb), tr(help_text), tr(action),
                tr(second) if second else "", tr(third) if third else "",
            )
            way.chosen.connect(self.aimChosen.emit)
            way.openedExisting.connect(
                self.collectionLoadoutRequested.emit if key == AIM_MANAGE
                else self.ownModRequested.emit
            )
            way.thirdChosen.connect(self.libraryLoadoutRequested.emit)
            way.continued.connect(self.continueRequested.emit)
            layout.addWidget(way)
            self.ways[key] = way
        return card

    # -- layout ---------------------------------------------------------------
    def _reflow(self, force: bool = False) -> None:
        """One column or two, for the width there is.

        Worked out from the room the page actually has rather than from the
        screen: a window half the width of a big monitor is the same problem as
        a laptop.
        """
        px = theme.FONT_PX
        # The page's own margins, and room for the scrollbar whether or not it
        # is showing -- so its coming and going cannot re-flow the page.
        available = self.width() - 32 - self.style().pixelMetric(
            self.style().PixelMetric.PM_ScrollBarExtent
        )
        half = (min(WIDE_EMS * px, available) - self._split.spacing()) // 2
        # Two columns only where each fits in half: a column held narrower than
        # its own minimum would overlap what is in it.
        wide = available >= SPLIT_EMS * px and all(
            column.minimumSizeHint().width() <= half
            for column in (self._setup, self._choice)
        )
        # An exact width, not a maximum. Qt works out how tall wrapped text is
        # from the width a layout *would* give it, and with only a cap to go on
        # it reckoned the page wider than the cap allowed -- so the second
        # column's paragraphs were given the height of a shorter wrap than
        # they got, and lost their last lines.
        width = max(560, min((WIDE_EMS if wide else PAGE_EMS) * px, available))
        self._body.setFixedWidth(width)
        self._intro.setMaximumWidth(PAGE_EMS * px)
        # Side by side, each column gets exactly half. Left to the layout, the
        # setting-up column's wider minimum took more than its share, while
        # the height of the row was worked out for an even split -- so the
        # other column's paragraphs were wrapped narrower than they had been
        # measured for, and lost their last lines.
        for column in (self._setup, self._choice):
            if wide:
                column.setFixedWidth((width - self._split.spacing()) // 2)
            else:
                column.setMinimumWidth(0)
                column.setMaximumWidth(16777215)
        if wide == self._wide and not force:
            return
        self._wide = wide
        self._split.setDirection(
            QBoxLayout.Direction.LeftToRight if wide else QBoxLayout.Direction.TopToBottom
        )

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._reflow()

    def changeEvent(self, event) -> None:
        # Everything here is measured in body text, so a new text size
        # re-measures it.
        super().changeEvent(event)
        if event.type() == QEvent.Type.FontChange and hasattr(self, "ways_step"):
            self._reflow(force=True)

    def _open_recent(self, item) -> None:
        where = item.data(Qt.ItemDataRole.UserRole)
        if where:
            self.recentChosen.emit(where)

    def _recent_menu(self, point) -> None:
        item = self.recent_list.itemAt(point)
        if item is None:
            return
        self.recent_list.setCurrentItem(item)
        menu = self.menu_for(item)
        menu.exec(self.recent_list.viewport().mapToGlobal(point))
        menu.deleteLater()

    def menu_for(self, item) -> QMenu:
        """What can be done to a loadout on the Lately list.

        Opening it is a click, and always was.  The other two are the ones a
        list of files is expected to answer and this one could not: showing
        where it is, for somebody about to copy or back one up, and the path
        itself, which the row deliberately does not show.
        """
        where = str(item.data(Qt.ItemDataRole.UserRole) or "")
        menu = QMenu(self)
        menu.addAction(tr("Open this loadout"), lambda: self._open_recent(item))
        menu.addSeparator()
        menu.addAction(
            tr("Show in folder"),
            lambda: open_on_disk(Path(where).parent),
        )
        menu.addAction(
            tr("Copy path"),
            lambda: QGuiApplication.clipboard().setText(where),
        )
        return menu

    def _rehold(self) -> None:
        """New text in a wrapped label changes how tall it needs to be, and
        nothing else re-measures it -- its width has not moved."""
        for keeper in self._wrapped:
            keeper.apply()

    def set_games(self, rows) -> None:
        """One row per game: what has been read for it, and the way in.

        ``rows`` are ``{key, label, detail, loaded, can_use, action}``.  The
        one in use says so; one whose files are still current offers to open
        them, which is what switching games is; the rest offer to read.
        """
        while self.games_row.count():
            item = self.games_row.takeAt(0)
            widget = item.widget()
            if widget is not None:
                # Out of the layout is not out of the card: deleteLater runs on
                # the next turn of the event loop, and until it does the widget
                # is still a child of this box -- still drawn, still found by
                # findChildren. Re-listing the games left the previous rows on
                # screen with the new ones underneath, so switching games
                # looked as though nothing about the buttons had changed.
                widget.setParent(None)
                widget.deleteLater()
        before = self.import_button
        for row in rows:
            game = self._game_row(row)
            self.games_row.addWidget(game)
            before = tab_after(before, game)
        self.games_box.setVisible(bool(rows))

        # The card's own button is the first-run action. Once a game has been
        # read, the rows carry the actions that matter and this one is the
        # quiet way to a *different* installation -- highlighted, it read as
        # the thing to do next even with files already loaded.
        known = any(row.get("loaded") or row.get("can_use") for row in rows)
        self.import_button.setObjectName("" if known else "Primary")
        self.import_button.setText(
            tr("Read other game files…") if known else tr("Load from game files…")
        )
        _repolish(self.import_button)
        self._rehold()

    def _game_row(self, row) -> QWidget:
        holder = QFrame()
        holder.setObjectName("WelcomeCard")
        line = QHBoxLayout(holder)
        line.setContentsMargins(12, 8, 12, 8)
        line.setSpacing(10)

        name = rich_label("<b>%s</b>" % html.escape(row["label"]))
        line.addWidget(name, 0, Qt.AlignmentFlag.AlignVCenter)
        detail = QLabel(row.get("detail", ""))
        detail.setObjectName("StepHint")
        detail.setWordWrap(True)
        line.addWidget(detail, 1)
        if row.get("loaded"):
            # Translators: marks the game whose files are loaded now.
            mark = QLabel(tr("in use"))
            mark.setObjectName("SubHeading")
            line.addWidget(mark, 0, Qt.AlignmentFlag.AlignVCenter)

        button = QPushButton(row.get("action") or tr("Read from game files…"))
        button.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        if row.get("can_use") and not row.get("loaded"):
            button.setObjectName("Primary")
        key = row["key"]
        if row.get("can_use"):
            button.clicked.connect(lambda _c=False, k=key: self.gameChosen.emit(k))
        else:
            button.clicked.connect(
                lambda _c=False, k=key: self.readGameRequested.emit(k)
            )
        line.addWidget(button, 0, Qt.AlignmentFlag.AlignVCenter)
        return holder

    def set_creator(self, name: str) -> None:
        """Who exported mods will say wrote them."""
        self._creator = (name or "").strip()
        self.creator_label.setText(self._creator or tr("not set"))
        theme.tint(self.creator_label, "text" if self._creator else "warn")
        self._show_loadout_status()

    def _show_loadout_status(self) -> None:
        """The loadout's header line: which loadout, and whose it is.

        Both, because the card folds away once a loadout is settled and these
        are the two things about it worth having on screen after that.
        """
        text, state = self._loadout_status
        if self._creator and self._loadout_settled:
            # Translators: a loadout's name, and who you publish its mods as.
            text = tr("%(loadout)s  ·  as %(creator)s", loadout=text, creator=self._creator)
        self.loadout_step.set_status(text, state)

    def _toggle_loadout(self) -> None:
        self._loadout_open = not self._loadout_open
        self._show_loadout()

    def _show_loadout(self) -> None:
        open_ = not self._loadout_settled or self._loadout_open
        self.loadout_step.set_open(open_)
        self.loadout_toggle.setVisible(self._loadout_settled)
        self.loadout_toggle.setText(tr("Hide") if open_ else tr("Change…"))

    def _toggle_files(self) -> None:
        self._files_open = not self._files_open
        self._show_files()

    def _show_files(self) -> None:
        loaded = self._loaded_path is not None
        open_ = not loaded or self._files_open
        self.files_step.set_open(open_)
        self.files_toggle.setVisible(loaded)
        self.files_toggle.setText(tr("Hide") if open_ else tr("Change…"))

    # -- state --------------------------------------------------------------
    def set_loadout(self, path, on_disk: bool, recent=(), changes: int = 0,
                    game: str = "", live: str = "",
                    live_profile: str = "", beside: bool = False,
                    mods: int = 0, held: bool = False) -> None:
        """Say which loadout is open, and offer the ones that were before.

        ``held``, with no ``path``, is a loadout holding something with no
        file yet -- mods read from the File menu with no card chosen: it is a
        new loadout, and only one holding nothing is no loadout.

        ``changes`` is how many were made here, and ``beside`` that mods are
        read in beside them: the changes list holds theirs too, so the count
        says whose it is, as the title bar does.

        ``path`` with ``on_disk`` false is a loadout whose home is settled and
        whose file is not written yet -- which is every new loadout until its
        first change.  That is worth saying plainly rather than calling it
        unsaved: nothing is missing.  There is not always nothing in it,
        though: a loadout, or the patch of a downloaded mod, is named after
        its mods are read in.  So ``mods`` is how many are loaded, as the
        title bar counts them, and only a loadout with none is called empty.
        """
        self.rename_button.setEnabled(path is not None)
        self.fork_button.setVisible(path is not None)
        self.play_button.setVisible(path is not None)
        if path is None and held:
            # Mods read from the File menu with no card chosen: a loadout
            # with no file yet, as the title calls it (D8).
            self.loadout_label.setFullText(tr("New loadout"))
            self.loadout_label.setToolTip("")
            theme.tint(self.loadout_label, "text")
            self.loadout_detail.setText(tr(
                "It has no file yet. Save it to keep it, or pick a way of "
                "working to start afresh."
            ))
            self._loadout_status = (tr("New loadout"), "")
        elif path is None:
            self.loadout_label.setFullText(tr("No loadout yet"))
            self.loadout_label.setToolTip("")
            theme.tint(self.loadout_label, "text-dim")
            self.loadout_detail.setText(tr(
                "Pick a way of working to start one, or open one you have."
            ))
            self._loadout_status = (tr("None yet"), "")
        else:
            path = Path(path)
            name = loadout_name(path)
            self.loadout_label.setFullText(name)
            self.loadout_label.setToolTip(str(path))
            theme.tint(self.loadout_label, "text")
            self._loadout_status = (name if on_disk else tr("%s (new)", name), "ok")
            where = wrappable_path(path.parent)
            if on_disk and beside:
                self.loadout_detail.setText(trn(
                    "%(n)s change of yours · saved in %(where)s",
                    "%(n)s changes of yours · saved in %(where)s",
                    changes, where=where,
                ))
            elif on_disk:
                self.loadout_detail.setText(trn(
                    "%(n)s change · saved in %(where)s",
                    "%(n)s changes · saved in %(where)s",
                    changes, where=where,
                ))
            elif mods:
                self.loadout_detail.setText(trn(
                    "%(n)s mod loaded. It is written to %(where)s when you make a change.",
                    "%(n)s mods loaded. It is written to %(where)s when you make a change.",
                    mods, where=where,
                ))
            else:
                self.loadout_detail.setText(tr(
                    "Empty. It is written to %s when you make a change.", where
                ))

        # What the list is of, said outright: it is the loadouts for the game
        # whose files are loaded, and a list with two entries beside a folder
        # holding a hundred needs to say why.
        self.recent_label.setText(
            tr("Recent loadouts for %s:", game) if game else tr("Recent loadouts:")
        )
        self.recent_list.clear()
        # Named as File ▸ Open recent names them, less the one named above.
        recent = recent_rows(recent, but=path, live=live, live_profile=live_profile)
        self.recent_label.setVisible(bool(recent))
        self.recent_list.setVisible(bool(recent))
        recent = recent[:LoadoutFiles.RECENT_SHOWN]
        for row, (name, other) in enumerate(recent):
            item = QListWidgetItem(name)
            item.setData(Qt.ItemDataRole.UserRole, other)
            item.setToolTip(other)
            self.recent_list.addItem(item)
            if not live or os.path.normcase(other) != os.path.normcase(str(live)):
                continue
            # The loadout the game loads is on top whatever was opened since,
            # and nothing said why: so it wears Play's sign, in bold, with a
            # rule between it and the rest.
            item.setIcon(theme.glyph("live"))
            self.recent_list.ensurePolished()
            font = QFont(self.recent_list.font())
            font.setBold(True)
            item.setFont(font)
            if row < len(recent) - 1:
                rule = QListWidgetItem()
                rule.setFlags(Qt.ItemFlag.NoItemFlags)
                rule.setSizeHint(QSize(0, 9))
                self.recent_list.addItem(rule)
                line = QFrame()
                line.setObjectName("RecentsRule")
                line.setFixedHeight(1)
                self.recent_list.setItemWidget(rule, line)
        if recent:
            # Exactly as tall as it has rows: a list box with empty space under
            # it would read as somewhere things could be dropped.  The frame is
            # the stylesheet's border and padding, both sides of it -- guessing
            # it short by two pixels clipped the last row, and Qt answered that
            # with a scroll bar for a list that never holds more than it shows.
            self.recent_list.ensurePolished()
            rows = sum(
                self.recent_list.sizeHintForRow(row)
                for row in range(self.recent_list.count())
            )
            self.recent_list.setFixedHeight(rows + 2 * self.recent_list.frameWidth())
        # A loadout that has just been settled folds the card away; one being
        # looked for leaves it open.
        was = self._loadout_settled
        self._loadout_settled = path is not None
        if self._loadout_settled != was:
            self._loadout_open = False
        self._show_loadout()
        self._show_loadout_status()
        self._rehold()

    # The list's own rows for the three acts, told from profiles by their data.
    _NEW, _RENAME, _DELETE, _SEPARATE = "\x00new", "\x00rename", "\x00delete", "\x00separate"

    def set_profiles(self, names, chosen: str, live=None, shown=None,
                     visible: bool = True) -> None:
        """Offer the loadout's profiles, ``chosen`` picked out and ``live``
        -- the one the game loads, if it is one of these -- marked.
        ``shown`` gives each a name to show where it has none of its own."""
        box = self.profile_box
        box.blockSignals(True)
        box.clear()
        names = list(names)
        for name in names:
            label = name or (shown or {}).get(name, "") or tr("This loadout")
            if live is not None and name == live:
                # Translators: marks the profile the game loads now.
                label = tr("%s (live)", label)
            box.addItem(label, name)
        box.insertSeparator(box.count())
        box.addItem(tr("New profile…"), self._NEW)
        box.addItem(tr("Rename this profile…"), self._RENAME)
        box.addItem(tr("Delete this profile…"), self._DELETE)
        # The last one cannot go: offered, it would only say so.
        model = box.model()
        model.item(box.count() - 1).setEnabled(len(names) > 1)
        box.insertSeparator(box.count())
        box.addItem(tr("Copy to new loadout…"), self._SEPARATE)
        # Every row in view: scrolled to the chosen profile, a long list
        # would hide the acts again.
        box.setMaxVisibleItems(box.count())
        self._profile_chosen = chosen
        box.setCurrentIndex(names.index(chosen) if chosen in names else 0)
        box.blockSignals(False)
        box.setVisible(visible)

    def show_game_open(self, said: str) -> None:
        """Play greyed while the game is open, with ``said`` saying why;
        "" is the game not open."""
        self.play_button.setEnabled(not said)
        self.play_button.setToolTip(said or self._play_tip)

    def _profile_picked(self, index: int) -> None:
        data = self.profile_box.itemData(index)
        acts = {
            self._NEW: self.newProfileRequested,
            self._RENAME: self.renameProfileRequested,
            self._DELETE: self.deleteProfileRequested,
            self._SEPARATE: self.separateCopyRequested,
        }
        if data in acts:
            # Back to the chosen one first: an act is not a profile, and the
            # window redraws the list once it has done it, or not.
            self._show_chosen_profile()
            acts[data].emit()
        elif data is not None and data != self._profile_chosen:
            self.profileChosen.emit(data)

    def _show_chosen_profile(self) -> None:
        box = self.profile_box
        box.blockSignals(True)
        for position in range(box.count()):
            if box.itemData(position) == self._profile_chosen:
                box.setCurrentIndex(position)
                break
        box.blockSignals(False)

    def set_aim(self, aim: str, status=None, ready: bool = True,
                pending: str = "") -> None:
        """Mark which way set the loadout up, and say what each suggests next.

        ``status`` is ``{aim: line}``; a way with nothing to add says nothing
        rather than repeating its own description back at the user.  The
        lines are read off what the loadout holds, so they stay true however
        far the work has moved from the card that started it.

        ``pending`` is the way just clicked, while its questions are still
        open.  It is the one picked out, and the earlier one is not: somebody
        who clicked a card is looking at the answer they just gave, and a
        highlight left on the old one reads as the click not having taken.
        """
        status = status or {}
        for key, card in self.ways.items():
            card.set_state(
                aim == key and not pending,
                status.get(key, "" if ready else tr("Load the game files first.")),
                ready,
                pending=pending == key,
            )
        if pending:
            self.ways_step.set_status(tr("Setting up…"))
        elif aim in AIM_NAMES:
            self.ways_step.set_status(tr(AIM_NAMES[aim]), "ok")
        else:
            self.ways_step.set_status(tr("Not chosen yet"), "todo" if ready else "")
        self._rehold()

    def set_state(self, assets_path, asset_count: int, texts_path,
                  text_count: int, game: str = "", language: str = "") -> None:
        """What is loaded: the game's file, and the in-game names.

        Whether names are loaded is ``text_count``, the index's own answer,
        and ``language`` is the one it keeps for them.  ``texts_path`` is only
        where they were read from, when that is known: an index that comes
        from the cache has its names and no file waiting, and going by the
        path the card said "None" with Clear off while the status bar counted
        the names.
        """
        from ..core import games, texts

        self.game_label.setText(games.get(game).label if assets_path else "—")
        theme.tint(self.game_label, "text" if assets_path else "text-dim")
        # Loaded, this is a step you have finished: the card keeps its button --
        # a game patch, another language or a different Anno all bring you back
        # here -- but stops taking up the room of an explanation you have read.
        show_rich(
            self.import_blurb,
            tr("<b>Load from your game installation</b><br>"
               "Reads the current assets and localisation from the archives "
               "again. Use it after a game patch, or for another language.")
            if assets_path
            else tr("<b>Load from your game installation</b><br>"
                    "Finds the archive that holds the current files, and "
                    "extracts the assets and localisation from it.")
        )
        self.manual.setVisible(not assets_path)
        # The welcome is for an empty page. With game files loaded, the room
        # belongs to the work.
        self._heading.setVisible(not assets_path)
        self._intro.setVisible(not assets_path)

        if assets_path:
            path = Path(assets_path)
            self.assets_label.setFullText(str(path))
            self.assets_label.setToolTip(str(path))
            theme.tint(self.assets_label, "text")
            size = path.stat().st_size / 1e6 if path.exists() else 0
            self.assets_detail.setText(trn(
                "%(n)s asset indexed   ·   %(size)s MB",
                "%(n)s assets indexed   ·   %(size)s MB",
                asset_count, size=number(size),
            ))
        else:
            self.assets_label.setFullText(tr("No game files loaded."))
            self.assets_label.setToolTip("")
            theme.tint(self.assets_label, "text-dim")
            self.assets_detail.setText(tr("Load from your game, or choose an extracted file."))

        if text_count:
            where = (
                str(Path(texts_path)) if texts_path
                else tr("Loaded earlier and kept with the game files.")
            )
            self.texts_label.setFullText(where)
            self.texts_label.setToolTip(where)
            theme.tint(self.texts_label, "text")
            self.texts_detail.setText(trn(
                "%(n)s in-game name loaded", "%(n)s in-game names loaded", text_count
            ))
            self.texts_clear.setEnabled(True)
        else:
            self.texts_label.setFullText(tr("None. Assets show their internal names."))
            self.texts_label.setToolTip("")
            theme.tint(self.texts_label, "text-dim")
            self.texts_detail.setText(tr("Optional, but makes searching far easier."))
            self.texts_clear.setEnabled(False)

        # The header says it all once it is done: which game, how much of it,
        # and in which language -- facts set side by side, not a sentence.
        if assets_path:
            bits = [
                games.get(game).label,
                trn("%(n)s asset", "%(n)s assets", asset_count),
            ]
            language = language or (texts.language_of(texts_path) if texts_path else "")
            if text_count and language:
                # Translators: the language is the English name of the game's
                # texts file, e.g. "German" from texts_german.xml.
                bits.append(tr("%s names", language.replace("_", " ").title()))
            elif text_count:
                bits.append(tr("in-game names"))
            self.files_step.set_status("  ·  ".join(bits), "ok")
        else:
            self.files_step.set_status(tr("Not loaded yet"), "todo")

        # Fold away on arrival of a different set of files -- including the
        # first -- and stay however somebody left it otherwise.
        loaded = str(assets_path) if assets_path else None
        if loaded != self._loaded_path:
            self._loaded_path = loaded
            self._files_open = False
        self._show_files()
        self._rehold()
