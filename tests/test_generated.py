"""Generated copies: a library mod with the Studio's own files written over
it, built beside the library and linked in its place, so that the library's
copy stays as its author shipped it (docs/proposals/patches-proposal.md,
phase 1).

Junctions are a Windows thing, as is the game, so the library's half is too.
"""

from __future__ import annotations

import io
import json
import os
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

import _isolation  # noqa: F401 -- first: keeps the suite out of real files under any runner

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from support import IMYA_PATCH, IMYA_RADIUS, IMYA_TWEAKABLE  # noqa: E402

from anno_modding_studio.core import (  # noqa: E402
    disk,
    generated,
    imya,
    installplan,
    library,
    modio_cache,
    places,
)

PATCH = "data/config/export/main/asset/assets.xml"
TEXTURE = "data/graphics/thing/diffuse.dds"


def a_mod(folder: Path, name: str = "[Gameplay] Thing", patch: str = "<ModOps />") -> Path:
    mod = folder / name
    (mod / Path(PATCH).parent).mkdir(parents=True)
    (mod / PATCH).write_text(patch, encoding="utf-8")
    (mod / Path(TEXTURE).parent).mkdir(parents=True)
    (mod / TEXTURE).write_bytes(b"DDS " + bytes(range(256)))
    (mod / "modinfo.json").write_text(json.dumps({"ModID": "thing", "Version": "1"}), encoding="utf-8")
    return mod


class _Folder(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.library = self.root / "library" / "anno1800"
        self.source = a_mod(self.library)
        self.into = generated.folder_for(self.library, self.source.name)


class TestBuildingOne(_Folder):
    def test_beside_the_library_under_the_same_root(self):
        self.assertEqual(generated.root_for(self.library), self.root / "library" / "generated" / "anno1800")
        self.assertEqual(self.into.parent, generated.root_for(self.library))

    def test_with_nothing_written_it_is_the_mod_file_for_file(self):
        made = generated.build(self.source, self.into, {})
        self.assertTrue(made.built)
        self.assertEqual(made.wrong, [])
        self.assertEqual(generated.check(self.into), [])
        for rel in (PATCH, TEXTURE, "modinfo.json"):
            with self.subTest(rel=rel):
                self.assertEqual((self.into / rel).read_bytes(), (self.source / rel).read_bytes())

    def test_text_is_copied_and_anything_else_linked(self):
        """A tool that writes into a mod folder in place writes into its text;
        linked, that write would go through into the library's copy."""
        made = generated.build(self.source, self.into, {})
        self.assertEqual((made.copied, made.linked, made.written), (2, 1, 0))
        self.assertTrue(os.path.samefile(self.into / TEXTURE, self.source / TEXTURE))
        self.assertFalse(os.path.samefile(self.into / PATCH, self.source / PATCH))
        (self.into / PATCH).write_text("<ModOps><!-- written in place --></ModOps>", encoding="utf-8")
        self.assertEqual((self.source / PATCH).read_text(encoding="utf-8"), "<ModOps />")

    def test_where_no_hard_link_can_be_made_it_is_copied(self):
        with mock.patch.object(generated.os, "link", side_effect=OSError("another volume")):
            made = generated.build(self.source, self.into, {})
        self.assertEqual((made.copied, made.linked), (3, 0))
        self.assertEqual(generated.check(self.into), [])

    def test_written_files_go_over_the_authors_and_beside_them(self):
        written = {PATCH: b"<ModOps><!-- mine --></ModOps>", "data/new.include.xml": b"<ModOps />"}
        made = generated.build(self.source, self.into, written, edits=[{"kind": "test"}])
        self.assertEqual(made.written, 2)
        self.assertEqual((self.into / PATCH).read_bytes(), written[PATCH])
        self.assertTrue((self.into / "data/new.include.xml").is_file())
        self.assertEqual((self.source / PATCH).read_text(encoding="utf-8"), "<ModOps />")
        self.assertEqual(generated.check(self.into), [])

    def test_it_says_what_it_is_made_of(self):
        generated.build(self.source, self.into, {PATCH: b"<ModOps />\n"}, edits=[{"kind": "test"}],
                        meta={"mod_id": "thing"})
        said = json.loads((self.into / generated.MANIFEST).read_text(encoding="utf-8"))
        self.assertEqual(said["convention"], generated.CONVENTION)
        self.assertEqual(said["source"], str(self.source))
        self.assertEqual(said["mod"], {"mod_id": "thing"})
        self.assertEqual(list(said["written"]), [PATCH])
        self.assertEqual(said["edits"], [{"kind": "test"}])
        self.assertEqual(generated.source_of(self.into), self.source)
        self.assertTrue(generated.is_generated(self.into, self.library))
        self.assertFalse(generated.is_generated(self.source, self.library))

    def test_built_of_the_same_it_is_left_as_it_is(self):
        generated.build(self.source, self.into, {PATCH: b"<ModOps />\n"})
        self.assertTrue(generated.is_current(self.into, self.source, {PATCH: b"<ModOps />\n"}))
        self.assertFalse(generated.build(self.source, self.into, {PATCH: b"<ModOps />\n"}).built)
        self.assertFalse(generated.is_current(self.into, self.source, {PATCH: b"<ModOps/>"}))
        self.assertTrue(generated.build(self.source, self.into, {PATCH: b"<ModOps/>"}).built)

    def test_the_authors_mod_changing_builds_it_again(self):
        generated.build(self.source, self.into, {})
        (self.source / "readme.txt").write_text("new in 1.1", encoding="utf-8")
        self.assertFalse(generated.is_current(self.into, self.source, {}))
        generated.build(self.source, self.into, {})
        self.assertEqual((self.into / "readme.txt").read_text(encoding="utf-8"), "new in 1.1")

    def test_the_check_finds_what_is_not_as_it_says(self):
        generated.build(self.source, self.into, {PATCH: b"<ModOps />\n"})
        (self.into / "modinfo.json").write_text("{}", encoding="utf-8")
        (self.into / PATCH).write_bytes(b"<ModOps>changed</ModOps>")
        (self.into / "stray.txt").write_text("", encoding="utf-8")
        (self.into / TEXTURE).unlink()
        self.assertEqual(
            sorted(generated.check(self.into)), sorted([PATCH, TEXTURE, "modinfo.json", "stray.txt"])
        )
        self.assertEqual(generated.check(self.source), [generated.MANIFEST])

    def test_one_that_does_not_check_out_is_not_moved_in(self):
        generated.build(self.source, self.into, {})
        with mock.patch.object(generated, "check", side_effect=lambda folder: ["x"]):
            made = generated.build(self.source, self.into, {PATCH: b"<ModOps>new</ModOps>"})
        self.assertEqual(made.wrong, ["x"])
        self.assertFalse(made.built)
        self.assertEqual((self.into / PATCH).read_text(encoding="utf-8"), "<ModOps />")
        self.assertEqual([p.name for p in self.into.parent.iterdir()], [self.into.name])

    def test_removed_only_if_it_is_one(self):
        generated.remove(self.source)
        self.assertTrue(self.source.is_dir())
        generated.build(self.source, self.into, {})
        generated.remove(self.into)
        self.assertFalse(self.into.exists())

    def test_materialised_it_stands_on_its_own(self):
        generated.build(self.source, self.into, {})
        generated.materialise(self.into)
        self.assertFalse((self.into / generated.MANIFEST).exists())
        self.assertFalse(generated.is_generated(self.into, self.library))


def _zip(entries: dict) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zipped:
        for name, data in entries.items():
            zipped.writestr(name, data)
    return buffer.getvalue()


@unittest.skipUnless(os.name == "nt", "junctions are a Windows thing")
class _Linked(_Folder):
    """A library mod linked into a mods folder, as an install leaves it."""

    def setUp(self):
        super().setUp()
        self.mods = self.root / "mods"
        self.mods.mkdir()
        for patched in (
            mock.patch.dict(os.environ, {
                modio_cache.ENV_ROOT: str(self.root / "modio"),
                library.ENV_LIBRARY: str(self.root / "library"),
            }),
            mock.patch.object(places, "backups_dir", return_value=self.root / "backups"),
            mock.patch.object(installplan.switches, "documents_mods", return_value=None),
        ):
            patched.start()
            self.addCleanup(patched.stop)
        linked = library.link(self.source, self.mods)
        library.note_link(self.library, linked, library.COPY)
        self.link = linked.link
        self.name = self.link.name

    def generate(self, written=None):
        return library.generate(self.library, self.name, written or {PATCH: b"<ModOps><!-- mine --></ModOps>"})


class TestALinkToAGeneratedCopy(_Linked):
    def test_generated_the_link_points_at_the_copy_and_the_library_is_untouched(self):
        made = self.generate()
        self.assertTrue(made.built)
        self.assertEqual(library.target_of(self.link), self.into)
        self.assertEqual(library.links(self.library)[self.name]["generated"], str(self.into))
        self.assertIn("mine", (self.link / PATCH).read_text(encoding="utf-8"))
        self.assertEqual((self.source / PATCH).read_text(encoding="utf-8"), "<ModOps />")

    def test_it_is_still_the_librarys_copy_to_whatever_asks_which_mod_it_is(self):
        self.generate()
        self.assertEqual(library.source_of(self.link, self.library), self.source)
        self.assertEqual(library.kind_of(self.link, self.library), library.COPY)
        self.assertEqual(library.copy_name(self.library, self.link), self.source.name)
        self.assertTrue(library.holds(self.link, self.library))

    def test_with_nothing_written_it_goes_back_to_the_librarys_copy(self):
        self.generate()
        self.assertIsNone(library.generate(self.library, self.name, {}))
        self.assertEqual(library.target_of(self.link), self.source)
        self.assertNotIn("generated", library.links(self.library)[self.name])
        self.assertFalse(self.into.exists())

    def test_switched_off_and_on_it_comes_back_pointing_at_the_copy(self):
        self.generate()
        self.assertTrue(library.unlink(self.library, self.link))
        self.assertFalse(self.link.exists())
        self.assertEqual(library.relink(self.library, self.name), self.link)
        self.assertEqual(library.target_of(self.link), self.into)

    def test_a_copy_not_checking_out_is_not_pointed_at(self):
        with mock.patch.object(generated, "check", side_effect=lambda folder: ["x"]):
            made = self.generate()
        self.assertEqual(made.wrong, ["x"])
        self.assertEqual(library.target_of(self.link), self.source)

    def test_deleting_the_mod_deletes_its_generated_copy(self):
        self.generate()
        self.assertTrue(library.remove_copy(self.library, self.link))
        self.assertFalse(self.source.exists())
        self.assertFalse(self.into.exists())
        self.assertFalse(self.link.exists())

    def test_put_back_it_is_the_mod_the_game_was_loading(self):
        self.generate()
        done = library.put_back([self.mods], self.library)
        self.assertEqual(done.materialised, [self.link])
        self.assertFalse(library.is_junction(self.link))
        self.assertIn("mine", (self.link / PATCH).read_text(encoding="utf-8"))
        self.assertEqual((self.link / TEXTURE).read_bytes()[:4], b"DDS ")
        self.assertFalse((self.link / generated.MANIFEST).exists())
        self.assertFalse(self.source.exists())

    def test_put_back_switched_off_too(self):
        self.generate()
        library.unlink(self.library, self.link)
        done = library.put_back([self.mods], self.library)
        self.assertEqual(done.materialised, [self.link])
        self.assertIn("mine", (self.link / PATCH).read_text(encoding="utf-8"))
        self.assertFalse(self.source.exists())

    def test_installed_again_it_is_linked_as_shipped(self):
        self.generate()
        archive = self.root / "thing.zip"
        archive.write_bytes(_zip({"%s/modinfo.json" % self.name: '{"ModID": "thing", "Version": "2"}'}))
        done = library.install(archive, self.library, self.mods, replace=self.link)
        self.assertEqual(done.placed, [self.library / self.name])
        self.assertEqual(library.target_of(self.link), self.library / self.name)
        self.assertFalse(self.into.exists())
        self.assertNotIn("generated", library.links(self.library)[self.name])


class TestABuildOfTheMod(_Linked):
    def test_built_again_from_the_loadout_its_link_is_to_what_was_built(self):
        """Play builds a mod you edited in place into the library before it
        writes any settings: a generated copy made of what was there before
        is not the mod any more."""
        from anno_modding_studio.core import build
        from anno_modding_studio.core.changes import Change, Loadout, ModLayer

        self.generate()
        loadout = Loadout(game="anno1800")
        loadout.set_layer(ModLayer(origin=self.name, name="Thing", folder=str(self.link)))
        loadout.set_change(Change(
            guid="1000", asset_label="Farm", path="/Values/Building/Radius", tag="Radius",
            old_value="20", new_value="30", origin=self.name, authored=True,
        ))
        done = build.write(loadout, [self.name], self.mods, self.library)
        self.assertEqual(done.failed, [])
        self.assertEqual(library.target_of(self.link), self.library / self.name)
        self.assertNotIn("generated", library.links(self.library)[self.name])
        self.assertFalse(self.into.exists())
        self.assertIn("<Radius>30</Radius>", (self.link / PATCH).read_text(encoding="utf-8"))


class TestTheSweepOfModsOnThisPC(_Linked):
    def test_a_mod_reached_through_its_generated_copy_and_in_the_library_is_one_mod(self):
        from anno_modding_studio.core import discovery

        (self.source / PATCH).write_text(
            '<ModOps><ModOp Type="addNextSibling" GUID="1"><Asset><Values><Standard>'
            "<GUID>1500010001</GUID></Standard></Values></Asset></ModOp></ModOps>",
            encoding="utf-8",
        )
        self.generate({"readme.txt": b"mine"})
        mods = discovery.mods_in(self.mods, discovery.WHERE_MODS) + discovery.mods_in(
            self.library, discovery.WHERE_LIBRARY)
        owners = discovery.guid_owners(mods, Path(PATCH))
        self.assertEqual(len(owners["1500010001"]), 1)


class TestAModIsReadAsItsAuthorShippedIt(_Linked):
    """Read through its link, a mod with a generated copy is the library's
    copy: the copy's Skips are a loadout's edits, not the author's (R3-27,
    hands-on run 3: re-added after Make this live, a patched mod read its
    switched-off ops as shipped off, and the next save emptied the patch)."""

    THEIRS = (
        '<ModOps><ModOp Type="merge" GUID="1000" Path="/Values/Building">'
        "<Radius>30</Radius></ModOp></ModOps>"
    )

    def setUp(self):
        super().setUp()
        (self.source / PATCH).write_text(self.THEIRS, encoding="utf-8")
        self.generate({PATCH: self.THEIRS.replace('Type="merge"', 'Type="merge" Skip="1"').encode("utf-8")})

    def test_the_author_folder_of_the_link_is_the_librarys_copy(self):
        self.assertTrue(disk.same_file(generated.author_folder(self.link), self.source))
        self.assertTrue(disk.same_file(generated.author_folder(self.into), self.source))

    def test_a_folder_inside_the_copy_is_the_same_folder_in_the_library(self):
        inner = Path(PATCH).parent
        self.assertTrue(disk.same_file(generated.author_folder(self.link / inner), self.source / inner))

    def test_any_other_folder_is_itself(self):
        self.assertEqual(generated.author_folder(self.mods), self.mods)
        self.assertEqual(generated.author_folder(self.source), self.source)

    def test_read_through_the_link_no_op_of_theirs_is_off(self):
        from anno_modding_studio.core import modops

        self.assertIn('Skip="1"', (self.link / PATCH).read_text(encoding="utf-8"))
        report, ops = modops.read_source(self.link, "[Gameplay] Thing")
        self.assertEqual(report.error, "")
        self.assertEqual(len(ops), 1)
        self.assertNotIn("Skip", ops[0].attrs)


class TestAnUpdateKeepsTheSettings(_Linked):
    """An update installs the author's fresh folder, and the player's
    iModYourAnno settings go into a generated copy of it -- never into the
    library's copy."""

    def setUp(self):
        super().setUp()
        (self.source / IMYA_PATCH).write_text(IMYA_TWEAKABLE, encoding="utf-8")
        tweaks = imya.read(self.source)
        self.generate(imya.changed_files(self.source, tweaks, {IMYA_RADIUS: "30"}))

    def test_the_settings_are_carried_into_a_generated_copy_of_the_new_version(self):
        self.assertEqual(imya.read(self.link).chosen(), {IMYA_RADIUS: "30"})
        archive = self.root / "thing.zip"
        archive.write_bytes(_zip({
            "%s/modinfo.json" % self.name: '{"ModID": "thing", "Version": "2"}',
            "%s/%s" % (self.name, IMYA_PATCH): IMYA_TWEAKABLE,
        }))
        done = library.install(archive, self.library, self.mods, replace=self.link)
        self.assertEqual(done.carried.kept, {IMYA_RADIUS: "30"})
        self.assertEqual(done.carried.error, "")
        fresh = self.library / self.name
        self.assertEqual(imya.read(fresh).chosen(), {})
        self.assertEqual(library.target_of(self.link), self.into)
        self.assertEqual(generated.source_of(self.into), fresh)
        self.assertEqual(json.loads((self.link / "modinfo.json").read_text())["Version"], "2")
        self.assertEqual(imya.read(self.link).chosen(), {IMYA_RADIUS: "30"})

    def test_settings_at_the_authors_defaults_write_nothing(self):
        tweaks = imya.read(self.source)
        self.assertTrue(tweaks.is_set_to({}))
        self.assertEqual(imya.changed_files(self.source, tweaks, {IMYA_RADIUS: "20"}), {})
        self.assertNotEqual(imya.changed_files(self.source, tweaks, {IMYA_RADIUS: "25"}), {})


if __name__ == "__main__":
    unittest.main()
