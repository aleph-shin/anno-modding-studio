"""Declaring a mod's own options: what stands in the way of writing them,
and which of them its ModOps already read.

What a declaration *is* -- the block, read and written -- is
:mod:`.settings`, which everything may import. This is the part that says
things to the person declaring them, and reads the mod's ops to do it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, List, Optional

from . import games, modinfo, settings
from .i18n import tr
from .modop import SETTING

# Where a ModOp names a setting: a condition, on an op or a group, and the
# value a ``<ModValue>`` inserts.
_ASKED_IN = re.compile(r"\b(Condition|Insert)\s*=\s*(?:\"([^\"]*)\"|'([^']*)')", re.I)
# What, beside a name, makes it a number: a comparison, arithmetic.
_NUMBER_BEFORE = re.compile(r"(?:[=<>*+/-]|&lt;|&gt;|\bdiv|\bmod)\s*\Z")
_NUMBER_AFTER = re.compile(r"\A\s*(?:[=<>*+/-]|!=|&lt;|&gt;|div\b|mod\b)")


def problems(options) -> List[str]:
    """What stands in the way of writing ``options`` as a mod's: one line
    each, and none where they can be written."""
    found = []
    seen = set()
    for option in options:
        name = option.name
        if not name:
            found.append(tr("An option has no name."))
            continue
        if not settings.NAME.match(name):
            found.append(tr(
                "“%s” cannot name an option: it takes a letter or _, then "
                "letters, digits, - or _.", name,
            ))
            continue
        if name in seen:
            found.append(tr("Two options are called %s.", name))
        seen.add(name)
        if option.is_heading:
            continue
        if option.kind == settings.TOGGLE:
            if option.default.strip().lower() not in ("true", "false"):
                found.append(tr("%s is a toggle: its default is true or false.", name))
            if any(option.labels) and len(option.labels) != 2:
                found.append(tr("%s is a toggle: it is shown with two words or none.", name))
        elif option.kind == settings.ENUM:
            values = list(option.values)
            if not values or any(not value.strip() for value in values):
                found.append(tr("Every choice of %s needs a value.", name))
            elif len(set(values)) != len(values):
                found.append(tr("Two choices of %s have the same value.", name))
            elif option.default not in values:
                found.append(tr("The default of %s is not one of its choices.", name))
            labels = list(option.labels)
            if any(labels) and (len(labels) != len(values) or not all(label.strip() for label in labels)):
                found.append(tr("Each choice of %s is shown as something, or none is.", name))
        elif option.kind == settings.SLIDER:
            reach = option.range
            if reach is None:
                found.append(tr("The lowest, highest and step of %s are numbers.", name))
                continue
            low, high, step = reach
            if len(option.values) > 2:
                # The range shows a step of 0 as 1; the step written is the typed one.
                step = float(option.values[2])
            if not low < high or step <= 0:
                found.append(tr(
                    "%s runs from a lower number to a higher one, in steps above 0.", name
                ))
                continue
            try:
                default = float(option.default)
            except ValueError:
                found.append(tr("The default of %s is a number.", name))
                continue
            if not low <= default <= high:
                found.append(tr("The default of %s is between its lowest and highest.", name))
    return found


def named_by(loadout, origin: str, mod_id: str = "") -> List[str]:
    """The settings of its own a mod's ModOps read, by name, in the order
    they first come: ``$range``, and ``$<its ModID>.range`` as ``range``.
    Another mod's, named in full, are that mod's to declare."""
    own = "%s." % mod_id.strip() if mod_id and mod_id.strip() else None
    found: List[str] = []
    held = list(loadout.ops_of(origin)) + list(loadout.unread_of(origin))
    for one in sorted(held, key=lambda one: one.order):
        for name in SETTING.findall(one.source or ""):
            if own and name.startswith(own):
                name = name[len(own):]
            if "." in name or not name or name in found:
                continue
            found.append(name)
    return found


def mod_id_of(loadout, origin: str) -> str:
    """The ModID a mod's ModOps name their own settings by in full: a loaded
    mod's own, or the one the mod written here goes out under; none for a
    mod not loaded here."""
    record = loadout.mod(origin)
    if record is None:
        return ""
    if record is loadout.own and loadout.game == games.ANNO_117:
        return modinfo.own_mod_id(loadout)
    return record.mod_id


def undeclared(loadout, origin: str) -> List[str]:
    """The settings of its own a mod's ModOps read that it does not declare,
    in the order they first come."""
    offered = settings.read(settings.block_of(loadout, origin))
    return [
        name for name in named_by(loadout, origin, mod_id_of(loadout, origin))
        if offered.get(name) is None
    ]


def guessed_kind(loadout, origin: str, name: str) -> str:
    """What kind of option ``name`` looks like from how the mod's ModOps
    read it: a number -- compared, worked with, or inserted as a value --
    is a slider, and anything else a toggle, which a condition asks of
    whether it is on. A guess, for the person declaring it to correct."""
    mod_id = mod_id_of(loadout, origin)
    spelled = {name} | ({"%s.%s" % (mod_id, name)} if mod_id else set())
    held = list(loadout.ops_of(origin)) + list(loadout.unread_of(origin))
    for one in held:
        for asked in _ASKED_IN.finditer(one.source or ""):
            value = asked.group(2) if asked.group(2) is not None else asked.group(3)
            for found in SETTING.finditer(value):
                if found.group(1) not in spelled:
                    continue
                if (asked.group(1).lower() == "insert"
                        or _NUMBER_BEFORE.search(value[:found.start()])
                        or _NUMBER_AFTER.search(value[found.end():])):
                    return settings.SLIDER
    return settings.TOGGLE


# -- where an option is named ---------------------------------------------------
@dataclass(frozen=True)
class Named:
    """A setting named in some text: where, counted in characters of the
    text, and the name as it is written there, ``range`` or
    ``their-mod.range``."""

    start: int
    end: int
    name: str


def named_in(text: str) -> List[Named]:
    """Every setting ``text`` names where the loader reads one: in a
    condition, on an op or a group, and in what a ``<ModValue>`` inserts."""
    found: List[Named] = []
    for asked in _ASKED_IN.finditer(text or ""):
        group = 2 if asked.group(2) is not None else 3
        offset = asked.start(group)
        for one in SETTING.finditer(asked.group(group)):
            found.append(Named(offset + one.start(), offset + one.end(), one.group(1)))
    return found


@dataclass(frozen=True)
class Reference:
    """Which mod's option a name is, as the loader reads it from ``origin``'s
    ops: that mod, the option's own name, and how it is declared -- None
    where it is not."""

    origin: str
    name: str
    option: Optional[settings.Option]


def resolved(loadout, origin: str, name: str) -> Optional[Reference]:
    """The option ``name`` is, written in one of ``origin``'s ops: its own by
    a bare name or by its ModID, and a loaded mod's by that mod's ModID.
    None for a mod nothing here has that ModID of."""
    if "." in name:
        mod_id, _dot, bare = name.rpartition(".")
        if mod_id == mod_id_of(loadout, origin):
            origin, name = origin, bare
        else:
            layer = next((one for one in loadout.layers if one.mod_id and one.mod_id == mod_id), None)
            if layer is None:
                if not loadout.has_own_mod() or mod_id != mod_id_of(loadout, ""):
                    return None
                origin = ""
            else:
                origin = layer.origin
            name = bare
    return Reference(origin, name, settings.read(settings.block_of(loadout, origin)).get(name))


@dataclass(frozen=True)
class Use:
    """An op of a mod that reads one of its options: ``kind`` "op" or
    "unread", ``which`` its uid or its place, ``order`` where it is in the
    file, and what it is called."""

    kind: str
    which: Any
    order: int
    said: str


def uses(loadout, origin: str, name: str) -> List[Use]:
    """The ops of ``origin`` that read its option ``name``, by either of its
    names, in the order they are in the mod's file."""
    spelled = {name}
    mod_id = mod_id_of(loadout, origin)
    if mod_id:
        spelled.add("%s.%s" % (mod_id, name))
    found: List[Use] = []
    for op in loadout.ops_of(origin):
        if any(one.name in spelled for one in named_in(op.source)):
            found.append(Use("op", op.uid, op.order, op.summary or _first_tag(op.source)))
    for entry in loadout.unread_of(origin):
        if any(one.name in spelled for one in named_in(entry.source)):
            found.append(Use("unread", entry.order, entry.order, entry.label or _first_tag(entry.source)))
    return sorted(found, key=lambda use: use.order)


def asked_by(loadout, origin: str, source: str) -> List[str]:
    """The options of ``origin`` that an op's own condition asks -- of a
    group, the group's, and not those of the ops in it -- by name."""
    head = re.match(r"\s*<[^>]*>", source or "")
    found: List[str] = []
    for one in named_in(head.group(0) if head else ""):
        reference = resolved(loadout, origin, one.name)
        if reference is not None and reference.origin == origin and reference.name not in found:
            found.append(reference.name)
    return found


def _first_tag(source: str) -> str:
    head = re.match(r"\s*(<[^>]*>)", source or "")
    return head.group(1) if head else (source or "").strip()[:80]


def with_labels(loadout, origin: str, text: str) -> str:
    """``text`` -- what a row says of one of ``origin``'s ops -- with each
    declared option it names as its label, ``option “Shrine of Ceres”``
    for ``$my-mod.ceres``: what the mod's author called it, and not what
    the loader does.  A name nothing declares stays as it is, and so does
    every name in Anno 1800, which has none of them."""
    if loadout.game != games.ANNO_117 or "$" not in (text or ""):
        return text

    def labelled(found) -> str:
        reference = resolved(loadout, origin, found.group(1))
        if reference is None or reference.option is None:
            return found.group(0)
        # Translators: one of a mod's options, by its label, where a row
        # says what a group or a ModOp asks.
        return tr("option “%s”", reference.option.label or reference.option.name)

    return SETTING.sub(labelled, text)
