"""An update to a mod you edited: each op of theirs an edit of yours was made
to is looked for in the new version, and an edit whose op changed or went is
held back until you decide (docs/proposals/patches-proposal.md, phase 3).

Junctions are a Windows thing, as is the game, so the library's half is too.
"""

from __future__ import annotations

import json
import os
import sys
import unittest
from dataclasses import replace
from pathlib import Path

import _isolation  # first: keeps the suite out of real files under any runner

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_edits import NAME, PATCH, THEIRS, _Edited  # noqa: E402

from anno_modding_studio.core import edits, held, library, reader, stale  # noqa: E402
from anno_modding_studio.core.changes import Loadout  # noqa: E402

RADIUS = "<ModOp Type=\"replace\" GUID=\"1010030\" Path=\"/Values/Standard/Name\">"


class _Updated(_Edited):
    def type_over(self, words: str, new: str):
        """As the editor does it: theirs taken out, yours in its place."""
        op = self.op(words)
        mine = replace(op, source=op.source.replace(words, new), authored=True,
                       uid=op.uid + "m", within=list(op.within))
        self.loadout.drop_op(op.uid)
        self.loadout.set_op(mine)
        return mine

    def update(self, text: str):
        """The author's new version lands, and is read in where it stands."""
        (self.copy / PATCH).write_text(text, encoding="utf-8")
        report = reader.analyse(self.link, self.index, origin=NAME)
        stale.take(self.loadout, {NAME: report})

    def theirs(self):
        return [op for op in self.loadout.ops if op.origin == NAME and not op.authored]


class TestAnUpdateFindsTheOp(_Updated):
    def test_unchanged_but_moved_the_edit_stands(self):
        mine = self.type_over("<Name>Theirs</Name>", "<Name>Mine</Name>")
        # Their two ops the other way round.
        head, tail = THEIRS.split("  <ModOp Type=\"replace\"")
        replaced = "  <ModOp Type=\"replace\"" + tail.split("</ModOps>")[0]
        self.update("<ModOps>\n" + replaced + head.split("<ModOps>\n")[1] + "</ModOps>\n")
        self.assertEqual(self.loadout.held_edits, [])
        self.assertTrue(self.loadout.op(mine.uid).active)
        self.assertFalse(any("<Name>Theirs</Name>" in op.source for op in self.theirs()))

    def test_unchanged_in_a_new_group_it_is_taken_out_where_it_now_is(self):
        self.type_over("<Name>Theirs</Name>", "<Name>Mine</Name>")
        grouped = THEIRS.replace(RADIUS, '<Group Condition="#other">' + RADIUS).replace(
            "<Name>Theirs</Name>\n  </ModOp>", "<Name>Theirs</Name>\n  </ModOp></Group>")
        self.update(grouped)
        self.assertEqual(self.loadout.held_edits, [])
        self.assertFalse(any("<Name>Theirs</Name>" in op.source for op in self.theirs()))

    def test_an_op_switched_off_stays_off(self):
        self.op("<Residents>99</Residents>").active = False
        self.update(THEIRS.replace("<!-- the author's -->", "<!-- 1.1 -->"))
        (op,) = [op for op in self.theirs() if "<Residents>99</Residents>" in op.source]
        self.assertFalse(op.active)
        self.assertEqual(self.loadout.held_edits, [])


class TestAnUpdateChangesTheOp(_Updated):
    def changed(self):
        mine = self.type_over("<Name>Theirs</Name>", "<Name>Mine</Name>")
        self.update(THEIRS.replace("<Name>Theirs</Name>", "<Name>Theirs 1.1</Name>"))
        (record,) = self.loadout.held_edits
        return mine, record

    def test_the_edit_is_held_and_their_new_version_runs(self):
        mine, record = self.changed()
        self.assertEqual((record["kind"], record["why"]), (held.REPLACED, held.CHANGED))
        self.assertIn("<Name>Theirs</Name>", record["was"])
        self.assertIn("<Name>Theirs 1.1</Name>", record["now"])
        self.assertFalse(self.loadout.op(mine.uid).active)
        self.assertTrue(any("Theirs 1.1" in op.source and op.active for op in self.theirs()))
        # Nothing of yours goes into the generated copy while it is held.
        done = edits.work_out(self.loadout, NAME, self.copy, self.index)
        self.assertNotIn(b"<Name>Mine</Name>", b"".join(done.written.values()))

    def test_used_on_the_new_version_theirs_goes_and_yours_is_back(self):
        mine, record = self.changed()
        held.apply_mine(self.loadout, record)
        self.assertEqual(self.loadout.held_edits, [])
        self.assertTrue(self.loadout.op(mine.uid).active)
        self.assertFalse(any("Theirs 1.1" in op.source for op in self.theirs()))
        self.assertTrue(any("Theirs 1.1" in entry[1] for entry in self.loadout.taken_out))
        done = edits.work_out(self.loadout, NAME, self.copy, self.index)
        text = done.written[PATCH].decode("utf-8")
        self.assertIn("<Name>Mine</Name>", text)
        self.assertIn('Path="/Values/Standard/Name" Skip="1">', text)

    def test_keeping_theirs_drops_yours(self):
        mine, record = self.changed()
        held.keep_theirs(self.loadout, record)
        self.assertEqual(self.loadout.held_edits, [])
        self.assertIsNone(self.loadout.op(mine.uid))
        self.assertFalse(edits.has_edits(self.loadout, NAME))

    def test_an_op_taken_out_and_then_gone_is_held_as_gone(self):
        self.loadout.drop_op(self.op("<Name>Theirs</Name>").uid)
        self.update(THEIRS.split("  <ModOp Type=\"replace\"")[0] + "</ModOps>\n")
        (record,) = self.loadout.held_edits
        self.assertEqual((record["kind"], record["why"], record["now"]), (held.TAKEN_OUT, held.GONE, ""))
        held.keep_theirs(self.loadout, record)
        self.assertEqual(self.loadout.taken_out, [])

    def test_an_op_switched_off_and_then_changed_is_held(self):
        self.op("<Name>Theirs</Name>").active = False
        self.update(THEIRS.replace("<Name>Theirs</Name>", "<Name>Theirs 1.1</Name>"))
        (record,) = self.loadout.held_edits
        self.assertEqual(record["kind"], held.SWITCHED_OFF)
        held.apply_mine(self.loadout, record)
        (op,) = [op for op in self.theirs() if "Theirs 1.1" in op.source]
        self.assertFalse(op.active)

    def test_held_edits_are_saved_with_the_loadout(self):
        self.changed()
        path = self.root / "p.annoload"
        self.loadout.save(path)
        again = Loadout.load(path)
        self.assertEqual(again.held_edits, self.loadout.held_edits)
        self.assertEqual(again.taken_out, self.loadout.taken_out)


class TestTheSameOp(unittest.TestCase):
    """What names an op from one version to the next, however it is spelt."""

    def test_an_op_in_anno_117_shorthand_is_the_same_op_changed(self):
        short = '<ModOp Merge="//A"><X>1</X></ModOp>'
        self.assertTrue(held.same_op(short, short))
        self.assertTrue(held.same_op(short, '<ModOp Merge="//A"><X>2</X></ModOp>'))
        self.assertTrue(held.same_op(short, '<ModOp Type="merge" Path="//A"><X>2</X></ModOp>'))
        self.assertFalse(held.same_op(short, '<ModOp Replace="//A"><X>1</X></ModOp>'))

    def test_a_path_with_a_bracket_or_an_entity_is_read_as_written(self):
        one = '<ModOp Type="replace" GUID="1" Path="/Values[a &gt; 1]/Name"><Name/></ModOp>'
        self.assertTrue(held.same_op(one, one.replace("&gt;", "&#62;")))

    def test_a_modopid_names_it_and_nothing_aimed_names_nothing(self):
        self.assertTrue(held.same_op('<ModOp ModOpID="x" Type="add" GUID="1"/>',
                                     '<ModOp ModOpID="x" Type="replace" GUID="2"/>'))
        self.assertFalse(held.same_op('<ModOp ModOpID="x" Type="add" GUID="1"/>',
                                      '<ModOp Type="add" GUID="1"/>'))
        self.assertFalse(held.same_op('<ModOp Type="add"/>', '<ModOp Type="add"/>'))


class TestThePreviousVersion(_Updated):
    def test_an_update_keeps_the_text_of_the_version_it_replaces(self):
        import io
        import zipfile

        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as zipped:
            zipped.writestr("%s/modinfo.json" % NAME, json.dumps({"ModID": "crowded", "Version": "2"}))
            zipped.writestr("%s/%s" % (NAME, PATCH), THEIRS.replace("99", "100"))
        archive = self.root / "update.zip"
        archive.write_bytes(buffer.getvalue())
        library.install(archive, self.library, self.mods, replace=self.link)
        kept = library.previous_of(self.library, NAME)
        self.assertEqual((kept / PATCH).read_text(encoding="utf-8"), THEIRS)
        self.assertIn("100", (self.copy / PATCH).read_text(encoding="utf-8"))
        self.assertEqual(kept.parent, self.root / "library" / "previous" / "anno1800")


@unittest.skipUnless(os.name == "nt", "junctions are a Windows thing")
class TestTheDialog(_Updated):
    def setUp(self):
        super().setUp()
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication

        self.app = QApplication.instance() or QApplication([])
        self.addCleanup(_isolation.forget_settings)

    def test_it_shows_the_three_and_decides_one_at_a_time(self):
        from anno_modding_studio.ui.held_dialog import HeldDialog

        self.type_over("<Name>Theirs</Name>", "<Name>Mine</Name>")
        self.update(THEIRS.replace("<Name>Theirs</Name>", "<Name>Theirs 1.1</Name>"))
        decided = []
        dialog = HeldDialog(self.loadout, None, done=lambda: decided.append(True))
        self.addCleanup(dialog.deleteLater)
        self.assertEqual(dialog.list.count(), 1)
        self.assertIn("an op you typed over, changed by the update", dialog.list.item(0).text())
        self.assertIn("<Name>Theirs</Name>", dialog.was.toPlainText())
        self.assertIn("<Name>Theirs 1.1</Name>", dialog.now.toPlainText())
        self.assertIn("<Name>Mine</Name>", dialog.yours.toPlainText())
        self.assertEqual(dialog.mine_button.text(), "&Use mine on the new version")
        dialog.mine_button.click()
        self.assertEqual((decided, self.loadout.held_edits), ([True], []))


class _Reading:
    """The mods folder dialog, answered: the mod read where it stands."""

    report = None
    detour = None

    def __init__(self, *args, **kwargs):
        from PySide6.QtWidgets import QLineEdit

        self.settings = {}
        self.folder_box = QLineEdit(kwargs.get("start") or "")
        self.layers, self.ops, self.changes, self.unread = [], [], [], []

    def reload(self):
        pass

    def exec(self):
        from PySide6.QtWidgets import QDialog

        from anno_modding_studio.core.changes import ModLayer

        report = type(self).report
        self.layers = [ModLayer(origin=NAME, name=NAME, mod_id="crowded", folder=str(_Reading.link))]
        self.ops, self.changes = list(report.ops), list(report.changes)
        self.unread = list(report.unread(NAME))
        return int(QDialog.DialogCode.Accepted)


@unittest.skipUnless(os.name == "nt", "junctions are a Windows thing")
class TestReadingTheFolderAfterAnUpdate(_Updated):
    """The read of the mods folder that follows Download and update."""

    def setUp(self):
        super().setUp()
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from unittest import mock

        from PySide6.QtWidgets import QApplication, QMessageBox

        from anno_modding_studio.ui.main_window import MainWindow

        self.app = QApplication.instance() or QApplication([])
        self.window = MainWindow()
        self.addCleanup(_isolation.forget_settings)
        self.addCleanup(self.window.close)
        self.addCleanup(self.window.files.mark_saved)
        self.window.game_files.adopt_index(self.db, self.assets, cached=True)
        for patched in (
            mock.patch.object(self.window.where, "library_dir", return_value=self.library),
            mock.patch("anno_modding_studio.ui.main_window.ModsFolderDialog", _Reading),
            mock.patch.object(self.window.live, "live_profile_here", return_value=None),
            mock.patch.object(QMessageBox, "information", staticmethod(lambda *a: None)),
        ):
            patched.start()
            self.addCleanup(patched.stop)
        self.window.settings.setValue(self.window.mods_key("last_mods_folder"), str(self.mods))
        _Reading.link = self.link
        self.read()
        self.loadout = self.window.loadout

    def read(self):
        _Reading.report = reader.analyse(self.link, self.index, origin=NAME)
        self.assertTrue(self.window.load_installed_mods(keep_switches=True))

    def test_an_op_switched_off_stays_off_and_a_changed_one_holds_your_edit(self):
        from unittest import mock

        self.op("<Residents>99</Residents>").active = False
        mine = self.type_over("<Name>Theirs</Name>", "<Name>Mine</Name>")
        (self.copy / PATCH).write_text(THEIRS.replace("<Name>Theirs</Name>", "<Name>Theirs 1.1</Name>"),
                                       encoding="utf-8")
        with mock.patch.object(self.window, "review_held_edits") as asked:
            self.read()
        asked.assert_called_once_with()
        (switched,) = [op for op in self.window.loadout.ops if "<Residents>99</Residents>" in op.source]
        self.assertFalse(switched.active)
        (record,) = self.window.loadout.held_edits
        self.assertEqual(record["why"], held.CHANGED)
        self.assertFalse(self.window.loadout.op(mine.uid).active)
        self.assertIn("1 edit of yours is held", self.window.status_label.text())


if __name__ == "__main__":
    unittest.main()
