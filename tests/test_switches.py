"""Which installed mods the game is actually loading.

Both games record that on disk, and neither of them does it the same way.  The
shapes here were taken from a real installation rather than guessed: Anno 1800's
``-`` folder prefix from a mods folder with seven of them switched off that way,
and Anno 117's profile files from the ``how-to.txt`` the game itself ships in
``mods/`` -- checked against that installation's own ``mod-loader.log``, which
lists exactly the mods this reads as switched on.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import _isolation  # noqa: F401 -- first: keeps the suite out of real files under any runner

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from anno_modding_studio.core import switches  # noqa: E402
from anno_modding_studio.core.games import ANNO_117, ANNO_1800  # noqa: E402


def _folder(root: Path, *names) -> Path:
    for name in names:
        (root / name).mkdir(parents=True)
    return root


class FolderCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name) / "mods"
        self.root.mkdir()
        self.addCleanup(self._tmp.cleanup)


class TestReadingOffFoldersAlreadyRead(FolderCase):
    """Play has the inventory and the mods-folder dialog its reading when they
    ask which mods are on, so the folders are not listed a second time
    (P8-29). The answer has to be the one the listing gives."""

    def test_the_same_answer_as_listing_the_folder(self):
        _folder(self.root, "On", "-Off", "Host/Inside")
        names = ["On", "Off", "Host/Inside"]
        listed = switches.read(self.root, ANNO_1800, names=names)
        tops = switches.tops_of(ANNO_1800, self.root, [
            self.root / "On", self.root / "-Off", self.root / "Host" / "Inside", self.root / "Host",
        ])
        self.assertEqual(tops, [self.root / "On", self.root / "-Off", self.root / "Host"])
        given = switches.read(self.root, ANNO_1800, names=names, tops=tops)
        self.assertEqual((given.on, given.folders), (listed.on, listed.folders))

    def test_a_folder_of_mods_inside_one_is_where_they_start(self):
        """Anno 117's ``mods/.ubi`` holds the game's own mods, inside the
        folder that holds everyone else's."""
        _folder(self.root, ".ubi/free-farmfields-ubi", "Mine")
        tops = switches.tops_of(ANNO_117, self.root,
                                [self.root / ".ubi" / "free-farmfields-ubi", self.root / "Mine"])
        self.assertEqual(tops, [self.root / ".ubi" / "free-farmfields-ubi", self.root / "Mine"])


class TestTheMarkAnno1800Uses(FolderCase):
    """A folder name starting with "-" is how 1800 is told to skip a mod."""

    def test_a_marked_folder_reads_as_switched_off(self):
        _folder(self.root, "[Seal] Charcoal", "-[Seal] Buildable Salvagers")
        state = switches.read(self.root, ANNO_1800)
        self.assertTrue(state.is_on("[Seal] Charcoal"))
        self.assertFalse(state.is_on("[Seal] Buildable Salvagers"))

    def test_and_the_mod_is_still_named_without_it(self):
        """The mark is about the folder, not about the mod: the same mod, on or
        off, is the same mod, and everything else names it the same way."""
        _folder(self.root, "-One Free Reroll")
        state = switches.read(self.root, ANNO_1800)
        self.assertEqual(list(state.on), ["One Free Reroll"])
        self.assertEqual(state.folders["One Free Reroll"].name, "-One Free Reroll")

    def test_load_order_is_where_it_would_be_switched_on(self):
        """"-" sorts in front of every letter, so ordering by the raw folder name
        would stack every switched-off mod at the front of a folder the game is
        not even reading them from -- and move it somewhere else entirely the
        moment it was switched on."""
        mods = {"AAA": {}, "MMM": {}, "ZZZ": {}}
        self.assertEqual(switches.order(ANNO_1800, mods), ["AAA", "MMM", "ZZZ"])
        _folder(self.root, "AAA", "-MMM", "ZZZ")
        self.assertEqual(list(switches.read(self.root, ANNO_1800).on), ["AAA", "MMM", "ZZZ"])

    def test_switching_one_off_renames_its_folder(self):
        _folder(self.root, "Keep Me", "Not This")
        state = switches.read(self.root, ANNO_1800)
        written = switches.write(state, {"Keep Me": True, "Not This": False})
        self.assertEqual(written.changed, 1)
        self.assertTrue((self.root / "-Not This").is_dir())
        self.assertFalse((self.root / "Not This").exists())

    def test_and_switching_it_on_takes_the_mark_back_off(self):
        _folder(self.root, "-Not This")
        state = switches.read(self.root, ANNO_1800)
        switches.write(state, {"Not This": True})
        self.assertTrue((self.root / "Not This").is_dir())

    def test_nothing_moves_when_the_folder_already_agrees(self):
        _folder(self.root, "On", "-Off")
        state = switches.read(self.root, ANNO_1800)
        written = switches.write(state, {"On": True, "Off": False})
        self.assertEqual(written.changed, 0)
        self.assertIn("already", written.describe())

    def test_only_failures_say_nothing_was_switched(self):
        """Every change failing is no switch: the summary said "Switched
        your mods folder to match" over a folder nothing had moved in
        (A11-15)."""
        said = switches.Written(failed=[("a", "x")]).describe()
        self.assertNotIn("Switched", said)
        self.assertIn("1 could not be changed", said)
        partly = switches.Written(renamed=[("b", Path("-b"))], failed=[("a", "x")]).describe()
        self.assertTrue(partly.startswith("Switched your mods folder to match"))

    def test_a_name_collision_is_refused_rather_than_overwritten(self):
        """Both spellings present is somebody's half-finished rename; renaming
        onto it would merge two mods into one folder."""
        _folder(self.root, "Twice", "-Twice")
        state = switches.read(self.root, ANNO_1800)
        # Both map to the same mod, so ask for whichever one the read did not.
        written = switches.write(state, {"Twice": not state.is_on("Twice")})
        self.assertEqual(written.renamed, [])
        self.assertEqual(len(written.failed), 1)

    def test_a_bundled_mod_switched_with_its_host_is_not_a_failure(self):
        """The host's rename is the bundled mod's switch too. Asked for both
        at once, it was reported as one that could not be changed over a
        folder that had become exactly what was wanted (A8-9)."""
        names = ["Host", "Host/Inner"]
        for start, on in (("-Host/Inner", True), ("Host/Inner", False)):
            with self.subTest(on=on):
                root = _folder(self.root / ("on" if on else "off"), start)
                state = switches.read(root, ANNO_1800, names=names)
                written = switches.write(state, {"Host": on, "Host/Inner": on})
                self.assertEqual(written.failed, [])
                self.assertEqual([mod for mod, _path in written.renamed], ["Host"])
                self.assertNotIn("could not be changed", written.describe())
                after = switches.read(root, ANNO_1800, names=names)
                self.assertEqual(after.on, {"Host": on, "Host/Inner": on})

    def test_a_bundled_mod_asked_for_against_its_host_still_is(self):
        _folder(self.root, "Host/Inner")
        state = switches.read(self.root, ANNO_1800, names=["Host", "Host/Inner"])
        for wanted in ({"Host/Inner": False}, {"Host": True, "Host/Inner": False}):
            written = switches.write(state, wanted)
            self.assertEqual([mod for mod, _why in written.failed], ["Host/Inner"])
        self.assertTrue((self.root / "Host" / "Inner").is_dir())

    def test_only_the_folders_named_as_mods_are_counted(self):
        """A mods directory holds other things -- a shared ``data`` override,
        the loader's own log -- and the caller has just read which are mods."""
        _folder(self.root, "A Mod", "data")
        state = switches.read(self.root, ANNO_1800, names=["A Mod"])
        self.assertEqual(list(state.on), ["A Mod"])


class TestTheProfileAnno117Keeps(FolderCase):
    """117 lists mods by ModID in a profile file, "#" for off."""

    PROFILE = "\n".join(
        [
            "## Download mods at: https://mod.io/g/anno-117-pax-romana",
            "##",
            "## Disable a mod:",
            "## - Add a single `#` before it.",
            "EnableNewMods",
            "DisplayName default",
            "",
            "on-mod",
            "# off-mod",
            "somebody-elses-mod",
        ]
    )

    def profile(self, text=None, active="default") -> None:
        (self.root / "profiles").mkdir(exist_ok=True)
        (self.root / "profiles" / (active + ".txt")).write_text(
            text if text is not None else self.PROFILE, encoding="utf-8"
        )
        (self.root / "profile.txt").write_text(
            "## a comment\nActiveProfile %s\n" % active, encoding="utf-8"
        )

    def test_a_hash_in_front_of_a_mod_switches_it_off(self):
        _folder(self.root, "On Mod", "Off Mod")
        self.profile()
        state = switches.read(
            self.root,
            ANNO_117,
            ids={"On Mod": "on-mod", "Off Mod": "off-mod"},
            names=["On Mod", "Off Mod"],
        )
        self.assertTrue(state.is_on("On Mod"))
        self.assertFalse(state.is_on("Off Mod"))

    def test_prose_comments_are_not_read_as_switched_off_mods(self):
        _folder(self.root, "On Mod")
        self.profile()
        state = switches.read(self.root, ANNO_117, ids={"On Mod": "on-mod"})
        # "## - Add a single `#` before it." is prose about switching mods off,
        # not a mod that has been switched off.
        self.assertEqual(
            sorted(switches.read_profile(state.profile).on),
            ["off-mod", "on-mod", "somebody-elses-mod"],
        )

    def test_a_mod_nobody_listed_follows_enablenewmods(self):
        _folder(self.root, "Brand New")
        self.profile()
        state = switches.read(self.root, ANNO_117, ids={"Brand New": "brand-new"})
        self.assertTrue(state.is_on("Brand New"))

        self.profile(self.PROFILE.replace("EnableNewMods", "# EnableNewMods"))
        state = switches.read(self.root, ANNO_117, ids={"Brand New": "brand-new"})
        self.assertFalse(state.is_on("Brand New"))

    def test_the_pointer_file_chooses_which_profile_is_live(self):
        _folder(self.root, "On Mod")
        self.profile(active="default")
        self.profile("# on-mod\n", active="minimal")
        (self.root / "profile.txt").write_text("ActiveProfile minimal\n", encoding="utf-8")
        state = switches.read(self.root, ANNO_117, ids={"On Mod": "on-mod"})
        self.assertEqual(state.profile.stem, "minimal")
        self.assertFalse(state.is_on("On Mod"))

    def test_mods_listed_that_are_not_here_are_noticed_not_lost(self):
        """117 loads from several folders at once, so an id with no folder in
        this one is usually a real mod living somewhere else."""
        _folder(self.root, "On Mod")
        self.profile()
        state = switches.read(self.root, ANNO_117, ids={"On Mod": "on-mod"}, names=["On Mod"])
        self.assertIn("somebody-elses-mod", state.elsewhere)

    def test_the_profile_is_looked_for_beside_the_users_own_mods_too(self):
        """The installation's mods folder often has no profile of its own: the
        game keeps it under Documents and governs every folder with it."""
        _folder(self.root, "On Mod")
        documents = Path(self._tmp.name) / "docs"
        (documents / "mods" / "profiles").mkdir(parents=True)
        (documents / "mods" / "profiles" / "default.txt").write_text(
            "# on-mod\nEnableNewMods\n", encoding="utf-8"
        )
        with mock.patch.object(switches, "documents_mods", return_value=documents / "mods"):
            state = switches.read(self.root, ANNO_117, ids={"On Mod": "on-mod"})
        self.assertFalse(state.is_on("On Mod"))

    def test_switching_one_off_writes_a_hash_and_leaves_the_rest_alone(self):
        _folder(self.root, "On Mod", "Off Mod")
        self.profile()
        ids = {"On Mod": "on-mod", "Off Mod": "off-mod"}
        state = switches.read(self.root, ANNO_117, ids=ids, names=list(ids))
        written = switches.write(state, {"On Mod": False, "Off Mod": True}, ids=ids)
        self.assertEqual(written.listed, 2)
        after = state.profile.read_text(encoding="utf-8")
        self.assertIn("# on-mod", after)
        self.assertIn("\noff-mod", after)

    def test_and_keeps_the_comments_the_ordering_and_the_name(self):
        """A profile is a file the user owns.  Regenerating it would throw away
        their comments and their display name, and -- worse -- drop every mod
        living in one of the other folders 117 loads from."""
        _folder(self.root, "On Mod")
        self.profile()
        ids = {"On Mod": "on-mod"}
        state = switches.read(self.root, ANNO_117, ids=ids, names=list(ids))
        switches.write(state, {"On Mod": False}, ids=ids)
        after = state.profile.read_text(encoding="utf-8").splitlines()
        self.assertEqual(after[0], self.PROFILE.splitlines()[0])
        self.assertIn("DisplayName default", after)
        self.assertIn("EnableNewMods", after)
        self.assertIn("somebody-elses-mod", after)

    def test_the_games_own_note_after_an_id_is_not_part_of_the_id(self):
        """117 writes back into the live profile: a listed mod it could not
        find gets "# not installed" on the same line.  Read whole, that line
        was an id nobody had; the id is the first word, and the note stays
        with it when the line is rewritten -- it is the game's to take back."""
        _folder(self.root, "Gone")
        _folder(self.root, "Other")
        self.profile(
            "# EnableNewMods\nDisplayName default\nmine\n"
            "gone-mod # not installed\n# other-mod  # not installed\n"
        )
        state = switches.read(self.root, ANNO_117, ids={"Gone": "gone-mod", "Other": "other-mod"}, names=["Gone", "Other"])
        profile = switches.read_profile(state.profile)
        self.assertEqual(profile.on, {"mine": True, "gone-mod": True, "other-mod": False})
        self.assertEqual(profile.notes, {"gone-mod": "not installed", "other-mod": "not installed"})
        self.assertEqual(state.on, {"Gone": True, "Other": False})
        switches.write(state, {"Gone": False, "Other": True}, ids={"Gone": "gone-mod", "Other": "other-mod"})
        after = state.profile.read_text(encoding="utf-8")
        self.assertIn("\n# gone-mod  # not installed\n", after)
        self.assertIn("\nother-mod  # not installed\n", after)

    def test_a_mod_with_no_id_cannot_be_named_in_a_profile(self):
        _folder(self.root, "Nameless")
        self.profile()
        state = switches.read(self.root, ANNO_117, ids={"Nameless": ""}, names=["Nameless"])
        written = switches.write(state, {"Nameless": False}, ids={"Nameless": ""})
        self.assertEqual(written.listed, 0)
        self.assertIn("ModID", written.failed[0][1])

    def test_two_mods_of_one_modid_wanted_apart_are_not_switched_together(self):
        """One profile line switches every mod declaring its ModID -- a
        mod.io cache copy and a local copy of one mod, say.  Asked for one
        on and one off, the line went the way of the mod that moved, the
        other mod with it, and nothing said so (A11-24)."""
        _folder(self.root, "A Copy", "B Copy")
        self.profile("EnableNewMods\nDisplayName default\n\na-mod\n")
        ids = {"A Copy": "a-mod", "B Copy": "A-Mod"}
        state = switches.read(self.root, ANNO_117, ids=ids, names=list(ids))
        self.assertEqual(state.on, {"A Copy": True, "B Copy": True})
        before = state.profile.read_text(encoding="utf-8")
        written = switches.write(state, {"A Copy": True, "B Copy": False}, ids=ids)
        self.assertEqual(written.listed, 0)
        self.assertEqual([mod for mod, _why in written.failed], ["B Copy"])
        self.assertIn("A Copy", written.failed[0][1])
        self.assertEqual(state.profile.read_text(encoding="utf-8"), before)
        # Wanted the same way, the one line is both switched.
        written = switches.write(state, {"A Copy": False, "B Copy": False}, ids=ids)
        self.assertEqual((written.listed, written.failed), (1, []))
        self.assertIn("\n# a-mod\n", state.profile.read_text(encoding="utf-8"))

    def test_with_no_profile_at_all_every_mod_is_on(self):
        _folder(self.root, "On Mod")
        with mock.patch.object(switches, "documents_mods", return_value=None):
            state = switches.read(self.root, ANNO_117, ids={"On Mod": "on-mod"})
        self.assertTrue(state.is_on("On Mod"))
        self.assertIsNone(state.profile)


# The D1 probe's mods by folder, each with the ModID its profile line names.
PROBE_IDS = {
    "A Host": "a-host", "A Host/a": "a-bundled",
    "B Host": "b-host", "B Host/b": "b-bundled",
    "C Host": "c-host", "C Host/c": "c-bundled",
    "D": "d-top",
}


class TestABundledModOn117GoesWithItsHost(FolderCase):
    """117 does not read a profile line for a mod bundled inside another: it
    is on exactly when its host is.

    Measured in the game on 16 Oct 2026 (D1): three hosts, each with a mod
    bundled inside, under a frozen profile -- ``# EnableNewMods`` -- listing
    the three hosts on, bundled ``c`` on, bundled ``b`` off (``#
    bundledprobe-b-bundled``) and bundled ``a`` not at all, beside a
    top-level mod it did not list.  Every bundled mod loaded, and the
    unlisted top-level one stayed off.  The loader's log::

        Enabled mod: .../[Test] BundledProbe A Host  (bundledprobe-a-host@1.0)
        Enabled mod: .../[Test] BundledProbe B Host  (bundledprobe-b-host@1.0)
        Enabled mod: .../[Test] BundledProbe A Host/Bundled a [SubMod] (bundledprobe-a-bundled@1.0)
        Enabled mod: .../[Test] BundledProbe B Host/Bundled b [SubMod] (bundledprobe-b-bundled@1.0)
        Enabled mod: .../[Test] BundledProbe C Host/Bundled c [SubMod] (bundledprobe-c-bundled@1.0)
        Enabled mod: .../[Test] BundledProbe C Host  (bundledprobe-c-host@1.0)
        Enabled mods: 3 (plus 3 nested)
        -- Load bundledprobe-b-bundled@1.0 (from bundledprobe-b-host)
    """

    FROZEN = "# EnableNewMods\nDisplayName probe\na-host\nb-host\nc-host\nc-bundled\n# b-bundled\n"

    profile = TestTheProfileAnno117Keeps.profile

    def setUp(self):
        super().setUp()
        _folder(self.root, "A Host/a", "B Host/b", "C Host/c", "D")

    def read(self, text):
        self.profile(text)
        return switches.read(self.root, ANNO_117, ids=PROBE_IDS, names=list(PROBE_IDS))

    def test_listed_off_or_not_listed_it_loads_with_its_host(self):
        state = self.read(self.FROZEN)
        self.assertEqual(state.on, {
            "A Host": True, "A Host/a": True,
            "B Host": True, "B Host/b": True,
            "C Host": True, "C Host/c": True,
            "D": False,
        })

    def test_and_listed_on_it_stays_off_with_its_host(self):
        state = self.read(self.FROZEN.replace("a-host", "# a-host").replace("\nc-host", "\n# c-host"))
        self.assertFalse(state.is_on("A Host/a"))
        self.assertFalse(state.is_on("C Host/c"))
        self.assertTrue(state.is_on("B Host/b"))

    def test_a_host_unlisted_takes_enablenewmods_and_its_bundled_mod_with_it(self):
        text = "EnableNewMods\nDisplayName probe\n# a-bundled\n"
        state = self.read(text)
        self.assertTrue(state.is_on("A Host/a"))
        state = self.read(text.replace("EnableNewMods", "# EnableNewMods").replace("# a-bundled", "a-bundled"))
        self.assertFalse(state.is_on("A Host/a"))

    def test_a_modio_mod_below_its_numbered_folder_is_a_host_not_bundled(self):
        """The mod.io cache's numbered folder is where a mod was downloaded
        to, not a mod: the one inside is top-level, with a line of its own,
        and what is bundled in it goes with that line."""
        _folder(self.root, "123456/Host/inner")
        self.profile("# EnableNewMods\nhost-id\n# inner-id\n")
        ids = {"123456/Host": "host-id", "123456/Host/inner": "inner-id"}
        state = switches.read(self.root, ANNO_117, ids=ids, names=list(ids))
        self.assertEqual(state.on, {"123456/Host": True, "123456/Host/inner": True})

    def test_switching_the_host_is_switching_what_it_bundles(self):
        """So asked for both at once, only the host's line is written, and
        nothing is reported as a switch that could not be made."""
        state = self.read(self.FROZEN)
        written = switches.write(state, {"C Host": False, "C Host/c": False}, ids=PROBE_IDS)
        self.assertEqual((written.failed, written.listed), ([], 1))
        after = switches.read(self.root, ANNO_117, ids=PROBE_IDS, names=list(PROBE_IDS))
        self.assertFalse(after.is_on("C Host/c"))
        lines = state.profile.read_text(encoding="utf-8").splitlines()
        self.assertIn("# c-host", lines)
        self.assertIn("c-bundled", lines)

    def test_but_asked_for_against_its_host_it_cannot_be(self):
        state = self.read(self.FROZEN)
        written = switches.write(state, {"A Host/a": False}, ids=PROBE_IDS)
        self.assertEqual([mod for mod, _why in written.failed], ["A Host/a"])
        self.assertEqual(written.listed, 0)


class TestWhatWasWrittenAddsUp(unittest.TestCase):
    """Two passes of switching, told as one: every field of the second is
    kept, where callers once copied each a different few by hand."""

    def test_merging_keeps_every_field(self):
        first = switches.Written(renamed=[("a", Path("a"))], failed=[("x", "why")])
        second = switches.Written(
            renamed=[("b", Path("b"))],
            linked=[("c", Path("c"))],
            profile=Path("profile.txt"),
            listed=2,
            failed=[("y", "why")],
            unlisted=[("d", Path("activation.json"))],
        )
        first.merge(second)
        self.assertEqual(first.renamed, [("a", Path("a")), ("b", Path("b"))])
        self.assertEqual(first.linked, [("c", Path("c"))])
        self.assertEqual(first.profile, Path("profile.txt"))
        self.assertEqual(first.listed, 2)
        self.assertEqual(first.failed, [("x", "why"), ("y", "why")])
        self.assertEqual(first.unlisted, [("d", Path("activation.json"))])

    def test_a_profile_already_written_stays(self):
        first = switches.Written(profile=Path("one.txt"))
        first.merge(switches.Written(profile=Path("two.txt")))
        self.assertEqual(first.profile, Path("one.txt"))


class TestInWhatOrderTheyRun(unittest.TestCase):
    """Load order, which decides which of two mods the game actually obeys.

    Not the same question for the two games, and for Anno 1800 not one that can
    be answered from the outside at all -- so the loader's own log wins wherever
    it speaks.  See :func:`core.loaderlog.logged_order`.
    """

    def test_anno_1800_falls_back_to_folder_name(self):
        mods = {"Zeta": {}, "Alpha": {}}
        self.assertEqual(switches.order(ANNO_1800, mods), ["Alpha", "Zeta"])

    def test_anno_117_sorts_by_category_and_name_from_the_modinfo(self):
        """Read off the loader's own log, which this reproduces exactly for all
        sixteen mods it had enabled.  The folder name has no bearing on it."""
        mods = {
            "zzz folder": {"category": "Cheat", "mod_name": "Keybinds"},
            "aaa folder": {"category": "Map", "mod_name": "Clover"},
        }
        self.assertEqual(switches.order(ANNO_117, mods), ["zzz folder", "aaa folder"])

    def test_and_compares_case_sensitively(self):
        """Which is what puts "Powerful Flagship" in front of "[Seal] Shrine
        Rework": "P" is below "[" in code points, and "p" is above it."""
        mods = {
            "a": {"category": "Gameplay", "mod_name": "Powerful Flagship"},
            "b": {"category": "Gameplay", "mod_name": "[Seal] Shrine Rework"},
        }
        self.assertEqual(switches.order(ANNO_117, mods), ["a", "b"])

    def test_a_mod_with_no_category_is_sorted_as_Mod(self):
        mods = {
            "a": {"mod_name": "Efficient Charcoal"},
            "b": {"category": "Misc", "mod_name": "Intro removed"},
        }
        self.assertEqual(switches.order(ANNO_117, mods), ["b", "a"])

    def test_a_declared_load_after_beats_the_sort(self):
        """Four mods in a real folder declare a mod they must follow that plain
        folder order puts later, so they would patch a document that does not
        have the thing they are patching yet."""
        mods = {
            "Aaa": {"mod_id": "aaa", "load_after": ["zzz"]},
            "Zzz": {"mod_id": "zzz"},
        }
        self.assertEqual(switches.order(ANNO_1800, mods), ["Zzz", "Aaa"])

    def test_requiring_a_mod_counts_as_loading_after_it(self):
        """The point of requiring one is to patch what it leaves behind."""
        mods = {"Aaa": {"mod_id": "aaa", "load_after": ["zzz"]}, "Zzz": {"mod_id": "zzz"}}
        self.assertEqual(switches.order(ANNO_117, mods)[-1], "Aaa")

    def test_a_star_means_after_everything(self):
        mods = {
            "Aaa": {"mod_id": "aaa", "load_after": ["*"]},
            "Mmm": {"mod_id": "mmm"},
            "Zzz": {"mod_id": "zzz"},
        }
        self.assertEqual(switches.order(ANNO_1800, mods)[-1], "Aaa")

    def test_a_mod_that_declares_something_goes_first_with_what_it_needs(self):
        """The loader's ``DependencyGraph::Sort``: every mod with a load-after
        list is walked first, what it needs straight before it; the rest follow.
        The Studio used to leave ``Bbb`` in front, at its base place."""
        mods = {
            "Bbb": {"mod_id": "bbb"},
            "Ccc": {"mod_id": "ccc", "load_after": ["ddd"]},
            "Ddd": {"mod_id": "ddd"},
        }
        self.assertEqual(switches.order(ANNO_1800, mods), ["Ddd", "Ccc", "Bbb"])

    def test_even_when_what_it_names_is_not_there(self):
        """The loader tests the list, not what it finds in it."""
        mods = {"Aaa": {"mod_id": "aaa"}, "Zzz": {"mod_id": "zzz", "load_after": ["missing"]}}
        self.assertEqual(switches.order(ANNO_1800, mods), ["Zzz", "Aaa"])

    def test_1800_compares_folder_names_as_written(self):
        """Capitals, then ``[``, then lower case -- and a bundled mod by its own
        folder, not its host's. Lower-cased, the machine's folder agreed with
        the loader's log for 106 of 131 mods; as written, all 131."""
        mods = {
            "[Addon] Extended Arctic/JDG_Quay": {"mod_id": "jdg_quay"},
            "[Addon] Extended Arctic": {"mod_id": "arctic"},
            "AI Everyone Hard": {"mod_id": "ai"},
            "data": {"mod_id": "data"},
        }
        self.assertEqual(
            switches.order(ANNO_1800, mods),
            ["AI Everyone Hard", "[Addon] Extended Arctic/JDG_Quay", "[Addon] Extended Arctic", "data"],
        )

    def test_1800_knows_a_mod_without_a_modid_by_its_folder(self):
        mods = {"Aaa": {"mod_id": "aaa", "load_after": ["Old Style Mod"]}, "Old Style Mod": {}}
        self.assertEqual(switches.order(ANNO_1800, mods), ["Old Style Mod", "Aaa"])

    def test_late_mods_still_order_among_themselves(self):
        """The docs' example: ``["*", "post-a"]`` loads after post-a, both late.
        The Studio used to drop everything after the star."""
        mods = {
            "Aaa Post B": {"mod_id": "post-b", "load_after": ["*", "post-a"]},
            "Mmm": {"mod_id": "mmm"},
            "Zzz Post A": {"mod_id": "post-a", "load_after": ["*"]},
        }
        self.assertEqual(switches.order(ANNO_1800, mods), ["Mmm", "Zzz Post A", "Aaa Post B"])

    def test_a_normal_mod_cannot_load_after_a_late_one(self):
        """"A mod without a * cannot load after a mod with a *" -- the loader
        sorts the two phases apart, so the entry does nothing."""
        mods = {
            "Aaa": {"mod_id": "aaa", "load_after": ["late"]},
            "Late": {"mod_id": "late", "load_after": ["*"]},
            "Zzz": {"mod_id": "zzz"},
        }
        self.assertEqual(switches.order(ANNO_1800, mods), ["Aaa", "Zzz", "Late"])

    def test_last_of_all_is_the_star_and_every_late_mod(self):
        """The star alone leaves a mod in front of a late one whose ModID
        sorts after its own; naming the late mods puts it behind them too."""
        for game in (ANNO_1800, ANNO_117):
            mods = {
                "[Gameplay] Efficient Charcoal": {"mod_id": "efficient-charcoal"},
                "[Map] Seed Search": {"mod_id": "seed-search", "load_after": ["*"]},
                "[Misc] Zzz Late": {"mod_id": "zzz-late", "load_after": ["*", "seed-search"]},
            }
            after = switches.last_of_all(game, mods)
            self.assertEqual(after, ["*", "seed-search", "zzz-late"])
            mods["[Gameplay] Anno mod list patch"] = {
                "mod_id": "anno-mod-list-patch", "category": "Gameplay",
                "mod_name": "Anno mod list patch", "load_after": after,
            }
            self.assertEqual(
                switches.order(game, mods)[-1], "[Gameplay] Anno mod list patch", game
            )
            # Which the star alone does not do.
            mods["[Gameplay] Anno mod list patch"]["load_after"] = ["*"]
            self.assertNotEqual(
                switches.order(game, mods)[-1], "[Gameplay] Anno mod list patch", game
            )

    def test_last_of_all_knows_an_1800_mod_without_a_modid_by_its_folder(self):
        mods = {"Old Late Mod": {"load_after": ["*"]}, "Plain": {"mod_id": "plain"}}
        self.assertEqual(switches.last_of_all(ANNO_1800, mods), ["*", "Old Late Mod"])
        # Anno 117's loader has no name for it, so there is nothing to say.
        self.assertEqual(switches.last_of_all(ANNO_117, mods), ["*"])

    def test_mods_that_need_each_other_do_not_hang(self):
        mods = {
            "Aaa": {"mod_id": "aaa", "load_after": ["bbb"]},
            "Bbb": {"mod_id": "bbb", "load_after": ["aaa"]},
        }
        self.assertEqual(sorted(switches.order(ANNO_1800, mods)), ["Aaa", "Bbb"])

    # -- the log ----------------------------------------------------------
    def test_what_the_loader_recorded_wins_over_any_rule(self):
        """It is what happened, rather than what should have happened."""
        mods = {"Aaa": {}, "Zzz": {}}
        self.assertEqual(
            switches.order(ANNO_1800, mods, logged=["Zzz", "Aaa"]), ["Zzz", "Aaa"]
        )

    def test_a_mod_the_log_does_not_name_is_slotted_in_by_the_rule(self):
        """Installed since the game last ran, or switched off when it did."""
        mods = {"Aaa": {}, "Mmm": {}, "Zzz": {}}
        self.assertEqual(
            switches.order(ANNO_1800, mods, logged=["Zzz", "Aaa"]),
            ["Zzz", "Aaa", "Mmm"],
        )

    def test_and_one_that_sorts_before_everything_logged_goes_first(self):
        mods = {"Aaa": {}, "Mmm": {}, "Zzz": {}}
        self.assertEqual(
            switches.order(ANNO_1800, mods, logged=["Mmm", "Zzz"]),
            ["Aaa", "Mmm", "Zzz"],
        )


class TestWhereTheGameLooks(unittest.TestCase):
    """Every folder the game loads from, the mod.io cache among them."""

    def test_both_games_read_the_modio_cache_when_it_exists(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp) / "cache" / "mods"
            cache.mkdir(parents=True)
            with mock.patch.object(switches, "documents_mods", return_value=None), \
                    mock.patch.object(switches.modio_cache, "mods_dir", return_value=cache):
                for game in (ANNO_1800, ANNO_117):
                    with self.subTest(game=game):
                        self.assertIn(cache, switches.mod_paths(game, Path(tmp)))

    def test_and_not_when_it_does_not(self):
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.object(switches, "documents_mods", return_value=None), \
                mock.patch.object(switches.modio_cache, "mods_dir", return_value=None):
            self.assertEqual(switches.mod_paths(ANNO_1800, Path(tmp)), [Path(tmp)])


if __name__ == "__main__":
    unittest.main()
