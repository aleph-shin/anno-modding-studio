"""Every release's files, read by this build.

From 0.1.0 on, each release keeps a file of every format it writes in
``tests/fixtures/formats/<release>/`` (``tools/format_fixtures.py``, run by the
release checklist).  Every later build reads each one here: what it holds is
read, written back without losing anything, and the format after it is
refused, never guessed at.  A change of format that fails here needs a
migration, not a new fixture (docs/standards/code-standards.md, section 11).
"""

from __future__ import annotations

import json
import os
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
from anno_modding_studio.core import (  # noqa: E402
    backup,
    changes,
    convert,
    dataversion,
    gameversion,
    library,
    projects,
    records,
    saving,
    versioning,
)
from anno_modding_studio.core.changes import Loadout, LoadoutError  # noqa: E402

FORMATS = ROOT / "tests" / "fixtures" / "formats"
RELEASES = sorted((path for path in FORMATS.iterdir() if path.is_dir()), key=lambda path: versioning.key(path.name))
NEWEST = RELEASES[-1]


def _without(data, *keys):
    return {key: value for key, value in data.items() if key not in keys}


class EveryRelease(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)

    def copy_of(self, release: Path, name: str) -> Path:
        source = release / name
        target = self.tmp / release.name / name
        if source.is_dir():
            shutil.copytree(source, target)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
        return target

    def bumped(self, path: Path, key: str = "format") -> None:
        data = json.loads(path.read_text(encoding="utf-8"))
        data[key] += 1
        path.write_text(json.dumps(data), encoding="utf-8")

    def bumped_past(self, release: Path, path: Path, current: int) -> None:
        """``path`` said to be of the format after its own.  For the
        release this build is, that is its own number plus one, not this
        build's: a file left at an older number than the build writes then
        reads, and fails here.  For an earlier release it is past what this
        build reads too, since a later build reads that release's next
        format by a migration."""
        data = json.loads(path.read_text(encoding="utf-8"))
        ours = release.name == __version__
        data["format"] = data["format"] + 1 if ours else max(data["format"], current) + 1
        path.write_text(json.dumps(data), encoding="utf-8")

    def test_there_is_a_release_to_read(self):
        self.assertIn("0.1.0", [release.name for release in RELEASES])

    def in_studio(self, release: Path):
        """The release's files where the Studio looks for them: its
        loadouts' projects are named relative to the Studio's folder."""
        home = self.tmp / release.name
        if (release / "projects").is_dir() and not (home / "projects").exists():
            self.copy_of(release, "projects")
        return mock.patch.dict(os.environ, {"ANNO_STUDIO_HOME": str(home)})

    def test_each_loadout_is_read_and_written_back_whole(self):
        for release in RELEASES:
            for fixture in sorted(release.glob("*" + changes.LOADOUT_SUFFIX)):
                with self.subTest(release=release.name, loadout=fixture.name), self.in_studio(release):
                    path = self.copy_of(release, fixture.name)
                    before = json.loads(path.read_text(encoding="utf-8"))
                    loadout = Loadout.load(path)
                    self.assertTrue(loadout.changes)
                    self.assertFalse([origin for origin in loadout.projects if loadout.held(origin)])
                    loadout.save(path)
                    after = json.loads(path.read_text(encoding="utf-8"))
                    self.assertEqual(_without(after, "written_by"), _without(before, "written_by"))

    def test_each_project_is_read_and_written_back_whole(self):
        for release in RELEASES:
            for fixture in sorted(release.glob("*" + changes.LOADOUT_SUFFIX)):
                with self.subTest(release=release.name, loadout=fixture.name), self.in_studio(release):
                    path = self.copy_of(release, fixture.name)
                    loadout = Loadout.load(path)
                    self.assertTrue(loadout.projects)
                    kept = {origin: projects.where(ref.path) for origin, ref in loadout.projects.items()}
                    before = {origin: projects.read(where) for origin, where in kept.items()}
                    # Moved and back: each project is written, and holds
                    # what it held.
                    for origin in kept:
                        if origin:
                            loadout.layer(origin).description = "moved"
                        else:
                            loadout.own.description = "moved"
                    loadout.save(path)
                    for origin in kept:
                        if origin:
                            loadout.layer(origin).description = before[origin]["details"].get("description", "")
                        else:
                            loadout.own.description = before[origin]["details"].get("description", "")
                    loadout.save(path)
                    for origin, where in kept.items():
                        after = projects.read(where)
                        self.assertEqual(after["revision"], before[origin]["revision"] + 2)
                        self.assertEqual(_without(after, "written_by", "revision"),
                                         _without(before[origin], "written_by", "revision"))

    def test_the_project_format_after_each_is_refused(self):
        for release in RELEASES:
            for project in sorted((release / "projects").glob("*" + projects.PROJECT_SUFFIX)):
                with self.subTest(release=release.name, project=project.name):
                    path = self.copy_of(release, "projects/" + project.name)
                    self.bumped_past(release, path, projects.PROJECT_FORMAT)
                    with self.assertRaises(projects.ProjectError):
                        projects.read(path)

    def test_the_loadout_format_after_each_is_refused(self):
        for release in RELEASES:
            for fixture in sorted(release.glob("*" + changes.LOADOUT_SUFFIX)):
                with self.subTest(release=release.name, loadout=fixture.name):
                    path = self.copy_of(release, fixture.name)
                    self.bumped_past(release, path, changes.LOADOUT_FORMAT)
                    with self.assertRaises(LoadoutError):
                        Loadout.load(path)

    def test_the_library_records_are_read_and_kept_when_one_changes(self):
        for release in RELEASES:
            with self.subTest(release=release.name):
                lib = self.copy_of(release, "library")
                held = library.registry(lib)
                self.assertTrue(held)
                self.assertTrue(library.links(lib))
                self.assertTrue(library.references(lib))
                self.assertTrue(library.live(lib))
                library.record(lib, "Another", source=library.ZIP)
                self.assertEqual({**held, "Another": {"source": library.ZIP}}, library.registry(lib))

    def test_a_library_record_of_the_next_format_is_not_written_over(self):
        writes = {
            library.REGISTRY: lambda lib: library.record(lib, "Another", source=library.ZIP),
            library.LINKS: lambda lib: library.write_links(lib, {}),
            library.REFERENCES: lambda lib: library.check_records(lib),
            library.LIVE: lambda lib: library.set_live(lib, "another"),
        }
        for release in RELEASES:
            for name, write in writes.items():
                with self.subTest(release=release.name, record=name):
                    lib = self.copy_of(release, "library")
                    self.bumped(lib / name)
                    before = (lib / name).read_bytes()
                    with self.assertRaises(records.RecordError):
                        write(lib)
                    self.assertEqual((lib / name).read_bytes(), before)
                    shutil.rmtree(lib)

    def test_each_conversion_can_be_undone_and_the_next_format_cannot(self):
        for release in RELEASES:
            with self.subTest(release=release.name):
                lib = self.copy_of(release, "library")
                for path in sorted((lib / "conversions").glob("*.json")):
                    self.assertEqual(convert.cannot_undo(convert.read_record(path)), "")
                    self.bumped(path)
                    self.assertTrue(convert.cannot_undo(convert.read_record(path)))

    def test_each_save_s_journal_can_be_settled_and_the_next_format_cannot(self):
        for release in RELEASES:
            with self.subTest(release=release.name):
                journals = self.copy_of(release, saving.JOURNALS)
                found = sorted(journals.glob("*.json"))
                self.assertTrue(found)
                for path in found:
                    entries, problem = saving.read_journal(path)
                    self.assertEqual(problem, "")
                    self.assertTrue(entries)
                    self.bumped_past(release, path, saving.JOURNAL_FORMAT)
                    self.assertEqual(saving.read_journal(path)[0], [])
                    self.assertTrue(saving.read_journal(path)[1])

    def test_each_backup_is_read_and_the_next_format_is_kept_untouched(self):
        for release in RELEASES:
            with self.subTest(release=release.name):
                save = self.copy_of(release, "backup")
                kept = backup.read(save)
                self.assertEqual(kept.refusal, "")
                self.assertTrue(kept.files)
                self.bumped(save / backup.MANIFEST)
                kept = backup.read(save)
                self.assertTrue(kept.refusal)
                self.assertTrue(kept.kept_for_good)
                self.assertEqual(backup.restore(kept)[0], 0)

    def test_the_game_updates_remembered_are_read(self):
        for release in RELEASES:
            with self.subTest(release=release.name):
                path = self.copy_of(release, gameversion.REMEMBERED_NAME)
                with mock.patch.object(gameversion, "_remembered_path", return_value=path):
                    fingerprint = next(iter(records.read(path, gameversion.REMEMBERED_FORMAT).data))
                    self.assertTrue(gameversion.remembered(fingerprint))
                    self.bumped(path)
                    before = path.read_bytes()
                    gameversion.remember("0" * 40, gameversion.Build("", "1", "1", "", "", ""))
                    self.assertEqual(path.read_bytes(), before)

    def test_the_data_version_is_read(self):
        for release in RELEASES:
            with self.subTest(release=release.name):
                path = self.copy_of(release, dataversion.FILE_NAME)
                stamp = dataversion.read(path.parent)
                self.assertLessEqual(stamp.data_version, dataversion.DATA_VERSION)
                self.assertEqual(stamp.app_version, release.name)


# Each file the fixture tool writes, by where it is in a release's folder,
# and the format this build writes it in.
STAMPS = (
    ("*" + changes.LOADOUT_SUFFIX, changes.LOADOUT_FORMAT),
    ("projects/*" + projects.PROJECT_SUFFIX, projects.PROJECT_FORMAT),
    ("library/" + library.REGISTRY, library.RECORD_FORMAT),
    ("library/" + library.LINKS, library.RECORD_FORMAT),
    ("library/" + library.REFERENCES, library.RECORD_FORMAT),
    ("library/" + library.LIVE, library.RECORD_FORMAT),
    ("library/conversions/*.json", convert.JOURNAL_FORMAT),
    (saving.JOURNALS + "/*.json", saving.JOURNAL_FORMAT),
    ("backup/" + backup.MANIFEST, backup.FORMAT),
    (gameversion.REMEMBERED_NAME, gameversion.REMEMBERED_FORMAT),
)


class TheNewestReleaseIsThisBuild(unittest.TestCase):
    """While this build is the newest release that has fixtures, each of
    them says the format this build writes.  One left at an older number
    is read by the migration a later build would need, and the test above
    misses it: the save journal stayed at 1 after the build went to 2.
    Caught here, it is regenerated before the tag freezes it."""

    def test_each_fixture_says_the_format_this_build_writes(self):
        if NEWEST.name != __version__:
            self.skipTest("this build is %s, and the newest fixtures are %s's" % (__version__, NEWEST.name))
        seen = set()
        for pattern, current in STAMPS:
            found = sorted(NEWEST.glob(pattern))
            self.assertTrue(found, pattern)
            for path in found:
                seen.add(path)
                with self.subTest(fixture=str(path.relative_to(NEWEST))):
                    self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["format"], current)
        stamp = dataversion.read(NEWEST)
        self.assertEqual((stamp.data_version, stamp.app_version), (dataversion.DATA_VERSION, __version__))
        seen.add(NEWEST / dataversion.FILE_NAME)
        # Every file the tool writes is one of those, but the copies a
        # backup holds: a new format is added here when it is added there.
        unknown = [str(path.relative_to(NEWEST)) for path in NEWEST.rglob("*")
                   if path.is_file() and path not in seen and path.parent != NEWEST / "backup"]
        self.assertEqual(unknown, [])


if __name__ == "__main__":
    unittest.main()
