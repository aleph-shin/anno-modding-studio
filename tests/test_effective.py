"""The effective asset: base game plus this mod's own pending ModOps.

The point of this layer is that an edit's meaning depends on where the node came
from.  Touching a shipped node emits a ModOp; touching a node this mod invented
has to be folded into the payload that introduces it, because there is nothing in
the base game to patch.
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

from anno_modding_studio.core import (  # noqa: E402
    conflicts,
    effective,
    games,
)
from anno_modding_studio.core import (  # noqa: E402
    index as index_mod,
)
from anno_modding_studio.core.changes import (  # noqa: E402
    OP_ADD,
    OP_ADD_NEXT,
    OP_ADD_PREV,
    OP_MERGE,
    OP_REMOVE,
    OP_REPLACE,
    Change,
    Loadout,
    ModLayer,
    emit_order,
)
from anno_modding_studio.core.workspace import Workspace  # noqa: E402


class TestTheChangesEachAssetIsShapedBy(unittest.TestCase):
    """``changes_for`` groups the change set once rather than scanning it per asset.

    Scanning made anything that asks about every asset quadratic: *GUID Explorer*
    is one ModOp for each of 18,057 assets. The grouping has to follow the
    loadout wherever it moves -- and switching a mod off or reordering the mods
    moves it without moving a single change."""

    def setUp(self):
        self.loadout = Loadout()
        self.loadout.set_layer(ModLayer(origin="a", name="A"))
        self.loadout.set_layer(ModLayer(origin="b", name="B"))
        self.workspace = Workspace(None, self.loadout)

    def change(self, origin, guid, value):
        made = Change(
            guid=guid, asset_label="asset " + guid, path="/Values/X", tag="X",
            old_value="0", new_value=value, origin=origin,
        )
        self.loadout.set_change(made)
        return made

    def values(self, guid):
        return [change.new_value for change in self.workspace.changes_for(guid)]

    def test_each_asset_gets_its_own_changes_in_load_order(self):
        self.change("b", "1", "from b")
        self.change("a", "1", "from a")
        self.change("a", "2", "other")
        self.assertEqual(self.values("1"), ["from a", "from b"])
        self.assertEqual(self.values("2"), ["other"])
        self.assertEqual(self.values("3"), [])

    def test_it_follows_the_loadout_as_it_moves(self):
        first = self.change("a", "1", "one")
        self.assertEqual(self.values("1"), ["one"])
        self.change("b", "1", "two")
        self.assertEqual(self.values("1"), ["one", "two"])
        first.active = False
        self.assertEqual(self.values("1"), ["two"])
        self.loadout.layer("b").active = False
        self.assertEqual(self.values("1"), [])
        first.active = True
        self.loadout.layer("b").active = True
        self.loadout.layer("a").order, self.loadout.layer("b").order = 2, 1
        self.assertEqual(self.values("1"), ["two", "one"])

    def test_a_sweep_of_every_asset_reads_the_change_set_once(self):
        for guid in range(200):
            self.change("a", str(guid), "v")
        real, calls = self.loadout.active, []

        def counting():
            calls.append(1)
            return real()

        self.loadout.active = counting
        with self.workspace.steady():
            for guid in range(200):
                self.workspace.changes_for(str(guid))
        # Once for the grouping, once for the assets this session invents.
        self.assertLessEqual(len(calls), 2)

ASSETS = """<AssetList><Assets>
  <Asset><Template>FactoryBuilding7</Template><Values>
    <Standard><GUID>1000</GUID><Name>alpha</Name></Standard>
    <Building><BuildingType>Factory</BuildingType></Building>
    <Cost><Costs><Item><Ingredient>7</Ingredient></Item></Costs></Cost>
  </Values></Asset>
</Assets></AssetList>"""


def _base():
    return etree.fromstring(
        b"<Asset><Values><Building><BuildingType>Factory</BuildingType></Building>"
        b"<Cost><Costs><Item><Ingredient>7</Ingredient></Item></Costs></Cost>"
        b"</Values></Asset>"
    )


def _change(**kw):
    base = dict(guid="1000", asset_label="alpha", path="", tag="X")
    base.update(kw)
    return Change(**base)


class TestEffectiveTree(unittest.TestCase):
    def test_an_addition_appears_in_the_tree(self):
        change = _change(path="/Values/Building", tag="Building", op=OP_ADD,
                         payload="<Radius>5</Radius>")
        eff = effective.build(_base(), [change], "1000")
        node = eff.element.find("Values/Building/Radius")
        self.assertIsNotNone(node)
        self.assertEqual(node.text, "5")
        self.assertEqual(eff.origin(node).provenance, effective.PROV_ADDED)
        self.assertTrue(eff.origin(node).is_mod_owned)

    def test_a_field_made_sure_of_is_made_where_it_is_absent(self):
        change = _change(path="/Values/Building", tag="Radius", op=OP_MERGE,
                         payload="<Radius>5</Radius>")
        eff = effective.build(_base(), [change], "1000")
        node = eff.element.find("Values/Building/Radius")
        self.assertEqual(node.text, "5")
        self.assertEqual(eff.origin(node).change_key, change.key)
        self.assertTrue(eff.origin(node).is_mod_owned)

    def test_a_field_made_sure_of_is_set_where_another_mod_made_it(self):
        """The whole difference from an addition: one field, not two."""
        first = _change(path="/Values/Building", tag="Radius", op=OP_MERGE,
                        payload="<Radius>5</Radius>", origin="a mod")
        second = _change(path="/Values/Building", tag="Radius", op=OP_MERGE,
                         payload="<Radius>9</Radius>", origin="b mod")
        eff = effective.build(_base(), [first, second], "1000")
        found = eff.element.findall("Values/Building/Radius")
        self.assertEqual([node.text for node in found], ["9"])
        # Whoever set it last holds it: an edit there belongs in that payload.
        self.assertEqual(eff.origin(found[0]).change_key, second.key)
        self.assertEqual(eff.origin(found[0]).payload_path, "")
        # Set, not added: the field was the other mod's.
        self.assertEqual(eff.origin(found[0]).provenance, effective.PROV_SET)
        self.assertTrue(eff.origin(found[0]).is_mod_owned)

    def test_an_ensure_reaching_into_a_section_another_mod_made(self):
        first = _change(path="/Values", tag="Extra", op=OP_MERGE,
                        payload="<Extra><Fee>1</Fee></Extra>", origin="a mod")
        second = _change(path="/Values", tag="Extra", op=OP_MERGE,
                         payload="<Extra><Fee>7</Fee></Extra>", origin="b mod")
        eff = effective.build(_base(), [first, second], "1000")
        self.assertEqual([n.text for n in eff.element.findall("Values/Extra/Fee")], ["7"])
        fee = eff.element.find("Values/Extra/Fee")
        self.assertEqual(eff.origin(fee).provenance, effective.PROV_SET)
        self.assertEqual(eff.origin(fee).payload_path, "0")
        # And without the other mod it makes the section itself.
        alone = effective.build(_base(), [second], "1000")
        self.assertEqual(alone.element.findtext("Values/Extra/Fee"), "7")

    def test_a_section_made_sure_of_goes_into_the_one_that_is_there(self):
        change = _change(path="/Values", tag="Building", op=OP_MERGE,
                         payload="<Building><BuildingType>Farm</BuildingType>"
                                 "<Radius>5</Radius></Building>")
        eff = effective.build(_base(), [change], "1000")
        building = eff.element.findall("Values/Building")
        self.assertEqual(len(building), 1)
        self.assertEqual(building[0].findtext("BuildingType"), "Farm")
        self.assertEqual(building[0].findtext("Radius"), "5")
        # The section was the game's and stays so; the fields are the change's.
        self.assertEqual(eff.origin(building[0]).provenance, effective.PROV_BASE)
        self.assertEqual(eff.origin(building[0].find("BuildingType")).payload_path, "0")
        self.assertEqual(eff.origin(building[0].find("Radius")).payload_path, "1")

    def test_an_addition_to_the_asset_itself_is_shown(self):
        """An empty path is the asset. *Orchard Rework* merges ``<Template>``
        in there; the export wrote it and the Studio showed nothing."""
        change = _change(path="", tag="Template", op=OP_ADD,
                         payload="<Template>FarmBuilding</Template>")
        eff = effective.build(_base(), [change], "1000")
        node = eff.element.find("Template")
        self.assertIsNotNone(node)
        self.assertEqual(node.text, "FarmBuilding")
        self.assertEqual(eff.origin(node).provenance, effective.PROV_ADDED)

    def test_a_removal_is_marked_not_deleted(self):
        """The node stays so the removal can be seen and undone."""
        change = _change(path="/Values/Building/BuildingType", tag="BuildingType",
                         op=OP_REMOVE)
        eff = effective.build(_base(), [change], "1000")
        node = eff.element.find("Values/Building/BuildingType")
        self.assertIsNotNone(node)
        self.assertTrue(eff.origin(node).is_removed)

    def test_a_removed_node_is_the_doing_of_the_change_that_removed_it(self):
        """Not of whatever it had been: a node the game ships had nobody's
        key, and one another mod had added had the adder's, so the editor
        named the wrong mod for the removal, or the one being edited."""
        shipped = _change(path="/Values/Building/BuildingType", tag="BuildingType",
                          op=OP_REMOVE)
        eff = effective.build(_base(), [shipped], "1000")
        node = eff.element.find("Values/Building/BuildingType")
        self.assertEqual(eff.origin(node).change_key, shipped.key)

        added = Change(
            guid="1000", asset_label="A Building", path="/Values/Building", tag="Extra",
            op=OP_ADD, payload="<Extra>1</Extra>", origin="Adder",
        )
        removed = Change(
            guid="1000", asset_label="A Building", path="/Values/Building/Extra",
            tag="Extra", op=OP_REMOVE, origin="Deleter",
        )
        eff = effective.build(_base(), [added, removed], "1000")
        node = eff.element.find("Values/Building/Extra")
        self.assertTrue(eff.origin(node).is_removed)
        self.assertEqual(eff.origin(node).change_key, removed.key)

    LISTED = (
        "<Asset><Values><Standard><GUID>1000</GUID></Standard><List>"
        "<Item><W>a</W><V>1</V></Item>"
        "<Item><W>b</W><V>2</V></Item>"
        "</List></Values></Asset>"
    )

    def _one_op(self, *changes):
        """``changes`` as one op's, in the order given, applied to LISTED."""
        made = [
            Change(guid="1000", asset_label="A Building", order=3, **change)
            for change in changes
        ]
        eff = effective.build(etree.fromstring(self.LISTED), made, "1000")
        return eff, made

    def test_one_ops_changes_are_applied_in_the_order_they_are_written(self):
        """Removals, then replacements: the order the writer gives the game.
        With the replacement first, the removal found the entry it was for and
        the one the replacement had just made like it, and was passed over."""
        replace = {"path": "/Values/List/Item[W='a']/V", "tag": "V", "op": OP_REPLACE,
                   "old_value": "1", "new_value": "2"}
        remove = {"path": "/Values/List/Item[V='2']", "tag": "Item", "op": OP_REMOVE}
        for order in ((replace, remove), (remove, replace)):
            eff, made = self._one_op(*order)
            self.assertEqual(
                [change.op for change in emit_order(made)], [OP_REMOVE, OP_REPLACE]
            )
            left = eff.as_the_game_reads_it().findall("Values/List/Item")
            self.assertEqual(
                [(item.findtext("W"), item.findtext("V")) for item in left], [("a", "2")]
            )

    def test_what_holds_the_guid_goes_last_as_it_is_written(self):
        """A removal of the node the GUID is under is written after the op's
        other changes, and applied after them here."""
        gone = {"path": "/Values/Standard", "tag": "Standard", "op": OP_REMOVE}
        other = {"path": "/Values/List/Item[W='b']", "tag": "Item", "op": OP_REMOVE}
        eff, made = self._one_op(gone, other)
        self.assertEqual([change.path for change in emit_order(made)],
                         [other["path"], gone["path"]])
        read = eff.as_the_game_reads_it()
        self.assertIsNone(read.find("Values/Standard"))
        self.assertEqual(len(read.findall("Values/List/Item")), 1)

    def test_two_ops_keep_their_own_order(self):
        """Only one op's changes are ordered by kind: a later op's removal
        comes after an earlier op's replacement, as the file runs them."""
        first = Change(guid="1000", asset_label="A Building", order=1,
                       path="/Values/List/Item[W='a']/V", tag="V", op=OP_REPLACE,
                       old_value="1", new_value="2")
        second = Change(guid="1000", asset_label="A Building", order=2,
                        path="/Values/List/Item[V='2']", tag="Item", op=OP_REMOVE)
        eff = effective.build(etree.fromstring(self.LISTED), [second, first], "1000")
        # Two entries hold 2 by then, so the removal matches neither alone.
        self.assertEqual(len(eff.as_the_game_reads_it().findall("Values/List/Item")), 2)

    def test_a_replace_shows_the_new_value_and_is_marked_edited(self):
        change = _change(path="/Values/Building/BuildingType", tag="BuildingType",
                         old_value="Factory", new_value="Residence")
        eff = effective.build(_base(), [change], "1000")
        node = eff.element.find("Values/Building/BuildingType")
        self.assertEqual(node.text, "Residence")
        self.assertEqual(eff.origin(node).provenance, effective.PROV_EDITED)
        self.assertFalse(eff.origin(node).is_mod_owned)

    def test_untouched_nodes_stay_base(self):
        eff = effective.build(_base(), [], "1000")
        node = eff.element.find("Values/Building/BuildingType")
        self.assertEqual(eff.origin(node).provenance, effective.PROV_BASE)
        self.assertFalse(eff.origin(node).is_mod_owned)

    def test_origins_survive_proxy_churn(self):
        """lxml hands out fresh proxies, so origins cannot be keyed on id()."""
        import gc

        change = _change(path="/Values/Building", tag="Building", op=OP_ADD,
                         payload="<Radius>5</Radius>")
        eff = effective.build(_base(), [change], "1000")
        first = eff.origin(eff.element.find("Values/Building/Radius"))
        gc.collect()
        second = eff.origin(eff.element.find("Values/Building/Radius"))
        self.assertEqual(first.provenance, effective.PROV_ADDED)
        self.assertEqual(first, second)

    def test_nested_additions_get_payload_paths(self):
        change = _change(
            path="/Values/Cost/Costs", tag="Costs", op=OP_ADD,
            payload="<Item><Ingredient>9</Ingredient><Amount>2</Amount></Item>",
        )
        eff = effective.build(_base(), [change], "1000")
        items = eff.element.findall("Values/Cost/Costs/Item")
        self.assertEqual(len(items), 2)
        added = items[1]
        self.assertEqual(eff.origin(added).payload_path, "")
        self.assertEqual(eff.origin(added.find("Ingredient")).payload_path, "0")
        self.assertEqual(eff.origin(added.find("Amount")).payload_path, "1")

    def test_a_merge_goes_by_the_rules_of_the_game_it_is_for(self):
        """Anno 1800's loader compares names as written, Anno 117's in any
        case: a ``<radius>`` merged in beside ``<Radius>`` is a second field
        in Anno 1800, and was shown set into the first."""
        change = _change(path="/Values/Building", tag="radius", op=OP_MERGE,
                         payload="<radius>30</radius>")
        base = _base()
        etree.SubElement(base.find("Values/Building"), "Radius").text = "5"

        def fields(game):
            shown = effective.build(base, [change], "1000", game=game).as_the_game_reads_it()
            return [(node.tag, node.text) for node in shown.find("Values/Building")]

        self.assertEqual(fields(games.ANNO_1800),
                         [("BuildingType", "Factory"), ("Radius", "5"), ("radius", "30")])
        self.assertEqual(fields(games.ANNO_117), [("BuildingType", "Factory"), ("Radius", "30")])

    def test_unresolvable_changes_are_ignored_here(self):
        """Reporting them is the verifier's job; this must not crash."""
        change = _change(path="/Values/Nope/Missing", tag="Missing", op=OP_REMOVE)
        eff = effective.build(_base(), [change], "1000")
        self.assertIsNotNone(eff.element)


class TestPayloadEditing(unittest.TestCase):
    """Edits to mod-owned nodes fold into the change that created them."""

    def _add_change(self):
        return _change(
            path="/Values/Cost/Costs", tag="Costs", op=OP_ADD,
            payload="<Item><Ingredient>9</Ingredient><Amount>2</Amount></Item>",
        )

    def test_setting_a_value_rewrites_the_payload(self):
        change = self._add_change()
        self.assertTrue(effective.set_payload_text(change, "1", "99"))
        root = effective.parse_payload(change)
        self.assertEqual(root.findtext("Amount"), "99")
        self.assertEqual(root.findtext("Ingredient"), "9")

    def test_adding_under_an_added_node_grows_the_same_payload(self):
        """The whole point: one add ModOp, richer payload -- not two ModOps."""
        change = self._add_change()
        extra = etree.Element("StorageAmount")
        extra.text = "50"
        self.assertTrue(effective.add_to_payload(change, "", extra))
        root = effective.parse_payload(change)
        self.assertEqual([c.tag for c in root], ["Ingredient", "Amount", "StorageAmount"])

    def test_appending_reindents_the_whole_payload(self):
        """Otherwise the new child lands at the wrong depth in the output."""
        change = self._add_change()
        extra = etree.Element("StorageAmount")
        extra.text = "50"
        effective.add_to_payload(change, "", extra)
        lines = change.payload.splitlines()
        self.assertEqual(lines[0], "<Item>")
        self.assertEqual(lines[-1], "</Item>")
        for line in lines[1:-1]:
            self.assertTrue(line.startswith("  "), repr(line))
            self.assertFalse(line.startswith("   "), repr(line))

    def test_removing_a_payload_child_leaves_the_change(self):
        change = self._add_change()
        self.assertEqual(effective.remove_from_payload(change, "1"), "removed")
        root = effective.parse_payload(change)
        self.assertEqual([c.tag for c in root], ["Ingredient"])

    def test_removing_the_payload_root_means_dropping_the_change(self):
        change = self._add_change()
        self.assertEqual(effective.remove_from_payload(change, ""), "empty")

    def test_a_change_keeps_its_identity_when_its_payload_is_edited(self):
        """Identity must not be derived from the payload.

        It used to hash the payload, so editing one in place silently moved the
        change to a new key and orphaned every reference already held to it --
        the next edit or removal aimed at that node quietly did nothing.
        """
        loadout = Loadout()
        change = self._add_change()
        loadout.set_change(change)
        original_key = change.key

        effective.set_payload_text(change, "1", "42")
        self.assertEqual(change.key, original_key)
        self.assertIn(change.key, loadout.changes)
        self.assertEqual(len(loadout), 1)

    def test_two_additions_to_one_path_stay_distinct(self):
        """The uid is what keeps them apart now that the payload does not."""
        loadout = Loadout()
        first, second = self._add_change(), self._add_change()
        loadout.set_change(first)
        loadout.set_change(second)
        self.assertEqual(len(loadout), 2)
        self.assertNotEqual(first.key, second.key)

    def test_successive_payload_edits_all_land(self):
        """Regression: the second edit used to be silently dropped."""
        loadout = Loadout()
        change = self._add_change()
        loadout.set_change(change)

        for path, value in (("0", "11"), ("1", "22")):
            found = loadout.changes[change.key]
            self.assertTrue(effective.set_payload_text(found, path, value))

        root = effective.parse_payload(loadout.changes[change.key])
        self.assertEqual(root.findtext("Ingredient"), "11")
        self.assertEqual(root.findtext("Amount"), "22")
        self.assertEqual(len(loadout), 1)

    def test_a_bad_payload_path_is_refused(self):
        change = self._add_change()
        self.assertFalse(effective.set_payload_text(change, "9.9", "x"))
        self.assertEqual(effective.remove_from_payload(change, "9"), "failed")


class TestWorkspace(unittest.TestCase):
    """Base index plus the assets this mod invents."""

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        tmp = Path(cls._tmp.name)
        cls.assets_path = tmp / "assets.xml"
        cls.assets_path.write_text(ASSETS, encoding="utf-8")
        cls.db_path = tmp / "index.sqlite"
        index_mod.build_index(cls.assets_path, cls.db_path)
        cls.index = index_mod.AssetIndex(cls.db_path, cls.assets_path)

    @classmethod
    def tearDownClass(cls):
        cls.index.close()
        cls._tmp.cleanup()

    def _workspace_with_new_asset(self):
        loadout = Loadout()
        loadout.set_change(
            Change(
                guid="1000", asset_label="brand new  (new asset)", path="", tag="Asset",
                op=OP_ADD_NEXT, summary="create asset 1337000000",
                payload="<Asset><BaseAssetGUID>1000</BaseAssetGUID><Values><Standard>"
                        "<GUID>1337000000</GUID><Name>brand_new</Name></Standard>"
                        "<Building><BuildingType>Factory</BuildingType></Building>"
                        "</Values></Asset>",
            )
        )
        return Workspace(self.index, loadout), loadout

    def test_a_created_asset_becomes_findable(self):
        workspace, _loadout = self._workspace_with_new_asset()
        self.assertTrue(workspace.is_new_asset("1337000000"))
        self.assertFalse(workspace.is_new_asset("1000"))

        row = workspace.asset_row("1337000000")
        self.assertIsNotNone(row)
        self.assertTrue(row.is_new)
        self.assertEqual(row.name, "brand_new")
        self.assertTrue(row.label.startswith("+ "))

    def test_base_and_created_assets_stay_distinguishable(self):
        """The syntax an edit needs depends on which kind it is."""
        workspace, _loadout = self._workspace_with_new_asset()
        self.assertTrue(workspace.guid_exists("1337000000"))
        self.assertFalse(workspace.guid_in_base_game("1337000000"))
        self.assertTrue(workspace.guid_in_base_game("1000"))
        self.assertFalse(workspace.asset_row("1000").is_new)

    def test_a_created_asset_is_searchable_and_leads(self):
        workspace, _loadout = self._workspace_with_new_asset()
        rows = workspace.search("brand")
        self.assertTrue(rows)
        self.assertEqual(rows[0].guid, "1337000000")
        self.assertGreater(workspace.total_matches("brand"), 0)

    def test_a_created_asset_is_counted_under_its_template(self):
        """The Template list is counted from here, and the search beside it
        lists created assets: the two have to agree."""
        workspace, loadout = self._workspace_with_new_asset()
        shipped = dict(self.index.template_counts_for(""))
        known = sorted(shipped)[0]
        for guid, name, template in (
            ("1337000001", "brand_other", "BrandNewKind"),
            ("1337000002", "unrelated", known),
        ):
            loadout.set_change(
                Change(
                    guid="1000", asset_label=name + "  (new asset)", path="",
                    tag="Asset", op=OP_ADD_NEXT, summary="create asset " + guid,
                    payload="<Asset><Template>%s</Template><Values><Standard>"
                            "<GUID>%s</GUID><Name>%s</Name></Standard>"
                            "</Values></Asset>" % (template, guid, name),
                )
            )
        counts = workspace.template_counts_for("")
        self.assertEqual([name for name, _n in counts], sorted(dict(counts)))
        self.assertEqual(dict(counts)[known], shipped[known] + 1)
        self.assertEqual(dict(counts)["BrandNewKind"], 1)
        # Narrowed by the words the search is narrowed by.
        narrowed = dict(workspace.template_counts_for("brand_other"))
        self.assertEqual(narrowed, {"BrandNewKind": 1})

    def test_a_created_asset_that_inherits_is_counted_under_no_template(self):
        """It names an asset to inherit from where a template would be, and
        its row says so: "inherits 1000".  That is not a template, and it
        was offered in the Template list as one.  A shipped asset that
        inherits is counted under none either."""
        workspace, _loadout = self._workspace_with_new_asset()
        self.assertEqual(workspace.asset_row("1337000000").template, "inherits 1000")
        self.assertEqual(
            workspace.template_counts_for(""), self.index.template_counts_for("")
        )
        # Found by its name all the same, with nothing to narrow it by.
        self.assertEqual(
            [row.guid for row in workspace.search("brand_new")], ["1337000000"]
        )
        self.assertEqual(workspace.template_counts_for("brand_new"), [])
        offered = {name for name, _n in workspace.template_counts_for("1000")}
        self.assertLessEqual(offered, set(self.index.templates()))

    def test_the_asset_whose_guid_was_typed_leads_a_created_one(self):
        """The created asset inherits from 1000 and says so in its Template
        column, so a search for 1000 finds both.  It was listed first, and
        the wrong one of the two was picked as a new asset's base."""
        workspace, _loadout = self._workspace_with_new_asset()
        rows = workspace.search("1000")
        self.assertEqual([row.guid for row in rows[:2]], ["1000", "1337000000"])
        # Its own GUID typed finds it first, as before.
        self.assertEqual(workspace.search("1337000000")[0].guid, "1337000000")

    def test_a_created_asset_opens_as_a_tree_owned_by_its_change(self):
        workspace, _loadout = self._workspace_with_new_asset()
        eff = workspace.effective_asset("1337000000")
        self.assertTrue(eff.is_new)
        node = eff.element.find("Values/Building/BuildingType")
        self.assertIsNotNone(node)
        origin = eff.origin(node)
        self.assertEqual(origin.provenance, effective.PROV_NEW_ASSET)
        self.assertTrue(origin.is_mod_owned)

    def test_editing_a_created_asset_folds_into_its_payload(self):
        workspace, loadout = self._workspace_with_new_asset()
        eff = workspace.effective_asset("1337000000")
        node = eff.element.find("Values/Building/BuildingType")
        origin = eff.origin(node)

        change = loadout.changes[tuple(origin.change_key)]
        self.assertTrue(effective.set_payload_text(change, origin.payload_path, "Residence"))

        # Still exactly one ModOp -- the creating one, now carrying the new value.
        self.assertEqual(len(loadout), 1)
        reloaded = workspace.effective_asset("1337000000")
        self.assertEqual(
            reloaded.element.findtext("Values/Building/BuildingType"), "Residence"
        )

    def test_a_new_asset_guid_is_checked_against_the_base_game_only(self):
        """The workspace knows about our own new asset; that must not read as a
        collision with the shipped game."""
        from anno_modding_studio.core import generator

        workspace, loadout = self._workspace_with_new_asset()
        results = generator.verify_changes(workspace, loadout)
        self.assertEqual(len(results), 1)
        self.assertTrue(results[0].ok, results[0].message)

    def test_a_new_asset_reusing_a_shipped_guid_is_still_caught(self):
        from anno_modding_studio.core import generator

        loadout = Loadout()
        loadout.set_change(
            Change(
                guid="1000", asset_label="clash", path="", tag="Asset", op=OP_ADD_NEXT,
                payload="<Asset><Values><Standard><GUID>1000</GUID></Standard>"
                        "</Values></Asset>",
            )
        )
        workspace = Workspace(self.index, loadout)
        result = generator.verify_changes(workspace, loadout)[0]
        self.assertFalse(result.ok)
        self.assertIn("already belongs", result.message)

    def test_your_own_change_over_a_loaded_mod_s_value_is_checked_on_the_stack(self):
        """A loadout's own mod is written over what the mods in it leave. The
        shipped file says Factory, the loaded mod makes it Farm, and yours
        expects Farm."""
        from anno_modding_studio.core import generator

        loadout = Loadout()
        loadout.own.loads_last = True
        loadout.set_layer(ModLayer(origin="Their Mod", mod_id="theirs", order=1))
        for origin, before, after in (("Their Mod", "Factory", "Farm"), ("", "Farm", "Mine")):
            loadout.set_change(Change(
                guid="1000", asset_label="alpha", path="/Values/Building/BuildingType",
                tag="BuildingType", old_value=before, new_value=after, origin=origin,
            ))
        workspace = Workspace(self.index, loadout)
        yours = generator.for_mod(loadout, "")
        results = generator.verify_changes(workspace, yours)
        self.assertEqual([(r.ok, r.message) for r in results], [(True, "ok")])

    def test_and_one_that_expects_what_no_mod_leaves_is_still_caught(self):
        from anno_modding_studio.core import generator

        loadout = Loadout()
        loadout.own.loads_last = True
        loadout.set_layer(ModLayer(origin="Their Mod", mod_id="theirs", order=1))
        for origin, before, after in (("Their Mod", "Factory", "Farm"), ("", "Harbour", "Mine")):
            loadout.set_change(Change(
                guid="1000", asset_label="alpha", path="/Values/Building/BuildingType",
                tag="BuildingType", old_value=before, new_value=after, origin=origin,
            ))
        workspace = Workspace(self.index, loadout)
        result = generator.verify_changes(workspace, generator.for_mod(loadout, ""))[0]
        self.assertFalse(result.ok)
        self.assertIn("checked against the mods loaded before it", result.message)

    def test_the_base_index_is_never_polluted(self):
        """New assets live in the loadout, not in the shared on-disk cache."""
        workspace, _loadout = self._workspace_with_new_asset()
        self.assertTrue(workspace.guid_exists("1337000000"))
        self.assertFalse(self.index.guid_exists("1337000000"))
        self.assertIsNone(self.index.asset_row("1337000000"))

    def test_an_asset_is_shown_by_the_rules_of_the_loadouts_game(self):
        """As the asset stands and as a later mod finds it: an Anno 1800
        merge of ``<buildingType>`` is a field of its own beside
        ``<BuildingType>``, where Anno 117 sets the one that is there."""
        for game, wanted in (
            (games.ANNO_1800, [("BuildingType", "Factory"), ("buildingType", "Farm")]),
            (games.ANNO_117, [("BuildingType", "Farm")]),
        ):
            loadout = Loadout(game=game)
            loadout.set_layer(ModLayer(origin="Early", mod_id="early", order=1))
            loadout.set_layer(ModLayer(origin="Late", mod_id="late", order=2))
            loadout.set_change(
                Change(guid="1000", asset_label="alpha", path="/Values/Building",
                       tag="buildingType", op=OP_MERGE, origin="Early",
                       payload="<buildingType>Farm</buildingType>")
            )
            workspace = Workspace(self.index, loadout)
            shown = workspace.effective_asset("1000").as_the_game_reads_it()
            before = workspace.asset_before("1000", "Late")
            for tree in (shown, before):
                with self.subTest(game=game):
                    self.assertEqual(
                        [(node.tag, node.text) for node in tree.find("Values/Building")], wanted
                    )

    def test_effective_asset_applies_changes_to_a_base_asset(self):
        loadout = Loadout()
        loadout.set_change(
            Change(guid="1000", asset_label="alpha", path="/Values/Building",
                   tag="Building", op=OP_ADD, payload="<Radius>5</Radius>")
        )
        workspace = Workspace(self.index, loadout)
        eff = workspace.effective_asset("1000")
        self.assertIsNotNone(eff.element.find("Values/Building/Radius"))
        # The shipped asset is untouched.
        self.assertIsNone(
            self.index.asset_element("1000").find("Values/Building/Radius")
        )


class TestAnOverrideTheLaterModMeantToMake(unittest.TestCase):
    """Two mods reaching for one field is only a problem when neither knew
    about the other.

    A patch exists to override the mod it patches; a mod declaring
    ``LoadAfterIds`` naming another was written knowing it might be there and
    chose to come after it.  Both still get listed -- which mods touch a value
    is worth knowing -- but neither is counted among the things wanting a look.
    """

    # Named so that folder order -- which is load order -- puts the winner
    # last, the way a patch's " (my patch)" suffix does for real.
    HOST = "A Their Mod"
    LATER = "B Later Mod"

    def stack(self, later_kwargs):
        loadout = Loadout(game=games.ANNO_1800)
        loadout.set_layer(ModLayer(origin=self.HOST, mod_id="theirs"))
        loadout.set_layer(ModLayer(origin=self.LATER, **later_kwargs))
        for origin, value in ((self.HOST, "10"), (self.LATER, "20")):
            loadout.set_change(
                Change(
                    guid="1000", asset_label="A Building",
                    path="/Values/Building/Radius", tag="Radius",
                    old_value="5", new_value=value, origin=origin,
                )
            )
        return loadout

    def only(self, loadout):
        found = conflicts.find(loadout)
        self.assertEqual(len(found), 1, found)
        return found[0]

    def test_two_strangers_contest_a_field(self):
        clash = self.only(self.stack({}))
        self.assertEqual(clash.kind, conflicts.OVERWRITES)
        self.assertFalse(clash.is_harmless)
        self.assertIn("contested", conflicts.summarise(loadout=self.stack({}),
                                                       name=lambda o: o))

    def test_a_patch_overriding_what_it_patches_is_by_design(self):
        clash = self.only(self.stack({"patches": self.HOST}))
        self.assertEqual(clash.kind, conflicts.INTENDED)
        self.assertEqual(clash.expected, "patch")
        self.assertTrue(clash.is_harmless)
        self.assertIn("its job", clash.describe(lambda o: o))

    def test_and_so_is_a_mod_that_declares_it_loads_after_the_other(self):
        clash = self.only(self.stack({"load_after": ["theirs"]}))
        self.assertEqual(clash.kind, conflicts.INTENDED)
        self.assertEqual(clash.expected, "declared")
        self.assertIn("declares it loads after", clash.describe(lambda o: o))

    def test_declaring_a_load_after_for_some_other_mod_does_not_count(self):
        clash = self.only(self.stack({"load_after": ["somebody-else"]}))
        self.assertEqual(clash.kind, conflicts.OVERWRITES)

    def test_neither_is_counted_among_the_contested(self):
        for later in ({"patches": self.HOST}, {"load_after": ["theirs"]}):
            with self.subTest(later=later):
                self.assertEqual(
                    conflicts.summarise(self.stack(later), lambda o: o), ""
                )

    def test_but_they_are_still_listed(self):
        """The user may well want to know which mods touch a value."""
        self.assertEqual(len(conflicts.find(self.stack({"patches": self.HOST}))), 1)

    def with_your_own(self, own_mod_loads_last):
        """One loaded mod, and the loadout's own mod setting the same field."""
        loadout = Loadout(game=games.ANNO_1800)
        loadout.own.loads_last = own_mod_loads_last
        loadout.set_layer(ModLayer(origin=self.HOST, mod_id="theirs"))
        for origin, value in ((self.HOST, "10"), ("", "20")):
            loadout.set_change(
                Change(
                    guid="1000", asset_label="A Building",
                    path="/Values/Building/Radius", tag="Radius",
                    old_value="5", new_value=value, origin=origin,
                )
            )
        return loadout

    def test_a_loadouts_own_mod_overriding_a_mod_in_it_is_by_design(self):
        loadout = self.with_your_own(own_mod_loads_last=True)
        clash = self.only(loadout)
        self.assertEqual(clash.kind, conflicts.INTENDED)
        self.assertEqual(clash.expected, "loadout")
        self.assertIn("your changes to this loadout", clash.describe(lambda o: o or "Mine"))
        self.assertEqual(conflicts.summarise(loadout, lambda o: o), "")

    def test_a_mod_you_are_writing_to_publish_still_contests_one_loaded_beside_it(self):
        clash = self.only(self.with_your_own(own_mod_loads_last=False))
        self.assertEqual(clash.kind, conflicts.OVERWRITES)

    def test_agreeing_on_a_value_is_still_its_own_thing(self):
        loadout = Loadout(game=games.ANNO_1800)
        loadout.set_layer(ModLayer(origin=self.HOST, mod_id="theirs"))
        loadout.set_layer(ModLayer(origin=self.LATER, patches=self.HOST))
        for origin in (self.HOST, self.LATER):
            loadout.set_change(
                Change(
                    guid="1000", asset_label="A Building",
                    path="/Values/Building/Radius", tag="Radius",
                    old_value="5", new_value="20", origin=origin,
                )
            )
        self.assertEqual(self.only(loadout).kind, conflicts.AGREES)


class TestAModThatDeletesAnAssetOutright(unittest.TestCase):
    """XPath allows it and real mods do it.

    It was imported and listed as a change, and then never applied: the path is
    empty, ``_resolve`` returns nothing for an empty path, and ``_apply`` bails
    unless exactly one node matched.  So the Studio said a mod deleted an asset
    and went on showing that asset exactly as shipped.
    """

    ASSET = (
        "<Asset><Template>Building</Template><Values>"
        "<Standard><GUID>1000</GUID><Name>a_building</Name></Standard>"
        "<Building><Radius>5</Radius></Building>"
        "</Values></Asset>"
    )

    def built(self, *changes):
        from lxml import etree

        base = etree.fromstring(self.ASSET.encode("utf-8"))
        return effective.build(base, list(changes), "1000", False)

    def removal(self):
        return Change(
            guid="1000", asset_label="A Building", path="", tag="Asset",
            op=OP_REMOVE, origin="Deleter", summary="remove Asset",
        )

    def test_a_swap_beside_a_node_swapped_before_it_finds_only_the_new_one(self):
        """*Pin Button Preset* replaces four regions whole, one after another.
        The second goes in after the first -- which matched the struck-through
        old copy as well as the new, and the region was dropped."""
        from lxml import etree

        base = etree.fromstring(
            b"<Asset><Values><Standard><GUID>1000</GUID></Standard><Regions>"
            b"<A>old</A><B>old</B><C>old</C></Regions></Values></Asset>"
        )
        def swap(order, tag, anchor, op):
            return [
                Change(guid="1000", asset_label="a", path="/Values/Regions/" + tag, tag=tag,
                       op=OP_REMOVE, origin="Pins", order=order),
                Change(guid="1000", asset_label="a", path="/Values/Regions/" + anchor, tag=tag,
                       op=op, payload="<%s>new</%s>" % (tag, tag), origin="Pins", order=order),
            ]
        changes = swap(0, "A", "B", OP_ADD_PREV) + swap(1, "B", "A", OP_ADD_NEXT)
        shown = effective.build(base, changes, "1000", False).as_the_game_reads_it()
        self.assertEqual(
            [(node.tag, node.text) for node in shown.find("Values/Regions")],
            [("A", "new"), ("B", "new"), ("C", "old")],
        )

    def test_a_later_mod_removes_what_an_earlier_mod_added(self):
        """Applied by kind across mods, the removal ran first and missed."""
        added = Change(
            guid="1000", asset_label="A Building", path="/Values/Building", tag="Extra",
            op=OP_ADD, payload="<Extra>1</Extra>", origin="Adder",
        )
        removed = Change(
            guid="1000", asset_label="A Building", path="/Values/Building/Extra",
            tag="Extra", op=OP_REMOVE, origin="Deleter",
        )
        asset = self.built(added, removed)
        self.assertIsNone(asset.as_the_game_reads_it().find("Values/Building/Extra"))

    def test_the_whole_asset_reads_as_removed(self):
        asset = self.built(self.removal())
        self.assertTrue(asset.origin(asset.element).is_removed)

    def test_and_so_does_everything_inside_it(self):
        asset = self.built(self.removal())
        nodes = [n for n in asset.element.iter() if isinstance(n.tag, str)]
        self.assertTrue(nodes)
        for node in nodes:
            self.assertTrue(asset.origin(node).is_removed, node.tag)

    def test_an_asset_nobody_removed_is_untouched(self):
        asset = self.built()
        self.assertFalse(asset.origin(asset.element).is_removed)

    def test_a_removal_of_one_section_still_only_takes_that_section(self):
        asset = self.built(
            Change(
                guid="1000", asset_label="A Building",
                path="/Values/Building", tag="Building", op=OP_REMOVE,
                origin="Deleter",
            )
        )
        self.assertFalse(asset.origin(asset.element).is_removed)
        section = asset.element.find("Values/Building")
        self.assertTrue(asset.origin(section).is_removed)


class TestWhatPointsAtThisWithAWholeModsFolderOpen(unittest.TestCase):
    """"What points at this?" had to re-walk every asset the session touches.

    A change can add or remove a link, so an asset with any change on it could
    no longer be trusted to the index -- and with a mods folder open that is
    thousands of assets, each one a tree rebuilt and walked, to draw a graph of
    a dozen nodes. But a change can only alter a link that runs through the
    part of the asset it touches: a retuned cycle time does not move the good
    a factory consumes.
    """

    ASSETS = """<AssetList><Assets>
  <Asset><Template>FactoryBuilding7</Template><Values>
    <Standard><GUID>9100</GUID><Name>maker</Name></Standard>
    <Factory7><CycleTime>30</CycleTime><FactoryInputs><Item>
      <Product>9000</Product><Amount>2</Amount>
    </Item></FactoryInputs></Factory7>
  </Values></Asset>
  <Asset><Template>Product</Template><Values>
    <Standard><GUID>9000</GUID><Name>the_good</Name></Standard>
  </Values></Asset>
  <Asset><Template>Product</Template><Values>
    <Standard><GUID>9001</GUID><Name>other_good</Name></Standard>
  </Values></Asset>
</Assets></AssetList>"""

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        tmp = Path(cls._tmp.name)
        cls.assets_path = tmp / "assets.xml"
        cls.assets_path.write_text(cls.ASSETS, encoding="utf-8")
        cls.db_path = tmp / "index.sqlite"
        index_mod.build_index(cls.assets_path, cls.db_path)
        cls.index = index_mod.AssetIndex(cls.db_path, cls.assets_path)

    @classmethod
    def tearDownClass(cls):
        cls.index.close()
        cls._tmp.cleanup()

    def asking(self, *changes, about="9000"):
        """``(what points at it, which assets were walked live)``."""
        from unittest import mock

        from anno_modding_studio.core.workspace import Workspace

        loadout = Loadout()
        for change in changes:
            loadout.set_change(change)
        workspace = Workspace(self.index, loadout)
        walked = []
        real = Workspace._walk_links

        def spy(inner, guid, confident_only, with_nominal=True):
            walked.append(str(guid))
            return real(inner, guid, confident_only, with_nominal)

        with mock.patch.object(Workspace, "_walk_links", spy):
            links = workspace.references_to(about, confident_only=False)
        return {link.guid for link in links}, walked

    def change(self, path, tag, value, old="30"):
        return Change(
            guid="9100", asset_label="maker", path=path, tag=tag,
            old_value=old, new_value=value,
        )

    def test_the_index_already_knows_who_consumes_the_good(self):
        found, walked = self.asking()
        self.assertIn("9100", found)
        self.assertEqual(walked, [], "nothing was touched, so nothing to walk")

    def test_a_change_somewhere_else_in_the_asset_is_not_worth_a_walk(self):
        found, walked = self.asking(
            self.change("/Values/Factory7/CycleTime", "CycleTime", "45")
        )
        self.assertIn("9100", found)
        self.assertEqual(walked, [])

    def test_but_a_change_to_the_link_itself_is(self):
        found, walked = self.asking(
            self.change(
                "/Values/Factory7/FactoryInputs/Item/Product", "Product", "9001",
                old="9000",
            )
        )
        self.assertEqual(walked, ["9100"])
        # And the answer moved with it: it consumes the other good now.
        self.assertNotIn("9100", found)

    def test_and_so_is_a_change_beside_it_in_the_same_entry(self):
        """Whether an entry sets a value is what the graph hides links by, and
        an Amount beside the Product decides that."""
        _found, walked = self.asking(
            self.change(
                "/Values/Factory7/FactoryInputs/Item/Amount", "Amount", "3", old="2"
            )
        )
        self.assertEqual(walked, ["9100"])

    def test_a_predicate_in_the_path_still_matches_the_entry(self):
        """A path that picks one entry out of a list would match nothing as
        plain text: ``Item[Product='9000']`` is not ``Item``."""
        _found, walked = self.asking(
            self.change(
                "/Values/Factory7/FactoryInputs/Item[Product='9000']/Amount",
                "Amount", "3", old="2",
            )
        )
        self.assertEqual(walked, ["9100"])

    def test_and_a_change_that_takes_the_whole_asset_away(self):
        found, walked = self.asking(
            Change(guid="9100", asset_label="maker", path="", tag="Asset",
                   op=OP_REMOVE)
        )
        self.assertEqual(walked, ["9100"])
        # An asset the game no longer has consumes nothing.
        self.assertNotIn("9100", found)

    def test_the_new_link_is_found_from_the_other_end_too(self):
        moved = self.change(
            "/Values/Factory7/FactoryInputs/Item/Product", "Product", "9001",
            old="9000",
        )
        found, _walked = self.asking(moved, about="9001")
        self.assertIn("9100", found)

    def test_the_change_set_is_taken_stock_of_once_per_question(self):
        """Each suspect is asked whether a mod makes it, and each such
        question took stock of every change: 50 ms apiece on a mods folder,
        and a thousand suspects for Coins."""
        from unittest import mock

        loadout = Loadout()
        loadout.set_change(self.change(
            "/Values/Factory7/FactoryInputs/Item/Amount", "Amount", "3", old="2"
        ))
        workspace = Workspace(self.index, loadout)
        workspace._sync()
        real = Workspace._fingerprint
        taken = []

        def counted(inner):
            taken.append(1)
            return real(inner)

        with mock.patch.object(Workspace, "_fingerprint", counted):
            workspace.references_to("9000", confident_only=False)
            workspace.references_from("9100", confident_only=False)
        self.assertLessEqual(len(taken), 2)

    def test_an_asset_is_read_once_whatever_the_settings(self):
        """The graph asks three settings of the same assets to say how many
        links it hides.  Reading the tree is the cost, so it is read once
        and each setting keeps its share of what was read."""
        from unittest import mock

        loadout = Loadout()
        loadout.set_change(self.change(
            "/Values/Factory7/FactoryInputs/Item/Amount", "Amount", "3", old="2"
        ))
        workspace = Workspace(self.index, loadout)
        real = Workspace._links_in
        read = []

        def spy(inner, guid):
            read.append(str(guid))
            return real(inner, guid)

        answers = {}
        with mock.patch.object(Workspace, "_links_in", spy):
            for confident_only in (True, False):
                for with_nominal in (True, False):
                    answers[confident_only, with_nominal] = {
                        link.guid for link in workspace.references_to(
                            "9000", confident_only, with_nominal
                        )
                    }
        self.assertEqual(read, ["9100"])
        self.assertIn("9100", answers[False, True])
        # And each setting answers as it does asked on its own.
        for (confident_only, with_nominal), found in answers.items():
            with self.subTest(confident_only=confident_only, with_nominal=with_nominal):
                alone = Workspace(self.index, loadout)
                self.assertEqual(found, {
                    link.guid for link in alone.references_to(
                        "9000", confident_only, with_nominal
                    )
                })

    def test_switching_a_whole_mod_on_brings_its_links_in(self):
        """No change moves when a mod is switched on, but which changes are
        active does -- and what the session touches, the paths it reaches and
        the numbers it names were all read off the active ones."""
        loadout = Loadout()
        loadout.set_layer(ModLayer(origin="modA", name="modA", order=1, active=False))
        moved = self.change(
            "/Values/Factory7/FactoryInputs/Item/Product", "Product", "9001",
            old="9000",
        )
        moved.origin = "modA"
        loadout.set_change(moved)
        workspace = Workspace(self.index, loadout)
        self.assertNotIn(
            "9100", {link.guid for link in workspace.references_to("9001", False)}
        )
        self.assertIn(
            "9100", {link.guid for link in workspace.references_to("9000", False)}
        )
        loadout.layer("modA").active = True
        self.assertIn(
            "9100", {link.guid for link in workspace.references_to("9001", False)}
        )
        self.assertNotIn(
            "9100", {link.guid for link in workspace.references_to("9000", False)}
        )
        self.assertEqual(
            [link.guid for link in workspace.references_from("9100", False)], ["9001"]
        )

    def test_a_link_to_an_asset_made_after_it_was_typed_is_found(self):
        """A number names an asset only once there is one under it, so what
        an asset links to moves when another asset is made, not only when the
        asset itself is edited."""
        loadout = Loadout()
        loadout.set_change(self.change(
            "/Values/Factory7/FactoryInputs/Item/Product", "Product", "1500001",
            old="9000",
        ))
        workspace = Workspace(self.index, loadout)
        self.assertEqual(workspace.references_from("9100", False), [])
        self.assertEqual(workspace.references_to("1500001", False), [])
        loadout.set_change(Change(
            guid="9000", asset_label="the_good", path="", tag="Asset",
            op=OP_ADD_NEXT, summary="create asset 1500001",
            payload="<Asset><Template>Product</Template><Values><Standard>"
                    "<GUID>1500001</GUID><Name>new_good</Name></Standard>"
                    "</Values></Asset>",
        ))
        self.assertEqual(
            [link.guid for link in workspace.references_from("9100", False)],
            ["1500001"],
        )
        self.assertEqual(
            [link.guid for link in workspace.references_to("1500001", False)],
            ["9100"],
        )

    def test_a_link_to_an_asset_in_a_mods_guid_range_is_found(self):
        """Anno 117 hands mods GUIDs from 1,337,471,142 up, ten digits where
        the shipped games stop at eight: the walk took anything longer than
        nine for an id of another kind, and a second effect added to a shrine
        was never drawn (hands-on, 25 Oct)."""
        loadout = Loadout()
        loadout.set_change(Change(
            guid="9000", asset_label="the_good", path="", tag="Asset",
            op=OP_ADD_NEXT, summary="create asset 2130025000",
            payload="<Asset><Template>Product</Template><Values><Standard>"
                    "<GUID>2130025000</GUID><Name>new_good</Name></Standard>"
                    "</Values></Asset>",
        ))
        loadout.set_change(Change(
            guid="9100", asset_label="maker", path="/Values/Factory7/FactoryInputs",
            tag="Item", op=OP_ADD,
            payload="<Item><Product>2130025000</Product><Amount>1</Amount></Item>",
        ))
        workspace = Workspace(self.index, loadout)
        self.assertEqual(
            {link.guid for link in workspace.references_from("9100", False)},
            {"9000", "2130025000"},
        )
        self.assertEqual(
            [link.guid for link in workspace.references_to("2130025000", False)],
            ["9100"],
        )


class TestReadingSomethingDamaged(unittest.TestCase):
    """What the effective view does with a payload or path it cannot read.

    All of it arrives from a saved loadout or somebody else's mod, so none of
    it is under our control -- and the answer to each has to be "nothing here",
    not an exception out of the middle of a redraw.
    """

    def test_a_payload_step_that_is_not_a_number_has_no_parent(self):
        """Payload paths are dotted indices; a saved one may not be."""
        root = etree.fromstring(b"<Values><A><B/></A></Values>")
        for path in ("A.not-a-number", "A.", "A.1x"):
            with self.subTest(path=path):
                self.assertEqual(effective.payload_parent(root, path), (None, -1))

    def test_payload_text_that_is_not_xml_either_way_reads_as_nothing(self):
        """Tried as a document, then wrapped in one; a lone `<` is neither."""
        for text in ("<never-closed>", "<a></b>", "<"):
            with self.subTest(text=text):
                self.assertIsNone(effective.parse_payload_text(text))

    def test_a_paste_is_one_element_or_nothing(self):
        """The clipboard holds whatever was copied last, from anywhere."""
        block = effective.parse_block("<Item>\n  <Amount>1</Amount>\n</Item>\n")
        self.assertEqual(etree.tostring(block), b"<Item><Amount>1</Amount></Item>")
        for text in ("", "just words", "<A/><B/>", "<never-closed>", "<!-- only -->"):
            with self.subTest(text=text):
                self.assertIsNone(effective.parse_block(text))

    def test_a_path_that_will_not_evaluate_selects_nothing(self):
        element = etree.fromstring(b"<Values><A>1</A></Values>")
        for path in ("/A[", "(((", "/a b c"):
            with self.subTest(path=path):
                self.assertEqual(effective._resolve(element, path), [])


class TestWhatAnAdditionSaysItIsAdding(unittest.TestCase):
    """An op that adds something has to say *what*, in one narrow column.

    "+ new asset" was true of every asset a mod invents and "+ Item (2 fields)"
    of every cost, ingredient and item in the game: both rows told you an
    addition had happened and nothing at all about what it was. What
    distinguishes them is the template being instantiated, or the one reference
    inside the payload -- the good, the item, the building the entry is about.
    """

    @staticmethod
    def creating(payload: str) -> Change:
        return Change(
            guid="1010340", asset_label="anchor", path="", tag="Asset",
            op=OP_ADD_NEXT, payload=payload,
        )

    def test_a_new_asset_says_which_template_it_instantiates(self):
        change = self.creating(
            "<Asset><Template>Product</Template><Values><Standard>"
            "<GUID>1337000001</GUID><Name>marble_deluxe</Name>"
            "</Standard></Values></Asset>"
        )
        self.assertTrue(change.creates_asset)
        self.assertEqual(effective.change_summary(change), "+ new Product")

    def test_and_one_that_follows_another_asset_says_which(self):
        """What "Follow the original" makes.  It read "+ copy of", which is
        the New asset dialog's word for the other kind."""
        change = self.creating(
            "<Asset><BaseAssetGUID>1010340</BaseAssetGUID><Values><Standard>"
            "<GUID>1337000002</GUID><Name>mine</Name></Standard></Values></Asset>"
        )
        names = {"1010340": "Lumberjack"}.get
        self.assertEqual(
            effective.change_summary(change, names), "+ follows Lumberjack"
        )
        # Without anything to ask, the number still beats saying nothing.
        self.assertEqual(effective.change_summary(change), "+ follows 1010340")

    def test_and_one_whose_values_were_copied_once_is_a_new_asset_of_its_template(self):
        """What "Copy its values once" makes: nothing in it names the asset
        the values came from, so the row does not either."""
        change = self.creating(
            "<Asset><Template>FreeAreaBuilding</Template><Values><Standard>"
            "<GUID>1337000004</GUID><Name>copy of lumberjack</Name></Standard>"
            "<FactoryBase><CycleTime>15</CycleTime></FactoryBase></Values></Asset>"
        )
        names = {"1010340": "Lumberjack"}.get
        summary = effective.change_summary(change, names)
        self.assertEqual(summary, "+ new FreeAreaBuilding")
        self.assertNotIn("follows", summary)

    def test_and_one_that_says_neither_is_still_a_new_asset(self):
        change = self.creating(
            "<Asset><Values><Standard><GUID>1337000003</GUID></Standard>"
            "</Values></Asset>"
        )
        self.assertEqual(effective.change_summary(change), "+ new asset")

    def test_a_multi_field_addition_names_the_asset_it_is_about(self):
        change = Change(
            guid="1010340", asset_label="Lumberjack",
            path="/Values/Maintenance/Maintenances", tag="Item", op=OP_ADD,
            payload="<Item><Product>1010196</Product><Amount>2</Amount></Item>",
        )
        names = {"1010196": "Marble"}.get
        self.assertEqual(
            effective.change_summary(change, names), "+ Item Marble (2 fields)"
        )

    def test_a_whole_list_is_counted_by_its_entries_and_not_named_by_the_first(self):
        """H2-30: an Anno 117 merge whose entry no narrow op can name is shown
        as the whole list swapped, and the list's row read "+ Costs Denarii
        (10 fields)" -- as if one entry, Denarii, had been added."""
        change = Change(
            guid="2955", asset_label="Fishing Hut", path="/Values/Cost", tag="Costs",
            op=OP_ADD,
            payload="<Costs><Item><Ingredient>1010017</Ingredient></Item>"
                    "<Item><Ingredient>2174</Ingredient><Amount>5</Amount></Item>"
                    "<Item><Ingredient>2176</Ingredient></Item></Costs>",
        )
        names = {"1010017": "Denarii"}.get
        self.assertEqual(effective.change_summary(change, names), "+ Costs (3 entries)")

    def test_but_only_when_the_number_in_it_is_one_it_recognises(self):
        """Most numbers in a payload are amounts, not references."""
        change = Change(
            guid="1010340", asset_label="Lumberjack", path="/Values/X", tag="Item",
            op=OP_ADD, payload="<Item><Amount>2</Amount><Ratio>4</Ratio></Item>",
        )
        self.assertEqual(
            effective.change_summary(change, lambda guid: ""), "+ Item (2 fields)"
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
