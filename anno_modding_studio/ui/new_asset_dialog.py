"""Create a brand new asset, either by copying an existing one or from a template.

Copying uses ``BaseAssetGUID``, which is a real engine feature rather than a
trick: the base game itself uses it 556 times in Anno 1800 and 906 times in
Anno 117, and an asset that has one carries no ``<Template>`` because it inherits
everything from its base. That makes a copy tiny -- you declare the handful of
values that differ instead of duplicating several hundred lines -- and it keeps
following the base asset when a patch changes it.

A one-time copy is the other thing "copy" can mean: every value of the
original written into the new asset as it stands now, with nothing tying the
two together afterwards.  It is what an asset that must not move when its
original does wants, and it costs the several hundred lines the first kind
saves.

From-scratch assets instead name a template, and get that template's exact
section list from ``templates.xml``.

The new asset is inserted with ``addNextSibling`` anchored on an existing asset,
which is how the published mods do it in both games.

Its GUID comes from a community range -- see :mod:`core.guidranges`.  A
creator listed in the public register draws from their own range; anybody
else draws from the *Personal use* range the register sets aside.  Settings
can name another range per game -- a name can be spelled differently from
its listing -- and that one is used instead.  The range is a choice in the
dialog as well, and a choice other than the one Settings and the creator name
make is kept on the loadout.

The register is fetched the first time the dialog is opened for a game (the
main window asks :func:`guidranges.fetch_if_missing` before building it).
When it could not be fetched, personal use is the only range offered, the
note under it says the register could not be reached, and a range kept on
the loadout is left there for when it can be.
"""

from __future__ import annotations

import copy
import html

from lxml import etree
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QButtonGroup,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QRadioButton,
    QVBoxLayout,
)

from ..core import discovery, effective, games, guidranges, repeats, texts
from ..core.changes import OP_ADD_NEXT, Change
from ..core.i18n import STORED, tr, tr_noop, trn
from . import theme
from .asset_picker import AssetPickerDialog
from .help_tip import HelpTip
from .widgets import show_rich, tab_as_laid_out

# The register's own *Personal use* range, for a fetched copy that leaves it
# out: every register has one to fall back on.
PERSONAL_RANGE = guidranges.PERSONAL_USE


def your_range(register, creator: str, preferred: str = "", aliases=()) -> guidranges.GuidRange:
    """The range new assets draw from before anybody chooses in the dialog:
    the one Settings names, ``preferred``, then the listing of the author
    name, or failing that of the first of the other names you have published
    under, ``aliases``, that has one, then personal use.  A preference the
    register no longer lists -- a range given up, or renamed -- falls through
    like anybody else's."""
    named = register.named(preferred) if preferred else []
    if named:
        return named[0]
    _name, listed = register.listed_as([creator, *aliases])
    if listed:
        return listed[0]
    return register.personal() or PERSONAL_RANGE


def describe_range(chosen, register, creator: str, preferred: str = "", aliases=()) -> str:
    """What drawing GUIDs from ``chosen`` means, as a sentence: whose range
    it is, and whether it is safe.  Here and in Settings alike.  Rich text,
    for the register's link, with the name found escaped into it."""
    creator = (creator or "").strip()
    aliases = [name for name in (str(alias).strip() for alias in aliases) if name]
    listed_as, creator_ranges = register.listed_as([creator, *aliases])
    named = html.escape(listed_as or creator)
    url = guidranges.REPOSITORY
    if register.source == guidranges.UNREACHABLE:
        # Translators: "Update now" is the button on the Authoring tab of
        # Settings.
        return tr(
            "The <a href=\"%s\">community register</a> could not be reached, "
            "so new assets use its personal-use range. Once you are online, "
            "press Update now in Settings ▸ Authoring.", url
        )
    if register.missing:
        # Translators: "Update now" is the button beside this text.
        return tr(
            "The <a href=\"%s\">community register</a> has not been fetched "
            "yet, so new assets use its personal-use range. Update now "
            "fetches it.", url
        )
    if chosen.personal:
        if creator_ranges:
            text = tr("The register's personal-use range, not a range of your own.")
        elif aliases:
            text = tr(
                "None of your author names is in the <a href=\"%s\">community "
                "register</a>, so new assets use its personal-use range. If "
                "you publish, claim a range there and choose it here.", url
            )
        elif creator:
            text = tr(
                "%(creator)s is not in the <a href=\"%(url)s\">community "
                "register</a>, so new assets use its personal-use range. If "
                "you publish, claim a range there and choose it here.",
                creator=named, url=url,
            )
        else:
            text = tr(
                "No author name is set, so new assets use the <a "
                "href=\"%s\">community register</a>'s personal-use range.", url
            )
    elif chosen in creator_ranges:
        text = tr(
            "Your range, as %(creator)s is listed in the <a href=\"%(url)s\">"
            "community register</a>.", creator=named, url=url,
        )
    elif preferred and chosen.owner == preferred:
        text = tr("The range chosen in Settings.")
    else:
        text = tr(
            "Somebody else's listed range. Only right if you publish under "
            "that name."
        )
    if not chosen.safe:
        text += " " + tr(
            "It lies below the span Ubisoft promised to leave alone "
            "(from %s), so it is only safe if it always was yours.",
            guidranges.dotted(guidranges.SAFE_START)
        )
    return text


class SuggestionEdit(QLineEdit):
    """A box that may hold a suggestion, which typing replaces.

    The Name box is filled in with "copy of agriculture_05" once a base asset
    is chosen.  Clicking into it put the cursor in that, and what was typed
    was added to it.  So the click that brings the focus selects the text,
    but only while it is still what was suggested: a name typed by hand is
    left as it is, and so is a suggestion once the cursor is already in it.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.suggestion = ""
        self._clicked_into = False

    def suggest(self, text: str) -> None:
        self.suggestion = text
        self.setText(text)

    def holds_suggestion(self) -> bool:
        return bool(self.suggestion) and self.text() == self.suggestion

    def focusInEvent(self, event) -> None:
        super().focusInEvent(event)
        # Tab and Alt+N select the text already, as in any box.  A click
        # brings the focus first and puts the cursor down after, which
        # unselects, so the selecting waits for the press.
        self._clicked_into = (
            event.reason() == Qt.FocusReason.MouseFocusReason and self.holds_suggestion()
        )

    def mousePressEvent(self, event) -> None:
        super().mousePressEvent(event)
        if self._clicked_into:
            self.selectAll()

    def mouseMoveEvent(self, event) -> None:
        # A hand moves a little during a click, and the box takes that for
        # dragging out a selection of its own.
        if not self._clicked_into:
            super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        super().mouseReleaseEvent(event)
        self._clicked_into = False


class NewAssetDialog(QDialog):
    """Collects everything needed to emit one new-asset ModOp."""

    def __init__(
        self,
        index,
        loadout,
        parent=None,
        mods=None,
        export_target=None,
        register=None,
        base_guid=None,
        preferred: str = "",
    ):
        """``base_guid`` is the asset the editor has open, if any: the one
        somebody went and found before asking for a new asset, so it is the
        asset offered to copy and its template the one offered to start from.
        ``preferred`` is the range Settings names for this game, by owner."""
        super().__init__(parent)
        self.index = index
        self.loadout = loadout
        self.change = None
        self._base_row = None
        self._suggested = ""

        # Who has claimed which GUIDs, per the community register.
        self.register = (
            register if register is not None else guidranges.load(loadout.game)
        )
        if self.register.personal() is None:
            self.register.ranges.append(PERSONAL_RANGE)
        # What Settings and the creator name pick, before any choice here.
        self._preferred = preferred

        # Best-effort: every mod on this PC the Studio can find -- ``mods`` is
        # (folder, where) pairs, see discovery.mods_on_this_pc -- as far as
        # each can be read. A mod on someone else's machine is unreachable no
        # matter what -- this catches the case that is actually detectable,
        # GUIDs already in play locally.
        self._sibling_guids = {}
        if mods:
            game = games.get(loadout.game)
            # Which of them are not the loadout's, as the changes list asks
            # it (repeats.outside): by ModID as well as by folder. Leaving
            # out the Export tab's destination alone, the loadout's own Play
            # build in the library was "another mod on this PC".
            owners = discovery.guid_owners(mods, game.asset_relpath, exclude=export_target)
            self._sibling_guids = {
                guid: names[0] for guid, names in repeats.outside(loadout, owners).items()
            }
        # And what the loadout itself goes on to create that is not a change
        # of its own: ops of its mods kept as written -- a group, an asset
        # written straight under <ModOps> -- and ones it could not read.
        # Each goes back out in the export, and the assets in it with it.
        self._kept_guids = {
            guid
            for held in (*loadout.ops, *loadout.unread)
            for guid in discovery.created_guids_in_text(held.source)
        }

        self.setWindowTitle(tr("New asset"))
        self.setMinimumSize(760, 600)
        self._build_ui()
        self._suggest_guid()
        if base_guid:
            self._set_base(str(base_guid))
            self._select_template(self._template_of(str(base_guid)))
        self._sync()

    # -- construction -------------------------------------------------------
    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 14, 16, 12)
        outer.setSpacing(10)

        heading = QLabel(tr("Create a new asset"))
        heading.setObjectName("Heading")
        outer.addWidget(heading)


        # -- how
        mode_box = QGroupBox(tr("Start from"))
        mode_layout = QVBoxLayout(mode_box)
        self.copy_radio = QRadioButton(tr("An existing asset"))
        self.copy_radio.setChecked(True)
        self.scratch_radio = QRadioButton(tr("A template"))
        group = QButtonGroup(self)
        group.addButton(self.copy_radio)
        group.addButton(self.scratch_radio)
        self.copy_radio.toggled.connect(self._sync)
        mode_layout.addWidget(self.copy_radio)

        copy_row = QHBoxLayout()
        copy_row.addSpacing(22)
        self.base_label = QLabel(tr("No asset chosen"))
        theme.monospace(self.base_label)
        copy_row.addWidget(self.base_label, 1)
        self.pick_button = QPushButton(tr("Choose asset…"))
        self.pick_button.clicked.connect(self._pick_base)
        copy_row.addWidget(self.pick_button)
        mode_layout.addLayout(copy_row)

        # Which kind of copy. A group of its own, so choosing here does not
        # unchoose "An existing asset" above.
        kind_row = QHBoxLayout()
        kind_row.addSpacing(22)
        self.follow_radio = QRadioButton(tr("Follow the original"))
        self.follow_radio.setChecked(True)
        self.once_radio = QRadioButton(tr("Copy its values once"))
        kinds = QButtonGroup(self)
        kinds.addButton(self.follow_radio)
        kinds.addButton(self.once_radio)
        self.follow_radio.toggled.connect(self._sync)
        kind_row.addWidget(self.follow_radio)
        kind_row.addSpacing(12)
        kind_row.addWidget(self.once_radio)
        kind_row.addStretch(1)
        kind_row.addWidget(HelpTip(tr(
            "Follow the original: the new asset inherits everything from "
            "the original through BaseAssetGUID, so you list only what you "
            "change. It keeps following the original when a game patch "
            "updates it.\n\n"
            "Copy its values once: every value of the original is written "
            "into the new asset as this mod leaves it, your changes to it "
            "included. Later changes to the original, by a game patch or by "
            "a mod, do not reach the copy."
        ), self.copy_radio.text()))
        mode_layout.addLayout(kind_row)

        # Said only of an original that is not as the game has it: what a
        # one-time copy takes is then not what the game's files hold.
        self.copy_note = QLabel("")
        self.copy_note.setObjectName("StepHint")
        self.copy_note.setWordWrap(True)
        self.copy_note.setContentsMargins(22, 0, 0, 0)
        self.copy_note.setVisible(False)
        mode_layout.addWidget(self.copy_note)

        mode_layout.addWidget(self.scratch_radio)
        scratch_row = QHBoxLayout()
        scratch_row.addSpacing(22)
        template_caption = QLabel(tr("&Template:"))
        scratch_row.addWidget(template_caption)
        # A plain combo, not a typeable one: Qt's own type-ahead (type a few
        # letters, it jumps to the first matching name) reaches any of the
        # ~1,000 templates in a few keystrokes without ever looking like a
        # text field to get there.
        self.template_combo = QComboBox()
        template_caption.setBuddy(self.template_combo)
        self.template_combo.currentTextChanged.connect(self._sync)
        scratch_row.addWidget(self.template_combo, 1)
        mode_layout.addLayout(scratch_row)

        self.template_note = QLabel("")
        self.template_note.setObjectName("StepHint")
        self.template_note.setWordWrap(True)
        self.template_note.setContentsMargins(22, 0, 0, 0)
        mode_layout.addWidget(self.template_note)
        outer.addWidget(mode_box)

        # -- identity
        detail_box = QGroupBox(tr("The new asset"))
        form = QFormLayout(detail_box)
        self.guid_edit = QLineEdit()
        theme.monospace(self.guid_edit)
        self.guid_edit.textChanged.connect(self._sync)
        self.name_edit = SuggestionEdit()
        self.name_edit.setPlaceholderText(tr("Internal name, e.g. my_small_trade_union"))
        self.name_edit.textChanged.connect(self._sync)
        # What players see. Written, when the mod is exported, into every
        # language's texts file the game has -- one name for all of them.
        self.in_game_edit = QLineEdit()
        self.in_game_edit.setPlaceholderText(tr("What players see, e.g. Small Trade Union"))
        self.in_game_edit.setToolTip(tr(
            "Written to every language's texts file on export. Leave it "
            "empty for no name."
        ))
        self.in_game_edit.textChanged.connect(self._sync)
        # The community range GUIDs are drawn from. A plain combo like the
        # template one: type-ahead finds a name among a hundred-odd.
        self.range_combo = QComboBox()
        for item in self.register.claimable():
            self.range_combo.addItem(item.label, item)
        self._select_range(self._default_range())
        self.range_combo.currentIndexChanged.connect(self._range_changed)
        self.range_note = QLabel("")
        self.range_note.setObjectName("StepHint")
        self.range_note.setWordWrap(True)
        self.range_note.setOpenExternalLinks(True)
        self._describe_range()
        form.addRow(tr("GUID &range"), self.range_combo)
        form.addRow("", self.range_note)
        form.addRow("&GUID", self.guid_edit)  # i18n: keep - a name
        form.addRow(tr("&Name"), self.name_edit)
        form.addRow(tr("&In-game name"), self.in_game_edit)
        outer.addWidget(detail_box)

        self.status = QLabel("")
        self.status.setWordWrap(True)
        outer.addWidget(self.status)

        preview_label = QLabel(tr("The ModOp this will produce"))
        preview_label.setObjectName("SubHeading")
        outer.addWidget(preview_label)
        self.preview = QPlainTextEdit()
        self.preview.setReadOnly(True)
        theme.monospace(self.preview)
        self.preview.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        outer.addWidget(self.preview, 1)

        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        self.create_button = self.buttons.addButton(
            tr("Create asset"), QDialogButtonBox.ButtonRole.AcceptRole
        )
        self.create_button.setObjectName("Primary")
        self.create_button.clicked.connect(self._accept)
        self.buttons.rejected.connect(self.reject)
        tab_as_laid_out(self.buttons)
        outer.addWidget(self.buttons)

        # Only now that every widget _sync() touches exists.
        self._load_templates()

    def _load_templates(self) -> None:
        schema = self.index.schema if self.index is not None else None
        names = schema.templates() if schema is not None else []
        self.template_combo.addItems(names)
        if not names:
            # Translators: "Start" is the name of the Studio's first tab.
            self.template_note.setText(tr(
                "No templates.xml loaded, so from-scratch assets cannot be offered. "
                "Load your game files on the Start tab to enable this."
            ))
            self.scratch_radio.setEnabled(False)
            self.template_combo.setEnabled(False)

    # -- the range ------------------------------------------------------------
    def _default_range(self) -> guidranges.GuidRange:
        """The range this loadout draws from before anybody chooses here.

        A choice kept on the loadout wins, then :func:`your_range`.  A kept
        choice that the register no longer lists falls through to it.
        """
        kept = self.register.named(self.loadout.own.guid_range or "")
        if kept:
            return kept[0]
        return self._your_range()

    def _your_range(self) -> guidranges.GuidRange:
        return your_range(self.register, self.loadout.own.creator, self._preferred,
                          self.loadout.aliases)

    def _your_ranges(self) -> list:
        """Every range that is yours: the one Settings names, and the ones
        your author name is listed under, or the first of your other names
        that is listed."""
        named = self.register.named(self._preferred) if self._preferred else []
        _name, listed = self.register.listed_as([self.loadout.own.creator, *self.loadout.aliases])
        return named + [r for r in listed if r not in named]

    def chosen_range(self) -> guidranges.GuidRange:
        item = self.range_combo.currentData()
        return item if item is not None else self._default_range()

    def _select_range(self, wanted: guidranges.GuidRange) -> None:
        for row in range(self.range_combo.count()):
            if self.range_combo.itemData(row) == wanted:
                self.range_combo.setCurrentIndex(row)
                return

    def _range_changed(self) -> None:
        self._describe_range()
        # A GUID still at its suggestion follows the range; one typed stays.
        if self.guid_edit.text().strip() == self._suggested:
            self._suggest_guid()
        self._sync()

    def _describe_range(self) -> None:
        show_rich(self.range_note, describe_range(
            self.chosen_range(), self.register, self.loadout.own.creator, self._preferred,
            self.loadout.aliases,
        ))

    # -- helpers ------------------------------------------------------------
    def _taken(self, guid: str) -> bool:
        """Anything that already answers to ``guid`` where this Studio can see."""
        if self.index is not None and self.index.guid_exists(guid):
            return True
        if guid in self._sibling_guids or guid in self._kept_guids:
            return True
        return any(effective.new_asset_guid(c) == guid for c in self.loadout)

    def _suggest_guid(self) -> None:
        """Pick the first free GUID in the chosen range.

        A range with nothing left in it -- ten thousand assets is a lot, but
        a listing can also be a single number -- falls back to the other
        ranges that are yours, then to personal use, which is the
        register's own answer for numbers nobody has claimed.  Counting on
        past the end of a range would walk into whoever is listed next.
        """
        chosen = self.chosen_range()
        personal = self.register.personal() or PERSONAL_RANGE
        ranges = [chosen]
        ranges += [r for r in self._your_ranges() if r not in ranges]
        if personal not in ranges:
            ranges.append(personal)
        found = self.register.free_guid(ranges, self._taken)
        if found is None:
            found = personal.end + 1
            while self._taken(str(found)):
                found += 1
        self._suggested = str(found)
        self.guid_edit.setText(self._suggested)

    def _pick_base(self) -> None:
        if self.index is None:
            return
        # Translators: completes "Choose an asset for %s", the picker's title.
        dialog = AssetPickerDialog(self.index, tr("the new asset's base"), "", self)
        if dialog.exec() == int(QDialog.DialogCode.Accepted) and dialog.chosen_guid:
            self._set_base(dialog.chosen_guid)
            self._sync()

    def _set_base(self, guid: str) -> None:
        if self.index is None:
            return
        self._base_row = self.index.asset_row(guid)
        if self._base_row is not None:
            self.base_label.setText(
                "%s  ·  %s" % (self._base_row.guid, self._base_row.label)  # i18n: keep - a separator
            )
            # A name still at its suggestion follows the asset chosen; one
            # typed stays.
            if not self.name_edit.text() or self.name_edit.holds_suggestion():
                self.name_edit.suggest("copy of " + (self._base_row.name or "asset"))  # i18n: keep - written into the mod

    def _template_of(self, guid: str) -> str:
        """The template ``guid`` is made from, through what it inherits.

        An asset with a ``BaseAssetGUID`` names no template of its own, so
        its base is asked, and that one's base, as far as it goes.  A chain
        that loops, or runs past any the game has, gives up.
        """
        if self.index is None:
            return ""
        # The workspace knows the loadout's own new assets as well.
        element_of = getattr(self.index, "base_element", None) or self.index.asset_element
        seen = set()
        while guid and guid not in seen and len(seen) < 32:
            seen.add(guid)
            element = element_of(guid)
            if element is None:
                return ""
            template = (element.findtext("Template") or "").strip()
            if template:
                return template
            guid = (element.findtext("BaseAssetGUID") or "").strip()
        return ""

    def _select_template(self, template: str) -> None:
        # Only one the combo offers: templates.xml decides what can be made.
        found = self.template_combo.findText(template) if template else -1
        if found >= 0:
            self.template_combo.setCurrentIndex(found)

    def _anchor_guid(self) -> str:
        """The existing asset the new one is inserted after."""
        if self.copy_radio.isChecked() and self._base_row is not None:
            return self._base_row.guid
        # From scratch: anchor on any asset of the same template so the new asset
        # lands among its own kind.
        template = self.template_combo.currentText().strip()
        if self.index is not None and template:
            rows = self.index.search("", template=template, limit=1)
            if rows:
                return rows[0].guid
        return ""

    def _build_asset(self):
        if self._copies_once():
            return self._build_copy()
        asset = etree.Element("Asset")
        if self.copy_radio.isChecked():
            if self._base_row is None:
                return None
            base = etree.SubElement(asset, "BaseAssetGUID")
            base.text = self._base_row.guid
        else:
            template = self.template_combo.currentText().strip()
            if not template:
                return None
            node = etree.SubElement(asset, "Template")
            node.text = template

        values = etree.SubElement(asset, "Values")
        standard = etree.SubElement(values, "Standard")
        guid = etree.SubElement(standard, "GUID")
        guid.text = self.guid_edit.text().strip()
        name = etree.SubElement(standard, "Name")
        name.text = self.name_edit.text().strip()
        if self.in_game_edit.text().strip() and self.loadout.game == games.ANNO_117:
            texts.point_at_own_name(asset, guid.text)

        if self.scratch_radio.isChecked():
            # Give a from-scratch asset the empty sections its template declares,
            # so there is somewhere to add fields afterwards.
            schema = self.index.schema if self.index is not None else None
            template = self.template_combo.currentText().strip()
            sections = schema.sections_for_template(template) if schema else []
            for section in sections:
                if section != "Standard" and values.find(section) is None:
                    etree.SubElement(values, section)
        return asset

    def _copies_once(self) -> bool:
        return self.copy_radio.isChecked() and self.once_radio.isChecked()

    def _original(self):
        """The asset a one-time copy is taken of, as the mod being edited
        finds it: the game's, with what loads before this mod applied and
        this mod's own changes to it -- what the editor shows for it."""
        if self._base_row is None or self.index is None:
            return None
        guid = self._base_row.guid
        effective_asset = getattr(self.index, "effective_asset", None)
        if effective_asset is None:
            # A bare index, with no loadout over it.
            found = self.index.asset_element(guid)
        else:
            found = effective_asset(
                guid, upto=self.loadout.editing_origin()
            ).as_the_game_reads_it()
        # A copy, to rename: the tree handed over is not this dialog's.
        return copy.deepcopy(found) if found is not None else None

    def _changed_already(self) -> bool:
        """Whether what a one-time copy takes is other than the game's own
        asset: changed by the mod being edited, or by one that loads before
        it.  An asset the loadout makes has no other form to be taken for."""
        effective_asset = getattr(self.index, "effective_asset", None)
        if self._base_row is None or effective_asset is None:
            return False
        held = effective_asset(self._base_row.guid, upto=self.loadout.editing_origin())
        if held.element is None or held.is_new:
            return False
        return any(
            held.origin(node).provenance != effective.PROV_BASE
            for node in held.element.iter()
        )

    def _build_copy(self):
        """The original with the new asset's GUID and name in place of its own.

        Everything else is the original's, a ``BaseAssetGUID`` of its own
        included: a copy of an asset that inherits goes on inheriting from
        the same base.
        """
        asset = self._original()
        if asset is None or asset.tag != "Asset":
            return None
        values = asset.find("Values")
        if values is None:
            values = etree.SubElement(asset, "Values")
        standard = values.find("Standard")
        if standard is None:
            standard = etree.Element("Standard")
            values.insert(0, standard)
        guid = self.guid_edit.text().strip()
        for tag, said in (("GUID", guid), ("Name", self.name_edit.text().strip())):
            node = standard.find(tag)
            if node is None:
                node = etree.SubElement(standard, tag)
            node.text = said
        if self.in_game_edit.text().strip() and self.loadout.game == games.ANNO_117:
            # Left alone, the copy's OasisId is the original's, and so is the
            # name the game shows for it.
            texts.point_at_own_name(asset, guid)
        # Laid out afresh: the original's own indentation is the game file's,
        # and the nodes put in above have none.
        for element in asset.iter():
            element.tail = None
            if len(element):
                element.text = None
        etree.indent(asset, space="  ")
        return asset

    def _problem(self) -> str:
        guid = self.guid_edit.text().strip()
        if not guid:
            return tr("Give the new asset a GUID.")
        if not guid.isdigit():
            return tr("A GUID must be a plain number.")
        if self.index is not None and self.index.guid_exists(guid):
            row = self.index.asset_row(guid)
            if row:
                return tr("GUID %(guid)s is already used by %(asset)s.", guid=guid, asset=row.label)
            return tr("GUID %s is already used by an asset.", guid)
        if guid in self._kept_guids or any(
            effective.new_asset_guid(c) == guid for c in self.loadout
        ):
            return tr("This loadout already creates GUID %s.", guid)
        if guid in self._sibling_guids:
            return tr(
                "GUID %(guid)s is already used by another mod on this PC: %(mod)s.",
                guid=guid, mod=self._sibling_guids[guid],
            )
        if not self.name_edit.text().strip():
            return tr("Give the new asset a name.")
        if self.copy_radio.isChecked() and self._base_row is None:
            return tr("Choose the asset to copy.")
        if self.scratch_radio.isChecked() and not self.template_combo.currentText().strip():
            return tr("Choose a template.")
        if self._copies_once() and self._original() is None:
            return tr("The asset to copy could not be read.")
        if not self._anchor_guid():
            return tr("Could not find an existing asset to insert next to.")
        return ""

    def _warning(self) -> str:
        """A GUID that will work today but is somebody else's to give.

        Nothing here stops the asset being made: the game accepts any
        number, and a mod that has always used its own old range should go
        on doing so.  It is said, because it is the collision that no local
        check can catch -- the other mod is on someone else's machine.
        """
        guid = self.guid_edit.text().strip()
        chosen = self.chosen_range()
        if guid in chosen:
            return ""
        others = [
            item
            for item in self.register.owners_of(guid)
            if not item.personal and not item.reserved and item not in self._your_ranges()
        ]
        if others:
            return tr(
                "GUID %(guid)s lies in %(owner)s's listed range, so a mod of theirs may "
                "already use it.", guid=guid, owner=others[0].owner,
            )
        if int(guid) < guidranges.SAFE_START:
            return tr(
                "GUID %(guid)s is below the span Ubisoft promised to leave "
                "alone (from %(start)s). A future update could take it.",
                guid=guid, start=guidranges.dotted(guidranges.SAFE_START),
            )
        if int(guid) > guidranges.SAFE_END:
            return tr(
                "GUID %(guid)s is above %(end)s, the largest the game can hold.",
                guid=guid, end=guidranges.dotted(guidranges.SAFE_END),
            )
        return tr("GUID %(guid)s is outside the chosen range (%(range)s).",
                  guid=guid, range=chosen.label)

    def _sync(self) -> None:
        self.pick_button.setEnabled(self.copy_radio.isChecked())
        self.follow_radio.setEnabled(self.copy_radio.isChecked())
        self.once_radio.setEnabled(self.copy_radio.isChecked())
        self.template_combo.setEnabled(self.scratch_radio.isChecked())

        # Copying once takes the asset as the editor shows it, and that is
        # said where it is not the asset the game's files hold: the copy came
        # out with CycleTime 10 where the game has 15, to nobody's notice.
        changed = self._copies_once() and self._changed_already()
        if changed:
            self.copy_note.setText(tr(
                "The copy takes %s as this mod leaves it, with the changes "
                "made to it so far.",
                self._base_row.display or self._base_row.name or self._base_row.guid,
            ))
        self.copy_note.setVisible(changed)

        if self.scratch_radio.isChecked():
            schema = self.index.schema if self.index is not None else None
            template = self.template_combo.currentText().strip()
            sections = schema.sections_for_template(template) if schema else []
            if sections:
                # A list after a colon, and cut short: not part of the sentence.
                named = ", ".join(sections[:8]) + ("…" if len(sections) > 8 else "")
                self.template_note.setText(trn(
                    "%(template)s has %(n)s section: %(sections)s",
                    "%(template)s has %(n)s sections: %(sections)s",
                    len(sections), template=template, sections=named,
                ))
            elif template:
                self.template_note.setText(tr("templates.xml does not describe %r.", template))
        elif self.scratch_radio.isEnabled():
            # The template is described only while it is the one chosen;
            # with none to choose, the note says why and stays.
            self.template_note.setText("")

        problem = self._problem()
        self.create_button.setEnabled(not problem)
        if problem:
            self.status.setText(problem)
            self.status.setObjectName("Warn")
            self.preview.setPlainText("")
        else:
            warning = self._warning()
            self.status.setText(warning or tr("Ready."))
            self.status.setObjectName("Warn" if warning else "Ok")
            asset = self._build_asset()
            if asset is not None:
                body = etree.tostring(asset, pretty_print=True).decode().strip()
                self.preview.setPlainText(
                    '<ModOp Type="addNextSibling" GUID="%s">\n%s\n</ModOp>'  # i18n: keep - XML
                    % (self._anchor_guid(), "\n".join("  " + l for l in body.splitlines()))
                )
        theme.repolish(self.status)

    def _accept(self) -> None:
        if self._problem():
            return
        asset = self._build_asset()
        if asset is None:
            return
        guid = self.guid_edit.text().strip()
        name = self.name_edit.text().strip()
        self.change = Change(
            guid=self._anchor_guid(),
            asset_label=tr_noop("%s  (new asset)", context=STORED) % name,
            path="",  # anchors on the asset itself
            tag="Asset",
            op=OP_ADD_NEXT,
            payload=etree.tostring(asset, pretty_print=True).decode().strip(),
            summary=tr_noop("create asset %(guid)s (%(name)s)", context=STORED)
            % {"guid": guid, "name": name},
            in_game_name=self.in_game_edit.text().strip(),
        )
        # Remember a range chosen over the one Settings and the creator name
        # pick; forget one chosen back.  With no register fetched there was
        # no choice to make, and a range kept from before stays for when
        # the register can be read again.
        chosen = self.chosen_range()
        if not self.register.missing:
            self.loadout.own.guid_range = "" if chosen == self._your_range() else chosen.owner
        self.accept()
