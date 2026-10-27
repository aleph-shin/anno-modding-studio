"""A mod's own options, declared here: the ``Options`` block of a mod written
in the Studio, or of a mod of yours read in, carried out on export and read
back by the mod's ModOps as ``$name``."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import _isolation  # noqa: F401 -- first: keeps the suite out of real files under any runner
from lxml import etree

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from support import (  # noqa: E402
    MODINFO_WITH_OPTIONS,
    ImportCase,
    PlayingWindow,
    modops_file,
)

from anno_modding_studio.core import (  # noqa: E402
    declaring,
    generator,
    keptops,
    modops,
    reader,
    settings,
    typedops,
)
from anno_modding_studio.core.changes import (  # noqa: E402
    Loadout,
    ModLayer,
    Op,
    Unread,
    own_record,
)
from anno_modding_studio.core.games import ANNO_117  # noqa: E402
from anno_modding_studio.core.workspace import Workspace  # noqa: E402

DECLARED = json.loads(MODINFO_WITH_OPTIONS)["Options"]
GUARDED = (
    '<ModOp Type="merge" GUID="1000" Path="/Values/Building" Condition="$roomier">'
    "<Radius>30</Radius></ModOp>"
)


def _own(**blocks) -> Loadout:
    loadout = Loadout(game=ANNO_117, own=own_record(name="My Mod", mod_id="my-mod"))
    for name, block in blocks.items():
        setattr(loadout, name, block)
    loadout.make_own_mod()
    return loadout


class TheBlock(unittest.TestCase):
    def test_only_what_means_something_is_written(self):
        block = settings.to_block([
            settings.Option(name="roomier", label="roomier", kind=settings.TOGGLE, default="false"),
            settings.Option(name="size", label="Radius", kind=settings.ENUM, default="10",
                            values=("5", "10"), labels=("Small", "Large")),
            settings.Option(name="factor", label="Cost factor", kind=settings.SLIDER, default="1",
                            values=("0", "2", "0.5"), description="Times the cost."),
        ])
        self.assertEqual(block, {
            "roomier": {"type": "toggle", "default": "false"},
            "size": {"label": "Radius", "type": "enum", "default": "10",
                     "values": ["5", "10"], "labels": ["Small", "Large"]},
            "factor": {"label": "Cost factor", "type": "slider", "default": "1",
                       "values": ["0", "2", "0.5"], "description": "Times the cost."},
        })

    def test_read_and_written_it_says_the_same(self):
        once = settings.read(DECLARED)
        again = settings.read(settings.to_block(once, DECLARED))
        self.assertEqual(once.to_dict(), again.to_dict())

    def test_a_key_the_studio_does_not_model_goes_on_being_said(self):
        original = {"roomier": {"type": "toggle", "default": "false", "group": "Houses"}}
        written = settings.to_block(settings.read(original), original)
        self.assertEqual(written["roomier"]["group"], "Houses")

    def test_your_mod_s_is_on_the_loadout(self):
        loadout = _own()
        settings.set_block(loadout, "", {"roomier": {"type": "toggle", "default": "true"}})
        self.assertEqual(settings.block_of(loadout, ""), {"roomier": {"type": "toggle", "default": "true"}})
        self.assertEqual(loadout.own_layer().offered["roomier"]["default"], "true")

    def test_a_loaded_mod_s_keeps_its_key_and_place_and_its_tweaks(self):
        layer = ModLayer(
            origin="A", mod_id="a",
            declared={"ModID": "a", "options": {"old": {"type": "toggle", "default": "true"}}, "Version": "1"},
            # What it offers: its modinfo's option, and an iModYourAnno tweak.
            offered=settings.ModOptions([
                settings.Option(name="old", kind=settings.TOGGLE, default="true"),
                settings.Option(name="IMYA_tweak", kind=settings.TEXT, default="3"),
            ]).to_dict(),
            options={"old": "false", "IMYA_tweak": "4"},
        )
        loadout = Loadout(game=ANNO_117)
        loadout.set_layer(layer)
        settings.set_block(loadout, "A", {"new": {"type": "toggle", "default": "false"}})
        self.assertEqual(list(layer.declared), ["ModID", "options", "Version"])
        self.assertEqual(list(layer.offered), ["IMYA_tweak", "new"])
        # What was chosen for an option that is gone goes with it.
        self.assertEqual(layer.options, {"IMYA_tweak": "4"})
        settings.set_block(loadout, "A", {})
        self.assertEqual(list(layer.declared), ["ModID", "Version"])

    def test_saved_and_counted_as_work(self):
        loadout = _own()
        before = loadout.typed_details()
        loadout.options = dict(DECLARED)
        self.assertTrue(loadout.details_moved(before))
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "p.json"
            loadout.save(path)
            self.assertEqual(Loadout.load(path).options, DECLARED)


class WhatStandsInTheWay(unittest.TestCase):
    def said(self, *options):
        return declaring.problems(list(options))

    def test_a_declaration_a_mod_ships_is_fine(self):
        self.assertEqual(declaring.problems(settings.read(DECLARED)), [])

    def test_names_the_loader_could_not_read(self):
        for name in ("a.b", "1x", "has space", "$x"):
            with self.subTest(name=name):
                self.assertEqual(len(self.said(settings.started_as(settings.TOGGLE, name))), 1)
        self.assertEqual(self.said(settings.started_as(settings.TOGGLE, "")), ["An option has no name."])
        self.assertEqual(self.said(settings.started_as(settings.TOGGLE, "on-or_off2")), [])

    def test_two_of_one_name(self):
        twice = settings.started_as(settings.TOGGLE, "x")
        self.assertEqual(self.said(twice, twice), ["Two options are called x."])

    def test_each_kind_s_default(self):
        self.assertTrue(self.said(settings.Option(name="t", kind=settings.TOGGLE, default="yes")))
        self.assertTrue(self.said(settings.Option(name="e", kind=settings.ENUM, default="3",
                                                  values=("1", "2"))))
        self.assertTrue(self.said(settings.Option(name="e", kind=settings.ENUM, default="1",
                                                  values=("1", "2"), labels=("One",))))
        self.assertTrue(self.said(settings.Option(name="s", kind=settings.SLIDER, default="11",
                                                  values=("0", "10", "1"))))
        self.assertTrue(self.said(settings.Option(name="s", kind=settings.SLIDER, default="1",
                                                  values=("10", "0", "1"))))
        self.assertFalse(self.said(settings.Option(name="s", kind=settings.SLIDER, default="0.5",
                                                   values=("0", "2", "0.5"))))

    def test_a_slider_s_step_of_0_as_typed(self):
        # Option.range shows a step of 0 as 1; what is written is the 0.
        self.assertEqual(self.said(settings.Option(name="s", kind=settings.SLIDER, default="1",
                                                   values=("0", "10", "0"))),
                         ["s runs from a lower number to a higher one, in steps above 0."])


class WhatItsOpsRead(unittest.TestCase):
    def test_its_own_by_either_name_and_not_another_mod_s(self):
        loadout = _own()
        loadout.ops = [
            Op(origin="", order=1, source='<ModOp Condition="$b and $my-mod.a"/>'),
            Op(origin="other", order=0, source='<ModOp Condition="$theirs"/>'),
        ]
        loadout.unread = [
            Unread(origin="", order=0, source='<ModOp><x><ModValue Insert="$c * $other-mod.d"/></x></ModOp>'),
        ]
        self.assertEqual(declaring.named_by(loadout, "", "my-mod"), ["c", "b", "a"])


class WhatKindItLooksLike(unittest.TestCase):
    def kinds(self, source: str):
        loadout = _own()
        loadout.unread = [Unread(origin="", order=0, source=source)]
        return {name: declaring.guessed_kind(loadout, "", name)
                for name in declaring.undeclared(loadout, "")}

    def test_a_condition_on_its_own_is_a_toggle_and_a_number_a_slider(self):
        self.assertEqual(self.kinds(
            '<ModOp Condition="$on and not($off) and $my-mod.far &gt; 3 and 2 * $twice = 4">'
            '<x><ModValue Insert="self::node() * $factor"/></x></ModOp>'
        ), {"on": settings.TOGGLE, "off": settings.TOGGLE, "far": settings.SLIDER,
            "twice": settings.SLIDER, "factor": settings.SLIDER})

    def test_what_it_declares_is_not_asked_again(self):
        loadout = _own(options={"roomier": {"type": "toggle", "default": "false"}})
        loadout.unread = [Unread(origin="", order=0, source=GUARDED.replace("$roomier", "$roomier or $more"))]
        self.assertEqual(declaring.undeclared(loadout, ""), ["more"])


class WhereItIsNamed(unittest.TestCase):
    GROUP = '<Group Condition="$my-mod.ceres and $their-mod.hard"><ModOp Condition="$inner"/></Group>'

    def loadout(self):
        loadout = _own(options={"ceres": {"type": "toggle", "default": "true"}})
        loadout.set_layer(ModLayer(origin="Theirs", mod_id="their-mod",
                                   declared={"Options": {"hard": {"default": "1"}}}))
        loadout.ops = [Op(origin="", order=4, uid="g", source=self.GROUP, summary="a group")]
        loadout.unread = [Unread(origin="", order=2, source='<ModOp Condition="!$ceres"/>', label="first")]
        return loadout

    def test_where_in_the_text(self):
        found = declaring.named_in(self.GROUP)
        self.assertEqual([one.name for one in found], ["my-mod.ceres", "their-mod.hard", "inner"])
        self.assertEqual([self.GROUP[one.start:one.end] for one in found],
                         ["$my-mod.ceres", "$their-mod.hard", "$inner"])
        self.assertEqual(declaring.named_in("<x>$not-read</x>"), [])

    def test_whose_it_is(self):
        loadout = self.loadout()
        own = declaring.resolved(loadout, "", "my-mod.ceres")
        self.assertEqual((own.origin, own.name, own.option.default), ("", "ceres", "true"))
        self.assertEqual(declaring.resolved(loadout, "", "ceres"), own)
        theirs = declaring.resolved(loadout, "", "their-mod.hard")
        self.assertEqual((theirs.origin, theirs.name), ("Theirs", "hard"))
        self.assertIsNone(declaring.resolved(loadout, "", "nobody.x"))
        self.assertIsNone(declaring.resolved(loadout, "", "loose").option)

    def test_which_ops_read_it_in_file_order(self):
        uses = declaring.uses(self.loadout(), "", "ceres")
        self.assertEqual([(use.kind, use.which, use.said) for use in uses],
                         [("unread", 2, "first"), ("op", "g", "a group")])

    def test_what_a_group_itself_asks(self):
        self.assertEqual(declaring.asked_by(self.loadout(), "", self.GROUP), ["ceres"])


class WhatIsPutUnderAnOption(unittest.TestCase):
    """Which lines of a mod's file go into each group (core.option_groups)."""

    TEXT = "\n".join([
        "<ModOps>",                          # 1
        "  <ModOp A/>",                      # 2  op a
        "  <ModOp Changes/>",                # 3  the Studio's, for two changes
        "  <Group Condition=\"#x\">",        # 4  group g
        "    <ModOp Inner/>",                # 5  op i
        "  </Group>",                        # 6
        "  <ModOp B/>",                      # 7  op b
        "</ModOps>",                         # 8
    ])

    def shown(self):
        from anno_modding_studio.core import generator, modfile
        from anno_modding_studio.core.changes import Change

        def op(uid):
            return Op(origin="", uid=uid)

        changes = tuple(
            Change(guid=guid, asset_label="", path=path, tag="Radius", op="replace", new_value="1")
            for guid, path in (("1", "/a"), ("2", "/b"))
        )
        items = [
            modfile.Item(generator.SPAN_OP, 2, 2, op=op("a")),
            modfile.Item(generator.SPAN_CHANGES, 3, 3, changes=changes),
            modfile.Item(generator.SPAN_GROUP, 4, 4, op=op("g")),
            modfile.Item(generator.SPAN_OP, 5, 5, op=op("i"), depth=1),
            modfile.Item(generator.SPAN_GROUP_END, 6, 6, op=op("g")),
            modfile.Item(generator.SPAN_OP, 7, 7, op=op("b")),
        ]
        return modfile.Shown(origin="", text=self.TEXT, items=items), changes

    def test_what_stands_together_is_one_group_and_a_group_goes_whole(self):
        from anno_modding_studio.core import option_groups

        shown, changes = self.shown()
        # A change of the Studio's ModOp picks all of it; the op inside a
        # picked group is the group's.
        found = option_groups.runs(shown, [("op", "a"), ("change", changes[1].key),
                                           ("op", "g"), ("op", "i")])
        self.assertEqual(found, [option_groups.Run(2, 6)])
        found = option_groups.runs(shown, [("op", "a"), ("op", "b")])
        self.assertEqual(found, [option_groups.Run(2, 2), option_groups.Run(7, 7)])
        # Inside a group and outside it: a run each, at its own depth.
        found = option_groups.runs(shown, [("op", "i"), ("op", "b")])
        self.assertEqual(found, [option_groups.Run(5, 5), option_groups.Run(7, 7)])

    def test_wrapped_at_its_own_indent(self):
        from anno_modding_studio.core import option_groups

        shown, _changes = self.shown()
        text = option_groups.wrapped(shown.text, [option_groups.Run(5, 5), option_groups.Run(7, 7)], "on")
        self.assertEqual(text.split("\n")[4:11], [
            '    <Group Condition="$on">',
            "      <ModOp Inner/>",
            "    </Group>",
            "  </Group>",
            '  <Group Condition="$on">',
            "    <ModOp B/>",
            "  </Group>",
        ])

    def test_a_row_says_the_label_and_not_the_name(self):
        loadout = _own(options={"ceres": {"type": "toggle", "default": "true", "label": "Shrine of Ceres"}})
        self.assertEqual(
            declaring.with_labels(loadout, "", "a group of ops, only if $my-mod.ceres and not($odd)"),
            "a group of ops, only if option “Shrine of Ceres” and not($odd)",
        )
        loadout.game = "anno1800"
        self.assertEqual(declaring.with_labels(loadout, "", "only if $ceres"), "only if $ceres")


class GoingOut(unittest.TestCase):
    def test_your_mod_s_options_are_in_its_modinfo(self):
        loadout = _own(options=dict(DECLARED))
        written = json.loads(generator.build_modinfo(generator.for_mod(loadout, "")))
        self.assertEqual(written["Options"], DECLARED)
        self.assertNotIn("Options", json.loads(generator.build_modinfo(generator.for_mod(_own(), ""))))

    def test_a_loaded_mod_s_edited_block_goes_out_in_its_modinfo(self):
        layer = ModLayer(origin="A", name="A", declared={"ModID": "a", "Anno": 8})
        loadout = Loadout(game=ANNO_117)
        loadout.set_layer(layer)
        settings.set_block(loadout, "A", {"x": {"type": "toggle", "default": "true"}})
        written = json.loads(generator.build_modinfo(generator.for_mod(loadout, "A")))
        self.assertEqual(written["Options"], {"x": {"type": "toggle", "default": "true"}})


class ReadBack(ImportCase):
    """An op of your own that reads a setting, before the setting is declared
    and after."""

    def typed(self, loadout, text: str = GUARDED):
        return typedops.read_one(Workspace(self.index, loadout), "", text)

    def test_undeclared_it_says_which_and_declared_it_is_read(self):
        loadout = _own()
        found = self.typed(loadout)
        self.assertEqual(len(found.unread), 1)
        self.assertIn("reads $roomier, which the mod does not declare as an option",
                      found.unread[0].why)
        settings.set_block(loadout, "", {"roomier": {"type": "toggle", "default": "true"}})
        found = self.typed(loadout)
        self.assertEqual(found.unread, [])

    def test_named_in_full_under_a_mod_id_made_of_the_name(self):
        loadout = _own()
        loadout.own.mod_id = ""
        settings.set_block(loadout, "", {"roomier": {"type": "toggle", "default": "true"}})
        found = self.typed(loadout, GUARDED.replace("$roomier", "$my-mod.roomier"))
        self.assertEqual(found.unread, [])
        self.assertEqual(keptops.reading_of(loadout, "")["mod_ids"][""], "my-mod")

    def test_your_mod_in_a_reading_of_the_stack(self):
        """How it is read again after an edit: no folder, its text, and what
        it offers from the loadout."""
        ops = modops.parse_ops(etree.fromstring(modops_file(GUARDED).encode("utf-8")), [], "assets.xml")
        for default, status in (("true", modops.IMPORTED), ("false", modops.SETTLED)):
            with self.subTest(default=default):
                offered = settings.read({"roomier": {"type": "toggle", "default": default}})
                session = reader.analyse_all(
                    [], self.index, written={"": ops},
                    without_folder=[("", {"mod_id": "my-mod", "options": offered})],
                )
                self.assertEqual(session.reads[0].report.results[0].status, status)

    def test_and_named_in_full_there_with_no_mod_id_yet(self):
        """Under the ModID it will go out with, which is only for that: the
        mod's own stays blank, since which mod loads goes by it."""
        text = GUARDED.replace("$roomier", "$my-mod.roomier")
        ops = modops.parse_ops(etree.fromstring(modops_file(text).encode("utf-8")), [], "assets.xml")
        offered = settings.read({"roomier": {"type": "toggle", "default": "true"}})
        session = reader.analyse_all(
            [], self.index, written={"": ops},
            without_folder=[("", {"mod_id": "", reader.SETTINGS_ID: "my-mod", "options": offered})],
        )
        report = session.reads[0].report
        self.assertEqual(report.results[0].status, modops.IMPORTED, report.results[0].reason)
        self.assertEqual(report.metadata["mod_id"], "")


class _Qt(unittest.TestCase):
    def setUp(self):
        from PySide6.QtWidgets import QApplication

        self.app = QApplication.instance() or QApplication([])

    def make(self, loadout, origin="", proposed=(), focus="", after_apply=False):
        from anno_modding_studio.ui.options_editor_dialog import OptionsEditorDialog

        dialog = OptionsEditorDialog(loadout, origin, "my-mod", None, proposed, focus,
                                     after_apply=after_apply)
        self.addCleanup(dialog.deleteLater)
        return dialog


class TheDialog(_Qt):
    def ok(self, dialog):
        from PySide6.QtWidgets import QDialogButtonBox

        return dialog.buttons.button(QDialogButtonBox.StandardButton.Ok)

    def test_a_name_its_ops_read_is_offered_and_declared_on_ok(self):
        loadout = _own()
        loadout.unread = [Unread(origin="", order=0, source=GUARDED)]
        dialog = self.make(loadout)
        self.assertFalse(dialog.missing.isHidden())
        self.assertIn("$roomier", dialog.missing.text())
        dialog.declare_missing()
        self.assertTrue(dialog.missing.isHidden())
        self.assertEqual(dialog.read_as.text(), "$my-mod.roomier")
        dialog._accept()
        self.assertEqual(loadout.options, {"roomier": {"type": "toggle", "default": "false"}})

    def test_what_is_typed_is_what_is_kept(self):
        loadout = _own(options={"size": {"type": "enum", "default": "10", "values": ["5", "10"]}})
        dialog = self.make(loadout)
        dialog.label_edit.setText("Radius")
        dialog.label_edit.textEdited.emit("Radius")
        dialog._add_choice()
        dialog.choices.item(2, 1).setText("Huge")
        self.assertIn("Each choice of size", dialog.problem.text())
        self.assertFalse(self.ok(dialog).isEnabled())
        dialog.choices.item(0, 1).setText("Small")
        dialog.choices.item(1, 1).setText("Medium")
        self.assertTrue(self.ok(dialog).isEnabled())
        dialog._accept()
        self.assertEqual(loadout.options["size"], {
            "label": "Radius", "type": "enum", "default": "10",
            "values": ["5", "10", "1"], "labels": ["Small", "Medium", "Huge"],
        })

    def test_a_name_the_loader_cannot_read_holds_ok(self):
        dialog = self.make(_own())
        dialog.add_option("a.b")
        self.assertFalse(self.ok(dialog).isEnabled())
        dialog.name_edit.setText("ab")
        dialog.name_edit.textEdited.emit("ab")
        self.assertTrue(self.ok(dialog).isEnabled())
        self.assertEqual(dialog.list.item(0).text(), "ab")

    def test_names_an_apply_brought_in_are_proposed_and_kept_only_by_ok(self):
        loadout = _own()
        loadout.unread = [Unread(order=0, origin="", source=(
            '<ModOp Condition="$roomier and $far &lt; 3"/>'))]
        dialog = self.make(loadout, proposed=["roomier", "far"], after_apply=True)
        self.assertEqual([dialog.list.item(row).text() for row in range(2)],
                         ["roomier (new)", "far (new)"])
        self.assertEqual([item["kind"] for item in dialog.items], [settings.TOGGLE, settings.SLIDER])
        self.assertFalse(dialog.new_note.isHidden())
        self.assertIn("$roomier, $far", dialog.new_note.text())
        self.assertIn("Undo the Apply takes back what you applied.", dialog.new_note.text())
        self.assertTrue(dialog.missing.isHidden())
        # Cancel takes the Apply back, and says so.
        from PySide6.QtWidgets import QDialogButtonBox

        self.assertEqual(dialog.buttons.button(QDialogButtonBox.StandardButton.Cancel).text(),
                         "Undo the Appl&y")
        self.assertNotEqual(
            self.make(_own()).buttons.button(QDialogButtonBox.StandardButton.Cancel).text(),
            "Undo the Appl&y",
        )
        self.assertEqual(loadout.options, {})
        dialog._remove()
        self.assertEqual(dialog.list.item(0).text(), "far (new)")
        dialog._accept()
        self.assertEqual(list(loadout.options), ["far"])

    def test_a_name_proposed_with_no_apply_has_no_apply_to_undo(self):
        """Opened from a $name nothing declares, as a link to it opens it:
        proposed, but nothing was applied, so Cancel only cancels and the
        note does not speak of an Apply."""
        from PySide6.QtWidgets import QDialogButtonBox

        loadout = _own()
        loadout.unread = [Unread(order=0, origin="", source='<ModOp Condition="$roomier"/>')]
        dialog = self.make(loadout, proposed=["roomier"], focus="roomier")
        self.assertEqual(dialog.list.item(0).text(), "roomier (new)")
        self.assertFalse(dialog.new_note.isHidden())
        self.assertIn("$roomier", dialog.new_note.text())
        self.assertNotIn("Apply", dialog.new_note.text())
        self.assertNotEqual(dialog.buttons.button(QDialogButtonBox.StandardButton.Cancel).text(),
                            "Undo the Appl&y")

    def test_a_removed_option_leaves_no_name_behind(self):
        """One added after a removal is new, whatever the removed one was
        declared as: no ModOp reads it."""
        loadout = _own(options={"a": {"type": "toggle", "default": "true"}})
        loadout.ops = [Op(origin="", order=1, uid="g", summary="a group",
                          source='<Group Condition="$a"><ModOp/></Group>')]
        dialog = self.make(loadout)
        dialog.list.setCurrentRow(0)
        self.assertEqual(dialog.uses.count(), 1)
        dialog._remove()
        for n in range(8):
            dialog.add_option("x%d" % n)
            self.assertEqual(dialog.uses.count(), 0, dialog.uses_note.text())
            self.assertEqual(dialog.uses_note.text(), "No ModOp reads it yet.")
        # One put in here goes by the name it has: added as "a" again, the
        # ModOp reads it.
        dialog.add_option("a")
        self.assertEqual(dialog.uses.count(), 1)
        self.assertTrue(dialog.uses_note.isHidden())

    def test_it_opens_on_the_option_asked_for_and_says_what_reads_it(self):
        loadout = _own(options={"a": {"type": "toggle", "default": "true"},
                                 "ceres": {"type": "toggle", "default": "true"}})
        loadout.ops = [Op(origin="", order=1, uid="g", summary="a group",
                          source='<Group Condition="$ceres"><ModOp/></Group>')]
        loadout.options["ceres"]["label"] = "Shrine of Ceres"
        loadout.ops.append(Op(origin="", order=2, uid="h", summary="a group of ops, only if $my-mod.ceres",
                              source='<Group Condition="$my-mod.ceres"><ModOp/></Group>'))
        dialog = self.make(loadout, focus="ceres")
        self.assertEqual(dialog.current()["name"], "ceres")
        # Each by what it does, and an option by its label.
        self.assertEqual([dialog.uses.item(row).text() for row in range(dialog.uses.count())],
                         ["a group", "a group of ops, only if option “Shrine of Ceres”"])
        dialog.list.setCurrentRow(0)
        self.assertEqual(dialog.uses.count(), 0)
        self.assertEqual(dialog.uses_note.text(), "No ModOp reads it yet.")

    def test_renamed_it_says_what_still_reads_the_old_name(self):
        loadout = _own(options={"ceres": {"type": "toggle", "default": "true"}})
        loadout.ops = [Op(origin="", order=1, uid="g", summary="a group",
                          source='<Group Condition="$ceres"><ModOp/></Group>')]
        dialog = self.make(loadout)
        dialog.name_edit.setText("vesta")
        dialog.name_edit.textEdited.emit("vesta")
        self.assertEqual(dialog.uses.count(), 1)
        self.assertEqual(dialog.uses_note.text(),
                         "Renamed here: 1 ModOp still reads $ceres. Change it there too.")

    def test_going_to_a_use_keeps_the_dialog(self):
        loadout = _own(options={"ceres": {"type": "toggle", "default": "true"}})
        loadout.ops = [Op(origin="", order=1, uid="g", summary="a group",
                          source='<Group Condition="$ceres"><ModOp/></Group>')]
        dialog = self.make(loadout)
        self.assertFalse(dialog.use_button.isEnabled())
        dialog.show_in_file = True
        dialog.uses.setCurrentRow(0)
        self.assertTrue(dialog.use_button.isEnabled())
        dialog.label_edit.setText("Ceres")
        dialog.label_edit.textEdited.emit("Ceres")
        dialog.show_use()
        self.assertEqual(dialog.wanted_use, ("op", "g"))
        self.assertEqual(loadout.options["ceres"]["label"], "Ceres")

    def test_changing_the_kind_starts_it_again_as_that_kind(self):
        dialog = self.make(_own())
        dialog.add_option("speed")
        dialog.kind_combo.setCurrentIndex(dialog.kind_combo.findData(settings.SLIDER))
        dialog.kind_combo.activated.emit(dialog.kind_combo.currentIndex())
        self.assertEqual(dialog.current()["values"], ("0", "10", "1"))
        self.assertEqual(dialog.low_edit.text(), "0")
        self.assertEqual(dialog.pages.currentIndex(), 2)


class TheExportTab(PlayingWindow):
    def test_it_says_what_your_mod_offers_and_what_it_lacks(self):
        loadout = self.window.loadout
        loadout.game = ANNO_117
        loadout.make_own_mod()
        output = self.window.output
        output.refresh()
        self.assertTrue(output._scripts_form.isRowVisible(output._options_row))
        self.assertEqual(output.options_label.text(), "none")
        loadout.unread = [Unread(origin="", order=0, source=GUARDED)]
        output.refresh()
        self.assertEqual(output.options_label.text(),
                         "none, and its ModOps read $roomier, which nothing declares")
        loadout.options = dict(DECLARED)
        output.refresh()
        self.assertEqual(output.options_label.text(), "3 options")

    def test_keeping_the_dialog_reads_the_mod_again(self):
        from anno_modding_studio.ui import options_editor_dialog

        loadout = self.window.loadout
        loadout.game = ANNO_117
        loadout.make_own_mod()

        def kept(dialog):
            dialog.declare_missing()
            dialog.add_option("roomier")
            dialog._accept()
            return int(options_editor_dialog.QDialog.DialogCode.Accepted)

        with mock.patch.object(options_editor_dialog.OptionsEditorDialog, "exec", kept), \
                mock.patch.object(self.window.readings, "options_declared") as declared:
            self.window.output.edit_options()
        declared.assert_called_once_with("")
        self.assertIn("roomier", loadout.options)

    def your_mod(self, game=ANNO_117):
        loadout = self.window.loadout
        loadout.game = game
        loadout.make_own_mod()
        return loadout

    def test_an_apply_that_reads_a_new_setting_opens_the_dialog_with_it(self):
        from anno_modding_studio.ui import options_editor_dialog

        loadout = self.your_mod()
        loadout.unread = [Unread(origin="", order=0, source=GUARDED)]
        seen = []

        def opened(dialog):
            seen.append(([dialog._list_text(item) for item in dialog.items], dialog.after_apply))
            return int(options_editor_dialog.QDialog.DialogCode.Rejected)

        with mock.patch.object(options_editor_dialog.OptionsEditorDialog, "exec", opened):
            # Turned down: the caller takes the Apply back.
            self.assertIs(self.window.output.propose_options("", []), False)
            # Read before the Apply too: not new, so not asked again.
            self.assertIsNone(self.window.output.propose_options("", ["roomier"]))
        self.assertEqual(seen, [(["roomier (new)"], True)])
        self.assertEqual(loadout.options, {})

    def test_a_name_opened_from_where_it_is_read_is_proposed_with_no_apply_to_undo(self):
        from anno_modding_studio.ui import options_editor_dialog

        loadout = self.your_mod()
        loadout.unread = [Unread(origin="", order=0, source=GUARDED)]
        seen = []

        def opened(dialog):
            seen.append(([dialog._list_text(item) for item in dialog.items], dialog.after_apply))
            return int(options_editor_dialog.QDialog.DialogCode.Rejected)

        with mock.patch.object(options_editor_dialog.OptionsEditorDialog, "exec", opened):
            self.window.open_option("", "roomier")
        self.assertEqual(seen, [(["roomier (new)"], False)])
        self.assertEqual(loadout.options, {})

    def test_not_for_anno_1800_nor_for_somebody_else_s_mod(self):
        loadout = self.your_mod(game="anno1800")
        loadout.unread = [Unread(origin="", order=0, source=GUARDED)]
        self.assertIsNone(self.window.output.propose_options("", []))
        loadout.game = ANNO_117
        loadout.layers = [ModLayer(origin="Theirs", name="Theirs", creator="someone else")]
        loadout.unread = [Unread(origin="Theirs", order=0, source=GUARDED)]
        self.assertIsNone(self.window.output.propose_options("Theirs", []))

    def test_both_xml_panes_ask_after_an_apply(self):
        loadout = self.your_mod()
        with mock.patch.object(self.window.output, "propose_options") as proposed:
            self.window.editor.typedApplied.emit("", ["before"], ("ops", "1000", "", "<ModOps/>"))
        proposed.assert_called_once_with("", ["before"])

        panel = self.window.file_panel
        loadout.unread = [Unread(origin="", order=0, source=GUARDED)]
        done = mock.Mock(moved=True)
        quiet = [mock.patch.object(panel, name) for name in ("is_typed_in", "revert_typed", "refresh", "applied")]
        quiet += [mock.patch.object(self.window, "_on_edited"),
                  mock.patch.object(self.window.editor, "refresh_from_loadout"),
                  mock.patch.object(self.window, "workspace", object())]
        from anno_modding_studio.core import modfile

        with mock.patch.object(modfile, "apply", return_value=(None, done)), \
                mock.patch.object(panel, "shown", object(), create=True), \
                mock.patch.object(panel, "origin", return_value=""), \
                mock.patch.object(self.window.output, "propose_options") as proposed:
            for one in quiet:
                one.start()
            try:
                self.window._apply_file("<ModOps/>")
            finally:
                for one in quiet:
                    one.stop()
        proposed.assert_called_once_with("", ["roomier"])

    def test_turning_the_proposals_down_takes_an_xml_view_apply_back(self):
        from anno_modding_studio.core import undo

        self.your_mod()
        typed = ("ops", "1000", "", "<ModOps/>")
        for top, answer, taken_back in (
            (mock.Mock(typed=typed), False, True),
            (mock.Mock(typed=typed), True, False),
            (mock.Mock(typed=typed), None, False),
            # Not the step that Apply recorded: never an older one.
            (mock.Mock(typed=tuple(part for part in typed)), False, False),
        ):
            with self.subTest(answer=answer, same=top.typed is typed), \
                    mock.patch.object(self.window.output, "propose_options", return_value=answer), \
                    mock.patch.object(undo.UndoHistory, "last_step", mock.PropertyMock(return_value=top)), \
                    mock.patch.object(self.window, "undo_last") as undone:
                self.window.editor.typedApplied.emit("", [], typed)
            self.assertEqual(undone.called, taken_back)

    def test_and_a_mod_file_tab_apply_with_its_text_put_back(self):
        loadout = self.your_mod()
        panel = self.window.file_panel
        loadout.unread = [Unread(origin="", order=0, source=GUARDED)]
        quiet = [mock.patch.object(panel, name) for name in ("is_typed_in", "revert_typed", "refresh", "applied")]
        quiet += [mock.patch.object(self.window, "_on_edited"),
                  mock.patch.object(self.window.editor, "refresh_from_loadout"),
                  mock.patch.object(self.window, "workspace", object())]
        from anno_modding_studio.core import modfile

        steps = iter([None, mock.Mock(), mock.Mock()])
        recorded = mock.PropertyMock(side_effect=lambda: next(steps))
        with mock.patch.object(modfile, "apply", return_value=(None, mock.Mock(moved=True))), \
                mock.patch.object(panel, "shown", object(), create=True), \
                mock.patch.object(panel, "origin", return_value=""), \
                mock.patch.object(self.window.output, "propose_options", return_value=False), \
                mock.patch.object(type(self.window.undo), "last_step", recorded), \
                mock.patch.object(self.window, "undo_last") as undone, \
                mock.patch.object(panel, "put_back") as put_back, \
                mock.patch.object(panel, "say") as said:
            for one in quiet:
                one.start()
            try:
                self.window._apply_file("<ModOps>typed</ModOps>")
            finally:
                for one in quiet:
                    one.stop()
        undone.assert_called_once_with()
        put_back.assert_called_once_with("<ModOps>typed</ModOps>")
        self.assertIn("Apply undone", said.call_args.args[0])


if __name__ == "__main__":
    unittest.main()
