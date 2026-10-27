"""Moving a ModOp in a mod's file, as the outline drags one (#196).

``core.moving`` makes the edit a cut and paste would: the thing's lines
taken out and put in again elsewhere, with which lines are what in the text
that leaves.  The test of each move is that the two agree with Apply: the
text applied is the text the Studio then writes, laid out as the move said
it would be, and every ModOp is the one it was.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest import mock

import _isolation  # noqa: F401 - first: keeps the suite out of real files

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import test_mod_files  # noqa: E402

from anno_modding_studio.core import generator, modfile, moving  # noqa: E402
from anno_modding_studio.core.workspace import Workspace  # noqa: E402

radius = test_mod_files.radius


def laid(shown: modfile.Shown) -> tuple:
    """Which lines are what, as two layouts of one text are compared."""
    return (
        [
            (item.kind, item.first, item.last, item.file, item.depth,
             item.op.uid if item.op is not None else None)
            for item in shown.items
        ],
        [(r.file, r.line, r.first, r.last, r.depth, r.within) for r in shown.regions],
    )


class MovingCase(test_mod_files.FilesCase):
    """The mod of three files: 1, the line naming parts/one.xml, 2 in the
    patch; 11, the line naming two.xml, 12 in one.xml; 21 and a group
    holding 22 in two.xml."""

    def setUp(self):
        super().setUp()
        self.loadout = self.loadout_of(self.folder())
        self.shown = modfile.shown(self.loadout, "m")

    def op(self, value) -> int:
        return next(
            at for at, item in enumerate(self.shown.items)
            if item.op is not None and "<Radius>%s<" % value in (item.op.source or "")
        )

    def kind(self, kind: str, nth: int = 0) -> int:
        return [at for at, item in enumerate(self.shown.items) if item.kind == kind][nth]

    def line(self, file: str) -> int:
        return next(
            at for at, item in enumerate(self.shown.items)
            if item.kind == generator.SPAN_INCLUDE and item.op.includes == file
        )

    def applied(self, made: moving.Moved) -> modfile.Applied:
        """Apply what the move made, and check that it is what it said."""
        self.assertIsNotNone(made)
        uids = sorted(item.op.uid for item in self.shown.items if item.op is not None)
        _found, done = modfile.apply(
            Workspace(self.index, self.loadout), "m", made.shown.text, self.shown,
            made.shown.regions,
        )
        self.assertIsNotNone(done)
        after = modfile.shown(self.loadout, "m")
        self.assertEqual(after.text, made.shown.text)
        self.assertEqual(laid(after), laid(made.shown))
        self.assertEqual(
            sorted(item.op.uid for item in after.items if item.op is not None), uids
        )
        return done

    def order(self) -> list:
        """``(file, what)`` for each thing, as the mod holds them now."""
        return self.laid(self.loadout)


class TestWhereAThingGoes(MovingCase):
    def test_before_another_in_the_patch(self):
        done = self.applied(moving.moved(self.shown, self.op(2), None, self.op(1)))
        self.assertTrue(done.rearranged)
        self.assertEqual(self.order()[:2], [("", "2"), ("", "1")])

    def test_at_the_end_of_the_patch(self):
        self.applied(moving.moved(self.shown, self.op(1), None))
        self.assertEqual(self.order()[-2:], [("", "2"), ("", "1")])

    def test_into_another_file(self):
        self.applied(moving.moved(self.shown, self.op(1), self.line("parts/two.xml")))
        self.assertIn(("parts/two.xml", "1"), self.order())
        self.assertEqual(self.files_of("parts/two.xml"), ["21", "22", "1"])

    def test_into_a_group_and_out_of_it(self):
        made = moving.moved(self.shown, self.op(1), self.kind(generator.SPAN_GROUP))
        lines = made.shown.text.split("\n")
        # A step deeper, as the group's own ops are.
        self.assertEqual(lines[made.line - 1], "    " + lines[made.line - 1].strip())
        self.applied(made)
        self.shown = modfile.shown(self.loadout, "m")
        self.assertEqual(self.shown.items[self.op(1)].depth, 1)
        self.assertEqual(self.files_of("parts/two.xml"), ["21", "22", "1"])

        made = moving.moved(self.shown, self.op(22), None)
        self.assertTrue(made.shown.text.split("\n")[made.line - 1].startswith("  <ModOp"))
        self.applied(made)
        self.assertEqual(self.order()[-1], ("", "22"))

    def test_a_group_with_what_is_in_it(self):
        self.applied(moving.moved(self.shown, self.kind(generator.SPAN_GROUP), None, self.op(1)))
        self.assertEqual(self.order()[:2], [("", "group"), ("", "22")])

    def test_a_files_line_with_the_file(self):
        """Named from the top of the mod where it stands now, and still the
        line it was: the file is read from anywhere."""
        made = moving.moved(self.shown, self.line("parts/two.xml"), None)
        self.assertIn('<Include File="/parts/two.xml"/>', made.shown.text)
        done = self.applied(made)
        self.assertTrue(done.rearranged)
        self.assertEqual(done.joined, [])
        self.assertEqual(self.loadout.files_of("m")["parts/two.xml"], "")
        self.assertEqual(self.files_of("parts/two.xml"), ["21", "22"])

    def test_a_files_line_naming_an_ampersand_names_it_as_xml_spells_it(self):
        self.loadout = self.loadout_of(self.folder(**{
            "parts|one.xml": radius(11) + '<Include File="t&amp;o.xml"/>' + radius(12),
            "parts|t&o.xml": radius(21),
        }))
        self.shown = modfile.shown(self.loadout, "m")
        made = moving.moved(self.shown, self.line("parts/t&o.xml"), None)
        self.assertIn('File="/parts/t&amp;o.xml"', made.shown.text)
        self.assertNotIn('File="t&amp;o.xml"', made.shown.text)
        _found, done = modfile.apply(
            Workspace(self.index, self.loadout), "m", made.shown.text, self.shown,
            made.shown.regions,
        )
        self.assertIsNotNone(done)
        self.assertEqual(self.loadout.files_of("m")["parts/t&o.xml"], "")

    def test_two_that_say_the_same_put_in_each_others_place_is_no_move(self):
        self.loadout = self.loadout_of(self.folder(main=radius(1) + radius(1) + radius(2)))
        self.shown = modfile.shown(self.loadout, "m")
        ones = [at for at, item in enumerate(self.shown.items) if item.op is not None
                and "<Radius>1<" in item.op.source]
        self.assertIsNone(moving.moved(self.shown, ones[1], None, ones[0]))

    def test_a_studio_written_modop_goes_where_it_is_put(self):
        test_mod_files.TestWhereTheStudiosOwnChangesGo.own(
            self, self.loadout, "99", self.loadout.after_last_op("m")
        )
        self.shown = modfile.shown(self.loadout, "m")
        own = next(at for at, item in enumerate(self.shown.items) if item.changes and item.op is None)
        self.assertTrue(moving.can_move(self.shown, own))
        made = moving.moved(self.shown, own, None, self.op(1))
        _found, done = modfile.apply(
            Workspace(self.index, self.loadout), "m", made.shown.text, self.shown,
            made.shown.regions,
        )
        self.assertTrue(done.rearranged)
        after = modfile.shown(self.loadout, "m")
        self.assertEqual(after.items[0].changes[0].new_value, "99")

    def test_an_op_carried_unread_is_carried_there(self):
        self.loadout = self.loadout_of(self.folder(**{"parts|two.xml": "<Strange/>" + radius(21)}))
        self.shown = modfile.shown(self.loadout, "m")
        unread = next(at for at, item in enumerate(self.shown.items) if item.unread is not None)
        done = self.applied(moving.moved(self.shown, unread, None, self.op(1)))
        self.assertEqual(done.unread, 1)
        self.assertEqual(self.loadout.unread[0].file, "")

    def files_of(self, name: str) -> list:
        whole = self.written(self.loadout)
        return self.radii(whole.files[name])


class TestWhereAThingCannotGo(MovingCase):
    def test_where_it_is_already(self):
        self.assertIsNone(moving.moved(self.shown, self.op(1), None, self.line("parts/one.xml")))
        self.assertIsNone(moving.moved(self.shown, self.op(2), None))

    def test_into_itself(self):
        group = self.kind(generator.SPAN_GROUP)
        self.assertFalse(moving.can_go(self.shown, group, group))
        one = self.line("parts/one.xml")
        self.assertFalse(moving.can_go(self.shown, one, self.line("parts/two.xml")))

    def test_a_files_line_into_a_group(self):
        self.assertFalse(moving.can_go(
            self.shown, self.line("parts/two.xml"), self.kind(generator.SPAN_GROUP)
        ))

    def test_before_what_is_somewhere_else(self):
        self.assertFalse(moving.can_go(self.shown, self.op(1), None, self.op(11)))

    def test_into_what_holds_nothing(self):
        self.assertFalse(moving.can_go(self.shown, self.op(1), self.op(2)))

    def test_what_has_no_row_does_not_move(self):
        end = self.kind(generator.SPAN_GROUP_END)
        self.assertFalse(moving.can_move(self.shown, end))
        self.assertIsNone(moving.moved(self.shown, end, None))


class TestWhatMovesWithIt(MovingCase):
    def test_its_note_and_the_blank_lines_before_it(self):
        made = moving.moved(self.shown, self.line("parts/one.xml"), None)
        lines = made.shown.text.split("\n")
        self.assertIn("note: the parts", lines[made.line - 1])
        self.applied(made)
        line = next(op for op in self.loadout.ops if op.includes == "parts/one.xml")
        self.assertEqual(line.note, "the parts")

    def test_a_comment_standing_loose_above_it_stays(self):
        self.loadout = self.loadout_of(self.folder(
            main=radius(1) + "<!-- loose -->\n\n" + radius(2) + radius(3)
        ))
        self.shown = modfile.shown(self.loadout, "m")
        made = moving.moved(self.shown, self.op(2), None)
        text = made.shown.text
        self.assertLess(text.index("loose"), text.index("<Radius>3<"))
        self.assertGreater(text.index("<Radius>2<"), text.index("<Radius>3<"))

    def test_put_first_in_a_file_it_goes_without_blank_lines(self):
        made = moving.moved(self.shown, self.op(2), self.line("parts/one.xml"), self.op(11))
        lines = made.shown.text.split("\n")
        region = next(r for r in made.shown.regions if r.file == "parts/one.xml")
        # The heading's blank line, then the op.
        self.assertEqual(made.line, region.first + 1)
        self.assertEqual(lines[region.first - 1].strip(), "")
        self.applied(made)

    def test_into_an_empty_file(self):
        self.loadout = self.loadout_of(self.folder(**{"parts|two.xml": ""}))
        self.shown = modfile.shown(self.loadout, "m")
        region = next(r for r in self.shown.regions if r.file == "parts/two.xml")
        # Its one blank line, which its heading is drawn on.
        self.assertEqual(region.first, region.last)
        made = moving.moved(self.shown, self.op(1), self.line("parts/two.xml"))
        self.applied(made)
        self.assertEqual(self.files_of("parts/two.xml"), ["1"])

    files_of = TestWhereAThingGoes.files_of


class TestUpAndDown(MovingCase):
    def test_a_place_at_a_time_among_its_own(self):
        self.applied(moving.down(self.shown, self.op(11)))
        self.assertEqual(
            [what for file, what in self.order() if file == "parts/one.xml"],
            ["includes parts/two.xml", "11", "12"],
        )

    def test_at_the_top_of_a_group_out_of_it_before_it(self):
        made = moving.up(self.shown, self.op(22))
        self.applied(made)
        self.assertEqual(
            [what for file, what in self.order() if file == "parts/two.xml"],
            ["21", "22", "group"],
        )

    def test_at_the_end_of_a_file_out_of_it_after_its_line(self):
        """Where it is in the text already: a blank line before it is what
        says it is out of the file."""
        made = moving.down(self.shown, self.op(12))
        self.assertNotEqual(made.shown.text, self.shown.text)
        self.assertEqual(made.shown.text.split("\n")[made.line - 2], "")
        self.applied(made)
        self.assertEqual([what for file, what in self.order() if file == ""][:3], [
            "1", "includes parts/one.xml", "12",
        ])

    def test_a_file_or_a_group_is_one_place_to_step_over(self):
        self.applied(moving.down(self.shown, self.op(12)))
        self.shown = modfile.shown(self.loadout, "m")
        self.applied(moving.up(self.shown, self.op(12)))
        self.assertEqual(self.order()[:3], [
            ("", "1"), ("", "12"), ("", "includes parts/one.xml"),
        ])
        self.shown = modfile.shown(self.loadout, "m")
        self.applied(moving.down(self.shown, self.op(21)))
        self.assertEqual(
            [what for file, what in self.order() if file == "parts/two.xml"],
            ["group", "22", "21"],
        )

    def test_not_past_the_ends_of_the_patch(self):
        self.assertIsNone(moving.up(self.shown, self.op(1)))
        self.assertIsNone(moving.down(self.shown, self.op(2)))


class TestALineIsTheFileItNames(MovingCase):
    def test_however_it_is_spelled(self):
        """Typed as another spelling of the same file, the line is the one
        the mod had, and what it said of it stands."""
        text = self.shown.text.replace('<Include File="two.xml"/>', '<Include File="./two.xml"/>')
        with mock.patch.object(modfile, "_unnamed", wraps=modfile._unnamed) as unnamed:
            _found, done = modfile.apply(
                Workspace(self.index, self.loadout), "m", text, self.shown,
            )
        self.assertTrue(unnamed.called)
        self.assertEqual((done.lines, done.added, done.dropped), (0, 0, 0))
