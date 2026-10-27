"""Which InfoTips a mod patches, and which mods patch the same ones -- listed,
not read for what the tooltips come to."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

import _isolation  # noqa: F401 -- first: keeps the suite out of real files under any runner

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from support import PlayingWindow  # noqa: E402

from anno_modding_studio.core import infotips, modops  # noqa: E402
from anno_modding_studio.core.changes import Change, Loadout, ModLayer  # noqa: E402
from anno_modding_studio.core.games import ANNO_117, ANNO_1800  # noqa: E402


class _Mods(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

    def mod(self, name, body, **layer) -> ModLayer:
        folder = self.root / name
        path = folder / infotips.PATCH
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("<ModOps>%s</ModOps>" % body, encoding="utf-8")
        layer.setdefault("mod_id", name.lower())
        return ModLayer(origin=name, name=name, folder=str(folder), **layer)

    def patch(self, body, game=ANNO_1800) -> infotips.Patch:
        layer = self.mod("m%d" % len(list(self.root.iterdir())), body)
        return infotips.read(infotips.patch_file(layer.folder), game)


class TestReadingAPatch(_Mods):
    def test_ops_keyed_to_an_infotip(self):
        """How nearly every op in the census names its InfoTip."""
        patch = self.patch(
            "<ModOp Type=\"addNextSibling\" Path=\"//InfoTipData[Guid='25000020']/InfoElement[1]\">"
            "<InfoElement/></ModOp>"
            "<ModOp Type=\"remove\" Path=\"//InfoTipData[Guid='25000020']/InfoElement[2]\"/>"
            "<ModOp Type=\"replace\" Path=\"//InfoTipData[Guid='1' or Guid='2']/InfoElement\"><InfoElement/></ModOp>"
        )
        self.assertEqual(patch.changes, {"25000020": 2, "1": 1, "2": 1})
        self.assertEqual((patch.ops, patch.searches, patch.adds), (3, 0, []))

    def test_117_names_them_by_lookup(self):
        patch = self.patch(
            '<ModOp GUID="500934" Add=""><InfoElement/></ModOp>'
            '<ModOp Add="@500935"><InfoElement/></ModOp>',
            ANNO_117,
        )
        self.assertEqual(sorted(patch.changes), ["500934", "500935"])

    def test_a_guid_is_looked_for_as_written(self):
        """Split on the comma and nothing trimmed, as the loader does: the
        space before 500935 is part of what it looks for, and finds none."""
        patch = self.patch('<ModOp GUID="500934, 500935" Add=""><InfoElement/></ModOp>', ANNO_117)
        self.assertEqual((patch.changes, patch.searches), ({"500934": 1}, 0))

    def test_an_op_its_game_does_not_read_touches_none(self):
        patch = self.patch('<ModOp Add="@500935"><InfoElement/></ModOp>', ANNO_1800)
        self.assertEqual((patch.ops, patch.changes, patch.searches), (1, {}, 0))

    def test_a_whole_infotip_added(self):
        patch = self.patch(
            "<ModOp Type=\"addNextSibling\" Path=\"//InfoTipData[Guid='100']\">"
            "<InfoTipData><Guid>1500000001</Guid><InfoElement/></InfoTipData></ModOp>"
        )
        self.assertEqual((patch.adds, patch.changes), (["1500000001"], {}))

    def test_a_search_across_all_of_them(self):
        patch = self.patch(
            "<ModOp Type=\"replace\" Path=\"//VisibilityElement[Condition='1']\"><VisibilityElement/></ModOp>"
        )
        self.assertEqual((patch.searches, patch.changes), (1, {}))

    def test_groups_and_included_files_are_read_through(self):
        layer = self.mod(
            "Grouped",
            '<Group Condition="#other"><ModOp Type="remove" Path="//InfoTipData[Guid=\'7\']/InfoElement"/></Group>'
            '<Include File="more.include.xml"/>',
        )
        (Path(layer.folder) / "data/infotips/more.include.xml").write_text(
            "<ModOps><ModOp Type=\"remove\" Path=\"//InfoTipData[Guid='8']/InfoElement\"/></ModOps>",
            encoding="utf-8",
        )
        patch = infotips.read(infotips.patch_file(layer.folder), ANNO_1800)
        self.assertEqual(sorted(patch.changes), ["7", "8"])

    def test_a_file_the_loader_cannot_read_says_so(self):
        folder = self.root / "Broken" / "data" / "infotips"
        folder.mkdir(parents=True)
        (folder / "export.bin.xml").write_text("", encoding="utf-8")
        patch = infotips.read(folder / "export.bin.xml", ANNO_1800)
        self.assertTrue(patch.error)
        self.assertIn("could not be read", infotips.describe(patch)[0])


class TestWhichModsPatchTheSameOnes(_Mods):
    ONE = "<ModOp Type=\"remove\" Path=\"//InfoTipData[Guid='%s']/InfoElement\"/>"

    def test_an_infotip_two_mods_patch(self):
        loadout = Loadout(game=ANNO_1800)
        loadout.layers = [
            self.mod("First", self.ONE % "1" + self.ONE % "2", order=1),
            self.mod("Second", self.ONE % "1", order=2),
            # A second copy of the first, bundled elsewhere: one mod to a reader.
            ModLayer(origin="Host/First", name="First", mod_id="first-copy",
                     folder=self.mod("Copy", self.ONE % "1").folder, order=3),
            self.mod("Off", self.ONE % "2", active=False, order=4),
        ]
        seen = infotips.overview(loadout)
        self.assertEqual(seen.shared, {"1": ["First", "Second"]})
        self.assertEqual(seen.others("First", "First"), {"1": ["Second"]})
        self.assertEqual(seen.others("Second", "Second"), {"1": ["First"]})

    def test_a_replaced_mod_patches_nothing(self):
        loadout = Loadout(game=ANNO_1800)
        loadout.layers = [
            self.mod("New", self.ONE % "1", declared={"DeprecateIds": ["old"]}, order=1),
            self.mod("Old", self.ONE % "1", order=2),
        ]
        self.assertEqual(infotips.overview(loadout).shared, {})

    def test_the_lines_for_a_tooltip(self):
        patch = infotips.Patch(changes={"1": 2, "2": 1}, adds=["3"], searches=1, ops=4)
        lines = infotips.describe(patch, {"1": ["A", "B", "C", "D", "E"]})
        self.assertEqual(lines[0], "Patches the game's tooltips: changes 2 InfoTips, adds 1 InfoTip, "
                                   "has 1 op that searches every InfoTip. What they come to is not shown here.")
        self.assertEqual(lines[1], "  InfoTip 1 is patched by A, B, C and 2 more as well")
        self.assertEqual(infotips.describe(infotips.Patch()), [])


class TestTheImportSaysWhatItIs(_Mods):
    def test_listed_as_an_infotip_patch(self):
        layer = self.mod("M", "")
        (Path(layer.folder) / "modinfo.json").write_text('{"ModID": "m"}', encoding="utf-8")
        _patches, extras, _meta = modops.scan_folder(Path(layer.folder))
        (found,) = [extra for extra in extras if "export.bin.xml" in extra.where]
        self.assertEqual(found.what, "an InfoTip patch")


class TestInTheChangesPanel(PlayingWindow):
    def test_the_tooltip_says_what_it_patches_and_who_else_does(self):
        from anno_modding_studio.ui import changes_dock

        loadout = self.window.loadout
        for name in ("Serp Tooltips", "Kurila Farm"):
            path = self.mods / name / infotips.PATCH
            path.parent.mkdir(parents=True)
            path.write_text(
                "<ModOps><ModOp Type=\"remove\" Path=\"//InfoTipData[Guid='25000020']/InfoElement\"/></ModOps>",
                encoding="utf-8",
            )
        loadout.layers = [
            ModLayer(origin=name, name=name, mod_id=name.lower(), folder=str(self.mods / name), order=n)
            for n, name in enumerate(("Serp Tooltips", "Kurila Farm"), start=1)
        ]
        for n, origin in enumerate(("Serp Tooltips", "Kurila Farm")):
            change = Change(guid=str(n + 1), asset_label=origin, path="Values/Standard/Name", tag="Name",
                            op="replace", old_value="a", new_value="b", origin=origin)
            loadout.changes[change.key] = change
        panel = self.window.changes_panel
        panel.refresh()
        rows = {}
        for position in range(panel.tree.topLevelItemCount()):
            item = panel.tree.topLevelItem(position)
            if item.data(0, changes_dock.KIND_ROLE) == "mod":
                rows[item.data(0, changes_dock.ORIGIN_ROLE)] = item
        tip = rows["Kurila Farm"].toolTip(0)
        self.assertIn("Patches the game's tooltips: changes 1 InfoTip.", tip)
        self.assertIn("InfoTip 25000020 is patched by Serp Tooltips as well", tip)


if __name__ == "__main__":
    unittest.main()
