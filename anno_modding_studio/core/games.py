"""Per-game differences in how a mod is written.

Anno 1800 and Anno 117 both patch ``assets.xml`` with ModOps, but almost every
surface detail differs:

===================  ==========================================  ===============================
                     Anno 1800                                   Anno 117
===================  ==========================================  ===============================
file in the mod      data/config/export/main/asset/assets.xml    data/base/config/export/assets.xml
replace a value      <ModOp Type="replace" GUID="x" Path="/Values/A/B">   <ModOp Replace="@x/A/B">
path root            starts at /Values                           starts at the section, no /Values
modinfo.json         ModDependencies, DeprecateIds               "Anno": 8, Dependencies object
===================  ==========================================  ===============================

Both dialects were taken from real, currently-shipping mods rather than
guessed, but 117's tooling is young: if the game rejects a generated file, the
dialect here is the first thing to check. The modinfo itself is written by
:mod:`core.modinfo`, which asks this module; this module asks nothing of the
ones that write a mod.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional
from xml.sax.saxutils import quoteattr

from . import places
from .i18n import tr

ANNO_1800 = "anno1800"

# The texts files each game ships, one per language, read off both games'
# archives -- ``data/config/gui/texts_<language>.xml`` for 1800 and
# ``data/base/config/gui/…`` for 117 (whose ``texts_metadata.xml`` is not a
# language). A name the Studio writes goes into all of them: a language
# missing an entry shows the raw id in game.
LANGUAGES = {
    "anno1800": (
        "brazilian", "chinese", "english", "french", "german", "italian", "japanese",
        "korean", "polish", "portuguese", "russian", "spanish", "taiwanese",
    ),
    "anno117": (
        "brazilian", "english", "french", "german", "italian", "japanese", "korean",
        "polish", "russian", "simplified_chinese", "spanish", "traditional_chinese",
    ),
}
ANNO_117 = "anno117"


@dataclass(frozen=True)
class Game:
    """The facts about one game: where its assets live, how its ModOps are
    written, what mod.io calls it."""

    key: str
    label: str
    asset_relpath: Path
    # Mod categories as they appear on mod.io for this game.
    categories: tuple
    # How a ModOp addresses its target.
    #   "classic"   -> Type="replace" GUID="…" Path="/Values/…"
    #   "shorthand" -> Replace="@GUID/…"
    modop_style: str
    strips_values_prefix: bool
    # The game as mod.io knows it: the number its API and its download cache
    # are keyed on, and the name in its page address.
    modio_game_id: int
    modio_name_id: str
    # Whether the game has a mod.io browser of its own, which downloads what
    # the account subscribes to into its cache.  Anno 117 has none: there a
    # subscription puts nothing on disk, and there is nothing to unsubscribe
    # from to free a copy.
    in_game_browser: bool = False
    # The game's own folder under Documents: its logs, and for Anno 117 the
    # mod profiles, the options file and a mods folder of its own.
    documents_folder: str = ""
    # The folder Steam installs it into, under steamapps/common: what its
    # appmanifest gives as "installdir".
    install_folder: str = ""
    # The process it runs as, under Bin/Win64 in the install.
    exe: str = ""
    # Which build of the loader's command-line twin, in the Anno Modding
    # Tools extension, answers for this game.
    xmltest_build: str = ""
    # Where a mod keeps its texts_<language>.xml patches.
    gui_relpath: Path = Path("data/config/gui")

    # Shorthand attribute name per op, for the games that use that form.
    _SHORTHAND = {
        "replace": "Replace",
        "add": "Add",
        "remove": "Remove",
        "merge": "Merge",
    }

    def modop_open_tag(self, guid: str, path: str, op: str = "replace") -> str:
        """The opening ``<ModOp …>`` for ``op`` against ``path``.

        ``addNextSibling`` always uses the classic attribute form: that is what
        real Anno 117 mods do for it even though they use the shorthand
        elsewhere, and it is the only form with a Path-less variant (anchoring on
        the asset itself, which is how a new asset gets inserted).
        """
        shorthand = self._SHORTHAND.get(op)
        if self.modop_style == "shorthand" and shorthand is not None:
            target = "@%s%s" % (guid, self.display_path(path))
            return "<ModOp %s=%s>" % (shorthand, quoteattr(target))

        parts = ["<ModOp Type=%s GUID=%s" % (quoteattr(op), quoteattr(guid))]
        if path:
            parts.append(" Path=%s" % quoteattr(path))
        parts.append(">")
        return "".join(parts)

    @property
    def modio_url(self) -> str:
        return "https://mod.io/g/%s" % self.modio_name_id

    @property
    def modio_add_url(self) -> str:
        """The website's page for creating a mod for this game."""
        return "%s/m/add" % self.modio_url

    def documents_dir(self) -> Path:
        """The game's own folder under Documents -- its logs, and for Anno
        117 its mod profiles, its options file and a mods folder of its own
        -- whether or not it exists yet."""
        return places.documents() / self.documents_folder

    def display_path(self, path: str) -> str:
        """Convert a generated (1800-style) path into this game's convention."""
        if self.strips_values_prefix and path.startswith("/Values/"):
            return path[len("/Values") :]
        if self.strips_values_prefix and path == "/Values":
            return "/"
        return path

    def mod_id_problem(self, mod_id: str) -> str:
        """Why this game's loader would refuse ``mod_id``, or "" if it would not.

        Anno 117's loader checks the ID and refuses the mod outright -- its
        ``mod-loader.log`` says *Invalid ModID format. Please use only lowercase
        letters, numbers and dashes, e.g. "fancy-mod-creatorname"*.  Anno 1800
        never has: underscores and capitals are common there, and spaces and
        brackets are in published mods that load ("[Map] Lost World", "More
        Water": 7 of the 549 IDs in the bench's mods), so nothing about an
        1800 ID is a problem.
        """
        if self.key != ANNO_117 or _MOD_ID_117.match(mod_id or ""):
            return ""
        return tr(
            "Anno 117 refuses a Mod ID that is not lowercase letters, numbers "
            "and dashes, e.g. fancy-mod-authorname."
        )

    def usable_mod_id(self, text: str) -> str:
        """The nearest ID this game's loader would take, made from ``text``.

        For Anno 117 that is the slug in lowercase; for Anno 1800 the slug as
        it is, which is what a blank Mod ID has always been filled in with.
        """
        slug = slugify(text)
        return slug.lower() if self.key == ANNO_117 else slug


# What Anno 117's loader accepts as a ModID.
_MOD_ID_117 = re.compile(r"^[a-z0-9-]+$")


def slugify(text: str) -> str:
    """A ModID made out of words: anything but letters and digits is a dash."""
    slug = re.sub(r"[^A-Za-z0-9]+", "-", text or "").strip("-")
    return slug or "anno-mod"


GAMES = {
    ANNO_1800: Game(
        key=ANNO_1800,
        label="Anno 1800",
        asset_relpath=Path("data/config/export/main/asset/assets.xml"),
        categories=(
            "Cheat",
            "Addon",
            "Building",
            "Ship",
            "Gameplay",
            "Cosmetic",
            "Map",
            "Misc",
        ),
        modop_style="classic",
        strips_values_prefix=False,
        modio_game_id=4169,
        modio_name_id="anno-1800",
        in_game_browser=True,
        documents_folder="Anno 1800",
        install_folder="Anno 1800",
        exe="Anno1800.exe",
        xmltest_build="xmltest.exe",
        gui_relpath=Path("data/config/gui"),
    ),
    ANNO_117: Game(
        key=ANNO_117,
        label="Anno 117: Pax Romana",
        asset_relpath=Path("data/base/config/export/assets.xml"),
        categories=("Fix", "Content", "Gameplay", "Cosmetic", "Map", "Misc"),
        modop_style="shorthand",
        strips_values_prefix=True,
        modio_game_id=11358,
        modio_name_id="anno-117-pax-romana",
        documents_folder="Anno 117 - Pax Romana",
        install_folder="Anno 117 - Pax Romana",
        exe="Anno117.exe",
        xmltest_build="xmltest3.exe",
        gui_relpath=Path("data/base/config/gui"),
    ),
}

DEFAULT = ANNO_1800


def get(key: Optional[str]) -> Game:
    return GAMES.get(key or DEFAULT, GAMES[DEFAULT])


def documents_dir(key: Optional[str]) -> Optional[Path]:
    """:meth:`Game.documents_dir` of the game ``key`` names; None for a game
    the Studio does not know, rather than 1800's as :func:`get` would."""
    found = GAMES.get(key or "")
    return found.documents_dir() if found else None


def detect_from_path(path) -> str:
    """Guess the game from where the base asset file was extracted to.

    117 keeps assets under ``data/base/config/export``; 1800 uses the longer
    ``data/config/export/main/asset``.  Anything else falls back to 1800.
    """
    if not path:
        return DEFAULT
    parts = [p.lower() for p in Path(path).parts]
    joined = "/".join(parts)
    if "data/base/config/export" in joined:
        return ANNO_117
    if "config/export/main/asset" in joined:
        return ANNO_1800
    return DEFAULT
