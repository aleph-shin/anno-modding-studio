"""Pick an asset for a field that holds a GUID reference.

Reuses the asset editor's search, in a modal.  The template filter starts on whatever the
field currently points at: nothing in the XML says what a field is *allowed* to
reference, but the value already there is a strong, data-driven hint, and the
filter is a normal dropdown the user can widen to "All templates".
"""

from __future__ import annotations

import html

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from ..core.i18n import number, tr, trn
from . import theme
from .widgets import (
    enter_activates,
    fit_columns,
    rich_label,
    single_line,
    sortable,
    tab_as_laid_out,
)

RESULT_LIMIT = 300
GUID_ROLE = Qt.ItemDataRole.UserRole


class AssetPickerDialog(QDialog):
    """Modal asset search.  ``chosen_guid`` holds the result on accept."""

    def __init__(self, index, field_name: str, current_guid: str = "", parent=None):
        super().__init__(parent)
        self.index = index
        self.chosen_guid = None
        self.setWindowTitle(tr("Choose an asset for %s", field_name))
        self.setMinimumSize(820, 520)

        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(180)
        self._debounce.timeout.connect(self.refresh)

        self._build_ui(field_name, current_guid)
        self._preselect(current_guid)
        self.refresh()

    def _build_ui(self, field_name: str, current_guid: str) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 14, 16, 12)
        outer.setSpacing(8)

        current = self.index.asset_row(current_guid) if current_guid else None
        if current and current.template:
            hint = rich_label(tr(
                "<code>%(field)s</code> currently points at <b>%(asset)s</b> "
                "(%(template)s). The filter starts on that template. Widen "
                "it if you need something else.",
                field=html.escape(field_name), asset=html.escape(current.label),
                template=html.escape(current.template),
            ))
        elif current:
            hint = rich_label(tr(
                "<code>%(field)s</code> currently points at <b>%(asset)s</b>, "
                "which has no template, so the list starts on every template.",
                field=html.escape(field_name), asset=html.escape(current.label),
            ))
        else:
            hint = rich_label(tr(
                "Choose the asset that <code>%s</code> should point at.", html.escape(field_name)))
        hint.setObjectName("StepHint")
        hint.setWordWrap(True)
        outer.addWidget(hint)

        self.search_box = QLineEdit()
        self.search_box.setObjectName("Search")
        self.search_box.setPlaceholderText(tr("Search assets…"))
        self.search_box.setClearButtonEnabled(True)
        self.search_box.textChanged.connect(self._debounce.start)
        outer.addWidget(self.search_box)

        row = QHBoxLayout()
        template_caption = QLabel(tr("&Template:"))
        row.addWidget(template_caption)
        self.template_combo = QComboBox()
        template_caption.setBuddy(self.template_combo)
        self.template_combo.setMinimumWidth(280)
        self.template_combo.addItem(tr("All templates"), "")
        for name, count in self.index.template_counts():
            self.template_combo.addItem(
                tr("%(template)s  (%(n)s)", template=name, n=number(count)), name
            )
        self.template_combo.currentIndexChanged.connect(self._debounce.start)
        row.addWidget(self.template_combo)
        row.addStretch(1)
        outer.addLayout(row)

        self.table = QTableWidget(0, 4)
        self.table.setTabKeyNavigation(False)
        # One line per row, so a name too long for its column is cut where
        # it runs out rather than losing its last word whole.
        single_line(self.table)
        self.table.setHorizontalHeaderLabels(
            [tr("Name"), tr("Internal name"), "GUID", tr("Template")]  # i18n: keep - GUID
        )
        self.table.verticalHeader().setVisible(False)
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        enter_activates(self.table)
        self.table.activated.connect(self._accept_selection)
        self.table.itemSelectionChanged.connect(self._sync_buttons)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Interactive)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Interactive)
        self.table.setColumnWidth(1, 200)
        self.table.setColumnWidth(3, 200)
        fit_columns(self.table)
        outer.addWidget(self.table, 1)
        self.sorting = sortable(self.table)

        self.status = QLabel("")
        self.status.setObjectName("SubHeading")
        outer.addWidget(self.status)

        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        self.use_button = self.buttons.addButton(
            tr("Use this asset"), QDialogButtonBox.ButtonRole.AcceptRole
        )
        self.use_button.setObjectName("Primary")
        self.use_button.clicked.connect(self._accept_selection)
        self.buttons.rejected.connect(self.reject)
        tab_as_laid_out(self.buttons)
        outer.addWidget(self.buttons)

    def _preselect(self, current_guid: str) -> None:
        row = self.index.asset_row(current_guid) if current_guid else None
        if row and row.template:
            position = self.template_combo.findData(row.template)
            if position >= 0:
                self.template_combo.blockSignals(True)
                self.template_combo.setCurrentIndex(position)
                self.template_combo.blockSignals(False)

    def refresh(self) -> None:
        query = self.search_box.text().strip()
        template = self.template_combo.currentData() or ""
        rows = self.index.search(query, template=template, limit=RESULT_LIMIT)
        total = self.index.total_matches(query, template=template)

        mono = theme.mono_font()
        self.table.setRowCount(len(rows))
        for r, row in enumerate(rows):
            name = QTableWidgetItem(row.display or row.name or tr("(unnamed)"))
            name.setData(GUID_ROLE, row.guid)
            self.table.setItem(r, 0, name)
            internal = QTableWidgetItem(row.name)
            internal.setForeground(QColor(theme.TEXT_DIM))
            self.table.setItem(r, 1, internal)
            guid_item = QTableWidgetItem(row.guid)
            guid_item.setFont(mono)
            self.table.setItem(r, 2, guid_item)
            self.table.setItem(r, 3, QTableWidgetItem(row.template))
        # Filled again with as many rows, the sort hears nothing: it is asked (A10-40).
        self.sorting.apply()

        if total > len(rows):
            self.status.setText(trn(
                "Showing the best of %(total)s matches.",
                "Showing the %(n)s best of %(total)s matches.",
                len(rows), total=number(total),
            ))
        else:
            self.status.setText(trn("%(n)s match", "%(n)s matches", total))
        self._sync_buttons()

    def selected_guid(self):
        model = self.table.selectionModel()
        rows = model.selectedRows() if model else []
        if not rows:
            return None
        item = self.table.item(rows[0].row(), 0)
        return item.data(GUID_ROLE) if item else None

    def _sync_buttons(self) -> None:
        self.use_button.setEnabled(self.selected_guid() is not None)

    def _accept_selection(self) -> None:
        guid = self.selected_guid()
        if guid:
            self.chosen_guid = guid
            self.accept()
