"""mod.io's mark, on every window that talks to mod.io.

The API terms ask for it in as many words (clause 3.3): include mod.io's
branding at all times you are using the mod.io API, with the material at
https://mod.io/about and a licence to use it for exactly that.  So every
dialog that reads from or sends to mod.io carries mod.io's own logo, at the
height of a line of text, as a link to mod.io -- with a word in front saying
what mod.io is to this window.

The logo is mod.io's, from its press kit, in the version the colour scheme
names for its background. It is drawn from the SVG at the screen's pixel
ratio. If it is missing the name is shown in its place, so the mark is never
absent.
"""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import QHBoxLayout, QLabel, QWidget

from ..core.i18n import tr
from . import theme

MODIO_URL = "https://mod.io"
# The logo's height beside a line of text, in logical pixels.
LOGO_HEIGHT = 20


def logo_pixmap() -> QPixmap:
    """mod.io's logo for the scheme in effect, or a null pixmap without it."""
    renderer = QSvgRenderer(str(theme.logo_path()))
    if not renderer.isValid():
        return QPixmap()
    screen = QGuiApplication.primaryScreen()
    ratio = screen.devicePixelRatio() if screen is not None else 1.0
    natural = renderer.defaultSize()
    width = LOGO_HEIGHT * natural.width() / max(natural.height(), 1)
    pixmap = QPixmap(round(width * ratio), round(LOGO_HEIGHT * ratio))
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    renderer.render(painter)
    painter.end()
    pixmap.setDevicePixelRatio(ratio)
    return pixmap


def branding(what: Optional[str] = None) -> QWidget:
    """``<what>`` beside mod.io's logo, the logo a link to mod.io itself.

    ``what`` arrives translated; left out, it is "Powered by".
    """
    row = QWidget()
    row.setObjectName("ModioBrand")
    layout = QHBoxLayout(row)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(6)

    # Translators: shown just before mod.io's logo.
    lead = QLabel(what if what is not None else tr("Powered by"))
    theme.tint(lead, "text-dim")
    layout.addWidget(lead)

    pixmap = logo_pixmap()
    if pixmap.isNull():
        mark = QLabel()
        mark.setTextFormat(Qt.TextFormat.RichText)
        mark.setText(
            '<a href="%s" style="font-weight:600; text-decoration:none">mod.io</a>'  # i18n: keep - a name
            % MODIO_URL
        )
        mark.setOpenExternalLinks(True)
        mark.setTextInteractionFlags(Qt.TextInteractionFlag.TextBrowserInteraction)
    else:
        mark = _Logo(pixmap)
    mark.setToolTip(tr("Mods are read from and sent to mod.io, at %s.", MODIO_URL))
    mark.setCursor(Qt.CursorShape.PointingHandCursor)
    layout.addWidget(mark)
    layout.addStretch(1)
    return row


class _Logo(QLabel):
    """The logo, a link to mod.io, redrawn when the scheme changes.

    A scheme names its own version of the logo. The redraw is a method of
    the label so that Qt drops the connection when the label goes: a lambda
    stayed connected and raised on every later change of scheme.
    """

    def __init__(self, pixmap: QPixmap) -> None:
        super().__init__()
        self.setPixmap(pixmap)
        theme.notifier().changed.connect(self._retheme)

    def _retheme(self) -> None:
        self.setPixmap(logo_pixmap())

    def mousePressEvent(self, _event) -> None:
        _open()


def _open() -> None:
    from PySide6.QtCore import QUrl
    from PySide6.QtGui import QDesktopServices

    QDesktopServices.openUrl(QUrl(MODIO_URL))
