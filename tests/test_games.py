"""Per-game output dialects, and discovery of base asset files.

The expected shapes here were taken from real shipping mods, not guessed:
Anno 1800 mods use ``Type="replace" GUID Path``; the Anno 117 community bug-fix
mod uses the ``Replace="@GUID/Section/Field"`` shorthand with no ``/Values``.
"""

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

from anno_modding_studio.core import discovery, games, generator, modinfo  # noqa: E402
from anno_modding_studio.core.changes import Change, Loadout, own_record  # noqa: E402


def _loadout(game):
    loadout = Loadout(own=own_record(name="Faster Lumber", creator="tester", version="1.2"), game=game)
    loadout.set_change(
        Change(
            guid="1010340",
            asset_label="Lumberjack's Hut",
            path="/Values/Factory7/CycleTime",
            tag="CycleTime",
            old_value="30.00",
            new_value="15.00",
        )
    )
    loadout.set_change(
        Change(
            guid="1010340",
            asset_label="Lumberjack's Hut",
            path="/Values/Maintenance/Maintenances/Item[Product='1010017']/Amount",
            tag="Amount",
            old_value="40",
            new_value="10",
        )
    )
    return loadout


class TestDialects(unittest.TestCase):
    def test_anno1800_uses_type_guid_path(self):
        xml = generator.build_modops_xml(_loadout(games.ANNO_1800))
        root = etree.fromstring(xml.encode("utf-8"))
        ops = root.findall("ModOp")
        self.assertEqual(len(ops), 2)
        for op in ops:
            self.assertEqual(op.get("Type"), "replace")
            self.assertEqual(op.get("GUID"), "1010340")
            self.assertTrue(op.get("Path").startswith("/Values/"))
            self.assertIsNone(op.get("Replace"))

    def test_anno117_uses_shorthand_without_values_prefix(self):
        xml = generator.build_modops_xml(_loadout(games.ANNO_117))
        root = etree.fromstring(xml.encode("utf-8"))
        targets = sorted(op.get("Replace") for op in root.findall("ModOp"))
        self.assertEqual(
            targets,
            [
                "@1010340/Factory7/CycleTime",
                "@1010340/Maintenance/Maintenances/Item[Product='1010017']/Amount",
            ],
        )
        for op in root.findall("ModOp"):
            self.assertIsNone(op.get("Type"))
            self.assertIsNone(op.get("Path"))
            self.assertIsNone(op.get("GUID"))

    def test_payload_is_identical_across_games(self):
        """Only the addressing differs; the replacement element must not."""
        bodies = []
        for game in (games.ANNO_1800, games.ANNO_117):
            root = etree.fromstring(
                generator.build_modops_xml(_loadout(game)).encode("utf-8")
            )
            bodies.append(
                sorted(etree.tostring(op[0]).strip() for op in root.findall("ModOp"))
            )
        self.assertEqual(bodies[0], bodies[1])

    def test_switching_game_only_changes_addressing(self):
        loadout = _loadout(games.ANNO_1800)
        before = len(etree.fromstring(generator.build_modops_xml(loadout).encode()).findall("ModOp"))
        loadout.game = games.ANNO_117
        after = len(etree.fromstring(generator.build_modops_xml(loadout).encode()).findall("ModOp"))
        self.assertEqual(before, after)

    def test_path_display_conversion(self):
        p117 = games.get(games.ANNO_117)
        self.assertEqual(p117.display_path("/Values/Building/BuildingType"), "/Building/BuildingType")
        p1800 = games.get(games.ANNO_1800)
        self.assertEqual(p1800.display_path("/Values/Building/BuildingType"), "/Values/Building/BuildingType")

    def test_unknown_game_key_falls_back_to_1800(self):
        self.assertEqual(games.get("anno9999").key, games.ANNO_1800)
        self.assertEqual(games.get(None).key, games.ANNO_1800)


class TestGameDetection(unittest.TestCase):
    def test_detects_1800_layout(self):
        self.assertEqual(
            games.detect_from_path(r"D:\extract\data\config\export\main\asset\assets.xml"),
            games.ANNO_1800,
        )

    def test_detects_117_layout(self):
        self.assertEqual(
            games.detect_from_path(r"D:\extract\data\base\config\export\assets.xml"),
            games.ANNO_117,
        )

    def test_unknown_layout_defaults(self):
        self.assertEqual(games.detect_from_path(r"D:\somewhere\assets.xml"), games.ANNO_1800)
        self.assertEqual(games.detect_from_path(""), games.ANNO_1800)


class TestModFolder(unittest.TestCase):
    def test_1800_folder_layout_and_modinfo(self):
        loadout = _loadout(games.ANNO_1800)
        with tempfile.TemporaryDirectory() as tmp:
            root = generator.write_mod_folder(
                loadout, Path(tmp), generator.build_modops_xml(loadout)
            )
            target = root / "data/config/export/main/asset/assets.xml"
            self.assertTrue(target.is_file())
            info = json.loads((root / "modinfo.json").read_text(encoding="utf-8"))
        self.assertNotIn("Anno", info)
        # The names the 1800 loader reads; it ignores the ones written before.
        self.assertEqual(info["ModDependencies"], [])
        self.assertEqual(info["DeprecateIds"], [])
        self.assertNotIn("Dependencies", info)
        self.assertNotIn("DeprecatedIds", info)

    def test_117_folder_layout_and_modinfo(self):
        loadout = _loadout(games.ANNO_117)
        with tempfile.TemporaryDirectory() as tmp:
            root = generator.write_mod_folder(
                loadout, Path(tmp), generator.build_modops_xml(loadout)
            )
            target = root / "data/base/config/export/assets.xml"
            self.assertTrue(target.is_file(), sorted(str(p) for p in root.rglob("*")))
            info = json.loads((root / "modinfo.json").read_text(encoding="utf-8"))
        self.assertEqual(info["Anno"], 8)
        self.assertIn("Require", info["Dependencies"])
        self.assertIn("GameSetup", info)


class TestWhatAModIDMayBe(unittest.TestCase):
    """Anno 117's loader refuses a ModID that is not lowercase letters, numbers
    and dashes -- ``mod-loader.log`` says so, and offers "fancy-mod-creatorname".
    Anno 1800 has never minded, and underscores are common there."""

    def test_117_refuses_capitals_underscores_and_spaces(self):
        game = games.get(games.ANNO_117)
        for bad in ("Faster_Lumber", "faster lumber", "FasterLumber", "faster.lumber", ""):
            self.assertIn("lowercase", game.mod_id_problem(bad), bad)
        self.assertEqual(game.mod_id_problem("faster-lumber-tester"), "")
        self.assertEqual(game.mod_id_problem("mod2"), "")

    def test_1800_minds_none_of_it(self):
        game = games.get(games.ANNO_1800)
        for anything in ("Faster_Lumber", "faster lumber", "FasterLumber", ""):
            self.assertEqual(game.mod_id_problem(anything), "")

    def test_the_offered_id_is_one_117_would_take(self):
        game = games.get(games.ANNO_117)
        for typed in ("Faster_Lumber", "Faster Lumber!", "FASTER--lumber"):
            offered = game.usable_mod_id(typed)
            self.assertEqual(game.mod_id_problem(offered), "", offered)
        self.assertEqual(game.usable_mod_id("Faster_Lumber"), "faster-lumber")
        # And 1800's stays what a blank field was always filled in with.
        self.assertEqual(games.get(games.ANNO_1800).usable_mod_id("Faster Lumber"), "Faster-Lumber")

    def test_a_blank_117_id_is_filled_in_lowercase(self):
        loadout = _loadout(games.ANNO_117)
        self.assertEqual(modinfo.written_mod_id(loadout), "faster-lumber")
        self.assertEqual(modinfo.written_mod_id(_loadout(games.ANNO_1800)), "Faster-Lumber")

    def test_but_what_was_typed_is_written_as_typed(self):
        """The check is where it is refused, not the file; rewriting the ID
        silently would leave a mod published under one the author never saw."""
        loadout = _loadout(games.ANNO_117)
        loadout.own.mod_id = "Faster_Lumber"
        self.assertEqual(modinfo.written_mod_id(loadout), "Faster_Lumber")


class TestAValueWrittenOverSeveralLines(unittest.TestCase):
    """Indenting every line of a payload is layout only while every line break
    is.  *Genshin cards* binds a key to thirty console commands, one to a line:
    the line breaks are in the value, and the export re-indented them."""

    COMMANDS = "First.Command(1);\n      Second.Command(2);\n\n   Third.Command(3);"

    def written(self, payload):
        loadout = Loadout(game=games.ANNO_1800)
        loadout.set_change(
            Change(guid="2001271", asset_label="keys", path="/Values/KeyBindings", tag="Item",
                   op="add", payload=payload)
        )
        return generator.build_modops_xml(loadout)

    def test_it_goes_out_as_it_was_written(self):
        written = self.written("<Item><Command>%s</Command></Item>" % self.COMMANDS)
        found = etree.fromstring(written.encode("utf-8")).find(".//Command")
        self.assertEqual(found.text, self.COMMANDS)

    def test_and_an_ordinary_payload_is_still_indented(self):
        written = self.written("<Item>\n<Product>1</Product>\n</Item>")
        self.assertIn("\n    <Item>\n    <Product>1</Product>\n    </Item>\n", written)

    def test_a_kept_op_whose_text_starts_on_a_line_of_its_own(self):
        """The loader appends an op's own text as it stands: *Extended Enbesa*
        adds ``;Africa`` on a line of its own, and indented again the region
        list in game came out with two more spaces in it."""
        source = ('<ModOp Type="add" GUID="1" Path="/Values/Product/AssociatedRegion" '
                  'Condition="@1">\n            ;Africa\n          </ModOp>')
        lines = generator._verbatim_lines(source)
        self.assertEqual(etree.fromstring("\n".join(lines).encode("utf-8")).text,
                         "\n            ;Africa\n          ")


class TestDiscoverySniff(unittest.TestCase):
    """The scan must never offer somebody else's mod as the base file."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def _write(self, relpath, text):
        path = self.tmp / relpath
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def test_rejects_a_mod_at_the_canonical_1800_path(self):
        path = self._write(
            "[Mod] Something/data/config/export/main/asset/assets.xml",
            '<ModOps>\n<ModOp Type="replace" GUID="1" Path="/Values/A/B"><B>2</B></ModOp>\n</ModOps>',
        )
        self.assertFalse(discovery.looks_like_assets_file(path))

    def test_rejects_a_mod_at_the_canonical_117_path(self):
        path = self._write(
            "[Mod] Other/data/base/config/export/assets.xml",
            '<ModOps>\n<ModOp Replace="@4115/Building"><Building/></ModOp>\n</ModOps>',
        )
        self.assertFalse(discovery.looks_like_assets_file(path))

    def test_accepts_a_base_file_in_either_layout(self):
        body = (
            "<Assets><Asset><Template>T</Template><Values><Standard>"
            "<GUID>1</GUID><Name>a</Name></Standard></Values></Asset></Assets>"
        )
        for relpath in (
            "e1/data/config/export/main/asset/assets.xml",
            "e2/data/base/config/export/assets.xml",
        ):
            self.assertTrue(discovery.looks_like_assets_file(self._write(relpath, body)), relpath)

    def test_accepts_the_grouped_assetlist_form(self):
        path = self._write(
            "e3/assets.xml",
            "<Content><AssetList><Groups><Group><Name>G</Name></Group></Groups></AssetList></Content>",
        )
        self.assertTrue(discovery.looks_like_assets_file(path))

    def test_rejects_unrelated_xml(self):
        path = self._write("e4/assets.xml", "<Settings><Volume>3</Volume></Settings>")
        self.assertFalse(discovery.looks_like_assets_file(path))


class TestInstallsFound(unittest.TestCase):
    """Only the games the Studio makes mods for are installs it reports."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.common = Path(self._tmp.name) / "steamapps" / "common"
        for name in ("Anno 1800", "Anno 117 - Pax Romana", "Anno 2205", "Some Other Game"):
            (self.common / name / "maindata").mkdir(parents=True)
            (self.common / name / "maindata" / "data0.rda").write_bytes(b"rda")

    def tearDown(self):
        self._tmp.cleanup()

    def test_anno_2205_is_not_one(self):
        from unittest import mock

        with mock.patch.object(discovery, "steam_libraries", return_value=[self.common]), \
                mock.patch.object(discovery, "ubisoft_dirs", return_value=[]):
            found = discovery.find_game_installs()
        self.assertEqual([install.name for install in found], ["Anno 117 - Pax Romana", "Anno 1800"])

class TestBreadcrumbsByName(unittest.TestCase):
    """The changes list is a summary, so a keyed entry reads by its name."""

    @staticmethod
    def name(guid):
        return "Marble" if guid == "2178" else ""

    def test_a_keyed_entry_the_game_names_is_shown_by_that_name(self):
        from anno_modding_studio.ui.changes_dock import field_label

        self.assertEqual(
            field_label("/Values/Cost/Costs/Item[Ingredient='2178']/Amount", self.name),
            "Cost \u203a Costs \u203a Marble \u203a Amount",
        )

    def test_one_it_does_not_know_keeps_its_key(self):
        from anno_modding_studio.ui.changes_dock import field_label

        self.assertEqual(
            field_label("/Values/Cost/Costs/Item[Ingredient='1010017']/Amount", self.name),
            "Cost \u203a Costs \u203a Ingredient 1010017 \u203a Amount",
        )

    def test_positions_are_left_alone(self):
        from anno_modding_studio.ui.changes_dock import field_label

        self.assertEqual(
            field_label("/Values/Tags/Tag[2]", self.name), "Tags \u203a Tag #2"
        )


class TestCategories(unittest.TestCase):
    """Categories mirror what mod.io offers, and differ per game."""

    def test_the_two_games_differ(self):
        self.assertNotEqual(
            games.get(games.ANNO_1800).categories, games.get(games.ANNO_117).categories
        )


class TestAsModioKnowsThem(unittest.TestCase):
    """The ids mod.io's API and its download cache key each game on."""

    def test_ids_and_addresses(self):
        self.assertEqual(games.get(games.ANNO_1800).modio_game_id, 4169)
        self.assertEqual(games.get(games.ANNO_117).modio_game_id, 11358)
        self.assertEqual(games.get(games.ANNO_1800).modio_url, "https://mod.io/g/anno-1800")
        self.assertEqual(games.get(games.ANNO_117).modio_url, "https://mod.io/g/anno-117-pax-romana")


class TestFieldLabel(unittest.TestCase):
    """The changes list has to identify which "Value" was changed."""

    def setUp(self):
        import os

        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

    def test_shows_the_whole_path(self):
        from anno_modding_studio.ui.changes_dock import field_label

        self.assertEqual(
            field_label("/Values/FactoryUpgrade/ProductivityUpgrade/Value"),
            "FactoryUpgrade \u203a ProductivityUpgrade \u203a Value",
        )

    def test_a_keyed_entry_is_named_by_the_field_that_picks_it_out(self):
        """The parent step already names the list, so ``Item`` only repeats it;
        which of the entry's own fields was matched is the part that identifies
        it, and it was the part being thrown away."""
        from anno_modding_studio.ui.changes_dock import field_label

        self.assertEqual(
            field_label("/Values/Cost/Costs/Item[Ingredient='1010196']/Amount"),
            "Cost \u203a Costs \u203a Ingredient 1010196 \u203a Amount",
        )

    def test_and_a_positional_one_reads_as_a_position(self):
        """``Item[2]`` claims *the second entry, whatever it holds*, which is a
        different and more fragile claim than ``the entry whose X is 2``."""
        from anno_modding_studio.ui.changes_dock import field_label

        self.assertEqual(field_label("/Values/Tags/Tag[2]"), "Tags \u203a Tag #2")

    def test_the_two_are_no_longer_written_the_same_way(self):
        from anno_modding_studio.ui.changes_dock import compact_step

        self.assertNotEqual(
            compact_step("Item[2]"), compact_step("Item[Product='2']")
        )

    def test_compacting_keeps_it_shorter_than_the_raw_path(self):
        from anno_modding_studio.ui.changes_dock import field_label

        path = "/Values/ExpeditionAttribute/ExpeditionAttributes/Item[Attribute='Diplomacy']/Amount"
        self.assertLess(len(field_label(path)), len(path))
        self.assertIn("Diplomacy", field_label(path))

    def test_steps_without_predicates_are_untouched(self):
        from anno_modding_studio.ui.changes_dock import compact_step

        self.assertEqual(compact_step("BuildingType"), "BuildingType")

    def test_a_predicate_the_studio_did_not_write_is_left_alone(self):
        """An op may carry any XPath at all; guessing at one is worse than
        showing it."""
        from anno_modding_studio.ui.changes_dock import compact_step

        self.assertEqual(
            compact_step("Values[../Template='Factory7']"),
            "Values[../Template='Factory7']",
        )

    def test_the_tooltip_form_looks_the_guids_up(self):
        """Not the column: a resolved name is several times the width of the
        GUID, and a path with two in would be all name and no path."""
        from anno_modding_studio.ui.changes_dock import named_path

        self.assertEqual(
            named_path(
                "/Values/Cost/Costs/Item[Ingredient='1010196']/Amount",
                lambda guid: {"1010196": "Timber"}.get(guid, ""),
            ),
            "Cost \u203a Costs \u203a Ingredient Timber (1010196) \u203a Amount",
        )

    def test_and_leaves_a_number_that_is_not_a_guid_as_it_found_it(self):
        from anno_modding_studio.ui.changes_dock import field_label, named_path

        path = "/Values/Tags/Tag[2]"
        self.assertEqual(named_path(path, lambda guid: ""), field_label(path))


class TestExportMessage(unittest.TestCase):
    """The dialog must report the path it actually wrote, per game."""

    def test_reported_path_matches_the_written_one(self):
        import os

        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication

        app = QApplication.instance() or QApplication([])
        from anno_modding_studio.ui.output_panel import OutputPanel

        for game, expected in (
            (games.ANNO_1800, "data/config/export/main/asset/assets.xml"),
            (games.ANNO_117, "data/base/config/export/assets.xml"),
        ):
            loadout = _loadout(game)
            panel = OutputPanel(loadout)
            try:
                with tempfile.TemporaryDirectory() as tmp:
                    root = generator.write_mod_folder(
                        loadout, Path(tmp), generator.build_modops_xml(loadout)
                    )
                    reported = root / games.get(loadout.game).asset_relpath
                    self.assertTrue(reported.is_file(), reported)
                    self.assertTrue(
                        reported.as_posix().endswith(expected), reported.as_posix()
                    )
            finally:
                panel.deleteLater()
        del app


class TestTheExportTabsGameRows(unittest.TestCase):
    """Scripts, Difficulty and Game setup are Anno 117's. They were in view
    until the tab's first refresh, which is the first time it is shown, so
    the window started 128 px taller at its smallest than it needed to be."""

    def setUp(self):
        import os

        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication

        self.app = QApplication.instance() or QApplication([])

    def panel(self, game):
        from anno_modding_studio.ui.output_panel import OutputPanel

        panel = OutputPanel(_loadout(game))
        self.addCleanup(panel.deleteLater)
        return panel

    def rows(self, panel):
        form = panel._scripts_form
        return [form.isRowVisible(row)
                for row in (panel._scripts_row, panel._difficulty_row, panel._setup_row)]

    def test_they_are_in_view_only_for_an_anno_117_loadout_from_the_start(self):
        self.assertEqual(self.rows(self.panel(games.ANNO_1800)), [False] * 3)
        self.assertEqual(self.rows(self.panel(games.ANNO_117)), [True] * 3)

    def test_the_column_asks_for_their_height_only_while_they_are_in_view(self):
        panel = self.panel(games.ANNO_1800)
        inside = panel.left_scroll.widget()
        tab, without = panel.minimumSizeHint().height(), inside.minimumSizeHint().height()
        # Never shown, as the tab is until it is first picked.
        panel.loadout.game = games.ANNO_117
        panel._show_game_rows()
        self.app.processEvents()
        self.assertEqual(self.rows(panel), [True] * 3)
        with_rows = inside.minimumSizeHint().height()
        self.assertGreater(with_rows, without + 60)
        # The column scrolls, so the tab, and with it the window, is no
        # taller at its smallest for what the column holds.
        self.assertEqual(panel.minimumSizeHint().height(), tab)
        panel.loadout.game = games.ANNO_1800
        panel._show_game_rows()
        self.app.processEvents()
        self.assertLess(inside.minimumSizeHint().height(), with_rows - 60)


class TestSavingTheXmlAlone(unittest.TestCase):
    def test_a_write_that_fails_says_so_and_records_nothing(self):
        """A failed write used to raise out of the button and still go on
        to the "Exported" checkpoint that modExported records."""
        import os
        from unittest import mock

        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication

        app = QApplication.instance() or QApplication([])
        from anno_modding_studio.ui import output_panel

        panel = output_panel.OutputPanel(_loadout(games.ANNO_1800))
        exported = []
        panel.modExported.connect(lambda folder, origin: exported.append(folder))
        try:
            with tempfile.TemporaryDirectory() as tmp:
                nowhere = str(Path(tmp) / "missing" / "assets.xml")
                with mock.patch.object(output_panel.QFileDialog, "getSaveFileName",
                                       return_value=(nowhere, "")), \
                        mock.patch.object(output_panel.QMessageBox, "critical") as said, \
                        mock.patch.object(output_panel.QMessageBox, "information") as saved:
                    panel._save_xml()
            said.assert_called_once()
            saved.assert_not_called()
            self.assertEqual(exported, [])
        finally:
            panel.deleteLater()
        del app


class TestWritingSomebodyElsesModinfoBackOut(unittest.TestCase):
    """A loaded mod's modinfo is its author's file, not a form the Studio fills.

    The Studio models six of its fields.  A real modinfo carries a dozen: what
    the mod is incompatible with, what it deprecates, what it must load after,
    what its author warns about, and the ten other languages its name is written
    in.  Rebuilding one from the six published the rest as empty, so exporting a
    whole mods folder quietly unpicked its load order -- 55 of the 107 modinfos
    on this machine declared something that would have gone.
    """

    def loaded(self, declared, game=None, **layer_fields):
        from anno_modding_studio.core import generator
        from anno_modding_studio.core.changes import Loadout, ModLayer

        loadout = Loadout(game=game or games.ANNO_1800)
        loadout.set_layer(
            ModLayer(origin="Their Mod", declared=dict(declared), **layer_fields)
        )
        return json.loads(
            modinfo.build(
                generator.for_mod(loadout, "Their Mod")
            )
        )

    def test_a_declaration_the_studio_does_not_model_survives(self):
        out = self.loaded(
            {
                "ModID": "theirs",
                "IncompatibleIds": ["the other edition"],
                "DeprecateIds": ["the old one"],
                "KnownIssues": {"English": "crashes on Tuesdays"},
            },
            mod_id="theirs",
        )
        self.assertEqual(out["IncompatibleIds"], ["the other edition"])
        self.assertEqual(out["DeprecateIds"], ["the old one"])
        self.assertEqual(out["KnownIssues"], {"English": "crashes on Tuesdays"})

    def test_the_studios_old_1800_misspellings_are_put_right(self):
        """Earlier Studio mods wrote two keys the 1800 loader never reads."""
        out = self.loaded(
            {
                "ModID": "theirs",
                "Dependencies": ["needed mod"],
                "IncompatibleIds": [],
                "DeprecatedIds": ["the old one"],
                "LoadAfterIds": [],
            },
            mod_id="theirs",
        )
        self.assertEqual(out["ModDependencies"], ["needed mod"])
        self.assertEqual(out["DeprecateIds"], ["the old one"])
        self.assertNotIn("Dependencies", out)
        self.assertNotIn("DeprecatedIds", out)
        # Where they were, not moved to the end.
        self.assertEqual(
            list(out)[:5],
            ["ModID", "ModDependencies", "IncompatibleIds", "DeprecateIds",
             "LoadAfterIds"],
        )

    def test_a_misspelling_beside_the_real_key_is_left_alone(self):
        out = self.loaded(
            {
                "ModID": "theirs",
                "ModDependencies": ["real"],
                "Dependencies": ["stale"],
                "DeprecateIds": ["real"],
                "DeprecatedIds": ["stale"],
            },
            mod_id="theirs",
        )
        self.assertEqual(out["ModDependencies"], ["real"])
        self.assertEqual(out["Dependencies"], ["stale"])
        self.assertEqual(out["DeprecateIds"], ["real"])
        self.assertEqual(out["DeprecatedIds"], ["stale"])

    def test_anno_117_dependencies_are_not_respelled(self):
        out = self.loaded(
            {"ModID": "theirs", "Dependencies": {"Require": ["something"]}},
            game=games.ANNO_117,
            mod_id="theirs",
        )
        self.assertEqual(out["Dependencies"]["Require"], ["something"])
        self.assertNotIn("ModDependencies", out)

    def test_so_does_the_load_order_it_declares(self):
        """The one that decides whether mods work together at all."""
        out = self.loaded({"ModID": "theirs", "LoadAfterIds": ["some other mod"]},
                          mod_id="theirs")
        self.assertEqual(out["LoadAfterIds"], ["some other mod"])

    def test_and_a_key_no_version_of_the_studio_has_heard_of(self):
        """Being unrecognised is the strongest reason not to delete something."""
        out = self.loaded({"ModID": "theirs", "CreatorContact": "https://example.com"})
        self.assertEqual(out["CreatorContact"], "https://example.com")

    def test_only_the_english_of_a_localised_field_is_written(self):
        """98 of the 107 modinfos here carry other languages."""
        out = self.loaded(
            {
                "ModName": {
                    "English": "Their Mod",
                    "German": "Ihr Mod",
                    "Chinese": "他们的模组",
                }
            },
            name="Their Mod Renamed",
        )
        self.assertEqual(
            out["ModName"],
            {
                "English": "Their Mod Renamed",
                "German": "Ihr Mod",
                "Chinese": "他们的模组",
            },
        )

    def test_a_field_its_author_never_wrote_stays_unwritten(self):
        """A third of the modinfos exported from the mod.io census came back
        with a ``CreatorName: ""`` they never had, and some with a Category of
        "Gameplay" -- which in Anno 117 is part of where a mod loads."""
        declared = {"ModID": "theirs", "ModName": {"English": "Their Mod"}}
        out = self.loaded(declared, game=games.ANNO_117, mod_id="theirs", name="Their Mod")
        self.assertEqual(out, declared)

    def test_nor_is_its_folder_s_name_written_in_as_its_own(self):
        """A mod whose modinfo gives no name is *shown* under its folder's."""
        declared = {"ModID": "theirs", "Version": "3"}
        out = self.loaded(declared, mod_id="theirs", version="3", name="Their Mod")
        self.assertEqual(out, declared)

    def test_a_field_written_as_plain_text_stays_plain_text(self):
        declared = {"ModName": "Their Mod", "Description": "What it does."}
        same = self.loaded(declared, name="Their Mod", description="What it does.")
        self.assertEqual(same, declared)
        renamed = self.loaded(declared, name="Renamed", description="What it does.")
        self.assertEqual(renamed["ModName"], "Renamed")
        self.assertEqual(renamed["Description"], "What it does.")

    def test_but_one_filled_in_here_is_added(self):
        out = self.loaded({"ModID": "theirs"}, mod_id="theirs", creator="Me")
        self.assertEqual(out["CreatorName"], "Me")

    def test_what_the_studio_does_own_is_still_written_over_it(self):
        out = self.loaded(
            {"ModID": "theirs", "Version": "1.0", "CreatorName": "Them"},
            version="2.0",
            creator="Them",
        )
        self.assertEqual(out["Version"], "2.0")

    def test_a_patch_declares_that_it_loads_after_what_it_patches(self):
        from anno_modding_studio.core import generator
        from anno_modding_studio.core.changes import Loadout, ModLayer

        loadout = Loadout(game=games.ANNO_1800)
        loadout.set_layer(ModLayer(origin="Their Mod", mod_id="theirs"))
        loadout.set_layer(
            ModLayer(origin="Their Mod (my patch)", patches="Their Mod")
        )
        out = json.loads(
            modinfo.build(
                generator.for_mod(loadout, "Their Mod (my patch)")
            )
        )
        self.assertEqual(out["LoadAfterIds"], ["theirs"])

    def test_and_says_so_in_the_shape_the_file_already_uses(self):
        """117 nests it under Dependencies; a patch of a 117 mod has to use
        the same spelling the rest of that file does."""
        from anno_modding_studio.core import generator
        from anno_modding_studio.core.changes import Loadout, ModLayer

        loadout = Loadout(game=games.ANNO_117)
        loadout.set_layer(ModLayer(origin="Their Mod", mod_id="theirs"))
        loadout.set_layer(
            ModLayer(
                origin="Their Mod (my patch)",
                patches="Their Mod",
                declared={"Dependencies": {"Require": ["something"], "LoadAfter": []}},
            )
        )
        out = json.loads(
            modinfo.build(
                generator.for_mod(loadout, "Their Mod (my patch)")
            )
        )
        self.assertEqual(out["Dependencies"]["LoadAfter"], ["theirs"])
        # What it required stays, and the mod it patches is required too: a
        # patch running without it is the loader's to say.
        self.assertEqual(out["Dependencies"]["Require"], ["something", "theirs"])

    def loadout_own(self, game, **details):
        from anno_modding_studio.core import generator
        from anno_modding_studio.core.changes import Loadout, ModLayer, own_record

        loadout = Loadout(
            game=game, own=own_record(name="%s mod list patch" % games.get(game).label, **details)
        )
        loadout.set_layer(ModLayer(
            origin="[Gameplay] Efficient Celtic Charcoal", name="Efficient Celtic Charcoal",
            category="Gameplay", mod_id="efficient-celtic-charcoal",
        ))
        loadout.set_layer(ModLayer(
            origin="[Map] Easy Seed Search", name="Easy Seed Search", category="Map",
            mod_id="easy-seed-search", load_after=["*"],
        ))
        return loadout, json.loads(modinfo.build(generator.for_mod(loadout, "")))

    def test_a_loadouts_own_mod_declares_that_it_loads_after_everything(self):
        """Nothing else puts it last: by name it sorts in front of every
        ``[Gameplay]`` mod from B onwards, in both games, and the mods it was
        written to override then override it."""
        _loadout, out = self.loadout_own(games.ANNO_1800, loads_last=True)
        self.assertEqual(out["LoadAfterIds"], ["*", "easy-seed-search"])
        _loadout, out = self.loadout_own(games.ANNO_117, loads_last=True)
        self.assertEqual(out["Dependencies"]["LoadAfter"], ["*", "easy-seed-search"])
        self.assertEqual(out["Dependencies"]["Require"], [])

    def test_and_so_the_game_loads_it_where_the_studio_says_it_does(self):
        """The Studio ranks the loadout's own mod last everywhere; read back
        the way a mods folder is, the modinfo it writes has to land there."""
        from anno_modding_studio.core import generator, switches

        for game in (games.ANNO_1800, games.ANNO_117):
            loadout, out = self.loadout_own(game, loads_last=True)
            mods = {
                layer.origin: {
                    "category": layer.category, "mod_name": layer.name,
                    "mod_id": layer.mod_id, "load_after": list(layer.load_after),
                }
                for layer in loadout.layers
            }
            folder = generator.folder_for(loadout, "")
            after = out.get("LoadAfterIds") or out["Dependencies"]["LoadAfter"]
            mods[folder] = {
                "category": out["Category"]["English"], "mod_name": out["ModName"]["English"],
                "mod_id": out["ModID"], "load_after": after,
            }
            self.assertEqual(switches.order(game, mods)[-1], folder, game)
            # Left to its name, it is not: the hazard this declaration is for.
            mods[folder]["load_after"] = []
            self.assertNotEqual(switches.order(game, mods)[-1], folder, game)
            self.assertEqual(max(["", *loadout.origins(loaded=True)], key=loadout.layer_rank), "")

    def test_a_mod_written_to_publish_declares_no_such_thing(self):
        """It has no business loading after mods it has never heard of."""
        _loadout, out = self.loadout_own(games.ANNO_1800)
        self.assertNotIn("LoadAfterIds", out)
        _loadout, out = self.loadout_own(games.ANNO_117)
        self.assertEqual(out["Dependencies"]["LoadAfter"], [])

    def test_a_patch_on_a_loadout_still_names_only_what_it_patches(self):
        from anno_modding_studio.core import generator
        from anno_modding_studio.core.changes import ModLayer

        loadout, _out = self.loadout_own(games.ANNO_1800, loads_last=True)
        loadout.set_layer(ModLayer(
            origin="[Gameplay] Efficient Celtic Charcoal (my patch)",
            patches="[Gameplay] Efficient Celtic Charcoal",
        ))
        out = json.loads(modinfo.build(generator.for_mod(
            loadout, "[Gameplay] Efficient Celtic Charcoal (my patch)"
        )))
        self.assertEqual(out["LoadAfterIds"], ["efficient-celtic-charcoal"])

    def test_a_patch_for_a_late_mod_is_late_too(self):
        """"A mod without a * cannot load after a mod with a *": naming the
        late mod alone sorted the patch into the phase before it."""
        from anno_modding_studio.core import generator, switches
        from anno_modding_studio.core.changes import ModLayer

        for game, declared in (
            (games.ANNO_1800, None),
            (games.ANNO_117, None),
            (games.ANNO_1800, {"ModID": "mine", "LoadAfterIds": ["other"]}),
            (games.ANNO_117, {"ModID": "mine", "Dependencies": {"LoadAfter": ["other"]}}),
        ):
            loadout, own = self.loadout_own(game, loads_last=True)
            patch = "[Map] Easy Seed Search (my patch)"
            loadout.set_layer(ModLayer(
                origin=patch, name="Easy Seed Search (my patch)", category="Map",
                mod_id="mine", patches="[Map] Easy Seed Search", declared=declared,
            ))
            out = json.loads(modinfo.build(generator.for_mod(loadout, patch)))
            after = out.get("LoadAfterIds") or out["Dependencies"]["LoadAfter"]
            # After whatever a 117 file already listed: its Dependencies are
            # its author's, and only added to.
            self.assertEqual(after[-2:], ["*", "easy-seed-search"], (game, declared))
            # The loadout's own mod names it, being a late mod now.
            own = json.loads(modinfo.build(generator.for_mod(loadout, "")))
            own_after = own.get("LoadAfterIds") or own["Dependencies"]["LoadAfter"]
            self.assertEqual(own_after, ["*", "easy-seed-search", "mine"])
            mods = {
                layer.origin: {
                    "category": layer.category, "mod_name": layer.name,
                    "mod_id": layer.mod_id, "load_after": list(layer.load_after),
                }
                for layer in loadout.layers
            }
            mods[patch]["load_after"] = after
            mods["own"] = {"category": "Gameplay", "mod_name": "Aaa", "mod_id": own["ModID"],
                           "load_after": own_after}
            self.assertEqual(
                switches.order(game, mods)[-3:], ["[Map] Easy Seed Search", patch, "own"], game
            )

    def test_a_mod_of_your_own_is_still_generated_whole(self):
        """There is no author's file to preserve, so nothing changes for it."""
        from anno_modding_studio.core.changes import Loadout, own_record

        out = json.loads(
            modinfo.build(
                Loadout(own=own_record(name="Mine", creator="Me"), game=games.ANNO_1800)
            )
        )
        self.assertEqual(out["ModName"], {"English": "Mine"})
        self.assertEqual(out["CreatorName"], "Me")
        self.assertEqual(out["IncompatibleIds"], [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
