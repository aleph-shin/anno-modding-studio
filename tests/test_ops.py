"""The structural ModOp types, and the schema that makes them offerable.

Every dialect here was taken from real shipping mods, not guessed:
``Type="add"``/``addNextSibling``/``remove`` from Anno 1800 mods, and
``Add=``/``Remove=``/``Merge=`` from the Anno 117 community bug-fix mod.
``BaseAssetGUID`` is a base-game feature (556 uses in 1800, 906 in 117).
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

import _isolation  # noqa: F401 -- first: keeps the suite out of real files under any runner
from lxml import etree

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from support import SCHEMA_ASSETS, SchemaTestBase  # noqa: E402

from anno_modding_studio.core import games, generator  # noqa: E402
from anno_modding_studio.core import index as index_mod  # noqa: E402
from anno_modding_studio.core.changes import (  # noqa: E402
    OP_ADD,
    OP_ADD_NEXT,
    OP_MERGE,
    OP_REMOVE,
    Change,
    Loadout,
    own_record,
)


class TestObservedSchema(SchemaTestBase):
    """Field suggestions come from what the base game actually does."""

    def test_children_are_collected_with_counts(self):
        tags = {f.tag: f for f in self.schema.children_of("/Values/Building")}
        self.assertEqual(tags["BuildingType"].count, 2)  # both assets
        self.assertEqual(tags["TerrainType"].count, 1)  # only the first
        self.assertEqual(tags["Radius"].count, 1)  # only the second

    def test_sample_values_are_recorded(self):
        tags = {f.tag: f for f in self.schema.children_of("/Values/Building")}
        self.assertIn("Factory", tags["BuildingType"].samples)

    def test_containers_are_distinguished_from_leaves(self):
        tags = {f.tag: f for f in self.schema.children_of("/Values")}
        self.assertTrue(tags["Cost"].is_container)
        tags = {f.tag: f for f in self.schema.children_of("/Values/Building")}
        self.assertFalse(tags["BuildingType"].is_container)

    def test_missing_children_excludes_what_the_asset_already_has(self):
        missing = self.schema.missing_children(
            "/Values/Building", ["BuildingType", "TerrainType"]
        )
        self.assertEqual([f.tag for f in missing], ["Radius"])

    def test_suggestions_are_ordered_by_how_common_they_are(self):
        fields = self.schema.children_of("/Values/Building")
        counts = [f.count for f in fields]
        self.assertEqual(counts, sorted(counts, reverse=True))

    def test_unknown_path_is_reported_as_unknown(self):
        self.assertFalse(self.schema.knows_path("/Values/NoSuchSection"))
        self.assertTrue(self.schema.knows_path("/Values/Building"))


class TestMostlyEmptyList(unittest.TestCase):
    """A list most assets ship empty is still a container (A11-118).

    The tree asks ``knows_path`` and the Add field dialog asks ``is_container``;
    the dialog used to want children on most assets, so it offered such a list
    as a value field that the tree then drew as a container.
    """

    ASSETS = """<AssetList><Assets>
      <Asset><Values><Standard><GUID>1</GUID></Standard><Building><Upgrades><Item><Level>2</Level></Item></Upgrades></Building></Values></Asset>
      <Asset><Values><Standard><GUID>2</GUID></Standard><Building><Upgrades /></Building></Values></Asset>
      <Asset><Values><Standard><GUID>3</GUID></Standard><Building><Upgrades /></Building></Values></Asset>
    </Assets></AssetList>"""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        assets = Path(tmp.name) / "assets.xml"
        assets.write_text(self.ASSETS, encoding="utf-8")
        db = Path(tmp.name) / "index.sqlite"
        index_mod.build_index(assets, db)
        index = index_mod.AssetIndex(db, assets)
        self.addCleanup(index.close)
        self.schema = index.schema

    def test_the_dialog_and_the_tree_agree_it_is_a_container(self):
        upgrades = {f.tag: f for f in self.schema.children_of("/Values/Building")}["Upgrades"]
        self.assertEqual((upgrades.count, upgrades.container_count), (3, 1))
        self.assertTrue(self.schema.knows_path("/Values/Building/Upgrades"))
        self.assertTrue(upgrades.is_container)


class TestTemplatesAndProperties(SchemaTestBase):
    def test_template_sections_come_from_templates_xml(self):
        self.assertEqual(
            self.schema.sections_for_template("FactoryBuilding7"),
            ["Standard", "Building", "Cost"],
        )
        self.assertEqual(sorted(self.schema.templates()), ["FactoryBuilding7", "Product"])

    def test_list_entry_shape_comes_from_real_entries(self):
        entry = self.schema.new_list_entry("/Values/Cost/Costs")
        self.assertEqual(entry.tag, "Item")
        self.assertEqual(sorted(c.tag for c in entry), ["Amount", "Ingredient"])

    def test_a_new_list_entry_carries_no_values(self):
        """The shape is the game's; a value borrowed from another asset is not
        the user's, whether it was observed or declared."""
        for path in ("/Values/Cost/Costs", "/Values/Empty/Slots"):
            entry = self.schema.new_list_entry(path)
            self.assertTrue(len(entry))
            self.assertEqual("".join(entry.itertext()).strip(), "", path)

    def test_list_entry_payload_has_no_stray_whitespace(self):
        """properties.xml is indented; the payload must not inherit that."""
        entry = self.schema.new_list_entry("/Values/Cost/Costs")
        text = etree.tostring(entry, pretty_print=True).decode()
        self.assertNotIn("\n\n", text)
        for line in text.splitlines():
            self.assertEqual(line, line.rstrip())

    def test_entry_shape_follows_real_entries_not_the_declaration(self):
        """properties.xml can declare a field nothing uses.

        Anno 117 declares <PerUnitLengths> for Maintenance/Maintenances, while
        every shipping entry there carries Product and Amount. What the game
        actually does wins.
        """
        entry = self.schema.new_list_entry("/Values/Cost/Costs")
        tags = [c.tag for c in entry]
        self.assertIn("Ingredient", tags)
        self.assertIn("Amount", tags)

    def test_rare_fields_are_left_out_of_a_new_entry(self):
        """Only what most real entries carry; the long tail is noise."""
        entry = self.schema.new_list_entry("/Values/Cost/Costs")
        self.assertNotIn("Rare", [c.tag for c in entry])

    def test_the_declaration_is_still_a_fallback(self):
        """When no real entry exists to learn from, use what the game declares."""
        entry = self.schema.new_list_entry("/Values/Empty/Slots")
        self.assertEqual([c.tag for c in entry], ["Declared"])

    def test_entry_tag_detects_a_list(self):
        self.assertEqual(self.schema.entry_tag_for("/Values/Cost/Costs"), "Item")
        # A section holding assorted fields is not a list.
        self.assertIsNone(self.schema.entry_tag_for("/Values/Building"))

    def test_a_block_is_held_to_the_shape_the_game_uses(self):
        """What a paste is checked against: every field inside, where it
        would land, is one the game has there."""
        entry = etree.fromstring(
            b"<Item><Ingredient>1</Ingredient><Rare>1</Rare></Item>"
        )
        self.assertEqual(self.schema.unknown_inside("/Values/Cost/Costs/Item", entry), "")
        # The same entry somewhere else fits nowhere inside.
        self.assertEqual(
            self.schema.unknown_inside("/Values/Building/Item", entry),
            "/Values/Building/Item/Ingredient",
        )
        # And a field nested deeper is found where it is.
        section = etree.fromstring(
            b"<Cost><Costs><Item><Amount>1</Amount><Bogus/></Item></Costs></Cost>"
        )
        self.assertEqual(
            self.schema.unknown_inside("/Values/Cost", section),
            "/Values/Cost/Costs/Item/Bogus",
        )

    def test_one_child_is_not_the_same_as_a_list(self):
        """Regression: <Hull> has exactly one <ModuleCount>, always.

        Judging "list" by "has a single child tag" made that a list, so the
        editor offered to append more of a child that must appear once -- and
        "add list entry" and "add field" became two routes to the same thing.
        """
        self.assertIsNone(self.schema.entry_tag_for("/Values/Modules/Hull"))
        self.assertFalse(self.schema.is_list("/Values/Modules/Hull"))
        self.assertTrue(self.schema.is_list("/Values/Cost/Costs"))

    def test_the_schema_object_is_held_not_rebuilt(self):
        """Its answers are cached, so handing out a fresh one throws them away.

        The editor asks the schema a question per node it draws; rebuilding on
        every access turned opening a large asset into a second and a half.
        """
        self.assertIs(self.index.schema, self.index.schema)

    def test_entry_tags_are_read_off_the_data(self):
        """Which tag names a list entry is observed, not assumed.

        Anno 117 uses <Item> alone; Anno 1800 uses <Item> and <Entry>. Assuming
        the obvious name would have been wrong for one of the two games.
        """
        self.assertEqual(self.schema.repeating_tags(), {"Item"})

    def test_schema_survives_without_the_optional_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "assets.xml"
            path.write_text(SCHEMA_ASSETS, encoding="utf-8")
            db = Path(tmp) / "i.sqlite"
            index_mod.build_index(path, db)
            index = index_mod.AssetIndex(db, path)
            try:
                # Observed schema still works; template list is simply empty.
                self.assertTrue(index.schema.children_of("/Values/Building"))
                self.assertEqual(index.schema.templates(), [])
                self.assertFalse(index.schema.has_templates)
            finally:
                index.close()


class TestStructuralOps(unittest.TestCase):
    """Rendering each op in each game's dialect."""

    def _loadout(self, game, change):
        loadout = Loadout(own=own_record(name="Ops"), game=game)
        loadout.set_change(change)
        return loadout

    def _render(self, game, change):
        xml = generator.build_modops_xml(
            self._loadout(game, change),
        )
        return etree.fromstring(xml.encode("utf-8")).find("ModOp")

    def test_add_appends_the_payload(self):
        change = Change(
            guid="1000", asset_label="alpha", path="/Values/Cost/Costs", tag="Costs",
            op=OP_ADD, payload="<Item><Ingredient>7</Ingredient></Item>",
        )
        op = self._render(games.ANNO_1800, change)
        self.assertEqual(op.get("Type"), "add")
        self.assertEqual(op.get("Path"), "/Values/Cost/Costs")
        self.assertEqual(op[0].tag, "Item")

        op = self._render(games.ANNO_117, change)
        self.assertEqual(op.get("Add"), "@1000/Cost/Costs")
        self.assertIsNone(op.get("Type"))

    def test_remove_has_no_body(self):
        change = Change(
            guid="1000", asset_label="alpha",
            path="/Values/Cost/Costs/Item[Ingredient='1010196']", tag="Item", op=OP_REMOVE,
        )
        op = self._render(games.ANNO_1800, change)
        self.assertEqual(op.get("Type"), "remove")
        self.assertEqual(len(op), 0)
        self.assertFalse((op.text or "").strip())

        op = self._render(games.ANNO_117, change)
        self.assertEqual(op.get("Remove"), "@1000/Cost/Costs/Item[Ingredient='1010196']")
        self.assertEqual(len(op), 0)

    def test_merge_uses_the_shorthand_on_117(self):
        change = Change(
            guid="1000", asset_label="alpha", path="/Values/Building", tag="Building",
            op=OP_MERGE, payload="<Radius>4</Radius>",
        )
        self.assertEqual(self._render(games.ANNO_117, change).get("Merge"), "@1000/Building")
        self.assertEqual(self._render(games.ANNO_1800, change).get("Type"), "merge")

    def test_new_asset_uses_the_classic_form_in_both_games(self):
        """Real 117 mods use the classic attribute form for addNextSibling even
        though they use the shorthand elsewhere."""
        change = Change(
            guid="1000", asset_label="new thing", path="", tag="Asset", op=OP_ADD_NEXT,
            payload="<Asset><BaseAssetGUID>1000</BaseAssetGUID><Values><Standard>"
                    "<GUID>1337000000</GUID></Standard></Values></Asset>",
        )
        for game in (games.ANNO_1800, games.ANNO_117):
            op = self._render(game, change)
            self.assertEqual(op.get("Type"), "addNextSibling", game)
            self.assertEqual(op.get("GUID"), "1000", game)
            self.assertIsNone(op.get("Path"), game)  # anchors on the asset itself
            self.assertEqual(op[0].tag, "Asset", game)

    def _new_asset(self, guid):
        return Change(
            guid="1000", asset_label="new thing  (new asset)", path="", tag="Asset",
            op=OP_ADD_NEXT, summary="create asset %s (new thing)" % guid,
            payload="<Asset><BaseAssetGUID>1000</BaseAssetGUID><Values><Standard>"
                    "<GUID>%s</GUID></Standard></Values></Asset>" % guid,
        )

    def test_a_new_asset_is_written_beside_the_asset_it_is_put_beside(self):
        """In that asset's block, and first in it where its name sorts first;
        with nothing of the Studio's over it, as there was once a heading
        that had to be told whose GUID to give."""
        loadout = self._loadout(games.ANNO_1800, self._new_asset("1337000000"))
        loadout.set_change(Change(
            guid="1000", asset_label="omega", path="/Values/Building", tag="Building",
            op=OP_MERGE, payload="<Radius>4</Radius>",
        ))
        xml = generator.build_modops_xml(loadout)
        self.assertIn('<ModOp Type="addNextSibling" GUID="1000">', xml)
        self.assertLess(xml.index("addNextSibling"), xml.index('Type="merge"'))
        self.assertNotIn("<!--", xml)

    def test_payload_is_identical_across_games(self):
        change = Change(
            guid="1000", asset_label="alpha", path="/Values/Cost/Costs", tag="Costs",
            op=OP_ADD, payload="<Item><Ingredient>7</Ingredient><Amount>3</Amount></Item>",
        )
        bodies = [
            etree.tostring(self._render(g, change)[0])
            for g in (games.ANNO_1800, games.ANNO_117)
        ]
        self.assertEqual(bodies[0], bodies[1])

    def test_replace_still_renders_as_before(self):
        change = Change(
            guid="1000", asset_label="alpha", path="/Values/Building/BuildingType",
            tag="BuildingType", old_value="Factory", new_value="Residence",
        )
        op = self._render(games.ANNO_1800, change)
        self.assertEqual(op.get("Type"), "replace")
        self.assertEqual(op[0].text, "Residence")


class TestChangeIdentity(unittest.TestCase):
    """Structural ops need different identity rules than replace."""

    def test_two_adds_to_the_same_list_are_separate_changes(self):
        loadout = Loadout()
        for ingredient in ("1", "2"):
            loadout.set_change(
                Change(
                    guid="1000", asset_label="a", path="/Values/Cost/Costs", tag="Costs",
                    op=OP_ADD, payload="<Item><Ingredient>%s</Ingredient></Item>" % ingredient,
                )
            )
        self.assertEqual(len(loadout), 2)

    def test_two_replaces_of_the_same_value_are_one_change(self):
        loadout = Loadout()
        for value in ("1", "2"):
            loadout.set_change(
                Change(
                    guid="1000", asset_label="a", path="/Values/X/Y", tag="Y",
                    old_value="0", new_value=value,
                )
            )
        self.assertEqual(len(loadout), 1)

    def test_a_removal_and_a_replace_can_coexist_at_one_path(self):
        loadout = Loadout()
        loadout.set_change(
            Change(guid="1000", asset_label="a", path="/Values/X/Y", tag="Y",
                   old_value="0", new_value="1")
        )
        loadout.set_change(
            Change(guid="1000", asset_label="a", path="/Values/X/Y", tag="Y", op=OP_REMOVE)
        )
        self.assertEqual(len(loadout), 2)

    def test_structural_ops_are_never_noops(self):
        change = Change(guid="1", asset_label="a", path="/Values/X", tag="X", op=OP_REMOVE)
        self.assertFalse(change.is_noop)
        self.assertTrue(change.is_structural)

    def test_ops_survive_a_loadout_round_trip(self):
        loadout = Loadout()
        loadout.set_change(
            Change(guid="1000", asset_label="a", path="/Values/Cost/Costs", tag="Costs",
                   op=OP_ADD, payload="<Item><Ingredient>7</Ingredient></Item>",
                   summary="add entry")
        )
        loadout.set_change(
            Change(guid="1000", asset_label="a", path="", tag="Asset", op=OP_ADD_NEXT,
                   payload="<Asset><Values /></Asset>", summary="new asset")
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "p.annoload"
            loadout.save(path)
            loaded = Loadout.load(path)
        self.assertEqual(len(loaded), 2)
        ops = sorted(c.op for c in loaded)
        self.assertEqual(ops, [OP_ADD, OP_ADD_NEXT])
        self.assertTrue(all(c.payload for c in loaded))


class TestOpVerification(SchemaTestBase):
    """Each op has its own notion of "would this actually apply?"."""

    def _verify(self, change):
        loadout = Loadout()
        loadout.set_change(change)
        return generator.verify_changes(self.index, loadout)[0]

    def test_add_needs_exactly_one_parent(self):
        good = self._verify(
            Change(guid="1000", asset_label="a", path="/Values/Cost/Costs", tag="Costs",
                   op=OP_ADD, payload="<Item />")
        )
        self.assertTrue(good.ok)

        ambiguous = self._verify(
            Change(guid="1000", asset_label="a", path="/Values/Cost/Costs/Item", tag="Item",
                   op=OP_ADD, payload="<Amount>1</Amount>")
        )
        self.assertFalse(ambiguous.ok)
        self.assertIn("2 nodes", ambiguous.message)

    def test_remove_must_find_its_target(self):
        gone = self._verify(
            Change(guid="1000", asset_label="a", path="/Values/Nope", tag="Nope", op=OP_REMOVE)
        )
        self.assertFalse(gone.ok)
        self.assertIn("matches nothing", gone.message)

    def test_a_new_asset_may_not_reuse_an_existing_guid(self):
        clash = self._verify(
            Change(guid="1000", asset_label="new", path="", tag="Asset", op=OP_ADD_NEXT,
                   payload="<Asset><Values><Standard><GUID>1001</GUID></Standard>"
                           "</Values></Asset>")
        )
        self.assertFalse(clash.ok)
        self.assertIn("already belongs", clash.message)

        fresh = self._verify(
            Change(guid="1000", asset_label="new", path="", tag="Asset", op=OP_ADD_NEXT,
                   payload="<Asset><Values><Standard><GUID>1337000000</GUID></Standard>"
                           "</Values></Asset>")
        )
        self.assertTrue(fresh.ok)

    def test_a_new_asset_without_a_guid_is_rejected(self):
        result = self._verify(
            Change(guid="1000", asset_label="new", path="", tag="Asset", op=OP_ADD_NEXT,
                   payload="<Asset><Values /></Asset>")
        )
        self.assertFalse(result.ok)
        self.assertIn("no GUID", result.message)

    def test_replace_verification_is_unchanged(self):
        drifted = self._verify(
            Change(guid="1000", asset_label="a", path="/Values/Building/BuildingType",
                   tag="BuildingType", old_value="NotThis", new_value="X")
        )
        self.assertFalse(drifted.ok)


class TestNewAssetDialog(SchemaTestBase):
    """The dialog builds a valid new-asset ModOp and refuses invalid ones."""

    def setUp(self):
        import os

        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication

        self.app = QApplication.instance() or QApplication([])
        from anno_modding_studio.ui.new_asset_dialog import NewAssetDialog

        self.loadout = Loadout()
        self.dialog = NewAssetDialog(self.index, self.loadout)

    def tearDown(self):
        self.dialog.deleteLater()

    def test_constructing_it_does_not_error(self):
        """Regression: populating the template list used to fire _sync() before
        the rest of the widgets existed."""
        self.assertTrue(self.dialog.guid_edit.text())
        self.assertEqual(sorted(self.dialog.template_combo.itemText(i)
                                for i in range(self.dialog.template_combo.count())),
                         ["FactoryBuilding7", "Product"])

    def test_it_suggests_a_free_guid_from_the_personal_range(self):
        """No author name is set, so the register's personal-use range."""
        guid = self.dialog.guid_edit.text()
        self.assertTrue(guid.isdigit())
        self.assertFalse(self.index.guid_exists(guid))
        self.assertEqual(guid, "2001000000")
        self.assertEqual(self.dialog._warning(), "")

    def test_copying_an_asset_uses_baseassetguid(self):
        self.dialog._base_row = self.index.asset_row("1000")
        self.dialog.name_edit.setText("my_copy")
        self.dialog._sync()
        self.assertEqual(self.dialog._problem(), "")

        self.dialog._accept()
        change = self.dialog.change
        self.assertEqual(change.op, OP_ADD_NEXT)
        self.assertEqual(change.guid, "1000")   # anchored on the base asset
        self.assertEqual(change.path, "")
        asset = etree.fromstring(change.payload.encode("utf-8"))
        self.assertEqual(asset.findtext("BaseAssetGUID"), "1000")
        self.assertIsNone(asset.find("Template"))  # inherited, not restated
        self.assertEqual(asset.findtext("Values/Standard/Name"), "my_copy")

    def test_either_kind_of_copy_suggests_a_name_from_the_original(self):
        """Run 2 saw the Name left empty for *Copy its values once* and
        filled in for *Follow the original* (H2-27): whichever kind is
        chosen, and before or after the asset, the name is suggested."""
        from unittest import mock

        from anno_modding_studio.ui import new_asset_dialog

        for once, kind_first in ((False, False), (True, True), (True, False)):
            dialog = new_asset_dialog.NewAssetDialog(self.index, self.loadout)
            self.addCleanup(dialog.deleteLater)
            if kind_first:
                dialog.once_radio.setChecked(once)
            picker = mock.Mock(**{"exec.return_value": 1})
            picker.chosen_guid = "1000"
            with mock.patch.object(new_asset_dialog, "AssetPickerDialog", return_value=picker):
                dialog._pick_base()
            dialog.once_radio.setChecked(once)
            self.assertEqual(dialog.name_edit.text(), "copy of alpha", (once, kind_first))

    def test_it_starts_from_the_asset_the_editor_has_open(self):
        """Found first, then New asset: the asset was not to be looked for
        again in the picker."""
        from anno_modding_studio.ui.new_asset_dialog import NewAssetDialog

        dialog = NewAssetDialog(self.index, self.loadout, base_guid="1001")
        self.addCleanup(dialog.deleteLater)
        self.assertTrue(dialog.copy_radio.isChecked())
        self.assertEqual(dialog._base_row.guid, "1001")
        self.assertIn("beta", dialog.base_label.text())
        self.assertEqual(dialog.name_edit.text(), "copy of beta")
        self.assertEqual(dialog.template_combo.currentText(), "FactoryBuilding7")
        self.assertEqual(dialog._problem(), "")
        self.assertIn("<BaseAssetGUID>1001</BaseAssetGUID>", dialog.preview.toPlainText())

    def test_its_template_is_found_through_what_it_inherits(self):
        """A new asset of the loadout's, and one that copies it: the copy
        names no template, so the one it inherits is offered."""
        from anno_modding_studio.core.workspace import Workspace
        from anno_modding_studio.ui.new_asset_dialog import NewAssetDialog

        for guid, payload in (
            ("1337000000", "<Template>Product</Template>"),
            ("1337000001", "<BaseAssetGUID>1337000000</BaseAssetGUID>"),
        ):
            self.loadout.set_change(Change(
                guid="1000", asset_label="new", path="", tag="Asset", op=OP_ADD_NEXT,
                payload="<Asset>%s<Values><Standard><GUID>%s</GUID><Name>n%s</Name>"
                        "</Standard></Values></Asset>" % (payload, guid, guid),
            ))
        workspace = Workspace(self.index, self.loadout)
        for guid in ("1337000000", "1337000001"):
            dialog = NewAssetDialog(workspace, self.loadout, base_guid=guid)
            self.addCleanup(dialog.deleteLater)
            self.assertEqual(dialog._base_row.guid, guid)
            self.assertEqual(dialog.template_combo.currentText(), "Product", guid)
            dialog.scratch_radio.setChecked(True)
            self.assertIn("<Template>Product</Template>", dialog.preview.toPlainText())

    def test_an_asset_that_is_not_there_leaves_it_as_it_was(self):
        """The editor can hold a GUID that is gone: a new asset undone."""
        from anno_modding_studio.ui.new_asset_dialog import NewAssetDialog

        dialog = NewAssetDialog(self.index, self.loadout, base_guid="424242")
        self.addCleanup(dialog.deleteLater)
        self.assertIsNone(dialog._base_row)
        self.assertEqual(dialog.name_edit.text(), "")
        self.assertEqual(dialog.template_combo.currentIndex(), 0)

    def test_a_one_time_copy_writes_the_originals_values_and_no_base(self):
        self.dialog._base_row = self.index.asset_row("1000")
        self.dialog.once_radio.setChecked(True)
        self.dialog.name_edit.setText("my_copy")
        self.dialog._sync()
        self.assertEqual(self.dialog._problem(), "")
        self.assertIn("<TerrainType>Land</TerrainType>", self.dialog.preview.toPlainText())

        self.dialog._accept()
        change = self.dialog.change
        self.assertEqual(change.op, OP_ADD_NEXT)
        self.assertEqual(change.guid, "1000")   # still put beside the original
        asset = etree.fromstring(change.payload.encode("utf-8"))
        self.assertIsNone(asset.find("BaseAssetGUID"))  # nothing ties it back
        self.assertEqual(asset.findtext("Template"), "FactoryBuilding7")
        self.assertEqual(asset.findtext("Values/Standard/GUID"), self.dialog.guid_edit.text())
        self.assertEqual(asset.findtext("Values/Standard/Name"), "my_copy")
        self.assertEqual(asset.findtext("Values/Building/TerrainType"), "Land")
        self.assertEqual(
            [item.findtext("Amount") for item in asset.iterfind("Values/Cost/Costs/Item")],
            ["5", "8"],
        )
        # The original is as it was.
        self.assertEqual(self.index.asset_element("1000").findtext("Values/Standard/Name"), "alpha")

    def test_a_one_time_copy_takes_the_asset_as_this_mod_leaves_it(self):
        """With a loadout over the index, as the window hands it one: what is
        copied is what the editor shows, a change of your own included."""
        from anno_modding_studio.core.workspace import Workspace
        from anno_modding_studio.ui.new_asset_dialog import NewAssetDialog

        self.loadout.set_change(Change(
            guid="1000", asset_label="alpha", path="/Values/Building/TerrainType",
            tag="TerrainType", old_value="Land", new_value="Water",
        ))
        dialog = NewAssetDialog(Workspace(self.index, self.loadout), self.loadout)
        self.addCleanup(dialog.deleteLater)
        dialog._base_row = dialog.index.asset_row("1000")
        dialog.once_radio.setChecked(True)
        dialog.name_edit.setText("my_copy")
        dialog._accept()
        asset = etree.fromstring(dialog.change.payload.encode("utf-8"))
        self.assertEqual(asset.findtext("Values/Building/TerrainType"), "Water")
        self.assertEqual(asset.findtext("Values/Standard/Name"), "my_copy")

    def test_a_one_time_copy_of_an_asset_this_mod_changed_says_what_it_takes(self):
        """Nothing said so, and the copy came out with the mod's own values
        where the game's were expected."""
        from anno_modding_studio.core.workspace import Workspace
        from anno_modding_studio.ui.new_asset_dialog import NewAssetDialog

        self.loadout.set_change(Change(
            guid="1000", asset_label="alpha", path="/Values/Building/TerrainType",
            tag="TerrainType", old_value="Land", new_value="Water",
        ))
        dialog = NewAssetDialog(Workspace(self.index, self.loadout), self.loadout)
        self.addCleanup(dialog.deleteLater)
        dialog._base_row = dialog.index.asset_row("1000")
        dialog._sync()
        # Following copies nothing, so there is nothing to say.
        self.assertTrue(dialog.copy_note.isHidden())

        dialog.once_radio.setChecked(True)
        self.assertFalse(dialog.copy_note.isHidden())
        self.assertEqual(
            dialog.copy_note.text(),
            "The copy takes alpha as this mod leaves it, with the changes made to it so far.",
        )

        # An asset the mod has not touched is the game's own, and is not remarked on.
        dialog._base_row = dialog.index.asset_row("1001")
        dialog._sync()
        self.assertTrue(dialog.copy_note.isHidden())

    def _choose_base(self, guid):
        """Choose asset…, with the picker answering for itself."""
        from unittest import mock

        from PySide6.QtWidgets import QDialog

        from anno_modding_studio.ui import new_asset_dialog

        def chosen(picker):
            picker.chosen_guid = guid
            return int(QDialog.DialogCode.Accepted)

        with mock.patch.object(new_asset_dialog.AssetPickerDialog, "exec", chosen):
            self.dialog._pick_base()

    def _click_into(self, box):
        """A click that brings the focus: the focus arrives, then the press."""
        from PySide6.QtCore import QEvent, Qt
        from PySide6.QtGui import QFocusEvent
        from PySide6.QtTest import QTest

        self.app.sendEvent(
            box, QFocusEvent(QEvent.Type.FocusIn, Qt.FocusReason.MouseFocusReason)
        )
        QTest.mouseClick(box, Qt.MouseButton.LeftButton)

    def test_typing_replaces_the_name_suggested_for_a_copy(self):
        """Clicking into "copy of alpha" put the cursor in it, and what was
        typed was added to it."""
        from PySide6.QtTest import QTest

        self._choose_base("1000")
        box = self.dialog.name_edit
        self.assertEqual(box.text(), "copy of alpha")
        self._click_into(box)
        self.assertEqual(box.selectedText(), "copy of alpha")
        QTest.keyClicks(box, "my_hut")
        self.assertEqual(box.text(), "my_hut")

        # Its own now: a click puts the cursor in it and selects nothing.
        self._click_into(box)
        self.assertEqual(box.selectedText(), "")

    def test_a_name_typed_by_hand_is_not_written_over_or_selected(self):
        box = self.dialog.name_edit
        box.setText("my_hut")
        self._choose_base("1000")
        self.assertEqual(box.text(), "my_hut")
        self._click_into(box)
        self.assertEqual(box.selectedText(), "")

    def test_a_suggested_name_follows_the_asset_chosen(self):
        """Left at "copy of alpha", it went on saying so of another asset."""
        self._choose_base("1000")
        self._choose_base("1001")
        self.assertEqual(self.dialog.name_edit.text(), "copy of beta")

    def test_the_kind_of_copy_is_only_asked_of_a_copy(self):
        self.assertTrue(self.dialog.once_radio.isEnabled())
        self.dialog.scratch_radio.setChecked(True)
        self.assertFalse(self.dialog.follow_radio.isEnabled())
        self.assertFalse(self.dialog.once_radio.isEnabled())

    def test_from_scratch_uses_the_templates_section_list(self):
        self.dialog.scratch_radio.setChecked(True)
        self.dialog.template_combo.setCurrentText("FactoryBuilding7")
        self.dialog.name_edit.setText("brand_new")
        self.dialog._sync()
        self.assertEqual(self.dialog._problem(), "")

        self.dialog._accept()
        asset = etree.fromstring(self.dialog.change.payload.encode("utf-8"))
        self.assertEqual(asset.findtext("Template"), "FactoryBuilding7")
        sections = [c.tag for c in asset.find("Values")]
        self.assertEqual(sections, ["Standard", "Building", "Cost"])

    def test_the_template_note_goes_with_from_scratch(self):
        """Back to copying, it went on describing the template under the
        disabled combo."""
        self.dialog.scratch_radio.setChecked(True)
        self.dialog.template_combo.setCurrentText("FactoryBuilding7")
        self.assertIn("FactoryBuilding7", self.dialog.template_note.text())
        self.dialog.copy_radio.setChecked(True)
        self.assertEqual(self.dialog.template_note.text(), "")

    def test_a_taken_guid_is_refused(self):
        self.dialog._base_row = self.index.asset_row("1000")
        self.dialog.name_edit.setText("x")
        self.dialog.guid_edit.setText("1001")   # already a base game asset
        self.dialog._sync()
        self.assertIn("already used", self.dialog._problem())
        self.assertFalse(self.dialog.create_button.isEnabled())

    def test_a_non_numeric_guid_is_refused(self):
        self.dialog.guid_edit.setText("abc")
        self.assertIn("plain number", self.dialog._problem())

    def test_a_nameless_asset_is_refused(self):
        self.dialog._base_row = self.index.asset_row("1000")
        self.dialog.name_edit.setText("")
        self.assertIn("name", self.dialog._problem())


class TestSiblingModGuidCollisions(SchemaTestBase):
    """A GUID another local mod already claims should be caught at creation,
    not discovered later when generating this one.

    Detectable only by looking at other mods this Studio happens to know the
    folder for -- a mod on someone else's machine is unreachable no matter
    what, so this is a courtesy check, not a guarantee.
    """

    def setUp(self):
        import os
        import tempfile

        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication

        self.app = QApplication.instance() or QApplication([])
        from anno_modding_studio.ui.new_asset_dialog import NewAssetDialog

        self._mods_tmp = tempfile.TemporaryDirectory()
        self.mods_folder = Path(self._mods_tmp.name)
        self.asset_relpath = Path("data/config/export/main/asset/assets.xml")
        self.loadout = Loadout()  # defaults to Anno 1800
        self._DialogClass = NewAssetDialog

    def tearDown(self):
        self._mods_tmp.cleanup()

    def _write_sibling_mod(self, folder_name: str, guid: str) -> Path:
        mod_dir = self.mods_folder / folder_name
        asset_file = mod_dir / self.asset_relpath
        asset_file.parent.mkdir(parents=True)
        asset_file.write_text(
            '<ModOps><ModOp Type="addNextSibling" GUID="1000">'
            "<Asset><Values><Standard><GUID>%s</GUID><Name>theirs</Name>"
            "</Standard></Values></Asset></ModOp></ModOps>" % guid,
            encoding="utf-8",
        )
        return mod_dir

    def _dialog(self, export_target=None):
        from anno_modding_studio.core import discovery

        return self._DialogClass(
            self.index,
            self.loadout,
            mods=discovery.mods_in(self.mods_folder, discovery.WHERE_MODS),
            export_target=export_target,
        )

    def test_a_guid_another_mod_creates_is_refused(self):
        self._write_sibling_mod("[Gameplay] Someone Elses Mod", "1337000000")
        dialog = self._dialog()
        try:
            dialog._base_row = self.index.asset_row("1000")
            dialog.name_edit.setText("mine")
            dialog.guid_edit.setText("1337000000")
            problem = dialog._problem()
            self.assertIn("already used by another mod", problem)
            self.assertIn("Someone Elses Mod", problem)
        finally:
            dialog.deleteLater()

    def test_the_suggested_guid_skips_ones_other_mods_already_claim(self):
        """The one it would suggest with no other mod there, taken by one:
        a GUID it would never have suggested proves nothing."""
        dialog = self._dialog()
        try:
            first = dialog.guid_edit.text()
        finally:
            dialog.deleteLater()
        self.assertTrue(first.isdigit(), first)
        self._write_sibling_mod("[Gameplay] Taken", first)
        dialog = self._dialog()
        try:
            self.assertTrue(dialog.guid_edit.text().isdigit())
            self.assertNotEqual(dialog.guid_edit.text(), first)
        finally:
            dialog.deleteLater()

    def test_a_field_edit_is_never_mistaken_for_a_new_asset_guid(self):
        """Only a real ``<Asset>``-wrapped payload counts -- a ModOp editing an
        existing field is not a claim on the GUID it happens to sit near."""
        mod_dir = self.mods_folder / "[Balancing] Just Edits"
        asset_file = mod_dir / self.asset_relpath
        asset_file.parent.mkdir(parents=True)
        asset_file.write_text(
            '<ModOps><ModOp Type="replace" GUID="1000" Path="/Values/Cost/Costs">'
            "<Costs><Ingredient>1337000000</Ingredient></Costs>"
            "</ModOp></ModOps>",
            encoding="utf-8",
        )
        dialog = self._dialog()
        try:
            dialog._base_row = self.index.asset_row("1000")
            dialog.name_edit.setText("mine")
            dialog.guid_edit.setText("1337000000")
            self.assertEqual(dialog._problem(), "")
        finally:
            dialog.deleteLater()

    def test_this_loadouts_own_previous_export_is_not_flagged_against_itself(self):
        """Regression risk: re-opening New asset after already exporting once
        must not treat the mod's own earlier output as someone else's mod."""
        own_export = self.mods_folder / "[Gameplay] My Own Mod"
        self._write_sibling_mod("[Gameplay] My Own Mod", "1337000000")
        dialog = self._dialog(export_target=own_export)
        try:
            dialog._base_row = self.index.asset_row("1000")
            dialog.name_edit.setText("mine")
            dialog.guid_edit.setText("1337000000")
            self.assertEqual(dialog._problem(), "")
        finally:
            dialog.deleteLater()

    def test_nor_is_its_own_build_somewhere_else_on_this_pc(self):
        """The Play build sits in the library, not at the Export tab's
        destination, and was "another mod on this PC". It is the loadout's
        by its ModID, as the changes list tells them apart."""
        import json

        build = self._write_sibling_mod("[Gameplay] My Own Mod", "1337000000")
        (build / "modinfo.json").write_text(json.dumps({"ModID": "my-own-mod"}), encoding="utf-8")
        theirs = self._write_sibling_mod("[Gameplay] Theirs", "1337000001")
        (theirs / "modinfo.json").write_text(json.dumps({"ModID": "theirs"}), encoding="utf-8")
        self.loadout.make_own_mod()
        self.loadout.own.mod_id = "my-own-mod"
        dialog = self._dialog()
        try:
            dialog._base_row = self.index.asset_row("1000")
            dialog.name_edit.setText("mine")
            dialog.guid_edit.setText("1337000000")
            self.assertEqual(dialog._problem(), "")
            dialog.guid_edit.setText("1337000001")
            self.assertIn("already used by another mod", dialog._problem())
        finally:
            dialog.deleteLater()

    def test_no_mods_folder_means_no_collision_check_and_no_crash(self):
        from anno_modding_studio.ui.new_asset_dialog import NewAssetDialog

        dialog = NewAssetDialog(self.index, self.loadout)  # mods=None
        try:
            self.assertEqual(dialog._sibling_guids, {})
        finally:
            dialog.deleteLater()

    def test_an_unreadable_sibling_file_is_skipped_not_fatal(self):
        mod_dir = self.mods_folder / "[Gameplay] Corrupt Mod"
        asset_file = mod_dir / self.asset_relpath
        asset_file.parent.mkdir(parents=True)
        asset_file.write_bytes(b"not xml at all {[")
        self._write_sibling_mod("[Gameplay] Fine Mod", "1337000005")

        dialog = self._dialog()
        try:
            self.assertEqual(
                dialog._sibling_guids,
                {"1337000005": "[Gameplay] Fine Mod, in your mods folder"},
            )
        finally:
            dialog.deleteLater()

    def test_an_asset_the_loadout_keeps_as_written_is_taken(self):
        """A loaded mod's op carried verbatim -- a group kept whole, an op
        the Studio could not read -- still goes out in the export, and the
        asset inside it with it."""
        from anno_modding_studio.core import guidranges
        from anno_modding_studio.core.changes import Op, Unread
        from anno_modding_studio.ui.new_asset_dialog import PERSONAL_RANGE

        start = (guidranges.load(self.loadout.game).personal() or PERSONAL_RANGE).start
        self.loadout.ops.append(Op(source=(
            '<Group Condition="#SomeMod"><ModOp Type="addNextSibling" GUID="1000">'
            "<Asset><Values><Standard><GUID>%d</GUID></Standard></Values></Asset>"
            "</ModOp></Group>" % start
        )))
        self.loadout.unread.append(Unread(source=(
            '<ModOp Type="replace" GUID="1000" Path="/Values/Standard/GUID">'
            "<GUID>%d</GUID></ModOp>" % (start + 1)
        )))
        dialog = self._dialog()
        try:
            self.assertEqual(dialog.guid_edit.text(), str(start + 2))
            dialog._base_row = self.index.asset_row("1000")
            dialog.name_edit.setText("mine")
            for guid in (str(start), str(start + 1)):
                dialog.guid_edit.setText(guid)
                self.assertEqual(
                    dialog._problem(), "This loadout already creates GUID %s." % guid
                )
        finally:
            dialog.deleteLater()


class TestGuidsClaimedOnThisPC(unittest.TestCase):
    """What :func:`discovery.guid_owners` finds, and where it looks."""

    RELPATH = Path("data/config/export/main/asset/assets.xml")

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.mods = self.root / "mods"
        self.mods.mkdir()

    def tearDown(self):
        self._tmp.cleanup()

    def _patch(self, mod_dir: Path, body: str, relpath=None) -> Path:
        path = mod_dir / (relpath or self.RELPATH)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("<ModOps>%s</ModOps>" % body, encoding="utf-8")
        return path

    @staticmethod
    def _asset(guid: str) -> str:
        return "<Asset><Values><Standard><GUID>%s</GUID></Standard></Values></Asset>" % guid

    def _scan(self, mods=None, relpath=None, exclude=None):
        from anno_modding_studio.core import discovery

        return {guid: owners[0].label for guid, owners in discovery.guid_owners(
            mods if mods is not None else discovery.mods_in(self.mods, discovery.WHERE_MODS),
            relpath or self.RELPATH,
            exclude=exclude,
        ).items()}

    def test_every_way_of_adding_an_asset_counts(self):
        self._patch(self.mods / "Adds", "".join((
            '<ModOp Type="addNextSibling" GUID="1000">%s</ModOp>' % self._asset("1"),
            '<ModOp Type="addPrevSibling" GUID="1000">%s</ModOp>' % self._asset("2"),
            '<ModOp Type="add" Path="/AssetList/Groups/Group/Assets">%s</ModOp>' % self._asset("3"),
            '<ModOp Type="replace" GUID="1000">%s</ModOp>' % self._asset("4"),
            '<Group><ModOp Type="addNextSibling" GUID="1000">%s</ModOp></Group>' % self._asset("5"),
            '<ModOp Type="addNextSibling" GUID="1000" Skip="1">%s</ModOp>' % self._asset("6"),
            # An existing asset renumbered, three ways of spelling it.
            '<ModOp Type="replace" GUID="1000" Path="/Values/Standard/GUID"><GUID>7</GUID></ModOp>',
            '<ModOp Merge="Standard" GUID="1001"><GUID>8</GUID></ModOp>',
            '<ModOp Type="replace" GUID="1002" Path="/Values/Standard">'
            "<Standard><GUID>9</GUID></Standard></ModOp>",
        )))
        self.assertEqual(sorted(self._scan(), key=int), [str(n) for n in range(1, 10)])

    def test_an_asset_written_straight_under_modops_counts(self):
        """How Anno 117 mods add one: no op round it."""
        relpath = games.get(games.ANNO_117).asset_relpath
        self._patch(self.mods / "Roman", self._asset("117000001"), relpath)
        self.assertEqual(self._scan(relpath=relpath), {"117000001": "Roman, in your mods folder"})

    def test_field_edits_near_a_guid_do_not(self):
        self._patch(self.mods / "Edits", "".join((
            '<ModOp Type="replace" GUID="1000" Path="/Values/Cost/Costs">'
            "<Costs><Item><Ingredient>1337</Ingredient></Item></Costs></ModOp>",
            '<ModOp Type="add" GUID="1000" Path="/Values/Trade/Items">'
            "<Item><GUID>1338</GUID></Item></ModOp>",
            '<ModOp Type="replace" GUID="1000" Path="/Values/Building/GUID"><GUID>1339</GUID></ModOp>',
        )))
        self.assertEqual(self._scan(), {})

    def test_included_files_are_read(self):
        mod = self.mods / "Split Up"
        patch = self._patch(mod, '<Include File="./parts/one.include.xml"/>'
                                 '<Group><Include File="/extra/two.xml" Condition="#Other"/></Group>')
        (patch.parent / "parts").mkdir()
        (patch.parent / "parts" / "one.include.xml").write_text(
            '<ModOps><ModOp Type="addNextSibling" GUID="1000">%s</ModOp>'
            '<Include File="three.xml"/></ModOps>' % self._asset("11"),
            encoding="utf-8",
        )
        (patch.parent / "parts" / "three.xml").write_text(
            '<ModOps><ModOp Type="addNextSibling" GUID="1000">%s</ModOp>'
            '<Include File="../../../../../../../Split Up/data/config/export/main/asset/assets.xml"/>'
            "</ModOps>" % self._asset("13"),
            encoding="utf-8",
        )
        (mod / "extra").mkdir()
        (mod / "extra" / "two.xml").write_text(
            '<ModOps><ModOp Type="addNextSibling" GUID="1000">%s</ModOp></ModOps>' % self._asset("12"),
            encoding="utf-8",
        )
        self.assertEqual(sorted(self._scan()), ["11", "12", "13"])

    def test_a_mod_bundled_inside_another_is_found_and_named_as_itself(self):
        host = self.mods / "[Shared] Host"
        self._patch(host, "")
        self._patch(host / "shared" / "[Shared] Piece",
                    '<ModOp Type="addNextSibling" GUID="1000">%s</ModOp>' % self._asset("21"))
        self.assertEqual(self._scan(), {"21": "[Shared] Piece, in your mods folder"})

    def test_mod_io_numbered_folders_are_read_through(self):
        from anno_modding_studio.core import discovery

        downloads = self.root / "modio" / "4169" / "mods"
        self._patch(downloads / "123456" / "[Gameplay] Downloaded",
                    '<ModOp Type="addNextSibling" GUID="1000">%s</ModOp>' % self._asset("31"))
        found = self._scan(mods=discovery.mods_in(downloads, discovery.WHERE_MODIO))
        self.assertEqual(found, {"31": "[Gameplay] Downloaded, in mod.io's downloads"})

    def test_a_mod_reached_twice_is_read_once_under_the_first(self):
        from anno_modding_studio.core import discovery

        self._patch(self.mods / "Twice",
                    '<ModOp Type="addNextSibling" GUID="1000">%s</ModOp>' % self._asset("41"))
        found = self._scan(mods=[
            (self.mods / "Twice", discovery.WHERE_MODS),
            (self.mods / "Twice", discovery.WHERE_LIBRARY),
        ])
        self.assertEqual(found, {"41": "Twice, in your mods folder"})

    def test_the_export_target_is_left_out_nested_or_not(self):
        own = self.mods / "Mine"
        self._patch(own, '<ModOp Type="addNextSibling" GUID="1000">%s</ModOp>' % self._asset("51"))
        host = self.mods / "Collection"
        self._patch(host / "Mine too",
                    '<ModOp Type="addNextSibling" GUID="1000">%s</ModOp>' % self._asset("52"))
        self.assertEqual(self._scan(exclude=own).keys(), {"52"})
        self.assertEqual(self._scan(exclude=host / "Mine too").keys(), {"51"})

    def test_a_patch_edited_since_is_read_again(self):
        import os

        patch = self._patch(self.mods / "Changing",
                            '<ModOp Type="addNextSibling" GUID="1000">%s</ModOp>' % self._asset("61"))
        self.assertEqual(self._scan().keys(), {"61"})
        patch.write_text(
            '<ModOps><ModOp Type="addNextSibling" GUID="1000">%s</ModOp></ModOps>' % self._asset("62"),
            encoding="utf-8",
        )
        later = patch.stat().st_mtime_ns + 10_000_000_000
        os.utime(patch, ns=(later, later))
        self.assertEqual(self._scan().keys(), {"62"})

    def test_every_place_the_game_loads_from_and_the_library_are_looked_in(self):
        import os
        from unittest import mock

        from anno_modding_studio.core import discovery, library, switches

        game = games.ANNO_117
        relpath = games.get(game).asset_relpath
        documents = self.root / "Documents mods"
        modio = self.root / "modio"
        lib = self.root / "library" / game
        elsewhere = self.root / "checkout" / "My Work In Progress"
        for folder, guid in (
            (self.mods / "In Mods", "71"),
            (self.mods / "-Switched Off", "72"),
            (self.mods / ".ubi" / "Ubisoft Shipped", "73"),
            (documents / "In Documents", "74"),
            (modio / str(games.get(game).modio_game_id) / "mods" / "9" / "From Modio", "75"),
            (lib / "Unlinked Copy", "76"),
            (elsewhere, "77"),
        ):
            self._patch(folder, '<ModOp Type="addNextSibling" GUID="1000">%s</ModOp>'
                        % self._asset(guid), relpath)
        library._write_references(lib, {"My Work In Progress": str(elsewhere)})

        with mock.patch.dict(os.environ, {"ANNO_STUDIO_MODIO": str(modio)}), \
                mock.patch.object(switches, "documents_mods", return_value=documents):
            mods = discovery.mods_on_this_pc(game, ["", self.mods], lib)
        found = self._scan(mods=mods, relpath=relpath)
        self.assertEqual(found, {
            "71": "In Mods, in your mods folder",
            "72": "-Switched Off, in your mods folder",
            "73": "Ubisoft Shipped, in the game's .ubi mods folder",
            "74": "In Documents, in your Documents mods folder",
            "75": "From Modio, in mod.io's downloads",
            "76": "Unlinked Copy, in the Studio's library",
            "77": "My Work In Progress, in the Studio's library",
        })


if __name__ == "__main__":
    unittest.main(verbosity=2)
