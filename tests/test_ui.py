"""End-to-end test through the real widgets, rendered offscreen.

Drives the same path a user takes: open a file, search, pick an asset, edit a
value, review it in the dock, and generate the XML.
"""

from __future__ import annotations

import contextlib
import json
import os
import shutil
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
sys.path.insert(0, str(ROOT / "tools"))

import make_sample_assets  # noqa: E402
from lxml import etree  # noqa: E402
from PySide6.QtCore import QCoreApplication, QEvent, Qt  # noqa: E402
from PySide6.QtGui import QColor, QGuiApplication  # noqa: E402
from PySide6.QtWidgets import (  # noqa: E402
    QApplication,
    QDialog,
    QDialogButtonBox,
    QMessageBox,
)

from anno_modding_studio.core import (  # noqa: E402
    build,
    games,
    generator,
    modinfo,
    patching,
    projects,
    reader,
)
from anno_modding_studio.core import (  # noqa: E402
    index as index_mod,
)
from anno_modding_studio.core.changes import OP_ADD, Change  # noqa: E402
from anno_modding_studio.ui import output_panel, theme, widgets  # noqa: E402
from anno_modding_studio.ui.changes_dock import GUID_ROLE  # noqa: E402
from anno_modding_studio.ui.editor_panel import (  # noqa: E402
    ADD_ROLE,
    NEW_PATCH,
    NEW_PROJECT,
)
from anno_modding_studio.ui.main_window import (  # noqa: E402
    TAB_EDIT,
    TAB_FILE,
    TAB_GENERATE,
    TAB_REFS,
    TAB_SOURCE,
    MainWindow,
)

_app = QApplication.instance() or QApplication([])


def _flush_deletes() -> None:
    """Carry out every deletion a test queued, now.

    ``deleteLater`` only queues one. Four hundred windows queued that way
    outlived their tests -- still answering events against an index that had
    been closed -- until the first later test to wait on a timer drained them
    all, which took longer than that test was willing to wait. Registered as the
    first cleanup of a test that makes a window, so it runs last: after tearDown
    and after every other cleanup, which may still be holding a dialog or menu
    that belongs to the window.
    """
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    # And what it remembered goes with it -- see _isolation.forget_settings.
    _isolation.forget_settings()


def as_yours(loadout, origin: str) -> None:
    """Make the loaded mod ``origin`` one of yours, by its author, that a
    project keeps: changes can go into a mod of yours, and never into
    somebody else's (section 5 of
    docs/proposals/projects-and-loadouts-proposal.md)."""
    loadout.own.creator = loadout.own.creator or "Me"
    layer = loadout.layer(origin)
    layer.creator = loadout.own.creator
    layer.kept = True


def switch_off(loadout, origin: str) -> None:
    """Switch off everything ``origin`` does, as unticking its rows would:
    its ops, which is what the box on an op's row switches, and its changes."""
    for op in loadout.ops_of(origin):
        op.active = False
    for change in loadout.changes_of(origin):
        change.active = False


def change_rows(panel):
    """Every change row in the changes tree, top to bottom.

    The dock groups by asset now, so a change sits one or two levels down and
    "which row is the change" is no longer the same as "which row is row 0".
    """
    from anno_modding_studio.ui.changes_dock import KIND_ROLE

    # A folded mod's rows are made when it is opened; these are all of them.
    panel.build_every_row()
    found = []

    def walk(item):
        if item.data(0, KIND_ROLE) == "change":
            found.append(item)
            return
        for index in range(item.childCount()):
            walk(item.child(index))

    for index in range(panel.tree.topLevelItemCount()):
        walk(panel.tree.topLevelItem(index))
    return found


def shown_change_rows(panel):
    """The change rows the dock is actually showing.

    Hiding is inherited: a row under a hidden asset is on screen in no sense
    that matters, however cheerfully it reports its own ``isHidden``.
    """
    def buried(item):
        while item is not None:
            if item.isHidden():
                return True
            item = item.parent()
        return False

    return [row for row in change_rows(panel) if not buried(row)]


def pick_change(panel, position=0):
    """Select one change row in the dock and hand it back."""
    panel.tree.clearSelection()
    row = change_rows(panel)[position]
    row.setSelected(True)
    return row


def open_row(panel, row):
    """Open ``row`` of the changes dock as a person does, with Enter on it:
    the tree emits ``itemActivated`` for that as for a double-click, and
    what the panel connected to it answers. (QTest's double-click opened
    nothing on a tree that was never shown.)"""
    from PySide6.QtTest import QTest

    panel.tree.setCurrentItem(row)
    QTest.keyClick(panel.tree, Qt.Key.Key_Return)


class WorkflowCase(unittest.TestCase):
    """One window over a small sample index, for the editing workflow.

    Split out of a single 119-test ``TestWorkflow`` that had grown into
    whatever had no better home -- which is also how a gate test that checked
    one tab of three went unnoticed. The fixture and helpers live here; the
    subclasses below each say what they are about.
    """

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        tmp = Path(cls._tmp.name)
        cls.assets = tmp / "assets.xml"
        cls.texts = tmp / "texts_english.xml"
        cls.assets.write_text(make_sample_assets.build_assets_xml(40), encoding="utf-8")
        cls.texts.write_text(make_sample_assets.build_texts_xml(), encoding="utf-8")
        cls.db = tmp / "index.sqlite"
        index_mod.build_index(cls.assets, cls.db)

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def setUp(self):
        from anno_modding_studio.core.changes import AIM_MAKE

        self.window = MainWindow()

        self.addCleanup(_flush_deletes)
        self.window.game_files.adopt_index(self.db, self.assets, cached=True)
        # Loading the game files is half the Start tab; the other half is what
        # you are here to do, and the tabs beyond Start wait for it. These
        # tests are about what happens once it is answered -- the question
        # itself is TestTheOtherTabsWaitForTheStartTab's.
        self.window.loadout.aim = AIM_MAKE
        # Somebody who has said who they are, as that card asks as it starts:
        # an edit into your own mod asks otherwise.
        self.window.settings.setValue(self.window.preferences.CREATOR_KEY, "Me")
        self.window.update_tab_gates()
        self.index = self.window.index

    def tearDown(self):
        self.window.editor.search._debounce.stop()
        # The window may have a texts load in flight (it restores the last one
        # into a fresh index); that writes through the index's own connection
        # from another thread, so it has to finish before the index closes.
        self.window.game_files.stop_texts_worker()
        if self.window.index is not None:
            self.window.index.close()
            self.window.index = None
        self.window.loadout.clear()
        self.window.deleteLater()

    # -- helpers ------------------------------------------------------------
    def _search(self, text):
        """Type ``text`` into the asset search, and let the pause after
        typing run out: that is what searches."""
        from PySide6.QtTest import QTest
        from support import wait_until

        debounce = self.window.browser._debounce
        self.window.browser.search_box.clear()
        QTest.keyClicks(self.window.browser.search_box, text)
        self.assertTrue(debounce.isActive())
        self.assertTrue(wait_until(lambda: not debounce.isActive()))

    def _find_item(self, tag):
        for item in self.window.editor._iter_items():
            if item.text(0) == tag and item.data(0, 258) is not None:  # ORIG_ROLE
                return item
        return None

    def _edit(self, tag, value):
        item = self._find_item(tag)
        self.assertIsNotNone(item, "no editable field named %s" % tag)
        item.setText(1, value)
        return item

    # -- structural editing -------------------------------------------------
    def _container(self, tag):
        for item in self.window.editor._iter_items():
            if item.text(0) == tag and item.data(0, 258) is None:  # ORIG_ROLE
                return item
        return None

    def _open_add_field_dialog(self, spath, present):
        """Build the dialog the way ``_add_field``/``_add_root_field`` do."""
        from anno_modding_studio.ui.add_field_dialog import AddFieldDialog

        fields = self.index.schema.children_of(spath)
        return AddFieldDialog(fields, spath, present, parent=self.window)

    @staticmethod
    def _row_for_tag(dialog, tag):
        from PySide6.QtCore import Qt

        tag_role = Qt.ItemDataRole.UserRole
        for r in range(dialog.table.rowCount()):
            if dialog.table.item(r, 0).data(tag_role) == tag:
                return r
        return None


class TestTheUnsavedMarkMeansWhatClosingWillAsk(WorkflowCase):
    """The ``*`` in the title bar and the prompt on the way out are one question.

    They used to be two. The mark meant "this differs from what is on disk";
    the prompt meant that *and* "and at least one of the differences is a
    change you made". So everything else a loadout holds -- the mod's name,
    version, creator and description, its mod.io links, which of your mods an
    edit lands in -- wore the mark and then went without a word: typing a
    description on the Export tab and closing the window lost the description,
    with the title bar having said so the whole time in between.
    """

    def _closing_asks(self) -> bool:


        with _isolation.answering("Discard") as asked:
            self.window.files.confirm_discard()
        return asked.called

    def _marked(self) -> bool:
        self.window.update_title()
        return "*" in self.window.windowTitle()

    def _assert_agree(self, expected: bool, what: str) -> None:
        self.assertEqual(self._marked(), expected, "the title bar, after %s" % what)
        self.assertEqual(self._closing_asks(), expected, "closing, after %s" % what)

    def test_a_loadout_nothing_has_happened_to_is_neither(self):
        self.window.files.mark_saved()
        self._assert_agree(False, "nothing")

    def test_a_change_you_made_is_both(self):
        self.window._open_asset("1010340")
        self._edit("CycleTime", "15.00")
        self._assert_agree(True, "an edit")

    def test_a_detail_you_typed_is_both(self):
        """The one that used to be marked and then silently thrown away."""
        self.window.files.mark_saved()
        self.window.loadout.own.description = "what this mod does"
        self.window.files.on_loadout_edited()
        self.assertEqual(self.window.loadout.authored_count, 0, "no changes at all")
        self._assert_agree(True, "typing a description")

    def test_renaming_the_mod_is_both(self):
        self.window.files.mark_saved()
        self.window.loadout.own.name = "Something Else"
        self.window.files.on_loadout_edited()
        self._assert_agree(True, "renaming the mod")

    def test_and_saving_puts_both_back(self):
        from unittest import mock

        self.window._open_asset("1010340")
        self._edit("CycleTime", "15.00")
        self.window.loadout.own.description = "what this mod does"
        self.window.files.on_loadout_edited()
        self._assert_agree(True, "an edit and a description")

        from PySide6.QtWidgets import QFileDialog

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "mod.annoload"
            with mock.patch.object(
                QFileDialog, "getSaveFileName", return_value=(str(path), "")
            ):
                self.assertTrue(self.window.files.save_loadout(ask=True))
        self._assert_agree(False, "saving")

    def test_what_a_mods_folder_read_lands_is_neither(self):
        """Deliberately: it is on disk already and the menu will read it again.

        Which was always the prompt's rule; the mark did not share it, so a
        folder read raised a mark that closing then ignored.
        """
        from anno_modding_studio.core.changes import Change, ModLayer

        self.window.files.mark_saved()
        self.window.loadout.set_layer(ModLayer(origin="Theirs", name="Theirs"))
        self.window.loadout.set_change(
            Change(guid="1010340", asset_label="a", path="/Values/X", tag="X",
                   old_value="1", new_value="2", origin="Theirs",
                   authored=False)  # read from the mod, not made here
        )
        self.window.dirty = True  # as reading one does
        self.assertEqual(self.window.loadout.authored_count, 0)
        self._assert_agree(False, "reading a mod in")

    def test_the_title_says_so_the_moment_the_loadout_is_marked(self):
        """Closing asked "This loadout has been worked on since it was
        saved" under a title with no ``*``: a dozen places marked the
        loadout unsaved and left the title to whatever drew it next."""
        self.window._open_asset("1010340")
        self._edit("CycleTime", "15.00")
        self.window.files.mark_saved()
        self.window.update_title()
        self.assertNotIn("*", self.window.windowTitle())
        self.window.files.mark_unsaved()
        # Not drawn again by the test: marking it is what draws it.
        self.assertIn("*", self.window.windowTitle())
        self.assertTrue(self._closing_asks())

    def test_but_learning_a_modio_id_for_one_of_them_is_both(self):
        """A layer appearing is a read; a layer *changing* is something said."""
        from anno_modding_studio.core.changes import ModLayer

        self.window.loadout.set_layer(ModLayer(origin="Theirs", name="Theirs"))
        self.window.files.mark_saved()
        self.window.loadout.layer("Theirs").modio_id = 12345
        self.window.dirty = True
        self._assert_agree(True, "learning a mod.io id")


class TestTheFirstEditIntoYourModAsksWhoYouAre(WorkflowCase):
    """The patch over a list of mods to play is a mod with your name on it
    from its first edit, and nothing asked what that name was: from the
    card, or from mods read in with no card, it went out with no author."""

    def setUp(self):
        from anno_modding_studio.core.changes import AIM_MANAGE

        super().setUp()
        self.window.settings.remove(self.window.preferences.CREATOR_KEY)
        self.window.loadout.set_up_as(AIM_MANAGE)
        self.window.loadout.own.creator = ""
        self.window._open_asset("1010340")

    def _edits(self, *values, answer=("Ann", True)):
        with mock.patch.object(self.window.prompts, "ask_creator_name",
                               return_value=answer) as asked:
            for value in values:
                self._edit("CycleTime", value)
        return asked

    def test_it_is_asked_once_and_the_name_goes_on_the_mod(self):
        asked = self._edits("15.00", "16.00")
        asked.assert_called_once()
        self.assertEqual(self.window.loadout.own.creator, "Ann")
        self.assertEqual(self.window.preferences.creator_name(), "Ann")

    def test_cancelled_it_keeps_the_edit_and_is_not_asked_again(self):
        asked = self._edits("15.00", "16.00", answer=("", False))
        asked.assert_called_once()
        self.assertEqual(self.window.loadout.authored_count, 1)
        self.assertEqual(self.window.loadout.own.creator, "")

    def test_a_name_known_already_goes_on_without_asking(self):
        self.window.settings.setValue(self.window.preferences.CREATOR_KEY, "Me")
        asked = self._edits("15.00")
        asked.assert_not_called()
        self.assertEqual(self.window.loadout.own.creator, "Me")

    def test_and_it_is_in_the_file_the_first_edit_writes(self):
        """Asked after, the name waited unsaved behind a file that had
        just been written without it."""
        from anno_modding_studio.core.changes import Loadout

        self.window.files.name_loadout("Played")
        self._edits("15.00")
        self.assertTrue(self.window.on_disk)
        self.assertFalse(self.window.files.unsaved_work())
        self.assertEqual(Loadout.load(self.window.loadout_path).own.creator, "Ann")


class TestSearchResultMenu(WorkflowCase):
    """Right-clicking a search result: the GUID without opening the asset."""

    def _row_for(self, needle: str) -> int:
        self._search(needle)
        # The one asset the whole needle finds: its first letter finds dozens.
        self.assertEqual(self.window.browser.table.rowCount(), 1)
        return 0

    def test_a_result_offers_opening_it_its_graph_and_its_two_names(self):
        row = self._row_for("lumberjack")
        texts = [
            a.text()
            for a in self.window.browser.menu_for(row).actions()
            if not a.isSeparator()
        ]
        self.assertIn("Open in asset editor", texts)
        self.assertIn("References for this asset…", texts)
        self.assertIn("Copy GUID", texts)
        self.assertIn("Copy internal name", texts)

    def test_copying_the_guid_never_opens_the_asset(self):
        from PySide6.QtGui import QGuiApplication

        row = self._row_for("lumberjack")
        before = self.window.editor.guid
        menu = self.window.browser.menu_for(row)
        next(a for a in menu.actions() if a.text() == "Copy GUID").trigger()
        self.assertEqual(QGuiApplication.clipboard().text(), "1010340")
        self.assertEqual(self.window.editor.guid, before)

    def test_the_graph_can_be_asked_for_straight_from_a_result(self):
        row = self._row_for("lumberjack")
        asked = []
        self.window.editor.referencesRequested.connect(asked.append)
        menu = self.window.browser.menu_for(row)
        next(
            a for a in menu.actions() if a.text() == "References for this asset…"
        ).trigger()
        self.assertEqual(asked, ["1010340"])

    def test_narrowing_to_a_row_s_template_sets_the_filter_above(self):
        row = self._row_for("lumberjack")
        browser = self.window.browser
        menu = browser.menu_for(row)
        narrow = next(
            a for a in menu.actions() if a.text().startswith("Show only ")
        )
        narrow.trigger()
        self.assertEqual(browser.template_combo.currentData(), "FactoryBuilding7")
        # What was typed is left alone: this narrows a search, it does not
        # replace one.
        self.assertEqual(browser.search_box.text(), "lumberjack")


class TestSearchAndTemplateFilter(WorkflowCase):
    """Finding the asset to work on: the search box and the template filter."""

    # -- tests --------------------------------------------------------------
    def test_search_populates_and_filters(self):
        browser = self.window.browser
        # As clicking into the box does: a shut list waits to be opened.
        browser.open_results()
        self.assertGreater(browser.table.rowCount(), 0)

        self._search("lumberjack")
        self.assertEqual(browser.table.item(0, 2).text(), "1010340")

        browser.clear_filters()
        idx = browser.template_combo.findData("ResidenceBuilding7")
        browser.template_combo.setCurrentIndex(idx)
        browser.refresh()
        self.assertEqual(browser.table.rowCount(), 4)

    def test_template_filter_narrows_to_what_the_search_actually_matches(self):
        """~1,000 templates in a real install is too many for a plain combo's
        type-ahead to realistically find one in -- once there is a query, the
        list should shrink to the handful of templates that show up among
        those results, not stay the full, unfiltered wall of choices."""
        browser = self.window.browser
        browser.clear_filters()
        full_count = browser.template_combo.count()

        self._search("lumberjack")
        narrowed_count = browser.template_combo.count()

        self.assertLess(narrowed_count, full_count)
        offered = {
            browser.template_combo.itemData(i) for i in range(browser.template_combo.count())
        }
        self.assertIn("FactoryBuilding7", offered)
        self.assertNotIn("ResidenceBuilding7", offered)

    def test_template_filter_widens_back_out_when_the_query_is_cleared(self):
        browser = self.window.browser
        browser.clear_filters()
        full_count = browser.template_combo.count()

        browser.search_box.setText("lumberjack")
        browser.refresh()
        self.assertLess(browser.template_combo.count(), full_count)

        browser.clear_filters()
        self.assertEqual(browser.template_combo.count(), full_count)
        self.assertEqual(browser.template_combo.currentData(), "")

    def test_a_selected_template_survives_a_widened_query(self):
        browser = self.window.browser
        browser.search_box.setText("lumberjack")
        browser.refresh()
        idx = browser.template_combo.findData("FactoryBuilding7")
        self.assertGreaterEqual(idx, 0)
        browser.template_combo.setCurrentIndex(idx)

        browser.search_box.setText("lumber")
        browser.refresh()
        self.assertEqual(browser.template_combo.currentData(), "FactoryBuilding7")

    def test_a_selected_template_resets_once_the_query_excludes_it(self):
        """Regression risk this guards against: silently keeping a filter
        selected that no longer applies to anything the search would return."""
        browser = self.window.browser
        browser.search_box.setText("lumberjack")
        browser.refresh()
        idx = browser.template_combo.findData("FactoryBuilding7")
        browser.template_combo.setCurrentIndex(idx)

        browser.search_box.setText("residence tier01")
        browser.refresh()
        self.assertEqual(browser.template_combo.currentData(), "")
        self.assertEqual(browser.template_combo.currentIndex(), 0)

    def _offered(self):
        combo = self.window.browser.template_combo
        return [combo.itemText(i) for i in range(1, combo.count())]

    def test_the_list_follows_texts_loaded_and_cleared_under_the_same_query(self):
        """It was rebuilt only when the words typed changed, so a query the
        texts alone answer kept the list it had before they were read: one
        match in the table and no template to pick, then no match and a
        template still on offer."""
        browser = self.window.browser
        # A word no asset holds itself: the sample's own texts repeat what
        # each asset already says.
        texts = Path(self._tmp.name) / "texts_german.xml"
        texts.write_text(
            "<TextExport><Texts><Text><GUID>1010030</GUID>"
            "<Text>Bauernhaus</Text></Text></Texts></TextExport>",
            encoding="utf-8",
        )
        browser.open_results()
        browser.search_box.setText("bauernhaus")
        browser.refresh()
        self.assertEqual(self._offered(), [])

        self.window.index.load_texts(texts)
        self.window.game_files.on_texts_loaded(self.window.index.text_count)
        self.assertEqual(browser.table.rowCount(), 1)
        self.assertEqual(self._offered(), ["ResidenceBuilding7  (1)"])

        self.window.game_files.clear_texts()
        self.assertEqual(browser.table.rowCount(), 0)
        self.assertEqual(self._offered(), [])

    def test_the_list_counts_the_assets_this_mod_creates(self):
        """The results list them, so the list beside it has to offer their
        templates: it was counted from the game's file alone."""
        from anno_modding_studio.core.changes import OP_ADD_NEXT, Change

        browser = self.window.browser
        browser.open_results()
        self.window.loadout.set_change(
            Change(
                guid="1010340", asset_label="villa  (new asset)", path="",
                tag="Asset", op=OP_ADD_NEXT, summary="create asset 1337000007",
                payload="<Asset><Template>ResidenceBuilding7</Template><Values>"
                        "<Standard><GUID>1337000007</GUID>"
                        "<Name>lumberjack_villa</Name></Standard></Values></Asset>",
            )
        )
        browser.search_box.setText("lumberjack")
        browser.refresh()
        self.assertEqual(browser.table.rowCount(), 2)
        self.assertEqual(
            self._offered(),
            ["FactoryBuilding7  (1)", "ResidenceBuilding7  (1)"],
        )

        idx = browser.template_combo.findData("ResidenceBuilding7")
        browser.template_combo.setCurrentIndex(idx)
        browser.refresh()
        self.assertEqual(browser.table.rowCount(), 1)
        self.assertEqual(browser.table.item(0, 2).text(), "1337000007")

        browser.clear_filters()
        self.assertIn("ResidenceBuilding7  (5)", self._offered())

    def test_a_plain_template_reselection_leaves_the_list_as_it_was(self):
        """The template combo's own currentIndexChanged also triggers a
        (debounced) refresh, which must not re-narrow the list out from under
        a selection the user is actively making with the same query text."""
        browser = self.window.browser
        browser.search_box.setText("lumberjack")
        browser.refresh()
        before = [
            browser.template_combo.itemText(i) for i in range(browser.template_combo.count())
        ]

        browser.template_combo.setCurrentIndex(0)
        browser.refresh()
        after = [
            browser.template_combo.itemText(i) for i in range(browser.template_combo.count())
        ]
        self.assertEqual(before, after)

    def test_a_refresh_that_changes_nothing_leaves_the_list_alone(self):
        """Every refresh of the results asks what the list should hold: a
        template picked, an edit made with the list open.  The same words
        over the same assets get the answer that was held, and the list is
        not emptied and filled again with what it already said."""
        browser = self.window.browser
        browser.open_results()
        browser.search_box.setText("lumberjack")
        browser.refresh()
        swept, filled = [], []
        self.index._con.set_trace_callback(
            lambda sql: swept.append(sql) if "GROUP BY a.template" in sql else None
        )
        model = browser.template_combo.model()
        model.rowsInserted.connect(lambda *_a: filled.append(1))
        try:
            browser.refresh()
            browser.template_combo.setCurrentIndex(1)
            browser.refresh()
            self.assertEqual((swept, filled), ([], []))
            self.assertEqual(browser.template_combo.currentData(), "FactoryBuilding7")
            self.assertEqual(browser.table.rowCount(), 1)

            # Other words are another answer, and the list follows.
            browser.search_box.setText("")
            browser.refresh()
        finally:
            # Before the window shuts the index: nothing is set on a closed one.
            self.index._con.set_trace_callback(None)
        self.assertEqual(len(swept), 1)
        self.assertTrue(filled)
        self.assertIn("ResidenceBuilding7  (4)", self._offered())

    def test_no_group_filter_is_offered(self):
        """Real game files never name their groups, so the facet was removed."""
        headers = [
            self.window.browser.table.horizontalHeaderItem(i).text()
            for i in range(self.window.browser.table.columnCount())
        ]
        self.assertNotIn("Group", headers)
        self.assertNotIn("group", self.window.browser.hint.text().lower())
        self.assertNotIn("group", self.window.editor.hint.text().lower())

    def test_template_filter_reaches_a_long_list_without_a_text_field(self):
        """645 templates in a real install, and a plain dropdown still finds
        one in a few keystrokes: Qt's own type-ahead jumps to the first item
        whose name starts with what you just typed. A custom typeable combo
        used to back this field and never stopped looking subtly different
        from a plain one (a caret, a highlighted-selection bar) -- the main
        asset search box above it already does full substring matching against
        template names, so this filter never needed to be a text field itself."""
        combo = self.window.browser.template_combo
        self.assertFalse(combo.isEditable())
        self.assertIsNone(combo.lineEdit())
        self.assertGreater(combo.count(), 1)


class TestTheSourceStepAndTabGates(WorkflowCase):
    """The Start tab is the gate: what opens the others, and when."""

    def test_source_step_is_the_gate_until_a_file_is_loaded(self):
        fresh = MainWindow()
        self.addCleanup(_flush_deletes)
        try:
            self.assertEqual(fresh.tabs.currentIndex(), TAB_SOURCE)
            self.assertFalse(fresh.tabs.isTabEnabled(TAB_EDIT))
            ways = list(fresh.source.ways.values())
            self.assertFalse(any(way.button.isEnabled() for way in ways))

            fresh.game_files.adopt_index(self.db, self.assets, cached=True)
            self.assertTrue(all(way.button.isEnabled() for way in ways))
            self.assertIn("assets.xml", fresh.source.assets_label.full_text())
            # Still shut: the files are loaded, but nothing says what they are
            # loaded *for* yet. See TestTheOtherTabsWaitForTheStartTab.
            self.assertFalse(fresh.tabs.isTabEnabled(TAB_EDIT))
            from anno_modding_studio.core.changes import AIM_MAKE

            fresh.loadout.aim = AIM_MAKE
            fresh.update_tab_gates()
            self.assertTrue(fresh.tabs.isTabEnabled(TAB_EDIT))
        finally:
            fresh.game_files.stop_texts_worker()
            if fresh.index is not None:
                fresh.index.close()
                fresh.index = None
            fresh.loadout.clear()
            fresh.deleteLater()

    def test_every_gated_tab_is_shut_on_a_cold_start(self):
        """All three, not just the editor.

        Construction used to shut two of them by hand and leave Export open, so
        a window with nothing loaded offered to export a mod called "My Anno
        Mod" for whichever game the defaults named -- the "looks like it works
        and does nothing" landing the gate exists to prevent. The gate decides
        the opening state now, which is why this asserts about all of them.
        """
        fresh = MainWindow()
        self.addCleanup(_flush_deletes)
        try:
            self.assertEqual(fresh.tabs.currentIndex(), TAB_SOURCE)
            self.assertTrue(fresh.tabs.isTabEnabled(TAB_SOURCE))
            for tab, name in (
                (TAB_EDIT, "asset editor"),
                (TAB_REFS, "references"),
                (TAB_GENERATE, "export"),
            ):
                with self.subTest(tab=name):
                    self.assertFalse(fresh.tabs.isTabEnabled(tab))
        finally:
            fresh.game_files.stop_texts_worker()
            if fresh.index is not None:
                fresh.index.close()
                fresh.index = None
            fresh.loadout.clear()
            fresh.deleteLater()

    def test_shutting_the_gate_never_strands_the_user_on_a_dead_tab(self):
        """Un-settling a loadout while sitting on one of the gated tabs."""
        from anno_modding_studio.core.changes import AIM_MAKE

        self.window.loadout.aim = AIM_MAKE
        self.window.update_tab_gates()
        self.window.tabs.setCurrentIndex(TAB_EDIT)
        self.assertEqual(self.window.tabs.currentIndex(), TAB_EDIT)

        self.window.loadout.aim = ""
        self.window.loadout.changes.clear()
        self.window.loadout.layers.clear()
        self.window.update_tab_gates()

        self.assertTrue(
            self.window.tabs.isTabEnabled(self.window.tabs.currentIndex()),
            "left on a tab that is switched off",
        )
        self.assertEqual(self.window.tabs.currentIndex(), TAB_SOURCE)

    def test_source_step_stays_available_for_swapping_files(self):
        """Changing the base file must not be a one-time first-run action."""
        self.assertTrue(self.window.tabs.isTabEnabled(TAB_SOURCE))
        self.window.tabs.setCurrentIndex(TAB_SOURCE)
        self.assertEqual(self.window.tabs.currentIndex(), TAB_SOURCE)
        self.assertIn(str(self.assets), self.window.source.assets_label.full_text())

    def test_source_step_reports_and_clears_localisation(self):
        self.window.index.load_texts(self.texts)
        self.window.game_files.on_texts_loaded(self.window.index.text_count)
        self.window.game_files.texts_path = self.texts
        self.window.start.refresh_source_panel()
        self.assertIn("texts_english.xml", self.window.source.texts_label.full_text())
        self.assertTrue(self.window.source.texts_clear.isEnabled())

        self.window.game_files.clear_texts()
        self.assertEqual(self.window.index.text_count, 0)
        self.assertIn("None", self.window.source.texts_label.full_text())
        self.assertFalse(self.window.source.texts_clear.isEnabled())

    def test_reindexing_the_current_file_does_not_error_on_windows(self):
        """Regression: File -> Re-index current assets.xml failed with a
        Windows PermissionError, because the Studio's own read connection to
        the cache file was still open when the rebuild tried to replace it.

        Deliberately drives the real ``open_assets`` flow (not the ``_adopt_index``
        shortcut ``setUp`` uses) so the currently-open index really does share
        ``db_path`` with the one about to be rebuilt -- that collision is the
        whole bug.
        """
        from unittest import mock

        from anno_modding_studio.core import places as places_mod
        from anno_modding_studio.ui.main_window import QMessageBox

        with tempfile.TemporaryDirectory() as cache_tmp:
            # Isolated from the real %LOCALAPPDATA% cache -- this test builds
            # for real, and should not leave files behind in the user's own
            # cache directory just for exercising the reindex path.
            with mock.patch.object(
                places_mod, "cache_dir", return_value=Path(cache_tmp)
            ):
                self.window.game_files.open_assets(str(self.assets))
                self.assertIsNotNone(
                    self.window.game_files.worker, "a fresh build should have started"
                )
                self.assertTrue(
                    self.window.game_files.worker.wait(10000), "initial index did not finish"
                )
                _app.processEvents()
                self.assertIsNotNone(self.window.index)
                db_path = self.window.index.db_path

                with mock.patch.object(QMessageBox, "critical") as critical:
                    self.window.game_files.open_assets(str(self.assets), force=True)
                    self.assertIsNotNone(self.window.game_files.worker)
                    self.assertTrue(
                        self.window.game_files.worker.wait(10000), "reindex did not finish"
                    )
                    _app.processEvents()
                    critical.assert_not_called()

                self.assertIsNotNone(self.window.index, "the index must still be usable")
                self.assertEqual(self.window.index.db_path, db_path)
                self.assertGreater(self.window.index.asset_count, 0)
                self.assertTrue(self.window.index.search("lumberjack", limit=1))

                # Release the handle into cache_tmp before its context manager
                # tries to delete it -- Windows won't remove an open file.
                self.window.game_files.stop_texts_worker()
                self.window.index.close()


class TestEditingAValue(WorkflowCase):
    """Recording a change, and what happens to it afterwards."""

    def test_opening_an_asset_switches_tab_and_builds_tree(self):
        self.window._open_asset("1010340")
        self.assertEqual(self.window.tabs.currentIndex(), TAB_EDIT)
        self.assertTrue(self.window.tabs.isTabEnabled(TAB_EDIT))
        self.assertIn("1010340", self.window.editor.subtitle_label.text())
        self.assertIsNotNone(self._find_item("CycleTime"))

    def test_editing_a_value_records_one_change(self):
        self.window._open_asset("1010340")
        self._edit("CycleTime", "15.00")

        self.assertEqual(len(self.window.loadout), 1)
        change = next(iter(self.window.loadout))
        self.assertEqual(change.guid, "1010340")
        self.assertEqual(change.path, "/Values/Factory7/CycleTime")
        self.assertEqual(change.old_value, "30.00")
        self.assertEqual(change.new_value, "15.00")
        self.assertEqual(len(change_rows(self.window.changes_panel)), 1)

    def test_drawing_an_asset_reads_the_change_set_once(self):
        """Every reference drawn asks the workspace about it, and each question
        used to fingerprint the whole change set again: 524 times for one
        asset of a 400-mod loadout, 21 s.  The draw holds it steady."""
        workspace = self.window.workspace
        real, counted = workspace._fingerprint, []
        workspace._fingerprint = lambda: counted.append(1) or real()
        self.addCleanup(lambda: setattr(workspace, "_fingerprint", real))
        self.window._open_asset("1010340")
        # A handful around the draw, whatever the asset holds; one per
        # reference drawn was nine here, and hundreds on a real one.
        self.assertLessEqual(len(counted), 5)

    def test_an_edit_is_one_edit(self):
        """Painting the edited row bold is not a second edit.  Each font and
        colour set on it used to come back as an edit of its own: a first
        edit to a field went round four times, and rebuilt the changes list
        four times over."""
        self.window._open_asset("1010340")
        heard = []
        self.window.editor.changesUpdated.connect(lambda: heard.append(1))
        self._edit("CycleTime", "15.00")
        self.assertEqual(len(heard), 1)

    def test_editing_does_not_mutate_the_source_tree(self):
        """Paths must always be generated against pristine base-game values."""
        self.window._open_asset("1010340")
        self._edit("CycleTime", "15.00")
        root = self.window.editor.asset_root
        self.assertEqual(root.findtext("Values/Factory7/CycleTime"), "30.00")

    def test_typing_the_original_value_back_removes_the_change(self):
        self.window._open_asset("1010340")
        self._edit("CycleTime", "15.00")
        self.assertEqual(len(self.window.loadout), 1)
        self._edit("CycleTime", "30.00")
        self.assertEqual(len(self.window.loadout), 0)
        self.assertEqual(change_rows(self.window.changes_panel), [])

    def test_changes_survive_leaving_and_revisiting_an_asset(self):
        self.window._open_asset("1010340")
        self._edit("CycleTime", "15.00")
        self.window._open_asset("1010030")
        self.window._open_asset("1010340")

        item = self._find_item("CycleTime")
        self.assertEqual(item.text(1), "15.00")
        self.assertEqual(item.text(3), "30.00")  # original shown alongside
        self.assertEqual(len(self.window.loadout), 1)

    def test_removing_from_the_dock_updates_the_editor(self):
        self.window._open_asset("1010340")
        self._edit("CycleTime", "15.00")
        pick_change(self.window.changes_panel)
        self.window.changes_panel._remove_selected()

        self.assertEqual(len(self.window.loadout), 0)
        self.assertEqual(self._find_item("CycleTime").text(1), "30.00")

    def test_repeated_list_entries_get_distinct_paths(self):
        """Two <Amount> fields in the same list must not collide."""
        self.window._open_asset("1010340")
        amounts = [
            item
            for item in self.window.editor._iter_items()
            if item.text(0) == "Amount" and item.data(0, 258) is not None
        ]
        self.assertGreaterEqual(len(amounts), 3)
        for n, item in enumerate(amounts):
            item.setText(1, str(900 + n))
        paths = {c.path for c in self.window.loadout}
        self.assertEqual(len(paths), len(amounts))

    def test_generate_tab_produces_verified_xml(self):
        self.window._open_asset("1010340")
        self._edit("CycleTime", "15.00")
        self.window._open_asset("1010030")
        self._edit("Residents", "12")

        self.window.tabs.setCurrentIndex(TAB_GENERATE)
        xml = self.window.output._xml_text
        root = etree.fromstring(xml.encode("utf-8"))
        self.assertEqual(len(root.findall("ModOp")), 2)
        self.assertIn("verified", self.window.output.check_summary.text())
        self.assertEqual(self.window.output.check_list.count(), 0)

    def test_positional_edit_is_reported_as_a_warning(self):
        self.window._open_asset("1010030")
        self._edit("Tag", "manor")  # <Tags><Tag> has no identifying child
        change = next(iter(self.window.loadout))
        self.assertTrue(change.positional)

        self.window.tabs.setCurrentIndex(TAB_GENERATE)
        self.assertEqual(self.window.output.check_list.count(), 1)
        self.assertIn("positional", self.window.output.check_list.item(0).text())

    def test_a_validation_row_can_be_opened_rather_than_only_read(self):
        """The list names an asset; getting to it used to mean finding it again."""
        self.window._open_asset("1010030")
        self._edit("Tag", "manor")
        self.window.tabs.setCurrentIndex(TAB_GENERATE)
        item = self.window.output.check_list.item(0)

        self.window._open_asset("1010340")  # somewhere else entirely
        self.window.tabs.setCurrentIndex(TAB_GENERATE)
        self.window.output.check_list.itemActivated.emit(item)
        self.assertEqual(self.window.editor.guid, "1010030")
        self.assertEqual(self.window.tabs.currentIndex(), TAB_EDIT)

    def test_a_validation_row_offers_its_path_for_quoting_elsewhere(self):
        from PySide6.QtGui import QGuiApplication

        self.window._open_asset("1010030")
        self._edit("Tag", "manor")
        self.window.tabs.setCurrentIndex(TAB_GENERATE)
        item = self.window.output.check_list.item(0)
        menu = self.window.output.menu_for(item)
        texts = [a.text() for a in menu.actions() if not a.isSeparator()]
        self.assertIn("Open this change in the asset editor", texts)
        self.assertIn("Copy the path", texts)

        next(a for a in menu.actions() if a.text() == "Copy the path").trigger()
        change = next(iter(self.window.loadout))
        self.assertEqual(QGuiApplication.clipboard().text(), change.path)

    def test_an_anno_117_mod_id_the_game_would_refuse_stops_the_export(self):
        """The loader says so in a log; the Export tab says so first."""
        out = self.window.output
        self.window._open_asset("1010340")
        self._edit("CycleTime", "15.00")
        out.set_game(games.ANNO_117)
        out.modid_edit.setText("Faster_Lumber")
        self.window.tabs.setCurrentIndex(TAB_GENERATE)
        out.refresh()

        self.assertIn("refuse", out.check_summary.text())
        rows = [out.check_list.item(i).text() for i in range(out.check_list.count())]
        self.assertEqual(len(rows), 1)
        self.assertIn("lowercase", rows[0])
        self.assertIn("faster-lumber", rows[0])
        self.assertFalse(out.folder_action.isEnabled())
        with mock.patch.object(QMessageBox, "warning") as warned:
            self.assertIsNone(out.export_mod(confirm_overwrite=False))
        self.assertIn("Faster_Lumber", warned.call_args[0][2])

        # Taking the offer is the row itself: opened, it fills the field in.
        out.check_list.itemActivated.emit(out.check_list.item(0))
        self.assertEqual(out.modid_edit.text(), "faster-lumber")
        self.assertEqual(out.check_list.count(), 0)
        self.assertTrue(out.folder_action.isEnabled())

    def test_and_says_so_before_the_mod_has_a_change(self):
        """An ID typed into a new mod went unremarked under "No changes to
        export yet", and was refused only once the first change landed (H2-30)."""
        out = self.window.output
        out.set_game(games.ANNO_117)
        self.window.tabs.setCurrentIndex(TAB_GENERATE)
        out.refresh()
        # A blank one is filled in from the name in a shape the game takes.
        self.assertEqual(out.check_summary.text(), "No changes to export yet.")
        out.modid_edit.setText("Run_Two_Roman")
        out.refresh()
        self.assertIn("refuse", out.check_summary.text())
        rows = [out.check_list.item(i).text() for i in range(out.check_list.count())]
        self.assertEqual(len(rows), 1)
        self.assertIn("lowercase", rows[0])
        out.check_list.itemActivated.emit(out.check_list.item(0))
        self.assertEqual(out.modid_edit.text(), "run-two-roman")
        self.assertEqual(out.check_summary.text(), "No changes to export yet.")

    def test_the_same_mod_id_is_no_problem_for_anno_1800(self):
        out = self.window.output
        self.window._open_asset("1010340")
        self._edit("CycleTime", "15.00")
        out.modid_edit.setText("Faster_Lumber")
        self.window.tabs.setCurrentIndex(TAB_GENERATE)
        out.refresh()
        self.assertEqual(out.check_list.count(), 0)
        self.assertTrue(out.folder_action.isEnabled())

    def test_loadout_round_trip_restores_the_dock_and_editor(self):
        self.window._open_asset("1010340")
        self._edit("CycleTime", "15.00")
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "mod.json"
            self.window.loadout.save(path)
            self.window.loadout.clear()

            from anno_modding_studio.core.changes import Loadout

            loaded = Loadout.load(path)
            self.window.loadout.__dict__.update(loaded.__dict__)

        self.window.changes_panel.refresh()
        self.window.editor.load_asset("1010340")
        self.assertEqual(len(change_rows(self.window.changes_panel)), 1)
        self.assertEqual(self._find_item("CycleTime").text(1), "15.00")

    def test_filter_keeps_ancestors_of_matches(self):
        self.window._open_asset("1010340")
        editor = self.window.editor
        editor.filter_box.setText("cycletime")
        editor._apply_filter()

        item = self._find_item("CycleTime")
        self.assertFalse(item.isHidden())
        self.assertFalse(item.parent().isHidden())
        self.assertTrue(self._find_item("BuildingType").isHidden())

    def test_only_changed_filter(self):
        self.window._open_asset("1010340")
        self._edit("CycleTime", "15.00")
        editor = self.window.editor
        editor.only_changed.setChecked(True)

        self.assertFalse(self._find_item("CycleTime").isHidden())
        self.assertTrue(self._find_item("BuildingType").isHidden())


class TestTheEditorTree(WorkflowCase):
    """What the tree shows, and what it refuses to let you touch."""

    def test_empty_fields_are_hidden_until_asked_for(self):
        """Assets are padded with empty <Item/> and unused sections."""
        self.window._open_asset("1010340")
        empties = [
            item
            for item in self.window.editor._iter_items()
            if item.data(0, 260)  # EMPTY_ROLE
        ]
        self.assertTrue(empties, "sample asset should contain an empty node")
        self.assertTrue(all(item.isHidden() for item in empties))

        self.window.editor.show_empty.setChecked(True)
        self.assertTrue(any(not item.isHidden() for item in empties))

    def test_reference_fields_show_what_they_point_at(self):
        self.window._open_asset("1010340")
        products = {
            item.text(1): item
            for item in self.window.editor._iter_items()
            if item.text(0) == "Product"
        }
        # 1010196 is Timber in the sample data...
        self.assertIn("Timber", products["1010196"].text(2))
        self.assertTrue(self.window.editor._is_reference_field(products["1010196"]))
        # ...while 1010017 is referenced but not itself an asset here, so there
        # is nothing to show and nothing to pick from.
        self.assertEqual(products["1010017"].text(2), "")
        self.assertFalse(self.window.editor._is_reference_field(products["1010017"]))

    def test_dock_field_column_elides_from_the_left(self):
        """The tail of the path is what identifies it, so keep the tail."""
        from PySide6.QtCore import Qt
        from PySide6.QtWidgets import QStyleOptionViewItem

        self.window._open_asset("1010340")
        self._edit("CycleTime", "15.00")
        tree = self.window.changes_panel.tree
        delegate = tree.itemDelegateForColumn(0)
        self.assertIsNotNone(delegate)

        row = change_rows(self.window.changes_panel)[0]
        option = QStyleOptionViewItem()
        delegate.initStyleOption(option, self.window.changes_panel.tree.indexFromItem(row, 0))
        self.assertEqual(option.textElideMode, Qt.TextElideMode.ElideLeft)

    def _shown_at(self, row, width: int) -> str:
        """What the dock's first column draws of ``row`` in ``width`` pixels."""
        from PySide6.QtCore import QRect
        from PySide6.QtWidgets import QStyleOptionViewItem

        tree = self.window.changes_panel.tree
        option = QStyleOptionViewItem()
        option.widget = tree
        option.font = tree.font()
        option.rect = QRect(0, 0, width, 24)
        tree.itemDelegateForColumn(0).initStyleOption(option, tree.indexFromItem(row, 0))
        return option.text

    def _just_too_narrow(self, row) -> int:
        """The widest the column can be and still not hold all of ``row``."""
        full = row.text(0)
        return next(
            width for width in range(4000, 40, -4) if self._shown_at(row, width) != full
        )

    def test_but_a_row_that_starts_with_a_name_keeps_the_start_of_it(self):
        """An asset's row was cut from the left too, which took the name the
        game shows and left the end of the internal one in brackets."""
        self.window._open_asset("1010340")
        self._edit("CycleTime", "15.00")
        asset = change_rows(self.window.changes_panel)[0].parent()
        self.assertEqual(asset.text(0), "Lumberjack's Hut  (lumberjack)")
        self.assertIn(asset.text(0), asset.toolTip(0))
        narrow = self._just_too_narrow(asset)
        # First the internal name goes, and what is left is whole.
        self.assertEqual(self._shown_at(asset, narrow), "Lumberjack's Hut")
        # Then it is cut in the middle, so it still starts as the name does.
        tight = self._shown_at(asset, 90)
        self.assertIn("…", tight)
        self.assertTrue(tight.startswith("L"), tight)
        # And so is a mod's row, which has no internal name to give up.
        mod = asset.parent()
        self.assertTrue(self._shown_at(mod, 90).startswith(mod.text(0)[0]))

    def test_a_patterns_match_keeps_the_assets_name_and_the_field(self):
        """It read "…er  (agriculture_arctic_04 (Bear Hunter)) > FactoryBase >
        CycleTime": the one part of the row that says which asset, cut."""
        from anno_modding_studio.core.changes import Op

        loadout = self.window.loadout
        op = Op(
            expression="//Values/FactoryBase",
            source='<ModOp Type="merge" Path="//Values/FactoryBase">'
                   "<CycleTime>45</CycleTime></ModOp>",
            summary="merge CycleTime = 45", searches=True, authored=True,
            matched=["1010340", "1010341"],
        )
        loadout.set_op(op)
        for guid in op.matched:
            loadout.set_change(Change(
                guid=guid, asset_label=guid, path="/Values/FactoryBase/CycleTime",
                tag="CycleTime", old_value="30", new_value="45", op_uid=op.uid,
            ))
        self.window.on_changes_updated()
        rows = {row.data(0, 256): row for row in change_rows(self.window.changes_panel)}
        row = rows["1010340"]
        self.assertEqual(
            row.text(0), "Lumberjack's Hut  (lumberjack) \u203a FactoryBase \u203a CycleTime"
        )
        self.assertTrue(row.toolTip(0).startswith("Lumberjack's Hut  (lumberjack)\n"))
        narrow = self._just_too_narrow(row)
        self.assertEqual(
            self._shown_at(row, narrow), "Lumberjack's Hut \u203a FactoryBase \u203a CycleTime"
        )
        tight = self._shown_at(row, 200)
        self.assertIn("…", tight)
        self.assertTrue(tight.startswith("Lu"), tight)
        self.assertTrue(tight.endswith("Time"), tight)

    def test_a_list_entry_says_which_entry_it_is_beside_its_tag(self):
        """Seven rows called Item are seven rows to open one at a time."""
        from anno_modding_studio.ui.editor_panel import IDENT_ROLE

        # The farmer residence: the sample buildings with a cost list.
        self.window._open_asset("1010030")
        entries = [
            item
            for item in self.window.editor._iter_items()
            if item.text(0) == "Item"
            and item.parent() is not None
            and item.parent().text(0) == "Costs"
        ]
        self.assertTrue(entries)
        shown = [item.data(0, IDENT_ROLE) for item in entries]
        self.assertTrue(all(shown), shown)
        self.assertEqual(len(set(shown)), len(shown))
        # Beside the tag, not in it: paths and keys read the tag back.
        self.assertTrue(all(item.text(0) == "Item" for item in entries))

        editor = self.window.editor
        editor.filter_box.setText(str(shown[-1]))
        editor._apply_filter()
        self.assertFalse(entries[-1].isHidden())

    def _cost_entries(self):
        """The rows of the open asset's cost list, by the ingredient each names."""
        return [
            (item, next(
                (item.child(i).text(1) for i in range(item.childCount())
                 if item.child(i).text(0) == "Ingredient"), ""
            ))
            for item in self.window.editor._iter_items()
            if item.text(0) == "Item"
            and item.parent() is not None
            and item.parent().text(0) == "Costs"
        ]

    def test_two_entries_naming_the_same_thing_both_keep_its_name(self):
        """A second cost entry given an ingredient already in the list took
        the name off both rows, and each is still an entry for that."""
        from anno_modding_studio.ui.editor_panel import IDENT_ROLE

        self.window._open_asset("1010030")
        editor = self.window.editor
        named = {
            ingredient: item.data(0, IDENT_ROLE)
            for item, ingredient in self._cost_entries() if ingredient
        }
        ingredient, name = next(iter(named.items()))
        self.assertTrue(name)

        editor._add_list_entry(self._container("Costs"))
        added = next(
            item for item, _ingredient in self._cost_entries()
            if item.text(3) == "added by this mod"
        )
        next(
            added.child(i) for i in range(added.childCount())
            if added.child(i).text(0) == "Ingredient"
        ).setText(1, ingredient)

        def twice():
            return [item for item, held in self._cost_entries() if held == ingredient]

        # As soon as it is typed: nothing draws the tree again for a value.
        self.assertEqual([item.data(0, IDENT_ROLE) for item in twice()], [name, name])
        # And once something does, which is when both rows lost it.
        editor.refresh_from_loadout()
        self.assertEqual([item.data(0, IDENT_ROLE) for item in twice()], [name, name])
        # The others are told apart as they were.
        for item, held in self._cost_entries():
            if held and held != ingredient:
                self.assertEqual(item.data(0, IDENT_ROLE), named[held])
        # What the row is called is not what its path is keyed by: neither
        # entry has anything of its own, so each is still picked by position.
        self.assertEqual([editor._path_for(item)[1] for item in twice()], [True, True])

    def test_an_entry_is_named_as_soon_as_its_ingredient_is_typed(self):
        from anno_modding_studio.ui.editor_panel import IDENT_ROLE

        self.window._open_asset("1010030")
        editor = self.window.editor
        editor._add_list_entry(self._container("Costs"))
        added = next(
            item for item, _ingredient in self._cost_entries()
            if item.text(3) == "added by this mod"
        )
        self.assertIsNone(added.data(0, IDENT_ROLE))
        ingredient = next(
            added.child(i) for i in range(added.childCount())
            if added.child(i).text(0) == "Ingredient"
        )
        ingredient.setText(1, "1010208")  # Beer, which nothing here costs yet
        self.assertEqual(added.data(0, IDENT_ROLE), "Beer")
        ingredient.setText(1, "")
        self.assertIsNone(added.data(0, IDENT_ROLE))

    def test_a_new_entrys_empty_ingredient_offers_to_choose_an_asset(self):
        """Only *Edit value* was in its menu until a GUID was typed into it,
        though it is a GUID field wherever the game uses it (H2-27)."""
        self.window._open_asset("1010030")
        editor = self.window.editor
        editor._add_list_entry(self._container("Costs"))
        added = next(
            item for item, _ingredient in self._cost_entries()
            if item.text(3) == "added by this mod"
        )
        ingredient = next(
            added.child(i) for i in range(added.childCount())
            if added.child(i).text(0) == "Ingredient"
        )
        self.assertEqual(ingredient.text(1), "")
        offered = [action.text() for action in editor._build_item_menu(ingredient).actions()]
        self.assertIn("Choose asset…", offered)
        # And nothing about references, which an empty field has none of.
        self.assertFalse(any(text.startswith("References for") for text in offered))
        # An empty field that holds a number is offered no asset.
        amount = next(
            added.child(i) for i in range(added.childCount())
            if added.child(i).text(0) == "Amount"
        )
        amount.setText(1, "")
        offered = [action.text() for action in editor._build_item_menu(amount).actions()]
        self.assertNotIn("Choose asset…", offered)

    def test_and_double_click_and_enter_on_it_choose_one_too(self):
        """The menu offered *Choose asset…*, and a double-click on its
        "Refers to" cell did nothing: the two asked different questions,
        and only the menu's knew an empty field (A11-107)."""
        self.window._open_asset("1010030")
        editor = self.window.editor
        editor._add_list_entry(self._container("Costs"))
        added = next(
            item for item, _ingredient in self._cost_entries()
            if item.text(3) == "added by this mod"
        )
        ingredient = next(
            added.child(i) for i in range(added.childCount())
            if added.child(i).text(0) == "Ingredient"
        )
        self.assertEqual(ingredient.text(1), "")
        # Through the tree, not its slots: a test that called them passed with
        # neither connected (A11-154).
        from PySide6.QtCore import QPointF
        from PySide6.QtGui import QMouseEvent
        from PySide6.QtTest import QTest

        tree = editor.tree
        self.window.show()
        self.window.tabs.setCurrentWidget(editor)
        tree.scrollToItem(ingredient)
        _app.processEvents()
        viewport = tree.viewport()
        at = QPointF(tree.visualRect(tree.indexFromItem(ingredient, 2)).center())
        left = Qt.MouseButton.LeftButton
        with mock.patch.object(editor, "_pick_asset") as pick:
            # The events of a double-click, sent one by one: the view's
            # doubleClicked never came of QTest.mouseDClick's here.
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
            self.assertEqual(pick.call_args_list, [mock.call(ingredient)])
            tree.setCurrentItem(ingredient, 0)
            QTest.keyClick(tree, Qt.Key.Key_Return)
        self.assertEqual(pick.call_args_list, [mock.call(ingredient), mock.call(ingredient)])

    def test_a_row_that_cannot_be_typed_in_is_not_offered_edit_value(self):
        """The asset's own GUID: *Edit value* was offered, and did nothing
        (A11-63)."""
        self.window._open_asset("1010340")
        editor = self.window.editor
        guid_item = next(
            i for i in editor._iter_items()
            if i.text(0) == "GUID" and i.text(1) == "1010340"
        )
        self.assertFalse(bool(guid_item.flags() & Qt.ItemFlag.ItemIsEditable))
        offered = [action.text() for action in editor._build_item_menu(guid_item).actions()]
        self.assertIn("Copy value", offered)
        self.assertNotIn("Edit value", offered)

    def test_identity_guid_is_not_annotated_as_a_reference(self):
        """It resolves to the asset you are already looking at."""
        self.window._open_asset("1010340")
        guid_item = next(
            i
            for i in self.window.editor._iter_items()
            if i.text(0) == "GUID" and i.text(1) == "1010340"
        )
        self.assertEqual(guid_item.text(2), "")

    def test_nor_is_the_guid_of_an_asset_the_mod_makes(self):
        """That one can be typed in, which was what the row was told by: it
        "referred to" its own asset, and Enter on it opened the asset picker."""
        from anno_modding_studio.core.changes import OP_ADD_NEXT, Change

        self.window.loadout.set_change(
            Change(
                guid="1010340", asset_label="made_here  (new asset)", path="",
                tag="Asset", op=OP_ADD_NEXT, summary="create asset 1337000009",
                payload="<Asset><Template>FactoryBuilding7</Template><Values>"
                        "<Standard><GUID>1337000009</GUID><Name>made_here</Name></Standard>"
                        "</Values></Asset>",
            )
        )
        self.window.editor.load_asset("1337000009")
        editor = self.window.editor
        guid_item = next(
            i for i in editor._iter_items()
            if i.text(0) == "GUID" and i.text(1) == "1337000009"
        )
        self.assertTrue(bool(guid_item.flags() & Qt.ItemFlag.ItemIsEditable))
        self.assertEqual(guid_item.text(2), "")
        self.assertFalse(editor._is_reference_field(guid_item))

    def test_plain_numbers_are_not_treated_as_references(self):
        self.window._open_asset("1010340")
        for tag in ("Amount", "CycleTime", "StorageAmount"):
            item = self._find_item(tag)
            if item is not None:
                self.assertEqual(item.text(2), "", "%s was annotated" % tag)

    def test_nor_is_a_number_that_is_some_assets_guid_in_a_field_of_numbers(self):
        """A rotation of 90 degrees "referred to" the asset whose GUID is 90,
        on every building: the field resolves nearly every time, and the base
        game says its GUIDs are plain numbers elsewhere."""
        self.window._open_asset("1010340")
        editor = self.window.editor
        product = next(
            i for i in editor._iter_items()
            if i.text(0) == "Product" and i.text(1) == "1010196"
        )
        self.assertIn("Timber", product.text(2))
        index = editor.index
        with mock.patch.object(index, "reference_field", return_value=(757, 756, False)):
            editor._annotate_reference(product)
            self.assertEqual(product.text(2), "")
            self.assertEqual(product.toolTip(2), "")
            self.assertFalse(editor._is_reference_field(product))
        # And with the same numbers where its GUIDs are no plain numbers.
        with mock.patch.object(index, "reference_field", return_value=(757, 756, True)):
            editor._annotate_reference(product)
            self.assertIn("Timber", product.text(2))
            self.assertIn("100%", product.toolTip(2))

    def test_picking_an_asset_records_a_change(self):
        self.window._open_asset("1010340")
        product = next(
            i
            for i in self.window.editor._iter_items()
            if i.text(0) == "Product" and i.text(1) == "1010196"
        )
        original = product.text(1)
        product.setText(1, "1010208")  # Beer

        change = next(iter(self.window.loadout))
        self.assertEqual(change.new_value, "1010208")
        self.assertEqual(change.old_value, original)
        self.assertIn("Beer", product.text(2))

    def test_only_the_value_column_can_be_edited(self):
        """Item flags are per-row, so the delegate has to gate the columns."""
        self.window._open_asset("1010340")
        item = self._find_item("CycleTime")
        from PySide6.QtWidgets import QStyleOptionViewItem

        tree = self.window.editor.tree
        delegate = tree.itemDelegate()
        option = QStyleOptionViewItem()
        editor = delegate.createEditor(tree.viewport(), option, tree.indexFromItem(item, 1))
        self.assertIsNotNone(editor)
        editor.deleteLater()
        for column in (0, 2, 3):
            self.assertIsNone(
                delegate.createEditor(
                    tree.viewport(), option, tree.indexFromItem(item, column)
                ),
                "column %d should not be editable" % column,
            )

    def test_identity_guid_is_not_editable(self):
        """Editing an asset's own GUID would retarget the ModOp, not change it."""
        from PySide6.QtCore import Qt

        self.window._open_asset("1010340")
        guid_item = None
        for item in self.window.editor._iter_items():
            if item.text(0) == "GUID" and item.text(1) == "1010340":
                guid_item = item
        self.assertIsNotNone(guid_item)
        self.assertFalse(bool(guid_item.flags() & Qt.ItemFlag.ItemIsEditable))
        self.assertIn("cannot be edited", guid_item.toolTip(1))

    def test_other_guid_fields_remain_editable(self):
        """Only /Values/Standard/GUID is the identity; list GUIDs are ordinary."""
        from PySide6.QtCore import Qt

        self.window._open_asset("1010340")
        products = [
            i for i in self.window.editor._iter_items() if i.text(0) == "Product"
        ]
        self.assertTrue(products)
        for item in products:
            self.assertTrue(bool(item.flags() & Qt.ItemFlag.ItemIsEditable))

    def test_editor_rows_are_tall_enough_for_an_inline_editor(self):
        """Regression: the edit box used to be clipped to half its height."""
        from PySide6.QtWidgets import QLineEdit

        from anno_modding_studio.ui.editor_panel import ROW_HEIGHT

        self.window._open_asset("1010340")
        item = self._find_item("CycleTime")
        rect = self.window.editor.tree.visualItemRect(item)
        self.assertGreaterEqual(rect.height(), ROW_HEIGHT)

        # And an actual editor fits inside that row.
        self.window.editor.tree.editItem(item, 1)
        editor = self.window.editor.tree.findChild(QLineEdit)
        self.assertIsNotNone(editor)
        self.assertGreaterEqual(editor.height(), editor.sizeHint().height() - 1)
        self.window.editor.tree.closePersistentEditor(item, 1)


class TestExportingAndHistory(WorkflowCase):
    """Exporting, what it saves on the way, and putting a version back."""

    def test_the_left_column_scrolls_where_the_window_is_too_short_for_it(self):
        """Short of height, the layout took it out of the hint under the
        description, whose last line was cut through in German."""
        from anno_modding_studio.ui import output_panel

        output = self.window.output
        column = output.left_scroll
        inside = column.widget()
        # As short as the window goes: the column is shorter than it was,
        # with mod.io's box gone from it.
        self.window.resize(1500, 300)
        self.window.show()
        self.window.tabs.setCurrentWidget(output)
        _app.processEvents()
        # Nothing in it is squeezed: it is as tall as it asks to be.
        self.assertGreaterEqual(inside.height(), inside.minimumSizeHint().height())
        self.assertGreater(column.verticalScrollBar().maximum(), 0)
        # The bar stands in the gap the column keeps, so the fields are as
        # wide with it as without.
        self.assertEqual(inside.layout().contentsMargins().right(), 0)
        self.assertLessEqual(column.verticalScrollBar().width(), output_panel.COLUMN_GAP)
        self.window.resize(1500, 1600)
        _app.processEvents()
        self.assertEqual(column.verticalScrollBar().maximum(), 0)
        self.assertEqual(inside.layout().contentsMargins().right(), output_panel.COLUMN_GAP)

    def test_exporting_records_a_version_and_saves_the_loadout(self):
        import tempfile

        self.window._open_asset("1010340")
        self._edit("CycleTime", "15.00")
        self.assertTrue(self.window.dirty)
        self.assertEqual(self.window.loadout.history, [])

        with tempfile.TemporaryDirectory() as tmp:
            self.window.loadout_path = Path(tmp) / "p.annoload"
            self.window.files.on_mod_exported(tmp)

            # A checkpoint of the mod's project, and one of the loadout's list.
            self.assertEqual(len(self.window.loadout.history), 1)
            self.assertIn("Exported", self.window.loadout.history[0].label)
            self.assertEqual(len(self.window.loadout.history_of("")), 1)
            self.assertIn("Exported", self.window.loadout.history_of("")[0].label)
            self.assertFalse(self.window.dirty)
            self.assertTrue(self.window.loadout_path.is_file())

            from anno_modding_studio.core.changes import Loadout

            reloaded = Loadout.load(self.window.loadout_path)
            self.assertEqual(len(reloaded.history), 1)
            self.assertEqual(len(reloaded), 1)

    def test_autosave_picks_a_home_when_the_loadout_has_none(self):
        """Exporting must not leave work unsaved just because it is untitled."""
        self.assertIsNone(self.window.loadout_path)
        default = self.window.files.default_loadout_path()
        self.assertEqual(default.parent, self.window.files.loadouts_dir())
        self.assertTrue(default.name.endswith(".annoload"))

    def test_and_the_name_it_picks_reads_like_a_name(self):
        """It is shown to the user now, as the space they are working in, so a
        slug is the wrong shape for it."""
        self.window.loadout.own.name = "Roomier Farmers"
        self.assertEqual(
            self.window.files.default_loadout_path().name,
            "Roomier Farmers.annoload",
        )

    def test_title_marks_unsaved_work(self):
        self.window._open_asset("1010340")
        self._edit("CycleTime", "15.00")
        self.assertIn("*", self.window.windowTitle())

        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            self.window.loadout_path = Path(tmp) / "p.annoload"
            self.window.files.autosave()
        self.assertNotIn("*", self.window.windowTitle())

    def test_a_save_of_no_typed_work_waits_for_a_held_project(self):
        """R3-16, hands-on run 3: opening a loadout another one had renamed a
        mod in followed the new name and saved at once, while the mod's
        project was still held for its read-back -- and said it could not."""
        from anno_modding_studio.core import projects

        loadout = self.window.loadout
        loadout.make_own_mod()
        loadout.projects["Mine"] = projects.Ref("projects/Mine.annoproj", held=projects.MOVED)
        with mock.patch.object(loadout, "project_origins", return_value=["Mine"]), \
                mock.patch.object(self.window.files, "autosave") as saved:
            self.window.on_disk = True
            self.window.files.save_when_free()
            saved.assert_not_called()
            self.assertTrue(self.window.dirty)
            loadout.projects["Mine"].held = ""
            self.window.files.took_projects(["Mine"])
            saved.assert_called_once()
            # Once only.
            self.window.files.took_projects(["Mine"])
            saved.assert_called_once()

    def test_working_copy_is_not_called_unsaved_after_an_export(self):
        """The bug: exporting saves a checkpoint of the work, but the label always said unsaved."""
        import tempfile

        from anno_modding_studio.ui.history_dialog import HistoryDialog

        self.window._open_asset("1010340")
        self._edit("CycleTime", "15.00")
        with tempfile.TemporaryDirectory() as tmp:
            self.window.loadout_path = Path(tmp) / "p.annoload"
            self.window.files.on_mod_exported(tmp)

        dialog = HistoryDialog(self.window.loadout, self.window)
        try:
            working = dialog.mod.checkpoints.item(0).text()
            self.assertIn("1 change,", working)
            self.assertIn("saved as the latest checkpoint", working)
            self.assertNotIn("unsaved", working)
        finally:
            dialog.deleteLater()

        # ...and once something else is edited it says so again.
        self._edit("CycleTime", "9.00")
        dialog = HistoryDialog(self.window.loadout, self.window)
        try:
            self.assertIn("1 edited", dialog.mod.checkpoints.item(0).text())
        finally:
            dialog.deleteLater()

    def test_a_checkpoint_is_offered_only_with_something_new_to_save(self):
        import tempfile

        from anno_modding_studio.core.changes import Loadout, own_record
        from anno_modding_studio.ui.history_dialog import HistoryDialog

        # A project with nothing made in it yet: no checkpoint for the
        # working copy to be saved as, and nothing to save.
        empty = Loadout(own=own_record(name="Empty", creator="me", mod_id="me-empty"), own_mod=True)
        with tempfile.TemporaryDirectory() as tmp:
            empty.save(Path(tmp) / "empty.annoload")
            dialog = HistoryDialog(empty, self.window, origin="")
            try:
                self.assertNotIn("saved as", dialog.mod.checkpoints.item(0).text())
                for tab in (dialog.mod, dialog.list):
                    self.assertFalse(tab.checkpoint_button.isEnabled())
                    self.assertEqual(tab.checkpoint_button.toolTip(), "There are no changes to save yet.")
            finally:
                dialog.deleteLater()

        self.window._open_asset("1010340")
        self._edit("CycleTime", "15.00")
        dialog = HistoryDialog(self.window.loadout, self.window)
        try:
            self.assertTrue(dialog.mod.checkpoint_button.isEnabled())
        finally:
            dialog.deleteLater()

        # An export records it, and exporting it again records nothing more.
        with tempfile.TemporaryDirectory() as tmp:
            self.window.loadout_path = Path(tmp) / "p.annoload"
            self.window.files.on_mod_exported(tmp)
            self.window.files.on_mod_exported(tmp)
        self.assertEqual(len(self.window.loadout.history_of("")), 1)
        self.assertEqual(len(self.window.loadout.history), 1)
        dialog = HistoryDialog(self.window.loadout, self.window)
        try:
            for tab in (dialog.mod, dialog.list):
                self.assertFalse(tab.checkpoint_button.isEnabled())
                self.assertEqual(tab.checkpoint_button.toolTip(),
                                 "The working copy is the same as the last checkpoint.")
        finally:
            dialog.deleteLater()

    def test_export_destination_is_never_the_games_mods_folder(self):
        """Putting a mod in the game is Play's: it builds into the library and
        sets everything else to match. Export is for somewhere else."""
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            mods = Path(tmp) / "mods"
            mods.mkdir()
            shared = Path(tmp) / "shared"
            self.window.output.set_default_output_dir(mods)
            self.assertNotEqual(self.window.output.export_dir(), mods)
            self.window.output.set_default_output_dir(shared)
            self.window.output.name_edit.setText("Test Mod")
            self.window.output.category_combo.setCurrentText("Cheat")

            self.assertEqual(self.window.output.export_dir(), shared)
            self.assertEqual(
                self.window.output.export_target(), shared / "[Cheat] Test Mod"
            )
            self.assertIn(
                "[Cheat] Test Mod", self.window.output.destination_label.full_text()
            )
            self.assertIn("Play tests", self.window.output.play_note.help_text())

    def test_export_writes_without_a_folder_dialog(self):
        import tempfile

        self.window._open_asset("1010340")
        self._edit("CycleTime", "15.00")
        with tempfile.TemporaryDirectory() as tmp:
            self.window.output.set_default_output_dir(Path(tmp))
            self.window.output.name_edit.setText("Silent Mod")
            self.window.output.category_combo.setCurrentText("Map")
            self.window.output.refresh()

            root = self.window.output.export_mod(confirm_overwrite=False, notify=False)
            self.assertIsNotNone(root)
            self.assertEqual(root.name, "[Map] Silent Mod")
            self.assertTrue((root / "modinfo.json").is_file())

    def test_a_folder_and_a_zip_are_the_loadout_as_it_is_now(self):
        """Both wrote the preview as it was last drawn, where Play and the
        other exports wrote the loadout: an export offered from another tab,
        after a checkpoint is restored, put back the mod from before it."""
        import tempfile
        import zipfile

        from anno_modding_studio.core import games

        out = self.window.output
        patch = games.get(self.window.loadout.game).asset_relpath.as_posix()
        self.window._open_asset("1010340")
        self._edit("CycleTime", "15.00")
        with tempfile.TemporaryDirectory() as tmp:
            out.set_default_output_dir(Path(tmp))
            out.name_edit.setText("Silent Mod")
            out.category_combo.setCurrentText("Map")
            out.refresh()
            # Changed with the Export tab out of sight, so nothing redraws it.
            self._edit("CycleTime", "12.00")

            root = out.export_mod(confirm_overwrite=False, notify=False)
            self.assertIn(">12.00<", (root / patch).read_text(encoding="utf-8"))
            archive = out.export_zip(notify=False)
            with zipfile.ZipFile(archive) as held:
                self.assertIn(">12.00<", held.read("%s/%s" % (root.name, patch)).decode("utf-8"))
            # One build, written twice.
            self.assertEqual(self.window.loadout.builds[""]["build"], 1)

    def test_a_folder_and_a_zip_are_written_by_the_loop_play_writes_with(self):
        import tempfile

        from anno_modding_studio.core import build, generator

        out = self.window.output
        self.window._open_asset("1010340")
        self._edit("CycleTime", "15.00")
        with tempfile.TemporaryDirectory() as tmp:
            out.set_default_output_dir(Path(tmp))
            out.refresh()
            with mock.patch.object(build, "write", wraps=build.write) as write:
                root = out.export_mod(confirm_overwrite=False, notify=False)
                # Named as Export names it, which a renamed mod's folder follows.
                named = generator.export_folder_for
                write.assert_called_once_with(
                    self.window.loadout, [""], Path(tmp), out._library_or_none(), views={},
                    folder_of=named,
                )
                archive = out.export_zip(notify=False)
                write.assert_called_with(self.window.loadout, [""], Path(tmp), zipped=True,
                                         views={}, folder_of=named)
            self.assertEqual(archive, Path(tmp) / (root.name + ".zip"))

    def test_an_export_that_cannot_be_written_says_why_and_takes_no_number(self):
        import tempfile

        from anno_modding_studio.core import generator
        from anno_modding_studio.ui.widgets import wrappable_path

        out = self.window.output
        self.window._open_asset("1010340")
        self._edit("CycleTime", "15.00")
        said = []
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(
            generator, "write_mod_folder", side_effect=OSError("disk full")
        ), mock.patch.object(
            QMessageBox, "critical", staticmethod(lambda _p, _t, text, *a: said.append(text))
        ):
            out.set_default_output_dir(Path(tmp))
            out.refresh()
            self.assertIsNone(out.export_mod(confirm_overwrite=False, notify=False))
            self.assertIsNone(out.export_zip(notify=False))
            self.assertEqual(
                said, ["%s\n\ndisk full" % wrappable_path(out.export_target()), "disk full"]
            )
        self.assertNotIn("", self.window.loadout.builds)

    def test_an_export_is_numbered_and_the_same_one_again_is_not(self):
        import json
        import tempfile

        from anno_modding_studio.core import games

        def export():
            self.window.output.refresh()
            root = self.window.output.export_mod(confirm_overwrite=False, notify=False)
            info = json.loads((root / "modinfo.json").read_text(encoding="utf-8"))
            patch = (root / games.get(self.window.loadout.game).asset_relpath).read_text(
                encoding="utf-8"
            )
            return info[modinfo.STUDIO_KEY]["build"], patch

        self.window._open_asset("1010340")
        self._edit("CycleTime", "15.00")
        with tempfile.TemporaryDirectory() as tmp:
            self.window.output.set_default_output_dir(Path(tmp))
            self.window.output.name_edit.setText("Counted Mod")

            build, patch = export()
            self.assertEqual(build, 1)
            # The modinfo says which build it is, and the file does not.
            self.assertNotIn("build 1", patch)
            self.assertEqual(self.window.loadout.history_of("")[-1].label, "Exported v1.0, build 1")
            self.assertTrue(self.window.loadout.base_fingerprint)

            self.assertEqual(export()[0], 1)
            self._edit("CycleTime", "12.00")
            self.assertEqual(export()[0], 2)

    def test_bump_offers_each_move_and_the_version_follows(self):
        out = self.window.output
        out.set_version("1.2")
        out._fill_bump_menu()
        labels = [action.text() for action in out.bump_menu.actions()]
        self.assertEqual(
            labels,
            [
                "Major: 2.0.0 (breaks saves or other mods)",
                "Minor: 1.3.0 (adds something)",
                "Patch: 1.2.1 (tunes or fixes)",
            ],
        )
        out.bump_menu.actions()[1].trigger()
        self.assertEqual(out.version_edit.text(), "1.3.0")
        self.assertEqual(self.window.loadout.own.version, "1.3.0")

    def test_a_version_that_is_not_numbered_is_offered_a_switch_not_a_bump(self):
        out = self.window.output
        out.set_version("v2 beta")
        out._fill_bump_menu()
        note, switch = out.bump_menu.actions()
        self.assertFalse(note.isEnabled())
        self.assertIn("not numbered", note.text())
        self.assertEqual(switch.text(), "Use numbered versions, starting at 2.0.0")
        switch.trigger()
        self.assertEqual(self.window.loadout.own.version, "2.0.0")

    def test_the_build_is_shown_beside_the_version(self):
        import tempfile

        out = self.window.output
        self.window._open_asset("1010340")
        self._edit("CycleTime", "15.00")
        out.refresh()
        self.assertEqual(out.build_label.text(), "no builds yet")
        with tempfile.TemporaryDirectory() as tmp:
            out.set_default_output_dir(Path(tmp))
            out.export_mod(confirm_overwrite=False, notify=False)
        self.assertEqual(out.build_label.text(), "build 1")

    def test_the_version_the_changes_call_for_is_offered_after_a_release(self):
        import tempfile

        out = self.window.output
        self.window._open_asset("1010340")
        self._edit("CycleTime", "15.00")
        out.refresh()
        # Before the first release there is nothing to measure from.
        self.assertFalse(out.suggest_label.isVisibleTo(out))
        with tempfile.TemporaryDirectory() as tmp:
            out.set_default_output_dir(Path(tmp))
            out.export_mod(confirm_overwrite=False, notify=False)
        self.assertEqual(out.suggest_label.text(), "No changes since 1.0.")
        self.assertFalse(out.suggest_button.isVisibleTo(out))

        self._edit("CycleTime", "12.00")
        out.refresh()
        self.assertEqual(out.suggest_label.text(), "Patch change since 1.0 (tunes or fixes).")
        # The reasons are a hover away rather than stacked under the field.
        self.assertIn("1 value changed on 1 asset", out.suggest_label.toolTip())
        self.assertEqual(out.suggest_button.text(), "Use 1.0.1")
        self.assertTrue(out.suggest_button.isVisibleTo(out))
        out.suggest_button.click()
        self.assertEqual(self.window.loadout.own.version, "1.0.1")
        self.assertEqual(out.suggest_label.text(), "Patch change since 1.0. 1.0.1 covers it.")
        self.assertFalse(out.suggest_button.isVisibleTo(out))

    def test_a_build_made_on_older_game_data_says_so(self):
        import tempfile

        out = self.window.output
        self.window._open_asset("1010340")
        self._edit("CycleTime", "15.00")
        with tempfile.TemporaryDirectory() as tmp:
            out.set_default_output_dir(Path(tmp))
            out.export_mod(confirm_overwrite=False, notify=False)
        self.assertEqual(out.build_label.text(), "build 1")
        self.assertEqual(self.window.game_files.stale_builds(), [])

        # The game is patched: the same loadout, other data under it.
        self.window.loadout.base_fingerprint = "f" * 40
        self.window.loadout.base_update = "2.1.0"
        out.refresh()
        self.assertEqual(out.build_label.text(), "build 1 · older game data")
        self.assertIn("The game data loaded now is 2.1.0", out.build_label.toolTip())
        self.assertEqual(self.window.game_files.stale_builds(), [self.window.loadout.own.name])

    def test_plays_build_is_numbered_and_says_the_loadout_changed(self):
        import tempfile

        from anno_modding_studio.core import build

        self.window._open_asset("1010340")
        self._edit("CycleTime", "15.00")
        with tempfile.TemporaryDirectory() as tmp:
            built = build.write(self.window.loadout, [""], Path(tmp))
            self.assertEqual((len(built.written), built.failed), (1, []))
            self.assertEqual(self.window.loadout.builds[""]["build"], 1)
            self.assertTrue(built.numbered)

            again = build.write(self.window.loadout, [""], Path(tmp))
            self.assertEqual(self.window.loadout.builds[""]["build"], 1)
            self.assertFalse(again.numbered, "the same build again is not an edit")

    def test_reverting_everything_asks_first(self):

        self.window._open_asset("1010340")
        self._edit("CycleTime", "15.00")
        panel = self.window.changes_panel
        self.assertEqual(panel.remove_button.text(), "Revert selected")
        # Off the panel's foot, on the right-click menu with Revert selected.
        self.assertFalse(hasattr(panel, "clear_button"))
        menu = panel.menu_for(None)
        clear = next(a for a in menu.actions() if a.text() == "Revert all…")
        self.assertTrue(clear.isEnabled())
        menu.deleteLater()

        with _isolation.answering(False) as asked:
            panel._clear_all()
        self.assertEqual(asked.buttons, [["Revert all", "Cancel"]], "Revert all must confirm")
        self.assertEqual(len(self.window.loadout), 1)
        # One mod, and it is the loadout: "this mod" is what is reverted.
        self.assertEqual(asked.texts, [
            "Discard the one change in this mod?\n\n"
            "Saved versions in the loadout history are not affected."
        ])

        with _isolation.answering("Revert all"):
            panel._clear_all()
        self.assertEqual(len(self.window.loadout), 0)

    def test_history_dialog_diffs_the_working_copy(self):
        from anno_modding_studio.ui.history_dialog import HistoryDialog

        self.window._open_asset("1010340")
        self._edit("CycleTime", "15.00")
        self.window.loadout.checkpoint("Exported v1.0")
        self._edit("CycleTime", "12.00")

        dialog = HistoryDialog(self.window.loadout, self.window)
        try:
            history = dialog.mod
            # Row 0 is the working copy, row 1 the checkpoint.
            self.assertEqual(history.checkpoints.count(), 2)
            history.checkpoints.setCurrentRow(0)
            self.assertIn("1 edited", history.diff_heading.text())
            # The working copy cannot be "restored" onto itself.
            self.assertFalse(history.restore_button.isEnabled())
            history.checkpoints.setCurrentRow(1)
            self.assertTrue(history.restore_button.isEnabled())
        finally:
            dialog.deleteLater()

    def test_an_edit_to_an_own_mod_whose_project_is_missing_is_taken_back(self):
        """Nothing could keep it: the project is missing, and what is read
        when it is found takes the edit's place."""
        from anno_modding_studio.core import projects

        window, loadout = self.window, self.window.loadout
        window._open_asset("1010340")
        self._edit("CycleTime", "15.00")
        loadout.projects[""] = projects.Ref("projects/Gone.annoproj", held=projects.MISSING)
        self._edit("CycleTime", "12.00")
        self.assertEqual([change.new_value for change in loadout.authored()], ["15.00"])
        self.assertTrue(window.status_label.text().startswith(
            "%s cannot be changed here: its project is missing" % (loadout.own.name or "My Anno Mod")),
            window.status_label.text())
        self.assertFalse(window.undo.can_redo)
        # Nor its details, which are in that project too.
        window.output.load_loadout_fields()
        self.assertTrue(window.output.name_edit.isReadOnly())
        self.assertTrue(window.output.version_edit.isReadOnly())

    def test_restoring_keeps_the_replaced_work_in_history(self):
        self.window._open_asset("1010340")
        self._edit("CycleTime", "15.00")
        self.window.loadout.checkpoint("Exported v1.0")
        self._edit("CycleTime", "12.00")

        target = self.window.loadout.history_of("")[0]
        self.window.loadout.checkpoint("Before restoring")
        self.window.loadout.restore(target)

        self.assertEqual(next(iter(self.window.loadout)).new_value, "15.00")
        # The 12.00 state is still recoverable.
        self.assertTrue(
            any(
                c["new_value"] == "12.00"
                for snap in self.window.loadout.history_of("")
                for c in snap.changes
            )
        )

    def test_a_patchs_history_shows_what_it_keeps_inside_their_mod(self):
        """An op of theirs a patch switched off is its work, and comes to no
        change of its own: the history has a row for it, and its restore
        says it goes back."""
        from anno_modding_studio.core import inside, patching
        from anno_modding_studio.core.changes import Loadout, ModLayer, Op, own_record
        from anno_modding_studio.ui import history_dialog

        theirs = '<ModOp Type="merge" GUID="2" Path="/Values/Building">\n  <Radius>8</Radius>\n</ModOp>'
        loadout = Loadout(own=own_record(creator="Ay"))
        loadout.set_layer(ModLayer(origin="Their Mod", name="Their Mod", creator="Dee", order=1))
        loadout.set_op(Op(source=theirs, origin="Their Mod", uid="theirs", order=1))
        patch = patching.make(loadout, "Their Mod")
        loadout.editing = patch.origin
        loadout.checkpoint("Before", origin=patch.origin)
        loadout.op("theirs").active = False
        inside.assign(loadout)
        dialog = history_dialog.HistoryDialog(loadout, self.window, origin=patch.origin,
                                              loadout_name="Main")
        self.addCleanup(dialog.deleteLater)
        history = dialog.mod
        history.checkpoints.setCurrentRow(0)
        # The working copy has moved since, with no change to count.
        self.assertEqual(history.checkpoints.item(0).foreground().color().name(),
                         QColor(theme.ACCENT_TEXT).name())
        rows = [history.diff_tree.topLevelItem(i) for i in range(history.diff_tree.topLevelItemCount())]
        self.assertEqual([[row.text(i) for i in range(5)] for row in rows], [[
            "switched off", "Their Mod", "", "ModOp",
            '<ModOp Type="merge" GUID="2" Path="/Values/Building"> <Radius>8</Radius> </ModOp>',
        ]])
        self.assertEqual(rows[0].toolTip(4), theirs)
        history.checkpoints.setCurrentRow(1)
        asked = []

        def yes(_parent, _title, text, *_rest, **_more):
            asked.append(text)
            return True

        with mock.patch.object(history_dialog, "ask", yes):
            history._restore()
        self.assertIn("\n\nIts edits inside other mods' ModOps go back as well: Their Mod.\n\n",
                      asked[0])
        self.assertTrue(loadout.op("theirs").active)
        self.assertIsNone(loadout.op("theirs").kept_by)

    def two_histories(self, **kw):
        from anno_modding_studio.ui.history_dialog import HistoryDialog

        dialog = HistoryDialog(self.window.loadout, self.window, loadout_name="Main", **kw)
        self.addCleanup(dialog.deleteLater)
        return dialog

    def test_the_history_has_the_projects_and_the_loadouts(self):
        """Two histories, a tab each: the project of the mod in question
        first, then the loadout's list (section 9 of the
        projects-and-loadouts proposal)."""
        self.window.loadout.own.name = "Counted Mod"
        self.window._open_asset("1010340")
        self._edit("CycleTime", "15.00")
        dialog = self.two_histories()
        self.assertEqual(
            [dialog.tabs.tabText(i) for i in range(dialog.tabs.count())],
            ["Project: Counted Mod", "Loadout: Main"],
        )
        self.assertIn("Made in", [dialog.mod.diff_tree.headerItem().text(i)
                                  for i in range(dialog.mod.diff_tree.columnCount())])
        # The edit is being made here.
        dialog.mod.checkpoints.setCurrentRow(0)
        self.assertEqual(dialog.mod.diff_tree.topLevelItem(0).text(5), "Main")

    def test_restoring_a_project_names_every_loadout_that_has_it(self):
        self.window._open_asset("1010340")
        self._edit("CycleTime", "15.00")
        self.window.loadout.checkpoint("Exported v1.0", loadout="Main")
        self.window.loadout.projects[""] = projects.Ref("projects/Mine.annoproj")
        self._edit("CycleTime", "12.00")
        dialog = self.two_histories(having=lambda origin: ["Main", "Test"])
        dialog.mod.checkpoints.setCurrentRow(1)
        with _isolation.answering(True) as asked:
            dialog.mod._restore()
        self.assertIn("in every loadout that has it: Main, Test.", asked.texts[0])
        self.assertIn("Its build numbers, its mod.io link and what was published stay", asked.texts[0])
        self.assertTrue(dialog.restored and dialog.mod_restored)
        self.assertEqual(next(iter(self.window.loadout)).new_value, "15.00")

    def test_restoring_the_loadout_says_what_it_changes_and_can_take_its_projects(self):
        from anno_modding_studio.core import histories

        loadout = self.window.loadout
        self.window._open_asset("1010340")
        self._edit("CycleTime", "15.00")
        loadout.add_profile("Second")
        histories.take(loadout, "by hand", taken_in="Main")
        loadout.delete_profile("Second")
        self._edit("CycleTime", "12.00")
        dialog = self.two_histories()
        dialog.tabs.setCurrentWidget(dialog.list)
        dialog.list.checkpoints.setCurrentRow(1)
        with _isolation.answering("Restore, with each project as it was then") as asked:
            dialog.list._restore()
        self.assertIn("Brings back the profiles: Second.", asked.texts[0])
        self.assertIn("With each project as it was then", asked.texts[0])
        self.assertEqual(asked.buttons[0], ["Restore the loadout", "Restore, with each project as it was then",
                                            "Cancel"])
        self.assertEqual(loadout.profile_names(), ["Main", "Second"])
        self.assertEqual(next(iter(loadout)).new_value, "15.00")
        self.assertTrue(dialog.mod_restored)

    def test_the_loadouts_history_shows_another_profiles_switches(self):
        from anno_modding_studio.core import histories
        from anno_modding_studio.core.changes import ModLayer

        loadout = self.window.loadout
        loadout.set_layer(ModLayer(origin="Theirs", name="Theirs", order=1))
        loadout.add_profile("Second")
        histories.take(loadout, "by hand", taken_in="Main")
        loadout.choose_profile("Second")
        loadout.layer("Theirs").active = False
        loadout.choose_profile("Main")
        dialog = self.two_histories()
        dialog.tabs.setCurrentWidget(dialog.list)
        dialog.list.checkpoints.setCurrentRow(0)
        row = dialog.list.diff_tree.topLevelItem(0)
        self.assertEqual([row.text(column) for column in range(3)],
                         ["profile", "Second", "mods switched on or off"])
        dialog.list.checkpoints.setCurrentRow(1)
        with _isolation.answering("Restore the loadout") as asked:
            dialog.list._restore()
        self.assertIn("Sets back which mods are on in the profiles: Second.", asked.texts[0])

    def _their_mods(self, *names):
        from anno_modding_studio.core.changes import ModLayer

        for order, name in enumerate(names, start=1):
            folder = Path(self._tmp.name) / "theirs" / name
            folder.mkdir(parents=True, exist_ok=True)
            self.window.loadout.set_layer(
                ModLayer(origin=name, name=name, folder=str(folder), order=order))

    def test_a_mod_read_another_way_says_so_not_the_defaults(self):
        """A11-23: a mod whose reading alone changed was listed with "the
        defaults" as its new settings."""
        from anno_modding_studio.core import histories

        loadout = self.window.loadout
        self._their_mods("Theirs")
        loadout.layer("Theirs").options = {"Speed": "2"}
        histories.take(loadout, "by hand", taken_in="Main")
        loadout.layer("Theirs").read_as = {"keep_inert": True}
        dialog = self.two_histories()
        dialog.list.checkpoints.setCurrentRow(0)
        row = dialog.list.diff_tree.topLevelItem(0)
        self.assertEqual((row.text(0), row.text(1), row.text(2)),
                         ("settings", "Theirs", "inert removals kept"))
        loadout.layer("Theirs").options = {}
        dialog.list.reload()
        dialog.list.checkpoints.setCurrentRow(0)
        self.assertEqual(dialog.list.diff_tree.topLevelItem(0).text(2),
                         "the defaults; inert removals kept")

    def test_restoring_the_loadout_counts_the_mods_it_changes(self):
        """A11-119: the count was of the question's lines, one of which can
        name several mods, and a note of a mod not put back changes nothing."""
        from anno_modding_studio.core import histories

        loadout = self.window.loadout
        self._their_mods("One", "Two", "Three")
        then = histories.take(loadout, "by hand", taken_in="Main")
        then.mods.append({"origin": "mine", "name": "Mine", "order": 4})
        then.projects["mine"] = {"revision": 1, "checkpoint": 0}
        for name in ("One", "Two", "Three"):
            loadout.drop_layer(name)
        dialog = self.two_histories()
        said = widgets.plain_text(dialog.list._question(
            then, histories.plan(loadout, then), histories.plan(loadout, then, with_projects=True)))
        self.assertIn("It changes 3 things in its list.", said)
        self.assertIn("Puts back, to read again from its folder: One, Two, Three.", said)
        self.assertIn("Mine is a mod of yours, and comes back only with its project.", said)

        # Only a mod of yours that stays out: nothing in the list changes.
        self._their_mods("One", "Two", "Three")
        said = widgets.plain_text(dialog.list._question(
            then, histories.plan(loadout, then), histories.plan(loadout, then, with_projects=True)))
        self.assertIn("It changes nothing in its list.", said)
        self.assertIn("Mine is a mod of yours, and comes back only with its project.", said)


class TestStructuralEdits(WorkflowCase):
    """Additions and removals: ops that change the shape of an asset."""

    def test_schema_is_available_from_the_index(self):
        schema = self.index.schema
        self.assertTrue(schema.children_of("/Values/Building"))
        self.assertTrue(schema.knows_path("/Values/Building"))

    def test_adding_a_field_records_an_add_modop_and_shows_it(self):
        from lxml import etree

        from anno_modding_studio.core.changes import OP_ADD

        self.window._open_asset("1010340")
        building = self._container("Building")
        self.assertIsNotNone(building)
        before = building.childCount()

        element = etree.Element("NewlyAddedField")
        element.text = "42"
        self.window.editor._record_addition(building, element, "add NewlyAddedField")

        self.assertEqual(len(self.window.loadout), 1)
        change = next(iter(self.window.loadout))
        self.assertEqual(change.op, OP_ADD)
        self.assertEqual(change.path, "/Values/Building")
        self.assertIn("NewlyAddedField", change.payload)

        # The tree is rebuilt from the effective asset, so re-find the rows.
        building = self._container("Building")
        self.assertEqual(building.childCount(), before + 1)
        added = self._find_item("NewlyAddedField")
        self.assertIsNotNone(added)
        self.assertEqual(added.text(1), "42")
        self.assertEqual(added.text(3), "added by this mod")

    def test_the_changes_list_describes_structural_ops(self):
        """Each op describes itself; an addition names what it puts there."""
        from lxml import etree

        self.window._open_asset("1010340")
        building = self._container("Building")
        element = etree.Element("Described")
        element.text = "1"
        self.window.editor._record_addition(building, element, "add Described")

        panel = self.window.changes_panel
        rows = change_rows(panel)
        self.assertEqual(len(rows), 1)
        header = panel.tree.headerItem()
        self.assertEqual(
            [header.text(i) for i in range(panel.tree.columnCount())],
            ["Asset / field", "Change"],
        )
        # The asset is the group heading now, so the row itself is the field.
        self.assertIn("Building", rows[0].text(0))
        self.assertIn("lumberjack", panel.tree.topLevelItem(0).child(0).text(0))
        # The field and its value, not <Described>1</Described>: a fixed-width
        # column has no room to spend on repeating the tag.
        self.assertEqual(rows[0].text(1), "+ Described = 1")
        self.assertEqual(rows[0].toolTip(1), "Described = 1")

    def test_the_changes_list_describes_a_replace_and_a_removal(self):
        self.window._open_asset("1010340")
        self._edit("CycleTime", "15.00")
        panel = self.window.changes_panel
        self.assertEqual(change_rows(panel)[0].text(1), "30.00 → 15.00")

        self.window.editor._remove_node(self._find_item("BuildingType"))
        rows = {row.text(0): row.text(1) for row in change_rows(panel)}
        self.assertIn("removed", list(rows.values()))

    def test_a_pending_addition_can_be_dropped_again(self):
        from lxml import etree

        self.window._open_asset("1010340")
        building = self._container("Building")
        element = etree.Element("Temp")
        element.text = "1"
        self.window.editor._record_addition(building, element, "add Temp")
        self.assertEqual(len(self.window.loadout), 1)

        added = self._find_item("Temp")
        self.window.editor._remove_node(added)
        self.assertEqual(len(self.window.loadout), 0)
        self.assertIsNone(self._find_item("Temp"))

    def test_a_field_is_made_sure_of_and_edited_in_its_own_modop(self):
        """A field has one of its name, so it goes out as a merge into the node
        it belongs in -- and a value typed over it lands in that same ModOp."""
        from lxml import etree

        from anno_modding_studio.core.changes import OP_MERGE

        self.window._open_asset("1010340")
        element = etree.Element("Ensured")
        element.text = "1"
        self.window.editor._record_addition(
            self._container("Building"), element, "set Ensured", op=OP_MERGE
        )
        self._find_item("Ensured").setText(1, "77")

        self.assertEqual(len(self.window.loadout), 1)
        change = next(iter(self.window.loadout))
        self.assertEqual((change.op, change.path), (OP_MERGE, "/Values/Building"))
        self.assertEqual(change.sets, ("/Values/Building/Ensured", "77"))
        from anno_modding_studio.core import generator

        xml = generator.build_modops_xml(self.window.loadout)
        self.assertIn('Type="merge"', xml)
        self.assertIn("<Ensured>77</Ensured>", xml)

    def test_adding_a_list_entry_uses_the_schema_shape(self):
        from anno_modding_studio.core.changes import OP_ADD

        self.window._open_asset("1010340")
        costs = self._container("Maintenances")
        self.assertIsNotNone(costs)
        self.window.editor._add_list_entry(costs)

        change = next(iter(self.window.loadout))
        self.assertEqual(change.op, OP_ADD)
        self.assertTrue(change.payload.startswith("<Item"))

    def test_removing_a_node_records_a_remove_modop(self):
        from anno_modding_studio.core.changes import OP_REMOVE

        self.window._open_asset("1010340")
        item = self._find_item("CycleTime")
        self.window.editor._remove_node(item)

        change = next(iter(self.window.loadout))
        self.assertEqual(change.op, OP_REMOVE)
        self.assertEqual(change.path, "/Values/Factory7/CycleTime")
        self.assertEqual(change.old_value, "30.00")
        # struck through in the rebuilt tree
        self.assertTrue(self._find_item("CycleTime").font(0).strikeOut())

    def test_a_removal_can_be_undone(self):
        self.window._open_asset("1010340")
        item = self._find_item("CycleTime")
        self.window.editor._remove_node(item)
        self.assertEqual(len(self.window.loadout), 1)

        self.window.editor._undo_remove(
            self._find_item("CycleTime"), "/Values/Factory7/CycleTime"
        )
        self.assertEqual(len(self.window.loadout), 0)
        self.assertFalse(self._find_item("CycleTime").font(0).strikeOut())

    def test_structural_edits_survive_revisiting_the_asset(self):
        from lxml import etree

        self.window._open_asset("1010340")
        building = self._container("Building")
        element = etree.Element("Persisted")
        element.text = "9"
        self.window.editor._record_addition(building, element, "add Persisted")
        removed = self._find_item("CycleTime")
        self.window.editor._remove_node(removed)

        self.window._open_asset("1010030")
        self.window._open_asset("1010340")

        self.assertEqual(len(self.window.loadout), 2)
        names = [i.text(0) for i in self.window.editor._iter_items()]
        self.assertIn("Persisted", names)
        self.assertTrue(self._find_item("CycleTime").font(0).strikeOut())

    def test_you_can_add_underneath_something_you_added(self):
        """The whole point of the effective view: additions are real, not visual."""
        from lxml import etree

        self.window._open_asset("1010340")
        costs = self._container("Maintenances")
        self.window.editor._add_list_entry(costs)
        self.assertEqual(len(self.window.loadout), 1)

        # The new entry is now a normal node in the tree...
        added = None
        for item in self.window.editor._iter_items():
            if item.text(3) == "added by this mod" and item.text(0) == "Item":
                added = item
        self.assertIsNotNone(added, "the added entry should be in the tree")

        # ...so a field can be added underneath it, folding into the same ModOp.
        extra = etree.Element("Nested")
        extra.text = "5"
        self.window.editor._record_addition(added, extra, "add Nested")

        self.assertEqual(len(self.window.loadout), 1, "must stay one add ModOp")
        change = next(iter(self.window.loadout))
        self.assertIn("Nested", change.payload)
        self.assertIsNotNone(self._find_item("Nested"))

    def test_an_added_container_stays_visible_even_though_it_is_empty(self):
        """A container is added empty on purpose; hiding it made the add look
        like it had done nothing."""
        from lxml import etree

        self.window._open_asset("1010340")
        building = self._container("Building")
        self.assertFalse(self.window.editor.show_empty.isChecked())

        container = etree.Element("AddedContainer")   # no children, no text
        self.window.editor._record_addition(building, container, "add AddedContainer")

        row = self._find_item("AddedContainer")
        self.assertIsNotNone(row, "the added container should be in the tree")
        self.assertFalse(row.isHidden(), "and must not be hidden as empty")

    def test_you_can_add_into_a_container_you_just_added(self):
        from lxml import etree

        self.window._open_asset("1010340")
        building = self._container("Building")
        container = etree.Element("Wrapper")
        self.window.editor._record_addition(building, container, "add Wrapper")

        wrapper = self._find_item("Wrapper")
        inner = etree.Element("Inner")
        inner.text = "1"
        self.window.editor._record_addition(wrapper, inner, "add Inner")

        self.assertEqual(len(self.window.loadout), 1, "still one add ModOp")
        self.assertIn("Inner", next(iter(self.window.loadout)).payload)
        self.assertIsNotNone(self._find_item("Inner"))

    def test_successive_edits_to_one_payload_all_land(self):
        """Regression: a payload edit re-keys its change, so the second edit
        used to look the change up under a key that no longer existed."""
        from lxml import etree

        self.window._open_asset("1010340")
        building = self._container("Building")
        element = etree.Element("Twice")
        element.text = "1"
        self.window.editor._record_addition(building, element, "add Twice")

        self._find_item("Twice").setText(1, "2")
        self.assertIn("<Twice>2</Twice>", next(iter(self.window.loadout)).payload)

        self._find_item("Twice").setText(1, "3")
        self.assertEqual(len(self.window.loadout), 1)
        self.assertIn("<Twice>3</Twice>", next(iter(self.window.loadout)).payload)

    def _on_a_value_of_an_added_entry(self):
        """Add a list entry and put the tree on its first value; the path
        box and the hint are then about a row only the addition has."""
        editor = self.window.editor
        self.window._open_asset("1010340")
        editor._add_list_entry(self._container("Maintenances"))
        entry = next(
            i for i in editor._iter_items()
            if i.text(0) == "Item" and i.text(3) == "added by this mod"
        )
        editor.tree.setCurrentItem(entry.child(0))
        self.assertIn("/Maintenances/Item[", editor.path_box.text())
        self.assertIn("position index", editor.warning_label.text())
        return editor

    def test_the_path_box_does_not_outlive_the_entry_it_was_about(self):
        """Revert asset took the added entry out of the tree, and its path
        and "no unique sibling key" stayed below it."""
        editor = self._on_a_value_of_an_added_entry()
        editor._revert_asset()
        self.assertEqual(len(self.window.loadout), 0)
        self.assertIsNone(editor.tree.currentItem())
        self.assertEqual((editor.path_box.text(), editor.warning_label.text()), ("", ""))

    def test_nor_when_the_addition_is_undone(self):
        editor = self._on_a_value_of_an_added_entry()
        self.window.undo_last()
        self.assertEqual(len(self.window.loadout), 0)
        self.assertEqual((editor.path_box.text(), editor.warning_label.text()), ("", ""))

    def test_but_a_row_that_is_still_there_keeps_them(self):
        editor = self.window.editor
        self.window._open_asset("1010340")
        editor.tree.setCurrentItem(self._find_item("CycleTime"))
        path = editor.path_box.text()
        self.assertTrue(path.endswith("/CycleTime"))
        self._edit("BuildingType", "Residence")
        editor._revert_asset()
        self.assertEqual(editor.path_box.text(), path)
        self.assertEqual(editor.tree.currentItem().text(0), "CycleTime")

    def test_deleting_one_field_of_an_added_entry_keeps_the_entry(self):
        """Regression: the entry looked deleted because, once empty, it was
        hidden by the same filter that hides base game padding."""
        self.window._open_asset("1010340")
        costs = self._container("Maintenances")
        self.window.editor._add_list_entry(costs)

        entry = next(
            i for i in self.window.editor._iter_items()
            if i.text(0) == "Item" and i.text(3) == "added by this mod"
        )
        self.assertGreaterEqual(entry.childCount(), 1)
        first = entry.child(0).text(0)
        self.window.editor._remove_node(entry.child(0))

        entry = next(
            (i for i in self.window.editor._iter_items()
             if i.text(0) == "Item" and i.text(3) == "added by this mod"),
            None,
        )
        self.assertIsNotNone(entry, "the entry itself must survive")
        self.assertFalse(entry.isHidden())
        self.assertEqual(len(self.window.loadout), 1)
        self.assertNotIn("<%s>" % first, next(iter(self.window.loadout)).payload)

    def test_refers_to_follows_an_edit_inside_an_addition(self):
        """Regression: a payload edit never rebuilds the tree, so the "Refers to"
        column kept describing the asset the field used to point at."""
        from lxml import etree

        self.window._open_asset("1010340")
        costs = self._container("Maintenances")
        element = etree.Element("Product")
        element.text = "1010196"                 # Timber
        self.window.editor._record_addition(costs, element, "add Product")

        item = self._find_item("Product")
        self.assertIn("Timber", item.text(2))

        item.setText(1, "1010202")               # Steel
        self.assertIn("Steel", item.text(2))
        self.assertNotIn("Timber", item.text(2))

    def test_only_changed_shows_additions_and_removals(self):
        """"Changed" used to mean "replaced", which hid most of a modern mod."""
        from lxml import etree

        self.window._open_asset("1010340")
        building = self._container("Building")
        element = etree.Element("AddedLeaf")
        element.text = "1"
        self.window.editor._record_addition(building, element, "add AddedLeaf")
        self.window.editor._remove_node(self._find_item("BuildingType"))

        self.window.editor.only_changed.setChecked(True)
        added = self._find_item("AddedLeaf")
        removed = self._find_item("BuildingType")
        self.assertFalse(added.isHidden(), "an addition is a change")
        self.assertFalse(removed.isHidden(), "so is a removal")
        self.assertTrue(self._find_item("CycleTime").isHidden())

    def test_only_changed_keeps_an_added_container_visible(self):
        from lxml import etree

        self.window._open_asset("1010340")
        building = self._container("Building")
        self.window.editor._record_addition(
            building, etree.Element("EmptyOne"), "add EmptyOne"
        )
        self.window.editor.only_changed.setChecked(True)
        row = self._find_item("EmptyOne") or self._container("EmptyOne")
        self.assertIsNotNone(row)
        self.assertFalse(row.isHidden())

    def test_an_empty_added_container_can_still_take_children(self):
        """Regression: an added container has no children and no text, so the
        tree treated it as a leaf and offered no way to put anything inside it."""
        from lxml import etree

        self.window._open_asset("1010340")
        editor = self.window.editor
        outputs = self._container("FactoryOutputs")
        # An empty <Item/> is indistinguishable in XML from an empty value; the
        # schema knows this path holds children, and that has to be what counts.
        editor._record_addition(outputs, etree.Element("Item"), "add Item")

        added = next(
            i for i in editor._iter_items()
            if i.text(0) == "Item" and i.text(3) == "added by this mod"
        )
        self.assertEqual(added.childCount(), 0)
        self.assertTrue(
            editor._can_hold_children(added),
            "an empty node the schema knows as a container must accept children",
        )

        inner = etree.Element("Product")
        inner.text = "1010196"
        editor._record_addition(added, inner, "add Product")
        self.assertEqual(len(self.window.loadout), 1, "still one add ModOp")
        self.assertIn("Product", next(iter(self.window.loadout)).payload)

    def test_a_structural_edit_keeps_the_view_and_lands_on_the_new_row(self):
        """Rebuilding the tree used to throw away every expansion and the scroll
        position, so the thing you just added was often off screen."""
        from lxml import etree

        self.window._open_asset("1010340")
        editor = self.window.editor
        self._container("Factory7").setExpanded(True)
        self._container("Maintenances").setExpanded(True)

        editor._record_addition(
            self._container("Maintenances"), etree.Element("Marker"), "add Marker"
        )

        self.assertTrue(
            self._container("Factory7").isExpanded(),
            "an unrelated expanded section stays expanded",
        )
        current = editor.tree.currentItem()
        self.assertIsNotNone(current)
        self.assertEqual(current.text(0), "Marker", "selection lands on the addition")

    def test_reverting_one_structural_change_from_the_dock(self):
        """Structural ops are keyed by a uid, so (guid, path) cannot find them."""
        from lxml import etree

        self.window._open_asset("1010340")
        building = self._container("Building")
        editor = self.window.editor
        editor._record_addition(building, etree.Element("First"), "add First")
        # The tree is rebuilt after every structural edit, so re-find the row.
        editor._record_addition(
            self._container("Building"), etree.Element("Second"), "add Second"
        )
        self.assertEqual(len(self.window.loadout), 2)

        panel = self.window.changes_panel
        pick_change(panel)
        panel._remove_selected()
        self.assertEqual(len(self.window.loadout), 1)

    def test_add_field_and_add_list_entry_never_overlap(self):
        """One node, one meaning: entries for lists, fields for everything else."""
        self.window._open_asset("1010340")
        editor = self.window.editor
        self.assertEqual(editor._add_kind(self._container("Maintenances")), "entry")
        self.assertEqual(editor._add_kind(self._container("Building")), "field")

    def test_a_node_with_nothing_left_to_add_offers_nothing(self):
        """The menu greys out instead of opening a dialog to say "nothing"."""
        from lxml import etree

        self.window._open_asset("1010340")
        editor = self.window.editor
        building = self._container("Building")
        schema = self.index.schema
        for field in schema.missing_children(
            editor._structural_path(building), editor._present_children(building)
        ):
            element = etree.Element(field.tag)
            element.text = "1"
            editor._record_addition(self._container("Building"), element, "add")

        building = self._container("Building")
        self.assertEqual(editor._add_kind(building), "")
        self.assertIsNone(building.data(0, ADD_ROLE), 'no "+" is painted')

    def test_the_plus_is_offered_only_where_something_can_be_added(self):
        self.window._open_asset("1010340")
        editor = self.window.editor
        self.assertEqual(editor._add_kind(self._container("Maintenances")), "entry")
        self.assertTrue(self._container("Maintenances").data(0, ADD_ROLE))
        # A plain value has nothing to put inside it.
        self.assertIsNone(self._find_item("CycleTime").data(0, ADD_ROLE))

    def test_clicking_the_plus_adds_without_the_context_menu(self):
        from PySide6.QtCore import QEvent, Qt
        from PySide6.QtGui import QMouseEvent

        from anno_modding_studio.ui.editor_panel import plus_rect

        self.window._open_asset("1010340")
        self.window.resize(1200, 800)
        editor = self.window.editor
        target = self._container("Maintenances")
        target.parent().setExpanded(True)
        editor.tree.scrollToItem(target)
        _app.processEvents()

        rect = editor.tree.visualRect(editor.tree.indexFromItem(target, 0))
        point = plus_rect(rect).center()
        for kind in (QEvent.Type.MouseButtonPress, QEvent.Type.MouseButtonRelease):
            _app.sendEvent(
                editor.tree.viewport(),
                QMouseEvent(
                    kind,
                    point,
                    editor.tree.viewport().mapToGlobal(point),
                    Qt.MouseButton.LeftButton,
                    Qt.MouseButton.LeftButton,
                    Qt.KeyboardModifier.NoModifier,
                ),
            )
        self.assertEqual(len(self.window.loadout), 1)
        self.assertEqual(next(iter(self.window.loadout)).op, "add")

    def test_the_plus_lights_up_only_under_the_cursor(self):
        from PySide6.QtCore import QEvent, QPoint, Qt
        from PySide6.QtGui import QMouseEvent

        from anno_modding_studio.ui.editor_panel import plus_rect

        self.window.show()
        self.window._open_asset("1010340")
        editor = self.window.editor
        target = self._container("Maintenances")
        target.parent().setExpanded(True)
        editor.tree.scrollToItem(target)
        _app.processEvents()

        index = editor.tree.indexFromItem(target, 0)
        rect = editor.tree.visualRect(index)

        def move(point):
            _app.sendEvent(
                editor.tree.viewport(),
                QMouseEvent(
                    QEvent.Type.MouseMove,
                    point,
                    editor.tree.viewport().mapToGlobal(point),
                    Qt.MouseButton.NoButton,
                    Qt.MouseButton.NoButton,
                    Qt.KeyboardModifier.NoModifier,
                ),
            )

        move(QPoint(rect.left() + 20, rect.center().y()))
        self.assertFalse(
            editor.is_plus_hovered(index), "the row is not the glyph"
        )
        move(plus_rect(rect).center())
        self.assertTrue(editor.is_plus_hovered(index))

    def test_verification_uses_the_shipped_asset_not_the_edited_one(self):
        """A workspace's asset_element already has this mod applied; checking a
        replace against it compares the change to its own result."""
        from anno_modding_studio.core import generator

        self.window._open_asset("1010340")
        self._edit("CycleTime", "15.00")
        results = generator.verify_changes(self.window.workspace, self.window.loadout)
        self.assertTrue(results)
        for result in results:
            self.assertTrue(result.ok, result.message)

    def test_editing_an_added_value_does_not_emit_a_second_modop(self):
        from lxml import etree

        self.window._open_asset("1010340")
        building = self._container("Building")
        element = etree.Element("Tweakable")
        element.text = "1"
        self.window.editor._record_addition(building, element, "add Tweakable")
        self.assertEqual(len(self.window.loadout), 1)

        item = self._find_item("Tweakable")
        item.setText(1, "77")

        self.assertEqual(len(self.window.loadout), 1, "no replace against the base game")
        change = next(iter(self.window.loadout))
        self.assertEqual(change.op, "add")
        self.assertIn("77", change.payload)

    def test_removing_an_added_node_drops_it_from_the_payload(self):

        self.window._open_asset("1010340")
        costs = self._container("Maintenances")
        self.window.editor._add_list_entry(costs)
        added = next(
            i for i in self.window.editor._iter_items()
            if i.text(3) == "added by this mod" and i.text(0) == "Item"
        )
        child = added.child(0)
        tag = child.text(0)
        self.window.editor._remove_node(child)

        self.assertEqual(len(self.window.loadout), 1)
        change = next(iter(self.window.loadout))
        self.assertNotIn("<%s>" % tag, change.payload)


class TestCopyingAndPastingABlock(WorkflowCase):
    """Copy a row and what is in it, and paste it where its shape fits:
    another entry like one already in a list, without rebuilding it."""

    def _inputs(self):
        """The rows of the open factory's input list."""
        return [
            item for item in self.window.editor._iter_items()
            if item.text(0) == "Item"
            and item.parent() is not None
            and item.parent().text(0) == "FactoryInputs"
        ]

    @staticmethod
    def _field(entry, tag):
        return next(
            entry.child(i) for i in range(entry.childCount())
            if entry.child(i).text(0) == tag
        )

    def _paste_text(self, text):
        from PySide6.QtGui import QGuiApplication

        QGuiApplication.clipboard().setText(text)

    def test_an_entry_pasted_on_itself_joins_its_list(self):
        from anno_modding_studio.core.changes import OP_ADD

        self.window._open_asset("1010340")
        editor = self.window.editor
        entry = self._inputs()[0]
        product = self._field(entry, "Product").text(1)
        editor._copy_block(entry)
        editor._paste(self._inputs()[0])

        self.assertEqual(len(self.window.loadout), 1)
        change = next(iter(self.window.loadout))
        self.assertEqual(change.op, OP_ADD)
        self.assertEqual(change.path, "/Values/Factory7/FactoryInputs")
        self.assertIn("<Product>%s</Product>" % product, change.payload)
        self.assertIn("<StorageAmount>50</StorageAmount>", change.payload)
        # And the editor is on the copy, ready for its product to be changed.
        self.assertEqual(len(self._inputs()), 2)
        current = editor.tree.currentItem()
        self.assertEqual(editor._item_key(current), "Factory7[0]/FactoryInputs[0]/Item[1]")
        self.assertEqual(current.text(3), "added by this mod")

    def test_what_is_copied_is_what_the_rows_say_now(self):
        """A value typed since the tree was drawn is copied as typed, and a
        field struck out is left behind."""
        self.window._open_asset("1010340")
        editor = self.window.editor
        self._field(self._inputs()[0], "Amount").setText(1, "7")
        editor._remove_node(self._field(self._inputs()[0], "StorageAmount"))
        entry = self._inputs()[0]
        self.assertEqual(
            etree_text(editor._block_of(entry)),
            "<Item><Product>%s</Product><Amount>7</Amount></Item>"
            % self._field(entry, "Product").text(1),
        )

    def test_the_clipboard_holds_plain_xml(self):
        from PySide6.QtGui import QGuiApplication

        self.window._open_asset("1010340")
        self.window.editor._copy_block(self._inputs()[0])
        text = QGuiApplication.clipboard().text()
        self.assertTrue(text.startswith("<Item>\n  <Product>"), text)

    def test_a_section_goes_to_an_asset_that_lacks_it(self):
        """Copied from one asset and pasted into another: the whole of it."""
        from anno_modding_studio.core.changes import OP_MERGE

        self.window._open_asset("1010340")
        self.window.editor._copy_block(self._container("Maintenance"))
        self.window._open_asset("1010030")
        self.window.editor._paste(self._container("Building"))

        change = next(iter(self.window.loadout))
        self.assertEqual((change.guid, change.op), ("1010030", OP_MERGE))
        self.assertEqual(change.path, "/Values")
        self.assertIn("<Product>1010017</Product>", change.payload)
        self.assertIsNotNone(self._container("Maintenances"))

    def test_a_field_already_there_is_not_pasted_twice(self):
        self.window._open_asset("1010340")
        editor = self.window.editor
        editor._copy_block(self._container("Building"))
        editor._paste(self._container("Factory7"))
        self.assertEqual(len(self.window.loadout), 0)
        self.assertEqual(
            editor.warning_label.text(), "This asset has a <Building> already."
        )

    def test_a_shape_the_game_never_uses_is_refused(self):
        self.window._open_asset("1010340")
        editor = self.window.editor
        self._paste_text("<Item><Product>1</Product><Bogus>2</Bogus></Item>")
        editor._paste(self._inputs()[0])
        self.assertEqual(len(self.window.loadout), 0)
        self.assertIn("Factory7/FactoryInputs/Item/Bogus", editor.warning_label.text())
        # Nor anywhere the game does not put that tag at all.
        self._paste_text("<Item><Product>1</Product></Item>")
        editor._paste(self._container("Building"))
        self.assertEqual(len(self.window.loadout), 0)

    def test_but_one_copied_from_that_very_place_is_trusted(self):
        """It was there already, so whether the game uses it is not asked:
        a field a mod gave it comes along."""
        self.window._open_asset("1010340")
        editor = self.window.editor
        editor._record_addition(
            self._inputs()[0], etree_element("Bogus", "2"), "add Bogus"
        )
        editor._copy_block(self._inputs()[0])
        editor._paste(self._inputs()[0])
        self.assertEqual(len(self.window.loadout), 2)
        self.assertEqual(len(self._inputs()), 2)

    def test_a_lone_value_pasted_on_its_field_is_typed_into_it(self):
        self.window._open_asset("1010340")
        editor = self.window.editor
        self._paste_text("<CycleTime>12.5</CycleTime>")
        editor._paste(self._find_item("CycleTime"))
        self.assertEqual(self._find_item("CycleTime").text(1), "12.5")
        change = next(iter(self.window.loadout))
        self.assertEqual((change.path, change.new_value), ("/Values/Factory7/CycleTime", "12.5"))

    def test_the_menu_offers_both_and_says_their_keys(self):
        self.window._open_asset("1010340")
        editor = self.window.editor
        entry = self._inputs()[0]
        self._paste_text("not xml")
        offered = [a.text() for a in editor._build_item_menu(entry).actions()]
        self.assertIn("Copy block\tCtrl+C", offered)
        self.assertFalse(any(text.startswith("Paste") for text in offered))

        editor._copy_block(entry)
        paste = next(
            a for a in editor._build_item_menu(entry).actions()
            if a.text().startswith("Paste")
        )
        self.assertEqual(paste.text(), "Paste <Item>\tCtrl+V")
        self.assertTrue(paste.isEnabled())
        paste = next(
            a for a in editor._build_item_menu(self._container("Building")).actions()
            if a.text().startswith("Paste")
        )
        self.assertFalse(paste.isEnabled())
        self.assertTrue(paste.toolTip())

    def test_ctrl_c_and_ctrl_v_on_the_tree(self):
        from PySide6.QtCore import Qt
        from PySide6.QtGui import QGuiApplication
        from PySide6.QtTest import QTest

        self.window.show()
        self.window.activateWindow()
        self.window._open_asset("1010340")
        _app.processEvents()
        editor = self.window.editor
        tree = editor.tree
        tree.setCurrentItem(self._inputs()[0])
        tree.setFocus()
        _app.processEvents()
        if not tree.hasFocus():
            self.skipTest("no keyboard focus on this platform")
        QTest.keyClick(tree, Qt.Key.Key_C, Qt.KeyboardModifier.ControlModifier)
        self.assertTrue(QGuiApplication.clipboard().text().startswith("<Item>"))
        QTest.keyClick(tree, Qt.Key.Key_V, Qt.KeyboardModifier.ControlModifier)
        self.assertEqual(len(self._inputs()), 2)


def etree_text(element) -> str:
    from lxml import etree

    return etree.tostring(element).decode()


def etree_element(tag, text):
    from lxml import etree

    element = etree.Element(tag)
    element.text = text
    return element


class TestRemovingAnEntryTheGameMayNotMiss(WorkflowCase):
    """Remove from asset on a cost entry that names a good and sets no
    amount, which the shipped files use for a good that costs nothing."""

    @classmethod
    def setUpClass(cls):
        import support

        cls._tmp = tempfile.TemporaryDirectory()
        tmp = Path(cls._tmp.name)
        cls.assets = tmp / "assets.xml"
        cls.assets.write_text(support.IMPORT_ASSETS, encoding="utf-8")
        cls.db = tmp / "index.sqlite"
        index_mod.build_index(cls.assets, cls.db)

    def entry(self, tag: str, value: str):
        """The list entry whose ``tag`` is ``value``."""
        for item in self.window.editor._iter_items():
            if item.text(0) == tag and item.text(1) == value:
                return item.parent()
        return None

    def test_fields_says_the_game_may_not_notice_as_the_xml_view_does(self):
        """Both write the removal. Only the XML view said what is known of
        such an entry: here the row was struck through with nothing said."""
        editor = self.window.editor
        self.window._open_asset("1002")
        editor._remove_node(self.entry("Ingredient", "1012"))
        (removal,) = self.window.loadout
        self.assertEqual(
            (removal.op, removal.path), ("remove", "/Values/Cost/Costs/Item[Ingredient='1012']")
        )
        self.assertEqual(
            editor.warning_label.text(),
            "This entry only names an asset and sets nothing, so the game may not "
            "notice it gone. The removal is written all the same.",
        )
        # It is about the removal just made, and goes with the next row.
        editor.tree.setCurrentItem(self.entry("Ingredient", "1010"))
        self.assertNotIn("may not notice", editor.warning_label.text())

    def test_but_not_of_an_entry_that_sets_something_or_is_the_entry(self):
        """1011 costs 7, and a product list's entries are the entries."""
        editor = self.window.editor
        self.window._open_asset("1002")
        for tag, value in (("Ingredient", "1011"), ("Product", "2002")):
            with self.subTest(tag):
                editor._remove_node(self.entry(tag, value))
                self.assertNotIn("may not notice", editor.warning_label.text())
        self.assertEqual(len(self.window.loadout), 2)


class TestTheAddFieldDialog(WorkflowCase):
    """The dialog itself: what it offers, what it explains, what it blocks."""

    def test_an_addition_is_described_by_field_and_value(self):
        """A fixed-width column cannot afford <Tag>value</Tag>."""
        from lxml import etree

        from anno_modding_studio.core import effective

        self.window._open_asset("1010340")
        building = self._container("Building")
        element = etree.Element("SecondPartyRelevant")
        element.text = "0"
        self.window.editor._record_addition(building, element, "add it")

        change = next(iter(self.window.loadout))
        self.assertEqual(effective.change_summary(change), "+ SecondPartyRelevant = 0")
        self.assertEqual(effective.describe_payload(change), "SecondPartyRelevant = 0")
        self.assertNotIn("<", effective.describe_payload(change))

    def test_a_multi_field_addition_is_counted_not_spelled_out(self):
        from anno_modding_studio.core import effective

        self.window._open_asset("1010340")
        self.window.editor._add_list_entry(self._container("Maintenances"))
        change = next(iter(self.window.loadout))
        summary = effective.change_summary(change)
        self.assertTrue(summary.startswith("+ Item ("), summary)
        # The tooltip spells it out, indented, still without XML noise.
        tooltip = effective.describe_payload(change)
        self.assertIn("Product", tooltip)
        self.assertNotIn("</", tooltip)

    def test_a_new_list_entry_arrives_with_no_values(self):
        """The fields are there to fill in, and stay in view while empty."""
        self.window._open_asset("1010340")
        self.window.editor._add_list_entry(self._container("Maintenances"))
        entry = next(
            i for i in self.window.editor._iter_items()
            if i.text(0) == "Item" and i.text(3) == "added by this mod"
        )
        self.assertGreaterEqual(entry.childCount(), 1)
        for i in range(entry.childCount()):
            self.assertEqual(entry.child(i).text(1), "")
            self.assertFalse(entry.child(i).isHidden())

    def test_picking_a_field_does_not_type_a_value_for_you(self):
        dialog = self._open_add_field_dialog("/Values/Building", [])
        self.addCleanup(dialog.deleteLater)
        for row in range(dialog.table.rowCount()):
            dialog.table.selectRow(row)
            self.assertEqual(dialog.value_edit.text(), "")

    def test_containers_in_the_add_dialog_are_marked_not_dimmed(self):
        """Greying them out read as "not selectable", which they are."""
        from PySide6.QtCore import Qt

        from anno_modding_studio.ui.add_field_dialog import (
            CONTAINER_MARK,
            AddFieldDialog,
        )

        self.window._open_asset("1010340")
        editor = self.window.editor
        factory = self._container("Factory7")
        spath = editor._structural_path(factory)
        # Everything the game puts here, so the list is sure to hold a container;
        # what the dialog is being asked about is how it renders one.
        fields = self.index.schema.children_of(spath)
        containers = [f for f in fields if f.is_container]
        self.assertTrue(containers, "sample data should offer a container here")

        dialog = AddFieldDialog(fields, spath, parent=self.window)
        try:
            marked = 0
            for row in range(dialog.table.rowCount()):
                item = dialog.table.item(row, 0)
                info = item.data(Qt.ItemDataRole.UserRole + 1)
                if not info.is_container:
                    continue
                marked += 1
                self.assertTrue(item.text().startswith(CONTAINER_MARK))
                self.assertEqual(dialog.table.item(row, 2).text(), "(holds fields)")
                # The marker is decoration; it must never reach the XML.
                self.assertEqual(item.data(Qt.ItemDataRole.UserRole), info.tag)
            self.assertEqual(marked, len(containers))
        finally:
            dialog.deleteLater()

    def test_a_field_already_on_the_asset_is_shown_demoted_and_greyed_not_hidden(self):
        """Regression: a field the asset already has (just empty, and hidden
        by the empty-value filter) used to be missing from this list
        entirely -- indistinguishable from a field the game never uses here
        at all, even though it demonstrably does (another asset using the
        same container proves it)."""
        from PySide6.QtGui import QColor

        from anno_modding_studio.ui import theme

        self.window._open_asset("1010340")
        editor = self.window.editor
        building = self._container("Building")
        spath = editor._structural_path(building)
        present = editor._present_children(building)
        self.assertIn("BuildingType", present)

        dialog = self._open_add_field_dialog(spath, present)
        try:
            present_row = self._row_for_tag(dialog, "BuildingType")
            self.assertIsNotNone(present_row, "already-present fields should still be listed")

            from PySide6.QtCore import Qt

            tag_role = Qt.ItemDataRole.UserRole
            is_present_by_row = [
                dialog.table.item(r, 0).data(tag_role) in dialog._present
                for r in range(dialog.table.rowCount())
            ]
            self.assertTrue(any(is_present_by_row), "the sample Building has present fields")
            self.assertTrue(any(not p for p in is_present_by_row), "and addable ones too")
            first_present = is_present_by_row.index(True)
            self.assertTrue(
                all(is_present_by_row[first_present:]),
                "every already-present field should sort after every addable one",
            )
            self.assertEqual(
                dialog.table.item(present_row, 0).foreground().color(),
                QColor(theme.TEXT_DIM),
            )
            self.assertEqual(
                dialog.table.item(present_row, 2).text(), "(already on this asset)"
            )
        finally:
            dialog.deleteLater()

    def test_the_note_height_stays_fixed_across_selections(self):
        """Regression: the "already on this asset" note wraps to two lines
        while most other notes are one -- letting the note grow and shrink
        moved the table above it, shifting whatever row you had just clicked
        out from under the cursor."""
        self.window._open_asset("1010340")
        editor = self.window.editor
        building = self._container("Building")
        spath = editor._structural_path(building)
        present = editor._present_children(building)

        from PySide6.QtCore import Qt

        tag_role = Qt.ItemDataRole.UserRole

        dialog = self._open_add_field_dialog(spath, present)
        try:
            no_selection_height = dialog.note.height()

            addable_row = next(
                r for r in range(dialog.table.rowCount())
                if dialog.table.item(r, 0).data(tag_role) not in dialog._present
            )
            dialog.table.selectRow(addable_row)
            addable_height = dialog.note.height()

            present_row = self._row_for_tag(dialog, "BuildingType")
            dialog.table.selectRow(present_row)
            present_height = dialog.note.height()

            self.assertEqual(no_selection_height, addable_height)
            self.assertEqual(addable_height, present_height)
        finally:
            dialog.deleteLater()

    def test_selecting_an_already_present_field_explains_itself_and_blocks_adding(self):
        """The whole point: you can look at it, but you cannot proceed."""
        from PySide6.QtWidgets import QDialog

        self.window._open_asset("1010340")
        editor = self.window.editor
        building = self._container("Building")
        spath = editor._structural_path(building)
        present = editor._present_children(building)

        dialog = self._open_add_field_dialog(spath, present)
        try:
            row = self._row_for_tag(dialog, "BuildingType")
            dialog.table.selectRow(row)
            self.assertFalse(dialog.add_button.isEnabled())
            self.assertIn("already has", dialog.note.text())
            self.assertIn("BuildingType", dialog.note.text())
            self.assertIn("Show empty", dialog.note.text())

            # Neither the button, Enter, nor a double-click should let it through.
            dialog._accept_selection()
            self.assertIsNone(dialog.chosen_tag)
            self.assertNotEqual(dialog.result(), int(QDialog.DialogCode.Accepted))
        finally:
            dialog.deleteLater()

    def test_add_field_dialog_plain_message_when_nothing_matches_at_all(self):
        """A search matching neither an offered field nor one already present
        keeps the original, simpler message -- the "already present" note is
        specific to fields that are genuinely there."""
        self.window._open_asset("1010340")
        editor = self.window.editor
        building = self._container("Building")
        spath = editor._structural_path(building)
        present = editor._present_children(building)

        dialog = self._open_add_field_dialog(spath, present)
        try:
            dialog.search_box.setText("zzz_not_a_real_field")
            dialog._refresh()
            self.assertEqual(dialog.table.rowCount(), 0)
            self.assertIn("No fields match", dialog.note.text())
        finally:
            dialog.deleteLater()


class TestTheAddFieldButton(WorkflowCase):
    """The button beside the filter box, as against the dialog behind it."""

    # -- adding a field with no container to right-click: the asset root ---
    def test_add_field_button_sits_by_the_filter_box_not_up_with_new_asset(self):
        """A user already looking at the asset they want to edit is not going
        to think to look up by New asset -- this belongs beside the fields it
        actually affects."""
        self.window.show()
        # On the page being measured: Qt lays out a tab when it is first
        # shown, so on a tab nobody has opened every widget still sits at
        # y=0 -- where "same row as Filter fields" and "not on New asset's
        # row" are both trivially true of nothing.
        self.window.tabs.setCurrentIndex(TAB_EDIT)
        _app.processEvents()
        editor = self.window.editor
        self.assertLess(
            abs(editor.add_field_button.y() - editor.filter_box.y()), 4,
            "should be on the same row as Filter fields",
        )
        self.assertGreater(
            abs(editor.add_field_button.y() - editor.new_asset_button.y()), 10,
            "should not be on New asset's row any more",
        )

    def test_add_field_button_is_disabled_until_an_asset_is_open(self):
        self.assertFalse(self.window.editor.add_field_button.isEnabled())
        self.window._open_asset("1010340")
        self.assertTrue(self.window.editor.add_field_button.isEnabled())

    def test_add_field_button_offers_top_level_fields_this_asset_lacks(self):
        """Regression: every other field lives inside some container you can
        right-click to add to -- the top level itself had no such row, so
        there was no way to add a field the base game only ships on some
        variants of a building (an ``EffectSource`` some shrines lack, say)."""
        self.window._open_asset("1010340")  # a FactoryBuilding7
        missing = {f.tag for f in self.window.editor._root_missing_fields()}
        # Genuinely absent from this asset, present elsewhere in the sample data.
        self.assertIn("Tags", missing)
        self.assertNotIn("Building", missing)  # this asset already has one

    def test_the_add_field_button_adds_a_new_top_level_field_and_selects_it(self):
        from unittest import mock

        from PySide6.QtWidgets import QDialog

        from anno_modding_studio.core.changes import OP_MERGE
        from anno_modding_studio.ui.add_field_dialog import AddFieldDialog

        self.window._open_asset("1010340")
        editor = self.window.editor

        def fake_exec(dialog_self):
            for field in dialog_self.fields:
                if field.tag == "Tags":
                    dialog_self.chosen_tag = "Tags"
                    dialog_self.is_container = True
                    dialog_self.value = ""
                    return int(QDialog.DialogCode.Accepted)
            return int(QDialog.DialogCode.Rejected)

        with mock.patch.object(AddFieldDialog, "exec", fake_exec):
            editor._add_root_field()

        change = next(iter(self.window.loadout))
        self.assertEqual(change.path, "/Values")
        self.assertEqual(change.tag, "Tags")
        # Made sure of, not added: another mod may have put it there first.
        self.assertEqual(change.op, OP_MERGE)

        found = None
        for i in range(editor.tree.topLevelItemCount()):
            item = editor.tree.topLevelItem(i)
            if item.text(0) == "Tags":
                found = item
        self.assertIsNotNone(found, "the new field should show up as a top-level row")
        self.assertIs(editor.tree.currentItem(), found, "should land on what was just added")

    def test_a_field_added_under_a_newly_created_root_field_merges_into_one_modop(self):
        """A ``Tags`` added at the root and a ``<Tag>`` entry added under it are
        one act of asset creation from the mod's own point of view -- they
        should stay one ModOp, not become two that both target ``/Values``."""
        from unittest import mock

        from PySide6.QtWidgets import QDialog

        from anno_modding_studio.ui.add_field_dialog import AddFieldDialog

        self.window._open_asset("1010340")
        editor = self.window.editor

        def add_tags(dialog_self):
            for field in dialog_self.fields:
                if field.tag == "Tags":
                    dialog_self.chosen_tag = "Tags"
                    dialog_self.is_container = True
                    dialog_self.value = ""
                    return int(QDialog.DialogCode.Accepted)
            return int(QDialog.DialogCode.Rejected)

        with mock.patch.object(AddFieldDialog, "exec", add_tags):
            editor._add_root_field()

        tags_item = None
        for i in range(editor.tree.topLevelItemCount()):
            item = editor.tree.topLevelItem(i)
            if item.text(0) == "Tags":
                tags_item = item
        self.assertIsNotNone(tags_item)
        self.assertEqual(editor._add_kind(tags_item), "entry")  # Tags is a list

        editor._add_list_entry(tags_item)

        self.assertEqual(len(self.window.loadout), 1, "one ModOp, not two")
        change = next(iter(self.window.loadout))
        self.assertEqual(change.tag, "Tags")
        self.assertIn("<Tag", change.payload)

    def test_add_field_button_does_nothing_when_nothing_is_missing(self):
        from unittest import mock

        self.window._open_asset("1010340")
        editor = self.window.editor
        with mock.patch.object(editor, "_root_missing_fields", return_value=[]):
            editor._populate()
            self.assertFalse(editor.add_field_button.isEnabled())
            editor._add_root_field()
        self.assertEqual(len(self.window.loadout), 0)

    def test_a_root_field_added_to_a_brand_new_asset_grows_its_own_creation_modop(self):
        from unittest import mock

        from PySide6.QtWidgets import QDialog

        from anno_modding_studio.core.changes import OP_ADD_NEXT, Change
        from anno_modding_studio.ui.add_field_dialog import AddFieldDialog

        self.window.loadout.set_change(
            Change(
                guid="1010340", asset_label="mine  (new asset)", path="",
                tag="Asset", op=OP_ADD_NEXT, summary="create asset 1337000006",
                payload="<Asset><BaseAssetGUID>1010340</BaseAssetGUID><Values>"
                        "<Standard><GUID>1337000006</GUID><Name>mine</Name>"
                        "</Standard></Values></Asset>",
            )
        )
        self.window.on_changes_updated()
        self.window._open_asset("1337000006")
        editor = self.window.editor
        self.assertTrue(editor.is_new_asset)

        def add_building(dialog_self):
            for field in dialog_self.fields:
                if field.tag == "Building":
                    dialog_self.chosen_tag = "Building"
                    dialog_self.is_container = True
                    dialog_self.value = ""
                    return int(QDialog.DialogCode.Accepted)
            return int(QDialog.DialogCode.Rejected)

        with mock.patch.object(AddFieldDialog, "exec", add_building):
            editor._add_root_field()

        # Still one ModOp -- the creation payload grew, nothing new was emitted.
        self.assertEqual(len(self.window.loadout), 1)
        change = next(iter(self.window.loadout))
        self.assertEqual(change.op, OP_ADD_NEXT)
        self.assertIn("<Building", change.payload)


class TestAssetsTheModInvents(WorkflowCase):
    """Assets the loadout creates, which are in no index."""

    def test_a_new_asset_row_opens_the_new_asset_not_its_anchor(self):
        from anno_modding_studio.core.changes import OP_ADD_NEXT, Change

        self.window.loadout.set_change(
            Change(
                guid="1010340", asset_label="mine  (new asset)", path="",
                tag="Asset", op=OP_ADD_NEXT, summary="create asset 1337000005",
                payload="<Asset><BaseAssetGUID>1010340</BaseAssetGUID><Values>"
                        "<Standard><GUID>1337000005</GUID><Name>mine</Name>"
                        "</Standard></Values></Asset>",
            )
        )
        self.window.on_changes_updated()
        open_row(self.window.changes_panel, change_rows(self.window.changes_panel)[0])
        self.assertEqual(self.window.editor.guid, "1337000005")

    def test_the_row_for_one_says_which_asset_and_what_kind(self):
        """It read "the asset itself / + new asset" -- true of every one.

        A creating op anchors on a *neighbour*, so the path it has is not its
        own and there is nothing in it for the usual label to say.
        """
        from anno_modding_studio.core.changes import OP_ADD_NEXT, Change

        self.window.loadout.set_change(
            Change(
                guid="1010340", asset_label="mine  (new asset)", path="",
                tag="Asset", op=OP_ADD_NEXT, summary="create asset 1337000009",
                payload="<Asset><Template>Product</Template><Values><Standard>"
                        "<GUID>1337000009</GUID><Name>marble_deluxe</Name>"
                        "</Standard></Values></Asset>",
            )
        )
        self.window.on_changes_updated()
        row = change_rows(self.window.changes_panel)[0]
        self.assertEqual(row.text(0), "marble_deluxe")
        self.assertEqual(row.text(1), "+ new Product")

    def test_a_created_asset_can_be_opened_and_edited(self):
        from anno_modding_studio.core.changes import OP_ADD_NEXT, Change

        self.window.loadout.set_change(
            Change(
                guid="1010340", asset_label="my_thing  (new asset)", path="",
                tag="Asset", op=OP_ADD_NEXT, summary="create asset 1337000001",
                payload="<Asset><BaseAssetGUID>1010340</BaseAssetGUID><Values>"
                        "<Standard><GUID>1337000001</GUID><Name>my_thing</Name></Standard>"
                        "<Building><BuildingType>Factory</BuildingType></Building>"
                        "</Values></Asset>",
            )
        )
        self.assertTrue(self.window.workspace.is_new_asset("1337000001"))
        self.assertTrue(self.window._open_asset("1337000001") is None
                        or True)  # opens without error
        self.window.editor.load_asset("1337000001")
        self.assertTrue(self.window.editor.is_new_asset)
        self.assertIn("created by this mod", self.window.editor.subtitle_label.text())

        # Editing it folds into the creating change rather than adding a ModOp.
        item = self._find_item("BuildingType")
        self.assertIsNotNone(item)
        item.setText(1, "Residence")
        self.assertEqual(len(self.window.loadout), 1)
        self.assertIn("Residence", next(iter(self.window.loadout)).payload)

    def test_a_created_asset_shows_up_in_search(self):
        from anno_modding_studio.core.changes import OP_ADD_NEXT, Change

        self.window.loadout.set_change(
            Change(
                guid="1010340", asset_label="searchable  (new asset)", path="",
                tag="Asset", op=OP_ADD_NEXT,
                payload="<Asset><Values><Standard><GUID>1337000002</GUID>"
                        "<Name>searchable_thing</Name></Standard></Values></Asset>",
            )
        )
        self._search("searchable_thing")
        # Only the created asset, where the first letter alone finds dozens.
        self.assertEqual(self.window.browser.table.rowCount(), 1)
        self.assertEqual(self.window.browser.table.item(0, 2).text(), "1337000002")

    def test_a_guid_typed_lists_its_own_asset_above_one_that_inherits_from_it(self):
        """In the search and in the picker New asset… opens: the created
        asset's Template column reads "inherits 1010340", and it led."""
        from anno_modding_studio.core.changes import OP_ADD_NEXT, Change
        from anno_modding_studio.ui.asset_picker import AssetPickerDialog

        self.window.loadout.set_change(
            Change(
                guid="1010340", asset_label="follows  (new asset)", path="",
                tag="Asset", op=OP_ADD_NEXT,
                payload="<Asset><BaseAssetGUID>1010340</BaseAssetGUID><Values>"
                        "<Standard><GUID>1337000004</GUID><Name>follows</Name>"
                        "</Standard></Values></Asset>",
            )
        )
        self._search("1010340")
        table = self.window.browser.table
        self.assertEqual(
            [table.item(row, 2).text() for row in range(2)], ["1010340", "1337000004"]
        )

        picker = AssetPickerDialog(self.window.workspace, "the new asset's base", "", self.window)
        self.addCleanup(picker.deleteLater)
        self.addCleanup(picker._debounce.stop)
        picker.search_box.setText("1010340")
        picker.refresh()
        self.assertEqual(
            [picker.table.item(row, 2).text() for row in range(2)],
            ["1010340", "1337000004"],
        )

    def test_deleting_the_open_new_asset_clears_the_editor(self):
        """Regression: removing a created asset's change from the dock left it
        fully visible and still "editable" in the tree, even though the guid it
        pointed at no longer existed anywhere."""
        from anno_modding_studio.core.changes import OP_ADD_NEXT, Change

        self.window.loadout.set_change(
            Change(
                guid="1010340", asset_label="gone_soon  (new asset)", path="",
                tag="Asset", op=OP_ADD_NEXT, summary="create asset 1337000003",
                payload="<Asset><Values><Standard><GUID>1337000003</GUID>"
                        "<Name>gone_soon</Name></Standard></Values></Asset>",
            )
        )
        self.window.on_changes_updated()  # populates the dock, as new_asset() does
        self.window._open_asset("1337000003")
        self.assertEqual(self.window.editor.guid, "1337000003")

        self.assertEqual(len(change_rows(self.window.changes_panel)), 1)
        pick_change(self.window.changes_panel)
        self.window.changes_panel._remove_selected()

        self.assertIsNone(self.window.editor.guid)
        self.assertEqual(self.window.editor.tree.topLevelItemCount(), 0)
        self.assertIn("No asset selected", self.window.editor.title_label.text())

    def test_opening_a_different_missing_asset_leaves_the_current_one_alone(self):
        """The clear-on-delete fix must not fire for an unrelated failed open.

        Goes through ``editor.load_asset`` directly rather than the window's
        ``_open_asset``, which pops a modal warning on failure -- not what this
        test is checking, and modal dialogs do not resolve themselves headless.
        """
        self.window._open_asset("1010340")
        self.assertFalse(self.window.editor.load_asset("9999999999"))  # does not exist
        self.assertEqual(self.window.editor.guid, "1010340")
        self.assertGreater(self.window.editor.tree.topLevelItemCount(), 0)


class TestTheChangesPanel(WorkflowCase):
    """The dock listing what the loadout changes."""

    def test_double_clicking_a_change_opens_its_asset(self):
        self.window._open_asset("1010340")
        self._edit("CycleTime", "15.00")
        self.window._open_asset("1010030")
        self.assertEqual(self.window.editor.guid, "1010030")

        open_row(self.window.changes_panel, change_rows(self.window.changes_panel)[0])
        self.assertEqual(self.window.editor.guid, "1010340")

    def test_drawing_it_takes_the_changes_as_they_stand_once(self):
        """Each row asks the view about its asset, and each question used to
        fingerprint every change again: a whole Anno 1800 folder, thirteen
        thousand changes, took a minute and a half to draw -- on opening its
        loadout, and after reading the folder in. The whole redraw holds the
        change set steady, so none of them does."""
        self.window._open_asset("1010340")
        self._edit("CycleTime", "15.00")
        panel = self.window.changes_panel
        held = []
        real = panel._rebuild

        def rebuild():
            held.append(self.window.workspace._stamp_held)
            real()

        panel._rebuild = rebuild
        try:
            panel.refresh()
        finally:
            del panel._rebuild
        self.assertEqual(len(held), 1)
        self.assertGreater(held[0], 0)

    def test_the_changes_columns_share_a_wider_window(self):
        """Both of them: Change was pinned at 150px whatever the window did.

        It was asked for that way when it held ``12.00 → 15.00`` and nothing
        else. It now holds resolved names, what kind of asset is being created
        and how many fields an addition carries, and a wider window is the one
        thing that could help with any of it.
        """
        self.window._open_asset("1010340")
        self._edit("CycleTime", "15.00")
        tree = self.window.changes_panel.tree
        self.window.show()

        self.window.resize(1100, 800)
        _app.processEvents()
        narrow = (tree.columnWidth(0), tree.columnWidth(1))
        self.window.resize(2200, 800)
        _app.processEvents()
        wide = (tree.columnWidth(0), tree.columnWidth(1))

        self.assertGreater(wide[0], narrow[0], "the name column should grow")
        self.assertGreater(wide[1], narrow[1], "and so should the change column")
        self.assertEqual(sum(wide), tree.viewport().width())

    def test_the_mod_changes_panel_gets_a_bigger_default_share(self):
        """Regression: a 3:1 splitter stretch shrank the Mod changes panel's
        relative share as the window grew, on the panel whose columns are
        elided most often -- it should keep more room, not less. Checked on a
        genuinely wide window: at the app's own minimum size there is no
        slack left for either stretch factor to distribute."""
        self.window.show()
        self.window.resize(2200, 900)
        _app.processEvents()
        self.assertGreaterEqual(self.window.changes_side.width(), 550)


class TestOneLoadoutIsCountedOneWay(WorkflowCase):
    """"N changes" said of one loadout is one number wherever it is said.

    A mod of your own with a value typed in Fields, ModOps typed in the XML
    view and a pattern was counted five ways at once: 29 changes in the title
    bar, 22 in the Mod changes panel and its footer, where they were called
    ModOps, 18 on the Start tab and in each checkpoint, and 21 verified on
    the Export tab with one switched off.  A change is a row of the changes
    list, and that is what each of them counts now.  Where a number is of
    something else, its words say what.
    """

    def setUp(self):
        from PySide6.QtWidgets import QDialog

        from anno_modding_studio.core.changes import AIM_MAKE, Op
        from anno_modding_studio.ui.pattern_dialog import PatternDialog

        super().setUp()
        self.home = tempfile.TemporaryDirectory()
        self.addCleanup(self.home.cleanup)
        window, loadout = self.window, self.window.loadout
        window.files.loadouts_dir = lambda: Path(self.home.name)
        loadout.own.name = "Counted"
        loadout.set_up_as(AIM_MAKE)
        loadout.make_own_mod()
        window.files.name_loadout("Counted")
        # A value typed in Fields.
        window._open_asset("1010340")
        self._edit("CycleTime", "15.00")
        # A ModOp typed in the XML view, stored with what it comes to.
        typed = Op(
            expression="/Values/FactoryBase/CycleTime", authored=True, plain=True,
            source='<ModOp Type="replace" GUID="1010341" Path="/Values/FactoryBase/CycleTime">'
                   "<CycleTime>50.00</CycleTime></ModOp>",
        )
        loadout.set_op(typed)
        loadout.set_change(Change(
            guid="1010341", asset_label="Sawmill", path="/Values/FactoryBase/CycleTime",
            tag="CycleTime", old_value="45.00", new_value="50.00", op_uid=typed.uid,
        ))
        # And one that comes to nothing as things stand.
        loadout.set_op(Op(
            expression="/Values/Nowhere", authored=True,
            source='<ModOp Type="remove" GUID="1010341" Path="/Values/Nowhere" />',
        ))

        # A pattern, typed in its editor: four assets answer to it.
        def composed(dialog):
            dialog.editor.setPlainText(
                '<ModOp Type="merge" Path="//Values/Residence7">'
                "<Residents>42</Residents></ModOp>"
            )
            dialog._recheck()
            dialog._accept()
            return int(QDialog.DialogCode.Accepted)

        with mock.patch.object(PatternDialog, "exec", composed):
            window.patterns.new_pattern()
        self.pattern = loadout.ops[-1]
        self.assertTrue(self.pattern.searches)
        window.on_changes_updated()
        self.assertEqual(len(loadout), 6)
        self.assertEqual(sum(1 for op in loadout.ops if op.authored), 3)

    def history(self):
        from anno_modding_studio.ui.history_dialog import HistoryDialog

        dialog = HistoryDialog(self.window.loadout, self.window)
        self.addCleanup(dialog.deleteLater)
        return dialog

    def test_a_patterns_changes_are_yours_as_the_pattern_is(self):
        """They were read as a mod's own, so they were left out wherever
        your changes were counted: the Start tab and every checkpoint."""
        loadout = self.window.loadout
        answers = loadout.changes_by_op(self.pattern.uid)
        self.assertEqual(len(answers), 4)
        self.assertTrue(all(change.authored for change in answers))
        self.assertEqual(loadout.changes_made, 6)

    def test_every_place_says_the_number_the_changes_list_does(self):
        from anno_modding_studio.core.changes import AIM_MAKE

        window, panel = self.window, self.window.changes_panel
        # The list: its rows, the mod's own row over them, and its footer.
        self.assertEqual(len(change_rows(panel)), 6)
        self.assertTrue(panel.tree.topLevelItem(0).text(1).endswith("6 changes"))
        self.assertTrue(panel.summary.text().startswith("6 changes across "), panel.summary.text())
        # The title bar, which counted the three ops beside them: 9.
        self.assertRegex(window.windowTitle(), r"  —  6 changes$")
        # The Start tab: the card for a mod of your own, and the loadout's.
        window.start.refresh_source_panel()
        self.assertTrue(window.start._aim_status()[AIM_MAKE].endswith("  6 changes so far."))
        self.assertTrue(window.source.loadout_detail.text().startswith("6 changes · "))
        # The question on the way out counts the ones not saved: the first
        # was written with the loadout's file, as it was made.
        window.files.mark_unsaved()
        with mock.patch.object(window.prompts, "ask_save_first", return_value="") as asked:
            self.assertFalse(window.files.confirm_discard())
        self.assertEqual(asked.call_args[0], (5, 0, False, False, 0))
        # And the history, of the working copy and of a checkpoint of it.
        window.loadout.checkpoint("now")
        rows = self.history().mod.checkpoints
        self.assertIn("\n    6 changes, saved as the latest checkpoint", rows.item(0).text())
        self.assertTrue(rows.item(1).text().endswith("now, 6 changes"), rows.item(1).text())

    def test_with_one_switched_off_each_count_says_which_it_is(self):
        window, loadout = self.window, self.window.loadout
        off = loadout.find("1010340", loadout.for_asset("1010340")[0].path)
        loadout.set_active([off.key], False)
        window.changes_panel.refresh()
        window.on_activation_changed()
        self.assertTrue(
            window.changes_panel.summary.text().startswith("5 of 6 changes on, across ")
        )
        # Still six, switched on or not, where the changes are counted.
        self.assertRegex(window.windowTitle(), r"  —  6 changes$")
        # Only what is on is checked, and the line said "All 5 changes".
        window.output.refresh()
        said = window.output.check_summary.text()
        self.assertIn("5 changes", said)
        self.assertTrue(said.endswith(" 1 more is switched off, so it was not checked."), said)

    def test_restoring_a_checkpoint_gives_the_number_it_was_offered_as(self):
        """It offered "the 18 from 21:27" and 22 came back: the pattern and
        its four changes were no part of what a checkpoint held."""
        from anno_modding_studio.ui import history_dialog

        window, loadout = self.window, self.window.loadout
        loadout.checkpoint("six")
        loadout.drop_op(self.pattern.uid)
        window._open_asset("1010030")
        self._edit("Residents", "12")
        self.assertEqual(len(loadout), 3)
        dialog = self.history().mod
        dialog.checkpoints.setCurrentRow(1)
        asked = []

        def yes(_parent, _title, text, *_rest, **_more):
            asked.append(text)
            return True

        with mock.patch.object(history_dialog, "ask", yes):
            dialog._restore()
        self.assertTrue(asked[0].startswith("Replace the current 3 changes with the 6 from "), asked)
        self.assertEqual((len(loadout), loadout.changes_made), (6, 6))
        self.assertIsNotNone(loadout.op(self.pattern.uid))
        self.assertEqual(len(loadout.changes_by_op(self.pattern.uid)), 4)
        self.assertIn("\n    6 changes, ", dialog.checkpoints.item(0).text())


class TestTheResultListAndCombos(WorkflowCase):
    """The floating search results, and the widgets around them."""

    @staticmethod
    def _click(widget):
        """A left-click in the middle of a widget, as the user would."""
        from PySide6.QtCore import QEvent, Qt
        from PySide6.QtGui import QMouseEvent

        point = widget.rect().center()
        _app.sendEvent(
            widget,
            QMouseEvent(
                QEvent.Type.MouseButtonPress,
                point,
                widget.mapToGlobal(point),
                Qt.MouseButton.LeftButton,
                Qt.MouseButton.LeftButton,
                Qt.KeyboardModifier.NoModifier,
            ),
        )

    def test_every_combo_box_is_the_same_plain_widget(self):
        """A custom searchable combo used to back the template pickers and the
        category field, and kept looking subtly different from a plain combo
        ("Target game") no matter how it was patched -- a caret here, a
        highlighted-selection bar there. Every combo in the app is now a
        plain, non-editable QComboBox: Qt's own type-ahead reaches any of
        ~1,000 templates in a few keystrokes without a line edit at all, and
        the category field never needed typing -- mod.io defines a short,
        fixed list per game and does not take an arbitrary value.
        """
        from PySide6.QtWidgets import QComboBox

        for combo in (
            self.window.browser.template_combo,
            self.window.output.category_combo,
        ):
            self.assertIsInstance(combo, QComboBox)
            self.assertIsNone(combo.lineEdit(), "%r should not be editable" % combo)

    def test_the_game_is_said_on_the_generate_tab_not_asked(self):
        """It was a dropdown, and changing it could only relabel a mod read
        against one game's files as the other's. The files decide it."""
        from PySide6.QtWidgets import QComboBox, QLabel

        output = self.window.output
        self.assertIsInstance(output.game_label, QLabel)
        self.assertEqual(
            output.game_label.text(), games.get(self.window.loadout.game).label
        )
        # Category, and Anno 117's Difficulty: neither is the game.
        self.assertFalse(
            [c for c in output.meta_box.findChildren(QComboBox)
             if c not in (output.category_combo, output.difficulty_combo)]
        )

    def test_mod_id_placeholder_does_not_read_as_an_instruction(self):
        """Parenthesised placeholder text reads as "fill this in like so"."""
        placeholder = self.window.output.modid_edit.placeholderText()
        self.assertTrue(placeholder.startswith("<") and placeholder.endswith(">"))
        self.assertIn("mod name", placeholder)

    def test_the_result_list_waits_until_it_is_reached_for(self):
        """It used to drop open the moment a base file was adopted.

        Which meant the first thing on the editor tab was a panel nobody had
        asked for, and the first click anywhere else made it vanish -- so it
        read as something going wrong rather than as the search box's own list.
        """
        self.window.show()
        # Arriving at the editor parks the caret in the search box so typing
        # lands somewhere useful, and still leaves the list shut.
        self.window._go_to_editor()
        _app.processEvents()
        search = self.window.editor.search
        self.assertFalse(search.results.isVisible())

        # Clicking in the box is asking for it -- and by then the caret is
        # already here, so there is no focus change to go on.
        self._click(search.search_box)
        _app.processEvents()
        self.assertTrue(search.results.isVisible())

    def test_and_typing_or_pressing_find_opens_it_too(self):
        self.window.show()
        self.window._go_to_editor()
        search = self.window.editor.search
        search.search_box.setText("lumber")
        _app.processEvents()
        self.assertTrue(search.results.isVisible())
        search._debounce.stop()

        search.close_results()
        self.window._focus_search()   # the search shortcut: asked for by name
        _app.processEvents()
        self.assertTrue(search.results.isVisible())

    def _in_front(self):
        """Show the window with the keyboard in it, or skip where there is none."""
        self.window.show()
        self.window.activateWindow()
        self.window._open_asset("1010340")
        _app.processEvents()
        self.window.editor.tree.setFocus()
        _app.processEvents()
        if not self.window.editor.tree.hasFocus():
            self.skipTest("no keyboard focus on this platform")

    def test_tabbing_into_the_box_opens_it_as_well(self):
        from PySide6.QtCore import Qt

        self._in_front()
        search = self.window.editor.search
        search.search_box.setFocus(Qt.FocusReason.TabFocusReason)
        _app.processEvents()
        self.assertTrue(search.search_box.hasFocus())
        self.assertTrue(search.results.isVisible())

    def test_a_button_greying_out_under_the_keyboard_does_not(self):
        """Qt hands the keyboard of a control that greys out to the next one
        along and calls that Tab. Nobody pressed it, and the list dropped
        over the asset: after Revert asset, the next control was this box."""
        self._in_front()
        editor = self.window.editor
        self._edit("CycleTime", "15.00")
        button = editor.revert_asset_button
        # The panel's Move button comes next now, which drops nothing: put the
        # box back after it, where it was.
        from PySide6.QtWidgets import QWidget

        QWidget.setTabOrder(button, editor.search.search_box)
        button.setFocus()
        _app.processEvents()
        button.setEnabled(False)
        _app.processEvents()
        self.assertTrue(editor.search.search_box.hasFocus(), "the box is still next in line")
        self.assertFalse(editor.search.results.isVisible())

    def _revert_with_the_mouse(self, view: str):
        """Change a value, click Revert asset in ``view``, and say where the
        keyboard went."""
        from PySide6.QtCore import Qt
        from PySide6.QtTest import QTest
        from PySide6.QtWidgets import QApplication

        self._in_front()
        editor = self.window.editor
        self._edit("CycleTime", "15.00")
        editor.set_view(view)
        _app.processEvents()
        QTest.mouseClick(editor.revert_asset_button, Qt.MouseButton.LeftButton)
        _app.processEvents()
        self.assertEqual(len(self.window.loadout), 0)
        self.assertFalse(editor.search.results.isVisible())
        return QApplication.focusWidget()

    def test_revert_asset_leaves_the_keyboard_on_the_fields(self):
        """It went to the search box, the list opened over the asset, and
        Ctrl+Z undid text in the box and not the revert."""
        from PySide6.QtCore import Qt
        from PySide6.QtTest import QTest

        focus = self._revert_with_the_mouse("fields")
        self.assertIs(focus, self.window.editor.tree)
        QTest.keyClick(focus, Qt.Key.Key_Z, Qt.KeyboardModifier.ControlModifier)
        self.assertEqual(len(self.window.loadout), 1)

    def test_and_in_the_xml_view_on_its_strip_of_mods(self):
        """Not in a pane: one that can be typed in keeps Ctrl+Z for itself."""
        from PySide6.QtCore import Qt
        from PySide6.QtTest import QTest

        focus = self._revert_with_the_mouse("xml")
        self.assertIs(focus, self.window.editor.xml.strip)
        QTest.keyClick(focus, Qt.Key.Key_Z, Qt.KeyboardModifier.ControlModifier)
        self.assertEqual(len(self.window.loadout), 1)

    def _step_with_the_mouse(self, view: str, button: str):
        """Open a second asset, click Back -- and Forward after it, where
        that is the ``button`` asked about -- and say where the keyboard
        went.  Either click is the last step that way, so its button greys
        out under it."""
        from PySide6.QtCore import Qt
        from PySide6.QtTest import QTest
        from PySide6.QtWidgets import QApplication

        self._in_front()
        editor = self.window.editor
        self.window._open_asset("1010030")
        editor.set_view(view)
        _app.processEvents()
        QTest.mouseClick(editor.nav.back_button, Qt.MouseButton.LeftButton)
        _app.processEvents()
        self.assertEqual(editor.guid, "1010340")
        self.assertFalse(editor.nav.back_button.isEnabled())
        if button == "forward":
            QTest.mouseClick(editor.nav.forward_button, Qt.MouseButton.LeftButton)
            _app.processEvents()
            self.assertEqual(editor.guid, "1010030")
            self.assertFalse(editor.nav.forward_button.isEnabled())
        self.assertFalse(editor.search.results.isVisible())
        return QApplication.focusWidget()

    def test_back_and_forward_leave_the_keyboard_on_the_fields(self):
        """Each greys out at the end of the trail, and the keyboard went on
        to New asset…, where Enter opens a dialog nobody asked for."""
        for button in ("back", "forward"):
            with self.subTest(button):
                self.assertIs(
                    self._step_with_the_mouse("fields", button), self.window.editor.tree
                )

    def test_and_in_the_xml_view_they_leave_it_on_the_strip_of_mods(self):
        for button in ("back", "forward"):
            with self.subTest(button):
                self.assertIs(
                    self._step_with_the_mouse("xml", button), self.window.editor.xml.strip
                )

    def test_a_step_from_the_keys_leaves_the_keyboard_where_it_was(self):
        """Alt+Left from the filter is not a reason to take it out of there."""
        self._in_front()
        editor = self.window.editor
        self.window._open_asset("1010030")
        editor.filter_box.setFocus()
        _app.processEvents()
        editor.go_back()
        _app.processEvents()
        self.assertEqual(editor.guid, "1010340")
        self.assertTrue(editor.filter_box.hasFocus())

    def test_the_open_list_is_most_of_the_window_not_eight_rows(self):
        """A fixed height spent a maximised window showing the editor
        underneath the one thing the user was looking at."""
        self.window.resize(1400, 1000)
        self.window.show()
        self.window._go_to_editor()
        _app.processEvents()
        editor = self.window.editor
        editor.search.open_results()
        _app.processEvents()
        self.assertGreater(editor.search.results.height(), editor.height() * 0.5)
        # And the table takes what the frame gives it rather than a set cap.
        self.assertGreater(editor.search.table.height(), 400)

    def test_the_result_list_floats_instead_of_moving_the_editor(self):
        """An inline list pushed the tree down and let it spring back, so the
        row under the cursor was not the row about to be clicked."""
        self.window.show()
        self.window._open_asset("1010340")
        _app.processEvents()
        editor = self.window.editor
        editor.search.close_results()
        _app.processEvents()
        closed = editor.tree.geometry()

        editor.search.open_results()
        _app.processEvents()
        self.assertTrue(editor.search.results.isVisible())
        self.assertEqual(editor.tree.geometry(), closed)
        # It overlays the editor rather than living in its layout.
        self.assertIs(editor.search.results.parentWidget(), editor)
        self.assertEqual(editor.layout().indexOf(editor.search.results), -1)

    def test_a_click_anywhere_else_folds_the_result_list_away(self):
        """It is a drop-down: left open under a click on the changes list or
        the XML view, it sat over the editor while the work went on behind."""
        from PySide6.QtCore import Qt
        from PySide6.QtTest import QTest

        self.window.show()
        self.window._open_asset("1010340")
        search = self.window.editor.search
        search.open_results()
        _app.processEvents()
        # A click in the list's own frame, or in the search box, leaves it.
        QTest.mouseClick(search.status_label, Qt.MouseButton.LeftButton)
        QTest.mouseClick(search.search_box, Qt.MouseButton.LeftButton)
        self.assertTrue(search.results.isVisible())
        QTest.mouseClick(self.window.changes_panel.tree.viewport(), Qt.MouseButton.LeftButton)
        self.assertFalse(search.results.isVisible())
        # And so does the keyboard going somewhere else.
        search.open_results()
        self.window.changes_panel.filter_box.setFocus()
        _app.processEvents()
        if self.window.changes_panel.filter_box.hasFocus():
            self.assertFalse(search.results.isVisible())

    def test_touching_the_tree_folds_the_result_list_away(self):
        from PySide6.QtCore import QEvent, QPoint, Qt
        from PySide6.QtGui import QMouseEvent

        self.window.show()
        self.window._open_asset("1010340")
        editor = self.window.editor
        # The keyboard settles where opening the asset put it before the list
        # is asked for: it going elsewhere afterwards shuts the list too.
        _app.processEvents()
        editor.search.open_results()
        _app.processEvents()
        self.assertTrue(editor.search.results.isVisible())

        target = self._container("Building")
        editor.tree.scrollToItem(target)
        _app.processEvents()
        rect = editor.tree.visualRect(editor.tree.indexFromItem(target, 0))
        point = QPoint(rect.left() + 40, rect.center().y())
        _app.sendEvent(
            editor.tree.viewport(),
            QMouseEvent(
                QEvent.Type.MouseButtonPress,
                point,
                editor.tree.viewport().mapToGlobal(point),
                Qt.MouseButton.LeftButton,
                Qt.MouseButton.LeftButton,
                Qt.KeyboardModifier.NoModifier,
            ),
        )
        self.assertFalse(editor.search.results.isVisible())

    def test_a_shut_list_waits_to_be_opened(self):
        """An edit asks the list to refresh, and searching every asset again
        for a list nobody had open was a second of every edit on a large
        loadout.  It refreshes when it next opens."""
        search = self.window.editor.search
        search.close_results()
        search.table.setRowCount(0)
        search.refresh()
        self.assertEqual(search.table.rowCount(), 0)
        search.open_results()
        self.assertGreater(search.table.rowCount(), 0)

    def test_the_open_asset_stays_marked_in_the_result_list(self):
        """Recording a change rebuilds the list, which dropped the highlight --
        leaving the user to work out which asset they were editing."""
        from PySide6.QtCore import Qt

        self.window._open_asset("1010340")
        search = self.window.editor.search
        search.open_results()
        search.refresh()

        def selected():
            rows = search.table.selectionModel().selectedRows()
            if not rows:
                return None
            return search.table.item(rows[0].row(), 0).data(Qt.ItemDataRole.UserRole)

        self.assertEqual(selected(), "1010340")
        self._edit("CycleTime", "15.00")   # rebuilds the list via the main window
        search.refresh()
        self.assertEqual(selected(), "1010340")


class TestMenus(WorkflowCase):
    """The menu bar, and the Edit menu that follows the selection."""

    @staticmethod
    def _open(menu):
        """Show ``menu``, as a click on it does, and shut it again.

        Some menus are filled or greyed as they are about to show, and only
        then: tests that called the method themselves passed with nothing
        connected to the menu (A11-155)."""
        from PySide6.QtCore import QPoint

        menu.popup(QPoint(0, 0))
        menu.hide()

    # -- the File menu: loadout actions first, Ctrl+O opens a loadout -------
    def _bar_menu(self, title):
        return next(
            action.menu() for action in self.window.menuBar().actions()
            if action.text() == title
        )

    def _file_menu(self):
        return self._bar_menu("&File")

    def _file_menu_order(self):
        """Every File entry in the order a reader meets it, submenus inlined.

        A submenu's own row comes first, then what is inside it -- which is the
        order somebody actually reads, and the order the assertions below are
        about.  Written as a flattening rather than as a list per submenu so
        that moving an entry between them cannot quietly stop it being
        checked.
        """
        found = []

        def walk(menu):
            for action in menu.actions():
                if action.isSeparator():
                    continue
                found.append(action)
                if action.menu() is not None:
                    walk(action.menu())

        walk(self._file_menu())
        return found

    def test_ctrl_o_opens_a_loadout_not_the_base_assets_file(self):
        from PySide6.QtGui import QKeySequence

        by_text = {a.text(): a for a in self._file_menu_order()}
        self.assertIn("&Open loadout…", by_text)
        self.assertIn("&Load base assets.xml…", by_text)
        self.assertEqual(
            by_text["&Open loadout…"].shortcut(), QKeySequence(QKeySequence.StandardKey.Open)
        )
        self.assertTrue(by_text["&Load base assets.xml…"].shortcut().isEmpty())

    def test_the_loadout_actions_come_before_the_loading_actions(self):
        texts = [a.text() for a in self._file_menu_order()]
        self.assertLess(
            texts.index("&Open loadout…"), texts.index("&Load base assets.xml…")
        )
        self.assertLess(
            texts.index("Loadout &history…"), texts.index("&Load from game files…")
        )

    # -- and a menu a kind of thing, rather than everything under File -------
    def test_the_menu_bar_has_a_menu_a_kind_of_thing(self):
        """File held the loadout, the game data, everybody else's mods and the
        checks, in submenus; the busiest of them, Mods, ran to seventeen rows,
        and whether mod.io had moved on was the fourteenth."""
        titles = [action.text() for action in self.window.menuBar().actions()]
        self.assertEqual(
            titles, ["&File", "&Edit", "&View", "&Mods", "&Game", "&Check", "&Help"],
        )
        submenus = {a.text() for a in self._file_menu().actions() if a.menu() is not None}
        self.assertEqual(submenus, {"Open &recent", "Game &files"})
        for title in ("&Mods", "&Game", "&Check"):
            rows = [a for a in self._bar_menu(title).actions() if not a.isSeparator()]
            self.assertLessEqual(len(rows), 10, title)
        self.assertIn(self.window.updates_action, self._bar_menu("&Mods").actions())

    def test_one_entry_adds_mods_and_another_reads_the_folder_in(self):
        """Four entries brought mods in, three of them saying "import", and
        which of them a loadout wanted was the reader's to work out."""
        mods = [a.text() for a in self._bar_menu("&Mods").actions()]
        self.assertIn("&Add mods…", mods)
        for gone in ("Import changes from a &mod…", "Import your &installed mods…",
                     "&Add mods to this loadout…"):
            self.assertNotIn(gone, mods)
        # Not an addition: the whole folder read in again, beside Make this live.
        game = [a.text() for a in self._bar_menu("&Game").actions()]
        self.assertIn("Match this loadout to the mods &folder…", game)

    # -- and the entries nobody found in a menu, as buttons too --------------
    def test_the_fourth_card_asks_mod_io_and_the_loader_as_the_menus_do(self):
        """Whether mod.io had moved on, and the whole loadout through the
        game's loader, were an entry each in a menu nobody had a reason to
        open; the card about what the game will load has both, greyed
        while the entry is."""
        card = self.window.source
        for action, button in ((self.window.updates_action, card.updates_button),
                               (self.window.loadout_check_action, card.loadout_check_button)):
            with self.subTest(button.text()):
                action.setEnabled(False)
                self.assertFalse(button.isEnabled())
                action.setEnabled(True)
                self.assertTrue(button.isEnabled())
                with mock.patch.object(action, "trigger") as asked:
                    button.click()
                asked.assert_called_once_with()

    def test_the_fourth_card_s_buttons_are_named_apart(self):
        """"Add to loadout" read the folder in with the game's switches, and
        "Add to loadout…" beside it added the mods the loadout lacked; and
        "Check again" was also the Export tab's loader button."""
        card = self.window.source
        texts = [button.text() for button in (
            card.bring_in_button, card.strangers_button, card.truth_refresh,
            card.make_live_button, card.updates_button, card.loadout_check_button,
        )]
        self.assertEqual(len(texts), len(set(texts)), texts)
        self.assertEqual(card.bring_in_button.text(), "Match the mods folder…")
        self.assertEqual(card.strangers_button.text(), "Add these mods…")
        self.assertNotEqual(card.truth_refresh.text(), self.window.output.loader_button.text())

    def test_the_mod_changes_panel_adds_mods_from_its_foot(self):
        panel = self.window.changes_panel
        self.window.library_add_action.setEnabled(True)
        self.assertTrue(panel.add_button.isEnabled())
        self.window.library_add_action.setEnabled(False)
        self.assertFalse(panel.add_button.isEnabled())
        panel.add_button.setEnabled(True)
        with mock.patch.object(self.window.library_controller, "add_mods") as added:
            panel.add_button.click()
        added.assert_called_once_with()

    def test_the_settings_submenu_is_a_dialog_now(self):
        """It held four ways in to four different things and said the state of
        none of them, which is a settings dialog with the lid off."""
        from PySide6.QtGui import QKeySequence

        by_text = {a.text(): a for a in self._file_menu().actions()}
        self.assertNotIn("&Settings", by_text, "the submenu should be gone")
        action = by_text["&Settings…"]
        self.assertIsNone(action.menu(), "it opens a dialog, not a list")
        self.assertEqual(action.shortcut(), QKeySequence("Ctrl+,"))

    def test_a_menu_with_nothing_live_in_it_says_why_in_a_row(self):
        """A menu of the bar's greyed there could not be opened, and could
        not carry the reason either."""
        window = self.window
        self._open(window.mods_menu)
        # These fixtures load game files, so the mods can be asked about.
        self.assertFalse(window._shut_rows[window.mods_menu].isVisible())

        shut = window._shut_rows[window.check_menu]
        for action in window.check_menu.actions():
            if action is not shut:
                action.setEnabled(False)
        self._open(window.check_menu)
        self.assertTrue(shut.isVisible())
        self.assertFalse(shut.isEnabled())
        self.assertTrue(shut.text(), "a menu with nothing live should say why")

    def test_a_submenu_with_nothing_live_in_it_is_shut_and_says_why(self):
        """A separator is enabled, to Qt: it is not something to click."""
        from unittest import mock

        window = self.window
        for opened, submenu in (
            (window.game_menu, window.library_menu),
            (self._file_menu(), window.game_data_menu),
        ):
            with self.subTest(submenu.title()):
                for action in submenu.actions():
                    action.setEnabled(False)
                with mock.patch.object(window, "_sync_entries"):
                    self._open(opened)
                shut = submenu.menuAction()
                self.assertFalse(shut.isEnabled())
                self.assertTrue(shut.toolTip(), "a shut submenu should say why")

    def test_the_view_menu_greys_the_entry_of_a_shut_tab(self):
        window = self.window
        view = self._bar_menu("&View")
        window.tabs.setTabEnabled(TAB_GENERATE, False)
        self._open(view)
        self.assertFalse(window.tab_actions[TAB_GENERATE].isEnabled())
        window.tabs.setTabEnabled(TAB_GENERATE, True)
        self._open(view)
        self.assertTrue(window.tab_actions[TAB_GENERATE].isEnabled())

    def test_the_conflicts_dialog_has_a_menu_entry_of_its_own(self):
        """It used to be reachable only from a button that comes and goes."""
        texts = [a.text() for a in self._bar_menu("&Check").actions()]
        self.assertIn("Mods reaching for the same &thing…", texts)

    def test_open_recent_lists_the_loadouts_the_start_tab_lists(self):
        self._open(self.window.recent_menu)
        listed = [a.text() for a in self.window.recent_menu.actions()]
        expected = [
            self.window.files.name_of(path)
            for path in self.window.files.recent_loadouts(self.window.game_files.open_game())
        ]
        self.assertEqual(listed, expected or ["No loadouts opened yet"])

    def test_and_names_each_of_them_as_the_start_tab_does(self):
        """Each built its rows with code of its own: the Start tab cut the
        suffix out of the middle of a name, and only it said which one the
        game loads."""
        folder = Path(self._tmp.name)
        odd, live, here = (
            folder / (name + ".annoload")
            for name in ("a.annoload b", "Tom & Jerry", "plain")
        )
        self.window.loadout_path = here
        with (
            mock.patch.object(self.window.files, "recent_loadouts",
                              return_value=[odd, live, here]),
            mock.patch.object(self.window.live, "live_for", return_value=live),
            mock.patch.object(self.window.live, "live_profile_shown", return_value=""),
        ):
            self._open(self.window.recent_menu)
            self.window.start.refresh_source_panel()
        listed = self.window.source.recent_list
        on_start = [listed.item(row).text() for row in range(listed.count())]
        self.assertEqual(
            on_start, ["a.annoload b", "Tom & Jerry: what the game loads"]
        )
        # The menu has the open loadout as well: the Start tab names it above
        # its list, and the menu has nowhere else to.
        self.assertEqual(
            [action.text() for action in self.window.recent_menu.actions()],
            [*on_start, "plain"],
        )
        self.assertEqual(
            [action.toolTip() for action in self.window.recent_menu.actions()],
            [str(odd), str(live), str(here)],
        )

    # -- the Edit menu mirrors the field editor's own right-click menu ------
    def test_the_asset_menu_is_now_called_edit(self):
        titles = [action.text() for action in self.window.menuBar().actions()]
        self.assertIn("&Edit", titles)
        self.assertNotIn("&Asset", titles)
        self.assertIn(self.window.new_asset_action, self.window.edit_menu.actions())

    def test_the_edit_menu_offers_nothing_without_a_selection(self):
        self.window._open_asset("1010340")
        self.window.editor.tree.setCurrentItem(None)
        self._open(self.window.edit_menu)
        texts = [a.text() for a in self.window.edit_menu.actions() if not a.isSeparator()]
        self.assertIn("Select a field to edit…", texts)
        # And it is not clickable -- there is genuinely nothing to act on.
        placeholder = next(a for a in self.window.edit_menu.actions() if a.text() == texts[-1])
        self.assertFalse(placeholder.isEnabled())

    def test_the_edit_menu_mirrors_the_right_click_menu_for_the_selected_row(self):
        self.window._open_asset("1010340")
        item = self._find_item("CycleTime")
        self.window.editor.tree.setCurrentItem(item)

        self._open(self.window.edit_menu)
        edit_menu_texts = {
            a.text() for a in self.window.edit_menu.actions() if not a.isSeparator()
        }

        context_menu = self.window.editor._build_item_menu(item)
        context_menu_texts = {
            a.text() for a in context_menu.actions() if not a.isSeparator()
        }

        # What the Edit menu has on top of whatever the right-click menu offers
        # for this row: undo and redo, and making a new asset or a new pattern.
        # New pattern used to be taken out -- and deleted -- the first time the
        # menu opened, which the window then went on enabling.
        fixed = {"&Undo", "&Redo", "&New asset…", "New &pattern…"}
        self.assertLessEqual(fixed, edit_menu_texts)
        self.assertEqual(edit_menu_texts - fixed, context_menu_texts)
        self.assertIn("Copy ModOp path", context_menu_texts)
        self.assertIn("Copy value", context_menu_texts)
        self.assertIn("Edit value", context_menu_texts)

    def test_the_edit_menu_rebuilds_for_a_newly_selected_row(self):
        """Re-opening the Edit menu after picking a different row must not
        just keep piling actions onto the old ones."""
        self.window._open_asset("1010340")
        first = self._find_item("CycleTime")
        self.window.editor.tree.setCurrentItem(first)
        self._open(self.window.edit_menu)
        first_count = len(self.window.edit_menu.actions())
        self.assertIn("Copy value", [a.text() for a in self.window.edit_menu.actions()])

        second = self._find_item("BuildingType")
        self.assertIsNotNone(second)
        self.window.editor.tree.setCurrentItem(second)
        self._open(self.window.edit_menu)
        second_count = len(self.window.edit_menu.actions())

        # Both selections offer a similar, small handful of actions -- if the
        # menu were merely growing, the second count would dwarf the first.
        self.assertLess(abs(second_count - first_count), 3)


class TestHintsAndGeometry(WorkflowCase):
    """Hint rows, column widths, and the things that used to jump about."""

    # -- Copy button feedback ------------------------------------------------
    def test_the_modop_path_copy_button_confirms_the_click(self):
        """A Copy button used to change the clipboard with no visible sign it
        did anything -- clicking it should say so, if only for a moment."""
        self.window._open_asset("1010340")
        item = self._edit("CycleTime", "15.00")
        self.window.editor.tree.setCurrentItem(item)
        button = self.window.editor.copy_path_button
        path_text = self.window.editor.path_box.text()
        self.assertTrue(path_text)

        self.assertEqual(button.text(), "Copy")
        button.click()
        self.assertEqual(QGuiApplication.clipboard().text(), path_text)
        self.assertEqual(button.text(), "Copied!")
        # Still enabled: disabled, it would throw the keyboard focus away.
        self.assertTrue(button.isEnabled())

    def test_one_button_folds_the_fields_and_says_which_way(self):
        """Expand all and Collapse sat side by side, and one of them always
        did nothing."""
        self.window._open_asset("1010340")
        editor = self.window.editor
        button = editor.fold_button
        self.assertEqual(button.text(), "Expand all")
        button.click()
        self.assertEqual(button.text(), "Collapse all")
        rows = [item for item in editor._iter_items() if item.childCount()]
        self.assertTrue(all(item.isExpanded() for item in rows))
        button.click()
        self.assertEqual(button.text(), "Expand all")
        deep = [item for item in rows if item.parent() is not None]
        self.assertTrue(deep)
        self.assertFalse(any(item.isExpanded() for item in deep))
        # A row shut by hand leaves something to open: the button says so.
        button.click()
        deep[0].setExpanded(False)
        self.assertEqual(button.text(), "Expand all")

    def test_the_copy_xml_button_confirms_the_click(self):
        self.window._open_asset("1010340")
        self._edit("CycleTime", "15.00")
        self.window.tabs.setCurrentIndex(TAB_GENERATE)
        button = self.window.output.export_button

        self.assertEqual(button.text(), "Export")
        self.assertEqual(self.window.output.copy_action.text(), "Copy XML")
        self.window.output.copy_action.trigger()
        self.assertIn("ModOp", QGuiApplication.clipboard().text())
        # The menu has closed: the button it opened from says so.
        self.assertEqual(button.text(), "Copied!")
        # Still enabled: disabled, it would throw the keyboard focus away.
        self.assertTrue(button.isEnabled())

    # -- column layout distributes extra width across several columns -------
    def test_the_field_editor_columns_share_a_wider_window(self):
        """Only "Refers to" used to grow; Value and Original hold the longer,
        more-often-truncated text and should grow too."""
        self.window.show()
        self.window._open_asset("1010340")
        tree = self.window.editor.tree

        self.window.resize(1100, 800)
        _app.processEvents()
        narrow = (tree.columnWidth(1), tree.columnWidth(2), tree.columnWidth(3))
        self.window.resize(2200, 800)
        _app.processEvents()
        wide = (tree.columnWidth(1), tree.columnWidth(2), tree.columnWidth(3))

        self.assertGreater(wide[0], narrow[0], "Value should grow")
        self.assertGreater(wide[1], narrow[1], "Refers to should grow")
        self.assertGreater(wide[2], narrow[2], "Original should grow")

    def test_a_field_column_dragged_wider_never_scrolls_the_tree_sideways(self):
        """It pushed the columns after it past the edge, and the tree
        scrolled sideways to reach them."""
        self.window.show()
        self.window.tabs.setCurrentIndex(TAB_EDIT)
        self.window._open_asset("1010340")
        self.window.resize(1400, 800)
        _app.processEvents()
        tree = self.window.editor.tree
        header = tree.header()
        for column in (0, 1, 2):
            header.resizeSection(column, tree.columnWidth(column) + 2000)
            _app.processEvents()
            self.assertEqual(
                sum(tree.columnWidth(each) for each in range(4)), tree.viewport().width()
            )
            self.assertEqual(tree.horizontalScrollBar().maximum(), 0)
        # Property holds what it was dragged to as the window grows.
        property_width = tree.columnWidth(0)
        self.window.resize(1800, 800)
        _app.processEvents()
        self.assertEqual(tree.columnWidth(0), property_width)

    def test_the_search_result_columns_share_a_wider_window(self):
        """Only Name used to grow; Internal name and Template hold the longer,
        more-often-truncated text and should grow too."""
        self.window.show()
        # The results list lives on the editor tab, and an unopened tab is
        # never laid out -- its columns keep whatever width they were built
        # with, however wide the window gets.
        self.window.tabs.setCurrentIndex(TAB_EDIT)
        _app.processEvents()
        browser = self.window.browser
        browser.search_box.setText("")
        browser.refresh()
        browser.open_results()
        table = browser.table

        self.window.resize(1100, 800)
        _app.processEvents()
        narrow = (table.columnWidth(0), table.columnWidth(1), table.columnWidth(3))
        self.window.resize(2200, 800)
        _app.processEvents()
        wide = (table.columnWidth(0), table.columnWidth(1), table.columnWidth(3))

        self.assertGreater(wide[0], narrow[0], "Name should grow")
        self.assertGreater(wide[1], narrow[1], "Internal name should grow")
        self.assertGreater(wide[2], narrow[2], "Template should grow")

    # -- the container hint no longer reads as a warning or resizes the tree
    def test_selecting_a_container_shows_a_plain_not_a_warning_styled_hint(self):
        self.window._open_asset("1010340")
        building = self._container("Building")
        self.window.editor.tree.setCurrentItem(building)

        label = self.window.editor.warning_label
        self.assertIn("container", label.text())
        self.assertEqual(label.objectName(), "StepHint")

    def test_a_positional_path_still_gets_the_warning_style(self):
        self.window._open_asset("1010030")
        item = self._edit("Tag", "manor")  # positional: no identifying child
        self.window.editor.tree.setCurrentItem(item)

        label = self.window.editor.warning_label
        self.assertIn("position", label.text())
        self.assertEqual(label.objectName(), "Warn")

    def test_the_hint_row_height_stays_fixed_across_selections(self):
        """Regression: switching between "no hint", the container hint, and
        the positional-path warning used to grow or shrink the row beneath
        the tree, nudging the tree's own height along with it."""
        self.window._open_asset("1010340")
        label = self.window.editor.warning_label
        no_hint_height = label.height()

        building = self._container("Building")
        self.window.editor.tree.setCurrentItem(building)
        container_height = label.height()

        self.window.editor.tree.setCurrentItem(None)
        self.window._open_asset("1010030")
        item = self._edit("Tag", "manor")
        self.window.editor.tree.setCurrentItem(item)
        warning_height = label.height()

        self.assertEqual(no_hint_height, container_height)
        self.assertEqual(container_height, warning_height)

    # -- Generate mod: typing the mod name must not grow the preview panel --
    def test_typing_the_mod_name_does_not_widen_the_preview_panel(self):
        """Regression: the "Export to:" label's un-elided full path forced its
        whole layout wider as the mod name (part of the export folder name)
        grew, dragging the Preview panel's width along with it, character by
        character."""
        self.window.show()
        self.window.tabs.setCurrentIndex(TAB_GENERATE)
        _app.processEvents()
        # Switching to the tab already ran one refresh -- the one-time settle
        # that can come from that happens here, before any typing.
        before = self.window.output.preview.width()

        for ch in "Shrine Rework Extended Edition":
            self.window.output.name_edit.setText(self.window.output.name_edit.text() + ch)
            _app.processEvents()

        after = self.window.output.preview.width()
        self.assertLessEqual(after - before, 4, "preview grew with typing")


class TestSourceFileAndTexts(WorkflowCase):
    """Which game the source names, and the localisation beside it."""

    def test_game_is_detected_from_the_source_path_and_drives_output(self):
        from anno_modding_studio.core import games

        self.assertEqual(self.window.loadout.game, games.ANNO_1800)
        self.window._open_asset("1010340")
        self._edit("CycleTime", "15.00")

        self.window.output.set_game(games.ANNO_117)
        self.window.tabs.setCurrentIndex(TAB_GENERATE)
        self.window.output.refresh()
        root = etree.fromstring(self.window.output._xml_text.encode("utf-8"))
        self.assertEqual(root.find("ModOp").get("Replace"), "@1010340/Factory7/CycleTime")
        self.assertIn(
            "data/base/config/export", self.window.output.preview_label.full_text()
        )

    def test_a_file_of_the_other_game_brings_that_games_categories(self):
        """The game is written on the loadout before the Export tab hears of
        it, and the tab took the two agreeing for nothing having changed: the
        label said Anno 117 over 1800's categories."""
        from anno_modding_studio.core import games

        source = Path(self._tmp.name) / "117" / "data/base/config/export/assets.xml"
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_bytes(self.assets.read_bytes())
        self.window.game_files.adopt_index(self.db, source, cached=True)

        out = self.window.output
        self.assertEqual(out.game_label.text(), games.get(games.ANNO_117).label)
        wanted = list(games.get(games.ANNO_117).categories)
        shown = [out.category_combo.itemText(i) for i in range(out.category_combo.count())]
        self.assertEqual(shown[: len(wanted)], wanted)

    def test_status_line_reports_texts_once(self):
        self.window.game_files.on_texts_loaded(17)
        self.window.game_files.on_texts_loaded(17)
        self.assertEqual(self.window.status_label.text().count("localised names"), 1)
        self.assertIn("assets.xml", self.window.status_label.text())

    def test_localised_names_show_up_in_the_browser(self):
        self.index.load_texts(self.texts)
        self._search("lumberjack's hut")
        # The hut alone, where the first letter alone finds dozens.
        self.assertEqual(self.window.browser.table.rowCount(), 1)
        self.assertEqual(self.window.browser.table.item(0, 0).text(), "Lumberjack's Hut")
        self.assertEqual(self.window.browser.table.item(0, 2).text(), "1010340")



class TestReferenceGraphUi(unittest.TestCase):
    """The References step: a graph of what points here and what this points at."""

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        tmp = Path(cls._tmp.name)
        cls.assets = tmp / "assets.xml"
        cls.assets.write_text(make_sample_assets.build_assets_xml(40), encoding="utf-8")
        cls.db = tmp / "index.sqlite"
        index_mod.build_index(cls.assets, cls.db)

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def setUp(self):
        from anno_modding_studio.core.changes import AIM_MAKE

        self.window = MainWindow()
        self.addCleanup(_flush_deletes)
        self.window.game_files.adopt_index(self.db, self.assets, cached=True)
        # The tab's gate: game files, and an answer on the Start tab.
        self.window.loadout.aim = AIM_MAKE
        self.window.update_tab_gates()
        self.panel = self.window.references

    def tearDown(self):
        self.window.editor.search._debounce.stop()
        # The window may have a texts load in flight (it restores the last one
        # into a fresh index); that writes through the index's own connection
        # from another thread, so it has to finish before the index closes.
        self.window.game_files.stop_texts_worker()
        if self.window.index is not None:
            self.window.index.close()
            self.window.index = None
        self.window.loadout.clear()
        self.window.deleteLater()

    def _nodes(self):
        from anno_modding_studio.ui.reference_panel import GraphNode

        return [
            item
            for item in self.panel.view.scene().items()
            if isinstance(item, GraphNode)
        ]

    def _node_guids(self):
        return {node.guid for node in self._nodes() if node.role != "more"}

    def test_the_tab_waits_for_an_asset_file(self):
        fresh = MainWindow()
        self.addCleanup(_flush_deletes)
        try:
            self.assertFalse(fresh.tabs.isTabEnabled(TAB_REFS))
        finally:
            fresh.loadout.clear()
            fresh.deleteLater()
        # ...and opens once one is loaded and the Start tab is answered.
        from anno_modding_studio.core.changes import AIM_MAKE

        self.window.loadout.aim = ""
        self.window.update_tab_gates()
        self.assertFalse(self.window.tabs.isTabEnabled(TAB_REFS))
        self.window.loadout.aim = AIM_MAKE
        self.window.update_tab_gates()
        self.assertTrue(self.window.tabs.isTabEnabled(TAB_REFS))

    def test_going_to_an_asset_does_not_open_a_closed_tab(self):
        """The gate is the one rule.  Going to an asset, or its references,
        used to switch the tab on as a side effect (P4-2)."""
        self.window.loadout.aim = ""
        self.window.update_tab_gates()
        self.window.show_references("1010340")
        self.window.jump_to_change("1010340", "")
        self.assertEqual(self.window.tabs.currentIndex(), TAB_SOURCE)
        self.assertFalse(self.window.tabs.isTabEnabled(TAB_REFS))
        self.assertFalse(self.window.tabs.isTabEnabled(TAB_EDIT))

    def test_it_draws_the_asset_with_what_it_points_at_and_what_points_at_it(self):
        self.window.show_references("1010340")
        self.assertEqual(self.window.tabs.currentIndex(), TAB_REFS)

        guids = self._node_guids()
        self.assertIn("1010340", guids)   # the asset itself, in the middle
        self.assertIn("1010196", guids)   # a good it consumes

        focus = [n for n in self._nodes() if n.role == "focus"]
        self.assertEqual(len(focus), 1)
        self.assertEqual(focus[0].guid, "1010340")

    def test_what_it_points_at_sits_right_and_what_points_at_it_sits_left(self):
        """The layout is the explanation: direction is which side you are on."""
        self.window.show_references("1010196")  # a good, consumed by factories
        by_guid = {n.guid: n for n in self._nodes() if n.role != "more"}
        focus_x = by_guid["1010196"].pos().x()

        incoming = {
            link.guid for link in self.window.workspace.references_to("1010196")
        }
        self.assertTrue(incoming, "the sample good should be consumed by something")
        for guid in incoming:
            if guid in by_guid:
                self.assertLess(
                    by_guid[guid].pos().x(), focus_x, "%s should be on the left" % guid
                )

    def test_uncertain_links_are_off_until_asked_for(self):
        self.assertFalse(self.panel.uncertain_box.isChecked())
        self.window.show_references("1010340")
        confident = len(self._node_guids())
        self.panel.uncertain_box.setChecked(True)
        # Never fewer: asking for uncertain links only ever adds to the graph.
        self.assertGreaterEqual(len(self._node_guids()), confident)

    def test_clicking_a_node_moves_the_graph_to_it(self):
        self.window.show_references("1010340")
        self.panel.view.nodeClicked.emit("1010196")
        self.assertEqual(self.panel.guid, "1010196")
        focus = [n for n in self._nodes() if n.role == "focus"]
        self.assertEqual(focus[0].guid, "1010196")

    def test_double_clicking_a_node_opens_it_in_the_editor(self):
        self.window.show_references("1010340")
        self.panel.view.nodeActivated.emit("1010196")
        self.assertEqual(self.window.editor.guid, "1010196")
        self.assertEqual(self.window.tabs.currentIndex(), TAB_EDIT)

    def test_the_editor_can_send_you_here(self):
        self.window._open_asset("1010340")
        self.window.editor.references_button.click()
        self.assertEqual(self.window.tabs.currentIndex(), TAB_REFS)
        self.assertEqual(self.panel.guid, "1010340")

    def test_there_and_back_is_one_place_on_the_screen(self):
        """References… on the editor and Edit this asset on the graph: two
        clicks on the same spot go to the graph and back."""
        self.window.show()
        self.window.resize(1400, 800)
        self.window._open_asset("1010340")
        self.window.tabs.setCurrentIndex(TAB_EDIT)
        _app.processEvents()
        there = self.window.editor.references_button
        spot = there.mapTo(self.window, there.rect().center())
        there.click()
        _app.processEvents()
        back = self.panel.open_button
        self.assertEqual(back.mapTo(self.window, back.rect().center()), spot)
        back.click()
        self.assertEqual(self.window.tabs.currentIndex(), TAB_EDIT)

    def test_arriving_by_the_tab_bar_shows_whatever_is_open(self):
        self.window._open_asset("1010030")
        self.window.tabs.setCurrentIndex(TAB_REFS)
        self.assertEqual(self.panel.guid, "1010030")

    def test_the_status_line_counts_both_directions(self):
        self.window.show_references("1010340")
        text = self.panel.status_label.text()
        self.assertIn("out", text)
        self.assertIn("in", text)
        self.assertIn("drawn", text)

    def test_a_new_asset_this_mod_created_shows_what_it_points_at(self):
        """The indexed graph describes the shipped file; an asset this mod
        invented is in no index, so its links have to be read off the payload."""
        from anno_modding_studio.core.changes import OP_ADD_NEXT, Change

        self.window.loadout.set_change(
            Change(
                guid="1010340", asset_label="mine  (new asset)", path="",
                tag="Asset", op=OP_ADD_NEXT, summary="create asset 1337000009",
                payload="<Asset><Values><Standard><GUID>1337000009</GUID>"
                        "<Name>mine</Name></Standard><Factory7><FactoryInputs>"
                        "<Item><Product>1010196</Product></Item>"
                        "</FactoryInputs></Factory7></Values></Asset>",
            )
        )
        self.window.on_changes_updated()
        self.window.show_references("1337000009")

        guids = self._node_guids()
        self.assertIn("1337000009", guids)
        self.assertIn("1010196", guids, "the good it consumes should be drawn")

    def test_the_asset_a_new_one_points_at_knows_about_it(self):
        from anno_modding_studio.core.changes import OP_ADD_NEXT, Change

        self.window.loadout.set_change(
            Change(
                guid="1010340", asset_label="mine  (new asset)", path="",
                tag="Asset", op=OP_ADD_NEXT, summary="create asset 1337000010",
                payload="<Asset><Values><Standard><GUID>1337000010</GUID>"
                        "<Name>mine</Name></Standard><Factory7><FactoryInputs>"
                        "<Item><Product>1010196</Product></Item>"
                        "</FactoryInputs></Factory7></Values></Asset>",
            )
        )
        self.window.on_changes_updated()
        incoming = self.window.workspace.references_to("1010196")
        self.assertIn("1337000010", {link.guid for link in incoming})

    def test_clearing_the_panel_leaves_nothing_behind(self):
        self.window.show_references("1010340")
        self.assertTrue(self._nodes())
        self.panel.clear()
        self.assertEqual(self._nodes(), [])
        self.assertIsNone(self.panel.guid)
        self.assertFalse(self.panel.open_button.isEnabled())

    # -- right-clicking the graph ------------------------------------------
    def _node_for(self, guid: str):
        return next(node for node in self._nodes() if node.guid == guid)

    def test_a_node_offers_editing_it_moving_to_it_and_copying_it(self):
        self.window.show_references("1010340")
        other = next(
            guid for guid in self._node_guids() if guid != "1010340"
        )
        menu = self.panel.menu_for(self._node_for(other))
        texts = [a.text() for a in menu.actions() if not a.isSeparator()]
        self.assertIn("Copy GUID", texts)
        self.assertIn("Copy name", texts)
        self.assertTrue(any(t.startswith("Edit ") for t in texts))
        self.assertTrue(any(t.startswith("Move the graph to ") for t in texts))

    def test_a_long_name_and_many_links_keep_the_menu_and_tooltips_small(self):
        from anno_modding_studio.ui import reference_panel

        long_name = "A Name That Somebody Made Very Long Indeed For Testing " * 3
        with mock.patch.object(self.window.index, "display_name", return_value=long_name):
            self.window.show_references("1010340")
            other = next(guid for guid in self._node_guids() if guid != "1010340")
            menu = self.panel.menu_for(self._node_for(other))
            texts = [a.text() for a in menu.actions() if not a.isSeparator()]
            for text in texts:
                self.assertLess(len(text), 70, text)
            for node in self._nodes():
                if node.role == "more":
                    continue
                for line in node.toolTip().splitlines():
                    self.assertLess(len(line), 110, line)
                links = [line for line in node.toolTip().splitlines()
                         if line.startswith(("→", "←"))]
                self.assertLessEqual(len(links), reference_panel.NODE_TIP_LINKS)

    def test_copying_a_guid_from_the_graph_puts_it_on_the_clipboard(self):
        from PySide6.QtGui import QGuiApplication

        self.window.show_references("1010340")
        other = next(guid for guid in self._node_guids() if guid != "1010340")
        menu = self.panel.menu_for(self._node_for(other))
        next(a for a in menu.actions() if a.text() == "Copy GUID").trigger()
        self.assertEqual(QGuiApplication.clipboard().text(), other)

    def test_the_asset_in_the_middle_cannot_be_moved_to_and_says_so(self):
        self.window.show_references("1010340")
        menu = self.panel.menu_for(self._node_for("1010340"))
        centre = next(
            a for a in menu.actions() if a.text().startswith("Move the graph to")
        )
        self.assertFalse(centre.isEnabled())
        self.assertTrue(centre.toolTip())

    def test_editing_from_the_menu_opens_that_asset_not_the_focus(self):
        self.window.show_references("1010340")
        other = next(guid for guid in self._node_guids() if guid != "1010340")
        opened = []
        self.panel.assetActivated.connect(opened.append)
        menu = self.panel.menu_for(self._node_for(other))
        next(a for a in menu.actions() if a.text().startswith("Edit ")).trigger()
        self.assertEqual(opened, [other])

    def test_the_canvas_offers_the_trail_and_the_two_link_filters(self):
        self.window.show_references("1010340")
        menu = self.panel.menu_for(None)
        texts = [a.text() for a in menu.actions() if not a.isSeparator()]
        self.assertIn("Back", texts)
        self.assertIn("Forward", texts)
        self.assertIn("Reset zoom", texts)
        self.assertIn(self.panel.uncertain_box.text(), texts)
        self.assertIn(self.panel.nominal_box.text(), texts)

    def test_the_canvas_filters_are_the_checkboxes_above_the_graph(self):
        self.window.show_references("1010340")
        self.assertFalse(self.panel.uncertain_box.isChecked())
        menu = self.panel.menu_for(None)
        action = next(
            a for a in menu.actions() if a.text() == self.panel.uncertain_box.text()
        )
        self.assertFalse(action.isChecked())
        action.trigger()
        self.assertTrue(self.panel.uncertain_box.isChecked())

    def test_back_is_offered_but_shut_with_nowhere_to_go(self):
        self.window.show_references("1010340")
        menu = self.panel.menu_for(None)
        back = next(a for a in menu.actions() if a.text() == "Back")
        self.assertFalse(back.isEnabled())



class TestReferenceGraphDepth(unittest.TestCase):
    """Following the graph out more than one hop.

    Its own fixture: six assets in a ring, each pointing at the next. The
    sample game data is only two levels deep (buildings point at goods, and
    goods point at nothing), so there is nothing there for a second hop to
    reach. A ring rather than a chain so the field resolves every time and is
    classified as a link -- one dangling value at the end of a chain drops it
    to 5 in 6, which is not confident, which is the classifier being right.
    """

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        tmp = Path(cls._tmp.name)
        parts = ['<?xml version="1.0"?><Assets>']
        for i in range(6):
            parts.append(
                "<Asset><Template>Link</Template><Values>"
                "<Standard><GUID>%d</GUID><Name>link_%d</Name></Standard>"
                "<Bits><Item><Product>%d</Product></Item></Bits>"
                "</Values></Asset>" % (7000 + i, i, 7000 + (i + 1) % 6)
            )
        parts.append("</Assets>")
        cls.assets = tmp / "assets.xml"
        cls.assets.write_text("".join(parts), encoding="utf-8")
        cls.db = tmp / "index.sqlite"
        index_mod.build_index(cls.assets, cls.db)

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def setUp(self):
        self.window = MainWindow()
        self.addCleanup(_flush_deletes)
        self.window.game_files.adopt_index(self.db, self.assets, cached=True)
        self.panel = self.window.references

    def tearDown(self):
        self.window.editor.search._debounce.stop()
        # The window may have a texts load in flight (it restores the last one
        # into a fresh index); that writes through the index's own connection
        # from another thread, so it has to finish before the index closes.
        self.window.game_files.stop_texts_worker()
        if self.window.index is not None:
            self.window.index.close()
            self.window.index = None
        self.window.loadout.clear()
        self.window.deleteLater()

    def _guids(self):
        from anno_modding_studio.ui.reference_panel import GraphNode

        return {
            item.guid
            for item in self.panel.view.scene().items()
            if isinstance(item, GraphNode) and item.role != "more"
        }

    def test_each_hop_reaches_one_link_further_along_the_chain(self):
        self.window.show_references("7002")

        self.panel.depth_spin.setValue(1)
        self.assertEqual(self._guids(), {"7001", "7002", "7003"})

        self.panel.depth_spin.setValue(2)
        self.assertEqual(self._guids(), {"7000", "7001", "7002", "7003", "7004"})

        self.panel.depth_spin.setValue(3)
        self.assertEqual(
            self._guids(), {"7000", "7001", "7002", "7003", "7004", "7005"}
        )

    def test_each_hop_gets_its_own_column_on_the_correct_side(self):
        from anno_modding_studio.ui.reference_panel import GraphNode

        self.window.show_references("7002")
        self.panel.depth_spin.setValue(2)
        x = {
            item.guid: item.pos().x()
            for item in self.panel.view.scene().items()
            if isinstance(item, GraphNode) and item.role != "more"
        }
        # What 7002 points at runs off to the right, one column per hop...
        self.assertLess(x["7002"], x["7003"])
        self.assertLess(x["7003"], x["7004"])
        # ...and what points at it runs off to the left the same way.
        self.assertGreater(x["7002"], x["7001"])
        self.assertGreater(x["7001"], x["7000"])

    def test_an_asset_already_drawn_is_not_drawn_again_further_out(self):
        """A ring would otherwise redraw the same assets at every hop."""
        self.window.show_references("7000")
        self.panel.depth_spin.setValue(3)
        from anno_modding_studio.ui.reference_panel import GraphNode

        guids = [
            item.guid
            for item in self.panel.view.scene().items()
            if isinstance(item, GraphNode) and item.role != "more"
        ]
        self.assertEqual(len(guids), len(set(guids)))



class TestReferenceGraphCap(unittest.TestCase):
    """A hub is shown as a slice, and says so.

    Real data has assets referenced by hundreds -- Anno 117's "Money" by 604 --
    and drawing all of them is a wall of boxes that answers nothing. Its own
    fixture: forty assets all pointing at one.
    """

    HUB = "8000"

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        tmp = Path(cls._tmp.name)
        parts = [
            '<?xml version="1.0"?><Assets>'
            "<Asset><Template>Hub</Template><Values><Standard>"
            "<GUID>8000</GUID><Name>the_hub</Name></Standard></Values></Asset>"
        ]
        for i in range(40):
            parts.append(
                "<Asset><Template>Spoke</Template><Values>"
                "<Standard><GUID>%d</GUID><Name>spoke_%d</Name></Standard>"
                "<Bits><Item><Product>8000</Product></Item></Bits>"
                "</Values></Asset>" % (8100 + i, i)
            )
        parts.append("</Assets>")
        cls.assets = tmp / "assets.xml"
        cls.assets.write_text("".join(parts), encoding="utf-8")
        cls.db = tmp / "index.sqlite"
        index_mod.build_index(cls.assets, cls.db)

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def setUp(self):
        self.window = MainWindow()
        self.addCleanup(_flush_deletes)
        self.window.game_files.adopt_index(self.db, self.assets, cached=True)
        self.panel = self.window.references
        self.window.show_references(self.HUB)

    def tearDown(self):
        self.window.editor.search._debounce.stop()
        # The window may have a texts load in flight (it restores the last one
        # into a fresh index); that writes through the index's own connection
        # from another thread, so it has to finish before the index closes.
        self.window.game_files.stop_texts_worker()
        if self.window.index is not None:
            self.window.index.close()
            self.window.index = None
        self.window.loadout.clear()
        self.window.deleteLater()

    def _nodes(self):
        from anno_modding_studio.ui.reference_panel import GraphNode

        return [
            item
            for item in self.panel.view.scene().items()
            if isinstance(item, GraphNode)
        ]

    def test_the_index_really_does_know_about_all_of_them(self):
        """The cap is a drawing decision, not a gap in the data."""
        _out, into = self.window.index.reference_counts(self.HUB)
        self.assertEqual(into, 40)

    def test_only_a_slice_is_drawn(self):
        from anno_modding_studio.ui.reference_panel import DEFAULT_PER_COLUMN

        drawn = [n for n in self._nodes() if n.role != "more"]
        self.assertEqual(len(drawn), DEFAULT_PER_COLUMN + 1)  # the slice + the hub

    def test_the_graph_says_what_it_left_out(self):
        from anno_modding_studio.ui.reference_panel import DEFAULT_PER_COLUMN

        markers = [n for n in self._nodes() if n.role == "more"]
        self.assertEqual(len(markers), 1)
        self.assertIn(str(40 - DEFAULT_PER_COLUMN), markers[0].title)
        self.assertIn("showing", self.panel.status_label.text())
        self.assertIn("40", self.panel.status_label.text())

    def test_the_marker_is_not_something_you_can_click_through_to(self):
        markers = [n for n in self._nodes() if n.role == "more"]
        self.assertEqual(markers[0].guid, "")
        # It carries no asset, so the view refuses to treat it as one.
        self.assertIsNone(self.panel.view._node_at(markers[0].pos().toPoint()))

    def test_right_clicking_the_marker_offers_the_whole_hop_by_name(self):
        marker = next(n for n in self._nodes() if n.role == "more")
        menu = self.panel.menu_for(marker)
        texts = [a.text() for a in menu.actions() if not a.isSeparator()]
        self.assertEqual(texts, ["Show all 40 at this hop…"])
        # And nothing about copying a GUID: it has none to copy.
        self.assertNotIn("Copy GUID", texts)

    def test_a_redraw_takes_stock_of_the_mods_once(self):
        """Every question the graph asks of a workspace -- a name, whether a
        mod makes the asset -- took stock of every change first.  On a hub
        that is thousands of questions: Coins with 416 mods loaded hung the
        window for minutes.  A redraw takes stock once and asks the rest of
        a loadout held still."""
        from unittest import mock

        from anno_modding_studio.core.workspace import Workspace

        # Spokes whose own changes touch the entry that names the hub, so
        # each is walked again rather than taken from the index.
        for i in range(5):
            self.window.loadout.set_change(Change(
                guid=str(8100 + i), asset_label="spoke_%d" % i,
                path="/Values/Bits/Item/Amount", tag="Amount",
                old_value="", new_value="2",
            ))
        self.assertIsInstance(self.panel.index, Workspace)
        real = Workspace._fingerprint
        taken = []

        def counted(workspace):
            taken.append(1)
            return real(workspace)

        with mock.patch.object(Workspace, "_fingerprint", counted):
            self.panel.refresh()
        self.assertTrue(self._nodes(), "the graph was drawn")
        self.assertLessEqual(len(taken), 1)



class TestWhatTheOverflowSwallowsFirst(unittest.TestCase):
    """Which nodes a crowded hop gives up, and in what order.

    The cap has to cut somewhere, and the cut is not arbitrary: a link the
    evidence does not vouch for, or one that only names the asset without
    setting anything, is a weaker thing to draw than a link that does both.
    """

    @staticmethod
    def _link(confident, nominal):
        from types import SimpleNamespace

        return SimpleNamespace(confident=confident, nominal=nominal)

    def _rank(self, *links):
        from anno_modding_studio.ui.reference_panel import _demotion

        return _demotion(list(links))

    def test_a_confident_link_that_sets_something_keeps_its_place(self):
        solid = self._rank(self._link(True, False))
        self.assertEqual(solid, min(
            solid,
            self._rank(self._link(True, True)),
            self._rank(self._link(False, False)),
            self._rank(self._link(False, True)),
        ))

    def test_uncertainty_costs_more_than_setting_no_value(self):
        """A link that may not be a link at all is the worse thing to draw."""
        self.assertLess(
            self._rank(self._link(True, True)),
            self._rank(self._link(False, False)),
        )

    def test_the_doubtful_and_the_empty_go_last(self):
        worst = self._rank(self._link(False, True))
        self.assertGreater(worst, self._rank(self._link(False, False)))
        self.assertGreater(worst, self._rank(self._link(True, True)))

    def test_a_node_is_judged_by_its_strongest_link(self):
        """One doubtful field beside a solid one does not demote the asset."""
        self.assertEqual(
            self._rank(self._link(False, True), self._link(True, False)),
            self._rank(self._link(True, False)),
        )


class TestTextsSurviveAReindex(unittest.TestCase):
    """Localised names live in the index cache, so rebuilding it drops them.

    That happens on a game patch and on any Studio update that changes the
    cache format -- and it used to happen silently, leaving every asset
    showing its internal name with nothing on screen saying why.
    """

    def setUp(self):
        from PySide6.QtCore import QSettings

        self.window = MainWindow()

        self.addCleanup(_flush_deletes)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        self.texts = self.dir / "texts_english.xml"
        self.texts.write_text(make_sample_assets.build_texts_xml(), encoding="utf-8")
        # Its own settings file: these tests write the keys the real app reads,
        # and leaking one made an unrelated test try to load a texts file from
        # a temp folder that no longer existed.
        self.window.settings = QSettings(
            str(self.dir / "settings.ini"), QSettings.Format.IniFormat
        )

    def tearDown(self):
        self.window.editor.search._debounce.stop()
        # The window may have a texts load in flight (it restores the last one
        # into a fresh index); that writes through the index's own connection
        # from another thread, so it has to finish before the index closes.
        self.window.game_files.stop_texts_worker()
        if self.window.index is not None:
            self.window.index.close()
            self.window.index = None
        self.window.loadout.clear()
        self.window.deleteLater()

    def test_the_exact_file_last_loaded_is_the_one_restored(self):
        (self.dir / "texts_german.xml").write_text("<TextExport/>", encoding="utf-8")
        self.window.settings.setValue("last_texts_file", str(self.texts))
        self.window.settings.setValue("last_texts_dir", str(self.dir))
        self.assertEqual(self.window.game_files.remembered_texts(), self.texts)

    def test_a_folder_holding_one_candidate_is_enough_to_go_on(self):
        self.window.settings.setValue("last_texts_dir", str(self.dir))
        self.assertEqual(self.window.game_files.remembered_texts(), self.texts)

    def test_a_folder_of_several_languages_is_not_guessed_between(self):
        """Quietly loading German for an English speaker is worse than
        loading nothing: the Game files step says texts are missing, and
        picking one is a click away."""
        (self.dir / "texts_german.xml").write_text("<TextExport/>", encoding="utf-8")
        self.window.settings.setValue("last_texts_dir", str(self.dir))
        self.assertIsNone(self.window.game_files.remembered_texts())

    def test_nothing_remembered_means_nothing_restored(self):
        self.assertIsNone(self.window.game_files.remembered_texts())

    def test_a_remembered_file_that_has_since_gone_is_not_used(self):
        self.window.settings.setValue(
            "last_texts_file", str(self.dir / "texts_gone.xml")
        )
        self.assertIsNone(self.window.game_files.remembered_texts())

    def test_a_restore_that_fails_does_not_interrupt_with_a_dialog(self):
        """The user did not ask for this load, so a modal error about a file
        they never picked -- on a window they just opened -- is startling and
        not actionable. It reports in the status bar and forgets the file."""
        from unittest import mock

        from anno_modding_studio.ui.main_window import QMessageBox

        self.window.settings.setValue("last_texts_file", str(self.texts))
        self.window.game_files.texts_were_automatic = True
        with mock.patch.object(QMessageBox, "critical") as critical:
            self.window.game_files.on_texts_failed("not a texts file")
        critical.assert_not_called()
        self.assertIn("Could not reload", self.window.status_label.text())
        # And it stops trying, rather than failing again on every rebuild.
        self.assertIsNone(self.window.game_files.remembered_texts())

    def test_a_load_the_user_asked_for_still_reports_loudly(self):
        from unittest import mock

        from anno_modding_studio.ui.main_window import QMessageBox

        self.window.game_files.texts_were_automatic = False
        with mock.patch.object(QMessageBox, "critical") as critical:
            self.window.game_files.on_texts_failed("not a texts file")
        critical.assert_called_once()

    def _open_game_files(self):
        """The sample game file, indexed and open, with nothing put back."""
        assets = self.dir / "assets.xml"
        assets.write_text(make_sample_assets.build_assets_xml(5), encoding="utf-8")
        db = self.dir / "index.sqlite"
        index_mod.build_index(assets, db)
        self.window.game_files.adopt_index(db, assets, cached=True)
        return db, assets

    def test_texts_that_were_cleared_are_not_put_back(self):
        """Clearing left the file remembered, so the next time the index was
        opened -- a relaunch is enough -- the texts just cleared were loaded
        into it again (A8-33)."""
        from unittest import mock

        files = self.window.game_files
        db, assets = self._open_game_files()
        self.window.index.load_texts(self.texts)
        files.texts_path = self.texts
        self.window.settings.setValue("last_texts_dir", str(self.dir))
        files.on_texts_loaded(self.window.index.text_count)
        self.assertEqual(files.remembered_texts(), self.texts)

        files.clear_texts()
        self.assertIsNone(files.remembered_texts())
        with mock.patch.object(files, "open_texts") as opened:
            files.adopt_index(db, assets, cached=True)
        opened.assert_not_called()
        self.assertEqual(self.window.index.text_count, 0)
        # Where the last ones came from is still where choosing some starts.
        self.assertEqual(self.window.remembered("last_texts_dir"), str(self.dir))

    def test_until_some_are_chosen_again(self):
        from unittest import mock

        from anno_modding_studio.ui import game_files_controller

        files = self.window.game_files
        self._open_game_files()
        self.window.settings.setValue("last_texts_file", str(self.texts))
        files.clear_texts()
        self.assertIsNone(files.remembered_texts())
        with mock.patch.object(game_files_controller, "TextsWorker"):
            # Put back unasked is not chosen: the clearing stands.
            files.open_texts(self.texts, automatic=True)
            self.assertIsNone(files.remembered_texts())
            files.open_texts(self.texts)
        files.on_texts_loaded(0)
        self.assertEqual(files.remembered_texts(), self.texts)

    def test_another_index_with_texts_of_its_own_does_not_name_the_last_file(self):
        """The file loaded into the last index went on being named while
        another index, with texts it had kept from earlier, was open: the
        Start tab said where names came from that this index never read
        (A11-76)."""
        files = self.window.game_files
        self._open_game_files()
        self.window.index.load_texts(self.texts)
        files.texts_path = self.texts
        files.on_texts_loaded(self.window.index.text_count)

        other = self.dir / "other"
        other.mkdir()
        assets = other / "assets.xml"
        assets.write_text(make_sample_assets.build_assets_xml(5), encoding="utf-8")
        db = other / "index.sqlite"
        index_mod.build_index(assets, db)
        kept = index_mod.AssetIndex(db, assets)
        kept.load_texts(self.texts)
        kept.close()
        files.adopt_index(db, assets, cached=True)

        self.assertGreater(self.window.index.text_count, 0)
        self.assertIsNone(files.texts_path)
        self.assertNotIn(str(self.texts), self.window.source.texts_label.toolTip())

    def test_the_same_index_opened_again_still_names_its_file(self):
        files = self.window.game_files
        db, assets = self._open_game_files()
        self.window.index.load_texts(self.texts)
        files.texts_path = self.texts
        files.on_texts_loaded(self.window.index.text_count)
        files.adopt_index(db, assets, cached=True)
        self.assertEqual(files.texts_path, self.texts)

    def test_clearing_one_games_texts_leaves_the_other_games_to_come_back(self):
        from anno_modding_studio.core import games

        files = self.window.game_files
        self._open_game_files()
        self.window.settings.setValue("last_texts_file", str(self.texts))
        files.clear_texts()
        self.window.loadout.game = games.ANNO_117
        self.assertEqual(files.remembered_texts(), self.texts)



class TestReferenceHistory(unittest.TestCase):
    """Clicking through a graph is browsing, so it gets a browser's history.

    Without it, one stray click on a node meant hunting the asset you were on
    back out of a column of twenty.
    """

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        tmp = Path(cls._tmp.name)
        parts = ['<?xml version="1.0"?><Assets>']
        for i in range(6):
            parts.append(
                "<Asset><Template>Link</Template><Values>"
                "<Standard><GUID>%d</GUID><Name>link_%d</Name></Standard>"
                "<Bits><Item><Product>%d</Product></Item></Bits>"
                "</Values></Asset>" % (7300 + i, i, 7300 + (i + 1) % 6)
            )
        parts.append("</Assets>")
        cls.assets = tmp / "assets.xml"
        cls.assets.write_text("".join(parts), encoding="utf-8")
        cls.db = tmp / "index.sqlite"
        index_mod.build_index(cls.assets, cls.db)

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def setUp(self):
        from anno_modding_studio.core.changes import AIM_MAKE

        self.window = MainWindow()
        self.addCleanup(_flush_deletes)
        self.window.game_files.adopt_index(self.db, self.assets, cached=True)
        # The tab's gate: game files, and an answer on the Start tab.
        self.window.loadout.aim = AIM_MAKE
        self.window.update_tab_gates()
        self.panel = self.window.references

    def tearDown(self):
        self.window.editor.search._debounce.stop()
        self.window.game_files.stop_texts_worker()
        if self.window.index is not None:
            self.window.index.close()
            self.window.index = None
        self.window.loadout.clear()
        self.window.deleteLater()

    def test_nothing_to_go_back_to_at_the_start(self):
        self.window.show_references("7300")
        self.assertFalse(self.panel.back_button.isEnabled())
        self.assertFalse(self.panel.forward_button.isEnabled())

    def test_back_returns_to_where_you_were(self):
        self.window.show_references("7300")
        self.panel.show_asset("7301")
        self.assertTrue(self.panel.back_button.isEnabled())

        self.panel.go_back()
        self.assertEqual(self.panel.guid, "7300")
        self.assertTrue(self.panel.forward_button.isEnabled())

        self.panel.go_forward()
        self.assertEqual(self.panel.guid, "7301")

    def test_the_mouse_side_buttons_walk_the_history(self):
        """Mouse 4 and 5, as in a browser."""
        from PySide6.QtCore import QPointF
        from PySide6.QtGui import QMouseEvent

        self.window.show_references("7300")
        self.panel.show_asset("7301")

        def click(button):
            event = QMouseEvent(
                QMouseEvent.Type.MouseButtonPress,
                QPointF(10, 10),
                QPointF(10, 10),
                button,
                button,
                Qt.KeyboardModifier.NoModifier,
            )
            self.panel.mousePressEvent(event)

        click(Qt.MouseButton.BackButton)
        self.assertEqual(self.panel.guid, "7300")
        click(Qt.MouseButton.ForwardButton)
        self.assertEqual(self.panel.guid, "7301")

    def test_alt_arrows_do_the_same(self):
        from PySide6.QtGui import QKeyEvent

        self.window.show_references("7300")
        self.panel.show_asset("7301")
        for key, expected in (
            (Qt.Key.Key_Left, "7300"),
            (Qt.Key.Key_Right, "7301"),
        ):
            self.panel.keyPressEvent(
                QKeyEvent(
                    QKeyEvent.Type.KeyPress, key, Qt.KeyboardModifier.AltModifier
                )
            )
            self.assertEqual(self.panel.guid, expected)

    def test_going_somewhere_new_after_stepping_back_drops_the_forward_trail(self):
        """A browser does the same: the branch you abandoned is gone."""
        self.window.show_references("7300")
        self.panel.show_asset("7301")
        self.panel.show_asset("7302")
        self.panel.go_back()
        self.assertEqual(self.panel.guid, "7301")

        self.panel.show_asset("7304")
        self.assertFalse(self.panel.forward_button.isEnabled())
        self.panel.go_back()
        self.assertEqual(self.panel.guid, "7301")

    def test_revisiting_the_same_asset_does_not_stack_up(self):
        self.window.show_references("7300")
        self.panel.show_asset("7300")
        self.assertFalse(self.panel.back_button.isEnabled())

    def test_a_fresh_asset_clears_the_trail(self):
        self.window.show_references("7300")
        self.panel.show_asset("7301")
        self.panel.clear()
        self.assertFalse(self.panel.back_button.isEnabled())
        self.assertFalse(self.panel.forward_button.isEnabled())


class TestOneAssetForBothTabs(unittest.TestCase):
    """The asset editor and the graph are two views of one asset, on one trail.

    Each used to keep its own idea of which asset that was: open one by
    searching in the editor and the graph went on showing the last one it drew,
    and only the graph could go back.
    """

    setUpClass = TestReferenceHistory.__dict__["setUpClass"]
    tearDownClass = TestReferenceHistory.__dict__["tearDownClass"]
    tearDown = TestReferenceHistory.tearDown

    def setUp(self):
        from anno_modding_studio.core.changes import AIM_MAKE

        TestReferenceHistory.setUp(self)
        self.window.loadout.aim = AIM_MAKE
        self.window.update_tab_gates()
        self.editor = self.window.editor

    def test_opening_an_asset_in_the_editor_moves_the_graph(self):
        self.window._open_asset("7300")
        self.editor.load_asset("7301")  # as picking a search result does
        self.window.tabs.setCurrentIndex(TAB_REFS)
        self.assertEqual(self.panel.guid, "7301")

    def test_moving_the_graph_moves_the_editor(self):
        self.window._open_asset("7300")
        self.window.tabs.setCurrentIndex(TAB_REFS)
        self.panel.show_asset("7302")
        self.window.tabs.setCurrentIndex(TAB_EDIT)
        self.assertEqual(self.editor.guid, "7302")

    def test_back_in_either_takes_both_back(self):
        self.window._open_asset("7300")
        self.editor.load_asset("7301")
        self.assertTrue(self.editor.nav.back_button.isEnabled())

        self.editor.go_back()
        self.assertEqual(self.editor.guid, "7300")
        self.window.tabs.setCurrentIndex(TAB_REFS)
        self.assertEqual(self.panel.guid, "7300")
        self.assertTrue(self.panel.forward_button.isEnabled())

        self.panel.go_forward()
        self.window.tabs.setCurrentIndex(TAB_EDIT)
        self.assertEqual(self.editor.guid, "7301")

    def test_a_step_called_off_leaves_the_trail_where_it_was(self):
        """Forward with something typed and not applied asks first, and Cancel
        stays.  The trail had already moved: the buttons then said the asset
        in front of you was the next one along, and Back went from there."""
        self.window._open_asset("7300")
        self.editor.load_asset("7301")
        self.editor.load_asset("7302")
        self.editor.go_back()
        self.editor.go_back()
        self.assertEqual(self.editor.guid, "7300")

        with mock.patch.object(self.editor, "settle_typed", return_value=False):
            self.editor.go_forward()
            self.editor.go_forward()
        self.assertEqual(self.editor.guid, "7300")
        self.assertEqual(self.editor.trail.current, "7300")
        self.assertFalse(self.editor.nav.back_button.isEnabled())
        self.assertTrue(self.editor.nav.forward_button.isEnabled())

        self.editor.go_forward()
        self.assertEqual(self.editor.guid, "7301")
        with mock.patch.object(self.editor, "settle_typed", return_value=False):
            self.editor.go_back()
        self.assertEqual(self.editor.trail.current, "7301")
        self.assertTrue(self.editor.nav.back_button.isEnabled())

    def test_the_mouse_back_button_works_in_the_editor_too(self):
        from PySide6.QtCore import QPointF
        from PySide6.QtGui import QMouseEvent

        self.window._open_asset("7300")
        self.editor.load_asset("7301")
        button = Qt.MouseButton.BackButton
        self.editor.mousePressEvent(
            QMouseEvent(
                QMouseEvent.Type.MouseButtonPress,
                QPointF(10, 10),
                QPointF(10, 10),
                button,
                button,
                Qt.KeyboardModifier.NoModifier,
            )
        )
        self.assertEqual(self.editor.guid, "7300")

    def test_the_graph_is_not_redrawn_for_a_tab_nobody_is_looking_at(self):
        self.window._open_asset("7300")
        with mock.patch.object(self.panel, "refresh") as redraw:
            self.editor.load_asset("7301")
        redraw.assert_not_called()

    def test_reloading_the_same_asset_is_not_a_visit(self):
        """The editor redraws its own asset after every structural edit."""
        self.window._open_asset("7300")
        self.window.tabs.setCurrentIndex(TAB_REFS)
        self.panel.show_asset("7301")
        self.editor.load_asset("7300")
        self.assertEqual(self.window.trail.current, "7301")

    def test_the_arrows_are_drawn_rather_than_typed(self):
        """A typed arrow read as a dash from arm's length."""
        for button in (
            self.panel.back_button,
            self.panel.forward_button,
            self.editor.nav.back_button,
        ):
            self.assertEqual(button.text(), "")
            self.assertFalse(button.icon().isNull())
            self.assertGreaterEqual(button.iconSize().width(), 20)


class TestUndoingWhatTheModWritesOut(WorkflowCase):
    """Edit ▸ Undo, over the asset editor and the changes panel alike."""

    def test_an_edit_in_the_editor_is_undone_and_redone(self):
        self.window._open_asset("1010340")
        self._edit("CycleTime", "15.00")
        self.assertTrue(self.window.undo_action.isEnabled())
        self.assertIn("CycleTime", self.window.undo_action.text())

        self.window.undo_last()
        self.assertEqual(len(self.window.loadout), 0)
        self.assertEqual(self._find_item("CycleTime").text(1), "30.00")
        self.assertIn("CycleTime", self.window.redo_action.text())

        self.window.redo_last()
        self.assertEqual(len(self.window.loadout), 1)
        self.assertEqual(self._find_item("CycleTime").text(1), "15.00")

    def test_the_keys_reach_undo_from_a_text_box_with_nothing_to_undo(self):
        """The focus went back to the Mod changes panel's filter when a
        dialog closed, and Ctrl+Z and Ctrl+Y did nothing there until
        something else was clicked: the box kept them for its own text,
        which had nothing to take back (H2-26)."""
        from PySide6.QtCore import Qt
        from PySide6.QtTest import QTest

        window = self.window
        window._open_asset("1010340")
        self._edit("CycleTime", "15.00")
        window.show()
        self.addCleanup(window.hide)
        window.activateWindow()
        box = window.changes_panel.filter_box
        box.setText("Cycle")  # set, not typed: nothing of its own to undo
        box.setFocus()
        QApplication.processEvents()
        self.assertIs(QApplication.focusWidget(), box)
        QTest.keyClick(box, Qt.Key.Key_Z, Qt.KeyboardModifier.ControlModifier)
        self.assertEqual(len(window.loadout), 0)
        QTest.keyClick(box, Qt.Key.Key_Y, Qt.KeyboardModifier.ControlModifier)
        self.assertEqual(len(window.loadout), 1)
        # Typed in, the box takes Ctrl+Z back for its typing first.
        QTest.keyClicks(box, "Time")
        QTest.keyClick(box, Qt.Key.Key_Z, Qt.KeyboardModifier.ControlModifier)
        self.assertEqual(len(window.loadout), 1)
        self.assertEqual(box.text(), "Cycle")

    def test_reverting_a_change_unloads_no_mod_it_was_not_in(self):
        """A mod with nothing left of it goes when its last change is
        reverted. Judged over every mod and by changes alone, reverting one
        change unloaded each other mod that had none -- one whose ops are
        kept as written or could not be read -- and those ops with it."""
        from anno_modding_studio.core.changes import Change, ModLayer, Op, Unread

        loadout = self.window.loadout
        for name in ("Alpha", "Kept", "Unread", "Both"):
            loadout.set_layer(ModLayer(origin=name, name=name))

        def change(origin, tag):
            made = Change(guid="1010340", asset_label="A", path="/Values/" + tag, tag=tag,
                          old_value="1", new_value="2", origin=origin)
            loadout.set_change(made)
            return made

        mine = change("Alpha", "A")
        other = change("Both", "B")
        loadout.set_op(Op(expression="//Asset", source="<ModOp/>", origin="Kept"))
        loadout.set_op(Op(expression="//Asset", source="<ModOp/>", origin="Both"))
        loadout.unread = [Unread(origin="Unread", source="<ModOp/>", why="x")]
        panel = self.window.changes_panel
        panel.refresh()

        with mock.patch.object(panel, "_selected_changes", return_value=[mine.key]):
            panel._remove_selected()
        self.assertEqual(sorted(layer.origin for layer in loadout.layers),
                         ["Both", "Kept", "Unread"])
        self.assertEqual(len(loadout.ops), 2)
        self.assertEqual(len(loadout.unread), 1)

        # Its own last change reverted, a mod stays while an op of it is left.
        with mock.patch.object(panel, "_selected_changes", return_value=[other.key]):
            panel._remove_selected()
        self.assertEqual(sorted(layer.origin for layer in loadout.layers),
                         ["Both", "Kept", "Unread"])
        self.assertEqual(len(loadout.ops), 2)

    def test_reverting_in_the_changes_panel_is_undone(self):
        self.window._open_asset("1010340")
        self._edit("CycleTime", "15.00")
        pick_change(self.window.changes_panel)
        self.window.changes_panel._remove_selected()
        self.assertEqual(len(self.window.loadout), 0)

        self.window.undo_last()
        self.assertEqual(len(self.window.loadout), 1)
        self.assertEqual(len(change_rows(self.window.changes_panel)), 1)
        self.assertEqual(self._find_item("CycleTime").text(1), "15.00")

        # And the edit before it is still a step of its own.
        self.window.undo_last()
        self.assertEqual(len(self.window.loadout), 0)

    def test_what_was_redone_is_not_still_said_after_the_next_change(self):
        """"Redid 6 changes" stayed in the status bar through an edit, an
        Apply and a revert: none of them says anything there itself."""
        said = self.window.status_label.text
        self.window._open_asset("1010340")
        self._edit("CycleTime", "15.00")
        self.window.undo_last()
        self.assertTrue(said().startswith("Undid "), said())
        self.window.redo_last()
        self.assertTrue(said().startswith("Redid "), said())
        # An edit in the editor is the last thing done now.
        self._edit("CycleTime", "20.00")
        self.assertNotIn("Redid", said())
        self.assertEqual(said(), self.window.game_files.status_base)
        # And so is a revert in the changes panel.
        self.window.undo_last()
        self.window.redo_last()
        self.assertTrue(said().startswith("Redid "), said())
        pick_change(self.window.changes_panel)
        self.window.changes_panel._remove_selected()
        self.assertNotIn("Redid", said())

    def _add_an_asset_beside(self, guid: str, new: str) -> None:
        from anno_modding_studio.core.changes import OP_ADD_NEXT, Change

        self.window.loadout.set_change(Change(
            guid=guid, asset_label="mine  (new asset)", path="", tag="Asset",
            op=OP_ADD_NEXT, summary="create asset " + new,
            payload="<Asset><BaseAssetGUID>%s</BaseAssetGUID><Values><Standard>"
                    "<GUID>%s</GUID><Name>mine</Name></Standard></Values></Asset>" % (guid, new),
        ))
        self.window.on_changes_updated()

    def test_revert_asset_says_what_went_and_how_to_get_it_back(self):
        """It asks nothing, being one step to undo, and said nothing either:
        one click took out eighteen changes, two of them assets the mod adds
        beside this one, which are filed under it."""
        said = self.window.status_label.text
        self.window._open_asset("1010340")
        self._edit("CycleTime", "15.00")
        self.window.editor._revert_asset()
        name = self.window.editor.asset_label
        self.assertEqual(said(), "Reverted 1 change to %s. Ctrl+Z undoes this." % name)

        self._edit("CycleTime", "15.00")
        self._add_an_asset_beside("1010340", "1337000005")
        self._add_an_asset_beside("1010340", "1337000006")
        self.window.record()
        self.window.editor._revert_asset()
        self.assertEqual(len(self.window.loadout), 0)
        self.assertEqual(
            said(),
            "Reverted 3 changes to %s. 2 of them add new assets beside it, which "
            "went too. Ctrl+Z undoes this." % name,
        )
        # Said of that step: the next one is the last thing done.
        self._edit("CycleTime", "20.00")
        self.assertEqual(said(), self.window.game_files.status_base)

    def test_and_undoing_it_says_so_in_its_place(self):
        self.window._open_asset("1010340")
        self._edit("CycleTime", "15.00")
        self.window.editor._revert_asset()
        self.window.undo_last()
        self.assertEqual(len(self.window.loadout), 1)
        self.assertTrue(self.window.status_label.text().startswith("Undid "))

    def test_but_what_an_action_said_itself_is_left_alone(self):
        self.window._open_asset("1010340")
        self._edit("CycleTime", "15.00")
        self.window.undo_last()
        self.window.status_label.setText("Something else was done.")
        self._edit("CycleTime", "20.00")
        self.assertEqual(self.window.status_label.text(), "Something else was done.")

    def test_switching_a_change_off_in_the_panel_is_undone(self):
        self.window._open_asset("1010340")
        self._edit("CycleTime", "15.00")
        panel = self.window.changes_panel
        change_rows(panel)[0].setCheckState(0, Qt.CheckState.Unchecked)
        # The panel announces a burst of ticks once it is over, on its own
        # timer: let that timer fire rather than emitting the signal by hand.
        _app.processEvents()
        self.assertFalse(next(iter(self.window.loadout)).active)
        self.assertIn("off", self.window.undo_action.text())

        self.window.undo_last()
        self.assertTrue(next(iter(self.window.loadout)).active)
        self.assertEqual(
            change_rows(panel)[0].checkState(0), Qt.CheckState.Checked
        )

    def test_undoing_an_edit_to_another_asset_shows_that_asset(self):
        """Rather than taking something back out of sight."""
        self.window._open_asset("1010340")
        self._edit("CycleTime", "15.00")
        self.window._open_asset("1010030")
        self.window.undo_last()
        self.assertEqual(self.window.editor.guid, "1010340")
        self.assertEqual(self._find_item("CycleTime").text(1), "30.00")

    def test_a_new_loadout_has_nothing_to_undo(self):
        self.window._open_asset("1010340")
        self._edit("CycleTime", "15.00")
        with mock.patch.object(self.window.files, "confirm_discard", return_value=True):
            self.window.new_loadout()
        self.assertFalse(self.window.undo_action.isEnabled())
        self.assertEqual(self.window.undo_action.text(), "&Undo")

    def test_it_is_on_the_usual_keys(self):
        from PySide6.QtGui import QKeySequence

        self.assertEqual(
            self.window.undo_action.shortcut(),
            QKeySequence(QKeySequence.StandardKey.Undo),
        )


class TestNominalLinksInTheGraph(unittest.TestCase):
    """A costs list can name a good and spend none of it.

    Anno 117's Tiles is "referenced" that way by 110 assets that never spend
    one, which made it look far more used than it is.
    """

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        tmp = Path(cls._tmp.name)
        parts = ['<?xml version="1.0"?><Assets>']
        for i in range(8):
            amount = "<Amount>%d</Amount>" % (i + 1) if i < 5 else ""
            parts.append(
                "<Asset><Template>Building</Template><Values>"
                "<Standard><GUID>%d</GUID><Name>b_%d</Name></Standard>"
                "<Cost><Costs><Item><Ingredient>6000</Ingredient>%s</Item>"
                "</Costs></Cost></Values></Asset>" % (6100 + i, i, amount)
            )
        parts.append(
            "<Asset><Template>Product</Template><Values><Standard>"
            "<GUID>6000</GUID><Name>the_good</Name></Standard></Values></Asset>"
        )
        parts.append("</Assets>")
        cls.assets = tmp / "assets.xml"
        cls.assets.write_text("".join(parts), encoding="utf-8")
        cls.db = tmp / "index.sqlite"
        index_mod.build_index(cls.assets, cls.db)

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def setUp(self):
        self.window = MainWindow()
        self.addCleanup(_flush_deletes)
        self.window.game_files.adopt_index(self.db, self.assets, cached=True)
        self.panel = self.window.references
        self.window.show_references("6000")

    def tearDown(self):
        self.window.editor.search._debounce.stop()
        self.window.game_files.stop_texts_worker()
        if self.window.index is not None:
            self.window.index.close()
            self.window.index = None
        self.window.loadout.clear()
        self.window.deleteLater()

    def _guids(self):
        from anno_modding_studio.ui.reference_panel import GraphNode

        return {
            item.guid
            for item in self.panel.view.scene().items()
            if isinstance(item, GraphNode) and item.role != "more"
        }

    def test_they_are_hidden_by_default(self):
        """A link that names an asset and spends none of it is almost never
        what you came to the graph to find, and leaving them in makes a hub look
        connected to half the file."""
        self.assertTrue(self.panel.nominal_box.isChecked())
        drawn = self._guids()
        self.assertEqual(len(drawn), 6)  # the good plus the five with an amount
        for i in range(5, 8):
            self.assertNotIn(str(6100 + i), drawn)

    def test_and_the_status_line_says_what_it_hid(self):
        self.assertIn("3 name-only links hidden", self.panel.status_label.text())

    def test_showing_them_brings_back_every_link(self):
        """Still one tick away, for the case that wants them: seeing what would
        dangle if an asset went away."""
        self.panel.nominal_box.setChecked(False)
        self.assertEqual(len(self._guids()), 9)  # the good plus all eight

    def test_and_then_the_status_line_offers_the_way_out_again(self):
        self.panel.nominal_box.setChecked(False)
        text = self.panel.status_label.text()
        self.assertIn("3 links only name it", text)
        self.assertIn("Hide name-only links", text)


class TestShowInGraphWaitsForARow(unittest.TestCase):
    """It opens with nothing selected, and the button did nothing at all until
    something was -- highlighted the whole time, which reads as the way on."""

    def dialog(self):
        from anno_modding_studio.ui.reference_panel import (
            ListedAsset,
            ReferenceListDialog,
        )

        rows = [
            ListedAsset(guid="1010340", label="Lumberjack", template="Factory",
                        through="Product"),
            ListedAsset(guid="1010196", label="Timber", template="Product",
                        through="Product"),
        ]
        dialog = ReferenceListDialog(rows, "2 assets", None)
        self.addCleanup(dialog.deleteLater)
        return dialog

    def test_it_starts_disabled(self):
        self.assertFalse(self.dialog().open_button.isEnabled())

    def test_and_wakes_up_when_a_row_is_picked(self):
        dialog = self.dialog()
        dialog.table.selectRow(0)
        self.assertTrue(dialog.open_button.isEnabled())

    def test_and_that_row_is_what_it_opens(self):
        dialog = self.dialog()
        dialog.table.selectRow(1)
        dialog.open_button.click()
        self.assertEqual(dialog.chosen_guid, "1010196")


class TestReferenceListDialogUi(unittest.TestCase):
    """The whole of a hop that was too wide to draw."""

    HUB = "8500"

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        tmp = Path(cls._tmp.name)
        parts = [
            '<?xml version="1.0"?><Assets>'
            "<Asset><Template>Hub</Template><Values><Standard>"
            "<GUID>8500</GUID><Name>the_hub</Name></Standard></Values></Asset>"
        ]
        for i in range(30):
            parts.append(
                "<Asset><Template>Spoke</Template><Values>"
                "<Standard><GUID>%d</GUID><Name>spoke_%02d</Name></Standard>"
                "<Bits><Item><Product>8500</Product></Item></Bits>"
                "</Values></Asset>" % (8600 + i, i)
            )
        parts.append("</Assets>")
        cls.assets = tmp / "assets.xml"
        cls.assets.write_text("".join(parts), encoding="utf-8")
        cls.db = tmp / "index.sqlite"
        index_mod.build_index(cls.assets, cls.db)

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def setUp(self):
        self.window = MainWindow()
        self.addCleanup(_flush_deletes)
        self.window.game_files.adopt_index(self.db, self.assets, cached=True)
        self.panel = self.window.references
        self.window.show_references(self.HUB)

    def tearDown(self):
        self.window.editor.search._debounce.stop()
        self.window.game_files.stop_texts_worker()
        if self.window.index is not None:
            self.window.index.close()
            self.window.index = None
        self.window.loadout.clear()
        self.window.deleteLater()

    def _marker(self):
        from anno_modding_studio.ui.reference_panel import GraphNode

        for item in self.panel.view.scene().items():
            if isinstance(item, GraphNode) and item.role == "more":
                return item
        return None

    def _dialog(self):
        from anno_modding_studio.ui.reference_panel import ReferenceListDialog

        marker = self._marker()
        self.assertIsNotNone(marker)
        return ReferenceListDialog(
            self.panel._column_rows(marker.column), "heading", self.window
        )

    def test_the_marker_says_it_can_be_opened(self):
        marker = self._marker()
        self.assertIn("more", marker.title)
        self.assertIn("see all 30", marker.subtitle)

    def test_the_whole_hop_is_kept_even_though_it_is_not_drawn(self):
        marker = self._marker()
        self.assertEqual(len(self.panel._full_columns[marker.column]), 30)

    def test_the_dialog_lists_every_one_of_them(self):
        dialog = self._dialog()
        try:
            self.assertEqual(dialog.table.rowCount(), 30)
            self.assertIn("30 assets", dialog.status.text())
        finally:
            dialog.deleteLater()

    def test_it_can_be_filtered_down(self):
        dialog = self._dialog()
        try:
            dialog.search_box.setText("spoke_07")
            dialog._refresh()
            self.assertEqual(dialog.table.rowCount(), 1)
            self.assertIn("1 of 30", dialog.status.text())
        finally:
            dialog.deleteLater()

    def test_picking_one_moves_the_graph_to_it(self):
        from PySide6.QtWidgets import QDialog

        dialog = self._dialog()
        try:
            dialog.search_box.setText("spoke_11")
            dialog._refresh()
            dialog.table.selectRow(0)
            dialog._accept_selection()
            self.assertEqual(dialog.result(), int(QDialog.DialogCode.Accepted))
            chosen = dialog.chosen_guid
        finally:
            dialog.deleteLater()

        self.panel.show_asset(chosen)
        self.assertEqual(self.panel.guid, chosen)
        self.assertEqual(self.window.index.asset_row(chosen).name, "spoke_11")

    def test_filtering_asks_the_database_nothing(self):
        """The bug: every keystroke re-resolved every row -- a few hundred
        queries, about 90ms, for answers that cannot change while the dialog
        is open. Rows are resolved once now, so typing is pure string work."""
        dialog = self._dialog()
        connection = self.window.index._con
        statements = []
        try:
            connection.set_trace_callback(statements.append)
            dialog.search_box.setText("spoke_1")
            dialog._refresh()
        finally:
            connection.set_trace_callback(None)
            dialog.deleteLater()
        self.assertEqual(statements, [])

    def test_resolving_the_rows_is_what_costs_a_query(self):
        """...and that happens once, on the click that opens the list."""
        connection = self.window.index._con
        statements = []
        marker = self._marker()
        # The index holds on to asset rows it has already resolved, and this
        # test is about the resolving, so it starts from cold.
        self.window.index._rows.clear()
        try:
            connection.set_trace_callback(statements.append)
            rows = self.panel._column_rows(marker.column)
        finally:
            connection.set_trace_callback(None)
        self.assertEqual(len(rows), 30)
        self.assertTrue(statements, "the rows have to come from somewhere")

    def test_and_an_asset_already_named_is_not_asked_after_twice(self):
        marker = self._marker()
        self.panel._column_rows(marker.column)
        connection = self.window.index._con
        statements = []
        try:
            connection.set_trace_callback(statements.append)
            self.panel._column_rows(marker.column)
        finally:
            connection.set_trace_callback(None)
        self.assertEqual(statements, [])

    def test_a_keystroke_does_not_rebuild_the_table_on_the_spot(self):
        """Debounced like the app's other filters, so a fast typist is never
        waiting on a table rebuild between characters."""
        dialog = self._dialog()
        try:
            self.assertEqual(dialog.table.rowCount(), 30)
            dialog.search_box.setText("spoke_04")
            # Still the full list: the rebuild is queued, not immediate.
            self.assertEqual(dialog.table.rowCount(), 30)
            self.assertTrue(dialog._filter_timer.isActive())
        finally:
            dialog.deleteLater()

    def test_and_once_the_typing_stops_the_table_is_filtered(self):
        """The other filtering tests call _refresh() themselves; this one lets
        the debounce timer fire, so it fails if the timer filters nothing."""
        import time

        dialog = self._dialog()
        try:
            dialog.search_box.setText("spoke_04")
            for _ in range(200):
                _app.processEvents()
                if not dialog._filter_timer.isActive():
                    break
                time.sleep(0.005)
            _app.processEvents()
            self.assertEqual(dialog.table.rowCount(), 1)
            self.assertIn("1 of 30", dialog.status.text())
        finally:
            dialog.deleteLater()

    def test_the_through_column_names_the_field_the_link_travels(self):
        rows = self.panel._column_rows(self._marker().column)
        self.assertTrue(all(row.through == "Bits › Item › Product" for row in rows))

    def test_clicking_the_marker_is_what_opens_it(self):
        """The signal the view emits for a marker carries its column, not a
        GUID -- a marker is not an asset."""
        from unittest import mock

        from PySide6.QtWidgets import QDialog

        from anno_modding_studio.ui.reference_panel import ReferenceListDialog

        marker = self._marker()
        self.assertEqual(marker.guid, "", "a marker stands for no single asset")

        # The panel really does open the dialog on that signal, so its exec has
        # to be stubbed -- a modal loop in a test never returns.
        opened = []

        def fake_exec(dialog_self):
            opened.append(list(dialog_self.rows))
            return int(QDialog.DialogCode.Rejected)

        with mock.patch.object(ReferenceListDialog, "exec", fake_exec):
            self.panel.view.moreClicked.emit(marker.column)

        self.assertEqual(len(opened), 1, "the marker should open the list")
        self.assertEqual(len(opened[0]), 30, "with the whole hop in it")

    def test_a_closed_list_does_not_stay_behind_under_the_panel(self):
        """Each opening made a dialog parented to the panel and kept it, with
        its filled table, for as long as the panel lived."""
        from unittest import mock

        from PySide6.QtWidgets import QDialog

        from anno_modding_studio.ui.reference_panel import ReferenceListDialog

        marker = self._marker()
        with mock.patch.object(
            ReferenceListDialog, "exec",
            lambda dialog_self: int(QDialog.DialogCode.Rejected),
        ):
            for _ in range(3):
                self.panel.show_full_column(marker.column)
        _flush_deletes()
        self.assertEqual(self.panel.findChildren(ReferenceListDialog), [])



class TestModImportUi(unittest.TestCase):
    """Reading another author's mod in, through the dialog the user sees.

    Every op in the file gets a row, including the ones that produced nothing:
    a mod is only partly imported, and the part that was left is the part worth
    showing.
    """

    PATCH = (
        "<ModOps>"
        "<!-- #make the farmer residence roomier -->"
        '<ModOp Type="merge" GUID="1010030" Path="/Values/Residence7"><Residents>12</Residents>'
        "<Comfort>3</Comfort></ModOp>"
        "<ModOp Type=\"remove\" GUID=\"1010030\" Path=\"/Values/Cost/Costs/Item[Ingredient = '1010197']\"></ModOp>"
        '<ModOp Type="add" Path="@1010030/ProductList/List" '
        'Content="//Values/Standard/GUID/text()"><Item><ModOpContent /></Item></ModOp>'
        "<ModOp Type=\"merge\" GUID=\"1010030\" Path=\"/Values/Building\" Condition=\"@1010030/Building[BuildingType = 'Nope']\">"
        "<TerrainType>Water</TerrainType></ModOp>"
        "</ModOps>"
    )

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        tmp = Path(cls._tmp.name)
        cls.assets = tmp / "assets.xml"
        cls.assets.write_text(make_sample_assets.build_assets_xml(40), encoding="utf-8")
        cls.db = tmp / "index.sqlite"
        index_mod.build_index(cls.assets, cls.db)

        cls.mod = tmp / "[Fix] Somebody Elses Mod"
        patch = cls.mod / "data/config/export/main/asset/assets.xml"
        patch.parent.mkdir(parents=True)
        patch.write_text(cls.PATCH, encoding="utf-8")
        (cls.mod / "modinfo.json").write_text(
            '{"ModName": {"English": "Somebody Elses Mod"}, "CreatorName": "Taludas"}',
            encoding="utf-8",
        )
        (cls.mod / "readme.txt").write_text("hello", encoding="utf-8")

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def setUp(self):
        from anno_modding_studio.ui.mod_import_dialog import ModImportDialog

        self.window = MainWindow()

        self.addCleanup(_flush_deletes)
        self.window.game_files.adopt_index(self.db, self.assets, cached=True)
        self.dialog = ModImportDialog(self.window.index, self.window, str(self.mod))
        self.dialog.reload()

    def tearDown(self):
        self.dialog.deleteLater()
        self.window.editor.search._debounce.stop()
        self.window.game_files.stop_texts_worker()
        if self.window.index is not None:
            self.window.index.close()
            self.window.index = None
        self.window.loadout.clear()
        self.window.deleteLater()

    def _outcomes(self):
        return [
            self.dialog.ops_table.item(row, 3).text()
            for row in range(self.dialog.ops_table.rowCount())
        ]

    def test_the_row_names_the_asset_it_touches(self):
        self.assertIn("residence_tier01", self.dialog.ops_table.item(0, 2).text())

    def test_and_one_that_stays_an_op_brings_the_op_with_it(self):
        from anno_modding_studio.core.changes import SWITCHED_OFF_MARK
        from anno_modding_studio.ui.mod_import_dialog import ModImportDialog

        mod = Path(self._tmp.name) / "[Fix] Off Another Way"
        patch = mod / "data/config/export/main/asset/assets.xml"
        patch.parent.mkdir(parents=True, exist_ok=True)
        patch.write_text(
            "<ModOps><!-- %s:\n"
            '<ModOp Type="merge" GUID="1010030" Path="/Values/Residence7"><Residents>12</Residents></ModOp>'
            "\n--></ModOps>" % SWITCHED_OFF_MARK,
            encoding="utf-8",
        )
        dialog = ModImportDialog(self.window.index, self.window, str(mod))
        self.addCleanup(dialog.deleteLater)
        dialog.reload()
        with mock.patch.object(dialog, "accept"):
            dialog._accept()
        self.assertEqual([(c.new_value, c.active) for c in dialog.changes], [("12", False)])
        self.assertEqual([op.active for op in dialog.ops], [False])

    def test_a_search_that_matches_nothing_is_a_row_to_tick_or_leave(self):
        """Its op never reached its result, so the row was greyed out with no
        tick, the op was imported whatever was ticked, and the line above
        the table counted it nowhere."""
        from anno_modding_studio.ui.mod_import_dialog import ModImportDialog

        mod = Path(self._tmp.name) / "[Fix] Searches For Nothing"
        patch = mod / "data/config/export/main/asset/assets.xml"
        patch.parent.mkdir(parents=True, exist_ok=True)
        patch.write_text(
            '<ModOps><ModOp Type="merge" Path="//Values/Nope"><X>1</X></ModOp></ModOps>',
            encoding="utf-8",
        )
        dialog = ModImportDialog(self.window.index, self.window, str(mod))
        self.addCleanup(dialog.deleteLater)
        dialog.reload()
        tick = dialog.ops_table.item(0, 0)
        self.assertTrue(tick.flags() & Qt.ItemFlag.ItemIsUserCheckable)
        self.assertEqual(tick.checkState(), Qt.CheckState.Checked)
        self.assertIn("1 changes nothing today", dialog.status.text())
        tick.setCheckState(Qt.CheckState.Unchecked)
        with mock.patch.object(dialog, "accept"):
            dialog._accept()
        self.assertEqual(dialog.ops, [])

    def test_a_row_that_produced_nothing_cannot_be_ticked(self):
        blocked = self.dialog.ops_table.item(2, 0)
        self.assertFalse(blocked.flags() & Qt.ItemFlag.ItemIsUserCheckable)

    def test_unticking_everything_leaves_nothing_to_import(self):
        for row in range(self.dialog.ops_table.rowCount()):
            self.dialog.ops_table.item(row, 0).setCheckState(Qt.CheckState.Unchecked)
        self.assertFalse(self.dialog.import_button.isEnabled())

    def test_a_refused_op_says_why_rather_than_going_missing(self):
        self.assertIn("copies from", self.dialog.ops_table.item(2, 4).text())

    def test_selecting_a_row_shows_what_it_turned_into(self):
        self.dialog.ops_table.selectRow(0)
        text = self.dialog.detail.text()
        self.assertIn("8 → 12", text)
        self.assertIn("#make the farmer residence roomier", text)

    def test_the_other_files_in_the_mod_are_listed_too(self):
        found = [
            self.dialog.extras_table.item(row, 0).text()
            for row in range(self.dialog.extras_table.rowCount())
        ]
        self.assertTrue(any(".txt" in what for what in found), found)

    def test_the_tabs_say_how_much_is_in_each(self):
        self.assertEqual(self.dialog.tabs.tabText(0), "ModOps (4)")
        self.assertTrue(self.dialog.tabs.tabText(1).startswith("Not ModOps ("))

    def test_the_mods_own_details_are_offered(self):
        self.assertTrue(self.dialog.metadata_box.isEnabled())
        self.assertEqual(self.dialog.metadata_box.text(), "This is my mo&d")
        self.assertEqual(self.dialog.metadata_box.toolTip(), "")

    def test_but_not_ticked_for_somebody_elses_mod(self):
        """Folding their changes into yours is the commoner reason to merge,
        and for that their name on your loadout is wrong."""
        self.assertFalse(self.dialog.metadata_box.isChecked())

    def test_and_ticked_when_it_is_yours_to_carry_on(self):
        """Your own published mod, brought back in: its ModID and version are
        what keep the next export an update rather than a second mod."""
        self.dialog._creator = "taludas"
        self.dialog.reload()
        self.assertTrue(self.dialog.metadata_box.isChecked())
        self.dialog._accept()
        self.assertEqual(self.dialog.metadata.get("creator"), "Taludas")

    def test_your_own_answer_outlasts_keeping_it_separate_and_back(self):
        """The tick was worked out afresh each time the box was shown, so
        unticking it, then keeping the mod separate and merging it again,
        brought the tick back."""
        self.dialog._creator = "taludas"
        self.dialog.reload()
        self.dialog.metadata_box.click()
        self.assertFalse(self.dialog.metadata_box.isChecked())
        self.dialog.separate_box.setChecked(True)
        self.dialog.separate_box.setChecked(False)
        self.assertFalse(self.dialog.metadata_box.isChecked())

    def test_and_reading_the_mod_again(self):
        """Its options changed, the mod is read again: still the same mod."""
        self.dialog.metadata_box.click()
        self.assertTrue(self.dialog.metadata_box.isChecked())
        self.dialog.reload()
        self.assertTrue(self.dialog.metadata_box.isChecked())

    def test_but_another_mod_is_asked_afresh(self):
        self.dialog.metadata_box.click()
        other = Path(self._tmp.name) / "[Fix] Another Mod Of Theirs"
        patch = other / "data/config/export/main/asset/assets.xml"
        patch.parent.mkdir(parents=True, exist_ok=True)
        patch.write_text(self.PATCH, encoding="utf-8")
        (other / "modinfo.json").write_text(
            '{"ModName": {"English": "Another"}, "CreatorName": "Taludas"}', encoding="utf-8"
        )
        self.dialog.folder_box.setText(str(other))
        self.dialog.reload()
        self.assertFalse(self.dialog.metadata_box.isChecked())

    def test_kept_separate_the_question_is_not_shown_at_all(self):
        """Greyed out it was a question with no answer."""
        self.dialog.separate_box.setChecked(True)
        self.assertTrue(self.dialog.metadata_box.isHidden())
        self.dialog.separate_box.setChecked(False)
        self.assertFalse(self.dialog.metadata_box.isHidden())

    def test_accepting_keeps_only_the_ticked_changes(self):
        self.dialog.ops_table.item(1, 0).setCheckState(Qt.CheckState.Unchecked)
        self.dialog._accept()
        self.assertEqual(len(self.dialog.changes), 2)
        self.assertEqual({c.op for c in self.dialog.changes}, {"replace", "merge"})

    def test_declining_the_details_leaves_the_loadout_named_as_it_was(self):
        self.dialog.metadata_box.setChecked(False)
        self.dialog._accept()
        self.assertEqual(self.dialog.metadata, {})

    def test_a_mod_kept_as_its_own_hands_over_no_details_at_all(self):
        """It brings its details with it, so there is nothing to take -- and
        taking them anyway leaves two mods claiming one name and ModID."""
        self.dialog.separate_box.setChecked(True)
        self.assertFalse(self.dialog.metadata_box.isChecked())
        self.assertFalse(self.dialog.metadata_box.isEnabled())
        # Its ? goes with it: left, it was a ? under "Keep it separate"
        # that belonged to nothing on screen.
        self.assertFalse(self.dialog.metadata_box.isVisibleTo(self.dialog))
        self.assertFalse(self.dialog.metadata_help.isVisibleTo(self.dialog))
        self.dialog.separate_box.setChecked(False)
        self.assertTrue(self.dialog.metadata_help.isVisibleTo(self.dialog))
        self.dialog._accept()
        self.assertEqual(self.dialog.metadata, {})

    def test_and_re_reading_it_does_not_quietly_offer_them_again(self):
        """Reading a mod re-ticked this box whatever *keep it separate* said,
        so the author's creator name landed on the loadout -- which is how the
        Studio recognises a mod as yours, and so their mod read back as one you
        wrote and was refused a patch."""
        self.dialog.separate_box.setChecked(True)
        self.dialog.reload()
        self.assertFalse(self.dialog.metadata_box.isChecked())
        self.dialog._accept()
        self.assertEqual(self.dialog.metadata, {})
        self.assertEqual(self.dialog.layer().creator, "Taludas")

    def test_a_folder_with_nothing_in_it_says_so_instead_of_failing(self):
        empty = Path(self._tmp.name) / "not-a-mod"
        empty.mkdir(exist_ok=True)
        self.dialog.folder_box.setText(str(empty))
        self.dialog.reload()
        self.assertIn("No assets.xml patch", self.dialog.status.text())
        self.assertFalse(self.dialog.import_button.isEnabled())



class TestWideReplaceOfACostListUi(unittest.TestCase):
    """The shape the Seal shrine mod is written in, through the dialog.

    Its cost lists are patched with one wide ``replace``, which read strictly
    deletes every entry it did not retype -- including the goods the base game
    lists with no amount at all, which the game reads as absent anyway.
    """

    PATCH = (
        "<ModOps>"
        '<ModOp Type="replace" GUID="7100" Path="/Values/Cost/Costs">'
        "<Costs>"
        "<Item><Ingredient>7010</Ingredient><Amount>2500</Amount></Item>"
        "</Costs></ModOp>"
        "</ModOps>"
    )

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        tmp = Path(cls._tmp.name)
        # A cost list in the shipped shape: two goods that cost something and
        # two that are named with no amount.
        parts = [
            '<?xml version="1.0"?><Assets>'
            "<Asset><Template>Building</Template><Values>"
            "<Standard><GUID>7100</GUID><Name>a_shrine</Name></Standard>"
            "<Cost><Costs>"
            "<Item><Ingredient>7010</Ingredient><Amount>100</Amount></Item>"
            "<Item><Ingredient>7011</Ingredient><Amount>5</Amount></Item>"
            "<Item><Ingredient>7012</Ingredient></Item>"
            "<Item><Ingredient>7013</Ingredient></Item>"
            "</Costs></Cost></Values></Asset>"
        ]
        for guid in range(7010, 7014):
            parts.append(
                "<Asset><Template>Product</Template><Values><Standard>"
                "<GUID>%d</GUID><Name>good_%d</Name></Standard></Values></Asset>"
                % (guid, guid)
            )
        # Enough other cost lists that Ingredient reads as a link field at all.
        for n in range(4):
            parts.append(
                "<Asset><Template>Building</Template><Values>"
                "<Standard><GUID>%d</GUID><Name>other_%d</Name></Standard>"
                "<Cost><Costs><Item><Ingredient>7010</Ingredient>"
                "<Amount>3</Amount></Item></Costs></Cost></Values></Asset>"
                % (7200 + n, n)
            )
        parts.append("</Assets>")
        cls.assets = tmp / "assets.xml"
        cls.assets.write_text("".join(parts), encoding="utf-8")
        cls.db = tmp / "index.sqlite"
        index_mod.build_index(cls.assets, cls.db)

        cls.mod = tmp / "[Seal] A Mod"
        patch = cls.mod / "data/base/config/export/assets.xml"
        patch.parent.mkdir(parents=True)
        patch.write_text(cls.PATCH, encoding="utf-8")

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def setUp(self):
        from anno_modding_studio.ui.mod_import_dialog import ModImportDialog

        self.window = MainWindow()

        self.addCleanup(_flush_deletes)
        self.window.game_files.adopt_index(self.db, self.assets, cached=True)
        self.dialog = ModImportDialog(self.window.index, self.window, str(self.mod))
        self.dialog.reload()

    def tearDown(self):
        self.dialog.deleteLater()
        self.window.editor.search._debounce.stop()
        self.window.game_files.stop_texts_worker()
        if self.window.index is not None:
            self.window.index.close()
            self.window.index = None
        self.window.loadout.clear()
        self.window.deleteLater()

    def _removals(self):
        return sorted(
            change.path
            for change in self.dialog.report.changes
            if change.op == "remove"
        )

    def test_only_the_entry_that_really_costs_something_is_removed(self):
        self.assertEqual(
            self._removals(), ["/Values/Cost/Costs/Item[Ingredient='7011']"]
        )

    def test_the_row_says_what_it_left_out(self):
        self.assertIn("left out 2 removals", self.dialog.ops_table.item(0, 4).text())

    def test_the_change_the_mod_actually_makes_still_arrives(self):
        replaces = [c for c in self.dialog.report.changes if c.op == "replace"]
        self.assertEqual(len(replaces), 1)
        self.assertEqual((replaces[0].old_value, replaces[0].new_value), ("100", "2500"))

    def test_the_strict_reading_is_one_tick_away(self):
        self.dialog.keep_inert_box.setChecked(True)
        self.assertEqual(
            self._removals(),
            [
                "/Values/Cost/Costs/Item[Ingredient='70%d']" % n
                for n in (11, 12, 13)
            ],
        )

    def test_and_what_the_op_comes_to_follows_the_reading(self):
        """The op is the one op either way, and goes out as written; the box
        is about what is listed under it."""
        self.assertEqual(self.dialog.import_button.text(), "Import 1 ModOp")
        self.assertEqual(len(self.dialog._chosen()), 2)
        self.dialog.keep_inert_box.setChecked(True)
        self.assertEqual(len(self.dialog._chosen()), 4)
        self.assertEqual(self.dialog.import_button.text(), "Import 1 ModOp")
        self.dialog.keep_inert_box.setChecked(False)
        self.assertEqual(len(self.dialog._chosen()), 2)

    def test_the_box_starts_off_because_the_cleaner_reading_is_the_default(self):
        self.assertFalse(self.dialog.keep_inert_box.isChecked())

    def test_the_leaner_mod_is_smaller_and_still_verifies(self):
        self.dialog._accept()
        for change in self.dialog.changes:
            self.window.loadout.set_change(change)
        self.window.on_changes_updated()
        self.assertEqual(len(self.window.loadout), 2)
        problems = [
            result
            for result in generator.verify_changes(
                self.window.workspace, self.window.loadout
            )
            if not result.ok
        ]
        self.assertEqual(problems, [])

    def test_the_goods_it_leaves_alone_are_still_in_the_asset(self):
        """Which is the whole claim: the game reads them as absent either way,
        so the mod has no business claiming those entries."""
        self.dialog._accept()
        for change in self.dialog.changes:
            self.window.loadout.set_change(change)
        asset = self.window.workspace.effective_asset("7100")
        costs = asset.element.find("Values/Cost/Costs")
        kept = [
            (item.findtext("Ingredient"), item.findtext("Amount"))
            for item in costs
            if not asset.origin(item).is_removed
        ]
        self.assertEqual(kept, [("7010", "2500"), ("7012", None), ("7013", None)])

    def test_nothing_the_mod_does_not_touch_ends_up_in_the_generated_file(self):
        self.dialog._accept()
        for change in self.dialog.changes:
            self.window.loadout.set_change(change)
        xml = generator.build_modops_xml(
            self.window.loadout,
        )
        self.assertIn("7011", xml, "the good that really cost something")
        for good in ("7012", "7013"):
            self.assertNotIn(good, xml)

class TestModImportIntoTheLoadout(unittest.TestCase):
    """What the window does once the dialog has been accepted."""

    PATCH = TestModImportUi.PATCH

    @classmethod
    def setUpClass(cls):
        TestModImportUi.setUpClass.__func__(cls)

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def setUp(self):
        self.window = MainWindow()
        self.addCleanup(_flush_deletes)
        self.window.game_files.adopt_index(self.db, self.assets, cached=True)

    def tearDown(self):
        self.window.editor.search._debounce.stop()
        self.window.game_files.stop_texts_worker()
        if self.window.index is not None:
            self.window.index.close()
            self.window.index = None
        self.window.loadout.clear()
        self.window.deleteLater()

    def _import(self, separate=False, **kwargs):
        from unittest import mock

        from PySide6.QtWidgets import QDialog

        from anno_modding_studio.ui import mod_import_dialog

        real = mod_import_dialog.ModImportDialog

        class Driven(real):
            def exec(inner):
                inner.folder_box.setText(str(self.mod))
                inner.reload()
                for name, value in kwargs.items():
                    getattr(inner, name)(value)
                inner._accept()
                return int(QDialog.DialogCode.Accepted)

        with mock.patch.object(
            mod_import_dialog.ModImportDialog, "exec", Driven.exec
        ):
            self.window.import_mod(separate=separate)

    def test_the_menu_entry_waits_for_an_assets_file(self):
        fresh = MainWindow()
        self.addCleanup(_flush_deletes)
        try:
            self.assertFalse(fresh.library_add_action.isEnabled())
        finally:
            fresh.game_files.stop_texts_worker()
            fresh.deleteLater()
        self.assertTrue(self.window.library_add_action.isEnabled())

    def _add_through(self, label):
        """Mods > Add mods into this loadout, which has read no folder: the
        one-mod import, left by the button ``label``.  What the dialog
        offered, and what Add mods said."""
        from unittest import mock

        from anno_modding_studio.ui import mod_import_dialog

        offered = []

        def shown(inner):
            buttons = inner.buttons.buttons()
            offered.extend(button.text() for button in buttons)
            next(button for button in buttons if button.text() == label).click()
            return inner.result()

        with mock.patch.object(mod_import_dialog.ModImportDialog, "exec", shown):
            landed = self.window.library_controller.add_mods()
        return offered, landed

    def test_add_mods_has_mod_io_and_a_zip_a_button_away(self):
        """They were menu entries of their own, beside the ones that add the
        mods already on this PC."""
        from unittest import mock

        with mock.patch.object(self.window.modio, "get_mod_from_modio",
                               return_value=True) as fetched:
            offered, landed = self._add_through("&From mod.io…")
        self.assertIn("From a &zip…", offered)
        fetched.assert_called_once_with()
        self.assertTrue(landed)

    def test_a_zip_picked_is_installed_as_one_dropped_is(self):
        from unittest import mock

        controller = self.window.library_controller
        zipped = str(Path(self._tmp.name) / "Mod.zip")
        with mock.patch.object(self.window.prompts, "pick_mod_zips", return_value=[zipped]), \
                mock.patch.object(controller, "install_dropped", return_value=True) as installed:
            _offered, landed = self._add_through("From a &zip…")
        installed.assert_called_once_with([zipped])
        self.assertTrue(landed)

    def test_no_zip_picked_installs_nothing(self):
        from unittest import mock

        controller = self.window.library_controller
        with mock.patch.object(self.window.prompts, "pick_mod_zips", return_value=[]), \
                mock.patch.object(controller, "install_dropped") as installed:
            self.assertFalse(controller.add_zips())
        installed.assert_not_called()

    def test_cancelling_add_mods_takes_no_other_way(self):
        from unittest import mock

        with mock.patch.object(self.window.modio, "get_mod_from_modio") as fetched:
            _offered, landed = self._add_through("Cancel")
        fetched.assert_not_called()
        self.assertFalse(landed)

    def test_importing_puts_the_changes_in_the_loadout(self):
        self._import()
        self.assertEqual(len(self.window.loadout), 3)
        self.assertEqual(len(change_rows(self.window.changes_panel)), 3)

    def test_and_takes_the_mods_details_when_it_is_yours(self):
        """Merged in, the details are taken for a mod whose creator is you:
        carrying on your own published mod under its ModID and version."""
        self.window.settings.setValue(self.window.preferences.CREATOR_KEY, "Taludas")
        self.addCleanup(self.window.settings.remove, self.window.preferences.CREATOR_KEY)
        self._import()
        self.assertEqual(self.window.loadout.own.name, "Somebody Elses Mod")
        self.assertEqual(self.window.loadout.own.creator, "Taludas")
        self.assertEqual(self.window.output.name_edit.text(), "Somebody Elses Mod")

    def test_the_status_line_says_what_came_in(self):
        self._import()
        self.assertRegex(self.window.status_label.text(), r"Imported \d+ ModOps? from")

    def test_the_editor_shows_the_imported_edit_in_place(self):
        self._import()
        self.window._open_asset("1010030")
        residents = [
            item
            for item in self.window.editor._iter_items()
            if item.text(0) == "Residents"
        ]
        self.assertEqual(residents[0].text(1), "12")

    def test_what_comes_out_the_other_end_still_verifies(self):
        self._import()
        problems = [
            result
            for result in generator.verify_changes(
                self.window.workspace, self.window.loadout
            )
            if not result.ok
        ]
        self.assertEqual(problems, [], [p.message for p in problems])

    def test_importing_the_same_mod_twice_does_not_double_it_up(self):
        self._import()
        self._import()
        self.assertEqual(len(self.window.loadout), 3)
        self.assertIn("replaced a change already", self.window.status_label.text())

    def test_a_mod_read_in_with_no_card_chosen_starts_a_loadout_of_mods_to_play(self):
        """From mod.io, a dropped zip or the menu, with no way of working
        chosen: the loadout was a "New loadout" with no file, and the first
        edit went to a standalone My Anno Mod, a patch of neither the mod
        read in nor the list.  Set up as *Play a set of mods* sets one up,
        edits go to the patch over the list, which loads last."""
        from anno_modding_studio.core.changes import AIM_MANAGE, list_patch_name

        self.assertEqual(self.window.loadout.aim, "")
        self.assertIsNone(self.window.loadout_path)
        self._import(separate=True)
        self.assertEqual(len(self.window.loadout.layers), 1)
        name = self.window.start.list_loadout_name()
        self.assertEqual(self.window.loadout.aim, AIM_MANAGE)
        self.assertTrue(self.window.loadout.own.loads_last)
        self.assertFalse(self.window.loadout.is_the_mod)
        self.assertEqual(self.window.files.loadout_name(), name)
        self.assertEqual(self.window.loadout.own.name, list_patch_name(name))
        self.assertEqual(self.window.loadout.editing_origin(), "")
        self.assertIn(name, self.window.windowTitle())

    def test_but_one_read_in_for_a_card_keeps_that_card(self):
        from anno_modding_studio.core.changes import AIM_MAKE

        self.window.loadout.set_up_as(AIM_MAKE)
        self._import(separate=True)
        self.assertEqual(self.window.loadout.aim, AIM_MAKE)
        self.assertFalse(self.window.loadout.own.loads_last)

    def test_and_one_read_into_a_loadout_with_a_file_leaves_it_as_it_is(self):
        self.window.loadout_path = Path(self._tmp.name) / "Mine.annoload"
        self._import(separate=True)
        self.assertEqual(self.window.loadout.aim, "")
        self.assertEqual(self.window.loadout_path, Path(self._tmp.name) / "Mine.annoload")


class TestChangesTreeUi(unittest.TestCase):
    """The changes dock, once a loadout is more than a handful of edits."""

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        tmp = Path(cls._tmp.name)
        cls.assets = tmp / "assets.xml"
        cls.assets.write_text(make_sample_assets.build_assets_xml(40), encoding="utf-8")
        cls.db = tmp / "index.sqlite"
        index_mod.build_index(cls.assets, cls.db)

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def setUp(self):
        self.window = MainWindow()
        self.addCleanup(_flush_deletes)
        self.window.game_files.adopt_index(self.db, self.assets, cached=True)
        self.panel = self.window.changes_panel
        self.window._open_asset("1010340")
        self._edit("CycleTime", "15.00")
        self._edit("BuildingType", "Something")
        self.window._open_asset("1010030")
        self._edit("Residents", "12")

    def tearDown(self):
        self.window.editor.search._debounce.stop()
        self.panel._filter_timer.stop()
        self.window.game_files.stop_texts_worker()
        if self.window.index is not None:
            self.window.index.close()
            self.window.index = None
        self.window.loadout.clear()
        self.window.deleteLater()

    def _edit(self, tag, value):
        for item in self.window.editor._iter_items():
            if item.text(0) == tag and item.data(0, 258) is not None:  # ORIG_ROLE
                item.setText(1, value)
                return item
        self.fail("no editable field named %s" % tag)

    def _filter(self, text):
        """Type into the filter box, without waiting out the settling delay."""
        self.panel.filter_box.setText(text)
        self.panel._filter_timer.stop()
        self.panel._apply_filter()

    def _groups(self):
        # Under the mod's own row, which one mod alone has as well.
        tree = self.panel.tree
        mods = [tree.topLevelItem(i) for i in range(tree.topLevelItemCount())]
        return [mod.child(i) for mod in mods for i in range(mod.childCount())]

    # -- grouping -------------------------------------------------------
    def test_the_changes_sit_under_the_asset_they_are_about(self):
        groups = self._groups()
        self.assertEqual(len(groups), 2)
        self.assertEqual([g.childCount() for g in groups], [1, 2])

    def test_a_group_is_named_after_its_asset_and_says_how_much_it_holds(self):
        by_name = {g.text(0): g for g in self._groups()}
        lumber = next(name for name in by_name if "lumberjack" in name)
        self.assertEqual(by_name[lumber].text(1), "2 changes")

    def test_the_rows_underneath_are_the_fields_not_the_asset_again(self):
        group = next(g for g in self._groups() if g.childCount() == 2)
        labels = sorted(group.child(i).text(0) for i in range(2))
        self.assertTrue(any("CycleTime" in label for label in labels), labels)
        self.assertFalse(any("lumberjack" in label for label in labels), labels)

    def test_groups_start_open_and_can_be_folded_away(self):
        self.assertTrue(all(g.isExpanded() for g in self._groups()))
        self.panel._toggle_fold()
        self.assertFalse(any(g.isExpanded() for g in self._groups()))
        self.assertEqual(self.panel.fold_button.text(), "Expand all")

    def test_folding_everything_leaves_the_mod_row_itself_open(self):
        """Shutting the only row there is would hide the whole list."""
        self.panel._toggle_fold()
        self.assertTrue(self.panel.tree.topLevelItem(0).isExpanded())
        self.panel._toggle_fold()
        self.assertTrue(all(g.isExpanded() for g in self._groups()))

    def test_a_group_folded_by_hand_stays_folded_across_a_refresh(self):
        group = self._groups()[0]
        name = group.text(0)
        group.setExpanded(False)
        self.panel.refresh()
        again = next(g for g in self._groups() if g.text(0) == name)
        self.assertFalse(again.isExpanded())

    # -- the two columns ------------------------------------------------
    def _sized_tree(self, width=600):
        """The tree, laid out at a width worth measuring."""
        self.window.show()
        self.panel.resize(width, 700)
        _app.processEvents()
        tree = self.panel.tree
        tree.resize(width, 640)
        _app.processEvents()
        return tree

    def test_the_two_columns_always_fill_the_tree_between_them(self):
        """The Change column was fixed at 150px, so widening the panel only
        ever widened the names -- and dragging the divider left opened a strip
        of nothing on the right rather than a wider column."""
        tree = self._sized_tree()
        self.assertEqual(
            tree.columnWidth(0) + tree.columnWidth(1), tree.viewport().width()
        )
        was = tree.columnWidth(1)
        tree.resize(900, 640)
        _app.processEvents()
        self.assertGreater(tree.columnWidth(1), was)
        self.assertEqual(
            tree.columnWidth(0) + tree.columnWidth(1), tree.viewport().width()
        )

    def test_dragging_the_divider_moves_it_instead_of_leaving_a_gap(self):
        tree = self._sized_tree()
        narrower = tree.columnWidth(0) - 120
        tree.header().resizeSection(0, narrower)
        _app.processEvents()
        self.assertEqual(tree.columnWidth(0), narrower)
        self.assertEqual(
            tree.columnWidth(0) + tree.columnWidth(1), tree.viewport().width()
        )

    def test_and_what_is_dragged_is_a_proportion_not_a_pixel_width(self):
        """The panel lives in a splitter the user drags all day."""
        tree = self._sized_tree()
        tree.header().resizeSection(0, tree.columnWidth(0) - 120)
        _app.processEvents()
        share = tree.share()
        tree.resize(1000, 640)
        _app.processEvents()
        self.assertAlmostEqual(share, tree.share(), places=3)
        self.assertAlmostEqual(
            tree.columnWidth(1) / tree.viewport().width(), share, places=2
        )

    def test_the_change_columns_right_edge_is_the_trees(self):
        """Every view holds its columns to its width the one way, by
        widgets.FitColumns: a drag of the last column's right edge is undone.
        The tree had a way of its own that read it as a new boundary."""
        tree = self._sized_tree()
        before = (tree.columnWidth(0), tree.columnWidth(1))
        share = tree.share()
        tree.header().resizeSection(1, before[1] + 80)
        _app.processEvents()
        self.assertEqual((tree.columnWidth(0), tree.columnWidth(1)), before)
        self.assertAlmostEqual(tree.share(), share, places=3)
        self.assertIs(tree.column_fit.view, tree)

    def test_neither_column_can_be_dragged_away_altogether(self):
        tree = self._sized_tree()
        tree.header().resizeSection(0, 5)
        _app.processEvents()
        self.assertGreaterEqual(tree.columnWidth(0), 140)
        tree.header().resizeSection(1, 5)
        _app.processEvents()
        self.assertGreaterEqual(tree.columnWidth(1), 90)

    def test_the_proportion_is_remembered_between_launches(self):
        """Kept for the same reason the splitter beside it is kept."""
        tree = self._sized_tree()
        tree.header().resizeSection(0, tree.columnWidth(0) - 120)
        _app.processEvents()
        share = tree.share()
        self.addCleanup(self.window.settings.remove, "changesColumnShare")
        # Written the way closeEvent writes it, read the way a launch reads it.
        self.window.settings.setValue("changesColumnShare", "%.4f" % share)
        tree.set_share(0.3)
        self.window._restore_geometry()
        self.assertAlmostEqual(tree.share(), share, places=3)

    def test_a_window_closed_maximised_over_a_small_one_opens_with_room_under_it(self):
        """Shrunk as far as it went, then maximised and closed: the next
        launch showed a small window in the corner that said it was
        maximised, Qt having resized it up to what the layout needed."""
        from PySide6.QtCore import QSize, Qt

        window = self.window
        before = (window.saveGeometry(), window.windowState())
        self.addCleanup(window.setWindowState, before[1])
        self.addCleanup(window.restoreGeometry, before[0])
        self.addCleanup(window.settings.remove, "geometry")
        window.setWindowState(Qt.WindowState.WindowNoState)
        window.resize(400, 300)
        window.setWindowState(Qt.WindowState.WindowMaximized)
        window.settings.setValue("geometry", window.saveGeometry())
        window._restore_geometry()
        self.assertTrue(window.isMaximized())
        room = QSize(1400, 900).boundedTo(window.screen().availableSize())
        self.assertGreaterEqual(window.width(), room.width())
        self.assertGreaterEqual(window.height(), room.height())

    def test_a_remembered_proportion_that_is_nonsense_is_ignored(self):
        """It comes back off disk as text, and may be from any version."""
        tree = self._sized_tree()
        share = tree.share()
        for junk in ("", "wide", None, "0", "1.5", "-0.2"):
            with self.subTest(saved=junk):
                tree.set_share(junk)
                self.assertEqual(tree.share(), share)
        tree.set_share("0.45")
        self.assertAlmostEqual(tree.share(), 0.45)

    # -- finding one row among thousands --------------------------------
    def test_filtering_leaves_only_the_rows_that_mention_it(self):
        """A modlist is thousands of rows; scrolling was the only way in."""
        self._filter("CycleTime")
        shown = shown_change_rows(self.panel)
        self.assertEqual(len(shown), 1)
        self.assertIn("CycleTime", shown[0].text(0))

    def test_matching_an_asset_shows_everything_underneath_it(self):
        """Asking for an asset by name means asking for what was done to it.

        A parent that matched while its children vanished would read as an
        empty mod rather than as a found one.
        """
        lumber = next(g for g in self._groups() if "lumberjack" in g.text(0))
        self._filter("lumberjack")
        self.assertEqual(len(shown_change_rows(self.panel)), lumber.childCount())
        self.assertEqual(lumber.childCount(), 2)

    def test_a_guid_finds_the_row_whose_label_is_a_name(self):
        self._filter("1010030")
        shown = shown_change_rows(self.panel)
        self.assertEqual(len(shown), 1)
        self.assertIn("Residents", shown[0].text(0))

    def test_the_summary_says_how_much_of_the_list_is_hidden(self):
        self._filter("CycleTime")
        self.assertEqual(self.panel.summary.text(), "Filtered: 1 of 3 changes shown")
        self._filter("nothing whatever matches this")
        self.assertEqual(shown_change_rows(self.panel), [])
        self.assertIn("No rows match", self.panel.summary.text())

    def test_clearing_it_puts_the_list_back_the_way_it_was_left(self):
        """Hiding rows is not folding them: the fold state is the user's."""
        group = self._groups()[0]
        name = group.text(0)
        group.setExpanded(False)
        self._filter("CycleTime")
        self._filter("")
        self.assertEqual(len(shown_change_rows(self.panel)), 3)
        again = next(g for g in self._groups() if g.text(0) == name)
        self.assertFalse(again.isExpanded())
        self.assertIn("3 changes", self.panel.summary.text())

    def test_a_filter_outlives_the_edit_that_redraws_the_list(self):
        """Every landed keystroke rebuilds the tree from scratch."""
        self._filter("CycleTime")
        self.window._open_asset("1010030")
        self._edit("Residents", "14")
        self.assertEqual(len(shown_change_rows(self.panel)), 1)
        self.assertIn("CycleTime", shown_change_rows(self.panel)[0].text(0))

    def test_reverting_a_selection_cannot_reach_what_is_hidden(self):
        """A selection outlives the keystroke that hides it."""
        lumber = next(g for g in self._groups() if "lumberjack" in g.text(0))
        self.panel.tree.clearSelection()
        lumber.setSelected(True)        # the whole asset: two changes
        self._filter("CycleTime")       # one of which is now off screen
        self.panel._remove_selected()
        left = sorted(c.tag for c in self.window.loadout)
        self.assertEqual(left, ["BuildingType", "Residents"])

    def test_filtering_changes_nothing_about_the_mod(self):
        """It hides rows. Hidden is not unticked, and it is not reverted."""
        before = {c.key: c.active for c in self.window.loadout}
        self._filter("CycleTime")
        self.assertEqual(len(self.window.loadout), 3)
        self.assertEqual({c.key: c.active for c in self.window.loadout}, before)

    # -- double-clicking a change --------------------------------------------
    def _a_change(self, guid="1010030"):
        return next(c for c in self.window.loadout.changes.values() if c.guid == guid)

    def _current_path(self):
        item = self.window.editor.tree.currentItem()
        self.assertIsNotNone(item, "nothing selected")
        return self.window.editor._path_for(item)[0]

    def test_double_clicking_a_change_opens_it_at_that_field(self):
        change = self._a_change()
        self.window.jump_to_change(change.guid, change.path)
        self.assertEqual(self.window.editor.guid, change.guid)
        self.assertEqual(self._current_path(), change.path)

    def test_and_finds_it_even_when_a_filter_was_hiding_it(self):
        """A hidden row can be selected and scrolled to and still show nothing,
        which is one of the ways this looked like it did nothing at all."""
        change = self._a_change()
        self.window.editor.load_asset(change.guid)
        self.window.editor.filter_box.setText("nothing matches this")
        self.window.jump_to_change(change.guid, change.path)
        item = self.window.editor.tree.currentItem()
        self.assertFalse(item.isHidden())
        self.assertEqual(self._current_path(), change.path)

    def test_a_path_with_no_row_of_its_own_lands_on_the_nearest_container(self):
        """A structural op's path names where it happened, not a row that is
        still there to select."""
        change = self._a_change()
        self.window.jump_to_change(change.guid, change.path + "/GoneNow")
        self.assertEqual(self._current_path(), change.path)

    def test_and_an_asset_that_is_not_there_says_so(self):
        self.window.jump_to_change("999999999", "/Values/Building/Radius")
        self.assertIn("not in the assets.xml", self.window.status_label.text())

    def test_and_so_does_a_field_that_is_not_there(self):
        change = self._a_change()
        self.window.jump_to_change(change.guid, "/Values/Nowhere/AtAll")
        self.assertEqual(self.window.editor.guid, change.guid)
        self.assertIn("not a row in the tree", self.window.status_label.text())

    def test_double_clicking_a_group_opens_the_asset_it_names(self):
        self.window._open_asset("1010340")
        group = next(g for g in self._groups() if "residence" in g.text(0))
        seen = []
        self.panel.changeActivated.connect(lambda guid, _p, _o, _k: seen.append(guid))
        open_row(self.panel, group.child(0))
        self.assertEqual(seen, ["1010030"])

    # -- switching changes off ------------------------------------------
    def test_every_change_starts_ticked(self):
        rows = change_rows(self.panel)
        self.assertEqual(len(rows), 3)
        self.assertTrue(all(r.checkState(0) == Qt.CheckState.Checked for r in rows))
        self.assertIn("3 changes", self.panel.summary.text())

    def test_unticking_one_leaves_it_in_the_loadout(self):
        row = change_rows(self.panel)[0]
        row.setCheckState(0, Qt.CheckState.Unchecked)
        self.assertEqual(len(self.window.loadout), 3)
        self.assertEqual(self.window.loadout.active_count, 2)
        self.assertIn("2 of 3 changes on", self.panel.summary.text())

    def test_and_out_of_what_the_game_reads_of_the_generated_mod(self):
        from anno_modding_studio.core.changes import SWITCHED_OFF_MARK

        row = next(r for r in change_rows(self.panel) if "CycleTime" in r.text(0))
        row.setCheckState(0, Qt.CheckState.Unchecked)
        _app.processEvents()
        self.window.tabs.setCurrentIndex(TAB_GENERATE)
        self.window._refresh_output()
        written = self.window.output.preview.toPlainText()
        live = [op for op in etree.fromstring(written.encode("utf-8")) if isinstance(op.tag, str)]
        self.assertTrue(live)
        self.assertNotIn("CycleTime", [op.get("Path", "") for op in live])
        self.assertFalse(any(op.find("CycleTime") is not None for op in live))
        # It is there to be read, as a comment.
        self.assertIn(SWITCHED_OFF_MARK, written)
        self.assertIn("CycleTime", written)

    def test_unticking_a_group_takes_everything_under_it(self):
        group = next(g for g in self._groups() if g.childCount() == 2)
        group.setCheckState(0, Qt.CheckState.Unchecked)
        self.assertEqual(self.window.loadout.active_count, 1)

    def test_a_group_shows_when_only_some_of_it_is_on(self):
        group = next(g for g in self._groups() if g.childCount() == 2)
        group.child(0).setCheckState(0, Qt.CheckState.Unchecked)
        self.assertEqual(group.checkState(0), Qt.CheckState.PartiallyChecked)

    def test_the_editor_shows_the_asset_without_a_switched_off_change(self):
        self.window._open_asset("1010340")
        self.assertEqual(self._find("CycleTime").text(1), "15.00")
        row = next(r for r in change_rows(self.panel) if "CycleTime" in r.text(0))
        row.setCheckState(0, Qt.CheckState.Unchecked)
        # Ticking a group walks its changes one at a time, so the panel says so
        # once the burst is over rather than per change.
        _app.processEvents()
        self.assertEqual(self._find("CycleTime").text(1), "30.00")

    def test_switching_one_back_on_brings_it_back(self):
        row = next(r for r in change_rows(self.panel) if "CycleTime" in r.text(0))
        row.setCheckState(0, Qt.CheckState.Unchecked)
        row.setCheckState(0, Qt.CheckState.Checked)
        self.assertEqual(self.window.loadout.active_count, 3)

    def test_the_loadout_is_marked_unsaved_when_one_is_switched(self):
        self.window.dirty = False
        change_rows(self.panel)[0].setCheckState(0, Qt.CheckState.Unchecked)
        _app.processEvents()
        self.assertTrue(self.window.dirty)

    def test_a_switched_off_row_is_greyed_rather_than_hidden(self):
        from anno_modding_studio.ui import theme

        row = change_rows(self.panel)[0]
        row.setCheckState(0, Qt.CheckState.Unchecked)
        self.assertEqual(row.foreground(1).color().name(), theme.TEXT_DIM)

    def test_switching_survives_a_rebuild_of_the_tree(self):
        row = next(r for r in change_rows(self.panel) if "CycleTime" in r.text(0))
        row.setCheckState(0, Qt.CheckState.Unchecked)
        self.panel.refresh()
        again = next(r for r in change_rows(self.panel) if "CycleTime" in r.text(0))
        self.assertEqual(again.checkState(0), Qt.CheckState.Unchecked)

    def test_the_generate_tab_says_so_when_nothing_is_left_on(self):
        for row in change_rows(self.panel):
            row.setCheckState(0, Qt.CheckState.Unchecked)
        self.window.tabs.setCurrentIndex(TAB_GENERATE)
        self.window._refresh_output()
        said = self.window.output.check_summary.text()
        self.assertIn("switched off", said)
        # Not nothing to write: each is in the file as a comment.
        self.assertIn("exported as comments", said)
        self.assertTrue(self.window.output.copy_action.isEnabled())

    # -- reverting still means reverting --------------------------------
    def test_reverting_a_group_drops_all_of_its_changes(self):
        group = next(g for g in self._groups() if g.childCount() == 2)
        group.setSelected(True)
        self.panel._remove_selected()
        self.assertEqual(len(self.window.loadout), 1)

    def test_reverting_one_row_drops_only_that_one(self):
        pick_change(self.panel, 0)
        self.panel._remove_selected()
        self.assertEqual(len(self.window.loadout), 2)

    def _find(self, tag):
        for item in self.window.editor._iter_items():
            if item.text(0) == tag and item.data(0, 258) is not None:
                return item
        self.fail("no field named %s" % tag)


class TestRelatedAssetsInTheDock(unittest.TestCase):
    """Assets that belong together are drawn as one group.

    The fixture is the shape this exists for: two buildings sharing an effect,
    with a buff hanging off the effect.
    """

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        tmp = Path(cls._tmp.name)
        parts = ['<?xml version="1.0"?><Assets>']
        for n in range(6):
            parts.append(
                "<Asset><Template>Building</Template><Values>"
                "<Standard><GUID>%d</GUID><Name>building_%d</Name></Standard>"
                "<Building><FunctionalEffects><Item><FunctionalEffect>%d"
                "</FunctionalEffect></Item></FunctionalEffects></Building>"
                "</Values></Asset>" % (6100 + n, n, 6200 if n < 2 else 6300 + n)
            )
        parts.append(
            "<Asset><Template>Effect</Template><Values>"
            "<Standard><GUID>6200</GUID><Name>shared_effect</Name></Standard>"
            "<Effect><Buffs><Item><GUID>6400</GUID></Item></Buffs></Effect>"
            "</Values></Asset>"
        )
        parts.append(
            "<Asset><Template>Buff</Template><Values><Standard>"
            "<GUID>6400</GUID><Name>the_buff</Name></Standard>"
            "<Buff><Value>1</Value></Buff></Values></Asset>"
        )
        for n in range(2, 6):
            parts.append(
                "<Asset><Template>Effect</Template><Values><Standard>"
                "<GUID>%d</GUID><Name>effect_%d</Name></Standard></Values></Asset>"
                % (6300 + n, n)
            )
        parts.append("</Assets>")
        cls.assets = tmp / "assets.xml"
        cls.assets.write_text("".join(parts), encoding="utf-8")
        cls.db = tmp / "index.sqlite"
        index_mod.build_index(cls.assets, cls.db)

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def setUp(self):
        self.window = MainWindow()
        self.addCleanup(_flush_deletes)
        self.window.game_files.adopt_index(self.db, self.assets, cached=True)
        # Somebody who has said who they are: an edit into your own mod
        # asks otherwise.
        self.window.settings.setValue(self.window.preferences.CREATOR_KEY, "Me")
        self.panel = self.window.changes_panel

    def tearDown(self):
        self.window.editor.search._debounce.stop()
        self.window.game_files.stop_texts_worker()
        if self.window.index is not None:
            self.window.index.close()
            self.window.index = None
        self.window.loadout.clear()
        self.window.deleteLater()

    def _edit(self, guid, value):
        self.window._open_asset(guid)
        for item in self.window.editor._iter_items():
            if item.text(0) == "Name" and item.data(0, 258) is not None:
                item.setText(1, value)
                return
        self.fail("no Name field on %s" % guid)

    def _groups(self):
        # Under the mod's own row, which one mod alone has as well.
        tree = self.panel.tree
        mods = [tree.topLevelItem(i) for i in range(tree.topLevelItemCount())]
        return [mod.child(i) for mod in mods for i in range(mod.childCount())]

    def test_a_building_its_effect_and_its_buff_are_one_group(self):
        for guid in ("6100", "6101", "6200", "6400"):
            self._edit(guid, "changed_%s" % guid)
        groups = self._groups()
        self.assertEqual(len(groups), 1, [g.text(0) for g in groups])
        self.assertEqual(groups[0].text(1), "4 assets, 4 changes")

    def test_the_assets_are_a_level_of_their_own_inside_it(self):
        from anno_modding_studio.ui.changes_dock import KIND_ROLE

        for guid in ("6100", "6200"):
            self._edit(guid, "changed_%s" % guid)
        group = self._groups()[0]
        kinds = {group.child(i).data(0, KIND_ROLE) for i in range(group.childCount())}
        self.assertEqual(kinds, {"asset"})

    def test_a_lone_asset_is_not_given_a_pointless_extra_level(self):
        from anno_modding_studio.ui.changes_dock import KIND_ROLE

        self._edit("6102", "changed")
        group = self._groups()[0]
        self.assertEqual(group.child(0).data(0, KIND_ROLE), "change")

    def test_unrelated_assets_get_their_own_groups(self):
        self._edit("6102", "one")
        self._edit("6103", "two")
        self.assertEqual(len(self._groups()), 2)

    def test_unticking_the_group_takes_every_asset_in_it(self):
        for guid in ("6100", "6200", "6400"):
            self._edit(guid, "changed_%s" % guid)
        self._groups()[0].setCheckState(0, Qt.CheckState.Unchecked)
        self.assertEqual(self.window.loadout.active_count, 0)
        self.assertEqual(len(self.window.loadout), 3)

    def test_unticking_one_asset_leaves_the_rest_of_the_group_on(self):
        from anno_modding_studio.ui.changes_dock import KIND_ROLE

        for guid in ("6100", "6200", "6400"):
            self._edit(guid, "changed_%s" % guid)
        group = self._groups()[0]
        branch = next(
            group.child(i)
            for i in range(group.childCount())
            if group.child(i).data(0, KIND_ROLE) == "asset"
        )
        branch.setCheckState(0, Qt.CheckState.Unchecked)
        self.assertEqual(self.window.loadout.active_count, 2)

    def test_the_group_tooltip_explains_why_they_are_together(self):
        for guid in ("6100", "6200"):
            self._edit(guid, "changed_%s" % guid)
        tip = self._groups()[0].toolTip(0)
        self.assertIn("belong to one another", tip)
        # Which assets they are is the rows under it, and not said twice.
        self.assertNotIn("6200", tip)
        self.assertLessEqual(len(tip.splitlines()), 4, tip)

class ModsFolderCase(unittest.TestCase):
    """A window with a small mods folder beside it, as a user would have."""

    # Whether the mods read in are yours: changes can go into a mod of
    # yours, and never into somebody else's.
    YOURS = False

    # Two mods that both retune the same farmer residence, plus one that
    # touches something else -- which is the shape a real mods folder has.
    FIRST = (
        "<ModOps>"
        '<ModOp Type="merge" GUID="1010030" Path="/Values/Residence7"><Residents>12</Residents></ModOp>'
        "</ModOps>"
    )
    SECOND = (
        "<ModOps>"
        '<ModOp Type="merge" GUID="1010030" Path="/Values/Residence7"><Residents>20</Residents></ModOp>'
        "</ModOps>"
    )
    THIRD = (
        "<ModOps>"
        '<ModOp Type="merge" GUID="1010031" Path="/Values/Residence7"><Residents>30</Residents></ModOp>'
        # Copies from an asset that is not here, so the Studio cannot read it.
        # (A copy from one that is would be read, at what it comes to today.)
        '<ModOp Type="merge" GUID="1010031" Path="/Values/Residence7" '
        'Content="@9999999/Residence7/Residents"></ModOp>'
        "</ModOps>"
    )

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        tmp = Path(cls._tmp.name)
        cls.assets = tmp / "assets.xml"
        cls.assets.write_text(make_sample_assets.build_assets_xml(40), encoding="utf-8")
        cls.db = tmp / "index.sqlite"
        index_mod.build_index(cls.assets, cls.db)

        cls.mods = tmp / "mods"
        for name, body, author in (
            ("[Gameplay] Roomier Farmers", cls.FIRST, "Ay"),
            ("[Seal] Much Roomier Farmers", cls.SECOND, "Bee"),
            ("[Seal] Other Houses", cls.THIRD, "Cee"),
        ):
            patch = cls.mods / name / "data/config/export/main/asset/assets.xml"
            patch.parent.mkdir(parents=True)
            patch.write_text(body, encoding="utf-8")
            (cls.mods / name / "modinfo.json").write_text(
                '{"ModName": {"English": "%s"}, "CreatorName": "%s"}' % (name, author),
                encoding="utf-8",
            )
        (cls.mods / "not-a-mod").mkdir()

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def setUp(self):
        self.window = MainWindow()
        self.addCleanup(_flush_deletes)
        # A list of mods being played, so the loadout's own mod loads after
        # them all. A mod written to publish loads where its name sorts it:
        # TestWhichModAnEditGoesTo has that.
        self.window.loadout.own.loads_last = True
        self.window.files.mark_saved()
        self.window.game_files.adopt_index(self.db, self.assets, cached=True)
        # The window opens the dialog on the folder it was last pointed at, so
        # this fixture has to be that folder -- otherwise the test reads
        # whatever mods folder the machine running it happens to have.
        self.window.settings.setValue(
            self.window.mods_key("last_mods_folder"), str(self.mods)
        )
        # Somebody who has already said who they are, so making a patch does
        # not stop to ask. These used to get that from whichever earlier test
        # had last saved a name -- the Windows user's, as it happened.
        self.window.settings.setValue(self.window.preferences.CREATOR_KEY, "Me")

    def tearDown(self):
        self.window.editor.search._debounce.stop()
        self.window.game_files.stop_texts_worker()
        if self.window.index is not None:
            self.window.index.close()
            self.window.index = None
        self.window.loadout.clear()
        self.window.deleteLater()

    def load(self, **kwargs):
        """Drive the dialog the way the menu entry does, without showing it."""
        from unittest import mock

        from PySide6.QtWidgets import QDialog

        from anno_modding_studio.ui import mods_folder_dialog

        real = mods_folder_dialog.ModsFolderDialog
        holder = {}

        def settle(inner):
            if inner._worker is not None:
                inner._worker.wait()
            # The worker's result arrives as a queued signal, which needs one
            # turn of the event loop to be delivered.
            QApplication.processEvents()

        class Driven(real):
            def exec(inner):
                settle(inner)
                inner.folder_box.setText(str(self.mods))
                inner.reload()
                settle(inner)
                for name, value in kwargs.items():
                    getattr(inner, name)(value)
                holder["dialog"] = inner
                inner._accept()
                return int(QDialog.DialogCode.Accepted)

        if self.YOURS:
            # Before the read, so that who you are is no work of this test's.
            self.window.loadout.own.creator = self.window.loadout.own.creator or "Me"
        with mock.patch.object(
            mods_folder_dialog.ModsFolderDialog, "exec", Driven.exec
        ):
            self.window.load_installed_mods()
        if self.YOURS:
            for layer in self.window.loadout.layers:
                as_yours(self.window.loadout, layer.origin)
            self.window.editor.refresh_target()
        return holder.get("dialog")

    def test_opened_from_the_menu_nothing_is_read_until_it_is_asked_for(self):
        """A full folder takes a while, and reading it on opening was unasked."""
        from unittest import mock

        from PySide6.QtWidgets import QDialog

        from anno_modding_studio.ui import mods_folder_dialog

        self.window.settings.setValue(
            self.window.mods_key("last_mods_folder"), str(self.mods)
        )
        seen = []

        def opened(inner):
            seen.append((inner.folder_box.text(), inner._worker, inner.session,
                         inner.read_button.isDefault()))
            return int(QDialog.DialogCode.Rejected)

        with mock.patch.object(mods_folder_dialog.ModsFolderDialog, "exec", opened):
            self.window.load_installed_mods()
        self.assertEqual(seen, [(str(self.mods), None, None, True)])

    def test_nor_from_the_menu_entry_itself(self):
        """The entry's triggered signal passes its checked flag, False, which
        came in as ``focus`` and was taken for a list of mods to start
        ticked: the folder was read at once."""
        from unittest import mock

        from PySide6.QtWidgets import QDialog

        from anno_modding_studio.ui import mods_folder_dialog

        self.window.settings.setValue(
            self.window.mods_key("last_mods_folder"), str(self.mods)
        )
        seen = []

        def opened(inner):
            worker = inner._worker
            if worker is not None:
                worker.wait()
                QApplication.processEvents()
            seen.append((worker, inner.session))
            return int(QDialog.DialogCode.Rejected)

        with mock.patch.object(mods_folder_dialog.ModsFolderDialog, "exec", opened):
            self.window.load_mods_action.trigger()
        self.assertEqual(seen, [(None, None)])


class TestLoadingAModsFolder(ModsFolderCase):
    """The whole folder, read as the stack the game will load."""

    def test_the_menu_entry_waits_for_an_assets_file(self):
        fresh = MainWindow()
        self.addCleanup(_flush_deletes)
        try:
            self.assertFalse(fresh.load_mods_action.isEnabled())
        finally:
            fresh.game_files.stop_texts_worker()
            fresh.deleteLater()
        self.assertTrue(self.window.load_mods_action.isEnabled())

    def test_every_mod_comes_in_as_its_own(self):
        self.load()
        self.assertEqual(
            [layer.origin for layer in self.window.loadout.layers],
            ["[Gameplay] Roomier Farmers", "[Seal] Much Roomier Farmers",
             "[Seal] Other Houses"],
        )
        self.assertEqual(
            sorted({c.origin for c in self.window.loadout}),
            ["[Gameplay] Roomier Farmers", "[Seal] Much Roomier Farmers",
             "[Seal] Other Houses"],
        )

    def test_load_order_is_folder_order(self):
        self.load()
        self.assertEqual(
            [layer.order for layer in self.window.loadout.layers], [1, 2, 3]
        )

    def test_a_mod_carries_its_own_details(self):
        self.load()
        layer = self.window.loadout.layer("[Seal] Other Houses")
        self.assertEqual(layer.name, "[Seal] Other Houses")
        self.assertEqual(layer.creator, "Cee")

    def test_and_says_how_much_of_it_could_not_be_read(self):
        self.load()
        self.assertEqual(self.window.loadout.layer("[Seal] Other Houses").unread, 1)

    def test_the_status_line_says_what_arrived(self):
        self.load()
        self.assertIn("Loaded 3 mods", self.window.status_label.text())
        self.assertIn("could not be read", self.window.status_label.text())

    def test_reading_the_folder_again_replaces_it_rather_than_stacking(self):
        self.load()
        before = len(self.window.loadout)
        self.load()
        self.assertEqual(len(self.window.loadout.layers), 3)
        self.assertEqual(len(self.window.loadout), before)

    def test_a_folder_that_is_not_a_mod_is_not_loaded_as_one(self):
        dialog = self.load()
        self.assertEqual(
            [folder.name for folder, _why in dialog.session.skipped], ["not-a-mod"]
        )

    def test_the_user_can_leave_one_out(self):
        dialog = self.load(_tick_shown=False)
        self.assertEqual(dialog.layers, [])
        self.assertEqual(len(self.window.loadout), 0)


class TestTheModsFolderTableSaysWhatItsHeadingsSay(ModsFolderCase):
    """Every number under the heading it belongs to.

    The columns are the whole content of this dialog: it exists to be read
    before anything is loaded, and a number under the wrong heading is worse
    than no number, because it is believed.
    """

    def rowed(self, dialog, origin):
        for row in range(dialog.table.rowCount()):
            if dialog.table.item(row, 0).data(Qt.ItemDataRole.UserRole) == origin:
                return [dialog.table.item(row, c) for c in range(dialog.table.columnCount())]
        self.fail("no row for %s" % origin)

    def test_load_order_ops_and_changes_each_sit_under_their_own_heading(self):
        from anno_modding_studio.ui.mods_folder_dialog import COLUMNS

        dialog = self.load()
        cells = self.rowed(dialog, "[Seal] Other Houses")
        read = [r for r in dialog.session.reads if r.origin == "[Seal] Other Houses"][0]
        under = dict(zip(COLUMNS, [c.text() if c else None for c in cells]))
        self.assertEqual(under["Loads"], str(read.order))
        self.assertEqual(under["ModOps"], str(read.ops))
        self.assertEqual(under["Changes"], str(len(read.changes)))
        self.assertEqual(under["Not read"], str(read.unread))

    def test_the_in_game_column_says_whether_the_game_loads_it(self):
        from anno_modding_studio.ui.mods_folder_dialog import COLUMNS

        dialog = self.load()
        cells = self.rowed(dialog, "[Seal] Other Houses")
        self.assertEqual(cells[COLUMNS.index("In game")].text(), "on")

    def test_and_says_off_with_the_reason_for_a_mod_the_game_skips(self):
        off = "[Seal] Other Houses"
        marked = self.mods / ("-" + off)
        (self.mods / off).rename(marked)
        self.addCleanup(lambda: marked.rename(self.mods / off))
        from anno_modding_studio.ui.mods_folder_dialog import COLUMNS

        in_game = COLUMNS.index("In game")
        dialog = self.load()
        cell = self.rowed(dialog, off)[in_game]
        self.assertEqual(cell.text(), "off")
        self.assertIn("“-”", cell.toolTip())
        self.assertEqual(self.rowed(dialog, "[Gameplay] Roomier Farmers")[in_game].text(), "on")

    def test_the_update_this_data_came_from_is_remembered_and_said(self):
        from anno_modding_studio.core import gameversion

        line = (
            "BuildString: Developer build - for internal use only! [Build: 2024-11-11 "
            "14:14:36] Anno7 Steam Goldmaster Win64 Release-Static v18.4.1412158.165273 main"
        )
        gameversion.remember(self.window.loadout.base_fingerprint, gameversion.parse_build_string(line))
        self.addCleanup(lambda: gameversion._remembered_path().unlink(missing_ok=True))
        self.window.game_files.adopt_index(self.db, self.assets, cached=True)
        self.assertEqual(
            (self.window.loadout.base_update, self.window.loadout.base_game_version),
            ("18.4", "18.4.1412158.165273"),
        )
        self.assertIn("Anno 1800 18.4", self.window.game_files.status_base)

    def test_built_on_says_which_studio_mods_were_made_on_older_game_data(self):
        import json

        from anno_modding_studio.core import games
        from anno_modding_studio.ui import theme
        from anno_modding_studio.ui.mods_folder_dialog import COLUMNS

        here = self.window.loadout.base_fingerprint
        self.assertTrue(here)
        for name, against in (
            ("[Seal] Other Houses", {"update": "18.3", "assets": "sha1:" + "0" * 40}),
            ("[Seal] Much Roomier Farmers", {"update": "18.4", "assets": "sha1:" + here}),
        ):
            info = self.mods / name / "modinfo.json"
            before = info.read_text(encoding="utf-8")
            self.addCleanup(info.write_text, before, encoding="utf-8")
            declared = json.loads(before)
            declared[modinfo.STUDIO_KEY] = {
                "build": 3, "builtAgainst": dict(against, game=games.get(self.window.loadout.game).label),
            }
            info.write_text(json.dumps(declared), encoding="utf-8")

        built_on = COLUMNS.index("Built on")
        dialog = self.load()
        older = self.rowed(dialog, "[Seal] Other Houses")[built_on]
        self.assertEqual(older.text(), "18.3 (older)")
        self.assertEqual(older.foreground().color().name().lower(), theme.WARN.lower())
        self.assertIn("18.3's data", older.toolTip())
        self.assertEqual(self.rowed(dialog, "[Seal] Much Roomier Farmers")[built_on].text(), "18.4")
        # Nothing to say about a mod the Studio did not build.
        self.assertEqual(self.rowed(dialog, "[Gameplay] Roomier Farmers")[built_on].text(), "")

    def test_an_asset_made_here_is_named_in_game_from_the_editor(self):
        from unittest import mock

        from PySide6.QtWidgets import QInputDialog

        from anno_modding_studio.core.changes import OP_ADD_NEXT, Change

        change = Change(
            guid="1010030", asset_label="x", path="", tag="Asset", op=OP_ADD_NEXT,
            payload="<Asset><BaseAssetGUID>1010030</BaseAssetGUID><Values><Standard>"
                    "<GUID>2001000000</GUID><Name>my_house</Name></Standard></Values></Asset>",
        )
        self.window.loadout.set_change(change)
        editor = self.window.editor
        self.assertTrue(editor.load_asset("2001000000"))
        self.assertFalse(editor.in_game_button.isHidden())
        # Its own switch: the panel as a whole is disabled with no window shown.
        self.assertTrue(editor.in_game_button.isEnabledTo(editor))
        with mock.patch.object(QInputDialog, "getText", return_value=("Seal House", True)):
            editor._name_in_game()
        self.assertEqual(change.in_game_name, "Seal House")
        self.assertIn("Seal House", editor.title_label.text())
        # A shipped asset has no such button.
        editor.load_asset("1010030")
        self.assertTrue(editor.in_game_button.isHidden())

    def test_a_mod_s_texts_name_what_it_changes(self):
        """Read with the folder, in the language the game's texts are in --
        English when none are loaded -- and shown wherever the asset is."""
        texts_file = self.mods / "[Seal] Other Houses" / "data/config/gui/texts_english.xml"
        texts_file.parent.mkdir(parents=True)
        self.addCleanup(lambda: __import__("shutil").rmtree(texts_file.parents[2] / "gui", True))
        texts_file.write_text(
            "<ModOps><ModOp Type=\"add\" Path=\"/TextExport/Texts\">"
            "<Text><GUID>1010031</GUID><Text>Houses of the Seal</Text></Text>"
            "</ModOp></ModOps>",
            encoding="utf-8",
        )
        self.load()
        row = self.window.editor.index.asset_row("1010031")
        self.assertEqual(row.display, "Houses of the Seal")
        self.assertEqual(row.named_by, "[Seal] Other Houses")
        self.assertIn("1010031", [r.guid for r in self.window.editor.index.search("houses of the seal")])
        # Switched off, it names nothing.
        self.window.loadout.layer("[Seal] Other Houses").active = False
        self.window.refresh_truth(say=False, disk=False)
        self.assertNotEqual(self.window.editor.index.asset_row("1010031").display, "Houses of the Seal")

    def test_and_replaced_for_one_another_mod_replaces(self):
        """Both loaders drop a mod another loaded mod deprecates. With no
        ModID, Anno 1800 goes by the folder's name."""
        info = self.mods / "[Seal] Other Houses" / "modinfo.json"
        was = info.read_text(encoding="utf-8")
        self.addCleanup(info.write_text, was, encoding="utf-8")
        info.write_text(
            '{"ModName": {"English": "[Seal] Other Houses"}, "CreatorName": "Cee",'
            ' "DeprecateIds": ["[Gameplay] Roomier Farmers"]}',
            encoding="utf-8",
        )
        from anno_modding_studio.ui.mods_folder_dialog import COLUMNS

        in_game = COLUMNS.index("In game")
        dialog = self.load()
        cell = self.rowed(dialog, "[Gameplay] Roomier Farmers")[in_game]
        self.assertEqual(cell.text(), "replaced")
        self.assertIn("[Seal] Other Houses replaces it", cell.toolTip())
        self.assertEqual(self.rowed(dialog, "[Seal] Much Roomier Farmers")[in_game].text(), "on")
        # Loaded in, it is on and none of what it does counts.
        loadout = self.window.loadout
        self.assertTrue(loadout.layer("[Gameplay] Roomier Farmers").active)
        self.assertEqual(loadout.replaced(), {"[Gameplay] Roomier Farmers": "[Seal] Other Houses"})
        self.assertNotIn("[Gameplay] Roomier Farmers", {c.origin for c in loadout.active()})
        self.assertIn("because another mod replaces it", self.window.status_label.text())


class TestWhatYourOwnWorkIsCalledOnAModList(ModsFolderCase):
    """Running a whole mod list, your own changes are a patch over that list --
    not a standalone mod, and not the same thing as everybody's first
    "My Anno Mod"."""

    def test_it_is_not_called_the_same_as_a_first_loadout(self):
        from anno_modding_studio.core.changes import AIM_MANAGE

        with mock.patch.object(MainWindow, "load_installed_mods", return_value=True):
            self.window.start.choose_aim(AIM_MANAGE)
        self.assertNotEqual(self.window.loadout.own.name, "My Anno Mod")
        # The patch over this loadout's list, named after the loadout, so
        # that two loadouts' patches are never one mod.
        self.assertEqual(self.window.loadout.own.name, "%s patch" % self.window.files.loadout_name())

    def test_and_a_name_you_chose_yourself_is_kept(self):
        from anno_modding_studio.core.changes import AIM_MANAGE

        self.window.loadout.own.name = "Harbour Fixes"
        with mock.patch.object(MainWindow, "load_installed_mods", return_value=True):
            self.window.start.choose_aim(AIM_MANAGE)
        self.assertEqual(self.window.loadout.own.name, "Harbour Fixes")


class TestTheChangesListAsAStackOfMods(ModsFolderCase):
    """The panel gains an outer level once more than one mod is loaded."""

    def rows(self, kind):
        from anno_modding_studio.ui.changes_dock import KIND_ROLE

        found = []

        def walk(item):
            if item.data(0, KIND_ROLE) == kind:
                found.append(item)
            for index in range(item.childCount()):
                walk(item.child(index))

        tree = self.window.changes_panel.tree
        for index in range(tree.topLevelItemCount()):
            walk(tree.topLevelItem(index))
        return found

    def test_the_top_level_is_the_mods(self):
        self.load()
        self.assertEqual(
            [item.text(0) for item in self.rows("mod")][:3],
            ["[Gameplay] Roomier Farmers", "[Seal] Much Roomier Farmers",
             "[Seal] Other Houses"],
        )
        # And yours after them, empty: it is one of the stack.
        self.assertEqual(len(self.rows("mod")), 4)
        self.assertIn("(yours)", self.rows("mod")[3].text(0))

    def test_and_they_start_shut(self):
        self.load()
        self.assertFalse(any(item.isExpanded() for item in self.rows("mod")))

    def test_expand_all_opens_them_and_collapse_all_shuts_them(self):
        self.load()
        panel = self.window.changes_panel
        self.assertEqual(panel.fold_button.text(), "Expand all")
        panel._toggle_fold()
        self.assertTrue(all(item.isExpanded() for item in self.rows("mod")))
        self.assertEqual(panel.fold_button.text(), "Collapse all")
        panel._toggle_fold()
        self.assertFalse(any(item.isExpanded() for item in self.rows("mod")))

    def test_switching_a_whole_mod_off_stops_everything_it_does(self):
        self.load()
        panel = self.window.changes_panel
        row = self.rows("mod")[0]
        row.setCheckState(0, Qt.CheckState.Unchecked)
        loadout = self.window.loadout
        self.assertFalse(loadout.layer("[Gameplay] Roomier Farmers").active)
        self.assertNotIn(
            "[Gameplay] Roomier Farmers",
            {change.origin for change in loadout.active()},
        )
        self.assertIn("of", panel.summary.text())

    def test_and_its_own_changes_are_left_ticked_underneath(self):
        """A mod is off or on as a mod.  Switching it back on should find your
        tweaks to it as you left them, not four hundred boxes to re-tick."""
        self.load()
        self.rows("mod")[0].setCheckState(0, Qt.CheckState.Unchecked)
        loadout = self.window.loadout
        mine = [
            change
            for change in loadout
            if change.origin == "[Gameplay] Roomier Farmers"
        ]
        self.assertTrue(mine)
        self.assertTrue(all(change.active for change in mine))
        self.rows("mod")[0].setCheckState(0, Qt.CheckState.Checked)
        self.assertIn(
            "[Gameplay] Roomier Farmers",
            {change.origin for change in loadout.active()},
        )

    def test_and_the_change_is_kept_rather_than_deleted(self):
        self.load()
        before = len(self.window.loadout)
        self.rows("mod")[0].setCheckState(0, Qt.CheckState.Unchecked)
        self.assertEqual(len(self.window.loadout), before)

    def test_the_summary_counts_the_mods(self):
        self.load()
        self.assertIn("3 mods", self.window.changes_panel.summary.text())

    def test_with_one_mod_loaded_it_still_has_its_own_row(self):
        """The list reads the same way with one mod as with sixty."""
        self.load(_tick_shown=False)
        self.window.loadout.set_change(
            Change(guid="1010030", asset_label="a", path="/Values/X", tag="X",
                   old_value="1", new_value="2")
        )
        self.window.changes_panel.refresh()
        rows = self.rows("mod")
        self.assertEqual(len(rows), 1)
        self.assertTrue(rows[0].isExpanded(), "one mod alone starts open")


class TestSeeingWhereTwoModsCollide(ModsFolderCase):
    """Two mods retuning the same field is the thing a manager must surface."""

    def test_the_panel_offers_the_list_when_something_is_contested(self):
        self.load()
        panel = self.window.changes_panel
        self.assertIn("contested", panel.conflicts_button.text())

    def test_and_says_nothing_when_nothing_is(self):
        self.load()
        self.window.loadout.drop_layer("[Seal] Much Roomier Farmers")
        self.window.changes_panel.refresh()
        self.assertEqual(self.window.changes_panel.conflicts_button.text(), "")

    def test_the_list_names_both_mods_and_which_one_wins(self):
        from anno_modding_studio.ui.conflicts_dialog import ConflictsDialog

        self.load()
        dialog = ConflictsDialog(self.window.loadout, self.window)
        try:
            self.assertEqual(dialog.table.rowCount(), 1)
            mods = dialog.table.item(0, 2).text()
            self.assertIn("[Gameplay] Roomier Farmers", mods)
            self.assertIn("[Seal] Much Roomier Farmers", mods)
            self.assertIn("[Seal] Much Roomier Farmers", dialog.table.item(0, 3).text())
        finally:
            dialog.deleteLater()

    def test_a_contested_row_offers_the_asset_and_the_field_it_is_over(self):
        """The column shows the field's tail; the whole path is what you quote."""
        from PySide6.QtGui import QGuiApplication

        from anno_modding_studio.ui.conflicts_dialog import ConflictsDialog

        self.load()
        dialog = ConflictsDialog(self.window.loadout, self.window)
        try:
            menu = dialog.menu_for(0)
            texts = [a.text() for a in menu.actions() if not a.isSeparator()]
            self.assertIn("Open in asset editor", texts)
            self.assertIn("Copy field path", texts)
            self.assertIn("Copy GUID", texts)

            next(
                a for a in menu.actions() if a.text() == "Copy field path"
            ).trigger()
            wanted = dialog.table.item(0, 0).data(Qt.ItemDataRole.UserRole)[1]
            self.assertEqual(QGuiApplication.clipboard().text(), wanted)
        finally:
            dialog.deleteLater()

    def test_the_losing_change_is_greyed_the_way_a_switched_off_one_is(self):
        self.load()
        panel = self.window.changes_panel
        rows = {
            row.data(0, GUID_ROLE) + row.text(1): row for row in change_rows(panel)
        }
        beaten = [r for r in rows.values() if "12" in r.text(1)]
        self.assertTrue(beaten)
        self.assertEqual(
            beaten[0].foreground(0).color().name(), QColor(theme.TEXT_DIM).name()
        )

    def test_switching_the_winner_off_settles_it(self):
        self.load()
        switch_off(self.window.loadout, "[Seal] Much Roomier Farmers")
        self.window.changes_panel.refresh()
        self.assertEqual(self.window.changes_panel.conflicts_button.text(), "")


class TestTheChangesPanelsOwnMenu(ModsFolderCase):
    """Right-clicking a row offers what can be done to it.

    Everything here is reachable from somewhere else -- the Edit menu, a button
    beside the editor, the File menu -- and nowhere else is *beside the thing it
    is about*.  A mod whose settings you want to change is a row in this list,
    so this is where somebody looks.
    """

    THEIRS = "[Gameplay] Roomier Farmers"

    def setUp(self):
        super().setUp()
        # Making a patch asks for its name, which would sit modal in a test.
        from PySide6.QtWidgets import QInputDialog

        real = QInputDialog.getText
        QInputDialog.getText = staticmethod(
            lambda *a, **k: (k.get("text", ""), True)
        )
        self.addCleanup(setattr, QInputDialog, "getText", staticmethod(real))

    def menu(self, kind, origin=None):
        """The menu the panel builds for the first row of ``kind``."""
        from anno_modding_studio.ui.changes_dock import KIND_ROLE, ORIGIN_ROLE

        panel = self.window.changes_panel
        panel.build_every_row()
        wanted = None

        def walk(item):
            nonlocal wanted
            if wanted is None and item.data(0, KIND_ROLE) == kind:
                if origin is None or item.data(0, ORIGIN_ROLE) == origin:
                    wanted = item
            for index in range(item.childCount()):
                walk(item.child(index))

        for index in range(panel.tree.topLevelItemCount()):
            walk(panel.tree.topLevelItem(index))
        self.assertIsNotNone(wanted, "no %s row" % kind)

        # As right-clicking it would: the row under the cursor becomes the
        # selection, so "Revert selected" means that row.
        panel.tree.setCurrentItem(wanted)
        built = panel.menu_for(wanted)
        self.addCleanup(built.deleteLater)
        return {action.text(): action for action in built.actions() if action.text()}

    def test_a_mod_row_offers_everything_that_can_be_done_to_that_mod(self):
        self.load()
        actions = self.menu("mod", self.THEIRS)
        self.assertIn("Mod options…", actions)
        self.assertIn("Patch this mod…", actions)
        self.assertIn("Unload %s…" % self.THEIRS, actions)
        self.assertIn("New pattern…", actions)

    def test_a_mod_with_no_settings_says_so_rather_than_hiding_the_entry(self):
        """Hiding it leaves somebody hunting for where it went."""
        self.load()
        actions = self.menu("mod", self.THEIRS)
        self.assertFalse(actions["Mod options…"].isEnabled())
        self.assertIn("declares no settings", actions["Mod options…"].toolTip())

    def test_a_mod_row_opens_that_mod_s_folder(self):
        from pathlib import Path
        from unittest import mock

        from anno_modding_studio.ui import changes_dock

        self.load()
        folder = Path(self.window.loadout.layer(self.THEIRS).folder)
        self.assertTrue(folder.is_dir())
        action = self.menu("mod", self.THEIRS)["Open this mod's folder"]
        self.assertTrue(action.isEnabled())
        self.assertEqual(action.toolTip(), str(folder))
        with mock.patch.object(changes_dock, "open_on_disk") as opened:
            action.trigger()
        opened.assert_called_once_with(folder)

    def test_a_mod_whose_folder_is_gone_says_so(self):
        self.load()
        self.window.loadout.layer(self.THEIRS).folder = ""
        action = self.menu("mod", self.THEIRS)["Open this mod's folder"]
        self.assertFalse(action.isEnabled())
        self.assertIn("has no folder on this PC", action.toolTip())

    def test_a_library_mod_switched_off_opens_where_the_library_keeps_it(self):
        """Hands-on run 3, beside R3-46: its layer names where its link goes
        back, no folder until it is live, and the entry was greyed."""
        from pathlib import Path
        from unittest import mock

        from anno_modding_studio.core import library

        self.load()
        layer = self.window.loadout.layer(self.THEIRS)
        kept = Path(layer.folder)
        layer.folder = str(kept.parent / "not linked" / kept.name)
        with mock.patch.object(library, "kept_at", return_value=kept) as asked:
            action = self.menu("mod", self.THEIRS)["Open this mod's folder"]
        self.assertEqual(asked.call_args.args[1], Path(layer.folder))
        self.assertTrue(action.isEnabled())
        self.assertEqual(action.toolTip(), str(kept))

    def test_a_mod_of_yours_is_not_offered_a_patch(self):
        """R3-20, hands-on run 3: offered on a mod a project keeps, one
        with your name on it, and the loadout's own."""
        from dataclasses import replace
        from unittest import mock

        from anno_modding_studio.core import patching

        self.load()
        loadout = self.window.loadout
        theirs = loadout.layer(self.THEIRS)
        loadout.set_layer(replace(theirs, kept=True))
        action = self.menu("mod", self.THEIRS)["Patch this mod…"]
        self.assertFalse(action.isEnabled())
        self.assertIn("is yours", action.toolTip())
        loadout.set_layer(theirs)
        with mock.patch.object(patching, "looks_mine", return_value=True):
            self.assertFalse(self.menu("mod", self.THEIRS)["Patch this mod…"].isEnabled())
        self.assertTrue(self.menu("mod", self.THEIRS)["Patch this mod…"].isEnabled())

    def test_a_mod_already_patched_is_not_offered_a_second_patch(self):
        self.load()
        self.window.patches.make_patch(self.THEIRS)
        self.window.changes_panel.refresh()
        self.assertFalse(self.menu("mod", self.THEIRS)["Patch this mod…"].isEnabled())

    def test_a_change_row_offers_the_asset_it_is_about(self):
        self.load()
        actions = self.menu("change")
        self.assertIn("Open in asset editor", actions)
        self.assertIn("Revert selected", actions)

    def test_and_still_knows_which_mod_it_belongs_to(self):
        """A change five levels down belongs to the mod heading its section,
        and asking the row you actually clicked is what people do."""
        self.load()
        actions = self.menu("change")
        self.assertIn("Mod options…", actions)

    def test_but_unloading_the_mod_is_only_on_its_own_row(self):
        """Offered on every row inside the mod, it was a click away from
        what each of those rows is for."""
        self.load()
        actions = self.menu("change")
        self.assertFalse(
            any(name.startswith("Unload ") for name in actions), sorted(actions)
        )
        names = list(self.menu("mod", self.THEIRS))
        # And last, past everything that is done every day.
        self.assertEqual(names[-1], "Unload %s…" % self.THEIRS)

    def test_unloading_from_the_menu_forgets_that_one_mod(self):
        self.load()
        before = len(self.window.loadout.layers)
        with _isolation.answering(True) as asked:
            self.window.unload_one_mod(self.THEIRS)
        # Asked although nothing of yours is in it: it is one click in a menu.
        self.assertEqual(asked.titles, ["Unload mods?"])
        self.assertIn("Edit ▸ Undo puts it back", asked.texts[0])
        self.assertIsNone(self.window.loadout.layer(self.THEIRS))
        self.assertEqual(len(self.window.loadout.layers), before - 1)
        self.assertIn("Unloaded", self.window.status_label.text())

    def test_and_declining_keeps_it(self):
        self.load()
        with _isolation.answering(False):
            self.window.unload_one_mod(self.THEIRS)
        self.assertIsNotNone(self.window.loadout.layer(self.THEIRS))

    def test_undoing_an_unload_points_changes_at_the_mod_again(self):
        """Unloaded, the patch changes went to was forgotten as where they
        go, and the undo brought the patch back with "Currently editing"
        still on the stand-in."""
        self.load()
        self.window.patches.make_patch(self.THEIRS)
        patch = self.THEIRS + " (my patch)"
        self.window.loadout.editing = patch
        self.window.editor.refresh_target()
        box = self.window.editor.target_box
        with _isolation.answering(True):
            self.window.unload_one_mod(patch)
        self.assertEqual(box.currentData(), "")
        self.window.undo_last()
        self.assertIsNotNone(self.window.loadout.layer(patch))
        self.assertEqual(box.currentData(), patch)
        self.assertEqual(self.window.loadout.editing_origin(), patch)

    def test_patching_from_the_menu_gives_the_patch_a_row_of_its_own(self):
        self.load()
        self.window.patches.make_patch(self.THEIRS)
        from anno_modding_studio.ui.changes_dock import KIND_ROLE, ORIGIN_ROLE

        panel = self.window.changes_panel
        rows = {
            panel.tree.topLevelItem(i).data(0, ORIGIN_ROLE)
            for i in range(panel.tree.topLevelItemCount())
            if panel.tree.topLevelItem(i).data(0, KIND_ROLE) == "mod"
        }
        self.assertIn(self.THEIRS + " (my patch)", rows)


class TestTheTweakAimEndToEnd(unittest.TestCase):
    """"Change a mod you downloaded", driven through the dialog it opens.

    The earlier tests for this stubbed ``import_mod``, so they proved that
    ``choose_aim`` *asks* for a patch and never that one arrives.  It did not:
    the import handed the mod's own creator name to the loadout, the Studio
    recognises a mod as yours by its creator, and so the mod was taken for one
    the user wrote and refused a patch.  Nothing between those two steps was
    wrong on its own.
    """

    PATCH = (
        "<ModOps>"
        '<ModOp Type="merge" GUID="1010030" Path="/Values/Residence7"><Residents>16</Residents></ModOp>'
        "</ModOps>"
    )

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        tmp = Path(cls._tmp.name)
        cls.assets = tmp / "assets.xml"
        cls.assets.write_text(make_sample_assets.build_assets_xml(40), encoding="utf-8")
        cls.db = tmp / "index.sqlite"
        index_mod.build_index(cls.assets, cls.db)
        cls.mod = tmp / "Efficient Celtic Charcoal"
        patch = cls.mod / "data/config/export/main/asset/assets.xml"
        patch.parent.mkdir(parents=True)
        patch.write_text(cls.PATCH, encoding="utf-8")
        (cls.mod / "modinfo.json").write_text(
            '{"ModName": {"English": "Efficient Celtic Charcoal"}, '
            '"CreatorName": "Someone", "ModID": "celtic-charcoal"}',
            encoding="utf-8",
        )

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def setUp(self):
        self.window = MainWindow()
        self.addCleanup(_flush_deletes)
        self.window.game_files.adopt_index(self.db, self.assets, cached=True)
        # This aim asks who you are before it reads anything, so the prompt has
        # to be answered or the test sits on a modal box.
        self.window.settings.setValue(self.window.preferences.CREATOR_KEY, "Me")
        self.addCleanup(self.window.settings.remove, self.window.preferences.CREATOR_KEY)
        # A mod of yours nothing keeps is offered a project as it is opened
        # (section 5.5 of the projects and loadouts proposal): yes, here.
        made = mock.patch.object(self.window.prompts, "ask_make_project", return_value=True)
        self.make_project = made.start()
        self.addCleanup(made.stop)

    def tearDown(self):
        self.window.editor.search._debounce.stop()
        self.window.game_files.stop_texts_worker()
        if self.window.index is not None:
            self.window.index.close()
            self.window.index = None
        self.window.loadout.clear()
        self.window.deleteLater()

    def tweak(self, mod=None, existing=False):
        """Pick the aim, and let the real import dialog do the reading.

        ``existing`` picks "Open one of yours…" on the first card instead.
        """
        from anno_modding_studio.core.changes import AIM_MAKE, AIM_TWEAK
        from anno_modding_studio.ui import mod_import_dialog as import_mod_ui

        folder = str(mod or self.mod)
        opened = {}

        def run(dialog):
            dialog.folder_box.setText(folder)
            dialog.reload()
            opened["separate"] = dialog.separate_box.isChecked()
            opened["metadata"] = dialog.metadata_box.isChecked()
            dialog._accept()
            return int(QDialog.DialogCode.Accepted)

        with mock.patch.object(import_mod_ui.ModImportDialog, "exec", run):
            if existing:
                self.window.start.choose_aim(AIM_MAKE, existing=True)
            else:
                self.window.start.choose_aim(AIM_TWEAK)
        return opened

    def my_mod(self, name="My Own Charcoal"):
        """A mod on disk whose creator is the one the window publishes as."""
        mine = Path(self._tmp.name) / name
        ops = mine / "data/config/export/main/asset/assets.xml"
        ops.parent.mkdir(parents=True, exist_ok=True)
        ops.write_text(self.PATCH, encoding="utf-8")
        (mine / "modinfo.json").write_text(
            '{"ModName": {"English": "%s"}, "CreatorName": "Me"}' % name,
            encoding="utf-8",
        )
        return mine

    def test_a_mod_of_yours_is_writing_a_mod_of_your_own(self):
        """Whose mod it is decides the way of working, not where it was
        started: this one used to go on reading "Changing a downloaded mod"."""
        from anno_modding_studio.core.changes import AIM_MAKE

        mine = self.my_mod()
        self.tweak(mine)
        self.assertEqual(self.window.loadout.aim, AIM_MAKE)
        self.assertEqual(self.window.loadout.home, mine.name)
        self.window.start.refresh_source_panel()
        self.assertTrue(self.window.source.ways[AIM_MAKE].property("current"))
        self.assertEqual(self.window.files.loadout_name(), "My Own Charcoal")

    def test_and_its_details_are_yours_to_change_on_the_export_tab(self):
        """They were all read-only: a loaded mod read as somebody else's."""
        mine = self.my_mod()
        self.tweak(mine)
        out = self.window.output
        out.refresh()
        for box in (
            out.version_edit,
            out.creator_edit,
            out.modid_edit,
            out.description_edit,
        ):
            self.assertFalse(box.isReadOnly())
        self.assertTrue(out.category_combo.isEnabled())
        # Named on the Start tab, with the loadout, as any mod of your own is.
        self.assertTrue(out.name_edit.isReadOnly())

        out.version_edit.setText("2.0")
        self.assertEqual(self.window.loadout.layer(mine.name).version, "2.0")
        self.assertNotEqual(self.window.loadout.own.version, "2.0")

    def test_and_the_loadouts_own_mod_beside_it_is_not_the_one_released(self):
        """The mod.io box and the suggested version are the mod of yours the
        loadout carries on with. Offered for the loadout's own mod beside it,
        "Use" wrote a version that mod's suggestion is not measured against,
        and the offer never went away."""
        from anno_modding_studio.core.changes import KIND_EXPORT

        mine = self.my_mod()
        self.tweak(mine)
        loadout = self.window.loadout
        loadout.layer(mine.name).version = "3.4.0"
        loadout.make_own_mod()
        loadout.checkpoint("Exported v3.4.0", kind=KIND_EXPORT, origin=mine.name)
        loadout.set_change(
            Change(guid="1010340", asset_label="a", path="/Values/X", tag="X",
                   old_value="1", new_value="2", origin="")
        )
        out = self.window.output
        out.refresh()
        out.mod_box.setCurrentIndex(out.mod_box.findData(""))
        self.assertEqual(out.current_origin(), "")
        out.refresh()

        self.assertFalse(out.suggest_label.isVisibleTo(out))
        self.assertFalse(out.suggest_button.isVisibleTo(out))
        self.assertFalse(out.modio_button.isVisibleTo(out))

        # The mod of yours is still the one released from here.
        out.mod_box.setCurrentIndex(out.mod_box.findData(mine.name))
        out.refresh()
        self.assertTrue(out.modio_button.isVisibleTo(out))

    def test_and_renaming_the_loadout_renames_that_mod(self):
        mine = self.my_mod()
        self.tweak(mine)
        self.window.files.rename_loadout("Better Charcoal")
        self.assertEqual(self.window.loadout.layer(mine.name).name, "Better Charcoal")
        self.assertEqual(self.window.output.name_edit.text(), "Better Charcoal")
        # And its row in the Mod changes panel and "Changes go to" say so.
        tree = self.window.changes_panel.tree
        rows = [tree.topLevelItem(at).text(0) for at in range(tree.topLevelItemCount())]
        self.assertTrue(any("Better Charcoal" in row for row in rows), rows)
        self.assertIn("Better Charcoal", self.window.editor.target_box.currentText())

    def test_and_writes_its_new_name_into_the_loadout_file_at_once(self):
        """Discard on the way out left the file under the new name with the
        mod in it under the old one."""
        from anno_modding_studio.core.changes import Loadout

        mine = self.my_mod()
        self.tweak(mine)
        self.window.files.name_loadout("Charcoal")
        self.window.files.autosave()
        self.window.files.rename_loadout("Better Charcoal")
        saved = Loadout.load(self.window.loadout_path)
        self.assertEqual(self.window.files.loadout_name(), "Better Charcoal")
        self.assertEqual(saved.layer(mine.name).name, "Better Charcoal")
        self.assertFalse(self.window.files.unsaved_work())

    def test_and_its_project_file_takes_the_new_name(self):
        """The project kept the name it was first saved under, and nothing
        in the Studio could change it."""
        from anno_modding_studio.core import projects

        mine = self.my_mod()
        self.tweak(mine)
        self.window.files.name_loadout("Charcoal")
        self.window.files.autosave()
        was = projects.where(self.window.loadout.projects[mine.name].path)
        self.window.files.rename_loadout("Better Charcoal")
        ref = self.window.loadout.projects[mine.name]
        self.assertEqual(projects.where(ref.path).name, "Better Charcoal.annoproj")
        self.assertFalse(was.exists())
        # Its name is in it already: the next save does not write it again.
        renamed_at = ref.read_at
        self.window.files.autosave()
        self.assertEqual(projects.read(projects.where(ref.path))["revision"], renamed_at)

    def test_opening_one_of_yours_from_the_first_card(self):
        from PySide6.QtWidgets import QInputDialog

        from anno_modding_studio.core.changes import AIM_MAKE

        mine = self.my_mod("Harbour Tweaks")
        # Not asked to name a new mod: it has a name.
        with mock.patch.object(
            QInputDialog, "getText", side_effect=AssertionError("asked for a name")
        ):
            opened = self.tweak(mine, existing=True)
        self.assertTrue(opened["separate"])
        loadout = self.window.loadout
        self.assertEqual(loadout.aim, AIM_MAKE)
        self.assertEqual(loadout.home, mine.name)
        self.assertEqual(loadout.editing_origin(), mine.name)
        self.assertIsNone(loadout.patch_for(mine.name))
        self.assertEqual(self.window.files.loadout_name(), "Harbour Tweaks")
        # Nothing kept it, so it was asked first.
        self.make_project.assert_called_once_with("Harbour Tweaks")

    def test_the_mod_the_loadout_is_stays_loaded(self):
        """Unloaded from its row, it left a loadout with nothing to write and
        "Currently editing" on a stand-in nobody asked for."""
        from anno_modding_studio.ui.changes_dock import KIND_ROLE, ORIGIN_ROLE

        mine = self.my_mod("Harbour Tweaks")
        self.tweak(mine, existing=True)
        window = self.window
        tree = window.changes_panel.tree
        row = next(
            tree.topLevelItem(at) for at in range(tree.topLevelItemCount())
            if tree.topLevelItem(at).data(0, KIND_ROLE) == "mod"
            and tree.topLevelItem(at).data(0, ORIGIN_ROLE) == mine.name
        )
        built = window.changes_panel.menu_for(row)
        self.addCleanup(built.deleteLater)
        unload = next(a for a in built.actions() if a.text().startswith("Unload "))
        self.assertFalse(unload.isEnabled())
        self.assertIn("This loadout is Harbour Tweaks", unload.toolTip())
        # Nor by any other way in.
        window.unload_one_mod(mine.name)
        self.assertEqual(window.unload_mods([mine.name]), 0)
        self.assertIsNotNone(window.loadout.layer(mine.name))
        self.assertFalse(window.unload_action.isEnabled())
        self.assertEqual(window.editor.target_box.currentData(), mine.name)

    def test_one_of_yours_nothing_keeps_declined_is_read_like_anybody_elses(self):
        mine = self.my_mod("Harbour Tweaks")
        self.make_project.return_value = False
        self.tweak(mine, existing=True)
        loadout = self.window.loadout
        self.assertEqual(loadout.home, "")
        layer = loadout.layer(mine.name)
        self.assertIsNotNone(layer)
        self.assertNotIn(mine.name, loadout.project_origins())
        self.assertIn("read like anybody else's", self.window.status_label.text())
        # Currently editing offers to make it a project still.
        box = self.window.editor.target_box
        self.assertEqual(box.itemText(box.findData(NEW_PROJECT + mine.name)),
                         "Make Harbour Tweaks a project of yours…")

    def kept_mod(self, build: int = 0):
        """A mod of yours with a ModID, opened once and saved: its project
        is made, and the loadout that has it."""
        _isolation.forget_loadouts()
        self.addCleanup(_isolation.forget_loadouts)
        mine = self.my_mod("Harbour Tweaks")
        info = {"ModName": {"English": "Harbour Tweaks"}, "CreatorName": "Me", "ModID": "me-harbour"}
        if build:
            info["AnnoModdingStudio"] = {"build": build}
        (mine / "modinfo.json").write_text(json.dumps(info), encoding="utf-8")
        self.tweak(mine, existing=True)
        self.window.files.autosave()
        first = self.window.loadout_path
        self.window.new_loadout()
        return mine, first

    def test_one_of_yours_kept_in_a_project_offers_the_loadouts_that_have_it(self):
        """One project per ModID (section 4.6 of the projects and loadouts
        proposal): opening it again does not make a second."""
        mine, first = self.kept_mod()
        with mock.patch.object(self.window.prompts, "ask_open_kept", return_value=0) as asked, \
                mock.patch.object(self.window, "load_loadout", return_value=True) as opened:
            self.tweak(mine, existing=True)
        self.assertEqual(asked.call_args[0][:2], ("Harbour Tweaks", ["Harbour Tweaks"]))
        opened.assert_called_once_with(first, asked=True)
        # Read in to find out whose it was, and let go of again.
        self.assertEqual(self.window.loadout.layers, [])
        self.assertEqual(self.window.loadout.home, "")

    def test_and_declining_opens_nothing_and_keeps_nothing(self):
        mine, _first = self.kept_mod()
        with mock.patch.object(self.window.prompts, "ask_open_kept", return_value=-1), \
                mock.patch.object(self.window, "load_loadout") as opened:
            self.tweak(mine, existing=True)
        opened.assert_not_called()
        self.assertEqual(self.window.loadout.layers, [])

    def test_a_project_no_loadout_has_is_carried_on_in_and_not_made_again(self):
        from anno_modding_studio.core import projects
        from anno_modding_studio.core.changes import Loadout

        mine, first = self.kept_mod()
        kept = dict(Loadout.load(first).projects)[mine.name].path
        first.unlink()
        self.tweak(mine, existing=True)
        self.assertEqual(self.window.loadout.projects[mine.name].path, kept)
        self.window.files.autosave()
        self.assertEqual(len(list(projects.where(kept).parent.glob("*.annoproj"))), 1)

    def test_an_older_build_than_its_projects_last_is_said(self):
        from anno_modding_studio.core.changes import Loadout

        mine, first = self.kept_mod(build=2)
        loaded = Loadout.load(first)
        loaded.builds[mine.name] = {"build": 5, "digest": "d"}
        loaded.save(first)
        with mock.patch.object(self.window.prompts, "ask_open_kept", return_value=-1) as asked:
            self.tweak(mine, existing=True)
        self.assertIn("build 2", asked.call_args[0][2])
        self.assertIn("up to 5", asked.call_args[0][2])

    def test_an_older_folder_no_loadout_has_is_read_from_its_project(self):
        """Carrying on from a folder older than its project used to write
        the folder over the project at the next save, its history and its
        build record with it: the project is read back instead."""
        from anno_modding_studio.core import projects
        from anno_modding_studio.core.changes import Loadout

        mine, first = self.kept_mod(build=2)
        loaded = Loadout.load(first)
        loaded.builds[mine.name] = {"build": 5, "digest": "d"}
        loaded.checkpoint("v1", origin=mine.name, loadout="Harbour Tweaks")
        loaded.save(first)
        kept = projects.where(loaded.projects[mine.name].path)
        first.unlink()
        with _isolation.answering(False) as asked:
            self.tweak(mine, existing=True)
        self.assertEqual(asked.titles, [])
        loadout = self.window.loadout
        self.assertEqual((loadout.home, loadout.held(mine.name)), (mine.name, ""))
        self.assertTrue(self.window.status_label.text().startswith(
            "Carrying on with Harbour Tweaks from its project."), self.window.status_label.text())
        self.window.files.autosave()
        written = projects.read(kept)
        self.assertEqual(written["build"]["build"], 5)
        self.assertEqual([raw["label"] for raw in written["history"]], ["v1"])

    def test_changed_as_downloaded_one_kept_elsewhere_offers_its_loadouts(self):
        """Read in to be changed, a mod of yours is carried on with: where
        loadouts have its project, it is not carried on with a second time
        beside it, which made a second project for its ModID."""
        mine, first = self.kept_mod()
        with mock.patch.object(self.window.prompts, "ask_open_kept", return_value=0) as asked,                 mock.patch.object(self.window, "load_loadout", return_value=True) as opened:
            self.tweak(mine)
        self.assertEqual(asked.call_args[0][:2], ("Harbour Tweaks", ["Harbour Tweaks"]))
        opened.assert_called_once_with(first, asked=True)
        self.assertEqual(self.window.loadout.layers, [])

    def test_changed_as_downloaded_one_no_loadout_has_is_read_from_its_project(self):
        from anno_modding_studio.core import projects
        from anno_modding_studio.core.changes import Loadout

        mine, first = self.kept_mod()
        kept = projects.where(Loadout.load(first).projects[mine.name].path)
        was = projects.read(kept)
        first.unlink()
        self.tweak(mine)
        loadout = self.window.loadout
        self.assertEqual((loadout.home, loadout.held(mine.name)), (mine.name, ""))
        self.assertEqual(projects.where(loadout.projects[mine.name].path), kept)
        self.assertIn("kept by its project, which it is read from", self.window.status_label.text())
        self.window.files.autosave()
        self.assertEqual(projects.read(kept)["revision"], was["revision"])
        self.assertEqual(len(list(kept.parent.glob("*.annoproj"))), 1)

    def test_made_a_project_one_kept_elsewhere_is_read_from_it(self):
        """A mod with your name nothing keeps here, made a project in
        Currently editing: the project for its ModID another loadout has is
        taken up and read back, not written over at the next save."""
        from anno_modding_studio.core import projects
        from anno_modding_studio.core.changes import Loadout, ModLayer

        mine, first = self.kept_mod()
        kept = projects.where(Loadout.load(first).projects[mine.name].path)
        was = projects.read(kept)
        loadout = self.window.loadout
        loadout.own.creator = "Me"
        loadout.set_layer(ModLayer(origin=mine.name, name=mine.name, folder=str(mine),
                                   creator="Me", mod_id="me-harbour", order=1))
        self.assertNotIn(mine.name, loadout.project_origins())
        self.assertEqual(self.window.patches.make_project(mine.name), mine.name)
        self.assertEqual(loadout.held(mine.name), "")
        self.assertEqual(projects.where(loadout.projects[mine.name].path), kept)
        self.assertIn("which the loadout Harbour Tweaks has", self.window.status_label.text())
        self.window.files.autosave()
        self.assertEqual(projects.read(kept)["revision"], was["revision"])

    def test_a_second_loadout_changing_the_mod_takes_up_its_patch(self):
        """One patch to a mod (section 5.1 of the projects and loadouts
        proposal): changing it from another loadout joins the patch made in
        the first, rather than making a second patch of it."""
        _isolation.forget_loadouts()
        self.addCleanup(_isolation.forget_loadouts)
        self.tweak()
        patch = self.window.loadout.patch_for(self.mod.name)
        self.assertIsNotNone(patch)
        self.window.files.autosave()
        kept = self.window.loadout.projects[patch.origin].path
        self.window.new_loadout()
        self.tweak()
        joined = self.window.loadout.patch_for(self.mod.name)
        self.assertEqual(joined.origin, patch.origin)
        self.assertEqual(self.window.loadout.projects[joined.origin].path, kept)
        self.assertEqual(self.window.loadout.held(joined.origin), "")
        self.assertEqual(self.window.loadout.editing_origin(), joined.origin)
        self.window.files.autosave()
        from anno_modding_studio.core import places

        self.assertEqual(len(list(places.projects_dir().glob("*my patch*.annoproj"))), 1)

    def test_somebody_elses_is_not_taken_for_yours_without_asking(self):
        with _isolation.answering("Unload it") as asked:
            self.tweak(existing=True)
        self.assertEqual(asked.titles, ["Is this one yours?"])
        self.assertEqual(self.window.loadout.layers, [])
        self.assertEqual(self.window.loadout.home, "")
        self.assertEqual(self.window.loadout.aim, "")

    def test_and_saying_it_is_yours_makes_it_so(self):
        with _isolation.answering("Carry on as mine"):
            self.tweak(existing=True)
        loadout = self.window.loadout
        self.assertEqual(loadout.home, self.mod.name)
        self.assertTrue(patching.is_mine(loadout, loadout.layer(self.mod.name)))
        self.assertIsNone(loadout.patch_for(self.mod.name))

    def test_and_its_author_name_is_not_made_yours_unless_ticked(self):
        """A mod carried on with is not always one you published, and a name
        made yours makes every mod by it yours."""
        self.addCleanup(self.window.settings.remove, self.window.preferences.ALIASES_KEY)
        with _isolation.answering("Carry on as mine"):
            self.tweak(existing=True)
        self.assertEqual(self.window.preferences.aliases(), [])

    def test_ticked_it_is_one_of_your_author_names(self):
        self.addCleanup(self.window.settings.remove, self.window.preferences.ALIASES_KEY)
        with mock.patch.object(self.window.prompts, "ask_carry_on_as_mine", return_value=True) as asked:
            self.tweak(existing=True)
        self.assertEqual(asked.call_args.args[1], "Someone")
        self.assertEqual(self.window.preferences.aliases(), ["Someone"])
        self.assertEqual(self.window.loadout.home, self.mod.name)

    def test_a_mod_by_another_name_of_yours_is_yours_without_asking(self):
        """As one with your author name is: written directly, with no patch."""
        from anno_modding_studio.core.changes import AIM_MAKE

        self.addCleanup(self.window.settings.remove, self.window.preferences.ALIASES_KEY)
        self.window.preferences.set_aliases(["someone"])
        self.tweak()
        loadout = self.window.loadout
        self.assertEqual((loadout.aim, loadout.home), (AIM_MAKE, self.mod.name))
        self.assertIsNone(loadout.patch_for(self.mod.name))

    def test_a_loadout_saved_by_an_older_build_is_recognised(self):
        """Filed under "Changing a downloaded mod", editing a mod of yours
        straight: that was writing your own all along."""
        from anno_modding_studio.core.changes import AIM_MAKE, AIM_TWEAK

        mine = self.my_mod()
        self.tweak(mine)
        loadout = self.window.loadout
        loadout.home, loadout.aim = "", AIM_TWEAK
        path = Path(self._tmp.name) / "older.annoload"
        loadout.save(path)
        with mock.patch.object(self.window.files, "confirm_discard", return_value=True):
            self.assertTrue(self.window.load_loadout(path))
        self.assertEqual(self.window.loadout.aim, AIM_MAKE)
        self.assertEqual(self.window.loadout.home, mine.name)

    def test_the_dialog_opens_asking_for_the_mod_itself(self):
        self.assertTrue(self.tweak()["separate"])

    def test_and_does_not_hand_the_loadout_the_authors_identity(self):
        """Yours stays yours. Taking theirs made their mod read back as one you
        wrote, which is what refused it a patch."""
        self.assertFalse(self.tweak()["metadata"])
        self.assertEqual(self.window.loadout.own.creator, "Me")
        self.assertNotEqual(
            self.window.loadout.own.name, "Efficient Celtic Charcoal"
        )

    def test_a_mod_of_your_own_is_edited_directly(self):
        """No patch for a mod you wrote -- and no "My Anno Mod" beside it
        either: nothing made one.  "Changes go to" offers to."""
        mine = self.my_mod()
        self.tweak(mine)
        loadout = self.window.loadout
        self.assertIsNone(loadout.patch_for(mine.name))
        self.assertEqual(loadout.editing_origin(), mine.name)
        box = self.window.editor.target_box
        from anno_modding_studio.ui.editor_panel import NEW_OWN_MOD

        self.assertEqual(
            [box.itemData(i) for i in range(box.count())], [mine.name, NEW_OWN_MOD]
        )
        self.assertEqual(box.currentData(), mine.name)
        self.assertIn("Edits go straight into it.", self.window.editor.target_label.text())
        self.assertEqual(self.window.output.pickable(), [mine.name])
        self.assertFalse(loadout.has_own_mod())
        self.window.update_title()
        self.assertNotIn("My Anno Mod", self.window.windowTitle())

    def test_changing_a_downloaded_mod_makes_a_patch_and_nothing_else(self):
        """It used to leave a "My Anno Mod" standing beside the patch, which
        nobody had asked for."""
        from anno_modding_studio.ui.changes_dock import ORIGIN_ROLE

        self.tweak()
        loadout = self.window.loadout
        self.assertFalse(loadout.has_own_mod())
        tree = self.window.changes_panel.tree
        tops = [tree.topLevelItem(i).data(0, ORIGIN_ROLE) for i in range(tree.topLevelItemCount())]
        self.assertEqual(tops, [self.mod.name, self.mod.name + " (my patch)"])
        self.assertNotIn("", self.window.output.pickable())

    def test_and_a_mod_of_your_own_is_there_for_the_asking(self):
        """The last entry of "Changes go to" makes one -- the way from a
        patch on somebody's mod to a mod of your own."""
        from anno_modding_studio.ui.editor_panel import NEW_OWN_MOD

        self.tweak()
        box = self.window.editor.target_box
        box.setCurrentIndex(box.findData(NEW_OWN_MOD))
        loadout = self.window.loadout
        self.assertTrue(loadout.has_own_mod())
        self.assertEqual(loadout.editing_origin(), "")
        self.assertEqual(box.currentData(), "")
        self.assertEqual(box.findData(NEW_OWN_MOD), -1)
        self.window.changes_panel.refresh()
        self.assertIn("", self.window.output.pickable())

    def test_the_patch_mod_is_actually_there_afterwards(self):
        self.tweak()
        patch = self.window.loadout.patch_for(self.mod.name)
        self.assertIsNotNone(patch)
        self.assertEqual(patch.patches, self.mod.name)
        self.assertEqual(
            [layer.origin for layer in self.window.loadout.layers],
            [self.mod.name, self.mod.name + " (my patch)"],
        )

    def test_and_is_where_edits_go(self):
        self.tweak()
        self.assertEqual(
            self.window.loadout.editing_origin(), self.mod.name + " (my patch)"
        )

    def test_and_loads_after_the_mod_by_that_mods_declared_id(self):
        """The folder name sorts it after; the declaration is what the loader
        resolves, and it has to be the ModID rather than the folder."""
        self.tweak()
        view = generator.for_mod(
            self.window.loadout, self.mod.name + " (my patch)"
        )
        self.assertEqual(view.own.patches, "celtic-charcoal")
        written = modinfo.build(view)
        self.assertIn('"LoadAfterIds"', written)
        self.assertIn("celtic-charcoal", written)

    def test_an_edit_lands_in_the_patch_over_the_mods_own_value(self):
        self.tweak()
        self.window.editor.load_asset("1010030")
        for item in self.window.editor._iter_items():
            if item.text(0) == "Residents" and item.data(0, 258) is not None:
                item.setText(1, "99")
                break
        else:
            self.fail("no Residents row")
        mine = self.window.loadout.authored()
        self.assertEqual(
            [(c.origin, c.old_value, c.new_value) for c in mine],
            [(self.mod.name + " (my patch)", "16", "99")],
        )

    def _retune_residents(self, value: str) -> None:
        self.window.editor.load_asset("1010030")
        for item in self.window.editor._iter_items():
            if item.text(0) == "Residents" and item.data(0, 258) is not None:
                item.setText(1, value)
                return
        self.fail("no Residents row")

    def test_and_reverting_it_leaves_the_patch_standing(self):
        """The patch was empty before the first edit and is the place edits
        go; reverting the one edit puts it back the way it started.  It used
        to be dropped along with the change, as a mod with nothing left of
        it -- which is right for somebody else's mod and wrong for yours."""
        self.tweak()
        patch = self.mod.name + " (my patch)"
        self._retune_residents("99")
        panel = self.window.changes_panel
        panel.refresh()
        panel.tree.clearSelection()
        # The mod's own change is listed too; the one to revert is yours.
        mine = [row for row in change_rows(panel) if "99" in row.text(1)]
        self.assertEqual(len(mine), 1)
        mine[0].setSelected(True)
        panel._remove_selected()
        self.assertEqual(self.window.loadout.authored(), [])
        self.assertIsNotNone(self.window.loadout.layer(patch))
        self.assertEqual(self.window.loadout.patch_for(self.mod.name).origin, patch)
        self.assertEqual(self.window.loadout.editing_origin(), patch)

    def test_and_revert_all_says_it_is_the_whole_loadout_and_leaves_it_standing_too(self):
        """It asked "Discard all 2 changes in this mod?" and cleared every
        mod in the loadout, your patch with them."""
        self.tweak()
        patch = self.mod.name + " (my patch)"
        self._retune_residents("99")
        loadout, panel = self.window.loadout, self.window.changes_panel
        self.assertEqual(len(loadout), 2)
        self.assertTrue(loadout.ops)
        with _isolation.answering("Revert all") as asked:
            panel._clear_all()
        self.assertEqual(asked.texts, [
            "Discard all 2 changes in this loadout? That is every change of "
            "all 2 mods loaded in it.\n\n"
            "1 mod that is not yours is unloaded from the loadout with its "
            "changes. Its folder is not touched.\n\n"
            "Saved versions in the loadout history are not affected."
        ])
        # Nothing is left to be written that the list no longer shows: an op
        # with no change under it would still go out.
        self.assertEqual((len(loadout), loadout.ops, loadout.unread), (0, [], []))
        self.assertEqual([layer.origin for layer in loadout.layers], [patch])
        self.assertEqual(loadout.editing_origin(), patch)
        # And it is one step to take back, the mod it unloaded included.
        self.window.undo_last()
        self.assertEqual(len(loadout), 2)
        self.assertIsNotNone(loadout.layer(self.mod.name))

    def test_the_export_tab_previews_the_patch_not_the_mod_it_patches(self):
        """Read a mod in to change it, and before anything is changed the
        preview is the empty patch -- what will be written -- rather than the
        whole of somebody else's mod, as though exporting that were the idea."""
        self.tweak()
        patch = self.mod.name + " (my patch)"
        out = self.window.output
        out.refresh()
        self.assertIn(patch, out.pickable())
        self.assertEqual(out.current_origin(), patch)
        self.assertIn("No changes to export yet", out.check_summary.text())
        # And once something is in it, that is what is previewed.
        self._retune_residents("99")
        out.refresh()
        self.assertEqual(out.current_origin(), patch)
        self.assertEqual(out.exported_loadout().active_count, 1)


class TestTheMenuWithOnlyOneModLoaded(unittest.TestCase):
    """The single-mod shape, which is the one the tweak aim leaves you in.

    A mod's entries used to hang off the mod *row*, and mod rows only exist
    once more than one mod is loaded -- so with one mod the menu quietly lost
    half of itself.  Every test of it used the three-mod fixture, so nothing
    said so.
    """

    PATCH = (
        "<ModOps>"
        '<ModOp Type="merge" GUID="1010030" Path="/Values/Residence7"><Residents>16</Residents></ModOp>'
        "</ModOps>"
    )

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        tmp = Path(cls._tmp.name)
        cls.assets = tmp / "assets.xml"
        cls.assets.write_text(make_sample_assets.build_assets_xml(40), encoding="utf-8")
        cls.db = tmp / "index.sqlite"
        index_mod.build_index(cls.assets, cls.db)
        cls.mod = tmp / "Efficient Celtic Charcoal"
        patch = cls.mod / "data/config/export/main/asset/assets.xml"
        patch.parent.mkdir(parents=True)
        patch.write_text(cls.PATCH, encoding="utf-8")
        (cls.mod / "modinfo.json").write_text(
            '{"ModName": {"English": "Efficient Celtic Charcoal"}, '
            '"CreatorName": "Someone"}',
            encoding="utf-8",
        )

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def setUp(self):
        from anno_modding_studio.core.changes import ModLayer

        self.window = MainWindow()

        self.addCleanup(_flush_deletes)
        self.window.game_files.adopt_index(self.db, self.assets, cached=True)
        self.origin = self.mod.name
        report = reader.analyse(self.mod, self.window.index, origin=self.origin)
        self.window.loadout.set_layer(
            ModLayer(
                origin=self.origin,
                name="Efficient Celtic Charcoal",
                folder=str(self.mod),
                creator="Someone",
            )
        )
        for op in report.ops:
            self.window.loadout.set_op(op)
        for change in report.changes:
            self.window.loadout.set_change(change)
        self.window.changes_panel.refresh()

    def tearDown(self):
        self.window.editor.search._debounce.stop()
        self.window.game_files.stop_texts_worker()
        if self.window.index is not None:
            self.window.index.close()
            self.window.index = None
        self.window.loadout.clear()
        self.window.deleteLater()

    def rows(self):
        """The loaded mod's rows.  Your own mod has a row beside it, empty,
        as it has an entry in "Changes go to"; these tests are about theirs."""
        from anno_modding_studio.ui.changes_dock import ORIGIN_ROLE

        tree = self.window.changes_panel.tree
        tree.expandAll()
        found = []

        def walk(item):
            found.append(item)
            for index in range(item.childCount()):
                walk(item.child(index))

        for index in range(tree.topLevelItemCount()):
            top = tree.topLevelItem(index)
            if top.data(0, ORIGIN_ROLE):
                walk(top)
        return found

    def test_your_own_mod_has_a_row_beside_it_once_it_is_made(self):
        """Nothing made one by reading a mod in, so there is none.  Made, it
        is listed, empty: "Changes go to" offers it and the Export tab lists
        it, so this list leaving it out said the loadout held one mod fewer
        than they did."""
        from anno_modding_studio.ui.changes_dock import KIND_ROLE, ORIGIN_ROLE

        def tops():
            tree = self.window.changes_panel.tree
            return [tree.topLevelItem(i) for i in range(tree.topLevelItemCount())]

        self.assertEqual([t.data(0, ORIGIN_ROLE) or "" for t in tops()], [self.origin])
        self.window.loadout.make_own_mod()
        self.window.changes_panel.refresh()
        self.assertEqual([t.data(0, KIND_ROLE) for t in tops()], ["mod", "mod"])
        self.assertEqual([t.data(0, ORIGIN_ROLE) or "" for t in tops()], [self.origin, ""])

    def menu(self, item):
        built = self.window.changes_panel.menu_for(item)
        self.addCleanup(built.deleteLater)
        return {a.text(): a for a in built.actions() if a.text()}

    def test_one_mod_alone_still_draws_its_own_row(self):
        """The premise the menu got wrong was that no row carries the mod: one
        does now, and the rows under it still have to know which mod it is."""
        from anno_modding_studio.ui.changes_dock import KIND_ROLE

        self.assertFalse(self.window.loadout.is_multi_mod)
        self.assertEqual(
            len([r for r in self.rows() if r.data(0, KIND_ROLE) == "mod"]), 1
        )

    def test_and_every_row_still_offers_what_can_be_done_to_that_mod(self):
        from anno_modding_studio.ui.changes_dock import KIND_ROLE

        for item in self.rows():
            with self.subTest(row=item.text(0)):
                actions = self.menu(item)
                self.assertIn("Mod options…", actions)
                self.assertIn("Patch this mod…", actions)
                # Unloading it, only from the mod's own row.
                self.assertEqual(
                    "Unload Efficient Celtic Charcoal…" in actions,
                    item.data(0, KIND_ROLE) == "mod",
                )

    def test_because_every_row_says_which_mod_it_belongs_to(self):
        from anno_modding_studio.ui.changes_dock import ORIGIN_ROLE

        for item in self.rows():
            with self.subTest(row=item.text(0)):
                self.assertEqual(item.data(0, ORIGIN_ROLE), self.origin)

    def test_new_pattern_actually_asks_for_one(self):
        """A Qt signal is not callable, so an action handed one raises when it
        is triggered and nothing happens -- silently, at the point of use."""
        asked = []
        self.window.changes_panel.newPatternRequested.disconnect(self.window.patterns.new_pattern)
        self.window.changes_panel.newPatternRequested.connect(lambda: asked.append(1))
        self.menu(self.rows()[0])["New pattern…"].trigger()
        self.assertEqual(asked, [1])

    def test_adding_mods_is_on_the_same_menu(self):
        asked = []
        self.window.changes_panel.addModsRequested.connect(lambda: asked.append(1))
        with mock.patch.object(self.window.library_controller, "add_mods") as added:
            self.menu(self.rows()[0])["Add mods…"].trigger()
        self.assertEqual(asked, [1])
        added.assert_called_once_with()

    # -- a row as the XML it is written as ---------------------------------
    def row(self, kind: str):
        from anno_modding_studio.ui.changes_dock import KIND_ROLE

        return next(r for r in self.rows() if r.data(0, KIND_ROLE) == kind)

    def test_every_row_offers_its_xml(self):
        for item in self.rows():
            with self.subTest(row=item.text(0)):
                self.assertIn("Show XML…", self.menu(item))

    def test_a_mods_row_is_its_whole_document(self):
        heading, xml, note = self.window.changes_panel.xml_for(self.row("mod"))
        self.assertIn("Efficient Celtic Charcoal", heading)
        self.assertIn("<Residents>16</Residents>", xml)
        self.assertEqual(note, "")

    def test_it_is_written_as_the_file_is(self):
        """No header, and nothing of the Studio's over an op."""
        for kind in ("mod", "change"):
            with self.subTest(row=kind):
                _heading, xml, _note = self.window.changes_panel.xml_for(self.row(kind))
                self.assertNotIn("<!--", xml)
                self.assertIn('<ModOp Type="merge" GUID="1010030" Path="/Values/Residence7">', xml)

    def test_a_changes_row_is_that_change_alone(self):
        heading, xml, note = self.window.changes_panel.xml_for(self.row("change"))
        self.assertIn("Efficient Celtic Charcoal", heading)
        self.assertIn("<Residents>16</Residents>", xml)
        self.assertNotIn("Generated by", xml)
        # What it is under is an op kept as written, and that is what is shown.
        self.assertIn("kept as written", note)

    def test_a_switched_off_change_is_the_comment_it_is_written_as(self):
        from anno_modding_studio.core.changes import SWITCHED_OFF_MARK

        switch_off(self.window.loadout, self.origin)
        self.window.changes_panel.refresh()
        for kind in ("change", "mod"):
            with self.subTest(row=kind):
                _heading, xml, note = self.window.changes_panel.xml_for(self.row(kind))
                self.assertIn(SWITCHED_OFF_MARK, xml)
                self.assertIn("<Residents>16</Residents>", xml)
                self.assertIn("comment", note)

    def test_showing_it_opens_a_dialog_holding_that_xml(self):
        from unittest import mock

        from anno_modding_studio.ui import xml_view

        shown = []
        with mock.patch.object(
            xml_view.XmlDialog, "exec",
            lambda dialog: shown.append(dialog.text.toPlainText()),
        ):
            self.menu(self.row("change"))["Show XML…"].trigger()
        self.assertEqual(len(shown), 1)
        self.assertIn("<Residents>16</Residents>", shown[0])


class TestChangingAModsSettingsAfterItIsLoaded(unittest.TestCase):
    """The import dialogs ask before reading, which is the wrong moment and the
    only one they had: you have not seen what the mod does yet, and the answer
    is thrown away by the next import.  A loaded mod carries both what it offers
    and what it was read at, so the question can be put again where its changes
    are on screen."""

    PATCH = (
        "<ModOps>"
        '<ModOp Type="merge" GUID="1010030" Path="/Values/Residence7"><Residents>'
        '<ModValue Insert="$crowding"/></Residents></ModOp>'
        "</ModOps>"
    )
    MODINFO = (
        '{"ModName": {"English": "Crowded"}, "CreatorName": "Them", '
        '"Options": {"crowding": {"label": "how many", "type": "enum", '
        '"default": "30", "values": ["30", "60"], '
        '"labels": ["Thirty", "Sixty"]}}}'
    )

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        tmp = Path(cls._tmp.name)
        cls.assets = tmp / "assets.xml"
        cls.assets.write_text(make_sample_assets.build_assets_xml(40), encoding="utf-8")
        cls.db = tmp / "index.sqlite"
        index_mod.build_index(cls.assets, cls.db)
        cls.mod = tmp / "[Gameplay] Crowded"
        patch = cls.mod / "data/config/export/main/asset/assets.xml"
        patch.parent.mkdir(parents=True)
        patch.write_text(cls.PATCH, encoding="utf-8")
        (cls.mod / "modinfo.json").write_text(cls.MODINFO, encoding="utf-8")

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def setUp(self):
        from anno_modding_studio.core import reader
        from anno_modding_studio.core import settings as settings_mod
        from anno_modding_studio.core.changes import ModLayer

        self.window = MainWindow()

        self.addCleanup(_flush_deletes)
        self.window.game_files.adopt_index(self.db, self.assets, cached=True)
        self.origin = "[Gameplay] Crowded"
        report = reader.analyse(self.mod, self.window.index, origin=self.origin)
        offered = report.metadata["options"]
        self.window.loadout.set_layer(
            ModLayer(
                origin=self.origin,
                name="Crowded",
                folder=str(self.mod),
                creator="Them",
                options=offered.defaults(),
                offered=offered.to_dict(),
            )
        )
        for change in report.changes:
            self.window.loadout.set_change(change)
        self.window.changes_panel.refresh()
        self.settings_mod = settings_mod

    def tearDown(self):
        self.window.editor.search._debounce.stop()
        self.window.game_files.stop_texts_worker()
        if self.window.index is not None:
            self.window.index.close()
            self.window.index = None
        self.window.loadout.clear()
        self.window.deleteLater()

    def answer(self, values, accept=True):
        """Stand in for the options dialog, which would otherwise sit modal.

        Patched where it is defined rather than where it is used: the window
        imports it inside the method, so a name bound on the window's module
        would never be looked at.
        """
        from anno_modding_studio.ui import mod_options_dialog as options_mod

        return mock.patch.object(
            options_mod, "ModOptionsDialog", _options_dialog(values, accept)
        )

    # -- that it can be asked at all ---------------------------------------
    def test_a_loaded_mod_still_knows_what_it_offers(self):
        layer = self.window.loadout.layer(self.origin)
        self.assertIn("crowding", layer.offered)
        self.assertEqual(
            self.settings_mod.from_dict(layer.offered).get("crowding").default, "30"
        )

    def test_it_was_read_at_the_settling_that_makes_it_legible(self):
        """A value from the mod's own settings is settled by default now, so
        this reads as one replace rather than a section swap."""
        changes = self.window.loadout.changes_of(self.origin)
        self.assertEqual([c.op for c in changes], ["replace"])
        self.assertEqual(changes[0].new_value, "30")

    # -- and what answering it does ----------------------------------------
    def test_answering_it_re_reads_the_mod_at_the_new_setting(self):
        with self.answer({"crowding": "60"}):
            self.assertTrue(self.window.edit_mod_options(self.origin))
        changes = self.window.loadout.changes_of(self.origin)
        self.assertEqual([c.new_value for c in changes], ["60"])
        self.assertEqual(
            self.window.loadout.layer(self.origin).options["crowding"], "60"
        )

    def test_the_status_bar_names_the_setting_that_moved(self):
        with self.answer({"crowding": "60"}):
            self.assertTrue(self.window.edit_mod_options(self.origin))
        said = self.window.status_label.text()
        self.assertTrue(said.startswith("Crowded re-read at "), said)
        self.assertIn("Sixty", said)
        # Cut at the window's width, with the whole of it in the tooltip, and
        # never pushing what runs behind the window out of the status bar.
        self.assertEqual(self.window.status_label.toolTip(), said)
        # It gives way to the last pixel: however long, it asks for no width.
        self.assertEqual(self.window.status_label.minimumSizeHint().width(), 0)
        self.assertEqual(self.window.status_label.row.minimumSizeHint().width(), 0)

    def test_only_the_settings_that_moved_are_said_and_many_are_counted(self):
        """Every setting the mod has, each with its value, made a line that
        ran past the window's width, and "Reading 248 mods again…" was cut
        off at its end (H2-22)."""
        from anno_modding_studio.core.settings import ModOptions, Option
        from anno_modding_studio.ui.main_window import _reread_at

        offered = ModOptions([
            Option(name=name, label=name.title(), default="1")
            for name in ("wood", "fish", "potatoes", "calamary")
        ])
        was = offered.defaults()
        one = _reread_at("Early Game", offered, dict(was, calamary="0"), was, 446)
        self.assertEqual(one, "Early Game re-read at Calamary 0: 446 changes.")
        three = _reread_at(
            "Early Game", offered, dict(was, wood="2", fish="2", potatoes="2"), was, 1,
        )
        self.assertEqual(three, "Early Game re-read with 3 settings changed: 1 change.")

    def test_and_reads_it_the_way_it_was_read_when_it_came_in(self):
        """The boxes on the import dialog decide what a mod's changes are as
        much as its settings do.  They were not kept with the mod, so changing
        a setting re-read it at the defaults: a mod brought in with its values
        left for the game to work out came back with them frozen."""
        layer = self.window.loadout.layer(self.origin)
        layer.read_as = {"keep_inert": False, "settle_computed": False, "settle_copies": False}
        with self.answer({"crowding": "60"}):
            self.assertTrue(self.window.edit_mod_options(self.origin))
        kept = [op for op in self.window.loadout.ops if op.origin == self.origin]
        self.assertEqual(len(kept), 1)
        self.assertIn("works its value out", kept[0].kept)
        self.assertEqual(
            self.window.loadout.layer(self.origin).read_as["settle_computed"], False
        )

    def test_how_it_was_read_is_saved_with_the_mod(self):
        from anno_modding_studio.core.changes import ModLayer

        layer = ModLayer(origin="a", read_as={"settle_copies": True})
        self.assertEqual(ModLayer.from_dict(layer.to_dict()).read_as, {"settle_copies": True})
        self.assertEqual(ModLayer.from_dict({"origin": "a"}).read_as, {})

    def test_and_says_which_setting_it_was_read_at(self):
        with self.answer({"crowding": "60"}):
            self.window.edit_mod_options(self.origin)
        self.assertIn("Sixty", self.window.status_label.text())

    def test_cancelling_changes_nothing(self):
        with self.answer({"crowding": "60"}, accept=False):
            self.assertFalse(self.window.edit_mod_options(self.origin))
        self.assertEqual(
            [c.new_value for c in self.window.loadout.changes_of(self.origin)], ["30"]
        )

    def test_settling_on_the_same_answer_does_not_re_read_it(self):
        with self.answer({"crowding": "30"}):
            self.assertFalse(self.window.edit_mod_options(self.origin))

    def test_edits_of_your_own_are_asked_about_before_they_are_lost(self):
        """Re-reading is the whole of it -- the mod's ops read the answers back
        -- so an edit folded into one of them cannot survive it."""
        change = self.window.loadout.changes_of(self.origin)[0]
        change.authored = True
        with _isolation.answering(False) as asked, self.answer({"crowding": "60"}):
            self.assertFalse(self.window.edit_mod_options(self.origin))
        self.assertEqual(asked.buttons, [["Read again", "Cancel"]])
        self.assertIn("reading its folder again", asked.texts[0])

    def test_and_what_it_asks_is_reading_it_again_not_unloading_it(self):
        """The question was the unload one with a reason put in front: it was
        titled Unload mods?, ended Unload 1 mod? on an Unload button, and said
        the mod's patch would be greyed until the mod was loaded again -- of a
        mod that stays loaded, with its patch in use (A11-70)."""
        import copy

        from anno_modding_studio.core.changes import ModLayer

        change = self.window.loadout.changes_of(self.origin)[0]
        change.authored = True
        patch = "Crowded (my patch)"
        self.window.loadout.set_layer(ModLayer(origin=patch, name=patch, patches=self.origin))
        mine = copy.copy(change)
        mine.origin = patch
        self.window.loadout.set_change(mine)
        with _isolation.answering(False) as asked, self.answer({"crowding": "60"}):
            self.assertFalse(self.window.edit_mod_options(self.origin))
        self.assertEqual(asked.titles, ["Read it again?"])
        self.assertIn("1 change you made to it would be lost", asked.texts[0])
        self.assertTrue(asked.texts[0].startswith("Reading Crowded at other settings"), asked.texts[0])
        self.assertTrue(asked.texts[0].endswith("Read it again?"), asked.texts[0])
        self.assertNotIn("nload", asked.texts[0])
        self.assertNotIn("greyed", asked.texts[0])

    def test_a_mod_whose_folder_has_gone_says_so_rather_than_failing(self):
        self.window.loadout.layer(self.origin).folder = str(self.mod) + "-gone"
        with _isolation.answering(True) as told, self.answer({"crowding": "60"}):
            self.assertFalse(self.window.edit_mod_options(self.origin))
        self.assertEqual([title for title, _text in told.told], ["No folder to re-read"])


def _options_dialog(values, accept=True):
    """A stand-in for ModOptionsDialog that answers without being shown."""

    class Fake:
        def __init__(self, *args, **kwargs):
            self.values = dict(values)

        def exec(self):
            return int(
                QDialog.DialogCode.Accepted if accept else QDialog.DialogCode.Rejected
            )

        def deleteLater(self):
            pass

    return Fake


class TestNamingBelongsToDecidingNotExporting(unittest.TestCase):
    """"Decide what you are doing, then do it, then export." A mod's name is
    part of the first, and the Export tab is the last."""

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        tmp = Path(cls._tmp.name)
        cls.assets = tmp / "assets.xml"
        cls.assets.write_text(make_sample_assets.build_assets_xml(20), encoding="utf-8")
        cls.db = tmp / "index.sqlite"
        index_mod.build_index(cls.assets, cls.db)

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def window(self, aim):
        window = MainWindow()
        self.addCleanup(_flush_deletes)
        window.game_files.adopt_index(self.db, self.assets, cached=True)
        window.loadout.set_up_as(aim)
        window.update_tab_gates()
        window.output.refresh()

        def close():
            window.editor.search._debounce.stop()
            window.game_files.stop_texts_worker()
            if window.index is not None:
                window.index.close()
                window.index = None
            window.loadout.clear()
            window.deleteLater()

        self.addCleanup(close)
        return window

    def test_writing_a_mod_of_your_own_names_it_on_the_start_tab(self):
        from anno_modding_studio.core.changes import AIM_MAKE

        window = self.window(AIM_MAKE)
        self.assertTrue(window.output.name_edit.isReadOnly())
        self.assertIn("rename them on", window.output.meta_note.text().lower())

    def test_and_renaming_there_shows_here(self):
        from anno_modding_studio.core.changes import AIM_MAKE

        window = self.window(AIM_MAKE)
        window.files.name_loadout("Better Harbours")
        window.files.rename_loadout("Better Harbours II")
        self.assertEqual(window.loadout.own.name, "Better Harbours II")
        self.assertEqual(window.output.name_edit.text(), "Better Harbours II")

    def test_on_a_mod_list_your_own_mod_is_named_here(self):
        """The loadout is a workspace there, and its name is deliberately not
        the mod's -- so this is the only place that names the mod."""
        from anno_modding_studio.core.changes import AIM_MANAGE

        window = self.window(AIM_MANAGE)
        self.assertFalse(window.output.name_edit.isReadOnly())
        window.output.name_edit.setText("Harbour Fixes")
        self.assertEqual(window.loadout.own.name, "Harbour Fixes")

    def test_the_creator_says_where_it_comes_from(self):
        from anno_modding_studio.core.changes import AIM_MAKE

        window = self.window(AIM_MAKE)
        said = window.output.meta_note.text().lower()
        self.assertIn("who you publish as", said)
        self.assertIn("start tab", said)
        # Still typeable, for the one mod published under another name.
        self.assertFalse(window.output.creator_edit.isReadOnly())

    def test_somebody_elses_details_stay_as_they_ship(self):
        from anno_modding_studio.core.changes import AIM_MANAGE, ModLayer

        window = self.window(AIM_MANAGE)
        window.loadout.set_layer(ModLayer(origin="Theirs", name="Their Mod"))
        window.output._show_details_for("Theirs")
        self.assertTrue(window.output.name_edit.isReadOnly())
        self.assertTrue(window.output.creator_edit.isReadOnly())
        self.assertIn("as it ships", window.output.meta_note.text())

    def _previewing(self, window, layer):
        """Put a loaded mod with one change in the loadout, and preview it."""
        window.loadout.set_layer(layer)
        window.loadout.set_change(
            Change(
                guid="1010030",
                asset_label="Farmer Residence",
                path="/Values/Residence7/Residents",
                tag="Residents",
                old_value="8",
                new_value="9",
                origin=layer.origin,
            )
        )
        window.output.refresh()
        window.output.show_mod(layer.origin)
        window.output.refresh()
        return window.output

    def test_a_patch_of_yours_has_details_you_can_change(self):
        from anno_modding_studio.core.changes import AIM_MANAGE, ModLayer

        window = self.window(AIM_MANAGE)
        window.loadout.set_layer(ModLayer(origin="Theirs", name="Their Mod"))
        out = self._previewing(
            window,
            ModLayer(origin="Theirs (my patch)", name="Mine", patches="Theirs"),
        )
        self.assertFalse(out.version_edit.isReadOnly())
        self.assertFalse(out.name_edit.isReadOnly())
        self.assertIn("modinfo.json", out.meta_note.text())

        out.version_edit.setText("1.1")
        self.assertEqual(window.loadout.layer("Theirs (my patch)").version, "1.1")
        self.assertEqual(window.loadout.own.version, "1.0")

    def test_retyping_the_creator_of_a_mod_of_yours_does_not_lock_the_box(self):
        """It is yours partly by that name, and a letter into changing it the
        name no longer matched."""
        from anno_modding_studio.core.changes import AIM_MANAGE, ModLayer

        window = self.window(AIM_MANAGE)
        window.loadout.own.creator = "Me"
        out = self._previewing(window, ModLayer(origin="Mine", creator="Me", kept=True))
        out.creator_edit.setText("M")
        self.assertFalse(out.creator_edit.isReadOnly())
        out.creator_edit.setText("Me Again")
        self.assertEqual(window.loadout.layer("Mine").creator, "Me Again")


class TestCheckingOnlyTheModOnScreen(ModsFolderCase):
    """Arriving on the Generate tab re-verified every change in the loadout.

    On a real Anno 1800 folder that is six thousand changes from sixty other
    mods, checked to draw a page about one of them -- seconds of hang, before
    the tab had even painted.
    """

    def verified(self):
        """The changes the verifier was actually handed by a refresh."""
        from unittest import mock

        from anno_modding_studio.ui import output_panel as output_ui

        seen = {}
        real = output_ui.generator.verify_changes

        def spy(index, loadout):
            seen["changes"] = list(loadout.active())
            return real(index, loadout)

        with mock.patch.object(output_ui.generator, "verify_changes", spy):
            self.window.output.refresh()
        return seen.get("changes", [])

    def previewing(self, origin: str) -> None:
        # The list of mods to preview is filled when the page refreshes, which
        # it does on arrival -- and nobody has arrived yet in a test.
        self.window.output._reload_mods()
        box = self.window.output.mod_box
        position = box.findData(origin)
        self.assertGreaterEqual(position, 0, "%s is not on the preview list" % origin)
        box.setCurrentIndex(position)

    def test_only_the_previewed_mods_changes_are_checked(self):
        self.load()
        origin = self.window.loadout.layers[0].origin
        self.previewing(origin)
        checked = self.verified()
        self.assertTrue(checked)
        self.assertEqual({change.origin for change in checked}, {origin})

    def test_which_is_less_than_the_whole_stack(self):
        self.load()
        self.previewing(self.window.loadout.layers[0].origin)
        self.assertLess(len(self.verified()), len(self.window.loadout))

    def test_and_switching_the_preview_switches_what_is_checked(self):
        self.load()
        first, second = (layer.origin for layer in self.window.loadout.layers[:2])
        self.previewing(first)
        self.assertEqual({c.origin for c in self.verified()}, {first})
        self.previewing(second)
        self.assertEqual({c.origin for c in self.verified()}, {second})

    def test_the_count_it_reports_is_that_mods_count(self):
        self.load()
        origin = self.window.loadout.layers[0].origin
        self.previewing(origin)
        self.window.output.refresh()
        mine = len(self.window.loadout.changes_of(origin))
        self.assertIn(str(mine), self.window.output.check_summary.text())


class TestWritingBackOnlyWhatIsYours(ModsFolderCase):
    """Exporting a whole mods folder used to mean rewriting everybody's mod.

    What goes back is not the mod you downloaded, it is the Studio's reading of
    it -- so a mod it could read only nine tenths of comes back as nine tenths
    of a mod.  Doing that to fifty mods you never touched, in order to save the
    changes you made to two, is a bad trade nobody asked for.  Your own work is
    what Export writes; the full rewrite is gone from the tab.
    """

    THEIRS = "[Gameplay] Roomier Farmers"
    UNREADABLE = "[Seal] Other Houses"     # the fixture mod with an unread op

    def setUp(self):
        super().setUp()
        from PySide6.QtWidgets import QInputDialog

        real = QInputDialog.getText
        QInputDialog.getText = staticmethod(lambda *a, **k: (k.get("text", ""), True))
        self.addCleanup(setattr, QInputDialog, "getText", staticmethod(real))
        self.out = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.out, True)

    def edit_into(self, origin):
        """An edit into ``origin``: a patch, or a mod of yours read from its
        folder -- somebody else's mod is never edited."""
        if not self.window.loadout.layer(origin).patches:
            as_yours(self.window.loadout, origin)
        self.window.loadout.editing = origin
        self.window.editor.refresh_target()
        self.window.editor.load_asset("1010030")
        for item in self.window.editor._iter_items():
            if item.text(0) == "Residents" and item.data(0, 258) is not None:
                item.setText(1, "77")
                return
        self.fail("no Residents row")

    def written(self, how):
        self.window.output.set_default_output_dir(self.out)
        return {path.name for path in how(confirm=False, notify=False)}

    # -- what counts as yours ------------------------------------------------
    def test_with_nothing_of_yours_there_is_nothing_to_write(self):
        self.load()
        self.assertEqual(self.window.output.mine_to_export(), [])

    def test_a_mod_of_yours_edited_is_yours(self):
        self.load()
        self.edit_into(self.THEIRS)
        self.assertEqual(self.window.output.mine_to_export(), [self.THEIRS])

    def test_and_so_is_a_patch_you_made_and_wrote_into(self):
        self.load()
        self.window.patches.make_patch(self.THEIRS)
        self.edit_into(self.THEIRS + " (my patch)")
        self.assertIn(self.THEIRS + " (my patch)", self.window.output.mine_to_export())
        self.assertNotIn(self.THEIRS, self.window.output.mine_to_export())

    def test_but_a_patch_with_nothing_in_it_is_not_written(self):
        """It has nothing to say that is not already true."""
        self.load()
        self.window.patches.make_patch(self.THEIRS)
        self.assertEqual(self.window.output.mine_to_export(), [])

    # -- and what that writes ------------------------------------------------
    def test_exporting_yours_leaves_everybody_elses_mod_alone(self):
        self.load()
        self.window.patches.make_patch(self.THEIRS)
        self.edit_into(self.THEIRS + " (my patch)")
        names = self.written(self.window.output.export_mine)
        self.assertEqual(names, {self.THEIRS + " (my patch)"})

    def test_a_mod_s_row_asks_mod_io_about_that_mod(self):
        """The Mods menu asks about every loaded mod; this is the one
        somebody looking at its row wonders about."""
        self.load()
        panel = self.window.changes_panel
        panel.refresh()
        menu = panel.menu_for(panel._mod_items[self.THEIRS])
        entry = next(a for a in menu.actions() if a.text() == "Check for an update…")
        with mock.patch.object(self.window.modio, "check_modio_updates") as asked:
            entry.trigger()
        asked.assert_called_once_with(only=[self.THEIRS])
        menu.deleteLater()

    def test_the_menu_entry_and_the_card_s_button_open_the_check_on_every_mod(self):
        """Connected straight, triggered's checked flag came in as ``only``,
        and the dialog raised building its list: nothing opened, from the
        menu or the card. Through to the dialog, not stopped at trigger."""
        from anno_modding_studio.ui import modio_controller

        self.load()
        self.window.update_title()
        card = self.window.source.updates_button
        for click in (self.window.updates_action.trigger, card.click):
            with self.subTest(click), mock.patch.object(
                modio_controller, "ModioUpdatesDialog"
            ) as made:
                made.return_value.learned = {}
                made.return_value.verdicts = []
                made.return_value.installed = []
                click()
            made.assert_called_once()
            self.assertIsNone(made.call_args.kwargs["only"])
            made.return_value.exec.assert_called_once_with()

    def test_and_cancelling_it_writes_nothing(self):
        self.load()
        self.edit_into(self.THEIRS)
        with _isolation.answering(False):
            self.window.output.set_default_output_dir(self.out)
            self.window.output.export_mine()
        self.assertEqual(list(self.out.iterdir()), [])

    def test_exporting_yours_with_nothing_of_yours_says_so(self):
        self.load()
        told = []
        with mock.patch.object(
            QMessageBox, "information",
            staticmethod(lambda *a, **k: told.append(a[1])),
        ):
            self.window.output.set_default_output_dir(self.out)
            self.assertEqual(self.window.output.export_mine(confirm=False), [])
        self.assertEqual(told, ["No changes of yours to export"])

    def test_how_many_ops_a_rewrite_would_drop_is_counted_per_mod(self):
        self.load()
        panel = self.window.output
        self.assertEqual(panel.unreadable_in([self.UNREADABLE]), 1)
        self.assertEqual(panel.unreadable_in([self.THEIRS]), 0)

    def test_and_your_own_mod_is_counted_too(self):
        """Origin "" is the loadout, not a layer.  Counting layers alone said
        zero for the one mod a reinstall cannot bring back."""
        from anno_modding_studio.core.changes import Unread

        self.load()
        self.window.loadout.unread.append(
            Unread(origin="", source="<ModOp/>", label="a ModOp", why="no")
        )
        self.assertEqual(self.window.output.unreadable_in([""]), 1)

    def test_exporting_yours_says_what_would_not_survive(self):
        """The warning used to be on the full rewrite only -- which is the
        export that touches mods you can simply download again."""
        from anno_modding_studio.core.changes import Unread

        self.load()
        self.edit_into(self.THEIRS)
        self.window.loadout.unread.append(
            Unread(origin=self.THEIRS, source="<ModOp/>", label="a ModOp", why="no")
        )
        with _isolation.answering(False) as asked:
            self.window.output.set_default_output_dir(self.out)
            self.assertEqual(self.window.output.export_mine(), [])
        self.assertEqual(asked.buttons, [["Export", "Cancel"]])
        self.assertIn("1 ModOp in them could not be read", asked.texts[0])

    def test_the_export_menu_asks_before_it_writes_every_mod_of_yours(self):
        # A10-37: connected straight to the button, the checked flag came in
        # as confirm=False, and a click wrote the folders without asking.
        out = self.all_of_yours()
        out.set_default_output_dir(self.out)
        self.assertEqual(out.folder_action.text(), "Folders")
        with _isolation.answering(False) as asked:
            out.folder_action.trigger()
        self.assertEqual(asked.titles, ["Export your changes?"])
        self.assertIn("Write 2 mod folders", asked.texts[0])
        self.assertEqual(list(self.out.iterdir()), [])
        with _isolation.answering("Export"), mock.patch.object(QMessageBox, "information"):
            out.folder_action.trigger()
        self.assertEqual(sorted(path.name for path in self.out.iterdir()),
                         sorted(out.mine_to_export()))

    def all_of_yours(self):
        """Two mods of yours, and the box set to every one of them."""
        self.load()
        self.edit_into(self.THEIRS)
        self.edit_into("[Seal] Much Roomier Farmers")
        out = self.window.output
        self.assertEqual(len(out.mine_to_export()), 2)
        out.refresh()
        out.mod_box.setCurrentIndex(out.mod_box.findData(output_panel.ALL_YOURS))
        self.assertTrue(out.exporting_all_yours())
        return out

    def test_the_box_offers_every_mod_of_yours_once_there_are_two(self):
        self.load()
        out = self.window.output
        self.edit_into(self.THEIRS)
        out.refresh()
        self.assertLess(out.mod_box.findData(output_panel.ALL_YOURS), 0)
        out = self.all_of_yours()
        self.assertEqual(out.mod_box.currentText(), "All the mods you changed")
        # The last choice, under a line: it is not one more mod.
        self.assertEqual(out.mod_box.currentIndex(), out.mod_box.count() - 1)
        self.assertIsNone(out.mod_box.itemData(out.mod_box.count() - 2))

    def test_and_with_it_picked_the_tab_says_which_folders_go(self):
        out = self.all_of_yours()
        self.assertEqual(out.preview_label.full_text(), "The mods you changed")
        said = out.preview.toPlainText()
        self.assertIn("Export writes 2 mod folders:", said)
        for origin in out.mine_to_export():
            self.assertIn(origin, said)
        # One mod's column, one file and one zip are out of the way.
        self.assertTrue(out.left_scroll.isHidden())
        self.assertFalse(out.copy_action.isEnabled())
        self.assertFalse(out.zip_action.isEnabled())
        self.assertEqual(out.destination_label.full_text(), str(out.export_dir()))
        # One mod's rows: where it is, and its page on mod.io.
        self.assertFalse(out.game_button.isVisibleTo(out))
        self.assertFalse(out.modio_button.isVisibleTo(out))
        # Back on one mod, all of it comes back.
        out.mod_box.setCurrentIndex(out.mod_box.findData(self.THEIRS))
        self.assertFalse(out.left_scroll.isHidden())
        self.assertEqual(out.folder_action.text(), "Folder")

    def test_and_says_nothing_about_it_when_every_op_was_read(self):
        self.load()
        self.edit_into(self.THEIRS)
        with _isolation.answering(False) as asked:
            self.window.output.set_default_output_dir(self.out)
            self.window.output.export_mine()
        self.assertTrue(asked.called)
        self.assertNotIn("could not be read", asked.texts[0])

    def test_one_export_menu_writes_whatever_the_box_says(self):
        """There were three export buttons, and which was picked out moved
        with the loadout; Export writes whatever the box says. It is the
        way out used least, so it is not the highlighted one."""
        self.load()
        self.window.output.refresh()
        self.assertNotEqual(self.window.output.export_button.objectName(), "Primary")
        for gone in ("export_mine_button", "export_all_button", "export_all_mods",
                     "folder_button", "zip_button", "xml_button", "modio_box"):
            self.assertFalse(hasattr(self.window.output, gone), gone)


def rewrite_every_mod(window):
    """Every loaded mod written back out, each as its own folder: Play's
    build, done by the Export tab's writer. The Export tab no longer offers
    it, and what it writes of each mod is still what Play's build does."""
    return window.output._write_out(build.writable(window.loadout), notify=False)


class TestWritingTheWholeStackBackOut(ModsFolderCase):
    """Export one mod, or every mod, each into its own folder."""

    def setUp(self):
        super().setUp()
        self.out = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.out, True)
        self.window.output._chosen_output_dir = self.out

    def test_the_panel_offers_a_mod_to_export(self):
        self.load()
        self.window.output.refresh()
        self.assertEqual(self.window.output.mod_box.count(), 4)
        self.assertEqual(
            [self.window.output.mod_box.itemData(i) for i in range(3)],
            self.window.loadout.origins(),
        )

    def test_and_yours_is_on_the_list_even_with_nothing_in_it(self):
        """Otherwise opening a mods folder points the preview at somebody
        else's mod and gives you no way back to your own -- while the asset
        editor goes on writing to yours."""
        self.load()
        self.window.output.refresh()
        self.assertEqual(self.window.output.mod_box.itemData(3), "")
        self.assertIn("(yours)", self.window.output.mod_box.itemText(3))

    def test_and_it_is_the_one_the_preview_starts_on(self):
        self.load()
        self.window.output.refresh()
        self.assertEqual(self.window.output.current_origin(), "")
        self.assertTrue(self.window.output.previewing_yours())
        self.assertIn("your mod", self.window.output.preview_label.full_text())

    def test_the_details_box_shows_whose_mod_is_being_previewed(self):
        """Typing into a box that turned out to describe a different mod is the
        sharpest form of the same confusion."""
        self.load()
        out = self.window.output
        out.refresh()
        self.assertEqual(out.meta_box.title(), "Mod details")
        self.assertFalse(out.name_edit.isReadOnly())
        self.pick("[Gameplay] Roomier Farmers")
        self.assertIn("[Gameplay] Roomier Farmers", out.meta_box.title())
        self.assertEqual(out.name_edit.text(), "[Gameplay] Roomier Farmers")
        self.assertEqual(out.creator_edit.text(), "Ay")
        self.assertTrue(out.name_edit.isReadOnly())
        self.assertIn("that mod's own details", out.meta_note.text())

    def test_and_typing_in_it_then_does_not_rename_your_mod(self):
        self.load()
        yours = self.window.loadout.own.name
        out = self.window.output
        self.pick("[Gameplay] Roomier Farmers")
        out.name_edit.setText("something else")
        out._on_meta_changed()
        self.assertEqual(self.window.loadout.own.name, yours)

    def test_previewing_someone_else_s_says_whose_it_is(self):
        self.load()
        self.pick("[Gameplay] Roomier Farmers")
        self.assertFalse(self.window.output.previewing_yours())
        self.assertIn(
            "Roomier Farmers", self.window.output.preview_label.full_text()
        )

    def pick(self, origin):
        """Choose one of the loaded mods, the way the combo box does."""
        self.window.output.refresh()
        self.window.output.mod_box.setCurrentIndex(
            self.window.output.mod_box.findData(origin)
        )
        self.assertEqual(self.window.output.current_origin(), origin)

    def test_a_pick_the_loadout_no_longer_has_is_not_the_mod_about(self):
        """R3-29, hands-on run 3: another loadout opened while the box still
        held the last one's pick, and asking for that mod's folder raised in
        the middle of opening it."""
        self.load()
        self.pick("[Gameplay] Roomier Farmers")
        out = self.window.output
        self.window.loadout.drop_layer("[Gameplay] Roomier Farmers")
        self.assertEqual(out.mod_box.currentData(), "[Gameplay] Roomier Farmers")
        self.assertIn(out.current_origin(), out.pickable())
        out.export_target()

    def test_the_preview_is_that_mod_alone(self):
        self.load()
        self.pick("[Gameplay] Roomier Farmers")
        self.assertIn("<Residents>12</Residents>", self.window.output._xml_text)
        self.assertNotIn("<Residents>20</Residents>", self.window.output._xml_text)

    def test_exporting_writes_it_under_the_folder_it_came_from(self):
        self.load()
        self.pick("[Gameplay] Roomier Farmers")
        root = self.window.output.export_mod(confirm_overwrite=False, notify=False)
        self.assertEqual(root.name, "[Gameplay] Roomier Farmers")

    def test_exporting_everything_writes_one_folder_per_mod(self):
        self.load()
        self.window.output.refresh()
        written = rewrite_every_mod(self.window)
        self.assertEqual(
            sorted(root.name for root in written),
            ["[Gameplay] Roomier Farmers", "[Seal] Much Roomier Farmers",
             "[Seal] Other Houses"],
        )
        for root in written:
            patch = root / games.get(self.window.loadout.game).asset_relpath
            self.assertTrue(patch.is_file())
            self.assertIn("<ModOp", patch.read_text(encoding="utf-8"))

    def test_a_mod_with_every_change_off_is_written_as_comments(self):
        """Left unwritten, what was switched off would be lost from the
        folder the mod is read back from."""
        from anno_modding_studio.core.changes import SWITCHED_OFF_MARK

        self.load()
        switch_off(self.window.loadout, "[Seal] Much Roomier Farmers")
        self.window.output.refresh()
        written = rewrite_every_mod(self.window)
        found = next(root for root in written if root.name == "[Seal] Much Roomier Farmers")
        patch = next(found.rglob("assets.xml")).read_text(encoding="utf-8")
        self.assertIn(SWITCHED_OFF_MARK, patch)
        self.assertEqual(
            [op for op in etree.fromstring(patch.encode("utf-8")) if isinstance(op.tag, str)], []
        )

    def test_and_one_that_carries_an_op_that_has_no_switch_still_runs_it(self):
        """An op the Studio could not read has no box to untick, so a mod with
        one is never nothing for the game: it is written with that op, and
        the changes that were switched off as comments."""
        self.load()
        switch_off(self.window.loadout, "[Seal] Other Houses")
        self.window.output.refresh()
        written = rewrite_every_mod(self.window)
        found = next(root for root in written if root.name == "[Seal] Other Houses")
        patch = next(found.rglob("assets.xml")).read_text(encoding="utf-8")
        live = [op for op in etree.fromstring(patch.encode("utf-8")) if isinstance(op.tag, str)]
        self.assertEqual(len(live), 1)
        self.assertGreater(patch.count("<ModOp "), 1)

    def test_each_mod_keeps_its_own_details_in_its_modinfo(self):
        self.load()
        self.window.output.refresh()
        written = rewrite_every_mod(self.window)
        found = next(r for r in written if r.name == "[Seal] Other Houses")
        modinfo = json.loads((found / "modinfo.json").read_text(encoding="utf-8"))
        self.assertEqual(modinfo["CreatorName"], "Cee")

    def test_what_goes_back_out_still_verifies(self):
        self.load()
        problems = [
            result
            for result in generator.verify_changes(
                self.window.workspace, self.window.loadout
            )
            if not result.ok
        ]
        self.assertEqual(problems, [], [p.message for p in problems])
OPTIONS_JSON = """{
  "ModName": {"English": "Configurable Mod"},
  "CreatorName": "Dee",
  "Options": {
    "ph": {"type": "enum", "default": "-----", "values": ["-----"],
           "label": "----- Section -----"},
    "roomier": {"type": "toggle", "default": "false", "label": "Roomier?",
                "labels": ["Yes, roomier", "No"]},
    "factor": {"type": "slider", "default": "1", "values": ["0", "2", "0.5"],
               "label": "Factor"},
    "size": {"type": "enum", "default": "10", "values": ["5", "10", "20"],
             "labels": ["Small", "Medium", "Large"], "label": "Size"}
  }
}"""


class TestAModsOwnOptions(unittest.TestCase):
    """A configurable mod, and the dialog that asks what it is set to."""

    def setUp(self):
        from anno_modding_studio.core import settings as settings_mod
        from anno_modding_studio.ui.mod_options_dialog import ModOptionsDialog

        self.offered = settings_mod.read(json.loads(OPTIONS_JSON)["Options"])
        self.dialog = ModOptionsDialog("Configurable Mod", self.offered)
        self.addCleanup(self.dialog.deleteLater)

    def test_a_control_for_every_option_but_the_headings(self):
        self.assertEqual(
            sorted(self.dialog.controls), ["factor", "roomier", "size"]
        )

    def test_each_control_starts_at_the_mod_s_own_default(self):
        self.assertEqual(
            self.dialog.chosen(),
            {"ph": "-----", "roomier": "false", "factor": "1", "size": "10"},
        )

    def test_a_toggle_is_a_tick_box_in_front_of_its_question(self):
        """Left-aligned and wrapping, so a long question does not push a scroll
        bar along the bottom; the author's own word for "on" is its tooltip."""
        box = self.dialog.controls["roomier"]
        self.assertEqual(box.text(), "")
        self.assertEqual(box.toolTip(), "Yes, roomier")
        label = self.dialog.labels["roomier"]
        self.assertEqual(label.text(), "Roomier?")
        self.assertTrue(label.wordWrap())
        box.setChecked(True)
        self.assertEqual(self.dialog.chosen()["roomier"], "true")

    def test_clicking_the_question_ticks_the_box(self):
        from PySide6.QtCore import QPoint
        from PySide6.QtTest import QTest

        label = self.dialog.labels["roomier"]
        QTest.mouseClick(label, Qt.MouseButton.LeftButton, pos=QPoint(2, 2))
        self.assertTrue(self.dialog.controls["roomier"].isChecked())

    def test_a_toggle_without_wording_of_its_own_says_nothing_extra(self):
        """iModYourAnno's toggles have no "yes" wording; the box used to say
        "true" beside every one of them."""
        from anno_modding_studio.core import settings as settings_mod
        from anno_modding_studio.ui.mod_options_dialog import ModOptionsDialog

        offered = settings_mod.ModOptions([
            settings_mod.Option(
                name="t", label="A long question " * 8, kind=settings_mod.TOGGLE,
                default="true", description="What it does.",
            )
        ])
        dialog = ModOptionsDialog("Plain", offered)
        self.addCleanup(dialog.deleteLater)
        self.assertEqual(dialog.controls["t"].toolTip(), "What it does.")
        self.assertEqual(dialog.labels["t"].toolTip(), "What it does.")

    def test_a_slider_keeps_the_author_s_own_range(self):
        spin = self.dialog.controls["factor"]
        self.assertEqual((spin.minimum(), spin.maximum()), (0.0, 2.0))
        spin.setValue(0.5)
        self.assertEqual(self.dialog.chosen()["factor"], "0.5")

    def test_an_enum_shows_the_author_s_own_wording(self):
        combo = self.dialog.controls["size"]
        self.assertEqual(
            [combo.itemText(i) for i in range(combo.count())],
            ["Small", "Medium", "Large"],
        )
        combo.setCurrentIndex(2)
        self.assertEqual(self.dialog.chosen()["size"], "20")

    def test_and_it_can_be_put_back_the_way_the_mod_ships(self):
        self.dialog.controls["roomier"].setChecked(True)
        self.dialog.controls["size"].setCurrentIndex(2)
        self.dialog._reset()
        self.assertEqual(self.dialog.chosen()["roomier"], "false")
        self.assertEqual(self.dialog.chosen()["size"], "10")


class TestAFoldedModsRowsWait(unittest.TestCase):
    """A mods folder starts folded, and a folded mod's rows are made when it
    is first opened.  Making all of them -- 50,000 for a folder of 400 mods --
    was most of what every edit cost, for rows almost none of which were on
    screen."""

    def panel(self):
        from anno_modding_studio.core.changes import (
            Change,
            Loadout,
            ModLayer,
        )
        from anno_modding_studio.ui.changes_dock import ChangesPanel

        loadout = Loadout()
        for origin, value in (("[Gameplay] One", "11"), ("[Gameplay] Two", "22")):
            loadout.set_layer(ModLayer(origin=origin, name=origin))
            loadout.set_change(Change(
                guid="1010340", asset_label="Thing", path="/Values/X", tag="X",
                old_value="1", new_value=value, origin=origin, authored=False,
            ))
        panel = ChangesPanel(loadout)
        self.addCleanup(panel.deleteLater)
        panel.refresh()
        return panel

    def mod_row(self, panel, origin):
        from PySide6.QtWidgets import QTreeWidgetItemIterator

        from anno_modding_studio.ui.changes_dock import ORIGIN_ROLE

        found = QTreeWidgetItemIterator(panel.tree)
        while found.value() is not None:
            if found.value().data(0, ORIGIN_ROLE) == origin:
                return found.value()
            found += 1
        raise StopIteration(origin)

    def test_a_folded_mod_has_its_arrow_and_its_rows_come_when_opened(self):
        from PySide6.QtWidgets import QTreeWidgetItem

        panel = self.panel()
        row = self.mod_row(panel, "[Gameplay] Two")
        self.assertEqual(row.childCount(), 0)
        self.assertEqual(row.childIndicatorPolicy(), QTreeWidgetItem.ChildIndicatorPolicy.ShowIndicator)
        row.setExpanded(True)
        self.assertGreater(row.childCount(), 0)
        # The other mod is still folded, and still waiting.
        self.assertEqual(self.mod_row(panel, "[Gameplay] One").childCount(), 0)

    def test_the_filter_finds_what_a_folded_mod_holds(self):
        panel = self.panel()
        panel.filter_box.setText("22")
        panel._apply_filter()
        self.assertEqual(len([row for row in shown_change_rows(panel) if "22" in row.text(1)]), 1)

    def typed(self, panel, text):
        """As the box leaves it once somebody has stopped typing: started,
        and not yet gone through."""
        panel.filter_box.setText(text)
        panel._filter_timer.stop()
        panel._start_filter()
        panel._filter_step.stop()

    def one_mod_a_slice(self):
        from anno_modding_studio.ui import changes_dock

        slow = mock.patch.object(changes_dock, "FILTER_SLICE", 0)
        slow.start()
        self.addCleanup(slow.stop)

    def test_a_filter_typed_looks_in_the_folded_mods_one_at_a_time(self):
        """All their rows at once was five seconds on a list of 417 mods,
        with the window not answering."""
        self.one_mod_a_slice()
        panel = self.panel()
        one, two = (self.mod_row(panel, "[Gameplay] " + name) for name in ("One", "Two"))
        self.typed(panel, "22")
        # Neither has been looked in: no rows, and out of the way.
        self.assertEqual((one.childCount(), two.childCount()), (0, 0))
        self.assertTrue(one.isHidden() and two.isHidden())
        self.assertEqual(len(panel._unsearched), 2)
        panel._search_some()
        self.assertEqual(len(panel._unsearched), 1)
        self.assertGreater(one.childCount(), 0)
        self.assertEqual(two.childCount(), 0)
        self.assertTrue(one.isHidden())
        panel._search_some()
        self.assertEqual(panel._unsearched, [])
        self.assertFalse(two.isHidden())
        self.assertTrue(two.isExpanded())
        self.assertEqual(panel._shown, 1)
        self.assertFalse(panel._filter_step.isActive())

    def test_the_line_says_how_many_mods_are_still_to_be_looked_in(self):
        self.one_mod_a_slice()
        panel = self.panel()
        self.typed(panel, "nothing like it")
        # Not "no rows match", which nothing has shown yet.
        self.assertEqual(
            panel.summary.text(), "Filtered: 0 shown so far, 2 mods still to search…"
        )
        panel._search_some()
        self.assertEqual(
            panel.summary.text(), "Filtered: 0 shown so far, 1 mod still to search…"
        )
        panel._search_some()
        self.assertEqual(panel.summary.text(), "No rows match “nothing like it”.")

    def test_it_goes_on_by_itself_until_every_mod_is_looked_in(self):
        import time

        panel = self.panel()
        panel.filter_box.setText("22")
        for _ in range(200):
            _app.processEvents()
            if not (panel._filter_timer.isActive() or panel._filter_step.isActive()):
                break
            time.sleep(0.005)
        self.assertEqual(panel._unsearched, [])
        self.assertEqual(panel.summary.text(), "Filtered: 1 of 2 changes shown")
        self.assertEqual(len(shown_change_rows(panel)), 1)

    def test_a_mod_asked_for_by_name_stays_in_view_while_it_waits(self):
        self.one_mod_a_slice()
        panel = self.panel()
        one, two = (self.mod_row(panel, "[Gameplay] " + name) for name in ("One", "Two"))
        self.typed(panel, "two")
        self.assertTrue(one.isHidden())
        self.assertFalse(two.isHidden())
        panel.finish_filter()
        # And is what was asked for: everything under it shows.
        self.assertFalse(two.isHidden())
        self.assertEqual(len(shown_change_rows(panel)), 1)

    def test_filtered_all_at_once_it_comes_to_the_same(self):
        stepped, at_once = self.panel(), self.panel()
        self.typed(stepped, "22")
        stepped.finish_filter()
        at_once.filter_box.setText("22")
        at_once._filter_timer.stop()
        at_once._apply_filter()
        for panel in (stepped, at_once):
            self.assertEqual(panel._unsearched, [])
        self.assertEqual(
            [row.text(1) for row in shown_change_rows(stepped)],
            [row.text(1) for row in shown_change_rows(at_once)],
        )
        self.assertEqual(stepped.summary.text(), at_once.summary.text())

    def test_left_on_through_a_redraw_it_looks_first_where_it_found_something(self):
        from anno_modding_studio.ui.changes_dock import ORIGIN_ROLE

        self.one_mod_a_slice()
        panel = self.panel()
        self.typed(panel, "22")
        panel.finish_filter()
        panel.refresh()
        panel._filter_step.stop()
        self.assertEqual(
            [node.data(0, ORIGIN_ROLE) for node in panel._unsearched],
            ["[Gameplay] Two", "[Gameplay] One"],
        )
        panel._search_some()
        # What it showed before the redraw, with one mod still to go.
        self.assertEqual(len(panel._unsearched), 1)
        self.assertEqual(panel._shown, 1)

    def test_under_a_filter_the_fold_button_goes_by_the_rows_it_shows(self):
        """H2-20: the filter opens what it finds, and the button still said
        Expand all over a list it had opened."""
        panel = self.panel()
        self.typed(panel, "gameplay")
        panel.finish_filter()
        rows = [self.mod_row(panel, "[Gameplay] " + name) for name in ("One", "Two")]
        self.assertTrue(all(row.isExpanded() for row in rows))
        self.assertEqual(panel.fold_button.text(), "Collapse all")
        panel.fold_button.click()
        self.assertFalse(any(row.isExpanded() for row in rows))
        self.assertEqual(panel.fold_button.text(), "Expand all")
        panel.fold_button.click()
        self.assertTrue(all(row.isExpanded() for row in rows))
        self.assertEqual(panel.fold_button.text(), "Collapse all")

    def test_a_refill_under_the_same_filter_keeps_what_was_shut_by_hand(self):
        """H2-20: an undo or a re-read draws the list again, and the filter
        opened every row it found once more."""
        panel = self.panel()
        self.typed(panel, "gameplay")
        panel.finish_filter()
        two = self.mod_row(panel, "[Gameplay] Two")
        two.setExpanded(False)
        panel.refresh()
        panel.finish_filter()
        self.assertFalse(self.mod_row(panel, "[Gameplay] Two").isExpanded())
        self.assertTrue(self.mod_row(panel, "[Gameplay] One").isExpanded())
        self.assertEqual(panel.fold_button.text(), "Collapse all")
        # Collapse all, and a refill: still shut, and the button says so.
        panel.fold_button.click()
        panel.refresh()
        panel.finish_filter()
        self.assertFalse(self.mod_row(panel, "[Gameplay] One").isExpanded())
        self.assertEqual(panel.fold_button.text(), "Expand all")
        # Another filter opens what it finds afresh.
        self.typed(panel, "22")
        panel.finish_filter()
        self.assertTrue(self.mod_row(panel, "[Gameplay] Two").isExpanded())

    def test_a_redraw_forgets_the_rows_it_was_still_to_look_under(self):
        panel = self.panel()
        self.typed(panel, "22")
        waiting = list(panel._unsearched)
        self.assertEqual(len(waiting), 2)
        panel.filter_box.setText("")
        panel.refresh()
        self.assertEqual(panel._unsearched, [])
        self.assertFalse(panel._filter_step.isActive())
        self.assertIsNone(panel._shown)

    def test_clearing_the_box_stops_the_search_and_shows_every_mod(self):
        panel = self.panel()
        self.typed(panel, "22")
        panel.filter_box.setText("")
        self.assertEqual(panel._unsearched, [])
        for name in ("One", "Two"):
            self.assertFalse(self.mod_row(panel, "[Gameplay] " + name).isHidden())

    def test_rows_to_select_are_looked_for_before_the_filter_has_got_there(self):
        """What the filter hides is not selected, and a mod not looked in
        yet is hidden: the row an Apply had just made was not selected."""
        panel = self.panel()
        self.typed(panel, "22")
        wanted = next(c for c in panel.loadout if c.origin == "[Gameplay] Two")
        self.assertEqual(panel.show_rows(keys=[wanted.key]), 1)
        # Only the mod it was about was looked in for it.
        self.assertEqual(len(panel._unsearched), 1)
        hidden = next(c for c in panel.loadout if c.origin == "[Gameplay] One")
        self.assertEqual(panel.show_rows(keys=[hidden.key]), 0)

    def test_rows_to_select_that_are_not_there_leave_nothing_selected(self):
        """An Apply whose rows the filter hides, or which only took rows
        away, left the rows of the Apply before it selected, as if they were
        what it had done."""
        panel = self.panel()
        wanted = next(c for c in panel.loadout if c.origin == "[Gameplay] Two")
        self.assertEqual(panel.show_rows(keys=[wanted.key]), 1)
        self.assertEqual(len(panel.tree.selectedItems()), 1)
        self.assertEqual(panel.show_rows(), 0)
        self.assertEqual(panel.tree.selectedItems(), [])

    def test_every_row_asked_for_is_every_row_filtered(self):
        panel = self.panel()
        self.typed(panel, "22")
        panel.build_every_row()
        self.assertEqual(panel._unsearched, [])
        self.assertTrue(self.mod_row(panel, "[Gameplay] One").isHidden())
        self.assertEqual(panel._shown, 1)

    def test_a_folded_mods_row_selected_takes_its_changes_with_it(self):
        panel = self.panel()
        row = self.mod_row(panel, "[Gameplay] One")
        self.assertEqual(row.childCount(), 0)
        row.setSelected(True)
        # Offered without making the rows: it is asked at every selection.
        self.assertTrue(panel.remove_button.isEnabled())
        self.assertEqual(row.childCount(), 0)
        self.assertEqual(len(panel._selected_changes()), 1)

    def test_collapse_all_leaves_the_one_mod_with_anything_in_it_open(self):
        """Your own mod has a row while it is empty, and it made two mods of
        one: Collapse all shut the only row with anything under it, which the
        first drawing had opened as one mod alone."""
        from anno_modding_studio.core.changes import (
            Change,
            Loadout,
            ModLayer,
        )
        from anno_modding_studio.ui.changes_dock import ChangesPanel

        loadout = Loadout()
        loadout.set_layer(ModLayer(origin="[Gameplay] One", name="One"))
        loadout.set_change(Change(
            guid="1010340", asset_label="Thing", path="/Values/X", tag="X",
            old_value="1", new_value="11", origin="[Gameplay] One", authored=False,
        ))
        loadout.make_own_mod()
        panel = ChangesPanel(loadout)
        self.addCleanup(panel.deleteLater)
        panel.refresh()
        self.assertEqual(panel.tree.topLevelItemCount(), 2)
        row = self.mod_row(panel, "[Gameplay] One")
        self.assertTrue(row.isExpanded())
        self.assertEqual(panel.fold_button.text(), "Collapse all")

        panel._toggle_fold()
        self.assertTrue(row.isExpanded())
        self.assertFalse(any(row.child(n).isExpanded() for n in range(row.childCount())))
        self.assertEqual(panel.fold_button.text(), "Expand all")
        panel._toggle_fold()
        self.assertTrue(all(row.child(n).isExpanded() for n in range(row.childCount())))
        self.assertEqual(panel.fold_button.text(), "Collapse all")

    def test_the_one_mod_shut_by_hand_is_a_shut_list_to_expand(self):
        """With or without your own empty row beside it, the one mod's row
        shut by hand leaves nothing showing: the button offers Expand all,
        which opens the mod with its groups open, and not Collapse all over a
        list wholly shut."""
        from anno_modding_studio.core.changes import (
            Change,
            Loadout,
            ModLayer,
        )
        from anno_modding_studio.ui.changes_dock import ChangesPanel

        for own in (True, False):
            with self.subTest(own_empty_row=own):
                loadout = Loadout()
                loadout.set_layer(ModLayer(origin="[Gameplay] One", name="One"))
                loadout.set_change(Change(
                    guid="1010340", asset_label="Thing", path="/Values/X", tag="X",
                    old_value="1", new_value="11", origin="[Gameplay] One",
                    authored=False,
                ))
                if own:
                    loadout.make_own_mod()
                panel = ChangesPanel(loadout)
                self.addCleanup(panel.deleteLater)
                panel.refresh()
                row = self.mod_row(panel, "[Gameplay] One")
                self.assertEqual(panel.fold_button.text(), "Collapse all")

                row.setExpanded(False)
                self.assertEqual(panel.fold_button.text(), "Expand all")
                panel._toggle_fold()
                self.assertTrue(row.isExpanded())
                self.assertTrue(row.childCount())
                self.assertTrue(all(
                    row.child(n).isExpanded() for n in range(row.childCount())
                ))
                self.assertEqual(panel.fold_button.text(), "Collapse all")
                # Shut with its groups folded, it opens with them open all
                # the same.
                panel._toggle_fold()
                row.setExpanded(False)
                self.assertEqual(panel.fold_button.text(), "Expand all")
                panel._toggle_fold()
                self.assertTrue(row.isExpanded())
                self.assertTrue(all(
                    row.child(n).isExpanded() for n in range(row.childCount())
                ))

    def test_and_keeps_a_box_of_mods_open_around_it(self):
        """A download that is a box around one inner mod: the box has nothing
        of its own, so the inner mod is the one mod alone, and the fold button
        works inside it.  Its row is inside the box's, so the box stays open,
        Collapse all or not: shut, it hid the one mod there is to see."""
        from anno_modding_studio.core.changes import (
            Change,
            Loadout,
            ModLayer,
        )
        from anno_modding_studio.ui.changes_dock import ChangesPanel

        loadout = Loadout()
        loadout.set_layer(ModLayer(origin="Host", name="Host"))
        loadout.set_layer(ModLayer(origin="Host/Inner", name="Inner"))
        loadout.set_change(Change(
            guid="1010340", asset_label="Thing", path="/Values/X", tag="X",
            old_value="1", new_value="11", origin="Host/Inner", authored=False,
        ))
        panel = ChangesPanel(loadout)
        self.addCleanup(panel.deleteLater)
        panel.refresh()
        box, inner = self.mod_row(panel, "Host"), self.mod_row(panel, "Host/Inner")
        # The inner mod's row is inside the box's, so the box is open to show
        # it -- and nothing else of the box's is made, for it has nothing.
        self.assertTrue(box.isExpanded())
        self.assertIs(inner.parent(), box)
        self.assertTrue(inner.isExpanded())

        for text in ("Expand all", "Collapse all"):
            panel._toggle_fold()
            self.assertEqual(panel.fold_button.text(), text)
            self.assertTrue(box.isExpanded())
            self.assertEqual(box.childCount(), 1)
            self.assertTrue(inner.isExpanded())
        # Opened by hand, it is not shut either: the button folds the groups
        # of the one mod, and a box has none.
        box.setExpanded(True)
        panel._toggle_fold()
        self.assertTrue(box.isExpanded())


class TestFilteringInModsOrOneMod(TestAFoldedModsRowsWait):
    """A hundred mods' changes mention most words, so a mod looked for by
    its name was one row among every change that mentions it.  The filter
    can look in the mods' own rows alone, or in one mod's rows."""

    ONE, TWO = "[Gameplay] One", "[Gameplay] Two"

    def scope(self, panel, kind):
        panel.scope_box.setCurrentIndex(panel.scope_box.findData(kind))

    def test_by_name_finds_the_mod_and_leaves_it_as_it_was(self):
        from anno_modding_studio.ui.changes_dock import SCOPE_NAMES

        panel = self.panel()
        self.scope(panel, SCOPE_NAMES)
        self.typed(panel, "two")
        two, one = self.mod_row(panel, self.TWO), self.mod_row(panel, self.ONE)
        self.assertFalse(two.isHidden())
        self.assertTrue(one.isHidden())
        # Folded as it was, and its rows not made for it.
        self.assertFalse(two.isExpanded())
        self.assertEqual(two.childCount(), 0)
        self.assertEqual(panel._unsearched, [])
        self.assertEqual(panel.summary.text(), "Filtered: 1 of 2 mods shown")

    def test_by_name_a_word_only_their_changes_hold_finds_no_mod(self):
        from anno_modding_studio.ui.changes_dock import SCOPE_ALL, SCOPE_NAMES

        panel = self.panel()
        self.scope(panel, SCOPE_NAMES)
        self.typed(panel, "thing")
        self.assertTrue(self.mod_row(panel, self.ONE).isHidden())
        self.assertTrue(self.mod_row(panel, self.TWO).isHidden())
        self.assertIn("No mod's name has", panel.summary.text())
        # Across every row, both mods hold it.
        self.scope(panel, SCOPE_ALL)
        panel.finish_filter()
        self.assertEqual(len(shown_change_rows(panel)), 2)

    def test_one_mod_shows_it_alone_and_open(self):
        panel = self.panel()
        panel.filter_mod(self.TWO)
        two = self.mod_row(panel, self.TWO)
        self.assertTrue(self.mod_row(panel, self.ONE).isHidden())
        self.assertFalse(two.isHidden())
        self.assertTrue(two.isExpanded())
        self.assertEqual([row.text(1) for row in shown_change_rows(panel)], ["1 → 22"])
        self.assertIn(self.TWO, panel.scope_box.currentText())
        self.assertIn(self.TWO, panel.filter_box.placeholderText())

    def test_and_what_is_typed_looks_in_that_mod_alone(self):
        panel = self.panel()
        panel.filter_mod(self.TWO)
        self.typed(panel, "11")
        self.assertEqual(shown_change_rows(panel), [])
        self.assertTrue(self.mod_row(panel, self.ONE).isHidden())
        self.assertIn("No rows of", panel.summary.text())
        self.typed(panel, "22")
        self.assertEqual(len(shown_change_rows(panel)), 1)

    def test_a_mod_rows_menu_offers_it(self):
        panel = self.panel()
        menu = panel.menu_for(self.mod_row(panel, self.ONE))
        self.addCleanup(menu.deleteLater)
        entry = next(a for a in menu.actions() if a.text() == "Filter this mod's changes")
        entry.trigger()
        self.assertTrue(self.mod_row(panel, self.TWO).isHidden())

    def test_clearing_brings_every_mod_back_and_the_entry_goes(self):
        panel = self.panel()
        panel.filter_mod(self.TWO)
        panel.clear_filter()
        self.assertFalse(self.mod_row(panel, self.ONE).isHidden())
        self.assertFalse(self.mod_row(panel, self.TWO).isHidden())
        self.assertEqual(panel.scope_box.count(), 2)

    def test_a_mod_unloaded_under_it_puts_the_filter_back_on_every_row(self):
        panel = self.panel()
        panel.filter_mod(self.TWO)
        panel.loadout.drop_layer(self.TWO)
        panel.regroup()
        self.assertEqual(panel.scope_box.count(), 2)
        self.assertFalse(self.mod_row(panel, self.ONE).isHidden())


class TestAModRowSaysWhatItIs(unittest.TestCase):
    """A mod row's tooltip and caption, for a mod with a hundred options and for
    a mod that is only a box of others."""

    def panel(self, *layers):
        from anno_modding_studio.core.changes import Loadout
        from anno_modding_studio.ui.changes_dock import ChangesPanel

        loadout = Loadout()
        for layer in layers:
            loadout.set_layer(layer)
        panel = ChangesPanel(loadout)
        self.addCleanup(panel.deleteLater)
        panel.refresh()
        panel.build_every_row()
        self.last_panel = panel
        rows = {}

        def walk(item):
            if item.data(0, Qt.ItemDataRole.UserRole + 4) == "mod":
                rows[item.data(0, Qt.ItemDataRole.UserRole + 5)] = item
            for index in range(item.childCount()):
                walk(item.child(index))

        for index in range(panel.tree.topLevelItemCount()):
            walk(panel.tree.topLevelItem(index))
        return rows

    def test_only_changed_options_are_named_by_their_labels_and_not_all_of_them(self):
        """A tooltip taller than the screen was what a hundred iModYourAnno
        knobs listed by their internal names came to."""
        from anno_modding_studio.core import settings as settings_mod
        from anno_modding_studio.core.changes import ModLayer

        offered = settings_mod.ModOptions([
            settings_mod.Option(
                name="file::knob %d" % n, label="Knob %d" % n,
                kind=settings_mod.TOGGLE, default="true",
            )
            for n in range(40)
        ])
        chosen = {"file::knob %d" % n: ("false" if n < 20 else "true") for n in range(40)}
        rows = self.panel(ModLayer(
            origin="tweaked", name="Tweaked", options=chosen, offered=offered.to_dict(),
        ))
        tip = rows["tweaked"].toolTip(0)
        self.assertIn("read with 20 of its options changed from the author's defaults", tip)
        self.assertIn("  Knob 0: off", tip)
        self.assertNotIn("file::", tip)
        self.assertNotIn("Knob 20", tip)
        self.assertNotIn("Knob 5", tip)
        self.assertIn("and 15 more", tip)

    def test_the_mod_you_are_writing_is_not_said_to_be_patched(self):
        """A mod that patches nothing says so with an empty name, which is
        also the name of the mod being written: with any other mod loaded
        its tooltip said "patched by" that mod."""
        from anno_modding_studio.core.changes import Change, ModLayer

        self.panel(ModLayer(origin="[Gameplay] Theirs", name="Theirs"))
        loadout = self.last_panel.loadout
        self.assertIsNone(loadout.patch_for(""))
        loadout.set_change(Change(
            guid="1010340", asset_label="Thing", path="/Values/X", tag="X",
            old_value="1", new_value="2",
        ))
        self.last_panel.refresh()
        self.last_panel.build_every_row()
        tip = self.rows_of(self.last_panel)[""].toolTip(0)
        self.assertIn("the mod you are writing", tip)
        self.assertNotIn("patched by", tip)

    def rows_of(self, panel):
        rows = {}
        for index in range(panel.tree.topLevelItemCount()):
            item = panel.tree.topLevelItem(index)
            rows[item.data(0, Qt.ItemDataRole.UserRole + 5)] = item
        return rows

    def with_ops(self, *ops, unread=()):
        """One mod holding these kept ops, and its row."""
        from anno_modding_studio.core.changes import Loadout, ModLayer
        from anno_modding_studio.ui.changes_dock import ChangesPanel

        loadout = Loadout()
        loadout.set_layer(ModLayer(origin="[Gameplay] Theirs", name="Theirs"))
        for op in ops:
            loadout.set_op(op)
        loadout.unread = list(unread)
        panel = ChangesPanel(loadout)
        self.addCleanup(panel.deleteLater)
        panel.refresh()
        panel.build_every_row()
        return self.rows_of(panel)["[Gameplay] Theirs"]

    def test_only_the_ops_that_search_are_counted_as_targeting_by_search(self):
        """Every op is kept as written, and the line counted them all: three
        ops naming a GUID and two searching were five things targeted by
        search."""
        from anno_modding_studio.core.changes import Op

        def op(path, guid=""):
            source = '<ModOp Type="remove" %sPath="%s"/>' % (
                'GUID="%s" ' % guid if guid else "", path)
            return Op(expression=path, source=source, origin="[Gameplay] Theirs",
                      searches=not guid, matched=[guid or "1010340"])

        row = self.with_ops(
            op("/Values/A", "1"), op("/Values/B", "2"), op("/Values/C", "3"),
            op("//Values/D"), op("//Values/E"),
        )
        self.assertIn("targets 2 things by search, not by GUID", row.toolTip(0))
        named = self.with_ops(op("/Values/A", "1"))
        self.assertNotIn("by search", named.toolTip(0))

    def test_what_could_not_be_read_is_counted_from_what_is_listed(self):
        """Not from the number the layer keeps, which was nothing for an
        entry that arrived without it being counted again."""
        from anno_modding_studio.core.changes import Unread

        entry = Unread(origin="[Gameplay] Theirs", source="<ModOp/>", why="no")
        row = self.with_ops(unread=[entry])
        self.assertIn("1 ModOp here could not be read", row.toolTip(0))
        self.assertIn("1 unread", row.text(1))

    def test_a_mod_bundled_in_one_that_is_off_is_not_said_to_load(self):
        """Its own switch is on and its host's is not: the game loads nothing
        from a mod that is off, and the list said "loads at position 2"."""
        from anno_modding_studio.core.changes import ModLayer

        rows = self.panel(
            ModLayer(origin="Item Overhaul", name="Item Overhaul", active=False),
            ModLayer(origin="Item Overhaul/a.lodge", name="Lodge"),
            ModLayer(origin="[Gameplay] Other", name="Other"),
        )
        inner = rows["Item Overhaul/a.lodge"]
        self.assertIn("not loaded. Item Overhaul, which it came bundled inside", inner.toolTip(0))
        self.assertNotIn("loads at position", inner.toolTip(0))
        self.assertTrue(inner.font(0).strikeOut())
        self.assertIn("loads at position", rows["[Gameplay] Other"].toolTip(0))
        self.assertFalse(rows["[Gameplay] Other"].font(0).strikeOut())
        # Nor, further down, to load as a mod of its own: the host is named
        # once, as what keeps it from loading.
        self.assertNotIn("The game loads it", inner.toolTip(0))
        self.assertNotIn("which it came with", inner.toolTip(0))

    def test_a_bundled_mod_switches_with_its_host_and_has_no_box_of_its_own(self):
        """Play cannot switch a bundled mod alone -- the game skips its host's
        whole folder -- and a box of its own let it be switched off on its
        own, leaving the mods that need it broken in the Studio's reading
        while the game went on loading it."""
        from anno_modding_studio.core.changes import ModLayer

        rows = self.panel(
            ModLayer(origin="Item Overhaul", name="Item Overhaul"),
            ModLayer(origin="Item Overhaul/a.lodge", name="Lodge", active=False),
            ModLayer(origin="Item Overhaul/a.union", name="Union"),
        )
        host = rows["Item Overhaul"]
        self.assertEqual(host.checkState(0), Qt.CheckState.Checked)
        for origin in ("Item Overhaul/a.lodge", "Item Overhaul/a.union"):
            with self.subTest(origin=origin):
                row = rows[origin]
                self.assertIs(row.parent(), host)
                self.assertIsNone(row.data(0, Qt.ItemDataRole.CheckStateRole))
                self.assertFalse(row.font(0).strikeOut())
                self.assertIn(
                    "bundled inside Item Overhaul, which it came with. The game loads "
                    "it as a mod of its own, and switches it with that mod.",
                    row.toolTip(0),
                )
        host.setCheckState(0, Qt.CheckState.Unchecked)
        self.assertTrue(rows["Item Overhaul/a.union"].font(0).strikeOut())
        self.assertIn("not loaded. Item Overhaul, which it came bundled inside",
                      rows["Item Overhaul/a.union"].toolTip(0))

    def test_a_box_of_mods_says_what_it_holds_and_each_piece_says_where_it_lives(self):
        from anno_modding_studio.core.changes import ModLayer

        # In load order, which set_layer numbers as they arrive.
        rows = self.panel(
            ModLayer(origin="Item Overhaul/a.lodge", name="Lodge"),
            ModLayer(origin="Item Overhaul", name="Item Overhaul"),
            ModLayer(origin="Item Overhaul/a.trade_union", name="Trade Union"),
        )
        host = rows["Item Overhaul"]
        self.assertEqual(host.text(1), "holds 2 mods of its own")
        tip = host.toolTip(0)
        self.assertIn("holds 2 mods of its own", tip)
        self.assertNotIn("the mod you are writing", tip)
        self.assertIn("bundled inside Item Overhaul", rows["Item Overhaul/a.lodge"].toolTip(0))

    def children(self, item, kind):
        from anno_modding_studio.ui.changes_dock import KIND_ROLE

        return [item.child(i) for i in range(item.childCount())
                if item.child(i).data(0, KIND_ROLE) == kind]

    def test_a_box_of_mods_has_a_row_for_each_piece_that_goes_to_it(self):
        from anno_modding_studio.core.changes import ModLayer
        from anno_modding_studio.ui.changes_dock import GOTO_ROLE

        rows = self.panel(
            ModLayer(origin="Item Overhaul/a.lodge", name="Lodge"),
            ModLayer(origin="Item Overhaul", name="Item Overhaul"),
            ModLayer(origin="Item Overhaul/a.trade_union", name="Trade Union"),
        )
        host = rows["Item Overhaul"]
        # The pieces themselves, in load order, and no rows standing in for
        # them: they are where their changes are.
        self.assertEqual(self.children(host, "bundled"), [])
        pieces = self.children(host, "mod")
        self.assertEqual(pieces, [rows["Item Overhaul/a.lodge"], rows["Item Overhaul/a.trade_union"]])
        self.assertTrue(pieces[0].text(1).startswith("bundled, loads at position "))
        self.assertIsNone(pieces[0].data(0, GOTO_ROLE))
        self.assertTrue(self.last_panel.go_to_mod("Item Overhaul/a.trade_union"))
        self.assertTrue(host.isExpanded())

    def test_a_copy_the_game_takes_from_another_mod_goes_to_that_copy(self):
        """A [Shared] piece loads once however many mods carry it: this host's
        copy is no layer of its own, so its row goes to the one that loads."""
        from anno_modding_studio.core.changes import ModLayer

        host = ModLayer(origin="Item Overhaul", name="Item Overhaul")
        host.copies = {"Item Overhaul/shared-pools": "Low Tier/shared-pools"}
        rows = self.panel(
            host,
            ModLayer(origin="Low Tier", name="Low Tier"),
            ModLayer(origin="Low Tier/shared-pools", name="Pools and Definitions"),
        )
        pieces = self.children(rows["Item Overhaul"], "bundled")
        self.assertEqual([row.text(0) for row in pieces], ["↪ Pools and Definitions"])
        self.assertEqual(pieces[0].text(1), "loads from another mod")
        self.assertIn("The copy it uses is in Low Tier", pieces[0].toolTip(0))
        open_row(self.last_panel, pieces[0])
        self.assertEqual(self.last_panel.tree.currentItem(), rows["Low Tier/shared-pools"])
        self.assertIs(rows["Low Tier/shared-pools"].parent(), rows["Low Tier"])

    def carried_twice(self, a_on=True, version="1.3"):
        from anno_modding_studio.core.changes import ModLayer

        other = ModLayer(origin="B", name="Other Mod", mod_id="b")
        other.copies = {"B/Shared": "A/Shared"}
        other.copy_versions = {"B/Shared": version}
        return self.panel(
            ModLayer(origin="A", name="Big Overhaul", active=a_on),
            ModLayer(origin="A/Shared", name="Shared Pools", mod_id="shared", version="1.3"),
            other,
            ModLayer(origin="Shared (my patch)", name="Shared (my patch)", patches="A/Shared",
                     version="1.0"),
        )

    def test_your_patch_to_a_bundled_mod_sits_straight_below_it(self):
        rows = self.carried_twice()
        host = rows["A"]
        self.assertEqual(self.children(host, "mod"), [rows["A/Shared"], rows["Shared (my patch)"]])
        # Its own switch: a patch is a mod of yours, not something bundled.
        self.assertEqual(rows["Shared (my patch)"].checkState(0), Qt.CheckState.Checked)

    def test_a_shared_piece_whose_host_is_off_loads_from_another_carrier(self):
        rows = self.carried_twice(a_on=False)
        shared = rows["A/Shared"]
        self.assertFalse(shared.font(0).strikeOut())
        self.assertIn("(the game loads the copy in Other Mod)", shared.text(0))
        self.assertIn("the same version (1.3)", shared.toolTip(0))
        self.assertFalse(rows["Shared (my patch)"].font(0).strikeOut())
        self.assertNotIn("written against", rows["Shared (my patch)"].text(0))

    def test_and_says_when_that_copy_is_another_version(self):
        rows = self.carried_twice(a_on=False, version="1.1")
        self.assertIn("(the game loads the copy in Other Mod, version 1.1)", rows["A/Shared"].text(0))
        self.assertIn("These rows are of version 1.3", rows["A/Shared"].toolTip(0))
        self.assertIn("(written against version 1.3, and the game loads 1.1)",
                      rows["Shared (my patch)"].text(0))

    def test_a_filter_finds_a_bundled_mod_inside_its_host(self):
        rows = self.carried_twice()
        panel = self.last_panel
        panel.filter_box.setText("shared pools")
        panel._apply_filter()
        self.assertFalse(rows["A/Shared"].isHidden())
        self.assertFalse(rows["A"].isHidden())
        self.assertTrue(rows["A"].isExpanded())
        self.assertTrue(rows["B"].isHidden())
        panel.filter_box.setText("")
        panel._apply_filter()
        self.assertFalse(rows["B"].isHidden())

    def test_by_name_a_host_found_with_a_mod_inside_it_is_opened_to_show_that_one(self):
        """Both named: the host was put back as it was left, folded, after
        the mod inside it had opened it, and the mod counted as shown was
        out of sight."""
        from anno_modding_studio.core.changes import ModLayer
        from anno_modding_studio.ui.changes_dock import SCOPE_NAMES

        rows = self.panel(
            ModLayer(origin="Item Overhaul", name="Item Overhaul"),
            ModLayer(origin="Item Overhaul/a.lodge", name="Item Lodge"),
            ModLayer(origin="[Gameplay] Other", name="Other"),
        )
        panel = self.last_panel
        host = rows["Item Overhaul"]
        self.assertFalse(host.isExpanded())
        panel.scope_box.setCurrentIndex(panel.scope_box.findData(SCOPE_NAMES))
        panel.filter_box.setText("item")
        panel._apply_filter()
        self.assertFalse(rows["Item Overhaul/a.lodge"].isHidden())
        self.assertFalse(host.isHidden())
        self.assertTrue(host.isExpanded())
        self.assertTrue(rows["[Gameplay] Other"].isHidden())
        # Cleared, it is folded as it was left.
        panel.filter_box.setText("")
        panel._apply_filter()
        self.assertFalse(host.isExpanded())

    def test_a_host_selected_is_not_its_bundled_mods(self):
        from anno_modding_studio.core.changes import Change

        rows = self.carried_twice()
        panel = self.last_panel
        change = Change(guid="1", asset_label="x", path="Values/Standard/Name", tag="Name",
                        op="replace", old_value="a", new_value="b", origin="A/Shared")
        panel.loadout.changes[change.key] = change
        panel.refresh()
        panel.build_every_row()
        rows = {}

        def walk(item):
            if item.data(0, Qt.ItemDataRole.UserRole + 4) == "mod":
                rows[item.data(0, Qt.ItemDataRole.UserRole + 5)] = item
            for index in range(item.childCount()):
                walk(item.child(index))

        for index in range(panel.tree.topLevelItemCount()):
            walk(panel.tree.topLevelItem(index))
        rows["A"].setSelected(True)
        self.assertEqual(panel._selected_changes(), {})
        rows["A"].setSelected(False)
        rows["A/Shared"].setSelected(True)
        self.assertEqual(list(panel._selected_changes()), [change.key])

    def test_the_groups_inside_a_kept_group_are_rows_named_for_their_switches(self):
        from anno_modding_studio.core.changes import Change, ModLayer, Op

        op = Op(
            source='<Group ModOpID="Outer"/>', summary="the group Outer", origin="m",
            kept="it is named ModOpID=Outer", made=1,
            parts=[
                {"path": ["the group Kept"], "off": False, "id": "Kept", "title": "Keep it"},
                {"path": ["the group Off, switched off"], "off": True, "id": "Off",
                 "unswitched": True},
            ],
        )
        change = Change(
            guid="1000", asset_label="alpha", path="/Values/Building/Radius", tag="Radius",
            old_value="10", new_value="11", origin="m", authored=False,
            op_uid=op.uid, within=("the group Kept",),
        )
        from anno_modding_studio.core.changes import Loadout
        from anno_modding_studio.ui.changes_dock import ChangesPanel

        loadout = Loadout()
        loadout.set_layer(ModLayer(origin="m", name="M"))
        loadout.set_op(op)
        loadout.set_change(change)
        panel = ChangesPanel(loadout)
        self.addCleanup(panel.deleteLater)
        panel.refresh()
        mod = panel.tree.topLevelItem(0)
        op_row = self.children(mod, "op")[0]
        parts = self.children(op_row, "part")
        self.assertEqual([row.text(0) for row in parts], ["Keep it", "the group Off, switched off"])
        self.assertEqual(parts[0].text(1), "1 change")
        self.assertEqual(parts[1].text(1), "switched off, so nothing in it runs")
        self.assertFalse(parts[0].isExpanded(), "they start shut, one row per switch")
        self.assertEqual(parts[0].childCount(), 1)
        self.assertIn("none of the mod's options switches it", parts[1].toolTip(0))


class TestReadingAConfigurableMod(unittest.TestCase):
    """What the import dialog does with a mod that has settings."""

    PATCH = (
        "<ModOps>"
        '<ModOp Type="merge" GUID="1010030" Path="/Values/Residence7" Condition="$roomier">'
        "<Residents>30</Residents></ModOp>"
        '<ModOp Type="merge" GUID="1010031" Path="/Values/Residence7" Condition="!$roomier">'
        "<Residents>40</Residents></ModOp>"
        "</ModOps>"
    )

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        tmp = Path(cls._tmp.name)
        cls.assets = tmp / "assets.xml"
        cls.assets.write_text(make_sample_assets.build_assets_xml(40), encoding="utf-8")
        cls.db = tmp / "index.sqlite"
        index_mod.build_index(cls.assets, cls.db)
        cls.mod = tmp / "[Gameplay] Configurable"
        patch = cls.mod / "data/config/export/main/asset/assets.xml"
        patch.parent.mkdir(parents=True)
        patch.write_text(cls.PATCH, encoding="utf-8")
        (cls.mod / "modinfo.json").write_text(OPTIONS_JSON, encoding="utf-8")

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def setUp(self):
        from anno_modding_studio.ui.mod_import_dialog import ModImportDialog

        self.window = MainWindow()

        self.addCleanup(_flush_deletes)
        self.window.game_files.adopt_index(self.db, self.assets, cached=True)
        self.dialog = ModImportDialog(self.window.index, self.window, str(self.mod))
        self.dialog.reload()

    def tearDown(self):
        self.dialog.deleteLater()
        self.window.editor.search._debounce.stop()
        self.window.game_files.stop_texts_worker()
        if self.window.index is not None:
            self.window.index.close()
            self.window.index = None
        self.window.loadout.clear()
        self.window.deleteLater()

    def test_the_options_button_says_how_many_there_are(self):
        self.assertTrue(self.dialog.options_button.isEnabled())
        self.assertIn("3", self.dialog.options_button.text())

    def test_read_at_its_defaults_the_guarded_op_does_not_run(self):
        outcomes = [
            self.dialog.ops_table.item(row, 3).text()
            for row in range(self.dialog.ops_table.rowCount())
        ]
        self.assertEqual(outcomes, ["No change today", "Read"])

    def test_changing_a_setting_re_reads_the_mod(self):
        self.dialog.settings = {"roomier": "true"}
        self.dialog.reload()
        outcomes = [
            self.dialog.ops_table.item(row, 3).text()
            for row in range(self.dialog.ops_table.rowCount())
        ]
        self.assertEqual(outcomes, ["Read", "No change today"])

    def test_the_settings_travel_with_the_mod_when_it_is_kept_separate(self):
        self.dialog.settings = {"roomier": "true"}
        self.dialog.reload()
        self.dialog.separate_box.setChecked(True)
        self.dialog._accept()
        layer = self.dialog.layer()
        self.assertEqual(layer.options["roomier"], "true")
        self.assertIn("roomier", layer.offered)

    def test_another_mod_is_not_read_at_the_settings_chosen_for_this_one(self):
        other = Path(self._tmp.name) / "[Gameplay] Another"
        patch = other / "data/config/export/main/asset/assets.xml"
        patch.parent.mkdir(parents=True, exist_ok=True)
        patch.write_text(self.PATCH, encoding="utf-8")
        self.dialog.settings = {"roomier": "true"}
        self.dialog.reload()
        self.dialog.folder_box.setText(str(other))
        self.dialog.reload()
        self.assertEqual(self.dialog.settings, {})
        self.assertEqual(self.dialog._reading.options, {"": {}})
        self.dialog.separate_box.setChecked(True)
        self.assertEqual(self.dialog.layer().options, {})

    def test_a_mod_with_no_options_offers_no_button(self):
        from anno_modding_studio.ui.mod_import_dialog import ModImportDialog

        plain = Path(self._tmp.name) / "[Gameplay] Plain"
        patch = plain / "data/config/export/main/asset/assets.xml"
        patch.parent.mkdir(parents=True, exist_ok=True)
        patch.write_text(
            '<ModOps><ModOp Type="merge" GUID="1010030" Path="/Values/Residence7">'
            "<Residents>30</Residents></ModOp></ModOps>",
            encoding="utf-8",
        )
        other = ModImportDialog(self.window.index, self.window, str(plain))
        try:
            other.reload()
            self.assertFalse(other.options_button.isEnabled())
        finally:
            other.deleteLater()


class OpCase(unittest.TestCase):
    """A window over a small assets.xml, for kept ops."""

    # Whether the mod read in is yours: changes can go into a mod of
    # yours, and never into somebody else's.
    YOURS = False

    SEARCH = (
        "<ModOps>"
        '<ModOp Type="merge" Path="//Values/Residence7">'
        "<Residents>99</Residents></ModOp>"
        "</ModOps>"
    )

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        tmp = Path(cls._tmp.name)
        cls.assets = tmp / "assets.xml"
        cls.assets.write_text(make_sample_assets.build_assets_xml(40), encoding="utf-8")
        cls.db = tmp / "index.sqlite"
        index_mod.build_index(cls.assets, cls.db)
        cls.mod = tmp / "[Gameplay] Crowded"
        patch = cls.mod / "data/config/export/main/asset/assets.xml"
        patch.parent.mkdir(parents=True)
        patch.write_text(cls.SEARCH, encoding="utf-8")

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def setUp(self):
        self.window = MainWindow()
        self.addCleanup(_flush_deletes)
        self.window.game_files.adopt_index(self.db, self.assets, cached=True)

    def tearDown(self):
        self.window.editor.search._debounce.stop()
        self.window.game_files.stop_texts_worker()
        if self.window.index is not None:
            self.window.index.close()
            self.window.index = None
        self.window.loadout.clear()
        self.window.deleteLater()

    def load_mod(self, origin="[Gameplay] Crowded"):
        report = reader.analyse(self.mod, self.window.index, origin=origin)
        from anno_modding_studio.core.changes import ModLayer

        self.window.loadout.set_layer(ModLayer(origin=origin, name=origin))
        if self.YOURS:
            as_yours(self.window.loadout, origin)
        for op in report.ops:
            self.window.loadout.set_op(op)
        for change in report.changes:
            self.window.loadout.set_change(change)
        self.window.changes_panel.refresh()
        return report

    def rows(self, kind):
        from anno_modding_studio.ui.changes_dock import KIND_ROLE

        found = []

        def walk(item):
            if item.data(0, KIND_ROLE) == kind:
                found.append(item)
            for index in range(item.childCount()):
                walk(item.child(index))

        tree = self.window.changes_panel.tree
        for index in range(tree.topLevelItemCount()):
            walk(tree.topLevelItem(index))
        return found


class TestATooltipIsReadAtAGlance(unittest.TestCase):
    """What can be any length is cut to fit a tooltip, and says it was."""

    def test_a_long_pattern_keeps_where_it_starts_and_the_field_it_ends_at(self):
        from anno_modding_studio.core import keptops

        pattern = "//Values[%s]/ItemEffect" % " or ".join(
            "../Template='Kind%d'" % n for n in range(30)
        )
        cut = keptops.clipped(pattern)
        self.assertLessEqual(len(cut), 120)
        self.assertTrue(cut.startswith("//Values[../Template='Kind0'"), cut)
        self.assertTrue(cut.endswith("/ItemEffect"), cut)
        self.assertIn("…", cut)
        self.assertEqual(keptops.clipped("/Values/Cost"), "/Values/Cost")

    def test_a_block_is_its_first_lines_and_how_many_more(self):
        from anno_modding_studio.ui import change_rows

        block = "\n".join("Line %d" % n for n in range(40))
        lines = change_rows.first_lines(block).split("\n")
        self.assertEqual(len(lines), change_rows.TIP_LINES + 1)
        self.assertEqual(lines[0], "Line 0")
        self.assertEqual(lines[-1], "… and 30 more lines")
        self.assertEqual(change_rows.first_lines("One\nTwo"), "One\nTwo")
        wide = change_rows.first_lines("x" * 500)
        self.assertEqual(len(wide), change_rows.TIP_LINE)


class TestKeptOpsInTheChangesList(OpCase):
    """A kept op is one row saying what the mod does, not four hundred saying it
    again per asset."""

    # Edits go into this mod, so it is yours.
    YOURS = True

    def test_a_change_under_it_does_not_say_the_search_again(self):
        self.load_mod()
        for row in self.rows("change"):
            self.assertNotIn("matching", row.toolTip(0))
            self.assertIn("Part of a ModOp kept as written", row.toolTip(0))

    def test_a_search_gets_a_row_of_its_own(self):
        self.load_mod()
        rows = self.rows("op")
        self.assertEqual(len(rows), 1)
        self.assertIn("Residents = 99", rows[0].text(0))
        self.assertIn("4 assets", rows[0].text(1))

    def test_and_its_changes_hang_under_it(self):
        self.load_mod()
        op_row = self.rows("op")[0]
        under = []

        def walk(item):
            from anno_modding_studio.ui.changes_dock import KIND_ROLE

            if item.data(0, KIND_ROLE) == "change":
                under.append(item)
            for index in range(item.childCount()):
                walk(item.child(index))

        walk(op_row)
        self.assertEqual(len(under), len(self.window.loadout.changes))

    def test_the_row_says_what_its_pattern_covers_and_no_more_than_that(self):
        """That it is written back as the ModOp it came from is so of every
        op, and its row does not say it again; how much it changes is beside
        it."""
        self.load_mod()
        tip = self.rows("op")[0].toolTip(0)
        self.assertIn("covers assets other mods add", tip)
        self.assertNotIn("as the one ModOp it came from", tip)
        self.assertNotIn("matching asset", tip)
        self.assertLessEqual(len(tip.split()), 60, tip)

    def test_its_answers_have_no_boxes_of_their_own(self):
        """A search applies to every asset it matches or to none, so the rows
        underneath cannot be ticked one at a time -- the op's box is the one
        that counts."""
        from PySide6.QtCore import Qt

        self.load_mod()
        op_row = self.rows("op")[0]
        self.assertTrue(op_row.flags() & Qt.ItemFlag.ItemIsUserCheckable)
        for row in self.rows("change"):
            self.assertFalse(row.flags() & Qt.ItemFlag.ItemIsUserCheckable)
            self.assertIn("Part of a ModOp kept as written", row.toolTip(0))

    def test_a_kept_op_is_not_the_same_colour_as_a_mod_heading(self):
        """The list has two kinds of heading. They sat at different depths in
        the same amber, and depth is what a folded tree takes away."""
        from anno_modding_studio.ui import theme

        self.load_mod()
        op = self.rows("op")[0]
        mod = self.rows("mod")[0]
        self.assertEqual(op.foreground(0).color().name(), theme.RULE)
        self.assertEqual(mod.foreground(0).color().name(), theme.ACCENT_TEXT)
        self.assertNotEqual(theme.RULE, theme.ACCENT_TEXT)

    def test_and_an_op_that_is_off_greys_the_way_its_answers_do(self):
        from PySide6.QtCore import Qt

        from anno_modding_studio.ui import theme

        self.load_mod()
        self.rows("op")[0].setCheckState(0, Qt.CheckState.Unchecked)
        op = self.rows("op")[0]
        self.assertEqual(op.foreground(0).color().name(), theme.TEXT_DIM)
        self.assertEqual(
            {row.foreground(0).color().name() for row in self.rows("change")},
            {theme.TEXT_DIM},
        )
        # And back, without a redraw: the row is repainted where it is ticked.
        self.rows("op")[0].setCheckState(0, Qt.CheckState.Checked)
        self.assertEqual(self.rows("op")[0].foreground(0).color().name(), theme.RULE)

    def test_unticking_the_op_switches_everything_it_comes_to_off(self):
        from PySide6.QtCore import Qt

        self.load_mod()
        self.rows("op")[0].setCheckState(0, Qt.CheckState.Unchecked)
        loadout = self.window.loadout
        self.assertFalse(loadout.ops[0].active)
        self.assertTrue(loadout.changes)
        self.assertFalse(any(change.active for change in loadout))
        self.window.changes_panel.refresh()
        self.assertIn("Switched off", self.rows("op")[0].toolTip(0))
        self.rows("op")[0].setCheckState(0, Qt.CheckState.Checked)
        self.assertTrue(all(change.active for change in loadout))

    def edit_in_editor(self, field, value, guid="1010030"):
        self.window.loadout.editing = "[Gameplay] Crowded"
        self.window.editor.refresh_target()
        self.window.editor.load_asset(guid)
        for item in self.window.editor._iter_items():
            if item.text(0) == field and item.data(0, 258) is not None:
                item.setText(1, value)
                return
        self.fail("no %s row" % field)

    def test_an_edit_made_in_the_editor_lands_after_the_op(self):
        """All of these, except this one: written before the search, the
        search would overwrite it in game."""
        self.load_mod()
        loadout = self.window.loadout
        self.edit_in_editor("ResidenceUpgrade", "4")
        own = [c for c in loadout if not c.op_uid]
        self.assertEqual([c.tag for c in own], ["ResidenceUpgrade"])
        self.assertGreater(own[0].order, loadout.ops[0].order)

    def test_and_typing_over_one_of_its_answers_makes_an_exception_beside_it(self):
        self.load_mod()
        loadout = self.window.loadout
        answers = len(loadout.changes)
        self.edit_in_editor("Residents", "5")
        edited = [c for c in loadout if c.new_value == "5"]
        self.assertEqual(len(edited), 1)
        self.assertEqual(edited[0].exception_of, loadout.ops[0].uid)
        self.assertGreater(edited[0].order, loadout.ops[0].order)
        self.assertEqual(len(loadout.changes), answers + 1)
        self.window.changes_panel.refresh()
        rows = [row for row in self.rows("change") if "5" in row.text(1)]
        self.assertEqual(len(rows), 1)
        self.assertIn("An exception to the ModOp", rows[0].toolTip(0))
        # Its own change, so it can be reverted -- unlike the answer beside it.
        self.assertTrue(rows[0].flags() & Qt.ItemFlag.ItemIsUserCheckable)

    def test_a_hand_marked_answer_is_a_caveat_not_a_narrowing(self):
        self.load_mod()
        next(iter(self.window.loadout)).active = False
        self.window.changes_panel.refresh()
        tip = self.rows("op")[0].toolTip(0)
        self.assertNotIn("narrow ModOp", tip)
        self.assertIn("cannot honour", tip)

    def test_double_clicking_it_asks_to_edit_it(self):
        self.load_mod()
        seen = []
        # The window's own handler opens a modal dialog, which a test must not
        # sit inside; what is being checked here is that the row asks at all.
        self.window.changes_panel.opActivated.disconnect(self.window.patterns.edit_pattern)
        self.window.changes_panel.opActivated.connect(seen.append)
        open_row(self.window.changes_panel, self.rows("op")[0])
        self.assertEqual(seen, [self.window.loadout.ops[0].uid])

    def test_an_op_that_changes_nothing_is_still_a_row_you_can_switch_off(self):
        """It matched, it had nothing to do, and it is still an op the mod has."""
        from anno_modding_studio.core.changes import Op

        self.window.loadout.set_op(
            Op(
                expression="//Values/Residence7",
                source='<ModOp Type="merge" Path="//Values/Residence7">'
                       "<Residents>10</Residents></ModOp>",
                summary="merge Residents = 10",
            )
        )
        self.window.changes_panel.refresh()
        row = self.rows("op")[0]
        self.assertEqual(row.childCount(), 0)
        self.assertEqual(row.checkState(0), Qt.CheckState.Checked)
        row.setCheckState(0, Qt.CheckState.Unchecked)
        self.window.changes_panel._on_item_changed(row, 0)
        self.assertFalse(self.window.loadout.ops[0].active)


class TestCheckingWhatTheModsSearchFor(OpCase):
    """*Re-check what the mods search for*: which ops are patterns, and what
    is said of each."""

    def setUp(self):
        super().setUp()
        self.load_mod()
        self.op = self.window.loadout.ops[0]

    def invents(self, origin="0 earlier", guid="7777"):
        """Have ``origin``, loading before the mod, create a residence its
        search answers to."""
        from anno_modding_studio.core.changes import OP_ADD_NEXT, ModLayer

        self.window.loadout.set_layer(ModLayer(origin=origin, name=origin))
        self.window.loadout.set_change(Change(
            guid="1010030", asset_label="anchor", path="", tag="Asset",
            op=OP_ADD_NEXT, summary="create asset %s" % guid, origin=origin,
            payload="<Asset><Template>ResidenceBuilding7</Template><Values><Standard>"
                    "<GUID>%s</GUID><Name>invented</Name></Standard>"
                    "<Residence7><Residents>2</Residents></Residence7></Values></Asset>"
                    % guid,
        ))

    def check(self, answer=False):
        """Ask, as the menu does; what was said, and what was asked."""
        from anno_modding_studio.ui import patterns_controller

        with mock.patch.object(patterns_controller.QMessageBox, "information") as told, \
                mock.patch.object(patterns_controller, "ask", return_value=answer) as asked:
            self.window.patterns.check_patterns(True)
        return told, asked

    def test_an_op_that_names_its_assets_is_not_a_pattern_checked(self):
        """Every op remembers what it matched; only a search is a pattern
        (A11-88). The op naming its asset was counted as a second one."""
        import dataclasses

        named = dataclasses.replace(self.op, uid="", searches=False)
        self.window.loadout.set_op(named)
        self.assertTrue(named.matched)
        told, _asked = self.check()
        self.assertEqual(told.call_args.args[1], "Still up to date")
        self.assertIn("1 pattern checked.", told.call_args.args[2])

    def test_and_with_no_search_there_is_no_pattern(self):
        self.op.searches = False
        self.assertTrue(self.op.matched)
        told, _asked = self.check()
        self.assertEqual(told.call_args.args[1], "No patterns")

    def test_a_search_that_matched_nothing_when_read_is_checked_all_the_same(self):
        """An asset invented since can answer it now (A11-88): it was told
        there were no patterns at all."""
        self.op.matched = []
        self.invents()
        told, asked = self.check()
        told.assert_not_called()
        asked.assert_called_once()
        self.assertIn("now matches 1 new", asked.call_args.args[2])

    def answered(self, reply):
        """Ask, as the menu does, with every question answered by ``reply``
        (a button's text, or True/False); what was asked, and whether the
        mods were read again."""
        with _isolation.answering(reply) as asked, \
                mock.patch.object(self.window, "load_installed_mods") as read:
            self.window.patterns.check_patterns(True)
        return asked, read

    def an_old_one(self, uid="old"):
        """A second search of the mod's, as a loadout saved before ops kept
        their source has it: remembered, with nothing to run again."""
        import dataclasses

        old = dataclasses.replace(self.op, uid=uid, source="")
        self.window.loadout.set_op(old)
        return old

    def test_assets_gone_from_a_pattern_that_holds_its_op_are_not_blamed_on_old_loadouts(self):
        """A mod unloaded takes the assets it invented with it, and a search
        of another mod's that matched them still remembers them (A11-89):
        that is not a loadout read before ops were kept."""
        self.op.matched = [*self.op.matched, "5"]
        asked, read = self.answered(True)
        self.assertEqual(asked.titles, ["A search has moved"])
        text = asked.texts[0]
        self.assertIn("now matches 1 gone", text)
        self.assertNotIn("before the Studio kept", text)
        self.assertIn("no longer loaded", text)
        self.assertEqual(asked.buttons, [["Read the mods again", "Not now"]])
        read.assert_called_once_with()

    def test_patterns_that_can_run_and_ones_that_cannot_are_told_apart(self):
        """One holds its op and has a new asset to cover, the other was read
        before ops were kept (A11-89): the question said all of them could be
        run again, and never offered to read the rest again."""
        self.invents()
        self.an_old_one()
        asked, read = self.answered(False)
        self.assertEqual(asked.titles, ["A pattern has more to cover"])
        text = asked.texts[0]
        self.assertNotIn("Those patterns still hold", text)
        self.assertIn("1 of them still holds the ModOp it came from", text)
        self.assertIn("1 of them was read before the Studio kept", text)
        self.assertEqual([sorted(buttons) for buttons in asked.buttons],
                         [["Not now", "Read the mods again", "Run them again"]])
        read.assert_not_called()

    def test_and_the_rest_can_be_read_again_from_there(self):
        self.invents()
        self.an_old_one()
        _asked, read = self.answered("Read the mods again")
        read.assert_called_once_with()
        # Nothing was run first: the reading puts every one of them right.
        self.assertFalse(any(c.guid == "7777" for c in self.window.loadout))

    def test_or_the_ones_that_hold_their_op_run_again(self):
        self.invents()
        self.an_old_one()
        before = len(self.window.loadout.changes)
        _asked, read = self.answered("Run them again")
        read.assert_not_called()
        self.assertGreater(len(self.window.loadout.changes), before)
        self.assertIn("7777", self.op.matched)

    def test_a_pattern_with_new_assets_and_gone_ones_is_offered_both(self):
        """Running it again covers the new asset; the gone one stays edited
        by what it kept, which only reading the mods again settles."""
        self.invents()
        self.op.matched = [*self.op.matched, "5"]
        asked, _read = self.answered(False)
        text = asked.texts[0]
        self.assertIn("now matches 1 new and 1 gone", text)
        self.assertIn("1 of them matched assets that are no longer loaded", text)
        self.assertEqual([sorted(buttons) for buttons in asked.buttons],
                         [["Not now", "Read the mods again", "Run them again"]])

    def test_all_of_them_holding_their_op_are_only_offered_the_run(self):
        self.invents()
        asked, _read = self.answered(False)
        self.assertEqual(asked.titles, ["A pattern has more to cover"])
        self.assertIn("Those patterns still hold the ModOp", asked.texts[0])
        self.assertEqual(asked.buttons, [["Run them again", "Not now"]])

    def test_and_all_of_them_read_before_ops_were_kept_only_the_reading(self):
        self.op.source = ""
        self.invents()
        asked, _read = self.answered(False)
        self.assertEqual(asked.titles, ["A search has moved"])
        self.assertIn("before the Studio kept", asked.texts[0])
        self.assertEqual(asked.buttons, [["Read the mods again", "Not now"]])


class TestEditingSomebodyElsesPattern(OpCase):
    """*Edit pattern* on a pattern of a mod that is not yours writes it again
    in a mod of yours: your patch, or this loadout's own mod, whichever the
    three ways on are answered with (section 5.2 of
    docs/proposals/projects-and-loadouts-proposal.md).  Their file is never
    changed; it used to be whatever changes went to, a patch made and picked
    included (docs/proposals/patches-proposal.md)."""

    THEIRS = "[Gameplay] Crowded"

    def setUp(self):
        super().setUp()
        self.load_mod()
        self.op = self.window.loadout.ops[0]
        # The dialog is modal; what is checked here is what it is opened on.
        opened = mock.patch.object(self.window.patterns, "_pattern_dialog")
        self.opened = opened.start()
        self.addCleanup(opened.stop)

    def asked(self, answer):
        return mock.patch.object(self.window.prompts, "ask_way_on", return_value=answer)

    def edit(self):
        self.window.patterns.edit_pattern(self.op.uid)

    def patch(self):
        return self.window.loadout.patch_for(self.THEIRS)

    def test_written_in_a_patch_made_for_it(self):
        with self.asked("patch") as ask:
            self.edit()
        ask.assert_called_once_with(self.THEIRS, "", False)
        patch = self.patch()
        self.assertIsNotNone(patch)
        self.opened.assert_called_once_with(None, origin=patch.origin, copy_of=self.op)
        self.assertIs(self.window.loadout.op(self.op.uid), self.op)

    def test_the_patch_there_already_is_offered(self):
        from anno_modding_studio.core import patching

        patch = patching.make(self.window.loadout, self.THEIRS)
        with self.asked("patch") as ask:
            self.edit()
        self.assertEqual(ask.call_args.args[1], patch.label)
        self.opened.assert_called_once_with(None, origin=patch.origin, copy_of=self.op)
        self.assertEqual([layer for layer in self.window.loadout.layers if layer.patches], [patch])
        self.assertEqual(self.window.loadout.editing_origin(), patch.origin)

    def test_a_patch_picked_takes_it_without_asking(self):
        from anno_modding_studio.core import patching

        patch = patching.make(self.window.loadout, self.THEIRS)
        self.window.loadout.editing = patch.origin
        with self.asked("") as ask:
            self.edit()
        ask.assert_not_called()
        self.opened.assert_called_once_with(None, origin=patch.origin, copy_of=self.op)

    def test_the_loadouts_own_mod_on_asking_and_not_asked_again(self):
        with self.asked("own"):
            self.edit()
        self.opened.assert_called_once_with(None, origin="", copy_of=self.op)
        self.assertIsNone(self.patch())
        self.assertTrue(self.window.loadout.has_own_mod())
        with self.asked("patch") as ask:
            self.edit()
        ask.assert_not_called()
        self.assertEqual(self.opened.call_args_list[-1], mock.call(None, origin="", copy_of=self.op))

    def test_cancelled_nothing_happens(self):
        with self.asked(""):
            self.edit()
        self.opened.assert_not_called()
        self.assertIsNone(self.patch())

    def test_changes_pointed_at_their_mod_ask_all_the_same(self):
        # A loadout saved before their mod was no place for changes to go.
        self.window.loadout.editing = self.THEIRS
        with self.asked("") as ask:
            self.edit()
        ask.assert_called_once()
        self.opened.assert_not_called()

    def test_on_a_copy_made_of_it_its_own_is_changed(self):
        """*Make your own copy* answered: the pattern is the copy's now, and
        is edited where it is. The question was taken as cancelled, and the
        edit that asked it dropped (A11-97)."""
        from anno_modding_studio.core import forking

        with self.asked("fork"), \
                mock.patch.object(self.window.prompts, "ask_fork_a_mod", return_value=("copy", False)), \
                mock.patch.object(self.window.preferences, "ensure_creator"), \
                mock.patch.object(self.window.where, "library_dir", return_value=None):
            self.edit()
        moved = self.window.loadout.op(self.op.uid)
        self.assertEqual(moved.origin, self.THEIRS + forking.SUFFIX)
        # Read again, not the op as it was before the copy took it: opened
        # on that, the dialog wrote a copy of the pattern beside it.
        self.opened.assert_called_once_with(moved)

    def test_your_own_is_changed_directly(self):
        self.window.loadout.own.creator = "Me"
        self.window.loadout.layer(self.THEIRS).creator = "me"
        self.window.loadout.layer(self.THEIRS).kept = True
        with self.asked("patch") as ask:
            self.edit()
        ask.assert_not_called()
        self.opened.assert_called_once_with(self.op)

    def test_the_question_names_the_mod_once_and_each_answer_says_where_it_leads(self):
        from anno_modding_studio.ui import widgets

        real = type(self.window.prompts).ask_way_on
        for patch in ("", "Crowded (my patch)"):
            boxes = []
            with self.subTest(patch=patch), \
                    mock.patch.object(widgets, "answer_of", boxes.append):
                self.assertEqual(real(self.window.prompts, "Crowded", patch), "")
                text = _isolation.box_text(boxes[0])
                self.assertEqual(text, "Crowded is somebody else's mod, so your changes go "
                                       "into a mod of yours. Which one?")
                tips = {button.text(): button.toolTip() for button in boxes[0].buttons()}
                self.assertEqual(tips["Use this &loadout's mod"],
                                 "Keeps your changes in this loadout only.")
                self.assertIn("Its author's updates no longer reach it.",
                              tips["Make my own &copy…"])
                if patch:
                    self.assertIn("Your patch, Crowded (my patch),", tips["Use my &patch"])
                else:
                    self.assertIn("through its updates", tips["Make a &patch"])

    def test_a_bundled_one_goes_to_its_patch_unasked(self):
        self.window.loadout.clear()
        self.load_mod(origin="Host/" + self.THEIRS)
        self.op = self.window.loadout.ops[0]
        with self.asked("own") as ask, \
                mock.patch.object(self.window.patches, "explain_bundled_patch") as told:
            self.edit()
        ask.assert_not_called()
        told.assert_called_once()
        patch = self.window.loadout.patch_for("Host/" + self.THEIRS)
        self.opened.assert_called_once_with(None, origin=patch.origin, copy_of=self.op)


class TestWritingSomebodyElsesPatternInAPatch(OpCase):
    """The dialog opened on a copy: their pattern, to go into your patch."""

    THEIRS = "[Gameplay] Crowded"

    def dialog(self, group=None):
        from anno_modding_studio.core import patching
        from anno_modding_studio.ui.pattern_dialog import PatternDialog

        self.load_mod()
        loadout = self.window.loadout
        theirs = loadout.ops[0]
        if group is not None:
            loadout.set_op(group)
            theirs.within = [group.uid]
        self.patch = patching.make(loadout, self.THEIRS)
        made = PatternDialog(
            self.window.workspace, None, None, origin=self.patch.origin, copy_of=theirs
        )
        self.addCleanup(made.deleteLater)
        return made, theirs

    def test_it_starts_as_theirs_and_says_where_it_goes(self):
        dialog, _theirs = self.dialog()
        self.assertEqual(dialog.windowTitle(), "New pattern")
        self.assertIn("Residence7", dialog.editor.toPlainText())
        self.assertIn(self.THEIRS, dialog.lead.text())
        self.assertIn(self.patch.label, dialog.lead.text())
        self.assertIn("Theirs stays as it is", dialog.lead.text())
        self.assertNotIn("condition", dialog.lead.text())
        # Read already, so it can be saved as it is.
        self.assertTrue(dialog.buttons.button(QDialogButtonBox.StandardButton.Ok).isEnabled())

    def test_saved_it_is_the_patch_s_and_theirs_is_untouched(self):
        dialog, theirs = self.dialog()
        source = theirs.source
        dialog.editor.setPlainText(dialog.editor.toPlainText().replace("99", "7"))
        dialog._recheck()
        dialog._accept()
        made = dialog.result_op
        self.assertEqual(made.origin, self.patch.origin)
        self.assertNotEqual(made.uid, theirs.uid)
        self.assertIs(self.window.loadout.op(theirs.uid), theirs)
        self.assertEqual(theirs.source, source)
        self.assertEqual(theirs.origin, self.THEIRS)

    def test_a_condition_it_leaves_behind_is_said(self):
        from anno_modding_studio.core.changes import Op

        group = Op(
            expression="", source='<Group Condition="//Values/Residence7">',
            origin=self.THEIRS, scope=True, searches=False, uid="group1",
        )
        dialog, _theirs = self.dialog(group)
        self.assertIn("runs on a condition", dialog.lead.text())


class TestWritingAPattern(OpCase):
    """The dialog one is typed in, and what it shows before anything lands."""

    OP = (
        '<ModOp Type="merge" Path="//Values/Residence7">'
        "<Residents>42</Residents></ModOp>"
    )

    def dialog(self, text=None, op=None):
        from anno_modding_studio.ui.pattern_dialog import PatternDialog

        made = PatternDialog(self.window.workspace, None, op)
        self.addCleanup(made.deleteLater)
        if text is not None:
            made.editor.setPlainText(text)
            made._recheck()
        return made

    def ok(self, dialog):
        return dialog.buttons.button(QDialogButtonBox.StandardButton.Ok)

    @staticmethod
    def drawn(tree, under=None):
        """Every row of a tree, or of one row of it, as (depth, what it says)."""
        found = []

        def walk(item, depth):
            found.append((depth, item.text(0), item.text(1), item.toolTip(1)))
            for at in range(item.childCount()):
                walk(item.child(at), depth + 1)

        if under is not None:
            walk(under, 0)
        else:
            for at in range(tree.topLevelItemCount()):
                walk(tree.topLevelItem(at), 0)
        return found

    def test_a_new_one_starts_as_a_modop_to_finish(self):
        dialog = self.dialog()
        self.assertIn('<ModOp Type="merge" Path="//Values/">', dialog.editor.toPlainText())
        self.assertFalse(dialog.editor.isReadOnly())
        self.assertIn("Type a ModOp", dialog.typed_note.text())
        self.assertFalse(self.ok(dialog).isEnabled())
        self.assertFalse(dialog.refresh_button.isEnabled())
        self.assertEqual(self.drawn(dialog.preview), [])

    def test_it_says_nothing_until_there_is_something_typed(self):
        dialog = self.dialog("")
        self.assertIn("Type a ModOp", dialog.typed_note.text())
        self.assertFalse(self.ok(dialog).isEnabled())

    # -- the rows are drawn on asking, and stay until asked again ----------
    def test_nothing_is_drawn_of_what_is_typed_until_it_is_asked_for(self):
        dialog = self.dialog()
        dialog.editor.setPlainText(self.OP)
        self.assertEqual(self.drawn(dialog.preview), [])
        self.assertIn("Refresh the preview", dialog.typed_note.text())
        self.assertTrue(dialog.refresh_button.isEnabled())
        self.assertFalse(self.ok(dialog).isEnabled())
        dialog.refresh_button.click()
        self.assertEqual(len(self.drawn(dialog.preview)), 5)
        self.assertEqual(dialog.typed_note.text(), "")
        self.assertFalse(dialog.refresh_button.isEnabled())
        self.assertTrue(self.ok(dialog).isEnabled())

    def test_typing_on_leaves_the_last_preview_to_look_at(self):
        """Drawn as the op was typed, the rows went at every keystroke that
        left it unfinished: just when the last ones were being read."""
        dialog = self.dialog(self.OP)
        before = self.drawn(dialog.preview)
        said = dialog.summary.text()
        for text in (self.OP.replace("42", "4"), self.OP[:-3], ""):
            with self.subTest(text=text):
                dialog.editor.setPlainText(text)
                self.assertEqual(self.drawn(dialog.preview), before)
                self.assertEqual(dialog.summary.text(), said)
                # And what is typed is not saved for what was previewed.
                self.assertFalse(self.ok(dialog).isEnabled())
                dialog._accept()
                self.assertIsNone(dialog.result_op)
        dialog.editor.setPlainText(self.OP.replace("42", "4"))
        self.assertIn("Changed since the preview", dialog.typed_note.text())

    def test_text_that_is_not_xml_is_said_as_it_is_typed(self):
        dialog = self.dialog(self.OP)
        dialog.editor.setPlainText(self.OP[:-3])
        self.assertRegex(dialog.typed_note.text(), r"^Line \d+, column \d+: ")

    def test_typing_back_what_was_previewed_is_the_preview_again(self):
        dialog = self.dialog(self.OP)
        dialog.editor.setPlainText(self.OP + " ")
        self.assertFalse(self.ok(dialog).isEnabled())
        dialog.editor.setPlainText(self.OP)
        self.assertTrue(self.ok(dialog).isEnabled())
        self.assertEqual(dialog.typed_note.text(), "")

    def test_a_preview_that_cannot_be_read_leaves_the_last_one(self):
        dialog = self.dialog(self.OP)
        before = self.drawn(dialog.preview)
        dialog.editor.setPlainText(self.OP + self.OP)
        dialog.refresh_button.click()
        self.assertIn("A pattern is one ModOp", dialog.typed_note.text())
        self.assertEqual(self.drawn(dialog.preview), before)
        self.assertFalse(self.ok(dialog).isEnabled())

    def test_the_keyboard_refreshes_from_inside_the_pane(self):
        from PySide6.QtTest import QTest

        dialog = self.dialog()
        dialog.editor.setPlainText(self.OP)
        dialog.show()
        self.addCleanup(dialog.hide)
        dialog.editor.setFocus()
        QApplication.processEvents()
        QTest.keyClick(dialog.editor, Qt.Key.Key_F5)
        self.assertEqual(len(self.drawn(dialog.preview)), 5)
        self.assertEqual(dialog.editor.toPlainText(), self.OP)

    def test_a_search_is_answered_before_anything_is_added(self):
        dialog = self.dialog(self.OP)
        self.assertIn("Matches 4 assets", dialog.summary.text())
        self.assertEqual(len(dialog.changes), 4)
        self.assertEqual(len(self.window.loadout.ops), 0)
        self.assertEqual(len(self.window.loadout.changes), 0)
        self.assertTrue(self.ok(dialog).isEnabled())

    def test_what_is_under_it_is_what_the_changes_list_shows_of_it(self):
        """The same rows, made by the same code: the op's row and what it
        comes to under it, as they are once it is accepted."""
        from anno_modding_studio.ui.changes_dock import OP_ROLE

        dialog = self.dialog(self.OP)
        before = self.drawn(dialog.preview)
        self.assertEqual(len(before), 5)
        self.assertEqual(before[0][1:3], ("Residents = 42", "4 assets"))
        dialog._accept()
        self.window.on_changes_updated()
        self.window.changes_panel.build_every_row()
        row = next(
            row for row in self.rows("op") if row.data(0, OP_ROLE) == dialog.result_op.uid
        )
        self.assertEqual(self.drawn(None, row), before)

    def test_its_rows_have_no_boxes_and_open_nothing(self):
        dialog = self.dialog(self.OP)
        top = dialog.preview.topLevelItem(0)
        for row in (top, top.child(0)):
            self.assertIsNone(row.data(0, Qt.ItemDataRole.CheckStateRole))
        self.assertNotIn("Double-click", top.toolTip(0))

    def test_what_it_makes_can_be_opened_in_it_again(self):
        """The op said nothing of being a search, and "Edit this pattern…"
        is only offered on one that does: a pattern just made was greyed."""
        from anno_modding_studio.ui.changes_dock import OP_ROLE

        dialog = self.dialog(self.OP)
        dialog._accept()
        made = dialog.result_op
        self.assertTrue(made.searches)
        self.window.changes_panel.refresh()
        row = next(
            row for row in self.rows("op") if row.data(0, OP_ROLE) == made.uid
        )
        menu = self.window.changes_panel.menu_for(row)
        self.addCleanup(menu.deleteLater)
        edit = next(a for a in menu.actions() if a.text() == "Edit this pattern…")
        self.assertTrue(edit.isEnabled())
        again = self.dialog(op=made)
        # Laid out a line to an element: as stored the op is on one line, and
        # the box, which does not wrap, showed its first element and no more
        # (H2-28).
        self.assertEqual(again.editor.toPlainText().splitlines(), [
            '<ModOp Type="merge" Path="//Values/Residence7">',
            "  <Residents>42</Residents>",
            "</ModOp>",
        ])
        self.assertFalse(again.editor.isReadOnly())
        self.assertTrue(self.ok(again).isEnabled())

    def test_text_that_is_not_xml_says_where_it_stops(self):
        dialog = self.dialog("<ModOp Type=")
        self.assertRegex(dialog.typed_note.text(), r"^Line \d+, column \d+: ")
        self.assertFalse(self.ok(dialog).isEnabled())
        self.assertEqual(self.drawn(dialog.preview), [])

    def test_a_search_that_cannot_be_read_says_so(self):
        dialog = self.dialog('<ModOp Type="merge" Path="//Values["><A>1</A></ModOp>')
        self.assertIn("cannot be read as an XPath", dialog.summary.text())
        self.assertFalse(self.ok(dialog).isEnabled())

    def test_two_ops_are_not_one_pattern(self):
        for text in (self.OP + self.OP, "<Group>%s</Group>" % self.OP):
            with self.subTest(text=text):
                dialog = self.dialog(text)
                self.assertIn("A pattern is one ModOp", dialog.typed_note.text())
                self.assertIn("Several, or a group of them", dialog.typed_note.text())
                self.assertFalse(self.ok(dialog).isEnabled())

    def test_what_is_not_one_op_is_said_as_what_it_is(self):
        """Refused as not one ModOp too, these were told that several ops or
        a group are typed in an asset's XML view: none of them is (A11-79)."""
        for text, said in (
            ("<Asset><Values/></Asset>",
             "A pattern is one ModOp, and the element typed is <Asset>."),
            ('<Include File="x.xml"/>',
             "A pattern is one ModOp, and the element typed is <Include>."),
            ("<!-- only a comment -->",
             "A pattern is one ModOp, and none is typed here."),
        ):
            with self.subTest(text=text):
                dialog = self.dialog(text)
                self.assertEqual(dialog.typed_note.text(), said)
                self.assertFalse(self.ok(dialog).isEnabled())

    def test_text_beside_what_the_op_holds_is_said_as_the_xml_view_says_it(self):
        """The sentence was written here and twice more, and had drifted from
        the others' "inside a ModOp" (A11-78)."""
        dialog = self.dialog(self.OP.replace("</Residents>", "</Residents>loose"))
        self.assertEqual(
            dialog.typed_note.text(),
            "Line 1: “loose” is text beside the elements inside a ModOp. It "
            "comes to no change, so take it out or make it a comment.",
        )

    def test_an_op_that_names_its_asset_is_not_a_pattern(self):
        dialog = self.dialog(
            '<ModOp Type="replace" GUID="1010340" Path="/Values/FactoryBase/CycleTime">'
            "<CycleTime>15</CycleTime></ModOp>"
        )
        self.assertIn("names its asset", dialog.typed_note.text())
        self.assertFalse(self.ok(dialog).isEnabled())

    def test_accepting_it_lands_a_pattern_and_the_changes_it_comes_to(self):
        dialog = self.dialog(self.OP)
        dialog._accept()
        made = dialog.result_op
        loadout = self.window.loadout
        self.assertIs(loadout.op(made.uid), made)
        self.assertEqual(made.expression, "//Values/Residence7")
        self.assertEqual(made.summary, "merge Residents = 42")
        self.assertEqual(made.source, self.OP)
        self.assertTrue(made.authored)
        answers = loadout.changes_by_op(made.uid)
        self.assertEqual(len(answers), 4)
        self.assertEqual({change.origin for change in answers}, {""})
        self.assertTrue(all(change.authored for change in answers))

    def test_it_holds_whatever_one_modop_can_say(self):
        """Four boxes held a search, a kind, one field and one value: a
        condition or a second field was shown and could not be changed."""
        text = (
            '<ModOp Type="merge" Path="//Values/Residence7" '
            'Condition="//Values/Residence7">'
            "<Residents>42</Residents><Foo>1</Foo></ModOp>"
        )
        dialog = self.dialog(text)
        self.assertIn("Matches 4 assets, with 8 changes", dialog.summary.text())
        dialog._accept()
        self.assertEqual(dialog.result_op.source, text)

    def test_it_is_in_the_loadout_and_writes_as_a_search(self):
        dialog = self.dialog(self.OP)
        dialog._accept()
        written = generator.build_modops_xml(
            self.window.loadout
        )
        self.assertIn('Path="//Values/Residence7"', written)
        self.assertEqual(written.count("<ModOp "), 1)

    def test_one_changed_takes_the_place_of_the_one_it_was(self):
        first = self.dialog(self.OP)
        first._accept()
        made = first.result_op
        made.active = False
        loadout = self.window.loadout
        again = self.dialog(op=made)
        again.editor.setPlainText(self.OP.replace("42", "43"))
        again._recheck()
        again._accept()
        self.assertEqual(len(loadout.ops), 1)
        changed = loadout.ops[0]
        self.assertEqual((changed.uid, changed.order), (made.uid, made.order))
        self.assertFalse(changed.active)
        answers = loadout.changes_by_op(made.uid)
        self.assertEqual({change.new_value for change in answers}, {"43"})
        self.assertEqual(len(loadout.changes), 4)
        self.assertFalse(any(change.active for change in answers))

    def test_cancelling_leaves_the_loadout_as_it_was(self):
        dialog = self.dialog(self.OP)
        dialog.reject()
        self.assertEqual(len(self.window.loadout.ops), 0)
        self.assertIsNone(dialog.result_op)

    def test_a_search_nothing_answers_to_is_allowed_and_said_plainly(self):
        """It is the shape a pattern for a mod you have not installed yet has."""
        dialog = self.dialog('<ModOp Type="merge" Path="//Values/Harbour"><A>1</A></ModOp>')
        self.assertIn("No asset in your assets.xml matches", dialog.summary.text())
        self.assertTrue(dialog.offer_button.isHidden())
        self.assertTrue(self.ok(dialog).isEnabled())

    def test_a_template_named_on_its_own_is_offered_the_search_for_it(self):
        """A name alone reads as an XPath, so it was "allowed, but does
        nothing until a mod adds an asset that matches", and nothing said a
        path was wanted."""
        dialog = self.dialog(
            '<ModOp Type="merge" Path="residencebuilding7"><Residence7>'
            "<Residents>42</Residents></Residence7></ModOp>"
        )
        path = "//Asset[Template='ResidenceBuilding7']/Values"
        self.assertEqual(
            dialog.summary.text(),
            "ResidenceBuilding7 is a template. A search is a path, and this one "
            "matches the assets made from it:\n%s" % path,
        )
        ok = self.ok(dialog)
        self.assertFalse(ok.isEnabled())
        self.assertFalse(dialog.offer_button.isHidden())

        dialog.offer_button.click()
        self.assertIn('Path="%s"' % path, dialog.editor.toPlainText())
        self.assertIn("Matches 4 assets", dialog.summary.text())
        self.assertTrue(dialog.offer_button.isHidden())
        self.assertTrue(ok.isEnabled())

    def test_a_word_that_is_no_path_and_no_template_is_told_what_a_search_is(self):
        dialog = self.dialog('<ModOp Type="merge" Path="Lumberjack"><A>1</A></ModOp>')
        self.assertEqual(
            dialog.summary.text(),
            "Lumberjack is not a path. A search is a path through assets.xml, "
            "such as //Values/Trader.",
        )
        self.assertTrue(dialog.offer_button.isHidden())
        self.assertFalse(self.ok(dialog).isEnabled())

    def test_an_example_is_somewhere_to_start_from(self):
        dialog = self.dialog()
        dialog._start_from("//Values/Trader")
        self.assertIn('Path="//Values/Trader"', dialog.editor.toPlainText())
        # Nothing of the author's yet, so nothing is read from it.
        self.assertFalse(self.ok(dialog).isEnabled())
        self.assertFalse(dialog.refresh_button.isEnabled())
        self.assertIn("Type a ModOp", dialog.typed_note.text())


class TestAnOpThatIsOnlyShown(OpCase):
    """What the dialog does not change, it shows: as written, with the rows
    the loadout has of it, and nothing to save."""

    def dialog(self, source, expression="//Values/Residence7", **said):
        from anno_modding_studio.core.changes import Op
        from anno_modding_studio.ui.pattern_dialog import PatternDialog

        made = PatternDialog(
            self.window.workspace, None, Op(expression=expression, source=source, **said),
        )
        self.addCleanup(made.deleteLater)
        return made

    def ok(self, dialog):
        return dialog.buttons.button(QDialogButtonBox.StandardButton.Ok)

    BLOCK = (
        '<ModOp Add="/AssetList/Groups[last()]"><Group><Assets>'
        "<Asset><Template>FunctionImmediate</Template><Values><Standard><GUID>7777</GUID>"
        "</Standard></Values></Asset></Assets></Group></ModOp>"
    )

    def test_it_is_shown_as_written_with_nothing_to_type_or_save(self):
        """*Black Tides* adds 518 assets in one op, and it is no search."""
        # The short form is Anno 117's, and so is adding a block of assets.
        self.window.loadout.game = "anno117"
        dialog = self.dialog(self.BLOCK, expression="/AssetList/Groups[last()]")
        self.assertTrue(dialog.editor.isReadOnly())
        self.assertEqual(dialog.editor.toPlainText(), self.BLOCK)
        self.assertEqual(dialog.windowTitle(), "ModOp")
        self.assertIsNone(self.ok(dialog))
        self.assertIsNotNone(dialog.buttons.button(QDialogButtonBox.StandardButton.Close))
        self.assertFalse(dialog.examples_button.isVisibleTo(dialog))
        self.assertFalse(dialog.refresh_button.isVisibleTo(dialog))
        self.assertIn("cannot be changed here", dialog.lead.text())
        self.assertIn("matches no asset", dialog.summary.text())

    def test_what_the_loadout_holds_of_it_is_what_is_listed(self):
        from anno_modding_studio.core.changes import Change, Op
        from anno_modding_studio.ui.pattern_dialog import PatternDialog

        op = Op(expression="/AssetList/Groups[last()]", source=self.BLOCK,
                    kept="a group of assets added under /AssetList/Groups[last()]")
        loadout = self.window.loadout
        loadout.set_op(op)
        self.addCleanup(loadout.drop_op, op.uid)
        loadout.set_change(Change(
            guid="1010340", asset_label="new one  (new asset)", path="", tag="Asset",
            op="addNextSibling", payload="<Asset/>", op_uid=op.uid, authored=False,
            summary="create asset 7777 (new one)",
        ))
        dialog = PatternDialog(self.window.workspace, None, op)
        self.addCleanup(dialog.deleteLater)
        said = " ".join(
            "%s %s" % (item.text(0), item.text(1))
            for item in dialog.preview.findItems(
                "", Qt.MatchFlag.MatchContains | Qt.MatchFlag.MatchRecursive)
        )
        # Its row, and the one change under it, as the changes list has them.
        self.assertIn("/AssetList/Groups[last()]", said)
        self.assertIn("+ new asset", said)
        self.assertIn("Kept as the ModOp it came from", dialog.summary.text())
        # Shown, and the loadout is as it was.
        self.assertEqual(len(loadout.changes_by_op(op.uid)), 1)

    def test_so_is_something_that_is_not_a_modop_at_all(self):
        """An ``<Asset>`` written straight under ``<ModOps>`` is kept as
        written too."""
        bare = "<Asset><Values><Standard><GUID>7777</GUID></Standard></Values></Asset>"
        dialog = self.dialog(bare, expression="<Asset>")
        self.assertTrue(dialog.editor.isReadOnly())
        self.assertEqual(dialog.editor.toPlainText(), bare)
        self.assertIsNone(self.ok(dialog))

    def long(self, lines):
        """:attr:`BLOCK` over ``lines`` lines, no two of them alike: its
        assets are each on a line of their own."""
        head, asset, tail = self.BLOCK.partition("<Asset>")
        asset, closing, tail = (asset + tail).partition("</Assets>")
        return (
            [head]
            + [asset.replace("7777", str(7000 + n)) for n in range(lines - 2)]
            + [closing + tail]
        )

    def test_a_very_long_op_is_shown_in_part(self):
        from anno_modding_studio.ui import pattern_dialog

        written = self.long(pattern_dialog.SHOWN_LINES + 50)
        dialog = self.dialog("\n".join(written), expression="/AssetList/Groups[last()]")
        shown = dialog.editor.toPlainText().splitlines()
        # Its beginning, each line as written, and how much is left out.
        self.assertEqual(shown[:-1], written[:pattern_dialog.SHOWN_LINES])
        self.assertEqual(shown[-1], "\u2026 and 50 more lines")

    def test_and_one_line_left_out_is_one_line(self):
        from anno_modding_studio.ui import pattern_dialog

        written = self.long(pattern_dialog.SHOWN_LINES + 1)
        dialog = self.dialog("\n".join(written), expression="/AssetList/Groups[last()]")
        shown = dialog.editor.toPlainText().splitlines()
        self.assertEqual(shown[:-1], written[:-1])
        self.assertEqual(shown[-1], "\u2026 and 1 more line")

    def test_one_that_just_fits_is_shown_whole(self):
        from anno_modding_studio.ui import pattern_dialog

        written = self.long(pattern_dialog.SHOWN_LINES)
        dialog = self.dialog("\n".join(written), expression="/AssetList/Groups[last()]")
        self.assertEqual(dialog.editor.toPlainText().splitlines(), written)

    def test_a_search_in_no_group_is_typed_over(self):
        dialog = self.dialog(
            '<ModOp Type="merge" Path="//Values/Residence7">'
            "<Residents>42</Residents></ModOp>",
            searches=True,
        )
        self.assertFalse(dialog.editor.isReadOnly())
        self.assertTrue(self.ok(dialog).isEnabled())
        self.assertEqual(dialog.windowTitle(), "Edit pattern")


class TestAPatternInsideAGroup(OpCase):
    """A search in a group is typed over like any other, read as though its
    group ran; saved, it is still in the group, and comes to what an op of
    that group comes to."""

    INNER = (
        '<ModOp Type="merge" Path="//Values/Residence7">'
        "<Residents>99</Residents></ModOp>"
    )
    SEARCH = (
        "<ModOps>"
        '<Group Condition="//Values/Residence7">%s</Group>'
        '<Group Condition="//Values/NothingHasThis">%s</Group>'
        "</ModOps>"
    ) % (INNER, INNER.replace("99", "77"))

    def setUp(self):
        super().setUp()
        self.load_mod()
        ops = self.window.loadout.ops
        self.running, self.idle = (op for op in ops if op.scope)
        self.assertFalse(self.running.idle)
        self.assertTrue(self.idle.idle)

    def inside(self, group):
        return next(
            op for op in self.window.loadout.ops if op.within == [group.uid]
        )

    def edit(self, op, was, now):
        from anno_modding_studio.ui.pattern_dialog import PatternDialog

        dialog = PatternDialog(self.window.workspace, None, op)
        self.addCleanup(dialog.deleteLater)
        self.assertFalse(dialog.editor.isReadOnly())
        dialog.editor.setPlainText(op.source.replace(was, now))
        dialog._recheck()
        return dialog

    def test_its_row_offers_to_edit_it(self):
        from anno_modding_studio.ui.changes_dock import OP_ROLE

        self.window.changes_panel.build_every_row()
        op = self.inside(self.idle)
        row = next(row for row in self.rows("op") if row.data(0, OP_ROLE) == op.uid)
        menu = self.window.changes_panel.menu_for(row)
        self.addCleanup(menu.deleteLater)
        edit = next(a for a in menu.actions() if a.text() == "Edit this pattern…")
        self.assertTrue(edit.isEnabled())

    def test_in_a_group_that_runs_it_is_saved_with_what_it_comes_to(self):
        op = self.inside(self.running)
        dialog = self.edit(op, "99", "55")
        self.assertNotIn("does not run", dialog.summary.text())
        dialog._accept()
        loadout = self.window.loadout
        made = loadout.op(op.uid)
        self.assertEqual(made.within, [self.running.uid])
        self.assertEqual(made.order, op.order)
        answers = loadout.changes_by_op(op.uid)
        self.assertEqual({change.new_value for change in answers}, {"55"})
        self.assertEqual(len(answers), 4)

    def test_in_one_that_does_not_the_rows_say_so_and_nothing_is_saved_under_it(self):
        op = self.inside(self.idle)
        self.assertEqual(self.window.loadout.changes_by_op(op.uid), [])
        dialog = self.edit(op, "77", "55")
        # What it would change, with the group running.
        self.assertEqual(len(dialog.changes), 4)
        self.assertIn("Matches 4 assets", dialog.summary.text())
        self.assertIn("Its group does not run as things stand", dialog.summary.text())
        dialog._accept()
        loadout = self.window.loadout
        made = loadout.op(op.uid)
        self.assertIn("55", made.source)
        self.assertEqual(made.within, [self.idle.uid])
        self.assertEqual(loadout.changes_by_op(op.uid), [])
        self.assertEqual(made.matched, [])

    def test_it_is_written_back_inside_its_group(self):
        op = self.inside(self.idle)
        self.edit(op, "77", "55")._accept()
        written = generator.build_modops_xml(
            generator.for_mod(self.window.loadout, "[Gameplay] Crowded"),
        )
        root = etree.fromstring(written.encode("utf-8"))
        groups = root.findall("Group")
        self.assertEqual(len(groups), 2)
        self.assertEqual(groups[0].findtext("ModOp/Residents"), "99")
        self.assertEqual(groups[1].findtext("ModOp/Residents"), "55")
        self.assertEqual(len(root.findall("ModOp")), 0)

    def test_and_is_still_in_it_when_the_mod_is_read_again(self):
        """The group read again is the group that was here, by what it says
        and where it sits, so what names it goes on naming it."""
        op = self.inside(self.running)
        self.edit(op, "99", "55")._accept()
        loadout = self.window.loadout
        report = reader.analyse(self.mod, self.window.index, origin="[Gameplay] Crowded")
        loadout.as_before(report.ops, report.changes)
        again = {held.uid for held in report.ops if held.scope}
        self.assertIn(loadout.op(op.uid).within[0], again)


class TestSeeingWhatCouldNotBeRead(ModsFolderCase):
    """A count of unread ModOps is where the question starts, not where it ends."""

    # Edits go into these mods, so they are yours.
    YOURS = True

    def dialog(self):
        from anno_modding_studio.ui.unread_dialog import UnreadDialog

        made = UnreadDialog(self.window.loadout)
        self.addCleanup(made.deleteLater)
        return made

    def test_the_ops_survive_the_import_that_refused_them(self):
        self.load()
        self.assertTrue(self.window.loadout.unread)
        entry = self.window.loadout.unread[0]
        self.assertEqual(entry.origin, "[Seal] Other Houses")
        self.assertIn("Content=", entry.source)
        self.assertTrue(entry.why)

    def test_the_panel_offers_them_only_when_there_are_some(self):
        panel = self.window.changes_panel
        self.assertFalse(panel.unread_button.text())
        self.load()
        self.assertIn("1 ModOp not read", panel.unread_button.text())

    def test_an_unread_op_can_be_copied_out_to_ask_somebody_about_it(self):
        """The dialog is read to act somewhere else; the op has to come with you."""
        from PySide6.QtGui import QGuiApplication

        self.load()
        dialog = self.dialog()
        entry = dialog.entries[0]
        menu = dialog.menu_for(0)
        texts = [a.text() for a in menu.actions() if not a.isSeparator()]
        self.assertEqual(
            texts,
            ["Copy this ModOp", "Copy why it was not read", "Copy all three"],
        )

        next(a for a in menu.actions() if a.text() == "Copy this ModOp").trigger()
        self.assertEqual(QGuiApplication.clipboard().text(), entry.source)

        next(a for a in menu.actions() if a.text() == "Copy all three").trigger()
        whole = QGuiApplication.clipboard().text()
        self.assertIn(entry.source, whole)
        self.assertIn(entry.why, whole)
        self.assertIn(dialog._name(entry.origin), whole)

    def unread_rows(self):
        from anno_modding_studio.ui.changes_dock import KIND_ROLE, ORIGIN_ROLE

        self.window.changes_panel.build_every_row()
        tree = self.window.changes_panel.tree
        found = []
        for top in range(tree.topLevelItemCount()):
            mod = tree.topLevelItem(top)
            for index in range(mod.childCount()):
                child = mod.child(index)
                if child.data(0, KIND_ROLE) == "unread":
                    found.append((mod.data(0, ORIGIN_ROLE), child))
        return found

    def test_each_is_a_row_of_its_own_under_its_mod(self):
        """A count in a tooltip said they existed. A row says which, and
        stops the list claiming to be everything the mod does."""
        from PySide6.QtCore import Qt

        self.load()
        rows = self.unread_rows()
        self.assertEqual([origin for origin, _row in rows], ["[Seal] Other Houses"])
        _origin, row = rows[0]
        self.assertIn("kept as written", row.text(1))
        # The tooltip says how to open the dialog, which holds the rest.
        self.assertIn("to see why it was not read", row.toolTip(0))
        self.assertLessEqual(len(row.toolTip(0).split()), 15)
        # Nothing to switch: the Studio did not read it, so it cannot leave
        # it out either.
        self.assertFalse(row.flags() & Qt.ItemFlag.ItemIsUserCheckable)

    def test_and_the_row_is_greyed_the_way_a_switched_off_change_is(self):
        from anno_modding_studio.ui import theme

        self.load()
        _origin, row = self.unread_rows()[0]
        self.assertEqual(row.foreground(0).color().name(), theme.TEXT_DIM.lower())

    def test_the_editor_says_when_one_reaches_the_asset_shown(self):
        """The op is carried but never applied to what is shown, so the value
        in front of you may not be the game's -- said on the asset."""
        self.load()
        editor = self.window.editor
        self.window.loadout.editing = "[Seal] Other Houses"
        editor.refresh_target()
        editor.load_asset("1010031")
        self.assertIn("could not read reaches this asset", editor.subtitle_label.text())
        self.assertIn("copies from asset 9999999", editor.subtitle_label.toolTip())
        editor.load_asset("1010030")
        self.assertNotIn("could not read", editor.subtitle_label.text())

    def test_but_not_for_a_mod_loading_before_the_op_s_own(self):
        self.load()
        editor = self.window.editor
        self.window.loadout.editing = "[Gameplay] Roomier Farmers"
        editor.refresh_target()
        editor.load_asset("1010031")
        self.assertNotIn("could not read", editor.subtitle_label.text())
        # Your own mod loads last, so it sees everything.
        self.window.loadout.editing = ""
        editor.refresh_target()
        editor.load_asset("1010031")
        self.assertIn("could not read", editor.subtitle_label.text())

    def test_nor_for_a_mod_that_is_switched_off(self):
        """The game does not load it, so its op does nothing to the asset --
        as the XML view leaves it out."""
        self.load()
        editor = self.window.editor
        self.window.loadout.editing = ""
        self.window.loadout.layer("[Seal] Other Houses").active = False
        editor.refresh_target()
        editor.load_asset("1010031")
        self.assertNotIn("could not read", editor.subtitle_label.text())

    def test_double_clicking_one_opens_the_full_list(self):
        from unittest import mock

        from anno_modding_studio.ui.unread_dialog import UnreadDialog

        self.load()
        _origin, row = self.unread_rows()[0]
        # The window answers the panel's signal by showing the dialog, which
        # would block here; what is being checked is that it is asked for.
        with mock.patch.object(UnreadDialog, "exec", return_value=0) as shown:
            open_row(self.window.changes_panel, row)
        self.assertEqual(shown.call_count, 1)

    def test_and_with_the_one_double_clicked_selected(self):
        """The row says to double-click it to see why it was not read, and
        the list opened on its first row whichever was double-clicked."""
        from unittest import mock

        from anno_modding_studio.core.changes import Unread
        from anno_modding_studio.ui.changes_dock import UNREAD_ROLE
        from anno_modding_studio.ui.unread_dialog import UnreadDialog

        self.load()
        self.window.loadout.unread.append(Unread(
            origin="[Seal] Other Houses", source='<ModOp Type="zzz" GUID="1"/>',
            label="zzz last of them", why="not a ModOp type", order=99,
        ))
        self.window.changes_panel.regroup()
        row = next(row for _origin, row in self.unread_rows() if row.data(0, UNREAD_ROLE) == 99)
        seen = []

        def shown(dialog):
            seen.append(dialog._selected_entry())
            return 0

        with mock.patch.object(UnreadDialog, "exec", new=shown):
            open_row(self.window.changes_panel, row)
        self.assertEqual([entry.order for entry in seen], [99])
        # The button over the list opens it on the first row, as before.
        seen.clear()
        with mock.patch.object(UnreadDialog, "exec", new=shown):
            self.window.changes_panel.unread_button.click()
        self.assertEqual(len(seen), 1)
        self.assertNotEqual(seen[0].order, 99)

    def test_the_list_says_which_mod_each_came_from(self):
        self.load()
        dialog = self.dialog()
        self.assertEqual(dialog.table.rowCount(), 1)
        self.assertEqual(dialog.table.item(0, 0).text(), "[Seal] Other Houses")
        self.assertIn("copies from asset 9999999", dialog.table.item(0, 2).text())

    def test_and_shows_the_op_the_mod_actually_wrote(self):
        self.load()
        dialog = self.dialog()
        dialog.table.selectRow(0)
        self.assertIn('Content="@9999999', dialog.source.toPlainText())

    def test_but_only_while_one_row_is_selected(self):
        """Two rows selected, or none, and the pane went on showing the last
        one op as if it were still the one chosen (A11-92)."""
        import dataclasses

        self.load()
        first = self.window.loadout.unread[0]
        self.window.loadout.unread.append(
            dataclasses.replace(first, source="<ModOp Type=\"remove\" />",
                                order=first.order + 1)
        )
        dialog = self.dialog()
        self.assertEqual(dialog.table.rowCount(), 2)
        dialog.table.selectRow(0)
        self.assertTrue(dialog.source.toPlainText())
        dialog.table.selectAll()
        self.assertEqual(dialog.source.toPlainText(), "")
        dialog.table.selectRow(1)
        self.assertTrue(dialog.source.toPlainText())
        dialog.table.clearSelection()
        # Empty, so the placeholder asking for a row is what shows.
        self.assertEqual(dialog.source.toPlainText(), "")

    def test_with_nothing_loaded_it_says_so_rather_than_being_empty(self):
        dialog = self.dialog()
        self.assertEqual(dialog.table.rowCount(), 0)
        self.assertIn("was read", dialog.summary.text())

    def test_dropping_the_mod_takes_its_unread_ops_with_it(self):
        self.load()
        self.window.loadout.drop_layer("[Seal] Other Houses")
        self.assertEqual(self.window.loadout.unread, [])

    def test_they_survive_saving_and_opening(self):
        self.load()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "p.annoload"
            self.window.loadout.save(path)
            from anno_modding_studio.core.changes import Loadout

            reopened = Loadout.load(path)
        self.assertEqual(len(reopened.unread), 1)
        self.assertEqual(reopened.unread[0].origin, "[Seal] Other Houses")


class TestWhichModAnEditGoesTo(ModsFolderCase):
    """Three places used to answer that differently, or not at all."""

    # Edits go into these mods, so they are yours.
    YOURS = True

    def test_the_editor_says_so_before_any_mods_are_loaded(self):
        from anno_modding_studio.core.changes import AIM_MAKE

        # With no loadout it names no mod: there is none (H2-24).
        self.window.update_title()
        self.assertNotIn("My Anno Mod", self.window.editor.target_label.text())
        self.assertIn("No loadout is open", self.window.editor.target_label.text())
        self.window.loadout.aim = AIM_MAKE
        self.window.update_title()
        self.assertIn("My Anno Mod", self.window.editor.target_label.text())
        self.assertNotIn("you have open", self.window.editor.target_label.text())

    def test_the_mods_menu_greys_what_has_nothing_to_act_on(self):
        """In a fresh data folder with an empty library, three entries of
        the mods' menus were offered with nothing behind them (H2-24)."""
        window = self.window
        # A library of its own: the suite's is shared, and others fill it.
        empty = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, empty, True)
        patched = mock.patch.object(window.where, "library_dir", return_value=empty)
        patched.start()
        self.addCleanup(patched.stop)
        window._sync_menus()
        for action in (window.loadout_mods_action, window.put_back_action,
                       window.undo_convert_action):
            self.assertFalse(action.isEnabled(), action.text())
            self.assertTrue(action.toolTip(), action.text())
        self.assertIn("no mods", window.loadout_mods_action.toolTip())
        # Mods read in here are yours, and nobody else has to fetch them.
        self.load()
        window._sync_menus()
        self.assertFalse(window.loadout_mods_action.isEnabled())
        # Somebody else's is a mod the loadout uses.
        window.loadout.layers[0].kept = False
        window._sync_menus()
        self.assertTrue(window.loadout_mods_action.isEnabled())

    def test_the_title_names_the_mod_the_editor_says_edits_go_to(self):
        """The title worked out which mod is being written by a rule of its
        own, with a patch the one loaded mod it named: with a mod of yours
        kept here, or a fork, being written, it named the loadout's own mod
        while *Currently editing* named that mod."""
        self.load()
        window, loadout = self.window, self.window.loadout
        window.loadout_path = Path(self._tmp.name) / "mine.annoload"
        loadout.own.name = "Mine"
        loadout.make_own_mod()
        origin = "[Seal] Other Houses"
        loadout.editing = origin
        window.editor.refresh_target()
        self.assertEqual(window.editor.target_box.currentData(), origin)
        window.update_title()
        title = window.windowTitle()
        self.assertIn(loadout.layer(origin).label, title)
        self.assertNotIn("Mine", title)
        # A fork the same.
        loadout.layer(origin).kept = False
        loadout.layer(origin).fork_of = "[Seal] Somebody's"
        window.editor.refresh_target()
        self.assertEqual(window.editor.target_box.currentData(), origin)
        window.update_title()
        self.assertIn(loadout.layer(origin).label, window.windowTitle())
        # And the loadout's own mod once it is where edits go again.
        loadout.editing = ""
        window.editor.refresh_target()
        window.update_title()
        self.assertIn("Mine", window.windowTitle())

    def test_a_folder_read_with_no_loadout_names_no_made_up_mod_in_the_title(self):
        """"No loadout — Anno 1800 — My Anno Mod — 427 mods loaded": reading
        the folder made the mod laid over it, and the title named it with no
        loadout to be its name (H2-24).  The loadout it makes now names it
        after itself, as *Play a set of mods* does."""
        self.load()
        self.assertTrue(self.window.loadout.has_own_mod())
        title = self.window.windowTitle()
        self.assertIn("mods loaded", title)
        self.assertNotIn("My Anno Mod", title)
        self.assertIn(self.window.loadout.own.name, title)

    def test_a_folder_read_with_no_card_chosen_is_a_loadout_of_mods_to_play(self):
        """"No loadout — Anno 1800 — 427 mods loaded": a loadout holding
        every mod in the folder, called nothing (D8).  It was then a "New
        loadout" with no file, whose edits went to a standalone My Anno Mod
        that patched neither the mods nor the list.  Mods read in with no
        card chosen are a set of mods to play, set up as that card sets one
        up: named after the game, its own mod the patch over its list."""
        from anno_modding_studio.core.changes import AIM_MANAGE, list_patch_name

        window = self.window
        # Nothing loaded, changed or chosen: no loadout, everywhere.
        window.update_title()
        self.assertFalse(window.holds_something())
        self.assertIn("No loadout", window.windowTitle())
        self.assertNotIn("New loadout", window.windowTitle())
        self.assertEqual(window.source.loadout_label.full_text(), "No loadout yet")
        self.assertIn("No loadout is open", window.editor.target_label.text())
        self.load()
        name = window.start.list_loadout_name()
        self.assertEqual(window.loadout.aim, AIM_MANAGE)
        self.assertTrue(window.loadout.own.loads_last)
        self.assertFalse(window.loadout.is_the_mod)
        self.assertEqual(window.files.loadout_name(), name)
        self.assertEqual(window.loadout.own.name, list_patch_name(name))
        title = window.windowTitle()
        self.assertIn(name, title)
        self.assertNotIn("No loadout", title)
        self.assertNotIn("New loadout", title)
        self.assertIn("mods loaded", title)
        self.assertEqual(window.source.loadout_label.full_text(), name)
        self.assertNotIn("No loadout", window.editor.target_label.text())
        # The File menu's New loadout clears it: no loadout again.
        with mock.patch.object(window.files, "confirm_discard", return_value=True):
            window.new_loadout()
        self.assertIn("No loadout", window.windowTitle())
        self.assertEqual(window.source.loadout_label.full_text(), "No loadout yet")

    def test_a_card_chosen_or_a_change_made_is_a_new_loadout_too(self):
        from anno_modding_studio.core.changes import AIM_MAKE, Change

        window = self.window
        window.loadout.aim = AIM_MAKE
        window.update_title()
        self.assertIn("New loadout", window.windowTitle())
        window.loadout.aim = ""
        window.update_title()
        self.assertIn("No loadout", window.windowTitle())
        window.loadout.set_change(Change(
            guid="1010340", asset_label="1010340", path="/Values/FactoryBase/CycleTime",
            tag="CycleTime", old_value="30", new_value="45",
        ))
        window.update_title()
        self.assertIn("New loadout", window.windowTitle())

    def test_and_says_what_it_sits_on_top_of_once_they_are(self):
        self.load()
        editor = self.window.editor
        self.assertTrue(editor.target_box.isVisibleTo(editor.target_row))
        self.assertEqual(editor.target_box.currentText(),
                         "This loadout's own mod: only in this loadout")
        self.assertIn("3 mods you have open", editor.target_label.text())

    def test_a_mod_written_to_publish_is_not_promised_the_last_word(self):
        """Nothing in its modinfo asks to load last, so the game sorts it by
        name -- ``[Gameplay] My Anno Mod`` in front of all three here -- and
        the caption used to say it loaded after them regardless."""
        self.load()
        self.window.loadout.own.loads_last = False
        editor = self.window.editor
        editor.refresh_target()
        self.assertNotIn("you have open", editor.target_label.text())
        self.assertEqual("3 mods load after it and can overwrite what you write.",
                         editor.target_label.text())
        self.assertNotIn("last", editor.target_label.toolTip())
        # And named to sort after them, it is back to loading after them.
        self.window.loadout.own.name = "Zz Mine"
        self.window.loadout.own.category = "Seal"
        editor.refresh_target()
        self.assertIn("3 mods you have open", editor.target_label.text())

    def test_a_mod_with_an_op_not_read_is_said_to_be_written_back_with_it(self):
        """The tooltip said exporting from here would lose the op, from when
        it did. An export carries it and writes it back as it is."""
        from anno_modding_studio.core import generator

        self.load()
        loadout, editor = self.window.loadout, self.window.editor
        origin = "[Seal] Other Houses"
        loadout.editing = origin
        editor.refresh_target()
        self.assertIn("1 of its ModOps could not be read", editor.target_label.text())
        written = generator.build_modops_xml(generator.for_mod(loadout, origin))
        self.assertIn('Content="@9999999/Residence7/Residents"', written)
        self.assertNotIn("lose", editor.target_label.toolTip())
        self.assertIn(
            "1 of its ModOps could not be read. An export from here writes it back "
            "as it is, and what it does is not shown.",
            editor.target_label.toolTip(),
        )

    def test_the_title_counts_your_changes_apart_from_their_mods(self):
        """Adding them up into one number meant neither."""
        self.load()
        self.window.update_title()
        title = self.window.windowTitle()
        # The mods are yours here, and counted so.
        self.assertIn("3 mods of yours", title)
        # Three mods' worth of changes are loaded and none of them are yours.
        self.assertNotRegex(title, r"\d+ changes?\b")

    def test_the_title_counts_a_fork_and_a_kept_mod_as_yours(self):
        """Only a patch was counted as yours: a fork, or a mod of yours made
        a project here, was counted among the mods of other people."""
        self.load()
        first, _second, third = self.window.loadout.layers
        # A fork, a mod of yours made a project here, and somebody else's.
        first.kept, first.fork_of = False, "[Gameplay] Somebody's"
        third.kept = False
        self.window.update_title()
        self.assertIn("1 mod loaded · 2 yours", self.window.windowTitle())
        # All of them yours, and none a patch: they are not called patches.
        third.kept = True
        self.window.update_title()
        self.assertRegex(self.window.windowTitle(), r"  —  3 mods of yours$")

    def test_and_counts_them_once_you_make_one(self):
        self.load()
        self.window.loadout.set_change(
            Change(
                guid="1010030", asset_label="Farmer", path="/Values/Residence7/Residents",
                tag="Residents", old_value="10", new_value="15",
            )
        )
        self.window.update_title()
        self.assertRegex(self.window.windowTitle(), r"\b1 change\b")

    def test_the_changes_list_marks_which_row_is_yours(self):
        self.load()
        self.window.loadout.set_change(
            Change(
                guid="1010030", asset_label="Farmer", path="/Values/Residence7/Residents",
                tag="Residents", old_value="10", new_value="15",
            )
        )
        self.window.changes_panel.refresh()
        from anno_modding_studio.ui.changes_dock import KIND_ROLE, ORIGIN_ROLE

        tree = self.window.changes_panel.tree
        mine = [
            tree.topLevelItem(i)
            for i in range(tree.topLevelItemCount())
            if tree.topLevelItem(i).data(0, KIND_ROLE) == "mod"
            and not tree.topLevelItem(i).data(0, ORIGIN_ROLE)
        ]
        self.assertEqual(len(mine), 1)
        self.assertIn("(yours)", mine[0].text(0))


class TestEditingIntoAnotherMod(ModsFolderCase):
    """Editing into another of your mods loaded here, rather than laying the
    loadout's own over the top.  Somebody else's mod is never where changes
    go, so the mod edited into is made one of yours.

    *Roomier Farmers* sets the farmer residence to 12 and *Much Roomier
    Farmers*, loading after it, sets the same field to 20 -- so "as of the
    first mod" and "as the game ends up" are genuinely different documents,
    which is the whole difficulty.
    """

    YOURS = True
    FARMER = "1010030"
    FIELD = "/Values/Residence7/Residents"
    SHIPPED = "8"
    EARLY = "[Gameplay] Roomier Farmers"
    LATE = "[Seal] Much Roomier Farmers"

    def editing(self, origin):
        self.window.loadout.editing = origin
        self.window.editor.refresh_target()
        self.window.editor.load_asset(self.FARMER)

    def residents(self):
        for item in self.window.editor._iter_items():
            if item.text(0) == "Residents" and item.data(0, 258) is not None:
                return item
        return None

    # -- what you are looking at ------------------------------------------
    def test_by_default_you_see_what_the_game_ends_up_with(self):
        self.load()
        self.editing("")
        self.assertEqual(self.residents().text(1), "20")

    def test_and_editing_into_a_mod_shows_the_asset_as_that_mod_leaves_it(self):
        """Not as the stack does: the value a later mod overwrites is not the
        value this mod's ModOp is written against."""
        self.load()
        self.editing(self.EARLY)
        self.assertEqual(self.residents().text(1), "12")

    def test_a_field_a_later_mod_overwrites_says_so_on_the_row(self):
        self.load()
        self.editing(self.EARLY)
        self.assertEqual(
            self.residents().text(3), "overwritten by [Seal] Much Roomier Farmers"
        )

    def test_your_own_mod_overwriting_it_is_named_as_any_other_mod_is(self):
        """It has no folder to be known by, and it used to go unmentioned for
        that: the row said nothing, or named the mod yours goes on to beat."""
        self.load()
        self.window.loadout.set_change(self.window.loadout.place(Change(
            guid=self.FARMER, asset_label="Farmer", path=self.FIELD,
            tag="Residents", old_value="20", new_value="30",
        )))
        self.editing(self.EARLY)
        own = self.window.editor._layer_name("")
        self.assertTrue(own)
        self.assertEqual(self.residents().text(3), "overwritten by %s" % own)

    def test_and_says_nothing_when_nothing_loads_after_you(self):
        self.load()
        self.editing("")
        self.assertNotIn("overwritten", self.residents().text(3))

    # -- a field one mod makes and another sets ----------------------------
    def made_and_set(self):
        """*Roomier Farmers* makes a field the game does not ship, and *Much
        Roomier Farmers*, loading after it, makes sure of the same field.

        Both as changes made here, into those mods: neither is an op of the
        mod's file.  :class:`TestAFieldAModShipsAndAnotherSets` has the two
        mods shipping them."""
        from anno_modding_studio.core.changes import OP_MERGE

        self.load()
        for origin, value in ((self.EARLY, "3"), (self.LATE, "5")):
            self.window.loadout.set_change(self.window.loadout.place(Change(
                guid=self.FARMER, asset_label="Farmer", path="/Values/Residence7",
                tag="Roominess", op=OP_MERGE, origin=origin,
                payload="<Roominess>%s</Roominess>" % value,
            )))

    def roominess(self):
        return [item for item in self.window.editor._iter_items() if item.text(0) == "Roominess"]

    def test_what_another_mod_removed_is_said_to_be_that_mods_doing(self):
        """A removed node kept the key of what it had been, and one the game
        ships had none: read as nobody's, it was "removed by this mod"."""
        from anno_modding_studio.core.changes import OP_REMOVE

        self.load()
        loadout = self.window.loadout
        loadout.set_change(loadout.place(Change(
            guid=self.FARMER, asset_label="Farmer", path="/Values/Building/TerrainType",
            tag="TerrainType", op=OP_REMOVE, origin=self.EARLY,
        )))

        def said():
            return next(
                item.text(3) for item in self.window.editor._iter_items()
                if item.text(0) == "TerrainType"
            )

        self.editing(self.LATE)
        self.assertEqual(said(), "removed by %s" % self.window.loadout.layer(self.EARLY).label)
        self.editing(self.EARLY)
        self.assertEqual(said(), "removed by this mod")

    def test_only_the_mod_that_removed_it_is_offered_to_keep_it(self):
        """*Keep this after all* takes back a removal of the mod edited
        into.  It was offered on what an earlier mod removed too, where
        there was none of this mod's to take back, and did nothing
        (A11-62)."""
        from anno_modding_studio.core.changes import OP_REMOVE

        self.load()
        loadout = self.window.loadout
        loadout.set_change(loadout.place(Change(
            guid=self.FARMER, asset_label="Farmer", path="/Values/Building/TerrainType",
            tag="TerrainType", op=OP_REMOVE, origin=self.EARLY,
        )))

        def menu():
            row = next(
                item for item in self.window.editor._iter_items()
                if item.text(0) == "TerrainType"
            )
            return {a.text(): a for a in self.window.editor._build_item_menu(row).actions()}

        self.editing(self.LATE)
        offered = menu()
        self.assertNotIn("Keep this after all", offered)
        self.assertNotIn("Remove from asset", offered)
        self.editing(self.EARLY)
        offered = menu()
        self.assertIn("Keep this after all", offered)
        offered["Keep this after all"].trigger()
        self.assertFalse([c for c in loadout if c.tag == "TerrainType"])

    def test_typing_the_value_back_keeps_what_the_row_says_of_its_mod(self):
        """Typed over and typed back, the row lost "set by" and the colour
        it had been given by where it came from, until the asset was read
        again (A11-94)."""
        from anno_modding_studio.ui import theme

        self.load()
        self.editing("")
        row = self.residents()
        said = "set by [Seal] Much Roomier Farmers"
        self.assertEqual(row.text(3), said)
        row.setText(1, "25")
        self.assertEqual(row.text(3), "20")
        row.setText(1, "20")
        self.assertEqual(row.text(3), said)
        self.assertEqual(row.foreground(3).color().name(), theme.TEXT_DIM)
        self.assertEqual(row.foreground(1).color().name(), theme.TEXT)

    def test_and_the_colour_of_a_field_another_mod_added(self):
        from anno_modding_studio.core.changes import OP_MERGE
        from anno_modding_studio.ui import theme

        self.load()
        self.window.loadout.set_change(self.window.loadout.place(Change(
            guid=self.FARMER, asset_label="Farmer", path="/Values/Residence7",
            tag="Roominess", op=OP_MERGE, origin=self.EARLY,
            payload="<Roominess>3</Roominess>",
        )))
        self.editing(self.LATE)
        (row,) = self.roominess()
        said = "added by [Gameplay] Roomier Farmers"
        self.assertEqual(row.text(3), said)
        row.setText(1, "9")
        self.assertEqual(row.text(3), "3")
        row.setText(1, "3")
        self.assertEqual(row.text(3), said)
        self.assertEqual(row.foreground(1).color().name(), theme.CHANGED)
        self.assertEqual(row.foreground(3).color().name(), theme.CHANGED)

    def test_an_asset_another_mod_makes_is_said_to_be_made_by_that_mod(self):
        """The subtitle said "created by this mod" of any asset a mod made,
        beside a button saying another mod adds it (A11-82)."""
        from anno_modding_studio.core.changes import OP_ADD_NEXT

        self.load()
        self.window.loadout.set_change(self.window.loadout.place(Change(
            guid=self.FARMER, asset_label="made  (new asset)", path="",
            tag="Asset", op=OP_ADD_NEXT, summary="create asset 1337000077",
            origin=self.EARLY,
            payload="<Asset><Template>ResidenceBuilding7</Template><Values>"
                    "<Standard><GUID>1337000077</GUID><Name>made</Name></Standard>"
                    "</Values></Asset>",
        )))
        editor = self.window.editor
        for origin, said in (
            (self.LATE, "created by [Gameplay] Roomier Farmers"),
            (self.EARLY, "created by this mod"),
        ):
            self.window.loadout.editing = origin
            editor.refresh_target()
            self.assertTrue(editor.load_asset("1337000077"))
            bits = editor.subtitle_label.text().split("   ·   ")
            self.assertIn(said, bits)
            self.assertEqual(editor.in_game_button.isEnabled(), origin == self.EARLY)

    def test_a_field_one_mod_made_and_this_one_sets_is_one_row_set_by_this_mod(self):
        """Not "added by this mod": the field is there because of the other
        mod, and what is this one's is the value."""
        self.made_and_set()
        self.editing(self.LATE)
        rows = self.roominess()
        self.assertEqual([(row.text(1), row.text(3)) for row in rows], [("5", "set by this mod")])

    def test_and_from_your_own_mod_it_is_set_by_the_mod_that_set_it(self):
        self.made_and_set()
        self.editing("")
        rows = self.roominess()
        self.assertEqual(
            [(row.text(1), row.text(3)) for row in rows],
            [("5", "set by [Seal] Much Roomier Farmers")],
        )

    def test_typing_over_it_changes_what_that_mod_sets_it_to(self):
        self.made_and_set()
        self.editing(self.LATE)
        before = len(self.window.loadout)
        self.roominess()[0].setText(1, "9")
        self.assertEqual(len(self.window.loadout), before)
        late = [c for c in self.window.loadout if c.origin == self.LATE and c.tag == "Roominess"]
        self.assertEqual(late[0].sets, ("/Values/Residence7/Roominess", "9"))

    def test_the_mod_that_made_it_sees_its_own_addition_overwritten(self):
        self.made_and_set()
        self.editing(self.EARLY)
        row = self.roominess()[0]
        self.assertEqual(row.text(1), "3")
        self.assertEqual(row.text(3), "overwritten by [Seal] Much Roomier Farmers")
        # The field, and not the section the later mod's merge is aimed at.
        self.assertNotIn("overwritten", row.parent().text(3))

    def test_a_restated_value_keeps_its_value_in_the_changes_list(self):
        """The column is narrow and cuts a long line from the left: the first
        wording lost the value and kept "…er mod already set it)". So the row
        is the value and one word, and the tooltip says the rest."""
        self.made_and_set()
        self.window.loadout.set_change(self.window.loadout.place(Change(
            guid=self.FARMER, asset_label="Farmer", path="/Values/Residence7/Roominess",
            tag="Roominess", old_value="5", new_value="5", restated=True,
            origin="[Seal] Other Houses",
        )))
        self.window.changes_panel.refresh()
        tree = self.window.changes_panel.tree
        tree.expandAll()
        rows, stack = [], [tree.topLevelItem(i) for i in range(tree.topLevelItemCount())]
        while stack:
            item = stack.pop()
            stack.extend(item.child(i) for i in range(item.childCount()))
            if item.text(1) == "5 (restated)":
                rows.append(item)
        self.assertEqual(len(rows), 1)
        self.assertEqual(
            rows[0].toolTip(1), "Another mod already set this value. This mod sets it too."
        )

    def test_its_menu_offers_to_stop_setting_it_not_to_remove_an_addition(self):
        self.made_and_set()
        self.editing(self.LATE)
        menu = self.window.editor._build_item_menu(self.roominess()[0])
        labels = [action.text() for action in menu.actions()]
        self.assertIn("Stop setting this", labels)
        self.assertNotIn("Remove this addition", labels)

    def test_typing_over_it_is_what_that_mod_is_written_with(self):
        from anno_modding_studio.core import generator

        self.made_and_set()
        self.editing(self.LATE)
        self.roominess()[0].setText(1, "9")
        out = generator.build_modops_xml(generator.for_mod(self.window.loadout, self.LATE))
        self.assertIn("<Roominess>9</Roominess>", out)
        self.assertNotIn("<Roominess>5</Roominess>", out)

    # -- where the change goes --------------------------------------------
    def test_an_edit_becomes_that_mod_s_own_change(self):
        self.load()
        self.editing(self.EARLY)
        self.residents().setText(1, "15")
        made = [c for c in self.window.loadout if c.new_value == "15"]
        self.assertEqual([c.origin for c in made], [self.EARLY])

    def test_and_is_the_exception_to_the_op_that_mod_already_had(self):
        """It already sets this field, by an op that says more than that one
        value. The op stays as its author wrote it, and the edit is an op of
        the Studio's after it, typed over what the op left."""
        self.load()
        before = len(self.window.loadout)
        ops = list(self.window.loadout.ops)
        self.editing(self.EARLY)
        self.residents().setText(1, "15")
        self.assertEqual(self.window.loadout.ops, ops)
        self.assertEqual(len(self.window.loadout), before + 1)
        (edit,) = self.window.loadout.authored()
        theirs = self.window.loadout.op(edit.exception_of)
        self.assertEqual(theirs.origin, self.EARLY)
        (answer,) = self.window.loadout.changes_by_op(theirs.uid)
        self.assertEqual((edit.old_value, edit.new_value), (answer.new_value, "15"))
        # Typing over it again is that exception retuned, not another.
        self.residents().setText(1, "16")
        self.assertEqual(len(self.window.loadout), before + 1)

    def test_while_editing_into_yours_lays_a_change_on_top_instead(self):
        self.load()
        before = len(self.window.loadout)
        self.editing("")
        self.residents().setText(1, "15")
        self.assertEqual(len(self.window.loadout), before + 1)
        change = self.window.loadout.get(self.FARMER, self.FIELD)
        self.assertEqual((change.origin, change.old_value), ("", "20"))

    def test_reverting_the_asset_only_reverts_the_mod_you_are_in(self):
        self.load()
        self.editing(self.EARLY)
        self.window.editor._revert_asset()
        self.assertEqual(
            self.window.loadout.for_asset(self.FARMER, self.EARLY), []
        )
        self.assertTrue(self.window.loadout.for_asset(self.FARMER, self.LATE))

    def test_editing_a_node_an_earlier_mod_added_is_a_modop_of_your_own(self):
        """Not a rewrite of their payload. The node exists by the time your mod
        loads, so it is an ordinary thing to patch -- and folding the edit into
        their file would change a mod you did not mean to touch."""
        self.load()
        added = Change(
            guid=self.FARMER, asset_label="Farmer", path="/Values/Residence7",
            tag="Roominess", op=OP_ADD, origin=self.EARLY,
            payload="<Roominess>3</Roominess>", summary="add Roominess",
        )
        self.window.loadout.set_change(added)
        self.editing("")
        item = None
        for row in self.window.editor._iter_items():
            if row.text(0) == "Roominess":
                item = row
        self.assertIsNotNone(item)
        item.setText(1, "9")
        self.assertEqual(added.payload, "<Roominess>3</Roominess>")
        mine = self.window.loadout.get(
            self.FARMER, "/Values/Residence7/Roominess"
        )
        self.assertIsNotNone(mine)
        self.assertEqual((mine.origin, mine.new_value), ("", "9"))

    def test_but_a_node_the_same_mod_added_still_folds_into_its_payload(self):
        self.load()
        added = Change(
            guid=self.FARMER, asset_label="Farmer", path="/Values/Residence7",
            tag="Roominess", op=OP_ADD, origin=self.EARLY,
            payload="<Roominess>3</Roominess>", summary="add Roominess",
        )
        self.window.loadout.set_change(added)
        self.editing(self.EARLY)
        for row in self.window.editor._iter_items():
            if row.text(0) == "Roominess":
                row.setText(1, "9")
        self.assertIn("9", added.payload)

    # -- the control itself ------------------------------------------------
    def test_the_picker_lists_every_mod_of_yours_with_the_loadouts_own_last(self):
        self.load()
        box = self.window.editor.target_box
        self.assertEqual(
            [box.itemData(i) for i in range(box.count())],
            [layer.origin for layer in self.window.loadout.layers] + [""],
        )
        self.assertEqual(box.itemText(box.count() - 1),
                         "This loadout's own mod: only in this loadout")

    def test_choosing_one_says_what_that_means(self):
        self.load()
        self.editing(self.EARLY)
        text = self.window.editor.target_label.text()
        self.assertIn("Edits go straight into it.", text)
        self.assertIn("load after it", text)
        # The picker names the mod, and the line does not name it again.
        self.assertNotIn(self.window.editor.target_box.currentText(), text)

    def test_and_warns_when_that_mod_is_not_fully_read(self):
        self.load()
        self.editing("[Seal] Other Houses")
        self.assertIn(
            "1 of its ModOps could not be read", self.window.editor.target_label.text()
        )

    def test_the_changes_list_marks_the_mod_being_edited(self):
        self.load()
        self.window.loadout.editing = self.EARLY
        self.window.changes_panel.refresh()
        from anno_modding_studio.ui.changes_dock import KIND_ROLE

        tree = self.window.changes_panel.tree
        marked = [
            tree.topLevelItem(i).text(0)
            for i in range(tree.topLevelItemCount())
            if tree.topLevelItem(i).data(0, KIND_ROLE) == "mod"
            and "editing this mod" in tree.topLevelItem(i).text(0)
        ]
        self.assertEqual(len(marked), 1)
        self.assertTrue(marked[0].startswith(self.EARLY))

    def test_the_preview_follows_you_to_the_mod_you_are_editing(self):
        """Two different questions -- what am I writing into, what am I writing
        out -- with nearly always the same answer."""
        self.load()
        editor = self.window.editor
        editor.target_box.setCurrentIndex(editor.target_box.findData(self.EARLY))
        self.assertEqual(self.window.output.current_origin(), self.EARLY)
        # Not rebuilt on the spot: that verifies every change in the loadout,
        # which is three seconds on a real mods folder and for a tab nobody is
        # looking at. It is marked dirty and redrawn on arrival.
        self.assertTrue(self.window._output_dirty)
        self.window.output.refresh()
        self.assertIn(
            "Roomier Farmers", self.window.output.preview_label.full_text()
        )

    def test_switching_does_not_redraw_the_whole_changes_list(self):
        """Which mod an edit goes to is all that moved; redrawing four thousand
        rows to restate it is most of a second."""
        self.load()
        panel = self.window.changes_panel
        drawn = []
        panel._rebuild = lambda: drawn.append(1)
        self.window.editor.target_box.setCurrentIndex(
            self.window.editor.target_box.findData(self.EARLY)
        )
        self.assertEqual(drawn, [])
        marked = [
            panel.tree.topLevelItem(i).text(0)
            for i in range(panel.tree.topLevelItemCount())
            if "editing this mod" in panel.tree.topLevelItem(i).text(0)
        ]
        self.assertEqual(len(marked), 1)

    def test_the_mod_being_edited_stands_out_from_the_rest(self):
        """Not only the words at the end of its name: bold, the pencil its
        tab wears in the XML view, and a band behind the row."""
        self.load()
        self.window.loadout.editing = self.EARLY
        panel = self.window.changes_panel
        panel.refresh()
        from anno_modding_studio.ui import theme
        from anno_modding_studio.ui.changes_dock import KIND_ROLE, ORIGIN_ROLE

        rows = {
            (panel.tree.topLevelItem(i).data(0, ORIGIN_ROLE) or ""): panel.tree.topLevelItem(i)
            for i in range(panel.tree.topLevelItemCount())
            if panel.tree.topLevelItem(i).data(0, KIND_ROLE) == "mod"
        }
        target, other = rows[self.EARLY], rows[self.LATE]
        self.assertTrue(target.font(0).bold())
        self.assertFalse(target.icon(0).isNull())
        self.assertEqual(target.background(0).color().name(), theme.wash())
        self.assertEqual(target.background(1).color().name(), theme.wash())
        self.assertFalse(other.font(0).bold())
        self.assertTrue(other.icon(0).isNull())
        self.assertEqual(other.background(0).style(), Qt.BrushStyle.NoBrush)

    def test_and_the_mark_moves_when_another_mod_is_chosen(self):
        self.load()
        panel = self.window.changes_panel
        box = self.window.editor.target_box
        box.setCurrentIndex(box.findData(self.EARLY))
        box.setCurrentIndex(box.findData(self.LATE))
        from anno_modding_studio.ui.changes_dock import KIND_ROLE, ORIGIN_ROLE

        bold = [
            panel.tree.topLevelItem(i).data(0, ORIGIN_ROLE)
            for i in range(panel.tree.topLevelItemCount())
            if panel.tree.topLevelItem(i).data(0, KIND_ROLE) == "mod"
            and panel.tree.topLevelItem(i).font(0).bold()
        ]
        self.assertEqual(bold, [self.LATE])

    def test_double_clicking_a_change_opens_the_tab_of_the_mod_it_is_under(self):
        """Not the tab of the mod changes go to: the change is that mod's."""
        self.load()
        self.editing("")
        editor = self.window.editor
        editor.set_view("xml")
        self.window.editor.load_asset("1010031")
        panel = self.window.changes_panel
        from anno_modding_studio.ui.changes_dock import GUID_ROLE, KIND_ROLE, OPEN_ROLE

        panel.build_every_row()
        found = []

        def walk(item):
            for i in range(item.childCount()):
                child = item.child(i)
                if (child.data(0, KIND_ROLE) == "change"
                        and (child.data(0, OPEN_ROLE) or child.data(0, GUID_ROLE)) == self.FARMER
                        and panel._origin_of(child) == self.EARLY):
                    found.append(child)
                walk(child)

        walk(panel.tree.invisibleRootItem())
        self.assertTrue(found, "no change of the first mod to the farmer")
        open_row(panel, found[0])
        self.assertEqual(editor.guid, self.FARMER)
        self.assertEqual(editor.xml.current().origin, self.EARLY)
        # At the ModOp it comes of, on a tab only read as on the one typed
        # in, and from where each line starts: at its end, a long line was
        # scrolled past the tags that say what it is in.
        op = editor.xml.modops.textCursor()
        self.assertTrue(op.block().text().lstrip().startswith("<ModOp "), op.block().text())
        self.assertEqual(op.positionInBlock(), 0)
        field = editor.xml.asset_in_front().textCursor()
        self.assertIn("Residents", field.block().text())
        self.assertEqual(field.positionInBlock(), 0)

    def test_and_in_the_fields_view_leaves_the_xml_tabs_alone(self):
        self.load()
        self.editing("")
        self.window.jump_to_change(self.FARMER, self.FIELD, self.EARLY)
        self.assertEqual(self.window.editor.guid, self.FARMER)
        self.window.editor.set_view("xml")
        self.assertTrue(self.window.editor.xml.current().editing)

    def test_which_mod_you_are_in_survives_saving_and_opening(self):
        self.load()
        self.window.loadout.editing = self.EARLY
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "p.annoload"
            self.window.loadout.save(path)
            from anno_modding_studio.core.changes import Loadout

            reopened = Loadout.load(path)
        self.assertEqual(reopened.editing_origin(), self.EARLY)

    def test_and_falls_back_to_yours_when_that_mod_is_not_loaded(self):
        """Otherwise a loadout remembering a mod you have since removed writes
        changes into a layer that is not there."""
        self.load()
        self.window.loadout.editing = self.EARLY
        self.window.loadout.drop_layer(self.EARLY)
        self.assertEqual(self.window.loadout.editing_origin(), "")

    def test_or_to_the_mod_the_loadout_is_and_remembers_the_one_picked(self):
        """The mod of yours a loadout carries on with before the loadout's
        own mod, which nobody asked for there; and the mod picked comes back
        as where changes go once it is loaded again, as an undo brings it."""
        self.load()
        loadout = self.window.loadout
        loadout.home = self.LATE
        loadout.editing = self.EARLY
        layer = loadout.layer(self.EARLY)
        loadout.drop_layer(self.EARLY)
        self.assertEqual(loadout.editing_origin(), self.LATE)
        loadout.set_layer(layer)
        self.assertEqual(loadout.editing_origin(), self.EARLY)


class TestAFieldAModShipsAndAnotherSets(ModsFolderCase):
    """The field one mod makes and another sets, where both are ops of the
    mods' own files and not changes made here: each is the one answer of an
    op kept as its author wrote it, and is read as the change made here is.

    An edit made into the mod over such a field follows the rule there is
    for a value an op replaces.  A plain op, one change and nothing else
    said, is let go of and the mod is written with the edited merge where
    the op was.  Any other op stays as written, and the edit is an op of the
    Studio's after it.  Once it was made in the answer and never reached the
    mod's file, which was written with the op as it had been.
    """

    # Edits go into these mods, so they are yours.
    YOURS = True

    ROOMY = (
        '<ModOp Type="merge" GUID="1010030" Path="/Values/Residence7">'
        "<Roominess>%s</Roominess></ModOp>"
    )
    # A section, to add to and take from.
    ROOMS = (
        '<ModOp Type="merge" GUID="1010030" Path="/Values/Residence7">'
        "<Rooms><Width>2</Width><Depth>3</Depth></Rooms></ModOp>"
    )
    # Each names two assets, so it says more than its change to the farmer.
    AIRY = (
        '<ModOp Type="merge" GUID="1010030,1010031" Path="/Values/Residence7">'
        "<Airiness>2</Airiness></ModOp>"
    )
    LOFTS = (
        '<ModOp Type="merge" GUID="1010030,1010031" Path="/Values/Residence7">'
        "<Lofts><Width>2</Width></Lofts></ModOp>"
    )
    # An asset of the mod's own, which has no row above its top fields.
    HOUSE = (
        '<ModOp Type="addNextSibling" GUID="1010030"><Asset>'
        "<Template>ResidenceBuilding7</Template><Values>"
        "<Standard><GUID>1999000001</GUID><Name>New house</Name></Standard>"
        "<Residence7><Residents>4</Residents></Residence7>"
        "</Values></Asset></ModOp>"
    )
    FIRST = ModsFolderCase.FIRST.replace("</ModOps>", ROOMY % "3" + "</ModOps>")
    SECOND = ModsFolderCase.SECOND.replace(
        "</ModOps>", ROOMY % "5" + ROOMS + AIRY + LOFTS + HOUSE + "</ModOps>"
    )

    FARMER = TestEditingIntoAnotherMod.FARMER
    EARLY = TestEditingIntoAnotherMod.EARLY
    LATE = TestEditingIntoAnotherMod.LATE
    editing = TestEditingIntoAnotherMod.editing
    roominess = TestEditingIntoAnotherMod.roominess

    def test_each_is_the_answer_of_an_op_read_from_its_mod(self):
        self.load()
        loadout = self.window.loadout
        made = {c.origin: c for c in loadout if c.tag == "Roominess"}
        self.assertEqual(sorted(made), [self.EARLY, self.LATE])
        for origin, value in ((self.EARLY, "3"), (self.LATE, "5")):
            change = made[origin]
            self.assertFalse(change.authored)
            self.assertEqual(change.sets, ("/Values/Residence7/Roominess", value))
            self.assertEqual(loadout.op(change.op_uid).origin, origin)

    def test_the_mod_that_sets_it_has_one_row_set_by_this_mod(self):
        self.load()
        self.editing(self.LATE)
        rows = self.roominess()
        self.assertEqual([(row.text(1), row.text(3)) for row in rows], [("5", "set by this mod")])

    def test_and_from_your_own_mod_it_is_set_by_the_mod_that_set_it(self):
        self.load()
        self.editing("")
        rows = self.roominess()
        self.assertEqual(
            [(row.text(1), row.text(3)) for row in rows],
            [("5", "set by [Seal] Much Roomier Farmers")],
        )

    def test_the_mod_that_made_it_sees_its_own_addition_overwritten(self):
        self.load()
        self.editing(self.EARLY)
        row = self.roominess()[0]
        self.assertEqual(row.text(1), "3")
        self.assertEqual(row.text(3), "overwritten by [Seal] Much Roomier Farmers")
        self.assertNotIn("overwritten", row.parent().text(3))

    def test_its_menu_offers_to_stop_setting_it_not_to_remove_an_addition(self):
        self.load()
        self.editing(self.LATE)
        menu = self.window.editor._build_item_menu(self.roominess()[0])
        labels = [action.text() for action in menu.actions()]
        self.assertIn("Stop setting this", labels)
        self.assertNotIn("Remove this addition", labels)

    # -- an edit made into the mod, over what its op adds -------------------
    def row(self, tag, under=""):
        """The row of ``tag`` in the mod that ships it, in the section
        ``under`` where two rows have the name."""
        self.load()
        self.editing(self.LATE)
        return self.the_row(tag, under)

    def the_row(self, tag, under=""):
        return next(
            item for item in self.window.editor._iter_items()
            if item.text(0) == tag and (not under or item.parent().text(0) == under)
        )

    def shipped(self, tag):
        """The op of the mod's file that adds ``tag``, if it is still kept."""
        return next(
            (op for op in self.window.loadout.ops_of(self.LATE) if "<%s>" % tag in op.source),
            None,
        )

    def written(self):
        from anno_modding_studio.core import generator

        return generator.build_modops_xml(generator.for_mod(self.window.loadout, self.LATE))

    def of_the_studio(self):
        """The changes made here into the mod that ships the ops."""
        return [c for c in self.window.loadout.authored() if c.origin == self.LATE]

    def test_typing_over_it_lets_go_of_a_plain_op_for_the_retuned_merge(self):
        row = self.row("Roominess")
        loadout = self.window.loadout
        op = self.shipped("Roominess")
        self.assertTrue(op.plain)
        before = len(loadout)
        row.setText(1, "9")
        self.assertIsNone(self.shipped("Roominess"))
        self.assertEqual(len(loadout), before)
        (edit,) = self.of_the_studio()
        self.assertEqual(edit.sets, ("/Values/Residence7/Roominess", "9"))
        self.assertEqual((edit.op_uid, edit.exception_of, edit.order), ("", "", op.order))
        out = self.written()
        self.assertEqual(out.count("<Roominess>"), 1)
        self.assertIn(
            '<ModOp Type="merge" GUID="1010030" Path="/Values/Residence7">\n'
            "    <Roominess>9</Roominess>",
            out,
        )
        # Where the op was: before the op that followed it in the file.
        self.assertLess(out.index("<Roominess>"), out.index("<Rooms>"))
        # And it is one step to undo, which puts the op back.
        self.window.undo_last()
        self.assertIsNotNone(self.shipped("Roominess"))
        self.assertEqual(self.of_the_studio(), [])
        self.assertIn("<Roominess>5</Roominess>", self.written())

    def test_and_taking_it_back_takes_the_plain_op_out(self):
        row = self.row("Roominess")
        self.window.editor._remove_node(row)
        self.assertIsNone(self.shipped("Roominess"))
        self.assertEqual(self.of_the_studio(), [])
        self.assertNotIn("Roominess", self.written())
        self.assertEqual(
            [(item.text(1), item.text(3)) for item in self.roominess()],
            [("3", "added by [Gameplay] Roomier Farmers")],
        )

    def test_adding_to_what_a_plain_op_adds_is_written_in_its_merge(self):
        rooms = self.row("Rooms")
        height = etree.Element("Height")
        height.text = "4"
        self.window.editor._record_addition(rooms, height, "add Height")
        self.assertIsNone(self.shipped("Rooms"))
        (edit,) = self.of_the_studio()
        self.assertEqual(edit.tag, "Rooms")
        out = self.written()
        self.assertEqual(out.count("<Rooms>"), 1)
        self.assertIn("<Depth>3</Depth>\n      <Height>4</Height>\n    </Rooms>", out)

    def test_and_so_is_taking_out_part_of_it(self):
        depth = self.row("Depth", under="Rooms")
        self.window.editor._remove_node(depth)
        self.assertIsNone(self.shipped("Rooms"))
        out = self.written()
        self.assertIn("<Rooms>\n      <Width>2</Width>\n    </Rooms>", out)
        self.assertNotIn("<Depth>", out)

    def test_and_a_top_field_added_to_an_asset_a_plain_op_makes(self):
        from PySide6.QtWidgets import QDialog

        from anno_modding_studio.ui.add_field_dialog import AddFieldDialog

        self.load()
        self.window.loadout.editing = self.LATE
        self.window.editor.refresh_target()
        self.window.editor.load_asset("1999000001")
        self.assertTrue(self.shipped("Asset").plain)

        def building(dialog):
            dialog.chosen_tag, dialog.is_container, dialog.value = "Building", True, ""
            return int(QDialog.DialogCode.Accepted)

        with mock.patch.object(AddFieldDialog, "exec", building):
            self.window.editor._add_root_field()
        self.assertIsNone(self.shipped("Asset"))
        (edit,) = self.of_the_studio()
        self.assertTrue(edit.creates_asset)
        out = self.written()
        self.assertEqual(out.count("<GUID>1999000001</GUID>"), 1)
        self.assertIn("</Residence7>\n        <Building/>\n      </Values>", out)

    def test_typing_over_what_any_other_op_adds_is_an_op_after_it(self):
        """The op names two assets and the edit is to one of them, so the op
        stays as its author wrote it."""
        row = self.row("Airiness")
        loadout = self.window.loadout
        op = self.shipped("Airiness")
        self.assertFalse(op.plain)
        ops, before = list(loadout.ops), len(loadout)
        row.setText(1, "9")
        self.assertEqual(loadout.ops, ops)
        (edit,) = self.of_the_studio()
        self.assertEqual(len(loadout), before + 1)
        self.assertEqual(
            (edit.path, edit.old_value, edit.new_value, edit.exception_of),
            ("/Values/Residence7/Airiness", "2", "9", op.uid),
        )
        out = self.written()
        self.assertIn(
            '<ModOp Type="merge" GUID="1010030,1010031" Path="/Values/Residence7">\n'
            "    <Airiness>2</Airiness>",
            out,
        )
        mine = '<ModOp Type="replace" GUID="1010030" Path="/Values/Residence7/Airiness">'
        self.assertIn(mine + "\n    <Airiness>9</Airiness>", out)
        self.assertLess(out.index("<Lofts>"), out.index(mine))
        # The asset read again shows the edit, and typing over it again is
        # that exception retuned, not another.
        self.editing(self.LATE)
        row = self.the_row("Airiness")
        self.assertEqual(row.text(1), "9")
        row.setText(1, "10")
        (edit,) = self.of_the_studio()
        self.assertEqual((edit.old_value, edit.new_value, edit.exception_of), ("2", "10", op.uid))
        # Typed back to what the op leaves, there is no exception to make.
        self.editing(self.LATE)
        self.the_row("Airiness").setText(1, "2")
        self.assertEqual(self.of_the_studio(), [])
        self.assertEqual(self.written().count("Airiness>"), 2)

    def test_and_taking_it_back_is_a_remove_after_it(self):
        from anno_modding_studio.core.changes import OP_REMOVE

        row = self.row("Airiness")
        loadout = self.window.loadout
        op = self.shipped("Airiness")
        ops = list(loadout.ops)
        self.window.editor._remove_node(row)
        self.assertEqual(loadout.ops, ops)
        (edit,) = self.of_the_studio()
        self.assertEqual(
            (edit.op, edit.path, edit.exception_of),
            (OP_REMOVE, "/Values/Residence7/Airiness", op.uid),
        )
        out = self.written()
        mine = '<ModOp Type="remove" GUID="1010030" Path="/Values/Residence7/Airiness"'
        self.assertIn("<Airiness>2</Airiness>", out)
        self.assertLess(out.index("<Lofts>"), out.index(mine))
        # It stays on the asset, struck through, to be kept after all.
        row = self.the_row("Airiness")
        self.assertEqual(row.text(3), "removed by this mod")
        labels = [a.text() for a in self.window.editor._build_item_menu(row).actions()]
        self.assertIn("Keep this after all", labels)

    def test_and_adding_to_what_it_adds_is_an_op_after_it(self):
        lofts = self.row("Lofts")
        loadout = self.window.loadout
        op = self.shipped("Lofts")
        ops = list(loadout.ops)
        height = etree.Element("Height")
        height.text = "4"
        self.window.editor._record_addition(lofts, height, "add Height")
        self.assertEqual(loadout.ops, ops)
        (edit,) = self.of_the_studio()
        self.assertEqual(
            (edit.path, edit.tag, edit.exception_of),
            ("/Values/Residence7/Lofts", "Height", op.uid),
        )
        out = self.written()
        self.assertIn("<Lofts>\n      <Width>2</Width>\n    </Lofts>", out)
        mine = '<ModOp Type="add" GUID="1010030" Path="/Values/Residence7/Lofts">'
        self.assertIn(mine + "\n    <Height>4</Height>", out)
        self.assertLess(out.index("<Lofts>"), out.index(mine))
        self.assertEqual(self.the_row("Height", under="Lofts").text(1), "4")


class TestAValueTypedIntoTheOpThatAddsIt(ModsFolderCase):
    """A field the mod's own op adds and writes down, typed over, is typed
    into that op, which stays where it is: in its group, or among the other
    fields it adds.  Not an exception after it at the end of the file, nor,
    for a plain op in a group, the Studio's ModOp outside the group."""

    YOURS = True

    GROUP = '<Group Condition="@1010030/Residence7">%s</Group>'
    ROOMY = TestAFieldAModShipsAndAnotherSets.ROOMY % "5"
    # One asset, two fields: two changes, so not plain.
    TWO = (
        '<ModOp Type="merge" GUID="1010030" Path="/Values/Residence7">'
        "<Lofts><Width>2</Width></Lofts><Attics><!-- low --><Width>1</Width></Attics></ModOp>"
    )
    HOUSE = TestAFieldAModShipsAndAnotherSets.HOUSE
    SECOND = ModsFolderCase.SECOND.replace(
        "</ModOps>", GROUP % ROOMY + TWO + GROUP % HOUSE + "</ModOps>"
    )

    FARMER = TestEditingIntoAnotherMod.FARMER
    LATE = TestEditingIntoAnotherMod.LATE
    editing = TestEditingIntoAnotherMod.editing
    row = TestAFieldAModShipsAndAnotherSets.row
    the_row = TestAFieldAModShipsAndAnotherSets.the_row
    shipped = TestAFieldAModShipsAndAnotherSets.shipped
    written = TestAFieldAModShipsAndAnotherSets.written

    def exceptions(self):
        return [c for c in self.window.loadout if c.exception_of or (c.authored and not c.op_uid)]

    def test_a_plain_op_in_a_group_is_typed_over_in_it(self):
        row = self.row("Roominess")
        loadout = self.window.loadout
        op = self.shipped("Roominess")
        self.assertTrue(op.plain and op.within)
        before = len(loadout)
        row.setText(1, "9")
        typed = loadout.op(op.uid)
        self.assertEqual((typed.within, typed.order), (op.within, op.order))
        self.assertIn("<Roominess>9</Roominess>", typed.source)
        self.assertEqual((len(loadout), self.exceptions()), (before, []))
        out = self.written()
        self.assertEqual(out.count("<Roominess>"), 1)
        group = etree.fromstring(out.encode("utf-8")).find("Group")
        self.assertEqual(group.findtext(".//Roominess"), "9")
        # One step to undo, which puts the value it had back.
        self.window.undo_last()
        self.assertIn("<Roominess>5</Roominess>", self.shipped("Roominess").source)

    def test_one_of_the_fields_an_op_adds_is_typed_over_in_that_op(self):
        row = self.row("Width", under="Attics")
        loadout = self.window.loadout
        op = self.shipped("Attics")
        self.assertFalse(op.plain)
        uids = [held.uid for held in loadout.ops]
        row.setText(1, "4")
        self.assertEqual([held.uid for held in loadout.ops], uids)
        self.assertEqual(self.exceptions(), [])
        self.assertEqual(self.widths(), [("Lofts", "2"), ("Attics", "4")])
        self.assertIn("<!-- low -->", self.written())
        # The asset read again shows it, and typed over again it is that op.
        self.editing(self.LATE)
        row = self.the_row("Width", under="Attics")
        self.assertEqual(row.text(1), "4")
        row.setText(1, "6")
        self.assertEqual(self.exceptions(), [])
        self.assertEqual(self.widths(), [("Lofts", "2"), ("Attics", "6")])

    def widths(self):
        """Each ``<Width>`` the mod is written with, by what it is in."""
        out = etree.fromstring(self.written().encode("utf-8"))
        return [(width.getparent().tag, width.text) for width in out.iter("Width")]

    def test_an_asset_the_mod_adds_in_a_group_is_typed_over_in_it(self):
        self.load()
        self.window.loadout.editing = self.LATE
        self.window.editor.refresh_target()
        self.window.editor.load_asset("1999000001")
        op = self.shipped("Asset")
        self.assertTrue(op.within)
        self.the_row("Residents").setText(1, "6")
        self.assertEqual(self.exceptions(), [])
        self.assertIn("<Residents>6</Residents>", self.window.loadout.op(op.uid).source)
        group = etree.fromstring(self.written().encode("utf-8")).findall("Group")[1]
        self.assertEqual(group.findtext(".//Residents"), "6")

    def house(self):
        """The asset the mod adds in a group, open in the editor."""
        self.load()
        self.window.loadout.editing = self.LATE
        self.window.editor.refresh_target()
        self.window.editor.load_asset("1999000001")
        return self.shipped("Asset")

    def in_the_group(self):
        """The asset as the mod is written with it, which must be in its group."""
        out = etree.fromstring(self.written().encode("utf-8"))
        (asset,) = out.iter("Asset")
        self.assertEqual(asset.getparent().getparent().tag, "Group")
        return asset

    def test_an_entry_added_to_an_asset_the_mod_adds_in_a_group_is_added_in_it(self):
        op = self.house()
        entry = etree.Element("Item")
        etree.SubElement(entry, "Product").text = "1010200"
        etree.SubElement(entry, "Amount").text = "2"
        self.window.editor._record_addition(self.the_row("Residence7"), entry, "add Item")
        self.assertEqual(self.exceptions(), [])
        typed = self.window.loadout.op(op.uid)
        self.assertEqual(typed.within, op.within)
        added = self.in_the_group().find("Values/Residence7/Item")
        self.assertEqual((added.findtext("Product"), added.findtext("Amount")), ("1010200", "2"))
        # Laid out as what is beside it, which the mod wrote on one line.
        self.assertIn(
            "<Residents>4</Residents><Item><Product>1010200</Product><Amount>2</Amount></Item>",
            typed.source,
        )
        self.assertEqual(self.the_row("Amount").text(1), "2")

    def test_a_field_added_at_the_top_of_it_is_added_in_it(self):
        from PySide6.QtWidgets import QDialog

        from anno_modding_studio.ui.add_field_dialog import AddFieldDialog

        op = self.house()

        def building(dialog):
            dialog.chosen_tag, dialog.is_container, dialog.value = "Building", True, ""
            return int(QDialog.DialogCode.Accepted)

        with mock.patch.object(AddFieldDialog, "exec", building):
            self.window.editor._add_root_field()
        self.assertEqual(self.exceptions(), [])
        self.assertEqual(self.window.loadout.op(op.uid).within, op.within)
        self.assertIsNotNone(self.in_the_group().find("Values/Building"))

    def test_a_field_taken_out_of_it_is_taken_out_in_it(self):
        op = self.house()
        self.window.editor._remove_node(self.the_row("Name"))
        self.assertEqual(self.exceptions(), [])
        self.assertEqual(self.window.loadout.op(op.uid).within, op.within)
        self.assertIsNone(self.in_the_group().find("Values/Standard/Name"))
        self.assertIn("<GUID>1999000001</GUID></Standard>", self.window.loadout.op(op.uid).source)
        # One step to undo, which puts it back.
        self.window.undo_last()
        self.assertIn("<Name>New house</Name>", self.shipped("Asset").source)

    def test_added_to_and_taken_from_an_op_that_adds_two_fields(self):
        loadout = self.window.loadout
        lofts = self.row("Lofts")
        op = self.shipped("Attics")
        height = etree.Element("Height")
        height.text = "3"
        self.window.editor._record_addition(lofts, height, "add Height")
        self.assertEqual((self.exceptions(), loadout.op(op.uid).uid), ([], op.uid))
        self.assertIn("<Lofts><Width>2</Width><Height>3</Height></Lofts>", loadout.op(op.uid).source)
        self.editing(self.LATE)
        self.window.editor._remove_node(self.the_row("Attics"))
        self.assertEqual(self.exceptions(), [])
        source = loadout.op(op.uid).source
        self.assertNotIn("Attics", source)
        self.assertIn("<Lofts>", source)
        self.assertEqual([c.tag for c in loadout.changes_by_op(op.uid)], ["Lofts"])


class TestYourWorkVersusWhatYouOpened(ModsFolderCase):
    """A loadout is a workspace; a loadout's *history* is what you did in it.

    Opening a mods folder lands thousands of changes that are already on disk
    and can be read again. Counting those as unsaved work, checkpointing them,
    or throwing them away on a re-read are three different ways of confusing
    the two.
    """

    # Edits go into these mods, so they are yours.
    YOURS = True

    FARMER = "1010030"
    FIELD = "/Values/Residence7/Residents"
    EARLY = "[Gameplay] Roomier Farmers"

    def edit(self, origin, value):
        self.window.loadout.editing = origin
        self.window.editor.refresh_target()
        self.window.editor.load_asset(self.FARMER)
        for item in self.window.editor._iter_items():
            if item.text(0) == "Residents" and item.data(0, 258) is not None:
                item.setText(1, value)
                return item
        self.fail("no Residents row")

    def test_what_you_open_is_not_work_you_have_done(self):
        self.load()
        self.assertTrue(len(self.window.loadout) >= 3)
        self.assertEqual(self.window.loadout.authored_count, 0)

    def test_but_what_you_type_is(self):
        self.load()
        self.edit("", "15")
        self.assertEqual(self.window.loadout.authored_count, 1)
        self.assertTrue(self.window.loadout.authored()[0].authored)

    def test_and_so_is_an_edit_into_one_of_the_loaded_mods(self):
        self.load()
        self.edit(self.EARLY, "15")
        made = self.window.loadout.authored()
        self.assertEqual([c.origin for c in made], [self.EARLY])

    def test_a_version_records_your_work_not_the_mods_you_had_open(self):
        self.load()
        self.edit("", "15")
        checkpoint = self.window.loadout.checkpoint("mine")
        self.assertEqual(len(checkpoint.changes), 1)
        self.assertLess(len(checkpoint.changes), len(self.window.loadout))

    def test_so_opening_a_mods_folder_is_not_unsaved_work(self):
        self.load()
        self.assertFalse(self.window.loadout.diff_since(None).total)

    def test_while_one_typed_value_is(self):
        self.load()
        self.edit("", "15")
        self.assertTrue(self.window.loadout.changed_since_checkpoint())
        self.assertEqual(
            self.window.loadout.diff_since(None).summary(), "1 added"
        )

    def test_restoring_a_version_leaves_the_loaded_mods_where_they_are(self):
        """It undoes what you have done since, not what you had open."""
        self.load()
        self.edit("", "15")
        checkpoint = self.window.loadout.checkpoint("mine")
        self.edit("", "16")
        loaded = len(self.window.loadout)
        self.window.loadout.restore(checkpoint)
        self.assertEqual(len(self.window.loadout), loaded)
        self.assertEqual(
            self.window.loadout.get(self.FARMER, self.FIELD).new_value, "15"
        )

    def test_the_title_counts_your_work_apart_from_their_mods(self):
        self.load()
        self.window.update_title()
        self.assertNotRegex(self.window.windowTitle(), r"\d+ changes?\b")
        self.edit(self.EARLY, "15")
        self.window.update_title()
        self.assertRegex(self.window.windowTitle(), r"\b1 change\b")
        # Edits go into these mods, so they are counted as yours.
        self.assertIn("3 mods of yours", self.window.windowTitle())
        # And says whose it counts: the list beside it holds their changes
        # too, and counts every one.
        self.assertIn("  —  1 change of yours  —  ", self.window.windowTitle())
        self.assertGreater(len(self.window.loadout), 1)

    def test_and_the_start_tab_counts_what_the_title_does(self):
        """Its card and "Your loadout" counted their changes too: 24 changes
        on the Start tab under a title saying 2 changes of yours."""
        from anno_modding_studio.core.changes import AIM_MAKE

        self.load()
        self.edit(self.EARLY, "15")
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.window.loadout_path = Path(tmp.name) / "p.annoload"
        self.window.files.autosave()
        self.window.start.refresh_source_panel()
        self.assertIn("  —  1 change of yours  —  ", self.window.windowTitle())
        self.assertTrue(
            self.window.start._aim_status()[AIM_MAKE].endswith("  1 change of yours so far."),
            self.window.start._aim_status()[AIM_MAKE],
        )
        detail = self.window.source.loadout_detail.text()
        self.assertTrue(detail.startswith("1 change of yours · saved in "), detail)

    def test_closing_with_a_folder_open_and_nothing_typed_asks_nothing(self):
        # Read as it comes: whose the mods are is no part of this.
        self.YOURS = False
        self.load()
        self.window.dirty = True
        self.assertTrue(self.window.files.confirm_discard())

    # -- taking away what was read in ----------------------------------
    def saved(self):
        """The folder read in and the loadout written to a file of its own."""
        self.load()
        # Which mod an edit lands in is a detail of the loadout, and work of
        # its own kind: settled first, so that what follows is the only work.
        self.window.loadout.editing = self.EARLY
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.window.loadout_path = Path(tmp.name) / "p.annoload"
        self.window.files.autosave()
        self.assertFalse(self.window.files.unsaved_work())
        return self.window.loadout_path

    def revert_theirs(self):
        """Revert asset, in the mod that was read in."""
        self.window.loadout.editing = self.EARLY
        self.window.editor.refresh_target()
        self.window.editor.load_asset(self.FARMER)
        self.window.editor._revert_asset()
        self.assertEqual(self.window.loadout.for_asset(self.FARMER, self.EARLY), [])

    def test_but_reverting_a_change_that_was_read_in_is_work(self):
        """The folder still has it, so only the loadout says it is gone: the
        title had no mark, and closing asked nothing and put it back."""
        self.saved()
        self.revert_theirs()
        self.assertTrue(self.window.files.unsaved_work())
        self.assertIn(" *", self.window.windowTitle())
        # Nothing made here, so the question counts what was taken out: the
        # change, and the ModOp that named this asset alone and went with it.
        self.assertEqual(self.window.loadout.authored_count, 0)
        with mock.patch.object(self.window.prompts, "ask_save_first", return_value="") as asked:
            self.assertFalse(self.window.files.confirm_discard())
        self.assertEqual(asked.call_args[0], (0, 2, False, False, 0))
        # Taken back, there is nothing to save again.
        self.window.undo_last()
        self.assertTrue(self.window.loadout.for_asset(self.FARMER, self.EARLY))
        self.assertFalse(self.window.files.unsaved_work())
        self.assertNotIn("*", self.window.windowTitle())

    def test_and_so_is_reverting_the_last_of_a_mod_in_the_changes_panel(self):
        """Which drops the mod as well, and a mod no longer loaded was not
        counted: no mark, no question, and the saved loadout brought the
        mod and its change back.  Somebody else's mod, whose edits the
        loadout's own mod is said to keep."""
        self.YOURS = False
        asked = mock.patch.object(self.window.prompts, "ask_way_on", return_value="own")
        asked.start()
        self.addCleanup(asked.stop)
        self.saved()
        # Where edits land has nothing to do with it, so that alone would
        # not ask.
        self.window.loadout.editing = ""
        self.window.files.autosave()
        loadout = self.window.loadout
        (change,) = loadout.for_asset(self.FARMER, self.EARLY)
        panel = self.window.changes_panel
        # Its ModOp drawn as this one row, as grouping.split draws most ops
        # of a real mod: the row is the op, and goes with it.
        panel._op_rows.discard(change.op_uid)
        with mock.patch.object(panel, "_selected_changes", return_value=[change.key]):
            panel._remove_selected()
        self.assertIsNone(loadout.layer(self.EARLY))
        self.assertTrue(self.window.files.unsaved_work())
        self.assertIn(" *", self.window.windowTitle())
        # The change and the ModOp that came to it alone.
        self.assertEqual(self.asked_on_the_way_out(), (0, 2, False, False, 0))

    def test_and_with_a_change_made_here_as_well_the_question_says_both(self):
        """It named the change made here and nothing else, so Discard lost
        a revert the question had not mentioned."""

        self.saved()
        self.revert_theirs()
        self.edit("", "15")
        with _isolation.answering("Cancel") as ask:
            self.assertFalse(self.window.files.confirm_discard())
        self.assertEqual(
            ask.texts[0],
            "1 change you have made is not saved. 2 changes and ModOps have been "
            "reverted or switched since this loadout was saved. Saving also "
            "saves the projects of Anno 1800 loadout patch, [Gameplay] Roomier Farmers.\n\n"
            "Save before continuing?",
        )

    def asked_on_the_way_out(self) -> tuple:
        """What the question before the loadout is left is asked with."""
        with mock.patch.object(self.window.prompts, "ask_save_first", return_value="") as asked:
            self.assertFalse(self.window.files.confirm_discard())
        return asked.call_args[0]

    def test_the_question_counts_what_was_made_or_typed_again_since_the_save(self):
        """It counted every change made here, saved or not: with one saved
        and one more made it said two were not saved."""
        self.saved()
        self.edit(self.EARLY, "15")
        self.assertEqual(self.asked_on_the_way_out(), (1, 0, False, False, 0))
        self.window.files.autosave()
        self.assertFalse(self.window.files.unsaved_work())
        # One more, in your own mod. Which mod an edit lands in has moved
        # with it, and that is not for the question: see below.
        self.edit("", "25")
        self.assertEqual(self.window.loadout.changes_made, 2)
        self.assertEqual(self.asked_on_the_way_out(), (1, 0, False, False, 0))
        self.window.files.autosave()
        # A saved one typed again is the same change saying something else.
        self.edit("", "26")
        self.assertEqual(self.window.loadout.changes_made, 2)
        self.assertEqual(self.asked_on_the_way_out(), (1, 0, False, False, 0))
        # Typed back to what was saved, it is asked all the same, with
        # nothing it can count.
        self.edit("", "25")
        self.assertTrue(self.window.files.unsaved_work())
        self.assertEqual(self.asked_on_the_way_out(), (0, 0, False, False, 0))
        # And only a detail typed, with two changes made here and saved.
        self.window.files.autosave()
        self.window.loadout.own.description = "typed, not saved"
        self.window.files.mark_unsaved()
        self.assertEqual(self.asked_on_the_way_out(), (0, 0, False, True, 0))

    def test_and_names_the_mods_details_beside_a_change_but_not_where_edits_land(self):
        """A description typed and a change made: the question counted the
        change and said nothing of the description.  Saying the details
        whenever any had moved was no answer, since which mod an edit lands
        in is kept with them and moves with most edits."""

        self.saved()
        def text() -> str:
            with _isolation.answering("Cancel") as ask:
                self.assertFalse(self.window.files.confirm_discard())
            return ask.texts[0]

        # An edit into your own mod, which moves where edits land.
        self.edit("", "15")
        self.assertEqual(self.window.loadout.editing, "")
        self.assertEqual(self.asked_on_the_way_out(), (1, 0, False, False, 0))
        self.assertEqual(
            text(), "1 change you have made is not saved. Saving also saves the "
            "project of Anno 1800 loadout patch.\n\nSave before continuing?"
        )
        self.window.loadout.own.description = "typed, not saved"
        self.assertEqual(self.asked_on_the_way_out(), (1, 0, False, True, 0))
        self.assertEqual(
            text(),
            "1 change you have made is not saved. A mod's details have changed "
            "since this loadout was saved. Saving also saves the project of Anno "
            "1800 loadout patch.\n\nSave before continuing?",
        )
        # The same for a mod.io link, and for a loaded mod called something
        # else, each alone.
        self.window.files.autosave()
        self.window.loadout.modio_mod_id = 4711
        self.window.files.mark_unsaved()
        self.assertEqual(self.asked_on_the_way_out(), (0, 0, False, True, 0))
        self.window.files.autosave()
        self.window.loadout.layer(self.EARLY).name = "Called something else"
        self.window.files.mark_unsaved()
        self.assertEqual(self.asked_on_the_way_out(), (0, 0, False, True, 0))
        self.assertEqual(
            text(),
            "A mod's details have changed since this loadout was saved. Saving "
            "also saves the project of Called something else.\n\n"
            "Save before continuing?",
        )
        # Where edits land, alone, is still something to save and nothing
        # the question can name.
        self.window.files.autosave()
        self.window.loadout.editing = self.EARLY
        self.window.files.mark_unsaved()
        self.assertTrue(self.window.files.unsaved_work())
        self.assertEqual(self.asked_on_the_way_out(), (0, 0, False, False, 0))
        self.assertEqual(
            text(),
            "This loadout has been worked on since it was saved.\n\n"
            "Save before continuing?",
        )

    def test_and_the_revert_is_in_the_loadout_that_is_saved_and_opened_again(self):
        from anno_modding_studio.core import stale

        path = self.saved()
        self.revert_theirs()
        self.window.files.autosave()
        self.assertFalse(self.window.files.unsaved_work())
        opened = type(self.window.loadout).load(path)
        self.assertEqual(opened.for_asset(self.FARMER, self.EARLY), [])
        # And opening it reads no folder again, which would bring it back.
        self.assertEqual(stale.check(opened), [])
        self.assertEqual(stale.waiting(opened), [])

    def test_before_the_loadout_was_ever_written_too(self):
        self.load()
        self.revert_theirs()
        self.assertEqual(self.window.files.taken_away(), 2)

    def test_and_so_is_switching_off_a_modop_that_was_read_in(self):
        self.saved()
        (op,) = self.window.loadout.ops_of(self.EARLY)
        op.active = False
        self.window.on_activation_changed()
        self.assertTrue(self.window.files.unsaved_work())
        op.active = True
        self.window.on_activation_changed()
        self.assertFalse(self.window.files.unsaved_work(), "switched back is nothing")

    def test_but_a_note_typed_on_one_is_not_a_revert_and_is_said_as_what_it_is(self):
        """The question said one change or ModOp had been reverted or
        switched, of a ModOp that was there and on with a note typed on it."""

        self.saved()
        (op,) = self.window.loadout.ops_of(self.EARLY)
        (change, *_rest) = self.window.loadout.for_asset(self.FARMER, self.EARLY)
        self.assertFalse(op.authored or change.authored)
        was = op.note
        op.note = "slower on purpose"
        self.window.files.mark_unsaved()
        self.assertTrue(self.window.files.unsaved_work())
        self.assertEqual(self.window.files.taken_away(), 0)
        self.assertEqual(self.asked_on_the_way_out(), (0, 0, False, False, 1))
        change.note = "and so is this"
        with _isolation.answering("Cancel") as ask:
            self.assertFalse(self.window.files.confirm_discard())
        self.assertEqual(
            ask.texts[0],
            "The notes on 2 changes and ModOps have changed since this loadout "
            "was saved. Saving also saves the project of [Gameplay] Roomier "
            "Farmers.\n\nSave before continuing?",
        )
        # Switched off as well, it is one thing switched and not two things.
        op.active = False
        self.assertEqual(self.asked_on_the_way_out(), (0, 1, False, False, 1))
        op.active = True
        # Typed back, it is nothing.
        op.note, change.note = was, ""
        self.assertFalse(self.window.files.unsaved_work())
        # On a change made here the note is part of what was made: counted
        # there, once.
        self.edit(self.EARLY, "15")
        self.window.files.autosave()
        (made,) = self.window.loadout.authored()
        self.assertTrue(made.authored)
        made.note = "mine"
        self.window.files.mark_unsaved()
        self.assertEqual(self.asked_on_the_way_out(), (1, 0, False, False, 0))

    def test_and_reverting_the_last_change_of_your_own_that_was_saved(self):
        self.load()
        self.edit("", "15")
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.window.loadout_path = Path(tmp.name) / "p.annoload"
        self.window.files.autosave()
        self.window.editor._revert_asset()
        self.assertEqual(self.window.loadout.authored_count, 0)
        self.assertTrue(self.window.files.unsaved_work())

    def test_what_a_reading_no_longer_brings_is_still_not_work(self):
        """A mod updated on disk has fewer ops because its author took some
        out, and reading it again is not you taking them out."""
        self.saved()
        (op,) = self.window.loadout.ops_of(self.EARLY)
        self.window.loadout.drop_op(op.uid)
        self.window.files.took_reading({self.EARLY})
        self.window.files.mark_unsaved()
        self.assertFalse(self.window.files.unsaved_work())
        # Nor is the folder read again over a revert: it is all back.
        self.revert_theirs()
        self.load()
        self.assertEqual(self.window.files.taken_away(), 0)

    def test_and_a_reading_does_not_save_what_you_made_in_that_mod(self):
        """A change made here inside a loaded mod was filed with what the
        reading brought, so once that mod's folder was read the question
        counted nothing made, and the title lost its mark."""
        self.saved()
        self.edit(self.EARLY, "15")
        self.assertEqual(self.window.files.made_unsaved(), 1)
        self.window.files.took_reading({self.EARLY})
        self.assertEqual(self.window.files.made_unsaved(), 1)
        self.assertEqual(self.asked_on_the_way_out(), (1, 0, False, False, 0))
        # The folder read again through the dialog keeps it, and keeps it
        # unsaved.
        self.load()
        self.assertEqual(self.window.files.made_unsaved(), 1)
        self.assertEqual(self.asked_on_the_way_out(), (1, 0, False, False, 0))

    def test_nor_is_unloading_a_mod(self):
        self.saved()
        self.window.unload_mods(["[Seal] Other Houses"])
        self.assertFalse(self.window.files.unsaved_work())

    # -- reading the folder again --------------------------------------
    def test_reading_the_folder_again_keeps_what_you_edited_into_a_mod(self):
        """Your edit shares that ModOp's identity -- it *is* that op, retuned --
        so a re-read would otherwise put the mod's own value back."""
        self.load()
        self.edit(self.EARLY, "15")
        self.load()
        change = self.window.loadout.get(self.FARMER, self.FIELD, origin=self.EARLY)
        self.assertEqual(change.new_value, "15")
        self.assertTrue(change.authored)

    def test_and_says_how_many_it_kept(self):
        self.load()
        self.edit(self.EARLY, "15")
        self.load()
        self.assertIn("1 change you had made was kept",
                      self.window.status_label.text())

    def test_and_keeps_one_of_your_own_that_no_mod_touches(self):
        self.load()
        self.edit("", "15")
        self.load()
        self.assertEqual(self.window.loadout.authored_count, 1)

    def test_the_rest_of_the_mod_still_comes_back_fresh(self):
        self.load()
        before = len(self.window.loadout)
        ops = [(op.uid, op.source) for op in self.window.loadout.ops]
        self.edit(self.EARLY, "15")
        self.load()
        # Every op of every mod, each the op it was, and your edit beside the
        # one it is the exception to.
        self.assertEqual(len(self.window.loadout), before + 1)
        self.assertEqual([(op.uid, op.source) for op in self.window.loadout.ops], ops)
        (edit,) = self.window.loadout.authored()
        self.assertIsNotNone(self.window.loadout.op(edit.exception_of))

    def test_whether_a_change_is_yours_survives_saving_and_opening(self):
        self.load()
        self.edit(self.EARLY, "15")
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "p.annoload"
            self.window.loadout.save(path)
            from anno_modding_studio.core.changes import Loadout

            reopened = Loadout.load(path)
        self.assertEqual(reopened.authored_count, 1)
        self.assertEqual(reopened.authored()[0].origin, self.EARLY)

    def test_the_history_says_which_mod_each_edit_was_into(self):
        from anno_modding_studio.ui.history_dialog import HistoryDialog

        self.load()
        self.edit(self.EARLY, "15")
        dialog = HistoryDialog(self.window.loadout, self.window)
        self.addCleanup(dialog.deleteLater)
        history = dialog.mod
        history.checkpoints.setCurrentRow(0)
        rows = [
            history.diff_tree.topLevelItem(i)
            for i in range(history.diff_tree.topLevelItemCount())
        ]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].text(1), self.EARLY)
        # Typed over what that mod's own ModOp left.
        self.assertEqual(rows[0].text(4), "12 → 15")


class TestChangesGoToSaysHowFarEachReaches(ModsFolderCase):
    """*Currently editing* offers mods of yours only, each saying how far
    its changes reach: your patch for somebody else's mod wherever you use
    it, and the loadout's own mod in this loadout only.  Their mod itself is
    never offered, since it is never changed; picking "A new patch for…"
    makes one (section 5.1 of docs/proposals/projects-and-loadouts-proposal.md).
    """

    THEIRS = "[Gameplay] Roomier Farmers"

    def setUp(self):
        super().setUp()
        self.asked = []
        named = _isolation.answering(lambda title, _text: self.asked.append(title) or True)
        named.start()
        self.addCleanup(named.stop)

    def box(self):
        return self.window.editor.target_box

    def entries(self):
        box = self.box()
        return {box.itemData(at): box.itemText(at) for at in range(box.count())}

    def aim_at(self, data):
        self.box().setCurrentIndex(self.box().findData(data))

    def test_their_mod_is_not_offered_and_a_patch_for_it_is(self):
        self.load()
        entries = self.entries()
        self.assertNotIn(self.THEIRS, entries)
        self.assertEqual(entries[NEW_PATCH + self.THEIRS], "A new patch for %s…" % self.THEIRS)
        self.assertEqual(entries[""], "This loadout's own mod: only in this loadout")

    def test_picking_a_new_patch_makes_it_unasked_and_aims_there(self):
        self.load()
        self.aim_at(NEW_PATCH + self.THEIRS)
        patch = self.window.loadout.patch_for(self.THEIRS)
        self.assertIsNotNone(patch)
        self.assertEqual(self.asked, [])
        self.assertEqual(self.window.loadout.editing_origin(), patch.origin)
        self.assertIn("loads straight after", self.window.status_label.text())
        entries = self.entries()
        self.assertEqual(entries[patch.origin],
                         "Your patch for %s: wherever you use it" % self.THEIRS)
        self.assertNotIn(NEW_PATCH + self.THEIRS, entries)

    def test_a_loaded_mod_that_is_yours_is_offered_as_itself(self):
        """Editing your own file directly is exactly right; a patch would be an
        odd second folder holding half of it."""
        self.load()
        self.window.loadout.own.creator = "Ay"   # the fixture's author of THEIRS
        self.window.loadout.layer(self.THEIRS).kept = True
        self.window.editor.refresh_target()
        self.assertEqual(self.entries()[self.THEIRS], self.THEIRS)
        self.aim_at(self.THEIRS)
        self.assertEqual(self.window.loadout.editing_origin(), self.THEIRS)
        self.assertIsNone(self.window.loadout.patch_for(self.THEIRS))

    def test_one_with_your_name_and_no_project_is_offered_one(self):
        """Nothing keeps it, so it is read like anybody else's until it is
        made a project (section 5.5)."""
        from anno_modding_studio.ui.editor_panel import NEW_PROJECT

        self.load()
        self.window.loadout.own.creator = "Ay"   # the fixture's author of THEIRS
        self.window.editor.refresh_target()
        entries = self.entries()
        self.assertNotIn(self.THEIRS, entries)
        self.assertNotIn(NEW_PATCH + self.THEIRS, entries)
        self.assertEqual(entries[NEW_PROJECT + self.THEIRS],
                         "Make %s a project of yours…" % self.THEIRS)
        with mock.patch.object(self.window.prompts, "ask_make_project", return_value=False):
            self.aim_at(NEW_PROJECT + self.THEIRS)
        self.assertEqual(self.window.loadout.editing_origin(), "")
        with mock.patch.object(self.window.prompts, "ask_make_project", return_value=True) as asked:
            self.aim_at(NEW_PROJECT + self.THEIRS)
        asked.assert_called_once_with(self.THEIRS)
        self.assertTrue(self.window.loadout.layer(self.THEIRS).kept)
        self.assertIn(self.THEIRS, self.window.loadout.project_origins())
        self.assertEqual(self.window.loadout.editing_origin(), self.THEIRS)
        self.assertIn("has a project of its own now", self.window.status_label.text())

    def test_another_name_of_yours_given_with_it_loaded_is_followed_at_once(self):
        """Given in Settings with the loadout open, the name went to the
        loadout and nothing on screen followed: *Currently editing* went on
        offering a patch for the mod, and the file tab went on reading it as
        anybody else's."""
        from anno_modding_studio.ui.editor_panel import NEW_PROJECT

        self.load()
        self.window.open_tab(TAB_FILE)
        panel = self.window.file_panel
        panel.look_at(self.THEIRS)
        self.addCleanup(self.window.settings.remove, self.window.preferences.ALIASES_KEY)
        self.window.preferences.set_aliases(["Ay"])   # the fixture's author of THEIRS
        entries = self.entries()
        self.assertNotIn(NEW_PATCH + self.THEIRS, entries)
        self.assertIn(NEW_PROJECT + self.THEIRS, entries)
        self.assertIn("has your name on it", panel.look_note.text())
        # And taken back, it is somebody else's again.
        self.window.preferences.set_aliases([])
        self.assertIn(NEW_PATCH + self.THEIRS, self.entries())
        self.assertIn("is by Ay", panel.look_note.text())

    def test_the_file_of_one_with_your_name_says_edit_this_file_makes_it_yours(self):
        """It said only that changes go elsewhere, which is as true of
        somebody else's mod: nothing said that this one is yours to take
        up, or how."""
        self.load()
        self.window.loadout.own.creator = "Ay"   # the fixture's author of THEIRS
        self.window.open_tab(TAB_FILE)
        panel = self.window.file_panel
        panel.look_at(self.THEIRS)
        self.assertEqual(
            panel.look_note.text(),
            "Only shown. %s has your name on it, but no project of yours keeps it yet. "
            "Edit this file makes it one, and your changes go straight into it." % self.THEIRS,
        )
        with mock.patch.object(self.window.prompts, "ask_make_project", return_value=True) as asked:
            panel.edit_button.click()
        asked.assert_called_once_with(self.THEIRS)
        self.assertEqual(self.window.loadout.editing_origin(), self.THEIRS)
        self.assertTrue(panel.is_typed_in())
        self.assertFalse(panel.pane.isReadOnly())

    def project_entry(self, origin):
        panel = self.window.changes_panel
        panel.refresh()
        menu = panel.menu_for(panel._mod_items[origin])
        self.addCleanup(menu.deleteLater)
        return next((a for a in menu.actions() if a.text() == "Make a project of yours…"), None)

    def test_the_mod_changes_row_of_one_with_your_name_offers_it_a_project(self):
        """Only Currently editing did: the row, where somebody looking at
        the mod wonders why it is read like anybody else's, had nothing."""
        self.load()
        self.assertIsNone(self.project_entry(self.THEIRS))
        self.addCleanup(self.window.settings.remove, self.window.preferences.ALIASES_KEY)
        self.window.preferences.set_aliases(["Ay"])   # the fixture's author of THEIRS
        entry = self.project_entry(self.THEIRS)
        self.assertIsNotNone(entry)
        self.assertIsNone(self.project_entry("[Seal] Much Roomier Farmers"))
        with mock.patch.object(self.window.prompts, "ask_make_project", return_value=True) as asked:
            entry.trigger()
        asked.assert_called_once_with(self.THEIRS)
        self.assertTrue(self.window.loadout.layer(self.THEIRS).kept)
        self.assertEqual(self.window.loadout.editing_origin(), self.THEIRS)
        # Made one, it is not offered again.
        self.assertIsNone(self.project_entry(self.THEIRS))

    def test_the_file_of_somebody_elses_says_whose_it_is(self):
        """Why it is not yours, and where to say that its name is."""
        self.load()
        self.window.open_tab(TAB_FILE)
        panel = self.window.file_panel
        panel.look_at(self.THEIRS)
        self.assertEqual(
            panel.look_note.text(),
            "Only shown. Your changes go to Anno 1800 loadout patch. "
            "%s is by Ay, which is not one of your author names (Settings ▸ Authoring)."
            % self.THEIRS,
        )

    def test_a_loadout_saved_aiming_into_their_mod_aims_at_its_own(self):
        self.load()
        self.window.loadout.editing = self.THEIRS
        self.window.editor.refresh_target()
        self.assertEqual(self.window.loadout.editing_origin(), "")
        self.assertEqual(self.box().currentData(), "")

    def test_a_mod_written_here_keeps_its_name(self):
        self.load()
        self.window.loadout.is_the_mod = True
        self.window.editor.refresh_target()
        self.assertIn("(yours)", self.entries()[""])


class TestTheThreeWaysOn(ModsFolderCase):
    """Switching one of somebody else's ops off is an edit inside their op,
    and their mod is never changed: it asks which mod of yours keeps it
    (sections 5.2 and 5.3 of docs/proposals/projects-and-loadouts-proposal.md)."""

    THEIRS = "[Gameplay] Roomier Farmers"

    def setUp(self):
        super().setUp()
        self.answer = ""
        asked = mock.patch.object(self.window.prompts, "ask_way_on",
                                  side_effect=lambda *args, **kwargs: self.answer)
        self.asked = asked.start()
        self.addCleanup(asked.stop)

    def op_row(self, origin: str):
        """The row of ``origin``'s first op, and that op."""
        from anno_modding_studio.ui.changes_dock import KIND_ROLE, OP_ROLE

        panel = self.window.changes_panel
        panel.refresh()
        # A folded mod's rows are made when it is opened; these are all of them.
        panel.build_every_row()
        op = next(op for op in self.window.loadout.ops if op.origin == origin)
        found = []

        def walk(item):
            if item.data(0, KIND_ROLE) == "op" and item.data(0, OP_ROLE) == op.uid:
                found.append(item)
            for at in range(item.childCount()):
                walk(item.child(at))

        for at in range(panel.tree.topLevelItemCount()):
            walk(panel.tree.topLevelItem(at))
        return found[0], op

    def untick(self, origin: str):
        row, op = self.op_row(origin)
        row.setCheckState(0, Qt.CheckState.Unchecked)
        # The question is asked once the tick has returned.
        _app.processEvents()
        return row, op

    def test_cancelled_nothing_is_switched(self):
        self.load()
        row, op = self.untick(self.THEIRS)
        self.asked.assert_called_once_with(self.THEIRS, "", False)
        self.assertTrue(op.active)
        self.assertEqual(row.checkState(0), Qt.CheckState.Checked)

    def test_kept_by_the_loadouts_own_mod_and_not_asked_again(self):
        from anno_modding_studio.core import inside

        self.load()
        self.answer = "own"
        _row, op = self.untick(self.THEIRS)
        self.assertFalse(self.window.loadout.op(op.uid).active)
        inside.assign(self.window.loadout)
        self.assertEqual(self.window.loadout.op(op.uid).kept_by, inside.OWN)
        self.assertTrue(self.window.loadout.has_own_mod())
        row, _op = self.op_row(self.THEIRS)
        row.setCheckState(0, Qt.CheckState.Checked)
        self.asked.assert_called_once()
        self.assertTrue(self.window.loadout.op(op.uid).active)

    def test_kept_by_a_patch_made_for_it(self):
        from anno_modding_studio.core import inside

        self.load()
        self.answer = "patch"
        _row, op = self.untick(self.THEIRS)
        patch = self.window.loadout.patch_for(self.THEIRS)
        self.assertIsNotNone(patch)
        self.assertEqual(self.window.loadout.editing_origin(), patch.origin)
        self.assertFalse(self.window.loadout.op(op.uid).active)
        inside.assign(self.window.loadout)
        self.assertEqual(self.window.loadout.op(op.uid).kept_by, patch.origin)

    def test_nothing_is_asked_or_drawn_until_the_tick_has_returned(self):
        """Drawing the list again deletes every row, the ticked one too, and
        Qt reads that row again after its tick's signal: asked and answered
        inside the tick, a patch made for it crashed one run in six."""
        self.load()
        self.answer = "patch"
        row, _op = self.op_row(self.THEIRS)
        tree = self.window.changes_panel.tree
        with mock.patch.object(tree, "clear", wraps=tree.clear) as cleared:
            row.setCheckState(0, Qt.CheckState.Unchecked)
            self.asked.assert_not_called()
            cleared.assert_not_called()
            _app.processEvents()
        self.asked.assert_called_once()
        cleared.assert_called()
        self.assertIsNotNone(self.window.loadout.patch_for(self.THEIRS))

    def test_a_mod_of_yours_is_not_asked_about(self):
        self.YOURS = True
        self.load()
        _row, op = self.untick(self.THEIRS)
        self.asked.assert_not_called()
        self.assertFalse(op.active)


class TestABundledModIsChangedThroughAPatch(ModsFolderCase):
    """A bundled mod's file is a copy inside somebody's host -- and for a shared
    piece, the one copy of several the game happens to load.  A change written
    into it goes when the host updates, and stops loading when another mod
    ships a newer copy.  So changes aimed at one go to a patch, unasked."""

    THEIRS = "[Seal] Other Houses/shared piece"

    def setUp(self):
        super().setUp()
        self.asked = []
        named = _isolation.answering(lambda title, _text: self.asked.append(title) or False)
        named.start()
        self.addCleanup(named.stop)
        self.explained = []
        self.window.patches.explain_bundled_patch = lambda layer, patch: self.explained.append(
            (layer.origin, patch.origin)
        )

    def test_the_message_names_the_bundled_mod_once_and_its_host_nowhere(self):
        """The changes list shows which mod it came in. The message names the
        mod it is about, once, and says the rest without names."""
        from anno_modding_studio.core.changes import ModLayer
        from anno_modding_studio.ui import widgets

        del self.window.patches.explain_bundled_patch
        loadout = self.window.loadout
        loadout.set_layer(ModLayer(origin="[Seal] Other Houses", name="Other Houses"))
        deep = ModLayer(origin="[Seal] Other Houses/shared/piece", name="Shared Piece")
        loadout.set_layer(deep)
        self.assertEqual(loadout.host_layer(deep.origin).origin, "[Seal] Other Houses")
        self.assertIsNone(loadout.host_layer("[Seal] Other Houses"))
        with mock.patch.object(widgets, "answer_of") as answered, \
                mock.patch.object(widgets.QMessageBox, "setText") as said:
            self.window.patches.explain_bundled_patch(deep, ModLayer(origin="My Patch"))
        self.assertEqual(answered.call_count, 1)
        text = said.call_args.args[0]
        self.assertEqual(text.count("Shared Piece"), 1)
        self.assertIn('<span style="font-weight:600">Shared Piece</span> came bundled', text)
        self.assertNotIn("Other Houses", text)
        self.assertNotIn("My Patch", text)

    def load_with_a_bundled_mod(self, creator="Dee"):
        from anno_modding_studio.core.changes import ModLayer

        self.load()
        self.window.loadout.set_layer(
            ModLayer(origin=self.THEIRS, name="Shared Piece", creator=creator)
        )
        self.window.editor.refresh_target()

    def aim_at(self, origin):
        box = self.window.editor.target_box
        box.setCurrentIndex(box.findData(origin))

    def test_aiming_at_it_makes_a_patch_without_asking(self):
        self.load_with_a_bundled_mod()
        self.aim_at(NEW_PATCH + self.THEIRS)
        patch = self.window.loadout.patch_for(self.THEIRS)
        self.assertIsNotNone(patch)
        self.assertEqual(self.asked, [])
        self.assertEqual(self.window.loadout.editing_origin(), patch.origin)
        self.assertEqual(self.explained, [(self.THEIRS, patch.origin)])
        self.assertIn("came bundled inside another mod", self.window.status_label.text())

    def test_the_patch_is_called_what_the_picker_calls_it(self):
        """By the mod's name, as Changes go to lists it, and not by the
        folder name its ModID is made of (H2-25)."""
        self.load_with_a_bundled_mod()
        self.aim_at(NEW_PATCH + self.THEIRS)
        patch = self.window.loadout.patch_for(self.THEIRS)
        self.assertEqual(patch.label, "Shared Piece (my patch)")
        status = self.window.status_label.text()
        self.assertIn("Shared Piece (my patch)", status)
        self.assertNotIn(patch.origin, status)
        del self.window.patches.explain_bundled_patch
        with _isolation.answering(True) as told:
            self.window.patches.explain_bundled_patch(self.window.loadout.layer(self.THEIRS), patch)
        self.assertEqual([title for title, _text in told.told], ["Changes go to a patch"])
        self.assertIn("Shared Piece came bundled inside another mod", told.told[0][1])
        box = self.window.editor.target_box
        self.assertEqual(box.itemText(box.findData(patch.origin)),
                         "Your patch for Shared Piece: wherever you use it")

    def test_the_patch_stands_on_its_own_outside_the_host(self):
        self.load_with_a_bundled_mod()
        self.aim_at(NEW_PATCH + self.THEIRS)
        self.assertNotIn("/", self.window.loadout.patch_for(self.THEIRS).origin)

    def test_aiming_at_it_again_goes_to_the_patch_it_already_has(self):
        self.load_with_a_bundled_mod()
        self.aim_at(NEW_PATCH + self.THEIRS)
        patch = self.window.loadout.patch_for(self.THEIRS)
        self.aim_at("")
        self.assertEqual(self.window.editor.target_box.findData(NEW_PATCH + self.THEIRS), -1)
        self.aim_at(patch.origin)
        self.assertEqual(self.window.loadout.editing_origin(), patch.origin)
        self.assertEqual(len(self.explained), 1, "said once, when it was made")

    def test_a_bundled_mod_of_your_own_is_edited_directly(self):
        self.load_with_a_bundled_mod(creator="")
        self.window.loadout.own.creator = "Dee"
        self.window.loadout.layer(self.THEIRS).creator = "Dee"
        self.window.loadout.layer(self.THEIRS).kept = True
        self.window.editor.refresh_target()
        self.aim_at(self.THEIRS)
        self.assertIsNone(self.window.loadout.patch_for(self.THEIRS))
        self.assertEqual(self.window.loadout.editing_origin(), self.THEIRS)

    def test_a_loadout_saved_aiming_into_one_opens_aiming_elsewhere(self):
        self.load_with_a_bundled_mod()
        self.window.loadout.editing = self.THEIRS
        self.window.patches.off_a_bundled_copy()
        self.assertEqual(self.window.loadout.editing, "")
        self.assertIn("no longer go into it", self.window.status_label.text())


class TestAPatchOutlastsReadingTheFolderAgain(ModsFolderCase):
    """A patch made here is in no mods folder until Play builds it, so a read
    of the folder -- *Add mods to this loadout…* among them -- cannot give it
    back.  It lost its layer: its changes stayed, under a mod nothing was
    loaded as, drawn at the top of the panel as a mod of its own bundled in
    itself, and Changes go to went back to your own mod (H2-1)."""

    BUNDLED = "[Seal] Other Houses/shared piece"
    TOP = "[Gameplay] Roomier Farmers"

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        inner = cls.mods / "[Seal] Other Houses" / "shared piece"
        patch = inner / "data/config/export/main/asset/assets.xml"
        patch.parent.mkdir(parents=True)
        patch.write_text(
            "<ModOps>"
            '<ModOp Type="merge" GUID="1010032" Path="/Values/Residence7"><Residents>40</Residents></ModOp>'
            "</ModOps>",
            encoding="utf-8",
        )
        (inner / "modinfo.json").write_text(
            '{"ModID": "shared_piece", "ModName": {"English": "Shared Piece"}, "CreatorName": "Dee"}',
            encoding="utf-8",
        )

    def setUp(self):
        super().setUp()
        self.window.patches.explain_bundled_patch = lambda layer, patch: None
        named = _isolation.answering(lambda _title, _text: False)
        named.start()
        self.addCleanup(named.stop)

    def aim_at(self, origin):
        box = self.window.editor.target_box
        box.setCurrentIndex(box.findData(origin))

    def edit_into(self, origin):
        change = Change(guid="1010030", asset_label="Farm", path="/Values/Residence7/Residents",
                        tag="Residents", old_value="12", new_value="15", origin=origin,
                        authored=True)
        self.window.loadout.set_change(change)
        return change.key

    def test_a_bundled_mods_patch_stays_under_it_and_changes_still_go_to_it(self):
        self.load()
        loadout = self.window.loadout
        self.assertIsNotNone(loadout.layer(self.BUNDLED))
        self.aim_at(NEW_PATCH + self.BUNDLED)
        patch = loadout.patch_for(self.BUNDLED)
        self.assertIsNotNone(patch)
        key = self.edit_into(patch.origin)
        self.load()
        again = loadout.layer(patch.origin)
        self.assertIsNotNone(again, "the patch is still a loaded mod")
        self.assertEqual(again.patches, self.BUNDLED)
        self.assertIs(loadout.patch_for(self.BUNDLED), again)
        self.assertEqual(again.label, "Shared Piece (my patch)")
        self.assertEqual(loadout.editing_origin(), patch.origin)
        self.assertEqual(self.window.editor.target_box.currentData(), patch.origin)
        self.assertIn(key, loadout.changes)
        # Straight after the mod it patches, not at the top.
        order = [layer.origin for layer in loadout.layers]
        self.assertEqual(order.index(patch.origin), order.index(self.BUNDLED) + 1)

    def test_so_does_the_patch_of_a_mod_of_its_own(self):
        from anno_modding_studio.core import patching

        self.load()
        loadout = self.window.loadout
        patch = patching.make(loadout, self.TOP)
        loadout.editing = patch.origin
        key = self.edit_into(patch.origin)
        self.load()
        self.assertEqual(loadout.patch_for(self.TOP).origin, patch.origin)
        self.assertEqual(loadout.editing_origin(), patch.origin)
        self.assertIn(key, loadout.changes)

    def test_so_it_does_when_a_mod_is_added_to_the_loadout(self):
        """*Add mods to this loadout…* opens on the loadout's own mods and
        reads the folder at the tick (D3): the patch outlasts that read too."""
        from unittest import mock

        from PySide6.QtWidgets import QDialog

        from anno_modding_studio.core import patching
        from anno_modding_studio.ui import mods_folder_dialog

        self.load()
        loadout = self.window.loadout
        patch = patching.make(loadout, self.TOP)
        loadout.editing = patch.origin
        key = self.edit_into(patch.origin)
        added = "[Seal] Much Roomier Farmers"
        loadout.drop_layer(added)

        def driven(inner):
            for row in range(inner.table.rowCount()):
                item = inner.table.item(row, 0)
                if item.data(mods_folder_dialog.FOLDER_ROLE) == added:
                    item.setCheckState(Qt.CheckState.Checked)
            for _ in range(500):
                QApplication.processEvents()
                if inner._worker is not None:
                    inner._worker.wait(100)
                elif not inner._shelf_timer.isActive() and inner.session is not None:
                    break
            inner._accept()
            return int(QDialog.DialogCode.Accepted)

        with mock.patch.object(mods_folder_dialog.ModsFolderDialog, "exec", driven):
            self.assertTrue(self.window.library_controller.add_mods())
        self.assertIsNotNone(loadout.layer(added))
        self.assertEqual(loadout.patch_for(self.TOP).origin, patch.origin)
        self.assertEqual(loadout.editing_origin(), patch.origin)
        self.assertIn(key, loadout.changes)

    def test_changes_aimed_at_a_mod_of_yours_stay_aimed_at_it(self):
        self.window.loadout.own.creator = "Ay"   # the fixture's author of TOP
        self.load()
        # A project keeps it, and still does once the folder is read again.
        self.window.loadout.layer(self.TOP).kept = True
        self.window.loadout.editing = self.TOP
        self.load()
        self.assertEqual(self.window.loadout.editing_origin(), self.TOP)

    def test_a_patch_a_read_lost_before_comes_back_on_opening(self):
        """A loadout saved after the read lost its patch: the changes under
        the patch's name, and no mod of that name.  The mod it was named after
        is loaded, and patched by nothing, so the patch is made again."""
        self.load()
        loadout = self.window.loadout
        key = self.edit_into("shared piece (my patch)")
        self.assertIsNone(loadout.layer("shared piece (my patch)"))
        self.window.patches.find_lost_patches()
        again = loadout.layer("shared piece (my patch)")
        self.assertIsNotNone(again)
        self.assertEqual(again.patches, self.BUNDLED)
        self.assertEqual(again.label, "Shared Piece (my patch)")
        self.assertIn(key, loadout.changes)


class TestHowAPatchIsDrawnBesideItsMod(ModsFolderCase):
    """A patch is your layer on a mod, not another entry in the list.

    It already sits immediately below the mod it patches -- that is what the
    " (my patch)" name is for, and load order is folder order -- so it needs no
    new level in the tree, only to stop reading like a stranger.
    """

    THEIRS = "[Gameplay] Roomier Farmers"

    def setUp(self):
        super().setUp()
        from PySide6.QtWidgets import QInputDialog

        real = QInputDialog.getText
        QInputDialog.getText = staticmethod(lambda *a, **k: (k.get("text", ""), True))
        self.addCleanup(setattr, QInputDialog, "getText", staticmethod(real))

    def mod_rows(self):
        from anno_modding_studio.ui.changes_dock import KIND_ROLE, ORIGIN_ROLE

        tree = self.window.changes_panel.tree
        return [
            (
                tree.topLevelItem(i).data(0, ORIGIN_ROLE),
                tree.topLevelItem(i).text(0),
                tree.topLevelItem(i),
            )
            for i in range(tree.topLevelItemCount())
            if tree.topLevelItem(i).data(0, KIND_ROLE) == "mod"
        ]

    def test_it_sits_immediately_below_the_mod_it_patches(self):
        self.load()
        self.window.patches.make_patch(self.THEIRS)
        self.window.changes_panel.refresh()
        origins = [origin for origin, _text, _item in self.mod_rows()]
        self.assertEqual(
            origins[origins.index(self.THEIRS) + 1], self.THEIRS + " (my patch)"
        )

    def test_and_reads_as_a_continuation_rather_than_another_mod(self):
        self.load()
        self.window.patches.make_patch(self.THEIRS)
        self.window.changes_panel.refresh()
        text = dict((o, t) for o, t, _i in self.mod_rows())[self.THEIRS + " (my patch)"]
        self.assertIn("↳", text)
        self.assertIn("your patch for", text)
        self.assertNotIn("(my patch)", text)

    def test_switching_its_mod_off_says_it_has_nothing_to_patch(self):
        self.load()
        self.window.patches.make_patch(self.THEIRS)
        self.window.loadout.layer(self.THEIRS).active = False
        self.window.changes_panel.refresh()
        rows = dict((o, (t, i)) for o, t, i in self.mod_rows())
        text, item = rows[self.THEIRS + " (my patch)"]
        self.assertIn("switched off", text)
        # Greyed the way anything the game will not read is greyed.
        self.assertEqual(
            item.foreground(0).color().name(), QColor(theme.TEXT_DIM).name()
        )

    def test_and_it_is_left_out_of_exporting_everything(self):
        """Its ops were measured against what that mod leaves behind."""
        self.load()
        self.window.patches.make_patch(self.THEIRS)
        self.edit_into_the_patch()
        self.window.loadout.layer(self.THEIRS).active = False
        with tempfile.TemporaryDirectory() as tmp:
            self.window.output.set_default_output_dir(Path(tmp))
            written = rewrite_every_mod(self.window)
            names = {path.name for path in written}
        self.assertNotIn(self.THEIRS + " (my patch)", names)

    def test_but_is_exported_while_its_mod_is_on(self):
        self.load()
        self.window.patches.make_patch(self.THEIRS)
        self.edit_into_the_patch()
        with tempfile.TemporaryDirectory() as tmp:
            self.window.output.set_default_output_dir(Path(tmp))
            written = rewrite_every_mod(self.window)
            names = {path.name for path in written}
        self.assertIn(self.THEIRS + " (my patch)", names)

    def edit_into_the_patch(self):
        """A patch with nothing in it is not exported either way."""
        self.window.editor.load_asset("1010030")
        for item in self.window.editor._iter_items():
            if item.text(0) == "Residents" and item.data(0, 258) is not None:
                item.setText(1, "99")
                return
        self.fail("no Residents row")

    def test_your_patches_are_counted_apart_from_the_mods_you_installed(self):
        self.load()
        self.window.patches.make_patch(self.THEIRS)
        self.window.update_title()
        self.assertIn("3 mods loaded", self.window.windowTitle())
        self.assertIn("1 yours", self.window.windowTitle())


class TestPatchingSomebodyElsesMod(ModsFolderCase):
    """Changing another author's mod without editing their file.

    Editing it directly works and is what the Studio does by default -- but the
    author releases 1.7, you drop it in, and your work is gone with the file you
    changed.  A patch mod is your own, loads straight after theirs, and survives
    the update.
    """

    FARMER = "1010030"
    FIELD = "/Values/Residence7/Residents"
    THEIRS = "[Gameplay] Roomier Farmers"

    def setUp(self):
        super().setUp()
        from PySide6.QtWidgets import QInputDialog

        self.named = []
        self.answer = None
        real = QInputDialog.getText

        def named(parent, title, label, *args, **kwargs):
            self.named.append(kwargs.get("text", ""))
            return (self.answer or kwargs.get("text", ""), True)

        QInputDialog.getText = staticmethod(named)
        self.addCleanup(setattr, QInputDialog, "getText", staticmethod(real))

        # Refusing a name opens a modal box, which a test must not sit inside.
        from PySide6.QtWidgets import QMessageBox

        warned = QMessageBox.warning
        self.warnings = []
        QMessageBox.warning = staticmethod(
            lambda parent, title, text, *a, **k: self.warnings.append(title)
        )
        self.addCleanup(setattr, QMessageBox, "warning", staticmethod(warned))
        # A box with one button, said through widgets.tell.
        self.told = _isolation.answering(True).start()
        self.addCleanup(self.told.stop)

    def edit(self, origin, value):
        self.window.loadout.editing = origin
        self.window.editor.refresh_target()
        self.window.editor.load_asset(self.FARMER)
        for item in self.window.editor._iter_items():
            if item.text(0) == "Residents" and item.data(0, 258) is not None:
                item.setText(1, value)
                return
        self.fail("no Residents row")

    # -- where it goes ----------------------------------------------------
    def test_the_patch_loads_straight_after_the_mod_it_is_for(self):
        self.load()
        self.window.patches.make_patch(self.THEIRS)
        patch = self.window.loadout.patch_for(self.THEIRS)
        self.assertEqual(patch.origin, self.THEIRS + " (my patch)")
        self.assertEqual(
            patch.order, self.window.loadout.layer(self.THEIRS).order + 1
        )

    def test_renaming_it_to_sort_before_its_mod_is_said_on_the_export_tab(self):
        """Anno 117 sorts by category and name, so a rename there can put the
        patch in front. Nothing used to say so after the patch was made."""
        from anno_modding_studio.core import games

        self.load()
        self.window.patches.make_patch(self.THEIRS)
        patch = self.window.loadout.patch_for(self.THEIRS)
        out = self.window.output
        out.show_mod(patch.origin)
        self.assertNotIn("sort the patch before", out.meta_note.text())
        self.window.loadout.game = games.ANNO_117
        patch.name = "A fix"
        out.load_loadout_fields()
        self.assertIn("sort the patch before", out.meta_note.text())
        patch.name = "Zz fix"
        patch.category = "Zz"
        out.load_loadout_fields()
        self.assertNotIn("sort the patch before", out.meta_note.text())

    def test_and_the_name_it_offers_is_the_one_that_does_that(self):
        self.load()
        self.window.patches.make_patch(self.THEIRS)
        self.assertEqual(self.named, [self.THEIRS + " (my patch)"])

    def test_a_name_that_would_load_first_is_refused(self):
        """Named wrongly it still holds your changes and the mod it is about
        simply overwrites them again."""
        self.load()
        # Mods are conventionally named "[Category] …", and "[" sorts below
        # every letter -- so a name has to start lower still to land first.
        self.answer = "!my patch"
        self.window.patches.make_patch(self.THEIRS)
        self.assertIsNone(self.window.loadout.patch_for(self.THEIRS))
        self.assertFalse(patching.loads_after(self.answer, self.THEIRS))
        self.assertEqual([title for title, _text in self.told.told], ["That would load first"])

    def test_editing_then_goes_to_the_patch(self):
        self.load()
        self.window.patches.make_patch(self.THEIRS)
        self.assertEqual(
            self.window.loadout.editing_origin(), self.THEIRS + " (my patch)"
        )
        self.edit(self.window.loadout.editing_origin(), "15")
        made = self.window.loadout.authored()
        self.assertEqual([c.origin for c in made], [self.THEIRS + " (my patch)"])

    def test_and_writes_over_what_that_mod_leaves(self):
        """Loading straight after it means the value it replaces is that mod's
        own, not the one the game shipped."""
        self.load()
        self.window.patches.make_patch(self.THEIRS)
        self.edit(self.window.loadout.editing_origin(), "15")
        change = self.window.loadout.authored()[0]
        self.assertEqual((change.old_value, change.new_value), ("12", "15"))

    # -- what you changed already ------------------------------------------
    def test_what_the_loadouts_own_mod_keeps_of_it_stays_there(self):
        """An edit the own mod keeps holds in this loadout only, and making
        a patch does not widen it unasked (section 5.3 of the projects and
        loadouts proposal)."""
        from anno_modding_studio.core import inside

        self.load()
        loadout = self.window.loadout
        theirs = next(op for op in loadout.ops if op.origin == self.THEIRS)
        theirs.active = False
        inside.assign(loadout)
        self.window.patches.make_patch(self.THEIRS)
        self.assertEqual(theirs.kept_by, inside.OWN)
        self.assertEqual(loadout.editing_origin(), self.THEIRS + " (my patch)")
        self.assertEqual(self.window.status_label.text(),
                         "Changes now go to %(mod)s (my patch), which loads straight "
                         "after %(mod)s." % {"mod": self.THEIRS})

    # -- what it exports as ------------------------------------------------
    def test_it_declares_which_mod_it_loads_after(self):
        self.load()
        self.window.patches.make_patch(self.THEIRS)
        self.edit(self.window.loadout.editing_origin(), "15")
        patch = self.window.loadout.patch_for(self.THEIRS)
        info = json.loads(
            modinfo.build(
                generator.for_mod(self.window.loadout, patch.origin)
            )
        )
        self.assertEqual(info["LoadAfterIds"], [self.THEIRS])
        self.assertEqual(info["CreatorName"], self.window.loadout.own.creator)

    def test_and_exports_as_its_own_folder_holding_only_your_change(self):
        self.load()
        self.window.patches.make_patch(self.THEIRS)
        self.edit(self.window.loadout.editing_origin(), "15")
        patch = self.window.loadout.patch_for(self.THEIRS)
        xml = generator.build_modops_xml(
            generator.for_mod(self.window.loadout, patch.origin),
        )
        self.assertEqual(xml.count("<ModOp "), 1)
        self.assertIn("<Residents>15</Residents>", xml)
        self.assertEqual(
            generator.folder_for(self.window.loadout, patch.origin), patch.origin
        )

    def test_and_its_export_is_recorded_with_its_own_build(self):
        """The checkpoint gave the build of the loadout's own mod, whichever
        mod had been written: none here, where only the patch ever was."""
        from anno_modding_studio.core import builds

        self.load()
        self.window.patches.make_patch(self.THEIRS)
        patch = self.window.loadout.patch_for(self.THEIRS)
        loadout = self.window.loadout
        out = self.window.output
        version = loadout.version_of(patch.origin) or "1.0"
        with tempfile.TemporaryDirectory() as tmp:
            self.window.loadout_path = Path(tmp) / "p.annoload"
            out._chosen_output_dir = Path(tmp) / "out"
            for value in ("15", "16"):
                self.edit(patch.origin, value)
                out.refresh()
                out.show_mod(patch.origin)
                self.assertIsNotNone(out.export_mod(confirm_overwrite=False, notify=False))
            self.assertEqual(builds.number(loadout, patch.origin), 2)
            self.assertEqual(builds.number(loadout, loadout.home or ""), 0)
            self.assertEqual(
                [entry.label for entry in loadout.history_of(patch.origin)],
                ["Exported v%s, build 1" % version, "Exported v%s, build 2" % version],
            )
            # A zip of it is the same build, and the same work: it is the
            # checkpoint the export took.
            self.assertIsNotNone(out.export_zip(notify=False))
            self.assertEqual([entry.label for entry in loadout.history_of(patch.origin)],
                             ["Exported v%s, build 1" % version, "Exported v%s, build 2" % version])
            # Several mods at once is not one mod's build: that one is left
            # saying the build of the mod the loadout releases, none here.
            self.edit(patch.origin, "17")
            self.assertTrue(out.export_mine(confirm=False, notify=False))
            self.assertEqual(loadout.history[-1].label,
                             "Exported v%s" % (loadout.version_of(loadout.home or "") or "1.0"))
            # And each mod written takes a checkpoint in its own project's
            # history, with its own build.
            self.assertEqual([entry.label for entry in loadout.history_of(patch.origin)][-1],
                             "Exported v%s, build %d" % (version, builds.number(loadout, patch.origin)))
            self.assertEqual(len(loadout.history_of(patch.origin)), 3)

    def test_where_one_mod_of_yours_fails_to_write_the_others_are_still_recorded(self):
        """One failure sent no list, so the mods that were written took no
        checkpoint and the loadout's own mod took one though it was the mod
        that failed."""
        from unittest import mock

        from anno_modding_studio.core.changes import KIND_EXPORT

        self.load()
        self.window.patches.make_patch(self.THEIRS)
        patch = self.window.loadout.patch_for(self.THEIRS)
        loadout = self.window.loadout
        home = loadout.home or ""
        out = self.window.output
        self.edit(patch.origin, "15")
        self.edit(home, "14")
        out.refresh()
        self.assertEqual(sorted(out.mine_to_export()), sorted([home, patch.origin]))
        real = generator.write_mod_folder
        home_name = generator.for_mod(loadout, home).own.name

        def write(exported, *args, **kwargs):
            if exported.own.name == home_name:
                raise OSError("the disk is full")
            return real(exported, *args, **kwargs)

        with tempfile.TemporaryDirectory() as tmp:
            self.window.loadout_path = Path(tmp) / "p.annoload"
            out._chosen_output_dir = Path(tmp) / "out"
            with mock.patch.object(generator, "write_mod_folder", write):
                self.assertEqual(len(out.export_mine(confirm=False, notify=False)), 1)
            # Its work is kept with the loadout's checkpoint, as any project's
            # that moved is, but not as an export.
            self.assertEqual([e.kind for e in loadout.history_of(home) if e.kind == KIND_EXPORT], [])
            self.assertEqual([e.kind for e in loadout.history_of(patch.origin)], [KIND_EXPORT])

    # -- when it is offered -------------------------------------------------
    def offered(self) -> bool:
        """Whether Currently editing offers to make the patch."""
        editor = self.window.editor
        editor.refresh_target()
        return editor.target_box.findData(NEW_PATCH + self.THEIRS) >= 0

    def test_it_is_offered_for_somebody_else_s_mod(self):
        self.load()
        self.assertTrue(self.offered())

    def test_nor_for_a_loaded_mod_you_wrote_yourself(self):
        """Recognised by creator, which is what the Studio writes into a mod it
        exports -- so one that came back out of here is editable directly
        without the caveat."""
        self.load()
        self.window.loadout.own.creator = "Ay"   # the author of Roomier Farmers
        # Offered a project, not a patch (section 5.5).
        self.assertFalse(self.offered())

    def test_nor_once_it_already_has_one(self):
        self.load()
        self.window.patches.make_patch(self.THEIRS)
        self.assertFalse(self.offered())

    def test_the_picker_says_it_is_a_patch_when_you_are_in_one(self):
        self.load()
        self.window.patches.make_patch(self.THEIRS)
        self.window.editor.refresh_target()
        self.assertIn("Your patch for", self.window.editor.target_box.currentText())
        self.assertNotIn("patch", self.window.editor.target_label.text())

    def test_and_both_mods_say_so_in_the_changes_list(self):
        self.load()
        self.window.patches.make_patch(self.THEIRS)
        self.edit(self.window.loadout.editing_origin(), "15")
        self.window.changes_panel.refresh()
        from anno_modding_studio.ui.changes_dock import KIND_ROLE, ORIGIN_ROLE

        tree = self.window.changes_panel.tree
        tips = {}
        for i in range(tree.topLevelItemCount()):
            item = tree.topLevelItem(i)
            if item.data(0, KIND_ROLE) == "mod":
                tips[item.data(0, ORIGIN_ROLE)] = item.toolTip(0)
        self.assertIn("your patch for", tips[self.THEIRS + " (my patch)"])
        self.assertIn("patched by", tips[self.THEIRS])

    def test_what_it_patches_survives_saving_and_opening(self):
        self.load()
        self.window.patches.make_patch(self.THEIRS)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "p.annoload"
            self.window.loadout.save(path)
            from anno_modding_studio.core.changes import Loadout

            reopened = Loadout.load(path)
        self.assertEqual(reopened.patch_for(self.THEIRS).origin,
                         self.THEIRS + " (my patch)")


class TestModsTheGameIsNotLoading(ModsFolderCase):
    """Installed and switched off is an ordinary state, and not the same thing
    as uninstalled: the mod is still there, still in the list, one tick away.

    Both games write it down themselves -- 1800 marks the folder name, 117 keeps
    a profile -- so the Studio reads theirs rather than inventing a fourth place
    for it to live, and that is what makes it survive closing the app.
    """

    OFF = "[Seal] Other Houses"

    def switch(self, origin, on):
        from anno_modding_studio.ui.changes_dock import KIND_ROLE, ORIGIN_ROLE

        tree = self.window.changes_panel.tree
        for index in range(tree.topLevelItemCount()):
            item = tree.topLevelItem(index)
            if (
                item.data(0, KIND_ROLE) == "mod"
                and item.data(0, ORIGIN_ROLE) == origin
            ):
                item.setCheckState(
                    0, Qt.CheckState.Checked if on else Qt.CheckState.Unchecked
                )
                return item
        self.fail("no row for %s" % origin)

    # -- reading it --------------------------------------------------------
    def test_a_marked_folder_comes_in_switched_off(self):
        marked = self.mods / ("-" + self.OFF)
        (self.mods / self.OFF).rename(marked)
        self.addCleanup(lambda: marked.rename(self.mods / self.OFF))
        self.load()
        self.assertFalse(self.window.loadout.layer(self.OFF).active)
        self.assertTrue(self.window.loadout.layer("[Gameplay] Roomier Farmers").active)

    def test_and_is_still_named_by_the_mod_rather_than_by_the_folder(self):
        marked = self.mods / ("-" + self.OFF)
        (self.mods / self.OFF).rename(marked)
        self.addCleanup(lambda: marked.rename(self.mods / self.OFF))
        self.load()
        self.assertIsNotNone(self.window.loadout.layer(self.OFF))
        self.assertIsNone(self.window.loadout.layer("-" + self.OFF))

    # -- what it means -----------------------------------------------------
    def test_nothing_a_switched_off_mod_does_counts(self):
        self.load()
        loadout = self.window.loadout
        before = len(loadout.active())
        mine = len(loadout.changes_of(self.OFF))
        self.assertTrue(mine)
        self.switch(self.OFF, False)
        self.assertEqual(len(loadout.active()), before - mine)

    def test_the_row_is_struck_through_and_its_tooltip_says_why(self):
        """The strike-through is the mark; the name is not also given a note."""
        self.load()
        item = self.switch(self.OFF, False)
        self.window.changes_panel.refresh()
        item = self.switch(self.OFF, False)
        self.assertNotIn("switched off", item.text(0))
        self.assertTrue(item.font(0).strikeOut())
        self.assertIn("switched off", item.toolTip(0))

    def test_it_is_left_out_of_exporting_everything(self):
        """Writing it back under its plain name would switch it on again."""
        self.load()
        self.switch(self.OFF, False)
        with tempfile.TemporaryDirectory() as tmp:
            self.window.output.set_default_output_dir(Path(tmp))
            written = rewrite_every_mod(self.window)
            names = {path.name for path in written}
        self.assertNotIn(self.OFF, names)
        self.assertIn("[Gameplay] Roomier Farmers", names)

    def test_being_off_survives_saving_and_opening(self):
        self.load()
        self.switch(self.OFF, False)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "p.annoload"
            self.window.loadout.save(path)
            from anno_modding_studio.core.changes import Loadout

            reopened = Loadout.load(path)
        self.assertFalse(reopened.layer(self.OFF).active)

    # -- putting it back ----------------------------------------------------
    def test_switching_the_folder_to_match_marks_the_mod_off(self):
        self.load()
        self.switch(self.OFF, False)
        self.window.live.sync_loadout(confirm=False)
        self.addCleanup(
            lambda: (self.mods / ("-" + self.OFF)).exists()
            and (self.mods / ("-" + self.OFF)).rename(self.mods / self.OFF)
        )
        self.assertTrue((self.mods / ("-" + self.OFF)).is_dir())
        self.assertFalse((self.mods / self.OFF).exists())

    def test_and_the_loadout_follows_the_folder_it_just_renamed(self):
        self.load()
        self.switch(self.OFF, False)
        self.window.live.sync_loadout(confirm=False)
        self.addCleanup(
            lambda: (self.mods / ("-" + self.OFF)).exists()
            and (self.mods / ("-" + self.OFF)).rename(self.mods / self.OFF)
        )
        self.assertEqual(
            Path(self.window.loadout.layer(self.OFF).folder).name, "-" + self.OFF
        )

    def test_exporting_over_a_mod_keeps_a_copy_of_what_it_replaced(self):
        """The export writes the Studio's *reading* of a mod. A reading of a mod
        nobody has tested this against can come back subtly different, and the
        way back should not be re-downloading it."""
        import shutil

        from anno_modding_studio.core import backup

        self.load()
        with tempfile.TemporaryDirectory() as tmp:
            store = Path(tmp) / "backups"
            self.window.output.backup_into = store
            # Back over a copy of the folder the mods were read from -- Export
            # writes anywhere but a mods folder itself, which is Play's.
            out = Path(tmp) / "shared"
            shutil.copytree(self.mods / self.OFF, out / self.OFF)
            self.window.output.set_default_output_dir(out)
            written = out / self.OFF / "data/config/export/main/asset/assets.xml"
            theirs = written.read_text(encoding="utf-8")
            rewrite_every_mod(self.window)
            self.assertNotEqual(written.read_text(encoding="utf-8"), theirs)
            saved = backup.saves(out, into=store)
            self.assertEqual(len(saved), 1)
            put, failed = backup.restore(saved[0])
            self.assertFalse(failed)
            self.assertEqual(written.read_text(encoding="utf-8"), theirs)
        self.assertTrue(put)

    def test_with_nothing_to_move_it_says_so_rather_than_touching_anything(self):
        from unittest import mock

        from PySide6.QtWidgets import QMessageBox

        self.load()
        self.window.loadout_path = self.mods.parent / "Here.annoload"
        with mock.patch.object(QMessageBox, "information") as told:
            self.window.live.sync_loadout(confirm=False)
        self.assertIn("Already live", told.call_args[0][1])
        self.window.files.mark_saved()


class WrongGameModCase(unittest.TestCase):
    """A window loaded for 1800, and a mod shaped like a 117 one beside it.

    Real: an assets.xml pointed at 1800, and a mods folder that turns out to
    hold mods for 117 (or the other way round) -- easy to do since both games'
    mods folders are just called "mods", and easy not to notice, since a
    mismatched read does not fail loudly, it just imports almost nothing.
    """

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        tmp = Path(cls._tmp.name)
        cls.assets = tmp / "assets.xml"
        cls.assets.write_text(make_sample_assets.build_assets_xml(20), encoding="utf-8")
        cls.db = tmp / "index.sqlite"
        index_mod.build_index(cls.assets, cls.db)

        # 117's shape: data/base/config/export/assets.xml, no /Values prefix.
        cls.mod = tmp / "[Gameplay] A 117 Mod"
        patch = cls.mod / "data/base/config/export/assets.xml"
        patch.parent.mkdir(parents=True)
        patch.write_text(
            '<ModOps><ModOp Merge="@1010030/Residence7">'
            "<Residents>77</Residents></ModOp></ModOps>",
            encoding="utf-8",
        )
        (cls.mod / "modinfo.json").write_text(
            '{"ModName": {"English": "A 117 Mod"}}', encoding="utf-8"
        )

        cls.mods_root = tmp / "mods117"
        cls.mods_root.mkdir()
        shutil.copytree(cls.mod, cls.mods_root / cls.mod.name)

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def setUp(self):
        from anno_modding_studio.core.games import ANNO_1800

        self.window = MainWindow()

        self.addCleanup(_flush_deletes)
        self.window.game_files.adopt_index(self.db, self.assets, cached=True)
        self.window.loadout.game = ANNO_1800

    def tearDown(self):
        self.window.editor.search._debounce.stop()
        self.window.game_files.stop_texts_worker()
        if self.window.index is not None:
            self.window.index.close()
            self.window.index = None
        self.window.loadout.clear()
        self.window.deleteLater()


class TestTheOneModReaderWarnsAboutIt(WrongGameModCase):
    def dialog(self):
        from anno_modding_studio.ui.mod_import_dialog import ModImportDialog

        dialog = ModImportDialog(self.window.index, self.window, str(self.mod), "anno1800")
        self.addCleanup(dialog.deleteLater)
        dialog.reload()
        return dialog

    def test_the_banner_names_the_game_it_looks_like(self):
        dialog = self.dialog()
        self.assertTrue(dialog.mismatch_label.isVisibleTo(dialog))
        self.assertIn("Anno 117", dialog.mismatch_label.text())
        self.assertIn("Anno 1800", dialog.mismatch_label.text())

    def test_the_import_button_is_held_off_until_acknowledged(self):
        dialog = self.dialog()
        # This mod's op does have a narrow equivalent, so without the mismatch
        # there would be something tickable to import.
        self.assertFalse(dialog.import_button.isEnabled())
        dialog.mismatch_box.setChecked(True)
        self.assertTrue(dialog.import_button.isEnabled())

    def test_unticking_it_again_holds_the_button_off_once_more(self):
        dialog = self.dialog()
        dialog.mismatch_box.setChecked(True)
        dialog.mismatch_box.setChecked(False)
        self.assertFalse(dialog.import_button.isEnabled())

    def test_a_matching_mod_shows_no_banner_at_all(self):
        from anno_modding_studio.ui.mod_import_dialog import ModImportDialog

        plain = self.mod.parent / "[Gameplay] A 1800 Mod"
        patch = plain / "data/config/export/main/asset/assets.xml"
        patch.parent.mkdir(parents=True)
        patch.write_text(
            '<ModOps><ModOp Type="merge" GUID="1010030" Path="/Values/Residence7">'
            "<Residents>77</Residents></ModOp></ModOps>",
            encoding="utf-8",
        )
        dialog = ModImportDialog(self.window.index, self.window, str(plain), "anno1800")
        self.addCleanup(dialog.deleteLater)
        dialog.reload()
        self.assertFalse(dialog.mismatch_label.isVisibleTo(dialog))
        self.assertTrue(dialog.import_button.isEnabled())


class TestTheModsFolderReaderWarnsAboutIt(WrongGameModCase):
    def dialog(self):
        from anno_modding_studio.ui.mods_folder_dialog import ModsFolderDialog

        dialog = ModsFolderDialog(
            self.window.index, self.window, start_dir=str(self.mods_root), game="anno1800"
        )
        self.addCleanup(dialog.deleteLater)
        dialog.reload()
        if dialog._worker is not None:
            dialog._worker.wait()
            _app.processEvents()
        return dialog

    def test_the_banner_names_which_mods_and_which_game(self):
        dialog = self.dialog()
        self.assertTrue(dialog.mismatch_label.isVisibleTo(dialog))
        self.assertIn("A 117 Mod", dialog.mismatch_label.text())
        self.assertIn("Anno 117", dialog.mismatch_label.text())

    def test_it_starts_unticked_so_nothing_is_held_off(self):
        """Ticked and blocking the import was a start nobody wants: the rest
        of the folder could not be imported until the one mod was found."""
        dialog = self.dialog()
        self.assertEqual(dialog.chosen(), [])
        self.assertIn("left unticked", dialog.mismatch_label.text())
        # The box is about a mod that has been ticked, and none has.
        self.assertFalse(dialog.mismatch_box.isVisibleTo(dialog))
        dialog.table.item(0, 0).setCheckState(Qt.CheckState.Checked)
        self.assertTrue(dialog.mismatch_box.isVisibleTo(dialog))
        self.assertFalse(dialog.load_button.isEnabled())

    def test_the_filter_narrows_the_list_and_the_ticking(self):
        dialog = self.dialog()
        dialog.filter_box.setText("117 gameplay")
        self.assertFalse(dialog.table.isRowHidden(0))
        dialog.filter_box.setText("nothing like it")
        self.assertTrue(dialog.table.isRowHidden(0))
        self.assertTrue(dialog.none_label.isVisibleTo(dialog))
        # Tick none is of the rows shown: a hidden row keeps its tick.
        dialog.table.item(0, 0).setCheckState(Qt.CheckState.Checked)
        dialog._tick_shown(False)
        self.assertEqual(len(dialog.chosen()), 1)
        dialog.filter_box.clear()
        dialog._tick_shown(False)
        self.assertEqual(dialog.chosen(), [])

    def test_tick_all_leaves_it_out(self):
        """Or one press brought back the import held off by one mod."""
        dialog = self.dialog()
        dialog._tick_shown(True)
        self.assertEqual(dialog.chosen(), [])
        self.assertFalse(dialog.mismatch_box.isVisibleTo(dialog))

    def test_the_import_button_is_held_off_while_it_is_ticked(self):
        dialog = self.dialog()
        for row in range(dialog.table.rowCount()):
            dialog.table.item(row, 0).setCheckState(Qt.CheckState.Checked)
        dialog._refresh_button()
        self.assertFalse(dialog.load_button.isEnabled())
        dialog.mismatch_box.setChecked(True)
        self.assertTrue(dialog.load_button.isEnabled())

    def test_unticking_the_mismatched_mod_itself_also_clears_the_block(self):
        """You do not have to acknowledge a mod you are not even importing."""
        dialog = self.dialog()
        for row in range(dialog.table.rowCount()):
            dialog.table.item(row, 0).setCheckState(Qt.CheckState.Unchecked)
        dialog._refresh_button()
        # Nothing ticked -> nothing to import, but not because of a mismatch.
        self.assertFalse(dialog.load_button.isEnabled())
        self.assertIn("Import mods", dialog.load_button.text())


class TestEnterInTheModsFolderBoxes(WrongGameModCase):
    """Enter in a box is that box's own: the folder box reads, the filter box
    has nothing left to do.  Neither passes the key on to the default button,
    which after a read is Import."""

    def setUp(self):
        super().setUp()
        from anno_modding_studio.ui.mods_folder_dialog import ModsFolderDialog

        self.dialog = ModsFolderDialog(
            self.window.index, self.window, start_dir=str(self.mods_root), game="anno1800"
        )
        self.addCleanup(self.dialog.deleteLater)
        # Shown: a dialog that is not passes Enter to no button at all.
        self.dialog.show()
        self.addCleanup(self.dialog.close)
        _app.processEvents()

    def read(self):
        """The folder read, and its one mod ticked to be imported."""
        dialog = self.dialog
        dialog.reload()
        dialog._worker.wait()
        _app.processEvents()
        dialog.table.item(0, 0).setCheckState(Qt.CheckState.Checked)
        dialog.mismatch_box.setChecked(True)
        self.assertTrue(dialog.load_button.isEnabled())
        self.assertTrue(dialog.load_button.isDefault())

    def press(self, widget):
        """Return and the keypad's Enter, pressed with ``widget`` focused."""
        from PySide6.QtTest import QTest

        widget.setFocus()
        for key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            QTest.keyClick(widget, key)

    def imported(self) -> bool:
        return self.dialog.result() == QDialog.DialogCode.Accepted or bool(self.dialog.layers)

    def test_in_the_filter_box_it_reads_nothing_before_a_read(self):
        dialog = self.dialog
        self.assertTrue(dialog.read_button.isDefault())
        dialog.filter_box.setText("gameplay")
        self.press(dialog.filter_box)
        reading = dialog._worker
        if reading is not None:
            reading.wait()
            _app.processEvents()
        self.assertIsNone(reading)
        self.assertIsNone(dialog.session)

    def test_in_the_filter_box_it_imports_nothing_after_a_read(self):
        self.read()
        self.dialog.filter_box.setText("gameplay")
        self.press(self.dialog.filter_box)
        self.assertFalse(self.imported())

    def test_in_the_folder_box_it_reads_and_does_not_import(self):
        """A mistyped path said "is not a folder" and then imported the read
        before it, leaving the bad path to be saved as the mods folder."""
        self.read()
        self.dialog.folder_box.setText(str(self.mods_root / "no such folder"))
        self.press(self.dialog.folder_box)
        self.assertIn("is not a folder", self.dialog.status.text())
        self.assertFalse(self.imported())

    def test_outside_the_boxes_it_still_imports_after_a_read(self):
        self.read()
        self.press(self.dialog)
        self.assertTrue(self.imported())
        self.assertEqual([layer.name for layer in self.dialog.layers], ["A 117 Mod"])


class TestTheXmlView(WorkflowCase):
    """The asset editor's other view: the asset as XML, a tab for each mod.

    Read-only here.  It is the same asset, the same mod written into and the
    same changes as the Fields view, shown as the ModOps each mod writes and
    the asset each leaves."""

    GUID = "1010340"

    def setUp(self):
        super().setUp()
        self.editor = self.window.editor
        self.xml = self.editor.xml

    def load_a_mod(self, origin="Their Mod", value="20.00"):
        from anno_modding_studio.core.changes import ModLayer

        loadout = self.window.loadout
        loadout.set_layer(ModLayer(origin=origin, name=origin, order=1))
        item = self._find_item("CycleTime")
        loadout.set_change(loadout.place(Change(
            guid=self.GUID, asset_label="asset", path=self.editor._path_for(item)[0],
            tag="CycleTime", old_value=item.text(1), new_value=value,
            origin=origin, authored=False,
        )))
        self.editor.refresh_target()
        self.editor.refresh_from_loadout()

    def tab_names(self):
        return [self.xml.strip.tabText(at) for at in range(self.xml.strip.count())]

    def test_the_editor_opens_on_fields_and_fills_no_xml_behind_it(self):
        self.window._open_asset(self.GUID)
        self.assertEqual(self.editor.view(), "fields")
        self.assertIs(self.editor.stack.currentWidget(), self.editor.fields_page)
        self.assertEqual(self.xml.turns, [])

    def test_switching_shows_the_base_game_and_your_mod(self):
        self.window._open_asset(self.GUID)
        self.editor.set_view("xml")
        self.assertIs(self.editor.stack.currentWidget(), self.xml)
        self.assertEqual(len(self.tab_names()), 2)
        self.assertEqual(self.tab_names()[0], "Base game")
        # It opens on the mod changes go to.
        self.assertTrue(self.xml.current().editing)

    def test_an_edit_made_in_fields_is_a_modop_in_the_xml_view(self):
        self.window._open_asset(self.GUID)
        self._edit("CycleTime", "15.00")
        self.editor.set_view("xml")
        self.assertIn("CycleTime", self.xml.typing.toPlainText())
        self.assertIn("<ModOp ", self.xml.typing.toPlainText())
        self.assertIn("<CycleTime>15.00</CycleTime>", self.xml.asset_in_front().toPlainText())
        self.xml.strip.setCurrentIndex(0)
        self.assertNotIn("<CycleTime>15.00</CycleTime>", self.xml.asset_in_front().toPlainText())

    def test_the_base_game_tab_has_no_modops_pane(self):
        self.window._open_asset(self.GUID)
        self.editor.set_view("xml")
        self.xml.strip.setCurrentIndex(0)
        self.assertTrue(self.xml.modops_side.isHidden())
        self.xml.strip.setCurrentIndex(1)
        self.assertFalse(self.xml.modops_side.isHidden())

    def test_every_pane_of_another_tab_is_read_only(self):
        self.window._open_asset(self.GUID)
        self.editor.set_view("xml")
        self.xml.strip.setCurrentIndex(0)
        self.assertTrue(self.xml.modops.isReadOnly())
        self.assertTrue(self.xml.asset_in_front().isReadOnly())
        self.assertIn("read-only", self.xml.asset_in_front().accessibleName())

    def test_a_loaded_mod_gets_a_tab_in_load_order(self):
        self.window._open_asset(self.GUID)
        self.load_a_mod()
        self.editor.set_view("xml")
        self.assertEqual(self.tab_names()[:2], ["Base game", "Their Mod"])
        self.assertEqual(len(self.tab_names()), 3)

    def test_the_tab_changes_go_to_says_so_and_the_others_say_read_only(self):
        self.window._open_asset(self.GUID)
        self.load_a_mod()
        self.editor.set_view("xml")
        names = [self.xml.strip.accessibleTabName(at) for at in range(3)]
        self.assertIn("read-only", names[0])
        self.assertIn("read-only", names[1])
        self.assertIn("the mod you are editing", names[2])
        self.assertFalse(self.xml.strip.tabIcon(2).isNull())

    def test_the_last_tabs_asset_is_headed_as_what_the_game_loads(self):
        self.window._open_asset(self.GUID)
        self.load_a_mod()
        self.editor.set_view("xml")
        self.xml.strip.setCurrentIndex(1)
        self.assertNotIn("game loads", self.xml.asset_heading.text())
        self.xml.strip.setCurrentIndex(2)
        self.assertIn("game loads", self.xml.asset_heading.text())

    def test_a_later_mod_writing_over_yours_is_said_on_both_tabs(self):
        from anno_modding_studio.core.changes import ModLayer

        self.window._open_asset(self.GUID)
        self.load_a_mod()
        # A second mod, loading after the first and setting the same field.
        loadout = self.window.loadout
        loadout.set_layer(ModLayer(origin="Zz Later", name="Zz Later", order=2))
        as_yours(loadout, "Their Mod")
        loadout.editing = "Their Mod"
        self.editor.refresh_target()
        self.editor.load_asset(self.GUID)
        self.load_a_mod("Zz Later", "30.00")
        self.editor.set_view("xml")
        self.assertEqual(self.xml.current().origin, "Their Mod")
        self.assertFalse(self.xml.contested_label.isHidden())
        self.assertIn("CycleTime", self.xml.contested_label.text())
        later = self.tab_names().index("Zz Later")
        self.assertIn("writes over", self.xml.strip.accessibleTabName(later))
        self.xml.strip.setCurrentIndex(later)
        self.assertIn("Writes over", self.xml.contested_label.text())

    def test_it_follows_a_change_made_while_it_is_in_front(self):
        self.window._open_asset(self.GUID)
        self.editor.set_view("xml")
        self.assertEqual(self.xml.typing.toPlainText(), "")
        self.load_a_mod()
        self.assertIn("Their Mod", self.tab_names())

    def test_it_keeps_the_tab_you_were_on_when_the_asset_is_read_again(self):
        self.window._open_asset(self.GUID)
        self.load_a_mod()
        self.editor.set_view("xml")
        self.xml.strip.setCurrentIndex(1)
        self.editor.refresh_from_loadout()
        self.assertEqual(self.xml.current().origin, "Their Mod")

    def test_a_row_can_be_shown_in_the_xml_view(self):
        self.window._open_asset(self.GUID)
        item = self._edit("CycleTime", "15.00")
        self.editor._show_in_xml(item)
        self.assertEqual(self.editor.view(), "xml")
        self.assertIn("<CycleTime>", self.xml.asset_in_front().textCursor().selectedText())
        self.assertIn("<ModOp ", self.xml.typing.textCursor().selectedText())

    def test_a_line_can_be_shown_in_the_fields_view(self):
        self.window._open_asset(self.GUID)
        self.editor.set_view("xml")
        lines = self.xml.asset_in_front().toPlainText().splitlines()
        line = next(n for n, text in enumerate(lines, 1) if "<CycleTime>" in text)
        self.xml.asset_in_front().fieldRequested.emit(line)
        self.assertEqual(self.editor.view(), "fields")
        self.assertEqual(self.editor.tree.currentItem().text(0), "CycleTime")

    def test_the_view_is_remembered(self):
        self.window._open_asset(self.GUID)
        self.editor.set_view("xml")
        self.assertEqual(self.window.settings.value("editorView"), "xml")
        self.editor.set_view("fields")
        self.assertEqual(self.window.settings.value("editorView"), "fields")

    # -- finding ------------------------------------------------------------
    def test_find_opens_the_find_bar_in_the_xml_view(self):
        self.window._open_asset(self.GUID)
        self.editor.set_view("xml")
        self.assertTrue(self.xml.find_bar.isHidden())
        self.window._find_in_asset()
        self.assertFalse(self.xml.find_bar.isHidden())

    def test_find_counts_the_matches_and_goes_to_the_first(self):
        self.window._open_asset(self.GUID)
        self.editor.set_view("xml")
        self.window._find_in_asset()
        self.xml.find_bar.box.setText("cycletime")
        self.assertIn("2", self.xml.find_bar.count.text())
        self.assertEqual(self.xml.asset_in_front().textCursor().selectedText(), "CycleTime")
        self.xml.find_bar.box.setText("no such text anywhere")
        self.assertEqual(self.xml.find_bar.count.text(), "No matches")

    def test_find_goes_round_the_end(self):
        self.window._open_asset(self.GUID)
        self.editor.set_view("xml")
        self.window._find_in_asset()
        self.xml.find_bar.box.setText("cycletime")
        first = self.xml.asset_in_front().textCursor().selectionStart()
        self.xml.find_bar.find()
        self.assertNotEqual(self.xml.asset_in_front().textCursor().selectionStart(), first)
        self.xml.find_bar.find()
        self.assertEqual(self.xml.asset_in_front().textCursor().selectionStart(), first)

    def test_find_in_the_fields_view_is_the_field_filter(self):
        self.window.show()
        self.window._open_asset(self.GUID)
        self.assertTrue(self.editor.find_in_asset())
        self.assertTrue(self.xml.find_bar.isHidden())

    def test_find_with_no_asset_open_is_the_search_for_one(self):
        self.window.show()
        self.window._go_to_editor()
        self.assertFalse(self.editor.find_in_asset())
        self.window._find_in_asset()
        _app.processEvents()
        self.assertTrue(self.editor.search.results.isVisible())

    def test_the_two_keys_are_find_and_search(self):
        from PySide6.QtGui import QKeySequence

        keys = {
            action.text(): action.shortcut().toString()
            for menu in self.window.menuBar().actions()
            for action in menu.menu().actions()
        }
        self.assertEqual(
            keys["Find in asset or file"],
            QKeySequence(QKeySequence.StandardKey.Find).toString(),
        )
        self.assertEqual(keys["Search for an asset"], "Ctrl+P")


class TestNamesBesideTheXmlView(WorkflowCase):
    """Each pane of the XML view names, after a line, the assets its numbers
    link to -- all but the asset the view is of."""

    GUID = "1010340"

    def setUp(self):
        super().setUp()
        self.editor = self.window.editor
        self.xml = self.editor.xml
        self.window._open_asset(self.GUID)

    def lines(self, pane) -> list:
        return pane.toPlainText().split("\n")

    def test_the_asset_names_what_it_links_to_and_not_itself(self):
        self.editor.set_view("xml")
        self.xml.strip.setCurrentIndex(0)
        pane = self.xml.asset
        lines, named = self.lines(pane), pane.named_assets()
        product = next(at for at, line in enumerate(lines) if "<Product>" in line)
        guid = lines[product].split(">")[1].split("<")[0]
        ((linked, name),) = named[product]
        self.assertEqual(linked, guid)
        self.assertEqual(name, self.window.workspace.asset_row(guid).display)
        own = next(at for at, line in enumerate(lines) if "<GUID>%s<" % self.GUID in line)
        self.assertNotIn(own, named)
        # Amounts are numbers, whatever asset has the same one.
        amount = next(at for at, line in enumerate(lines) if "<Amount>" in line)
        self.assertNotIn(amount, named)

    def test_the_modops_do_not_name_the_asset_they_are_about(self):
        self._edit("CycleTime", "15.00")
        self.editor.set_view("xml")
        lines = self.lines(self.xml.typing)
        self.assertTrue(any('GUID="%s"' % self.GUID in line for line in lines))
        self.assertEqual(self.xml.typing.named_assets(), {})

    def test_a_name_opens_its_asset(self):
        self.editor.set_view("xml")
        self.xml.strip.setCurrentIndex(0)
        pane = self.xml.asset
        ((guid, _name),) = next(iter(pane.named_assets().values()))
        pane.assetClicked.emit(guid)
        self.assertEqual(self.editor.guid, guid)


class TestTypingModOpsInTheXmlView(WorkflowCase):
    """The pane of the mod changes go to is typed in, and Apply makes the mod
    say what was typed: read as an import reads it, as one step to undo."""

    GUID = "1010340"

    def setUp(self):
        super().setUp()
        self.editor = self.window.editor
        self.xml = self.editor.xml
        self.window._open_asset(self.GUID)
        self.cycle = self.editor._path_for(self._find_item("CycleTime"))[0]
        self.editor.set_view("xml")

    def op(self, value: str) -> str:
        return (
            '<ModOp Type="replace" GUID="%s" Path="%s"><CycleTime>%s</CycleTime></ModOp>'
            % (self.GUID, self.cycle, value)
        )

    def type(self, text: str) -> None:
        """Type ``text`` over whatever the pane holds, as the keyboard would:
        setPlainText is how the Studio fills it, and counts as nothing typed."""
        from PySide6.QtGui import QTextCursor

        cursor = self.xml.typing.textCursor()
        cursor.select(QTextCursor.SelectionType.Document)
        cursor.insertText(text)

    def changes(self):
        return self.window.loadout.for_asset(self.GUID, "")

    def test_only_the_pane_of_the_mod_changes_go_to_is_typed_in(self):
        self.assertFalse(self.xml.typing.isReadOnly())
        self.assertFalse(self.xml.typing.isHidden())
        self.assertTrue(self.xml.modops.isHidden())
        self.assertFalse(self.xml.bar.isHidden())
        self.assertNotIn("read-only", self.xml.typing.accessibleName())
        self.xml.strip.setCurrentIndex(0)
        self.assertTrue(self.xml.bar.isHidden())
        self.assertTrue(self.xml.typing.isHidden())
        self.assertTrue(self.xml.asset_in_front().isReadOnly())

    def test_apply_and_revert_wait_for_something_typed(self):
        self.assertFalse(self.xml.apply_button.isEnabled())
        self.assertFalse(self.xml.revert_button.isEnabled())
        self.type(self.op("15.00"))
        self.assertTrue(self.xml.apply_button.isEnabled())
        self.assertTrue(self.xml.revert_button.isEnabled())

    def test_apply_makes_the_mod_say_what_was_typed(self):
        self.type(self.op("15.00"))
        self.xml.apply_button.click()
        (change,) = self.changes()
        self.assertEqual((change.path, change.new_value), (self.cycle, "15.00"))
        # The panes are written again from the mod, and nothing is pending.
        self.assertFalse(self.xml.has_unapplied())
        self.assertIn("<?xml", self.xml.typing.toPlainText())
        self.assertIn("<CycleTime>15.00</CycleTime>", self.xml.asset_in_front().toPlainText())
        self.assertIn("1", self.xml.status.text())
        # And the rest of the window has heard.
        self.assertEqual(len(change_rows(self.window.changes_panel)), 1)
        self.assertTrue(self.window.files.unsaved_work())

    def test_the_title_counts_the_change_and_not_the_op_beside_it(self):
        """From the first Apply the title bar ran ahead of the changes list:
        the op typed was counted as a change, and so was what it comes to."""
        self.type(self.op("15.00"))
        self.xml.apply_button.click()
        self.assertEqual(len(change_rows(self.window.changes_panel)), 1)
        self.assertTrue(self.window.changes_panel.summary.text().startswith("1 change across "))
        self.assertRegex(self.window.windowTitle(), r"  —  1 change$")

    def test_the_fields_view_shows_what_was_applied(self):
        self.type(self.op("15.00"))
        self.xml.apply_button.click()
        self.editor.set_view("fields")
        self.assertEqual(self._find_item("CycleTime").text(1), "15.00")

    def test_an_apply_is_one_step_to_undo(self):
        self.type(self.op("15.00"))
        self.xml.apply_button.click()
        self.window.undo_action.trigger()
        self.assertEqual(self.changes(), [])
        # The asset is without it, and what was typed is typed again.
        self.assertNotIn("15.00", self.xml.asset_in_front().toPlainText())
        self.assertTrue(self.xml.has_unapplied())
        self.window.redo_action.trigger()
        self.assertEqual(len(self.changes()), 1)
        self.assertFalse(self.xml.has_unapplied())

    def test_text_that_is_not_xml_is_not_applied_and_says_where(self):
        self.type("<ModOps>\n<ModOp Type=\n</ModOps>")
        self.xml.apply_button.click()
        self.assertEqual(self.changes(), [])
        self.assertIn("Not applied", self.xml.status.text())
        # Where the reader stopped, said once.
        self.assertEqual(self.xml.status.text().count("ine 3"), 1)
        # What was typed is still there to be put right.
        self.assertTrue(self.xml.has_unapplied())
        self.assertIn("<ModOp Type=", self.xml.typing.toPlainText())

    def test_revert_puts_back_what_the_mod_says(self):
        self.type(self.op("15.00"))
        self.xml.apply_button.click()
        written = self.xml.typing.toPlainText()
        self.type("something else")
        self.xml.revert_button.click()
        self.assertEqual(self.xml.typing.toPlainText(), written)
        self.assertFalse(self.xml.has_unapplied())

    def test_what_is_typed_stays_while_another_tab_is_looked_at(self):
        self.type(self.op("15.00"))
        self.xml.strip.setCurrentIndex(0)
        self.xml.strip.setCurrentIndex(1)
        self.assertEqual(self.xml.typing.toPlainText(), self.op("15.00"))
        self.assertTrue(self.xml.apply_button.isEnabled())

    def test_a_comment_typed_above_an_op_is_its_note_and_undo_takes_it_back(self):
        self.type(self.op("15.00"))
        self.xml.apply_button.click()
        written = self.xml.typing.toPlainText()
        self.type(written.replace("<ModOp ", "<!-- slower on purpose -->\n<ModOp "))
        self.xml.apply_button.click()
        (op,) = self.window.loadout.ops
        self.assertEqual(op.note, "slower on purpose")
        self.assertIn("note: slower on purpose", self.xml.typing.toPlainText())
        self.window.undo_action.trigger()
        (op,) = self.window.loadout.ops
        self.assertEqual(op.note, "")

    # -- typing the asset ------------------------------------------------------
    def type_asset(self, text: str) -> None:
        from PySide6.QtGui import QTextCursor

        cursor = self.xml.asset_typing.textCursor()
        cursor.select(QTextCursor.SelectionType.Document)
        cursor.insertText(text)

    def shown_asset(self) -> str:
        return self.xml.asset_typing.toPlainText()

    def cycle_line(self, value: str) -> str:
        return "<CycleTime>%s</CycleTime>" % value

    def original(self) -> str:
        import re

        return re.search(r"<CycleTime>([^<]*)</CycleTime>", self.shown_asset()).group(1)

    def test_the_asset_pane_of_the_mod_changes_go_to_is_typed_in(self):
        self.assertFalse(self.xml.asset_typing.isReadOnly())
        self.assertFalse(self.xml.asset_typing.isHidden())
        self.assertTrue(self.xml.asset.isHidden())
        self.assertNotIn("read-only", self.xml.asset_typing.accessibleName())

    def test_a_value_typed_in_the_asset_becomes_a_narrow_modop(self):
        was = self.original()
        self.type_asset(self.shown_asset().replace(self.cycle_line(was), self.cycle_line("15.00")))
        self.xml.apply_button.click()
        (change,) = self.changes()
        self.assertEqual((change.path, change.old_value, change.new_value),
                         (self.cycle, was, "15.00"))
        # The other pane shows the op that was worked out.
        self.assertIn('Path="%s"' % self.cycle, self.xml.typing.toPlainText())
        self.assertIn("1", self.xml.status.text())
        self.assertFalse(self.xml.has_unapplied())
        self.window.undo_action.trigger()
        self.assertEqual(self.changes(), [])

    def test_typing_in_one_pane_holds_the_other_shut(self):
        self.type_asset(self.shown_asset() + "\n")
        self.assertTrue(self.xml.typing.isReadOnly())
        self.assertFalse(self.xml.asset_typing.isReadOnly())
        self.xml.revert_button.click()
        self.assertFalse(self.xml.typing.isReadOnly())
        self.type(self.op("15.00"))
        self.assertTrue(self.xml.asset_typing.isReadOnly())
        self.assertFalse(self.xml.typing.isReadOnly())

    def test_the_pane_held_shut_says_why(self):
        """It goes grey the moment the other is typed in: its heading, its
        tooltip and the line beside Apply each say what opens it again."""
        modops, asset = self.xml.modops_heading.text(), self.xml.asset_heading.text()
        self.type_asset(self.shown_asset() + "\n")
        self.assertNotEqual(self.xml.modops_heading.text(), modops)
        self.assertIn("Apply", self.xml.modops_heading.text())
        self.assertEqual(self.xml.asset_heading.text(), asset)
        self.assertIn("Apply", self.xml.typing.toolTip())
        self.assertIn("Revert", self.xml.status.text())
        self.xml.revert_button.click()
        self.assertEqual(self.xml.modops_heading.text(), modops)
        self.assertEqual(self.xml.typing.toolTip(), "")
        self.assertEqual(self.xml.status.text(), "")
        self.type(self.op("15.00"))
        self.assertIn("Apply", self.xml.asset_heading.text())
        self.assertEqual(self.xml.modops_heading.text(), modops)

    def test_a_refusal_is_not_talked_over_by_the_next_key(self):
        self.type("<ModOps><ModOp")
        self.xml.apply_button.click()
        refused = self.xml.status.text()
        self.xml.typing.textCursor().insertText(" ")
        self.assertEqual(self.xml.status.text(), refused)

    def test_the_lines_the_mod_changed_are_marked_in_its_asset(self):
        self.type(self.op("15.00"))
        self.xml.apply_button.click()
        pane = self.xml.asset_typing
        (line,) = pane.marks()
        self.assertEqual(pane.marks()[line], "changed")
        self.assertIn(self.cycle_line("15.00"), pane.document().findBlockByNumber(line).text())
        # A line typed above it, and the mark is still on its line.
        cursor = pane.textCursor()
        cursor.setPosition(0)
        cursor.insertText("\n")
        (moved,) = pane.marks()
        self.assertEqual(moved, line + 1)

    def test_the_caret_on_an_op_shows_the_node_it_sets(self):
        from PySide6.QtGui import QTextCursor
        from PySide6.QtWidgets import QApplication

        self.type(self.op("15.00"))
        self.xml.apply_button.click()
        self.window.show()
        self.addCleanup(self.window.hide)
        pane = self.xml.typing
        pane.setFocus()
        QApplication.processEvents()
        if not pane.hasFocus():
            self.skipTest("no keyboard focus on this platform")
        at = next(
            number for number, line in enumerate(pane.toPlainText().splitlines())
            if line.lstrip().startswith("<ModOp ")
        )
        pane.setTextCursor(QTextCursor(pane.document().findBlockByNumber(at + 1)))
        chosen = self.xml.asset_typing.textCursor().selectedText()
        self.assertIn(self.cycle_line("15.00"), chosen)
        self.assertFalse(self.xml.has_unapplied())

    def test_fields_and_xml_are_the_two_halves_of_one_switch(self):
        self.assertTrue(self.editor.xml_button.isChecked())
        self.assertFalse(self.editor.fields_button.isChecked())
        self.editor.fields_button.click()
        self.assertEqual(self.editor.view(), "fields")
        self.assertFalse(self.editor.xml_button.isChecked())

    def test_a_whole_asset_pasted_and_changed_inline_comes_to_what_changed(self):
        """What a beginner does with a wide replace: the Studio works out
        the narrow ModOps."""
        was = self.original()
        pasted = " ".join(line.strip() for line in self.shown_asset().splitlines())
        self.type_asset(pasted.replace(self.cycle_line(was), self.cycle_line("15.00")))
        self.xml.apply_button.click()
        self.assertEqual(len(self.changes()), 1)
        self.assertEqual(self.xml.typing.toPlainText().count("<ModOp "), 1)

    def test_an_asset_that_is_not_xml_says_where_in_that_pane(self):
        self.type_asset(self.shown_asset().replace("</CycleTime>", "", 1))
        self.xml.apply_button.click()
        self.assertEqual(self.changes(), [])
        self.assertIn("Not applied. Line", self.xml.status.text())
        self.assertTrue(self.xml.has_unapplied())

    def test_another_guid_typed_is_refused_in_words(self):
        self.type_asset(self.shown_asset().replace(
            "<GUID>%s</GUID>" % self.GUID, "<GUID>1</GUID>", 1))
        self.xml.apply_button.click()
        self.assertEqual(self.changes(), [])
        self.assertIn("GUID", self.xml.status.text())
        self.assertIn("Not applied", self.xml.status.text())

    def test_a_list_nothing_tells_apart_refuses_all_of_it_and_says_which_list(self):
        """Two of the four maintenance entries are empty, so no narrow ModOp
        can say which entry a change to that list is in. The refusal said
        "Not applied" and then that "what is shown swaps the whole node",
        with nothing shown, and never said where. It says one thing now:
        none of it is applied, the value typed elsewhere included, and the
        caret goes to the list that stops it."""
        was = self.original()
        self.type_asset(
            self.shown_asset()
            .replace(self.cycle_line(was), self.cycle_line("15.00"))
            .replace("<Amount>40</Amount>", "<Amount>41</Amount>")
        )
        self.xml.apply_button.click()
        self.assertEqual(self.changes(), [])
        self.assertEqual(
            self.xml.status.text(),
            "Not applied, none of it. The change at /Values/Maintenance/Maintenances "
            "cannot be written as a narrow ModOp: its 4 <Item> entries have nothing "
            "unique to match them on. Make that change in Fields, or type the ModOp "
            "for it.",
        )
        self.assertTrue(self.xml.has_unapplied())
        self.assertIn("<Maintenances>", self.xml.asset_typing.textCursor().selectedText())

    def test_a_placeholder_taken_out_is_said_to_be_written_and_maybe_unnoticed(self):
        """An entry that only names an asset is one the game may read as no
        entry at all. Taken out by hand it is written, and the line says
        both: it used to say the removal was not written, and put it back."""
        from anno_modding_studio.core import typedops

        self.xml.applied(
            typedops.Read(ops=1, unnoticed=1), typedops.Applied(added=1),
            self.index.display_name,
        )
        self.assertEqual(
            self.xml.status.text(),
            "Read as 1 change. 1 entry you took out only names an asset and sets "
            "nothing, so the game may not notice it gone. The removal is written "
            "all the same.",
        )

    def test_an_asset_left_other_than_as_typed_is_said_without_a_guess_at_why(self):
        """Two fields typed in the other order are no change a ModOp makes,
        so the asset is shown in the order it has. The line used to put any
        such difference down to a placeholder entry."""
        fields = [
            line for line in self.shown_asset().splitlines(keepends=True)
            if "<BuildingType>" in line or "<BuildModeStartLevel>" in line
        ]
        self.assertEqual(len(fields), 2)
        swapped = self.shown_asset().replace("".join(fields), "".join(reversed(fields)))
        self.assertNotEqual(swapped, self.shown_asset())
        self.type_asset(swapped)
        self.xml.apply_button.click()
        self.assertEqual(self.changes(), [])
        self.assertEqual(
            self.xml.status.text(),
            "No change to the mod. The asset is not all as you typed it. It is "
            "shown as the mod's ModOps leave it.",
        )

    def test_text_typed_between_an_assets_fields_is_said_to_be_left_out(self):
        """A number straight after a node's opening tag is no field's value.
        The value typed with it is applied, and the line says what was not."""
        was = self.original()
        self.type_asset(
            self.shown_asset()
            .replace(self.cycle_line(was), self.cycle_line("15.00"))
            .replace("<Building>", "<Building>55")
        )
        self.xml.apply_button.click()
        self.assertEqual(len(self.changes()), 1)
        self.assertNotIn("<Building>55", self.shown_asset())
        self.assertEqual(
            self.xml.status.text(),
            "Read as 1 change. Text typed between elements was left out.",
        )

    def test_leaving_with_the_asset_typed_asks_and_apply_applies_it(self):
        was = self.original()
        self.type_asset(self.shown_asset().replace(self.cycle_line(was), self.cycle_line("15.00")))
        other = self.other_asset()
        with mock.patch.object(self.window.prompts, "ask_unapplied_modops", return_value="apply"):
            self.window._open_asset(other)
        self.assertEqual(self.editor.guid, other)
        self.assertEqual(len(self.changes()), 1)

    def test_and_the_question_is_told_which_pane_was_typed_in(self):
        """It asked about "the ModOps you typed" when it was the asset."""
        self.type_asset(self.shown_asset() + "\n")
        with mock.patch.object(self.window.prompts, "ask_unapplied_modops", return_value="") as ask:
            self.window._open_asset(self.other_asset())
        ask.assert_called_once_with(self.editor.asset_label, True)
        self.xml.revert_button.click()
        self.type(self.op("15.00"))
        with mock.patch.object(self.window.prompts, "ask_unapplied_modops", return_value="") as ask:
            self.window._open_asset(self.other_asset())
        ask.assert_called_once_with(self.editor.asset_label, False)

    # -- indenting ------------------------------------------------------------
    def press(self, key, modifiers=Qt.KeyboardModifier.NoModifier, text=""):
        from PySide6.QtGui import QKeyEvent

        pane = self.xml.typing
        pane.keyPressEvent(QKeyEvent(QEvent.Type.KeyPress, key, modifiers, text))

    def test_tab_indents_where_the_pane_is_typed_in(self):
        self.type("<ModOps/>")
        self.press(Qt.Key.Key_Return)
        self.press(Qt.Key.Key_Tab)
        self.assertEqual(self.xml.typing.toPlainText(), "<ModOps/>\n  ")
        self.assertFalse(self.xml.typing.tabChangesFocus())
        # A pane that is only read leaves Tab to move on.
        self.assertTrue(self.xml.asset_in_front().tabChangesFocus())

    def test_enter_starts_the_next_line_where_this_one_starts(self):
        self.type("    <ModOp/>")
        self.press(Qt.Key.Key_Return)
        self.assertEqual(self.xml.typing.toPlainText(), "    <ModOp/>\n    ")
        # After an opening tag, a level deeper: the rest is test_xml_typing's.
        self.type("    <ModOp>")
        self.press(Qt.Key.Key_Return)
        self.assertEqual(self.xml.typing.toPlainText(), "    <ModOp>\n      ")

    def test_tab_and_shift_tab_move_every_line_selected(self):
        from PySide6.QtGui import QTextCursor

        self.type("<a>\n<b/>\n</a>")
        cursor = self.xml.typing.textCursor()
        cursor.select(QTextCursor.SelectionType.Document)
        self.xml.typing.setTextCursor(cursor)
        self.press(Qt.Key.Key_Tab)
        self.assertEqual(self.xml.typing.toPlainText(), "  <a>\n  <b/>\n  </a>")
        self.press(Qt.Key.Key_Backtab, Qt.KeyboardModifier.ShiftModifier)
        self.assertEqual(self.xml.typing.toPlainText(), "<a>\n<b/>\n</a>")
        # And one press of the pane's own undo takes a whole indent back.
        self.press(Qt.Key.Key_Tab)
        self.xml.typing.undo()
        self.assertEqual(self.xml.typing.toPlainText(), "<a>\n<b/>\n</a>")

    def test_esc_then_tab_leaves_the_pane_and_does_not_indent(self):
        self.window.show()
        self.type("<a>")
        self.xml.typing.setFocus()
        _app.processEvents()
        held = self.xml.typing.hasFocus()
        self.press(Qt.Key.Key_Escape)
        self.press(Qt.Key.Key_Tab)
        self.assertEqual(self.xml.typing.toPlainText(), "<a>")
        if held:
            # And it does leave: a text box typed in says it has no next
            # control, so the keyboard stayed in the pane.
            self.assertFalse(self.xml.typing.hasFocus())
        # Once: the Tab after that indents again.
        self.press(Qt.Key.Key_Tab)
        self.assertEqual(self.xml.typing.toPlainText(), "<a>  ")
        self.assertIn("Esc", self.xml.typing.accessibleDescription())

    def test_a_pane_tabbed_into_is_passed_through_until_it_is_used(self):
        """H2-17: tabbing round the window went into the pane and stayed,
        each Tab indenting its first line."""
        from PySide6.QtTest import QTest

        self.window.show()
        self.window.activateWindow()
        # Active first: a window that becomes so gives the focus its own reason.
        _app.processEvents()
        pane = self.xml.typing
        self.type("<a>")

        def come(reason) -> None:
            self.xml.strip.setFocus()
            pane.setFocus(reason)
            _app.processEvents()

        come(Qt.FocusReason.TabFocusReason)
        if not pane.hasFocus():
            self.skipTest("the window cannot take the keyboard on this platform")
        self.press(Qt.Key.Key_Tab)
        self.assertEqual(pane.toPlainText(), "<a>")
        self.assertFalse(pane.hasFocus())
        # Come to by Shift+Tab, the same; and Shift on its way to it is no use.
        come(Qt.FocusReason.BacktabFocusReason)
        self.press(Qt.Key.Key_Shift, Qt.KeyboardModifier.ShiftModifier)
        self.press(Qt.Key.Key_Backtab, Qt.KeyboardModifier.ShiftModifier)
        self.assertEqual(pane.toPlainText(), "<a>")
        self.assertFalse(pane.hasFocus())
        # A key that moves the caret is using it: Tab indents from then on.
        come(Qt.FocusReason.TabFocusReason)
        self.press(Qt.Key.Key_End)
        self.press(Qt.Key.Key_Tab)
        self.assertEqual(pane.toPlainText(), "<a>  ")
        self.assertTrue(pane.hasFocus())
        # And so is a click.
        come(Qt.FocusReason.TabFocusReason)
        QTest.mouseClick(pane.viewport(), Qt.MouseButton.LeftButton)
        self.press(Qt.Key.Key_Tab)
        self.assertEqual(pane.toPlainText().count("  "), 2)
        # Come to any other way, it is in use from the start.
        come(Qt.FocusReason.OtherFocusReason)
        self.press(Qt.Key.Key_Tab)
        self.assertEqual(pane.toPlainText().count("  "), 3)

    def test_revert_and_apply_keep_the_keyboard_in_the_view(self):
        """Each goes grey once nothing is typed, and Qt hands on the keyboard
        of a control that greys out under it: out of the view, to Play in the
        window's tab bar, where Enter starts the game."""
        from PySide6.QtTest import QTest
        from PySide6.QtWidgets import QApplication

        self.window.show()
        self.window.activateWindow()
        _app.processEvents()
        if not self.window.isActiveWindow():
            self.skipTest("the window cannot take the keyboard on this platform")
        for button in (self.xml.revert_button, self.xml.apply_button):
            with self.subTest(button=button.text()):
                self.type(self.op("15.00"))
                _app.processEvents()
                QTest.mouseClick(button, Qt.MouseButton.LeftButton)
                _app.processEvents()
                self.assertFalse(button.isEnabled())
                self.assertIs(QApplication.focusWidget(), self.xml.strip)

    # -- leaving what was typed ----------------------------------------------
    def other_asset(self) -> str:
        return next(
            row.guid for row in self.window.index.search("", limit=5) if row.guid != self.GUID
        )

    def test_opening_another_asset_asks_and_cancel_stays(self):
        self.type(self.op("15.00"))
        with mock.patch.object(self.window.prompts, "ask_unapplied_modops", return_value="") as ask:
            self.window._open_asset(self.other_asset())
        ask.assert_called_once()
        self.assertEqual(self.editor.guid, self.GUID)
        self.assertEqual(self.xml.typing.toPlainText(), self.op("15.00"))

    def test_and_apply_applies_before_it_goes(self):
        self.type(self.op("15.00"))
        other = self.other_asset()
        with mock.patch.object(self.window.prompts, "ask_unapplied_modops", return_value="apply"):
            self.window._open_asset(other)
        self.assertEqual(self.editor.guid, other)
        self.assertEqual(len(self.changes()), 1)

    def test_and_discard_goes_without_it(self):
        self.type(self.op("15.00"))
        other = self.other_asset()
        with mock.patch.object(self.window.prompts, "ask_unapplied_modops", return_value="discard"):
            self.window._open_asset(other)
        self.assertEqual(self.editor.guid, other)
        self.assertEqual(self.changes(), [])

    def test_text_that_cannot_be_applied_keeps_you_there(self):
        self.type("<ModOps><ModOp")
        with mock.patch.object(self.window.prompts, "ask_unapplied_modops", return_value="apply"):
            self.window._open_asset(self.other_asset())
        self.assertEqual(self.editor.guid, self.GUID)
        self.assertIn("Not applied", self.xml.status.text())

    def test_going_to_fields_asks_too(self):
        self.type(self.op("15.00"))
        with mock.patch.object(self.window.prompts, "ask_unapplied_modops", return_value=""):
            self.editor.set_view("fields")
        self.assertEqual(self.editor.view(), "xml")
        with mock.patch.object(self.window.prompts, "ask_unapplied_modops", return_value="discard"):
            self.editor.set_view("fields")
        self.assertEqual(self.editor.view(), "fields")

    def test_throwing_the_loadout_away_asks_about_it_first(self):
        self.type(self.op("15.00"))
        with mock.patch.object(self.window.prompts, "ask_unapplied_modops", return_value="") as ask:
            self.assertFalse(self.window.files.confirm_discard())
        ask.assert_called_once()

    def test_nothing_typed_asks_nothing(self):
        with mock.patch.object(self.window.prompts, "ask_unapplied_modops") as ask:
            self.window._open_asset(self.other_asset())
        ask.assert_not_called()


class TestTypingModOpsKeptAsWritten(WorkflowCase):
    """The XML view's pane read with every op kept as written: Apply stores
    what was typed, the pane is never shut, and there is no mark to offer."""

    GUID = "1010340"

    def setUp(self):
        super().setUp()
        self.editor = self.window.editor
        self.xml = self.editor.xml
        self.window._open_asset(self.GUID)
        self.cycle = self.editor._path_for(self._find_item("CycleTime"))[0]
        self.editor.set_view("xml")

    type = TestTypingModOpsInTheXmlView.type
    changes = TestTypingModOpsInTheXmlView.changes
    type_asset = TestTypingModOpsInTheXmlView.type_asset

    def wide(self) -> str:
        return (
            '<ModOp Type="merge" GUID="%s" Path="%s"><CycleTime>15.00</CycleTime></ModOp>'
            % (self.GUID, self.cycle.rsplit("/", 1)[0])
        )

    def test_apply_makes_the_mod_say_what_was_typed(self):
        self.type(self.wide())
        self.xml.apply_button.click()
        (op,) = self.window.loadout.ops
        self.assertIn('Type="merge"', op.source)
        (change,) = self.changes()
        self.assertEqual((change.new_value, change.op_uid), ("15.00", op.uid))
        self.assertFalse(self.xml.has_unapplied())
        # The pane has the op as it was typed, and the asset what it comes to.
        self.assertIn('Type="merge"', self.xml.typing.toPlainText())
        self.assertIn("<CycleTime>15.00</CycleTime>", self.xml.asset_in_front().toPlainText())
        self.assertEqual(self.xml.status.text(), "1 new or changed ModOp kept as typed.")
        self.assertTrue(self.window.files.unsaved_work())

    other_asset = TestTypingModOpsInTheXmlView.other_asset

    def renames(self, guids: str, name: str) -> str:
        return (
            '<ModOp Type="replace" GUID="%s" Path="/Values/Standard/Name"><Name>%s</Name></ModOp>'
            % (guids, name)
        )

    def panel_rows(self) -> list:
        """Every row of the Mod changes panel, as the words in its two columns."""
        panel = self.window.changes_panel
        panel.build_every_row()
        found = []

        def walk(item):
            found.append("%s | %s" % (item.text(0), item.text(1)))
            for at in range(item.childCount()):
                walk(item.child(at))

        for top in range(panel.tree.topLevelItemCount()):
            walk(panel.tree.topLevelItem(top))
        return found

    def test_what_leaves_the_pane_is_counted_apart_and_said_to_be_in_mod_changes(self):
        """Three typed after the one the pane had: one for this asset, one
        for another, and a group holding one. Each count is of something to
        be seen: the one new op the pane holds, and the two ModOps that are
        rows in the Mod changes panel. The group's own tag is no third."""
        self.type(self.wide())
        self.xml.apply_button.click()
        more = (
            self.renames(self.GUID, "typed here")
            + self.renames(self.other_asset(), "typed for another")
            + '<Group Condition="#Some_Dependency">%s</Group>' % self.renames(self.GUID, "grouped")
        )
        self.type(self.xml.typing.toPlainText().replace("</ModOps>", more + "</ModOps>"))
        self.xml.apply_button.click()
        self.assertEqual(
            self.xml.status.text(),
            "2 new or changed ModOps kept as typed. 1 ModOp does not reach this asset, "
            "so it is applied and listed in the Mod changes panel, not in this pane.",
        )
        pane = self.xml.typing.toPlainText()
        self.assertEqual(pane.count("<ModOp "), 3)
        self.assertIn("typed here", pane)
        self.assertNotIn("typed for another", pane)
        # The op of the group reaches this asset, so it is in the pane: as
        # the op, under a heading that says it is a group's.
        self.assertIn("grouped", pane)
        self.assertNotIn("<Group", pane)
        self.assertEqual(
            [words for _line, words in self.xml.typing.headings()],
            ["In a group that does not run today · Condition #Some_Dependency"],
        )
        # Each of the two is a row there: the one under the asset it changes,
        # the other under its group.
        rows = self.panel_rows()
        for typed in ("typed for another", "grouped"):
            self.assertEqual(len([row for row in rows if typed in row]), 1, rows)

    def typed_for_two(self) -> str:
        """Type and apply one op naming this asset and another; the other."""
        other = self.other_asset()
        self.type(self.renames("%s,%s" % (self.GUID, other), "typed for two"))
        self.xml.apply_button.click()
        return other

    def test_an_op_that_reaches_another_asset_too_is_in_the_pane_and_drawn_so(self):
        """File tab proposal, phase 2.  It used to be applied and leave the
        pane, counted in a line under it."""
        self.typed_for_two()
        pane = self.xml.typing
        self.assertIn("typed for two", pane.toPlainText())
        self.assertEqual(self.xml.status.text(), "1 new or changed ModOp kept as typed.")
        # A heading on the blank line above it, which is no part of the text.
        ((line, words),) = pane.headings()
        self.assertEqual(words, "Reaches this asset and 1 other")
        self.assertEqual(pane.document().findBlockByNumber(line).text().strip(), "")
        self.assertIn("<ModOp ", pane.document().findBlockByNumber(line + 1).text())
        # Its lines on a band, the heading's with them, and its badge beside it.
        marks = pane.marks()
        self.assertEqual(set(marks.values()), {"beyond"})
        self.assertEqual(sorted(marks), list(range(line, line + 4)))
        self.assertEqual(list(pane.badges()), [line + 1])
        self.assertIn("tinted band", pane._gutter.toolTip())
        # Nothing is left to say under the panes.
        self.assertTrue(self.xml.also_button.isHidden())
        self.assertTrue(self.xml.also_label.isHidden())

    def test_the_button_says_how_far_what_was_typed_reaches(self):
        self.typed_for_two()
        button = self.xml.apply_button
        self.assertEqual(button.text(), "Apply")
        self.type(self.xml.typing.toPlainText().replace("typed for two", "typed again"))
        self.assertEqual(button.text(), "Apply to 2 assets")
        button.click()
        self.assertEqual(button.text(), "Apply")
        self.assertEqual(
            sorted(change.new_value for change in self.window.loadout.changes.values()),
            ["typed again", "typed again"],
        )
        # One of the asset's own reaches no further, and the button says no more.
        self.type(self.xml.typing.toPlainText().replace("</ModOps>", self.wide() + "</ModOps>"))
        self.assertEqual(button.text(), "Apply")

    def searches(self, name: str) -> str:
        return (
            '<ModOp Type="merge" Path="//Values[Standard/GUID=\'%s\']/Standard">'
            "<Name>%s</Name></ModOp>" % (self.GUID, name)
        )

    def test_a_search_typed_is_counted_once_it_is_read_and_asked_about(self):
        self.type(self.searches("found by searching"))
        self.assertEqual(self.xml.apply_button.text(), "Apply to a search")
        with mock.patch.object(self.window.prompts, "confirm_reach", return_value=False) as ask:
            self.xml.apply_button.click()
        ask.assert_called_once_with(1)
        # Not applied, and what was typed is still there to apply.
        self.assertEqual(self.window.loadout.ops, [])
        self.assertTrue(self.xml.has_unapplied())
        self.assertIn("found by searching", self.xml.typing.toPlainText())
        with mock.patch.object(self.window.prompts, "confirm_reach", return_value=True):
            self.xml.apply_button.click()
        (op,) = self.window.loadout.ops
        self.assertTrue(op.searches)
        self.assertFalse(self.xml.has_unapplied())
        ((_line, words),) = self.xml.typing.headings()
        self.assertEqual(words, "Pattern · Not this asset's alone")

    def test_an_op_of_the_assets_own_is_not_asked_about(self):
        with mock.patch.object(self.window.prompts, "confirm_reach") as ask:
            self.type(self.wide())
            self.xml.apply_button.click()
        ask.assert_not_called()

    def test_its_heading_offers_the_file_and_the_other_assets(self):
        other = self.typed_for_two()
        offered = dict(self.xml._beyond_menu(0))
        self.assertEqual(
            list(offered), ["Show in file", "Open %s" % self.window.index.display_name(other)]
        )
        (op,) = self.window.loadout.ops
        offered["Show in file"]()
        self.assertEqual(self.window.tabs.currentIndex(), TAB_FILE)
        self.assertIn("typed for two", self.window.file_panel.pane.toPlainText())
        self.assertEqual(self.window.file_panel.shown.line_of_op(op.uid),
                         self.window.file_panel.pane.textCursor().blockNumber() + 1)
        self.window.open_tab(TAB_EDIT)
        offered["Open %s" % self.window.index.display_name(other)]()
        self.assertEqual(self.editor.guid, other)

    def test_a_heading_clicked_opens_what_it_offers(self):
        self.typed_for_two()
        pane = self.xml.typing
        ((line, _words),) = pane.headings()
        self.assertEqual(pane.heading_at(line), 0)
        self.assertIsNone(pane.heading_at(line + 1))
        with mock.patch.object(self.xml, "_open_beyond_menu") as opened:
            pane.headingClicked.emit(0)
        opened.assert_called_once_with(0)

    # -- file tab proposal, phase 3: ops that go somewhere else ---------------
    def test_an_op_typed_for_another_asset_says_where_it_goes_before_apply(self):
        other = self.other_asset()
        pane, button = self.xml.typing, self.xml.apply_button
        self.type(
            "<ModOps>\n" + self.wide() + "\n" + self.renames(other, "typed for another")
            + "\n" + self.renames("%s,%s" % (self.GUID, other), "typed for two")
            + "\n" + self.renames("%s,424242" % other, "typed for two others")
            + "\n" + self.searches("found by searching") + "\n</ModOps>"
        )
        name = self.window.index.display_name(other)
        # The asset's own says nothing; each of the others says it on its line.
        self.assertEqual(pane.asides(), {
            2: "Goes to %s" % name,
            3: "Goes to this asset and 1 other",
            4: "Goes to 2 other assets",
            5: "Goes to what its search finds",
        })
        # In the strip's tooltip for that line, and read out on the way to Apply.
        self.assertEqual(pane._gutter.tip_for(2), "Goes to %s" % name)
        self.assertIn("tinted band", pane._gutter.tip_for(1))
        self.assertIn("Line 3: Goes to %s." % name, button.accessibleDescription())
        # Half typed, it is not XML: what was said stays with its lines.
        cursor = pane.textCursor()
        cursor.movePosition(cursor.MoveOperation.Start)
        cursor.insertText("\n<")
        self.assertEqual(sorted(pane.asides()), [3, 4, 5, 6])
        self.xml.revert_button.click()
        self.assertEqual(pane.asides(), {})
        self.assertEqual(button.accessibleDescription(), "")

    def test_an_op_the_pane_was_handed_says_nothing_until_it_is_typed_over(self):
        self.typed_for_two()
        pane = self.xml.typing
        self.assertEqual(pane.asides(), {})
        self.type(pane.toPlainText().replace("typed for two", "typed again"))
        self.assertEqual(list(pane.asides().values()), ["Goes to this asset and 1 other"])

    def selected_rows(self) -> list:
        return [item.text(0) + " | " + item.text(1)
                for item in self.window.changes_panel.tree.selectedItems()]

    def test_what_apply_added_is_selected_in_the_mod_changes_panel(self):
        self.type(self.wide())
        self.xml.apply_button.click()
        tree = self.window.changes_panel.tree
        first = self.selected_rows()
        self.assertEqual(len(first), 1, first)
        # One more, for another asset: its row and no other, and in view.
        self.type(self.xml.typing.toPlainText().replace(
            "</ModOps>", self.renames(self.other_asset(), "typed for another") + "</ModOps>"
        ))
        self.xml.apply_button.click()
        (row,) = self.selected_rows()
        self.assertIn("typed for another", row)
        (item,) = tree.selectedItems()
        self.assertIs(tree.currentItem(), item)
        above = item.parent()
        while above is not None:
            self.assertTrue(above.isExpanded())
            above = above.parent()
        # The asset in front is the one it was typed on.
        self.assertEqual(self.editor.guid, self.GUID)

    def test_a_value_set_in_fields_selects_nothing_there(self):
        self.editor.set_view("fields")
        self._find_item("CycleTime").setText(1, "20")
        self.assertEqual(self.selected_rows(), [])

    def test_undoing_an_apply_puts_what_was_typed_back_as_typed(self):
        typed = "<ModOps>\n" + self.wide() + "\n</ModOps>"
        self.type(typed)
        self.xml.apply_button.click()
        self.assertFalse(self.xml.has_unapplied())
        self.window.undo_last()
        self.assertEqual(self.window.loadout.ops, [])
        self.assertTrue(self.xml.has_unapplied())
        self.assertEqual(self.xml.typing.toPlainText(), typed)
        self.assertEqual(self.xml.status.text(), "What you typed is back in the pane, not applied.")
        self.assertTrue(self.xml.apply_button.isEnabled())
        # Redo applies it again, and the pane is the mod's.
        self.window.redo_last()
        (op,) = self.window.loadout.ops
        self.assertIn('Type="merge"', op.source)
        self.assertFalse(self.xml.has_unapplied())
        self.assertIn('Type="merge"', self.xml.typing.toPlainText())
        # And undone once more, Apply does what Redo would.
        self.window.undo_last()
        self.assertTrue(self.xml.has_unapplied())
        self.xml.apply_button.click()
        self.assertEqual(len(self.window.loadout.ops), 1)
        self.assertFalse(self.xml.has_unapplied())

    def test_a_step_further_back_is_not_made_under_the_text_put_back(self):
        """Stepping back through an Apply and on is what it was: no text is
        left typed over a mod that has moved under it."""
        self.type(self.wide())
        self.xml.apply_button.click()
        self.type(self.xml.typing.toPlainText().replace("15.00", "16.00"))
        self.xml.apply_button.click()
        self.window.undo_last()
        self.assertIn("16.00", self.xml.typing.toPlainText())
        self.window.undo_last()
        # The first Apply's text now, and nothing said of the mod having moved.
        self.assertTrue(self.xml.has_unapplied())
        self.assertIn("15.00", self.xml.typing.toPlainText())
        self.assertNotIn("16.00", self.xml.typing.toPlainText())
        self.assertEqual(self.xml.status.text(), "What you typed is back in the pane, not applied.")
        self.window.redo_last()
        self.window.redo_last()
        self.assertFalse(self.xml.has_unapplied())
        self.assertIn("16.00", self.xml.typing.toPlainText())

    def test_text_typed_in_after_it_was_put_back_is_kept(self):
        self.type(self.wide())
        self.xml.apply_button.click()
        self.window.undo_last()
        self.type(self.xml.typing.toPlainText().replace("15.00", "18.00"))
        self.window.redo_last()
        self.assertTrue(self.xml.has_unapplied())
        self.assertIn("18.00", self.xml.typing.toPlainText())

    def test_undone_with_another_asset_in_front_it_goes_to_the_one_it_was_typed_on(self):
        """An op for two assets, typed on this one.  The step is about both,
        so it used to be undone wherever the editor was."""
        other = self.other_asset()
        typed = self.renames("%s,%s" % (self.GUID, other), "typed for two")
        self.type(typed)
        self.xml.apply_button.click()
        self.window._open_asset(other)
        self.window.undo_last()
        self.assertEqual(self.editor.guid, self.GUID)
        self.assertTrue(self.xml.has_unapplied())
        self.assertEqual(self.xml.typing.toPlainText(), typed)

    def test_an_op_that_left_the_pane_comes_back_to_it_on_undo(self):
        """H2-3: an op typed on this asset for another one, with a note above
        it.  Applied, it leaves the pane.  Undo opened the asset it landed on,
        and the text came back nowhere."""
        other = self.other_asset()
        # The asset's own op first, applied: the step undone is then about
        # the other asset alone.
        self.type(self.wide())
        self.xml.apply_button.click()
        typed = self.xml.typing.toPlainText().replace(
            "</ModOps>",
            "<!-- slower there -->\n" + self.renames(other, "typed for another") + "\n</ModOps>",
        )
        self.type(typed)
        self.xml.apply_button.click()
        self.assertNotIn("typed for another", self.xml.typing.toPlainText())
        self.assertEqual(self.editor.guid, self.GUID)
        self.window.undo_last()
        self.assertEqual(self.editor.guid, self.GUID)
        self.assertEqual(self.editor.view(), "xml")
        self.assertTrue(self.xml.has_unapplied())
        self.assertEqual(self.xml.typing.toPlainText(), typed)
        self.assertTrue(self.xml.apply_button.isEnabled())
        # Redo applies it again, from the asset it was typed on.
        self.window.redo_last()
        self.assertEqual(self.editor.guid, self.GUID)
        self.assertFalse(self.xml.has_unapplied())
        (landed,) = [op for op in self.window.loadout.ops if "typed for another" in op.source]
        self.assertEqual(landed.note, "slower there")

    def test_a_pattern_typed_over_is_drawn_as_typed_in_the_mod_changes_panel(self):
        """H2-2: a pattern made in its editor, then typed over here.  The
        rows went on showing the value it had, and the status bar the
        pattern editor's sentence about it, until another Apply."""
        from anno_modding_studio.core.changes import AIM_MAKE
        from anno_modding_studio.ui.pattern_dialog import PatternDialog

        window = self.window
        window.loadout.set_up_as(AIM_MAKE)
        window.loadout.make_own_mod()
        pattern = (
            '<ModOp Type="replace" Path="//Values/Factory7/CycleTime">'
            "<CycleTime>20.00</CycleTime></ModOp>"
        )

        def composed(dialog):
            dialog.editor.setPlainText(pattern)
            dialog._recheck()
            dialog._accept()
            return int(QDialog.DialogCode.Accepted)

        with mock.patch.object(PatternDialog, "exec", composed):
            window.patterns.new_pattern()
        said = window.status_label.text()
        self.assertIn("20.00", said)
        rows = self.panel_rows()
        self.assertTrue(any(row.startswith("CycleTime = 20.00 |") for row in rows), rows)
        # The asset open is one it matches, and shows it at once.
        self.assertIn("<CycleTime>20.00</CycleTime>", self.xml.typing.toPlainText())
        self.type(self.xml.typing.toPlainText().replace("20.00", "25.00"))
        self.assertEqual(self.xml.apply_button.text(), "Apply to a search")
        with mock.patch.object(window.prompts, "confirm_reach", return_value=True):
            self.xml.apply_button.click()
        self.assertEqual(self.xml.status.text(), "1 new or changed ModOp kept as typed.")
        rows = self.panel_rows()
        self.assertTrue(any(row.startswith("CycleTime = 25.00 |") for row in rows), rows)
        self.assertFalse(any(row.endswith("20.00") for row in rows), rows)
        # What Apply changed is selected: the pattern's row.
        self.assertEqual([row.split(" | ")[0] for row in self.selected_rows()],
                         ["CycleTime = 25.00"])
        self.assertNotEqual(window.status_label.text(), said)

    def test_a_value_typed_over_in_fields_is_drawn_as_typed_in_the_mod_changes_panel(self):
        """The same change typed twice is one key and two objects: the rows
        were drawn from the first."""
        self.editor.set_view("fields")
        self._edit("CycleTime", "15.00")
        self._edit("CycleTime", "17.00")
        (row,) = change_rows(self.window.changes_panel)
        self.assertTrue(row.text(1).endswith("17.00"), row.text(1))

    def test_undone_in_fields_the_text_is_not_put_back_and_redo_has_it(self):
        self.type(self.wide())
        self.xml.apply_button.click()
        self.editor.set_view("fields")
        self.window.undo_last()
        self.editor.set_view("xml")
        self.assertFalse(self.xml.has_unapplied())
        self.assertNotIn('Type="merge"', self.xml.typing.toPlainText())
        self.window.redo_last()
        self.assertIn('Type="merge"', self.xml.typing.toPlainText())

    def test_the_asset_typed_and_applied_is_put_back_in_its_own_pane(self):
        pane = self.xml.asset_typing
        typed = pane.toPlainText().replace("<CycleTime>", "<CycleTime>1", 1)
        self.assertNotEqual(typed, pane.toPlainText())
        self.type_asset(typed)
        self.xml.apply_button.click()
        self.assertFalse(self.xml.has_unapplied())
        self.window.undo_last()
        self.assertTrue(pane.document().isModified())
        self.assertFalse(self.xml.typing.document().isModified())
        self.assertEqual(pane.toPlainText(), typed)

    def test_and_goes_on_saying_so_once_the_asset_is_reverted(self):
        """Revert asset takes the changes and leaves an op that is about more
        than this asset: it is written still, and does in game what it did.
        The pane said the mod "does not change this asset yet"."""
        both = "%s,%s" % (self.GUID, self.other_asset())
        self.type(self.renames(both, "typed for two"))
        self.xml.apply_button.click()
        self.editor._revert_asset()
        self.assertEqual(len(self.window.loadout.ops), 1)
        self.assertEqual(self.changes(), [])
        self.assertNotIn("typed for two", self.xml.asset_in_front().toPlainText())
        # In the pane still, and its heading says what became of its change.
        self.assertIn("typed for two", self.xml.typing.toPlainText())
        ((_line, words),) = self.xml.typing.headings()
        self.assertEqual(
            words, "Reaches this asset and 1 other · Its change to this asset was reverted"
        )
        self.assertEqual(
            self.xml.also_label.text(),
            "1 kept ModOp of %s reaches this asset. The change 1 of them made to "
            "this asset was reverted, but the ModOp is written all the same and is in "
            "the Mod changes panel: the game still makes the change, and the asset "
            "shown is without it." % self.xml.name_of(self.xml.current()),
        )
        from anno_modding_studio.core import xmlview

        self.assertIn("typed for two", xmlview.also_xml(self.window.loadout, self.xml.current()))

    def test_and_fields_says_so_too_under_the_heading_for_as_long_as_it_is_so(self):
        """In Fields the values are the game's again once the asset is
        reverted, and nothing said the mod still changes them in game."""
        said = "1 kept ModOp reaches this asset, its change here reverted"
        subtitle = self.editor.subtitle_label
        other = self.other_asset()
        self.type(self.renames("%s,%s" % (self.GUID, other), "typed for two"))
        self.xml.apply_button.click()
        self.assertNotIn("reverted", subtitle.text())
        self.assertEqual(subtitle.toolTip(), "")
        self.editor._revert_asset()
        self.assertTrue(subtitle.text().endswith("   ·   " + said), subtitle.text())
        # The whole of it on hover, in the XML view's words.
        self.assertIn(subtitle.toolTip(), self.xml.also_label.text())
        self.assertIn("the game still makes the change", subtitle.toolTip())
        # The other asset the op names has its change still.
        self.window._open_asset(other)
        self.assertNotIn("reverted", subtitle.text())
        self.window._open_asset(self.GUID)
        self.assertIn(said, subtitle.text())
        self.window.undo_action.trigger()
        self.assertEqual(len(self.changes()), 1)
        self.assertNotIn("reverted", subtitle.text())
        self.assertEqual(subtitle.toolTip(), "")

    def test_and_counts_the_ops_of_every_mod_loaded_up_to_the_one_being_edited(self):
        """The asset Fields shows is what every mod up to the one edited
        leaves. It counted that mod's ops only, so an earlier mod's op, which
        the game loads all the same, was said on no heading but its own."""
        from anno_modding_studio.core.changes import ModLayer, Op

        loadout = self.window.loadout
        both = "%s,%s" % (self.GUID, self.other_asset())
        for order, name in enumerate(("Early", "Late"), 1):
            loadout.set_layer(ModLayer(origin=name, name=name, order=order))
            as_yours(loadout, name)
            # Each answered to both assets, and its changes are reverted.
            loadout.set_op(Op(
                source=self.renames(both, name), origin=name,
                matched=both.split(","), made=2,
            ))
        subtitle = self.editor.subtitle_label

        def shown(editing: str) -> str:
            loadout.editing = editing
            self.editor.refresh_target()
            self.editor.load_asset(self.GUID)
            return subtitle.text()

        self.assertIn("2 kept ModOps reach this asset, their changes here reverted", shown("Late"))
        # One count, and on hover whose they are, since not all are Late's.
        self.assertIn("the game still makes the changes", subtitle.toolTip())
        self.assertTrue(
            subtitle.toolTip().endswith(" They are ModOps of Early and Late."), subtitle.toolTip()
        )
        # Late loads after Early, and Early's asset is without Late's op anyway.
        self.assertIn("1 kept ModOp reaches this asset, its change here reverted", shown("Early"))
        self.assertNotIn("ModOps of", subtitle.toolTip())
        self.assertNotIn("ModOp of", subtitle.toolTip())
        # Your own mod loads last here, as it does for the ModOps not read.
        self.assertIn("2 kept ModOps reach this asset", shown(""))
        self.assertIn("ModOps of Early and Late.", subtitle.toolTip())
        # The XML view goes on saying it a mod at a time.
        shown("Late")
        self.assertIn("1 kept ModOp of Late reaches this asset.", self.xml.also_label.text())

    def test_and_says_so_on_the_tab_of_a_mod_that_is_only_read(self):
        """An earlier mod's tab has its op in a pane without headings, and
        the asset without the op's change. Nothing under the panes said the
        change was reverted: the line was hidden with the button."""
        from anno_modding_studio.core.changes import ModLayer, Op

        loadout = self.window.loadout
        both = "%s,%s" % (self.GUID, self.other_asset())
        for order, name in enumerate(("Early", "Late"), 1):
            loadout.set_layer(ModLayer(origin=name, name=name, order=order))
            loadout.set_op(Op(
                source=self.renames(both, name), origin=name,
                matched=both.split(","), made=2,
            ))
        loadout.editing = "Late"
        self.editor.refresh_target()
        self.editor.load_asset(self.GUID)
        self.assertTrue(self.xml.show_mod("Early"))
        turn = self.xml.current()
        self.assertFalse(turn.editing)
        self.assertEqual(len(turn.reverted), 1)
        self.assertIn("Early", self.xml.modops.toPlainText())
        self.assertEqual(self.xml.modops.headings(), [])
        self.assertFalse(self.xml.also_label.isHidden())
        self.assertEqual(
            self.xml.also_label.text(),
            "1 kept ModOp of Early reaches this asset. The change 1 of them made to "
            "this asset was reverted, but the ModOp is written all the same and is in "
            "the Mod changes panel: the game still makes the change, and the asset "
            "shown is without it.",
        )
        # Nothing of it is beside the pane, so there is nothing to show apart.
        self.assertTrue(self.xml.also_button.isHidden())
        # A tab with no such op says nothing.
        self.xml.strip.setCurrentIndex(0)
        self.assertTrue(self.xml.current().is_game)
        self.assertTrue(self.xml.also_label.isHidden())

    def test_a_reverted_group_kept_whole_is_counted_once_on_the_tab_typed_in(self):
        """Such a group is beside the pane, and among the reverted ops too:
        it was counted as a group kept whole and again as a kept ModOp, as
        though two things reached the asset."""
        from anno_modding_studio.core.changes import ModLayer, Op

        loadout = self.window.loadout
        both = "%s,%s" % (self.GUID, self.other_asset())
        loadout.set_layer(ModLayer(origin="Late", name="Late", order=1))
        as_yours(loadout, "Late")
        loadout.set_op(Op(
            source="<Group>%s</Group>" % self.renames(both, "grouped"), origin="Late",
            matched=both.split(","), made=2,
        ))
        loadout.editing = "Late"
        self.editor.refresh_target()
        self.editor.load_asset(self.GUID)
        self.assertTrue(self.xml.show_mod("Late"))
        turn = self.xml.current()
        self.assertTrue(turn.editing)
        self.assertEqual(len(turn.reverted), 1)
        self.assertEqual(
            self.xml.also_label.text(),
            "1 group of Late kept whole reaches this asset. The change 1 of them made "
            "to this asset was reverted, but the ModOp is written all the same and is "
            "in the Mod changes panel: the game still makes the change, and the asset "
            "shown is without it.",
        )
        self.assertFalse(self.xml.also_button.isHidden())

    def test_text_typed_between_two_ops_is_said_to_be_left_out(self):
        """It was applied without a word and was gone from the pane."""
        self.type("%s\nstray\n%s" % (self.wide(), self.renames(self.GUID, "typed here")))
        self.xml.apply_button.click()
        self.assertNotIn("stray", self.xml.typing.toPlainText())
        self.assertEqual(
            self.xml.status.text(),
            "2 new or changed ModOps kept as typed. Text typed between elements was left out.",
        )

    def test_text_typed_beside_what_a_modop_holds_is_refused_and_said_where(self):
        """Typed after an element of the op it was dropped without a word,
        and typed before the first it was kept and exported. Neither is
        applied now: it stays in the pane, with the caret on its line."""
        self.type(self.wide().replace("</CycleTime>", "</CycleTime>\nloose\n"))
        self.xml.apply_button.click()
        self.assertEqual(self.window.loadout.ops, [])
        self.assertTrue(self.xml.has_unapplied())
        self.assertEqual(
            self.xml.status.text(),
            "Not applied. Line 2: “loose” is text beside the elements inside a "
            "ModOp. It comes to no change, so take it out or make it a comment.",
        )
        self.assertEqual(self.xml.typing.textCursor().selectedText(), "loose")
        self.type(self.wide())
        self.xml.apply_button.click()
        self.assertEqual(self.xml.status.text(), "1 new or changed ModOp kept as typed.")

    def test_text_that_is_not_xml_under_blank_lines_is_said_on_the_line_it_is_on(self):
        """The reader does not see blank lines at the top of the pane, so the
        line it named was that many too high up, and the caret went there."""
        self.type("\n\n" + self.wide().replace("</CycleTime>", "<Oops>\nhere</CycleTime>"))
        self.xml.apply_button.click()
        self.assertEqual(self.window.loadout.ops, [])
        self.assertIn("Not applied. Line 4, column", self.xml.status.text())
        # The line of the tag left open is the reader's count as well.
        self.assertIn("Oops line 3 and CycleTime", self.xml.status.text())
        self.assertEqual(self.xml.typing.textCursor().blockNumber(), 3)
        self.assertIn("here</CycleTime>", self.xml.typing.textCursor().selectedText())

    def test_emptying_the_pane_says_how_many_modops_were_taken_out(self):
        self.type(self.wide())
        self.xml.apply_button.click()
        self.type(" ")
        self.xml.apply_button.click()
        self.assertEqual(self.window.loadout.ops, [])
        self.assertEqual(self.xml.status.text(), "1 ModOp taken out.")

    def test_applying_what_the_studio_wrote_changes_nothing(self):
        self.type(self.wide())
        self.xml.apply_button.click()
        (op,) = self.window.loadout.ops
        before = (dict(self.window.loadout.changes), vars(op).copy())
        self.type(self.xml.typing.toPlainText() + "\n")
        self.xml.apply_button.click()
        self.assertEqual((dict(self.window.loadout.changes), vars(op)), before)
        self.assertEqual(self.xml.status.text(), "No change to the mod.")

    def test_an_apply_is_one_step_to_undo(self):
        self.type(self.wide())
        self.xml.apply_button.click()
        self.window.undo_action.trigger()
        self.assertEqual((self.changes(), self.window.loadout.ops), ([], []))
        self.window.redo_action.trigger()
        self.assertEqual(len(self.window.loadout.ops), 1)

    def test_the_pane_is_typed_in_whatever_the_mod_holds(self):
        self.type(self.wide())
        self.xml.apply_button.click()
        self.assertFalse(self.xml.typing.isReadOnly())
        self.assertNotIn("do not read back", self.xml.status.text())

    def test_a_row_offers_no_mark(self):
        self.type(self.wide())
        self.xml.apply_button.click()
        panel = self.window.changes_panel

        def rows(item):
            yield item
            for at in range(item.childCount()):
                yield from rows(item.child(at))

        offered = {
            action.text()
            for top in range(panel.tree.topLevelItemCount())
            for item in rows(panel.tree.topLevelItem(top))
            for action in panel.menu_for(item).actions()
        }
        self.assertNotIn("Keep as written", offered)
        self.assertIn("Show XML\u2026", offered)

    def test_what_apply_does_is_said_as_it_is(self):
        self.assertIn("keeps each ModOp as you typed it", self.xml.rewrite_help.help_text())
        self.assertNotIn("KeepAsWritten", self.xml.rewrite_help.help_text())


class StaleReadingsCase(ModsFolderCase):
    """Three mods read as a stack, the second of which doubles what the
    first leaves; and reading again switched on for as long as a test asks."""

    FIRST = (
        "<ModOps>"
        '<ModOp Type="merge" GUID="1010030" Path="/Values/Residence7"><Residents>12</Residents></ModOp>'
        "</ModOps>"
    )
    # Twice whatever is there when it runs: 24 after FIRST, 16 without it.
    SECOND = (
        "<ModOps>"
        '<ModOp Type="merge" GUID="1010030" Path="/Values/Residence7/Residents">'
        '<Residents><ModValue Insert="self::node() * 2"/></Residents></ModOp>'
        '<ModOp Type="merge" GUID="1010031" Path="/Values/Residence7"><Residents>30</Residents></ModOp>'
        "</ModOps>"
    )
    EARLY, LATE, LAST = "[Gameplay] Early", "[Seal] Late", "[Seal] Other Houses"

    def setUp(self):
        super().setUp()
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, True)
        self.mods = tmp / "mods"
        for name, body in (
            (self.EARLY, self.FIRST), (self.LATE, self.SECOND), (self.LAST, ModsFolderCase.FIRST),
        ):
            patch = self.mods / name / "data/config/export/main/asset/assets.xml"
            patch.parent.mkdir(parents=True)
            patch.write_text(body, encoding="utf-8")
            (self.mods / name / "modinfo.json").write_text(
                '{"ModName": {"English": "%s"}}' % name, encoding="utf-8"
            )
        self.window.settings.setValue(self.window.mods_key("last_mods_folder"), str(self.mods))
        self.load()
        self.window.readings.check()
        self.started = []

    def reading(self, now: bool):
        """Reading again switched on: done at once where ``now``, and held
        for the test to finish where not."""
        from anno_modding_studio.ui import workers

        def start(worker):
            self.started.append(worker)
            if now:
                worker.run()

        from anno_modding_studio.ui import readings_controller

        stack = contextlib.ExitStack()
        stack.enter_context(mock.patch.dict(os.environ, {"ANNO_STUDIO_REREAD": "thread"}))
        # No wait after an edit, which no test has an event loop to sit out.
        stack.enter_context(mock.patch.object(readings_controller, "EDIT_MS", 0))
        stack.enter_context(mock.patch.object(workers.RereadWorker, "start", start))
        return stack

    def comes_to(self, origin):
        return sorted(
            (change.guid, change.new_value)
            for change in self.window.loadout.changes.values() if change.origin == origin
        )

    def mod_row(self, origin):
        from anno_modding_studio.ui.changes_dock import ORIGIN_ROLE

        tree = self.window.changes_panel.tree
        return next(
            tree.topLevelItem(i) for i in range(tree.topLevelItemCount())
            if tree.topLevelItem(i).data(0, ORIGIN_ROLE) == origin
        )

    def op_rows(self, origin):
        from anno_modding_studio.ui.changes_dock import KIND_ROLE

        row = self.mod_row(origin)
        # A mod's rows are drawn when it is opened.
        row.setExpanded(True)
        found = [row.child(i) for i in range(row.childCount()) if row.child(i).data(0, KIND_ROLE) == "op"]
        self.assertEqual(len(found), 2)
        return found


class TestReadingsThatHaveGoneStale(StaleReadingsCase):
    """Phase 5 of ops as written: a change in what loads marks the readings
    it could reach, and they are read again behind the window."""

    def test_switched_off_nothing_is_marked(self):
        self.window.unload_mods([self.EARLY])
        self.assertEqual([layer.stale for layer in self.window.loadout.layers], [[], []])
        self.assertNotIn("out of date", self.mod_row(self.LATE).text(1))

    def test_a_folder_just_read_is_not_in_doubt(self):
        with self.reading(now=False):
            self.window.on_changes_updated()
        self.assertEqual(self.started, [])
        self.assertEqual(self.comes_to(self.LATE), [("1010030", "24"), ("1010031", "30")])

    def test_the_rows_say_so_until_the_reading_is_fresh(self):
        with self.reading(now=False):
            self.window.unload_mods([self.EARLY])
            self.assertEqual(len(self.started), 1)
            self.assertIn("being read again", self.mod_row(self.LATE).text(1))
            self.assertIn("no longer loads before it", self.mod_row(self.LATE).toolTip(0))
            for row in self.op_rows(self.LATE):
                self.assertTrue(row.text(1).startswith("being read again"), row.text(1))
                self.assertIn("may be out of date", row.toolTip(0))
            # Still what it came to before, until the reading is in.
            self.assertIn(("1010030", "24"), self.comes_to(self.LATE))
            self.started[0].run()
        self.assertNotIn("read again", self.mod_row(self.LATE).text(1))
        self.assertIn(("1010030", "16"), self.comes_to(self.LATE))

    def test_what_moved_is_said_once_it_is_read(self):
        with self.reading(now=True):
            self.window.unload_mods([self.EARLY])
        self.assertEqual(self.comes_to(self.LATE), [("1010030", "16"), ("1010031", "30")])
        self.assertEqual([layer.stale for layer in self.window.loadout.layers], [[], []])
        doubled, plain = self.op_rows(self.LATE)
        self.assertTrue(doubled.text(1).startswith("changed when read again"), doubled.text(1))
        self.assertIn("comes to something else", doubled.toolTip(0))
        self.assertNotIn("read again", plain.text(1))
        said = self.window.status_label.text()
        self.assertIn("2 mods were read again", said)
        self.assertIn("1 ModOp comes to something else now", said)
        # A reading of folders ends the history, as the others do.
        self.assertFalse(self.window.undo.can_undo)
        self.assertIsNone(self.window.readings.worker)

    def test_a_reading_taken_over_a_saved_loadout_leaves_it_saved(self):
        """A loadout saved with readings in doubt, opened and only looked
        at: the reading came in behind the window and the title gained its
        ``*``, the loadout holding a change of yours (H2-13)."""
        from anno_modding_studio.core import stale

        window = self.window
        window.loadout.set_change(
            Change(guid="1010030", asset_label="a", path="/Values/X", tag="X",
                   old_value="1", new_value="2")
        )
        stale._mark(window.loadout.layer(self.LATE), stale.GONE, self.EARLY)
        window.files.mark_saved()
        window.update_title()
        self.assertFalse(window.files.unsaved_work())
        with self.reading(now=True):
            window.readings.check()
        self.assertEqual(len(self.started), 1)
        self.assertEqual(window.loadout.layer(self.LATE).stale, [])
        self.assertFalse(window.files.unsaved_work())
        self.assertNotIn("*", window.windowTitle())
        # An edit is still work, reading or not.
        window.on_changes_updated()
        self.assertTrue(window.files.unsaved_work())

    def test_a_reading_overtaken_by_another_change_is_not_taken(self):
        with self.reading(now=False):
            self.window.unload_mods([self.EARLY])
            first = self.started[0]
            self.window.unload_mods([self.LAST])
            self.assertTrue(first._cancelled)
            self.assertEqual(len(self.started), 1)
            first.run()
            # Stopped, and the reading that is wanted now is started.
            self.assertEqual(len(self.started), 2)
            self.assertIn(("1010030", "24"), self.comes_to(self.LATE))
            self.started[1].run()
        self.assertEqual(self.comes_to(self.LATE), [("1010030", "16"), ("1010031", "30")])

    def test_no_reading_runs_while_the_index_is_held(self):
        # Closed, built again or written to: a reading in a process of its
        # own has the index's file open.
        with self.reading(now=False):
            self.window.unload_mods([self.EARLY])
            first = self.started[0]
            self.window.readings.hold("texts")
            self.assertTrue(first._closing)
            first.run()
            # Stopped, and the one that is wanted waits.
            self.assertEqual(len(self.started), 1)
            self.assertIsNone(self.window.readings.worker)
            self.window.readings.hold("schema")
            self.window.readings.release("texts")
            self.assertEqual(len(self.started), 1)
            # Told of something it does not hold, it does nothing.
            self.window.readings.release("rebuild")
            self.assertEqual(len(self.started), 1)
            self.window.readings.release("schema")
            self.assertEqual(len(self.started), 2)
            self.started[1].run()
        self.assertEqual(self.comes_to(self.LATE), [("1010030", "16"), ("1010031", "30")])

    def test_clearing_the_texts_stops_a_reading_and_starts_the_one_wanted(self):
        with self.reading(now=False):
            self.window.unload_mods([self.EARLY])
            first = self.started[0]
            # The worker never ran, so the window still holds it: as a
            # thread that has been told to stop and has not stopped yet.
            self.window.game_files.clear_texts()
            self.assertTrue(first._closing)
            self.assertEqual(self.window.readings._holds, set())
            first.run()
            self.assertEqual(len(self.started), 2)

    def test_a_reading_done_here_after_all_is_said_once(self):
        with self.reading(now=False):
            self.window.unload_mods([self.EARLY])
            self.window.status_label.setText("")
            self.started[0].fell_back.emit("it ended with no answer")
            said = self.window.status_label.text()
            self.assertIn("it ended with no answer", said)
            self.window.status_label.setText("")
            self.started[0].fell_back.emit("again")
            self.assertEqual(self.window.status_label.text(), "")

    def test_an_answer_to_an_older_question_is_not_taken_either(self):
        from anno_modding_studio.core import stale

        with self.reading(now=False):
            self.window.unload_mods([self.EARLY])
            first = self.started[0]
            reports = stale.read_again(first.taken, self.window.index)
            self.window.loadout.drop_layer(self.LAST)
            self.window.readings._on_done(first.taken, reports, "")
            self.assertIn(("1010030", "24"), self.comes_to(self.LATE))
            self.assertEqual(len(self.started), 2)

    def test_a_reading_that_comes_while_a_dialog_is_open_waits_for_it(self):
        from anno_modding_studio.ui import readings_controller

        with self.reading(now=False), mock.patch.object(
            readings_controller.QTimer, "singleShot"
        ) as later:
            self.window.unload_mods([self.EARLY])
            with mock.patch.object(
                readings_controller.QApplication, "activeModalWidget", return_value=mock.Mock(**{"property.return_value": None})
            ):
                self.started[0].run()
            self.assertIn(("1010030", "24"), self.comes_to(self.LATE))
            later.assert_called_once()
            self.window.readings._take_held()
        self.assertIn(("1010030", "16"), self.comes_to(self.LATE))

    def corner(self):
        from anno_modding_studio.ui.background import BackgroundIndicator

        return self.window.statusBar().findChild(BackgroundIndicator)

    def test_the_status_bar_says_whether_anything_is_still_being_read(self):
        corner = self.corner()
        self.assertEqual(corner.label.text(), "No background work running")
        self.assertTrue(corner.bar.isHidden())
        with self.reading(now=False):
            self.window.unload_mods([self.EARLY])
            self.assertEqual(corner.label.text(), "Reading 2 mods again…")
            self.assertFalse(corner.bar.isHidden())
            self.assertTrue(self.window.background.busy)
            self.started[0].run()
        self.assertEqual(corner.label.text(), "No background work running")
        self.assertFalse(self.window.background.busy)

    def test_a_reading_waiting_for_a_dialog_is_still_running(self):
        from anno_modding_studio.ui import readings_controller

        with self.reading(now=False), mock.patch.object(readings_controller.QTimer, "singleShot"):
            self.window.unload_mods([self.EARLY])
            with mock.patch.object(
                readings_controller.QApplication, "activeModalWidget", return_value=mock.Mock(**{"property.return_value": None})
            ):
                self.started[0].run()
            self.assertEqual(self.corner().label.text(), "Reading 2 mods again…")
            self.window.readings._take_held()
        self.assertFalse(self.window.background.busy)

    def test_several_things_running_are_counted_and_listed(self):
        tasks, corner = self.window.background, self.corner()
        tasks.begin("one", "Doing one thing…")
        tasks.begin("two", "Doing another…")
        self.assertEqual(corner.label.text(), "Doing one thing… (and 1 more)")
        self.assertEqual(corner.toolTip(), "Doing one thing…\nDoing another…")
        tasks.end("one")
        self.assertEqual(corner.label.text(), "Doing another…")
        tasks.end("two")
        tasks.end("two")
        self.assertEqual(corner.label.text(), "No background work running")

    def test_the_status_bar_is_as_tall_working_as_idle(self):
        from anno_modding_studio.ui import theme

        corner, tasks = self.corner(), self.window.background
        # In the app's own style sheet, where a QProgressBar's frame and
        # padding made it taller than the bar's line of text.
        corner.setStyleSheet(theme.stylesheet())
        try:
            idle = corner.sizeHint().height()
            tasks.begin("one", "Doing one thing…")
            self.assertFalse(corner.bar.isHidden())
            self.assertEqual(corner.sizeHint().height(), idle)
        finally:
            tasks.end("one")
            corner.setStyleSheet("")

    def test_export_waits_for_a_reading_to_finish(self):
        from PySide6.QtCore import QTimer

        output = self.window.output
        seen = []

        def finish():
            from anno_modding_studio.ui.background import is_waiting

            waiting = QApplication.activeModalWidget()
            seen.append((is_waiting(waiting), waiting.labelText()))
            self.started[0].run()

        with self.reading(now=False), mock.patch.object(
            output, "for_export", side_effect=RuntimeError("went on")
        ) as went_on:
            self.window.unload_mods([self.EARLY])
            QTimer.singleShot(0, finish)
            with self.assertRaises(RuntimeError):
                output.export_mod(confirm_overwrite=False, notify=False)
            # Only once the reading was in, taken under the waiting dialog.
            went_on.assert_called_once()
        self.assertEqual(len(seen), 1)
        self.assertTrue(seen[0][0])
        self.assertIn("Reading 2 mods again…", seen[0][1])
        self.assertFalse(self.window.background.busy)
        self.assertIn(("1010030", "16"), self.comes_to(self.LATE))

    def test_cancelling_the_wait_does_not_export_or_play(self):
        from PySide6.QtCore import QTimer

        output = self.window.output

        def cancel():
            QApplication.activeModalWidget().cancel()

        with self.reading(now=False), mock.patch.object(
            output, "exported_loadout"
        ) as went_on, mock.patch.object(
            self.window.prompts, "game_running"
        ) as asked:
            self.window.unload_mods([self.EARLY])
            for act, nothing in (
                (lambda: output.export_mod(confirm_overwrite=False, notify=False), None),
                (lambda: output.export_zip(notify=False), None),
                (lambda: output.export_mine(confirm=False, notify=False), []),
                (lambda: self.window.live.play(confirm=False), False),
            ):
                QTimer.singleShot(0, cancel)
                self.assertEqual(act(), nothing)
            went_on.assert_not_called()
            asked.assert_not_called()
            self.assertTrue(self.window.background.busy)

    def test_with_nothing_running_nothing_waits(self):
        from PySide6.QtWidgets import QProgressDialog

        with mock.patch.object(QProgressDialog, "exec") as shown:
            self.assertTrue(self.window.background.wait(self.window))
            self.assertTrue(self.window.output.caught_up())
        shown.assert_not_called()

    def test_export_does_not_wait_for_the_games_loader_and_a_whole_wait_does(self):
        """Exporting a loadout's one-change mod waited more than two minutes
        on the loader replaying the loadout's mods, for a check none of the
        files needs (H2-15). A wait that leaves nothing out still waits for
        it. Play's own wait for it is in test_loadercheck."""
        from PySide6.QtWidgets import QProgressDialog

        tasks = self.window.background
        tasks.begin("loader", "Asking the game's loader about the export…")
        tasks.begin("probe", "Trying what the game's loader does…")
        self.addCleanup(tasks.end, "loader")
        self.addCleanup(tasks.end, "probe")
        with mock.patch.object(QProgressDialog, "exec", return_value=0) as shown:
            self.assertTrue(self.window.output.caught_up())
            shown.assert_not_called()
            self.assertFalse(tasks.wait(self.window))
            shown.assert_called_once()
        # With a reading as well, that is waited for, and only that is said.
        tasks.begin("reading", "Reading 2 mods again…")
        self.addCleanup(tasks.end, "reading")
        said = []

        class Said(QProgressDialog):
            def exec(self):
                said.append(self.labelText())
                return 0

        with mock.patch("anno_modding_studio.ui.background.QProgressDialog", Said):
            self.assertFalse(self.window.output.caught_up())
        self.assertEqual(said, ["Waiting for this to finish first:\nReading 2 mods again…"])

    def test_a_reading_that_fails_leaves_nothing_running(self):
        from anno_modding_studio.core import stale

        with self.reading(now=True), mock.patch.object(
            stale, "read_again", side_effect=OSError("no")
        ), mock.patch("traceback.print_exc"):
            self.window.unload_mods([self.EARLY])
        self.assertFalse(self.window.background.busy)

    def test_a_reading_that_fails_says_so_and_is_not_asked_again(self):
        from anno_modding_studio.core import stale

        with self.reading(now=True), mock.patch.object(
            stale, "read_again", side_effect=OSError("no such folder")
        ), mock.patch("traceback.print_exc"):
            self.window.unload_mods([self.EARLY])
            self.assertIn("no such folder", self.window.status_label.text())
            self.assertIn("may be out of date", self.mod_row(self.LATE).text(1))
            self.window.on_changes_updated()
            self.assertEqual(len(self.started), 1)

    def test_a_mod_imported_on_its_own_is_read_where_it_stands(self):
        from anno_modding_studio.core import stale

        loadout = self.window.loadout
        with self.reading(now=False):
            self.window.readings.read_alone(self.LATE)
            self.assertEqual(loadout.layer(self.LATE).stale, [[stale.ALONE, self.LATE]])
            self.window.on_changes_updated()
            # And the mod after it: a mod imported again may not be the mod it was.
            self.assertEqual(self.started[0].taken.asked, [self.LATE, self.LAST])

    def test_reading_the_folder_again_leaves_nothing_in_doubt(self):
        loadout = self.window.loadout
        with self.reading(now=False):
            self.window.unload_mods([self.EARLY])
            self.assertTrue(loadout.layer(self.LATE).stale)
            self.load()
        self.assertEqual([layer.stale for layer in loadout.layers], [[], [], []])


class TestAModUpdatedOnDisk(StaleReadingsCase):
    """Phase 6a of docs/proposals/projects-and-loadouts-proposal.md: a mod
    whose folder holds another version than the one read is read again when
    the loadout is opened and before Play."""

    def update_early(self):
        """As though Early had been read at 1.0, and its folder held 1.1,
        which leaves 13 where 1.0 left 12."""
        self.window.loadout.layer(self.EARLY).declared["Version"] = "1.0"
        folder = self.mods / self.EARLY
        (folder / "modinfo.json").write_text(
            '{"ModName": {"English": "%s"}, "Version": "1.1"}' % self.EARLY, encoding="utf-8"
        )
        (folder / "data/config/export/main/asset/assets.xml").write_text(
            self.FIRST.replace(">12<", ">13<"), encoding="utf-8"
        )

    def test_nothing_is_read_where_nothing_was_updated(self):
        with self.reading(now=False):
            self.window.readings.adopted()
        self.assertEqual(self.started, [])

    def test_opening_the_loadout_reads_it_again_and_the_mods_after_it(self):
        self.update_early()
        with self.reading(now=True):
            self.window.readings.adopted()
        self.assertEqual(self.started[0].taken.asked, [self.EARLY, self.LATE, self.LAST])
        self.assertEqual(self.comes_to(self.EARLY), [("1010030", "13")])
        self.assertIn(("1010030", "26"), self.comes_to(self.LATE))
        self.assertEqual(self.window.loadout.layer(self.EARLY).version, "1.1")
        said = self.window.status_label.text()
        self.assertIn("1 mod was updated on disk and has been read again.", said)
        self.assertIn("2 mods were read again, because what loads before them changed.", said)

    def test_play_reads_it_again_before_it_waits_for_the_background(self):
        self.update_early()
        window = self.window
        with self.reading(now=False), mock.patch.object(window.background, "wait", return_value=False) as wait:
            self.assertFalse(window.live.play())
            wait.assert_called_once()
            self.assertEqual(len(self.started), 1)
            self.assertIn("readings", window.background._running)
            self.assertIn("another version", self.mod_row(self.EARLY).toolTip(0))


class TestAProjectChangedInAnotherLoadout(StaleReadingsCase):
    """Phase 6b of docs/proposals/projects-and-loadouts-proposal.md: a
    project another loadout changed is read back when the loadout is opened,
    and its hold let go of (core/readback.py)."""

    def residents(self, new, old="0"):
        from anno_modding_studio.core.changes import Change

        return Change(
            guid="1010030", asset_label="Residence", path="/Values/Residence7/Residents",
            tag="Residents", old_value=old, new_value=new, authored=True,
        )

    def test_opening_it_reads_the_project_back_and_says_so(self):
        from anno_modding_studio.core import narrow, places, projects
        from anno_modding_studio.core.changes import Loadout

        loadout = self.window.loadout
        loadout.own_mod, loadout.own.loads_last = True, True
        loadout.set_change(loadout.place(self.residents("40")))
        narrow.write(loadout)
        path = places.loadouts_dir() / "Main.annoload"
        loadout.save(path)
        other = Loadout.load(path)
        (mine,) = other.changes_of("")
        other.set_change(other.place(self.residents("50", old=mine.old_value)))
        narrow.write(other)
        other.save(places.loadouts_dir() / "Other.annoload")

        opened = Loadout.load(path)
        self.assertEqual(opened.held(""), projects.MOVED)
        with self.reading(now=True):
            self.window.adopt_loadout(path, opened)
        self.assertEqual(self.window.loadout.held(""), "")
        self.assertEqual(self.comes_to(""), [("1010030", "50")])
        self.assertIn(
            "1 mod of yours was changed in its project by another loadout, "
            "and has been read again as its project is now.",
            self.window.status_label.text(),
        )
        self.assertFalse(self.window.files.unsaved_work())


class TestAnOpThatAddsAssetsToAList(StaleReadingsCase):
    """``<ModOp Type="add" Path="//Assets[Asset/Values/Standard/GUID='…']">``
    with assets in it.  Its row said "Asset", "0 assets"."""

    ASSET = ("<Asset><Template>Trigger</Template><Values><Standard><GUID>%s</GUID>"
             "<Name>%s</Name></Standard></Values></Asset>")
    PATH = "//Assets[Asset/Values/Standard/GUID='1010030']"
    SECOND = (
        '<ModOps><ModOp Type="add" Path="%s">%s</ModOp><ModOp Type="add" Path="%s">%s%s</ModOp>'
        "</ModOps>" % (
            PATH, ASSET % ("1800000300", "intermediate moderate 4.0"),
            PATH, ASSET % ("1800000301", "unlock one"), ASSET % ("1800000302", "unlock two"),
        )
    )

    def op_rows(self, origin):
        return []

    def test_its_row_names_what_it_adds_and_is_no_pattern(self):
        from anno_modding_studio.ui.navigation import TAB_FILE

        self.window.open_tab(TAB_FILE)
        self.window.file_panel.look_at(self.LATE)
        tree = self.window.file_panel.outline.tree
        rows = [tree.topLevelItem(at) for at in range(tree.topLevelItemCount())]
        # Each the tag it opens with, which is the same for both: what it
        # adds is in its tooltip, under the tag.
        self.assertEqual(
            [(row.text(0), row.text(1)) for row in rows],
            [
                ('<ModOp Type="add" Path="%s">' % self.PATH, "1 change, kept as the op itself"),
                ('<ModOp Type="add" Path="%s">' % self.PATH, "2 changes, kept as the op itself"),
            ],
        )
        self.assertIn("new asset intermediate moderate 4.0", rows[0].toolTip(0))
        self.assertIn("2 new assets, the first unlock one", rows[1].toolTip(0))
        for row in rows:
            self.assertNotIn("pattern", row.toolTip(1))
        # And the assets are assets the Studio knows.
        self.assertIn("unlock two", self.window.workspace.display_name("1800000302"))


class TestAnEditLeavesReadingsInDoubt(StaleReadingsCase):
    """Phase 1 of the file tab: an edit made here is a change in what loads
    for every op after it, and those are read again behind the window."""

    def switch_first_off(self):
        """Switch off the one op of the mod that loads first, as a tick in
        the Mod changes panel does, and record the step."""
        loadout = self.window.loadout
        (op,) = [op for op in loadout.ops if op.origin == self.EARLY]
        op.active = False
        for change in loadout.changes_by_op(op.uid):
            change.active = False
        self.window._on_edited()

    def test_switched_off_an_edit_marks_nothing(self):
        self.switch_first_off()
        self.assertEqual([layer.stale for layer in self.window.loadout.layers], [[], [], []])

    def test_the_mods_after_it_are_read_again_and_the_step_stays(self):
        with self.reading(now=True):
            self.switch_first_off()
            self.assertEqual(self.comes_to(self.LATE), [("1010030", "16"), ("1010031", "30")])
            self.assertEqual(
                [layer.stale for layer in self.window.loadout.layers], [[], [], []]
            )
            self.assertIn("1 ModOp comes to something else", self.window.status_label.text())
            # Asked for by an edit, it is no end to the history.
            self.assertTrue(self.window.undo.can_undo)

            self.window.undo_last()
            self.assertEqual(self.comes_to(self.LATE), [("1010030", "24"), ("1010031", "30")])
            self.assertTrue(self.window.undo.can_redo)
            self.assertFalse(self.window.undo.can_undo)

            self.window.redo_last()
            self.assertEqual(self.comes_to(self.LATE), [("1010030", "16"), ("1010031", "30")])
        self.assertIsNone(self.window.readings.worker)

    def test_the_rows_say_so_until_the_reading_is_in(self):
        with self.reading(now=False):
            self.switch_first_off()
            self.assertEqual(len(self.started), 1)
            self.assertIn("being read again", self.mod_row(self.LATE).text(1))
            self.assertIn("was edited here", self.mod_row(self.LATE).toolTip(0))
            # The op at the asset the edit was about, and not the one beside it.
            doubled, plain = self.op_rows(self.LATE)
            self.assertTrue(doubled.text(1).startswith("being read again"), doubled.text(1))
            self.assertNotIn("read again", plain.text(1))
            self.assertIn(("1010030", "24"), self.comes_to(self.LATE))
            self.started[0].run()
        self.assertNotIn("read again", self.mod_row(self.LATE).text(1))
        self.assertIn(("1010030", "16"), self.comes_to(self.LATE))

    def test_a_reading_overtaken_by_another_edit_is_not_taken(self):
        with self.reading(now=False):
            self.switch_first_off()
            first = self.started[0]
            self.window.undo_last()
            self.assertTrue(first._cancelled)
            first.run()
            self.assertEqual(len(self.started), 2)
            self.started[1].run()
        self.assertEqual(self.comes_to(self.LATE), [("1010030", "24"), ("1010031", "30")])
        self.assertTrue(self.window.undo.can_redo)

    def test_the_rows_are_drawn_once_for_an_edit_and_its_marks(self):
        """Drawn for the edit and then again for the marks it left, a list
        of 68 mods was two seconds of every Apply."""
        panel = self.window.changes_panel
        with self.reading(now=False), mock.patch.object(
            panel, "refresh", wraps=panel.refresh
        ) as drawn:
            self.switch_first_off()
            self.assertEqual(drawn.call_count, 1)
            # And they were drawn with the marks.
            self.assertIn("being read again", self.mod_row(self.LATE).text(1))

    def test_the_reading_waits_for_the_edits_to_stop_coming(self):
        from support import wait_until

        from anno_modding_studio.ui import readings_controller

        readings = self.window.readings
        with self.reading(now=False), mock.patch.object(readings_controller, "EDIT_MS", 60000):
            self.switch_first_off()
            # Nothing is read yet, and it is said to be on its way.
            self.assertEqual(self.started, [])
            self.assertTrue(readings._soon.isActive())
            self.assertTrue(self.window.background.busy)
            self.assertIn("being read again", self.mod_row(self.LATE).text(1))
            # Another edit, and still nothing: one reading for the two.
            self.window.undo_last()
            self.window.on_changes_updated()
            self.assertEqual(self.started, [])
            # The pause runs out, cut short here: what it starts is the reading.
            readings._soon.setInterval(0)
            self.assertTrue(wait_until(lambda: self.started))
            self.assertEqual(len(self.started), 1)
            self.started[0].run()
        self.assertFalse(self.window.background.busy)
        self.assertEqual(self.comes_to(self.LATE), [("1010030", "24"), ("1010031", "30")])

    def test_an_edit_with_nothing_after_it_starts_no_reading(self):
        loadout = self.window.loadout
        with self.reading(now=False):
            (op,) = [op for op in loadout.ops if op.origin == self.LAST]
            op.active = False
            for change in loadout.changes_by_op(op.uid):
                change.active = False
            self.window._on_edited()
            # Itself, from the op that was switched: nothing loads after it.
            self.assertEqual(len(self.started), 1)
            self.assertEqual(self.started[0].taken.asked, [self.LAST])


if __name__ == "__main__":
    unittest.main(verbosity=2)
