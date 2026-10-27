"""Collections in the get dialog, the plan dialog they open, and the offer a
loadout makes when mods it was built on are not installed -- driven offscreen
against a fake mod.io.
"""

from __future__ import annotations

import os
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

import _isolation  # noqa: F401 -- first: keeps the suite out of real files under any runner

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from PySide6.QtCore import QCoreApplication, QEvent, Qt  # noqa: E402
from PySide6.QtWidgets import QApplication, QDialog  # noqa: E402
from support import (  # noqa: E402
    FakeServer,
    downloadable_mod,
    install_mod,
    modio_collection,
    reverse_bytes,
    zip_bytes,
)

from anno_modding_studio.core import (  # noqa: E402
    disk,
    installplan,
    library,
    modio,
    modio_cache,
    places,
    secrets,
)
from anno_modding_studio.core.changes import Loadout, ModLayer  # noqa: E402
from anno_modding_studio.core.games import ANNO_1800  # noqa: E402
from anno_modding_studio.ui.install_plan_dialog import InstallPlanDialog  # noqa: E402
from anno_modding_studio.ui.main_window import MainWindow  # noqa: E402
from anno_modding_studio.ui.modio_get_dialog import (  # noqa: E402
    COLLECTIONS,
    MODS,
    ModioGetDialog,
)

_app = QApplication.instance() or QApplication([])


def _settle(dialog) -> None:
    """Let every worker the dialog starts run to the end and its signals land."""
    for _ in range(10):
        worker = dialog._worker
        if worker is not None:
            worker.wait(10000)
        for _ in range(30):
            _app.processEvents()
        again = dialog._worker
        if again is None or (again is worker and not again.isRunning()):
            return


class _Setup(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.mods = self.root / "mods"
        self.mods.mkdir()
        self.store = secrets.Store(self.root / "c.bin", wrap=reverse_bytes, unwrap=reverse_bytes)
        self.store.save(secrets.Credentials(api_key="k"))
        for patched in (
            mock.patch.dict(os.environ, {modio_cache.ENV_ROOT: str(self.root / "modio")}),
            mock.patch.object(places, "backups_dir", return_value=self.root / "backups"),
            mock.patch.object(installplan.switches, "documents_mods", return_value=None),
        ):
            patched.start()
            self.addCleanup(patched.stop)
        self.bodies = {}
        self.fetched = []

    def mod(self, mod_id: int, name: str) -> dict:
        body = zip_bytes({"%s/modinfo.json" % name: '{"Version": "2.0"}'})
        self.bodies["https://cdn/%d" % mod_id] = body
        return downloadable_mod(mod_id, name, "2.0", body)

    def chunks(self, url: str):
        self.fetched.append(url)
        body = self.bodies[url]
        for start in range(0, len(body), 64):
            yield body[start:start + 64]

    def server(self) -> FakeServer:
        return FakeServer(
            [self.mod(1, "One"), self.mod(2, "Two"), self.mod(3, "Three")],
            collections=[
                modio_collection(39, "Jakob's Collection", [1, 2, 3]),
                modio_collection(40, "Broken Set", [1], incomplete=True,
                            stats={"mods_total": 3, "incomplete": 2}),
            ],
        )


class TestThePlanDialog(_Setup):
    def dialog(self, server, collection_id: int = 39) -> InstallPlanDialog:
        collection = modio.summarise_collection(server.collections[collection_id])
        dialog = InstallPlanDialog(
            ANNO_1800, self.store, self.mods,
            installplan.collection_source(4169, collection),
            title=collection.name, intro="", page=collection.url,
            fetch=server, chunks=self.chunks, library_dir=library.game_dir(ANNO_1800),
        )
        self.addCleanup(dialog.deleteLater)
        _settle(dialog)
        return dialog

    def test_it_says_what_is_here_and_what_fetching_the_rest_costs(self):
        install_mod(self.mods, "[Gameplay] Two", "two", "2.0", "Two")
        dialog = self.dialog(self.server())
        self.assertEqual(dialog.table.rowCount(), 3)
        self.assertEqual([row.status for row in dialog.plan.rows],
                         [installplan.MISSING, installplan.CURRENT, installplan.MISSING])
        # What is here has no box to tick; what is not is ticked already.
        self.assertFalse(dialog.table.item(1, 0).flags() & Qt.ItemFlag.ItemIsUserCheckable)
        self.assertEqual(dialog.table.item(0, 0).checkState(), Qt.CheckState.Checked)
        self.assertEqual(dialog.fetch_button.text(), "&Install 2 mods")
        self.assertTrue(dialog.fetch_button.isEnabled())
        self.assertIn("1 of these 3 mods is already installed", dialog.summary.text())

    def test_the_here_column_is_drawn_in_the_scheme_in_use(self):
        from anno_modding_studio.ui import theme

        install_mod(self.mods, "[Gameplay] Two", "two", "2.0", "Two")
        before = theme.scheme()
        try:
            for key in ("marble", "brick"):
                theme.apply(_app, theme.SCHEMES[key])
                dialog = self.dialog(self.server())
                here = [dialog.table.item(row, 4).foreground().color().name() for row in range(3)]
                self.assertEqual(here, [theme.TEXT, theme.TEXT_DIM, theme.TEXT], key)
        finally:
            theme.apply(_app, before)

    def test_unticking_a_row_takes_it_out_of_the_fetch(self):
        dialog = self.dialog(self.server())
        dialog.table.item(0, 0).setCheckState(Qt.CheckState.Unchecked)
        self.assertEqual(dialog.fetch_button.text(), "&Install 2 mods")
        self.assertEqual([row.name for row in dialog.plan.chosen()], ["Two", "Three"])

    def test_fetching_installs_each_and_says_so_on_its_row(self):
        server = self.server()
        dialog = self.dialog(server)
        asked = len(server.calls)
        dialog._fetch_chosen()
        _settle(dialog)
        self.assertEqual(sorted(p.name for p in self.mods.iterdir()), ["One", "Three", "Two"])
        self.assertEqual(sorted(p.name for p in dialog.placed), ["One", "Three", "Two"])
        self.assertEqual(dialog.table.item(0, 4).text(), "installed just now")
        self.assertIn("3 of 3 installed", dialog.summary.text())
        # Nothing left to fetch, and mod.io was not asked again for any of it.
        self.assertFalse(dialog.fetch_button.isEnabled())
        self.assertEqual(len(server.calls), asked)
        dialog._close()
        self.assertEqual(dialog.result(), int(QDialog.DialogCode.Accepted))

    def test_one_mod_io_gave_no_checksum_for_is_installed_and_said_unchecked(self):
        server = self.server()
        del server.mods[2]["modfile"]["filehash"]
        dialog = self.dialog(server)
        dialog._fetch_chosen()
        _settle(dialog)
        self.assertEqual(sorted(p.name for p in self.mods.iterdir()), ["One", "Three", "Two"])
        self.assertEqual([dialog.table.item(row, 4).text() for row in range(3)],
                         ["installed just now", "installed just now, unchecked", "installed just now"])
        self.assertIn("mod.io gave no checksum for Two's file, so the download couldn't be checked.",
                      dialog.summary.text())

    def test_what_it_brought_is_told_from_what_was_here_already(self):
        """Updating a mod you had does not make it the collection's: switching
        the collection off must leave it be."""
        install_mod(self.mods, "[Gameplay] Two", "two", "1.0", "Two")
        dialog = self.dialog(self.server())
        dialog._fetch_chosen()
        _settle(dialog)
        dialog._close()
        self.assertEqual(sorted(dialog.brought), ["One", "Three"])
        self.assertEqual(len(dialog.present), 3)

    def test_one_that_fails_stays_ticked_to_be_tried_again(self):
        server = self.server()
        server.mods[2]["modfile"]["filehash"]["md5"] = "0" * 32
        dialog = self.dialog(server)
        dialog._fetch_chosen()
        _settle(dialog)
        self.assertEqual(sorted(p.name for p in self.mods.iterdir()), ["One", "Three"])
        self.assertIn("could not be installed", dialog.table.item(1, 4).text())
        self.assertEqual(dialog.fetch_button.text(), "&Install 1 mod")
        self.assertIn("2 of 3 installed", dialog.summary.text())

    def test_a_disk_without_room_is_said_before_anything_is_fetched(self):
        with mock.patch.object(disk, "free_space", return_value=1):
            dialog = self.dialog(self.server())
            self.assertFalse(dialog.fetch_button.isEnabled())
            self.assertIn("needs about", dialog.warnings.text())

    def test_an_incomplete_collection_is_said_to_be_one(self):
        dialog = self.dialog(self.server(), 40)
        self.assertIn("incomplete: 2 of its mods are no longer there", dialog.warnings.text())

    def test_without_a_key_it_asks_for_one_and_nothing_else(self):
        self.store.save(secrets.Credentials())
        server = self.server()
        dialog = self.dialog(server)
        self.assertEqual(server.calls, [])
        self.assertFalse(dialog.account_button.isHidden())
        self.assertFalse(dialog.fetch_button.isEnabled())

    def test_closing_with_nothing_fetched_is_a_cancel(self):
        dialog = self.dialog(self.server())
        dialog._close()
        self.assertEqual(dialog.result(), int(QDialog.DialogCode.Rejected))

    def test_closing_while_mod_io_is_slow_to_answer_leaves_the_process_alive(self):
        """A11-146: the wait on the way out ended with the request still in
        flight, and the dialog's deleteLater deleted its running thread --
        which ends the process."""
        server = self.server()
        reached, held = threading.Event(), threading.Event()
        self.addCleanup(held.set)

        def stalled(url, headers):
            reached.set()
            held.wait(20)
            return server(url, headers)

        collection = modio.summarise_collection(server.collections[39])
        dialog = InstallPlanDialog(
            ANNO_1800, self.store, self.mods,
            installplan.collection_source(4169, collection),
            title=collection.name, intro="", page=collection.url,
            fetch=stalled, chunks=self.chunks, library_dir=library.game_dir(ANNO_1800),
        )
        dialog.halt_wait = 50
        deadline = time.monotonic() + 20
        while not reached.is_set() and time.monotonic() < deadline:
            _app.processEvents()
        worker = dialog._worker
        dialog._close()
        self.assertTrue(worker.isRunning())
        self.assertIsNot(worker.parent(), dialog)
        dialog.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete.value)
        gone = []
        worker.destroyed.connect(lambda: gone.append(True))
        held.set()
        deadline = time.monotonic() + 20
        while not gone and time.monotonic() < deadline:
            _app.processEvents()
            QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete.value)
        self.assertEqual(gone, [True])
        self.assertEqual(self.fetched, [])


class TestCollectionsInTheGetDialog(_Setup):
    def dialog(self, server) -> ModioGetDialog:
        dialog = ModioGetDialog(ANNO_1800, self.store, self.mods, fetch=server, chunks=self.chunks, library_dir=library.game_dir(ANNO_1800))
        self.addCleanup(dialog.deleteLater)
        return dialog

    def test_the_switch_searches_collections_instead(self):
        dialog = self.dialog(self.server())
        dialog.kind.setCurrentText(COLLECTIONS)
        self.assertEqual(dialog.get_button.text(), "See what it installs…")
        dialog.query.setText("jakob")
        dialog._search()
        _settle(dialog)
        self.assertEqual([c.name for c in dialog.collections], ["Jakob's Collection"])
        self.assertEqual(dialog.table.horizontalHeaderItem(0).text(), "Collection")
        self.assertEqual(dialog.table.item(0, 3).text(), "3")
        self.assertTrue(dialog.get_button.isEnabled())
        # And back again empties the table: the rows were not mods.
        dialog.kind.setCurrentText(MODS)
        self.assertEqual(dialog.table.rowCount(), 0)
        self.assertFalse(dialog.get_button.isEnabled())

    def test_a_pasted_collection_address_says_which_kind_it_is(self):
        dialog = self.dialog(self.server())
        dialog.query.setText("https://mod.io/g/anno-1800/c/jakobs-collection")
        dialog._search()
        _settle(dialog)
        self.assertEqual(dialog.kind.currentText(), COLLECTIONS)
        self.assertEqual([c.id for c in dialog.collections], [39])

    def test_picking_one_opens_its_plan_and_what_that_installs_comes_back(self):
        dialog = self.dialog(self.server())
        dialog.kind.setCurrentText(COLLECTIONS)
        dialog.query.setText("jakob")
        dialog._search()
        _settle(dialog)

        def run(plan):
            _settle(plan)
            plan._fetch_chosen()
            _settle(plan)
            return int(QDialog.DialogCode.Accepted)

        with mock.patch.object(InstallPlanDialog, "exec", run):
            dialog._download()
        self.assertEqual(dialog.result(), int(QDialog.DialogCode.Accepted))
        self.assertEqual(dialog.collection.id, 39)
        self.assertIsNone(dialog.installed)
        self.assertEqual(sorted(p.name for p in dialog.placed), ["One", "Three", "Two"])

    def test_a_plan_closed_with_nothing_fetched_leaves_the_search_open(self):
        dialog = self.dialog(self.server())
        dialog.kind.setCurrentText(COLLECTIONS)
        dialog.query.setText("jakob")
        dialog._search()
        _settle(dialog)
        with mock.patch.object(InstallPlanDialog, "exec", lambda plan: _settle(plan)):
            dialog._download()
        self.assertIsNone(dialog.collection)
        self.assertNotEqual(dialog.result(), int(QDialog.DialogCode.Accepted))


class TestOpeningALoadoutBuiltOnOtherMods(_Setup):
    def setUp(self):
        super().setUp()
        self.window = MainWindow()
        self.addCleanup(_isolation.forget_settings)
        self.addCleanup(self.window.close)
        self.addCleanup(self.window.editor.search._debounce.stop)
        patched = mock.patch.object(self.window.where, "mods_folder", return_value=str(self.mods))
        patched.start()
        self.addCleanup(patched.stop)

    def saved(self, *layers) -> Path:
        loadout = Loadout()
        loadout.layers = list(layers)
        path = self.root / "theirs.annoload"
        loadout.save(path)
        return path

    def test_mods_that_are_not_here_are_offered_and_mod_io_is_not_asked(self):
        install_mod(self.mods, "[Gameplay] Here", "here", "1")
        path = self.saved(
            ModLayer(origin="[Gameplay] Here", name="Here", mod_id="here", folder="C:/theirs/mods/[Gameplay] Here"),
            ModLayer(origin="[Gameplay] Gone", name="Gone", mod_id="gone", folder="C:/theirs/mods/[Gameplay] Gone"),
        )
        asked = []
        with mock.patch.object(self.window.prompts, "ask_missing_mods",
                               side_effect=lambda wanted, absent: asked.append((wanted, absent)) or False), \
                mock.patch.object(self.window.modio, "fetch_missing_mods") as fetch:
            self.assertTrue(self.window.load_loadout(path))
        (wanted, absent), = asked
        self.assertEqual((sorted(one.name for one in wanted), [one.name for one in absent]),
                         (["Gone", "Here"], ["Gone"]))
        # Declined: the loadout is open as it is, and nothing else happened.
        fetch.assert_not_called()
        self.assertEqual(len(self.window.loadout.layers), 2)

    def test_saying_yes_opens_the_plan_for_the_folder_that_was_looked_in(self):
        path = self.saved(ModLayer(origin="[Gameplay] Gone", name="Gone", folder="C:/theirs/x"))
        with mock.patch.object(self.window.prompts, "ask_missing_mods", return_value=True), \
                mock.patch.object(self.window.modio, "fetch_missing_mods", return_value=True) as fetch:
            self.window.load_loadout(path)
        fetch.assert_called_once_with(str(self.mods))

    def test_a_loadout_opened_where_it_was_made_is_not_looked_into_at_all(self):
        here = install_mod(self.mods, "[Gameplay] Here", "here", "1")
        path = self.saved(ModLayer(origin="[Gameplay] Here", name="Here", folder=str(here)))
        with mock.patch.object(installplan, "inventory") as inventory, \
                mock.patch.object(self.window.prompts, "ask_missing_mods") as ask:
            self.window.load_loadout(path)
        inventory.assert_not_called()
        ask.assert_not_called()

    def test_a_mod_installed_under_another_name_is_not_missing(self):
        install_mod(self.mods, "[Seal] Renamed", "here", "1")
        path = self.saved(ModLayer(origin="[Gameplay] Here", name="Here", mod_id="here", folder="C:/theirs/x"))
        with mock.patch.object(self.window.prompts, "ask_missing_mods") as ask:
            self.window.load_loadout(path)
        ask.assert_not_called()

    def test_what_the_plan_learned_is_kept_in_the_loadout(self):
        path = self.saved(ModLayer(origin="[Gameplay] Gone", name="Gone", folder="C:/theirs/x"))
        with mock.patch.object(self.window.prompts, "ask_missing_mods", return_value=False):
            self.window.load_loadout(path)

        def run(plan):
            plan.learned["[Gameplay] Gone"] = 77
            return int(QDialog.DialogCode.Rejected)

        with mock.patch.object(InstallPlanDialog, "exec", run), \
                mock.patch.object(InstallPlanDialog, "_begin"):
            self.assertFalse(self.window.modio.fetch_missing_mods(str(self.mods)))
        self.assertEqual(self.window.loadout.layer("[Gameplay] Gone").modio_id, 77)
        # Which is a change to the loadout, and is treated as one.
        self.assertTrue(self.window.dirty)
        self.window.files.mark_saved()

    def test_and_in_the_library_for_a_copy_it_holds(self):
        """The library outlives the loadout, and is what is asked whether a
        copy could be had again."""
        from anno_modding_studio.core import library

        library_dir = self.window.where.library_dir()
        linked = library.adopt(library_dir, install_mod(self.mods, "[Gameplay] Held", "held", "1"))
        path = self.saved(
            ModLayer(origin="[Gameplay] Held", name="Held", mod_id="held", folder=str(linked.link)),
            ModLayer(origin="[Gameplay] Gone", name="Gone", folder="C:/theirs/x"),
        )
        with mock.patch.object(self.window.prompts, "ask_missing_mods", return_value=False):
            self.window.load_loadout(path)

        def run(plan):
            plan.learned["[Gameplay] Held"] = 55
            plan.learned["[Gameplay] Gone"] = 77
            return int(QDialog.DialogCode.Rejected)

        with mock.patch.object(InstallPlanDialog, "exec", run), \
                mock.patch.object(InstallPlanDialog, "_begin"):
            self.window.modio.fetch_missing_mods(str(self.mods))
        self.window.files.mark_saved()
        # The suite's library is one for every test: only this one's entry.
        known = library.registry(library_dir)
        self.addCleanup(library.remove_copy, library_dir, linked.link)
        self.assertEqual(known[linked.target.name], {
            "source": library.ADOPTED, "version": "1", "modio_id": 55, library.KNOWN_BY: "name",
        })
        self.assertNotIn("[Gameplay] Gone", known)
        # And made sure of by its file, it is that file of that mod, and no
        # longer only a match.
        from anno_modding_studio.core import updates

        self.window.modio._tell_the_library(
            {"[Gameplay] Held": 55}, {"[Gameplay] Held": updates.HASH}, {"[Gameplay] Held": 550},
        )
        self.assertEqual(library.registry(library_dir)[linked.target.name], {
            "source": library.ADOPTED, "version": "1", "modio_id": 55, "modfile_id": 550,
        })


if __name__ == "__main__":
    unittest.main()
