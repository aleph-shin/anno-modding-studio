"""Grouping a change list, and switching changes off without losing them.

The fixture is shaped like the thing the grouping is for: a building, a second
building that is the other culture's version of it, an effect the two share, a
buff that effect applies, a pool that lists both buildings, and a good that
everything in the game costs.  That is the Anno 117 shrine, in miniature.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

import _isolation  # noqa: F401 -- first: keeps the suite out of real files under any runner

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from anno_modding_studio.core import generator, grouping  # noqa: E402
from anno_modding_studio.core import index as index_mod  # noqa: E402
from anno_modding_studio.core.changes import (  # noqa: E402
    OP_ADD_NEXT,
    OP_REMOVE,
    SWITCHED_OFF_MARK,
    Change,
    Loadout,
    ModLayer,
)
from anno_modding_studio.core.workspace import Workspace  # noqa: E402


def asset(guid, name, template, body=""):
    return (
        "<Asset><Template>%s</Template><Values>"
        "<Standard><GUID>%s</GUID><Name>%s</Name></Standard>%s"
        "</Values></Asset>" % (template, guid, name, body)
    )


def cost(good):
    return "<Cost><Costs><Item><Ingredient>%s</Ingredient><Amount>5</Amount></Item></Costs></Cost>" % good


ASSETS = "<AssetList><Assets>%s</Assets></AssetList>" % "".join(
    [
        asset("7000", "the_good", "Product"),
        asset("7001", "money", "Product"),
        # Two shrines, one effect between them, one buff under that effect.
        asset(
            "7100",
            "shrine_roman",
            "Building",
            "<Building><FunctionalEffects><Item><FunctionalEffect>7200"
            "</FunctionalEffect></Item></FunctionalEffects></Building>" + cost("7000"),
        ),
        asset(
            "7101",
            "shrine_celtic",
            "Building",
            "<Building><FunctionalEffects><Item><FunctionalEffect>7200"
            "</FunctionalEffect></Item></FunctionalEffects></Building>" + cost("7000"),
        ),
        asset(
            "7200",
            "shrine_effect",
            "Effect",
            "<Effect><Buffs><Item><GUID>7300</GUID></Item></Buffs></Effect>",
        ),
        asset("7300", "shrine_buff", "BuildingBuff", "<Buff><Value>3</Value></Buff>"),
        # A pool listing both shrines, plus enough other members that its field
        # reads as a catalogue rather than as ownership.
        asset(
            "7400",
            "all_shrines",
            "AssetPool",
            "<AssetPool><AssetList>%s</AssetList></AssetPool>"
            % "".join(
                "<Item><Asset>%d</Asset></Item>" % guid
                for guid in [7100, 7101] + list(range(7500, 7520))
            ),
        ),
        # Something entirely unrelated, to prove groups stay apart.
        asset("7900", "a_road", "Street", cost("7000")),
    ]
    # The pool's other members have to exist for its links to resolve.
    + [asset(str(guid), "filler_%d" % guid, "Building") for guid in range(7500, 7520)]
    # And FunctionalEffects has to be used widely enough that the index
    # recognises it as a link field at all: a field seen twice is not evidence
    # of anything, which is the whole point of how refs.py decides.
    + [
        asset(
            str(7600 + n),
            "other_building_%d" % n,
            "Building",
            "<Building><FunctionalEffects><Item><FunctionalEffect>%d"
            "</FunctionalEffect></Item></FunctionalEffects></Building>" % (7700 + n),
        )
        for n in range(4)
    ]
    + [asset(str(7700 + n), "other_effect_%d" % n, "Effect") for n in range(4)]
)


class GroupingCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        tmp = Path(cls._tmp.name)
        cls.assets = tmp / "assets.xml"
        cls.assets.write_text(ASSETS, encoding="utf-8")
        cls.db = tmp / "index.sqlite"
        index_mod.build_index(cls.assets, cls.db)
        cls.index = index_mod.AssetIndex(cls.db, cls.assets)

    @classmethod
    def tearDownClass(cls):
        cls.index.close()
        cls._tmp.cleanup()

    def setUp(self):
        self.loadout = Loadout()
        self.workspace = Workspace(self.index, self.loadout)

    def edit(self, guid, path="/Values/Standard/Name", value="edited"):
        root = self.index.asset_element(guid)
        found = root.xpath("." + path) if root is not None else []
        change = Change(
            guid=guid,
            asset_label=self.index.display_name(guid),
            path=path,
            tag=path.rsplit("/", 1)[-1],
            old_value=(found[0].text or "").strip() if found else "",
            new_value=value,
        )
        self.loadout.set_change(change)
        return change

    def groups(self):
        return grouping.build(self.loadout, self.workspace)

    def labels(self):
        return sorted(group.label for group in self.groups())


class TestGroupingByAsset(GroupingCase):
    def test_one_group_per_asset(self):
        self.edit("7900")
        self.edit("7000")
        self.assertEqual(len(self.groups()), 2)

    def test_an_asset_s_changes_all_land_in_its_group(self):
        self.edit("7900", "/Values/Standard/Name")
        self.edit("7900", "/Values/Cost/Costs/Item[Ingredient='7000']/Amount", "9")
        groups = self.groups()
        self.assertEqual(len(groups), 1)
        self.assertEqual(len(groups[0].changes), 2)
        self.assertFalse(groups[0].is_cluster)

    def test_without_an_index_it_still_groups_by_asset(self):
        self.edit("7900")
        self.edit("7000")
        self.assertEqual(len(grouping.build(self.loadout, None)), 2)

    def test_a_created_asset_is_filed_under_itself_not_its_anchor(self):
        """The GUID on a creating change says where it is inserted, which is a
        neighbouring asset -- not what it is."""
        self.loadout.set_change(
            Change(
                guid="7900",
                asset_label="invented  (new asset)",
                path="",
                tag="Asset",
                op=OP_ADD_NEXT,
                payload="<Asset><Values><Standard><GUID>8888</GUID>"
                "<Name>invented</Name></Standard></Values></Asset>",
            )
        )
        self.edit("7900")
        groups = self.groups()
        self.assertEqual(len(groups), 2)
        self.assertIn("8888", {group.lead for group in groups})


class TestGroupingRelatedAssets(GroupingCase):
    def test_a_buff_joins_the_effect_that_applies_it(self):
        self.edit("7200")
        self.edit("7300")
        groups = self.groups()
        self.assertEqual(len(groups), 1)
        self.assertTrue(groups[0].is_cluster)
        self.assertEqual(
            sorted(a.guid for a in groups[0].assets), ["7200", "7300"]
        )

    def test_the_whole_shrine_comes_together(self):
        for guid in ("7100", "7101", "7200", "7300"):
            self.edit(guid)
        groups = self.groups()
        self.assertEqual(len(groups), 1, [g.label for g in groups])
        self.assertEqual(len(groups[0].assets), 4)

    def test_it_bridges_an_asset_the_mod_does_not_touch(self):
        """Change the building and the buff underneath it and the effect in
        between is usually left alone -- which should not split them."""
        self.edit("7100")
        self.edit("7300")
        self.assertEqual(len(self.groups()), 1)

    def test_a_good_everything_costs_belongs_to_nobody(self):
        self.edit("7100")
        self.edit("7000")
        self.assertEqual(len(self.groups()), 2)

    def test_being_listed_in_a_pool_is_not_belonging_to_it(self):
        """A catalogue names dozens of assets through one field; that says
        nothing about what any of them is part of."""
        self.edit("7100")
        self.edit("7400")
        self.assertEqual(len(self.groups()), 2)

    def test_unrelated_assets_stay_apart(self):
        self.edit("7100")
        self.edit("7900")
        self.assertEqual(sorted(g.lead for g in self.groups()), ["7100", "7900"])

    def test_the_group_is_named_after_what_the_rest_hangs_off(self):
        """Both shrines are owned by nobody in the group, so "has no owner" is
        no help; the one the others descend from is."""
        for guid in ("7100", "7101", "7200", "7300"):
            self.edit(guid)
        group = self.groups()[0]
        self.assertIn(group.lead, ("7100", "7101"))
        self.assertEqual(group.assets[0].guid, group.lead)

    def test_the_caption_says_what_the_group_holds(self):
        for guid in ("7200", "7300"):
            self.edit(guid)
        self.assertEqual(self.groups()[0].caption(), "2 assets, 2 changes")
        self.loadout.clear()
        self.edit("7900")
        self.assertEqual(self.groups()[0].caption(), "1 change")

    def test_a_catalogue_field_is_told_apart_by_how_wide_it_reaches(self):
        pool = self.index.reference_fanout("/Values/AssetPool/AssetList/Item", "Asset")
        owned = self.index.reference_fanout(
            "/Values/Building/FunctionalEffects/Item", "FunctionalEffect"
        )
        self.assertGreater(pool, grouping.FANOUT_MAX)
        self.assertLessEqual(owned, grouping.FANOUT_MAX)


class TestSwitchingChangesOff(GroupingCase):
    def setUp(self):
        super().setUp()
        self.kept = self.edit("7100", "/Values/Standard/Name", "kept")
        self.dropped = self.edit("7900", "/Values/Standard/Name", "dropped")

    @staticmethod
    def live(xml: str) -> list:
        """The ops of a document that the game reads."""
        from lxml import etree

        return [op for op in etree.fromstring(xml.encode("utf-8")) if isinstance(op.tag, str)]

    def test_a_change_is_on_when_it_is_made(self):
        self.assertTrue(self.kept.active)
        self.assertEqual(self.loadout.active_count, 2)

    def test_switching_one_off_leaves_it_in_the_loadout(self):
        self.dropped.active = False
        self.assertEqual(len(self.loadout), 2)
        self.assertEqual(self.loadout.active_count, 1)
        self.assertEqual([c.guid for c in self.loadout.active()], ["7100"])

    def test_it_is_a_comment_in_the_generated_mod(self):
        self.dropped.active = False
        xml = generator.build_modops_xml(self.loadout)
        self.assertEqual([op.get("GUID") for op in self.live(xml)], ["7100"])
        self.assertIn(SWITCHED_OFF_MARK, xml)
        self.assertIn('GUID="7900"', xml)

    def test_a_switched_off_change_is_not_verified_either(self):
        """It cannot fail a check it is not part of."""
        broken = self.edit("7100", "/Values/Nowhere/AtAll", "x")
        self.assertEqual(
            len([r for r in generator.verify_changes(self.index, self.loadout) if not r.ok]),
            1,
        )
        broken.active = False
        self.assertEqual(
            [r for r in generator.verify_changes(self.index, self.loadout) if not r.ok],
            [],
        )

    def test_the_asset_reads_as_the_mod_will_leave_it(self):
        element = self.workspace.asset_element("7900")
        self.assertEqual(element.findtext("Values/Standard/Name"), "dropped")
        self.dropped.active = False
        element = self.workspace.asset_element("7900")
        self.assertEqual(element.findtext("Values/Standard/Name"), "a_road")

    def test_an_asset_the_mod_no_longer_creates_stops_existing(self):
        self.loadout.set_change(
            Change(
                guid="7900",
                asset_label="invented  (new asset)",
                path="",
                tag="Asset",
                op=OP_ADD_NEXT,
                payload="<Asset><Values><Standard><GUID>8888</GUID>"
                "<Name>invented</Name></Standard></Values></Asset>",
            )
        )
        self.assertTrue(self.workspace.is_new_asset("8888"))
        self.loadout.find("7900", "", OP_ADD_NEXT).active = False
        self.assertFalse(self.workspace.is_new_asset("8888"))

    def test_switching_them_all_off_leaves_a_mod_that_does_nothing(self):
        self.loadout.set_active([c.key for c in self.loadout], False)
        self.assertEqual(self.loadout.active_count, 0)
        xml = generator.build_modops_xml(self.loadout)
        self.assertEqual(self.live(xml), [])
        self.assertEqual(xml.count(SWITCHED_OFF_MARK), 2)

    def test_set_active_reports_only_what_moved(self):
        keys = [c.key for c in self.loadout]
        self.assertEqual(self.loadout.set_active(keys, False), 2)
        self.assertEqual(self.loadout.set_active(keys, False), 0)
        self.assertEqual(self.loadout.set_active(keys, True), 2)


class TestSwitchesSurviveTheLoadoutFile(GroupingCase):
    def test_saved_and_loaded_back(self):
        kept = self.edit("7100")
        dropped = self.edit("7900")
        dropped.active = False
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "mod.annoload"
            self.loadout.save(path)
            raw = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(
                sorted(c["active"] for c in raw["changes"]), [False, True]
            )
            loaded = Loadout.load(path)
        self.assertEqual(len(loaded), 2)
        self.assertEqual(loaded.active_count, 1)
        self.assertEqual([c.guid for c in loaded.active()], [kept.guid])

    def test_history_notices_a_change_being_switched_off(self):
        change = self.edit("7100")
        self.loadout.checkpoint("before")
        self.assertFalse(self.loadout.changed_since_checkpoint())
        change.active = False
        self.assertTrue(self.loadout.changed_since_checkpoint())
        diff = self.loadout.diff_since(self.loadout.latest_checkpoint)
        self.assertEqual(len(diff.modified), 1)

    def test_restoring_a_version_restores_its_switches(self):
        change = self.edit("7100")
        self.loadout.checkpoint("all on")
        change.active = False
        self.loadout.restore(self.loadout.latest_checkpoint)
        self.assertEqual(self.loadout.active_count, 1)


class TestGroupingByMod(GroupingCase):
    """With a mods folder loaded, the outer level of the list is the mods."""

    def setUp(self):
        super().setUp()
        for origin, name in (("b mod", "Bee"), ("a mod", "Ay")):
            self.loadout.set_layer(ModLayer(origin=origin, name=name))
        # A list of mods being played, whose own mod loads after them all.
        self.loadout.own.loads_last = True

    def edit_as(self, origin, guid, value="edited"):
        change = self.edit(guid, value=value)
        self.loadout.changes.pop(change.key, None)
        change.origin = origin
        self.loadout.set_change(change)
        return change

    def sections(self):
        return grouping.by_mod(self.loadout, self.workspace)

    def test_one_section_per_mod_in_load_order(self):
        self.edit_as("b mod", "7100")
        self.edit_as("a mod", "7000")
        self.assertEqual([s.origin for s in self.sections()], ["a mod", "b mod"])
        self.assertEqual([s.label for s in self.sections()], ["Ay", "Bee"])

    def test_a_section_carries_the_ops_its_mod_could_not_be_read_for(self):
        """Each in its own mod's section, in the order the file had them."""
        from anno_modding_studio.core.changes import Unread

        self.edit_as("a mod", "7000")
        self.loadout.unread.extend(
            [
                Unread(origin="a mod", label="second", why="no", order=5),
                Unread(origin="b mod", label="theirs", why="no", order=1),
                Unread(origin="a mod", label="first", why="no", order=2),
            ]
        )
        a_mod, b_mod = self.sections()
        self.assertEqual([entry.label for entry in a_mod.unread], ["first", "second"])
        self.assertEqual([entry.label for entry in b_mod.unread], ["theirs"])

    def test_your_own_mod_is_a_section_too_and_comes_last(self):
        self.edit_as("a mod", "7000")
        self.edit("7100")
        self.assertEqual(
            [s.origin for s in self.sections()], ["a mod", "b mod", ""]
        )
        self.assertEqual(self.sections()[-1].label, self.loadout.own.name)

    def test_but_a_mod_written_to_publish_comes_where_the_game_loads_it(self):
        """``[Gameplay] My Anno Mod`` sorts in front of both, and nothing in
        its modinfo says otherwise."""
        self.loadout.own.loads_last = False
        self.edit_as("a mod", "7000")
        self.edit("7100")
        self.assertEqual(
            [s.origin for s in self.sections()], ["", "a mod", "b mod"]
        )

    def test_a_loaded_mod_that_has_changed_nothing_still_gets_a_row(self):
        """It can still be unloaded, re-read at other settings, or have a patch
        put in front of it, and this list is where those are asked for -- so a
        mod whose ops were all unread, or a patch mod the moment it is made,
        cannot simply be missing."""
        self.edit_as("a mod", "7000")
        empty = [s for s in self.sections() if s.origin == "b mod"]
        self.assertEqual(len(empty), 1)
        self.assertEqual(empty[0].changes, [])

    def test_and_so_does_your_own_mod_once_it_has_been_made(self):
        """An empty row says both that it is there and that nothing is in it.
        It used to be left out while empty, as a heading over nothing, and
        every rule about when was one more thing to know before the list
        could be read.  The only question now is whether it exists."""
        self.edit_as("a mod", "7000")
        self.loadout.make_own_mod()
        last = self.sections()[-1]
        self.assertEqual((last.origin, last.changes), ("", []))

    def test_with_nothing_else_loaded_too(self):
        loadout = Loadout()
        loadout.make_own_mod()
        sections = grouping.by_mod(loadout, self.workspace)
        self.assertEqual([s.origin for s in sections], [""])

    def test_but_nothing_is_there_until_something_makes_it(self):
        """A loadout that has done nothing holds nothing: no "My Anno Mod"
        before anybody asked for one, and none beside the mods read in."""
        self.assertEqual(grouping.by_mod(Loadout(), self.workspace), [])
        self.edit_as("a mod", "7000")
        self.assertNotIn("", [s.origin for s in self.sections()])
        # Writing into it is one of the things that makes it.
        self.edit("7100")
        self.assertEqual(self.sections()[-1].origin, "")

    def test_each_section_groups_its_own_assets_the_usual_way(self):
        """Two shrines and their buff are one thing inside one mod."""
        for guid in ("7100", "7101", "7300"):
            self.edit_as("a mod", guid)
        self.edit_as("b mod", "7000")
        first = self.sections()[0]
        self.assertEqual(len(first.groups), 1)
        self.assertTrue(first.groups[0].is_cluster)
        self.assertEqual(len(first.groups[0].assets), 3)

    def test_and_does_not_reach_into_another_mod_s_changes(self):
        self.edit_as("a mod", "7100")
        self.edit_as("b mod", "7300")
        for section in self.sections():
            self.assertEqual(len(section.groups), 1)
            self.assertFalse(section.groups[0].is_cluster)

    def test_a_section_says_how_much_of_its_mod_could_not_be_read(self):
        """By the ops it lists as unread, whatever number the layer keeps."""
        from anno_modding_studio.core.changes import Unread

        self.loadout.set_layer(ModLayer(origin="a mod", name="Ay", unread=0))
        self.loadout.unread = [
            Unread(origin="a mod", label="op %d" % n, why="no", order=n) for n in range(3)
        ]
        self.edit_as("a mod", "7000")
        self.assertIn("3 unread", self.sections()[0].caption())

    def test_a_section_counts_its_assets_and_changes(self):
        self.edit_as("a mod", "7100")
        self.edit_as("a mod", "7101")
        self.assertEqual(self.sections()[0].caption(), "2 assets, 2 changes")


class TestWritingOneModOutOfAStack(GroupingCase):
    """Each loaded mod goes back to its own folder, as its own mod."""

    def setUp(self):
        super().setUp()
        self.loadout.own.name = "Mine"
        self.loadout.set_layer(
            ModLayer(origin="[Seal] Theirs", name="Theirs", creator="Somebody",
                     category="Gameplay", version="2.1", mod_id="theirs")
        )
        theirs = self.edit("7100", value="from theirs")
        self.loadout.changes.pop(theirs.key, None)
        theirs.origin = "[Seal] Theirs"
        self.loadout.set_change(theirs)
        self.mine = self.edit("7000", value="from mine")

    def test_a_mod_view_holds_only_that_mod_s_changes(self):
        view = generator.for_mod(self.loadout, "[Seal] Theirs")
        self.assertEqual([c.new_value for c in view], ["from theirs"])
        self.assertEqual(view.own.name, "Theirs")
        self.assertEqual(view.own.creator, "Somebody")
        self.assertEqual(view.own.version, "2.1")

    def test_and_your_own_mod_keeps_your_own_details(self):
        view = generator.for_mod(self.loadout, "")
        self.assertEqual([c.new_value for c in view], ["from mine"])
        self.assertEqual(view.own.name, "Mine")

    def test_the_xml_written_for_one_mod_holds_only_its_ops(self):
        xml = generator.build_modops_xml(generator.for_mod(self.loadout, ""))
        self.assertIn("from mine", xml)
        self.assertNotIn("from theirs", xml)

    def test_a_loaded_mod_goes_back_under_the_folder_name_it_came_from(self):
        """That is the name the game keys on, and where its load order comes
        from -- a tidied-up one would quietly move it in the stack."""
        self.assertEqual(
            generator.folder_for(self.loadout, "[Seal] Theirs"), "[Seal] Theirs"
        )

    def test_a_space_in_front_of_its_name_goes_back_with_it(self):
        """P7-26: ``- [Hadley] …`` is a switched-off mod, and what it is called
        when on keeps the space after the mark. Trimmed, it was written to a
        folder beside the mod's own, and sorted somewhere else in the stack."""
        from anno_modding_studio.core import modfolder

        origin = modfolder.bare_name("- [Hadley] Entfernt die Werft")
        self.assertEqual(origin, " [Hadley] Entfernt die Werft")
        self.loadout.set_layer(ModLayer(origin=origin, name="Werft"))
        self.assertEqual(generator.folder_for(self.loadout, origin), origin)
        with tempfile.TemporaryDirectory() as scratch:
            root = generator.write_mod_folder(
                generator.for_mod(self.loadout, origin), Path(scratch), "<ModOps/>",
                folder_name=generator.folder_for(self.loadout, origin),
            )
            self.assertEqual(os.listdir(scratch), [origin])
            self.assertEqual(root.name, origin)

    def test_only_what_windows_cannot_hold_is_changed(self):
        """The characters it forbids, and the spaces and dots it drops from
        the end of a name; a name of nothing else falls back."""
        self.assertEqual(generator.kept_folder_name(" Mod: v1. "), " Mod_ v1")
        self.assertEqual(generator.kept_folder_name("  "), "[Mod] Anno Mod")

    def test_while_words_someone_typed_are_trimmed(self):
        self.assertEqual(generator.safe_folder_name("  My <mod>  "), "My _mod_")

    def test_while_your_own_is_named_from_its_details(self):
        self.loadout.own.category = "Gameplay"
        self.assertEqual(generator.folder_for(self.loadout, ""), "[Gameplay] Mine")

    def shrine(self, **changed) -> str:
        """A mod read from its folder with a modinfo, as the user's own
        *Shrine Rework* was."""
        origin = "[Gameplay] Shrine Rework [Aleph]"
        details = {"name": "Shrine Rework [Aleph]", "category": "Gameplay"}
        details.update(changed)
        self.loadout.set_layer(ModLayer(
            origin=origin, creator="Aleph", version="1.0.0",
            declared={"ModName": {"English": "Shrine Rework [Aleph]"},
                      "Category": {"English": "Gameplay"}},
            **details,
        ))
        return origin

    def test_a_loaded_mod_not_renamed_exports_under_its_own_folder_name(self):
        """Which its author may have numbered to place it in the load order."""
        origin = self.shrine()
        self.assertFalse(generator.renamed(self.loadout, origin))
        self.assertEqual(generator.export_folder_for(self.loadout, origin), origin)

    def test_renamed_it_exports_under_its_new_name(self):
        """Renaming a mod changed its modinfo and neither its folder nor its
        zip, and nothing else on the Export tab named either."""
        origin = self.shrine(name="Flavourful Shrine Mechanics (Aleph)")
        self.assertTrue(generator.renamed(self.loadout, origin))
        self.assertEqual(generator.export_folder_for(self.loadout, origin),
                         "[Gameplay] Flavourful Shrine Mechanics (Aleph)")

    def test_and_given_another_category_it_follows_that(self):
        origin = self.shrine(category="Balancing")
        self.assertEqual(generator.export_folder_for(self.loadout, origin),
                         "[Balancing] Shrine Rework [Aleph]")

    def test_while_play_keeps_the_folder_the_mods_folder_holds_it_under(self):
        """Its switches and its link are keyed on that name."""
        origin = self.shrine(name="Flavourful Shrine Mechanics (Aleph)")
        self.assertEqual(generator.folder_for(self.loadout, origin), origin)

    def test_a_mod_with_no_modinfo_is_called_what_its_folder_is(self):
        """So that is the name it is renamed from."""
        self.loadout.set_layer(ModLayer(origin="Old Thing", name="Old Thing"))
        self.assertEqual(generator.export_folder_for(self.loadout, "Old Thing"), "Old Thing")
        self.loadout.set_layer(ModLayer(origin="Old Thing", name="New Thing", category="Gameplay"))
        self.assertEqual(generator.export_folder_for(self.loadout, "Old Thing"),
                         "[Gameplay] New Thing")

    def test_a_modinfo_that_names_nothing_leaves_it_named_after_its_folder(self):
        self.loadout.set_layer(ModLayer(origin="Old Thing", name="Old Thing",
                                        declared={"ModID": "old-thing"}))
        self.assertFalse(generator.renamed(self.loadout, "Old Thing"))

    def test_a_mod_bundled_inside_another_keeps_its_place_in_it(self):
        self.loadout.set_layer(ModLayer(origin="Host/bits", name="Renamed bits",
                                        declared={"ModName": "Bits"}))
        self.assertEqual(generator.export_folder_for(self.loadout, "Host/bits"), "Host/bits")

    def test_a_switched_off_change_is_left_out_of_the_mod_it_belongs_to(self):
        self.mine.active = False
        view = generator.for_mod(self.loadout, "")
        self.assertEqual(len(view), 1)
        self.assertEqual(view.active_count, 0)


class TestWhatPointsAtThis(GroupingCase):
    """The live half of the reference graph, and what it costs to ask.

    With a mods folder loaded this is asked of a thousand assets every time the
    changes list redraws, so it is answered by bounding the question rather than
    by walking every asset the session touches -- and the bounding has to give
    exactly the same answer the walk would.
    """

    def pointing_at(self, guid):
        return sorted(link.guid for link in self.workspace.references_to(guid))

    def test_the_shipped_answer_is_the_starting_point(self):
        self.assertEqual(self.pointing_at("7200"), ["7100", "7101"])

    def repoint(self):
        """Aim one shrine's functional effect somewhere else."""
        self.loadout.set_change(
            Change(
                guid="7100", asset_label="shrine",
                path="/Values/Building/FunctionalEffects/Item/FunctionalEffect",
                tag="FunctionalEffect", old_value="7200", new_value="7300",
            )
        )

    def test_a_link_a_change_adds_is_found(self):
        self.repoint()
        self.assertIn("7100", self.pointing_at("7300"))

    def test_a_link_a_change_moves_away_is_gone(self):
        self.repoint()
        self.assertEqual(self.pointing_at("7200"), ["7101"])

    def test_and_so_is_one_a_change_strikes_out(self):
        """The editor keeps a removed node, struck through, so it can be put
        back -- but a link inside one is a link the game no longer follows."""
        self.loadout.set_change(
            Change(
                guid="7100", asset_label="shrine",
                path="/Values/Building/FunctionalEffects/Item[FunctionalEffect='7200']",
                tag="Item", op=OP_REMOVE, old_value="",
            )
        )
        self.assertEqual(self.pointing_at("7200"), ["7101"])

    def test_a_link_into_an_asset_the_session_invents_is_found(self):
        """Nothing shipped can point at it, so the only possible source is a
        change that writes the number down."""
        self.loadout.set_change(
            Change(
                guid="7100", asset_label="anchor", path="", tag="Asset",
                op=OP_ADD_NEXT, summary="create asset 7777",
                payload="<Asset><Template>Buff</Template><Values><Standard>"
                        "<GUID>7777</GUID><Name>made_up</Name></Standard>"
                        "</Values></Asset>",
            )
        )
        self.loadout.set_change(
            Change(
                guid="7200", asset_label="effect",
                path="/Values/Effect/Buffs/Item/GUID", tag="GUID",
                old_value="7300", new_value="7777",
            )
        )
        self.assertIn("7200", self.pointing_at("7777"))

    def test_editing_one_asset_does_not_change_the_answer_about_another(self):
        """The regression: one typed value used to throw away every cached
        walk, so a redraw re-derived the whole graph."""
        before = self.pointing_at("7200")
        self.assertTrue(before)
        self.edit("7900", path="/Values/Standard/Name", value="renamed")
        self.assertEqual(self.pointing_at("7200"), before)

    def test_and_the_walks_held_for_other_assets_are_kept(self):
        self.pointing_at("7200")
        self.workspace._live_links("7100", True, True)
        held = dict(self.workspace._live_cache)
        self.edit("7900", path="/Values/Standard/Name", value="renamed")
        self.workspace._sync()
        self.assertIn(("7100", True, True), self.workspace._live_cache)
        self.assertNotIn(("7900", True, True), self.workspace._live_cache)
        self.assertTrue(held)


class TestTheOwnershipCacheOutlivesARedraw(GroupingCase):
    """Which assets belong together is a property of the base game."""

    def test_a_shipped_answer_is_kept_across_refreshes(self):
        owners = grouping.Owners(self.workspace, ())
        first = owners.of("7300")
        owners.refresh(())
        self.assertEqual(owners._shipped["7300"], first)

    def test_but_an_invented_one_is_asked_again(self):
        owners = grouping.Owners(self.workspace, {"7300"})
        owners.of("7300")
        self.assertIn("7300", owners._live)
        owners.refresh({"7300"})
        self.assertEqual(owners._live, {})

    def test_a_lookup_that_failed_is_asked_again_after_a_redraw(self):
        """P3-3: "no owners" for that drawing of the panel, not for good."""

        class Flaky:
            def __init__(self, view):
                self.view, self.asked = view, 0

            def shipped_references_to(self, guid):
                self.asked += 1
                if self.asked == 1:
                    raise OSError("the index was busy")
                return self.view.shipped_references_to(guid)

            def reference_fanout(self, path, tag):
                return self.view.reference_fanout(path, tag)

        view = Flaky(self.workspace)
        owners = grouping.Owners(view, ())
        self.assertEqual(owners.of("7300"), ())
        self.assertEqual(owners.of("7300"), ())
        self.assertEqual(view.asked, 1, "not asked again within one drawing")
        owners.refresh(())
        found = owners.of("7300")
        self.assertTrue(found)
        self.assertEqual(found, grouping.Owners(self.workspace, ()).of("7300"))
        self.assertEqual(view.asked, 2)
        self.assertIn("7300", owners._shipped)

    def test_the_panel_hands_the_same_cache_back_in(self):
        owners = grouping.owners_for(self.workspace, None)
        again = grouping.owners_for(self.workspace, owners)
        self.assertIs(again, owners)

    def test_a_cache_of_another_view_is_not_handed_back(self):
        """A8-4: the game files were adopted again, and the cache a panel was
        keeping asks the workspace that went with the old ones."""
        owners = grouping.owners_for(self.workspace, None)
        owners.of("7300")
        other = Workspace(self.index, self.loadout)
        again = grouping.owners_for(other, owners)
        self.assertIsNot(again, owners)
        self.assertIs(again.view, other)
        self.assertEqual(again._shipped, {})


class TestNamingAGroup(GroupingCase):
    """Named for what its assets share, never passed off as one of them."""

    class View:
        def __init__(self, names):
            self.names = names

        def asset_row(self, guid):
            from types import SimpleNamespace

            display, name = self.names[guid]
            return SimpleNamespace(display=display, name=name)

    @staticmethod
    def lead(guid):
        return grouping.AssetChanges(guid, "label " + guid, ())

    def test_assets_sharing_a_name_in_game_are_called_by_it(self):
        view = self.View({
            "1": ("Shrine of Epona", "Public Roman Shrine Epona"),
            "2": ("Shrine of Epona", "Attribute Buff Epona Shrine"),
        })
        self.assertEqual(
            grouping._cluster_name(view, ["1", "2"], self.lead("1")), "Shrine of Epona"
        )

    def test_otherwise_it_says_there_is_more_to_it_than_the_lead(self):
        view = self.View({
            "1": ("Shrine of Epona", "a"), "2": ("", "Buff"), "3": ("Other", "c"),
        })
        self.assertEqual(
            grouping._cluster_name(view, ["1", "2", "3"], self.lead("1")),
            "Shrine of Epona + 2 related",
        )

    def test_a_single_asset_keeps_its_own_label(self):
        self.edit("7900")
        group = self.groups()[0]
        self.assertEqual(group.label, group.assets[0].label)

    def test_a_cluster_of_differently_named_assets_says_so(self):
        for guid in ("7100", "7101", "7200", "7300"):
            self.edit(guid)
        self.assertTrue(self.groups()[0].label.endswith("+ 3 related"))


if __name__ == "__main__":
    unittest.main()
