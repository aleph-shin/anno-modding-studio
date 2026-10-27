"""A mod kept in more files than its patch (file tab proposal, phase 4).

A file an ``<Include>`` names is read where the line stands, as it always
was.  What is new is that it stays a file: each op says which file it is in
(``Op.file``), the line is kept in its place (``Op.includes``), and an
export writes the files again.  Everything shown and typed in is still the
mod's ops in one text.
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
from pathlib import Path

import _isolation  # noqa: F401 - first: keeps the suite out of real files

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import test_import  # noqa: E402
import test_ui  # noqa: E402
from PySide6.QtGui import QTextCursor  # noqa: E402

from anno_modding_studio.core import (  # noqa: E402
    build,
    games,
    generator,
    modfile,
    modops,
    reader,
)
from anno_modding_studio.core.changes import (  # noqa: E402
    Change,
    Loadout,
    ModLayer,
    Op,
)
from anno_modding_studio.ui.navigation import TAB_EDIT, TAB_FILE  # noqa: E402


def radius(value) -> str:
    return (
        '<ModOp Type="merge" GUID="1000" Path="/Values/Building"><Radius>%s</Radius></ModOp>'
        % value
    )


class FilesCase(test_import.ImportCase):
    """A mod of three files: its patch, one it includes, and one that one
    includes."""

    mod = test_import.TestOpsKeptInOtherFiles.mod

    MAIN = radius(1) + '<!-- the parts --><Include File="/parts/one.xml" />' + radius(2)
    ONE = radius(11) + '<Include File="two.xml"/>' + radius(12)
    TWO = radius(21) + '<Group Condition="#x">' + radius(22) + "</Group>"

    def folder(self, main=None, **beside) -> Path:
        files = {"parts|one.xml": self.ONE, "parts|two.xml": self.TWO}
        files.update(beside)
        return self.mod(self.MAIN if main is None else main, **files)

    def loadout_of(self, root: Path) -> Loadout:
        report = reader.analyse(root, self.index, origin="m")
        loadout = Loadout(game=games.ANNO_1800)
        loadout.set_layer(ModLayer(origin="m", folder=str(root)))
        loadout.ops = list(report.ops)
        for change in report.changes:
            loadout.changes[change.key] = change
        loadout.unread = report.unread("m")
        return loadout

    def written(self, loadout: Loadout, files: bool = True) -> generator.Written:
        return generator.written(generator.for_mod(loadout, "m"), files=files)

    def laid(self, loadout: Loadout) -> list:
        """``(file, what it is)`` for each thing the mod holds, in order."""
        return [
            (op.file, "includes " + op.includes if op.includes
             else "group" if op.scope else op.source.split("<Radius>")[1].split("<")[0])
            for op in sorted(loadout.ops, key=lambda op: op.order)
        ]

    def radii(self, text: str) -> list:
        return [part.split("<")[0] for part in text.split("<Radius>")[1:]]


class TestWhichFileAnOpIsIn(FilesCase):
    def test_each_op_says_its_file_and_each_line_is_kept_in_its_place(self):
        loadout = self.loadout_of(self.folder())
        self.assertEqual(self.laid(loadout), [
            ("", "1"),
            ("", "includes parts/one.xml"),
            ("parts/one.xml", "11"),
            ("parts/one.xml", "includes parts/two.xml"),
            ("parts/two.xml", "21"),
            ("parts/two.xml", "group"),
            ("parts/two.xml", "22"),
            ("parts/one.xml", "12"),
            ("", "2"),
        ])
        line = next(op for op in loadout.ops if op.includes == "parts/one.xml")
        # The line as written, with the comment over it; no ModOp, like a
        # group's tag.
        self.assertEqual(line.source, '<Include File="/parts/one.xml"/>')
        self.assertEqual(line.note, "the parts")
        self.assertTrue(line.scope)
        self.assertFalse(line.searches)

    def test_the_line_is_nothing_the_reading_counts(self):
        report = reader.analyse(self.folder(), self.index, origin="m")
        # Six ModOps and a group, and nothing said of either line.
        self.assertEqual(len(report.results), 7)
        self.assertFalse([one for one in report.results if one.op.attrs.get(modops.INCLUDES)])
        self.assertEqual(report.unread("m"), [])

    def test_the_element_in_any_case(self):
        loadout = self.loadout_of(self.folder(main='<include File="/parts/one.xml" />'))
        self.assertEqual(loadout.files_of("m"), {"parts/one.xml": "", "parts/two.xml": "parts/one.xml"})

    def test_a_file_on_a_condition_or_in_a_group_is_written_into_the_one_that_names_it(self):
        """As every file was.  A change of the Studio's own among its ops is
        written with the group closed round it, which a file cannot be."""
        for main in (
            '<Include File="/parts/one.xml" Condition="#x"/>',
            '<Group Condition="#x"><Include File="/parts/one.xml"/></Group>',
            '<Group><Include File="/parts/one.xml"/></Group>',
        ):
            with self.subTest(main=main):
                loadout = self.loadout_of(self.folder(main=main, **{"parts|one.xml": radius(11)}))
                self.assertEqual(loadout.files_of("m"), {})
                self.assertEqual({op.file for op in loadout.ops}, {""})

    def test_and_so_is_a_file_another_mods_folder_holds(self):
        # The line climbs out of the mod's own folder, so the mod is put in a
        # folder of this test's: left in the temporary folder, the file it
        # climbs to was %TEMP%\shared\pool.xml, the same one for every run on
        # the machine, and a run beside this one could not write it.
        outer = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, outer, True)
        root = self.folder(main='<Include File="../../../../../../shared/pool.xml"/>')
        root = Path(shutil.move(str(root), str(outer / "mod")))
        shared = outer / "shared"
        shared.mkdir()
        (shared / "pool.xml").write_text(test_import.modops_file(radius(5)), encoding="utf-8")
        loadout = self.loadout_of(root)
        self.assertEqual(self.radii(self.written(loadout).text), ["5"])
        self.assertEqual(loadout.files_of("m"), {})

    def test_a_file_in_such_a_file_is_written_into_it_too(self):
        loadout = self.loadout_of(self.folder(
            main='<Include File="/parts/one.xml" Condition="#x"/>',
        ))
        self.assertEqual(loadout.files_of("m"), {})

    def test_an_op_carried_unread_says_its_file(self):
        loadout = self.loadout_of(self.folder(**{"parts|two.xml": "<Strange/>" + radius(21)}))
        (entry,) = loadout.unread
        self.assertEqual(entry.file, "parts/two.xml")

    def test_it_is_saved_with_the_loadout(self):
        loadout = self.loadout_of(self.folder())
        saved = Path(tempfile.mkdtemp()) / "kept.amsproj"
        self.addCleanup(shutil.rmtree, saved.parent, True)
        loadout.save(saved)
        self.assertEqual(self.laid(Loadout.load(saved)), self.laid(loadout))


class TestAModWrittenAsItsFiles(FilesCase):
    def test_each_file_holds_its_own_ops_and_the_lines_that_name_the_others(self):
        whole = self.written(self.loadout_of(self.folder()))
        self.assertEqual(self.radii(whole.text), ["1", "2"])
        self.assertIn('<!-- note: the parts -->\n  <Include File="/parts/one.xml"/>', whole.text)
        self.assertEqual(sorted(whole.files), ["parts/one.xml", "parts/two.xml"])
        one, two = whole.files["parts/one.xml"], whole.files["parts/two.xml"]
        self.assertEqual(self.radii(one), ["11", "12"])
        self.assertLess(one.index("<Radius>11"), one.index('<Include File="two.xml"/>'))
        self.assertLess(one.index('<Include File="two.xml"/>'), one.index("<Radius>12"))
        # A group is its tags, in the file it was in.
        self.assertEqual(self.radii(two), ["21", "22"])
        self.assertIn('<Group Condition="#x">', two)
        for text in (whole.text, one, two):
            self.assertTrue(text.startswith('<?xml version="1.0" encoding="UTF-8"?>\n<ModOps>'))
            self.assertTrue(text.endswith("</ModOps>\n"))
            modops.etree.fromstring(text.encode("utf-8"))

    def test_shown_it_is_the_same_ops_in_one_file_with_no_such_line(self):
        whole = self.written(self.loadout_of(self.folder()), files=False)
        self.assertEqual(self.radii(whole.text), ["1", "11", "21", "22", "12", "2"])
        self.assertNotIn("<Include", whole.text)
        self.assertEqual(whole.files, {})

    def test_each_span_says_which_file_it_is_in(self):
        whole = self.written(self.loadout_of(self.folder()))
        lines = {"": whole.text.split("\n"), **{k: v.split("\n") for k, v in whole.files.items()}}
        found = []
        for span in whole.spans:
            said = "\n".join(lines[span.file][span.first - 1:span.last])
            found.append((span.file, span.kind, self.radii(said)))
        self.assertIn(("parts/one.xml", generator.SPAN_INCLUDE, []), found)
        self.assertIn(("parts/two.xml", generator.SPAN_OP, ["21"]), found)
        self.assertIn(("", generator.SPAN_OP, ["2"]), found)

    def test_an_export_writes_the_files_and_reads_back_as_the_mod_it_was(self):
        root = self.folder()
        loadout = self.loadout_of(root)
        out = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, out, True)
        build.write(loadout, ["m"], out)
        (folder,) = [path for path in out.iterdir() if path.is_dir()]
        self.assertEqual(modops.laid_out(folder), modops.laid_out(root))
        self.assertTrue((folder / "parts/two.xml").is_file())
        again = self.loadout_of(folder)
        self.assertEqual(self.laid(again), self.laid(loadout))

    def test_written_over_its_files_of_modops_are_saved_and_named_first(self):
        """The backup of an export with Replace held the patch, the texts and
        the modinfo, and not parts/extra.xml, which the same export rewrote;
        the question named only assets.xml and modinfo.json (H2-11)."""
        from PySide6.QtWidgets import QApplication

        from anno_modding_studio.core import backup
        from anno_modding_studio.ui.output_panel import OutputPanel

        loadout = self.loadout_of(self.folder())
        out = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, out, True)
        (folder,) = build.write(loadout, ["m"], out).written
        before = {inside: (folder / inside).read_text(encoding="utf-8")
                  for inside in ("parts/one.xml", "parts/two.xml")}

        app = QApplication.instance() or QApplication([])
        self.assertIsNotNone(app)
        panel = OutputPanel(loadout)
        self.addCleanup(panel.deleteLater)
        panel._chosen_output_dir = out
        said = panel.overwrite_warning("m")
        self.assertIn("assets.xml and the 2 files of ModOps it includes", said)
        self.assertIn("modinfo.json", said)

        kept = build.back_up(loadout, ["m"], out, into=out.parent / (out.name + "-kept"))
        self.addCleanup(shutil.rmtree, out.parent / (out.name + "-kept"), True)
        copied = {entry["path"] for entry in kept.files if entry["copy"]}
        self.assertLessEqual({"parts/one.xml", "parts/two.xml", "modinfo.json",
                              games.get(games.ANNO_1800).asset_relpath.as_posix()}, copied)
        for inside in before:
            (folder / inside).write_text("<ModOps/>", encoding="utf-8")
        backup.restore(kept)
        self.assertEqual({inside: (folder / inside).read_text(encoding="utf-8") for inside in before},
                         before)

    def test_written_over_what_it_carries_is_saved_and_put_back(self):
        """Out of a folder other than its own, an export copies what the mod
        ships over the files there; the backup left them out, so undoing it
        kept the copies and lost what had been there (A11-14)."""
        from anno_modding_studio.core import backup

        root = self.folder()
        (root / "data/config/game").mkdir(parents=True)
        (root / "data/config/game/x.cfg").write_text("theirs", encoding="utf-8")
        loadout = self.loadout_of(root)
        out = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, out, True)
        (folder,) = build.write(loadout, ["m"], out).written
        (folder / "data/config/game/x.cfg").write_text("mine", encoding="utf-8")
        (root / "icon.dds").write_text("new", encoding="utf-8")

        self.assertLessEqual({Path("data/config/game/x.cfg"), Path("icon.dds")},
                             set(build.writes(loadout, "m", folder).carried))
        kept = build.back_up(loadout, ["m"], out, into=out.parent / (out.name + "-kept"))
        self.addCleanup(shutil.rmtree, out.parent / (out.name + "-kept"), True)
        build.write(loadout, ["m"], out)
        self.assertEqual((folder / "icon.dds").read_text(encoding="utf-8"), "new")
        backup.restore(kept)
        self.assertEqual((folder / "data/config/game/x.cfg").read_text(encoding="utf-8"), "mine")
        self.assertFalse((folder / "icon.dds").exists())
        # Written back over its own folder, nothing is copied, so nothing is.
        self.assertEqual(build.writes(loadout, "m", root).carried, [])

    def test_a_file_named_out_of_the_mod_is_none_it_writes(self):
        """The export skips a file of ModOps whose path climbs out of the mod;
        the list the backup goes by named it all the same (A11-14)."""
        loadout = self.loadout_of(self.folder())
        (line,) = [op for op in loadout.ops if op.includes == "parts/two.xml"]
        line.includes = "../outside.xml"
        out = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, out, True)
        (folder,) = build.write(loadout, ["m"], out).written
        self.assertFalse((out / "outside.xml").exists())
        written = build.writes(loadout, "m", folder)
        self.assertEqual(written.included, [Path("parts/one.xml")])
        self.assertNotIn(Path("../outside.xml"), written.everything)

    def test_a_zip_holds_the_folder_an_export_writes(self):
        import zipfile

        loadout = self.loadout_of(self.folder())
        out = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, out, True)
        (folder,) = build.write(loadout, ["m"], out).written
        built = build.write(loadout, ["m"], out / "zips", zipped=True)
        self.assertEqual((built.written, built.failed), ([out / "zips" / "m.zip"], []))
        with zipfile.ZipFile(built.written[0]) as held:
            inside = {name: held.read(name) for name in held.namelist()}
        self.assertEqual(inside, {
            path.relative_to(out).as_posix(): path.read_bytes()
            for path in folder.rglob("*") if path.is_file()
        })
        self.assertIn("m/parts/two.xml", inside)

    def test_renamed_its_export_and_its_zip_take_the_new_name(self):
        """While Play's build keeps the folder name the mods folder knows it by."""
        import zipfile

        from PySide6.QtWidgets import QApplication

        from anno_modding_studio.ui.output_panel import OutputPanel

        loadout = self.loadout_of(self.folder())
        layer = loadout.layer("m")
        layer.declared = {"ModName": "m"}
        layer.name, layer.category = "Better Name", "Gameplay"
        out = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, out, True)
        (folder,) = build.write(loadout, ["m"], out, folder_of=generator.export_folder_for).written
        self.assertEqual(folder, out / "[Gameplay] Better Name")
        self.assertTrue((folder / "parts/two.xml").is_file())
        zipped = build.write(loadout, ["m"], out / "zips", zipped=True,
                             folder_of=generator.export_folder_for)
        self.assertEqual(zipped.written, [out / "zips" / "[Gameplay] Better Name.zip"])
        with zipfile.ZipFile(zipped.written[0]) as held:
            self.assertIn("[Gameplay] Better Name/parts/two.xml", held.namelist())
        (played,) = build.write(loadout, ["m"], out / "played").written
        self.assertEqual(played.name, "m")

        app = QApplication.instance() or QApplication([])
        self.assertIsNotNone(app)
        panel = OutputPanel(loadout)
        self.addCleanup(panel.deleteLater)
        panel._chosen_output_dir = out
        self.assertEqual(panel.export_target("m"), folder)
        # And what an export over it would write over is read from there.
        self.assertIn("modinfo.json", panel.overwrite_warning("m"))

    def test_a_mod_inside_it_writes_its_own_files_and_they_are_not_copied_over(self):
        """Found by the census (*Diagonal Residences*, which holds *shared
        pools and definitions*): a mod's export copies what the mod ships,
        and the mod inside it is part of that.  Written second, the outer
        mod put the inner one's files back as they shipped."""
        root = self.folder(main=radius(1))
        inner = root / "inner"
        patch = inner / "data/config/export/main/asset/assets.xml"
        patch.parent.mkdir(parents=True)
        patch.write_text(
            test_import.modops_file('<Include File="/more.xml"/>'), encoding="utf-8"
        )
        (inner / "more.xml").write_text(test_import.modops_file(radius(41)), encoding="utf-8")
        (inner / "modinfo.json").write_text("{}", encoding="utf-8")
        loadout = self.loadout_of(root)
        report = reader.analyse(inner, self.index, origin="m/inner")
        loadout.set_layer(ModLayer(origin="m/inner", folder=str(inner)))
        loadout.ops += list(report.ops)
        for change in report.changes:
            loadout.changes[change.key] = change
        # The inner mod says something else by now.
        held = next(op for op in loadout.ops if op.origin == "m/inner" and not op.includes)
        held.source = held.source.replace("41", "42")
        self.assertEqual(generator.kept_by_mods_inside(loadout, "m"), {"inner/more.xml"})
        self.assertEqual(generator.kept_by_mods_inside(loadout, "m/inner"), frozenset())
        out = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, out, True)
        build.write(loadout, ["m/inner", "m"], out)
        self.assertIn("<Radius>42<", (out / "m/inner/more.xml").read_text(encoding="utf-8"))

    def test_a_file_with_nothing_in_it_is_written_all_the_same(self):
        whole = self.written(self.loadout_of(self.folder(**{"parts|two.xml": ""})))
        self.assertEqual(self.radii(whole.files["parts/two.xml"]), [])

    def test_a_line_that_no_longer_reaches_its_file_names_it_from_the_top_of_the_mod(self):
        """The patch is written at the game's own path.  A mod read from a
        patch somewhere else names its files from there."""
        loadout = self.loadout_of(self.folder())
        line = next(op for op in loadout.ops if op.includes == "parts/one.xml")
        line.source = '<Include File="../elsewhere/one.xml"/>'
        self.assertIn('<Include File="/parts/one.xml"/>', self.written(loadout).text)
        # One that reaches it is left as its mod wrote it.
        line.source = '<Include File="../../../../../parts/one.xml"/>'
        self.assertIn('<Include File="../../../../../parts/one.xml"/>', self.written(loadout).text)

    def test_a_file_name_is_what_the_line_says_and_not_how_it_spells_it(self):
        """*Modular Specialists Pack* keeps a pool in ``AllItems&Specialists.xml``.
        Its line reaches the file, so it is left as written; taken for one
        that does not, it was written again with the ``&`` bare, which is
        not XML."""
        root = self.folder(
            main='<Include File="/parts/this&amp;that.xml"/>',
            **{"parts|this&that.xml": radius(31)},
        )
        loadout = self.loadout_of(root)
        self.assertEqual(loadout.files_of("m"), {"parts/this&that.xml": ""})
        whole = self.written(loadout)
        self.assertIn('<Include File="/parts/this&amp;that.xml"/>', whole.text)
        # And where it has to be named afresh, it is spelled as XML is.
        line = next(op for op in loadout.ops if op.includes)
        line.source = '<Include File="elsewhere.xml"/>'
        whole = self.written(loadout)
        self.assertIn('<Include File="/parts/this&amp;that.xml"/>', whole.text)
        modops.etree.fromstring(whole.text.encode("utf-8"))

    def test_switched_off_a_line_is_written_as_it_was(self):
        loadout = self.loadout_of(self.folder())
        for op in loadout.ops:
            if op.includes:
                op.active = False
        whole = self.written(loadout)
        self.assertEqual(sorted(whole.files), ["parts/one.xml", "parts/two.xml"])
        self.assertNotIn("off", whole.text)


class TestWhereTheStudiosOwnChangesGo(FilesCase):
    def own(self, loadout: Loadout, value: str, order: int) -> Change:
        change = Change(
            guid="1001", asset_label="Another", path="/Values/Building/Radius", tag="Radius",
            op="replace",
            old_value="10", new_value=value, origin="m",
        )
        change.order = order
        loadout.set_change(change)
        return change

    def at(self, loadout: Loadout, uid_of: str) -> int:
        return next(
            op.order for op in loadout.ops
            if not op.scope and "<Radius>%s<" % uid_of in op.source
        )

    def where(self, loadout: Loadout, value: str) -> str:
        whole = self.written(loadout)
        for name, text in {"": whole.text, **whole.files}.items():
            if "<Radius>%s</Radius>" % value in text:
                return name
        self.fail("not written: %s" % value)

    def test_one_made_after_the_mods_last_op_is_in_the_patch(self):
        loadout = self.loadout_of(self.folder(main='<Include File="/parts/one.xml" />'))
        self.own(loadout, "99", loadout.after_last_op("m"))
        self.assertEqual(self.where(loadout, "99"), "")

    def test_one_between_two_ops_of_a_file_is_in_that_file(self):
        loadout = self.loadout_of(self.folder())
        loadout.make_room("m", self.at(loadout, "21"))
        self.own(loadout, "99", self.at(loadout, "21") + 1)
        self.assertEqual(self.where(loadout, "99"), "parts/two.xml")
        whole = self.written(loadout)
        self.assertEqual(self.radii(whole.files["parts/two.xml"]), ["21", "99", "22"])

    def test_one_after_a_files_last_op_is_in_the_file_above(self):
        loadout = self.loadout_of(self.folder())
        after = max(op.order for op in loadout.ops if op.file == "parts/two.xml")
        loadout.make_room("m", after)
        self.own(loadout, "99", after + 1)
        self.assertEqual(self.where(loadout, "99"), "parts/one.xml")
        self.assertEqual(self.radii(self.written(loadout).files["parts/one.xml"]), ["11", "99", "12"])


class TestFilesThatCannotBeToldApart(FilesCase):
    """The game reads a file where its line stands.  Where the ops that say
    they are in it do not stand together there, written apart they would
    run in another order: the mod goes in one file, as it always did."""

    def test_an_op_of_a_file_standing_after_it_was_left(self):
        loadout = self.loadout_of(self.folder())
        last = next(op for op in loadout.ops if "<Radius>2<" in op.source)
        last.file = "parts/two.xml"
        whole = self.written(loadout)
        self.assertEqual(whole.files, {})
        self.assertNotIn("<Include", whole.text)
        self.assertEqual(self.radii(whole.text), ["1", "11", "21", "22", "12", "2"])

    def test_two_lines_naming_one_file(self):
        loadout = self.loadout_of(self.folder())
        for op in loadout.ops:
            if op.includes:
                op.includes = "parts/one.xml"
        self.assertEqual(self.written(loadout).files, {})

    def test_an_op_whose_file_no_line_names_is_written_where_it_stands(self):
        """The line taken out: the file's ops join the file above."""
        loadout = self.loadout_of(self.folder())
        gone = next(op for op in loadout.ops if op.includes == "parts/two.xml")
        loadout.drop_op(gone.uid)
        whole = self.written(loadout)
        self.assertEqual(sorted(whole.files), ["parts/one.xml"])
        self.assertEqual(self.radii(whole.files["parts/one.xml"]), ["11", "21", "22", "12"])


class TestTheLayoutOfAFile(FilesCase):
    """Comments that are no op's note, and blank lines, are kept where they
    stand (file tab proposal, phase 4, the last part)."""

    MAIN = (
        "\n<!-- a mod, by somebody -->\n\n<!-- farms -->\n" + radius(1) + "\n" + radius(2)
        + "\n\n\n<!-- a\n     long one -->\n\n<!-- its note -->\n" + radius(3)
        + '\n<Include File="/parts/one.xml" />\n<Group Condition="#x">\n'
        "<!-- in the group -->\n\n<!-- note in group -->\n" + radius(4)
        + "\n<!-- last in group -->\n</Group>\n\n<!-- the end -->\n"
    )
    ONE = "\n\n<!-- top of one -->\n\n" + radius(11) + "\n<!-- end of one -->\n"
    TWO = ""

    def held(self, loadout) -> list:
        return [
            (op.gap, op.file, "aside" if op.aside else op.note,
             " ".join(op.source.split())[:24] if op.aside else "")
            for op in sorted(loadout.ops, key=lambda op: op.order)
        ]

    def test_a_comment_directly_above_an_op_is_its_note_and_any_other_is_kept(self):
        loadout = self.loadout_of(self.folder())
        self.assertEqual(self.held(loadout), [
            (0, "", "aside", "<!-- a mod, by somebody "),
            (1, "", "farms", ""),
            (0, "", "", ""),
            (2, "", "aside", "<!-- a long one -->"),
            (1, "", "its note", ""),
            (0, "", "", ""),                                  # the line naming a file
            (1, "parts/one.xml", "aside", "<!-- top of one -->"),
            (1, "parts/one.xml", "", ""),
            (0, "parts/one.xml", "aside", "<!-- end of one -->"),
            (0, "", "", ""),                                  # the group
            (0, "", "aside", "<!-- in the group -->"),
            (1, "", "note in group", ""),
            (0, "", "aside", "<!-- last in group -->"),
            (1, "", "aside", "<!-- the end -->"),
        ])

    def test_a_comment_kept_is_no_op_of_the_mod(self):
        from anno_modding_studio.core import keptops

        loadout = self.loadout_of(self.folder())
        self.assertFalse([view for view in keptops.of(loadout, "m") if view.op.aside])
        report = reader.analyse(self.folder(), self.index, origin="m")
        self.assertEqual(len(report.results), 6)

    def test_written_each_is_where_it_stood_with_the_blank_lines_before_it(self):
        whole = self.written(self.loadout_of(self.folder()))
        self.assertEqual(whole.text, "\n".join((
            '<?xml version="1.0" encoding="UTF-8"?>',
            "<ModOps>",
            "  <!-- a mod, by somebody -->",
            "",
            "  <!-- note: farms -->",
            '  <ModOp Type="merge" GUID="1000" Path="/Values/Building">',
            "    <Radius>1</Radius>",
            "  </ModOp>",
            '  <ModOp Type="merge" GUID="1000" Path="/Values/Building">',
            "    <Radius>2</Radius>",
            "  </ModOp>",
            "",
            "",
            "  <!-- a",
            "     long one -->",
            "",
            "  <!-- note: its note -->",
            '  <ModOp Type="merge" GUID="1000" Path="/Values/Building">',
            "    <Radius>3</Radius>",
            "  </ModOp>",
            '  <Include File="/parts/one.xml"/>',
            '  <Group Condition="#x">',
            "    <!-- in the group -->",
            "",
            "    <!-- note: note in group -->",
            '    <ModOp Type="merge" GUID="1000" Path="/Values/Building">',
            "      <Radius>4</Radius>",
            "    </ModOp>",
            "    <!-- last in group -->",
            "  </Group>",
            "",
            "  <!-- the end -->",
            "",
            "</ModOps>",
            "",
        )))
        self.assertEqual(whole.files["parts/one.xml"], "\n".join((
            '<?xml version="1.0" encoding="UTF-8"?>',
            "<ModOps>",
            "",
            "  <!-- top of one -->",
            "",
            '  <ModOp Type="merge" GUID="1000" Path="/Values/Building">',
            "    <Radius>11</Radius>",
            "  </ModOp>",
            "  <!-- end of one -->",
            "",
            "</ModOps>",
            "",
        )))

    def test_an_export_read_back_is_laid_out_as_it_was(self):
        root = self.folder()
        loadout = self.loadout_of(root)
        out = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, out, True)
        build.write(loadout, ["m"], out)
        (folder,) = [path for path in out.iterdir() if path.is_dir()]
        self.assertEqual(self.held(self.loadout_of(folder)), self.held(loadout))
        self.assertEqual(modops.laid_out(folder), modops.laid_out(root))

    def test_a_comment_above_a_bare_group_is_the_note_of_the_first_op_in_it(self):
        """Found by the census (*City Watch City Skins*, *Modular Specialists
        Pack*): a bare group is read straight through and nothing stands
        for it, so the comment above it was nobody's and was dropped."""
        loadout = self.loadout_of(self.folder(main=(
            "<!-- a heading -->\n<!-- what the group is for -->\n<Group>\n"
            + radius(1) + "\n</Group>\n"
        ), **{"parts|one.xml": ""}))
        self.assertEqual(self.held(loadout), [
            (0, "", "aside", "<!-- a heading -->"),
            (0, "", "what the group is for", ""),
        ])

    def test_and_kept_where_it_stands_if_that_op_has_a_note_of_its_own(self):
        root = self.folder(main=(
            "<!-- over the group -->\n<Group>\n<!-- its own -->\n" + radius(1) + "\n</Group>\n"
        ), **{"parts|one.xml": ""})
        loadout = self.loadout_of(root)
        self.assertEqual(self.held(loadout), [
            (0, "", "aside", "<!-- over the group -->"),
            (0, "", "its own", ""),
        ])
        # And read back from an export it is the same.
        out = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, out, True)
        build.write(loadout, ["m"], out)
        (folder,) = [path for path in out.iterdir() if path.is_dir()]
        self.assertEqual(self.held(self.loadout_of(folder)), self.held(loadout))

    def test_a_file_that_is_only_a_comment_is_written(self):
        """Found by the census (*Production Works*, a mod of whose patch says
        only what the mod is for)."""
        loadout = self.loadout_of(self.folder(
            main="<!-- only the texts are here -->", **{"parts|one.xml": ""}
        ))
        view = generator.for_mod(loadout, "m")
        self.assertTrue(generator.writes_anything(view))
        self.assertFalse(generator.writes_for_the_game(view))
        self.assertIn("<!-- only the texts are here -->", self.written(loadout).text)

    def test_a_comment_in_a_file_written_into_a_group_goes_in_with_it(self):
        """Found by the census (*Military Attention*): a file included on a
        condition is carried as a group holding what the file holds, and
        only its elements were put in."""
        root = self.folder(
            main='<Include File="/parts/one.xml" Condition="#NotThere"/>',
            **{"parts|one.xml": radius(11) + "\n\n<!-- the end of it -->\n"},
        )
        loadout = self.loadout_of(root)
        self.assertIn("<!-- the end of it -->", self.written(loadout).text)
        out = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, out, True)
        build.write(loadout, ["m"], out)
        (folder,) = [path for path in out.iterdir() if path.is_dir()]
        self.assertEqual(modops.laid_out(folder), modops.laid_out(root))

    def test_a_comment_ending_one_file_is_not_the_note_of_the_op_starting_the_next(self):
        """Found by the census (*Military Attention*): two files included
        in a group are written into it, the last line of the one straight
        above the first op of the other.  Read back, the comment was that
        op's note."""
        root = self.folder(
            main='<Group Condition="#NotThere"><Include File="/parts/one.xml"/>'
                 '<Include File="/parts/two.xml"/></Group>',
            **{"parts|one.xml": radius(11) + "\n<!-- the end of one -->\n",
               "parts|two.xml": radius(21)},
        )
        loadout = self.loadout_of(root)
        held = self.held(loadout)
        self.assertIn((0, "", "aside", "<!-- the end of one -->"), held)
        self.assertEqual(held[-1], (1, "", "", ""))
        out = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, out, True)
        build.write(loadout, ["m"], out)
        (folder,) = [path for path in out.iterdir() if path.is_dir()]
        self.assertEqual(self.held(self.loadout_of(folder)), held)

    def test_what_the_studio_says_in_a_file_is_not_kept_as_somebodys_comment(self):
        """Its heading over an asset's ModOps, an op it wrote switched off,
        and a note under its mark."""
        loadout = self.loadout_of(self.folder(main=(
            "<!-- :AMS: Farm | GUID 1000 -->\n" + radius(1)
            + "\n\n<!-- switched off in Anno Modding Studio: " + radius(2).replace("--", "")
            + " -->\n\n<!-- note: said of it -->\n" + radius(3)
        ), **{"parts|one.xml": ""}))
        self.assertFalse([op for op in loadout.ops if op.aside])

    def test_it_is_saved_with_the_loadout(self):
        loadout = self.loadout_of(self.folder())
        saved = Path(tempfile.mkdtemp()) / "kept.amsproj"
        self.addCleanup(shutil.rmtree, saved.parent, True)
        loadout.save(saved)
        self.assertEqual(self.held(Loadout.load(saved)), self.held(loadout))

    def test_no_more_blank_lines_are_kept_than_anybody_meant(self):
        loadout = self.loadout_of(self.folder(main=radius(1) + "\n" * 40 + radius(2)))
        self.assertEqual(
            [op.gap for op in sorted(loadout.ops, key=lambda op: op.order)],
            [0, modops.MOST_BLANK_LINES],
        )

    def test_modops_typed_for_one_asset_keep_no_comment_as_a_thing_of_its_own(self):
        """The XML view's pane holds one asset's ModOps and is no file."""
        root = modops.etree.fromstring(
            ("<ModOps><!-- one --><!-- two -->%s</ModOps>" % radius(1)).encode("utf-8")
        )
        self.assertEqual(len(modops.parse_ops(root, [], "typed")), 1)
        self.assertEqual(len(modops.parse_ops(root, [], "typed", asides=True)), 2)


class TestFindingTheRegionsAgain(FilesCase):
    """Where nobody held the text while it was typed in: from the text as
    it was shown and the text now."""

    def setUp(self):
        super().setUp()
        from anno_modding_studio.core import modfile

        self.modfile = modfile
        self.was = modfile.shown(self.loadout_of(self.folder()), "m")
        self.lines = self.was.text.split("\n")

    def now(self, text: str) -> dict:
        return {
            region.file: (region.line, region.first, region.last)
            for region in self.modfile.regions_in(self.was, text)
        }

    def as_shown(self) -> dict:
        return {
            region.file: (region.line, region.first, region.last)
            for region in self.was.regions
        }

    def test_text_nobody_typed_in_has_them_where_they_were(self):
        self.assertEqual(self.now(self.was.text), self.as_shown())

    def test_a_line_typed_above_them_moves_them_down(self):
        text = "\n".join([*self.lines[:2], radius(50), *self.lines[2:]])
        self.assertEqual(
            self.now(text),
            {file: tuple(at + 1 for at in where) for file, where in self.as_shown().items()},
        )

    def test_a_line_typed_inside_one_is_in_it(self):
        one = self.as_shown()["parts/one.xml"]
        two = self.as_shown()["parts/two.xml"]
        at = two[2]
        text = "\n".join([*self.lines[:at - 1], radius(50), *self.lines[at - 1:]])
        self.assertEqual(self.now(text)["parts/two.xml"], (two[0], two[1], two[2] + 1))
        self.assertEqual(self.now(text)["parts/one.xml"], (one[0], one[1], one[2] + 1))

    def test_a_line_typed_after_ones_last_line_is_not(self):
        two = self.as_shown()["parts/two.xml"]
        text = "\n".join([*self.lines[:two[2]], radius(50), *self.lines[two[2]:]])
        self.assertEqual(self.now(text)["parts/two.xml"], two)

    def test_the_line_that_names_one_gone_it_is_no_region(self):
        two = self.as_shown()["parts/two.xml"]
        text = "\n".join([*self.lines[:two[0] - 1], *self.lines[two[0]:]])
        self.assertNotIn("parts/two.xml", self.now(text))
        self.assertIn("parts/one.xml", self.now(text))

    def test_a_long_text_with_one_line_typed_in_it_is_not_laid_line_for_line(self):
        import time

        long = self.modfile.Shown(
            origin="m", regions=list(self.was.regions),
            text="\n".join([*self.lines, *("<!-- %d -->" % n for n in range(120_000))]),
        )
        typed = long.text.split("\n")
        typed.insert(100_000, "<!-- typed -->")
        started = time.monotonic()
        found = self.modfile.regions_in(long, "\n".join(typed))
        self.assertLess(time.monotonic() - started, 5)
        self.assertEqual(
            [(region.line, region.first, region.last) for region in found],
            [(region.line, region.first, region.last) for region in self.was.regions],
        )


class TestTheFileOfANewOp(FilesCase):
    def test_a_new_op_is_in_the_file_of_what_it_stands_after(self):
        loadout = self.loadout_of(self.folder())
        after = next(op.order for op in loadout.ops if "<Radius>21<" in op.source)
        loadout.make_room("m", after)
        new = Op(source=radius(50), origin="m", order=after + 1)
        loadout.set_op(new)
        loadout.file_new_things("m", [new])
        self.assertEqual(new.file, "parts/two.xml")

    def test_and_at_the_top_of_a_file_where_that_is_the_line_naming_one(self):
        loadout = self.loadout_of(self.folder())
        line = next(op for op in loadout.ops if op.includes == "parts/two.xml")
        loadout.make_room("m", line.order)
        new = Op(source=radius(50), origin="m", order=line.order + 1)
        loadout.set_op(new)
        loadout.file_new_things("m", [new])
        self.assertEqual(new.file, "parts/two.xml")
        self.assertEqual(self.radii(self.written(loadout).files["parts/two.xml"]), ["50", "21", "22"])


class TestAnIncludeTypedThatCannotBeFollowed(FilesCase):
    def typed(self, line: str):
        from anno_modding_studio.core import modfile
        from anno_modding_studio.core.workspace import Workspace

        loadout = self.loadout_of(self.folder())
        shown = modfile.shown(loadout, "m")
        text = shown.text.replace("</ModOps>", "  %s\n</ModOps>" % line)
        found, done = modfile.apply(Workspace(self.index, loadout), "m", text, shown)
        return modfile, found, done, text

    def test_is_refused_where_the_mod_has_a_folder_as_where_it_has_none(self):
        """Read from the folder, it was passed by with nothing said: applied,
        the line was gone without a word."""
        for line in (
            '<Include File="/parts/missing.xml" Condition="#x"/>',
            '<Group Condition="#x"><Include File="/parts/missing.xml"/></Group>',
            '<Include File="../../../../../../nowhere/missing.xml"/>',
        ):
            with self.subTest(line=line):
                modfile, found, done, text = self.typed(line)
                self.assertIsNone(done)
                self.assertEqual(found.refused, modfile.INCLUDE_NOT_FOLLOWED)
                self.assertIn("missing.xml", text.split("\n")[found.line - 1])

    def test_while_one_on_a_condition_that_can_be_is_applied(self):
        _modfile, found, done, _text = self.typed(
            '<Include File="/parts/two.xml" Condition="#y"/>'
        )
        self.assertIsNotNone(done, found)


def residents(guid, value) -> str:
    return (
        '<ModOp Type="merge" GUID="%s" Path="/Values/Residence7"><Residents>%s</Residents></ModOp>'
        % (guid, value)
    )


class TestAModOfSeveralFilesInTheWindow(test_ui.StaleReadingsCase):
    # Its file is typed in, so the mod is yours: somebody else's is never
    # changed.
    YOURS = True
    SECOND = (
        "<ModOps>" + residents("1010030", 21) + '<Include File="/parts/one.xml"/>'
        + residents("1010030", 29) + "</ModOps>"
    )

    def load(self):
        parts = self.mods / self.LATE / "parts"
        parts.mkdir()
        (parts / "one.xml").write_text(
            "<ModOps>" + residents("1010030", 22) + '<Include File="two.xml"/>'
            + residents("1010031", 23) + "</ModOps>", encoding="utf-8",
        )
        (parts / "two.xml").write_text(
            "<ModOps>" + residents("1010031", 24) + "</ModOps>", encoding="utf-8"
        )
        super().load()

    def files(self) -> dict:
        loadout = self.window.loadout
        whole = build.as_files(generator.for_mod(loadout, self.LATE))
        return {
            name: [part.split("<")[0] for part in text.split("<Residents>")[1:]]
            for name, text in {"": whole.text, **whole.files}.items()
        }

    def type_in(self, pane, text: str) -> None:
        cursor = pane.textCursor()
        cursor.select(QTextCursor.SelectionType.Document)
        cursor.insertText(text)

    def test_its_file_is_shown_as_one_text(self):
        self.window.open_tab(TAB_FILE)
        self.window.file_panel.look_at(self.LATE)
        pane = self.window.file_panel.pane
        text = pane.toPlainText()
        self.assertEqual(
            [part.split("<")[0] for part in text.split("<Residents>")[1:]],
            ["21", "22", "24", "23", "29"],
        )
        # Each line that names a file is in it, and the file's ModOps
        # follow: a region the pane holds, drawn and no part of the text.
        lines = text.split("\n")
        regions = pane.regions()
        self.assertEqual([region.file for region in regions], ["parts/one.xml", "parts/two.xml"])
        one, two = regions
        self.assertIn('<Include File="/parts/one.xml"/>', lines[one.line - 1])
        self.assertIn('<Include File="two.xml"/>', lines[two.line - 1])
        said = lambda region: [  # noqa: E731
            part.split("<")[0]
            for part in "\n".join(lines[region.first - 1:region.last]).split("<Residents>")[1:]
        ]
        self.assertEqual(said(one), ["22", "24", "23"])
        self.assertEqual(said(two), ["24"])
        self.assertEqual((one.depth, two.depth, two.within), (0, 1, "parts/one.xml"))
        # Its heading, on the blank line it starts with.
        self.assertIn((one.first - 1, "In parts/one.xml"), pane.headings())
        self.assertEqual(lines[one.first - 1].strip(), "")
        # A bar in the strip for each file a line is inside.
        self.assertEqual(pane.stripes_at(one.line - 1), 0)
        self.assertEqual(pane.stripes_at(one.first), 1)
        self.assertEqual(pane.stripes_at(two.first), 2)

    def test_a_line_has_a_row_that_says_what_its_file_holds_and_no_switch(self):
        from PySide6.QtCore import Qt

        from anno_modding_studio.ui.changes_dock import OP_ROLE

        row = self.mod_row(self.LATE)
        row.setExpanded(True)
        line = next(op for op in self.window.loadout.ops if op.includes == "parts/one.xml")
        item = next(
            row.child(at) for at in range(row.childCount())
            if row.child(at).data(0, OP_ROLE) == line.uid
        )
        self.assertEqual(item.text(0), "includes parts/one.xml")
        self.assertEqual(item.text(1), "a file of the mod, with 2 ModOps")
        self.assertFalse(item.flags() & Qt.ItemFlag.ItemIsUserCheckable)

    def test_typed_in_its_file_what_is_new_is_in_the_file_of_what_it_follows(self):
        window = self.window
        window.open_tab(TAB_FILE)
        window.file_panel.look_at(self.LATE)
        window.file_panel.editRequested.emit(self.LATE)
        pane = window.file_panel.pane
        after_22 = "<Residents>22</Residents>\n  </ModOp>"
        text = pane.toPlainText().replace(after_22, after_22 + "\n" + residents("1010031", 56))
        text = text.replace("</ModOps>", residents("1010031", 55) + "\n</ModOps>")
        self.type_in(pane, text)
        window.file_panel.apply_button.click()
        wanted = {
            "": ["21", "29", "55"],
            "parts/one.xml": ["22", "56", "23"],
            "parts/two.xml": ["24"],
        }
        self.assertEqual(self.files(), wanted)
        window.undo_last()
        self.assertEqual(self.files()["parts/one.xml"], ["22", "23"])
        window.redo_last()
        self.assertEqual(self.files(), wanted)

    # -- the file tab: each file a region of the one text -------------------
    def edit_file(self):
        """Open the mod's file to type in; the pane."""
        window = self.window
        window.open_tab(TAB_FILE)
        window.file_panel.look_at(self.LATE)
        window.file_panel.editRequested.emit(self.LATE)
        return window.file_panel.pane

    def apply(self, text: str) -> str:
        """Type ``text`` over the file, changing only what differs, and
        apply it; what the line beside Apply says."""
        panel = self.window.file_panel
        panel.pane.type_over(text)
        panel.apply_button.click()
        return panel.status.text()

    def region(self, pane, file: str):
        return next(region for region in pane.regions() if region.file == file)

    def test_applied_as_it_stands_nothing_moves(self):
        pane = self.edit_file()
        before = self.files()
        cursor = pane.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        cursor.insertText(" ")
        self.window.file_panel.apply_button.click()
        self.assertEqual(self.window.file_panel.status.text(), "No change to the mod.")
        self.assertEqual(self.files(), before)

    def test_a_bare_group_typed_round_ops_is_said_not_to_be_kept(self):
        """R3-45, hands-on run 3: it went from the text with "No change to the
        mod." and nothing else."""
        pane = self.edit_file()
        before = self.files()
        text = pane.toPlainText()
        opened = text.index("<ModOp ")
        closed = text.index("</ModOp>", opened) + len("</ModOp>")
        said = self.apply(text[:opened] + "<Group>" + text[opened:closed] + "</Group>" + text[closed:])
        self.assertIn("No change to the mod.", said)
        self.assertIn("A <Group> with no Condition or anything else on it changes nothing", said)
        self.assertEqual(self.files(), before)
        self.assertEqual(modfile.bare_groups("<!-- <Group> --><Group><ModOp/></Group><Group Condition='x'>"), 1)

    def test_a_line_typed_at_the_end_of_a_file_is_the_files_and_after_it_is_not(self):
        pane = self.edit_file()
        two = self.region(pane, "parts/two.xml")
        # At the end of the file's last line: the file grows by it.
        cursor = QTextCursor(pane.document().findBlockByNumber(two.last - 1))
        cursor.movePosition(QTextCursor.MoveOperation.EndOfBlock)
        cursor.insertText("\n" + residents("1010031", 61))
        # On the line after it: in the file around it.
        grown = self.region(pane, "parts/two.xml")
        self.assertEqual(grown.last, two.last + 1)
        cursor = QTextCursor(pane.document().findBlockByNumber(grown.last))
        cursor.insertText(residents("1010031", 62))
        self.assertEqual(self.region(pane, "parts/two.xml").last, grown.last)
        self.window.file_panel.apply_button.click()
        self.assertEqual(self.files(), {
            "": ["21", "29"],
            "parts/one.xml": ["22", "62", "23"],
            "parts/two.xml": ["24", "61"],
        })

    def test_cut_from_one_file_and_pasted_in_another_an_op_is_in_that_one(self):
        pane = self.edit_file()
        lines = pane.toPlainText().split("\n")
        first = next(at for at, line in enumerate(lines) if "<Residents>23<" in line) - 1
        # The three lines of the op, cut.
        cursor = QTextCursor(pane.document().findBlockByNumber(first))
        cursor.movePosition(QTextCursor.MoveOperation.NextBlock, QTextCursor.MoveMode.KeepAnchor, 3)
        cut = cursor.selectedText().replace("\u2029", "\n")
        self.assertEqual(cut.count("<ModOp "), 1)
        cursor.removeSelectedText()
        # And pasted on the line that closes the mod's own file.
        last = next(
            at for at in range(pane.document().blockCount())
            if "</ModOps>" in pane.document().findBlockByNumber(at).text()
        )
        QTextCursor(pane.document().findBlockByNumber(last)).insertText(cut)
        self.window.file_panel.apply_button.click()
        said = self.window.file_panel.status.text()
        self.assertEqual(self.files(), {
            "": ["21", "29", "23"], "parts/one.xml": ["22"], "parts/two.xml": ["24"],
        })
        self.assertIn("in the order and the files you typed them in", said)
        # The op it was: it keeps its uid, and what each op comes to is there.
        self.assertEqual(len(self.comes_to(self.LATE)), 5)

    def test_dragged_in_the_outline_into_a_folded_file_it_is_in_it_and_shown(self):
        """#196: the same edit as the cut and paste above, from the outline."""
        self.edit_file()
        panel = self.window.file_panel
        pane = panel.pane
        pane.fold("parts/two.xml")
        tree = panel.outline.tree
        rows = []

        def walk(row):
            rows.append(row)
            for at in range(row.childCount()):
                walk(row.child(at))

        for at in range(tree.topLevelItemCount()):
            walk(tree.topLevelItem(at))
        op_21 = next(row for row in rows if "1010030" in row.text(0))
        line_two = next(row for row in rows if "two.xml" in row.text(0))
        self.assertTrue(panel._can_drop(op_21, line_two, None))
        panel._dropped(panel._thing(op_21), panel._thing(line_two), None)
        from PySide6.QtCore import QCoreApplication

        QCoreApplication.processEvents()
        self.assertNotIn("parts/two.xml", pane.folded())
        two = self.region(pane, "parts/two.xml")
        lines = pane.toPlainText().split("\n")
        self.assertIn("<Residents>21<", "\n".join(lines[two.first - 1:two.last]))
        self.assertIn("parts/two.xml", [region.file for region in pane.regions()])
        panel.apply_button.click()
        self.assertEqual(self.files(), {
            "": ["29"], "parts/one.xml": ["22", "23"], "parts/two.xml": ["24", "21"],
        })
        self.assertEqual(len(self.comes_to(self.LATE)), 5)

    def test_an_element_opened_in_one_file_and_closed_in_another_is_refused(self):
        """Each file is read as a file, as the game reads it: the group is
        left open in the one and closed where none is open in the other."""
        pane = self.edit_file()
        text = pane.toPlainText()
        opened = text.replace("  <Include File=\"two.xml\"/>", "  <Group>\n  <Include File=\"two.xml\"/>")
        closed = opened.replace("<Residents>24</Residents>\n  </ModOp>", "<Residents>24</Residents>\n  </ModOp>\n  </Group>")
        before = self.files()
        said = self.apply(closed)
        self.assertIn("Not applied. Line", said)
        self.assertEqual(self.files(), before)
        self.assertTrue(self.window.file_panel.has_unapplied())

    def test_a_line_typed_for_a_file_the_mod_has_brings_its_modops_in(self):
        (self.mods / self.LATE / "parts" / "three.xml").write_text(
            "<ModOps>" + residents("1010031", 31) + residents("1010031", 32) + "</ModOps>",
            encoding="utf-8",
        )
        pane = self.edit_file()
        said = self.apply(pane.toPlainText().replace(
            "</ModOps>", '  <Include File="/parts/three.xml"/>\n</ModOps>'
        ))
        self.assertEqual(
            said, "2 ModOps added. parts/three.xml is included, with its 2 ModOps."
        )
        self.assertEqual(self.files()["parts/three.xml"], ["31", "32"])
        # Shown under its line from here on.
        three = self.region(pane, "parts/three.xml")
        self.assertEqual(three.last - three.first, 6)

    def test_and_for_one_it_has_not_makes_the_file(self):
        pane = self.edit_file()
        said = self.apply(pane.toPlainText().replace(
            "</ModOps>", '  <Include File="/parts/new.xml"/>\n</ModOps>'
        ))
        self.assertEqual(said, "parts/new.xml is a new file of the mod.")
        self.assertEqual(self.files()["parts/new.xml"], [])
        # A line of its own to type in, which the next op goes on.
        new = self.region(pane, "parts/new.xml")
        cursor = QTextCursor(pane.document().findBlockByNumber(new.first - 1))
        cursor.insertText(residents("1010031", 71))
        self.window.file_panel.apply_button.click()
        self.assertEqual(self.files()["parts/new.xml"], ["71"])

    def test_the_line_taken_out_its_modops_are_in_the_file_it_was_in(self):
        pane = self.edit_file()
        said = self.apply(pane.toPlainText().replace('  <Include File="two.xml"/>\n', ""))
        self.assertIn("No line names parts/two.xml now", said)
        self.assertEqual(self.files(), {"": ["21", "29"], "parts/one.xml": ["22", "24", "23"]})
        self.assertFalse([op for op in self.window.loadout.ops if op.includes == "parts/two.xml"])

    def test_taken_out_with_its_modops_the_file_is_gone_and_nothing_more_is_said(self):
        pane = self.edit_file()
        text = pane.toPlainText()
        two = self.region(pane, "parts/two.xml")
        lines = text.split("\n")
        said = self.apply("\n".join(lines[:two.line - 1] + lines[two.last:]))
        self.assertEqual(said, "1 ModOp taken out.")
        self.assertEqual(self.files(), {"": ["21", "29"], "parts/one.xml": ["22", "23"]})

    def test_the_line_typed_over_names_the_file_anew(self):
        pane = self.edit_file()
        self.apply(pane.toPlainText().replace('File="two.xml"', 'File="second.xml"'))
        self.assertEqual(self.files(), {
            "": ["21", "29"], "parts/one.xml": ["22", "23"], "parts/second.xml": ["24"],
        })

    def test_a_condition_on_a_line_with_its_file_under_it_is_refused(self):
        pane = self.edit_file()
        said = self.apply(pane.toPlainText().replace(
            '<Include File="two.xml"/>', '<Include File="two.xml" Condition="#Other"/>'
        ))
        self.assertIn("it can only name a file of this mod", said)

    def test_a_line_whose_file_cannot_be_followed_is_refused(self):
        pane = self.edit_file()
        said = self.apply(pane.toPlainText().replace(
            "</ModOps>", '  <Include File="/parts/missing.xml" Condition="#Other"/>\n</ModOps>'
        ))
        self.assertIn("cannot follow this <Include>", said)

    def test_a_line_for_a_file_that_cannot_be_read_stays_carried_as_it_was(self):
        """Found by the census (*Super Specialists*): such a line is carried
        unread, for the game to read the file.  Shown, it is a line that
        names a file with nothing under it; taken for a new one, it became
        a file of the mod's with nothing in it, to be written over the one
        the game reads."""
        (self.mods / self.LATE / "parts" / "odd.xml").write_text(
            "stray text\n<ModOps>" + residents("1010031", 31) + "</ModOps>", encoding="utf-8"
        )
        pane = self.edit_file()
        said = self.apply(pane.toPlainText().replace(
            "</ModOps>", '  <Include File="/parts/odd.xml"/>\n</ModOps>'
        ))
        self.assertIn("could not be read", said)
        loadout = self.window.loadout
        self.assertNotIn("parts/odd.xml", loadout.files_of(self.LATE))
        (entry,) = [one for one in loadout.unread if one.origin == self.LATE]
        self.assertIn("odd.xml", entry.source)
        # And applied again as it stands, it is that still.
        cursor = pane.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        cursor.insertText(" ")
        self.window.file_panel.apply_button.click()
        self.assertEqual(self.window.file_panel.status.text(), "No change to the mod.")
        self.assertNotIn("parts/odd.xml", self.files())

    def test_two_lines_for_one_file_are_refused(self):
        pane = self.edit_file()
        said = self.apply(pane.toPlainText().replace(
            "</ModOps>", '  <Include File="/parts/two.xml"/>\n</ModOps>'
        ))
        self.assertIn("another <Include> already names this file", said)

    def test_a_files_lines_fold_away_under_its_heading_and_are_still_applied(self):
        pane = self.edit_file()
        one = self.region(pane, "parts/one.xml")
        document = pane.document()
        at = next(index for index, (line, _words) in enumerate(pane.headings())
                  if line == one.first - 1)
        self.assertEqual(dict(pane._heading_menu(at)).keys(), {"Fold this file"})
        pane.headingClicked.emit(at)
        self.assertEqual(pane.folded(), {"parts/one.xml"})
        self.assertTrue(document.findBlockByNumber(one.first - 1).isVisible())
        self.assertFalse(document.findBlockByNumber(one.first).isVisible())
        self.assertFalse(document.findBlockByNumber(one.last - 1).isVisible())
        self.assertTrue(document.findBlockByNumber(one.last).isVisible())
        self.assertIn(
            "In parts/one.xml · %d lines folded" % (one.last - one.first),
            [words for _line, words in pane.headings()],
        )
        self.assertEqual(dict(pane._heading_menu(at)).keys(), {"Unfold this file"})
        # Typed in elsewhere and applied: what is folded is in the text.
        before = self.files()
        said = self.apply(pane.toPlainText().replace(
            "</ModOps>", residents("1010031", 55) + "\n</ModOps>"
        ))
        self.assertEqual(said, "1 ModOp added.")
        self.assertEqual(self.files(), {**before, "": ["21", "29", "55"]})
        # Folded still, in the text written afresh; and going to a line in
        # it unfolds it.
        self.assertEqual(pane.folded(), {"parts/one.xml"})
        self.assertFalse(document.findBlockByNumber(one.first).isVisible())
        pane.go_to_line(one.first + 2)
        self.assertEqual(pane.folded(), set())
        self.assertTrue(document.findBlockByNumber(one.first).isVisible())

    def test_a_files_heading_says_it_can_be_clicked(self):
        """The heading folded on a click and nothing said so: it is drawn
        like the headings over assets, which do nothing."""
        from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
        from PySide6.QtGui import QHelpEvent, QMouseEvent
        from PySide6.QtWidgets import QToolTip

        pane = self.edit_file()
        self.window.resize(1400, 900)
        one = self.region(pane, "parts/one.xml")

        def middle_of(line: int) -> QPoint:
            return pane.cursorRect(
                QTextCursor(pane.document().findBlockByNumber(line - 1))
            ).center()

        def move_to(point: QPoint) -> None:
            pane.mouseMoveEvent(QMouseEvent(
                QEvent.Type.MouseMove, QPointF(point), QPointF(pane.viewport().mapToGlobal(point)),
                Qt.MouseButton.NoButton, Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier,
            ))

        self.assertEqual(pane._file_heading_at(middle_of(one.first)), "parts/one.xml")
        self.assertIsNone(pane._file_heading_at(middle_of(one.first + 1)))
        move_to(middle_of(one.first))
        self.assertEqual(pane.viewport().cursor().shape(), Qt.CursorShape.PointingHandCursor)
        move_to(middle_of(one.first + 1))
        self.assertEqual(pane.viewport().cursor().shape(), Qt.CursorShape.IBeamCursor)
        # And in words, on hover: what a click does now.
        for folded, said in ((False, "Click to fold this file's lines away."),
                             (True, "Click to show this file's lines.")):
            pane.fold("parts/one.xml", folded)
            point = middle_of(one.first)
            pane.viewportEvent(QHelpEvent(
                QEvent.Type.ToolTip, point, pane.viewport().mapToGlobal(point)
            ))
            self.assertEqual(QToolTip.text(), said)
        QToolTip.hideText()

    def test_the_outline_is_a_tree_of_the_files(self):
        self.edit_file()
        tree = self.window.file_panel.outline.tree
        tops = [tree.topLevelItem(at) for at in range(tree.topLevelItemCount())]
        self.assertEqual(
            [row.text(0) for row in tops][1], '<Include File="/parts/one.xml"/>'
        )
        one = tops[1]
        self.assertEqual(len(tops), 3)
        self.assertEqual(one.childCount(), 3)
        self.assertEqual(one.child(1).text(0), '<Include File="two.xml"/>')
        self.assertEqual(one.child(1).childCount(), 1)
        self.assertTrue(one.isExpanded())

    def test_a_comment_and_a_blank_line_typed_in_the_file_are_kept(self):
        pane = self.edit_file()
        text = pane.toPlainText()
        first = "  </ModOp>\n"
        said = self.apply(text.replace(first, first + "\n\n  <!-- the rest -->\n\n", 1))
        self.assertEqual(
            said, "1 comment added, changed or taken out. The blank lines are as you typed them."
        )
        self.assertIn("  </ModOp>\n\n\n  <!-- the rest -->\n\n", pane.toPlainText())
        loadout = self.window.loadout
        (kept,) = [op for op in loadout.ops if op.aside]
        self.assertEqual((kept.source, kept.gap, kept.file), ("<!-- the rest -->", 2, ""))
        # Nothing was read again, and it is no row anywhere.
        self.assertEqual(len(self.comes_to(self.LATE)), 5)
        tree = self.window.file_panel.outline.tree
        self.assertEqual(tree.topLevelItemCount(), 3)
        whole = build.as_files(generator.for_mod(loadout, self.LATE))
        self.assertIn("\n\n\n  <!-- the rest -->\n\n", whole.text)
        # One step to undo.
        self.window.undo_last()
        self.assertFalse([op for op in loadout.ops if op.aside])
        self.assertNotIn("the rest", pane.toPlainText())

    def test_a_blank_line_alone_is_a_change_and_no_rearrangement(self):
        pane = self.edit_file()
        text = pane.toPlainText()
        opens = '  <ModOp Type="merge" GUID="1010030" Path="/Values/Residence7">\n    <Residents>29<'
        self.assertIn(opens, text)
        said = self.apply(text.replace(opens, "\n\n" + opens))
        self.assertEqual(said, "The blank lines are as you typed them.")
        last = next(op for op in self.window.loadout.ops if "<Residents>29<" in op.source)
        self.assertEqual(last.gap, 2)

    def test_typed_in_the_xml_view_too(self):
        window = self.window
        window.open_tab(TAB_FILE)
        window.file_panel.look_at(self.LATE)
        window.file_panel.editRequested.emit(self.LATE)
        window.open_tab(TAB_EDIT)
        window._open_asset("1010031")
        window.editor.set_view("xml")
        xml = window.editor.xml
        after_24 = "<Residents>24</Residents>\n  </ModOp>"
        self.assertIn(after_24, xml.typing.toPlainText())
        self.type_in(xml.typing, xml.typing.toPlainText().replace(
            after_24, after_24 + "\n" + residents("1010031", 77)
        ))
        xml.apply_button.click()
        self.assertEqual(self.files(), {
            "": ["21", "29"],
            "parts/one.xml": ["22", "23"],
            "parts/two.xml": ["24", "77"],
        })

    def test_read_again_after_an_edit_before_it_the_files_are_what_they_were(self):
        """The mod typed in is read again from its text, which is one file."""
        window = self.window
        window.open_tab(TAB_FILE)
        window.file_panel.look_at(self.LATE)
        window.file_panel.editRequested.emit(self.LATE)
        pane = window.file_panel.pane
        self.type_in(pane, pane.toPlainText().replace(
            "</ModOps>", residents("1010031", 55) + "\n</ModOps>"
        ))
        window.file_panel.apply_button.click()
        before = self.files()
        with self.reading(now=True):
            # The mod before it changes: this one is read again.
            (early,) = [op for op in window.loadout.ops if op.origin == self.EARLY]
            early.active = False
            for change in window.loadout.changes_by_op(early.uid):
                change.active = False
            window._on_edited()
        self.assertTrue(self.started)
        self.assertEqual(self.files(), before)

    def test_and_so_are_they_for_a_mod_read_again_from_its_folder(self):
        before = self.files()
        with self.reading(now=True):
            (early,) = [op for op in self.window.loadout.ops if op.origin == self.EARLY]
            early.active = False
            for change in self.window.loadout.changes_by_op(early.uid):
                change.active = False
            self.window._on_edited()
        self.assertTrue(self.started)
        self.assertEqual(self.files(), before)
        self.assertEqual(
            len([op for op in self.window.loadout.ops if op.includes]), 2
        )
