"""Assets created under a GUID something else already has, and which one the
game finds: the first in the file (measured, both games -- see
``test_modio_loader``)."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

import _isolation  # noqa: F401 -- first: keeps the suite out of real files under any runner

sys.path.insert(0, str(Path(__file__).resolve().parent))

from support import SessionCase  # noqa: E402

from anno_modding_studio.core import conflicts, repeats  # noqa: E402
from anno_modding_studio.core.changes import Loadout, ModLayer  # noqa: E402


def make(guid: str, name: str, beside: str = "1000") -> str:
    return (
        '<ModOp Type="addNextSibling" GUID="%s"><Asset><Template>Thing</Template><Values>'
        "<Standard><GUID>%s</GUID><Name>%s</Name></Standard></Values></Asset></ModOp>"
        % (beside, guid, name)
    )


def before(guid: str, name: str, anchor: str = "1001") -> str:
    """The same, put in before the asset it names."""
    return make(guid, name, anchor).replace("addNextSibling", "addPrevSibling")


class RepeatCase(SessionCase):
    def loadout(self, **mods) -> Loadout:
        """The folder read into a loadout the way the window reads it,
        unread ops and all."""
        session = self.session(self.mods(**mods))
        loadout = Loadout()
        for read in session.reads:
            loadout.set_layer(ModLayer(origin=read.origin, name=read.name, order=read.order))
            for op in read.report.ops:
                loadout.set_op(op)
            for change in read.changes:
                loadout.set_change(change)
            loadout.unread = loadout.unread + list(read.report.unread(read.origin))
        return loadout

    def found(self, **mods):
        loadout = self.loadout(**mods)
        return loadout, repeats.find(loadout, self.index)


class TestFound(RepeatCase):
    def test_one_mod_twice(self):
        """*Local Department for Arctic Enbesa Horai*: two decree buffs, one
        GUID, in two compatibility sections that both always run."""
        loadout, found = self.found(a__mod=make("7777", "one", "1002") + make("7777", "two", "1002"))
        (repeat,) = found
        self.assertEqual(repeat.guid, "7777")
        self.assertEqual(repeat.origins, ("a mod",))
        self.assertEqual(repeats.mark(found, "a mod"), "1 GUID taken twice")
        (line,) = repeats.describe(found, "a mod", lambda origin: origin)[1:]
        self.assertIn("twice in this mod", line)

    def test_the_game_finds_the_first_in_the_file_not_the_first_made(self):
        """Made first beside the last asset, then beside the first: the second
        stands first in the file, and that is the one the loader finds.
        *Cultural Expeditions* makes its balancing asset first, and the game
        finds the trip's name its bundled sub-mod made."""
        _loadout, (repeat,) = self.found(a__mod=make("7777", "made first", "1002") + make("7777", "made second", "1000"))
        self.assertEqual([copy.name for copy in repeat.copies], ["made second", "made first"])
        self.assertEqual(repeat.found, "made second")

    def test_beside_one_asset_the_later_op_stands_nearer(self):
        """Each ``addNextSibling`` goes straight after the asset it names, so
        the second one beside it comes between them -- across mods too."""
        _loadout, (repeat,) = self.found(a__one=make("7777", "one"), b__two=make("7777", "two"))
        self.assertEqual(repeat.found, "two")
        self.assertEqual(repeat.origins, ("b two", "a one"))

    def test_put_in_before_one_asset_the_earlier_op_stands_first(self):
        """Each ``addPrevSibling`` goes straight before the asset it names, so
        the second lands after the first. *Merchants* makes two Marble Stores
        under one GUID that way, and the game finds the first made."""
        _loadout, (repeat,) = self.found(a__mod=before("7777", "one") + before("7777", "two"))
        self.assertEqual(repeat.found, "one")
        self.assertEqual([copy.name for copy in repeat.copies], ["one", "two"])

    def test_a_guid_the_game_already_has(self):
        _loadout, (repeat,) = self.found(a__mod=make("1001", "mine", "1002"))
        self.assertEqual(repeat.shipped, "beta")
        self.assertEqual(repeat.found, "the base game's beta")

    def test_unless_it_is_put_in_ahead_of_the_shipped_one(self):
        _loadout, (repeat,) = self.found(a__mod=make("1001", "mine", "1000"))
        self.assertTrue(repeat.copy_first)
        self.assertEqual(repeat.found, "mine")


class TestNotWarnedAbout(RepeatCase):
    def test_a_mod_that_removes_all_but_the_last_itself(self):
        """*A_Modified_Ornaments_Tab* adds a placeholder and then its asset, and
        removes all but the last copy of that GUID -- its own and any other
        mod's. Said, but as tidied away rather than warned about."""
        tidy = """<ModOp Type="remove" Path="//Asset[Values/Standard/GUID='7777'][position() &lt; last()]" />"""
        loadout, (repeat,) = self.found(a__mod=make("7777", "duplicate") + make("7777", "real") + tidy)
        self.assertTrue(repeat.tidied)
        self.assertEqual(repeats.mark([repeat], "a mod"), "")
        self.assertEqual(repeats.summarise([repeat]), "")
        self.assertIn("removes all but the last", repeats.describe([repeat], "a mod", str)[1])

    TIDY = """<ModOp Type="remove" Path="//Asset[Values/Standard/GUID='7777'][position() &lt; last()]" />"""

    def test_but_not_when_the_tidy_runs_before_the_copies(self):
        """It removes what is there when it runs. Run first, it removed
        nothing, and was still taken to have tidied up -- naming the last
        copy as the one found and warning of nothing."""
        _loadout, (repeat,) = self.found(a__mod=self.TIDY + make("7777", "one") + make("7777", "two"))
        self.assertFalse(repeat.tidied)
        self.assertEqual(repeat.found, "two")
        self.assertEqual(repeats.mark([repeat], "a mod"), "1 GUID taken twice")

    def test_nor_for_a_copy_made_after_it(self):
        """The copy a later mod makes is still there, and stands first."""
        loadout, (repeat,) = self.found(
            a__mod=make("7777", "one") + make("7777", "two") + self.TIDY, b__mod=make("7777", "three"))
        self.assertFalse(repeat.tidied)
        self.assertEqual([copy.name for copy in repeat.copies], ["three", "one"])
        self.assertEqual(repeat.found, "three")
        standing = repeats.standing(loadout, self.index)["7777"]
        self.assertEqual(effective_name(standing.change), "three")
        self.assertEqual(standing.others, 1)

    def test_the_same_asset_each_time(self):
        """Kurila's shared sub-mods each carry the same description asset:
        whichever copy the game finds, it is that one."""
        loadout, (repeat,) = self.found(a__one=make("7777", "shared"), b__two=make("7777", "shared"))
        self.assertTrue(repeat.same)
        self.assertEqual(repeats.mark([repeat], "a one"), "")
        self.assertEqual(repeats.summarise([repeat]), "")
        self.assertIn("the same asset each time", repeats.describe([repeat], "a one", str)[1])

    def test_an_asset_taken_out_to_put_another_in_its_place(self):
        swap = '<ModOp Type="remove" GUID="1001" />'
        _loadout, found = self.found(a__mod=swap + make("1001", "instead", "1002"))
        self.assertEqual(found, [])

    def test_a_mod_that_does_not_load(self):
        loadout = self.loadout(a__one=make("7777", "one"), b__two=make("7777", "two"))
        loadout.layer("b two").active = False
        self.assertEqual(repeats.find(loadout, self.index), [])

    def test_one_asset_under_a_guid_of_its_own(self):
        self.assertEqual(self.found(a__mod=make("7777", "only"))[1], [])


class TestSaid(RepeatCase):
    def test_a_bundle_s_row_says_what_its_sub_mods_take_twice(self):
        """*Extended Agriculture Modules*' farm and animal modules each make
        1500300934; folded, the bundle's row is all there is to see."""
        bundle = "[Seal] Extended Agriculture Modules"
        found = [repeats.Repeat("1500300934", (repeats.Copy(bundle + "/farm", "silo trigger"),
                                               repeats.Copy(bundle + "/animal", "barn trigger")))]
        self.assertEqual(repeats.mark(found, bundle), "1 GUID taken twice")
        self.assertEqual(repeats.mark(found, bundle + "/farm"), "1 GUID taken twice")
        self.assertEqual(repeats.mark(found, "[Seal] Extended"), "")
        (line,) = repeats.describe(found, bundle, str)[1:]
        self.assertEqual(line, "  GUID 1500300934 (silo trigger / barn trigger): twice in this mod, "
                               "the game finds only silo trigger")

    def test_on_the_changes_panel_s_conflicts_line(self):
        loadout, found = self.found(a__one=make("7777", "one"), b__two=make("7777", "two"))
        self.assertIn("1 GUID taken twice", conflicts.summarise(loadout, str, [], found))


class TestBeforePlay(RepeatCase):
    """Said on the card of what loads and in the Play confirmation, where
    which asset the game finds is about to matter."""

    def test_one_sentence_each_naming_who_and_what_is_found(self):
        _loadout, found = self.found(a__one=make("7777", "one"), b__two=make("7777", "two"),
                                     c__mine=make("1001", "mine", "1002"),
                                     d__self=make("8888", "x", "1002") + make("8888", "y", "1002"))
        self.assertEqual(sorted(repeats.before_play(found, str)), [
            "GUID 1001 is created by the base game and c mine. The game finds only the base game's beta.",
            "GUID 7777 is created by b two and a one. The game finds only two.",
            "GUID 8888 is created twice by d self. The game finds only y.",
        ])

    def test_two_mods_sharing_many_are_one_sentence(self):
        """*Trade Union Rebalance* and *Extended Agriculture Modules* share 17."""
        _loadout, found = self.found(
            a__one="".join(make(guid, "a%s" % guid) for guid in ("7771", "7772", "7773", "7774", "7775")),
            b__two="".join(make(guid, "b%s" % guid) for guid in ("7771", "7772", "7773", "7774", "7775")))
        (line,) = repeats.before_play(found, str)
        self.assertEqual(line, "5 GUIDs are created by b two and a one (7771, 7772, 7773, 7774, …). "
                               "The game finds only one of each.")

    def test_not_one_that_does_no_harm_nor_one_only_elsewhere_on_this_pc(self):
        _loadout, found = self.found(a__one=make("7777", "shared"), b__two=make("7777", "shared"))
        self.assertEqual(repeats.before_play(found, str), [])
        elsewhere = repeats.Repeat("9999", (repeats.Copy("a one", "mine"),), elsewhere=("Other Mod",))
        self.assertEqual(repeats.before_play([elsewhere], str), [])

    def test_the_card_says_one_and_counts_more(self):
        """The sentences were most of the card on a big loadout: past one,
        it says how many GUIDs, and the list is behind its button."""
        from anno_modding_studio.core import truth

        found = truth.Truth(game="anno1800", folder="")
        self.assertEqual(found.describe_repeats(), "")
        found.repeats = ["one."]
        self.assertEqual(found.describe_repeats(), "GUIDs taken twice by what loads:\n• one.")
        found.repeats = ["one.", "two.", "three.", "four."]
        found.repeated = 9
        self.assertEqual(found.describe_repeats(), "9 GUIDs are taken twice by what loads. The game "
                                                   "finds only one of the assets under each.")

    def test_the_card_does_not_count_its_sentences_as_guids(self):
        """A sentence is a set of mods, which can share any number: five
        shared by two mods were headed "1 GUID taken twice"."""
        from anno_modding_studio.core import truth

        guids = ("7771", "7772", "7773", "7774", "7775")
        _loadout, held = self.found(a__one="".join(make(guid, "a%s" % guid) for guid in guids),
                                    b__two="".join(make(guid, "b%s" % guid) for guid in guids))
        found = truth.Truth(game="anno1800", folder="")
        found.repeats = repeats.before_play(held, str)
        self.assertEqual(repeats.summarise(held), "5 GUIDs taken twice")
        self.assertEqual(found.describe_repeats(),
                         "GUIDs taken twice by what loads:\n• 5 GUIDs are created by b two and a one "
                         "(7771, 7772, 7773, 7774, …). The game finds only one of each.")


class TestTheReading(RepeatCase):
    """What a later op aimed at a repeated GUID edits, as the Studio reads it:
    the one first in the file, as the loader does -- not the first made."""

    EDIT = '<ModOp Type="add" GUID="%s" Path="/Values/Standard"><Edited>1</Edited></ModOp>'

    def payloads(self, loadout):
        from anno_modding_studio.core import effective

        return {
            effective.new_asset_name(change): change.payload
            for change in loadout if change.creates_asset
        }

    def test_the_copy_first_in_the_file_takes_the_edit(self):
        held = self.payloads(self.loadout(
            a__mod=make("7777", "made first", "1002") + make("7777", "made second", "1000") + self.EDIT % "7777"
        ))
        self.assertIn("<Edited>", held["made second"])
        self.assertNotIn("<Edited>", held["made first"])

    def test_and_the_first_made_where_it_stands_first(self):
        held = self.payloads(self.loadout(
            a__mod=make("7777", "made first", "1000") + make("7777", "made second", "1002") + self.EDIT % "7777"
        ))
        self.assertIn("<Edited>", held["made first"])
        self.assertNotIn("<Edited>", held["made second"])

    def test_two_put_in_before_one_asset_the_first_made_takes_the_edit(self):
        held = self.payloads(self.loadout(
            a__mod=before("7777", "made first") + before("7777", "made second") + self.EDIT % "7777"
        ))
        self.assertIn("<Edited>", held["made first"])
        self.assertNotIn("<Edited>", held["made second"])

    def test_and_each_goes_back_out_on_the_side_it_came_in(self):
        """Written back as after the asset, the second Marble Store came
        first in the export, and the game found the other one."""
        from anno_modding_studio.core import generator

        loadout = self.loadout(a__mod=before("7777", "one") + make("7778", "two"))
        made = {change.payload.count("<GUID>7777</GUID>"): change.op
                for change in loadout if change.creates_asset}
        self.assertEqual(made, {1: "addPrevSibling", 0: "addNextSibling"})
        written = generator.build_modops_xml(loadout)
        self.assertIn('<ModOp Type="addPrevSibling" GUID="1001">', written)
        self.assertIn('<ModOp Type="addNextSibling" GUID="1000">', written)

    def test_a_shipped_asset_ahead_of_the_copy_takes_the_edit(self):
        """A copy of *beta* put in after it: the game's own stays the one
        found, and the edit is a ModOp on it -- where it used to go into the
        copy, which nothing reaches."""
        loadout = self.loadout(a__mod=make("1001", "copy", "1002") + self.EDIT % "1001")
        self.assertNotIn("<Edited>", self.payloads(loadout)["copy"])
        (edit,) = [change for change in loadout if not change.creates_asset]
        self.assertEqual((edit.guid, edit.op), ("1001", "add"))

    def test_and_a_copy_put_in_ahead_of_it_takes_it(self):
        loadout = self.loadout(a__mod=make("1001", "copy", "1000") + self.EDIT % "1001")
        self.assertIn("<Edited>", self.payloads(loadout)["copy"])


class TestTheView(RepeatCase):
    """What the asset view shows of a repeated GUID: the asset the game finds,
    as the reading above has it, with the edit aimed at the GUID -- not the
    copy made last (#151)."""

    EDIT = TestTheReading.EDIT

    def view(self, **mods):
        from anno_modding_studio.core.workspace import Workspace

        loadout = self.loadout(**mods)
        return loadout, Workspace(self.index, loadout)

    def shown(self, workspace, guid):
        """``(name, new, edited)`` of the asset the view shows."""
        row = workspace.asset_row(guid)
        element = workspace.effective_asset(guid).element
        return row.name, row.is_new, element.find("Values/Standard/Edited") is not None

    def panel(self, loadout, workspace, guid):
        from PySide6.QtWidgets import QApplication

        from anno_modding_studio.ui.editor_panel import EditorPanel

        self.app = QApplication.instance() or QApplication([])
        panel = EditorPanel(loadout)
        self.addCleanup(panel.deleteLater)
        panel.set_index(workspace)
        self.assertTrue(panel.load_asset(guid))
        return panel.subtitle_label.text(), panel.subtitle_label.toolTip()

    def test_the_copy_first_in_the_file(self):
        _loadout, workspace = self.view(
            a__mod=make("7777", "made first", "1002") + make("7777", "made second", "1000") + self.EDIT % "7777")
        self.assertEqual(self.shown(workspace, "7777"), ("made second", True, True))

    def test_and_the_first_made_where_it_stands_first(self):
        loadout, workspace = self.view(
            a__mod=make("7777", "made first", "1000") + make("7777", "made second", "1002") + self.EDIT % "7777")
        self.assertEqual(self.shown(workspace, "7777"), ("made first", True, True))
        self.assertEqual(workspace.base_element("7777").findtext("Values/Standard/Name"), "made first")
        self.assertEqual(workspace.new_asset_rows()[0].name, "made first")
        self.assertEqual([guid for guid, _element in workspace.invented_assets()], ["7777"])
        said, tip = self.panel(loadout, workspace, "7777")
        self.assertIn("1 other asset has this GUID", said)
        self.assertIn("the game finds this one", said)
        self.assertIn("in the file: made first (a mod) and made second (a mod).", tip)

    def test_two_put_in_before_one_asset(self):
        _loadout, workspace = self.view(
            a__mod=before("7777", "made first") + before("7777", "made second") + self.EDIT % "7777")
        self.assertEqual(self.shown(workspace, "7777"), ("made first", True, True))

    def test_a_copy_of_a_shipped_asset_behind_it(self):
        """The game's own *beta* is found, and is shown with the edit; the copy
        is not an asset the mod invents, though the mod still makes it."""
        loadout, workspace = self.view(a__mod=make("1001", "copy", "1002") + self.EDIT % "1001")
        self.assertEqual(self.shown(workspace, "1001"), ("beta", False, True))
        self.assertFalse(workspace.is_new_asset("1001"))
        self.assertIsNone(workspace.creator_for("1001"))
        self.assertNotIn("1001", workspace.new_asset_guids())
        self.assertEqual(workspace.invented_assets(), [])
        mine = workspace.creator_for("1001", "a mod")
        self.assertEqual(effective_name(mine), "copy")
        self.assertIsNone(workspace.creator_for("1001", "another mod"))
        self.assertEqual(workspace.creator_changes(), [mine])
        said, tip = self.panel(loadout, workspace, "1001")
        self.assertNotIn("created by this mod", said)
        self.assertIn("1 other asset has this GUID", said)
        self.assertIn("the game finds the one it ships, shown here", said)
        self.assertIn("copy (a mod)", tip)
        self.assertIn("The base game ships one as well.", tip)

    def test_a_copy_put_in_ahead_of_the_shipped_one(self):
        loadout, workspace = self.view(a__mod=make("1001", "copy", "1000") + self.EDIT % "1001")
        self.assertEqual(self.shown(workspace, "1001"), ("copy", True, True))
        said, _tip = self.panel(loadout, workspace, "1001")
        # Seen from the loadout's own mod, which does not make it (A11-82).
        self.assertIn("created by a mod", said)
        self.assertNotIn("created by this mod", said)
        self.assertIn("the game finds this one, put in ahead of the one it ships", said)

    def test_two_mods_the_later_one_found_is_not_there_for_the_earlier(self):
        """Edited into the first mod, the asset is that mod's copy: the one the
        game finds is made by a mod that loads later."""
        _loadout, workspace = self.view(a__one=make("7777", "one"), b__two=make("7777", "two"))
        self.assertEqual(workspace.asset_row("7777").name, "two")
        early = workspace.effective_asset("7777", upto="a one").element
        self.assertEqual(early.findtext("Values/Standard/Name"), "one")
        self.assertEqual(workspace.creator_for("7777", "a one").origin, "a one")
        self.assertEqual(workspace.creator_for("7777", "b two").origin, "b two")

    def test_one_copy_is_as_it_was(self):
        _loadout, workspace = self.view(a__mod=make("7777", "only") + self.EDIT % "7777")
        self.assertEqual(self.shown(workspace, "7777"), ("only", True, True))
        self.assertIsNone(workspace.standing_of("7777"))


def repeats_generator():
    from anno_modding_studio.core import generator

    return generator


def effective_name(change):
    from anno_modding_studio.core import effective

    return effective.new_asset_name(change)


class TestOnThisPC(RepeatCase):
    """A mod read into the loadout is played beside every other mod on this
    PC, not only the ones loaded with it: a GUID one of those creates too is
    said as well, without claiming which the game would find."""

    RELPATH = Path("data/config/export/main/asset/assets.xml")

    def setUp(self):
        super().setUp()
        import tempfile

        self._pc = tempfile.TemporaryDirectory()
        self.addCleanup(self._pc.cleanup)
        self.pc = Path(self._pc.name)

    def installed(self, name: str, guid: str, mod_id: str = "") -> Path:
        folder = self.pc / name
        patch = folder / self.RELPATH
        patch.parent.mkdir(parents=True)
        patch.write_text("<ModOps>%s</ModOps>" % make(guid, name), encoding="utf-8")
        if mod_id:
            (folder / "modinfo.json").write_text('{"ModID": "%s"}' % mod_id, encoding="utf-8")
        return folder

    def owners(self):
        from anno_modding_studio.core import discovery

        return discovery.guid_owners(discovery.mods_in(self.pc, discovery.WHERE_MODS), self.RELPATH)

    def test_every_mod_that_makes_a_guid_is_named_with_its_mod_id(self):
        self.installed("Other", "7777", "other-mod")
        self.installed("Another", "7777")
        found = self.owners()["7777"]
        self.assertEqual([(owner.name, owner.mod_id) for owner in found], [("Another", ""), ("Other", "other-mod")])

    def test_a_guid_a_mod_on_this_pc_makes_too_is_said(self):
        self.installed("Other", "7777")
        loadout = self.loadout(a__mod=make("7777", "mine"))
        (repeat,) = repeats.find(loadout, self.index, self.owners())
        self.assertEqual(repeat.elsewhere, ("Other, in your mods folder",))
        self.assertEqual(repeats.mark([repeat], "a mod"), "1 GUID taken twice")
        line = repeats.describe([repeat], "a mod", str)[1]
        self.assertIn("also made by Other, in your mods folder, on this PC and not in this loadout", line)
        self.assertIn("If both load, the game finds only one", line)

    def test_but_not_the_mod_itself_nor_another_copy_of_it(self):
        """Read from its folder, the loaded mod is on this PC too, and so is a
        second copy -- the library's, mod.io's -- under the same ModID."""
        loaded = self.installed("Mine", "7777", "mine-id")
        self.installed("Mine copy", "7777", "MINE-ID")
        loadout = self.loadout(a__mod=make("7777", "mine"))
        layer = loadout.layer("a mod")
        layer.folder = str(loaded)
        layer.mod_id = "mine-id"
        self.assertEqual(repeats.find(loadout, self.index, self.owners()), [])

    def test_nor_the_library_build_of_a_mod_of_yours(self):
        """R3-4, hands-on run 3: its Mod ID left blank, its build in the
        library was "also made by" it."""
        from anno_modding_studio.core import modinfo, projects

        loadout = self.loadout(a__mod=make("7777", "mine"))
        layer = loadout.layer("a mod")
        layer.name, layer.mod_id, layer.folder = "Run Three Ale", "", str(self.pc / "not linked")
        loadout.projects["a mod"] = projects.Ref("projects/Run Three Ale.annoproj")
        built_as = modinfo.written_mod_id(repeats_generator().for_mod(loadout, "a mod"))
        self.assertTrue(built_as)
        self.installed("[Gameplay] Run Three Ale", "7777", built_as)
        self.assertEqual(repeats.find(loadout, self.index, self.owners()), [])
        # Somebody else's under another ModID is still said.
        self.installed("Other", "7777", "other-id")
        (repeat,) = repeats.find(loadout, self.index, self.owners())
        self.assertEqual(repeat.elsewhere, ("Other, in your mods folder",))

    def test_another_mod_in_two_places_is_named_once(self):
        """The mods folder and mod.io's downloads often both hold a mod."""
        self.installed("Other", "7777", "other-id")
        self.installed("Other copy", "7777", "other-id")
        (repeat,) = repeats.find(self.loadout(a__mod=make("7777", "mine")), self.index, self.owners())
        self.assertEqual(len(repeat.elsewhere), 1)

    def test_the_panel_says_it_once_the_scan_is_in(self):
        from PySide6.QtWidgets import QApplication

        from anno_modding_studio.core.workspace import Workspace
        from anno_modding_studio.ui import changes_dock

        QApplication.instance() or QApplication([])
        self.installed("Other", "7777")
        loadout = self.loadout(a__mod=make("7777", "mine"))
        panel = changes_dock.ChangesPanel(loadout)
        self.addCleanup(panel.deleteLater)
        panel.set_index(Workspace(self.index, loadout))
        self.assertEqual(panel.elsewhere_of("a mod"), [])
        panel.set_elsewhere(self.owners())
        self.assertEqual(len(panel.elsewhere_of("a mod")), 1)
        self.assertIn("1 GUID taken twice", panel.conflicts_button.text())

    def test_the_conflicts_dialog_counts_and_lists_what_the_panel_does(self):
        """H2-9: the panel's button counted a GUID a mod outside the loadout
        creates too, and the dialog it opens did not: 71 against 53 on a
        loadout of 150 mods."""
        from PySide6.QtWidgets import QApplication

        from anno_modding_studio.core.workspace import Workspace
        from anno_modding_studio.ui import changes_dock
        from anno_modding_studio.ui.conflicts_dialog import ConflictsDialog

        QApplication.instance() or QApplication([])
        self.installed("Other", "7777")
        loadout = self.loadout(a__mod=make("7777", "mine"))
        panel = changes_dock.ChangesPanel(loadout)
        self.addCleanup(panel.deleteLater)
        panel.set_index(Workspace(self.index, loadout))
        panel.set_elsewhere(self.owners())
        dialog = ConflictsDialog(loadout, None, self.index, panel.elsewhere)
        self.addCleanup(dialog.deleteLater)
        self.assertIn("1 GUID taken twice", panel.conflicts_button.text())
        self.assertIn("1 GUID taken twice", dialog.summary.text())
        self.assertEqual(dialog.repeats_table.rowCount(), 1)
        self.assertEqual(
            dialog.repeats_table.item(0, 2).text(),
            "a mod, and on this PC by Other, in your mods folder",
        )
        self.assertEqual(
            dialog.repeats_table.item(0, 3).text(),
            "made by a mod not in this loadout as well: if both load, the "
            "game finds only one of them",
        )

    def test_the_conflicts_dialog_gives_the_part_the_card_leaves_out(self):
        """A11-32: the card counts only what two loading mods take twice
        (repeats.playing), the panel's button that and what a mod outside the
        loadout takes as well; the dialog said the panel's number alone, so
        the card's and the dialog's differed with nothing to say why."""
        from PySide6.QtWidgets import QApplication

        from anno_modding_studio.core.workspace import Workspace
        from anno_modding_studio.ui import changes_dock
        from anno_modding_studio.ui.conflicts_dialog import ConflictsDialog

        QApplication.instance() or QApplication([])
        self.installed("Other", "7777")
        loadout = self.loadout(a__mod=make("7777", "mine") + make("8888", "one"),
                               b__two=make("8888", "two"))
        panel = changes_dock.ChangesPanel(loadout)
        self.addCleanup(panel.deleteLater)
        panel.set_index(Workspace(self.index, loadout))
        panel.set_elsewhere(self.owners())
        dialog = ConflictsDialog(loadout, None, self.index, panel.elsewhere)
        self.addCleanup(dialog.deleteLater)
        self.assertIn("2 GUIDs taken twice", panel.conflicts_button.text())
        self.assertEqual(len(repeats.playing(repeats.find(loadout, self.index))), 1)
        self.assertIn(
            "2 GUIDs taken twice, 1 of them only with a mod on this PC outside the loadout",
            dialog.summary.text(),
        )

    def test_and_says_no_part_when_every_one_loads_with_the_loadout(self):
        from PySide6.QtWidgets import QApplication

        from anno_modding_studio.ui.conflicts_dialog import ConflictsDialog

        QApplication.instance() or QApplication([])
        loadout = self.loadout(a__one=make("7777", "one"), b__two=make("7777", "two"))
        dialog = ConflictsDialog(loadout, None, self.index, self.owners())
        self.addCleanup(dialog.deleteLater)
        self.assertIn("1 GUID taken twice", dialog.summary.text())
        self.assertNotIn("of them", dialog.summary.text())

    def test_and_gives_the_part_for_identical_copies_a_mod_on_this_pc_makes_too(self):
        """A11-32: two identical copies in the loadout are no harm, and the
        card leaves them out; a mod on this PC making the GUID as well puts
        them in the panel's count, and the dialog said 1 with no part."""
        from PySide6.QtWidgets import QApplication

        from anno_modding_studio.core.workspace import Workspace
        from anno_modding_studio.ui import changes_dock
        from anno_modding_studio.ui.conflicts_dialog import ConflictsDialog

        QApplication.instance() or QApplication([])
        self.installed("Other", "7777")
        loadout = self.loadout(a__one=make("7777", "same"), b__two=make("7777", "same"))
        panel = changes_dock.ChangesPanel(loadout)
        self.addCleanup(panel.deleteLater)
        panel.set_index(Workspace(self.index, loadout))
        panel.set_elsewhere(self.owners())
        dialog = ConflictsDialog(loadout, None, self.index, panel.elsewhere)
        self.addCleanup(dialog.deleteLater)
        self.assertIn("1 GUID taken twice", panel.conflicts_button.text())
        self.assertEqual(repeats.playing(repeats.find(loadout, self.index)), [])
        self.assertIn(
            "1 GUID taken twice, 1 of them only with a mod on this PC outside the loadout",
            dialog.summary.text(),
        )

    def test_what_plays_is_the_same_with_the_mods_on_this_pc_or_without(self):
        """The card counts playing() of find without them, the dialog takes
        its part from find with them: the two must agree on what loads."""
        self.installed("Other", "7777")
        self.installed("Another", "8888")
        loadout = self.loadout(
            a__one=make("7777", "same") + make("8888", "one") + make("9999", "x"),
            b__two=make("7777", "same") + make("8888", "two") + make("9999", "x"),
        )
        bare = [repeat.guid for repeat in repeats.playing(repeats.find(loadout, self.index))]
        seen = repeats.find(loadout, self.index, self.owners())
        self.assertEqual(bare, ["8888"])
        self.assertEqual([repeat.guid for repeat in repeats.playing(seen)], bare)


class TestOnScreen(RepeatCase):
    def setUp(self):
        super().setUp()
        from PySide6.QtWidgets import QApplication

        self.app = QApplication.instance() or QApplication([])
        self.loadout_ = self.loadout(a__one=make("7777", "one"), b__two=make("7777", "two"))

    def test_the_mod_row_says_so_and_its_tooltip_says_which(self):
        from anno_modding_studio.core.workspace import Workspace
        from anno_modding_studio.ui import changes_dock

        panel = changes_dock.ChangesPanel(self.loadout_)
        self.addCleanup(panel.deleteLater)
        panel.set_index(Workspace(self.index, self.loadout_))
        rows = {}
        for position in range(panel.tree.topLevelItemCount()):
            item = panel.tree.topLevelItem(position)
            if item.data(0, changes_dock.KIND_ROLE) == "mod":
                rows[item.data(0, changes_dock.ORIGIN_ROLE)] = item
        self.assertIn("(1 GUID taken twice)", rows["a one"].text(0))
        tip = rows["a one"].toolTip(0)
        self.assertIn("GUID 7777 (one): also in b two, the game finds only two", tip)
        self.assertIn("1 GUID taken twice", panel.conflicts_button.text())

    def test_the_conflicts_dialog_lists_them(self):
        from anno_modding_studio.ui.conflicts_dialog import ConflictsDialog

        dialog = ConflictsDialog(self.loadout_, None, self.index)
        self.addCleanup(dialog.deleteLater)
        self.assertFalse(dialog.repeats_table.isHidden())
        self.assertEqual(dialog.repeats_table.rowCount(), 1)
        self.assertEqual(dialog.repeats_table.item(0, 0).text(), "7777")
        self.assertEqual(dialog.repeats_table.item(0, 2).text(), "b two → a one")
        self.assertEqual(dialog.repeats_table.item(0, 3).text(), "the game finds only two")
        self.assertIn("1 GUID taken twice", dialog.summary.text())
        opened = []
        dialog.assetActivated.connect(lambda guid, path: opened.append((guid, path)))
        dialog._open_repeat(dialog.repeats_table.item(0, 1))
        self.assertEqual(opened, [("7777", "")])

    def test_and_hides_the_table_when_there_are_none(self):
        from anno_modding_studio.ui.conflicts_dialog import ConflictsDialog

        dialog = ConflictsDialog(self.loadout(a__mod=make("7777", "only")), None, self.index)
        self.addCleanup(dialog.deleteLater)
        self.assertTrue(dialog.repeats_table.isHidden())


class TestCopiesInDifferentGroups(RepeatCase):
    """The first in the file is the one found only among copies in one of the
    file's top-level groups: of two in different groups the loader finds the
    one in the group its last look-up landed in (measured in both games).  So
    for those it is said that one is found, and not which."""

    @classmethod
    def setUpClass(cls):
        import tempfile

        from anno_modding_studio.core import index as index_mod

        def asset(guid, name):
            return ("<Asset><Template>Thing</Template><Values><Standard><GUID>%s</GUID>"
                    "<Name>%s</Name></Standard></Values></Asset>" % (guid, name))

        cls._tmp = tempfile.TemporaryDirectory()
        tmp = Path(cls._tmp.name)
        cls.assets = tmp / "assets.xml"
        cls.assets.write_text(
            "<AssetList><Groups>"
            "<Group><Assets>%s</Assets></Group>"
            "<Group><Assets>%s%s</Assets></Group>"
            "<Group><Assets>%s</Assets></Group>"
            "</Groups></AssetList>"
            % (asset("100", "first"), asset("200", "second"), asset("201", "beside"),
               asset("300", "third")),
            encoding="utf-8",
        )
        cls.db = tmp / "index.sqlite"
        index_mod.build_index(cls.assets, cls.db)
        cls.index = index_mod.AssetIndex(cls.db, cls.assets)

    def test_two_a_mod_makes_in_two_groups(self):
        _loadout, found = self.found(a__mod=make("7777", "one", "100") + make("7777", "two", "300"))
        (repeat,) = found
        self.assertTrue(repeat.apart)
        (line,) = repeats.describe(found, "a mod", lambda origin: origin)[1:]
        self.assertIn("which one depends on the ops run before", line)
        self.assertNotIn("finds only one,", line)
        (said,) = repeats.before_play(found, lambda origin: origin)
        self.assertIn("The game finds only one of each.", said)

    def test_a_copy_of_a_shipped_asset_in_another_group(self):
        _loadout, (repeat,) = self.found(a__mod=make("200", "mine", "300"))
        self.assertTrue(repeat.apart)

    def test_in_one_group_it_is_still_the_first_in_the_file(self):
        _loadout, found = self.found(a__mod=make("200", "mine", "201"))
        (repeat,) = found
        self.assertFalse(repeat.apart)
        self.assertEqual(repeat.found, "the base game's second")
        _loadout, (repeat,) = self.found(a__mod=make("7777", "one", "300") + make("7777", "two", "300"))
        self.assertFalse(repeat.apart)
        self.assertEqual(repeat.found, "two")

    def test_one_added_at_the_end_of_the_file_is_in_the_group_the_reader_has_it_in(self):
        """The reader carries an op aimed at a GUID it has in two groups, and
        reads one aimed at a GUID it has in one.  Which group an asset
        written straight under ``<ModOps>`` or added in a block is in was
        worked out here a second time, from the op's text, and came out the
        other way for both of these."""
        def straight(name):
            return ("<Asset><Template>Thing</Template><Values><Standard><GUID>7777</GUID>"
                    "<Name>%s</Name></Standard></Values></Asset>" % name)

        def block(guid):
            return ('<ModOp Add="/AssetList/Groups[last()]"><Group><Assets>%s</Assets></Group>'
                    "</ModOp>" % straight("in a block").replace("7777", guid))

        aimed = '<ModOp Type="merge" GUID="7777" Path="/Values/Standard"><Name>x</Name></ModOp>'
        for said, body, apart in (
            ("after a block, in its group", block("7777") + straight("after it"), False),
            ("a block between two", straight("one") + block("8888") + straight("two"), True),
            ("two with nothing between", straight("one") + straight("two"), False),
        ):
            with self.subTest(said):
                loadout, (repeat,) = self.found(a__mod=body + aimed)
                self.assertEqual(bool(loadout.unread), apart)
                self.assertEqual(repeat.apart, apart)
        loadout, (repeat,) = self.found(
            a__mod=straight("one"), b__mod=block("8888"), c__mod=straight("two") + aimed)
        self.assertTrue(loadout.unread)
        self.assertTrue(repeat.apart)

    @staticmethod
    def _straight(name, guid="7777"):
        return ("<Asset><Template>Thing</Template><Values><Standard><GUID>%s</GUID>"
                "<Name>%s</Name></Standard></Values></Asset>" % (guid, name))

    def _block(self, guid, name="in a block"):
        return ('<ModOp Add="/AssetList/Groups[last()]"><Group><Assets>%s</Assets></Group>'
                "</ModOp>" % self._straight(name, guid))

    def test_a_block_of_a_mod_that_does_not_load_divides_nothing(self):
        """The groups were counted over every mod's ops, so a switched-off
        mod's block still stood between two copies the game holds in one."""
        loadout, (repeat,) = self.found(
            a__mod=self._straight("one"), b__mod=self._block("8888"), c__mod=self._straight("two"))
        self.assertTrue(repeat.apart)
        loadout.layer("b mod").active = False
        (repeat,) = repeats.find(loadout, self.index)
        self.assertFalse(repeat.apart)

    def test_a_block_inside_a_group_kept_whole_is_a_group_of_its_own(self):
        """A block in a ``MaxRepeat`` loop is the loop's op, kept whole, and
        the groups were counted from ops under the block's own path: the
        asset after it was put in a group apart from the block's."""
        looped = '<Group MaxRepeat="3">%s</Group>' % self._block("8888", "looped")
        _loadout, (repeat,) = self.found(a__mod=looped + self._straight("after", "8888"))
        self.assertFalse(repeat.apart)
        _loadout, (repeat,) = self.found(
            a__mod=looped + self._block("9999") + self._straight("after", "8888"))
        self.assertTrue(repeat.apart)

    def test_the_conflicts_dialog_does_not_name_one(self):
        from PySide6.QtWidgets import QApplication

        from anno_modding_studio.ui.conflicts_dialog import ConflictsDialog

        self.app = QApplication.instance() or QApplication([])
        loadout = self.loadout(a__mod=make("7777", "one", "100") + make("7777", "two", "300"))
        dialog = ConflictsDialog(loadout, None, self.index)
        self.addCleanup(dialog.deleteLater)
        self.assertEqual(dialog.repeats_table.item(0, 3).text(), repeats.said_apart())

    def test_the_asset_view_says_it_cannot_tell(self):
        """It shows the first in the file, for want of an answer, and says the
        game finds one or the other depending on the ops before (#151)."""
        from anno_modding_studio.core.workspace import Workspace

        loadout = self.loadout(a__mod=make("7777", "one", "100") + make("7777", "two", "300"))
        workspace = Workspace(self.index, loadout)
        standing = workspace.standing_of("7777")
        self.assertTrue(standing.apart)
        self.assertEqual(workspace.asset_row("7777").name, "one")
        said, _tip = TestTheView.panel(self, loadout, workspace, "7777")
        self.assertIn(repeats.said_apart(), said)
        self.assertIn("shown as the first in the file", said)
        self.assertNotIn("the game finds this one", said)

    def test_and_of_a_shipped_asset_in_another_group(self):
        from anno_modding_studio.core.workspace import Workspace

        loadout = self.loadout(a__mod=make("200", "mine", "300"))
        workspace = Workspace(self.index, loadout)
        self.assertFalse(workspace.is_new_asset("200"))
        self.assertEqual(workspace.asset_row("200").name, "second")
        said, _tip = TestTheView.panel(self, loadout, workspace, "200")
        self.assertIn(repeats.said_apart(), said)
        self.assertIn("shown as the game ships it", said)


if __name__ == "__main__":
    unittest.main()
