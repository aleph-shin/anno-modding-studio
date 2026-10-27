"""Which file keeps each part of a loadout is said by the part itself.

Each field of :class:`~anno_modding_studio.core.changes.Loadout` and
:class:`~anno_modding_studio.core.changes.ModLayer` declares which file keeps
it (``core/owners.py``), and saving and opening move each part to the file it
declares.  So a field added to either says where it goes, or this refuses it;
and one that says a project keeps it is kept by the project, and travels with
the mod into every loadout that has it.

And a project is read and written on its own, without a loadout
(:class:`~anno_modding_studio.core.projects.Project`).
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from dataclasses import fields
from pathlib import Path

import _isolation  # first: keeps the suite out of real files under any runner

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from anno_modding_studio.core import owners, projects  # noqa: E402
from anno_modding_studio.core.changes import (  # noqa: E402
    Loadout,
    ModLayer,
    name_in_file,
    own_record,
)

# Values a field only takes in a shape of its own, as opening reads them.
_SHAPED = {
    "game_setup": {"set": True},
    "options": {"set": {"type": "toggle", "default": "true"}},
    "published": {"version": "1.4", "build": 4, "when": 1.0, "modfile_id": 9, "checkpoint": 2.0},
}


def _other_than(value, name: str):
    """A value of ``value``'s kind that is not it, and says which field."""
    if name in _SHAPED:
        return _SHAPED[name]
    if isinstance(value, bool):
        return not value
    if isinstance(value, int):
        return value + 7
    if isinstance(value, str):
        return "%s, as set" % name
    if isinstance(value, dict):
        return {"set": name}
    if isinstance(value, list):
        return [name]
    raise AssertionError("no value to set %s to" % name)


class TestEachFieldSaysWhereItIsKept(unittest.TestCase):
    def test_every_field_of_both_declares_its_owner(self):
        self.assertEqual(owners.undeclared("loadout"), ())
        self.assertEqual(owners.undeclared("layer"), ())

    def test_what_is_never_saved_is_not_in_a_loadout_file(self):
        loadout = Loadout()
        loadout.edits, loadout.emptied = 3, {"Gone"}
        loadout.set_layer(ModLayer(origin="Theirs", written_whole=True, named_after_folder=True))
        written = loadout.to_dict()
        self.assertEqual([name for name in owners.names("loadout", owners.MEMORY) if name in written], [])
        (layer,) = written["layers"]
        self.assertEqual([name for name in owners.names("layer", owners.MEMORY) if name in layer], [])

    def test_the_project_moves_what_the_fields_say_it_keeps_by_mod(self):
        # A collection declared one mod's entry by entry, or kept inside
        # their ops by a mod of yours, is one :func:`projects.split` moves:
        # declaring another without teaching it to is refused here.
        by_mod = set(owners.names("loadout", owners.PER_MOD, owners.PER_MOD_AND_INSIDE))
        self.assertEqual(by_mod, set(projects.BY_ORIGIN) | set(projects.KEYED_BY_ORIGIN))
        kept = set(owners.names("loadout", owners.INSIDE, owners.PER_MOD_AND_INSIDE))
        # What a patch sets aside is no part of the file: it goes to the
        # project with the save, as :func:`projects.split`'s ``aside``.
        self.assertEqual(kept - {"kept_aside"}, set(projects.KEPT_INSIDE))

    def test_the_details_lists_are_the_schema_s(self):
        self.assertEqual(set(projects.layer_details()), {
            "name", "category", "version", "creator", "description", "mod_id",
            "declared", "load_after", "fork_of",
            "scripts_folder", "guid_range", "loads_last",
        })
        self.assertEqual(set(projects.release_facts()), {"modio_mod_id", "published"})


class TestEachGoesWhereItSays(unittest.TestCase):
    """Every field a project keeps, set to something of its own and saved:
    in the project's file and not the loadout's, and back as it was on
    opening."""

    def setUp(self):
        _isolation.forget_loadouts()
        self.addCleanup(_isolation.forget_loadouts)
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = Path(self._tmp.name) / "Main.annoload"

    def loadout(self):
        loadout = Loadout(own_mod=True)
        loadout.set_layer(ModLayer(origin="Theirs", name="Theirs", order=1))
        patch = ModLayer(origin="Patch", patches="Theirs", order=2)
        loadout.set_layer(patch)
        wanted = {}
        # What the loadout keeps of the mod it releases.
        for found in fields(Loadout):
            if owners.owner_of(found) in (owners.DETAIL[owners.OWNER], owners.RELEASE[owners.OWNER]):
                value = _other_than(getattr(loadout, found.name), found.name)
                setattr(loadout, found.name, value)
                wanted[("", found.name)] = value
        # And each mod's record, the own mod's as a patch's.
        for origin, record in (("", loadout.own), ("Patch", patch)):
            for found in fields(ModLayer):
                if owners.owner_of(found) == owners.DETAIL[owners.OWNER]:
                    value = _other_than(getattr(record, found.name), found.name)
                    setattr(record, found.name, value)
                    wanted[(origin, found.name)] = value
        return loadout, wanted

    @staticmethod
    def holder(loadout, origin: str, name: str):
        if name in projects.release_facts():
            return loadout
        return loadout.mod(origin)

    def test_in_the_project_and_not_the_loadout(self):
        loadout, wanted = self.loadout()
        loadout.save(self.path)
        written = json.loads(self.path.read_text(encoding="utf-8"))
        (layer,) = [raw for raw in written["layers"] if raw["origin"] == "Patch"]
        for (origin, name), value in wanted.items():
            with self.subTest(origin=origin, field=name):
                if name in projects.release_facts():
                    held = written
                else:
                    held = written["own"] if not origin else layer
                self.assertNotIn(name, held)
                project = projects.Project.read(projects.where(loadout.projects[origin].path))
                kept = project.releases if name in projects.release_facts() else project.details
                self.assertEqual(kept[name], value)

    def test_and_back_as_it_was_on_opening(self):
        loadout, wanted = self.loadout()
        loadout.save(self.path)
        loaded = Loadout.load(self.path)
        for (origin, name), value in wanted.items():
            with self.subTest(origin=origin, field=name):
                self.assertEqual(getattr(self.holder(loaded, origin, name), name), value)


class TestAProjectOnItsOwn(unittest.TestCase):
    def setUp(self):
        _isolation.forget_loadouts()
        self.addCleanup(_isolation.forget_loadouts)
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        loadout = Loadout(own=own_record(name="My Mod", mod_id="me-my-mod"), own_mod=True, game="anno117")
        loadout.builds[""] = {"build": 4}
        self.loadout_path = Path(self._tmp.name) / "Main.annoload"
        loadout.save(self.loadout_path)
        self.path = projects.where(loadout.projects[""].path)

    def test_it_reads_what_its_file_holds(self):
        project = projects.Project.read(self.path)
        self.assertEqual((project.mod_id, project.game, project.revision, project.build),
                         ("me-my-mod", "anno117", 1, 4))
        self.assertEqual(project.name(""), "My Mod")
        self.assertIsNotNone(project.created_at)

    def test_a_save_writes_its_next_revision_and_keeps_the_rest(self):
        raw = json.loads(self.path.read_text(encoding="utf-8"))
        raw["a newer Studio's"] = {"kept": True}
        self.path.write_text(json.dumps(raw), encoding="utf-8")
        project = projects.Project.read(self.path)
        created = project.created_at
        project.part["details"] = {**project.details, "version": "2.0"}
        self.assertEqual(project.save(), 2)
        again = projects.Project.read(self.path)
        self.assertEqual((again.revision, again.details["version"], again.created_at), (2, "2.0", created))
        self.assertEqual(json.loads(self.path.read_text(encoding="utf-8"))["a newer Studio's"], {"kept": True})

    def test_one_saved_since_it_was_read_is_not_written_over(self):
        first, second = projects.Project.read(self.path), projects.Project.read(self.path)
        first.save()
        with self.assertRaises(projects.ProjectError):
            second.save()
        self.assertEqual(projects.Project.read(self.path).revision, 2)

    def test_a_rename_goes_into_it_through_it(self):
        move = name_in_file(self.loadout_path, "", "Renamed")
        self.assertEqual(move.renamed, (1, 2))
        self.assertEqual(projects.Project.read(projects.where(move.after)).details["name"], "Renamed")
        self.assertEqual(Loadout.load(self.loadout_path).held(""), "")


if __name__ == "__main__":
    unittest.main()
