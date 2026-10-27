"""The xmltest wrapper: options written in, diffs and logs read back.

Most of this is about text and runs anywhere.  The class at the bottom runs the
real loader build when the VS Code extension that ships it is installed, and
skips otherwise.
"""

from __future__ import annotations

import shutil
import sys
import tempfile
import unittest
from pathlib import Path

import _isolation  # noqa: F401 -- first: keeps the suite out of real files under any runner

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

import modio_loader  # noqa: E402
from lxml import etree  # noqa: E402
from support import IMPORT_ASSETS, LOADER, missing, requires  # noqa: E402

from anno_modding_studio.core import games  # noqa: E402
from anno_modding_studio.core.loadercheck import canonical  # noqa: E402

MODINFO = """{"ModID": "m", "Options": {
  "speed": {"type": "slider", "default": "2.5", "values": ["1", "5", "0.5"]},
  "start": {"type": "toggle", "default": "true"},
  "late": {"type": "toggle", "default": "false"},
  "mode": {"type": "enum", "default": "hard", "values": ["easy", "hard"]}
}}"""


VALUES = {"speed": "2.5", "start": "true", "late": "false", "mode": "hard", "count": "3"}


class TestWritingOptionsIn(unittest.TestCase):
    def written(self, text):
        return modio_loader.substitute(text, VALUES)

    def test_a_value_in_an_expression_goes_in_as_a_literal(self):
        text, count, unknown = self.written('<ModValue Insert="self::node() * $speed + $count"/>')
        self.assertEqual(text, '<ModValue Insert="self::node() * 2.5 + 3"/>')
        self.assertEqual((count, unknown), (2, []))

    def test_a_condition_that_is_only_an_option_asks_whether_it_is_on(self):
        self.assertEqual(self.written('<Group Condition="$start">')[0], '<Group Condition="true()">')
        self.assertEqual(self.written('<Group Condition="!$start">')[0], '<Group Condition="false()">')
        self.assertEqual(self.written('<ModOp condition="$late">')[0], '<ModOp condition="false()">')

    def test_a_condition_comparing_an_option_keeps_the_comparison(self):
        self.assertEqual(
            self.written('<ModOp Condition="$count=1 or $count=3">')[0],
            '<ModOp Condition="3=1 or 3=3">',
        )

    def test_a_word_is_quoted_and_an_option_named_with_its_mod_still_found(self):
        self.assertEqual(self.written("<X Insert=\"$m.mode\"/>")[0], "<X Insert=\"'hard'\"/>")
        self.assertEqual(self.written("<X Insert='$mode'/>")[0], "<X Insert='\"hard\"'/>")

    def test_an_option_the_mod_does_not_declare_is_left_for_the_loader(self):
        text, count, unknown = self.written('<X Insert="$other"/>')
        self.assertEqual((text, count, unknown), ('<X Insert="$other"/>', 0, ["other"]))

    def test_text_outside_attributes_is_not_touched(self):
        text = "<Text>costs $5</Text>"
        self.assertEqual(self.written(text)[0], text)


class TestCopyingAStackOfMods(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root, True)

    def test_each_mod_takes_its_own_options_and_only_patch_files_come_along(self):
        host = self.root / "src/Host"
        inner = host / "Inner"
        for folder, speed in ((host, "2"), (inner, "7")):
            (folder / "data").mkdir(parents=True)
            (folder / "modinfo.json").write_text(
                '{"Options": {"speed": {"default": "%s"}}}' % speed, encoding="utf-8"
            )
            (folder / "data/assets.xml").write_text(
                '<ModOps><ModValue Insert="$speed"/></ModOps>', encoding="utf-8"
            )
        (host / "data/icon.dds").write_bytes(b"DDS ")

        count, unknown = modio_loader.copy_with_options(self.root / "src", self.root / "dst")

        self.assertEqual((count, unknown), (2, []))
        dst = self.root / "dst/Host"
        self.assertIn('Insert="2"', (dst / "data/assets.xml").read_text(encoding="utf-8"))
        self.assertIn('Insert="7"', (dst / "Inner/data/assets.xml").read_text(encoding="utf-8"))
        self.assertFalse((dst / "data/icon.dds").exists())

    def test_option_values_are_the_modinfo_defaults(self):
        (self.root / "modinfo.json").write_text(MODINFO, encoding="utf-8")
        self.assertEqual(
            modio_loader.option_values(self.root),
            {"speed": "2.5", "start": "true", "late": "false", "mode": "hard"},
        )


class TestReadingWhatItPrints(unittest.TestCase):
    OUTPUT = (
        "[2026-09-12 18:24:53.706] [warning] xmltest: Nothing matches \"false()\" (data/a.xml:4)\n"
        "[2026-09-12 18:24:53.707] [error] xmltest: Unknown ModOp  (data/a.xml:12)\n"
        "ModOp time: 0.001s\n"
        "##annodiff##<Assets>\n"
        "<Asset><Values><Standard><GUID>1</GUID></Standard><X>2</X></Values></Asset>\n"
        "<Asset><Values><Standard><GUID>9</GUID></Standard></Values></Asset>\n"
        "</Assets>\n"
        "##annodiff##<Assets>\n"
        "<Asset><Values><Standard><GUID>1</GUID></Standard><X>1</X></Values></Asset>\n"
        "</Assets>\n"
    )

    def test_the_first_list_is_after_and_the_second_before(self):
        after, before = modio_loader.parse_diff(self.OUTPUT)
        self.assertEqual(sorted(after), ["1", "9"])
        self.assertEqual(after["1"].findtext("Values/X"), "2")
        self.assertEqual(before["1"].findtext("Values/X"), "1")
        self.assertNotIn("9", before)

    def test_warnings_and_errors_are_counted_by_kind(self):
        log = modio_loader.parse_log(self.OUTPUT)
        self.assertEqual(
            dict(log),
            {"warning: Nothing matches '...'": 1, "error: Unknown ModOp": 1},
        )

    def test_output_without_a_diff_is_an_error(self):
        with self.assertRaises(modio_loader.LoaderError):
            modio_loader.parse_diff("xmltest crashed\n")

    def test_the_newest_extension_wins(self):
        with tempfile.TemporaryDirectory() as tmp:
            for version in ("2.3.1", "2.10.0", "2.9.9"):
                exe = Path(tmp) / ("jakobharder.anno-modding-tools-" + version) / "external/xmltest.exe"
                exe.parent.mkdir(parents=True)
                exe.write_bytes(b"")
            found = modio_loader.find_xmltest(games.ANNO_1800, Path(tmp))
        self.assertIn("2.10.0", str(found))


XMLTEST = modio_loader.find_xmltest(games.ANNO_1800)


def _on_the_probe(kind, tag, inside, path=""):
    """An op on one field of the asset ``TestTheRealLoader.PROBE`` adds, as
    the probe mod run in the game wrote its ops."""
    return '<ModOp Type="%s" GUID="5000" Path="/Values/MVP%s"><%s>%s</%s></ModOp>' % (
        kind, path, tag, inside, tag
    )


@requires(XMLTEST, LOADER)
class TestTheRealLoader(unittest.TestCase):
    """Anno 1800's build against a small assets.xml, so what it does can be seen."""

    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root, True)
        self.target = self.root / "assets.xml"
        self.target.write_text(IMPORT_ASSETS, encoding="utf-8")

    def mod(self, name, body, modinfo="", game=games.ANNO_1800):
        folder = self.root / "mods" / name
        patch = folder / games.get(game).asset_relpath
        patch.parent.mkdir(parents=True)
        patch.write_text("<ModOps>%s</ModOps>" % body, encoding="utf-8")
        if modinfo:
            (folder / "modinfo.json").write_text(modinfo, encoding="utf-8")
        return folder

    def apply(self, *folders, game=games.ANNO_1800):
        exe = XMLTEST if game == games.ANNO_1800 else modio_loader.find_xmltest(game)
        if exe is None:
            missing("%s for %s" % (LOADER, game))
        return modio_loader.apply_stack(exe, self.target, folders, self.root / "mods", game)

    def test_a_stack_ends_with_each_asset_as_the_last_mod_left_it(self):
        first = self.mod(
            "a",
            '<ModOp Type="replace" GUID="1000" Path="/Values/Building/Radius"><Radius>12</Radius></ModOp>'
            '<ModOp Type="replace" GUID="1001" Path="/Values/Building/Radius"><Radius>5</Radius></ModOp>',
        )
        second = self.mod(
            "b", '<ModOp Type="replace" GUID="1000" Path="/Values/Building/Radius"><Radius>30</Radius></ModOp>'
        )
        applied = self.apply(first, second)

        self.assertEqual(applied.failures, [])
        self.assertEqual(applied.runs, 2)
        self.assertEqual(applied.assets["1000"].findtext("Values/Building/Radius"), "30")
        self.assertEqual(applied.assets["1001"].findtext("Values/Building/Radius"), "5")

    def test_a_later_mod_patches_what_an_earlier_one_left(self):
        """For both games.  ``-p`` reads an earlier mod's patch at Anno 1800's
        path only, so an Anno 117 stack was each mod on its own until the copy
        was given a stub there: the second mod here matched nothing."""
        for game in (games.ANNO_1800, games.ANNO_117):
            with self.subTest(game=game):
                first = self.mod(
                    "a " + game,
                    '<ModOp Type="add" GUID="1000" Path="/Values/Building"><Extra><N>1</N></Extra></ModOp>',
                    game=game,
                )
                second = self.mod(
                    "b " + game,
                    '<ModOp Type="replace" GUID="1000" Path="/Values/Building/Extra/N"><N>2</N></ModOp>',
                    game=game,
                )
                applied = self.apply(first, second, game=game)

                self.assertEqual(applied.failures, [])
                self.assertEqual(applied.assets["1000"].findtext("Values/Building/Extra/N"), "2")
                self.assertEqual(applied.log, {})
                # And only the copy: the mod as shipped is not touched.
                self.assertFalse((first / modio_loader.PREPATCH_RELPATH).exists() and game == games.ANNO_117)

    def test_an_include_rooted_with_a_slash_is_read_from_the_top_of_the_mod(self):
        """What the Studio's reader has to agree with: measured, not assumed."""
        folder = self.mod("rooted", '<Include File="/parts/one.xml" />')
        (folder / "parts").mkdir()
        (folder / "parts" / "one.xml").write_text(
            '<ModOps><ModOp Type="replace" GUID="1000" Path="/Values/Building/Radius">'
            "<Radius>77</Radius></ModOp></ModOps>",
            encoding="utf-8",
        )
        applied = self.apply(folder)

        self.assertEqual(applied.failures, [])
        self.assertEqual(applied.assets["1000"].findtext("Values/Building/Radius"), "77")

    def test_an_include_with_a_condition_is_only_included_when_it_holds(self):
        """And what the Studio carries in its place -- a group with the same
        condition and the file's ops written into it -- does the same."""
        op = (
            '<ModOp Type="replace" GUID="%s" Path="/Values/Building/Radius">'
            "<Radius>77</Radius></ModOp>"
        )
        folder = self.mod(
            "conditional",
            '<Include File="/parts/yes.xml" Condition="@1000/Building" />'
            '<Include File="/parts/no.xml" Condition="@1000/Nothing" />',
        )
        (folder / "parts").mkdir()
        (folder / "parts" / "yes.xml").write_text("<ModOps>%s</ModOps>" % (op % "1000"), encoding="utf-8")
        (folder / "parts" / "no.xml").write_text("<ModOps>%s</ModOps>" % (op % "1001"), encoding="utf-8")
        grouped = self.mod(
            "grouped",
            '<Group Condition="@1000/Building">%s</Group><Group Condition="@1000/Nothing">%s</Group>'
            % (op % "1000", op % "1001"),
        )
        for which in (folder, grouped):
            with self.subTest(mod=which.name):
                applied = self.apply(which)
                self.assertEqual(applied.failures, [])
                self.assertEqual(applied.assets["1000"].findtext("Values/Building/Radius"), "77")
                self.assertNotIn("1001", applied.assets)

    def test_a_merge_of_same_named_entries_goes_by_position(self):
        """For both games, and not what anybody would guess: an entry that names
        the second ingredient still writes over the first.  The Studio's reading
        of a merge is built on this, so it is measured here rather than taken
        from anywhere."""
        body = (
            '<ModOp Type="merge" GUID="1000" Path="/Values/ProductList/List">'
            "<Item><Product>2002</Product></Item></ModOp>"
            '<ModOp Type="merge" GUID="1000" Path="/Values/Cost/Costs">'
            "<Item><Ingredient>1011</Ingredient><Amount>80</Amount></Item></ModOp>"
        )
        for game in (games.ANNO_1800, games.ANNO_117):
            with self.subTest(game=game):
                applied = self.apply(self.mod("merge " + game, body, game=game), game=game)
                asset = applied.assets["1000"]
                self.assertEqual(
                    [node.text for node in asset.iterfind("Values/ProductList/List/Item/Product")],
                    ["2002", "2002", "2003"],
                )
                self.assertEqual(
                    [(item.findtext("Ingredient"), item.findtext("Amount"))
                     for item in asset.find("Values/Cost/Costs")],
                    [("1011", "80"), ("1011", "8")],
                )

    def test_an_option_is_applied_at_its_default(self):
        # <ModValue> is Anno 117's: the 1800 build leaves the value where it was
        # whatever Insert says, so this is asked of the 117 build.
        folder = self.mod(
            "opt",
            '<ModOp Type="merge" GUID="1000" Path="/Values/Building/Radius">'
            '<Radius><ModValue Insert="$radius"/></Radius></ModOp>',
            modinfo='{"Options": {"radius": {"type": "slider", "default": "17"}}}',
            game=games.ANNO_117,
        )
        applied = self.apply(folder, game=games.ANNO_117)
        self.assertEqual(applied.options_written, 1)
        self.assertEqual(applied.assets["1000"].findtext("Values/Building/Radius"), "17")

    def test_a_group_carrying_a_name_is_nothing_to_the_loader(self):
        """``ModOpID`` and ``Name`` are for a tweak tool to find the group by.
        The ops inside run exactly as they would bare -- which is why the
        Studio reads them, and why the group still has to go back out carrying
        the name."""
        op = '<ModOp Type="replace" GUID="1000" Path="/Values/Building/Radius"><Radius>77</Radius></ModOp>'
        for game in (games.ANNO_1800, games.ANNO_117):
            for attribute in ('ModOpID="x"', 'Name="x"'):
                with self.subTest(game=game, attribute=attribute):
                    applied = self.apply(
                        self.mod("label %s %s" % (game, attribute[:4]),
                                 "<Group %s>%s</Group>" % (attribute, op), game=game),
                        game=game,
                    )
                    self.assertEqual(applied.failures, [])
                    self.assertEqual(applied.assets["1000"].findtext("Values/Building/Radius"), "77")

    def test_any_skip_at_all_switches_a_group_off(self):
        """Not only ``Skip="1"``: ``Skip="0"`` and an empty one skip it too, in
        both games.  The attribute being there is the whole of it."""
        op = '<ModOp Type="replace" GUID="1000" Path="/Values/Building/Radius"><Radius>77</Radius></ModOp>'
        for game in (games.ANNO_1800, games.ANNO_117):
            for value in ("1", "0", "false", ""):
                with self.subTest(game=game, skip=value):
                    applied = self.apply(
                        self.mod("skip %s %s" % (game, value or "empty"),
                                 '<Group Skip="%s">%s</Group>' % (value, op), game=game),
                        game=game,
                    )
                    self.assertEqual(applied.failures, [])
                    self.assertNotIn("1000", applied.assets)

    def test_any_skip_at_all_switches_an_op_off(self):
        """As for a group: both loaders ask whether ``Skip`` is there before
        they ask what the node is."""
        for game in (games.ANNO_1800, games.ANNO_117):
            for value in ("1", "0", "false", ""):
                with self.subTest(game=game, skip=value):
                    op = ('<ModOp Type="replace" GUID="1000" Path="/Values/Building/Radius" '
                          'Skip="%s"><Radius>77</Radius></ModOp>' % value)
                    applied = self.apply(
                        self.mod("op skip %s %s" % (game, value or "empty"), op, game=game),
                        game=game,
                    )
                    self.assertEqual(applied.failures, [])
                    self.assertNotIn("1000", applied.assets)

    def test_each_game_runs_only_the_types_its_loader_lists(self):
        """``ReadType`` in each loader: no short form in Anno 1800, no
        ``Type="append"`` in either, no ``AddNextSibling=`` attribute in
        either, and ``addAfter`` only in Anno 117.  What a game does not list
        is an "Unknown ModOp" and changes nothing."""
        radius = "<Radius>77</Radius>"
        cases = (
            (games.ANNO_1800, '<ModOp Merge="@1000/Building">%s</ModOp>' % radius, False),
            (games.ANNO_117, '<ModOp AddNextSibling="@1000/Building/Radius">%s</ModOp>' % radius, False),
            (games.ANNO_117, '<ModOp Type="append" GUID="1000" Path="/Values/Building/Radius">'
                             '%s</ModOp>' % radius, False),
            (games.ANNO_1800, '<ModOp Type="append" GUID="1000" Path="/Values/Building/Radius">'
                              '%s</ModOp>' % radius, False),
            (games.ANNO_1800, '<ModOp Type="addAfter" GUID="1000" Path="/Values/Building/Radius">'
                              '%s</ModOp>' % radius, False),
            (games.ANNO_117, '<ModOp Type="addAfter" GUID="1000" Path="/Values/Building/Radius">'
                             '%s</ModOp>' % radius, True),
        )
        for number, (game, op, runs) in enumerate(cases):
            with self.subTest(game=game, op=op):
                applied = self.apply(self.mod("types %d" % number, op, game=game), game=game)
                self.assertEqual("1000" in applied.assets, runs)

    def test_max_repeat_runs_everything_in_the_group_that_many_times(self):
        """Anno 117 only -- the 1800 build runs it once whatever it says.  It
        does not stop when an op inside stops matching, so it is a count and
        not a "while there is anything left".  The Studio carries such a group
        rather than reading it."""
        add = ('<ModOp Type="add" GUID="1000" Path="/Values/ProductList/List">'
               "<Item><Product>2099</Product></Item></ModOp>")

        def added(applied):
            return [node.text for node in
                    applied.assets["1000"].iterfind("Values/ProductList/List/Item/Product")]

        plain = self.apply(self.mod("repeat once", add, game=games.ANNO_117), game=games.ANNO_117)
        self.assertEqual(added(plain).count("2099"), 1)
        for game, times in ((games.ANNO_117, 3), (games.ANNO_1800, 1)):
            with self.subTest(game=game):
                applied = self.apply(
                    self.mod("repeat %s" % game, '<Group MaxRepeat="3">%s</Group>' % add, game=game),
                    game=game,
                )
                self.assertEqual(added(applied).count("2099"), times)

    def entries(self, applied, guid="1000", path="Values/ProductList/List/Item"):
        return ["/".join("%s=%s" % (c.tag, (c.text or "").strip()) for c in item)
                for item in applied.assets[guid].iterfind(path)]

    def merged(self, body, game=games.ANNO_117, **named):
        folder = self.mod("item %d" % len(list((self.root / "mods").iterdir()) if
                                          (self.root / "mods").is_dir() else []),
                          '<ModOp Merge="@1000/ProductList/List">%s</ModOp>' % body, game=game)
        return self.apply(folder, game=game)

    def test_a_moditem_finds_its_entry_by_the_field_it_names(self):
        """Anno 117's answer to a merge being positional: the Studio's reading
        of one rests on every line of this."""
        applied = self.merged(
            '<ModItem Merge="Product"><Product>2002</Product><Note>x</Note></ModItem>'
        )
        self.assertEqual(self.entries(applied),
                         ["Product=2001", "Product=2002/Note=x", "Product=2003"])

    def test_or_by_a_value_it_names_outright(self):
        applied = self.merged('<ModItem Merge="Product=\'2003\'"><Note>y</Note></ModItem>')
        self.assertEqual(self.entries(applied),
                         ["Product=2001", "Product=2002", "Product=2003/Note=y"])

    def test_one_it_cannot_find_is_added_at_the_end(self):
        applied = self.merged('<ModItem Merge="Product"><Product>2099</Product></ModItem>')
        self.assertEqual(self.entries(applied)[-1], "Product=2099")

    def test_append_and_prepend_say_where_a_new_one_goes(self):
        for attribute, anchor, expected in (
            ("Append", "2001", 1),
            ("Prepend", "2003", 2),
        ):
            with self.subTest(attribute=attribute):
                applied = self.merged(
                    '<ModItem Merge="Product" %s="Product=\'%s\'">'
                    "<Product>2099</Product></ModItem>" % (attribute, anchor)
                )
                self.assertEqual(self.entries(applied).index("Product=2099"), expected)

    def test_what_it_adds_is_an_item_whatever_it_is_added_to(self):
        """Even where the target's children are ordinary fields: it matches
        none of them and an <Item> goes in beside them."""
        folder = self.mod(
            "item into a node",
            '<ModOp Merge="@1000/Building"><ModItem Merge="Radius">'
            "<Radius>99</Radius></ModItem></ModOp>",
            game=games.ANNO_117,
        )
        applied = self.apply(folder, game=games.ANNO_117)
        building = applied.assets["1000"].find("Values/Building")
        self.assertEqual(building.findtext("Radius"), "10")
        self.assertEqual(building.findtext("Item/Radius"), "99")

    def test_the_two_shapes_that_leave_the_loader_in_a_muddle(self):
        """An entry found *and* told where to go keeps the Append= attribute in
        the game's data; one whose anchor is missing stays a <ModItem> there.
        Both are why the Studio carries those two shapes rather than reading
        them."""
        found = self.merged(
            '<ModItem Merge="Product" Append="Product=\'2001\'">'
            "<Product>2003</Product><Note>m</Note></ModItem>"
        )
        stayed = found.assets["1000"].find("Values/ProductList/List/Item[3]")
        self.assertEqual(stayed.get("Append"), "Product='2001'")
        missing = self.merged(
            '<ModItem Merge="Product" Append="Product=\'9999\'">'
            "<Product>2099</Product></ModItem>"
        )
        self.assertIsNotNone(
            missing.assets["1000"].find("Values/ProductList/List/ModItem")
        )

    def test_anno_1800_leaves_the_list_alone_entirely(self):
        applied = self.merged(
            '<ModItem Merge="Product"><Product>2099</Product></ModItem>', game=games.ANNO_1800
        )
        self.assertNotIn("1000", applied.assets)

    def test_a_moditem_under_an_add_is_just_an_element(self):
        folder = self.mod(
            "item under add",
            '<ModOp Type="add" GUID="1000" Path="/Values/ProductList/List">'
            '<ModItem Merge="Product"><Product>2099</Product></ModItem></ModOp>',
            game=games.ANNO_117,
        )
        applied = self.apply(folder, game=games.ANNO_117)
        self.assertIsNotNone(applied.assets["1000"].find("Values/ProductList/List/ModItem"))

    def test_property_targets_every_asset_that_has_that_property(self):
        """Anno 117 only, and the whole of what the Studio turns into a path:
        the property is a child of <Values> and nowhere deeper, the rest of
        the expression is relative to it, and an absolute // path wins."""
        pay = "<Building><Radius>99</Radius></Building>"
        for name, body, expected in (
            ("filtered", '<ModOp Property="Building" Merge="self::node()[BuildingType=\'Factory\']">%s</ModOp>' % pay,
             {"1000": "99", "1001": "4"}),
            ("every one", '<ModOp Property="Building" Merge="self::node()">%s</ModOp>' % pay,
             {"1000": "99", "1001": "99"}),
            ("relative, no self::node()", '<ModOp Property="Building" Merge="Radius"><Radius>99</Radius></ModOp>',
             {"1000": "99", "1001": "99"}),
            ("an absolute path wins", '<ModOp Property="Nonesuch" Merge="//Values/Building">%s</ModOp>' % pay,
             {"1000": "99", "1001": "99"}),
        ):
            with self.subTest(shape=name):
                applied = self.apply(
                    self.mod("prop %d" % len(name), body, game=games.ANNO_117),
                    game=games.ANNO_117,
                )
                for guid, radius in expected.items():
                    got = applied.assets.get(guid)
                    if got is None:
                        self.assertEqual(radius, "4", guid)  # untouched, so not in the diff
                    else:
                        self.assertEqual(got.findtext("Values/Building/Radius"), radius, guid)

    def test_property_reaches_no_deeper_than_a_child_of_values(self):
        """``Property="Item"`` matches none of the <Item>s inside a list, which
        is why the Studio writes it as ``//Values/Item`` and not ``//Item``."""
        applied = self.apply(
            self.mod(
                "prop deep",
                '<ModOp Property="Item" Merge="self::node()[Ingredient=\'1011\']">'
                "<Item><Amount>77</Amount></Item></ModOp>",
                game=games.ANNO_117,
            ),
            game=games.ANNO_117,
        )
        self.assertEqual(applied.assets, {})

    def test_property_does_nothing_without_something_to_aim(self):
        applied = self.apply(
            self.mod("prop bare", '<ModOp Property="Building"><Building><Radius>99</Radius></Building></ModOp>',
                     game=games.ANNO_117),
            game=games.ANNO_117,
        )
        self.assertEqual(applied.assets, {})

    def test_and_anno_1800_does_nothing_with_one_at_all(self):
        applied = self.apply(
            self.mod(
                "prop 1800",
                '<ModOp Type="merge" Property="Building" Path="self::node()">'
                "<Building><Radius>99</Radius></Building></ModOp>",
                game=games.ANNO_1800,
            ),
            game=games.ANNO_1800,
        )
        self.assertEqual(applied.assets, {})

    def test_div_and_mod_are_xpaths_names_for_two_operators(self):
        """73 ModValues in the Anno 117 census use ``div``.  The spelling of
        the answer is the loader's too: ``%.16g``, measured."""
        for expr, start, expected in (
            ("self::node() div 2", "10", "5"),
            ("self::node() div 4", "10", "2.5"),
            ("self::node() div 3", "10", "3.333333333333333"),
            ("self::node() div 1.5", "10", "6.666666666666667"),
            ("self::node() mod 3", "10", "1"),
            ("self::node() div 2 + 1", "10", "6"),
        ):
            with self.subTest(expression=expr):
                body = (
                    '<ModOp Type="replace" GUID="1000" Path="/Values/Building/Radius">'
                    "<Radius>%s</Radius></ModOp>"
                    '<ModOp Type="merge" GUID="1000" Path="/Values/Building/Radius">'
                    '<Radius><ModValue Insert="%s"/></Radius></ModOp>' % (start, expr)
                )
                applied = self.apply(
                    self.mod("div %s" % len(expr) + expr[-4:], body, game=games.ANNO_117),
                    game=games.ANNO_117,
                )
                self.assertEqual(
                    applied.assets["1000"].findtext("Values/Building/Radius"), expected
                )

    def builds_for(self, game):
        """Every build the extension ships for a game: Anno 117 has two, and
        they do not always agree."""
        exe = XMLTEST if game == games.ANNO_1800 else modio_loader.find_xmltest(game)
        if exe is None:
            missing("%s for %s" % (LOADER, game))
        if game == games.ANNO_1800:
            return [exe]
        return sorted(
            other for other in exe.parent.glob("xmltest*.exe") if other.name != XMLTEST.name
        )

    def radius(self, exe, name, body, game):
        folder = self.mod(name, body, game=game)
        applied = modio_loader.apply_stack(exe, self.target, [folder], self.root / "mods", game)
        return applied.assets["1000"].findtext("Values/Building/Radius")

    def test_text_beside_a_copy_is_run_together_with_it(self):
        """``<ModOpContent/>`` with text on both sides, in every build: the
        text before, the copy and the text after.  The Studio reads it so."""
        body = (
            '<ModOp Type="merge" GUID="1000" Path="/Values/Building" '
            'Content="@1001/Building/Radius/text()"><Radius>1<ModOpContent/>7</Radius></ModOp>'
        )
        for game in (games.ANNO_1800, games.ANNO_117):
            for exe in self.builds_for(game):
                with self.subTest(build=exe.name):
                    self.assertEqual(self.radius(exe, "copy " + exe.stem, body, game), "147")

    # A ``<ModOpContent/>`` straight inside an ``add`` whose copy is text:
    # what stands in the op, and what asset 1000's name, ``alpha``, is after
    # it.  The copy is 1001's radius, ``4``, but for the last.
    STRAIGHT_INSIDE = (
        ("before<ModOpContent/>after", "alphabefore4after"),
        ("before<ModOpContent/>", "alphabefore4"),
        ("<ModOpContent/>after", "alpha4after"),
        ("<ModOpContent/>", "alpha4"),
        ("\n  <ModOpContent/>\n", "alpha4"),
        ("\n  be fore \n <ModOpContent /> af ter \n", "alpha\n  be fore \n 4 af ter \n"),
        # As *Enable DLC11 Story* writes it, in its texts.
        ("&lt;br/&gt;(<ModOpContent />)", "alpha<br/>(4)"),
        ("[<ModOpContent/>]", "alpha[2001][2002][2003]"),
    )
    STRAIGHT_INSIDE_OP = (
        '<ModOp Type="%s" GUID="1000" Path="/Values/Standard/Name" Content="@%s">%s</ModOp>'
    )

    def standard(self, exe, name, body, game):
        folder = self.mod(name, body, game=game)
        applied = modio_loader.apply_stack(exe, self.target, [folder], self.root / "mods", game)
        return applied.assets["1000"].find("Values/Standard")

    def test_a_copy_marked_straight_inside_an_add_is_added_as_text(self):
        """The loader's docs show ``<ModOpContent/>`` inside an element.  One
        standing in the op itself, in an ``add`` whose copy is text, is put
        in every build where the docs' adding of text to a field would put
        text: at the end of what the field says, with the op's own text
        round it, once for each node copied.  The Studio reads it so."""
        for number, (inside, expected) in enumerate(self.STRAIGHT_INSIDE):
            source = "1000/ProductList/List/Item/Product" if "[" in inside else "1001/Building/Radius"
            body = self.STRAIGHT_INSIDE_OP % ("add", source + "/text()", inside)
            for game in (games.ANNO_1800, games.ANNO_117):
                for exe in self.builds_for(game):
                    with self.subTest(inside=inside, build=exe.name):
                        name = self.standard(exe, "straight %s %d" % (exe.stem, number), body, game)[1]
                        self.assertEqual((name.tag, name.text, len(name)), ("Name", expected, 0))

    def test_marked_straight_inside_anything_else_it_comes_to_text_left_loose(self):
        """The builds agree on these too, and none comes to a thing the docs
        describe or the Studio could show: a copied node mixed with text
        inside a field, text loose among fields, a ``replace`` with the text
        after the mark first, a second mark left in the file.  The Studio
        reads none of them and carries the op."""
        text, node = "1001/Building/Radius/text()", "1001/Building/Radius"

        def said(standard):
            return etree.tostring(standard, encoding="unicode", with_tail=False).replace("\n    ", "")

        for number, (kind, source, inside, expected) in enumerate((
            ("add", node, "before<ModOpContent/>after",
             "<Name>alphabefore<Radius>4</Radius>after</Name>"),
            ("addNextSibling", text, "before<ModOpContent/>after", "<Name>alpha</Name>before4after"),
            ("addPrevSibling", text, "<ModOpContent/>", "<GUID>1000</GUID>4<Name>alpha</Name>"),
            ("replace", text, "before<ModOpContent/>after", "<GUID>1000</GUID>after4before"),
            ("add", text, "a<ModOpContent/>b<ModOpContent/>c", "<Name>alphaa4b<ModOpContent/>c</Name>"),
        )):
            body = self.STRAIGHT_INSIDE_OP % (kind, source, inside)
            for game in (games.ANNO_1800, games.ANNO_117):
                for exe in self.builds_for(game):
                    with self.subTest(kind=kind, inside=inside, build=exe.name):
                        standard = self.standard(exe, "loose %s %d" % (exe.stem, number), body, game)
                        self.assertIn(expected, said(standard))

    def test_text_beside_a_value_worked_out_is_not_run_together_with_it(self):
        """``<ModValue/>`` is not ``<ModOpContent/>``.  On a merge both Anno
        117 builds give the text after it alone, and the value alone where
        text only stands before it, and so does the game
        (``test_xmltest3_answers_what_the_game_was_asked_as_the_game_did``).
        The Studio read the first as ``15``, and reads it as ``7`` now."""
        merge = '<ModOp Type="merge" GUID="1000" Path="/Values/Building"><Radius>%s</Radius></ModOp>'
        for number, (inside, expected) in enumerate((
            ('<ModValue Insert="2+3"/>', "5"),
            ('0<ModValue Insert="2+3"/>', "5"),
            ('1<ModValue Insert="2+3"/>', "5"),
            ('1<ModValue Insert="2+3"/>7', "7"),
            ('<ModValue Insert="2+3"/>7', "7"),
            # What the field held is still what ``self::node()`` is, and a
            # copy is no different from a value worked out.
            ('3<ModValue Insert="self::node() * 2"/>', "20"),
            ('<ModValue Insert="self::node() * 2"/>7', "7"),
            ('9<ModValue Insert="@1001/Building/Radius/text()"/>', "4"),
            ('<ModValue Insert="@1001/Building/Radius/text()"/>7', "7"),
        )):
            for exe in self.builds_for(games.ANNO_117):
                with self.subTest(inside=inside, build=exe.name):
                    name = "value %s %d" % (exe.stem, number)
                    self.assertEqual(
                        self.radius(exe, name, merge % inside, games.ANNO_117), expected
                    )

    # A ``ModValue`` alone in its field, by what it works out: the mark, and
    # what it comes to for asset 1000's radius, ``10``, where it is worked out.
    WORKED_OUT = (
        ('<ModValue Insert="2+3"/>', "5"),
        ('<ModValue Insert="self::node() * 2"/>', "20"),
        ('<ModValue Insert="$size"/>', "17"),
        ('<ModValue Insert="self::node() * $size"/>', "170"),
        ('<ModValue Insert="@1001/Building/Radius/text()"/>', "4"),
        ('<ModValue Insert="../BuildingType/text()"/>', "Factory"),
    )
    SIZE = '{"Options": {"size": {"type": "slider", "default": "17"}}}'
    NEW_ASSET = (
        "<Asset><Template>Building</Template><Values><Standard><GUID>5000</GUID><Name>new</Name>"
        "</Standard><Building><BuildingType>Factory</BuildingType><Radius>%s</Radius></Building>"
        "</Values></Asset>"
    )

    def building(self, exe, name, body, guid="1000"):
        folder = self.mod(name, body, modinfo=self.SIZE, game=games.ANNO_117)
        applied = modio_loader.apply_stack(
            exe, self.target, [folder], self.root / "mods", games.ANNO_117
        )
        return applied.assets[guid].find("Values/Building")

    def test_under_a_merge_both_anno_117_builds_work_a_value_out(self):
        """Wherever the mark stands, in the op or in a field of its body.
        The docs work one out under ``Merge``, and the Studio reads it."""
        for shape, marks in (
            ('<ModOp Type="merge" GUID="1000" Path="/Values/Building"><Radius>%s</Radius></ModOp>',
             self.WORKED_OUT),
            ('<ModOp Type="merge" GUID="1000" Path="/Values/Building/Radius">%s</ModOp>',
             self.WORKED_OUT[:2]),
            ('<ModOp Merge="@1000/Building/Radius">%s</ModOp>', self.WORKED_OUT[1:3]),
        ):
            for number, (mark, expected) in enumerate(marks):
                for exe in self.builds_for(games.ANNO_117):
                    with self.subTest(shape=shape, mark=mark, build=exe.name):
                        name = "merged %s %d %d" % (exe.stem, number, len(shape))
                        radius = self.building(exe, name, shape % mark).find("Radius")
                        self.assertEqual((radius.text, len(radius)), (expected, 0))

    def test_under_anything_else_the_two_builds_part(self):
        """xmltest2 leaves the mark in the file as it was written.  xmltest3
        works it out, to ``NaN`` where it builds on the field's own value.
        The game was asked on 10 Oct 2026 and does as xmltest3 does
        (``test_xmltest3_answers_what_the_game_was_asked_as_the_game_did``),
        so the Studio reads these as xmltest3 has them and gives no number
        where that is ``NaN``.  *Custom Research Options* and six more mods
        on mod.io write their new assets' values so, 433 ops of them."""
        older, newer = self.builds_for(games.ANNO_117)
        self.assertEqual((older.name, newer.name), ("xmltest2.exe", "xmltest3.exe"))
        radius = "/Values/Building/Radius"
        for number, (shape, field, guid) in enumerate((
            ('<ModOp Type="replace" GUID="1000" Path="%s"><Radius>%%s</Radius></ModOp>' % radius,
             "Radius", "1000"),
            ('<ModOp Replace="@1000/Building/Radius"><Radius>%s</Radius></ModOp>', "Radius", "1000"),
            ('<ModOp Type="add" GUID="1000" Path="/Values/Building"><Extra>%s</Extra></ModOp>',
             "Extra", "1000"),
            ('<ModOp Type="addNextSibling" GUID="1000" Path="%s"><Extra>%%s</Extra></ModOp>' % radius,
             "Extra", "1000"),
            ('<ModOp Type="addPrevSibling" GUID="1000" Path="%s"><Extra>%%s</Extra></ModOp>' % radius,
             "Extra", "1000"),
            ('<ModOp Type="AddNextSibling" GUID="1000">%s</ModOp>' % self.NEW_ASSET, "Radius", "5000"),
        )):
            # Every kind of mark in a replace and in a new asset, which are
            # what the census writes, and three of them in the rest.
            for which, (mark, value) in enumerate(
                self.WORKED_OUT if number in (0, 5) else self.WORKED_OUT[:3]
            ):
                expected = "NaN" if "self::node()" in mark else value
                with self.subTest(shape=shape, mark=mark):
                    name = "elsewhere %d %d" % (number, which)
                    left = self.building(older, name + " a", shape % mark, guid).find(field)
                    made = self.building(newer, name + " b", shape % mark, guid).find(field)
                    self.assertEqual(
                        ([child.tag for child in left], (left.text or "").strip()), (["ModValue"], "")
                    )
                    self.assertEqual((made.text, len(made)), (expected, 0))

    # The build the Studio runs for Anno 117, and the one that answered every
    # question put to the game on 10 Oct 2026 as the game did.
    GAME_LIKE = "xmltest3.exe"
    ARITH = '<ModValue Insert="2+3"/>'
    LEFT = ("", ["ModValue"])

    def game_like(self):
        exe = modio_loader.find_xmltest(games.ANNO_117)
        if exe is None:
            missing("%s for Anno 117" % LOADER)
        self.assertEqual(exe.name, self.GAME_LIKE)
        return exe

    def asked(self, name, body, modinfo=None):
        """The assets one mod of many ops leaves, by GUID.  An op is nothing
        to the marks in another op of its file, which is measured here too:
        every answer below was first had from a mod of that one op."""
        folder = self.mod(name, body, modinfo=modinfo or self.SIZE, game=games.ANNO_117)
        applied = modio_loader.apply_stack(
            self.game_like(), self.target, [folder], self.root / "mods", games.ANNO_117
        )
        self.assertEqual(applied.failures, [])
        return applied.assets

    @staticmethod
    def held(section, name):
        """What a field holds: its text and the elements in it, or ``None``
        where the section has no such field."""
        field = section.find(name)
        if field is None:
            return None
        return (field.text or "").strip(), [child.tag for child in field]

    # The first run in the game (``cases.py`` in the evidence folder the
    # design notes name): an asset of the probe's own whose fields start at
    # 9, an option at 7 and a ``<Seven>7</Seven>`` to copy.  Each case is one
    # op on a field of its own: the field, the op, and what the game's log
    # said the field held afterwards.
    PROBE = (
        "<Asset><Template>Building</Template><Values><Standard><GUID>5000</GUID><Name>probe</Name>"
        "</Standard><MVP>%s<Anchor>1</Anchor><Seven>7</Seven></MVP></Values></Asset>"
    )
    TWICE = '<ModValue Insert="self::node() * 2"/>'
    SEVEN = '<ModValue Insert="@5000/MVP/Seven/text()"/>'
    OPT = '<ModValue Insert="$opt"/>'
    IN_GAME = (
        ("merge", _on_the_probe("merge", "merge", ARITH), ("5", [])),
        ("mshort", '<ModOp Merge="@5000/MVP"><mshort>%s</mshort></ModOp>' % ARITH, ("5", [])),
        ("mdirect", '<ModOp Type="merge" GUID="5000" Path="/Values/MVP/mdirect">%s</ModOp>' % ARITH,
         ("5", [])),
        ("self_m", _on_the_probe("merge", "self_m", TWICE), ("18", [])),
        ("copy_m", _on_the_probe("merge", "copy_m", SEVEN), ("7", [])),
        ("opt_m", _on_the_probe("merge", "opt_m", OPT), ("7", [])),
        # Text beside the mark under a merge: the text after is what the
        # field ends up holding, and the text before is dropped.
        ("t1", _on_the_probe("merge", "t1", "1" + ARITH + "7"), ("7", [])),
        ("t2", _on_the_probe("merge", "t2", ARITH + "7"), ("7", [])),
        ("t3", _on_the_probe("merge", "t3", "1" + ARITH), ("5", [])),
        ("replace", _on_the_probe("replace", "replace", ARITH, "/replace"), ("5", [])),
        ("rshort", '<ModOp Replace="@5000/MVP/rshort"><rshort>%s</rshort></ModOp>' % ARITH,
         ("5", [])),
        # Straight inside the replace: the field is gone.
        ("rdirect", '<ModOp Type="replace" GUID="5000" Path="/Values/MVP/rdirect">%s</ModOp>' % ARITH,
         None),
        ("self_r", _on_the_probe("replace", "self_r", TWICE, "/self_r"), ("NaN", [])),
        ("copy_r", _on_the_probe("replace", "copy_r", SEVEN, "/copy_r"), ("7", [])),
        ("opt_r", _on_the_probe("replace", "opt_r", OPT, "/opt_r"), ("7", [])),
        # Text beside the mark under a replace: left as written.
        ("t1r", _on_the_probe("replace", "t1r", "1" + ARITH + "7", "/t1r"), ("1", ["ModValue"])),
        ("add", _on_the_probe("add", "add", ARITH), ("5", [])),
        ("addshort", '<ModOp Add="@5000/MVP"><addshort>%s</addshort></ModOp>' % ARITH, ("5", [])),
        ("sib", _on_the_probe("addNextSibling", "sib", ARITH, "/Anchor"), ("5", [])),
        ("prev", _on_the_probe("addPrevSibling", "prev", ARITH, "/Anchor"), ("5", [])),
        ("app", '<ModOp Append="@5000/MVP/Anchor"><app>%s</app></ModOp>' % ARITH, ("5", [])),
    )
    STARTS_AT_9 = ("merge", "mshort", "mdirect", "self_m", "copy_m", "opt_m", "t1", "t2", "t3",
                   "replace", "rshort", "rdirect", "self_r", "copy_r", "opt_r", "t1r")
    # The second run (``round2.py``): three assets added whole beside a
    # shipped one, with no empty element, with one after the section holding
    # the marks and with one before it.
    ROUND_2 = (
        ("arith", ARITH), ("opt", OPT),
        ("optmath", '\n            <ModValue Insert="3 * $opt"/>\n          '),
        ("copy", '<ModValue Insert="@5001/MVK/Seven/text()"/>'), ("self", TWICE),
        ("text", "1" + ARITH + "7"),
    )
    WHOLE = (
        '<ModOp Type="AddNextSibling" GUID="1000"><Asset><Template>Building</Template><Values>'
        "<Standard><GUID>%s</GUID><Name>whole</Name></Standard>%s<MVK>%s<Seven>7</Seven></MVK>%s"
        "</Values></Asset></ModOp>"
    )
    OPT_AT_7 = '{"Options": {"opt": {"type": "slider", "default": "7"}}}'

    def test_xmltest3_answers_what_the_game_was_asked_as_the_game_did(self):
        """Anno 117 was asked 49 questions in two runs, with marker ops its
        loader logs, and xmltest3 had predicted every answer.  These are the
        ones the Studio's reading stands on.  The asset's sections here
        hold no empty element, as in the second run."""
        probe = '<ModOp Type="AddNextSibling" GUID="1000">%s</ModOp>' % (
            self.PROBE % "".join("<%s>9</%s>" % (tag, tag) for tag in self.STARTS_AT_9)
        )
        assets = self.asked(
            "in game", probe + "".join(op for _field, op, _held in self.IN_GAME), self.OPT_AT_7
        )
        for field, _op, held in self.IN_GAME:
            with self.subTest(field=field):
                self.assertEqual(self.held(assets["5000"].find("Values/MVP"), field), held)

        inside = "".join("<%s>%s</%s>" % (name, mark, name) for name, mark in self.ROUND_2)
        assets = self.asked(
            "in game 2",
            self.WHOLE % ("5001", "", inside, "")
            + self.WHOLE % ("5002", "", inside, "<Hollow />")
            + self.WHOLE % ("5003", "<Hollow />", inside, ""),
            self.OPT_AT_7,
        )
        worked = {"arith": ("5", []), "opt": ("7", []), "optmath": ("21", []), "copy": ("7", []),
                  "self": ("NaN", []), "text": ("1", ["ModValue"])}
        for guid, what in (("5001", "no empty element"), ("5002", "one after the marks")):
            for name, expected in worked.items():
                with self.subTest(what, field=name):
                    self.assertEqual(self.held(assets[guid].find("Values/MVK"), name), expected)
        for name, _mark in self.ROUND_2:
            with self.subTest("one before the marks", field=name):
                expected = worked["text"] if name == "text" else self.LEFT
                self.assertEqual(self.held(assets["5003"].find("Values/MVK"), name), expected)

    # An asset added whole whose radius is the ``size`` option, 17, by what
    # else stands in it: before <Values>, first in it, inside <Standard>,
    # between that and the mark's section, in the section before the mark's
    # field, and after the section.  And whether the mark is worked out.
    HOLLOW = (
        '<ModOp Type="%s" GUID="1000"><Asset>%s<Values>%s<Standard><GUID>%d</GUID><Name>new</Name>'
        '%s</Standard>%s<Building>%s<Radius><ModValue Insert="$size"/></Radius></Building>%s'
        "</Values></Asset></ModOp>"
    )
    TEMPLATE = "<Template>Building</Template>"
    WHERE_THE_LINE_RUNS = (
        # what, the op type, template, first, in Standard, between, in the section, after, worked
        ("none at all", "AddNextSibling", TEMPLATE, "", "", "", "", "", True),
        ("between, as the game was asked", "AddNextSibling", TEMPLATE, "", "", "<Hollow />", "", "", False),
        ("the same under addPrevSibling", "addPrevSibling", TEMPLATE, "", "", "<Hollow />", "", "", False),
        ("written with an end tag", "AddNextSibling", TEMPLATE, "", "", "<Hollow></Hollow>", "", "", False),
        ("holding layout", "AddNextSibling", TEMPLATE, "", "", "<Hollow>\n  </Hollow>", "", "", False),
        ("holding a comment", "AddNextSibling", TEMPLATE, "", "", "<Hollow><!-- c --></Hollow>", "", "",
         False),
        ("with an attribute", "AddNextSibling", TEMPLATE, "", "", '<Hollow a="1" />', "", "", False),
        ("first among the sections", "AddNextSibling", TEMPLATE, "<Hollow />", "", "", "", "", False),
        ("in the mark's own section", "AddNextSibling", TEMPLATE, "", "", "", "<Hollow />", "", False),
        ("before <Values>", "AddNextSibling", TEMPLATE + "<Hollow />", "", "", "", "", "", False),
        ("the template itself", "AddNextSibling", "<Template />", "", "", "", "", "", False),
        ("text loose among the sections", "AddNextSibling", TEMPLATE, "", "", "loose", "", "", False),
        ("a comment and no element", "AddNextSibling", TEMPLATE, "", "", "<!-- c -->", "", "", True),
        ("last in an earlier section", "AddNextSibling", TEMPLATE, "", "<Hollow />", "", "", "", True),
        ("in an earlier section, a field after it", "AddNextSibling", TEMPLATE, "", "",
         "<Other><Hollow /><F>1</F></Other>", "", "", True),
        ("alone in an earlier section", "AddNextSibling", TEMPLATE, "", "",
         "<Other><Hollow /></Other>", "", "", True),
        ("after the marks, as the game was asked", "AddNextSibling", TEMPLATE, "", "", "", "",
         "<Hollow />", True),
    )

    def test_an_empty_element_stops_the_marks_after_it_at_its_own_level(self):
        """The game was asked about an empty element among an asset's
        sections, before the one holding the marks and after it.  Where the
        line runs is xmltest3's answer: at each level of what an op puts in,
        the loader stops at the first thing with nothing inside it that has
        something after it, and one level up it goes on.  The Studio reads
        no op with a mark behind that line
        (``test_and_where_that_line_runs`` in test_import.py)."""
        body = "".join(
            self.HOLLOW % (kind, template, first, 5000 + number, named, between, beside, after)
            for number, (_what, kind, template, first, named, between, beside, after, _worked)
            in enumerate(self.WHERE_THE_LINE_RUNS)
        )
        assets = self.asked("hollow", body)
        for number, row in enumerate(self.WHERE_THE_LINE_RUNS):
            with self.subTest(row[0]):
                held = self.held(assets[str(5000 + number)].find("Values/Building"), "Radius")
                self.assertEqual(held, ("17", []) if row[-1] else self.LEFT)

    def test_it_stops_what_follows_it_and_nothing_before(self):
        """A mark before the empty element is worked out and one after it is
        left, in one asset."""
        assets = self.asked(
            "between",
            '<ModOp Type="AddNextSibling" GUID="1000"><Asset><Template>Building</Template><Values>'
            "<Standard><GUID>5000</GUID><Name>new</Name></Standard><Building><Radius>%s</Radius>"
            '<Hollow /><Kind><ModValue Insert="$size"/></Kind></Building></Values></Asset></ModOp>'
            % self.ARITH,
        )
        building = assets["5000"].find("Values/Building")
        self.assertEqual(self.held(building, "Radius"), ("5", []))
        self.assertEqual(self.held(building, "Kind"), self.LEFT)

    # What an op with a path puts in, and a merge of something the game does
    # not have: the op, where the field with the mark ends up in asset 1000,
    # and what it holds.
    PUT_IN = (
        ('<ModOp Type="add" GUID="1000" Path="/Values/Building"><P1><Hollow /><N>%s</N></P1></ModOp>',
         "Building/P1/N", LEFT),
        ('<ModOp Type="add" GUID="1000" Path="/Values/Building"><P2><N>%s</N><Hollow /></P2></ModOp>',
         "Building/P2/N", ("5", [])),
        # Each element straight inside the op is gone through on its own.
        ('<ModOp Type="add" GUID="1000" Path="/Values/Building"><Hollow /><P3>%s</P3></ModOp>',
         "Building/P3", ("5", [])),
        ('<ModOp Type="add" GUID="1000" Path="/Values/Building"><P4><S><Hollow /></S><N>%s</N></P4>'
         "</ModOp>", "Building/P4/N", ("5", [])),
        ('<ModOp Type="addNextSibling" GUID="1000" Path="/Values/Building/Radius">'
         "<P5><Hollow /><N>%s</N></P5></ModOp>", "Building/P5/N", LEFT),
        ('<ModOp Type="addPrevSibling" GUID="1000" Path="/Values/Building/Radius">'
         "<P6><Hollow /><N>%s</N></P6></ModOp>", "Building/P6/N", LEFT),
        ('<ModOp Type="replace" GUID="1001" Path="/Values/Building"><Building><Hollow />'
         "<BuildingType>Farm</BuildingType><Radius>%s</Radius></Building></ModOp>", "1001", LEFT),
        # Text before the mark is such a thing too, the shipped files' ``0``
        # included.  Text after it alone is dropped and the value given,
        # which the game was not asked about and the Studio does not read.
        ('<ModOp Type="add" GUID="1000" Path="/Values/Building"><T1>1%s</T1></ModOp>',
         "Building/T1", ("1", ["ModValue"])),
        ('<ModOp Type="add" GUID="1000" Path="/Values/Building"><T2>0%s</T2></ModOp>',
         "Building/T2", ("0", ["ModValue"])),
        ('<ModOp Type="add" GUID="1000" Path="/Values/Building"><T3>%s7</T3></ModOp>',
         "Building/T3", ("5", [])),
        # A merge: what the game does not have is put in as an add puts it.
        ('<ModOp Type="merge" GUID="1000" Path="/Values"><M1><N>%s</N></M1></ModOp>', "M1/N", ("5", [])),
        ('<ModOp Type="merge" GUID="1000" Path="/Values"><M2><Hollow /><N>%s</N></M2></ModOp>',
         "M2/N", LEFT),
        ('<ModOp Type="merge" GUID="1000" Path="/Values"><Building><M3><Hollow /><N>%s</N></M3>'
         "</Building></ModOp>", "Building/M3/N", LEFT),
        ('<ModOp Type="merge" GUID="1000" Path="/Values/Building"><Hollow /><M4>%s</M4></ModOp>',
         "Building/M4", ("5", [])),
        ('<ModOp Type="merge" GUID="1000" Path="/Values/Building"><M5>1%s</M5></ModOp>',
         "Building/M5", ("1", ["ModValue"])),
        # And what it finds in the game is merged, whatever stands before it.
        ('<ModOp Type="merge" GUID="1000" Path="/Values"><Empty /><Building><Hollow />'
         "<Radius>%s</Radius></Building></ModOp>", "Building/Radius", ("5", [])),
    )

    def test_a_field_put_in_with_a_path_follows_the_same_line(self):
        """Not only a whole asset: a section added, or one a merge adds, is
        gone through the same way.  The Studio reads these as xmltest3 has
        them (``test_a_field_added_with_a_path_follows_the_same_line`` and
        the test after it in test_import.py)."""
        assets = self.asked("put in", "".join(op % self.ARITH for op, _where, _held in self.PUT_IN))
        for op, where, held in self.PUT_IN:
            with self.subTest(op=op):
                guid, where = ("1001", "Building/Radius") if where == "1001" else ("1000", where)
                section, _slash, name = where.rpartition("/")
                self.assertEqual(self.held(assets[guid].find("Values/" + section), name), held)
        new = '<ModOp Type="merge" GUID="1000" Path="/Values/Building"><M6>%s</M6></ModOp>' % self.TWICE
        self.assertEqual(
            self.held(self.asked("no value yet", new)["1000"].find("Values/Building"), "M6"), ("NaN", [])
        )

    def test_and_flags_under_anything_else_are_left_as_written_too(self):
        """``<ModValue Merge="…"/>`` under a replace: xmltest2 leaves it,
        xmltest3 merges it into nothing.  ``Remove`` is left by both, and so
        is every mark where the field holds a list of the mod's own.  The
        game was not asked about flags, so the Studio still reads none
        outside a merge."""
        older, newer = self.builds_for(games.ANNO_117)
        replace = '<ModOp Replace="@1000/Building/BuildingType"><BuildingType>%s</BuildingType></ModOp>'

        def marks(exe, name, inside):
            kind = self.building(exe, name, replace % inside).find("BuildingType")
            return (kind.text or "").strip(), len(kind)

        self.assertEqual(marks(older, "flag a", '<ModValue Merge="Moderate"/>'), ("", 1))
        self.assertEqual(marks(newer, "flag b", '<ModValue Merge="Moderate"/>'), ("Moderate", 0))
        own = 'Rain;SnowRain<ModValue Remove="Rain;Snow"/><ModValue Merge="Custom"/>'
        for exe in (older, newer):
            with self.subTest(build=exe.name):
                self.assertEqual(
                    marks(exe, "flag c " + exe.stem, '<ModValue Remove="Factory"/>'), ("", 1)
                )
                self.assertEqual(marks(exe, "flag d " + exe.stem, own), ("Rain;SnowRain", 2))

    def test_a_field_copied_whole_into_a_field_is_put_there_as_a_node(self):
        """``Insert`` naming a field with no ``/text()``, under a merge: both
        Anno 117 builds leave the field holding the copied element and no
        value.  The Studio read it as the value, and now reads no such op."""
        body = (
            '<ModOp Merge="@1000/Building"><Radius>'
            '<ModValue Insert="@1001/Building/Radius"/></Radius></ModOp>'
        )
        for exe in self.builds_for(games.ANNO_117):
            with self.subTest(build=exe.name):
                radius = self.building(exe, "node " + exe.stem, body).find("Radius")
                self.assertEqual((radius.text or "").strip(), "")
                self.assertEqual([(child.tag, child.text) for child in radius], [("Radius", "4")])

    def test_an_op_that_matches_nothing_is_in_the_log(self):
        folder = self.mod(
            "miss", '<ModOp Type="replace" GUID="1000" Path="/Values/Nowhere"><Nowhere>1</Nowhere></ModOp>'
        )
        applied = self.apply(folder)
        self.assertTrue(any(key.startswith("warning") for key in applied.log), applied.log)

    def test_a_list_of_guids_is_split_on_the_comma_and_nothing_else(self):
        """Against every guess, and the reason three census mods reach fewer
        assets in game than they name.  The Studio's reading rests on it."""
        op = '<ModOp Type="replace" GUID="%s" Path="/Values/Building/Radius"><Radius>77</Radius></ModOp>'
        for game in (games.ANNO_1800, games.ANNO_117):
            for written, reaches in (
                ("1000,1001", ["1000", "1001"]),
                ("1000, 1001", ["1000"]),
                ("1000 ,1001", ["1001"]),
                (" 1001 ", []),
            ):
                with self.subTest(game=game, guid=written):
                    applied = self.apply(
                        self.mod("guids %s %s" % (game, reaches), op % written, game=game),
                        game=game,
                    )
                    self.assertEqual(
                        sorted(
                            guid for guid, asset in applied.assets.items()
                            if asset is not None
                            and asset.findtext("Values/Building/Radius") == "77"
                        ),
                        reaches,
                    )

    def test_one_op_adding_two_assets_is_not_two_ops_adding_one(self):
        """``addNextSibling`` puts what it carries right after the node it
        names, so the second of two such ops lands *before* the first: written
        back one change at a time, a pair of assets one op added comes out
        backwards.  ``addPrevSibling`` reads the same either way.

        Two assets of one GUID is how it can be seen at all -- the diff names
        each asset by its GUID, and holds the *last* of a repeated one -- and
        also how it showed up: *Harborlife* adds ninety triggers in one op and
        repeats a GUID by accident."""
        asset = (
            "<Asset><Template>Building</Template><Values><Standard><GUID>7777</GUID>"
            "<Name>%s</Name></Standard></Values></Asset>"
        )
        for game in (games.ANNO_1800, games.ANNO_117):
            for kind, last_when_together, last_when_apart in (
                ("addNextSibling", "B", "A"),
                ("addPrevSibling", "B", "B"),
            ):
                one = '<ModOp Type="%s" GUID="1000">%s%s</ModOp>' % (
                    kind, asset % "A", asset % "B")
                two = ('<ModOp Type="%s" GUID="1000">%s</ModOp>' % (kind, asset % "A")
                       + '<ModOp Type="%s" GUID="1000">%s</ModOp>' % (kind, asset % "B"))
                for body, last in ((one, last_when_together), (two, last_when_apart)):
                    with self.subTest(game=game, kind=kind, ops=1 if body is one else 2):
                        applied = self.apply(
                            self.mod("order %s %s %d" % (game, kind, len(body)), body, game=game),
                            game=game,
                        )
                        self.assertEqual(
                            applied.assets["7777"].findtext("Values/Standard/Name"), last
                        )

    def test_a_later_op_aimed_at_a_repeated_guid_edits_the_first_of_them(self):
        """The look-up takes the first match, where the diff reports the last,
        so the asset the diff shows is the one the later op did *not* touch.
        Both halves are needed to read a diff of a mod that repeats a GUID."""
        asset = (
            "<Asset><Template>Building</Template><Values><Standard><GUID>7777</GUID>"
            "<Name>%s</Name></Standard><Building><Radius>1</Radius></Building></Values></Asset>"
        )
        body = (
            '<ModOp Type="addNextSibling" GUID="1000">%s%s</ModOp>' % (asset % "A", asset % "B")
            + '<ModOp Type="replace" GUID="7777" Path="/Values/Building/Radius">'
            "<Radius>9</Radius></ModOp>"
        )
        for game in (games.ANNO_1800, games.ANNO_117):
            with self.subTest(game=game):
                applied = self.apply(self.mod("repeated " + game, body, game=game), game=game)
                found = applied.assets["7777"]
                self.assertEqual(found.findtext("Values/Standard/Name"), "B")
                self.assertEqual(found.findtext("Values/Building/Radius"), "1")

    def test_of_two_assets_under_one_guid_the_first_in_the_file_is_found(self):
        """Not the first made. Made beside the last asset and then beside the
        first, the second stands first in the file, and an op aimed at the
        GUID edits that one: what :mod:`core.repeats` names as the one the
        game finds. The diff shows the last, so the last is seen unedited."""
        def made(name, beside):
            return (
                '<ModOp Type="addNextSibling" GUID="%s"><Asset><Template>T</Template><Values>'
                "<Standard><GUID>7777</GUID><Name>%s</Name></Standard></Values></Asset></ModOp>"
                % (beside, name)
            )

        body = (made("made first", "1002") + made("made second", "1000")
                + '<ModOp Type="add" GUID="7777" Path="/Values/Standard"><Edited>1</Edited></ModOp>')
        for game in (games.ANNO_1800, games.ANNO_117):
            with self.subTest(game=game):
                applied = self.apply(self.mod("placed " + game, body, game=game), game=game)
                last = applied.assets["7777"]
                self.assertEqual(last.findtext("Values/Standard/Name"), "made first")
                self.assertIsNone(last.find("Values/Standard/Edited"))

    def test_a_comment_takes_the_blank_line_it_sat_on_with_it(self):
        """The loader's parser makes a node of a run of text only when there is
        something in it, so a value written around comments loses the lines the
        comments were on.  *Add Collection* writes a hundred console commands
        that way."""
        body = (
            '<ModOp Type="add" GUID="1001" Path="/Values/Building">'
            "<Command>first\n    <!-- a -->\n    <!-- b -->\n    second\n  </Command>"
            "<Blank>\n    <!-- only -->\n  </Blank></ModOp>"
        )
        for game in (games.ANNO_1800, games.ANNO_117):
            with self.subTest(game=game):
                applied = self.apply(self.mod("comments " + game, body, game=game), game=game)
                building = applied.assets["1001"].find("Values/Building")
                self.assertEqual(building.findtext("Command"), "first\n    \n    second\n  ")
                self.assertIsNone(building.find("Blank").text)


class TestTheWholeStackInOneRun(TestTheRealLoader):
    """Everything above again, the stack run once and the patched file
    compared with the unpatched one: the census's way, which has to come to
    what running it mod by mod comes to."""

    def apply(self, *folders, game=games.ANNO_1800, cache=None):
        exe = XMLTEST if game == games.ANNO_1800 else modio_loader.find_xmltest(game)
        if exe is None:
            missing("%s for %s" % (LOADER, game))
        return modio_loader.apply_whole(exe, self.target, folders, self.root / "mods", game, cache=cache)

    def test_a_stack_ends_with_each_asset_as_the_last_mod_left_it(self):
        first = self.mod(
            "a",
            '<ModOp Type="replace" GUID="1000" Path="/Values/Building/Radius"><Radius>12</Radius></ModOp>'
            '<ModOp Type="replace" GUID="1001" Path="/Values/Building/Radius"><Radius>5</Radius></ModOp>',
        )
        second = self.mod(
            "b", '<ModOp Type="replace" GUID="1000" Path="/Values/Building/Radius"><Radius>30</Radius></ModOp>'
        )
        applied = self.apply(first, second)

        self.assertEqual(applied.failures, [])
        self.assertEqual(applied.runs, 1)
        self.assertEqual(applied.assets["1000"].findtext("Values/Building/Radius"), "30")
        self.assertEqual(applied.assets["1001"].findtext("Values/Building/Radius"), "5")
        self.assertNotIn("1002", applied.assets)

    def test_what_is_added_removed_and_put_back_comes_out_as_mod_by_mod(self):
        for game in (games.ANNO_1800, games.ANNO_117):
            with self.subTest(game=game):
                stack = [
                    self.mod("add " + game, '<ModOp Type="addNextSibling" GUID="1000">'
                             "<Asset><Template>Thing</Template><Values><Standard><GUID>5000</GUID>"
                             "<Name>new</Name></Standard></Values></Asset></ModOp>", game=game),
                    self.mod("remove " + game, '<ModOp Type="remove" GUID="1001" />', game=game),
                    self.mod("back " + game, '<ModOp Type="replace" GUID="1002" Path="/Values/Standard/Name">'
                             "<Name>x</Name></ModOp>", game=game),
                    self.mod("undo " + game, '<ModOp Type="replace" GUID="1002" Path="/Values/Standard/Name">'
                             "<Name>gamma</Name></ModOp>", game=game),
                ]
                whole = self.apply(*stack, game=game)
                by_mod = TestTheRealLoader.apply(self, *stack, game=game)
                self.assertEqual(whole.failures, [])
                self.assertEqual(whole.assets["5000"].findtext("Values/Standard/Name"), "new")
                self.assertIsNone(whole.assets["1001"])
                self.assertIsNone(by_mod.assets["1001"])
                # Mod by mod, an asset put back as it was is still listed; the
                # whole file has nothing to show for it, which comes to the same.
                self.assertIn("1002", by_mod.assets)
                self.assertNotIn("1002", whole.assets)
                self.assertEqual(set(whole.assets), set(by_mod.assets) - {"1002"})
                for guid, element in whole.assets.items():
                    if element is not None:
                        # Laid out differently -- the diff indents with tabs.
                        self.assertEqual(canonical(element), canonical(by_mod.assets[guid]), guid)

    def test_a_stack_seen_before_is_answered_from_the_cache(self):
        cache = self.root / "cache"
        first = self.mod(
            "a", '<ModOp Type="replace" GUID="1000" Path="/Values/Building/Radius"><Radius>12</Radius></ModOp>'
        )
        ran = self.apply(first, cache=cache)
        again = self.apply(first, cache=cache)
        self.assertFalse(ran.cached)
        self.assertTrue(again.cached)
        self.assertEqual(again.runs, 0)
        self.assertEqual(again.assets["1000"].findtext("Values/Building/Radius"), "12")

        # Anything xmltest would be given differently is a different question.
        (first / games.get(games.ANNO_1800).asset_relpath).write_text(
            '<ModOps><ModOp Type="replace" GUID="1000" Path="/Values/Building/Radius">'
            "<Radius>13</Radius></ModOp></ModOps>", encoding="utf-8")
        changed = self.apply(first, cache=cache)
        self.assertFalse(changed.cached)
        self.assertEqual(changed.assets["1000"].findtext("Values/Building/Radius"), "13")

    def test_the_minute_an_export_was_written_is_not_part_of_the_question(self):
        cache = self.root / "cache"
        patch = "<!--\n  Generated by Anno Modding Studio on %s.\n-->" + (
            '<ModOp Type="replace" GUID="1000" Path="/Values/Building/Radius"><Radius>12</Radius></ModOp>'
        )
        folder = self.mod("stamped", patch % "2026-09-27 22:31")
        self.assertFalse(self.apply(folder, cache=cache).cached)
        (folder / games.get(games.ANNO_1800).asset_relpath).write_text(
            "<ModOps>%s</ModOps>" % (patch % "2026-09-27 22:39"), encoding="utf-8")
        self.assertTrue(self.apply(folder, cache=cache).cached)



# One asset, GUID 1000, shaped for each case of TestTheLoadersOwnApply.
_ONE_ASSET = ("<AssetList><Assets><Asset><Template>Building</Template><Values>"
              "<Standard><GUID>1000</GUID><Name>alpha</Name></Standard>%s"
              "</Values></Asset></Assets></AssetList>")


@requires(XMLTEST, LOADER)
class TestTheLoadersOwnApply(unittest.TestCase):
    """``core/applying.py`` against the loader it is ported from: each case
    applied by both, in both games, to the same asset, which has to come out
    the same. The shapes are the ones the port reads from the loader's source
    where the Studio used to read them otherwise."""

    # (what the asset holds, type, path, payload)
    CASES = (
        # Named like its target, the payload stands for the target, even where
        # the target holds a node of that name (117 has 1,887 of them).
        ("<Objective><Objective><A>1</A></Objective></Objective>",
         "merge", "/Values/Objective", "<Objective><B>2</B></Objective>"),
        # An empty field merged in leaves the value where it was.
        ("<Building><Radius>10</Radius></Building>",
         "merge", "/Values/Building", "<Radius/>"),
        # A name in another case: Anno 117 finds the field, Anno 1800 adds one.
        ("<Building><radius>10</radius></Building>",
         "merge", "/Values/Building", "<Radius>4</Radius>"),
        ("<Building><Radius>10</Radius></Building>",
         "merge", "/Values", "<building><Radius>4</Radius></building>"),
        # A replace puts several nodes in last first.
        ("<List><Item>1</Item><Item>2</Item></List>",
         "replace", "/Values/List/Item[1]", "<Item>a</Item><Item>b</Item><Item>c</Item>"),
        ("<List><Item>1</Item><Item>2</Item></List>",
         "addNextSibling", "/Values/List/Item[1]", "<Item>a</Item><Item>b</Item>"),
        ("<List><Item>1</Item><Item>2</Item></List>",
         "addPrevSibling", "/Values/List/Item[2]", "<Item>a</Item><Item>b</Item>"),
    )

    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root, True)
        self.target = self.root / "assets.xml"

    def test_each_case_comes_out_as_the_loader_leaves_it(self):
        from anno_modding_studio.core import applying

        for number, (holds, kind, path, payload) in enumerate(self.CASES):
            for game in (games.ANNO_1800, games.ANNO_117):
                with self.subTest(case=number, game=game):
                    shipped = _ONE_ASSET % holds
                    self.target.write_text(shipped, encoding="utf-8")
                    folder = TestTheRealLoader.mod(
                        self, "case %d %s" % (number, game),
                        '<ModOp Type="%s" GUID="1000" Path="%s">%s</ModOp>' % (kind, path, payload),
                        game=game,
                    )
                    applied = TestTheRealLoader.apply(self, folder, game=game)
                    asset = etree.fromstring(shipped).find("Assets/Asset")
                    (node,) = asset.xpath("." + path)
                    content = list(etree.fromstring("<x>%s</x>" % payload))
                    applying.apply(kind, node, content, game)
                    self.assertEqual(applied.failures, [])
                    # Only what the loader changed comes back.
                    left = applied.assets.get("1000")
                    if left is None:
                        left = etree.fromstring(shipped).find("Assets/Asset")
                    self.assertEqual(canonical(asset), canonical(left))


if __name__ == "__main__":
    unittest.main()
