"""Suggesting the next version from what changed since the last release."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

import _isolation  # noqa: F401 -- first: keeps the suite out of real files under any runner

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from anno_modding_studio.core import releases  # noqa: E402
from anno_modding_studio.core.changes import (  # noqa: E402
    KIND_EXPORT,
    KIND_PUBLISH,
    OP_ADD,
    OP_ADD_NEXT,
    Change,
    Loadout,
    ModLayer,
    own_record,
)
from anno_modding_studio.core.versioning import MAJOR, MINOR, PATCH  # noqa: E402

HUT = "1010340"
FARM = "1010030"


def _value(value: str, guid: str = HUT, label: str = "Lumberjack's Hut", origin: str = "") -> Change:
    return Change(
        guid=guid, asset_label=label, path="/Values/Factory7/CycleTime", tag="CycleTime",
        old_value="30", new_value=value, origin=origin,
    )


def _asset(guid: str, name: str, uid: str = "") -> Change:
    payload = (
        "<Asset><Template>Factory7</Template><Values><Standard>"
        "<GUID>%s</GUID><Name>%s</Name></Standard></Values></Asset>" % (guid, name)
    )
    return Change(
        guid=HUT, asset_label="Lumberjack's Hut", path="", tag="Asset",
        op=OP_ADD_NEXT, payload=payload, uid=uid or "new-%s" % guid,
    )


def _released(loadout: Loadout, kind: str = KIND_EXPORT) -> None:
    origin = loadout.home or ""
    loadout.checkpoint("Exported v%s" % loadout.version_of(origin), kind=kind, origin=origin)


class LastReleaseTest(unittest.TestCase):
    def test_none_before_the_first(self):
        loadout = Loadout(own=own_record(version="1.0"))
        loadout.checkpoint("Saved by hand")
        self.assertIsNone(releases.last_release(loadout))
        self.assertIsNone(releases.suggest(loadout))

    def test_the_last_export(self):
        loadout = Loadout(own=own_record(version="1.0"))
        _released(loadout)
        loadout.own.version = "1.1.0"
        _released(loadout)
        loadout.checkpoint("Saved by hand")
        release = releases.last_release(loadout)
        self.assertEqual((release.version, release.published), ("1.1.0", False))

    def test_what_was_published_wins_over_later_exports(self):
        loadout = Loadout(own=own_record(version="1.2.0"))
        _released(loadout, KIND_PUBLISH)
        sent = loadout.history_of("")[-1]
        loadout.published = {"version": "1.2.0", "checkpoint": sent.timestamp}
        loadout.own.version = "1.3.0"
        loadout.history_of("").append(
            type(sent)(timestamp=sent.timestamp + 5, label="Exported v1.3.0", version="1.3.0", kind=KIND_EXPORT)
        )
        release = releases.last_release(loadout)
        self.assertEqual((release.version, release.published), ("1.2.0", True))

    def test_a_release_is_looked_for_in_the_mods_own_history(self):
        """Measured against the project's history (section 9 of the
        projects-and-loadouts proposal): another mod's export, or the
        loadout's own list, is no release of this one."""
        loadout = Loadout(own=own_record(version="1.0"), own_mod=True)
        loadout.set_layer(ModLayer(origin="Patch", patches="Theirs", version="2.0"))
        loadout.checkpoint("Exported v2.0", kind=KIND_EXPORT, origin="Patch")
        self.assertIsNone(releases.last_release(loadout))
        self.assertEqual(releases.last_release(loadout, "Patch").version, "2.0")

    def test_a_release_made_in_another_loadout_counts(self):
        """The history is kept in the project, so a loadout that opens it
        measures from the release whichever loadout made it."""
        loadout = Loadout(own=own_record(name="Mine", version="1.0"), own_mod=True)
        loadout.set_change(_value("15"))
        with tempfile.TemporaryDirectory() as folder:
            first = Path(folder) / "loadouts" / "First.annoload"
            loadout.save(first)
            loadout.checkpoint("Exported v1.0", kind=KIND_EXPORT, loadout="First")
            loadout.save(first)
            data = json.loads(first.read_text(encoding="utf-8"))
            self.assertNotIn("mod_history", data)
            second = Path(folder) / "loadouts" / "Second.annoload"
            second.write_text(json.dumps(data), encoding="utf-8")
            loaded = Loadout.load(second)
        release = releases.last_release(loaded)
        self.assertEqual((release.version, release.checkpoint.loadout), ("1.0", "First"))


class ClassifyTest(unittest.TestCase):
    def test_nothing_changed(self):
        found = releases.classify([_value("15")], [_value("15")])
        self.assertIsNone(found.level)
        self.assertTrue(found.covered)

    def test_a_value_retuned_is_a_patch(self):
        found = releases.classify([_value("15")], [_value("12")])
        self.assertEqual(found.level, PATCH)
        self.assertEqual(found.reasons, ["1 value changed on 1 asset: Lumberjack's Hut."])

    def test_an_asset_not_changed_before_is_minor(self):
        found = releases.classify([_value("15")], [_value("15"), _value("9", FARM, "Farmer Residence")])
        self.assertEqual(found.level, MINOR)
        self.assertIn("Farmer Residence", found.reasons[0])

    def test_a_new_asset_is_minor_and_its_own_edits_are_part_of_it(self):
        after = [_value("15"), _asset("1500012345", "Roman Bakery"), _value("4", "1500012345", "Roman Bakery")]
        found = releases.classify([_value("15")], after)
        self.assertEqual(found.level, MINOR)
        self.assertEqual(found.reasons, ["1 new asset: Roman Bakery (1500012345)."])

    def test_an_asset_it_added_gone_is_major(self):
        before = [_value("15"), _asset("1500012345", "Roman Bakery")]
        found = releases.classify(before, [_value("12")])
        self.assertEqual(found.level, MAJOR)
        self.assertTrue(found.reasons[0].startswith("An asset this mod added is gone: Roman Bakery (1500012345)."))
        # The retune is still said, after the reason that decided it.
        self.assertIn("1 value changed", found.reasons[-1])

    def test_an_added_asset_switched_off_is_gone_too(self):
        bakery = _asset("1500012345", "Roman Bakery")
        off = _asset("1500012345", "Roman Bakery", uid=bakery.uid)
        off.active = False
        self.assertEqual(releases.classify([bakery], [off]).level, MAJOR)

    def test_a_new_guid_for_an_added_asset_is_major(self):
        before = _asset("1500012345", "Roman Bakery", uid="bakery")
        after = _asset("1500099999", "Roman Bakery", uid="bakery")
        found = releases.classify([before], [after])
        self.assertEqual(found.level, MAJOR)
        self.assertEqual(found.added, ["Roman Bakery (1500099999)"])

    def test_retuning_an_added_asset_in_place_is_a_patch(self):
        before = _asset("1500012345", "Roman Bakery", uid="bakery")
        after = _asset("1500012345", "Roman Bakery", uid="bakery")
        after.payload = after.payload.replace("Factory7", "Factory8")
        self.assertEqual(releases.classify([before], [after]).level, PATCH)

    def test_a_node_added_is_minor(self):
        added = Change(guid=HUT, asset_label="Lumberjack's Hut", path="/Values/Cost/Costs", tag="Item",
                       op=OP_ADD, payload="<Item><Ingredient>1010196</Ingredient></Item>")
        found = releases.classify([_value("15")], [_value("15"), added])
        self.assertEqual(found.level, MINOR)
        self.assertEqual(found.reasons, ["1 node added to or taken out of assets."])

    def test_a_change_taken_out_is_minor(self):
        found = releases.classify([_value("15"), _value("9", FARM, "Farmer Residence")], [_value("15")])
        self.assertEqual(found.level, MINOR)
        self.assertEqual(found.reasons, ["1 change taken out or switched off."])


class SuggestionTest(unittest.TestCase):
    def _loadout(self) -> Loadout:
        loadout = Loadout(own=own_record(version="1.2.0"))
        loadout.set_change(_value("15"))
        _released(loadout)
        return loadout

    def test_the_version_it_calls_for_and_whether_it_is_met(self):
        loadout = self._loadout()
        loadout.set_change(_value("9", FARM, "Farmer Residence"))
        found = releases.suggest(loadout)
        self.assertEqual((found.since, found.level, found.version), ("1.2.0", MINOR, "1.3.0"))
        self.assertFalse(found.covered)
        self.assertEqual(
            releases.headline(found),
            "Since 1.2.0: a minor change (adds something), so the next version is 1.3.0.",
        )
        loadout.own.version = "1.2.1"
        self.assertFalse(releases.suggest(loadout).covered)
        loadout.own.version = "1.3.0"
        self.assertEqual(
            releases.headline(releases.suggest(loadout)),
            "Since 1.2.0: a minor change (adds something). 1.3.0 covers it.",
        )
        loadout.own.version = "2.0.0"
        self.assertTrue(releases.suggest(loadout).covered)

    def test_a_version_written_without_its_last_zero_meets_it(self):
        """``1.3`` is the ``1.3.0`` a minor change calls for, as the Studio's
        own update check also counts it."""
        loadout = self._loadout()
        loadout.set_change(_value("9", FARM, "Farmer Residence"))
        loadout.own.version = "1.3"
        found = releases.suggest(loadout)
        self.assertTrue(found.met)
        self.assertEqual(
            releases.headline(found),
            "Since 1.2.0: a minor change (adds something). 1.3 covers it.",
        )

    def test_nothing_since_says_so(self):
        found = releases.suggest(self._loadout())
        self.assertEqual(releases.headline(found), "No changes since 1.2.0.")
        self.assertEqual(releases.short(found), "No changes since 1.2.0.")

    def test_the_short_line_for_the_export_tab(self):
        loadout = self._loadout()
        loadout.set_change(_value("9", FARM, "Farmer Residence"))
        self.assertEqual(releases.short(releases.suggest(loadout)), "Minor change since 1.2.0 (adds something).")
        loadout.own.version = "1.3.0"
        self.assertEqual(releases.short(releases.suggest(loadout)), "Minor change since 1.2.0. 1.3.0 covers it.")

    def test_a_version_it_cannot_judge_is_not_said_to_cover_it(self):
        """Cleared to retype, or typed without numbers: not judged, so not said to be enough."""
        loadout = self._loadout()
        loadout.set_change(_value("9", FARM, "Farmer Residence"))
        for typed in ("", "beta"):
            loadout.own.version = typed
            found = releases.suggest(loadout)
            self.assertEqual(
                releases.headline(found),
                "Since 1.2.0: a minor change (adds something), so the next version is 1.3.0.",
            )
            self.assertEqual(releases.short(found), "Minor change since 1.2.0 (adds something).")

    def test_a_release_not_numbered_gets_a_kind_but_no_number(self):
        loadout = Loadout(own=own_record(version="v2"))
        loadout.set_change(_value("15"))
        _released(loadout)
        loadout.set_change(_value("12"))
        found = releases.suggest(loadout)
        self.assertEqual((found.level, found.version, found.covered), (PATCH, "", True))
        self.assertEqual(releases.headline(found), "Since v2: a patch change (tunes or fixes).")

    def test_only_the_mod_it_releases_counts(self):
        loadout = self._loadout()
        loadout.set_layer(ModLayer(origin="[Gameplay] Theirs ~ patch", patches="[Gameplay] Theirs"))
        loadout.set_change(_value("1", FARM, "Farmer Residence", origin="[Gameplay] Theirs ~ patch"))
        self.assertIsNone(releases.suggest(loadout).level)

    def test_a_mod_of_yours_read_back_in_is_measured_by_its_own_version(self):
        loadout = Loadout(own=own_record(version="1.0"))
        loadout.set_layer(ModLayer(origin="[Gameplay] Mine", version="3.4.0"))
        loadout.home = "[Gameplay] Mine"
        loadout.set_change(_value("15", origin="[Gameplay] Mine"))
        _released(loadout)
        self.assertEqual(loadout.history_of("[Gameplay] Mine")[-1].version, "3.4.0")
        loadout.set_change(_value("12", origin="[Gameplay] Mine"))
        found = releases.suggest(loadout)
        self.assertEqual((found.since, found.current, found.version), ("3.4.0", "3.4.0", "3.4.1"))

    def test_the_changelog_draft(self):
        loadout = self._loadout()
        loadout.set_change(_value("12"))
        loadout.set_change(_asset("1500012345", "Roman Bakery"))
        self.assertEqual(
            releases.changelog(releases.suggest(loadout)),
            "Added: Roman Bakery (1500012345).\nChanged 1 value on Lumberjack's Hut.",
        )
        self.assertEqual(releases.changelog(None), "")

    def test_the_changelog_says_every_kind(self):
        before = [_value("15"), _value("9", FARM, "Farmer Residence")]
        added = Change(guid=HUT, asset_label="Lumberjack's Hut", path="/Values/Cost/Costs", tag="Item",
                       op=OP_ADD, payload="<Item/>")
        found = releases.classify(before, [_value("15"), added, _value("2", "77", "Market")])
        self.assertEqual(
            releases.changelog(found).splitlines(),
            ["Now also changes: Market.", "Added or removed 1 entry in assets.", "Took out 1 change."],
        )


if __name__ == "__main__":
    unittest.main()
