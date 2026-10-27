"""The two mod.io dialogs, driven offscreen against a fake mod.io.

Nothing here touches the network: the account dialog is handed a checker and
the updates dialog a transport, and both answer the way mod.io did when it was
tried.  The key store is given a stand-in for DPAPI, so the file it writes is
still not the key in the clear.
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import _isolation  # first: keeps the suite out of real files under any runner

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402
from support import FakeServer, downloadable_mod, modio_mod, reverse_bytes  # noqa: E402

from anno_modding_studio.core import library, modio, secrets, updates  # noqa: E402
from anno_modding_studio.core.changes import Loadout, ModLayer  # noqa: E402
from anno_modding_studio.core.games import ANNO_1800  # noqa: E402
from anno_modding_studio.ui.modio_account_panel import ModioAccountPanel  # noqa: E402
from anno_modding_studio.ui.modio_updates_dialog import ModioUpdatesDialog  # noqa: E402

_app = QApplication.instance() or QApplication([])


class _WithStore(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.store = secrets.Store(
            Path(self._tmp.name) / "credentials.bin", wrap=reverse_bytes, unwrap=reverse_bytes
        )


class TestTheAccount(_WithStore):
    def test_a_key_modio_accepts_is_kept_and_not_in_the_clear(self):
        asked = []
        dialog = ModioAccountPanel(self.store, check=asked.append)
        self.assertFalse(dialog.save_button.isEnabled())
        dialog.key_edit.setText("  abc123  ")
        self.assertTrue(dialog.save_button.isEnabled())
        dialog._save()
        self.assertEqual(asked, ["abc123"])
        self.assertIsNotNone(dialog.saved)
        self.assertEqual(self.store.load().api_key, "abc123")
        self.assertNotIn(b"abc123", self.store.path.read_bytes())
        self.assertIn("accepted", dialog.note)

    def test_a_key_modio_refuses_is_not_kept(self):
        def refuse(_key):
            raise modio.KeyRejected("no")

        dialog = ModioAccountPanel(self.store, check=refuse)
        dialog.key_edit.setText("bad")
        dialog._save()
        self.assertFalse(self.store.exists)
        self.assertIn("did not accept", dialog.status.text())

    def test_modio_being_unreachable_is_not_a_verdict_on_the_key(self):
        def down(_key):
            raise modio.ModioError("could not reach mod.io")

        dialog = ModioAccountPanel(self.store, check=down)
        dialog.key_edit.setText("maybe")
        dialog._save()
        self.assertEqual(self.store.load().api_key, "maybe")
        self.assertIn("could not be reached", dialog.note)

    def test_a_key_with_a_space_in_it_is_not_a_key(self):
        dialog = ModioAccountPanel(self.store, check=lambda _k: None)
        dialog.key_edit.setText("two words")
        dialog._save()
        self.assertFalse(self.store.exists)
        self.assertIn("no spaces", dialog.status.text())

    def test_forgetting_removes_the_file(self):
        self.store.save(secrets.Credentials(api_key="old"))
        dialog = ModioAccountPanel(self.store, check=lambda _k: None)
        self.assertEqual(dialog.key_edit.text(), "old")
        self.assertTrue(dialog.forget_button.isEnabled())
        dialog._forget()
        self.assertTrue(dialog.forgotten)
        self.assertFalse(self.store.exists)
        self.assertFalse(dialog.forget_button.isEnabled())

    def test_enter_in_a_field_does_what_the_field_is_for(self):
        """It pressed Forget key, the first button the dialog would give it
        to: the emailed code typed and Enter pressed, and the saved key and
        the sign-in were gone."""
        from PySide6.QtCore import Qt
        from PySide6.QtTest import QTest

        self.store.save(secrets.Credentials(api_key="old", email="a@b.c"))
        asked = []
        dialog = ModioAccountPanel(self.store, check=asked.append)
        self.addCleanup(dialog.deleteLater)
        dialog.show()
        sent, signed = [], []
        dialog._send_code = lambda: sent.append(1)
        dialog._sign_in = lambda: signed.append(1)
        dialog.code_request_button.clicked.disconnect()
        dialog.code_request_button.clicked.connect(dialog._send_code)
        dialog.sign_in_button.clicked.disconnect()
        dialog.sign_in_button.clicked.connect(dialog._sign_in)
        dialog.code_edit.setText("ABCDE")
        for field in (dialog.email_edit, dialog.code_edit, dialog.key_edit):
            for key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                field.setFocus()
                QTest.keyClick(field, key)
                self.assertEqual(self.store.load().api_key, "old", field.placeholderText())
                self.assertFalse(dialog.forgotten)
        self.assertFalse(any(button.isDefault() or button.autoDefault()
                             for button in (dialog.forget_button, dialog.sign_out_button)))
        # Each field's Enter went to its own button.
        self.assertEqual((len(sent), len(signed), asked), (2, 2, ["old", "old"]))

    def test_forgetting_the_key_takes_the_sign_in_with_it_and_says_so(self):
        self.store.save(secrets.Credentials(api_key="old", token="tok-123456", email="a@b.c"))
        dialog = ModioAccountPanel(self.store, check=lambda _k: None)
        dialog._forget()
        saved = self.store.load()
        self.assertEqual((saved.api_key, saved.token, saved.email), ("", "", "a@b.c"))
        self.assertIn("sign-in with it", dialog.note)

    def test_the_key_is_hidden_until_asked_to_show(self):
        from PySide6.QtWidgets import QLineEdit

        dialog = ModioAccountPanel(self.store, check=lambda _k: None)
        self.assertEqual(dialog.key_edit.echoMode(), QLineEdit.EchoMode.Password)
        dialog.show_box.setChecked(True)
        self.assertEqual(dialog.key_edit.echoMode(), QLineEdit.EchoMode.Normal)


def _loadout(*layers) -> Loadout:
    loadout = Loadout(game=ANNO_1800)
    for layer in layers:
        loadout.set_layer(layer)
    return loadout


def _finish(dialog: ModioUpdatesDialog) -> None:
    """Ask, as Check does, then let the worker run to the end and its
    signals land."""
    if dialog._worker is None:
        dialog.check_button.click()
    if dialog._worker is not None:
        dialog._worker.wait(10000)
    for _ in range(20):
        _app.processEvents()
        if dialog.verdicts or not dialog.progress.isVisible():
            break


class TestTheUpdatesDialog(_WithStore):
    def test_without_a_key_it_says_so_and_offers_the_account_dialog(self):
        loadout = _loadout(ModLayer(origin="A", name="A"))
        opened = []
        dialog = ModioUpdatesDialog(
            loadout, ANNO_1800, self.store, logged={}, cache={}, open_account=lambda: opened.append(True), library_dir=library.game_dir(ANNO_1800)
        )
        self.assertIn("API key", dialog.summary.text())
        self.assertTrue(dialog.account_button.isVisible() or not dialog.account_button.isHidden())
        self.assertIsNone(dialog._worker)
        self.assertFalse(dialog.check_button.isEnabled())
        dialog._account()
        self.assertEqual(opened, [True])

    def test_a_mod_row_asks_about_its_own_mod_alone(self):
        loadout = _loadout(ModLayer(origin="A", name="A"), ModLayer(origin="B", name="B"))
        dialog = ModioUpdatesDialog(loadout, ANNO_1800, self.store, logged={}, cache={},
                                    library_dir=library.game_dir(ANNO_1800), only=["B"])
        self.assertEqual([match.origin for match in dialog.matches], ["B"])
        every = ModioUpdatesDialog(loadout, ANNO_1800, self.store, logged={}, cache={},
                                   library_dir=library.game_dir(ANNO_1800))
        self.assertEqual([match.origin for match in every.matches], ["A", "B"])

    def test_with_nothing_loaded_there_is_nothing_to_check(self):
        dialog = ModioUpdatesDialog(_loadout(), ANNO_1800, self.store, logged={}, cache={}, library_dir=library.game_dir(ANNO_1800))
        self.assertIn("nothing to check", dialog.summary.text())

    def test_a_copy_unpacked_from_a_zip_is_made_sure_of_by_it(self):
        """The name finds the mod; the zip's hash says it is that mod, and
        which of its files -- an older one here, though the version reads
        the same."""
        import hashlib
        import tempfile
        import zipfile

        from support import downloadable_mod

        self.store.save(secrets.Credentials(api_key="k"))
        with tempfile.TemporaryDirectory() as made:
            root = Path(made)
            mods, folder = root / "mods", root / "library"
            mods.mkdir()
            dropped = root / "fishery.zip"
            with zipfile.ZipFile(dropped, "w") as zipped:
                zipped.writestr("[Gameplay] Large Fishery/modinfo.json", '{"Version": "1.0"}')
            done = library.install_zip(dropped, folder, mods)
            md5 = hashlib.md5(dropped.read_bytes()).hexdigest()
            server = FakeServer(
                [downloadable_mod(555, "Large Fishery", "1.0", b"the current file")],
                files={555: [{"id": 5550, "filehash": {"md5": "0" * 32}},
                             {"id": 5549, "filehash": {"md5": md5}}]},
            )
            loadout = _loadout(ModLayer(
                origin="[Gameplay] Large Fishery", name="Large Fishery", version="1.0",
                folder=str(done.linked[0].link),
            ))
            dialog = ModioUpdatesDialog(
                loadout, ANNO_1800, self.store, fetch=server, logged={}, cache={}, library_dir=folder,
            )
            _finish(dialog)
            (verdict,) = dialog.verdicts
            self.assertEqual((verdict.how, verdict.installed_file, verdict.status),
                             (updates.HASH, 5549, updates.UPDATE))
            self.assertEqual(dialog.table.item(0, 6).text(), "its file")

    def test_matched_and_named_mods_are_checked_and_the_matches_learned(self):
        self.store.save(secrets.Credentials(api_key="k"))
        server = FakeServer([
            modio_mod(3229975, "Nate's Windmill (Jakob)", version="2.1.0", modfile=901),
            modio_mod(555, "Large Fishery (Kurila)", version="1.0"),
        ])
        loadout = _loadout(
            ModLayer(
                origin="3229975/[Building] Nate's Windmill (Jakob)", name="Nate's Windmill (Jakob)",
                mod_id="jakob_Nates_Windmill", version="2.0.1",
            ),
            ModLayer(origin="[Gameplay] Large Fishery (Kurila)", name="Large Fishery (Kurila)", version="1.0"),
            ModLayer(origin="Nowhere", name="A Mod Nobody Uploaded", version="3"),
        )
        dialog = ModioUpdatesDialog(
            loadout, ANNO_1800, self.store, fetch=server,
            logged={"3229975/[Building] Nate's Windmill (Jakob)": 3229975}, cache={}, library_dir=library.game_dir(ANNO_1800),
        )
        _finish(dialog)
        self.assertEqual(len(dialog.verdicts), 3)
        by_origin = {v.origin: v for v in dialog.verdicts}
        self.assertEqual(by_origin["3229975/[Building] Nate's Windmill (Jakob)"].status, updates.UPDATE)
        self.assertEqual(by_origin["3229975/[Building] Nate's Windmill (Jakob)"].how, updates.LOG)
        self.assertEqual(by_origin["[Gameplay] Large Fishery (Kurila)"].status, updates.CURRENT)
        self.assertEqual(by_origin["[Gameplay] Large Fishery (Kurila)"].how, updates.NAME)
        self.assertEqual(by_origin["Nowhere"].status, updates.UNMATCHED)
        self.assertEqual(
            dialog.learned,
            {"3229975/[Building] Nate's Windmill (Jakob)": 3229975, "[Gameplay] Large Fishery (Kurila)": 555},
        )
        # A row per mod, in load order; drawn with the update at the top and
        # the mod nobody uploaded at the bottom.
        self.assertEqual(dialog.table.rowCount(), 3)
        row = dialog._row_of
        self.assertEqual(dialog.table.item(row["3229975/[Building] Nate's Windmill (Jakob)"], 5).text(),
                         "Update available")
        self.assertEqual(dialog.table.item(row["Nowhere"], 5).text(), "Not found on mod.io")
        vertical = dialog.table.verticalHeader()
        self.assertEqual([dialog.table.item(vertical.logicalIndex(at), 5).text() for at in range(3)],
                         ["Update available", "Up to date", "Not found on mod.io"])
        self.assertIn("1 with an update", dialog.summary.text())
        self.assertIn("1 matched by name only", dialog.summary.text())
        # Every request carried the key and went to the game's own host.
        for url, _headers in server.calls:
            self.assertIn("api_key=k", url)
            self.assertTrue(url.startswith("https://g-4169.modapi.io/"))

    def test_a_refused_key_is_reported_not_raised(self):
        self.store.save(secrets.Credentials(api_key="stale"))
        server = FakeServer(statuses=[(401, "")])
        loadout = _loadout(ModLayer(origin="A", name="A", modio_id=1))
        dialog = ModioUpdatesDialog(loadout, ANNO_1800, self.store, fetch=server, logged={}, cache={}, library_dir=library.game_dir(ANNO_1800))
        _finish(dialog)
        self.assertEqual(dialog.verdicts, [])
        self.assertIn("did not accept", dialog.summary.text())

    NAMES = ("Zed", "Mid", "Gone", "Alpha", "None")

    def _dialog(self, server=None):
        self.store.save(secrets.Credentials(api_key="k"))
        dialog = ModioUpdatesDialog(
            _loadout(*(ModLayer(origin=name, name=name, version="1") for name in self.NAMES)),
            ANNO_1800, self.store, fetch=server or FakeServer(),
            logged={}, cache={}, library_dir=library.game_dir(ANNO_1800),
        )
        self.addCleanup(dialog.deleteLater)
        return dialog

    def _drawn(self, dialog, column=1):
        vertical = dialog.table.verticalHeader()
        return [dialog.table.item(vertical.logicalIndex(at), column).text()
                for at in range(dialog.table.rowCount()) if not dialog.table.isRowHidden(
                    vertical.logicalIndex(at))]

    def _filled(self):
        """A dialog showing five verdicts, as a check would leave it."""
        dialog = self._dialog()
        dialog._on_checked([
            updates.Verdict("Zed", "Zed", updates.UPDATE, modio_id=1),
            updates.Verdict("Mid", "Mid", updates.DIFFERENT, modio_id=2),
            updates.Verdict("Gone", "Gone", updates.MISSING, modio_id=3),
            updates.Verdict("Alpha", "Alpha", updates.CURRENT, modio_id=4),
            updates.Verdict("None", "None", updates.UNMATCHED),
        ])
        _app.processEvents()
        return dialog

    # -- nothing until asked ---------------------------------------------------
    def test_opening_it_asks_mod_io_nothing(self):
        """Opened to see what it does, or to check two mods of four hundred,
        it set off a check of every one."""
        server = FakeServer()
        dialog = self._dialog(server)
        _app.processEvents()
        self.assertEqual(server.calls, [])
        self.assertIsNone(dialog._worker)
        self.assertEqual(dialog.check_button.text().replace("&", ""), "Check 5 mods")
        self.assertEqual({dialog.table.item(row, 5).text() for row in range(5)}, {"Not checked"})

    def test_only_the_mods_ticked_are_asked_about(self):
        server = FakeServer([modio_mod(4, "Alpha", version="1")])
        dialog = self._dialog(server)
        # Every row starts ticked, and the box over the ticks unticks them.
        dialog.tick_box.click()
        self.assertEqual(dialog._ticked(), [])
        self.assertFalse(dialog.check_button.isEnabled())
        # Filtered, it ticks what is shown and nothing hidden.
        dialog.filter_box.setText("alp")
        self.assertEqual(self._drawn(dialog), ["Alpha"])
        dialog.tick_box.click()
        self.assertEqual([match.origin for match in dialog._ticked()], ["Alpha"])
        dialog.filter_box.clear()
        self.assertEqual(dialog.check_button.text().replace("&", ""), "Check 1 mod")
        _finish(dialog)
        self.assertEqual([verdict.origin for verdict in dialog.verdicts], ["Alpha"])
        self.assertEqual(dialog.table.item(dialog._row_of["Alpha"], 5).text(), "Up to date")
        self.assertEqual(dialog.table.item(dialog._row_of["Zed"], 5).text(), "Not checked")
        self.assertEqual(dialog.learned, {"Alpha": 4})

    def test_each_answer_is_shown_as_it_lands(self):
        """The rate limit is most of a check's time: a row is filled in as
        soon as its answer is in, not when the last one is."""
        dialog = self._dialog()
        for row in range(5):
            dialog._said(row, "waiting")
        dialog._on_verdict(updates.Verdict("Gone", "Gone", updates.MISSING, modio_id=3,
                                           latest_version="2"))
        gone = dialog._row_of["Gone"]
        self.assertEqual(dialog.table.item(gone, 5).text(), "No longer on mod.io")
        self.assertEqual(dialog.table.item(gone, 3).text(), "2")
        self.assertEqual(dialog.table.item(dialog._row_of["Zed"], 5).text(), "Waiting")

    def test_the_worker_hands_over_each_verdict_as_it_is_decided(self):
        from anno_modding_studio.ui.workers import UpdatesWorker

        server = FakeServer([modio_mod(1, "Zed", version="2"), modio_mod(4, "Alpha", version="1")])
        matches = [updates.Match(origin="Zed", name="Zed", mod_id="", installed_version="1", modio_id=1,
                                 how=updates.LOADOUT),
                   updates.Match(origin="Alpha", name="Alpha", mod_id="", installed_version="1")]
        worker = UpdatesWorker("k", 4169, matches, fetch=server)
        handed, at_the_end = [], []
        worker.checked.connect(lambda verdict: handed.append(verdict.origin))
        worker.succeeded.connect(lambda verdicts: at_the_end.append(len(verdicts)))
        worker.run()  # here, not on a thread: the signals land at once
        self.assertEqual(handed, ["Zed", "Alpha"])
        self.assertEqual(at_the_end, [2])

    def test_stop_ends_the_check_and_keeps_what_was_answered(self):
        dialog = self._dialog()
        running = mock.Mock()
        running.isRunning.return_value = True
        dialog._worker = running
        dialog._refresh_check()
        self.assertEqual(dialog.check_button.text().replace("&", ""), "Stop")
        dialog.check_button.click()
        running.cancel.assert_called_once_with()
        running.isRunning.return_value = False
        dialog._on_verdict(updates.Verdict("Zed", "Zed", updates.UPDATE, modio_id=1))
        dialog._on_done()
        self.assertEqual(dialog.table.item(dialog._row_of["Zed"], 5).text(), "Update available")
        self.assertEqual(dialog.table.item(dialog._row_of["Mid"], 5).text(), "Not checked")
        self.assertIn("1 mod checked", dialog.summary.text())

    def test_a_bundled_mod_starts_unticked_and_says_why_it_is_not_updated_here(self):
        """Sixty-five of a real loadout's 137 mods were bundled in others. Each
        was looked up by its own name, and one with an update could not take
        it from here, which only the greyed button's tooltip said."""
        self.store.save(secrets.Credentials(api_key="k"))
        loadout = _loadout(
            ModLayer(origin="[Addon] Extended Arctic", name="Extended Arctic", version="2"),
            ModLayer(origin="[Addon] Extended Arctic/arctic_fishery", name="Arctic Fisheries",
                     version="1.0", folder=str(Path.cwd())),
        )
        dialog = ModioUpdatesDialog(loadout, ANNO_1800, self.store, fetch=FakeServer(),
                                    logged={}, cache={}, library_dir=library.game_dir(ANNO_1800))
        self.addCleanup(dialog.deleteLater)
        host, bundled = dialog._row_of["[Addon] Extended Arctic"], dialog._row_of["[Addon] Extended Arctic/arctic_fishery"]
        self.assertEqual([match.origin for match in dialog._ticked()], ["[Addon] Extended Arctic"])
        self.assertIn("Extended Arctic", dialog.table.item(bundled, 1).toolTip())
        latest = modio.summarise(downloadable_mod(9, "Arctic Fisheries", "1.1", b"zip"))
        dialog._on_verdict(updates.Verdict("[Addon] Extended Arctic/arctic_fishery", "Arctic Fisheries",
                                           updates.UPDATE, modio_id=9, latest=latest))
        dialog.table.selectRow(bundled)
        self.assertFalse(dialog.update_button.isEnabled())
        self.assertFalse(dialog.why_label.isHidden())
        self.assertEqual(dialog.why_label.text(),
                         "This mod is bundled inside Extended Arctic. Update that mod to update it.")
        # Not said of a mod with nothing to update.
        dialog._on_verdict(updates.Verdict("[Addon] Extended Arctic", "Extended Arctic", updates.CURRENT))
        dialog.table.selectRow(host)
        self.assertTrue(dialog.why_label.isHidden())

    def test_the_mods_it_cannot_update_are_counted_from_the_rows_shown(self):
        """A ticked mod the filter hid was still counted, under "Choose its
        row to see why", a row that could not be chosen."""
        self.store.save(secrets.Credentials(api_key="k"))
        loadout = _loadout(
            ModLayer(origin="[Addon] Extended Arctic", name="Extended Arctic", version="2"),
            ModLayer(origin="[Addon] Extended Arctic/arctic_fishery", name="Arctic Fisheries",
                     version="1.0", folder=str(Path.cwd())),
        )
        dialog = ModioUpdatesDialog(loadout, ANNO_1800, self.store, fetch=FakeServer(),
                                    logged={}, cache={}, library_dir=library.game_dir(ANNO_1800))
        self.addCleanup(dialog.deleteLater)
        bundled = dialog._row_of["[Addon] Extended Arctic/arctic_fishery"]
        dialog.table.item(bundled, 0).setCheckState(Qt.CheckState.Checked)
        latest = modio.summarise(downloadable_mod(9, "Arctic Fisheries", "1.1", b"zip"))
        dialog._on_verdict(updates.Verdict("[Addon] Extended Arctic/arctic_fishery", "Arctic Fisheries",
                                           updates.UPDATE, modio_id=9, latest=latest))
        dialog._refresh_open()
        self.assertIn("1 ticked mod with an update", dialog.why_label.text())
        dialog.filter_box.setText("nothing like it")
        self.assertTrue(dialog.table.isRowHidden(bundled))
        self.assertTrue(dialog.why_label.isHidden(), dialog.why_label.text())
        dialog.filter_box.clear()
        self.assertIn("1 ticked mod with an update", dialog.why_label.text())

    def test_download_and_update_follows_the_ticks_not_the_row_chosen(self):
        """It followed the row chosen, and a mod ticked alone, with no row
        chosen, left the button grey."""
        import tempfile

        dialog = self._dialog()
        with tempfile.TemporaryDirectory() as made:
            for name in self.NAMES:
                dialog.loadout.layer(name).folder = made
            latest = modio.summarise(downloadable_mod(1, "Zed", "2", b"zip"))
            for name in ("Zed", "Mid"):
                dialog._on_verdict(updates.Verdict(name, name, updates.UPDATE, modio_id=1, latest=latest))
            dialog._on_done()
            dialog.tick_box.tick(False)
            self.assertFalse(dialog.update_button.isEnabled())
            dialog.table.item(dialog._row_of["Zed"], 0).setCheckState(Qt.CheckState.Checked)
            self.assertTrue(dialog.update_button.isEnabled())
            self.assertEqual(dialog.update_button.text().replace("&", ""), "Download and update 1 mod")
            dialog.table.item(dialog._row_of["Mid"], 0).setCheckState(Qt.CheckState.Checked)
            self.assertEqual(dialog.update_button.text().replace("&", ""), "Download and update 2 mods")
            # More than one is asked about first.
            with _isolation.answering(False) as asked:
                dialog.update_button.click()
            self.assertEqual(asked.titles, ["Update these mods?"])
            self.assertIsNone(dialog._task)

    # -- the rows ------------------------------------------------------------
    def test_the_rows_stay_put_and_what_wants_doing_first_is_drawn_first(self):
        dialog = self._filled()
        self.assertEqual(self._drawn(dialog), ["Zed", "Mid", "Gone", "Alpha", "None"])
        dialog.sorting.sort(1)
        self.assertEqual(self._drawn(dialog), ["Alpha", "Gone", "Mid", "None", "Zed"])

    def test_the_selection_stays_on_its_mod_when_its_row_is_drawn_again(self):
        """An update redraws its row; the selection was a row number, and
        was on another mod once the rows were ranked again."""
        dialog = self._filled()
        dialog.table.selectRow(dialog._row_of["Zed"])
        zed = dialog._selected_verdict()
        self.assertEqual(zed.name, "Zed")
        zed.status = updates.CURRENT
        dialog._show(dialog._row_of["Zed"], zed)
        self.assertIs(dialog._selected_verdict(), zed)
        self.assertEqual(len(dialog.table.selectionModel().selectedRows()), 1)

    def test_a_sort_chosen_before_the_check_is_kept(self):
        dialog = self._dialog()
        dialog.sorting.sort(1)
        dialog._on_checked([updates.Verdict("Zed", "Zed", updates.UPDATE, modio_id=1)])
        self.assertEqual(self._drawn(dialog), sorted(self.NAMES))


if __name__ == "__main__":
    unittest.main()
