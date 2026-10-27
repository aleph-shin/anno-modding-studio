"""Colour schemes, drawing styles, and the style sheet built from them.

A theme has two independent parts (docs/standards/design-standards.md):

- a **colour scheme**: a value for each semantic token in ``TOKENS``
- a **drawing style**: Outlines (boxed or flat) and Density (comfortable or
  compact), each a block of rules plus metrics that never names a colour

Code asks for what a colour means. ``theme.TEXT_DIM`` is the current scheme's
``text-dim`` and changes when the scheme does. ``theme.tint(label, "warn")``
colours a widget through the style sheet. No other module holds a colour
value or a style sheet of its own, and ``tools/design_check.py`` counts the
ones that do.

The values are those approved in the design review. Their source, and the
renders they are checked against, are in ``docs/design-review/``.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional

from ..core.i18n import tr_noop

# Every token a scheme defines. ``changed`` and ``removed`` default to ``ok``
# and ``error``, and ``syn-attr`` to ``accent-text``, where a scheme leaves
# them out.
TOKENS = (
    # surfaces
    "bg", "surface", "raised", "alt-row",
    # lines
    "border", "field-border", "focus",
    # text
    "text", "text-dim", "text-disabled", "text-on-accent",
    # accent: something you can act on, or what is current
    "accent", "accent-hover", "accent-text", "selection",
    # status, each shown with a glyph or a word as well
    "ok", "warn", "error", "rule",
    # changes
    "changed", "removed", "added",
    # XML preview; ``syn-option`` is a mod's setting named in a condition or
    # a value, a hue none of the others has
    "syn-comment", "syn-tag", "syn-attr", "syn-value", "syn-option",
    # scrollbar handles
    "scrollbar", "scrollbar-hover",
)

_DEFAULTS = {"changed": "ok", "removed": "error", "syn-attr": "accent-text"}


@dataclass(frozen=True)
class Scheme:
    """One colour scheme: a value for every token, and three facts about it."""

    key: str
    label: str          # English, marked for translation where it is defined
    tokens: Dict[str, str]
    dark: bool
    contrast: bool = False   # every boundary drawn, text at 7:1
    logo: str = "Blue_White"  # the mod.io press-kit logo for this background

    def __getitem__(self, token: str) -> str:
        return self.tokens[token]


def _scheme(key, label, dark, logo, tokens, contrast=False) -> Scheme:
    full = dict(tokens)
    for token, source in _DEFAULTS.items():
        full.setdefault(token, full[source])
    missing = [token for token in TOKENS if token not in full]
    if missing:
        raise ValueError("%s leaves out %s" % (key, ", ".join(missing)))
    return Scheme(key, label, full, dark, contrast, logo)


# A scheme is named after a product from an Anno game. That is a guideline,
# and a close name is fine. A high-contrast scheme says so in its name.
SCHEMES: Dict[str, Scheme] = {
    scheme.key: scheme
    for scheme in (
        # Blue-grey dark with Anno gold. The default.
        _scheme("granite", tr_noop("Granite", context="colour scheme"), True, "Blue_White", {
            "bg": "#181b20", "surface": "#1f232a", "raised": "#262b33",
            "alt-row": "#22262e", "border": "#363d49", "field-border": "#6e7582",
            "focus": "#e0a44c",
            "text": "#e3e7ee", "text-dim": "#9ba4b2", "text-disabled": "#5d6675",
            "text-on-accent": "#201a10",
            "accent": "#e0a44c", "accent-hover": "#efb75f", "accent-text": "#e8b25f",
            "selection": "#6e5226",
            "ok": "#7ec699", "warn": "#f0894a", "error": "#f07272", "rule": "#7aa7e6",
            "added": "#62c7d9",
            "syn-comment": "#818c9c", "syn-tag": "#7aa6d6", "syn-value": "#a8d99a",
            "syn-option": "#d6a2e8",
            "scrollbar": "#616874", "scrollbar-hover": "#727a87",
        }),
        # Brick red carried by the surfaces, strongest in the raised bands.
        # Error and removed are lighter and pinker than any surface.
        _scheme("brick", tr_noop("Brick", context="colour scheme"), True, "Blue_White", {
            "bg": "#1f1311", "surface": "#291715", "raised": "#4a221c",
            "alt-row": "#251513", "border": "#5f322b", "field-border": "#9a7068",
            "focus": "#e2a64e",
            "text": "#f0e7e3", "text-dim": "#c2aaa3", "text-disabled": "#7d5f58",
            "text-on-accent": "#201a10",
            "accent": "#e2a64e", "accent-hover": "#f0b862", "accent-text": "#eab562",
            "selection": "#6e3f1e",
            "ok": "#8fd3a4", "warn": "#f39257", "error": "#ff8fa3", "rule": "#93b6e8",
            "added": "#7fd3e0",
            "syn-comment": "#a8948e", "syn-tag": "#93b6e8", "syn-value": "#a9d99c",
            "syn-option": "#e2b0f2",
            "scrollbar": "#90615a", "scrollbar-hover": "#a3716a",
        }),
        # Cool white. Gold fills the primary button. Bronze wherever the
        # accent is read as text, and for focus (gold on white is 2.3:1).
        _scheme("marble", tr_noop("Marble", context="colour scheme"), False, "Blue_Black", {
            "bg": "#f4f5f7", "surface": "#ffffff", "raised": "#e9ebef",
            "alt-row": "#f7f8fa", "border": "#d3d7de", "field-border": "#80868f",
            "focus": "#8a5800",
            "text": "#1c2129", "text-dim": "#59616e", "text-disabled": "#9aa1ad",
            "text-on-accent": "#1f1606",
            "accent": "#d8962f", "accent-hover": "#e4a541", "accent-text": "#8a5800",
            "selection": "#f6e2bd",
            "ok": "#1d7a45", "warn": "#b4530f", "error": "#c0312f", "rule": "#2a5fae",
            "added": "#0b6f80",
            "syn-comment": "#6b7280", "syn-tag": "#2257a3", "syn-value": "#2f7a32",
            "syn-option": "#8b3aa8",
            "scrollbar": "#898d93", "scrollbar-hover": "#767b83",
        }),
        # Pale pine with a pine-green accent, like painted trim, so nothing
        # you can act on shares the wood's hue. Changed is a teal-green to
        # stay apart from it, which leaves plain green for added.
        _scheme("timber", tr_noop("Timber", context="colour scheme"), False, "Blue_Black", {
            "bg": "#efe3cf", "surface": "#fbf6ee", "raised": "#e3d2b6",
            "alt-row": "#f6efe3", "border": "#cfb894", "field-border": "#8b7251",
            "focus": "#2f6146",
            "text": "#2b2117", "text-dim": "#5c4c3a", "text-disabled": "#9a8a74",
            "text-on-accent": "#ffffff",
            "accent": "#2f6146", "accent-hover": "#3a7356", "accent-text": "#285a3f",
            "selection": "#cfe2c9",
            "ok": "#17666b", "warn": "#a8460a", "error": "#a3142a", "rule": "#23559c",
            "added": "#4b6510",
            "syn-comment": "#6f604c", "syn-tag": "#23559c", "syn-value": "#2c6a2c",
            "syn-option": "#7d2c8c",
            "scrollbar": "#917e62", "scrollbar-hover": "#7d6a50",
        }),
        # Black, white and yellow, every boundary drawn.
        _scheme("obsidian", tr_noop("Obsidian (high contrast)", context="colour scheme"),
                True, "Mono_White", {
            "bg": "#000000", "surface": "#000000", "raised": "#0d0d0d",
            "alt-row": "#000000", "border": "#ffffff", "field-border": "#ffffff",
            "focus": "#ffd84a",
            "text": "#ffffff", "text-dim": "#e0e0e0", "text-disabled": "#a0a0a0",
            "text-on-accent": "#000000",
            "accent": "#ffd84a", "accent-hover": "#ffe57a", "accent-text": "#ffd84a",
            "selection": "#ffd84a",
            "ok": "#5cff9a", "warn": "#ffa040", "error": "#ff7a7a", "rule": "#8ec5ff",
            "added": "#5ce1ff",
            "syn-comment": "#d0d0d0", "syn-tag": "#8ec5ff", "syn-value": "#5cff9a",
            "syn-option": "#ff9cff",
            "scrollbar": "#ffffff", "scrollbar-hover": "#ffd84a",
        }, contrast=True),
        # White, black and a deep blue accent with white text on it. Rule is
        # purple so it never matches the accent.
        _scheme("quartz", tr_noop("Quartz (high contrast)", context="colour scheme"),
                False, "Mono_Black", {
            "bg": "#ffffff", "surface": "#ffffff", "raised": "#f2f2f2",
            "alt-row": "#ffffff", "border": "#000000", "field-border": "#000000",
            "focus": "#00308a",
            "text": "#000000", "text-dim": "#262626", "text-disabled": "#6b6b6b",
            "text-on-accent": "#ffffff",
            "accent": "#00308a", "accent-hover": "#0040b0", "accent-text": "#00308a",
            "selection": "#00308a",
            "ok": "#00602a", "warn": "#7a3300", "error": "#a0001c", "rule": "#5a1d8c",
            "added": "#00505c",
            "syn-comment": "#3d3d3d", "syn-tag": "#00308a", "syn-value": "#00602a",
            "syn-option": "#6f1d80",
            "scrollbar": "#000000", "scrollbar-hover": "#00308a",
        }, contrast=True),
    )
}
DEFAULT_SCHEME = "granite"


def luminance(colour: str) -> float:
    """Relative luminance of ``#rrggbb``, as WCAG defines it."""
    channels = [int(colour[i:i + 2], 16) / 255 for i in (1, 3, 5)]
    linear = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
              for c in channels]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def contrast_ratio(a: str, b: str) -> float:
    hi, lo = sorted((luminance(a), luminance(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def system_scheme(window: str, text: str, gray: str, highlight: str,
                  highlight_text: str, button: str, hot: str) -> Scheme:
    """The scheme for a Windows contrast theme, from that theme's own colours.

    People who turn a contrast theme on may need those exact colours, so
    nothing here is the Studio's. Status has no colour of its own in a
    Windows palette: it is drawn in the text colour and told apart by its
    glyph or word.
    """
    dark = luminance(window) < luminance(text)
    return _scheme("windows-contrast", tr_noop("Windows contrast theme", context="colour scheme"),
                   dark, "Mono_White" if dark else "Mono_Black", {
        "bg": window, "surface": window, "raised": button, "alt-row": window,
        "border": text, "field-border": text, "focus": highlight,
        "text": text, "text-dim": text, "text-disabled": gray,
        "text-on-accent": highlight_text,
        "accent": highlight, "accent-hover": highlight, "accent-text": hot,
        "selection": highlight,
        "ok": text, "warn": text, "error": text, "rule": hot, "added": text,
        "syn-comment": text, "syn-tag": hot, "syn-attr": text, "syn-value": text,
        "syn-option": text,
        "scrollbar": text, "scrollbar-hover": highlight,
    }, contrast=True)


# -- drawing style ------------------------------------------------------------
# Each option is a block of rules plus metrics, and names tokens only, so any
# style works with any scheme. Flat draws its rules in ``field-border`` to
# stay at the 3:1 floor. A Flat group box is as tall as a Boxed one (10 + 9
# and one line, for 10 + 8 and two), so a window that fits the screen in one
# fits in the other.
OUTLINES = {
    "boxed": "",
    "flat": """
QGroupBox {{ border: none; border-top: 1px solid {field-border}; border-radius: 0; margin-top: 10px; padding-top: 9px; }}
QGroupBox::title {{ left: 0; padding: 0 6px 0 0; color: {text}; }}
QTreeWidget, QTableWidget, QListWidget {{ border: none; }}
QHeaderView::section {{ background: {surface}; border: none; border-bottom: 1px solid {field-border}; }}
""",
}
OUTLINE_LABELS = (
    ("boxed", tr_noop("Boxed", context="outlines")),
    ("flat", tr_noop("Flat", context="outlines")),
)
DEFAULT_OUTLINES = "boxed"

# ``radius`` rounds fields, buttons and lists, ``card-radius`` and
# ``step-radius`` the cards, ``pad-v`` and ``pad-h`` pad a button, ``row-pad``
# a list row, and ``tab-pad`` a main tab. ``gap`` is the standard spacing
# between controls, for layouts built in code.
DENSITIES = {
    "comfortable": {
        "radius": 4, "card-radius": 8, "step-radius": 10,
        "pad-v": 6, "pad-h": 14, "row-pad": 3, "gap": 8,
        "tab-pad": "10px 22px 8px 22px",
    },
    "compact": {
        "radius": 2, "card-radius": 6, "step-radius": 6,
        "pad-v": 4, "pad-h": 12, "row-pad": 2, "gap": 6,
        "tab-pad": "8px 18px 7px 18px",
    },
}
DENSITY_LABELS = (
    ("comfortable", tr_noop("Comfortable", context="density")),
    ("compact", tr_noop("Compact", context="density")),
)
DEFAULT_DENSITY = "comfortable"

# Interface size scales everything through Qt's own scale factor, so these
# are logical pixels and never change while the Studio runs.
FONT_PX = 14
SMALL_PX = 13

# Paths, GUIDs and XML. Named in the style sheet, because a style sheet that
# sets a font family overrides setFont outright.
MONO_FAMILIES = '"Consolas", "Cascadia Mono", "Courier New", monospace'

# The tones ``tint`` can give a widget's text.
TONES = ("text", "text-dim", "accent-text", "ok", "warn", "error", "rule")
# The tones that are a status. No status is shown by colour alone, so each
# of these comes with a glyph: beside a tinted label, and as the icon of a
# marked list item.
STATUS = ("ok", "warn", "error")
# Labels whose object name already sets a colour. A tone has to be written
# against each to outrank it.
_COLOURED_LABELS = ("SubHeading", "StepHint", "StepStatus", "Warn", "Error", "Ok")

# -- the theme in effect ------------------------------------------------------
_scheme_now: Scheme = SCHEMES[DEFAULT_SCHEME]
_outlines_now = DEFAULT_OUTLINES
_density_now = DEFAULT_DENSITY
_notifier = None


def scheme() -> Scheme:
    """The colour scheme in effect."""
    return _scheme_now


def outlines() -> str:
    return _outlines_now


def density() -> str:
    return _density_now


def colour(token: str) -> str:
    """The current scheme's value for ``token``, as ``#rrggbb``."""
    return _scheme_now.tokens[token]


def wash(token: str = "accent", share: float = 0.16,
         of: Optional[Scheme] = None) -> Optional[str]:
    """``token`` laid thinly over the surface, as ``#rrggbb``: a row's
    background that makes it stand out without looking selected.

    Thin enough that ``accent-text`` and ``text-dim`` on it hold 4.5:1 in
    every scheme here.  None under a Windows contrast theme, whose colours
    are the system's own and nothing in between.
    """
    of = of or _scheme_now
    if of.key == "windows-contrast":
        return None
    over, under = of[token], of["surface"]
    return "#" + "".join(
        "%02x" % round(int(over[i:i + 2], 16) * share + int(under[i:i + 2], 16) * (1 - share))
        for i in (1, 3, 5)
    )


def metric(name: str):
    """A metric of the density in effect. See ``DENSITIES``."""
    if name == "radius" and _scheme_now.contrast:
        return 0
    return DENSITIES[_density_now][name]


def __getattr__(name: str):
    """``theme.TEXT_DIM`` is the current scheme's ``text-dim``, and so on for
    every token. ``theme.STYLESHEET`` is the style sheet in effect."""
    if name == "STYLESHEET":
        return stylesheet()
    token = name.lower().replace("_", "-")
    if name.isupper() and token in _scheme_now.tokens:
        return _scheme_now.tokens[token]
    raise AttributeError("module %r has no attribute %r" % (__name__, name))


def notifier():
    """An object whose ``changed`` signal fires after the theme changes, for
    whatever draws with colours it read earlier."""
    global _notifier
    if _notifier is None:
        from PySide6.QtCore import QObject, Signal

        class Notifier(QObject):
            changed = Signal()

        _notifier = Notifier()
    return _notifier


# What a ModOp's type is drawn in, in the Mod changes panel (ui.op_marks): the
# type's own colour and not the scheme's, so an addition is green and a
# removal red in every theme.  By (dark, contrast): add, replace, remove,
# merge.  Merge is a purple: a grey or a slate one is the group's colour,
# which is the scheme's dim text.
OP_HUES = {
    (True, False): ("#7ec699", "#6aa9ea", "#dc5252", "#b39af0"),
    (False, False): ("#1d7a45", "#2a5fae", "#a82422", "#7048b8"),
    (True, True): ("#5cff9a", "#8ec5ff", "#ff5c5c", "#cfb3ff"),
    (False, True): ("#00602a", "#00308a", "#8a0018", "#7b1fa2"),
}


# -- images -------------------------------------------------------------------
# A style sheet can fill a box and cannot draw inside one, so every indicator
# state is an image. Each is an SVG template filled from the scheme's tokens
# and written to the cache folder when the scheme is applied.
def _box(r, fill, stroke):
    return ('<rect x="1" y="1" width="14" height="14" rx="%s" fill="%s" '
            'stroke="%s" stroke-width="1.5"/>' % (r, fill, stroke))


def _tick(c):
    return ('<path d="M4 8.3l2.6 2.6L12 5.4" fill="none" stroke="%s" '
            'stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>' % c)


def _dash(c):
    return '<path d="M4.6 8h6.8" stroke="%s" stroke-width="2" stroke-linecap="round"/>' % c


def _ring(fill, stroke):
    return ('<circle cx="8" cy="8" r="6.6" fill="%s" stroke="%s" '
            'stroke-width="1.5"/>' % (fill, stroke))


def _dot(c):
    return '<circle cx="8" cy="8" r="3" fill="%s"/>' % c


def _chevron(d, c):
    return ('<path d="%s" fill="none" stroke="%s" stroke-width="1.8" '
            'stroke-linecap="round" stroke-linejoin="round"/>' % (d, c))


def _help(c):
    return ('<circle cx="8" cy="8" r="6.6" fill="none" stroke="%s" stroke-width="1.4"/>'
            '<path d="M5.9 6.4a2.1 2.1 0 1 1 3.3 1.7c-.75.5-1.2.95-1.2 1.75" fill="none" '
            'stroke="%s" stroke-width="1.5" stroke-linecap="round"/>'
            '<circle cx="8" cy="11.9" r=".95" fill="%s"/>' % (c, c, c))


def _status(tone, c):
    """The glyph that says warning, error or ok without the colour."""
    if tone == "warn":
        return ('<path d="M8 2 14.4 13.4H1.6Z" fill="none" stroke="%s" stroke-width="1.5" '
                'stroke-linejoin="round"/><path d="M8 6.3v3.4" stroke="%s" stroke-width="1.6" '
                'stroke-linecap="round"/><circle cx="8" cy="11.5" r=".9" fill="%s"/>' % (c, c, c))
    ring = '<circle cx="8" cy="8" r="6.6" fill="none" stroke="%s" stroke-width="1.5"/>' % c
    if tone == "error":
        return ring + ('<path d="M5.7 5.7l4.6 4.6M10.3 5.7l-4.6 4.6" stroke="%s" '
                       'stroke-width="1.6" stroke-linecap="round"/>' % c)
    return ring + ('<path d="M4.9 8.2l2.1 2.1 4.1-4.2" fill="none" stroke="%s" stroke-width="1.6" '
                   'stroke-linecap="round" stroke-linejoin="round"/>' % c)


def _lock(c):
    """Closed: what is under it is there to be read."""
    return ('<rect x="3.2" y="7.2" width="9.6" height="6.8" rx="1.4" fill="none" stroke="%s" '
            'stroke-width="1.5"/><path d="M5.4 7.2V5.1a2.6 2.6 0 0 1 5.2 0v2.1" fill="none" '
            'stroke="%s" stroke-width="1.5" stroke-linecap="round"/>' % (c, c))


def _play(c):
    """What the game loads, as the Play button says it."""
    return '<path d="M5 3.2v9.6l7.6-4.8Z" fill="%s" stroke="%s" stroke-width="1" stroke-linejoin="round"/>' % (c, c)


def _pencil(c):
    """Where what you write goes."""
    return ('<path d="M3 13l.7-3 6.9-6.9a1.3 1.3 0 0 1 1.9 0l.4.4a1.3 1.3 0 0 1 0 1.9L6 12.3Z" '
            'fill="none" stroke="%s" stroke-width="1.5" stroke-linejoin="round"/>'
            '<path d="M9.4 4.3l2.3 2.3" stroke="%s" stroke-width="1.5"/>' % (c, c))


_UP, _DOWN, _RIGHT = "M4 10l4-4 4 4", "M4 6l4 4 4-4", "M6 4l4 4-4 4"
_LEFT = "M10 4l-4 4 4 4"


def indicator_svgs(of: Optional[Scheme] = None) -> Dict[str, str]:
    """Every indicator image for a scheme, as SVG text by name."""
    t = (of or _scheme_now).tokens
    r = 0 if (of or _scheme_now).contrast else 3
    fb, fa, ac = t["field-border"], t["surface"], t["accent"]
    dim, dis, focus = t["text-dim"], t["text-disabled"], t["focus"]
    bg, border = t["bg"], t["border"]
    shapes = {
        "check_off": _box(r, fa, fb),
        "check_off_hover": _box(r, fa, ac),
        "check_off_disabled": _box(r, bg, border),
        "check": _box(r, ac, focus) + _tick(t["text-on-accent"]),
        "check_disabled": _box(r, bg, border) + _tick(dis),
        "check_partial": _box(r, ac, focus) + _dash(t["text-on-accent"]),
        "radio_off": _ring(fa, fb),
        "radio_off_hover": _ring(fa, ac),
        "radio_off_disabled": _ring(bg, border),
        "radio_on": _ring(fa, focus) + _dot(focus),
        "radio_on_disabled": _ring(bg, border) + _dot(dis),
        "spin_up": _chevron(_UP, dim),
        "spin_up_hover": _chevron(_UP, ac),
        "spin_up_disabled": _chevron(_UP, dis),
        "spin_down": _chevron(_DOWN, dim),
        "spin_down_hover": _chevron(_DOWN, ac),
        "spin_down_disabled": _chevron(_DOWN, dis),
        "branch_closed": _chevron(_RIGHT, dim),
        "strip_left": _chevron(_LEFT, t["text"]),
        "strip_left_disabled": _chevron(_LEFT, dis),
        "strip_right": _chevron(_RIGHT, t["text"]),
        "strip_right_disabled": _chevron(_RIGHT, dis),
        "branch_open": _chevron(_DOWN, dim),
        "help": _help(dim),
        "help_hover": _help(focus),
        "status_ok": _status("ok", t["ok"]),
        "status_warn": _status("warn", t["warn"]),
        "status_error": _status("error", t["error"]),
        "tab_locked": _lock(dim),
        "tab_edit": _pencil(t["accent-text"]),
        "live": _play(t["accent-text"]),
    }
    svgs = {
        name: '<svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" '
              'viewBox="0 0 16 16">%s</svg>' % body
        for name, body in shapes.items()
    }
    # A status glyph as a label draws it: at the top of the label, where
    # the line starts, and the line's capitals are lower than that. Drawn
    # from the top, the glyph's middle stood 2px above theirs, in the body
    # font and in the hint's alike.
    for tone in STATUS:
        svgs["label_" + tone] = (
            '<svg xmlns="http://www.w3.org/2000/svg" width="16" height="%d" '
            'viewBox="0 -%d 16 %d">%s</svg>'
            % (16 + _GLYPH_DROP, _GLYPH_DROP, 16 + _GLYPH_DROP, shapes["status_" + tone])
        )
    return svgs


# How far below the top of its line a label's status glyph is drawn.
_GLYPH_DROP = 2


def indicators(of: Optional[Scheme] = None) -> Dict[str, str]:
    """The scheme's indicator images, written out. Their paths by name.

    The folder is named for the values in it, so a scheme whose values change
    (a Windows contrast theme) never reads a stale image through Qt's cache.
    Forward slashes: Qt reads a url() as a path, and a backslash in one is an
    escape character.
    """
    from ..core import places

    of = of or _scheme_now
    svgs = indicator_svgs(of)
    digest = hashlib.sha1("".join(svgs.values()).encode("utf-8")).hexdigest()[:10]
    folder = places.cache_dir() / "theme" / ("%s-%s" % (of.key, digest))
    folder.mkdir(parents=True, exist_ok=True)
    paths = {}
    for name, svg in svgs.items():
        path = folder / (name + ".svg")
        if not path.is_file():
            path.write_text(svg, encoding="utf-8")
        paths[name] = path.as_posix()
    return paths


def logo_path(of: Optional[Scheme] = None) -> Path:
    """mod.io's logo for the scheme's background, from its press kit."""
    resources = Path(__file__).resolve().parent.parent / "resources" / "modio"
    return resources / ("%s.svg" % (of or _scheme_now).logo)


# -- the style sheet ----------------------------------------------------------
def stylesheet(of: Optional[Scheme] = None, outline: str = "", dense: str = "") -> str:
    """The whole style sheet for a scheme and style (default: those in effect)."""
    of = of or _scheme_now
    t = of.tokens
    m = DENSITIES[dense or _density_now]
    px, small = FONT_PX, SMALL_PX
    radius = 0 if of.contrast else m["radius"]
    pad_v, pad_h = m["pad-v"], m["pad-h"]
    step = m["step-radius"]
    img = indicators(of)

    css = f"""
QWidget {{
    background: {t['bg']};
    color: {t['text']};
    font-family: "Segoe UI", sans-serif;
    font-size: {px}px;
    selection-background-color: {t['selection']};
}}

QLabel#Heading {{ font-size: {px + 5}px; font-weight: 600; }}
QLabel#SubHeading {{ font-size: {small}px; color: {t['text-dim']}; }}
QLabel#StepHint {{ color: {t['text-dim']}; font-size: {small}px; }}
QLabel#AssetTitle {{ font-size: {px + 3}px; font-weight: 600; color: {t['text']}; }}
QLabel#Warn {{ color: {t['warn']}; }}
/* Stands in for the title bar a QDockWidget used to draw. */
QLabel#PanelTitle {{
    background: {t['raised']};
    color: {t['text']};
    padding: 6px 10px;
    border-bottom: 1px solid {t['border']};
    font-weight: 600;
}}
QLabel#Error {{ color: {t['error']}; }}
QLabel#Ok {{ color: {t['ok']}; }}

QLineEdit, QPlainTextEdit, QComboBox, QAbstractSpinBox {{
    background: {t['surface']};
    border: 1px solid {t['field-border']};
    border-radius: {radius}px;
    padding: {pad_v - 1}px 7px;
    selection-background-color: {t['selection']};
}}
QLineEdit:focus, QPlainTextEdit:focus, QComboBox:focus, QAbstractSpinBox:focus {{
    border: 1px solid {t['focus']};
}}
QLineEdit#Search {{ font-size: {px + 2}px; padding: 8px 10px; }}
QLineEdit[readOnly="true"] {{ color: {t['text-dim']}; }}
/* A pane of the asset editor's XML view that is only read sits on the
   panel, with its text at full strength: a field's surface is for what can
   be typed in. */
QPlainTextEdit#XmlPane[readOnly="true"] {{ background: {t['bg']}; border-color: {t['border']}; }}

/* Styling a spin box at all takes its buttons off the native style, and a
   style sheet cannot draw an arrow into a box, so the arrows are images.
   QAbstractSpinBox, for the box of whole numbers and the one of decimals. */
QAbstractSpinBox::up-button, QAbstractSpinBox::down-button {{
    subcontrol-origin: border;
    width: 16px;
    border-left: 1px solid {t['border']};
    background: {t['raised']};
}}
QAbstractSpinBox::up-button {{ subcontrol-position: top right; border-top-right-radius: 4px; }}
QAbstractSpinBox::down-button {{ subcontrol-position: bottom right; border-bottom-right-radius: 4px; }}
QAbstractSpinBox::up-button:hover, QAbstractSpinBox::down-button:hover {{ background: {t['bg']}; }}
QAbstractSpinBox::up-button:disabled, QAbstractSpinBox::down-button:disabled {{ background: {t['surface']}; }}
QAbstractSpinBox::up-arrow {{ image: url({img['spin_up']}); width: {px - 4}px; height: {px - 4}px; }}
QAbstractSpinBox::up-arrow:hover {{ image: url({img['spin_up_hover']}); }}
QAbstractSpinBox::up-arrow:disabled {{ image: url({img['spin_up_disabled']}); }}
QAbstractSpinBox::down-arrow {{ image: url({img['spin_down']}); width: {px - 4}px; height: {px - 4}px; }}
QAbstractSpinBox::down-arrow:hover {{ image: url({img['spin_down_hover']}); }}
QAbstractSpinBox::down-arrow:disabled {{ image: url({img['spin_down_disabled']}); }}

/* Fusion draws the drop-down as a raised button once the box is styled.
   This is the same chevron the spin box uses, on the field's own surface. */
QComboBox::drop-down {{
    subcontrol-origin: padding;
    subcontrol-position: center right;
    width: {px + 8}px;
    border: none;
}}
QComboBox::down-arrow {{ image: url({img['spin_down']}); width: {px - 4}px; height: {px - 4}px; }}
QComboBox::down-arrow:hover {{ image: url({img['spin_down_hover']}); }}
QComboBox::down-arrow:disabled {{ image: url({img['spin_down_disabled']}); }}

QComboBox QAbstractItemView {{
    background: {t['surface']};
    border: 1px solid {t['border']};
    selection-background-color: {t['selection']};
}}
/* The profile picker's list drops down below it, as a menu does: Fusion
   opens it over the box with the chosen row under the pointer, which moved
   the acts at its foot a row with each profile chosen. */
QComboBox#ProfileBox {{ combobox-popup: 0; }}

QPushButton {{
    background: {t['raised']};
    border: 1px solid {t['border']};
    border-radius: {radius}px;
    padding: {pad_v}px {pad_h}px;
}}
QPushButton:hover {{ border-color: {t['accent']}; }}
/* In the disabled colour, as a tab that is off is.  In text-dim, the colour
   of a hint, a button that was off read as one that was on: "Extract and
   load" sat greyed beside "Cancel" and was clicked to no effect. */
QPushButton:disabled {{ color: {t['text-disabled']}; border-color: {t['border']}; background: {t['surface']}; }}
QPushButton#Primary {{
    background: {t['accent']};
    color: {t['text-on-accent']};
    font-weight: 600;
    border: 1px solid {t['accent']};
}}
QPushButton#Primary:hover {{ background: {t['accent-hover']}; }}
QPushButton#Primary:disabled {{ background: {t['surface']}; color: {t['text-disabled']}; border-color: {t['border']}; }}
/* The history buttons: a drawn arrow in a narrow, fixed-width box, which the
   usual side padding would leave almost no room for. */
QPushButton#NavButton {{ padding: 4px 6px; }}
/* The button that moves a panel to a window of its own: one arrow, drawn
   as a push button is and as tall as the ones beside it. A tool button,
   which has no look of its own here: see panel_windows.MoveButton.  And
   the arrow beside Play that opens its menu, drawn the same, with no
   second arrow of Qt's own under the one it shows. */
QToolButton#PlayWhere::menu-indicator {{ image: none; width: 0px; }}
QToolButton#PlayWhere, QToolButton#PanelMove {{
    background: {t['raised']};
    border: 1px solid {t['border']};
    border-radius: {radius}px;
    padding: {pad_v}px 7px;
}}
QToolButton#PlayWhere:hover, QToolButton#PanelMove:hover {{ border-color: {t['accent']}; }}
/* The border grows by a pixel when focused, so the padding gives one back. */
QPushButton:focus {{ border: 2px solid {t['focus']}; padding: {pad_v - 1}px {pad_h - 1}px; }}
QToolButton#PlayWhere:focus, QToolButton#PanelMove:focus {{
    border: 2px solid {t['focus']}; padding: {pad_v - 1}px 6px;
}}
QPushButton#Primary:focus {{ border: 2px solid {t['text']}; }}
/* Two buttons joined into one control, the pressed-in half being what is on
   show. Filled, edged in the accent and semi-bold: three things say which. */
QPushButton#Segment {{ color: {t['text-dim']}; background: {t['surface']}; border-radius: 0; }}
QPushButton#Segment[side="first"] {{
    border-top-left-radius: {radius}px; border-bottom-left-radius: {radius}px;
}}
QPushButton#Segment[side="last"] {{
    border-top-right-radius: {radius}px; border-bottom-right-radius: {radius}px;
    margin-left: -1px;
}}
QPushButton#Segment:hover {{ color: {t['text']}; }}
QPushButton#Segment:checked {{
    color: {t['text']};
    background: {t['selection']};
    border-color: {t['accent']};
    font-weight: 600;
}}
/* After the pressed-in half's own edge, which would otherwise be its edge
   with the focus too. */
QPushButton#Segment:focus {{ border: 2px solid {t['focus']}; }}

QTreeWidget, QTableWidget, QListWidget {{
    background: {t['surface']};
    border: 1px solid {t['border']};
    border-radius: {radius}px;
    alternate-background-color: {t['alt-row']};
    gridline-color: {t['border']};
    selection-background-color: {t['selection']};
    selection-color: {t['text']};
}}
QTreeWidget::item, QTableWidget::item {{ padding: {m['row-pad']}px 4px; }}
QTreeWidget:focus, QTableWidget:focus, QListWidget:focus {{ border: 1px solid {t['focus']}; }}
QTreeView::branch:has-children:closed {{ image: url({img['branch_closed']}); }}
QTreeView::branch:has-children:open {{ image: url({img['branch_open']}); }}
QHeaderView::section {{
    background: {t['raised']};
    color: {t['text-dim']};
    border: none;
    border-right: 1px solid {t['border']};
    border-bottom: 1px solid {t['border']};
    padding: 5px 6px;
    font-weight: 600;
}}

QFrame#WelcomeCard {{
    background: {t['surface']};
    border: 1px solid {t['border']};
    border-radius: 8px;
}}
/* Children would otherwise paint the window background over the card. */
QFrame#WelcomeCard QLabel {{ background: transparent; }}

/* One of the ways of working, on the first tab. The one in use is picked
   out. The others are not dimmed, because they stay available. */
QFrame#WayCard {{
    background: {t['surface']};
    border: 1px solid {t['border']};
    border-radius: {m['card-radius']}px;
}}
QFrame#WayCard[current="true"] {{
    background: {t['raised']};
    border: 1px solid {t['accent']};
}}
QFrame#WayCard QLabel {{ background: transparent; }}

/* Editors opened inside a cell must fit the row, not the roomy form styling. */
QTreeWidget QLineEdit, QTableWidget QLineEdit {{
    padding: 0px 3px;
    margin: 0px;
    border-radius: 2px;
    border: 1px solid {t['accent']};
    background: {t['bg']};
}}

/* The Start page's numbered steps. The card is the page colour with a
   rounded border. The header is a raised band whose status is the step's
   answer. */
QFrame#StepCard {{
    background: {t['bg']};
    border: 1px solid {t['border']};
    border-radius: {step}px;
}}
QFrame#StepHead {{
    background: {t['raised']};
    border: none;
    border-bottom: 1px solid {t['border']};
    border-top-left-radius: {step - 1}px;
    border-top-right-radius: {step - 1}px;
}}
QFrame#StepHead[alone="true"] {{
    border-bottom: none;
    border-bottom-left-radius: 9px;
    border-bottom-right-radius: 9px;
}}
QFrame#StepHead QLabel {{ background: transparent; }}
QWidget#StepBody {{ background: transparent; }}
QFrame#StepHead QLabel#StepBadge {{
    background: {t['accent']};
    color: {t['text-on-accent']};
    font-weight: 700;
    border-radius: {(px + 10) // 2}px;
    min-width: {px + 10}px; max-width: {px + 10}px;
    min-height: {px + 10}px; max-height: {px + 10}px;
}}
QLabel#StepTitle {{ font-size: {px + 4}px; font-weight: 600; }}
QLabel#StepStatus {{ color: {t['text-dim']}; }}
QLabel#StepStatus[state="ok"] {{ color: {t['ok']}; }}
QLabel#StepStatus[state="todo"] {{ color: {t['warn']}; }}

/* Projects you had open before: a list of files to pick one from. */
QListWidget#Recents {{
    background: {t['surface']};
    border: 1px solid {t['border']};
    border-radius: 6px;
    padding: 2px;
}}
QListWidget#Recents::item {{
    padding: 4px 8px;
    border-radius: 4px;
    color: {t['text']};
}}
QListWidget#Recents:focus {{ border: 1px solid {t['focus']}; }}
QListWidget#Recents::item:hover {{ background: {t['raised']}; color: {t['accent-text']}; }}
QListWidget#Recents::item:selected {{ background: {t['selection']}; color: {t['text']}; }}
/* The rule under what the game loads: a row nothing happens to, and a
   line one pixel tall that the row's padding centres in it. */
QListWidget#Recents::item:disabled {{ background: transparent; }}
QFrame#RecentsRule {{ background: {t['border']}; border: none; }}

/* The main tabs are the app's navigation: larger, semi-bold, and the current
   one underlined in the accent. A dialog's tabs keep the quieter style below. */
QTabWidget#MainTabs::pane {{ border: none; border-top: 1px solid {t['border']}; top: -1px; }}
QTabWidget#MainTabs QTabBar {{ background: transparent; }}
QTabWidget#MainTabs QTabBar::tab {{
    background: transparent;
    color: {t['text-dim']};
    /* An edge all round, seen only with the focus: so the tab does not
       move as it gets it. */
    border: 1px solid transparent;
    border-bottom: 2px solid transparent;
    padding: {m['tab-pad']};
    margin-right: 4px;
    font-size: {px + 1}px;
    font-weight: 600;
}}
QTabWidget#MainTabs QTabBar::tab:hover {{ color: {t['text']}; border-bottom: 2px solid {t['border']}; }}
QTabWidget#MainTabs QTabBar::tab:selected {{ color: {t['text']}; border-bottom: 2px solid {t['accent']}; }}
/* The focus is a box round the tab. An underline was the selected tab's own
   mark again, and the focus is always on the selected tab: tabbing to the
   main tabs, or to the XML view's strip of mods inside them, showed nothing. */
QTabWidget#MainTabs QTabBar::tab:focus {{ color: {t['text']}; border-color: {t['focus']}; }}
QTabWidget#MainTabs QTabBar::tab:disabled {{ color: {t['text-disabled']}; border-bottom: 2px solid transparent; }}

/* The XML view's strip of mods. Where the mods outrun the strip, its two
   arrows are buttons to be seen. */
QTabBar#ModStrip::scroller {{ width: {(px + 14) * 2}px; }}
QTabBar#ModStrip QToolButton {{
    background: {t['raised']};
    border: 1px solid {t['border']};
    border-radius: {radius}px;
    margin: 2px 1px;
}}
QTabBar#ModStrip QToolButton:hover {{ border-color: {t['accent']}; }}
QTabBar#ModStrip QToolButton:disabled {{ background: {t['surface']}; }}
QTabBar#ModStrip QToolButton::left-arrow {{ image: url({img['strip_left']}); }}
QTabBar#ModStrip QToolButton::left-arrow:disabled {{ image: url({img['strip_left_disabled']}); }}
QTabBar#ModStrip QToolButton::right-arrow {{ image: url({img['strip_right']}); }}
QTabBar#ModStrip QToolButton::right-arrow:disabled {{ image: url({img['strip_right_disabled']}); }}

/* A dialog's tabs stand on the box's edge rather than over it: drawn into
   the box, the selected one broke its top line and its rounded corner,
   which read as the tab covering the box (the main tabs had the same fix
   as their underline). */
QTabWidget::pane {{ border: 1px solid {t['border']}; border-radius: 4px; top: 0px; }}
QTabBar::tab {{
    background: {t['bg']};
    color: {t['text-dim']};
    border: 1px solid transparent;
    border-top-left-radius: 4px;
    border-top-right-radius: 4px;
    padding: 8px 18px;
    margin-right: 2px;
}}
QTabBar::tab:selected {{
    background: {t['surface']};
    color: {t['accent-text']};
    border-color: {t['border']};
    border-bottom-color: {t['surface']};
}}
QTabBar::tab:focus {{ border-color: {t['focus']}; }}
QTabBar::tab:disabled {{ color: {t['text-disabled']}; }}

QGroupBox {{
    border: 1px solid {t['border']};
    border-radius: 4px;
    margin-top: 10px;
    padding-top: 8px;
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    left: 10px;
    padding: 0 4px;
    color: {t['text-dim']};
    font-weight: 600;
}}

QScrollBar:vertical {{ background: {t['bg']}; width: 12px; margin: 0; }}
QScrollBar:horizontal {{ background: {t['bg']}; height: 12px; margin: 0; }}
QScrollBar::handle:vertical {{ background: {t['scrollbar']}; border-radius: 6px; min-height: 30px; }}
QScrollBar::handle:horizontal {{ background: {t['scrollbar']}; border-radius: 6px; min-width: 30px; }}
QScrollBar::handle:vertical:hover, QScrollBar::handle:horizontal:hover {{ background: {t['scrollbar-hover']}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
/* The Export tab's left column: its bar is as wide as the gap it stands
   in (output_panel.COLUMN_GAP), and the handle is rounded to that. */
QScrollArea#ExportColumn QScrollBar:vertical {{ width: 8px; }}
QScrollArea#ExportColumn QScrollBar::handle:vertical {{ border-radius: 4px; }}

QStatusBar {{ background: {t['raised']}; color: {t['text-dim']}; }}
QStatusBar::item {{ border: none; }}
/* Without this, the QWidget background above paints the status label its own
   rectangle on the bar. */
QStatusBar QLabel {{ background: transparent; }}
QToolTip {{
    background: {t['raised']};
    color: {t['text']};
    border: 1px solid {t['field-border']};
    padding: 6px 8px;
}}
/* A HelpTip's floating panel. */
QFrame#HelpPanel {{
    background: {t['raised']};
    border: 1px solid {t['field-border']};
    border-radius: {radius}px;
}}
QFrame#HelpPanel QLabel {{ background: transparent; }}
QProgressBar {{
    background: {t['surface']};
    border: 1px solid {t['border']};
    border-radius: 4px;
    text-align: center;
}}
QProgressBar::chunk {{ background: {t['accent']}; border-radius: 3px; }}
/* Every state is an image at the same declared size, with no CSS border on
   any of them, so toggling one cannot nudge the label beside it. */
QCheckBox::indicator, QRadioButton::indicator {{
    width: {px + 2}px; height: {px + 2}px;
    border: none;
    background: transparent;
}}
QCheckBox::indicator {{ image: url({img['check_off']}); }}
QCheckBox::indicator:hover {{ image: url({img['check_off_hover']}); }}
QCheckBox::indicator:checked {{ image: url({img['check']}); }}
/* Some ticked: the box over a table's ticks (widgets.HeaderTick). */
QCheckBox::indicator:indeterminate {{ image: url({img['check_partial']}); }}
QCheckBox::indicator:disabled {{ image: url({img['check_off_disabled']}); }}
QCheckBox::indicator:checked:disabled {{ image: url({img['check_disabled']}); }}
/* A checkbox on an item of a tree, list or table is the same image. Fusion's
   own has no edge on a light surface, so an unticked one could not be seen. */
QAbstractItemView::indicator {{ width: {px + 2}px; height: {px + 2}px; }}
QAbstractItemView::indicator:unchecked {{ image: url({img['check_off']}); }}
QAbstractItemView::indicator:checked {{ image: url({img['check']}); }}
QAbstractItemView::indicator:indeterminate {{ image: url({img['check_partial']}); }}
QAbstractItemView::indicator:unchecked:disabled {{ image: url({img['check_off_disabled']}); }}
QAbstractItemView::indicator:checked:disabled {{ image: url({img['check_disabled']}); }}
QRadioButton::indicator {{ image: url({img['radio_off']}); }}
QRadioButton::indicator:hover {{ image: url({img['radio_off_hover']}); }}
QRadioButton::indicator:checked {{ image: url({img['radio_on']}); }}
QRadioButton::indicator:disabled {{ image: url({img['radio_off_disabled']}); }}
QRadioButton::indicator:checked:disabled {{ image: url({img['radio_on_disabled']}); }}
/* In the disabled colour, as a button that is off is. A checkbox had no
   rule and kept the text colour, and a radio button had text-dim, the
   colour of a hint: either one that was off read as one that was on. So
   did a field, a list to pick from, a number box, a label and a group's
   title, each given the text colour by QWidget above and never taking it
   back (A10-39). */
QCheckBox:disabled, QRadioButton:disabled,
QLineEdit:disabled, QPlainTextEdit:disabled, QComboBox:disabled,
QAbstractSpinBox:disabled, QLabel:disabled, QGroupBox::title:disabled {{ color: {t['text-disabled']}; }}
QCheckBox:focus, QRadioButton:focus {{ outline: none; border-bottom: 2px solid {t['focus']}; }}
/* The box over a table's ticks is on the heading's surface, not its own. */
QHeaderView QCheckBox {{ background: transparent; }}
QMenuBar {{ background: {t['raised']}; }}
QMenuBar::item:selected {{ background: {t['selection']}; }}
QMenuBar::item:disabled {{ color: {t['text-disabled']}; }}
QMenu {{ background: {t['surface']}; border: 1px solid {t['border']}; }}
/* Styling a menu at all takes it off the palette, so an entry that is not
   available has to be told to look it: in the disabled colour, as a button
   that is off is, not text-dim, a hint's colour that reads as on (A11-96). */
QMenu::item {{ color: {t['text']}; padding: 5px 26px 5px 22px; }}
QMenu::item:selected {{ background: {t['selection']}; }}
QMenu::item:disabled {{ color: {t['text-disabled']}; }}
QMenu::item:disabled:selected {{ background: transparent; color: {t['text-disabled']}; }}
QMenu::separator {{ height: 1px; background: {t['border']}; margin: 4px 10px; }}
/* Wide enough to grab: the main window is split by one of these. */
QSplitter::handle {{ background: {t['border']}; }}
QSplitter::handle:hover {{ background: {t['selection']}; }}
QSplitter::handle:horizontal {{ width: 4px; }}
QSplitter::handle:vertical {{ height: 4px; }}
/* Between two panes that each have an edge of their own, the handle is the
   gap, and shows itself under the pointer. */
QSplitter#XmlPanes::handle {{ background: transparent; }}
QSplitter#XmlPanes::handle:hover {{ background: {t['selection']}; }}

/* A button, checkbox, radio button or tab that got the focus from a click
   (``keep_focus_marks_for_the_keyboard`` marks it ``pointed``) is drawn as
   it is without the focus: the click said where the pointer is, and a mark
   under a checkbox just clicked reads as a second selection. Each rule
   here undoes one focus rule above, and a key pressed takes the mark off. */
QPushButton[pointed="true"]:focus {{
    border: 1px solid {t['border']}; padding: {pad_v}px {pad_h}px;
}}
QPushButton[pointed="true"]:focus:hover {{ border-color: {t['accent']}; }}
QPushButton#Primary[pointed="true"]:focus {{ border: 1px solid {t['accent']}; }}
QPushButton#NavButton[pointed="true"]:focus {{ padding: 4px 6px; }}
QToolButton#PlayWhere[pointed="true"]:focus, QToolButton#PanelMove[pointed="true"]:focus {{
    border: 1px solid {t['border']}; padding: {pad_v}px 7px;
}}
QToolButton#PlayWhere[pointed="true"]:focus:hover, QToolButton#PanelMove[pointed="true"]:focus:hover {{ border-color: {t['accent']}; }}
QPushButton#Segment[pointed="true"]:focus {{ border: 1px solid {t['border']}; }}
QPushButton#Segment[pointed="true"]:focus:checked {{ border-color: {t['accent']}; }}
QCheckBox[pointed="true"]:focus, QRadioButton[pointed="true"]:focus {{ border: none; }}
QTabBar[pointed="true"]::tab:focus {{
    border-color: {t['border']}; border-bottom-color: {t['surface']};
}}
QTabWidget#MainTabs QTabBar[pointed="true"]::tab:focus {{
    border-color: transparent; border-bottom-color: {t['accent']};
}}
"""
    css += OUTLINES[outline or _outlines_now].format_map(t)
    if of.contrast:
        # Every boundary drawn, and what is selected filled with the accent.
        css += f"""
QWidget {{ selection-color: {t['text-on-accent']}; }}
QPushButton {{ border: 1px solid {t['text']}; }}
QPushButton:hover {{ border: 2px solid {t['accent']}; }}
QTreeWidget::item:selected, QTableWidget::item:selected, QListWidget::item:selected {{ background: {t['accent']}; color: {t['text-on-accent']}; }}
QMenu::item:selected, QMenuBar::item:selected {{ background: {t['accent']}; color: {t['text-on-accent']}; }}
QTabWidget#MainTabs QTabBar::tab:selected {{ border-bottom: 3px solid {t['accent']}; }}
QPushButton#Segment:checked {{ background: {t['accent']}; color: {t['text-on-accent']}; }}
/* Filled with the accent, as the primary button is, so its ring is the text colour. */
QPushButton#Segment:checked:focus {{ border: 2px solid {t['text']}; }}
QPushButton#Segment[pointed="true"]:focus {{ border: 1px solid {t['text']}; }}
QPushButton#Segment[pointed="true"]:focus:checked {{ border: 1px solid {t['accent']}; }}
QPushButton[pointed="true"]:focus {{ border: 1px solid {t['text']}; }}
QPushButton[pointed="true"]:focus:hover {{ border: 2px solid {t['accent']}; }}
QToolButton#PanelMove {{ border: 1px solid {t['text']}; }}
QToolButton#PanelMove:hover {{ border: 2px solid {t['accent']}; }}
QToolButton#PlayWhere[pointed="true"]:focus, QToolButton#PanelMove[pointed="true"]:focus {{ border: 1px solid {t['text']}; }}
QToolButton#PlayWhere[pointed="true"]:focus:hover, QToolButton#PanelMove[pointed="true"]:focus:hover {{ border: 2px solid {t['accent']}; }}
QTabWidget#MainTabs QTabBar[pointed="true"]::tab:focus {{ border-bottom: 3px solid {t['accent']}; }}
QFrame#StepHead QLabel#StepBadge {{ background: {t['accent']}; color: {t['text-on-accent']}; }}
"""
    # What ``tint`` sets. After everything a label's own name can say.
    for tone in TONES:
        selectors = ['QWidget[tone="%s"]' % tone] + [
            'QLabel#%s[tone="%s"]' % (name, tone) for name in _COLOURED_LABELS
        ]
        css += "%s { color: %s; }\n" % (", ".join(selectors), t[tone])
    css += 'QWidget[strong="true"] { font-weight: 600; }\n'
    # A status label's glyph, drawn at the start of its first line. Labels
    # named Warn, Error and Ok get theirs from the name.
    #
    # The indent is set, to nothing. The padding makes the label a frame to
    # Qt, and a frame with no indent of its own gets one: the width of an x
    # when the label measures its text, half that when it draws it. So it
    # wrapped earlier than it drew, kept a line too many, and centred the
    # text in it -- a line below its glyph. The padding is the glyph's room
    # and the half x it was drawn after.
    for tone in STATUS:
        selectors = ['QLabel[glyph="%s"]' % tone, "QLabel#%s" % tone.capitalize()] + [
            'QLabel#%s[glyph="%s"]' % (name, tone) for name in _COLOURED_LABELS
        ]
        css += (
            "%s { background-image: url(%s); background-repeat: no-repeat; "
            "background-position: top left; padding-left: %dpx; qproperty-indent: 0; }\n"
            % (", ".join(selectors), img["label_" + tone], px + 10)
        )
    # Last, so it wins over every rule above that names a family.
    css += '*[mono="true"] { font-family: %s; }\n' % MONO_FAMILIES
    return css


def palette(of: Optional[Scheme] = None):
    """The application palette for a scheme, so Fusion's own drawing (the
    checkboxes in item views, focus rectangles) follows it too."""
    from PySide6.QtGui import QColor, QPalette

    of = of or _scheme_now
    t = of.tokens
    pal = QPalette()
    role = QPalette.ColorRole
    for r, token in (
        (role.Window, "bg"), (role.Base, "surface"), (role.AlternateBase, "alt-row"),
        (role.Button, "raised"), (role.Text, "text"), (role.WindowText, "text"),
        (role.ButtonText, "text"), (role.Highlight, "selection"),
        (role.HighlightedText, "text-on-accent" if of.contrast else "text"),
        (role.ToolTipBase, "raised"), (role.ToolTipText, "text"),
        (role.PlaceholderText, "text-dim"), (role.Link, "accent-text"),
        (role.Mid, "border"), (role.Dark, "border"), (role.Light, "raised"),
        (role.Midlight, "raised"),
    ):
        pal.setColor(r, QColor(t[token]))
    return pal


# What an item in a list, tree or table may be drawn in. On a scheme change
# each item drawn in one of the old scheme's values gets the new scheme's.
_ITEM_TOKENS = ("text", "text-dim", "text-disabled", "accent-text", "rule",
                "warn", "added", "error", "removed", "ok", "changed")


def _recolour_items(app, old: Scheme, new: Scheme) -> None:
    """Move every list, tree and table item from ``old``'s colours to ``new``'s.

    Items keep the brush they were filled with, so they would otherwise stay
    in the old scheme until their list is next filled. Two tokens with the
    same value in ``old`` cannot be told apart here, and the first in
    ``_ITEM_TOKENS`` wins. That only happens leaving a Windows contrast
    theme, whose status colours are all the text colour.
    """
    from PySide6.QtCore import QSignalBlocker, Qt
    from PySide6.QtGui import QColor
    from PySide6.QtWidgets import (
        QListWidget,
        QTableWidget,
        QTreeWidget,
        QTreeWidgetItemIterator,
    )

    moved = {}
    for token in reversed(_ITEM_TOKENS):
        moved[QColor(old[token]).rgb()] = new[token]
    none = Qt.BrushStyle.NoBrush

    def turn(brush, alpha_of=None):
        if brush.style() == none:
            return None
        to = moved.get(brush.color().rgb())
        if to is None:
            return None
        colour = QColor(to)
        colour.setAlpha(brush.color().alpha())
        return colour

    for widget in app.allWidgets():
        if isinstance(widget, QTreeWidget):
            blocker = QSignalBlocker(widget)
            items = QTreeWidgetItemIterator(widget)
            while items.value():
                item = items.value()
                items += 1
                for column in range(item.columnCount()):
                    colour = turn(item.foreground(column))
                    if colour is not None:
                        item.setForeground(column, colour)
        elif isinstance(widget, QTableWidget):
            blocker = QSignalBlocker(widget)
            for row in range(widget.rowCount()):
                for column in range(widget.columnCount()):
                    item = widget.item(row, column)
                    colour = turn(item.foreground()) if item is not None else None
                    if colour is not None:
                        item.setForeground(colour)
        elif isinstance(widget, QListWidget):
            blocker = QSignalBlocker(widget)
            for row in range(widget.count()):
                colour = turn(widget.item(row).foreground())
                if colour is not None:
                    widget.item(row).setForeground(colour)
        else:
            continue
        del blocker
        widget.viewport().update()


def apply(app, to: Optional[Scheme] = None, outline: str = "", dense: str = "") -> None:
    """Restyle the running app. Anything left out stays as it is.

    Sets the application palette and style sheet from the scheme, moves list
    items to the new colours, and tells whatever listens to ``notifier()``.
    Nothing needs a restart.
    """
    global _scheme_now, _outlines_now, _density_now
    old = _scheme_now
    _scheme_now = to or _scheme_now
    _outlines_now = outline or _outlines_now
    _density_now = dense or _density_now
    app.setPalette(palette())
    app.setStyleSheet(stylesheet())
    if old.tokens != _scheme_now.tokens:
        _recolour_items(app, old, _scheme_now)
    notifier().changed.emit()


def repolish(widget):
    """Have the style sheet read ``widget`` again, after a property or an
    object name it selects on has changed."""
    from PySide6.QtCore import QCoreApplication, QEvent

    widget.style().unpolish(widget)
    widget.style().polish(widget)
    glyph_beside_first_line(widget)
    # Polishing alone leaves a shown widget with the padding it had. This
    # is what has it measure itself again, so a glyph gets its room.
    QCoreApplication.sendEvent(widget, QEvent(QEvent.Type.StyleChange))
    widget.updateGeometry()
    return widget


def glyph_beside_first_line(label) -> None:
    """Start a status label's text at its top, where its glyph is drawn --
    and put back where it was once the glyph is gone.

    A label centres its text up and down, and a label given more height than
    its text left its glyph a line above it. A row that wants a sentence
    centred on a taller button puts the label in it at its own height
    (``addWidget(label, 1, Qt.AlignmentFlag.AlignVCenter)``)."""
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QLabel

    if not isinstance(label, QLabel):
        return
    vertical = Qt.AlignmentFlag.AlignVertical_Mask
    held = label.property("unglyphed-align")
    if label.property("glyph") in STATUS or label.objectName().lower() in STATUS:
        if held is None:
            label.setProperty("unglyphed-align", (label.alignment() & vertical).value)
        label.setAlignment((label.alignment() & ~vertical) | Qt.AlignmentFlag.AlignTop)
    elif held is not None:
        label.setAlignment((label.alignment() & ~vertical) | Qt.AlignmentFlag(held))
        label.setProperty("unglyphed-align", None)


def keep_focus_marks_for_the_keyboard(app) -> None:
    """From now on, a button, checkbox, radio button or tab clicked with the
    mouse does not draw its focus mark, until a key is pressed on it.

    Qt gives a clicked control the focus, and a style sheet cannot tell that
    focus from the keyboard's. So a click left an underline under a checkbox
    or an edge round a button that said nothing the click had not, and beside
    tabs that underline what is current it read as a second thing selected.
    A control reached by Tab, by a shortcut or by the Studio itself draws
    its mark as before, and so does every field and list, where the mark
    says where typing goes.

    A window coming to the front hands the focus to a control with no click
    on it: a dialog opening on its first radio button, or the Studio come
    back to from another program. That one goes by what was last used, the
    mouse or the keyboard. A dialog opened with a click drew the mark under
    the radio button it opened on, which nobody had tabbed to.

    Qt hands the focus on by itself in two more cases, and names a reason
    that is not the mouse's in each. A control that was clicked and then
    disabled or hidden passes it to the next one as if Tab had been pressed:
    that one goes by what was last used too, and a real Tab is a key pressed
    first. And a menu that closes gives it back to the control that had it,
    which is then as it was before the menu, unless the keyboard was used in
    between.
    """
    if getattr(app, "_pointer_focus_filter", None) is not None:
        return
    from PySide6.QtCore import QEvent, QObject, Qt
    from PySide6.QtWidgets import (
        QCheckBox,
        QPushButton,
        QRadioButton,
        QTabBar,
        QToolButton,
    )

    kinds = (QPushButton, QCheckBox, QRadioButton, QTabBar, QToolButton)
    alone = (Qt.Key.Key_Shift, Qt.Key.Key_Control, Qt.Key.Key_Alt, Qt.Key.Key_Meta)
    # The reasons Qt gives when it moves the focus with no click on the
    # control, and with no key pressed either unless one just was.
    handed_on = (Qt.FocusReason.ActiveWindowFocusReason, Qt.FocusReason.TabFocusReason,
                 Qt.FocusReason.BacktabFocusReason)

    def mark(widget, pointed: bool) -> None:
        if bool(widget.property("pointed")) != pointed:
            widget.setProperty("pointed", pointed)
            repolish(widget)

    class Filter(QObject):
        by_mouse = False   # what was last used: the mouse, or the keyboard

        def eventFilter(self, watched, event):
            kind = event.type()
            if kind == QEvent.Type.MouseButtonPress:
                self.by_mouse = True
            elif kind == QEvent.Type.FocusIn and isinstance(watched, kinds):
                reason = event.reason()
                if reason == Qt.FocusReason.MouseFocusReason:
                    mark(watched, True)
                elif reason in handed_on:
                    mark(watched, self.by_mouse)
                elif reason == Qt.FocusReason.PopupFocusReason:
                    mark(watched, bool(watched.property("pointed")) and self.by_mouse)
                else:
                    mark(watched, False)
            elif kind == QEvent.Type.KeyPress and event.key() not in alone:
                self.by_mouse = False
                if isinstance(watched, kinds) and watched.property("pointed"):
                    mark(watched, False)
            return False

    app._pointer_focus_filter = Filter(app)
    app.installEventFilter(app._pointer_focus_filter)


def tint(widget, tone: str = "", strong: bool = False, glyph: bool = True):
    """Draw ``widget``'s text in a tone (one of ``TONES``), or in its usual
    colour for "". ``strong`` makes it semi-bold.

    A status tone (``STATUS``) puts its glyph before a label's text, so the
    status does not rest on colour alone. ``glyph=False`` leaves it out, for
    text that already says so in words or has no room for one.

    The colour comes from the application's style sheet, so it follows the
    scheme and the widget carries no style sheet of its own.
    """
    if tone and tone not in TONES:
        raise ValueError("no such tone: %s" % tone)
    shown = tone if glyph and tone in STATUS else ""
    if ((widget.property("tone") or "") == tone and bool(widget.property("strong")) == strong
            and (widget.property("glyph") or "") == shown):
        return widget
    widget.setProperty("tone", tone or None)
    widget.setProperty("strong", True if strong else None)
    widget.setProperty("glyph", shown or None)
    return repolish(widget)


_status_engine = None


def icon(tone: str):
    """The glyph for a status tone, as an icon that follows the scheme.

    It is drawn from the scheme in effect each time it is painted, so an
    item that carries it needs nothing done when the scheme changes.
    """
    if tone not in STATUS:
        raise ValueError("no such status: %s" % tone)
    return glyph("status_" + tone)


def glyph(image: str):
    """One of :func:`indicator_svgs`'s images by name, as an icon that
    follows the scheme the way :func:`icon` does."""
    global _status_engine
    from PySide6.QtCore import QRectF, QSize, Qt
    from PySide6.QtGui import QIcon, QIconEngine, QPainter, QPixmap
    from PySide6.QtSvg import QSvgRenderer

    if image not in indicator_svgs():
        raise ValueError("no such image: %s" % image)
    if _status_engine is None:

        class StatusEngine(QIconEngine):
            def __init__(self, image):
                super().__init__()
                self.image = image

            def paint(self, painter, rect, mode, state):
                svg = indicator_svgs()[self.image]
                side = min(rect.width(), rect.height())
                QSvgRenderer(svg.encode("utf-8")).render(painter, QRectF(
                    rect.x() + (rect.width() - side) / 2,
                    rect.y() + (rect.height() - side) / 2, side, side))

            def pixmap(self, size, mode, state):
                pixmap = QPixmap(size)
                pixmap.fill(Qt.GlobalColor.transparent)
                painter = QPainter(pixmap)
                self.paint(painter, pixmap.rect(), mode, state)
                painter.end()
                return pixmap

            def actualSize(self, size, mode, state):
                side = min(size.width(), size.height())
                return QSize(side, side)

            def clone(self):
                return StatusEngine(self.image)

        _status_engine = StatusEngine
    return QIcon(_status_engine(image))


def mark(item, tone: str, column=None):
    """Draw a list, tree or table item's text in a token's colour, with the
    glyph as its icon where the token is a status (``STATUS``).

    ``column`` is for a tree item. Marking with any other token, such as
    "text-dim", takes the glyph off again.
    """
    from PySide6.QtGui import QColor, QIcon

    glyph = icon(tone) if tone in STATUS else QIcon()
    where = () if column is None else (column,)
    item.setForeground(*where, QColor(colour(tone)))
    item.setIcon(*where, glyph)
    return item


def monospace(widget):
    """Draw ``widget`` in the monospaced face. See MONO_FAMILIES for why this
    is a property the style sheet reads and not a setFont call."""
    widget.setProperty("mono", True)
    return repolish(widget)


def mono_font():
    """The monospaced face for a table or tree cell, where setFont does work:
    an item's font reaches the delegate as its own role, over the style sheet.

    Sized explicitly, because it is not resolved against the view's font.
    """
    from PySide6.QtGui import QFont

    font = QFont(MONO_FAMILIES.split(",")[0].strip().strip('"'))
    font.setStyleHint(QFont.StyleHint.Monospace)
    font.setPixelSize(FONT_PX)
    return font
