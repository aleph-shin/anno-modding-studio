"""What a mod runs as code: a modinfo's ``Scripts`` block, and the script
files its assets name -- seen and found, never read."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import _isolation  # noqa: F401 -- first: keeps the suite out of real files under any runner

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from support import PlayingWindow  # noqa: E402

from anno_modding_studio.core import generator, modinfo, scripts  # noqa: E402
from anno_modding_studio.core.changes import Change, Loadout, ModLayer  # noqa: E402
from anno_modding_studio.core.games import ANNO_117, ANNO_1800  # noqa: E402

# As *Commuter Pier* writes it, capitalised the way every census mod does.
COMMUTER_PIER = {
    "Anno": 8,
    "ModID": "commuter-pier",
    "Version": "4.1.0",
    "Scripts": {
        "Modules": ["scripts/commuter-pier.lua", "scripts/demand-data.lua"],
        "Init": 'CommuterPier = require("commuter-pier")',
        "Load": "CommuterPier:Load()",
        "Unload": "CommuterPier:Unload()",
        "Tick": "CommuterPier:Tick()",
    },
}


def _change(origin, guid, path="Values/Standard/Name", value="b", payload=""):
    return Change(guid=guid, asset_label=origin, path=path, tag=path.rsplit("/", 1)[-1],
                  op="replace", old_value="a", new_value=value, origin=origin, payload=payload)


class _Folders(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

    def mod(self, name, *files) -> Path:
        folder = self.root / name
        folder.mkdir()
        for relative in files:
            path = folder / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("-- lua", encoding="utf-8")
        return folder


class TestTheScriptsBlock(_Folders):
    def test_what_it_runs_and_whether_the_files_are_there(self):
        folder = self.mod("commuter-pier", "scripts/commuter-pier.lua")
        block = scripts.declared(COMMUTER_PIER, folder)
        self.assertEqual([m.path for m in block.modules],
                         ["scripts/commuter-pier.lua", "scripts/demand-data.lua"])
        self.assertEqual(block.missing, ["scripts/demand-data.lua"])
        self.assertEqual([name for name, _code in block.hooks], ["Init", "Load", "Unload", "Tick"])

    def test_the_docs_spelling_reads_the_same(self):
        """The docs write ``scripts`` and ``modules``; the mods do not."""
        block = scripts.declared({"scripts": {"modules": ["mymod\\some-script.lua"],
                                              "Init": "SomeScript = require(\"some-script\")"}})
        self.assertEqual([m.path for m in block.modules], ["mymod/some-script.lua"])
        self.assertIsNone(block.modules[0].found)  # no folder to look in
        # *Goods Amount* writes its module from the folder it sits in.
        folder = self.mod("goods", "goods-amount.lua")
        block = scripts.declared({"Scripts": {"Modules": ["./goods-amount.lua"]}}, folder)
        self.assertEqual((block.modules[0].path, block.modules[0].found), ("goods-amount.lua", True))

    def test_a_hook_nobody_has_heard_of_is_shown_like_the_rest(self):
        block = scripts.declared({"Scripts": {"Modules": [], "Save": "X:Save()"}})
        self.assertEqual(block.hooks, [("Save", "X:Save()")])

    def test_a_mod_without_one_runs_nothing(self):
        for declared in ({"ModID": "x"}, {"Scripts": []}, {"Scripts": {}}, None):
            self.assertIsNone(scripts.declared(declared))

    def test_it_goes_back_out_as_its_author_wrote_it(self):
        """The Studio owns six fields of a modinfo and carries the rest; the
        feature is experimental, so this is pinned rather than assumed."""
        for game in (ANNO_117, ANNO_1800):
            loadout = Loadout(game=game)
            loadout.set_layer(ModLayer(origin="Commuter Pier", mod_id="commuter-pier",
                                       declared=json.loads(json.dumps(COMMUTER_PIER))))
            out = json.loads(modinfo.build(generator.for_mod(loadout, "Commuter Pier")))
            self.assertEqual(out["Scripts"], COMMUTER_PIER["Scripts"], game)


class TestWhatAssetsRun(_Folders):
    def loadout(self, *layers):
        loadout = Loadout(game=ANNO_1800)
        loadout.layers = list(layers)
        return loadout

    def test_every_file_a_mod_s_assets_name(self):
        loadout = self.loadout(ModLayer(origin="Speed"), ModLayer(origin="Plain"))
        for change in (
            _change("Speed", "1", "Values/ActionExecuteScript/ScriptFileName", "data\\scripts\\speed_2.lua"),
            _change("Speed", "2", "Values", payload=(
                "<Asset><Values><ActionExecuteScript><ScriptFileName> data/scripts/speed_3.lua "
                "</ScriptFileName></ActionExecuteScript></Values></Asset>")),
            _change("Speed", "3", "Values/ActionExecuteScript/ScriptFileName", "data/scripts/speed_2.lua"),
            _change("Plain", "4", "Values/Standard/Name", "speed.lua"),
        ):
            loadout.changes[change.key] = change
        loadout.unread = [mock.Mock(origin="Speed",
                                    source="<ScriptFileName>data/scripts/kept.lua</ScriptFileName>")]
        self.assertEqual(scripts.named_by(loadout), {
            "Speed": ["data/scripts/speed_2.lua", "data/scripts/speed_3.lua", "data/scripts/kept.lua"],
        })
        self.assertEqual(scripts.runs_code(loadout), {"Speed": "its assets"})

    def test_the_file_is_where_the_last_mod_that_loads_has_it(self):
        """The game's files are one tree with every mod laid over it: a later
        mod's file stands in front of an earlier one's."""
        early = ModLayer(origin="Early", folder=str(self.mod("Early", "data/s.lua")), order=1)
        late = ModLayer(origin="Late", folder=str(self.mod("Late", "data/s.lua")), order=2)
        off = ModLayer(origin="Off", folder=str(self.mod("Off", "data/s.lua")), order=3, active=False)
        loadout = self.loadout(early, late, off)
        self.assertIs(scripts.locate(loadout, "data/s.lua"), late)
        self.assertIs(scripts.locate(loadout, "\\data\\s.lua"), late)
        self.assertIsNone(scripts.locate(loadout, "data/elsewhere.lua"))

    def test_a_replaced_mod_supplies_nothing(self):
        old = ModLayer(origin="Old", mod_id="old", folder=str(self.mod("Old", "data/s.lua")), order=2)
        new = ModLayer(origin="New", mod_id="new", folder=str(self.mod("New")), order=1,
                       declared={"DeprecateIds": ["old"]})
        self.assertIsNone(scripts.locate(self.loadout(new, old), "data/s.lua"))

    def test_the_loadout_s_own_modules_are_found_where_it_keeps_them(self):
        """Its own mod's modules are copied into its export (:func:`carry`), so
        the game finds them: an asset of its own naming one was said to name a
        script in no mod that loads (A11-90)."""
        mine = self.mod("Mine", "scripts/mine.lua", "scripts/loose.lua")
        early = ModLayer(origin="Early", folder=str(self.mod("Early", "scripts/mine.lua")), order=1)
        loadout = self.loadout(early)
        loadout.own_mod, loadout.own.loads_last = True, True
        loadout.scripts, loadout.own.scripts_folder = {"Modules": ["scripts/mine.lua"]}, str(mine)
        supplier = scripts.locate(loadout, "scripts\\mine.lua")
        self.assertEqual((supplier.origin, supplier.folder), ("", str(mine)))
        self.assertEqual(scripts.files(loadout.own_layer(), ["scripts/mine.lua"], loadout),
                         [mine / "scripts/mine.lua"])
        # Only a module goes out with the mod: another file there does not.
        self.assertIsNone(scripts.locate(loadout, "scripts/loose.lua"))

    def test_what_to_open(self):
        folder = self.mod("Pier", "scripts/commuter-pier.lua", "data/scripts/speed.lua")
        layer = ModLayer(origin="Pier", folder=str(folder), declared=COMMUTER_PIER, order=1)
        loadout = self.loadout(layer)
        self.assertEqual(
            scripts.files(layer, ["data/scripts/speed.lua", "data/scripts/gone.lua"], loadout),
            [folder / "scripts/commuter-pier.lua", folder / "data/scripts/speed.lua"],
        )

    def test_only_lua_inside_the_mod_is_offered_to_open(self):
        """A module is the mod's to name, and Windows runs a ``.js`` or a
        ``.cmd`` it is asked to open: one click on "Open a script" would
        have run it."""
        self.mod("Elsewhere", "x.lua")
        folder = self.mod("Sly", "data/scripts/tools.js", "data/scripts/run.cmd",
                          "data/scripts/ok.lua", "data/scripts/x\u202eaul.exe")
        declared = {"Scripts": {"Modules": [
            "data/scripts/tools.js", "data/scripts/run.cmd", "../Elsewhere/x.lua",
            str(self.root / "Elsewhere" / "x.lua"), "data/scripts/x\u202eaul.exe",
            "data/scripts/ok.lua",
        ]}}
        layer = ModLayer(origin="Sly", folder=str(folder), declared=declared, order=1)
        loadout = self.loadout(layer)
        self.assertEqual(scripts.files(layer, ["../Elsewhere/x.lua"], loadout),
                         [folder / "data/scripts/ok.lua"])

    def test_may_open_asks_the_kind_and_the_place(self):
        folder = self.mod("Mod", "a.lua", "a.js")
        other = self.mod("Other", "b.lua")
        self.assertTrue(scripts.may_open(folder / "a.lua", folder))
        self.assertTrue(scripts.may_open(folder / "A.LUA", folder))
        self.assertFalse(scripts.may_open(folder / "a.js", folder))
        self.assertFalse(scripts.may_open(other / "b.lua", folder))
        self.assertFalse(scripts.may_open(folder / ".." / "Other" / "b.lua", folder))
        self.assertFalse(scripts.may_open(folder / "gone.lua", folder))
        self.assertFalse(scripts.may_open(folder / "a.lua", ""))


class TestOpeningIt(_Folders):
    """A mod's script is opened to be read, never run."""

    def setUp(self):
        super().setUp()
        self.where = self.mod("Mod", "a.lua")
        self.script = self.where / "a.lua"

    def _open(self, program, edit_error=None):
        from anno_modding_studio.ui import widgets

        with mock.patch.object(widgets, "_opened_by", return_value=program), \
                mock.patch.object(widgets, "open_on_disk") as shown, \
                mock.patch.object(widgets.os, "startfile", create=True,
                                  side_effect=edit_error) as started, \
                mock.patch.object(widgets.subprocess, "Popen") as explorer:
            opened = widgets.open_script(self.script, self.where)
        return opened, shown, started, explorer

    def test_an_editor_opens_it_as_it_would_from_explorer(self):
        opened, shown, started, explorer = self._open(r"C:\Program Files\Code\Code.exe")
        self.assertTrue(opened)
        shown.assert_called_once_with(self.script)
        started.assert_not_called()
        explorer.assert_not_called()

    def test_where_lua_runs_it_the_edit_verb_opens_it(self):
        for program in (r"C:\Lua\5.1\lua.exe", r"C:\Lua\wlua.exe", r"C:\x\LuaJIT.exe",
                        r"C:\x\lua5.4.exe"):
            with self.subTest(program=program):
                opened, shown, started, explorer = self._open(program)
                self.assertTrue(opened)
                shown.assert_not_called()
                started.assert_called_once_with(str(self.script), "edit")
                explorer.assert_not_called()

    def test_with_no_edit_verb_explorer_shows_it(self):
        opened, shown, _started, explorer = self._open(r"C:\Lua\lua.exe", edit_error=OSError())
        self.assertTrue(opened)
        shown.assert_not_called()
        args = explorer.call_args[0][0]
        self.assertTrue(args[0].lower().endswith("explorer.exe"))
        self.assertEqual(args[1:], ["/select,", str(self.script)])

    def test_another_kind_is_not_opened_at_all(self):
        from anno_modding_studio.ui import widgets

        other = self.where / "a.cmd"
        other.write_text("echo", encoding="utf-8")
        with mock.patch.object(widgets, "open_on_disk") as shown:
            self.assertFalse(widgets.open_script(other, self.where))
        shown.assert_not_called()


class TestSayingIt(_Folders):
    def test_the_lines_for_a_mod_s_tooltip(self):
        folder = self.mod("Pier", "scripts/commuter-pier.lua")
        layer = ModLayer(origin="Pier", folder=str(folder), declared=COMMUTER_PIER)
        lines = scripts.describe(layer, ["data/a.lua"])
        self.assertTrue(lines[0].startswith("Runs scripts."))
        self.assertIn("none of it is in the changes shown here", lines[0])
        self.assertIn("  module scripts/commuter-pier.lua", lines)
        self.assertIn("  module scripts/demand-data.lua  (not in its folder)", lines)
        self.assertIn('  Init: CommuterPier = require("commuter-pier")', lines)
        self.assertEqual(lines[-1], "  its assets run data/a.lua")
        self.assertEqual(scripts.describe(ModLayer(origin="Plain")), [])

    def test_a_long_snippet_is_cut(self):
        layer = ModLayer(origin="X", declared={"Scripts": {"Init": "x = " + "1" * 200}})
        (hook,) = [line for line in scripts.describe(layer) if "Init" in line]
        self.assertTrue(hook.endswith("…"))
        self.assertLess(len(hook), 100)


class TestInTheWindow(PlayingWindow):
    def test_the_row_says_it_runs_scripts_and_offers_to_open_them(self):
        folder = self.mods / "commuter-pier"
        (folder / "scripts").mkdir(parents=True)
        (folder / "scripts" / "commuter-pier.lua").write_text("-- lua", encoding="utf-8")
        loadout = self.window.loadout
        loadout.layers = [
            ModLayer(origin="commuter-pier", mod_id="commuter-pier", name="Commuter Pier",
                     folder=str(folder), declared=COMMUTER_PIER),
            ModLayer(origin="Plain", mod_id="plain", name="Plain"),
        ]
        for change in (_change("commuter-pier", "1"), _change("Plain", "2")):
            loadout.changes[change.key] = change
        panel = self.window.changes_panel
        panel.refresh()
        from anno_modding_studio.ui import changes_dock

        rows = {}
        for position in range(panel.tree.topLevelItemCount()):
            item = panel.tree.topLevelItem(position)
            if item.data(0, changes_dock.KIND_ROLE) == "mod":
                rows[item.data(0, changes_dock.ORIGIN_ROLE)] = item
        self.assertEqual(rows["commuter-pier"].text(0), "Commuter Pier  (runs scripts)")
        self.assertEqual(rows["Plain"].text(0), "Plain")
        self.assertIn("module scripts/demand-data.lua  (not in its folder)", rows["commuter-pier"].toolTip(0))

        menu = panel.menu_for(rows["commuter-pier"])
        (opener,) = [action for action in menu.actions() if action.text() == "Open a script"]
        self.assertEqual([action.text() for action in opener.menu().actions()], ["commuter-pier.lua"])
        (script,) = opener.menu().actions()
        self.assertEqual(script.toolTip(), str(folder / "scripts" / "commuter-pier.lua"))
        menu = panel.menu_for(rows["Plain"])
        self.assertEqual([action for action in menu.actions() if action.text() == "Open a script"], [])

        # Read from a zip, the file is the Studio's copy of what the zip holds,
        # and the entry says so where it gave the copy's path as the mod's.
        with mock.patch.object(changes_dock.zipped, "is_mirrored", return_value=True), \
                mock.patch.object(changes_dock.zipped, "zip_for",
                                  return_value=self.mods / "commuter-pier.zip"):
            menu = panel.menu_for(rows["commuter-pier"])
        (opener,) = [action for action in menu.actions() if action.text() == "Open a script"]
        (script,) = opener.menu().actions()
        self.assertIn("A copy the Studio keeps of the script in commuter-pier.zip. A change made "
                      "to the copy does not reach the mod.", script.toolTip())

    def test_the_editor_says_which_mod_has_the_script(self):
        from PySide6.QtWidgets import QTreeWidgetItem

        folder = self.mods / "Speed"
        (folder / "data" / "scripts").mkdir(parents=True)
        (folder / "data" / "scripts" / "speed_2.lua").write_text("-- lua", encoding="utf-8")
        self.window.loadout.layers = [ModLayer(origin="Speed", name="Speed", folder=str(folder))]
        editor = self.window.editor
        found = QTreeWidgetItem(["ScriptFileName", "data/scripts/speed_2.lua", ""])
        editor._annotate_reference(found)
        self.assertEqual(found.text(2), "script in Speed")
        self.assertIn("Double-click or press Enter to open it", found.toolTip(2))
        lost = QTreeWidgetItem(["ScriptFileName", "data/scripts/vanilla.lua", ""])
        editor._annotate_reference(lost)
        self.assertEqual(lost.text(2), "script in no mod that loads")
        self.assertIn("the game's own scripts", lost.toolTip(2))


if __name__ == "__main__":
    unittest.main()
