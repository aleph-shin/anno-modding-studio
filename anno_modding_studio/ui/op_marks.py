"""What a ModOp is, drawn: a badge for what it does and a strip of marks for
what the game works out about it as it loads.

Words say the same things beside these and in the tooltip, so nothing here is
the only way to know; the drawing is for telling one kind of op from another
down a long list without reading.

**The badge** is the op's type, filled and in a colour: add, add after, add
before, remove, replace, merge.  Drawn as a dashed outline for an op its mod
ships switched off (``Skip``), which is the op's own state and not the box
beside it.

**The strip** is four slots, each with one meaning and empty where it does not
apply, so an op's shape is the same silhouette wherever it turns up:

1. target -- a magnifying glass where what the op lands on is a pattern, its
   lens filled when it finds something today;
2. payload -- a calculator where the value is copied or worked out;
3. condition -- a diamond where the op or its group asks something first,
   filled when that holds today, hollow when it does not, half when the
   Studio cannot say;
4. options -- a gear where the op answers to the mod's own options.

Every image is an SVG filled from the scheme in effect, as the indicators in
:mod:`ui.theme` are, and kept by what it shows.
"""

from __future__ import annotations

from typing import Dict, Tuple

from PySide6.QtCore import QByteArray, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import QApplication

from . import theme

BADGE = 18          # the badge's side, in logical pixels
MARK = 14           # a mark's side
SLOT = 15           # from one slot's left edge to the next
GAP = 4             # between the badge and the first slot
HEIGHT = BADGE

# What a badge can be: the op types, and a group, which has ops in it.
ADD, ADD_AFTER, ADD_BEFORE, REMOVE, REPLACE, MERGE, GROUP = (
    "add", "addNextSibling", "addPrevSibling", "remove", "replace", "merge", "group",
)
# A slot's states.  A condition has three: see the module's own words.
ON, OFF, UNKNOWN = 1, 2, 3  # and 0 for a slot left empty

_VERBS = {
    ADD: "M8 4.5v7M4.5 8h7",
    # An L and a plus in the corner it leaves: the new one goes beside what
    # is there, below the L's foot for after and above its head for before.
    ADD_AFTER: "M4.2 3.4v8.2h7.4M9.2 3.7v5M6.7 6.2h5",
    ADD_BEFORE: "M4.2 12.6V4.4h7.4M9.2 7.3v5M6.7 9.8h5",
    REMOVE: "M4.5 8h7",
    REPLACE: "M4 6h7.5l-2-2M12 10H4.5l2 2",
    MERGE: "M3.5 4.5c2.6 0 2.4 3.5 5 3.5h4M3.5 11.5c2.6 0 2.4-3.5 5-3.5",
    GROUP: "M6.2 3.6H4.4v8.8h1.8M9.8 3.6h1.8v8.8H9.8",
}
# A badge's colour is its type's and not the theme's: green for what is
# added, blue for what is replaced, red for what is removed, purple for what
# is merged.  Four of them for the four kinds of scheme, since a colour that
# reads on a dark surface does not on a light one.
_HUE = {ADD: 0, ADD_AFTER: 0, ADD_BEFORE: 0, REPLACE: 1, REMOVE: 2, MERGE: 3}
# How near in hue, in degrees, a badge's colour may come to the scheme's
# accent, which is the colour of the box beside it, before it is turned off
# it; and how far it is turned.
_TOO_NEAR = 30
_TURNED = 40
# What a badge and a mark keep against what they are drawn on, as any
# drawing that says something does.
_FLOOR = 3.0


def _apart(one: float, two: float) -> float:
    """How far apart two hues are, in degrees, the short way round."""
    return min(abs(one - two) % 360, 360 - abs(one - two) % 360)


def _tone(verb: str) -> str:
    """The colour a type's badge is drawn in, in the scheme in effect."""
    if verb not in _HUE:
        return theme.colour("text-dim")
    scheme = theme.scheme()
    colour = theme.OP_HUES[(scheme.dark, scheme.contrast)][_HUE[verb]]
    one, box = QColor(colour), QColor(theme.colour("accent"))
    hue, box_hue = one.hslHue(), box.hslHue()  # -1 for a grey, which has none
    if hue >= 0 and box_hue >= 0 and _apart(hue, box_hue) < _TOO_NEAR:
        # A tick box and a badge in one colour, side by side, read as a
        # pair: Timber's accent is the green of an addition and Quartz's the
        # blue of a replace.  Turned away from the box's hue, it keeps its
        # lightness, and so what it stands out against: made lighter or
        # darker instead, a badge on a light scheme went pale.  Judged by
        # hue, the gold of the dark schemes' box is not the red of a remove.
        ahead = (hue - box_hue) % 360
        turn = _TURNED if 0 < ahead < 180 else -_TURNED
        _hue, saturation, lightness, _alpha = one.getHslF()
        colour = QColor.fromHslF(((box_hue + turn) % 360) / 360, saturation, lightness).name()
    return _kept_against(colour, theme.colour("bg"))


def _kept_against(colour: str, ground: str) -> str:
    """``colour``, made lighter or darker until it holds the floor against
    ``ground``."""
    one = QColor(colour)
    hue, saturation, lightness, _alpha = one.getHslF()
    step = 0.03 if theme.luminance(ground) < 0.2 else -0.03
    while theme.contrast_ratio(colour, ground) < _FLOOR and 0 < lightness < 1:
        lightness = min(1.0, max(0.0, lightness + step))
        colour = QColor.fromHslF(max(hue, 0.0), saturation, lightness).name()
    return colour


def _on_selection() -> Tuple[str, str]:
    """A selected row's fill, and the colour its words are drawn in there."""
    if theme.scheme().contrast:
        return theme.colour("accent"), theme.colour("text-on-accent")
    return theme.colour("selection"), theme.colour("text")


def _svg(body: str) -> str:
    return '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16">%s</svg>' % body


def _stroke(d: str, colour: str, width: float = 1.6) -> str:
    return (
        '<path d="%s" fill="none" stroke="%s" stroke-width="%s" '
        'stroke-linecap="round" stroke-linejoin="round"/>' % (d, colour, width)
    )


def _mix(a: str, b: str, share: float) -> str:
    """``a`` with ``share`` of ``b`` in it."""
    one, two = QColor(a), QColor(b)
    return QColor(
        round(one.red() * (1 - share) + two.red() * share),
        round(one.green() * (1 - share) + two.green() * share),
        round(one.blue() * (1 - share) + two.blue() * share),
    ).name()


def _badge(verb: str, hollow: bool, selected: bool = False) -> str:
    """A type's badge; on a selected row, given an edge in the row's words'
    colour where the fill alone would be lost against the row's."""
    tone = _tone(verb)
    fill, words = _on_selection()
    lost = selected and theme.contrast_ratio(tone, fill) < _FLOOR
    if hollow:
        # Nothing but its outline and its sign: on a row it would be lost
        # against, both are drawn in the words' colour.
        tone = words if lost else tone
        box = (
            '<rect x="1.5" y="1.5" width="13" height="13" rx="3" fill="none" stroke="%s" '
            'stroke-width="1.3" stroke-dasharray="2.2 1.8"/>' % tone
        )
        return _svg(box + _stroke(_VERBS[verb], tone))
    box = '<rect x="1" y="1" width="14" height="14" rx="3" fill="%s"/>' % tone
    if lost:
        box = (
            '<rect x="1" y="1" width="14" height="14" rx="3" fill="%s" stroke="%s" '
            'stroke-width="1"/>' % (tone, words)
        )
    return _svg(box + _stroke(_VERBS[verb], theme.colour("bg"), 1.8))


# What each mark is drawn in: its own colour, and on a selected row the
# row's words' colour where its own would be lost against the row.
def _ink(own: str, selected: bool) -> str:
    fill, words = _on_selection()
    return words if selected and theme.contrast_ratio(own, fill) < _FLOOR else own


def _pattern(state: int, selected: bool = False) -> str:
    colour = _ink(theme.colour("rule"), selected)
    lens = '<circle cx="6.8" cy="6.8" r="4.1" fill="%s" stroke="%s" stroke-width="1.5"/>' % (
        colour if state == ON else "none", colour,
    )
    return _svg(lens + _stroke("M10 10l3.6 3.6", colour, 1.9))


def _computed(_state: int, selected: bool = False) -> str:
    colour = _ink(theme.colour("text"), selected)
    body = (
        '<rect x="3" y="1.8" width="10" height="12.4" rx="1.6" fill="none" stroke="%s" '
        'stroke-width="1.3"/>' % colour
    )
    display = '<rect x="5" y="3.8" width="6" height="2.3" fill="%s"/>' % colour
    keys = "".join(
        '<rect x="%s" y="%s" width="2" height="2" fill="%s"/>' % (x, y, colour)
        for x in (5, 9) for y in (7.9, 10.7)
    )
    return _svg(body + display + keys)


def _conditional(state: int, selected: bool = False) -> str:
    colour = _ink(theme.colour("warn"), selected)
    outline = "M8 2.2l5.8 5.8L8 13.8 2.2 8z"
    if state == ON:
        return _svg(
            '<path d="%s" fill="%s" stroke="%s" stroke-width="1.4" stroke-linejoin="round"/>'
            % (outline, colour, colour)
        )
    if state == UNKNOWN:
        # The lower half filled: it asks, and what it was answered is not known.
        half = '<path d="M2.2 8L8 13.8 13.8 8z" fill="%s"/>' % colour
        return _svg(half + _stroke(outline, colour, 1.4))
    return _svg(_stroke(outline, colour, 1.4))


def _options(_state: int, selected: bool = False) -> str:
    colour = _ink(_mix(theme.colour("text-dim"), theme.colour("ok"), 0.45), selected)
    teeth = "".join(
        '<rect x="6.6" y="1.3" width="2.8" height="3.4" rx="0.5" fill="%s" '
        'transform="rotate(%d 8 8)"/>' % (colour, turn) for turn in range(0, 360, 60)
    )
    # A ring and not a disc with a dot on it, so the hub is the row's own
    # colour whichever row it is on.
    wheel = (
        '<circle cx="8" cy="8" r="3.3" fill="none" stroke="%s" stroke-width="2.7"/>' % colour
    )
    return _svg(teeth + wheel)


_SLOTS = (_pattern, _computed, _conditional, _options)

_made: Dict[Tuple, QIcon] = {}


def forget() -> None:
    """Drop every image made so far: the scheme they were coloured from has
    changed."""
    _made.clear()


def _draw(painter: QPainter, svg: str, x: float, y: float, side: float) -> None:
    QSvgRenderer(QByteArray(svg.encode("utf-8"))).render(painter, QRectF(x, y, side, side))


def width(strip: bool) -> int:
    """How wide an op's drawing is: the badge alone, or with its strip."""
    return BADGE + (GAP + SLOT * len(_SLOTS) if strip else 0)


def icon_size() -> QSize:
    """The most room any row's drawing takes, for the view to leave."""
    return QSize(width(True), HEIGHT)


def picture(svg: str, side: int = MARK) -> QPixmap:
    """One drawing alone, for a legend."""
    app = QApplication.instance()
    ratio = app.devicePixelRatio() if app is not None else 1.0
    image = QPixmap(round(side * ratio), round(side * ratio))
    image.setDevicePixelRatio(ratio)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    _draw(painter, svg, 0, 0, side)
    painter.end()
    return image


def legend():
    """Every drawing with its words, in the order a row has them:
    ``[(picture, words), ...]``, with ``None`` between the badges and the
    strip."""
    from ..core.i18n import tr

    def badge(verb, hollow=False):
        return picture(_badge(verb, hollow), BADGE)

    return [
        (badge(ADD), tr("adds inside the node it names")),
        (badge(ADD_AFTER), tr("adds after the node it names")),
        (badge(ADD_BEFORE), tr("adds before the node it names")),
        (badge(REMOVE), tr("removes")),
        (badge(REPLACE), tr("replaces")),
        (badge(MERGE), tr("merges")),
        (badge(GROUP), tr("a group, with ModOps in it")),
        (badge(REPLACE, True), tr("the mod ships it switched off (Skip)")),
        None,
        (picture(_pattern(ON)), tr("its target is a pattern, and finds something")),
        (picture(_pattern(OFF)), tr("a pattern that finds nothing as things stand")),
        (picture(_computed(ON)), tr("its value is copied or worked out as the game loads")),
        (picture(_conditional(ON)), tr("it has a condition, which holds")),
        (picture(_conditional(OFF)), tr("a condition that does not hold as things stand")),
        (picture(_conditional(UNKNOWN)), tr("a condition the Studio cannot say of")),
        (picture(_options(ON)), tr("it answers to the mod's own options")),
    ]


def icon(verb: str, hollow: bool = False, marks: Tuple[int, int, int, int] = (), ) -> QIcon:
    """The drawing for one op: its badge, and its strip where ``marks`` gives
    the four slots' states (target, payload, condition, options).

    With no ``marks`` it is the badge alone, which is all there is to draw of
    an op with nothing worked out: one narrow change.
    """
    app = QApplication.instance()
    ratio = app.devicePixelRatio() if app is not None else 1.0
    key = (theme.scheme().key, verb, hollow, tuple(marks), ratio)
    found = _made.get(key)
    if found is not None:
        return found
    wide = width(bool(marks))
    made = QIcon()
    # A selected row is filled with another colour, which a mark drawn for
    # the list's own can vanish against: Obsidian's blue lens on its yellow.
    # So the drawing has a second version for it, which the view draws there.
    for mode, selected in ((QIcon.Mode.Normal, False), (QIcon.Mode.Selected, True)):
        image = QPixmap(round(wide * ratio), round(HEIGHT * ratio))
        image.setDevicePixelRatio(ratio)
        image.fill(Qt.GlobalColor.transparent)
        painter = QPainter(image)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        if verb in _VERBS:
            _draw(painter, _badge(verb, hollow, selected), 0, 0, BADGE)
        for at, state in enumerate(marks):
            if state:
                _draw(
                    painter, _SLOTS[at](state, selected),
                    BADGE + GAP + at * SLOT, (HEIGHT - MARK) / 2, MARK,
                )
        painter.end()
        made.addPixmap(image, mode)
    _made[key] = made
    return made
