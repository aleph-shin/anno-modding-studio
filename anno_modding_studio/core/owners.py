"""Which file keeps each part of a loadout, said by the part itself.

A loadout and the projects of its mods are one :class:`~.changes.Loadout` in
memory, and several files on the disk (``docs/proposals/
projects-and-loadouts-proposal.md``, section 4): saving takes each project's
part out of what the loadout would write, and opening puts it back
(:func:`.projects.split`, :func:`.projects.join`).  Which file each part goes
to is declared where the part is, on its field, as the field's ``metadata``:

* :data:`LOADOUT` -- the loadout file keeps it: what this loadout is and
  works out, whichever mods are in it;
* :data:`DETAIL` -- the project of the mod the record describes keeps it: of
  a :class:`~.changes.ModLayer` its mod's, of the loadout its own mod's;
* :data:`RELEASE` -- the project of the mod the loadout releases: its
  carried-on mod's where it has one, its own mod's otherwise;
* :data:`PER_MOD` -- a collection each entry of which is one mod's, by its
  origin, and goes to that mod's project where it has one;
* :data:`INSIDE` -- a collection whose entries a mod of yours keeps inside
  somebody else's ops go to that mod's project (:mod:`.inside`), the rest
  staying with the loadout; :data:`PER_MOD_AND_INSIDE` is both at once;
* :data:`MEMORY` -- never saved: worked out, remembered while open, or only
  for a throwaway view.

So a field added to either class says where it goes, or a test refuses it
(``tests/test_owners.py``); and what :mod:`.projects` moves between files is
read off these declarations (:func:`names`), not off lists of its own.

Imports nothing of the Studio's, so that :mod:`.projects` and
:mod:`.changes` can both use it.
"""

from __future__ import annotations

from dataclasses import Field, fields
from functools import cache
from types import MappingProxyType
from typing import Any, Callable, Dict, Mapping, Tuple, TypeVar

# The key of a field's ``metadata`` that says which file keeps it.
OWNER = "owner"


def _owned_by(name: str) -> Mapping[str, str]:
    return MappingProxyType({OWNER: name})


LOADOUT = _owned_by("loadout")
DETAIL = _owned_by("detail")
RELEASE = _owned_by("release")
PER_MOD = _owned_by("per mod")
INSIDE = _owned_by("inside")
PER_MOD_AND_INSIDE = _owned_by("per mod and inside")
MEMORY = _owned_by("memory")

EVERY = (LOADOUT, DETAIL, RELEASE, PER_MOD, INSIDE, PER_MOD_AND_INSIDE, MEMORY)

# The classes whose fields say where they are kept, by a name of their own.
_SCHEMAS: Dict[str, type] = {}

T = TypeVar("T")


def schema(name: str) -> Callable[[T], T]:
    """Say that the dataclass decorated is one whose fields each declare
    which file keeps them, known as ``name`` to :func:`names`."""
    def register(cls: T) -> T:
        _SCHEMAS[name] = cls  # type: ignore[assignment]
        return cls
    return register


def owner_of(found: Field) -> str:
    """Which file keeps the field ``found``, by the name of its owner, or
    "" where it does not say."""
    said = found.metadata.get(OWNER, "")
    return said if isinstance(said, str) else ""


def names(name: str, *owners: Mapping[str, Any]) -> Tuple[str, ...]:
    """The fields of the schema ``name`` that any of ``owners`` keeps, in
    the order the class declares them."""
    return _names(name, tuple(sorted(owner[OWNER] for owner in owners)))


@cache
def _names(name: str, owners: Tuple[str, ...]) -> Tuple[str, ...]:
    cls = _SCHEMAS.get(name)
    if cls is None:
        raise LookupError("no schema called %r: core.changes declares them" % name)
    return tuple(found.name for found in fields(cls) if owner_of(found) in owners)


def undeclared(name: str) -> Tuple[str, ...]:
    """The fields of the schema ``name`` that say nothing of where they
    are kept: none, where the schema is whole."""
    known = {owner[OWNER] for owner in EVERY}
    return tuple(found.name for found in fields(_SCHEMAS[name]) if owner_of(found) not in known)
