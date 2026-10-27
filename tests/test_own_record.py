"""The loadout's own mod has a record, as a loaded mod has.

:attr:`~anno_modding_studio.core.changes.Loadout.own` is a
:class:`~anno_modding_studio.core.changes.ModLayer` with origin "": the own
mod's name, version, ModID and the rest, what it loads after and why its
reading is in doubt.  The names those had as the loadout's own fields read
and set the record, and a loadout file keeps them as it did
(docs/proposals/own-mod-layer-proposal.md, phase 1).
"""

from __future__ import annotations

import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path

import _isolation  # first: keeps the suite out of real files under any runner

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from anno_modding_studio.core import (  # noqa: E402
    build,
    generator,
    held,
    inside,
    modinfo,
    projects,
    scripts,
    settings,
)
from anno_modding_studio.core.changes import (  # noqa: E402
    OWN,
    Loadout,
    ModLayer,
    is_own_mod,
    own_record,
)


class TestTheRecord(unittest.TestCase):
    def test_a_new_loadout_has_one_as_a_new_mod_starts(self):
        own = Loadout().own
        self.assertEqual(own.origin, "")
        self.assertEqual((own.name, own.category, own.version), ("My Anno Mod", "Gameplay", "1.0"))
        self.assertEqual(own, own_record())

    def test_the_names_it_had_on_the_loadout_are_gone(self):
        # Phase 5: the record is the one place they are.
        for name in ("mod_name", "creator", "description", "category", "version", "mod_id",
                     "own_mod_loads_last", "guid_range", "scripts_folder", "own_stale"):
            with self.subTest(name=name):
                self.assertFalse(hasattr(Loadout(), name))

    def test_each_loadout_has_its_own(self):
        first, second = Loadout(), Loadout()
        first.own.name = "Mine"
        self.assertEqual(second.own.name, "My Anno Mod")

    def test_a_copy_has_its_own_details(self):
        loadout = Loadout(own=own_record(name="Mine"))
        twin = copy.copy(loadout)
        twin.own.name = "The view's"
        self.assertEqual(loadout.own.name, "Mine")
        view = inside.only_kept_by(loadout, "Theirs", "Patch")
        view.own.name = "Another view's"
        self.assertEqual(loadout.own.name, "Mine")

    def test_its_layer_is_a_copy_with_where_it_loads_worked_out(self):
        loadout = Loadout(own=own_record(name="", loads_last=True), own_mod=True)
        loadout.set_layer(ModLayer(origin="Theirs", name="Theirs", mod_id="theirs", order=1))
        layer = loadout.own_layer()
        self.assertEqual(layer.name, "My Anno Mod")
        self.assertEqual(layer.order, 2)
        self.assertEqual(layer.load_after, loadout.own_load_after())
        self.assertTrue(layer.load_after)
        layer.stale.append(["updated", "Theirs"])
        self.assertEqual(loadout.own.stale, [])
        self.assertEqual(loadout.own.load_after, [])


_OPTIONS = {"roomier": {"type": "toggle", "default": "false"}}


class TestItsModinfoBlocks(unittest.TestCase):
    """Its scripts, options, difficulty and game setup are blocks of its
    record's ``declared``, as a loaded mod's are of its modinfo, and are
    read and set the same way (phase 3)."""

    def setUp(self):
        self.loadout = Loadout(game="anno117", own=own_record(name="Mine"))

    def test_they_are_in_declared_under_the_modinfo_s_keys(self):
        self.loadout.scripts = {"Modules": [{"Path": "data/lua/mine.lua"}]}
        self.loadout.options = _OPTIONS
        self.loadout.difficulty = "harder"
        self.loadout.game_setup = {"Multiplayer": False}
        self.assertEqual(self.loadout.own.declared, {
            "Scripts": {"Modules": [{"Path": "data/lua/mine.lua"}]},
            "Options": _OPTIONS,
            "Difficulty": "harder",
            "GameSetup": {"Multiplayer": False},
        })

    def test_and_not_there_while_empty(self):
        self.loadout.options = _OPTIONS
        self.loadout.options = {}
        self.loadout.difficulty = ""
        self.assertEqual(self.loadout.own.declared, {})
        self.assertEqual((self.loadout.scripts, self.loadout.difficulty, self.loadout.game_setup), ({}, "", {}))

    def test_the_modules_read_and_set_them_as_a_loaded_mod_s(self):
        settings.set_block(self.loadout, "", _OPTIONS)
        self.assertEqual(self.loadout.own.declared["Options"], _OPTIONS)
        self.assertEqual(settings.block_of(self.loadout, ""), _OPTIONS)
        scripts.set_block(self.loadout, "", {"Modules": []})
        self.assertEqual(scripts.block_of(self.loadout, ""), {"Modules": []})
        scripts.set_block(self.loadout, "", {})
        self.assertNotIn("Scripts", self.loadout.own.declared)

    def test_what_it_offers_follows_its_options(self):
        self.loadout.options = _OPTIONS
        self.assertIn("roomier", self.loadout.own.offered)
        settings.set_block(self.loadout, "", {})
        self.assertEqual(self.loadout.own.offered, {})

    def test_a_mod_made_here_starts_safe_to_remove_and_unchanged(self):
        self.assertEqual(modinfo.setup_of(self.loadout, ""), ("unchanged", modinfo.NEW_MOD_SETUP))
        self.loadout.set_layer(ModLayer(origin="Theirs", declared={"ModID": "theirs"}))
        self.assertEqual(modinfo.setup_of(self.loadout, "Theirs"), ("", modinfo.SETUP_DEFAULTS))

    def test_a_copy_s_blocks_are_its_own(self):
        self.loadout.options = _OPTIONS
        twin = copy.copy(self.loadout)
        twin.options = {}
        self.assertEqual(self.loadout.options, _OPTIONS)

    def test_its_layer_has_no_modinfo_as_written(self):
        # An export takes a layer with a modinfo for an author's own, and
        # writes over it; the own mod's is the Studio's to write.
        self.loadout.options = _OPTIONS
        layer = self.loadout.own_layer()
        self.assertEqual(layer.declared, {})
        self.assertIn("roomier", layer.offered)


_THEIRS = {"ModID": "theirs", "ModName": {"English": "Theirs"}, "Anno": 8,
           "Scripts": {"Modules": ["data/lua/theirs.lua"]}}


class TestTheViewOfOneMod(unittest.TestCase):
    """An export's view of one mod is a loadout whose own mod is that mod
    (:func:`generator.for_mod`, phase 4)."""

    def setUp(self):
        self.loadout = Loadout(game="anno117", own=own_record(name="Mine", mod_id="me-mine"))
        self.loadout.make_own_mod()
        self.loadout.set_layer(ModLayer(origin="Theirs", name="Theirs", mod_id="theirs",
                                        declared=dict(_THEIRS), order=1))
        self.loadout.set_layer(ModLayer(origin="Patch", name="Patch", patches="Theirs", order=2))

    def test_the_own_mod_s_is_written_whole_with_its_blocks(self):
        self.loadout.options = _OPTIONS
        view = generator.for_mod(self.loadout, "")
        self.assertTrue(view.own.written_whole)
        self.assertEqual(view.own.declared, {"Options": _OPTIONS})
        self.assertEqual(modinfo.as_written(view), {})
        self.assertEqual(view.own.load_after, self.loadout.own_load_after())

    def test_a_loaded_mod_s_carries_its_modinfo_to_write_over(self):
        view = generator.for_mod(self.loadout, "Theirs")
        self.assertFalse(view.own.written_whole)
        self.assertEqual(modinfo.as_written(view), _THEIRS)
        self.assertEqual(json.loads(modinfo.build(view))["Scripts"], _THEIRS["Scripts"])

    def test_and_its_lua_comes_with_what_it_ships_not_as_scripts_copied_in(self):
        view = generator.for_mod(self.loadout, "Theirs")
        self.assertFalse(generator.writes_for_the_game(view))
        self.assertEqual(build.writes(self.loadout, "Theirs").scripts, [])

    def test_and_an_export_of_it_copies_in_no_lua(self):
        # Its modules come across with what it ships; asked to copy them
        # from a scripts folder it has none of, the export said each was
        # missing (#319).
        trouble = []
        with tempfile.TemporaryDirectory() as tmp:
            copied = scripts.carry(generator.for_mod(self.loadout, "Theirs"), tmp, trouble)
        self.assertEqual((copied, trouble), (0, []))

    def test_a_patch_s_names_what_it_patches_by_its_mod_id(self):
        view = generator.for_mod(self.loadout, "Patch")
        self.assertEqual(view.own.patches, "theirs")
        self.assertEqual(modinfo.studio_block(view)[modinfo.PATCHES_KEY], "theirs")

    def test_a_mod_this_loadout_does_not_have_is_refused(self):
        with self.assertRaises(LookupError):
            generator.for_mod(self.loadout, "Gone")

    def test_what_only_the_view_knows_is_never_saved(self):
        view = generator.for_mod(self.loadout, "Theirs")
        self.assertNotIn("written_whole", view.own.to_dict())
        self.assertNotIn("named_after_folder", view.own.to_dict())


class TestSayingTheOwnMod(unittest.TestCase):
    """"" is the own mod's origin, and is asked for as that: not as a false
    origin, which None, no origin given, is as well (phase 6)."""

    def test_one_name_and_one_question(self):
        self.assertEqual(OWN, "")
        self.assertEqual(Loadout().own.origin, OWN)
        self.assertTrue(is_own_mod(""))
        self.assertFalse(is_own_mod(None))
        self.assertFalse(is_own_mod("Theirs"))

    def test_every_mod_s_held_edits_are_asked_for_with_none(self):
        loadout = Loadout()
        loadout.held_edits = [{"origin": "Theirs", "why": "changed"}, {"origin": "Other", "why": "gone"}]
        self.assertEqual(len(held.of(loadout)), 2)
        self.assertEqual([one["origin"] for one in held.of(loadout, "Theirs")], ["Theirs"])
        # "" is the own mod, which has none.
        self.assertEqual(held.of(loadout, OWN), [])


class TestAskingForAMod(unittest.TestCase):
    def setUp(self):
        self.loadout = Loadout(own=own_record(name="Mine"))
        self.theirs = ModLayer(origin="Theirs", name="Theirs", order=1)
        self.other = ModLayer(origin="Other", name="Other", order=2)
        self.loadout.set_layer(self.theirs)
        self.loadout.set_layer(self.other)

    def test_mod_is_any_mod_and_layer_the_loaded_ones(self):
        self.assertIs(self.loadout.mod(""), self.loadout.own)
        self.assertIs(self.loadout.mod("Theirs"), self.loadout.layer("Theirs"))
        self.assertIsNone(self.loadout.mod("Gone"))
        self.assertIsNone(self.loadout.layer(""))

    def test_label_of_names_any_mod_and_one_not_here_by_its_origin(self):
        self.assertEqual(self.loadout.label_of(""), "Mine")
        self.assertEqual(self.loadout.label_of("Theirs"), "Theirs")
        self.assertEqual(self.loadout.label_of("Gone"), "Gone")
        # An own mod nobody has named: each place says it its own way.
        self.loadout.own.name = ""
        self.assertEqual(self.loadout.label_of(""), "")

    def test_mods_has_the_own_mod_only_once_it_is_made(self):
        self.assertEqual([mod.origin for mod in self.loadout.mods()], ["Theirs", "Other"])
        self.loadout.make_own_mod()
        self.assertIn(self.loadout.own, self.loadout.mods())

    def test_mods_are_in_load_order(self):
        self.loadout.make_own_mod()
        for loads_last in (False, True):
            with self.subTest(loads_last=loads_last):
                self.loadout.own.loads_last = loads_last
                origins = [mod.origin for mod in self.loadout.mods()]
                self.assertEqual(sorted(origins), sorted(["", "Theirs", "Other"]))
                ranks = [self.loadout.layer_rank(origin) for origin in origins]
                self.assertEqual(ranks, sorted(ranks))
                if loads_last:
                    self.assertEqual(origins[-1], "")


class TestTheFile(unittest.TestCase):
    """A loadout file keeps the own mod as one record, "own", written as a
    layer is, and its project its details under a layer's names (loadout
    format 6, project format 2).  A file from before is read whole."""

    def setUp(self):
        _isolation.forget_loadouts()
        self.addCleanup(_isolation.forget_loadouts)
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = Path(self._tmp.name) / "Main.annoload"

    def mine(self):
        loadout = Loadout(game="anno117", own=own_record(
            name="Mine", version="2.0", guid_range="Jakob", scripts_folder="C:/lua",
            loads_last=True, stale=[["updated", "Theirs"]]))
        loadout.options = dict(_OPTIONS)
        return loadout

    def test_the_own_mod_is_one_record(self):
        written = self.mine().to_dict()
        self.assertEqual(written["format"], 6)
        self.assertEqual(
            {key: written["own"][key] for key in ("origin", "name", "version", "guid_range",
                                                  "scripts_folder", "loads_last", "stale")},
            {"origin": "", "name": "Mine", "version": "2.0", "guid_range": "Jakob",
             "scripts_folder": "C:/lua", "loads_last": True, "stale": [["updated", "Theirs"]]},
        )
        self.assertEqual(written["own"]["declared"], {"Options": _OPTIONS})
        for old in ("mod_name", "creator", "description", "category", "version", "mod_id",
                    "own_mod_loads_last", "guid_range", "scripts_folder", "scripts", "options",
                    "difficulty", "game_setup", "own_stale"):
            self.assertNotIn(old, written)

    def test_and_back_on_opening(self):
        loadout = self.mine()
        loadout.save(self.path)
        loaded = Loadout.load(self.path)
        self.assertEqual(loaded.own, loadout.own)
        self.assertTrue(loaded.own.written_whole)
        self.assertIn("roomier", loaded.own.offered)

    def test_its_project_keeps_its_details_under_a_layer_s_names(self):
        loadout = self.mine()
        loadout.make_own_mod()
        loadout.save(self.path)
        project = projects.Project.read(projects.where(loadout.projects[""].path))
        self.assertEqual(project.details["name"], "Mine")
        self.assertEqual(project.details["declared"], {"Options": _OPTIONS})
        self.assertNotIn("mod_name", project.details)
        written = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertNotIn("name", written["own"])

    def test_a_checkpoint_keeps_the_own_mod_s_blocks_and_not_a_loaded_mod_s_modinfo(self):
        loadout = self.mine()
        loadout.set_layer(ModLayer(origin="Theirs", name="Theirs", declared={"ModID": "theirs"}))
        self.assertEqual(loadout.details_of("")["declared"], {"Options": _OPTIONS})
        self.assertNotIn("declared", loadout.details_of("Theirs"))
        self.assertNotIn("mod_id", loadout.details_of(""))

    def test_a_loaded_mod_without_what_only_a_mod_written_here_has_says_nothing_of_it(self):
        layer = ModLayer(origin="Theirs", name="Theirs")
        written = layer.to_dict()
        for name in ("scripts_folder", "guid_range", "loads_last"):
            self.assertNotIn(name, written)
        held = ModLayer(origin="Patch", scripts_folder="C:/lua", guid_range="Jakob", loads_last=True)
        self.assertEqual(ModLayer.from_dict(json.loads(json.dumps(held.to_dict()))), held)


if __name__ == "__main__":
    unittest.main()
