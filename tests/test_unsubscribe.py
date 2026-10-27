"""The game's mod browser's copies of mods the library has: which are offered,
where each stands by the game's own files, the list and the checklist's last
row -- against temporary folders.  The client's unsubscribe, which the probe
uses and the Studio does not, against a fake mod.io, never the network."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
import urllib.parse
from pathlib import Path
from unittest import mock

import _isolation  # noqa: F401 -- first: keeps the suite out of real files under any runner

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from support import LibraryFolders, install_mod  # noqa: E402

from anno_modding_studio.core import (  # noqa: E402
    convert,
    modio,
    modio_cache,
    truth,
    unsubscribe,
)
from anno_modding_studio.core.changes import Loadout, ModLayer  # noqa: E402
from anno_modding_studio.core.games import ANNO_1800  # noqa: E402

ME, OTHER = 7654321, 1234567


class FakeModio:
    """Answers /me, /me/subscribed and the DELETE that unsubscribes."""

    def __init__(self, subscribed, me=ME, refuse=()):
        self.subscribed = set(subscribed)
        self.me = me
        self.refuse = set(refuse)
        self.deleted = []
        self.headers = []

    def fetch(self, url: str, headers: dict) -> modio.Response:
        self.headers.append(headers)
        path = urllib.parse.urlparse(url).path
        if path.endswith("/me"):
            return modio.Response(200, {}, json.dumps({"id": self.me, "username": "bitwyse"}).encode())
        if path.endswith("/me/subscribed"):
            data = [{"id": i, "name": str(i)} for i in sorted(self.subscribed)]
            return modio.Response(200, {}, json.dumps(
                {"data": data, "result_count": len(data), "result_total": len(data)}).encode())
        return modio.Response(404, {}, b"{}")

    def delete(self, url: str, headers: dict, body: bytes) -> modio.Response:
        self.headers.append(headers)
        mod_id = int(urllib.parse.urlparse(url).path.split("/")[-2])
        self.deleted.append((urllib.parse.urlparse(url).path, mod_id))
        if mod_id in self.refuse:
            return modio.Response(403, {}, b'{"error":{"message":"no"}}')
        self.subscribed.discard(mod_id)
        return modio.Response(204, {}, b"")


def _client(server: FakeModio) -> modio.Client:
    return modio.Client("sekrit", fetch=server.fetch, deleter=server.delete, token="tok-abcdef",
                        sleep=lambda _s: None, game_id=4169)


class TestTheClient(unittest.TestCase):
    def test_unsubscribing_is_a_signed_in_delete_on_the_games_host(self):
        server = FakeModio({5, 6})
        _client(server).unsubscribe(4169, 5)
        self.assertEqual(server.deleted, [("/v1/games/4169/mods/5/subscribe", 5)])
        self.assertEqual(server.headers[-1]["Authorization"], "Bearer tok-abcdef")
        self.assertEqual(_client(server).subscriptions(4169), {6})


class TestWhatTheGamesClientRecords(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

    def test_the_accounts_holding_each_copy_are_read(self):
        state = self.root / "4169" / "metadata" / "state.json"
        state.parent.mkdir(parents=True)
        state.write_text(json.dumps({"Mods": [
            {"ID": 1, "Profile": {"name": "Shared"}, "SubscriptionCount": [ME, OTHER]},
            {"ID": 2, "Profile": {"name": "Mine"}, "SubscriptionCount": [ME]},
            {"ID": 3, "Profile": {"name": "Odd"}, "SubscriptionCount": "not a list"},
        ]}), encoding="utf-8")
        held = {one.mod_id: one.holders for one in modio_cache.installed(ANNO_1800, at=self.root)}
        self.assertEqual(held, {1: (OTHER, ME), 2: (ME,), 3: ()})

    def test_the_games_account_is_its_profile_and_never_its_sign_in(self):
        old = self.root / "4169" / "someone" / "user.json"
        new = self.root / "4169" / "someone-else" / "user.json"
        for path, who in ((old, OTHER), (new, ME)):
            path.parent.mkdir(parents=True)
            path.write_text(json.dumps({
                "OAuth": {"token": "the-games-own-token", "expiry": 1},
                "Profile": {"id": who, "username": "u%d" % who},
                "subscriptions": [7, 8] if who == ME else [],
            }), encoding="utf-8")
        os.utime(old, (1_000_000, 1_000_000))
        account = modio_cache.game_account(ANNO_1800, at=self.root)
        self.assertEqual(account, modio_cache.Account(ME, "u%d" % ME))
        self.assertNotIn("token", repr(account))
        self.assertIsNone(modio_cache.game_account(ANNO_1800, at=self.root / "nowhere"))
        # And what it last fetched of that account's subscriptions, from the same record.
        self.assertEqual(modio_cache.game_subscriptions(ANNO_1800, at=self.root), {7, 8})
        self.assertNotIn("OAuth", modio_cache._user_record(ANNO_1800, at=self.root))
        self.assertIsNone(modio_cache.game_subscriptions(ANNO_1800, at=self.root / "nowhere"))

    def test_an_empty_saved_list_says_nothing(self):
        # The client writes it back empty after a start that did not fetch the
        # account's subscriptions: 0, 18, 17 and 0 again, on one day, for an
        # account with 17 throughout.
        record = self.root / "4169" / "someone" / "user.json"
        record.parent.mkdir(parents=True)
        record.write_text(json.dumps({"Profile": {"id": ME}, "subscriptions": []}), encoding="utf-8")
        self.assertIsNone(modio_cache.game_subscriptions(ANNO_1800, at=self.root))


class _Converted(LibraryFolders):
    """One mod the library has copied out of the cache, one it has not, and
    one of the user's own."""

    def setUp(self):
        super().setUp()
        self.cached(7, "[Gameplay] Copied", "copied", "1")
        self.cached(8, "[Gameplay] Only Cached", "cached", "1")
        install_mod(self.mods, "[Gameplay] Mine", "mine", "1")
        self.hold(7, [ME, OTHER])
        self.hold(8, [ME])
        rows = [row for row in self.survey().rows if row.name == "[Gameplay] Copied"]
        done = convert.convert(rows, self.library, self.mods, self.game)
        self.assertEqual(len(done.copied), 1)

    def hold(self, mod_id: int, holders) -> None:
        state = self.cache / "metadata" / "state.json"
        held = json.loads(state.read_text(encoding="utf-8"))
        for entry in held["Mods"]:
            if entry["ID"] == mod_id:
                entry["SubscriptionCount"] = list(holders)
        state.write_text(json.dumps(held), encoding="utf-8")

    def found(self):
        return truth.read(Loadout(game=self.game), self.mods, library_dir=self.library)



class TestWhereEachStands(_Converted):
    def test_only_cache_copies_of_mods_the_library_holds(self):
        left = unsubscribe.left_in_cache(self.found(), account=ME, listed={7, 8})
        self.assertEqual([(one.modio_id, one.name, one.holders) for one in left],
                         [(7, "[Gameplay] Copied", (OTHER, ME))])
        self.assertFalse(left[0].loads)  # the library's link wins

    def test_a_subscription_of_several_mods_waits_for_the_library_to_hold_them_all(self):
        """Unsubscribing takes the numbered folder whole: offered while the
        library held one of two, the other was lost with it."""
        self.cached(9, "[Gameplay] One Of Two", "one-of-two")
        install_mod(self.cache / "mods" / "9", "[Gameplay] Other Of Two", "other-of-two", "1")
        self.hold(9, [ME])

        def offered():
            left = unsubscribe.left_in_cache(self.found(), account=ME, listed={7, 8, 9})
            return sorted(one.name for one in left if one.modio_id == 9)

        def bring_in(name):
            rows = [row for row in self.survey().rows if row.name == name]
            self.assertEqual(
                len(convert.convert(rows, self.library, self.mods, self.game).copied), 1
            )

        bring_in("[Gameplay] One Of Two")
        self.assertEqual(offered(), [])
        bring_in("[Gameplay] Other Of Two")
        self.assertEqual(offered(), ["[Gameplay] One Of Two", "[Gameplay] Other Of Two"])

    def test_subscribed_by_the_games_account_is_to_unsubscribe_in_game(self):
        [one] = unsubscribe.left_in_cache(self.found(), account=ME, listed={7})
        self.assertEqual(one.standing, unsubscribe.LISTED)
        self.assertEqual(one.describe(), "unsubscribe in game, and another account holds it too")
        self.assertIn("keeps its copy while that account does", one.explain())

    def test_unsubscribed_from_outside_the_game_is_kept_and_said_so(self):
        # Held for the game's account, and gone from the client's own list:
        # what the probe saw after unsubscribing through the API.
        [one] = unsubscribe.left_in_cache(self.found(), account=ME, listed={8})
        self.assertEqual(one.standing, unsubscribe.DROPPED)
        self.assertIn("unsubscribed elsewhere, kept", one.describe())
        self.assertIn("Subscribe to it again in the game's mod browser", one.explain())

    def test_held_only_for_another_account(self):
        self.hold(7, [OTHER])
        [one] = unsubscribe.left_in_cache(self.found(), account=ME, listed=set())
        self.assertEqual(one.standing, unsubscribe.OTHERS)
        self.assertEqual(one.describe(), "kept for another account (id %d)" % OTHER)
        # Which may be one that no longer exists, and then only a fresh start
        # of the folder lets go of it.
        self.assertIn("merged into another or closed, it never will", one.explain())
        self.assertIn("Starting the mod browser's folder afresh clears it", one.explain())

    def test_without_the_games_own_files_it_says_what_it_can(self):
        [one] = unsubscribe.left_in_cache(self.found())
        self.assertEqual(one.standing, unsubscribe.UNKNOWN)
        self.assertEqual(one.describe(), "unsubscribe in game")
        self.assertIn("If it is not among your subscriptions there", one.explain())

    def test_an_account_with_no_list_to_go_by_is_not_said_to_have_dropped_everything(self):
        [one] = unsubscribe.left_in_cache(self.found(), account=ME, listed=None)
        self.assertEqual(one.standing, unsubscribe.UNKNOWN)
        [one] = unsubscribe.left_in_cache(self.found(), account=ME, listed=set())
        self.assertEqual(one.standing, unsubscribe.UNKNOWN)

    def test_another_account_holding_it_too_is_said_with_no_list_to_go_by(self):
        """The client often writes its saved list back empty: a copy the game's
        account and another both hold is still held by the other."""
        for listed in (None, set()):
            [one] = unsubscribe.left_in_cache(self.found(), account=ME, listed=listed)
            self.assertEqual(one.standing, unsubscribe.UNKNOWN)
            self.assertTrue(one.shared)
            self.assertEqual(one.describe(), "unsubscribe in game, and another account holds it too")
            self.assertIn("keeps its copy while that account does", one.explain())

    def test_the_games_account_unknown_is_not_said_to_share_it(self):
        [one] = unsubscribe.left_in_cache(self.found(), account=0, listed=None)
        self.assertFalse(one.shared)
        self.assertEqual(one.describe(), "unsubscribe in game")

    def test_the_games_own_files_are_what_it_reads(self):
        record = Path(os.environ[modio_cache.ENV_USERS]) / "4169" / "u" / "user.json"
        record.parent.mkdir(parents=True, exist_ok=True)
        self.addCleanup(record.unlink)
        record.write_text(json.dumps({"Profile": {"id": ME, "username": "bitwyse"},
                                      "subscriptions": [7]}), encoding="utf-8")
        [one] = unsubscribe.left_in_cache(self.found())
        self.assertEqual((one.account, one.standing), (ME, unsubscribe.LISTED))


class TestAnno117HasNoBrowser(LibraryFolders):
    """No in-game browser: nothing is the browser's, nothing is to unsubscribe
    from, and nothing the Studio says suggests otherwise."""

    game = "anno117"

    def test_nothing_is_left_in_a_cache_it_does_not_have(self):
        from anno_modding_studio.core import games

        self.assertFalse(games.get(self.game).in_game_browser)
        self.assertTrue(games.get(ANNO_1800).in_game_browser)
        install_mod(self.mods, "[Gameplay] Mine", "mine", "1")
        found = truth.read(Loadout(game=self.game), self.mods, library_dir=self.library)
        self.assertEqual(unsubscribe.left_in_cache(found, account=ME, listed={7}), [])

    def test_the_survey_says_nothing_of_a_mod_browser(self):
        from PySide6.QtWidgets import QApplication, QLabel

        from anno_modding_studio.ui.convert_dialog import ConvertDialog

        QApplication.instance() or QApplication([])
        install_mod(self.mods, "[Gameplay] Mine", "mine", "1")
        dialog = ConvertDialog(self.survey())
        self.addCleanup(dialog.deleteLater)
        said = " ".join(label.text() for label in dialog.findChildren(QLabel))
        said += " " + dialog.help.help_text()
        self.assertNotIn("mod browser", said)
        self.assertIn(".ubi", said)


class TestTheList(_Converted):
    def setUp(self):
        super().setUp()
        from PySide6.QtWidgets import QApplication

        self.app = QApplication.instance() or QApplication([])

    def dialog(self, left, **kwargs):
        from anno_modding_studio.ui.unsubscribe_dialog import UnsubscribeDialog

        made = UnsubscribeDialog(left, **kwargs)
        self.addCleanup(made.deleteLater)
        return made

    def test_it_says_what_to_do_in_game_and_that_it_is_account_wide(self):
        left = unsubscribe.left_in_cache(self.found(), account=ME, listed={7})
        dialog = self.dialog(left, game_account=modio_cache.Account(ME, "bitwyse"))
        self.assertEqual(dialog.tree.topLevelItemCount(), 1)
        self.assertIn("1 to unsubscribe from in game as bitwyse.", dialog.summary.text())
        self.assertIn("held by another account on this machine as well", dialog.summary.text())
        self.assertTrue(dialog.summary.text().endswith(
            "the game rebuilds it from your subscriptions alone."))
        self.assertIn("on every machine you play on", dialog.wide.text())
        self.assertIn("in the game's own mod browser", dialog.hint.text())
        self.assertIn("leaves the game's copy where it is", dialog.help.help_text())

    def test_what_was_unsubscribed_elsewhere_is_said_with_the_way_out(self):
        left = unsubscribe.left_in_cache(self.found(), account=ME, listed={8})
        dialog = self.dialog(left)
        self.assertIn("1 was unsubscribed from outside the game, and the game still keeps it",
                      dialog.summary.text())

    def test_a_missing_version_sorts_last_either_way(self):
        from PySide6.QtCore import Qt

        def one(name, version, loads=False):
            return unsubscribe.Left(1, name, name, self.root / name, version, version, loads)

        dialog = self.dialog([one("a", "1.10"), one("b", ""), one("c", "1.9"), one("d", "", True)])
        for column in (2, 3):
            for order in (Qt.SortOrder.AscendingOrder, Qt.SortOrder.DescendingOrder):
                dialog.sorting.sort(column, order)
                names = [dialog.tree.topLevelItem(at).text(0) for at in range(4)]
                self.assertEqual(names[2:], ["b", "d"], (column, order))

    def test_look_again_reads_afresh(self):
        left = unsubscribe.left_in_cache(self.found(), account=ME, listed={7})
        dialog = self.dialog(left, look_again=lambda: [])
        self.assertFalse(dialog.again.isHidden())
        dialog.again.click()
        self.assertEqual(dialog.tree.topLevelItemCount(), 0)
        self.assertIn("keeps no copies of the library's mods any more", dialog.summary.text())


class TestStartingTheFolderAfresh(_Converted):
    """What a merged or closed account holds, no unsubscribe frees: set aside
    whole, the game's client rebuilds its folder from the signed-in account's
    subscriptions alone -- what the probe's reset saw."""

    def plan(self, **kwargs):
        return unsubscribe.afresh(self.found(), account=ME, **kwargs)

    def bring_the_rest_in(self):
        rows = [row for row in self.survey().rows if row.name == "[Gameplay] Only Cached"]
        convert.convert(rows, self.library, self.mods, self.game)

    def test_not_while_the_game_loads_a_mod_from_it(self):
        plan = self.plan()
        self.assertEqual(plan.loaded, ["[Gameplay] Only Cached"])
        self.assertFalse(plan.can)
        self.assertIn("loads 1 mod from the mod browser's folder ([Gameplay] Only Cached)", plan.why_not())
        with self.assertRaises(ValueError):
            unsubscribe.start_afresh(plan, self.root / "backups")
        self.assertTrue(self.cache.is_dir())

    def test_nor_while_the_game_runs(self):
        self.bring_the_rest_in()
        plan = self.plan(running=True)
        self.assertEqual(plan.loaded, [])
        self.assertIn("running", plan.why_not())

    def test_what_comes_back_and_what_stays_out(self):
        self.hold(7, [OTHER])
        plan = self.plan()
        self.assertEqual((plan.come_back, plan.stay_out), (["[Gameplay] Only Cached"], ["[Gameplay] Copied"]))

    def test_with_the_games_account_unknown_nothing_is_said_to_come_back_and_it_waits(self):
        # Which copies are the game's account's cannot be told: claiming them
        # all for it would count those only another account holds.
        self.bring_the_rest_in()
        self.hold(7, [OTHER])
        plan = unsubscribe.afresh(self.found(), account=0)
        self.assertEqual((plan.come_back, plan.stay_out), ([], []))
        self.assertFalse(plan.can)
        self.assertIn("not known here", plan.why_not())
        with self.assertRaises(ValueError):
            unsubscribe.start_afresh(plan, self.root / "backups")
        self.assertTrue(self.cache.is_dir())

    def test_the_folder_goes_into_the_backups_whole_with_a_note_and_the_sign_in_stays(self):
        record = Path(os.environ[modio_cache.ENV_USERS]) / "4169" / "u" / "user.json"
        record.parent.mkdir(parents=True, exist_ok=True)
        self.addCleanup(record.unlink)
        record.write_text(json.dumps({"Profile": {"id": ME}}), encoding="utf-8")
        self.bring_the_rest_in()
        plan = self.plan()
        self.assertTrue(plan.can, plan.why_not())
        aside = unsubscribe.start_afresh(plan, self.root / "backups")
        self.assertFalse(self.cache.exists())
        self.assertEqual(aside.parent.parent, self.root / "backups" / unsubscribe.AFRESH)
        self.assertTrue((aside / "metadata" / "state.json").is_file())
        note = json.loads((aside.parent / unsubscribe.AFRESH_NOTE).read_text(encoding="utf-8"))
        self.assertEqual(note["from"], str(self.cache))
        self.assertTrue(record.is_file())
        # And the game loads what it did: the library's copies.
        found = self.found()
        self.assertEqual(sorted(mod.name for mod in found.loading),
                         ["[Gameplay] Copied", "[Gameplay] Mine", "[Gameplay] Only Cached"])

    def test_anno_117_has_no_folder_of_the_kind(self):
        found = self.found()
        found.game = "anno117"
        self.assertIsNone(unsubscribe.afresh(found, account=ME))


class TestTheFreshStartInTheList(_Converted):
    def setUp(self):
        super().setUp()
        from PySide6.QtWidgets import QApplication

        # Only there to be: a dialog needs an application.
        QApplication.instance() or QApplication([])
        self.started = []

    def dialog(self, listed=frozenset({7}), plan=None):
        from anno_modding_studio.ui.unsubscribe_dialog import UnsubscribeDialog

        left = unsubscribe.left_in_cache(self.found(), account=ME, listed=set(listed))
        made = UnsubscribeDialog(
            left, afresh=plan or unsubscribe.afresh(self.found(), account=ME),
            start_afresh=lambda: self.started.append(1) or self.root / "backups" / "x",
            look_again=lambda: [],
        )
        self.addCleanup(made.deleteLater)
        return made

    def test_offered_for_copies_another_account_holds_and_only_when_it_can_be_done(self):
        dialog = self.dialog()
        self.assertFalse(dialog.afresh_button.isHidden())
        # Only Cached is loaded from the folder: not now, and it says why.
        self.assertFalse(dialog.afresh_button.isEnabled())
        self.assertIn("Bring it into the library first", dialog.afresh_button.toolTip())

    def test_offered_for_a_copy_another_account_holds_too_with_no_list_to_go_by(self):
        dialog = self.dialog(listed=())
        self.assertEqual([one.standing for one in dialog.left], [unsubscribe.UNKNOWN])
        self.assertFalse(dialog.afresh_button.isHidden())
        self.assertIn("1 is held by another account on this machine as well",
                      dialog.summary.text())

    def test_not_offered_when_only_the_games_account_holds_them(self):
        self.hold(7, [ME])
        self.assertTrue(self.dialog().afresh_button.isHidden())

    def test_asked_first_then_done_and_said(self):
        plan = unsubscribe.afresh(self.found(), account=ME)
        plan.loaded = []
        dialog = self.dialog(plan=plan)
        self.assertTrue(dialog.afresh_button.isEnabled())
        with mock.patch.object(dialog, "confirm_afresh", return_value=False):
            dialog.afresh_button.click()
        self.assertEqual(self.started, [])
        with mock.patch.object(dialog, "confirm_afresh", return_value=True):
            dialog.afresh_button.click()
        self.assertEqual(self.started, [1])
        self.assertIn("open its mod browser once", dialog.outcome.text())
        self.assertTrue(dialog.afresh_button.isHidden())

    def test_a_refusal_is_said_and_nothing_else(self):
        plan = unsubscribe.afresh(self.found(), account=ME)
        plan.loaded = []
        dialog = self.dialog(plan=plan)

        def refuse():
            raise ValueError("Anno 1800 is running; quit it first.")

        dialog._start_afresh = refuse
        with mock.patch.object(dialog, "confirm_afresh", return_value=True):
            dialog.afresh_button.click()
        self.assertEqual(dialog.outcome.text(), "The folder was not set aside: Anno 1800 is running; quit it first.")


class TestTheChecklistRow(_Converted):
    def setUp(self):
        super().setUp()
        import _isolation as isolation
        from PySide6.QtWidgets import QApplication

        from anno_modding_studio.core import launch
        from anno_modding_studio.ui.main_window import MainWindow

        self.app = QApplication.instance() or QApplication([])
        self.window = MainWindow()
        self.addCleanup(isolation.forget_settings)
        self.addCleanup(self.window.close)
        self.addCleanup(self.window.editor.search._debounce.stop)
        self.window.index = mock.Mock(source_path=str(self.root / "assets.xml"), asset_count=0, text_count=0)
        self.addCleanup(setattr, self.window, "index", None)
        for target, name, value in (
            (self.window.where, "mods_folder", str(self.mods)),
            (self.window.where, "library_dir", self.library),
            (launch, "install_for", None),
        ):
            patched = mock.patch.object(target, name, return_value=value)
            patched.start()
            self.addCleanup(patched.stop)
        self.window.loadout.game = ANNO_1800
        self.window.loadout.set_up_as("manage")
        self.window.loadout.mods_folder = str(self.mods)
        self.window.loadout.layers = [ModLayer(origin="[Gameplay] Copied", mod_id="copied")]
        self.addCleanup(self.window.files.mark_saved)

    def test_the_row_opens_the_list_and_goes_when_the_cache_does(self):
        self.window.refresh_truth()
        holder, said, button = self.window.source.checklist["subscribed"]
        self.assertFalse(holder.isHidden())
        self.assertEqual(said.text(), "1 copy the game's mod browser keeps of mods the library has")
        self.assertEqual(button.text(), "Unsubscribe in game…")
        self.assertTrue(self.window.source.checklist_done.isHidden())
        shown = []
        with mock.patch.object(self.window.modio, "show_unsubscribe", side_effect=shown.append):
            button.click()
        self.assertEqual([[one.modio_id for one in left] for left in shown], [[7]])
        # Nothing is asked of mod.io and nothing is written: the doing is the game's.
        self.assertFalse((self.library / "unsubscribed.json").exists())
        # The game's client removes its copy: the row goes.
        import shutil

        shutil.rmtree(self.cache / "mods" / "7")
        self.window.refresh_truth()
        self.assertTrue(holder.isHidden())

    def test_the_window_asks_the_disk_again_before_setting_the_folder_aside(self):
        from anno_modding_studio.core import places

        # The game is signed in: without its account, what comes back cannot be said.
        record = Path(os.environ[modio_cache.ENV_USERS]) / "4169" / "u" / "user.json"
        record.parent.mkdir(parents=True, exist_ok=True)
        self.addCleanup(record.unlink)
        record.write_text(json.dumps({"Profile": {"id": ME}}), encoding="utf-8")
        self.window.refresh_truth()
        # Only Cached still loads from the folder: refused, and nothing moves.
        with self.assertRaises(ValueError):
            self.window.modio.start_modio_afresh()
        self.assertTrue(self.cache.is_dir())
        rows = [row for row in self.survey().rows if row.name == "[Gameplay] Only Cached"]
        convert.convert(rows, self.library, self.mods, self.game)
        aside = self.window.modio.start_modio_afresh()
        self.assertFalse(self.cache.exists())
        self.assertTrue(str(aside).startswith(str(places.backups_dir() / unsubscribe.AFRESH)))
        self.assertIn("starts it afresh from your subscriptions", self.window.status_label.text())


if __name__ == "__main__":
    unittest.main()
