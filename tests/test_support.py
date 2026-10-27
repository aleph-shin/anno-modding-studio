"""The suite's own scaffolding: what it isolates, and what it insists on."""

from __future__ import annotations

import os
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

import _isolation  # first: keeps the suite out of real files under any runner

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from support import NOT_HERE, SKIP_MISSING, requires  # noqa: E402


class TestTheSettingsAreTheSuitesOwn(unittest.TestCase):
    def test_one_named_from_outside_is_neither_used_nor_written(self):
        """A value left set by a scratch script used to be taken, and being
        somebody else's was never emptied between tests: seven failures."""
        outside = str(HERE / "no-such-dir" / "outside.ini")
        said = subprocess.run(
            [sys.executable, "-c",
             "import os, _isolation; print(os.environ['ANNO_STUDIO_SETTINGS']); print(_isolation.OUTSIDE_SETTINGS)"],
            cwd=HERE, env=dict(os.environ, ANNO_STUDIO_SETTINGS=outside),
            capture_output=True, text=True, check=True,
        ).stdout.splitlines()
        self.assertNotEqual(said[0], outside)
        self.assertIn("ams-settings-", said[0])
        self.assertEqual(said[1], outside)
        self.assertFalse(Path(outside).exists())


class TestAProcessOfTheSuiteHasFoldersOfItsOwn(unittest.TestCase):
    def said(self, **env):
        return subprocess.run(
            [sys.executable, "-c",
             "import os, _isolation; print(os.environ['ANNO_STUDIO_HOME'])"],
            cwd=HERE, env=dict(os.environ, **env),
            capture_output=True, text=True, check=True,
        ).stdout.strip()

    def test_not_the_ones_the_process_that_started_it_made(self):
        """A worker of a parallel run inherits what the pytest starting it
        made: every worker shared one home, and emptied the loadouts the
        others were saving."""
        self.assertIn(_isolation.MADE, os.environ)
        self.assertNotEqual(self.said(), os.environ["ANNO_STUDIO_HOME"])

    def test_but_one_named_from_outside_is_kept(self):
        outside = str(HERE / "no-such-dir" / "home")
        self.assertEqual(self.said(ANNO_STUDIO_HOME=outside, **{_isolation.MADE: ""}), outside)


class TestWhatTheSuiteInsistsOn(unittest.TestCase):
    def setUp(self):
        # CI sets it for the whole run; these say what happens either way.
        patched = mock.patch.dict(os.environ)
        patched.start()
        self.addCleanup(patched.stop)
        os.environ.pop(SKIP_MISSING, None)

    @staticmethod
    def run_needing():
        @requires(None, "the thing it needs")
        class Needing(unittest.TestCase):
            def test_it(self):
                pass

        class NeedingOne(unittest.TestCase):
            @requires(None, "the thing it needs")
            def test_it(self):
                pass

        results = []
        for case in (Needing, NeedingOne):
            result = unittest.TestResult()
            unittest.defaultTestLoader.loadTestsFromTestCase(case).run(result)
            results.append(result)
        return results

    def test_a_test_needing_what_is_missing_fails_and_says_what(self):
        """Decision 5.5: a skip is a pass nobody reads."""
        for result in self.run_needing():
            self.assertEqual(len(result.errors) + len(result.failures), 1)
            self.assertIn("This test needs the thing it needs", (result.errors + result.failures)[0][1])
            self.assertEqual(result.skipped, [])

    def test_where_the_machine_is_known_not_to_have_it_it_skips_and_says_what(self):
        """CI has neither game nor the loader, and tools/check.py --ci reads
        the skips into the job summary."""
        os.environ[SKIP_MISSING] = "1"
        for result in self.run_needing():
            self.assertEqual(result.errors + result.failures, [])
            self.assertEqual([reason for _test, reason in result.skipped],
                             ["needs the thing it needs, " + NOT_HERE])

    def test_and_runs_as_usual_when_it_is_there(self):
        @requires(object(), "the thing it needs")
        class Having(unittest.TestCase):
            def test_it(self):
                pass

        result = unittest.TestResult()
        unittest.defaultTestLoader.loadTestsFromTestCase(Having).run(result)
        self.assertTrue(result.wasSuccessful())


if __name__ == "__main__":
    unittest.main()
