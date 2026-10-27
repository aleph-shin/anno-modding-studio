"""Anno 1800's activation file: ``mods/activation.json`` listing mods to skip
by ModID, or by folder name for a mod that declares none."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

import _isolation  # noqa: F401 -- first: keeps the suite out of real files under any runner

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from anno_modding_studio.core import switches  # noqa: E402
from anno_modding_studio.core.games import ANNO_117, ANNO_1800  # noqa: E402


class TestActivationFile(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.mods = Path(self._tmp.name) / "mods"
        for name in ("Ships", "-Farms", "Trade", "Host", "Host/Inner"):
            (self.mods / name).mkdir(parents=True)

    def activation(self, *ids, extra=None):
        data = dict(extra or {})
        data["disabledIds"] = list(ids)
        (self.mods / "activation.json").write_text(json.dumps(data), encoding="utf-8")

    def read(self):
        return switches.read(
            self.mods, ANNO_1800,
            ids={"Ships": "ships-mod", "Farms": "", "Trade": "trade-routes", "Host": "host"},
            names=["Ships", "Farms", "Trade", "Host", "Host/Inner"],
        )

    def test_a_listed_mod_is_off_by_modid_or_by_folder(self):
        self.activation("Ships-Mod", "Trade", "Farms")
        found = self.read()
        # Ships by its ModID, in any case; Trade's folder name is not its
        # ModID, so that entry does nothing; Farms declares none, so its folder
        # name is what the file names.
        self.assertEqual(found.on, {"Ships": False, "Farms": False, "Trade": True,
                                    "Host": True, "Host/Inner": True})
        self.assertEqual(set(found.disabled), {"Ships", "Farms"})

    def test_a_bundled_mod_goes_with_its_host(self):
        self.activation("host")
        found = self.read()
        self.assertFalse(found.on["Host"])
        self.assertFalse(found.on["Host/Inner"])

    def test_without_one_nothing_changes_and_117_has_none(self):
        self.assertEqual(self.read().disabled, {})
        self.activation("ships-mod")
        self.assertEqual(switches.activation_files(ANNO_117, self.mods), [])

    def test_switched_on_it_comes_out_of_the_file_and_the_rest_stays(self):
        self.activation("ships-mod", "farms", extra={"note": "mine"})
        found = self.read()
        written = switches.write(found, {"Ships": True, "Farms": True},
                                ids={"Ships": "ships-mod", "Farms": ""})
        data = json.loads((self.mods / "activation.json").read_text(encoding="utf-8"))
        self.assertEqual(data, {"note": "mine", "disabledIds": []})
        # Farms was off twice over, so its folder is renamed as well.
        self.assertTrue((self.mods / "Farms").is_dir())
        self.assertEqual([mod for mod, _ in written.unlisted], ["Farms", "Ships"])
        self.assertIn("took 2 mods out of activation.json", written.describe())
        self.assertEqual(self.read().off, [])

    def test_switched_off_it_is_the_folder_mark_as_before(self):
        self.activation()
        written = switches.write(self.read(), {"Trade": False})
        self.assertTrue((self.mods / "-Trade").is_dir())
        self.assertEqual(written.unlisted, [])


sys.path.insert(0, str(Path(__file__).resolve().parent))

from support import TruthFolders, install_mod  # noqa: E402


class TestTheCard(TruthFolders):
    def test_a_mod_the_documents_file_lists_does_not_load(self):
        from anno_modding_studio.core.changes import ModLayer

        install_mod(self.mods, "[Gameplay] Mine", "mine", "1")
        install_mod(self.mods, "[Gameplay] Skipped", "skipped", "1")
        (self.documents / "activation.json").write_text('{"disabledIds": ["SKIPPED"]}', encoding="utf-8")
        found = self.read(ModLayer(origin="[Gameplay] Mine", mod_id="mine"))
        self.assertEqual([mod.name for mod in found.loading], ["[Gameplay] Mine"])


if __name__ == "__main__":
    unittest.main()
