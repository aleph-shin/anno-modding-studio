"""What ``tools/render_themes.py --compare`` counts as differing: the
acceptance check of ``docs/standards/release-checklist.md``."""

from __future__ import annotations

import contextlib
import io
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import _isolation  # noqa: F401 -- first: keeps the suite out of real files under any runner

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import render_themes  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

# The sheet asks for an application to read pictures with, and takes the one
# there is: the suite's, which has to be a QApplication for every other test.
_app = QApplication.instance() or QApplication([])

# The pictures a --compare render of Granite makes.
TABS = ["granite_%s.png" % tab for tab in render_themes.TABS]


class TestWhatComparingCounts(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.out = Path(self._tmp.name) / "out"
        self.references = Path(self._tmp.name) / "references"
        self.out.mkdir()
        self.references.mkdir()
        patched = mock.patch.object(render_themes, "REFERENCES", self.references)
        patched.start()
        self.addCleanup(patched.stop)

    def differing(self, built=(), references=(), changed=0, rows=("granite",)):
        for name in built:
            (self.out / name).write_bytes(b"png")
        for name in references:
            (self.references / name).write_bytes(b"png")
        said = io.StringIO()
        with mock.patch.object(render_themes, "difference", return_value=changed), \
                contextlib.redirect_stdout(said):
            count = render_themes.contact_sheet(self.out, [(row,) for row in rows], True)
        return count, said.getvalue()

    def test_every_picture_like_its_reference_is_none(self):
        self.assertEqual(self.differing(TABS, TABS)[0], 0)

    def test_a_picture_that_differs_is_one_each(self):
        self.assertEqual(self.differing(TABS, TABS, changed=12)[0], len(TABS))

    def test_a_variant_that_rendered_nothing_is_not_a_pass(self):
        """Counted over the pictures found, a run that rendered none compared
        none and exited 0."""
        count, said = self.differing()
        self.assertEqual(count, len(TABS))
        self.assertIn("not rendered", said)

    def test_a_reference_with_no_picture_is_counted(self):
        count, said = self.differing(TABS[1:], TABS)
        self.assertEqual(count, 1)
        self.assertIn("%s not rendered" % TABS[0].ljust(28), said)

    def test_a_picture_with_no_reference_is_counted(self):
        count, said = self.differing(TABS, TABS[1:])
        self.assertEqual(count, 1)
        self.assertIn("%s no reference" % TABS[0].ljust(28), said)

    def test_pictures_left_from_a_run_of_the_whole_set_are_not_counted(self):
        """The output folder is not cleared, and the whole set writes the
        stack and the dialogs under the same name as the tabs."""
        leftovers = ["granite_5_stack.png", "granite_dialog_about.png"]
        count, said = self.differing(TABS + leftovers, TABS)
        self.assertEqual(count, 0, said)

    def test_a_name_that_matches_no_variant_is_refused(self):
        """``--compare --only`` with such a name rendered nothing and passed."""
        with self.assertRaises(SystemExit) as stopped, contextlib.redirect_stderr(io.StringIO()) as said:
            render_themes.main([str(self.out), "--compare", "--only", "granit"])
        self.assertEqual(stopped.exception.code, 2)
        self.assertIn("no variant is called 'granit'", said.getvalue())


if __name__ == "__main__":
    unittest.main()
