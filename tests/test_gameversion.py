"""Reading which game update is installed from the games' own logs."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

import _isolation  # noqa: F401 -- first: keeps the suite out of real files under any runner

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from anno_modding_studio.core import gameversion as probe  # noqa: E402

# Verbatim from this machine's logs, one per game.
LINE_117 = (
    "[02:09:48.077][54476     ][SYSTEM][   NOTICE] rdgc::CGameClient::Run::<lambda_1>"
    "::operator (): BuildString: [Build:2026-08-28 09:11:47] Anno8 Steam "
    "GoldmasterSteam Win64 Retail v2.1.0.1711091.299885 //Anno8/Anno117_v2_1"
)
LINE_1800 = (
    "[21:50:59.123][1234      ][SYSTEM][   NOTICE] BuildString: Developer build - for "
    "internal use only! [Build: 2024-11-11 14:14:36] Anno7 Steam Goldmaster Win64 "
    "Release-Static v18.4.1412158.165273 main"
)


class BuildStringTest(unittest.TestCase):
    def test_anno_117(self):
        build = probe.parse_build_string(LINE_117)
        self.assertEqual(build.update, "2.1.0")
        self.assertEqual(build.version, "2.1.0.1711091.299885")
        self.assertEqual(build.built, "2026-08-28 09:11:47")
        self.assertEqual(build.store, "Steam")
        self.assertEqual(build.branch, "//Anno8/Anno117_v2_1")

    def test_anno_1800(self):
        # Every retail 1800 calls itself a developer build; the space after
        # "Build:" is 1800's too.
        build = probe.parse_build_string(LINE_1800)
        self.assertEqual(build.update, "18.4")
        self.assertEqual(build.built, "2024-11-11 14:14:36")
        self.assertEqual(build.branch, "main")

    def test_the_update_stops_at_the_build_number(self):
        # 2.0 had two components before its build number, 1.6.1 three.
        self.assertEqual(probe.update_of("2.0.1702353.294543"), "2.0")
        self.assertEqual(probe.update_of("1.6.1.1680013.279957"), "1.6.1")

    def test_other_lines_are_not_builds(self):
        self.assertIsNone(probe.parse_build_string("NVIDIA Driver Version 61692"))
        self.assertIsNone(probe.parse_build_string("BuildString: [Build:2026-01-01 00:00:00] no version"))


class LogsTest(unittest.TestCase):
    def test_builds_are_read_per_run_and_changes_kept(self):
        with tempfile.TemporaryDirectory() as folder:
            log_dir = Path(folder)
            runs = [
                ("2026-08-20-10-00-00", LINE_117.replace("2.1.0.1711091", "2.0.1702353")),
                ("2026-09-23-18-39-29", LINE_117),
                ("2026-09-26-21-09-48", LINE_117),
            ]
            for when, line in runs:
                (log_dir / ("Anno117_LOGFILE_HOST_%s.log" % when)).write_text(
                    "a line before\n%s\nand after\n" % line, encoding="utf-8"
                )
            (log_dir / "sysinfo.txt").write_text("not a log", encoding="utf-8")

            builds = probe.logged_builds(log_dir)
            self.assertEqual([b.build.update for b in builds], ["2.0", "2.1.0", "2.1.0"])
            self.assertEqual(builds[-1].when.strftime("%Y-%m-%d %H:%M"), "2026-09-26 21:09")
            self.assertEqual([b.build.update for b in probe.changes(builds)], ["2.0", "2.1.0"])

    def test_a_missing_folder_has_no_builds(self):
        self.assertEqual(probe.logged_builds(Path(tempfile.gettempdir()) / "no-such-log-dir"), [])


class InstalledTest(unittest.TestCase):
    """The answer is only given while the newest log is newer than the game."""

    def setUp(self):
        import datetime
        import os

        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        root = Path(self._tmp.name)
        self.install = root / "Anno 117 - Pax Romana"
        exe = self.install / "Bin" / "Win64" / "Anno117.exe"
        exe.parent.mkdir(parents=True)
        exe.write_bytes(b"")
        self.logs = root / "log"
        self.logs.mkdir()
        (self.logs / "Anno117_LOGFILE_HOST_2026-09-26-21-09-48.log").write_text(
            LINE_117 + "\n", encoding="utf-8"
        )
        run = datetime.datetime(2026, 9, 26, 21, 9, 48).timestamp()

        def exe_written(seconds):
            os.utime(exe, (seconds, seconds))

        self.exe_written = exe_written
        self.run = run

    def test_the_log_answers_when_the_game_ran_since_it_was_written(self):
        self.exe_written(self.run - 3600)
        build = probe.installed(self.install, "anno117", logs=self.logs)
        self.assertEqual((build.update, build.version), ("2.1.0", "2.1.0.1711091.299885"))

    def test_no_answer_when_the_game_was_updated_after_its_last_run(self):
        self.exe_written(self.run + 3600)
        self.assertIsNone(probe.installed(self.install, "anno117", logs=self.logs))

    def test_no_answer_without_logs(self):
        self.assertIsNone(probe.installed(self.install, "anno117", logs=self.logs / "none"))
        self.assertIsNone(probe.installed(None, "anno117", logs=self.logs))


class RememberedTest(unittest.TestCase):
    def test_the_update_is_kept_against_the_datas_fingerprint(self):
        build = probe.parse_build_string(LINE_117)
        probe.remember("abc", build)
        self.assertEqual(
            probe.remembered("abc"), {"update": "2.1.0", "version": "2.1.0.1711091.299885"}
        )
        self.assertIsNone(probe.remembered("other"))
        self.assertIsNone(probe.remembered(""))


if __name__ == "__main__":
    unittest.main()
