"""tools/private_scan.py, on commits made in a repository of its own."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import _isolation  # noqa: F401 -- first: keeps the suite out of real files under any runner

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import private_scan  # noqa: E402

TOOL = ROOT / "tools" / "private_scan.py"
LIST = "# mine\nHollowfield\nre:acct-[0-9]{5}\n\n"


class TheList(unittest.TestCase):
    def test_a_term_a_line_matched_without_case_and_comments_skipped(self):
        patterns = private_scan.terms(LIST)
        self.assertEqual(len(patterns), 2)
        self.assertEqual(private_scan.matches(patterns, "in C:/hollowfield/x, acct-12345 and acct-12"),
                         ["hollowfield", "acct-12345"])

    def test_a_plain_term_is_not_a_pattern(self):
        self.assertEqual(private_scan.matches(private_scan.terms("a.b"), "axb a.b"), ["a.b"])


class AScan(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.repo = Path(self._tmp.name) / "repo"
        self.repo.mkdir()
        self.list = Path(self._tmp.name) / "terms.txt"
        self.list.write_text(LIST, encoding="utf-8")
        self.git("init", "-q")

    def git(self, *args, email="dev@example.com") -> str:
        done = subprocess.run(
            ["git", "-c", "user.name=Dev", "-c", "user.email=" + email,
             "-c", "commit.gpgsign=false", "-c", "core.hooksPath=", *args],
            cwd=self.repo, capture_output=True, check=True)
        return done.stdout.decode().strip()

    def commit(self, name: str, text, message="A change", email="dev@example.com") -> str:
        path = self.repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(text, bytes):
            path.write_bytes(text)
        else:
            path.write_text(text, encoding="utf-8")
        self.git("add", name)
        self.git("commit", "-q", "-m", message, email=email)
        return self.git("rev-parse", "HEAD")

    def scan(self, *args, stdin="", listed=True):
        env = dict(os.environ)
        env.pop("STUDIO_PRIVATE_TERMS", None)
        if listed:
            env["STUDIO_PRIVATE_TERMS"] = str(self.list)
        else:
            env["GIT_CONFIG_GLOBAL"] = os.devnull
        return subprocess.run([sys.executable, str(TOOL), *args], cwd=self.repo, env=env,
                              input=stdin.encode(), capture_output=True)

    def test_a_line_added_with_a_term_refuses_the_range(self):
        first = self.commit("readme.md", "Nothing here.\n")
        second = self.commit("docs/notes.md", "Results are in D:\\Hollowfield\\census.\n")
        done = self.scan("--range", "%s..%s" % (first, second))
        self.assertEqual(done.returncode, 1)
        said = done.stderr.decode()
        self.assertIn(second[:8], said)
        self.assertIn("docs/notes.md", said)
        self.assertIn("refused", said)

    def test_a_clean_range_passes(self):
        first = self.commit("readme.md", "Nothing here.\n")
        second = self.commit("readme.md", "Nothing here either.\n")
        self.assertEqual(self.scan("--range", "%s..%s" % (first, second)).returncode, 0)

    def test_the_author_and_message_are_read(self):
        first = self.commit("a.txt", "a\n")
        second = self.commit("b.txt", "b\n", email="me@hollowfield.example")
        self.assertEqual(self.scan("--range", "%s..%s" % (first, second)).returncode, 1)
        third = self.commit("c.txt", "c\n", message="Fix for acct-55555")
        self.assertEqual(self.scan("--range", "%s..%s" % (second, third)).returncode, 1)

    def test_only_what_the_push_adds_is_read(self):
        first = self.commit("a.txt", "Hollowfield\n")
        second = self.commit("b.txt", "fine\n")
        line = "refs/heads/main %s refs/heads/main %s\n" % (second, first)
        self.assertEqual(self.scan(stdin=line).returncode, 0)
        deleted = "(delete) %s refs/heads/old %s\n" % (private_scan.NOTHING, first)
        self.assertEqual(self.scan(stdin=deleted).returncode, 0)

    def test_an_image_is_listed_and_refuses_nothing(self):
        first = self.commit("a.txt", "a\n")
        second = self.commit("docs/shot.png", b"\x89PNG\r\n\x1a\n\x00\x00")
        done = self.scan("--range", "%s..%s" % (first, second))
        self.assertEqual(done.returncode, 0)
        self.assertIn("docs/shot.png", done.stderr.decode())

    def test_no_list_looks_for_nothing_and_says_so(self):
        first = self.commit("a.txt", "a\n")
        second = self.commit("b.txt", "Hollowfield\n")
        done = self.scan("--range", "%s..%s" % (first, second), listed=False)
        self.assertEqual(done.returncode, 0)
        self.assertIn("nothing was looked for", done.stderr.decode())

    def test_one_file_or_what_comes_in(self):
        self.assertEqual(self.scan("--file", "-", stdin="a body naming acct-12345").returncode, 1)
        self.assertEqual(self.scan("--file", "-", stdin="a body").returncode, 0)


if __name__ == "__main__":
    unittest.main()
