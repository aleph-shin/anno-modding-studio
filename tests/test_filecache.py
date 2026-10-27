"""The cache over the loadout files: what it answers, and that it can be
deleted, ruined or held by somebody else and still answer from the files."""

from __future__ import annotations

import json
import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import _isolation  # first: keeps the suite out of real files under any runner

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from anno_modding_studio.core import filecache, places  # noqa: E402
from anno_modding_studio.core.changes import (  # noqa: E402
    AIM_MAKE,
    AIM_MANAGE,
    AIM_TWEAK,
    Loadout,
    ModLayer,
)
from anno_modding_studio.core.games import ANNO_117, ANNO_1800  # noqa: E402


class _Folders(unittest.TestCase):
    """A loadouts folder and a local folder of the test's own."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.loadouts = Path(tmp.name) / "loadouts"
        self.loadouts.mkdir()
        local = Path(tmp.name) / "local"
        patched = mock.patch.dict(os.environ, {"LOCALAPPDATA": str(local)})
        patched.start()
        self.addCleanup(patched.stop)
        self.database = places.files_cache()

    def write(self, name, layers, game=ANNO_117, aim=""):
        """A loadout file naming ``layers`` -- (ModID, origin) pairs -- as the
        Studio writes one, set up for ``aim``."""
        loadout = Loadout(game=game, aim=aim)
        loadout.layers = [ModLayer(origin=origin, mod_id=mod_id) for mod_id, origin in layers]
        path = self.loadouts / (name + ".annoload")
        loadout.save(path)
        return path

    def mentioned(self, game=ANNO_117, excluding=None):
        return filecache.mentioned_keys(game, self.loadouts, excluding)


class WhatItAnswersTest(_Folders):
    def test_every_loadout_for_the_game_names_its_mods_by_modid_and_folder(self):
        self.write("Main", [("new", "[Gameplay] New")])
        self.write("Other", [(None, "[Gameplay] Other")])
        self.write("Old game", [("old", "[Gameplay] Old")], game=ANNO_1800)
        self.assertEqual(self.mentioned(), {"new", "[gameplay] new", "[gameplay] other"})
        self.assertEqual(self.mentioned(ANNO_1800), {"old", "[gameplay] old"})

    def test_one_loadout_is_left_out_however_its_path_is_spelt(self):
        main = self.write("Main", [("new", "[Gameplay] New")])
        self.write("Other", [("other", "[Gameplay] Other")])
        spelt = Path(str(main.parent).upper()) / main.name.lower()
        self.assertEqual(self.mentioned(excluding=spelt), {"other", "[gameplay] other"})

    def test_which_loadouts_have_a_mod(self):
        self.write("Main", [("new", "[Gameplay] New")])
        other = self.write("Other", [("new", "[Gameplay] New (2)")])
        self.write("Neither", [("else", "[Gameplay] Else")])
        found = filecache.loadouts_naming({"new"}, ANNO_117, self.loadouts)
        self.assertEqual([path.name for path in found], ["Main.annoload", "Other.annoload"])
        found = filecache.loadouts_naming({"[gameplay] new (2)"}, ANNO_117, self.loadouts)
        self.assertEqual(found, [other])
        self.assertEqual(filecache.loadouts_naming({"new"}, ANNO_1800, self.loadouts), [])

    def test_which_loadouts_are_sets_of_mods_to_play(self):
        """For Add to loadout's What I play now… (section 5.7 of the Export
        tab proposal): offered where none of them is."""
        main = self.write("Main", [("new", "[Gameplay] New")], aim=AIM_MANAGE)
        self.write("Harbours", [], aim=AIM_MAKE)
        self.write("Patching", [], aim=AIM_TWEAK)
        self.assertEqual(filecache.loadouts_to_play(ANNO_117, self.loadouts), [main])
        self.assertEqual(filecache.loadouts_to_play(ANNO_117, self.loadouts, excluding=main), [])
        # And it is no name of a mod's.
        self.assertEqual(self.mentioned(), {"new", "[gameplay] new"})

    def test_a_file_that_names_no_game_is_taken_for_this_one(self):
        """As the scan it replaced had it."""
        (self.loadouts / "Bare.annoload").write_text(
            json.dumps({"layers": [{"mod_id": "bare", "origin": "Bare"}]}), encoding="utf-8")
        self.assertEqual(self.mentioned(), {"bare"})
        self.assertEqual(self.mentioned(ANNO_1800), {"bare"})

    def test_a_file_that_is_not_a_loadout_names_nothing(self):
        (self.loadouts / "Broken.annoload").write_text("{ half a file", encoding="utf-8")
        (self.loadouts / "Odd.annoload").write_text(
            json.dumps({"game": 117, "layers": 5}), encoding="utf-8")
        self.write("Main", [("new", "New")])
        self.assertEqual(self.mentioned(), {"new"})

    def test_the_cache_is_in_the_local_folder_not_in_documents(self):
        self.write("Main", [("new", "New")])
        self.mentioned()
        self.assertTrue(self.database.is_file())
        self.assertEqual(self.database.parent, places.local_home())
        self.assertNotIn(places.studio_home(), self.database.parents)


class ProjectsTest(_Folders):
    """Which project keeps a ModID, and which loadouts have it
    (projects and loadouts, 4b-3)."""

    def setUp(self):
        super().setUp()
        _isolation.forget_loadouts()
        self.addCleanup(_isolation.forget_loadouts)

    def with_a_patch(self, name, game=ANNO_117):
        loadout = Loadout(game=game)
        loadout.layers = [
            ModLayer(origin="Their Mod", mod_id="theirs"),
            ModLayer(origin="My Patch", mod_id="me-patch", patches="Their Mod"),
        ]
        path = self.loadouts / (name + ".annoload")
        loadout.save(path)
        return loadout, path

    def test_a_patch_is_named_by_its_modid_though_its_project_keeps_it(self):
        self.with_a_patch("Main")
        self.assertEqual(self.mentioned() & {"me-patch", "my patch"}, {"me-patch", "my patch"})

    def test_the_project_keeping_a_modid_and_the_loadouts_that_have_it(self):
        from anno_modding_studio.core import projects

        loadout, main = self.with_a_patch("Main")
        # The same ModID for the other game is another mod, in a project of its own.
        elsewhere, _path = self.with_a_patch("Elsewhere", game=ANNO_1800)
        kept = projects.where(loadout.projects["My Patch"].path)
        self.assertEqual(filecache.project_keeping("ME-PATCH", ANNO_117), kept)
        self.assertEqual(filecache.project_keeping("me-patch", ANNO_1800),
                         projects.where(elsewhere.projects["My Patch"].path))
        self.assertIsNone(filecache.project_keeping("nobody", ANNO_117))
        self.assertEqual(filecache.loadouts_with_project(kept, ANNO_117, self.loadouts), [main])
        self.assertEqual(
            filecache.loadouts_with_project(kept, ANNO_117, self.loadouts, excluding=main), [],
        )

    def test_a_project_with_a_blank_modid_keeps_the_one_its_export_goes_out_under(self):
        """R3-14, hands-on run 3: the Mod ID field left blank, as it is by
        default, the export was "Run-Three-Ale", and read in again it was
        offered a second project, its own keeping it by no ModID at all."""
        from anno_modding_studio.core import projects

        places.projects_dir().mkdir(parents=True, exist_ok=True)
        made = []
        for game, name in ((ANNO_1800, "Run Three Ale"), (ANNO_117, "Harbour Walls")):
            path = places.projects_dir() / (name + projects.PROJECT_SUFFIX)
            projects.Project(path, part={"details": {"name": name}}, game=game).save()
            made.append(path)
        self.assertEqual(filecache.project_keeping("Run-Three-Ale", ANNO_1800), made[0])
        self.assertEqual(filecache.project_keeping("harbour-walls", ANNO_117), made[1])
        self.assertIsNone(filecache.project_keeping("Run-Three-Ale", ANNO_117))


class ReadAgainTest(_Folders):
    def reads(self):
        """How many loadout files one question reads."""
        with mock.patch.object(filecache, "read_loadout", wraps=filecache.read_loadout) as read:
            self.mentioned()
        return read.call_count

    def test_a_file_is_read_once_until_it_changes(self):
        self.write("Main", [("new", "New")])
        self.write("Other", [("other", "Other")])
        self.assertEqual(self.reads(), 2)
        self.assertEqual(self.reads(), 0)
        self.write("Other", [("other", "Other"), ("more", "More")])
        self.assertEqual(self.reads(), 1)
        self.assertEqual(self.mentioned(), {"new", "other", "more"})

    def test_a_change_of_size_alone_is_seen(self):
        path = self.write("Main", [("new", "New")])
        self.mentioned()
        stamp = path.stat().st_mtime_ns
        self.write("Main", [("newer", "Newer")])
        os.utime(path, ns=(stamp, stamp))
        self.assertEqual(self.mentioned(), {"newer"})

    def test_a_change_of_time_alone_is_seen(self):
        path = self.write("Main", [("aaa", "Aaa")])
        self.mentioned()
        stamp = path.stat().st_mtime_ns
        self.write("Main", [("bbb", "Bbb")])
        os.utime(path, ns=(stamp + 10**9, stamp + 10**9))
        self.assertEqual(self.mentioned(), {"bbb"})

    def test_a_deleted_loadout_is_forgotten(self):
        path = self.write("Main", [("new", "New")])
        self.write("Other", [("other", "Other")])
        self.mentioned()
        path.unlink()
        self.assertEqual(self.mentioned(), {"other"})
        with sqlite3.connect(str(self.database)) as con:
            self.assertEqual(con.execute("select count(*) from files").fetchone()[0], 1)
        con.close()

    def test_another_loadouts_folder_keeps_its_own_rows(self):
        """A scratch script's home shares the local folder: asking about one
        folder does not forget another's."""
        self.write("Main", [("new", "New")])
        elsewhere = self.loadouts.parent / "elsewhere"
        elsewhere.mkdir()
        Loadout(game=ANNO_117).save(elsewhere / "There.annoload")
        self.mentioned()
        filecache.mentioned_keys(ANNO_117, elsewhere)
        self.assertEqual(self.reads(), 0)


class BuiltAgainTest(_Folders):
    """The cache can be deleted at any time, and whatever is wrong with it,
    the answer is the files'."""

    def setUp(self):
        super().setUp()
        self.write("Main", [("new", "New")])
        self.write("Other", [("other", "Other")])
        self.expected = {"new", "other"}

    def schema(self):
        con = sqlite3.connect(str(self.database))
        try:
            return con.execute(
                "select value from meta where key = 'schema_version'").fetchone()[0]
        finally:
            con.close()

    def test_deleted_between_questions_it_is_built_again(self):
        self.assertEqual(self.mentioned(), self.expected)
        self.database.unlink()
        self.assertEqual(self.mentioned(), self.expected)
        self.assertTrue(self.database.is_file())

    def test_no_connection_is_held_between_questions(self):
        """Windows will not delete a database somebody has open."""
        self.mentioned()
        self.database.unlink()

    def test_a_file_that_is_not_a_database_is_built_again(self):
        self.database.parent.mkdir(parents=True)
        self.database.write_bytes(b"not a database at all " * 200)
        self.assertEqual(self.mentioned(), self.expected)
        self.assertEqual(self.schema(), str(filecache.SCHEMA_VERSION))

    def test_a_database_that_is_somebody_elses_is_built_again(self):
        self.database.parent.mkdir(parents=True)
        con = sqlite3.connect(str(self.database))
        con.execute("create table files (whatever)")
        con.commit()
        con.close()
        self.assertEqual(self.mentioned(), self.expected)
        self.assertEqual(self.schema(), str(filecache.SCHEMA_VERSION))

    def test_a_cache_from_an_older_build_is_built_again(self):
        self.mentioned()
        con = sqlite3.connect(str(self.database))
        con.execute("update meta set value = '0' where key = 'schema_version'")
        con.execute("delete from mentions")
        con.commit()
        con.close()
        self.assertEqual(self.mentioned(), self.expected)
        self.assertEqual(self.schema(), str(filecache.SCHEMA_VERSION))

    def test_a_cache_from_a_newer_build_is_left_alone(self):
        self.database.parent.mkdir(parents=True)
        con = sqlite3.connect(str(self.database))
        con.execute("create table meta (key text primary key, value text not null)")
        con.execute("insert into meta values ('schema_version', ?)",
                    (str(filecache.SCHEMA_VERSION + 1),))
        con.commit()
        con.close()
        before = self.database.read_bytes()
        self.assertEqual(self.mentioned(), self.expected)
        self.assertEqual(self.database.read_bytes(), before)

    def test_a_cache_another_studio_is_writing_is_answered_around(self):
        """Not deleted, and not waited on for long: a window is waiting."""
        self.mentioned()
        self.write("Third", [("third", "Third")])
        holder = sqlite3.connect(str(self.database))
        self.addCleanup(holder.close)
        holder.execute("begin exclusive")
        with mock.patch.object(filecache, "BUSY_SECONDS", 0.1):
            self.assertEqual(self.mentioned(), self.expected | {"third"})
        holder.rollback()
        self.assertEqual(self.schema(), str(filecache.SCHEMA_VERSION))
        self.assertEqual(self.mentioned(), self.expected | {"third"})

    def test_a_cache_that_cannot_be_made_is_answered_from_the_files(self):
        blocked = self.loadouts.parent / "a file"
        blocked.write_text("in the way", encoding="utf-8")
        with mock.patch.object(places, "files_cache", return_value=blocked / "files.sqlite"):
            self.assertEqual(self.mentioned(), self.expected)


if __name__ == "__main__":
    unittest.main()
