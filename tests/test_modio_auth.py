"""Signing in to mod.io and writing to it, against a fake that answers as it does."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import _isolation  # noqa: F401 -- first: keeps the suite out of real files under any runner

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from support import reverse_bytes  # noqa: E402

from anno_modding_studio.core import modio, secrets  # noqa: E402


class FakeModio:
    """GET and POST, remembering everything, answering like mod.io."""

    def __init__(self):
        self.gets = []
        self.posts = []
        self.refuse_uploads = ""
        self.token_dead = False

    def _json(self, status, payload):
        return modio.Response(status, {}, json.dumps(payload).encode())

    def get(self, url, headers):
        self.gets.append((url, headers))
        path = urlparse(url).path
        if "Authorization" in headers and self.token_dead:
            return self._json(401, {"error": {"code": 401, "error_ref": 11001, "message": "expired"}})
        if path.endswith("/authenticate/terms"):
            return self._json(200, {
                "plaintext": "Be nice.",
                "buttons": {"agree": {"text": "I Agree"}, "disagree": {"text": "No, Thanks"}},
                "links": {"terms": {"url": "https://mod.io/terms"}, "privacy": {"url": "https://mod.io/privacy"}},
            })
        if path.endswith("/me"):
            return self._json(200, {"id": 5, "username": "lars"})
        if path.endswith("/me/mods"):
            game = parse_qs(urlparse(url).query).get("game_id", [""])[0]
            data = [{"id": 9, "name": "Better Harbours", "modfile": {"id": 90, "version": "1.0"}}] if game == "4169" else []
            return self._json(200, {"data": data, "result_count": len(data), "result_total": len(data)})
        if "/mods" in path:
            return self._json(200, {"data": [{"id": 9, "name": "Better Harbours", "modfile": {"id": 90, "version": "1.0"}}], "result_count": 1, "result_total": 1})
        return self._json(200, {"data": [], "result_count": 0, "result_total": 0})

    def post(self, url, headers, body):
        self.posts.append((url, headers, body))
        path = urlparse(url).path
        if path.endswith("/oauth/emailrequest"):
            return self._json(200, {"code": 200, "message": "Enter the 5-digit code sent to your email."})
        if path.endswith("/oauth/emailexchange"):
            fields = parse_qs(body.decode())
            if fields.get("terms_agreed") != ["true"]:
                return self._json(403, {"error": {"code": 403, "error_ref": 11074, "message": "agree first"}})
            return self._json(200, {"access_token": "tok-123", "date_expires": 1800000000})
        if path.endswith("/oauth/logout"):
            return self._json(200, {"code": 200, "message": "bye"})
        if path.endswith("/files"):
            if self.refuse_uploads:
                return self._json(403, {"error": {"code": 403, "error_ref": 15000, "message": self.refuse_uploads}})
            return self._json(201, {"id": 91, "version": "1.1", "filename": "up.zip", "filesize": 3})
        return self._json(404, {"error": {"message": "no"}})


def _client(fake, token="") -> modio.Client:
    clock = [0.0]

    def sleep(seconds):
        clock[0] += seconds

    return modio.Client("sekrit", fetch=fake.get, poster=fake.post, sleep=sleep, clock=lambda: clock[0], token=token)


class TestWhichHost(unittest.TestCase):
    """mod.io has retired api.mod.io: a sign-in there is refused with error
    11001, whatever the key. Every call goes to a game's own host."""

    def test_account_calls_go_to_a_game_host_even_without_a_game(self):
        fake = FakeModio()
        _client(fake).terms()
        _client(fake).request_code("a@b.c")
        _client(fake, token="t").me()
        for url in [fake.gets[0][0], fake.posts[0][0], fake.gets[1][0]]:
            self.assertTrue(url.startswith("https://g-4169.modapi.io/v1/"), url)

    def test_a_client_made_for_a_game_uses_that_game(self):
        fake = FakeModio()
        modio.Client("k", fetch=fake.get, poster=fake.post, sleep=lambda s: None, clock=lambda: 0.0, game_id=11358).terms()
        self.assertTrue(fake.gets[0][0].startswith("https://g-11358.modapi.io/v1/"))

    def test_a_refused_key_carries_what_modio_said(self):
        class Retired(FakeModio):
            def get(self, url, headers):
                return self._json(401, {"error": {"code": 401, "error_ref": 11001, "message": "using the deprecated api.mod.io domain"}})

        with self.assertRaises(modio.KeyRejected) as caught:
            _client(Retired()).terms()
        self.assertIn("deprecated api.mod.io domain", str(caught.exception))


class TestSigningIn(unittest.TestCase):
    def test_the_terms_come_with_their_buttons_and_links(self):
        terms = _client(FakeModio()).terms()
        self.assertEqual(terms.text, "Be nice.")
        self.assertEqual((terms.agree, terms.disagree), ("I Agree", "No, Thanks"))
        self.assertEqual(terms.terms_url, "https://mod.io/terms")

    def test_asking_for_a_code_posts_the_address_with_the_key(self):
        fake = FakeModio()
        said = _client(fake).request_code(" a@b.c ")
        self.assertIn("code", said)
        url, headers, body = fake.posts[0]
        self.assertIn("api_key=sekrit", url)
        self.assertEqual(parse_qs(body.decode()), {"email": ["a@b.c"]})
        self.assertEqual(headers["Content-Type"], "application/x-www-form-urlencoded")

    def test_the_code_becomes_a_token_only_with_the_terms_agreed(self):
        fake = FakeModio()
        token = _client(fake).exchange_code("12345")
        self.assertEqual((token.access_token, token.expires), ("tok-123", 1800000000))
        with self.assertRaises(modio.Refused) as caught:
            _client(fake).exchange_code("12345", terms_agreed=False)
        self.assertEqual(caught.exception.error_ref, 11074)

    def test_a_token_goes_in_the_header_and_never_in_an_error(self):
        fake = FakeModio()
        client = _client(fake, token="tok-123")
        self.assertEqual(client.me().username, "lars")
        self.assertEqual(fake.gets[-1][1]["Authorization"], "Bearer tok-123")

        class Leaky(FakeModio):
            def get(self, url, headers):
                return modio.Response(500, {}, ("broken " + headers["Authorization"]).encode())

        with self.assertRaises(modio.ModioError) as caught:
            _client(Leaky(), token="tok-123").me()
        self.assertNotIn("tok-123", str(caught.exception))

    def test_a_dead_token_is_signed_out_not_a_bad_key(self):
        fake = FakeModio()
        fake.token_dead = True
        with self.assertRaises(modio.SignedOut):
            _client(fake, token="tok-123").me()

    def test_my_mods_are_asked_for_this_game(self):
        fake = FakeModio()
        mine = _client(fake, token="t").my_mods(4169)
        self.assertEqual([m.name for m in mine], ["Better Harbours"])
        self.assertEqual(_client(fake, token="t").my_mods(11358), [])

    def test_a_sign_in_post_is_not_sent_twice_when_it_may_have_arrived(self):
        """A reset or a 5xx leaves it open whether mod.io took the request: sent
        again, it can email a second code or spend a code twice (A11-43)."""
        class Flaky(FakeModio):
            def __init__(self, failure):
                super().__init__()
                self.failure = failure

            def post(self, url, headers, body):
                self.posts.append((url, headers, body))
                if self.failure == "reset":
                    raise OSError("connection reset")
                return self._json(503, {"error": {"code": 503, "message": "busy"}})

        for failure in ("reset", "503"):
            for ask in (lambda c: c.request_code("a@b.c"), lambda c: c.exchange_code("12345")):
                with self.subTest(failure=failure):
                    fake = Flaky(failure)
                    with self.assertRaises(modio.ModioError):
                        ask(_client(fake))
                    self.assertEqual(len(fake.posts), 1)

    def test_a_sign_in_post_turned_away_for_the_rate_is_sent_again(self):
        """A 429 says mod.io did not take the request, so sending it again is safe."""
        class Busy(FakeModio):
            def post(self, url, headers, body):
                if not self.posts:
                    self.posts.append((url, headers, body))
                    return self._json(429, {"error": {"code": 429, "message": "slow down"}})
                return super().post(url, headers, body)

        fake = Busy()
        self.assertIn("code", _client(fake).request_code("a@b.c"))
        self.assertEqual(len(fake.posts), 2)

    def test_logging_out_posts_and_swallows_a_refusal(self):
        fake = FakeModio()
        _client(fake, token="t").logout()
        self.assertTrue(fake.posts[0][0].endswith("/oauth/logout?api_key=sekrit"))


class TestHowLongASignInLasts(unittest.TestCase):
    """The email-code token runs out at the day mod.io gives with it, and
    mod.io has no refresh for it: past that day it is no sign-in."""

    NOW = 1_800_000_000
    DAY = 24 * 3600

    def held(self, expires):
        return secrets.Credentials(api_key="k", token="tok-123", token_expires=expires)

    def test_a_token_past_its_day_is_not_sent(self):
        gone = self.held(self.NOW - 1)
        self.assertTrue(gone.expired(self.NOW))
        self.assertFalse(gone.signed_in(self.NOW))
        self.assertEqual(gone.live_token(self.NOW), "")

    def test_a_good_one_is_and_says_when_it_runs_out(self):
        good = self.held(self.NOW + 100 * self.DAY)
        self.assertEqual(good.live_token(self.NOW), "tok-123")
        self.assertFalse(good.runs_out_soon(self.NOW))
        soon = self.held(self.NOW + 13 * self.DAY)
        self.assertTrue(soon.signed_in(self.NOW))
        self.assertTrue(soon.runs_out_soon(self.NOW))

    def test_one_whose_day_is_not_known_is_good_until_modio_says(self):
        unknown = self.held(0)
        self.assertTrue(unknown.signed_in(self.NOW))
        self.assertFalse(unknown.runs_out_soon(self.NOW))
        self.assertFalse(secrets.Credentials(api_key="k").signed_in(self.NOW))

    def test_dropping_the_token_keeps_the_key_and_the_address(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = secrets.Store(Path(tmp) / "c.bin", wrap=reverse_bytes, unwrap=reverse_bytes)
            store.save(secrets.Credentials(api_key="k", token="t", token_expires=5, email="a@b.c"))
            left = store.drop_token()
            self.assertEqual(store.load(), left)
            self.assertEqual((left.api_key, left.token, left.token_expires, left.email),
                             ("k", "", 0, "a@b.c"))


class TestUploading(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.archive = Path(self._tmp.name) / "[Gameplay] Better Harbours.zip"
        self.archive.write_bytes(b"PK-zip-bytes")

    def test_the_file_goes_up_as_multipart_with_version_changelog_and_hash(self):
        fake = FakeModio()
        result = _client(fake, token="t").add_modfile(4169, 9, self.archive, "1.1", "Fixed the quay.")
        self.assertEqual((result.id, result.modfile_id, result.version), (9, 91, "1.1"))
        url, headers, body = fake.posts[0]
        self.assertTrue(url.startswith("https://g-4169.modapi.io/v1/games/4169/mods/9/files?"))
        self.assertTrue(headers["Content-Type"].startswith("multipart/form-data; boundary="))
        boundary = headers["Content-Type"].split("boundary=")[1].encode()
        self.assertIn(boundary, body)
        self.assertIn(b'name="version"\r\n\r\n1.1', body)
        self.assertIn(b'name="changelog"\r\n\r\nFixed the quay.', body)
        self.assertIn(b'name="active"\r\n\r\ntrue', body)
        self.assertIn(b'name="filedata"; filename="[Gameplay] Better Harbours.zip"', body)
        self.assertIn(b"PK-zip-bytes", body)
        self.assertIn(b'name="filehash"', body)

    def test_a_refusal_carries_modios_own_words(self):
        fake = FakeModio()
        fake.refuse_uploads = "A Ubisoft account is required in order to add mods."
        with self.assertRaises(modio.Refused) as caught:
            _client(fake, token="t").add_modfile(4169, 9, self.archive, "1.1")
        self.assertIn("Ubisoft", str(caught.exception))
        self.assertEqual(caught.exception.error_ref, 15000)
        # A write is never retried on its own: one upload, one request.
        self.assertEqual(len(fake.posts), 1)


if __name__ == "__main__":
    unittest.main()
