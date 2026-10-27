"""Whether a newer Studio is out: GitHub asked, and the window's note.

``core.studio_release`` asks GitHub's ``releases/latest`` with nothing but
the request; ``ui.studio_release_controller`` asks it once a day behind the
window and says what it found in the status bar.  No test here touches the
network: GitHub's answers are handed in.
"""

from __future__ import annotations

import datetime
import json
import sys
import unittest
from pathlib import Path
from unittest import mock

import _isolation  # first: keeps the suite out of real files under any runner

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from PySide6.QtWidgets import QApplication  # noqa: E402

from anno_modding_studio import REPO_URL, __version__  # noqa: E402
from anno_modding_studio.core import (  # noqa: E402
    dataversion,
    disk,
    modio,
    places,
    studio_release,
)
from anno_modding_studio.ui.main_window import MainWindow  # noqa: E402

_app = QApplication.instance() or QApplication([])

LATEST = "https://api.github.com/repos/aleph-shin/anno-modding-studio/releases/latest"


def _release(tag="v0.2.0", **more) -> dict:
    answer = {
        "tag_name": tag,
        "html_url": "https://github.com/aleph-shin/anno-modding-studio/releases/tag/" + tag,
        "name": "Anno Modding Studio %s (beta)" % tag.lstrip("v"),
        "draft": False,
        "prerelease": False,
        "published_at": "2026-11-02T09:30:00Z",
        "body": "## What changed\n\n- Something.\n",
    }
    answer.update(more)
    return answer


class _GitHub:
    """Stands in for the network: answers one GET, and keeps what was asked."""

    def __init__(self, status=200, body=None, headers=None, raises=None):
        self.status = status
        self.body = json.dumps(body if body is not None else _release()).encode("utf-8") \
            if not isinstance(body, bytes) else body
        self.headers = headers or {}
        self.raises = raises
        self.asked = []

    def __call__(self, url, headers, timeout, most):
        self.asked.append((url, dict(headers), timeout, most))
        if self.raises is not None:
            raise self.raises
        return modio.Response(self.status, self.headers, self.body)


class TestTheQuestion(unittest.TestCase):
    def test_it_asks_for_the_repositorys_latest_release_and_nothing_else(self):
        github = _GitHub()
        studio_release.check(get=github, current="0.1.0")
        (url, headers, timeout, most), = github.asked
        self.assertEqual(url, LATEST)
        self.assertEqual(headers, {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "AnnoModdingStudio/%s" % __version__,
        })
        self.assertNotIn("authorization", {name.lower() for name in headers})
        self.assertLessEqual(timeout, 15)
        self.assertLessEqual(most, 1024 * 1024)

    def test_the_repository_is_read_from_its_address(self):
        self.assertEqual(studio_release.repository(REPO_URL), ("aleph-shin", "anno-modding-studio"))
        self.assertEqual(studio_release.repository("https://github.com/someone/fork.git"), ("someone", "fork"))
        with self.assertRaises(ValueError):
            studio_release.repository("https://github.com/")

    def test_it_goes_through_the_careful_opener(self):
        with mock.patch.object(modio, "_answered", return_value=modio.Response(404, {}, b"")) as answered:
            answer = studio_release.check()
        request, timeout, most = answered.call_args.args
        self.assertEqual(request.full_url, LATEST)
        self.assertEqual((timeout, most), (studio_release.TIMEOUT, studio_release.MOST_ANSWER))
        self.assertEqual(answer.reason, studio_release.NOT_PUBLISHED)

    def test_the_careful_get_refuses_an_address_that_is_not_https(self):
        with mock.patch.object(modio, "_answered") as answered, self.assertRaises(OSError):
            modio.careful_get("http://api.github.com/x", {}, 1.0, 10)
        answered.assert_not_called()


class TestWhatItComesTo(unittest.TestCase):
    def test_a_newer_release_is_its_version_and_its_page(self):
        answer = studio_release.check(get=_GitHub(), current="0.1.0")
        self.assertEqual(answer.state, studio_release.NEWER)
        self.assertEqual(answer.release, studio_release.Release(
            "0.2.0", "v0.2.0", "https://github.com/aleph-shin/anno-modding-studio/releases/tag/v0.2.0",
            "2026-11-02"))

    def test_the_same_or_an_older_release_is_up_to_date(self):
        for tag, current in (("v0.1.0", "0.1.0"), ("v0.1", "0.1.0"), ("v0.1.0", "0.2.0")):
            with self.subTest(tag=tag, current=current):
                answer = studio_release.check(get=_GitHub(body=_release(tag)), current=current)
                self.assertEqual(answer.state, studio_release.UP_TO_DATE)
                self.assertEqual(answer.release.tag, tag)

    def test_a_404_is_could_not_check_which_is_what_a_private_repository_answers(self):
        answer = studio_release.check(get=_GitHub(status=404, body=b'{"message": "Not Found"}'))
        self.assertEqual((answer.state, answer.reason), (studio_release.UNCHECKED, studio_release.NOT_PUBLISHED))
        self.assertIsNone(answer.release)
        self.assertEqual(answer.why, "GitHub shows no published release of the Studio yet.")

    def test_a_rate_limit_is_could_not_check(self):
        for status, headers in ((403, {"x-ratelimit-remaining": "0"}), (429, {}), (403, {"retry-after": "60"})):
            with self.subTest(status=status, headers=headers):
                answer = studio_release.check(get=_GitHub(status=status, body=b"{}", headers=headers))
                self.assertEqual(answer.reason, studio_release.RATE_LIMITED)
                self.assertIn("Try again in an hour", answer.why)

    def test_another_status_is_could_not_check_and_names_it(self):
        answer = studio_release.check(get=_GitHub(status=502, body=b""))
        self.assertEqual(answer.reason, studio_release.REFUSED)
        self.assertIn("502", answer.why)
        self.assertEqual(studio_release.check(get=_GitHub(status=403, body=b"{}")).reason, studio_release.REFUSED)

    def test_offline_is_could_not_check(self):
        import http.client
        import urllib.error

        for error in (urllib.error.URLError("getaddrinfo failed"), TimeoutError("timed out"),
                      http.client.IncompleteRead(b"{"), ConnectionResetError("reset")):
            with self.subTest(error=type(error).__name__):
                answer = studio_release.check(get=_GitHub(raises=error))
                self.assertEqual(answer.reason, studio_release.UNREACHABLE)
                self.assertTrue(answer.why.startswith("GitHub could not be reached ("))

    def test_an_answer_that_names_no_version_is_could_not_check(self):
        for body in (b"<html>", b"\xff\xfe", b"[]", json.dumps(_release("latest")).encode(),
                     json.dumps(_release("")).encode(), json.dumps(_release("v1.0.0-<b>")).encode()):
            with self.subTest(body=body[:30]):
                answer = studio_release.check(get=_GitHub(body=body))
                self.assertEqual(answer.reason, studio_release.UNREADABLE)

    def test_a_draft_or_a_pre_release_is_never_offered(self):
        for more in ({"draft": True}, {"prerelease": True}):
            with self.subTest(**more):
                answer = studio_release.check(get=_GitHub(body=_release("v9.0.0", **more)), current="0.1.0")
                self.assertEqual((answer.state, answer.reason), (studio_release.UNCHECKED, studio_release.NOT_A_RELEASE))

    def test_a_page_elsewhere_is_made_again_from_the_tag(self):
        for page in ("https://evil.example/releases/tag/v0.2.0", "file:///C:/Windows/System32/calc.exe",
                     "http://github.com/aleph-shin/anno-modding-studio/releases/tag/v0.2.0",
                     "https://github.com/someone-else/anno-modding-studio/releases/tag/v0.2.0", None):
            with self.subTest(page=page):
                answer = studio_release.check(get=_GitHub(body=_release(html_url=page)), current="0.1.0")
                self.assertEqual(answer.release.page, REPO_URL + "/releases/tag/v0.2.0")

    def test_a_published_date_that_is_not_one_is_left_out(self):
        answer = studio_release.check(get=_GitHub(body=_release(published_at="soon")), current="0.1.0")
        self.assertEqual(answer.release.published, "")


class TestVersions(unittest.TestCase):
    def test_newer_compares_numbers_and_ignores_a_trailing_zero(self):
        self.assertTrue(studio_release.is_newer("0.10.0", "0.9.9"))
        self.assertTrue(studio_release.is_newer("1.0", "0.9"))
        self.assertFalse(studio_release.is_newer("0.1", "0.1.0"))
        self.assertFalse(studio_release.is_newer("0.1.0", "0.1"))
        self.assertFalse(studio_release.is_newer("", "0.1.0"))
        self.assertFalse(studio_release.is_newer("0.2.0", ""))

    def test_a_tag_is_a_version_with_or_without_its_v(self):
        self.assertEqual(studio_release.version_of("v0.2.0"), "0.2.0")
        self.assertEqual(studio_release.version_of("1.4"), "1.4")
        for tag in ("", "latest", "v0.2.0-beta", "v0.2.0 ", "0.2.0/../x", None):
            with self.subTest(tag=tag):
                self.assertEqual(studio_release.version_of(tag), "0.2.0" if tag == "v0.2.0 " else "")

    def test_updated_from_is_an_older_version_only(self):
        self.assertEqual(studio_release.updated_from("0.1.0", "0.2.0"), "0.1.0")
        self.assertEqual(studio_release.updated_from("", "0.2.0"), "")  # a first start
        self.assertEqual(studio_release.updated_from("0.2.0", "0.2.0"), "")
        self.assertEqual(studio_release.updated_from("0.3.0", "0.2.0"), "")  # gone back a version
        self.assertEqual(studio_release.updated_from("<b>0.1</b>", "0.2.0"), "")

    def test_a_skipped_version_is_not_said_and_a_later_one_is(self):
        release = studio_release.Release("0.3.0", "v0.3.0", "")
        self.assertTrue(studio_release.wants_saying(release, "", current="0.1.0"))
        self.assertFalse(studio_release.wants_saying(release, "0.3.0", current="0.1.0"))
        self.assertTrue(studio_release.wants_saying(release, "0.2.0", current="0.1.0"))
        self.assertFalse(studio_release.wants_saying(release, "", current="0.3.0"))
        self.assertFalse(studio_release.wants_saying(None))


class TestOnceADay(unittest.TestCase):
    NOW = datetime.datetime(2026, 10, 18, 12, 0, tzinfo=datetime.timezone.utc)

    def test_it_is_due_a_day_after_the_last_time_or_with_no_last_time(self):
        self.assertTrue(studio_release.is_due("", self.NOW))
        self.assertTrue(studio_release.is_due("not a time", self.NOW))
        self.assertFalse(studio_release.is_due("2026-10-18T08:00:00Z", self.NOW))
        self.assertFalse(studio_release.is_due("2026-10-17T12:00:01Z", self.NOW))
        self.assertTrue(studio_release.is_due("2026-10-17T12:00:00Z", self.NOW))

    def test_a_clock_put_back_does_not_hold_it_off(self):
        self.assertTrue(studio_release.is_due("2027-01-01T00:00:00Z", self.NOW))

    def test_the_stamp_reads_back(self):
        written = studio_release.stamp(self.NOW)
        self.assertEqual(written, "2026-10-18T12:00:00Z")
        self.assertFalse(studio_release.is_due(written, self.NOW + datetime.timedelta(hours=23)))
        self.assertTrue(studio_release.is_due(written, self.NOW + datetime.timedelta(hours=24)))


class TestTheVersionBefore(unittest.TestCase):
    """dataversion names the newest version that used the Studio's folders,
    which, asked before this one writes itself in, is the one before an
    update."""

    def test_it_is_the_newest_any_folder_names_and_none_on_a_first_start(self):
        folders = places.data_folders()
        for folder in folders:
            (folder / dataversion.FILE_NAME).unlink(missing_ok=True)
        self.addCleanup(dataversion.record)
        self.assertEqual(dataversion.last_used_by(), "")
        versions = ("0.1.0", "0.1.2")
        for at, folder in enumerate(folders):
            folder.mkdir(parents=True, exist_ok=True)
            disk.write_json(folder / dataversion.FILE_NAME, {
                "data_version": dataversion.DATA_VERSION,
                "app_version": versions[min(at, len(versions) - 1)],
            })
        self.assertEqual(dataversion.last_used_by(), versions[min(len(folders), len(versions)) - 1])


class TestTheWindow(unittest.TestCase):
    def setUp(self):
        self.window = MainWindow()
        self.addCleanup(_isolation.forget_settings)
        self.addCleanup(self.window.deleteLater)
        self.window.editor.search._debounce.stop()
        self.studio = self.window.studio_release
        # Nothing here starts a thread: the answer is handed in.
        started = mock.patch("anno_modding_studio.ui.workers.StudioReleaseWorker.start")
        self.started = started.start()
        self.addCleanup(started.stop)

    def newer(self, version="9.0.0"):
        return studio_release.Answer(studio_release.NEWER, studio_release.Release(
            version, "v" + version, REPO_URL + "/releases/tag/v" + version, "2026-11-02"))

    def links(self) -> str:
        return self.studio.notice.links.text()

    def test_it_is_asked_once_a_day_and_not_when_switched_off(self):
        self.assertTrue(self.studio.is_on())
        self.assertTrue(self.studio.check_if_due())
        self.started.assert_called_once()
        self.studio.worker = None
        self.assertFalse(self.studio.check_if_due())  # asked today already
        self.window.settings.remove(self.studio.CHECKED_KEY)
        self.window.preferences.set_checks_for_studio(False)
        self.assertFalse(self.studio.check_if_due())
        self.assertEqual(self.started.call_count, 1)

    def test_the_help_menu_asks_now_whatever_the_day_and_the_setting(self):
        self.window.preferences.set_checks_for_studio(False)
        self.window.settings.setValue(self.studio.CHECKED_KEY, studio_release.stamp())
        self.window.release_check_action.trigger()
        self.started.assert_called_once()
        self.assertIn("Checking GitHub", self.window.status_label.text())
        self.studio.worker = None
        self.studio.on_answer(studio_release.Answer(
            studio_release.UNCHECKED, reason=studio_release.NOT_PUBLISHED,
            why="GitHub shows no published release of the Studio yet."))
        self.assertEqual(self.window.status_label.text(),
                         "Couldn't check for a newer version of the Studio. "
                         "GitHub shows no published release of the Studio yet.")
        self.assertTrue(self.studio.notice.isHidden())

    def test_could_not_check_behind_the_window_is_quiet(self):
        self.window.status_label.setText("Something else.")
        self.studio.check_if_due()
        self.studio.worker = None
        self.studio.on_answer(studio_release.Answer(studio_release.UNCHECKED, reason=studio_release.NOT_PUBLISHED))
        self.assertEqual(self.window.status_label.text(), "Something else.")
        self.assertTrue(self.studio.notice.isHidden())

    def test_up_to_date_is_said_only_when_asked(self):
        self.studio.check_now()
        self.studio.worker = None
        self.studio.on_answer(studio_release.Answer(studio_release.UP_TO_DATE, studio_release.Release(
            __version__, "v" + __version__, "")))
        self.assertEqual(self.window.status_label.text(),
                         "Anno Modding Studio %s is the newest version." % __version__)
        self.assertTrue(self.studio.notice.isHidden())

    def test_a_newer_version_gets_a_note_with_its_page_and_how_to_update(self):
        self.studio.on_answer(self.newer())
        notice = self.studio.notice
        self.assertFalse(notice.isHidden())
        self.assertIn(notice, self.window.statusBar().findChildren(type(notice)))
        self.assertEqual(notice.label.text(),
                         "Anno Modding Studio 9.0.0 is out. Update your checkout with git pull to run it.")
        self.assertEqual(notice.addresses, {"page": REPO_URL + "/releases/tag/v9.0.0"})
        self.assertIn("Skip this version", self.links())
        self.assertEqual(notice.label.toolTip(), "Published 02 Nov 2026.")
        with mock.patch.object(sys, "frozen", True, create=True):
            self.studio.on_answer(self.newer())
        self.assertEqual(notice.label.text(), "Anno Modding Studio 9.0.0 is out. Unzip it into a "
                                              "new folder, and your data carries over.")
        self.assertIn("How to update", self.links())
        self.assertTrue(notice.addresses["guide"].endswith("/docs/guide.md#install-and-run"))
        self.assertIn("/blob/v%s/" % __version__, notice.addresses["guide"])

    def test_following_the_page_opens_it_and_downloads_nothing(self):
        from anno_modding_studio.ui import studio_release_controller

        self.studio.on_answer(self.newer())
        with mock.patch.object(studio_release_controller.QDesktopServices, "openUrl") as opened:
            self.studio.notice.links.linkActivated.emit("page")
        self.assertEqual(opened.call_args.args[0].toString(), REPO_URL + "/releases/tag/v9.0.0")

    def test_skipping_a_version_keeps_it_quiet_until_a_later_one(self):
        self.studio.on_answer(self.newer("9.0.0"))
        self.studio.notice.links.linkActivated.emit("skip")
        self.assertTrue(self.studio.notice.isHidden())
        self.assertEqual(self.studio.skipped(), "9.0.0")
        self.assertIn("won't be shown again", self.window.status_label.text())
        self.studio.on_answer(self.newer("9.0.0"))
        self.assertTrue(self.studio.notice.isHidden())
        # Asked for from the Help menu, it is said all the same.
        self.studio.check_now()
        self.studio.worker = None
        self.studio.on_answer(self.newer("9.0.0"))
        self.assertFalse(self.studio.notice.isHidden())
        self.studio.hide()
        self.studio.on_answer(self.newer("9.1.0"))
        self.assertFalse(self.studio.notice.isHidden())
        self.assertIn("9.1.0", self.studio.notice.label.text())

    def test_hiding_the_note_keeps_nothing(self):
        self.studio.on_answer(self.newer())
        self.studio.notice.links.linkActivated.emit("hide")
        self.assertTrue(self.studio.notice.isHidden())
        self.assertEqual(self.studio.skipped(), "")

    def test_a_newer_version_found_before_is_said_at_the_next_start_without_asking(self):
        self.studio.on_answer(self.newer())
        self.studio.hide()
        self.window.settings.setValue(self.studio.CHECKED_KEY, studio_release.stamp())
        with mock.patch("anno_modding_studio.ui.studio_release_controller.QTimer.singleShot") as later:
            self.studio.after_start(__version__)
        self.assertFalse(self.studio.notice.isHidden())
        self.assertIn("9.0.0", self.studio.notice.label.text())
        later.assert_called_once_with(self.studio.START_DELAY_MS, self.studio.check_if_due)
        self.started.assert_not_called()
        # Once GitHub says this is the newest, it is forgotten.
        self.studio.on_answer(studio_release.Answer(studio_release.UP_TO_DATE))
        self.assertTrue(self.studio.notice.isHidden())
        self.assertIsNone(self.studio.remembered())

    def test_a_kept_release_that_is_not_one_is_not_said(self):
        for kept in ("9.0.0|v8.0.0|", "9.0.0", "x|vx|", "9.0.0|v9.0.0|2026|extra"):
            with self.subTest(kept=kept):
                self.window.settings.setValue(self.studio.NEWEST_KEY, kept)
                self.assertIsNone(self.studio.remembered())

    def test_the_first_start_after_an_update_says_so_once(self):
        with mock.patch("anno_modding_studio.ui.studio_release_controller.QTimer.singleShot"):
            self.studio.after_start("0.0.9")
        notice = self.studio.notice
        self.assertFalse(notice.isHidden())
        self.assertEqual(notice.label.text(), "Updated to %s from 0.0.9." % __version__)
        self.assertEqual(notice.addresses, {"page": REPO_URL + "/releases/tag/v" + __version__})
        self.assertIn("Release notes", self.links())
        self.assertNotIn("Skip", self.links())
        self.studio.hide()
        for previous in ("", __version__, "99.0.0"):
            with self.subTest(previous=previous), \
                    mock.patch("anno_modding_studio.ui.studio_release_controller.QTimer.singleShot"):
                self.studio.after_start(previous)
                self.assertTrue(notice.isHidden())

    def test_the_setting_is_a_checkbox_on_the_general_tab(self):
        from support import no_credentials

        from anno_modding_studio.ui.settings_dialog import TAB_GENERAL, SettingsDialog

        dialog = SettingsDialog(self.window, self.window, store=no_credentials())
        self.addCleanup(dialog.deleteLater)
        box = dialog.release_check
        self.assertTrue(dialog.pages.widget(TAB_GENERAL).isAncestorOf(box))
        self.assertEqual(box.text(), "Check fo&r a newer Studio once a day")
        self.assertTrue(box.isChecked())
        box.setChecked(False)
        self.assertFalse(self.studio.is_on())
        box.setChecked(True)
        self.assertTrue(self.studio.is_on())

    def test_a_check_still_running_is_waited_for_as_the_window_closes(self):
        worker = mock.Mock()
        self.studio.worker = worker
        self.studio.stop()
        worker.wait.assert_called_once()
        self.studio.worker = None


if __name__ == "__main__":
    unittest.main()
