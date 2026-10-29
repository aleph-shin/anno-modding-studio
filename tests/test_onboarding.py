"""The first-run experience: someone who has never opened the Studio before.

The premise the rest of the app relies on is that a new user should never have
to know where their game is installed, where assets.xml lives inside a .rda
archive, or where mods go -- the Studio works all of that out.  These tests
simulate that machine state (via a synthetic archive standing in for a real
game install, and monkeypatched discovery for "nothing detected" and "one clear
install") since a real Anno installation is not something CI or a fresh clone
can assume.
"""

from __future__ import annotations

import os
import shutil
import struct
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import _isolation  # first: keeps the suite out of real files under any runner
from PySide6.QtCore import QSettings

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

# Keep the suite out of the developer's own saved settings -- and, just as
# importantly, out of their real ones' way. MainWindow reads remembered paths
# (the last localisation texts, for one) and acts on them at startup, so a
# machine that has used the app would otherwise drag those files into tests.
_SETTINGS_DIR = tempfile.mkdtemp(prefix="ams-settings-")
QSettings.setDefaultFormat(QSettings.Format.IniFormat)
QSettings.setPath(
    QSettings.Format.IniFormat, QSettings.Scope.UserScope, _SETTINGS_DIR
)

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import (  # noqa: E402
    QApplication,
    QInputDialog,
    QMenu,
    QMessageBox,
)
from support import no_credentials  # noqa: E402

from anno_modding_studio import __version__  # noqa: E402
from anno_modding_studio.core import discovery, gamefiles, places, rda  # noqa: E402
from anno_modding_studio.ui.import_dialog import ImportDialog  # noqa: E402
from anno_modding_studio.ui.main_window import (  # noqa: E402
    TAB_EDIT,
    TAB_GENERATE,
    TAB_REFS,
    TAB_SOURCE,
    MainWindow,
)

_app = QApplication.instance() or QApplication([])

ASSET_RELPATH = "data/config/export/main/asset/assets.xml"


def _stub_store(credentials):
    """A store that answers with these credentials and writes nowhere."""

    class Stub:
        def load(self):
            return credentials

    return Stub()


def _build_archive(path: Path, files) -> Path:
    """A minimal, real V2.2 archive holding ``files`` uncompressed in one block.

    Trimmed down from the RDA-format tests: just enough for `gamefiles.scan_game_files`
    to find a plausible ``assets.xml`` inside it.
    """
    out = bytearray(b"\x00" * rda.HEADER_SIZE)
    out[: len(rda.MAGIC_V22)] = rda.MAGIC_V22

    entries = bytearray()
    for name, payload in files:
        offset = len(out)
        out += payload
        name_bytes = name.encode("utf-16-le")
        name_bytes += b"\x00" * (rda.NAME_BYTES - len(name_bytes))
        entries += name_bytes
        entries += struct.pack("<QQQQQ", offset, len(payload), len(payload), 0, 0)

    # The directory sits immediately *before* the block header that points at
    # it -- the reader looks back from the header's own offset, not forward
    # from the directory.
    out += bytes(entries)
    block_offset = len(out)
    out += b"\x00" * rda.BLOCK_HEADER_SIZE
    struct.pack_into("<II", out, block_offset, 0, len(files))
    struct.pack_into(
        "<QQQ", out, block_offset + 8, len(entries), len(entries), len(out)
    )
    struct.pack_into("<Q", out, rda.FIRST_BLOCK_OFFSET_POS, block_offset)

    path.write_bytes(bytes(out))
    return path


class TestNoInstallDetected(unittest.TestCase):
    """A machine where the Studio cannot find Anno at all.

    Not "no Anno installed" necessarily -- a store other than Steam or Ubisoft
    Connect (Xbox/Game Pass, GOG, a portable copy) looks identical to this from
    the app's point of view, so this case has to degrade gracefully rather than
    look broken.
    """

    def setUp(self):
        patcher = mock.patch.object(discovery, "find_game_installs", return_value=[])
        self.addCleanup(patcher.stop)
        patcher.start()

    def test_the_app_opens_on_the_game_files_step_with_nothing_loaded(self):
        window = MainWindow()
        self.addCleanup(_isolation.forget_settings)
        try:
            self.assertEqual(window.tabs.currentIndex(), TAB_SOURCE)
            self.assertIn("No game files loaded", window.source.assets_label.full_text())
            self.assertFalse(
                any(way.button.isEnabled() for way in window.source.ways.values())
            )
            # The one action available has to be visible without hunting for it.
            self.assertTrue(window.source.import_button.isEnabled())
        finally:
            window.deleteLater()

    def test_the_import_dialog_explains_the_dead_end_and_offers_browse(self):
        dialog = ImportDialog()
        try:
            self.assertFalse(dialog.install_combo.isEnabled())
            self.assertIn("No Anno installation was detected", dialog.status.text())
            # The escape hatch: point the Studio at the folder directly.
            self.assertEqual(dialog.folder_edit.text(), "")
        finally:
            dialog.reject()
            dialog.deleteLater()

    def test_no_scan_is_attempted_with_nothing_to_scan(self):
        """Nothing to autodetect means nothing should spin up on its own."""
        dialog = ImportDialog()
        try:
            self.assertIsNone(dialog._scan_worker)
        finally:
            dialog.reject()
            dialog.deleteLater()


class TestExactlyOneInstallDetected(unittest.TestCase):
    """The common case: Anno lives in the one Steam library the Studio found.

    This is the scenario the onboarding experience is built around, so it gets
    the strongest guarantee -- opening the dialog should require no decision at
    all before "Extract and load" is ready to press.
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        root = Path(self._tmp.name)
        self.game_dir = root / "Anno 1800"
        maindata = self.game_dir / "maindata"
        maindata.mkdir(parents=True)
        _build_archive(
            maindata / "data33.rda",
            [
                (ASSET_RELPATH, b"<AssetList><Assets></Assets></AssetList>"),
                ("data/config/gui/texts_english.xml", b"<TextExport></TextExport>"),
            ],
        )

        install = discovery.GameInstall(
            name="Anno 1800",
            path=self.game_dir,
            archive_dir=maindata,
            archive_count=1,
        )
        patcher = mock.patch.object(
            discovery, "find_game_installs", return_value=[install]
        )
        self.addCleanup(patcher.stop)
        patcher.start()

    def _wait_for_scan(self, dialog, timeout_ms=5000) -> None:
        worker = dialog._scan_worker
        self.assertIsNotNone(worker, "the scan should have started on its own")
        self.assertTrue(worker.wait(timeout_ms), "scan did not finish in time")
        _app.processEvents()

    def test_the_folder_is_filled_in_and_scanning_starts_unasked(self):
        dialog = ImportDialog()
        try:
            self.assertEqual(dialog.folder_edit.text(), str(self.game_dir))
            self._wait_for_scan(dialog)
            self.assertTrue(dialog.extract_button.isEnabled())
            self.assertIsNotNone(dialog.result_scan)
            self.assertIsNotNone(dialog.result_scan.best_assets)
        finally:
            dialog.reject()
            dialog.deleteLater()

    def test_the_findings_table_shows_every_row_without_scrolling(self):
        """Two findings (the asset file and a language of texts) both fit --
        a fixed-height box previously clipped the second one behind a scrollbar."""
        dialog = ImportDialog()
        try:
            self._wait_for_scan(dialog)
            self.assertEqual(dialog.table.rowCount(), 2)
            self.assertEqual(
                dialog.table.verticalScrollBarPolicy(), Qt.ScrollBarPolicy.ScrollBarAlwaysOff
            )
            header = dialog.table.horizontalHeader().height()
            frame = 2 * dialog.table.frameWidth()
            rows = sum(
                dialog.table.rowHeight(r) for r in range(dialog.table.rowCount())
            )
            self.assertEqual(dialog.table.height(), header + frame + rows)
        finally:
            dialog.reject()
            dialog.deleteLater()

    def test_a_returning_users_last_folder_overrides_autodetection(self):
        """A previous explicit choice must not be second-guessed by a new scan
        turning up a different (or the same) install."""
        other = Path(self._tmp.name) / "somewhere else entirely"
        dialog = ImportDialog(last_folder=str(other))
        try:
            self.assertEqual(dialog.folder_edit.text(), str(other))
            self.assertIsNone(dialog._scan_worker, "must not auto-scan over a saved choice")
        finally:
            dialog.reject()
            dialog.deleteLater()

    def test_extraction_needs_only_one_more_click(self):
        """From a cold open to a loaded, editable asset file: fill in, scan,
        and the only remaining step is the explicit "Extract and load"."""
        dialog = ImportDialog()
        try:
            self._wait_for_scan(dialog)
            with tempfile.TemporaryDirectory() as dest:
                dialog.dest_edit.setText(dest)
                # The button, not the slot: a test that called _extract()
                # passed with the click connected to nothing (A11-156).
                dialog.extract_button.click()
                worker = dialog._extract_worker
                self.assertIsNotNone(worker)
                self.assertTrue(worker.wait(5000), "extraction did not finish in time")
                _app.processEvents()
                self.assertTrue(dialog.assets_path.is_file())
                # The mods folder and game folder are derived, never asked for.
                self.assertEqual(dialog.game_folder, self.game_dir)
        finally:
            dialog.reject()
            dialog.deleteLater()

    def test_cancelling_during_extraction_stops_it(self):
        """The extraction had no way to be stopped: Cancel waited three
        seconds for it and closed, and it went on writing files, then
        accepted the dialog that had been cancelled."""
        import threading

        dialog = ImportDialog()
        self.addCleanup(dialog.deleteLater)
        self._wait_for_scan(dialog)
        extracted = []
        started, release = threading.Event(), threading.Event()
        real = gamefiles.extract

        def slow(found, destination, progress=None):
            extracted.append(found.filename)
            started.set()
            release.wait(5)
            return real(found, destination, progress)

        with tempfile.TemporaryDirectory() as dest, \
                mock.patch.object(gamefiles, "extract", slow):
            dialog.dest_edit.setText(dest)
            dialog.extract_button.click()
            worker = dialog._extract_worker
            self.assertTrue(started.wait(5), "extraction did not start")
            # Enter in the folder box scans, but not under an extraction.
            scanned = dialog._scan_worker
            dialog.folder_edit.returnPressed.emit()
            self.assertIs(dialog._scan_worker, scanned)
            # The file being written is finished; the next is not begun.
            threading.Timer(0.2, release.set).start()
            dialog.reject()
            self.assertTrue(worker.wait(5000), "extraction did not stop")
            _app.processEvents()
            self.assertEqual(extracted, ["assets.xml"])
            # And an answer that comes in after all is not taken.
            worker.succeeded.emit(Path(dest) / "assets.xml", None, {})
            _app.processEvents()
        self.assertEqual(dialog.result(), int(dialog.DialogCode.Rejected))
        self.assertIsNone(dialog.assets_path)


ANNO_117_ASSET_RELPATH = "data/base/config/export/assets.xml"


class TestSwitchingGamesUpdatesTheDestination(unittest.TestCase):
    """Scanning a different game than last time must relabel "Extract to".

    A field left at Anno 1800's suggested folder is still just a suggestion
    once the user points the dialog at Anno 117 instead -- left unchanged, it
    would name the wrong game, which is confusing both in the dialog and later
    if someone goes looking through the extracted files by hand.
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        root = Path(self._tmp.name)

        self.anno1800 = root / "Anno 1800"
        (self.anno1800 / "maindata").mkdir(parents=True)
        _build_archive(
            self.anno1800 / "maindata" / "data33.rda",
            [(ASSET_RELPATH, b"<AssetList><Assets></Assets></AssetList>")],
        )

        self.anno117 = root / "Anno 117 - Pax Romana"
        (self.anno117 / "maindata").mkdir(parents=True)
        _build_archive(
            self.anno117 / "maindata" / "config.rda",
            [(ANNO_117_ASSET_RELPATH, b"<AssetList><Assets></Assets></AssetList>")],
        )

    def _scan(self, dialog, folder) -> None:
        dialog.folder_edit.setText(str(folder))
        dialog._scan()
        self.assertIsNotNone(dialog._scan_worker)
        self.assertTrue(dialog._scan_worker.wait(5000))
        _app.processEvents()

    def test_the_suggested_destination_follows_the_game_actually_scanned(self):
        from anno_modding_studio.core import gamefiles, games

        dialog = ImportDialog()
        try:
            self._scan(dialog, self.anno1800)
            self.assertEqual(
                dialog.dest_edit.text(),
                str(gamefiles.default_extract_root(games.ANNO_1800)),
            )

            self._scan(dialog, self.anno117)
            self.assertEqual(
                dialog.dest_edit.text(),
                str(gamefiles.default_extract_root(games.ANNO_117)),
                "the field must not still name the previous game",
            )
        finally:
            dialog.reject()
            dialog.deleteLater()

    def test_a_suggestion_from_before_the_default_moved_is_still_a_suggestion(self):
        """It was taken for a choice, so Anno 117 was read into Anno 1800's
        folder: the field said Documents, and the default no longer did."""
        from anno_modding_studio.core import gamefiles, games

        before = gamefiles.former_extract_root(games.ANNO_1800)
        with mock.patch.object(places, "extracted_dir", return_value=before.parent.parent / "now"):
            dialog = ImportDialog(last_destination=str(before))
            try:
                self._scan(dialog, self.anno117)
                self.assertEqual(
                    dialog.dest_edit.text(),
                    str(gamefiles.default_extract_root(games.ANNO_117)),
                )
            finally:
                dialog.reject()
                dialog.deleteLater()

    def test_a_folder_named_for_one_game_is_never_where_another_is_read_to(self):
        """In a place of the user's own, the other game goes beside it."""
        from anno_modding_studio.core import gamefiles, games

        self.assertEqual(
            gamefiles.suggested_root(r"D:\Extracts\Anno 1800", games.ANNO_117),
            str(Path(r"D:\Extracts") / gamefiles.extract_folder_name(games.ANNO_117)),
        )
        self.assertEqual(
            gamefiles.suggested_root(r"D:\Extracts\Anno 1800", games.ANNO_1800),
            r"D:\Extracts\Anno 1800",
        )
        self.assertEqual(gamefiles.suggested_root(r"D:\Extracts", games.ANNO_117), r"D:\Extracts")

    def test_a_hand_chosen_destination_survives_switching_games(self):
        dialog = ImportDialog()
        try:
            self._scan(dialog, self.anno1800)
            dialog.dest_edit.setText(r"C:\MyCustomExtracts")

            self._scan(dialog, self.anno117)
            self.assertEqual(
                dialog.dest_edit.text(),
                r"C:\MyCustomExtracts",
                "a deliberate choice must not be overwritten by a later scan",
            )
        finally:
            dialog.reject()
            dialog.deleteLater()


class TestMultipleInstallsDetected(unittest.TestCase):
    """Two or more candidates: a genuine choice, so autodetection stays hands-off."""

    def setUp(self):
        installs = [
            discovery.GameInstall("Anno 1800", Path("C:/games/Anno 1800"), None, 3),
            discovery.GameInstall("Anno 117", Path("C:/games/Anno 117"), None, 2),
        ]
        patcher = mock.patch.object(
            discovery, "find_game_installs", return_value=installs
        )
        self.addCleanup(patcher.stop)
        patcher.start()

    def test_the_user_is_left_to_pick_one(self):
        dialog = ImportDialog()
        try:
            self.assertEqual(dialog.folder_edit.text(), "")
            self.assertIsNone(dialog._scan_worker)
            # Both are offered; "Detected installs..." plus the two candidates.
            self.assertEqual(dialog.install_combo.count(), 3)
        finally:
            dialog.reject()
            dialog.deleteLater()

    def test_scan_waits_for_a_folder(self):
        """On a first start with both games installed, Scan archives was
        offered over an empty box, whose grey example path read as a folder
        filled in, and said "That folder does not exist." """
        dialog = ImportDialog()
        try:
            self.assertFalse(dialog.scan_button.isEnabled())
            told = dialog.status.text()
            dialog._folder_entered()
            self.assertEqual(dialog.status.text(), told)
            dialog.folder_edit.setText("C:/games/Anno 1800")
            self.assertTrue(dialog.scan_button.isEnabled())
            dialog.folder_edit.setText("  ")
            self.assertFalse(dialog.scan_button.isEnabled())
        finally:
            dialog.reject()
            dialog.deleteLater()


class TestBothGamesInstalled(unittest.TestCase):
    """Two installs the dialog can tell apart, each with archives to read.

    Which of them is a choice, so nothing is read before it is made.  It is
    made by picking one from the list, or by having opened the dialog from
    one game's own row on the Start tab.
    """

    def setUp(self):
        from anno_modding_studio.core import games

        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.anno1800 = self._install(
            "Anno 1800", games.ANNO_1800, "data33.rda", ASSET_RELPATH
        )
        self.anno117 = self._install(
            "Anno 117 - Pax Romana", games.ANNO_117, "config.rda", ANNO_117_ASSET_RELPATH
        )
        # In the list's order, which is by name: 117 is row 1 and 1800 row 2.
        installs = [
            discovery.GameInstall(folder.name, folder, folder / "maindata", 1)
            for folder in (self.anno117, self.anno1800)
        ]
        patcher = mock.patch.object(
            discovery, "find_game_installs", return_value=installs
        )
        self.addCleanup(patcher.stop)
        patcher.start()

    def _install(self, name, game, archive, relpath, under=""):
        """A game folder: the program it runs as, and one archive."""
        from anno_modding_studio.core import games

        folder = self.root / under / name
        (folder / "maindata").mkdir(parents=True)
        _build_archive(
            folder / "maindata" / archive,
            [(relpath, b"<AssetList><Assets></Assets></AssetList>")],
        )
        exe = folder / "Bin" / "Win64" / games.GAMES[game].exe
        exe.parent.mkdir(parents=True)
        exe.write_bytes(b"")
        return folder

    def _dialog(self, **how):
        dialog = ImportDialog(**how)
        self.addCleanup(dialog.deleteLater)
        self.addCleanup(dialog.reject)
        return dialog

    def _wait_for_scan(self, dialog) -> None:
        self.assertIsNotNone(dialog._scan_worker, "no scan was started")
        self.assertTrue(dialog._scan_worker.wait(5000), "scan did not finish in time")
        _app.processEvents()

    def test_opened_from_a_games_row_that_games_install_is_chosen_and_scanned(self):
        """It opened on "Detected installs…" whichever row was clicked, and
        the game just asked for had to be picked from the list again."""
        from anno_modding_studio.core import games

        for game, folder in ((games.ANNO_1800, self.anno1800), (games.ANNO_117, self.anno117)):
            with self.subTest(game=game):
                dialog = self._dialog(game=game)
                self.assertEqual(dialog.install_combo.currentData(), str(folder))
                self.assertEqual(dialog.folder_edit.text(), str(folder))
                self._wait_for_scan(dialog)
                self.assertEqual(dialog.result_scan.best_assets.game, game)
                self.assertTrue(dialog.extract_button.isEnabled())

    def test_the_folder_read_last_does_not_stand_in_for_the_game_asked_for(self):
        """With nothing remembered for a game, the folder read last is
        offered, and that is the other game's."""
        from anno_modding_studio.core import games

        dialog = self._dialog(last_folder=str(self.anno1800), game=games.ANNO_117)
        self.assertEqual(dialog.folder_edit.text(), str(self.anno117))
        self.assertEqual(dialog.install_combo.currentData(), str(self.anno117))

    def only_installed(self, folder: Path) -> None:
        installs = [discovery.GameInstall(folder.name, folder, folder / "maindata", 1)]
        patcher = mock.patch.object(discovery, "find_game_installs", return_value=installs)
        self.addCleanup(patcher.stop)
        patcher.start()

    def test_a_game_with_no_install_found_opens_on_that_game_and_reads_nothing(self):
        """With only the other game installed, its install was the one
        detected: the dialog opened on it and scanned it, as if that were
        the game whose row was clicked."""
        from anno_modding_studio.core import games

        self.only_installed(self.anno1800)
        for last in ("", str(self.anno1800)):
            with self.subTest(last_folder=last):
                dialog = self._dialog(game=games.ANNO_117, last_folder=last)
                self.assertEqual(dialog.folder_edit.text(), "")
                self.assertEqual(dialog.install_combo.currentIndex(), 0)
                self.assertIsNone(dialog._scan_worker)
                self.assertEqual(
                    dialog.status.text(),
                    "No installation of Anno 117: Pax Romana was found. Use Browse "
                    "to choose its folder, then scan its archives.",
                )
                self.assertFalse(dialog.extract_button.isEnabled())
                # The install that was found is still there to be picked.
                self.assertEqual(dialog.install_combo.itemData(1), str(self.anno1800))
                self.assertTrue(dialog.install_combo.isEnabled())

    def test_the_empty_folder_box_names_the_folder_of_the_game_it_opened_on(self):
        """Opened on Anno 117 with no install of it found, the empty box
        still showed where Anno 1800 is installed."""
        from anno_modding_studio.core import games

        self.only_installed(self.anno1800)
        dialog = self._dialog(game=games.ANNO_117)
        self.assertEqual(dialog.folder_edit.text(), "")
        self.assertEqual(
            dialog.folder_edit.placeholderText(),
            "…\\steamapps\\common\\Anno 117 - Pax Romana",
        )
        # No game asked for and none found: the one the Studio starts on.
        self.only_installed(self.root / "nothing here")
        self.assertEqual(
            self._dialog().folder_edit.placeholderText(), "…\\steamapps\\common\\Anno 1800"
        )

    def test_and_the_folder_of_the_game_read_once_another_one_is(self):
        from anno_modding_studio.core import games

        self.only_installed(self.anno1800)
        dialog = self._dialog(game=games.ANNO_117)
        dialog.install_combo.setCurrentIndex(1)
        dialog._install_chosen(1)
        self._wait_for_scan(dialog)
        self.assertEqual(dialog.result_scan.best_assets.game, games.ANNO_1800)
        self.assertEqual(
            dialog.folder_edit.placeholderText(), "…\\steamapps\\common\\Anno 1800"
        )
        dialog.folder_edit.setText(str(self.anno117))
        dialog._scan()
        self._wait_for_scan(dialog)
        self.assertEqual(
            dialog.folder_edit.placeholderText(),
            "…\\steamapps\\common\\Anno 117 - Pax Romana",
        )

    def test_and_a_folder_of_that_game_chosen_by_hand_before_is_where_it_opens(self):
        from anno_modding_studio.core import games

        self.only_installed(self.anno1800)
        dialog = self._dialog(game=games.ANNO_117, last_folder=str(self.anno117))
        self.assertEqual(dialog.folder_edit.text(), str(self.anno117))
        self.assertIsNone(dialog._scan_worker)
        self.assertNotIn("No installation", dialog.status.text())

    def test_the_one_install_found_is_still_opened_on_when_it_is_the_game_asked_for(self):
        from anno_modding_studio.core import games

        self.only_installed(self.anno1800)
        dialog = self._dialog(game=games.ANNO_1800, last_folder=str(self.anno117))
        self.assertEqual(dialog.folder_edit.text(), str(self.anno1800))
        self._wait_for_scan(dialog)

    def test_another_copy_of_that_game_read_last_is_still_where_it_opens(self):
        """A returning user's own choice of folder, for this game: kept, and
        not scanned over."""
        from anno_modding_studio.core import games

        other = self._install(
            "Anno 1800", games.ANNO_1800, "data33.rda", ASSET_RELPATH, under="a second copy"
        )
        dialog = self._dialog(last_folder=str(other), game=games.ANNO_1800)
        self.assertEqual(dialog.folder_edit.text(), str(other))
        self.assertEqual(dialog.install_combo.currentIndex(), 0)
        self.assertIsNone(dialog._scan_worker)

    def test_the_row_on_the_start_tab_says_which_game_it_is(self):
        from PySide6.QtWidgets import QPushButton

        from anno_modding_studio.core import games

        window = MainWindow()
        self.addCleanup(_isolation.forget_settings)
        self.addCleanup(window.deleteLater)
        window.start.refresh_games()
        rows = [
            button
            for button in window.source.games_box.findChildren(QPushButton)
            if button.text() == "Read from game files…"
        ]
        self.assertEqual(len(rows), len(games.GAMES))
        opened_on = []

        def look(dialog):
            opened_on.append(dialog.folder_edit.text())
            dialog.reject()
            return int(dialog.DialogCode.Rejected)

        with mock.patch.object(ImportDialog, "exec", look):
            for row in rows:
                row.click()
            # The card's own button asks for no game, and still chooses none.
            window.source.import_button.click()
        self.assertEqual(opened_on, [str(self.anno1800), str(self.anno117), ""])

    def test_picking_an_install_from_the_list_scans_it(self):
        """The folder went into the box and nothing else happened: "Scan
        archives" had still to be found and pressed."""
        from anno_modding_studio.core import games

        dialog = self._dialog()
        self.assertIsNone(dialog._scan_worker)
        dialog.install_combo.setCurrentIndex(2)
        dialog.install_combo.activated.emit(2)
        self.assertEqual(dialog.folder_edit.text(), str(self.anno1800))
        self._wait_for_scan(dialog)
        self.assertEqual(dialog.result_scan.best_assets.game, games.ANNO_1800)
        self.assertTrue(dialog.extract_button.isEnabled())

        # The line that is no install picks nothing, and reads nothing.
        scanned = dialog._scan_worker
        dialog.install_combo.setCurrentIndex(0)
        dialog.install_combo.activated.emit(0)
        self.assertIs(dialog._scan_worker, scanned)

    def test_extract_and_load_says_a_scan_comes_first_until_one_has_run(self):
        """Greyed, it looks much as it does when it can be pressed, and
        nothing said why pressing it did nothing."""
        dialog = self._dialog()
        self.assertFalse(dialog.extract_button.isEnabled())
        self.assertEqual(dialog.extract_button.toolTip(), "Scan the archives first.")
        self.assertEqual(
            dialog.status.text(),
            "Choose the game folder, then scan its archives. Extract and load "
            "takes the files the scan finds.",
        )

        dialog.install_combo.setCurrentIndex(1)
        dialog.install_combo.activated.emit(1)
        self._wait_for_scan(dialog)
        self.assertTrue(dialog.extract_button.isEnabled())
        self.assertEqual(dialog.extract_button.toolTip(), "")
        self.assertNotIn("then scan its archives", dialog.status.text())

    def test_a_scan_given_up_for_another_folder_is_not_taken_as_its_answer(self):
        """One install picked while another is being read: the first scan is
        stopped, and still reports what it had got to."""
        from anno_modding_studio.core import games
        from anno_modding_studio.ui.import_dialog import ScanWorker

        dialog = self._dialog()
        dialog.install_combo.setCurrentIndex(2)
        dialog.install_combo.activated.emit(2)
        first = dialog._scan_worker
        dialog.install_combo.setCurrentIndex(1)
        dialog.install_combo.activated.emit(1)
        self.assertIsNot(dialog._scan_worker, first)
        self.assertFalse(first.isRunning())
        self._wait_for_scan(dialog)
        self.assertEqual(dialog.result_scan.best_assets.game, games.ANNO_117)
        self.assertEqual(dialog.folder_edit.text(), str(self.anno117))

        # And one that reports late, after the answer wanted is in.
        answer = dialog.result_scan
        late = ScanWorker(self.anno1800, dialog)
        late.succeeded.connect(dialog._on_scan_done)
        late.failed.connect(dialog._on_failed)
        late.succeeded.emit(object())
        late.failed.emit("too late to matter")
        self.assertIs(dialog.result_scan, answer)
        self.assertNotEqual(dialog.status.text(), "too late to matter")

    def _extract(self, dialog) -> None:
        dialog.dest_edit.setText(str(self.root / "extracted"))
        dialog._extract()
        self.assertTrue(dialog._extract_worker.wait(5000), "extraction did not finish in time")
        _app.processEvents()
        self.assertIsNotNone(dialog.assets_path, "nothing was extracted")

    def test_the_game_folder_recorded_is_the_one_whose_files_were_extracted(self):
        """It was read off the folder box when extraction ended, and the box
        can name another install by then: the extracted files got stamped
        with a folder they did not come from, and the game launched there."""
        dialog = self._dialog()
        dialog.install_combo.setCurrentIndex(2)
        dialog.install_combo.activated.emit(2)
        self._wait_for_scan(dialog)
        # Another folder typed in, or browsed to, and never scanned.
        dialog.folder_edit.setText(str(self.anno117))
        self._extract(dialog)
        self.assertEqual(dialog.game_folder, self.anno1800)

    def test_enter_in_the_folder_box_scans_the_folder_it_names(self):
        """Enter there pressed the default button: before a scan nothing,
        and after one "Extract and load", of the folder scanned before
        whatever the box named by then."""
        from PySide6.QtTest import QTest

        from anno_modding_studio.core import games

        dialog = self._dialog()
        dialog.show()
        dialog.folder_edit.setFocus()
        dialog.folder_edit.setText(str(self.anno1800))
        QTest.keyClick(dialog.folder_edit, Qt.Key.Key_Return)
        self._wait_for_scan(dialog)
        self.assertEqual(dialog.result_scan.best_assets.game, games.ANNO_1800)

        dialog.folder_edit.setText(str(self.anno117))
        QTest.keyClick(dialog.folder_edit, Qt.Key.Key_Enter)
        self._wait_for_scan(dialog)
        self.assertEqual(dialog.result_scan.best_assets.game, games.ANNO_117)

        # A folder that is none is said so, and the last scan not extracted.
        dialog.folder_edit.setText(str(self.root / "nowhere"))
        QTest.keyClick(dialog.folder_edit, Qt.Key.Key_Return)
        self.assertEqual(dialog.status.text(), "That folder does not exist.")
        self.assertIsNone(dialog._extract_worker, "Enter extracted")

    def test_and_a_rescan_that_fails_leaves_it_the_folder_scanned_before(self):
        from anno_modding_studio.core import gamefiles

        dialog = self._dialog()
        dialog.install_combo.setCurrentIndex(2)
        dialog.install_combo.activated.emit(2)
        self._wait_for_scan(dialog)
        dialog.folder_edit.setText(str(self.anno117))
        with mock.patch.object(gamefiles, "scan_game_files", side_effect=OSError("unreadable")):
            dialog._scan()
            self._wait_for_scan(dialog)
        # The failure put the button back on, over the first scan's files.
        self.assertTrue(dialog.extract_button.isEnabled())
        self._extract(dialog)
        self.assertEqual(dialog.game_folder, self.anno1800)


class TestRememberedPlaces(unittest.TestCase):
    """Where the app keeps what it remembered, and what it does with a path
    that is no longer there.

    Both halves of one bug.  The suite thought it had redirected QSettings away
    from the developer's own -- ``setDefaultFormat`` looks like it does that and
    does not -- so a test run wrote its temporary mods folder in as the
    remembered one, and the app then offered a deleted temp directory as the
    place to look for the user's mods.
    """

    def setUp(self):
        self.window = MainWindow()
        self.addCleanup(_isolation.forget_settings)

    def tearDown(self):
        self.window.editor.search._debounce.stop()
        self.window.close()

    def test_the_settings_are_a_named_file_rather_than_the_platform_default(self):
        """Which is the only way anything can redirect them: on Windows the
        native format is the registry, and nothing can point that elsewhere."""
        from anno_modding_studio.ui.main_window import open_settings

        self.assertEqual(open_settings().format(), QSettings.Format.IniFormat)

    def test_a_folder_that_is_still_there_is_offered(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.window.settings.setValue("last_mods_folder", tmp)
            self.assertEqual(self.window.remembered("last_mods_folder"), tmp)

    def test_one_that_has_been_deleted_is_not(self):
        with tempfile.TemporaryDirectory() as tmp:
            gone = str(Path(tmp) / "not here")
        self.window.settings.setValue("last_mods_folder", gone)
        self.assertEqual(self.window.remembered("last_mods_folder"), "")

    def test_and_the_fallback_is_used_instead(self):
        self.window.settings.setValue("last_loadout_dir", r"C:\nowhere at all")
        self.assertEqual(
            self.window.remembered("last_loadout_dir", "fallback"), "fallback"
        )

    def test_a_remembered_file_is_checked_the_same_way(self):
        self.window.settings.setValue("last_texts_file", r"C:\nowhere\texts.xml")
        self.assertEqual(self.window.remembered_file("last_texts_file"), "")


def name_the_mod(case, answer=None, ok=True):
    """Answer the "Name your mod" prompt that starting that aim now asks.

    Returns the list it records the suggestions in, so a test can check what it
    was offered as well as what it did with the answer.
    """
    from PySide6.QtWidgets import QInputDialog

    asked = []
    real = QInputDialog.getText

    def named(parent, title, label, *args, **kwargs):
        asked.append(kwargs.get("text", ""))
        return (answer if answer is not None else kwargs.get("text", ""), ok)

    QInputDialog.getText = staticmethod(named)
    case.addCleanup(setattr, QInputDialog, "getText", staticmethod(real))
    return asked


class TestThreeWaysOfWorking(unittest.TestCase):
    """The question the first tab asks, and what each answer sets up.

    Three quite different jobs share this one window.  They want the workspace
    arranged in opposite ways -- writing a mod to publish wants nothing else
    loaded, running a mod list wants everything loaded -- and somebody who never
    gets asked finds out several steps later, when their mod exports with
    another author's changes folded into it.
    """

    @classmethod
    def setUpClass(cls):
        sys.path.insert(0, str(ROOT / "tools"))
        import make_sample_assets

        from anno_modding_studio.core import index as index_mod

        cls._tmp = tempfile.TemporaryDirectory()
        tmp = Path(cls._tmp.name)
        cls.assets = tmp / "assets.xml"
        cls.assets.write_text(make_sample_assets.build_assets_xml(20), encoding="utf-8")
        cls.db = tmp / "index.sqlite"
        index_mod.build_index(cls.assets, cls.db)

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def setUp(self):
        self.window = MainWindow()
        self.addCleanup(_isolation.forget_settings)
        self.named = name_the_mod(self)

    def tearDown(self):
        self.window.editor.search._debounce.stop()
        self.window.close()

    def ready(self):
        self.window.game_files.adopt_index(self.db, self.assets, cached=True)

    def cards(self):
        return self.window.source.ways

    # -- what the tab offers -------------------------------------------------
    def test_the_first_tab_names_all_three(self):
        from anno_modding_studio.core.changes import AIM_MAKE, AIM_MANAGE, AIM_TWEAK

        self.assertEqual(
            set(self.cards()), {AIM_MAKE, AIM_TWEAK, AIM_MANAGE}
        )

    def test_none_of_them_can_be_started_before_the_game_files_are_loaded(self):
        for card in self.cards().values():
            self.assertFalse(card.button.isEnabled())
            self.assertIn("Load the game files", card.status.text())

    def test_and_all_of_them_can_once_they_are(self):
        self.ready()
        for card in self.cards().values():
            self.assertTrue(card.button.isEnabled())

    def test_nothing_is_picked_until_you_pick_it(self):
        """It is a signpost, not a mode -- and guessing would be worse than
        asking, because the wrong guess is invisible until export."""
        self.ready()
        self.assertEqual(self.window.loadout.aim, "")
        self.assertFalse(any(c.property("current") for c in self.cards().values()))

    # -- choosing one --------------------------------------------------------
    def test_writing_a_mod_of_your_own_goes_straight_to_the_editor(self):
        from anno_modding_studio.core.changes import AIM_MAKE

        self.ready()
        self.assertFalse(self.window.loadout.has_own_mod())
        self.window.start.choose_aim(AIM_MAKE)
        self.assertEqual(self.window.loadout.aim, AIM_MAKE)
        # Starting a new mod is what makes one; nothing was there before.
        self.assertTrue(self.window.loadout.has_own_mod())
        self.assertEqual(self.window.tabs.currentIndex(), TAB_EDIT)
        self.assertTrue(self.cards()[AIM_MAKE].property("current"))

    def test_and_offers_a_loadout_of_its_own_when_something_is_already_open(self):
        """One mod per loadout: a mod that ships with somebody else's changes
        folded into it is not a mistake you notice before publishing."""


        from anno_modding_studio.core.changes import AIM_MAKE, ModLayer

        self.ready()
        self.window.loadout.set_layer(ModLayer(origin="Somebody Else"))
        with _isolation.answering("Start fresh") as asked:
            self.window.start.choose_aim(AIM_MAKE)
        self.assertIn("clean loadout", asked.texts[-1])
        self.assertEqual(self.window.loadout.layers, [])

    def test_and_leaves_everything_alone_if_you_say_no(self):


        from anno_modding_studio.core.changes import AIM_MAKE, ModLayer

        self.ready()
        self.window.loadout.set_layer(ModLayer(origin="Somebody Else"))
        with _isolation.answering(False):
            self.window.start.choose_aim(AIM_MAKE)
        self.assertEqual(self.window.loadout.aim, "")
        self.assertEqual(len(self.window.loadout.layers), 1)

    # -- while its questions are open -----------------------------------------
    def switch_to_tweak_and_cancel(self):
        """Already writing a mod; click the second card; look at the tab while
        its import dialog is open; close that dialog without importing."""
        from unittest import mock

        from PySide6.QtWidgets import QDialog

        from anno_modding_studio.core.changes import AIM_MAKE, AIM_TWEAK
        from anno_modding_studio.ui import mod_import_dialog as import_mod_ui

        self.ready()
        self.window.settings.setValue(self.window.preferences.CREATOR_KEY, "Me")
        self.addCleanup(self.window.settings.remove, self.window.preferences.CREATOR_KEY)
        self.window.start.choose_aim(AIM_MAKE)
        # Back on the first tab, which is where the second card gets clicked.
        self.window.tabs.setCurrentIndex(0)
        seen = {}

        def look(_dialog):
            seen.update(
                {key: (bool(card.property("current")), card.mark.text())
                 for key, card in self.cards().items()}
            )
            return int(QDialog.DialogCode.Rejected)

        with mock.patch.object(import_mod_ui.ModImportDialog, "exec", look):
            self.window.start.choose_aim(AIM_TWEAK)
        return seen

    def test_the_card_clicked_is_picked_out_while_its_questions_are_open(self):
        """The dialogs are most of the time anybody sees this tab with a card
        picked out -- and the old highlight read as the click not having taken."""
        from anno_modding_studio.core.changes import AIM_MAKE, AIM_TWEAK

        seen = self.switch_to_tweak_and_cancel()
        self.assertEqual(seen[AIM_TWEAK], (True, "setting up…"))
        self.assertEqual(seen[AIM_MAKE], (False, ""))

    def test_and_giving_up_on_it_puts_things_back(self):
        """Nothing was read in, so it is not the way you are working."""
        from anno_modding_studio.core.changes import AIM_MAKE, AIM_TWEAK

        self.switch_to_tweak_and_cancel()
        self.assertEqual(self.window.loadout.aim, AIM_MAKE)
        self.assertTrue(self.window.loadout.is_the_mod)
        self.assertTrue(self.cards()[AIM_MAKE].property("current"))
        self.assertEqual(self.cards()[AIM_MAKE].mark.text(), "set up here")
        self.assertFalse(self.cards()[AIM_TWEAK].property("current"))
        # And you are left where you were, rather than sent to the editor.
        self.assertEqual(self.window.tabs.currentIndex(), 0)

    def test_a_mod_list_that_never_loaded_is_not_the_way_in_use_either(self):
        from unittest import mock

        from anno_modding_studio.core.changes import AIM_MANAGE

        self.ready()
        with mock.patch.object(self.window, "load_installed_mods", return_value=False):
            self.window.start.choose_aim(AIM_MANAGE)
        self.assertEqual(self.window.loadout.aim, "")
        self.assertFalse(any(c.property("current") for c in self.cards().values()))


    def test_changing_a_downloaded_mod_opens_the_one_mod_reader(self):
        from unittest import mock

        from anno_modding_studio.core.changes import AIM_TWEAK

        self.ready()
        with mock.patch.object(MainWindow, "import_mod") as opened:
            self.window.start.choose_aim(AIM_TWEAK)
        self.assertTrue(opened.called)
        self.assertEqual(self.window.loadout.aim, AIM_TWEAK)

    def test_cancelling_who_you_are_cancels_changing_a_downloaded_mod(self):
        """The mod reader opened all the same, after the question cancelled."""
        from unittest import mock

        from anno_modding_studio.core.changes import AIM_TWEAK

        self.ready()
        self.window.settings.remove(self.window.preferences.CREATOR_KEY)
        with mock.patch.object(self.window.prompts, "ask_creator_name",
                               return_value=("Lars", False)) as asked, \
                mock.patch.object(MainWindow, "import_mod") as opened:
            self.window.start.choose_aim(AIM_TWEAK)
        asked.assert_called_once()
        opened.assert_not_called()
        self.assertNotEqual(self.window.loadout.aim, AIM_TWEAK)

    def test_and_writing_a_mod_of_your_own(self):
        from unittest import mock

        from anno_modding_studio.core.changes import AIM_MAKE

        self.ready()
        self.window.settings.remove(self.window.preferences.CREATOR_KEY)
        name = self.window.loadout.own.name
        with mock.patch.object(self.window.prompts, "ask_creator_name",
                               return_value=("Lars", False)) as asked:
            self.window.start.choose_aim(AIM_MAKE)
        asked.assert_called_once()
        self.assertNotEqual(self.window.loadout.aim, AIM_MAKE)
        self.assertEqual(self.window.loadout.own.name, name)
        self.assertIsNone(self.window.loadout_path)

    def test_and_asks_it_for_the_mod_itself_rather_than_its_changes(self):
        """A patch has to have a mod to stand in front of, so folding the mod
        into yours is the one shape this aim cannot use."""
        from unittest import mock

        from anno_modding_studio.core.changes import AIM_TWEAK

        self.ready()
        with mock.patch.object(MainWindow, "import_mod") as opened:
            self.window.start.choose_aim(AIM_TWEAK)
        self.assertEqual(opened.call_args.kwargs.get("separate"), True)

    def test_and_the_mod_that_arrives_gets_its_patch_made_for_it(self):
        from unittest import mock

        from anno_modding_studio.core.changes import AIM_TWEAK, ModLayer

        self.ready()

        def arrive(separate=False, start_dir=""):
            self.window.loadout.set_layer(
                ModLayer(origin="Somebody Else", name="Somebody Else", creator="Them")
            )
            return True

        with mock.patch.object(MainWindow, "import_mod", side_effect=arrive):
            self.window.start.choose_aim(AIM_TWEAK)

        patch = self.window.loadout.patch_for("Somebody Else")
        self.assertIsNotNone(patch)
        self.assertEqual(patch.patches, "Somebody Else")
        # And it is what edits land in, which is the whole point of making it.
        self.assertEqual(self.window.loadout.editing_origin(), patch.origin)
        self.assertIn("loads straight after", self.window.status_label.text())

    def test_but_not_for_a_mod_that_is_already_yours(self):
        """Editing your own file directly is right; a patch would be an odd
        second folder holding half of it."""
        from unittest import mock

        from anno_modding_studio.core.changes import AIM_TWEAK, ModLayer

        self.ready()
        self.window.loadout.own.creator = "Me"

        def arrive(separate=False, start_dir=""):
            self.window.loadout.set_layer(
                ModLayer(origin="Mine", name="Mine", creator="Me")
            )
            return True

        with mock.patch.object(MainWindow, "import_mod", side_effect=arrive):
            self.window.start.choose_aim(AIM_TWEAK)
        self.assertIsNone(self.window.loadout.patch_for("Mine"))

    def test_and_not_when_the_reader_was_cancelled(self):
        from unittest import mock

        from anno_modding_studio.core.changes import AIM_TWEAK

        self.ready()
        with mock.patch.object(MainWindow, "import_mod", return_value=False):
            self.window.start.choose_aim(AIM_TWEAK)
        self.assertEqual(self.window.loadout.layers, [])

    def test_running_a_mod_list_opens_the_whole_folder_reader(self):
        """With no arguments: ``picked=[]`` is the library's list instead."""
        from anno_modding_studio.core.changes import AIM_MANAGE

        self.ready()
        with mock.patch.object(MainWindow, "load_installed_mods") as opened:
            self.window.start.choose_aim(AIM_MANAGE)
        opened.assert_called_once_with()
        self.assertEqual(self.window.loadout.aim, AIM_MANAGE)

    # -- what it says to do next ---------------------------------------------
    def test_the_next_step_is_read_off_what_is_actually_open(self):
        """Telling somebody to read in a mod when they have sixty loaded is
        worse than saying nothing."""
        from anno_modding_studio.core.changes import AIM_MANAGE, AIM_TWEAK, ModLayer

        self.ready()
        self.assertIn("No mods read in yet", self.cards()[AIM_TWEAK].status.text())
        self.window.loadout.set_layer(ModLayer(origin="Somebody Else"))
        self.window.start.refresh_source_panel()
        self.assertIn("Patch this mod", self.cards()[AIM_TWEAK].status.text())
        self.assertEqual("A loadout.", self.cards()[AIM_MANAGE].status.text())
        self.assertNotIn("1 mod", self.cards()[AIM_MANAGE].status.text())

    def test_and_says_where_switched_off_mods_get_written_back(self):
        from anno_modding_studio.core.changes import AIM_MANAGE, ModLayer

        self.ready()
        self.window.loadout.set_layer(ModLayer(origin="Off One", active=False))
        self.window.start.refresh_source_panel()
        self.assertIn("A loadout", self.cards()[AIM_MANAGE].status.text())
        # A loadout is played, and forked to try something.
        self.window.loadout.own.loads_last = True
        self.window.start.refresh_source_panel()
        said = self.cards()[AIM_MANAGE].status.text()
        # It says what the loadout is. Steps 3 and 4 say what they do themselves.
        self.assertEqual("A loadout.", said)

    def test_which_way_you_are_working_survives_saving_and_opening(self):
        from anno_modding_studio.core.changes import AIM_MANAGE, Loadout

        self.ready()
        self.window.loadout.aim = AIM_MANAGE
        self.window.loadout.mods_folder = r"C:\some\mods"
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "p.annoload"
            self.window.loadout.save(path)
            reopened = Loadout.load(path)
        self.assertEqual(reopened.aim, AIM_MANAGE)
        self.assertEqual(reopened.mods_folder, r"C:\some\mods")

    # -- landing where you actually said to go -------------------------------
    def test_loading_the_game_files_does_not_jump_ahead_of_the_question(self):
        """The second half of Start -- which of the three ways -- has not been
        asked yet, so landing here should not skip past it."""
        self.ready()
        self.assertEqual(self.window.tabs.currentIndex(), TAB_SOURCE)

    def test_a_reindex_does_not_jump_ahead_either(self):
        self.ready()
        self.window.tabs.setCurrentIndex(TAB_SOURCE)
        self.window.game_files.adopt_index(self.db, self.assets, cached=True)
        self.assertEqual(self.window.tabs.currentIndex(), TAB_SOURCE)

    def test_changing_a_mod_only_lands_you_there_once_something_came_of_it(self):
        """The click and the answer are the same gesture -- but only once
        there is an answer.  A cancelled dialog leaves you exactly where you
        were, on the tab that asked the question."""
        from unittest import mock

        from anno_modding_studio.core.changes import AIM_TWEAK

        self.ready()
        with mock.patch.object(MainWindow, "import_mod", return_value=False):
            self.window.start.choose_aim(AIM_TWEAK)
        self.assertEqual(self.window.tabs.currentIndex(), TAB_SOURCE)

    def test_but_does_once_a_mod_actually_came_in(self):
        from unittest import mock

        from anno_modding_studio.core.changes import AIM_TWEAK

        self.ready()
        with mock.patch.object(MainWindow, "import_mod", return_value=True):
            self.window.start.choose_aim(AIM_TWEAK)
        self.assertEqual(self.window.tabs.currentIndex(), TAB_EDIT)

    def test_the_same_holds_for_running_a_whole_mod_list(self):
        from unittest import mock

        from anno_modding_studio.core.changes import AIM_MANAGE

        self.ready()
        with mock.patch.object(MainWindow, "load_installed_mods", return_value=False):
            self.window.start.choose_aim(AIM_MANAGE)
        self.assertEqual(self.window.tabs.currentIndex(), TAB_SOURCE)

    # -- switching your mind --------------------------------------------------
    def test_switching_which_way_with_mods_already_loaded_asks_first(self):
        """Not just for "write a mod of your own" -- any switch away from a
        way that has mods loaded is the same trap: the wrong mix is invisible
        until export."""


        from anno_modding_studio.core.changes import AIM_MANAGE, AIM_TWEAK, ModLayer

        self.ready()
        self.window.loadout.aim = AIM_MANAGE
        self.window.loadout.set_layer(ModLayer(origin="A Mod"))
        with _isolation.answering(False) as asked:
            self.window.start.choose_aim(AIM_TWEAK)
        self.assertTrue(asked.called)
        self.assertEqual(self.window.loadout.aim, AIM_MANAGE)
        self.assertEqual(len(self.window.loadout.layers), 1)

    def test_keeping_what_is_loaded_is_not_one_of_the_answers(self):
        """A card is a setup, and a setup laid over whatever was loaded is not
        one.  There used to be a middle answer that kept the mods and only
        relabelled the work; the mods you want beside your own are imported
        after the card, not carried through it."""


        from anno_modding_studio.core.changes import AIM_MAKE, AIM_MANAGE, ModLayer

        self.ready()
        self.window.loadout.set_up_as(AIM_MANAGE)
        self.window.loadout.set_layer(ModLayer(origin="A Mod"))
        with _isolation.answering(False) as asked:
            self.window.start.choose_aim(AIM_MAKE)
        self.assertEqual(asked.buttons, [["Start fresh", "Cancel"]])
        self.assertEqual(self.window.loadout.aim, AIM_MANAGE)
        self.assertEqual(len(self.window.loadout.layers), 1)

    def test_the_same_card_again_with_mods_loaded_asks_too(self):
        """A card is a fresh piece of work, not a mode to be already in.  It
        used to compare against the card clicked last, which said nothing
        once the loadout had drifted into something else since."""
        from unittest import mock

        from anno_modding_studio.core.changes import AIM_MANAGE, ModLayer

        self.ready()
        self.window.loadout.set_up_as(AIM_MANAGE)
        self.window.loadout.set_layer(ModLayer(origin="A Mod"))
        with _isolation.answering(False) as asked:
            with mock.patch.object(MainWindow, "load_installed_mods", return_value=False):
                self.window.start.choose_aim(AIM_MANAGE)
        self.assertTrue(asked.called)
        self.assertEqual(len(self.window.loadout.layers), 1)

    # -- what a card is, and is not ------------------------------------------
    def test_the_card_that_set_the_loadout_up_stays_marked_however_far_it_drifts(self):
        """Pick "write a mod of your own", load somebody's mod to check
        against, patch it: the card is a recollection of the click, and says
        so.  Reading the card off what was loaded since would have moved it
        to "change a mod you downloaded", which is not what happened."""
        from anno_modding_studio.core.changes import AIM_MAKE, AIM_TWEAK, ModLayer

        self.ready()
        self.window.loadout.set_up_as(AIM_MAKE)
        self.window.loadout.set_layer(ModLayer(origin="Theirs"))
        self.window.loadout.set_layer(ModLayer(origin="Theirs (my patch)", patches="Theirs"))
        self.window.start.refresh_source_panel()
        self.assertTrue(self.cards()[AIM_MAKE].property("current"))
        self.assertEqual(self.cards()[AIM_MAKE].mark.text(), "set up here")
        self.assertFalse(self.cards()[AIM_TWEAK].property("current"))
        self.assertIn("Set up as", self.window.source.ways_step.status.full_text())

    def test_and_the_status_lines_say_what_is_there_rather_than_what_was_clicked(self):
        from anno_modding_studio.core.changes import AIM_MAKE, AIM_TWEAK, ModLayer

        self.ready()
        self.window.loadout.set_up_as(AIM_MAKE)
        self.window.loadout.set_layer(ModLayer(origin="Theirs"))
        self.window.loadout.set_layer(ModLayer(origin="Theirs (my patch)", patches="Theirs"))
        self.window.start.refresh_source_panel()
        self.assertIn("Other mods are loaded", self.cards()[AIM_MAKE].status.text())
        self.assertIn("Patched: Theirs", self.cards()[AIM_TWEAK].status.text())


class TestTheLoadoutYouAreWorkingIn(unittest.TestCase):
    """Picking a way of working picks a place for the work to live.

    Changes that do not know where they belong are changes you have not really
    decided to make.  The app always knew -- it slugified a home silently and
    committed to it on the first export -- it just never said so, and made you
    confirm a location you never chose once the work was already done.
    """

    @classmethod
    def setUpClass(cls):
        sys.path.insert(0, str(ROOT / "tools"))
        import make_sample_assets

        from anno_modding_studio.core import index as index_mod

        cls._tmp = tempfile.TemporaryDirectory()
        tmp = Path(cls._tmp.name)
        cls.assets = tmp / "assets.xml"
        cls.assets.write_text(make_sample_assets.build_assets_xml(20), encoding="utf-8")
        cls.db = tmp / "index.sqlite"
        index_mod.build_index(cls.assets, cls.db)

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def setUp(self):
        self.loadouts = tempfile.TemporaryDirectory()
        self.window = MainWindow()
        self.addCleanup(_isolation.forget_settings)
        # Never the developer's real loadouts folder: these write files.
        self.window.files.loadouts_dir = lambda: Path(self.loadouts.name)
        self.window.game_files.adopt_index(self.db, self.assets, cached=True)
        # Somebody who has already said who they are: these are about naming
        # the loadout, and only passed before because an earlier test had left
        # a creator name in the shared settings.
        self.window.settings.setValue(self.window.preferences.CREATOR_KEY, "Me")

    def tearDown(self):
        self.window.editor.search._debounce.stop()
        self.window.game_files.stop_texts_worker()
        if self.window.index is not None:
            self.window.index.close()
            self.window.index = None
        self.window.loadout.clear()
        self.window.deleteLater()
        self.loadouts.cleanup()

    # -- before anything is chosen -------------------------------------------
    def test_there_is_no_loadout_until_there_is(self):
        self.assertIsNone(self.window.loadout_path)
        self.assertIn("No loadout", self.window.windowTitle())
        self.assertIn("No loadout yet", self.window.source.loadout_label.full_text())

    def test_and_the_title_names_no_mod_while_there_is_none(self):
        """It ended "My Anno Mod": the name a mod would start with, of a mod
        nobody had made."""
        from anno_modding_studio.core.changes import AIM_MAKE

        self.assertEqual(
            self.window.windowTitle(),
            "Anno Modding Studio %s (dev)  —  No loadout  —  Anno 1800" % __version__,
        )
        name_the_mod(self, answer="Roomier Farmers")
        self.window.start.choose_aim(AIM_MAKE)
        self.assertIn("Roomier Farmers", self.window.windowTitle())
        # And none again once the loadout is gone.
        with mock.patch.object(self.window.files, "confirm_discard", return_value=True):
            self.assertTrue(self.window.new_loadout())
        self.assertEqual(
            self.window.windowTitle(),
            "Anno Modding Studio %s (dev)  —  No loadout  —  Anno 1800" % __version__,
        )

    # -- writing a mod of your own -------------------------------------------
    def test_choosing_it_asks_what_the_mod_is_called(self):
        from anno_modding_studio.core.changes import AIM_MAKE

        asked = name_the_mod(self, answer="Roomier Farmers")
        self.window.start.choose_aim(AIM_MAKE)
        self.assertEqual(asked, ["My Anno Mod"])
        self.assertEqual(self.window.loadout.own.name, "Roomier Farmers")

    def test_and_the_answer_places_the_loadout(self):
        from anno_modding_studio.core.changes import AIM_MAKE

        name_the_mod(self, answer="Roomier Farmers")
        self.window.start.choose_aim(AIM_MAKE)
        self.assertEqual(
            self.window.loadout_path,
            Path(self.loadouts.name) / "Roomier Farmers.annoload",
        )
        self.assertIn("Roomier Farmers", self.window.windowTitle())

    def test_but_nothing_is_written_for_somebody_only_looking(self):
        """A card opened to see what is behind it and then abandoned should not
        leave a file in the loadouts folder."""
        from anno_modding_studio.core.changes import AIM_MAKE

        name_the_mod(self, answer="Roomier Farmers")
        self.window.start.choose_aim(AIM_MAKE)
        self.assertFalse(self.window.loadout_path.exists())
        self.assertFalse(self.window.on_disk)
        self.assertIn("(new)", self.window.windowTitle())

    def test_the_first_change_is_what_writes_it(self):
        from anno_modding_studio.core.changes import AIM_MAKE

        name_the_mod(self, answer="Roomier Farmers")
        self.window.start.choose_aim(AIM_MAKE)
        self.window._open_asset("1010340")
        self.window.on_changes_updated()
        self.assertTrue(self.window.loadout_path.exists())
        self.assertTrue(self.window.on_disk)
        self.assertNotIn("(new)", self.window.windowTitle())

    def test_declining_the_name_declines_the_whole_thing(self):
        """Half-choosing a way of working is the state this exists to avoid."""
        from anno_modding_studio.core.changes import AIM_MAKE

        name_the_mod(self, ok=False)
        self.window.start.choose_aim(AIM_MAKE)
        self.assertEqual(self.window.loadout.aim, "")
        self.assertIsNone(self.window.loadout_path)

    def test_saving_no_longer_has_to_ask_where(self):
        """Ctrl+S has somewhere to go, which is the point of settling it early."""
        from PySide6.QtWidgets import QFileDialog

        from anno_modding_studio.core.changes import AIM_MAKE

        name_the_mod(self, answer="Roomier Farmers")
        self.window.start.choose_aim(AIM_MAKE)
        with mock.patch.object(QFileDialog, "getSaveFileName") as asked:
            self.assertTrue(self.window.files.save_loadout())
        asked.assert_not_called()
        self.assertTrue(self.window.loadout_path.exists())

    # -- the other two, which cannot be asked up front -----------------------
    def test_changing_a_downloaded_mod_is_named_after_the_patch(self):
        from anno_modding_studio.core.changes import AIM_TWEAK, ModLayer

        def arrive(separate=False, start_dir=""):
            self.window.loadout.set_layer(
                ModLayer(origin="Somebody Else", name="Somebody Else", creator="Them")
            )
            return True

        with mock.patch.object(MainWindow, "import_mod", side_effect=arrive):
            self.window.start.choose_aim(AIM_TWEAK)
        self.assertEqual(
            self.window.files.loadout_name(), "Somebody Else (my patch)"
        )

    def test_but_not_after_a_mod_of_yours_let_go_of_for_the_loadout_that_has_it(self):
        """A mod of yours whose project a loadout has is offered there and
        dropped here: declining that left the loadout named after the mod
        that was gone, and the status line saying it had been read in."""
        from anno_modding_studio.core import filecache
        from anno_modding_studio.core import projects as project_files
        from anno_modding_studio.core.changes import AIM_TWEAK, ModLayer

        self.window.loadout.own.creator = "Me"

        def arrive(separate=False, start_dir=""):
            self.window.loadout.set_layer(
                ModLayer(origin="Mine", name="Mine", creator="Me", mod_id="me-mine")
            )
            self.window.status_label.setText("Imported 1 ModOp from Mine.")
            return True

        kept = Path(self.loadouts.name) / "Mine.annoproj"
        with mock.patch.object(MainWindow, "import_mod", side_effect=arrive), \
                mock.patch.object(filecache, "project_keeping", return_value=kept), \
                mock.patch.object(filecache, "loadouts_with_project",
                                  return_value=[Path(self.loadouts.name) / "Mine.annoload"]), \
                mock.patch.object(project_files, "read", return_value={}), \
                mock.patch.object(self.window.prompts, "ask_open_kept", return_value=-1), \
                mock.patch.object(self.window, "load_loadout") as opened:
            self.window.start.choose_aim(AIM_TWEAK)
        opened.assert_not_called()
        self.assertEqual(self.window.loadout.layers, [])
        self.assertIsNone(self.window.loadout_path)
        self.assertEqual(self.window.loadout.aim, "")
        self.assertEqual(
            self.window.status_label.text(),
            "Left Mine out. It is kept in a project already: open a loadout that has "
            "it to carry on there.",
        )

    def test_a_loadout_is_named_after_the_game(self):
        from anno_modding_studio.core.changes import AIM_MANAGE

        def arrive():
            self.window.loadout.mods_folder = r"C:\Games\Anno 1800\mods"
            return True

        with mock.patch.object(
            MainWindow, "load_installed_mods", side_effect=arrive
        ):
            self.window.start.choose_aim(AIM_MANAGE)
        self.assertEqual(self.window.files.loadout_name(), "Anno 1800 loadout")
        self.assertTrue(self.window.loadout.own.loads_last)

    def test_a_loadout_read_from_a_folder_elsewhere_is_named_after_the_game_too(self):
        """It was named after the folder's parent: "hands-on loadout" of
        …/hands-on/mods-150 (H2-23)."""
        from anno_modding_studio.core.changes import AIM_MANAGE

        def arrive():
            self.window.loadout.mods_folder = r"D:\elsewhere\mods-150"
            return True

        with mock.patch.object(
            MainWindow, "load_installed_mods", side_effect=arrive
        ):
            self.window.start.choose_aim(AIM_MANAGE)
        self.assertEqual(self.window.files.loadout_name(), "Anno 1800 loadout")

    def test_neither_of_them_is_asked_for_a_name(self):
        from anno_modding_studio.core.changes import AIM_MANAGE

        asked = name_the_mod(self)
        with mock.patch.object(MainWindow, "load_installed_mods", return_value=True):
            self.window.start.choose_aim(AIM_MANAGE)
        self.assertEqual(asked, [])

    def test_and_the_card_counts_the_mods_they_were_named_with(self):
        """It said "Empty" of every loadout not written yet, and these two
        are named after their mods were read in: "Empty" under a title saying
        "3 mods loaded"."""
        from anno_modding_studio.core.changes import AIM_MANAGE, ModLayer

        def arrive():
            for name in ("One", "Two", "Three"):
                self.window.loadout.set_layer(ModLayer(origin=name, name=name))
            return True

        with mock.patch.object(MainWindow, "load_installed_mods", side_effect=arrive):
            self.window.start.choose_aim(AIM_MANAGE)
        self.assertFalse(self.window.on_disk)
        self.assertFalse(self.window.loadout_path.exists())
        self.assertIn("  —  3 mods loaded", self.window.windowTitle())
        detail = self.window.source.loadout_detail.text()
        self.assertTrue(detail.startswith("3 mods loaded. It is written to "), detail)
        self.assertTrue(detail.endswith(" when you make a change."), detail)

    def test_as_the_title_counts_them_with_a_patch_of_yours_apart(self):
        from anno_modding_studio.core.changes import AIM_TWEAK, ModLayer

        def arrive(separate=False, start_dir=""):
            self.window.loadout.set_layer(
                ModLayer(origin="Somebody Else", name="Somebody Else", creator="Them")
            )
            return True

        with mock.patch.object(MainWindow, "import_mod", side_effect=arrive):
            self.window.start.choose_aim(AIM_TWEAK)
        self.assertFalse(self.window.on_disk)
        self.assertIn("  —  1 mod loaded · 1 yours", self.window.windowTitle())
        detail = self.window.source.loadout_detail.text()
        self.assertTrue(detail.startswith("1 mod loaded. It is written to "), detail)

    def test_and_one_with_nothing_in_it_is_still_empty(self):
        from anno_modding_studio.core.changes import AIM_MAKE

        name_the_mod(self, answer="Roomier Farmers")
        self.window.start.choose_aim(AIM_MAKE)
        self.assertFalse(self.window.on_disk)
        detail = self.window.source.loadout_detail.text()
        self.assertTrue(detail.startswith("Empty. It is written to "), detail)

    # -- renaming ------------------------------------------------------------
    def test_renaming_moves_the_file_when_there_is_one(self):
        from anno_modding_studio.core.changes import AIM_MAKE

        name_the_mod(self, answer="Roomier Farmers")
        self.window.start.choose_aim(AIM_MAKE)
        self.window._open_asset("1010340")
        self.window.on_changes_updated()
        was = self.window.loadout_path

        self.assertTrue(self.window.files.rename_loadout("Much Roomier Farmers"))
        self.assertFalse(was.exists())
        self.assertTrue(self.window.loadout_path.exists())
        self.assertEqual(self.window.files.loadout_name(), "Much Roomier Farmers")

    def test_and_a_change_of_letter_case_alone_is_a_rename(self):
        """Roomier Farmers to roomier farmers did nothing and said nothing:
        Windows paths compare without case, so the new file was taken for
        the one already there."""
        from anno_modding_studio.core.changes import AIM_MAKE

        name_the_mod(self, answer="Roomier Farmers")
        self.window.start.choose_aim(AIM_MAKE)
        self.window._open_asset("1010340")
        self.window.on_changes_updated()

        self.assertTrue(self.window.files.rename_loadout("roomier farmers"))
        self.assertEqual(self.window.files.loadout_name(), "roomier farmers")
        self.assertEqual(self.window.loadout.own.name, "roomier farmers")
        on_disk = [p.name for p in self.window.loadout_path.parent.iterdir()]
        self.assertIn(self.window.loadout_path.name, on_disk)
        self.assertNotIn("Roomier Farmers" + self.window.loadout_path.suffix, on_disk)

    def test_but_the_name_it_already_has_is_not(self):
        from anno_modding_studio.core.changes import AIM_MAKE

        name_the_mod(self, answer="Roomier Farmers")
        self.window.start.choose_aim(AIM_MAKE)
        self.assertFalse(self.window.files.rename_loadout("Roomier Farmers"))

    def test_and_renames_the_mod_with_it_when_the_loadout_is_the_mod(self):
        from anno_modding_studio.core.changes import AIM_MAKE

        name_the_mod(self, answer="Roomier Farmers")
        self.window.start.choose_aim(AIM_MAKE)
        self.window.files.rename_loadout("Much Roomier Farmers")
        self.assertEqual(self.window.loadout.own.name, "Much Roomier Farmers")

    def test_and_the_file_it_moved_holds_the_new_name_at_once(self):
        """The name waited in the unsaved loadout while the file moved at
        once: Discard on the way out left "Much Roomier Farmers" holding a
        mod called "Roomier Farmers", and the title said both."""
        from anno_modding_studio.core.changes import AIM_MAKE, Loadout

        name_the_mod(self, answer="Roomier Farmers")
        self.window.start.choose_aim(AIM_MAKE)
        self.window._open_asset("1010340")
        self.window.on_changes_updated()
        self.assertFalse(self.window.files.unsaved_work())

        self.window.files.rename_loadout("Much Roomier Farmers")
        saved = Loadout.load(self.window.loadout_path)
        self.assertEqual(saved.own.name, "Much Roomier Farmers")
        # And so there is nothing left to save, or to ask about on the way out.
        self.assertFalse(self.window.files.unsaved_work())
        self.assertNotIn("*", self.window.windowTitle())

    def test_but_nothing_else_that_was_unsaved_is_written_with_it(self):
        from anno_modding_studio.core.changes import AIM_MAKE, Loadout

        name_the_mod(self, answer="Roomier Farmers")
        self.window.start.choose_aim(AIM_MAKE)
        self.window._open_asset("1010340")
        self.window.on_changes_updated()
        self.window.loadout.own.description = "Typed, and not saved."
        self.window.files.mark_unsaved()

        self.window.files.rename_loadout("Much Roomier Farmers")
        saved = Loadout.load(self.window.loadout_path)
        self.assertEqual(
            (saved.own.name, saved.own.description), ("Much Roomier Farmers", "")
        )
        self.assertTrue(self.window.files.unsaved_work(), "the description still is")

    def test_and_everything_that_names_the_mod_says_the_new_name_at_once(self):
        """Its row in the Mod changes panel kept the old name until the
        loadout was opened again: the row was drawn from a description of
        the mod taken before the rename."""
        from anno_modding_studio.core.changes import AIM_MAKE, Change
        from anno_modding_studio.ui.editor_panel import VIEW_XML

        name_the_mod(self, answer="Roomier Farmers")
        self.window.start.choose_aim(AIM_MAKE)
        self.window.editor.set_view(VIEW_XML)
        self.window._open_asset("1010340")
        self.window.loadout.set_change(Change(
            guid="1010340", asset_label="Lumberjack", path="/Values/FactoryBase/CycleTime",
            tag="CycleTime", old_value="15", new_value="20",
        ))
        self.window.on_changes_updated()
        strip = self.window.editor.xml.strip

        def named() -> list:
            row = self.window.changes_panel.tree.topLevelItem(0)
            return [
                row.text(0), row.toolTip(0).splitlines()[0],
                self.window.editor.target_box.currentText(),
                self.window.output.name_edit.text(),
                *(strip.tabText(at) for at in range(strip.count()) if strip.tabText(at) != "Base game"),
            ]

        self.assertTrue(all("Roomier Farmers" in said for said in named()), named())
        self.window.files.rename_loadout("Larger Farms")
        self.assertTrue(all("Larger Farms" in said for said in named()), named())
        self.assertFalse(any("Roomier" in said for said in named()), named())

    def test_but_leaves_somebody_elses_mods_alone_when_it_is_a_workspace(self):
        from anno_modding_studio.core.changes import AIM_MANAGE

        with mock.patch.object(MainWindow, "load_installed_mods", return_value=True):
            self.window.start.choose_aim(AIM_MANAGE)
        self.assertEqual(self.window.loadout.own.name, "Anno 1800 loadout patch")
        self.window.files.rename_loadout("Tuesday's list")
        self.assertEqual(self.window.files.loadout_name(), "Tuesday's list")
        # The patch over its list is named after it, and goes with it, so
        # that two loadouts' patches are never one mod; nothing else is.
        self.assertEqual(self.window.loadout.own.name, "Tuesday's list patch")

    # -- leaving one for another ----------------------------------------------
    def _open_an_asset_in_a_mod_of_your_own(self):
        from anno_modding_studio.core.changes import AIM_MAKE
        from anno_modding_studio.ui.editor_panel import VIEW_XML

        name_the_mod(self, answer="Roomier Farmers")
        self.window.start.choose_aim(AIM_MAKE)
        self.window.editor.set_view(VIEW_XML)
        self.window._open_asset("1010340")
        self.window._open_asset("1010030")
        self.window.show_references("1010030")
        # Found by its GUID, which is still in the box once it is open.
        search = self.window.editor.search
        search.search_box.setText("1010030")
        search._debounce.stop()
        search.close_results()
        strip = self.window.editor.xml.strip
        self.assertIn("Roomier Farmers", [strip.tabText(at) for at in range(strip.count())])
        return strip

    def _no_asset_is_open(self, strip):
        self.assertIsNone(self.window.editor.guid)
        self.assertEqual(self.window.editor.title_label.text(), "No asset selected")
        # No tab of any mod, the last loadout's or one nobody made.
        self.assertEqual(strip.count(), 0)
        self.assertIsNone(self.window.references.guid)
        self.assertIsNone(self.window.trail.current)
        self.assertFalse(self.window.trail.can_go_back())
        # Nor is what was typed to find it still searched for, and emptying
        # the box is not somebody typing in it: the list stays shut.
        search = self.window.editor.search
        self.assertEqual(search.search_box.text(), "")
        self.assertTrue(search.results.isHidden())
        self.assertFalse(search._debounce.isActive())
        self.assertFalse(search.search_box.hasFocus())

    def test_a_new_loadout_starts_with_no_asset_open(self):
        """The asset open in the loadout before stayed open, in the XML view
        under a tab named "My Anno Mod": a mod nobody had made, and still
        named there once a mod was read in and the loadout was that mod."""
        strip = self._open_an_asset_in_a_mod_of_your_own()
        with mock.patch.object(self.window.files, "confirm_discard", return_value=True):
            self.assertTrue(self.window.new_loadout())
        self._no_asset_is_open(strip)

    def test_and_so_does_a_loadout_that_is_opened(self):
        from anno_modding_studio.core.changes import AIM_MAKE, Loadout

        other = Loadout(source_path=str(self.assets), game=self.window.loadout.game)
        other.own.name = "Better Harbours"
        other.set_up_as(AIM_MAKE)
        other.make_own_mod()
        path = Path(self.loadouts.name) / "Better Harbours.annoload"
        other.save(path)
        strip = self._open_an_asset_in_a_mod_of_your_own()
        self.assertTrue(self.window.load_loadout(path, asked=True))
        self.assertEqual(self.window.loadout.own.name, "Better Harbours")
        self._no_asset_is_open(strip)

    def test_a_name_already_taken_gets_a_number_rather_than_an_overwrite(self):
        (Path(self.loadouts.name) / "Roomier Farmers.annoload").write_text(
            "{}", encoding="utf-8"
        )
        self.assertEqual(
            self.window.files.loadout_home("Roomier Farmers").name,
            "Roomier Farmers (2).annoload",
        )

    def test_but_a_loadout_never_clashes_with_its_own_file(self):
        home = Path(self.loadouts.name) / "Roomier Farmers.annoload"
        home.write_text("{}", encoding="utf-8")
        self.window.loadout_path = home
        self.assertEqual(self.window.files.loadout_home("Roomier Farmers"), home)

    # -- coming back to one --------------------------------------------------
    def test_a_loadout_you_open_goes_on_the_list_for_next_time(self):
        path = Path(self.loadouts.name) / "Earlier.annoload"
        self.window.loadout.save(path)
        self.assertTrue(self.window.load_loadout(path))
        self.assertIn(path, self.window.files.recent_loadouts())
        self.assertTrue(self.window.on_disk)

    def test_and_the_list_leaves_out_what_is_no_longer_there(self):
        path = Path(self.loadouts.name) / "Gone.annoload"
        self.window.loadout.save(path)
        self.window.load_loadout(path)
        path.unlink()
        self.assertNotIn(path, self.window.files.recent_loadouts())

    def test_opening_one_that_has_gone_says_so_rather_than_failing(self):
        from PySide6.QtWidgets import QMessageBox

        path = Path(self.loadouts.name) / "Gone.annoload"
        with mock.patch.object(QMessageBox, "information") as told:
            self.assertFalse(self.window.files.open_recent(path))
        self.assertTrue(told.called)

    def test_an_opened_loadout_answers_the_aim_question_itself(self):
        """Which is why the loadout comes first: it already knows."""
        from anno_modding_studio.core.changes import AIM_MANAGE

        path = Path(self.loadouts.name) / "Earlier.annoload"
        self.window.loadout.aim = AIM_MANAGE
        self.window.loadout.save(path)
        self.window.loadout.aim = ""

        self.window.load_loadout(path)
        self.assertEqual(self.window.loadout.aim, AIM_MANAGE)
        self.assertTrue(self.window.source.ways[AIM_MANAGE].property("current"))


class TestKeepingSettingsOutOfTheRealOnes(unittest.TestCase):
    """Where the remembered folders live, and how a script stays out of them.

    The suite redirects ``QSettings.setPath``, which works because it runs
    in-process.  A script that opens a window to look at something runs in its
    own process and has to remember to do the same -- and forgetting writes temp
    directories into the developer's real settings, which is not hypothetical:
    it has happened twice, once before this file existed and once after.

    An environment variable cannot be forgotten by the thing it is protecting,
    because it is set from outside it.
    """

    def test_a_named_file_is_used_when_one_is_given(self):
        from anno_modding_studio.ui.main_window import open_settings

        with tempfile.TemporaryDirectory() as tmp:
            wanted = Path(tmp) / "isolated.ini"
            with mock.patch.dict(
                os.environ, {"ANNO_STUDIO_SETTINGS": str(wanted)}
            ):
                settings = open_settings()
                self.assertEqual(
                    Path(settings.fileName()).resolve(), wanted.resolve()
                )
                settings.setValue("probe", "yes")
                settings.sync()
            self.assertTrue(wanted.is_file())

    def test_and_nothing_is_written_anywhere_else(self):
        from anno_modding_studio.ui.main_window import open_settings

        with tempfile.TemporaryDirectory() as tmp:
            wanted = Path(tmp) / "isolated.ini"
            with mock.patch.dict(
                os.environ, {"ANNO_STUDIO_SETTINGS": str(wanted)}
            ):
                open_settings().setValue("probe", "yes")
            elsewhere = open_settings()
            self.assertNotEqual(
                Path(elsewhere.fileName()).resolve(), wanted.resolve()
            )
            self.assertIsNone(elsewhere.value("probe"))

    def test_without_one_it_is_still_an_ini_and_never_the_registry(self):
        """Nothing can redirect the registry, so a test run that reached it
        would write into the developer's own settings and did."""
        from anno_modding_studio.ui.main_window import open_settings

        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("ANNO_STUDIO_SETTINGS", None)
            settings = open_settings()
        self.assertTrue(settings.fileName().endswith(".ini"), settings.fileName())



class TestOpeningALoadoutBuiltAgainstOtherGameFiles(unittest.TestCase):
    """A loadout knows which assets.xml it was built against. Opening it has to
    honour that, or nothing on screen means what it says.

    The offer to load a loadout's own base file used to be made only when
    *nothing* was indexed.  So: load 117, save a loadout, load 1800 instead,
    reopen the 117 loadout -- and the loadout came up saying Anno 117 while the
    index, the localisation and every GUID behind it were Anno 1800's.  Nothing
    said so, because the one place the game was written down was a file path
    that is usually elided.
    """

    @classmethod
    def setUpClass(cls):
        sys.path.insert(0, str(ROOT / "tools"))
        import make_sample_assets

        from anno_modding_studio.core import index as index_mod

        cls._tmp = tempfile.TemporaryDirectory()
        tmp = Path(cls._tmp.name)
        # Two base files at the two games' canonical paths, so detect_from_path
        # tells them apart the way it does for real.
        cls.files = {}
        for game, relpath in (
            ("1800", "data/config/export/main/asset/assets.xml"),
            ("117", "data/base/config/export/assets.xml"),
        ):
            path = tmp / game / relpath
            path.parent.mkdir(parents=True)
            path.write_text(make_sample_assets.build_assets_xml(20), encoding="utf-8")
            db = tmp / ("%s.sqlite" % game)
            index_mod.build_index(path, db)
            cls.files[game] = (db, path)

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def setUp(self):
        self.window = MainWindow()
        self.addCleanup(_isolation.forget_settings)
        self.asked = []
        # Whether to go on, where a question is asked.
        self.answer = True
        # And what each question said.
        self.texts = []
        named = _isolation.answering(
            lambda title, text: self.asked.append(title) or self.texts.append(text)
            or self.answer
        )
        named.start()
        self.addCleanup(named.stop)
        real_w = QMessageBox.warning
        # And what a warning said, for the ones whose wording is the point.
        self.warned = []
        QMessageBox.warning = staticmethod(
            lambda parent, title, text, *a, **k: self.asked.append(title)
            or self.warned.append(text)
        )
        self.addCleanup(setattr, QMessageBox, "warning", staticmethod(real_w))

    def tearDown(self):
        self.window.editor.search._debounce.stop()
        self.window.game_files.stop_texts_worker()
        if self.window.index is not None:
            self.window.index.close()
            self.window.index = None
        self.window.loadout.clear()
        self.window.deleteLater()

    def load(self, game):
        db, path = self.files[game]
        self.window.game_files.adopt_index(db, path, cached=True)

    def save_loadout_for(self, game):
        self.load(game)
        path = Path(self._tmp.name) / ("%s.annoload" % game)
        self.window.loadout.save(path)
        return path

    def gone_for(self, game):
        """A loadout for ``game`` whose base file is no longer where it was."""
        from anno_modding_studio.core.changes import Loadout

        saved = self.save_loadout_for(game)
        stray = Loadout.load(saved)
        stray.source_path = str(Path(self._tmp.name) / "vanished" / game / "assets.xml")
        stray.save(saved)
        return saved

    def settle(self):
        """Wait out the index build that opening a loadout kicks off.

        Nothing here is cached at the path the Studio caches to, so the base
        file a loadout brings with it goes to a worker thread rather than
        being adopted on the spot.  Until it lands, the index is still the one
        that was open -- so anything asking what is loaded has to wait for it.
        """
        worker = self.window.game_files.worker
        if worker is not None:
            self.assertTrue(worker.wait(30000))
        QApplication.processEvents()
        self.assertIsNone(
            self.window.game_files.pending_loadout, "the loadout never got taken up"
        )

    # -- the bug -------------------------------------------------------------
    def test_opening_one_for_the_other_game_loads_its_own_files(self):
        saved = self.save_loadout_for("117")
        self.load("1800")
        self.assertEqual(self.window.loadout.game, "anno1800")

        self.asked.clear()
        self.window.load_loadout(saved)
        self.assertIn("Load its game files too?", self.asked)
        self.settle()
        self.assertEqual(self.window.loadout_path, saved)
        # The index is the loadout's own, not the one that happened to be open.
        self.assertEqual(self.window.loadout.game, "anno117")
        self.assertEqual(
            Path(self.window.index.source_path), self.files["117"][1]
        )

    def test_and_what_is_shown_agrees_with_what_is_indexed(self):
        saved = self.save_loadout_for("117")
        self.load("1800")
        self.window.load_loadout(saved)
        self.settle()
        shown = self.window.source.assets_label.full_text()
        self.assertEqual(Path(shown), self.files["117"][1])
        self.assertIn("Anno 117", self.window.source.game_label.text())
        self.assertIn("Anno 117", self.window.windowTitle())

    def test_declining_leaves_the_loadout_unopened_rather_than_mismatched(self):
        """There is no version of carrying on that is correct."""
        saved = self.save_loadout_for("117")
        self.load("1800")
        before = self.window.loadout.source_path

        self.answer = False
        self.assertFalse(self.window.load_loadout(saved))
        self.assertEqual(self.window.loadout.game, "anno1800")
        self.assertEqual(self.window.loadout.source_path, before)

    def test_a_loadout_for_the_files_already_open_asks_nothing(self):
        saved = self.save_loadout_for("1800")
        self.asked.clear()
        self.assertTrue(self.window.load_loadout(saved))
        self.assertEqual(self.asked, [])

    def test_and_one_whose_files_have_gone_says_so_instead_of_opening(self):
        saved = self.save_loadout_for("117")
        from anno_modding_studio.core.changes import Loadout

        stray = Loadout.load(saved)
        stray.source_path = str(Path(self._tmp.name) / "vanished" / "assets.xml")
        stray.save(saved)

        self.load("1800")
        self.assertFalse(self.window.load_loadout(saved))
        self.assertIn("Its game files are not there", self.asked)
        self.assertEqual(self.window.loadout.game, "anno1800")

    def said(self):
        """Everything asked or warned of, as one text."""
        return "\n".join(self.texts + self.warned)

    def test_and_it_says_the_other_game_is_open_only_when_it_is(self):
        """"Anno 1800 is open instead" was said of an Anno 1800 loadout too
        (A11-104)."""
        theirs, mine = self.gone_for("117"), self.gone_for("1800")
        self.load("1800")
        self.assertFalse(self.window.load_loadout(theirs))
        self.assertIn("Anno 1800 is open instead", self.warned[-1])

        self.texts.clear()
        self.warned.clear()
        self.answer = False
        self.assertFalse(self.window.load_loadout(mine))
        self.assertNotIn("open instead", self.said())
        self.assertIn("That file is not where it was", self.said())

    def test_and_it_offers_no_way_back_that_does_not_lead_there(self):
        """It said to load the file with Load base assets.xml, which opens
        nothing: the loadout is matched to the open file by the path it was
        saved with, so only the file back at that path opens it (A11-102)."""
        saved = [self.gone_for(game) for game in ("117", "1800")]
        self.load("1800")
        self.answer = False
        for loadout in saved:
            self.texts.clear()
            self.warned.clear()
            self.assertFalse(self.window.load_loadout(loadout))
            self.assertNotIn("Load base assets.xml", self.said())
        # The other game's: only the file back where it was opens it.
        self.assertFalse(self.window.load_loadout(saved[0]))
        self.assertIn("Put that file back where it was", self.warned[-1])

    def test_one_of_the_same_game_whose_file_has_moved_opens_against_the_open_one(self):
        """An 1800 loadout whose 1800 file had moved was refused with 1800
        open, though it opened with nothing open, and the refusal's only
        reason was a switch between games, which this is not (A11-103)."""
        saved = self.gone_for("1800")
        self.load("1800")
        have = self.window.loadout.source_path
        self.asked.clear()
        self.assertTrue(self.window.load_loadout(saved))
        self.assertEqual(self.asked, ["Open it against the file you have open?"])
        self.assertEqual(self.window.loadout_path, saved)
        # It is the open file's loadout now, and says so where it is saved.
        self.assertEqual(self.window.loadout.source_path, have)
        self.assertEqual(self.window.loadout.game, "anno1800")
        self.assertTrue(self.window.dirty)
        self.window.loadout.save(saved)
        self.asked.clear()
        self.assertTrue(self.window.load_loadout(saved))
        self.assertEqual(self.asked, [])

    def test_and_declining_that_leaves_everything_as_it_was(self):
        saved = self.gone_for("1800")
        self.load("1800")
        before = self.window.loadout_path, self.window.loadout.source_path
        self.answer = False
        self.assertFalse(self.window.load_loadout(saved))
        self.assertEqual((self.window.loadout_path, self.window.loadout.source_path), before)

    # -- and the thing that made it invisible --------------------------------
    def test_the_game_is_named_where_it_can_be_read(self):
        """Not only in a file path that gets elided."""
        self.load("117")
        self.assertIn("Anno 117", self.window.source.game_label.text())
        self.assertIn("Anno 117", self.window.windowTitle())
        self.load("1800")
        self.assertIn("Anno 1800", self.window.source.game_label.text())
        self.assertIn("Anno 1800", self.window.windowTitle())


class TestWhoTheStudioThinksYouAre(unittest.TestCase):
    """Authorship decides whether editing a mod is safe to do in place.

    It used to live only in the Creator box on the Generate tab, which meant it
    was blank until somebody found that box -- and blank is what decides whether
    a loaded mod counts as someone else's.  So the Studio could not tell your
    own mods from anyone's, nothing ever asked, and the one thing that did fill
    the field in was a bug: importing a mod copied its author's name onto your
    loadout.
    """

    def setUp(self):
        self.window = MainWindow()
        self.addCleanup(_isolation.forget_settings)
        self.addCleanup(self.window.settings.remove, self.window.preferences.CREATOR_KEY)
        self.window.settings.remove(self.window.preferences.CREATOR_KEY)

    def tearDown(self):
        self.window.editor.search._debounce.stop()
        self.window.close()

    def ask(self, answer, ok=True):
        from PySide6.QtWidgets import QInputDialog

        asked = []
        real = QInputDialog.getText

        def named(parent, title, label, *args, **kwargs):
            asked.append(title)
            return (answer, ok)

        QInputDialog.getText = staticmethod(named)
        self.addCleanup(setattr, QInputDialog, "getText", staticmethod(real))
        return asked

    def test_nobody_is_assumed(self):
        self.assertEqual(self.window.preferences.creator_name(), "")

    def test_asking_once_is_remembered_for_every_loadout_after(self):
        asked = self.ask("Lars")
        self.assertEqual(self.window.preferences.ensure_creator(), "Lars")
        self.assertEqual(asked, ["Who are you?"])

        # And never again, in this loadout or the next.
        self.assertEqual(self.window.preferences.ensure_creator(), "Lars")
        self.assertEqual(len(asked), 1)
        fresh = MainWindow()
        self.addCleanup(_isolation.forget_settings)
        try:
            self.assertEqual(fresh.preferences.creator_name(), "Lars")
            self.assertEqual(fresh.loadout.own.creator, "Lars")
        finally:
            fresh.editor.search._debounce.stop()
            fresh.close()

    def test_declining_leaves_it_unknown_rather_than_guessing(self):
        self.ask("Lars", ok=False)
        self.assertIsNone(self.window.preferences.ensure_creator())
        self.assertEqual(self.window.preferences.creator_name(), "")

    def test_a_blank_answer_is_no_name_and_not_a_cancel(self):
        self.ask("   ")
        self.assertEqual(self.window.preferences.ensure_creator(), "")
        self.assertEqual(self.window.preferences.creator_name(), "")

    def test_cancelling_it_cancels_the_patch_it_was_asked_for(self):
        """The patch was made all the same."""
        from unittest import mock

        from anno_modding_studio.core import patching
        from anno_modding_studio.core.changes import ModLayer
        from anno_modding_studio.ui import patches_controller

        self.window.loadout.set_layer(ModLayer(origin="Their Mod", name="Theirs", creator="Dee"))
        named = (patching.suggest_name("Their Mod"), True)
        with mock.patch.object(patches_controller.QInputDialog, "getText", return_value=named), \
                mock.patch.object(self.window.prompts, "ask_creator_name",
                                  return_value=("Lars", False)) as asked:
            self.window.patches.make_patch("Their Mod")
        asked.assert_called_once()
        self.assertIsNone(self.window.loadout.patch_for("Their Mod"))

    def test_a_new_loadout_starts_out_knowing_it(self):
        self.window.settings.setValue(self.window.preferences.CREATOR_KEY, "Lars")
        self.window.new_loadout()
        self.assertEqual(self.window.loadout.own.creator, "Lars")

    # -- what it is for ----------------------------------------------------
    def test_a_mod_by_somebody_else_is_somebody_elses(self):
        from anno_modding_studio.core import patching
        from anno_modding_studio.core.changes import ModLayer

        self.window.loadout.own.creator = "Lars"
        self.assertFalse(
            patching.is_mine(self.window.loadout, ModLayer(origin="x", creator="Taludas"))
        )

    def test_and_one_by_you_is_yours(self):
        from anno_modding_studio.core import patching
        from anno_modding_studio.core.changes import ModLayer

        self.window.loadout.own.creator = "Lars"
        self.assertTrue(
            patching.looks_mine(self.window.loadout, ModLayer(origin="x", creator="  lars "))
        )

    def test_a_mod_id_you_own_settles_it_without_the_name(self):
        """The Studio writes its own ModID, so a mod that came back out of here
        is identifiable by something it controls rather than a display name."""
        from anno_modding_studio.core import patching
        from anno_modding_studio.core.changes import ModLayer

        self.window.loadout.own.mod_id = "roomier-farmers"
        self.window.loadout.own.creator = ""
        self.assertTrue(
            patching.looks_mine(
                self.window.loadout,
                ModLayer(origin="x", creator="Somebody", mod_id="roomier-farmers"),
            )
        )

    def test_with_nobody_named_nothing_is_taken_for_yours(self):
        """The safe way round: a mod wrongly taken for yours is edited in place
        and overwritten by its author's next release."""
        from anno_modding_studio.core import patching
        from anno_modding_studio.core.changes import ModLayer

        self.assertEqual(self.window.loadout.own.creator, "")
        self.assertFalse(
            patching.is_mine(self.window.loadout, ModLayer(origin="x", creator=""))
        )


class TestPerGameRememberedFolders(unittest.TestCase):
    """The mods folder you last pointed at is not one fact but two -- 1800's
    and 117's are never the same folder -- so remembering only one meant the
    dialog could default to the wrong game's folder with nothing to say so."""

    def setUp(self):
        self.window = MainWindow()
        self.addCleanup(_isolation.forget_settings)

    def tearDown(self):
        self.window.editor.search._debounce.stop()
        self.window.close()

    def test_the_key_carries_the_current_game(self):
        from anno_modding_studio.core.games import ANNO_1800

        self.window.loadout.game = ANNO_1800
        self.assertEqual(
            self.window.mods_key("last_mods_folder"), "last_mods_folder:anno1800"
        )

    def test_a_folder_remembered_for_one_game_is_not_offered_for_the_other(self):
        from anno_modding_studio.core.games import ANNO_117, ANNO_1800

        with tempfile.TemporaryDirectory() as tmp:
            self.window.loadout.game = ANNO_1800
            self.window.settings.setValue(self.window.mods_key("last_mods_folder"), tmp)
            self.assertEqual(
                self.window.remembered(self.window.mods_key("last_mods_folder")), tmp
            )
            self.window.loadout.game = ANNO_117
            self.assertEqual(
                self.window.remembered(self.window.mods_key("last_mods_folder")), ""
            )


class TestUnloadingAMod(unittest.TestCase):
    """Forgetting a loaded mod: menu action, confirmation, and what it never
    touches -- the mod being authored here, which is not one of the loaded
    mods, it is the loadout itself."""

    @classmethod
    def setUpClass(cls):
        sys.path.insert(0, str(ROOT / "tools"))
        import make_sample_assets

        from anno_modding_studio.core import index as index_mod

        cls._tmp = tempfile.TemporaryDirectory()
        tmp = Path(cls._tmp.name)
        cls.assets = tmp / "assets.xml"
        cls.assets.write_text(make_sample_assets.build_assets_xml(20), encoding="utf-8")
        cls.db = tmp / "index.sqlite"
        index_mod.build_index(cls.assets, cls.db)

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def setUp(self):
        self.window = MainWindow()
        self.addCleanup(_isolation.forget_settings)
        self.window.game_files.adopt_index(self.db, self.assets, cached=True)

    def tearDown(self):
        # .close() would run closeEvent() -- and its own unsaved-work prompt,
        # a real blocking dialog several of these tests would otherwise hit,
        # since they deliberately leave authored changes standing.
        # .deleteLater() does not.
        self.window.editor.search._debounce.stop()
        self.window.game_files.stop_texts_worker()
        if self.window.index is not None:
            self.window.index.close()
            self.window.index = None
        self.window.loadout.clear()
        self.window.deleteLater()

    def layer(self, origin, **kwargs):
        from anno_modding_studio.core.changes import ModLayer

        self.window.loadout.set_layer(ModLayer(origin=origin, **kwargs))

    def change(self, origin, authored=True, guid="1", path="/Values/X"):
        from anno_modding_studio.core.changes import Change

        self.window.loadout.set_change(
            Change(
                guid=guid, asset_label="a", path=path, tag="X",
                old_value="1", new_value="2", origin=origin, authored=authored,
            )
        )

    def test_the_menu_action_is_disabled_with_nothing_loaded(self):
        self.assertFalse(self.window.unload_action.isEnabled())

    def test_and_enabled_once_something_is(self):
        self.layer("A Mod")
        self.window.update_title()
        self.assertTrue(self.window.unload_action.isEnabled())

    def test_and_disabled_again_once_it_is_unloaded(self):
        self.layer("A Mod")
        self.window.update_title()
        self.window.unload_mods(["A Mod"])
        self.assertFalse(self.window.unload_action.isEnabled())

    def test_unloading_drops_the_layer_and_its_changes(self):
        self.layer("A Mod")
        self.change("A Mod", authored=False)
        gone = self.window.unload_mods(["A Mod"])
        self.assertEqual(gone, 1)
        self.assertIsNone(self.window.loadout.layer("A Mod"))
        self.assertEqual(self.window.loadout.changes_of("A Mod"), [])

    def test_it_never_touches_your_own_mod(self):
        """Origin "" is not one of the loaded mods -- it is the loadout
        itself -- so it is never in scope for "unload mods", not even by
        naming it explicitly."""
        self.change("", authored=True)
        gone = self.window.unload_mods(["A Mod", ""])
        self.assertEqual(gone, 0)
        self.assertEqual(len(self.window.loadout.authored_of("")), 1)

    def test_confirming_with_nothing_authored_does_not_ask(self):


        self.layer("A Mod")
        self.change("A Mod", authored=False)
        with _isolation.answering(True) as asked:
            ok = self.window.prompts.confirm_unload(["A Mod"], self.window.loadout)
        self.assertFalse(asked.called)
        self.assertTrue(ok)

    def test_confirming_with_authored_edits_warns_about_losing_them(self):


        self.layer("A Mod")
        self.change("A Mod", authored=True)
        with _isolation.answering("Unload") as asked:
            ok = self.window.prompts.confirm_unload(["A Mod"], self.window.loadout)
        self.assertTrue(ok)
        self.assertIn("1 change you made", asked.texts[-1])

    def test_declining_loses_nothing(self):


        self.layer("A Mod")
        self.change("A Mod", authored=True)
        with _isolation.answering(False):
            ok = self.window.prompts.confirm_unload(["A Mod"], self.window.loadout)
        self.assertFalse(ok)
        self.assertIsNotNone(self.window.loadout.layer("A Mod"))

    def patched(self):
        """Their Mod, changed only through its patch (H2-4)."""
        self.layer("Their Mod", name="Their Mod", mod_id="theirs")
        self.layer("Their Mod (my patch)", name="Their Mod (my patch)", patches="Their Mod")
        self.change("Their Mod (my patch)", authored=True)

    def test_a_mod_changed_through_its_patch_is_asked_about(self):
        self.patched()
        with _isolation.answering("Unload") as asked:
            ok = self.window.prompts.confirm_unload(["Their Mod"], self.window.loadout)
        self.assertTrue(ok)
        self.assertIn("Your patch for it stays, with the 1 change in it.", asked.texts[-1])
        self.assertIn("greyed and left out of exports and Play", asked.texts[-1])

    def test_an_op_of_theirs_switched_off_is_counted_by_who_keeps_it(self):
        """R3-26, hands-on run 3: an op switched off inside the mod went
        unsaid, the loadout's own mod's with it, which unloading drops; and
        so did the patch's, which stays (P5)."""
        from anno_modding_studio.core.changes import Op

        self.patched()
        loadout = self.window.loadout
        loadout.set_op(Op(source="<ModOp/>", origin="Their Mod", uid="off-own", order=0, active=False,
                          kept_by=""))
        loadout.set_op(Op(source="<ModOp/>", origin="Their Mod", uid="off-patch", order=1, active=False,
                          kept_by="Their Mod (my patch)"))
        with _isolation.answering("Unload") as asked:
            self.window.prompts.confirm_unload(["Their Mod"], loadout)
        self.assertIn("1 change you made to it would be lost", asked.texts[-1])
        self.assertIn("Your patch for it stays, with the 2 changes in it.", asked.texts[-1])

    def test_and_unloaded_its_patch_is_greyed_and_left_out(self):
        from anno_modding_studio.core import build, patching

        self.patched()
        self.window.unload_mods(["Their Mod"])
        patch = self.window.loadout.layer("Their Mod (my patch)")
        self.assertIsNotNone(patch)
        self.assertTrue(patching.is_inert(self.window.loadout, patch))
        self.assertNotIn(patch.origin, build.writable(self.window.loadout))

    def test_the_dialog_counts_the_changes_in_a_mods_patch(self):
        from anno_modding_studio.ui.unload_dialog import UnloadModsDialog

        self.patched()
        dialog = UnloadModsDialog(self.window.loadout, self.window)
        try:
            row = next(row for row in range(dialog.table.rowCount())
                       if dialog.table.item(row, 0).data(Qt.ItemDataRole.UserRole) == "Their Mod")
            edits = dialog.table.item(row, 3)
            self.assertEqual(edits.text(), "0, and 1 in its patch")
            self.assertIn("in its patch, Their Mod (my patch)", edits.toolTip())
        finally:
            dialog.deleteLater()

    def test_the_dialog_filters_and_ticks_only_what_it_shows(self):
        from anno_modding_studio.ui.unload_dialog import UnloadModsDialog

        self.layer("A Mod")
        self.layer("B Mod")
        dialog = UnloadModsDialog(self.window.loadout, self.window)
        try:
            dialog.filter_box.setText("b mod")
            shown = [row for row in range(dialog.table.rowCount()) if not dialog.table.isRowHidden(row)]
            self.assertEqual(len(shown), 1)
            dialog.tick_box.tick(True)
            self.assertEqual(dialog._ticked(), ["B Mod"])
        finally:
            dialog.deleteLater()

    def test_the_dialog_lists_loaded_mods_and_returns_what_was_ticked(self):
        from anno_modding_studio.ui.unload_dialog import UnloadModsDialog

        self.layer("A Mod")
        self.layer("B Mod")
        dialog = UnloadModsDialog(self.window.loadout, self.window)
        try:
            self.assertEqual(dialog.table.rowCount(), 2)
            dialog.tick_box.tick(True)
            self.assertEqual(sorted(dialog._ticked()), ["A Mod", "B Mod"])
            dialog.table.item(0, 0).setCheckState(Qt.CheckState.Unchecked)
            dialog._accept()
        finally:
            dialog.deleteLater()
        self.assertEqual(len(dialog.chosen), 1)

    def test_unload_all_via_tick_all_and_the_button_text_says_how_many(self):
        from anno_modding_studio.ui.unload_dialog import UnloadModsDialog

        self.layer("A Mod")
        self.layer("B Mod")
        dialog = UnloadModsDialog(self.window.loadout, self.window)
        try:
            self.assertFalse(dialog.unload_button.isEnabled())
            dialog.tick_box.tick(True)
            self.assertTrue(dialog.unload_button.isEnabled())
            self.assertIn("2", dialog.unload_button.text())
        finally:
            dialog.deleteLater()

    def test_the_box_in_the_heading_ticks_them_all_and_says_how_they_stand(self):
        """In place of Tick all and Tick none: a dash for some ticked, and a
        click ticks them all, or unticks them where they all are."""
        from anno_modding_studio.ui.unload_dialog import UnloadModsDialog

        self.layer("A Mod")
        self.layer("B Mod")
        dialog = UnloadModsDialog(self.window.loadout, self.window)
        try:
            box = dialog.tick_box
            self.assertEqual(box.checkState(), Qt.CheckState.Unchecked)
            box.click()
            self.assertEqual(sorted(dialog._ticked()), ["A Mod", "B Mod"])
            self.assertEqual(box.checkState(), Qt.CheckState.Checked)
            dialog.table.item(0, 0).setCheckState(Qt.CheckState.Unchecked)
            self.assertEqual(box.checkState(), Qt.CheckState.PartiallyChecked)
            box.click()
            self.assertEqual(len(dialog._ticked()), 2)
            box.click()
            self.assertEqual(dialog._ticked(), [])
            self.assertEqual(box.checkState(), Qt.CheckState.Unchecked)
            # Over the column of ticks, in the heading.
            self.assertIs(box.parent(), dialog.table.horizontalHeader().viewport())
            self.assertLess(box.x(), dialog.table.columnWidth(0))
        finally:
            dialog.deleteLater()


class TestKeepingModsAndBaseAssetsInLockstep(unittest.TestCase):
    """Loaded mods and every authored change target one game's dialect and
    GUID space.  Anno 117's paths and Anno 1800's do not overlap, so pointing
    the loadout at the other game's assets.xml while either is still standing
    is not a change to shrug off -- it is a new loadout."""

    @classmethod
    def setUpClass(cls):
        sys.path.insert(0, str(ROOT / "tools"))
        import make_sample_assets

        from anno_modding_studio.core import index as index_mod

        cls._tmp = tempfile.TemporaryDirectory()
        tmp = Path(cls._tmp.name)
        cls.assets = tmp / "assets.xml"
        cls.assets.write_text(make_sample_assets.build_assets_xml(20), encoding="utf-8")
        cls.db = tmp / "index.sqlite"
        index_mod.build_index(cls.assets, cls.db)

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def setUp(self):
        from anno_modding_studio.core.games import ANNO_1800

        self.window = MainWindow()
        self.addCleanup(_isolation.forget_settings)
        self.window.game_files.adopt_index(self.db, self.assets, cached=True)
        self.window.loadout.game = ANNO_1800

    def tearDown(self):
        # See TestUnloadingAMod.tearDown -- several of these deliberately
        # leave authored changes standing, which .close() would hit its own
        # real, blocking "unsaved loadout" prompt over.
        self.window.editor.search._debounce.stop()
        self.window.game_files.stop_texts_worker()
        if self.window.index is not None:
            self.window.index.close()
            self.window.index = None
        self.window.loadout.clear()
        self.window.deleteLater()

    def test_the_same_game_is_never_a_switch(self):


        from anno_modding_studio.core.changes import ModLayer
        from anno_modding_studio.core.games import ANNO_1800

        self.window.loadout.set_layer(ModLayer(origin="A Mod"))
        with _isolation.answering(True) as asked:
            ok = self.window.game_files.confirm_game_switch(ANNO_1800, "assets.xml")
        self.assertFalse(asked.called)
        self.assertTrue(ok)

    def test_a_different_game_with_nothing_loaded_is_also_never_a_switch(self):
        """Nothing built against the old game means nothing to protect."""


        from anno_modding_studio.core.games import ANNO_117

        with _isolation.answering(True) as asked:
            ok = self.window.game_files.confirm_game_switch(ANNO_117, "assets.xml")
        self.assertFalse(asked.called)
        self.assertTrue(ok)

    def test_a_different_game_with_mods_loaded_asks_and_then_unloads_them(self):


        from anno_modding_studio.core.changes import ModLayer
        from anno_modding_studio.core.games import ANNO_117

        self.window.loadout.set_layer(ModLayer(origin="A Mod"))
        with _isolation.answering("Switch games") as asked:
            ok = self.window.game_files.confirm_game_switch(ANNO_117, "the117.xml")
        self.assertTrue(ok)
        self.assertTrue(asked.called)
        # Agreed to, not done.  The mods go when the other game's files
        # arrive, so that a build which never arrives leaves them standing.
        self.assertEqual(len(self.window.loadout.layers), 1)
        self.window.game_files.adopt_index(self.db, self.assets, cached=True)
        self.assertEqual(self.window.loadout.layers, [])

    def test_declining_leaves_everything_exactly_as_it_was(self):


        from anno_modding_studio.core.changes import ModLayer
        from anno_modding_studio.core.games import ANNO_117

        self.window.loadout.set_layer(ModLayer(origin="A Mod"))
        with _isolation.answering(False):
            ok = self.window.game_files.confirm_game_switch(ANNO_117, "the117.xml")
        self.assertFalse(ok)
        self.assertEqual(len(self.window.loadout.layers), 1)

    def test_the_remembered_install_folder_is_cleared_on_a_real_switch(self):
        """It names an install for the game being left; offering it as where
        the new game's mods live is the exact bug this whole check exists to
        stop."""


        from anno_modding_studio.core.changes import ModLayer
        from anno_modding_studio.core.games import ANNO_117

        self.window.loadout.set_layer(ModLayer(origin="A Mod"))
        self.window.game_folder = r"C:\games\Anno 1800"
        with _isolation.answering("Switch games"):
            self.window.game_files.confirm_game_switch(ANNO_117, "the117.xml")
        self.assertIsNone(self.window.game_folder)

    def test_unsaved_edits_are_asked_about_too(self):
        """Layered on top of the game-switch question: the same "save first?"
        check starting over runs, asked at the same moment as before even
        though what it agrees to now waits for the files."""
        from anno_modding_studio.core.changes import Change
        from anno_modding_studio.core.games import ANNO_117

        self.window.loadout.set_change(
            Change(guid="1", asset_label="a", path="/Values/X", tag="X",
                   old_value="1", new_value="2", authored=True)
        )
        self.window.dirty = True
        # "Switch games?" is asked by name. "Unsaved loadout" offers Save,
        # Discard and Cancel, which are names already.
        with _isolation.answering(
            lambda title, _text: "Discard" if title == "Unsaved loadout" else "Switch games"
        ):
            ok = self.window.game_files.confirm_game_switch(ANNO_117, "the117.xml")
        self.assertTrue(ok)
        self.assertEqual(self.window.loadout.authored_count, 1)
        self.window.game_files.adopt_index(self.db, self.assets, cached=True)
        self.assertEqual(self.window.loadout.authored_count, 0)


class TestPuttingTheLastGameFilesBack(unittest.TestCase):
    """The fast path, or nothing: no archive scan, no extraction, and an index
    that is already built because the file it was built from has not moved."""

    @classmethod
    def setUpClass(cls):
        sys.path.insert(0, str(ROOT / "tools"))
        import make_sample_assets

        from anno_modding_studio.core import index as index_mod

        cls._tmp = tempfile.TemporaryDirectory()
        tmp = Path(cls._tmp.name)
        cls.root = tmp / "extracted"
        # The shape 1800's files are extracted into: the game is read off it.
        folder = cls.root / "data" / "config" / "export" / "main" / "asset"
        folder.mkdir(parents=True)
        cls.assets = folder / "assets.xml"
        cls.assets.write_text(make_sample_assets.build_assets_xml(20), encoding="utf-8")
        cls.game = tmp / "Anno 1800" / "maindata"
        cls.game.mkdir(parents=True)
        (cls.game / "data0.rda").write_bytes(b"data0")
        # The index that read would have built, already in the cache.
        cls.db = index_mod.cache_path_for(cls.assets)
        index_mod.build_index(cls.assets, cls.db)

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def window(self):
        window = MainWindow()
        self.addCleanup(_isolation.forget_settings)

        def close():
            window.editor.search._debounce.stop()
            window.game_files.stop_texts_worker()
            if window.index is not None:
                window.index.close()
                window.index = None
            window.deleteLater()

        self.addCleanup(close)
        return window

    def remember(self, window):
        from anno_modding_studio.core import games

        gamefiles.record_read(
            self.root,
            game=games.ANNO_1800,
            game_folder=self.game.parent,
            assets=self.assets,
            source_archive="data0.rda",
        )
        key = "%s:%s" % (window.game_files.READ_ROOT_KEY, games.ANNO_1800)
        window.settings.setValue(key, str(self.root))
        window.settings.setValue(window.game_files.READ_GAME_KEY, games.ANNO_1800)
        self.addCleanup(window.settings.remove, key)
        self.addCleanup(window.settings.remove, window.game_files.READ_GAME_KEY)

    def test_the_game_opened_last_is_the_one_a_launch_comes_back_to(self):
        """Not the one extracted last: choosing Anno 1800 on the Start tab
        used to leave every launch on Anno 117."""
        from anno_modding_studio.core import games

        window = self.window()
        self.remember(window)
        window.settings.setValue(window.game_files.READ_GAME_KEY, games.ANNO_117)
        self.assertTrue(window.game_files.restore_last_read(games.ANNO_1800))
        self.assertEqual(window.settings.value(window.game_files.READ_GAME_KEY), games.ANNO_1800)

    def test_a_stamp_of_the_other_game_is_not_this_games_read(self):
        """Two games read into one folder leave the later one's stamp.
        Opened for the other, it was the files already open, and "Use these
        files" loaded their texts again and changed nothing."""
        from anno_modding_studio.core import games

        window = self.window()
        self.remember(window)
        key = "%s:%s" % (window.game_files.READ_ROOT_KEY, games.ANNO_117)
        window.settings.setValue(key, str(self.root))
        self.addCleanup(window.settings.remove, key)
        stamp, state, _why = window.game_files.last_read(games.ANNO_117)
        self.assertIsNone(stamp)
        self.assertEqual(state, gamefiles.READ_GONE)
        self.assertFalse(window.game_files.restore_last_read(games.ANNO_117))
        # Its own is still its own.
        self.assertEqual(window.game_files.last_read(games.ANNO_1800)[0]["game"], games.ANNO_1800)

    def test_texts_that_were_cleared_do_not_come_back_with_the_files(self):
        """The read names the texts taken with it, and putting the files back
        loaded them again whatever had been cleared since (A8-33)."""
        from anno_modding_studio.core import games

        window = self.window()
        texts = self.root / "texts_english.xml"
        texts.write_text("<TextExport/>", encoding="utf-8")
        self.addCleanup(texts.unlink)
        self.remember(window)
        gamefiles.record_read(
            self.root, game=games.ANNO_1800, game_folder=self.game.parent,
            assets=self.assets, texts=texts, source_archive="data0.rda",
        )
        with mock.patch.object(window.game_files, "open_texts") as opened:
            self.assertTrue(window.game_files.restore_last_read(games.ANNO_1800))
            opened.assert_called_once_with(texts)
            opened.reset_mock()
            window.game_files.clear_texts()
            self.assertTrue(window.game_files.restore_last_read(games.ANNO_1800))
        opened.assert_not_called()

    def test_a_read_made_where_the_files_used_to_go_is_still_found(self):
        from anno_modding_studio.core import games

        window = self.window()
        before = gamefiles.former_extract_root(games.ANNO_1800)
        gamefiles.record_read(
            before, game=games.ANNO_1800, game_folder=self.game.parent,
            assets=self.assets, source_archive="data0.rda",
        )
        self.addCleanup(shutil.rmtree, before, True)
        with mock.patch.object(places, "extracted_dir", return_value=self.root / "nowhere yet"):
            stamp, _state, _why = window.game_files.last_read(games.ANNO_1800)
        self.assertEqual(Path(stamp["assets"]), self.assets)

    def test_it_opens_without_reading_the_archives_again(self):
        from anno_modding_studio.ui import import_dialog as import_ui

        window = self.window()
        self.remember(window)

        def refuse(_inner):
            raise AssertionError("it went back to the archives")

        with mock.patch.object(import_ui.ImportDialog, "exec", refuse):
            self.assertTrue(window.game_files.restore_last_read())
        self.assertIsNotNone(window.index)
        self.assertEqual(Path(window.loadout.source_path), self.assets)

    def test_a_fresh_window_puts_them_back_by_itself(self):
        self.remember(self.window())
        again = self.window()
        self.assertIsNotNone(again.index)
        self.assertIn("has not changed", again.status_label.text())

    def test_names_the_index_kept_are_still_counted_when_they_come_back(self):
        """The note was written over the line that counted them, so a launch
        that put names back said nothing of them (A11-67)."""
        from anno_modding_studio.core import index as index_mod

        self.remember(self.window())
        with mock.patch.object(
            index_mod.AssetIndex, "text_count", new_callable=mock.PropertyMock, return_value=7
        ):
            again = self.window()
            said = again.status_label.text()
        self.assertIn("7 localised names", said)
        self.assertIn("has not changed", said)

    def test_but_not_when_the_index_would_have_to_be_rebuilt(self):
        """Minutes of work nobody asked for at startup."""
        self.remember(self.window())
        moved = self.db.with_suffix(".sqlite.moved")
        self.db.rename(moved)
        self.addCleanup(lambda: moved.exists() and moved.rename(self.db))
        self.assertIsNone(self.window().index)

    def test_and_not_when_the_game_has_been_patched_since(self):
        self.remember(self.window())
        patched = self.game / "data1.rda"
        patched.write_bytes(b"a patch")
        self.addCleanup(lambda: patched.unlink(missing_ok=True))
        again = self.window()
        self.assertIsNone(again.index)
        self.assertEqual(again.game_files.last_read()[1], gamefiles.READ_PATCHED)

    def rows(self, window):
        """``(button texts, the text on the rows)`` for the games list."""
        from PySide6.QtWidgets import QLabel, QPushButton

        box = window.source.games_box
        return (
            [button.text() for button in box.findChildren(QPushButton)],
            " ".join(label.text() for label in box.findChildren(QLabel)),
        )

    def test_the_start_tab_offers_the_files_that_are_already_there(self):
        window = self.window()
        self.remember(window)
        window.start.refresh_games()
        buttons, said = self.rows(window)
        self.assertIn("Use these files", buttons)
        self.assertIn("data0.rda", said)
        self.assertIn("has not changed", said)

    def test_both_games_are_listed_so_switching_is_a_click(self):
        """Switching used to be the un-highlighted second button on a card
        that is folded away as soon as anything is loaded."""
        from anno_modding_studio.core import games

        window = self.window()
        self.remember(window)
        window.start.refresh_games()
        _buttons, said = self.rows(window)
        for game in games.GAMES.values():
            self.assertIn(game.label, said)
        self.assertIn("Not read yet", said)

    def test_using_them_loads_them_without_the_dialog(self):
        from PySide6.QtWidgets import QPushButton

        from anno_modding_studio.ui import import_dialog as import_ui

        window = self.window()
        self.remember(window)
        window.start.refresh_games()
        use = next(
            button
            for button in window.source.games_box.findChildren(QPushButton)
            if button.text() == "Use these files"
        )

        def refuse(_inner):
            raise AssertionError("it went back to the archives")

        with mock.patch.object(import_ui.ImportDialog, "exec", refuse):
            use.click()
        self.assertIsNotNone(window.index)

    def test_listing_the_games_again_replaces_the_rows(self):
        """Taken out of the layout is not taken out of the card: deleteLater
        runs next turn, and until it does the old rows are still drawn. Both
        games' rows stayed on screen with the new pair underneath, so
        switching games looked as though no button had changed."""
        from PySide6.QtWidgets import QPushButton

        window = self.window()
        self.remember(window)
        window.start.refresh_games()
        first = len(window.source.games_box.findChildren(QPushButton))
        window.start.refresh_games()
        window.start.refresh_games()
        self.assertEqual(
            len(window.source.games_box.findChildren(QPushButton)), first
        )

    def test_the_game_in_use_says_so_and_offers_no_button_of_its_own(self):
        from PySide6.QtWidgets import QPushButton

        from anno_modding_studio.core import games

        window = self.window()
        self.remember(window)
        window.game_files.restore_last_read(games.ANNO_1800)
        buttons = [
            (b.text(), b.objectName())
            for b in window.source.games_box.findChildren(QPushButton)
        ]
        self.assertEqual(len(buttons), len(games.GAMES))
        self.assertIn(("Read from game files…", ""), buttons)
        _rows, said = self.rows(window)
        self.assertIn("in use", said)

    def test_and_the_cards_own_button_stops_being_the_highlighted_one(self):
        """With files loaded it is the way to a *different* installation, not
        the thing to do next."""
        window = self.window()
        self.remember(window)
        window.start.refresh_games()
        self.assertEqual(window.source.import_button.objectName(), "")
        self.assertEqual(window.source.import_button.text(), "Read other game files…")

    def test_and_says_plainly_when_the_game_has_moved_on(self):
        window = self.window()
        self.remember(window)
        patched = self.game / "data2.rda"
        patched.write_bytes(b"a patch")
        self.addCleanup(lambda: patched.unlink(missing_ok=True))
        window.start.refresh_games()
        buttons, said = self.rows(window)
        self.assertNotIn("Use these files", buttons)
        self.assertIn("Read from game files…", buttons)
        self.assertIn("have changed since", said)


class StartTabCase(unittest.TestCase):
    """A window with the game files loaded and nothing else settled."""

    @classmethod
    def setUpClass(cls):
        sys.path.insert(0, str(ROOT / "tools"))
        import make_sample_assets

        from anno_modding_studio.core import index as index_mod

        cls._tmp = tempfile.TemporaryDirectory()
        tmp = Path(cls._tmp.name)
        cls.assets = tmp / "assets.xml"
        cls.assets.write_text(make_sample_assets.build_assets_xml(20), encoding="utf-8")
        cls.db = tmp / "index.sqlite"
        index_mod.build_index(cls.assets, cls.db)

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def setUp(self):
        self.window = MainWindow()
        self.addCleanup(_isolation.forget_settings)
        self.window.game_files.adopt_index(self.db, self.assets, cached=True)

    def tearDown(self):
        self.window.editor.search._debounce.stop()
        self.window.game_files.stop_texts_worker()
        if self.window.index is not None:
            self.window.index.close()
            self.window.index = None
        self.window.loadout.clear()
        self.window.deleteLater()


class TestTheOtherTabsWaitForTheStartTab(StartTabCase):
    """Loading an assets.xml and clicking straight into the editor landed you
    somewhere that looks like it works and does nothing: no loadout, no mod to
    write into, no way of working chosen."""

    def shut(self):
        return [
            tab
            for tab in (TAB_EDIT, TAB_REFS, TAB_GENERATE)
            if not self.window.tabs.isTabEnabled(tab)
        ]

    def test_the_start_tab_is_drawn_before_anything_happens(self):
        """All of it was built by a refresh nothing called on a cold start, so
        a fresh window showed no games and no creator line at all."""
        from PySide6.QtWidgets import QPushButton

        from anno_modding_studio.core import games

        fresh = MainWindow()
        self.addCleanup(_isolation.forget_settings)
        self.addCleanup(fresh.deleteLater)
        self.addCleanup(fresh.game_files.stop_texts_worker)
        box = fresh.source.games_box
        self.assertTrue(box.findChildren(QPushButton), "no games listed")
        said = " ".join(
            label.text() for label in box.findChildren(type(fresh.source.creator_label))
        )
        for game in games.GAMES.values():
            self.assertIn(game.label, said)
        self.assertTrue(fresh.source.creator_label.text())

    def test_the_files_alone_do_not_open_them(self):
        self.assertEqual(self.shut(), [TAB_EDIT, TAB_REFS, TAB_GENERATE])

    def test_and_they_say_what_is_missing(self):
        tip = self.window.tabs.tabToolTip(TAB_EDIT)
        self.assertIn("what you are here to do", tip)

    def test_choosing_a_way_of_working_opens_them(self):
        from anno_modding_studio.core.changes import AIM_MAKE

        name_the_mod(self)
        self.window.start.choose_aim(AIM_MAKE)
        self.assertEqual(self.shut(), [])
        self.assertEqual(self.window.tabs.tabToolTip(TAB_EDIT), "")

    def test_a_loadout_that_already_holds_work_answers_it_by_itself(self):
        """Opening one is answering the question -- the work in it says what
        it is for."""
        from anno_modding_studio.core.changes import Change

        self.window.loadout.set_change(
            Change(guid="1010030", asset_label="a house",
                   path="/Values/Residence7/Residents", tag="Residents",
                   old_value="8", new_value="12")
        )
        self.window.update_tab_gates()
        self.assertEqual(self.shut(), [])

    def test_and_so_does_a_mods_folder_read_in(self):
        from anno_modding_studio.core.changes import ModLayer

        self.window.loadout.set_layer(ModLayer(origin="Somebody Else"))
        self.window.update_tab_gates()
        self.assertEqual(self.shut(), [])


class TestTheLoadoutCardFoldsAwayToo(StartTabCase):
    """A loadout you are already in is a fact, not a question."""

    def test_it_is_open_while_there_is_no_loadout(self):
        self.window.source.set_loadout(None, False)
        self.assertFalse(self.window.source.loadout_step.body.isHidden())
        self.assertTrue(self.window.source.loadout_toggle.isHidden())

    def test_and_folds_once_there_is_one(self):
        self.window.source.set_loadout(r"C:\p\Better Harbours.annoload", True)
        self.assertTrue(self.window.source.loadout_step.body.isHidden())
        self.assertEqual(self.window.source.loadout_toggle.text(), "Change…")

    def test_which_change_opens_again(self):
        self.window.source.set_loadout(r"C:\p\Better Harbours.annoload", True)
        self.window.source.loadout_toggle.click()
        self.assertFalse(self.window.source.loadout_step.body.isHidden())
        self.assertEqual(self.window.source.loadout_toggle.text(), "Hide")

    def test_the_header_carries_the_loadout_and_who_it_is_by(self):
        """The two things worth having on screen once the card is folded."""
        self.window.source.set_creator("Lars")
        self.window.source.set_loadout(r"C:\p\Better Harbours.annoload", True)
        said = self.window.source.loadout_step.status.full_text()
        self.assertIn("Better Harbours", said)
        self.assertIn("as Lars", said)

    def test_and_says_nothing_about_a_creator_with_no_loadout(self):
        self.window.source.set_creator("Lars")
        self.window.source.set_loadout(None, False)
        self.assertEqual(self.window.source.loadout_step.status.full_text(), "None yet")


class _StubIndex:
    """Enough of an index for the Start tab to draw itself.

    Restoring a loadout refreshes that tab, which asks the index how much it
    holds and which file it came from -- so "something is loaded" has to be
    able to answer both, not merely exist.
    """

    asset_count = 0
    text_count = 0
    source_path = Path("C:/anno1800/data/config/export/main/asset/assets.xml")


class TestComingBackToTheLoadoutYouWereIn(unittest.TestCase):
    """The game files come back on their own; the loadout does not.

    Reading the files is a cache, with nothing to decide.  Which loadout to be
    in is the decision the Start tab exists to ask, and a new session asks it
    again -- with the ones you had open lately one click away on that tab.  It
    used to open the last one for you, on the same argument as the files.
    """

    def setUp(self):
        from anno_modding_studio.core import places

        self.home = tempfile.mkdtemp(prefix="ams-back-")
        self.addCleanup(shutil.rmtree, self.home, True)
        patcher = mock.patch.dict(os.environ, {places.ENV_HOME: self.home})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.folder = Path(self.home) / "loadouts"
        self.folder.mkdir(parents=True)

    def loadout(self, name, game, source=""):
        import json

        path = self.folder / (name + ".annoload")
        path.write_text(
            json.dumps(
                {"format": 1, "mod_name": name, "game": game,
                 "source_path": source, "changes": []}
            ),
            encoding="utf-8",
        )
        return path

    def window(self):
        window = MainWindow()
        self.addCleanup(_isolation.forget_settings)

        def close():
            window.editor.search._debounce.stop()
            window.game_files.stop_texts_worker()
            if window.index is not None:
                window.index.close()
                window.index = None
            window.deleteLater()

        self.addCleanup(close)
        return window

    def test_the_last_loadout_is_offered_not_opened(self):
        from anno_modding_studio.core import gamefiles, games
        from anno_modding_studio.core import index as index_mod

        wanted = self.loadout("Better Harbours", games.ANNO_1800)
        window = self.window()
        window.loadout.game = games.ANNO_1800
        stamp = {"assets": str(wanted), "game": games.ANNO_1800}

        def came_back(game=""):
            window.index = _StubIndex()
            return True

        with mock.patch.object(
            window.game_files, "last_read", return_value=(stamp, gamefiles.READ_CURRENT, "")
        ), mock.patch.object(
            index_mod, "cache_path_for", return_value=wanted
        ), mock.patch.object(window.game_files, "restore_last_read", side_effect=came_back):
            try:
                window.game_files.restore_on_launch()
            finally:
                window.index = None
        self.assertIsNone(window.loadout_path)
        self.assertEqual(window.files.recent_loadouts(games.ANNO_1800), [wanted])


class TestWhoYouPublishAs(StartTabCase):
    """Asked once, at the moment it first matters -- which is the worst moment
    to answer it: pre-filled with a Windows username, before anybody has seen
    what it decides."""

    def setUp(self):
        super().setUp()
        self.window.settings.setValue(self.window.preferences.CREATOR_KEY, "Old Name")
        self.addCleanup(self.window.settings.remove, self.window.preferences.CREATOR_KEY)
        self.window.start.refresh_source_panel()

    def test_the_start_tab_says_who_you_are(self):
        self.assertEqual(self.window.source.creator_label.text(), "Old Name")

    def test_and_offers_to_change_it(self):
        with mock.patch.object(
            QInputDialog, "getText", return_value=("New Name", True)
        ):
            self.assertTrue(self.window.preferences.change_creator())
        self.assertEqual(self.window.preferences.creator_name(), "New Name")
        self.assertEqual(self.window.source.creator_label.text(), "New Name")

    def test_the_open_loadout_follows_along_if_it_wore_the_old_name(self):
        self.window.loadout.own.creator = "Old Name"
        with mock.patch.object(
            QInputDialog, "getText", return_value=("New Name", True)
        ):
            self.window.preferences.change_creator()
        self.assertEqual(self.window.loadout.own.creator, "New Name")

    def test_but_one_published_under_something_else_is_left_alone(self):
        self.window.loadout.own.creator = "A Shared Account"
        with mock.patch.object(
            QInputDialog, "getText", return_value=("New Name", True)
        ):
            self.window.preferences.change_creator()
        self.assertEqual(self.window.loadout.own.creator, "A Shared Account")

    def test_cancelling_changes_nothing(self):
        with mock.patch.object(QInputDialog, "getText", return_value=("", False)):
            self.assertFalse(self.window.preferences.change_creator())
        self.assertEqual(self.window.preferences.creator_name(), "Old Name")

    def test_it_is_in_the_menu_bar_too(self):
        """Not as an entry of its own any more: it is a field in Settings,
        beside the rest of what the Studio remembers about you."""
        from anno_modding_studio.ui.settings_dialog import SettingsDialog

        found = [
            action.text()
            for menu in self.window.menuBar().findChildren(QMenu)
            for action in menu.actions()
            if action.text() == "&Settings…"
        ]
        self.assertTrue(found, "no menu entry for the settings")

        dialog = SettingsDialog(self.window, self.window, store=no_credentials())
        self.addCleanup(dialog.deleteLater)
        self.assertEqual(dialog.creator_edit.text(), "Old Name")

    def test_and_the_name_typed_there_lands_where_the_prompt_lands(self):
        from anno_modding_studio.ui.settings_dialog import SettingsDialog

        self.window.loadout.own.creator = "Old Name"
        dialog = SettingsDialog(self.window, self.window, store=no_credentials())
        self.addCleanup(dialog.deleteLater)
        dialog.creator_edit.setText("New Name")
        # Closed with the caret still in the field, which raises no
        # editingFinished of its own.
        dialog.done(0)
        self.assertEqual(self.window.preferences.creator_name(), "New Name")
        self.assertEqual(self.window.loadout.own.creator, "New Name")
        self.assertEqual(self.window.source.creator_label.text(), "New Name")


class TestWhereTheModImportDialogStarts(StartTabCase):
    """You are there to choose *which* mod, so it opens on the folder the mods
    are in -- and a folder that is itself a mod is read rather than merely
    filled in with the Import button dead."""

    def setUp(self):
        super().setUp()
        self.mods = Path(tempfile.mkdtemp(prefix="ams-mods-"))
        self.addCleanup(shutil.rmtree, self.mods, True)
        self.mod = self.mods / "[Gameplay] A Mod"
        patch = self.mod / "data/config/export/main/asset/assets.xml"
        patch.parent.mkdir(parents=True)
        patch.write_text(
            '<ModOps><ModOp Type="merge" GUID="1010030" Path="/Values/Residence7">'
            "<Residents>12</Residents></ModOp></ModOps>",
            encoding="utf-8",
        )
        (self.mod / "modinfo.json").write_text(
            '{"ModName": {"English": "A Mod"}, "CreatorName": "Someone"}',
            encoding="utf-8",
        )

    def test_a_mod_folder_is_told_from_the_folder_mods_live_in(self):
        from anno_modding_studio.ui.mod_import_dialog import ModImportDialog

        self.assertTrue(ModImportDialog._is_mod_folder(self.mod))
        self.assertFalse(ModImportDialog._is_mod_folder(self.mods))
        self.assertFalse(ModImportDialog._is_mod_folder(""))

    def test_one_it_opens_on_is_read_straight_away(self):
        from anno_modding_studio.ui.mod_import_dialog import ModImportDialog

        dialog = ModImportDialog(self.window.index, self.window, str(self.mod))
        self.addCleanup(dialog.deleteLater)
        self.assertIsNotNone(dialog.report, "the mod was filled in but not read")
        self.assertTrue(dialog.import_button.isEnabled())

    def test_the_folder_mods_live_in_is_not_read_as_a_mod(self):
        from anno_modding_studio.ui.mod_import_dialog import ModImportDialog

        dialog = ModImportDialog(self.window.index, self.window, str(self.mods))
        self.addCleanup(dialog.deleteLater)
        self.assertIsNone(dialog.report)

    def test_it_lists_the_mods_in_there_instead_of_making_you_browse(self):
        """There is no reason to open this dialog and not want to see them."""
        from anno_modding_studio.ui.mod_import_dialog import ModImportDialog

        dialog = ModImportDialog(self.window.index, self.window, str(self.mods))
        self.addCleanup(dialog.deleteLater)
        self.assertEqual(dialog.installed.count(), 1)
        self.assertIn("A Mod", dialog.installed.item(0).text())
        self.assertIn("1 mod in", dialog.installed_label.text())

    def test_and_picking_one_reads_it(self):
        from anno_modding_studio.ui.mod_import_dialog import ModImportDialog

        dialog = ModImportDialog(self.window.index, self.window, str(self.mods))
        self.addCleanup(dialog.deleteLater)
        dialog.installed.itemClicked.emit(dialog.installed.item(0))
        self.assertIsNotNone(dialog.report)
        self.assertEqual(Path(dialog.folder_box.text()), self.mod)
        self.assertTrue(dialog.import_button.isEnabled())

    def test_the_mod_read_is_the_one_highlighted(self):
        """R3-31, hands-on run 3: a path typed in was read, and another mod
        of the list stayed highlighted."""
        import shutil
        import tempfile

        from anno_modding_studio.ui.mod_import_dialog import ModImportDialog

        dialog = ModImportDialog(self.window.index, self.window, str(self.mods))
        self.addCleanup(dialog.deleteLater)
        listed = dialog.installed.item(0)
        dialog.installed.setCurrentItem(listed)
        elsewhere = Path(tempfile.mkdtemp()) / "Elsewhere"
        self.addCleanup(shutil.rmtree, elsewhere.parent, True)
        shutil.copytree(self.mod, elsewhere)
        dialog.folder_box.setText(str(elsewhere))
        dialog.reload()
        self.assertIsNone(dialog.installed.currentItem())
        dialog.folder_box.setText(str(self.mod))
        dialog.reload()
        self.assertIs(dialog.installed.currentItem(), listed)

    def test_and_that_is_where_it_opens(self):
        """Not the mod read last time: choosing is the point of being here."""
        from anno_modding_studio.ui import mod_import_dialog as import_ui

        # A game folder of the test's own: the mods folder's parent is the
        # machine's temporary folder, and its "mods" every run's.
        game = Path(tempfile.mkdtemp(prefix="ams-game-"))
        self.addCleanup(shutil.rmtree, game, True)
        self.window.game_folder = game
        (game / "mods").mkdir()
        self.window.settings.setValue(
            self.window.mods_key("last_mod_import_dir"), str(self.mod)
        )
        self.addCleanup(
            self.window.settings.remove, self.window.mods_key("last_mod_import_dir")
        )
        seen = {}

        class Driven(import_ui.ModImportDialog):
            def exec(inner):
                seen["start"] = inner.folder_box.text()
                return 0

        with mock.patch.object(import_ui.ModImportDialog, "exec", Driven.exec):
            self.window.import_mod()
        self.assertEqual(Path(seen["start"]), game / "mods")

    def test_and_it_is_the_folder_the_library_links_into(self):
        """It opened on the game's own mods folder first, which is another
        folder for a loadout whose mods are elsewhere: the library's
        switched-off mods link into that one, so none of them was offered."""
        from anno_modding_studio.core import library
        from anno_modding_studio.ui import mod_import_dialog as import_ui

        game = Path(tempfile.mkdtemp(prefix="ams-game-"))
        self.addCleanup(shutil.rmtree, game, True)
        (game / "mods" / "GameMod" / "data").mkdir(parents=True)
        self.window.game_folder = game
        self.window.loadout.mods_folder = str(self.mods)
        self.assertEqual(Path(self.window.where.mods_folder()), self.mods)
        shelf = game / "library"
        patched = mock.patch.object(self.window.where, "library_dir", return_value=shelf)
        patched.start()
        self.addCleanup(patched.stop)
        (self.mods / "Shelved" / "data").mkdir(parents=True)
        library.unlink(shelf, library.adopt(shelf, self.mods / "Shelved").link)
        seen = {}

        class Driven(import_ui.ModImportDialog):
            def exec(inner):
                seen["start"] = inner.folder_box.text()
                seen["listed"] = [inner.installed.item(i).text() for i in range(inner.installed.count())]
                return 0

        with mock.patch.object(import_ui.ModImportDialog, "exec", Driven.exec):
            self.window.import_mod()
        self.assertEqual(Path(seen["start"]), self.mods)
        self.assertEqual(seen["listed"][-2:], ["In the library, switched off", "Shelved"])


class TestWhereTheStudioKeepsWhatItMakes(unittest.TestCase):
    """Everything written goes under one home, and it answers to a variable.

    Not a nicety: loadouts are written by ordinary use, so the suite driving
    that path wrote real loadout files into the developer's own Documents --
    fourteen hundred of them, on the machine this was found on.
    """

    def test_and_the_places_that_write_use_it(self):
        from anno_modding_studio.core import backup, gamefiles, games, places

        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.dict(os.environ, {places.ENV_HOME: tmp}):
                window = MainWindow()
                self.addCleanup(_isolation.forget_settings)
                self.addCleanup(window.deleteLater)
                for folder in (
                    window.files.loadouts_dir(),
                    backup.home(),
                    window.output.export_dir(),
                    gamefiles.default_extract_root(games.ANNO_1800),
                ):
                    self.assertTrue(
                        str(folder).startswith(tmp), "%s escaped the home" % folder
                    )

class TestTheLoadoutsYouWorkedOnLately(unittest.TestCase):
    """The list used to be whatever the settings remembered, and nothing else."""

    def setUp(self):
        from anno_modding_studio.core import places

        self.home = tempfile.mkdtemp(prefix="ams-recents-")
        self.addCleanup(shutil.rmtree, self.home, True)
        patcher = mock.patch.dict(os.environ, {places.ENV_HOME: self.home})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.window = MainWindow()
        self.addCleanup(_isolation.forget_settings)
        self.addCleanup(self.window.deleteLater)
        self.folder = Path(self.home) / "loadouts"
        self.folder.mkdir(parents=True)

    def loadout(self, name: str, game: str, ago: float = 0.0) -> Path:
        import json
        import time

        path = self.folder / (name + ".annoload")
        path.write_text(
            json.dumps({"format": 1, "mod_name": name, "game": game, "changes": []}),
            encoding="utf-8",
        )
        when = time.time() - ago
        os.utime(path, (when, when))
        return path

    def test_they_are_read_off_the_folder_not_only_the_settings(self):
        """A cleared settings file used to empty the list beside a folder with
        a hundred loadouts in it."""
        from anno_modding_studio.core import games

        wanted = self.loadout("Harbours", games.ANNO_1800)
        self.assertEqual(
            self.window.files.recent_loadouts(games.ANNO_1800), [wanted]
        )

    def test_newest_first_and_only_eight_of_them(self):
        from anno_modding_studio.core import games

        for number in range(11):
            self.loadout("Mod %d" % number, games.ANNO_1800, ago=number * 60)
        found = [path.name for path in self.window.files.recent_loadouts(games.ANNO_1800)]
        self.assertEqual(len(found), 8)
        self.assertEqual(found[0], "Mod 0.annoload")
        self.assertEqual(found[-1], "Mod 7.annoload")

    def test_only_the_game_whose_files_are_loaded(self):
        """A loadout built against the other game's assets cannot be opened
        without swapping those files out from under everything."""
        from anno_modding_studio.core import games

        mine = self.loadout("Eighteen", games.ANNO_1800)
        self.loadout("Seventeen", games.ANNO_117)
        self.assertEqual(self.window.files.recent_loadouts(games.ANNO_1800), [mine])

    def test_and_all_of_them_when_no_game_is_loaded_yet(self):
        from anno_modding_studio.core import games

        self.loadout("Eighteen", games.ANNO_1800)
        self.loadout("Seventeen", games.ANNO_117)
        self.assertEqual(len(self.window.files.recent_loadouts()), 2)

    def test_one_that_has_gone_is_not_offered(self):
        from anno_modding_studio.core import games

        path = self.loadout("Gone", games.ANNO_1800)
        self.window.files.remember_recent(path)
        path.unlink()
        self.assertEqual(self.window.files.recent_loadouts(games.ANNO_1800), [])

    def remembered(self) -> list:
        raw = self.window.settings.value(self.window.files.RECENT_KEY) or []
        return [raw] if isinstance(raw, str) else list(raw)

    def test_as_many_are_remembered_as_the_settings_are_to_hold(self):
        """It was built from the five that were shown, so it never held more
        than six and the eight it was cut to were never reached."""
        from anno_modding_studio.core import games

        most = self.window.files.RECENT_MAX
        for number in range(most + 2):
            self.window.files.remember_recent(
                self.loadout("Mod %d" % number, games.ANNO_1800, ago=900 - number * 60)
            )
        held = self.remembered()
        self.assertEqual(len(held), most)
        self.assertEqual(Path(held[0]).name, "Mod %d.annoload" % (most + 1))
        self.assertEqual(Path(held[-1]).name, "Mod 2.annoload")

    def test_one_kept_outside_the_loadouts_folder_outlasts_five_newer_ones(self):
        """Nothing but the settings knows of it, and five loadouts saved in
        the folder after it took it out of them for good."""
        from anno_modding_studio.core import games

        elsewhere = Path(self.home) / "elsewhere"
        elsewhere.mkdir()
        outside = elsewhere / "Outside.annoload"
        # Moved with its date, which is older than every one that follows.
        shutil.move(self.loadout("Outside", games.ANNO_1800, ago=900), outside)
        self.window.files.remember_recent(outside)
        newer = [
            self.loadout("Mod %d" % number, games.ANNO_1800, ago=number * 60)
            for number in range(5)
        ]
        for path in reversed(newer):
            self.window.files.remember_recent(path)
        self.assertIn(str(outside), self.remembered())
        for path in newer[:2]:
            path.unlink()
        self.assertIn(outside, self.window.files.recent_loadouts(games.ANNO_1800))

    def test_and_one_that_has_gone_is_forgotten_and_nothing_else_is(self):
        """Opening one that has gone wrote the five that are shown over
        everything the settings held."""
        from anno_modding_studio.core import games

        paths = [
            self.loadout("Mod %d" % number, games.ANNO_1800, ago=number * 60)
            for number in range(7)
        ]
        for path in reversed(paths):
            self.window.files.remember_recent(path)
        paths[0].unlink()
        with mock.patch("anno_modding_studio.ui.loadout_files.QMessageBox.information"):
            self.assertFalse(self.window.files.open_recent(paths[0]))
        self.assertEqual(self.remembered(), [str(path) for path in paths[1:]])

    def test_the_loadout_the_game_loads_comes_first_and_says_so(self):
        """A conversion writes a loadout per profile after making the one you
        play live, and those pushed it off the list."""
        from anno_modding_studio.core import games, library

        live = self.loadout("What I play", games.ANNO_1800, ago=600)
        for number in range(9):
            self.loadout("Profile %d" % number, games.ANNO_1800, ago=number)
        held = library.game_dir(games.ANNO_1800)
        library.set_live(held, live)
        self.addCleanup(library.clear_live, held)
        found = self.window.files.recent_loadouts(games.ANNO_1800)
        self.assertEqual((found[0], len(found)), (live, 8))
        self.window.source.set_loadout(None, False, found, live=str(live))
        self.assertEqual(self.window.source.recent_list.item(0).text(),
                         "What I play: what the game loads")

    def test_the_list_says_which_game_it_is_for(self):
        from anno_modding_studio.core import games

        self.loadout("Harbours", games.ANNO_1800)
        self.window.source.set_loadout(
            None, False, self.window.files.recent_loadouts(games.ANNO_1800),
            game=games.get(games.ANNO_1800).label,
        )
        self.assertIn("Anno 1800", self.window.source.recent_label.text())


class TestTheStartPageReadsAsAPage(unittest.TestCase):
    """The one tab that is prose rather than a working surface."""

    def setUp(self):
        from anno_modding_studio.ui.source_panel import SourcePanel

        self.panel = SourcePanel()
        self.addCleanup(self.panel.deleteLater)

    def label(self, starting):
        from PySide6.QtWidgets import QLabel

        for label in self.panel.findChildren(QLabel):
            if label.text().startswith(starting):
                return label
        self.fail("no label starting %r" % starting)

    def test_the_intro_is_held_to_a_reading_width(self):
        """It once ran the full width of the window."""
        from anno_modding_studio.ui import source_panel, theme

        intro = self.label("The Studio reads and writes Anno mods")
        self.assertEqual(intro.maximumWidth(), source_panel.PAGE_EMS * theme.FONT_PX)

    def test_each_step_is_a_numbered_card_with_an_answer_in_its_header(self):
        from PySide6.QtWidgets import QLabel

        from anno_modding_studio.ui.source_panel import StepCard

        steps = self.panel.findChildren(StepCard)
        self.assertEqual(sorted(step.badge.text() for step in steps), ["1", "2", "3", "4"])
        # The fourth -- what the game will load -- has nothing to say until
        # there is a loadout and a folder to compare, and is not shown before.
        self.assertTrue(self.panel.truth_step.isHidden())
        self.assertEqual(self.panel.files_step.status.full_text(), "Not loaded yet")
        self.assertEqual(self.panel.loadout_step.status.full_text(), "None yet")
        # The square title bars are gone from this page.
        self.assertFalse(
            [l for l in self.panel.findChildren(QLabel) if l.objectName() == "PanelTitle"]
        )

    def test_the_ways_of_working_live_in_the_second_step(self):
        for key, card in self.panel.ways.items():
            self.assertTrue(self.panel.ways_step.isAncestorOf(card), key)

    # -- one column or two ----------------------------------------------------
    def at(self, width):
        from PySide6.QtCore import QPoint

        self.panel.resize(width, 900)
        self.panel.show()
        # Wrapped text settles on a second pass: laid out, measured at the
        # width it got, then laid out again at the height that needs.
        for _ in range(3):
            QApplication.processEvents()
        return (
            self.panel._setup.mapTo(self.panel, QPoint(0, 0)),
            self.panel.loadout_step.mapTo(self.panel, QPoint(0, 0)),
        )

    def test_a_narrow_window_reads_top_to_bottom(self):
        setup, choice = self.at(1000)
        self.assertEqual(setup.x(), choice.x())
        self.assertGreater(choice.y(), setup.y())

    def test_a_wide_one_puts_the_loadout_beside_the_setting_up(self):
        setup, choice = self.at(2400)
        self.assertEqual(setup.y(), choice.y())
        self.assertGreater(choice.x(), setup.x())

    def test_side_by_side_no_paragraph_is_cut_short(self):
        """Aligning the columns to the top of their cells sized them from the
        cards' unwrapped height, and the first card lost its last lines."""
        from PySide6.QtWidgets import QLabel

        for width in (2400, 1000):
            self.at(width)
            for key, way in self.panel.ways.items():
                for label in way.findChildren(QLabel):
                    if label.wordWrap() and label.isVisible() and label.text():
                        with self.subTest(width=width, way=key, text=label.text()[:30]):
                            self.assertGreaterEqual(
                                label.height(), label.heightForWidth(label.width())
                            )

    def test_and_goes_back_to_one_column_when_narrowed_again(self):
        self.at(2400)
        setup, choice = self.at(1000)
        self.assertEqual(setup.x(), choice.x())

    # -- game files fold away -------------------------------------------------
    def loaded(self, where=r"C:\nowhere\assets.xml", texts=r"C:\nowhere\texts_english.xml"):
        from anno_modding_studio.core import games

        self.panel.set_state(where, 25432, texts, 1000, games.ANNO_117)

    def test_names_the_index_has_are_loaded_whether_or_not_their_file_is_known(self):
        """An index from the cache has its names and no texts file waiting.
        Going by the file, the card said "None" with Clear switched off while
        the status bar counted the names."""
        from anno_modding_studio.core import games

        self.panel.set_state(r"C:\nowhere\assets.xml", 25432, None, 17, games.ANNO_117,
                             language="german")
        self.assertEqual(self.panel.texts_label.full_text(),
                         "Loaded earlier and kept with the game files.")
        self.assertEqual(self.panel.texts_detail.text(), "17 in-game names loaded")
        self.assertTrue(self.panel.texts_clear.isEnabled())
        self.assertIn("German names", self.panel.files_step.status.full_text())
        # And with none in the index, a file's path alone loads none.
        self.panel.set_state(r"C:\nowhere\assets.xml", 25432, r"C:\nowhere\texts_german.xml", 0,
                             games.ANNO_117)
        self.assertEqual(self.panel.texts_label.full_text(),
                         "None. Assets show their internal names.")
        self.assertFalse(self.panel.texts_clear.isEnabled())
        self.assertNotIn("names", self.panel.files_step.status.full_text())

    def test_before_anything_is_loaded_the_files_step_is_open(self):
        self.assertFalse(self.panel.files_step.body.isHidden())
        self.assertTrue(self.panel.files_toggle.isHidden())
        self.assertEqual(self.panel.files_step.status.property("state"), "todo")

    def test_once_loaded_it_folds_to_a_header_that_says_what_is_loaded(self):
        self.loaded()
        step = self.panel.files_step
        self.assertTrue(step.body.isHidden())
        for bit in ("Anno 117", "25,432 assets", "English names"):
            self.assertIn(bit, step.status.full_text())
        self.assertEqual(step.status.property("state"), "ok")
        self.assertEqual(self.panel.files_toggle.text(), "Change…")

    def test_change_opens_it_again_and_new_files_fold_it_back(self):
        self.loaded()
        self.panel.files_toggle.click()
        self.assertFalse(self.panel.files_step.body.isHidden())
        self.assertEqual(self.panel.files_toggle.text(), "Hide")
        # The same files again -- a refresh -- leaves it as it was left.
        self.loaded()
        self.assertFalse(self.panel.files_step.body.isHidden())
        self.loaded(where=r"C:\elsewhere\assets.xml")
        self.assertTrue(self.panel.files_step.body.isHidden())

    # -- the other two headers ------------------------------------------------
    def test_the_loadout_header_names_the_loadout(self):
        self.panel.set_loadout(r"C:\loadouts\Better Harbours.annoload", True)
        self.assertEqual(self.panel.loadout_step.status.full_text(), "Better Harbours")
        self.panel.set_loadout(r"C:\loadouts\Better Harbours.annoload", False)
        self.assertEqual(self.panel.loadout_step.status.full_text(), "Better Harbours (new)")

    def test_the_third_header_says_which_way_set_the_loadout_up(self):
        """A recollection of the click -- not "Changing a downloaded mod",
        which claimed the loadout was still doing that."""
        from anno_modding_studio.core.changes import AIM_MAKE, AIM_TWEAK

        self.panel.set_aim("", {}, ready=True)
        self.assertEqual(self.panel.ways_step.status.full_text(), "Not chosen yet")
        self.panel.set_aim(AIM_TWEAK, {}, ready=True)
        self.assertEqual(
            self.panel.ways_step.status.full_text(),
            "Set up as “Change a mod you downloaded”",
        )
        self.panel.set_aim(AIM_TWEAK, {}, ready=True, pending=AIM_MAKE)
        self.assertEqual(self.panel.ways_step.status.full_text(), "Setting up…")

    # -- the way back to work -------------------------------------------------
    def test_the_way_in_use_carries_the_way_back_to_the_editor(self):
        from anno_modding_studio.core.changes import AIM_MAKE

        self.panel.set_aim(AIM_MAKE, {}, ready=True)
        for key, way in self.panel.ways.items():
            self.assertEqual(way.continue_button.isHidden(), key != AIM_MAKE, key)
        fired = []
        self.panel.continueRequested.connect(lambda: fired.append(True))
        self.panel.ways[AIM_MAKE].continue_button.click()
        self.assertEqual(fired, [True])

    def test_but_not_while_it_is_still_being_set_up(self):
        from anno_modding_studio.core.changes import AIM_MAKE

        self.panel.set_aim("", {}, ready=True, pending=AIM_MAKE)
        self.assertTrue(self.panel.ways[AIM_MAKE].continue_button.isHidden())

    # -- loadouts you had open before -----------------------------------------
    def test_recent_loadouts_are_a_list_to_pick_from(self):
        """As links they read as somewhere to navigate to, rather than as a
        set of files one of which you open."""
        from PySide6.QtWidgets import QListWidget, QPushButton

        self.panel.set_loadout(
            None, False, [r"C:\p\One.annoload", r"C:\p\Two.annoload"]
        )
        self.assertIsInstance(self.panel.recent_list, QListWidget)
        self.assertEqual(
            [self.panel.recent_list.item(i).text() for i in range(2)], ["One", "Two"]
        )
        self.assertFalse(
            [b for b in self.panel.findChildren(QPushButton) if b.objectName() == "Link"]
        )

    def test_what_the_game_loads_is_set_apart_from_the_rest(self):
        """It is on top whatever was opened since, and nothing said why."""
        from PySide6.QtCore import Qt

        recent = [r"C:\p\Live.annoload", r"C:\p\One.annoload",
                  r"C:\p\Two.annoload"]
        self.panel.set_loadout(None, False, recent, live=recent[0])
        listed = self.panel.recent_list
        live, rule, one = (listed.item(row) for row in range(3))
        self.assertFalse(live.icon().isNull())
        self.assertTrue(live.font().bold())
        self.assertEqual(rule.flags(), Qt.ItemFlag.NoItemFlags)
        self.assertEqual(listed.itemWidget(rule).objectName(), "RecentsRule")
        self.assertTrue(one.icon().isNull())
        self.assertFalse(one.font().bold())
        self.assertEqual(listed.count(), 4)

    def test_eight_are_listed(self):
        recent = [r"C:\p\P%d.annoload" % n for n in range(12)]
        self.panel.set_loadout(None, False, recent)
        self.assertEqual(self.panel.recent_list.count(), 8)

    def test_the_studios_folders_are_a_click_away(self):
        """The library's line named one of several folders alike in name,
        under AppData, which Explorer hides. One button lists them all."""
        fired = []
        self.panel.foldersRequested.connect(lambda: fired.append(True))
        self.assertEqual(self.panel.folders_button.text(), "Studio folders…")
        self.panel.folders_button.click()
        self.assertEqual(fired, [True])

    def test_picking_one_opens_it(self):
        chosen = []
        self.panel.recentChosen.connect(chosen.append)
        self.panel.set_loadout(None, False, [r"C:\p\One.annoload"])
        self.panel.recent_list.itemClicked.emit(self.panel.recent_list.item(0))
        self.assertEqual(chosen, [r"C:\p\One.annoload"])

    def test_a_double_click_opens_one_loadout_once(self):
        """Somebody used to double-clicking files does it here too.  The
        first click opened the loadout and the list was rebuilt without it,
        so the second press landed on whichever row had moved up under the
        pointer and opened that one as well."""
        from PySide6.QtCore import QEvent, QPointF
        from PySide6.QtGui import QMouseEvent

        recent = [r"C:\p\A.annoload", r"C:\p\B.annoload", r"C:\p\C.annoload"]
        for rebuilt in (True, False):
            with self.subTest(rebuilt=rebuilt):
                self.panel.set_loadout(None, False, recent)
                chosen = []

                def opened(where, rebuilt=rebuilt, chosen=chosen):
                    chosen.append(where)
                    if rebuilt:  # as the window does, once it has opened it
                        self.panel.set_loadout(
                            where, True, [p for p in recent if p != where]
                        )

                self.panel.recentChosen.connect(opened)
                listed = self.panel.recent_list
                viewport = listed.viewport()
                at = QPointF(listed.visualItemRect(listed.item(0)).center())
                left = Qt.MouseButton.LeftButton
                for kind, held in (
                    (QEvent.Type.MouseButtonPress, left),
                    (QEvent.Type.MouseButtonRelease, Qt.MouseButton.NoButton),
                    (QEvent.Type.MouseButtonDblClick, left),
                    (QEvent.Type.MouseButtonRelease, Qt.MouseButton.NoButton),
                ):
                    QApplication.sendEvent(viewport, QMouseEvent(
                        kind, at, viewport.mapToGlobal(at), left, held,
                        Qt.KeyboardModifier.NoModifier,
                    ))
                self.panel.recentChosen.disconnect(opened)
                self.assertEqual(chosen, [recent[0]])

    def test_enter_opens_the_row_picked_with_the_keyboard(self):
        from PySide6.QtTest import QTest

        chosen = []
        self.panel.recentChosen.connect(chosen.append)
        self.panel.set_loadout(None, False, [r"C:\p\One.annoload"])
        self.panel.recent_list.setCurrentRow(0)
        QTest.keyClick(self.panel.recent_list, Qt.Key.Key_Return)
        self.assertEqual(chosen, [r"C:\p\One.annoload"])

    def test_five_recent_loadouts_fit_without_a_scroll_bar(self):
        """The height left out a pixel of the frame each side, so the fifth
        row was clipped and Qt put a scroll bar on a list with nowhere to go."""
        from PySide6.QtCore import Qt

        from anno_modding_studio.ui import theme

        self.panel.setStyleSheet(theme.STYLESHEET)
        recent = [r"C:\p\P%d.annoload" % n for n in range(5)]
        self.panel.set_loadout(None, False, recent)
        listed = self.panel.recent_list
        self.assertEqual(listed.count(), 5)
        rows = sum(listed.sizeHintForRow(row) for row in range(5))
        self.assertGreaterEqual(listed.height() - 2 * listed.frameWidth(), rows)
        self.assertEqual(
            listed.verticalScrollBarPolicy(), Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )

    def test_where_a_loadout_is_saved_breaks_at_a_folder_not_after_the_drive(self):
        from anno_modding_studio.ui.widgets import PATH_BREAK

        self.panel.set_loadout(
            r"C:\Users\me\Documents\Anno Modding Studio\loadouts\One.annoload",
            True,
            changes=3,
        )
        shown = self.panel.loadout_detail.text()
        self.assertIn("C:\\" + PATH_BREAK + "Users", shown)

    def test_no_recent_loadouts_shows_no_empty_box(self):
        self.panel.set_loadout(None, False, [])
        self.assertTrue(self.panel.recent_list.isHidden())
        self.assertTrue(self.panel.recent_label.isHidden())

    def test_a_recent_row_offers_where_it_is_and_what_it_is_called(self):
        """The row shows a shortened name; the path is only in the tooltip."""
        from PySide6.QtGui import QGuiApplication

        self.panel.set_loadout(None, False, [r"C:\p\One.annoload"])
        item = self.panel.recent_list.item(0)
        menu = self.panel.menu_for(item)
        texts = [a.text() for a in menu.actions() if not a.isSeparator()]
        self.assertEqual(
            texts,
            ["Open this loadout", "Show in folder", "Copy path"],
        )
        next(a for a in menu.actions() if a.text() == "Copy path").trigger()
        self.assertEqual(
            QGuiApplication.clipboard().text(), r"C:\p\One.annoload"
        )

    def test_opening_from_that_menu_is_the_same_as_clicking_the_row(self):
        chosen = []
        self.panel.recentChosen.connect(chosen.append)
        self.panel.set_loadout(None, False, [r"C:\p\One.annoload"])
        menu = self.panel.menu_for(self.panel.recent_list.item(0))
        next(a for a in menu.actions() if a.text() == "Open this loadout").trigger()
        self.assertEqual(chosen, [r"C:\p\One.annoload"])

    def test_the_extractor_link_points_somewhere_that_exists(self):
        """The original author's account was renamed and the old link 404s."""
        from anno_modding_studio.ui import source_panel

        self.assertEqual(
            source_panel.RDA_TOOL_URL, "https://github.com/anno-mods/RDAExplorer"
        )
        self.assertIn(source_panel.RDA_TOOL_URL, self.panel.manual.text())

    def test_there_is_no_footer_button_under_the_page(self):
        """It duplicated the Asset editor tab and cut the page off above it."""
        from PySide6.QtWidgets import QPushButton

        texts = [b.text() for b in self.panel.findChildren(QPushButton)]
        self.assertFalse([t for t in texts if t.startswith("Open the asset editor")])

    def test_the_page_is_centred_in_a_wide_window(self):
        from anno_modding_studio.ui import source_panel, theme

        self.at(2400)
        column = self.panel._body
        self.assertEqual(column.width(), source_panel.WIDE_EMS * theme.FONT_PX)
        left = column.mapTo(self.panel, column.rect().topLeft()).x()
        right = self.panel.width() - (left + column.width())
        # The vertical scrollbar, if any, takes a little off the right.
        self.assertLess(abs(left - right), 40, (left, right))


class RestylesTheApp(unittest.TestCase):
    """Leaves the shared test app exactly as it found it: unstyled, in the
    default theme."""

    def setUp(self):
        from anno_modding_studio.ui import theme

        app = QApplication.instance()
        sheet = app.styleSheet()

        def restore():
            theme.apply(app, theme.SCHEMES[theme.DEFAULT_SCHEME],
                        theme.DEFAULT_OUTLINES, theme.DEFAULT_DENSITY)
            app.setStyleSheet(sheet)

        self.addCleanup(restore)


class TestMonospaceSurvivesTheStylesheet(RestylesTheApp):
    """A stylesheet that names a font family overrides setFont outright, so
    every QFont("Consolas") in the app drew in Segoe UI."""

    def test_a_widget_marked_monospace_is_drawn_monospaced(self):
        from PySide6.QtWidgets import QLabel

        from anno_modding_studio.ui import theme

        QApplication.instance().setStyleSheet(theme.STYLESHEET)
        plain, mono = QLabel("1010340"), theme.monospace(QLabel("1010340"))
        for label in (plain, mono):
            self.addCleanup(label.deleteLater)
            label.show()
        QApplication.processEvents()
        # The family the stylesheet asked for. Whether it then draws fixed
        # pitch is up to the fonts installed -- the offscreen platform the
        # suite runs on has none, so QFontInfo there cannot say.
        self.assertEqual(mono.font().family(), "Consolas")
        self.assertNotEqual(plain.font().family(), "Consolas")
        self.assertEqual(mono.font().pixelSize(), theme.FONT_PX)

    def test_a_cells_monospaced_font_is_at_the_body_size(self):
        """Not Qt's default 9pt, which it was."""
        from anno_modding_studio.ui import theme

        self.assertEqual(theme.mono_font().pixelSize(), theme.FONT_PX)


class TestTextSizes(RestylesTheApp):
    """Body text is 14 px and a hint 13. Interface size scales both, through
    Qt's scale factor, so neither changes while the Studio runs."""

    def test_a_hint_is_one_step_below_the_body_in_every_scheme(self):
        from anno_modding_studio.ui import theme

        self.assertEqual((theme.FONT_PX, theme.SMALL_PX), (14, 13))
        for key, scheme in theme.SCHEMES.items():
            with self.subTest(scheme=key):
                sheet = theme.stylesheet(scheme)
                self.assertIn("font-size: 14px", sheet)
                self.assertIn("font-size: 13px", sheet)

    def test_the_start_page_column_is_measured_in_the_text(self):
        from anno_modding_studio.ui.source_panel import SourcePanel

        panel = SourcePanel()
        self.addCleanup(panel.deleteLater)
        panel.resize(1500, 900)
        panel.show()
        QApplication.processEvents()
        self.assertTrue(panel._wide, "1500 px is room for two columns at 14 px")


class TestWhatTheWindowIsStyledBy(RestylesTheApp):
    """How the window looks is a thing the Studio remembers about this
    machine, so it sits in Settings. tests/test_design.py covers the
    Appearance settings themselves."""

    def setUp(self):
        super().setUp()
        self.window = MainWindow()
        self.addCleanup(_isolation.forget_settings)

    def tearDown(self):
        self.window.editor.search._debounce.stop()
        self.window.close()

    def test_view_says_where_appearance_is_set(self):
        from PySide6.QtWidgets import QMenu

        view = next(
            m for m in self.window.menuBar().findChildren(QMenu) if m.title() == "&View"
        )
        by_text = {a.text(): a for a in view.actions()}
        self.assertIn("Appearance…", by_text)
        self.assertNotIn("Text size…", by_text)
        # The action holds the method it was connected to, so it is the
        # dialog's exec() that is stood in for. Unmocked, it waits for a click.
        from anno_modding_studio.ui.settings_dialog import SettingsDialog

        with mock.patch.object(SettingsDialog, "exec") as shown:
            by_text["Appearance…"].trigger()
        shown.assert_called_once()

    def test_the_main_tabs_are_styled_as_the_navigation(self):
        """Their own name, so only they get the larger, underlined style --
        a dialog's tabs keep the quieter one."""
        from anno_modding_studio.ui import theme

        self.assertEqual(self.window.tabs.objectName(), "MainTabs")
        self.assertIn(
            "QTabWidget#MainTabs QTabBar::tab:selected { color: %s; border-bottom: 2px solid %s; }"
            % (theme.TEXT, theme.ACCENT),
            theme.STYLESHEET,
        )

    def test_paths_and_xml_are_marked_monospace(self):
        for widget in (
            self.window.editor.path_box,
            self.window.output.preview,
            self.window.source.assets_label,
            self.window.source.texts_label,
        ):
            self.assertTrue(widget.property("mono"), widget)


class TestTheSettingsDialog(unittest.TestCase):
    """What the Studio remembers about you, in one place rather than four.

    It was a File submenu of three entries and a View submenu of four sizes.
    Every one of them opened something that asked a question; none of them
    said what the Studio currently thought the answer was.
    """

    def setUp(self):
        from anno_modding_studio.ui.settings_dialog import SettingsDialog

        self.window = MainWindow()
        self.addCleanup(_isolation.forget_settings)
        self.addCleanup(self.window.deleteLater)
        self.window.editor.search._debounce.stop()
        self.dialog = SettingsDialog(
            self.window, self.window, store=no_credentials()
        )
        self.addCleanup(self.dialog.deleteLater)

    def test_it_is_four_tabs_by_whose_each_setting_is(self):
        """Eight boxes in one scroll: the mod.io key was the second box and
        the loader the seventh, and finding one meant reading past the rest."""
        tabs = self.dialog.pages
        titles = [tabs.tabText(i) for i in range(tabs.count())]
        self.assertEqual(titles, ["General", "Authoring", "mod.io", "Game and library", "Advanced"])

        def tab_of(widget):
            return next(i for i in range(tabs.count()) if tabs.widget(i).isAncestorOf(widget))

        self.assertEqual(tab_of(self.dialog.language_combo), 0)
        self.assertEqual(tab_of(self.dialog.size_combo), 0)
        self.assertEqual(tab_of(self.dialog.creator_edit), 1)
        self.assertEqual(tab_of(self.dialog.ranges_label), 1)
        self.assertEqual(tab_of(self.dialog.account.key_edit), 2)
        self.assertEqual(tab_of(self.dialog.library_label), 3)
        self.assertEqual(tab_of(self.dialog.probes_label), 4)

    def test_it_says_what_the_answers_currently_are(self):
        from anno_modding_studio.core import games

        self.assertEqual(self.dialog.account.key_edit.text(), "")
        self.assertIn("Not signed in", self.dialog.account.signed_label.text())
        for key in games.GAMES:
            self.assertIn(games.get(key).label, self.dialog.ranges_label.text())
        # No register ships with the Studio, and the suite's cache has none.
        self.assertIn("not fetched yet", self.dialog.ranges_label.text())
        self.assertIsNotNone(self.dialog.size_combo.currentData())

    def test_how_each_game_is_started_is_said_and_can_be_chosen(self):
        from unittest import mock

        from anno_modding_studio.ui.widgets import wrappable_path

        with mock.patch.object(self.window.preferences, "automatic_game_start", return_value="steam://rungameid/916440"):
            self.dialog._refresh_start()
            self.assertEqual(self.dialog.start_labels["anno1800"].text(),
                             "Automatic: steam://rungameid/916440")
            self.dialog.set_start("anno1800", r"C:\Games\Anno 1800\Bin\Win64\Anno1800.exe")
            self.assertEqual(self.window.preferences.game_start("anno1800"), r"C:\Games\Anno 1800\Bin\Win64\Anno1800.exe")
            # Shown so that it wraps at a folder, not after "C:".
            self.assertEqual(self.dialog.start_labels["anno1800"].text(),
                             wrappable_path(r"C:\Games\Anno 1800\Bin\Win64\Anno1800.exe"))
            self.dialog.set_start("anno1800", "")
            self.assertTrue(self.dialog.start_labels["anno1800"].text().startswith("Automatic"))
        with mock.patch.object(self.window.preferences, "automatic_game_start", return_value=""):
            self.dialog._refresh_start()
            self.assertIn("no Steam or Ubisoft Connect", self.dialog.start_labels["anno117"].text())

    def test_the_library_folder_can_be_opened_once_it_is_there(self):
        import tempfile
        from pathlib import Path

        from anno_modding_studio.ui import settings_dialog

        with tempfile.TemporaryDirectory() as made:
            missing = str(Path(made) / "not yet")
            with mock.patch.object(self.window.preferences, "library_root", return_value=missing), \
                    mock.patch.dict("os.environ", {"ANNO_STUDIO_LIBRARY": ""}):
                self.dialog._refresh_library()
                self.assertFalse(self.dialog.library_open_button.isEnabled())
                self.assertIn("does not exist yet", self.dialog.library_open_button.toolTip())
            with mock.patch.object(self.window.preferences, "library_root", return_value=made), \
                    mock.patch.dict("os.environ", {"ANNO_STUDIO_LIBRARY": ""}), \
                    mock.patch.object(settings_dialog, "open_on_disk") as opened:
                self.dialog._refresh_library()
                self.assertTrue(self.dialog.library_open_button.isEnabled())
                self.dialog.library_open_button.click()
            opened.assert_called_once_with(Path(made))

    def test_the_library_says_how_many_only_copies_it_holds_and_lists_them(self):
        import tempfile
        from pathlib import Path

        from support import install_mod

        from anno_modding_studio.core import games, library
        from anno_modding_studio.ui.only_copies_dialog import OnlyCopiesDialog, arrived

        with tempfile.TemporaryDirectory() as made:
            root, mods = Path(made) / "library", Path(made) / "mods"
            mods.mkdir()
            with mock.patch.object(self.window.preferences, "library_root", return_value=str(root)), \
                    mock.patch.dict("os.environ", {"ANNO_STUDIO_LIBRARY": ""}):
                self.dialog._refresh_library()
                self.assertEqual(self.dialog.only_copies_label.text(), "The library holds no mods.")
                self.assertFalse(self.dialog.only_copies_button.isEnabled())

                folder = library.game_dir(games.ANNO_1800, str(root))
                linked = library.adopt(folder, install_mod(mods, "[Gameplay] Moved In", "moved", "3"))
                library.record(folder, linked.target.name, modio_id=5, known_by="name")
                self.dialog._refresh_library()
                self.assertEqual(self.dialog.only_copies_label.text(),
                                 "The library's 1 mod can be downloaded or built again.")
                self.assertFalse(self.dialog.only_copies_button.isEnabled())

                library.adopt(folder, install_mod(mods, "[Gameplay] Only Here", "only", "1"))
                self.dialog._refresh_library()
                self.assertEqual(
                    self.dialog.only_copies_label.text(),
                    "The library holds the only copy of 1 mod. It can't be downloaded again.",
                )
                self.assertTrue(self.dialog.only_copies_button.isEnabled())

                # A copy from before how it arrived was recorded is said apart.
                before = folder / "[Gameplay] From Before"
                before.mkdir()
                library.note_link(folder, library.link(before, mods), library.COPY)
                self.dialog._refresh_library()
                self.assertEqual(
                    self.dialog.only_copies_label.text(),
                    "The library holds the only copy of 1 mod. It can't be downloaded "
                    "again. Where 1 more came from is not recorded.",
                )

                shown = []
                with mock.patch.object(OnlyCopiesDialog, "exec", lambda dialog: shown.append(dialog)):
                    self.dialog.only_copies_button.click()
                (listed,) = shown
                group = listed.tree.topLevelItem(0)
                self.assertEqual(group.text(0), games.get(games.ANNO_1800).label)
                self.assertEqual([group.child(n).text(0) for n in range(group.childCount())],
                                 ["Only copies", "Not recorded"])
                unknown = group.child(1).child(0)
                self.assertEqual((unknown.text(0), unknown.text(2)), ("[Gameplay] From Before", ""))
                row = group.child(0).child(0)
                self.assertEqual((row.text(0), row.text(1), row.text(2)),
                                 ("[Gameplay] Only Here", "1", "Moved in from your mods folder"))
                # A game's name is not a folder; a mod's row is.
                self.assertFalse(listed.open_button.isEnabled())
                listed.tree.setCurrentItem(row)
                self.assertTrue(listed.open_button.isEnabled())
                from anno_modding_studio.ui import only_copies_dialog

                with mock.patch.object(only_copies_dialog, "open_on_disk") as opened:
                    listed.open_button.click()
                opened.assert_called_once_with(str(folder / "[Gameplay] Only Here"))
                self.assertEqual(arrived(library.Held("x", Path("x"), Path("x"), source=library.ZIP, zip="a.zip")),
                                 "Unpacked from a.zip")
                self.assertEqual(arrived(library.Held("x", Path("x"), Path("x"))), "Not recorded")

    def test_the_account_is_the_modio_tab_itself(self):
        """It was a dialog of its own behind a button on a tab that held
        nothing else.  A saved key shows hidden, and a sign-in by address."""
        from PySide6.QtWidgets import QLineEdit

        from anno_modding_studio.core import secrets
        from anno_modding_studio.ui.settings_dialog import TAB_MODIO, SettingsDialog

        held = secrets.Credentials(api_key="abc123", token="t", email="me@example.com")
        dialog = SettingsDialog(self.window, self.window, store=_stub_store(held), tab=TAB_MODIO)
        self.addCleanup(dialog.deleteLater)
        self.assertEqual(dialog.pages.currentIndex(), TAB_MODIO)
        self.assertEqual(dialog.account.key_edit.echoMode(), QLineEdit.EchoMode.Password)
        self.assertIn("Signed in as me@example.com", dialog.account.signed_label.text())

    def test_setting_up_the_account_elsewhere_opens_settings_there(self):
        """The install, get and update dialogs' "Set up mod.io account"."""
        from anno_modding_studio.ui.settings_dialog import TAB_MODIO

        with mock.patch.object(self.window.preferences, "show_settings") as opened:
            self.window.modio.modio_account()
        opened.assert_called_once_with(tab=TAB_MODIO)

    def test_what_the_account_did_is_said_on_closing(self):
        self.dialog.account.note = "Key saved and accepted by mod.io."
        self.dialog.done(0)
        self.assertEqual(self.window.status_label.text(), "Key saved and accepted by mod.io.")

    def test_the_range_new_assets_take_is_chosen_per_game(self):
        """It was a choice only in the New asset dialog, and kept on one
        loadout."""
        from anno_modding_studio.core import games, guidranges

        # A fetched copy of the register to choose from: none ships with the
        # Studio. In a cache of the test's own, so no other test finds it.
        cache = tempfile.TemporaryDirectory()
        self.addCleanup(cache.cleanup)
        own = mock.patch.dict(os.environ, {"ANNO_STUDIO_CACHE": cache.name})
        own.start()
        self.addCleanup(own.stop)
        target = guidranges.downloaded_path(games.ANNO_1800)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            "| User | Start ID | End ID |\n| :-- | --: | --: |\n"
            "| Somebody | 1.500.000.000 | 1.500.009.999 |\n"
            "| *Personal use* | 2.001.000.000 | 2.001.009.999 |\n",
            encoding="utf-8",
        )
        self.dialog._refresh_ranges()

        combo = self.dialog.range_combos[games.ANNO_1800]
        self.assertEqual(combo.currentData(), "")
        listed = guidranges.load(games.ANNO_1800).claimable()
        owner = next(item.owner for item in listed if not item.personal)
        combo.setCurrentIndex(combo.findData(owner))
        self.assertEqual(self.window.preferences.guid_range(games.ANNO_1800), owner)
        self.assertIn("Somebody else's", self.dialog.range_notes[games.ANNO_1800].text())
        self.assertEqual(self.window.preferences.guid_range(games.ANNO_117), "")
        combo.setCurrentIndex(0)
        self.assertEqual(self.window.preferences.guid_range(games.ANNO_1800), "")

    def test_updating_the_ranges_asks_for_them_and_says_what_it_got(self):
        with mock.patch.object(self.window.preferences, "update_guid_ranges") as fetched:
            self.dialog.ranges_button.click()
        fetched.assert_called_once_with()
        # Nothing fetched in the suite's own cache, and the label says so.
        self.assertIn("not fetched yet", self.dialog.ranges_label.text())

    def test_the_creator_name_lands_when_the_field_is_left(self):
        self.dialog.creator_edit.setText("Someone Else")
        self.dialog.creator_edit.editingFinished.emit()
        self.assertEqual(self.window.preferences.creator_name(), "Someone Else")
        self.assertIn("Someone Else", self.dialog.creator_note.text())

    def test_the_other_names_land_when_the_field_is_left(self):
        """Tidied: none twice, and not the author name, which they are beside."""
        self.window.preferences.set_creator_name("Me")
        self.dialog.aliases_edit.setText(" Old Me, ,old me, me ,Older Me")
        self.dialog.aliases_edit.editingFinished.emit()
        self.assertEqual(self.window.preferences.aliases(), ["Old Me", "Older Me"])
        self.assertEqual(self.window.loadout.aliases, ["Old Me", "Older Me"])
        self.assertEqual(self.dialog.aliases_edit.text(), "Old Me, Older Me")
        self.assertIn("Me, Old Me or Older Me", self.dialog.creator_note.text())

    def test_and_one_made_the_author_name_is_no_longer_among_them(self):
        self.window.preferences.set_creator_name("Me")
        self.window.preferences.set_aliases(["Old Me"])
        self.dialog.creator_edit.setText("Old Me")
        self.dialog.creator_edit.editingFinished.emit()
        self.assertEqual(self.window.preferences.aliases(), [])
        self.assertEqual(self.window.loadout.aliases, [])
        self.assertEqual(self.dialog.aliases_edit.text(), "")

    def test_a_loadout_opened_knows_them_too(self):
        """Never saved with a loadout: a fact about you, handed to each one."""
        from anno_modding_studio.core.changes import Loadout

        self.window.preferences.set_aliases(["Old Me"])
        path = Path(tempfile.mkdtemp(prefix="ams-aliases-")) / "a.annoload"
        Loadout(game=self.window.loadout.game).save(path)
        with mock.patch.object(self.window.files, "confirm_discard", return_value=True):
            self.window.load_loadout(path)
        self.assertEqual(self.window.loadout.aliases, ["Old Me"])
        self.window.reset_loadout()
        self.assertEqual(self.window.loadout.aliases, ["Old Me"])

    def test_and_clearing_it_forgets_who_you_are(self):
        self.window.preferences.set_creator_name("Someone")
        self.dialog.creator_edit.setText("")
        self.dialog.done(0)
        self.assertEqual(self.window.preferences.creator_name(), "")
        self.assertIn("counts as someone else's", self.dialog.creator_note.text())


class TestDecliningAGameSwitchLeavesEverythingWhereItWas(unittest.TestCase):
    """The Start tab could be talked into naming one game while holding the
    other's files.

    "Use these files" on the game you are not in set the install folder, the
    export destination and the texts, schema and archive waiting to be applied
    -- all of them the other game's -- and only *then* asked whether to switch
    at all.  Saying no returned from the middle of that, so nothing was loaded
    and nothing was put back: the Start tab went on saying Anno 117 over an
    Anno 1800 install, and the next index built for any reason picked up the
    declined game's localisation on its way past.
    """

    @classmethod
    def setUpClass(cls):
        sys.path.insert(0, str(ROOT / "tools"))
        import make_sample_assets

        from anno_modding_studio.core import index as index_mod

        cls._tmp = tempfile.TemporaryDirectory()
        tmp = Path(cls._tmp.name)
        cls.files = {}
        for game, relpath in (
            ("1800", "data/config/export/main/asset/assets.xml"),
            ("117", "data/base/config/export/assets.xml"),
        ):
            root = tmp / game
            path = root / relpath
            path.parent.mkdir(parents=True)
            path.write_text(make_sample_assets.build_assets_xml(20), encoding="utf-8")
            # Where the Studio would have cached it, so restoring is the fast
            # path it is meant to be rather than a rebuild.
            db = index_mod.cache_path_for(path)
            index_mod.build_index(path, db)
            cls.files[game] = (root, db, path)
        cls.install = tmp / "Anno 1800"
        (cls.install / "maindata").mkdir(parents=True)
        (cls.install / "maindata" / "data0.rda").write_bytes(b"data0")

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def setUp(self):
        from anno_modding_studio.core import games
        from anno_modding_studio.core.changes import Change

        self.window = MainWindow()
        self.addCleanup(_isolation.forget_settings)

        root, _db, assets = self.files["1800"]
        gamefiles.record_read(
            root,
            game=games.ANNO_1800,
            game_folder=self.install,
            assets=assets,
            source_archive="data0.rda",
        )
        key = "%s:%s" % (self.window.game_files.READ_ROOT_KEY, games.ANNO_1800)
        self.window.settings.setValue(key, str(root))
        self.addCleanup(self.window.settings.remove, key)

        # In the other game, with work in it -- which is what makes switching
        # a question rather than a no-op.
        _r, db117, assets117 = self.files["117"]
        self.window.game_files.adopt_index(db117, assets117, cached=True)
        self.window.loadout.set_change(
            Change(guid="1010030", asset_label="a house",
                   path="/Values/Residence7/Residents", tag="Residents",
                   old_value="8", new_value="12")
        )

        self.asked = []
        named = _isolation.answering(lambda title, _text: self.asked.append(title) or False)
        named.start()
        self.addCleanup(named.stop)

    def tearDown(self):
        self.window.editor.search._debounce.stop()
        self.window.game_files.stop_texts_worker()
        if self.window.index is not None:
            self.window.index.close()
            self.window.index = None
        self.window.loadout.clear()
        self.window.deleteLater()

    def decline(self):
        from anno_modding_studio.core import games

        before = (
            self.window.game_folder,
            self.window.game_files.pending_texts,
            dict(self.window.game_files.pending_schema),
            self.window.game_files.pending_archive,
        )
        answered = self.window.game_files.restore_last_read(games.ANNO_1800)
        self.assertIn("Switch games?", self.asked)
        return answered, before

    def test_nothing_it_would_have_set_up_is_set_up(self):
        answered, before = self.decline()
        self.assertFalse(answered)
        self.assertEqual(
            (
                self.window.game_folder,
                self.window.game_files.pending_texts,
                dict(self.window.game_files.pending_schema),
                self.window.game_files.pending_archive,
            ),
            before,
        )

    def test_and_the_files_that_are_open_are_still_the_ones_that_were(self):
        from anno_modding_studio.core import games

        self.decline()
        self.assertEqual(Path(self.window.index.source_path), self.files["117"][2])
        self.assertEqual(self.window.loadout.game, games.ANNO_117)

    def test_and_the_start_tab_still_names_the_game_it_is_holding(self):
        from PySide6.QtWidgets import QLabel

        from anno_modding_studio.core import games

        self.decline()
        self.window.start.refresh_games()
        rows = self.window.source.games_box
        said = " ".join(label.text() for label in rows.findChildren(QLabel))
        self.assertIn("in use", said)
        self.assertIn(games.get(games.ANNO_117).label, self.window.source.game_label.text())

    def test_and_the_work_that_made_it_a_question_is_still_there(self):
        self.decline()
        self.assertEqual(self.window.loadout.authored_count, 1)


class TestTheStartTabReadsTheGameOffTheIndex(unittest.TestCase):
    """Which game is "in use" is a fact about the files that are open.

    It used to be read off the loadout instead, and a loadout is adopted
    before its base file is: a build that never lands -- cancelled at the
    progress dialog, or failed -- left the Start tab marking a game "in use"
    whose index was not open, over an asset count belonging to the other one.
    """

    @classmethod
    def setUpClass(cls):
        sys.path.insert(0, str(ROOT / "tools"))
        import make_sample_assets

        from anno_modding_studio.core import index as index_mod

        cls._tmp = tempfile.TemporaryDirectory()
        tmp = Path(cls._tmp.name)
        cls.assets = tmp / "data" / "config" / "export" / "main" / "asset" / "assets.xml"
        cls.assets.parent.mkdir(parents=True)
        cls.assets.write_text(make_sample_assets.build_assets_xml(20), encoding="utf-8")
        cls.db = tmp / "index.sqlite"
        index_mod.build_index(cls.assets, cls.db)

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def setUp(self):
        self.window = MainWindow()
        self.addCleanup(_isolation.forget_settings)
        self.window.game_files.adopt_index(self.db, self.assets, cached=True)

    def tearDown(self):
        self.window.editor.search._debounce.stop()
        self.window.game_files.stop_texts_worker()
        if self.window.index is not None:
            self.window.index.close()
            self.window.index = None
        self.window.loadout.clear()
        self.window.deleteLater()

    def rows(self):
        from PySide6.QtWidgets import QLabel

        return " ".join(
            label.text()
            for label in self.window.source.games_box.findChildren(QLabel)
        )

    def test_a_loadout_claiming_the_other_game_does_not_move_it(self):
        from anno_modding_studio.core import games

        self.window.loadout.game = games.ANNO_117
        self.window.loadout.source_path = "C:/nowhere/data/base/config/export/assets.xml"
        self.window.start.refresh_source_panel()
        self.assertEqual(self.window.game_files.open_game(), games.ANNO_1800)
        self.assertIn(games.get(games.ANNO_1800).label, self.window.source.game_label.text())
        self.assertIn("in use", self.rows())

    def test_and_the_file_it_names_is_the_one_it_indexed(self):
        self.window.loadout.source_path = "C:/nowhere/assets.xml"
        self.window.start.refresh_source_panel()
        self.assertEqual(
            Path(self.window.source.assets_label.full_text()), self.assets
        )

    def test_with_nothing_loaded_it_claims_no_game_at_all(self):
        self.window.index.close()
        self.window.index = None
        self.window.start.refresh_source_panel()
        self.assertEqual(self.window.game_files.open_game(), "")
        self.assertEqual(self.window.source.game_label.text(), "—")


class TestOpeningALoadoutFromTheMenu(StartTabCase):
    """QAction.triggered hands its ``checked`` flag to whatever it is wired to.

    Open loadout takes a path as its first argument, so the menu entry was
    calling it with False -- and the Studio went looking for a file named
    "False", which it reported as "False could not be opened".
    """

    def action(self, label):
        from PySide6.QtGui import QAction

        for action in self.window.findChildren(QAction):
            if action.text() == label:
                return action
        self.fail("no %s action" % label)

    def test_it_asks_which_loadout_rather_than_opening_one_called_false(self):
        from PySide6.QtWidgets import QFileDialog

        asked = []
        blew_up = []
        real_open, real_critical = QFileDialog.getOpenFileName, QMessageBox.critical
        QFileDialog.getOpenFileName = staticmethod(
            lambda *a, **k: (asked.append(a) or ("", ""))
        )
        QMessageBox.critical = staticmethod(
            lambda parent, title, text, *a, **k: blew_up.append((title, text))
        )
        try:
            self.action("&Open loadout…").trigger()
        finally:
            QFileDialog.getOpenFileName = staticmethod(real_open)
            QMessageBox.critical = staticmethod(real_critical)
        self.assertEqual(blew_up, [])
        self.assertEqual(len(asked), 1)

    def test_and_the_same_for_every_other_entry_that_takes_a_path(self):
        """Each of these has a first argument the flag would land in."""
        from PySide6.QtWidgets import QFileDialog

        for label in ("&Load base assets.xml…", "Load &localisation texts…"):
            with self.subTest(label=label):
                asked = []
                real = QFileDialog.getOpenFileName
                QFileDialog.getOpenFileName = staticmethod(
                    lambda *a, **k: (asked.append(a) or ("", ""))
                )
                try:
                    self.action(label).trigger()
                finally:
                    QFileDialog.getOpenFileName = staticmethod(real)
                self.assertEqual(len(asked), 1, "it did not ask for a file")


class TestTheTabYouLandOnIsOneThatIsOpen(StartTabCase):
    """Qt will park on a disabled tab quite happily.

    What that looks like is the asset editor, working, under its own
    greyed-out tab -- which is where "Continue in the asset editor" put you
    after opening a loadout from the recents list.  The loadout brought the
    aim and the work the gates were waiting for, and nothing recomputed them.
    """

    def saved(self, aim=""):
        from anno_modding_studio.core.changes import Loadout

        loadout = Loadout(source_path=str(self.assets), game=self.window.loadout.game)
        loadout.own.name = "Better Harbours"
        loadout.aim = aim
        path = Path(self._tmp.name) / "recent.annoload"
        loadout.save(path)
        return path

    def test_opening_one_that_settles_the_question_opens_the_tabs(self):
        from anno_modding_studio.core.changes import AIM_MAKE

        self.assertFalse(self.window.tabs.isTabEnabled(TAB_EDIT))
        self.assertTrue(self.window.load_loadout(self.saved(AIM_MAKE)))
        self.assertTrue(self.window.tabs.isTabEnabled(TAB_EDIT))

    def test_and_continuing_into_the_editor_lands_on_an_open_tab(self):
        from anno_modding_studio.core.changes import AIM_MAKE

        self.window.load_loadout(self.saved(AIM_MAKE))
        self.window.source.continueRequested.emit()
        self.assertEqual(self.window.tabs.currentIndex(), TAB_EDIT)
        self.assertTrue(self.window.tabs.isTabEnabled(TAB_EDIT))

    def test_and_one_that_settles_nothing_leaves_them_shut(self):
        self.assertTrue(self.window.load_loadout(self.saved()))
        self.assertFalse(self.window.tabs.isTabEnabled(TAB_EDIT))

    def test_so_nothing_can_put_you_past_a_shut_gate(self):
        self.window._go_to_editor()
        self.assertEqual(self.window.tabs.currentIndex(), TAB_SOURCE)
        self.window._focus_search()
        self.assertEqual(self.window.tabs.currentIndex(), TAB_SOURCE)

    def test_and_shutting_one_under_you_brings_you_back(self):
        from anno_modding_studio.core.changes import AIM_MAKE

        self.window.load_loadout(self.saved(AIM_MAKE))
        self.window._go_to_editor()
        self.assertEqual(self.window.tabs.currentIndex(), TAB_EDIT)
        self.assertTrue(self.window.new_loadout())
        self.assertEqual(self.window.tabs.currentIndex(), TAB_SOURCE)


class TestALoadoutWaitsForTheFilesItIsMeasuredAgainst(unittest.TestCase):
    """Opening one used to be done before its base file was even read.

    A loadout names the assets.xml it was built against, and until that file
    is indexed there is nothing for its changes to mean: the GUIDs they name
    are in it, and the game it says it is for decides the dialect every one of
    them exports in.  It was taken up first regardless -- so cancelling the
    build at the progress dialog, or having it fail, left the Studio holding
    one game's loadout over the other game's index, offering to export Anno
    117 ModOps against Anno 1800 GUIDs, with a line in the status bar the only
    sign anything had gone wrong.
    """

    @classmethod
    def setUpClass(cls):
        sys.path.insert(0, str(ROOT / "tools"))
        import make_sample_assets

        from anno_modding_studio.core import index as index_mod

        cls._tmp = tempfile.TemporaryDirectory()
        tmp = Path(cls._tmp.name)
        cls.files = {}
        for game, relpath in (
            ("1800", "data/config/export/main/asset/assets.xml"),
            ("117", "data/base/config/export/assets.xml"),
        ):
            path = tmp / game / relpath
            path.parent.mkdir(parents=True)
            path.write_text(make_sample_assets.build_assets_xml(20), encoding="utf-8")
            db = tmp / ("%s.sqlite" % game)
            index_mod.build_index(path, db)
            cls.files[game] = (db, path)

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def setUp(self):
        self.window = MainWindow()
        self.addCleanup(_isolation.forget_settings)
        self.said = []
        real_c = QMessageBox.critical
        self.named = _isolation.answering()
        self.named.start()
        self.addCleanup(self.named.stop)
        self.answer(True)
        QMessageBox.critical = staticmethod(
            lambda parent, title, text, *a, **k: self.said.append(title)
        )
        self.addCleanup(setattr, QMessageBox, "critical", staticmethod(real_c))

    def tearDown(self):
        self.window.editor.search._debounce.stop()
        self.window.game_files.stop_texts_worker()
        if self.window.index is not None:
            self.window.index.close()
            self.window.index = None
        self.window.loadout.clear()
        self.window.deleteLater()

    def answer(self, go_on: bool):
        """Press the action of every question from here on, or the other button."""
        self.named.reply = lambda title, _text: self.said.append(title) or go_on

    def a_117_file(self) -> Path:
        """A saved loadout for the other game, with work of its own in it."""
        from anno_modding_studio.core import games
        from anno_modding_studio.core.changes import Change, Loadout

        loadout = Loadout(
            source_path=str(self.files["117"][1]), game=games.ANNO_117
        )
        loadout.set_change(
            Change(guid="1010030", asset_label="a house",
                   path="/Values/Residence7/Residents", tag="Residents",
                   old_value="8", new_value="12")
        )
        path = Path(self._tmp.name) / "seventeen.annoload"
        loadout.save(path)
        return path

    def in_1800_with_a_117_loadout(self) -> Path:
        saved = self.a_117_file()
        db, path = self.files["1800"]
        self.window.game_files.adopt_index(db, path, cached=True)
        return saved

    def stop_the_build(self, how):
        """End the next index build the way ``how`` says, as Cancel does."""
        from anno_modding_studio.core import index as index_mod
        from anno_modding_studio.ui import workers

        # An earlier test may have left this file's index in the cache, and a
        # build that never starts is not one this can stop.
        index_mod.cache_path_for(self.files["117"][1]).unlink(missing_ok=True)
        real = workers.IndexWorker.run
        workers.IndexWorker.run = lambda inner: inner.failed.emit(how)
        self.addCleanup(setattr, workers.IndexWorker, "run", real)

    def settle(self):
        worker = self.window.game_files.worker
        if worker is not None:
            self.assertTrue(worker.wait(30000))
        QApplication.processEvents()

    def open_it_but(self, how) -> Path:
        """Open the 117 loadout with its build ending in ``how``."""
        saved = self.in_1800_with_a_117_loadout()
        self.stop_the_build(how)
        self.assertFalse(
            self.window.load_loadout(saved), "the build was not stopped"
        )
        self.settle()
        return saved

    def test_a_cancelled_build_opens_nothing_rather_than_a_mismatch(self):
        from anno_modding_studio.core import games

        self.open_it_but("cancelled")
        self.assertIsNone(self.window.loadout_path)
        self.assertIsNone(self.window.game_files.pending_loadout)
        # The game, the file it names and the index all still agree.
        self.assertEqual(self.window.loadout.game, games.ANNO_1800)
        self.assertEqual(
            Path(self.window.loadout.source_path), self.files["1800"][1]
        )
        self.assertEqual(Path(self.window.index.source_path), self.files["1800"][1])
        self.assertNotIn("seventeen", self.window.windowTitle())

    def test_and_says_so_where_the_cancelling_happened(self):
        self.open_it_but("cancelled")
        said = self.window.status_label.text()
        self.assertIn("cancelled", said)
        self.assertIn("seventeen", said)
        self.assertNotIn("annoload", said)

    def test_a_failed_build_is_the_same_answer(self):
        from anno_modding_studio.core import games

        self.open_it_but("Boom: it fell over")
        self.assertIsNone(self.window.loadout_path)
        self.assertEqual(self.window.loadout.game, games.ANNO_1800)
        self.assertIn("Could not index that file", self.said)
        self.assertIn("seventeen", self.window.status_label.text())

    def test_and_its_changes_were_never_let_in_over_the_wrong_index(self):
        self.open_it_but("cancelled")
        self.assertEqual(len(self.window.loadout), 0)

    def test_but_a_build_that_lands_opens_it_as_before(self):
        from anno_modding_studio.core import games

        saved = self.in_1800_with_a_117_loadout()
        self.window.load_loadout(saved)
        self.settle()
        self.assertIsNone(self.window.game_files.pending_loadout)
        self.assertEqual(self.window.loadout_path, saved)
        self.assertEqual(self.window.loadout.game, games.ANNO_117)
        self.assertEqual(Path(self.window.index.source_path), self.files["117"][1])
        self.assertEqual(self.window.loadout.authored_count, 1)

    def test_and_it_is_asked_about_once_not_twice(self):
        """_confirm_base_swap names both files and both games; the generic
        switch guard behind it would only ask the same thing again, worse."""
        saved = self.in_1800_with_a_117_loadout()
        self.window.load_loadout(saved)
        self.settle()
        self.assertEqual(self.said, ["Load its game files too?"])

    def test_and_the_install_of_the_game_left_is_not_kept(self):
        """Its question stands in for the switch guard's, and what a switch
        costs was skipped with it: the window went on naming the Anno 1800
        install, offered as where the 117 loadout's mods are."""
        saved = self.in_1800_with_a_117_loadout()
        install = Path(self._tmp.name) / "Anno 1800"
        self.window.game_folder = install
        self.window.load_loadout(saved)
        self.settle()
        self.assertEqual(self.window.loadout_path, saved)
        self.assertIsNone(self.window.game_folder)

    def test_but_a_build_that_does_not_land_keeps_it(self):
        install = Path(self._tmp.name) / "Anno 1800"
        saved = self.in_1800_with_a_117_loadout()
        self.window.game_folder = install
        self.stop_the_build("cancelled")
        self.assertFalse(self.window.load_loadout(saved))
        self.settle()
        self.assertEqual(self.window.game_folder, install)

    def test_and_one_opened_against_nothing_still_opens(self):
        """Declining the offer to load its base file is a thing to want."""
        saved = self.a_117_file()
        self.assertIsNone(self.window.index, "this one starts with nothing loaded")
        self.answer(False)
        self.assertTrue(self.window.load_loadout(saved))
        self.assertEqual(self.said, ["Load the base file too?"])
        self.assertEqual(self.window.loadout_path, saved)
        self.assertIsNone(self.window.index)
        self.assertIsNone(self.window.game_files.pending_loadout)


class TestASwitchThatDoesNotHappenCostsNothing(unittest.TestCase):
    """Agreeing to lose the work used to be the same moment as losing it.

    Switching games is a new loadout, and you are asked to say so.  But the
    switch is not done until the other game's index is open, and clearing
    the loadout on the strength of the answer alone meant a build that failed
    -- or that you cancelled at the progress dialog, having changed your mind,
    which is the likeliest way to get here -- left you with neither game: the
    work discarded and the old files still loaded.
    """

    @classmethod
    def setUpClass(cls):
        sys.path.insert(0, str(ROOT / "tools"))
        import make_sample_assets

        from anno_modding_studio.core import index as index_mod

        cls._tmp = tempfile.TemporaryDirectory()
        tmp = Path(cls._tmp.name)
        cls.files = {}
        for game, relpath in (
            ("1800", "data/config/export/main/asset/assets.xml"),
            ("117", "data/base/config/export/assets.xml"),
        ):
            path = tmp / game / relpath
            path.parent.mkdir(parents=True)
            path.write_text(make_sample_assets.build_assets_xml(20), encoding="utf-8")
            db = tmp / ("%s.sqlite" % game)
            index_mod.build_index(path, db)
            cls.files[game] = (db, path)
        # An install for the game being left, so there is something to forget.
        cls.install = tmp / "Anno 1800"
        (cls.install / "maindata").mkdir(parents=True)
        (cls.install / "maindata" / "data0.rda").write_bytes(b"data0")

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def setUp(self):
        from anno_modding_studio.core.changes import Change

        self.window = MainWindow()
        self.addCleanup(_isolation.forget_settings)
        self.window.game_files.adopt_index(*self.files["1800"], cached=True)
        self.window.loadout.set_change(
            Change(guid="1010030", asset_label="a house",
                   path="/Values/Residence7/Residents", tag="Residents",
                   old_value="8", new_value="12")
        )
        self.window.dirty = True
        self.window.game_folder = self.install

        self.said = []
        # "Unsaved loadout" offers Save, Discard and Cancel, and is answered
        # Discard. Every other question is asked by its action's name.
        named = _isolation.answering(
            lambda title, _text: self.said.append(title)
            or ("Discard" if title == "Unsaved loadout" else True))
        named.start()
        self.addCleanup(named.stop)

    def tearDown(self):
        self.window.editor.search._debounce.stop()
        self.window.game_files.stop_texts_worker()
        if self.window.index is not None:
            self.window.index.close()
            self.window.index = None
        self.window.loadout.clear()
        self.window.deleteLater()

    def stop_the_build(self, how="cancelled"):
        from anno_modding_studio.core import index as index_mod
        from anno_modding_studio.ui import workers

        index_mod.cache_path_for(self.files["117"][1]).unlink(missing_ok=True)
        real = workers.IndexWorker.run
        workers.IndexWorker.run = lambda inner: inner.failed.emit(how)
        self.addCleanup(setattr, workers.IndexWorker, "run", real)

    def settle(self):
        worker = self.window.game_files.worker
        if worker is not None:
            self.assertTrue(worker.wait(30000))
        QApplication.processEvents()

    def remember_117_was_read(self):
        from anno_modding_studio.core import games

        root = self.files["117"][1].parents[3]
        gamefiles.record_read(
            root,
            game=games.ANNO_117,
            game_folder=self.install,
            assets=self.files["117"][1],
            source_archive="data0.rda",
        )
        key = "%s:%s" % (self.window.game_files.READ_ROOT_KEY, games.ANNO_117)
        self.window.settings.setValue(key, str(root))
        self.addCleanup(self.window.settings.remove, key)

    def still_there(self):
        from anno_modding_studio.core import games

        self.assertEqual(self.window.loadout.authored_count, 1)
        self.assertTrue(self.window.dirty, "and still unsaved")
        self.assertEqual(
            Path(self.window.index.source_path), self.files["1800"][1]
        )
        self.assertEqual(self.window.loadout.game, games.ANNO_1800)
        self.assertEqual(self.window.game_folder, self.install)

    # -- picking the other game's assets.xml by hand -------------------------
    def test_a_cancelled_build_leaves_the_work_it_was_to_replace(self):
        self.stop_the_build()
        self.window.game_files.open_assets(self.files["117"][1])
        self.settle()
        self.assertEqual(self.said, ["Switch games?", "Unsaved loadout"])
        self.still_there()

    def test_and_a_failed_one_does_too(self):
        self.stop_the_build("Boom: it fell over")
        real = QMessageBox.critical
        QMessageBox.critical = staticmethod(lambda *a, **k: None)
        self.addCleanup(setattr, QMessageBox, "critical", staticmethod(real))
        self.window.game_files.open_assets(self.files["117"][1])
        self.settle()
        self.still_there()

    def test_but_a_build_that_lands_takes_what_was_agreed_to(self):
        from anno_modding_studio.core import games

        self.window.game_files.open_assets(self.files["117"][1])
        self.settle()
        self.assertEqual(self.window.loadout.authored_count, 0)
        self.assertEqual(self.window.loadout.game, games.ANNO_117)
        self.assertIsNone(self.window.game_folder, "it named the game left behind")

    # -- and from the Start tab's own way in ---------------------------------
    def test_use_these_files_is_the_same_bargain(self):
        from anno_modding_studio.core import games

        self.remember_117_was_read()
        self.stop_the_build()
        self.assertFalse(self.window.game_files.restore_last_read(games.ANNO_117))
        self.settle()
        self.still_there()

    def test_and_asks_once_rather_than_twice(self):
        """Both callers put the question before anything moves; the guard
        inside open_assets() would only ask it again, which reads as the
        first answer not having taken."""
        from anno_modding_studio.core import games

        self.remember_117_was_read()
        self.window.game_files.restore_last_read(games.ANNO_117)
        self.settle()
        self.assertEqual(self.said, ["Switch games?", "Unsaved loadout"])

    # -- and nothing staged for it is left lying about -----------------------
    def test_nothing_staged_for_the_other_game_is_left_armed(self):
        """Its localisation, schema and archive name were applied to whatever
        index was adopted next."""
        self.remember_117_was_read()
        self.stop_the_build()
        self.window.game_files.restore_last_read("anno117")
        self.settle()
        self.assertIsNone(self.window.game_files.pending_texts)
        self.assertEqual(self.window.game_files.pending_schema, {})
        self.assertEqual(self.window.game_files.pending_archive, "")
        self.assertFalse(self.window.game_files.pending_reset)


if __name__ == "__main__":
    unittest.main(verbosity=2)
