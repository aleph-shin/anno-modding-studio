"""The Studio's own words, looked up in the reader's language.

Three layers, each tested on its own: the runtime that looks a sentence up
(core/i18n.py), the extractor that finds every marked sentence and refuses the
ones no translation could hold (tools/translations.py), and the repository
itself -- whose catalogs have to match its code, and whose translations have
to fill in what their English does.
"""

from __future__ import annotations

import sys
import tempfile
import textwrap
import time
import unittest
from pathlib import Path

import _isolation  # noqa: F401 -- first: keeps the suite out of real files under any runner

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

import make_sample_assets  # noqa: E402
import translations  # noqa: E402

from anno_modding_studio.core import i18n, po  # noqa: E402
from anno_modding_studio.core.i18n import (  # noqa: E402
    bullets,
    date_text,
    join_list,
    number,
    size_text,
    tr,
    tr_stored,
    trn,
)

GERMAN = r'''
msgid ""
msgstr ""
"Language: de\n"
"Content-Type: text/plain; charset=UTF-8\n"
"Plural-Forms: nplurals=2; plural=(n != 1);\n"
"X-Language-Label: Deutsch (maschinell übersetzt)\n"

msgid "Settings"
msgstr "Einstellungen"

msgctxt "verb"
msgid "Open"
msgstr "Öffnen"

#, python-format
msgid "Saved %s."
msgstr "%s gespeichert."

#, python-format
msgid "%(mod)s overrides %(other)s."
msgstr "%(other)s wird von %(mod)s überschrieben."

#, python-format
msgid "%(n)s mod"
msgid_plural "%(n)s mods"
msgstr[0] "Ein Mod"
msgstr[1] "%(n)s Mods"

#, python-format
msgid "Broken %s."
msgstr "Kaputt %(what)s."

#, fuzzy
msgid "Close"
msgstr "Schließen"

msgid "Untranslated"
msgstr ""

msgctxt "digit grouping mark"
msgid ","
msgstr "."

msgctxt "decimal mark"
msgid "."
msgstr ","

#, python-format
msgid "%(rest)s and %(last)s"
msgstr "%(rest)s und %(last)s"

msgctxt "month"
msgid "Sep"
msgstr "Sept."

#, python-format
msgctxt "date"
msgid "%(day)s %(month)s %(year)s"
msgstr "%(day)s. %(month)s %(year)s"
'''


class _German(unittest.TestCase):
    """With the catalog above installed, and English again afterwards."""

    def setUp(self):
        folder = Path(tempfile.mkdtemp(prefix="ams-locale-"))
        (folder / "de.po").write_text(GERMAN, encoding="utf-8")
        self.folder = folder
        self.assertTrue(i18n.install("de", folder))
        self.addCleanup(i18n.install, i18n.SOURCE)


class TestEnglish(unittest.TestCase):
    """What every test elsewhere sees: nothing installed, the source as it is."""

    def test_text_comes_back_as_written(self):
        self.assertEqual(i18n.language(), "en")
        self.assertEqual(tr("Settings"), "Settings")

    def test_and_is_filled_in_positionally_or_by_name(self):
        self.assertEqual(tr("Saved %s.", "x"), "Saved x.")
        self.assertEqual(tr("%(a)s then %(b)s", a=1, b=2), "1 then 2")

    def test_a_lone_percent_needs_nothing_doubled_when_nothing_is_filled(self):
        self.assertEqual(tr("100% sure"), "100% sure")

    def test_both_kinds_of_value_at_once_is_a_mistake(self):
        with self.assertRaises(TypeError):
            tr("%s", "a", b=1)

    def test_english_that_does_not_fit_its_values_still_raises(self):
        # The fallback is for a broken translation, not for broken English.
        with self.assertRaises(TypeError):
            tr("%s and %s", "one")

    def test_counts_choose_their_form(self):
        self.assertEqual(trn("One mod", "%(n)s mods", 1), "One mod")
        self.assertEqual(trn("One mod", "%(n)s mods", 0), "0 mods")
        self.assertEqual(trn("%(n)s mod in %(p)s", "%(n)s mods in %(p)s", 3, p="x"),
                         "3 mods in x")

    def test_numbers_are_grouped(self):
        self.assertEqual(number(1234567), "1,234,567")
        self.assertEqual(number(0.994, 2), "0.99")
        self.assertEqual(number(12), "12")

    def test_lists_read_as_a_sentence(self):
        self.assertEqual(join_list([]), "")
        self.assertEqual(join_list(["A"]), "A")
        self.assertEqual(join_list(["A", "B"]), "A and B")
        self.assertEqual(join_list(["A", "B", "C"]), "A, B and C")
        self.assertEqual(join_list(["A", "B"], "or"), "A or B")
        with self.assertRaises(ValueError):
            join_list(["A", "B"], "but")

    def test_a_bulleted_list_says_where_it_stops(self):
        self.assertEqual(bullets(["A", "B"]), "  • A\n  • B")
        self.assertEqual(bullets(["A", "B", "C"], 2), "  • A\n  • B\n  • and 1 more")
        self.assertEqual(bullets(["A", "B"], 2), "  • A\n  • B")
        self.assertEqual(bullets([]), "")

    def test_sizes_read_in_the_unit_that_suits_them(self):
        self.assertEqual(size_text(0), "0 KB")
        self.assertEqual(size_text(100), "1 KB")
        self.assertEqual(size_text(12 * 1024), "12 KB")
        self.assertEqual(size_text(int(4.25 * 1024 * 1024)), "4.2 MB")
        # Past what a signed thirty-two bits holds, which a progress bar cannot.
        self.assertEqual(size_text(3 * 1024 ** 3), "3.0 GB")

    def test_dates_read_as_they_did_before_there_was_a_catalog(self):
        stamp = time.mktime((2026, 9, 5, 14, 30, 0, 0, 0, -1))
        self.assertEqual(date_text(stamp), time.strftime("%d %b %Y", time.localtime(stamp)))
        self.assertEqual(date_text(stamp, year=False), "05 Sep")
        self.assertEqual(date_text(stamp, clock=True), "05 Sep 2026 14:30")
        self.assertEqual(date_text(stamp, year=False, clock=True), "05 Sep 14:30")


class TestGerman(_German):
    def test_a_translated_sentence_is_shown_in_german(self):
        self.assertEqual(i18n.language(), "de")
        self.assertEqual(tr("Settings"), "Einstellungen")

    def test_a_context_tells_two_uses_of_the_same_english_apart(self):
        self.assertEqual(tr("Open", context="verb"), "Öffnen")
        self.assertEqual(tr("Open"), "Open")

    def test_values_go_where_the_translation_puts_them(self):
        self.assertEqual(tr("Saved %s.", "x"), "x gespeichert.")
        self.assertEqual(tr("%(mod)s overrides %(other)s.", mod="A", other="B"),
                         "B wird von A überschrieben.")

    def test_a_form_may_leave_the_count_out(self):
        self.assertEqual(trn("%(n)s mod", "%(n)s mods", 1), "Ein Mod")
        self.assertEqual(trn("%(n)s mod", "%(n)s mods", 1234), "1.234 Mods")

    def test_a_broken_translation_costs_a_sentence_of_english_not_an_exception(self):
        self.assertEqual(tr("Broken %s.", "x"), "Broken x.")

    def test_fuzzy_and_blank_entries_fall_back_to_english(self):
        self.assertEqual(tr("Close"), "Close")
        self.assertEqual(tr("Untranslated"), "Untranslated")

    def test_numbers_lists_and_dates_follow_the_catalog(self):
        self.assertEqual(number(1234.5, 1), "1.234,5")
        self.assertEqual(join_list(["A", "B", "C"]), "A, B und C")
        stamp = time.mktime((2026, 9, 5, 12, 0, 0, 0, 0, -1))
        self.assertEqual(date_text(stamp), "05. Sept. 2026")

    def test_the_catalog_names_its_language_in_itself(self):
        self.assertEqual(i18n.available(self.folder),
                         [("en", "English"), ("de", "Deutsch (maschinell übersetzt)")])

    def test_a_language_with_no_catalog_is_english(self):
        self.assertFalse(i18n.install("fr", self.folder))
        self.assertEqual(i18n.language(), "en")
        self.assertEqual(tr("Settings"), "Settings")


STORED_GERMAN = r'''
msgid ""
msgstr ""
"Language: de\n"
"Plural-Forms: nplurals=2; plural=(n != 1);\n"

msgctxt "stored"
msgid "unnamed"
msgstr "unbenannt"

#, python-format
msgctxt "stored"
msgid "there is no asset %s in your assets.xml"
msgstr "in deiner assets.xml gibt es kein Asset %s"

#, python-format
msgctxt "stored"
msgid "%(path)s matches nothing in asset %(guid)s"
msgstr "in Asset %(guid)s passt nichts auf %(path)s"

#, python-format
msgctxt "stored"
msgid "kept as written: %s"
msgstr "unverändert behalten: %s"

#, python-format
msgctxt "stored"
msgid "it matches %d assets, which is too many"
msgstr "sie passt auf %d Assets, das sind zu viele"

#, python-format
msgctxt "stored"
msgid "create asset %(guid)s (%(name)s)"
msgstr "Asset %(guid)s anlegen (%(name)s)"

#, python-format
msgctxt "stored"
msgid "%r is not arithmetic"
msgstr "%r ist keine Arithmetik"

#, python-format
msgctxt "stored"
msgid "%(a)s, and %(a)s again"
msgstr "%(a)s, und nochmals %(a)s"

msgid "Settings"
msgstr "Einstellungen"
'''


class TestKeptEnglish(unittest.TestCase):
    """English kept in the loadout file, translated only where it is shown."""

    def setUp(self):
        folder = Path(tempfile.mkdtemp(prefix="ams-stored-"))
        (folder / "de.po").write_text(STORED_GERMAN, encoding="utf-8")
        self.assertTrue(i18n.install("de", folder))
        self.addCleanup(i18n.install, i18n.SOURCE)

    def test_a_sentence_of_a_known_shape_is_translated_with_its_values(self):
        self.assertEqual(tr_stored("there is no asset 9999 in your assets.xml"),
                         "in deiner assets.xml gibt es kein Asset 9999")
        self.assertEqual(tr_stored("unnamed"), "unbenannt")

    def test_named_values_go_where_the_translation_puts_them(self):
        self.assertEqual(tr_stored("Values/Cost matches nothing in asset 1001"),
                         "in Asset 1001 passt nichts auf Values/Cost")
        self.assertEqual(tr_stored("x, and x again"), "x, und nochmals x")
        self.assertEqual(tr_stored("x, and y again"), "x, and y again")

    def test_counts_and_reprs_are_values_like_any_other(self):
        self.assertEqual(tr_stored("it matches 12 assets, which is too many"),
                         "sie passt auf 12 Assets, das sind zu viele")
        self.assertEqual(tr_stored("'a + b' is not arithmetic"), "'a + b' ist keine Arithmetik")

    def test_a_value_of_a_known_shape_is_translated_too(self):
        self.assertEqual(tr_stored("kept as written: there is no asset 7 in your assets.xml"),
                         "unverändert behalten: in deiner assets.xml gibt es kein Asset 7")
        self.assertEqual(tr_stored("create asset 5 (unnamed)"), "Asset 5 anlegen (unbenannt)")

    def test_reasons_joined_with_semicolons_are_translated_one_by_one(self):
        self.assertEqual(
            tr_stored("there is no asset 1 in your assets.xml; something older"),
            "in deiner assets.xml gibt es kein Asset 1; something older",
        )

    def test_a_shape_that_begins_with_a_value_does_not_swallow_the_reason_before_it(self):
        self.assertEqual(
            tr_stored("there is no asset 1 in your assets.xml; Values/X matches nothing in asset 2"),
            "in deiner assets.xml gibt es kein Asset 1; in Asset 2 passt nichts auf Values/X",
        )
        self.assertEqual(
            tr_stored("kept as written: there is no asset 1 in your assets.xml; "
                      "there is no asset 2 in your assets.xml"),
            "unverändert behalten: in deiner assets.xml gibt es kein Asset 1; "
            "in deiner assets.xml gibt es kein Asset 2",
        )

    def test_anything_unrecognised_is_shown_as_it_is(self):
        self.assertEqual(tr_stored("a wording from an older build"), "a wording from an older build")
        self.assertEqual(tr_stored(""), "")
        # Only kept English is looked at: an ordinary sentence is tr()'s.
        self.assertEqual(tr_stored("Settings"), "Settings")

    def test_nothing_changes_in_english(self):
        i18n.install(i18n.SOURCE)
        self.assertEqual(tr_stored("unnamed"), "unnamed")


class TestPoRoundTrip(unittest.TestCase):
    def test_writing_what_was_read_gives_back_the_same_text(self):
        catalog = po.parse(GERMAN)
        again = po.parse(po.format_catalog(catalog))
        self.assertEqual(again.header, catalog.header)
        self.assertEqual(
            [(e.context, e.msgid, e.plural, e.strings, e.flags) for e in again.entries],
            [(e.context, e.msgid, e.plural, e.strings, e.flags) for e in catalog.entries],
        )

    def test_awkward_characters_survive(self):
        entry = po.Entry(msgid='Say "hi"\\now\n\tthen\nstop', strings=["x\ny"])
        catalog = po.Catalog(header={"Language": "de"}, entries=[entry])
        back = po.parse(po.format_catalog(catalog)).entries[0]
        self.assertEqual((back.msgid, back.strings), (entry.msgid, entry.strings))


def _extract(source: str):
    folder = Path(tempfile.mkdtemp(prefix="ams-extract-"))
    path = folder / "sample.py"
    path.write_text(textwrap.dedent(source), encoding="utf-8")
    return translations.extract([path])


class TestExtractor(unittest.TestCase):
    def assertRefused(self, source: str, why: str):
        _messages, problems = _extract(source)
        self.assertTrue(any(why in str(p) for p in problems),
                        "expected %r among %s" % (why, [str(p) for p in problems]))

    def test_it_finds_every_kind_of_mark(self):
        messages, problems = _extract('''
            from anno_modding_studio.core import i18n
            from anno_modding_studio.core.i18n import tr, trn, tr_noop
            LABELS = (tr_noop("Small", context="size"),)
            def show(n, who):
                # Translators: the heading of the main window.
                tr("Hello")
                tr("Hi %s", who)
                i18n.tr("Open", context="verb")
                trn("%(n)s mod", "%(n)s mods", n)
                tr(LABELS[0], context="size")
        ''')
        self.assertEqual(problems, [])
        found = {m.key: m for m in messages}
        self.assertEqual(set(found), {("size", "Small"), ("", "Hello"), ("", "Hi %s"),
                                      ("verb", "Open"), ("", "%(n)s mod")})
        self.assertEqual(found[("", "Hello")].notes, ["Translators: the heading of the main window."])
        self.assertTrue(found[("", "Hi %s")].formatted)
        self.assertFalse(found[("", "Hello")].formatted)
        self.assertEqual(found[("", "%(n)s mod")].plural, "%(n)s mods")

    def test_it_refuses_a_sentence_it_cannot_see(self):
        self.assertRefused('def f(x):\n    tr(f"Hi {x}")\n', "f-string")
        self.assertRefused('def f(x):\n    tr("Hi " + x)\n', "built before tr()")
        self.assertRefused('def f(x):\n    tr("Hi %s" % x)\n', "built before tr()")

    def test_and_a_sentence_glued_together_afterwards(self):
        self.assertRefused('def f(x):\n    tr("Hi %s") % x\n', "own arguments")

    def test_and_two_placeholders_that_cannot_move(self):
        self.assertRefused('def f(a, b):\n    tr("%s and %s", a, b)\n', "need names")

    def test_and_values_that_do_not_match_the_placeholders(self):
        self.assertRefused('def f(a):\n    tr("%(a)s", b=a)\n', "do not match")
        self.assertRefused('def f(a):\n    tr("%s")\n', "nothing to fill")
        self.assertRefused('def f(a):\n    tr("x", a)\n', "1 value(s) for 0")

    def test_and_counts_written_by_hand(self):
        self.assertRefused('def f(n):\n    trn("%d mod", "%d mods", n)\n', "by name only")
        self.assertRefused('def f(n):\n    trn("%(n)d mod", "%(n)d mods", n)\n', "use %(n)s")

    def test_and_a_translation_made_before_there_is_a_language(self):
        self.assertRefused('TITLE = tr("Settings")\n', "import time")
        self.assertRefused('class A:\n    title = tr("Settings")\n', "import time")
        self.assertRefused('def f(title=tr("Settings")):\n    pass\n', "import time")
        _messages, problems = _extract('def f():\n    return lambda: tr("Fine")\n')
        self.assertEqual(problems, [])

    def test_and_qts_own_tr(self):
        self.assertRefused('def f(self):\n    self.tr("Settings")\n', "Qt's own")


class TestMerging(unittest.TestCase):
    def test_translations_are_kept_changed_ones_flagged_and_gone_ones_dropped(self):
        old = po.parse(GERMAN)
        messages = [
            translations.Message(msgid="Settings", references=["a.py"]),
            translations.Message(msgid="%(n)s mod", plural="%(n)s mods (changed)",
                                 formatted=True),
            translations.Message(msgid="Brand new"),
        ]
        new = po.parse(po.format_catalog(translations.merged(old, messages))).by_key()
        self.assertEqual(set(new), {("", "Settings"), ("", "%(n)s mod"), ("", "Brand new")})
        self.assertEqual(new[("", "Settings")].strings, ["Einstellungen"])
        self.assertEqual(new[("", "Settings")].references, ["a.py"])
        self.assertTrue(new[("", "%(n)s mod")].fuzzy)
        self.assertEqual(new[("", "%(n)s mod")].strings, ["Ein Mod", "%(n)s Mods"])
        self.assertEqual(new[("", "Brand new")].strings, [""])

    def test_a_translation_must_fill_what_its_english_does(self):
        catalog = po.parse(GERMAN).by_key()
        self.assertTrue(translations.mismatched(catalog[("", "Broken %s.")]))
        self.assertEqual(translations.mismatched(catalog[("", "%(mod)s overrides %(other)s.")]), [])
        # A plural form may leave the count out, as "Ein Mod" does.
        self.assertEqual(translations.mismatched(catalog[("", "%(n)s mod")]), [])


class TestLint(unittest.TestCase):
    def lint(self, source: str):
        folder = Path(tempfile.mkdtemp(prefix="ams-lint-"))
        path = folder / "sample.py"
        path.write_text(textwrap.dedent(source), encoding="utf-8")
        return translations.lint([path])

    def test_english_handed_to_qt_unmarked_is_found(self):
        found = self.lint('''
            def build(self, x):
                QLabel("Hello there")
                self.status.setText("Saved %s" % x)
                button.setToolTip("On" if x else "Off")
                QMessageBox.warning(self, "Careful", "Really?")
        ''')
        self.assertEqual(len(found), 6)

    def test_marked_text_symbols_and_kept_lines_are_not(self):
        found = self.lint('''
            def build(self, x):
                QLabel(tr("Hello there"))
                QLabel("—")
                QLabel("")
                combo.addItem(tr("Automatic"), "automatic")
                QLabel("mod.io")  # i18n: keep - a brand
        ''')
        self.assertEqual(found, [])


class _Window:
    """What the Settings dialog asks of the main window, and nothing else --
    its preferences and its loader check included, faked on the same object."""

    def __init__(self):
        self.language = ""
        self.preferences = self
        self.loader_check = self

    def creator_name(self):
        return ""

    def aliases(self):
        return []

    def your_names(self):
        return []

    def appearance(self):
        return {"scheme": "granite", "outlines": "boxed", "density": "comfortable", "size": 100}

    def library_root(self):
        return ""

    def game_start(self, _key):
        return ""

    def automatic_game_start(self, _key):
        return ""

    def game_loader(self, _key=""):
        return None

    def loader_folder(self, _key=""):
        return ""

    def probes_said(self, _key=""):
        return ""

    def newest_said(self):
        return ""

    def guid_range(self, _game=""):
        return ""

    def checks_for_studio(self):
        return True

    def set_checks_for_studio(self, _on):
        pass

    def set_guid_range(self, _game, _owner):
        pass

    def language_choice(self):
        return self.language

    def set_language_choice(self, code):
        self.language = code


class TestTheLanguagePicker(unittest.TestCase):
    def setUp(self):
        from PySide6.QtWidgets import QApplication

        from anno_modding_studio.core import secrets
        from anno_modding_studio.ui.settings_dialog import SettingsDialog

        self.app = QApplication.instance() or QApplication([])
        self.window = _Window()
        store = secrets.Store(Path(tempfile.mkdtemp(prefix="ams-no-key-")) / "modio")
        from PySide6.QtWidgets import QWidget

        self.parent = QWidget()
        self.addCleanup(self.parent.deleteLater)
        self.dialog = SettingsDialog(self.window, self.parent, store=store)

    def test_it_offers_windows_own_then_every_catalog_named_in_itself(self):
        combo = self.dialog.language_combo
        codes = [combo.itemData(i) for i in range(combo.count())]
        self.assertEqual(codes, [""] + [code for code, _label in i18n.available()])
        self.assertIn("Deutsch", combo.itemText(codes.index("de")))
        self.assertTrue(combo.itemText(0).startswith("Match Windows"))

    def test_picking_one_is_remembered_and_waits_for_a_restart(self):
        combo = self.dialog.language_combo
        combo.setCurrentIndex(combo.findData("de"))
        self.assertEqual(self.window.language, "de")
        self.assertIn("next time", self.dialog.language_note.text())
        combo.setCurrentIndex(combo.findData("en"))
        self.assertEqual(self.window.language, "en")
        self.assertEqual(self.dialog.language_note.text(), "")

    def test_a_choice_with_no_catalog_comes_to_english(self):
        from anno_modding_studio.ui import language

        self.assertEqual(language.resolve("xx"), "en")
        self.assertEqual(language.resolve("de"), "de")


class TestNothingBypassesTheCatalog(unittest.TestCase):
    """The window built with every sentence 'translated' into ⟦itself⟧.

    Whatever is still bare English was never looked up: built by a helper,
    kept in a variable, or missed.  ``lint`` only sees text handed straight to
    Qt; this sees what actually reached the screen, for as much of the Studio
    as a window with one edited asset has built.
    """

    # English that is meant: names, a mod's own words, and mod.io's
    # categories, which are sent back to mod.io as they are.
    MEANT = (
        r"Anno (1800|117)", r"Pax Romana", r"mod\.io", r"Anno Modding Studio",
        r"^\W*GUID\W*$", r"^(Addon|Building|Cheat|Cosmetic|Gameplay|Map|Misc|Ship)$",
        r"^Main$", r"My Anno Mod", r"^English$", r"Deutsch",
        # Qt's own standard buttons, translated by Qt's catalog, not ours.
        r"^&?(Close|OK|Cancel)$",
    )

    @classmethod
    def setUpClass(cls):
        import untranslated_widgets
        from PySide6.QtWidgets import QApplication

        from anno_modding_studio.core import index as index_mod

        cls.app = QApplication.instance() or QApplication([])
        cls.tool = untranslated_widgets
        cls._tmp = tempfile.TemporaryDirectory()
        tmp = Path(cls._tmp.name)
        cls.assets = tmp / "assets.xml"
        cls.assets.write_text(make_sample_assets.build_assets_xml(40), encoding="utf-8")
        cls.db = tmp / "index.sqlite"
        index_mod.build_index(cls.assets, cls.db)
        translations.pseudo(tmp, "qps")
        cls.folder = tmp

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def setUp(self):
        from anno_modding_studio.core.changes import AIM_MAKE
        from anno_modding_studio.ui.main_window import MainWindow

        self.assertTrue(i18n.install("qps", self.folder))
        self.addCleanup(i18n.install, i18n.SOURCE)
        self.window = MainWindow()
        self.addCleanup(self._close)
        self.window.game_files.adopt_index(self.db, self.assets, cached=True)
        self.window.loadout.aim = AIM_MAKE
        self.window.update_tab_gates()

    def _close(self):
        self.window.editor.search._debounce.stop()
        self.window.game_files.stop_texts_worker()
        if self.window.index is not None:
            self.window.index.close()
            self.window.index = None
        self.window.loadout.clear()
        self.window.deleteLater()

    def bare_english(self, root):
        # The sample asset's own names and paths are data, not the Studio's
        # words; and a label elided mid-bracket has lost the bracket.
        meant = [*self.MEANT, r"Lumberjack", r"[\\/]", r"…"]
        return self.tool.untranslated(root, meant)

    def test_every_tab_of_a_window_with_an_edited_asset(self):
        window = self.window
        window._open_asset("1010340")
        for item in window.editor._iter_items():
            if item.text(0) == "CycleTime" and item.data(0, 258) is not None:
                item.setText(1, "15.00")
        self.assertEqual(len(window.loadout), 1)
        found = []
        for index in range(window.tabs.count()):
            window.tabs.setCurrentIndex(index)
            self.app.processEvents()
            found += self.bare_english(window)
        self.assertEqual(sorted(set(found)), [])


class TestGermanFitsTheSmallestWindow(unittest.TestCase):
    """A translation is longer than its English, and a window made as small
    as it will go has no room to spare for it."""

    def setUp(self):
        from PySide6.QtWidgets import QApplication

        from anno_modding_studio.ui.main_window import MainWindow

        self.app = QApplication.instance() or QApplication([])
        self.assertTrue(i18n.install("de"))
        self.addCleanup(i18n.install, i18n.SOURCE)
        self.window = MainWindow()
        self.addCleanup(self._close)

    def _close(self):
        self.window.editor.search._debounce.stop()
        self.window.game_files.stop_texts_worker()
        self.window.deleteLater()

    def test_no_button_under_the_mod_changes_list_is_cut_off(self):
        """The panel was held to a width of its own, under what "Auswahl
        zurücksetzen" and "Alle zurücksetzen" need beside "Alle einklappen":
        they read "wahl zurückset" and "lle zurücksetze"."""
        window, panel = self.window, self.window.changes_panel
        window.show()
        # As small as it will go: a window is never made smaller than this.
        window.resize(1, 1)
        self.app.processEvents()
        self.assertEqual(panel.remove_button.text(), "Auswahl zurücksetzen")
        buttons = (panel.fold_button, panel.remove_button, panel.add_button)
        for button in buttons:
            self.assertGreaterEqual(button.width(), button.sizeHint().width(), button.text())
        # Under either of the first one's two names.
        was = panel.fold_button.text()
        panel.fold_button.setText(tr("Expand all") if was == tr("Collapse all") else tr("Collapse all"))
        self.assertNotEqual(panel.fold_button.text(), was)
        self.app.processEvents()
        for button in buttons:
            self.assertGreaterEqual(button.width(), button.sizeHint().width(), button.text())
        # And the panel is no narrower than it ever was.
        self.assertGreaterEqual(window.changes_side.width(), 340)


def _filled(english: str, german: str):
    """Both sides of a translation with the same sample value in each slot."""
    values, positional = {}, []

    def sample(match):
        if match.group("kind") == "%":
            return "%"
        key = match.group("name")
        if key is None:
            key = "#%d" % len(positional)
            positional.append(key)
        if key not in values:
            number = len(values)
            values[key] = str(number + 7) if match.group("kind") in "diouxX" else "Q%dZ" % number
        return values[key]

    said = i18n._PLACEHOLDER.sub(sample, english)
    order = iter(positional)

    def same(match):
        if match.group("kind") == "%":
            return "%"
        key = match.group("name")
        return values[key if key is not None else next(order)]

    return said, i18n._PLACEHOLDER.sub(same, german)


class TestTheRepository(unittest.TestCase):
    """The rules above, held against the Studio's own code and catalogs."""

    def test_every_marked_sentence_can_be_translated(self):
        _messages, problems = translations.extract()
        self.assertEqual([str(p) for p in problems], [])

    def test_the_catalogs_match_the_code_and_every_translation_fits(self):
        self.assertEqual(translations.check(), [])

    def test_there_is_a_german_catalog(self):
        self.assertIn("de", dict(i18n.available()))

    def test_every_shape_of_kept_english_is_recognised_as_itself(self):
        """Each marked shape, filled with sample values, must come back as its
        own translation -- not as another shape's that happens to match too."""
        self.assertTrue(i18n.install("de"))
        self.addCleanup(i18n.install, i18n.SOURCE)
        wrong = []
        for entry in po.read(i18n.LOCALE_DIR / "de.po").entries:
            if entry.context != i18n.STORED or not entry.translated:
                continue
            english, german = _filled(entry.msgid, entry.strings[0])
            if i18n.tr_stored(english) != german:
                wrong.append("%r -> %r, not %r" % (english, i18n.tr_stored(english), german))
        self.assertEqual(wrong, [])


if __name__ == "__main__":
    unittest.main()
