"""What the Studio keeps survives going forward and back a version.

From 0.1.0 on, people go back and forth between versions: an update that
misbehaves, two installs, a downgrade.  Before this, a loadout an older build
opened lost every field it didn't know at the next autosave; a library record a
newer build wrote in another shape was read as empty, and the next install
wrote the emptiness back; undoing a conversion took a step of an unknown kind
for a folder moved; and a backup a newer build made could be pruned by an
older one that misjudged which save held the first copy of a file.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import _isolation  # first: keeps the suite out of real files under any runner

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from anno_modding_studio import __version__  # noqa: E402
from anno_modding_studio.core import (  # noqa: E402
    backup,
    changes,
    convert,
    gamefiles,
    gameversion,
    histories,
    library,
    modzip,
    patching,
    projects,
    records,
    zipped,
)
from anno_modding_studio.core.changes import (  # noqa: E402
    Change,
    Loadout,
    ModLayer,
    Op,
    Unread,
    own_record,
)

PATCH = "data/config/export/main/asset/assets.xml"


class TempCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)

    def json_of(self, path):
        return json.loads(Path(path).read_text(encoding="utf-8"))

    def put_json(self, path, data):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text(json.dumps(data), encoding="utf-8")


# -- loadouts --------------------------------------------------------------------------
def _a_loadout() -> Loadout:
    loadout = Loadout(own=own_record(name="Mine"), own_mod=True)
    loadout.set_layer(ModLayer(origin="Theirs", name="Theirs", folder="C:/mods/Theirs"))
    loadout.set_op(Op(expression="//Values/Trader", origin="Theirs", source="<ModOp/>"))
    loadout.unread.append(Unread(origin="Theirs", source="<ModOp/>", label="odd"))
    loadout.set_change(Change(guid="1", asset_label="A", path="/Values/X", tag="X",
                              old_value="1", new_value="2"))
    loadout.checkpoint("by hand")
    histories.take(loadout, "by hand")
    loadout.add_profile("Second")
    loadout.builds[""] = {"build": 1, "digest": "d", "assets": "a", "update": "u"}
    loadout.published = {"version": "1.0", "build": 1, "when": 1.0, "modfile_id": 2, "checkpoint": 0.0}
    loadout.taken_out = [["Theirs", "<ModOp/>", [], 3]]
    return loadout


class LoadoutsKeepWhatTheyDontKnow(TempCase):
    """A newer Studio's fields, at the top of the file and in each entry,
    are written back as they were by a build that doesn't know them."""

    def newer_file(self) -> Path:
        path = self.tmp / "p.annoload"
        _a_loadout().save(path)
        data = self.json_of(path)
        data["written_by"] = __version__  # the same version: no copy kept
        data["from_the_future"] = {"kept": [1, 2]}
        data["changes"][0]["tomorrow"] = "yes"
        data["layers"][0]["tomorrow"] = 1
        data["ops"][0]["tomorrow"] = True
        data["unread"][0]["tomorrow"] = "u"
        data["history"][0]["tomorrow"] = "h"
        data["history"][0]["mods"][0]["tomorrow"] = "hm"
        data["profiles"][1]["tomorrow"] = "p"
        data["taken_out"][0].append({"tomorrow": "t"})
        self.put_json(path, data)
        # The loadout's own mod is kept in its project, and so is what a
        # newer Studio wrote about it.
        project = self.project_of(path)
        kept = self.json_of(project)
        kept["from_the_future"] = "proj"
        kept["details"]["tomorrow"] = "d"
        kept["build"]["tomorrow"] = "b"
        kept["releases"]["published"]["tomorrow"] = "pub"
        kept["releases"]["tomorrow"] = "r"
        kept["history"][0]["tomorrow"] = "ph"
        kept["history"][0]["changes"][0]["tomorrow"] = "phc"
        self.put_json(project, kept)
        return path

    def project_of(self, path) -> Path:
        return projects.where(self.json_of(path)["projects"][0]["path"])

    def test_an_autosave_writes_back_every_field_it_does_not_know(self):
        path = self.newer_file()
        Loadout.load(path).save(path)
        data = self.json_of(path)
        self.assertEqual(data["from_the_future"], {"kept": [1, 2]})
        self.assertEqual(data["changes"][0]["tomorrow"], "yes")
        self.assertEqual(data["layers"][0]["tomorrow"], 1)
        self.assertIs(data["ops"][0]["tomorrow"], True)
        self.assertEqual(data["unread"][0]["tomorrow"], "u")
        self.assertEqual(data["history"][0]["tomorrow"], "h")
        self.assertEqual(data["history"][0]["mods"][0]["tomorrow"], "hm")
        self.assertEqual(data["profiles"][1]["tomorrow"], "p")
        self.assertEqual(data["taken_out"][0], ["Theirs", "<ModOp/>", [], 3, {"tomorrow": "t"}])

    def test_a_project_written_again_keeps_what_it_did_not_know(self):
        path = self.newer_file()
        loadout = Loadout.load(path)
        loadout.own.description = "Moved, so the project is written."
        loadout.save(path)
        kept = self.json_of(self.project_of(path))
        self.assertEqual(kept["revision"], 2)
        self.assertEqual(kept["details"]["description"], "Moved, so the project is written.")
        self.assertEqual(kept["from_the_future"], "proj")
        self.assertEqual(kept["details"]["tomorrow"], "d")
        self.assertEqual(kept["build"]["tomorrow"], "b")
        self.assertEqual(kept["releases"]["published"]["tomorrow"], "pub")
        self.assertEqual(kept["releases"]["tomorrow"], "r")
        self.assertEqual(kept["history"][0]["tomorrow"], "ph")
        self.assertEqual(kept["history"][0]["changes"][0]["tomorrow"], "phc")

    def patched_file(self) -> Path:
        """A loadout whose patch keeps an op of Theirs switched off, with
        a newer Studio's keys on the reference to the patch's project, in
        that project's ``inside`` and on its ``off`` entry, and in the
        ``inside`` of a checkpoint of the own mod's."""
        loadout = _a_loadout()
        patch = patching.make(loadout, "Theirs")
        loadout.ops[0].active = False
        loadout.ops[0].kept_by = patch.origin
        path = self.tmp / "p.annoload"
        loadout.save(path)
        data = self.json_of(path)
        data["written_by"] = __version__
        ref = next(raw for raw in data["projects"] if raw["origin"] == patch.origin)
        ref["tomorrow"] = "ref"
        self.put_json(path, data)
        project = projects.where(ref["path"])
        kept = self.json_of(project)
        self.assertEqual(len(kept["inside"]["off"]), 1)
        kept["inside"]["tomorrow"] = ["i"]
        kept["inside"]["off"][0]["tomorrow"] = "off"
        self.put_json(project, kept)
        own = self.project_of(path)
        kept = self.json_of(own)
        kept["history"][0]["inside"] = {"tomorrow": {"x": 1}, "later": ["l"]}
        self.put_json(own, kept)
        return path

    def test_a_reference_to_a_project_keeps_what_it_did_not_know(self):
        path = self.patched_file()
        Loadout.load(path).save(path)
        refs = self.json_of(path)["projects"]
        self.assertEqual([raw.get("tomorrow") for raw in refs], [None, "ref"])

    def test_what_a_patch_keeps_inside_their_ops_keeps_what_it_did_not_know(self):
        path = self.patched_file()
        loadout = Loadout.load(path)
        self.assertFalse(loadout.ops[0].active)
        loadout.layer("Theirs (my patch)").description = "Moved, so the project is written."
        loadout.own.description = "And this one."
        loadout.save(path)
        refs = self.json_of(path)["projects"]
        patch = self.json_of(projects.where(refs[1]["path"]))
        self.assertEqual(patch["revision"], 2)
        self.assertEqual(patch["inside"]["tomorrow"], ["i"])
        self.assertEqual(patch["inside"]["off"], [{"origin": "Theirs", "uid": loadout.ops[0].uid,
                                                   "source": "<ModOp/>", "tomorrow": "off"}])
        own = self.json_of(projects.where(refs[0]["path"]))
        self.assertEqual(own["revision"], 2)
        self.assertEqual(own["history"][0]["inside"], {"tomorrow": {"x": 1}, "later": ["l"]})

    def test_and_so_does_what_it_keeps_inside_a_mod_unloaded_here(self):
        path = self.patched_file()
        loadout = Loadout.load(path)
        loadout.drop_layer("Theirs")
        loadout.layer("Theirs (my patch)").description = "Moved, so the project is written."
        loadout.save(path)
        patch = self.json_of(projects.where(self.json_of(path)["projects"][1]["path"]))
        self.assertEqual(patch["inside"]["tomorrow"], ["i"])
        self.assertEqual([entry.get("tomorrow") for entry in patch["inside"]["off"]], ["off"])

    def test_an_op_switched_off_keeps_it_when_found_by_its_text(self):
        # Another loadout's reading gave their op another uid.
        off = projects._with_off_kept(
            [{"origin": "Theirs", "uid": "new", "source": "<ModOp/>"}],
            [[{"origin": "Theirs", "uid": "old", "source": "<ModOp/>"}, {"tomorrow": "off"}]])
        self.assertEqual(off, [{"origin": "Theirs", "uid": "new", "source": "<ModOp/>", "tomorrow": "off"}])

    def test_a_change_edited_here_keeps_what_it_did_not_know(self):
        path = self.newer_file()
        loadout = Loadout.load(path)
        change = loadout.authored()[0]
        from dataclasses import replace

        loadout.set_change(replace(change, new_value="3"))
        loadout.save(path)
        saved = self.json_of(path)["changes"][0]
        self.assertEqual((saved["new_value"], saved["tomorrow"]), ("3", "yes"))

    def test_no_name_is_kept_back_for_a_file_of_before_the_release(self):
        # Names test builds wrote are a later build's to use: read as old
        # data, a layer's "rules" became ops, and every one was dropped.
        path = self.tmp / "p.annoload"
        _a_loadout().save(path)
        data = self.json_of(path)
        data["written_by"] = __version__
        later = {"rules": ["r"], "exclusive": True, "own_stale": 1, "mod_name": "n",
                 "options": {"o": 1}, "scripts": "s", "version": "v"}
        data.update(later)
        data["layers"][0]["rules"] = {"//Trader": ["1"]}
        data["changes"][0].update({"rule": "//Trader", "rule_uid": "u"})
        data["ops"][0]["held"] = "h"
        self.put_json(path, data)
        loadout = Loadout.load(path)
        self.assertEqual(len(loadout.ops), 1)
        loadout.save(path)
        data = self.json_of(path)
        self.assertEqual({key: data[key] for key in later}, later)
        self.assertEqual(data["layers"][0]["rules"], {"//Trader": ["1"]})
        self.assertEqual((data["changes"][0]["rule"], data["changes"][0]["rule_uid"]), ("//Trader", "u"))
        self.assertEqual(data["ops"][0]["held"], "h")


class FilesOfBeforeTheReleaseAreRefused(TempCase):
    """A loadout or project that says no format, or one older than 0.1.0's,
    is refused with a sentence, never read by shape."""

    def test_a_loadout_without_a_format_or_of_a_test_build_s_is_refused(self):
        path = self.tmp / "old.annoload"
        for data, says in (({"changes": []}, "does not say which format"),
                           ({"format": changes.OLDEST_LOADOUT_FORMAT - 1, "mod_name": "Old"},
                            "before its first release"),
                           ({"format": "6"}, "does not say which format")):
            with self.subTest(data=data):
                self.put_json(path, data)
                with self.assertRaises(changes.LoadoutError) as raised:
                    Loadout.load(path)
                self.assertIn(says, str(raised.exception))

    def test_a_project_without_a_format_or_of_a_test_build_s_is_refused(self):
        path = self.tmp / "old.annoproj"
        for data, says in (({"kind": "project", "details": {}}, "does not say which format"),
                           ({"kind": "project", "format": projects.OLDEST_PROJECT_FORMAT - 1},
                            "before its first release")):
            with self.subTest(data=data):
                self.put_json(path, data)
                with self.assertRaises(projects.ProjectError) as raised:
                    projects.read(path)
                self.assertIn(says, str(raised.exception))


class LoadoutsKeepACopyBeforeAnotherVersionWritesOverThem(TempCase):
    def written_by(self, version, path=None) -> Path:
        path = path or self.tmp / "p.annoload"
        _a_loadout().save(path)
        data = self.json_of(path)
        if version is None:
            data.pop("written_by")
        else:
            data["written_by"] = version
        self.put_json(path, data)
        return path

    def test_the_first_save_over_an_older_version_keeps_its_file_once(self):
        path = self.written_by("0.0.9")
        before = path.read_bytes()
        loadout = Loadout.load(path)
        loadout.save(path)
        copy = path.with_name(path.name + ".0.0.9.bak")
        self.assertEqual(copy.read_bytes(), before)
        loadout.own.name = "Again"
        loadout.save(path)
        self.assertEqual(copy.read_bytes(), before)
        self.assertEqual(sorted(p.name for p in self.tmp.iterdir()), sorted([copy.name, path.name]))

    def test_a_newer_version_s_file_is_kept_too(self):
        path = self.written_by("9.0.0")
        Loadout.load(path).save(path)
        self.assertTrue(path.with_name(path.name + ".9.0.0.bak").is_file())

    def test_a_file_saying_no_version_is_kept_by_its_format(self):
        path = self.written_by(None)
        Loadout.load(path).save(path)
        self.assertTrue(path.with_name(path.name + ".format%d.bak" % changes.LOADOUT_FORMAT).is_file())

    def test_a_file_this_version_wrote_is_not_copied(self):
        path = self.tmp / "p.annoload"
        _a_loadout().save(path)
        Loadout.load(path).save(path)
        self.assertEqual([p.name for p in self.tmp.iterdir()], [path.name])

    def test_a_save_over_a_file_it_did_not_read_looks_at_it_first(self):
        path = self.written_by("0.0.9", self.tmp / "other.annoload")
        changes._ON_DISK.clear()
        _a_loadout().save(path)
        self.assertTrue(path.with_name(path.name + ".0.0.9.bak").is_file())


class ProjectsKeepACopyBeforeAnotherVersionWritesOverThem(TempCase):
    """A mod of yours lives in its project, so its file is kept as a
    loadout's is, by each of the three ways a project is written."""

    def setUp(self):
        super().setUp()
        # A copy left by another test would stand for this one's.
        _isolation.forget_loadouts()

    def written_by(self, version):
        loadout_path = self.tmp / "p.annoload"
        _a_loadout().save(loadout_path)
        project = projects.where(self.json_of(loadout_path)["projects"][0]["path"])
        data = self.json_of(project)
        data["written_by"] = version
        self.put_json(project, data)
        return loadout_path, project

    def copies(self, project):
        return sorted(path.name for path in project.parent.glob(project.name + ".*.bak"))

    def test_a_save_with_the_loadout_keeps_it_once(self):
        loadout_path, project = self.written_by("0.0.9")
        before = project.read_bytes()
        loadout = Loadout.load(loadout_path)
        loadout.own.description = "Moved, so the project is written."
        loadout.save(loadout_path)
        copy = project.with_name(project.name + ".0.0.9.bak")
        self.assertEqual(copy.read_bytes(), before)
        loadout.own.description = "Again."
        loadout.save(loadout_path)
        self.assertEqual(copy.read_bytes(), before)
        self.assertEqual(self.copies(project), [copy.name])
        self.assertEqual(self.json_of(project)["written_by"], __version__)

    def test_a_newer_version_s_is_kept_too(self):
        loadout_path, project = self.written_by("9.0.0")
        loadout = Loadout.load(loadout_path)
        loadout.own.description = "Moved, so the project is written."
        loadout.save(loadout_path)
        self.assertEqual(self.copies(project), [project.name + ".9.0.0.bak"])

    def test_a_project_saved_on_its_own_keeps_it(self):
        _loadout_path, project = self.written_by("0.0.9")
        before = project.read_bytes()
        held = projects.Project.read(project)
        held.part["details"]["description"] = "On its own."
        held.save()
        self.assertEqual(project.with_name(project.name + ".0.0.9.bak").read_bytes(), before)

    def test_a_rename_written_into_it_keeps_it(self):
        loadout_path, project = self.written_by("0.0.9")
        before = project.read_bytes()
        move = changes.name_in_file(loadout_path, "", "Renamed")
        self.assertIsNotNone(move)
        self.assertFalse(project.exists())
        self.assertEqual(project.with_name(project.name + ".0.0.9.bak").read_bytes(), before)

    def test_one_this_version_wrote_is_not_copied(self):
        loadout_path, project = self.written_by(__version__)
        loadout = Loadout.load(loadout_path)
        loadout.own.description = "Moved, so the project is written."
        loadout.save(loadout_path)
        self.assertEqual(self.copies(project), [])


# -- the library's records ----------------------------------------------------------------
class LibraryRecords(TempCase):
    def setUp(self):
        super().setUp()
        self.lib = self.tmp / "library"
        self.lib.mkdir()

    def test_each_record_is_stamped(self):
        library.record(self.lib, "Mod", source=library.ZIP)
        library.write_links(self.lib, {"Mod": {"target": "x"}})
        library.set_live(self.lib, "p.annoload")
        for name in (library.REGISTRY, library.LINKS, library.LIVE):
            data = self.json_of(self.lib / name)
            self.assertEqual((data["format"], data["written_by"]), (library.RECORD_FORMAT, __version__))
        self.assertEqual(self.json_of(self.lib / library.REGISTRY)["entries"],
                         {"Mod": {"source": library.ZIP}})

    def test_a_record_from_before_stamps_is_read_and_kept_beside_on_its_upgrade(self):
        self.put_json(self.lib / library.REGISTRY, {"Old": {"source": library.ADOPTED}})
        self.assertEqual(library.registry(self.lib), {"Old": {"source": library.ADOPTED}})
        library.record(self.lib, "New", source=library.ZIP)
        self.assertEqual(set(library.registry(self.lib)), {"Old", "New"})
        self.assertEqual(self.json_of(self.lib / (library.REGISTRY + ".format0.bak")),
                         {"Old": {"source": library.ADOPTED}})

    def test_a_newer_format_is_read_and_never_written_over(self):
        newer = {"format": library.RECORD_FORMAT + 1, "written_by": "9.0.0",
                 "entries": {"Old": {"source": library.ADOPTED}}}
        self.put_json(self.lib / library.REGISTRY, newer)
        self.assertEqual(library.registry(self.lib), {"Old": {"source": library.ADOPTED}})
        with self.assertRaises(records.RecordError) as raised:
            library.record(self.lib, "New", source=library.ZIP)
        self.assertIsInstance(raised.exception, OSError)
        self.assertIn("9.0.0", str(raised.exception))
        self.assertEqual(self.json_of(self.lib / library.REGISTRY), newer)

    def test_a_damaged_record_is_not_emptied(self):
        (self.lib / library.LINKS).write_text("{ not json", encoding="utf-8")
        with self.assertRaises(records.RecordError):
            library.write_links(self.lib, {})
        self.assertEqual((self.lib / library.LINKS).read_text(encoding="utf-8"), "{ not json")

    def test_an_entry_of_another_shape_is_not_dropped_by_a_write(self):
        self.put_json(self.lib / library.REGISTRY, {"Odd": ["a", "list"], "Fine": {"source": "zip"}})
        self.assertEqual(library.registry(self.lib), {"Fine": {"source": "zip"}})
        with self.assertRaises(records.RecordError):
            library.forget(self.lib, "Fine")
        self.assertIn("Odd", self.json_of(self.lib / library.REGISTRY))

    def test_what_a_newer_studio_put_beside_the_entries_is_written_back(self):
        self.put_json(self.lib / library.REGISTRY, {
            "format": library.RECORD_FORMAT, "written_by": "0.1.5", "entries": {}, "notes": "kept",
        })
        library.record(self.lib, "New", source=library.ZIP)
        self.assertEqual(self.json_of(self.lib / library.REGISTRY)["notes"], "kept")

    def test_nothing_is_moved_in_while_a_record_could_not_say_so(self):
        mods = self.tmp / "mods"
        folder = mods / "Mine"
        (folder / "data").mkdir(parents=True)
        (self.lib / library.REGISTRY).write_text("damaged", encoding="utf-8")
        with self.assertRaises(records.RecordError):
            library.adopt(self.lib, folder)
        self.assertTrue((folder / "data").is_dir())
        self.assertFalse((self.lib / "Mine").exists())

    def test_the_live_record_of_a_newer_studio_is_left_as_it_is(self):
        newer = {"format": library.RECORD_FORMAT + 1, "loadout": "theirs"}
        self.put_json(self.lib / library.LIVE, newer)
        with self.assertRaises(OSError):
            library.set_live(self.lib, "mine")
        self.assertEqual(self.json_of(self.lib / library.LIVE), newer)

    def test_game_updates_learnt_are_not_written_over_when_damaged(self):
        path = self.tmp / gameversion.REMEMBERED_NAME
        path.write_text("{ damaged", encoding="utf-8")
        with mock.patch.object(gameversion, "_remembered_path", return_value=path):
            gameversion.remember("f" * 40, gameversion.Build("", "1.0.0.1", "1.0.0", "", "", ""))
        self.assertEqual(path.read_text(encoding="utf-8"), "{ damaged")


class ConversionUndo(TempCase):
    def journal(self, steps, format_=convert.JOURNAL_FORMAT) -> Path:
        path = self.tmp / "library" / "conversions" / "c.json"
        self.put_json(path, {"format": format_, "written_by": "x", "journal": {"steps": steps}})
        return path

    def test_a_step_of_an_unknown_kind_is_refused_and_nothing_moves(self):
        moved = self.tmp / "library" / "Moved"
        moved.mkdir(parents=True)
        path = self.journal([
            {"op": "move", "from": str(self.tmp / "mods" / "Moved"), "to": str(moved)},
            {"op": "zip", "from": str(self.tmp / "mods" / "Z"), "to": str(self.tmp / "z.zip")},
        ])
        with self.assertRaises(records.RecordError) as raised:
            convert.undo(path, self.tmp / "library")
        self.assertIn("zip", str(raised.exception))
        self.assertTrue(moved.is_dir())
        self.assertNotIn("undone", self.json_of(path))

    def test_a_step_missing_where_it_went_is_refused_before_anything(self):
        path = self.journal([{"op": "move", "from": "C:/somewhere"}])
        self.assertTrue(convert.cannot_undo(convert.read_record(path)))

    def test_a_newer_journal_is_found_and_refused(self):
        path = self.journal([], format_=convert.JOURNAL_FORMAT + 1)
        self.assertEqual(convert.last_undoable(self.tmp / "library"), path)
        self.assertTrue(convert.cannot_undo(convert.read_record(path)))

    def test_a_snapshot_is_stamped(self):
        path = convert.save_snapshot({"when": 1.0, "game": "anno1800"}, self.tmp / "library")
        self.assertEqual(self.json_of(path)["format"], convert.JOURNAL_FORMAT)


# -- backups ---------------------------------------------------------------------------------
class Backups(TempCase):
    def setUp(self):
        super().setUp()
        self.mods = self.tmp / "mods"
        self.store = self.tmp / "backups"
        patch = self.mods / "Mod" / PATCH
        patch.parent.mkdir(parents=True)
        patch.write_text("<ModOps/>", encoding="utf-8")

    def take(self, **how):
        kept = backup.take(self.mods, ["Mod"], into=self.store, **how)
        self.assertIsNotNone(kept)
        return kept

    def test_the_manifest_says_which_studio_wrote_it(self):
        kept = self.take()
        self.assertEqual(self.json_of(kept.root / backup.MANIFEST)["written_by"], __version__)
        self.assertEqual(backup.read(kept.root).written_by, __version__)

    def test_a_newer_format_save_is_neither_pruned_nor_restored(self):
        first = self.take()
        manifest = self.json_of(first.root / backup.MANIFEST)
        manifest["format"] = backup.FORMAT + 1
        self.put_json(first.root / backup.MANIFEST, manifest)
        for _ in range(4):
            self.take()
        backup.prune(self.mods, into=self.store, keep=1)
        self.assertTrue(first.root.is_dir())
        kept = backup.read(first.root)
        self.assertTrue(kept.refusal)
        self.assertEqual(kept.files, [])
        self.assertEqual(backup.restore(kept), (0, [kept.refusal]))

    def test_a_save_of_another_shape_is_kept_and_does_not_stop_pruning(self):
        odd = self.take()
        manifest = self.json_of(odd.root / backup.MANIFEST)
        manifest["files"] = ["not", "entries"]
        self.put_json(odd.root / backup.MANIFEST, manifest)
        for _ in range(3):
            self.take()
        backup.prune(self.mods, into=self.store, keep=1)
        self.assertTrue(odd.root.is_dir())

    def test_the_first_copy_of_each_of_two_files_of_one_name_is_kept(self):
        one, other = self.tmp / "a" / "profile.txt", self.tmp / "b" / "profile.txt"
        for path in (one, other):
            path.parent.mkdir()
            path.write_text(path.parent.name, encoding="utf-8")
        first = backup.take(self.mods, [], into=self.store, extra=[one])
        with mock.patch("time.time", return_value=first.when + 10):
            second = backup.take(self.mods, [], into=self.store, extra=[other])
        for number in range(3):
            with mock.patch("time.time", return_value=first.when + 20 + number):
                backup.take(self.mods, [], into=self.store, extra=[one])
        backup.prune(self.mods, into=self.store, keep=0)
        self.assertTrue(first.root.is_dir())
        self.assertTrue(second.root.is_dir())


# -- caches made under other code ----------------------------------------------------------
class CachesOfOtherCode(TempCase):
    def test_a_zip_mirror_unpacked_under_another_unpacker_is_unpacked_again(self):
        import zipfile

        root = self.tmp / "mods"
        root.mkdir()
        archive = root / "Zipped.zip"
        with zipfile.ZipFile(archive, "w") as handle:
            handle.writestr("Zipped/modinfo.json", "{}")
        with mock.patch.dict(os.environ, {"ANNO_STUDIO_CACHE": str(self.tmp / "cache")}):
            mirror = zipped.sync("anno1800", root)
            (mirror / archive.name / "Zipped" / "left.txt").write_text("old", encoding="utf-8")
            zipped.sync("anno1800", root)
            self.assertTrue((mirror / archive.name / "Zipped" / "left.txt").is_file())
            with mock.patch.object(modzip, "UNPACK_VERSION", modzip.UNPACK_VERSION + 1):
                zipped.sync("anno1800", root)
            self.assertFalse((mirror / archive.name / "Zipped" / "left.txt").exists())

    def test_files_another_version_extracted_are_read_as_out_of_date(self):
        assets = self.tmp / "assets.xml"
        assets.write_text("<x/>", encoding="utf-8")
        stamp = {"version": gamefiles.EXTRACTED_VERSION + 1, "assets": str(assets),
                 "game_folder": str(self.tmp), "archives": []}
        self.assertEqual(gamefiles.read_state(stamp)[0], gamefiles.READ_PATCHED)


if __name__ == "__main__":
    unittest.main()
