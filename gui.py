"""PyQt6 GUI for osu-beatmap-fetcher — card-based UI with thumbnails."""

import sys
from pathlib import Path

from PyQt6.QtCore import (
    Qt, QThread, pyqtSignal, QSize, QRect, QUrl, QByteArray, QTimer,
)
from PyQt6.QtGui import (
    QFont, QColor, QPalette, QIcon, QPixmap, QPainter, QPainterPath,
    QLinearGradient, QBrush, QPen, QFontMetrics,
)
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QGridLayout, QLabel, QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox,
    QPushButton, QHeaderView, QProgressBar, QGroupBox, QMessageBox,
    QStatusBar, QFrame, QSizePolicy, QScrollArea, QCheckBox, QSplitter,
)
from PyQt6.QtNetwork import QNetworkAccessManager, QNetworkRequest, QNetworkReply

from src.client import OsuClient, BeatmapsetHit
from src.download import download_beatmapset, download_beatmapsets_parallel, DownloadResult, DownloadError
from src.registry import Registry
from src.search import sweep_search
from src.pp import filter_by_pp

# ── Palette ──────────────────────────────────────────────────────────

BG_BASE = "#191921"
BG_SURFACE = "#1e1e2e"
BG_CARD = "#252538"
BG_CARD_HOVER = "#2c2c44"
BG_CARD_SELECTED = "#2a2a50"
BG_INPUT = "#2a2a42"
BG_HEADER = "#16161d"

ACCENT_PINK = "#FF66AB"
ACCENT_PINK_HOVER = "#FF88C0"
ACCENT_PINK_PRESSED = "#E0508A"
ACCENT_PURPLE = "#B48EF0"
ACCENT_PURPLE_HOVER = "#C4A4FF"
ACCENT_BLUE = "#66AAFF"

TEXT_PRIMARY = "#EEEEF0"
TEXT_SECONDARY = "#B0B0C8"
TEXT_DIM = "#707088"

BORDER_SUBTLE = "#333348"
BORDER_FOCUS = "#FF66AB"

STAR_GOLD = "#FFCC22"
GREEN_OK = "#66CC77"
RED_ERR = "#FF6666"

BADGE_BPM = "#3A3560"
BADGE_BPM_TEXT = "#B0A0F0"
BADGE_LEN = "#2A3A48"
BADGE_LEN_TEXT = "#80C8E8"
BADGE_KEYS = "#3A2A40"
BADGE_KEYS_TEXT = "#E888CC"
BADGE_PP = "#3A3020"
BADGE_PP_TEXT = "#FFCC44"

# ── Stylesheet ───────────────────────────────────────────────────────

STYLESHEET = f"""
QMainWindow {{
    background-color: {BG_BASE};
}}
QWidget {{
    color: {TEXT_PRIMARY};
    font-family: 'Segoe UI', 'Noto Sans', 'Helvetica Neue', sans-serif;
    font-size: 13px;
}}
QGroupBox {{
    background-color: {BG_SURFACE};
    border: 1px solid {BORDER_SUBTLE};
    border-radius: 10px;
    margin-top: 14px;
    padding: 18px 14px 12px 14px;
    font-weight: 600;
    font-size: 13px;
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    left: 16px;
    padding: 0 8px;
    color: {ACCENT_PINK};
    font-size: 13px;
}}
QLabel {{
    color: {TEXT_SECONDARY};
    font-size: 12px;
    background: transparent;
}}
QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox {{
    background-color: {BG_INPUT};
    color: {TEXT_PRIMARY};
    border: 1px solid {BORDER_SUBTLE};
    border-radius: 6px;
    padding: 5px 10px;
    font-size: 12px;
    min-height: 24px;
    selection-background-color: {ACCENT_PURPLE};
}}
QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus {{
    border-color: {ACCENT_PINK};
}}
QComboBox::drop-down {{
    border: none;
    width: 24px;
}}
QComboBox::down-arrow {{
    image: none;
    width: 0;
}}
QComboBox QAbstractItemView {{
    background-color: {BG_INPUT};
    color: {TEXT_PRIMARY};
    selection-background-color: {ACCENT_PURPLE};
    border: 1px solid {BORDER_SUBTLE};
    border-radius: 4px;
    padding: 2px;
}}
QPushButton {{
    border: none;
    border-radius: 8px;
    padding: 9px 24px;
    font-weight: 600;
    font-size: 13px;
}}
QPushButton#searchBtn {{
    background-color: {ACCENT_PINK};
    color: white;
}}
QPushButton#searchBtn:hover {{
    background-color: {ACCENT_PINK_HOVER};
}}
QPushButton#searchBtn:pressed {{
    background-color: {ACCENT_PINK_PRESSED};
}}
QPushButton#downloadBtn {{
    background-color: {ACCENT_PURPLE};
    color: white;
}}
QPushButton#downloadBtn:hover {{
    background-color: {ACCENT_PURPLE_HOVER};
}}
QPushButton#cancelBtn {{
    background-color: #44303A;
    color: {RED_ERR};
}}
QPushButton#cancelBtn:hover {{
    background-color: #553A44;
}}
QPushButton#selectAllBtn, QPushButton#selectNoneBtn {{
    background-color: transparent;
    color: {TEXT_DIM};
    padding: 4px 12px;
    font-size: 11px;
    font-weight: normal;
    border: 1px solid {BORDER_SUBTLE};
    border-radius: 4px;
}}
QPushButton#selectAllBtn:hover, QPushButton#selectNoneBtn:hover {{
    color: {TEXT_PRIMARY};
    border-color: {TEXT_DIM};
}}
QPushButton:disabled {{
    background-color: #2a2a3a;
    color: {TEXT_DIM};
}}
QProgressBar {{
    background-color: {BG_INPUT};
    border: none;
    border-radius: 6px;
    text-align: center;
    color: {TEXT_DIM};
    font-size: 11px;
    min-height: 14px;
    max-height: 14px;
}}
QProgressBar::chunk {{
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
        stop:0 {ACCENT_PINK}, stop:1 {ACCENT_PURPLE});
    border-radius: 6px;
}}
QScrollArea {{
    border: none;
    background-color: transparent;
}}
QScrollBar:vertical {{
    background-color: {BG_BASE};
    width: 8px;
    border: none;
    border-radius: 4px;
}}
QScrollBar::handle:vertical {{
    background-color: {BORDER_SUBTLE};
    border-radius: 4px;
    min-height: 30px;
}}
QScrollBar::handle:vertical:hover {{
    background-color: {TEXT_DIM};
}}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
    height: 0;
}}
QStatusBar {{
    background-color: {BG_HEADER};
    color: {TEXT_DIM};
    font-size: 11px;
    border-top: 1px solid {BORDER_SUBTLE};
}}
"""


# ── Helpers ──────────────────────────────────────────────────────────

def format_length(seconds: int) -> str:
    m, s = divmod(seconds, 60)
    return f"{m}:{s:02d}"


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


# ── Thumbnail loader ────────────────────────────────────────────────

class ThumbnailManager:
    _instance = None

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
            url = f"https://assets.ppy.sh/beatmaps/{beatmapset_id}/covers/list.jpg"

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
                scaled = pixmap.scaled(
                    160, 90,
                    Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                    Qt.TransformationMode.SmoothTransformation,
                )
                cropped = scaled.copy(
                    (scaled.width() - 160) // 2,
                    (scaled.height() - 90) // 2,
                    160, 90,
                )
                rounded = make_rounded_pixmap(cropped, 8)
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

    @staticmethod
    def _make_placeholder() -> QPixmap:
        px = QPixmap(160, 90)
        px.fill(QColor(BG_INPUT))
        painter = QPainter(px)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(QColor(TEXT_DIM)))
        font = QFont("Segoe UI", 10)
        painter.setFont(font)
        painter.drawText(QRect(0, 0, 160, 90), Qt.AlignmentFlag.AlignCenter, "No Cover")
        painter.end()
        return make_rounded_pixmap(px, 8)


# ── Beatmap Card Widget ─────────────────────────────────────────────

class BeatmapCard(QFrame):
    selection_changed = pyqtSignal(int, bool)

    def __init__(self, hit: BeatmapsetHit, index: int, parent=None):
        super().__init__(parent)
        self.hit = hit
        self.index = index
        self._selected = True
        self._hovered = False
        self.setFixedHeight(100)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMouseTracking(True)
        self._setup_ui()
        self._load_thumbnail()
        self._update_style()

    def _setup_ui(self):
        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 8, 12, 8)
        layout.setSpacing(12)

        # Checkbox
        self.checkbox = QCheckBox()
        self.checkbox.setChecked(True)
        self.checkbox.setFixedSize(18, 18)
        self.checkbox.setStyleSheet(f"""
            QCheckBox::indicator {{
                width: 16px; height: 16px;
                border: 2px solid {BORDER_SUBTLE};
                border-radius: 4px;
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
        self.checkbox.toggled.connect(
            lambda checked: self._on_toggled(checked)
        )
        layout.addWidget(self.checkbox, 0, Qt.AlignmentFlag.AlignVCenter)

        # Thumbnail
        self.thumb_label = QLabel()
        self.thumb_label.setFixedSize(160, 84)
        self.thumb_label.setStyleSheet(
            f"background-color: {BG_INPUT}; border-radius: 8px;"
        )
        layout.addWidget(self.thumb_label)

        # Info section
        info_layout = QVBoxLayout()
        info_layout.setContentsMargins(0, 2, 0, 2)
        info_layout.setSpacing(4)

        # Title row
        title_label = QLabel(self.hit.title)
        title_label.setStyleSheet(
            f"color: {TEXT_PRIMARY}; font-size: 14px; font-weight: 600;"
        )
        title_label.setWordWrap(False)
        fm = QFontMetrics(title_label.font())
        elided = fm.elidedText(self.hit.title, Qt.TextElideMode.ElideRight, 400)
        title_label.setText(elided)
        info_layout.addWidget(title_label)

        # Artist / mapper row
        sub_text = self.hit.artist
        if self.hit.creator:
            sub_text += f"  ·  mapped by {self.hit.creator}"
        artist_label = QLabel(sub_text)
        artist_label.setStyleSheet(
            f"color: {TEXT_SECONDARY}; font-size: 11px;"
        )
        info_layout.addWidget(artist_label)

        info_layout.addStretch()

        # Stats row (badges)
        stats_layout = QHBoxLayout()
        stats_layout.setContentsMargins(0, 0, 0, 0)
        stats_layout.setSpacing(6)

        # Star badge
        sc = star_color(self.hit.stars)
        star_badge = self._make_badge(
            f"★ {self.hit.stars:.2f}",
            bg="#2A2820", text_color=sc
        )
        stats_layout.addWidget(star_badge)

        # Gather stats from beatmaps
        if self.hit.beatmaps:
            bm = self.hit.beatmaps[0]

            # BPM
            if bm.bpm:
                bpm_badge = self._make_badge(
                    f"♪ {bm.bpm:.0f} BPM",
                    bg=BADGE_BPM, text_color=BADGE_BPM_TEXT
                )
                stats_layout.addWidget(bpm_badge)

            # Length
            if bm.total_length:
                len_badge = self._make_badge(
                    f"⏱ {format_length(bm.total_length)}",
                    bg=BADGE_LEN, text_color=BADGE_LEN_TEXT
                )
                stats_layout.addWidget(len_badge)

            # Keys (mania)
            if bm.cs and bm.mode_int == 3:
                keys_badge = self._make_badge(
                    f"{int(bm.cs)}K",
                    bg=BADGE_KEYS, text_color=BADGE_KEYS_TEXT
                )
                stats_layout.addWidget(keys_badge)

        stats_layout.addStretch()
        info_layout.addLayout(stats_layout)

        layout.addLayout(info_layout, 1)

        # Right side — ID
        id_label = QLabel(f"#{self.hit.id}")
        id_label.setStyleSheet(
            f"color: {TEXT_DIM}; font-size: 10px;"
        )
        id_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignTop)
        layout.addWidget(id_label, 0, Qt.AlignmentFlag.AlignTop)

    def _make_badge(self, text: str, bg: str, text_color: str) -> QLabel:
        badge = QLabel(text)
        badge.setStyleSheet(
            f"background-color: {bg}; color: {text_color}; "
            f"font-size: 11px; font-weight: 500; "
            f"padding: 2px 8px; border-radius: 4px;"
        )
        badge.setFixedHeight(22)
        return badge

    def _load_thumbnail(self):
        ThumbnailManager.instance().request(
            self.hit.id, self.hit.cover_url, self._set_thumbnail
        )

    def _set_thumbnail(self, pixmap: QPixmap):
        scaled = pixmap.scaled(
            self.thumb_label.size(),
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        self.thumb_label.setPixmap(scaled)

    def _on_toggled(self, checked: bool):
        self._selected = checked
        self._update_style()
        self.selection_changed.emit(self.index, checked)

    def _update_style(self):
        if self._selected:
            bg = BG_CARD_SELECTED if not self._hovered else "#30305a"
            border = ACCENT_PURPLE
        elif self._hovered:
            bg = BG_CARD_HOVER
            border = BORDER_SUBTLE
        else:
            bg = BG_CARD
            border = BORDER_SUBTLE

        self.setStyleSheet(
            f"BeatmapCard {{ background-color: {bg}; "
            f"border: 1px solid {border}; border-radius: 10px; }}"
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

    def set_download_status(self, status: str, is_error: bool = False):
        color = RED_ERR if is_error else GREEN_OK
        status_badge = self._make_badge(status, bg=BG_INPUT, text_color=color)
        self.layout().insertWidget(self.layout().count() - 1, status_badge)


# ── Workers ──────────────────────────────────────────────────────────

class SearchWorker(QThread):
    progress = pyqtSignal(str)
    finished = pyqtSignal(list)
    error = pyqtSignal(str)
    pp_progress = pyqtSignal(int, int)

    def __init__(self, params: dict):
        super().__init__()
        self.params = params
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

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
    finished = pyqtSignal(int, int)

    def __init__(self, hits: list[BeatmapsetHit]):
        super().__init__()
        self.hits = hits
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
                self.progress.emit(completed, len(self.hits),
                                   f"Skipped (dupe): {hit.artist} - {hit.title}")
            else:
                to_download.append(hit.id)
                hit_by_id[hit.id] = hit

        def on_result(result: DownloadResult):
            nonlocal downloaded, completed
            hit = hit_by_id[result.beatmapset_id]
            completed += 1
            if result.ok:
                registry.record(hit.id, stars=hit.stars,
                                artist=hit.artist, title=hit.title)
                size_kb = result.path.stat().st_size / 1024
                downloaded += 1
                self.single_done.emit(hit.id, f"{result.path.name} ({size_kb:.0f} KB)")
                self.progress.emit(completed, len(self.hits),
                                   f"Downloaded: {hit.artist} - {hit.title}")
            else:
                self.error_single.emit(hit.id, result.error)
                self.progress.emit(completed, len(self.hits),
                                   f"Failed: {hit.artist} - {hit.title}")

        if to_download:
            self.progress.emit(completed, len(self.hits),
                               f"Downloading {len(to_download)} maps (parallel)...")
            download_beatmapsets_parallel(
                to_download,
                on_result=on_result,
                cancelled=lambda: self._cancelled,
            )

        registry.close()
        self.finished.emit(downloaded, skipped)


# ── Title bar widget ─────────────────────────────────────────────────

class TitleBar(QFrame):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(52)
        self.setStyleSheet(
            f"background-color: {BG_HEADER}; border: none; "
            f"border-bottom: 1px solid {BORDER_SUBTLE};"
        )

        layout = QHBoxLayout(self)
        layout.setContentsMargins(20, 0, 20, 0)

        icon_label = QLabel("●")
        icon_label.setStyleSheet(
            f"color: {ACCENT_PINK}; font-size: 24px; background: transparent;"
        )
        layout.addWidget(icon_label)

        title = QLabel("osu! Beatmap Fetcher")
        title.setStyleSheet(
            f"color: {TEXT_PRIMARY}; font-size: 18px; font-weight: 700; "
            f"letter-spacing: 0.5px; background: transparent;"
        )
        layout.addWidget(title)

        layout.addStretch()

        subtitle = QLabel("bulk download by filter")
        subtitle.setStyleSheet(
            f"color: {TEXT_DIM}; font-size: 11px; font-style: italic; "
            f"background: transparent;"
        )
        layout.addWidget(subtitle)


# ── Main Window ──────────────────────────────────────────────────────

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("osu! Beatmap Fetcher")
        self.setMinimumSize(880, 720)
        self.resize(960, 820)

        self.search_worker = None
        self.download_worker = None
        self.current_hits: list[BeatmapsetHit] = []
        self.cards: list[BeatmapCard] = []

        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QVBoxLayout(central)
        main_layout.setSpacing(0)
        main_layout.setContentsMargins(0, 0, 0, 0)

        # Title bar
        main_layout.addWidget(TitleBar())

        # Content area with margins
        content = QWidget()
        content_layout = QVBoxLayout(content)
        content_layout.setSpacing(8)
        content_layout.setContentsMargins(16, 12, 16, 8)

        content_layout.addWidget(self._build_filters())
        content_layout.addWidget(self._build_actions())
        content_layout.addWidget(self._build_results(), stretch=1)
        content_layout.addWidget(self._build_progress())

        main_layout.addWidget(content, 1)

        self.statusBar().showMessage("Ready — configure filters and search.")
        self.setStyleSheet(STYLESHEET)

    # ── Filter panel ──

    def _build_filters(self) -> QGroupBox:
        group = QGroupBox("Filters")
        grid = QGridLayout()
        grid.setSpacing(10)
        grid.setContentsMargins(8, 8, 8, 8)

        # Row 0: Mode, Keys, Status, Count
        grid.addWidget(self._filter_label("Mode"), 0, 0)
        self.mode_combo = QComboBox()
        self.mode_combo.addItems(["mania", "osu", "taiko", "catch"])
        self.mode_combo.currentTextChanged.connect(self._on_mode_changed)
        grid.addWidget(self.mode_combo, 0, 1)

        grid.addWidget(self._filter_label("Keys"), 0, 2)
        self.keys_combo = QComboBox()
        self.keys_combo.addItems(["Any", "4K", "7K"])
        grid.addWidget(self.keys_combo, 0, 3)

        grid.addWidget(self._filter_label("Status"), 0, 4)
        self.status_combo = QComboBox()
        self.status_combo.addItems(["ranked", "loved", "qualified", "pending", "graveyard"])
        grid.addWidget(self.status_combo, 0, 5)

        grid.addWidget(self._filter_label("Count"), 0, 6)
        self.count_spin = QSpinBox()
        self.count_spin.setRange(1, 500)
        self.count_spin.setValue(10)
        grid.addWidget(self.count_spin, 0, 7)

        # Row 1: Stars, BPM
        grid.addWidget(self._filter_label("Stars"), 1, 0)
        star_range = QWidget()
        sr_layout = QHBoxLayout(star_range)
        sr_layout.setContentsMargins(0, 0, 0, 0)
        sr_layout.setSpacing(4)
        self.star_min = QDoubleSpinBox()
        self.star_min.setRange(0, 20)
        self.star_min.setValue(4.0)
        self.star_min.setSingleStep(0.1)
        self.star_min.setDecimals(2)
        sr_layout.addWidget(self.star_min)
        dash1 = QLabel("—")
        dash1.setStyleSheet(f"color: {TEXT_DIM}; font-size: 14px;")
        dash1.setFixedWidth(14)
        dash1.setAlignment(Qt.AlignmentFlag.AlignCenter)
        sr_layout.addWidget(dash1)
        self.star_max = QDoubleSpinBox()
        self.star_max.setRange(0, 20)
        self.star_max.setValue(5.0)
        self.star_max.setSingleStep(0.1)
        self.star_max.setDecimals(2)
        sr_layout.addWidget(self.star_max)
        grid.addWidget(star_range, 1, 1, 1, 3)

        grid.addWidget(self._filter_label("BPM"), 1, 4)
        bpm_range = QWidget()
        br_layout = QHBoxLayout(bpm_range)
        br_layout.setContentsMargins(0, 0, 0, 0)
        br_layout.setSpacing(4)
        self.bpm_min = QLineEdit()
        self.bpm_min.setPlaceholderText("min")
        br_layout.addWidget(self.bpm_min)
        dash2 = QLabel("—")
        dash2.setStyleSheet(f"color: {TEXT_DIM}; font-size: 14px;")
        dash2.setFixedWidth(14)
        dash2.setAlignment(Qt.AlignmentFlag.AlignCenter)
        br_layout.addWidget(dash2)
        self.bpm_max = QLineEdit()
        self.bpm_max.setPlaceholderText("max")
        br_layout.addWidget(self.bpm_max)
        grid.addWidget(bpm_range, 1, 5, 1, 3)

        # Row 2: Length, PP
        grid.addWidget(self._filter_label("Length"), 2, 0)
        len_range = QWidget()
        lr_layout = QHBoxLayout(len_range)
        lr_layout.setContentsMargins(0, 0, 0, 0)
        lr_layout.setSpacing(4)
        self.length_min = QLineEdit()
        self.length_min.setPlaceholderText("min (s)")
        lr_layout.addWidget(self.length_min)
        dash3 = QLabel("—")
        dash3.setStyleSheet(f"color: {TEXT_DIM}; font-size: 14px;")
        dash3.setFixedWidth(14)
        dash3.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lr_layout.addWidget(dash3)
        self.length_max = QLineEdit()
        self.length_max.setPlaceholderText("max (s)")
        lr_layout.addWidget(self.length_max)
        grid.addWidget(len_range, 2, 1, 1, 3)

        grid.addWidget(self._filter_label("PP"), 2, 4)
        pp_range = QWidget()
        pr_layout = QHBoxLayout(pp_range)
        pr_layout.setContentsMargins(0, 0, 0, 0)
        pr_layout.setSpacing(4)
        self.pp_min = QLineEdit()
        self.pp_min.setPlaceholderText("min")
        pr_layout.addWidget(self.pp_min)
        dash4 = QLabel("—")
        dash4.setStyleSheet(f"color: {TEXT_DIM}; font-size: 14px;")
        dash4.setFixedWidth(14)
        dash4.setAlignment(Qt.AlignmentFlag.AlignCenter)
        pr_layout.addWidget(dash4)
        self.pp_max = QLineEdit()
        self.pp_max.setPlaceholderText("max")
        pr_layout.addWidget(self.pp_max)
        grid.addWidget(pp_range, 2, 5, 1, 3)

        # Row 3: Keyword (full width)
        grid.addWidget(self._filter_label("Keyword"), 3, 0)
        self.keyword_input = QLineEdit()
        self.keyword_input.setPlaceholderText(
            "e.g. jumpstream, chordjack, tech — matches tags & difficulty names"
        )
        grid.addWidget(self.keyword_input, 3, 1, 1, 7)

        group.setLayout(grid)
        return group

    @staticmethod
    def _filter_label(text: str) -> QLabel:
        label = QLabel(text)
        label.setStyleSheet(
            f"color: {TEXT_DIM}; font-size: 11px; font-weight: 600; "
            f"text-transform: uppercase; letter-spacing: 0.5px;"
        )
        label.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )
        label.setFixedWidth(52)
        return label

    # ── Action buttons ──

    def _build_actions(self) -> QWidget:
        w = QWidget()
        h = QHBoxLayout(w)
        h.setContentsMargins(0, 4, 0, 4)
        h.setSpacing(10)

        self.search_btn = QPushButton("Search")
        self.search_btn.setObjectName("searchBtn")
        self.search_btn.setFixedHeight(38)
        self.search_btn.clicked.connect(self._on_search)
        h.addWidget(self.search_btn)

        self.download_btn = QPushButton("Download Selected")
        self.download_btn.setObjectName("downloadBtn")
        self.download_btn.setFixedHeight(38)
        self.download_btn.setEnabled(False)
        self.download_btn.clicked.connect(self._on_download)
        h.addWidget(self.download_btn)

        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.setObjectName("cancelBtn")
        self.cancel_btn.setFixedHeight(38)
        self.cancel_btn.setEnabled(False)
        self.cancel_btn.clicked.connect(self._on_cancel)
        h.addWidget(self.cancel_btn)

        h.addStretch()

        # Result count
        self.result_count_label = QLabel("")
        self.result_count_label.setStyleSheet(
            f"color: {TEXT_DIM}; font-size: 12px;"
        )
        h.addWidget(self.result_count_label)

        return w

    # ── Results panel ──

    def _build_results(self) -> QWidget:
        container = QWidget()
        v = QVBoxLayout(container)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(6)

        # Header row
        header = QWidget()
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(0, 0, 0, 0)
        header_layout.setSpacing(8)

        results_title = QLabel("Results")
        results_title.setStyleSheet(
            f"color: {ACCENT_PINK}; font-size: 13px; font-weight: 600;"
        )
        header_layout.addWidget(results_title)

        header_layout.addStretch()

        self.select_all_btn = QPushButton("Select All")
        self.select_all_btn.setObjectName("selectAllBtn")
        self.select_all_btn.clicked.connect(self._select_all)
        self.select_all_btn.setVisible(False)
        header_layout.addWidget(self.select_all_btn)

        self.select_none_btn = QPushButton("Select None")
        self.select_none_btn.setObjectName("selectNoneBtn")
        self.select_none_btn.clicked.connect(self._select_none)
        self.select_none_btn.setVisible(False)
        header_layout.addWidget(self.select_none_btn)

        v.addWidget(header)

        # Scroll area for cards
        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self.scroll_area.setStyleSheet(
            f"QScrollArea {{ background-color: {BG_BASE}; border: none; }}"
        )

        self.cards_container = QWidget()
        self.cards_container.setStyleSheet(f"background-color: {BG_BASE};")
        self.cards_layout = QVBoxLayout(self.cards_container)
        self.cards_layout.setContentsMargins(0, 0, 4, 0)
        self.cards_layout.setSpacing(6)
        self.cards_layout.addStretch()

        # Empty state
        self.empty_label = QLabel("Search for beatmaps to see results here.")
        self.empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_label.setStyleSheet(
            f"color: {TEXT_DIM}; font-size: 13px; padding: 40px;"
        )
        self.cards_layout.insertWidget(0, self.empty_label)

        self.scroll_area.setWidget(self.cards_container)
        v.addWidget(self.scroll_area)

        return container

    # ── Progress bar ──

    def _build_progress(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(0, 4, 0, 0)
        v.setSpacing(4)

        self.progress_label = QLabel("")
        self.progress_label.setStyleSheet(
            f"color: {TEXT_DIM}; font-size: 11px;"
        )
        v.addWidget(self.progress_label)

        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 0)
        self.progress_bar.setVisible(False)
        v.addWidget(self.progress_bar)

        return w

    # ── Callbacks ──

    def _on_mode_changed(self, mode: str):
        is_mania = mode == "mania"
        self.keys_combo.setEnabled(is_mania)
        if not is_mania:
            self.keys_combo.setCurrentIndex(0)

    def _gather_params(self) -> dict:
        params = {
            "mode": self.mode_combo.currentText(),
            "star_min": self.star_min.value(),
            "star_max": self.star_max.value(),
            "count": self.count_spin.value(),
            "status": self.status_combo.currentText(),
        }

        if params["star_min"] > params["star_max"]:
            params["star_min"], params["star_max"] = params["star_max"], params["star_min"]

        keys_text = self.keys_combo.currentText()
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

    def _set_busy(self, busy: bool):
        self.search_btn.setEnabled(not busy)
        self.download_btn.setEnabled(not busy and len(self.current_hits) > 0)
        self.cancel_btn.setEnabled(busy)
        self.progress_bar.setVisible(busy)
        if busy:
            self.progress_bar.setRange(0, 0)

    def _on_search(self):
        params = self._gather_params()

        if params["star_min"] == params["star_max"]:
            QMessageBox.warning(self, "Invalid range", "Star min and max cannot be equal.")
            return

        self._set_busy(True)
        self.current_hits = []
        self._clear_cards()
        self.result_count_label.setText("")
        self.progress_label.setText("Starting search...")

        self.search_worker = SearchWorker(params)
        self.search_worker.progress.connect(self._on_search_progress)
        self.search_worker.pp_progress.connect(self._on_pp_progress)
        self.search_worker.finished.connect(self._on_search_done)
        self.search_worker.error.connect(self._on_search_error)
        self.search_worker.start()

    def _on_search_progress(self, msg: str):
        self.progress_label.setText(msg)
        self.statusBar().showMessage(msg)

    def _on_pp_progress(self, checked: int, total: int):
        self.progress_bar.setRange(0, total)
        self.progress_bar.setValue(checked)

    def _on_search_done(self, hits: list):
        self.current_hits = hits
        self._populate_cards(hits)
        self._set_busy(False)

        n = len(hits)
        selected = sum(1 for c in self.cards if c.is_selected())
        self.result_count_label.setText(f"{selected}/{n} selected")

        msg = f"Found {n} map{'s' if n != 1 else ''}."
        self.progress_label.setText(msg)
        self.statusBar().showMessage(msg)
        self.search_worker = None

    def _on_search_error(self, msg: str):
        self._set_busy(False)
        self.progress_label.setText(f"Error: {msg}")
        self.statusBar().showMessage(f"Search failed: {msg}")
        QMessageBox.critical(self, "Search Error", msg)
        self.search_worker = None

    def _clear_cards(self):
        self.cards.clear()
        while self.cards_layout.count() > 0:
            item = self.cards_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self.cards_layout.addStretch()
        self.select_all_btn.setVisible(False)
        self.select_none_btn.setVisible(False)

    def _populate_cards(self, hits: list[BeatmapsetHit]):
        self._clear_cards()

        if not hits:
            self.empty_label = QLabel("No maps found matching your filters.")
            self.empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.empty_label.setStyleSheet(
                f"color: {TEXT_DIM}; font-size: 13px; padding: 40px;"
            )
            self.cards_layout.insertWidget(0, self.empty_label)
            return

        self.select_all_btn.setVisible(True)
        self.select_none_btn.setVisible(True)

        for i, hit in enumerate(hits):
            card = BeatmapCard(hit, i)
            card.selection_changed.connect(self._on_card_selection_changed)
            self.cards.append(card)
            self.cards_layout.insertWidget(i, card)

    def _on_card_selection_changed(self, index: int, selected: bool):
        n = len(self.current_hits)
        selected_count = sum(1 for c in self.cards if c.is_selected())
        self.result_count_label.setText(f"{selected_count}/{n} selected")
        self.download_btn.setEnabled(selected_count > 0)

    def _select_all(self):
        for card in self.cards:
            card.set_selected(True)
        n = len(self.current_hits)
        self.result_count_label.setText(f"{n}/{n} selected")
        self.download_btn.setEnabled(n > 0)

    def _select_none(self):
        for card in self.cards:
            card.set_selected(False)
        n = len(self.current_hits)
        self.result_count_label.setText(f"0/{n} selected")
        self.download_btn.setEnabled(False)

    def _on_download(self):
        selected = [
            self.current_hits[c.index]
            for c in self.cards if c.is_selected()
        ]
        if not selected:
            QMessageBox.information(self, "No selection", "Select maps to download first.")
            return

        self._set_busy(True)
        total = len(selected)
        self.progress_bar.setRange(0, total)
        self.progress_bar.setValue(0)
        self.progress_label.setText(f"Downloading 0/{total}...")

        self.download_worker = DownloadWorker(selected)
        self.download_worker.progress.connect(self._on_dl_progress)
        self.download_worker.single_done.connect(self._on_dl_single)
        self.download_worker.error_single.connect(self._on_dl_error_single)
        self.download_worker.finished.connect(self._on_dl_done)
        self.download_worker.start()

    def _on_dl_progress(self, current: int, total: int, msg: str):
        self.progress_bar.setValue(current)
        self.progress_label.setText(f"[{current}/{total}] {msg}")
        self.statusBar().showMessage(msg)

    def _on_dl_single(self, beatmapset_id: int, info: str):
        self.statusBar().showMessage(f"Downloaded: {info}")

    def _on_dl_error_single(self, beatmapset_id: int, err: str):
        self.statusBar().showMessage(f"Failed {beatmapset_id}: {err}")

    def _on_dl_done(self, downloaded: int, skipped: int):
        self._set_busy(False)
        registry = Registry()
        total = registry.count()
        registry.close()
        msg = (
            f"Done — {downloaded} downloaded, {skipped} skipped (dupes). "
            f"Registry: {total} maps total."
        )
        self.progress_label.setText(msg)
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
        self._set_busy(False)


def main():
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
