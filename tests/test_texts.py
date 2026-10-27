"""What mods call things: their localisation patches, read for names -- and
the names the Studio then shows, for the game's assets and for the ones a mod
adds."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

import _isolation  # noqa: F401 -- first: keeps the suite out of real files under any runner

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from anno_modding_studio.core import index as index_mod  # noqa: E402
from anno_modding_studio.core import texts  # noqa: E402
from anno_modding_studio.core.changes import Change, Loadout, ModLayer  # noqa: E402
from anno_modding_studio.core.games import ANNO_117, ANNO_1800  # noqa: E402

GUI_1800 = "data/config/gui/texts_english.xml"
GUI_117 = "data/base/config/gui/texts_english.xml"


class _Mods(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

    def mod(self, name, body, game=ANNO_1800, **layer) -> ModLayer:
        folder = self.root / name
        path = folder / (GUI_1800 if game == ANNO_1800 else GUI_117)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("<ModOps>%s</ModOps>" % body, encoding="utf-8")
        layer.setdefault("mod_id", name.lower())
        return ModLayer(origin=name, name=name, folder=str(folder), **layer)

    def loadout(self, *layers, game=ANNO_1800) -> Loadout:
        loadout = Loadout(game=game)
        loadout.layers = list(layers)
        for order, layer in enumerate(loadout.layers, start=1):
            layer.order = layer.order or order
        return loadout


class TestTheLanguage(unittest.TestCase):
    def test_from_the_file_name(self):
        self.assertEqual(texts.language_of("C:/x/texts_english.xml"), "english")
        self.assertEqual(texts.language_of("texts_Chinese.XML"), "chinese")
        self.assertEqual(texts.language_of("assets.xml"), "")


class TestReadingAPatch(_Mods):
    def edits(self, body, game=ANNO_1800):
        layer = self.mod("m", body, game)
        path = texts.patch_file(layer.folder, game, "english")
        found = texts.read(path, game)
        return [(e.kind, e.key, e.text, e.conditions) for e in found.edits], found.unread

    def test_new_entries_in_the_list(self):
        edits, unread = self.edits(
            '<ModOp Type="add" Path="/TextExport/Texts">'
            "<Text><GUID>1500010401</GUID><Text>Diagonal House</Text></Text>"
            "<Text><GUID>1500010402</GUID><Text> Spaced </Text></Text></ModOp>"
        )
        self.assertEqual(edits, [("set", "1500010401", "Diagonal House", ()),
                                 ("set", "1500010402", "Spaced", ())])
        self.assertEqual(unread, 0)

    def test_a_file_s_own_entities_are_read_and_no_other_file_is(self):
        """The parser says so itself rather than leave it to lxml's defaults:
        an entity naming a file on this PC is not read into a text."""
        secret = self.root / "secret.txt"
        secret.write_text("TOPSECRET", encoding="utf-8")
        layer = self.mod("m", "")
        path = texts.patch_file(layer.folder, ANNO_1800, "english")
        path.write_text(
            '<!DOCTYPE ModOps [<!ENTITY own "Diagonal House">'
            '<!ENTITY theirs SYSTEM "%s">]>'
            '<ModOps><ModOp Type="add" Path="/TextExport/Texts">'
            "<Text><GUID>1</GUID><Text>&own;</Text></Text>"
            "<Text><GUID>2</GUID><Text>x&theirs;</Text></Text></ModOp></ModOps>" % secret.as_uri(),
            encoding="utf-8")
        found = texts.read(path, ANNO_1800)
        said = {edit.key: edit.text for edit in found.edits}
        self.assertEqual(said.get("1"), "Diagonal House")
        self.assertNotIn("TOPSECRET", repr(said))

    def test_117_writes_them_short_and_keys_them_by_lineid(self):
        edits, _ = self.edits(
            '<ModOp Add="/TextExport/Texts[1]"><Text><Text>Silver Veins</Text>'
            "<LineId>1700100014</LineId></Text>"
            # Keyed by GUID, which 117's loader does not look up.
            "<Text><GUID>1500030000</GUID><Text>Sigil</Text></Text></ModOp>"
            '<ModOp Replace="//Text[LineId=\'-6917482507662187473\']/Text">'
            "<Text>Licia Ma shows you the letter.</Text></ModOp>",
            ANNO_117,
        )
        self.assertEqual(edits, [
            ("set", "1700100014", "Silver Veins", ()),
            ("set", "-6917482507662187473", "Licia Ma shows you the letter.", ()),
        ])

    def test_renaming_one_or_several_by_key(self):
        edits, _ = self.edits(
            "<ModOp Type=\"replace\" Path=\"/TextExport/Texts/Text[GUID = '137489' or GUID='412']/Text\">"
            "<Text>Upgrade Floors</Text></ModOp>"
        )
        self.assertEqual([e[:3] for e in edits], [("set", "137489", "Upgrade Floors"),
                                                  ("set", "412", "Upgrade Floors")])

    def test_adding_to_the_end_of_one(self):
        edits, _ = self.edits(
            "<ModOp Type=\"add\" Path=\"/TextExport/Texts/Text[GUID = '25929']/Text\"> (1x1)</ModOp>"
        )
        self.assertEqual(edits, [("append", "25929", " (1x1)", ())])

    def test_a_whole_entry_replaced_or_merged(self):
        edits, _ = self.edits(
            "<ModOp Type=\"replace\" Path=\"/TextExport/Texts/Text[GUID = '100660']\">"
            "<Text><GUID>100660</GUID><Text>Bigger</Text></Text></ModOp>"
            "<ModOp Type=\"merge\" Path=\"/TextExport/Texts/Text[GUID='7328']\">"
            "<Text><Text>Beautification</Text></Text></ModOp>"
        )
        self.assertEqual([e[:3] for e in edits], [("set", "100660", "Bigger"),
                                                  ("set", "7328", "Beautification")])

    def test_what_is_not_one_of_those_is_counted_not_read(self):
        edits, unread = self.edits(
            # A copy of another entry's text.
            "<ModOp Type=\"replace\" Path=\"/TextExport/Texts/Text[GUID = '10595']/Text\" "
            "Content=\"/TextExport/Texts/Text[GUID = '10595']/Text/text()\"><Text><ModOpContent/></Text></ModOp>"
            # A merge into the list, by position.
            '<ModOp Type="merge" Path="/TextExport/Texts"><Text><GUID>1</GUID><Text>x</Text></Text></ModOp>'
            # A predicate that is not a lookup by key.
            "<ModOp Type=\"replace\" Path=\"/TextExport/Texts/Text[GUID = '1']/Text[not(contains(.,'x'))]\">"
            "<Text>y</Text></ModOp>"
        )
        self.assertEqual(edits, [])
        self.assertEqual(unread, 3)

    def test_conditions_travel_with_the_edit(self):
        edits, _ = self.edits(
            '<ModOp Type="add" Path="/TextExport/Texts" Condition="#enbesa_kurila">'
            "<Text><GUID>1</GUID><Text>Trucks</Text></Text></ModOp>"
            '<Group Condition="!#other"><ModOp Type="add" Path="/TextExport/Texts">'
            "<Text><GUID>2</GUID><Text>Ships</Text></Text></ModOp></Group>"
            '<ModOp Type="add" Path="/TextExport/Texts" Skip="1">'
            "<Text><GUID>3</GUID><Text>Off</Text></Text></ModOp>"
        )
        self.assertEqual(edits, [("set", "1", "Trucks", ("#enbesa_kurila",)),
                                 ("set", "2", "Ships", ("!#other",))])


class TestTheNamesOfWhatLoads(_Mods):
    ADD = '<ModOp Type="add" Path="/TextExport/Texts"><Text><GUID>%s</GUID><Text>%s</Text></Text></ModOp>'

    def test_the_last_mod_in_order_names_it_and_the_others_are_said(self):
        loadout = self.loadout(
            self.mod("First", self.ADD % ("1", "Fish Smokery")),
            self.mod("Second", self.ADD % ("1", "Smokehouse")),
            self.mod("Same", self.ADD % ("1", "Smokehouse")),
        )
        names = texts.build(loadout, "english")
        self.assertEqual(names.texts, {"1": "Smokehouse"})
        self.assertEqual(names.by, {"1": "Same"})
        # Only the one that says something else.
        self.assertEqual(names.also, {"1": ["First"]})

    def test_what_does_not_load_names_nothing(self):
        new = self.mod("New", self.ADD % ("1", "New"), declared={"DeprecateIds": ["old"]})
        old = self.mod("Old", self.ADD % ("2", "Old"))
        off = self.mod("Off", self.ADD % ("3", "Off"), active=False)
        names = texts.build(self.loadout(new, old, off), "english")
        self.assertEqual(names.texts, {"1": "New"})

    def test_adding_to_the_game_s_own_text(self):
        loadout = self.loadout(self.mod(
            "Sizes", "<ModOp Type=\"add\" Path=\"/TextExport/Texts/Text[GUID='25929']/Text\"> (1x1)</ModOp>"
        ))
        names = texts.build(loadout, "english", {"25929": "Fountain"}.get)
        self.assertEqual(names.texts, {"25929": "Fountain (1x1)"})

    def test_a_condition_about_another_mod_is_answered(self):
        body = (
            '<ModOp Type="add" Path="/TextExport/Texts" Condition="#helper">'
            "<Text><GUID>1</GUID><Text>With helper</Text></Text></ModOp>"
            '<ModOp Type="add" Path="/TextExport/Texts" Condition="!#helper">'
            "<Text><GUID>2</GUID><Text>Without</Text></Text></ModOp>"
            "<ModOp Type=\"add\" Path=\"/TextExport/Texts\" Condition=\"/TextExport/Texts/Text[GUID='9']\">"
            "<Text><GUID>3</GUID><Text>Asks the texts</Text></Text></ModOp>"
        )
        alone = texts.build(self.loadout(self.mod("M", body)), "english")
        self.assertEqual(alone.texts, {"2": "Without"})
        self.assertEqual(alone.unread, {"M": 1})
        helped = texts.build(self.loadout(self.mod("M2", body), ModLayer(origin="Helper", mod_id="helper")),
                             "english")
        self.assertEqual(helped.texts, {"1": "With helper"})

    def test_a_patch_rewritten_is_read_again(self):
        layer = self.mod("M", self.ADD % ("1", "Before"))
        loadout = self.loadout(layer)
        self.assertEqual(texts.build(loadout, "english").texts, {"1": "Before"})
        path = texts.patch_file(layer.folder, ANNO_1800, "english")
        path.write_text("<ModOps>%s</ModOps>" % (self.ADD % ("1", "After, and longer")), encoding="utf-8")
        self.assertEqual(texts.build(loadout, "english").texts, {"1": "After, and longer"})

    def test_only_the_language_asked_for(self):
        loadout = self.loadout(self.mod("M", self.ADD % ("1", "Name")))
        self.assertEqual(texts.build(loadout, "german").texts, {})
        self.assertEqual(texts.build(loadout, "").texts, {})


ASSETS = (
    "<AssetList>"
    # A shipped asset with its name baked in, and a LineID pointing elsewhere.
    "<Asset><Template>Product</Template><Values><Standard><GUID>1010216</GUID>"
    "<Name>schnapps</Name></Standard><Text><LocaText><English><Text>Schnapps</Text></English>"
    "</LocaText><LineID>5357</LineID></Text></Values></Asset>"
    # One with nothing baked in, named through its LineID.
    "<Asset><Template>FactoryBuilding7</Template><Values><Standard><GUID>1010278</GUID>"
    "<Name>coastal_01</Name></Standard><Text><LineID>5248</LineID></Text></Values></Asset>"
    "</AssetList>"
)
BASE_TEXTS = (
    "<TextExport><Texts>"
    "<Text><GUID>5357</GUID><Text>Remove</Text></Text>"
    "<Text><GUID>5248</GUID><Text>Fishery</Text></Text>"
    "</Texts></TextExport>"
)


class TestTheNamesShown(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        tmp = Path(self._tmp.name)
        (tmp / "assets.xml").write_text(ASSETS, encoding="utf-8")
        index_mod.build_index(tmp / "assets.xml", tmp / "index.sqlite")
        self.index = index_mod.AssetIndex(tmp / "index.sqlite", tmp / "assets.xml")
        self.addCleanup(self.index.close)
        (tmp / "texts_english.xml").write_text(BASE_TEXTS, encoding="utf-8")
        self.index.load_texts(tmp / "texts_english.xml")

    def names(self, **entries) -> texts.Names:
        names = texts.Names(language="english")
        for key, (text, by) in entries.items():
            names.texts[key.lstrip("_")] = text
            names.by[key.lstrip("_")] = by
        return names

    def test_the_language_is_kept_with_the_texts(self):
        self.assertEqual(self.index.texts_language, "english")
        self.index.clear_texts()
        self.assertEqual(self.index.texts_language, "")

    def test_a_mod_s_name_at_the_lineid(self):
        self.index.set_mod_names(self.names(_5248=("Harbour Fishery", "Fish Mod")))
        row = self.index.asset_row("1010278")
        self.assertEqual((row.display, row.named_by), ("Harbour Fishery", "Fish Mod"))

    def test_embedded_text_still_beats_a_lineid_a_mod_renamed(self):
        """A mod renaming entry 5357 renames the "Remove" button, not Schnapps."""
        self.index.set_mod_names(self.names(_5357=("Delete", "Button Mod")))
        self.assertEqual(self.index.asset_row("1010216").display, "Schnapps")

    def test_but_a_mod_s_name_for_the_asset_s_own_guid_beats_everything(self):
        self.index.set_mod_names(self.names(_1010216=("Kornbrand", "Schnapps Mod")))
        row = self.index.asset_row("1010216")
        self.assertEqual((row.display, row.named_by), ("Kornbrand", "Schnapps Mod"))
        self.assertEqual(row.label, "Kornbrand  (schnapps)")

    def test_search_finds_the_mod_s_names(self):
        self.index.set_mod_names(self.names(_5248=("Harbour Fishery", "Fish Mod")))
        self.assertEqual([row.guid for row in self.index.search("harbour")], ["1010278"])
        self.assertEqual(self.index.total_matches("harbour"), 1)
        self.assertEqual(self.index.template_counts_for("harbour"), [("FactoryBuilding7", 1)])
        self.index.set_mod_names(None)
        self.assertEqual(self.index.search("harbour"), [])
        self.assertEqual(self.index.asset_row("1010278").display, "Fishery")

    def test_the_template_counts_are_held_until_the_texts_move(self):
        """The Template list asks on every refresh of the results, the same
        words or not, and each answer was a sweep of every asset.  The last
        one is held, and let go whenever what the words are matched against
        moves: the mods' names, the game's texts cleared, or read."""
        swept = []
        self.index._con.set_trace_callback(
            lambda sql: swept.append(sql) if "GROUP BY a.template" in sql else None
        )
        self.addCleanup(self.index._con.set_trace_callback, None)

        self.assertEqual(self.index.template_counts_for("harbour"), [])
        self.assertEqual(self.index.template_counts_for("harbour"), [])
        self.assertEqual(self.index.template_counts_for("  Harbour "), [])
        self.assertEqual(len(swept), 1)

        self.index.set_mod_names(self.names(_5248=("Harbour Fishery", "Fish Mod")))
        found = [("FactoryBuilding7", 1)]
        self.assertEqual(self.index.template_counts_for("harbour"), found)
        self.assertEqual(len(swept), 2)
        # The answer handed out is the caller's own to change.
        self.index.template_counts_for("harbour").append(("Nothing", 0))
        self.assertEqual(self.index.template_counts_for("harbour"), found)
        self.assertEqual(len(swept), 2)

        self.assertEqual(self.index.template_counts_for("fishery"), found)
        self.index.clear_texts()
        self.assertEqual(self.index.template_counts_for("fishery"), found)
        self.index.set_mod_names(None)
        self.assertEqual(self.index.template_counts_for("fishery"), [])
        self.index.load_texts(Path(self._tmp.name) / "texts_english.xml")
        self.assertEqual(self.index.template_counts_for("fishery"), found)
        self.assertEqual(len(swept), 6)

    def test_setting_the_same_names_again_changes_nothing(self):
        self.assertTrue(self.index.set_mod_names(self.names(_1=("A", "M"))))
        self.assertFalse(self.index.set_mod_names(self.names(_1=("A", "M"))))
        self.assertTrue(self.index.set_mod_names(self.names(_1=("A", "Other"))))

    def test_names_that_move_let_go_of_the_rows_they_name_and_no_others(self):
        """A mod switched off or on moves its own entries.  Every row held
        was let go for it, and the next drawing of the Mod changes panel
        asked the index for each again."""
        asked = []
        self.index._con.set_trace_callback(
            lambda sql: asked.append(sql) if "FROM assets a LEFT JOIN texts" in sql else None
        )
        self.addCleanup(self.index._con.set_trace_callback, None)

        def rows():
            return [
                self.index.asset_row(guid).display for guid in ("1010278", "1010216")
            ] + [self.index.asset_row("no such asset")]

        self.assertEqual(rows(), ["Fishery", "Schnapps", None])
        self.assertEqual(len(asked), 3)
        # At the fishery's LineID: its row is asked for again, alone.
        self.index.set_mod_names(self.names(_5248=("Harbour Fishery", "Fish Mod")))
        self.assertEqual(rows(), ["Harbour Fishery", "Schnapps", None])
        self.assertEqual(len(asked), 4)
        # The same text from another mod is another answer: who named it.
        self.index.set_mod_names(self.names(_5248=("Harbour Fishery", "Other Mod")))
        self.assertEqual(self.index.asset_row("1010278").named_by, "Other Mod")
        self.assertEqual(len(asked), 5)
        # At an asset's own GUID, and gone again.
        self.index.set_mod_names(self.names(_1010216=("Kornbrand", "Schnapps Mod")))
        self.assertEqual(rows(), ["Fishery", "Kornbrand", None])
        self.assertEqual(len(asked), 7)
        self.index.set_mod_names(None)
        self.assertEqual(rows(), ["Fishery", "Schnapps", None])
        self.assertEqual(len(asked), 8)
        # The game's own texts read again name anything: every row goes.
        self.index.load_texts(Path(self._tmp.name) / "texts_english.xml")
        self.assertEqual(rows(), ["Fishery", "Schnapps", None])
        self.assertEqual(len(asked), 11)

    def test_what_a_mod_adds_is_named_and_found_too(self):
        from anno_modding_studio.core.workspace import Workspace

        loadout = Loadout(game=ANNO_1800)
        loadout.layers = [ModLayer(origin="Fish Mod", name="Fish Mod")]
        for guid, text_part in (
            ("1500000001", "<Text><LineID>1500000001</LineID></Text>"),
            ("1500000002", "<Text><LocaText><English><Text>Eel Pond</Text></English></LocaText></Text>"),
            ("1500000003", ""),
        ):
            payload = ("<Asset><Template>FactoryBuilding7</Template><Values><Standard><GUID>%s</GUID>"
                       "<Name>internal_%s</Name></Standard>%s</Values></Asset>" % (guid, guid, text_part))
            change = Change(guid="1010278", asset_label="coastal_01", path="", tag="Asset",
                            op="addNextSibling", origin="Fish Mod", payload=payload)
            loadout.changes[change.key] = change
        self.index.set_mod_names(self.names(_1500000001=("Smokery", "Fish Mod")))
        view = Workspace(self.index, loadout)
        rows = {row.guid: row for row in view.new_asset_rows()}
        self.assertEqual(rows["1500000001"].label, "+ Smokery  (internal_1500000001)")
        self.assertEqual(rows["1500000001"].named_by, "Fish Mod")
        self.assertEqual(rows["1500000002"].display, "Eel Pond")
        self.assertEqual(rows["1500000003"].display, "internal_1500000003")
        self.assertEqual([row.guid for row in view.search("smokery")], ["1500000001"])

    def test_the_row_of_an_added_asset_is_made_once_and_again_when_it_moves(self):
        """Every row of the Mod changes panel that names such an asset asks
        for it, each time the panel groups its changes."""
        from anno_modding_studio.core.workspace import Workspace

        loadout = Loadout(game=ANNO_1800)
        loadout.layers = [ModLayer(origin="Fish Mod", name="Fish Mod")]
        payload = ("<Asset><Template>FactoryBuilding7</Template><Values><Standard>"
                   "<GUID>1500000001</GUID><Name>internal</Name></Standard></Values></Asset>")
        change = Change(guid="1010278", asset_label="coastal_01", path="", tag="Asset",
                        op="addNextSibling", origin="Fish Mod", payload=payload)
        loadout.changes[change.key] = change
        view = Workspace(self.index, loadout)
        row = view.asset_row("1500000001")
        self.assertEqual(row.display, "internal")
        self.assertIs(view.asset_row("1500000001"), row)
        # Named here, named by a mod's texts, and written another way: each
        # is another row.
        change.in_game_name = "Smokery"
        self.assertEqual(view.asset_row("1500000001").display, "Smokery")
        change.in_game_name = ""
        self.index.set_mod_names(self.names(_1500000001=("Eel Pond", "Fish Mod")))
        self.assertEqual(view.asset_row("1500000001").display, "Eel Pond")
        change.payload = payload.replace("internal", "renamed")
        self.assertEqual(view.asset_row("1500000001").name, "renamed")

    def test_an_added_asset_renamed_by_a_mod_shows_the_new_name(self):
        """The row was held against the id of the mods' names, an object a
        refresh replaces whether they changed or not.  Set again unchanged and
        then renamed, the new object could land where the first had been, and
        the row went on saying what the mod had called the asset before: 42
        times in 50.  Asked several times, so a reused address is all but sure."""
        from anno_modding_studio.core.workspace import Workspace

        payload = ("<Asset><Template>FactoryBuilding7</Template><Values><Standard>"
                   "<GUID>1500000001</GUID><Name>internal</Name></Standard></Values></Asset>")
        for turn in range(10):
            loadout = Loadout(game=ANNO_1800)
            loadout.layers = [ModLayer(origin="Fish Mod", name="Fish Mod")]
            change = Change(guid="1010278", asset_label="coastal_01", path="", tag="Asset",
                            op="addNextSibling", origin="Fish Mod", payload=payload)
            loadout.changes[change.key] = change
            view = Workspace(self.index, loadout)
            self.index.set_mod_names(self.names(_1500000001=("Eel Pond %d" % turn, "Fish Mod")))
            self.assertEqual(view.asset_row("1500000001").display, "Eel Pond %d" % turn)
            self.index.set_mod_names(self.names(_1500000001=("Eel Pond %d" % turn, "Fish Mod")))
            self.index.set_mod_names(self.names(_1500000001=("Trout Farm %d" % turn, "Fish Mod")))
            self.assertEqual(view.asset_row("1500000001").display, "Trout Farm %d" % turn)


if __name__ == "__main__":
    unittest.main()
