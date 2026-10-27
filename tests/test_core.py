"""Core engine tests: indexing, path generation, verification, XML output."""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

import _isolation  # noqa: F401 -- first: keeps the suite out of real files under any runner
from lxml import etree

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from anno_modding_studio.core import (  # noqa: E402
    disk,
    games,
    generator,
    paths,
)
from anno_modding_studio.core import (  # noqa: E402
    index as index_mod,
)
from anno_modding_studio.core import (  # noqa: E402
    refs as refs_mod,
)
from anno_modding_studio.core.changes import (  # noqa: E402
    AIM_MAKE,
    AIM_MANAGE,
    AIM_TWEAK,
    LOADOUT_FORMAT,
    Change,
    Loadout,
    LoadoutError,
    ModLayer,
    Op,
    diff_changes,
    own_record,
)

sys.path.insert(0, str(ROOT / "tools"))
import make_sample_assets  # noqa: E402


class CoreTestBase(unittest.TestCase):
    """Builds one sample index for the whole suite."""

    @classmethod
    def setUpClass(cls):
        import tempfile

        cls._tmp = tempfile.TemporaryDirectory()
        tmp = Path(cls._tmp.name)
        cls.assets_path = tmp / "assets.xml"
        cls.texts_path = tmp / "texts_english.xml"
        cls.assets_path.write_text(make_sample_assets.build_assets_xml(60), encoding="utf-8")
        cls.texts_path.write_text(make_sample_assets.build_texts_xml(), encoding="utf-8")
        cls.db_path = tmp / "index.sqlite"
        cls.count = index_mod.build_index(cls.assets_path, cls.db_path)
        cls.index = index_mod.AssetIndex(cls.db_path, cls.assets_path)

    @classmethod
    def tearDownClass(cls):
        cls.index.close()
        cls._tmp.cleanup()


class TestIndex(CoreTestBase):
    def test_indexed_every_asset(self):
        expected = self.assets_path.read_text(encoding="utf-8").count("<GUID>")
        self.assertEqual(self.count, expected)
        self.assertEqual(self.index.asset_count, expected)

    def test_group_path_is_nested(self):
        row = self.index.asset_row("1010340")
        self.assertEqual(row.group_path, "Buildings / Production")
        self.assertEqual(row.template, "FactoryBuilding7")

    def test_group_names_do_not_leak_into_stored_xml(self):
        blob = self.index.asset_xml("1010340")
        self.assertNotIn(b"_amsGroupName", blob)

    def test_search_by_name_guid_and_template(self):
        self.assertTrue(any(r.guid == "1010340" for r in self.index.search("lumberjack")))
        self.assertTrue(any(r.guid == "1010340" for r in self.index.search("1010340")))
        rows = self.index.search("", template="ResidenceBuilding7")
        self.assertEqual(len(rows), 4)

    def test_template_counts_for_narrows_to_a_search(self):
        """Backs the Template filter's own narrowing: with a query, only
        templates that show up among its matches should come back."""
        full = {name for name, _n in self.index.template_counts_for("")}
        self.assertEqual(full, set(self.index.templates()))

        narrowed = dict(self.index.template_counts_for("lumberjack"))
        self.assertIn("FactoryBuilding7", narrowed)
        self.assertNotIn("ResidenceBuilding7", narrowed)
        self.assertLess(len(narrowed), len(full))

    def test_template_counts_for_reflects_the_narrowed_set_not_the_whole_game(self):
        """The count beside each template must describe the current results,
        not how common that template is overall -- otherwise picking it would
        silently widen the search back out to everything of that template."""
        narrow = dict(self.index.template_counts_for("lumberjack"))
        wide = dict(self.index.template_counts_for("lumber"))
        self.assertLessEqual(narrow["FactoryBuilding7"], wide["FactoryBuilding7"])

    def test_search_tokens_are_anded(self):
        rows = self.index.search("residence tier01")
        self.assertTrue(rows)
        self.assertTrue(all("tier01" in r.name for r in rows))

    def test_group_filter_includes_subgroups(self):
        rows = self.index.search("", group="Buildings")
        guids = {r.guid for r in rows}
        self.assertIn("1010030", guids)  # Buildings / Residence
        self.assertIn("1010340", guids)  # Buildings / Production

    def test_exact_guid_ranks_first(self):
        rows = self.index.search("1010340")
        self.assertEqual(rows[0].guid, "1010340")

    def test_exact_guid_wins_even_when_filters_exclude_it(self):
        """Typing a GUID must always find that asset, filters or not."""
        rows = self.index.search("1010340", template="Product")
        self.assertEqual(rows[0].guid, "1010340")

    def test_guid_lookup_is_not_capped_out_by_common_substrings(self):
        rows = self.index.search("1010030")
        self.assertEqual(rows[0].guid, "1010030")
        self.assertEqual(len({r.guid for r in rows}), len(rows))  # no duplicate row

    def test_texts_file_provides_display_names(self):
        loaded = self.index.load_texts(self.texts_path)
        self.assertGreater(loaded, 0)
        self.assertEqual(self.index.asset_row("1010340").display, "Lumberjack's Hut")
        # ...and is searchable.
        self.assertTrue(any(r.guid == "1010340" for r in self.index.search("lumberjack's hut")))

    def test_one_unreadable_asset_does_not_cost_the_whole_sweep(self):
        """find_matching reads every asset in the cache for one question.

        A mod that targets by search makes the Studio look at a hundred
        thousand assets; a single damaged row must not end that. Three ways a
        row can be unreadable, all of them ending the same way -- that asset is
        not in the answer, and the sweep carries on.
        """
        import shutil
        import sqlite3
        import tempfile
        import zlib

        folder = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, folder, True)
        db = folder / "damaged.sqlite"
        shutil.copy(self.db_path, db)

        con = sqlite3.connect(str(db))
        guids = [row[0] for row in con.execute("SELECT guid FROM assets LIMIT 2")]
        # Not compressed at all, and compressed but not XML.
        con.execute("UPDATE assets SET xml=? WHERE guid=?", (b"raw bytes", guids[0]))
        con.execute(
            "UPDATE assets SET xml=? WHERE guid=?",
            (zlib.compress(b"<Asset><never-closed>"), guids[1]),
        )
        con.commit()
        con.close()

        index = index_mod.AssetIndex(db, self.assets_path)
        self.addCleanup(index.close)
        # The third way: an expression that compiles and then fails on the
        # document, which is what an undefined variable does.
        found = index.find_matching(
            ["//Asset/Values/Standard/GUID", "//Asset[$missing]"]
        )

        matched = found["//Asset/Values/Standard/GUID"]
        self.assertGreater(len(matched), 1, "the sweep stopped early")
        for guid in guids:
            with self.subTest(guid=guid):
                self.assertNotIn(guid, matched)
        self.assertEqual(found["//Asset[$missing]"], [])

    def test_an_edit_in_the_middle_is_noticed_even_at_the_same_length(self):
        """The reason the key hashes the whole file rather than sampling it.

        Sampling four megabytes from each end plus the size missed exactly this
        -- a value retyped to the same width, far from either end -- and served
        the index built against the old bytes for it, silently.
        """
        import shutil
        import tempfile

        folder = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, folder, True)
        copy = folder / "assets.xml"
        # Big enough that the middle is nowhere near either sampled end.
        body = b"<Assets>" + b"<Asset><V>30.00</V></Asset>" * 400000 + b"</Assets>"
        copy.write_bytes(body)
        before = index_mod.cache_path_for(copy)

        middle = body.index(b"30.00", len(body) // 2)
        copy.write_bytes(body[:middle] + b"31.00" + body[middle + 5:])

        self.assertEqual(copy.stat().st_size, len(body), "the length must not move")
        self.assertNotEqual(before, index_mod.cache_path_for(copy))

    def test_an_unchanged_file_is_hashed_once(self):
        """A launch asked for the same 289 MB fingerprint three times; it is
        held while the file's size and modification time stand."""
        import os
        import shutil
        import tempfile
        from unittest import mock

        folder = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, folder, True)
        copy = folder / "assets.xml"
        shutil.copy(self.assets_path, copy)
        with mock.patch.object(index_mod, "open", wraps=open, create=True) as opened:
            first = index_mod.fingerprint(copy)
            self.assertEqual(index_mod.fingerprint(copy), first)
            self.assertEqual(opened.call_count, 1)
            stat = copy.stat()
            os.utime(copy, (stat.st_atime, stat.st_mtime + 600))
            self.assertEqual(index_mod.fingerprint(copy), first)
            self.assertEqual(opened.call_count, 2, "touched: read again")

    def test_cache_path_changes_when_the_contents_change(self):
        import os
        import shutil
        import tempfile

        folder = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, folder, True)
        copy = folder / "assets.xml"
        shutil.copy(self.assets_path, copy)
        first = index_mod.cache_path_for(copy)

        # Written again, byte for byte: re-extracting the game's files is the
        # commonest way this happens, and rebuilding the index for it costs
        # minutes and buys nothing.
        stat = copy.stat()
        os.utime(copy, (stat.st_atime, stat.st_mtime + 600))
        self.assertEqual(first, index_mod.cache_path_for(copy))

        copy.write_text(
            copy.read_text(encoding="utf-8").replace("<Assets>", "<Assets> "),
            encoding="utf-8",
        )
        self.assertNotEqual(first, index_mod.cache_path_for(copy))


class TestPaths(CoreTestBase):
    def _asset(self, guid):
        return self.index.asset_element(guid)

    def test_simple_unique_path(self):
        root = self._asset("1010340")
        node = root.find("Values/Factory7/CycleTime")
        gen = paths.path_for(root, node)
        self.assertEqual(gen.path, "/Values/Factory7/CycleTime")
        self.assertFalse(gen.positional)

    def test_a_lone_list_entry_can_be_keyed_anyway(self):
        """For a file whose own earlier ops add beside it: see ``path_for``."""
        from lxml import etree

        root = etree.fromstring(
            b"<Asset><Values><Standard><GUID>1</GUID></Standard>"
            b"<L><Item><GUID>7</GUID></Item></L></Values></Asset>"
        )
        entry = root.find("Values/L/Item")
        self.assertEqual(paths.path_for(root, entry).path, "/Values/L/Item")
        self.assertEqual(paths.path_for(root, entry, keyed=True).path, "/Values/L/Item[GUID='7']")
        standard = root.find("Values/Standard")
        self.assertEqual(paths.path_for(root, standard, keyed=True).path, "/Values/Standard")

    def test_the_asset_itself_is_the_empty_path(self):
        root = self._asset("1010340")
        self.assertEqual(paths.path_for(root, root).path, "")
        self.assertEqual(paths.resolve(root, ""), [root])

    def test_a_node_from_outside_the_asset_has_no_path(self):
        """P2-3: the empty path is the asset itself, so a node of another asset
        given one would be a ModOp aimed at the whole of this one."""
        root = self._asset("1010340")
        elsewhere = self._asset("1010030").find("Values")
        with self.assertRaises(ValueError):
            paths.path_for(root, elsewhere)
        with self.assertRaises(ValueError):
            paths.path_for(root, None)

    def test_repeated_siblings_use_content_key(self):
        root = self._asset("1010340")
        # Maintenances has two <Item> entries distinguished by <Product>.
        node = root.xpath("Values/Maintenance/Maintenances/Item[Product='1010365']/Amount")[0]
        gen = paths.path_for(root, node)
        self.assertEqual(
            gen.path, "/Values/Maintenance/Maintenances/Item[Product='1010365']/Amount"
        )
        self.assertFalse(gen.positional)

    def test_positional_fallback_when_nothing_identifies_the_node(self):
        root = self._asset("1010030")
        node = root.findall("Values/Tags/Tag")[1]
        gen = paths.path_for(root, node)
        self.assertEqual(gen.path, "/Values/Tags/Tag[2]")
        self.assertTrue(gen.positional)
        self.assertIn("positional", gen.warning)

    def test_every_generated_path_resolves_to_exactly_one_node(self):
        """The property that actually matters: paths must be unique selectors."""
        checked = 0
        for guid in ("1010030", "1010340", "1010343", "1010196"):
            root = self._asset(guid)
            for node in root.iter():
                if len(node) or not isinstance(node.tag, str) or node is root:
                    continue
                gen = paths.path_for(root, node)
                matches = paths.resolve(root, gen.path)
                self.assertEqual(
                    len(matches), 1, "%s -> %s matched %d" % (guid, gen.path, len(matches))
                )
                self.assertIs(matches[0], node)
                checked += 1
        self.assertGreater(checked, 30)

    def test_quoting_picks_a_usable_literal(self):
        xml = b"<Asset><Values><L><I><Name>it's</Name><V>1</V></I>"
        xml += b"<I><Name>other</Name><V>2</V></I></L></Values></Asset>"
        root = etree.fromstring(xml)
        node = root.xpath("Values/L/I")[0].find("V")
        gen = paths.path_for(root, node)
        self.assertEqual(gen.path, '/Values/L/I[Name="it\'s"]/V')
        self.assertEqual(len(paths.resolve(root, gen.path)), 1)

    def test_quoting_survives_a_value_holding_both_kinds_of_quote(self):
        """XPath 1.0 has no escape, so this needs concat() or it matches nothing.

        Wrapping such a value in either quote does not merely mis-target -- it
        produces a malformed expression that silently selects zero nodes, and
        path_for would report it as a perfectly good non-positional path.
        """
        value = "it's a " + '"thing"'
        xml = (
            b'<Asset><Values><L>'
            b'<I><Name>it&apos;s a &quot;thing&quot;</Name><V>1</V></I>'
            b'<I><Name>other</Name><V>2</V></I>'
            b'</L></Values></Asset>'
        )
        root = etree.fromstring(xml)
        node = root.xpath("Values/L/I")[0].find("V")
        gen = paths.path_for(root, node)
        self.assertFalse(gen.positional)
        self.assertIn("concat(", gen.path)
        matches = paths.resolve(root, gen.path)
        self.assertEqual(len(matches), 1)
        self.assertIs(matches[0], node)
        self.assertEqual(root.xpath("Values/L/I")[0].findtext("Name"), value)

    def test_a_stored_path_that_no_longer_parses_matches_nothing(self):
        """A path is read back out of a saved loadout, not written afresh.

        Hand-edited, half-migrated, or written by a Studio that spelled things
        differently, it can be something XPath will not accept at all -- and
        the answer to that is no matches, not an exception out of the middle of
        a verification sweep.
        """
        root = self._asset("1010340")
        for broken in ("/Values/[", "/Values/Factory7[", "(((", "/Values/a b c"):
            with self.subTest(path=broken):
                self.assertEqual(paths.resolve(root, broken), [])
                ok, why = paths.verify(root, broken, None)
                self.assertFalse(ok)
                self.assertIn("matches nothing", why)

    def test_verify_detects_drift_and_bad_paths(self):
        root = self._asset("1010340")
        ok, _msg = paths.verify(root, "/Values/Factory7/CycleTime", "30.00")
        self.assertTrue(ok)
        ok, msg = paths.verify(root, "/Values/Factory7/CycleTime", "999")
        self.assertFalse(ok)
        self.assertIn("expected", msg)
        ok, msg = paths.verify(root, "/Values/Nope/Missing", None)
        self.assertFalse(ok)
        ok, msg = paths.verify(root, "/Values/Maintenance/Maintenances/Item", None)
        self.assertFalse(ok)
        self.assertIn("would change them all", msg)


class TestChanges(unittest.TestCase):
    def _change(self, **kw):
        base = dict(
            guid="1010340",
            asset_label="Lumberjack",
            path="/Values/Factory7/CycleTime",
            tag="CycleTime",
            old_value="30.00",
            new_value="15.00",
        )
        base.update(kw)
        return Change(**base)

    def test_one_mods_share_is_in_the_same_order_as_the_whole(self):
        """Asked for one mod's changes, the loadout sorts only that mod's --
        and the answer is what sorting them all and keeping that mod's was."""
        loadout = Loadout()
        for origin in ("B", "A"):
            loadout.set_layer(ModLayer(origin=origin, name=origin))
        for n, (origin, label) in enumerate(
            [("A", "zeta"), ("B", "Alpha"), ("A", "alpha"), ("B", "mu"), ("A", "Mu")]
        ):
            loadout.set_change(self._change(
                origin=origin, asset_label=label, guid=str(100 + n), path="/Values/X%d" % n,
                authored=(n % 2 == 0),
            ))
        whole = loadout.ordered()
        for origin in ("A", "B"):
            with self.subTest(origin):
                self.assertEqual(loadout.changes_of(origin), [c for c in whole if c.origin == origin])
                self.assertEqual(loadout.authored_of(origin),
                                 [c for c in whole if c.origin == origin and c.authored])
        self.assertEqual(loadout.authored(), [c for c in whole if c.authored])

    def test_a_mod_added_later_loads_after_what_it_names(self):
        """Among mods that already had a place it was slotted in by its name
        alone: B, loading after Z, landed in front of Z."""
        for game in ("anno1800", "anno117"):
            with self.subTest(game):
                loadout = Loadout(game=game)
                for origin in ("A", "Z"):
                    loadout.set_layer(ModLayer(origin=origin, name=origin, mod_id=origin.lower()))
                loadout.set_layer(ModLayer(origin="B", name="B", mod_id="b", load_after=["Z"]))
                self.assertEqual([layer.origin for layer in loadout.layers], ["A", "Z", "B"])
                # One naming a mod that is not here goes by its name, as before.
                loadout.set_layer(ModLayer(origin="C", name="C", mod_id="c", load_after=["gone"]))
                self.assertEqual([layer.origin for layer in loadout.layers], ["A", "Z", "B", "C"])
                # And one loading after everything goes after every mod that does not.
                loadout.set_layer(ModLayer(origin="0", name="0", mod_id="0", load_after=["*"]))
                self.assertEqual([layer.origin for layer in loadout.layers][-1], "0")
                # The ones that had a place keep it.
                self.assertEqual([layer.order for layer in loadout.layers], [1, 2, 3, 4, 5])

    def test_a_late_mod_added_later_takes_its_place_among_the_late_ones(self):
        """The loader orders late mods among themselves by ModID. One added
        later was put straight after the last mod without a star, in front of
        a late mod whose ModID sorts before its own."""
        for game in ("anno1800", "anno117"):
            with self.subTest(game):
                loadout = Loadout(game=game)
                loadout.set_layer(ModLayer(origin="A", name="A", mod_id="a"))
                loadout.set_layer(ModLayer(origin="L", name="L", mod_id="l", load_after=["*"]))
                loadout.set_layer(ModLayer(origin="Z", name="Z", mod_id="z", load_after=["*"]))
                self.assertEqual([layer.origin for layer in loadout.layers], ["A", "L", "Z"])
                # One whose ModID sorts first goes in front of the late ones.
                loadout.set_layer(ModLayer(origin="K", name="K", mod_id="k", load_after=["*"]))
                self.assertEqual([layer.origin for layer in loadout.layers], ["A", "K", "L", "Z"])
                # A mod without a star that names a late one gets nothing from
                # it, as in the loader: it stays where its name put it.
                loadout.set_layer(ModLayer(origin="B", name="B", mod_id="b", load_after=["z"]))
                self.assertEqual(
                    [layer.origin for layer in loadout.layers], ["A", "B", "K", "L", "Z"]
                )

    def test_editing_back_to_original_drops_the_change(self):
        loadout = Loadout()
        loadout.set_change(self._change())
        self.assertEqual(len(loadout), 1)
        loadout.set_change(self._change(new_value="30.00"))
        self.assertEqual(len(loadout), 0)

    def test_same_path_is_updated_not_duplicated(self):
        loadout = Loadout()
        loadout.set_change(self._change())
        loadout.set_change(self._change(new_value="12.00"))
        self.assertEqual(len(loadout), 1)
        self.assertEqual(next(iter(loadout)).new_value, "12.00")

    def test_round_trip_through_disk(self):
        import tempfile

        loadout = Loadout(own=own_record(name="Test Mod", creator="me"))
        loadout.set_change(self._change())
        loadout.set_change(self._change(path="/Values/Building/BuildingType", tag="BuildingType",
                                        old_value="Factory", new_value="Residence"))
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "loadout.annoload"
            loadout.save(path)
            loaded = Loadout.load(path)
        self.assertEqual(loaded.own.name, "Test Mod")
        self.assertEqual(len(loaded), 2)
        self.assertEqual({c.path for c in loaded}, {c.path for c in loadout})

    def test_every_field_survives_the_round_trip(self):
        """Nothing may be written that is not read back.

        `to_dict` dumps whole dataclasses while `load` reads field by field, so
        a field added to one and not the other goes missing on the next save
        rather than at the moment of the mistake.
        """
        import dataclasses
        import tempfile

        loadout = Loadout(
            own=own_record(name="M", creator="c", description="d", category="Gameplay",
                           version="2.5", mod_id="mid"),
            source_path="s.xml", game="anno117",
            editing="e", aim=AIM_TWEAK, mods_folder="mf",
            base_archive="data33.rda", base_assets=42, created_at=999.0,
        )
        loadout.set_change(self._change(origin="modA", order=5, positional=True,
                                        note="nn", authored=False, search="//r",
                                        op_uid="ru", absorbs=True,
                                        payload="<x/>", summary="sum"))
        loadout.set_layer(ModLayer(origin="modA", name="Mod A", version="1.2",
                                   active=False, options={"opt": "on"}, order=2))
        loadout.rename_profile(loadout.profile, "Main")
        loadout.add_profile("Other").switches["modA"] = True
        loadout.checkpoint("first")
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "loadout.annoload"
            loadout.save(path)
            loaded = Loadout.load(path)
        for field in dataclasses.fields(Loadout):
            with self.subTest(field=field.name):
                if field.name == "profiles":
                    # Profiles are told apart by identity, not by contents.
                    self.assertEqual(
                        [(name, loaded.switches_of(name)) for name in loaded.profile_names()],
                        [(name, loadout.switches_of(name)) for name in loadout.profile_names()],
                    )
                    continue
                self.assertEqual(
                    getattr(loaded, field.name), getattr(loadout, field.name)
                )


class TestWhatACardSetsUp(unittest.TestCase):
    """A card on the Start tab is one click that writes facts, not a mode.

    The facts are what the places that used to ask "which card" read
    instead, so what is loaded and written afterwards can carry the loadout
    anywhere without anything being inferred from it -- or hidden by it.
    """

    THEIRS = "Somebody's Mod"
    PATCH = "Somebody's Mod (my patch)"

    def set_up(self, aim):
        loadout = Loadout()
        loadout.set_up_as(aim)
        return loadout

    def own_change(self, loadout):
        loadout.set_change(
            Change(guid="1", asset_label="Fish", path="Cost", tag="Value", new_value="2")
        )

    def patch(self, loadout):
        loadout.set_layer(ModLayer(origin=self.THEIRS))
        loadout.set_layer(ModLayer(origin=self.PATCH, patches=self.THEIRS))

    def test_each_card_writes_its_own_facts(self):
        for aim, is_mod in (
            (AIM_MAKE, True),
            (AIM_TWEAK, False),
            (AIM_MANAGE, False),
        ):
            with self.subTest(aim=aim):
                loadout = self.set_up(aim)
                self.assertEqual(loadout.aim, aim)
                self.assertEqual(loadout.is_the_mod, is_mod)

    def test_the_loadout_stands_for_the_mod_it_was_set_up_as(self):
        self.assertEqual(self.set_up(AIM_MAKE).stands_for(), "")
        self.assertIsNone(self.set_up(AIM_TWEAK).stands_for())
        self.assertIsNone(self.set_up(AIM_MANAGE).stands_for())

    def test_or_for_the_mod_of_yours_it_carries_on_with(self):
        for aim in (AIM_MAKE, AIM_TWEAK, AIM_MANAGE):
            with self.subTest(aim=aim):
                loadout = self.set_up(aim)
                loadout.set_layer(ModLayer(origin="Mine"))
                loadout.home = "Mine"
                self.assertEqual(loadout.stands_for(), "Mine")

    def test_a_loadout_starts_with_no_mod_of_its_own(self):
        """A loadout that has done nothing holds nothing.  A "My Anno Mod"
        standing in it before anybody asked for one was a mod the Studio made
        up -- and no card makes one just by being the card."""
        self.assertFalse(Loadout().has_own_mod())
        for aim in (AIM_MAKE, AIM_TWEAK, AIM_MANAGE):
            with self.subTest(aim=aim):
                self.assertFalse(self.set_up(aim).has_own_mod())

    def test_making_it_or_writing_into_it_is_what_brings_it_about(self):
        made = Loadout()
        made.make_own_mod()
        self.assertTrue(made.has_own_mod())
        written = Loadout()
        self.own_change(written)
        self.assertTrue(written.has_own_mod())

    def test_whether_it_was_made_survives_saving(self):
        import tempfile

        loadout = Loadout()
        loadout.make_own_mod()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "p.annoload"
            loadout.save(path)
            self.assertTrue(Loadout.load(path).own_mod)

    def test_nothing_afterwards_moves_the_facts(self):
        """Loading, patching, switching off: the facts are the click's."""
        loadout = self.set_up(AIM_MAKE)
        self.patch(loadout)
        loadout.layers[0].active = False
        loadout.mods_folder = r"C:\anno\mods"
        self.own_change(loadout)
        self.assertEqual(loadout.aim, AIM_MAKE)
        self.assertTrue(loadout.is_the_mod)

    def test_the_facts_survive_saving(self):
        import tempfile

        for aim in (AIM_MAKE, AIM_TWEAK, AIM_MANAGE):
            with self.subTest(aim=aim):
                loadout = self.set_up(aim)
                with tempfile.TemporaryDirectory() as tmp:
                    path = Path(tmp) / "p.annoload"
                    loadout.save(path)
                    back = Loadout.load(path)
                self.assertEqual(back.aim, loadout.aim)
                self.assertEqual(back.is_the_mod, loadout.is_the_mod)

    def test_but_the_facts_win_over_the_card_once_written(self):
        """A loadout that drifted and was saved carries what it was set up
        with, not what a fresh reading of the card would say."""
        import json
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "p.annoload"
            path.write_text(
                json.dumps(
                    {"format": LOADOUT_FORMAT, "aim": AIM_TWEAK, "is_the_mod": True}
                ),
                encoding="utf-8",
            )
            back = Loadout.load(path)
        self.assertTrue(back.is_the_mod)


class TestCachePruning(unittest.TestCase):
    """Only the caches that can never be used again.

    An index built under a different schema is unreachable by construction --
    the schema version is part of the cache key -- so it can only take up room.
    A perfectly good index is a different matter and is never touched here.
    """

    def setUp(self):
        import os
        import tempfile

        self._dir = tempfile.TemporaryDirectory()
        self._was = os.environ.get("ANNO_STUDIO_CACHE")
        os.environ["ANNO_STUDIO_CACHE"] = self._dir.name
        self.cache = Path(self._dir.name)

    def tearDown(self):
        import os

        if self._was is None:
            os.environ.pop("ANNO_STUDIO_CACHE", None)
        else:
            os.environ["ANNO_STUDIO_CACHE"] = self._was
        self._dir.cleanup()

    def _index(self, name, version):
        import sqlite3

        path = self.cache / name
        con = sqlite3.connect(str(path))
        con.execute("create table meta (key text, value text)")
        if version is not None:
            con.execute(
                "insert into meta values ('schema_version', ?)", (str(version),)
            )
        con.commit()
        con.close()
        return path

    def test_an_index_from_another_schema_goes_and_a_current_one_stays(self):
        current = self._index("a.sqlite", index_mod.SCHEMA_VERSION)
        older = self._index("b.sqlite", index_mod.SCHEMA_VERSION - 1)
        nameless = self._index("c.sqlite", None)
        garbage = self.cache / "d.sqlite"
        garbage.write_bytes(b"not a database at all")
        bystander = self.cache / "notes.txt"
        bystander.write_text("keep me", encoding="utf-8")

        removed = index_mod.prune_cache()

        self.assertEqual(removed, 3)
        self.assertTrue(current.exists())
        self.assertTrue(bystander.exists())
        for gone in (older, nameless, garbage):
            self.assertFalse(gone.exists(), gone.name)

    def test_an_index_of_a_newer_schema_is_a_newer_studios_and_stays(self):
        newer = self._index("h.sqlite", index_mod.SCHEMA_VERSION + 1)
        older = self._index("i.sqlite", index_mod.SCHEMA_VERSION - 1)

        self.assertEqual(index_mod.prune_cache(), 1)

        self.assertTrue(newer.exists())
        self.assertFalse(older.exists())

    def test_a_half_built_index_goes_only_once_no_build_could_own_it(self):
        import os
        import time

        running = self.cache / "e.sqlite.building"
        running.write_bytes(b"x")
        abandoned = self.cache / "f.sqlite.building"
        abandoned.write_bytes(b"x")
        os.utime(abandoned, (time.time() - 2 * index_mod.STALE_BUILDING_SECONDS,) * 2)

        index_mod.prune_cache()

        self.assertTrue(running.exists(), "a build in progress must survive")
        self.assertFalse(abandoned.exists())

    def test_the_index_just_built_is_never_the_one_pruned(self):
        """build_index prunes straight after committing, so this matters."""
        fresh = self._index("g.sqlite", index_mod.SCHEMA_VERSION - 1)
        index_mod.prune_cache(keep=fresh)
        self.assertTrue(fresh.exists())


class TestBuildingAnIndex(unittest.TestCase):
    """What ``build_index`` does with a file that is not the game's whole
    one, with a field named like the file's own structure, and with Cancel."""

    WHOLE = (
        "<AssetList><Groups><Group><Name>outer</Name><Assets>"
        "<Asset><Template>T</Template><Values><Standard><GUID>1</GUID><Name>one</Name></Standard>"
        "<Thing><Group><Name>inner</Name><Size>3</Size></Group></Thing></Values></Asset>"
        "<Asset><Template>T</Template><Values><Standard><GUID>2</GUID><Name>two</Name></Standard>"
        "</Values></Asset>"
        "</Assets></Group></Groups></AssetList>"
    )

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.assets = Path(self._tmp.name) / "assets.xml"
        self.db = Path(self._tmp.name) / "index.sqlite"

    def build(self, text, **more):
        self.assets.write_text(text, encoding="utf-8")
        return index_mod.build_index(self.assets, self.db, **more)

    def test_a_file_cut_short_is_not_indexed(self):
        """It was, up to the cut and with the half asset in it."""
        cut = self.WHOLE[: self.WHOLE.index("<GUID>2</GUID>") + 8]
        with self.assertRaises(index_mod.NotWholeXml) as refused:
            self.build(cut)
        self.assertIn("assets.xml is not whole XML", str(refused.exception))
        self.assertFalse(self.db.exists())
        self.assertEqual(list(Path(self._tmp.name).glob("*.building")), [])

    def test_nor_is_a_file_that_is_not_xml(self):
        """It gave an index of no assets, there for the next start to adopt."""
        with self.assertRaises(index_mod.NotWholeXml):
            self.build("hello, not xml")
        self.assertFalse(self.db.exists())

    def test_one_that_fails_leaves_the_index_there_was(self):
        self.assertEqual(self.build(self.WHOLE), 2)
        before = self.db.read_bytes()
        with self.assertRaises(index_mod.NotWholeXml):
            self.build(self.WHOLE[:-20])
        self.assertEqual(self.db.read_bytes(), before)

    def test_a_group_field_inside_an_asset_is_stored_as_it_is_written(self):
        """Its ``<Name>`` was taken for a group of the file's, and the
        Studio's own attribute written into the asset."""
        self.build(self.WHOLE)
        index = index_mod.AssetIndex(self.db, self.assets)
        self.addCleanup(index.close)
        stored = etree.tostring(index.asset_element("1")).decode("utf-8")
        self.assertIn("<Group><Name>inner</Name>", stored)
        self.assertNotIn("_ams", stored)
        # The file's own group is still what the asset is filed under.
        self.assertEqual(index.asset_row("1").group_path, "outer")

    def test_cancel_is_heard_after_the_file_has_been_read(self):
        """Asked only while assets went by, it was not: sorting out the
        references and the writing after it ran to the end."""
        asked = []

        def cancel_once_read() -> bool:
            asked.append(len(asked))
            return False

        self.build(self.WHOLE, should_cancel=cancel_once_read)
        self.assertGreaterEqual(len(asked), 3)  # between the steps after the file
        real, classified = index_mod._classify_refs, []

        def classify(con) -> None:
            classified.append(True)
            real(con)

        index_mod._classify_refs = classify
        self.addCleanup(setattr, index_mod, "_classify_refs", real)
        self.db.unlink()
        with self.assertRaises(index_mod.IndexCancelled):
            self.build(self.WHOLE, should_cancel=lambda: bool(classified))
        self.assertFalse(self.db.exists())
        self.assertEqual(list(Path(self._tmp.name).glob("*.building")), [])


class TestLoadoutFilesThatCannotBeRead(unittest.TestCase):
    """Every one of these reaches the user as a dialog.

    The last loadout is reopened on launch, so a damaged one greets you before
    you have done anything -- and "could not be read" has to be a sentence
    rather than whichever Python error happened to surface first.
    """

    def _load(self, text):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "broken.annoload"
            path.write_text(text, encoding="utf-8")
            return Loadout.load(path)

    def test_a_file_that_is_not_a_loadout_says_so(self):
        for label, text in (
            ("a json list", "[1, 2, 3]"),
            ("not json", "<xml>hello</xml>"),
            ("truncated", '{"mod_name": "x", "chan'),
        ):
            with self.subTest(label=label), self.assertRaises(LoadoutError):
                self._load(text)

    def test_something_that_cannot_be_opened_at_all_says_so(self):
        """A directory where a loadout file is expected, say."""
        import tempfile

        with tempfile.TemporaryDirectory() as tmp,                 self.assertRaises(LoadoutError) as caught:
            Loadout.load(Path(tmp))
        self.assertIn("could not be opened", str(caught.exception))

    def test_a_loadout_from_a_newer_studio_is_refused_rather_than_guessed_at(self):
        """Opening it would risk saving back less than it holds."""
        import json as json_mod

        raw = Loadout(own=own_record(name="Later")).to_dict()
        raw["format"] = LOADOUT_FORMAT + 1
        with self.assertRaises(LoadoutError) as caught:
            self._load(json_mod.dumps(raw))
        self.assertIn("newer version", str(caught.exception))
        # The same file at the current format is perfectly readable.
        raw["format"] = LOADOUT_FORMAT
        self.assertEqual(self._load(json_mod.dumps(raw)).own.name, "Later")

    def test_work_that_is_the_wrong_shape_is_refused_not_silently_dropped(self):
        import json as json_mod

        raw = Loadout(own=own_record(name="M")).to_dict()
        raw["changes"] = "oops"
        with self.assertRaises(LoadoutError):
            self._load(json_mod.dumps(raw))

    def test_bookkeeping_that_is_the_wrong_shape_falls_back(self):
        """A bad count is not worth refusing somebody's work over."""
        import json as json_mod

        raw = Loadout(own=own_record(name="M")).to_dict()
        raw["base_assets"] = "many"
        raw["created_at"] = "yesterday"
        raw["layers"] = None
        loaded = self._load(json_mod.dumps(raw))
        self.assertEqual(loaded.base_assets, 0)
        self.assertEqual(loaded.own.name, "M")


class TestGenerator(CoreTestBase):
    def _loadout(self):
        loadout = Loadout(own=own_record(name="Faster Lumber", creator="tester", version="1.2"))
        loadout.source_path = str(self.assets_path)
        loadout.set_change(
            Change(
                guid="1010340",
                asset_label="Lumberjack's Hut (lumberjack)",
                path="/Values/Factory7/CycleTime",
                tag="CycleTime",
                old_value="30.00",
                new_value="15.00",
            )
        )
        loadout.set_change(
            Change(
                guid="1010340",
                asset_label="Lumberjack's Hut (lumberjack)",
                path="/Values/Maintenance/Maintenances/Item[Product='1010017']/Amount",
                tag="Amount",
                old_value="40",
                new_value="10",
            )
        )
        loadout.set_change(
            Change(
                guid="1010030",
                asset_label="Farmer Residence (residence_tier01)",
                path="/Values/Residence7/Residents",
                tag="Residents",
                old_value="8",
                new_value="12",
            )
        )
        return loadout

    def test_one_modop_per_change(self):
        xml = generator.build_modops_xml(self._loadout())
        root = etree.fromstring(xml.encode("utf-8"))
        self.assertEqual(root.tag, "ModOps")
        modops = root.findall("ModOp")
        self.assertEqual(len(modops), 3)
        for modop in modops:
            self.assertEqual(modop.get("Type"), "replace")
            self.assertTrue(modop.get("GUID"))
            self.assertTrue(modop.get("Path"))
            # Surgical: the replacement payload is a single leaf element.
            self.assertEqual(len(modop), 1)
            self.assertEqual(len(modop[0]), 0)

    def test_payload_tag_matches_the_path_leaf(self):
        xml = generator.build_modops_xml(self._loadout())
        root = etree.fromstring(xml.encode("utf-8"))
        for modop in root.findall("ModOp"):
            leaf = modop.get("Path").rstrip("/").split("/")[-1].split("[")[0]
            self.assertEqual(modop[0].tag, leaf)

    def test_generated_modops_apply_to_the_real_asset(self):
        """Walk the generated ops back over the source asset and apply them."""
        loadout = self._loadout()
        for change in loadout:
            root = self.index.asset_element(change.guid)
            matches = paths.resolve(root, change.path)
            self.assertEqual(len(matches), 1)
            self.assertEqual((matches[0].text or "").strip(), change.old_value)
            matches[0].text = change.new_value
            self.assertEqual(paths.resolve(root, change.path)[0].text, change.new_value)

    def test_verification_flags_a_stale_change(self):
        loadout = self._loadout()
        loadout.set_change(
            Change(
                guid="1010340",
                asset_label="Lumberjack's Hut",
                path="/Values/Factory7/NotAThing",
                tag="NotAThing",
                old_value="1",
                new_value="2",
            )
        )
        results = generator.verify_changes(self.index, loadout)
        bad = [r for r in results if not r.ok]
        self.assertEqual(len(bad), 1)
        self.assertEqual(bad[0].change.tag, "NotAThing")

    def test_values_are_escaped(self):
        loadout = Loadout()
        loadout.set_change(
            Change(
                guid="1",
                asset_label="A & B",
                path="/Values/X/Y",
                tag="Y",
                old_value="a",
                new_value="1 < 2 & 3 > 2",
            )
        )
        xml = generator.build_modops_xml(loadout)
        root = etree.fromstring(xml.encode("utf-8"))
        self.assertEqual(root.find("ModOp/Y").text, "1 < 2 & 3 > 2")

    def test_comment_text_never_breaks_the_document(self):
        loadout = Loadout(own=own_record(name="Dashes -- everywhere ---"))
        loadout.set_change(
            Change(
                guid="1",
                asset_label="weird -- label",
                path="/Values/X/Y",
                tag="Y",
                old_value="a--b",
                new_value="c--d",
            )
        )
        xml = generator.build_modops_xml(loadout)
        etree.fromstring(xml.encode("utf-8"))  # would raise on a malformed comment

    def test_output_is_wellformed_without_comments(self):
        xml = generator.build_modops_xml(
            self._loadout(),
        )
        root = etree.fromstring(xml.encode("utf-8"))
        self.assertEqual(len(root.findall("ModOp")), 3)

    def test_writes_a_loadable_mod_folder(self):
        import json
        import tempfile

        loadout = self._loadout()
        xml = generator.build_modops_xml(loadout)
        with tempfile.TemporaryDirectory() as tmp:
            root = generator.write_mod_folder(loadout, Path(tmp), xml)
            asset_file = root / generator.MOD_ASSET_RELPATH
            self.assertTrue(asset_file.is_file())
            self.assertEqual(root.name, "[Gameplay] Faster Lumber")
            info = json.loads((root / "modinfo.json").read_text(encoding="utf-8"))
            self.assertEqual(info["ModName"]["English"], "Faster Lumber")
            self.assertEqual(info["Version"], "1.2")
            etree.parse(str(asset_file))


class TestAnExportCarriesTheRestOfTheMod(unittest.TestCase):
    """A mod is more than its assets.xml, and an export has to be the mod.

    534 of the 854 Anno 1800 mods on mod.io ship something besides the two
    files an export writes -- their texts, a templates.xml patch, a .cfg, an
    .ifo, a .fc.  Written to a folder of your own those used to stay behind,
    and the export was the part of the mod the Studio models rather than the
    mod.  Into the game's own mods folder nothing was ever lost, because the
    files were already there; that case still copies nothing.
    """

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.source = self.tmp / "mods" / "[Gameplay] Theirs"
        self.relpath = games.get(games.ANNO_1800).asset_relpath
        self.write(self.source / self.relpath, "<ModOps/>")
        self.write(self.source / "modinfo.json", "{}")
        self.write(self.source / "data/config/export/main/asset/templates.xml", "<ModOps/>")
        self.write(self.source / "data/config/gui/texts_english.xml", "<TextExport/>")
        self.write(self.source / "data/graphics/thing.cfg", "cfg")
        self.write(self.source / ".git/config", "tooling")

    def write(self, path: Path, text: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def loadout(self):
        loadout = Loadout(own=own_record(name="Theirs"), game=games.ANNO_1800)
        loadout.set_change(
            Change(guid="1000", asset_label="x", path="/Values/Building/Radius",
                   tag="Radius", old_value="10", new_value="20")
        )
        return loadout

    def written(self, root: Path):
        return sorted(p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file())

    def test_what_the_mod_ships_comes_along(self):
        out = self.tmp / "out"
        root = generator.write_mod_folder(
            self.loadout(), out, "<ModOps/>", folder_name="[Gameplay] Theirs",
            carry_from=self.source,
        )
        self.assertEqual(self.written(root), [
            "data/config/export/main/asset/assets.xml",
            "data/config/export/main/asset/templates.xml",
            "data/config/gui/texts_english.xml",
            "data/graphics/thing.cfg",
            "modinfo.json",
        ])

    def test_carrying_only_some_kinds_of_file_leaves_the_rest(self):
        """What the census harness asks for: the XML, and not the models."""
        out = self.tmp / "only"
        root = generator.write_mod_folder(
            self.loadout(), out, "<ModOps/>", folder_name="[Gameplay] Theirs",
            carry_from=self.source, carry_only=(".xml",),
        )
        self.assertEqual(self.written(root), [
            "data/config/export/main/asset/assets.xml",
            "data/config/export/main/asset/templates.xml",
            "data/config/gui/texts_english.xml",
            "modinfo.json",
        ])

    def test_without_a_folder_to_carry_from_it_writes_the_two(self):
        out = self.tmp / "bare"
        root = generator.write_mod_folder(
            self.loadout(), out, "<ModOps/>", folder_name="[Gameplay] Theirs"
        )
        self.assertEqual(
            self.written(root),
            ["data/config/export/main/asset/assets.xml", "modinfo.json"],
        )

    def test_writing_back_over_the_mod_itself_copies_nothing(self):
        """The files are already there, and copying a file onto itself is an
        error rather than a no-op."""
        before = self.written(self.source)
        root = generator.write_mod_folder(
            self.loadout(), self.source.parent, "<ModOps>new</ModOps>",
            folder_name="[Gameplay] Theirs", carry_from=self.source,
        )
        self.assertEqual(root, self.source)
        self.assertEqual(self.written(self.source), before)
        self.assertIn("new", (self.source / self.relpath).read_text(encoding="utf-8"))

    def test_the_two_files_are_left_out_at_any_depth(self):
        """Which is what makes a bundled mod come out right: its own patch is
        written by its own export, and everything else of its travels here."""
        inner = self.source / "Inner"
        self.write(inner / self.relpath, "<ModOps/>")
        self.write(inner / "modinfo.json", "{}")
        self.write(inner / "data/config/gui/texts_english.xml", "<TextExport/>")
        carried = generator.carried_files(self.source, games.ANNO_1800)
        self.assertIn(Path("Inner/data/config/gui/texts_english.xml"), carried)
        self.assertNotIn(Path("Inner") / self.relpath, carried)
        self.assertNotIn(Path("Inner/modinfo.json"), carried)

    def test_a_file_too_deep_for_windows_to_open_by_name_still_travels(self):
        """*Tourists Alternativ Buildings* keeps a .cfg 196 characters down its
        own tree, and an export folder in front of that is past the 260 a
        plain Windows path stops at.  The whole unit used to end in a
        FileNotFoundError; a file that cannot be seen is one the export would
        quietly leave behind."""
        deep = "data/" + "/".join("a_long_folder_name_%02d" % n for n in range(9))
        os.makedirs(disk.reachable(self.source / deep), exist_ok=True)
        Path(disk.reachable(self.source / deep / "thing.cfg")).write_text(
            "cfg", encoding="utf-8"
        )
        self.assertIn(Path(deep) / "thing.cfg", generator.carried_files(self.source, games.ANNO_1800))

        out = self.tmp / ("out_" + "x" * 50)
        trouble = []
        root = generator.write_mod_folder(
            self.loadout(), out, "<ModOps/>", folder_name="[Gameplay] Theirs",
            carry_from=self.source, trouble=trouble,
        )
        landed = root / deep / "thing.cfg"
        self.assertGreater(len(str(landed)), 260)
        self.assertTrue(Path(disk.reachable(landed)).is_file())
        self.assertEqual(trouble, [])

    def test_a_file_that_cannot_be_copied_is_reported_not_fatal(self):
        """A mod's hundred side files are not worth losing the export over,
        and losing one quietly is not worth it either.  Here a file sits
        where the copy needs a folder, which is as good a way as any to stop
        one file and nothing else."""
        out = self.tmp / "reported"
        blocked = out / "[Gameplay] Theirs/data/graphics"
        blocked.parent.mkdir(parents=True)
        blocked.write_text("in the way", encoding="utf-8")

        trouble = []
        root = generator.write_mod_folder(
            self.loadout(), out, "<ModOps/>",
            folder_name="[Gameplay] Theirs", carry_from=self.source, trouble=trouble,
        )
        self.assertEqual([line.split(":")[0] for line in trouble],
                         [str(Path("data/graphics/thing.cfg"))])
        # And everything else still got there, patch included.
        self.assertTrue((root / self.relpath).is_file())
        self.assertTrue((root / "data/config/gui/texts_english.xml").is_file())

    def test_and_without_a_list_to_report_to_it_still_raises(self):
        out = self.tmp / "raising"
        blocked = out / "[Gameplay] Theirs/data/graphics"
        blocked.parent.mkdir(parents=True)
        blocked.write_text("in the way", encoding="utf-8")
        with self.assertRaises(OSError):
            generator.write_mod_folder(
                self.loadout(), out, "<ModOps/>",
                folder_name="[Gameplay] Theirs", carry_from=self.source,
            )

    def test_the_patch_is_left_out_whatever_case_the_mod_spelled_it(self):
        """*Additional powerplants* calls its patch `Assets.xml`.  The game
        reads that name without case and so must this, or the copy lands on
        top of the file the export has just written -- which is how the census
        caught it: 2,405 lines of mod replaced by 17."""
        shouty = self.source / "data/config/export/main/asset/Assets.xml"
        (self.source / self.relpath).rename(shouty)
        self.assertEqual(
            [p for p in generator.carried_files(self.source, games.ANNO_1800)
             if p.name.lower() == "assets.xml"],
            [],
        )

        out = self.tmp / "cased"
        root = generator.write_mod_folder(
            self.loadout(), out, "<ModOps>mine</ModOps>",
            folder_name="[Gameplay] Theirs", carry_from=self.source,
        )
        self.assertIn("mine", (root / self.relpath).read_text(encoding="utf-8"))

    def test_a_stale_copy_at_the_destination_is_replaced(self):
        out = self.tmp / "again"
        stale = out / "[Gameplay] Theirs/data/config/gui/texts_english.xml"
        self.write(stale, "<TextExport>old</TextExport>")
        generator.write_mod_folder(
            self.loadout(), out, "<ModOps/>", folder_name="[Gameplay] Theirs",
            carry_from=self.source,
        )
        self.assertEqual(stale.read_text(encoding="utf-8"), "<TextExport/>")


class TestReferences(unittest.TestCase):
    """Deciding whether a bare integer is a pointer to another asset."""

    def test_guid_named_fields_are_references(self):
        self.assertTrue(refs_mod.could_be_reference("GUID", "7"))
        self.assertTrue(refs_mod.could_be_reference("NeedGUID", "42"))

    def test_large_numbers_in_unknown_fields_are_references(self):
        self.assertTrue(refs_mod.could_be_reference("FluffText", "19581"))
        self.assertTrue(refs_mod.could_be_reference("Product", "120031"))

    def test_small_numbers_in_unknown_fields_are_not(self):
        self.assertFalse(refs_mod.could_be_reference("Something", "7"))

    def test_known_numeric_fields_are_never_references(self):
        for tag in ("Amount", "TradePrice", "CycleTime", "Value", "Percental"):
            self.assertFalse(
                refs_mod.could_be_reference(tag, "750000"), tag
            )

    def test_non_numeric_values_are_not(self):
        self.assertFalse(refs_mod.could_be_reference("Product", "Timber"))
        self.assertFalse(refs_mod.could_be_reference("Product", ""))

    def test_resolve_without_an_index_is_safe(self):
        self.assertIsNone(refs_mod.resolve(None, "Product", "120031"))


class TestPathKeys(CoreTestBase):
    def test_falls_back_to_any_unique_child_tag(self):
        """A fixed key list will always be missing some list's real key."""
        xml = (
            b"<Asset><Values><L>"
            b"<Item><Attribute>PerkMale</Attribute><Amount>1</Amount></Item>"
            b"<Item><Attribute>Diplomacy</Attribute><Amount>25</Amount></Item>"
            b"<Item/><Item/>"
            b"</L></Values></Asset>"
        )
        root = etree.fromstring(xml)
        node = root.xpath("Values/L/Item[Attribute='Diplomacy']/Amount")[0]
        generated = paths.path_for(root, node)
        self.assertEqual(generated.path, "/Values/L/Item[Attribute='Diplomacy']/Amount")
        self.assertFalse(generated.positional)
        self.assertEqual(len(paths.resolve(root, generated.path)), 1)

    def test_a_key_inside_a_wrapper_still_identifies_the_entry(self):
        """Anno 117 keeps list keys one level down, inside <VectorElement>.

        Nothing at the top level of these entries tells one row from the next,
        so before nested keys were tried they addressed each other by position.
        """
        xml = (
            b"<Asset><Values><CharacterReasons>"
            b"<Item><VectorElement><InheritedIndex>0</InheritedIndex></VectorElement>"
            b"<Impacts><Item><Impact>93187</Impact></Item></Impacts></Item>"
            b"<Item><VectorElement><InheritedIndex>2</InheritedIndex></VectorElement>"
            b"<Impacts><Item><Impact>93196</Impact></Item></Impacts></Item>"
            b"</CharacterReasons></Values></Asset>"
        )
        root = etree.fromstring(xml)
        entry = root.xpath("Values/CharacterReasons/Item")[1]
        generated = paths.path_for(root, entry)
        self.assertEqual(
            generated.path,
            "/Values/CharacterReasons/Item[VectorElement/InheritedIndex='2']",
        )
        self.assertFalse(generated.positional)
        matches = paths.resolve(root, generated.path)
        self.assertEqual(len(matches), 1)
        self.assertIs(matches[0], entry)

    def test_a_direct_child_key_still_wins_over_a_nested_one(self):
        """Nested keys are a fallback, not a preference.

        An entry that has always been addressed by a direct child has to go on
        being addressed by it -- conflict detection compares path strings, so
        regenerating a different path for the same node would quietly stop two
        mods from being seen to collide.
        """
        xml = (
            b"<Asset><Values><L>"
            b"<Item><Product>1010365</Product>"
            b"<VectorElement><InheritedIndex>0</InheritedIndex></VectorElement></Item>"
            b"<Item><Product>1010366</Product>"
            b"<VectorElement><InheritedIndex>1</InheritedIndex></VectorElement></Item>"
            b"</L></Values></Asset>"
        )
        root = etree.fromstring(xml)
        entry = root.xpath("Values/L/Item")[1]
        generated = paths.path_for(root, entry)
        self.assertEqual(generated.path, "/Values/L/Item[Product='1010366']")
        self.assertEqual(len(paths.resolve(root, generated.path)), 1)

    def test_still_positional_when_neither_depth_identifies_the_entry(self):
        xml = (
            b"<Asset><Values><L>"
            b"<Item><W><X>same</X></W></Item>"
            b"<Item><W><X>same</X></W></Item>"
            b"</L></Values></Asset>"
        )
        root = etree.fromstring(xml)
        generated = paths.path_for(root, root.xpath("Values/L/Item")[1])
        self.assertEqual(generated.path, "/Values/L/Item[2]")
        self.assertTrue(generated.positional)

    def test_still_positional_when_entries_are_indistinguishable(self):
        xml = b"<Asset><Values><L><I>a</I><I>a</I></L></Values></Asset>"
        root = etree.fromstring(xml)
        generated = paths.path_for(root, root.xpath("Values/L/I")[1])
        self.assertEqual(generated.path, "/Values/L/I[2]")
        self.assertTrue(generated.positional)


class TestPublishSafety(unittest.TestCase):
    def test_the_file_never_carries_the_local_path(self):
        loadout = Loadout(
            own=own_record(name="Test"),
            source_path=r"C:\Users\alice\Documents\extracted\assets.xml",
            base_archive="data33.rda",
            base_assets=96102,
        )
        loadout.set_change(
            Change(
                guid="1",
                asset_label="A",
                path="/Values/X/Y",
                tag="Y",
                old_value="1",
                new_value="2",
            )
        )
        xml = generator.build_modops_xml(loadout)
        self.assertNotIn("alice", xml)
        self.assertNotIn("C:\\", xml)
        self.assertNotIn("alice", generator.build_modinfo(loadout))


class TestTextDemotion(CoreTestBase):
    def test_text_assets_sort_below_real_ones(self):
        rows = self.index.search("beer")
        self.assertTrue(rows)
        self.assertNotEqual(rows[0].template, "Text")


class TestModFolderNaming(unittest.TestCase):
    """Published mods are named "[Category] Name"."""

    def test_uses_the_category(self):
        loadout = Loadout(own=own_record(name="Faster Lumber", category="Cheat"))
        self.assertEqual(generator.mod_folder_name(loadout), "[Cheat] Faster Lumber")

    def test_falls_back_when_no_category(self):
        loadout = Loadout(own=own_record(name="Faster Lumber", category=""))
        self.assertEqual(generator.mod_folder_name(loadout), "[Mod] Faster Lumber")

    def test_strips_characters_windows_rejects(self):
        loadout = Loadout(own=own_record(name='A/B:C?', category="Map"))
        name = generator.mod_folder_name(loadout)
        for bad in '<>:"/\\|?*':
            self.assertNotIn(bad, name)

    def test_folder_on_disk_matches_the_computed_name(self):
        import tempfile

        loadout = Loadout(own=own_record(name="Faster Lumber", category="Ship"))
        loadout.set_change(
            Change(
                guid="1",
                asset_label="A",
                path="/Values/X/Y",
                tag="Y",
                old_value="1",
                new_value="2",
            )
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = generator.write_mod_folder(
                loadout, Path(tmp), generator.build_modops_xml(loadout)
            )
        self.assertEqual(root.name, generator.mod_folder_name(loadout))
        self.assertEqual(root.name, "[Ship] Faster Lumber")


class TestHistory(unittest.TestCase):
    """Checkpoints, diffs and restore."""

    def _loadout(self):
        loadout = Loadout(own=own_record(name="Test", version="1.0"))
        loadout.set_change(
            Change(
                guid="1",
                asset_label="A",
                path="/Values/A/B",
                tag="B",
                old_value="1",
                new_value="2",
            )
        )
        return loadout

    def test_a_checkpoint_records_the_current_state(self):
        loadout = self._loadout()
        snap = loadout.checkpoint("Exported v1.0")
        self.assertEqual(len(loadout.history_of("")), 1)
        self.assertEqual(snap.version, "1.0")
        self.assertEqual(len(snap.changes), 1)
        self.assertFalse(loadout.changed_since_checkpoint())

    def test_editing_after_a_checkpoint_is_detected(self):
        loadout = self._loadout()
        loadout.checkpoint("v1")
        self.assertFalse(loadout.changed_since_checkpoint())
        loadout.set_change(
            Change(
                guid="2",
                asset_label="B",
                path="/Values/C/D",
                tag="D",
                old_value="5",
                new_value="6",
            )
        )
        self.assertTrue(loadout.changed_since_checkpoint())
        diff = loadout.diff_since(loadout.latest_checkpoint)
        self.assertEqual(len(diff.added), 1)
        self.assertEqual(diff.summary(), "1 added")

    def test_diff_classifies_added_removed_and_edited(self):
        a = Change("1", "A", "/Values/A/B", "B", "1", "2")
        b = Change("2", "B", "/Values/C/D", "D", "5", "6")
        b2 = Change("2", "B", "/Values/C/D", "D", "5", "9")
        c = Change("3", "C", "/Values/E/F", "F", "7", "8")

        diff = diff_changes([a, b], [b2, c])
        self.assertEqual([x.guid for x in diff.removed], ["1"])
        self.assertEqual([x.guid for x in diff.added], ["3"])
        self.assertEqual(len(diff.modified), 1)
        self.assertEqual(diff.modified[0][0].new_value, "6")
        self.assertEqual(diff.modified[0][1].new_value, "9")
        self.assertEqual(diff.total, 3)

    def test_identical_sets_diff_to_nothing(self):
        a = Change("1", "A", "/Values/A/B", "B", "1", "2")
        diff = diff_changes([a], [Change("1", "A", "/Values/A/B", "B", "1", "2")])
        self.assertTrue(diff.is_empty)
        self.assertEqual(diff.summary(), "no changes")

    def test_restore_replaces_the_working_set(self):
        loadout = self._loadout()
        loadout.checkpoint("v1")
        loadout.set_change(
            Change("9", "Z", "/Values/Z/Z", "Z", "0", "1")
        )
        self.assertEqual(len(loadout), 2)

        loadout.restore(loadout.history_of("")[0])
        self.assertEqual(len(loadout), 1)
        self.assertEqual(next(iter(loadout)).guid, "1")

    @staticmethod
    def _typed(loadout, guid: str, value: str) -> Op:
        """An op typed here with the one change it comes to, as Apply in the
        XML view stores the pair."""
        op = Op(
            expression="/Values/C/D", authored=True,
            source='<ModOp Type="merge" GUID="%s" Path="/Values/C"><D>%s</D></ModOp>' % (guid, value),
        )
        loadout.set_op(op)
        loadout.set_change(Change(guid, "B", "/Values/C/D", "D", "5", value, op_uid=op.uid))
        return op

    def test_an_op_made_here_is_work_and_not_a_change(self):
        """The title bar counted both: a change for the op and one for what
        it comes to, so it ran ahead of the changes list by one an op."""
        loadout = self._loadout()
        self._typed(loadout, "2", "6")
        # Typed, and comes to nothing as things stand: work all the same.
        loadout.set_op(Op(expression="/Values/E", source="<ModOp/>", authored=True))
        self.assertEqual(len(loadout), 2)
        self.assertEqual(loadout.changes_made, 2)
        self.assertEqual(loadout.authored_count, 4)
        # And what was read from a mod's folder is neither.
        loadout.set_change(Change("3", "C", "/Values/E/F", "F", "7", "8", origin="Theirs", authored=False))
        self.assertEqual((len(loadout), loadout.changes_made), (3, 2))

    def test_what_is_made_since_a_save_is_what_is_new_or_says_something_else(self):
        """The question on the way out counted every change made here as not
        saved, the saved ones with the rest."""
        loadout = self._loadout()
        self.assertEqual(loadout.made_since({}), 1)
        self.assertEqual(loadout.made_since(None), 1)
        saved = loadout.standing()
        self.assertEqual(loadout.made_since(saved), 0)
        # One more made, and one of the saved ones typed again: its key is
        # the same and what it says is not.
        loadout.set_change(Change("3", "C", "/Values/E/F", "F", "7", "8"))
        again = loadout.find("1", "/Values/A/B")
        again.new_value = "30"
        self.assertEqual(loadout.made_since(saved), 2)
        # Neither is something taken away, so neither is counted twice.
        self.assertEqual(loadout.taken_since(saved), 0)
        # Typed back, it is as it was saved.
        again.new_value = saved[again.key][2][0]
        self.assertEqual(loadout.made_since(saved), 1)
        # What a folder brings is not made here, whenever it came.
        loadout.set_change(Change("3", "C", "/Values/E/G", "G", "7", "8", origin="Theirs", authored=False))
        self.assertEqual(loadout.made_since(saved), 1)

    def test_a_note_typed_is_an_edit_and_not_something_taken_away(self):
        """A note on a change or op was counted with the ones reverted or
        switched, and on a change made here in no count of what was made."""
        loadout = self._loadout()
        loadout.set_change(Change("3", "C", "/Values/E/G", "G", "7", "8", origin="Theirs", authored=False))
        theirs = Op(expression="/Values/E", source="<ModOp/>", origin="Theirs")
        loadout.set_op(theirs)
        loadout.layers.append(ModLayer(origin="Theirs", name="Theirs"))
        saved = loadout.standing()
        mine = loadout.find("1", "/Values/A/B")
        read = loadout.find("3", "/Values/E/G", origin="Theirs")

        def counts() -> tuple:
            return (
                loadout.made_since(saved), loadout.taken_since(saved),
                loadout.noted_since(saved),
            )

        self.assertEqual(counts(), (0, 0, 0))
        # On a change made here it is part of what was made, and only that.
        mine.note = "on purpose"
        self.assertEqual(counts(), (1, 0, 0))
        mine.note = saved[mine.key][1]
        self.assertEqual(counts(), (0, 0, 0))
        # On what was read in, nothing was made and nothing taken away.
        read.note = "why this is here"
        theirs.note = "and this"
        self.assertEqual(counts(), (0, 0, 2))
        # One switched as well is one thing switched.
        theirs.active = False
        self.assertEqual(counts(), (0, 1, 1))
        # And one taken out is one thing taken out.
        loadout.remove_key(read.key)
        self.assertEqual(counts(), (0, 2, 0))
        # Typed back, it is as it was saved.
        theirs.active, theirs.note = True, saved[theirs.key][1]
        loadout.set_change(read)
        read.note = saved[read.key][1]
        self.assertEqual(counts(), (0, 0, 0))
        # Against nothing there is nothing to have another note than.
        self.assertEqual(loadout.noted_since(None), 0)

    def test_a_mod_dropped_once_all_of_it_was_reverted_is_still_taken_away(self):
        """Reverting the last change of a mod in the Mod changes panel drops
        the mod as well, and then the revert counted for nothing: no mark,
        no question, and the saved loadout brought the change back."""
        loadout = self._loadout()
        read = Change("3", "C", "/Values/E/G", "G", "7", "8", origin="Theirs", authored=False)
        loadout.set_change(read)
        loadout.layers.append(ModLayer(origin="Theirs", name="Theirs"))
        loadout.set_change(Change("4", "D", "/Values/E/G", "G", "7", "8", origin="Other", authored=False))
        loadout.layers.append(ModLayer(origin="Other", name="Other"))
        saved = loadout.standing()
        loadout.remove_key(read.key)
        loadout.drop_layer("Theirs")
        self.assertEqual(loadout.taken_since(saved), 1)
        # A mod unloaded with its changes is not taken away: reading it in
        # again undoes that.
        loadout.drop_layer("Other")
        self.assertEqual(loadout.taken_since(saved), 1)
        # Nor, read in again and then unloaded whole, is the first one.
        loadout.layers.append(ModLayer(origin="Theirs", name="Theirs"))
        loadout.set_change(read)
        self.assertEqual(loadout.taken_since(saved), 0)
        loadout.drop_layer("Theirs")
        self.assertEqual(loadout.taken_since(saved), 0)

    def test_and_an_op_typed_that_comes_to_no_change_is_counted_where_no_change_is(self):
        loadout = self._loadout()
        saved = loadout.standing()
        nowhere = Op(expression="/Values/E", source="<ModOp/>", authored=True)
        loadout.set_op(nowhere)
        self.assertEqual(loadout.made_since(saved), 1)
        # Beside a change it is not a second one, as the title has it.
        typed = self._typed(loadout, "2", "6")
        self.assertEqual(loadout.made_since(saved), 1)
        # Saved and written another way, it is the one thing not saved.
        saved = loadout.standing()
        typed.source = typed.source.replace("<ModOp", "<ModOp Condition='#x'")
        self.assertEqual(loadout.made_since(saved), 1)
        self.assertEqual(loadout.taken_since(saved), 0)

    def test_what_an_op_made_here_comes_to_is_yours_on_the_ops_word(self):
        """Composing a pattern left its changes unmarked: in the list, and in
        no count of yours and no checkpoint."""
        loadout = self._loadout()
        empty = loadout.checkpoint("before the op")
        op = self._typed(loadout, "2", "6")
        (answer,) = loadout.changes_by_op(op.uid)
        answer.authored = False
        self.assertEqual(loadout.changes_made, 2)
        self.assertIn(answer, loadout.authored())
        self.assertEqual(len(loadout.checkpoint("with it").changes), 2)
        # And it goes with the op when a checkpoint from before it is put back.
        loadout.restore(empty)
        self.assertEqual([change.guid for change in loadout], ["1"])
        self.assertEqual(loadout.ops, [])

    def test_restore_puts_back_the_ops_made_here_with_their_changes(self):
        """A checkpoint held the changes and not the ops they hang off: one
        made since stayed in the mod, with nothing under it, and was still
        written out."""
        loadout = self._loadout()
        kept = self._typed(loadout, "2", "6")
        before = loadout.checkpoint("with one op")
        self.assertEqual([op["uid"] for op in before.ops], [kept.uid])
        since = self._typed(loadout, "4", "9")
        loadout.drop_op(kept.uid)
        # Somebody else's op is not history's to touch.
        theirs = Op(expression="/Values/X", source="<ModOp/>", origin="Theirs")
        loadout.set_op(theirs)

        loadout.restore(before)
        self.assertEqual(sorted(op.uid for op in loadout.ops), sorted([kept.uid, theirs.uid]))
        self.assertIsNone(loadout.op(since.uid))
        self.assertTrue(loadout.op(kept.uid).authored)
        self.assertEqual(loadout.changes_made, len(before.changes))
        self.assertEqual([c.guid for c in loadout.changes_by_op(kept.uid)], ["2"])

    def test_and_a_checkpoints_ops_survive_a_round_trip(self):
        loadout = self._loadout()
        kept = self._typed(loadout, "2", "6")
        loadout.checkpoint("with one op")
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "p.annoload"
            loadout.save(path)
            loaded = Loadout.load(path)
        self.assertEqual([op["uid"] for op in loaded.history_of("")[0].ops], [kept.uid])

    def test_history_survives_a_round_trip(self):
        import tempfile

        loadout = self._loadout()
        loadout.checkpoint("Exported v1.0")
        loadout.own.version = "1.1"
        loadout.set_change(Change("2", "B", "/Values/C/D", "D", "5", "6"))
        loadout.checkpoint("Exported v1.1")

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "p.annoload"
            loadout.save(path)
            loaded = Loadout.load(path)

        self.assertEqual(len(loaded.history_of("")), 2)
        self.assertEqual(loaded.history_of("")[0].label, "Exported v1.0")
        self.assertEqual(loaded.history_of("")[1].version, "1.1")
        self.assertEqual(len(loaded), 2)
        self.assertFalse(loaded.changed_since_checkpoint())


class TestLocalisationLinkage(unittest.TestCase):
    """How display names actually resolve, per game.

    An asset's own GUID has nothing to do with its entry in texts_<language>.xml.
    Anno 1800 assets usually embed their text directly and separately carry a
    Values/Text/LineID cross-reference (matched against <Text><GUID>); Anno 117
    embeds nothing and carries Values/Text/OasisId instead (matched against
    <Text><LineId>). Both were verified against a real, currently-patched Anno
    1800 install and a real Anno 117 install -- this reproduces those shapes as
    small fixtures so the behavior doesn't require owning the game.
    """

    def _build(self, tmp, assets_xml, texts_xml=None):
        assets_path = Path(tmp) / "assets.xml"
        assets_path.write_text(assets_xml, encoding="utf-8")
        db_path = Path(tmp) / "index.sqlite"
        index_mod.build_index(assets_path, db_path)
        index = index_mod.AssetIndex(db_path, assets_path)
        if texts_xml is not None:
            texts_path = Path(tmp) / "texts_english.xml"
            texts_path.write_text(texts_xml, encoding="utf-8")
            index.load_texts(texts_path)
        return index

    def test_anno1800_style_cross_reference_by_lineid(self):
        """1800: texts keyed <GUID>, matched against the asset's Values/Text/LineID,
        not the asset's own GUID."""
        import tempfile

        assets_xml = (
            "<AssetList><Asset><Template>FactoryBuilding7</Template><Values>"
            "<Standard><GUID>1010278</GUID><Name>coastal_01</Name></Standard>"
            "<Text><LineID>5248</LineID></Text>"
            "</Values></Asset></AssetList>"
        )
        texts_xml = (
            "<TextExport><Texts>"
            "<Text><GUID>5248</GUID><Text>Fishery</Text></Text>"
            "</Texts></TextExport>"
        )
        with tempfile.TemporaryDirectory() as tmp:
            index = self._build(tmp, assets_xml, texts_xml)
            try:
                self.assertEqual(index.asset_row("1010278").display, "Fishery")
            finally:
                index.close()

    def test_anno117_style_cross_reference_by_oasisid_and_lineid_tag(self):
        """117: no embedded text at all; texts keyed <LineId>, matched against
        the asset's Values/Text/OasisId."""
        import tempfile

        assets_xml = (
            "<AssetList><Asset><Template>OrnamentalBuilding</Template><Values>"
            "<Standard><GUID>144821</GUID><Name>Ornament Source cDLC01</Name></Standard>"
            "<Text><OasisId>-6900336895044403528</OasisId></Text>"
            "</Values></Asset></AssetList>"
        )
        texts_xml = (
            "<TextExport><Texts>"
            "<Text><LineId>-6900336895044403528</LineId><Text>Marvellous Mosaics</Text></Text>"
            "</Texts></TextExport>"
        )
        with tempfile.TemporaryDirectory() as tmp:
            index = self._build(tmp, assets_xml, texts_xml)
            try:
                self.assertEqual(index.asset_row("144821").display, "Marvellous Mosaics")
            finally:
                index.close()

    def test_117_texts_actually_load_with_the_lineid_tag(self):
        """Regression: load_texts only ever checked <GUID>, so every 117 texts
        file loaded zero entries and every asset showed its internal name."""
        import tempfile

        texts_xml = (
            "<TextExport><Texts>"
            "<Text><LineId>1</LineId><Text>Alpha</Text></Text>"
            "<Text><LineId>2</LineId><Text>Beta</Text></Text>"
            "</Texts></TextExport>"
        )
        with tempfile.TemporaryDirectory() as tmp:
            assets_path = Path(tmp) / "assets.xml"
            assets_path.write_text("<AssetList></AssetList>", encoding="utf-8")
            db_path = Path(tmp) / "index.sqlite"
            index_mod.build_index(assets_path, db_path)
            index = index_mod.AssetIndex(db_path, assets_path)
            texts_path = Path(tmp) / "texts_english.xml"
            texts_path.write_text(texts_xml, encoding="utf-8")
            try:
                self.assertEqual(index.load_texts(texts_path), 2)
            finally:
                index.close()

    def test_embedded_loca_wins_over_a_stale_texts_file_entry(self):
        """A numeric LineID is not permanently owned by the asset that first used
        it: years of patches can reassign it to unrelated content, while the
        asset's own baked-in text is never wrong. Real-world case: a Schnapps
        product's LineID 5357 was reused for an unrelated "Remove" button label
        in a later patch; the product's embedded LocaText still correctly says
        "Schnapps". The embedded text must win.
        """
        import tempfile

        assets_xml = (
            "<AssetList><Asset><Template>Product</Template><Values>"
            "<Standard><GUID>1010216</GUID><Name>schnapps</Name></Standard>"
            "<Text><LocaText><English><Text>Schnapps</Text></English></LocaText>"
            "<LineID>5357</LineID></Text>"
            "</Values></Asset></AssetList>"
        )
        texts_xml = (
            "<TextExport><Texts>"
            "<Text><GUID>5357</GUID><Text>Remove</Text></Text>"
            "</Texts></TextExport>"
        )
        with tempfile.TemporaryDirectory() as tmp:
            index = self._build(tmp, assets_xml, texts_xml)
            try:
                self.assertEqual(index.asset_row("1010216").display, "Schnapps")
                self.assertTrue(any(r.guid == "1010216" for r in index.search("schnapps")))
            finally:
                index.close()

    def test_texts_file_still_fills_gaps_when_nothing_is_embedded(self):
        """The precedence flip must not disable the texts-file lookup entirely --
        it is the only source of display names for assets with no embedded text,
        which in practice means every Anno 117 asset."""
        import tempfile

        assets_xml = (
            "<AssetList><Asset><Template>ConstructionCategory</Template><Values>"
            "<Standard><GUID>77297</GUID><Name>Roman Infrastructure Aqueduct</Name></Standard>"
            "<Text><OasisId>-6902497501265074008</OasisId></Text>"
            "</Values></Asset></AssetList>"
        )
        texts_xml = (
            "<TextExport><Texts>"
            "<Text><LineId>-6902497501265074008</LineId><Text>Aqueduct</Text></Text>"
            "</Texts></TextExport>"
        )
        with tempfile.TemporaryDirectory() as tmp:
            index = self._build(tmp, assets_xml, texts_xml)
            try:
                self.assertEqual(index.asset_row("77297").display, "Aqueduct")
            finally:
                index.close()

    def test_asset_with_no_cross_reference_falls_back_to_its_own_guid(self):
        """An asset with neither LineID nor OasisId still gets a usable loca_key
        (its own GUID) rather than never being able to match anything."""
        import tempfile

        assets_xml = (
            "<AssetList><Asset><Template>ShipConfiguration</Template><Values>"
            "<Standard><GUID>82205</GUID><Name>Quinquireme - Roman</Name></Standard>"
            "</Values></Asset></AssetList>"
        )
        with tempfile.TemporaryDirectory() as tmp:
            index = self._build(tmp, assets_xml)
            try:
                # No texts loaded at all: falls back to the internal name.
                self.assertEqual(index.asset_row("82205").display, "Quinquireme - Roman")
            finally:
                index.close()

    def test_schema_bump_invalidates_a_pre_fix_cache(self):
        """Without this, someone who already indexed a game keeps the old,
        guid-keyed join forever and never sees the fix."""
        self.assertGreaterEqual(index_mod.SCHEMA_VERSION, 4)


class TestReferenceClassifier(unittest.TestCase):
    """A field is judged by how often it really holds a GUID, not by its name.

    The name-based guess, :func:`refs.could_be_reference`, is fine for annotating one row
    but cannot carry a graph: with 96,000 GUIDs in Anno 1800 about half of all
    plausible integers resolve by coincidence, and whole fields of quantities
    (``DurationMaximum``: 79,000 millisecond values, 67% of them valid GUIDs)
    look exactly like links one value at a time.
    """

    def test_a_field_named_as_a_guid_is_taken_at_its_word(self):
        self.assertTrue(refs_mod.is_confident("ProductGUID", 1, 1))
        self.assertTrue(refs_mod.is_confident("NeedGUID", 2, 2))
        # ...but only if it ever actually resolves.
        self.assertFalse(refs_mod.is_confident("SomeGUID", 500, 0))

    def test_a_field_that_almost_always_resolves_is_a_link(self):
        self.assertTrue(refs_mod.is_confident("Product", 1000, 1000))
        self.assertTrue(refs_mod.is_confident("Product", 1000, 960))

    def test_a_field_that_often_resolves_is_still_not_a_link(self):
        """The real trap: Anno 1800's audio durations resolve 67% of the time
        purely because so many integers happen to be valid GUIDs."""
        self.assertFalse(refs_mod.is_confident("DurationMaximum", 20336, 13706))
        self.assertFalse(refs_mod.is_confident("LineID", 53892, 16922))
        self.assertFalse(refs_mod.is_confident("Amount", 3619, 1476))

    def test_too_few_samples_to_judge_is_not_confidence(self):
        self.assertFalse(refs_mod.is_confident("Thing", 2, 2))
        # But it is worth offering as uncertain rather than dropping outright.
        self.assertTrue(refs_mod.is_plausible("Thing", 2, 2))

    def test_chance_level_resolution_is_not_even_plausible(self):
        """Below this the field is a quantity that sometimes collides, not a
        link with exceptions; offering it would bury the real ones."""
        self.assertFalse(refs_mod.is_plausible("InheritedIndex", 2718, 160))  # 6%
        self.assertFalse(refs_mod.is_plausible("X", 307, 67))                 # 22%
        self.assertTrue(refs_mod.is_plausible("Infotip", 443, 122))           # 28%
        self.assertTrue(refs_mod.is_plausible("Infolayer", 297, 195))         # 66%

    def test_a_field_whose_guids_are_plain_numbers_elsewhere_is_no_link(self):
        """Anno 1800's BuildModeRandomRotation: 90 or 180 degrees on 757
        buildings, and the game has an asset with each of those GUIDs.  By
        the rate alone it was a link, and every building pointed at Visitor."""
        self.assertTrue(refs_mod.is_confident("BuildModeRandomRotation", 757, 756))
        self.assertFalse(
            refs_mod.is_confident("BuildModeRandomRotation", 757, 756, telling=False)
        )
        # Nor one to offer as uncertain, at that rate or a middling one.
        self.assertFalse(
            refs_mod.is_plausible("BuildModeRandomRotation", 757, 756, telling=False)
        )
        self.assertFalse(refs_mod.is_plausible("MaximumHitPoints", 583, 315, telling=False))
        self.assertFalse(refs_mod.is_plausible("Thing", 2, 2, telling=False))
        # A name that says GUID is still taken at its word.
        self.assertTrue(refs_mod.is_confident("AmmoGUID", 1, 1, telling=False))

    def test_telling_counts_different_guids_and_not_how_often(self):
        self.assertFalse(refs_mod.is_telling(2, 2))    # 90 and 180, both plain numbers
        self.assertTrue(refs_mod.is_telling(2, 1))     # as many that are not as that are
        self.assertTrue(refs_mod.is_telling(103, 17))  # Profile, across Anno 1800
        self.assertFalse(refs_mod.is_telling(6, 5))    # a NotificationPriority
        self.assertFalse(refs_mod.is_telling(0, 0))    # nothing that resolves

    def test_a_number_is_plain_where_a_field_plainly_is_no_link(self):
        self.assertTrue(refs_mod.holds_plain_numbers("Weight", 3997, 715))    # 18%
        self.assertFalse(refs_mod.holds_plain_numbers("Weight", 3, 0))        # too few to say
        # Durations two thirds of which collide: not a link, and not plainly not.
        self.assertFalse(refs_mod.holds_plain_numbers("DurationMaximum", 20336, 13706))
        self.assertFalse(refs_mod.holds_plain_numbers("SomeGUID", 500, 10))

    def test_the_confidence_note_says_which_way_and_why(self):
        certain = refs_mod.confidence_note("Product", 1000, 1000)
        self.assertIn("100%", certain)
        self.assertIn("link", certain)
        doubtful = refs_mod.confidence_note("DurationMaximum", 20336, 13706)
        self.assertIn("67%", doubtful)
        self.assertIn("coincidence", doubtful)

    def test_the_confidence_note_never_calls_an_unconfident_field_a_link(self):
        """The note says what :func:`is_confident` decided, so a link drawn
        dashed is never explained as a sure one."""
        cases = [
            ("ProductGUID", 0, 0, True),      # a path the game never uses
            ("ProductGUID", 20, 0, True),     # used, and never a GUID
            ("Product", 100, 100, False),     # GUIDs that are plain numbers elsewhere
            ("Product", 1000, 1000, True),
            ("ProductGUID", 20, 5, True),
        ]
        for tag, seen, resolved, telling in cases:
            note = refs_mod.confidence_note(tag, seen, resolved, telling)
            sure = note.endswith("so this is a link.")
            self.assertEqual(
                sure, refs_mod.is_confident(tag, seen, resolved, telling),
                (tag, seen, resolved, telling, note),
            )
        self.assertIn(
            "plain numbers", refs_mod.confidence_note("Product", 100, 100, False)
        )

    def test_numeric_leaves_skips_text_and_over_long_ids(self):
        root = etree.fromstring(
            "<Values><A>1010340</A><B>not a number</B>"
            "<C>-6909446927054001500</C><D>12345678901234</D>"
            "<F>2130025000</F><G>2147483648</G>"
            "<Nested><E>2176</E></Nested></Values>"
        )
        found = {
            (tag, value) for _path, tag, value, _carries in refs_mod.numeric_leaves(root)
        }
        tags = {tag for tag, _value in found}
        self.assertIn(("A", "1010340"), found)
        self.assertIn(("E", "2176"), found)
        # A GUID from an Anno 117 mod's range is ten digits long.
        self.assertIn(("F", "2130025000"), found)
        self.assertNotIn("B", tags)
        self.assertNotIn("C", tags)  # a negative OasisId is not a GUID
        self.assertNotIn("D", tags)  # too long to be one either
        self.assertNotIn("G", tags)  # one past the largest a GUID can be

    def test_numeric_leaves_records_the_path_it_found_them_at(self):
        root = etree.fromstring(
            "<Values><Cost><Costs><Item><Ingredient>2176"
            "</Ingredient></Item></Costs></Cost></Values>"
        )
        self.assertEqual(
            list(refs_mod.numeric_leaves(root)),
            # The entry sets nothing else, so it carries no value of its own.
            [("/Values/Cost/Costs/Item", "Ingredient", "2176", False)],
        )


class TestReferenceGraph(CoreTestBase):
    """The graph built into the index while the file is being read."""

    def test_a_factory_points_at_the_goods_it_uses(self):
        links = self.index.references_from("1010340")
        by_target = {link.guid: link for link in links}
        self.assertIn("1010196", by_target)  # its input
        self.assertIn("1010197", by_target)  # its output
        self.assertIn("FactoryInputs", by_target["1010196"].field_label)

    def test_a_good_knows_what_consumes_it(self):
        incoming = self.index.references_to("1010196")
        self.assertIn("1010340", {link.guid for link in incoming})

    def test_the_two_directions_agree(self):
        for link in self.index.references_from("1010340"):
            back = {b.guid for b in self.index.references_to(link.guid)}
            self.assertIn("1010340", back, "%s lost its way back" % link.guid)

    def test_an_asset_is_never_linked_to_itself(self):
        """Every asset's own <GUID> resolves -- to itself. A self-loop says
        nothing, and would put a node on every graph pointing at its centre."""
        for guid in ("1010340", "1010030"):
            self.assertNotIn(
                guid, {link.guid for link in self.index.references_from(guid)}
            )
            self.assertNotIn(
                guid, {link.guid for link in self.index.references_to(guid)}
            )

    def test_a_quantity_field_is_not_a_reference_field(self):
        self.assertFalse(self.index.is_reference_field("/Values/Factory7", "CycleTime"))

    def test_a_reference_field_is_recognised_as_one(self):
        self.assertTrue(
            self.index.is_reference_field(
                "/Values/Factory7/FactoryInputs/Item", "Product"
            )
        )

    def test_reference_counts_match_the_links(self):
        out, into = self.index.reference_counts("1010340")
        self.assertEqual(out, len(self.index.references_from("1010340")))
        self.assertEqual(into, len(self.index.references_to("1010340")))

    def test_a_list_naming_an_asset_twice_through_one_field_is_one_link(self):
        """P2-17: the index answered with a row per mention, where the walk an
        edited asset gets answers with one per link -- the same untouched
        asset came back with different counts depending on which asked."""
        import tempfile

        from anno_modding_studio.core.changes import Loadout
        from anno_modding_studio.core.workspace import Workspace

        xml = (
            '<?xml version="1.0"?><Assets>'
            "<Asset><Template>Thing</Template><Values><Standard><GUID>6000</GUID></Standard>"
            "<Cost><Costs><Item><Ingredient>6001</Ingredient><Amount>1</Amount></Item>"
            "<Item><Ingredient>6001</Ingredient><Amount>2</Amount></Item></Costs></Cost>"
            "</Values></Asset>"
            "<Asset><Template>Good</Template><Values><Standard><GUID>6001</GUID></Standard>"
            "</Values></Asset></Assets>"
        )
        with tempfile.TemporaryDirectory() as tmp:
            assets = Path(tmp) / "assets.xml"
            assets.write_text(xml, encoding="utf-8")
            db = Path(tmp) / "i.sqlite"
            index_mod.build_index(assets, db)
            index = index_mod.AssetIndex(db, assets)
            try:
                links = index.references_from("6000", confident_only=False)
                self.assertEqual([(link.guid, link.tag) for link in links], [("6001", "Ingredient")])
                self.assertEqual(
                    [link.guid for link in index.references_to("6001", confident_only=False)], ["6000"]
                )
                self.assertEqual(index.reference_counts("6000", confident_only=False), (1, 0))
                walked = Workspace(index, Loadout())._walk_links("6000", False)
                self.assertEqual(
                    [(link.guid, link.path, link.tag, link.nominal) for link in walked],
                    [(link.guid, link.path, link.tag, link.nominal) for link in links],
                )
            finally:
                index.close()

    def test_field_label_reads_as_a_path_without_the_values_prefix(self):
        link = index_mod.ReferenceLink(
            guid="1", path="/Values/Cost/Costs/Item", tag="Ingredient"
        )
        self.assertEqual(link.field_label, "Cost › Costs › Item › Ingredient")


class TestCoincidenceIsNotALink(unittest.TestCase):
    """The whole point of measuring per field rather than per value.

    Two fields of the same shape holding numbers from the same range: one
    holds real links, the other quantities that happen to collide with a GUID.
    Judged a value at a time the two are indistinguishable; judged by the
    field's own record they are not.
    """

    @classmethod
    def setUpClass(cls):
        import tempfile

        # Ten assets in a ring. Three fields of identical shape, holding
        # numbers from the same range, differing in how often they land on a
        # real GUID:
        #   <Product>  always      -- a link
        #   <Hint>     4 of 10     -- a link with exceptions, or so it might be
        #   <Pick>     2 of 10     -- chance
        # Pick is a tag the game uses as a link elsewhere: sixteen of them on
        # each asset, under <Many>, every one a GUID, so the tag vouches for
        # the field.  Without that a field landing on a GUID 2 times in 10
        # holds plain numbers and is dropped for that, before its rate is
        # asked (TestPlainNumbersAreNotLinks); and its name is none the build
        # takes for a quantity.  So only the rate can drop it here.
        # The two picks that collide are not the number the hints hold, and
        # none of asset 5000's picks under <Many> is 5001.
        parts = ['<?xml version="1.0"?><Assets>']
        for i in range(10):
            guid = 5000 + i
            pick = 5001 if i < 2 else 700 + i
            hint = 5000 if i < 4 else 800 + i
            many = "".join(
                "<Item><Pick>%d</Pick></Item>" % (5000 + (i + 2 + k % 7) % 10)
                for k in range(16)
            )
            parts.append(
                "<Asset><Template>Thing</Template><Values>"
                "<Standard><GUID>%d</GUID><Name>thing_%d</Name></Standard>"
                "<Bits><Item><Product>%d</Product></Item></Bits>"
                "<Bits2><Pick>%d</Pick><Hint>%d</Hint></Bits2>"
                "<Many>%s</Many>"
                "</Values></Asset>"
                % (guid, i, 5000 + (i + 1) % 10, pick, hint, many)
            )
        parts.append("</Assets>")

        cls._tmp = tempfile.TemporaryDirectory()
        tmp = Path(cls._tmp.name)
        cls.assets = tmp / "assets.xml"
        cls.assets.write_text("".join(parts), encoding="utf-8")
        cls.db = tmp / "i.sqlite"
        index_mod.build_index(cls.assets, cls.db)
        cls.index = index_mod.AssetIndex(cls.db, cls.assets)

    @classmethod
    def tearDownClass(cls):
        cls.index.close()
        cls._tmp.cleanup()

    def test_the_field_that_always_resolves_is_a_link(self):
        self.assertTrue(self.index.is_reference_field("/Values/Bits/Item", "Product"))
        self.assertIn("5001", {l.guid for l in self.index.references_from("5000")})

    def test_the_field_that_only_resolves_by_luck_is_not(self):
        self.assertEqual(self.index.reference_field("/Values/Bits2", "Pick"), (10, 2, True))
        self.assertFalse(self.index.is_reference_field("/Values/Bits2", "Pick"))
        # So no confident edge is drawn from it, even though the value resolves.
        confident = {(l.guid, l.tag) for l in self.index.references_from("5000")}
        self.assertNotIn(("5001", "Pick"), confident)

    def test_a_field_resolving_at_chance_is_dropped_even_from_the_loose_view(self):
        """Turning on uncertain links must not mean turning on every
        coincidence: 2 in 10 is what a colliding quantity looks like, even
        in a tag that is a link elsewhere."""
        # What the fixture rests on: the tag vouches, and the name lets 5001
        # through, so nothing but the rate is left to drop the field.
        self.assertTrue(self.index.is_reference_field("/Values/Many/Item", "Pick"))
        self.assertEqual(refs_mod.least_inferred_guid("Pick"), refs_mod.MIN_INFERRED_GUID)
        loose = {
            (l.guid, l.tag)
            for l in self.index.references_from("5000", confident_only=False)
        }
        self.assertNotIn(("5001", "Pick"), loose)

    def test_a_field_resolving_too_often_to_dismiss_survives_as_uncertain(self):
        """4 in 10 could be a link the game only sometimes fills in, so it is
        kept -- marked, and hidden until asked for, rather than asserted."""
        self.assertEqual(self.index.reference_field("/Values/Bits2", "Hint"), (10, 4, True))
        self.assertFalse(self.index.is_reference_field("/Values/Bits2", "Hint"))
        uncertain = [
            link
            for link in self.index.references_from("5001", confident_only=False)
            if not link.confident
        ]
        self.assertEqual([(l.guid, l.tag) for l in uncertain], [("5000", "Hint")])


class TestPlainNumbersAreNotLinks(unittest.TestCase):
    """How often a field resolves says nothing when it holds the same few
    numbers every time, and they are numbers.

    Found by hand in Anno 1800: every building's ``BuildModeRandomRotation``,
    90 or 180 degrees, "referred to" the assets with GUIDs 90 and 180.  The
    field resolves 756 times in 757, and has only ever been right twice.  What
    tells it from a link is that the game uses 90 and 180 as plain numbers
    elsewhere, in fields that are plainly not links.
    """

    @classmethod
    def setUpClass(cls):
        import tempfile

        # Forty buildings, GUIDs 100 to 139, forty goods from 2000, one text.
        #   <Rotation>    110 or 120 on every building: always "resolves"
        #   <Height>      a count on each, and on two of them 110 and 120:
        #                 plainly not a link, which makes those plain numbers
        #   <Ingredient>  a different good on each building: a link
        #   <Label>       the one text on every building: a link that names a
        #                 single asset, and no plain number
        #   <Profile>     110 on every building, a plain number -- and a
        #                 different building on each good, which is what says
        #                 the tag is a link
        #   <Delay>       110 on half the goods and 7 on the rest: resolves
        #                 half the time, and only ever to a plain number
        #   <Link>        on two buildings only, building 105 and good 2007:
        #                 too rare to be sure of, so plausible, not confident
        extra = {
            1: "<Extra><Link>105</Link></Extra>",
            2: "<Extra><Link>2007</Link></Extra>",
        }
        parts = ['<?xml version="1.0"?><Assets>']
        for i in range(40):
            parts.append(
                "<Asset><Template>Building</Template><Values>"
                "<Standard><GUID>%d</GUID><Name>building_%d</Name></Standard>"
                "<Building><Rotation>%d</Rotation><Height>%d</Height></Building>"
                "<Cost><Item><Ingredient>%d</Ingredient><Amount>5</Amount></Item></Cost>"
                "<Skin><Label>2100</Label></Skin>"
                "<Crew><Profile>110</Profile></Crew>"
                "%s"
                "</Values></Asset>"
                % (100 + i, i, 110 if i % 2 == 0 else 120,
                   (110, 120)[i] if i < 2 else 3 + i, 2000 + i, extra.get(i, ""))
            )
            parts.append(
                "<Asset><Template>Good</Template><Values>"
                "<Standard><GUID>%d</GUID><Name>good_%d</Name></Standard>"
                "<Owner><Profile>%d</Profile></Owner>"
                "<Timing><Delay>%d</Delay></Timing>"
                "</Values></Asset>"
                % (2000 + i, i, 100 + i, 110 if i < 20 else 7)
            )
        parts.append(
            "<Asset><Template>Text</Template><Values>"
            "<Standard><GUID>2100</GUID><Name>label</Name></Standard></Values></Asset>"
        )
        parts.append("</Assets>")

        cls._tmp = tempfile.TemporaryDirectory()
        tmp = Path(cls._tmp.name)
        cls.assets = tmp / "assets.xml"
        cls.assets.write_text("".join(parts), encoding="utf-8")
        cls.db = tmp / "i.sqlite"
        index_mod.build_index(cls.assets, cls.db)
        cls.index = index_mod.AssetIndex(cls.db, cls.assets)

    @classmethod
    def tearDownClass(cls):
        cls.index.close()
        cls._tmp.cleanup()

    def links(self, guid: str) -> set:
        """Every link out of ``guid``, the uncertain ones too."""
        return {
            (link.guid, link.tag)
            for link in self.index.references_from(guid, confident_only=False)
        }

    def test_two_numbers_on_every_building_are_not_a_link(self):
        self.assertEqual(
            self.index.reference_field("/Values/Building", "Rotation"), (40, 40, False)
        )
        self.assertFalse(self.index.is_reference_field("/Values/Building", "Rotation"))
        self.assertFalse({tag for _guid, tag in self.links("105")} & {"Rotation", "Height"})

    def test_a_field_naming_a_different_asset_each_time_still_is(self):
        self.assertTrue(self.index.is_reference_field("/Values/Cost/Item", "Ingredient"))
        self.assertIn(("2005", "Ingredient"), self.links("105"))

    def test_and_so_is_one_that_only_ever_names_one_asset(self):
        """One number on every building, like the rotation -- and no field of
        plain numbers has it, which is the whole of the difference."""
        self.assertEqual(self.index.reference_field("/Values/Skin", "Label"), (40, 40, True))
        self.assertTrue(self.index.is_reference_field("/Values/Skin", "Label"))
        self.assertIn(("2100", "Label"), self.links("105"))

    def test_the_tag_across_the_game_vouches_for_a_field_that_cannot_say(self):
        """Anno 1800's Profile under one notification holds only 220, a plain
        number; Profile across the game holds 103 different assets."""
        self.assertEqual(self.index.reference_field("/Values/Crew", "Profile"), (40, 40, True))
        self.assertTrue(self.index.is_reference_field("/Values/Crew", "Profile"))
        self.assertIn(("110", "Profile"), self.links("105"))

    def test_plain_numbers_that_often_resolve_are_not_offered_as_uncertain(self):
        """Half the delays are 110, which is an asset: a middling rate, and it
        used to draw a dashed line from each of those goods to that building."""
        self.assertEqual(self.index.reference_field("/Values/Timing", "Delay"), (40, 20, False))
        self.assertNotIn("Delay", {tag for _guid, tag in self.links("2005")})

    def test_an_asset_read_as_the_mod_leaves_it_is_judged_the_same(self):
        from anno_modding_studio.core.changes import Loadout
        from anno_modding_studio.core.workspace import Workspace

        walked = Workspace(self.index, Loadout())._walk_links("105", False)
        self.assertEqual({(link.guid, link.tag) for link in walked}, self.links("105"))
        self.assertEqual(
            {(link.guid, link.tag) for link in walked},
            {("2005", "Ingredient"), ("2100", "Label"), ("110", "Profile")},
        )

    def test_a_field_the_game_never_uses_there_is_judged_by_its_name(self):
        """A10-1: where the shipped file has nothing to say about a field, the
        editor's Refers-to column guesses from the field's name and the value's
        size.  The References graph kept any number that landed on an asset,
        so a range of 110 this mod typed in was drawn as a link to building 110
        that the row itself does not show."""
        from anno_modding_studio.core.workspace import Workspace

        loadout = Loadout()
        loadout.set_change(
            Change(
                guid="105", asset_label="building_5", path="/Values/Building",
                tag="Building", op="add",
                payload="<Range>110</Range><Spare>120</Spare>"
                "<SpareGUID>121</SpareGUID><Partner>2006</Partner>",
            )
        )
        workspace = Workspace(self.index, loadout)
        for tag in ("Range", "Spare", "SpareGUID", "Partner"):
            self.assertEqual(
                self.index.reference_field("/Values/Building", tag)[0], 0, tag
            )
        added = {
            (link.guid, link.tag)
            for link in workspace.references_from("105", confident_only=False)
            if link.path == "/Values/Building"
        }
        self.assertEqual(added, {("121", "SpareGUID"), ("2006", "Partner")})
        for tag, value in (("Range", "110"), ("Spare", "120"),
                           ("SpareGUID", "121"), ("Partner", "2006")):
            self.assertEqual(
                (value, tag) in added,
                refs_mod.resolve(self.index, tag, value) is not None,
                tag,
            )

    def test_a_field_too_rare_to_be_sure_of_is_judged_by_its_name_too(self):
        """A10-1: a field the game does use here, but too rarely to be sure it
        is a link, gets an uncertain link in the graph only where the editor's
        Refers-to column shows one -- 110 in it is more likely a count than
        building 110.  The shipped file's graph and an edited asset's walk
        both used to draw it."""
        from anno_modding_studio.core.workspace import Workspace

        seen, resolved, telling = self.index.reference_field("/Values/Extra", "Link")
        self.assertEqual((seen, resolved, telling), (2, 2, True))
        self.assertTrue(refs_mod.is_plausible("Link", seen, resolved, telling))
        self.assertFalse(refs_mod.is_confident("Link", seen, resolved, telling))

        # The shipped file, as the index has it and as the walk reads it.
        self.assertNotIn(("105", "Link"), self.links("101"))
        self.assertIn(("2007", "Link"), self.links("102"))
        plain = Workspace(self.index, Loadout())
        for guid in ("101", "102"):
            self.assertEqual(
                {(link.guid, link.tag) for link in plain._walk_links(guid, False)},
                self.links(guid),
                guid,
            )

        # An edited asset: the same field with a value this mod typed in.
        loadout = Loadout()
        loadout.set_change(
            Change(
                guid="106", asset_label="building_6", path="/Values",
                tag="Values", op="add",
                payload="<Extra><Link>110</Link><Link>2008</Link></Extra>",
            )
        )
        workspace = Workspace(self.index, loadout)
        added = {
            (link.guid, link.tag)
            for link in workspace.references_from("106", confident_only=False)
            if link.path == "/Values/Extra"
        }
        self.assertEqual(added, {("2008", "Link")})
        # Each one as the editor row decides it.
        for value in ("110", "2008"):
            self.assertEqual(
                (value, "Link") in added,
                refs_mod.is_link("Link", value, seen, resolved, telling),
                value,
            )

    def test_the_editor_row_and_the_graph_answer_by_one_rule(self):
        """A10-1: the editor's Refers-to column, as it stood, against
        refs.is_link: the same answer for each kind of field."""
        cases = [
            ("Ingredient", "5", 40, 40, True),     # confident, small value
            ("Link", "110", 2, 2, True),           # plausible, small value
            ("Link", "2008", 2, 2, True),          # plausible, large value
            ("Range", "2008", 2, 2, True),         # plausible, a quantity's name
            ("Delay", "2008", 40, 20, False),      # not plausible
            ("Spare", "120", 0, 0, True),          # never used, small value
            ("Spare", "2008", 0, 0, True),         # never used, large value
            ("SpareGUID", "12", 0, 0, True),       # never used, named a GUID
        ]
        for tag, value, seen, resolved, telling in cases:
            if seen:
                row = refs_mod.is_plausible(tag, seen, resolved, telling) and (
                    refs_mod.is_confident(tag, seen, resolved, telling)
                    or refs_mod.could_be_reference(tag, value)
                )
            else:
                row = refs_mod.could_be_reference(tag, value)
            self.assertEqual(
                refs_mod.is_link(tag, value, seen, resolved, telling), row, (tag, value)
            )


class TestNominalLinks(unittest.TestCase):
    """Telling a reference that does something from one that just names an asset.

    A costs list can name a good and spend none of it, so an asset ends up
    "referenced" by buildings that never touch it -- Anno 117's Tiles is named
    that way by 110 assets with no amount attached. Whether that means anything
    is not in the file: a missing ``Amount`` is zero, but a missing
    ``Probability`` beside a pool entry means *normal*. What can be measured is
    whether entries at a path carry values at all.
    """

    @classmethod
    def setUpClass(cls):
        import tempfile

        # Three lists, all holding bare pointers some of the time:
        #   Costs/Item    -- half set an Amount, so a bare one is a placeholder
        #   Members/Item  -- membership only, never a value: bare is normal
        #   Pool/Item     -- an occasional optional weight, below the threshold
        parts = ['<?xml version="1.0"?><Assets>']
        for i in range(10):
            amount = "<Amount>%d</Amount>" % (i + 1) if i < 6 else ""
            weight = "<Weight>5</Weight>" if i < 1 else ""
            parts.append(
                "<Asset><Template>Thing</Template><Values>"
                "<Standard><GUID>%d</GUID><Name>thing_%d</Name></Standard>"
                "<Cost><Costs><Item><Ingredient>9000</Ingredient>%s</Item></Costs></Cost>"
                "<Roster><Members><Item><Member>9000</Member></Item></Members></Roster>"
                "<Pool><Entries><Item><Pick>9000</Pick>%s</Item></Entries></Pool>"
                "</Values></Asset>" % (9100 + i, i, amount, weight)
            )
        parts.append(
            "<Asset><Template>Good</Template><Values><Standard>"
            "<GUID>9000</GUID><Name>the_good</Name></Standard></Values></Asset>"
        )
        parts.append("</Assets>")

        cls._tmp = tempfile.TemporaryDirectory()
        tmp = Path(cls._tmp.name)
        cls.assets = tmp / "assets.xml"
        cls.assets.write_text("".join(parts), encoding="utf-8")
        cls.db = tmp / "index.sqlite"
        index_mod.build_index(cls.assets, cls.db)
        cls.index = index_mod.AssetIndex(cls.db, cls.assets)

    @classmethod
    def tearDownClass(cls):
        cls.index.close()
        cls._tmp.cleanup()

    def _links(self, path_tail):
        return [
            link
            for link in self.index.references_to("9000")
            if link.path.endswith(path_tail)
        ]

    def test_an_entry_that_sets_a_value_is_never_nominal(self):
        costs = {link.guid: link for link in self._links("Cost/Costs/Item")}
        self.assertFalse(costs["9100"].nominal, "this one spends 1")

    def test_an_entry_that_only_names_the_asset_is_nominal(self):
        costs = {link.guid: link for link in self._links("Cost/Costs/Item")}
        self.assertTrue(costs["9106"].nominal, "this one spends nothing")

    def test_a_list_that_never_carries_values_is_left_alone(self):
        """Membership is the whole meaning there, so a bare entry is not a
        placeholder -- it is simply what the list looks like."""
        members = self._links("Roster/Members/Item")
        self.assertEqual(len(members), 10)
        self.assertFalse(any(link.nominal for link in members))

    def test_an_occasional_optional_value_does_not_make_the_rest_nominal(self):
        """One entry in ten carrying a weight is not evidence that the other
        nine are placeholders -- that is the Anno 1800 asset-pool shape."""
        pool = self._links("Pool/Entries/Item")
        self.assertEqual(len(pool), 10)
        self.assertFalse(any(link.nominal for link in pool))

    def test_the_field_records_how_often_its_entries_say_something(self):
        entries, valued = self.index.reference_entries(
            "/Values/Cost/Costs/Item", "Ingredient"
        )
        self.assertEqual((entries, valued), (10, 6))

    def test_nominal_links_can_be_left_out(self):
        everything = self.index.references_to("9000")
        meaningful = self.index.references_to("9000", with_nominal=False)
        self.assertEqual(len(everything) - len(meaningful), 4)
        self.assertFalse(any(link.nominal for link in meaningful))

    def test_counts_follow_the_same_filter(self):
        _out, all_in = self.index.reference_counts("9000")
        _out, some_in = self.index.reference_counts("9000", with_nominal=False)
        self.assertEqual(all_in - some_in, 4)

    def test_the_threshold_is_where_the_shipped_games_separate(self):
        """Anno 117: Cost/Costs/Item sets an amount 53% of the time, while
        AssetPool/AssetList/Item sets its optional Probability only 15% of
        the time and means every entry regardless."""
        self.assertTrue(refs_mod.path_uses_values(100, 53))
        self.assertFalse(refs_mod.path_uses_values(100, 15))
        self.assertFalse(refs_mod.path_uses_values(100, 0))
        self.assertFalse(refs_mod.path_uses_values(0, 0))

    def test_an_entry_is_read_as_carrying_a_value_only_when_it_really_does(self):
        entry = etree.fromstring(
            "<Item><Ingredient>2176</Ingredient><Amount>3</Amount></Item>"
        )
        self.assertTrue(refs_mod.carries_value(entry, entry.find("Ingredient")))

        bare = etree.fromstring("<Item><Ingredient>2176</Ingredient></Item>")
        self.assertFalse(refs_mod.carries_value(bare, bare.find("Ingredient")))

        # A zero is not something set; an empty container is not either.
        zero = etree.fromstring(
            "<Item><Ingredient>2176</Ingredient><Amount>0</Amount></Item>"
        )
        self.assertFalse(refs_mod.carries_value(zero, zero.find("Ingredient")))
        hollow = etree.fromstring(
            "<Item><Ingredient>2176</Ingredient><Extra><Inner/></Extra></Item>"
        )
        self.assertFalse(refs_mod.carries_value(hollow, hollow.find("Ingredient")))

        # ...but a filled one is.
        filled = etree.fromstring(
            "<Item><Ingredient>2176</Ingredient><Extra><Inner>7</Inner></Extra></Item>"
        )
        self.assertTrue(refs_mod.carries_value(filled, filled.find("Ingredient")))

    # -- with a loadout's changes on top: both ends of a link agree ---------
    def _workspace(self, *changes):
        from anno_modding_studio.core.workspace import Workspace

        loadout = Loadout()
        for change in changes:
            loadout.set_change(change)
        return Workspace(self.index, loadout)

    def _ends(self, workspace, source, with_nominal=False):
        """Whether ``source``'s costs link to the good is there, at each end."""
        outgoing = [
            link
            for link in workspace.references_from(source, True, with_nominal)
            if link.guid == "9000" and link.path.endswith("Cost/Costs/Item")
        ]
        incoming = [
            link
            for link in workspace.references_to("9000", True, with_nominal)
            if link.guid == source and link.path.endswith("Cost/Costs/Item")
        ]
        return len(outgoing), len(incoming)

    def test_a_value_given_to_a_bare_entry_shows_at_the_other_end_too(self):
        """A9-5: 9106 names the good and spends none, so with name-only links
        hidden the index leaves it out -- and an asset left out was never
        asked again, whatever the loadout had since given the entry."""
        for path in (
            "/Values/Cost/Costs/Item",
            "/Values/Cost/Costs/Item[Ingredient='9000']",
        ):
            with self.subTest(path=path):
                workspace = self._workspace(
                    Change(
                        guid="9106", asset_label="thing_6", path=path, tag="Item",
                        op="add", payload="<Amount>3</Amount>",
                    )
                )
                self.assertEqual(self._ends(workspace, "9106"), (1, 1))
                # Four entries were bare; three are left.
                self.assertEqual(
                    len(workspace.references_to("9000"))
                    - len(workspace.references_to("9000", with_nominal=False)),
                    3,
                )

    def test_a_bare_entry_left_alone_stays_hidden(self):
        workspace = self._workspace(
            Change(
                guid="9106", asset_label="thing_6", path="/Values/Standard/Name",
                tag="Name", old_value="thing_6", new_value="renamed",
            )
        )
        self.assertEqual(self._ends(workspace, "9106"), (0, 0))
        self.assertEqual(self._ends(workspace, "9106", with_nominal=True), (1, 1))

    def test_a_new_asset_s_bare_entry_shows_at_the_other_end_too(self):
        """A9-6: what a loadout invents is never hidden for setting no value
        (see ``Workspace.references_from``), and that holds for what points at
        an asset as it does for what an asset points at."""
        workspace = self._workspace(
            Change(
                guid="9100", asset_label="invented  (new asset)", path="",
                tag="Asset", op="addNextSibling",
                payload="<Asset><Template>Thing</Template><Values>"
                "<Standard><GUID>1337000000</GUID><Name>invented</Name></Standard>"
                "<Cost><Costs><Item><Ingredient>9000</Ingredient></Item></Costs></Cost>"
                "</Values></Asset>",
            )
        )
        self.assertEqual(self._ends(workspace, "1337000000"), (1, 1))


class TestAssetFieldsNamedLikeStructure(unittest.TestCase):
    """An asset survives holding fields named after the file's own structure.

    The index streams the file and prunes as it goes, watching for ``<Asset>``
    and ``<Group>`` end events. Both names are *also* ordinary field names
    inside an asset -- ``<Group>`` in 19 Anno 117 assets, ``<Asset>`` holding a
    GUID in lists like ``UnlockAssets`` -- and a field's end event arrives
    before the asset's. Pruning on those cleared the field and deleted every
    sibling recorded before it, out of an asset still being read: on the real
    files that silently truncated assets and blanked roughly 15,000 links.
    """

    @classmethod
    def setUpClass(cls):
        import tempfile

        cls._tmp = tempfile.TemporaryDirectory()
        tmp = Path(cls._tmp.name)
        cls.assets = tmp / "assets.xml"
        cls.assets.write_text(
            '<?xml version="1.0"?><AssetList><Groups><Group>'
            "<Name>Buildings</Name><Assets>"
            # Fields before and after each structural-looking name, so anything
            # deleted by pruning is missed.
            "<Asset><Template>Thing</Template><Values>"
            "<Standard><GUID>4001</GUID><Name>first</Name></Standard>"
            "<Before><Kept>yes</Kept></Before>"
            "<Group><Inner>1</Inner></Group>"
            "<After><AlsoKept>yes</AlsoKept></After>"
            # Four of them: a field needs that much use before its own
            # numbers can say it holds links rather than numbers.
            "<Unlocks>"
            "<Item><Asset>4002</Asset></Item><Item><Asset>4003</Asset></Item>"
            "<Item><Asset>4004</Asset></Item><Item><Asset>4005</Asset></Item>"
            "</Unlocks>"
            "<Last><Tail>end</Tail></Last>"
            "</Values></Asset>"
            + "".join(
                "<Asset><Template>Thing</Template><Values><Standard>"
                "<GUID>%d</GUID><Name>other_%d</Name></Standard></Values></Asset>"
                % (guid, guid)
                for guid in (4002, 4003, 4004, 4005)
            )
            + "</Assets></Group></Groups></AssetList>",
            encoding="utf-8",
        )
        cls.db = tmp / "index.sqlite"
        cls.count = index_mod.build_index(cls.assets, cls.db)
        cls.index = index_mod.AssetIndex(cls.db, cls.assets)

    @classmethod
    def tearDownClass(cls):
        cls.index.close()
        cls._tmp.cleanup()

    def test_every_asset_is_indexed(self):
        self.assertEqual(self.count, 5)

    def test_nothing_before_or_after_an_inner_group_is_lost(self):
        values = self.index.asset_element("4001").find("Values")
        self.assertEqual(
            [child.tag for child in values],
            ["Standard", "Before", "Group", "After", "Unlocks", "Last"],
        )
        self.assertEqual(values.findtext("Before/Kept"), "yes")
        self.assertEqual(values.findtext("After/AlsoKept"), "yes")
        self.assertEqual(values.findtext("Last/Tail"), "end")

    def test_an_asset_field_keeps_the_guid_it_holds(self):
        """``<Asset>4002</Asset>`` is a link, and it was being emptied."""
        values = self.index.asset_element("4001").find("Values")
        self.assertEqual(
            [item.findtext("Asset") for item in values.findall("Unlocks/Item")],
            ["4002", "4003", "4004", "4005"],
        )

    def test_a_link_through_an_asset_field_reaches_the_graph(self):
        self.assertEqual(
            {link.guid for link in self.index.references_from("4001")},
            {"4002", "4003", "4004", "4005"},
        )

    def test_the_file_s_own_grouping_still_names_the_asset(self):
        """The pruning skip must not cost the grouping it was there for."""
        self.assertEqual(self.index.asset_row("4001").group_path, "Buildings")


class TestFindingAnOpByItsUid(unittest.TestCase):
    """An op is looked up, not searched for, and the list it is looked up in
    is a plain one that anything may change behind the loadout's back."""

    def setUp(self):
        self.loadout = Loadout()
        self.first = Op(expression="//Values/Trader", origin="Theirs")
        self.second = Op(expression="//Values/Cost", origin="Theirs")
        self.loadout.set_op(self.first)
        self.loadout.set_op(self.second)

    def test_it_is_found(self):
        self.assertIs(self.loadout.op(self.first.uid), self.first)
        self.assertIs(self.loadout.op(self.second.uid), self.second)
        self.assertIsNone(self.loadout.op("no such op"))

    def test_one_appended_to_the_list_is_found(self):
        self.loadout.op(self.first.uid)
        third = Op(expression="//Values/Building", origin="Theirs")
        self.loadout.ops.append(third)
        self.assertIs(self.loadout.op(third.uid), third)

    def test_a_list_put_in_its_place_is_the_one_searched(self):
        self.loadout.op(self.first.uid)
        third = Op(expression="//Values/Building", origin="Theirs")
        self.loadout.ops = [third, self.second]
        self.assertIsNone(self.loadout.op(self.first.uid))
        self.assertIs(self.loadout.op(third.uid), third)
        self.assertIs(self.loadout.op(self.second.uid), self.second)

    def test_one_set_again_takes_the_place_of_the_first(self):
        self.loadout.op(self.first.uid)
        again = Op(expression="//Values/Trader", origin="Theirs", uid=self.first.uid, active=False)
        self.loadout.set_op(again)
        self.assertEqual(len(self.loadout.ops), 2)
        self.assertIs(self.loadout.ops[0], again)
        self.assertIs(self.loadout.op(self.first.uid), again)

    def test_one_dropped_is_gone(self):
        self.loadout.op(self.second.uid)
        self.loadout.drop_op(self.first.uid)
        self.assertIsNone(self.loadout.op(self.first.uid))
        self.assertIs(self.loadout.op(self.second.uid), self.second)

    def test_reverting_an_asset_takes_the_ops_that_are_its_changes(self):
        """They were left behind, listed as changing nothing, and written out
        with the mod all the same: the asset was not reverted at all."""
        body = "<CycleTime>10</CycleTime></ModOp>"
        plain = Op(
            expression="/Values/FactoryBase/CycleTime", plain=True,
            source='<ModOp Type="replace" GUID="7" Path="/Values/FactoryBase/CycleTime">' + body,
        )
        # Not one change said another way, and this asset's alone all the same.
        merged = Op(source="<ModOp Type='merge' GUID='7' Path='/Values/FactoryBase'>" + body)
        # The short form names its asset at the head of the path, in Anno 117,
        # the game that has one.
        self.loadout.game = "anno117"
        short = Op(source='<ModOp Merge="@7/FactoryBase">' + body)
        other = Op(source='<ModOp Merge="@70/FactoryBase">' + body)
        both = Op(source='<ModOp Type="merge" GUID="7,8" Path="/Values/FactoryBase">' + body)
        wide = Op(
            expression="//Values/FactoryBase/CycleTime", searches=True,
            source='<ModOp Type="merge" Path="//Values/FactoryBase">' + body,
        )
        only_here = Op(source=wide.source, searches=True)
        for op in (plain, merged, short, other, both, wide, only_here):
            self.loadout.set_op(op)
        answers = (("7", plain), ("7", merged), ("7", short), ("7", other), ("7", both),
                   ("7", wide), ("8", wide), ("7", only_here))
        for guid, op in answers:
            self.loadout.set_change(Change(
                guid=guid, asset_label="x", path="/Values/FactoryBase/CycleTime" + op.uid,
                tag="CycleTime", old_value="15", new_value="10", op_uid=op.uid,
            ))
        # Ops the Studio could not read are written as they stand, too.
        from anno_modding_studio.core.changes import Unread

        unread = [
            Unread(source='<ModOp Type="replace" GUID="7" Path="/Values/X">' + body),
            Unread(source='<ModOp Type="replace" GUID="8" Path="/Values/X">' + body),
            Unread(source='<ModOp Type="replace" GUID="7" Path="/Values/X">' + body, origin="Theirs"),
        ]
        self.loadout.unread = list(unread)
        self.loadout.remove_asset("7")
        self.assertEqual(self.loadout.unread, unread[1:])
        self.assertIsNone(self.loadout.op(plain.uid))
        self.assertIsNone(self.loadout.op(merged.uid))
        self.assertIsNone(self.loadout.op(short.uid))
        # One that is about more than this asset is its mod's still: by the
        # others it names, or by searching, whatever it found today.
        for op in (other, both, wide, only_here, self.first):
            self.assertIs(self.loadout.op(op.uid), op)
        self.assertEqual([change.guid for change in self.loadout.changes.values()], ["8"])

    def test_an_op_is_known_by_its_asset_whatever_its_attributes_hold(self):
        """Its tag was read up to the first ``>``, which a condition may
        hold: the GUID after it went unseen, and the op stayed."""
        body = "<CycleTime>10</CycleTime></ModOp>"
        guarded = Op(source=(
            '<ModOp Type="replace" Condition="/Values/FactoryBase[CycleTime > 5]" '
            'GUID="7" Path="/Values/FactoryBase/CycleTime">' + body))
        self.loadout.set_op(guarded)
        self.loadout.set_change(Change(
            guid="7", asset_label="x", path="/Values/FactoryBase/CycleTime",
            tag="CycleTime", old_value="15", new_value="10", op_uid=guarded.uid,
        ))
        self.loadout.remove_asset("7")
        self.assertIsNone(self.loadout.op(guarded.uid))

    def test_and_by_the_names_its_game_reads(self):
        """Anno 117 reads ``merge=`` as ``Merge=``; Anno 1800 passes it by,
        so there the op names no asset and is not this one's to take."""
        body = "<CycleTime>10</CycleTime></ModOp>"
        for game, gone in (("anno117", True), ("anno1800", False)):
            with self.subTest(game=game):
                self.loadout.game = game
                op = Op(source='<ModOp merge="@7/FactoryBase">' + body)
                self.loadout.set_op(op)
                self.loadout.set_change(Change(
                    guid="7", asset_label="x", path="/Values/FactoryBase/CycleTime",
                    tag="CycleTime", old_value="15", new_value="10", op_uid=op.uid,
                ))
                self.loadout.remove_asset("7")
                self.assertEqual(self.loadout.op(op.uid) is None, gone)

    def test_every_op_of_an_unloaded_mod_is_gone(self):
        mine = Op(expression="//Values/Building", origin="")
        self.loadout.set_op(mine)
        self.loadout.op(self.first.uid)
        self.loadout.ops = [op for op in self.loadout.ops if op.origin != "Theirs"]
        self.assertIsNone(self.loadout.op(self.first.uid))
        self.assertIs(self.loadout.op(mine.uid), mine)


if __name__ == "__main__":
    unittest.main(verbosity=2)
