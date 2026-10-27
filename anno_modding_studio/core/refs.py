"""Deciding which numeric fields are really links to another asset.

Anno stores cross-references as bare integers and declares nothing: there is no
schema saying ``<Product>`` points at an asset while ``<Amount>`` counts one.
:func:`could_be_reference` guesses per value, from the field's name plus
"does it resolve" -- good enough to annotate a single row, too weak to draw a graph
from, because with 96,000 GUIDs in Anno 1800 roughly half of all plausible
integers resolve *by coincidence*.

There is a much stronger signal available once the whole file has been read:
how often a given field resolves, measured across every asset that has it.
That number is sharply bimodal -- a field either essentially always resolves
or essentially never does:

======================  ==========  ==========
resolution rate         Anno 117    Anno 1800
======================  ==========  ==========
always (100%)            2,844        7,206
never (0%)               5,982        5,578
ambiguous (5-95%)        5% of        30% of
                         values       values
======================  ==========  ==========

So a field is classified once, from its own aggregate, rather than a value at
a time.  Measured against both shipped games, the rule below accepts every
reference field checked (``Product``, ``Ingredient``, ``ItemLink``,
``ProvidedNeed``, ``Good``, every ``*GUID``) and **no** quantity field --
including the ones that defeat a name-based guess: Anno 1800's
``DurationMaximum``/``DurationMinimum`` hold 79,000 millisecond durations of
which 67% happen to be valid GUIDs, and ``LineID``, ``InheritedIndex`` and
``Amount`` all collide in the tens of thousands.

The trade is deliberate: a field is rejected unless the evidence is strong, so
a rare path with three samples is dropped even when its links are real.  Those
edges are kept but marked uncertain rather than thrown away, where the field's
name and the number's size agree that it could be a link (:func:`is_link`):
a value under :data:`MIN_INFERRED_GUID`, or any in a field named for a
quantity, is not kept.  The graph draws them only when asked, since one honest
"I am not sure" beats a confident lie.

The rate has one blind side, and "no quantity field" above was wrong by it.
It counts how many times a field resolves, and a field that holds the same two
numbers 757 times has been right twice, not 757 times.  A building's
``BuildModeRandomRotation`` is 90 or 180 degrees, Anno 1800 has an asset with
each of those GUIDs (72% of all its three-digit numbers are one), and so every
rotation "resolved" and every building was drawn as pointing at a character
called Visitor.  What tells such a field from a link is in the same file: 90
and 180 are also values of fields that are plainly not links -- ones that
resolve under :data:`PLAUSIBLE_RATE` of the time -- while 1010196 is a value
of none.  So a field has to be *telling* as well (:func:`is_telling`): at most
half of the different GUIDs it holds may be numbers the game uses as plain
numbers elsewhere.  Where a field alone is not, the same tag taken over the
whole game may vouch for it: ``Profile`` under one notification holds only
220, a plain number, but ``Profile`` across the game holds 103 different
GUIDs, and 86 of them are plain numbers nowhere.

Measured on both shipped games, of the fields the rate alone called links:

======================  ==========  ==========
                        Anno 117    Anno 1800
======================  ==========  ==========
called links by rate     1,011        2,044
not telling                  2           79
  of them quantities         2           78
vouched for by the tag       2            5
======================  ==========  ==========

The one link lost is ``Monument/PhaseCompletedNotification``, 14 ruins all
naming the notification with GUID 18.  Still wrongly kept in Anno 1800, that
were found by reading the list: a ``NotificationPriority`` of 1001 and one of
550, ``CellSizeX`` 550, ``ShipSketchPosY`` and ``UplayAction/BadgeID``, 44
values in all, none of whose numbers a plainly-not-a-link field happens to use.
A field that is not telling is not offered as an uncertain link either: its
values are quantities that collide, which is what the uncertain view was
already kept clear of.
"""

from __future__ import annotations

from .i18n import number, tr, trn

# A field must resolve at least this often to be called a reference outright.
# 0.95 rather than 1.0: real reference fields do carry the odd dangling value
# (content cut from the shipped file, or a 0/-1 sentinel), and demanding
# perfection dropped several thousand genuine links for no gain in precision.
CONFIDENT_RATE = 0.95

# ...over at least this many values. Below it the rate is noise: one lucky hit
# out of two is 50%, and Anno 1800 has enough GUIDs to make that common.
MIN_SAMPLES = 4

# Below this a field is a quantity that sometimes collides, not a link with
# exceptions, and its edges are dropped outright rather than offered as
# "uncertain" -- showing 53,000 audio durations as maybe-links would bury the
# real ones. Chosen from where the shipped data actually separates: Anno 117's
# genuinely-ambiguous reference fields sit at 28% (`Infotip`) and up, while its
# colliding quantities sit at 22% (`Position/X`) and down.
PLAUSIBLE_RATE = 0.25

# How many of the different GUIDs a field holds may be plain numbers elsewhere
# before the field says nothing: over half, and it holds more ordinary numbers
# than not.  The shipped games do not turn on the exact figure: between 0.4 and
# 0.6 one field of each changes sides.  Lower, and real links start to go, to
# assets whose GUIDs are small: three at a third, seven at a quarter.  Higher,
# and fields of quantities come back: one at two thirds, three at three
# quarters.
PLAIN_SHARE = 0.5


def names_a_guid(tag: str) -> bool:
    """A field whose name says what it holds. The one dependable signal."""
    return "guid" in tag.lower()


def holds_plain_numbers(tag: str, seen: int, resolved: int) -> bool:
    """Whether a field's own numbers say it is plainly not a link.

    Enough values to judge by, and so few of them GUIDs that it is not even
    offered as uncertain.  Whatever such a field holds is a plain number, and
    that is what the game is asked about every other field's values.
    """
    if seen < MIN_SAMPLES or (names_a_guid(tag) and resolved > 0):
        return False
    return resolved / seen < PLAUSIBLE_RATE


def is_telling(targets: int, plain: int) -> bool:
    """Whether the GUIDs a field holds say more than plain numbers would.

    ``targets`` is how many different GUIDs it holds, and ``plain`` how many
    of those are also values of a field that :func:`holds_plain_numbers`.
    Different ones, not how often: 90 on 428 buildings is one number.
    """
    return targets > 0 and plain <= targets * PLAIN_SHARE


def is_confident(tag: str, seen: int, resolved: int, telling: bool = True) -> bool:
    """Whether a field's own numbers say it holds references.

    ``telling`` is what the index found by :func:`is_telling`, for the field
    or for its tag across the game.  Left out, the rate is all there is to go
    by, which is the case for numbers that did not come from an index.
    """
    if seen <= 0 or resolved <= 0:
        return False
    if names_a_guid(tag):
        return True
    if seen < MIN_SAMPLES:
        return False
    return telling and resolved / seen >= CONFIDENT_RATE


def is_plausible(tag: str, seen: int, resolved: int, telling: bool = True) -> bool:
    """Whether a field is worth offering as an uncertain link at all.

    Confident fields qualify trivially.  Beyond them: a field too rarely used
    to judge (a real reference at an unusual path), or one that resolves often
    enough to be a reference with exceptions -- but not one that resolves at
    the rate bare chance would produce, and not one whose GUIDs are plain
    numbers elsewhere, however often they resolve.
    """
    if seen <= 0 or resolved <= 0:
        return False
    if is_confident(tag, seen, resolved, telling):
        return True
    if not telling:
        return False  # quantities that collide, not a link with exceptions
    if seen < MIN_SAMPLES:
        return True  # too little evidence to convict, so let the user look
    return resolved / seen >= PLAUSIBLE_RATE


def confidence_note(tag: str, seen: int, resolved: int, telling: bool = True) -> str:
    """How to explain this field's standing, in one line.

    Takes the same evidence as :func:`is_confident` and says what it decided:
    only a confident field is called a link, so the note cannot call a link
    drawn as uncertain a sure one.
    """
    confident = is_confident(tag, seen, resolved, telling)
    if confident and names_a_guid(tag):
        return tr("Named as a GUID, so this is a link.")
    if seen < MIN_SAMPLES:
        return trn(
            "Only %(n)s value in the base game to judge by, so this link is a "
            "guess.",
            "Only %(n)s values in the base game to judge by, so this link is a "
            "guess.",
            seen,
        )
    if not telling:
        return tr(
            "Most of the GUIDs in this field are plain numbers elsewhere in the "
            "game, so this may be a coincidence and not a link."
        )
    rate = number(100.0 * resolved / seen)
    if confident:
        return trn(
            "%(rate)s%% of the %(n)s value in this field are asset GUIDs, so "
            "this is a link.",
            "%(rate)s%% of the %(n)s values in this field are asset GUIDs, so "
            "this is a link.",
            seen, rate=rate,
        )
    return trn(
        "Only %(rate)s%% of the %(n)s value in this field are asset GUIDs, "
        "so this may be a coincidence and not a link.",
        "Only %(rate)s%% of the %(n)s values in this field are asset GUIDs, "
        "so this may be a coincidence and not a link.",
        seen, rate=rate,
    )


# A reference sitting alone in a list entry may be doing nothing at all. An
# `<Item><Ingredient>2176</Ingredient></Item>` in a Costs list names a good and
# attaches no amount to it, so the building does not actually cost it -- Anno
# 117's Tiles is "referenced" that way by 132 assets that never spend one.
#
# Whether that means anything is not knowable from the file: a missing `Amount`
# means zero, but a missing `Probability` next to a pool entry means *normal*,
# and nothing says which default applies. What can be measured is whether a
# path uses values at all. Above this share, entries there normally say
# something beyond the name, so one that does not is a placeholder; below it
# the path is a plain list of pointers and a bare entry is simply how it looks.
#
# 0.25 because the shipped data separates cleanly there: Cost/Costs/Item sets
# an amount 53% of the time, while AssetPool/AssetList/Item (where membership
# alone is the point) sets its optional Probability only 15% of the time.
USES_VALUES_RATE = 0.25


def has_content(elem) -> bool:
    """Whether this node says anything -- a real value, or a filled container."""
    if len(elem):
        return any(has_content(child) for child in elem if isinstance(child.tag, str))
    text = (elem.text or "").strip()
    return bool(text) and text not in ("0", "0.0", "0.00", "False")


def carries_value(parent, child) -> bool:
    """Whether the entry holding ``child`` says anything besides naming it."""
    if parent is None:
        return False
    return any(
        has_content(other)
        for other in parent
        if other is not child and isinstance(other.tag, str)
    )


def path_uses_values(entries: int, valued: int) -> bool:
    """Whether entries at this path normally carry more than the pointer."""
    if entries <= 0:
        return False
    return valued / entries >= USES_VALUES_RATE


# GUIDs are 32-bit signed numbers, and a mod's own sit at the top of that: the
# shipped games stop at 8 digits, but Anno 117's register hands mods
# 1,337,471,142 to 2,147,483,647, so a link to an asset a mod makes is a
# 10-digit number.  Anything larger is an id of another kind -- a text's
# 19-digit hash, say -- and not worth carrying.
LARGEST_GUID = 2 ** 31 - 1


def fits_a_guid(text: str) -> bool:
    """Whether a field's text is a number a GUID could be."""
    return (
        text.isascii() and text.isdigit() and len(text) <= 10
        and int(text) <= LARGEST_GUID
    )


def numeric_leaves(values_elem, root_path: str = "/Values"):
    """Yield ``(path, tag, value, carries)`` for every integer-valued leaf.

    ``carries`` says whether the element holding the leaf sets anything else,
    which is what separates a cost of three tiles from a costs list that
    merely mentions tiles.

    The same walk the schema collector does, kept separate because it runs
    against a live edited asset too, not only during an index build.
    """
    stack = [(values_elem, root_path)]
    while stack:
        elem, path = stack.pop()
        for child in elem:
            tag = child.tag
            if not isinstance(tag, str):
                continue
            if len(child):
                stack.append((child, path + "/" + tag))
                continue
            text = (child.text or "").strip()
            if fits_a_guid(text):
                yield path, tag, text, carries_value(elem, child)


# -- one value at a time --------------------------------------------------------
# Where there is no evidence yet -- a field too rare to have a rate, or a row
# shown before the index has been read -- the editor still has to say something
# about a number, and guesses from the field's name.  A wrong guess only adds a
# misleading label, never a wrong ModOp; the rules, most confident first:
#
# * a field whose name contains "guid" is a reference if the value resolves;
# * any other numeric field is a reference if the value resolves *and* is large
#   enough that it is unlikely to be a quantity (real GUIDs below 1000 exist,
#   but so do amounts and percentages, and quantities win that collision);
# * fields that are known to hold plain numbers are never treated as references.

# Names that are unambiguously counts, prices or timings. Anno reuses these
# across thousands of assets, so excluding them removes almost all false hits.
NUMERIC_FIELDS = frozenset(
    {
        "amount",
        "attractiveness",
        "basemorale",
        "chance",
        "cost",
        "count",
        "cycletime",
        "duration",
        "height",
        "influence",
        "lineid",
        "maxstacksize",
        "moveespeed",
        "order",
        "percental",
        "radius",
        "range",
        "residents",
        "size",
        "speed",
        "storageamount",
        "tradeprice",
        "tradepriceonlinecurrency",
        "value",
        "weight",
        "width",
    }
)

# Below this, a bare number is much more likely to be a quantity than a GUID.
MIN_INFERRED_GUID = 1000


def is_numeric_field(tag: str) -> bool:
    return tag.lower() in NUMERIC_FIELDS


def least_inferred_guid(tag: str):
    """The smallest value a field named ``tag`` can hold and be guessed a link
    by its name, or ``None`` when no value can: :func:`could_be_reference` as
    one number, which is how the index build asks it of a whole field."""
    if is_numeric_field(tag):
        return None
    if names_a_guid(tag):
        return 0
    return MIN_INFERRED_GUID


def could_be_reference(tag: str, value: str) -> bool:
    """Whether one value in one field is shaped like a link. Does not
    consult the index."""
    value = (value or "").strip()
    if not value.isdigit():
        return False
    least = least_inferred_guid(tag)
    return least is not None and int(value) >= least


def is_link(tag: str, value: str, seen: int, resolved: int, telling: bool = True) -> bool:
    """Whether one value in one field is to be shown as a link, if it lands on
    an asset -- the one rule the editor's Refers-to column and the References
    graph both answer by.

    ``(seen, resolved, telling)`` is the base game's use of this exact field,
    as :meth:`AssetIndex.reference_field` gives it; ``seen`` of 0 where the
    game never uses the field there.  A confident field links whatever it
    holds.  Short of that the field's own record is not enough to tell a link
    from a quantity that lands on a GUID, and the field's name and the value's
    size have to agree too: a plausible field holding 110 is more likely a
    count than building 110.
    """
    if seen:
        if not is_plausible(tag, seen, resolved, telling):
            return False
        if is_confident(tag, seen, resolved, telling):
            return True
    return could_be_reference(tag, value)


def resolve(index, tag: str, value: str):
    """The asset one value links to, by the guess above, or None.

    ``index`` may be None, in which case nothing is ever resolved.
    """
    if index is None or not could_be_reference(tag, value):
        return None
    return index.asset_row(value.strip())
