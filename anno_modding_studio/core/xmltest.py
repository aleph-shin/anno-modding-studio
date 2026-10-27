"""Ask the game's own mod loader what a stack of mods does, through xmltest.

``xmltest.exe`` is the mod loader's patching code on the command line.  It ships
with the *Anno Modding Tools* VS Code extension, one build per game, and it is
the closest thing to the game there is short of starting it: whatever the
Studio thinks a ModOp means, this is what the loader does with it.

The Studio does not ship it.  It uses the copy in the user's own installed
extension when there is one, as a second opinion and never as something it
needs -- see :mod:`core.loadercheck` and the proposal's section 11.  It was
written for the census (``tools/modio_loader.py`` still offers it there) and
moved here unchanged apart from where it looks.

Three things about it decide how it is used here, and each was measured rather
than read:

**Which build.**  The extension runs ``xmltest.exe`` for Anno 1800 and
``xmltest2.exe`` for Anno 117.  But ``xmltest2`` never adds an ``<Asset>``
written straight under ``<ModOps>`` -- Anno 117's own way of adding assets, and
105 of the census's mods use it -- while ``xmltest3.exe``, which ships beside
it unused, does.  So 117 gets ``xmltest3``.  All three print the same version
banner, so it says nothing about which is which.

**What a run reports.**  ``-c diff`` prints every asset the patch changed,
twice: the asset *after* patching, then *before*, each as an ``<Assets>`` list
after a ``##annodiff##`` marker.  An asset the patch creates is in the first
list only.  ``-p`` applies another mod first, and what it did counts as
"before" -- so :func:`apply_stack` applies a stack one mod at a time, each with
every mod ahead of it as ``-p``, and the stack's result is each asset as the
last mod to change it left it.  :func:`apply_whole` comes to the same in one
run: ``-c patch`` writes the whole patched file, and it is compared asset by
asset with the build's own output for no patch.

**Where ``-p`` looks.**  For the mod it is asked to apply first, xmltest reads
the patch at Anno 1800's path -- ``data/config/export/main/asset/assets.xml``
-- whichever build it is and whichever game the target is.  An Anno 117 mod
keeps its patch at ``data/base/config/export/assets.xml``, so ``-p`` found
nothing, said nothing, and every mod in an Anno 117 stack was applied to the
shipped file as though it were alone.  *Lower Aqueducts* ships two mods, and
the second sets a value the first already has: the Studio rightly leaves it
out of the second mod's export, and the loader, never having applied the
first, called that a difference.  So each copied mod gets a stub at the path
``-p`` reads, which does nothing but ``<Include>`` the real patch.

**Where a bare ``<Asset>`` goes.**  ``xmltest3`` adds one under
``/AssetList/Groups[last()]/Group[last()]/Assets[last()]``, and in Anno 117's
file since September 2026 the last top-level group has no ``<Assets>`` of its
own: the path matches nothing, the build warns, and the asset is not added.
The game adds it all the same -- measured in Anno 117 itself on 4 October 2026
with a probe mod loaded first, whose bare asset was in the file afterwards
though the last group had no ``<Assets>``.  Sixty-seven of the census's mods
write bare assets and add no group, and each read as differing from the
loader by exactly those assets.  So for Anno 117 every stack is applied on
top of :func:`_end_group`: an empty group at the end of the file, which is
what the loader's own documentation has a mod add first, and after which the
build does what the game does.  It holds no asset, so it is in no answer.

**Mod options.**  The loader resolves ``$option`` from the mod's settings in
game, and xmltest has no way to be given any: every one comes out as
``false()``.  So each mod is copied first with its options written in at the
values the Studio would read them with -- its modinfo defaults -- which keeps
the comparison like for like.
"""

from __future__ import annotations

import contextlib
import gzip
import hashlib
import json
import re
import shutil
import subprocess
import tempfile
import time
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from lxml import etree

from .. import __version__
from . import disk, games, modfolder, settings, versioning
from .i18n import STORED, tr, tr_noop

EXTENSIONS = Path.home() / ".vscode" / "extensions"
# Where each editor that runs VS Code extensions keeps them: VS Code, its
# Insiders build, VSCodium, and Cursor.
EDITORS = (".vscode", ".vscode-insiders", ".vscode-oss", ".cursor")
EXTENSION = "jakobharder.anno-modding-tools-*"
# The build to run again with when one crashes. The extension itself runs
# xmltest2 for Anno 117; the Studio runs xmltest3, which adds an <Asset> written
# straight under <ModOps> as the game evidently does (105 of 364 census mods
# write one) and xmltest2 does not -- but xmltest3 crashes outright on a few
# ModOps xmltest2 patches fine (docs/proposals/modloader-gaps-proposal.md,
# "Which build for Anno 117"). A crash is no answer at all; xmltest2's is one,
# short of any bare <Asset>, and the result says it was the fallback's.
FALLBACK = {"xmltest3.exe": "xmltest2.exe"}
# What the fallback is known to get wrong, said with every answer it gives
# (Applied.said_fallback). Measured on the three census mods xmltest3 crashes
# on: xmltest2 left a ModValue in an asset being added as written, and emptied
# a field merged with self::node() * 1.5 where the Studio has 45.  The first
# is settled as that build's fault and no open question between the two: the
# game was asked on 10 Oct 2026 and works such a mark out, as xmltest3 does
# (docs/design-notes.md, "Values a mod works out while patching").
MARK = "##annodiff##"
TIMEOUT = 600
# The files a ModOps patch can reach; everything else in a mod is left behind.
COPIED = (".xml", ".include", ".json", ".jsonc")
# The one place ``-p`` looks for an earlier mod's patch, whatever the game.
PREPATCH_RELPATH = games.get(games.ANNO_1800).asset_relpath

_PARSER = etree.XMLParser(huge_tree=True, resolve_entities=False, remove_comments=True)


class LoaderError(RuntimeError):
    pass


class Stopped(LoaderError):
    """A run given up on because its caller asked to stop: the build is
    killed rather than waited out."""

    def __init__(self) -> None:
        # Translators: follows "The game's loader could not check My Mod:".
        super().__init__(tr("stopped"))


# How often a run asks whether it is to stop, in seconds.
STOP_POLL = 0.25


# ----------------------------------------------------------- finding it
def _version(path: Path) -> Tuple[int, ...]:
    found = re.search(r"(\d+(?:\.\d+)*)$", path.name)
    return versioning.key(found.group(1)) if found else ()


def extension_folders(extra=None) -> List[Path]:
    """Every folder an installed copy of the extension could be under: each
    editor's, and ``extra`` -- a folder somebody named by hand, which may be
    the extensions folder or the extension itself."""
    home = Path.home()
    found = [home / editor / "extensions" for editor in EDITORS]
    if extra:
        found.insert(0, Path(extra))
    return found


def _installed(folders) -> List[Path]:
    """Every installed copy of the extension, oldest first."""
    found = []
    for folder in folders:
        folder = Path(folder)
        if folder.name.startswith(EXTENSION[:-1]) and folder.is_dir():
            found.append(folder)
        elif folder.is_dir():
            found.extend(path for path in folder.glob(EXTENSION) if path.is_dir())
    return sorted(found, key=_version)


def find_xmltest(game: str, extensions: Optional[Path] = None, extra=None) -> Optional[Path]:
    """The xmltest build for ``game`` in the newest installed extension:
    looked for in ``extensions`` alone when given, else in every editor's
    folder and ``extra``."""
    found = games.GAMES.get(game)
    if found is None:
        return None
    build = found.xmltest_build
    folders = [extensions] if extensions is not None else extension_folders(extra)
    for extension in reversed(_installed(folders)):
        candidate = extension / "external" / build
        if candidate.is_file():
            return candidate
    return None


@dataclass
class Loader:
    """One game's loader, as an installed extension ships it."""

    exe: Path
    game: str
    # The extension's own version, from its folder's name: what changes when
    # it updates, and so what says the loader may have.
    version: str
    # What the build says it is -- "modloader GU17.1" -- the same for all
    # three builds, and the nearest thing to the loader's own version.
    banner: str = ""

    @property
    def said(self) -> str:
        if self.banner:
            # Translators: %(banner)s is what the loader calls itself, such as
            # "modloader GU17.1".
            return tr_noop("%(banner)s from Anno Modding Tools %(version)s", context=STORED) % {
                "banner": self.banner, "version": self.version}
        return tr_noop("the mod loader from Anno Modding Tools %s", context=STORED) % self.version


_BANNER = re.compile(r"xmltest using (.+?)\s*$", re.MULTILINE)
_BANNERS: Dict[Tuple[str, int], str] = {}


def banner(exe: Path) -> str:
    """What a build says it is, from running it with nothing to do."""
    try:
        key = (str(exe), Path(exe).stat().st_mtime_ns)
    except OSError:
        return ""
    if key not in _BANNERS:
        try:
            run = subprocess.run([str(exe)], capture_output=True, timeout=20, check=False,
                                 creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            output = (run.stdout + run.stderr).decode("utf-8", errors="replace")
        except (OSError, subprocess.TimeoutExpired):
            output = ""
        found = _BANNER.search(output)
        _BANNERS[key] = found.group(1).strip() if found else ""
    return _BANNERS[key]


def find(game: str, extra=None) -> Optional[Loader]:
    """The loader for ``game`` in the newest installed extension, or None."""
    exe = find_xmltest(game, extra=extra)
    if exe is None:
        return None
    folder = exe.parent.parent
    version = ".".join(str(part) for part in _version(folder)) or folder.name
    return Loader(exe=exe, game=game, version=version, banner=banner(exe))


# ------------------------------------------------------- a newer one published
# Where VS Code gets the extension from, and so where a newer loader would
# come from: the Studio never updates it, only says one is out. The loader
# check runs on the extension's copy, and when that copy trails the game --
# xmltest3 in 2.3.1, from September 2025, crashes on three Anno 117 mods
# against the September 2026 game files -- updating it is the first thing to
# try. Nothing about the user goes with the question: it names the extension.
MARKETPLACE = "https://marketplace.visualstudio.com/_apis/public/gallery/extensionquery"
PUBLISHED_AS = "jakobharder.anno-modding-tools"


@dataclass
class Published:
    """The newest version of the extension the Marketplace has."""

    version: str
    # When it was published, as YYYY-MM-DD; "" when the Marketplace does not say.
    date: str = ""

    def is_newer_than(self, installed: str) -> bool:
        return versioning.key(self.version) > versioning.key(installed)


def newest_published(timeout: float = 15.0) -> Published:
    """Ask the Marketplace for the extension's newest version.

    Raises ``OSError`` (which ``urllib.error.URLError`` is) when it cannot be
    asked, or ``ValueError`` when the answer is not what the Marketplace says
    of an extension."""
    import urllib.request

    body = json.dumps({
        "filters": [{"criteria": [{"filterType": 7, "value": PUBLISHED_AS}]}],
        # The latest version only.
        "flags": 0x200,
    }).encode("utf-8")
    request = urllib.request.Request(MARKETPLACE, data=body, method="POST", headers={
        "Content-Type": "application/json",
        "Accept": "application/json;api-version=3.0-preview.1",
        "User-Agent": "AnnoModdingStudio/%s" % __version__,
    })
    with urllib.request.urlopen(request, timeout=timeout) as answer:
        return read_published(json.loads(answer.read().decode("utf-8")))


def read_published(answer: dict) -> Published:
    """The newest version in a Marketplace answer."""
    try:
        extension = answer["results"][0]["extensions"][0]
        latest = extension["versions"][0]
        version = str(latest["version"])
    except (KeyError, IndexError, TypeError) as exc:
        raise ValueError("the Marketplace did not say which version is newest") from exc
    if not versioning.key(version):
        raise ValueError("the Marketplace named a version with no number in it: %r" % version)
    return Published(version=version, date=str(latest.get("lastUpdated") or "")[:10])


# ------------------------------------------------------- mod options
_ATTRIBUTE = re.compile(r"""(\w+)(\s*=\s*)(["'])(.*?)\3""", re.DOTALL)
_VARIABLE = re.compile(r"\$([A-Za-z_][\w-]*(?:\.[A-Za-z_][\w-]*)?)")
_BARE = re.compile(r"^\s*(!?)\s*\$([A-Za-z_][\w.-]*)\s*$")
_NUMBER = re.compile(r"-?\d+(?:\.\d+)?")


def option_values(mod_root: Path) -> Dict[str, str]:
    """A mod's options at the values the Studio reads it with: its defaults."""
    offered = modfolder.metadata_of(mod_root).get("options")
    return offered.settled() if offered is not None else {}


def _literal(value: str) -> str:
    text = str(value).strip()
    if _NUMBER.fullmatch(text):
        return text
    if text.lower() in ("true", "false"):
        return text.lower() + "()"
    # XPath 1.0 has no escape inside a string, so a quote cannot survive.
    return "'%s'" % text.replace("'", "")


def _named(name: str, values: Dict[str, str]) -> Optional[str]:
    if name in values:
        return values[name]
    # ``$mod-id.option`` names the mod as well; a mod's own is all a copy knows.
    tail = name.rsplit(".", 1)[-1]
    return values.get(tail)


def substitute(text: str, values: Dict[str, str]) -> Tuple[str, int, List[str]]:
    """Write ``values`` into every ``$option`` in a patch's attributes.

    A condition that is only an option -- ``Condition="$start"`` or its
    negation -- asks whether that setting is on, so it becomes ``true()`` or
    ``false()`` the way :func:`core.settings.is_on` answers it.  Anywhere else
    an option is a value inside an expression and goes in as a literal.  One
    this mod does not declare is left as it is, which is what the loader does
    with it too.
    """
    replaced = 0
    unknown: List[str] = []

    def one_attribute(match: re.Match[str]) -> str:
        nonlocal replaced
        name, equals, quote, value = match.groups()
        if "$" not in value:
            return match.group(0)
        bare = _BARE.match(value)
        if bare and name.lower() == "condition":
            found = _named(bare.group(2), values)
            if found is None:
                unknown.append(bare.group(2))
                return match.group(0)
            replaced += 1
            held = settings.is_on(found) != bool(bare.group(1))
            return "%s%s%s%s%s" % (name, equals, quote, "true()" if held else "false()", quote)

        def one_variable(variable: re.Match[str]) -> str:
            nonlocal replaced
            found = _named(variable.group(1), values)
            if found is None:
                unknown.append(variable.group(1))
                return variable.group(0)
            replaced += 1
            literal = _literal(found)
            return literal if quote == '"' else literal.replace("'", '"')

        return "%s%s%s%s%s" % (name, equals, quote, _VARIABLE.sub(one_variable, value), quote)

    return _ATTRIBUTE.sub(one_attribute, text), replaced, unknown


# A ModID asked after in a condition, as Anno 117's loader finds one.
_MOD_TOKEN = re.compile(r"#[A-Za-z_][\w\-.]*")


def substitute_mods(text: str, present, game: str) -> Tuple[str, int]:
    """Write whether each mod a condition asks after is loaded into it.

    ``xmltest`` runs with no mods loaded as far as a condition can tell: it
    hands the loader an empty table, so every ``#mod-id`` is false whatever
    is in the stack -- measured. The Studio answers them from the mods it read,
    so for the two to be asked the same question each is written in as
    ``true()`` or ``false()``, which the loader takes in both games. Anno 1800
    reads everything after the ``#`` as one ModID; Anno 117 reads each
    ``#name`` in an expression. ``present`` is lowercased, as the Studio's is.
    """
    replaced = 0

    def one_attribute(match) -> str:
        nonlocal replaced
        name, equals, quote, value = match.groups()
        if name.lower() != "condition" or "#" not in value:
            return match.group(0)
        stripped = value.strip()
        negated = stripped.startswith("!")
        body = stripped[1:].strip() if negated else stripped
        if game == games.ANNO_117:
            def token(hit) -> str:
                nonlocal replaced
                replaced += 1
                return "true()" if hit.group(0)[1:].lower() in present else "false()"

            written = _MOD_TOKEN.sub(token, body)
        elif body.startswith("#"):
            # Anno 1800's loader wants a condition that finds a node: it took
            # ``true()`` for false, and a path starting with ``/`` on an op
            # with a GUID is read inside that asset. ``//AssetList`` finds the
            # file's root from anywhere, measured on a group and an op alike.
            replaced += 1
            held = (body[1:].strip().lower() in present) != negated
            return "%s%s%s%s%s" % (name, equals, quote, "//AssetList" if held else "!//AssetList", quote)
        else:
            return match.group(0)
        return "%s%s%s%s%s" % (name, equals, quote, ("!" if negated else "") + written, quote)

    return _ATTRIBUTE.sub(one_attribute, text), replaced


def _mod_root_of(path: Path, top: Path) -> Optional[Path]:
    """The nearest folder at or above ``path`` that carries a modinfo."""
    for folder in [path, *path.parents]:
        if modfolder.modinfo_of(folder) is not None:
            return folder
        if folder == top:
            break
    return None


def copy_with_options(source: Path, dest: Path, present=None, game: str = "") -> Tuple[int, List[str]]:
    """Copy a tree of mods with each file's options written in.

    Only the files a patch can reach come along; each takes the option values
    of the mod it sits in, which for a bundled mod is the bundled mod's own.
    ``present``, when given, is which mods count as loaded, written into the
    conditions that ask -- see :func:`substitute_mods`.
    """
    replaced = 0
    unknown: List[str] = []
    values_of: Dict[Optional[Path], Dict[str, str]] = {}
    for path in sorted(source.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in COPIED:
            continue
        target = dest / path.relative_to(source)
        target.parent.mkdir(parents=True, exist_ok=True)
        if path.suffix.lower() in (".json", ".jsonc"):
            shutil.copyfile(path, target)
            continue
        root = _mod_root_of(path.parent, source)
        if root not in values_of:
            values_of[root] = option_values(root) if root is not None else {}
        raw = path.read_bytes()
        text = raw.decode("utf-8", errors="surrogateescape")
        written = text
        if "$" in text and values_of[root]:
            written, count, missing = substitute(text, values_of[root])
            replaced += count
            unknown.extend(missing)
        if present is not None and "#" in written:
            written, _count = substitute_mods(written, present, game)
        if written is text:
            target.write_bytes(raw)
            continue
        target.write_bytes(written.encode("utf-8", errors="surrogateescape"))
    return replaced, sorted(set(unknown))


# ------------------------------------------------------- running it
_LOG = re.compile(r"\[(warning|error|critical)\]\s*(?:xmltest:\s*)?(.*?)\s*(?:\(([^()]*?):(\d+)\))?\s*$")


def _assets(section: str) -> Dict[str, Any]:
    """The diff's assets by GUID -- where a mod repeats one, not the game's.

    A mod can add two assets of one GUID; *Harborlife* does, among ninety
    triggers added in a single op.  xmltest prints one of them, the last in the
    file, while the loader's own look-up takes the *first* match -- so a later
    op aimed at that GUID edits the asset the diff does not show (both measured;
    see ``test_modio_loader``).  Nothing here can mend that, so a unit whose mod
    repeats a GUID reads as differing from the Studio however faithful the
    reading is.  The round trip is still sound: the same document on both sides
    gives the same diff.
    """
    start, end = section.find("<Assets>"), section.rfind("</Assets>")
    if start < 0 or end < 0:
        raise LoaderError("an xmltest diff without its <Assets> list")
    try:
        root = etree.fromstring(section[start : end + len("</Assets>")].encode("utf-8"), _PARSER)
    except etree.XMLSyntaxError as exc:
        raise LoaderError("xmltest printed a diff that does not parse: %s" % exc) from None
    found = {}
    for asset in root:
        if not isinstance(asset.tag, str):
            continue
        guid = (asset.findtext("Values/Standard/GUID") or "").strip()
        if guid:
            found[guid] = asset
    return found


def parse_diff(output: str) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """``(after, before)``: each changed asset as the patch left it and as it was."""
    parts = output.split(MARK)
    if len(parts) < 3:
        raise LoaderError("xmltest printed no diff")
    return _assets(parts[1]), _assets(parts[2])


def parse_log(output: str) -> Counter:
    """Every warning and error the loader raised, like ones counted together."""
    found: Counter = Counter()
    for line in output.splitlines():
        match = _LOG.search(line)
        if match:
            level, message = match.group(1), match.group(2)
            message = re.sub(r"'[^']*'|\"[^\"]*\"", "'...'", message)
            found["%s: %s" % (level, re.sub(r"\d+", "#", message)[:160])] += 1
    return found


def _warnings(output: str) -> List[str]:
    """The warnings and errors a run raised, as the loader wrote them."""
    found = []
    for line in output.splitlines():
        match = _LOG.search(line)
        if match:
            found.append("%s: %s" % (match.group(1), match.group(2)))
    return found


@dataclass
class Applied:
    """What the loader made of a stack of mods."""

    # guid -> the asset as the last mod to change it left it; None once it can
    # no longer be found by its GUID. The diff cannot say which of two things
    # that was: xmltest pairs "after" with "before" by GUID, so an asset a mod
    # strips of Values/Standard -- Enbesa Quest Skip does it to eighty quests --
    # is missing from "after" exactly as one it deletes is.
    assets: Dict[str, Any] = field(default_factory=dict)
    log: Counter = field(default_factory=Counter)
    runs: int = 0
    failures: List[str] = field(default_factory=list)
    options_written: int = 0
    options_unknown: List[str] = field(default_factory=list)
    seconds: float = 0.0
    # Each mod's own run, by its folder under the root it was copied from:
    # the GUIDs that run changed, and the warnings and errors it raised as
    # the loader wrote them -- "No matching node for Path ..." and the like.
    changed_by: Dict[str, List[str]] = field(default_factory=dict)
    # The last run's assets as they were before its mod ran -- with every mod
    # ahead of it applied -- by GUID: what that mod was handed.
    before: Dict[str, Any] = field(default_factory=dict)
    warnings_by: Dict[str, List[str]] = field(default_factory=dict)
    # Answered from :func:`apply_whole`'s cache, without a run.
    cached: bool = False
    # The runs the build crashed on and :data:`FALLBACK` answered instead, by
    # folder -- "" for a whole stack run at once.
    fallback: List[str] = field(default_factory=list)

    def said_fallback(self, exe: Path) -> str:
        """A sentence for a result some of which is the fallback build's, or ""."""
        if not self.fallback:
            return ""
        # Translators: %(build)s and %(other)s are program files, such as
        # xmltest3.exe and xmltest2.exe.
        return tr("%(build)s crashed, so %(other)s answered instead: it does not add an <Asset> "
                  "written straight under <ModOps>, and leaves some ModValues unworked, so a "
                  "difference there may be that build's", build=Path(exe).name,
                  other=FALLBACK.get(Path(exe).name.lower(), "xmltest2.exe"))


def _reachable_by_prepatch(folder: Path, relpath: Path) -> None:
    """Give a copied mod a stub where ``-p`` looks, including its real patch."""
    if relpath == PREPATCH_RELPATH:
        return
    stub = folder / PREPATCH_RELPATH
    if stub.exists():
        return
    # From the stub's folder up to the mod's root, then down to the patch.
    up = "../" * len(PREPATCH_RELPATH.parent.parts)
    stub.parent.mkdir(parents=True, exist_ok=True)
    stub.write_text(
        '<ModOps><Include File="%s%s"/></ModOps>' % (up, relpath.as_posix()), encoding="utf-8"
    )


# What :func:`_end_group` adds: the snippet the loader's documentation gives
# for use before any bare <Asset>.
END_GROUP = '<ModOps><ModOp Add="/AssetList/Groups[last()]"><Group><Assets /></Group></ModOp></ModOps>'


# The folder it is applied from, which is what the loader calls it in what it
# says: a file with no <Groups> to add to -- a test's -- gets a warning under
# this name, which is nothing about the mods asked after.
END_GROUP_NAME = "ams-end-group"


def _said_of_the_stack(said: str) -> str:
    """What a run said, without what it said about :func:`_end_group`."""
    mark = " %s: " % END_GROUP_NAME
    return "\n".join(line for line in said.splitlines() if mark not in line)


def _end_group(work: Path, game: str) -> List[str]:
    """The arguments that apply an empty group at the end of the file ahead
    of a stack, for the game whose build needs one to add a bare ``<Asset>``
    as the game does -- see the module's docstring -- and none for any other.
    """
    if game != games.ANNO_117:
        return []
    folder = work / END_GROUP_NAME
    patch = folder / PREPATCH_RELPATH
    if not patch.is_file():
        patch.parent.mkdir(parents=True, exist_ok=True)
        patch.write_text(END_GROUP, encoding="utf-8")
    return ["-p", str(folder)]


def apply_stack(
    exe: Path,
    target: Path,
    folders: Sequence[Path],
    root: Path,
    game: str,
    slot=None,
    timeout: float = TIMEOUT,
    only_last: bool = False,
    present=None,
    should_stop: Optional[Callable[[], bool]] = None,
) -> Applied:
    """Apply ``folders`` -- mods under ``root``, in load order -- with the loader.

    ``root`` is copied first, with options written in, so every folder must be
    inside it.  ``slot`` is held around each run: an Anno 1800 run holds about
    1.5 GB, and a machine running a unit per core cannot afford one each.
    ``should_stop``: see :func:`_run`; :class:`Stopped` is raised.
    """
    started = time.monotonic()
    applied = Applied()
    relpath = games.get(game).asset_relpath
    work = Path(tempfile.mkdtemp(prefix="ams-xt-"))
    try:
        copy = work / "m"
        applied.options_written, applied.options_unknown = copy_with_options(root, copy, present, game)
        stack = [copy / Path(folder).relative_to(root) for folder in folders]
        patched = [folder for folder in stack if (folder / relpath).is_file()]
        for folder in patched[:-1]:
            _reachable_by_prepatch(folder, relpath)
        for position, folder in enumerate(patched):
            # ``only_last``: one run, the last mod with every mod ahead of it
            # applied first. What the stack leaves the assets that last mod
            # changes is all a check of that one mod needs, and one run with
            # thirty-four ``-p`` is seconds where thirty-five runs are minutes.
            if only_last and position < len(patched) - 1:
                continue
            args = [str(exe), "-v", "-c", "diff", *_end_group(work, game)]
            for earlier in patched[:position]:
                args += ["-p", str(earlier)]
            args += ["-m", str(folder), str(target), str(folder / relpath)]
            where = folder.relative_to(copy).as_posix()
            with slot if slot is not None else contextlib.nullcontext():
                try:
                    run = _run(args, timeout, should_stop)
                    instead = fallback_for(exe) if crashed(run) else None
                    if instead is not None:
                        run = _run([str(instead)] + args[1:], timeout, should_stop)
                        applied.fallback.append(where)
                except subprocess.TimeoutExpired:
                    applied.failures.append("%s: timed out" % folder.relative_to(copy))
                    continue
            applied.runs += 1
            output = run.stdout.decode("utf-8", errors="replace")
            said = _said_of_the_stack(output + "\n" + run.stderr.decode("utf-8", errors="replace"))
            applied.log.update(parse_log(said))
            applied.warnings_by[where] = _warnings(said)
            if run.returncode != 0:
                applied.failures.append("%s: exit %d" % (folder.relative_to(copy), run.returncode))
                continue
            try:
                after, before = parse_diff(output)
            except LoaderError as exc:
                applied.failures.append("%s: %s" % (folder.relative_to(copy), exc))
                continue
            applied.assets.update(after)
            applied.changed_by[where] = sorted(set(after) | set(before))
            applied.before = before
            for guid in before:
                if guid not in after:
                    applied.assets[guid] = None
    finally:
        shutil.rmtree(work, ignore_errors=True)
    applied.seconds = round(time.monotonic() - started, 1)
    return applied


# ------------------------------------------- a whole stack in one run
#
# :func:`apply_stack` runs a stack of n mods as n runs, the k-th with the k-1
# ahead of it as ``-p``, because ``-c diff`` shows only what its last mod did.
# Each run patches everything ahead of it again, so the cost grows with the
# square of the stack: *Item Overhaul (Kurila)*, twelve mods, went from 1.7 s
# for its first run to 26 s for its last, and the census's big bundles ran into
# its hour. :func:`apply_whole` runs the stack once, writes the whole patched
# file with ``-c patch``, and finds what changed by comparing it asset by asset
# with the same build's output for no patch at all: 1 s to run, under 2 s to
# compare, measured on Anno 1800's 284 MB.

# What a cached answer is keyed on besides the stack. 2: answers keep which
# runs :data:`FALLBACK` answered, and a crash kept under 1 is asked again.
WHOLE_FORMAT = 2
_ASSET_TAG = re.compile(rb"<(/?)Asset>")
_LEADING_GUID = re.compile(
    rb"\A<Asset>\s*(?:<Template>[^<]*</Template>\s*)?<Values>\s*<Standard>\s*<GUID>\s*(\d+)\s*</GUID>"
)
_base_memo: Dict[tuple, Dict[bytes, str]] = {}


def _top_assets(data: bytes):
    """Each top-level ``<Asset>``'s span. Nested ones are values --
    ``<Asset>114437</Asset>`` in an unlock list -- and open and close inside."""
    depth = 0
    start = 0
    for match in _ASSET_TAG.finditer(data):
        if match.group(1):
            depth -= 1
            if depth == 0:
                yield start, match.end()
        else:
            if depth == 0:
                start = match.start()
            depth += 1


def _digest(chunk: bytes) -> bytes:
    return hashlib.blake2b(chunk, digest_size=16).digest()


def _guid_of(chunk: bytes) -> str:
    found = _LEADING_GUID.match(chunk)
    if found:
        return found.group(1).decode("ascii")
    try:
        element = etree.fromstring(chunk, _PARSER)
    except etree.XMLSyntaxError:
        return ""
    return (element.findtext("Values/Standard/GUID") or "").strip()


def _identity(path: Path) -> tuple:
    stat = path.stat()
    return str(path.resolve()), stat.st_size, stat.st_mtime_ns


def _run(args, timeout: float, should_stop: Optional[Callable[[], bool]] = None):
    """One run of a build, as ``subprocess.run`` gives it.

    ``should_stop`` is asked every :data:`STOP_POLL` seconds while it runs: an
    Anno 1800 stack of a hundred mods is a minute and more, and a check the
    loadout has moved on from used to hold the loader's work folder all that
    while. When it says so, the build is killed and :class:`Stopped` raised.
    """
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    if should_stop is None:
        return subprocess.run(args, capture_output=True, timeout=timeout, check=False, creationflags=flags)
    if should_stop():
        raise Stopped()
    deadline = time.monotonic() + timeout
    with subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, creationflags=flags) as process:
        while True:
            left = deadline - time.monotonic()
            try:
                out, err = process.communicate(timeout=max(0.0, min(STOP_POLL, left)))
            except subprocess.TimeoutExpired:
                if left <= STOP_POLL:
                    process.kill()
                    out, err = process.communicate()
                    raise subprocess.TimeoutExpired(args, timeout, out, err) from None
                if should_stop():
                    process.kill()
                    process.communicate()
                    raise Stopped() from None
                continue
            return subprocess.CompletedProcess(args, process.returncode, out, err)


def crashed(run) -> bool:
    """Whether a run ended in a crash rather than an answer: a Windows
    exception code (0xC0000005 access violation, 0xC00000FD stack overflow),
    or a signal elsewhere -- not a loader that said no."""
    return run is not None and (run.returncode >= 0xC0000000 or run.returncode < 0)


def fallback_for(exe: Path) -> Optional[Path]:
    """The build beside ``exe`` to run again with when it crashes, or None."""
    other = FALLBACK.get(Path(exe).name.lower())
    if other is None:
        return None
    found = Path(exe).parent / other
    return found if found.is_file() else None


def _base_assets(exe: Path, target: Path, game: str, slot, timeout: float,
                 cache: Optional[Path], should_stop: Optional[Callable[[], bool]] = None) -> Dict[bytes, str]:
    """Each asset of ``target`` as this build writes it unpatched: digest -> GUID.

    Its own output, not the file: it writes the file back out in its own way,
    and a comparison with the original would find every asset changed.

    Kept in the cache as gzipped JSON, each digest in hex.  It was a pickle,
    and reading a pickle runs whatever it says: anyone able to write into the
    cache folder could have had code run as the user.
    """
    key = (_identity(exe), _identity(target), game, WHOLE_FORMAT)
    if key in _base_memo:
        return _base_memo[key]
    stored = None
    if cache is not None:
        stored = cache / "base" / ("%s.json.gz" % hashlib.sha1(repr(key).encode()).hexdigest())
        if stored.is_file():
            try:
                _base_memo[key] = _base_read(stored)
                return _base_memo[key]
            except (OSError, ValueError, TypeError, AttributeError, EOFError):
                pass
    relpath = games.get(game).asset_relpath
    work = Path(tempfile.mkdtemp(prefix="ams-xt-"))
    try:
        mod = work / "empty"
        (mod / relpath).parent.mkdir(parents=True)
        (mod / relpath).write_text("<ModOps></ModOps>", encoding="utf-8")
        out = work / "base.xml"
        with slot if slot is not None else contextlib.nullcontext():
            run = _run([str(exe), "-c", "patch", "-m", str(mod), str(target), str(mod / relpath),
                        "-o", str(out)], timeout, should_stop)
            if run.returncode != 0 or not out.is_file():
                raise LoaderError("xmltest could not write %s back out unpatched (exit %d)"
                                  % (target.name, run.returncode))
            data = out.read_bytes()
        found = {}
        for start, end in _top_assets(data):
            chunk = data[start:end]
            found[_digest(chunk)] = _guid_of(chunk)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    _base_memo[key] = found
    if stored is not None:
        stored.parent.mkdir(parents=True, exist_ok=True)
        disk.write_bytes(stored, gzip.compress(json.dumps(
            {digest.hex(): guid for digest, guid in found.items()}).encode("utf-8")))
    return found


def _base_read(stored: Path) -> Dict[bytes, str]:
    """What :func:`_base_assets` kept in ``stored``; ValueError, TypeError or
    AttributeError for anything that is not a map of hex digests to GUIDs."""
    raw = json.loads(gzip.decompress(stored.read_bytes()))
    return {bytes.fromhex(digest): str(guid) for digest, guid in raw.items()
            if isinstance(guid, str)}


def changed_assets(data: bytes, base: Dict[bytes, str]) -> Dict[str, Any]:
    """What a patched file changed, by GUID, as :attr:`Applied.assets` has it.

    An asset whose bytes are not among the unpatched file's is new or changed,
    and is taken as it stands -- the last of a GUID the file repeats, as the
    diff prints it. One of the unpatched file's that is gone and did not come
    back changed is None: removed, or no longer found by its GUID.
    """
    assets: Dict[str, Any] = {}
    seen = set()
    for start, end in _top_assets(data):
        chunk = data[start:end]
        digest = _digest(chunk)
        if digest in base:
            seen.add(digest)
            continue
        try:
            element = etree.fromstring(chunk, _PARSER)
        except etree.XMLSyntaxError:
            continue
        guid = (element.findtext("Values/Standard/GUID") or "").strip()
        if guid:
            assets[guid] = element
    for digest, guid in base.items():
        if guid and digest not in seen and guid not in assets:
            assets[guid] = None
    return assets


# The minute an export was written, in the Studio's own header comment: the
# loader drops comments, and a cache that counted it would never be asked the
# same question twice about an export.
_STAMP = re.compile(rb"(Generated by [^<>\n]*? on )\d{4}-\d{2}-\d{2} \d{2}:\d{2}\.")


def _files_digest(folder: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(folder.rglob("*")):
        if path.is_file():
            data = path.read_bytes()
            if path.suffix.lower() == ".xml":
                data = _STAMP.sub(rb"\1-.", data, count=1)
            digest.update(path.relative_to(folder).as_posix().encode("utf-8") + b"\0")
            digest.update(hashlib.sha256(data).digest())
    return digest.hexdigest()


def _stored(applied: Applied) -> dict:
    return {
        "assets": {guid: None if element is None else etree.tostring(element, encoding="unicode")
                   for guid, element in applied.assets.items()},
        "log": dict(applied.log),
        "runs": applied.runs,
        "failures": applied.failures,
        "options_written": applied.options_written,
        "options_unknown": applied.options_unknown,
        "seconds": applied.seconds,
        "fallback": applied.fallback,
    }


def _restored(stored: dict) -> Applied:
    return Applied(
        assets={guid: None if text is None else etree.fromstring(text.encode("utf-8"), _PARSER)
                for guid, text in stored["assets"].items()},
        log=Counter(stored["log"]),
        runs=0,
        failures=list(stored["failures"]),
        options_written=stored["options_written"],
        options_unknown=list(stored["options_unknown"]),
        cached=True,
        fallback=list(stored.get("fallback") or []),
    )


def apply_whole(
    exe: Path,
    target: Path,
    folders: Sequence[Path],
    root: Path,
    game: str,
    slot=None,
    timeout: float = TIMEOUT,
    present=None,
    cache: Optional[Path] = None,
    should_stop: Optional[Callable[[], bool]] = None,
) -> Applied:
    """:func:`apply_stack`'s answer from one run of the whole stack.

    What each asset ends up as, and what the loader said, but not which mod
    changed it: :attr:`Applied.changed_by` and :attr:`Applied.before` stay
    empty, and each mod's warnings come in the one run's output. A run that
    fails is done again mod by mod, so the failure is put down to its mod.

    ``cache``, a folder: the answer is kept there under a digest of exactly
    what xmltest is given -- the build, the target, every file of the copied
    stack -- so a stack it has seen before is not run again. The census reads
    the same mods as shipped at every commit, and most exports come out the
    same from one commit to the next.
    """
    started = time.monotonic()
    relpath = games.get(game).asset_relpath
    work = Path(tempfile.mkdtemp(prefix="ams-xt-"))
    try:
        copy = work / "m"
        written, unknown = copy_with_options(root, copy, present, game)
        stack = [copy / Path(folder).relative_to(root) for folder in folders]
        patched = [folder for folder in stack if (folder / relpath).is_file()]
        if not patched:
            return Applied(options_written=written, options_unknown=unknown)
        for folder in patched[:-1]:
            _reachable_by_prepatch(folder, relpath)
        stored = None
        if cache is not None:
            # What the stack is applied on top of is part of what xmltest is
            # given. Only Anno 117's key moves for it: Anno 1800's is the one
            # its kept answers were filed under.
            on_top = (END_GROUP,) if _end_group(work, game) else ()
            key = hashlib.sha256(repr((
                WHOLE_FORMAT, game, *on_top, _identity(exe), _identity(target),
                [folder.relative_to(copy).as_posix() for folder in patched],
                _files_digest(copy),
            )).encode("utf-8")).hexdigest()
            stored = cache / "runs" / key[:2] / ("%s.json.gz" % key)
            if stored.is_file():
                try:
                    applied = _restored(json.loads(gzip.decompress(stored.read_bytes())))
                    applied.seconds = round(time.monotonic() - started, 1)
                    return applied
                except (OSError, ValueError, KeyError, EOFError, etree.XMLSyntaxError):
                    pass
        base = _base_assets(exe, target, game, slot, timeout, cache, should_stop)
        last = patched[-1]
        out = work / "patched.xml"
        args = [str(exe), "-v", "-c", "patch", *_end_group(work, game)]
        for earlier in patched[:-1]:
            args += ["-p", str(earlier)]
        args += ["-m", str(last), str(target), str(last / relpath), "-o", str(out)]
        applied = Applied(options_written=written, options_unknown=unknown)
        with slot if slot is not None else contextlib.nullcontext():
            try:
                run = _run(args, timeout, should_stop)
            except subprocess.TimeoutExpired:
                run = None
            instead = fallback_for(exe) if crashed(run) else None
            if instead is not None:
                # Compared with the fallback's own output for no patch: the
                # two builds need not write the file alike.
                base = _base_assets(instead, target, game, None, timeout, cache, should_stop)
                try:
                    run = _run([str(instead)] + args[1:], timeout, should_stop)
                except subprocess.TimeoutExpired:
                    run = None
                applied.fallback.append("")
            # Read while the slot is held: the file is as big as the target,
            # and one per running unit at once is more than a machine has.
            if run is not None and run.returncode == 0 and out.is_file():
                applied.assets = changed_assets(out.read_bytes(), base)
        if run is None or run.returncode != 0 or not out.is_file():
            # Mod by mod instead, which says whose failure it is.
            applied = apply_stack(exe, target, folders, root, game, slot, timeout, present=present,
                                  should_stop=should_stop)
        else:
            applied.runs = 1
            said = _said_of_the_stack(run.stdout.decode("utf-8", errors="replace") + "\n"
                                      + run.stderr.decode("utf-8", errors="replace"))
            applied.log.update(parse_log(said))
            applied.warnings_by[last.relative_to(copy).as_posix()] = _warnings(said)
        applied.seconds = round(time.monotonic() - started, 1)
        if stored is not None and not any("timed out" in failure for failure in applied.failures):
            stored.parent.mkdir(parents=True, exist_ok=True)
            disk.write_bytes(stored, gzip.compress(json.dumps(_stored(applied)).encode("utf-8")))
        return applied
    finally:
        shutil.rmtree(work, ignore_errors=True)
