"""What ``tools/duplicate_check.py`` counts as written twice."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

import _isolation  # noqa: F401 -- first: keeps the suite out of real files under any runner

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import duplicate_check  # noqa: E402

BODY = """
    first = read(source)
    second = sorted(first, key=by_name)
    for entry in second:
        report(entry.name, entry.size)
    total = sum(entry.size for entry in second)
    write(target, total)
"""


def _function(name: str, body: str = BODY, note: str = "") -> str:
    return "def %s(source, target):\n    \"\"\"%s does it.\"\"\"%s%s" % (name, name, note, body)


class TestWhatIsARun(unittest.TestCase):
    def test_the_same_six_lines_in_two_files_are_counted_in_both(self):
        found = duplicate_check.counts({"a.py": _function("one"), "b.py": _function("two")})
        self.assertEqual(dict(found), {"a.py": 6, "b.py": 6})

    def test_and_twice_in_one_file(self):
        found = duplicate_check.counts({"a.py": _function("one") + "\n\n" + _function("two")})
        self.assertEqual(dict(found), {"a.py": 12})

    def test_five_lines_are_not_a_run(self):
        short = "\n".join(BODY.splitlines()[:6]) + "\n"
        found = duplicate_check.counts({"a.py": _function("one", short), "b.py": _function("two", short)})
        self.assertEqual(dict(found), {})

    def test_comments_blank_lines_indentation_and_docstrings_do_not_hide_one(self):
        moved = "\n".join(
            ("    " + line + "  # said again" if line.strip() else "") + "\n"
            for line in BODY.splitlines()
        )
        other = "class Held:\n    def two(self, source, target):\n        \"\"\"Another.\"\"\"\n" + moved
        found = duplicate_check.counts({"a.py": _function("one"), "b.py": other})
        self.assertEqual(dict(found), {"a.py": 6, "b.py": 6})

    def test_a_line_changed_in_the_middle_breaks_it(self):
        changed = BODY.replace("report(entry.name, entry.size)", "report(entry.name)")
        found = duplicate_check.counts({"a.py": _function("one"), "b.py": _function("two", changed)})
        self.assertEqual(dict(found), {})

    def test_imports_are_not_code_written_twice(self):
        imports = "from somewhere import (\n" + "".join("    name_%d,\n" % n for n in range(8)) + ")\n"
        self.assertEqual(dict(duplicate_check.counts({"a.py": imports, "b.py": imports})), {})

    def test_lines_of_punctuation_neither_make_a_run_nor_break_one(self):
        spread = BODY.replace("write(target, total)", "write(\n        target, total\n    )")
        closing = "x = [\n" + "]\n)\n}\n" * 4
        found = duplicate_check.counts({
            "a.py": _function("one", spread), "b.py": _function("two", spread),
            "c.py": closing, "d.py": closing,
        })
        self.assertEqual(sorted(found), ["a.py", "b.py"])

    def test_each_run_says_where_its_copy_is(self):
        found = duplicate_check.runs({"a.py": _function("one"), "b.py": "\n\n\n" + _function("two")})
        ((first, last, others),) = found["a.py"]
        self.assertEqual((first, last, others), (3, 8, [("b.py", 6)]))

    def test_a_copy_with_comments_between_its_lines_is_named_once(self):
        block = "".join("value_%d = compute(source, %d)\n" % (n, n) for n in range(20))
        spread = "".join(line + "\n# said again\n# and again\n" for line in block.splitlines())
        found = duplicate_check.runs({"a.py": block, "b.py": spread})
        ((first, last, others),) = found["a.py"]
        self.assertEqual((first, last, others), (1, 20, [("b.py", 1)]))

    def test_a_file_that_is_not_python_is_passed_over(self):
        self.assertEqual(duplicate_check.lines_of("def broken(:\n"), [])


class TestTheRepository(unittest.TestCase):
    def test_the_count_is_the_one_the_gate_holds(self):
        """A file's count may not grow: the recorded numbers are today's or
        higher, and every file counted is one the gate knows of."""
        import json

        recorded = json.loads((ROOT / "tools" / "check_baseline.json").read_text(encoding="utf-8"))
        held = recorded["duplicates"]
        for name, number in duplicate_check.counts().items():
            with self.subTest(file=name):
                self.assertLessEqual(number, held.get(name, 0))


if __name__ == "__main__":
    unittest.main()
