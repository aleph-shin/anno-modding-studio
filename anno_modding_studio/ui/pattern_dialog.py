"""Writing a pattern: one ModOp aimed at a shape rather than at a list of assets.

The Studio's whole habit is to narrow things down -- one ModOp per node, so mods
stack.  A pattern goes the other way on purpose, and it is worth being clear about
when that is the right thing to want: when the mod means *every asset like this*
and the set is not fixed.  "Every trade union gets a fourth slot" is true of
trade unions another mod adds too, and a list of GUIDs written today is not.

The dialog is the op itself and what it comes to.  The op is typed as it is
written in a mod, in the pane the XML view types ModOps in, and read by the
reader that view reads them with (:func:`core.typedops.read_one`).  Under it
are the rows it will have in the Mod changes panel once it is accepted, drawn
by what draws them there (:class:`.change_rows.ChangeRows`).  Nothing is added
to the loadout until those rows say something the author recognises.

The rows are drawn when they are asked for, and stay until they are asked for
again.  Drawn as the op was typed they were gone at every keystroke that left
it unfinished, and those are the moments somebody changing an op looks at
what the last one came to.  So the pane says at once whether what is typed
is XML, which costs nothing, and the rows are of the op as it was last
previewed; what is typed since is not saved until it has been previewed too.

It used to be four boxes -- search, kind, field, value -- and a list of its
own.  The boxes held less than an op can say, so an op that said more was
shown and could not be changed, and the search still had to be typed as the
XPath it is.

One ModOp, and a search: several ops, a group, or an op that names its asset
are typed in an asset's XML view, which is where the mod keeps them.  A
search inside a group is read on its own, as though the group ran, and the
rows say so where it does not: saved, it is still in its group, and comes to
what an op of that group comes to (:func:`core.typedops.store_one`).
"""

from __future__ import annotations

import re
from contextlib import nullcontext as _nothing
from dataclasses import replace as _replace

from PySide6.QtCore import Qt
from PySide6.QtGui import QKeySequence, QShortcut, QTextCursor
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QMenu,
    QPushButton,
    QVBoxLayout,
)

from ..core import generator, grouping, modops, typedops
from ..core.i18n import number, tr, tr_stored, trn
from . import theme
from .asset_xml import XmlPane, where_reading_stopped
from .change_rows import ChangeRows, changes_tree

# What a new pattern starts as: a search to finish, and somewhere to say what
# is done to each match.
SKELETON = '<ModOp Type="merge" Path="%s">\n    \n</ModOp>'

# Shapes worth offering as a starting point, taken from what real mods write.
EXAMPLES = [
    "//Values/Trader",
    "//Values[../Template='FactoryBuilding7']",
    "//Asset[Template='ResidenceBuilding7']/Values/Residence7",
]

# How much of an op that is only shown is shown: one mod's op can be two
# megabytes.
SHOWN_LINES = 400


class OpRows(ChangeRows):
    """The rows one op has in the Mod changes panel, in a tree of their own.

    Drawn of a loadout that holds that op and nothing else, so what is under
    it is its own and not the mod's other work; what loads beside it, and
    what a row is called, is still asked of the loadout it is for.
    """

    live = False

    def __init__(self, workspace):
        self.view = workspace
        self.loadout = workspace.loadout
        self.tree = changes_tree()
        self._owners = None
        self._init_rows()

    def show(self, op, changes) -> None:
        """Draw ``op`` with ``changes`` under it, as the panel will."""
        self.tree.clear()
        if op is None:
            return
        held = self.view.loadout
        # The mod list and the mod's name as they are; the ops and changes
        # only this one's.
        alone = _replace(held, changes={}, ops=[], unread=[], history=[])
        alone.set_op(op)
        for change in changes:
            alone.set_change(change)
        self.loadout = alone
        steady = getattr(self.view, "steady", None)
        with steady() if steady is not None else _nothing():
            self._owners = grouping.owners_for(self.view, self._owners)
            ops, rest = grouping.split(alone, self.view, op.origin, self._owners)
            self._op_rows = {inner.uid for group in ops for inner in group.every()}
            mono = theme.mono_font()
            for group in ops:
                self._add_op(self.tree, group, mono)
            for group in rest:
                self._add_group(self.tree, group, mono)
        self.tree.layout_columns()

    def count(self) -> int:
        """How many rows are drawn, at every depth."""
        def under(item) -> int:
            return 1 + sum(under(item.child(at)) for at in range(item.childCount()))

        return sum(
            under(self.tree.topLevelItem(at)) for at in range(self.tree.topLevelItemCount())
        )


def holds(op, game: str = "") -> bool:
    """Whether the dialog can change ``op``, and not only show it: one ModOp
    aimed by a search."""
    if not op.can_expand or not op.searches or op.scope:
        return False
    return modops.read_op(op.source, game=game) is not None


_CONDITION = re.compile(r"\A\s*<[^>]*\sCondition\s*=", re.I)


def _has_condition(group) -> bool:
    """Whether a group, as the loadout keeps it, runs on a condition: its
    opening tag carries one."""
    return group is not None and bool(_CONDITION.match(group.source or ""))


class PatternDialog(QDialog):
    """Write or change one pattern, with what it comes to drawn on asking."""

    def __init__(self, workspace, parent=None, op=None, origin=None, copy_of=None):
        """``op`` is the pattern to change, or None for a new one.  A new one
        goes into ``origin``, or where changes go; ``copy_of`` is somebody
        else's pattern it starts as, to run after theirs in your patch."""
        super().__init__(parent)
        self.workspace = workspace
        self.loadout = workspace.loadout
        self.op = op
        if op is not None:
            self.origin = op.origin
        else:
            self.origin = origin if origin is not None else self.loadout.editing_origin()
        self.copy_of = copy_of if op is None else None
        self.result_op = None
        self.found = None
        self.changes = []
        self.matched = []
        self._holds_it = op is None or holds(op, self.loadout.game)
        self._offered = ""
        # What a new pattern is opened with, and an example starts it with:
        # until it is typed in, there is nothing of the author's to read.
        self._unfinished = ""
        # The text the rows under the pane are of, or None before any are.
        self._previewed = None
        # Translators: a pattern is one ModOp aimed at every asset of a shape.
        self.setWindowTitle(tr("Edit pattern") if op is not None else tr("New pattern"))
        self.resize(760, 560)
        self._build_ui()
        if self.copy_of is not None:
            self.lead.setText(self._copy_lead())
            self.editor.setPlainText(generator.laid_out_op(self.copy_of.source))
            self._recheck()
        elif op is None:
            self._unfinished = SKELETON % "//Values/"
            self.editor.setPlainText(self._unfinished)
            self._typed()
        elif self._holds_it:
            # Laid out as the mod file has it: stored, the op is often on
            # one line, and the box does not wrap, so all there was to see
            # of it was its first element (H2-28).
            self.editor.setPlainText(generator.laid_out_op(op.source))
            self._recheck()
        else:
            self._show_as_written()
            self._read_as_written()

    # -- layout ----------------------------------------------------------
    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)

        top = QHBoxLayout()
        self.lead = QLabel(tr(
            "One ModOp, as a mod writes it. A search in its Path aims it at "
            "every asset that matches."
        ))
        self.lead.setWordWrap(True)
        top.addWidget(self.lead, 1)
        # Somewhere to start from, for whoever has no search in mind yet.
        self.examples_button = QPushButton(tr("E&xamples"))
        menu = QMenu(self.examples_button)
        for path in EXAMPLES:
            menu.addAction(path, lambda path=path: self._start_from(path))  # i18n: keep - an XPath
        self.examples_button.setMenu(menu)
        top.addWidget(self.examples_button)
        outer.addLayout(top)

        self.editor = XmlPane()
        self.editor.set_editable(True)
        self.editor.setAccessibleName(tr("The ModOp"))
        self.editor.textChanged.connect(self._typed)
        outer.addWidget(self.editor, 2)

        # Of what is typed: why it cannot be read, or that the rows below
        # are not of it yet, beside the button that makes them so.
        row = QHBoxLayout()
        self.typed_note = QLabel("")
        self.typed_note.setWordWrap(True)
        row.addWidget(self.typed_note, 1, Qt.AlignmentFlag.AlignVCenter)
        self.refresh_button = QPushButton(tr("&Refresh preview"))
        self.refresh_button.setToolTip(tr(
            "Read the ModOp as typed and draw what it comes to. F5 or Ctrl+Enter."
        ))
        self.refresh_button.clicked.connect(self._recheck)
        row.addWidget(self.refresh_button)
        outer.addLayout(row)
        for keys in ("F5", "Ctrl+Return", "Ctrl+Enter"):  # i18n: keep - key names
            shortcut = QShortcut(QKeySequence(keys), self)
            shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
            shortcut.activated.connect(self.refresh_button.click)

        # Of the op the rows are of: how much it matches.
        self.summary = QLabel("")
        self.summary.setObjectName("SubHeading")
        self.summary.setWordWrap(True)
        outer.addWidget(self.summary)

        # Shown with a template's name typed where a search goes: the search
        # for that template's assets, there to be taken.
        self.offer_button = QPushButton(tr("Use that path"))
        self.offer_button.clicked.connect(self._take_offer)
        self.offer_button.setVisible(False)
        outer.addWidget(self.offer_button, 0, Qt.AlignmentFlag.AlignLeft)

        self.rows = OpRows(self.workspace)
        self.preview = self.rows.tree
        self.preview.setAccessibleName(tr("What this pattern comes to"))
        self.preview.setToolTip(tr(
            "The rows this pattern will have in the Mod changes panel."
        ))
        outer.addWidget(self.preview, 3)

        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        self.buttons.accepted.connect(self._accept)
        self.buttons.rejected.connect(self.reject)
        outer.addWidget(self.buttons)


    def _copy_lead(self) -> str:
        """What the dialog says of a copy of somebody else's pattern: where it
        goes, that theirs stays, and what the copy does not carry with it."""
        theirs = self.loadout.layer(self.copy_of.origin)
        into = self.loadout.layer(self.origin)
        said = [tr(
            "A copy of a pattern of %(mod)s, to go into %(patch)s. Theirs stays "
            "as it is and runs first. This one runs after it.",
            mod=theirs.label if theirs is not None else self.copy_of.origin,
            patch=into.label if into is not None else (
                self.origin or tr("this loadout's own mod")),
        )]
        if any(_has_condition(self.loadout.op(uid)) for uid in self.copy_of.within):
            said.append(tr(
                "Theirs is in a group that runs on a condition. This copy is "
                "not, so it runs whatever the condition says."
            ))
        return " ".join(said)

    def _show_as_written(self) -> None:
        """Turn the dialog into a reading of the op: nothing to type, nothing
        to save, and under it the rows the loadout has of it."""
        self.setWindowTitle(tr("ModOp"))
        for widget in (self.examples_button, self.refresh_button, self.typed_note):
            widget.setVisible(False)
        self.lead.setText(tr(
            "This is not one ModOp aimed by a search, so it is shown as its "
            "author wrote it. It cannot be changed here, and it is exported "
            "as it is."
        ))
        lines = (self.op.source or "").splitlines()
        shown = lines[:SHOWN_LINES]
        if len(lines) > SHOWN_LINES:
            shown.append(trn("… and %(n)s more line", "… and %(n)s more lines",
                             len(lines) - SHOWN_LINES))
        self.editor.set_editable(False)
        self.editor.setPlainText("\n".join(shown))
        self.preview.setToolTip(tr("What this ModOp comes to, asset by asset."))
        self.buttons.setStandardButtons(QDialogButtonBox.StandardButton.Close)

    # -- typing ------------------------------------------------------------
    def _typed(self, *_args) -> None:
        """Say of the text as it stands what costs nothing to say: that it
        is not XML, or that the rows are not of it.  The rows are left."""
        if not self._holds_it:
            return
        text = self.editor.toPlainText()
        current = text == self._previewed
        self._ok().setEnabled(current and self.found is not None)
        if not text.strip() or text == self._unfinished:
            self.refresh_button.setEnabled(False)
            self._note(tr("Type a ModOp to see what it matches."))
            return
        self.refresh_button.setEnabled(not current)
        stopped = typedops.not_xml(text)
        if stopped is not None:
            self._note(self._why_not(stopped), "error")
        elif current:
            self._note("")
        elif self._previewed is None:
            self._note(tr("Refresh the preview to see what this comes to."))
        else:
            self._note(tr(
                "Changed since the preview below. Refresh it to see what "
                "this comes to, and before saving."
            ), "warn")

    def _note(self, text: str, tone: str = "") -> None:
        self.typed_note.setText(text)
        theme.tint(self.typed_note, tone)

    def _put(self, text: str) -> None:
        """Type ``text`` over everything in the pane, as one step to undo."""
        cursor = self.editor.textCursor()
        cursor.select(QTextCursor.SelectionType.Document)
        cursor.insertText(text)
        self.editor.setFocus()

    def _start_from(self, path: str) -> None:
        self._unfinished = SKELETON % path.replace('"', "&quot;")
        self._put(self._unfinished)
        self._typed()

    def _say(self, text: str, tone: str = "") -> None:
        self.summary.setText(text)
        theme.tint(self.summary, tone)

    def _ok(self):
        return self.buttons.button(QDialogButtonBox.StandardButton.Ok)

    # -- asking the file --------------------------------------------------
    def _recheck(self) -> None:
        """Read what is typed, and draw what it comes to.  What cannot be
        read is said beside the pane, and the rows stay as they were."""
        if not self._holds_it:
            return
        text = self.editor.toPlainText()
        if not text.strip() or text == self._unfinished:
            self._typed()
            return

        found = typedops.read_one(self.workspace, self.origin, text)
        typed = [held.source for held in found.reaching + found.unread]
        if any(self._names_assets(source) for source in typed):
            # Whether or not it could be read: it is that asset's, and the
            # asset's XML view is where the mod's ops for it are typed.
            self._note(tr(
                "This ModOp names its asset, so it is not a pattern. Type it "
                "in the XML view of the asset it names."
            ), "error")
            return
        if found.error or found.loose or found.refused or not found.reaching:
            self._note(self._why_not(found), "error")
            return

        record = found.reaching[0]
        self._previewed = text
        self.found = None
        self.changes = [c for c in found.answers if c.op_uid == record.uid]
        self.matched = list(record.matched)
        self._offered = ""
        self.offer_button.setVisible(False)
        self.rows.show(record, self.changes)
        if modops.compiled_search(record.expression) is None:
            # Said here, and not left to the sweep: a search the file cannot
            # evaluate simply matches nothing, and "no asset matches" is the
            # wrong thing to tell somebody who has left a bracket open.
            self._say(tr(
                "That search cannot be read as an XPath. Check the brackets and quotes."
            ), "error")
        elif not self.matched and "/" not in record.expression:
            self._say_what_a_search_is(record.expression)
        else:
            self.found = found
            self._say(self._comes_to(record))
        self._typed()

    def _comes_to(self, record) -> str:
        """How much the op the rows are of matches, and what of that is not
        so today."""
        if not self.matched:
            said = tr(
                "No asset in your assets.xml matches. The pattern is allowed, "
                "but does nothing until a mod adds an asset that matches."
            )
        else:
            touched = len({change.guid for change in self.changes})
            said = trn(
                "Matches %(n)s asset, with %(changes)s across %(touched)s of them.",
                "Matches %(n)s assets, with %(changes)s across %(touched)s of them.",
                len(self.matched),
                changes=trn("%(n)s change", "%(n)s changes", len(self.changes)),
                touched=number(touched),
            )
            if record.caveat and not self.changes:
                said += "  " + tr_stored(record.caveat)
        if typedops.in_idle_group(self.loadout, self.op):
            said += "  " + tr(
                "Its group does not run as things stand, so this is what it "
                "would change if the group ran."
            )
        return said

    def _names_assets(self, source: str) -> bool:
        op = modops.read_op(source, game=self.loadout.game)
        return op is not None and bool(op.guids)

    def _why_not(self, found) -> str:
        """Why what is typed is not a pattern to draw, and where the reader
        stopped if it stopped: the caret is not moved under somebody typing."""
        stopped = where_reading_stopped(found)
        if stopped:
            return stopped
        if found.refused == typedops.NOT_ONE_OP:
            # Said of what was typed: a bare asset, an Include or a comment
            # was told where several ops or a group are typed.
            if found.ops > 1 or found.refused_at == "Group":
                return tr(
                    "A pattern is one ModOp. Several, or a group of them, are "
                    "typed in an asset's XML view."
                )
            if found.refused_at:
                return tr(
                    "A pattern is one ModOp, and the element typed is <%s>.",
                    found.refused_at,
                )
            return tr("A pattern is one ModOp, and none is typed here.")
        why = "; ".join(tr_stored(entry.why) for entry in found.unread if entry.why)
        if why:
            return tr("The Studio cannot read this ModOp: %s.", why)
        return tr("The Studio cannot read this ModOp.")

    def _read_as_written(self) -> None:
        """An op that is only shown: the rows the loadout has of it."""
        record = self.op
        held = self.loadout.changes_by_op(record.uid) if record.uid else []
        self.rows.show(record, self.loadout.ordered(held))
        self.preview.setVisible(bool(self.rows.count()))
        if record.kept:
            said = tr("Kept as the ModOp it came from, because %s.", tr_stored(record.kept))
        elif held:
            said = trn("The Studio read %(n)s change from it.",
                       "The Studio read %(n)s changes from it.", len(held))
        elif record.matched:
            said = trn("It matches %(n)s asset.", "It matches %(n)s assets.", len(record.matched))
        else:
            said = tr("It matches no asset in your assets.xml, so the Studio shows "
                      "nothing under it. What it does in the game is not read.")
        self._say(said)

    def _say_what_a_search_is(self, text: str) -> None:
        """For a Path with no step in it that matches nothing: it is not a path.

        A name on its own reads as an XPath, so it was answered like any
        search nothing matches: "allowed, but does nothing until a mod adds
        an asset that matches".  A template's name is the likeliest thing to
        type there, and the search for that template's assets is offered.
        """
        templates = {name.lower(): name for name in self.workspace.index.templates()}
        template = templates.get(text.lower(), "")
        if not template:
            self._say(tr(
                "%(typed)s is not a path. A search is a path through "
                "assets.xml, such as %(example)s.",
                typed=text, example=EXAMPLES[0],
            ))
            return
        self._offered = "//Asset[Template='%s']/Values" % template
        # The path on a line of its own: wrapped, it broke after a slash.
        self._say(tr(
            "%(template)s is a template. A search is a path, and this one "
            "matches the assets made from it:\n%(path)s",
            template=template, path=self._offered,
        ))
        self.offer_button.setVisible(True)

    def _take_offer(self) -> None:
        """Put the offered search where the name was typed, and look it up
        at once."""
        offered = self._offered
        if not offered:
            return
        text = self.editor.toPlainText()
        root = modops.read_op(text, game=self.loadout.game)
        typed = root.path if root is not None else ""
        # As typed, between the quotes it was typed in.
        for quote in ('"', "'"):
            was = "%s%s%s" % (quote, typed, quote)
            if typed and text.count(was) == 1:
                other = "'" if quote == '"' else '"'
                self._put(text.replace(was, quote + offered.replace(quote, other) + quote))
                break
        else:
            return
        self._recheck()

    # -- result -----------------------------------------------------------
    def _accept(self) -> None:
        # Only what the rows are of: OK is off for anything typed since.
        if self.found is None or self.editor.toPlainText() != self._previewed:
            return
        self.result_op = typedops.store_one(
            self.workspace, self.origin, self.found, self.op
        )
        self.accept()
