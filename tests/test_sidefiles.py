"""A mod's other patched files, each said for what it is: a templates.xml
patch, a model file patch, link files."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

import _isolation  # noqa: F401 -- first: keeps the suite out of real files under any runner

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from anno_modding_studio.core import modops, sidefiles  # noqa: E402

TEMPLATES_1800 = """<ModOps>
  <!-- <ModOp Type="remove" Path="//Template[Name='Commented']"/> -->
  <ModOp Type="addNextSibling" Template="AirShip">
    <Template><Name>ObjectDummy</Name><Properties/></Template>
  </ModOp>
  <ModOp Type="add" Path="//Template[Name='Economy']/Properties"><Upgradable/></ModOp>
  <ModOp Type="remove" Template="ObjectDummy"/>
  <ModOp Type="replace" Path="//ItemBuyLimit"><ItemBuyLimit>99999</ItemBuyLimit></ModOp>
</ModOps>"""

# Anno 117 writes the kind as the attribute holding the path.
TEMPLATES_117 = """<ModOps>
  <ModOp Merge="//Locked[../../Name = 'Tech']"><Locked/></ModOp>
  <ModOp Append="//Template[Name='GuestHouse']"><Template><Name>GuestHouse_Harbour</Name></Template></ModOp>
</ModOps>"""

FC = """<ModOps>
  <ModOp Type="add" Path="/DummyRoot/Groups">
    <i><Name>1</Name><Id>$auto</Id></i>
    <i><Name>2</Name><Id>$auto(0)</Id></i>
  </ModOp>
</ModOps>"""


class _Folder(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

    def file(self, relative, text):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path


class TestTemplates(_Folder):
    def test_what_it_adds_changes_and_searches_for(self):
        path = self.file("templates.xml", TEMPLATES_1800)
        # AirShip is only where the new one goes; ObjectDummy is its own.
        self.assertEqual(sidefiles.templates(path),
                         "adds ObjectDummy; changes Economy; changes whatever //ItemBuyLimit finds")

    def test_the_117_spelling(self):
        path = self.file("templates.xml", TEMPLATES_117)
        self.assertEqual(sidefiles.templates(path), "adds GuestHouse_Harbour; changes Tech")

    def test_one_with_nothing_in_it(self):
        self.assertEqual(sidefiles.templates(self.file("templates.xml", "<ModOps><!-- none --></ModOps>")),
                         "has no ops")


class TestModelPatchAndLinks(_Folder):
    def test_a_world_map_patch(self):
        path = self.file("world_map_01.fc.xml", FC)
        self.assertEqual(sidefiles.model_patch(path),
                         "patches world_map_01.fc: 1 op adding 2 entries; its $auto ids are given "
                         "out when the game loads it")

    def test_link_files_name_what_they_read(self):
        self.file("data/islands/a.a7m.lnk", "data/islands/b.a7m\n")
        self.assertEqual(sidefiles.links(self.root, ["data/islands/a.a7m.lnk"]),
                         "each has the game read the file it names in its place, and "
                         "data/islands/a.a7m reads data/islands/b.a7m")


class TestInTheScan(_Folder):
    def test_each_is_said_for_what_it_is(self):
        self.file("data/config/export/main/asset/assets.xml", "<ModOps/>")
        self.file("data/config/export/main/asset/templates.xml", TEMPLATES_1800)
        self.file("data/graphics/ui/3d_objects/world_map/world_map_01.fc.xml", FC)
        for name in ("a", "b", "c"):
            self.file("data/islands/%s.a7m.lnk" % name, "data/islands/x.a7m")
        _patches, extras, _meta = modops.scan_folder(self.root)
        said = {extra.what: extra.detail for extra in extras}
        self.assertEqual(said["a templates.xml patch"],
                         "adds ObjectDummy; changes Economy; changes whatever //ItemBuyLimit finds")
        self.assertTrue(said["a model file patch"].startswith("patches world_map_01.fc"))
        self.assertTrue(said["3 link files"].startswith("each has the game read the file it names"))
        self.assertNotIn("a ModOps patch for another game file", said)

    def test_a_file_with_no_extension_replaces_no_game_file(self):
        """A README, a LICENSE and a .gitignore went by the extension none of
        them has, and were said to replace a game file outright.  A name that
        is all extension is no game file's, whichever it is."""
        for name in ("README", "LICENSE", ".gitignore", ".gitattributes", ".DS_Store",
                     "art/.DS_Store", "notes.txt"):
            self.file(name, "words")
        self.file("data/graphics/a.dds", "pixels")
        # A file a dot begins that has an extension goes by it, as any does.
        self.file("data/graphics/.b.dds", "pixels")
        _patches, extras, _meta = modops.scan_folder(self.root)
        said = {extra.what: extra.detail for extra in extras}
        self.assertEqual(said, {
            "2 files with no extension": "shipped alongside the mod",
            "1 .gitignore file": "shipped alongside the mod",
            "1 .gitattributes file": "shipped alongside the mod",
            "2 .DS_Store files": "shipped alongside the mod",
            "1 .txt file": "shipped alongside the mod",
            "2 .dds files": "replaces a game file outright and does not patch it",
        })

    def test_a_texts_or_infotip_patch_is_one_where_the_studio_reads_it(self):
        """Which the scan matched by hand, case and all: a capitalised folder
        was another game file, and a texts file under any gui folder was said
        to be read for names -- texts.patch_file looks in one place."""
        cases = {
            "Data/Config/GUI/texts_english.xml": "a localisation patch",
            "data/base/config/gui/Texts_German.xml": "a localisation patch",
            "data/gui/texts_english.xml": "a ModOps patch for another game file",
            "Data/InfoTips/export.bin.xml": "an InfoTip patch",
            "data/infotips/other.xml": "a ModOps patch for another game file",
        }
        for relative, wanted in cases.items():
            with self.subTest(relative=relative), tempfile.TemporaryDirectory() as tmp:
                self.root = Path(tmp)
                self.file(relative, "<ModOps/>")
                _patches, extras, _meta = modops.scan_folder(self.root)
                self.assertEqual([extra.what for extra in extras], [wanted])

    def test_nothing_of_a_bundled_mod_is_said_to_be_the_hosts(self):
        """The bundled mod is read on its own, and its texts patch and its
        .dds are its own; only its assets.xml used to be passed over."""
        self.file("inner/modinfo.json", "{}")
        self.file("inner/data/config/export/main/asset/assets.xml", "<ModOps/>")
        self.file("inner/data/config/gui/texts_english.xml", "<ModOps/>")
        self.file("inner/data/x.dds", "pixels")
        _patches, extras, _meta = modops.scan_folder(self.root)
        self.assertEqual([(extra.what, extra.where) for extra in extras],
                         [("a mod bundled inside this one", "inner/modinfo.json")])


if __name__ == "__main__":
    unittest.main()
