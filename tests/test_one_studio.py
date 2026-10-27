"""One Studio open over one set of its folders, and a second launch handing over.

Two Studios over the same folders wrote over each other's settings, built and
pruned the same cache and junctioned into the same mods folder.  The lock is
the operating system's on an open file, so it is tried here from another
process too: one that is killed must leave nothing behind.

No test here takes the real lock: each names a folder of its own.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

import _isolation  # noqa: F401 -- first: keeps the suite out of real files under any runner

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from PySide6.QtWidgets import QApplication  # noqa: E402

from anno_modding_studio.ui import one_studio  # noqa: E402

_app = QApplication.instance() or QApplication([])

# A process that takes the lock, says so, and waits to be ended.
_HOLDER = """
import sys
sys.path.insert(0, sys.argv[1])
from pathlib import Path
from anno_modding_studio.ui import one_studio
lock = one_studio.Lock(Path(sys.argv[2]))
print("taken" if lock.take() else "refused", flush=True)
sys.stdin.read()
"""

# A second launch, handing over to the first: in a process of its own, as it
# is, since the first answers from its event loop.
_SECOND = """
import sys
sys.path.insert(0, sys.argv[1])
from PySide6.QtCore import QCoreApplication
from anno_modding_studio.ui import one_studio
app = QCoreApplication([])
said = one_studio.hand_over(sys.argv[2], sys.argv[3], wait_ms=0)
print("no answer" if said is None else "answered " + said, flush=True)
"""
# And one handing over a path made longer, past what a command line holds.
_SECOND_LONG = _SECOND.replace("sys.argv[3]", "sys.argv[3] + 'y' * int(sys.argv[4])")


class TestTheLock(unittest.TestCase):
    def setUp(self):
        self.folder = Path(tempfile.mkdtemp(prefix="ams-lock-"))
        self.path = self.folder / one_studio.LOCK_NAME
        self.locks = []

    def tearDown(self):
        for lock in self.locks:
            lock.release()

    def lock(self, path=None):
        lock = one_studio.Lock(path or self.path)
        self.locks.append(lock)
        return lock

    def holder(self):
        process = subprocess.Popen(
            [sys.executable, "-c", _HOLDER, str(ROOT), str(self.path)],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True,
        )
        self.addCleanup(process.wait, 10)
        self.addCleanup(process.kill)
        return process, process.stdout.readline().strip()

    def test_a_second_is_refused_while_the_first_holds_it(self):
        self.assertTrue(self.lock().take())
        self.assertFalse(self.lock().take())

    def test_let_go_it_can_be_taken_again(self):
        first = self.lock()
        self.assertTrue(first.take())
        first.release()
        self.assertFalse(first.held)
        self.assertTrue(self.lock().take())

    def test_another_process_is_refused(self):
        self.assertTrue(self.lock().take())
        _process, said = self.holder()
        self.assertEqual(said, "refused")

    def test_a_studio_that_died_leaves_no_lock(self):
        process, said = self.holder()
        self.assertEqual(said, "taken")
        self.assertFalse(self.lock().take())
        process.kill()
        process.wait(10)
        self.assertTrue(self.lock().take(), "the lock went with the process")

    def test_a_folder_that_cannot_be_written_does_not_stop_the_studio(self):
        # A file where the folder would be: nothing can be made under it.
        blocked = self.folder / "a file"
        blocked.write_text("", encoding="utf-8")
        lock = self.lock(blocked / one_studio.LOCK_NAME)
        self.assertTrue(lock.take())
        self.assertFalse(lock.held)

    def test_it_is_in_the_folder_the_studio_keeps_for_itself(self):
        with mock.patch.dict(os.environ, {"ANNO_STUDIO_FOLDER": str(self.folder)}):
            self.assertEqual(one_studio.lock_path(), self.path)


class TestTheName(unittest.TestCase):
    def test_one_name_for_each_folder(self):
        first, second = Path(tempfile.mkdtemp()), Path(tempfile.mkdtemp())
        self.assertNotEqual(one_studio.server_name(first / "open.lock"),
                            one_studio.server_name(second / "open.lock"))

    def test_the_same_however_the_folder_is_spelt(self):
        folder = Path(tempfile.mkdtemp())
        (folder / "inner").mkdir()
        spelt = folder / "inner" / ".." / "open.lock"
        self.assertEqual(one_studio.server_name(folder / "open.lock"), one_studio.server_name(spelt))
        if sys.platform == "win32":
            self.assertEqual(one_studio.server_name(folder / "open.lock"),
                             one_studio.server_name(Path(str(folder).upper()) / "open.lock"))


class TestHandingOver(unittest.TestCase):
    def setUp(self):
        self.name = "%s-test-%d-%d" % (one_studio.places.NAME, os.getpid(), time.monotonic_ns())
        self.received = []

    def listen(self):
        server = one_studio.listen(self.name, self.received.append, _app)
        self.addCleanup(server.close)
        self.assertTrue(server.isListening())

    def second(self, message="", longer=0):
        """What a second launch said, with the first answering meanwhile:
        handing over ``message``, with ``longer`` more letters after it."""
        script, more = (_SECOND_LONG, [str(longer)]) if longer else (_SECOND, [])
        process = subprocess.Popen(
            [sys.executable, "-c", script, str(ROOT), self.name, message, *more],
            stdout=subprocess.PIPE, text=True, encoding="utf-8",
        )
        self.addCleanup(process.kill)
        deadline = time.monotonic() + 20
        while process.poll() is None and time.monotonic() < deadline:
            QApplication.processEvents()
            time.sleep(0.01)
        deadline = time.monotonic() + 5
        while not self.received and time.monotonic() < deadline:
            QApplication.processEvents()
            time.sleep(0.01)
        return process.communicate(timeout=5)[0].strip()

    def test_the_first_is_told_to_come_forward_and_says_which_it_is(self):
        from anno_modding_studio import __version__

        self.listen()
        self.assertEqual(self.second(), "answered " + __version__)
        self.assertEqual(self.received, [""])

    def test_and_given_the_file_the_second_was(self):
        from anno_modding_studio import __version__

        self.listen()
        path = str(Path(tempfile.mkdtemp()) / "assets ä.xml")
        self.assertEqual(self.second(path), "answered " + __version__)
        self.assertEqual(self.received, [path])

    def test_only_this_user_may_hand_over(self):
        server = one_studio.listen(self.name, self.received.append, _app)
        self.addCleanup(server.close)
        self.assertTrue(server.socketOptions() & one_studio.QLocalServer.SocketOption.UserAccessOption)

    def test_a_message_longer_than_any_path_is_dropped_unread(self):
        """No path is 32 KB long: what is, is not a second launch's, and the
        first read it to its end, however long (security review S12)."""
        self.listen()
        self.assertIn(self.second("C:/", longer=one_studio.MOST_HANDED), ("answered", "no answer"))
        self.assertEqual(self.received, [])
        self.assertTrue(self.second("C:/", longer=one_studio.MOST_HANDED - 10).startswith("answered "))
        self.assertEqual(self.received, ["C:/" + "y" * (one_studio.MOST_HANDED - 10)])

    def test_a_write_done_before_the_wait_is_still_answered(self):
        """On a busy machine the line is often in the pipe before the wait
        for it begins, and the wait says False with nothing left to write."""

        class Gone:
            """A socket whose write went before it was waited for."""

            def __init__(self):
                self.said = [b"0.1.0\n"]

            def connectToServer(self, name):
                pass

            def waitForConnected(self, ms):
                return True

            def write(self, data):
                return len(data)

            def waitForBytesWritten(self, ms):
                return False

            def bytesToWrite(self):
                return 0

            def waitForReadyRead(self, ms):
                return bool(self.said)

            def readAll(self):
                return self.said.pop() if self.said else b""

            def disconnectFromServer(self):
                pass

        with mock.patch.object(one_studio, "QLocalSocket", Gone):
            self.assertEqual(one_studio.hand_over(self.name, "C:/a.xml", wait_ms=0), "0.1.0")

    def test_nobody_listening_is_no_answer(self):
        started = time.monotonic()
        self.assertIsNone(one_studio.hand_over(self.name, wait_ms=400))
        self.assertLess(time.monotonic() - started, 4)

    def test_a_first_of_another_version_is_named(self):
        """An update unzipped beside the old one shares its folders, so
        its lock: started while the old one is open, it says so rather
        than ending as though it had started."""
        with mock.patch.object(one_studio, "__version__", "0.0.9"):
            self.listen()
            self.assertEqual(self.second(), "answered 0.0.9")
        with mock.patch.object(one_studio.QMessageBox, "information") as said:
            one_studio.say_another_version_is_open("0.0.9")
        self.assertIn("0.0.9 is already open", said.call_args.args[2])

    def test_what_the_first_says_is_shown_only_as_a_version(self):
        server = one_studio.QLocalServer(_app)
        self.addCleanup(server.close)
        self.assertTrue(server.listen(self.name))

        # Answering only once the second's line is in, as a Studio does: one
        # that hung up as soon as it was connected to was sometimes gone
        # before the second's line was written, and the second heard nothing.
        def answer() -> None:
            socket = server.nextPendingConnection()
            heard = bytearray()

            def read() -> None:
                heard.extend(bytes(socket.readAll()))
                if heard.endswith(b"\n"):
                    socket.write(b'<img src="file://host/a.png">\n')
                    socket.flush()
                    socket.disconnectFromServer()

            socket.readyRead.connect(read)
            read()

        server.newConnection.connect(answer)
        self.assertEqual(self.second(), "answered")

    def test_a_minimised_window_comes_back(self):
        from PySide6.QtWidgets import QMainWindow

        window = QMainWindow()
        window.showMinimized()
        one_studio.come_forward(window)
        self.assertFalse(window.isMinimized())
        self.assertTrue(window.isVisible())


class TestTheAppTakesIt(unittest.TestCase):
    """Before the window is made, so a second launch builds nothing."""

    def test_the_lock_comes_before_the_window(self):
        source = (ROOT / "anno_modding_studio" / "app.py").read_text(encoding="utf-8")
        self.assertLess(source.index("lock.take()"), source.index("MainWindow()"))

    def test_a_built_studio_says_it_does_not_use_the_folder_beside_it(self):
        source = (ROOT / "anno_modding_studio" / "app.py").read_text(encoding="utf-8")
        self.assertLess(source.index("one_studio.say_if_beside_is_unused()"),
                        source.index("MainWindow()"))
        beside = Path(tempfile.mkdtemp(prefix="ams-beside-"))
        self.addCleanup(shutil.rmtree, beside, True)
        with mock.patch.object(one_studio.QMessageBox, "information") as said:
            one_studio.say_if_beside_is_unused()
            said.assert_not_called()
            with mock.patch.object(one_studio.places, "unused_beside", return_value=beside):
                one_studio.say_if_beside_is_unused()
        self.assertIn(str(beside), said.call_args.args[2])
        self.assertIn("ANNO_STUDIO_FOLDER", said.call_args.args[2])

    def test_a_studio_under_a_wine_without_junctions_says_so_once_for_each_wine(self):
        source = (ROOT / "anno_modding_studio" / "app.py").read_text(encoding="utf-8")
        self.assertLess(source.index("one_studio.say_if_wine_makes_no_junctions("),
                        source.index("MainWindow()"))

        class Settings(dict):
            def value(self, key, default=None):
                return self.get(key, default)

            def setValue(self, key, value):
                self[key] = value

            def sync(self):
                pass

        settings = Settings()
        with mock.patch.object(one_studio.QMessageBox, "warning") as said:
            one_studio.say_if_wine_makes_no_junctions(settings)
            said.assert_not_called()
            with mock.patch.object(one_studio.wine, "version", return_value="10.0"):
                one_studio.say_if_wine_makes_no_junctions(settings)
                one_studio.say_if_wine_makes_no_junctions(settings)
            self.assertEqual(said.call_count, 1)
            self.assertIn("10.0", said.call_args.args[2])
            with mock.patch.object(one_studio.wine, "version", return_value="9.0"):
                one_studio.say_if_wine_makes_no_junctions(settings)
            self.assertEqual(said.call_count, 2)
            with mock.patch.object(one_studio.wine, "version", return_value="11.0"):
                one_studio.say_if_wine_makes_no_junctions(settings)
            self.assertEqual(said.call_count, 2)

    def test_and_the_data_s_version_after_the_lock_and_before_the_window(self):
        source = (ROOT / "anno_modding_studio" / "app.py").read_text(encoding="utf-8")
        asked = source.index("one_studio.may_use_data()")
        self.assertLess(source.index("lock.take()"), asked)
        self.assertLess(asked, source.index("MainWindow()"))

    def test_and_half_done_saves_are_settled_after_the_data_s_version_and_before_the_window(self):
        source = (ROOT / "anno_modding_studio" / "app.py").read_text(encoding="utf-8")
        settled = source.index("one_studio.finish_saves()")
        self.assertLess(source.index("one_studio.may_use_data()"), settled)
        self.assertLess(settled, source.index("MainWindow()"))

    def test_a_loadout_it_is_given_opens_as_a_loadout(self):
        """Opened with the Studio from Explorer, a loadout was read as the
        game's assets.xml, and said it was not whole XML."""
        from anno_modding_studio import app

        window = mock.Mock()
        loadout = Path("C:/Loadouts/Main.ANNOLOAD")
        app.open_given(window, loadout)
        window.load_loadout.assert_called_once_with(loadout)
        window.game_files.open_assets.assert_not_called()
        window.reset_mock()
        assets = Path("C:/Game/assets.xml")
        app.open_given(window, assets)
        window.game_files.open_assets.assert_called_once_with(assets)
        window.load_loadout.assert_not_called()
        # Both ways in: started with it, and handed it by a second launch.
        source = (ROOT / "anno_modding_studio" / "app.py").read_text(encoding="utf-8")
        self.assertEqual(source.count("open_given(shown[0], "), 1)
        self.assertEqual(source.count("open_given(window, given)"), 1)
        self.assertNotIn("game_files.open_assets(given)", source)

    def test_a_save_that_cannot_be_settled_is_said(self):
        with mock.patch.object(one_studio.QMessageBox, "warning") as said:
            with mock.patch.object(one_studio.saving, "recover", return_value=[]):
                one_studio.finish_saves()
            said.assert_not_called()
            with mock.patch.object(one_studio.saving, "recover", return_value=["Stuck."]):
                one_studio.finish_saves()
        self.assertIn("Stuck.", said.call_args.args[2])


class TestDataANewerStudioWrote(unittest.TestCase):
    """A build older than the data it finds says so and opens nothing."""

    def setUp(self):
        self.folder = Path(tempfile.mkdtemp(prefix="ams-data-"))
        self.addCleanup(shutil.rmtree, self.folder, True)
        patcher = mock.patch.dict(os.environ, {"ANNO_STUDIO_FOLDER": str(self.folder)})
        patcher.start()
        self.addCleanup(patcher.stop)
        # The home folder named for the suite would win over the one folder.
        home = os.environ.pop("ANNO_STUDIO_HOME", None)
        if home is not None:
            self.addCleanup(os.environ.__setitem__, "ANNO_STUDIO_HOME", home)

    def test_a_newer_data_version_is_refused_with_who_wrote_it(self):
        from anno_modding_studio.core import dataversion, disk

        disk.write_json(self.folder / dataversion.FILE_NAME,
                        {"data_version": dataversion.DATA_VERSION + 1, "app_version": "0.7.0"})
        with mock.patch.object(one_studio.QMessageBox, "critical") as said:
            self.assertFalse(one_studio.may_use_data())
        self.assertIn("last used by version 0.7.0", said.call_args.args[2])
        self.assertEqual(disk.read_json(self.folder / dataversion.FILE_NAME, {})["app_version"], "0.7.0")

    def test_otherwise_it_opens_and_says_it_used_the_folder(self):
        from anno_modding_studio import __version__
        from anno_modding_studio.core import dataversion, disk

        with mock.patch.object(one_studio.QMessageBox, "critical") as said:
            self.assertTrue(one_studio.may_use_data())
        said.assert_not_called()
        self.assertEqual(disk.read_json(self.folder / dataversion.FILE_NAME, {})["app_version"], __version__)


if __name__ == "__main__":
    unittest.main(verbosity=2)
