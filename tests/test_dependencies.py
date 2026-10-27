"""What the mods that will load say about each other, before Play -- the lists
each modinfo keeps of mods it needs, does not work with and replaces."""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from unittest import mock

import _isolation  # first: keeps the suite out of real files under any runner

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from support import PlayingWindow, TruthFolders, install_mod  # noqa: E402

from anno_modding_studio.core import dependencies, installplan, truth  # noqa: E402
from anno_modding_studio.core.changes import ModLayer  # noqa: E402
from anno_modding_studio.core.games import ANNO_117, ANNO_1800  # noqa: E402

Entry = dependencies.Entry


def _1800(**lists):
    keys = {"require": "ModDependencies", "incompatible": "IncompatibleIds",
            "deprecate": "DeprecateIds", "optional": "OptionalDependencies"}
    return {keys[kind]: values for kind, values in lists.items()}


def _117(**lists):
    keys = {"require": "Require", "incompatible": "Incompatible",
            "deprecate": "Deprecate", "optional": "Optional"}
    return {"Dependencies": {keys[kind]: values for kind, values in lists.items()}}


def _declare(folder: Path, **more) -> None:
    """Add to a mod's modinfo, as :func:`install_mod` wrote it."""
    path = folder / "modinfo.json"
    info = json.loads(path.read_text(encoding="utf-8"))
    info.update(more)
    path.write_text(json.dumps(info), encoding="utf-8")


class TestReadingTheLists(unittest.TestCase):
    def test_each_game_keeps_them_in_its_own_place(self):
        found = dependencies.links(_1800(require=["a"], incompatible=["b"], deprecate=["c"]), ANNO_1800)
        self.assertEqual((found.require, found.incompatible, found.deprecate), (["a"], ["b"], ["c"]))
        found = dependencies.links(_117(require=["a"], incompatible=["b"], deprecate=["c"]), ANNO_117)
        self.assertEqual((found.require, found.incompatible, found.deprecate), (["a"], ["b"], ["c"]))
        # And not in the other's.
        self.assertEqual(dependencies.links(_117(require=["a"]), ANNO_1800).require, [])
        self.assertEqual(dependencies.links(_1800(require=["a"]), ANNO_117).require, [])

    def test_what_is_not_a_modid_is_left_out(self):
        """A template's placeholder, an empty entry, a download link -- all in
        the census, and none of them anything the player can act on."""
        found = dependencies.links(_1800(require=[
            "ModId", "", "  ", "null", None, 3,
            "https://github.com/anno-mods/shared-resources/releases/download/v8.1/shared.zip",
            " real-one ",
        ]), ANNO_1800)
        self.assertEqual(found.require, ["real-one"])

    def test_the_wrong_shape_says_nothing(self):
        self.assertEqual(dependencies.links({"ModDependencies": "a"}, ANNO_1800).require, [])
        self.assertEqual(dependencies.links({"Dependencies": ["a"]}, ANNO_117).require, [])
        self.assertEqual(dependencies.links(None, ANNO_117).require, [])


class TestTheCheck(unittest.TestCase):
    def check(self, *entries, game=ANNO_1800, known=None):
        return dependencies.check(entries, game, known)

    def test_a_requirement_that_will_load_is_met(self):
        found = self.check(Entry("a", "A", _1800(require=["B"])), Entry("b", "B"))
        self.assertTrue(found.ok)

    def test_one_that_will_not_says_whether_it_is_there_at_all(self):
        found = self.check(Entry("a", "A", _1800(require=["b", "c"])), known={"b": "Mod B"})
        self.assertEqual(found.missing, [("A", "Mod B", True), ("A", "c", False)])
        self.assertEqual(found.lines(), [
            "A needs Mod B, which is switched off.",
            "A needs c, which is not installed.",
        ])
        self.assertEqual(found.about["a"], found.lines())

    def test_two_that_do_not_work_together_are_said_once(self):
        found = self.check(
            Entry("a", "A", _1800(incompatible=["b"])),
            Entry("b", "B", _1800(incompatible=["a"])),
            Entry("c", "C", _1800(incompatible=["off-mod"])),
        )
        self.assertEqual(found.clashes, [("A", "B")])
        self.assertEqual(found.about["b"], ["A does not work with B, and both will load."])
        self.assertNotIn("c", found.about)

    def test_a_replaced_mod_does_not_load_at_all(self):
        """Both loaders drop it (``HandleDeprecation``), so what it says no
        longer counts, and what needed it is met by what replaced it."""
        found = self.check(
            Entry("new", "New", _1800(deprecate=["old"])),
            Entry("old", "Old", _1800(require=["gone"], incompatible=["other"])),
            Entry("other", "Other", _1800(require=["old"])),
        )
        self.assertEqual(found.replaced, [("Old", "New")])
        self.assertEqual(found.missing, [])
        self.assertEqual(found.clashes, [])
        self.assertEqual(found.lines(), ["Old will not load: New replaces it."])
        self.assertIn("Old will not load: New replaces it.", found.about["new"])

    def test_two_mods_with_one_name_are_told_apart(self):
        """The case on this machine: Quay System Full Corners 1.0, bundled in
        *Extended Arctic*, replaced by 2.2 under a new ModID in *New
        Docklands* -- and both called the same. The loader loaded only 2.2."""
        found = self.check(
            Entry("jdg_06_quay_system_fc_2", "Quay System Full Corners",
                  _1800(deprecate=["JDG_06_quay_system_fc"]), "JDG_06_quay_system_fc_2"),
            Entry("jdg_06_quay_system_fc", "Quay System Full Corners", {}, "JDG_06_quay_system_fc"),
        )
        self.assertEqual(found.lines(), [
            "Quay System Full Corners (JDG_06_quay_system_fc) will not load: "
            "Quay System Full Corners (JDG_06_quay_system_fc_2) replaces it.",
        ])

    def test_a_mod_listing_itself_is_not_dropped(self):
        found = self.check(Entry("a", "A", _1800(deprecate=["A"], require=["a"])))
        self.assertTrue(found.ok)

    def test_ids_match_whatever_their_case(self):
        found = self.check(Entry("a", "A", _117(require=["Shared-Pools"])), Entry("shared-pools", "S"),
                           game=ANNO_117)
        self.assertTrue(found.ok)

    def test_the_card_says_one_and_counts_more(self):
        """By mods, not sentences: A needing eight is one mod to look at."""
        found = self.check(Entry("a", "A", _1800(require=["m0"])))
        self.assertEqual(found.describe(), "What these mods say about each other:\n"
                                           "• A needs m0, which is not installed.")
        found = self.check(
            Entry("a", "A", _1800(require=["m%d" % n for n in range(8)], incompatible=["b"])),
            Entry("b", "B", _1800(deprecate=["c"])),
            Entry("c", "C"),
        )
        self.assertEqual(found.describe(), "What these mods say about each other: 1 mod will not load "
                                           "because another replaces it, 1 mod needs one that will not "
                                           "load and 1 mod loads beside one it does not work with.")
        self.assertEqual(self.check(Entry("a", "A")).describe(), "")

    def test_117_may_be_asking_after_a_dlc(self):
        """Which DLCs are owned is nothing a mods folder says."""
        found = self.check(Entry("a", "A", _117(require=["DLC 1"])), game=ANNO_117)
        self.assertIn("names a DLC you own", found.describe())
        # Not for 1800, and not for a mod that is only switched off.
        self.assertEqual(self.check(Entry("a", "A", _1800(require=["x"]))).note(), "")
        self.assertEqual(
            self.check(Entry("a", "A", _117(require=["b"])), game=ANNO_117, known={"b": "B"}).note(), ""
        )


class TestWhatAModSaysOfTheGame(unittest.TestCase):
    def test_only_what_it_says(self):
        self.assertEqual(dependencies.setup({
            "Difficulty": "cheat",
            "GameSetup": {"RequiresNewGame": True, "SafeToRemove": False,
                          "Multiplayer": False, "Campaign": True},
        }), ["a cheat", "needs a new game", "not safe to take out of a save", "not for multiplayer"])
        self.assertEqual(dependencies.setup({"Difficulty": "unchanged",
                                             "GameSetup": {"SafeToRemove": True}}),
                         ["balanced like the base game", "can be taken out of a save"])
        self.assertEqual(dependencies.setup({"ModID": "x"}), [])
        self.assertEqual(dependencies.setup(None), [])


class TestWhatWillLoadOncePlayed(TruthFolders):
    """Every loadout is complete when played: what it has on, what is inside
    that, and what Play cannot switch."""

    def test_what_the_loadout_has_on_and_what_is_bundled_in_it(self):
        found = self.read()
        loadout = self.loadout(
            ModLayer(origin="Host", mod_id="host", declared=_1800(require=["piece", "off"])),
            ModLayer(origin="Host/Piece", mod_id="piece"),
            ModLayer(origin="Off", mod_id="off", active=False),
            ModLayer(origin="Off/Inner", mod_id="inner", declared=_1800(require=["nowhere"])),
        )
        entries, known = dependencies.after_play(loadout, found)
        self.assertEqual([entry.key for entry in entries], ["host", "piece"])
        self.assertEqual(known, {"off": "Off", "inner": "Off/Inner"})
        checked = dependencies.for_loadout(loadout, found)
        self.assertEqual(checked.lines(), ["Host needs Off, which is switched off."])

    def test_the_truth_carries_it_for_the_card(self):
        install_mod(self.mods, "Mine", "mine", "1")
        _declare(self.mods / "Mine", ModDependencies=["needed"], IncompatibleIds=["stray"])
        install_mod(self.mods, "Stray", "stray", "1")
        install_mod(self.mods, "-Needed", "needed", "1")
        # What the inventory read, kept for mods the loadout never read in.
        installed = installplan.inventory(ANNO_1800, self.mods, {})
        mine = next(mod for mod in installed if mod.mod_id == "mine")
        self.assertEqual(mine.declared["ModDependencies"], ["needed"])

        layer = ModLayer(origin="Mine", mod_id="mine", declared=mine.declared)
        found = truth.read(self.loadout(layer), self.mods)
        # Stray is switched off by Play, so it clashes with nothing.
        self.assertEqual(found.needs.lines(), ["Mine needs Needed, which is switched off."])
        self.assertIn("Mine needs Needed", found.describe_needs())

    def test_what_play_cannot_switch_is_counted_as_loading(self):
        """Anno 1800's own browser's copies load whatever the loadout says."""
        self.cached(5, "Theirs", "theirs", "1")
        _declare(self.cache_root / "4169" / "mods" / "5" / "Theirs", IncompatibleIds=["mine"])
        install_mod(self.mods, "Mine", "mine", "1")
        found = truth.read(self.loadout(ModLayer(origin="Mine", mod_id="mine")), self.mods)
        self.assertEqual(found.needs.clashes, [("Theirs", "Mine")])


class TestAReplacedModIsNotLoaded(unittest.TestCase):
    """Both loaders drop a mod another loaded mod deprecates, so nothing it
    does reaches the game -- and switching the replacing mod off brings it
    back, there as here."""

    def loadout(self, game=ANNO_1800):
        from anno_modding_studio.core.changes import Change, Loadout

        loadout = Loadout(game=game)
        loadout.layers = [
            ModLayer(origin="New", mod_id="new", declared=_1800(deprecate=["OLD"])),
            ModLayer(origin="Old", mod_id="old"),
            ModLayer(origin="Other", mod_id="other"),
        ]
        for origin, guid in (("New", "1"), ("Old", "2"), ("Other", "3")):
            change = Change(guid=guid, asset_label=origin, path="Values/Standard/Name", tag="Name",
                            op="replace", old_value="a", new_value="b", origin=origin)
            loadout.changes[change.key] = change
        return loadout

    def test_its_changes_are_not_what_the_game_gets(self):
        loadout = self.loadout()
        self.assertEqual(loadout.replaced(), {"Old": "New"})
        self.assertEqual({change.origin for change in loadout.active()}, {"New", "Other"})
        self.assertEqual(loadout.active_count, 2)
        # Its own switch is untouched: writing the folder back out keeps it.
        self.assertEqual(loadout.switched_off(), set())
        self.assertEqual(loadout.not_loading(), {"Old"})

    def test_switching_the_replacing_mod_off_brings_it_back(self):
        loadout = self.loadout()
        loadout.layer("New").active = False
        self.assertEqual(loadout.replaced(), {})
        self.assertEqual({change.origin for change in loadout.active()}, {"Old", "Other"})

    def test_a_bundled_mod_in_a_host_that_is_off_replaces_nothing(self):
        loadout = self.loadout()
        loadout.layer("New").origin = "Host/New"
        loadout.layers.append(ModLayer(origin="Host", mod_id="host", active=False))
        self.assertEqual(loadout.replaced(), {})

    def test_a_patch_of_a_replaced_mod_has_nothing_to_patch(self):
        from anno_modding_studio.core import patching

        loadout = self.loadout()
        patch = ModLayer(origin="Old (patch)", patches="Old")
        loadout.layers.append(patch)
        self.assertTrue(patching.is_inert(loadout, patch))
        self.assertEqual(
            patching.why_inert(loadout, patch),
            "the mod it patches is replaced by New, so the game does not load it",
        )

    def test_117_reads_its_own_lists(self):
        loadout = self.loadout(ANNO_117)
        loadout.layer("New").declared = _117(deprecate=["old"])
        self.assertEqual(loadout.replaced(), {"Old": "New"})


class TestTheLogIsNotAFaultForIt(unittest.TestCase):
    def test_a_replaced_mod_the_game_did_not_load_is_not_missing(self):
        from anno_modding_studio.core import loaderlog

        root = Path("C:/mods")

        def mod(folder, mod_id):
            installed = installplan.FolderMod(folder=root / folder, name=folder, mod_id=mod_id, version="1")
            return truth.Mod(installed=installed, on=True, loads=True, origin=folder, wanted=True)

        found = truth.Truth(game=ANNO_1800, folder=root, mods=[mod("New", "new"), mod("Old", "old")])
        found.needs = dependencies.Check(game=ANNO_1800, dropped={"old"})
        log = loaderlog.LoaderLog(
            path=Path("mod-loader.log"), complete=True,
            mods=[loaderlog.Logged(mod_id="new", name="New", folder="New", bundled=False)],
        )
        checked = loaderlog.check(log, found, mock.Mock(own_mod_loads_last=True, layers=[]))
        self.assertEqual(checked.missing, [])
        self.assertTrue(checked.ok)


class TestBeforePlay(PlayingWindow):
    """Said where it can still change what the game gets."""

    def test_the_confirm_says_it_first(self):

        install_mod(self.mods, "[Gameplay] Mine", "mine", "1")
        install_mod(self.mods, "[Gameplay] Stray", "stray", "1")
        self.window.loadout.layers = [ModLayer(
            origin="[Gameplay] Mine", mod_id="mine", name="Mine",
            declared=_1800(require=["stray"]),
        )]
        with _isolation.answering(False) as asked:
            self.assertFalse(self.window.live.play())
        (text,) = asked.texts
        self.assertIn("Worth knowing first:\nWhat these mods say about each other:\n"
                      "  • Mine needs [Gameplay] Stray, which is switched off.", text)

    def test_a_loader_check_that_failed_is_said_too(self):
        """Kept among the verdicts and read by nobody: only the differing and
        the agreeing were, so the mod went to the game with nothing said."""
        from anno_modding_studio.core import loadercheck, play

        failed = loadercheck.Verdict(loadercheck.FAILED, name="Mine", why="xmltest stopped")
        agrees = loadercheck.Verdict(loadercheck.AGREES, name="Other", checked=1)
        # And one not run, past the size that runs unasked or skipped (H2-15).
        skipped = loadercheck.Verdict(loadercheck.NOT_RUN, name="Third", why="You skipped the check.")
        loadout = self.window.loadout
        found = play.plan(loadout, str(self.mods), self.window.where.library_dir(),
                          verdicts=lambda built: [failed, agrees, skipped])
        said = dict(found.worth)
        self.assertEqual(said.get("The game's loader"),
                         ["The game's loader could not check Mine: xmltest stopped.",
                          "The game's loader has not checked Third. You skipped the check."])

    def test_a_long_list_is_a_count_of_each_kind_with_the_rest_behind_show_details(self):
        from anno_modding_studio.core import play

        plan = play.Plan(folder="")
        plan.worth_knowing("GUIDs taken twice", ["GUID %d is created twice." % n for n in range(6)])
        plan.worth_knowing("The game's loader", ["It differs.", ""])
        told, details = plan.told()
        self.assertEqual(details, "")  # seven: said in full
        self.assertIn("  • GUID 5 is created twice.", told)
        plan.worth_knowing("Your changes", ["Mine: 2 changes would not apply.", "Mine: a bad Mod ID."])
        told, details = plan.told()
        self.assertEqual(told, "GUIDs taken twice (6):\n  • GUID 0 is created twice.\n  • GUID 1 is created "
                               "twice.\n  • and 4 more\nThe game's loader (1):\n  • It differs.\nYour changes "
                               "(2):\n  • Mine: 2 changes would not apply.\n  • Mine: a bad Mod ID.\n\n"
                               "Show Details has every one.")
        self.assertIn("  • GUID 5 is created twice.", details)
        self.assertEqual(len(plan.warnings), 9)

    def test_guids_taken_twice_are_among_them(self):

        install_mod(self.mods, "[Gameplay] Mine", "mine", "1")
        install_mod(self.mods, "[Gameplay] Stray", "stray", "1")  # something for Play to do, so it asks
        self.window.loadout.layers = [ModLayer(origin="[Gameplay] Mine", mod_id="mine", name="Mine")]
        with mock.patch.object(self.window.live, "repeats_playing",
                               return_value=["GUID 7777 is created by Mine twice; the game finds only x."]), \
                _isolation.answering(False) as asked:
            self.assertFalse(self.window.live.play())
        (text,) = asked.texts
        self.assertIn("GUIDs taken twice:\n  • GUID 7777 is created by Mine twice; the game finds only x.", text)

    def test_and_the_card_says_it_while_nothing_is_pressed(self):
        install_mod(self.mods, "[Gameplay] Mine", "mine", "1")
        self.window.loadout.layers = [ModLayer(
            origin="[Gameplay] Mine", mod_id="mine", name="Mine",
            declared=_1800(require=["elsewhere"]),
        )]
        self.window.refresh_truth()
        label = self.window.source.truth_needs
        self.assertFalse(label.isHidden())
        self.assertIn("Mine needs elsewhere, which is not installed.", label.text())
        # And the mod's own row says it too.
        self.window.changes_panel.refresh()
        layer = self.window.loadout.layers[0]
        section = mock.Mock(layer=layer, ops=[], unread=[])
        self.assertIn("Mine needs elsewhere", self.window.changes_panel._mod_tip(section))

    def test_past_one_the_card_counts_and_lists_them_behind_a_button(self):
        """On a big loadout the sentences were most of the card."""
        from PySide6.QtWidgets import QPushButton

        from anno_modding_studio.ui.needs_dialog import NeedsDialog, rows

        install_mod(self.mods, "[Gameplay] Mine", "mine", "1")
        install_mod(self.mods, "[Gameplay] Two", "two", "1")
        self.window.loadout.layers = [
            ModLayer(origin="[Gameplay] Mine", mod_id="mine", name="Mine",
                     declared=_1800(require=["x", "y"])),
            ModLayer(origin="[Gameplay] Two", mod_id="two", name="Two", declared=_1800(require=["z"])),
        ]
        self.window.refresh_truth()
        source = self.window.source
        self.assertFalse(source.truth_needs_row.isHidden())
        self.assertEqual(source.truth_needs.text(),
                         "What these mods say about each other: 2 mods need one that will not load.")
        self.assertTrue(source.truth_repeats_row.isHidden())
        self.assertEqual([row[:2] for row in rows(self.window.truth.needs)],
                         [("Mine", "x"), ("Mine", "y"), ("Two", "z")])
        with mock.patch.object(NeedsDialog, "exec") as shown:
            source.truth_needs_row.findChild(QPushButton).click()
        shown.assert_called_once()

    def test_a_replaced_mod_reads_as_not_loading_until_its_replacement_is_off(self):
        from PySide6.QtCore import Qt

        from anno_modding_studio.core.changes import Change
        from anno_modding_studio.ui import changes_dock

        loadout = self.window.loadout
        loadout.layers = [
            ModLayer(origin="New", mod_id="new", name="New", declared=_1800(deprecate=["old"])),
            ModLayer(origin="Old", mod_id="old", name="Old"),
        ]
        for origin, guid in (("New", "1"), ("Old", "2")):
            change = Change(guid=guid, asset_label=origin, path="Values/Standard/Name", tag="Name",
                            op="replace", old_value="a", new_value="b", origin=origin)
            loadout.changes[change.key] = change
        panel = self.window.changes_panel
        panel.refresh()
        rows = {}
        for position in range(panel.tree.topLevelItemCount()):
            item = panel.tree.topLevelItem(position)
            if item.data(0, changes_dock.KIND_ROLE) == "mod":
                rows[item.data(0, changes_dock.ORIGIN_ROLE)] = item
        self.assertTrue(rows["Old"].font(0).strikeOut())
        self.assertFalse(rows["New"].font(0).strikeOut())
        self.assertEqual(rows["Old"].text(0), "Old  (replaced by New)")
        self.assertIn("New replaces it", rows["Old"].toolTip(0))
        # Switching New off repaints Old, which loads again.
        rows["New"].setCheckState(0, Qt.CheckState.Unchecked)
        self.assertFalse(loadout.layer("New").active)
        self.assertFalse(rows["Old"].font(0).strikeOut())
        self.assertEqual(rows["Old"].text(0), "Old")
        self.assertIn("loads at position", rows["Old"].toolTip(0))


class TestAModSeveralModsCarry(unittest.TestCase):
    """A shared piece loads once however many mods carry it, and the Studio
    reads the copy that loaded when the folder was read.  Switch that copy's
    host off with another carrier on and the game loads the other copy: the
    mod is still loading, and a mod that needs it still has it."""

    def loadout(self, a_on=True, b_on=True, a_version="1.3", b_version="1.3"):
        from anno_modding_studio.core.changes import Loadout

        loadout = Loadout(game=ANNO_1800)
        a = ModLayer(origin="A", name="Big Overhaul", mod_id="a", active=a_on)
        b = ModLayer(origin="B", name="Other Mod", mod_id="b", active=b_on,
                     declared=_1800(require=["shared"]))
        b.copies = {"B/Shared": "A/Shared"}
        b.copy_versions = {"B/Shared": b_version}
        shared = ModLayer(origin="A/Shared", name="Shared Pools", mod_id="shared",
                          version=a_version)
        for layer in (a, shared, b):
            loadout.set_layer(layer)
        return loadout

    def test_with_its_host_on_the_copy_read_is_the_one_that_loads(self):
        loadout = self.loadout()
        self.assertEqual(loadout.loading_copies()["A/Shared"], "A/Shared")
        self.assertEqual(dependencies.copies_elsewhere(loadout), [])

    def test_with_its_host_off_another_carrier_s_copy_loads(self):
        loadout = self.loadout(a_on=False)
        self.assertEqual(loadout.loading_copies()["A/Shared"], "B/Shared")
        self.assertNotIn("A/Shared", loadout.switched_off())
        self.assertIn("A", loadout.switched_off())
        self.assertIn("A/Shared", {layer.origin for layer in dependencies.switched_on(loadout)})

    def test_so_the_mod_that_needs_it_still_has_it(self):
        loadout = self.loadout(a_on=False)
        self.assertEqual(dependencies.for_loadout(loadout, None).missing, [])

    def test_with_every_carrier_off_it_does_not_load(self):
        loadout = self.loadout(a_on=False, b_on=False)
        self.assertEqual(loadout.loading_copies()["A/Shared"], "")
        self.assertIn("A/Shared", loadout.switched_off())

    def test_its_own_switch_is_its_host_s(self):
        """Play cannot switch a bundled mod alone, so its own box -- left off
        by an older loadout, a profile, a collection -- decides nothing."""
        loadout = self.loadout()
        loadout.layer("A/Shared").active = False
        self.assertNotIn("A/Shared", loadout.switched_off())
        self.assertIs(loadout.switch_of("A/Shared"), loadout.layer("A"))
        self.assertIs(loadout.switch_of("A"), loadout.layer("A"))

    def test_a_newer_copy_switched_on_is_the_one_that_loads(self):
        loadout = self.loadout(b_version="2.0")
        self.assertEqual(loadout.loading_copies()["A/Shared"], "B/Shared")

    def test_between_equal_versions_the_copy_read_is_kept(self):
        loadout = self.loadout(b_version="1.3.0")
        self.assertEqual(loadout.loading_copies()["A/Shared"], "A/Shared")

    def test_a_copy_of_another_version_is_said_and_for_play(self):
        loadout = self.loadout(a_on=False, b_version="1.1")
        said = " ".join(dependencies.about_copy(loadout, loadout.layer("A/Shared"), "B/Shared"))
        self.assertIn("The game loads the copy Other Mod carries, version 1.1", said)
        self.assertIn("These rows are of version 1.3, the copy in Big Overhaul", said)
        self.assertEqual(len(dependencies.copies_worth_knowing(loadout)), 1)

    def test_the_same_version_is_said_and_not_for_play(self):
        loadout = self.loadout(a_on=False)
        said = " ".join(dependencies.about_copy(loadout, loadout.layer("A/Shared"), "B/Shared"))
        self.assertIn("the same version (1.3)", said)
        self.assertEqual(dependencies.copies_worth_knowing(loadout), [])

    def test_your_edits_left_in_the_copy_read_are_for_play_whatever_the_version(self):
        from anno_modding_studio.core.changes import Change

        loadout = self.loadout(a_on=False)
        change = Change(guid="1", asset_label="x", path="Values/Standard/Name", tag="Name",
                        op="replace", old_value="a", new_value="b", origin="A/Shared",
                        authored=True)
        loadout.changes[change.key] = change
        said = dependencies.copies_worth_knowing(loadout)
        self.assertEqual(len(said), 1)
        self.assertIn("Your edits to it are in the copy in Big Overhaul", said[0])

    def test_a_mod_bundled_two_deep_answers_to_the_outermost_mod(self):
        from anno_modding_studio.core.changes import Loadout

        loadout = Loadout(game=ANNO_1800)
        for layer in (ModLayer(origin="A", active=False), ModLayer(origin="A/B"),
                      ModLayer(origin="A/B/C")):
            loadout.set_layer(layer)
        self.assertIs(loadout.switch_of("A/B/C"), loadout.layer("A"))
        self.assertEqual(loadout.switched_off(), {"A", "A/B", "A/B/C"})

    def test_a_host_that_is_no_mod_of_its_own_leaves_the_mod_its_own_switch(self):
        """A folder holding only a modinfo and the mod that does the work comes
        in as that mod alone, which then answers for itself."""
        from anno_modding_studio.core.changes import Loadout

        loadout = Loadout(game=ANNO_1800)
        loadout.set_layer(ModLayer(origin="Box/Inner", active=False))
        self.assertIs(loadout.switch_of("Box/Inner"), loadout.layer("Box/Inner"))
        self.assertEqual(loadout.switched_off(), {"Box/Inner"})


if __name__ == "__main__":
    unittest.main()
