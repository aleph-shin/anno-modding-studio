"""The shortcut a search of every asset takes is sound: it turns away no
asset the path matches.

``AssetIndex.find_matching`` reads a hundred thousand assets, and parses only
those ``index.narrowing`` lets through. The shortcut reads the path as text,
and a path is a language: a union, an ``or``, a function, an entity each made
it turn away assets that match (audit findings A8-7, A8-8 and A8-11), with
nothing to say so but a mod's op finding fewer assets than the game does.

So it is asked here of paths made by the thousand, each answered twice: by
the search as it is, and by parsing every asset. The two have to agree.
"""

from __future__ import annotations

import random
import sys
import tempfile
import unittest
import zlib
from pathlib import Path

import _isolation  # noqa: F401 -- first: keeps the suite out of real files under any runner
from lxml import etree

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from anno_modding_studio.core import index as index_mod  # noqa: E402
from anno_modding_studio.core import modops  # noqa: E402


def _asset(guid: int, template: str, name: str, body: str) -> str:
    return (
        "<Asset><Template>%s</Template><Values><Standard><GUID>%d</GUID><Name>%s</Name>"
        "</Standard>%s</Values></Asset>" % (template, guid, name, body)
    )


ASSETS = "<AssetList><Groups><Group><Name>all</Name><Assets>%s</Assets></Group></Groups></AssetList>" % "".join((
    _asset(1, "Factory", "alpha", "<Building><Radius>10</Radius><Type>Land</Type></Building>"),
    _asset(2, "Factory", "beta", "<Building><Radius>4</Radius></Building><Cost><Item><Good>10</Good>"
                                 "<Amount>5</Amount></Item><Item><Good>11</Good></Item></Cost>"),
    _asset(3, "Trader", "Tom &amp; Co", "<Trader><Radius>10</Radius><Text>a &lt; b</Text></Trader>"),
    _asset(4, "Trader", "it's", "<Trader><Type>Water</Type></Trader><Cost><Item><Good>10</Good></Item></Cost>"),
    _asset(5, "Residence", "ALPHA", "<Residence><Radius>4</Radius><Empty/></Residence>"),
    _asset(6, "Residence", "gamma delta", "<Residence><Type>Land</Type><Group><Name>inner</Name></Group></Residence>"),
    _asset(7, "Plain", "10", ""),
))

TAGS = ("Building", "Trader", "Residence", "Cost", "Item", "Good", "Amount", "Radius", "Type",
        "Name", "Text", "Standard", "Values", "Template", "GUID", "Empty", "Group", "Missing")
TOPS = ("Building", "Trader", "Residence", "Cost", "Values", "Asset", "Standard", "Missing", "*")
VALUES = ("alpha", "beta", "Tom & Co", "it's", "ALPHA", "gamma delta", "10", "4", "5", "11",
          "Land", "Water", "Factory", "Trader", "a < b", "inner", "zzz", "alp", "", "1")


def _quoted(value: str) -> str:
    return '"%s"' % value if "'" in value else "'%s'" % value


def _predicate(rng: random.Random, depth: int = 0) -> str:
    tag, value = rng.choice(TAGS), _quoted(rng.choice(VALUES))
    # The last four are made of others, and are left out two deep.
    kind = rng.randrange(17 if depth < 2 else 13)
    if kind == 13:
        return "%s or %s" % (_predicate(rng, depth + 1), _predicate(rng, depth + 1))
    if kind == 14:
        return "%s and %s" % (_predicate(rng, depth + 1), _predicate(rng, depth + 1))
    if kind == 15:
        return "%s[%s]" % (tag, _predicate(rng, depth + 1))
    if kind == 16:
        return "(%s) or %s" % (_predicate(rng, depth + 1), tag)
    return (
        "%s=%s" % (tag, value),
        "%s=%s" % (value, tag),
        ".//%s=%s" % (tag, value),
        "%s!=%s" % (tag, value),
        "%s=%d" % (tag, rng.choice((4, 5, 10, 11))),
        "contains(%s,%s)" % (tag, value),
        "contains(%s,%s)" % (value, tag),
        "starts-with(.//%s,%s)" % (tag, value),
        "not(%s)" % tag,
        tag,
        "%s > %s" % (tag, value),
        "string-length(%s)=%d" % (value, rng.randrange(8)),
        "translate(%s,'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz')=%s" % (tag, value),
    )[kind]


def _one(rng: random.Random) -> str:
    top, tag = rng.choice(TOPS), rng.choice(TAGS)
    kind = rng.randrange(8)
    if kind == 0:
        return "//%s/%s" % (top, tag)
    if kind == 1:
        return "//%s[%s]" % (top, _predicate(rng))
    if kind == 2:
        return "//%s[%s]/%s" % (top, _predicate(rng), tag)
    if kind == 3:
        return "//Asset[%s]/Values/%s" % (_predicate(rng), tag)
    if kind == 4:
        return "//%s/%s[%s]" % (top, tag, _predicate(rng))
    if kind == 5:
        return "//%s/text()" % tag
    if kind == 6:
        return "//%s/.." % tag
    return "//%s[%s][%s]" % (top, _predicate(rng), _predicate(rng))


def a_path(rng: random.Random) -> str:
    kind = rng.randrange(10)
    if kind == 0:
        return "%s | %s" % (_one(rng), _one(rng))
    if kind == 1:
        return "(%s|%s)[%s]" % (_one(rng), _one(rng), _predicate(rng))
    return _one(rng)


class TestTheShortcutTurnsAwayNothingThatMatches(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        assets = Path(cls._tmp.name) / "assets.xml"
        assets.write_text(ASSETS, encoding="utf-8")
        db = Path(cls._tmp.name) / "index.sqlite"
        assert index_mod.build_index(assets, db) == 7
        cls.index = index_mod.AssetIndex(db, assets)
        cls.trees = [
            (row["guid"], etree.fromstring(zlib.decompress(row["xml"])).getroottree())
            for row in cls.index._con.execute("SELECT guid, xml FROM assets")
        ]

    @classmethod
    def tearDownClass(cls):
        cls.index.close()
        cls._tmp.cleanup()

    def by_parsing_every_asset(self, path: str):
        try:
            compiled = etree.XPath(path)
        except etree.XPathError:
            return None
        found = []
        for guid, tree in self.trees:
            try:
                if compiled(tree):
                    found.append(guid)
            except etree.XPathError:
                pass
        return found

    def test_over_paths_made_by_the_thousand(self):
        rng = random.Random(20261011)
        paths = list(dict.fromkeys(a_path(rng) for _ in range(4000)))
        answered = self.index.find_matching(paths)
        matched = turned_away = 0
        for path in paths:
            expected = self.by_parsing_every_asset(path)
            if expected is None:
                continue
            with self.subTest(path=path):
                self.assertEqual(sorted(answered.get(path, [])), sorted(expected),
                                 index_mod.narrowing(path))
                # And one asset asked on its own says what the sweep said of
                # it: the two answers are joined, for the assets a mod has
                # changed or made (A8-13).
                self.assertEqual(
                    [guid for guid, tree in self.trees
                     if modops.matches_anything(tree.getroot(), path)],
                    expected,
                )
            matched += bool(expected)
            needed, one_of = index_mod.narrowing(path)
            turned_away += bool(needed or one_of)
        # The paths are worth asking: many match something, and the shortcut
        # is in play for many.
        self.assertGreater(matched, 300)
        self.assertGreater(turned_away, 500)

    def test_the_paths_it_was_wrong_about(self):
        for path, expected in (
            ("//Values/Building | //Values/Trader", ["1", "2", "3", "4"]),
            ("(//Building | //Residence)[Radius=4]", ["2", "5"]),
            ("//Building[Radius=10 or Missing='zzz']", ["1"]),
            ("//Standard[Name='Tom & Co']", ["3"]),
            ("//Building[string-length('qq')=2]", ["1", "2"]),
            # Asset 3 has no Type, and every string contains the empty one.
            ("//Trader[contains('Watermark', Type)]", ["3", "4"]),
        ):
            with self.subTest(path=path):
                self.assertEqual(sorted(self.index.find_matching([path])[path]), expected)

    def test_a_literal_set_against_no_node_is_not_asked_for(self):
        """A string equals a number, or a count of places, whatever the asset
        says; and a comparison that is itself compared can hold because the
        string is not there.  Each turned away assets that match (A8-13)."""
        for path, expected in (
            ("//Item['2'=2]", ["2", "4"]),
            ("//Item[2='2.0']", ["2", "4"]),
            ("//Item[position()='2']", ["2"]),
            ("//Building[last()='1.0']", ["1", "2"]),
            ("//Building[Radius='4'=0]", ["1"]),
            ("//Building[Radius='zzz'=Missing]", ["1", "2"]),
            ("//Building['zzz'=Radius=0]", ["1", "2"]),
            ("//Building[position()=last()='zzz']", ["1", "2"]),
            ("//Building[text()='zzz' = 0]", ["1", "2"]),
            ("//Building[contains(Radius,'9')=0]", ["1", "2"]),
            ("//Building[0=starts-with(Radius,'9')]", ["1", "2"]),
            ("//Building[starts-with(Radius,'9')='']", ["1", "2"]),
            ("//Building[contains(position(),'1')]", ["1", "2"]),
            ("//Trader[contains(Radius='zzz','f')]", ["3", "4"]),
        ):
            with self.subTest(path=path):
                self.assertEqual(self.by_parsing_every_asset(path), expected)
                self.assertEqual(sorted(self.index.find_matching([path])[path]), expected)
                self.assertEqual(index_mod.narrowing(path)[1], [])

    def test_nor_over_comparisons_compared_by_the_thousand(self):
        rng = random.Random(20261012)
        sides = ("Radius", "Type", "Good", "Missing", ".//Name", "text()", "position()", "last()",
                 "1", "2", "0", "'4'", "'10'", "'zzz'", "'Land'", "''", "'1'",
                 "contains(Radius,'1')", "contains(Type,'zzz')", "starts-with(.//Good,'9')",
                 "contains(position(),'1')", "Item[Good='10']/Amount", "Radius[.='4']",
                 "Radius | Type[.='zzz']", "contains(Radius[.='zzz'], Missing)",
                 "starts-with(Type, Radius[.='qq'])", "contains(Type, '')")
        paths = []
        for _ in range(2500):
            made = "=".join(rng.choice(sides) for _ in range(rng.choice((1, 2, 2, 3))))
            if rng.randrange(4) == 0:
                made = "%s %s %s" % (made, rng.choice(("and", "or")),
                                     "=".join(rng.choice(sides) for _ in range(2)))
            paths.append("//%s[%s]" % (rng.choice(("Building", "Trader", "Item", "Cost", "Standard")), made))
        paths = list(dict.fromkeys(paths))
        answered = self.index.find_matching(paths)
        narrowed = 0
        for path in paths:
            expected = self.by_parsing_every_asset(path)
            if expected is None:
                continue
            with self.subTest(path=path):
                self.assertEqual(sorted(answered.get(path, [])), sorted(expected),
                                 index_mod.narrowing(path))
            narrowed += bool(index_mod.narrowing(path)[1])
        self.assertGreater(len(paths), 1500)
        self.assertGreater(narrowed, 100)

    def test_it_still_narrows_the_searches_mods_make(self):
        """Sound by doing nothing would pass the test above, and a sweep of a
        hundred thousand assets would parse every one."""
        for path, needed, one_of in (
            ("//Asset[Template='Factory']/Values/Cost", {b"Cost"}, [b"Factory"]),
            ("//Values[Building]/Building/Radius", {b"Building", b"Radius"}, []),
            ("//Asset[Values/Standard/GUID='1' or Values/Standard/GUID='2']", set(), [b"1", b"2"]),
            ("//Values/Standard[IconFilename='data/ui/x.png']", set(), [b"data/ui/x.png"]),
            ("//Item[contains(Good,'10')]", {b"Item"}, [b"10"]),
            ("//Item[Good='10']/Amount[.='5']", {b"Item", b"Amount"}, [b"10", b"5"]),
            ("//Trader['Water'=Type/text()]", {b"Trader"}, [b"Water"]),
            ("//Cost/Item[position()=1][Good='10']", {b"Cost", b"Item"}, [b"10"]),
            ("//Trader[starts-with(./Type,'Wat') and @x='y']", {b"Trader"}, [b"Wat", b"y"]),
        ):
            with self.subTest(path=path):
                self.assertEqual(index_mod.narrowing(path), (frozenset(needed), one_of))


# The file as the game has it: groups inside groups, each asset in an
# <Assets> of its own group.  Asset 4 holds a field named as the file's own
# elements are, and asset 5 a <Group>, which a path can also mean.
def _group(name: str, assets: str, inner: str = "") -> str:
    return "<Group><Name>%s</Name><Assets>%s</Assets>%s</Group>" % (
        name, assets, "<Groups>%s</Groups>" % inner if inner else "")


NESTED = "<AssetList><Groups>%s%s</Groups></AssetList>" % (
    _group(
        "top", _asset(1, "T", "one", "<Building/>") + _asset(2, "U", "two", "<Trader/>"),
        _group(
            "in", _asset(3, "U", "three", "<Building/>")
            + _asset(4, "T", "four", "<Trader/><Unlock><Assets><Asset>9</Asset></Assets></Unlock>"),
            _group("deep", _asset(5, "U", "five", "<Trader><Group><Asset>9</Asset></Group></Trader>")),
        ),
    ),
    _group("other", _asset(6, "T", "six", "<Building/>")),
)

ABOVE = ("AssetList", "Groups", "Group", "Assets")
TAILS = ("Asset", "Asset[Template='U']", "Asset/Values/Trader", "Asset[.='9']", "Asset/Values/Building",
         "Asset/Template/text()", "Values/Trader", "*")


def a_path_from_above(rng: random.Random) -> str:
    steps = [rng.choice(ABOVE) for _ in range(rng.randrange(1, 5))] + [rng.choice(TAILS)]
    return "".join(rng.choice(("/", "//")) + step for step in steps)


PIECES = ("Template='U'", "Values/Trader", "not(Values/Trader)", "true()", "false()", "1", "0", "2",
          "count(Values/*)", "string-length()", "number()", "position()", "last()", "//Unlock",
          "/AssetList//GUID", "Template", ".", "'x'", "Values/Standard/GUID", "*", "text()",
          "boolean(Values/Building)", "contains(Template,'U')", "string(Template)", "sum(.//GUID)",
          "Values/*[1]", "Values/*[last()]", "..", "ancestor::Group", "self::Asset", "id('x')",
          "floor(1.5)", "name()", "local-name()='Asset'", "/", "//*", ".5", "Values[Trader]")
JOINS = ("+", "-", "*", "div", "mod", "=", "!=", "<", ">", "<=", ">=", "and", "or", "|")
HEADS = ("//Assets/Asset", "/AssetList//Asset", "//Group/Assets/Asset", "//Assets/*", "//Group//Asset",
         "/AssetList//*", "//Assets//Asset", "//Groups//Assets/Asset")


def _expression(rng: random.Random, depth: int = 0) -> str:
    kind = rng.randrange(10 if depth < 3 else 5)
    if kind < 5:
        return rng.choice(PIECES)
    if kind == 5:
        return "(%s)" % _expression(rng, depth + 1)
    if kind == 6:
        return "-%s%s" % (rng.choice(("", " ")), _expression(rng, depth + 1))
    if kind == 7:
        return "%s(%s)" % (rng.choice(("not", "boolean", "number", "count", "string", "string-length", "round")),
                           _expression(rng, depth + 1))
    # With a space or without: ``0-//Unlock``, ``Template and/AssetList``.
    return "%s%s%s%s%s" % (_expression(rng, depth + 1), rng.choice(("", " ")), rng.choice(JOINS),
                           rng.choice(("", " ")), _expression(rng, depth + 1))


def a_path_with_a_predicate(rng: random.Random) -> str:
    head, kind = rng.choice(HEADS), rng.randrange(6)
    tail = rng.choice(("", "", "", "/Values/Trader", "/Values/*", "//GUID"))
    if kind == 0:
        return "%s[%s][%s]%s" % (head, _expression(rng), _expression(rng), tail)
    if kind == 1:
        return "%s/Values/*[%s]" % (head, _expression(rng))
    if kind == 2:
        return "%s%s%s%s" % (head, rng.choice(("", " ")), rng.choice((*JOINS, "/", "//")), _expression(rng))
    return "%s[%s]%s" % (head, _expression(rng), tail)


class TestAPathNamingWhatAnAssetIsIn(unittest.TestCase):
    """``//Assets/Asset[...]`` is asked of the file, and each asset is kept
    and searched on its own, where nothing is above it (A8-12)."""

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        assets = Path(cls._tmp.name) / "assets.xml"
        assets.write_text(NESTED, encoding="utf-8")
        db = Path(cls._tmp.name) / "index.sqlite"
        assert index_mod.build_index(assets, db) == 6
        cls.index = index_mod.AssetIndex(db, assets)
        cls.whole = etree.fromstring(NESTED.encode("utf-8"))

    @classmethod
    def tearDownClass(cls):
        cls.index.close()
        cls._tmp.cleanup()

    def over_the_whole_file(self, path: str):
        """The assets ``path`` finds something in, asked of the file."""
        found = set()
        for node in self.whole.xpath(path):
            if not isinstance(getattr(node, "tag", None), str):
                node = node.getparent()
            asset = None
            while node is not None:
                if node.tag == "Asset":
                    asset = node
                node = node.getparent()
            if asset is not None:
                found.add(asset.findtext("Values/Standard/GUID"))
        return sorted(found)

    def one_by_one(self, path: str):
        return [guid for guid in "123456"
                if modops.matches_anything(self.index.asset_element(guid), path)]

    def test_the_paths_mods_write(self):
        for path, expected in (
            ("//Assets/Asset/Values/Trader", ["2", "4", "5"]),
            ("//Group/Assets/Asset[Template='U']", ["2", "3", "5"]),
            ("/AssetList//Asset[Template='U']", ["2", "3", "5"]),
            ("//Groups/Group/Assets/Asset/Values/Building", ["1", "3", "6"]),
            ("/AssetList/Groups//Assets/Asset[Template='T']", ["1", "4", "6"]),
            # The field in asset 4 is an Asset in an Assets too, and the one
            # in asset 5 an Asset somewhere under a Group.
            ("//Assets/Asset[.='9']", ["4"]),
            ("/AssetList//Group//Asset[.='9']", ["4", "5"]),
            ("//Group/Asset", ["5"]),
            # Nothing of an asset: the file's own elements, or no such place.
            ("//Group/Assets", []),
            ("/AssetList/Asset", []),
        ):
            with self.subTest(path=path):
                self.assertEqual(self.over_the_whole_file(path), expected)
                self.assertEqual(sorted(self.index.find_matching([path])[path]), expected)
                self.assertEqual(self.one_by_one(path), expected)

    def test_the_file_answers_the_same_wherever_it_is_answered(self):
        """A path is answered as the file answers it or left as written, which
        it is where the answer turns on how deep an asset's group is: the
        cache does not keep that."""
        rng = random.Random(20261012)
        paths = list(dict.fromkeys(a_path_from_above(rng) for _ in range(1500)))
        answered = self.index.find_matching(paths)
        as_the_file = 0
        for path in paths:
            expected = self.over_the_whole_file(path)
            left = index_mod.in_one_asset(path) == [path]
            with self.subTest(path=path):
                self.assertEqual(sorted(answered[path]), self.one_by_one(path))
                if not left:
                    self.assertEqual(sorted(answered[path]), expected)
            as_the_file += bool(expected) and not left
        # Most paths made of these names are no place in the file at all.
        self.assertGreater(as_the_file, 80)

    def test_one_that_turns_on_how_deep_the_group_is_is_left_as_written(self):
        path = "/AssetList/Groups/Group/Assets/Asset"
        self.assertEqual(self.over_the_whole_file(path), ["1", "2", "6"])
        self.assertEqual(index_mod.in_one_asset(path), [path])
        self.assertEqual(self.index.find_matching([path])[path], [])

    def test_one_counting_the_assets_place_is_left_as_written(self):
        """``//Assets/Asset[1]`` is the first asset of each group.  Read alone
        every asset is the first, and the last, and has none before it: asked
        of the asset that way the path matched them all, and an op with it
        was carried out on every one."""
        for path, expected in (
            ("//Assets/Asset[1]", ["1", "3", "4", "5", "6"]),
            ("//Assets/Asset[last()]", ["2", "4", "5", "6"]),
            ("//Assets/Asset[position() = 2]", ["2", "4"]),
            ("//Assets/Asset[Template='U'][1]", ["2", "3", "5"]),
            # A number is a place: the second of its group, with two values.
            ("//Assets/Asset[count(Values/*)]", ["2"]),
            ("//Assets/Asset[not(preceding-sibling::Asset)]", ["1", "3", "4", "5", "6"]),
            ("//Assets/Asset[../../Name='in']", ["3", "4"]),
            ("/AssetList//Asset[1]", ["1", "3", "4", "5", "6"]),
            ("/AssetList//*[2]/Values/Trader", ["2", "4"]),
            ("//Group/Assets/Asset[2]/Values/Trader", ["2", "4"]),
            # A number made of what is true or false is a place as well...
            ("//Assets/Asset[true()+0]", ["1", "3", "4", "5", "6"]),
            ("//Assets/Asset[1 + (Template='U')]", ["1", "2", "4", "6"]),
            ("//Assets/Asset[(Template!='U') * 1]", ["1", "6"]),
            ("//Assets/Asset[not(Values/Trader) + 1]", ["5"]),
            ("//Assets/Asset[count(Values/*) - (1=1)]", ["1", "3", "4", "5", "6"]),
            ("//Assets/Asset[-(Template='U') + 2]", ["3", "4", "5"]),
            ("//Assets/Asset[(Template='T') div 1]", ["1", "6"]),
            ("//Assets/Asset[2 mod (1 + (Template='U'))][Values]", []),
            ("//Assets/Asset[(1 + (Template='U'))]", ["1", "2", "4", "6"]),
            # ...and so is one a function comes to with nothing handed to it.
            ("//Assets/Asset[string-length()]", ["4"]),
            ("//Assets/Asset[string-length() - 3]", ["2"]),
            ("//Assets/Asset[number()]", []),
            ("//Assets/Asset[round(1.2)]", ["1", "3", "4", "5", "6"]),
            ("/AssetList//*[string-length(Template)]/Values/Trader", ["5"]),
        ):
            with self.subTest(path=path):
                self.assertEqual(self.over_the_whole_file(path), expected)
                self.assertEqual(index_mod.in_one_asset(path), [path])
                swept = sorted(self.index.find_matching([path])[path])
                self.assertEqual(swept, self.one_by_one(path))
                # Short of the file's answer it may be; past it, never.
                self.assertLessEqual(set(swept), set(expected))

    def test_one_looking_out_of_the_asset_is_left_as_written(self):
        """Anywhere below the asset a path can look back out of it, at what is
        above it or at the whole file, and read alone the asset has neither:
        ``[not(ancestor::Group)]`` and ``[not(//Unlock)]`` were true of every
        asset.  And a step to the asset that is not its name can stand
        somewhere else in a document that is all asset."""
        for path, expected in (
            ("//Assets/Asset/Values/Standard[not(ancestor::Group)]", []),
            ("//Assets/Asset/Values/Standard[not(../../../../Name)]", []),
            ("//Assets/Asset/Values/Standard[../../../../Name='in']", ["3", "4"]),
            ("//Assets/Asset/Values[not(parent::Asset/preceding-sibling::Asset)]/Trader", ["5"]),
            # Asset 4 has an unlock: the file has one, wherever it is asked.
            ("//Assets/Asset[not(//Unlock)]", []),
            ("//Assets/Asset/Values/Trader[count(//Trader) = 1]", []),
            ("//Assets/Asset[Values/Standard/GUID = /AssetList/Groups/Group/Assets/Asset[1]//GUID]",
             ["1", "6"]),
            ("/AssetList//Asset[Values/Trader and not( //Building )]", []),
            # Back out of the asset and in again, to the first of each group.
            ("//Assets/Asset/../Asset[1]", ["1", "3", "4", "5", "6"]),
            ("//Assets/Asset/parent::Assets/Asset[1]", ["1", "3", "4", "5", "6"]),
            # A step between <Assets> and the asset that is not a name.
            ("//Assets/./Asset[1]", ["1", "3", "4", "5", "6"]),
            ("//Assets/self::node()/Asset[1]", ["1", "3", "4", "5", "6"]),
            ("//Assets/descendant::Asset[1]", ["1", "3", "4", "5", "6"]),
            ("//Assets//descendant::Asset[1]", ["1", "3", "4", "5", "6"]),
            ("//Assets/child::Asset[1]", ["1", "3", "4", "5", "6"]),
            # A path from the top of the file after a minus, a number, a
            # star, an operator's word: whatever a slash does not carry on.
            ("//Assets/Asset[not(0-//Unlock)]", []),
            ("//Assets/Asset[-/AssetList//GUID = -1]", ["1", "2", "3", "4", "5", "6"]),
            ("//Assets/Asset[not(2*//Unlock)]", []),
            ("//Assets/Asset[not(1 div//Unlock)]", []),
            ("//Assets/Asset[Template and//Unlock]", ["1", "2", "3", "4", "5", "6"]),
            ("//Assets/Asset[Values/Trader][not(Values/Standard/GUID*//Unlock)]", []),
            ("//Assets/Asset[not(count(/AssetList/Nope) = 0)]", []),
            ("//Assets/Asset[not(id('x'))]", ["1", "2", "3", "4", "5", "6"]),
        ):
            with self.subTest(path=path):
                self.assertEqual(self.over_the_whole_file(path), expected)
                self.assertEqual(index_mod.in_one_asset(path), [path])
                swept = sorted(self.index.find_matching([path])[path])
                self.assertEqual(swept, self.one_by_one(path))
                self.assertLessEqual(set(swept), set(expected))

    def test_a_predicate_the_asset_answers_alone_is_still_asked_of_it(self):
        for path, expected in (
            ("//Assets/Asset[Values/Trader]", ["2", "4", "5"]),
            # The field in asset 4 is an Asset in an Assets with no trader too.
            ("//Assets/Asset[not(Values/Trader)]", ["1", "3", "4", "6"]),
            ("//Assets/Asset[Template='U' or Values/Building]", ["1", "2", "3", "5", "6"]),
            ("//Assets/Asset[contains(Template, 'U')]", ["2", "3", "5"]),
            ("//Assets/Asset[count(Values/*) = 2]", ["1", "2", "3", "5", "6"]),
            ("//Assets/Asset[Values/Standard[1]/GUID > 4]", ["5", "6"]),
            ("//Assets/Asset[Values/Standard/Name='last() of [1]']", []),
            # A place counted inside the asset is the same read alone.
            ("//Assets/Asset/Values/*[1]", ["1", "2", "3", "4", "5", "6"]),
            ("/AssetList//Values/*[2][self::Trader]", ["2", "4", "5"]),
        ):
            with self.subTest(path=path):
                self.assertEqual(self.over_the_whole_file(path), expected)
                self.assertNotEqual(index_mod.in_one_asset(path), [path])
                self.assertEqual(sorted(self.index.find_matching([path])[path]), expected)
                self.assertEqual(self.one_by_one(path), expected)

    def test_one_that_is_no_set_of_nodes_is_left_as_written(self):
        """``//Assets/Asset/Values/Standard/GUID*1`` is a number, and the
        file's is not nothing: asked of each asset from its top it was every
        asset's own, and the sweep said the path matched them all."""
        for path in (
            "//Assets/Asset/Values/Standard/GUID*1",
            "//Assets/Asset/Values/Standard/GUID -0",
            "//Assets/Asset/Values/Standard/GUID div 1",
            "//Assets/Asset/Template = 'U'",
            "//Assets/Asset/Values/count(*)",
        ):
            with self.subTest(path=path):
                self.assertEqual(index_mod.in_one_asset(path), [path])
                swept = sorted(self.index.find_matching([path]).get(path, []))
                self.assertEqual(swept, [])
                self.assertEqual(self.one_by_one(path), [])

    def test_a_predicate_made_any_way_is_never_answered_past_the_file(self):
        """A predicate on the asset's own step made of everything the
        language has, by the thousand: rewritten, the path finds what the
        file finds; left, the sweep and one asset asked alone still agree."""
        rng = random.Random(20261013)
        paths = list(dict.fromkeys(a_path_with_a_predicate(rng) for _ in range(6000)))
        answered = self.index.find_matching(paths)
        rewritten = left_alone = 0
        for path in paths:
            try:
                expected = self.over_the_whole_file(path)
            except (etree.XPathError, AttributeError, TypeError):
                # The file cannot answer it, or answers with no set of nodes.
                continue
            left = index_mod.in_one_asset(path) == [path]
            swept = sorted(answered.get(path, []))
            with self.subTest(path=path):
                self.assertEqual(swept, self.one_by_one(path))
                if not left:
                    self.assertEqual(swept, expected)
            rewritten += not left
            left_alone += left
        self.assertGreater(rewritten, 500)
        self.assertGreater(left_alone, 500)

    def test_an_expression_is_not_narrowed_by_its_names(self):
        """``//Missing or 1`` holds of an asset with no ``<Missing>`` in it:
        turned away by the name, an asset the sweep said did not match was
        one that, asked alone, did (A8-13)."""
        for path, expected in (
            ("//Missing or 1", ["1", "2", "3", "4", "5", "6"]),
            ("//Unlock or //Trader", ["2", "4", "5"]),
            ("count(//Missing[Name='zzz']) = 0", ["1", "2", "3", "4", "5", "6"]),
            ("//Missing[Name='zzz'] = 'zzz' or //Building", ["1", "3", "6"]),
            ("//Unlock | //Missing", ["4"]),
            ("(//Unlock | //Trader)[Assets]", ["4"]),
        ):
            with self.subTest(path=path):
                self.assertEqual(sorted(self.index.find_matching([path])[path]), expected)
                self.assertEqual(self.one_by_one(path), expected)
        self.assertEqual(index_mod.narrowing("//Missing or 1"), (frozenset(), []))
        self.assertEqual(index_mod.narrowing("//Unlock/Assets[Asset='9']"),
                         (frozenset({b"Unlock", b"Assets"}), [b"9"]))

    def test_a_literal_set_against_no_text_is_not_asked_for(self):
        """``[(GUID = GUID) = 'zzz']`` sets the string against a yes or no,
        and ``[GUID < 3 = 'zzz']`` too: no asset has to have it written."""
        for path, expected in (
            ("//Values/Standard[(GUID = GUID) = 'zzz']", ["1", "2", "3", "4", "5", "6"]),
            ("//Values/Standard[GUID < 3 = 'zzz']", ["1", "2"]),
            ("//Assets//Asset/Values/*[- Values/Trader <= . ='x' div - 1]", ["1", "2", "3", "4", "5", "6"]),
        ):
            with self.subTest(path=path):
                self.assertEqual(self.over_the_whole_file(path), expected)
                self.assertEqual(sorted(self.index.find_matching([path])[path]), expected)
                self.assertEqual(self.one_by_one(path), expected)
        # The literals mods write are still what an asset is turned away by.
        self.assertEqual(index_mod.narrowing("//Values/Standard[Name = 'six' or GUID = '2']"),
                         (frozenset(), [b"2", b"six"]))
        self.assertEqual(index_mod.narrowing("//Values[Trader]/Standard[contains(Name, 'iv')]"),
                         (frozenset(), [b"iv"]))

    def test_a_path_is_read_as_the_language_reads_it(self):
        for path, expected in (
            ("a * b", "a|*!|b"),
            ("a/*", "a|/!|*"),
            ("* * *", "*|*!|*"),
            ("a and/b", "a|and!|/!|b"),
            ("and and and", "and|and!|and"),
            ("2 div//c - -1", "2|div!|//!|c|-!|-!|1"),
            ("a-1", "a-1"),
            ("a - 1", "a|-!|1"),
            ("ancestor-or-self::x[@y!='] [']", "ancestor-or-self|::|x|[|@|y|!=!|'] ['|]"),
            ("ns:a/ns:*[.5 < $v]", "ns:a|/!|ns:*|[|.5|<!|$v|]"),
        ):
            with self.subTest(path=path):
                self.assertEqual(
                    "|".join(written + "!" * (kind == "operator")
                             for kind, written in index_mod._tokens(path)),
                    expected)
        # Two things with nothing joining them, and what is no path at all.
        for path in ("a andb", "1 or-b", "a b", "a # b", "'open"):
            with self.subTest(path=path):
                self.assertIsNone(index_mod._tokens(path))

    def test_a_path_naming_nothing_above_an_asset_is_as_it_was(self):
        for path in ("//Asset[Template='U']", "//Values/Trader", "//Trader/Group/Asset",
                     "Values/Trader", "//Asset[Template='U'] | //Assets/Asset"):
            with self.subTest(path=path):
                self.assertEqual(index_mod.in_one_asset(path), [path])


if __name__ == "__main__":
    unittest.main()
