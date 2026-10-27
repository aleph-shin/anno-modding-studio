"""Who points at this asset, and what it points at.

Anno's cross-references are bare integers with nothing marking them as links,
so the graph drawn here is *inferred* -- from how often each field turns out to
hold a real GUID across the whole shipped file (see ``core/refs.py``).  That
distinction is kept visible rather than smoothed over: established links are
drawn solid, and the ones the evidence does not settle are hidden until asked
for, then drawn dashed with their odds spelled out on hover.

Laid out in columns, like Unreal's reference viewer: what refers to this asset
on the left, the asset itself in the middle, what it refers to on the right,
one column per hop.
"""

from __future__ import annotations

from contextlib import nullcontext
from dataclasses import dataclass

from PySide6.QtCore import QPoint, QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import (
    QAccessible,
    QAccessibleEvent,
    QBrush,
    QColor,
    QFont,
    QFontInfo,
    QFontMetrics,
    QGuiApplication,
    QPainter,
    QPainterPath,
    QPen,
)
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QGraphicsItem,
    QGraphicsScene,
    QGraphicsView,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMenu,
    QPushButton,
    QSpacerItem,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..core.i18n import number, tr, trn
from ..core.keptops import clipped
from . import theme
from .help_tip import HelpTip
from .navigation import AssetTrail, NavButtons
from .widgets import (
    ElideItemDelegate,
    ElidingLabel,
    enter_activates,
    fit_columns,
    sortable,
    tab_as_laid_out,
)

NODE_WIDTH = 216
# A node's two lines of text, as a share of the view's font size.
TITLE_SCALE = 19 / 21
SUBTITLE_SCALE = 16 / 21
NODE_HEIGHT = 48
COLUMN_GAP = 96
ROW_GAP = 12

# An asset like "Money" is referenced by hundreds; drawing all of them turns the
# graph into a wall and tells you nothing. p99 in-degree is 24, so this shows
# essentially every asset in full while keeping the hubs readable.
DEFAULT_PER_COLUMN = 20

# The most of a name a menu entry or a tooltip line carries, of a field's
# path (cut from the middle, keeping the field), and how many links a
# tooltip lists. A tooltip is read at a glance beside the pointer:
# an asset's long name, or a hub's dozen links, made one as wide as the
# screen and as tall as half of it.
NAME_MOST = 40
FIELD_MOST = 50
NODE_TIP_LINKS = 6
EDGE_TIP_LINKS = 3


def short_name(name: str, most: int = NAME_MOST) -> str:
    """``name`` cut to ``most`` characters, the cut marked."""
    name = " ".join(str(name).split())
    return name if len(name) <= most else name[:most - 1].rstrip() + "\u2026"


def _demotion(links) -> tuple:
    """How readily a node gives up its place when a hop overflows.

    Two things make a node worth less than its neighbours: a link the evidence
    does not vouch for, and a link that only names the asset without setting
    anything.  Uncertainty weighs the heavier of the two -- a link that may not
    be a link at all is a worse thing to draw than a real one that happens to
    carry no value.  A node is judged by its strongest link, so a solid
    connection is never dragged down by a doubtful one beside it.
    """
    return min(
        (0 if link.confident else 1, 0 if not link.nominal else 1)
        for link in links
    )


class GraphNode(QGraphicsItem):
    """One asset in the graph."""

    def __init__(
        self, guid: str, title: str, subtitle: str, role: str = "normal", column: int = 0
    ):
        super().__init__()
        self.guid = guid
        self.title = title
        self.subtitle = subtitle
        self.role = role  # "focus" | "normal" | "more"
        self.column = column
        self.setAcceptHoverEvents(True)
        self._hover = False
        # Whether the keyboard is on this node, and what a screen reader is
        # told about it. Both are the view's to set.
        self.keyboard = False
        self.spoken = title
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def boundingRect(self) -> QRectF:
        return QRectF(0, 0, NODE_WIDTH, NODE_HEIGHT)

    def hoverEnterEvent(self, event):
        self._hover = True
        self.update()
        super().hoverEnterEvent(event)

    def hoverLeaveEvent(self, event):
        self._hover = False
        self.update()
        super().hoverLeaveEvent(event)

    def paint(self, painter, option, widget=None) -> None:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        rect = self.boundingRect().adjusted(0.5, 0.5, -0.5, -0.5)

        if self.role == "focus":
            fill, border = QColor(theme.RAISED), QColor(theme.ACCENT_TEXT)
        elif self.role == "more":
            fill, border = QColor(theme.BG), QColor(theme.BORDER)
        else:
            fill, border = QColor(theme.SURFACE), QColor(theme.BORDER)
        if self._hover:
            border = QColor(theme.ACCENT_TEXT)

        path = QPainterPath()
        path.addRoundedRect(rect, 5, 5)
        painter.fillPath(path, QBrush(fill))
        painter.setPen(QPen(border, 2 if self.role == "focus" else 1))
        painter.drawPath(path)

        # Sized from the view's font, a step below body text for the title
        # and two for the line under it.
        body = QFontInfo(painter.font()).pointSizeF()
        title_font = QFont(painter.font())
        title_font.setPointSizeF(body * TITLE_SCALE)
        title_font.setBold(self.role == "focus")
        painter.setFont(title_font)
        painter.setPen(
            QColor(theme.TEXT_DIM if self.role == "more" else theme.TEXT)
        )
        metrics = QFontMetrics(title_font)
        avail = NODE_WIDTH - 20
        painter.drawText(
            QRectF(10, 6, avail, 18),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            metrics.elidedText(self.title, Qt.TextElideMode.ElideRight, int(avail)),
        )

        if self.keyboard:
            # Inside the box, so it shows on the centre node's heavy border
            # too and cannot be clipped by the node's bounds.
            ring = QPainterPath()
            ring.addRoundedRect(rect.adjusted(3, 3, -3, -3), 3, 3)
            painter.setPen(QPen(QColor(theme.FOCUS), 2))
            painter.drawPath(ring)

        if self.subtitle:
            sub_font = QFont(painter.font())
            sub_font.setPointSizeF(body * SUBTITLE_SCALE)
            painter.setFont(sub_font)
            painter.setPen(QColor(theme.TEXT_DIM))
            sub_metrics = QFontMetrics(sub_font)
            painter.drawText(
                QRectF(10, 25, avail, 16),
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                sub_metrics.elidedText(
                    self.subtitle, Qt.TextElideMode.ElideRight, int(avail)
                ),
            )


class ReferenceView(QGraphicsView):
    """The canvas: drag to pan, wheel to zoom."""

    nodeClicked = Signal(str)
    nodeActivated = Signal(str)
    moreClicked = Signal(int)  # the column whose hidden rest was asked for
    backRequested = Signal()
    forwardRequested = Signal()
    menuRequested = Signal(QPoint)  # right-click, in viewport coordinates

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setScene(QGraphicsScene(self))
        self.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        self.setTransformationAnchor(
            QGraphicsView.ViewportAnchor.AnchorUnderMouse
        )
        self._retheme()
        theme.notifier().changed.connect(self._retheme)
        self._zoom = 1.0
        # The node the last click landed on -- see mouseDoubleClickEvent.
        self._clicked = None
        # The node the keyboard is on, as (guid, column, role). Kept as a key
        # and looked up, because every redraw makes the nodes afresh.
        self._cursor = None
        self.setAccessibleName(tr("Reference graph"))
        self.setAccessibleDescription(tr(
            "Arrow keys move between assets. Enter centres the graph on one, "
            "and Ctrl+Enter opens it in the asset editor."
        ))

    # -- the keyboard ---------------------------------------------------------
    def graph_nodes(self) -> list:
        return [item for item in self.scene().items() if isinstance(item, GraphNode)]

    def cursor_node(self):
        """The node the keyboard is on: the one last moved to if it is still
        drawn, or the asset in the middle."""
        nodes = self.graph_nodes()
        for node in nodes:
            if (node.guid, node.column, node.role) == self._cursor:
                return node
        for node in nodes:
            if node.role == "focus":
                return node
        return nodes[0] if nodes else None

    def set_cursor(self, node, announce: bool = True, reveal: bool = True) -> None:
        """Put the keyboard on ``node``, scroll to it unless ``reveal`` is
        false, and tell a screen reader."""
        self._cursor = (node.guid, node.column, node.role) if node is not None else None
        for other in self.graph_nodes():
            on = other is node and self.hasFocus()
            if other.keyboard != on:
                other.keyboard = on
                other.update()
        if node is None:
            return
        if reveal:
            self.ensureVisible(node)
        if announce:
            self.setAccessibleDescription(node.spoken)
            QAccessible.updateAccessibility(
                QAccessibleEvent(self, QAccessible.Event.DescriptionChanged)
            )

    def restore_cursor(self) -> None:
        """After a redraw: the keyboard back where it was, or on the middle."""
        self.set_cursor(self.cursor_node(), announce=self.hasFocus())

    def _step(self, key) -> None:
        """Move the keyboard one node: up or down its column, or across to
        the nearest node of the next column."""
        here = self.cursor_node()
        if here is None:
            return
        nodes = self.graph_nodes()
        x, y = here.pos().x(), here.pos().y()
        if key in (Qt.Key.Key_Up, Qt.Key.Key_Down):
            sign = -1 if key == Qt.Key.Key_Up else 1
            column = [n for n in nodes if n.pos().x() == x and (n.pos().y() - y) * sign > 0]
            there = min(column, key=lambda n: abs(n.pos().y() - y), default=None)
        else:
            sign = -1 if key == Qt.Key.Key_Left else 1
            beyond = [n for n in nodes if (n.pos().x() - x) * sign > 0]
            nearest = min((abs(n.pos().x() - x) for n in beyond), default=None)
            there = min(
                (n for n in beyond if abs(n.pos().x() - x) == nearest),
                key=lambda n: abs(n.pos().y() - y), default=None,
            )
        if there is not None:
            self.set_cursor(there)

    def keyPressEvent(self, event) -> None:
        key = event.key()
        if key in (Qt.Key.Key_Up, Qt.Key.Key_Down, Qt.Key.Key_Left, Qt.Key.Key_Right):
            self._step(key)
            return
        node = self.cursor_node()
        if node is not None and key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            if node.role == "more":
                self.moreClicked.emit(node.column)
            elif event.modifiers() & Qt.KeyboardModifier.ControlModifier:
                self.nodeActivated.emit(node.guid)
            else:
                self.nodeClicked.emit(node.guid)
            return
        shift_f10 = (key == Qt.Key.Key_F10
                     and event.modifiers() & Qt.KeyboardModifier.ShiftModifier)
        if node is not None and (key == Qt.Key.Key_Menu or shift_f10):
            self.menuRequested.emit(self.mapFromScene(node.sceneBoundingRect().center()))
            return
        super().keyPressEvent(event)

    def focusInEvent(self, event) -> None:
        super().focusInEvent(event)
        # A click on the canvas gives the focus too, and scrolling to the
        # keyboard's node then threw away the pan the click was starting.
        by_mouse = event.reason() == Qt.FocusReason.MouseFocusReason
        self.set_cursor(self.cursor_node(), reveal=not by_mouse)

    def focusOutEvent(self, event) -> None:
        super().focusOutEvent(event)
        for node in self.graph_nodes():
            if node.keyboard:
                node.keyboard = False
                node.update()

    def _retheme(self) -> None:
        # Nodes read their colours as they paint, so they only need asking.
        self.setBackgroundBrush(QBrush(QColor(theme.BG)))
        self.viewport().update()

    def wheelEvent(self, event) -> None:
        step = 1.15 if event.angleDelta().y() > 0 else 1 / 1.15
        target = self._zoom * step
        if not 0.25 <= target <= 3.0:
            return
        self._zoom = target
        self.scale(step, step)
        self._sync_drag_mode()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._sync_drag_mode()

    def _sync_drag_mode(self) -> None:
        """Grab-panning only where there's somewhere to pan to.

        Zoomed out far enough that the whole graph already fits the viewport,
        the scrollbars have nowhere to go -- so ScrollHandDrag's cursor would
        still promise a drag that moves nothing, which feels broken rather
        than just idle.
        """
        h = self.horizontalScrollBar()
        v = self.verticalScrollBar()
        pannable = h.minimum() != h.maximum() or v.minimum() != v.maximum()
        want = (
            QGraphicsView.DragMode.ScrollHandDrag
            if pannable
            else QGraphicsView.DragMode.NoDrag
        )
        if self.dragMode() != want:
            self.setDragMode(want)

    def _node_at(self, pos):
        for item in self.items(pos):
            if isinstance(item, GraphNode) and item.role != "more":
                return item
        return None

    def _any_node_at(self, pos):
        for item in self.items(pos):
            if isinstance(item, GraphNode):
                return item
        return None

    def node_at(self, pos):
        """The node under ``pos`` -- overflow marker included -- or ``None``.

        The panel asks this when it builds the right-click menu: what the menu
        offers is a question about what was clicked, and the view is the only
        thing that knows where the boxes are.
        """
        return self._any_node_at(pos)

    def contextMenuEvent(self, event) -> None:
        """Hand the right-click to the panel, which knows what a node means.

        Deliberately not swallowed into the view: the menu's entries are about
        assets and the trail through them, and the view knows about neither.
        The click does not move the graph on the way -- right-clicking a node
        to read what can be done to it is not the same act as clicking it,
        which re-centres on it.
        """
        self.menuRequested.emit(event.pos())
        event.accept()

    def mousePressEvent(self, event) -> None:
        # The side buttons walk the history, as they do in a browser.
        if event.button() == Qt.MouseButton.BackButton:
            self.backRequested.emit()
            return
        if event.button() == Qt.MouseButton.ForwardButton:
            self.forwardRequested.emit()
            return
        item = self._any_node_at(event.position().toPoint())
        if item is not None and event.button() == Qt.MouseButton.LeftButton:
            # Which node the click before a double-click was on. A single click
            # re-centres the graph, so the node under the cursor changes between
            # the two halves of a double-click -- and Qt, which decides by time
            # and distance rather than by what was hit, then reports a
            # double-click on whatever slid under the pointer.
            self._clicked = item.guid
            if item.role == "more":
                self.moreClicked.emit(item.column)
            else:
                self.nodeClicked.emit(item.guid)
            return  # swallowed: panning from a node would fight the click
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:
        # Left button only: a right-button double-click was opening the asset
        # *and* going on to be a right-click, so you arrived in the editor with
        # a context menu following you there.
        if event.button() != Qt.MouseButton.LeftButton:
            super().mouseDoubleClickEvent(event)
            return
        node = self._node_at(event.position().toPoint())
        if node is not None and node.guid == self._clicked:
            self.nodeActivated.emit(node.guid)
            return
        super().mouseDoubleClickEvent(event)

    def reset_zoom(self) -> None:
        self.resetTransform()
        self._zoom = 1.0
        self._sync_drag_mode()

    def sync_zoom(self) -> None:
        """Adopt whatever scale a fitInView just applied, so the wheel limits hold."""
        self._zoom = self.transform().m11() or 1.0
        self._sync_drag_mode()


@dataclass(frozen=True)
class ListedAsset:
    """One row of the full-hop list, resolved once when the list is built."""

    guid: str
    label: str
    template: str
    through: str

    @property
    def haystack(self) -> str:
        return " ".join((self.label, self.template, self.guid, self.through)).lower()


class ReferenceListDialog(QDialog):
    """Every asset at one hop, when there are too many to draw.

    The graph stays readable by showing a slice, which is only acceptable if
    the rest is reachable -- so the marker opens the full list here, as a
    table, where a few hundred rows are no trouble at all.

    Rows arrive already resolved.  Looking each asset up while filtering meant
    a few hundred database queries *per keystroke* -- around 90ms each on a
    real hop, for answers that cannot change while the dialog is open, which
    made typing into the box feel broken.
    """

    def __init__(self, rows, heading: str, parent=None):
        super().__init__(parent)
        self.rows = list(rows)
        self.chosen_guid = None

        # Translators: the Studio's graph of which assets point at which.
        self.setWindowTitle(tr("References"))
        self.setMinimumSize(760, 520)

        # Same debounce the app's other filters use: a keystroke should never
        # be waiting on a table rebuild.
        self._filter_timer = QTimer(self)
        self._filter_timer.setSingleShot(True)
        self._filter_timer.setInterval(150)
        self._filter_timer.timeout.connect(self._refresh)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 14, 16, 12)
        outer.setSpacing(8)

        title = QLabel(heading)
        title.setObjectName("Heading")
        outer.addWidget(title)

        self.search_box = QLineEdit()
        self.search_box.setPlaceholderText(tr("Filter…"))
        self.search_box.setAccessibleName(tr("Filter"))
        self.search_box.setClearButtonEnabled(True)
        self.search_box.textChanged.connect(self._filter_timer.start)
        outer.addWidget(self.search_box)

        self.table = QTableWidget(0, 4)
        self.table.setTabKeyNavigation(False)
        self.table.setHorizontalHeaderLabels([
            tr("Asset"), tr("Template"), "GUID",  # i18n: keep - GUID
            # Translators: a column naming the field a reference goes through.
            tr("Through"),
        ])
        self.table.verticalHeader().setVisible(False)
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setWordWrap(False)
        enter_activates(self.table)
        self.table.activated.connect(self._accept_selection)
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._context_menu)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Interactive)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Interactive)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self.table.setColumnWidth(0, 260)
        self.table.setColumnWidth(1, 150)
        fit_columns(self.table)
        self.table.setItemDelegateForColumn(0, ElideItemDelegate(self.table))
        self.table.setAccessibleName(heading)
        outer.addWidget(self.table, 1)
        self.sorting = sortable(self.table)

        self.status = QLabel("")
        self.status.setObjectName("SubHeading")
        outer.addWidget(self.status)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        self.open_button = buttons.addButton(
            tr("&Show in graph"), QDialogButtonBox.ButtonRole.AcceptRole
        )
        self.open_button.setObjectName("Primary")
        self.open_button.clicked.connect(self._accept_selection)
        # Nothing is selected when this opens, and the button did nothing at
        # all until something was -- highlighted the whole time, which reads as
        # the way on rather than as a button waiting for a row.
        self.open_button.setEnabled(False)
        self.table.itemSelectionChanged.connect(self._on_selection_changed)
        buttons.rejected.connect(self.reject)
        tab_as_laid_out(buttons)
        outer.addWidget(buttons)

        self._refresh()

    def _on_selection_changed(self) -> None:
        model = self.table.selectionModel()
        self.open_button.setEnabled(bool(model and model.selectedRows()))

    def _refresh(self) -> None:
        needle = self.search_box.text().strip().lower()
        rows = [row for row in self.rows if not needle or needle in row.haystack]

        self.table.setUpdatesEnabled(False)
        try:
            self.table.setRowCount(len(rows))
            for r, row in enumerate(rows):
                first = QTableWidgetItem(row.label)
                first.setData(Qt.ItemDataRole.UserRole, row.guid)
                self.table.setItem(r, 0, first)
                self.table.setItem(r, 1, QTableWidgetItem(row.template))
                self.table.setItem(r, 2, QTableWidgetItem(row.guid))
                self.table.setItem(r, 3, QTableWidgetItem(row.through))
            # Filled again with as many rows, the sort hears nothing: it is asked (A10-40).
            self.sorting.apply()
        finally:
            self.table.setUpdatesEnabled(True)

        total = len(self.rows)
        if len(rows) == total:
            self.status.setText(trn("%(n)s asset", "%(n)s assets", total))
        else:
            self.status.setText(trn(
                "%(shown)s of %(n)s asset", "%(shown)s of %(n)s assets", total,
                shown=number(len(rows)),
            ))

    def _accept_selection(self) -> None:
        model = self.table.selectionModel()
        rows = model.selectedRows() if model else []
        if not rows:
            return
        item = self.table.item(rows[0].row(), 0)
        if item is None:
            return
        self.chosen_guid = item.data(Qt.ItemDataRole.UserRole)
        self.accept()

    def _context_menu(self, point) -> None:
        """A few hundred rows, and the GUID in one of them is what you came for.

        Reading it off the screen and typing it back in is what this list left
        somebody doing: the row's own button takes the graph there, which is a
        different thing to want.
        """
        item = self.table.itemAt(point)
        if item is None:
            return
        self.table.selectRow(item.row())
        menu = self.menu_for(item.row())
        menu.exec(self.table.viewport().mapToGlobal(point))
        menu.deleteLater()

    def menu_for(self, row: int) -> QMenu:
        first = self.table.item(row, 0)
        guid = first.data(Qt.ItemDataRole.UserRole) if first is not None else ""
        menu = QMenu(self)
        menu.addAction(tr("&Show in graph"), self._accept_selection)
        menu.addSeparator()
        menu.addAction(
            tr("Copy GUID"), lambda: QGuiApplication.clipboard().setText(str(guid))
        )
        menu.addAction(
            tr("Copy name"),
            lambda: QGuiApplication.clipboard().setText(
                first.text() if first is not None else ""
            ),
        )
        return menu


class ReferencePanel(QWidget):
    """The References step: the graph around one asset."""

    assetActivated = Signal(str)  # open this GUID in the editor
    assetShown = Signal(str)      # the graph moved to this GUID
    pinChanged = Signal(bool)     # held on its asset, or let go to follow

    def __init__(self, parent=None, trail: AssetTrail = None):
        super().__init__(parent)
        self.index = None
        self.guid = None
        # Shared with the asset editor when the window hands one over, so
        # stepping back in either takes both back; a trail of its own otherwise.
        self.trail = trail if trail is not None else AssetTrail(self)
        # The one handed over, to take up again after walking alone.
        self._shared_trail = self.trail
        self._full_columns = {}
        self._edges = []
        self._build_ui()
        self.clear()

    # -- construction -------------------------------------------------------
    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 14, 16, 12)
        outer.setSpacing(8)

        # The tab says "References" already, so the row leads with the asset.
        top = QHBoxLayout()
        # Clicking through a graph is browsing, so it gets a browser's history --
        # including the mouse's own back and forward buttons.
        self.nav = NavButtons(self.trail, host=self)
        self.nav.backRequested.connect(self.go_back)
        self.nav.forwardRequested.connect(self.go_forward)
        self.back_button = self.nav.back_button
        self.forward_button = self.nav.forward_button
        top.addWidget(self.nav)
        top.addSpacing(8)
        self.title_label = QLabel(tr("No asset selected"))
        self.title_label.setObjectName("AssetTitle")
        # A long name gives way before the row pushes the window wider. The
        # subtitle and the node in the middle both still say which asset it is.
        self.title_label.setMinimumWidth(120)
        top.addWidget(self.title_label)
        self.help = HelpTip(tr(
            "Assets on the left refer to the asset in the middle. Assets on "
            "the right are the ones it refers to. The game's files don't mark "
            "which fields are links, so the Studio infers them from how often "
            "each field holds a real GUID across the whole game. Click an "
            "asset or press Enter to move the graph to it. Double-click or "
            "press Ctrl+Enter to edit it."
        ), tr("References"))
        top.addWidget(self.help)
        top.addStretch(1)
        self.open_button = QPushButton(tr("Edit this asset"))
        self.open_button.setToolTip(tr("Opens the asset in the middle in the asset editor."))
        self.open_button.clicked.connect(self._open_focus)
        top.addWidget(self.open_button)
        # The window adds what it has to say about the panel as a whole at
        # the end of this row, where it goes wherever the panel goes.
        self.top_row = top
        outer.addLayout(top)

        self.subtitle_label = ElidingLabel()
        self.subtitle_label.setObjectName("SubHeading")
        outer.addWidget(self.subtitle_label)

        # What to do here, for a tab with no asset. It goes once there is one.
        self.hint = QLabel(tr(
            "Open an asset in the asset editor to see what refers to it and "
            "what it refers to. You can also right-click an asset and choose "
            "“References for this asset…”."
        ))
        self.hint.setObjectName("StepHint")
        self.hint.setWordWrap(True)
        outer.addWidget(self.hint)

        tools = QHBoxLayout()
        # Translators: how many hops out from the asset the graph follows.
        depth_caption = QLabel(tr("&Depth:"))
        tools.addWidget(depth_caption)
        self.depth_spin = QSpinBox()
        self.depth_spin.setRange(1, 3)
        self.depth_spin.setValue(1)
        self.depth_spin.setToolTip(tr("How many hops out from this asset to follow."))
        self.depth_spin.valueChanged.connect(self.refresh)
        depth_caption.setBuddy(self.depth_spin)
        tools.addWidget(self.depth_spin)
        tools.addSpacing(8)

        self.uncertain_box = QCheckBox(tr("Show uncertain links"))
        self.uncertain_box.toggled.connect(self.refresh)
        tools.addWidget(self.uncertain_box)
        tools.addWidget(HelpTip(tr(
            "Also draws links from fields the evidence doesn't settle. They "
            "are dashed, and hovering over one shows the odds. They are "
            "guesses, so this is off to begin with."
        ), self.uncertain_box.text()))
        tools.addSpacing(8)

        self.nominal_box = QCheckBox(tr("Hide name-only links"))
        nominal_help = HelpTip(tr(
            "A costs list can name a good and spend none of it, so an asset "
            "can look like it uses something it never touches. This hides "
            "links whose entry names the asset without setting a value. It "
            "applies only where entries in that list normally set one. Untick "
            "it to see everything that names an asset, for example before "
            "removing it."
        ), self.nominal_box.text())
        # On by default. A link that names an asset and spends none of it is
        # almost never the thing you came to the graph to find, and leaving them
        # in makes a hub look connected to half the file. The box is still there
        # for the one case that wants them: checking what would dangle if an
        # asset went away.
        self.nominal_box.setChecked(True)
        self.nominal_box.toggled.connect(self.refresh)
        tools.addWidget(self.nominal_box)
        tools.addWidget(nominal_help)

        # Offered only while the graph and the asset editor are in different
        # windows: in one window only one of them shows, each catches up
        # with the other when it is next looked at, and there is nothing to
        # hold the graph against.
        self.pin_box = QCheckBox(tr("Pin to this asset"))
        self.pin_box.toggled.connect(self.pinChanged)
        self.pin_help = HelpTip(tr(
            "The graph moves to each asset you open in the asset editor. "
            "Tick this to keep the graph on its asset, for example to edit "
            "its neighbours with the graph still in view. You can still "
            "click through the graph. Untick it and the graph moves to the "
            "asset the editor has open."
        ), self.pin_box.text())
        self._pin_gap = QSpacerItem(8, 0)
        self._tools = tools
        tools.addSpacerItem(self._pin_gap)
        tools.addWidget(self.pin_box)
        tools.addWidget(self.pin_help)
        self.offer_pin(False)

        tools.addStretch(1)
        reset = QPushButton(tr("Reset zoom"))
        reset.clicked.connect(self._reset_view)
        tools.addWidget(reset)
        outer.addLayout(tools)

        self.view = ReferenceView()
        self.view.nodeClicked.connect(self.show_asset)
        self.view.nodeActivated.connect(self.assetActivated.emit)
        self.view.moreClicked.connect(self.show_full_column)
        self.view.backRequested.connect(self.go_back)
        self.view.forwardRequested.connect(self.go_forward)
        self.view.menuRequested.connect(self._context_menu)
        # The links are drawn in the scheme's colours when they are made.
        theme.notifier().changed.connect(self.refresh)
        outer.addWidget(self.view, 1)

        self.status_label = QLabel("")
        self.status_label.setObjectName("SubHeading")
        outer.addWidget(self.status_label)

    # -- beside the editor, in a window of its own or the editor's ----------
    @property
    def pinned(self) -> bool:
        """Whether the graph is held on its asset and follows nothing."""
        return self.pin_box.isChecked()

    def offer_pin(self, offered: bool) -> None:
        """Show the pin, or take it away and let the graph go with it."""
        if not offered:
            self.pin_box.setChecked(False)
        self.pin_box.setVisible(offered)
        self.pin_help.setVisible(offered)
        self._pin_gap.changeSize(8 if offered else 0, 0)
        self._tools.invalidate()

    def walk_alone(self, alone: bool) -> None:
        """Step back and forward through a history of the graph's own, or
        through the one shared with the asset editor again.

        One history fits two views of which only one shows: back in either
        takes both back.  Side by side the editor no longer follows the
        graph, and a step back here would move a history the editor is
        standing on to an asset it does not have open.
        """
        if alone == (self.trail is not self._shared_trail):
            return
        if alone:
            own = AssetTrail(self)
            if self.guid is not None:
                own.visit(self.guid)
        else:
            own, gone = self._shared_trail, self.trail
            gone.deleteLater()
        self.trail = own
        self.nav.walk(own)

    # -- state --------------------------------------------------------------
    def set_index(self, index) -> None:
        self.index = index
        if self.guid is not None:
            self.refresh()

    def clear(self) -> None:
        self.guid = None
        self.trail.clear()
        # The editor's too, where the graph is walking one of its own: no
        # asset is open in either, and there is no way back to one.
        if self._shared_trail is not self.trail:
            self._shared_trail.clear()
        self._full_columns = {}
        self._edges = []
        self.view.scene().clear()
        self.title_label.setText(tr("No asset selected"))
        self.subtitle_label.setFullText("")
        self.subtitle_label.setVisible(False)
        self.hint.setVisible(True)
        self.status_label.setText("")
        self.open_button.setEnabled(False)

    # -- history ------------------------------------------------------------
    def go_back(self) -> None:
        guid = self.trail.back()
        if guid is not None:
            self.show_asset(guid, record=False)

    def go_forward(self) -> None:
        guid = self.trail.forward()
        if guid is not None:
            self.show_asset(guid, record=False)

    def mousePressEvent(self, event) -> None:
        """The mouse's side buttons, anywhere on the step -- not just the canvas."""
        if event.button() == Qt.MouseButton.BackButton:
            self.go_back()
            return
        if event.button() == Qt.MouseButton.ForwardButton:
            self.go_forward()
            return
        super().mousePressEvent(event)

    def keyPressEvent(self, event) -> None:
        if event.modifiers() & Qt.KeyboardModifier.AltModifier:
            if event.key() == Qt.Key.Key_Left:
                self.go_back()
                return
            if event.key() == Qt.Key.Key_Right:
                self.go_forward()
                return
        super().keyPressEvent(event)

    def show_asset(self, guid: str, record: bool = True) -> None:
        """Move the graph to ``guid``."""
        guid = str(guid)
        if self.index is None:
            return
        if record:
            self.trail.visit(guid)
        self.guid = guid
        self.open_button.setEnabled(True)
        self.hint.setVisible(False)
        self.subtitle_label.setVisible(True)
        row = self.index.asset_row(guid)
        self.title_label.setText(row.label if row else ("GUID " + guid))  # i18n: keep - GUID
        bits = ["GUID %s" % guid]
        if row and row.template:
            bits.append(tr("Template: %s", row.template))
        if row and row.group_path:
            bits.append(row.group_path)
        self.subtitle_label.setFullText("   ·   ".join(bits))
        self.refresh()
        self.assetShown.emit(guid)

    def _open_focus(self) -> None:
        if self.guid:
            self.assetActivated.emit(self.guid)

    # -- what a node offers -------------------------------------------------
    def _context_menu(self, pos) -> None:
        menu = self.menu_for(self.view.node_at(pos))
        menu.exec(self.view.viewport().mapToGlobal(pos))
        menu.deleteLater()

    def menu_for(self, node) -> QMenu:
        """What can be done to one box in the graph, as a menu nobody has shown.

        The graph had none, which made it the one step where what you could do
        was only ever whatever the mouse happened to do to what was under it:
        a click re-centres, a double-click edits, and the GUID on screen could
        not be copied at all without opening the asset in the editor first and
        finding it there.  None of that was discoverable, and a graph is the
        part of the app somebody explores rather than reads.

        The graph only ever reads -- nothing here can change a ModOp, and
        nothing in this menu does either.  What it offers is the two things a
        click already meant, said out loud, and the two facts about an asset
        worth having in the clipboard.

        Built apart from being shown, like the Mod changes panel's, so what it
        offers can be asked without a modal menu waiting for a click.
        """
        menu = QMenu(self)
        menu.setToolTipsVisible(True)

        if node is not None and node.role == "more":
            # The overflow marker. Clicking it opens the full list already;
            # the entry is here so the menu is never empty on a box that has
            # something to say, and so the list is nameable.
            rest = self._full_columns.get(node.column) or []
            show = menu.addAction(
                trn("Show all %(n)s at this hop…", "Show all %(n)s at this hop…", len(rest)),
                lambda: self.show_full_column(node.column),
            )
            show.setEnabled(bool(rest))
            return menu

        if node is not None:
            guid = node.guid
            name = self.index.display_name(guid) if self.index else guid
            # Named, so it is plain which box was right-clicked; shortened,
            # so a long name does not make the menu as wide as the window.
            shown = short_name(name, 32)
            menu.addAction(
                tr("Edit %s in the asset editor", shown),
                lambda: self.assetActivated.emit(guid),
            )
            centre = menu.addAction(
                tr("Move the graph to %s", shown),
                lambda: self.show_asset(guid),
            )
            # The asset in the middle is already where the graph is; offering
            # to move there is offering to do nothing, and hiding the entry
            # would make the menu a different shape on the one box a reader is
            # most likely to try it on.
            centre.setEnabled(guid != self.guid)
            if guid == self.guid:
                centre.setToolTip(tr("This is the asset the graph is already on."))
            menu.addSeparator()
            menu.addAction(
                tr("Copy GUID"),
                lambda: QGuiApplication.clipboard().setText(guid),
            )
            menu.addAction(
                tr("Copy name"),
                lambda: QGuiApplication.clipboard().setText(name),
            )
            return menu

        # The canvas. Walking the trail and framing the graph are on buttons
        # above, which is a long way from where the cursor is on a graph you
        # are three hops into -- and the two filters are the answer to "why is
        # the thing I came for not drawn", which is a question asked *at* the
        # empty space where it is not.
        back = menu.addAction(tr("Back", context="navigation"), self.go_back)
        back.setEnabled(self.trail.can_go_back())
        forward = menu.addAction(tr("Forward", context="navigation"), self.go_forward)
        forward.setEnabled(self.trail.can_go_forward())
        menu.addSeparator()
        edit = menu.addAction(tr("Edit this asset"), self._open_focus)
        edit.setEnabled(bool(self.guid))
        menu.addAction(tr("Reset zoom"), self._reset_view)
        menu.addSeparator()
        for box in (self.uncertain_box, self.nominal_box):
            action = menu.addAction(box.text())
            action.setCheckable(True)
            action.setChecked(box.isChecked())
            action.toggled.connect(box.setChecked)
        return menu

    def _column_rows(self, column: int):
        """Resolve one hop into rows, once.

        The fields each link travels through come from the edges the walk
        already found rather than from fresh queries -- and correctly so at
        more than one hop out, where an asset connects to the column beside
        it, not to the asset in the middle.
        """
        guids = self._full_columns.get(column) or []
        fields = {}
        for source, target, links in self._edges:
            end = source if column < 0 else target
            if end in self._full_columns.get(column, ()):
                fields.setdefault(end, set()).update(
                    link.field_label for link in links
                )
        rows = []
        with self._steady():
            for guid in guids:
                row = self.index.asset_row(guid)
                rows.append(
                    ListedAsset(
                        guid=guid,
                        label=row.label if row else ("GUID " + guid),
                        template=row.template if row else "",
                        through=", ".join(sorted(fields.get(guid, ()))),
                    )
                )
        return rows

    def show_full_column(self, column: int) -> None:
        """The whole of one hop, for a column too wide to draw."""
        if self.index is None or not self._full_columns.get(column):
            return
        rows = self._column_rows(column)
        hop = abs(column)
        asset = self.index.display_name(self.guid)
        if column < 0 and hop > 1:
            heading = trn(
                "%(n)s asset that points at %(asset)s, %(hops)s hops out",
                "%(n)s assets that point at %(asset)s, %(hops)s hops out",
                len(rows), asset=asset, hops=number(hop),
            )
        elif column < 0:
            heading = trn(
                "%(n)s asset that points at %(asset)s",
                "%(n)s assets that point at %(asset)s",
                len(rows), asset=asset,
            )
        elif hop > 1:
            heading = trn(
                "%(n)s asset that is pointed at by %(asset)s, %(hops)s hops out",
                "%(n)s assets that are pointed at by %(asset)s, %(hops)s hops out",
                len(rows), asset=asset, hops=number(hop),
            )
        else:
            heading = trn(
                "%(n)s asset that is pointed at by %(asset)s",
                "%(n)s assets that are pointed at by %(asset)s",
                len(rows), asset=asset,
            )

        dialog = ReferenceListDialog(rows, heading, self)
        accepted = dialog.exec() == int(QDialog.DialogCode.Accepted)
        chosen = dialog.chosen_guid if accepted else None
        dialog.deleteLater()
        if chosen:
            self.show_asset(chosen)

    def _reset_view(self) -> None:
        self.view.reset_zoom()
        self._centre_on_focus()

    # -- the graph ----------------------------------------------------------
    def _steady(self):
        """The mods held still while the graph asks about them.

        With a mods folder loaded, each question the graph asks -- a name, or
        whether a mod makes the asset -- took stock of every change first: 50
        ms apiece on 32,000 changes, asked thousands of times for a hub like
        Coins, which hung the window for minutes.  Nothing can edit while the
        graph is drawn, so stock is taken once for the lot.
        """
        steady = getattr(self.index, "steady", None)
        return steady() if steady is not None else nullcontext()

    def refresh(self) -> None:
        scene = self.view.scene()
        scene.clear()
        if self.index is None or self.guid is None:
            return

        confident_only = not self.uncertain_box.isChecked()
        with_nominal = not self.nominal_box.isChecked()
        depth = self.depth_spin.value()
        with self._steady():
            columns, edges, capped = self._walk(
                self.guid, depth, confident_only, with_nominal
            )
            self._edges = edges
            nodes = self._draw(columns, edges, capped)
            self._draw_edges(scene, nodes, edges)
            self._report(columns, edges, capped, confident_only, with_nominal)
        self._centre_on_focus()

    def _walk(self, root: str, depth: int, confident_only: bool, with_nominal: bool):
        """Breadth-first out from ``root``, in both directions at once.

        Returns ``(columns, edges, capped)``: ``columns`` maps a column index
        (negative = things pointing here) to the GUIDs drawn in it, ``edges`` is
        a list of ``(source, target, links)``, and ``capped`` maps a column to
        everything it found, drawn or not.
        """
        columns = {0: [root]}
        edges = []
        capped = {}
        self._full_columns = {}
        seen = {root}

        for direction in (-1, 1):
            frontier = [root]
            for step in range(1, depth + 1):
                column = direction * step
                found = []
                reached_by = {}
                for guid in frontier:
                    if direction > 0:
                        links = self.index.references_from(
                            guid, confident_only, with_nominal
                        )
                    else:
                        links = self.index.references_to(
                            guid, confident_only, with_nominal
                        )
                    grouped = {}
                    for link in links:
                        grouped.setdefault(link.guid, []).append(link)
                    for other, group in grouped.items():
                        if other == guid:
                            continue
                        pair = (guid, other) if direction > 0 else (other, guid)
                        edges.append((pair[0], pair[1], group))
                        reached_by.setdefault(other, []).extend(group)
                        if other in seen:
                            continue
                        seen.add(other)
                        found.append(other)
                if len(found) > DEFAULT_PER_COLUMN:
                    # A slice has to be worth having, so the weakest links go
                    # last and are the first thing the overflow node swallows:
                    # what gets cut is the least informative end of the list.
                    found.sort(key=lambda guid: _demotion(reached_by[guid]))
                    capped[column] = list(found)
                self._full_columns[column] = list(found)
                if column in capped:
                    found = found[:DEFAULT_PER_COLUMN]
                if not found:
                    break
                columns[column] = found
                frontier = found
        return columns, edges, capped

    def _draw(self, columns, edges, capped=None):
        """Place a node for every GUID; returns ``{guid: GraphNode}``."""
        scene = self.view.scene()
        capped = capped or {}
        nodes = {}
        for column in sorted(columns):
            guids = columns[column]
            rows = len(guids) + (1 if column in capped else 0)
            total = rows * (NODE_HEIGHT + ROW_GAP) - ROW_GAP
            x = column * (NODE_WIDTH + COLUMN_GAP)
            if column in capped:
                hidden = len(capped[column]) - len(guids)
                marker = GraphNode(
                    "",
                    # Translators: the box standing for the assets not drawn.
                    trn("+ %(n)s more", "+ %(n)s more", hidden),
                    trn("click to see all %(n)s", "click to see all %(n)s",
                        len(capped[column])),
                    "more",
                    column,
                )
                marker.setPos(x, -total / 2 + len(guids) * (NODE_HEIGHT + ROW_GAP))
                marker.setToolTip(trn(
                    "%(n)s more asset at this hop. Click or press Enter to list them.",
                    "%(n)s more assets at this hop. Click or press Enter to list them.",
                    hidden,
                ))
                marker.spoken = marker.toolTip()
                scene.addItem(marker)
            for i, guid in enumerate(guids):
                y = -total / 2 + i * (NODE_HEIGHT + ROW_GAP)
                row = self.index.asset_row(guid)
                title = row.label if row else ("GUID " + guid)
                subtitle = row.template if row and row.template else ("GUID " + guid)
                node = GraphNode(
                    guid, title, subtitle, "focus" if column == 0 else "normal"
                )
                node.setPos(x, y)
                # Made once for both: on a hub it is a line for each of
                # thousands of links, every one of them a name to look up.
                lines = self._node_links(guid, edges)
                node.setToolTip(self._node_tooltip(guid, title, lines))
                # What it is, then how it links: each line an arrow, the
                # other asset, and the field the link is through.
                node.spoken = "\n".join(["%s, %s" % (title, subtitle), *lines[:12]])
                node.setZValue(1)
                scene.addItem(node)
                nodes[guid] = node
        self.view.restore_cursor()
        return nodes

    def _node_links(self, guid: str, edges) -> list:
        """One line for each link ``guid`` takes part in."""
        fields = []
        for source, target, links in edges:
            if guid not in (source, target):
                continue
            for link in links:
                other = target if source == guid else source
                arrow = "→" if source == guid else "←"
                fields.append(
                    "%s %s  ·  %s"
                    % (arrow, short_name(self.index.display_name(other)),
                       clipped(link.field_label, FIELD_MOST))
                )
        return fields

    def _node_tooltip(self, guid: str, title: str, fields) -> str:
        head = tr(
            "%(title)s\nGUID %(guid)s\n\n"
            "Double-click or press Ctrl+Enter to edit this asset.",
            title=short_name(title, 60), guid=guid,
        )
        if not fields:
            return head
        shown = fields[:NODE_TIP_LINKS]
        more = len(fields) - len(shown)
        if more:
            shown.append(trn("…and %(n)s more", "…and %(n)s more", more))
        return head + "\n\n" + "\n".join(shown)

    def _draw_edges(self, scene, nodes, edges) -> None:
        drawn = set()
        for source, target, links in edges:
            if source not in nodes or target not in nodes:
                continue
            key = (source, target)
            if key in drawn:
                continue
            drawn.add(key)
            confident = any(link.confident for link in links)

            a = nodes[source]
            b = nodes[target]
            start = QPointF(a.pos().x() + NODE_WIDTH, a.pos().y() + NODE_HEIGHT / 2)
            end = QPointF(b.pos().x(), b.pos().y() + NODE_HEIGHT / 2)
            if end.x() < start.x():
                # A link pointing back the way we came: leave from the left.
                start = QPointF(a.pos().x(), a.pos().y() + NODE_HEIGHT / 2)
                end = QPointF(b.pos().x() + NODE_WIDTH, b.pos().y() + NODE_HEIGHT / 2)
            span = (end.x() - start.x()) / 2
            path = QPainterPath(start)
            path.cubicTo(
                QPointF(start.x() + span, start.y()),
                QPointF(end.x() - span, end.y()),
                end,
            )

            pen = QPen(QColor(theme.TEXT_DIM if confident else theme.WARN), 1.4)
            if not confident:
                pen.setStyle(Qt.PenStyle.DashLine)
                pen.setWidthF(1.1)
            item = scene.addPath(path, pen)
            item.setZValue(0)
            item.setToolTip(self._edge_tooltip(source, target, links))

    def _edge_tooltip(self, source: str, target: str, links) -> str:
        head = "%s  →  %s" % (
            short_name(self.index.display_name(source)),
            short_name(self.index.display_name(target)),
        )
        lines = []
        for link in links[:EDGE_TIP_LINKS]:
            seen, resolved, telling = self.index.reference_field(link.path, link.tag)
            from ..core import refs as refs_mod

            lines.append(
                "%s\n    %s" % (clipped(link.field_label, FIELD_MOST),
                                refs_mod.confidence_note(link.tag, seen, resolved,
                                                         telling))
            )
        if len(links) > EDGE_TIP_LINKS:
            lines.append(trn("…and %(n)s more", "…and %(n)s more",
                             len(links) - EDGE_TIP_LINKS))
        return head + "\n\n" + "\n".join(lines)

    def _report(self, columns, edges, capped, confident_only, with_nominal) -> None:
        out = len([e for e in edges if e[0] == self.guid])
        into = len([e for e in edges if e[1] == self.guid])
        drawn = sum(len(v) for v in columns.values())
        # Separate facts side by side, not one sentence: each is whole.
        bits = [
            trn("%(n)s reference out, %(into)s in", "%(n)s references out, %(into)s in",
                out, into=number(into)),
            trn("%(n)s asset drawn", "%(n)s assets drawn", drawn),
        ]
        if capped:
            worst = max(len(v) for v in capped.values())
            bits.append(trn(
                "showing %(shown)s of %(n)s at the widest hop (click “+ more” for all)",
                "showing %(shown)s of %(n)s at the widest hop (click “+ more” for all)",
                worst, shown=number(DEFAULT_PER_COLUMN),
            ))
        if confident_only:
            uncertain = self._link_count(False, with_nominal) - self._link_count(
                True, with_nominal
            )
            if uncertain > 0:
                bits.append(trn(
                    "%(n)s uncertain link hidden", "%(n)s uncertain links hidden",
                    uncertain,
                ))
        nominal = self._link_count(confident_only, True) - self._link_count(
            confident_only, False
        )
        if nominal > 0:
            if with_nominal:
                # Said even while they are being shown: this is the explanation
                # for an asset that looks more used than it really is.
                bits.append(trn(
                    "%(n)s link only names it (see “%(box)s”)",
                    "%(n)s links only name it (see “%(box)s”)",
                    nominal, box=self.nominal_box.text(),
                ))
            else:
                bits.append(trn(
                    "%(n)s name-only link hidden", "%(n)s name-only links hidden",
                    nominal,
                ))
        self.status_label.setText("   ·   ".join(bits))

    def _link_count(self, confident_only: bool, with_nominal: bool) -> int:
        """Links touching the focus asset, either direction, under one setting."""
        if self.index is None or self.guid is None:
            return 0
        return len(
            self.index.references_from(self.guid, confident_only, with_nominal)
        ) + len(self.index.references_to(self.guid, confident_only, with_nominal))

    def _centre_on_focus(self) -> None:
        """Frame the whole graph, or centre on the focus if it already fits.

        Zooming out to fit is right for a graph that overflows; zooming *in* to
        fill the window with three boxes is not, so a small graph is left at
        its natural size with the focus asset in the middle.
        """
        scene = self.view.scene()
        rect = scene.itemsBoundingRect()
        if rect.isNull():
            return
        scene.setSceneRect(rect.adjusted(-60, -60, 60, 60))
        self.view.reset_zoom()
        viewport = self.view.viewport().rect()
        if rect.width() > viewport.width() or rect.height() > viewport.height():
            self.view.fitInView(
                scene.sceneRect(), Qt.AspectRatioMode.KeepAspectRatio
            )
            self.view.sync_zoom()
        else:
            self.view.centerOn(NODE_WIDTH / 2, 0)
