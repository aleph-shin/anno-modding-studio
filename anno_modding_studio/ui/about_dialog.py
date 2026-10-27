"""About the Studio: what it is, which version, under what licence, and the
facts a bug report needs.

It was a ``QMessageBox.about`` that named no version, so a person reporting
a bug could not say which Studio they had, and it had no room for the
licence the Studio is published under or for the notices of what it ships.
"""

from __future__ import annotations

import platform
import sys

import PySide6
from lxml import etree
from PySide6.QtCore import Qt, qVersion
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QDialogButtonBox,
    QLabel,
    QMessageBox,
    QVBoxLayout,
)

from .. import REPO_URL, __version__, source_url
from ..core import wine
from ..core.changes import APP_NAME
from ..core.dataversion import DATA_VERSION
from ..core.i18n import tr
from ..core.reader import READER_VERSION
from .widgets import flash_button_feedback, tab_as_laid_out

# Where the licence, the notices and the credits are read from: the
# repository's copies, as this Studio has them -- a built one's at its
# release's tag, whose zip carries the same files beside the program.
DOCUMENTS_URL = source_url()
LICENCE = DOCUMENTS_URL + "LICENSE"
NOTICES = DOCUMENTS_URL + "THIRD-PARTY-NOTICES.md"
# GPLv3's "How to Apply These Terms": the copyright line goes with the notice.
COPYRIGHT = "Copyright © 2026 aleph-shin"
CREDITS = DOCUMENTS_URL + "docs/credits.md"


def _dotted(numbers) -> str:
    return ".".join(str(number) for number in numbers)


def version_info() -> str:
    """What a bug report needs to say about this Studio, one fact to a
    line.  English in every language: it is for whoever reads the report."""
    if sys.platform == "win32":
        system = "Windows: %s (%s)" % (platform.release(), platform.version())
    else:
        system = "System: %s" % platform.platform()
    lines = (
        "%s %s" % (APP_NAME, __version__),
        "Reader version: %d" % READER_VERSION,
        "Data version: %d" % DATA_VERSION,
        "Python: %s (%s, %s)" % (
            platform.python_version(), platform.python_implementation(),
            platform.architecture()[0],
        ),
        "Qt: %s, PySide6: %s" % (qVersion(), PySide6.__version__),
        "lxml: %s, libxml2: %s" % (_dotted(etree.LXML_VERSION), _dotted(etree.LIBXML_VERSION)),
        system,
        "Wine: %s" % (wine.version() or "no"),
        "Frozen: %s" % ("yes" if getattr(sys, "frozen", False) else "no"),
    )
    return "\n".join(lines) + "\n"


def _linked(text: str) -> QLabel:
    # The Studio's own markup, with nothing from outside in it; every other
    # label is plain (widgets.external_text_stays_plain).
    label = QLabel(text)
    label.setTextFormat(Qt.TextFormat.RichText)
    label.setWordWrap(True)
    label.setOpenExternalLinks(True)
    label.setTextInteractionFlags(Qt.TextInteractionFlag.TextBrowserInteraction)
    return label


class AboutDialog(QDialog):
    """Help > About.  No heading: its title names it."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr("About %s", APP_NAME))
        self.setMinimumWidth(480)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 12)
        layout.setSpacing(12)

        self.version_label = QLabel(tr("Version %s", __version__))
        layout.addWidget(self.version_label)
        self.what = _linked(tr(
            "<p>A workshop for Anno mods. It plays a set of mods, changes "
            "a mod you downloaded, and builds a mod of your own.</p>"
            "<p>It reads the game's files and writes each change as its "
            "own narrow ModOp, so mods that change different parts of the "
            "same asset work together.</p>"
            "<p>Mods are looked up, installed and published through "
            "<a href=\"https://mod.io\">mod.io</a>, with your own mod.io "
            "account. The mod.io name and logo belong to mod.io and are "
            "used under its API terms.</p>"
        ))
        layout.addWidget(self.what)
        self.licence = _linked(tr(
            '%(copyright)s. Licensed under the <a href="%(licence)s">GNU General '
            'Public License v3 or later</a>. It comes with ABSOLUTELY NO WARRANTY.',
            copyright=COPYRIGHT, licence=LICENCE,
        ))
        layout.addWidget(self.licence)
        self.links = _linked(tr(
            '<a href="%(notices)s">Third-party notices</a> &nbsp;·&nbsp; '
            '<a href="%(credits)s">Credits</a> &nbsp;·&nbsp; '
            '<a href="%(source)s">Source code on GitHub</a>',
            notices=NOTICES, credits=CREDITS, source=REPO_URL,
        ))
        layout.addWidget(self.links)
        self.disclaimer = QLabel(tr(
            "Not affiliated with Ubisoft or mod.io. Anno is a trademark of Ubisoft."
        ))
        self.disclaimer.setObjectName("StepHint")
        self.disclaimer.setWordWrap(True)
        layout.addWidget(self.disclaimer)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.button(QDialogButtonBox.StandardButton.Close).setText(tr("&Close"))
        self.copy_button = buttons.addButton(
            tr("Copy &version info"), QDialogButtonBox.ButtonRole.ActionRole
        )
        self.copy_button.setToolTip(tr("Copies the versions a bug report needs."))
        self.copy_button.clicked.connect(self.copy_version_info)
        # The LGPL asks for Qt's own notice where the Studio shows its copyright.
        self.about_qt_button = buttons.addButton(
            tr("About &Qt"), QDialogButtonBox.ButtonRole.ActionRole
        )
        self.about_qt_button.setToolTip(tr("Shows the Qt version and Qt's licence."))
        self.about_qt_button.clicked.connect(lambda: QMessageBox.aboutQt(self))
        buttons.rejected.connect(self.reject)
        tab_as_laid_out(buttons)
        layout.addWidget(buttons)

    def copy_version_info(self) -> None:
        QApplication.clipboard().setText(version_info())
        flash_button_feedback(self.copy_button)
