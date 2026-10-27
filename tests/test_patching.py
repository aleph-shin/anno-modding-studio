"""Patches: saying which mod one is for, and what happens when that mod goes.

A patch mod is an ordinary mod folder with one extra fact attached -- *this is
my layer on top of that* -- and that fact is the whole of what makes it a patch
rather than a mod that happens to load late.  Keeping it across a folder read,
and deriving what follows from it, is what these cover.
"""

from __future__ import annotations

import json
import sys
import unittest
from dataclasses import replace
from pathlib import Path
from unittest import mock

import _isolation  # noqa: F401 -- first: keeps the suite out of real files under any runner

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from anno_modding_studio.core import (  # noqa: E402
    games,
    generator,
    modfolder,
    modinfo,
    patching,
)
from anno_modding_studio.core.changes import (  # noqa: E402
    Change,
    Loadout,
    ModLayer,
    own_record,
)


class TestSayingWhichModAPatchIsFor(unittest.TestCase):
    """A patch that comes back out of a mods folder has to still be a patch.

    ``ModLayer.patches`` is set when one is made and saved with the loadout, but
    a folder read builds layers out of modinfo files -- and nothing in one says
    "I patch that" in any vocabulary the Studio knows.  So a patch came back as
    an ordinary mod beside the one it patches, and since nothing then claimed to
    patch that mod, the Studio would offer to build it a second one.
    """

    def patch_modinfo(self, game, declared=None):
        loadout = Loadout(game=game, own=own_record(creator="Lars"))
        loadout.set_layer(ModLayer(origin="Their Mod", mod_id="theirs"))
        loadout.set_layer(
            ModLayer(
                origin="Their Mod (my patch)",
                patches="Their Mod",
                declared=dict(declared or {}),
            )
        )
        return json.loads(
            modinfo.build(
                generator.for_mod(loadout, "Their Mod (my patch)")
            )
        )

    def test_a_patch_writes_a_marker_naming_what_it_patches(self):
        for game in (games.ANNO_1800, games.ANNO_117):
            with self.subTest(game=game):
                out = self.patch_modinfo(game)
                self.assertEqual(out[modinfo.STUDIO_KEY], {"patches": "theirs"})
                self.assertEqual(modinfo.declared_patch_of(out), "theirs")

    def test_which_survives_the_mod_being_written_again(self):
        out = self.patch_modinfo(
            games.ANNO_1800, {modinfo.STUDIO_KEY: {"patches": "theirs"}, "Extra": 1}
        )
        self.assertEqual(modinfo.declared_patch_of(out), "theirs")
        self.assertEqual(out["Extra"], 1)

    def test_and_makes_the_patch_a_patch_again(self):
        loadout = Loadout(game=games.ANNO_1800, own=own_record(creator="Lars"))
        loadout.set_layer(ModLayer(origin="Their Mod", mod_id="theirs"))
        loadout.set_layer(
            ModLayer(
                origin="Their Mod (my patch)",
                creator="Lars",
                declared={modinfo.STUDIO_KEY: {"patches": "theirs"}},
            )
        )
        self.assertEqual(patching.reattach(loadout), 1)

    def test_an_ordinary_mod_carries_no_marker(self):
        out = json.loads(
            modinfo.build(
                Loadout(own=own_record(name="Mine"), game=games.ANNO_1800)
            )
        )
        self.assertNotIn(modinfo.STUDIO_KEY, out)
        self.assertEqual(modinfo.declared_patch_of(out), "")

    # -- reading it back ---------------------------------------------------
    def test_the_marker_makes_the_patch_a_patch_again(self):
        loadout = Loadout(game=games.ANNO_1800, own=own_record(creator="Lars"))
        loadout.set_layer(
            ModLayer(origin="Their Mod", mod_id="theirs", creator="Them")
        )
        loadout.set_layer(
            ModLayer(
                origin="Their Mod (my patch)",
                creator="Lars",
                declared={modinfo.STUDIO_KEY: {"patches": "theirs"}},
            )
        )
        self.assertEqual(patching.reattach(loadout), 1)
        self.assertEqual(
            loadout.patch_for("Their Mod").origin, "Their Mod (my patch)"
        )

    def test_and_one_made_before_the_marker_existed_is_inferred(self):
        """Yours, declaring that it loads after exactly one mod that is here.
        Good evidence rather than proof, so it is only the fallback."""
        loadout = Loadout(game=games.ANNO_1800, own=own_record(creator="Lars"))
        loadout.set_layer(
            ModLayer(origin="Their Mod", mod_id="theirs", creator="Them")
        )
        loadout.set_layer(
            ModLayer(
                origin="Their Mod (my patch)", creator="Lars", load_after=["theirs"]
            )
        )
        self.assertEqual(patching.reattach(loadout), 1)
        self.assertEqual(
            loadout.layer("Their Mod (my patch)").patches, "Their Mod"
        )

    def test_somebody_elses_mod_is_never_inferred_to_be_your_patch(self):
        loadout = Loadout(game=games.ANNO_1800, own=own_record(creator="Lars"))
        loadout.set_layer(
            ModLayer(origin="Their Mod", mod_id="theirs", creator="Them")
        )
        loadout.set_layer(
            ModLayer(origin="Another Of Theirs", creator="Them", load_after=["theirs"])
        )
        self.assertEqual(patching.reattach(loadout), 0)

    def test_nor_is_a_mod_of_yours_that_depends_on_two(self):
        """Loading after several is a dependency, not a patch."""
        loadout = Loadout(game=games.ANNO_1800, own=own_record(creator="Lars"))
        loadout.set_layer(ModLayer(origin="A", mod_id="a", creator="Them"))
        loadout.set_layer(ModLayer(origin="B", mod_id="b", creator="Them"))
        loadout.set_layer(
            ModLayer(origin="Mine", creator="Lars", load_after=["a", "b"])
        )
        self.assertEqual(patching.reattach(loadout), 0)

    def test_a_marker_naming_a_mod_that_is_not_here_attaches_nothing(self):
        loadout = Loadout(game=games.ANNO_1800, own=own_record(creator="Lars"))
        loadout.set_layer(
            ModLayer(
                origin="Orphan (my patch)",
                creator="Lars",
                declared={modinfo.STUDIO_KEY: {"patches": "long-gone"}},
            )
        )
        self.assertEqual(patching.reattach(loadout), 0)
        self.assertEqual(loadout.layer("Orphan (my patch)").patches, "")

    def test_re_attaching_twice_changes_nothing(self):
        loadout = Loadout(game=games.ANNO_1800, own=own_record(creator="Lars"))
        loadout.set_layer(ModLayer(origin="Their Mod", mod_id="theirs"))
        loadout.set_layer(
            ModLayer(
                origin="Their Mod (my patch)",
                creator="Lars",
                declared={modinfo.STUDIO_KEY: {"patches": "theirs"}},
            )
        )
        self.assertEqual(patching.reattach(loadout), 1)
        self.assertEqual(patching.reattach(loadout), 0)

    def test_and_the_studio_no_longer_offers_a_second_patch(self):
        """The bug this exists to stop: a re-read forgot the patch, so nothing
        claimed to patch the mod and the offer came round again."""
        loadout = Loadout(game=games.ANNO_1800, own=own_record(creator="Lars"))
        loadout.set_layer(
            ModLayer(origin="Their Mod", mod_id="theirs", creator="Them")
        )
        loadout.set_layer(
            ModLayer(
                origin="Their Mod (my patch)",
                creator="Lars",
                declared={modinfo.STUDIO_KEY: {"patches": "theirs"}},
            )
        )
        self.assertIsNone(loadout.patch_for("Their Mod"))   # before
        patching.reattach(loadout)
        self.assertIsNotNone(loadout.patch_for("Their Mod"))  # after


class TestWhereAPatchIsMade(unittest.TestCase):
    """A patch made here has no place in the load order yet, and is given
    the one its load-after list gives it: straight after its mod."""

    def test_straight_after_its_mod_whatever_its_name(self):
        """It was placed by its name alone, which need only sort after its
        mod: named for the end of the list it loaded after every mod there,
        where the game loads it after the one it patches."""
        loadout = Loadout(game=games.ANNO_1800)
        for origin in ("[Gameplay] Theirs", "[Gameplay] Uplands", "[Seal] Other"):
            loadout.set_layer(ModLayer(origin=origin, name=origin, mod_id=origin.lower()))
        patching.make(loadout, "[Gameplay] Theirs", "zz my fixes")
        self.assertEqual(
            [layer.origin for layer in loadout.layers],
            ["[Gameplay] Theirs", "zz my fixes", "[Gameplay] Uplands", "[Seal] Other"],
        )

    def test_and_after_a_late_mod_in_the_late_phase(self):
        """A patch of a mod that loads after everything loads after
        everything too, and then after that mod."""
        for game in (games.ANNO_1800, games.ANNO_117):
            with self.subTest(game):
                loadout = Loadout(game=game)
                loadout.set_layer(ModLayer(
                    origin="[Map] Seeds", name="Seeds", category="Map", mod_id="seeds",
                    load_after=["*"],
                ))
                loadout.set_layer(ModLayer(
                    origin="[Gameplay] Uplands", name="Uplands", category="Gameplay",
                    mod_id="uplands",
                ))
                patching.make(loadout, "[Map] Seeds", "[Gameplay] A fix")
                self.assertEqual(
                    [layer.origin for layer in loadout.layers],
                    ["[Gameplay] Uplands", "[Map] Seeds", "[Gameplay] A fix"],
                )

    def test_named_for_another_loaded_mod_it_takes_a_free_name(self):
        """Named for a mod loaded already, it was put in that mod's place
        and the mod was gone: its name and ModID the patch's, its ops
        nobody's. It is numbered instead, as a fork is."""
        loadout = Loadout(game=games.ANNO_1800)
        loadout.set_layer(ModLayer(origin="A", name="A", mod_id="a"))
        loadout.set_layer(ModLayer(origin="B", name="B", mod_id="b"))
        made = patching.make(loadout, "A", "B")
        self.assertEqual(made.origin, "B 2")
        self.assertEqual(
            [(layer.origin, layer.mod_id, layer.patches) for layer in loadout.layers],
            [("A", "a", ""), ("B 2", "", "A"), ("B", "b", "")],
        )


class TestAPatchWhoseModIsNotThere(unittest.TestCase):
    """A patch is written against what the mod it patches leaves behind.

    Switch that mod off and the patch still loads, against a game that never had
    it -- so it does not do nothing, it does something nobody chose.  Derived
    from the host's own switch rather than stored beside it: a second checkbox
    to keep in step is a second checkbox to get out of step, and switching one
    mod off should not have to ask a question about another.
    """

    def loadout(self, host_active=True, loaded=True):
        loadout = Loadout(game=games.ANNO_1800)
        if loaded:
            loadout.set_layer(
                ModLayer(origin="Their Mod", mod_id="theirs", active=host_active)
            )
        loadout.set_layer(
            ModLayer(origin="Their Mod (my patch)", patches="Their Mod")
        )
        return loadout

    def patch(self, loadout):
        return loadout.layer("Their Mod (my patch)")

    def test_with_its_mod_on_it_is_an_ordinary_mod(self):
        loadout = self.loadout()
        self.assertFalse(patching.is_inert(loadout, self.patch(loadout)))
        self.assertEqual(patching.why_inert(loadout, self.patch(loadout)), "")

    def test_with_its_mod_switched_off_it_has_nothing_to_patch(self):
        loadout = self.loadout(host_active=False)
        self.assertTrue(patching.is_inert(loadout, self.patch(loadout)))
        self.assertIn(
            "switched off", patching.why_inert(loadout, self.patch(loadout))
        )

    def test_and_the_same_when_its_mod_is_not_loaded_at_all(self):
        loadout = self.loadout(loaded=False)
        self.assertTrue(patching.is_inert(loadout, self.patch(loadout)))
        self.assertIn(
            "not loaded", patching.why_inert(loadout, self.patch(loadout))
        )

    def test_an_ordinary_mod_is_never_inert(self):
        loadout = self.loadout()
        self.assertFalse(patching.is_inert(loadout, loadout.layer("Their Mod")))

    def test_what_is_out_of_the_game_is_worked_out_once_however_many_it_needs(self):
        """It was worked out again for every mod the patch needs, and the
        changes list and Play ask of every patch."""
        loadout = self.loadout()
        for origin in ("Second", "Third"):
            loadout.set_layer(ModLayer(origin=origin, mod_id=origin.lower()))
        loadout.set_layer(replace(self.patch(loadout), needs=["Second", "Third"]))
        built = []
        real = Loadout.loading_copies

        def counted(this):
            built.append(1)
            return real(this)

        with mock.patch.object(Loadout, "loading_copies", counted):
            loadout.not_loading()
            once = len(built)
            built.clear()
            self.assertFalse(patching.is_inert(loadout, self.patch(loadout)))
        self.assertEqual(len(built), once)


class TestABundledModOfSomebodyElsesIsPatched(unittest.TestCase):
    """Changes aimed at a bundled mod that is not yours go to a patch: its file
    is a copy inside somebody's host, and the next update of that host -- or a
    newer copy in another mod -- takes them with it."""

    def loadout(self, creator="Dee"):
        loadout = Loadout(own=own_record(creator="Ay"))
        loadout.set_layer(ModLayer(origin="Host/shared piece", name="Shared", creator=creator))
        loadout.set_layer(ModLayer(origin="Their Mod", name="Theirs", creator="Dee"))
        return loadout

    def test_a_bundled_mod_is_one_whose_origin_is_inside_another(self):
        self.assertTrue(modfolder.is_bundled("Host/shared piece"))
        self.assertFalse(modfolder.is_bundled("Their Mod"))

    def test_somebody_else_s_bundled_mod_needs_a_patch(self):
        loadout = self.loadout()
        self.assertTrue(patching.edits_need_a_patch(loadout, loadout.layer("Host/shared piece")))

    def test_a_top_level_mod_is_left_to_the_question(self):
        loadout = self.loadout()
        self.assertFalse(patching.edits_need_a_patch(loadout, loadout.layer("Their Mod")))

    def test_a_bundled_mod_of_your_own_a_project_keeps_does_not(self):
        loadout = self.loadout(creator="Ay")
        # With your name on it and no project, it is read like anybody
        # else's (section 5.5 of the projects and loadouts proposal).
        self.assertTrue(patching.edits_need_a_patch(loadout, loadout.layer("Host/shared piece")))
        loadout.layer("Host/shared piece").kept = True
        self.assertFalse(patching.edits_need_a_patch(loadout, loadout.layer("Host/shared piece")))

    def test_a_mod_the_game_s_client_installed_is_top_level(self):
        # The in-game client keeps each mod as <mod.io id>/<folder>: the number
        # is where it was downloaded to, not a host it is bundled inside.
        self.assertFalse(modfolder.is_bundled("123456/Their Mod"))
        self.assertTrue(modfolder.is_bundled("123456/Host/shared piece"))

    def test_a_mod_the_game_s_client_installed_is_left_to_the_question(self):
        loadout = self.loadout()
        loadout.set_layer(ModLayer(origin="123456/Their Mod", name="Theirs", creator="Dee"))
        self.assertFalse(patching.edits_need_a_patch(loadout, loadout.layer("123456/Their Mod")))


class TestWhoseOpsAreRewrittenInTheirFile(unittest.TestCase):
    """*Edit pattern* and *Write as narrow ModOps* rewrite one of a mod's own
    ops.  Somebody else's mod is rewritten only while it is the mod changes
    go to: *Edit pattern* used to write into it whatever changes went to."""

    def loadout(self):
        loadout = Loadout(own=own_record(creator="Ay"))
        loadout.set_layer(ModLayer(origin="Their Mod", name="Theirs", creator="Dee"))
        loadout.set_layer(ModLayer(origin="My Mod", name="Mine", creator="Ay"))
        loadout.set_layer(ModLayer(origin="Host/shared piece", name="Shared", creator="Dee"))
        return loadout

    def test_your_own_mods_and_patches_always(self):
        loadout = self.loadout()
        loadout.layer("My Mod").kept = True
        patch = patching.make(loadout, "Their Mod")
        for origin in ("", "My Mod", patch.origin):
            with self.subTest(origin=origin):
                self.assertTrue(patching.changed_directly(loadout, origin))

    def test_one_with_your_name_and_no_project_is_read_like_anybody_elses(self):
        # Section 5.5 of the projects and loadouts proposal.
        loadout = self.loadout()
        layer = loadout.layer("My Mod")
        self.assertTrue(patching.looks_mine(loadout, layer))
        self.assertTrue(patching.without_a_project(loadout, layer))
        self.assertFalse(patching.changed_directly(loadout, "My Mod"))
        layer.kept = True
        self.assertFalse(patching.without_a_project(loadout, layer))
        self.assertIn("My Mod", loadout.project_origins())

    def test_somebody_elses_not_even_while_changes_go_to_it(self):
        # Their mod is never changed (section 5.2 of the projects and
        # loadouts proposal), and is no place for changes to go.
        loadout = self.loadout()
        self.assertFalse(patching.changed_directly(loadout, "Their Mod"))
        loadout.editing = "Their Mod"
        self.assertFalse(patching.changed_directly(loadout, "Their Mod"))

    def test_a_patch_made_and_picked_keeps_the_edit(self):
        # Inside their op, kept by the patch: their file is never written
        # (section 5.3 of the projects and loadouts proposal).
        loadout = self.loadout()
        loadout.editing = patching.make(loadout, "Their Mod").origin
        self.assertTrue(patching.changed_directly(loadout, "Their Mod"))

    def test_a_bundled_one_of_somebody_elses_never(self):
        loadout = self.loadout()
        loadout.editing = "Host/shared piece"
        self.assertFalse(patching.changed_directly(loadout, "Host/shared piece"))


class TestAPatchMadeHereOutlastsAFolderRead(unittest.TestCase):
    """A folder read drops every layer and brings back what the folder holds;
    a patch made here is in no folder until Play builds it (H2-1)."""

    def loadout(self):
        loadout = Loadout(game=games.ANNO_1800, own=own_record(creator="Ay"))
        loadout.set_layer(ModLayer(origin="Host/shared piece", name="Shared", mod_id="shared"))
        loadout.set_layer(ModLayer(origin="Other", name="Other", mod_id="other"))
        patching.make(loadout, "Host/shared piece")
        return loadout

    def read_again(self, loadout, *layers):
        held = patching.held_over_a_read(loadout)
        for layer in list(loadout.layers):
            loadout.drop_layer(layer.origin, keep_authored=True)
        for layer in layers:
            loadout.set_layer(layer)
        patching.reattach(loadout)
        return patching.put_back(loadout, held)

    def test_it_is_put_back_under_its_mod(self):
        loadout = self.loadout()
        self.assertEqual(self.read_again(
            loadout, ModLayer(origin="Host/shared piece", mod_id="shared"),
            ModLayer(origin="Other", mod_id="other"),
        ), 1)
        self.assertEqual(loadout.patch_for("Host/shared piece").origin, "shared piece (my patch)")

    def test_and_follows_the_copy_that_loads_now(self):
        """Another mod added ships a newer copy of the shared piece, and that
        copy is the one the read keeps."""
        loadout = self.loadout()
        self.read_again(loadout, ModLayer(origin="Newer/shared piece", mod_id="shared"))
        self.assertEqual(loadout.layer("shared piece (my patch)").patches, "Newer/shared piece")

    def test_with_its_mod_gone_from_the_read_it_is_kept_and_has_nothing_to_patch(self):
        loadout = self.loadout()
        self.read_again(loadout, ModLayer(origin="Other", mod_id="other"))
        patch = loadout.layer("shared piece (my patch)")
        self.assertIsNotNone(patch)
        self.assertTrue(patching.is_inert(loadout, patch))

    def test_one_the_folder_gives_back_is_not_doubled(self):
        loadout = self.loadout()
        built = ModLayer(origin="shared piece (my patch)", mod_id="built",
                         declared={modinfo.STUDIO_KEY: {"patches": "shared"}})
        self.assertEqual(self.read_again(
            loadout, ModLayer(origin="Host/shared piece", mod_id="shared"), built), 0)
        self.assertIs(loadout.layer("shared piece (my patch)"), built)

    def test_a_lost_one_is_made_again_for_the_mod_it_was_named_after(self):
        loadout = Loadout(game=games.ANNO_1800, own=own_record(creator="Ay"))
        loadout.set_layer(ModLayer(origin="Host/shared piece", name="Shared", mod_id="shared"))
        loadout.set_change(Change(guid="1", asset_label="A", path="/Values/X", tag="X", old_value="1",
                                  new_value="2", origin="shared piece (my patch)", authored=True))
        self.assertEqual(patching.find_lost(loadout), 1)
        self.assertEqual(loadout.patch_for("Host/shared piece").origin, "shared piece (my patch)")
        self.assertEqual(loadout.layer("shared piece (my patch)").label, "Shared (my patch)")

    def test_but_not_where_two_mods_have_that_name(self):
        loadout = Loadout(game=games.ANNO_1800, own=own_record(creator="Ay"))
        loadout.set_layer(ModLayer(origin="Host/shared piece", mod_id="shared"))
        loadout.set_layer(ModLayer(origin="Else/shared piece", mod_id="shared2"))
        loadout.set_change(Change(guid="1", asset_label="A", path="/Values/X", tag="X", old_value="1",
                                  new_value="2", origin="shared piece (my patch)", authored=True))
        self.assertEqual(patching.find_lost(loadout), 0)


class TestAPatchFollowsWhicheverCopyLoads(unittest.TestCase):
    """A patch names the mod it patches by ModID, so it follows whichever
    host's copy of a shared piece the game loads -- and Play keeps building it
    while any copy does."""

    def loadout(self, a_on=True, b_on=True):
        loadout = Loadout(game=games.ANNO_1800)
        a = ModLayer(origin="A", name="Big Overhaul", active=a_on)
        b = ModLayer(origin="B", name="Other Mod", active=b_on)
        b.copies = {"B/Shared": "A/Shared"}
        b.copy_versions = {"B/Shared": "1.1"}
        for layer in (a, ModLayer(origin="A/Shared", mod_id="shared", version="1.3"), b,
                      ModLayer(origin="Shared (my patch)", patches="A/Shared")):
            loadout.set_layer(layer)
        return loadout

    def patch(self, loadout):
        return loadout.layer("Shared (my patch)")

    def test_with_the_copy_read_off_and_another_on_it_still_has_a_mod_to_patch(self):
        loadout = self.loadout(a_on=False)
        self.assertFalse(patching.is_inert(loadout, self.patch(loadout)))

    def test_with_every_copy_off_it_has_none(self):
        loadout = self.loadout(a_on=False, b_on=False)
        self.assertTrue(patching.is_inert(loadout, self.patch(loadout)))
        self.assertIn("switched off with Big Overhaul, which it came bundled in",
                      patching.why_inert(loadout, self.patch(loadout)))

    def test_and_it_is_said_to_be_written_against_another_version(self):
        from anno_modding_studio.core import dependencies

        loadout = self.loadout(a_on=False)
        said = dependencies.copies_worth_knowing(loadout)
        self.assertEqual(len(said), 1)
        self.assertIn("follows it to that copy, and was written against version 1.3", said[0])


if __name__ == "__main__":
    unittest.main()
