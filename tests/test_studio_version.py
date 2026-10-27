"""The Studio's version, written once, and what is stamped with it.

``anno_modding_studio.__version__`` is the one place the Studio's version is
written; pyproject.toml reads it.  Before 0.1.0 it said 1.0.0, pyproject said
0.1.0, the mod.io User-Agent said 0.1, and nothing read the first.  And
each data folder says which version of the Studio has used it, so that an
older one does not start over data a newer one wrote.
"""

from __future__ import annotations

import ast
import os
import re
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import _isolation  # noqa: F401 -- first: keeps the suite out of real files under any runner

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from anno_modding_studio import __version__  # noqa: E402
from anno_modding_studio.core import dataversion, disk, modio, places  # noqa: E402
from anno_modding_studio.core.changes import Loadout  # noqa: E402

# A User-Agent, or anything like one, with a version written into it.
_VERSIONED_NAME = re.compile(r"(?i)anno[-_ ]?modding[-_ ]?studio[\w-]*/\d")


def _strings(path: Path):
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            yield node.lineno, node.value


class TheVersionTest(unittest.TestCase):
    def test_pyloadout_reads_the_package_s_version(self):
        text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
        loadout = text.split("[project]", 1)[1].split("\n[", 1)[0]
        self.assertIsNone(re.search(r"(?m)^version\s*=", loadout))
        self.assertRegex(loadout, r'(?m)^dynamic = \["version"\]$')
        self.assertIn('[tool.setuptools.dynamic]\nversion = {attr = "anno_modding_studio.__version__"}', text)

    def test_it_is_a_release_number(self):
        self.assertRegex(__version__, r"^\d+\.\d+\.\d+$")

    def test_no_other_copy_of_it_is_written_anywhere(self):
        found = []
        sources = [*(ROOT / "anno_modding_studio").rglob("*.py"), *(ROOT / "tools").rglob("*.py")]
        for path in sources:
            if path == ROOT / "anno_modding_studio" / "__init__.py":
                continue
            for line, text in _strings(path):
                if text == __version__ or _VERSIONED_NAME.search(text):
                    found.append("%s:%d %r" % (path.relative_to(ROOT).as_posix(), line, text))
        self.assertEqual(found, [])

    def test_the_check_finds_a_written_version(self):
        self.assertTrue(_VERSIONED_NAME.search("anno-modding-studio/0.1"))
        self.assertTrue(_VERSIONED_NAME.search("AnnoModdingStudio/2.0"))
        self.assertFalse(_VERSIONED_NAME.search("AnnoModdingStudio"))

    def test_mod_io_is_told_which_studio_is_asking(self):
        self.assertEqual(modio.USER_AGENT, "anno-modding-studio/%s" % __version__)

    def test_a_loadout_file_says_which_studio_wrote_it(self):
        self.assertEqual(Loadout().to_dict()["written_by"], __version__)


class DataVersionCase(unittest.TestCase):
    """The Studio's two folders, each a temporary one of this test's."""

    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="ams-data-version-"))
        self.addCleanup(shutil.rmtree, self.root, True)
        self.home, self.local = self.root / "home", self.root / "local"
        patcher = mock.patch.dict(os.environ, {
            places.ENV_HOME: str(self.home),
            "LOCALAPPDATA": str(self.local),
            places.ENV_FOLDER: "none",
        })
        patcher.start()
        self.addCleanup(patcher.stop)

    def stamp(self, folder: Path, data_version, app_version="9.0.0") -> None:
        folder.mkdir(parents=True, exist_ok=True)
        disk.write_json(folder / dataversion.FILE_NAME,
                        {"data_version": data_version, "app_version": app_version})

    def said(self, folder: Path) -> dict:
        return disk.read_json(folder / dataversion.FILE_NAME, {})


class TheFoldersTest(DataVersionCase):
    def test_what_you_make_and_what_the_studio_keeps_are_each_stamped(self):
        self.assertEqual(places.data_folders(), [self.home, self.local / places.NAME])
        dataversion.record()
        for folder in places.data_folders():
            self.assertEqual(self.said(folder), {
                "data_version": dataversion.DATA_VERSION, "app_version": __version__,
            })

    def test_one_folder_is_one_stamp(self):
        one = self.root / "one"
        with mock.patch.dict(os.environ, {places.ENV_FOLDER: str(one)}):
            os.environ.pop(places.ENV_HOME)
            self.assertEqual(places.data_folders(), [one])
            dataversion.record()
        self.assertEqual(self.said(one)["app_version"], __version__)
        self.assertFalse(self.home.exists())


class RefusingTest(DataVersionCase):
    def test_a_folder_a_newer_data_version_used_is_refused_and_named(self):
        self.stamp(self.local / places.NAME, dataversion.DATA_VERSION + 1, "0.4.2")
        newer = dataversion.newer_than_this()
        self.assertEqual(newer.folder, self.local / places.NAME)
        said = dataversion.refusal(newer)
        self.assertIn("last used by version 0.4.2", said)
        self.assertIn("(%s)" % __version__, said)
        self.assertIn(str(self.local / places.NAME), said)

    def test_a_refused_folder_is_never_written_over(self):
        self.stamp(self.home, dataversion.DATA_VERSION + 1, "0.4.2")
        dataversion.record()
        self.assertEqual(self.said(self.home), {
            "data_version": dataversion.DATA_VERSION + 1, "app_version": "0.4.2",
        })

    def test_one_that_does_not_say_which_version_is_refused_all_the_same(self):
        self.home.mkdir()
        disk.write_json(self.home / dataversion.FILE_NAME, {"data_version": dataversion.DATA_VERSION + 1})
        self.assertIn("last used by a newer version", dataversion.refusal(dataversion.newer_than_this()))

    def test_the_same_or_an_older_data_version_is_this_build_s_to_use(self):
        self.stamp(self.home, dataversion.DATA_VERSION, "9.0.0")
        self.stamp(self.local / places.NAME, dataversion.DATA_VERSION - 1, "0.0.1")
        self.assertIsNone(dataversion.newer_than_this())

    def test_a_file_that_is_not_a_stamp_is_no_stamp(self):
        self.home.mkdir()
        (self.home / dataversion.FILE_NAME).write_text("{not json", encoding="utf-8")
        self.assertIsNone(dataversion.read(self.home))
        self.assertIsNone(dataversion.newer_than_this())
        dataversion.record()
        self.assertEqual(self.said(self.home)["data_version"], dataversion.DATA_VERSION)


class RecordingTest(DataVersionCase):
    def test_the_newest_version_to_use_a_folder_stays_named(self):
        # A later release at the same data version was here first; going
        # back to this one leaves it named, as the one to go forward to.
        self.stamp(self.home, dataversion.DATA_VERSION, "9.0.0")
        dataversion.record()
        self.assertEqual(self.said(self.home)["app_version"], "9.0.0")
        self.assertEqual(self.said(self.local / places.NAME)["app_version"], __version__)

    def test_a_newer_data_version_is_written_over_an_older_one(self):
        self.stamp(self.home, dataversion.DATA_VERSION - 1, "9.0.0")
        dataversion.record()
        self.assertEqual(self.said(self.home), {
            "data_version": dataversion.DATA_VERSION, "app_version": __version__,
        })

    def test_a_folder_that_cannot_be_written_is_left(self):
        with mock.patch.object(disk, "write_json", side_effect=PermissionError("read-only")):
            dataversion.record()
        self.assertEqual(self.said(self.home), {})


if __name__ == "__main__":
    unittest.main()
