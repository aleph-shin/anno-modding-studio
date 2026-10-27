"""Which language the Studio speaks, chosen once as it starts.

The choice lives in the settings file: a language code, or nothing for
"whatever Windows is shown in".  It is applied before the first window is
built and never again while the Studio runs, because every sentence is
translated as it is made -- a window already open would stay in the old
language, half of it anyway.  So changing it says so, and waits for a restart.

Two things are installed.  The Studio's own catalog, through
:mod:`..core.i18n`, and Qt's, for the words Qt itself draws: the standard
buttons (Close, Cancel), the right-click menu of a text field, the file dialog
where it is Qt's rather than Windows'.  PySide6 ships Qt's in every language
the Studio is likely to add.
"""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import QLibraryInfo, QLocale, QTranslator

from ..core import i18n

LANGUAGE_KEY = "language"

# Kept alive for as long as it is installed: Qt holds only a pointer to it.
_qt_translator: Optional[QTranslator] = None


def saved_choice(settings) -> str:
    """The language picked in Settings, or "" for the one Windows uses."""
    return str(settings.value(LANGUAGE_KEY) or "").strip()


def system_language() -> str:
    """The language Windows shows itself in, as a bare code: "de", "en".

    The display language rather than the regional format, which is what a
    person means by the language their computer is in; an English Windows with
    German dates and currency is still English to read.
    """
    languages = QLocale.system().uiLanguages()
    first = languages[0] if languages else QLocale.system().name()
    return first.replace("_", "-").split("-")[0].lower() or i18n.SOURCE


def resolve(choice: str) -> str:
    """The language a choice comes to: one there is a catalog for, or English."""
    wanted = choice or system_language()
    known = {code for code, _label in i18n.available()}
    return wanted if wanted in known else i18n.SOURCE


def apply(app, settings) -> str:
    """Install the language ``settings`` asks for; the code it came to."""
    global _qt_translator
    code = resolve(saved_choice(settings))
    if not i18n.install(code):
        code = i18n.SOURCE
    if _qt_translator is not None:
        app.removeTranslator(_qt_translator)
        _qt_translator = None
    if code != i18n.SOURCE:
        translator = QTranslator(app)
        folder = QLibraryInfo.path(QLibraryInfo.LibraryPath.TranslationsPath)
        if translator.load("qtbase_%s" % code, folder):
            app.installTranslator(translator)
            _qt_translator = translator
    return code
