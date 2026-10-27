"""Check every approved scheme against the proposal's contrast floors.

    python docs/design-review/mockup/contrast.py [scheme ...]

Text pairs need 4.5:1 (7:1 in a high-contrast scheme); boundaries, focus and
scrollbar handles need 3:1. Marble's gold accent fill on white is the one
known exception: a filled button is identified by its text, and focus and
checked edges use the scheme's FOCUS colour instead. Stage 0 turns this into
``tests/test_design.py``.
"""

from __future__ import annotations

import colorsys
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from schemes import REJECTED, SCHEMES

SURFACES = ("BG", "BG_ALT", "BG_RAISED")
TEXT_PAIRS = (
    [("TEXT", s) for s in SURFACES]
    + [("TEXT_DIM", s) for s in SURFACES]
    + [(k, "BG_ALT") for k in ("ACCENT_TEXT", "CHANGED", "WARN", "ERROR", "RULE",
                               "SYN_COMMENT", "SYN_TAG", "SYN_VALUE")]
    + [(k, "BG") for k in ("ACCENT_TEXT", "CHANGED", "WARN", "ERROR", "RULE")]
    + [("ON_ACCENT", "ACCENT"), ("TEXT", "ACCENT_DIM")]
)
BOUNDARY_PAIRS = (
    [("FIELD_BORDER", s) for s in SURFACES]
    + [("FOCUS", s) for s in SURFACES]
    + [("SCROLL", "BG")]
)
ALLOWED = {("marble", "ACCENT")}


def luminance(colour: str) -> float:
    channels = [int(colour[i:i + 2], 16) / 255 for i in (1, 3, 5)]
    linear = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
              for c in channels]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def ratio(a: str, b: str) -> float:
    hi, lo = sorted((luminance(a), luminance(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def hue(colour: str) -> int:
    r, g, b = (int(colour[i:i + 2], 16) / 255 for i in (1, 3, 5))
    return round(colorsys.rgb_to_hls(r, g, b)[0] * 360)


def failures(name: str, t: dict) -> list:
    found = []
    floor = 7 if t.get("contrast") else 4.5
    for a, b in TEXT_PAIRS:
        if t.get("contrast") and (a, b) == ("TEXT", "ACCENT_DIM"):
            a = "ON_ACCENT"  # selected text is drawn on the accent
        if ratio(t[a], t[b]) < floor:
            found.append("%s on %s %.2f" % (a, b, ratio(t[a], t[b])))
    for a, b in BOUNDARY_PAIRS:
        if (name, a) not in ALLOWED and ratio(t[a], t[b]) < 3:
            found.append("%s on %s %.2f" % (a, b, ratio(t[a], t[b])))
    return found


def main(argv=None) -> int:
    names = list(argv if argv is not None else sys.argv[1:]) or list(SCHEMES)
    bad = 0
    for name in names:
        t = SCHEMES.get(name) or REJECTED[name]
        found = failures(name, t)
        bad += bool(found)
        hues = {k: hue(t[k]) for k in ("BG_RAISED", "ACCENT", "WARN", "ERROR",
                                       "CHANGED", "RULE")}
        print("%-14s %s" % (name, "; ".join(found) or "passes"))
        print("%-14s hues %s" % ("", hues))
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
