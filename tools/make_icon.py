"""Draw the application icon and write a multi-size .ico.

A gold compass rose on a dark plaque: nautical enough to sit next to Anno on a
taskbar, deliberately unlike either game's own artwork so nobody mistakes it for
the game itself. The small `< >` marks read as "this edits XML" at large sizes
and simply vanish into the rose at 16px.

    python tools/make_icon.py
"""

from __future__ import annotations

import math
import os
import struct
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QBuffer, QByteArray, QPointF, QRectF, Qt
from PySide6.QtGui import (
    QBrush,
    QColor,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
    QRadialGradient,
)
from PySide6.QtWidgets import QApplication

SIZES = (16, 20, 24, 32, 48, 64, 128, 256)

PLAQUE_TOP = "#2c333f"
PLAQUE_BOTTOM = "#12151a"
GOLD = "#e0a44c"
GOLD_DARK = "#a8752c"
GOLD_LIGHT = "#f6d08a"


def _rose(painter: QPainter, cx: float, cy: float, radius: float, points: int, ratio: float):
    """One compass rose: `points` spikes, each a kite from the centre."""
    waist = radius * ratio
    step = 2 * math.pi / points
    for i in range(points):
        angle = i * step - math.pi / 2
        tip = QPointF(cx + radius * math.cos(angle), cy + radius * math.sin(angle))
        left = QPointF(
            cx + waist * math.cos(angle - step / 2), cy + waist * math.sin(angle - step / 2)
        )
        right = QPointF(
            cx + waist * math.cos(angle + step / 2), cy + waist * math.sin(angle + step / 2)
        )
        centre = QPointF(cx, cy)

        # Two halves, lit differently, so the rose reads as faceted metal.
        for half, shade in ((left, GOLD_LIGHT), (right, GOLD_DARK)):
            path = QPainterPath(centre)
            path.lineTo(half)
            path.lineTo(tip)
            path.closeSubpath()
            painter.setBrush(QBrush(QColor(shade)))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawPath(path)


def render(size: int) -> QPixmap:
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)

    s = size / 256.0  # everything below is authored at 256px
    inset = 8 * s
    body = QRectF(inset, inset, size - 2 * inset, size - 2 * inset)

    plaque = QLinearGradient(0, inset, 0, size - inset)
    plaque.setColorAt(0.0, QColor(PLAQUE_TOP))
    plaque.setColorAt(1.0, QColor(PLAQUE_BOTTOM))
    painter.setBrush(QBrush(plaque))
    painter.setPen(QPen(QColor(GOLD_DARK), max(1.0, 6 * s)))
    painter.drawRoundedRect(body, 48 * s, 48 * s)

    cx = cy = size / 2.0

    # Below ~32px the bezel just crowds the rose into an illegible dot, so small
    # renditions drop it and let the rose fill the plaque instead.
    detailed = size >= 32
    outer = 90 * s if detailed else 112 * s

    if detailed:
        bezel = 112 * s
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(QColor(GOLD_DARK), 5 * s))
        painter.drawEllipse(QPointF(cx, cy), bezel, bezel)
        painter.setPen(
            QPen(QColor(GOLD_DARK), 5 * s, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
        )
        for i in range(24):
            angle = i * math.pi / 12
            inner = bezel - (14 * s if i % 3 == 0 else 8 * s)
            painter.drawLine(
                QPointF(cx + inner * math.cos(angle), cy + inner * math.sin(angle)),
                QPointF(cx + bezel * math.cos(angle), cy + bezel * math.sin(angle)),
            )

    if detailed:
        _rose(painter, cx, cy, outer * 0.58, 8, 0.30)  # short diagonal spikes
        _rose(painter, cx, cy, outer, 4, 0.16)  # long cardinal spikes
    else:
        # A single fat 4-point star survives downsampling; the 8-point layer
        # underneath would only turn to mush.
        _rose(painter, cx, cy, outer, 4, 0.30)

    # Centre boss.
    boss_r = 18 * s if detailed else 26 * s
    boss = QRadialGradient(cx - boss_r / 4, cy - boss_r / 4, boss_r * 1.2)
    boss.setColorAt(0.0, QColor(GOLD_LIGHT))
    boss.setColorAt(1.0, QColor(GOLD))
    painter.setBrush(QBrush(boss))
    painter.setPen(QPen(QColor(PLAQUE_BOTTOM), max(1.0, 3 * s)))
    painter.drawEllipse(QPointF(cx, cy), boss_r, boss_r)

    painter.end()
    return pixmap


def _png_bytes(pixmap: QPixmap) -> bytes:
    # The QByteArray must outlive the QBuffer that wraps it.
    store = QByteArray()
    buffer = QBuffer(store)
    buffer.open(QBuffer.OpenModeFlag.WriteOnly)
    pixmap.save(buffer, "PNG")
    buffer.close()
    return bytes(store)


def write_ico(path: Path, sizes=SIZES) -> None:
    """Assemble a PNG-in-ICO container (supported by Windows Vista onwards)."""
    images = [(size, _png_bytes(render(size))) for size in sizes]
    header = struct.pack("<HHH", 0, 1, len(images))
    offset = len(header) + 16 * len(images)

    entries, blobs = b"", b""
    for size, blob in images:
        entries += struct.pack(
            "<BBBBHHII",
            size if size < 256 else 0,
            size if size < 256 else 0,
            0,
            0,
            1,
            32,
            len(blob),
            offset,
        )
        blobs += blob
        offset += len(blob)

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(header + entries + blobs)


def main(argv):
    app = QApplication.instance() or QApplication([])
    root = Path(__file__).resolve().parents[1]
    out_dir = Path(argv[1]) if len(argv) > 1 else root / "anno_modding_studio" / "resources"
    out_dir.mkdir(parents=True, exist_ok=True)

    write_ico(out_dir / "icon.ico")
    render(256).save(str(out_dir / "icon.png"), "PNG")
    print("wrote %s" % (out_dir / "icon.ico"))
    print("wrote %s" % (out_dir / "icon.png"))
    del app
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
