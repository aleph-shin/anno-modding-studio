"""``core.packaged``: that a Studio whose own folder Windows keeps in a
packaged app's private copy says so as it starts (R3-30, hands-on run 3)."""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import _isolation  # noqa: F401 -- first: keeps the suite out of real files under any runner

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from anno_modding_studio.core import packaged  # noqa: E402

PACKAGE = "Claude_pzs8sxrjxfjjc"


@unittest.skipUnless(sys.platform == "win32", "package redirection is a Windows thing")
class TestWhereItReallyIs(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        # As Windows spells it: the temp folder can be named by its short
        # name, which realpath gives at length.
        self.local = Path(os.path.realpath(tmp.name)) / "Local"
        self.studio = self.local / "AnnoModdingStudio"
        self.studio.mkdir(parents=True)
        self.private = self.local / "Packages" / PACKAGE / "LocalCache" / "Local" / "AnnoModdingStudio"

    def redirected(self, to):
        """``os.path.realpath`` as Windows answers it through a package's
        redirection: the Studio's folder at ``to``."""
        real = os.path.realpath

        def answer(path, *args, **kwargs):
            if os.path.normcase(os.path.abspath(path)) == os.path.normcase(str(self.studio)):
                return str(to)
            return real(path, *args, **kwargs)

        return mock.patch.object(packaged.os.path, "realpath", answer)

    def test_a_folder_in_a_packages_private_copy_is_found_and_said(self):
        with self.redirected(self.private):
            self.assertEqual(packaged.private_copy(self.studio, self.local), self.private)
            with mock.patch.object(packaged.places, "local_app_data", return_value=self.local):
                said = packaged.note(self.studio)
        self.assertIn(str(self.private), said)
        self.assertIn(str(self.studio), said)
        self.assertIn("would point at nothing for the game", said)

    def test_one_where_it_was_asked_for_says_nothing(self):
        """The real call, on a folder nothing redirects."""
        self.assertIsNone(packaged.private_copy(self.studio, self.local))
        with mock.patch.object(packaged.places, "local_app_data", return_value=self.local):
            self.assertEqual(packaged.note(self.studio), "")

    def test_nor_one_moved_elsewhere_by_a_junction_of_the_users_own(self):
        """It resolves to another path too, and is where everything looks."""
        with self.redirected(Path(tempfile.gettempdir()) / "D-drive" / "AnnoModdingStudio"):
            self.assertIsNone(packaged.private_copy(self.studio, self.local))

    def test_nor_a_packages_own_folder_that_is_not_its_private_copy(self):
        with self.redirected(self.local / "Packages" / PACKAGE / "Settings" / "AnnoModdingStudio"):
            self.assertIsNone(packaged.private_copy(self.studio, self.local))

    def test_nor_one_not_there(self):
        self.assertIsNone(packaged.private_copy(self.local / "Not there", self.local))


@unittest.skipUnless(sys.platform == "win32", "package redirection is a Windows thing")
class TestAsItStarts(unittest.TestCase):
    def setUp(self):
        from PySide6.QtWidgets import QApplication

        QApplication.instance() or QApplication([])

    def test_nothing_to_say_carries_on_unasked(self):
        from anno_modding_studio.ui import one_studio

        with mock.patch.object(one_studio.packaged, "note", return_value=""), \
                mock.patch.object(one_studio.QMessageBox, "exec") as shown:
            self.assertTrue(one_studio.carry_on_if_kept_in_a_package())
        shown.assert_not_called()

    def test_said_it_closes_unless_told_to_carry_on(self):
        from anno_modding_studio.ui import one_studio

        for pressed, carried_on in (("&Close the Studio", False), ("Carry &on anyway", True)):
            with self.subTest(pressed=pressed):
                def press(box, text=pressed):
                    box._pressed = next(b for b in box.buttons() if b.text() == text)
                    return 0

                with mock.patch.object(one_studio.packaged, "note", return_value="Kept elsewhere."), \
                        mock.patch.object(one_studio.QMessageBox, "exec", press), \
                        mock.patch.object(one_studio.QMessageBox, "clickedButton",
                                          lambda box: box._pressed):
                    self.assertEqual(one_studio.carry_on_if_kept_in_a_package(), carried_on)


if __name__ == "__main__":
    unittest.main()
