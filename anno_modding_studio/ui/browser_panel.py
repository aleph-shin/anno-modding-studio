"""Finding an asset: a search field that drops a result list down over the editor.

Search used to be its own step, which meant every edit began with a tab switch
and ended with another one.  It is really a way of *addressing* the thing you are
about to edit, so it lives at the top of the editor instead: type, pick, and the
list folds away again, leaving the whole window for the asset.

The result list *floats over* the editor rather than sitting in its layout.  That
matters for more than tidiness: an inline list pushes the tree down while it is
open and lets it spring back when it closes, so the row under your cursor is not
the row you were about to click.  Overlaying it means the asset below never moves,
which in turn makes it safe to fold the list away the moment you touch the tree.

It opens when you *reach for it* -- click the box, type in it, press Find -- and
not merely because you arrived at the editor.  A panel that is already down when
the tab opens, and that vanishes the first time you click anything else, reads as
something that went wrong rather than as the search field's own list.
"""

from __future__ import annotations

from PySide6.QtCore import QEvent, QPoint, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QGuiApplication
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMenu,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..core.i18n import number, tr, trn
from . import theme
from .widgets import fit_columns, single_line, sortable

RESULT_LIMIT = 400

# How much of the editor the open list is allowed to cover, and the floor it
# never goes under.  While the list is open it *is* what you are looking at, so
# on a full-screen window it should read as a page of results rather than as the
# eight rows a fixed 300px left room for; on a short window the floor keeps it
# usable, and it folds away the moment a row is picked either way.
RESULTS_SHARE = 0.7
RESULTS_MIN = 300

# Ways of landing in the search box that mean "I am searching now".  Anything
# else -- a programmatic setFocus, the window coming back to the front -- leaves
# the list shut until the user types or clicks.
_REACHED_FOR = (
    Qt.FocusReason.MouseFocusReason,
    Qt.FocusReason.TabFocusReason,
    Qt.FocusReason.BacktabFocusReason,
    Qt.FocusReason.ShortcutFocusReason,
)
# The two of them Qt also gives when nobody pressed Tab: a control that goes
# grey or is hidden with the keyboard on it has it handed to the next one
# along, as if Tab had been pressed.  Which it was is known once the focus has
# finished moving -- see AssetSearchBar._on_focus_moved.
_TABBED = (Qt.FocusReason.TabFocusReason, Qt.FocusReason.BacktabFocusReason)

# How Name, Internal name and Template share what GUID, sized to its
# contents, leaves. A header can only stretch one column, and Name swallowed
# every extra pixel of a wide window while the other two -- the longer, more
# often cut short -- stayed as they were built.
RESULT_SHARES = {0: 0.34, 1: 0.33, 3: 0.33}


class AssetSearchBar(QWidget):
    """Search + progressive filtering over every asset in the game files."""

    assetChosen = Signal(str)          # GUID
    referencesRequested = Signal(str)  # GUID, from the row's own menu

    def __init__(self, parent=None):
        super().__init__(parent)
        self.index = None
        self.current_guid = None
        # What the Template list was last built from, or None for nothing.
        self._offered = None
        # What the results float over, once attach_overlay names it.
        self._host = None
        # Whether the list is open and every click in the Studio is watched,
        # so one outside it shuts it.
        self._watching = False
        # Whether the focus is on its way into the search box by Tab, or by
        # what Qt calls Tab: settled when it has arrived.
        self._tabbed_in = False
        # Set while the search is emptied without anybody typing.
        self._forgetting = False
        # Whether the list is out of date, having been asked to refresh while
        # shut: it is refreshed when it next opens rather than after every
        # edit nobody is searching through.
        self._stale = False
        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(180)
        self._debounce.timeout.connect(self.refresh)
        self._build_ui()
        self.set_index(None)

    # -- construction -------------------------------------------------------
    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(6)

        row = QHBoxLayout()
        row.setSpacing(8)
        self.search_box = QLineEdit()
        self.search_box.setObjectName("Search")
        self.search_box.setPlaceholderText(
            tr("Search assets…  (e.g. “lumberjack”, “1010340”, “residence tier01”)")
        )
        self.search_box.setAccessibleName(tr("Search assets"))
        self.search_box.setClearButtonEnabled(True)
        self.search_box.textChanged.connect(self._on_typed)
        self.search_box.returnPressed.connect(self._focus_results)
        self.search_box.installEventFilter(self)
        row.addWidget(self.search_box, 1)

        template_caption = QLabel(tr("&Template:"))
        row.addWidget(template_caption)
        # A real install has ~1,000 templates. A plain combo still finds one in
        # a few keystrokes -- Qt's own type-ahead jumps to the first item whose
        # name starts with what you type -- and the main search box above
        # already does full substring matching against template names too, so
        # this is a narrowing filter rather than the only way to search by
        # template. Being a plain combo, like "Target game", matters more:
        # it never grows a text-editing caret the way a typeable one would.
        self.template_combo = QComboBox()
        template_caption.setBuddy(self.template_combo)
        self.template_combo.setMinimumWidth(230)
        self.template_combo.currentIndexChanged.connect(self._on_typed)
        row.addWidget(self.template_combo)

        # Translators: a button beside the asset search box; the verb.
        self.clear_button = QPushButton(tr("Clear"))
        self.clear_button.setToolTip(tr("Clear the search and close the result list."))
        self.clear_button.clicked.connect(self._clear_and_close)
        row.addWidget(self.clear_button)
        outer.addLayout(row)

        # -- the drop-down itself
        self.results = QFrame()
        self.results.setObjectName("WelcomeCard")
        results_layout = QVBoxLayout(self.results)
        results_layout.setContentsMargins(8, 8, 8, 8)
        results_layout.setSpacing(6)

        self.hint = QLabel(tr(
            "Search by in-game name, internal name, GUID, or template, then narrow "
            "down with the template filter. Pick a row to start editing it."
        ))
        self.hint.setObjectName("StepHint")
        self.hint.setWordWrap(True)
        results_layout.addWidget(self.hint)

        self.table = QTableWidget(0, 4)
        self.table.setTabKeyNavigation(False)
        self.table.setAccessibleName(tr("Search results"))
        self.table.setHorizontalHeaderLabels(
            [tr("Name"), tr("Internal name"), "GUID", tr("Template")]  # i18n: keep - GUID
        )
        self.table.verticalHeader().setVisible(False)
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        single_line(self.table)
        self.table.setMinimumHeight(160)
        # No maximum: the frame is given a height by _position_results and the
        # table takes whatever is left after the hint and the count.  A cap here
        # would hold the rows at eight however much room the window had.
        # One click opens it: this is a picker, not a list to browse and confirm.
        self.table.itemClicked.connect(self._open_selected)
        self.table.itemActivated.connect(self._open_selected)
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._context_menu)
        self.table.installEventFilter(self)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Interactive)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Interactive)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Interactive)
        fit_columns(self.table, shares=RESULT_SHARES)
        results_layout.addWidget(self.table, 1)
        self.sorting = sortable(self.table)

        self.status_label = QLabel("")
        self.status_label.setObjectName("SubHeading")
        results_layout.addWidget(self.status_label)

        # Deliberately not added to the layout: see the module docstring.
        self.results.setVisible(False)

    # -- open / close -------------------------------------------------------
    def attach_overlay(self, host) -> None:
        """Float the result list above ``host`` instead of inside a layout."""
        self._host = host
        self.results.setParent(host)
        self.results.hide()
        host.installEventFilter(self)
        # A drop-down shuts when the keyboard goes somewhere else.
        QApplication.instance().focusChanged.connect(self._on_focus_moved)

    def _outside(self, widget) -> bool:
        """Whether ``widget`` is neither the search bar nor its list, nor a
        menu or drop-down opened from one of them."""
        if widget.window().windowType() == Qt.WindowType.Popup:
            return False
        while widget is not None:
            if widget is self or widget is self.results:
                return False
            widget = widget.parentWidget()
        return True

    def _on_focus_moved(self, old, now) -> None:
        tabbed, self._tabbed_in = self._tabbed_in, False
        if tabbed and now is self.search_box:
            # Tab is asking for the list. A button that greyed out or went
            # away under the keyboard is not: Revert asset does, with nothing
            # left to revert, and the list dropped over the asset just reverted.
            if old is None or (old.isEnabled() and old.isVisibleTo(old.window())):
                self.open_results()
            return
        if now is not None and self.results.isVisible() and self._outside(now):
            self.close_results()

    def _position_results(self) -> None:
        host = self._host
        if host is None:
            return
        top_left = self.mapTo(host, QPoint(0, self.height() + 4))
        room = host.height() - top_left.y() - 12
        # A share of the window rather than a fixed height: the list is the
        # whole point while it is open, and on a maximised window a fixed height
        # spent most of the screen showing the editor underneath it instead.
        wanted = max(RESULTS_MIN, int(host.height() * RESULTS_SHARE))
        self.results.setGeometry(
            top_left.x(), top_left.y(), self.width(), max(200, min(wanted, room))
        )
        self.results.raise_()

    def eventFilter(self, watched, event):
        """Open when reached for, close on Escape -- the manners of a drop-down."""
        kind = event.type()
        if kind == QEvent.Type.MouseButtonPress and self._watching:
            # A click anywhere else in the Studio shuts the list, as it shuts
            # any drop-down. The click still does what it was for.
            if isinstance(watched, QWidget) and self._outside(watched):
                self.close_results()
            return False
        if kind == QEvent.Type.Resize and watched is self._host:
            if self.results.isVisible():
                self._position_results()
        elif kind == QEvent.Type.FocusIn and watched is self.search_box:
            # Only when the user put the caret here themselves.  Focus also
            # arrives programmatically -- opening the editor tab parks it here
            # so typing goes somewhere useful -- and dropping the list then
            # showed a panel nobody asked for, which the next click dismissed.
            if event.reason() in _TABBED and self._host is not None:
                # Tab, or Qt moving on from a control that can no longer hold
                # the keyboard: told apart by where it came from, which is
                # said only once it has arrived.
                self._tabbed_in = True
            elif event.reason() in _REACHED_FOR:
                self.open_results()
        elif kind == QEvent.Type.MouseButtonPress and watched is self.search_box:
            # A click in the box is the plainest way of asking for the list,
            # and arriving at the editor has already parked the caret here --
            # so by the time it is clicked there is no focus change left to
            # notice.  The click itself is what has to answer for it.
            self.open_results()
        elif kind == QEvent.Type.KeyPress and event.key() == Qt.Key.Key_Escape:
            if self.results.isVisible():
                self.close_results()
                self.search_box.setFocus()
                return True
        elif kind == QEvent.Type.KeyPress and watched is self.search_box:
            if event.key() == Qt.Key.Key_Down:
                self._focus_results()
                return True
        return super().eventFilter(watched, event)

    def open_results(self) -> None:
        if self.index is None:
            return
        self._position_results()
        self.results.setVisible(True)
        self.results.raise_()
        if self._host is not None and not self._watching:
            # Every click in the Studio comes past while the list is open,
            # and none once it is shut.
            self._watching = True
            QApplication.instance().installEventFilter(self)
        if self._stale:
            self.refresh()

    def close_results(self) -> None:
        self.results.setVisible(False)
        if self._watching:
            self._watching = False
            QApplication.instance().removeEventFilter(self)

    def set_current_guid(self, guid) -> None:
        """Remember which asset is open, and keep it highlighted in the list.

        Without this, anything that rebuilds the list -- recording a change does
        -- dropped the highlight, and the only way to close the list again was to
        work out which row you had been editing and click it a second time.
        """
        self.current_guid = str(guid) if guid else None
        self._select_current()

    def _select_current(self) -> None:
        if not self.current_guid:
            return
        for row in range(self.table.rowCount()):
            item = self.table.item(row, 0)
            if item and item.data(Qt.ItemDataRole.UserRole) == self.current_guid:
                self.table.blockSignals(True)
                self.table.selectRow(row)
                self.table.blockSignals(False)
                self.table.scrollToItem(item)
                return

    def _clear_and_close(self) -> None:
        self.clear_filters()
        self.close_results()

    def _on_typed(self) -> None:
        if self._forgetting:
            return
        self.open_results()
        self._debounce.start()

    # -- index wiring -------------------------------------------------------
    def set_index(self, index) -> None:
        # A pending keystroke must not land on the index we are replacing: the
        # old one is closed the moment a new base file is adopted.
        self._debounce.stop()
        self.index = index
        self._offered = None
        loaded = index is not None
        for widget in (
            self.search_box,
            self.template_combo,
            self.clear_button,
            self.table,
        ):
            widget.setEnabled(loaded)
        self._reload_filters("")
        self.refresh()
        if not loaded:
            self.close_results()

    def _reload_filters(self, query: str) -> None:
        """Rebuild the Template list from what ``query`` actually matches.

        With ~1,000 templates and nothing typed yet, the list is every
        template in the game -- about as useful as a plain combo's type-ahead
        can be. Once there is a query, the templates worth offering are the
        handful that show up among *those* results, so the list narrows down
        alongside the results themselves rather than staying a wall of
        choices. Asked every time, the same query or not: what a query
        matches moves under it when texts are read or cleared, or the mod
        gains an asset. The list is emptied and filled again only when the
        answer is another one. A template *selection* change also lands here
        (it re-runs the debounced refresh), and so does every edit made with
        the list open: those get the answer the index holds, and the list is
        left as it is.
        """
        counts = (
            list(self.index.template_counts_for(query))
            if self.index is not None else []
        )
        if counts == self._offered:
            return
        self._offered = counts
        current = self.template_combo.currentData()
        self.template_combo.blockSignals(True)
        self.template_combo.clear()
        self.template_combo.addItem(tr("All templates"), "")
        for name, count in counts:
            self.template_combo.addItem(
                tr("%(template)s  (%(n)s)", template=name, n=number(count)), name
            )
        # Keep the current choice if it is still on offer; otherwise falling
        # back to "All templates" beats silently keeping a now-absent filter.
        if current:
            found = self.template_combo.findData(current)
            self.template_combo.setCurrentIndex(found if found >= 0 else 0)
        self.template_combo.blockSignals(False)

    def clear_filters(self) -> None:
        self.search_box.clear()
        self.template_combo.setCurrentIndex(0)
        self.refresh()

    def forget_search(self) -> None:
        """Nothing searched for, as when the Studio started.

        For a loadout that is not the one the search was made in: after File
        > New loadout the box still held the GUID typed for the last one.
        Nobody is typing, so the list stays shut and the keyboard where it
        is.
        """
        self._debounce.stop()
        self._forgetting = True
        try:
            self.search_box.clear()
            self.template_combo.setCurrentIndex(0)
        finally:
            self._forgetting = False
        self.refresh()

    # -- results ------------------------------------------------------------
    def refresh(self) -> None:
        # A floating list that is shut waits until it opens: an edit asks for
        # a refresh, and searching every asset again for a list nobody is
        # looking at was a second of every edit on a large loadout.
        if self._host is not None and self.results.isHidden():
            self._stale = True
            return
        self._stale = False
        self.table.setRowCount(0)
        if self.index is None:
            self.status_label.setText(
                tr("Load a base asset file on the Start tab first.")
            )
            return

        query = self.search_box.text().strip()
        self._reload_filters(query)
        template = self.template_combo.currentData() or ""

        rows = self.index.search(query, template=template, limit=RESULT_LIMIT)
        total = self.index.total_matches(query, template=template)

        self.table.setRowCount(len(rows))
        mono = theme.mono_font()
        for r, row in enumerate(rows):
            display = QTableWidgetItem(row.display or row.name or tr("(unnamed)"))
            display.setData(Qt.ItemDataRole.UserRole, row.guid)
            self.table.setItem(r, 0, display)

            internal = QTableWidgetItem(row.name)
            internal.setForeground(QColor(theme.TEXT_DIM))
            self.table.setItem(r, 1, internal)

            guid_item = QTableWidgetItem(row.guid)
            guid_item.setFont(mono)
            self.table.setItem(r, 2, guid_item)

            self.table.setItem(r, 3, QTableWidgetItem(row.template))

        # Repopulating drops the selection; the asset being edited stays marked.
        self._select_current()
        # GUID (ResizeToContents) may have just changed width with new rows;
        # the other three columns split whatever it left behind.
        self.table.column_fit.fit()

        if total == 0:
            self.status_label.setText(
                tr("No assets match. Try fewer words, or clear the filters.")
            )
        elif total > len(rows):
            self.status_label.setText(trn(
                "Showing the best of %(total)s matches. Refine your search "
                "to see the rest.",
                "Showing the %(n)s best of %(total)s matches. Refine your "
                "search to see the rest.",
                len(rows), total=number(total),
            ))
        else:
            self.status_label.setText(trn("%(n)s match", "%(n)s matches", total))

    def _focus_results(self) -> None:
        if self.table.rowCount():
            self.open_results()
            self.table.setFocus()
            if not self.table.selectedItems():
                self.table.selectRow(0)

    def selected_guid(self):
        rows = (
            self.table.selectionModel().selectedRows()
            if self.table.selectionModel()
            else []
        )
        if not rows:
            return None
        item = self.table.item(rows[0].row(), 0)
        return item.data(Qt.ItemDataRole.UserRole) if item else None

    def _open_selected(self, item=None) -> None:
        guid = None
        if item is not None:
            source = self.table.item(item.row(), 0)
            guid = source.data(Qt.ItemDataRole.UserRole) if source else None
        guid = guid or self.selected_guid()
        if guid:
            self.close_results()
            self.assetChosen.emit(guid)

    def focus_search(self, show_results: bool = True) -> None:
        """Put the caret in the search box.

        ``show_results`` is what tells the two callers apart.  Pressing Find is
        asking for the list, so it comes down with the caret.  Merely arriving
        at the editor is not: the caret goes here so typing lands somewhere
        useful, and the list waits until there is something to show.
        """
        if show_results:
            self.open_results()
        self.search_box.setFocus()
        self.search_box.selectAll()

    # -- what a result row offers -------------------------------------------
    def _context_menu(self, point) -> None:
        item = self.table.itemAt(point)
        if item is None:
            return
        # A left-click here opens the asset; a right-click must not, so the row
        # is only selected -- which is what makes the menu about a row the user
        # can see they picked.
        self.table.selectRow(item.row())
        menu = self.menu_for(item.row())
        menu.exec(self.table.viewport().mapToGlobal(point))
        menu.deleteLater()

    def menu_for(self, row: int) -> QMenu:
        """What can be done to one search result, as a menu nobody has shown yet.

        Search had one gesture and one only: click a row, and the asset opens.
        Which meant that the commonest thing anybody wants out of this list --
        the GUID, to paste into a wiki page, a bug report or another mod's XML
        -- could only be got by opening the asset and finding it again in the
        field editor.  The number is right there in the third column; this is
        how it gets to the clipboard.

        Narrowing to a template is here for the same reason: the combo above
        does it, and the row in front of you is where you noticed you wanted
        it.
        """
        guid = ""
        first = self.table.item(row, 0)
        if first is not None:
            guid = str(first.data(Qt.ItemDataRole.UserRole) or "")
        internal = self.table.item(row, 1)
        template = self.table.item(row, 3)

        menu = QMenu(self)
        menu.setToolTipsVisible(True)
        menu.addAction(
            tr("Open in asset editor"), lambda: self._open_selected_row(row)
        )
        refs = menu.addAction(
            tr("References for this asset…"),
            lambda: self.referencesRequested.emit(guid),
        )
        refs.setEnabled(bool(guid))
        menu.addSeparator()
        menu.addAction(
            tr("Copy GUID"), lambda: QGuiApplication.clipboard().setText(guid)
        )
        copy_name = menu.addAction(
            tr("Copy internal name"),
            lambda: QGuiApplication.clipboard().setText(
                internal.text() if internal is not None else ""
            ),
        )
        copy_name.setEnabled(bool(internal is not None and internal.text()))
        name = template.text() if template is not None else ""
        if name:
            menu.addSeparator()
            narrow = menu.addAction(
                tr("Show only %s", name), lambda: self._filter_to_template(name)
            )
            narrow.setToolTip(tr(
                "Sets the Template filter above to %s, leaving what you "
                "typed alone.", name
            ))
        return menu

    def _open_selected_row(self, row: int) -> None:
        item = self.table.item(row, 0)
        if item is not None:
            self._open_selected(item)

    def _filter_to_template(self, name: str) -> None:
        found = self.template_combo.findData(name)
        if found >= 0:
            self.template_combo.setCurrentIndex(found)

