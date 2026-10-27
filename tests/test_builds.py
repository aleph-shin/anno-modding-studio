"""Build numbers: counted per mod of yours, only when the output differs."""

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

from anno_modding_studio import __version__  # noqa: E402
from anno_modding_studio.core import builds, games, generator, modinfo  # noqa: E402
from anno_modding_studio.core.changes import (  # noqa: E402
    Change,
    Loadout,
    ModLayer,
    own_record,
    switched_on,
)

FINGERPRINT = "0123456789abcdef0123456789abcdef01234567"
THEIRS = "[Gameplay] Their Mod"
PATCH = "[Gameplay] Their Mod ~ patch"


def _change(value: str, origin: str = "", guid: str = "1010340") -> Change:
    return Change(
        guid=guid,
        asset_label="Lumberjack's Hut",
        path="/Values/Factory7/CycleTime",
        tag="CycleTime",
        old_value="30",
        new_value=value,
        origin=origin,
        authored=True,
    )


def _loadout() -> Loadout:
    loadout = Loadout(own=own_record(name="Faster Lumber", version="1.2"), game=games.ANNO_1800)
    loadout.own_mod = True
    loadout.base_archive = "data30.rda"
    loadout.base_fingerprint = FINGERPRINT
    loadout.set_change(_change("15"))
    return loadout


def _write(loadout: Loadout, origin: str = ""):
    """What an export does: plan, and keep the build."""
    exported = generator.for_mod(loadout, origin)
    xml = generator.build_modops_xml(exported)
    build = builds.plan(loadout, origin, exported, xml)
    builds.record(loadout, build)
    return build, xml, exported


class NumberingTest(unittest.TestCase):
    def test_the_first_build_is_one(self):
        loadout = _loadout()
        build, _xml, _exported = _write(loadout)
        self.assertEqual((build.number, build.new), (1, True))
        self.assertEqual(builds.number(loadout), 1)

    def test_the_same_output_again_is_the_same_build(self):
        loadout = _loadout()
        _write(loadout)
        build, _xml, _exported = _write(loadout)
        self.assertEqual((build.number, build.new), (1, False))

    def test_a_change_is_a_new_build(self):
        loadout = _loadout()
        _write(loadout)
        loadout.set_change(_change("12"))
        build, _xml, _exported = _write(loadout)
        self.assertEqual((build.number, build.new), (2, True))

    def test_comments_do_not_count(self):
        # A note: none of it is what the mod does.
        loadout = _loadout()
        _write(loadout)
        next(iter(loadout)).note = "said since"
        build, xml, _exported = _write(loadout)
        self.assertIn("note: said since", xml)
        self.assertFalse(build.new)

    def test_nor_does_what_is_switched_off(self):
        # Written as a comment: the game reads the same file with it as
        # without it.
        loadout = _loadout()
        _write(loadout)
        extra = _change("20", guid="1010341")
        extra.active = False
        loadout.set_change(extra)
        build, xml, _exported = _write(loadout)
        self.assertIn(">20<", xml)
        self.assertFalse(build.new)

    def test_other_game_data_is_a_new_build(self):
        loadout = _loadout()
        _write(loadout)
        loadout.base_fingerprint = "f" * 40
        build, _xml, _exported = _write(loadout)
        self.assertEqual(build.number, 2)

    def test_a_planned_build_is_not_kept_until_recorded(self):
        # An export that fails part way must not use up a number.
        loadout = _loadout()
        exported = generator.for_mod(loadout, "")
        builds.plan(loadout, "", exported, generator.build_modops_xml(exported))
        self.assertEqual(builds.number(loadout), 0)

    def test_files_the_mod_ships_count_by_their_contents(self):
        loadout = _loadout()
        with tempfile.TemporaryDirectory() as folder:
            texts = Path(folder) / "data" / "config" / "gui" / "texts_english.xml"
            texts.parent.mkdir(parents=True)
            texts.write_text("<Texts>one</Texts>", encoding="utf-8")

            def build_now():
                exported = generator.for_mod(loadout, "")
                planned = builds.plan(
                    loadout, "", exported, generator.build_modops_xml(exported), carry_from=folder
                )
                builds.record(loadout, planned)
                return planned

            self.assertEqual(build_now().number, 1)
            self.assertFalse(build_now().new)
            texts.write_text("<Texts>two</Texts>", encoding="utf-8")
            self.assertEqual(build_now().number, 2)

    def test_a_new_studio_writing_the_same_mod_is_the_same_build(self):
        # The modinfo names the Studio that wrote it, and an update of the
        # Studio alone is not a new build of every mod of yours.
        loadout = _loadout()
        _build, _xml, exported = _write(loadout)
        self.assertEqual(json.loads(generator.build_modinfo(exported))[modinfo.STUDIO_KEY]["studio"],
                         __version__)
        with mock.patch.object(modinfo, "__version__", "99.0.0"):
            build, _xml, exported = _write(loadout)
            stamped = json.loads(generator.build_modinfo(exported))[modinfo.STUDIO_KEY]["studio"]
        self.assertEqual((build.number, build.new), (1, False))
        self.assertEqual(stamped, "99.0.0")


class WhoseTest(unittest.TestCase):
    def _with_their_mod(self) -> Loadout:
        loadout = _loadout()
        loadout.set_layer(ModLayer(origin=THEIRS, name="Their Mod", folder=""))
        loadout.set_layer(ModLayer(origin=PATCH, name="Their Mod patch", patches=THEIRS))
        loadout.set_change(_change("20", origin=THEIRS, guid="1010030"))
        loadout.set_change(_change("25", origin=PATCH, guid="1010031"))
        return loadout

    def test_somebody_elses_mod_has_no_build(self):
        loadout = self._with_their_mod()
        build, _xml, exported = _write(loadout, THEIRS)
        self.assertIsNone(build)
        self.assertNotIn(THEIRS, loadout.builds)
        self.assertNotIn(modinfo.STUDIO_KEY, json.loads(generator.build_modinfo(exported)))

    def test_patches_count_on_their_own(self):
        loadout = self._with_their_mod()
        _write(loadout)
        _write(loadout)
        build, _xml, _exported = _write(loadout, PATCH)
        self.assertEqual(build.number, 1)
        self.assertEqual(builds.number(loadout, ""), 1)

    def test_the_mod_you_carry_on_with_is_yours(self):
        loadout = self._with_their_mod()
        loadout.home = THEIRS
        build, _xml, _exported = _write(loadout, THEIRS)
        self.assertEqual(build.number, 1)


class WrittenTest(unittest.TestCase):
    def test_the_file_has_no_header(self):
        """The modinfo says the mod's details, its build and what it was
        built against; a comment saying them again was out of date the
        moment the file was typed in."""
        loadout = _loadout()
        loadout.own.creator, loadout.own.description = "Somebody", "Cuts faster."
        _build, xml, _exported = _write(loadout)
        self.assertEqual(xml.split("\n")[1], "<ModOps>")
        for said in ("Faster Lumber", "Somebody", "Cuts faster.", "Generated by",
                     "Built against", "ModOp(s)", "data30.rda"):
            self.assertNotIn(said, xml)

    def test_the_modinfo_says_the_build_and_what_it_was_built_against(self):
        loadout = _loadout()
        _build, _xml, exported = _write(loadout)
        info = json.loads(generator.build_modinfo(exported))
        self.assertEqual(info["Version"], "1.2")
        self.assertEqual(
            info[modinfo.STUDIO_KEY],
            {
                "build": 1,
                "builtAgainst": {
                    "game": "Anno 1800",
                    "archive": "data30.rda",
                    "assets": "sha1:" + FINGERPRINT,
                },
                "studio": __version__,
            },
        )

    def test_a_stale_block_is_replaced_whole(self):
        # One of yours read back in: its modinfo says the build it was, and
        # what goes out now says this one, not both.
        loadout = _loadout()
        loadout.set_layer(
            ModLayer(
                origin=THEIRS,
                name="Their Mod",
                declared={
                    "ModID": "their-mod",
                    "Version": "1.2",
                    modinfo.STUDIO_KEY: {"build": 4, "builtAgainst": {"game": "old"}},
                },
            )
        )
        loadout.set_change(_change("20", origin=THEIRS, guid="1010030"))
        loadout.home = THEIRS
        build, _xml, exported = _write(loadout, THEIRS)
        # Carried on from the number in its modinfo, not started again at one.
        self.assertEqual(build.number, 5)
        block = json.loads(generator.build_modinfo(exported))[modinfo.STUDIO_KEY]
        self.assertEqual(block["build"], 5)
        self.assertEqual(block["builtAgainst"]["game"], "Anno 1800")

    def test_the_block_it_was_read_back_with_is_not_what_it_is(self):
        # One of yours read back in, whose modinfo then says another build
        # by another Studio, as taking its folder up again does
        # (stale._follow): nothing the game sees moved, so neither does the
        # build.  The declared block went into the digest (A11-33, A11-42).
        loadout = _loadout()
        loadout.set_layer(ModLayer(
            origin=THEIRS, name="Their Mod",
            declared={"ModID": "their-mod", "Version": "1.2",
                      modinfo.STUDIO_KEY: {"build": 4, "studio": "0.1.0"}},
        ))
        loadout.home = THEIRS
        build, _xml, _exported = _write(loadout, THEIRS)
        self.assertEqual(build.number, 5)
        loadout.layer(THEIRS).declared[modinfo.STUDIO_KEY] = {
            "build": 7, "studio": "0.2.0", "builtAgainst": {"game": "Anno 1800"},
        }
        build, _xml, exported = _write(loadout, THEIRS)
        self.assertEqual((build.number, build.new), (5, False))
        self.assertEqual(json.loads(generator.build_modinfo(exported))[modinfo.STUDIO_KEY]["build"], 5)

    def test_one_read_back_in_is_the_build_its_modinfo_says(self):
        """Before its first export here, the Export tab said "no builds yet"
        over a mod whose modinfo said build 1; the export wrote 2 (H2-14)."""
        loadout = _loadout()
        loadout.set_layer(ModLayer(
            origin=THEIRS, name="Their Mod",
            declared={"ModID": "their-mod", modinfo.STUDIO_KEY: {"build": 1}},
        ))
        loadout.set_change(_change("20", origin=THEIRS, guid="1010030"))
        loadout.home = THEIRS
        self.assertEqual(builds.number(loadout, THEIRS), 1)
        build, _xml, _exported = _write(loadout, THEIRS)
        self.assertEqual(build.number, 2)
        self.assertEqual(builds.number(loadout, THEIRS), 2)
        # Somebody else's mod with a block in its modinfo is no build of yours,
        # and the loadout's own mod has no modinfo to read one from.
        loadout.home = ""
        self.assertFalse(builds.is_numbered(loadout, THEIRS))
        self.assertEqual(builds.number(loadout, ""), 0)

    def test_a_mod_of_yours_beside_others_is_numbered_too(self):
        """R3-25, hands-on run 3: built from a loadout of several mods, a mod
        of yours kept in a project went into the library with no number and
        no Studio block, so every later build wrote it again."""
        from anno_modding_studio.core import projects

        loadout = _loadout()
        mine = "[Gameplay] Run Three Stout"
        loadout.set_layer(ModLayer(origin=mine, name="Run Three Stout", mod_id="run-three-stout"))
        loadout.set_change(_change("20", origin=mine))
        self.assertFalse(builds.is_numbered(loadout, mine))
        loadout.projects[mine] = projects.Ref("projects/Run Three Stout.annoproj")
        self.assertTrue(builds.is_numbered(loadout, mine))
        build, _xml, exported = _write(loadout, mine)
        self.assertEqual(build.number, 1)
        self.assertEqual(json.loads(generator.build_modinfo(exported))[modinfo.STUDIO_KEY]["build"], 1)

    def test_a_patch_with_only_a_typed_op_is_built_by_play(self):
        """R3-24, hands-on run 3: a patch whose only work of its own was a
        typed group was never built, and its ops never reached the game.
        An op typed over somebody else's mod builds nothing of theirs."""
        from anno_modding_studio.core import build
        from anno_modding_studio.core.changes import Op

        loadout = _loadout()
        loadout.set_layer(ModLayer(origin=THEIRS, name="Their Mod", mod_id="their-mod", order=1))
        loadout.set_layer(ModLayer(origin=PATCH, name="Their Mod patch", patches=THEIRS, order=2))
        self.assertNotIn(PATCH, build.mine(loadout))
        loadout.set_op(Op(source='<ModOp Type="merge" GUID="1" Path="/Values"><X>1</X></ModOp>',
                          origin=PATCH, authored=True, uid="typed"))
        loadout.set_op(Op(source='<ModOp Type="merge" GUID="2" Path="/Values"><X>2</X></ModOp>',
                          origin=THEIRS, authored=True, uid="over-theirs"))
        self.assertIn(PATCH, build.mine(loadout))
        self.assertNotIn(THEIRS, build.mine(loadout))

    def test_a_patch_keeps_saying_what_it_patches(self):
        loadout = _loadout()
        loadout.set_layer(ModLayer(origin=THEIRS, name="Their Mod", mod_id="their-mod"))
        loadout.set_layer(ModLayer(origin=PATCH, name="Their Mod patch", patches=THEIRS))
        loadout.set_change(_change("25", origin=PATCH, guid="1010031"))
        _build, _xml, exported = _write(loadout, PATCH)
        block = json.loads(generator.build_modinfo(exported))[modinfo.STUDIO_KEY]
        self.assertEqual(block["patches"], "their-mod")
        self.assertEqual(block["build"], 1)

    def test_nothing_with_a_path_is_published(self):
        loadout = _loadout()
        loadout.source_path = r"C:\Users\somebody\extracted\assets.xml"
        _build, _xml, exported = _write(loadout)
        self.assertNotIn("somebody", generator.build_modinfo(exported))


class SavedTest(unittest.TestCase):
    def test_builds_and_the_fingerprint_survive_a_save(self):
        loadout = _loadout()
        _write(loadout)
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "p.annoload"
            loadout.save(path)
            loaded = Loadout.load(path)
        self.assertEqual(loaded.base_fingerprint, FINGERPRINT)
        self.assertEqual(loaded.builds, loadout.builds)
        build, _xml, _exported = _write(loaded)
        self.assertFalse(build.new)

    def test_a_damaged_record_costs_only_the_count(self):
        loadout = _loadout()
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "p.annoload"
            loadout.save(path)
            data = json.loads(path.read_text(encoding="utf-8"))
            data["builds"] = {"": "seven", "x": {"build": "many"}, "y": {"build": 3}}
            path.write_text(json.dumps(data), encoding="utf-8")
            loaded = Loadout.load(path)
        self.assertEqual(loaded.builds, {"y": {"build": 3, "digest": "", "assets": "", "update": ""}})

    def test_a_loadout_from_before_builds_has_none(self):
        loadout = _loadout()
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "p.annoload"
            loadout.save(path)
            data = json.loads(path.read_text(encoding="utf-8"))
            del data["builds"], data["base_fingerprint"]
            path.write_text(json.dumps(data), encoding="utf-8")
            loaded = Loadout.load(path)
        self.assertEqual((loaded.builds, loaded.base_fingerprint), ({}, ""))


class GameDataTest(unittest.TestCase):
    def test_a_build_remembers_what_it_was_built_on(self):
        loadout = _loadout()
        loadout.base_update = "18.4"
        _write(loadout)
        held = loadout.builds[""]
        self.assertEqual((held["assets"], held["update"]), (FINGERPRINT, "18.4"))
        self.assertIsNone(builds.stale(loadout))

    def test_other_data_since_makes_the_last_build_stale(self):
        loadout = _loadout()
        _write(loadout)
        loadout.base_fingerprint = "f" * 40
        self.assertEqual(builds.stale(loadout)["build"], 1)
        # Built again on the new data, it is current again.
        _write(loadout)
        self.assertIsNone(builds.stale(loadout))

    def test_nothing_to_compare_is_not_stale(self):
        loadout = _loadout()
        self.assertIsNone(builds.stale(loadout))
        loadout.base_fingerprint = ""
        _write(loadout)
        self.assertIsNone(builds.stale(loadout))

    def test_the_update_goes_into_the_modinfo(self):
        loadout = _loadout()
        loadout.base_update, loadout.base_game_version = "18.4", "18.4.1412158.165273"
        _build, _xml, exported = _write(loadout)
        against = json.loads(generator.build_modinfo(exported))[modinfo.STUDIO_KEY]["builtAgainst"]
        self.assertEqual((against["update"], against["gameVersion"]), ("18.4", "18.4.1412158.165273"))

    def test_whether_a_mod_was_built_on_other_data(self):
        def declared(game="Anno 1800", assets="sha1:" + FINGERPRINT):
            return {modinfo.STUDIO_KEY: {"build": 2, "builtAgainst": {"game": game, "assets": assets}}}

        other = builds.built_on_other_data
        self.assertFalse(other(declared(), games.ANNO_1800, FINGERPRINT))
        self.assertTrue(other(declared(assets="sha1:" + "0" * 40), games.ANNO_1800, FINGERPRINT))
        self.assertIsNone(other(declared(game="Anno 117"), games.ANNO_1800, FINGERPRINT))
        self.assertIsNone(other({}, games.ANNO_1800, FINGERPRINT))
        self.assertIsNone(other(declared(), games.ANNO_1800, ""))


class DeclaredTest(unittest.TestCase):
    def test_reads_the_build_or_nothing(self):
        self.assertEqual(builds.declared_build({modinfo.STUDIO_KEY: {"build": 9}}), 9)
        self.assertEqual(builds.declared_build({modinfo.STUDIO_KEY: {"build": "x"}}), 0)
        self.assertEqual(builds.declared_build({modinfo.STUDIO_KEY: "nonsense"}), 0)
        self.assertEqual(builds.declared_build(None), 0)


class FragmentTest(unittest.TestCase):
    """A few rows of a mod, written by what writes the whole of it."""

    SEARCH = (
        '<ModOp Type="merge" Path="//Values/Factory7"><CycleTime>5</CycleTime></ModOp>'
    )

    def _stack(self) -> Loadout:
        from anno_modding_studio.core.changes import Op, Unread

        loadout = _loadout()
        loadout.set_layer(ModLayer(origin=THEIRS, name="Their Mod"))
        loadout.set_change(_change("20", THEIRS))
        loadout.set_change(_change("25", THEIRS, guid="1010341"))
        loadout.ops.append(
            Op(expression="//Values/Factory7", source=self.SEARCH, origin=THEIRS,
                 uid="search", order=7)
        )
        answer = _change("5", THEIRS, guid="1010342")
        answer.op_uid = "search"
        answer.order = 7
        loadout.set_change(answer)
        loadout.unread.append(
            Unread(origin=THEIRS, source='<ModOp Type="odd" GUID="9"/>', why="odd", order=9)
        )
        return loadout

    def _key(self, loadout: Loadout, guid: str):
        return next(c.key for c in loadout.changes_of(THEIRS) if c.guid == guid)

    def test_only_what_was_asked_for_is_written(self):
        from anno_modding_studio.core import build

        loadout = self._stack()
        xml = build.fragment_xml(loadout, THEIRS, keys=[self._key(loadout, "1010340")])
        self.assertIn(">20<", xml)
        self.assertNotIn(">25<", xml)
        self.assertNotIn("//Values/Factory7", xml)
        self.assertNotIn('Type="odd"', xml)

    def test_one_of_an_ops_answers_is_written_as_the_op(self):
        from anno_modding_studio.core import build

        loadout = self._stack()
        xml = build.fragment_xml(loadout, THEIRS, keys=[self._key(loadout, "1010342")])
        self.assertIn('Path="//Values/Factory7"', xml)
        self.assertNotIn("1010342", xml, "its answer is not written beside it")

    def test_an_unread_op_is_asked_for_by_its_place(self):
        from anno_modding_studio.core import build

        xml = build.fragment_xml(self._stack(), THEIRS, unread=[9])
        self.assertIn('Type="odd"', xml)
        self.assertNotIn(">20<", xml)

    def test_a_switched_off_row_is_the_comment_it_is_written_as(self):
        from anno_modding_studio.core import build
        from anno_modding_studio.core.changes import SWITCHED_OFF_MARK

        loadout = self._stack()
        key = self._key(loadout, "1010340")
        loadout.changes[key].active = False
        # An op goes off with its answers.
        loadout.ops[0].active = False
        for change in loadout.changes_of(THEIRS):
            if change.op_uid == "search":
                change.active = False
        xml = build.fragment_xml(loadout, THEIRS, keys=[key], ops=["search"])
        self.assertEqual(xml.count(SWITCHED_OFF_MARK), 2)
        self.assertEqual(_live(xml), [])
        self.assertEqual(
            sorted(op.get("Path") for op in _shelved(xml)),
            ["//Values/Factory7", "/Values/Factory7/CycleTime"],
        )


def _live(xml: str) -> list:
    """The ops of a document that the game reads."""
    from lxml import etree

    return [op for op in etree.fromstring(xml.encode("utf-8")) if isinstance(op.tag, str)]


def _shelved(xml: str) -> list:
    """The ops of a document that are written switched off, uncommented."""
    from lxml import etree

    from anno_modding_studio.core.changes import SWITCHED_OFF_MARK

    found = []
    for node in etree.fromstring(xml.encode("utf-8")):
        text = (node.text or "").strip() if isinstance(node, etree._Comment) else ""
        if text.startswith(SWITCHED_OFF_MARK):
            held = switched_on(text[len(SWITCHED_OFF_MARK) + 1:])
            found.append(etree.fromstring(held.encode("utf-8")))
    return found


class SwitchedOffTest(unittest.TestCase):
    """What is switched off, written as comments."""

    def _stack(self) -> Loadout:
        return FragmentTest()._stack()

    def _written(self, loadout) -> str:
        return generator.build_modops_xml(generator.for_mod(loadout, THEIRS))

    def _off(self, loadout, guid="1010340"):
        change = next(c for c in loadout.changes_of(THEIRS) if c.guid == guid)
        change.active = False
        return change

    def test_it_is_not_an_option(self):
        """Left out, a switched-off op was lost from the folder the mod is
        read back from; so every export writes it."""
        from anno_modding_studio.core import build

        loadout = self._stack()
        self._off(loadout)
        xml = build.modops_xml(generator.for_mod(loadout, THEIRS))
        self.assertEqual(len(_shelved(xml)), 1)

    def test_a_change_goes_out_as_the_op_it_would_be_in_a_comment(self):
        loadout = self._stack()
        self._off(loadout)
        xml = self._written(loadout)
        self.assertNotIn(">20<", [op.findtext("CycleTime") for op in _live(xml)])
        ops = _shelved(xml)
        self.assertEqual(len(ops), 1)
        self.assertEqual(ops[0].get("GUID"), "1010340")
        self.assertEqual(ops[0].findtext("CycleTime"), "20")

    def test_what_the_game_reads_is_the_file_without_it(self):
        from lxml import etree

        loadout = self._stack()
        change = self._off(loadout)
        with_it = self._written(loadout)
        loadout.remove_key(change.key)
        without = self._written(loadout)
        self.assertEqual(
            [etree.tostring(op, with_tail=False) for op in _live(with_it)],
            [etree.tostring(op, with_tail=False) for op in _live(without)],
        )
        self.assertEqual(len(_shelved(with_it)), 1)
        self.assertEqual(_shelved(without), [])

    def test_an_op_goes_out_as_itself_and_its_answers_do_not(self):
        loadout = self._stack()
        loadout.ops[0].active = False
        self._off(loadout, "1010342")
        xml = self._written(loadout)
        ops = _shelved(xml)
        self.assertEqual([op.get("Path") for op in ops], ["//Values/Factory7"])
        self.assertNotIn("1010342", xml)

    def test_one_of_an_op_that_is_on_is_still_the_ops(self):
        loadout = self._stack()
        self._off(loadout, "1010342")
        self.assertEqual(_shelved(self._written(loadout)), [])

    def test_a_mod_that_is_not_loading_is_not_written_line_by_line(self):
        loadout = self._stack()
        self._off(loadout)
        loadout.not_loading = lambda: {THEIRS}
        self.assertEqual(_shelved(generator.build_modops_xml(loadout)), [])

    def test_a_mod_with_everything_off_is_still_written(self):
        from anno_modding_studio.core import build

        loadout = self._stack()
        loadout.unread.clear()
        loadout.ops.clear()
        for change in loadout.changes_of(THEIRS):
            change.active = False
        view = generator.for_mod(loadout, THEIRS)
        self.assertTrue(generator.writes_anything(view))
        self.assertTrue(generator.writes_only_comments(view))
        self.assertFalse(generator.writes_for_the_game(view))
        self.assertIn(THEIRS, build.writable(loadout))
        self.assertEqual(_live(build.modops_xml(view)), [])

    def test_a_comment_in_the_op_is_spelt_as_none_and_so_are_two_dashes(self):
        from lxml import etree

        from anno_modding_studio.core.changes import OP_ADD

        loadout = self._stack()
        added = Change(
            guid="1010340", asset_label="Lumberjack's Hut", path="/Values/Text",
            tag="Text", op=OP_ADD, origin=THEIRS, active=False,
            payload="<Text>\n  <!-- theirs -->\n  <Said>a -- b --- c</Said><!-- too -->\n</Text>",
        )
        loadout.set_change(added)
        xml = self._written(loadout)
        # Neither can stand inside a comment as it is, and the document still
        # has to be one.
        self.assertIn("<!&#45;&#45; theirs &#45;&#45;>", xml)
        self.assertIn("<Said>a -&#45; b -&#45;&#45; c</Said><!&#45;&#45; too &#45;&#45;>", xml)
        ops = _shelved(xml)
        self.assertEqual(ops[0].findtext("Text/Said"), "a -- b --- c")
        self.assertEqual(
            [node.text for node in ops[0].iter() if isinstance(node, etree._Comment)],
            [" theirs ", " too "],
        )

    def test_its_note_stays_above_it(self):
        loadout = self._stack()
        self._off(loadout).note = "why it is off"
        lines = self._written(loadout).splitlines()
        at = next(n for n, line in enumerate(lines) if "switched off in" in line and "<!--" in line)
        self.assertIn("note: why it is off", lines[at - 1])
        self.assertNotIn("CycleTime: 30 -> 20", "\n".join(lines))
        self.assertEqual(lines[at + 4].strip(), "-->")


if __name__ == "__main__":
    unittest.main()
