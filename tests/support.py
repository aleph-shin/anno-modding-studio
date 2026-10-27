"""What the test modules share: fixtures, fakes and bases.

Test modules used to import these from each other -- ``from test_truth import
_Folders`` and thirty-one more -- which loaded one test module's whole
namespace into another and left nobody sure where a helper lived.  Anything
two test modules need goes here instead.
"""

from __future__ import annotations

import functools
import hashlib
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock
from urllib.parse import parse_qs, urlparse

import _isolation  # first: keeps the suite out of real files under any runner

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from lxml import etree  # noqa: E402

from anno_modding_studio.core import (  # noqa: E402
    convert,
    imya,
    library,
    modfolder,
    modio,
    modio_cache,
    modops,
    places,
    reader,
    switches,
    truth,
)
from anno_modding_studio.core import index as index_mod  # noqa: E402
from anno_modding_studio.core.changes import Loadout, own_record  # noqa: E402
from anno_modding_studio.core.games import ANNO_1800  # noqa: E402

# -- a small index, and ModOps read against it --------------------------------
IMPORT_ASSETS = """<AssetList><Assets>
  <Asset><Template>Building</Template><Values>
    <Standard><GUID>1000</GUID><Name>alpha</Name></Standard>
    <Building><BuildingType>Factory</BuildingType><Radius>10</Radius></Building>
    <Empty />
    <Cost><Costs>
      <Item><Ingredient>1010</Ingredient><Amount>5</Amount></Item>
      <Item><Ingredient>1011</Ingredient><Amount>8</Amount></Item>
    </Costs></Cost>
    <ProductList><List>
      <Item><Product>2001</Product></Item>
      <Item><Product>2002</Product></Item>
      <Item><Product>2003</Product></Item>
    </List></ProductList>
  </Values></Asset>
  <Asset><Template>Building</Template><Values>
    <Standard><GUID>1001</GUID><Name>beta</Name></Standard>
    <Building><BuildingType>Farm</BuildingType><Radius>4</Radius></Building>
  </Values></Asset>
  <!-- gamma exists to be read strictly: its cost list mixes goods that cost
       something with goods that cost nothing, which is how the shipped files
       are written, and its product list is membership only. -->
  <Asset><Template>Building</Template><Values>
    <Standard><GUID>1002</GUID><Name>gamma</Name></Standard>
    <Cost><Costs>
      <Item><Ingredient>1010</Ingredient><Amount>5</Amount></Item>
      <Item><Ingredient>1011</Ingredient><Amount>7</Amount></Item>
      <Item><Ingredient>1012</Ingredient></Item>
      <Item><Ingredient>1013</Ingredient></Item>
    </Costs></Cost>
    <ProductList><List>
      <Item><Product>2001</Product></Item>
      <Item><Product>2002</Product></Item>
      <Item><Product>2003</Product></Item>
      <Item><Product>2004</Product></Item>
    </List></ProductList>
  </Values></Asset>
%s
</Assets></AssetList>""" % "".join(
    "<Asset><Template>Product</Template><Values><Standard><GUID>%d</GUID>"
    "<Name>good_%d</Name></Standard></Values></Asset>" % (guid, guid)
    for guid in list(range(1010, 1014)) + list(range(2001, 2005))
)


def modops_file(body: str) -> str:
    return "<ModOps>%s</ModOps>" % body


class ImportCase(unittest.TestCase):
    """A real index over a small assets.xml, which is what makes ops decidable."""

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        tmp = Path(cls._tmp.name)
        cls.assets = tmp / "assets.xml"
        cls.assets.write_text(IMPORT_ASSETS, encoding="utf-8")
        cls.db = tmp / "index.sqlite"
        index_mod.build_index(cls.assets, cls.db)
        cls.index = index_mod.AssetIndex(cls.db, cls.assets)

    @classmethod
    def tearDownClass(cls):
        cls.index.close()
        cls._tmp.cleanup()

    def read(self, body: str, keep_inert: bool = True) -> modops.ImportReport:
        """Run a ModOps document through the whole translation.

        ``keep_inert`` by default, so these tests see every removal a wide
        replace implies; the ones about leaving those out say so.
        """
        report = modops.ImportReport()
        root = etree.fromstring(modops_file(body).encode("utf-8"))
        ops = modops.parse_ops(root, report.extras, "assets.xml")
        report.results = reader.Translator(self.index, keep_inert).run_all(ops)
        return report

    def only(self, body: str) -> modops.OpResult:
        report = self.read(body)
        self.assertEqual(len(report.results), 1, report.results)
        return report.results[0]

    def imported(self, body: str):
        result = self.only(body)
        self.assertEqual(result.status, modops.IMPORTED, result.reason)
        return result.changes

    def products(self, root) -> list:
        return [item.findtext("Product") for item in root.findall("Values/ProductList/List/Item")]


RADIUS = "/Values/Building/Radius"


def radius_change(origin: str, old: str, new: str, guid: str = "1000"):
    """One mod setting alpha's (or another asset's) radius."""
    from anno_modding_studio.core.changes import Change

    return Change(
        guid=guid, asset_label="alpha", path=RADIUS, tag="Radius",
        old_value=old, new_value=new, origin=origin,
    )


class StackCase(ImportCase):
    """Two loaded mods, *Early* and *Late*, over the small index, and a
    workspace on them: one asset as each mod finds it and leaves it."""

    def setUp(self):
        from anno_modding_studio.core.changes import ModLayer
        from anno_modding_studio.core.workspace import Workspace

        self.loadout = Loadout(own=own_record(name="Mine"))
        self.loadout.set_layer(ModLayer(origin="Early", name="Early", order=1))
        self.loadout.set_layer(ModLayer(origin="Late", name="Late", order=2))
        self.workspace = Workspace(self.index, self.loadout)


class SessionCase(ImportCase):
    """A folder of mods on disk, read the way the game reads them."""

    def mods(self, **folders) -> Path:
        """Write ``{folder name: modops body}`` into a fresh mods directory."""
        root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, root, True)
        for name, body in folders.items():
            name = name.replace("__", " ")
            patch = root / name / "data/base/config/export/assets.xml"
            patch.parent.mkdir(parents=True)
            patch.write_text(modops_file(body), encoding="utf-8")
        return root

    def session(self, root: Path, **kwargs):
        return reader.analyse_all(modfolder.mod_folders(root), self.index, **kwargs)


MODINFO_WITH_OPTIONS = """{
  "ModName": {"English": "Configurable Mod"},
  "Options": {
    "ph_head": {"type": "enum", "default": "----------",
                "values": ["----------"], "label": "----- Section -----"},
    "roomier": {"type": "toggle", "default": "false",
                "label": "Make it roomier?",
                "labels": ["Yes, roomier", "No, leave it"]},
    "factor": {"type": "slider", "default": "1",
               "values": ["0", "2", "0.5"], "label": "Cost factor"},
    "size": {"type": "enum", "default": "10", "values": ["5", "10", "20"],
             "labels": ["Small", "Medium", "Large"], "label": "Radius"}
  }
}"""


# -- an index that knows its templates and properties -------------------------
SCHEMA_ASSETS = """<AssetList><Assets>
  <Asset><Template>FactoryBuilding7</Template><Values>
    <Standard><GUID>1000</GUID><Name>alpha</Name></Standard>
    <Building><BuildingType>Factory</BuildingType><TerrainType>Land</TerrainType></Building>
    <Cost><Costs>
      <Item><Ingredient>1010196</Ingredient><Amount>5</Amount></Item>
      <Item><Ingredient>1010197</Ingredient><Amount>8</Amount><Rare>1</Rare></Item>
    </Costs></Cost>
    <Empty><Slots /></Empty>
    <Modules><Hull><ModuleCount>1</ModuleCount></Hull></Modules>
  </Values></Asset>
  <Asset><Template>FactoryBuilding7</Template><Values>
    <Standard><GUID>1001</GUID><Name>beta</Name></Standard>
    <Building><BuildingType>Factory</BuildingType><Radius>3</Radius></Building>
    <Cost><Costs><Item><Ingredient>1010196</Ingredient><Amount>2</Amount></Item></Costs></Cost>
    <Modules><Hull><ModuleCount>2</ModuleCount></Hull></Modules>
  </Values></Asset>
</Assets></AssetList>"""


SCHEMA_TEMPLATES = """<Templates><Group><Name>G</Name>
  <Template><Name>FactoryBuilding7</Name>
    <Properties><Standard /><Building /><Cost /></Properties>
  </Template>
  <Template><Name>Product</Name>
    <Properties><Standard /><Text /><Product /></Properties>
  </Template>
</Group></Templates>"""


SCHEMA_PROPERTIES = """<Properties><Groups><Group><Name>Balancing</Name>
  <DefaultValues><Cost><ItemsPerCost>1</ItemsPerCost></Cost></DefaultValues>
  <DefaultContainerValues>
    <Cost><Costs><Ingredient>0</Ingredient><Amount>1</Amount></Costs></Cost>
    <Empty><Slots><Declared>0</Declared></Slots></Empty>
  </DefaultContainerValues>
</Group></Groups></Properties>"""


class SchemaTestBase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        tmp = Path(cls._tmp.name)
        cls.assets_path = tmp / "assets.xml"
        cls.assets_path.write_text(SCHEMA_ASSETS, encoding="utf-8")
        (tmp / "templates.xml").write_text(SCHEMA_TEMPLATES, encoding="utf-8")
        (tmp / "properties.xml").write_text(SCHEMA_PROPERTIES, encoding="utf-8")
        cls.db_path = tmp / "index.sqlite"
        index_mod.build_index(cls.assets_path, cls.db_path)
        cls.index = index_mod.AssetIndex(cls.db_path, cls.assets_path)
        cls.index.load_schema_files(tmp / "templates.xml", tmp / "properties.xml")
        cls.schema = cls.index.schema

    @classmethod
    def tearDownClass(cls):
        cls.index.close()
        cls._tmp.cleanup()


# -- mod.io, faked ------------------------------------------------------------
def modio_mod(mod_id: int, name: str, version: str = "1.0", modfile: int = 0) -> dict:
    return {
        "id": mod_id,
        "name": name,
        "name_id": name.lower().replace(" ", "-"),
        "profile_url": "https://mod.io/g/anno-1800/m/%d" % mod_id,
        "date_updated": 100 + mod_id,
        "submitted_by": {"username": "modder"},
        "stats": {"downloads_total": 5},
        "modfile": {"id": modfile or mod_id * 10, "version": version, "date_added": 50, "filesize": 1024},
    }


class FakeServer:
    """Answers the way mod.io does, and remembers what it was asked."""

    def __init__(self, mods=None, statuses=None, deps=None, collections=None, files=None):
        self.mods = {m["id"]: m for m in (mods or [])}
        # mod id -> every file the mod has had, as mod.io lists them; a mod
        # not named here has the one file it carries.
        self.files = dict(files or {})
        # Collection objects as mod.io returns them, each with a "mods" list of
        # ids that is the fake's own and is not sent.
        self.collections = {c["id"]: c for c in (collections or [])}
        self.statuses = list(statuses or [])  # forced answers, first request first
        self.deps = dict(deps or {})  # mod id -> ids it needs
        self.calls = []

    def __call__(self, url: str, headers: dict) -> modio.Response:
        self.calls.append((url, headers))
        if self.statuses:
            status, retry = self.statuses.pop(0)
            return modio.Response(status, {"retry-after": retry} if retry else {}, b'{"error":"x"}')
        parsed = urlparse(url)
        query = parse_qs(parsed.query)
        last = parsed.path.rsplit("/", 1)[-1]
        if "/collections" in parsed.path:
            return self._collections(parsed.path, query)
        if parsed.path.endswith("/files"):
            mod_id = int(parsed.path.split("/")[-2])
            carried = (self.mods.get(mod_id) or {}).get("modfile")
            data = self.files.get(mod_id) or ([carried] if carried else [])
            return modio.Response(200, {}, json.dumps({"data": data, "result_count": len(data), "result_total": len(data)}).encode())
        if "/mods/" in parsed.path and last.isdigit():
            mod = self.mods.get(int(last))
            # A hidden mod is there for its team only; anyone else is told it
            # does not exist, as mod.io does.
            if mod is None or (not mod.get("visible", 1) and "Authorization" not in headers):
                return modio.Response(404, {}, b'{"error":{"code":404,"error_ref":15022,"message":"The requested mod id could not be found."}}')
            return modio.Response(200, {}, json.dumps(mod).encode("utf-8"))
        if parsed.path.endswith("/dependencies"):
            mod_id = int(parsed.path.split("/")[-2])
            data = [
                {"mod_id": dep, "name": self.mods[dep]["name"], "name_id": self.mods[dep].get("name_id", "")}
                for dep in self.deps.get(mod_id, [])
            ]
            return modio.Response(200, {}, json.dumps({"data": data, "result_count": len(data), "result_total": len(data)}).encode())
        # The listing never carries a hidden mod, whoever is asking.
        listed = {i: m for i, m in self.mods.items() if m.get("visible", 1)}
        if "name_id" in query:
            data = [m for m in listed.values() if m.get("name_id") == query["name_id"][0]]
        elif "id-in" in query:
            wanted = [int(i) for i in query["id-in"][0].split(",")]
            data = [listed[i] for i in wanted if i in listed]
        elif "_q" in query:
            needle = query["_q"][0].casefold()
            data = [m for m in listed.values() if needle in m["name"].casefold()]
        else:
            data = list(listed.values())
        body = {"data": data, "result_count": len(data), "result_total": len(data)}
        return modio.Response(200, {}, json.dumps(body).encode("utf-8"))


    def _collections(self, path: str, query: dict) -> modio.Response:
        def sent(collection):
            return {k: v for k, v in collection.items() if k != "mods"}

        parts = path.split("/")
        after = parts[parts.index("collections") + 1:]
        if after and after[0].isdigit():
            collection = self.collections.get(int(after[0]))
            if collection is None:
                return modio.Response(404, {}, b'{"error":{"code":404,"message":"not found"}}')
            if after[1:] == ["mods"]:
                # Paged as mod.io pages it, so a long collection takes several asks.
                every = [self.mods[i] for i in collection.get("mods", []) if i in self.mods]
                offset = int(query.get("_offset", ["0"])[0])
                limit = int(query.get("_limit", ["100"])[0])
                data = every[offset:offset + limit]
                body = {"data": data, "result_count": len(data), "result_total": len(every)}
                return modio.Response(200, {}, json.dumps(body).encode("utf-8"))
            return modio.Response(200, {}, json.dumps(sent(collection)).encode("utf-8"))
        found = list(self.collections.values())
        if "name_id" in query:
            found = [c for c in found if c.get("name_id") == query["name_id"][0]]
        elif "_q" in query:
            needle = query["_q"][0].casefold()
            found = [c for c in found if needle in c["name"].casefold()]
        data = [sent(c) for c in found]
        body = {"data": data, "result_count": len(data), "result_total": len(data)}
        return modio.Response(200, {}, json.dumps(body).encode("utf-8"))


def fake_client(server, **kwargs) -> modio.Client:
    clock = [0.0]

    def tick():
        return clock[0]

    def sleep(seconds):
        clock[0] += seconds

    return modio.Client("sekrit", fetch=server, sleep=sleep, clock=tick, **kwargs)


# -- mods on disk -------------------------------------------------------------
def zip_bytes(entries: dict) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zipped:
        for name, data in entries.items():
            zipped.writestr(name, data)
    return buffer.getvalue()


def downloadable_mod(mod_id: int, name: str, version: str, body: bytes = b"", flagged: bool = False) -> dict:
    raw = {
        "id": mod_id,
        "name": name,
        "name_id": "mod-%d" % mod_id,
        "profile_url": "https://mod.io/g/anno-1800/m/mod-%d" % mod_id,
        "submitted_by": {"username": "modder"},
        "stats": {"downloads_total": 1},
    }
    if body:
        raw["modfile"] = {
            "id": mod_id * 10,
            "version": version,
            "filename": "mod%d.zip" % mod_id,
            "filesize": len(body),
            "filesize_uncompressed": len(body) * 3,
            "filehash": {"md5": hashlib.md5(body).hexdigest()},
            "download": {"binary_url": "https://cdn/%d" % mod_id},
            "virus_positive": int(flagged),
        }
    return raw


def modio_collection(collection_id: int, name: str, mods, **more) -> dict:
    raw = {
        "id": collection_id,
        "name": name,
        "name_id": name.lower().replace(" ", "-").replace("'", ""),
        "game_name_id": "anno-1800",
        "summary": "All of them.",
        "submitted_by": {"username": "collector"},
        "date_updated": 1700000000,
        "filesize": 2048,
        "filesize_uncompressed": 4096,
        "incomplete": False,
        "stats": {"mods_total": len(mods), "downloads_total": 9, "followers_total": 4, "incomplete": 0},
        "mods": list(mods),
    }
    raw.update(more)
    return raw


def install_mod(folder: Path, name: str, mod_id: str = "", version: str = "", mod_name: str = "") -> Path:
    mod = folder / name
    (mod / "data").mkdir(parents=True)
    info = {}
    if mod_id:
        info["ModID"] = mod_id
    if version:
        info["Version"] = version
    if mod_name:
        info["ModName"] = {"English": mod_name}
    (mod / "modinfo.json").write_text(json.dumps(info), encoding="utf-8")
    return mod


# -- a mods folder, a Documents folder and the mod.io cache, and a window over them ---
class TruthFolders(unittest.TestCase):
    game = ANNO_1800

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.mods = self.root / "mods"
        self.mods.mkdir()
        self.documents = self.root / "documents"
        self.documents.mkdir()
        self.cache_root = self.root / "modio"
        for patched in (
            mock.patch.dict(os.environ, {modio_cache.ENV_ROOT: str(self.cache_root)}),
            mock.patch.object(switches, "documents_mods", lambda game: self.documents),
        ):
            patched.start()
            self.addCleanup(patched.stop)

    def cached(self, mod_id: int, folder: str, mod: str = "", version: str = "") -> Path:
        placed = install_mod(self.cache_root / str(4169 if self.game == ANNO_1800 else 11358) / "mods" / str(mod_id), folder, mod, version)
        return placed

    def loadout(self, *layers) -> Loadout:
        loadout = Loadout()
        loadout.game = self.game
        loadout.layers = list(layers)
        return loadout

    def read(self, *layers) -> truth.Truth:
        return truth.read(self.loadout(*layers), self.mods)


class TruthWindow(TruthFolders):
    """A window over the test's folders, with a game loaded as far as the
    truth needs to know."""

    def setUp(self):
        super().setUp()
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication

        from anno_modding_studio.ui.main_window import MainWindow

        self.app = QApplication.instance() or QApplication([])
        self.window = MainWindow()
        self.addCleanup(_isolation.forget_settings)
        self.addCleanup(self.window.close)
        self.addCleanup(self.window.editor.search._debounce.stop)
        # A game is loaded, as far as the truth needs to know, and the folder
        # to compare against is the test's.
        self.window.index = mock.Mock(source_path=str(self.root / "assets.xml"), asset_count=0, text_count=0)
        self.addCleanup(setattr, self.window, "index", None)
        # A library of the test's own: which loadout is live is written there,
        # and one run's library is shared by every test in it.
        # And this PC knows the test's mods folder, as one read in: a loadout
        # naming it opens without being asked about it.
        for name, value in (("mods_folder", str(self.mods)), ("library_dir", self.root / "library"),
                            ("known_mods_folders", [str(self.mods)])):
            patched = mock.patch.object(self.window.where, name, return_value=value)
            patched.start()
            self.addCleanup(patched.stop)
        self.window.loadout.game = self.game


class PlayingWindow(TruthWindow):
    """A loadout, a library and a Steam install to start it through."""

    def setUp(self):
        super().setUp()
        self.window.loadout.mods_folder = str(self.mods)
        self.window.loadout.set_up_as("manage")
        self.window.loadout_path = self.root / "Main.annoload"
        self.started = len(_isolation.STARTED)
        steamapps = self.root / "steam" / "steamapps"
        self.install = steamapps / "common" / "Anno 1800"
        (self.install / "Bin" / "Win64").mkdir(parents=True)
        (self.install / "Bin" / "Win64" / "Anno1800.exe").write_bytes(b"")
        (steamapps / "appmanifest_916440.acf").write_text(
            '"AppState"\n{\n\t"appid"\t\t"916440"\n\t"installdir"\t\t"Anno 1800"\n}\n', encoding="utf-8"
        )
        from anno_modding_studio.core import launch

        patched = mock.patch.object(launch, "install_for", return_value=self.install)
        patched.start()
        self.addCleanup(patched.stop)
        self.addCleanup(self.window.files.mark_saved)

    def started_now(self):
        return _isolation.STARTED[self.started:]


class InstalledCollection:
    """What the Get dialog leaves behind once a collection is installed."""

    def __init__(self, folder: Path, name: str, *mods: str):
        self.collection = mock.Mock()
        self.collection.name = name
        self.placed = [folder / mod for mod in mods]
        self.present = list(mods)


# What the loader tests need, as a sentence.
LOADER = "the Anno Modding Tools extension for VS Code (its xmltest)"


# Set on a machine known to have neither the games nor the loader, which is
# CI: a test that needs one skips there instead of failing. tools/check.py
# --ci sets it, and lists every such skip in the job summary by what it
# needed, so the skip is still read.
SKIP_MISSING = "ANNO_STUDIO_SKIP_MISSING"
# How such a skip ends, which is how tools/check.py tells it from any other.
NOT_HERE = "which this machine does not have"


def missing(what: str):
    """Stop a test that needs ``what`` -- an installed game, the game's
    loader -- and did not find it: a failure that says so, or where
    ``ANNO_STUDIO_SKIP_MISSING`` is set, a skip that says so.

    Decision 5.5 of docs/proposals/consolidation-proposal.md: anyone
    changing the Studio has both games and the loader, and a skip is a pass
    nobody reads. These skipped, so a machine without them ran green over
    the tests that mattered most. CI cannot have them, and reads its skips.
    """
    if os.environ.get(SKIP_MISSING):
        raise unittest.SkipTest("needs %s, %s" % (what, NOT_HERE))
    raise AssertionError("This test needs %s, and it was not found. Install it and run again." % what)


def requires(found, what: str):
    """A test class or test that needs ``what``, stopped by :func:`missing`
    when ``found`` is not there."""
    def wrap(test):
        if found:
            return test
        if isinstance(test, type):
            test.setUpClass = classmethod(lambda _cls: missing(what))
            return test

        @functools.wraps(test)
        def refuse(*_args, **_kwargs):
            missing(what)

        return refuse

    return wrap


@unittest.skipUnless(os.name == "nt", "junctions are a Windows thing")
class LibraryFolders(unittest.TestCase):
    game = ANNO_1800

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.mods = self.root / "mods"
        self.mods.mkdir()
        self.library = self.root / "library" / self.game
        self.cache = self.root / "modio" / ("4169" if self.game == ANNO_1800 else "11358")
        for patched in (
            mock.patch.dict(os.environ, {
                modio_cache.ENV_ROOT: str(self.root / "modio"),
                library.ENV_LIBRARY: str(self.root / "library"),
            }),
            mock.patch.object(places, "backups_dir", return_value=self.root / "backups"),
            mock.patch.object(switches, "documents_mods", return_value=None),
        ):
            patched.start()
            self.addCleanup(patched.stop)

    def cached(self, mod_id: int, folder: str, modid: str, version: str = "1") -> Path:
        placed = install_mod(self.cache / "mods" / str(mod_id), folder, modid, version)
        state = self.cache / "metadata" / "state.json"
        state.parent.mkdir(parents=True, exist_ok=True)
        held = json.loads(state.read_text(encoding="utf-8")) if state.is_file() else {"Mods": []}
        held["Mods"].append({
            "ID": mod_id, "PathOnDisk": str(self.cache / "mods" / str(mod_id)),
            "Profile": {"name": folder, "modfile": {"id": mod_id * 10, "version": version}},
        })
        state.write_text(json.dumps(held), encoding="utf-8")
        return placed

    def survey(self, *layers) -> convert.Survey:
        loadout = Loadout(game=self.game)
        loadout.layers = list(layers)
        return convert.survey(truth.read(loadout, self.mods, library_dir=self.library))


# -- credentials --------------------------------------------------------------
def no_credentials():
    """A mod.io credential store with nothing in it, and nowhere to write.

    The Settings dialog says whether a key is saved, and the answer must come
    from a file this suite owns rather than from the developer's own -- a real
    machine has a real key in it, and no test should read or replace one.
    """
    from anno_modding_studio.core import secrets

    return secrets.Store(Path(tempfile.gettempdir()) / "ams-no-such-folder" / "modio")


# -- an iModYourAnno mod ------------------------------------------------------
IMYA_PATCH = "data/config/export/main/asset/assets.xml"


IMYA_TWEAKABLE = """<ModOps>
  <!-- the author's own note, which must survive being tweaked -->
  <ImyaTweaks Title="Radius tweaks" Description="How far things reach." />
  <ModOp Type="replace" GUID="1000" Path="/Values/Building/Radius" ModOpID="AlphaRadius">
    <Radius>20</Radius>
  </ModOp>
  <ImyaExpose Path="self::Radius" ModOpID="AlphaRadius" ExposeID="Alpha radius" Kind="Slider">
    <SliderDefinition Min="10" Max="40" Stepping="5" />
  </ImyaExpose>
  <Group ModOpID="BetaGroup">
    <ModOp Type="replace" GUID="1001" Path="/Values/Building/Radius">
      <Radius>8</Radius>
    </ModOp>
  </Group>
  <ImyaExpose Path="*" ModOpID="BetaGroup" ExposeID="Widen beta" Kind="SkipToggle" InitState="True" />
  <ModOp Type="replace" GUID="1000" Path="/Values/Building/BuildingType" ModOpID="AlphaType">
    <BuildingType>Factory</BuildingType>
  </ModOp>
  <ImyaExpose Path="self::BuildingType" ModOpID="AlphaType" ExposeID="Alpha type" Kind="Enum">
    <FixedValues><Value>Factory</Value><Value>Farm</Value></FixedValues>
  </ImyaExpose>
</ModOps>
"""


IMYA_RADIUS = IMYA_PATCH + imya.SEPARATOR + "Alpha radius"


def reverse_bytes(data: bytes) -> bytes:
    """A stand-in for DPAPI: anything that is not the identity."""
    return data[::-1]


def wait_until(holds, ms: int = 5000) -> bool:
    """Let the event loop run until ``holds()`` is true, or ``ms`` have gone:
    for a test that lets a timer of the window's fire, which PySide's QTest
    has no ``qWaitFor`` for.  Says whether it came true."""
    import time

    from PySide6.QtTest import QTest

    until = time.monotonic() + ms / 1000
    while not holds():
        if time.monotonic() > until:
            return False
        QTest.qWait(10)
    return True
