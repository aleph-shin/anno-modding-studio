"""What ``tools/check.py --ci`` adds for GitHub Actions: the suite run so
that a test needing the games or the loader skips, and a summary that says
which did, beside each check's result and what CI never runs."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import _isolation  # noqa: F401 -- first: keeps the suite out of real files under any runner

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import check  # noqa: E402
import support  # noqa: E402

# A JUnit report as pytest writes one: a test that passed, one that failed,
# a class skipped for want of the loader, a test skipped for want of a game,
# and a skip of another kind.
REPORT = """<?xml version="1.0" encoding="utf-8"?>
<testsuites><testsuite name="pytest" tests="6">
<testcase classname="tests.test_a.TestA" name="test_passes" time="0.1"/>
<testcase classname="tests.test_a.TestA" name="test_fails" time="0.1"><failure message="no">no</failure></testcase>
<testcase classname="tests.test_b.TestLoader" name="test_one" time="0">
  <skipped type="pytest.skip" message="needs the loader, which this machine does not have">x</skipped></testcase>
<testcase classname="tests.test_b.TestLoader" name="test_two" time="0">
  <skipped type="pytest.skip" message="tests\\test_b.py:12: needs the loader, which this machine does not have">x</skipped></testcase>
<testcase classname="tests.test_c.TestGame" name="test_it" time="0">
  <skipped type="pytest.skip" message="needs Anno 1800 or Anno 117 installed, which this machine does not have">x</skipped></testcase>
<testcase classname="tests.test_d.TestFocus" name="test_it" time="0">
  <skipped type="pytest.skip" message="no keyboard focus on this platform">x</skipped></testcase>
</testsuite></testsuites>
"""


class TestTheSuitesReport(unittest.TestCase):
    def setUp(self):
        folder = Path(tempfile.mkdtemp(prefix="ams-check-test-"))
        self.addCleanup(__import__("shutil").rmtree, folder, True)
        self.report = folder / "suite.xml"
        self.report.write_text(REPORT, encoding="utf-8")

    def test_a_skip_for_want_of_the_games_or_the_loader_is_told_from_any_other(self):
        found = check.suite_report(self.report)
        self.assertEqual((found["tests"], found["failed"], found["skipped"]), (6, 1, 4))
        self.assertEqual(found["missing"], {
            "the loader": ["tests.test_b.TestLoader.test_one", "tests.test_b.TestLoader.test_two"],
            "Anno 1800 or Anno 117 installed": ["tests.test_c.TestGame.test_it"],
        })
        self.assertEqual(found["other"], {"no keyboard focus on this platform": ["tests.test_d.TestFocus.test_it"]})

    def test_no_report_is_nothing_found_rather_than_an_error(self):
        found = check.suite_report(self.report.parent / "none.xml")
        self.assertEqual((found["tests"], found["missing"], found["other"]), (0, {}, {}))
        self.assertEqual(check.suite_report(None)["tests"], 0)

    def test_the_summary_names_each_check_what_was_not_run_and_what_never_is(self):
        text = check.summary([("ruff", "3 findings (recorded 3)", True),
                              ("the suite", "6 tests, 1 failed | 4 skipped", False)],
                             check.suite_report(self.report))
        self.assertIn("| ruff | 3 findings (recorded 3) | passed |", text)
        # A bar in what was found would end the cell.
        self.assertIn("| the suite | 6 tests, 1 failed \\| 4 skipped | **FAILED** |", text)
        self.assertIn("3 tests skipped.", text)
        self.assertIn("| the loader | 2 |", text)
        self.assertIn("| Anno 1800 or Anno 117 installed | 1 |", text)
        self.assertIn("- `tests.test_c.TestGame.test_it`", text)
        self.assertIn("- no keyboard focus on this platform: 1", text)
        for what in check.NEVER_IN_CI:
            self.assertIn("- " + what, text)

    def test_with_nothing_missing_the_summary_says_so(self):
        text = check.summary([("ruff", "0 findings", True)], {"missing": {}, "other": {}})
        self.assertIn("None: every test that needs them ran.", text)
        self.assertNotIn("Skipped for another reason", text)

    def test_with_no_suite_there_is_nothing_about_skips(self):
        text = check.summary([("ruff", "0 findings", True)])
        self.assertNotIn("for want of", text)
        self.assertIn("### Never run in CI", text)

    def test_the_summary_goes_where_actions_names_and_else_to_the_screen(self):
        target = self.report.parent / "summary.md"
        target.write_text("before\n", encoding="utf-8")
        with mock.patch.dict(os.environ, {"GITHUB_STEP_SUMMARY": str(target)}):
            check.write_summary("after\n")
        self.assertEqual(target.read_text(encoding="utf-8"), "before\nafter\n")
        with mock.patch.dict(os.environ, {"GITHUB_STEP_SUMMARY": ""}), \
                mock.patch("builtins.print") as printed:
            check.write_summary("shown\n")
        printed.assert_called_once_with("shown\n")


class TestTheSuiteUnderCI(unittest.TestCase):
    def ran(self, ci):
        done = subprocess.CompletedProcess([], 0)
        with mock.patch.object(check.subprocess, "run", return_value=done) as run, \
                mock.patch("builtins.print"):
            passed, said, report = check.run_suite(ci=ci)
        self.assertTrue(passed)
        self.assertIn("passed", said)
        (command,), kwargs = run.call_args
        return command, kwargs["env"], report

    def test_a_test_needing_what_is_missing_skips_and_a_report_is_written(self):
        command, env, report = self.ran(ci=True)
        self.assertEqual(env[check.SKIP_MISSING], "1")
        self.assertEqual(command[command.index("--junitxml") + 1], str(report))

    def test_and_without_ci_it_fails_as_it_always_has(self):
        with mock.patch.dict(os.environ):
            os.environ.pop(check.SKIP_MISSING, None)
            command, env, report = self.ran(ci=False)
        self.assertNotIn(check.SKIP_MISSING, env)
        self.assertNotIn("--junitxml", command)
        self.assertIsNone(report)

    def test_it_is_shared_among_processes_that_write_down_what_they_ran(self):
        # An order of its own: one process's record, where this runs from one,
        # is run in one process.
        with mock.patch.dict(os.environ, {check.TEST_WORKERS: "4", check.TEST_ORDER: "12345"}), \
                mock.patch.object(check.importlib.util, "find_spec", return_value=object()):
            command, env, _report = self.ran(ci=False)
        self.assertEqual(command[command.index("-n") + 1], "4")
        self.assertIn(check.TEST_RECORD, env)

    def test_and_in_one_process_where_there_is_no_xdist(self):
        with mock.patch.dict(os.environ, {check.TEST_WORKERS: "4"}), \
                mock.patch.object(check.importlib.util, "find_spec", return_value=None):
            # Set already where this test runs in such a process itself.
            os.environ.pop(check.TEST_RECORD, None)
            command, env, _report = self.ran(ci=False)
        self.assertNotIn("-n", command)
        self.assertNotIn(check.TEST_RECORD, env)

    def test_it_reads_the_skips_the_suite_makes(self):
        """check.py and tests/support.py agree on how such a skip is said."""
        self.assertEqual((check.SKIP_MISSING, check.NOT_HERE), (support.SKIP_MISSING, support.NOT_HERE))


class TestAFailureInAParallelRun(unittest.TestCase):
    def setUp(self):
        self.record = Path(tempfile.mkdtemp(prefix="ams-order-"))
        self.addCleanup(shutil.rmtree, self.record, True)

    def test_says_which_process_and_how_to_run_what_it_ran(self):
        (self.record / "gw3.txt").write_text("tests/test_a.py::A::test_x\n", encoding="utf-8")
        (self.record / "gw3.failed").write_text("tests/test_a.py::A::test_x\n", encoding="utf-8")
        (self.record / "gw4.txt").write_text("tests/test_b.py::B::test_y\n", encoding="utf-8")
        said, = check.replays(self.record)
        self.assertIn("gw3 failed tests/test_a.py::A::test_x", said)
        self.assertIn("%s=%s" % (check.TEST_ORDER, self.record / "gw3.txt"), said)

    def test_and_what_it_ran_is_run_again_in_one_process(self):
        ran = self.record / "gw3.txt"
        ran.write_text("tests/test_a.py::A::test_x\n", encoding="utf-8")
        with mock.patch.dict(os.environ, {check.TEST_WORKERS: "4"}):
            self.assertEqual(check.suite_workers(str(ran)), 1)
            self.assertEqual(check.suite_workers("12345"), 4)
        with mock.patch.dict(os.environ, {check.TEST_WORKERS: ""}):
            self.assertLessEqual(check.suite_workers("12345"), check.MOST_WORKERS)


if __name__ == "__main__":
    unittest.main()
