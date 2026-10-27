"""What the game's loader said a mod's ops did not do -- a path that matched
nothing, a file it includes that was not there -- kept from its log and
shown with the mod, rather than thrown away."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import _isolation  # noqa: F401 -- first: keeps the suite out of real files under any runner

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from support import PlayingWindow  # noqa: E402

from anno_modding_studio.core import loaderlog  # noqa: E402
from anno_modding_studio.core.changes import Change, ModLayer  # noqa: E402
from anno_modding_studio.core.games import ANNO_1800  # noqa: E402

# As the machine's own log writes them -- and twice, as it does: the loader
# runs its patching more than once a start.
LOG = """[2026-09-26 21:51:14.000] [info] Load mods
[2026-09-26 21:51:14.100] [info] Load [Item] Town Hall Rebalance (Kurila) (1.2, town_hall_rebalance_kurila) from mods\\[Item] Town Hall Rebalance (Kurila)
[2026-09-26 21:51:14.200] [info] Load [Ornamental] Quay System (2.2, JDG_06_quay) from mods\\[Gameplay] New Docklands\\JDG_06_quay
[2026-09-26 21:51:15.000] [warning] [Item] Town Hall Rebalance (Kurila): No matching node for Path "//Asset[Values/Standard/GUID='191732']/Values/ItemEffect" (data/config/export/main/asset/assets.xml:1036)
[2026-09-26 21:51:15.100] [warning] [Ornamental] Quay System: No matching node for Path "//Asset[Values/Standard/GUID='1']" (data/config/export/main/asset/assets.xml:3)
[2026-09-26 21:51:15.200] [error] [Ships] Military Attention: Include file missing or empty: data\\hier0nimus\\a.include.xml (data/config/export/main/asset/assets.xml:111)
[2026-09-26 21:51:15.300] [error] modinfo.json of Broken Mod has errors
[2026-09-26 21:53:15.000] [warning] [Item] Town Hall Rebalance (Kurila): No matching node for Path "//Asset[Values/Standard/GUID='191732']/Values/ItemEffect" (data/config/export/main/asset/assets.xml:1036)
[2026-09-26 21:53:20.000] [info] Loading complete
"""


def _read(text=LOG) -> loaderlog.LoaderLog:
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "mod-loader.log"
        path.write_text(text, encoding="utf-8")
        return loaderlog.read(ANNO_1800, path)


class TestReadingThem(unittest.TestCase):
    def test_each_once_with_the_mod_it_is_about(self):
        log = _read()
        self.assertEqual(
            [(w.mod_id, w.kind, w.where) for w in log.op_warnings],
            [("town_hall_rebalance_kurila", loaderlog.NO_MATCH, "data/config/export/main/asset/assets.xml:1036"),
             ("JDG_06_quay", loaderlog.NO_MATCH, "data/config/export/main/asset/assets.xml:3"),
             ("", loaderlog.NO_INCLUDE, "data/config/export/main/asset/assets.xml:111")],
        )
        self.assertEqual(log.op_warnings[2].what, "data\\hier0nimus\\a.include.xml")
        # Still not problems about whether a mod loaded.
        self.assertEqual(log.problems, [("error", "modinfo.json of Broken Mod has errors")])

    def test_one_that_cannot_be_put_to_a_mod_is_kept_as_it_is(self):
        """Not every line about an op is in the shape read above; one that is
        not is still something the loader said, and is not thrown away."""
        for body in (
            'No matching node for Path "//Asset"',
            '[A] Mod: No matching node for Path "//Asset" (assets.xml:3) and more besides',
        ):
            with self.subTest(body=body):
                log = _read("[2026-09-26 21:51:15.000] [warning] %s\n" % body)
                self.assertEqual(log.op_warnings, [])
                self.assertEqual(log.problems, [("warning", body)])

    def test_found_by_modid_and_a_bundled_one_not_by_its_host(self):
        log = _read()
        self.assertEqual(len(log.warnings_for({"town_hall_rebalance_kurila"})), 1)
        self.assertEqual(len(log.warnings_for({"[item] town hall rebalance (kurila)"})), 1)
        self.assertEqual(len(log.warnings_for({"jdg_06_quay"})), 1)
        self.assertEqual(log.warnings_for({"[gameplay] new docklands"}), [])

    def test_the_lines_for_a_tooltip(self):
        log = _read()
        lines = loaderlog.describe_warnings(log.op_warnings, log.started, most=1)
        self.assertTrue(lines[0].startswith("When the game last started (26 Sep 21:51), 2 of its ops "
                                            "matched nothing. This is usually a mod it expects"))
        self.assertEqual(lines[1], "  //Asset[Values/Standard/GUID='191732']/Values/ItemEffect  "
                                   "(data/config/export/main/asset/assets.xml:1036)")
        self.assertEqual(lines[2], "  and 1 more")
        self.assertEqual(lines[3], "a file it includes was missing or empty: data\\hier0nimus\\a.include.xml")
        self.assertEqual(loaderlog.describe_warnings([]), [])

    def test_the_card_says_how_many_and_where_to_look(self):
        log = _read()
        checked = loaderlog.Check(log=log, loaded=2)
        self.assertIn("3 ops in 3 mods did nothing", checked.describe())
        self.assertTrue(checked.ok)  # not a fault of Play's
        checked.changed = True
        self.assertNotIn("did nothing", checked.describe())

    def test_those_of_mods_with_no_tooltip_are_named_on_the_card(self):
        """Counted in the card's sentence that points at the changes panel's
        tooltips, warnings from mods the loadout does not have -- a mod the
        game's browser installed, one bundled in a mod not listed -- had no
        tooltip to be seen in (A11-10). They are said apart, by name."""
        from anno_modding_studio.core import truth
        from anno_modding_studio.core.changes import Loadout

        loadout = Loadout(game=ANNO_1800)
        loadout.layers = [ModLayer(origin="[Item] Town Hall Rebalance (Kurila)", name="Town Hall Rebalance",
                                   mod_id="town_hall_rebalance_kurila", order=1)]
        log = _read()
        checked = loaderlog.check(log, truth.Truth(game=ANNO_1800, folder=Path("mods")), loadout)
        self.assertEqual([warning.mod for warning in checked.elsewhere],
                         ["[Ornamental] Quay System", "[Ships] Military Attention"])
        said = checked.describe()
        self.assertIn(
            "1 op in 1 mod did nothing: a path that matched nothing, or a file it includes that was "
            "not there. Each mod's tooltip in the changes panel lists its own. 2 more did nothing in "
            "2 mods this loadout does not have: [Ornamental] Quay System, [Ships] Military Attention.",
            said,
        )
        # With none of them the loadout's, nothing points at a tooltip.
        loadout.layers = []
        said = loaderlog.check(log, truth.Truth(game=ANNO_1800, folder=Path("mods")), loadout).describe()
        self.assertIn(
            "3 ops in 3 mods this loadout does not have ([Item] Town Hall Rebalance (Kurila), "
            "[Ornamental] Quay System, [Ships] Military Attention) did nothing: a path that matched "
            "nothing, or a file it includes that was not there.",
            said,
        )
        self.assertNotIn("tooltip", said)

    def test_the_card_and_the_tooltip_match_a_mod_by_one_rule(self):
        """A bundled mod is found by its ModID, a mod of its own by its folder
        too: the rule the changes panel's tooltip goes by, which the card now
        shares."""
        bundled = ModLayer(origin="[Gameplay] New Docklands/JDG_06_quay", name="Quay", mod_id="", order=1)
        self.assertEqual(loaderlog.layer_keys(bundled), {"jdg_06_quay"})
        own = ModLayer(origin="[Item] Town Hall Rebalance (Kurila)", name="T", mod_id="Kurila_THR", order=1)
        self.assertEqual(loaderlog.layer_keys(own), {"kurila_thr", "[item] town hall rebalance (kurila)"})


class TestInTheChangesPanel(PlayingWindow):
    def test_a_mod_s_tooltip_lists_its_own(self):
        from anno_modding_studio.ui import changes_dock

        loadout = self.window.loadout
        loadout.layers = [
            ModLayer(origin="[Item] Town Hall Rebalance (Kurila)", name="Town Hall Rebalance",
                     mod_id="town_hall_rebalance_kurila", order=1),
            ModLayer(origin="Clean", name="Clean", mod_id="clean", order=2),
        ]
        for n, layer in enumerate(loadout.layers):
            change = Change(guid=str(n + 1), asset_label="a", path="Values/Standard/Name", tag="Name",
                            op="replace", old_value="a", new_value="b", origin=layer.origin)
            loadout.changes[change.key] = change
        panel = self.window.changes_panel
        with mock.patch.object(loaderlog, "read_cached", return_value=_read()):
            panel.refresh()
        rows = {}
        for position in range(panel.tree.topLevelItemCount()):
            item = panel.tree.topLevelItem(position)
            if item.data(0, changes_dock.KIND_ROLE) == "mod":
                rows[item.data(0, changes_dock.ORIGIN_ROLE)] = item
        self.assertIn("one of its ops matched nothing", rows["[Item] Town Hall Rebalance (Kurila)"].toolTip(0))
        self.assertNotIn("matched nothing", rows["Clean"].toolTip(0))


if __name__ == "__main__":
    unittest.main()
