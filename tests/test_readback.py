"""A project another loadout changed is read back into this one.

``docs/proposals/projects-and-loadouts-proposal.md``, section 8, phase 6b:
its ModOps, as they are now, become the mod's changes in this loadout's load
order, and its hold is let go of.  See ``core/readback.py``.
"""

from __future__ import annotations

import json
import re
import shutil
import sys
import unittest
from dataclasses import replace
from pathlib import Path
from unittest import mock

import _isolation  # first: keeps the suite out of real files under any runner

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from lxml import etree  # noqa: E402
from support import SessionCase, modops_file  # noqa: E402

from anno_modding_studio.core import (  # noqa: E402
    generator,
    inside,
    library,
    loadercheck,
    modfile,
    modinfo,
    narrow,
    patching,
    places,
    projects,
    readback,
    stale,
)
from anno_modding_studio.core.changes import (  # noqa: E402
    OP_ADD,
    OP_ADD_NEXT,
    OP_REMOVE,
    Change,
    Loadout,
    ModLayer,
    Op,
    own_record,
)
from anno_modding_studio.core.workspace import Workspace  # noqa: E402

ALPHA = '<ModOp Type="merge" GUID="1000" Path="/Values/Building"><Radius>8</Radius></ModOp>'
DOUBLED = (
    '<ModOp Type="merge" GUID="1000" Path="/Values/Building/Radius">'
    '<Radius><ModValue Insert="self::node() * 2"/></Radius></ModOp>'
)


def radius(new, old="10", origin="", guid="1000") -> Change:
    return Change(
        guid=guid, asset_label="alpha", path="/Values/Building/Radius", tag="Radius",
        old_value=old, new_value=new, origin=origin, authored=True,
    )


def no_cost(origin="") -> Change:
    return Change(
        guid="1000", asset_label="alpha", path="/Values/Cost/Costs/Item[Ingredient='1011']",
        tag="Item", op=OP_REMOVE, origin=origin, authored=True,
        payload="<Item><Ingredient>1011</Ingredient><Amount>8</Amount></Item>",
    )


def upkeep(origin="") -> Change:
    return Change(
        guid="1001", asset_label="beta", path="/Values/Building", tag="Upkeep", op=OP_ADD,
        origin=origin, authored=True, payload="<Upkeep>3</Upkeep>",
    )


def made(guid="9001", origin="", note="") -> Change:
    """A new asset beside beta, as the New asset dialog makes one."""
    asset = etree.fromstring(
        "<Asset><Template>Building</Template><Values><Standard><GUID>%s</GUID>"
        "<Name>new</Name></Standard></Values></Asset>" % guid
    )
    return Change(
        guid="1001", asset_label="new  (new asset)", path="", tag="Asset", op=OP_ADD_NEXT,
        origin=origin, authored=True, note=note, in_game_name="New One",
        payload=etree.tostring(asset, pretty_print=True).decode().strip(),
    )


def said(loadout, origin=""):
    """What the mod's changes say, whatever this loadout read under them:
    not what a removal took out, which is what was there."""
    return sorted(
        (c.guid, c.path, c.op, c.new_value,
         "" if c.op == OP_REMOVE else loadercheck.canonical_fragment(c.payload or ""),
         c.active, c.note, c.written_as, c.op_uid)
        for c in loadout.changes.values() if c.origin == origin
    )


class _Case(SessionCase):
    maxDiff = None

    def setUp(self):
        _isolation.forget_loadouts()
        self.addCleanup(_isolation.forget_loadouts)
        self.root = places.loadouts_dir()
        self.root.mkdir(parents=True, exist_ok=True)

    def edit(self, loadout, *changes):
        """Make ``changes`` as Fields does, and end the step."""
        for change in changes:
            loadout.set_change(loadout.place(change))
        narrow.write(loadout)

    def save(self, loadout, name="Main") -> Path:
        path = self.root / ("%s.annoload" % name)
        loadout.save(path)
        return path

    def elsewhere(self, path, *changes, revert=(), name="Other") -> Loadout:
        """Open the loadout at ``path`` as another loadout, edit its mods
        there and save it as that one."""
        other = Loadout.load(path)
        for key in revert:
            other.changes.pop(key, None)
        self.edit(other, *changes)
        self.save(other, name)
        return other

    def read_back(self, loadout):
        """Read what is marked again and take it, as the window does."""
        stale.follow_projects(loadout)
        taken = stale.snapshot(loadout)
        reports = stale.read_again(taken, self.index)
        self.assertTrue(stale.still_asked(loadout, taken))
        moved = stale.take(loadout, reports)
        stale.give_up(loadout, [origin for origin in taken.asked if origin not in reports])
        return {found.origin: found for found in moved}


class TestTheOwnModReadBack(_Case):
    def own(self) -> Loadout:
        loadout = Loadout(own=own_record(name="My Mod", mod_id="me-my-mod", loads_last=True), own_mod=True)
        self.edit(loadout, radius("12"), no_cost(), upkeep(), made(note="the new one"))
        return loadout

    def test_its_changes_are_what_the_other_loadout_made_them(self):
        path = self.save(self.own())
        other = self.elsewhere(path, radius("15", old="10"))
        loaded = Loadout.load(path)
        self.assertEqual(loaded.held(""), projects.MOVED)
        moved = self.read_back(loaded)
        self.assertTrue(moved[""].project)
        self.assertEqual(loaded.held(""), "")
        self.assertEqual(said(loaded), said(other))
        # The same ModOps, each under its uid.
        self.assertEqual(
            sorted((op.uid, op.source, op.order, op.active, op.note) for op in loaded.narrow),
            sorted((op.uid, op.source, op.order, op.active, op.note) for op in other.narrow),
        )

    def test_the_value_before_it_is_read_where_it_stands_here(self):
        root = self.mods(a=ALPHA)
        path = self.save(self.own())
        self.elsewhere(path, radius("15", old="10"))
        loaded = Loadout.load(path)
        _session, there = loadercheck.read_folders([root / "a"], [root], self.index, "anno1800")
        for layer in there.layers:
            loaded.set_layer(layer)
        for op in there.ops:
            loaded.set_op(op)
        for change in there.changes.values():
            loaded.set_change(change)
        self.read_back(loaded)
        (mine,) = [c for c in loaded.changes_of("") if c.tag == "Radius"]
        self.assertEqual((mine.old_value, mine.new_value), ("8", "15"))

    def test_its_hold_is_let_go_of_and_it_saves_where_it_moves(self):
        path = self.save(self.own())
        self.elsewhere(path, radius("15", old="10"))
        loaded = Loadout.load(path)
        self.read_back(loaded)
        self.assertEqual(loaded.projects[""].read_at, 2)
        self.assertEqual(loaded.projects_to_save(), [])
        self.edit(loaded, radius("20", old="10"))
        self.assertEqual(loaded.projects_to_save(), ["My Mod"])
        loaded.save(path)
        project = projects.read(projects.where(loaded.projects[""].path))
        self.assertEqual(project["revision"], 3)
        self.assertIn("<Radius>20</Radius>", json.dumps(project["narrow"]))

    def test_one_the_other_loadout_took_back_is_gone(self):
        loadout = self.own()
        path = self.save(loadout)
        gone = next(c.key for c in loadout.changes.values() if c.tag == "Upkeep")
        other = self.elsewhere(path, revert=[gone])
        loaded = Loadout.load(path)
        self.read_back(loaded)
        self.assertEqual(said(loaded), said(other))
        self.assertFalse(any(c.tag == "Upkeep" for c in loaded.changes.values()))

    def test_one_switched_off_there_is_switched_off_here(self):
        loadout = self.own()
        path = self.save(loadout)
        other = Loadout.load(path)
        for change in other.changes.values():
            if change.tag == "Upkeep":
                change.active = False
        narrow.write(other)
        self.save(other, "Other")
        loaded = Loadout.load(path)
        self.read_back(loaded)
        self.assertEqual(said(loaded), said(other))

    def test_an_edit_here_never_saved_into_it_is_given_up_and_counted(self):
        path = self.save(self.own())
        self.elsewhere(path, radius("15", old="10"))
        loaded = Loadout.load(path)
        # Held: no ModOp is written for it.
        self.edit(loaded, Change(
            guid="1002", asset_label="gamma", path="/Values/Standard/Name", tag="Name",
            old_value="gamma", new_value="mine", authored=True,
        ))
        moved = self.read_back(loaded)
        self.assertEqual(moved[""].given_up, 1)
        self.assertFalse(any(c.guid == "1002" for c in loaded.changes.values()))

    def test_a_typed_op_and_an_exception_to_it(self):
        loadout = Loadout(own=own_record(name="My Mod", mod_id="me-my-mod"), own_mod=True)
        loadout.set_op(Op(source=ALPHA, origin="", authored=True, uid="typed", order=0, matched=["1000"]))
        loadout.set_change(Change(
            guid="1000", asset_label="alpha", path="/Values/Building/Radius", tag="Radius",
            old_value="10", new_value="8", op_uid="typed", authored=True, order=0,
        ))
        self.edit(loadout, Change(
            guid="1000", asset_label="alpha", path="/Values/Building/Radius", tag="Radius",
            old_value="8", new_value="9", authored=True,
        ))
        path = self.save(loadout)
        other = self.elsewhere(path, upkeep())
        loaded = Loadout.load(path)
        self.read_back(loaded)
        self.assertEqual([op.uid for op in loaded.ops], ["typed"])
        mine = {(c.new_value, c.op_uid, c.exception_of, bool(c.written_as)) for c in loaded.changes.values()
                if c.tag == "Radius"}
        self.assertEqual(mine, {("8", "typed", "", False), ("9", "", "typed", True)})
        self.assertEqual(said(loaded), said(other))


class TestOneModOpForSeveralChanges(_Case):
    """A ModOp the Studio writes for more than one change reads back as
    those changes, and is written again as the same ModOp."""

    def round_trip(self, *changes):
        loadout = Loadout(own=own_record(name="My Mod", mod_id="me-my-mod"), own_mod=True)
        self.edit(loadout, *changes)
        path = self.save(loadout)
        other = self.elsewhere(path, radius("5", old="4", guid="1001"))
        loaded = Loadout.load(path)
        self.read_back(loaded)
        return loaded, other

    def same_modops(self, loaded, other):
        self.assertEqual(
            sorted((op.uid, op.source, op.made, op.note, op.active) for op in loaded.narrow),
            sorted((op.uid, op.source, op.made, op.note, op.active) for op in other.narrow),
        )

    def test_a_node_swapped_whole(self):
        standard = etree.tostring(etree.fromstring(
            "<Standard><GUID>1000</GUID><Name>renamed</Name></Standard>"
        ), pretty_print=True).decode().strip()
        loaded, other = self.round_trip(
            Change(guid="1000", asset_label="alpha", path="/Values/Standard", tag="Standard",
                   op=OP_REMOVE, authored=True),
            Change(guid="1000", asset_label="alpha", path="/Values", tag="Standard", op=OP_ADD,
                   payload=standard, note="swapped", authored=True),
        )
        # Read narrowly, as the one field in it that moved, which would be
        # written as another ModOp: it is kept as the op it is.
        (swap,) = [op for op in other.narrow if 'GUID="1000"' in op.source]
        self.assertNotIn(swap.uid, {op.uid for op in loaded.narrow})
        (kept,) = loaded.ops
        self.assertEqual((kept.uid, kept.source, kept.note, kept.authored),
                         (swap.uid, swap.source, "swapped", True))
        self.assertEqual(
            [(c.tag, c.op_uid) for c in loaded.changes_of("") if c.guid == "1000"], [("Name", swap.uid)]
        )
        self.assertEqual([op.uid for op in loaded.narrow],
                         [op.uid for op in other.narrow if op.uid != swap.uid])

    def test_one_that_comes_to_nothing_here_is_kept_as_it_is(self):
        # Aimed at an asset a mod makes that this loadout does not have.
        loaded, other = self.round_trip(radius("12", old="5", guid="7777"))
        (aimed,) = [op for op in other.narrow if 'GUID="7777"' in op.source]
        (kept,) = loaded.ops
        self.assertEqual((kept.uid, kept.source, kept.authored), (aimed.uid, aimed.source, True))
        self.assertNotIn(aimed.uid, {op.uid for op in loaded.narrow})
        self.assertIn('GUID="7777"', generator.build_modops_xml(loaded))

    def test_two_assets_made_side_by_side(self):
        first, second = made("9001", note="one"), made("9002", note="two")
        second.spot = 1
        loaded, other = self.round_trip(first, second)
        self.same_modops(loaded, other)
        self.assertEqual(said(loaded), said(other))
        names = {c.in_game_name for c in loaded.changes.values() if c.creates_asset}
        self.assertEqual(names, {"New One"})

    def test_the_projects_text_is_what_the_export_writes(self):
        """A11-40: the text read back gave a ModOp for several changes one
        comment for all their notes, and the export one to each."""
        first, second = made("9001", note="one"), made("9002", note="two")
        second.spot = 1
        loadout = Loadout(own=own_record(name="My Mod", mod_id="me-my-mod"), own_mod=True)
        self.edit(loadout, first, second)
        exported = generator.build_modops_xml(loadout)
        self.assertIn("<!-- note: one -->\n  <!-- note: two -->\n  <ModOp", exported)
        self.assertEqual(readback.shown(loadout, "").text, exported)
        # Switched off, it is the same ModOp in the comment.
        for change in loadout.changes.values():
            change.active = False
        narrow.write(loadout)
        self.assertEqual(readback.shown(loadout, "").text, generator.build_modops_xml(loadout))


class TestAPatchReadBack(_Case):
    def test_a_patch_and_the_mods_after_it(self):
        root = self.mods(a=ALPHA, b=DOUBLED)
        _session, loadout = loadercheck.read_folders(
            [root / "a", root / "b"], [root], self.index, "anno1800"
        )
        loadout.set_layer(ModLayer(origin="Patch", name="Patch", patches="a", order=1.5))
        self.edit(loadout, radius("12", old="8", origin="Patch"))
        path = self.save(loadout)
        self.assertIn("Patch", loadout.projects)
        self.elsewhere(path, radius("20", old="8", origin="Patch"))
        loaded = Loadout.load(path)
        self.assertEqual(loaded.held("Patch"), projects.MOVED)
        stale.follow_projects(loaded)
        # And the mod it patches, for what the patch keeps inside it.
        self.assertEqual(stale.waiting(loaded), ["a", "Patch", "b"])
        self.read_back(loaded)
        self.assertEqual(loaded.held("Patch"), "")
        (patched,) = [c for c in loaded.changes_of("Patch") if c.tag == "Radius"]
        self.assertEqual((patched.old_value, patched.new_value), ("8", "20"))
        self.assertEqual(
            [c.new_value for c in loaded.changes_of("b") if c.tag == "Radius"], ["40"]
        )


class TestOnePatchToAMod(_Case):
    """A patch is how a mod should have been wherever it is used (section
    5.1): a loadout making a patch of a mod you have patched elsewhere takes
    up that patch's project rather than making a second."""

    def loadout_of(self, root):
        _session, loadout = loadercheck.read_folders(
            [root / "a", root / "b"], [root], self.index, "anno1800"
        )
        return loadout

    def patched(self, root):
        from anno_modding_studio.core import patching

        loadout = self.loadout_of(root)
        patch = patching.make(loadout, "a")
        self.edit(loadout, radius("12", old="8", origin=patch.origin))
        self.save(loadout)
        return loadout, patch

    def found(self, loadout):
        from anno_modding_studio.core import filecache, modfolder

        theirs = loadout.layer("a")
        return filecache.patch_project_for(modfolder.match_keys(theirs.mod_id, "a"), loadout.game)

    def test_the_cache_finds_the_patch_of_a_mod(self):
        root = self.mods(a=ALPHA, b=DOUBLED)
        first, patch = self.patched(root)
        other = self.loadout_of(root)
        self.assertEqual(self.found(other), projects.where(first.projects[patch.origin].path))

    def test_a_mod_with_no_patch_has_none(self):
        root = self.mods(a=ALPHA, b=DOUBLED)
        self.assertIsNone(self.found(self.loadout_of(root)))

    def test_joined_it_is_the_patch_as_its_project_holds_it(self):
        root = self.mods(a=ALPHA, b=DOUBLED)
        first, patch = self.patched(root)
        other = self.loadout_of(root)
        made = readback.join_patch(other, "a", self.found(other))
        stale.read_project_now(other, made.origin, self.index)
        self.assertEqual((made.origin, made.patches), (patch.origin, "a"))
        self.assertEqual(other.held(made.origin), "")
        self.assertEqual(other.projects[made.origin].path, first.projects[patch.origin].path)
        (change,) = [c for c in other.changes_of(made.origin) if c.tag == "Radius"]
        self.assertEqual((change.old_value, change.new_value), ("8", "12"))
        self.assertTrue(change.written_as)
        # What loads after it is read again in the background.
        self.assertEqual(stale.waiting(other), ["b"])

    def test_an_edit_in_it_reaches_the_loadout_it_was_made_in(self):
        root = self.mods(a=ALPHA, b=DOUBLED)
        _first, patch = self.patched(root)
        other = self.loadout_of(root)
        made = readback.join_patch(other, "a", self.found(other))
        stale.read_project_now(other, made.origin, self.index)
        self.edit(other, radius("14", old="8", origin=made.origin))
        self.save(other, "Other")
        main = Loadout.load(self.root / "Main.annoload")
        self.assertEqual(main.held(patch.origin), projects.MOVED)
        self.read_back(main)
        (change,) = [c for c in main.changes_of(patch.origin) if c.tag == "Radius"]
        self.assertEqual(change.new_value, "14")


class TestACarriedOnModReadBack(_Case):
    def test_it_is_read_from_its_project_and_not_its_folder(self):
        root = self.mods(Mine=ALPHA)
        (root / "Mine" / "modinfo.json").write_text(json.dumps({
            "ModID": "me-mine", "ModName": {"English": "Mine"},
            modinfo.STUDIO_KEY: {modinfo.BUILD_KEY: 3},
        }), encoding="utf-8")
        _session, loadout = loadercheck.read_folders([root / "Mine"], [root], self.index, "anno1800")
        loadout.home = "Mine"
        self.edit(loadout, radius("12", old="8", origin="Mine"))
        path = self.save(loadout)
        self.assertIn("Mine", loadout.projects)
        other = self.elsewhere(path, radius("20", old="8", origin="Mine"))
        # And built again there, under a build number this loadout never read.
        (root / "Mine" / "modinfo.json").write_text(json.dumps({
            "ModID": "me-mine", "ModName": {"English": "Mine"},
            modinfo.STUDIO_KEY: {modinfo.BUILD_KEY: 4},
        }), encoding="utf-8")
        loaded = Loadout.load(path)
        self.assertEqual(stale.follow(loaded), [])
        self.read_back(loaded)
        self.assertEqual(loaded.held("Mine"), "")
        self.assertEqual(said(loaded, "Mine"), said(other, "Mine"))
        (mine,) = [c for c in loaded.changes_of("Mine") if c.authored]
        self.assertEqual((mine.old_value, mine.new_value), ("8", "20"))


class TestTakingUpAProject(_Case):
    """A mod of yours read in from its folder, whose project another loadout
    made: taking that project up -- carrying on with the mod, or making it
    a project of yours -- reads it back, so a save writes nothing over what
    the project holds (section 4.6 of the projects and loadouts proposal)."""

    def folder(self, build=3):
        root = self.mods(Mine=ALPHA)
        (root / "Mine" / "modinfo.json").write_text(json.dumps({
            "ModID": "me-mine", "ModName": {"English": "Mine"}, "CreatorName": "Me",
            modinfo.STUDIO_KEY: {modinfo.BUILD_KEY: build},
        }), encoding="utf-8")
        return root

    def read_in(self, root) -> Loadout:
        _session, loadout = loadercheck.read_folders([root / "Mine"], [root], self.index, "anno1800")
        return loadout

    def first(self, root):
        """The loadout the project was made in: an edit, a checkpoint, a
        build record further on than the folder, and a mod.io page."""
        loadout = self.read_in(root)
        loadout.home = "Mine"
        self.edit(loadout, radius("12", old="8", origin="Mine"))
        loadout.checkpoint("v1", origin="Mine", loadout="Main")
        loadout.builds["Mine"] = {"build": 5, "digest": "d"}
        loadout.modio_mod_id = 77
        path = self.save(loadout)
        return loadout, path, projects.where(loadout.projects["Mine"].path)

    def taken_up(self, root, found, home=True, before=()):
        other = self.read_in(root)
        if home:
            other.home = "Mine"
        else:
            other.set_layer(replace(other.layer("Mine"), kept=True))
        self.edit(other, *before)
        given_up = readback.join(other, "Mine", found)
        stale.read_project_now(other, "Mine", self.index)
        return other, given_up

    def test_carried_on_it_is_the_mod_as_its_project_holds_it(self):
        root = self.folder()
        first, _path, found = self.first(root)
        other, given_up = self.taken_up(root, found)
        self.assertEqual((given_up, other.held("Mine")), (0, ""))
        self.assertEqual(other.projects["Mine"].path, first.projects["Mine"].path)
        self.assertEqual(said(other, "Mine"), said(first, "Mine"))
        self.assertEqual([checkpoint.label for checkpoint in other.history_of("Mine")], ["v1"])
        self.assertEqual(other.builds["Mine"]["build"], 5)
        self.assertEqual(other.modio_mod_id, 77)
        # Its modinfo as read is still the folder's, so the folder, an older
        # build than the project's last, is not taken for an update of it.
        self.assertEqual(stale.follow(other), [])

    def test_made_a_project_it_is_too(self):
        root = self.folder()
        first, _path, found = self.first(root)
        other, _given_up = self.taken_up(root, found, home=False)
        self.assertEqual(said(other, "Mine"), said(first, "Mine"))
        self.assertIn("Mine", other.project_origins())
        # The mod.io page is the carried-on mod's, which this loadout has none of.
        self.assertEqual(other.modio_mod_id, 0)

    def test_a_save_writes_nothing_over_the_project(self):
        root = self.folder()
        _first, path, found = self.first(root)
        was = projects.read(found)
        other, _given_up = self.taken_up(root, found)
        self.save(other, "Other")
        now = projects.read(found)
        self.assertEqual(now["revision"], was["revision"])
        self.assertEqual(now.get("history"), was.get("history"))
        self.assertEqual(now.get("build"), was.get("build"))
        self.assertEqual(Loadout.load(path).held("Mine"), "")

    def test_an_edit_there_reaches_the_loadout_it_was_made_in(self):
        root = self.folder()
        _first, path, found = self.first(root)
        other, _given_up = self.taken_up(root, found)
        self.edit(other, radius("14", old="8", origin="Mine"))
        self.save(other, "Other")
        main = Loadout.load(path)
        self.read_back(main)
        (change,) = [c for c in main.changes_of("Mine") if c.authored]
        self.assertEqual(change.new_value, "14")
        self.assertEqual([checkpoint.label for checkpoint in main.history_of("Mine")], ["v1"])

    def test_what_was_done_to_it_here_first_is_given_up(self):
        root = self.folder()
        first, _path, found = self.first(root)
        other, given_up = self.taken_up(root, found, home=False,
                                        before=[radius("30", old="8", origin="Mine")])
        self.assertEqual(given_up, 1)
        self.assertEqual(said(other, "Mine"), said(first, "Mine"))

    def test_its_build_moving_is_no_update_to_read_from_the_folder(self):
        """R3-40, hands-on run 3: Update built the mod again elsewhere and the
        project's revision stayed; opened, the loadout took the new build for
        an update and read the mod from its folder, whose ops are the
        project's own written out -- and the next save doubled them."""
        root = self.folder()
        _first, path, _found = self.first(root)
        (root / "Mine" / "modinfo.json").write_text(json.dumps({
            "ModID": "me-mine", "ModName": {"English": "Mine"}, "CreatorName": "Me",
            modinfo.STUDIO_KEY: {modinfo.BUILD_KEY: 9},
        }), encoding="utf-8")
        loaded = Loadout.load(path)
        self.assertEqual(loaded.held("Mine"), "")
        self.assertEqual(stale.follow(loaded), [])
        self.assertEqual(stale.updated(loaded), [])

    def test_one_that_cannot_be_read_leaves_the_mod_as_read(self):
        root = self.folder()
        other = self.read_in(root)
        other.home = "Mine"
        broken = self.root / "Broken.annoproj"
        broken.write_text("{", encoding="utf-8")
        before = said(other, "Mine")
        with self.assertRaises(projects.ProjectError):
            readback.join(other, "Mine", broken)
        self.assertEqual(said(other, "Mine"), before)
        self.assertNotIn("Mine", other.projects)


MERGE = '<ModOp Type="merge" GUID="1000" Path="/Values/Building"><Radius>%s</Radius></ModOp>'
GROUPED = (MERGE % "8" + '<Group Condition="#mod-x">' + MERGE % "9" + MERGE % "10" + "</Group>"
           + MERGE % "11")


class TestAPatchsEditsInAnotherLoadout(_Case):
    """What a patch keeps inside their mod reaches another loadout that
    reads their mod for itself, by text: the uids a reading gives their ops
    are that loadout's alone.  Each loadout writes their mod as the one the
    edits were made in does: an op taken out is out, and an op of yours is
    after the op of theirs it followed, in the groups it was in."""

    def read_in(self, root) -> Loadout:
        _session, loadout = loadercheck.read_folders([root / "a"], [root], self.index, "anno1800")
        return loadout

    @staticmethod
    def said(loadout) -> list:
        """Their mod as the loadout writes it, a line to each op and tag."""
        return [line.strip() for line in modfile.shown(loadout, "a").text.splitlines()
                if line.strip().startswith(("<Group", "</Group", "<Radius"))]

    def type_in(self, loadout, value, typed):
        """Type over the op of theirs that sets the radius to ``value`` in
        the Mod file tab: ``typed``, where "SAME" is that op as it was."""
        shown = modfile.shown(loadout, "a")
        (old,) = re.findall(r"<ModOp[^>]*>\s*<Radius>%s</Radius>\s*</ModOp>" % value, shown.text)
        read, done = modfile.apply(Workspace(self.index, loadout), "a",
                                   shown.text.replace(old, typed.replace("SAME", old)), shown)
        self.assertIsNotNone(done, (read.error, read.loose, read.refused))
        inside.assign(loadout)

    def read_again(self, loadout):
        """Their mod read again from its folder, as after an update."""
        stale._mark(loadout.layer("a"), stale.UPDATED, "a", anyway=True)
        taken = stale.snapshot(loadout)
        stale.take(loadout, stale.read_again(taken, self.index))

    def patched(self):
        """The first loadout, with a patch that keeps one of each: an op of
        theirs in a group typed over, one taken out, two of yours after
        the same op of theirs, and one at the end of the group."""
        root = self.mods(a=GROUPED)
        first = self.read_in(root)
        patch = patching.make(first, "a")
        first.editing = patch.origin
        self.save(first, "First")
        return root, first, patch

    def edited(self, first):
        self.type_in(first, "9", MERGE % "19")
        self.type_in(first, "11", "")
        self.type_in(first, "8", "SAME\n" + MERGE % "50" + "\n" + MERGE % "51")
        self.type_in(first, "10", "SAME\n" + MERGE % "60")
        self.save(first, "First")

    def test_one_that_had_the_patch_reads_it_back_as_written_there(self):
        root, first, patch = self.patched()
        other = self.read_in(root)
        readback.join_patch(other, "a", projects.where(first.projects[patch.origin].path))
        stale.read_project_now(other, patch.origin, self.index)
        path = self.save(other, "Other")
        self.edited(first)
        other = Loadout.load(path)
        self.assertEqual(other.held(patch.origin), projects.MOVED)
        self.read_back(other)
        self.assertEqual(self.said(other), self.said(first))
        self.assertNotIn("<Radius>9</Radius>", self.said(other))
        self.assertNotIn("<Radius>11</Radius>", self.said(other))
        # And so it stays when their mod is read again there.
        self.read_again(other)
        self.assertEqual(self.said(other), self.said(first))

    def test_one_that_takes_it_up_after_puts_them_where_they_were(self):
        root, first, patch = self.patched()
        self.edited(first)
        other = self.read_in(root)
        readback.join_patch(other, "a", projects.where(first.projects[patch.origin].path))
        stale.read_project_now(other, patch.origin, self.index)
        self.assertEqual(self.said(other), self.said(first))
        typed_over = next(op for op in other.ops if "<Radius>19</Radius>" in op.source)
        group = next(op for op in other.ops if op.origin == "a" and op.source.startswith("<Group"))
        self.assertEqual(typed_over.within, [group.uid])

    def test_the_first_writes_it_the_same_when_their_mod_is_read_again(self):
        _root, first, _patch = self.patched()
        self.edited(first)
        before = self.said(first)
        self.assertEqual(before, [
            "<Radius>8</Radius>", "<Radius>50</Radius>", "<Radius>51</Radius>",
            '<Group Condition="#mod-x">', "<Radius>19</Radius>", "<Radius>10</Radius>",
            "<Radius>60</Radius>", "</Group>",
        ])
        self.read_again(first)
        self.assertEqual(self.said(first), before)


class TestAModCopiedOutOfModIo(_Case):
    """R3-12, hands-on run 3: a mod the conversion copied out of mod.io's
    folder keeps the name it was read under there, ``3293951/Docklands``,
    and its link sits in the mods folder.  Placed by counting its name's
    parts, its top was the mods folder itself; every mod read beside it was
    named after it, none was found again, and the project waiting to be
    read back never was, so the loadout could not be saved."""

    def test_the_mods_beside_it_are_read_under_their_names(self):
        root = self.mods(a=ALPHA, Docklands=DOUBLED)
        _session, loadout = loadercheck.read_folders(
            [root / "a", root / "Docklands"], [root], self.index, "anno1800")
        old, new = "Docklands", "3293951/Docklands"
        layer = loadout.layer(old)
        loadout.drop_layer(old)
        loadout.set_layer(replace(layer, origin=new))
        taken = stale.snapshot(loadout, asked=["a", new])
        found = stale.read_again(taken, self.index)
        self.assertEqual(sorted(found), sorted(["a", new]))
        # Docklands loads first: it doubles the game's 10.
        self.assertEqual([c.new_value for c in found[new].changes if c.tag == "Radius"], ["20"])
        self.assertEqual([c.new_value for c in found["a"].changes if c.tag == "Radius"], ["8"])

    def test_a_project_beside_it_is_read_back(self):
        root = self.mods(a=ALPHA, Docklands=DOUBLED)
        _session, loadout = loadercheck.read_folders(
            [root / "a", root / "Docklands"], [root], self.index, "anno1800")
        layer = loadout.layer("Docklands")
        loadout.drop_layer("Docklands")
        loadout.set_layer(replace(layer, origin="3293951/Docklands"))
        loadout.make_own_mod()
        self.edit(loadout, radius("12", old="8"))
        path = self.save(loadout)
        self.elsewhere(path, radius("15", old="8"))
        loaded = Loadout.load(path)
        self.assertEqual(loaded.held(""), projects.MOVED)
        self.read_back(loaded)
        self.assertEqual(loaded.held(""), "")
        (mine,) = [c for c in loaded.changes_of("") if c.authored]
        self.assertEqual(mine.new_value, "15")


class TestAProjectFoundAgain(_Case):
    def test_one_moved_away_is_found_by_its_modid_and_read_back(self):
        loadout = Loadout(own=own_record(name="My Mod", mod_id="me-my-mod"), own_mod=True)
        self.edit(loadout, radius("12"))
        path = self.save(loadout)
        was = projects.where(loadout.projects[""].path)
        moved_to = was.with_name("Renamed.annoproj")
        shutil.move(str(was), str(moved_to))
        loaded = Loadout.load(path)
        self.assertEqual(loaded.held(""), projects.MOVED)
        self.assertEqual(projects.where(loaded.projects[""].path), moved_to)
        self.assertEqual(loaded.projects[""].lost, str(was))
        self.read_back(loaded)
        self.assertEqual(loaded.held(""), "")
        self.assertEqual([c.new_value for c in loaded.changes.values()], ["12"])


class TestTheOwnModsMissingProjectStandsIn(_Case):
    """The loadout's own mod, whose project cannot be found, is read from the
    library's build of it as a patch is (section 8), and nothing is written
    into it until its project is found."""

    def missing(self):
        root = self.mods(a=ALPHA)
        _session, loadout = loadercheck.read_folders([root / "a"], [root], self.index, "anno1800")
        loadout.own.name, loadout.own.mod_id, loadout.own_mod = "My Mod", "me-my-mod", True
        self.edit(loadout, radius("12", old="8"))
        path = self.save(loadout)
        project = projects.where(loadout.projects[""].path)
        project.unlink()
        return root, path, project

    def built(self, root, value="20"):
        """The own mod as Play built it into the library."""
        folder = root / "[Gameplay] My Mod"
        (folder / "data/base/config/export").mkdir(parents=True, exist_ok=True)
        (folder / "data/base/config/export/assets.xml").write_text(modops_file(
            '<ModOp Type="replace" GUID="1000" Path="/Values/Building/Radius">'
            "<Radius>%s</Radius></ModOp>" % value
        ), encoding="utf-8")
        (folder / "modinfo.json").write_text(json.dumps({
            "ModID": "me-my-mod", "ModName": {"English": "My Mod"}, "Version": "2.0",
            "CreatorName": "Me", modinfo.STUDIO_KEY: {modinfo.BUILD_KEY: 3},
        }), encoding="utf-8")
        return folder

    @staticmethod
    def catalogued(*folders):
        return mock.patch.object(readback.library_mod, "catalogue", return_value=[
            library.Held(name=folder.name, link=folder, target=folder, mod_id="me-my-mod",
                         source="built")
            for folder in folders
        ])

    def test_its_build_is_read_in_its_place(self):
        root, path, project = self.missing()
        build = self.built(root)
        loaded = Loadout.load(path)
        self.assertEqual(loaded.held(""), projects.MISSING)
        with self.catalogued(build):
            self.assertEqual(readback.stand_in(loaded, "", root), build)
            stale.follow_projects(loaded, root)
        # Its details went with its project: its build's modinfo says them.
        self.assertEqual((loaded.own.name, loaded.own.mod_id, loaded.own.version),
                         ("My Mod", "me-my-mod", "2.0"))
        # And "a", which sorts after it by name, as after any mod before it moved.
        self.assertEqual(stale.waiting(loaded), ["", "a"])
        self.assertEqual(stale.why(loaded, loaded.own_layer()),
                         ["Its project is missing, and the library's build of it is read instead."])
        self.read_back(loaded)
        self.assertEqual([(c.new_value, c.authored) for c in loaded.changes_of("")], [("20", False)])
        self.assertEqual(loaded.own.stale, [])
        self.assertEqual(loaded.held(""), projects.MISSING)
        # The rest still saves, and nothing writes a project for it.
        loaded.save(path)
        self.assertFalse(project.exists())

    def test_the_build_its_project_made_is_the_one(self):
        root, path, _project = self.missing()
        build = self.built(root)
        loaded = Loadout.load(path)
        with self.catalogued(), mock.patch.object(
                readback.library_mod, "built_from", return_value=[build.name]):
            self.assertEqual(readback.stand_in(loaded, "", root), build)

    def test_not_one_of_two_with_its_modid(self):
        root, path, _project = self.missing()
        build = self.built(root)
        other = root / "Another copy"
        other.mkdir()
        loaded = Loadout.load(path)
        with self.catalogued(build, other):
            self.assertIsNone(readback.stand_in(loaded, "", root))

    def test_an_edit_to_it_is_one_no_project_can_keep(self):
        from anno_modding_studio.core.undo import UndoHistory, held_in

        _root, path, _project = self.missing()
        loaded = Loadout.load(path)
        history = UndoHistory(loaded)
        self.edit(loaded, radius("30", old="8"))
        step = history.commit()
        self.assertEqual(held_in(loaded, step), [""])
        history.take_back()
        self.assertFalse(any(change.new_value == "30" for change in loaded.changes.values()))
        self.assertFalse(history.can_redo)
        # And one kept inside somebody else's ops by it.
        loaded.op(next(op.uid for op in loaded.ops if op.origin == "a")).active = False
        self.assertEqual(held_in(loaded, history.commit()), [""])

    def test_unloading_their_mod_is_no_edit_to_it(self):
        from anno_modding_studio.core.undo import UndoHistory, held_in

        _root, path, _project = self.missing()
        loaded = Loadout.load(path)
        loaded.op(next(op.uid for op in loaded.ops if op.origin == "a")).active = False
        history = UndoHistory(loaded)
        loaded.drop_layer("a")
        self.assertEqual(held_in(loaded, history.commit()), [])


class TestAMissingProjectStandsIn(_Case):
    """Section 8: a project that cannot be found is said to be missing, and
    the library's build of it is read in its place, not to be edited."""

    def built(self, root, name="Patch", value="20", build=3):
        """The patch as Play built it into the library: its ModOps, and the
        modinfo the Studio writes, with its ModID and build number."""
        folder = root / name / "data/base/config/export"
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "assets.xml").write_text(modops_file(
            '<ModOp Type="replace" GUID="1000" Path="/Values/Building/Radius">'
            "<Radius>%s</Radius></ModOp>" % value
        ), encoding="utf-8")
        (root / name / "modinfo.json").write_text(json.dumps({
            "ModID": "me-patch", "ModName": {"English": "My Patch"}, "Version": "1.0",
            modinfo.STUDIO_KEY: {modinfo.BUILD_KEY: build},
        }), encoding="utf-8")
        return root / name

    def missing(self):
        root = self.mods(a=ALPHA, b=DOUBLED)
        _session, loadout = loadercheck.read_folders(
            [root / "a", root / "b"], [root], self.index, "anno1800"
        )
        loadout.set_layer(ModLayer(origin="Patch", name="My Patch", patches="a", order=1.5,
                                   mod_id="me-patch"))
        self.edit(loadout, radius("12", old="8", origin="Patch"))
        path = self.save(loadout)
        project = projects.where(loadout.projects["Patch"].path)
        project.unlink()
        return root, path, project

    def catalogued(self, folder):
        return mock.patch.object(readback.library_mod, "catalogue", return_value=[
            library.Held(name=folder.name, link=folder, target=folder, mod_id="me-patch",
                         source="built"),
        ])

    def test_it_is_said_missing_and_its_build_is_read_instead(self):
        root, path, project = self.missing()
        build = self.built(root)
        loaded = Loadout.load(path)
        self.assertEqual(loaded.held_projects(), [("Patch", projects.MISSING, str(project))])
        with self.catalogued(build):
            self.assertEqual(readback.stand_in(loaded, "Patch", root), build)
            stale.follow_projects(loaded, root)
        self.assertEqual(stale.waiting(loaded), ["Patch", "b"])
        self.assertEqual(stale.why(loaded, loaded.layer("Patch")),
                         ["Its project is missing, and the library's build of it is read instead."])
        layer = loaded.layer("Patch")
        self.assertEqual((layer.folder, layer.name, layer.mod_id), (str(build), "My Patch", "me-patch"))
        self.read_back(loaded)
        # Read like anybody else's mod: nothing of it is yours to edit.
        patched = loaded.changes_of("Patch")
        self.assertEqual([(c.new_value, c.authored) for c in patched], [("20", False)])
        self.assertEqual([c.new_value for c in loaded.changes_of("b") if c.tag == "Radius"], ["40"])
        self.assertEqual(loaded.held("Patch"), projects.MISSING)
        loaded.editing = "Patch"
        self.assertEqual(loaded.editing_origin(), "")
        # The rest still saves, and nothing writes a project for it.
        loaded.save(path)
        self.assertFalse(project.exists())
        self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["projects"][0]["read_at"], -1)

    def test_not_a_build_another_project_made(self):
        root, path, _project = self.missing()
        build = self.built(root)
        elsewhere = places.projects_dir() / "Somebody's.annoproj"
        elsewhere.write_text("{}", encoding="utf-8")
        loaded = Loadout.load(path)
        with self.catalogued(build), mock.patch.object(
            library, "built_by", return_value=projects.as_written(elsewhere)
        ):
            self.assertIsNone(readback.stand_in(loaded, "Patch", root))
        with self.catalogued(build), mock.patch.object(
            library, "built_by", return_value=loaded.projects["Patch"].path
        ):
            self.assertEqual(readback.stand_in(loaded, "Patch", root), build)

    def test_its_build_number_is_followed(self):
        root, path, _project = self.missing()
        build = self.built(root)
        loaded = Loadout.load(path)
        with self.catalogued(build):
            stale.follow_projects(loaded, root)
        self.read_back(loaded)
        self.assertEqual(stale.follow(loaded), [])
        self.built(root, value="30", build=4)
        self.assertEqual(stale.follow(loaded), ["Patch", "b"])
        self.read_back(loaded)
        self.assertEqual([c.new_value for c in loaded.changes_of("Patch")], ["30"])

    def test_found_again_where_it_was_it_is_read_back(self):
        root, path, project = self.missing()
        build = self.built(root)
        loaded = Loadout.load(path)
        with self.catalogued(build):
            stale.follow_projects(loaded, root)
        self.read_back(loaded)
        loaded.save(path)
        # Put back as it was, with its revision: what was read here was
        # the build, so it is read back all the same.
        other = Loadout.load(path)
        other.projects.clear()
        other.set_layer(ModLayer(origin="Patch", name="My Patch", patches="a", order=1.5,
                                 mod_id="me-patch"))
        for change in list(other.changes_of("Patch")):
            other.changes.pop(change.key)
        self.edit(other, radius("12", old="8", origin="Patch"))
        other.save(self.root / "Other.annoload")
        shutil.move(str(projects.where(other.projects["Patch"].path)), str(project))
        again = Loadout.load(path)
        self.assertEqual(again.held("Patch"), projects.MOVED)
        stale.follow_projects(again, root)
        self.assertNotIn([stale.LIBRARY, "Patch"], again.layer("Patch").stale)
        # Found again, not changed by another loadout (R3-42, hands-on run 3).
        self.assertEqual(stale.why(again, again.layer("Patch")),
                         ["It is read from its project, which this loadout has not read yet."])
        moved = self.read_back(again)
        self.assertEqual((moved["Patch"].project, moved["Patch"].first), (True, True))
        self.assertFalse(stale.first_reading(again, "Patch"))
        self.assertEqual(again.held("Patch"), "")
        self.assertEqual([(c.new_value, c.authored) for c in again.changes_of("Patch")], [("12", True)])

    def test_with_no_build_it_stays_as_it_was_read(self):
        _root, path, _project = self.missing()
        loaded = Loadout.load(path)
        self.assertIsNone(readback.stand_in(loaded, "Patch", None))
        self.assertEqual(stale.follow_projects(loaded), [])
        self.assertEqual([c.new_value for c in loaded.changes_of("Patch")], ["12"])


class TestShownAsTheProjectHasIt(_Case):
    def test_its_modops_stand_after_its_ops_and_nothing_of_the_old_reading(self):
        loadout = Loadout(own_mod=True)
        loadout.set_op(Op(source=ALPHA, origin="", authored=True, uid="typed", order=0))
        self.edit(loadout, radius("12"))
        loadout.changes.clear()
        text = readback.shown(loadout, "").text
        self.assertLess(text.index("<Radius>8"), text.index("<Radius>12"))


if __name__ == "__main__":
    unittest.main()
