"""ModOps XML, shown: the colouring, and a dialog that holds some to read.

The Export tab shows a whole mod's document.  The changes list is where one
row of it is in front of you, and what that row is written as is a question
asked there -- so the same view is offered from the row, on its own.
"""

from __future__ import annotations

from PySide6.QtCore import QRegularExpression
from PySide6.QtGui import (
    QColor,
    QGuiApplication,
    QSyntaxHighlighter,
    QTextCharFormat,
)
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QLabel,
    QPlainTextEdit,
    QVBoxLayout,
)

from ..core.i18n import tr
from . import theme
from .widgets import flash_button_feedback, tab_as_laid_out


class XmlHighlighter(QSyntaxHighlighter):
    """Just enough highlighting to make the preview scannable."""

    def __init__(self, document):
        super().__init__(document)
        self._build_rules()
        theme.notifier().changed.connect(self._retheme)

    def _retheme(self) -> None:
        self._build_rules()
        self.rehighlight()

    def _build_rules(self) -> None:
        self.rules = []

        comment = QTextCharFormat()
        comment.setForeground(QColor(theme.SYN_COMMENT))
        comment.setFontItalic(True)
        self.rules.append((QRegularExpression(r"<!--[^\v]*?-->"), comment))

        tag = QTextCharFormat()
        tag.setForeground(QColor(theme.SYN_TAG))
        self.rules.append((QRegularExpression(r"</?\b[A-Za-z_][\w.-]*"), tag))
        self.rules.append((QRegularExpression(r"/?>"), tag))

        attr = QTextCharFormat()
        attr.setForeground(QColor(theme.SYN_ATTR))
        self.rules.append((QRegularExpression(r"\b[A-Za-z_][\w.-]*(?=\=)"), attr))

        value = QTextCharFormat()
        value.setForeground(QColor(theme.SYN_VALUE))
        self.rules.append((QRegularExpression(r'"[^"]*"'), value))

        # A mod's setting, where the loader reads one: in its own colour,
        # and underlined, so that it is told apart where the colours are a
        # contrast theme's and the same as a value's.
        option = QTextCharFormat()
        option.setForeground(QColor(theme.SYN_OPTION))
        option.setFontUnderline(True)
        self.rules.append((
            QRegularExpression(r"(?<=[\"'\s(!])\$[A-Za-z_][\w\-.]*(?=[^\"'<>]*[\"'])"), option,
        ))

    def highlightBlock(self, text) -> None:
        for pattern, fmt in self.rules:
            it = pattern.globalMatch(text)
            while it.hasNext():
                match = it.next()
                self.setFormat(match.capturedStart(), match.capturedLength(), fmt)
        # A number that names an asset, where the pane has said which do:
        # underlined in dots, in whatever colour it is, as a link is.
        for start, length in getattr(self.currentBlockUserData(), "spans", ()):
            for at in range(start, start + length):
                linked = QTextCharFormat(self.format(at))
                linked.setUnderlineStyle(QTextCharFormat.UnderlineStyle.DotLine)
                self.setFormat(at, 1, linked)


class XmlDialog(QDialog):
    """Some ModOps XML to read and to copy: ``heading`` says what of, and
    ``note`` anything about it the XML does not say itself."""

    def __init__(self, heading: str, xml: str, note: str = "", parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr("ModOps XML"))
        self.setMinimumSize(760, 520)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 14, 16, 12)
        outer.setSpacing(8)

        self.heading = QLabel(heading)
        self.heading.setObjectName("SubHeading")
        self.heading.setWordWrap(True)
        outer.addWidget(self.heading)

        self.note = QLabel(note)
        self.note.setObjectName("StepHint")
        self.note.setWordWrap(True)
        self.note.setVisible(bool(note))
        outer.addWidget(self.note)

        self.text = QPlainTextEdit(xml)
        self.text.setReadOnly(True)
        self.text.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.text.setAccessibleName(heading)
        theme.monospace(self.text)
        # The document's from here on, as its parent: it needs no name.
        XmlHighlighter(self.text.document())
        outer.addWidget(self.text, 1)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        self.copy_button = buttons.addButton(
            tr("&Copy"), QDialogButtonBox.ButtonRole.ActionRole
        )
        self.copy_button.clicked.connect(self._copy)
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self.accept)
        tab_as_laid_out(buttons)
        outer.addWidget(buttons)

    def _copy(self) -> None:
        QGuiApplication.clipboard().setText(self.text.toPlainText())
        flash_button_feedback(self.copy_button)
