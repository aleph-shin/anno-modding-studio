"""The built Studio and its release: what tools/build_release.py keeps out of
the folder, what it adds, how the zip is named, the lock it builds from, the
workflow that runs it, and what the package does differently once built.

A frozen build was never made before 0.1.0, and nothing tested what one
would carry: the audit found the freezing tool would have taken Qt Virtual
Keyboard (GPL-only) and Qt PDF along, and a wheel built with pip had neither
icons nor German.  The build itself needs PyInstaller and minutes, and is
the release workflow's to run; what decides it is tested here.
"""

from __future__ import annotations

import fnmatch
import hashlib
import os
import re
import subprocess
import sys
import tempfile
import time
import unittest
import zipfile
from pathlib import Path
from unittest import mock

import _isolation  # noqa: F401 -- first: keeps the suite out of real files under any runner

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

import build_release as release  # noqa: E402

from anno_modding_studio import GUIDE, REPO_URL, __version__, source_url  # noqa: E402


class TestWhatTheFolderKeeps(unittest.TestCase):
    def test_qt_s_gpl_only_and_unneeded_modules_stay_out(self):
        for dest in ("PySide6/Qt6VirtualKeyboard.dll", "PySide6/Qt6Pdf.dll",
                     "PySide6/Qt6Quick.dll", "PySide6/Qt6Qml.dll",
                     "PySide6/opengl32sw.dll",
                     "PySide6/plugins/platforminputcontexts/qtvirtualkeyboardplugin.dll",
                     "PySide6/plugins/imageformats/qpdf.dll"):
            self.assertFalse(release.kept_in_bundle(dest), dest)

    def test_the_plugins_the_studio_uses_stay_and_no_others(self):
        kept = ("PySide6/plugins/platforms/qwindows.dll",
                "PySide6/plugins/platforms/qoffscreen.dll",
                "PySide6\\plugins\\imageformats\\qsvg.dll",
                "PySide6/plugins/imageformats/qico.dll",
                "PySide6/plugins/iconengines/qsvgicon.dll")
        gone = ("PySide6/plugins/imageformats/qjpeg.dll",
                "PySide6/plugins/imageformats/qwebp.dll",
                "PySide6/plugins/platforms/qdirect2d.dll",
                "PySide6/plugins/tls/qopensslbackend.dll",
                "PySide6/plugins/networkinformation/qnetworklistmanager.dll",
                "PySide6/plugins/styles/qmodernwindowsstyle.dll",
                "PySide6/plugins/generic/qtuiotouchplugin.dll")
        for dest in kept:
            self.assertTrue(release.kept_in_bundle(dest), dest)
        for dest in gone:
            self.assertFalse(release.kept_in_bundle(dest), dest)

    def test_qt_s_catalog_is_kept_for_each_language_the_studio_has(self):
        self.assertIn("de", release.studio_languages())
        self.assertTrue(release.kept_in_bundle("PySide6/translations/qtbase_de.qm"))
        self.assertFalse(release.kept_in_bundle("PySide6/translations/qtbase_fr.qm"))
        self.assertFalse(release.kept_in_bundle("PySide6/translations/assistant_de.qm"))

    def test_modules_nothing_imports_are_left_out_of_the_freeze(self):
        """studio.spec hands these to the freezing tool."""
        for name in ("tkinter", "lxml.objectify", "lxml.html"):
            self.assertIn(name, release.EXCLUDED_MODULES)
        spec = (ROOT / "tools" / "release" / "studio.spec").read_text(encoding="utf-8")
        self.assertIn("release.EXCLUDED_MODULES", spec)
        self.assertIn("release.kept_in_bundle", spec)

    def test_python_s_own_openssl_stays_and_another_program_s_goes(self):
        self.assertTrue(release.kept_in_bundle("libcrypto-3.dll"))
        self.assertTrue(release.kept_in_bundle("libssl-3.dll"))
        self.assertFalse(release.kept_in_bundle("libcrypto-3-x64.dll"))
        self.assertFalse(release.kept_in_bundle("libssl-3-x64.dll"))
        self.assertTrue(release.from_this_python(os.path.join(sys.base_prefix, "DLLs", "x.dll")))
        self.assertFalse(release.from_this_python("C:/Program Files/Git/mingw64/bin/libssl-3-x64.dll"))

    def test_a_folder_is_checked_for_what_is_there_and_what_is_not(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            (folder / "_internal" / "PySide6").mkdir(parents=True)
            (folder / "_internal" / "PySide6" / "Qt6Pdf.dll").write_bytes(b"")
            (folder / "_internal" / "PySide6" / "Qt6Core.dll").write_bytes(b"")
            problems = release.check_bundle(folder)
        self.assertTrue(any("Qt6Pdf.dll" in problem for problem in problems))
        self.assertFalse(any("Qt6Core.dll" in problem for problem in problems))
        self.assertTrue(any(problem.startswith("missing") and "AnnoModdingStudio.exe" in problem
                            for problem in problems))


class TestTheRelease(unittest.TestCase):
    def test_the_zip_and_the_tag_are_named_for_the_version(self):
        self.assertEqual(release.version(), __version__)
        self.assertEqual(release.repo_url(), REPO_URL)
        self.assertEqual(release.guide(), GUIDE)
        self.assertEqual(release.zip_name("1.2.3"), "AnnoModdingStudio-1.2.3-windows-x64.zip")
        self.assertTrue(release.tag_matches("v" + __version__, __version__))
        self.assertFalse(release.tag_matches(__version__, __version__))
        self.assertFalse(release.tag_matches("v9.9.9", __version__))

    def test_the_exe_s_version_resource_is_the_version(self):
        text = release.version_resource("1.2.3")
        self.assertIn("filevers=(1, 2, 3, 0)", text)
        self.assertIn("StringStruct('ProductVersion', '1.2.3')", text)
        self.assertIn("StringStruct('OriginalFilename', 'AnnoModdingStudio.exe')", text)

    def test_the_mit_notices_are_the_ones_the_notices_quote(self):
        found = release.mit_notices()
        self.assertIn("Alexander Guettler", found["MIT-anno-mod-loader.txt"])
        self.assertIn("not affiliated in any way with Ubisoft", found["MIT-iModYourAnno.txt"])

    def test_every_licence_text_section_5_names_is_there_or_made_by_the_build(self):
        """THIRD-PARTY-NOTICES.md, section 5: each file it names for
        licenses/ is committed, or copied by add_notices."""
        section = (ROOT / "THIRD-PARTY-NOTICES.md").read_text(encoding="utf-8").split(
            "## 5.", 1)[1]
        named = set(re.findall(r"`([\w.-]+\.txt)`", section))
        committed = {path.name for path in (ROOT / "tools" / "release" / "licenses").iterdir()}
        made = {"LICENSE.txt", "THIRD-PARTY-NOTICES.txt", "SOURCES.txt", "GPL-3.0.txt",
                "Python-LICENSE.txt", "lxml-LICENSE.txt", "lxml-LICENSES.txt",
                "MIT-anno-mod-loader.txt", "MIT-iModYourAnno.txt", "COPYING.txt",
                "PyInstaller-COPYING.txt"}
        self.assertEqual(named - committed - made, set())
        source = (ROOT / "tools" / "build_release.py").read_text(encoding="utf-8")
        for name in made - {"COPYING.txt"}:
            self.assertIn('"%s"' % name, source)

    def test_the_qt_notices_are_for_the_qt_the_lock_pins(self):
        pinned = release.locked()["pyside6-essentials"]
        folder = ROOT / "tools" / "release" / "licenses" / "Qt"
        self.assertEqual(release.notices_qt_version(folder), ".".join(pinned.split(".")[:2]))
        self.assertIsNone(release.stale_qt_notices(folder, pinned))

    def test_qt_notices_for_another_qt_stop_the_zip(self):
        folder = ROOT / "tools" / "release" / "licenses" / "Qt"
        named = release.notices_qt_version(folder)
        major, minor = named.split(".")
        newer = "%s.%d.0" % (major, int(minor) + 1)
        self.assertIn("are for Qt %s, and this build carries Qt %s" % (named, newer),
                      release.stale_qt_notices(folder, newer))
        self.assertIsNone(release.stale_qt_notices(folder, named + ".3"))
        with tempfile.TemporaryDirectory(dir=ROOT / "tools" / "release") as empty:
            self.assertIn("for Qt an unnamed version",
                          release.stale_qt_notices(Path(empty), named + ".0"))

    def test_the_licence_texts_are_the_published_ones(self):
        """By their digests: the texts at gnu.org and apache.org."""
        licenses = ROOT / "tools" / "release" / "licenses"
        published = {
            "LGPL-3.0.txt": "da7eabb7bafdf7d3ae5e9f223aa5bdc1eece45ac569dc21b3b037520b4464768",
            "LGPL-2.1.txt": "dc626520dcd53a22f727af3ee42c770e56c97a64fe3adb063799d8ab032fe551",
            "Apache-2.0.txt": "cfc7749b96f63bd31c3c42b5c471bf756814053e847c10f3eb003417bc523d30",
        }
        for name, digest in published.items():
            self.assertEqual(hashlib.sha256((licenses / name).read_bytes()).hexdigest(), digest, name)
        self.assertEqual(hashlib.sha256((ROOT / "LICENSE").read_bytes()).hexdigest(),
                         "3972dc9744f6499f0f9b2dbf76696f2ae7ad8af9b23dde66d6af86c9dfb36986")

    def test_the_zip_holds_the_folder_at_its_root_with_its_checksum_beside(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / "AnnoModdingStudio"
            (folder / "_internal").mkdir(parents=True)
            (folder / "AnnoModdingStudio.exe").write_bytes(b"exe")
            (folder / "_internal" / "python3.dll").write_bytes(b"dll")
            target = Path(tmp) / release.zip_name("1.2.3")
            digest = release.make_zip(folder, target)
            with zipfile.ZipFile(target) as made:
                self.assertEqual(sorted(made.namelist()),
                                 ["AnnoModdingStudio.exe", "_internal/python3.dll"])
            self.assertEqual(digest, hashlib.sha256(target.read_bytes()).hexdigest())
            self.assertEqual(Path(str(target) + ".sha256").read_text(encoding="ascii"),
                             "%s *%s\n" % (digest, target.name))

    def test_the_zip_s_times_are_the_commit_s_in_utc_wherever_it_is_built(self):
        """The release workflow builds on a UTC runner: a build of the same
        commit six hours west of UTC stamps the same times, and a commit
        clamped to 1980 does not turn into 1979 there."""
        west = lambda seconds=None: time.gmtime(  # noqa: E731
            (time.time() if seconds is None else seconds) - 6 * 3600)
        for commit, stamp in ((1761195120.0, (2025, 10, 23, 4, 52, 0)),
                              (315532800.0, (1980, 1, 1, 0, 0, 0))):
            with tempfile.TemporaryDirectory() as tmp:
                folder = Path(tmp) / "AnnoModdingStudio"
                folder.mkdir()
                (folder / "AnnoModdingStudio.exe").write_bytes(b"exe")
                target = Path(tmp) / release.zip_name("1.2.3")
                with mock.patch.object(release, "_commit_time", return_value=commit), \
                        mock.patch.object(time, "localtime", west):
                    release.make_zip(folder, target)
                with zipfile.ZipFile(target) as made:
                    self.assertEqual(made.infolist()[0].date_time, stamp)

    def test_the_draft_s_text_names_the_zip_its_digest_and_the_source(self):
        text = release.release_notes("1.2.3", "ab" * 32)
        self.assertIn("AnnoModdingStudio-1.2.3-windows-x64.zip", text)
        self.assertIn("ab" * 32, text)
        self.assertIn("/tree/v1.2.3", text)
        self.assertIn("SmartScreen", text)


class TestTheLock(unittest.TestCase):
    def test_every_package_is_pinned_with_its_hashes(self):
        text = (ROOT / "requirements-release.txt").read_text(encoding="utf-8")
        pins = re.findall(r"(?m)^([A-Za-z0-9_.-]+)==\S+ \\\n((?:    --hash=sha256:[0-9a-f]{64}.*\n)+)", text)
        self.assertEqual(len(pins), len(release.locked()))
        for name in ("pyinstaller", "pyside6-essentials", "shiboken6", "lxml"):
            self.assertIn(name, release.locked())

    def test_it_meets_the_studio_s_own_floors(self):
        def numbers(text):
            return tuple(int(part) for part in re.findall(r"\d+", text)[:3])

        pins = release.locked()
        for line in (ROOT / "requirements.txt").read_text(encoding="utf-8").splitlines():
            name, floor = line.split(">=")
            self.assertGreaterEqual(numbers(pins[name.lower()]), numbers(floor), name)

    def test_the_build_is_from_essentials_and_never_the_add_ons(self):
        pins = release.locked()
        self.assertNotIn("pyside6", pins)
        self.assertNotIn("pyside6-addons", pins)
        with mock.patch.object(release, "installed",
                               side_effect=lambda name: "6.11.2" if name == "PySide6-Addons" else None):
            problems = release.check_environment(unlocked=True)
        self.assertTrue(any("PySide6-Addons" in problem for problem in problems))

    def test_the_build_stops_when_pyside6_does_not_import(self):
        """Uninstalling PySide6-Addons takes PySide6/__init__.py with it, and a
        freeze from there finished with no shiboken6 and no Qt plugins."""
        self.assertIsNone(release.qt_import_error())
        with mock.patch.object(release, "qt_import_error", return_value="DLL load failed"):
            problems = release.check_environment(unlocked=True)
        self.assertTrue(any("DLL load failed" in problem and "PySide6-Essentials" in problem
                            for problem in problems))


class TestTheWorkflow(unittest.TestCase):
    TEXT = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")

    def test_it_runs_on_a_version_tag_and_only_drafts(self):
        self.assertIn('tags: ["v*.*.*"]', self.TEXT)
        self.assertIn("check-tag", self.TEXT)
        self.assertIn("--draft", self.TEXT)
        self.assertNotIn("--prerelease", self.TEXT)
        self.assertIn('--title "Anno Modding Studio $version (beta)"', self.TEXT)

    def test_it_builds_from_the_lock_and_runs_the_self_test(self):
        self.assertIn("--require-hashes", self.TEXT)
        self.assertIn("requirements-release.txt", self.TEXT)
        for step in ("freeze", "self-test", "package"):
            self.assertIn("tools/build_release.py %s" % step, self.TEXT)

    def test_it_may_write_only_in_its_one_job_and_pins_each_action(self):
        self.assertIn("\npermissions: {}\n", self.TEXT)
        self.assertEqual(self.TEXT.count("contents: write"), 1)
        uses = re.findall(r"uses: (\S+)", self.TEXT)
        self.assertTrue(uses)
        for action in uses:
            self.assertRegex(action, r"@[0-9a-f]{40}$")


class TestThePackage(unittest.TestCase):
    def test_the_wheel_takes_every_file_the_package_reads_beside_its_modules(self):
        """pyproject's package-data; a wheel had no icons and no German."""
        text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
        globs = re.search(r'anno_modding_studio = \[(.*?)\]', text.split(
            "[tool.setuptools.package-data]", 1)[1]).group(1)
        patterns = re.findall(r'"([^"]+)"', globs)
        package = ROOT / "anno_modding_studio"
        for folder in ("resources", "locale"):
            for path in (package / folder).rglob("*"):
                if path.is_file():
                    relative = path.relative_to(package).as_posix()
                    self.assertTrue(
                        any(fnmatch.fnmatch(relative, pattern) and
                            relative.count("/") == pattern.count("/") for pattern in patterns),
                        relative,
                    )

    def test_a_built_studio_reads_its_own_release_s_documents(self):
        self.assertEqual(source_url("LICENSE"), REPO_URL + "/blob/main/LICENSE")
        with mock.patch.object(sys, "frozen", True, create=True):
            self.assertEqual(source_url(GUIDE),
                             "%s/blob/v%s/docs/guide.md" % (REPO_URL, __version__))

    def test_a_built_studio_opens_the_guide_on_github(self):
        from PySide6.QtWidgets import QApplication

        from anno_modding_studio.ui import help_tip

        QApplication.instance() or QApplication([])
        with mock.patch.object(sys, "frozen", True, create=True), \
                mock.patch.object(help_tip.QDesktopServices, "openUrl") as opened:
            help_tip.open_guide()
        self.assertEqual(opened.call_args.args[0].toString(),
                         "%s/blob/v%s/docs/guide.md" % (REPO_URL, __version__))

    def test_the_self_test_passes_from_the_source(self):
        """As the release workflow runs it on the built Studio; here on the
        source, in a process of its own, as the exe would be."""
        done = subprocess.run(
            [sys.executable, "-m", "anno_modding_studio", "--self-test"],
            cwd=ROOT, capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=300,
        )
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn("ok    reading", done.stdout)
        self.assertTrue(done.stdout.rstrip().endswith("passed"), done.stdout)


if __name__ == "__main__":
    unittest.main()
