"""What a mod runs as code, and where the Studio's picture of it stops.

Mods run Lua two ways.  An asset can: an ``ActionExecuteScript`` whose
``<ScriptFileName>`` names a ``.lua`` file, run when a trigger fires -- long
established in Anno 1800, 1,051 such references in 31 mods of the mod.io
census.  And since Anno 117 a modinfo can: a ``Scripts`` block listing module
files and a snippet of Lua for each moment the loader offers -- ``Init`` after
the mods are loaded, ``Load`` when a game is, ``Unload``, ``Tick``.  Its docs
call that experimental, and say to expect changes; twelve of the census's 371
Anno 117 mods use it already, Ubisoft's own two examples among them.

The Studio does not read Lua and does not mean to -- writing it belongs in an
editor made for it.  What it can do is say which mods run code at all, since a
script can change anything while the game runs and none of that is in the
changes the Studio shows; say what each one runs and whether the files are
there; and find the file a ``ScriptFileName`` names.

Keys are matched without regard to case: every census mod writes ``Scripts``
and ``Modules``, the docs write ``scripts`` and ``modules``.  A hook nobody has
heard of yet is shown like the others -- the feature is young, and one that is
added should not need the Studio to learn it before it is seen.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from . import disk
from .i18n import join_list, tr, trn

# The field an ActionExecuteScript names its file in.
SCRIPT_FIELD = "ScriptFileName"
_IN_A_PAYLOAD = re.compile(r"<ScriptFileName>\s*([^<]*?)\s*</ScriptFileName>")
# The characters that turn text around where it is drawn: ``x\u202eaul.exe``
# is drawn as ``xexe.lua``.
_BIDI = re.compile(r"[\u061c\u200e\u200f\u202a-\u202e\u2066-\u2069]")


@dataclass
class Module:
    path: str
    # Whether the file is in the mod's folder; None where there is no folder
    # to look in.
    found: Optional[bool] = None


@dataclass
class Scripts:
    """A modinfo's ``Scripts`` block."""

    modules: List[Module] = field(default_factory=list)
    # (name, the Lua it runs) in the order the modinfo gives them.
    hooks: List[Tuple[str, str]] = field(default_factory=list)

    @property
    def missing(self) -> List[str]:
        return [module.path for module in self.modules if module.found is False]


def _get(mapping: dict, name: str):
    for key, value in mapping.items():
        if isinstance(key, str) and key.lower() == name:
            return value
    return None


def _clean(path: str) -> str:
    path = path.strip().replace("\\", "/")
    # ``./goods-amount.lua`` -- one census mod writes its module that way.
    while path.startswith(("./", "/")):
        path = path[2:] if path.startswith("./") else path[1:]
    return path


def declared(modinfo, folder=None) -> Optional[Scripts]:
    """The ``Scripts`` block of ``modinfo`` (a raw modinfo), or None for a mod
    without one.  ``folder`` is the mod's own, to look for its modules in."""
    if not isinstance(modinfo, dict):
        return None
    block = _get(modinfo, "scripts")
    if not isinstance(block, dict):
        return None
    found = Scripts()
    modules = _get(block, "modules")
    for path in modules if isinstance(modules, list) else []:
        if not isinstance(path, str) or not path.strip():
            continue
        path = _clean(path)
        there = (Path(folder) / path).is_file() if folder else None
        found.modules.append(Module(path, there))
    for key, value in block.items():
        if isinstance(key, str) and key.lower() != "modules" and isinstance(value, str) and value.strip():
            found.hooks.append((key, value.strip()))
    if not (found.modules or found.hooks):
        return None
    return found


def is_script_path(tag: str, value: str) -> bool:
    return tag == SCRIPT_FIELD and value.strip().lower().endswith(".lua")


def named_by(loadout) -> Dict[str, List[str]]:
    """The script files each mod's assets name, by origin, in first-seen order:
    what its changes set a ``ScriptFileName`` to or carry one in, and what its
    kept ops and unread ops carry, since those go out as written and run all the
    same."""
    found: Dict[str, List[str]] = {}

    def note(origin: str, path: str) -> None:
        path = _clean(path)
        if path.lower().endswith(".lua"):
            held = found.setdefault(origin, [])
            if path not in held:
                held.append(path)

    for change in loadout.changes.values():
        if change.path.rsplit("/", 1)[-1] == SCRIPT_FIELD and change.new_value:
            note(change.origin, change.new_value)
        for match in _IN_A_PAYLOAD.finditer(change.payload or ""):
            note(change.origin, match.group(1))
    for held in (loadout.ops, loadout.unread):
        for entry in held:
            for match in _IN_A_PAYLOAD.finditer(getattr(entry, "source", "") or ""):
                note(entry.origin, match.group(1))
    return found


def locate(loadout, path: str):
    """The mod that supplies the file at ``path``, as the game would find it:
    the last in load order among the mods that load and have it, since a later
    mod's file stands in front of an earlier one's.  The loadout's own mod is
    one of them, with the modules :func:`carry` puts in its export, found in
    its scripts folder.  None when no mod has it -- which may well mean the
    game's own, whose files the Studio does not list."""
    import dataclasses

    from . import dependencies

    path = _clean(path)
    if not path:
        return None
    gone = loadout.replaced()
    best = None
    for layer in dependencies.switched_on(loadout):
        if not layer.folder or layer.origin in gone:
            continue
        if (Path(layer.folder) / path).is_file():
            if best is None or layer.order >= best.order:
                best = layer
    folder = folder_of(loadout, "")
    if folder and loadout.has_own_mod():
        block = declared({"Scripts": block_of(loadout, "")}, folder)
        has_it = block is not None and any(
            module.found and module.path.lower() == path.lower() for module in block.modules
        )
        if has_it and (best is None or loadout.layer_rank("") >= loadout.layer_rank(best.origin)):
            best = dataclasses.replace(loadout.own_layer(), folder=folder)
    return best


def runs_code(loadout, named: Optional[Dict[str, List[str]]] = None) -> Dict[str, str]:
    """Which mods run scripts, by origin, and through what: "its modinfo",
    "its assets", or both."""
    found: Dict[str, str] = {}
    named = named_by(loadout) if named is None else named
    for layer in loadout.layers:
        how = []
        if declared(layer.declared) is not None:
            how.append("its modinfo")
        if named.get(layer.origin):
            how.append("its assets")
        if how:
            found[layer.origin] = " and ".join(how)
    how = []
    if declared({"Scripts": block_of(loadout, "")}) is not None:
        how.append("its modinfo")
    if named.get(""):
        how.append("its assets")
    if how:
        found[""] = " and ".join(how)
    return found


def _stays_inside(path: str) -> bool:
    """Whether a module's path, as :func:`_clean` leaves it, is one inside
    the folder it is read from: no ``..`` step and no drive or stream."""
    parts = path.split("/")
    return all(part not in ("", ".", "..") and ":" not in part for part in parts)


def carry(loadout, root, trouble: Optional[List[str]] = None) -> int:
    """Copy the modules of the loadout's own mod into its export at ``root``,
    from the folder the loadout keeps them in, each to the path its modinfo
    names.  How many were copied.  A module that is not there goes into
    ``trouble`` -- the modinfo will still ask the game for it -- or, without
    one, is raised."""
    import shutil

    # A loaded mod's Lua is in its folder, and comes across with what it
    # ships; only a mod the Studio writes whole has modules to copy in.
    if not loadout.own.written_whole:
        return 0
    folder = loadout.own.scripts_folder
    block = declared({"Scripts": block_of(loadout, "")}, folder or None)
    if block is None or not block.modules:
        return 0
    copied = 0
    for module in block.modules:
        # A path out of the mod -- ``..``, a drive -- would copy from outside
        # the scripts folder to outside the export, and the loadout naming
        # it may be somebody else's (security review N1).
        if not _stays_inside(module.path):
            why = tr("%s: not a path inside the mod, so it was not copied", module.path)
            if trouble is None:
                raise FileNotFoundError(why)
            trouble.append(why)
            continue
        source, target = (Path(folder) / module.path if folder else None), Path(root) / module.path
        if source is None or not source.is_file():
            if folder:
                why = tr("%(file)s: not in %(folder)s, so the mod will ask the game for a "
                         "file it does not have", file=module.path, folder=folder)
            else:
                why = tr("%s: no scripts folder is set for this mod, so the mod will ask "
                         "the game for a file it does not have", module.path)
            if trouble is None:
                raise FileNotFoundError(why)
            trouble.append(why)
            continue
        try:
            if source.resolve() != target.resolve():
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target)
            copied += 1
        except OSError as exc:
            if trouble is None:
                raise
            trouble.append("%s: %s" % (module.path, exc))
    return copied


def describe(layer, named: Optional[List[str]] = None) -> List[str]:
    """What one mod runs, in lines for its tooltip, or none for a mod that
    runs nothing.  Only what is cheap to know: this is asked for every mod row
    each time the list is drawn, so where each named file is found is left to
    :func:`locate`, asked when somebody wants to open one."""
    block = declared(layer.declared, layer.folder or None)
    named = list(named or [])
    if block is None and not named:
        return []
    lines = [
        tr("Runs scripts. What a script does happens while the game runs, and none "
           "of it is in the changes shown here.")
    ]
    if block is not None:
        for module in block.modules:
            if module.found is False:
                said = tr("module %s  (not in its folder)", module.path)
            else:
                # Translators: a Lua module the mod's modinfo lists, by its path.
                said = tr("module %s", module.path)
            lines.append("  " + said)
        for name, code in block.hooks:
            lines.append("  %s: %s" % (name, code if len(code) <= 80 else code[:77] + "…"))
    if named:
        shown = named[:6]
        if len(named) > len(shown):
            said = trn("its assets run %(files)s and %(n)s more",
                       "its assets run %(files)s and %(n)s more",
                       len(named) - len(shown), files=", ".join(shown))
        else:
            said = tr("its assets run %s", join_list(shown))
        lines.append("  " + said)
    return lines


_STATEMENTS = re.compile(r"[;\n]")
_SETS = re.compile(r"\s*([A-Za-z_]\w*)\s*=(?!=)")
LUA_WORDS = {"local", "return", "function", "if", "then", "end", "for", "while", "do"}


def sets(code: str) -> List[str]:
    """The globals a hook's snippet assigns, in order: every statement of the
    form ``Name = …``.  The docs' own form is ``SomeScript = require(…)``, and
    all twelve census mods write that; SirLocksley's also keep a handle as
    ``SirLocksleyPC03 = SpecialistFinderRC.PC03``, which is a global all the
    same.  Nothing else is read: ``local x = …`` is not a global, and
    ``A.b = …`` sets a field of one."""
    found: List[str] = []
    for statement in _STATEMENTS.split(code or ""):
        match = _SETS.match(statement)
        if match and match.group(1) not in LUA_WORDS and match.group(1) not in found:
            found.append(match.group(1))
    return found


def stem(path: str) -> str:
    """What ``require`` calls a module: its file name without ``.lua``."""
    name = _clean(path).rsplit("/", 1)[-1]
    return name[:-4] if name.lower().endswith(".lua") else name


# What two script mods can both claim.
MODULE = "module"
GLOBAL = "global"


@dataclass(frozen=True)
class Clash:
    """A module name, or a global, that more than one loading mod claims.

    A global holds one value, so the later mod's replaces the earlier one's.
    For a module it is likelier than certain: Lua's ``require`` keeps one
    module per name, but how the game finds a mod's module is not in the
    loader's public source, and it could look in the asking mod first.  Two
    census mods, Ship Finder and Harbor Arrivals, ship different files under
    one name; one test in game would settle it."""

    kind: str
    name: str
    # In load order, with what each has under that name: its module's file,
    # or the hook that sets the global.
    origins: Tuple[str, ...]
    where: Tuple[str, ...]
    # Every copy is the same file, byte for byte: the same library bundled
    # into two mods, where whichever is kept is the one both expect.
    same: bool = False

    @property
    def is_harmless(self) -> bool:
        return self.same

    def describe(self, name) -> str:
        """One line, with ``name(origin)`` supplying readable mod names."""
        listed = join_list(name(origin) for origin in self.origins)
        if self.kind == MODULE:
            if self.same:
                return tr(
                    "%(mods)s ship the same %(module)s.lua. One copy is "
                    "loaded, and it is the same code", mods=listed, module=self.name,
                )
            return tr(
                "%(mods)s each ship their own %(module)s.lua. Lua's require keeps one "
                "module per name, so one of them is likely to get the other's",
                mods=listed, module=self.name,
            )
        # Translators: a global is a Lua variable every script can see.
        return tr("%(mods)s each set the global %(name)s, so one overwrites the other's",
                  mods=listed, name=self.name)


def block_of(loadout, origin: str) -> dict:
    """The ``Scripts`` block of a mod in ``loadout``, as written: from its
    record's ``declared``, a loaded mod's modinfo or the own mod's blocks."""
    record = loadout.mod(origin)
    held = _get(record.declared, "scripts") if record is not None and isinstance(record.declared, dict) else None
    return dict(held) if isinstance(held, dict) else {}


def folder_of(loadout, origin: str) -> str:
    """Where a mod's module paths are read from: the folder a mod written
    here keeps its Lua in, or a loaded mod's own folder."""
    record = loadout.mod(origin)
    return (record.scripts_folder or record.folder) if record is not None else ""


def set_block(loadout, origin: str, block: dict) -> None:
    """Put ``block`` in as a mod's ``Scripts``, or take it out when empty.  In a
    loaded mod's modinfo it keeps its key and its place -- whatever case the
    author wrote it in -- so the file reads as it did."""
    block = dict(block or {})
    layer = loadout.mod(origin)
    if layer is None:
        return
    declared = dict(layer.declared or {})
    key = next((k for k in declared if isinstance(k, str) and k.lower() == "scripts"), None)
    if not block:
        declared.pop(key, None)
    elif key is None:
        declared["Scripts"] = block
    else:
        declared[key] = block
    layer.declared = declared


def _loading(loadout):
    """Every mod the game would run scripts for, in load order, as (origin,
    its block, the folder its modules are in)."""
    from . import dependencies

    gone = loadout.replaced()
    found = []
    layers = [layer for layer in dependencies.switched_on(loadout) if layer.origin not in gone]
    for layer in sorted(layers, key=lambda layer: layer.order):
        block = declared(layer.declared, layer.folder or None)
        if block is not None:
            found.append((layer.origin, block, layer.folder or ""))
    if loadout.has_own_mod():
        folder = folder_of(loadout, "")
        block = declared({"Scripts": block_of(loadout, "")}, folder or None)
        if block is not None:
            found.append(("", block, folder))
    return found


def _same_file(paths: List[str]) -> bool:
    if len(paths) < 2 or not all(paths):
        return False
    try:
        first = Path(paths[0]).read_bytes()
        return all(Path(path).read_bytes() == first for path in paths[1:])
    except OSError:
        return False


def clashes(loadout, instead: Optional[Tuple[str, dict, str]] = None) -> List[Clash]:
    """Module names and globals that two or more loading mods claim.

    A module is its file's stem, compared without case since the game's
    files are; a global is compared exactly, as Lua does.  One mod naming a
    thing twice is its own business.

    ``instead`` is ``(origin, block, folder)`` to count for that mod in place
    of what the loadout holds -- a block being edited, before it is kept."""
    modules: Dict[str, List[Tuple[str, str, str]]] = {}
    globals_: Dict[str, List[Tuple[str, str]]] = {}
    loading = _loading(loadout)
    if instead is not None:
        origin, raw, folder = instead
        block = declared({"Scripts": raw}, folder or None)
        at = next((i for i, held in enumerate(loading) if held[0] == origin), None)
        if at is not None:
            loading = loading[:at] + ([(origin, block, folder)] if block else []) + loading[at + 1:]
        elif block is not None:
            layer = loadout.layer(origin)
            # Where it would load: a mod of its own goes last, a loaded one by
            # its place -- off or not, since this is about keeping it.
            if layer is None:
                loading = loading + [(origin, block, folder)]
            else:
                ranks = {held[0]: loadout.layer_rank(held[0]) for held in loading}
                mine = loadout.layer_rank(origin)
                at = sum(1 for held in loading if ranks[held[0]] < mine)
                loading = loading[:at] + [(origin, block, folder)] + loading[at:]
    for origin, block, folder in loading:
        seen = set()
        for module in block.modules:
            key = stem(module.path).lower()
            if key and key not in seen:
                seen.add(key)
                path = str(Path(folder) / module.path) if folder and module.found else ""
                modules.setdefault(key, []).append((origin, path, stem(module.path)))
        named = set()
        for hook, code in block.hooks:
            for name in sets(code):
                if name not in named:
                    named.add(name)
                    globals_.setdefault(name, []).append((origin, hook))
    found: List[Clash] = []
    for key, held in modules.items():
        if len(held) > 1:
            found.append(Clash(
                MODULE, held[0][2], tuple(o for o, _, _ in held), tuple(p for _, p, _ in held),
                same=_same_file([p for _, p, _ in held]),
            ))
    for name, setters in globals_.items():
        if len(setters) > 1:
            found.append(Clash(GLOBAL, name, tuple(o for o, _ in setters), tuple(h for _, h in setters)))
    found.sort(key=lambda clash: (clash.is_harmless, clash.kind, clash.name.lower()))
    return found


def clashes_of(found: List[Clash], origin: str) -> List[Clash]:
    return [clash for clash in found if origin in clash.origins]


def describe_clashes(found: List[Clash], origin: str, name) -> List[str]:
    """What one mod's scripts share with other mods', for its tooltip."""
    lines = []
    for clash in clashes_of(found, origin):
        others = join_list(name(other) for other in clash.origins if other != origin)
        if clash.kind == MODULE and clash.same:
            said = tr("module %(module)s.lua is also %(mods)s's (the same file)",
                      module=clash.name, mods=others)
        elif clash.kind == MODULE:
            said = tr("module %(module)s.lua is also %(mods)s's", module=clash.name, mods=others)
        else:
            # Translators: a global is a Lua variable every script can see.
            said = tr("global %(name)s is also set by %(mods)s", name=clash.name, mods=others)
        lines.append("  " + said)
    return lines


def summarise(found: List[Clash]) -> str:
    """A phrase for the changes panel's conflicts line, or ""."""
    real = [clash for clash in found if not clash.is_harmless]
    if not real:
        return ""
    return trn("%(n)s script name shared", "%(n)s script names shared", len(real))


# The four moments the loader offers, in the order its docs give them.
HOOKS = ("Init", "Load", "Unload", "Tick")
# Where a new module goes in the mod, as Commuter Pier keeps its.
MODULE_FOLDER = "scripts"


def global_name(text: str) -> str:
    """A Lua global made out of a ModID: ``commuter-pier`` is ``CommuterPier``,
    which is what the census mods call theirs."""
    parts = [part for part in re.split(r"[^A-Za-z0-9]+", text or "") if part]
    name = "".join(part[:1].upper() + part[1:] for part in parts)
    if not name:
        return "MyScript"
    return "Mod" + name if name[0].isdigit() else name


def module_name(text: str) -> str:
    """A module's file stem made out of a ModID: lowercase, hyphenated."""
    name = "-".join(part for part in re.split(r"[^a-z0-9_]+", (text or "").lower()) if part)
    return name or "my-script"


def taken(loadout, origin: str, block: Optional[dict] = None) -> Tuple[set, set]:
    """The module stems (lowercased) and globals already used by any mod that
    loads -- including the one at ``origin``, whose ``block`` stands in for
    what the loadout holds when given."""
    stems, names = set(), set()
    loading = [held for held in _loading(loadout) if held[0] != origin]
    own = declared({"Scripts": block if block is not None else block_of(loadout, origin)})
    if own is not None:
        loading.append((origin, own, ""))
    for _, held, _ in loading:
        stems |= {stem(module.path).lower() for module in held.modules}
        for _, code in held.hooks:
            names |= set(sets(code))
    return stems, names


def suggest(loadout, origin: str, mod_id: str, block: Optional[dict] = None) -> Tuple[str, str]:
    """A module stem and a global for a new module of the mod at ``origin``,
    made from its ModID and numbered past anything a loading mod already
    uses -- the docs' "use unique names to not clash with other mods"."""
    stems, names = taken(loadout, origin, block)
    base_stem, base_name = module_name(mod_id), global_name(mod_id)
    count = 1
    while True:
        tail = "" if count == 1 else str(count)
        candidate = (base_stem + ("-" + tail if tail else ""), base_name + tail)
        if candidate[0].lower() not in stems and candidate[1] not in names:
            return candidate
        count += 1


def template(name: str, module: str, hooks=("Tick",)) -> str:
    """A new module, from the docs' template: a table with a function for each
    hook it is called from, returned for ``require`` to hand back."""
    lines = [
        "-- Loaded by the mod's modinfo: its Init runs %s = require(\"%s\")," % (name, module),
        "-- and each hook below is called from the hook of the same name.",
        "local %s = {}" % name,
        "",
    ]
    for hook in hooks:
        if hook != "Init":
            lines += ["function %s.%s() end" % (name, hook), ""]
    lines.append("return %s;" % name)
    return "\n".join(lines) + "\n"


def key_in(block: dict, name: str) -> str:
    """The key ``block`` already spells ``name`` with, or ``name``."""
    return next((k for k in block if isinstance(k, str) and k.lower() == name.lower()), name)


def _then(code: str, more: str) -> str:
    code = (code or "").strip()
    return more if not code else "%s; %s" % (code.rstrip(";").rstrip(), more)


def with_module(block: dict, path: str, name: str, hooks=("Tick",)) -> dict:
    """``block`` with a module added: its path under Modules, ``name =
    require(stem)`` added to Init, and ``name:Hook()`` to each hook asked for.
    What the hooks ran before still runs first."""
    block = dict(block or {})
    modules_key = key_in(block, "Modules")
    modules = [m for m in (block.get(modules_key) or []) if isinstance(m, str)]
    if path not in modules:
        modules.append(path)
    ordered: dict = {modules_key: modules}
    ordered.update({k: v for k, v in block.items() if k != modules_key})
    block = ordered
    init = key_in(block, "Init")
    block[init] = _then(block.get(init, ""), '%s = require("%s")' % (name, stem(path)))
    for hook in hooks:
        if hook == "Init":
            continue
        key = key_in(block, hook)
        block[key] = _then(block.get(key, ""), "%s:%s()" % (name, hook))
    return block


def may_open(path, folder) -> bool:
    """Whether the Studio may hand ``path`` to Windows to open as a script:
    a ``.lua`` file inside the mod folder ``folder``, with no character in its
    name that draws it as something else.

    The path comes from a mod -- a modinfo's ``Scripts.Modules``, an asset's
    ``ScriptFileName`` -- and opening a file is running it for any kind
    Windows runs: a module listed as ``tools.js`` or ``../x.cmd`` would have
    been run by one click on a menu item promising to open a script.  Only
    Lua opens, in whatever the user opens Lua with, and only from the mod.
    """
    if not path or not folder:
        return False
    path = Path(path)
    if path.suffix.lower() != ".lua" or _BIDI.search(str(path)):
        return False
    try:
        if path.resolve().suffix.lower() != ".lua" or not path.is_file():
            return False
    except OSError:
        return False
    return disk.inside(path, folder)


def files(layer, named: Optional[List[str]] = None, loadout=None) -> List[Path]:
    """The script files one mod runs that are on disk, to open: its modules,
    then what its assets name, wherever the game would find them.  Only what
    :func:`may_open` allows: a Lua file inside the mod it is found in."""
    found: List[Path] = []
    block = declared(layer.declared, layer.folder or None)
    if block is not None and layer.folder:
        for module in block.modules:
            target = Path(layer.folder) / module.path
            if module.found and may_open(target, layer.folder):
                found.append(target)
    for path in named or []:
        supplier = locate(loadout, path) if loadout is not None else None
        if supplier is not None:
            target = Path(supplier.folder) / _clean(path)
            if target not in found and may_open(target, supplier.folder):
                found.append(target)
    return found
