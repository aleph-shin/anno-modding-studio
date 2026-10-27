"""Lua tier 2: what script mods share across a loadout, and a mod's own
``Scripts`` block, edited and scaffolded here and carried out on export."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

import _isolation  # noqa: F401 -- first: keeps the suite out of real files under any runner

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from support import PlayingWindow  # noqa: E402

from anno_modding_studio.core import conflicts, generator, scripts  # noqa: E402
from anno_modding_studio.core.changes import Loadout, ModLayer, own_record  # noqa: E402
from anno_modding_studio.core.games import ANNO_117  # noqa: E402

# Two of SirLocksley's mods as the census has them: each ships its own
# parchment-controls-consumer.lua (566 and 282 bytes), and numbers the global
# it keeps it in so that those do not clash.
SHIP_FINDER = {
    "ModID": "ship-finder-sirlocksley",
    "Scripts": {
        "Modules": ["shipfinder/ship-finder.lua", "shipfinder/parchment-controls-consumer.lua"],
        "Init": 'ShipFinder = require("ship-finder"); SirLocksleyPC01 = require("parchment-controls-consumer"); '
                "SirLocksleyPC01:SetRoot(ShipFinder)",
        "Tick": "ShipFinder:Tick()",
    },
}
HARBOR_ARRIVALS = {
    "ModID": "harbor-arrivals-sirlocksley-prototype",
    "Scripts": {
        "Modules": ["harborarrivals/harbor-arrivals.lua", "harborarrivals/parchment-controls-consumer.lua"],
        "Init": 'HarborArrivals = require("harbor-arrivals"); SirLocksleyPC04 = require("parchment-controls-consumer")',
        "Tick": "HarborArrivals:Tick()",
    },
}


def _script_mod(origin, init, modules=(), order=1, **kw):
    block = {"Modules": list(modules), "Init": init}
    return ModLayer(origin=origin, name=origin, mod_id=origin.lower(), order=order,
                    declared={"ModID": origin.lower(), "Scripts": block}, **kw)


def _loadout(*layers) -> Loadout:
    loadout = Loadout(game=ANNO_117)
    for layer in layers:
        loadout.set_layer(layer)
    return loadout


class _Folders(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

    def mod(self, name, files) -> str:
        folder = self.root / name
        for relative, text in files.items():
            path = folder / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
        folder.mkdir(exist_ok=True)
        return str(folder)


class TestWhatASnippetSets(unittest.TestCase):
    def test_the_require_form_and_a_handle_kept_alongside(self):
        self.assertEqual(scripts.sets(SHIP_FINDER["Scripts"]["Init"]), ["ShipFinder", "SirLocksleyPC01"])
        self.assertEqual(scripts.sets("SpecialistFinderRC = require(\"specialist-finder\"); "
                                      "SirLocksleyPC03 = SpecialistFinderRC.PC03"),
                         ["SpecialistFinderRC", "SirLocksleyPC03"])

    def test_not_a_local_a_field_a_comparison_or_a_call(self):
        self.assertEqual(scripts.sets("local X = 1; A.b = 2; if a == b then end; F:Tick()\nY=3"), ["Y"])

    def test_names_made_from_a_mod_id(self):
        self.assertEqual(scripts.global_name("commuter-pier"), "CommuterPier")
        self.assertEqual(scripts.global_name("117-tweaks"), "Mod117Tweaks")
        self.assertEqual(scripts.global_name(""), "MyScript")
        self.assertEqual(scripts.module_name("Goods Finder!"), "goods-finder")
        self.assertEqual(scripts.stem("./scripts/Goods-Amount.lua"), "Goods-Amount")


class TestClashes(_Folders):
    def test_the_census_pair_share_a_module_and_no_global(self):
        loadout = _loadout(
            ModLayer(origin="ShipFinder", name="Ship Finder", order=1, declared=SHIP_FINDER),
            ModLayer(origin="Harbor Arrivals", name="Harbor Arrivals", order=2, declared=HARBOR_ARRIVALS),
        )
        (clash,) = scripts.clashes(loadout)
        self.assertEqual((clash.kind, clash.name, clash.origins),
                         (scripts.MODULE, "parchment-controls-consumer", ("ShipFinder", "Harbor Arrivals")))
        self.assertFalse(clash.is_harmless)
        self.assertEqual(
            clash.describe(lambda origin: loadout.layer(origin).label),
            "Ship Finder and Harbor Arrivals each ship their own parchment-controls-consumer.lua. "
            "Lua's require keeps one module per name, so one of them is likely to get the other's",
        )
        self.assertEqual(conflicts.summarise(loadout, str), "1 script name shared")

    def test_the_same_file_twice_is_harmless_and_a_different_one_is_not(self):
        same = {"lib/shared.lua": "return {}"}
        loadout = _loadout(
            _script_mod("A", 'A = require("a")', ["lib/shared.lua"], 1, folder=self.mod("A", same)),
            _script_mod("B", 'B = require("b")', ["lib/Shared.lua"], 2,
                        folder=self.mod("B", {"lib/Shared.lua": "return {}"})),
        )
        (clash,) = scripts.clashes(loadout)
        self.assertTrue(clash.same)
        self.assertEqual(scripts.summarise([clash]), "")
        Path(loadout.layer("B").folder, "lib/Shared.lua").write_text("return {1}", encoding="utf-8")
        self.assertFalse(scripts.clashes(loadout)[0].same)

    def test_a_global_two_mods_set(self):
        loadout = _loadout(_script_mod("A", 'Finder = require("a")', order=1),
                           _script_mod("B", 'Finder = require("b")', order=2))
        (clash,) = scripts.clashes(loadout)
        self.assertEqual((clash.kind, clash.name, clash.where), (scripts.GLOBAL, "Finder", ("Init", "Init")))
        self.assertEqual(scripts.describe_clashes([clash], "A", str), ["  global Finder is also set by B"])

    def test_only_what_loads_counts(self):
        off = _script_mod("B", 'Finder = require("b")', order=2)
        off.active = False
        loadout = _loadout(_script_mod("A", 'Finder = require("a")', order=1), off)
        self.assertEqual(scripts.clashes(loadout), [])
        # Nor one mod naming a thing twice.
        loadout = _loadout(_script_mod("A", 'Finder = require("a"); Finder = Finder'))
        self.assertEqual(scripts.clashes(loadout), [])

    def test_your_own_mod_counts_last(self):
        loadout = _loadout(_script_mod("A", 'Finder = require("a")'))
        loadout.make_own_mod()
        loadout.scripts = {"Init": 'Finder = require("mine")'}
        (clash,) = scripts.clashes(loadout)
        self.assertEqual(clash.origins, ("A", ""))
        self.assertEqual(scripts.runs_code(loadout)[""], "its modinfo")

    def test_a_block_being_edited_counts_in_its_place(self):
        loadout = _loadout(_script_mod("A", 'Finder = require("a")', order=1),
                           _script_mod("B", 'Other = require("b")', order=2))
        (clash,) = scripts.clashes(loadout, instead=("B", {"Init": 'Finder = require("b")'}, ""))
        self.assertEqual(clash.origins, ("A", "B"))
        self.assertEqual(scripts.clashes(loadout, instead=("A", {}, "")), [])


class TestANewModule(unittest.TestCase):
    def test_names_suggested_clear_of_every_loading_mod(self):
        loadout = _loadout(_script_mod("CP", 'CommuterPier = require("commuter-pier")',
                                       ["scripts/commuter-pier.lua"]))
        self.assertEqual(scripts.suggest(loadout, "", "commuter-pier"), ("commuter-pier-2", "CommuterPier2"))
        self.assertEqual(scripts.suggest(loadout, "", "my-mod"), ("my-mod", "MyMod"))
        # Its own block counts too, so a second module gets a name of its own.
        own = scripts.with_module({}, "scripts/my-mod.lua", "MyMod")
        self.assertEqual(scripts.suggest(loadout, "", "my-mod", own), ("my-mod-2", "MyMod2"))

    def test_wired_in_after_what_the_hooks_already_run(self):
        block = {"modules": ["a.lua"], "Init": "A = require(\"a\");", "Tick": "A:Tick()"}
        wired = scripts.with_module(block, "scripts/b.lua", "B", ("Load", "Tick"))
        self.assertEqual(wired, {
            "modules": ["a.lua", "scripts/b.lua"],
            "Init": 'A = require("a"); B = require("b")',
            "Tick": "A:Tick(); B:Tick()",
            "Load": "B:Load()",
        })

    def test_from_the_docs_template(self):
        self.assertEqual(scripts.template("MyMod", "my-mod", ("Load", "Tick")), "\n".join([
            '-- Loaded by the mod\'s modinfo: its Init runs MyMod = require("my-mod"),',
            "-- and each hook below is called from the hook of the same name.",
            "local MyMod = {}",
            "",
            "function MyMod.Load() end",
            "",
            "function MyMod.Tick() end",
            "",
            "return MyMod;",
        ]) + "\n")


class TestKeepingIt(_Folders):
    def test_a_loaded_mod_s_block_keeps_its_key_and_place(self):
        layer = ModLayer(origin="A", declared={"ModID": "a", "scripts": {"Init": "x = 1"}, "Version": "1"})
        loadout = _loadout(layer)
        scripts.set_block(loadout, "A", {"Init": "y = 2"})
        self.assertEqual(list(layer.declared), ["ModID", "scripts", "Version"])
        self.assertEqual(layer.declared["scripts"], {"Init": "y = 2"})
        scripts.set_block(loadout, "A", {})
        self.assertEqual(list(layer.declared), ["ModID", "Version"])

    def test_your_mod_s_goes_out_with_its_files(self):
        folder = self.mod("lua", {"scripts/my-mod.lua": "return {}"})
        loadout = Loadout(game=ANNO_117, own=own_record(name="My Mod", mod_id="my-mod"))
        loadout.make_own_mod()
        scripts.set_block(loadout, "", scripts.with_module({}, "scripts/my-mod.lua", "MyMod"))
        loadout.own.scripts_folder = folder
        view = generator.for_mod(loadout, "")
        out = self.root / "out"
        trouble = []
        root = generator.write_mod_folder(view, out, generator.build_modops_xml(view), trouble=trouble)
        self.assertEqual(trouble, [])
        self.assertEqual((root / "scripts/my-mod.lua").read_text(encoding="utf-8"), "return {}")
        written = json.loads((root / "modinfo.json").read_text(encoding="utf-8"))
        self.assertEqual(written["Scripts"]["Init"], 'MyMod = require("my-mod")')

    def test_a_mod_that_is_only_its_lua_has_something_to_write(self):
        loadout = Loadout(game=ANNO_117)
        loadout.make_own_mod()
        self.assertFalse(generator.writes_anything(generator.for_mod(loadout, "")))
        loadout.scripts = {"Init": "x = 1"}
        self.assertTrue(generator.writes_anything(generator.for_mod(loadout, "")))

    def test_a_module_that_is_not_there_is_said(self):
        loadout = Loadout(game=ANNO_117)
        loadout.scripts = {"Modules": ["scripts/gone.lua"]}
        trouble = []
        self.assertEqual(scripts.carry(loadout, self.root, trouble), 0)
        self.assertEqual(trouble, ["scripts/gone.lua: no scripts folder is set for this mod, so the "
                                   "mod will ask the game for a file it does not have"])

    def test_a_loaded_mod_s_edited_block_goes_out_in_its_modinfo(self):
        layer = ModLayer(origin="A", name="A", declared={"ModID": "a", "Anno": 8, "Scripts": {"Init": "x = 1"}})
        loadout = _loadout(layer)
        scripts.set_block(loadout, "A", {"Init": "y = 2"})
        written = json.loads(generator.build_modinfo(generator.for_mod(loadout, "A")))
        self.assertEqual(written["Scripts"], {"Init": "y = 2"})

    def test_saved_and_counted_as_work(self):
        loadout = Loadout(game=ANNO_117)
        before = loadout.typed_details()
        loadout.scripts = {"Init": "x = 1"}
        loadout.own.scripts_folder = "C:/lua"
        self.assertTrue(loadout.details_moved(before))
        path = self.root / "p.json"
        loadout.save(path)
        back = Loadout.load(path)
        self.assertEqual((back.scripts, back.own.scripts_folder), ({"Init": "x = 1"}, "C:/lua"))


class _Qt(_Folders):
    def setUp(self):
        super().setUp()
        from PySide6.QtWidgets import QApplication

        self.app = QApplication.instance() or QApplication([])


class TestTheDialog(_Qt):
    def make(self, loadout, origin="", mod_id="my-mod"):
        from anno_modding_studio.ui.scripts_dialog import ScriptsDialog

        dialog = ScriptsDialog(loadout, origin, mod_id)
        self.addCleanup(dialog.deleteLater)
        return dialog

    def test_a_new_module_is_written_on_ok_and_not_before(self):
        loadout = _loadout()
        loadout.make_own_mod()
        loadout.own.scripts_folder = str(self.root)
        dialog = self.make(loadout)
        path = dialog.add_module("my-mod", "MyMod", ["Tick"])
        self.assertEqual(path, "scripts/my-mod.lua")
        self.assertFalse((self.root / path).exists())
        self.assertEqual(dialog.hook_edits["Init"].text(), 'MyMod = require("my-mod")')
        self.assertIn("new, written when you press OK", dialog.modules.item(0).text())
        dialog._accept()
        self.assertIn("local MyMod = {}", (self.root / path).read_text(encoding="utf-8"))
        self.assertEqual(loadout.scripts, {"Modules": [path], "Init": 'MyMod = require("my-mod")',
                                           "Tick": "MyMod:Tick()"})

    def test_cancelled_it_leaves_nothing(self):
        loadout = _loadout()
        loadout.own.scripts_folder = str(self.root)
        dialog = self.make(loadout)
        dialog.add_module("my-mod", "MyMod", [])
        dialog.reject()
        self.assertEqual(list(self.root.iterdir()), [])
        self.assertEqual(loadout.scripts, {})

    def test_a_clash_is_said_as_you_type(self):
        loadout = _loadout(_script_mod("A", 'Finder = require("a")'))
        dialog = self.make(loadout)
        self.assertTrue(dialog.shared.isHidden())
        dialog.hook_edits["Init"].setText('Finder = require("mine")')
        self.assertFalse(dialog.shared.isHidden())
        self.assertIn("A and My Anno Mod each set the global Finder", dialog.shared.text())

    def test_what_it_does_not_edit_it_keeps(self):
        layer = ModLayer(origin="A", name="A", folder=self.mod("A", {}), declared={
            "Scripts": {"Tick": "A:Tick()", "Future": {"x": 1}, "modules": ["a.lua"], "Save": "A:Save()"},
        })
        loadout = _loadout(layer)
        dialog = self.make(loadout, "A")
        self.assertIn("Save", dialog.hook_edits)
        self.assertEqual(dialog.block(), layer.declared["Scripts"])
        self.assertIn("a.lua  (not in the folder)", dialog.modules.item(0).text())

    def test_a_file_outside_the_mod_is_refused(self):
        from unittest import mock

        from anno_modding_studio.ui import scripts_dialog

        loadout = _loadout()
        loadout.own.scripts_folder = self.mod("lua", {"a.lua": ""})
        dialog = self.make(loadout)
        self.assertTrue(dialog.add_path(str(self.root / "lua" / "a.lua")))
        self.assertEqual(dialog.paths(), ["a.lua"])
        with mock.patch.object(scripts_dialog.QMessageBox, "information") as said:
            self.assertFalse(dialog.add_path(str(self.root / "elsewhere.lua")))
        said.assert_called_once()

    def test_the_new_module_names_must_be_free(self):
        from anno_modding_studio.ui.scripts_dialog import NewModuleDialog

        dialog = NewModuleDialog("my-mod", "Finder", ({"my-mod"}, {"Finder"}))
        self.addCleanup(dialog.deleteLater)
        self.assertEqual(dialog.problem_text(), "A loading mod already has a module my-mod.lua.")
        dialog.stem_edit.setText("other")
        self.assertEqual(dialog.problem_text(), "A loading mod already sets the global Finder.")
        dialog.name_edit.setText("1bad")
        self.assertIn("a Lua name", dialog.problem_text())
        self.assertFalse(dialog.problem.isHidden())
        dialog.name_edit.setText("Mine")
        self.assertEqual(dialog.problem_text(), "")
        self.assertEqual(dialog.values(), ("other", "Mine", ["Tick"]))
        # With nothing to say, nothing is shown: its warning glyph stood
        # alone under Called on (H2-30).
        self.assertTrue(dialog.problem.isHidden())


class TestTheConflictsDialog(_Qt):
    def test_scripts_are_listed_beneath_the_fields(self):
        from anno_modding_studio.ui.conflicts_dialog import ConflictsDialog

        loadout = _loadout(
            ModLayer(origin="ShipFinder", name="Ship Finder", order=1, declared=SHIP_FINDER),
            ModLayer(origin="Harbor Arrivals", name="Harbor Arrivals", order=2, declared=HARBOR_ARRIVALS),
        )
        dialog = ConflictsDialog(loadout)
        self.addCleanup(dialog.deleteLater)
        self.assertFalse(dialog.scripts_table.isHidden())
        self.assertEqual(dialog.scripts_table.rowCount(), 1)
        self.assertEqual(dialog.scripts_table.item(0, 0).text(), "parchment-controls-consumer.lua")
        self.assertEqual(dialog.scripts_table.item(0, 2).text(), "Ship Finder → Harbor Arrivals")
        self.assertIn("1 script name shared", dialog.summary.text())
        menu = dialog.script_menu_for(0)
        self.addCleanup(menu.deleteLater)
        opens = [action for action in menu.actions() if action.text().startswith("Open")]
        # Neither file is on disk here, so there is nothing to open.
        self.assertEqual([action.isEnabled() for action in opens], [False, False])

    def test_hidden_when_nothing_is_shared(self):
        from anno_modding_studio.ui.conflicts_dialog import ConflictsDialog

        dialog = ConflictsDialog(_loadout(_script_mod("A", 'A = require("a")')))
        self.addCleanup(dialog.deleteLater)
        self.assertTrue(dialog.scripts_table.isHidden())




class TestInTheWindow(PlayingWindow):
    def test_the_export_tab_says_what_a_mod_runs_and_edits_only_yours(self):
        loadout = self.window.loadout
        loadout.game = ANNO_117
        loadout.make_own_mod()
        loadout.scripts = {"Modules": ["scripts/a.lua"], "Init": 'A = require("a")', "Tick": "A:Tick()"}
        output = self.window.output
        output.refresh()
        self.assertTrue(output._scripts_form.isRowVisible(output._scripts_row))
        self.assertEqual(output.scripts_label.text(), "1 module, run on Init, Tick")
        self.assertTrue(output.scripts_button.isEnabledTo(output.meta_box))
        loadout.layers = [ModLayer(origin="Theirs", name="Theirs", creator="someone else", declared=SHIP_FINDER)]
        output._show_details_for("Theirs")
        self.assertEqual(output.scripts_label.text(), "2 modules, run on Init, Tick")
        self.assertFalse(output.scripts_button.isEnabledTo(output.meta_box))
        loadout.game = "anno1800"
        output._show_details_for("")
        self.assertFalse(output._scripts_form.isRowVisible(output._scripts_row))

    def test_a_mod_of_yours_that_is_only_lua_is_written_out(self):
        loadout = self.window.loadout
        loadout.game = ANNO_117
        loadout.make_own_mod()
        loadout.layers = [ModLayer(origin="Theirs", name="Theirs")]
        output = self.window.output
        self.assertNotIn("", output.mine_to_export())
        loadout.scripts = {"Init": "x = 1"}
        self.assertEqual(output.mine_to_export(), [""])
        output.refresh()
        self.assertEqual(output.check_summary.text(),
                         "No changes to check. This mod is its scripts: the modinfo "
                         "lists them, and the export copies them in.")

    def test_the_mod_rows_say_what_they_share(self):
        from anno_modding_studio.ui import changes_dock

        loadout = self.window.loadout
        loadout.layers = [
            ModLayer(origin="ShipFinder", name="Ship Finder", order=1, declared=SHIP_FINDER),
            ModLayer(origin="Harbor Arrivals", name="Harbor Arrivals", order=2, declared=HARBOR_ARRIVALS),
        ]
        panel = self.window.changes_panel
        panel.refresh()
        self.assertFalse(panel.conflicts_button.isHidden())
        self.assertEqual(panel.conflicts_button.text(), "1 script name shared")
        rows = {}
        for position in range(panel.tree.topLevelItemCount()):
            item = panel.tree.topLevelItem(position)
            if item.data(0, changes_dock.KIND_ROLE) == "mod":
                rows[item.data(0, changes_dock.ORIGIN_ROLE)] = item
        self.assertIn("module parchment-controls-consumer.lua is also Harbor Arrivals's",
                      rows["ShipFinder"].toolTip(0))


if __name__ == "__main__":
    unittest.main()
