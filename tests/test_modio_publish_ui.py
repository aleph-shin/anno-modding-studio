"""Signing in, linking a loadout to its mod, and publishing, driven offscreen.

Every mod.io call goes to a fake that records it; the credential store is
given a stand-in for DPAPI; the mod published is built from a change made
for the test.
"""

from __future__ import annotations

import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from typing import ClassVar
from unittest import mock

import _isolation  # first: keeps the suite out of real files under any runner

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from PySide6.QtWidgets import QApplication, QDialog, QLabel, QMessageBox  # noqa: E402
from support import reverse_bytes  # noqa: E402

from anno_modding_studio.core import modio, secrets  # noqa: E402
from anno_modding_studio.core.changes import (  # noqa: E402
    AIM_MAKE,
    Change,
    Loadout,
    own_record,
)
from anno_modding_studio.ui import modio_publish_dialog, output_panel  # noqa: E402
from anno_modding_studio.ui.modio_account_panel import ModioAccountPanel  # noqa: E402
from anno_modding_studio.ui.output_panel import OutputPanel  # noqa: E402

_app = QApplication.instance() or QApplication([])


class FakeClient:
    """Remembers what it was asked, keyed by the token it was made with."""

    calls: ClassVar[list] = []

    def __init__(self, key: str, token: str = "", game_id: int = 0, refuse: str = "", live: str = "1.0"):
        self.key, self.token, self.refuse, self.live = key, token, refuse, live

    def terms(self):
        FakeClient.calls.append(("terms", self.token))
        return modio.Terms(text="Be nice.")

    def request_code(self, email):
        FakeClient.calls.append(("request_code", email))
        return "Enter the 5-digit code sent to your email."

    def exchange_code(self, code, terms_agreed=True):
        FakeClient.calls.append(("exchange", code, terms_agreed))
        return modio.Token("tok-123", 4000000000)

    def me(self):
        FakeClient.calls.append(("me", self.token))
        return modio.Me(5, "lars")

    def logout(self):
        FakeClient.calls.append(("logout", self.token))

    # What my_mods lists, for a test to change: the mod made on the
    # website, say.
    mine: ClassVar[list] = []

    def my_mods(self, game_id):
        FakeClient.calls.append(("my_mods", game_id))
        return list(FakeClient.mine) or [
            modio.ModSummary(id=9, name="Better Harbours", version=self.live, modfile_id=90)
        ]

    # The summary the mod has on mod.io, for a test to change.
    summary: ClassVar[str] = ""
    refuse_summary: ClassVar[str] = ""

    def mod(self, game_id, mod_id):
        return modio.ModSummary(
            id=mod_id, name="Better Harbours", version=self.live, modfile_id=90,
            profile_url="https://mod.io/g/anno-1800/m/bh", summary=FakeClient.summary,
        )

    def set_summary(self, game_id, mod_id, summary):
        FakeClient.calls.append(("summary", game_id, mod_id, summary))
        if FakeClient.refuse_summary:
            raise modio.Refused(FakeClient.refuse_summary)
        FakeClient.summary = summary
        return self.mod(game_id, mod_id)

    def add_modfile(self, game_id, mod_id, archive, version, changelog="", active=True):
        FakeClient.calls.append(("upload", game_id, mod_id, Path(archive).name, version, changelog, active))
        if self.refuse:
            raise modio.Refused(self.refuse)
        return modio.ModSummary(id=mod_id, name="Better Harbours", version=version, modfile_id=91)


class _WithStore(unittest.TestCase):
    def setUp(self):
        FakeClient.calls = []
        FakeClient.mine = []
        FakeClient.summary = ""
        FakeClient.refuse_summary = ""
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.store = secrets.Store(self.root / "c.bin", wrap=reverse_bytes, unwrap=reverse_bytes)
        self.store.save(secrets.Credentials(api_key="k"))


class TestSigningIn(_WithStore):
    def test_terms_then_code_then_token_kept_and_who_you_are(self):
        dialog = ModioAccountPanel(self.store, check=lambda _k: None, client=FakeClient, show_terms=lambda _t: True)
        self.assertFalse(dialog.code_request_button.isEnabled())
        dialog.email_edit.setText("a@b.c")
        self.assertTrue(dialog.code_request_button.isEnabled())
        dialog._send_code()
        self.assertIn("code", dialog.status.text())
        self.assertEqual(FakeClient.calls[:2], [("terms", ""), ("request_code", "a@b.c")])
        dialog.code_edit.setText("12345")
        dialog._sign_in()
        saved = self.store.load()
        self.assertEqual((saved.token, saved.token_expires, saved.email), ("tok-123", 4000000000, "a@b.c"))
        self.assertNotIn(b"tok-123", self.store.path.read_bytes())
        self.assertIn("lars", dialog.note)
        self.assertIn(("me", "tok-123"), FakeClient.calls)
        self.assertTrue(dialog.sign_out_button.isEnabled())
        self.assertFalse(dialog.code_request_button.isEnabled())

    def test_declining_the_terms_asks_for_no_code(self):
        dialog = ModioAccountPanel(self.store, check=lambda _k: None, client=FakeClient, show_terms=lambda _t: False)
        dialog.email_edit.setText("a@b.c")
        dialog._send_code()
        self.assertIn("not agreed", dialog.status.text())
        self.assertEqual([c[0] for c in FakeClient.calls], ["terms"])

    def test_signing_out_tells_modio_and_keeps_the_key(self):
        self.store.save(secrets.Credentials(api_key="k", token="tok-123", email="a@b.c"))
        dialog = ModioAccountPanel(self.store, check=lambda _k: None, client=FakeClient)
        self.assertIn("Signed in as a@b.c", dialog.signed_label.text())
        dialog._sign_out()
        self.assertEqual(FakeClient.calls, [("logout", "tok-123")])
        saved = self.store.load()
        self.assertEqual((saved.api_key, saved.token), ("k", ""))

    def test_a_sign_in_past_its_day_is_said_and_can_be_replaced(self):
        self.store.save(secrets.Credentials(
            api_key="k", token="tok-old", token_expires=int(time.time()) - 60, email="a@b.c"))
        dialog = ModioAccountPanel(self.store, check=lambda _k: None, client=FakeClient)
        self.assertIn("ran out", dialog.signed_label.text())
        self.assertTrue(dialog.email_edit.isEnabled())
        self.assertFalse(dialog.sign_out_button.isEnabled())
        dialog.code_edit.setText("12345")
        self.assertTrue(dialog.sign_in_button.isEnabled())

    def test_a_sign_in_running_out_soon_says_when(self):
        self.store.save(secrets.Credentials(
            api_key="k", token="tok-123", token_expires=int(time.time()) + 3 * 24 * 3600))
        dialog = ModioAccountPanel(self.store, check=lambda _k: None, client=FakeClient)
        self.assertIn("runs out on", dialog.signed_label.text())
        self.assertTrue(dialog.sign_out_button.isEnabled())

    def test_the_real_terms_dialog_carries_modios_wording(self):
        dialog = modio_publish_dialog.TermsDialog(modio.Terms(text="Be nice.", agree="Yes please", disagree="Nope"))
        self.assertEqual(dialog.agree_button.text(), "Yes please")


class TestPublishing(_WithStore):
    def setUp(self):
        super().setUp()
        self.store.save(secrets.Credentials(api_key="k", token="tok-123", email="a@b.c"))
        self.loadout = Loadout(game="anno1800", own=own_record(name="Better Harbours", version="1.1"), aim=AIM_MAKE)
        self.loadout.set_change(Change(guid="1", asset_label="Fish", path="Cost", tag="Value", new_value="2"))
        self.panel = OutputPanel(self.loadout)
        self.panel.modio_store = self.store
        self.panel.modio_client = FakeClient
        # Where an export would go: publishing writes nothing there.
        self.exports = self.root / "exports"
        self.panel.choose_destination(self.exports)

    def test_unlinked_says_so_and_the_button_walks_on(self):
        self.panel._refresh_modio()
        self.assertIn("Not linked", self.panel.modio_status.text())
        self.assertEqual(self.panel.modio_button.text(), "Publish to mod.io…")
        self.assertTrue(self.panel.modio_button.isEnabled())

    def test_the_row_is_only_for_the_mod_this_loadout_publishes(self):
        self.loadout.is_the_mod = False
        self.loadout.home = "someone-else"
        self.panel._refresh_modio()
        self.assertFalse(self.panel.modio_button.isVisibleTo(self.panel))
        self.assertFalse(self.panel.modio_status.isVisibleTo(self.panel))

    def test_a_sign_in_past_its_day_is_not_used(self):
        self.loadout.modio_mod_id = 9
        self.store.save(secrets.Credentials(api_key="k", token="tok-123", token_expires=1))
        self.panel._refresh_modio()
        self.assertIn("ran out", self.panel.modio_status.text())
        self.assertEqual(self.panel.modio_button.text(), "Publish to mod.io…")
        self.assertFalse(self.panel.link_modio())
        self.assertFalse(self.panel.publish_update())
        self.assertEqual(FakeClient.calls, [])

    def test_a_sign_in_running_out_soon_is_said_beside_publishing(self):
        self.loadout.modio_mod_id = 9
        self.store.save(secrets.Credentials(
            api_key="k", token="tok-123", token_expires=int(time.time()) + 3 * 24 * 3600))
        self.panel._refresh_modio()
        self.assertIn("runs out on", self.panel.modio_status.text())
        self.assertEqual(self.panel.modio_button.text(), "Publish 1.1…")

    def test_a_sign_in_modio_no_longer_takes_is_removed_and_said(self):
        self.loadout.modio_mod_id = 9

        class Dead(FakeClient):
            def mod(self, game_id, mod_id):
                raise modio.SignedOut("mod.io no longer accepts the sign-in")

        self.panel.modio_client = Dead
        warned = []
        with mock.patch.object(QMessageBox, "warning",
                               staticmethod(lambda *a, **k: warned.append(a[1:]))):
            self.assertFalse(self.panel.publish_update())
        self.assertEqual(self.store.load().token, "")
        self.assertEqual(self.store.load().api_key, "k")
        self.assertEqual(len(warned), 1)
        self.assertIn("Sign in again", warned[0][1])
        self.assertIn("Not signed in", self.panel.modio_status.text())
        self.assertEqual(self.panel.modio_button.text(), "Publish to mod.io…")

    def test_not_signed_in_says_so_and_the_button_signs_in(self):
        self.store.save(secrets.Credentials(api_key="k"))
        self.panel._refresh_modio()
        self.assertIn("Not signed in", self.panel.modio_status.text())
        self.assertIn("Signs you in", self.panel.modio_button.toolTip())
        asked = []
        self.panel.signInRequested.connect(lambda: asked.append(True))
        # Settings closed without signing in: nothing more happens.
        self.assertFalse(self.panel.publish_to_modio())
        self.assertEqual(asked, [True])
        self.assertEqual(FakeClient.calls, [])

    def test_the_button_signs_in_links_and_publishes_in_turn(self):
        self.store.save(secrets.Credentials(api_key="k"))
        self.panel.signInRequested.connect(lambda: self.store.save(
            secrets.Credentials(api_key="k", token="tok-123", email="a@b.c")))
        with _isolation.answering("Link"), \
                mock.patch.object(modio_publish_dialog.PublishDialog, "exec", lambda d: int(QDialog.DialogCode.Accepted)), \
                mock.patch.object(QMessageBox, "information", staticmethod(lambda *a, **k: None)), \
                mock.patch.object(output_panel, "tell", lambda *a, **k: None):
            self.assertTrue(self.panel.publish_to_modio())
        self.assertEqual(self.loadout.modio_mod_id, 9)
        self.assertEqual(next(c for c in FakeClient.calls if c[0] == "upload")[2], 9)

    def test_a_mod_of_the_same_name_is_offered_first(self):
        FakeClient.mine = [
            modio.ModSummary(id=7, name="Something Else", version="1.0"),
            modio.ModSummary(id=9, name="Other name", name_id="better-harbours", version="1.0"),
        ]
        with _isolation.answering("Link") as asked:
            self.assertTrue(self.panel.link_modio())
        self.assertEqual(asked.texts[0], "This looks like Other name on mod.io. Link it?")
        self.assertEqual(self.loadout.modio_mod_id, 9)

    def test_declining_the_one_that_looks_like_it_shows_your_mods(self):
        listed = []

        def pick(dialog):
            listed.append([mod.id for mod in dialog.mine])
            return int(QDialog.DialogCode.Rejected)

        with _isolation.answering(False), \
                mock.patch.object(modio_publish_dialog.LinkDialog, "exec", pick):
            self.assertFalse(self.panel.link_modio())
        self.assertEqual(listed, [[9]])
        self.assertEqual(self.loadout.modio_mod_id, 0)

    def test_a_mod_not_on_modio_is_created_on_the_website_and_then_linked(self):
        FakeClient.mine = [modio.ModSummary(id=7, name="Something Else", version="1.0")]
        self.loadout.own.description = "Bigger quays."
        opened = []

        def create_then_pick(dialog):
            self.assertTrue(dialog.create_button.isVisible() or not dialog.isVisible())
            dialog.create_on_modio()
            FakeClient.mine = [*FakeClient.mine, modio.ModSummary(id=12, name="Harbours", version="")]
            dialog.list_again()
            dialog._accept()
            return int(QDialog.DialogCode.Accepted)

        with mock.patch.object(modio_publish_dialog.LinkDialog, "exec", create_then_pick), \
                mock.patch.object(modio_publish_dialog.QDesktopServices, "openUrl",
                                  staticmethod(lambda url: opened.append(url.toString()))):
            self.assertTrue(self.panel.link_modio())
        self.assertEqual(opened, ["https://mod.io/g/anno-1800/m/add"])
        # The summary alone: mod.io's page has a box for the name and one
        # for the summary, and the two copied as one went into the second.
        self.assertEqual(QApplication.clipboard().text(), "Bigger quays.")
        self.assertEqual(self.loadout.modio_mod_id, 12)

    def test_creating_one_for_117_says_first_that_modio_wants_a_ubisoft_account(self):
        dialog = modio_publish_dialog.LinkDialog([], new=modio_publish_dialog.NewMod(
            page="https://mod.io/g/anno-117-pax-romana/m/add", name="M", summary="",
            ubisoft=True, relist=lambda: [],
        ))
        self.assertIn("Ubisoft account", dialog.new_note.text())
        self.assertFalse(dialog.created_button.isVisibleTo(dialog))

    def test_linking_lists_your_mods_for_this_game_and_keeps_the_choice(self):
        # None of them has the mod's name, so the list is shown at once.
        FakeClient.mine = [modio.ModSummary(id=9, name="Harbours Deluxe", version="1.0")]
        edited = []
        self.panel.loadoutEdited.connect(lambda: edited.append(True))

        def choose(dialog):
            dialog.chosen = 9
            return int(QDialog.DialogCode.Accepted)

        with mock.patch.object(modio_publish_dialog.LinkDialog, "exec", choose):
            self.assertTrue(self.panel.link_modio())
        self.assertEqual(self.loadout.modio_mod_id, 9)
        self.assertIn(("my_mods", 4169), FakeClient.calls)
        self.assertEqual(edited, [True])
        self.assertIn("Linked to mod.io mod 9", self.panel.modio_status.text())
        self.assertEqual(self.panel.modio_button.text(), "Publish 1.1…")

    def test_publishing_builds_zips_confirms_and_uploads(self):
        self.loadout.modio_mod_id = 9
        published = []
        self.panel.modPublished.connect(published.append)
        seen = {}

        def confirm(dialog):
            seen["heading"] = dialog.ready.mod.name
            seen["as"] = dialog.ready.as_user
            dialog.changelog.setPlainText("Quays.")
            dialog._accept()
            return int(QDialog.DialogCode.Accepted)

        with mock.patch.object(modio_publish_dialog.PublishDialog, "exec", confirm), \
                mock.patch.object(QMessageBox, "information", staticmethod(lambda *a, **k: None)), \
                mock.patch.object(output_panel, "tell", lambda *a, **k: None):
            self.assertTrue(self.panel.publish_update())
        upload = next(c for c in FakeClient.calls if c[0] == "upload")
        self.assertEqual(upload, ("upload", 4169, 9, "[Gameplay] Better Harbours.zip", "1.1", "Quays.", True))
        self.assertEqual(seen, {"heading": "Better Harbours", "as": "lars"})
        self.assertEqual(len(published), 1)
        self.assertIn("live now", published[0])
        # Built aside and zipped: the export folder is not written.
        self.assertFalse(self.exports.exists())

    def test_the_live_version_again_is_offered_the_next_before_anything_is_built(self):
        self.loadout.modio_mod_id = 9
        self.loadout.own.version = "1.0"
        self.panel.load_loadout_fields()
        exported = []
        with _isolation.answering(False) as asked, \
                mock.patch.object(output_panel.build, "write",
                                  lambda *a, **k: exported.append(True)):
            self.assertFalse(self.panel.publish_update())
        self.assertEqual(asked.titles[0], "Publish as 1.0.1?")
        self.assertEqual(asked.buttons[0], ["Publish as 1.0.1", "Cancel"])
        self.assertIn("already the live version", asked.texts[0])
        # Declined: nothing written, nothing sent, and the version as it was.
        self.assertEqual((exported, self.loadout.own.version), ([], "1.0"))
        self.assertFalse([c for c in FakeClient.calls if c[0] == "upload"])

    def test_taking_the_next_version_publishes_it_and_keeps_it(self):
        self.loadout.modio_mod_id = 9
        self.loadout.own.version = "1.0"
        self.panel.load_loadout_fields()
        with _isolation.answering("Publish as 1.0.1"), \
                mock.patch.object(modio_publish_dialog.PublishDialog, "exec", lambda d: int(QDialog.DialogCode.Accepted)), \
                mock.patch.object(QMessageBox, "information", staticmethod(lambda *a, **k: None)), \
                mock.patch.object(output_panel, "tell", lambda *a, **k: None):
            self.assertTrue(self.panel.publish_update())
        upload = next(c for c in FakeClient.calls if c[0] == "upload")
        self.assertEqual(upload[4], "1.0.1")
        self.assertEqual(self.loadout.own.version, "1.0.1")
        self.assertEqual(self.panel.version_edit.text(), "1.0.1")

    def test_a_version_with_no_numbers_is_still_refused(self):
        # Nothing to count on from "beta", so no next is offered; the check
        # that holds regardless says why.
        self.loadout.modio_mod_id = 9
        self.loadout.own.version = "beta"
        self.panel.modio_client = lambda key, token="", game_id=0: FakeClient(key, token, live="beta")
        warned = []
        with mock.patch.object(QMessageBox, "warning", staticmethod(lambda *a, **k: warned.append(a[2]))):
            self.assertFalse(self.panel.publish_update())
        self.assertIn("already the live version", warned[0])
        self.assertFalse([c for c in FakeClient.calls if c[0] == "upload"])

    def _released_then_added(self):
        """1.1 released with one change; since then an asset it never touched."""
        from anno_modding_studio.core.changes import KIND_EXPORT, Change

        self.loadout.modio_mod_id = 9
        self.loadout.set_change(Change(guid="1", asset_label="Fish", path="Cost", tag="Value", new_value="2"))
        self.loadout.checkpoint("Exported v1.1", kind=KIND_EXPORT)
        self.loadout.set_change(Change(guid="2", asset_label="Bread", path="Cost", tag="Value", new_value="3"))
        self.panel.load_loadout_fields()

    def _publish_answering(self, answer: str):
        seen = {}

        def confirm(dialog):
            seen["note"] = dialog.note.text()
            seen["changelog"] = dialog.changelog.toPlainText()
            dialog._accept()
            return int(QDialog.DialogCode.Accepted)

        with mock.patch.object(OutputPanel, "_ask_suggested", lambda panel, found: answer), \
                mock.patch.object(modio_publish_dialog.PublishDialog, "exec", confirm), \
                mock.patch.object(QMessageBox, "information", staticmethod(lambda *a, **k: None)), \
                mock.patch.object(output_panel, "tell", lambda *a, **k: None):
            done = self.panel.publish_update()
        return done, seen

    def test_a_version_below_what_changed_is_offered_the_one_it_calls_for(self):
        self._released_then_added()
        done, seen = self._publish_answering("use")
        self.assertTrue(done)
        upload = next(c for c in FakeClient.calls if c[0] == "upload")
        self.assertEqual(upload[4], "1.2.0")
        self.assertEqual(self.loadout.own.version, "1.2.0")
        self.assertEqual(seen["note"], "Since 1.1: a minor change (adds something). 1.2.0 covers it.")
        self.assertEqual(seen["changelog"], "Now also changes: Bread.")

    def test_keeping_the_version_is_an_answer(self):
        self._released_then_added()
        done, _seen = self._publish_answering("keep")
        self.assertTrue(done)
        self.assertEqual(next(c for c in FakeClient.calls if c[0] == "upload")[4], "1.1")

    def test_cancelling_sends_nothing(self):
        self._released_then_added()
        done, _seen = self._publish_answering("cancel")
        self.assertFalse(done)
        self.assertFalse([c for c in FakeClient.calls if c[0] == "upload"])
        self.assertEqual(self.loadout.own.version, "1.1")

    def test_what_was_published_is_remembered_with_a_checkpoint(self):
        self.loadout.modio_mod_id = 9
        self.loadout.builds[""] = {"build": 4, "digest": "x"}
        edited = []
        self.panel.loadoutEdited.connect(lambda: edited.append(True))
        with mock.patch.object(modio_publish_dialog.PublishDialog, "exec", lambda d: int(QDialog.DialogCode.Accepted)), \
                mock.patch.object(QMessageBox, "information", staticmethod(lambda *a, **k: None)), \
                mock.patch.object(output_panel, "tell", lambda *a, **k: None):
            self.assertTrue(self.panel.publish_update())
        sent = self.loadout.published
        # Built for it, so the build after the last one.
        self.assertEqual((sent["version"], sent["build"], sent["modfile_id"]), ("1.1", 5, 91))
        # In the mod's project history, which is what the next release is
        # measured from, and in the loadout's.
        self.assertEqual(self.loadout.history_of("")[-1].label, "Published v1.1, build 5")
        self.assertEqual(sent["checkpoint"], self.loadout.history_of("")[-1].timestamp)
        self.assertEqual(self.loadout.history[-1].label, "Published v1.1, build 5")
        self.assertTrue(edited)
        self.assertIn("Last published from here: 1.1, build 5", self.panel.modio_status.text())

    def publish(self, confirm=None):
        """Publish with the dialog answered: by ``confirm``, given the
        dialog, or accepted as it stands."""
        def answer(dialog):
            if confirm is not None:
                confirm(dialog)
            dialog._accept()
            return int(QDialog.DialogCode.Accepted)

        self.loadout.modio_mod_id = 9
        warned = []
        with mock.patch.object(modio_publish_dialog.PublishDialog, "exec", answer), \
                mock.patch.object(QMessageBox, "information", staticmethod(lambda *a, **k: None)), \
                mock.patch.object(QMessageBox, "warning", staticmethod(lambda *a, **k: warned.append(a[2]))), \
                mock.patch.object(output_panel, "tell", lambda *a, **k: None):
            self.assertTrue(self.panel.publish_update())
        return warned

    def test_the_summary_goes_with_the_file_where_it_differs(self):
        self.loadout.own.description = "Bigger quays."
        FakeClient.summary = "Old words."
        shown = []
        self.publish(lambda dialog: shown.append(dialog.summary_box.isChecked()))
        self.assertEqual(shown, [True])
        names = [call[0] for call in FakeClient.calls]
        # After the file: a summary refused must not keep the file back.
        self.assertLess(names.index("upload"), names.index("summary"))
        self.assertIn(("summary", 4169, 9, "Bigger quays."), FakeClient.calls)

    def test_a_summary_mod_io_has_already_is_not_sent_again(self):
        self.loadout.own.description = "Bigger quays."
        FakeClient.summary = "Bigger quays."
        boxes = []
        self.publish(lambda dialog: boxes.append(dialog.summary_box))
        self.assertEqual(boxes, [None])
        self.assertNotIn("summary", [call[0] for call in FakeClient.calls])

    def test_an_empty_one_leaves_mod_ios(self):
        FakeClient.summary = "Written on the website."
        self.publish()
        self.assertNotIn("summary", [call[0] for call in FakeClient.calls])

    def test_unticked_it_is_not_sent(self):
        self.loadout.own.description = "Bigger quays."
        self.publish(lambda dialog: dialog.summary_box.setChecked(False))
        self.assertNotIn("summary", [call[0] for call in FakeClient.calls])

    def test_one_too_long_for_mod_io_is_not_sent_and_says_so(self):
        self.loadout.own.description = "x" * (modio.SUMMARY_LIMIT + 1)
        said = []
        self.publish(lambda dialog: said.append(" ".join(
            label.text() for label in dialog.findChildren(QLabel))))
        self.assertIn("mod.io takes 250 at most", said[0])
        self.assertNotIn("summary", [call[0] for call in FakeClient.calls])

    def test_a_summary_refused_leaves_the_file_published(self):
        self.loadout.own.description = "Bigger quays."
        FakeClient.refuse_summary = "Not allowed."
        warned = self.publish()
        self.assertEqual(self.loadout.published["version"], "1.1")
        self.assertEqual(len(warned), 1)
        self.assertIn("The file is published", warned[0])

    def test_the_export_tab_calls_it_the_summary_and_holds_it_to_mod_ios_limit(self):
        edit = self.panel.description_edit
        labels = [label.text() for label in self.panel.meta_box.findChildren(QLabel)]
        self.assertIn("&Summary", labels)
        self.assertNotIn("&Description", labels)
        self.assertEqual(self.panel.summary_count.text(), "250 characters left")
        # Typed or pasted past it: what fits is kept, the rest left out.
        edit.setFocus()
        edit.insertPlainText("a" * 240)
        edit.insertPlainText("b" * 20)
        _app.processEvents()
        self.assertEqual(edit.toPlainText(), "a" * 240 + "b" * 10)
        self.assertEqual(self.loadout.own.description, "a" * 240 + "b" * 10)
        self.assertEqual(self.panel.summary_count.text(), "0 characters left")

    def test_a_paste_into_the_empty_box_is_held_to_it_too(self):
        """R3-1, hands-on run 3: 296 characters pasted into it were all
        kept, and the cut was refused past the end of the text."""
        edit = self.panel.description_edit
        for put in (lambda text: edit.insertPlainText(text),
                    lambda text: (_app.clipboard().setText(text), edit.paste())):
            with self.subTest():
                edit.clear()
                _app.processEvents()
                edit.setFocus()
                put("c" * 296)
                _app.processEvents()
                self.assertEqual(edit.toPlainText(), "c" * 250)
                self.assertEqual(self.panel.summary_count.text(), "0 characters left")

    def test_a_longer_summary_written_before_is_kept_and_said(self):
        self.loadout.own.description = "x" * 260
        self.panel.load_loadout_fields()
        _app.processEvents()
        edit = self.panel.description_edit
        self.assertEqual(len(edit.toPlainText()), 260)
        self.assertEqual(self.panel.summary_count.text(), "10 characters more than mod.io takes")
        # It can be shortened, and not made longer.
        edit.moveCursor(edit.textCursor().MoveOperation.End)
        edit.insertPlainText("y")
        _app.processEvents()
        self.assertEqual(len(edit.toPlainText()), 260)
        edit.textCursor().deletePreviousChar()
        _app.processEvents()
        self.assertEqual(self.loadout.own.description, "x" * 259)

    def test_a_refusal_is_explained_with_the_ubisoft_hint(self):
        self.loadout.modio_mod_id = 9
        self.panel.modio_client = lambda key, token="", game_id=0: FakeClient(key, token, refuse="A Ubisoft account is required")
        warned = []

        def confirm(dialog):
            return int(QDialog.DialogCode.Accepted)

        with mock.patch.object(modio_publish_dialog.PublishDialog, "exec", confirm), \
                mock.patch.object(QMessageBox, "warning", staticmethod(lambda *a, **k: warned.append(a[2]))):
            self.assertFalse(self.panel.publish_update())
        self.assertIn("Ubisoft account is required", warned[0])
        self.assertIn("linked", warned[0])


if __name__ == "__main__":
    unittest.main()
