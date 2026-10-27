"""Write a file of every format the Studio keeps, as this build writes them.

    python tools/format_fixtures.py              into tests/fixtures/formats/<version>/
    python tools/format_fixtures.py --out DIR    somewhere else, to look at

From 0.1.0 on, what one release wrote is read by every later one, so each
release keeps a file of every format it writes: three loadouts (Anno 1800
with one profile and with two, and Anno 117) with the projects their mods
of yours are kept in, the library's records, a conversion's record, a
save's journal, a backup's manifest, the game updates remembered, and
``data-version.json``.  ``tests/test_formats.py`` reads every release's
files with the build under test, checks nothing in them is lost, and
checks that the format after each is refused.  The release checklist runs
this from the release build (docs/standards/release-checklist.md).

The loadouts hold what a user's files hold, each made the way the Studio
makes it: the Studio's ModOps for a change made in Fields, an op kept as
written, a patch's edits inside the ops of the mod it patches (an op of
yours among theirs, one of theirs switched off, one taken out, one held
over an update), checkpoints taken after them, a fork, and in Anno 117 the
own mod's Options block and a mod's settings.

Each file is written by the Studio's own writers, in a folder of its own that
the Studio is pointed at as its one folder, so nothing of the machine's real
data is read or written.  The folder's path is then written as
``C:\\fixture`` in every file, so no path of the machine it ran on is
committed.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
PLACEHOLDER = "C:\\fixture"


def _sandboxed(sandbox: Path) -> None:
    """Point every folder the Studio keeps at ``sandbox``."""
    for name in ("ANNO_STUDIO_HOME", "ANNO_STUDIO_CACHE", "ANNO_STUDIO_LIBRARY"):
        os.environ.pop(name, None)
    os.environ["ANNO_STUDIO_FOLDER"] = str(sandbox)


def _their_mod(folder: Path, name: str, game: str) -> None:
    """A mod's folder for the Studio to copy, as a fork does."""
    from anno_modding_studio.core import games

    patch = folder / games.get(game).asset_relpath
    patch.parent.mkdir(parents=True, exist_ok=True)
    patch.write_text("<ModOps/>\n", encoding="utf-8")
    (folder / "modinfo.json").write_text(json.dumps({"ModID": name.lower().replace(" ", "-")}) + "\n",
                                         encoding="utf-8")


def _loadout(sandbox: Path, profiles: int):
    from anno_modding_studio.core import (
        forking,
        held,
        histories,
        inside,
        narrow,
        patching,
        stale,
    )
    from anno_modding_studio.core.changes import (
        Change,
        Loadout,
        ModLayer,
        Op,
        Unread,
        own_record,
    )

    loadout = Loadout(own=own_record(name="Fixture Mod", creator="Fixture", mod_id="fixture-mod"),
                      game="anno1800", own_mod=True, is_the_mod=True, aim="make")
    # Installed from a collection before it was read in.
    loadout.provenance = {"Their Mod": "Fixture collection"}
    loadout.listed = {"Their Mod": ["Fixture collection"]}
    mods = sandbox / "mods"
    folder = mods / "Their Mod"
    loadout.set_layer(ModLayer(origin="Their Mod", name="Their Mod", folder=str(folder),
                               version="1.2", mod_id="their-mod", order=0,
                               copies={"Their Mod/[Shared] Piece": "Other Mod/[Shared] Piece"}))
    loadout.set_op(Op(expression="//Values/Trader", origin="Their Mod", searches=True,
                      source='<ModOp Type="merge" Path="//Values/Trader"><Trader/></ModOp>',
                      summary="merge Trader", order=0))
    loadout.unread.append(Unread(origin="Their Mod", source="<ModOp Type=\"odd\"/>",
                                 label="odd", why="not read", order=1))
    loadout.set_change(Change(guid="1010", asset_label="Bakery", path="/Values/Building/Upkeep",
                              tag="Upkeep", old_value="10", new_value="5"))
    loadout.set_change(Change(guid="1010", asset_label="Bakery", path="/Values/Cost/Gold",
                              tag="Gold", old_value="100", new_value="90", origin="Their Mod",
                              authored=False, order=2))
    loadout.checkpoint("Exported v1.0", kind="export")
    histories.take(loadout, "Exported v1.0", kind="export")
    loadout.builds[""] = {"build": 1, "digest": "d" * 40, "assets": "a" * 40, "update": "1.0"}
    loadout.modio_mod_id = 4243
    # An op of the own mod's, typed and kept as written, with what it comes to.
    kept = Op(expression="/Values/Building", origin="", authored=True, order=1,
              source='<ModOp Type="merge" GUID="1010" Path="/Values/Building"><Radius>8</Radius></ModOp>',
              summary="merge Building", kept="kept as written: typed in the XML view")
    loadout.set_op(kept)
    loadout.set_change(Change(guid="1010", asset_label="Bakery", path="/Values/Building/Radius",
                              tag="Radius", old_value="6", new_value="8", op="merge", op_uid=kept.uid,
                              order=1))
    # The Studio's ModOps for the change made in Fields.
    narrow.write(loadout)
    # A patch, which is kept in a project of its own like the loadout's own
    # mod, with its edits inside the ops of the mod it patches.
    patch = patching.make(loadout, "Their Mod", "Their Mod (my patch)")
    patch.creator, patch.mod_id = "Fixture", "fixture-their-patch"
    theirs = [Op(source='<ModOp Type="replace" GUID="1010" Path="/Values/Standard/Name">'
                        '<Name>Bakery</Name></ModOp>', origin="Their Mod", order=3),
              Op(source='<ModOp Type="remove" GUID="1011" />', origin="Their Mod", order=4),
              Op(source='<ModOp Type="remove" GUID="1012" />', origin="Their Mod", order=5),
              Op(source='<ModOp Type="remove" GUID="1013" />', origin="Their Mod", order=6)]
    for op in theirs:
        loadout.set_op(op)
    loadout.editing = patch.origin
    # An op of yours among theirs, with what it comes to.
    mine = Op(source='<ModOp Type="replace" GUID="1010" Path="/Values/Standard/Name">'
                     '<Name>Better Bakery</Name></ModOp>', expression="/Values/Standard/Name",
              origin="Their Mod", authored=True, order=3, within=[])
    loadout.ops.insert(loadout.ops.index(theirs[0]) + 1, mine)
    loadout.set_change(Change(guid="1010", asset_label="Bakery", path="/Values/Standard/Name",
                              tag="Name", old_value="Bakery", new_value="Better Bakery",
                              origin="Their Mod", op_uid=mine.uid, order=3))
    # One of theirs switched off, and two taken out: one stays out, and
    # the other's op is gone from an update, so the edit is held.
    theirs[1].active = False
    loadout.drop_op(theirs[2].uid)
    loadout.drop_op(theirs[3].uid)
    inside.assign(loadout)
    stocks = held.stock(loadout, ["Their Mod"])
    held.settle(loadout, stocks, {"Their Mod": [op.source for op in loadout.ops if op.origin == "Their Mod"]
                                  + [theirs[2].source]})
    loadout.taken_out = [entry for entry in loadout.taken_out if entry[1] != theirs[3].source]
    loadout.editing = ""
    # Checkpoints after all that, of both mods of yours.
    loadout.checkpoint("Before the patch", origin="")
    loadout.checkpoint("Exported v1.0", kind="export", origin=patch.origin)
    histories.take(loadout, "With the patch")
    # A fork of another mod, its files copied beside the library.
    other = mods / "Other Mod"
    _their_mod(other, "Other Mod", "anno1800")
    loadout.set_layer(ModLayer(origin="Other Mod", name="Other Mod", folder=str(other), creator="Dee",
                               version="2.0", mod_id="other-mod", order=2))
    forking.make(loadout, "Other Mod", sandbox / "library" / "anno1800")
    loadout.read_stack = stale.stack(loadout)
    for number in range(1, profiles):
        loadout.add_profile("Profile %d" % (number + 1))
    return loadout


def _loadout_117(sandbox: Path):
    """An Anno 117 loadout: the own mod's modinfo blocks, and a mod read at
    settings of its own."""
    from anno_modding_studio.core import narrow
    from anno_modding_studio.core.changes import Change, Loadout, ModLayer, own_record

    loadout = Loadout(own=own_record(name="Fixture Roman Mod", creator="Fixture", mod_id="fixture-roman",
                                     guid_range="Fixture", loads_last=True),
                      game="anno117", own_mod=True, is_the_mod=True, aim="make")
    loadout.options = {"roomier": {"type": "toggle", "default": "false", "label": "Roomier"}}
    loadout.scripts = {"Modules": [{"Path": "data/lua/fixture.lua"}]}
    folder = sandbox / "mods117" / "Their Roman Mod"
    loadout.set_layer(ModLayer(origin="Their Roman Mod", name="Their Roman Mod", folder=str(folder),
                               version="1.0", mod_id="their-roman", order=0, options={"hard": "1"},
                               offered={"hard": {"type": "toggle", "default": "0"}}))
    loadout.set_change(Change(guid="2001000", asset_label="Mill", path="/Values/Building/Upkeep",
                              tag="Upkeep", old_value="10", new_value="5"))
    narrow.write(loadout)
    loadout.checkpoint("by hand")
    return loadout


def write(out: Path) -> None:
    sandbox = Path(tempfile.mkdtemp(prefix="ams-fixtures-"))
    try:
        _sandboxed(sandbox)
        from anno_modding_studio.core import (
            backup,
            convert,
            dataversion,
            gameversion,
            library,
            places,
            projects,
            records,
            saving,
        )

        made = sandbox / "made"
        made.mkdir()
        _loadout(sandbox, 1).save(made / "loadout-one-profile.annoload")
        _loadout(sandbox, 2).save(made / "loadout-profiles.annoload")
        _loadout_117(sandbox).save(made / "loadout-anno117.annoload")
        shutil.copytree(str(places.projects_dir()), str(made / "projects"))

        lib = made / "library"
        mod = lib / "Their Mod"
        library.record(lib, mod.name, source=library.ADOPTED, version="1.2")
        library.record(lib, "From mod.io", source=library.MODIO, modio_id=4242, modfile_id=7,
                       version="2.0", name="From mod.io")
        # A copy built from a project, renamed once since it was first built.
        library.record_built(lib, "[Gameplay] Fixture Mod",
                             project=projects.as_written(places.projects_dir() / "Fixture Mod.annoproj"),
                             mod_id="fixture-mod", build=1)
        library.record(lib, "[Gameplay] Fixture Mod", **{library.WAS: ["[Gameplay] Fixture"]})
        library.write_links(lib, {mod.name: {
            "target": str(mod), "kind": library.COPY,
            "mods_folder": str(sandbox / "mods"), "linked": True,
        }})
        records.write(lib / library.REFERENCES, {"Working copy": str(sandbox / "checkout")},
                      library.RECORD_FORMAT, entry=str)
        library.set_live(lib, made / "loadout-profiles.annoload", at=1760000000.0,
                         fingerprint="f" * 40, profile="Main")

        record = {"when": 1760000000.0, "game": "anno1800", "loading": ["their-mod"],
                  "names": {"their-mod": "Their Mod"}, "on": {"their-mod": True}, "options": {}}
        kept = convert.save_snapshot(record, lib)
        record["journal"] = {
            "mods_folder": str(sandbox / "mods"), "backups": [], "loadout": "", "paths": {},
            "profiles": [], "full_copy": "",
            "steps": [{"op": "move", "from": str(sandbox / "mods" / "Their Mod"),
                       "to": str(mod), "link": str(sandbox / "mods" / "Their Mod")}],
        }
        convert.write_record(kept, record)

        target = made / "loadout-profiles.annoload"
        journal = saving.write_journal([saving.Entry(
            target=target, part=target.with_name(target.name + ".1-fixture.part"),
            old=target.with_name(target.name + ".1-fixture.old"), existed=True,
        )], "1-fixture")
        (made / saving.JOURNALS).mkdir()
        shutil.copy2(str(journal), str(made / saving.JOURNALS))

        mods = sandbox / "mods"
        patch = mods / "Their Mod" / "data/config/export/main/asset/assets.xml"
        patch.parent.mkdir(parents=True)
        patch.write_text("<ModOps/>\n", encoding="utf-8")
        (mods / "Their Mod" / "modinfo.json").write_text('{"ModID": "their-mod"}\n', encoding="utf-8")
        save = backup.take(mods, ["Their Mod"], into=sandbox / "backups", label="before exporting",
                           game="anno1800", writing=["data/config/export/main/asset/new.xml"])
        shutil.copytree(str(save.root), str(made / "backup"))

        gameversion.remember("e" * 40, gameversion.Build(
            built="Oct 1 2026", version="1.0.0.1.2", update="1.0.0", store="Steam",
            branch="main", line=""))
        shutil.copy2(str(places.extracted_dir() / gameversion.REMEMBERED_NAME), str(made))

        dataversion.record()
        shutil.copy2(str(places.local_home() / dataversion.FILE_NAME), str(made))

        spelled = {str(sandbox), str(sandbox).replace("\\", "/")}
        escaped = {json.dumps(text)[1:-1] for text in spelled}
        for path in made.rglob("*"):
            if path.is_file() and path.suffix in (".json", ".annoload", ".annoproj"):
                text = path.read_text(encoding="utf-8")
                for spelling in sorted(escaped | spelled, key=len, reverse=True):
                    text = text.replace(spelling, json.dumps(PLACEHOLDER)[1:-1])
                path.write_text(text, encoding="utf-8", newline="\n")
        if out.exists():
            shutil.rmtree(str(out))
        shutil.copytree(str(made), str(out))
    finally:
        shutil.rmtree(str(sandbox), ignore_errors=True)


def main(argv=None) -> int:
    from anno_modding_studio import __version__

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args(argv)
    out = args.out or ROOT / "tests" / "fixtures" / "formats" / __version__
    started = time.time()
    write(out)
    print("wrote %s in %.1f s" % (out, time.time() - started))
    return 0


if __name__ == "__main__":
    sys.exit(main())
