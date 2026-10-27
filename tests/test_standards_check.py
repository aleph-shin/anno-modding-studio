"""The rules of tools/standards_check.py, each on a snippet of source.

Every rule gets source that breaks it and source that keeps it, as the design
rules do in test_design_check.py, and the repository itself has to pass.
"""

from __future__ import annotations

import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

import _isolation  # noqa: F401 -- first: keeps the suite out of real files under any runner

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import check  # noqa: E402
import standards_check  # noqa: E402


def rules(found) -> list:
    return [rule for rule, _file, _line, _text in found]


def src(text: str) -> str:
    return textwrap.dedent(text)


class Layers(unittest.TestCase):
    def test_finds_qt_and_ui_in_core(self):
        found = standards_check.qt_in_core("core/x.py", src('''
            from PySide6.QtCore import Qt
            import PySide6.QtWidgets as widgets
            from ..ui import theme
            from ..ui.widgets import ask
            from anno_modding_studio.ui import theme as other

            def later():
                from PySide6 import QtGui
        '''))
        self.assertEqual(rules(found), ["qt-in-core"] * 6)

    def test_leaves_core_and_everything_else(self):
        found = standards_check.qt_in_core("core/x.py", src('''
            import json
            from lxml import etree
            from . import disk
            from .i18n import tr
            from .. import __version__
            from anno_modding_studio.core import places
        '''))
        self.assertEqual(found, [])

    def test_finds_a_leaf_that_imports_more(self):
        sources = {
            "disk": "def later():\n    from . import games\n",
            "i18n": "from .po import Catalog\nfrom .disk import read\n",
            "po": "", "games": "from . import disk\n",
        }
        found = standards_check.leaves(sources, {"disk": set(), "i18n": {"po"}, "po": set()})
        self.assertEqual(rules(found), ["leaf", "leaf"])
        self.assertIn("games", found[0][3])
        self.assertIn("disk", found[1][3])

    def test_leaves_a_leaf_within_its_list(self):
        sources = {"i18n": "import json\nfrom . import po\n", "po": "import re\n"}
        self.assertEqual(standards_check.leaves(sources, {"i18n": {"po"}, "po": set()}), [])

    def test_every_listed_leaf_is_a_module(self):
        self.assertEqual(set(standards_check.LEAVES) - set(standards_check._modules("core")), set())


class PrivateNames(unittest.TestCase):
    MODULES = frozenset({"modfolder", "widgets"})

    def found(self, text: str) -> list:
        return standards_check.private_names("x.py", src(text), self.MODULES)

    def test_finds_one_imported_or_reached(self):
        found = self.found('''
            from .modfolder import _read
            from ..core import modfolder
            from anno_modding_studio.ui import widgets as w

            def use():
                return modfolder._cache, w._tint()
        ''')
        self.assertEqual(rules(found), ["private-name"] * 3)

    def test_leaves_public_names_dunders_and_other_packages(self):
        found = self.found('''
            from __future__ import annotations
            from .modfolder import read, __doc__
            from modio_fetch import _read_key
            from . import modfolder

            class Thing:
                def use(self, other):
                    return self._own, other._theirs, modfolder.read, modfolder.__name__
        ''')
        self.assertEqual(found, [])


class Isolation(unittest.TestCase):
    def test_finds_a_test_module_without_it_or_with_it_late(self):
        missing = standards_check.isolation("t.py", "import unittest\n")
        late = standards_check.isolation("t.py", src('''
            from anno_modding_studio.core import disk
            import _isolation
        '''))
        self.assertEqual(rules(missing + late), ["isolation", "isolation"])

    def test_leaves_one_that_imports_it_first(self):
        found = standards_check.isolation("t.py", src('''
            import sys
            import _isolation  # noqa: F401
            from anno_modding_studio.core import disk
        '''))
        self.assertEqual(found, [])


class Waivers(unittest.TestCase):
    def test_finds_a_blind_except_waived_without_a_reason(self):
        found = standards_check.waivers("x.py", src('''
            try:
                pass
            except Exception:  # noqa: BLE001
                pass
            except BaseException as exc:  # noqa: E722, BLE001
                pass
        '''))
        self.assertEqual([line for _rule, _file, line, _text in found], [4, 6])

    def test_leaves_one_with_a_reason_and_other_rules(self):
        found = standards_check.waivers("x.py", src('''
            except Exception:  # noqa: BLE001 - cosmetic only
            except Exception:  # noqa: BLE001 -- a worker reports it
            import thing  # noqa: F401
        '''))
        self.assertEqual(found, [])


class WrittenTwice(unittest.TestCase):
    PYPROJECT = '[project]\nname = "x"\ndependencies = ["lxml>=5.0", "PySide6>=6.6"]\n'

    def test_finds_requirements_that_differ(self):
        found = standards_check.requirements(self.PYPROJECT, "lxml>=5.0\nPySide6>=6.7\n")
        self.assertEqual(rules(found), ["requirements"])

    def test_leaves_the_same_list_in_any_order(self):
        found = standards_check.requirements(self.PYPROJECT, "# what it needs\nPySide6>=6.6\n\nlxml>=5.0\n")
        self.assertEqual(found, [])

    def test_finds_a_link_to_nothing(self):
        folder = Path(tempfile.mkdtemp(prefix="ams-links-"))
        (folder / "there.md").write_text("", encoding="utf-8")
        found = standards_check.links("doc.md", src('''
            [here](there.md), [a heading in it](there.md#part), [this page](#top),
            [the web](https://example.com) and [mail](mailto:a@example.com).

            ```
            [in a code block](gone.md)
            ```

            [gone](gone.md) and [gone too](sub/gone.md#part)
        '''), folder)
        self.assertEqual([(line, text.split()[2]) for _rule, _file, line, text in found],
                         [(9, "gone.md,"), (9, "sub/gone.md#part,")])


def homes(text: str) -> str:
    """Source written with "Homes" for "Users", or the rule would find this file."""
    return src(text).replace("Homes", "Users").replace("homes", "users")


class UserPaths(unittest.TestCase):
    def test_finds_a_real_users_folder_in_any_spelling(self):
        found = standards_check.user_paths("x.py", homes(r'''
            a = r"C:\Homes\jdoe\Documents"
            b = "C:\\Homes\\JDOE~1\\AppData"
            c = "d:/homes/jdoe/x"
        '''))
        self.assertEqual([(line, text.split()[4]) for _rule, _file, line, text in found],
                         [(2, "'jdoe':"), (3, "'JDOE~1':"), (4, "'jdoe':")])

    def test_leaves_placeholders(self):
        found = standards_check.user_paths("x.md", src(r'''
            C:\Users\Public\Documents, C:\Users\me\x, C:\\Users\\someone,
            C:\Users\<name> and %USERPROFILE%\Documents
        '''))
        self.assertEqual(found, [])


BEFORE = {"ruff": {"B007": 3, "E741": 12}, "mypy": {"a.py": 1}, "cycle": 4,
          "walk": {"no-mnemonic": 7}}


class Baseline(unittest.TestCase):
    def test_finds_a_count_raised_or_added(self):
        now = {"ruff": {"B007": 4, "E741": 12, "F401": 1}, "mypy": {"a.py": 1, "b.py": 2},
               "cycle": 5, "walk": {"no-mnemonic": 8}}
        self.assertEqual(len(check.raised(now, BEFORE)), 5)

    def test_leaves_counts_that_are_the_same_lower_or_gone(self):
        now = {"ruff": {"B007": 3, "E741": 2}, "mypy": {}, "cycle": 3,
               "walk": {"no-mnemonic": 7}}
        self.assertEqual(check.raised(now, BEFORE), [])
        self.assertEqual(check.raised(BEFORE, BEFORE), [])

    def test_a_moved_file_keeps_the_count_it_had(self):
        before = dict(BEFORE, duplicates={"ui/old_name.py": 12})
        now = dict(BEFORE, duplicates={"ui/new_name.py": 12})
        self.assertEqual(len(check.raised(now, before)), 1)
        self.assertEqual(check.raised(now, before, {"ui/old_name.py": "ui/new_name.py"}), [])
        now = dict(BEFORE, duplicates={"ui/new_name.py": 13})
        self.assertEqual(len(check.raised(now, before, {"ui/old_name.py": "ui/new_name.py"})), 1)

    def test_leaves_a_kind_of_count_the_main_branch_does_not_keep_yet(self):
        now = dict(BEFORE, vulture={"a.py": 3})
        self.assertEqual(check.raised(now, BEFORE), [])
        self.assertEqual(len(check.raised(now, dict(BEFORE, vulture={"a.py": 2}))), 1)


class TheRepository(unittest.TestCase):
    def test_it_has_no_finding(self):
        self.assertEqual(standards_check.failures(), [])

    def test_every_tool_is_on_the_map(self):
        self.assertEqual(check.unmapped_tools(), [])


if __name__ == "__main__":
    unittest.main()
