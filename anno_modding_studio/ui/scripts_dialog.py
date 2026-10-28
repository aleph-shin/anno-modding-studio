"""A mod's modinfo ``Scripts`` block, edited like its other details.

Anno 117 runs Lua a mod's modinfo lists: its modules, and a snippet for each
moment the loader offers.  The Lua itself is written somewhere made for it --
VS Code, with the Anno Modding Tools beside it -- and this is only the part of
the mod that tells the game about it: which files, and what to call when.

What the Studio adds is the rest of the loadout.  Every loading mod's modules
and globals share one space, the loader's docs ask for names nobody else uses,
and this is where both are in view at once: a new module's names are
suggested clear of every other mod's, and a clash is said as you type.

A new module is written from the docs' template when the dialog is kept, not
before, so cancelling leaves nothing behind on disk.
"""

from __future__ import annotations

import re
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
)

from ..core import scripts
from ..core.changes import is_own_mod
from ..core.i18n import tr
from . import theme
from .help_tip import HelpTip
from .widgets import (
    enter_activates,
    least_size,
    open_script,
    shown_row,
    tab_after,
    wrappable_path,
)

_MODULE_NAME = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.\-]*$")
_GLOBAL_NAME = re.compile(r"^[A-Za-z_]\w*$")


class NewModuleDialog(QDialog):
    """A new module's file name, the global its Init keeps it in, and which
    hooks call it -- suggested clear of every loading mod's."""

    def __init__(self, stem: str, name: str, taken, parent=None):
        super().__init__(parent)
        self._stems, self._names = taken
        self.setWindowTitle(tr("New module"))
        self.setMinimumWidth(460)
        outer = QVBoxLayout(self)
        form = QFormLayout()
        self.stem_edit = QLineEdit(stem)
        self.name_edit = QLineEdit(name)
        for edit in (self.stem_edit, self.name_edit):
            theme.monospace(edit)
            edit.textChanged.connect(self._check)
        file_row = QHBoxLayout()
        file_row.addLayout(self._with_suffix(self.stem_edit), 1)
        file_caption = QLabel(tr("&File", context="Lua module"))
        file_caption.setBuddy(self.stem_edit)
        # Translators: Modules and Init are keys of a modinfo's Scripts block,
        # and stay as they are; a global is a Lua variable every script sees.
        file_row.addWidget(HelpTip(tr(
            "The file is written from the loader's template into the mod's "
            "scripts folder, listed under Modules, and required into a global "
            "by Init. The names are suggested from the Mod ID and differ from "
            "those of every loaded mod."
        ), self.windowTitle()))
        form.addRow(file_caption, file_row)
        # Translators: the Lua global the module is kept in.
        form.addRow(tr("&Global", context="Lua variable"), self.name_edit)
        self.hook_boxes = {}
        row = QHBoxLayout()
        for hook in scripts.HOOKS[1:]:
            box = QCheckBox("&" + hook)  # i18n: keep - the loader's name
            box.setChecked(hook == "Tick")
            self.hook_boxes[hook] = box
            row.addWidget(box)
        row.addStretch(1)
        # Translators: followed by the loader's hooks, Load, Unload and Tick.
        shown_row(form, tr("Called on"), row)
        outer.addLayout(form)
        self.problem = QLabel("")
        self.problem.setWordWrap(True)
        theme.tint(self.problem, "warn")
        outer.addWidget(self.problem)
        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        outer.addWidget(self.buttons)
        self._check()

    @staticmethod
    def _with_suffix(edit):
        row = QHBoxLayout()
        row.addWidget(edit, 1)
        row.addWidget(QLabel(".lua"))  # i18n: keep - a file extension
        return row

    def values(self):
        hooks = [hook for hook, box in self.hook_boxes.items() if box.isChecked()]
        return self.stem_edit.text().strip(), self.name_edit.text().strip(), hooks

    def problem_text(self) -> str:
        stem, name, _ = self.values()
        if not _MODULE_NAME.match(stem):
            return tr("The file name needs letters, digits, - or _.")
        if not _GLOBAL_NAME.match(name) or name in scripts.LUA_WORDS:
            return tr("The global has to be a Lua name: a letter or _, then letters, digits or _.")
        if stem.lower() in self._stems:
            return tr("A loading mod already has a module %s.lua.", stem)
        if name in self._names:
            return tr("A loading mod already sets the global %s.", name)
        return ""

    def _check(self) -> None:
        why = self.problem_text()
        self.problem.setText(why)
        # With nothing to say its warning glyph stood alone under Called on
        # (H2-30).
        self.problem.setVisible(bool(why))
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(not why)


class ScriptsDialog(QDialog):
    """Modules and hooks of one mod, with what they share with other mods'."""

    def __init__(self, loadout, origin: str, mod_id: str, parent=None):
        super().__init__(parent)
        self.loadout = loadout
        self.origin = origin
        self.mod_id = mod_id
        self.original = scripts.block_of(loadout, origin)
        self.folder = scripts.folder_of(loadout, origin)
        # New modules, written when the dialog is kept: path in the mod -> Lua.
        self.pending = {}
        self.mod_name = loadout.label_of(origin) or tr("your mod")
        self.setWindowTitle(tr("Scripts: %s", self.mod_name))
        least_size(self, 720, 520)
        self._build_ui()
        self._load(self.original)

    # -- building ---------------------------------------------------------
    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 14, 16, 12)
        where = QHBoxLayout()
        self.folder_label = QLabel("")
        self.folder_label.setWordWrap(True)
        where.addWidget(self.folder_label, 1)
        self.folder_button = QPushButton(tr("&Choose folder…"))
        self.folder_button.setToolTip(tr(
            "Where this mod's Lua is on this machine. Exporting copies it into the mod."
        ))
        self.folder_button.clicked.connect(self._choose_folder)
        where.addWidget(self.folder_button)
        # The title says whose scripts. What the game does with them is behind
        # the ?.
        # Translators: Init, Load, Unload and Tick are the loader's names for
        # those moments, and stay as they are.
        where.addWidget(HelpTip(tr(
            "Anno 117 runs the Lua that a mod's modinfo lists: its modules, "
            "and a snippet for each moment. Init runs once the mods are "
            "loaded, Load and Unload with a game, and Tick while it runs. The "
            "loader calls this experimental. Write the Lua in an editor made "
            "for it. This dialog edits what the modinfo lists."
        ), tr("Scripts")))
        outer.addLayout(where)
        # Once it is in the dialog: a widget with no parent shows as a window.
        self.folder_button.setVisible(not self.origin)

        # Translators: the Lua modules the modinfo lists.
        modules_caption = QLabel(tr("&Modules"))
        outer.addWidget(modules_caption)
        self.modules = QListWidget()
        modules_caption.setBuddy(self.modules)
        self.modules.setMaximumHeight(130)
        theme.monospace(self.modules)
        self.modules.currentRowChanged.connect(self._sync_buttons)
        enter_activates(self.modules)
        self.modules.itemActivated.connect(lambda _item: self._open())
        outer.addWidget(self.modules)
        buttons = QHBoxLayout()
        self.new_button = QPushButton(tr("&New module…"))
        self.new_button.clicked.connect(self.new_module)
        self.add_button = QPushButton(tr("&Add a file…"))
        self.add_button.clicked.connect(self._add_file)
        self.remove_button = QPushButton(tr("&Remove", context="verb"))
        self.remove_button.setToolTip(tr("Takes it off the list. The file stays where it is."))
        self.remove_button.clicked.connect(self._remove)
        self.open_button = QPushButton(tr("&Open", context="verb"))
        self.open_button.clicked.connect(self._open)
        for button in (self.new_button, self.add_button, self.remove_button, self.open_button):
            buttons.addWidget(button)
        buttons.addStretch(1)
        outer.addLayout(buttons)

        self.hooks_form = QFormLayout()
        outer.addLayout(self.hooks_form)
        self.hook_edits = {}

        self.shared = QLabel("")
        self.shared.setWordWrap(True)
        theme.tint(self.shared, "warn")
        outer.addWidget(self.shared)
        outer.addStretch(1)

        box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        box.accepted.connect(self._accept)
        box.rejected.connect(self.reject)
        outer.addWidget(box)

    def _load(self, block: dict) -> None:
        self.modules.clear()
        found = scripts.declared({"Scripts": block}) or scripts.Scripts()
        for module in found.modules:
            self._add_row(module.path)
        # The four the loader offers, and any other the block already has --
        # a hook added in a later version is kept and editable, not dropped.
        names = list(scripts.HOOKS)
        for key, value in block.items():
            if (isinstance(key, str) and key.lower() != "modules" and isinstance(value, str)
                    and key.lower() not in (n.lower() for n in names)):
                names.append(key)
        before = self.open_button
        for name in names:
            edit = QLineEdit(str(block.get(scripts.key_in(block, name), "") or ""))
            theme.monospace(edit)
            # Translators: %s is one of the loader's hooks: Init, Load, Unload, Tick.
            edit.setPlaceholderText(tr("Lua run on %s", name))
            edit.textChanged.connect(self._refresh)
            self.hook_edits[name] = edit
            self.hooks_form.addRow("&" + name, edit)  # i18n: keep - the loader's name
            before = tab_after(before, edit)
        self._refresh()

    def _add_row(self, path: str) -> None:
        item = QListWidgetItem(path)
        item.setData(Qt.ItemDataRole.UserRole, path)
        self.modules.addItem(item)

    # -- state ------------------------------------------------------------
    def paths(self):
        return [self.modules.item(i).data(Qt.ItemDataRole.UserRole) for i in range(self.modules.count())]

    def block(self) -> dict:
        """What the dialog says the block is now.  Keys the author wrote keep
        their spelling and their place, and anything that is neither the
        module list nor a hook is carried over untouched."""
        out = {}
        paths = self.paths()
        if paths:
            out[scripts.key_in(self.original, "Modules")] = paths
        for name, edit in self.hook_edits.items():
            code = edit.text().strip()
            if code:
                out[scripts.key_in(self.original, name)] = code
        for key, value in self.original.items():
            if key.lower() != "modules" and not isinstance(value, str):
                out[key] = value
        kept = [key for key in self.original if key in out]
        return {key: out[key] for key in kept + [key for key in out if key not in kept]}

    def _there(self, path: str) -> bool:
        return path in self.pending or bool(self.folder and (Path(self.folder) / path).is_file())

    def clashes(self):
        found = scripts.clashes(self.loadout, instead=(self.origin, self.block(), self.folder))
        return scripts.clashes_of(found, self.origin)

    def _refresh(self) -> None:
        if self.folder:
            text = tr("Lua files in %s", wrappable_path(self.folder))
        elif not is_own_mod(self.origin):
            text = tr("This mod has no folder yet, so its module files cannot be looked for.")
        else:
            text = tr("No folder is chosen for this mod's Lua yet.")
        self.folder_label.setText(text)
        for i in range(self.modules.count()):
            item = self.modules.item(i)
            path = item.data(Qt.ItemDataRole.UserRole)
            if path in self.pending:
                item.setText(tr("%s  (new, written when you press OK)", path))
            elif self.folder and not self._there(path):
                item.setText(tr("%s  (not in the folder)", path))
            else:
                item.setText(path)
        lines = [clash.describe(self._name) for clash in self.clashes() if not clash.is_harmless]
        self.shared.setText("\n".join(lines))
        self.shared.setVisible(bool(lines))
        self.new_button.setEnabled(bool(self.folder) or not self.origin)
        self.new_button.setToolTip(
            "" if self.new_button.isEnabled()
            else tr("This mod has no folder yet to write a module into.")
        )
        self.add_button.setEnabled(bool(self.folder))
        self._sync_buttons()

    def _sync_buttons(self, *_args) -> None:
        item = self.modules.currentItem()
        self.remove_button.setEnabled(item is not None)
        path = item.data(Qt.ItemDataRole.UserRole) if item is not None else ""
        self.open_button.setEnabled(bool(path) and path not in self.pending and self._there(path))

    def _name(self, origin: str) -> str:
        if origin == self.origin:
            return self.mod_name
        return self.loadout.label_of(origin) or tr("your mod")

    # -- acts -------------------------------------------------------------
    def _choose_folder(self) -> bool:
        chosen = QFileDialog.getExistingDirectory(
            self, tr("Where this mod's Lua lives"), self.folder or str(Path.home())
        )
        if not chosen:
            return False
        self.folder = chosen
        self._refresh()
        return True

    def new_module(self) -> None:
        if not self.folder:
            # A mod of your own asks where its Lua is to live, once; a loaded
            # mod's is its folder, and without one the button is off.
            if not is_own_mod(self.origin) or not self._choose_folder():
                return
        stem, name = scripts.suggest(self.loadout, self.origin, self.mod_id, self.block())
        stems, names = scripts.taken(self.loadout, self.origin, self.block())
        stems |= {scripts.stem(path).lower() for path in self.pending}
        dialog = NewModuleDialog(stem, name, (stems, names), self)
        try:
            if dialog.exec() != QDialog.DialogCode.Accepted:
                return
            stem, name, hooks = dialog.values()
        finally:
            dialog.deleteLater()
        self.add_module(stem, name, hooks)

    def add_module(self, stem: str, name: str, hooks) -> str:
        """List a new module and wire it into the hooks; its file is written
        on OK.  Where the mod already has a file of that name, that file is
        listed and not replaced."""
        path = "%s/%s.lua" % (scripts.MODULE_FOLDER, stem)
        if not (self.folder and (Path(self.folder) / path).is_file()):
            self.pending[path] = scripts.template(name, stem, hooks)
        wired = scripts.with_module(self.block(), path, name, hooks)
        if path not in self.paths():
            self._add_row(path)
        for hook, edit in self.hook_edits.items():
            value = wired.get(scripts.key_in(wired, hook), "")
            if value != edit.text():
                edit.setText(value)
        self._refresh()
        return path

    def _add_file(self) -> None:
        chosen, _ = QFileDialog.getOpenFileName(
            self, tr("A module of this mod's"), self.folder,
            "Lua (*.lua)",  # i18n: keep - a file pattern
        )
        if chosen:
            self.add_path(chosen)

    def add_path(self, chosen: str) -> bool:
        try:
            inside = Path(chosen).resolve().relative_to(Path(self.folder).resolve())
        except ValueError:
            QMessageBox.information(
                self, tr("Not in this mod"),
                tr("A module's path is read from the mod's own folder, so the file "
                   "has to be in %s.", self.folder),
            )
            return False
        path = inside.as_posix()
        if path not in self.paths():
            self._add_row(path)
        self._refresh()
        return True

    def _remove(self) -> None:
        row = self.modules.currentRow()
        if row < 0:
            return
        path = self.modules.item(row).data(Qt.ItemDataRole.UserRole)
        self.pending.pop(path, None)
        self.modules.takeItem(row)
        self._refresh()

    def _open(self) -> None:
        item = self.modules.currentItem()
        path = item.data(Qt.ItemDataRole.UserRole) if item is not None else ""
        if path and path not in self.pending and self._there(path):
            open_script(Path(self.folder) / path, self.folder)

    def _accept(self) -> None:
        # The new modules first: a block naming a file that failed to be
        # written is worse than a dialog that stays open to say so.
        for path, text in self.pending.items():
            target = Path(self.folder) / path
            try:
                target.parent.mkdir(parents=True, exist_ok=True)
                if not target.exists():
                    target.write_text(text, encoding="utf-8")
            except OSError as exc:
                QMessageBox.warning(self, tr("Could not write the module"), "%s: %s" % (target, exc))
                return
        self.pending.clear()
        self.keep()
        self.accept()

    def keep(self) -> None:
        scripts.set_block(self.loadout, self.origin, self.block())
        if is_own_mod(self.origin):
            self.loadout.own.scripts_folder = self.folder
