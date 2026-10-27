"""The mod.io client, against a fake server: paced, redacting, cancellable."""

from __future__ import annotations

import http.client
import sys
import unittest
from pathlib import Path

import _isolation  # noqa: F401 -- first: keeps the suite out of real files under any runner

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from support import FakeServer, fake_client, modio_mod  # noqa: E402

from anno_modding_studio.core import modio  # noqa: E402


class TestAsking(unittest.TestCase):
    def test_mods_by_ids_goes_to_the_games_own_host_a_hundred_at_a_time(self):
        server = FakeServer([modio_mod(i, "Mod %d" % i) for i in range(1, 251)])
        found = fake_client(server).mods_by_ids(4169, range(1, 251))
        self.assertEqual(len(found), 250)
        self.assertEqual(len(server.calls), 3)
        for url, _headers in server.calls:
            self.assertTrue(url.startswith("https://g-4169.modapi.io/v1/games/4169/mods?"))
        self.assertEqual(found[7].name, "Mod 7")
        self.assertEqual(found[7].modfile_id, 70)

    def test_nothing_asked_is_no_request(self):
        server = FakeServer()
        self.assertEqual(fake_client(server).mods_by_ids(4169, []), {})
        self.assertEqual(server.calls, [])

    def test_search_passes_the_query_and_summarises(self):
        server = FakeServer([modio_mod(1, "Large Fishery"), modio_mod(2, "Small Fishery"), modio_mod(3, "Windmill")])
        found = fake_client(server).search(4169, "fishery")
        self.assertEqual([m.name for m in found], ["Large Fishery", "Small Fishery"])
        self.assertEqual(found[0].submitted_by, "modder")
        self.assertEqual(found[0].downloads, 5)

    def test_every_request_says_who_is_asking(self):
        server = FakeServer()
        fake_client(server).check_key()
        self.assertEqual(server.calls[0][1]["User-Agent"], modio.USER_AGENT)


class TestManners(unittest.TestCase):
    def test_requests_are_paced_to_the_keys_limit(self):
        server = FakeServer([modio_mod(i, "M") for i in range(1, 201)])
        slept = []
        clock = [0.0]

        def sleep(seconds):
            slept.append(seconds)
            clock[0] += seconds

        client = modio.Client("k", fetch=server, sleep=sleep, clock=lambda: clock[0])
        client.mods_by_ids(4169, range(1, 201))
        self.assertEqual(len(server.calls), 2)
        self.assertTrue(any(abs(s - modio.MIN_INTERVAL) < 1e-6 for s in slept))

    def test_a_429_waits_what_the_server_says_and_tries_again(self):
        server = FakeServer([modio_mod(1, "M")], statuses=[(429, "7")])
        slept = []
        clock = [0.0]

        def sleep(seconds):
            slept.append(seconds)
            clock[0] += seconds

        client = modio.Client("k", fetch=server, sleep=sleep, clock=lambda: clock[0])
        self.assertEqual(list(client.mods_by_ids(4169, [1])), [1])
        self.assertIn(7.0, slept)

    def test_a_401_is_the_key_being_refused(self):
        server = FakeServer(statuses=[(401, "")])
        with self.assertRaises(modio.KeyRejected):
            fake_client(server).check_key()

    def test_other_errors_never_carry_the_key(self):
        class Leaky(FakeServer):
            def __call__(self, url, headers):
                self.calls.append((url, headers))
                return modio.Response(404, {}, ("no such thing at %s" % url).encode())

        with self.assertRaises(modio.ModioError) as caught:
            fake_client(Leaky()).check_key()
        self.assertNotIn("sekrit", str(caught.exception))
        self.assertIn("<key>", str(caught.exception))

    def test_a_network_error_is_retried_and_then_reported_without_the_key(self):
        def down(url, headers):
            raise OSError("unreachable via %s" % url)

        with self.assertRaises(modio.ModioError) as caught:
            fake_client(down).check_key()
        self.assertNotIn("sekrit", str(caught.exception))

    def test_an_answer_cut_short_is_retried_and_then_reported_as_a_network_error(self):
        # A10-38: http.client's errors are not OSErrors, and escaped as themselves.
        calls = []

        def cut_short(url, headers):
            calls.append(url)
            raise http.client.IncompleteRead(b"half", 10)

        with self.assertRaises(modio.ModioError):
            fake_client(cut_short).check_key()
        self.assertEqual(len(calls), modio.MAX_ATTEMPTS)

    def test_cancelling_stops_before_the_next_request(self):
        server = FakeServer([modio_mod(i, "M") for i in range(1, 201)])
        asked = []

        def cancelled():
            asked.append(True)
            return len(asked) > 1

        with self.assertRaises(modio.Cancelled):
            fake_client(server, cancelled=cancelled).mods_by_ids(4169, range(1, 201))
        self.assertEqual(len(server.calls), 1)


class TestSummary(unittest.TestCase):
    def test_a_mods_summary_is_read_and_written_on_its_own(self):
        self.assertEqual(modio.summarise({"id": 5, "summary": " Short. "}).summary, "Short.")
        posted = []

        def post(url, headers, body):
            posted.append((url, headers, body))
            return modio.Response(200, {}, b'{"id": 9, "summary": "Bigger quays."}')

        client = modio.Client("k", poster=post, sleep=lambda s: None, clock=lambda: 0.0,
                              token="tok")
        self.assertEqual(client.set_summary(4169, 9, " Bigger quays. ").summary, "Bigger quays.")
        ((url, headers, body),) = posted
        self.assertTrue(url.startswith("https://g-4169.modapi.io/v1/games/4169/mods/9?"))
        # Multipart, with no file: mod.io refused it form-encoded.
        boundary = headers["Content-Type"].split("boundary=", 1)[1]
        self.assertTrue(headers["Content-Type"].startswith("multipart/form-data; boundary="))
        # The summary and nothing else: not the description, which is the
        # website's.
        self.assertEqual(body, (
            '--%(b)s\r\nContent-Disposition: form-data; name="summary"\r\n\r\n'
            'Bigger quays.\r\n--%(b)s--\r\n' % {"b": boundary}).encode())

    def test_a_mod_with_no_file_yet(self):
        summary = modio.summarise({"id": 5, "name": "Bare", "modfile": None, "stats": None})
        self.assertEqual(summary.modfile_id, 0)
        self.assertEqual(summary.version, "")
        self.assertEqual(summary.downloads, 0)


class TestAddressesFromAnswers(unittest.TestCase):
    """An address in mod.io's answer is opened by Windows, whatever it
    names: only a web page of mod.io's is kept."""

    def test_only_mod_io_web_pages(self):
        self.assertEqual(modio.web_url("https://mod.io/g/anno-117/m/x"), "https://mod.io/g/anno-117/m/x")
        self.assertEqual(modio.web_url("https://docs.mod.io/x"), "https://docs.mod.io/x")
        for url in ("file:///C:/x.exe", "search-ms:query=x", "http://mod.io/x",
                    "https://mod.io.example.com/x", "https://evilmod.io/x", "", "ms-settings:"):
            with self.subTest(url=url):
                self.assertEqual(modio.web_url(url, "fallback"), "fallback")

    def test_a_mods_page_address_that_is_not_one_is_dropped(self):
        summary = modio.summarise({"id": 5, "name": "x", "profile_url": "file:///C:/x.exe"})
        self.assertEqual(summary.url, "")


class TestRedirects(unittest.TestCase):
    """urllib copies a request's headers to wherever a redirect points; the
    bearer token must not go with it to another host, or over http."""

    def redirected(self, to):
        import urllib.request

        request = urllib.request.Request(
            "https://g-4169.modapi.io/v1/me", headers={"Authorization": "Bearer secret"})
        handler = modio._CarefulRedirects()
        return handler.redirect_request(request, None, 302, "Found", {}, to)

    def test_the_sign_in_stays_with_its_host(self):
        same = self.redirected("https://g-4169.modapi.io/v1/me/again")
        self.assertEqual(same.get_header("Authorization"), "Bearer secret")
        other = self.redirected("https://elsewhere.example/steal")
        self.assertIsNone(other.get_header("Authorization"))

    def test_a_redirect_away_from_https_is_refused(self):
        import urllib.error

        with self.assertRaises(urllib.error.HTTPError):
            self.redirected("http://g-4169.modapi.io/v1/me")


class TestPageLinks(unittest.TestCase):
    def test_an_address_names_the_game_and_the_mod_by_slug(self):
        link = modio.parse_page("https://mod.io/g/anno-1800/m/better-harbours?tab=files")
        self.assertEqual((link.game_name_id, link.mod_name_id), ("anno-1800", "better-harbours"))
        self.assertEqual(modio.parse_page("mod.io/g/Anno-117-Pax-Romana/m/Curia-Julia").mod_name_id, "curia-julia")

    def test_not_an_address_is_none(self):
        for text in ("Better Harbours", "", "https://mod.io/g/anno-1800", "https://example.com/g/x/m/y"):
            with self.subTest(text=text):
                self.assertIsNone(modio.parse_page(text))

    def test_by_name_id_asks_with_the_slug_and_checks_the_answer(self):
        server = FakeServer([modio_mod(7, "Better Harbours")])
        found = fake_client(server).by_name_id(4169, "Better-Harbours")
        self.assertEqual(found.id, 7)
        self.assertIn("name_id=better-harbours", server.calls[0][0])
        self.assertIsNone(fake_client(server).by_name_id(4169, "nothing-here"))

    def test_dependencies_come_back_as_mods(self):
        server = FakeServer([modio_mod(7, "Host"), modio_mod(8, "Shared Piece"), modio_mod(9, "Other Piece")], deps={7: [8, 9]})
        needs = fake_client(server).dependencies(4169, 7)
        self.assertEqual([(d.id, d.name) for d in needs], [(8, "Shared Piece"), (9, "Other Piece")])
        self.assertEqual(fake_client(server).dependencies(4169, 8), [])

class TestOneMod(unittest.TestCase):
    def test_a_mod_is_asked_for_by_itself(self):
        server = FakeServer([modio_mod(7, "Seven")])
        found = fake_client(server).mod(4169, 7)
        self.assertEqual(found.name, "Seven")
        self.assertTrue(server.calls[0][0].startswith("https://g-4169.modapi.io/v1/games/4169/mods/7?"))

    def test_a_missing_mod_is_none(self):
        self.assertIsNone(fake_client(FakeServer()).mod(4169, 7))

    def test_a_hidden_mod_is_there_for_its_owner_and_nobody_else(self):
        """The listing never returns a hidden mod, not even to its owner, which
        is how publishing to a private test mod failed before this."""
        hidden = dict(modio_mod(7, "Not Yet"), visible=0)
        server = FakeServer([hidden])
        self.assertIsNone(fake_client(server).mod(4169, 7))
        mine = fake_client(server, token="tok-123456").mod(4169, 7)
        self.assertEqual(mine.name, "Not Yet")
        self.assertFalse(mine.visible)
        self.assertEqual(fake_client(server, token="tok-123456").mods_by_ids(4169, [7]), {})


if __name__ == "__main__":
    unittest.main()
