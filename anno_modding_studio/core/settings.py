"""A mod's own options, as its ``modinfo.json`` declares them.

Some mods are configurable.  The game shows their options in its mod menu and
the mod's ModOps read the chosen values back: ``Condition="$gates_nomaintenance
costs"`` runs an op only when a toggle is on, and ``<ModValue Insert="self::node()
* $gatecostfactor"/>`` computes a value from a slider.

Read against the shipped file alone, none of that can be answered -- which is
why every op in Custom Walls and Gates used to come in as "a condition I cannot
check".  But the options are *declared*, with their types and their defaults, so
the answer is right there in the mod folder next to the ModOps that ask for it.

Reading them is also what lets the Studio offer them.  A mod's options are a
question its author meant somebody to answer, and answering it here rather than
in the game's menu means the change list shows what that answer does.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional

# Option kinds a modinfo.json declares. Anything else is read as a plain value.
TOGGLE = "toggle"
SLIDER = "slider"
ENUM = "enum"
# A value typed in rather than picked: what iModYourAnno calls a SimpleValue.
TEXT = "text"

# What a toggle-ish value has to say to count as off.
_OFF = frozenset({"", "0", "false", "no", "off"})


@dataclass(frozen=True)
class Option:
    """One setting a mod offers, and what it may be set to."""

    name: str
    label: str = ""
    kind: str = ENUM
    default: str = ""
    values: tuple = ()
    labels: tuple = ()
    # What the author says it does, beyond its label, where they say anything.
    description: str = ""

    @property
    def is_heading(self) -> bool:
        """Some mods declare a dummy option purely to head a section.

        An enum whose only value is its own default, and that default is a rule
        of dashes: nothing to choose, so nothing to show as a choice.
        """
        return (
            self.kind == ENUM
            and len(self.values) <= 1
            and not self.default.strip("- ")
        )

    @property
    def range(self):
        """``(low, high, step)`` for a slider, or None."""
        if self.kind != SLIDER or len(self.values) < 2:
            return None
        try:
            low, high = float(self.values[0]), float(self.values[1])
            step = float(self.values[2]) if len(self.values) > 2 else 1.0
        except (TypeError, ValueError):
            return None
        return (low, high, step or 1.0)

    def label_for(self, value: str) -> str:
        """The author's own wording for one of the values, when there is one."""
        if self.kind == TOGGLE:
            # Two labels, "yes" first, matching how the game lists them.
            wanted = 0 if is_on(value) else 1
            return self.labels[wanted] if len(self.labels) > wanted else value
        if value in self.values:
            position = list(self.values).index(value)
            if len(self.labels) > position:
                return self.labels[position]
        return value


@dataclass
class ModOptions:
    """Everything a mod offers, in the order it declares them."""

    options: List[Option] = field(default_factory=list)

    def __bool__(self) -> bool:
        return bool(self.settable)

    def __iter__(self):
        return iter(self.options)

    def __len__(self) -> int:
        return len(self.options)

    @property
    def settable(self) -> List[Option]:
        return [option for option in self.options if not option.is_heading]

    def get(self, name: str) -> Optional[Option]:
        for option in self.options:
            if option.name == name:
                return option
        return None

    def defaults(self) -> Dict[str, str]:
        return {option.name: option.default for option in self.options}

    def settled(self, chosen=None) -> Dict[str, str]:
        """The values to read the mod with: its defaults, under any choices."""
        values = self.defaults()
        for name, value in (chosen or {}).items():
            if name in values:
                values[name] = str(value)
        return values

    def to_dict(self) -> dict:
        return {
            option.name: {
                "label": option.label,
                "type": option.kind,
                "default": option.default,
                "values": list(option.values),
                "labels": list(option.labels),
                "description": option.description,
            }
            for option in self.options
        }


def is_on(value) -> bool:
    """Whether a setting's value counts as true.

    A condition naming a setting is asking exactly this: the game runs the op
    when the toggle is on, and a slider or an enum set to zero is off in the
    same way an unticked box is.
    """
    return str(value).strip().lower() not in _OFF


def visible(chosen, mod_ids=None) -> Dict[str, Dict[str, str]]:
    """What each mod's ModOps can name, given each mod's settings by mod.

    Anno 117's loader keeps every loaded mod's options in one table, under
    ``mod-id.option``. A name with a dot in it is looked up as written, so
    ``$other-mod.range`` reads another mod's option; one without is the
    mod's own, ``$range`` standing for ``$<its ModID>.range``
    (``ReplaceStaticVariables`` in the loader's source). So each mod sees
    its own settings by their bare names and everybody's -- its own too --
    by the full ones. ``mod_ids`` is each mod's ModID, by mod; a mod
    without one can still name its own settings, and nobody can name its.
    A mod named in ``mod_ids`` with no settings of its own sees everybody's.
    """
    chosen = chosen or {}
    ids = {origin: str(mod_id).strip() for origin, mod_id in (mod_ids or {}).items()}
    qualified = {
        "%s.%s" % (ids[origin], name): str(value)
        for origin, values in chosen.items() if ids.get(origin)
        for name, value in (values or {}).items() if "." not in name
    }
    return {
        origin: {**qualified, **dict(chosen.get(origin) or {})}
        for origin in [*chosen, *(origin for origin in ids if origin not in chosen)]
    }


def read(raw) -> ModOptions:
    """Read the ``Options`` block of a modinfo.json."""
    found: List[Option] = []
    if not isinstance(raw, dict):
        return ModOptions()
    for name, spec in raw.items():
        if not isinstance(spec, dict):
            continue
        values = spec.get("values")
        labels = spec.get("labels")
        found.append(
            Option(
                name=str(name),
                label=str(spec.get("label") or name),
                kind=str(spec.get("type") or ENUM).strip().lower(),
                default=str(spec.get("default", "")),
                values=tuple(str(value) for value in values)
                if isinstance(values, list)
                else (),
                labels=tuple(str(label) for label in labels)
                if isinstance(labels, list)
                else (),
                description=str(spec.get("description") or ""),
            )
        )
    return ModOptions(found)


def from_dict(raw) -> ModOptions:
    """Rebuild a declaration saved into a loadout file."""
    return read(raw)


# -- declaring them ------------------------------------------------------------
# What a modinfo calls the block, and what an option may be named: a name the
# loader's pattern takes whole (``[$#][a-zA-Z_][\w\-.]*``), less the dot, which
# would make ``$a.b`` the option ``b`` of a mod called ``a``.
OPTIONS_KEY = "Options"
NAME = re.compile(r"^[A-Za-z_][\w\-]*$")


def block_of(loadout, origin: str) -> dict:
    """The ``Options`` block of a mod in ``loadout``, as written: from its
    record's ``declared``, a loaded mod's modinfo or the own mod's blocks."""
    record = loadout.mod(origin)
    declared = record.declared if record is not None and isinstance(record.declared, dict) else {}
    key = _key_in(declared)
    held = declared.get(key) if key is not None else None
    return dict(held) if isinstance(held, dict) else {}


def set_block(loadout, origin: str, block: dict) -> None:
    """Put ``block`` in as a mod's ``Options``, or take it out when empty.

    In a loaded mod's modinfo it keeps its key and its place, and what the
    mod offers is read again from it -- less any iModYourAnno tweaks, which
    are the mod's files and not its modinfo, and stay as they were.
    """
    block = dict(block or {})
    layer = loadout.mod(origin)
    if layer is None:
        return
    was = read(block_of(loadout, origin))
    declared = dict(layer.declared or {})
    key = _key_in(declared)
    if not block:
        declared.pop(key, None)
    else:
        declared[key or OPTIONS_KEY] = block
    layer.declared = declared
    kept = [
        option for option in from_dict(layer.offered)
        if was.get(option.name) is None and option.name not in block
    ]
    layer.offered = ModOptions(kept + list(read(block))).to_dict()
    layer.options = {
        name: value for name, value in layer.options.items() if name in layer.offered
    }


def _key_in(declared: dict) -> Optional[str]:
    return next(
        (key for key in declared if isinstance(key, str) and key.lower() == "options"), None
    )


def as_fields(option: Option) -> dict:
    """An option as the plain fields it has, to edit in place."""
    return {
        "name": option.name, "label": option.label, "kind": option.kind,
        "default": option.default, "values": tuple(option.values),
        "labels": tuple(option.labels), "description": option.description,
    }


def started_as(kind: str, name: str, label: str = "") -> Option:
    """A new option of ``kind``, at a default a mod starts from: a toggle
    off, a choice of one value, a slider from 0 to 10 in steps of 1."""
    if kind == TOGGLE:
        return Option(name=name, label=label or name, kind=TOGGLE, default="false")
    if kind == SLIDER:
        return Option(name=name, label=label or name, kind=SLIDER, default="1",
                      values=("0", "10", "1"))
    return Option(name=name, label=label or name, kind=kind, default="1", values=("1",))


def to_block(options, original=None) -> dict:
    """The ``Options`` block ``options`` are written as, in their order.

    Each option is written over what ``original`` said of it, so a key the
    Studio does not model goes on being said. Only what means something is
    written: a label that is only the name, labels nobody gave and values a
    toggle does not have are left out.
    """
    original = original or {}
    block = {}
    for option in options:
        was = original.get(option.name)
        spec = dict(was) if isinstance(was, dict) else {}
        for key in ("label", "type", "default", "values", "labels", "description"):
            spec.pop(key, None)
        written = {}
        if option.label and option.label != option.name:
            written["label"] = option.label
        written["type"] = option.kind
        written["default"] = option.default
        if option.kind != TOGGLE and option.values:
            written["values"] = list(option.values)
        if any(option.labels):
            written["labels"] = list(option.labels)
        if option.description:
            written["description"] = option.description
        block[option.name] = {**written, **spec}
    return block
