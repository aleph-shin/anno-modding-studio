"""The unsubscribe probe's verdicts: what ``--check`` says the game's client
did, from a record of its own making and a client that is stubbed -- never
mod.io, never the game's folders."""

from __future__ import annotations

import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import _isolation  # noqa: F401 -- first: keeps the suite out of real files under any runner

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import unsubscribe_probe  # noqa: E402

ME, MOD = 1234567, 123456


class TestWhatTheClientDidAfterAnUnsubscribeMadeElsewhere(unittest.TestCase):
    """The account is unsubscribed, the game has started since, and the
    client still holds its copy for the account."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.record = Path(self._tmp.name) / "unsubscribe-probe.json"

    def verdict(self, listed_before, known) -> str:
        self.record.write_text(json.dumps({
            "mod": MOD, "name": "A Mod", "folder": str(Path(self._tmp.name) / "A Mod"),
            "holders": [ME], "account": ME, "at": 0, "log": 100.0,
            "client_listed": listed_before, "how": "api",
        }), encoding="utf-8")
        talk = SimpleNamespace(subscriptions=lambda _game: set())
        said = io.StringIO()
        with mock.patch.object(unsubscribe_probe, "RECORD", self.record), \
                mock.patch.object(unsubscribe_probe, "entry",
                                  return_value=SimpleNamespace(holders=(ME,))), \
                mock.patch.object(unsubscribe_probe, "log_stamp", return_value=200.0), \
                mock.patch.object(unsubscribe_probe, "client", return_value=talk), \
                mock.patch.object(unsubscribe_probe.modio_cache, "game_subscriptions",
                                  return_value=known), \
                mock.patch("sys.stdout", said):
            unsubscribe_probe.check()
        return said.getvalue().split("RESULT: ", 1)[1]

    def test_a_list_without_the_mod_that_had_it_before_is_a_client_that_saw_it_go(self):
        self.assertIn("saw it go", self.verdict(True, {1, 2}))

    def test_a_list_that_still_has_the_mod_is_a_client_that_has_not_fetched(self):
        self.assertIn("has not fetched your subscriptions", self.verdict(True, {MOD}))

    def test_no_list_kept_is_not_a_client_that_fetched(self):
        # The client writes its list back empty after a start that fetched
        # nothing: no list says it has not looked, not that the mod went.
        for listed_before in (True, False, None):
            with self.subTest(listed_before=listed_before):
                verdict = self.verdict(listed_before, None)
                self.assertNotIn("the client fetched your subscriptions", verdict)
                self.assertIn("keeps no list", verdict)
                self.assertIn("check again", verdict)


if __name__ == "__main__":
    unittest.main()
