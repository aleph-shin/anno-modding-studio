"""New assets take their GUIDs from the community's register of ranges.

A creator listed at anno-mods/GuidRanges draws from their own range; anybody
else from the *Personal use* range the register sets aside.  The register is a
Markdown table, read as the repository serves it.  None of it ships with the
Studio: it is fetched the first time it is needed, and until then, or while
it cannot be reached, personal use is the only range offered.
"""

from __future__ import annotations

import http.client
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import _isolation  # noqa: F401 -- first: keeps the suite out of real files under any runner
from lxml import etree

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from support import SchemaTestBase  # noqa: E402

from anno_modding_studio.core import discovery, games, guidranges
from anno_modding_studio.core.changes import Loadout, own_record

TABLE = """# Some GUID Ranges

| User            |      Start ID |        End ID |
| :-------------- | ------------: | ------------: |
| *Personal use*  | 2.001.000.000 | 2.001.009.999 |

## Public GUID Ranges

| User            |      Start ID |        End ID |
| :-------------- | ------------: | ------------: |
| _**range start**_   | 1.337.471.142 |  |
| *reserved for special cases* | 1.337.471.142 | 1.337.471.999 |
| Jakob           |          2268 |          2268 |
| Sabular/Sabunata| 1.360.000.000 | 1.360.099.999 |
| Taludas (Noblesse oblige pop levels) | 601.387 | 601.389 |
| Taludas         | 1.999.000.000 | 1.999.999.999 |
| Jakob           | 1.500.010.001 | 1.500.023.999 |
| Archipelago Randomizer (DarkListener) | 1.701.000.000 | 1701.099.999 |
| Tiny            | 1.800.000.000 | 1.800.000.001 |
| *Personal use*  | 2.001.000.000 | 2.001.009.999 |
| _**range end**_   | | 2.147.483.647 |
"""


class TestParsing(unittest.TestCase):
    def setUp(self):
        self.ranges = guidranges.parse(TABLE)
        self.register = guidranges.Register(self.ranges)

    def test_every_numbered_row_is_a_range_and_the_edges_are_not(self):
        owners = [r.owner for r in self.ranges]
        self.assertNotIn("range start", " ".join(owners).lower())
        self.assertNotIn("range end", " ".join(owners).lower())
        self.assertIn("Jakob", owners)
        self.assertEqual(owners.count("Personal use"), 2)

    def test_dotted_numbers_and_the_registers_own_typo_are_read(self):
        darklistener = self.register.named("Archipelago Randomizer (DarkListener)")[0]
        self.assertEqual((darklistener.start, darklistener.end), (1701000000, 1701099999))

    def test_reserved_rows_are_not_claimable_and_personal_is_offered_once(self):
        claimable = self.register.claimable()
        self.assertFalse(any(r.reserved for r in claimable))
        self.assertEqual(sum(1 for r in claimable if r.personal), 1)

    def test_a_creator_matches_case_insensitively_and_by_alias(self):
        self.assertEqual(
            [r.start for r in self.register.for_creator("sabunata")], [1360000000]
        )
        self.assertEqual(
            [r.start for r in self.register.for_creator("  SABULAR ")], [1360000000]
        )
        self.assertEqual(self.register.for_creator("Nobody"), [])
        self.assertEqual(self.register.for_creator(""), [])

    def test_your_names_are_looked_up_in_their_order(self):
        """The author name first, then the others you have published under."""
        name, found = self.register.listed_as(["Nobody", " taludas ", "Jakob"])
        self.assertEqual((name, [r.start for r in found]), ("taludas", [1999000000, 601387]))
        self.assertEqual(self.register.listed_as(["Jakob", "Taludas"])[0], "Jakob")
        self.assertEqual(self.register.listed_as(["Nobody", ""]), ("", []))

    def test_a_creators_safe_range_comes_before_an_old_one(self):
        """Jakob holds 2268 from before the announcement; new work goes in the
        listed slice of the safe span."""
        jakob = self.register.for_creator("Jakob")
        self.assertEqual([r.start for r in jakob], [1500010001, 2268])

    def test_a_parenthetical_names_the_same_person(self):
        taludas = self.register.for_creator("Taludas")
        self.assertEqual([r.start for r in taludas], [1999000000, 601387])

    def test_personal_use_is_never_a_creator_match(self):
        self.assertEqual(self.register.for_creator("Personal use"), [])
        self.assertEqual(self.register.personal().start, 2001000000)

    def test_owners_of_a_guid(self):
        self.assertEqual([r.owner for r in self.register.owners_of("1500010001")], ["Jakob"])
        self.assertEqual(self.register.owners_of("abc"), [])

    def test_free_guid_skips_taken_and_walks_the_next_range(self):
        tiny = self.register.named("Tiny")
        jakob = self.register.for_creator("Jakob")
        taken = {"1800000000", "1800000001"}
        self.assertEqual(
            guidranges.Register.free_guid(tiny + jakob, taken.__contains__), 1500010001
        )
        self.assertIsNone(guidranges.Register.free_guid(tiny, taken.__contains__))


class _OwnCache(unittest.TestCase):
    """A cache folder of the test's own, and no failed fetch remembered."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._env = mock.patch.dict(os.environ, {"ANNO_STUDIO_CACHE": self._tmp.name})
        self._env.start()
        guidranges._UNREACHABLE.clear()
        self.addCleanup(guidranges._UNREACHABLE.clear)

    def tearDown(self):
        self._env.stop()
        self._tmp.cleanup()


class TestNothingFetched(_OwnCache):
    """No copy of the register ships with the Studio: it carries no licence."""

    def test_the_package_holds_no_copy_of_the_register(self):
        resources = Path(guidranges.__file__).resolve().parent.parent / "resources"
        self.assertFalse((resources / "guidranges").exists())

    def test_with_nothing_fetched_personal_use_is_the_only_range(self):
        for key in (games.ANNO_1800, games.ANNO_117):
            with self.subTest(game=key):
                register = guidranges.load(key)
                self.assertTrue(register.missing)
                self.assertEqual(register.source, guidranges.NOT_FETCHED)
                self.assertEqual(register.claimable(), [guidranges.PERSONAL_USE])
                personal = register.personal()
                self.assertEqual((personal.start, personal.end), (2001000000, 2001009999))
                self.assertTrue(personal.safe)
                self.assertLessEqual(personal.end, guidranges.SAFE_END)
                self.assertEqual(register.for_creator("Jakob"), [])

    def test_the_kept_personal_range_is_the_registers_own(self):
        """The numbers kept in the code are the ones the register lists."""
        listed = guidranges.Register(guidranges.parse(TABLE)).personal()
        self.assertEqual(listed, guidranges.PERSONAL_USE)

    def test_an_unknown_game_reads_as_the_default_one(self):
        self.assertEqual(guidranges.load("anno9999").ranges, guidranges.load(games.DEFAULT).ranges)


class TestRefresh(_OwnCache):
    """A fetched copy is kept beside the indexes and read from then on."""

    @staticmethod
    def _response(body: bytes):
        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *_):
                return False

            def read(self):
                return body

        return Response()

    def _serving(self, body: bytes):
        return mock.patch.object(
            guidranges.urllib.request, "urlopen", return_value=self._response(body)
        )

    def test_a_fetched_register_is_kept_and_then_loaded_first(self):
        with self._serving(TABLE.encode("utf-8")) as urlopen:
            register = guidranges.refresh(games.ANNO_1800)
        self.assertIn("/1800/guids.md", urlopen.call_args[0][0])
        self.assertEqual(register.source, "downloaded")
        self.assertTrue(guidranges.downloaded_path(games.ANNO_1800).is_file())
        loaded = guidranges.load(games.ANNO_1800)
        self.assertEqual(loaded.source, "downloaded")
        self.assertEqual(len(loaded.ranges), len(guidranges.parse(TABLE)))
        # The other game is untouched and still not fetched.
        self.assertTrue(guidranges.load(games.ANNO_117).missing)

    def test_a_response_that_is_not_a_table_is_refused_and_keeps_nothing(self):
        with self._serving(b"<html>rate limited</html>"), self.assertRaises(ValueError):
            guidranges.refresh(games.ANNO_117)
        self.assertFalse(guidranges.downloaded_path(games.ANNO_117).exists())
        self.assertTrue(guidranges.load(games.ANNO_117).missing)

    def test_a_kept_copy_that_has_gone_bad_reads_as_not_fetched(self):
        target = guidranges.downloaded_path(games.ANNO_1800)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("nothing here", encoding="utf-8")
        register = guidranges.load(games.ANNO_1800)
        self.assertTrue(register.missing)
        self.assertEqual(register.ranges, [guidranges.PERSONAL_USE])

    def test_first_use_fetches_the_register_and_keeps_it(self):
        with self._serving(TABLE.encode("utf-8")) as urlopen:
            register = guidranges.fetch_if_missing(games.ANNO_117)
        self.assertEqual(urlopen.call_count, 1)
        self.assertIn("/117/guids.md", urlopen.call_args[0][0])
        self.assertEqual(register.source, guidranges.DOWNLOADED)
        self.assertEqual([r.start for r in register.for_creator("Jakob")], [1500010001, 2268])

    def test_a_kept_copy_is_used_without_asking_again(self):
        with self._serving(TABLE.encode("utf-8")):
            guidranges.refresh(games.ANNO_1800)
        with self._serving(b"") as urlopen:
            register = guidranges.fetch_if_missing(games.ANNO_1800)
        urlopen.assert_not_called()
        self.assertEqual(register.source, guidranges.DOWNLOADED)

    def test_offline_on_first_use_gives_personal_use_and_is_not_retried(self):
        with mock.patch.object(
            guidranges.urllib.request, "urlopen", side_effect=OSError("no route to host")
        ) as urlopen:
            first = guidranges.fetch_if_missing(games.ANNO_1800)
            second = guidranges.fetch_if_missing(games.ANNO_1800)
        self.assertEqual(urlopen.call_count, 1)
        for register in (first, second):
            self.assertTrue(register.missing)
            self.assertEqual(register.source, guidranges.UNREACHABLE)
            self.assertEqual(register.claimable(), [guidranges.PERSONAL_USE])
        # The other game has not been tried, and is not marked.
        self.assertEqual(guidranges.load(games.ANNO_117).source, guidranges.NOT_FETCHED)

    def test_an_answer_cut_short_on_first_use_is_unreachable_too(self):
        with mock.patch.object(
            guidranges.urllib.request, "urlopen",
            side_effect=http.client.IncompleteRead(b"half", 10),
        ):
            register = guidranges.fetch_if_missing(games.ANNO_117)
        self.assertEqual(register.source, guidranges.UNREACHABLE)

    def test_update_now_tries_again_after_a_failure_and_clears_it(self):
        with mock.patch.object(guidranges.urllib.request, "urlopen", side_effect=OSError("offline")):
            guidranges.fetch_if_missing(games.ANNO_1800)
        with self._serving(TABLE.encode("utf-8")) as urlopen:
            fetched, failed = guidranges.refresh_all()
        self.assertEqual(urlopen.call_count, 2)
        self.assertEqual(set(fetched), {games.ANNO_1800, games.ANNO_117})
        self.assertEqual(failed, {})
        self.assertEqual(guidranges.load(games.ANNO_1800).source, guidranges.DOWNLOADED)

    def test_refresh_all_reports_a_failure_without_losing_the_other_game(self):
        calls = {"n": 0}

        def flaky(url, timeout):
            calls["n"] += 1
            if "/117/" in url:
                raise OSError("no route to host")
            return self._response(TABLE.encode("utf-8"))

        with mock.patch.object(guidranges.urllib.request, "urlopen", side_effect=flaky):
            fetched, failed = guidranges.refresh_all()
        self.assertEqual(set(fetched), {games.ANNO_1800})
        self.assertEqual(set(failed), {games.ANNO_117})
        self.assertIn("no route", failed[games.ANNO_117])

    def test_an_answer_cut_short_is_a_failure_of_that_game_too(self):
        # A10-38: IncompleteRead is no OSError, and escaped refresh_all.
        def flaky(url, timeout):
            if "/117/" in url:
                raise http.client.IncompleteRead(b"half", 10)
            return self._response(TABLE.encode("utf-8"))

        with mock.patch.object(guidranges.urllib.request, "urlopen", side_effect=flaky):
            fetched, failed = guidranges.refresh_all()
        self.assertEqual(set(fetched), {games.ANNO_1800})
        self.assertEqual(set(failed), {games.ANNO_117})


class TestLoadoutRemembersTheRange(unittest.TestCase):
    def test_guid_range_round_trips(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "p.json"
            loadout = Loadout(own=own_record(guid_range="Jakob"))
            loadout.save(path)
            self.assertEqual(Loadout.load(path).own.guid_range, "Jakob")
            # An older file without it reads as no choice made.
            loadout.own.guid_range = ""
            loadout.save(path)
            self.assertEqual(Loadout.load(path).own.guid_range, "")


class TestNewAssetDialogRanges(SchemaTestBase):
    """The dialog suggests from the right range, and says so."""

    def setUp(self):
        from PySide6.QtWidgets import QApplication

        self.app = QApplication.instance() or QApplication([])
        self.register = guidranges.Register(guidranges.parse(TABLE), source="test")
        self._dialogs = []

    def tearDown(self):
        for dialog in self._dialogs:
            dialog.deleteLater()

    def _dialog(self, loadout, **kwargs):
        from anno_modding_studio.ui.new_asset_dialog import NewAssetDialog

        dialog = NewAssetDialog(self.index, loadout, register=self.register, **kwargs)
        self._dialogs.append(dialog)
        return dialog

    def _accept(self, dialog, name="mine"):
        dialog._base_row = self.index.asset_row("1000")
        dialog.name_edit.setText(name)
        dialog._sync()
        self.assertEqual(dialog._problem(), "")
        dialog._accept()
        return dialog.change

    def test_a_listed_creator_draws_from_their_own_safe_range(self):
        dialog = self._dialog(Loadout(own=own_record(creator="jakob")))
        self.assertEqual(dialog.guid_edit.text(), "1500010001")
        self.assertEqual(dialog.chosen_range().owner, "Jakob")
        self.assertIn("Your range", dialog.range_note.text())
        self.assertEqual(dialog._warning(), "")

    def test_an_unlisted_creator_draws_from_personal_use_and_is_told_why(self):
        dialog = self._dialog(Loadout(own=own_record(creator="Nobody")))
        self.assertEqual(dialog.guid_edit.text(), "2001000000")
        self.assertTrue(dialog.chosen_range().personal)
        self.assertIn("Nobody is not in the", dialog.range_note.text())

    def test_an_unlisted_creator_draws_from_the_first_other_name_listed(self):
        dialog = self._dialog(Loadout(own=own_record(creator="Nobody"),
                                      aliases=["Ghost", "jakob", "Taludas"]))
        self.assertEqual(dialog.guid_edit.text(), "1500010001")
        self.assertEqual(dialog.chosen_range().owner, "Jakob")
        self.assertIn("Your range, as jakob is listed", dialog.range_note.text())
        self.assertEqual(dialog._warning(), "")

    def test_and_with_none_of_them_listed_is_told_so(self):
        dialog = self._dialog(Loadout(own=own_record(creator="Nobody"), aliases=["Ghost"]))
        self.assertTrue(dialog.chosen_range().personal)
        self.assertIn("None of your author names is in the", dialog.range_note.text())

    def test_no_creator_at_all_is_personal_use_too(self):
        dialog = self._dialog(Loadout())
        self.assertEqual(dialog.guid_edit.text(), "2001000000")
        self.assertIn("No author name is set", dialog.range_note.text())

    def test_the_suggestion_skips_guids_the_loadout_already_invents(self):
        loadout = Loadout(own=own_record(creator="Jakob"))
        first = self._accept(self._dialog(loadout), "first")
        loadout.set_change(first)
        asset = etree.fromstring(first.payload.encode("utf-8"))
        self.assertEqual(asset.findtext("Values/Standard/GUID"), "1500010001")

        second = self._dialog(loadout)
        self.assertEqual(second.guid_edit.text(), "1500010002")
        second.guid_edit.setText("1500010001")
        self.assertIn("already creates", second._problem())

    def test_the_suggestion_skips_guids_a_sibling_mod_claims_in_the_range(self):
        with tempfile.TemporaryDirectory() as tmp:
            mods = Path(tmp)
            asset_file = mods / "[Gameplay] Theirs" / games.get(games.ANNO_1800).asset_relpath
            asset_file.parent.mkdir(parents=True)
            asset_file.write_text(
                '<ModOps><ModOp Type="addNextSibling" GUID="1000"><Asset><Values>'
                "<Standard><GUID>2001000000</GUID></Standard></Values></Asset>"
                "</ModOp></ModOps>",
                encoding="utf-8",
            )
            dialog = self._dialog(
                Loadout(), mods=discovery.mods_in(mods, discovery.WHERE_MODS)
            )
            self.assertEqual(dialog.guid_edit.text(), "2001000001")

    def test_a_full_range_walks_the_creators_next_then_personal_use(self):
        register = guidranges.Register(
            [*guidranges.parse(TABLE), guidranges.GuidRange("Tiny", 1_500_010_001, 1_500_010_001)],
        )
        from anno_modding_studio.ui.new_asset_dialog import NewAssetDialog

        loadout = Loadout(own=own_record(creator="Tiny"))
        loadout.set_change(
            self._accept(NewAssetDialog(self.index, loadout, register=register), "a")
        )
        loadout.set_change(
            self._accept(NewAssetDialog(self.index, loadout, register=register), "b")
        )
        third = NewAssetDialog(self.index, loadout, register=register)
        self._dialogs.append(third)
        # Tiny's two ranges hold three numbers in all, lowest range first.
        self.assertEqual(third.guid_edit.text(), "1800000001")
        loadout.set_change(self._accept(third, "c"))
        fourth = NewAssetDialog(self.index, loadout, register=register)
        self._dialogs.append(fourth)
        # Not 1500010002, which is Jakob's: personal use instead.
        self.assertEqual(fourth.guid_edit.text(), "2001000000")
        self.assertIn("outside the chosen range", fourth._warning())

    def test_a_guid_in_somebody_elses_range_is_allowed_but_flagged(self):
        dialog = self._dialog(Loadout(own=own_record(creator="Jakob")))
        dialog._base_row = self.index.asset_row("1000")
        dialog.name_edit.setText("x")
        dialog.guid_edit.setText("1360000005")
        dialog._sync()
        self.assertEqual(dialog._problem(), "")
        self.assertTrue(dialog.create_button.isEnabled())
        self.assertIn("Sabular/Sabunata's listed range", dialog._warning())
        self.assertEqual(dialog.status.text(), dialog._warning())

    def test_a_guid_below_the_safe_span_is_flagged_as_such(self):
        dialog = self._dialog(Loadout(own=own_record(creator="Jakob")))
        dialog.guid_edit.setText("1337000000")
        self.assertIn("below the span", dialog._warning())
        dialog.guid_edit.setText("2147483648")
        self.assertIn("largest the game can hold", dialog._warning())

    def test_a_creators_own_old_range_is_theirs_and_not_flagged_as_anothers(self):
        dialog = self._dialog(Loadout(own=own_record(creator="Jakob")))
        dialog.guid_edit.setText("2268")
        self.assertIn("below the span", dialog._warning())
        self.assertNotIn("listed range", dialog._warning())

    def test_choosing_another_range_moves_a_suggestion_but_not_a_typed_guid(self):
        dialog = self._dialog(Loadout(own=own_record(creator="Jakob")))
        dialog._select_range(self.register.personal())
        self.assertEqual(dialog.guid_edit.text(), "2001000000")
        self.assertIn("not a range of your own", dialog.range_note.text())

        dialog.guid_edit.setText("2001000777")
        dialog._select_range(self.register.for_creator("Jakob")[0])
        self.assertEqual(dialog.guid_edit.text(), "2001000777")

    def test_a_range_chosen_over_the_creators_own_is_kept_on_the_loadout(self):
        loadout = Loadout(own=own_record(creator="Sabular"))
        dialog = self._dialog(loadout)
        dialog._select_range(self.register.for_creator("Taludas")[0])
        self._accept(dialog)
        self.assertEqual(loadout.own.guid_range, "Taludas")

        again = self._dialog(loadout)
        self.assertEqual(again.chosen_range().owner, "Taludas")
        self.assertEqual(again.guid_edit.text(), "1999000000")
        self.assertIn("Somebody else's", again.range_note.text())

        # Chosen back, the loadout forgets the override.
        again._select_range(self.register.for_creator("Sabular")[0])
        self._accept(again)
        self.assertEqual(loadout.own.guid_range, "")

    def test_the_range_chosen_in_settings_is_where_it_starts_and_is_not_kept(self):
        """Settings names a range per game: a listing that spells your name
        another way. It is yours, so the loadout need not remember it."""
        loadout = Loadout(own=own_record(creator="Sabular"))
        dialog = self._dialog(loadout, preferred="Taludas")
        self.assertEqual(dialog.chosen_range().owner, "Taludas")
        self.assertEqual(dialog.guid_edit.text(), "1999000000")
        self.assertIn("chosen in Settings", dialog.range_note.text())
        dialog.guid_edit.setText("1999000005")
        self.assertNotIn("listed range", dialog._warning())
        self._accept(dialog)
        self.assertEqual(loadout.own.guid_range, "")

        # Chosen away from it for this loadout, that is kept.
        again = self._dialog(loadout, preferred="Taludas")
        own = self.register.for_creator("Sabular")[0]
        again._select_range(own)
        self._accept(again, name="mine2")
        self.assertEqual(loadout.own.guid_range, own.owner)

    def test_a_kept_range_the_register_no_longer_lists_is_ignored(self):
        loadout = Loadout(own=own_record(creator="Jakob", guid_range="Gone"))
        dialog = self._dialog(loadout)
        self.assertEqual(dialog.chosen_range().owner, "Jakob")

    def test_without_a_register_given_the_loadouts_game_decides(self):
        from anno_modding_studio.ui.new_asset_dialog import NewAssetDialog

        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(
            os.environ, {"ANNO_STUDIO_CACHE": tmp}
        ):
            # Vaeloridas is listed in the copy fetched for Anno 1800 only.
            listed = TABLE + "| Vaeloridas | 1.416.000.000 | 1.416.009.999 |\n"
            for game, text in ((games.ANNO_1800, listed), (games.ANNO_117, TABLE)):
                target = guidranges.downloaded_path(game)
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(text, encoding="utf-8")
            for game, expected in ((games.ANNO_1800, "1416000000"), (games.ANNO_117, "2001000000")):
                with self.subTest(game=game):
                    dialog = NewAssetDialog(self.index, Loadout(own=own_record(creator="Vaeloridas"), game=game))
                    self._dialogs.append(dialog)
                    self.assertEqual(dialog.guid_edit.text(), expected)

    def _offline(self, loadout):
        register = guidranges.Register([guidranges.PERSONAL_USE], source=guidranges.UNREACHABLE)
        from anno_modding_studio.ui.new_asset_dialog import NewAssetDialog

        dialog = NewAssetDialog(self.index, loadout, register=register)
        self._dialogs.append(dialog)
        return dialog

    def test_offline_the_dialog_offers_personal_use_and_says_why(self):
        """A listed creator still gets a safe GUID, and is told the register
        could not be reached, not that they are missing from it."""
        dialog = self._offline(Loadout(own=own_record(creator="Jakob")))
        self.assertEqual(dialog.range_combo.count(), 1)
        self.assertTrue(dialog.chosen_range().personal)
        self.assertEqual(dialog.guid_edit.text(), "2001000000")
        note = dialog.range_note.text()
        self.assertIn("could not be reached", note)
        self.assertIn("Update now", note)
        self.assertNotIn("is not in the", note)
        self.assertEqual(dialog._warning(), "")

    def test_offline_a_range_kept_on_the_loadout_is_left_there(self):
        loadout = Loadout(own=own_record(creator="Sabular", guid_range="Taludas"))
        dialog = self._offline(loadout)
        self._accept(dialog)
        self.assertEqual(loadout.own.guid_range, "Taludas")

    def test_a_register_never_fetched_says_so_in_its_own_words(self):
        from anno_modding_studio.ui.new_asset_dialog import describe_range

        register = guidranges.Register([guidranges.PERSONAL_USE], source=guidranges.NOT_FETCHED)
        text = describe_range(guidranges.PERSONAL_USE, register, "Jakob")
        self.assertIn("has not been fetched yet", text)
        self.assertNotIn("could not be reached", text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
