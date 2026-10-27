"""Mod options in both games: iModYourAnno's tweaks for Anno 1800, and Anno
117's active-options.jsonc.

iModYourAnno writes a player's answers into the mod's own files, so the tests
are about files: what a folder says it is set to, what the export writes, that
writing it twice changes nothing, and that an update carries the answers into
the author's fresh copy rather than losing them.  The export was checked
against iModYourAnno's own output on two real tweaked mods (130 generated
files, 132 originals, all equal); these pin the behaviour down on a small one.
"""

from __future__ import annotations

import io
import json
import os
import shutil
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

import _isolation  # noqa: F401 -- first: keeps the suite out of real files under any runner

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from lxml import etree  # noqa: E402
from support import (  # noqa: E402
    IMYA_PATCH,
    IMYA_RADIUS,
    IMYA_TWEAKABLE,
    MODINFO_WITH_OPTIONS,
    SessionCase,
)

from anno_modding_studio.core import (  # noqa: E402
    imya,
    library,
    modfolder,
    modio_cache,
    modops,
    places,
    reader,
    settings,
    switches,
)
from anno_modding_studio.core.games import ANNO_117, ANNO_1800  # noqa: E402

# A mod the way an iModYourAnno author writes one: a slider on one op, a
# switch on a group, a list on another op, and a comment of the author's own.

WIDEN = IMYA_PATCH + imya.SEPARATOR + "Widen beta"
TYPE = IMYA_PATCH + imya.SEPARATOR + "Alpha type"


def _mod(folder: Path, name: str = "Tweakable", body: str = IMYA_TWEAKABLE) -> Path:
    mod = folder / name
    patch = mod / IMYA_PATCH
    patch.parent.mkdir(parents=True)
    patch.write_text(body, encoding="utf-8")
    (mod / "modinfo.json").write_text(json.dumps({"ModID": "tweakable"}), encoding="utf-8")
    return mod


def _generated(mod: Path) -> Path:
    return (mod / IMYA_PATCH).parent / ("assets" + imya.TWEAK_SUFFIX)


class _Folder(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)


class TestWhatAModOffers(_Folder):
    """An ``<ImyaExpose>`` read the way iModYourAnno reads it."""

    def setUp(self):
        super().setUp()
        self.mod = _mod(self.root)
        self.tweaks = imya.read(self.mod)

    def test_a_mod_with_no_exposes_offers_nothing(self):
        plain = _mod(self.root, "Plain", "<ModOps><ModOp Type='merge' GUID='1'/></ModOps>")
        self.assertIsNone(imya.read(plain))

    def test_every_knob_is_read_with_its_kind(self):
        kinds = {expose.expose_id: expose.kind for expose in self.tweaks.exposes}
        self.assertEqual(
            kinds, {"Alpha radius": imya.SLIDER, "Widen beta": imya.SKIP_TOGGLE, "Alpha type": imya.ENUM}
        )

    def test_the_author_s_defaults_are_what_the_ops_say(self):
        self.assertEqual(
            self.tweaks.defaults(), {IMYA_RADIUS: "20", WIDEN: "true", TYPE: "Factory"}
        )

    def test_a_switch_is_off_by_any_skip_as_the_game_reads_it(self):
        for value in ("1", "0", ""):
            with self.subTest(skip=value):
                body = IMYA_TWEAKABLE.replace(
                    '<Group ModOpID="BetaGroup">', '<Group ModOpID="BetaGroup" Skip="%s">' % value
                )
                tweaks = imya.read(_mod(self.root, "Off %s" % (value or "empty"), body))
                self.assertEqual(tweaks.current[WIDEN], "false")

    def test_a_folder_never_tweaked_is_at_its_defaults(self):
        self.assertEqual(self.tweaks.current, self.tweaks.defaults())
        self.assertEqual(self.tweaks.chosen(), {})

    def test_they_are_offered_as_mod_options_under_the_file_s_title(self):
        offered = self.tweaks.options()
        self.assertTrue(offered.options[0].is_heading)
        self.assertEqual(offered.options[0].label, "Radius tweaks")
        radius = offered.get(IMYA_RADIUS)
        self.assertEqual((radius.kind, radius.range), (settings.SLIDER, (10.0, 40.0, 5.0)))
        self.assertEqual(offered.get(WIDEN).kind, settings.TOGGLE)
        self.assertEqual(offered.get(TYPE).values, ("Factory", "Farm"))

    def test_a_kind_whose_definition_is_missing_is_a_plain_value(self):
        body = IMYA_TWEAKABLE.replace('<SliderDefinition Min="10" Max="40" Stepping="5" />', "")
        tweaks = imya.read(_mod(self.root, "No definition", body))
        self.assertEqual(tweaks.get(IMYA_RADIUS).kind, imya.SIMPLE)
        self.assertEqual(tweaks.get(IMYA_RADIUS).option().kind, settings.TEXT)


class TestWritingThem(_Folder):
    """The files iModYourAnno would write, written."""

    def setUp(self):
        super().setUp()
        self.mod = _mod(self.root)

    def ops(self):
        root = etree.parse(str(self.mod / IMYA_PATCH)).getroot()
        return {
            node.get("ModOpID"): node.get("Skip")
            for node in root.xpath(imya._MODOP_NODES) if node.get("ModOpID")
        }

    def test_the_originals_are_switched_off_and_the_copies_carry_the_answer(self):
        imya.write(self.mod, {IMYA_RADIUS: "30", WIDEN: "false"})
        self.assertEqual(self.ops(), {"AlphaRadius": "1", "BetaGroup": "1", "AlphaType": "1"})
        generated = etree.parse(str(_generated(self.mod))).getroot()
        radius = generated.xpath("ModOp[@GUID='1000' and @Path='/Values/Building/Radius']/Radius")
        self.assertEqual(radius[0].text, "30")
        # Path, GUID and Type last, and neither the ID nor the switch, as
        # iModYourAnno writes a copy.
        self.assertEqual(list(generated[0].attrib), ["Path", "GUID", "Type"])
        included = etree.parse(str(self.mod / IMYA_PATCH)).getroot().findall("Include")
        self.assertEqual([node.get("File") for node in included], ["./assets" + imya.TWEAK_SUFFIX])

    def test_a_switch_left_on_leaves_its_group_running(self):
        imya.write(self.mod, {IMYA_RADIUS: "30"})
        self.assertIsNone(self.ops()["BetaGroup"])

    def test_what_was_written_reads_back_as_what_was_asked(self):
        imya.write(self.mod, {IMYA_RADIUS: "35", WIDEN: "false", TYPE: "Farm"})
        self.assertEqual(
            imya.read(self.mod).chosen(), {IMYA_RADIUS: "35", WIDEN: "false", TYPE: "Farm"}
        )

    def test_writing_the_same_answers_twice_changes_nothing(self):
        imya.write(self.mod, {IMYA_RADIUS: "30", WIDEN: "false"})
        self.assertEqual(imya.write(self.mod, {IMYA_RADIUS: "30", WIDEN: "false"}), [])

    def test_and_the_include_is_not_added_twice(self):
        imya.write(self.mod, {IMYA_RADIUS: "30"})
        imya.write(self.mod, {IMYA_RADIUS: "25"})
        root = etree.parse(str(self.mod / IMYA_PATCH)).getroot()
        self.assertEqual(len(root.findall("Include")), 1)

    def test_the_author_s_own_layout_and_comments_are_left_alone(self):
        imya.write(self.mod, {IMYA_RADIUS: "30"})
        text = (self.mod / IMYA_PATCH).read_text(encoding="utf-8")
        self.assertIn("<!-- the author's own note, which must survive being tweaked -->", text)
        # Indentation kept; lxml spells an empty element <X/> rather than <X />.
        self.assertIn('\n    <SliderDefinition Min="10" Max="40" Stepping="5"', text)

    def test_render_writes_nothing(self):
        before = (self.mod / IMYA_PATCH).read_bytes()
        rendered = imya.render(self.mod, imya.read(self.mod), {IMYA_RADIUS: "40"})
        self.assertEqual((self.mod / IMYA_PATCH).read_bytes(), before)
        self.assertFalse(_generated(self.mod).exists())
        self.assertIn(_generated(self.mod).resolve(), rendered)


INCLUDED = "data/config/export/main/asset/needs.include.xml"

# The toggles of three census mods, in small.  "Whole op": a Toggle on the op's
# own body (Path=".") whose AltValue is empty, so off removes the body, as
# *PC Time as Ingame Time* has it.  "Need": a Toggle whose AltValue the path no
# longer selects (its Product is 0), as *Subway Network* has it, in a file that
# is itself an include.  "Bonus" and "Bonus kept": a Toggle that is not the
# first knob on its op, plain and inverted, so its answer is only in the copy.
# "Inverted need": an inverted Toggle, read off its Skip flag.  "Oil": a knob
# whose op's ID has a stray space, as *Powerplants* has it, so it names no op.
IMYA_TOGGLES = """<ModOps>
  <ModOp Type="addNextSibling" GUID="130248" ModOpID="WholeOp">
    <Asset><Values><Standard><GUID>1999</GUID></Standard></Values></Asset>
  </ModOp>
  <ImyaExpose Path="." ModOpID="WholeOp" ExposeID="Whole op" Kind="Toggle">
    <AltValue>
    </AltValue>
  </ImyaExpose>
  <ModOp Type="replace" GUID="15000001" Path="/Values/Inputs/Item[Product='7']" ModOpID="Need">
    <Item><Product>7</Product><Amount>6</Amount></Item>
  </ModOp>
  <ImyaExpose Path="self::Item[Product='7']" ModOpID="Need" ExposeID="Need" Kind="Toggle">
    <AltValue><Item><Product>0</Product><Amount>0</Amount></Item></AltValue>
  </ImyaExpose>
  <ModOp Type="replace" GUID="15000002" Path="/Values/Inputs/Item[Product='7']" ModOpID="InvertedNeed">
    <Item><Product>7</Product><Amount>6</Amount></Item>
  </ModOp>
  <ImyaExpose Path="self::Item[Product='7']" ModOpID="InvertedNeed" ExposeID="Inverted need" Kind="Toggle" Invert="True">
    <AltValue><Item><Product>0</Product><Amount>0</Amount></Item></AltValue>
  </ImyaExpose>
  <ModOp Type="merge" GUID="15000003" Path="/" ModOpID="Shared">
    <Values>
      <Amount>6</Amount>
      <Bonus><Product>7</Product></Bonus>
      <Kept><Product>7</Product></Kept>
    </Values>
  </ModOp>
  <ImyaExpose Path="Amount" ModOpID="Shared" ExposeID="Amount" Kind="Slider">
    <SliderDefinition Min="0" Max="10" Stepping="1" />
  </ImyaExpose>
  <ImyaExpose Path="Bonus[Product='7']" ModOpID="Shared" ExposeID="Bonus" Kind="Toggle">
    <AltValue><Bonus><Product>0</Product></Bonus></AltValue>
  </ImyaExpose>
  <ImyaExpose Path="Kept[Product='7']" ModOpID="Shared" ExposeID="Bonus kept" Kind="Toggle" Invert="True">
    <AltValue><Kept><Product>0</Product></Kept></AltValue>
  </ImyaExpose>
  <Group ModOpID=" Oil">
    <ModOp Type="merge" GUID="1002" Path="/Values"><Oil>1</Oil></ModOp>
  </Group>
  <ImyaExpose Path="*" ModOpID="Oil" ExposeID="Oil" Kind="SkipToggle" InitState="True" />
</ModOps>
"""


def _toggle(expose_id: str) -> str:
    return INCLUDED + imya.SEPARATOR + expose_id


class TestTogglesReadBack(_Folder):
    """A toggle written either way reads back as written, whatever its AltValue
    leaves at the path -- the census found five that did not."""

    TOGGLES = ("Whole op", "Need", "Inverted need", "Bonus", "Bonus kept")

    def setUp(self):
        super().setUp()
        self.mod = self.root / "Toggles"
        patch = self.mod / INCLUDED
        patch.parent.mkdir(parents=True)
        patch.write_text(IMYA_TOGGLES, encoding="utf-8")
        (self.mod / "modinfo.json").write_text(json.dumps({"ModID": "toggles"}), encoding="utf-8")

    def written(self, answers):
        imya.write(self.mod, answers, imya.read(self.mod))
        return imya.read(self.mod)

    def test_each_toggle_reads_back_as_written_either_way(self):
        for expose_id in self.TOGGLES:
            for answer in ("false", "true", "false"):
                with self.subTest(toggle=expose_id, answer=answer):
                    tweaks = self.written({_toggle(expose_id): answer})
                    self.assertEqual(tweaks.current[_toggle(expose_id)], answer)
                    self.assertEqual(tweaks.unreadable, [])

    def test_all_off_at_once_reads_back_off(self):
        tweaks = self.written({_toggle(expose_id): "false" for expose_id in self.TOGGLES})
        self.assertEqual(
            {expose_id: tweaks.current[_toggle(expose_id)] for expose_id in self.TOGGLES},
            dict.fromkeys(self.TOGGLES, "false"),
        )

    def test_off_is_what_iModYourAnno_writes(self):
        """The write was right all along: off skips the original and puts the
        AltValue in the copy, or leaves the copy empty when it is not XML."""
        self.written({_toggle("Whole op"): "false", _toggle("Need"): "false"})
        made = (self.mod / INCLUDED).with_name("needs.include" + imya.TWEAK_SUFFIX)
        generated = etree.parse(str(made)).getroot()
        whole, need = generated[0], generated[1]
        self.assertEqual((whole.get("Type"), len(whole)), ("addNextSibling", 0))
        self.assertEqual(need.findtext("Item/Product"), "0")
        original = etree.parse(str(self.mod / INCLUDED)).getroot()
        self.assertEqual(original.xpath("ModOp[@ModOpID='Need']/@Skip"), ["1"])

    def test_a_folder_shipped_with_a_toggle_off_reads_off(self):
        """*Subway Network* ships tweaked, its needs off; that is what it is set to."""
        imya.write(self.mod, {_toggle("Need"): "false"})
        self.assertEqual(imya.read(self.mod).chosen(), {_toggle("Need"): "false"})

    def test_a_knob_that_names_no_op_is_not_offered(self):
        tweaks = imya.read(self.mod)
        self.assertIsNone(tweaks.get(_toggle("Oil")))
        self.assertNotIn(_toggle("Oil"), tweaks.current)

    def test_but_its_op_is_switched_off_as_iModYourAnno_switches_it(self):
        """No knob claims the " Oil" group, so like every other tagged op it is
        skipped -- for good, in both tools, whatever is set."""
        self.written({_toggle("Need"): "true"})
        original = etree.parse(str(self.mod / INCLUDED)).getroot()
        self.assertEqual(original.xpath("Group[@ModOpID=' Oil']/@Skip"), ["1"])


CHEFS = "data/config/export/main/asset/chefs.include.xml"

# Knobs whose answer the export ignores.  "Angela": a skip toggle second on
# Aaden's group, its ModOpID pasted from his, as *Trade Union Rebalance* has
# it -- only the first knob on an op decides its Skip flag.  "Chef": a slider
# on that group, which gets no copy to carry it.  "Bonus": a skip toggle second
# on a ModOp, which removes the node at its path from the copy either way.
# "Happiness": a slider whose path lands on nothing in its op, as *The perfect
# specialists & items pack* has it.
IMYA_INERT = """<ModOps>
  <Group ModOpID="Aaden">
    <ModOp Type="merge" GUID="1001" Path="/Values"><Chef>1</Chef></ModOp>
  </Group>
  <ImyaExpose Path="*" ModOpID="Aaden" ExposeID="Aaden" Kind="SkipToggle" InitState="True" />
  <ImyaExpose Path="*" ModOpID="Aaden" ExposeID="Angela" Kind="SkipToggle" InitState="True" />
  <ImyaExpose Path="Chef" ModOpID="Aaden" ExposeID="Chef" Kind="Slider">
    <SliderDefinition Min="0" Max="5" Stepping="1" />
  </ImyaExpose>
  <ModOp Type="merge" GUID="1002" Path="/" ModOpID="Shared">
    <Values><Amount>6</Amount><Bonus>1</Bonus></Values>
  </ModOp>
  <ImyaExpose Path="Amount" ModOpID="Shared" ExposeID="Amount" Kind="Slider">
    <SliderDefinition Min="0" Max="10" Stepping="1" />
  </ImyaExpose>
  <ImyaExpose Path="Bonus" ModOpID="Shared" ExposeID="Bonus" Kind="SkipToggle" InitState="True" />
  <ModOp Type="replace" GUID="1003" Path="/Values/Happiness" ModOpID="Happiness">
    <Happiness>5</Happiness>
  </ModOp>
  <ImyaExpose Path="self::AmountInPercent" ModOpID="Happiness" ExposeID="Happiness" Kind="Slider">
    <SliderDefinition Min="0" Max="100" Stepping="1" />
  </ImyaExpose>
</ModOps>
"""


def _chef(expose_id: str) -> str:
    return CHEFS + imya.SEPARATOR + expose_id


class TestKnobsThatChangeNothing(_Folder):
    """A knob iModYourAnno lets the player set but whose answer its export
    ignores is still applied, and is not offered."""

    INERT = ("Angela", "Chef", "Bonus", "Happiness")

    def setUp(self):
        super().setUp()
        self.mod = self.root / "Chefs"
        patch = self.mod / CHEFS
        patch.parent.mkdir(parents=True)
        patch.write_text(IMYA_INERT, encoding="utf-8")
        (self.mod / "modinfo.json").write_text(json.dumps({"ModID": "chefs"}), encoding="utf-8")
        self.tweaks = imya.read(self.mod)

    def test_they_are_read_but_not_offered(self):
        self.assertEqual(
            {expose.expose_id for expose in self.tweaks.exposes if expose.inert}, set(self.INERT)
        )
        offered = [option.name for option in self.tweaks.options().settable]
        self.assertEqual(offered, [_chef("Aaden"), _chef("Amount")])

    def test_their_answer_changes_nothing_written(self):
        for expose_id, one, two in (
            ("Angela", "true", "false"), ("Chef", "1", "4"), ("Bonus", "true", "false"),
            ("Happiness", "0", "50"),
        ):
            with self.subTest(knob=expose_id):
                self.assertEqual(
                    imya.render(self.mod, self.tweaks, {_chef(expose_id): one}),
                    imya.render(self.mod, self.tweaks, {_chef(expose_id): two}),
                )

    def test_the_first_switch_on_the_group_still_decides(self):
        imya.write(self.mod, {_chef("Aaden"): "false", _chef("Angela"): "true"}, self.tweaks)
        self.assertEqual(imya.read(self.mod).chosen(), {_chef("Aaden"): "false"})
        imya.write(self.mod, {_chef("Aaden"): "true", _chef("Angela"): "false"}, self.tweaks)
        self.assertEqual(imya.read(self.mod).chosen(), {})

    def test_but_a_skip_toggle_s_removal_is_still_written(self):
        imya.write(self.mod, {_chef("Amount"): "8"}, self.tweaks)
        made = (self.mod / CHEFS).with_name("chefs.include" + imya.TWEAK_SUFFIX)
        copy = etree.parse(str(made)).getroot()[0]
        self.assertEqual((copy.findtext("Values/Amount"), copy.find("Values/Bonus")), ("8", None))

    def test_an_answer_to_one_is_not_carried_across_an_update(self):
        before = imya.Snapshot(
            chosen={_chef("Angela"): "false", _chef("Amount"): "8"},
            labels={_chef("Angela"): "Angela", _chef("Amount"): "Amount"},
        )
        done = imya.carry(before, self.mod)
        self.assertEqual((done.kept, done.dropped), ({_chef("Amount"): "8"}, ["Angela"]))


class TestCarryingThemAcrossAnUpdate(_Folder):
    """An update installs the author's fresh folder, which knows nothing of the
    player's answers.  They are carried across, or said to be lost."""

    def setUp(self):
        super().setUp()
        self.old = _mod(self.root, "Old")
        imya.write(self.old, {IMYA_RADIUS: "35", TYPE: "Farm"})
        self.before = imya.snapshot(self.old)

    def test_the_snapshot_is_what_differs_from_the_defaults(self):
        self.assertEqual(self.before.chosen, {IMYA_RADIUS: "35", TYPE: "Farm"})

    def test_a_knob_the_new_version_still_offers_keeps_its_answer(self):
        new = _mod(self.root, "New")
        done = imya.carry(self.before, new)
        self.assertEqual(done.kept, {IMYA_RADIUS: "35", TYPE: "Farm"})
        self.assertEqual(imya.read(new).chosen(), {IMYA_RADIUS: "35", TYPE: "Farm"})

    def test_one_it_no_longer_offers_is_named_not_dropped_in_silence(self):
        body = IMYA_TWEAKABLE.replace('ExposeID="Alpha type"', 'ExposeID="Alpha kind"')
        done = imya.carry(self.before, _mod(self.root, "New", body))
        self.assertEqual(done.kept, {IMYA_RADIUS: "35"})
        self.assertEqual(done.dropped, ["Alpha type"])
        self.assertIn("no longer offers Alpha type", done.describe())

    def test_an_answer_that_is_no_longer_one_of_its_choices_is_named_too(self):
        body = IMYA_TWEAKABLE.replace("<Value>Farm</Value>", "<Value>Mine</Value>")
        done = imya.carry(self.before, _mod(self.root, "New", body))
        self.assertEqual(done.dropped, ["Alpha type"])

    def test_a_folder_at_its_defaults_has_nothing_to_carry(self):
        self.assertFalse(imya.snapshot(_mod(self.root, "Untouched")))

    def test_a_bundled_mod_s_knobs_are_its_own_but_travel_with_its_host(self):
        """*Extended Arctic* ships its pieces as mods inside it.  To the reading
        each is a mod of its own, so the host does not claim their knobs; an
        update replaces the host whole, so carrying takes them all."""
        host = self.root / "Host"
        (host / "data").mkdir(parents=True)
        (host / "modinfo.json").write_text("{}", encoding="utf-8")
        inner = _mod(host, "Piece")
        imya.write(inner, {IMYA_RADIUS: "40"})
        self.assertIsNone(imya.read(host))
        self.assertEqual(imya.read(inner).chosen(), {IMYA_RADIUS: "40"})
        before = imya.snapshot(host)
        self.assertEqual(before.chosen, {"Piece/" + IMYA_RADIUS: "40"})

        fresh = self.root / "Fresh"
        (fresh / "data").mkdir(parents=True)
        (fresh / "modinfo.json").write_text("{}", encoding="utf-8")
        _mod(fresh, "Piece")
        self.assertEqual(imya.carry(before, fresh).kept, {"Piece/" + IMYA_RADIUS: "40"})
        self.assertEqual(imya.read(fresh / "Piece").chosen(), {IMYA_RADIUS: "40"})


def _zip_of(folder: Path) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zipped:
        for path in folder.rglob("*"):
            if path.is_file():
                zipped.write(path, (Path(folder.name) / path.relative_to(folder)).as_posix())
    return buffer.getvalue()


@unittest.skipUnless(os.name == "nt", "junctions are a Windows thing")
class TestAnUpdateThroughTheLibrary(_Folder):
    """What the update check and a collection's replacing rows both go through."""

    def setUp(self):
        super().setUp()
        self.mods = self.root / "mods"
        self.mods.mkdir()
        self.library = self.root / "library" / "anno1800"
        for patched in (
            mock.patch.dict(os.environ, {
                modio_cache.ENV_ROOT: str(self.root / "modio"),
                library.ENV_LIBRARY: str(self.root / "library"),
            }),
            mock.patch.object(places, "backups_dir", return_value=self.root / "backups"),
        ):
            patched.start()
            self.addCleanup(patched.stop)
        fresh = _mod(self.root / "author", "Tweakable")
        self.archive = self.root / "update.zip"
        self.archive.write_bytes(_zip_of(fresh))

    def test_a_tweaked_folder_of_the_user_s_own_keeps_its_answers(self):
        installed = _mod(self.mods, "Tweakable")
        imya.write(installed, {IMYA_RADIUS: "35"})
        done = library.install(self.archive, self.library, self.mods, replace=installed)
        self.assertEqual(done.carried.kept, {IMYA_RADIUS: "35"})
        self.assertEqual(imya.read(self.mods / "Tweakable").chosen(), {IMYA_RADIUS: "35"})

    def test_and_so_does_a_library_copy_being_replaced(self):
        first = library.install(self.archive, self.library, self.mods)
        imya.write(first.placed[0], {WIDEN: "false"})
        done = library.install(self.archive, self.library, self.mods, replace=self.mods / "Tweakable")
        self.assertEqual(done.carried.kept, {WIDEN: "false"})
        self.assertIn("Kept 1 iModYourAnno setting", done.carried.describe())

    def test_a_folder_with_nothing_set_says_nothing(self):
        installed = _mod(self.mods, "Tweakable")
        done = library.install(self.archive, self.library, self.mods, replace=installed)
        self.assertFalse(done.carried)


class TestReadingAtTheirSettings(SessionCase):
    """The Studio reads a tweaked mod as the game runs it, and at other settings
    without touching the folder."""

    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root, True)
        self.mod = _mod(self.root)

    def radius(self, report) -> str:
        found = [
            change.new_value for change in report.changes
            if str(change.guid) == "1000" and change.path.endswith("/Radius")
        ]
        return found[-1] if found else ""

    def read(self, **kwargs):
        return reader.analyse(self.mod, self.index, origin="Tweakable", **kwargs)

    def test_the_options_come_across_with_the_mod(self):
        offered = self.read().metadata["options"]
        self.assertEqual(
            [option.name for option in offered.settable], [IMYA_RADIUS, WIDEN, TYPE]
        )

    def test_a_tweaked_folder_is_read_at_what_it_is_set_to(self):
        imya.write(self.mod, {IMYA_RADIUS: "35"})
        report = self.read()
        self.assertEqual(self.radius(report), "35")
        self.assertEqual(report.options_read, {IMYA_RADIUS: "35"})

    def test_other_settings_are_read_from_memory_and_the_folder_is_left_alone(self):
        before = (self.mod / IMYA_PATCH).read_bytes()
        report = self.read(reading=modops.Reading(options={"Tweakable": {IMYA_RADIUS: "40"}}))
        self.assertEqual(self.radius(report), "40")
        self.assertEqual((self.mod / IMYA_PATCH).read_bytes(), before)
        self.assertFalse(_generated(self.mod).exists())

    def test_a_whole_folder_read_takes_them_in_too(self):
        imya.write(self.mod, {IMYA_RADIUS: "25"})
        session = reader.analyse_all(modfolder.mod_folders(self.root), self.index, game=ANNO_1800)
        self.assertEqual(session.reads[0].report.options_read, {IMYA_RADIUS: "25"})


class TestTheGameOwnOptionsFile(unittest.TestCase):
    """Anno 117's active-options.jsonc: what the loader runs each mod at."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.file = Path(self._tmp.name) / switches.OPTIONS_FILE

    def test_it_is_read_by_mod_id_comments_and_all(self):
        self.file.write_text(
            '{\n  // written by a mod manager\n  "Powerful-Flagship": {\n'
            '    "num_flagship_scorpions": "4", "archers": 0, "on": true\n  },\n'
            '  "broken": "not a table"\n}\n',
            encoding="utf-8",
        )
        self.assertEqual(
            switches.game_options(ANNO_117, at=self.file),
            {"powerful-flagship": {"num_flagship_scorpions": "4", "archers": "0", "on": "true"}},
        )

    def test_no_file_is_nothing_set(self):
        self.assertEqual(switches.game_options(ANNO_117, at=self.file), {})

    def test_an_unreadable_file_is_nothing_set(self):
        self.file.write_text("{ not json", encoding="utf-8")
        self.assertEqual(switches.game_options(ANNO_117, at=self.file), {})

    def test_1800_has_no_such_file(self):
        self.assertIsNone(switches.options_file(ANNO_1800))


class TestReadingAtTheGameOwnOptions(SessionCase):
    """A 117 mod nobody has answered here is read at what the game runs."""

    GUARDED = (
        '<ModOp Merge="@1000/Building" Condition="$roomier">'
        "<Radius>30</Radius></ModOp>"
    )

    def setUp(self):
        self.root = self.mods(a__mod=self.GUARDED)
        info = json.loads(MODINFO_WITH_OPTIONS)
        info["ModID"] = "configurable-mod"
        (self.root / "a mod" / "modinfo.json").write_text(json.dumps(info), encoding="utf-8")

    def read(self, reading):
        return reader.analyse(self.root / "a mod", self.index, origin="a mod", reading=reading)

    def test_the_game_s_answer_is_used_when_nobody_has_answered_here(self):
        report = self.read(modops.Reading(game_options={"configurable-mod": {"roomier": "true"}}))
        self.assertEqual(report.results[0].status, modops.IMPORTED)
        self.assertEqual(report.options_read, {"roomier": "true"})

    def test_an_answer_given_here_wins(self):
        report = self.read(modops.Reading(
            options={"a mod": {"roomier": "false"}},
            game_options={"configurable-mod": {"roomier": "true"}},
        ))
        self.assertEqual(report.results[0].status, modops.SETTLED)

    def test_without_either_it_is_the_author_s_default(self):
        report = self.read(modops.Reading())
        self.assertEqual(report.results[0].status, modops.SETTLED)
        self.assertEqual(report.options_read, {})


class TestWhatAGroupIsCalled(unittest.TestCase):
    """A group iModYourAnno names is not a group that asks anything; it used to
    be listed as "a group of ops, only if" with nothing after the "if"."""

    def called(self, xml: str) -> str:
        root = etree.fromstring("<ModOps>%s</ModOps>" % xml)
        ops = modops.parse_ops(root, [], "assets.xml")
        return modops.describe_op(next(op for op in ops if op.scope))

    def test_a_named_group_is_called_by_its_name(self):
        self.assertEqual(self.called('<Group ModOpID="TradeUnion"><ModOp/></Group>'), "the group TradeUnion")

    def test_a_named_group_switched_off_says_so(self):
        self.assertEqual(
            self.called('<Group ModOpID="Angela" Skip="1"><ModOp/></Group>'),
            "the group Angela, switched off",
        )

    def test_a_condition_is_still_said(self):
        self.assertEqual(
            self.called('<Group Condition="#other-mod"><ModOp/></Group>'),
            "a group of ops, only if #other-mod",
        )
        self.assertEqual(
            self.called('<Group ModOpID="Pools" Condition="#other-mod"><ModOp/></Group>'),
            "the group Pools, only if #other-mod",
        )


NESTED = """<ModOps>
  <Group ModOpID="Outer">
    <Group ModOpID="Kept">
      <ModOp Type="replace" GUID="1000" Path="/Values/Building/Radius"><Radius>11</Radius></ModOp>
    </Group>
    <Group ModOpID="Off" Skip="1">
      <ModOp Type="replace" GUID="1001" Path="/Values/Building/Radius"><Radius>12</Radius></ModOp>
    </Group>
    <Group ModOpID="Lonely" Skip="1">
      <ModOp Type="replace" GUID="1001" Path="/Values/Building/Radius"><Radius>13</Radius></ModOp>
    </Group>
  </Group>
  <ImyaExpose Path="*" Kind="SkipToggle" InitState="True" ModOpID="Kept" ExposeID="Keep the radius" />
  <ImyaExpose Path="*" Kind="SkipToggle" InitState="True" ModOpID="Off" ExposeID="Switch the other" />
  <ImyaExpose Path="*" Kind="SkipToggle" InitState="True" ModOpID="Kept" ExposeID="Switch the lonely one" />
</ModOps>
"""


class TestGroupsInsideANamedGroup(SessionCase):
    """*Trade Union Rebalance* is one named group holding a named group per
    specialist -- iModYourAnno's switches."""

    def setUp(self):
        super().setUp()
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root, True)
        self.mod = _mod(self.root, body=NESTED)
        self.report = reader.analyse(self.mod, self.index, origin="Tweakable")

    def test_a_switched_off_group_inside_goes_out_once(self):
        """It used to go out twice -- inside its parent, and as an op of its
        own -- and switched on, iModYourAnno would have run it twice."""
        from anno_modding_studio.core import generator
        from anno_modding_studio.core.changes import Loadout, ModLayer

        loadout = Loadout()
        loadout.set_layer(ModLayer(origin="Tweakable", name="Tweakable"))
        for op in self.report.ops:
            loadout.set_op(op)
        for change in self.report.changes:
            loadout.set_change(change)
        xml = generator.build_modops_xml(
            generator.for_mod(loadout, "Tweakable")
        )
        self.assertEqual(xml.count('ModOpID="Off"'), 1)
        self.assertEqual(xml.count('ModOpID="Outer"'), 1)


class TestTheSameSharedModInTwoHosts(SessionCase):
    """Two mods each ship a copy of one [Shared] piece; the game loads one.  The
    host whose copy is passed over can still say it carries it."""

    def test_the_passed_over_copy_points_at_the_one_that_loads(self):
        root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, root, True)
        for host, version in (("Alpha Host", "1.0"), ("Beta Host", "2.0")):
            (root / host).mkdir()
            (root / host / "modinfo.json").write_text('{"ModID": "%s"}' % host.lower().replace(" ", "-"), encoding="utf-8")
            patch = root / host / "shared" / "data/config/export/main/asset/assets.xml"
            patch.parent.mkdir(parents=True)
            patch.write_text(
                '<ModOps><ModOp Type="replace" GUID="1000" Path="/Values/Building/Radius">'
                "<Radius>30</Radius></ModOp></ModOps>", encoding="utf-8",
            )
            (root / host / "shared" / "modinfo.json").write_text(
                json.dumps({"ModID": "shared-pools", "Version": version}), encoding="utf-8"
            )
        session = reader.analyse_all(modfolder.mod_folders(root), self.index, roots=[root])
        self.assertEqual(
            reader.copies_by_host(session, [root]),
            {"Alpha Host": {"Alpha Host/shared": "Beta Host/shared"}},
        )
        # And its version: switch Beta Host off and Alpha's copy loads, which
        # is not the version these rows were read from.
        self.assertEqual(reader.copy_versions(session, [root]), {"Alpha Host/shared": "1.0"})


class TestAnOptionSaysWhatItDoes(unittest.TestCase):
    def test_the_author_s_description_travels_with_the_mod(self):
        offered = settings.ModOptions([settings.Option(name="a", label="A", description="Does a thing.")])
        again = settings.from_dict(offered.to_dict())
        self.assertEqual(again.get("a").description, "Does a thing.")

    def test_an_imya_knob_carries_its_description(self):
        body = IMYA_TWEAKABLE.replace('ExposeID="Alpha radius"', 'ExposeID="Alpha radius" Description="How far alpha reaches."')
        with tempfile.TemporaryDirectory() as tmp:
            tweaks = imya.read(_mod(Path(tmp), body=body))
        self.assertEqual(tweaks.options().get(IMYA_RADIUS).description, "How far alpha reaches.")


if __name__ == "__main__":
    unittest.main()
