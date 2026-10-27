"""Streaming index over an Anno ``assets.xml``.

The shipped asset files are far too large to keep in memory as a DOM (hundreds of
megabytes), so we stream them once with ``iterparse`` and stash a compressed copy
of every ``<Asset>`` subtree in a SQLite database next to a small searchable
summary.  Subsequent launches reuse that database, so the expensive parse happens
only when the source file changes.

Localisation is the one genuinely surprising part.  An asset's own GUID has
*nothing* to do with its entry in ``texts_<language>.xml`` -- that would be too
easy.  Anno 1800 usually doesn't need the texts file at all: most assets embed
their display text directly (``Values/Text/LocaText/English/Text``), and where
they don't, the real cross-reference is a separate field, ``Values/Text/LineID``,
matched against ``<Text><GUID>…</GUID><Text>…</Text></Text>`` entries.  Anno 117
never embeds text; every asset instead carries ``Values/Text/OasisId``, matched
against ``<Text><LineId>…</LineId><Text>…</Text></Text>`` entries in its own
texts file -- same shape, different tag name, different key. ``loca_key`` is
whichever of ``LineID``/``OasisId`` an asset has, falling back to its own GUID
for the (rare) asset with neither, so the join always has *something* to try.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
import sqlite3
import threading
import time
import zlib
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Optional, Sequence

from lxml import etree

from . import places
from . import refs as refs_mod
from . import schema as schema_mod

if TYPE_CHECKING:
    from .texts import Names

# Bumped whenever a cache built by an older build stops being usable -- which
# covers the columns in it *and* how it is addressed. Raising it is what makes
# the stale ones collectable: prune_cache() reads this out of each file, so
# every index from before the bump is identified as dead and removed on the
# next successful build.
#
# 8: the cache key hashes the whole source file rather than sampling its ends.
# 9: each asset's top-level group (``top_group``).
# 10: whether a field's GUIDs are more than plain numbers (``telling``), which
#     takes fields of plain numbers out of the links.
# 11: an uncertain link is kept only where the field's name and the value's
#     size agree, as the editor's Refers-to column has it (refs.is_link).
# 12: a 10-digit number that fits in a GUID is a candidate link too
#     (refs.fits_a_guid), so the fields that hold them have a rate.
SCHEMA_VERSION = 12

# Anno stores localisation strings as assets in their own right. They match
# searches constantly ("Schnapps" the description asset vs schnapps the product)
# and are almost never what a modder is looking for, so they sort last.
TEXT_TEMPLATES = frozenset({"text"})

# Attribute we scribble onto <Group> nodes while streaming so an <Asset> can look
# up its ancestry without keeping a side table of element proxies alive.
_GROUP_NAME_ATTR = "_amsGroupName"

_SCHEMA_SQL = """
PRAGMA journal_mode = OFF;
PRAGMA synchronous = OFF;

CREATE TABLE meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE assets (
    guid       TEXT PRIMARY KEY,
    name       TEXT,
    loca       TEXT,
    loca_key   TEXT,  -- key into `texts`; NOT the asset's own guid, see below
    template   TEXT,
    group_path TEXT,
    haystack   TEXT,
    xml        BLOB,
    top_group  INTEGER  -- which of the file's top-level groups it is in, from 0
);

CREATE INDEX idx_assets_template ON assets(template);
CREATE INDEX idx_assets_group    ON assets(group_path);
CREATE INDEX idx_assets_loca_key ON assets(loca_key);

CREATE TABLE texts (
    guid TEXT PRIMARY KEY,
    text TEXT
);

-- What the base game actually does, observed while indexing: which child tags
-- appear under which path. Drives the editor's "add a field" suggestions.
CREATE TABLE schema_children (
    path        TEXT,
    tag         TEXT,
    n           INTEGER,
    n_container INTEGER,
    samples     TEXT,
    PRIMARY KEY (path, tag)
);

-- Populated from templates.xml / properties.xml when those are loaded.
CREATE TABLE schema_templates (
    template TEXT PRIMARY KEY,
    sections TEXT
);

CREATE TABLE schema_defaults (
    kind TEXT,
    name TEXT,
    xml  BLOB,
    PRIMARY KEY (kind, name)
);

-- How often each numeric field turns out to hold a real asset GUID, measured
-- over the whole file. This is what separates a link from a number that
-- happens to collide with one; see core/refs.py.
CREATE TABLE ref_fields (
    path      TEXT,
    tag       TEXT,
    n         INTEGER,  -- integer values seen at this field
    resolved  INTEGER,  -- how many of them are a real asset GUID
    confident INTEGER,  -- 1 when the field's own numbers say it is a link
    entries   INTEGER,  -- resolving entries, i.e. links found here
    valued    INTEGER,  -- of those, how many set something besides the name
    telling   INTEGER,  -- 0 when its GUIDs are plain numbers elsewhere
    PRIMARY KEY (path, tag)
);

-- One row per resolved link, both directions indexed: the reference graph.
CREATE TABLE refs (
    src       TEXT,
    target    TEXT,
    path      TEXT,
    tag       TEXT,
    confident INTEGER,
    -- 1 when the entry names the asset and sets nothing: a costs list that
    -- mentions a good without spending any of it. See core/refs.py.
    nominal   INTEGER
);

CREATE INDEX idx_refs_src    ON refs(src);
CREATE INDEX idx_refs_target ON refs(target);
"""


class IndexCancelled(Exception):
    """Raised inside the worker when the user aborts an index build."""


class NotWholeXml(ValueError):
    """The file to index is cut short or is not XML: there is no index of it."""


@dataclass(frozen=True)
class AssetRow:
    """One search hit.  ``display`` is the friendliest name we could find."""

    guid: str
    name: str
    display: str
    template: str
    group_path: str
    # True for an asset this mod creates rather than one the game ships.
    is_new: bool = False
    # When ``display`` came from a mod's texts patch: that mod, and any other
    # loaded mod that named it differently -- see :mod:`core.texts`.
    named_by: str = ""
    also: tuple = ()

    @property
    def label(self) -> str:
        base = self.display or self.name or ("GUID " + self.guid)
        if self.display and self.display != self.name:
            base = "%s  (%s)" % (self.display, self.name)
        return ("+ " + base) if self.is_new else base


@dataclass(frozen=True)
class ReferenceLink:
    """One edge of the reference graph, seen from one end.

    ``guid`` is the asset at the *other* end -- which end that is depends on
    whether this came from ``references_from`` or ``references_to``.
    """

    guid: str
    path: str
    tag: str
    confident: bool = True
    # The entry names the asset and sets nothing else -- a costs list that
    # mentions a good without spending any of it.
    nominal: bool = False

    @property
    def field_label(self) -> str:
        """The field the link lives in, without its ``/Values`` prefix."""
        trimmed = self.path[len("/Values"):].lstrip("/") if self.path.startswith("/Values") else self.path
        steps = [step for step in trimmed.split("/") if step]
        steps.append(self.tag)
        return " › ".join(steps)


class _CountingReader:
    """File wrapper that tracks how far we have read, for progress reporting."""

    def __init__(self, fh):
        self._fh = fh
        self.pos = 0

    def read(self, size: int = -1) -> bytes:
        chunk = self._fh.read(size)
        self.pos += len(chunk)
        return chunk


# Read in chunks rather than whole: the largest assets.xml here is 289 MB and
# there is no reason to hold it in memory to hash it.
HASH_CHUNK = 4 * 1024 * 1024


def fingerprint(source: Path) -> str:
    """What identifies this file's *contents*.

    A hash of all of it, plus the size. This used to sample four megabytes from
    each end, on the reasoning that hashing the whole file "would cost about
    what it saves" -- which measurement does not bear out. SHA-1 runs at about
    1.5 GB/s here, so the largest assets.xml costs a fifth of a second, against
    an index build measured in minutes; and cache_path_for is asked once per
    thing the user does, not in a loop.

    What sampling bought in exchange was a hole: an edit in the middle of the
    file that happened to preserve its length read as the same file, and the
    index built against the old bytes was served for it silently. No game patch
    would do that, but a hand-edited extract is not exotic, and a stale index is
    a wrong answer nobody is told about.
    """
    source = Path(source)
    stat = source.stat()
    # Held per file while its size and modification time stand: a launch
    # asked three times over for the same 289 MB, most of its start-up.  An
    # edit that keeps the length still moves the time, so it still counts.
    key = (str(source.resolve()), stat.st_size, stat.st_mtime_ns)
    known = _FINGERPRINTS.get(key[0])
    if known is not None and known[0] == key:
        return known[1]
    digest = hashlib.sha1(str(stat.st_size).encode("utf-8"))
    with open(source, "rb") as handle:
        for chunk in iter(lambda: handle.read(HASH_CHUNK), b""):
            digest.update(chunk)
    _FINGERPRINTS[key[0]] = (key, digest.hexdigest())
    return _FINGERPRINTS[key[0]][1]


# The last fingerprint taken of each file, with the size and time it was taken
# at -- one entry per file, so it holds as many as there are files asked about.
_FINGERPRINTS: dict = {}


def cache_path_for(source: Path) -> Path:
    """Deterministic cache location for a source file.

    Keyed on what the file *says* rather than when it was written. Extracting
    the game's files again writes a new copy with a new timestamp, and under an
    mtime key that meant rebuilding the whole index -- minutes, for a file
    identical to the one already indexed. The game being patched changes the
    contents, which still rebuilds it.

    Identical bytes therefore mean the same cache, whatever the file is called
    or when it was written; any difference at all means a different one.
    """
    source = Path(source).resolve()
    key = "%s|%s|v%s" % (source, fingerprint(source), SCHEMA_VERSION)
    digest = hashlib.sha1(key.encode("utf-8")).hexdigest()[:16]
    return places.cache_dir() / ("%s-%s.sqlite" % (source.stem, digest))


# A half-built index left behind by a run that was killed. Old enough that no
# build still going could own it.
STALE_BUILDING_SECONDS = 24 * 60 * 60


def prune_cache(keep: Optional[Path] = None) -> int:
    """Delete index caches that can never be used again.

    Only the unambiguous ones. The schema version is part of the cache key, so
    an index built under an older one is unreachable by construction -- it
    can only sit there. Likewise a ``.building`` file from a run that was
    killed, once it is far too old to belong to a build still going. The
    search answers kept beside an index go with it, and so do any whose
    index is gone already.

    An index of a *newer* schema stays: it is a newer Studio's, and someone
    going back and forth between two versions, or trying an older one,
    would otherwise have each delete the other's and wait minutes for its
    own at every switch.  One that will not say its schema is no Studio's.

    What this does *not* do is decide that a perfectly good index is surplus.
    Those pile up too -- one per game patch, and one per re-extraction to a new
    path -- but which of them is still wanted is a question about what the user
    is working on, not one this function can answer (P2-15).

    Returns the number of files removed. Never raises: a cache another Studio
    still has open refuses to be deleted on Windows, which is the right answer.
    """
    removed = 0
    now = time.time()
    try:
        entries = list(places.cache_dir().iterdir())
    except OSError:
        return 0
    for path in entries:
        if keep is not None and path == Path(keep):
            continue
        try:
            if path.name.endswith(".sqlite.building") or (
                SEARCHES_SUFFIX + "." in path.name and path.name.endswith(".writing")
            ):
                if now - path.stat().st_mtime > STALE_BUILDING_SECONDS:
                    path.unlink()
                    removed += 1
                continue
            if path.name.endswith(SEARCHES_SUFFIX):
                # Kept for an index, and of no use without it.
                index = path.with_name(path.name[: -len(SEARCHES_SUFFIX)] + ".sqlite")
                if not index.exists():
                    path.unlink()
                    removed += 1
                continue
            if path.suffix != ".sqlite":
                continue
            schema = _schema_of(path)
            if schema is not None and schema >= SCHEMA_VERSION:
                continue
            path.unlink()
            _forget_searches(path)
            removed += 1
        except OSError:
            continue
    return removed


def _schema_of(path: Path) -> Optional[int]:
    """The schema an index was built under, or ``None`` if it will not say."""
    try:
        con = sqlite3.connect("file:%s?mode=ro" % path.as_posix(), uri=True)
    except sqlite3.Error:
        return None
    try:
        row = con.execute(
            "select value from meta where key = 'schema_version'"
        ).fetchone()
    except sqlite3.Error:
        return None
    finally:
        con.close()
    try:
        return int(row[0]) if row else None
    except (TypeError, ValueError):
        return None


def _first_text(elem, *paths: str) -> str:
    for path in paths:
        value = elem.findtext(path)
        if value:
            stripped = value.strip()
            if stripped:
                return stripped
    return ""


def _inside_asset(elem) -> bool:
    """Whether this node is part of an asset rather than the file's structure."""
    node = elem.getparent()
    while node is not None:
        if node.tag == "Asset":
            return True
        node = node.getparent()
    return False


def _top_level(group) -> bool:
    """Whether a ``<Group>`` is one of the file's top-level ones: a child of
    the ``<Groups>`` straight under the root."""
    parent = group.getparent()
    if parent is None or parent.tag != "Groups":
        return False
    root = parent.getparent()
    return root is not None and root.getparent() is None


def _group_path_for(asset) -> str:
    parts = []
    node = asset.getparent()
    while node is not None:
        if node.tag == "Group":
            name = node.get(_GROUP_NAME_ATTR)
            if name:
                parts.append(name)
        node = node.getparent()
    parts.reverse()
    return " / ".join(parts)


def _classify_refs(con) -> None:
    """Turn the candidate links collected during the walk into a graph.

    Every integer leaf went into ``refs`` on the way past, because whether one
    is a GUID cannot be known until every GUID has been seen. Now it can: each
    field is scored by how often it resolves, judged once (see core/refs.py),
    and the rows are rebuilt keeping only links worth showing.

    Rebuilt rather than deleted from: better than nine in ten candidate rows
    go, and writing the survivors into a fresh table beats deleting the rest
    out of an indexed one.
    """
    # A row for each different value of each field, not one for each field:
    # how often a field resolves is counted in values, and whether that says
    # anything is counted in different ones (see core/refs.py).
    stats: Dict[tuple, list] = {}
    for path, tag, value, n, hit, entries, valued in con.execute(
        """
        SELECT r.path, r.tag, r.target, COUNT(*) AS n,
               MAX(a.guid IS NOT NULL) AS hit,
               SUM(CASE WHEN a.guid IS NULL OR r.src = r.target THEN 0 ELSE 1 END)
                   AS entries,
               SUM(CASE WHEN a.guid IS NULL OR r.src = r.target THEN 0
                        ELSE r.nominal END) AS valued
        FROM refs r LEFT JOIN assets a ON a.guid = r.target
        GROUP BY r.path, r.tag, r.target
        """
    ):
        held = stats.get((path, tag))
        if held is None:
            # Values seen, how many resolve, links, links that set a value,
            # and the different GUIDs among them.
            held = stats[(path, tag)] = [0, 0, 0, 0, []]
        held[0] += n
        if hit:
            held[1] += n
            held[4].append(value)
        held[2] += entries or 0
        held[3] += valued or 0

    # The numbers the game uses as plain numbers: whatever a field that is
    # plainly not a link holds. Only the GUIDs among them can matter.
    plain: set = set()
    by_tag: Dict[str, list] = {}
    for (_path, tag), (n, resolved, _entries, _valued, targets) in stats.items():
        if refs_mod.holds_plain_numbers(tag, n, resolved):
            plain.update(targets)
        pooled = by_tag.setdefault(tag, [0, 0, set()])
        pooled[0] += n
        pooled[1] += resolved
        pooled[2].update(targets)
    # A tag that is a link taken over the whole game vouches for a field of
    # it that could not say so alone.
    vouched = {
        tag for tag, (n, resolved, targets) in by_tag.items()
        if refs_mod.is_confident(
            tag, n, resolved, refs_mod.is_telling(len(targets), len(targets & plain))
        )
    }

    fields = []
    keep = []
    for (path, tag), (n, resolved, entries, valued, targets) in stats.items():
        telling = tag in vouched or refs_mod.is_telling(
            len(targets), sum(1 for value in targets if value in plain)
        )
        confident = refs_mod.is_confident(tag, n, resolved, telling)
        fields.append(
            (path, tag, n, resolved, 1 if confident else 0, entries, valued,
             1 if telling else 0)
        )
        if refs_mod.is_plausible(tag, n, resolved, telling):
            # Short of a confident field, a link is kept only where the field's
            # name and the value's size agree (refs.is_link, the rule the
            # editor's Refers-to column and an edited asset's walk answer by):
            # the smallest value that passes, or none at all.
            least = 0 if confident else refs_mod.least_inferred_guid(tag)
            if least is None:
                continue
            # Only a path whose entries normally say something can have one
            # that says nothing stand out as a placeholder.
            uses_values = refs_mod.path_uses_values(entries, valued)
            keep.append(
                (path, tag, 1 if confident else 0, 1 if uses_values else 0, least)
            )

    con.executemany("INSERT OR REPLACE INTO ref_fields VALUES (?,?,?,?,?,?,?,?)", fields)
    con.execute(
        "CREATE TEMP TABLE keep_fields (path TEXT, tag TEXT, confident INTEGER, "
        "uses_values INTEGER, least INTEGER, PRIMARY KEY (path, tag))"
    )
    con.executemany("INSERT OR REPLACE INTO keep_fields VALUES (?,?,?,?,?)", keep)

    con.executescript(
        """
        CREATE TABLE refs_kept (
            src TEXT, target TEXT, path TEXT, tag TEXT,
            confident INTEGER, nominal INTEGER
        );
        -- `nominal` arrives holding "this entry set a value"; it becomes the
        -- opposite -- "names it and sets nothing" -- and only where the path
        -- gives that any meaning.
        -- An asset's own <GUID> resolves to itself; a self-loop says nothing.
        INSERT INTO refs_kept
        SELECT r.src, r.target, r.path, r.tag, k.confident,
               CASE WHEN k.uses_values = 1 AND r.nominal = 0 THEN 1 ELSE 0 END
        FROM refs r
        JOIN keep_fields k ON k.path = r.path AND k.tag = r.tag
        JOIN assets a ON a.guid = r.target
        WHERE r.src <> r.target AND CAST(r.target AS INTEGER) >= k.least;

        DROP INDEX idx_refs_src;
        DROP INDEX idx_refs_target;
        DROP TABLE refs;
        ALTER TABLE refs_kept RENAME TO refs;
        CREATE INDEX idx_refs_src    ON refs(src);
        CREATE INDEX idx_refs_target ON refs(target);
        DROP TABLE keep_fields;
        """
    )


def build_index(
    source: Path,
    db_path: Path,
    progress: Optional[Callable[[int, int, int], None]] = None,
    should_cancel: Optional[Callable[[], bool]] = None,
) -> int:
    """Parse ``source`` into a fresh SQLite index at ``db_path``.

    ``progress`` is called as ``(bytes_read, total_bytes, assets_found)``.
    Returns the number of indexed assets.

    Raises :class:`NotWholeXml` for a file the parser could not read to its
    end, and writes no index of it.  The file is read forgivingly, since that
    is the only way to read it as a stream; left at that, a file cut inside
    an asset was indexed up to the cut, half an asset included, and one that
    was no XML at all gave an index of nothing -- which the next start then
    took for the game's.  Both games' files read with no error at all.
    """
    source = Path(source)
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    total = source.stat().st_size

    staging = db_path.with_name(db_path.name + ".building")
    if staging.exists():
        staging.unlink()

    con = sqlite3.connect(str(staging))
    try:
        con.executescript(_SCHEMA_SQL)
        batch = []
        edges = []
        count = 0
        # How many of the file's top-level groups have closed: the one an
        # asset is in, by the time the asset itself closes.
        top_group = 0
        last_report = 0.0
        collector = schema_mod.SchemaCollector()

        def stop_if_asked() -> None:
            if should_cancel is not None and should_cancel():
                raise IndexCancelled()

        with open(source, "rb") as raw:
            reader = _CountingReader(raw)
            context = etree.iterparse(
                reader,
                events=("end",),
                tag=("Asset", "Group", "Name"),
                recover=True,
                huge_tree=True,
                # Said, not left to lxml: before 6.1 iterparse resolved
                # external entities (CVE-2026-41066).
                resolve_entities="internal",
                no_network=True,
            )
            for _event, elem in context:
                tag = elem.tag
                if tag == "Name":
                    # Remember group names as we pass them; <Name> always precedes
                    # the <Assets> block inside a <Group>.  Of the file's own
                    # groups only: <Group> is a field name inside an asset too,
                    # and one with a <Name> had the attribute written into
                    # the asset as it is stored.
                    parent = elem.getparent()
                    if (
                        parent is not None and parent.tag == "Group" and elem.text
                        and not _inside_asset(parent)
                    ):
                        parent.set(_GROUP_NAME_ATTR, elem.text.strip())
                    continue

                if _inside_asset(elem):
                    # Both names we watch for are also ordinary field names
                    # inside an asset: <Group> appears in 19 Anno 117 assets,
                    # and <Asset> holds a GUID in lists like UnlockAssets.
                    # Their end event arrives before the enclosing asset's, so
                    # the pruning below would clear the field and delete
                    # everything recorded before it -- out of an asset that has
                    # not finished being read. The asset's own end event does
                    # all the cleaning that is needed.
                    continue

                if tag == "Asset":
                    guid = _first_text(elem, "Values/Standard/GUID")
                    if guid:
                        name = _first_text(elem, "Values/Standard/Name")
                        loca = _first_text(elem, "Values/Text/LocaText/English/Text")
                        # 1800's cross-reference, then 117's; see module docstring.
                        loca_key = (
                            _first_text(elem, "Values/Text/LineID", "Values/Text/OasisId")
                            or guid
                        )
                        template = (elem.findtext("Template") or "").strip()
                        group_path = _group_path_for(elem)
                        haystack = " ".join(
                            p for p in (guid, name, loca, template, group_path) if p
                        ).lower()
                        values_elem = elem.find("Values")
                        if values_elem is not None:
                            collector.walk(values_elem)
                            # Every integer leaf is a candidate link. Which of
                            # them really are cannot be known yet -- that needs
                            # the full set of GUIDs, so it is settled below.
                            for rpath, rtag, value, carries in refs_mod.numeric_leaves(
                                values_elem
                            ):
                                edges.append(
                                    (guid, value, rpath, rtag, 1 if carries else 0)
                                )
                            if len(edges) >= 100_000:
                                con.executemany(
                                    "INSERT INTO refs VALUES (?,?,?,?,0,?)", edges
                                )
                                edges = []
                        blob = zlib.compress(etree.tostring(elem, encoding="utf-8"), 6)
                        batch.append(
                            (guid, name, loca, loca_key, template, group_path, haystack, blob,
                             top_group)
                        )
                        count += 1

                    if len(batch) >= 400:
                        con.executemany(
                            "INSERT OR REPLACE INTO assets VALUES (?,?,?,?,?,?,?,?,?)", batch
                        )
                        batch = []
                elif tag == "Group" and _top_level(elem):
                    top_group += 1

                # Free the subtree we just consumed, then drop already-processed
                # siblings so the in-memory document stays a thin skeleton.
                elem.clear()
                parent = elem.getparent()
                if parent is not None:
                    while elem.getprevious() is not None:
                        del parent[0]

                now = time.monotonic()
                if now - last_report > 0.1:
                    last_report = now
                    if progress is not None:
                        progress(reader.pos, total, count)
                    stop_if_asked()

            problems = list(context.error_log)
            del context
            if problems:
                first = problems[0]
                raise NotWholeXml(
                    "%s is not whole XML: %s (line %d)" % (source.name, first.message, first.line)
                )

        # Cancel is asked between the steps that follow too: sorting out the
        # references is the longest of them, and until here a Cancel pressed
        # once the file was read did nothing.
        stop_if_asked()

        if batch:
            con.executemany("INSERT OR REPLACE INTO assets VALUES (?,?,?,?,?,?,?,?,?)", batch)

        if edges:
            con.executemany("INSERT INTO refs VALUES (?,?,?,?,0,?)", edges)
        edges = []
        stop_if_asked()
        _classify_refs(con)
        stop_if_asked()

        con.executemany(
            "INSERT OR REPLACE INTO schema_children VALUES (?,?,?,?,?)", collector.rows()
        )

        stat = source.stat()
        con.executemany(
            "INSERT OR REPLACE INTO meta VALUES (?,?)",
            [
                ("schema_version", str(SCHEMA_VERSION)),
                ("source_path", str(Path(source).resolve())),
                ("source_size", str(stat.st_size)),
                ("source_mtime", str(int(stat.st_mtime))),
                ("asset_count", str(count)),
                ("schema_paths", str(len(collector))),
                ("built_at", str(int(time.time()))),
            ],
        )
        con.commit()
    except BaseException:
        con.close()
        if staging.exists():
            staging.unlink()
        raise
    con.close()

    if progress is not None:
        progress(total, total, count)

    if db_path.exists():
        db_path.unlink()
    # What was found in the index this one replaces was found in its file.
    _forget_searches(db_path)
    os.replace(staging, db_path)
    prune_cache(keep=db_path)
    return count


# An element name at the head of an XPath step: what an asset must contain for
# that step to have anything to match.
_STEP_TAG = re.compile(r"^[A-Za-z_][\w.-]*")


# Tags every asset has, so testing for them narrows nothing and costs a scan of
# the whole asset to find out.
# An element's name where it is written down in the file.
_TAG_IN_XML = re.compile(rb"<([A-Za-z_][\w.-]*)")

UNIVERSAL_TAGS = frozenset({"Asset", "Values", "Standard", "Template", "GUID", "Name"})

_LITERAL = re.compile(r"'([^']*)'|\"([^\"]*)\"")

# A predicate that says a value must *not* be something rules nothing out by its
# presence, so the shortcut below cannot be taken.
_NEGATIVE = re.compile(r"!=|\bnot\s*\(")


# A function a literal can be handed to and still have to be in the asset:
# what is looked for in a node's text.  Any other call drops the literal test,
# since ``string-length('qq')`` or ``translate(Name, 'ABC', 'abc')`` hold
# whatever the asset says.
_LITERAL_SAFE_CALLS = frozenset({"contains", "starts-with", "text", "node", "position", "last"})
_CALL = re.compile(r"([A-Za-z][\w.-]*)\s*\(")
# What an asset's own text writes another way than a path does.
_WRITTEN_ESCAPED = re.compile(r"""[&<>"']""")
_OR = re.compile(r"\bor\b")


def narrowing(path: str):
    """``(must all appear, at least one must appear)`` as raw bytes.

    A cheap test on an asset's text, so that most assets are never parsed at
    all.  Each half is given only where it is sound, and is empty where it is
    not: an asset the test turns away is one the path cannot match.

    The first half is the element names of the path's own steps, which a
    match cannot do without -- of one path.  A union is two, and an asset
    matching either side matches it: asked for the names of both, a search
    for ``//Values/Building | //Values/Trader`` found nothing at all.

    The second is the literals a predicate compares against, one of which has
    to be written in the asset where every way for the path to hold goes
    through such a comparison (:func:`_literals_bind`).

    One thing it takes on trust: that a literal is the text of one field.  A
    node's value is all the text under it, so ``//Item[. = '105']`` holds of
    an Item whose Good is 10 and whose Amount is 5 where nothing stands
    between the two -- and ``105`` is written nowhere.  In a game's file
    every field is on a line of its own (no asset of Anno 1800's 96,102 has
    two texts with only tags between), so such a literal would have to hold
    the line break; an asset a mod makes is not swept, it is asked alone.
    """
    plain = _outside(path)
    needed: frozenset = frozenset()
    # An expression that is no set of nodes -- ``//Missing or 1``,
    # ``count(//Missing) = 0`` -- can hold of an asset with none of its names
    # or its literals in it.
    tokens = _tokens(path)
    if tokens is None or not _only_steps(tokens, joined=True):
        return needed, []
    if "|" not in plain and "(" not in plain:
        needed = frozenset(
            tag.encode("utf-8") for tag in set(step_tags(path)) - UNIVERSAL_TAGS
        )
    one_of = []
    if "|" not in plain and _literals_bind(path):
        for match in _LITERAL.finditer(path):
            value = match.group(1) or match.group(2) or ""
            if value:
                one_of.append(value.encode("utf-8"))
    return needed, sorted(set(one_of))


def _outside(path: str) -> str:
    """``path`` with its predicates and its quoted strings taken out: what is
    left is the path's own steps and whatever joins them."""
    out, depth, quote = [], 0, ""
    for char in path:
        if quote:
            if char == quote:
                quote = ""
        elif char in "'\"":
            quote = char
        elif char == "[":
            depth += 1
        elif char == "]":
            depth = max(0, depth - 1)
        elif not depth:
            out.append(char)
    return "".join(out)


def _unquoted(path: str) -> str:
    """``path`` with each quoted string's inside blanked, its length kept."""
    out, quote = [], ""
    for char in path:
        if quote:
            out.append(char if char == quote else " ")
            if char == quote:
                quote = ""
        else:
            out.append(char)
            if char in "'\"":
                quote = char
    return "".join(out)


def _literals_bind(path: str) -> bool:
    """Whether a match of ``path`` has to have one of its literals written in
    the asset.

    It has where each literal is something a node is said to equal, or to
    contain, and no way round the comparison is offered.  It has not where:

    - the path tests for absence (``!=``, ``not(``), where a literal missing
      is the whole point;
    - a literal is compared by order, handed to a function that does not
      look for it, or is what is searched *in*;
    - a predicate offers an alternative with no literal in it
      (``[R=1 or Missing='zzz']``);
    - a literal holds what an asset's text writes as an entity
      (``'Tom & Co'`` is ``Tom &amp; Co`` there);
    - the path reckons or compares by order anywhere, or brackets part of
      itself: what a literal is then set against can be a number or a yes or
      no and not a node's text (``[(A = B) = 'x']``, ``[A < B = 'x']``,
      ``[A = 'x' div 1]``);
    - a literal is set against something that is no node: a number, another
      literal, a count of places (``['2' = 2]``, ``[position() = '1']``),
      where it holds whatever the asset says;
    - a comparison is itself compared (``[Radius = '4' = 0]``,
      ``[contains(Radius, '9') = 0]``), which can hold because the literal is
      *not* there -- anywhere in the path, since a literal in a predicate
      binds only while the nodes it is a predicate of have to be there
      (``[A = B = Item[Good = '10']]`` holds of an asset with no Item);
    - a predicate joins paths with ``|``, or hands one holding a literal to a
      function (``[A | B[. = 'x']]``, ``[contains(A[. = 'x'], B)]``), for the
      same reason;
    - a literal is empty: every node's text contains it, and
      ``[A = 'x' or B = '']`` holds of an asset with an empty B and no ``x``.

    Each of those passed the old test, which asked only for no negation, and
    the search then missed assets that match.
    """
    if _NEGATIVE.search(path):
        return False
    tokens = _tokens(path) or [("", "(")]
    for position, (kind, written) in enumerate(tokens):
        if kind == "operator" and written not in ("/", "//", "|", "=", "and", "or"):
            return False
        if written == "(" and not (position and tokens[position - 1][0] == "name"):
            return False
    bare = _unquoted(path)
    if any(name not in _LITERAL_SAFE_CALLS for name in _CALL.findall(bare)):
        return False
    if not _comparisons_need_their_nodes(tokens):
        return False
    if not all(_set_against_a_node(tokens, at) for at, (kind, _w) in enumerate(tokens) if kind == "literal"):
        return False
    for match in _LITERAL.finditer(path):
        value = match.group(1) or match.group(2) or ""
        if _WRITTEN_ESCAPED.search(value):
            return False
        before = bare[: match.start()].rstrip()
        after = bare[match.end():].lstrip()
        equals_before = before.endswith("=") and not before.endswith(("!=", "<=", ">="))
        equals_after = after.startswith("=")
        # The second thing handed to contains() or starts-with(): what is
        # looked for.  The first is what it is looked for in.
        looked_for = before.endswith(",")
        if not (equals_before or equals_after or looked_for):
            return False
    return all(_every_branch_has_a_literal(group) for group in _predicates(bare))


def _operand(tokens, at: int, step: int):
    """One side of a comparison, from ``at`` going ``step`` (1 or -1) to
    where the side ends: its tokens as written, each with whether it is the
    side's own or inside a bracket of it, and what ended it -- an ``=``, an
    ``and`` or ``or``, a comma, the bracket the comparison stands in, or ""
    at the path's own end."""
    opens, closes = (("[", "("), ("]", ")")) if step > 0 else (("]", ")"), ("[", "("))
    side, depth, ended = [], 0, ""
    while 0 <= at < len(tokens):
        kind, written = tokens[at]
        inside = bool(depth)
        if kind == "sign" and written in opens:
            depth += 1
            inside = True
        elif kind == "sign" and written in closes:
            if not depth:
                ended = written
                break
            depth -= 1
        elif not depth and (written == "," or (kind == "operator" and written in ("=", "and", "or"))):
            ended = written
            break
        side.append((inside, kind, written))
        at += step
    return (side if step > 0 else side[::-1]), ended


_LOOKS_IN_TEXT = frozenset({"contains", "starts-with"})


def _comparisons_need_their_nodes(tokens) -> bool:
    """Whether every path inside a predicate has to find a node for the
    predicate to hold, as far as how it is compared goes: no ``|``, no empty
    string, no comparison compared with another or with what a function
    answers, and no string inside a function's brackets but the one it looks
    for."""
    for at, (kind, written) in enumerate(tokens):
        if kind == "operator" and written == "|":
            return False
        if kind == "literal" and len(written) == 2:
            return False
        if kind == "operator" and written == "=":
            for side, ended in (_operand(tokens, at - 1, -1), _operand(tokens, at + 1, 1)):
                if ended == "=" or any(
                        not inside and name in _LOOKS_IN_TEXT for inside, _kind, name in side):
                    return False
        if kind == "name" and written in _LOOKS_IN_TEXT and at + 1 < len(tokens) and tokens[at + 1][1] == "(":
            depth, place = 0, at + 1
            while place < len(tokens):
                if tokens[place][1] == "(":
                    depth += 1
                elif tokens[place][1] == ")":
                    depth -= 1
                    if not depth:
                        break
                elif tokens[place][0] == "literal" and not (
                        depth == 1 and tokens[place - 1][1] == "," and place + 1 < len(tokens)
                        and tokens[place + 1][1] == ")"):
                    return False
                place += 1
    return True


def _is_nodes(side) -> bool:
    """Whether one side of a comparison is a path to nodes: names and what
    joins them, with whatever predicates.  Not a number, a string, or a call
    other than the node tests ``text()`` and ``node()``."""
    for place, (inside, kind, written) in enumerate(side):
        if inside:
            continue
        if kind in ("number", "literal", "variable"):
            return False
        called = place + 1 < len(side) and side[place + 1][2] == "("
        if called and written not in ("text", "node"):
            return False
    return bool(side)


def _set_against_a_node(tokens, at: int) -> bool:
    """Whether the literal at ``at`` is what a node is said to equal, or what
    is looked for in one, in a comparison that is not itself compared."""
    def written(place: int) -> str:
        return tokens[place][1] if 0 <= place < len(tokens) else ""

    if written(at - 1) == ",":
        # contains(node, 'x'): the call's first argument has to be nodes, and
        # the call's answer not something set against another.
        first, opened = _operand(tokens, at - 2, -1)
        name = at - len(first) - 3
        if opened != "(" or written(at + 1) != ")" or name < 0 or tokens[name][0] != "name":
            return False
        return _is_nodes(first) and "=" not in (written(name - 1), written(at + 2))
    if written(at - 1) == "=" and written(at + 1) != "=":
        other, ended = _operand(tokens, at - 2, -1)
    elif written(at + 1) == "=" and written(at - 1) != "=":
        other, ended = _operand(tokens, at + 2, 1)
    else:
        return False
    return ended != "=" and _is_nodes(other)


def _predicates(bare: str):
    """Each ``[...]`` of a path whose strings are blanked, without its
    brackets, the ones inside another given too."""
    opened = []
    for position, char in enumerate(bare):
        if char == "[":
            opened.append(position)
        elif char == "]" and opened:
            yield bare[opened.pop() + 1: position]


def _every_branch_has_a_literal(predicate: str) -> bool:
    """Whether each side of an ``or`` at the predicate's own level compares
    against a literal; True for a predicate with no ``or``."""
    level, depth = [], 0
    for char in predicate:
        if char in "[(":
            depth += 1
        elif char in "])":
            depth = max(0, depth - 1)
        level.append(char if not depth else "_")
    branches = _OR.split("".join(level))
    if len(branches) < 2:
        return True
    at = 0
    for branch in branches:
        # The branch as written, nested parts and all: a literal inside a
        # bracket of it is still a literal of this side.
        if "'" not in predicate[at: at + len(branch)] and '"' not in predicate[at: at + len(branch)]:
            return False
        at += len(branch) + 2
    return True


def step_tags(path: str):
    """The element names an XPath's own steps require, predicates aside.

    Only the steps: a predicate can name an element that has to be *absent*, or
    one on a different branch, so requiring those would rule out assets that do
    match.  The steps themselves are what a match cannot do without.
    """
    for step in _steps(path):
        step = step.split("[", 1)[0].strip()
        if not step or step in (".", "..", "*") or "(" in step or "::" in step:
            continue
        matched = _STEP_TAG.match(step)
        if matched is not None:
            yield matched.group(0)


def _steps(path: str):
    """A path split at its own slashes, not the ones inside a predicate.

    ``//Values/Standard[IconFilename='data/ui/fhd/…/obsidian.png']`` has two
    steps.  Split at every slash it had eight, and ``ui``, ``fhd`` and the rest
    became element names an asset had to contain -- which no asset does, so the
    search was answered "nothing matches" before a single one was parsed, and
    *Rainbow Obsidian*'s new icons never reached the eight assets they are for.
    """
    depth, quote, start = 0, "", 0
    for position, char in enumerate(path):
        if quote:
            if char == quote:
                quote = ""
        elif char in "'\"":
            quote = char
        elif char == "[":
            depth += 1
        elif char == "]":
            depth = max(0, depth - 1)
        elif char == "/" and not depth:
            yield path[start:position]
            start = position + 1
    yield path[start:]


# What an asset stands inside in the file, from the top down: the root, then
# a group in a list of groups as many deep as the file has it, then that
# group's assets.
_ABOVE_AN_ASSET = frozenset({"AssetList", "Groups", "Group", "Assets"})
def _stands_in(depth: int) -> List[str]:
    return ["AssetList", "Groups", "Group"] + ["Groups", "Group"] * depth + ["Assets"]


def _above_holds(leading, to_the_asset: bool) -> Optional[bool]:
    """Whether the steps ``leading``, as ``(joiner, name)``, name what an asset
    stands in -- ``to_the_asset`` for ending at the ``<Assets>`` it is a child
    of.  None where that turns on how deep the asset's group is."""
    said = set()
    for depth in range(len(leading) + 2):
        above = _stands_in(depth)
        # Where in ``above`` the steps so far can have got to: before the
        # first of them, the document over its root.
        at = {-1}
        for joiner, name in leading:
            if joiner == "//":
                reached = set(range(min(at) + 1, len(above))) if at else set()
            else:
                reached = {place + 1 for place in at}
            at = {place for place in reached if place < len(above) and above[place] == name}
        said.add((len(above) - 1) in at if to_the_asset else bool(at))
    return said.pop() if len(said) == 1 else None


# A search path a token at a time, as XPath 1.0 reads one: a number, a
# quoted string, a sign, a name.  The names take a prefix, and ``-`` and ``.``
# inside them, as the language has it: ``a-1`` is one name.
_TOKEN = re.compile(
    r"\s*(?:(?P<number>\d+(?:\.\d*)?|\.\d+)"
    r"|(?P<literal>\"[^\"]*\"|'[^']*')"
    r"|(?P<sign>//|::|\.\.|!=|<=|>=|[/|+=<>()\[\],@.*-])"
    r"|(?P<variable>\$[^\W\d][\w.-]*(?::[^\W\d][\w.-]*)?)"
    r"|(?P<name>(?:[^\W\d][\w.-]*:)?(?:[^\W\d][\w.-]*|\*)))")
# What a name or a star is read as a name after -- and anywhere else as an
# operator, if it is one's word: ``a * b``, ``a and/b``, ``2 div//c``.
_NAME_FOLLOWS = frozenset({"@", "::", "(", "[", ","})
_WORD_OPERATORS = frozenset({"and", "or", "mod", "div", "*"})
_OPERATORS = frozenset({"/", "//", "|", "+", "-", "=", "!=", "<", "<=", ">", ">="})


def _tokens(text: str) -> Optional[List[tuple]]:
    """``text`` as ``(kind, written)`` tokens, the kinds ``number``,
    ``literal``, ``variable``, ``name``, ``operator`` and ``sign``; None for
    what is not read to its end."""
    found: List[tuple] = []
    at = 0
    while text[at:].strip():
        match = _TOKEN.match(text, at)
        if match is None:
            return None
        at = match.end()
        kind = match.lastgroup or ""
        written = match.group(kind)
        after_an_operand = bool(found) and not (
            found[-1][0] == "operator" or found[-1][1] in _NAME_FOLLOWS)
        if written in _WORD_OPERATORS:
            kind = "operator" if after_an_operand else "name"
        elif written in _OPERATORS:
            kind = "operator"
        elif after_an_operand and (kind != "sign" or written in ("@", ".", "..")):
            # Two things with nothing joining them: ``a andb``, ``1or-b``.
            # The language has no such path, and what reads one anyway may
            # find an operator's word at the head of the name.
            return None
        found.append((kind, written))
    return found


# The steps that leave an asset for what is beside or above it.
_AXES_OUT = frozenset({"parent", "ancestor", "ancestor-or-self", "preceding", "following",
                       "preceding-sibling", "following-sibling"})
# What a path's own step can be made of, its predicates aside: a slash, a
# name, an axis, the node tests.
_NODE_TESTS = frozenset({"text", "node", "comment", "processing-instruction"})
_YES_OR_NO = frozenset({"not", "true", "false", "boolean", "contains", "starts-with"})


def _only_steps(tokens, joined: bool = False) -> bool:
    """Whether a path is steps and nothing else, once its predicates are out
    of it: no union, no comparison, no arithmetic for a rewritten half to
    bind to.  ``joined`` for letting paths joined into one set of nodes be
    steps too: ``a | b``, ``(a | b)[c]``."""
    depth = 0
    for position, (kind, written) in enumerate(tokens):
        if written == "[":
            depth += 1
        elif written == "]":
            depth -= 1
        elif depth or kind == "name" or written in ("/", "//", "::", "@", ".", ".."):
            continue
        elif written == "(":
            # Only the brackets of a node test, ``text()``: not a function's.
            called = tokens[position - 1] if position else ("", "")
            if called[0] == "name" and called[1] in _NODE_TESTS:
                if tokens[position + 1: position + 2] != [("sign", ")")]:
                    return False
            elif called[0] == "name" or not joined:
                return False
        elif written != ")" and not (joined and written == "|"):
            return False
    return True


def _looks_out(tokens) -> bool:
    """Whether a path into an asset looks back out of it anywhere, from a
    step below it or from a predicate, at what is beside or above it: a step
    up, an axis that leaves it, the language an element above it is in, an
    element looked up by its id in the whole file -- and a path from the top
    of the file, which is any slash that does not carry a step on."""
    for position, (kind, written) in enumerate(tokens):
        following = tokens[position + 1][1] if position + 1 < len(tokens) else ""
        if written == ".." or kind == "variable":
            return True
        if kind == "name" and (
                (following == "::" and written in _AXES_OUT)
                or (following == "(" and written in ("lang", "id"))):
            return True
        if written in ("/", "//") and position:
            before_kind, before = tokens[position - 1]
            if not (before_kind == "name" or before in (")", "]", ".")):
                return True
    return False


def _comes_to_a_place(tokens) -> bool:
    """Whether a predicate can come to a number, which XPath reads as a place
    among the element's siblings: ``[1]``, ``[last()]``, ``[true() + 0]``,
    ``[string-length()]``.  False only for one that is surely true or false
    -- it compares, joins with ``and`` or ``or``, asks a yes-or-no function --
    or surely a set of nodes, true where it finds any."""
    if not tokens:
        return True
    depth, operators = 0, set()
    wrapped = tokens[0][1] == "("
    for position, (kind, written) in enumerate(tokens):
        if kind == "name" and written in ("position", "last") \
                and tokens[position + 1: position + 2] == [("sign", "(")]:
            return True
        if written in ("(", "["):
            depth += 1
        elif written in (")", "]"):
            depth -= 1
            if not depth and position < len(tokens) - 1:
                wrapped = False
        elif not depth and kind == "operator":
            operators.add(written)
    # The loosest operator at the predicate's own level is what it comes to.
    if operators & {"or", "and", "=", "!=", "<", "<=", ">", ">="}:
        return False
    if operators & {"+", "-", "*", "div", "mod"}:
        return True
    if "|" in operators:
        return False
    if wrapped:
        return _comes_to_a_place(tokens[1:-1])
    kind, written = tokens[0]
    if kind != "name" and written not in ("/", "//", "@", "."):
        # A number, a string, a variable, a bracketed expression carried on.
        return True
    if kind == "name" and tokens[1:2] == [("sign", "(")] and written not in _NODE_TESTS:
        # A function, and nothing after it: one that says yes or no.
        closes = len(tokens) - 1 if tokens[-1][1] == ")" else -1
        depth = 0
        for position, (_kind, sign) in enumerate(tokens[1:], 1):
            depth += (sign == "(") - (sign == ")")
            if not depth:
                return not (position == closes and written in _YES_OR_NO)
        return True
    return False


def _turns_on_its_place(step) -> bool:
    """Whether a predicate of the step these tokens are turns on where the
    element stands among its siblings.  One this cannot tell is taken to."""
    depth, start = 0, 0
    for position, (_kind, written) in enumerate(step):
        if written == "[":
            depth += 1
            if depth == 1:
                start = position + 1
        elif written == "]":
            depth -= 1
            if not depth and _comes_to_a_place(step[start:position]):
                return True
    return False


def in_one_asset(path: str) -> List[str]:
    """``path``, written for the whole file, as the paths that find the same
    of it in one asset read as a document of its own; ``[path]`` for one that
    is the same either way, or is left as written.

    Each asset is kept and searched on its own, with nothing above it, so a
    path naming what an asset stands in -- ``//Assets/Asset[...]``,
    ``/AssetList//Asset[...]`` -- found nothing there and matched no asset.
    Its leading steps are answered here instead, from how the file is laid
    out, and the rest of it is asked of the asset: once for each place the
    path can pass from what is above an asset into it.

    Left as written, and so answered of the asset alone as it was before, is
    a path whose answer turns on how deep an asset's group is, which the
    cache does not keep; one with a predicate on a step above the asset,
    which only the file can answer; one whose step for the asset itself
    counts its place among the assets beside it (``//Assets/Asset[1]``,
    ``[last()]``, and any predicate that can come to a number:
    ``[true() + 0]``, ``[string-length()]``), which is true of every asset
    read alone and so would match them all; one that anywhere looks back out
    of the asset, at what is around it or at the whole file (``..``,
    ``[not(ancestor::Group)]``, ``[not(//Unlock)]``, ``[not(0-//Unlock)]``),
    which the asset alone answers of nothing; one whose step into the asset
    is not a name (``//Assets/./Asset[1]``), which read alone can stand above
    it; a union or an expression, whose halves are not rewritten; and one
    this does not read to its end.

    The path is read a token at a time for all of it, as the language reads
    it (:func:`_tokens`), and is rewritten only where every part of it is
    known: a rewritten path finds in an asset what the file finds in it and
    no more, and an op is carried out on what its path matches.
    """
    steps = list(_steps(path))
    tokens = _tokens(path)
    if steps[0] or tokens is None or not _only_steps(tokens):
        return [path]
    leading, rests = [], []
    at = 1
    while at < len(steps):
        start, joiner = at, "/"
        if not steps[at]:
            joiner, at = "//", at + 1
            if at >= len(steps) or not steps[at]:
                return [path]
        rests.append((joiner, "/" + "/".join(steps[start:]), steps[at]))
        name = steps[at].strip()
        if name not in _ABOVE_AN_ASSET:
            break
        leading.append((joiner, name))
        at += 1
    if not leading:
        return [path]
    # A path from anywhere can also be all inside the asset: a field named
    # as one of the file's own elements is.
    found = [path] if leading[0][0] == "//" else []
    for taken, (joiner, rest, step) in enumerate(rests[1:], 1):
        holds = _above_holds(leading[:taken], to_the_asset=joiner == "/")
        if holds is None:
            return [path]
        if holds:
            # The step the rest begins with is the asset, or under ``//`` can
            # be: only a name that is not an asset's is surely inside one.
            named = step.split("[", 1)[0].strip()
            if named != "*" and not _STEP_TAG.fullmatch(named):
                return [path]
            can_be_the_asset = joiner == "/" or named in ("*", "Asset")
            if can_be_the_asset and _turns_on_its_place(_tokens(step)):
                return [path]
            if _looks_out(_tokens(rest)):
                return [path]
            found.append(rest)
    return found or [path]


def found_anything(found) -> bool:
    """Whether what a search expression came to counts as finding something,
    the way the loader decides it: a non-empty list -- of nodes, text or
    attributes -- or an expression that comes to ``true()``."""
    if isinstance(found, list):
        return bool(found)
    if isinstance(found, float):
        return found == found and found != 0
    return bool(found)


# -- search answers kept between launches -------------------------------------
# What each search expression matched in the game's own file, written beside
# the index so that the first reading of a session need not sweep again: half
# a minute on a long mod list.  The mods are no part of it -- a reading asks
# again of every asset a mod has changed or made (``Translator._run_search``)
# -- so an answer changes only with the expression, the file and the rule
# that answers an expression of one asset.  The index's own name stands for
# the file; :func:`search_rule` stands for the rule.
SEARCHES_SUFFIX = ".searches.json"


def searches_path_for(db_path: Path) -> Path:
    """Where the search answers of the index at ``db_path`` are kept."""
    db_path = Path(db_path)
    return db_path.with_name(db_path.stem + SEARCHES_SUFFIX)


def search_rule() -> str:
    """What an answer kept on disk was worked out under: the Studio's
    version, lxml's, which reads the paths, and -- for a Studio run from its
    source, whose version does not move when the rule does -- a hash of this
    file, where all of the rule is written (:func:`in_one_asset`,
    :func:`found_anything`, :func:`narrowing`, ``find_matching``).  Any
    change to any of them and the kept answers are not read."""
    from .. import __version__

    rule = "%s|lxml %s" % (__version__, ".".join(map(str, etree.LXML_VERSION)))
    # A built Studio has no source to read, and is its version.
    with contextlib.suppress(OSError):
        rule += "|" + hashlib.sha1(Path(__file__).read_bytes()).hexdigest()
    return rule


def _forget_searches(db_path: Path) -> None:
    with contextlib.suppress(OSError):
        searches_path_for(db_path).unlink()


# How many asset rows to hold before starting over. A mods folder touches a
# thousand assets; a search can walk far more, and none of it is worth a leak.
ROW_CACHE_MAX = 20000


class AssetIndex:
    """Read-only view over a built index.  Cheap to open, safe to keep around."""

    def __init__(self, db_path: Path, source_path: Optional[Path] = None,
                 read_only: bool = False):
        self.db_path = Path(db_path)
        # ``read_only`` is for a second process reading an index the window
        # has open: it can then write nothing into the window's file, texts
        # and schema included.  What a connection keeps for itself (the
        # mods' names, below) is no part of the file.
        if read_only:
            self._con = sqlite3.connect(
                "file:%s?mode=ro" % self.db_path.as_posix(), uri=True,
                check_same_thread=False,
            )
        else:
            self._con = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._con.row_factory = sqlite3.Row
        self._schema: Optional[schema_mod.AssetSchema] = None
        self._fanout: Optional[Dict[tuple, float]] = None
        self._ref_fields: Dict[tuple, Any] = {}
        self._ref_tags: Dict[str, tuple] = {}
        self._rows: Dict[str, tuple] = {}
        self._positions: Dict[str, Any] = {}
        self._top_groups: Dict[str, Any] = {}
        # What the mods that load call things -- a :class:`core.texts.Names`,
        # or None. Held in memory for names and in a table of this connection
        # alone for search, so nothing a mod says is ever written into the
        # cache of the game's own files.
        self._mod_names: Optional[Names] = None
        self._con.execute(
            "CREATE TEMP TABLE IF NOT EXISTS mod_texts (guid TEXT PRIMARY KEY, text TEXT)"
        )
        # What each search expression matched: the file never changes under
        # an index, so a sweep is never worth doing twice for one expression.
        # Written beside the index as well (searches_path_for), and read back
        # from there at the next launch -- by the window's index only: one
        # opened ``read_only`` is handed the window's.
        self._read_only = read_only
        self._matched: Dict[str, List[str]] = {}
        self._writing_searches = threading.Lock()
        self._built = self._index_stamp()
        if not read_only:
            self._matched.update(self._searches_on_disk())
        # The texts a search reads beside the file do change: the game's are
        # read and cleared, the mods' names set.  Counted, so an answer made
        # from them can be told from one that still stands.
        self._texts_stamp = 0
        # The last answer of template_counts_for, as (stamp, words, counts).
        self._template_counts: Optional[tuple] = None
        meta_source = self.meta("source_path")
        self.source_path = Path(source_path) if source_path else Path(meta_source or "")

    # -- metadata -----------------------------------------------------------
    def meta(self, key: str) -> Optional[str]:
        row = self._con.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return row["value"] if row else None

    @property
    def asset_count(self) -> int:
        return int(self.meta("asset_count") or 0)

    @property
    def texts_language(self) -> str:
        """The language of the game's texts loaded into this index, or ""."""
        return self.meta("texts_language") or ""

    @property
    def text_count(self) -> int:
        row = self._con.execute("SELECT COUNT(*) AS n FROM texts").fetchone()
        return int(row["n"]) if row else 0

    def close(self) -> None:
        self._con.close()

    # -- facets -------------------------------------------------------------
    def template_counts(self):
        rows = self._con.execute(
            "SELECT template, COUNT(*) AS n FROM assets "
            "WHERE template <> '' GROUP BY template ORDER BY template"
        ).fetchall()
        return [(r["template"], r["n"]) for r in rows]

    def template_counts_for(self, query: str = ""):
        """Template counts restricted to assets a free-text search would find.

        With ~1,000 templates and no search text yet, the Template filter is a
        wall of choices to scroll a plain combo's type-ahead through.  Once the
        user has typed something, the templates actually worth offering are
        the handful that show up among *those* matches -- so the dropdown is
        recomputed against the same query the results list uses, deliberately
        without a template of its own to narrow by (that would just narrow the
        facet down to whatever is already selected).
        """
        tokens, where, params = self._filter_sql(query, "", "")
        # The last answer is held: the list is asked for on every refresh of
        # the results -- a template picked, an edit made with the list open --
        # and a sweep of every asset for the same words over the same texts
        # is the same answer.  The stamp is read first, so an answer made
        # while texts were being read on their own thread is not kept past
        # the end of the read.
        stamp = self._texts_stamp
        held = self._template_counts
        if held is not None and held[0] == stamp and held[1] == tokens:
            return list(held[2])
        sql = (
            "SELECT a.template, COUNT(*) AS n FROM assets a" + _TEXT_JOINS
            + " WHERE a.template <> ''"
        )
        if where:
            sql += " AND " + " AND ".join(where)
        sql += " GROUP BY a.template ORDER BY a.template"
        rows = self._con.execute(sql, params).fetchall()
        counts = [(r["template"], r["n"]) for r in rows]
        self._template_counts = (stamp, tokens, counts)
        return list(counts)

    def templates(self):
        return [t for t, _n in self.template_counts()]

    def group_paths(self):
        rows = self._con.execute(
            "SELECT DISTINCT group_path FROM assets "
            "WHERE group_path <> '' ORDER BY group_path"
        ).fetchall()
        return [r["group_path"] for r in rows]

    # -- search -------------------------------------------------------------
    def _filter_sql(self, query: str, template: str, group: str):
        tokens = [t for t in query.lower().split() if t]
        where = []
        params = []
        for token in tokens:
            where.append(
                "(a.haystack LIKE ? OR LOWER(COALESCE(t.text,'')) LIKE ? "
                "OR LOWER(COALESCE(m.text,'')) LIKE ? OR LOWER(COALESCE(g.text,'')) LIKE ?)"
            )
            like = "%" + token + "%"
            params.extend([like, like, like, like])
        if template:
            where.append("a.template = ?")
            params.append(template)
        if group:
            where.append("(a.group_path = ? OR a.group_path LIKE ?)")
            params.extend([group, group + " / %"])
        return tokens, where, params

    def search(self, query="", template="", group="", limit=400):
        tokens, where, params = self._filter_sql(query, template, group)

        # A bare GUID is looked up directly: the LIKE scan below is capped, so a
        # very common digit sequence could otherwise push the exact hit off the end.
        exact = None
        stripped = query.strip()
        if stripped.isdigit():
            exact = self.asset_row(stripped)

        sql = (
            "SELECT a.guid, a.name, a.loca, a.loca_key, a.template, a.group_path, "
            "t.text AS loctext FROM assets a" + _TEXT_JOINS
        )
        if where:
            sql += " WHERE " + " AND ".join(where)
        # Pull a generous candidate set, rank it in Python, then trim.
        sql += " LIMIT ?"
        params.append(max(limit * 6, 2000))

        rows = self._con.execute(sql, params).fetchall()
        results = [self._row_from(r) for r in rows]
        results.sort(key=lambda row: _rank(row, tokens))
        if exact is not None:
            results = [exact] + [r for r in results if r.guid != exact.guid]
        return results[:limit]

    def total_matches(self, query="", template="", group="") -> int:
        """Count of everything matching, so the UI can say '400 of 1832'."""
        _tokens, where, params = self._filter_sql(query, template, group)
        sql = "SELECT COUNT(*) AS n FROM assets a" + _TEXT_JOINS
        if where:
            sql += " WHERE " + " AND ".join(where)
        return int(self._con.execute(sql, params).fetchone()["n"])

    # -- searching the whole file -------------------------------------------
    def find_matching(self, paths, progress=None, should_cancel=None):
        """``{path: [guid]}`` for a batch of XPath expressions, in one sweep.

        A mod may aim a ModOp at "every asset shaped like this" rather than at a
        GUID, and the only way to answer that is to look at every asset.  Done
        once for the whole batch, because reading and inflating the file is most
        of the cost and each extra expression is nearly free -- and because a
        mod that searches once usually searches a dozen times.

        An asset is one the path finds anything in (:func:`found_anything`),
        the path read as :func:`in_one_asset` gives it: the rule
        ``modops.matches_anything`` answers one asset by, since the two
        answers are joined wherever a mod has changed or made an asset.

        Each expression is narrowed first by the element names its steps
        mention: an asset whose text does not contain ``<SocketCount`` cannot
        match a path ending in one, and skipping the parse for those is what
        keeps a sweep of a hundred thousand assets to a couple of seconds.

        Each answer is kept, since the file is fixed: reading a mods folder
        asks about every search its mods make, and opening one of those patterns
        afterwards -- or asking again as a search is typed -- is then instant.
        Kept beside the index as well, so that the next launch has them
        (:func:`searches_path_for`).
        """
        paths = list(dict.fromkeys(paths))
        known = {path: list(self._matched[path]) for path in paths if path in self._matched}
        wanted = []
        for path in paths:
            if path in known:
                continue
            try:
                etree.XPath(path)
                asked = [(etree.XPath(one), *narrowing(one)) for one in in_one_asset(path)]
            except etree.XPathError:
                continue
            wanted.append((path, asked))
        found: Dict[str, List] = {path: [] for path, _asked in wanted}
        if not wanted:
            if progress is not None:
                progress(1, 1)
            return known
        stopped = False

        total = self.asset_count or 1
        for position, row in enumerate(self._con.execute("SELECT guid, xml FROM assets")):
            if should_cancel is not None and should_cancel():
                stopped = True
                break
            if progress is not None and not position % 4096:
                progress(position, total)
            try:
                blob = zlib.decompress(row["xml"])
            except zlib.error:
                # A damaged row in our own cache. The two guards below already
                # say that one unreadable asset must not cost the whole sweep --
                # a hundred thousand of them, for a mod that searches once --
                # and this is the same case arriving one step earlier.
                continue
            # Which element names this asset has, read once for every path
            # rather than scanned for once per path.  Eighty expressions over a
            # hundred thousand assets is a lot of scanning; this is one pass.
            present = frozenset(_TAG_IN_XML.findall(blob))
            tree = None
            for path, compiled, needed, one_of in (
                (path, *one) for path, asked in wanted for one in asked
            ):
                if not needed <= present:
                    continue
                if one_of and not any(needle in blob for needle in one_of):
                    continue
                if found[path][-1:] == [row["guid"]]:
                    continue
                if tree is None:
                    try:
                        # As its own document, so that a path written for the
                        # whole file -- "//Asset[…]" included -- means here what
                        # it means there.
                        tree = etree.fromstring(blob).getroottree()
                    except etree.XMLSyntaxError:
                        break
                try:
                    if found_anything(compiled(tree)):
                        found[path].append(row["guid"])
                except etree.XPathError:
                    continue
        if progress is not None:
            progress(total, total)
        if not stopped:
            for path, guids in found.items():
                self._matched[path] = list(guids)
            if found:
                self._write_searches()
        found.update(known)
        return found

    def kept_searches(self) -> Dict[str, List[str]]:
        """What each search expression asked so far matched, to hand to
        another index over the same file (:meth:`keep_searches`)."""
        return {path: list(guids) for path, guids in dict(self._matched).items()}

    def keep_searches(self, found: Dict[str, List[str]]) -> None:
        """Take answers another index over the same file worked out.  The
        file is fixed, so they are this one's answers as well, and the sweep
        that found them need not be made again."""
        new = False
        for path, guids in found.items():
            if path not in self._matched:
                self._matched[path] = list(guids)
                new = True
        if new:
            self._write_searches()

    def _index_stamp(self) -> str:
        """Which build of the index this is: answers found in another build
        at the same place are not this one's.  Read once, when it is opened,
        so that writing the answers from another thread asks nothing of the
        connection."""
        try:
            return self.meta("built_at") or ""
        except sqlite3.Error:
            return ""

    def _searches_on_disk(self) -> Dict[str, List[str]]:
        """The answers written beside the index, if they were worked out
        under this rule (:func:`search_rule`) in this build of it; else
        none.  A file that is not whole, or not what this writes, is none."""
        try:
            kept = json.loads(searches_path_for(self.db_path).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        if (
            not isinstance(kept, dict)
            or kept.get("rule") != search_rule()
            or kept.get("index") != self._built
            or not isinstance(kept.get("searches"), dict)
        ):
            return {}
        return {
            path: [str(guid) for guid in guids]
            for path, guids in kept["searches"].items()
            if isinstance(path, str) and isinstance(guids, list)
        }

    def _write_searches(self) -> None:
        """Write every answer this index holds beside it, for the next
        launch.  Not from an index opened ``read_only``, which writes
        nothing.  Called from whichever thread the answers came in on; never
        raises, since an answer not kept is a sweep at the next launch and
        no more."""
        if self._read_only:
            return
        with self._writing_searches:
            target = searches_path_for(self.db_path)
            # Its own, should two Studios write at once: the last one stands.
            staging = target.with_name(
                "%s.%d-%d.writing" % (target.name, os.getpid(), threading.get_ident())
            )
            kept = {
                "rule": search_rule(),
                "index": self._built,
                "searches": self.kept_searches(),
            }
            try:
                staging.write_text(json.dumps(kept, separators=(",", ":")), encoding="utf-8")
                os.replace(staging, target)
            except OSError:
                with contextlib.suppress(OSError):
                    staging.unlink()

    # -- asset access -------------------------------------------------------
    def asset_xml(self, guid: str):
        row = self._con.execute("SELECT xml FROM assets WHERE guid=?", (guid,)).fetchone()
        if row is None:
            return None
        return zlib.decompress(row["xml"])

    def asset_element(self, guid: str):
        blob = self.asset_xml(guid)
        if blob is None:
            return None
        return etree.fromstring(blob)

    def position(self, guid: str) -> Optional[int]:
        """Where a shipped asset stands in the file, as a number that sorts in
        file order -- the order it was indexed in. What decides which of two
        assets sharing a GUID the loader finds: the first in the file."""
        held = self._positions.get(guid, False)
        if held is not False:
            return held
        row = self._con.execute("SELECT rowid FROM assets WHERE guid=?", (guid,)).fetchone()
        found = None if row is None else int(row[0])
        if len(self._positions) >= ROW_CACHE_MAX:
            self._positions.clear()
        # Asked after every edit, of every asset a mods folder creates beside:
        # a few thousand round trips to SQLite for answers that cannot change.
        self._positions[guid] = found
        return found

    def top_group(self, guid: str) -> Optional[int]:
        """Which of the file's top-level groups a shipped asset is in, counted
        from 0, or None for an asset the file has not.

        The loader looks an asset up by GUID in the top-level group its last
        look-up landed in before anywhere else, so of two assets sharing a
        GUID the first in the file is the one found only where both are in
        one group -- see :meth:`core.reader.Translator._stands_apart`."""
        held = self._top_groups.get(guid, False)
        if held is not False:
            return held
        row = self._con.execute("SELECT top_group FROM assets WHERE guid=?", (guid,)).fetchone()
        found = None if row is None or row[0] is None else int(row[0])
        if len(self._top_groups) >= ROW_CACHE_MAX:
            self._top_groups.clear()
        self._top_groups[guid] = found
        return found

    def asset_row(self, guid: str):
        """One asset's identity, held once it has been looked up.

        The changes list asks for a name per asset on every redraw, so with a
        mods folder loaded that was a thousand round trips to SQLite for a
        thousand answers that cannot change.  Bounded, because a search can walk
        far more assets than a mod ever touches.
        """
        found = self._rows.get(guid)
        if found is not None:
            return found[0]
        row = self._con.execute(
            "SELECT a.guid, a.name, a.loca, a.loca_key, a.template, a.group_path, "
            "t.text AS loctext FROM assets a LEFT JOIN texts t ON t.guid=a.loca_key "
            "WHERE a.guid=?",
            (guid,),
        ).fetchone()
        made = None if row is None else self._row_from(row)
        if len(self._rows) >= ROW_CACHE_MAX:
            self._rows.clear()
        # With the entry its name can come from besides its own GUID: what
        # set_mod_names() needs to know whether the row is still right.
        self._rows[guid] = (made, "" if row is None else row["loca_key"] or "")
        return made

    def display_name(self, guid: str) -> str:
        row = self.asset_row(guid)
        return row.label if row else ("GUID " + guid)

    def _row_from(self, r) -> AssetRow:
        display, named_by, also = self.name_for(
            r["guid"], r["loca"] or "", r["loca_key"] or "", r["loctext"] or "", r["name"] or "",
        )
        return AssetRow(
            guid=r["guid"],
            name=r["name"] or "",
            display=display,
            template=r["template"] or "",
            group_path=r["group_path"] or "",
            named_by=named_by,
            also=also,
        )

    def name_for(self, guid: str, embedded: str, key: str, from_texts: str, name: str):
        """``(display, named_by, also)`` for one asset.

        In order: a mod's text for the asset's own GUID -- it named this asset
        and no other; then the text embedded in the asset; then a mod's text at
        its LineID/OasisId; then the game's; then its internal name.

        Embedded text wins over any lookup by LineID/OasisId, the game's or a
        mod's, and deliberately: such an id is not owned by the asset that
        first used it, and patches reassign them -- Schnapps' ``LineID`` names
        a "Remove" button now, while its embedded text still says Schnapps.
        """
        mods = self._mod_names
        texts: Dict[str, str] = mods.texts if mods is not None else {}
        for found, where in ((texts.get(guid), guid), (embedded, ""), (texts.get(key) if key else None, key)):
            if found:
                if not where or mods is None:
                    return found, "", ()
                return found, mods.by.get(where, ""), tuple(mods.also.get(where, ()))
        return (from_texts or name or ""), "", ()

    # -- what the mods call things ------------------------------------------
    def set_mod_names(self, names) -> bool:
        """Show assets by the names the mods that load give them: ``names``, a
        :class:`core.texts.Names`, or None for none.  Whether anything
        changed -- the table is only rewritten when it did."""
        was = self._mod_names
        before: Dict[str, str] = was.texts if was is not None else {}
        after = names.texts if names is not None else {}
        same = before == after and (
            names is None or was is None
            or (names.by == was.by and names.also == was.also)
        )
        self._mod_names = names if after else None
        if same:
            return False
        self._con.execute("DELETE FROM mod_texts")
        self._con.executemany("INSERT OR REPLACE INTO mod_texts VALUES (?,?)", after.items())
        self._con.commit()
        # Only the rows a moved entry names are let go.  A mod switched off
        # or on moves its own entries and no others, and letting every row
        # go had the next drawing of the Mod changes panel ask for all of
        # them again: 10,083 queries on a list of 417 mods, a third of a
        # second of the first edit after any switch.
        moved = self._moved_names(was, names)
        for guid in [
            guid for guid, held in self._rows.items() if guid in moved or held[1] in moved
        ]:
            del self._rows[guid]
        self._texts_stamp += 1
        return True

    @staticmethod
    def _moved_names(was, now) -> set:
        """The entries that :meth:`name_for` answers differently for between
        two sets of the mods' names: another text, or another mod's."""
        def told(names, key):
            if names is None or key not in names.texts:
                return None
            return names.texts[key], names.by.get(key, ""), tuple(names.also.get(key, ()))

        keys = set(was.texts if was is not None else ()) | set(now.texts if now is not None else ())
        return {key for key in keys if told(was, key) != told(now, key)}

    @property
    def mod_names(self):
        return self._mod_names

    def base_text(self, key: str) -> str:
        """The game's own text for a key, from the texts loaded."""
        row = self._con.execute("SELECT text FROM texts WHERE guid=?", (str(key),)).fetchone()
        return (row["text"] or "") if row else ""

    def text_for(self, key: str) -> str:
        """A key's text as the game will show it: a mod's, else the game's."""
        mods = self._mod_names
        if mods is not None and key in mods.texts:
            return mods.texts[key]
        return self.base_text(key)

    # -- the reference graph ------------------------------------------------
    def references_from(
        self, guid: str, confident_only: bool = True, with_nominal: bool = True
    ):
        """Assets ``guid`` points at, as ``ReferenceLink`` rows."""
        return self._reference_links(
            "SELECT target AS other, path, tag, confident, nominal, MIN(rowid) "
            "FROM refs WHERE src=?",
            guid,
            confident_only,
            with_nominal,
        )

    def references_to(
        self, guid: str, confident_only: bool = True, with_nominal: bool = True
    ):
        """Assets pointing at ``guid``."""
        return self._reference_links(
            "SELECT src AS other, path, tag, confident, nominal, MIN(rowid) "
            "FROM refs WHERE target=?",
            guid,
            confident_only,
            with_nominal,
        )

    def _reference_links(
        self, sql: str, guid: str, confident_only: bool, with_nominal: bool = True
    ):
        if confident_only:
            sql += " AND confident=1"
        if not with_nominal:
            sql += " AND nominal=0"
        # One row per asset, field and asset named, the first in the file: a
        # list that names the same asset twice through the same field is one
        # link. Workspace._walk_links reads an edited asset that way, and the
        # two used to answer the same untouched asset with different counts
        # (P2-17). MIN(rowid) is what makes SQLite take the other columns
        # from the first such row.
        sql += " GROUP BY other, path, tag ORDER BY MIN(rowid)"
        rows = self._con.execute(sql, (str(guid),)).fetchall()
        return [
            ReferenceLink(
                guid=row["other"],
                path=row["path"],
                tag=row["tag"],
                confident=bool(row["confident"]),
                nominal=bool(row["nominal"]),
            )
            for row in rows
        ]

    def reference_counts(
        self, guid: str, confident_only: bool = True, with_nominal: bool = True
    ):
        """``(outgoing, incoming)`` counts, without building the rows: one per
        link, as :meth:`references_from` and :meth:`references_to` give them."""
        clause = " AND confident=1" if confident_only else ""
        if not with_nominal:
            clause += " AND nominal=0"
        guid = str(guid)
        out = self._con.execute(
            "SELECT COUNT(*) AS n FROM (SELECT 1 FROM refs WHERE src=?" + clause
            + " GROUP BY target, path, tag)", (guid,)
        ).fetchone()["n"]
        into = self._con.execute(
            "SELECT COUNT(*) AS n FROM (SELECT 1 FROM refs WHERE target=?" + clause
            + " GROUP BY src, path, tag)", (guid,)
        ).fetchone()["n"]
        return int(out), int(into)

    def _ref_field(self, path: str, tag: str):
        """One row of the per-field evidence, held once it has been asked for.

        These are asked once per numeric leaf of every asset the session walks,
        and the same handful of fields come round again and again -- with a mods
        folder loaded that was tens of thousands of round trips to SQLite for a
        few hundred distinct answers.
        """
        key = (path, tag)
        if key not in self._ref_fields:
            row = self._con.execute(
                "SELECT n, resolved, entries, valued, telling FROM ref_fields "
                "WHERE path=? AND tag=?",
                (path, tag),
            ).fetchone()
            self._ref_fields[key] = (
                (0, 0, 0, 0, False)
                if row is None
                else (
                    int(row["n"]),
                    int(row["resolved"] or 0),
                    int(row["entries"] or 0),
                    int(row["valued"] or 0),
                    bool(row["telling"]),
                )
            )
        return self._ref_fields[key]

    def reference_field(self, path: str, tag: str):
        """What the base game's use of this field says of it: ``(seen,
        resolved, telling)`` -- how many values, how many of them a real GUID,
        and whether those GUIDs are more than plain numbers.  What
        :func:`refs.is_confident` and :func:`refs.is_plausible` are asked with."""
        found = self._ref_field(path, tag)
        return found[0], found[1], found[4]

    def reference_tag(self, tag: str):
        """:meth:`reference_field` for ``tag`` wherever the game has it: for a
        value whose place is not known, such as one in a ModOp aimed by a
        search.  Summed over the fields; telling where any of them is."""
        if tag not in self._ref_tags:
            row = self._con.execute(
                "SELECT SUM(n), SUM(resolved), MAX(telling) FROM ref_fields WHERE tag=?",
                (tag,),
            ).fetchone()
            self._ref_tags[tag] = (int(row[0] or 0), int(row[1] or 0), bool(row[2]))
        return self._ref_tags[tag]

    def reference_entries(self, path: str, tag: str):
        """``(links, how many of them set a value)`` for this field."""
        found = self._ref_field(path, tag)
        return found[2], found[3]

    def reference_fanout(self, path: str, tag: str) -> float:
        """How many assets a typical source names through this one field.

        A field with a wide fan-out is a catalogue -- an asset pool listing
        fifty buildings, a construction menu listing twenty -- and being listed
        in one says nothing about what an asset belongs to.  A field with a
        narrow one names a particular asset for a particular purpose, which is
        what :mod:`.grouping` reads as ownership.

        Built in one pass the first time it is asked for (about 50ms on either
        shipped file) and then held: the alternative is a GROUP BY per lookup.
        """
        if self._fanout is None:
            self._fanout = {
                (row[0], row[1]): row[2] / row[3]
                for row in self._con.execute(
                    "SELECT path, tag, COUNT(*), COUNT(DISTINCT src) FROM refs "
                    "WHERE confident=1 AND nominal=0 GROUP BY path, tag"
                )
                if row[3]
            }
        return self._fanout.get((path, tag), 0.0)

    def is_reference_field(self, path: str, tag: str) -> bool:
        """Whether this exact field is an established link in the base game."""
        row = self._con.execute(
            "SELECT confident FROM ref_fields WHERE path=? AND tag=?", (path, tag)
        ).fetchone()
        return bool(row and row["confident"])

    # -- schema -------------------------------------------------------------
    @property
    def schema(self):
        """Query interface over the schema stored alongside the assets.

        Held, not rebuilt per access: the editor asks the schema a question for
        every node it draws, and those answers are cached on the object.  Handing
        out a fresh one each time threw the cache away on every call, which cost
        about a second and a half to open a large asset.
        """
        if self._schema is None:
            self._schema = schema_mod.AssetSchema(self._con)
        return self._schema

    def load_schema_files(self, templates_path=None, properties_path=None):
        """Import ``templates.xml`` / ``properties.xml``.

        Optional: without them the editor still works from the observed schema,
        but cannot offer whole sections or authoritative list-entry shapes.
        Returns ``(templates, defaults)`` counts.
        """
        templates = defaults = 0
        if templates_path:
            rows = [
                (name, ",".join(sections))
                for name, sections in schema_mod.parse_templates(Path(templates_path)).items()
            ]
            self._con.execute("DELETE FROM schema_templates")
            self._con.executemany("INSERT OR REPLACE INTO schema_templates VALUES (?,?)", rows)
            templates = len(rows)
        if properties_path:
            section_defaults, containers = schema_mod.parse_properties(Path(properties_path))
            kinds = [("section", name, xml) for name, xml in section_defaults.items()]
            kinds += [("container", name, xml) for name, xml in containers.items()]
            self._con.execute("DELETE FROM schema_defaults")
            self._con.executemany("INSERT OR REPLACE INTO schema_defaults VALUES (?,?,?)", kinds)
            defaults = len(kinds)
        self._con.commit()
        self._schema = None   # its caches predate these files
        return templates, defaults

    @property
    def schema_path_count(self) -> int:
        row = self._con.execute("SELECT COUNT(*) AS n FROM schema_children").fetchone()
        return int(row["n"]) if row else 0

    def guid_exists(self, guid: str) -> bool:
        row = self._con.execute(
            "SELECT 1 FROM assets WHERE guid=? LIMIT 1", (str(guid),)
        ).fetchone()
        return row is not None

    # -- localisation -------------------------------------------------------
    def clear_texts(self) -> None:
        """Drop imported localisation, falling back to internal names."""
        self._con.execute("DELETE FROM texts")
        self._con.execute("DELETE FROM meta WHERE key='texts_language'")
        self._con.commit()
        self._rows.clear()
        self._texts_stamp += 1

    def load_texts(self, texts_path: Path) -> int:
        """Import a ``texts_*.xml`` so the browser can show in-game names.

        Anno 1800 keys each entry with ``<GUID>``, Anno 117 with ``<LineId>`` --
        same document shape, different tag.  Both are tried; whichever is
        present identifies the outer ``<Text>`` record (the inner ``<Text>``
        holding the string has neither).
        """
        count = 0
        batch = []
        # Before the first text goes and again after the last is in: this
        # runs on a thread of its own, and what is asked in between is asked
        # of texts half read.
        self._texts_stamp += 1
        self._con.execute("DELETE FROM texts")
        # Which language, so mods' texts are read in the same one -- kept with
        # the texts, which outlive the session that loaded them.
        from .texts import language_of

        self._con.execute(
            "INSERT OR REPLACE INTO meta VALUES ('texts_language', ?)", (language_of(texts_path),)
        )
        with open(texts_path, "rb") as fh:
            context = etree.iterparse(
                fh, events=("end",), tag="Text", recover=True, huge_tree=True,
                resolve_entities="internal", no_network=True,
            )
            for _event, elem in context:
                guid = elem.findtext("GUID") or elem.findtext("LineId")
                if guid:  # outer <Text> record; the inner <Text> has neither key
                    value = (elem.findtext("Text") or "").strip()
                    guid = guid.strip()
                    if guid and value:
                        batch.append((guid, value))
                        count += 1
                    if len(batch) >= 500:
                        self._con.executemany(
                            "INSERT OR REPLACE INTO texts VALUES (?,?)", batch
                        )
                        batch = []
                    elem.clear()
                    parent = elem.getparent()
                    if parent is not None:
                        while elem.getprevious() is not None:
                            del parent[0]
        if batch:
            self._con.executemany("INSERT OR REPLACE INTO texts VALUES (?,?)", batch)
        self._con.commit()
        # Every asset's display name may have just changed.
        self._rows.clear()
        self._texts_stamp += 1
        return count


# The game's texts by the asset's LineID/OasisId, and the mods' by that and by
# the asset's own GUID -- see :meth:`AssetIndex.name_for`.
_TEXT_JOINS = (
    " LEFT JOIN texts t ON t.guid = a.loca_key"
    " LEFT JOIN mod_texts m ON m.guid = a.loca_key"
    " LEFT JOIN mod_texts g ON g.guid = a.guid"
)


def _rank(row: AssetRow, tokens: Sequence[str]):
    """Sort key: relevance bucket first, then real assets before text assets."""
    is_text = 1 if (row.template or "").lower() in TEXT_TEMPLATES else 0
    if not tokens:
        return (5, is_text, (row.display or "").lower(), row.guid)
    joined = " ".join(tokens)
    name_lc = (row.name or "").lower()
    disp_lc = (row.display or "").lower()
    if row.guid.lower() == joined:
        bucket = 0
    elif name_lc == joined or disp_lc == joined:
        bucket = 1
    elif name_lc.startswith(joined) or disp_lc.startswith(joined):
        bucket = 2
    elif joined in name_lc or joined in disp_lc:
        bucket = 3
    else:
        bucket = 4
    return (bucket, is_text, len(name_lc or disp_lc), disp_lc, row.guid)
