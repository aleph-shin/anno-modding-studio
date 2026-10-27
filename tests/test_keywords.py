"""Keywords: a term the Studio uses in one sense, marked where a sentence says
it, and shown in a box or a help panel as a term to point at for its meaning.
"""

from __future__ import annotations

import re
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest import mock

import _isolation  # first: keeps the suite out of real files under any runner

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

import translations  # noqa: E402
from PySide6.QtCore import QPoint, QSize, Qt  # noqa: E402
from PySide6.QtWidgets import QApplication, QLabel, QMessageBox, QWidget  # noqa: E402

from anno_modding_studio.core import i18n, keywords, po  # noqa: E402
from anno_modding_studio.core.i18n import tr, tr_kw, trn, trn_kw  # noqa: E402
from anno_modding_studio.ui import help_tip, widgets  # noqa: E402
from anno_modding_studio.ui.help_tip import HelpTip, KeywordTips  # noqa: E402

_app = QApplication.instance() or QApplication([])

GERMAN = textwrap.dedent('''\
    msgid ""
    msgstr ""
    "Language: de\\n"
    "Content-Type: text/plain; charset=UTF-8\\n"
    "Plural-Forms: nplurals=2; plural=(n != 1);\\n"

    msgid "Your [[patch]] for %s."
    msgstr "Dein [[patch|Patch]] für %s."

    msgid "A patch is a mod of yours that loads straight after somebody else's mod and holds your changes to it. An update to their mod leaves your changes in place, and the patch goes wherever you use their mod."
    msgstr "Ein Patch ist eine Mod von dir."
''')


class TestTheMarks(unittest.TestCase):
    def test_tr_shows_the_words_and_tr_kw_keeps_the_term(self):
        self.assertEqual(tr("Make a [[patch]] for it."), "Make a patch for it.")
        self.assertEqual(tr("Your [[patch|patches]]."), "Your patches.")
        kept = tr_kw("Your [[patch|patches]]  and [[project]].")
        self.assertEqual(widgets.terms_in_text(kept), ["patch", "project"])
        self.assertEqual(i18n.unmarked(kept), "Your patches  and project.")

    def test_a_count_keeps_them_too(self):
        self.assertEqual(trn("%(n)s [[patch]]", "%(n)s [[patch|patches]]", 2), "2 patches")
        kept = trn_kw("%(n)s [[patch]]", "%(n)s [[patch|patches]]", 1)
        self.assertEqual(widgets.terms_in_text(kept), ["patch"])

    def test_a_value_is_never_read_as_a_mark(self):
        """A mod may be called anything, brackets and all."""
        said = tr_kw("Your [[patch]] for %s.", "[[project]] Mod")
        self.assertEqual(widgets.terms_in_text(said), ["patch"])
        self.assertTrue(said.endswith("for [[project]] Mod."))

    def test_a_translation_marks_its_own_words(self):
        folder = Path(tempfile.mkdtemp(prefix="ams-locale-"))
        (folder / "de.po").write_text(GERMAN, encoding="utf-8")
        self.assertTrue(i18n.install("de", folder))
        self.addCleanup(i18n.install, i18n.SOURCE)
        self.assertEqual(tr("Your [[patch]] for %s.", "X"), "Dein Patch für X.")
        kept = tr_kw("Your [[patch]] for %s.", "X")
        self.assertEqual(widgets.terms_in_text(kept), ["patch"])
        self.assertIn("Patch", i18n.unmarked(kept))
        self.assertEqual(keywords.meaning("patch"), "Ein Patch ist eine Mod von dir.")


class TestTheKeywords(unittest.TestCase):
    def test_each_meaning_starts_with_its_term_and_is_short(self):
        for key, said in keywords.KEYWORDS.items():
            with self.subTest(key=key):
                self.assertIn(key, said.split(".")[0].lower())
                self.assertLessEqual(len(said.split()), 60)

    def test_each_is_in_the_glossary(self):
        """The glossary says what each term means for whoever writes the text;
        a keyword is a term it has."""
        glossary = (ROOT / "docs" / "standards" / "design-standards.md").read_text(encoding="utf-8")
        for key in keywords.KEYWORDS:
            with self.subTest(key=key):
                self.assertRegex(glossary, r"\n\| \*\*%s\*\* \|" % re.escape(key))

    def test_an_unknown_key_has_no_meaning(self):
        self.assertEqual(keywords.meaning("nonsense"), "")


class TestTheCatalogCheck(unittest.TestCase):
    def entry(self, msgid, *strings, plural=""):
        return po.Entry(msgid=msgid, plural=plural, strings=list(strings))

    def test_a_translation_marks_the_same_keywords(self):
        self.assertEqual(translations.unkept_terms(
            self.entry("Your [[patch]].", "Dein [[patch|Patch]].")), [])
        self.assertEqual(len(translations.unkept_terms(
            self.entry("Your [[patch]].", "Dein Patch."))), 1)
        self.assertEqual(len(translations.unkept_terms(
            self.entry("Your [[patch]].", "Dein [[project|Patch]]."))), 1)
        self.assertEqual(len(translations.unkept_terms(self.entry(
            "%(n)s [[patch]]", "%(n)s [[patch]]", "%(n)s Patches", plural="%(n)s [[patch]]"))), 1)

    def test_the_extractor_refuses_an_unknown_keyword_and_one_in_kept_english(self):
        source = textwrap.dedent('''
            from anno_modding_studio.core.i18n import STORED, tr_kw, tr_noop
            def f():
                tr_kw("A [[pach]] here")
                tr_kw("A [[patch]] here")
            X = tr_noop("A [[patch]] kept", context=STORED)
        ''')
        finder = translations._Finder("x.py", source)
        import ast
        finder.visit(ast.parse(source), False)
        said = " ".join(str(problem) for problem in finder.problems)
        self.assertIn("pach is no keyword", said)
        self.assertIn("written to files", said)
        self.assertIn("A [[patch]] here", [message.msgid for message in finder.messages])


class TestInABox(unittest.TestCase):
    def setUp(self):
        self.parent = QWidget()
        self.addCleanup(self.parent.deleteLater)

    def box(self, text):
        box = QMessageBox(self.parent)
        widgets.show_box_text(box, text)
        return box

    def test_a_keyword_is_a_link_that_goes_nowhere_but_its_meaning(self):
        box = self.box(tr_kw("No [[project]] keeps %s.", widgets.named("<Mine>")))
        self.assertIn('<a href="term:project">project</a>', box.text())
        self.assertIn('<span style="font-weight:600">&lt;Mine&gt;</span>', box.text())
        label = box.findChild(QLabel, "qt_msgbox_label")
        self.assertFalse(label.openExternalLinks())
        self.assertTrue(label.textInteractionFlags()
                        & Qt.TextInteractionFlag.LinksAccessibleByKeyboard)
        self.assertIn(keywords.meaning("project"), label.accessibleDescription())
        self.assertIsInstance(label._keyword_tips, KeywordTips)

    def test_a_box_with_none_is_left_as_it_was(self):
        box = self.box(tr_kw("Nothing to point at."))
        label = box.findChild(QLabel, "qt_msgbox_label")
        self.assertIsNone(getattr(label, "_keyword_tips", None))

    def test_a_term_of_two_words_comes_back_whole(self):
        markup = widgets.box_markup(tr_kw("Into a [[generated copy]]."))
        self.assertIn('<a href="term:generated%20copy">generated copy</a>', markup)
        self.assertEqual(help_tip._term_of("term:generated%20copy"), "generated copy")

    def test_its_text_reads_back_plain(self):
        text = tr_kw("Your [[patch]] for %s.", widgets.named("Mine"))
        self.assertEqual(widgets.plain_text(text), "Your patch for Mine.")
        self.assertEqual(_isolation.box_text(self.box(text)), "Your patch for Mine.")


class TestTheMeaning(unittest.TestCase):
    def setUp(self):
        self.label = QLabel()
        self.addCleanup(self.label.deleteLater)
        text = tr_kw("Your [[patch]].")
        widgets.show_rich(self.label, widgets.box_markup(text))
        self.tips = KeywordTips.watch(self.label, text)
        self.addCleanup(self.tips.close)

    def test_a_click_pins_it_and_a_second_closes_it(self):
        self.label.linkActivated.emit("term:patch")
        self.assertTrue(self.tips.is_open())
        self.assertTrue(self.tips.pinned)
        self.assertEqual(self.tips.panel.body.text(), keywords.meaning("patch"))
        self.label.linkActivated.emit("term:patch")
        self.assertFalse(self.tips.is_open())

    def test_resting_on_it_opens_it_and_leaving_closes_it(self):
        self.label.linkHovered.emit("term:patch")
        self.assertTrue(self.tips._hover.isActive())
        self.tips._hover.timeout.emit()
        self.assertTrue(self.tips.is_open())
        self.assertFalse(self.tips.pinned)
        self.label.linkHovered.emit("")
        with mock.patch.object(help_tip.QCursor, "pos", return_value=QPoint(-5000, -5000)):
            self.tips._close_if_left()
        self.assertFalse(self.tips.is_open())

    def test_esc_closes_it(self):
        self.tips.open("patch", pinned=True, at=QPoint(0, 0))
        from PySide6.QtTest import QTest
        QTest.keyClick(self.label, Qt.Key.Key_Escape)
        self.assertFalse(self.tips.is_open())

    def test_it_opens_above_where_below_would_cover_a_button(self):
        """R3-8, hands-on run 3: open below the keyword, over the box's own
        buttons, the pointer on its way to one went into the panel."""
        from PySide6.QtWidgets import QPushButton, QVBoxLayout

        window = QWidget()
        self.addCleanup(window.deleteLater)
        layout = QVBoxLayout(window)
        label, button = QLabel("Take it out of Main?"), QPushButton("Take it out")
        layout.addWidget(label)
        layout.addWidget(button)
        window.move(300, 300)
        window.show()
        self.addCleanup(window.close)
        tips = KeywordTips(label)
        at = label.mapToGlobal(QPoint(5, label.height() - 2))
        size = QSize(max(200, button.width()), 120)
        self.assertLess(tips._place(at, size).y(), at.y())
        button.hide()
        self.assertGreater(tips._place(at, size).y(), at.y())

    def test_a_link_that_is_no_keyword_opens_nothing(self):
        self.label.linkActivated.emit("https://mod.io")
        self.tips.open("nonsense", pinned=True, at=QPoint(0, 0))
        self.assertFalse(self.tips.is_open())


class TestInAHelpPanel(unittest.TestCase):
    def test_its_keywords_are_links_and_a_screen_reader_hears_their_meaning(self):
        tip = HelpTip(tr_kw("Other mods get a [[patch]]."), "&Author")
        self.addCleanup(tip.deleteLater)
        self.addCleanup(tip.close_panel)
        self.assertEqual(tip.accessibleDescription(),
                         "Other mods get a patch.\n\n" + keywords.meaning("patch"))
        tip.open()
        body = tip._panel.body
        self.assertEqual(body.textFormat(), Qt.TextFormat.RichText)
        self.assertIn('href="term:patch"', body.text())
        self.assertIsInstance(body._keyword_tips, KeywordTips)

    def test_one_without_is_plain(self):
        tip = HelpTip(tr("Other mods get a <patch>."), "&Author")
        self.addCleanup(tip.deleteLater)
        self.addCleanup(tip.close_panel)
        tip.open()
        self.assertEqual(tip._panel.body.textFormat(), Qt.TextFormat.PlainText)
        self.assertEqual(tip._panel.body.text(), "Other mods get a <patch>.")


if __name__ == "__main__":
    unittest.main()
