"""The game's own loader, from an installed Anno Modding Tools extension, as a
second opinion on what the Studio says a mod does -- and the drift probes.

Most of this runs anywhere. The classes that need the real loader skip
themselves when the extension is not installed, as the census tests do."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import _isolation  # noqa: F401 -- first: keeps the suite out of real files under any runner

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from support import (  # noqa: E402
    LOADER,
    PlayingWindow,
    SchemaTestBase,
    no_credentials,
    requires,
    wait_until,
)

from anno_modding_studio.core import games, loadercheck, xmltest  # noqa: E402
from anno_modding_studio.core.changes import (  # noqa: E402
    OP_REPLACE,
    Change,
    Loadout,
    ModLayer,
    own_record,
)


def _radius(guid, value, origin=""):
    return Change(guid=guid, asset_label="x", path="/Values/Building/Radius", tag="Radius",
                  op=OP_REPLACE, old_value="", new_value=value, origin=origin)


class TestFindingIt(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

    def install(self, folder, version):
        external = folder / ("jakobharder.anno-modding-tools-" + version) / "external"
        external.mkdir(parents=True)
        for build in (game.xmltest_build for game in games.GAMES.values()):
            (external / build).write_bytes(b"")
        return external.parent

    def test_every_editor_s_folder_and_one_named_by_hand(self):
        with mock.patch.object(Path, "home", return_value=self.root):
            folders = xmltest.extension_folders(self.root / "elsewhere")
        self.assertEqual([str(f.relative_to(self.root)).replace("\\", "/") for f in folders], [
            "elsewhere", ".vscode/extensions", ".vscode-insiders/extensions",
            ".vscode-oss/extensions", ".cursor/extensions",
        ])

    def test_the_newest_anywhere_wins_and_the_extension_itself_can_be_named(self):
        self.install(self.root / ".vscode" / "extensions", "2.3.1")
        self.install(self.root / ".cursor" / "extensions", "2.4.0")
        own = self.install(self.root / "mine", "2.10.2")
        with mock.patch.object(Path, "home", return_value=self.root), \
                mock.patch.object(xmltest, "banner", return_value="modloader GU17.1"):
            found = xmltest.find(games.ANNO_117)
            named = xmltest.find(games.ANNO_117, extra=own)
        self.assertEqual((found.version, found.exe.name), ("2.4.0", "xmltest3.exe"))
        self.assertEqual(named.version, "2.10.2")
        self.assertEqual(named.said, "modloader GU17.1 from Anno Modding Tools 2.10.2")

    def test_none_is_none(self):
        with mock.patch.object(Path, "home", return_value=self.root):
            self.assertIsNone(xmltest.find(games.ANNO_1800))


class TestWhatAModReads(unittest.TestCase):
    def test_a_changed_file_is_read_again_and_held_once(self):
        """Held per file by its size and time -- one entry a file, replaced
        when it changes, where every version of it used to stay."""
        import os

        folder = Path(tempfile.mkdtemp())
        patch = folder / "data" / "config" / "export" / "main" / "asset" / "assets.xml"
        patch.parent.mkdir(parents=True)
        patch.write_text('<ModOps><ModOp Content="@100586/Values" /></ModOps>', encoding="utf-8")
        self.assertEqual(loadercheck._reads_in(str(folder)), {"100586"})
        patch.write_text('<ModOps><ModOp Content="@100587/Values" /></ModOps>', encoding="utf-8")
        stat = patch.stat()
        os.utime(patch, (stat.st_atime, stat.st_mtime + 60))
        self.assertEqual(loadercheck._reads_in(str(folder)), {"100587"})
        self.assertEqual(sum(1 for key in loadercheck._READ_CACHE if key == str(patch)), 1)


class TestWhatItStandsOn(unittest.TestCase):
    def test_the_mods_ahead_that_touch_its_assets_or_that_it_names(self):
        loadout = Loadout(game=games.ANNO_1800)
        for order, origin in enumerate(("Shares", "Named", "Elsewhere", "Off", "After"), 1):
            loadout.set_layer(ModLayer(origin=origin, mod_id=origin.lower(), order=order,
                                       active=origin != "Off"))
        loadout.layer("After").load_after = ["named"]
        for change in (_radius("1", "5", "Shares"), _radius("2", "5", "Elsewhere"),
                       _radius("1", "6", "Off"), _radius("1", "7", "After")):
            loadout.set_change(change)
        self.assertEqual(loadercheck._stack(loadout, "After", None), ["Shares", "Named", "After"])


class TestWhereYourOwnModStandsInTheCheck(unittest.TestCase):
    """The Studio's side of the check is a narrowed copy of the loadout, and
    the copy has to place the loadout's own mod where the loadout does."""

    def loadout(self, own_mod_loads_last):
        loadout = Loadout(game=games.ANNO_117)
        loadout.own.loads_last = own_mod_loads_last
        loadout.own.name, loadout.own.category = "Anno 117 mod list patch", "Gameplay"
        # Sorts after "[Gameplay] Anno 117 mod list patch" by category and name.
        loadout.set_layer(ModLayer(origin="Efficient Charcoal", name="Efficient Charcoal",
                                   category="Mod", mod_id="efficient", order=1))
        loadout.set_change(_radius("1", "5", "Efficient Charcoal"))
        loadout.set_change(_radius("1", "6"))
        return loadout

    def test_a_loadout_s_own_mod_is_last_in_the_copy_too(self):
        loadout = self.loadout(own_mod_loads_last=True)
        view = loadercheck._only(loadout, loadercheck._stack(loadout, "", None))
        self.assertEqual(loadercheck._stack(loadout, "", None), ["Efficient Charcoal", ""])
        self.assertEqual(view.layer_rank(""), loadout.layer_rank(""))
        self.assertGreater(view.layer_rank(""), view.layer_rank("Efficient Charcoal"))

    def test_a_mod_written_to_publish_is_where_its_name_sorts_in_both(self):
        loadout = self.loadout(own_mod_loads_last=False)
        view = loadercheck._only(loadout, ["Efficient Charcoal", ""])
        self.assertEqual(view.layer_rank(""), loadout.layer_rank(""))
        self.assertLess(view.layer_rank(""), view.layer_rank("Efficient Charcoal"))


class TestWhatItStandsOnAlsoReads(unittest.TestCase):
    """*Item Sockets* copies each building's socket count from the town hall's,
    which another mod ahead of it raises. Standing only on the mods that
    change the same buildings, the loader copied the shipped count."""

    def test_a_mod_that_changes_what_it_copies_from(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / "Sockets"
            folder.mkdir()
            (folder / "assets.xml").write_text(
                '<ModOps><ModOp Type="merge" GUID="2" Path="/Values/ItemContainer/SocketCount" '
                'Content="@100415/ItemContainer/SocketCount"/></ModOps>', encoding="utf-8")
            loadout = Loadout(game=games.ANNO_1800)
            loadout.set_layer(ModLayer(origin="Raises", order=1))
            loadout.set_layer(ModLayer(origin="Unrelated", order=2))
            loadout.set_layer(ModLayer(origin="Sockets", order=3, folder=str(folder)))
            for change in (_radius("100415", "4", "Raises"), _radius("777", "4", "Unrelated"),
                           _radius("2", "4", "Sockets")):
                loadout.set_change(change)
            self.assertEqual(loadercheck._stack(loadout, "Sockets", None), ["Raises", "Sockets"])


class TestWhatAModPulledInReads(unittest.TestCase):
    """Of a mod the check stands on, only what its ops on the assets wanted
    read: every GUID in its folder took a loadout's one-change mod onto 311
    mods (H2-15)."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.loadout = Loadout(game=games.ANNO_1800)
        self.order = 0

    PATCH = games.get(games.ANNO_1800).asset_relpath

    def mod(self, origin, touches, files=None):
        """A mod that changes ``touches`` and keeps ``files`` (name -> text)."""
        folder = None
        if files is not None:
            folder = self.root / origin
            folder.mkdir()
            for name, text in files.items():
                # The patch of the assets where it is only named.
                where = folder / (self.PATCH if name == "assets.xml" else name)
                where.parent.mkdir(parents=True, exist_ok=True)
                where.write_text(text, encoding="utf-8")
        self.order += 1
        self.loadout.set_layer(ModLayer(origin=origin, order=self.order,
                                        folder=str(folder) if folder else None))
        for guid in touches:
            self.loadout.set_change(_radius(guid, "4", origin))

    def stack(self, origin="Mine"):
        return loadercheck._stack(self.loadout, origin, None)

    def test_not_what_its_ops_on_other_assets_read(self):
        self.mod("Copied", ["555"])
        self.mod("Elsewhere", ["666"])
        self.mod("Between", ["2", "9"], {"assets.xml":
            '<ModOps><ModOp Type="merge" GUID="2" Path="/Values" Content="@555/Values"/>'
            '<ModOp Type="merge" GUID="9" Path="/Values" Content="@666/Values"/></ModOps>'})
        self.mod("Mine", ["2"], {})
        self.assertEqual(self.stack(), ["Copied", "Between", "Mine"])

    def test_a_condition_on_the_group_around_the_op(self):
        self.mod("Asked", ["555"])
        self.mod("Between", ["2"], {"assets.xml":
            '<ModOps><Group Condition="@555/Values/Building"><ModOp Type="replace" GUID="2" '
            'Path="/Values/Building/Radius"><Radius>4</Radius></ModOp></Group></ModOps>'})
        self.mod("Mine", ["2"], {})
        self.assertEqual(self.stack(), ["Asked", "Between", "Mine"])

    def test_an_included_file_under_a_condition(self):
        self.mod("Asked", ["555"])
        self.mod("Copied", ["777"])
        self.mod("Between", ["2"], {
            "data/config/export/main/asset/assets.xml":
                '<ModOps><Group Condition="@555/Values"><Include File="ops.include.xml"/></Group></ModOps>',
            "data/config/export/main/asset/ops.include.xml":
                '<ModOps><ModOp Type="merge" GUID="2" Path="/Values" Content="@777/Values"/></ModOps>',
        })
        self.mod("Mine", ["2"], {})
        self.assertEqual(self.stack(), ["Asked", "Copied", "Between", "Mine"])

    def test_what_a_search_reads_whatever_it_finds(self):
        self.mod("Copied", ["555"])
        self.mod("Between", ["2"], {"assets.xml":
            '<ModOps><ModOp Type="merge" Path="//Asset[Template=\'House\']/Values" '
            'Content="@555/Values"/></ModOps>'})
        self.mod("Mine", ["2"], {})
        self.assertEqual(self.stack(), ["Copied", "Between", "Mine"])

    def test_an_op_on_an_asset_only_wanted_later(self):
        """``Between`` is in for asset 2, which copies from 900; its own op on
        900 copies from 555, which only then is wanted."""
        self.mod("Deepest", ["555"])
        self.mod("Between", ["2", "900"], {"assets.xml":
            '<ModOps><ModOp Type="merge" GUID="900" Path="/Values" Content="@555/Values"/>'
            '<ModOp Type="merge" GUID="2" Path="/Values" Content="@900/Values"/></ModOps>'})
        self.mod("Mine", ["2"], {})
        self.assertEqual(self.stack(), ["Deepest", "Between", "Mine"])

    def test_an_asset_it_adds_by_its_own_guid(self):
        self.mod("Copied", ["555"])
        self.mod("Elsewhere", ["666"])
        self.mod("Adds", ["8"], {"assets.xml":
            '<ModOps><ModOp Type="addNextSibling" GUID="1000"><Asset><Values><Standard>'
            '<GUID>8</GUID></Standard><Building><Radius><ModValue Insert="@555/Values/Building/Radius"/>'
            '</Radius></Building></Values></Asset></ModOp>'
            '<ModOp Type="merge" GUID="9" Path="/Values" Content="@666/Values"/></ModOps>'})
        self.mod("Mine", ["8"], {})
        self.assertEqual(self.stack(), ["Copied", "Adds", "Mine"])

    def aimed(self, text, game):
        from lxml import etree

        ops, _includes = loadercheck._aimed_ops(etree.fromstring(text), game)
        return [aimed for aimed, _read, _finds in ops]

    def test_the_patch_read_as_the_studio_reads_it(self):
        """As :mod:`modops` reads the structure (A11-51): a group with any
        Skip is switched off, a bare Asset adds one in Anno 117 alone, and a
        MaxRepeat group with no game to say how it runs is carried whole."""
        op = '<ModOp Type="merge" GUID="5" Path="/Values" Content="@555/Values"/>'
        asset = "<Asset><Values><Standard><GUID>9</GUID></Standard></Values></Asset>"
        self.assertEqual(self.aimed('<ModOps><Group Skip="0">%s</Group></ModOps>' % op, games.ANNO_1800), [])
        self.assertEqual(self.aimed("<ModOps>%s</ModOps>" % asset, games.ANNO_117), [frozenset({"9"})])
        self.assertEqual(self.aimed("<ModOps>%s</ModOps>" % asset, games.ANNO_1800), [None])
        self.assertEqual(self.aimed('<ModOps><Group MaxRepeat="3">%s</Group></ModOps>' % op, games.ANNO_117),
                         [frozenset({"5"})])
        self.assertEqual(self.aimed('<ModOps><Group MaxRepeat="3">%s</Group></ModOps>' % op, ""), [None])

    def test_a_commented_out_op_reads_nothing(self):
        self.mod("Copied", ["555"])
        self.mod("Between", ["2"], {"assets.xml":
            '<ModOps><!-- <ModOp Type="merge" GUID="2" Path="/Values" Content="@555/Values"/> -->'
            '<ModOp Type="replace" GUID="2" Path="/Values/Building/Radius"><Radius>4</Radius></ModOp>'
            '</ModOps>'})
        self.mod("Mine", ["2"], {})
        self.assertEqual(self.stack(), ["Between", "Mine"])

    def test_a_file_that_is_not_a_patch_reads_all_it_names(self):
        self.mod("Copied", ["555"])
        self.mod("Between", ["2"], {"assets.xml": '<Other>@555</Other>'})
        self.mod("Mine", ["2"], {})
        self.assertEqual(self.stack(), ["Copied", "Between", "Mine"])

    class _Index:
        """Answers each search from ``found``, as the index sweeps the
        shipped file; ``asked`` keeps what it was asked."""

        def __init__(self, found):
            self.found, self.asked = found, []

        def find_matching(self, paths, progress=None, should_cancel=None):
            self.asked.extend(paths)
            return {path: self.found.get(path, []) for path in paths}

    def test_what_its_own_search_finds_in_the_shipped_file(self):
        """*Revival of the Low Tier Goods* replaces every shipped description
        of one kind; *Orchard Rework*, ahead of it, gives seven orchards
        another, so the search passes them by in the game. Without it under
        the check, the loader found them and changed all seven."""
        path = "//Standard[InfoDescription='132783']/InfoDescription"
        self.mod("Orchards", ["133004"])
        self.mod("Mine", ["900"], {"assets.xml":
            '<ModOps><ModOp Type="replace" Path="%s"><InfoDescription>1500300044</InfoDescription>'
            '</ModOp></ModOps>' % path.replace("'", "&apos;")})
        index = self._Index({path: ["133004"]})
        self.assertEqual(loadercheck._stack(self.loadout, "Mine", index), ["Orchards", "Mine"])
        self.assertEqual(index.asked, [path])
        self.assertEqual(self.stack(), ["Mine"])

    def test_what_a_copy_by_search_reads(self):
        path = "//Asset[Values/Standard/Name='Town Hall']/Values/ItemContainer"
        self.mod("Copied", ["100415"])
        self.mod("Between", ["2"], {"assets.xml":
            '<ModOps><ModOp Type="merge" GUID="2" Path="/Values" Content="%s/text()"/></ModOps>'
            % path.replace("'", "&apos;")})
        self.mod("Mine", ["2"], {})
        index = self._Index({path: ["100415"]})
        self.assertEqual(loadercheck._stack(self.loadout, "Mine", index), ["Copied", "Between", "Mine"])

    def test_not_its_patches_of_other_files(self):
        """A mod's texts and infotips are patched by files of their own,
        which the loader does not apply to the assets."""
        self.mod("Copied", ["555"])
        self.mod("Between", ["2"], {
            "assets.xml": '<ModOps><ModOp Type="replace" GUID="2" Path="/Values/Building/Radius">'
                          '<Radius>4</Radius></ModOp></ModOps>',
            "data/config/gui/texts_english.xml":
                '<ModOps><ModOp Type="add" Path="/TextExport/Texts" Content="@555/Values"/></ModOps>',
            "data/infotips/export.xml":
                '<ModOps><ModOp Type="merge" Path="//InfoTipData[Guid=\'555\']" Content="@555/Values"/></ModOps>',
        })
        self.mod("Mine", ["2"], {})
        index = self._Index({})
        self.assertEqual(loadercheck._stack(self.loadout, "Mine", index), ["Between", "Mine"])
        self.assertEqual(index.asked, [])

    def test_every_file_where_an_included_one_does_not_read(self):
        self.mod("Copied", ["555"])
        self.mod("Between", ["2"], {
            "assets.xml": '<ModOps><Include File="broken.xml"/></ModOps>',
            "data/config/export/main/asset/broken.xml": "<<< not XML at all",
            "data/elsewhere.xml": '<ModOps><ModOp Type="merge" GUID="2" Path="/Values" '
                                  'Content="@555/Values"/></ModOps>',
        })
        self.mod("Mine", ["2"], {})
        self.assertEqual(self.stack(), ["Copied", "Between", "Mine"])

    def test_the_checked_mod_still_reads_its_whole_folder(self):
        self.mod("Copied", ["555"])
        self.mod("Mine", ["2"], {"assets.xml":
            '<ModOps><ModOp Type="merge" GUID="9" Path="/Values" Content="@555/Values"/></ModOps>'})
        self.assertEqual(self.stack(), ["Copied", "Mine"])


class TestWhichModsArePresent(unittest.TestCase):
    """xmltest hands the loader no mods at all, so every #ModID is false in
    it; the check writes in what the Studio's reading answered."""

    def test_anno_1800_as_one_modid_with_a_condition_that_finds_a_node(self):
        text, count = xmltest.substitute_mods(
            '<Group Condition="#NewWorldDocklands"><ModOp Condition="!#Absent Mod"/>'
            '<ModOp Condition="!#newworlddocklands"/><ModOp Condition="@1000/Building"/></Group>',
            {"newworlddocklands"}, games.ANNO_1800)
        self.assertEqual(count, 3)
        self.assertEqual(text, '<Group Condition="//AssetList"><ModOp Condition="//AssetList"/>'
                               '<ModOp Condition="!//AssetList"/><ModOp Condition="@1000/Building"/></Group>')

    def test_anno_117_each_name_in_an_expression(self):
        text, _ = xmltest.substitute_mods('<Group Condition="!#a-mod or not(#b)">', {"a-mod"}, games.ANNO_117)
        self.assertEqual(text, '<Group Condition="!true() or not(false())">')


class TestStoppingARun(unittest.TestCase):
    """A check the loadout has moved on from kills the build it is waiting
    on, rather than holding the loader's work folder until it ends."""

    SLEEPS = (sys.executable, "-c", "import time; time.sleep(30)")

    def test_the_build_is_killed_when_asked(self):
        asked = []

        def stop():
            asked.append(1)
            return len(asked) > 2

        import time
        started = time.monotonic()
        with self.assertRaises(xmltest.Stopped):
            xmltest._run(list(self.SLEEPS), 60, stop)
        self.assertLess(time.monotonic() - started, 10)

    def test_asked_before_it_starts(self):
        with mock.patch("subprocess.Popen") as popen, self.assertRaises(xmltest.Stopped):
            xmltest._run(list(self.SLEEPS), 60, lambda: True)
        popen.assert_not_called()

    def test_its_answer_when_left_to_run(self):
        done = xmltest._run([sys.executable, "-c", "print('said')"], 60, lambda: False)
        self.assertEqual((done.returncode, done.stdout.strip()), (0, b"said"))

    def test_its_time_still_runs_out(self):
        import subprocess
        with self.assertRaises(subprocess.TimeoutExpired):
            xmltest._run(list(self.SLEEPS), 1, lambda: False)

    def test_a_check_stopped_inside_the_loader_says_so(self):
        loadout = Loadout(game=games.ANNO_1800)
        loadout.set_change(_radius("1001", "9"))
        job = loadercheck.prepare(loadout, "", index=None)
        loader = xmltest.Loader(exe=Path("xmltest.exe"), game=games.ANNO_1800, version="2.3.1")
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "assets.xml"
            target.write_text("<AssetList/>", encoding="utf-8")
            with mock.patch.object(xmltest, "_run", side_effect=xmltest.Stopped()):
                verdict = loadercheck.run(job, None, loader, target, should_stop=lambda: False)
        self.assertEqual((verdict.state, verdict.why), (loadercheck.FAILED, "stopped"))


class _Planted:
    """A pickle that makes a folder as it is read: what anyone able to write
    into the cache could have had run."""

    def __init__(self, marker):
        self.marker = str(marker)

    def __reduce__(self):
        import os

        return os.mkdir, (self.marker,)


class TestTheUnpatchedFileIsKeptAsData(unittest.TestCase):
    """The loader's own output for no patch is kept between runs, and read
    back as data: JSON, never a pickle."""

    ASSETS = (b"<AssetList><Asset><Values><Standard><GUID>1</GUID></Standard></Values></Asset>"
              b"<Asset><Values><Standard><GUID>2</GUID></Standard></Values></Asset></AssetList>")

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        root = Path(self._tmp.name)
        self.exe, self.target, self.cache = root / "xmltest.exe", root / "assets.xml", root / "cache"
        self.exe.write_bytes(b"not run")
        self.target.write_bytes(self.ASSETS)
        self.marker = root / "ran"
        xmltest._base_memo.clear()
        self.addCleanup(xmltest._base_memo.clear)

    def _writes_it_back(self, args, *_rest):
        import subprocess

        Path(args[args.index("-o") + 1]).write_bytes(self.ASSETS)
        return subprocess.CompletedProcess(args, 0, b"", b"")

    def base(self):
        return xmltest._base_assets(self.exe, self.target, games.ANNO_1800, None, 60, self.cache)

    def test_it_is_kept_as_json_and_read_back_without_a_run(self):
        with mock.patch.object(xmltest, "_run", side_effect=self._writes_it_back):
            first = self.base()
        self.assertEqual(sorted(first.values()), ["1", "2"])
        [kept] = (self.cache / "base").iterdir()
        self.assertTrue(kept.name.endswith(".json.gz"))
        xmltest._base_memo.clear()
        with mock.patch.object(xmltest, "_run", side_effect=AssertionError("ran again")):
            self.assertEqual(self.base(), first)

    def test_a_pickle_in_the_cache_is_never_read(self):
        import hashlib
        import pickle

        key = (xmltest._identity(self.exe), xmltest._identity(self.target), games.ANNO_1800,
               xmltest.WHOLE_FORMAT)
        old = self.cache / "base" / ("%s.pickle" % hashlib.sha1(repr(key).encode()).hexdigest())
        old.parent.mkdir(parents=True)
        old.write_bytes(pickle.dumps(_Planted(self.marker)))
        with mock.patch.object(xmltest, "_run", side_effect=self._writes_it_back) as run:
            self.base()
        run.assert_called_once()
        self.assertFalse(self.marker.exists())

    def test_a_kept_file_that_is_not_the_map_is_run_again(self):
        import gzip

        with mock.patch.object(xmltest, "_run", side_effect=self._writes_it_back):
            self.base()
        [kept] = (self.cache / "base").iterdir()
        for junk in (b"not gzip", gzip.compress(b"[1, 2]"), gzip.compress(b'{"zz": "1"}')):
            with self.subTest(junk=junk):
                kept.write_bytes(junk)
                xmltest._base_memo.clear()
                with mock.patch.object(xmltest, "_run", side_effect=self._writes_it_back) as run:
                    self.assertEqual(sorted(self.base().values()), ["1", "2"])
                run.assert_called_once()


class TestAssetsTwoModsAdd(unittest.TestCase):
    def test_a_guid_another_mod_also_adds_is_said_and_set_apart(self):
        from anno_modding_studio.core.changes import OP_ADD_NEXT

        def adds(origin, guid, name):
            return Change(guid="1000", asset_label="x", path="", tag="Asset", op=OP_ADD_NEXT, origin=origin,
                          payload="<Asset><Values><Standard><GUID>%s</GUID><Name>%s</Name></Standard>"
                                  "</Values></Asset>" % (guid, name))

        loadout = Loadout(game=games.ANNO_1800)
        loadout.set_layer(ModLayer(origin="Agriculture", name="Extended Agriculture", order=1))
        loadout.set_layer(ModLayer(origin="Union", name="Trade Union", order=2))
        loadout.set_change(adds("Agriculture", "1500301983", "Advanced_Silo_Buff_Container_1"))
        loadout.set_change(adds("Union", "1500301983", "Simple Pump"))
        loadout.set_change(adds("Union", "1500301990", "Own"))
        self.assertEqual(loadercheck._shared(loadout, "Union", None), {"1500301983": ["Extended Agriculture"]})
        self.assertEqual(sorted(loadercheck.prepare(loadout, "Union", index=None).mine), ["1500301983", "1500301990"])

    def test_as_the_whole_loadout_s_check_sets_them_apart(self):
        """The same GUIDs as :func:`repeats.playing`: not an identical copy,
        one a mod tidies, nor one a mod removes outright (A11-48)."""
        from anno_modding_studio.core import repeats
        from anno_modding_studio.core.changes import OP_ADD_NEXT, OP_REMOVE, Unread

        def adds(origin, guid, name):
            return Change(guid="1000", asset_label="x", path="", tag="Asset", op=OP_ADD_NEXT, origin=origin,
                          payload="<Asset><Values><Standard><GUID>%s</GUID><Name>%s</Name></Standard>"
                                  "</Values></Asset>" % (guid, name))

        loadout = Loadout(game=games.ANNO_1800)
        loadout.set_layer(ModLayer(origin="A", name="A", order=1))
        loadout.set_layer(ModLayer(origin="B", name="B", order=2))
        for guid in ("77", "78", "79", "80"):
            loadout.set_change(adds("A", guid, "Same" if guid == "77" else "One"))
            loadout.set_change(adds("B", guid, "Same" if guid == "77" else "Other"))
        loadout.set_change(Change(guid="79", asset_label="x", path="", tag="Asset", op=OP_REMOVE, origin="B"))
        loadout.unread = [Unread(origin="B", source="<ModOp Type=\"remove\" "
                                 "Path=\"//Asset[Values/Standard/GUID='80'][position() &lt; last()]\"/>",
                                 why="test", order=1)]
        whole = {repeat.guid for repeat in repeats.playing(repeats.find(loadout))}
        self.assertEqual(whole, {"78"})
        self.assertEqual(loadercheck._shared(loadout, "B", None), {"78": ["A"]})

    def test_nor_one_the_base_game_ships_or_the_mod_adds_twice(self):
        """One mod and the base game under a GUID, or one mod twice, is set
        apart by the whole loadout's check, and so by the one mod's too
        (A11-48)."""
        from lxml import etree

        from anno_modding_studio.core import repeats
        from anno_modding_studio.core.changes import OP_ADD_NEXT

        class Shipped:
            def position(self, guid):
                if guid != "90":
                    raise KeyError(guid)
                return 5

            def asset_element(self, guid):
                return etree.fromstring("<Asset><Values><Standard><GUID>90</GUID><Name>Shipped</Name>"
                                        "</Standard></Values></Asset>")

        def adds(guid, name):
            return Change(guid="1000", asset_label="x", path="", tag="Asset", op=OP_ADD_NEXT, origin="A",
                          payload="<Asset><Values><Standard><GUID>%s</GUID><Name>%s</Name></Standard>"
                                  "</Values></Asset>" % (guid, name))

        loadout = Loadout(game=games.ANNO_1800)
        loadout.set_layer(ModLayer(origin="A", name="A", order=1))
        loadout.set_change(adds("90", "Mine"))
        loadout.set_change(adds("91", "One"))
        loadout.set_change(adds("91", "Two"))
        loadout.set_change(adds("92", "Alone"))
        index = Shipped()
        whole = {repeat.guid for repeat in repeats.playing(repeats.find(loadout, index))}
        self.assertEqual(whole, {"90", "91"})
        shared = loadercheck._shared(loadout, "A", index)
        self.assertEqual(shared, {"90": ["the base game (Shipped)"], "91": ["this mod itself"]})


class TestWhatUnreadOpsAreAimedAt(unittest.TestCase):
    SOURCE = ('<ModOp Type="merge" GUID="191675,191676, 191677" Path="/Values"/>'
              '<ModOp Merge="@1500"/><ModOp Content="@9"/>')

    def test_their_guids_and_not_what_they_only_read(self):
        # The space is kept: the loader looks for " 191677", and finds none.
        self.assertEqual(
            loadercheck._named_in(self.SOURCE, games.ANNO_117),
            ["191675", "191676", " 191677", "1500"],
        )

    def test_as_their_game_reads_them(self):
        """Anno 1800 has no short form, so ``Merge="@1500"`` is aimed at
        nothing there; and an op inside a group is aimed where it says."""
        self.assertEqual(
            loadercheck._named_in(self.SOURCE, games.ANNO_1800), ["191675", "191676", " 191677"]
        )
        self.assertEqual(
            loadercheck._named_in('<Group ModOpID="x"><ModOp Merge="@7/Values"/></Group>',
                                  games.ANNO_117),
            ["7"],
        )


class TestWithoutTheExtension(SchemaTestBase):
    def test_the_verdict_says_so_and_nothing_else_happens(self):
        loadout = Loadout(game=games.ANNO_1800)
        loadout.make_own_mod()
        loadout.set_change(_radius("1001", "9"))
        verdict = loadercheck.check(loadout, "", self.index, None)
        self.assertEqual(verdict.state, loadercheck.UNAVAILABLE)
        self.assertEqual(verdict.describe(), "Not checked by the game's loader: the Anno Modding "
                                             "Tools extension for VS Code is not installed")

    def test_one_line_each_way(self):
        loader = xmltest.Loader(Path("x.exe"), games.ANNO_1800, "2.3.1", "modloader GU17.1")
        agrees = loadercheck.Verdict(loadercheck.AGREES, name="My Mod", loader=loader, checked=3)
        self.assertEqual(agrees.describe(), "Checked by the game's loader (modloader GU17.1 from Anno "
                                            "Modding Tools 2.3.1): agrees on all 3 assets My Mod changes.")
        differs = loadercheck.Verdict(loadercheck.DIFFERS, name="My Mod", loader=loader, checked=3,
                                      differing_count=1, not_shown=2, warnings=["warning: No matching node"])
        self.assertEqual(differs.describe(), "Checked by the game's loader (modloader GU17.1 from Anno "
                                             "Modding Tools 2.3.1): sees 1 of the 3 assets My Mod changes "
                                             "differently; 2 more changed by ops the Studio carries unread; "
                                             "1 op matches nothing.")


    def test_a_mod_with_no_asset_left_to_compare_is_not_said_to_agree(self):
        """Every asset it changes set apart -- carried unread, differing
        beneath -- left "agrees on all 0 assets My Mod changes"."""
        loader = xmltest.Loader(Path("x.exe"), games.ANNO_1800, "2.3.1", "modloader GU17.1")
        verdict = loadercheck.Verdict(loadercheck.AGREES, name="My Mod", loader=loader, checked=0,
                                      not_shown=2)
        self.assertEqual(verdict.describe(), "Checked by the game's loader (modloader GU17.1 from Anno "
                                             "Modding Tools 2.3.1): no asset My Mod changes was left to "
                                             "compare; 2 more changed by ops the Studio carries unread.")
        whole = loadercheck.LoadoutVerdict(loadercheck.AGREES, loader=loader, mods=2, checked=0, seconds=1)
        self.assertIn("all 2 mods at once: no asset they change was left to compare", whole.describe())
        self.assertNotIn("0 assets", whole.describe())

    def test_a_mod_s_line_and_the_whole_loadout_s_are_one_ladder(self):
        """Two copies of it, and they had parted: the mod's line for a check
        that failed had no full stop, and the loadout's had."""
        self.assertIs(loadercheck.Verdict.describe, loadercheck.LoadoutVerdict.describe)
        mod = loadercheck.Verdict(loadercheck.FAILED, name="My Mod", why="stopped")
        whole = loadercheck.LoadoutVerdict(loadercheck.FAILED, why="stopped")
        self.assertEqual(mod.describe(), "The game's loader could not check My Mod: stopped.")
        self.assertEqual(whole.describe(), "The game's loader could not check the loadout: stopped.")
        mod.state = whole.state = loadercheck.UNAVAILABLE
        self.assertEqual(mod.describe(), whole.describe())
        self.assertEqual(loadercheck.Verdict(loadercheck.NOTHING, name="My Mod").describe(),
                         "My Mod has no ModOps for the game's loader to check.")
        self.assertEqual(loadercheck.LoadoutVerdict(loadercheck.NOTHING).describe(),
                         "No mod loads for the game's loader to check.")

    def test_a_check_that_failed_is_said_in_german_with_its_full_stop(self):
        """The full stop was first written into the sentence, which made it one
        the catalog had not got: the line came out in English."""
        from anno_modding_studio.core import i18n
        self.assertTrue(i18n.install("de"))
        self.addCleanup(i18n.install, i18n.SOURCE)
        mod = loadercheck.Verdict(loadercheck.FAILED, name="My Mod", why="0xC0000005")
        whole = loadercheck.LoadoutVerdict(loadercheck.FAILED, why="0xC0000005")
        self.assertEqual(mod.describe(), "Der Loader des Spiels konnte My Mod nicht prüfen: 0xC0000005.")
        self.assertEqual(whole.describe(), "Der Loader des Spiels konnte das Loadout nicht prüfen: 0xC0000005.")

    def test_only_an_op_that_matches_nothing_is_counted_as_one(self):
        """A missing include and a critical line were "2 ops match nothing"."""
        loader = xmltest.Loader(Path("x.exe"), games.ANNO_1800, "2.3.1", "modloader GU17.1")
        lines = ["warning: No matching node for Path \"//Asset\"",
                 "error: Include file missing or empty: data\\a.include.xml",
                 "critical: Failed to parse"]
        verdict = loadercheck.Verdict(loadercheck.AGREES, name="My Mod", loader=loader, checked=3,
                                      warnings=lines)
        self.assertTrue(verdict.describe().endswith(
            "agrees on all 3 assets My Mod changes; 1 op matches nothing; "
            "2 other warnings or errors from the loader."), verdict.describe())
        verdict.warnings = lines[1:2]
        self.assertTrue(verdict.describe().endswith(
            "My Mod changes; 1 other warning or error from the loader."), verdict.describe())
        whole = loadercheck.LoadoutVerdict(
            loadercheck.AGREES, loader=loader, mods=2, checked=3, seconds=1,
            warnings={"One": lines[:1], "Two": lines[1:]})
        self.assertIn("; 1 op matches nothing, in 1 mod; 2 other warnings or errors from the loader (",
                      whole.describe())


class TestWhichChangeIsBehindIt(unittest.TestCase):
    """A disagreement names the fields where the two part and the mod's
    changes there, so there is something to open rather than a diff to read."""

    ASSET = ("<Asset><Values><Standard><GUID>1</GUID></Standard><Building><Radius>%s</Radius></Building>"
             "<Cost><Costs>%s</Costs></Cost></Values></Asset>")
    ITEM = "<Item><Ingredient>%s</Ingredient><Amount>%s</Amount></Item>"

    def asset(self, radius="5", items=(("1010", "5"),)):
        return self.ASSET % (radius, "".join(self.ITEM % item for item in items))

    def change(self, path, op=OP_REPLACE):
        return {"guid": "1", "path": path, "said": "%s %s" % (op, path),
                "walks": loadercheck._bare(path), "creates": False}

    def test_a_field_that_differs(self):
        self.assertEqual(loadercheck._where_parted(self.asset("5"), self.asset("6")),
                         [("Values", "Building", "Radius")])

    def test_an_entry_in_a_list_and_a_list_that_grew(self):
        one = self.asset(items=(("1010", "5"), ("1011", "8")))
        self.assertEqual(loadercheck._where_parted(one, self.asset(items=(("1010", "5"), ("1011", "9")))),
                         [("Values", "Cost", "Costs", "Item", "Amount")])
        self.assertEqual(loadercheck._where_parted(one, self.asset(items=(("1010", "5"),))),
                         [("Values", "Cost", "Costs", "Item")])

    def test_gone_is_the_whole_asset(self):
        self.assertEqual(loadercheck._where_parted(loadercheck.GONE, self.asset()), [()])

    def test_the_changes_there_and_not_the_others(self):
        radius = self.change("/Values/Building/Radius")
        amount = self.change("/Values/Cost/Costs/Item[Ingredient='1011']/Amount")
        added = self.change("/Values/Cost/Costs", op="add")
        changes = [radius, amount, added]
        self.assertEqual(loadercheck._behind(changes, [("Values", "Building", "Radius")]), [radius])
        # A list that grew is about the addition to it and any change inside it.
        self.assertEqual(loadercheck._behind(changes, [("Values", "Cost", "Costs", "Item")]), [amount, added])

    def test_none_there_names_every_change_to_the_asset(self):
        radius = self.change("/Values/Building/Radius")
        self.assertEqual(loadercheck._behind([radius], [("Values", "Standard", "Name")]), [radius])


class TestANewerExtension(unittest.TestCase):
    def test_what_the_marketplace_says_is_newest(self):
        answer = {"results": [{"extensions": [{"versions": [
            {"version": "2.3.1", "lastUpdated": "2025-09-21T11:14:26.98Z"}]}]}]}
        published = xmltest.read_published(answer)
        self.assertEqual((published.version, published.date), ("2.3.1", "2025-09-21"))
        self.assertFalse(published.is_newer_than("2.3.1"))
        self.assertTrue(published.is_newer_than("2.2.10"))
        self.assertTrue(xmltest.Published("2.10.0").is_newer_than("2.9.9"))

    def test_an_answer_that_is_not_one(self):
        for answer in ({}, {"results": []}, {"results": [{"extensions": []}]},
                       {"results": [{"extensions": [{"versions": [{"version": "latest"}]}]}]}):
            with self.subTest(answer=answer), self.assertRaises(ValueError):
                xmltest.read_published(answer)


class TestWhatItWarns(unittest.TestCase):
    def test_only_the_checked_mod_s_own(self):
        """As a real run on the installed Anno 1800 folder put them: the mods
        stood on are numbered by their folders, and so is the checked one."""
        lines = [
            r"error: : Failed to open data\hier0nimus\buildings\assets.include.xml",
            r"error: 010: Include file missing or empty: data\hier0nimus\buildings\assets.include.xml",
            "warning: 012: No matching node for Path \"//Asset[Values/Standard/GUID='1']\"",
            "warning: No matching node for Path \"//Asset[Values/Standard/GUID='2']\"",
        ]
        self.assertEqual(loadercheck._own_warnings(lines, "012"), [
            "warning: No matching node for Path \"//Asset[Values/Standard/GUID='1']\"",
            "warning: No matching node for Path \"//Asset[Values/Standard/GUID='2']\"",
        ])


class TestWhatTheModsUnderneathLeave(unittest.TestCase):
    """An asset the loader was handed otherwise than the Studio reads the mods
    underneath is about those mods, counted apart -- also when the two sides
    end up alike, which was counted nowhere and the line said nothing of."""

    def judge(self, handed, before, shown, done):
        class Side:
            def __init__(self, _index, loadout):
                self.asset = loadout

            def steady(self):
                from contextlib import nullcontext
                return nullcontext()

            def effective_asset(self, _guid):
                return mock.Mock(as_the_game_reads_it=lambda: self.asset)

        from types import SimpleNamespace

        job = SimpleNamespace(studio=shown, underneath=handed, carried_guids=frozenset(), changes_at={})
        applied = SimpleNamespace(before={"1": before}, assets={"1": done})
        verdict = loadercheck.Verdict(loadercheck.UNAVAILABLE, name="My Mod")
        with mock.patch("anno_modding_studio.core.workspace.Workspace", Side), \
                mock.patch.object(loadercheck.recording, "Inert"), \
                mock.patch.object(loadercheck, "prepared", lambda asset, _inert: (asset, asset)):
            loadercheck._judge(verdict, job, None, applied, {"1"}, {"1"})
        return verdict

    def test_whether_or_not_the_two_sides_end_up_alike(self):
        for shown, done in (("z", "z"), ("y", "z")):
            with self.subTest(shown=shown, done=done):
                verdict = self.judge(handed="a", before="b", shown=shown, done=done)
                self.assertEqual((verdict.state, verdict.checked, verdict.beneath),
                                 (loadercheck.AGREES, 0, 1))


XMLTEST = xmltest.find(games.ANNO_1800)


@requires(XMLTEST, LOADER)
class TestTheCheck(SchemaTestBase):
    """Against the real loader, on the suite's own small assets.xml."""

    def loadout(self, *changes):
        loadout = Loadout(game=games.ANNO_1800, own=own_record(name="My Mod"), source_path=str(self.assets_path))
        loadout.make_own_mod()
        for change in changes:
            loadout.set_change(change)
        return loadout

    def test_a_mod_it_reads_as_the_studio_does(self):
        verdict = loadercheck.check(self.loadout(_radius("1001", "9")), "", self.index, XMLTEST)
        self.assertEqual((verdict.state, verdict.checked), (loadercheck.AGREES, 1), verdict.details())

    def test_and_one_it_does_not(self):
        loadout = self.loadout(_radius("1001", "9"))
        job = loadercheck.prepare(loadout, "", index=None)
        # The Studio's side says 99 where the export says 9.
        (change,) = job.studio.changes.values()
        job.studio.changes = {change.key: _radius("1001", "99")}
        verdict = loadercheck.run(job, self.index, XMLTEST)
        self.assertEqual((verdict.state, verdict.differing_count), (loadercheck.DIFFERS, 1))
        self.assertIn("1001", verdict.details()[2])
        (found,) = verdict.differing
        self.assertEqual(found["fields"], ["Values/Building/Radius"])
        self.assertEqual(found["changes"], [{"guid": "1001", "path": "/Values/Building/Radius",
                                             "said": "replace /Values/Building/Radius"}])

    def test_it_stands_on_the_mods_ahead(self):
        with tempfile.TemporaryDirectory() as tmp:
            ahead = Path(tmp) / "Ahead"
            patch = ahead / games.get(games.ANNO_1800).asset_relpath
            patch.parent.mkdir(parents=True)
            patch.write_text('<ModOps><ModOp Type="replace" GUID="1001" Path="/Values/Building/Radius">'
                             "<Radius>5</Radius></ModOp></ModOps>", encoding="utf-8")
            loadout = self.loadout(_radius("1001", "9"))
            loadout.set_layer(ModLayer(origin="Ahead", folder=str(ahead), order=1))
            loadout.set_change(_radius("1001", "5", "Ahead"))
            verdict = loadercheck.check(loadout, "", self.index, XMLTEST)
        self.assertEqual(verdict.on_top_of, ["Ahead"])
        self.assertEqual(verdict.state, loadercheck.AGREES, verdict.details())

    def test_what_it_warns_and_what_it_is_not_asked(self):
        loadout = self.loadout(_radius("1001", "9"))
        from anno_modding_studio.core.changes import Unread

        loadout.unread = [Unread(origin="", source='<ModOp Type="replace" GUID="4242" '
                                 'Path="/Values/Nothing"><Nothing/></ModOp>', why="test")]
        verdict = loadercheck.check(loadout, "", self.index, XMLTEST)
        self.assertEqual(verdict.state, loadercheck.AGREES, verdict.details())
        self.assertTrue(any("No matching node" in line for line in verdict.warnings), verdict.warnings)

    def test_nothing_to_check(self):
        verdict = loadercheck.check(self.loadout(), "", self.index, XMLTEST)
        self.assertEqual(verdict.state, loadercheck.NOTHING)


def _mod_folder(root: Path, name: str, ops: str, version: str = "1.0") -> Path:
    folder = root / name
    patch = folder / games.get(games.ANNO_1800).asset_relpath
    patch.parent.mkdir(parents=True)
    patch.write_text("<ModOps>%s</ModOps>" % ops, encoding="utf-8")
    (folder / "modinfo.json").write_text('{"ModID": "%s", "Version": "%s"}' % (name.lower(), version),
                                         encoding="utf-8")
    return folder


HULL = "/Values/Modules/Hull/ModuleCount"
HULL_OP = '<ModOp Type="replace" GUID="%s" Path="' + HULL + '"><ModuleCount>%s</ModuleCount></ModOp>'


def _hull(guid, value, origin=""):
    return Change(guid=guid, asset_label="x", path=HULL, tag="ModuleCount",
                  op=OP_REPLACE, old_value="", new_value=value, origin=origin)


RADIUS_OP = '<ModOp Type="replace" GUID="1001" Path="/Values/Building/Radius"><Radius>%s</Radius></ModOp>'


@requires(XMLTEST, LOADER)
class TestWhatUnreadOpsDo(SchemaTestBase):
    """Each op the Studio carries unread, asked of the loader: the mod with it
    and without it, and what differs."""

    def test_one_that_changes_an_asset_one_that_matches_nothing_and_markup(self):
        from anno_modding_studio.core.changes import Unread

        loadout = Loadout(game=games.ANNO_1800, own=own_record(name="My Mod"), source_path=str(self.assets_path))
        loadout.make_own_mod()
        loadout.set_change(_radius("1000", "3"))
        loadout.unread = [
            Unread(origin="", source=RADIUS_OP % "77", why="test", order=1),
            Unread(origin="", source='<ModOp Type="replace" GUID="4242" Path="/Values/Nothing">'
                                     "<Nothing/></ModOp>", why="test", order=2),
            Unread(origin="", source='<ImyaTweaks Title="Radius"/>', why="markup", order=3),
        ]
        job = loadercheck.prepare(loadout, "", index=None)
        seen = []
        effects = loadercheck.explain_unread(job, self.index, XMLTEST, progress=lambda *a: seen.append(a))
        self.assertEqual(len(effects), 2)  # the markup is not asked about
        changes, nothing = effects
        self.assertEqual(changes.count, 1, changes.details())
        self.assertEqual(changes.assets[0]["guid"], "1001")
        self.assertIn("+", "\n".join(changes.assets[0]["diff"]))
        self.assertTrue(changes.describe().startswith("Changes 1 asset in game: "))
        self.assertEqual(nothing.count, 0)
        self.assertTrue(nothing.warnings, "the loader's No matching node, found by its path")
        self.assertEqual(nothing.describe(), "Does nothing in game: the loader finds nothing where it is aimed")
        self.assertEqual(seen[-1], (2, 3))

    def test_without_the_loader_each_says_why(self):
        from anno_modding_studio.core.changes import Unread

        loadout = Loadout(game=games.ANNO_1800)
        loadout.make_own_mod()
        loadout.unread = [Unread(origin="", source=RADIUS_OP % "77", why="test", order=1)]
        (effect,) = loadercheck.explain_unread(loadercheck.prepare(loadout, "", index=None), self.index, None)
        self.assertEqual(effect.describe(), "Not asked: the Anno Modding Tools extension for VS Code is not installed")


@requires(XMLTEST, LOADER)
class TestTheWholeLoadout(SchemaTestBase):
    """Every loading mod through the loader at once, against the Studio."""

    def make_loadout(self, root: Path, first="5", second="9", read_second=None, version="1.0"):
        loadout = Loadout(game=games.ANNO_1800, source_path=str(self.assets_path))
        one = _mod_folder(root, "One", RADIUS_OP % first)
        two = _mod_folder(root, "Two", HULL_OP % ("1000", second), version=version)
        loadout.set_layer(ModLayer(origin="One", name="One", folder=str(one), order=1, version="1.0"))
        loadout.set_layer(ModLayer(origin="Two", name="Two", folder=str(two), order=2, version="1.0"))
        loadout.set_change(_radius("1001", first, "One"))
        loadout.set_change(_hull("1000", read_second or second, "Two"))
        return loadout

    def test_a_loadout_it_reads_as_the_studio_does(self):
        with tempfile.TemporaryDirectory() as tmp:
            job = loadercheck.prepare_loadout(self.make_loadout(Path(tmp)), [], self.index)
            said = []
            verdict = loadercheck.check_loadout(job, self.index, XMLTEST, progress=said.append)
        self.assertEqual((verdict.state, verdict.mods, verdict.checked), (loadercheck.AGREES, 2, 2),
                         verdict.describe())
        self.assertTrue(verdict.describe().startswith(
            "Checked by the game's loader (modloader GU17.1 from Anno Modding Tools 2.3.1), all 2 mods "
            "at once: agrees on all 2 assets they change ("), verdict.describe())
        self.assertIn("The game's loader is patching the game's assets with all 2 mods…", said)

    def test_and_one_it_does_not_names_the_mod_and_its_change(self):
        with tempfile.TemporaryDirectory() as tmp:
            # The Studio's reading of Two says 99 where its file says 9.
            job = loadercheck.prepare_loadout(self.make_loadout(Path(tmp), read_second="99"), [], self.index)
            verdict = loadercheck.check_loadout(job, self.index, XMLTEST)
        self.assertEqual((verdict.state, verdict.differing_count), (loadercheck.DIFFERS, 1))
        (found,) = verdict.differing
        self.assertEqual((found["guid"], found["mods"], found["fields"]),
                         ("1000", ["Two"], ["Values/Modules/Hull/ModuleCount"]))
        self.assertEqual(found["changes"], [{"guid": "1000", "path": HULL, "said": "replace " + HULL, "mod": "Two"}])

    def test_a_mod_updated_since_it_was_read_is_left_out_of_both_sides(self):
        with tempfile.TemporaryDirectory() as tmp:
            # Two's folder now says 2.0 and does something else; the Studio read 1.0.
            job = loadercheck.prepare_loadout(self.make_loadout(Path(tmp), read_second="99", version="2.0"),
                                              [], self.index)
            verdict = loadercheck.check_loadout(job, self.index, XMLTEST)
        self.assertEqual(job.updated, ["Two"])
        self.assertEqual((verdict.state, verdict.mods, verdict.checked), (loadercheck.AGREES, 1, 1))
        self.assertIn("1 left out, updated on disk since it was read", verdict.describe())

    def test_your_own_mod_goes_as_export_writes_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            loadout = self.make_loadout(Path(tmp))
            loadout.own.name = "Mine"
            loadout.make_own_mod()
            loadout.set_change(_hull("1001", "4"))
            job = loadercheck.prepare_loadout(loadout, [""], self.index)
            verdict = loadercheck.check_loadout(job, self.index, XMLTEST)
        self.assertEqual([name for name, _laid in job.stack], ["One", "Two", "Mine"])
        self.assertEqual((verdict.state, verdict.checked), (loadercheck.AGREES, 2), verdict.describe())

    def test_what_the_loader_warns_of_the_one_mod_of_several_that_patches(self):
        # Two has no patch of the assets, so the loader is handed One alone
        # and names it by no number: its warnings are still One's (A11-45).
        with tempfile.TemporaryDirectory() as tmp:
            loadout = self.make_loadout(Path(tmp))
            (Path(tmp) / "One" / games.get(games.ANNO_1800).asset_relpath).write_text(
                '<ModOps><ModOp Type="replace" GUID="4242" Path="/Values/Nothing"><Nothing/></ModOp></ModOps>',
                encoding="utf-8")
            (Path(tmp) / "Two" / games.get(games.ANNO_1800).asset_relpath).unlink()
            loadout.changes = {}
            job = loadercheck.prepare_loadout(loadout, [], self.index)
            verdict = loadercheck.check_loadout(job, self.index, XMLTEST)
        self.assertEqual(verdict.mods, 2)
        self.assertEqual(list(verdict.warnings), ["One"], verdict.warnings)


class TestAFallbackForACrash(unittest.TestCase):
    def test_what_counts_as_a_crash(self):
        crash = mock.Mock(returncode=0xC0000005)
        self.assertTrue(xmltest.crashed(crash))
        self.assertTrue(xmltest.crashed(mock.Mock(returncode=0xC00000FD)))
        self.assertTrue(xmltest.crashed(mock.Mock(returncode=-11)))
        self.assertFalse(xmltest.crashed(mock.Mock(returncode=1)))  # the loader said no
        self.assertFalse(xmltest.crashed(None))

    def test_only_xmltest3_has_one_and_only_beside_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            three, two = Path(tmp) / "xmltest3.exe", Path(tmp) / "xmltest2.exe"
            three.write_bytes(b"")
            self.assertIsNone(xmltest.fallback_for(three))
            two.write_bytes(b"")
            self.assertEqual(xmltest.fallback_for(three), two)
            self.assertIsNone(xmltest.fallback_for(Path(tmp) / "xmltest.exe"))

    def test_the_verdict_says_so(self):
        applied = xmltest.Applied(fallback=["000"])
        said = applied.said_fallback(Path("xmltest3.exe"))
        self.assertEqual(said, "xmltest3.exe crashed, so xmltest2.exe answered instead: it does not add an "
                               "<Asset> written straight under <ModOps>, and leaves some ModValues unworked, "
                               "so a difference there may be that build's")
        verdict = loadercheck.Verdict(loadercheck.AGREES, name="My Mod", loader=FAKE, checked=1, fallback=said)
        self.assertTrue(verdict.describe().endswith("; answered by a second build after the first crashed."))
        self.assertTrue(verdict.details()[1].startswith("xmltest3.exe crashed, so xmltest2.exe answered"))


XMLTEST3 = xmltest.find(games.ANNO_117)
# A ModValue reading its own field in an asset being added: xmltest3 (2.3.1)
# overflows its stack, xmltest2 adds the asset. Found in the census.
SELF_IN_ADDED = ('<ModOp Type="AddNextSibling" GUID="1000"><Asset><Template>Tech</Template><Values>'
                 "<Standard><GUID>1600000717</GUID><Name>probe</Name></Standard><Tech><KnowledgeNeeded>"
                 '<ModValue Insert="self::node()"/></KnowledgeNeeded></Tech></Values></Asset></ModOp>')


@requires(XMLTEST3 and XMLTEST3.exe.name == "xmltest3.exe" and xmltest.fallback_for(XMLTEST3.exe),
          "the Anno Modding Tools extension with both xmltest3 and xmltest2")
class TestTheFallbackRuns(unittest.TestCase):
    def setUp(self):
        tmp = Path(tempfile.mkdtemp(prefix="ams-fallback-"))
        self.addCleanup(__import__("shutil").rmtree, tmp, True)
        self.target = tmp / "assets.xml"
        self.target.write_text(loadercheck._file(games.ANNO_117), encoding="utf-8")
        self.mods = tmp / "mods"
        patch = self.mods / "probe" / games.get(games.ANNO_117).asset_relpath
        patch.parent.mkdir(parents=True)
        patch.write_text("<ModOps>%s</ModOps>" % SELF_IN_ADDED, encoding="utf-8")

    def test_one_mod_at_a_time(self):
        applied = xmltest.apply_stack(XMLTEST3.exe, self.target, [self.mods / "probe"], self.mods, games.ANNO_117)
        self.assertEqual((applied.failures, applied.fallback), ([], ["probe"]))
        self.assertIn("1600000717", applied.assets)

    def test_the_whole_stack_at_once(self):
        applied = xmltest.apply_whole(XMLTEST3.exe, self.target, [self.mods / "probe"], self.mods, games.ANNO_117)
        self.assertEqual((applied.failures, applied.fallback), ([], [""]))
        self.assertIn("1600000717", applied.assets)


class TestTheEndGroup(unittest.TestCase):
    """Anno 117 adds an ``<Asset>`` written straight under ``<ModOps>``
    whatever the file's last group holds -- measured in the game with a probe
    mod loaded first.  xmltest3 adds it only under an ``<Assets>`` of the last
    top-level group's own, which the shipped file's has not, so every stack
    is applied on top of an empty group at the end."""

    # The shipped file's shape: the last top-level group holds groups, and
    # no <Assets> of its own.
    TARGET = (
        "<AssetList><Groups>"
        "<Group><Assets><Asset><Template>Building</Template><Values><Standard><GUID>1000</GUID>"
        "<Name>alpha</Name></Standard></Values></Asset></Assets></Group>"
        "<Group><Groups><Group><Assets><Asset><Template>Building</Template><Values><Standard>"
        "<GUID>1001</GUID><Name>beta</Name></Standard></Values></Asset></Assets></Group></Groups></Group>"
        "</Groups></AssetList>"
    )
    BARE = ("<Asset><Template>Building</Template><Values><Standard><GUID>5001</GUID>"
            "<Name>bare</Name></Standard></Values></Asset>")

    def test_it_is_for_anno_117_alone(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(xmltest._end_group(Path(tmp), games.ANNO_1800), [])
            flag, folder = xmltest._end_group(Path(tmp), games.ANNO_117)
            self.assertEqual(flag, "-p")
            # Where -p reads a mod's patch, whichever game it is for.
            written = (Path(folder) / xmltest.PREPATCH_RELPATH).read_text(encoding="utf-8")
            self.assertEqual(written, xmltest.END_GROUP)
            self.assertIn('<ModOp Add="/AssetList/Groups[last()]"><Group><Assets /></Group></ModOp>', written)

    @requires(XMLTEST3 and XMLTEST3.exe.name == "xmltest3.exe", "the Anno Modding Tools extension with xmltest3")
    def test_a_bare_asset_is_added_with_no_group_of_the_mods_own(self):
        tmp = Path(tempfile.mkdtemp(prefix="ams-endgroup-"))
        self.addCleanup(__import__("shutil").rmtree, tmp, True)
        target = tmp / "assets.xml"
        target.write_text(self.TARGET, encoding="utf-8")
        mods = tmp / "mods"
        patch = mods / "probe" / games.get(games.ANNO_117).asset_relpath
        patch.parent.mkdir(parents=True)
        patch.write_text("<ModOps>%s</ModOps>" % self.BARE, encoding="utf-8")
        for name, apply in (("one mod at a time", xmltest.apply_stack), ("the whole stack", xmltest.apply_whole)):
            with self.subTest(name):
                applied = apply(XMLTEST3.exe, target, [mods / "probe"], mods, games.ANNO_117)
                self.assertEqual(applied.failures, [])
                # The asset, and nothing of the group it was given to land in.
                self.assertEqual(sorted(applied.assets), ["5001"])
                self.assertEqual(applied.warnings_by.get("probe"), [])


@requires(XMLTEST, LOADER)
class TestTheProbes(unittest.TestCase):
    def test_every_reading_rule_agrees_with_the_loader_installed(self):
        for game in (games.ANNO_1800, games.ANNO_117):
            loader = xmltest.find(game)
            results = loadercheck.probe(loader)
            with self.subTest(game=game):
                self.assertTrue(results)
                self.assertEqual([r.probe.name for r in results if not r.agrees], [],
                                 [r.said for r in results if not r.agrees])

    def test_a_probe_that_changes_nothing_is_not_an_agreement(self):
        loader = xmltest.find(games.ANNO_1800)
        idle = loadercheck.Probe("does nothing", games.ANNO_1800,
                                 '<ModOp Type="replace" GUID="4242" Path="/Values/Nothing"><Nothing/></ModOp>')
        (result,) = loadercheck.probe(loader, [idle])
        self.assertIsNone(result.agrees)
        self.assertIn("did not run", result.said)



FAKE = xmltest.Loader(Path("xmltest.exe"), games.ANNO_1800, "2.3.1", "modloader GU17.1")


def _differs(origin=""):
    return loadercheck.Verdict(loadercheck.DIFFERS, origin=origin, name="My Mod", loader=FAKE,
                               checked=2, differing_count=1,
                               differing=[{"guid": "1001", "label": "beta", "diff": ["-9", "+4"]}])


def _job(stands_on: int):
    """A check of "My Mod" standing on ``stands_on`` mods with folders."""
    return loadercheck.Job(
        origin="", name="My Mod", game=games.ANNO_1800, view=Loadout(),
        stack=[("Mod %d" % n, Path("mod-%d" % n)) for n in range(stands_on)] + [("My Mod", None)],
        studio=Loadout(), underneath=Loadout(), mine=frozenset(), present=frozenset(),
        shared={}, carried=False, carried_guids=frozenset(), writes=True, xml="<ModOps/>",
    )


class TestInTheWindow(PlayingWindow):
    def test_the_export_tab_says_when_there_is_no_loader(self):
        output = self.window.output
        output.set_index(object())
        output.set_loader_finder(lambda: None)
        output.check_with_loader(force=True)
        self.assertIsNone(output._checking)  # nothing to ask, so no thread
        self.assertEqual(output.loader_label.text(), "Not checked by the game's loader: the Anno "
                                                     "Modding Tools extension for VS Code is not installed")
        self.assertTrue(output.loader_button.isHidden())

    def test_and_what_it_found_when_there_is(self):
        output = self.window.output
        output._on_verdict(("", "x", (), 0), _differs())
        self.assertIn("sees 1 of the 2 assets My Mod changes differently", output.loader_label.text())
        self.assertEqual(output.loader_label.objectName(), "Warn")
        self.assertIn("beta (GUID 1001):", output.loader_label.toolTip())
        self.assertFalse(output.loader_button.isHidden())

    def test_each_difference_is_a_row_that_opens_its_change(self):
        output = self.window.output
        verdict = _differs()
        verdict.differing[0].update(fields=["Values/Building/Radius"], changes=[
            {"guid": "1001", "path": "/Values/Building/Radius", "said": "replace /Values/Building/Radius"}])
        output._on_verdict(("", "x", (), 0), verdict)
        rows = [output.check_list.item(row) for row in range(output.check_list.count())]
        (row,) = [item for item in rows if "the game's loader" in item.text()]
        self.assertIn("reads Values/Building/Radius differently. The change there: replace "
                      "/Values/Building/Radius.", row.text())
        opened = []
        output.changeActivated.connect(lambda guid, path: opened.append((guid, path)))
        output._open_problem(row)
        self.assertEqual(opened, [("1001", "/Values/Building/Radius")])
        # Asked again and agreeing, the row goes.
        output._on_verdict(("", "y", (), 0), loadercheck.Verdict(loadercheck.AGREES, name="My Mod",
                                                                 loader=FAKE, checked=2))
        self.assertFalse([output.check_list.item(r) for r in range(output.check_list.count())
                          if "the game's loader" in output.check_list.item(r).text()])

    def test_the_summary_is_not_all_clear_over_a_difference(self):
        class Shown:
            unread, scripts = (), None

            def __init__(self, count):
                self.active_count = count

            def __len__(self):
                return self.active_count

        output = self.window.output
        output.index = object()
        output._render_validation([], 3, Shown(3))
        verified = "All 3 changes verified against the base file."
        self.assertEqual((output.check_summary.text(), output.check_summary.objectName()), (verified, "Ok"))
        output._on_verdict(("", "x", (), 0), _differs())
        with_loader = verified + " The game's loader reads 1 asset differently (see below)."
        self.assertEqual((output.check_summary.text(), output.check_summary.objectName()), (with_loader, "Warn"))
        # Drawn again, as a refresh does, it still says both.
        output._render_validation([], 3, Shown(3))
        self.assertEqual((output.check_summary.text(), output.check_summary.objectName()), (with_loader, "Warn"))
        # A summary that verified nothing has nothing for the loader to add to.
        with mock.patch("anno_modding_studio.core.generator.writes_anything", return_value=False):
            output._render_validation([], 0, Shown(0))
        self.assertEqual((output.check_summary.text(), output.check_summary.objectName()),
                         ("No changes to export yet.", "SubHeading"))
        # Asked again and agreeing, it is all clear again.
        output._render_validation([], 3, Shown(3))
        output._on_verdict(("", "y", (), 0), loadercheck.Verdict(loadercheck.AGREES, name="My Mod",
                                                                 loader=FAKE, checked=2))
        self.assertEqual((output.check_summary.text(), output.check_summary.objectName()), (verified, "Ok"))

    def test_what_it_said_of_one_loadout_is_not_shown_over_the_next(self):
        """Your own mod is "" in every loadout: after closing one and opening
        a loadout, the line said the loader "agrees on all 7 assets Run Two
        Renamed changes" over the loadout's one-change mod (H2-10)."""
        output = self.window.output
        said = loadercheck.Verdict(loadercheck.AGREES, name="Run Two Renamed", loader=FAKE, checked=7)
        output._on_verdict(("", "x", (), 7), said)
        self.assertIn("Run Two Renamed", output.loader_label.text())
        with mock.patch.object(self.window.files, "confirm_discard", return_value=True):
            self.window.new_loadout()
        self.assertEqual(output._verdicts, {})
        self.assertNotIn("Run Two Renamed", output.loader_label.text())

    def test_nor_what_a_check_of_the_loadout_before_says_when_it_ends(self):
        from anno_modding_studio.ui import workers

        output = self.held_off()
        running = mock.Mock()
        output._checking = running
        self.window.forget_the_loadout_before()
        running.requestInterruption.assert_called_once()
        late = loadercheck.Verdict(loadercheck.AGREES, name="Run Two Renamed", loader=FAKE, checked=7)
        output._on_verdict(("", "x", (), 7), late)
        self.assertEqual(output._verdicts, {})
        self.assertNotIn("Run Two Renamed", output.loader_label.text())
        self.assertIsNone(output._checking)
        # And this loadout is asked instead, once the tab's wait runs out
        # (cut short here); what that says is shown.
        with mock.patch.object(output, "_job", return_value=_job(1)), \
                mock.patch.object(workers.LoaderCheckWorker, "start") as started:
            output._check_timer.setInterval(0)
            self.assertTrue(wait_until(lambda: started.called))
        output._on_verdict(("", "y", (), 1), loadercheck.Verdict(
            loadercheck.AGREES, name="Anno 1800 mod list patch", loader=FAKE, checked=1))
        self.assertIn("Anno 1800 mod list patch", output.loader_label.text())

    # -- how many mods a check runs, and Play's wait for it (H2-15) ---------
    TOO_MANY = ("The game's loader has not checked My Mod. The check would run 45 mods "
                "through the loader, and only checks of up to 40 mods start on their own.")
    HOW = ("Run it with “Ask the loader again” on the Export tab, or check the whole loadout from "
           "the Check menu.")

    def held_off(self, started=None):
        """The Export tab with a loader to ask, and nothing started by a timer."""
        output = self.window.output
        output.set_index(object())
        output.set_loader_finder(lambda: FAKE)
        output._check_timer.stop()
        self.addCleanup(self.window.background.end, "loader")
        return output

    def test_a_check_that_would_run_many_mods_waits_for_check_again(self):
        """A loadout's one-change mod stood on 311 mods, and the loader ran
        them for over two minutes at every opening (H2-15)."""
        from anno_modding_studio.ui import workers

        output = self.held_off()
        with mock.patch.object(output, "_job", return_value=_job(44)), \
                mock.patch.object(workers.LoaderCheckWorker, "start") as started:
            output.check_with_loader()
            started.assert_not_called()
            self.assertIsNone(output._checking)
            self.assertEqual(output.loader_label.text(), self.TOO_MANY + " “Ask the loader again” runs it.")
            self.assertEqual(output.loader_label.objectName(), "StepHint")
            self.assertFalse(output.loader_button.isHidden())
            # Asked again of the same mod, as every refresh asks: still not run.
            output.check_with_loader()
            started.assert_not_called()
            # Check again runs it, in the background. (Clicked by its signal:
            # the tab is shut while the window has no loadout's files.)
            output.loader_button.clicked.emit()
            started.assert_called_once()
            self.assertIsNotNone(output._checking)
            self.assertEqual(self.window.background.running(), ["Asking the game's loader about the export…"])
        output._checking = None

    def test_a_check_of_up_to_forty_mods_still_runs_by_itself(self):
        from anno_modding_studio.ui import workers

        self.assertEqual(loadercheck.AUTOMATIC_LIMIT, 40)
        output = self.held_off()
        with mock.patch.object(output, "_job", return_value=_job(39)), \
                mock.patch.object(workers.LoaderCheckWorker, "start") as started:
            # A loader found starts the tab's wait, cut short here.
            output.set_loader_finder(lambda: FAKE)
            output._check_timer.setInterval(0)
            self.assertTrue(wait_until(lambda: started.called))
        started.assert_called_once()
        output._checking = None

    def ask_for_play(self, job, run, during=None):
        """Play's verdicts on ``job``, the loader answering through ``run`` on
        the worker's thread; ``during`` is called with the wait's dialog."""
        import time

        from PySide6.QtCore import QTimer
        from PySide6.QtWidgets import QApplication

        window = self.window
        window.index = object()
        window.output.set_index(window.index)
        seen = []
        until = time.monotonic() + 10

        def look():
            dialog = QApplication.activeModalWidget()
            if dialog is None:
                return
            if time.monotonic() > until:
                # Never left open: a wait nobody answers would hang the suite.
                dialog.cancel()
            elif not seen:
                seen.append(dialog)
                if during is not None:
                    during(dialog)

        poll = QTimer()
        poll.setInterval(20)
        poll.timeout.connect(look)
        poll.start()
        try:
            with mock.patch.object(window.loader_check, "game_loader", return_value=FAKE), \
                    mock.patch.object(window.output, "_job", return_value=job), \
                    mock.patch.object(loadercheck, "run", side_effect=run):
                verdicts = window.loader_check.loader_verdicts([""])
        finally:
            poll.stop()
        return verdicts, seen

    def join_play_checks(self):
        """Let Play's checks end, and their threads go."""
        from PySide6.QtCore import QCoreApplication, QEvent

        for worker in list(self.window.loader_check.play_workers):
            self.assertTrue(worker.wait(5000))
        QCoreApplication.processEvents()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.assertEqual(self.window.loader_check.play_workers, [])

    def test_play_asks_the_loader_in_the_background_and_says_a_disagreement(self):
        """It asked on the window's own thread, with no way out, for as long
        as the loader took (H2-15)."""
        import threading

        released, threads, said = threading.Event(), [], []

        def run(*_args, **_kwargs):
            threads.append(threading.current_thread() is threading.main_thread())
            released.wait(5)
            return _differs()

        def during(dialog):
            said.append((dialog.windowTitle(), dialog.labelText()))
            released.set()

        verdicts, _seen = self.ask_for_play(_job(3), run, during)
        self.join_play_checks()
        self.assertEqual(threads, [False])
        self.assertEqual([v.state for v in verdicts], [loadercheck.DIFFERS])
        self.assertEqual(said, [("Waiting for the game's loader",
                                 "Waiting for this to finish first:\nAsking the game's loader about My Mod "
                                 "for Play…\n\nSkip the check to go on without it. It is then listed as "
                                 "not run.")])
        # The Export tab keeps the answer, and does not ask again.
        output = self.window.output
        self.assertIs(output.fresh_loader_verdict("", output._key(_job(3))), verdicts[0])
        self.assertFalse(self.window.background.busy)
        with mock.patch.object(self.window.loader_check, "game_loader", return_value=None):
            self.assertEqual(self.window.loader_check.loader_verdicts([""]), [])

    def test_skipping_the_wait_lets_play_go_on_and_says_it_was_not_run(self):
        import threading

        released = threading.Event()
        self.addCleanup(released.set)

        def run(*_args, **_kwargs):
            released.wait(5)
            return _differs()

        verdicts, seen = self.ask_for_play(_job(3), run, lambda dialog: dialog.cancel())
        self.assertEqual(len(seen), 1)
        self.assertEqual([(v.state, v.describe()) for v in verdicts], [(
            loadercheck.NOT_RUN,
            "The game's loader has not checked My Mod. You skipped the check. " + self.HOW)])
        self.assertFalse(self.window.background.busy)
        # Asked to stop; and what it said when it ended is not kept.
        (worker,) = self.window.loader_check.play_workers
        self.assertTrue(worker.isInterruptionRequested())
        released.set()
        self.join_play_checks()
        self.assertIsNone(self.window.output.fresh_loader_verdict("", self.window.output._key(_job(3))))

    def test_play_does_not_run_a_check_of_many_mods_either(self):
        ran = []
        verdicts, seen = self.ask_for_play(_job(44), lambda *a, **k: ran.append(a))
        self.assertEqual((ran, seen), ([], []))
        self.assertEqual([(v.state, v.describe()) for v in verdicts],
                         [(loadercheck.NOT_RUN, self.TOO_MANY + " " + self.HOW)])

    def test_play_s_first_wait_leaves_the_loader_to_its_own(self):
        from PySide6.QtWidgets import QMessageBox, QProgressDialog

        window = self.window
        window.index = object()
        window.background.begin("loader", "Asking the game's loader about the export…")
        self.addCleanup(window.background.end, "loader")
        with mock.patch.object(QProgressDialog, "exec", return_value=0) as waited, \
                mock.patch.object(window.prompts, "game_running", return_value=False), \
                mock.patch.object(window.where, "mods_folder", return_value=""), \
                mock.patch.object(QMessageBox, "information") as told:
            self.assertFalse(window.live.make_live(confirm=False))
        waited.assert_not_called()
        told.assert_called_once()  # went on as far as asking for the mods folder

    def test_the_probes_run_once_a_version_and_say_a_disagreement(self):
        window = self.window
        results = [loadercheck.ProbeResult(loadercheck.PROBES[0], True),
                   loadercheck.ProbeResult(loadercheck.PROBES[3], False, "-1\n+2")]
        window.loader_check.on_probed(FAKE, results)
        self.assertEqual(window.loader_check.probes_said(games.ANNO_1800),
                         "1 of 2 of the Studio's reading rules agree with modloader GU17.1 from Anno "
                         "Modding Tools 2.3.1. These do not: MaxRepeat: again while the condition holds")
        self.assertIn("reads 1 of the Studio's reading rules differently", window.status_label.text())
        window.loadout.game = games.ANNO_1800
        with mock.patch.object(window.loader_check, "game_loader", return_value=FAKE):
            self.assertFalse(window.loader_check.probe_loader())
        newer = xmltest.Loader(Path("xmltest.exe"), games.ANNO_1800, "2.4.0", "modloader GU17.1")
        from anno_modding_studio.ui import workers

        with mock.patch.object(window.loader_check, "game_loader", return_value=newer), \
                mock.patch.object(workers.ProbeWorker, "start") as started:
            self.assertTrue(window.loader_check.probe_loader())
        started.assert_called_once()
        window.loader_check.probe_worker = None

    def test_a_studio_that_reads_mods_differently_runs_the_probes_again(self):
        from anno_modding_studio.ui import loader_controller, workers

        window = self.window
        window.loadout.game = games.ANNO_1800
        window.loader_check.on_probed(FAKE, [loadercheck.ProbeResult(loadercheck.PROBES[0], True)])
        with mock.patch.object(window.loader_check, "game_loader", return_value=FAKE), \
                mock.patch.object(workers.ProbeWorker, "start") as started:
            self.assertFalse(window.loader_check.probe_loader())
            with mock.patch.object(loader_controller, "READER_VERSION", loader_controller.READER_VERSION + 1):
                self.assertTrue(window.loader_check.probe_loader())
        started.assert_called_once()
        window.loader_check.probe_worker = None

    def test_a_newer_extension_is_asked_for_once_a_day_and_said_with_a_failed_check(self):
        window = self.window
        from anno_modding_studio.ui import workers

        window.settings.remove(window.loader_check.NEWEST_ASKED_KEY)
        with mock.patch.object(window.loader_check, "game_loader", return_value=None):
            self.assertFalse(window.loader_check.ask_newest_loader())  # nothing installed, nothing to update
        with mock.patch.object(window.loader_check, "game_loader", return_value=FAKE), \
                mock.patch.object(workers.NewestLoaderWorker, "start") as started:
            self.assertTrue(window.loader_check.ask_newest_loader())
            started.assert_called_once()
            window.loader_check.newest_worker = None
            window.loader_check.on_newest_loader(xmltest.Published("2.4.0", "2026-10-01"))
            self.assertFalse(window.loader_check.ask_newest_loader())  # asked today already
            self.assertEqual(started.call_count, 1)
            self.assertEqual(window.loader_check.loader_update(), "Anno Modding Tools 2.4.0 is out (01 Oct 2026). The "
                                                     "check uses 2.3.1. VS Code updates it from its "
                                                     "Extensions view.")
            self.assertEqual(window.status_label.text(), window.loader_check.loader_update())
            output = window.output
            output._on_verdict(("", "x", (), 0), loadercheck.Verdict(
                loadercheck.FAILED, name="My Mod", loader=FAKE, why="xmltest3.exe stopped (0xC0000005)"))
            self.assertEqual(output.loader_label.text(), "The game's loader could not check My Mod: "
                                                         "xmltest3.exe stopped (0xC0000005). " + window.loader_check.loader_update())
            # The newest is the one installed: nothing to say.
            window.loader_check.on_newest_loader(xmltest.Published("2.3.1", "2025-09-21"))
            self.assertEqual(window.loader_check.loader_update(), "")
            self.assertNotIn("is out", output.loader_label.text())

    def test_the_loaders_own_checks_are_on_the_check_menu(self):
        """They were two buttons in Settings, among the settings."""
        window = self.window
        with mock.patch.object(window.loader_check, "game_loader", return_value=None):
            window._sync_menus()
        for action in (window.probes_action, window.newest_action):
            self.assertIn(action, window.check_menu.actions())
            self.assertFalse(action.isEnabled(), action.text())
            self.assertIn("Anno Modding Tools", action.toolTip())
        # The mod checks greyed: the menu still says why, live loader or not.
        for action in (window.conflicts_action, window.check_patterns_action,
                       window.unread_action, window.loadout_check_action):
            action.setEnabled(False)
        with mock.patch.object(window.loader_check, "game_loader", return_value=FAKE):
            window._sync_menus()
        self.assertTrue(window.probes_action.isEnabled())
        self.assertTrue(window._shut_rows[window.check_menu].isVisible())
        with mock.patch.object(window.loader_check, "probe_loader") as probed, \
                mock.patch.object(window.loader_check, "ask_newest_loader") as asked:
            window.probes_action.trigger()
            window.newest_action.trigger()
        probed.assert_called_once_with(force=True, say=True)
        asked.assert_called_once_with(force=True, say=True)

    def test_run_from_the_menu_they_say_how_they_came_out_even_when_all_is_well(self):
        """Run in the background, they said something only when something was
        wrong; from the menu, nothing at all would say they had finished."""
        window = self.window
        from anno_modding_studio.ui import workers

        agreed = [loadercheck.ProbeResult(loadercheck.PROBES[0], True)]
        window.status_label.setText("")
        window.loader_check.on_probed(FAKE, agreed)
        self.assertEqual(window.status_label.text(), "", "unasked, all is well says nothing")
        window.loadout.game = games.ANNO_1800
        with mock.patch.object(window.loader_check, "game_loader", return_value=FAKE), \
                mock.patch.object(workers.ProbeWorker, "start"):
            self.assertTrue(window.loader_check.probe_loader(force=True, say=True))
        window.loader_check.probe_worker = None
        window.loader_check.on_probed(FAKE, agreed)
        self.assertIn("1 of 1 of the Studio's reading rules agree", window.status_label.text())

        with mock.patch.object(window.loader_check, "game_loader", return_value=FAKE), \
                mock.patch.object(workers.NewestLoaderWorker, "start"):
            self.assertTrue(window.loader_check.ask_newest_loader(force=True, say=True))
            self.assertIn("Asking the Marketplace", window.status_label.text())
            window.loader_check.newest_worker = None
            window.loader_check.on_newest_loader(xmltest.Published("2.3.1", "2025-09-21"))
            self.assertIn("2.3.1 is the newest version published", window.status_label.text())
            self.assertTrue(window.loader_check.ask_newest_loader(force=True, say=True))
            window.loader_check.newest_worker = None
            window.loader_check.on_newest_loader(None)
            self.assertIn("could not be reached", window.status_label.text())

    def test_asked_from_the_menu_while_they_already_run_the_run_says_its_answer(self):
        """Loading the game's files starts both in the background; a click on
        the menu meanwhile started nothing and ended in silence (A11-114)."""
        window = self.window
        from anno_modding_studio.ui import workers

        agreed = [loadercheck.ProbeResult(loadercheck.PROBES[0], True)]
        window.loadout.game = games.ANNO_1800
        window.status_label.setText("")
        with mock.patch.object(window.loader_check, "game_loader", return_value=FAKE), \
                mock.patch.object(workers.ProbeWorker, "start"), \
                mock.patch.object(workers.NewestLoaderWorker, "start"):
            self.assertTrue(window.loader_check.probe_loader(force=True))
            self.assertFalse(window.loader_check.probe_loader(force=True, say=True))
            self.assertTrue(window.loader_check.ask_newest_loader(force=True))
            self.assertFalse(window.loader_check.ask_newest_loader(force=True, say=True))
        window.loader_check.probe_worker = None
        window.loader_check.on_probed(FAKE, agreed)
        self.assertIn("1 of 1 of the Studio's reading rules agree", window.status_label.text())
        window.loader_check.newest_worker = None
        window.loader_check.on_newest_loader(None)
        self.assertIn("could not be reached", window.status_label.text())

    def ask_in_the_background(self):
        """Asks the Marketplace with a real thread, and lets it end and go."""
        from PySide6.QtCore import QCoreApplication, QEvent

        window = self.window
        window.loader_check.newest_worker = None
        with mock.patch.object(window.loader_check, "game_loader", return_value=FAKE):
            self.assertTrue(window.loader_check.ask_newest_loader(force=True))
        worker = window.loader_check.newest_worker
        self.assertTrue(worker.wait(5000))
        QCoreApplication.processEvents()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)

    def test_a_marketplace_answer_cut_short_is_no_answer(self):
        """A dropped connection is no OSError (A10-14): the worker said
        nothing, and its reference outlived the thread."""
        import http.client

        cut = http.client.IncompleteRead(b"{")
        with mock.patch.object(xmltest, "newest_published", side_effect=cut), \
                mock.patch("traceback.print_exc"):
            self.ask_in_the_background()
        self.assertIsNone(self.window.loader_check.newest_worker)

    def test_a_worker_that_ends_without_an_answer_is_let_go(self):
        """However the thread ends, the window's reference goes with it: one
        left behind made closing the window wait on a deleted thread (A10-14)."""
        from anno_modding_studio.ui import workers

        with mock.patch.object(workers.NewestLoaderWorker, "run", lambda worker: None):
            self.ask_in_the_background()
        self.assertIsNone(self.window.loader_check.newest_worker)

    def test_the_unread_list_asks_the_loader_and_keeps_its_answers(self):
        from anno_modding_studio.core.changes import Unread
        from anno_modding_studio.ui import workers
        from anno_modding_studio.ui.unread_dialog import IN_GAME, UnreadDialog

        window = self.window
        window.loadout.make_own_mod()
        window.loadout.own.name = "My Mod"
        op = Unread(origin="", source=RADIUS_OP % "77", label="replace Radius", why="test", order=1)
        other = Unread(origin="", source='<Extras Title="R"/>', label="unknown", why="unknown", order=2)
        # A tweak tool's markup is carried, and is no row here (#174).
        markup = Unread(origin="", source='<ImyaTweaks Title="R"/>', label="tweaks", why="markup", order=3)
        window.loadout.unread = [op, other, markup]
        window.index = object()
        with mock.patch.object(window.loader_check, "game_loader", return_value=FAKE):
            dialog = UnreadDialog(window.loadout, window, window=window)
            self.addCleanup(dialog.deleteLater)
            dialog.table.selectRow(0)
            self.assertTrue(dialog.ask_button.isEnabled())
            self.assertIn("1 op in My Mod for the game's loader", dialog.ask_label.text())
            self.assertEqual(dialog.table.rowCount(), 2)
            self.assertEqual(dialog.table.item(1, IN_GAME).text(), "Not an op: nothing to run")
            with mock.patch.object(loadercheck, "prepare", return_value=mock.Mock(writes=False, view=None)), \
                    mock.patch.object(workers.UnreadEffectsWorker, "start") as started:
                dialog.ask_loader()
            started.assert_called_once()
            effect = loadercheck.UnreadEffect(1, op.source, count=1, assets=[
                {"guid": "1001", "label": "beta", "diff": ["-3", "+77"]}])
            dialog._on_effects("", [effect])
        self.assertEqual(dialog.table.item(0, IN_GAME).text(), "Changes 1 asset in game: beta")
        self.assertIn("+77", dialog.source.toPlainText())
        self.assertIn("the one op in My Mod changes something in game", dialog.ask_label.text())
        # Kept on the window: the next opening shows it without asking.
        again = UnreadDialog(window.loadout, window, window=window)
        self.addCleanup(again.deleteLater)
        self.assertEqual(again.table.item(0, IN_GAME).text(), "Changes 1 asset in game: beta")
        window.index = None

    def test_the_whole_loadout_says_how_long_first_and_needs_the_loader(self):
        from PySide6.QtWidgets import QMessageBox

        window = self.window
        window.index = object()
        with mock.patch.object(window.loader_check, "game_loader", return_value=None), \
                mock.patch.object(QMessageBox, "information") as told:
            self.assertFalse(window.loader_check.check_whole_loadout())
        told.assert_called_once()
        job = loadercheck.LoadoutJob(game=games.ANNO_1800, stack=[("One", None)] * 3, missing=["Gone"],
                                     updated=["Newer"], studio=Loadout(), touched=frozenset(),
                                     present=frozenset(), shared=frozenset(), carried=frozenset(), changes_at={})
        asked = []

        def answer(box):
            asked.append(box.text())
            return box.button(QMessageBox.StandardButton.Cancel)

        with mock.patch.object(window.loader_check, "game_loader", return_value=FAKE), \
                mock.patch.object(loadercheck, "prepare_loadout", return_value=job), \
                mock.patch.object(window.prompts, "answer", side_effect=answer), \
                mock.patch.object(window.loader_check, "_show_loadout_check") as shown:
            self.assertFalse(window.loader_check.check_whole_loadout())
        shown.assert_not_called()
        (text,) = asked
        self.assertIn("All 3 mods that load go through the game's loader", text)
        self.assertIn("This can take a while", text)
        self.assertIn("2 mods will be left out", text)
        window.index = None

    def test_a_stopped_check_keeps_the_last_result(self):
        """Stop is no result: the one before stays the one shown again (A10-33).
        A check the loader could not finish is kept, as the latest word."""
        from anno_modding_studio.ui.loadout_check_dialog import LoadoutCheckDialog

        window = self.window
        controller = window.loader_check
        good = loadercheck.LoadoutVerdict(loadercheck.AGREES, loader=FAKE, mods=3, checked=10)
        controller.last_loadout_check = ("20:14", good)
        stopped = loadercheck.LoadoutVerdict(loadercheck.FAILED, loader=FAKE, why=loadercheck.tr("stopped"))
        broke = loadercheck.LoadoutVerdict(loadercheck.FAILED, loader=FAKE, why="xmltest.exe stopped (0xC0000005)")
        for verdict, kept in ((stopped, good), (broke, broke)):
            with self.subTest(why=verdict.why), \
                    mock.patch.object(LoadoutCheckDialog, "start"), \
                    mock.patch.object(LoadoutCheckDialog, "exec",
                                      lambda dialog, verdict=verdict: dialog.show_verdict(verdict)):
                controller._show_loadout_check(None, FAKE)
                self.assertIs(controller.last_loadout_check[1], kept)
        controller.last_loadout_check = None

    def test_the_loadout_dialog_lists_each_difference_and_opens_its_change(self):
        from anno_modding_studio.ui.loadout_check_dialog import LoadoutCheckDialog

        verdict = loadercheck.LoadoutVerdict(
            loadercheck.DIFFERS, loader=FAKE, mods=3, checked=10, differing_count=2, seconds=51,
            updated=["Item Sockets"], warnings={"Town Hall": ["warning: No matching node"]},
            differing=[{"guid": "1000", "label": "alpha", "diff": ["-1", "+2"], "fields": [HULL[1:]],
                        "mods": ["Two"], "changes": [{"guid": "1000", "path": HULL, "said": "replace " + HULL,
                                                      "mod": "Two"}]}])
        dialog = LoadoutCheckDialog(None, None, FAKE)
        self.addCleanup(dialog.deleteLater)
        dialog.show_verdict(verdict)
        self.assertIn("sees 2 of the 10 assets they change differently", dialog.status.text())
        self.assertEqual(dialog.table.item(0, 1).text(), "Two")
        self.assertEqual(dialog.table.item(0, 3).text(), "Two: replace " + HULL)
        self.assertIn("Listed: the first 1 of 2.", dialog.notes.text())
        self.assertIn("since the loadout read them: Item Sockets. Read the mods folder again", dialog.notes.text())
        self.assertIn("Town Hall (1)", dialog.notes.text())
        opened = []
        dialog.changeActivated.connect(lambda guid, path: opened.append((guid, path)))
        dialog.open_row(0)
        self.assertEqual(opened, [("1000", HULL)])

    def test_settings_says_what_it_found(self):

        from anno_modding_studio.ui.settings_dialog import SettingsDialog

        with mock.patch.object(self.window.loader_check, "game_loader", side_effect=lambda game="": FAKE if game != games.ANNO_117 else None):
            dialog = SettingsDialog(self.window, self.window, store=no_credentials())
            self.addCleanup(dialog.deleteLater)
        self.assertIn("modloader GU17.1 from Anno Modding Tools 2.3.1", dialog.loader_labels[games.ANNO_1800].text())
        self.assertTrue(dialog.loader_labels[games.ANNO_117].text().startswith("Not found"))


if __name__ == "__main__":
    unittest.main()
