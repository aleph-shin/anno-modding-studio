"""The static design rules of tools/design_check.py, each on a snippet of source.

Every rule gets source that breaks it and source that keeps it, so a rule that
stops finding anything, or starts finding everything, shows up here before it
shows up as a count nobody can explain.
"""

from __future__ import annotations

import sys
import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest import mock

import _isolation  # noqa: F401 -- first: keeps the suite out of real files under any runner

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import design_check  # noqa: E402


def lines(found) -> list:
    return [line for line, _text in found]


def src(text: str) -> str:
    return textwrap.dedent(text).lstrip("\n")


class ColourLiteralTest(unittest.TestCase):
    def test_finds_each_way_of_writing_a_colour(self):
        found = design_check.colour_literals(src('''
            a = "#fff"
            b = "#1A2b3C"
            c = QColor("#6f7a8a")
            d = QColor("red")
            e = QColor(255, 0, 0)
            f = Qt.GlobalColor.gray
            g = "color: #aabbcc; margin: 0"
        '''))
        self.assertEqual(lines(found), [1, 2, 3, 4, 5, 6, 7])

    def test_a_qcolor_literal_counts_once(self):
        self.assertEqual(len(design_check.colour_literals('x = QColor("#abcdef")\n')), 1)

    def test_leaves_what_is_not_a_colour(self):
        found = design_check.colour_literals(src('''
            """Issue #123 and the colour #ffffff in a docstring."""
            a = Qt.GlobalColor.transparent
            b = QColor(scheme.accent)
            c = "#12"
            d = "see #1234567"
            e = "&#160;"
            f = palette.color(role)
        '''))
        self.assertEqual(found, [])


class StylesheetTest(unittest.TestCase):
    def test_finds_a_call(self):
        found = design_check.stylesheets('x = 1\nself.label.setStyleSheet("font-weight: bold")\n')
        self.assertEqual(lines(found), [2])

    def test_leaves_other_calls(self):
        self.assertEqual(design_check.stylesheets("self.label.setText(styleSheet)\n"), [])


class DoubleClickTest(unittest.TestCase):
    def test_finds_a_double_click_with_nothing_for_the_keyboard(self):
        found = design_check.double_click_only(src('''
            self.table.itemDoubleClicked.connect(self.open)
            self.tree.doubleClicked.connect(self.pick)
            self.other.activated.connect(self.pick)
        '''))
        self.assertEqual(lines(found), [1, 2])

    def test_leaves_one_that_is_activated_too(self):
        found = design_check.double_click_only(src('''
            self.table.itemDoubleClicked.connect(self.open)
            self.table.itemActivated.connect(self.open)
            view.doubleClicked.connect(pick)
            view.activated.connect(pick)
        '''))
        self.assertEqual(found, [])


class YesNoBoxTest(unittest.TestCase):
    def test_finds_generic_buttons(self):
        found = design_check.yes_no_boxes(src('''
            QMessageBox.question(self, title, text)
            QMessageBox.question(
                self, title, text,
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            QMessageBox.warning(self, title, text,
                                QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel)
            box.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        '''))
        self.assertEqual(lines(found), [1, 2, 7, 9])

    def test_leaves_a_single_ok_and_named_buttons(self):
        found = design_check.yes_no_boxes(src('''
            QMessageBox.information(self, title, text)
            QMessageBox.critical(self, title, text, QMessageBox.StandardButton.Ok)
            QMessageBox.warning(self, title, text,
                                QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Cancel)
            buttons = QDialogButtonBox(
                QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
            if answer == QMessageBox.StandardButton.Yes:
                pass
        '''))
        self.assertEqual(found, [])


class LongLabelTest(unittest.TestCase):
    def test_finds_a_label_over_four_words(self):
        found = design_check.long_labels(src('''
            a = QPushButton(tr("Revert all changes to this asset"))
            b = QCheckBox("Header comment with the mod details")
            c = QRadioButton(tr("A copy of an existing asset"), self)
        '''))
        self.assertEqual(lines(found), [1, 2, 3])

    def test_leaves_short_built_and_waived_labels(self):
        found = design_check.long_labels(src('''
            a = QPushButton(tr("&Revert asset"))
            b = QCheckBox(tr("Export as zip …"))
            c = QPushButton(some_text)
            d = QLabel(tr("A label is not a button and may run on"))
            e = QCheckBox(tr("Keep the name the mod came with"))  # design: quotes the mod's term
            f = QPushButton(
                tr("Unload every one of these mods"),  # design: the count is the point
            )
        '''))
        self.assertEqual(found, [])

    def test_a_design_comment_needs_a_reason(self):
        found = design_check.long_labels(
            'a = QPushButton(tr("Revert all changes to this asset"))  # design:\n')
        self.assertEqual(lines(found), [1])


class LongHelpTest(unittest.TestCase):
    def test_finds_help_over_eighty_words(self):
        found = design_check.long_help('tip = HelpTip(tr("%s"))\n' % " ".join(["word"] * 81))
        self.assertEqual(lines(found), [1])

    def test_leaves_help_within_budget(self):
        found = design_check.long_help('tip = HelpTip(tr("%s"))\n' % " ".join(["word"] * 80))
        self.assertEqual(found, [])


class LongTooltipTest(unittest.TestCase):
    LONG = " ".join(["word"] * 16)

    def test_finds_literal_text_however_it_is_put_together(self):
        half = " ".join(["word"] * 8)
        found = design_check.long_tooltips(src('''
            a.setToolTip("%s")
            b.setToolTip(tr("%s"))
            c.setToolTip(tr(
                "%s "
                "%s"))
            d.setToolTip(tr("%s " + "%s"))
        ''') % (self.LONG, self.LONG, half, half, half, half))
        self.assertEqual(lines(found), [1, 2, 3, 6])

    def test_leaves_short_and_built_text(self):
        found = design_check.long_tooltips(src('''
            a.setToolTip(tr("Open the mod's folder"))
            b.setToolTip(entry.why)
            c.setToolTip(tr("<b>%s</b>"))
        ''') % " ".join(["word"] * 15))
        self.assertEqual(found, [])

    def test_a_buttons_tip_in_a_question_is_a_tooltip_too(self):
        found = design_check.long_tooltips(src('''
            tip(button, tr("%s"))
            ask(parent, title, text, action, action_tip=tr("%s"), cancel_tip=tr("Short"))
            choose(parent, title, text, [
                (tr("&One"), tr("%s")),
                (tr("&Two"), tr("Short enough")),
            ])
        ''') % (self.LONG, self.LONG, self.LONG))
        self.assertEqual(lines(found), [1, 2, 4])


class LongQuestionTest(unittest.TestCase):
    LONG = " ".join(["word"] * 41)

    def test_finds_long_text_in_each_kind_of_box(self):
        found = design_check.long_questions(src('''
            ask(self, title, tr("%s"), tr("&Go"))
            choose(self, title, tr("%s"), choices)
            tell(self, title, tr("%s"))
            show_box_text(box, tr("%s"))
            QMessageBox.information(self, title, tr("%s"))
        ''') % ((self.LONG,) * 5))
        self.assertEqual(lines(found), [1, 2, 3, 4, 5])

    def test_leaves_short_built_waived_and_other_text(self):
        found = design_check.long_questions(src('''
            ask(self, title, tr("Short"), tr("&Go"))
            ask(self, title, "\\n".join(lines), tr("&Go"))
            tell(self, title, tr("%s"))  # design: a one-time notice
            self.logger.information(self, title, tr("%s"))
            QLabel(tr("%s"))
        ''') % ((self.LONG,) * 3))
        self.assertEqual(found, [])


class BoxQuotesButtonTest(unittest.TestCase):
    def test_finds_text_that_says_what_its_own_button_does(self):
        found = design_check.boxes_quoting_buttons(src('''
            def ask_dropped(self):
                said = tr("“Add to loadout” reads it into the loadout open now.")
                box.addButton(tr("&Add to loadout"), role)

            def ask_more(self):
                choose(self, title, tr("“Make my copy” makes a copy."),
                       [(tr("Make my &copy…"), "")])
        '''))
        self.assertEqual(lines(found), [2, 6])

    def test_leaves_a_button_made_elsewhere_and_plain_quotes(self):
        found = design_check.boxes_quoting_buttons(src('''
            def status(self):
                said = tr("Pick the mod, then “Patch this mod…”.")

            def ask(self):
                box.addButton(tr("&Read mods"), role)
                said = tr("No rows match “%s”.")
        '''))
        self.assertEqual(found, [])


class RepeatedValueTest(unittest.TestCase):
    def test_finds_a_value_filled_in_twice(self):
        found = design_check.repeated_values(src('''
            a = tr("Forked %(was)s as %(fork)s. %(was)s is untouched.", was=w, fork=f)
            b = trn("%(n)s mod of %(c)s", "%(n)s mods of %(c)s and %(c)s", n, c=c)
        '''))
        self.assertEqual(lines(found), [1, 2])

    def test_leaves_values_once_markup_and_waived_text(self):
        found = design_check.repeated_values(src('''
            a = tr("Forked %(was)s as %(fork)s. The original is untouched.", was=w, fork=f)
            b = tr('Get a key from <a href="%(url)s">%(url)s</a>.', url=u)
            c = tr("Add %(where)s to %(where)s.", where=w)  # design: typed as shown
            d = "%(a)s %(a)s" % values
        '''))
        self.assertEqual(found, [])


class VoiceTest(unittest.TestCase):
    def rules(self, source: str) -> list:
        return [(rule, line) for rule, line, _text in design_check.voice(src(source))]

    def test_finds_each_pattern(self):
        self.assertEqual(self.rules('''
            tr("Saved — twice")
            tr("Saved -- twice")
            tr("It is saved; it is safe")
            tr("It patches rather than edits")
            tr("Done. Nothing was written.")
            tr("What the loader says it loaded")
            tr_noop("Does exactly that")
            trn("One mod", "Simply %(n)s mods", n)
        '''), [
            ("voice-dash", 1), ("voice-dash", 2), ("voice-semicolon", 3),
            ("voice-rather-than", 4), ("voice-nothing", 5), ("voice-says", 6),
            ("voice-exactly", 7), ("voice-simply", 8),
        ])

    def test_the_studio_as_a_speaker(self):
        self.assertEqual(self.rules('''
            tr("The Studio asks its copy of the tools")
            tr("A hint tells you why")
            tr("The Studio knows the op")
        '''), [("voice-says", 1), ("voice-says", 2), ("voice-says", 3)])

    def test_leaves_plain_text_and_unmarked_strings(self):
        self.assertEqual(self.rules('''
            tr("Saved %s.")
            tr("Mods &amp; maps")
            tr("There is nothing to convert.")
            tr("Use --verbose for more")
            tr("The Studio checks the folder")
            tr("XML files (*.xml);;All files (*)")
            label = "Not marked — so not read; simply ignored"
            tr(built)
        '''), [])


class LabelBuddyTest(unittest.TestCase):
    def test_finds_form_rows_and_labels_with_no_buddy(self):
        found = design_check.labels_without_buddy(src('''
            def build(self):
                form.addRow(tr("Name"), self.name_edit)
                form.addRow("GUID", self.guid_edit)
                label = QLabel(tr("Template:"))
                row.addWidget(QLabel(tr("Into:")))
        '''))
        self.assertEqual(lines(found), [2, 3, 4, 5])

    def test_leaves_labels_with_a_buddy_and_rows_with_a_widget(self):
        found = design_check.labels_without_buddy(src('''
            def build(self):
                form.addRow(tr("&Name"), self.name_edit)
                form.addRow("&GUID", self.guid_edit)
                label = QLabel(tr("&Template:"))
                label.setBuddy(self.template_box)
                self.into_label = QLabel(tr("Into:"))
                self.into_label.setBuddy(self.into_edit)
                form.addRow(label, self.template_box)
                form.addRow("", self.note)
                form.addRow(name, edit)
                heading = QLabel(tr("Mods"))
        '''))
        self.assertEqual(found, [])

    def test_a_caption_for_a_value_that_is_only_read_can_be_waived(self):
        found = design_check.labels_without_buddy(src('''
            def build(self):
                grid.addWidget(QLabel(tr("Game:")))  # design: names a value that is only read
                grid.addWidget(QLabel(tr("Into:")))  # design:
        '''))
        self.assertEqual(lines(found), [3])

    def test_a_buddy_set_in_another_function_does_not_count(self):
        found = design_check.labels_without_buddy(src('''
            def build(self):
                label = QLabel(tr("Template:"))

            def other(self):
                label.setBuddy(box)
        '''))
        self.assertEqual(lines(found), [2])


class CheckSourceTest(unittest.TestCase):
    SOURCE = 'x.setStyleSheet("color: #ff0000")\ny = tr("Saved — twice")\n'

    def rules(self, **how) -> list:
        return sorted(rule for rule, _line, _text in design_check.check_source(self.SOURCE, **how))

    def test_interface_code_gets_every_rule(self):
        self.assertEqual(self.rules(), ["colour-literal", "stylesheet", "voice-dash"])

    def test_the_theme_may_hold_colours_and_style_sheets(self):
        self.assertEqual(self.rules(theme=True), ["voice-dash"])

    def test_core_is_read_for_its_strings_only(self):
        self.assertEqual(self.rules(code=False), ["voice-dash"])


class RasterImageTest(unittest.TestCase):
    def test_only_the_app_icon_may_be_a_raster_image(self):
        with tempfile.TemporaryDirectory() as folder:
            resources = Path(folder)
            (resources / "sub").mkdir()
            for name in ("icon.ico", "icon.png", "check.png", "arrow.svg", "sub/logo.JPG",
                         "sub/icon.png", "data.json"):
                (resources / name).write_bytes(b"")
            found = [path.relative_to(resources).as_posix()
                     for path in design_check.raster_images(resources)]
        self.assertEqual(found, ["check.png", "sub/icon.png", "sub/logo.JPG"])


class RepositoryTest(unittest.TestCase):
    def test_the_repository_has_no_finding(self):
        # Every rule reached zero with area 8, and one finding fails since
        # the design review closed.
        self.assertEqual(design_check.failures(), [])

    def test_a_finding_is_printed_with_its_rule_and_place(self):
        found = [("voice-dash", "anno_modding_studio/ui/x.py:3", "Saved — twice")]
        with mock.patch.object(design_check, "findings", return_value=found), \
                mock.patch.object(design_check, "raster_images",
                                  return_value=[design_check.PACKAGE / "resources" / "a.png"]):
            self.assertEqual(design_check.failures(), [
                "  voice-dash: anno_modding_studio/ui/x.py:3: Saved — twice",
                "  raster-image: anno_modding_studio/resources/a.png",
            ])
            self.assertEqual(design_check.main([]), 1)

    def test_counts_use_only_known_rules_and_repository_paths(self):
        found = design_check.findings()
        self.assertLessEqual({rule for rule, _where, _text in found}, set(design_check.RULES))
        for _rule, where, _text in found:
            path, line = where.rsplit(":", 1)
            self.assertTrue(path.startswith("anno_modding_studio/"), where)
            self.assertTrue(line.isdigit(), where)
        self.assertEqual(sum(design_check.counts().values()), len(found))


if __name__ == "__main__":
    unittest.main()
