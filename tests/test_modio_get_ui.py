"""Getting a mod from mod.io, and updating one, driven offscreen against a fake.

The fake mod.io answers searches and lookups; the fake CDN hands back a zip
built for the test.  Nothing touches the network, and the mods folder is a
temporary one.
"""

from __future__ import annotations

import hashlib
import os
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

import _isolation  # first: keeps the suite out of real files under any runner

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from PySide6.QtWidgets import QApplication, QDialog, QMessageBox  # noqa: E402
from support import FakeServer, reverse_bytes, zip_bytes  # noqa: E402

from anno_modding_studio.core import (  # noqa: E402
    library,
    modio_cache,
    places,
    secrets,
    updates,
)
from anno_modding_studio.core.changes import Loadout, ModLayer  # noqa: E402
from anno_modding_studio.core.games import ANNO_1800  # noqa: E402
from anno_modding_studio.ui.modio_get_dialog import ModioGetDialog  # noqa: E402
from anno_modding_studio.ui.modio_updates_dialog import ModioUpdatesDialog  # noqa: E402

_app = QApplication.instance() or QApplication([])


def _mod_with_file(mod_id: int, name: str, version: str, body: bytes, flagged: bool = False) -> dict:
    return {
        "id": mod_id,
        "name": name,
        "name_id": name.lower().replace(" ", "-").replace("(", "").replace(")", ""),
        "profile_url": "https://mod.io/g/anno-1800/m/%d" % mod_id,
        "date_updated": 1700000000,
        "submitted_by": {"username": "kurila"},
        "stats": {"downloads_total": 42},
        "modfile": {
            "id": mod_id * 10,
            "version": version,
            "filename": "%s-9rm7.zip" % name.lower().replace(" ", ""),
            "filesize": len(body),
            "filehash": {"md5": hashlib.md5(body).hexdigest()},
            "download": {"binary_url": "https://api.mod.io/v1/dl/%d" % mod_id, "date_expires": 9},
            "virus_positive": int(flagged),
        },
    }


def _settle(dialog, worker_attr: str = "_worker") -> None:
    """Let every worker the dialog starts run to the end and its signals land.

    A download is two workers now -- the dependency lookup, then the fetches
    -- and the second is started by the first one finishing, so this keeps
    going until nothing is running any more.
    """
    for _ in range(10):
        worker = getattr(dialog, worker_attr)
        if worker is not None:
            worker.wait(10000)
        for _ in range(30):
            _app.processEvents()
        again = getattr(dialog, worker_attr)
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
        patched = mock.patch.object(places, "backups_dir", return_value=self.root / "backups")
        patched.start()
        self.addCleanup(patched.stop)
        self.body = zip_bytes({"[Gameplay] Thing (Kurila)/modinfo.json": '{"Version": "2.0"}'})
        self.bodies = {"https://api.mod.io/v1/dl/7": self.body}
        self.fetched = []

    def chunks(self, url: str):
        self.fetched.append(url)
        body = self.bodies[url]
        for start in range(0, len(body), 100):
            yield body[start:start + 100]


class TestGettingAMod(_Setup):
    def test_search_then_download_puts_the_mod_in_the_folder_and_accepts(self):
        server = FakeServer([_mod_with_file(7, "Thing (Kurila)", "2.0", self.body), _mod_with_file(8, "Other", "1", b"x")])
        dialog = ModioGetDialog(ANNO_1800, self.store, self.mods, fetch=server, chunks=self.chunks, library_dir=library.game_dir(ANNO_1800))
        self.assertFalse(dialog.get_button.isEnabled())
        dialog.query.setText("thing")
        dialog._search()
        _settle(dialog)
        self.assertEqual([m.name for m in dialog.results], ["Thing (Kurila)"])
        self.assertEqual(dialog.table.rowCount(), 1)
        self.assertTrue(dialog.get_button.isEnabled())
        dialog._download()
        _settle(dialog)
        self.assertEqual(dialog.result(), int(QDialog.DialogCode.Accepted))
        self.assertEqual([l.link for l in dialog.installed.linked], [self.mods / "[Gameplay] Thing (Kurila)"])
        self.assertEqual(
            (self.mods / "[Gameplay] Thing (Kurila)" / "modinfo.json").read_text(encoding="utf-8"),
            '{"Version": "2.0"}',
        )
        # The scratch folder went, and the file came from the fresh link.
        self.assertEqual([p.name for p in self.mods.iterdir()], ["[Gameplay] Thing (Kurila)"])
        self.assertEqual(self.fetched, ["https://api.mod.io/v1/dl/7"])
        self.assertEqual(dialog.summary.id, 7)

    def test_a_second_search_with_as_many_results_is_drawn_sorted_too(self):
        # A10-40: the rows were filled in place, which tells the sort
        # nothing, and stood in the first search's order under its arrow.
        names = ["Alpha B", "Alpha C", "Alpha A", "Beta Z", "Beta X", "Beta Y"]
        server = FakeServer([_mod_with_file(10 + n, name, "1", b"x") for n, name in enumerate(names)])
        dialog = ModioGetDialog(ANNO_1800, self.store, self.mods, fetch=server, chunks=self.chunks, library_dir=library.game_dir(ANNO_1800))

        def shown():
            header = dialog.table.verticalHeader()
            return [dialog.table.item(header.logicalIndex(v), 0).text() for v in range(dialog.table.rowCount())]

        dialog.query.setText("alpha")
        dialog._search()
        _settle(dialog)
        dialog.sorting.sort(0)
        self.assertEqual(shown(), ["Alpha A", "Alpha B", "Alpha C"])
        dialog.query.setText("beta")
        dialog._search()
        _settle(dialog)
        self.assertEqual(shown(), ["Beta X", "Beta Y", "Beta Z"])

    def test_a_pasted_address_finds_exactly_that_mod(self):
        server = FakeServer([_mod_with_file(7, "Thing (Kurila)", "2.0", self.body), _mod_with_file(8, "Thing Two", "1", b"x")])
        server.mods[7]["name_id"] = "thing-kurila"
        dialog = ModioGetDialog(ANNO_1800, self.store, self.mods, fetch=server, chunks=self.chunks, library_dir=library.game_dir(ANNO_1800))
        dialog.query.setText("https://mod.io/g/anno-1800/m/thing-kurila?tab=description")
        dialog._search()
        _settle(dialog)
        self.assertEqual([m.id for m in dialog.results], [7])
        self.assertTrue(dialog.get_button.isEnabled())

    def test_an_address_for_the_other_game_is_refused_without_asking(self):
        server = FakeServer()
        dialog = ModioGetDialog(ANNO_1800, self.store, self.mods, fetch=server, library_dir=library.game_dir(ANNO_1800))
        dialog.query.setText("https://mod.io/g/anno-117-pax-romana/m/curia-julia")
        dialog._search()
        self.assertIn("another game", dialog.status.text())
        self.assertEqual(server.calls, [])

    def test_a_mod_that_needs_others_fetches_them_first_when_asked_to(self):
        piece = zip_bytes({"[Shared] Piece/modinfo.json": "{}"})
        self.bodies["https://api.mod.io/v1/dl/8"] = piece
        server = FakeServer(
            [_mod_with_file(7, "Thing (Kurila)", "2.0", self.body), _mod_with_file(8, "Piece", "1.0", piece),
             _mod_with_file(9, "Already Here", "1.0", b"x")],
            deps={7: [8, 9]},
        )
        dialog = ModioGetDialog(ANNO_1800, self.store, self.mods, fetch=server, chunks=self.chunks, known_ids={9}, library_dir=library.game_dir(ANNO_1800))
        asked = []

        def answer(chosen, needs, missing):
            asked.append(([d.id for d in needs], [d.id for d in missing]))
            return "all"

        dialog.ask_dependencies = answer
        dialog.query.setText("thing")
        dialog._search()
        _settle(dialog)
        dialog._download()
        _settle(dialog)
        self.assertEqual(asked, [([8, 9], [8])])
        self.assertEqual(dialog.result(), int(QDialog.DialogCode.Accepted))
        self.assertEqual([l.link for l in dialog.installed.linked], [self.mods / "[Gameplay] Thing (Kurila)"])
        self.assertEqual([l.link.name for _m, done in dialog.also for l in done.linked], ["[Shared] Piece"])
        self.assertEqual(self.fetched, ["https://api.mod.io/v1/dl/8", "https://api.mod.io/v1/dl/7"])

    def test_or_just_the_one_when_told_to(self):
        server = FakeServer([_mod_with_file(7, "Thing (Kurila)", "2.0", self.body), _mod_with_file(8, "Piece", "1", b"x")], deps={7: [8]})
        dialog = ModioGetDialog(ANNO_1800, self.store, self.mods, fetch=server, chunks=self.chunks, library_dir=library.game_dir(ANNO_1800))
        dialog.ask_dependencies = lambda chosen, needs, missing: "one"
        dialog.query.setText("thing")
        dialog._search()
        _settle(dialog)
        dialog._download()
        _settle(dialog)
        self.assertEqual(dialog.also, [])
        self.assertEqual(self.fetched, ["https://api.mod.io/v1/dl/7"])

    def _with_piece_installed(self, server) -> None:
        """Install mod 7 and the Piece it needs (8), as an earlier session did."""
        dialog = ModioGetDialog(ANNO_1800, self.store, self.mods, fetch=server, chunks=self.chunks, library_dir=library.game_dir(ANNO_1800))
        dialog.ask_dependencies = lambda chosen, needs, missing: "all"
        dialog.query.setText("thing")
        dialog._search()
        _settle(dialog)
        dialog._download()
        _settle(dialog)
        self.assertEqual([one.link.name for _m, done in dialog.also for one in done.linked], ["[Shared] Piece"])
        del self.fetched[:]

    def test_a_dependency_installed_and_not_loaded_is_not_fetched_again(self):
        """Only the loadout's layers were asked, so a dependency sitting in
        the mods folder unloaded was offered, downloaded again, and the copy
        that was there set aside."""
        piece = zip_bytes({"[Shared] Piece/modinfo.json": "{}"})
        self.bodies["https://api.mod.io/v1/dl/8"] = piece
        server = FakeServer(
            [_mod_with_file(7, "Thing (Kurila)", "2.0", self.body), _mod_with_file(8, "Piece", "1.0", piece)],
            deps={7: [8]},
        )
        self._with_piece_installed(server)
        # A fresh loadout: nothing is loaded, and both mods are installed.
        dialog = ModioGetDialog(ANNO_1800, self.store, self.mods, fetch=server, chunks=self.chunks, library_dir=library.game_dir(ANNO_1800))
        asked = []
        dialog.ask_dependencies = lambda chosen, needs, missing: asked.append(missing) or "all"
        dialog.query.setText("thing")
        dialog._search()
        _settle(dialog)
        dialog._download()
        _settle(dialog)
        self.assertEqual(asked, [])
        self.assertEqual(dialog.result(), int(QDialog.DialogCode.Accepted))
        self.assertEqual(dialog.also, [])
        self.assertEqual(self.fetched, ["https://api.mod.io/v1/dl/7"])

    def test_the_prompt_says_which_dependencies_are_installed_and_which_loaded(self):
        piece = zip_bytes({"[Shared] Piece/modinfo.json": "{}"})
        self.bodies["https://api.mod.io/v1/dl/8"] = piece
        server = FakeServer(
            [_mod_with_file(7, "Thing (Kurila)", "2.0", self.body), _mod_with_file(8, "Piece", "1.0", piece)],
            deps={7: [8]},
        )
        self._with_piece_installed(server)
        server.mods[9] = _mod_with_file(9, "Loaded One", "1.0", b"x")
        server.mods[10] = _mod_with_file(10, "Not Here", "1.0", b"x")
        server.deps[7] = [8, 9, 10]
        dialog = ModioGetDialog(ANNO_1800, self.store, self.mods, fetch=server, chunks=self.chunks, known_ids={9}, library_dir=library.game_dir(ANNO_1800))
        said = []

        def exec_(box):
            said.append(_isolation.box_text(box))
            return 0

        dialog.query.setText("thing")
        dialog._search()
        _settle(dialog)
        with mock.patch.object(QMessageBox, "exec", exec_):
            dialog._download()
            _settle(dialog)
        self.assertEqual(len(said), 1)
        self.assertEqual(said[0].splitlines(), [
            "Thing (Kurila) needs 3 other mods:",
            "",
            "  • Piece  (already installed)",
            "  • Loaded One  (already loaded)",
            "  • Not Here",
            "",
            "Install the 1 not installed yet first?",
        ])
        # Nobody pressed anything, which is Cancel.
        self.assertEqual(self.fetched, [])
        self.assertEqual(dialog.status.text(), "No mod was installed.")

    def test_the_bar_and_stop_stay_while_a_mod_that_needs_nothing_downloads(self):
        """The download starts in the slot the lookup's result lands in, and
        the lookup's ``finished`` came after it and hid both for the whole
        download."""
        reached, held = threading.Event(), threading.Event()
        self.addCleanup(held.set)

        def chunks(url):
            reached.set()
            held.wait(20)
            yield from self.chunks(url)

        server = FakeServer([_mod_with_file(7, "Thing (Kurila)", "2.0", self.body)])
        dialog = ModioGetDialog(ANNO_1800, self.store, self.mods, fetch=server, chunks=chunks, library_dir=library.game_dir(ANNO_1800))
        dialog.query.setText("thing")
        dialog._search()
        _settle(dialog)
        dialog._download()
        lookup = dialog._worker
        deadline = time.monotonic() + 20
        while not reached.is_set() and time.monotonic() < deadline:
            _app.processEvents()
        self.assertTrue(reached.is_set())
        lookup.wait(10000)
        for _ in range(30):
            _app.processEvents()
        self.assertIsNot(dialog._worker, lookup)
        self.assertTrue(dialog._busy())
        self.assertFalse(dialog.progress.isHidden())
        self.assertFalse(dialog.stop_button.isHidden())
        held.set()
        _settle(dialog)
        self.assertEqual(dialog.result(), int(QDialog.DialogCode.Accepted))
        self.assertTrue(dialog.progress.isHidden())
        self.assertTrue(dialog.stop_button.isHidden())

    def _held_at_the_dependencies(self, deps=None):
        """A dialog whose lookup of what the mod needs is in flight, and the
        Event that lets mod.io answer it."""
        reached, held = threading.Event(), threading.Event()
        self.addCleanup(held.set)
        server = FakeServer([_mod_with_file(7, "Thing (Kurila)", "2.0", self.body),
                             _mod_with_file(8, "Piece", "1", b"x")], deps=deps)

        def fetch(url, headers):
            if url.split("?")[0].endswith("/dependencies"):
                reached.set()
                held.wait(20)
            return server(url, headers)

        dialog = ModioGetDialog(ANNO_1800, self.store, self.mods, fetch=fetch, chunks=self.chunks, library_dir=library.game_dir(ANNO_1800))
        asked = []
        dialog.ask_dependencies = lambda chosen, needs, missing: asked.append(missing) or "all"
        dialog.query.setText("thing")
        dialog._search()
        _settle(dialog)
        dialog._download()
        self.assertTrue(reached.wait(20))
        return dialog, held, asked

    def test_cancel_while_mod_io_is_asked_what_the_mod_needs_installs_nothing(self):
        """A11-145: the request in flight finished, the job came back as if
        never stopped, and the closed dialog went on to download and install
        the mod -- and to ask about what it needs -- after Cancel."""
        for deps in (None, {7: [8]}):
            with self.subTest(deps=deps):
                dialog, held, asked = self._held_at_the_dependencies(deps)
                threading.Timer(0.3, held.set).start()
                dialog.reject()
                _settle(dialog)
                self.assertEqual(dialog.result(), int(QDialog.DialogCode.Rejected))
                self.assertEqual(asked, [])
                self.assertEqual(self.fetched, [])
                self.assertEqual(list(self.mods.iterdir()), [])
                self.assertIsNone(dialog.installed)

    def test_stop_while_mod_io_is_asked_what_the_mod_needs_fetches_nothing(self):
        dialog, held, asked = self._held_at_the_dependencies({7: [8]})
        dialog._stop()
        held.set()
        _settle(dialog)
        self.assertEqual(asked, [])
        self.assertEqual(self.fetched, [])
        self.assertEqual(list(self.mods.iterdir()), [])
        self.assertIsNone(dialog.installed)
        self.assertFalse(dialog._busy())
        self.assertTrue(dialog.stop_button.isHidden())

    def test_without_a_key_nothing_can_be_searched(self):
        self.store.forget()
        dialog = ModioGetDialog(ANNO_1800, self.store, self.mods, fetch=FakeServer(), library_dir=library.game_dir(ANNO_1800))
        self.assertFalse(dialog.search_button.isEnabled())
        self.assertIn("API key", dialog.status.text())

    def test_a_flagged_file_cannot_be_downloaded(self):
        server = FakeServer([_mod_with_file(7, "Thing (Kurila)", "2.0", self.body, flagged=True)])
        dialog = ModioGetDialog(ANNO_1800, self.store, self.mods, fetch=server, chunks=self.chunks, library_dir=library.game_dir(ANNO_1800))
        dialog.query.setText("thing")
        dialog._search()
        _settle(dialog)
        self.assertFalse(dialog.get_button.isEnabled())
        self.assertIn("flagged", dialog.status.text())

    def test_a_bad_download_is_reported_and_leaves_the_folder_untouched(self):
        server = FakeServer([_mod_with_file(7, "Thing (Kurila)", "2.0", self.body)])
        self.bodies["https://api.mod.io/v1/dl/7"] = self.body[:-5]
        dialog = ModioGetDialog(ANNO_1800, self.store, self.mods, fetch=server, chunks=self.chunks, library_dir=library.game_dir(ANNO_1800))
        dialog.query.setText("thing")
        dialog._search()
        _settle(dialog)
        dialog._download()
        _settle(dialog)
        self.assertNotEqual(dialog.result(), int(QDialog.DialogCode.Accepted))
        self.assertIsNone(dialog.installed)
        self.assertIn("bytes", dialog.status.text())
        self.assertEqual(list(self.mods.iterdir()), [])

    def test_a_download_past_two_gigabytes_fits_the_bar(self):
        """Get counted its bar in bytes, which overflow its signed 32 bits
        past 2 GB; the install plan alone counted in kilobytes.  Said through
        the worker, as a download says it: its signal carried ints too, and
        wrapped 3 GB round to minus 1 GB on the way to the bar."""
        dialog = ModioGetDialog(ANNO_1800, self.store, self.mods, fetch=FakeServer(), library_dir=library.game_dir(ANNO_1800))
        seen = []

        def say_once(done, total):
            def job(client, say, stop):
                say(done, total, "")
            return job

        def bar(_result):
            seen.append((dialog.progress.maximum(), dialog.progress.value()))

        dialog._start(say_once(1024 ** 3, 3 * 1024 ** 3), bar)
        _settle(dialog)
        # Below that, counted as it comes.
        dialog._start(say_once(5, 12), bar)
        _settle(dialog)
        self.assertEqual(seen, [(3 * 1024 ** 2, 1024 ** 2), (12, 5)])


class TestUpdatingFromTheCheck(_Setup):
    def _loadout(self, folder: Path) -> Loadout:
        loadout = Loadout(game=ANNO_1800)
        loadout.set_layer(ModLayer(
            origin=folder.name, name="Thing (Kurila)", mod_id="thing_kurila",
            folder=str(folder), version="1.0", modio_id=7,
        ))
        return loadout

    def test_an_installed_mod_is_replaced_by_the_current_file(self):
        old = self.mods / "-[Seal] Thing (Kurila)"
        old.mkdir()
        (old / "modinfo.json").write_text('{"Version": "1.0"}', encoding="utf-8")
        server = FakeServer([_mod_with_file(7, "Thing (Kurila)", "2.0", self.body)])
        dialog = ModioUpdatesDialog(
            self._loadout(old), ANNO_1800, self.store, fetch=server, chunks=self.chunks,
            logged={}, cache={}, library_dir=library.game_dir(ANNO_1800),
        )
        dialog.check_button.click()
        _settle(dialog)
        self.assertEqual(dialog.verdicts[0].status, updates.UPDATE)
        # Ticked, as it is after the check that found the update: no row chosen.
        self.assertTrue(dialog.update_button.isEnabled(), dialog.update_button.toolTip())
        self.assertEqual(dialog.update_button.text().replace("&", ""), "Download and update 1 mod")
        dialog.update_button.click()
        _settle(dialog, "_task")
        self.assertEqual(len(dialog.installed), 1)
        # Same folder name, mark and all; new contents; the old folder kept.
        self.assertEqual((old / "modinfo.json").read_text(encoding="utf-8"), '{"Version": "2.0"}')
        self.assertTrue((dialog.installed[0].set_aside / "modinfo.json").is_file())
        self.assertEqual(dialog.verdicts[0].status, updates.CURRENT)
        self.assertIn("Updated Thing (Kurila) to 2.0", dialog.summary.text())
        self.assertFalse(dialog.update_button.isEnabled())

    def test_an_update_mod_io_gave_no_checksum_for_is_said_unchecked(self):
        old = self.mods / "-[Seal] Thing (Kurila)"
        old.mkdir()
        (old / "modinfo.json").write_text('{"Version": "1.0"}', encoding="utf-8")
        listed = _mod_with_file(7, "Thing (Kurila)", "2.0", self.body)
        del listed["modfile"]["filehash"]
        dialog = ModioUpdatesDialog(
            self._loadout(old), ANNO_1800, self.store, fetch=FakeServer([listed]), chunks=self.chunks,
            logged={}, cache={}, library_dir=library.game_dir(ANNO_1800),
        )
        dialog.check_button.click()
        _settle(dialog)
        dialog.update_button.click()
        _settle(dialog, "_task")
        self.assertEqual((old / "modinfo.json").read_text(encoding="utf-8"), '{"Version": "2.0"}')
        self.assertIn("Updated Thing (Kurila) to 2.0.", dialog.summary.text())
        self.assertIn("mod.io gave no checksum for Thing (Kurila)'s file, so the download couldn't be "
                      "checked.", dialog.summary.text())

    def test_the_download_is_said_as_it_is(self):
        """The update's sentences went through the check's progress, which
        takes any after the first for a mod's name: "Looking up Downloading
        Thing (Kurila)……"."""
        old = self.mods / "-[Seal] Thing (Kurila)"
        old.mkdir()
        (old / "modinfo.json").write_text('{"Version": "1.0"}', encoding="utf-8")
        server = FakeServer([_mod_with_file(7, "Thing (Kurila)", "2.0", self.body)])
        dialog = ModioUpdatesDialog(
            self._loadout(old), ANNO_1800, self.store, fetch=server, chunks=self.chunks,
            logged={}, cache={}, library_dir=library.game_dir(ANNO_1800),
        )
        dialog.check_button.click()
        _settle(dialog)
        said = []
        shown = dialog.summary.setText
        dialog.summary.setText = lambda text: (said.append(text), shown(text))
        dialog.update_button.click()
        _settle(dialog, "_task")
        self.assertIn("Downloading Thing (Kurila)…", said)
        self.assertFalse([text for text in said if text.startswith("Looking up")], said)

    def test_a_copy_the_games_own_browser_installed_is_not_touched(self):
        # A cache of this test's own, not the suite's shared one: a mod left
        # in that would turn up in every later test that reads a mods folder.
        own_cache = mock.patch.dict(os.environ, {"ANNO_STUDIO_MODIO": str(self.root / "modio")})
        own_cache.start()
        self.addCleanup(own_cache.stop)
        cache = self.root / "modio" / "4169" / "mods"
        folder = cache / "7" / "[Gameplay] Thing (Kurila)"
        folder.mkdir(parents=True)
        (folder / "modinfo.json").write_text("{}", encoding="utf-8")
        self.assertEqual(modio_cache.mods_dir(ANNO_1800), cache)
        server = FakeServer([_mod_with_file(7, "Thing (Kurila)", "2.0", self.body)])
        loadout = Loadout(game=ANNO_1800)
        loadout.set_layer(ModLayer(
            origin="7/[Gameplay] Thing (Kurila)", name="Thing (Kurila)", folder=str(folder),
            version="1.0", modio_id=7,
        ))
        dialog = ModioUpdatesDialog(loadout, ANNO_1800, self.store, fetch=server, logged={}, cache={}, library_dir=library.game_dir(ANNO_1800))
        dialog.check_button.click()
        _settle(dialog)
        self.assertFalse(dialog.update_button.isEnabled())
        # Ticked and not chosen, it is counted; chosen, it says why.
        self.assertIn("1 ticked mod with an update", dialog.why_label.text())
        dialog.table.selectRow(0)
        self.assertIn("mod browser", dialog.why_label.text())


class TestWhatTheWindowSaysOfAnInstall(unittest.TestCase):
    def test_a_mod_mod_io_gave_no_checksum_for_is_said_unchecked(self):
        from types import SimpleNamespace

        from anno_modding_studio.core import modio
        from anno_modding_studio.ui.modio_controller import ModioController

        link = Path("C:/mods/Thing")
        dialog = mock.Mock(
            collection=None, also=[(modio.ModSummary(id=8, name="Piece", md5="ab"), mock.Mock(linked=[]))],
            summary=modio.ModSummary(id=7, name="Thing", md5=""),
            installed=mock.Mock(linked=[mock.Mock(link=link)], set_aside=None),
        )
        dialog.exec.return_value = int(QDialog.DialogCode.Accepted)
        window = SimpleNamespace(index=object(), where=mock.Mock(), status_label=mock.Mock(),
                                 read_installed=mock.Mock(return_value=True))
        controller = ModioController(window)
        with mock.patch.object(controller, "_get_dialog", return_value=dialog):
            self.assertTrue(controller.get_mod_from_modio())
        said = window.status_label.say.call_args.args[0]
        self.assertIn("mod.io gave no checksum for Thing's file, so the download couldn't be checked.", said)
        self.assertNotIn("Piece's file", said)


if __name__ == "__main__":
    unittest.main()
