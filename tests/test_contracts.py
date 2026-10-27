"""The words the Studio writes into mods and game files, and finds there later.

An export, a Play and a backup leave marks for a later Studio to recognise: the
line that makes an Anno 117 profile the Studio's, the comments over its ModOps,
the block of names in a texts file, the block in a modinfo.  Those files live on
other people's machines and on mod.io, so a mark can never be migrated.  A
reworded one is simply not recognised: Play writes ``Anno Modding Studio
(2).txt`` beside the old profile, the Studio's comments come back as the
author's notes, a second names block is added, a patch loses the mod it
patches.  The same holds for the names the Studio finds its own things by.

So each is pinned here, word for word, from the constant the code uses.  A test
that fails here is a change to a contract.  Don't change the expected text:
add a new marker beside the old one and keep reading the old one, as
docs/standards/code-standards.md, section 11 says.
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import _isolation  # noqa: F401 -- first: keeps the suite out of real files under any runner

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from anno_modding_studio.core import (  # noqa: E402
    backup,
    changes,
    convert,
    dataversion,
    gamefiles,
    gameversion,
    generated,
    generator,
    index,
    library,
    modinfo,
    modops,
    places,
    projects,
    records,
    saving,
    splicing,
    switches,
    texts,
)
from anno_modding_studio.core.games import ANNO_117  # noqa: E402
from anno_modding_studio.ui import where  # noqa: E402

FROZEN = (
    "Text the Studio wrote into mods and game files is recognised by later "
    "versions. Don't reword it: add a new marker and keep reading this one "
    "(docs/standards/code-standards.md, section 11)."
)


class ContractCase(unittest.TestCase):
    def pinned(self, actual, expected):
        self.assertEqual(actual, expected, FROZEN)


class GameFiles(ContractCase):
    def test_the_anno_117_profile_line_is_pinned(self):
        # Reworded before 0.1.0, when Play came to play a loadout: frozen
        # from 0.1.0 as it is here.
        self.pinned(
            switches.STUDIO_LINE,
            "## Written by Anno Modding Studio; every Play rewrites it from the loadout it plays.",
        )
        self.pinned(switches.STUDIO_PROFILE, "Anno Modding Studio")

    def test_a_profile_written_with_the_line_is_recognised_as_the_studios(self):
        with tempfile.TemporaryDirectory() as folder:
            path = switches.write_profile(folder, switches.STUDIO_PROFILE, "Mine", {"a": True}, False)
            self.assertIn(switches.STUDIO_LINE + "\n", path.read_text(encoding="utf-8"))
            self.assertTrue(switches.is_studios(path))
            self.assertTrue(switches.named_as_studios(path))


class ModOpsComments(ContractCase):
    def test_the_marks_over_and_around_ops_are_pinned(self):
        self.pinned(changes.APP_NAME, "Anno Modding Studio")
        self.pinned(changes.SWITCHED_OFF_MARK, "switched off in Anno Modding Studio")
        self.pinned(changes.STUDIO_SAYS_MARK, ":AMS:")
        self.pinned(changes.NOTE_MARK, "note:")

    def test_an_op_switched_off_is_written_as_it_was_in_0_1_0(self):
        self.pinned(changes.switched_off(['  <ModOp Type="remove" GUID="1" />']), [
            "  <!-- switched off in Anno Modding Studio:",
            '  <ModOp Type="remove" GUID="1" />',
            "  -->",
        ])

    def test_the_note_is_written_as_it_was_in_0_1_0(self):
        self.pinned(generator._notes("why  so"), ["  <!-- note: why so -->"])

    def test_what_exports_before_the_mark_said_is_still_known(self):
        said = modops._SAID_BEFORE_THE_MARK
        for line in (
            " Bakery  |  GUID 1010 ",
            " Bakery  |  kept as written: it works its value out",
            " Bakery  |  every asset matching //Values/Trader",
            " kept exactly as written: Anno Modding Studio could not read it (why)",
            " WARNING: something",
        ):
            self.assertIsNotNone(said.match(line), "%r: %s" % (line, FROZEN))

    def test_the_mark_on_a_splice_is_pinned(self):
        self.pinned(splicing.MARK, "Anno Modding Studio")
        self.pinned(splicing._comment("added here"), "<!-- Anno Modding Studio: added here -->")


class TextsBlock(ContractCase):
    def test_the_names_block_is_pinned(self):
        self.pinned(texts._BEGIN, (
            "<!-- Anno Modding Studio: the in-game names of the assets this mod adds. "
            "Written on every export; edit them in the Studio. -->"
        ))
        self.pinned(texts._END, "<!-- /Anno Modding Studio names -->")

    def test_a_block_written_is_found_again_and_not_added_twice(self):
        block = texts.names_block(ANNO_117, [("2001000", "Mill")])
        once = texts.merged(None, block)
        twice = texts.merged(once, block)
        self.assertEqual(twice.count(texts._BEGIN), 1, FROZEN)


class ModinfoBlock(ContractCase):
    def test_the_block_and_its_keys_are_pinned(self):
        self.pinned(modinfo.STUDIO_KEY, "AnnoModdingStudio")
        self.pinned(
            (modinfo.PATCHES_KEY, modinfo.NEEDS_KEY, modinfo.BUILD_KEY,
             modinfo.BUILT_AGAINST_KEY, modinfo.STUDIO_VERSION_KEY),
            ("patches", "needs", "build", "builtAgainst", "studio"),
        )
        self.pinned(
            (modinfo.AGAINST_GAME, modinfo.AGAINST_UPDATE, modinfo.AGAINST_GAME_VERSION,
             modinfo.AGAINST_ARCHIVE, modinfo.AGAINST_ASSETS, modinfo.ASSETS_HASH),
            ("game", "update", "gameVersion", "archive", "assets", "sha1:"),
        )

    def test_a_block_written_by_0_1_0_is_read_back(self):
        declared = {"AnnoModdingStudio": {
            "patches": "theirs", "needs": ["other"], "build": 3, "studio": "0.1.0",
            "builtAgainst": {"game": "Anno 1800", "assets": "sha1:abc"},
        }}
        from anno_modding_studio.core import builds

        self.assertEqual(modinfo.declared_patch_of(declared), "theirs", FROZEN)
        self.assertEqual(modinfo.declared_needs_of(declared), ["other"], FROZEN)
        self.assertEqual(builds.declared_build(declared), 3, FROZEN)
        self.assertIs(builds.built_on_other_data(declared, "anno1800", "abc"), False, FROZEN)


class WhereTheStudioFindsItsOwn(ContractCase):
    """Names and keys a later Studio finds what an earlier one kept by: a
    change leaves what is there where no Studio looks."""

    def test_a_backup_store_is_named_as_it_was_in_0_1_0(self):
        self.pinned(backup.slug(r"C:\Program Files (x86)\Ubisoft\Anno 1800\mods"),
                    "c-program-files-x86-ubisoft-anno-1800-mods")
        self.pinned(backup.MANIFEST, "backup.json")

    def test_the_asset_fingerprint_is_what_it_was_in_0_1_0(self):
        # It is in every exported mod's builtAgainst, in loadouts and in the
        # keys of game-updates.json: another algorithm reads every mod built
        # before it as built on other game data.
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "assets.xml"
            path.write_bytes(
                b"<Assets><Asset><Values><Standard><GUID>1</GUID></Standard></Values></Asset></Assets>")
            self.pinned(index.fingerprint(path), "1a4f18eedbb371486217d9cdd2026edfe7a182e3")

    def test_the_names_of_the_records_are_pinned(self):
        self.pinned(
            (library.REGISTRY, library.LINKS, library.REFERENCES, library.LIVE),
            ("installed.json", "links.json", "references.json", "live.json"),
        )
        self.pinned((records.FORMAT, records.WRITTEN_BY, records.ENTRIES),
                    ("format", "written_by", "entries"))
        # What a built copy's registry entry says of the project it was built
        # from (core.builtby).
        self.pinned((library.BUILT, library.PROJECT, library.MOD_ID, library.BUILD, library.WAS),
                    ("built", "project", "mod_id", "build", "was"))
        self.pinned(gameversion.REMEMBERED_NAME, "game-updates.json")
        self.pinned(generated.MANIFEST, "generated.json")
        self.pinned(gamefiles.STAMP_NAME, ".anno-studio-read.json")
        self.pinned(changes.LOADOUT_SUFFIX, ".annoload")
        self.pinned((saving.JOURNALS, saving.FILES, saving.ENTRY_KEYS),
                    ("saving", "files", ("target", "part", "old", "existed")))

    def test_the_kinds_of_a_conversion_step_are_pinned(self):
        self.pinned(convert.STEPS, ("move", "copy", "aside"))

    def test_the_data_version_is_kept_as_it_was_in_0_1_0(self):
        # The gate that stops a build that would misread the folders: a key
        # renamed, and every older build reads no data version and starts.
        self.pinned(dataversion.FILE_NAME, "data-version.json")
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder)
            (folder / "data-version.json").write_text('{"data_version": 1, "app_version": "0.1.0"}',
                                                      encoding="utf-8")
            self.pinned(dataversion.read(folder), dataversion.Stamp(folder, 1, "0.1.0"))
            (folder / "data-version.json").unlink()
            with mock.patch.object(dataversion.places, "data_folders", return_value=[folder]):
                dataversion.record()
            written = json.loads((folder / "data-version.json").read_text(encoding="utf-8"))
            self.pinned(sorted(written), ["app_version", "data_version"])

    def test_a_project_file_is_named_and_says_what_it_is_as_in_0_1_0(self):
        # The files cache finds projects by the suffix, and a project is
        # told from any other JSON by its kind.
        self.pinned((projects.PROJECT_SUFFIX, projects.KIND), (".annoproj", "project"))

    def test_the_studio_s_folders_and_settings_are_named_as_in_0_1_0(self):
        # A loadout names its projects relative to the Studio's folder.
        self.pinned((places.loadouts_dir().name, places.projects_dir().name, places.exports_dir().name),
                    ("loadouts", "projects", "exports"))
        self.pinned(places.SETTINGS_NAME, "settings.ini")
        # Renamed, the library looks empty while every junction still
        # points into it.
        self.pinned(where.LIBRARY_KEY, "library_dir")

    def test_a_generated_copy_says_what_it_was_made_of_as_in_0_1_0(self):
        # What is read as the author's files goes by it (R3-27).
        with tempfile.TemporaryDirectory() as folder:
            source, copy = Path(folder) / "library" / "Mod", Path(folder) / "generated" / "Mod"
            source.mkdir(parents=True)
            (source / "modinfo.json").write_text("{}", encoding="utf-8")
            generated.build(source, copy, {"data/a.xml": b"<ModOps/>"})
            self.pinned(json.loads((copy / generated.MANIFEST).read_text(encoding="utf-8"))["source"],
                        str(source))
            (copy / generated.MANIFEST).write_text(json.dumps({"convention": 1, "source": "C:/lib/Mod"}),
                                                   encoding="utf-8")
            self.pinned(generated.source_of(copy), Path("C:/lib/Mod"))

    def test_the_live_loadout_is_recorded_as_in_0_1_0(self):
        with tempfile.TemporaryDirectory() as folder:
            lib = Path(folder)
            library.set_live(lib, "C:/loadouts/Mine.annoload", at=1.0, fingerprint="f", profile="Main")
            written = json.loads((lib / library.LIVE).read_text(encoding="utf-8"))
            self.pinned(sorted(written), ["at", "fingerprint", "format", "launched", "loadout", "profile",
                                          "written_by"])
            (lib / library.LIVE).write_text(json.dumps({
                "format": 1, "written_by": "0.1.0", "loadout": "C:/loadouts/Theirs.annoload",
                "profile": "Second", "at": 2.0, "fingerprint": "e", "launched": 3.0,
            }), encoding="utf-8")
            self.pinned((library.live(lib), library.live_profile(lib)),
                        ("C:/loadouts/Theirs.annoload", "Second"))


if __name__ == "__main__":
    unittest.main()
