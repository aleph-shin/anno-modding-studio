"""How to build each dialog, for the design checks and the theme renders.

``tests/test_design.py`` walks every widget of every dialog here, and
``tools/render_themes.py`` takes a picture of each. A factory takes the main
window and returns the dialog, built and not shown. It does not say what
opens the dialog, so the walk cannot check that the focus goes back there
when Esc closes it.

``WAITING`` names the dialogs with no factory yet. Each needs more of a
scenario than a bare window gives it: a read mods folder, a mod.io answer, a
conversion survey. Each gets its factory when its area is rewritten (Stage 2
of docs/proposals/design-review-proposal.md). A dialog class that is in
neither list fails ``test_design``, so a new one cannot be left out.
"""

from __future__ import annotations

import tempfile
from pathlib import Path


def _settings(window):
    from anno_modding_studio.core import secrets
    from anno_modding_studio.ui.settings_dialog import SettingsDialog

    # An empty credential store of its own: this reads nobody's mod.io key.
    store = secrets.Store(Path(tempfile.mkdtemp(prefix="ams-no-key-")) / "modio")
    return SettingsDialog(window, window, store=store)


def _conflicts(window):
    from anno_modding_studio.ui.conflicts_dialog import ConflictsDialog

    return ConflictsDialog(window.loadout, window, window.index)


def _history(window):
    from anno_modding_studio.ui.history_dialog import HistoryDialog

    return HistoryDialog(window.loadout, window)


def _import(window):
    from anno_modding_studio.ui.import_dialog import ImportDialog

    return ImportDialog(window)


def _add_to_loadout(window):
    """With What I play now… offered, which is the dialog and a hint and a
    button more: none of the loadouts is a set of mods to play."""
    from pathlib import Path

    from anno_modding_studio.ui.add_to_loadout_dialog import AddToLoadoutDialog, Row

    return AddToLoadoutDialog("Faster Lumberjacks", [
        Row(Path("Better Harbours.annoload"), "Better Harbours", True, False, []),
        Row(Path("Test.annoload"), "Test", False, False, ["Better Harbours"]),
    ], window, offer_play_now=True)


def _unload(window):
    from anno_modding_studio.ui.unload_dialog import UnloadModsDialog

    return UnloadModsDialog(window.loadout, window)


def _unread(window):
    from anno_modding_studio.ui.unread_dialog import UnreadDialog

    return UnreadDialog(window.loadout, window, window)


def _held(window):
    from anno_modding_studio.ui.held_dialog import HeldDialog

    return HeldDialog(window.loadout, window)


def _new_module(window):
    from anno_modding_studio.ui.scripts_dialog import NewModuleDialog

    return NewModuleDialog("better_bricks", "Better Bricks", ((), ()), window)


def _link(window):
    from anno_modding_studio.ui.modio_publish_dialog import LinkDialog

    return LinkDialog([], 0, window)


def _backup(window):
    from anno_modding_studio.ui.backup_dialog import BackupDialog

    return BackupDialog(Path(tempfile.mkdtemp(prefix="ams-mods-")), window)


def _terms(window):
    from anno_modding_studio.core import modio
    from anno_modding_studio.ui.modio_publish_dialog import TermsDialog

    return TermsDialog(modio.Terms(text="The terms, in mod.io's own words."), window)


def _publish(window):
    from anno_modding_studio.core import modio, publish
    from anno_modding_studio.ui.modio_publish_dialog import PublishDialog

    ready = publish.Plan(
        mod=modio.ModSummary(id=9, name="Better Bricks", version="1.0.0"),
        archive=Path("Better Bricks.zip"), version="1.1.0", size=20480, md5="",
        as_user="bricklayer", changelog="Artisans house five more.",
    )
    return PublishDialog(ready, window, note="Adds something: a minor version.")


def _reference_list(window):
    from anno_modding_studio.ui.reference_panel import ListedAsset, ReferenceListDialog

    rows = [
        ListedAsset(guid="1010032", label="Artisan Residence  (residence_tier03)",
                    template="ResidenceBuilding7", through="Costs > Item > Ingredient"),
        ListedAsset(guid="1010283", label="Brick Factory  (factory_brick)",
                    template="FactoryBuilding7", through="FactoryBase > FactoryOutputs"),
    ]
    return ReferenceListDialog(rows, "2 assets that point at Bricks  (brick)", window)


def _mod_import(window):
    from anno_modding_studio.ui.mod_import_dialog import ModImportDialog

    return ModImportDialog(window.index, window)


def _mods_folder(window):
    from anno_modding_studio.ui.mods_folder_dialog import ModsFolderDialog

    return ModsFolderDialog(window.index, window)


def _mod_options(window):
    from anno_modding_studio.core import settings
    from anno_modding_studio.ui.mod_options_dialog import ModOptionsDialog

    offered = settings.read({
        "Houses": {"type": "enum", "label": "Houses", "default": "----", "values": ["----"]},
        "Roomier": {"type": "toggle", "label": "Artisans house five more", "default": "true",
                    "description": "Raises every artisan residence by five."},
        "Bonus": {"type": "enum", "label": "How many more", "default": "5",
                  "values": ["5", "10"], "labels": ["Five", "Ten"]},
    })
    return ModOptionsDialog("Better Bricks", offered, None, window)


def _scripts(window):
    from anno_modding_studio.ui.scripts_dialog import ScriptsDialog

    return ScriptsDialog(window.loadout, "", "better-bricks", window)


def _options_editor(window):
    from anno_modding_studio.ui.options_editor_dialog import OptionsEditorDialog

    window.loadout.options = {
        "roomier": {"label": "Artisans house five more", "type": "toggle", "default": "true"},
        "bonus": {"label": "How many more", "type": "enum", "default": "5",
                  "values": ["5", "10"], "labels": ["Five", "Ten"]},
    }
    # One proposed, as an Apply that read an undeclared setting opens it.
    return OptionsEditorDialog(window.loadout, "", "better-bricks", window, ["cheaper"],
                               after_apply=True)


def _option_group(window):
    from anno_modding_studio.ui.option_group_dialog import OptionGroupDialog

    window.loadout.options = {
        "ceres": {"label": "Shrine of Ceres", "type": "toggle", "default": "true"},
    }
    return OptionGroupDialog(window.loadout, "", 3, 2, window)


def _convert(window):
    from anno_modding_studio.core import convert
    from anno_modding_studio.ui.convert_dialog import ConvertDialog

    return ConvertDialog(
        convert.Survey(game=window.loadout.game), window,
        full_size=2_400_000_000, full_free=237_000_000_000,
        full_folders=[Path("mods")], full_default=True,
    )


def _loadout_check(window):
    from anno_modding_studio.ui.loadout_check_dialog import LoadoutCheckDialog

    return LoadoutCheckDialog(None, window.index, None, window)


def _needs(window):
    from anno_modding_studio.core import dependencies
    from anno_modding_studio.ui.needs_dialog import NeedsDialog

    check = dependencies.Check(
        game=window.loadout.game,
        replaced=[("Quay System Full Corners (JDG_06_quay_system_fc)",
                   "Quay System Full Corners (JDG_06_quay_system_fc_2)")],
        missing=[("Object Dummies", "shared_NatureParticipant_Serp", False)],
        clashes=[("AI AIs at Peace (Serp)", "AI Allied AIs (Serp)")],
    )
    return NeedsDialog(check, window)


def _unsubscribe(window):
    from anno_modding_studio.ui.unsubscribe_dialog import UnsubscribeDialog

    return UnsubscribeDialog([], window)


def _modio_get(window):
    from anno_modding_studio.core import secrets
    from anno_modding_studio.ui.modio_get_dialog import ModioGetDialog

    # An empty credential store of its own, and nothing that reaches mod.io.
    store = secrets.Store(Path(tempfile.mkdtemp(prefix="ams-no-key-")) / "modio")
    folder = Path(tempfile.mkdtemp(prefix="ams-mods-"))
    return ModioGetDialog(window.loadout.game, store, folder, parent=window,
                          fetch=lambda *_args, **_more: None, library_dir=folder)


def _modio_updates(window):
    from anno_modding_studio.core import secrets
    from anno_modding_studio.core.changes import Loadout, ModLayer
    from anno_modding_studio.ui.modio_updates_dialog import ModioUpdatesDialog

    # A loadout of its own, an empty credential store and nothing that
    # reaches mod.io: the dialog asks nothing until Check is pressed.
    loadout = Loadout(game=window.loadout.game)
    for order, name in enumerate(("[Gameplay] Better Bricks", "[Visual] Quiet Streets",
                                  "[Economy] Harbour Tax"), start=1):
        loadout.set_layer(ModLayer(origin=name, name=name, version="1.0", order=order))
    store = secrets.Store(Path(tempfile.mkdtemp(prefix="ams-no-key-")) / "modio")
    folder = Path(tempfile.mkdtemp(prefix="ams-library-"))
    return ModioUpdatesDialog(loadout, window.loadout.game, store, window, logged={}, cache={},
                              fetch=lambda *_args, **_more: None, library_dir=folder)


def _only_copies(window):
    from anno_modding_studio.core import library
    from anno_modding_studio.ui.only_copies_dialog import OnlyCopiesDialog

    mods = [
        library.Held(name="[Gameplay] Better Bricks", link=Path("mods/[Gameplay] Better Bricks"),
                     target=Path("library/[Gameplay] Better Bricks"), mod_name="Better Bricks",
                     version="1.2.0", source=library.ZIP, zip="better-bricks-1.2.0.zip"),
        library.Held(name="[Visual] Quiet Streets", link=Path("mods/[Visual] Quiet Streets"),
                     target=Path("library/[Visual] Quiet Streets"), mod_name="Quiet Streets",
                     version="0.4", source=library.ADOPTED),
    ]
    unknown = [
        library.Held(name="[Economy] Harbour Tax", link=Path("mods/[Economy] Harbour Tax"),
                     target=Path("library/[Economy] Harbour Tax"), mod_name="Harbour Tax",
                     version="2.1"),
    ]
    return OnlyCopiesDialog([("Anno 1800", mods, unknown, [])], window)


def _shortcuts(window):
    from anno_modding_studio.ui.shortcuts_dialog import ShortcutsDialog

    return ShortcutsDialog(window)


def _about(window):
    from anno_modding_studio.ui.about_dialog import AboutDialog

    return AboutDialog(window)


def _folders(window):
    from anno_modding_studio.ui.folders_dialog import FoldersDialog

    return FoldersDialog(window.where.places(), window.loadout.game, window)


def _xml(window):
    from anno_modding_studio.ui.xml_view import XmlDialog

    return XmlDialog(
        "Lumberjack's Hut in Their Mod, as the Studio writes it",
        '<ModOps>\n  <ModOp Type="remove" GUID="1010340" Path="/Values/Cost"></ModOp>\n</ModOps>\n',
        parent=window,
    )


DIALOGS = {
    "settings": _settings,
    "conflicts": _conflicts,
    "history": _history,
    "import": _import,
    "unload": _unload,
    "unread": _unread,
    "new_module": _new_module,
    "modio_link": _link,
    "backup": _backup,
    "modio_terms": _terms,
    "modio_publish": _publish,
    "reference_list": _reference_list,
    "mod_import": _mod_import,
    "mods_folder": _mods_folder,
    "mod_options": _mod_options,
    "scripts": _scripts,
    "options_editor": _options_editor,
    "option_group": _option_group,
    "convert": _convert,
    "loadout_check": _loadout_check,
    "needs": _needs,
    "unsubscribe": _unsubscribe,
    "modio_get": _modio_get,
    "only_copies": _only_copies,
    "modio_updates": _modio_updates,
    "shortcuts": _shortcuts,
    "about": _about,
    "folders": _folders,
    "xml": _xml,
    "held": _held,
    "add_to_loadout": _add_to_loadout,
}

# The class each factory builds, and the classes still waiting for one.
BUILT = (
    "SettingsDialog", "ConflictsDialog", "HistoryDialog", "ImportDialog",
    "UnloadModsDialog", "UnreadDialog", "NewModuleDialog", "LinkDialog",
    "BackupDialog", "TermsDialog", "PublishDialog",
    "ReferenceListDialog", "ModImportDialog", "ModsFolderDialog", "ModOptionsDialog",
    "ScriptsDialog", "OptionsEditorDialog", "OptionGroupDialog", "ConvertDialog", "LoadoutCheckDialog", "NeedsDialog", "UnsubscribeDialog",
    "ModioGetDialog", "ShortcutsDialog", "XmlDialog", "HeldDialog",
    "OnlyCopiesDialog", "ModioUpdatesDialog", "AboutDialog", "FoldersDialog",
    "AddToLoadoutDialog",
)
WAITING = (
    "AddFieldDialog", "AssetPickerDialog", "InstallPlanDialog", "ModioTaskDialog",
    "NewAssetDialog", "PatternDialog",
)
