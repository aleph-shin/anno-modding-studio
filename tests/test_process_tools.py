"""The tools a PR's process rests on: tools/ledger.py, tools/pr_description.py
and tools/ingame_pack.py (docs/standards/pr-checklist.md)."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import _isolation  # noqa: F401 -- first: keeps the suite out of real files under any runner

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import ingame_pack  # noqa: E402
import ledger  # noqa: E402
import pr_description  # noqa: E402

TABLE = """# Ledger

| ID | Added | Impact | Where | What | Kept |
|---|---|---|---|---|---|
%s
"""


def ledger_text(*rows: str) -> str:
    return TABLE % "\n".join(rows)


class Ledger(unittest.TestCase):
    def test_a_well_formed_ledger_has_no_problems(self):
        text = ledger_text("| L1 | 2026-11-21 | user | `ui/x.py` | A thing. | - |",
                           "| L2 | 2026-11-20 | cosmetic | a screen | Another \\| thing. | 0.2.0 |")
        self.assertEqual(ledger.problems(text), [])
        self.assertEqual(ledger.summary(text), "2 open (1 user, 1 cosmetic), the oldest added 2026-11-20")

    def test_an_empty_table_is_nothing_open(self):
        self.assertEqual(ledger.problems(ledger_text()), [])
        self.assertEqual(ledger.summary(ledger_text()), "nothing open")

    def test_finds_each_kind_of_bad_row(self):
        text = ledger_text("| 1 | yesterday | users | | x | - |",
                           "| L2 | 2026-11-21 | user | here | x |",
                           "| L3 | 2026-11-21 | user | here | x | soon |")
        said = " ".join(problem for _line, problem in ledger.problems(text))
        for expected in ("not L and a number", "not a date", "not one of", "says nothing",
                         "has 5 cells", "is not -"):
            self.assertIn(expected, said)

    def test_a_row_kept_at_three_releases_is_a_problem(self):
        text = ledger_text("| L1 | 2026-01-01 | user | here | x | 0.2.0, 0.3.0, 0.4.0 |")
        self.assertIn("kept at 3 releases", ledger.problems(text)[0][1])
        self.assertEqual(ledger.problems(ledger_text("| L1 | 2026-01-01 | user | here | x | 0.2.0, 0.3.0 |")), [])

    def test_an_id_used_twice_is_a_problem(self):
        text = ledger_text("| L1 | 2026-01-01 | user | here | x | - |",
                           "| L1 | 2026-01-02 | user | there | y | - |")
        self.assertIn("used on line", ledger.problems(text)[0][1])

    def test_a_release_needs_every_row_kept_for_it(self):
        text = ledger_text("| L1 | 2026-01-01 | user | here | x | 0.2.0 |",
                           "| L2 | 2026-01-01 | user | here | y | - |")
        self.assertEqual([row.id for row in ledger.untriaged(text, "0.2.0")], ["L2"])
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "ledger.md"
            path.write_text(text, encoding="utf-8")
            self.assertEqual(ledger.main(["--release", "--ledger", str(path), "--version", "0.2.0"]), 1)
            path.write_text(text.replace("| y | - |", "| y | 0.2.0 |"), encoding="utf-8")
            self.assertEqual(ledger.main(["--release", "--ledger", str(path), "--version", "0.2.0"]), 0)

    def test_the_repository_ledger_is_well_formed(self):
        self.assertEqual(ledger.problems(ledger.LEDGER.read_text(encoding="utf-8")), [])


GOOD = """## What and why

A thing was wrong; now it is not.

## AI assistance

ai-generated: the model wrote it.

## Verified

- **Suite:** 6600 passed, both games and the loader installed
- **In the Studio:** opened the editor, saw the row marked
- **In the game:** Not needed: no export changes.
- **Ledger:** none

## Exceptions

None.
"""


class PrDescription(unittest.TestCase):
    def test_a_filled_description_passes(self):
        self.assertEqual(pr_description.problems(GOOD), [])

    def test_the_template_as_it_is_fails_on_every_line(self):
        template = (ROOT / ".github" / "pull_request_template.md").read_text(encoding="utf-8")
        said = pr_description.problems(template)
        self.assertIn('nothing under "## What and why"', said)
        self.assertIn('nothing under "## AI assistance"', said)
        for label in pr_description.VERIFIED:
            self.assertIn('"**%s:**" under "Verified" says nothing' % label, said)
        self.assertFalse([item for item in said if "Exceptions" in item or "exception" in item])

    def test_a_missing_heading_and_level(self):
        body = GOOD.replace("## Exceptions\n\nNone.\n", "").replace("ai-generated", "lots")
        said = pr_description.problems(body)
        self.assertIn('no "## Exceptions" heading', said)
        self.assertTrue(any("names none of the levels" in item for item in said))

    def test_a_line_left_as_a_comment_says_nothing(self):
        body = GOOD.replace("opened the editor, saw the row marked", "<!-- the steps -->")
        self.assertEqual(pr_description.problems(body), ['"**In the Studio:**" under "Verified" says nothing'])

    def test_an_exception_names_who_allowed_it(self):
        body = GOOD.replace("None.", "Raised the ruff count by one.")
        self.assertTrue(pr_description.problems(body)[0].startswith("an exception names nobody"))
        allowed = GOOD.replace("None.", 'Raised the ruff count by one, allowed by the maintainer: "fine".')
        self.assertEqual(pr_description.problems(allowed), [])

    def test_an_empty_description(self):
        self.assertIn("empty", pr_description.problems("")[0])

    def test_a_bot_is_not_checked(self):
        with mock.patch.dict("os.environ", {"PR_AUTHOR": "dependabot[bot]", "PR_BODY": ""}):
            self.assertEqual(pr_description.main([]), 0)


class IngamePack(unittest.TestCase):
    def setUp(self):
        self.folder = Path(tempfile.mkdtemp()) / "a-pack"
        ingame_pack.new(self.folder, "anno117", "A pack", "#1")
        self.pack = json.loads((self.folder / ingame_pack.PACK).read_text(encoding="utf-8"))

    def test_a_marker_matches_nothing_and_names_its_case(self):
        text = ingame_pack.marker(self.pack, "x", condition="//A[@b=\"c\"]")
        self.assertIn('Path="/AssetList/Pack_a_pack_x"', text)
        self.assertIn('Condition="//A[@b=&quot;c&quot;]"', text)
        self.assertIn('GUID="123" Path="/Values/Pack_a_pack_y"', ingame_pack.marker(self.pack, "y", guid="123"))
        both = ingame_pack.pair(self.pack, "z", "//A")
        self.assertIn("Pack_a_pack_z_yes", both)
        self.assertIn('Condition="!//A"', both)

    def test_a_probe_mod_is_written_as_an_export_writes_one(self):
        patch = "<ModOps>\n" + ingame_pack.marker(self.pack, "a_ran") + "</ModOps>\n"
        root = ingame_pack.add_mod(self.folder, self.pack, "Probe A", "studio-probe-a", patch,
                                   load_after=["*"])
        self.assertEqual(root.name, "[Test] Probe A")
        info = json.loads((root / "modinfo.json").read_text(encoding="utf-8"))
        self.assertEqual((info["ModID"], info["Dependencies"]["LoadAfter"]), ("studio-probe-a", ["*"]))
        self.assertIn("Pack_a_pack_a_ran", (root / "data/base/config/export/assets.xml").read_text(encoding="utf-8"))

    def test_judges_each_kind_of_question(self):
        pack = dict(self.pack, questions=[
            {"case": "a", "asks": "A?", "expect": "ran"},
            {"case": "b", "asks": "B?", "expect": "not run"},
            {"case": "c", "asks": "C?", "expect": "yes"},
            {"case": "d", "asks": "D?", "expect": "no"},
            {"case": "e", "asks": "E?", "expect": "eye"},
        ])
        log = "\n".join('Mod: No matching node for Path "/AssetList/Pack_a_pack_%s"' % case
                        for case in ("a", "b", "c_yes", "d_yes", "d_no"))
        verdicts = {row["case"]: (row["answer"], row["verdict"]) for row in ingame_pack.judge(pack, log)}
        self.assertEqual(verdicts, {
            "a": ("ran", "as expected"), "b": ("ran", "NOT as expected"),
            "c": ("yes", "as expected"), "d": ("both", "NOT as expected"), "e": ("____", "by hand"),
        })

    def test_the_readme_says_where_the_pack_is_on_the_gaming_pc(self):
        text = ingame_pack.readme(self.folder)
        self.assertIn('install "<companion>\\in-game\\a-pack"', text)
        self.assertNotIn(str(self.folder.parent), text)

    def test_install_and_remove_put_everything_back(self):
        game = Path(tempfile.mkdtemp())
        (game / "mods").mkdir()
        profile = game / "profile.txt"
        profile.write_bytes(b"# a header\r\nsomeone-else\r\n")
        before = profile.read_bytes()
        mod = self.folder / "mods" / "[Test] Probe"
        mod.mkdir(parents=True)
        (mod / "modinfo.json").write_text('{"ModID": "probe-a"}', encoding="utf-8")
        with mock.patch("anno_modding_studio.core.launch.install_for", return_value=game), \
                mock.patch.object(ingame_pack, "_profile", return_value=profile):
            self.assertEqual(ingame_pack.install(self.folder), 0)
            self.assertTrue((game / "mods" / "[Test] Probe" / "modinfo.json").is_file())
            self.assertIn("probe-a", profile.read_text(encoding="utf-8"))
            self.assertEqual(ingame_pack.install(self.folder), 1)
            self.assertEqual(ingame_pack.remove(self.folder), 0)
        self.assertFalse((game / "mods" / "[Test] Probe").exists())
        self.assertEqual(profile.read_bytes(), before)
        self.assertFalse((self.folder / ingame_pack.INSTALLED).exists())

    def test_install_leaves_a_folder_that_is_there_already(self):
        game = Path(tempfile.mkdtemp())
        (game / "mods" / "[Test] Probe").mkdir(parents=True)
        (self.folder / "mods" / "[Test] Probe").mkdir(parents=True)
        with mock.patch("anno_modding_studio.core.launch.install_for", return_value=game), \
                mock.patch.object(ingame_pack, "_profile", return_value=None):
            self.assertEqual(ingame_pack.install(self.folder), 1)
        self.assertFalse((self.folder / ingame_pack.INSTALLED).exists())


if __name__ == "__main__":
    unittest.main()
