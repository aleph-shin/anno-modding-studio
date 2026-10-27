"""The colour schemes and style rules approved in the design review (2 Oct 2026).

This is the specification Stage 0 builds ``ui/theme.py`` from, kept as the
code that produced the reference renders in ``../renders/``. It works by
patching the theme of commit ``cb10f5a``; see ``../README.md``.

Keys are the mockup's own and map to the proposal's tokens like this:

    BG bg · BG_ALT surface · BG_RAISED raised · ALT_ROW alt-row · BORDER border
    FIELD_BORDER field-border · FOCUS focus · TEXT text · TEXT_DIM text-dim
    DISABLED text-disabled · ON_ACCENT text-on-accent · ACCENT accent
    ACCENT_HOVER accent-hover · ACCENT_TEXT accent-text · ACCENT_DIM selection
    CHANGED ok/changed · WARN warn · ERROR error/removed · RULE rule
    SYN_* syn-* (syn-attr is ACCENT_TEXT) · SCROLL(_HOVER) scrollbar handle
    LOGO the mod.io press-kit logo for this background
    RADIUS, PAD_V, PAD_H, ROW_PAD metrics (Comfortable density)
"""

from __future__ import annotations

from pathlib import Path

SCHEMES = {
    # Today's look, tidied. Gold means only "you can act on this / current",
    # warnings are orange, field borders and scrollbars reach 3:1.
    "granite": {
        "label": "Granite",
        "BG": "#181b20", "BG_ALT": "#1f232a", "BG_RAISED": "#262b33",
        "ALT_ROW": "#22262e", "BORDER": "#363d49", "FIELD_BORDER": "#6e7582",
        "FOCUS": "#e0a44c",
        "TEXT": "#e3e7ee", "TEXT_DIM": "#9ba4b2", "DISABLED": "#5d6675",
        "ON_ACCENT": "#201a10",
        "ACCENT": "#e0a44c", "ACCENT_HOVER": "#efb75f", "ACCENT_TEXT": "#e8b25f",
        "ACCENT_DIM": "#6e5226",
        "CHANGED": "#7ec699", "WARN": "#f0894a", "ERROR": "#f07272", "RULE": "#7aa7e6",
        "SYN_COMMENT": "#818c9c", "SYN_TAG": "#7aa6d6", "SYN_VALUE": "#a8d99a",
        "SCROLL": "#616874", "SCROLL_HOVER": "#727a87",
        "LOGO": "Blue_White",
        "RADIUS": 4, "PAD_V": 6, "PAD_H": 14, "ROW_PAD": 3,
        "dark": True,
    },
    # Brick red carried by the surfaces, strongest in the raised bands. Error
    # and removed are lighter and pinker than any surface.
    "brick": {
        "label": "Brick",
        "BG": "#1f1311", "BG_ALT": "#291715", "BG_RAISED": "#4a221c",
        "ALT_ROW": "#251513", "BORDER": "#5f322b", "FIELD_BORDER": "#9a7068",
        "FOCUS": "#e2a64e",
        "TEXT": "#f0e7e3", "TEXT_DIM": "#c2aaa3", "DISABLED": "#7d5f58",
        "ON_ACCENT": "#201a10",
        "ACCENT": "#e2a64e", "ACCENT_HOVER": "#f0b862", "ACCENT_TEXT": "#eab562",
        "ACCENT_DIM": "#6e3f1e",
        "CHANGED": "#8fd3a4", "WARN": "#f39257", "ERROR": "#ff8fa3", "RULE": "#93b6e8",
        "SYN_COMMENT": "#a8948e", "SYN_TAG": "#93b6e8", "SYN_VALUE": "#a9d99c",
        "SCROLL": "#90615a", "SCROLL_HOVER": "#a3716a",
        "LOGO": "Blue_White",
        "RADIUS": 4, "PAD_V": 6, "PAD_H": 14, "ROW_PAD": 3,
        "dark": True,
    },
    # Cool white, not cream. Gold fills the primary button; bronze wherever
    # the accent is read as text, and for focus (gold on white is 2.3:1).
    "marble": {
        "label": "Marble",
        "BG": "#f4f5f7", "BG_ALT": "#ffffff", "BG_RAISED": "#e9ebef",
        "ALT_ROW": "#f7f8fa", "BORDER": "#d3d7de", "FIELD_BORDER": "#80868f",
        "FOCUS": "#8a5800",
        "TEXT": "#1c2129", "TEXT_DIM": "#59616e", "DISABLED": "#9aa1ad",
        "ON_ACCENT": "#1f1606",
        "ACCENT": "#d8962f", "ACCENT_HOVER": "#e4a541", "ACCENT_TEXT": "#8a5800",
        "ACCENT_DIM": "#f6e2bd",
        "CHANGED": "#1d7a45", "WARN": "#b4530f", "ERROR": "#c0312f", "RULE": "#2a5fae",
        "SYN_COMMENT": "#6b7280", "SYN_TAG": "#2257a3", "SYN_VALUE": "#2f7a32",
        "SCROLL": "#898d93", "SCROLL_HOVER": "#767b83",
        "LOGO": "Blue_Black",
        "RADIUS": 4, "PAD_V": 6, "PAD_H": 14, "ROW_PAD": 3,
        "dark": False,
    },
    # Pale pine with a pine-green accent, like painted trim, so nothing you
    # can act on shares the wood's hue. Changed moves to a teal-green.
    "timber": {
        "label": "Timber",
        "BG": "#efe3cf", "BG_ALT": "#fbf6ee", "BG_RAISED": "#e3d2b6",
        "ALT_ROW": "#f6efe3", "BORDER": "#cfb894", "FIELD_BORDER": "#8b7251",
        "FOCUS": "#2f6146",
        "TEXT": "#2b2117", "TEXT_DIM": "#5c4c3a", "DISABLED": "#9a8a74",
        "ON_ACCENT": "#ffffff",
        "ACCENT": "#2f6146", "ACCENT_HOVER": "#3a7356", "ACCENT_TEXT": "#285a3f",
        "ACCENT_DIM": "#cfe2c9",
        "CHANGED": "#17666b", "WARN": "#a8460a", "ERROR": "#a3142a", "RULE": "#23559c",
        "SYN_COMMENT": "#6f604c", "SYN_TAG": "#23559c", "SYN_VALUE": "#2c6a2c",
        "SCROLL": "#917e62", "SCROLL_HOVER": "#7d6a50",
        "LOGO": "Blue_Black",
        "RADIUS": 4, "PAD_V": 6, "PAD_H": 14, "ROW_PAD": 3,
        "dark": False,
    },
    # Black, white and yellow, every boundary drawn. Text at 7:1 or better.
    "obsidian": {
        "label": "Obsidian (high contrast)",
        "BG": "#000000", "BG_ALT": "#000000", "BG_RAISED": "#0d0d0d",
        "ALT_ROW": "#000000", "BORDER": "#ffffff", "FIELD_BORDER": "#ffffff",
        "FOCUS": "#ffd84a",
        "TEXT": "#ffffff", "TEXT_DIM": "#e0e0e0", "DISABLED": "#a0a0a0",
        "ON_ACCENT": "#000000",
        "ACCENT": "#ffd84a", "ACCENT_HOVER": "#ffe57a", "ACCENT_TEXT": "#ffd84a",
        "ACCENT_DIM": "#ffd84a",
        "CHANGED": "#5cff9a", "WARN": "#ffa040", "ERROR": "#ff7a7a", "RULE": "#8ec5ff",
        "SYN_COMMENT": "#d0d0d0", "SYN_TAG": "#8ec5ff", "SYN_VALUE": "#5cff9a",
        "SCROLL": "#ffffff", "SCROLL_HOVER": "#ffd84a",
        "LOGO": "Mono_White",
        "RADIUS": 0, "PAD_V": 6, "PAD_H": 14, "ROW_PAD": 3,
        "dark": True, "contrast": True,
    },
    # White, black and a deep blue accent with white text on it. Rule goes
    # purple so it never matches the accent. Text at 7:1 or better.
    "quartz": {
        "label": "Quartz (high contrast)",
        "BG": "#ffffff", "BG_ALT": "#ffffff", "BG_RAISED": "#f2f2f2",
        "ALT_ROW": "#ffffff", "BORDER": "#000000", "FIELD_BORDER": "#000000",
        "FOCUS": "#00308a",
        "TEXT": "#000000", "TEXT_DIM": "#262626", "DISABLED": "#6b6b6b",
        "ON_ACCENT": "#ffffff",
        "ACCENT": "#00308a", "ACCENT_HOVER": "#0040b0", "ACCENT_TEXT": "#00308a",
        "ACCENT_DIM": "#00308a",
        "CHANGED": "#00602a", "WARN": "#7a3300", "ERROR": "#a0001c", "RULE": "#5a1d8c",
        "SYN_COMMENT": "#3d3d3d", "SYN_TAG": "#00308a", "SYN_VALUE": "#00602a",
        "SCROLL": "#000000", "SCROLL_HOVER": "#00308a",
        "LOGO": "Mono_Black",
        "RADIUS": 0, "PAD_V": 6, "PAD_H": 14, "ROW_PAD": 3,
        "dark": False, "contrast": True,
    },
}

# Rendered and turned down on 2 Oct, kept so the choice can be revisited.
REJECTED = {
    # Read as brown rather than brick.
    "brick_muted": dict(
        SCHEMES["brick"],
        label="Brick, muted",
        BG="#1d1514", BG_ALT="#251a19", BG_RAISED="#3a2421", ALT_ROW="#221817",
        BORDER="#4e3531", FIELD_BORDER="#896d67", TEXT_DIM="#bba7a1",
        DISABLED="#76625d", ACCENT_DIM="#6a4a22", SCROLL="#76605c",
        SCROLL_HOVER="#8a716c",
    ),
    # The accent shared the wood's hue and stood out only by being darker.
    "timber_amber": dict(
        SCHEMES["timber"],
        label="Timber, burnt amber",
        FOCUS="#a4520a", ON_ACCENT="#ffffff", ACCENT="#a4520a",
        ACCENT_HOVER="#b8620f", ACCENT_TEXT="#8f4706", ACCENT_DIM="#f0cf98",
        CHANGED="#1e6b3a", WARN="#b0360c",
    ),
}

# Graphite's flattening and density, as first rendered. Stage 0 splits it
# into Outlines: Flat (the QGroupBox, list and header rules) and Density:
# Compact (the metrics and the tab padding). It sets no colour of its own
# beyond the scheme's tokens.
GRAPHITE_STYLE = {"RADIUS": 2, "PAD_V": 4, "PAD_H": 12, "ROW_PAD": 2, "flat": True}

# The press kit's logos, as committed for Stage 0.
LOGOS = Path(__file__).resolve().parents[3] / "anno_modding_studio" / "resources" / "modio"


def _svg(path: Path, body: str) -> str:
    path.write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" '
        f'viewBox="0 0 16 16">{body}</svg>',
        encoding="utf-8",
    )
    return path.as_posix()


def _box(r, fill, stroke):
    return (f'<rect x="1" y="1" width="14" height="14" rx="{r}" fill="{fill}" '
            f'stroke="{stroke}" stroke-width="1.5"/>')


def _tick(c):
    return (f'<path d="M4 8.3l2.6 2.6L12 5.4" fill="none" stroke="{c}" '
            'stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>')


def _ring(fill, stroke):
    return (f'<circle cx="8" cy="8" r="6.6" fill="{fill}" stroke="{stroke}" '
            'stroke-width="1.5"/>')


def _dot(c):
    return f'<circle cx="8" cy="8" r="3" fill="{c}"/>'


def _chevron(d, c):
    return (f'<path d="{d}" fill="none" stroke="{c}" stroke-width="1.8" '
            'stroke-linecap="round" stroke-linejoin="round"/>')


UP, DOWN, RIGHT = "M4 10l4-4 4 4", "M4 6l4 4 4-4", "M6 4l4 4-4 4"


def indicators(t: dict, out: Path) -> dict:
    """Write the scheme's SVG indicators into ``out``; their paths by name."""
    out.mkdir(parents=True, exist_ok=True)
    r = 3 if t["RADIUS"] else 0
    fb, fa, ac = t["FIELD_BORDER"], t["BG_ALT"], t["ACCENT"]
    dim, dis, focus = t["TEXT_DIM"], t["DISABLED"], t["FOCUS"]
    bg, border = t["BG"], t["BORDER"]
    shapes = {
        "check_off": _box(r, fa, fb),
        "check_off_hover": _box(r, fa, ac),
        "check_off_disabled": _box(r, bg, border),
        "check": _box(r, ac, focus) + _tick(t["ON_ACCENT"]),
        "check_disabled": _box(r, bg, border) + _tick(dis),
        "radio_off": _ring(fa, fb),
        "radio_off_hover": _ring(fa, ac),
        "radio_off_disabled": _ring(bg, border),
        "radio_on": _ring(fa, focus) + _dot(focus),
        "radio_on_disabled": _ring(bg, border) + _dot(dis),
        "spin_up": _chevron(UP, dim),
        "spin_up_hover": _chevron(UP, ac),
        "spin_up_disabled": _chevron(UP, dis),
        "spin_down": _chevron(DOWN, dim),
        "spin_down_hover": _chevron(DOWN, ac),
        "spin_down_disabled": _chevron(DOWN, dis),
        "branch_closed": _chevron(RIGHT, dim),
        "branch_open": _chevron(DOWN, dim),
    }
    return {name: _svg(out / f"{name}.svg", body) for name, body in shapes.items()}


def overrides(t: dict, ind: dict, px: int) -> str:
    """The QSS laid over the base theme: fields, focus, indicators, and the
    flat and high-contrast rules where the scheme or style asks for them."""
    R, F = t["RADIUS"], t["FOCUS"]
    css = f"""
QWidget {{ selection-background-color: {t['ACCENT_DIM']}; }}
QLineEdit, QPlainTextEdit, QTextEdit, QComboBox, QSpinBox {{ border: 1px solid {t['FIELD_BORDER']}; border-radius: {R}px; padding: {t['PAD_V'] - 1}px 7px; }}
QLineEdit:focus, QPlainTextEdit:focus, QTextEdit:focus, QComboBox:focus, QSpinBox:focus {{ border: 1px solid {F}; }}
QPushButton {{ border-radius: {R}px; padding: {t['PAD_V']}px {t['PAD_H']}px; }}
QPushButton:focus {{ border: 2px solid {F}; padding: {t['PAD_V'] - 1}px {t['PAD_H'] - 1}px; }}
QPushButton#Primary:focus {{ border: 2px solid {t['TEXT']}; }}
QPushButton#Link {{ color: {t['ACCENT_TEXT']}; }}
QCheckBox::indicator, QRadioButton::indicator {{ width: {px + 2}px; height: {px + 2}px; }}
QCheckBox::indicator {{ image: url({ind['check_off']}); }}
QCheckBox::indicator:hover {{ image: url({ind['check_off_hover']}); }}
QCheckBox::indicator:checked {{ image: url({ind['check']}); }}
QCheckBox::indicator:disabled {{ image: url({ind['check_off_disabled']}); }}
QCheckBox::indicator:checked:disabled {{ image: url({ind['check_disabled']}); }}
QRadioButton::indicator {{ image: url({ind['radio_off']}); }}
QRadioButton::indicator:hover {{ image: url({ind['radio_off_hover']}); }}
QRadioButton::indicator:checked {{ image: url({ind['radio_on']}); }}
QRadioButton::indicator:disabled {{ image: url({ind['radio_off_disabled']}); }}
QCheckBox:focus, QRadioButton:focus {{ outline: none; border-bottom: 2px solid {F}; }}
QSpinBox::up-arrow {{ image: url({ind['spin_up']}); width: {px - 4}px; height: {px - 4}px; }}
QSpinBox::up-arrow:hover {{ image: url({ind['spin_up_hover']}); }}
QSpinBox::down-arrow {{ image: url({ind['spin_down']}); width: {px - 4}px; height: {px - 4}px; }}
QSpinBox::down-arrow:hover {{ image: url({ind['spin_down_hover']}); }}
QTreeView::branch:has-children:closed {{ image: url({ind['branch_closed']}); }}
QTreeView::branch:has-children:open {{ image: url({ind['branch_open']}); }}
QTreeWidget, QTableWidget, QListWidget {{ border-radius: {R}px; alternate-background-color: {t['ALT_ROW']}; }}
QTreeWidget::item, QTableWidget::item {{ padding: {t['ROW_PAD']}px 4px; }}
QTreeWidget:focus, QTableWidget:focus, QListWidget:focus {{ border: 1px solid {F}; }}
QTabWidget#MainTabs QTabBar::tab:focus {{ color: {t['TEXT']}; border-bottom: 2px solid {t['ACCENT_TEXT']}; }}
QTabWidget#MainTabs QTabBar::tab:disabled {{ color: {t['DISABLED']}; }}
QLabel#AssetTitle {{ color: {t['TEXT']}; }}
QToolTip {{ border: 1px solid {t['FIELD_BORDER']}; padding: 6px 8px; }}
QScrollBar::handle:vertical, QScrollBar::handle:horizontal {{ background: {t['SCROLL']}; }}
QScrollBar::handle:vertical:hover, QScrollBar::handle:horizontal:hover {{ background: {t['SCROLL_HOVER']}; }}
"""
    if t.get("flat"):
        css += f"""
QGroupBox {{ border: none; border-top: 1px solid {t['BORDER']}; border-radius: 0; margin-top: 14px; padding-top: 10px; }}
QGroupBox::title {{ left: 0; padding: 0 6px 0 0; color: {t['TEXT']}; }}
QTreeWidget, QTableWidget, QListWidget {{ border: none; }}
QHeaderView::section {{ background: {t['BG_ALT']}; border: none; border-bottom: 1px solid {t['BORDER']}; }}
QFrame#StepCard {{ border-radius: 6px; }}
QFrame#StepHead {{ border-top-left-radius: 5px; border-top-right-radius: 5px; }}
QFrame#WayCard {{ border-radius: 6px; }}
QTabWidget#MainTabs QTabBar::tab {{ padding: 8px 18px 7px 18px; }}
"""
    if t.get("contrast"):
        css += f"""
QWidget {{ selection-color: {t['ON_ACCENT']}; }}
QPushButton {{ border: 1px solid {t['TEXT']}; }}
QPushButton:hover {{ border: 2px solid {t['ACCENT']}; }}
QTreeWidget::item:selected, QTableWidget::item:selected, QListWidget::item:selected {{ background: {t['ACCENT']}; color: {t['ON_ACCENT']}; }}
QMenu::item:selected, QMenuBar::item:selected {{ background: {t['ACCENT']}; color: {t['ON_ACCENT']}; }}
QTabWidget#MainTabs QTabBar::tab:selected {{ border-bottom: 3px solid {t['ACCENT']}; }}
QFrame#StepHead QLabel#StepBadge {{ background: {t['ACCENT']}; color: {t['ON_ACCENT']}; }}
"""
    return css


def apply_tokens(theme_mod, t: dict) -> None:
    """Point the old theme module's constants at the scheme, so every
    ``theme.X`` read at draw time follows it."""
    for key in ("BG", "BG_ALT", "BG_RAISED", "BORDER", "TEXT", "TEXT_DIM",
                "ACCENT", "ACCENT_DIM", "CHANGED", "WARN", "ERROR", "RULE"):
        setattr(theme_mod, key, t[key])


def base_with_tokens(theme_mod, t: dict, px: int) -> str:
    """The old stylesheet with its six hard-coded colours swapped for tokens."""
    css = theme_mod.stylesheet(px)
    for old, new in (("#22262e", t["ALT_ROW"]), ("#efb75f", t["ACCENT_HOVER"]),
                     ("#201a10", t["ON_ACCENT"]), ("#4d5563", t["DISABLED"]),
                     ("#3a424f", t["SCROLL"]), ("#4b5566", t["SCROLL_HOVER"])):
        css = css.replace(old, new)
    return css


def palette(t: dict):
    """The application palette from the scheme, so Fusion's own drawing
    (item-view checkboxes, focus rects) follows it too."""
    from PySide6.QtGui import QColor, QPalette

    pal = QPalette()
    role = QPalette.ColorRole
    for r, key in ((role.Window, "BG"), (role.Base, "BG_ALT"),
                   (role.AlternateBase, "ALT_ROW"), (role.Button, "BG_RAISED"),
                   (role.Text, "TEXT"), (role.WindowText, "TEXT"),
                   (role.ButtonText, "TEXT"), (role.Highlight, "ACCENT_DIM"),
                   (role.HighlightedText, "TEXT"), (role.ToolTipBase, "BG_RAISED"),
                   (role.ToolTipText, "TEXT"), (role.PlaceholderText, "TEXT_DIM"),
                   (role.Link, "ACCENT_TEXT"), (role.Mid, "BORDER"),
                   (role.Dark, "BORDER"), (role.Light, "BG_RAISED"),
                   (role.Midlight, "BG_RAISED")):
        pal.setColor(r, QColor(t[key]))
    if t.get("contrast"):
        pal.setColor(role.HighlightedText, QColor(t["ON_ACCENT"]))
    return pal
