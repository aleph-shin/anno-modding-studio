"""The mod options dialog gives back what nobody touched exactly as it was
given -- see ``ui/mod_options_dialog.py`` (H2-7)."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

import _isolation  # noqa: F401 - first: keeps the suite out of real files

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from PySide6.QtWidgets import QApplication  # noqa: E402

from anno_modding_studio.core import settings as settings_mod  # noqa: E402
from anno_modding_studio.ui.mod_options_dialog import ModOptionsDialog  # noqa: E402

APP = QApplication.instance() or QApplication([])


def _offered() -> settings_mod.ModOptions:
    return settings_mod.ModOptions([
        settings_mod.Option(name="fine", label="Fine", kind=settings_mod.SLIDER,
                            default="0.0004", values=("0", "0.01", "0.0001")),
        settings_mod.Option(name="long", label="Long", kind=settings_mod.SLIDER,
                            default="0.0004166667", values=("0", "1", "0.5")),
        settings_mod.Option(name="text", label="Text", kind=settings_mod.TEXT,
                            default="0.00023810"),
        settings_mod.Option(name="box", label="Box", kind=settings_mod.TOGGLE, default="True"),
    ])


class TestAModsWordsAreShownAsText(unittest.TestCase):
    """A modinfo option's label and description are the mod's to write:
    ``<img src="file://host/share/a.png">`` in one had Qt fetch that file, over
    SMB, as soon as the dialog opened."""

    def test_an_options_label_and_description_are_plain_text(self):
        from PySide6.QtCore import Qt
        from PySide6.QtWidgets import QLabel

        from anno_modding_studio.ui import widgets

        widgets.external_text_stays_plain(APP)
        self.addCleanup(lambda: (APP.removeEventFilter(APP._plain_labels),
                                 setattr(APP, "_plain_labels", None)))
        hostile = '<img src="x">'
        offered = settings_mod.ModOptions([settings_mod.Option(
            name="box", label=hostile + " label", description=hostile + " described",
            kind=settings_mod.TOGGLE, default="True")])
        dialog = ModOptionsDialog(hostile + " mod", offered, None)
        self.addCleanup(dialog.deleteLater)
        dialog.show()
        APP.processEvents()
        dialog.hide()
        shown = [label for label in dialog.findChildren(QLabel) if "<img" in label.text()]
        self.assertTrue(shown)
        for label in shown:
            self.assertNotEqual(label.textFormat(), Qt.TextFormat.AutoText, label.text())
            self.assertNotEqual(label.textFormat(), Qt.TextFormat.RichText, label.text())


class TestUntouchedIsAsGiven(unittest.TestCase):
    def dialog(self, chosen=None) -> ModOptionsDialog:
        dialog = ModOptionsDialog("Early Game", _offered(), chosen)
        self.addCleanup(dialog.deleteLater)
        return dialog

    def test_nothing_touched_is_every_value_as_it_was_given(self):
        self.assertEqual(self.dialog().chosen(), {
            "fine": "0.0004", "long": "0.0004166667", "text": "0.00023810", "box": "True",
        })

    def test_a_fine_step_is_not_rounded_away(self):
        """Two decimals whatever the step showed 0.0004 as 0.00, and read it
        back as an answer of 0."""
        spin = self.dialog().controls["fine"]
        self.assertEqual(spin.decimals(), 4)
        self.assertAlmostEqual(spin.value(), 0.0004)

    def test_a_value_moved_reads_as_the_number_it_is(self):
        dialog = self.dialog()
        dialog.controls["fine"].stepBy(2)
        dialog.controls["box"].setChecked(False)
        chosen = dialog.chosen()
        self.assertEqual(chosen["fine"], "0.0006")
        self.assertEqual(chosen["box"], "false")
        # The ones left alone are still as given.
        self.assertEqual(chosen["long"], "0.0004166667")

    def test_moved_and_moved_back_is_untouched(self):
        dialog = self.dialog({"fine": "0.00040"})
        spin = dialog.controls["fine"]
        spin.stepBy(1)
        spin.stepBy(-1)
        self.assertEqual(dialog.chosen()["fine"], "0.00040")

    def test_a_value_it_cannot_show_reads_as_what_it_shows(self):
        """A slider's answer out of its range, or a list's no longer among
        its choices, is shown as the nearest it has: that is the answer."""
        offered = settings_mod.ModOptions([
            settings_mod.Option(name="size", label="Size", kind=settings_mod.ENUM,
                                default="10", values=("5", "10")),
            settings_mod.Option(name="fine", label="Fine", kind=settings_mod.SLIDER,
                                default="0.0004", values=("0", "0.01", "0.0001")),
        ])
        dialog = ModOptionsDialog("Early Game", offered, {"size": "40", "fine": "3"})
        self.addCleanup(dialog.deleteLater)
        self.assertEqual(dialog.chosen(), {"size": "5", "fine": "0.01"})

    def test_the_defaults_button_gives_the_defaults_as_the_mod_spells_them(self):
        dialog = self.dialog({"fine": "0.0009", "box": "false"})
        dialog._reset()
        self.assertEqual(dialog.chosen(), dict(_offered().defaults()))


if __name__ == "__main__":
    unittest.main()
