"""What is allowed to go where, so the editor can offer real choices.

Three sources, in decreasing order of authority:

``templates.xml``
    Ships next to ``assets.xml``.  Maps a template name to the exact list of
    top-level sections an asset of that template has.  Verified exact: the 27
    sections it lists for ``FactoryBuilding7`` are precisely the 27 a real
    factory carries.  This is what makes "create an asset from scratch"
    possible.

``properties.xml``
    Also ships next to ``assets.xml``.  Gives default values per section, and --
    the useful part -- ``DefaultContainerValues``, the shape of a *single entry*
    in a list.  That is what a new ``<Item>`` should look like.

The base game's own ``assets.xml``
    Neither file above enumerates the fields *inside* a section, so for that we
    observe what the game actually does: every ``(parent path -> child tag)``
    pair across all ~96,000 assets, with counts and sample values.  This is
    stronger than a nominal schema in one respect -- everything it suggests is
    something the shipping game really does -- and weaker in another: a field no
    base asset happens to use will not be offered.  Counts are surfaced in the UI
    so the user can judge.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from lxml import etree

# Values are stored joined by a character that cannot appear in XML text.
_SAMPLE_SEP = "\x1f"
MAX_SAMPLES = 4


@dataclass(frozen=True)
class FieldInfo:
    """One candidate child tag at a given path."""

    tag: str
    count: int  # assets in the base game with this child here
    container_count: int  # how often it had children of its own
    samples: Tuple[str, ...] = ()

    @property
    def is_container(self) -> bool:
        """Whether the base game ever gave this field children.

        The same rule as :meth:`AssetSchema.knows_path`, which the tree asks: a
        list most assets ship empty is still a list.  A majority here once
        offered such a list as a value field the tree then drew as a container.
        """
        return self.container_count > 0

    @property
    def sample_text(self) -> str:
        return ", ".join(self.samples[:3])


class SchemaCollector:
    """Accumulates the observed schema during an index build."""

    def __init__(self):
        # (parent_path, tag) -> [count, container_count, {samples}]
        self._seen: Dict[Tuple[str, str], list] = {}

    def walk(self, values_elem, root_path: str = "/Values") -> None:
        stack = [(values_elem, root_path)]
        while stack:
            elem, path = stack.pop()
            for child in elem:
                tag = child.tag
                if not isinstance(tag, str):
                    continue
                has_children = len(child) > 0
                entry = self._seen.get((path, tag))
                if entry is None:
                    entry = [0, 0, set()]
                    self._seen[(path, tag)] = entry
                entry[0] += 1
                if has_children:
                    entry[1] += 1
                    stack.append((child, path + "/" + tag))
                else:
                    text = (child.text or "").strip()
                    # Long values are almost always file paths; a couple suffice.
                    if text and len(entry[2]) < MAX_SAMPLES and len(text) <= 60:
                        entry[2].add(text)

    def rows(self):
        for (path, tag), (count, container, samples) in self._seen.items():
            yield (path, tag, count, container, _SAMPLE_SEP.join(sorted(samples)))

    def __len__(self) -> int:
        return len(self._seen)


def parse_templates(path: Path) -> Dict[str, List[str]]:
    """``templates.xml`` -> ``{template name: [section, ...]}``."""
    result: Dict[str, List[str]] = {}
    root = etree.parse(str(path)).getroot()
    for template in root.iter("Template"):
        name = (template.findtext("Name") or "").strip()
        properties = template.find("Properties")
        if not name or properties is None:
            continue
        result[name] = [c.tag for c in properties if isinstance(c.tag, str)]
    return result


def parse_properties(path: Path):
    """``properties.xml`` -> (default section XML, default list-entry XML).

    Both are keyed by section name; the container map is keyed by
    ``"Section/ListName"`` since a section can hold several different lists.
    """
    defaults: Dict[str, bytes] = {}
    containers: Dict[str, bytes] = {}
    root = etree.parse(str(path)).getroot()
    for group in root.findall("Groups/Group"):
        holder = group.find("DefaultValues")
        if holder is not None:
            for section in holder:
                if isinstance(section.tag, str) and section.tag not in defaults:
                    defaults[section.tag] = etree.tostring(section)
        holder = group.find("DefaultContainerValues")
        if holder is not None:
            for section in holder:
                if not isinstance(section.tag, str):
                    continue
                for listing in section:
                    if not isinstance(listing.tag, str):
                        continue
                    key = "%s/%s" % (section.tag, listing.tag)
                    if key not in containers:
                        containers[key] = etree.tostring(listing)
    return defaults, containers


def _strip_formatting(elem) -> None:
    """Drop the source file's indentation so payloads serialise cleanly."""
    for node in elem.iter():
        if node.text is not None and not node.text.strip():
            node.text = None
        if node.tail is not None and not node.tail.strip():
            node.tail = None


# Evidence that a tag is a repeated *entry* rather than a named field: somewhere
# in the game it appears more than this many times per non-empty parent. Just
# over 1.0, because "repeats at all" is the actual claim; the margin drops a lone
# spurious hit in Anno 1800 without touching either real entry tag.
REPEAT_EVIDENCE = 1.05

# One tag repeats inside its parent far more often than the parent occurs.
_REPEATING_TAGS_SQL = """
SELECT c.tag
FROM schema_children c
JOIN schema_children p ON p.path || '/' || p.tag = c.path
WHERE p.n_container > 0 AND c.n > p.n_container * ?
GROUP BY c.tag
"""


class AssetSchema:
    """Read-only queries against the schema stored in an index database.

    The database never changes after the index is built, so every answer here is
    cached: the editor asks these questions once per node while building a tree.
    """

    def __init__(self, connection):
        self._con = connection
        self._children: Dict[str, List[FieldInfo]] = {}
        self._paths: Optional[set] = None
        self._repeating: Optional[set] = None

    # -- observed structure -------------------------------------------------
    def children_of(self, path: str) -> List[FieldInfo]:
        """Every child tag seen at ``path`` in the base game, most common first."""
        cached = self._children.get(path)
        if cached is not None:
            return cached
        rows = self._con.execute(
            "SELECT tag, n, n_container, samples FROM schema_children "
            "WHERE path=? ORDER BY n DESC, tag",
            (path,),
        ).fetchall()
        out = []
        for row in rows:
            samples = tuple(s for s in (row["samples"] or "").split(_SAMPLE_SEP) if s)
            out.append(FieldInfo(row["tag"], row["n"], row["n_container"], samples))
        self._children[path] = out
        return out

    def repeating_tags(self) -> set:
        """Tags the game uses for repeated list entries, read off the data.

        This is what separates a *list* from a node that merely has one child --
        the difference between "add another entry" and "add a field".  Asking the
        shipped data rather than assuming: Anno 117 answers ``Item`` alone (745
        paths), while Anno 1800 answers ``Item`` **and** ``Entry`` (886 and 57),
        so hard-coding the obvious name would have been wrong for one of the two
        games we support.  Nothing else qualifies in either: a container whose
        single child has some other name never repeats it.
        """
        if self._repeating is None:
            rows = self._con.execute(
                _REPEATING_TAGS_SQL, (REPEAT_EVIDENCE,)
            ).fetchall()
            self._repeating = {r["tag"] for r in rows}
        return self._repeating

    def missing_children(self, path: str, present: List[str]) -> List[FieldInfo]:
        """Candidates for "add a field": seen here, but not in this asset yet."""
        have = set(present)
        return [f for f in self.children_of(path) if f.tag not in have]

    def unknown_inside(self, path: str, element) -> str:
        """The first field inside ``element``, put at ``path``, that the base
        game never has there: its path, or "" when the whole shape is one the
        game uses.

        What a pasted block is held to.  ``element`` itself is not asked
        about, only what it holds: whether it may go at ``path`` at all is
        the caller's question, a list entry and a named field being asked
        differently.
        """
        for child in element:
            if not isinstance(child.tag, str):
                continue
            if child.tag not in {f.tag for f in self.children_of(path)}:
                return path + "/" + child.tag
            found = self.unknown_inside(path + "/" + child.tag, child)
            if found:
                return found
        return ""

    def knows_path(self, path: str) -> bool:
        """True when the base game puts children at this path.

        Every known path is held in memory: the editor asks this for each node it
        draws, and one query per row made loading a large asset noticeably slow.
        """
        if self._paths is None:
            rows = self._con.execute(
                "SELECT DISTINCT path FROM schema_children"
            ).fetchall()
            self._paths = {r["path"] for r in rows}
        return path in self._paths

    # -- templates ----------------------------------------------------------
    def templates(self) -> List[str]:
        rows = self._con.execute(
            "SELECT template FROM schema_templates ORDER BY template"
        ).fetchall()
        return [r["template"] for r in rows]

    def sections_for_template(self, template: str) -> List[str]:
        row = self._con.execute(
            "SELECT sections FROM schema_templates WHERE template=?", (template,)
        ).fetchone()
        if row is None:
            return []
        return [s for s in (row["sections"] or "").split(",") if s]

    @property
    def has_templates(self) -> bool:
        row = self._con.execute("SELECT COUNT(*) AS n FROM schema_templates").fetchone()
        return bool(row and row["n"])

    # -- properties ---------------------------------------------------------
    def default_section(self, section: str):
        """The default body of a section, as an element (or ``None``)."""
        row = self._con.execute(
            "SELECT xml FROM schema_defaults WHERE kind='section' AND name=?", (section,)
        ).fetchone()
        return etree.fromstring(row["xml"]) if row else None

    def list_entry_template(self, section: str, list_tag: str):
        """The default shape of one entry in ``Section/ListName``."""
        row = self._con.execute(
            "SELECT xml FROM schema_defaults WHERE kind='container' AND name=?",
            ("%s/%s" % (section, list_tag),),
        ).fetchone()
        return etree.fromstring(row["xml"]) if row else None

    @property
    def has_properties(self) -> bool:
        row = self._con.execute(
            "SELECT COUNT(*) AS n FROM schema_defaults"
        ).fetchone()
        return bool(row and row["n"])

    # -- helpers for the editor --------------------------------------------
    def new_list_entry(self, path: str, entry_tag: str = "Item"):
        """Build a fresh list entry for the list at ``path``.

        Shaped from the fields actually seen on existing entries; otherwise
        from ``properties.xml``'s declared shape.  Only the shape: every field
        comes back empty.  A value borrowed from some other asset is not one
        the user chose, and it reads in the tree as if it were.
        """
        # What real entries contain comes first. properties.xml's declared shape
        # can name a field nothing actually uses -- Anno 117 declares
        # <PerUnitLengths> for Maintenances, where every shipping entry carries
        # Product and Amount instead.
        observed = self.children_of(path + "/" + entry_tag)
        if observed:
            entry = etree.Element(entry_tag)
            most_common = observed[0].count
            for field in observed:
                if field.is_container:
                    continue
                # Only fields most real entries carry; the long tail is noise.
                if field.count * 2 < most_common:
                    continue
                etree.SubElement(entry, field.tag)
            if len(entry):
                return entry

        # Nothing observed (an empty list everywhere): fall back to the declared
        # shape, which is at least the game's own idea of the entry.
        steps = [s for s in path.split("/") if s]
        if len(steps) >= 3 and steps[0] == "Values":
            declared = self.list_entry_template(steps[1], steps[-1])
            if declared is not None:
                entry = etree.Element(entry_tag)
                for child in declared:
                    entry.append(copy.deepcopy(child))
                if len(entry):
                    _strip_formatting(entry)
                    for leaf in entry.iter():
                        if not len(leaf):
                            leaf.text = None
                    return entry

        return etree.Element(entry_tag)

    def entry_tag_for(self, path: str) -> Optional[str]:
        """The tag repeated inside a list container, or ``None`` if not a list.

        Two conditions, and the second one matters.  A list has essentially one
        child tag -- but so does ``ShipModules/AdditionalHull``, whose single
        ``<ModuleCount>`` is a field that must appear exactly once.  Treating
        that as a list let the editor stack duplicate ``ModuleCount`` children,
        and made "Add list entry" and "Add field" two routes to the same thing.
        So the tag also has to be one the game genuinely repeats.
        """
        children = self.children_of(path)
        if not children:
            return None
        best = children[0]
        others = sum(c.count for c in children[1:])
        if others > best.count * 0.05:
            return None
        if best.tag not in self.repeating_tags():
            return None
        return best.tag

    def is_list(self, path: str) -> bool:
        """Whether this node holds repeated entries rather than named fields."""
        return self.entry_tag_for(path) is not None
