"""Visual layer for the desktop app: dark "aurora glass" theme, painted background and the status orb.

Qt widgets can't blur what is behind them, so the glass look is built from a painted aurora background
plus translucent, softly bordered cards drawn on top of it.
"""

from __future__ import annotations

import math

from PySide6.QtCore import Property, QEasingCurve, QPointF, QPropertyAnimation, QRectF, Qt, QTimer
from PySide6.QtGui import QBrush, QColor, QLinearGradient, QPainter, QPainterPath, QPen, QRadialGradient
from PySide6.QtWidgets import QWidget

NIGHT = "#0E1024"
INK = "#E8E9F6"
MUTED = "#9A9CC0"
TEAL = "#4FF0C4"
VIOLET = "#8B7BFF"
ROSE = "#FF8FB1"

# Status orb palettes: (core, halo-a, halo-b, breathing period in seconds)
ORB_STATES = {
    "idle": (VIOLET, "#5A4BD6", "#2E2A6E", 6.0),
    "watching": (TEAL, "#2FB8C9", VIOLET, 3.2),
    "working": (ROSE, "#FFB86B", VIOLET, 1.1),
    "error": ("#FF5D73", "#B0304A", "#3A1630", 2.0),
}

STYLESHEET = f"""
* {{ font-family: "Segoe UI Variable", "Segoe UI", "SF Pro Text", "Inter", "Noto Sans", sans-serif; font-size: 13.5px; color: {INK}; }}
QWidget#root {{ background: transparent; }}
QLabel#title {{ font-size: 26px; font-weight: 700; letter-spacing: -0.5px; }}
QLabel#subtitle {{ color: {MUTED}; font-size: 13px; }}
QLabel#status {{ color: {TEAL}; font-weight: 600; }}
QLabel.section {{ color: {MUTED}; font-size: 12px; }}

QFrame#card {{
  background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 rgba(255,255,255,0.10), stop:1 rgba(255,255,255,0.035));
  border: 1px solid rgba(255,255,255,0.13); border-radius: 18px;
}}

QPushButton {{
  background: rgba(255,255,255,0.08); border: 1px solid rgba(255,255,255,0.16); border-radius: 17px;
  padding: 8px 18px; font-weight: 600;
}}
QPushButton:hover {{ background: rgba(255,255,255,0.14); border-color: rgba(79,240,196,0.6); }}
QPushButton:pressed {{ background: rgba(255,255,255,0.05); }}
QPushButton:disabled {{ color: rgba(232,233,246,0.35); border-color: rgba(255,255,255,0.06); background: rgba(255,255,255,0.03); }}
QPushButton:focus {{ border: 1px solid {TEAL}; }}
QPushButton#primary {{
  border: none; color: #07131A;
  background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 {TEAL}, stop:1 #7FD8FF);
}}
QPushButton#primary:hover {{ background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #7CF7D6, stop:1 #A6E6FF); }}
QPushButton#primary:checked {{
  color: {INK}; background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #FF7A9A, stop:1 {VIOLET});
}}
QPushButton#primary:disabled {{ background: rgba(79,240,196,0.18); color: rgba(7,19,26,0.5); }}

QComboBox {{
  background: rgba(0,0,0,0.25); border: 1px solid rgba(255,255,255,0.14); border-radius: 10px; padding: 6px 12px; min-width: 120px;
}}
QComboBox:hover, QComboBox:focus {{ border-color: {TEAL}; }}
QComboBox::drop-down {{ border: none; width: 22px; }}
QComboBox QAbstractItemView {{
  background: #1A1C3A; border: 1px solid rgba(255,255,255,0.16); border-radius: 10px; selection-background-color: rgba(79,240,196,0.25);
  padding: 4px; outline: none;
}}
QCheckBox {{ spacing: 8px; }}
QCheckBox::indicator {{ width: 18px; height: 18px; border-radius: 6px; border: 1px solid rgba(255,255,255,0.3); background: rgba(0,0,0,0.25); }}
QCheckBox::indicator:checked {{ background: {TEAL}; border-color: {TEAL}; }}

QPlainTextEdit, QTextEdit {{
  background: rgba(0,0,0,0.28); border: 1px solid rgba(255,255,255,0.08); border-radius: 12px; padding: 10px;
  font-family: "Cascadia Code", "JetBrains Mono", "Consolas", monospace; font-size: 12.5px; selection-background-color: rgba(139,123,255,0.45);
}}
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 4px; }}
QScrollBar::handle:vertical {{ background: rgba(255,255,255,0.18); border-radius: 4px; min-height: 30px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; }}
QDialog {{ background: {NIGHT}; }}
QToolTip {{ background: #1A1C3A; color: {INK}; border: 1px solid rgba(255,255,255,0.2); border-radius: 8px; padding: 6px; }}
"""


def paint_aurora(widget: QWidget, phase: float = 0.0) -> None:
    """Paint the night background with three slow aurora lights (call from paintEvent)."""
    p = QPainter(widget)
    p.setRenderHint(QPainter.Antialiasing)
    r = widget.rect()
    base = QLinearGradient(0, 0, 0, r.height())
    base.setColorAt(0, QColor("#141738"))
    base.setColorAt(1, QColor(NIGHT))
    p.fillRect(r, base)
    w, h = r.width(), r.height()
    lights = [
        (0.82 + 0.03 * math.sin(phase), -0.05, 0.75, QColor(79, 240, 196, 55)),
        (0.05, 1.05 + 0.03 * math.cos(phase * 0.8), 0.8, QColor(139, 123, 255, 60)),
        (0.55, 0.55 + 0.04 * math.sin(phase * 0.6), 0.55, QColor(255, 143, 177, 22)),
    ]
    for fx, fy, fr, color in lights:
        g = QRadialGradient(QPointF(fx * w, fy * h), fr * max(w, h))
        g.setColorAt(0, color)
        transparent = QColor(color)
        transparent.setAlpha(0)
        g.setColorAt(1, transparent)
        p.fillRect(r, QBrush(g))
    p.end()


class AuraOrb(QWidget):
    """A breathing, slowly swirling orb whose colours show what Aura is doing."""

    def __init__(self, size: int = 64, parent: QWidget | None = None):
        super().__init__(parent)
        self.setFixedSize(size, size)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self._state = "idle"
        self._t = 0.0
        self._mix = 1.0
        self._from = ORB_STATES["idle"]
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(33)
        self._anim = QPropertyAnimation(self, b"mix", self)
        self._anim.setDuration(600)
        self._anim.setEasingCurve(QEasingCurve.InOutCubic)
        self.setToolTip("Idle")

    def _tick(self) -> None:
        self._t += 0.033
        self.update()

    def get_mix(self) -> float:
        return self._mix

    def set_mix(self, value: float) -> None:
        self._mix = value
        self.update()

    mix = Property(float, get_mix, set_mix)

    @property
    def state(self) -> str:
        return self._state

    def set_state(self, state: str) -> None:
        if state == self._state or state not in ORB_STATES:
            return
        self._from = self._palette()
        self._state = state
        self.setToolTip(state.capitalize())
        self._anim.stop()
        self._anim.setStartValue(0.0)
        self._anim.setEndValue(1.0)
        self._anim.start()

    def _palette(self):
        target = ORB_STATES[self._state]
        if self._mix >= 1:
            return target

        def lerp(a: str, b: str) -> str:
            ca, cb = QColor(a), QColor(b)
            k = self._mix
            return QColor(
                int(ca.red() + (cb.red() - ca.red()) * k),
                int(ca.green() + (cb.green() - ca.green()) * k),
                int(ca.blue() + (cb.blue() - ca.blue()) * k),
            ).name()

        return tuple(lerp(a, b) for a, b in zip(self._from[:3], target[:3])) + (target[3],)

    def paintEvent(self, _event) -> None:  # noqa: N802 (Qt API)
        core, halo_a, halo_b, period = self._palette()
        breath = 0.5 + 0.5 * math.sin(self._t * 2 * math.pi / period)
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        s = self.width()
        c = QPointF(s / 2, s / 2)

        halo = QRadialGradient(c, s / 2)
        hc = QColor(halo_a)
        hc.setAlpha(int(70 + 60 * breath))
        halo.setColorAt(0.35, hc)
        edge = QColor(halo_b)
        edge.setAlpha(0)
        halo.setColorAt(1.0, edge)
        p.setBrush(QBrush(halo))
        p.setPen(Qt.NoPen)
        p.drawEllipse(c, s / 2, s / 2)

        radius = s * (0.27 + 0.03 * breath)
        path = QPainterPath()
        path.addEllipse(c, radius, radius)
        p.setClipPath(path)
        body = QRadialGradient(QPointF(c.x() - radius * 0.35, c.y() - radius * 0.4), radius * 1.6)
        body.setColorAt(0, QColor("#FFFFFF"))
        body.setColorAt(0.25, QColor(core))
        body.setColorAt(1, QColor(halo_b))
        p.fillRect(QRectF(0, 0, s, s), QBrush(body))
        # two swirling bands give the orb its "aura" motion
        for k, col in ((0, halo_a), (1, halo_b)):
            ang = self._t * (0.9 + k * 0.5) + k * math.pi
            off = QPointF(math.cos(ang) * radius * 0.5, math.sin(ang) * radius * 0.5)
            g = QRadialGradient(c + off, radius * 0.9)
            band = QColor(col)
            band.setAlpha(150)
            g.setColorAt(0, band)
            band.setAlpha(0)
            g.setColorAt(1, band)
            p.fillRect(QRectF(0, 0, s, s), QBrush(g))
        p.setClipping(False)
        p.setPen(QPen(QColor(255, 255, 255, 90), 1))
        p.setBrush(Qt.NoBrush)
        p.drawEllipse(c, radius, radius)
        p.end()
