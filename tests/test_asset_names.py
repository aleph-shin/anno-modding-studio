"""What players see an asset the Studio creates called: given when it is made,
written into every language's texts file on export, and read back in."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

import _isolation  # noqa: F401 -- first: keeps the suite out of real files under any runner
from lxml import etree

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from support import SchemaTestBase  # noqa: E402

from anno_modding_studio.core import generator, texts  # noqa: E402
from anno_modding_studio.core.changes import (  # noqa: E402
    OP_ADD_NEXT,
    Change,
    Loadout,
    ModLayer,
    change_from_dict,
    own_record,
)
from anno_modding_studio.core.games import ANNO_117, ANNO_1800  # noqa: E402


def _creates(guid: str, name: str = "", origin: str = "", game: str = ANNO_1800, text: str = "") -> Change:
    payload = (
        "<Asset><BaseAssetGUID>1000</BaseAssetGUID><Values><Standard><GUID>%s</GUID>"
        "<Name>internal_%s</Name></Standard>%s</Values></Asset>" % (guid, guid, text)
    )
    return Change(guid="1000", asset_label="x", path="", tag="Asset", op=OP_ADD_NEXT,
                  payload=payload, origin=origin, in_game_name=name)


def _loadout(game=ANNO_1800, *changes) -> Loadout:
    loadout = Loadout(game=game, own=own_record(name="My Mod"))
    for change in changes:
        loadout.set_change(change)
    return loadout


class TestTheNameGoesWithTheChange(unittest.TestCase):
    def test_saved_and_read_back(self):
        change = _creates("2001000000", "Small Trade Union")
        raw = json.loads(json.dumps(change.__dict__))
        self.assertEqual(change_from_dict(raw).in_game_name, "Small Trade Union")
        self.assertEqual(change_from_dict({**raw, "in_game_name": None}).in_game_name, "")

    def test_only_what_creates_an_asset_and_is_on(self):
        named = _creates("2001000000", "Union")
        off = _creates("2001000001", "Off")
        off.active = False
        loadout = _loadout(ANNO_1800, named, off, _creates("2001000002"))
        self.assertEqual(texts.names_of(loadout), [("2001000000", "Union")])


class TestWritingThem(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

    def test_every_language_1800_keyed_by_guid(self):
        loadout = _loadout(ANNO_1800, _creates("2001000000", "Small <Trade> Union"))
        self.assertEqual(texts.write_names(loadout, self.root), 13)
        english = (self.root / "data/config/gui/texts_english.xml").read_text(encoding="utf-8")
        self.assertIn('<ModOp Type="add" Path="/TextExport/Texts">', english)
        self.assertIn("<Text><GUID>2001000000</GUID><Text>Small &lt;Trade&gt; Union</Text></Text>", english)
        self.assertTrue((self.root / "data/config/gui/texts_taiwanese.xml").is_file())
        # What the game makes of it is what the Studio reads back.
        layer = ModLayer(origin="My Mod", folder=str(self.root), mod_id="my-mod")
        read_back = texts.build(_loadout(ANNO_1800), "english")
        self.assertEqual(read_back.texts, {})
        mods = _loadout(ANNO_1800)
        mods.layers = [layer]
        self.assertEqual(texts.build(mods, "german").texts, {"2001000000": "Small <Trade> Union"})

    def test_read_back_onto_the_changes_that_add_the_assets(self):
        """A mod exported here and read in again: the block is in its texts
        files and on no change, so the next export wrote it out empty."""
        texts.write_names(
            _loadout(ANNO_1800, _creates("2001000000", "Small <Trade> Union"),
                     _creates("2001000001", "Depot")),
            self.root,
        )
        self.assertEqual(
            texts.names_written(self.root),
            {"2001000000": "Small <Trade> Union", "2001000001": "Depot"},
        )
        read = [_creates("2001000000"), _creates("2001000001", "Renamed here"),
                _creates("2001000002")]
        self.assertEqual(texts.adopt_names(read, self.root), 1)
        self.assertEqual(
            [change.in_game_name for change in read],
            ["Small <Trade> Union", "Renamed here", ""],
        )
        # And so writing the mod back leaves its names where they were.
        before = (self.root / "data/config/gui/texts_english.xml").read_text(encoding="utf-8")
        texts.write_names(_loadout(ANNO_1800, read[0], _creates("2001000001", "Depot")), self.root)
        self.assertEqual(
            (self.root / "data/config/gui/texts_english.xml").read_text(encoding="utf-8"), before
        )

    def test_117_read_back_by_lineid(self):
        texts.write_names(_loadout(ANNO_117, _creates("2001000000", "Villa", game=ANNO_117)), self.root)
        read = [_creates("2001000000", game=ANNO_117)]
        self.assertEqual(texts.adopt_names(read, self.root), 1)
        self.assertEqual(read[0].in_game_name, "Villa")

    def test_a_mod_with_no_block_names_nothing(self):
        path = self.root / "data/config/gui/texts_english.xml"
        path.parent.mkdir(parents=True)
        path.write_text(
            '<ModOps><ModOp Type="add" Path="/TextExport/Texts"><Text><GUID>2001000000</GUID>'
            "<Text>Theirs</Text></Text></ModOp></ModOps>", encoding="utf-8",
        )
        read = [_creates("2001000000")]
        self.assertEqual(texts.adopt_names(read, self.root), 0)
        self.assertEqual(read[0].in_game_name, "")

    def test_the_files_an_export_writes(self):
        named = _loadout(ANNO_1800, _creates("2001000000", "Union"))
        self.assertEqual(len(texts.files_written(named, self.root)), 13)
        unnamed = _loadout(ANNO_1800, _creates("2001000000"))
        self.assertEqual(texts.files_written(unnamed, self.root), [])
        texts.write_names(named, self.root)
        # A block to take out is a file written too.
        self.assertEqual(len(texts.files_written(unnamed, self.root)), 13)

    def test_117_short_and_keyed_by_lineid(self):
        loadout = _loadout(ANNO_117, _creates("2001000000", "Villa", game=ANNO_117))
        self.assertEqual(texts.write_names(loadout, self.root), 12)
        text = (self.root / "data/base/config/gui/texts_simplified_chinese.xml").read_text(encoding="utf-8")
        self.assertIn('<ModOp Add="/TextExport/Texts">', text)
        self.assertIn("<Text><LineId>2001000000</LineId><Text>Villa</Text></Text>", text)

    def test_a_texts_file_the_mod_has_keeps_its_own_and_gets_the_block_once(self):
        path = self.root / "data/config/gui/texts_english.xml"
        path.parent.mkdir(parents=True)
        own = '<ModOps>\n  <ModOp Type="add" Path="/TextExport/Texts"><Text><GUID>9</GUID><Text>Theirs</Text></Text></ModOp>\n</ModOps>\n'
        path.write_bytes(b"\xef\xbb\xbf" + own.encode("utf-8"))
        loadout = _loadout(ANNO_1800, _creates("2001000000", "First"))
        texts.write_names(loadout, self.root)
        texts.write_names(loadout, self.root)
        raw = path.read_bytes()
        self.assertTrue(raw.startswith(b"\xef\xbb\xbf"))
        text = raw.decode("utf-8-sig")
        self.assertIn("<Text>Theirs</Text>", text)
        self.assertEqual(text.count("Anno Modding Studio: the in-game names"), 1)
        # Renamed, the block is replaced; unnamed, it goes -- and theirs stays.
        next(iter(loadout.changes.values())).in_game_name = "Second"
        texts.write_names(loadout, self.root)
        text = path.read_text(encoding="utf-8-sig")
        self.assertNotIn("First", text)
        self.assertIn("<Text>Second</Text>", text)
        next(iter(loadout.changes.values())).in_game_name = ""
        texts.write_names(loadout, self.root)
        self.assertEqual(path.read_text(encoding="utf-8-sig"), own)

    def test_a_file_that_is_not_modops_is_left_alone_and_said(self):
        path = self.root / "data/config/gui/texts_english.xml"
        path.parent.mkdir(parents=True)
        path.write_text("<TextExport/>", encoding="utf-8")
        trouble = []
        texts.write_names(_loadout(ANNO_1800, _creates("2001000000", "Union")), self.root, trouble)
        self.assertEqual(path.read_text(encoding="utf-8"), "<TextExport/>")
        self.assertEqual(trouble, ["texts_english.xml: not a ModOps file, so the names were not added to it"])

    def test_nothing_to_write_writes_nothing(self):
        self.assertEqual(texts.write_names(_loadout(ANNO_1800, _creates("2001000000")), self.root), 0)
        self.assertFalse((self.root / "data").exists())

    def test_an_export_carries_them(self):
        change = _creates("2001000000", "Union")
        loadout = _loadout(ANNO_1800, change)
        view = generator.for_mod(loadout, "")
        root = generator.write_mod_folder(view, self.root, generator.build_modops_xml(view))
        self.assertIn("<Text>Union</Text>",
                      (root / "data/config/gui/texts_english.xml").read_text(encoding="utf-8"))


class TestPointingAn117AssetAtItsName(unittest.TestCase):
    ASSET = ("<Asset><Template>Residence</Template><Values><Standard><GUID>7</GUID>"
             "<Name>n</Name></Standard><Building/></Values></Asset>")

    def test_an_oasisid_of_its_own_guid(self):
        linked = etree.fromstring(texts.link_name(self.ASSET, "7", ANNO_117).encode("utf-8"))
        self.assertEqual(linked.findtext("Values/Text/OasisId"), "7")
        self.assertEqual([child.tag for child in linked.find("Values")], ["Standard", "Text", "Building"])

    def test_one_that_is_its_own_is_left_as_written_and_1800_needs_none(self):
        own = self.ASSET.replace("<Building/>", "<Text><OasisId>7</OasisId></Text><Building/>")
        self.assertEqual(texts.link_name(own, "7", ANNO_117), own)
        self.assertEqual(texts.link_name(self.ASSET, "7", ANNO_1800), self.ASSET)

    def test_one_that_points_elsewhere_is_pointed_at_its_own(self):
        """The name is written under the asset's GUID, so left pointing at
        another's it was never looked up: the asset showed the other's name."""
        theirs = self.ASSET.replace("<Building/>", "<Text><OasisId>5</OasisId></Text><Building/>")
        linked = etree.fromstring(texts.link_name(theirs, "7", ANNO_117).encode("utf-8"))
        self.assertEqual([node.text for node in linked.iter("OasisId")], ["7"])


class TestTheNameShown(unittest.TestCase):
    def test_a_name_given_here_is_the_one_shown(self):
        from anno_modding_studio.core.workspace import Workspace

        loadout = _loadout(ANNO_1800, _creates("2001000000", "Small Trade Union"))
        row = Workspace(None, loadout).asset_row("2001000000")
        self.assertEqual(row.label, "+ Small Trade Union  (internal_2001000000)")


class TestTheDialog(SchemaTestBase):
    def setUp(self):
        from PySide6.QtWidgets import QApplication

        self.app = QApplication.instance() or QApplication([])

    def make(self, game, in_game):
        from anno_modding_studio.ui.new_asset_dialog import NewAssetDialog

        dialog = NewAssetDialog(self.index, Loadout(game=game))
        self.addCleanup(dialog.deleteLater)
        dialog._base_row = self.index.asset_row("1000")
        dialog.name_edit.setText("my_union")
        dialog.in_game_edit.setText(in_game)
        dialog._sync()
        self.assertEqual(dialog._problem(), "")
        dialog._accept()
        return dialog.change, etree.fromstring(dialog.change.payload.encode("utf-8"))

    def test_the_name_goes_on_the_change(self):
        change, asset = self.make(ANNO_1800, " Small Trade Union ")
        self.assertEqual(change.in_game_name, "Small Trade Union")
        self.assertIsNone(asset.find("Values/Text"))

    def test_117_points_the_asset_at_it(self):
        change, asset = self.make(ANNO_117, "Villa")
        self.assertEqual(asset.findtext("Values/Text/OasisId"), asset.findtext("Values/Standard/GUID"))

    def test_no_name_no_link(self):
        change, asset = self.make(ANNO_117, "")
        self.assertEqual(change.in_game_name, "")
        self.assertIsNone(asset.find("Values/Text"))

    def copied_once(self, in_game):
        """A one-time copy, for Anno 117, of an asset the game names."""
        from unittest import mock

        from anno_modding_studio.ui.new_asset_dialog import NewAssetDialog

        named = etree.fromstring(
            "<Asset><Template>FactoryBuilding7</Template><Values><Standard><GUID>1000</GUID>"
            "<Name>alpha</Name></Standard><Text><OasisId>1000</OasisId></Text></Values></Asset>"
        )
        dialog = NewAssetDialog(self.index, Loadout(game=ANNO_117))
        self.addCleanup(dialog.deleteLater)
        dialog._base_row = self.index.asset_row("1000")
        dialog.once_radio.setChecked(True)
        dialog.name_edit.setText("my_union")
        dialog.in_game_edit.setText(in_game)
        dialog._sync()
        with mock.patch.object(dialog, "_original", return_value=named):
            self.assertEqual(dialog._problem(), "")
            dialog._accept()
        asset = etree.fromstring(dialog.change.payload.encode("utf-8"))
        return dialog.change, asset.findtext("Values/Standard/GUID")

    def oasis_of(self, change):
        return etree.fromstring(change.payload.encode("utf-8")).findtext("Values/Text/OasisId")

    def test_a_copy_given_a_name_is_pointed_at_it(self):
        change, guid = self.copied_once("Villa")
        self.assertNotEqual(guid, "1000")
        self.assertEqual(self.oasis_of(change), guid)

    def test_a_copy_with_no_name_shows_its_originals_until_it_is_given_one(self):
        """Named afterwards, in the editor, it went on pointing at the
        original's name while its own was written under its GUID."""
        change, guid = self.copied_once("")
        self.assertEqual(self.oasis_of(change), "1000")
        change.payload = texts.link_name(change.payload, guid, ANNO_117)
        self.assertEqual(self.oasis_of(change), guid)


if __name__ == "__main__":
    unittest.main()
