"""Sending a new version to mod.io: the zip, the checks, and the link kept."""

from __future__ import annotations

import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

import _isolation  # noqa: F401 -- first: keeps the suite out of real files under any runner

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from anno_modding_studio.core import modio, publish  # noqa: E402
from anno_modding_studio.core.changes import Loadout  # noqa: E402


class FakeClient:
    def __init__(self, live_version="1.0", listed=True, summary=""):
        self.live_version = live_version
        self.listed = listed
        self.summary = summary
        self.sent = []

    def mod(self, game_id, mod_id):
        if not self.listed:
            return None
        return modio.ModSummary(id=mod_id, name="Better Harbours", version=self.live_version,
                                modfile_id=90, summary=self.summary)

    def set_summary(self, game_id, mod_id, summary):
        self.sent.append(("summary", game_id, mod_id, summary))
        return self.mod(game_id, mod_id)

    def add_modfile(self, game_id, mod_id, archive, version, changelog="", active=True):
        self.sent.append((game_id, mod_id, Path(archive).name, version, changelog, active))
        return modio.ModSummary(id=mod_id, name="Better Harbours", version=version, modfile_id=91)


class TestTheZip(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.mod = self.root / "[Gameplay] Better Harbours"
        (self.mod / "data" / "config").mkdir(parents=True)
        (self.mod / ".vscode").mkdir()
        (self.mod / "modinfo.json").write_text("{}", encoding="utf-8")
        (self.mod / "data" / "config" / "assets.xml").write_text("<ModOps/>", encoding="utf-8")
        (self.mod / ".vscode" / "settings.json").write_text("{}", encoding="utf-8")

    def test_the_folder_is_the_top_level_entry_and_tooling_is_left_out(self):
        archive = publish.zip_mod(self.mod, self.root / "out")
        self.assertEqual(archive.name, "[Gameplay] Better Harbours.zip")
        with zipfile.ZipFile(archive) as zipped:
            self.assertEqual(
                sorted(zipped.namelist()),
                ["[Gameplay] Better Harbours/data/config/assets.xml", "[Gameplay] Better Harbours/modinfo.json"],
            )
        self.assertEqual([p.name for p in (self.root / "out").iterdir()], [archive.name])

    def test_not_a_folder(self):
        with self.assertRaises(publish.PublishError):
            publish.zip_mod(self.root / "missing", self.root / "out")


class TestThePlan(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.mod = self.root / "[Gameplay] Better Harbours"
        self.mod.mkdir()
        (self.mod / "modinfo.json").write_text("{}", encoding="utf-8")

    def _plan(self, client, version, summary=""):
        return publish.plan(client, 4169, 9, self.mod, version, self.root / "scratch", as_user="lars",
                            summary=summary)

    def test_a_new_version_is_zipped_and_described(self):
        ready = self._plan(FakeClient("1.0"), "1.1")
        self.assertEqual(ready.version, "1.1")
        self.assertEqual(ready.live_version, "1.0")
        self.assertTrue(ready.archive.is_file())
        self.assertEqual(ready.size, ready.archive.stat().st_size)
        self.assertEqual(len(ready.md5), 32)
        self.assertEqual(ready.as_user, "lars")

    def test_the_live_version_again_is_refused(self):
        with self.assertRaises(publish.PublishError) as caught:
            self._plan(FakeClient("1.1"), "1.1")
        self.assertIn("already the live version", str(caught.exception))

    def test_a_version_the_live_one_is_past_is_refused(self):
        with self.assertRaises(publish.PublishError) as caught:
            self._plan(FakeClient("2.0"), "1.5")
        self.assertIn("newer", str(caught.exception))

    def test_no_version_and_no_mod(self):
        with self.assertRaises(publish.PublishError):
            self._plan(FakeClient(), "  ")
        with self.assertRaises(publish.PublishError):
            self._plan(FakeClient(listed=False), "1.1")

    def test_sending_uploads_the_planned_file_as_active(self):
        client = FakeClient("1.0")
        ready = self._plan(client, "1.1")
        ready.changelog = "Quays."
        result = publish.send(client, 4169, ready)
        self.assertEqual(result.modfile_id, 91)
        self.assertEqual(client.sent, [(4169, 9, "[Gameplay] Better Harbours.zip", "1.1", "Quays.", True)])


    def test_the_summary_is_sent_where_it_differs_and_fits(self):
        """And never an empty one: mod.io requires a summary, and the
        website's stands where none was written here."""
        for live, mine, sent in (
            ("Old words.", "  Bigger quays. ", "Bigger quays."),
            ("Bigger quays.", "Bigger quays.", ""),
            ("Written on the website.", "", ""),
            ("Old words.", "x" * (modio.SUMMARY_LIMIT + 1), ""),
        ):
            with self.subTest(mine=mine[:20]):
                ready = self._plan(FakeClient("1.0", summary=live), "1.1", summary=mine)
                self.assertEqual(ready.summary, sent)
                self.assertEqual(bool(ready.too_long), len(mine) > modio.SUMMARY_LIMIT)

    def test_sending_the_summary_sends_that_alone(self):
        client = FakeClient("1.0", summary="Old words.")
        ready = self._plan(client, "1.1", summary="Bigger quays.")
        self.assertTrue(publish.send_summary(client, 4169, ready))
        self.assertEqual(client.sent, [("summary", 4169, 9, "Bigger quays.")])
        ready.summary = ""
        self.assertFalse(publish.send_summary(client, 4169, ready))
        self.assertEqual(len(client.sent), 1)


class TestSmallThings(unittest.TestCase):
    def test_a_mod_id_from_a_number_or_an_address(self):
        self.assertEqual(publish.mod_id_from(" 3229975 "), 3229975)
        self.assertEqual(publish.mod_id_from("https://mod.io/g/anno-1800/m/x?id=77"), 77)
        self.assertIsNone(publish.mod_id_from("https://mod.io/g/anno-1800/m/better-harbours"))
        self.assertIsNone(publish.mod_id_from(""))

    def test_a_refusal_says_what_to_do_about_ubisoft(self):
        text = publish.why_refused(modio.Refused("A Ubisoft account is required"), "Anno 117: Pax Romana")
        self.assertIn("Ubisoft account is required", text)
        self.assertIn("linked", text)

    def test_the_link_survives_a_loadout_round_trip(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "p.ammproj"
            Loadout(game="anno1800", modio_mod_id=9).save(path)
            self.assertEqual(Loadout.load(path).modio_mod_id, 9)
            # A loadout saved before there was a link to keep.
            Loadout(game="anno1800").save(path)
            self.assertEqual(Loadout.load(path).modio_mod_id, 0)


if __name__ == "__main__":
    unittest.main()
