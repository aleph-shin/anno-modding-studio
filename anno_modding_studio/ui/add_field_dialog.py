"""Pick a field to add to a node, from what the base game actually does there.

The candidate list is observed from the shipping ``assets.xml``: every child tag
that appears under this path somewhere in the game, with how many assets use it
and a few real values. That grounding matters -- everything offered is something
the game demonstrably accepts at this exact position, and the counts let the user
tell a mainstream field from an exotic one.

A field already on the asset is shown too, not hidden -- demoted to the bottom
and greyed out, rather than silently missing from the list the way something the
game never uses here at all would be. The two are otherwise indistinguishable
from outside the tree: a field present but holding no values yet looks exactly
like one that was never added, until "Show empty" is switched on.
"""

from __future__ import annotations

import html

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
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
from .help_tip import HelpTip
from .widgets import (
    ElideItemDelegate,
    enter_activates,
    fit_columns,
    hold_lines,
    rich_label,
    show_rich,
    sort_as,
    sortable,
    tab_as_laid_out,
)

TAG_ROLE = Qt.ItemDataRole.UserRole
INFO_ROLE = Qt.ItemDataRole.UserRole + 1

# A container is added empty and filled afterwards, so it behaves differently
# from a value -- but it is just as selectable, and dimming it said otherwise.
CONTAINER_MARK = "▸ "     # a small right-pointing triangle, as in the tree


class AddFieldDialog(QDialog):
    """Choose one field to add under ``path``.  Result in ``chosen_tag``/``value``.

    ``fields`` is every child tag the schema has observed at ``path`` -- not
    just the ones missing from this asset. ``present`` names the ones this
    asset already has; those still show up (demoted to the bottom, greyed),
    picking one just explains itself rather than offering to add it.
    """

    def __init__(self, fields, path: str, present=None, parent=None):
        super().__init__(parent)
        self.fields = list(fields)
        self.path = path
        self._present = set(present or ())
        self.chosen_tag = None
        self.value = ""
        self.is_container = False

        # Translators: a field is one XML tag of a game asset, with its value.
        self.setWindowTitle(tr("Add a field"))
        self.setMinimumSize(720, 520)

        self._filter_timer = QTimer(self)
        self._filter_timer.setSingleShot(True)
        self._filter_timer.setInterval(150)
        self._filter_timer.timeout.connect(self._refresh)

        self._build_ui()
        self._refresh()

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 14, 16, 12)
        outer.setSpacing(8)


        heading = rich_label(tr("Add a field to <code>%s</code>", html.escape(self.path)))
        heading.setObjectName("Heading")
        head = QHBoxLayout()
        head.addWidget(heading, 1)
        head.addWidget(HelpTip(tr(
            "These are the child tags the base game uses at this path. Used "
            "by is the number of base game assets that have the tag there. A "
            "high count means a common field, and a low count an unusual "
            "one. Rows marked %s hold other fields and no value. They are "
            "added empty, and you fill them in afterwards.",
            CONTAINER_MARK.strip()
        ), tr("Add a field")))
        outer.addLayout(head)

        self.search_box = QLineEdit()
        self.search_box.setPlaceholderText(tr("Filter fields…"))
        self.search_box.setClearButtonEnabled(True)
        self.search_box.textChanged.connect(self._filter_timer.start)
        outer.addWidget(self.search_box)

        self.table = QTableWidget(0, 3)
        self.table.setTabKeyNavigation(False)
        self.table.setHorizontalHeaderLabels([
            tr("Field", context="column"), tr("Used by", context="column"),
            tr("Values in the base game", context="column"),
        ])
        self.table.verticalHeader().setVisible(False)
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        enter_activates(self.table)
        self.table.activated.connect(self._accept_selection)
        self.table.itemSelectionChanged.connect(self._on_selected)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Interactive)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.table.setColumnWidth(0, 250)
        fit_columns(self.table)
        # QStyleSheetStyle's own eliding can collapse a long field name to a
        # bare ellipsis instead of keeping as much of it as fits -- computing
        # it here, against the column's real width, is what actually keeps
        # "ResearchCenterMissingElectr…" instead of just "…".
        self.table.setItemDelegateForColumn(0, ElideItemDelegate(self.table))
        outer.addWidget(self.table, 1)
        self.sorting = sortable(self.table)

        value_row = QHBoxLayout()
        value_caption = QLabel(tr("&Value:"))
        value_row.addWidget(value_caption)
        self.value_edit = QLineEdit()
        value_caption.setBuddy(self.value_edit)
        theme.monospace(self.value_edit)
        self.value_edit.setPlaceholderText(tr("(select a field)"))
        self.value_edit.returnPressed.connect(self._accept_selection)
        value_row.addWidget(self.value_edit, 1)
        outer.addLayout(value_row)

        self.note = QLabel("")
        self.note.setObjectName("SubHeading")
        self.note.setWordWrap(True)
        # Fixed height, sized for two lines: the "already on this asset"
        # message routinely wraps to two lines while the one-line messages
        # don't, and without this the table above it would grow and shrink
        # to fit -- moving the very row you just clicked on out from under you.
        hold_lines(self.note, 2, 4)
        outer.addWidget(self.note)

        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        self.add_button = self.buttons.addButton(
            tr("Add field"), QDialogButtonBox.ButtonRole.AcceptRole
        )
        self.add_button.setObjectName("Primary")
        self.add_button.setEnabled(False)
        self.add_button.clicked.connect(self._accept_selection)
        self.buttons.rejected.connect(self.reject)
        tab_as_laid_out(self.buttons)
        outer.addWidget(self.buttons)

    def _refresh(self) -> None:
        needle = self.search_box.text().strip().lower()

        def matches(field) -> bool:
            return not needle or needle in field.tag.lower()

        # Addable fields first, in their usual most-common-first order; fields
        # this asset already has come after, as a group, rather than being
        # missing from the list the way a field the game never uses here
        # would be -- the two are otherwise indistinguishable from outside
        # the tree.
        addable = [f for f in self.fields if f.tag not in self._present and matches(f)]
        already = [f for f in self.fields if f.tag in self._present and matches(f)]
        rows = addable + already
        mono = theme.mono_font()

        self.table.setRowCount(len(rows))
        for r, field in enumerate(rows):
            is_present = field.tag in self._present
            # The marker sits in front of the name so containers line up as a
            # group, while the name itself stays at full strength: dimming it
            # made a perfectly selectable row look disabled.
            label = (CONTAINER_MARK if field.is_container else "   ") + field.tag
            tag_item = QTableWidgetItem(label)
            tag_item.setData(TAG_ROLE, field.tag)
            tag_item.setData(INFO_ROLE, field)
            tag_item.setFont(mono)

            if is_present:
                kind = QTableWidgetItem(tr("(already on this asset)"))
                # Translators: "Show empty" is a switch above the asset's tree.
                tag_item.setToolTip(tr(
                    "Already on this asset, but empty. Turn on Show empty to find it."
                ))
            elif field.is_container:
                # Parenthesised like a caption, not a value -- the accent
                # colour this used to have drew the eye more than a mostly
                # informational column should.
                kind = QTableWidgetItem(tr("(holds fields)"))
                tag_item.setToolTip(tr("A container: added empty, then filled in."))
            else:
                kind = QTableWidgetItem(field.sample_text)

            count_item = sort_as(QTableWidgetItem(number(field.count)), field.count)
            if is_present:
                # Grey, not just unmarked: genuinely unselectable-for-adding,
                # not merely a different kind of selectable row (a container is).
                for item in (tag_item, count_item, kind):
                    item.setForeground(QColor(theme.TEXT_DIM))
            self.table.setItem(r, 0, tag_item)
            self.table.setItem(r, 1, count_item)
            self.table.setItem(r, 2, kind)
        # Filled again with as many rows, the sort hears nothing: it is asked (A10-40).
        self.sorting.apply()

        if not rows:
            self.note.setText(tr(
                "No fields match. The suggestions come from what the base "
                "game uses here, so a field that no base game asset uses is "
                "not listed."
            ))
        self._on_selected()

    def _selected_field(self):
        model = self.table.selectionModel()
        rows = model.selectedRows() if model else []
        if not rows:
            return None
        item = self.table.item(rows[0].row(), 0)
        return item.data(INFO_ROLE) if item else None

    def _on_selected(self) -> None:
        field = self._selected_field()
        is_present = field is not None and field.tag in self._present
        self.add_button.setEnabled(field is not None and not is_present)
        if field is None:
            self.value_edit.setPlaceholderText(tr("(select a field)"))
            return
        if is_present:
            self.value_edit.setText("")
            self.value_edit.setEnabled(False)
            self.value_edit.setPlaceholderText(tr("already on this asset"))
            show_rich(self.note, tr(
                "This asset already has <b>%s</b>, so it can't be added "
                "again. If it isn't in the tree, it is empty. Turn on "
                "<b>Show empty</b> to find and edit it.", html.escape(field.tag)
            ))
            return
        if field.is_container:
            self.value_edit.setText("")
            self.value_edit.setEnabled(False)
            self.value_edit.setPlaceholderText(tr("container, added empty"))
            show_rich(self.note, tr(
                "<b>%s</b> holds other fields. It will be added empty. Add "
                "fields inside it afterwards.", html.escape(field.tag)
            ))
        else:
            self.value_edit.setEnabled(True)
            # Translators: placeholder of the box the new field's value is typed in.
            self.value_edit.setPlaceholderText(tr("value"))
            # Left blank on purpose. The base game's values are in the table to
            # read; one typed in here for the user got added without a look.
            self.note.setText(trn(
                "Used by %(n)s base game asset at this path.",
                "Used by %(n)s base game assets at this path.", field.count
            ))

    def _accept_selection(self) -> None:
        field = self._selected_field()
        if field is None or field.tag in self._present:
            # The button is disabled for this case, but double-click and
            # Enter reach here too -- neither should be able to "add" a field
            # that is already there.
            return
        self.chosen_tag = field.tag
        self.is_container = field.is_container
        self.value = "" if field.is_container else self.value_edit.text()
        self.accept()
