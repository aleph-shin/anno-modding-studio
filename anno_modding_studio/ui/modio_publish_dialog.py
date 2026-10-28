"""The three small dialogs publishing needs: the terms, the link, the send.

None of them talks to mod.io itself.  The terms dialog shows what the account
dialog fetched; the link dialog shows the mods the Export tab fetched; the
publish dialog shows the plan the Export tab made and takes a changelog.  Each
answers one yes-or-no, so each is a plain modal and its result is its answer.
"""

from __future__ import annotations

import html
from typing import Callable, List, NamedTuple, Optional

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices, QGuiApplication
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from ..core import modio, publish
from ..core.i18n import number, size_text, tr
from .help_tip import HelpTip
from .modio_brand import branding
from .widgets import (
    least_size,
    rich_label,
    shown_row,
    single_line,
    sortable,
    tab_as_laid_out,
)


class TermsDialog(QDialog):
    """mod.io's terms, with its own button wording, before the first sign-in."""

    def __init__(self, terms: modio.Terms, parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr("mod.io terms"))
        least_size(self, 560, 420)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 14, 16, 12)
        heading = QLabel(tr("Before signing in to mod.io"))
        heading.setObjectName("Heading")
        outer.addWidget(heading)
        # Translators: shown just before mod.io's logo.
        outer.addWidget(branding(tr("The terms of")))
        whose = QLabel(tr(
            "This is mod.io's own wording, shown as mod.io requires before "
            "any sign-in. If you already have a mod.io account with this "
            "email, that is the account you sign in to. A new one is made "
            "only when there is none."
        ))
        whose.setObjectName("StepHint")
        whose.setWordWrap(True)
        outer.addWidget(whose)
        text = QPlainTextEdit(terms.text)
        text.setAccessibleName(self.windowTitle())
        text.setReadOnly(True)
        outer.addWidget(text, 1)
        # The two addresses come from mod.io's answer: escaped into the markup.
        links = rich_label(tr(
            '<a href="%(terms)s">Terms of Use</a> &nbsp;·&nbsp; '
            '<a href="%(privacy)s">Privacy Policy</a>',
            terms=html.escape(terms.terms_url),
            privacy=html.escape(terms.privacy_url),
        ))
        links.setOpenExternalLinks(True)
        links.setTextInteractionFlags(Qt.TextInteractionFlag.TextBrowserInteraction)
        outer.addWidget(links)
        self.buttons = QDialogButtonBox()
        self.agree_button = self.buttons.addButton(terms.agree, QDialogButtonBox.ButtonRole.AcceptRole)
        self.buttons.addButton(terms.disagree, QDialogButtonBox.ButtonRole.RejectRole)
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        outer.addWidget(self.buttons)


class NewMod(NamedTuple):
    """What the link dialog needs to send somebody to mod.io's website to
    create the mod there: the game's *Add mod* page, the mod's name and
    summary, whether mod.io asks for a linked Ubisoft account first, and how
    to list your mods on mod.io again afterwards (None where they could not
    be listed)."""

    page: str
    name: str
    summary: str
    ubisoft: bool
    relist: Callable[[], Optional[List[modio.ModSummary]]]


class LinkDialog(QDialog):
    """Pick which of your mods on mod.io this loadout publishes to, or go
    and create it on mod.io's website and pick it once it is there."""

    def __init__(self, mine: List[modio.ModSummary], current: int = 0, parent=None,
                 new: Optional[NewMod] = None):
        super().__init__(parent)
        self.mine = list(mine)
        self.new = new
        self.chosen: Optional[int] = None
        self.setWindowTitle(tr("Link to a mod on mod.io"))
        least_size(self, 600, 400)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 14, 16, 12)
        # The title says what this is. What picking one does is behind the ?.
        head = QHBoxLayout()
        head.addWidget(branding(), 1)
        head.addWidget(HelpTip(tr(
            "These are your mods on mod.io for this game. Pick the one your "
            "mod is. Publishing then sends each version of it as the mod's "
            "next file. A mod that isn't on mod.io yet is created on the "
            "website first: It's new opens the page for it."
        ), self.windowTitle()))
        outer.addLayout(head)
        self.table = QTableWidget(0, 3)
        self.table.setTabKeyNavigation(False)
        # One line per row, so a name too long for its column is cut where
        # it runs out rather than losing its last word whole.
        single_line(self.table)
        self.table.setHorizontalHeaderLabels((tr("Mod"), tr("Live version"), tr("mod.io id")))
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self._fill(current)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.table.itemSelectionChanged.connect(self._refresh)
        self.table.setAccessibleName(tr("Your mods on mod.io"))
        outer.addWidget(self.table, 1)
        self.sorting = sortable(self.table)
        form = QFormLayout()
        self.id_edit = QLineEdit(str(current) if current else "")
        self.id_edit.setPlaceholderText(tr("or its mod.io id"))
        self.id_edit.textChanged.connect(self._refresh)
        form.addRow(tr("Or by &id"), self.id_edit)
        outer.addLayout(form)
        # The way out for a mod that is not on mod.io yet, which this list
        # can only be without: the website creates it, and the dialog stays
        # open to link it once it is there.
        self.new_note = QLabel("")
        self.new_note.setObjectName("StepHint")
        self.new_note.setWordWrap(True)
        outer.addWidget(self.new_note)
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        # Translators: a button that opens mod.io's website to create a mod
        # that is not on mod.io yet.
        self.create_button = self.buttons.addButton(
            tr("It's &new: create it on mod.io"), QDialogButtonBox.ButtonRole.ActionRole
        )
        self.create_button.clicked.connect(self.create_on_modio)
        # Translators: pressed once the mod has been created on mod.io's
        # website, to list your mods there again.
        self.created_button = self.buttons.addButton(
            tr("I've &created it"), QDialogButtonBox.ButtonRole.ActionRole
        )
        self.created_button.clicked.connect(self.list_again)
        self.created_button.hide()
        self.create_button.setVisible(new is not None)
        if new is not None and new.ubisoft:
            # Said before the page is opened: the website asks for it before
            # anything else, and the page is no help until it has it.
            self.new_note.setText(tr(
                "For Anno 117, mod.io asks for a Ubisoft account linked to your "
                "mod.io account before it lets you create a mod."
            ))
        self.new_note.setVisible(bool(self.new_note.text()))
        self.link_button = self.buttons.addButton(
            tr("&Link", context="verb"), QDialogButtonBox.ButtonRole.AcceptRole
        )
        self.buttons.accepted.connect(self._accept)
        self.buttons.rejected.connect(self.reject)
        tab_as_laid_out(self.buttons)
        outer.addWidget(self.buttons)
        self._refresh()

    def _fill(self, current: int = 0) -> None:
        """The table, from :attr:`mine`, with ``current`` picked."""
        self.table.clearSelection()
        self.table.setRowCount(len(self.mine))
        for row, mod in enumerate(self.mine):
            name = QTableWidgetItem(mod.name if mod.visible else tr("%s  (hidden)", mod.name))
            # Each row knows its mod: a sorted table's rows are not in the
            # list's order.
            name.setData(Qt.ItemDataRole.UserRole, mod.id)
            self.table.setItem(row, 0, name)
            self.table.setItem(row, 1, QTableWidgetItem(mod.version))
            self.table.setItem(row, 2, QTableWidgetItem(str(mod.id)))
            if mod.id == current:
                self.table.selectRow(row)

    def create_on_modio(self) -> None:
        """Open the game's *Add mod* page and copy the summary to paste
        there; then wait for :meth:`list_again`.

        The name and the summary were copied as one, and the page has a box
        for each: both went into its Summary.  The name is a few words to
        type, and the summary is the part worth copying."""
        new = self.new
        if new is None:
            return
        if new.summary:
            QGuiApplication.clipboard().setText(new.summary)
        QDesktopServices.openUrl(QUrl(new.page))
        said = [tr(
            "mod.io's page for a new mod is open in your browser. The mod's "
            "summary is copied, to paste into Summary there. Once it is "
            "created, press I've created it."
        ) if new.summary else tr(
            "mod.io's page for a new mod is open in your browser. Once it is "
            "created, press I've created it."
        )]
        if new.ubisoft:
            said.insert(0, tr(
                "For Anno 117, mod.io asks for a Ubisoft account linked to your "
                "mod.io account before it lets you create a mod."
            ))
        self.new_note.setText(" ".join(said))
        self.new_note.show()
        self.created_button.show()

    def list_again(self) -> None:
        """Your mods on mod.io once more, with the one new since picked."""
        if self.new is None:
            return
        found = self.new.relist()
        if found is None:
            return
        before = {mod.id for mod in self.mine}
        self.mine = list(found)
        fresh = [mod.id for mod in self.mine if mod.id not in before]
        self._fill(fresh[0] if len(fresh) == 1 else 0)
        self.new_note.setText(
            tr("Picked the mod new on mod.io. Link links it.") if len(fresh) == 1 else
            tr("No mod is new on mod.io since the list was made. It can take a "
               "moment to show. Press I've created it again.") if not fresh else
            tr("Several mods are new on mod.io. Pick the one this is.")
        )
        self._refresh()

    def _selected_id(self) -> Optional[int]:
        rows = self.table.selectionModel().selectedRows() if self.table.selectionModel() else []
        if rows:
            item = self.table.item(rows[0].row(), 0)
            if item is not None and item.data(Qt.ItemDataRole.UserRole) is not None:
                return int(item.data(Qt.ItemDataRole.UserRole))
        return publish.mod_id_from(self.id_edit.text())

    def _refresh(self) -> None:
        self.link_button.setEnabled(self._selected_id() is not None)

    def _accept(self) -> None:
        self.chosen = self._selected_id()
        if self.chosen is not None:
            self.accept()


class PublishDialog(QDialog):
    """Exactly what is about to be sent, as whom, and that it goes live at once."""

    def __init__(self, ready: publish.Plan, parent=None, note: str = ""):
        super().__init__(parent)
        self.ready = ready
        self.setWindowTitle(tr("Publish update to mod.io"))
        self.setMinimumWidth(560)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 14, 16, 12)
        heading = QLabel(tr("Publish %(mod)s %(version)s", mod=ready.mod.name,
                            version=ready.version))
        heading.setObjectName("Heading")
        outer.addWidget(heading)
        # What changed since the last release, in a line: the version below
        # is the author's, and this is what it is being sent against.
        self.note = QLabel(note)
        self.note.setObjectName("StepHint")
        self.note.setWordWrap(True)
        outer.addWidget(self.note)
        self.note.setVisible(bool(note))
        # Translators: shown just before mod.io's logo.
        outer.addWidget(branding(tr("Publishing to")))
        form = QFormLayout()
        mod, mod_id = ready.mod.name, str(ready.mod.id)
        shown_row(
            form,
            # Translators: which mod on mod.io the file is sent to.
            tr("To", context="publish to"),
            QLabel(
                tr("%(mod)s (mod.io id %(id)s)", mod=mod, id=mod_id)
                if ready.mod.visible else
                tr("%(mod)s (mod.io id %(id)s), hidden: only you and your team see it",
                   mod=mod, id=mod_id)
            ),
        )
        # Translators: which mod.io account the file is sent as.
        shown_row(form, tr("As", context="publish as"),
                  QLabel(ready.as_user or tr("your mod.io account")))
        shown_row(form, tr("Version"), QLabel(
            tr("%(version)s, replacing %(live)s", version=ready.version,
               live=ready.live_version)
            if ready.live_version else
            tr("%s, replacing no file", ready.version)
        ))
        shown_row(form, tr("File"), QLabel(tr("%(file)s, %(size)s", file=ready.archive.name,
                                              size=size_text(ready.size))))
        # The summary, where the Export tab's is not mod.io's already.
        self.summary_box = None
        if ready.summary:
            self.summary_box = QCheckBox(tr("&Replace mod.io's summary"))
            self.summary_box.setChecked(True)
            said = QLabel(ready.summary)
            said.setWordWrap(True)
            said.setObjectName("StepHint")
            said.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            column = QVBoxLayout()
            column.setContentsMargins(0, 0, 0, 0)
            column.addWidget(self.summary_box)
            column.addWidget(said)
            shown_row(form, tr("Summary"), column)
        elif ready.too_long:
            too_long = QLabel(tr(
                "Not sent: the summary on the Export tab is %(n)s characters, and "
                "mod.io takes %(limit)s at most.",
                n=number(len(ready.too_long)), limit=number(modio.SUMMARY_LIMIT)))
            too_long.setWordWrap(True)
            shown_row(form, tr("Summary"), too_long)
        outer.addLayout(form)
        changelog_caption = QLabel(tr("&Changelog"))
        outer.addWidget(changelog_caption)
        self.changelog = QPlainTextEdit(ready.changelog)
        changelog_caption.setBuddy(self.changelog)
        self.changelog.setPlaceholderText(tr("What changed in this version, for the mod's page."))
        self.changelog.setMaximumHeight(120)
        outer.addWidget(self.changelog)
        warning = QLabel(
            tr("It goes live for every subscriber as soon as it uploads, with no "
               "review and no undo from here.")
            if ready.mod.visible
            else tr("The mod is hidden, so only its team sees the file. It becomes "
                    "the current file at once, with no undo from here.")
        )
        warning.setObjectName("StepHint")
        warning.setWordWrap(True)
        outer.addWidget(warning)
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        self.buttons.addButton(tr("&Publish now"), QDialogButtonBox.ButtonRole.AcceptRole)
        self.buttons.accepted.connect(self._accept)
        self.buttons.rejected.connect(self.reject)
        tab_as_laid_out(self.buttons)
        outer.addWidget(self.buttons)

    def _accept(self) -> None:
        self.ready.changelog = self.changelog.toPlainText().strip()
        if self.summary_box is not None and not self.summary_box.isChecked():
            self.ready.summary = ""
        self.accept()


