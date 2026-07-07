"""PyQt6 GUI for osu-beatmap-fetcher — card-based UI with thumbnails.

Visual overhaul notes
----------------------
The behaviour (search / download / cancel flow, threading, thumbnails,
selection) is unchanged from the working version — this is a design pass. The
main visual ideas:

* All glyphs are drawn with QPainter (see ``icon_pixmap`` / the ``_draw_*``
  functions) so nothing depends on the host's emoji font. Stars, BPM notes,
  clocks, key icons, checks/crosses and the app logo all render identically on
  every OS.
* Mode / Keys are segmented "pill" toggles (``SegmentedControl``) instead of
  dropdowns — small fixed choice sets read better as buttons.
* Range filters (stars / BPM / length / PP) are unified "min — max" controls
  (``RangeField``) that look like a single input, not four loose boxes.
* The star range carries a live difficulty-gradient bar as a visual cue.
* Result cards get download-status stamps (✓ / ✗ / already-have) driven by the
  DownloadWorker signals.
"""

import math
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path

from PyQt6.QtCore import (
    Qt, QThread, pyqtSignal, QSize, QRect, QRectF, QPointF, QUrl, QEvent,
    QSettings,
)
from PyQt6.QtGui import (
    QFont, QColor, QPixmap, QPainter, QPainterPath, QPolygonF,
    QLinearGradient, QPen, QFontMetrics,
)
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QGridLayout, QLabel, QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox,
    QPushButton, QProgressBar, QMessageBox, QFrame, QScrollArea, QCheckBox,
    QButtonGroup, QFileDialog, QSplitter, QSizePolicy,
)
from PyQt6.QtNetwork import QNetworkAccessManager, QNetworkRequest, QNetworkReply

from src.client import OsuClient, BeatmapsetHit
from src.download import (
    download_beatmapset, download_beatmapsets_parallel, DownloadResult,
    DownloadError, DEFAULT_DOWNLOAD_DIR, bundle_osz, bundle_osz_split,
    cleanup_broken_osz,
)
from src.registry import Registry
from src.search import sweep_search
from src.pp import filter_by_pp

# ── Palette ──────────────────────────────────────────────────────────

BG_BASE = "#17171f"
BG_SURFACE = "#1e1e2b"
BG_CARD = "#242435"
BG_CARD_HOVER = "#2b2b42"
BG_CARD_SELECTED = "#2a2a52"
BG_CARD_SELECTED_HOVER = "#31315e"
BG_INPUT = "#2a2a40"
BG_INPUT_TRACK = "#232336"
BG_HEADER = "#141419"

ACCENT_PINK = "#FF66AB"
ACCENT_PINK_HOVER = "#FF88C0"
ACCENT_PINK_PRESSED = "#E0508A"
ACCENT_PURPLE = "#B48EF0"
ACCENT_PURPLE_HOVER = "#C4A4FF"
ACCENT_BLUE = "#66AAFF"

TEXT_PRIMARY = "#EEEEF0"
TEXT_SECONDARY = "#B0B0C8"
TEXT_DIM = "#707088"
TEXT_FAINT = "#565670"

BORDER_SUBTLE = "#33334a"
BORDER_FOCUS = ACCENT_PINK

GREEN_OK = "#67D08A"
RED_ERR = "#FF7080"

# Badge backgrounds / text
BADGE_STAR_BG = "#26242f"
BADGE_BPM = "#332d55"
BADGE_BPM_TEXT = "#B7A6F5"
BADGE_LEN = "#243642"
BADGE_LEN_TEXT = "#86CEEC"
BADGE_KEYS = "#3a2740"
BADGE_KEYS_TEXT = "#EC90D2"

STATUS_OK_BG = "#1f3129"
STATUS_ERR_BG = "#361f27"
STATUS_DUPE_BG = "#212a3d"


# ── Drawn icons (no emoji — QPainter for cross-platform consistency) ──

_icon_cache: dict = {}


def _star_path(s: float) -> QPainterPath:
    """A filled 5-point star path sized to an ``s``×``s`` box."""
    path = QPainterPath()
    cx = cy = s / 2
    outer = s * 0.46
    inner = outer * 0.42
    for i in range(10):
        r = outer if i % 2 == 0 else inner
        ang = -math.pi / 2 + i * math.pi / 5
        pt = QPointF(cx + r * math.cos(ang), cy + r * math.sin(ang))
        path.moveTo(pt) if i == 0 else path.lineTo(pt)
    path.closeSubpath()
    return path


def _draw_star(p: QPainter, s: float, c: QColor):
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(c)
    p.drawPath(_star_path(s))


def _draw_note(p: QPainter, s: float, c: QColor):
    # Eighth note: filled head bottom-left, stem up-right, small flag.
    pen = QPen(c, s * 0.09)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    p.setPen(pen)
    p.drawLine(QPointF(s * 0.62, s * 0.24), QPointF(s * 0.62, s * 0.66))
    flag = QPainterPath()
    flag.moveTo(QPointF(s * 0.62, s * 0.24))
    flag.cubicTo(QPointF(s * 0.9, s * 0.26), QPointF(s * 0.86, s * 0.46),
                 QPointF(s * 0.66, s * 0.44))
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawPath(flag)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(c)
    p.save()
    p.translate(s * 0.4, s * 0.68)
    p.rotate(-20)
    p.drawEllipse(QRectF(-s * 0.17, -s * 0.12, s * 0.34, s * 0.24))
    p.restore()


def _draw_clock(p: QPainter, s: float, c: QColor):
    pen = QPen(c, s * 0.09)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    p.setPen(pen)
    p.setBrush(Qt.BrushStyle.NoBrush)
    m = s * 0.15
    p.drawEllipse(QRectF(m, m, s - 2 * m, s - 2 * m))
    cx = cy = s / 2
    p.drawLine(QPointF(cx, cy), QPointF(cx, cy - s * 0.22))
    p.drawLine(QPointF(cx, cy), QPointF(cx + s * 0.16, cy))


def _draw_keys(p: QPainter, s: float, c: QColor):
    pen = QPen(c, s * 0.08)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    p.setPen(pen)
    p.setBrush(Qt.BrushStyle.NoBrush)
    rect = QRectF(s * 0.16, s * 0.30, s * 0.68, s * 0.40)
    p.drawRoundedRect(rect, s * 0.06, s * 0.06)
    for fx in (0.39, 0.5, 0.61):
        x = s * fx
        p.drawLine(QPointF(x, s * 0.30), QPointF(x, s * 0.70))


def _draw_check(p: QPainter, s: float, c: QColor):
    pen = QPen(c, s * 0.14)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    p.setPen(pen)
    poly = QPolygonF([QPointF(s * 0.22, s * 0.52), QPointF(s * 0.42, s * 0.72),
                      QPointF(s * 0.78, s * 0.30)])
    p.drawPolyline(poly)


def _draw_cross(p: QPainter, s: float, c: QColor):
    pen = QPen(c, s * 0.14)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    p.setPen(pen)
    p.drawLine(QPointF(s * 0.28, s * 0.28), QPointF(s * 0.72, s * 0.72))
    p.drawLine(QPointF(s * 0.72, s * 0.28), QPointF(s * 0.28, s * 0.72))


def _draw_search(p: QPainter, s: float, c: QColor):
    pen = QPen(c, s * 0.10)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    p.setPen(pen)
    p.setBrush(Qt.BrushStyle.NoBrush)
    r = s * 0.28
    p.drawEllipse(QRectF(s * 0.40 - r, s * 0.40 - r, 2 * r, 2 * r))
    p.drawLine(QPointF(s * 0.61, s * 0.61), QPointF(s * 0.82, s * 0.82))


def _draw_chevron(p: QPainter, s: float, c: QColor):
    pen = QPen(c, s * 0.12)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    p.setPen(pen)
    poly = QPolygonF([QPointF(s * 0.28, s * 0.40), QPointF(s * 0.5, s * 0.62),
                      QPointF(s * 0.72, s * 0.40)])
    p.drawPolyline(poly)


def _draw_logo(p: QPainter, s: float, c: QColor):
    # osu!-style ring: thick outer circle + filled inner dot.
    pen = QPen(c, s * 0.12)
    p.setPen(pen)
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawEllipse(QRectF(s * 0.16, s * 0.16, s * 0.68, s * 0.68))
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(c)
    p.drawEllipse(QRectF(s * 0.5 - s * 0.13, s * 0.5 - s * 0.13, s * 0.26, s * 0.26))


_ICON_DRAWERS = {
    "star": _draw_star,
    "note": _draw_note,
    "clock": _draw_clock,
    "keys": _draw_keys,
    "check": _draw_check,
    "cross": _draw_cross,
    "search": _draw_search,
    "chevron": _draw_chevron,
    "logo": _draw_logo,
}


def icon_pixmap(name: str, size: int, color: str) -> QPixmap:
    """Return a cached QPainter-drawn icon pixmap (crisp on HiDPI)."""
    key = (name, size, color)
    if key in _icon_cache:
        return _icon_cache[key]

    dpr = 2  # render at 2x for retina crispness, then tag the ratio
    px = QPixmap(size * dpr, size * dpr)
    px.fill(Qt.GlobalColor.transparent)
    p = QPainter(px)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    _ICON_DRAWERS[name](p, size * dpr, QColor(color))
    p.end()
    px.setDevicePixelRatio(dpr)
    _icon_cache[key] = px
    return px


# ── Helpers ──────────────────────────────────────────────────────────

def format_length(seconds: int) -> str:
    m, s = divmod(seconds, 60)
    return f"{m}:{s:02d}"


def fmt_duration(seconds: float) -> str:
    """Human-friendly duration, e.g. '45s', '3m 20s', '1h 05m'."""
    seconds = int(max(0, seconds))
    if seconds < 60:
        return f"{seconds}s"
    m, s = divmod(seconds, 60)
    if m < 60:
        return f"{m}m {s:02d}s"
    h, m = divmod(m, 60)
    return f"{h}h {m:02d}m"


def fmt_speed(bytes_per_s: float) -> str:
    """Human-friendly transfer rate, e.g. '4.2 MB/s' or '780 KB/s'."""
    mb = bytes_per_s / (1024 * 1024)
    if mb >= 1:
        return f"{mb:.1f} MB/s"
    return f"{bytes_per_s / 1024:.0f} KB/s"


def star_color(stars: float) -> str:
    if stars < 2:
        return "#88CC88"
    if stars < 3:
        return "#AADD66"
    if stars < 4:
        return "#FFCC22"
    if stars < 5:
        return "#FF8844"
    if stars < 6:
        return "#FF6688"
    if stars < 7:
        return "#CC66FF"
    return "#8888FF"


def make_rounded_pixmap(pixmap: QPixmap, radius: int = 8) -> QPixmap:
    size = pixmap.size()
    rounded = QPixmap(size)
    rounded.fill(Qt.GlobalColor.transparent)
    painter = QPainter(rounded)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    path = QPainterPath()
    path.addRoundedRect(0, 0, size.width(), size.height(), radius, radius)
    painter.setClipPath(path)
    painter.drawPixmap(0, 0, pixmap)
    painter.end()
    return rounded


def _repolish(w: QWidget):
    """Re-run the stylesheet for a widget after a dynamic property change."""
    w.style().unpolish(w)
    w.style().polish(w)
    w.update()


def _clear_layout(layout):
    while layout.count():
        item = layout.takeAt(0)
        if item.widget():
            item.widget().deleteLater()


def _qss_asset(name: str, color: str, size: int = 14) -> str:
    """Render an icon to a PNG on disk and return a QSS-friendly url path.

    Qt stylesheets can only point ``image:`` at a file, so widgets that need a
    themed glyph baked into their QSS (combo-box arrow) get one written to a
    temp cache dir at startup.
    """
    d = Path(tempfile.gettempdir()) / "osu_fetcher_assets"
    d.mkdir(exist_ok=True)
    path = d / f"{name}_{color.lstrip('#')}.png"
    px = QPixmap(size * 2, size * 2)
    px.fill(Qt.GlobalColor.transparent)
    p = QPainter(px)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    _ICON_DRAWERS[name](p, size * 2, QColor(color))
    p.end()
    px.save(str(path))
    return str(path).replace("\\", "/")


# ── Stylesheet ───────────────────────────────────────────────────────

def build_stylesheet(chevron_url: str) -> str:
    return f"""
QMainWindow {{
    background-color: {BG_BASE};
}}
QWidget {{
    color: {TEXT_PRIMARY};
    font-family: 'Segoe UI', 'Noto Sans', 'Helvetica Neue', sans-serif;
    font-size: 13px;
}}
QLabel {{
    color: {TEXT_SECONDARY};
    background: transparent;
}}

/* Filter surface */
QFrame#filterPanel {{
    background-color: {BG_SURFACE};
    border: 1px solid {BORDER_SUBTLE};
    border-radius: 14px;
}}

/* Plain inputs (status combo, count, keyword) */
QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox {{
    background-color: {BG_INPUT};
    color: {TEXT_PRIMARY};
    border: 1px solid {BORDER_SUBTLE};
    border-radius: 8px;
    padding: 6px 10px;
    font-size: 13px;
    min-height: 22px;
    selection-background-color: {ACCENT_PURPLE};
}}
QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus {{
    border-color: {ACCENT_PINK};
}}
QLineEdit::placeholder {{
    color: {TEXT_FAINT};
}}
QSpinBox::up-button, QSpinBox::down-button,
QDoubleSpinBox::up-button, QDoubleSpinBox::down-button {{
    width: 0; border: none;
}}
QComboBox::drop-down {{
    border: none;
    width: 26px;
}}
QComboBox::down-arrow {{
    image: url({chevron_url});
    width: 12px;
    height: 12px;
}}
QComboBox QAbstractItemView {{
    background-color: {BG_INPUT};
    color: {TEXT_PRIMARY};
    selection-background-color: {ACCENT_PURPLE};
    border: 1px solid {BORDER_SUBTLE};
    border-radius: 8px;
    padding: 4px;
    outline: none;
}}

/* Unified range field wrapper */
QFrame#rangeField {{
    background-color: {BG_INPUT};
    border: 1px solid {BORDER_SUBTLE};
    border-radius: 8px;
}}
QFrame#rangeField[focused="true"] {{
    border-color: {ACCENT_PINK};
}}
QLineEdit#rangeInner, QDoubleSpinBox#rangeInner, QSpinBox#rangeInner {{
    background: transparent;
    border: none;
    border-radius: 0;
    padding: 6px 10px;
    min-height: 22px;
}}
QLineEdit#rangeInner:focus, QDoubleSpinBox#rangeInner:focus {{
    border: none;
}}

/* Buttons */
QPushButton {{
    border: none;
    border-radius: 9px;
    padding: 9px 22px;
    font-weight: 600;
    font-size: 13px;
}}
QPushButton#searchBtn {{
    background-color: {ACCENT_PINK};
    color: white;
}}
QPushButton#searchBtn:hover {{ background-color: {ACCENT_PINK_HOVER}; }}
QPushButton#searchBtn:pressed {{ background-color: {ACCENT_PINK_PRESSED}; }}
QPushButton#downloadBtn {{
    background-color: {ACCENT_PURPLE};
    color: white;
}}
QPushButton#downloadBtn:hover {{ background-color: {ACCENT_PURPLE_HOVER}; }}
QPushButton#cancelBtn {{
    background-color: transparent;
    color: {RED_ERR};
    border: 1px solid #4a3038;
}}
QPushButton#cancelBtn:hover {{ background-color: #2e2027; }}
QPushButton#selectAllBtn, QPushButton#selectNoneBtn {{
    background-color: transparent;
    color: {TEXT_DIM};
    padding: 5px 12px;
    font-size: 11px;
    font-weight: 600;
    border: 1px solid {BORDER_SUBTLE};
    border-radius: 7px;
}}
QPushButton#selectAllBtn:hover, QPushButton#selectNoneBtn:hover {{
    color: {TEXT_PRIMARY};
    border-color: {TEXT_DIM};
}}
QPushButton#browseBtn {{
    background-color: {BG_INPUT};
    color: {TEXT_SECONDARY};
    padding: 7px 16px;
    font-size: 12px;
    font-weight: 600;
    border: 1px solid {BORDER_SUBTLE};
    border-radius: 8px;
}}
QPushButton#browseBtn:hover {{
    color: {TEXT_PRIMARY};
    border-color: {ACCENT_PURPLE};
}}
QPushButton:disabled {{
    background-color: #26263a;
    color: {TEXT_FAINT};
    border-color: transparent;
}}

/* Progress */
QProgressBar {{
    background-color: {BG_INPUT_TRACK};
    border: none;
    border-radius: 6px;
    text-align: center;
    color: {TEXT_DIM};
    font-size: 11px;
    min-height: 8px;
    max-height: 8px;
}}
QProgressBar::chunk {{
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
        stop:0 {ACCENT_PINK}, stop:1 {ACCENT_PURPLE});
    border-radius: 6px;
}}

/* Scroll */
QScrollArea {{ border: none; background-color: transparent; }}
QScrollBar:vertical {{
    background-color: transparent;
    width: 10px;
    border: none;
    margin: 2px;
}}
QScrollBar::handle:vertical {{
    background-color: {BORDER_SUBTLE};
    border-radius: 4px;
    min-height: 36px;
}}
QScrollBar::handle:vertical:hover {{ background-color: {TEXT_DIM}; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: none; }}

QStatusBar {{
    background-color: {BG_HEADER};
    color: {TEXT_DIM};
    font-size: 11px;
    border-top: 1px solid {BORDER_SUBTLE};
}}
QToolTip {{
    background-color: {BG_HEADER};
    color: {TEXT_PRIMARY};
    border: 1px solid {BORDER_SUBTLE};
    border-radius: 6px;
    padding: 4px 8px;
}}
"""


# ── Reusable widgets ─────────────────────────────────────────────────

class SegmentedControl(QWidget):
    """A row of mutually-exclusive pill buttons — a prettier combo box.

    Exposes ``currentText`` / ``currentTextChanged`` / ``setCurrentIndex`` so it
    is a drop-in for the QComboBoxes it replaces.
    """
    currentTextChanged = pyqtSignal(str)

    def __init__(self, options: list[str], accent: str = ACCENT_PINK, parent=None):
        super().__init__(parent)
        self._accent = accent
        self._buttons: list[QPushButton] = []
        self._group = QButtonGroup(self)
        self._group.setExclusive(True)

        lay = QHBoxLayout(self)
        lay.setContentsMargins(3, 3, 3, 3)
        lay.setSpacing(3)

        for i, opt in enumerate(options):
            btn = QPushButton(opt)
            btn.setCheckable(True)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            self._group.addButton(btn, i)
            lay.addWidget(btn)
            self._buttons.append(btn)

        self._buttons[0].setChecked(True)
        self._group.idClicked.connect(self._on_click)
        self._apply_style()

    def _apply_style(self):
        self.setStyleSheet(f"""
            SegmentedControl {{
                background-color: {BG_INPUT_TRACK};
                border: 1px solid {BORDER_SUBTLE};
                border-radius: 9px;
            }}
            SegmentedControl QPushButton {{
                background: transparent;
                border: none;
                border-radius: 6px;
                padding: 6px 16px;
                color: {TEXT_SECONDARY};
                font-weight: 600;
                font-size: 12px;
            }}
            SegmentedControl QPushButton:hover:!checked {{
                color: {TEXT_PRIMARY};
            }}
            SegmentedControl QPushButton:checked {{
                background-color: {self._accent};
                color: white;
            }}
            SegmentedControl QPushButton:disabled {{
                color: {TEXT_FAINT};
            }}
            SegmentedControl:disabled {{
                background-color: {BG_INPUT_TRACK};
            }}
        """)

    def _on_click(self, idx: int):
        self.currentTextChanged.emit(self._buttons[idx].text())

    def currentText(self) -> str:
        checked = self._group.checkedId()
        return self._buttons[checked].text() if checked >= 0 else ""

    def setCurrentIndex(self, i: int):
        if 0 <= i < len(self._buttons):
            self._buttons[i].setChecked(True)

    def setEnabled(self, enabled: bool):
        super().setEnabled(enabled)
        for b in self._buttons:
            b.setEnabled(enabled)


class RangeField(QFrame):
    """Wraps two inputs into a single ``min — max`` control with one border."""

    def __init__(self, min_widget: QWidget, max_widget: QWidget, parent=None):
        super().__init__(parent)
        self.setObjectName("rangeField")
        self.min_widget = min_widget
        self.max_widget = max_widget
        for w in (min_widget, max_widget):
            w.setObjectName("rangeInner")
            w.installEventFilter(self)

        lay = QHBoxLayout(self)
        lay.setContentsMargins(2, 0, 2, 0)
        lay.setSpacing(0)
        lay.addWidget(min_widget, 1)

        dash = QLabel("–")
        dash.setStyleSheet(f"color: {TEXT_FAINT}; font-size: 14px; background: transparent;")
        dash.setFixedWidth(12)
        dash.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(dash)

        lay.addWidget(max_widget, 1)

    def eventFilter(self, obj, event):
        if event.type() == QEvent.Type.FocusIn:
            self.setProperty("focused", "true")
            _repolish(self)
        elif event.type() == QEvent.Type.FocusOut:
            self.setProperty("focused", "false")
            _repolish(self)
        return super().eventFilter(obj, event)


class GradientBar(QWidget):
    """Thin rounded bar painted with a two-stop gradient (star difficulty cue)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(5)
        self._c1 = QColor(star_color(4.0))
        self._c2 = QColor(star_color(5.0))

    def set_colors(self, c1: str, c2: str):
        self._c1 = QColor(c1)
        self._c2 = QColor(c2)
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        grad = QLinearGradient(0, 0, self.width(), 0)
        grad.setColorAt(0.0, self._c1)
        grad.setColorAt(1.0, self._c2)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(grad)
        p.drawRoundedRect(QRectF(0, 0, self.width(), self.height()), 2.5, 2.5)
        p.end()


class IconLabel(QLabel):
    """A QLabel that shows a drawn icon pixmap at a given size/colour."""

    def __init__(self, name: str, size: int, color: str, parent=None):
        super().__init__(parent)
        self.setStyleSheet("background: transparent;")
        self.setPixmap(icon_pixmap(name, size, color))
        self.setFixedSize(size, size)


# ── Thumbnail loader ────────────────────────────────────────────────

class ThumbnailManager:
    _instance = None
    THUMB_W = 168
    THUMB_H = 94

    def __init__(self):
        self._nam = QNetworkAccessManager()
        self._cache: dict[int, QPixmap] = {}
        self._pending: dict[int, list] = {}

    @classmethod
    def instance(cls):
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def request(self, beatmapset_id: int, cover_url: str, callback):
        if beatmapset_id in self._cache:
            callback(self._cache[beatmapset_id])
            return

        if beatmapset_id in self._pending:
            self._pending[beatmapset_id].append(callback)
            return

        self._pending[beatmapset_id] = [callback]

        url = cover_url
        if not url:
            url = f"https://assets.ppy.sh/beatmaps/{beatmapset_id}/covers/list@2x.jpg"

        request = QNetworkRequest(QUrl(url))
        reply = self._nam.get(request)
        reply.finished.connect(lambda: self._on_reply(beatmapset_id, reply))

    def _on_reply(self, beatmapset_id: int, reply: QNetworkReply):
        callbacks = self._pending.pop(beatmapset_id, [])
        if reply.error() == QNetworkReply.NetworkError.NoError:
            data = reply.readAll()
            pixmap = QPixmap()
            pixmap.loadFromData(data)
            if not pixmap.isNull():
                w, h = self.THUMB_W, self.THUMB_H
                scaled = pixmap.scaled(
                    w, h,
                    Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                    Qt.TransformationMode.SmoothTransformation,
                )
                cropped = scaled.copy(
                    (scaled.width() - w) // 2,
                    (scaled.height() - h) // 2,
                    w, h,
                )
                rounded = make_rounded_pixmap(cropped, 9)
                self._cache[beatmapset_id] = rounded
                for cb in callbacks:
                    cb(rounded)
                reply.deleteLater()
                return

        placeholder = self._make_placeholder()
        self._cache[beatmapset_id] = placeholder
        for cb in callbacks:
            cb(placeholder)
        reply.deleteLater()

    @classmethod
    def _make_placeholder(cls) -> QPixmap:
        px = QPixmap(cls.THUMB_W, cls.THUMB_H)
        px.fill(QColor(BG_INPUT_TRACK))
        painter = QPainter(px)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        # centred faint logo instead of "No Cover" text
        logo = icon_pixmap("logo", 34, TEXT_FAINT)
        painter.drawPixmap(
            (cls.THUMB_W - 34) // 2, (cls.THUMB_H - 34) // 2, logo
        )
        painter.end()
        return make_rounded_pixmap(px, 9)


# ── Beatmap Card Widget ─────────────────────────────────────────────

class BeatmapCard(QFrame):
    selection_changed = pyqtSignal(int, bool)

    def __init__(self, hit: BeatmapsetHit, index: int, parent=None):
        super().__init__(parent)
        self.hit = hit
        self.index = index
        self._selected = True
        self._hovered = False
        self._dl_status = None  # None | 'ok' | 'error' | 'dupe'
        self.setFixedHeight(114)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMouseTracking(True)
        self._setup_ui()
        self._load_thumbnail()
        self._update_style()

    def _setup_ui(self):
        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 10, 14, 10)
        layout.setSpacing(14)

        # Checkbox
        self.checkbox = QCheckBox()
        self.checkbox.setChecked(True)
        self.checkbox.setFixedSize(20, 20)
        self.checkbox.setCursor(Qt.CursorShape.PointingHandCursor)
        self.checkbox.setStyleSheet(f"""
            QCheckBox::indicator {{
                width: 18px; height: 18px;
                border: 2px solid {BORDER_SUBTLE};
                border-radius: 5px;
                background-color: {BG_INPUT};
            }}
            QCheckBox::indicator:checked {{
                background-color: {ACCENT_PINK};
                border-color: {ACCENT_PINK};
            }}
            QCheckBox::indicator:hover {{
                border-color: {ACCENT_PINK};
            }}
        """)
        self.checkbox.toggled.connect(self._on_toggled)
        layout.addWidget(self.checkbox, 0, Qt.AlignmentFlag.AlignVCenter)

        # Thumbnail
        self.thumb_label = QLabel()
        self.thumb_label.setFixedSize(
            ThumbnailManager.THUMB_W, ThumbnailManager.THUMB_H
        )
        self.thumb_label.setStyleSheet(
            f"background-color: {BG_INPUT_TRACK}; border-radius: 9px;"
        )
        layout.addWidget(self.thumb_label, 0, Qt.AlignmentFlag.AlignVCenter)

        # Info section
        info_layout = QVBoxLayout()
        info_layout.setContentsMargins(0, 2, 0, 2)
        info_layout.setSpacing(3)

        title_label = QLabel()
        title_label.setStyleSheet(
            f"color: {TEXT_PRIMARY}; font-size: 15px; font-weight: 700; "
            f"background: transparent;"
        )
        fm = QFontMetrics(title_label.font())
        elided = fm.elidedText(self.hit.title, Qt.TextElideMode.ElideRight, 380)
        title_label.setText(elided)
        title_label.setToolTip(self.hit.title)
        info_layout.addWidget(title_label)

        sub_text = self.hit.artist
        if self.hit.creator:
            sub_text += f"   ·   mapped by {self.hit.creator}"
        artist_label = QLabel(sub_text)
        artist_label.setStyleSheet(
            f"color: {TEXT_SECONDARY}; font-size: 11px; background: transparent;"
        )
        info_layout.addWidget(artist_label)

        info_layout.addStretch()

        # Stats row (badges)
        stats_layout = QHBoxLayout()
        stats_layout.setContentsMargins(0, 0, 0, 0)
        stats_layout.setSpacing(6)

        sc = star_color(self.hit.stars)
        stats_layout.addWidget(self._make_badge(
            f"{self.hit.stars:.2f}", bg=BADGE_STAR_BG, text_color=sc, icon="star"
        ))

        if self.hit.beatmaps:
            bm = self.hit.beatmaps[0]
            if bm.bpm:
                stats_layout.addWidget(self._make_badge(
                    f"{bm.bpm:.0f} BPM", bg=BADGE_BPM,
                    text_color=BADGE_BPM_TEXT, icon="note"
                ))
            if bm.total_length:
                stats_layout.addWidget(self._make_badge(
                    format_length(bm.total_length), bg=BADGE_LEN,
                    text_color=BADGE_LEN_TEXT, icon="clock"
                ))
            if bm.cs and bm.mode_int == 3:
                stats_layout.addWidget(self._make_badge(
                    f"{int(bm.cs)}K", bg=BADGE_KEYS,
                    text_color=BADGE_KEYS_TEXT, icon="keys"
                ))

        stats_layout.addStretch()
        info_layout.addLayout(stats_layout)

        layout.addLayout(info_layout, 1)

        # Right column: ID (top) + download-status stamp (bottom)
        right_col = QVBoxLayout()
        right_col.setContentsMargins(0, 0, 0, 0)
        right_col.setSpacing(4)

        id_label = QLabel(f"#{self.hit.id}")
        id_label.setStyleSheet(
            f"color: {TEXT_FAINT}; font-size: 10px; background: transparent;"
        )
        id_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignTop)
        right_col.addWidget(id_label)

        right_col.addStretch()

        self.status_container = QWidget()
        self.status_container.setStyleSheet("background: transparent;")
        self.status_row = QHBoxLayout(self.status_container)
        self.status_row.setContentsMargins(0, 0, 0, 0)
        self.status_row.setSpacing(0)
        self.status_row.addStretch()
        right_col.addWidget(self.status_container, 0, Qt.AlignmentFlag.AlignRight)

        layout.addLayout(right_col, 0)

    def _make_badge(self, text: str, bg: str, text_color: str,
                    icon: str | None = None) -> QFrame:
        badge = QFrame()
        badge.setStyleSheet(f"background-color: {bg}; border-radius: 7px;")
        badge.setFixedHeight(24)
        lay = QHBoxLayout(badge)
        lay.setContentsMargins(8, 0, 10, 0)
        lay.setSpacing(5)
        if icon:
            lay.addWidget(IconLabel(icon, 13, text_color))
        lbl = QLabel(text)
        lbl.setStyleSheet(
            f"color: {text_color}; font-size: 11px; font-weight: 600; "
            f"background: transparent;"
        )
        lay.addWidget(lbl)
        return badge

    def _load_thumbnail(self):
        ThumbnailManager.instance().request(
            self.hit.id, self.hit.cover_url, self._set_thumbnail
        )

    def _set_thumbnail(self, pixmap: QPixmap):
        # Pixmap is already sized/rounded by the manager — set it directly
        # so cover art stays crisp (no double-scaling).
        self.thumb_label.setPixmap(pixmap)

    def _on_toggled(self, checked: bool):
        self._selected = checked
        self._update_style()
        self.selection_changed.emit(self.index, checked)

    def _update_style(self):
        # Border colour: download status wins, else selection/hover state.
        if self._dl_status == "ok":
            border = GREEN_OK
        elif self._dl_status == "error":
            border = RED_ERR
        elif self._selected:
            border = ACCENT_PURPLE
        else:
            border = BORDER_SUBTLE

        if self._selected:
            bg = BG_CARD_SELECTED_HOVER if self._hovered else BG_CARD_SELECTED
        elif self._hovered:
            bg = BG_CARD_HOVER
        else:
            bg = BG_CARD

        width = 2 if (self._selected or self._dl_status in ("ok", "error")) else 1
        self.setStyleSheet(
            f"BeatmapCard {{ background-color: {bg}; "
            f"border: {width}px solid {border}; border-radius: 12px; }}"
        )

    def set_selected(self, selected: bool):
        self._selected = selected
        self.checkbox.blockSignals(True)
        self.checkbox.setChecked(selected)
        self.checkbox.blockSignals(False)
        self._update_style()

    def is_selected(self) -> bool:
        return self._selected

    def enterEvent(self, event):
        self._hovered = True
        self._update_style()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._hovered = False
        self._update_style()
        super().leaveEvent(event)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.checkbox.toggle()
        super().mousePressEvent(event)

    def set_download_status(self, kind: str, text: str):
        """Stamp the card with a download result. kind = ok | error | dupe."""
        self._dl_status = kind
        _clear_layout(self.status_row)
        self.status_row.addStretch()

        if kind == "error":
            bg, color, icon = STATUS_ERR_BG, RED_ERR, "cross"
        elif kind == "dupe":
            bg, color, icon = STATUS_DUPE_BG, ACCENT_BLUE, "check"
        else:
            bg, color, icon = STATUS_OK_BG, GREEN_OK, "check"

        self.status_row.addWidget(self._make_badge(text, bg, color, icon))
        self._update_style()


# ── Workers ──────────────────────────────────────────────────────────

class SearchWorker(QThread):
    progress = pyqtSignal(str)
    finished = pyqtSignal(list)
    error = pyqtSignal(str)
    pp_progress = pyqtSignal(int, int)
    sweep_progress = pyqtSignal(int, int, int, int)  # done, total, found, target

    def __init__(self, params: dict):
        super().__init__()
        self.params = params
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    def _on_sweep(self, done, total, found, target):
        if not self._cancelled:
            self.sweep_progress.emit(done, total, found, target)

    def run(self):
        try:
            p = self.params
            self.progress.emit("Connecting to osu! API...")
            client = OsuClient()
            registry = Registry()

            pp_min = p.get("pp_min")
            pp_max = p.get("pp_max")
            pool_multiplier = 10 if (pp_min is not None or pp_max is not None) else 3

            if self._cancelled:
                registry.close()
                return

            self.progress.emit("Searching beatmaps...")
            hits = sweep_search(
                client=client,
                registry=registry,
                star_min=p["star_min"],
                star_max=p["star_max"],
                mode=p["mode"],
                category=p.get("status", "ranked"),
                max_total=p["count"] * pool_multiplier,
                keys=p.get("keys"),
                bpm_min=p.get("bpm_min"),
                bpm_max=p.get("bpm_max"),
                length_min=p.get("length_min"),
                length_max=p.get("length_max"),
                keyword=p.get("keyword"),
                on_progress=self._on_sweep,
            )

            if self._cancelled:
                registry.close()
                return

            self.progress.emit(f"Found {len(hits)} candidates after sweep search.")

            if pp_min is not None or pp_max is not None:
                self.progress.emit(f"Computing PP for {len(hits)} candidates...")
                hits = self._filter_pp_with_progress(
                    hits, pp_min, pp_max, registry, p["count"]
                )
                if self._cancelled:
                    registry.close()
                    return
                self.progress.emit(f"{len(hits)} maps passed PP filter.")

            registry.close()
            if not self._cancelled:
                self.finished.emit(hits[:p["count"]])
        except Exception as e:
            self.error.emit(str(e))

    def _filter_pp_with_progress(self, candidates, pp_min, pp_max, registry, target_count):
        from concurrent.futures import ThreadPoolExecutor, as_completed
        from src.pp import _load_pp_cache, _pp_in_range, _fetch_and_compute, WORKERS, BATCH_SIZE

        if pp_min is None and pp_max is None:
            return candidates

        pp_cache = _load_pp_cache(registry)
        passed = []
        total = len(candidates)

        with ThreadPoolExecutor(max_workers=WORKERS) as pool:
            for batch_start in range(0, total, BATCH_SIZE):
                if self._cancelled or len(passed) >= target_count:
                    break

                batch = candidates[batch_start:batch_start + BATCH_SIZE]
                resolved = {}
                futures = {}

                for bi, hit in enumerate(batch):
                    passed_from_cache = False
                    uncached = []
                    for bm in hit.beatmaps:
                        if bm.id in pp_cache:
                            if _pp_in_range(pp_cache[bm.id], pp_min, pp_max):
                                resolved[bi] = [bm]
                                passed_from_cache = True
                                break
                        else:
                            uncached.append(bm)

                    if passed_from_cache:
                        continue
                    if len(uncached) == 0:
                        resolved[bi] = None
                    else:
                        for bm in uncached:
                            fut = pool.submit(_fetch_and_compute, bm.id, bm.mode_int)
                            futures[fut] = (bi, bm)

                for fut in as_completed(futures):
                    if self._cancelled:
                        break
                    bi, bm = futures[fut]
                    try:
                        bm_id, pp = fut.result()
                        pp_cache[bm_id] = pp
                        registry.cache_pp(bm_id, pp)
                    except Exception:
                        pass

                for bi, hit in enumerate(batch):
                    if bi in resolved:
                        matching = resolved[bi]
                        if matching is not None:
                            bm = matching[0]
                            passed.append(BeatmapsetHit(
                                id=hit.id, artist=hit.artist, title=hit.title,
                                stars=bm.difficulty_rating, beatmaps=matching,
                                cover_url=hit.cover_url, creator=hit.creator,
                            ))
                    else:
                        for bm in hit.beatmaps:
                            pp = pp_cache.get(bm.id)
                            if pp is not None and _pp_in_range(pp, pp_min, pp_max):
                                passed.append(BeatmapsetHit(
                                    id=hit.id, artist=hit.artist, title=hit.title,
                                    stars=bm.difficulty_rating, beatmaps=[bm],
                                    cover_url=hit.cover_url, creator=hit.creator,
                                ))
                                break

                checked = min(batch_start + BATCH_SIZE, total)
                self.pp_progress.emit(checked, total)
                self.progress.emit(f"PP check: {checked}/{total} sets checked, {len(passed)} passed")

        return passed[:target_count]


class DownloadWorker(QThread):
    progress = pyqtSignal(int, int, str)
    single_done = pyqtSignal(int, str)
    error_single = pyqtSignal(int, str)
    skipped_single = pyqtSignal(int)
    bundle_progress = pyqtSignal(int, int)
    stats = pyqtSignal(str)  # live "speed · N/M · ~ETA left" readout
    finished = pyqtSignal(int, int, list)  # downloaded, skipped, bundle_paths ([] if none)

    def __init__(self, hits: list[BeatmapsetHit],
                 dest_dir: Path = DEFAULT_DOWNLOAD_DIR, bundle: bool = False,
                 split_bytes: int = 0, split_count: int = 0):
        super().__init__()
        self.hits = hits
        self.dest_dir = Path(dest_dir)
        self.bundle = bundle
        # Split mode (only one non-zero): by size (bytes/part) or by map count.
        self.split_bytes = split_bytes
        self.split_count = split_count
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    def run(self):
        registry = Registry()
        downloaded = 0
        skipped = 0
        completed = 0

        hit_by_id = {}
        to_download = []
        for hit in self.hits:
            if registry.is_downloaded(hit.id):
                skipped += 1
                completed += 1
                self.skipped_single.emit(hit.id)
                self.progress.emit(completed, len(self.hits),
                                   f"Skipped (dupe): {hit.artist} - {hit.title}")
            else:
                to_download.append(hit.id)
                hit_by_id[hit.id] = hit

        total_dl = len(to_download)
        dl_start = None
        bytes_done = 0
        done_dl = 0

        def on_result(result: DownloadResult):
            nonlocal downloaded, completed, bytes_done, done_dl
            hit = hit_by_id[result.beatmapset_id]
            completed += 1
            done_dl += 1
            if result.ok:
                registry.record(hit.id, stars=hit.stars,
                                artist=hit.artist, title=hit.title)
                size = result.path.stat().st_size
                bytes_done += size
                downloaded += 1
                self.single_done.emit(hit.id, f"{result.path.name} ({size / 1024:.0f} KB)")
                self.progress.emit(completed, len(self.hits),
                                   f"Downloaded: {hit.artist} - {hit.title}")
            else:
                self.error_single.emit(hit.id, result.error)
                self.progress.emit(completed, len(self.hits),
                                   f"Failed: {hit.artist} - {hit.title}")

            # Live speed / ETA off actual throughput and average time per map.
            elapsed = time.monotonic() - dl_start
            if elapsed > 0 and done_dl > 0:
                speed = bytes_done / elapsed
                eta = (total_dl - done_dl) * (elapsed / done_dl)
                self.stats.emit(
                    f"{fmt_speed(speed)} · {done_dl}/{total_dl} · "
                    f"~{fmt_duration(eta)} left"
                )

        if to_download:
            dl_start = time.monotonic()
            self.progress.emit(completed, len(self.hits),
                               f"Downloading {len(to_download)} maps (parallel)...")
            download_beatmapsets_parallel(
                to_download,
                dest_dir=self.dest_dir,
                on_result=on_result,
                cancelled=lambda: self._cancelled,
            )

        registry.close()

        bundle_paths: list[str] = []
        if self.bundle and not self._cancelled:
            # Bundle every selected map whose .osz is present in the folder —
            # this includes both fresh downloads and dupes already sitting there.
            files = [self.dest_dir / f"{h.id}.osz" for h in self.hits]
            files = [p for p in files if p.exists()]
            if files:
                base = f"osu-beatmaps-{datetime.now():%Y%m%d-%H%M%S}-{len(files)}maps"
                on_prog = lambda i, t: self.bundle_progress.emit(i, t)
                try:
                    if self.split_bytes > 0 or self.split_count > 0:
                        if self.split_count > 0:
                            desc = f"≤{self.split_count} maps"
                        else:
                            desc = f"≤{self.split_bytes / (1024 ** 3):.1f} GB"
                        self.progress.emit(
                            len(self.hits), len(self.hits),
                            f"Bundling {len(files)} maps into {desc} parts…",
                        )
                        result = bundle_osz_split(
                            files, self.dest_dir, base,
                            max_bytes=self.split_bytes,
                            max_files=self.split_count,
                            on_progress=on_prog,
                            cancelled=lambda: self._cancelled,
                        )
                        if result:
                            bundle_paths = [str(p) for p in result]
                    else:
                        out = self.dest_dir / f"{base}.zip"
                        self.progress.emit(
                            len(self.hits), len(self.hits),
                            f"Bundling {len(files)} maps into {out.name}…",
                        )
                        result = bundle_osz(
                            files, out, on_progress=on_prog,
                            cancelled=lambda: self._cancelled,
                        )
                        if result is not None:
                            bundle_paths = [str(result)]
                except Exception as e:
                    self.progress.emit(
                        len(self.hits), len(self.hits), f"Bundle failed: {e}"
                    )

        self.finished.emit(downloaded, skipped, bundle_paths)


# ── Title bar widget ─────────────────────────────────────────────────

class TitleBar(QFrame):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(58)
        self.setStyleSheet(
            f"background-color: {BG_HEADER}; border: none; "
            f"border-bottom: 1px solid {BORDER_SUBTLE};"
        )

        layout = QHBoxLayout(self)
        layout.setContentsMargins(22, 0, 22, 0)
        layout.setSpacing(12)

        logo = IconLabel("logo", 26, ACCENT_PINK)
        layout.addWidget(logo, 0, Qt.AlignmentFlag.AlignVCenter)

        title = QLabel("osu! Beatmap Fetcher")
        title.setStyleSheet(
            f"color: {TEXT_PRIMARY}; font-size: 18px; font-weight: 800; "
            f"letter-spacing: 0.3px; background: transparent;"
        )
        layout.addWidget(title)

        layout.addStretch()

        subtitle = QLabel("bulk download by filter")
        subtitle.setStyleSheet(
            f"color: {TEXT_FAINT}; font-size: 11px; font-style: italic; "
            f"background: transparent;"
        )
        layout.addWidget(subtitle)


# ── Main Window ──────────────────────────────────────────────────────

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("osu! Beatmap Fetcher")
        # Tall enough that filters + actions + ~4 result cards all fit; the
        # results list is the dominant pane (see the splitter setup below).
        self.setMinimumSize(900, 980)
        self.resize(1040, 1040)

        self.search_worker = None
        self.download_worker = None
        self.current_hits: list[BeatmapsetHit] = []
        self.cards: list[BeatmapCard] = []
        self.cards_by_id: dict[int, BeatmapCard] = {}
        self._busy = False
        self._eta_state: dict = {}  # per-phase timing for time-remaining estimates

        # Where .osz files get saved. Remembered across sessions via QSettings
        # so a chosen external drive sticks. Defaults to the bundled downloads/.
        self.settings = QSettings("osu-beatmap-fetcher", "osu-beatmap-fetcher")
        saved_dir = self.settings.value("download_dir", "")
        self.download_dir = Path(saved_dir) if saved_dir else DEFAULT_DOWNLOAD_DIR

        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QVBoxLayout(central)
        main_layout.setSpacing(0)
        main_layout.setContentsMargins(0, 0, 0, 0)

        main_layout.addWidget(TitleBar())

        content = QWidget()
        content_layout = QVBoxLayout(content)
        content_layout.setSpacing(14)
        content_layout.setContentsMargins(20, 18, 20, 12)

        # ── Filters ↕ Results split ─────────────────────────────────────
        # The filter panel is ~440px tall; left in a plain stack it starved the
        # results list of vertical space. Instead the filters go in a scroll
        # area (so a tall filter stack scrolls rather than crushing/clipping
        # anything) and share a vertical QSplitter with the results list. The
        # splitter is auto-balanced toward results and is user-draggable.
        self._filters_panel = self._build_filters()
        filters_scroll = QScrollArea()
        filters_scroll.setObjectName("filtersScroll")
        filters_scroll.setWidgetResizable(True)
        filters_scroll.setWidget(self._filters_panel)
        filters_scroll.setFrameShape(QFrame.Shape.NoFrame)
        filters_scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        filters_scroll.setStyleSheet(
            "QScrollArea#filtersScroll { background: transparent; border: none; }"
        )
        filters_scroll.setMinimumHeight(120)

        results = self._build_results()

        self.splitter = QSplitter(Qt.Orientation.Vertical)
        self.splitter.setChildrenCollapsible(False)  # neither pane draggable to 0
        self.splitter.setHandleWidth(10)
        self.splitter.addWidget(filters_scroll)
        self.splitter.addWidget(results)
        # Filters keep their natural height; every extra pixel goes to results.
        self.splitter.setStretchFactor(0, 0)
        self.splitter.setStretchFactor(1, 1)
        self.splitter.setSizes([440, 700])
        self.splitter.setStyleSheet(f"""
            QSplitter::handle:vertical {{
                background-color: {BORDER_SUBTLE};
                height: 3px;
                margin: 5px 44%;
                border-radius: 1px;
            }}
            QSplitter::handle:vertical:hover {{
                background-color: {TEXT_DIM};
            }}
        """)

        # Auto-rebalance on resize. Once the user drags the handle we remember
        # their chosen filters height and keep honouring it on later resizes.
        self._splitter_user_set = False
        self._user_top = 0
        self._autosizing = False
        self.splitter.splitterMoved.connect(self._on_splitter_moved)

        content_layout.addWidget(self.splitter, stretch=1)
        # Save-to, action buttons and progress stay OUTSIDE the splitter at
        # their natural height, so Search / Download are always visible.
        content_layout.addWidget(self._build_save_location())
        content_layout.addWidget(self._build_actions())
        content_layout.addWidget(self._build_progress())

        main_layout.addWidget(content, 1)

        # Stylesheet depends on a generated combo-arrow asset, so build it
        # here (after the QApplication exists).
        chevron = _qss_asset("chevron", TEXT_SECONDARY)
        self.setStyleSheet(build_stylesheet(chevron))

        self._show_placeholder(
            "search", "Ready to search",
            "Set your filters above, then hit Search."
        )
        self.statusBar().showMessage("Ready — configure filters and search.")

    # ── Splitter auto-balance ──

    def _autosize_splitter(self):
        """Keep the results pane dominant: give it every pixel beyond the
        filters' height, but never less than room for ~4 cards. Filters are
        capped at their natural height so they never balloon into empty space.

        Before the user touches the handle we target the filters' full natural
        height; afterwards we honour whatever height they dragged it to.
        """
        total = self.splitter.height()
        if total <= 0:
            return
        if self._splitter_user_set:
            desired_top = self._user_top
        else:
            desired_top = self._filters_panel.sizeHint().height()
        # Results pane floor = 4 full cards (viewport) + the pane's own header
        # ("Results" / Select-All row ~50px) so the *viewport* clears 4 cards.
        min_results = 4 * 114 + 3 * 9 + 62
        top = min(desired_top, max(120, total - min_results))
        self._autosizing = True
        self.splitter.setSizes([top, total - top])
        self._autosizing = False

    def _on_splitter_moved(self, *_):
        # Ignore the moves our own setSizes() triggers; record real user drags.
        if self._autosizing:
            return
        self._splitter_user_set = True
        self._user_top = self.splitter.sizes()[0]

    def showEvent(self, event):
        super().showEvent(event)
        self._autosize_splitter()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._autosize_splitter()

    # ── Filter panel ──

    def _build_filters(self) -> QFrame:
        panel = QFrame()
        panel.setObjectName("filterPanel")
        # Take only the height it needs — never expand to grab leftover space;
        # that space belongs to the results list.
        panel.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
        outer = QVBoxLayout(panel)
        outer.setContentsMargins(20, 18, 20, 18)
        outer.setSpacing(16)

        # ── Toggles row: Mode (left) + Keys (right) ──
        toggles = QHBoxLayout()
        toggles.setSpacing(12)

        mode_block = QVBoxLayout()
        mode_block.setSpacing(6)
        mode_block.addWidget(self._section_label("Mode"))
        self.mode_seg = SegmentedControl(["mania", "osu", "taiko", "catch"])
        self.mode_seg.currentTextChanged.connect(self._on_mode_changed)
        mode_block.addWidget(self.mode_seg)
        toggles.addLayout(mode_block)

        toggles.addStretch()

        keys_block = QVBoxLayout()
        keys_block.setSpacing(6)
        keys_lbl = self._section_label("Keys")
        keys_lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        keys_block.addWidget(keys_lbl)
        self.keys_seg = SegmentedControl(["Any", "4K", "7K"], accent=ACCENT_PURPLE)
        keys_block.addWidget(self.keys_seg, 0, Qt.AlignmentFlag.AlignRight)
        toggles.addLayout(keys_block)

        outer.addLayout(toggles)
        outer.addWidget(self._separator())

        # ── Range / value grid ──
        grid = QGridLayout()
        grid.setHorizontalSpacing(20)
        grid.setVerticalSpacing(14)
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)

        # Stars (with live difficulty gradient bar)
        self.star_min = self._num_spin(4.0)
        self.star_max = self._num_spin(5.0)
        star_field = RangeField(self.star_min, self.star_max)
        self.star_bar = GradientBar()
        stars_cell = QVBoxLayout()
        stars_cell.setSpacing(6)
        stars_cell.addWidget(self._section_label("Stars"))
        stars_cell.addWidget(star_field)
        stars_cell.addWidget(self.star_bar)
        stars_wrap = QWidget()
        stars_wrap.setLayout(stars_cell)
        grid.addWidget(stars_wrap, 0, 0)
        self.star_min.valueChanged.connect(self._update_star_bar)
        self.star_max.valueChanged.connect(self._update_star_bar)
        self._update_star_bar()

        # BPM
        self.bpm_min = self._opt_line("min")
        self.bpm_max = self._opt_line("max")
        grid.addWidget(
            self._cell("BPM", RangeField(self.bpm_min, self.bpm_max), optional=True),
            0, 1
        )

        # Length
        self.length_min = self._opt_line("min (s)")
        self.length_max = self._opt_line("max (s)")
        grid.addWidget(
            self._cell("Length", RangeField(self.length_min, self.length_max), optional=True),
            1, 0
        )

        # PP
        self.pp_min = self._opt_line("min")
        self.pp_max = self._opt_line("max")
        grid.addWidget(
            self._cell("PP", RangeField(self.pp_min, self.pp_max), optional=True),
            1, 1
        )

        # Status
        self.status_combo = QComboBox()
        self.status_combo.addItems(["ranked", "loved", "qualified", "pending", "graveyard"])
        self.status_combo.setCursor(Qt.CursorShape.PointingHandCursor)
        grid.addWidget(self._cell("Status", self.status_combo), 2, 0)

        # Count
        self.count_spin = QSpinBox()
        # No practical upper bound — user decides how many new maps to pull.
        self.count_spin.setRange(1, 1_000_000)
        self.count_spin.setValue(10)
        self.count_spin.setButtonSymbols(QSpinBox.ButtonSymbols.NoButtons)
        self.count_spin.setAlignment(Qt.AlignmentFlag.AlignLeft)
        grid.addWidget(self._cell("Count", self.count_spin, hint="new maps"), 2, 1)

        outer.addLayout(grid)

        # ── Keyword (full width, with search icon) ──
        kw_cell = QVBoxLayout()
        kw_cell.setSpacing(6)
        kw_cell.addWidget(self._section_label("Keyword", optional=True))

        kw_wrap = QFrame()
        kw_wrap.setObjectName("rangeField")
        kw_lay = QHBoxLayout(kw_wrap)
        kw_lay.setContentsMargins(10, 0, 4, 0)
        kw_lay.setSpacing(8)
        kw_lay.addWidget(IconLabel("search", 15, TEXT_DIM))
        self.keyword_input = QLineEdit()
        self.keyword_input.setObjectName("rangeInner")
        self.keyword_input.setPlaceholderText(
            "e.g. jumpstream, chordjack, tech — matches tags & difficulty names"
        )
        kw_lay.addWidget(self.keyword_input, 1)
        kw_cell.addWidget(kw_wrap)
        outer.addLayout(kw_cell)

        return panel

    # small builders --------------------------------------------------

    @staticmethod
    def _section_label(text: str, optional: bool = False) -> QLabel:
        label = QLabel(text.upper() + ("   ·  OPTIONAL" if optional else ""))
        label.setStyleSheet(
            f"color: {TEXT_DIM}; font-size: 10px; font-weight: 700; "
            f"letter-spacing: 1px; background: transparent;"
        )
        return label

    def _cell(self, label: str, widget: QWidget, optional: bool = False,
              hint: str = "") -> QWidget:
        cell = QVBoxLayout()
        cell.setSpacing(6)
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(self._section_label(label, optional=optional))
        if hint:
            row.addStretch()
            h = QLabel(hint)
            h.setStyleSheet(f"color: {TEXT_FAINT}; font-size: 10px; background: transparent;")
            row.addWidget(h)
        cell.addLayout(row)
        cell.addWidget(widget)
        w = QWidget()
        w.setLayout(cell)
        return w

    @staticmethod
    def _num_spin(value: float) -> QDoubleSpinBox:
        sp = QDoubleSpinBox()
        sp.setRange(0, 20)
        sp.setValue(value)
        sp.setSingleStep(0.1)
        sp.setDecimals(2)
        sp.setButtonSymbols(QDoubleSpinBox.ButtonSymbols.NoButtons)
        sp.setAlignment(Qt.AlignmentFlag.AlignCenter)
        return sp

    @staticmethod
    def _opt_line(placeholder: str) -> QLineEdit:
        le = QLineEdit()
        le.setPlaceholderText(placeholder)
        le.setAlignment(Qt.AlignmentFlag.AlignCenter)
        return le

    @staticmethod
    def _separator() -> QFrame:
        line = QFrame()
        line.setFixedHeight(1)
        line.setStyleSheet(f"background-color: {BORDER_SUBTLE}; border: none;")
        return line

    def _update_star_bar(self):
        lo = min(self.star_min.value(), self.star_max.value())
        hi = max(self.star_min.value(), self.star_max.value())
        self.star_bar.set_colors(star_color(lo), star_color(hi))

    # ── Action buttons ──

    def _build_actions(self) -> QWidget:
        w = QWidget()
        h = QHBoxLayout(w)
        h.setContentsMargins(2, 0, 2, 0)
        h.setSpacing(10)

        self.search_btn = QPushButton("Search")
        self.search_btn.setObjectName("searchBtn")
        self.search_btn.setFixedHeight(40)
        self.search_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.search_btn.clicked.connect(self._on_search)
        h.addWidget(self.search_btn)

        self.download_btn = QPushButton("Download Selected")
        self.download_btn.setObjectName("downloadBtn")
        self.download_btn.setFixedHeight(40)
        self.download_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.download_btn.setEnabled(False)
        self.download_btn.clicked.connect(self._on_download)
        h.addWidget(self.download_btn)

        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.setObjectName("cancelBtn")
        self.cancel_btn.setFixedHeight(40)
        self.cancel_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.cancel_btn.setEnabled(False)
        self.cancel_btn.clicked.connect(self._on_cancel)
        h.addWidget(self.cancel_btn)

        h.addStretch()

        self.result_count_label = QLabel("")
        self.result_count_label.setStyleSheet(
            f"color: {TEXT_SECONDARY}; font-size: 12px; font-weight: 600;"
        )
        h.addWidget(self.result_count_label)

        return w

    # ── Save location ──

    def _build_save_location(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(2, 0, 2, 0)
        v.setSpacing(8)

        # Row 1: folder chooser
        h = QHBoxLayout()
        h.setSpacing(10)
        h.addWidget(self._section_label("Save to"))

        self.dir_display = QLineEdit(str(self.download_dir))
        self.dir_display.setReadOnly(True)
        self.dir_display.setCursor(Qt.CursorShape.IBeamCursor)
        self.dir_display.setToolTip("Folder where .osz files are saved")
        h.addWidget(self.dir_display, 1)

        self.browse_btn = QPushButton("Browse…")
        self.browse_btn.setObjectName("browseBtn")
        self.browse_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.browse_btn.clicked.connect(self._choose_dir)
        h.addWidget(self.browse_btn)

        self.cleanup_btn = QPushButton("Clean up broken")
        self.cleanup_btn.setObjectName("browseBtn")
        self.cleanup_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.cleanup_btn.setToolTip(
            "Scan this folder for corrupt/empty .osz files (0–1 KB maps osu! "
            "can't import), delete them, and forget them so they re-download."
        )
        self.cleanup_btn.clicked.connect(self._on_cleanup_broken)
        h.addWidget(self.cleanup_btn)
        v.addLayout(h)

        # Row 2: bundle-into-one-zip toggle
        self.bundle_check = QCheckBox(
            "Bundle downloaded maps into a single .zip (easy to send)"
        )
        self.bundle_check.setCursor(Qt.CursorShape.PointingHandCursor)
        self.bundle_check.setToolTip(
            ".osz files are already compressed, so this packages them into one "
            "archive for sharing rather than shrinking them further."
        )
        self.bundle_check.setChecked(
            self.settings.value("bundle_zip", False, type=bool)
        )
        self.bundle_check.setStyleSheet(self._checkbox_qss())
        self.bundle_check.toggled.connect(self._on_bundle_toggled)
        v.addWidget(self.bundle_check)

        # Row 3: split-into-parts toggle (indented under the bundle option)
        split_row = QHBoxLayout()
        split_row.setContentsMargins(26, 0, 0, 0)
        split_row.setSpacing(8)

        self.split_check = QCheckBox("Split into parts of")
        self.split_check.setCursor(Qt.CursorShape.PointingHandCursor)
        self.split_check.setToolTip(
            "Instead of one big archive, produce several independent .zip parts — "
            "split either by size (GB) or by number of maps per part."
        )
        self.split_check.setChecked(self.settings.value("split_parts", False, type=bool))
        self.split_check.setStyleSheet(self._checkbox_qss())
        self.split_check.toggled.connect(self._on_split_toggled)
        split_row.addWidget(self.split_check)

        self.split_amount = QLineEdit(str(self.settings.value("split_amount", "2")))
        self.split_amount.setFixedWidth(52)
        self.split_amount.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.split_amount.setToolTip("Maximum per part (GB or map count, per the unit)")
        self.split_amount.textChanged.connect(
            lambda t: self.settings.setValue("split_amount", t)
        )
        split_row.addWidget(self.split_amount)

        self.split_unit_combo = QComboBox()
        self.split_unit_combo.addItems(["GB each", "maps each"])
        self.split_unit_combo.setCursor(Qt.CursorShape.PointingHandCursor)
        self.split_unit_combo.setFixedWidth(104)
        unit_idx = self.split_unit_combo.findText(
            self.settings.value("split_unit", "GB each")
        )
        self.split_unit_combo.setCurrentIndex(max(0, unit_idx))
        self.split_unit_combo.currentTextChanged.connect(
            lambda t: self.settings.setValue("split_unit", t)
        )
        split_row.addWidget(self.split_unit_combo)
        split_row.addStretch()
        v.addLayout(split_row)

        # Row 4: AFK auto-download — kick off downloading automatically as soon
        # as a search finishes, so a search + full download run unattended.
        self.auto_check = QCheckBox(
            "Auto-download all results when a search finishes (AFK mode)"
        )
        self.auto_check.setCursor(Qt.CursorShape.PointingHandCursor)
        self.auto_check.setToolTip(
            "When a search completes, immediately start downloading every map it "
            "found — no need to click Download."
        )
        self.auto_check.setChecked(self.settings.value("auto_download", False, type=bool))
        self.auto_check.setStyleSheet(self._checkbox_qss())
        self.auto_check.toggled.connect(
            lambda on: self.settings.setValue("auto_download", on)
        )
        v.addWidget(self.auto_check)

        self._sync_bundle_controls()

        return w

    @staticmethod
    def _checkbox_qss() -> str:
        return f"""
            QCheckBox {{
                color: {TEXT_SECONDARY};
                font-size: 12px;
                spacing: 8px;
                background: transparent;
            }}
            QCheckBox:disabled {{ color: {TEXT_FAINT}; }}
            QCheckBox::indicator {{
                width: 16px; height: 16px;
                border: 2px solid {BORDER_SUBTLE};
                border-radius: 5px;
                background-color: {BG_INPUT};
            }}
            QCheckBox::indicator:checked {{
                background-color: {ACCENT_PURPLE};
                border-color: {ACCENT_PURPLE};
            }}
            QCheckBox::indicator:hover {{
                border-color: {ACCENT_PURPLE};
            }}
            QCheckBox::indicator:disabled {{
                border-color: {BORDER_SUBTLE};
                background-color: {BG_INPUT_TRACK};
            }}
        """

    def _on_bundle_toggled(self, on: bool):
        self.settings.setValue("bundle_zip", on)
        self._sync_bundle_controls()

    def _on_split_toggled(self, on: bool):
        self.settings.setValue("split_parts", on)
        self._sync_bundle_controls()

    def _sync_bundle_controls(self):
        """Split controls only make sense when bundling, and never while busy."""
        bundling = self.bundle_check.isChecked()
        self.split_check.setEnabled(bundling and not self._busy)
        split_on = bundling and self.split_check.isChecked() and not self._busy
        self.split_amount.setEnabled(split_on)
        self.split_unit_combo.setEnabled(split_on)

    def _choose_dir(self):
        start = (
            str(self.download_dir)
            if self.download_dir.exists()
            else str(Path.home())
        )
        chosen = QFileDialog.getExistingDirectory(
            self, "Choose download folder", start
        )
        if chosen:
            self.download_dir = Path(chosen)
            self.dir_display.setText(chosen)
            self.settings.setValue("download_dir", chosen)
            self.statusBar().showMessage(f"Saving beatmaps to {chosen}")

    def _on_cleanup_broken(self):
        """Delete corrupt .osz files in the current folder and forget them in
        the registry so they get re-downloaded next run."""
        self.cleanup_btn.setEnabled(False)
        self.statusBar().showMessage("Scanning for broken .osz files…")
        QApplication.processEvents()
        try:
            removed_ids = cleanup_broken_osz(self.download_dir)
        except Exception as e:
            self.cleanup_btn.setEnabled(not self._busy)
            QMessageBox.critical(self, "Cleanup failed", str(e))
            return

        if removed_ids:
            registry = Registry()
            forgotten = sum(registry.remove(bid) for bid in removed_ids)
            registry.close()
            msg = (
                f"Removed {len(removed_ids)} broken map"
                f"{'s' if len(removed_ids) != 1 else ''} "
                f"({forgotten} cleared from the registry — they'll re-download)."
            )
        else:
            msg = "No broken maps found in this folder."

        self.progress_label.setText(msg)
        self.statusBar().showMessage(msg)
        self.cleanup_btn.setEnabled(not self._busy)
        QMessageBox.information(self, "Cleanup complete", msg)

    # ── Results panel ──

    def _build_results(self) -> QWidget:
        container = QWidget()
        v = QVBoxLayout(container)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(8)

        header = QWidget()
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(2, 0, 2, 0)
        header_layout.setSpacing(8)

        results_title = QLabel("Results")
        results_title.setStyleSheet(
            f"color: {TEXT_PRIMARY}; font-size: 14px; font-weight: 700;"
        )
        header_layout.addWidget(results_title)

        header_layout.addStretch()

        self.select_all_btn = QPushButton("Select All")
        self.select_all_btn.setObjectName("selectAllBtn")
        self.select_all_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.select_all_btn.clicked.connect(self._select_all)
        self.select_all_btn.setVisible(False)
        header_layout.addWidget(self.select_all_btn)

        self.select_none_btn = QPushButton("Select None")
        self.select_none_btn.setObjectName("selectNoneBtn")
        self.select_none_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.select_none_btn.clicked.connect(self._select_none)
        self.select_none_btn.setVisible(False)
        header_layout.addWidget(self.select_none_btn)

        v.addWidget(header)

        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        # Anti-sliver floor only (~1.5 cards). The 4-card default comes from the
        # splitter auto-balance; keeping this minimum small is what lets the
        # handle travel far enough to drag the filters fully open.
        self.scroll_area.setMinimumHeight(180)
        self.scroll_area.setStyleSheet(
            f"QScrollArea {{ background-color: {BG_BASE}; border: none; }}"
        )

        self.cards_container = QWidget()
        self.cards_container.setStyleSheet(f"background-color: {BG_BASE};")
        self.cards_layout = QVBoxLayout(self.cards_container)
        self.cards_layout.setContentsMargins(0, 2, 8, 2)
        self.cards_layout.setSpacing(9)
        self.cards_layout.addStretch()

        self.scroll_area.setWidget(self.cards_container)
        v.addWidget(self.scroll_area)

        return container

    # ── Progress bar ──

    def _build_progress(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(2, 2, 2, 0)
        v.setSpacing(6)

        # Message on the left, live speed / time-remaining on the right.
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(10)

        self.progress_label = QLabel("")
        self.progress_label.setStyleSheet(f"color: {TEXT_DIM}; font-size: 11px;")
        row.addWidget(self.progress_label, 1)

        self.eta_label = QLabel("")
        self.eta_label.setStyleSheet(
            f"color: {TEXT_SECONDARY}; font-size: 11px; font-weight: 600;"
        )
        self.eta_label.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )
        row.addWidget(self.eta_label, 0)
        v.addLayout(row)

        self.progress_bar = QProgressBar()
        self.progress_bar.setTextVisible(False)
        self.progress_bar.setRange(0, 0)
        self.progress_bar.setVisible(False)
        v.addWidget(self.progress_bar)

        return w

    # ── Time-remaining estimation ──

    def _estimate_eta(self, key: str, done: int, total: int) -> float | None:
        """Estimate seconds remaining for a progress stream from its own rate.

        Timing starts at the first sample of each stream (keyed) so setup time
        and earlier phases don't skew it; returns None until there's a rate.
        """
        now = time.monotonic()
        st = self._eta_state.get(key)
        if st is None or done < st["done0"] or total != st["total"]:
            self._eta_state[key] = {"t0": now, "done0": done, "total": total}
            return None
        progressed = done - st["done0"]
        elapsed = now - st["t0"]
        if progressed <= 0 or elapsed <= 0:
            return None
        return (total - done) / (progressed / elapsed)

    def _reset_eta(self):
        self._eta_state.clear()
        self.eta_label.setText("")

    # ── Placeholder / empty & loading states ──

    def _show_placeholder(self, icon: str, title: str, subtitle: str):
        self._clear_cards()
        self.cards_layout.addStretch()

        holder = QWidget()
        hv = QVBoxLayout(holder)
        hv.setSpacing(10)
        hv.setContentsMargins(0, 0, 0, 0)

        ic = IconLabel(icon, 52, TEXT_FAINT)
        hv.addWidget(ic, 0, Qt.AlignmentFlag.AlignHCenter)

        t = QLabel(title)
        t.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        t.setStyleSheet(
            f"color: {TEXT_SECONDARY}; font-size: 15px; font-weight: 600; "
            f"background: transparent;"
        )
        hv.addWidget(t)

        s = QLabel(subtitle)
        s.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        s.setStyleSheet(f"color: {TEXT_FAINT}; font-size: 12px; background: transparent;")
        hv.addWidget(s)

        self.cards_layout.addWidget(holder, 0, Qt.AlignmentFlag.AlignHCenter)
        self.cards_layout.addStretch()

    # ── Callbacks ──

    def _on_mode_changed(self, mode: str):
        is_mania = mode == "mania"
        self.keys_seg.setEnabled(is_mania)
        if not is_mania:
            self.keys_seg.setCurrentIndex(0)

    def _gather_params(self) -> dict:
        params = {
            "mode": self.mode_seg.currentText(),
            "star_min": self.star_min.value(),
            "star_max": self.star_max.value(),
            "count": self.count_spin.value(),
            "status": self.status_combo.currentText(),
        }

        if params["star_min"] > params["star_max"]:
            params["star_min"], params["star_max"] = params["star_max"], params["star_min"]

        keys_text = self.keys_seg.currentText()
        if keys_text != "Any" and params["mode"] == "mania":
            params["keys"] = int(keys_text.replace("K", ""))

        for attr, key, as_int in [
            (self.bpm_min, "bpm_min", False),
            (self.bpm_max, "bpm_max", False),
            (self.length_min, "length_min", True),
            (self.length_max, "length_max", True),
            (self.pp_min, "pp_min", False),
            (self.pp_max, "pp_max", False),
        ]:
            text = attr.text().strip()
            if text:
                try:
                    params[key] = int(text) if as_int else float(text)
                except ValueError:
                    pass

        keyword = self.keyword_input.text().strip()
        if keyword:
            params["keyword"] = keyword

        return params

    def _selected_count(self) -> int:
        return sum(1 for c in self.cards if c.is_selected())

    def _update_download_enabled(self):
        self.download_btn.setEnabled(not self._busy and self._selected_count() > 0)

    def _update_count_label(self):
        n = len(self.current_hits)
        if n:
            self.result_count_label.setText(f"{self._selected_count()}/{n} selected")
        else:
            self.result_count_label.setText("")

    def _set_busy(self, busy: bool):
        self._busy = busy
        self.search_btn.setEnabled(not busy)
        self.cancel_btn.setEnabled(busy)
        self.browse_btn.setEnabled(not busy)
        self.cleanup_btn.setEnabled(not busy)
        self.bundle_check.setEnabled(not busy)
        self.auto_check.setEnabled(not busy)
        self._sync_bundle_controls()
        self.progress_bar.setVisible(busy)
        if busy:
            self.progress_bar.setRange(0, 0)
        self._update_download_enabled()

    def _on_search(self):
        params = self._gather_params()

        if params["star_min"] == params["star_max"]:
            QMessageBox.warning(self, "Invalid range", "Star min and max cannot be equal.")
            return

        self._set_busy(True)
        self._reset_eta()
        self.current_hits = []
        self._show_placeholder(
            "search", "Searching…",
            "Sweeping sort orders and star shards — this can take a moment."
        )
        self.result_count_label.setText("")
        self.progress_label.setText("Starting search...")

        self.search_worker = SearchWorker(params)
        self.search_worker.progress.connect(self._on_search_progress)
        self.search_worker.pp_progress.connect(self._on_pp_progress)
        self.search_worker.sweep_progress.connect(self._on_sweep_progress)
        self.search_worker.finished.connect(self._on_search_done)
        self.search_worker.error.connect(self._on_search_error)
        self.search_worker.start()

    def _on_search_progress(self, msg: str):
        self.progress_label.setText(msg)
        self.statusBar().showMessage(msg)

    def _on_sweep_progress(self, done: int, total: int, found: int, target: int):
        self.progress_bar.setRange(0, total)
        self.progress_bar.setValue(done)
        msg = f"Searching… {found} candidates found ({done}/{total} sweeps)"
        self.progress_label.setText(msg)
        eta = self._estimate_eta("sweep", done, total)
        self.eta_label.setText(f"~{fmt_duration(eta)} left" if eta is not None else "")

    def _on_pp_progress(self, checked: int, total: int):
        self.progress_bar.setRange(0, total)
        self.progress_bar.setValue(checked)
        eta = self._estimate_eta("pp", checked, total)
        self.eta_label.setText(f"~{fmt_duration(eta)} left" if eta is not None else "")

    def _on_search_done(self, hits: list):
        self.current_hits = hits
        self._populate_cards(hits)
        self._set_busy(False)
        self._update_count_label()

        n = len(hits)
        msg = f"Found {n} map{'s' if n != 1 else ''}."
        self.progress_label.setText(msg)
        self.eta_label.setText("")
        self.statusBar().showMessage(msg)
        self.search_worker = None

        # AFK mode: chain straight into downloading everything we found.
        if self.auto_check.isChecked() and hits:
            self.progress_label.setText(f"{msg} Auto-downloading…")
            self._on_download()

    def _on_search_error(self, msg: str):
        self._set_busy(False)
        self.eta_label.setText("")
        self._show_placeholder("cross", "Search failed", msg)
        self.progress_label.setText(f"Error: {msg}")
        self.statusBar().showMessage(f"Search failed: {msg}")
        QMessageBox.critical(self, "Search Error", msg)
        self.search_worker = None

    def _clear_cards(self):
        self.cards.clear()
        self.cards_by_id.clear()
        _clear_layout(self.cards_layout)
        self.select_all_btn.setVisible(False)
        self.select_none_btn.setVisible(False)

    def _populate_cards(self, hits: list[BeatmapsetHit]):
        self._clear_cards()

        if not hits:
            self._show_placeholder(
                "search", "No maps found",
                "Try widening your star range or clearing some filters."
            )
            return

        self.select_all_btn.setVisible(True)
        self.select_none_btn.setVisible(True)

        for i, hit in enumerate(hits):
            card = BeatmapCard(hit, i)
            card.selection_changed.connect(self._on_card_selection_changed)
            self.cards.append(card)
            self.cards_by_id[hit.id] = card
            self.cards_layout.addWidget(card)

        self.cards_layout.addStretch()

    def _on_card_selection_changed(self, index: int, selected: bool):
        self._update_count_label()
        self._update_download_enabled()

    def _select_all(self):
        for card in self.cards:
            card.set_selected(True)
        self._update_count_label()
        self._update_download_enabled()

    def _select_none(self):
        for card in self.cards:
            card.set_selected(False)
        self._update_count_label()
        self._update_download_enabled()

    def _on_download(self):
        selected = [
            self.current_hits[c.index]
            for c in self.cards if c.is_selected()
        ]
        if not selected:
            QMessageBox.information(self, "No selection", "Select maps to download first.")
            return

        # Resolve the split mode before going busy so a bad value can't leave
        # the UI stuck in a busy state.
        split_bytes, split_count = 0, 0
        if self.bundle_check.isChecked() and self.split_check.isChecked():
            by_count = self.split_unit_combo.currentText().startswith("maps")
            try:
                val = float(self.split_amount.text().strip())
            except ValueError:
                val = 0
            if val <= 0:
                QMessageBox.warning(
                    self, "Invalid split amount",
                    "Enter a positive number for the split amount "
                    "(GB or maps per part)."
                )
                return
            if by_count:
                split_count = int(val)
            else:
                split_bytes = int(val * (1024 ** 3))

        self._set_busy(True)
        self._reset_eta()
        total = len(selected)
        self.progress_bar.setRange(0, total)
        self.progress_bar.setValue(0)
        self.progress_label.setText(f"Downloading 0/{total}...")

        self.download_worker = DownloadWorker(
            selected, dest_dir=self.download_dir,
            bundle=self.bundle_check.isChecked(),
            split_bytes=split_bytes, split_count=split_count,
        )
        self.download_worker.progress.connect(self._on_dl_progress)
        self.download_worker.single_done.connect(self._on_dl_single)
        self.download_worker.error_single.connect(self._on_dl_error_single)
        self.download_worker.skipped_single.connect(self._on_dl_skipped)
        self.download_worker.bundle_progress.connect(self._on_bundle_progress)
        self.download_worker.stats.connect(self._on_dl_stats)
        self.download_worker.finished.connect(self._on_dl_done)
        self.download_worker.start()

    def _on_dl_progress(self, current: int, total: int, msg: str):
        self.progress_bar.setValue(current)
        self.progress_label.setText(f"[{current}/{total}] {msg}")
        self.statusBar().showMessage(msg)

    def _on_dl_stats(self, text: str):
        self.eta_label.setText(text)

    def _on_dl_single(self, beatmapset_id: int, info: str):
        self.statusBar().showMessage(f"Downloaded: {info}")
        card = self.cards_by_id.get(beatmapset_id)
        if card:
            card.set_download_status("ok", "Downloaded")

    def _on_dl_error_single(self, beatmapset_id: int, err: str):
        self.statusBar().showMessage(f"Failed {beatmapset_id}: {err}")
        card = self.cards_by_id.get(beatmapset_id)
        if card:
            card.set_download_status("error", "Failed")

    def _on_dl_skipped(self, beatmapset_id: int):
        card = self.cards_by_id.get(beatmapset_id)
        if card:
            card.set_download_status("dupe", "Already have")

    def _on_bundle_progress(self, current: int, total: int):
        self.progress_bar.setRange(0, total)
        self.progress_bar.setValue(current)
        self.progress_label.setText(f"Bundling into .zip… {current}/{total}")
        eta = self._estimate_eta("bundle", current, total)
        self.eta_label.setText(f"~{fmt_duration(eta)} left" if eta is not None else "")

    def _on_dl_done(self, downloaded: int, skipped: int, bundle_paths: list):
        self._set_busy(False)
        registry = Registry()
        total = registry.count()
        registry.close()
        msg = (
            f"Done — {downloaded} downloaded, {skipped} skipped (dupes). "
            f"Registry: {total} maps total."
        )
        if bundle_paths:
            total_mb = 0.0
            try:
                total_mb = sum(
                    Path(p).stat().st_size for p in bundle_paths
                ) / (1024 * 1024)
            except OSError:
                pass
            if len(bundle_paths) == 1:
                msg += f" Bundled → {Path(bundle_paths[0]).name} ({total_mb:.0f} MB)."
            else:
                msg += (
                    f" Bundled → {len(bundle_paths)} parts, "
                    f"{total_mb:.0f} MB total (e.g. {Path(bundle_paths[0]).name})."
                )
        self.progress_label.setText(msg)
        self.eta_label.setText("")
        self.statusBar().showMessage(msg)
        self.download_worker = None

    def _on_cancel(self):
        if self.search_worker and self.search_worker.isRunning():
            self.search_worker.cancel()
            self.progress_label.setText("Cancelling search...")
            self.statusBar().showMessage("Cancelling...")
        if self.download_worker and self.download_worker.isRunning():
            self.download_worker.cancel()
            self.progress_label.setText("Cancelling download...")
            self.statusBar().showMessage("Cancelling...")
        self.eta_label.setText("")
        self._set_busy(False)


def main():
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
