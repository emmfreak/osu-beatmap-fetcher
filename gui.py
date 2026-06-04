"""PyQt6 GUI for osu-beatmap-fetcher (slice 4).

Thin layer over the existing engine — calls src/ functions directly.
"""

import sys
import threading
from pathlib import Path

from PyQt6.QtCore import Qt, QThread, pyqtSignal, QSize
from PyQt6.QtGui import QFont, QColor, QPalette, QIcon
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QGridLayout, QLabel, QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox,
    QPushButton, QTableWidget, QTableWidgetItem, QHeaderView,
    QProgressBar, QGroupBox, QMessageBox, QStatusBar, QFrame,
    QAbstractItemView, QSizePolicy,
)

from src.client import OsuClient, BeatmapsetHit
from src.download import download_beatmapset, polite_delay, DownloadError
from src.registry import Registry
from src.search import sweep_search
from src.pp import filter_by_pp

ACCENT_PINK = "#FF66AA"
ACCENT_PURPLE = "#8866CC"
BG_DARK = "#1a1a2e"
BG_PANEL = "#22223a"
BG_INPUT = "#2a2a4a"
TEXT_PRIMARY = "#e0e0e0"
TEXT_DIM = "#888899"
BORDER = "#3a3a5a"

STYLESHEET = f"""
QMainWindow, QWidget {{
    background-color: {BG_DARK};
    color: {TEXT_PRIMARY};
    font-family: 'Segoe UI', 'Noto Sans', sans-serif;
}}
QGroupBox {{
    background-color: {BG_PANEL};
    border: 1px solid {BORDER};
    border-radius: 6px;
    margin-top: 12px;
    padding: 14px 10px 10px 10px;
    font-weight: bold;
    font-size: 13px;
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    left: 12px;
    padding: 0 6px;
    color: {ACCENT_PINK};
}}
QLabel {{
    color: {TEXT_PRIMARY};
    font-size: 12px;
}}
QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox {{
    background-color: {BG_INPUT};
    color: {TEXT_PRIMARY};
    border: 1px solid {BORDER};
    border-radius: 4px;
    padding: 4px 8px;
    font-size: 12px;
    min-height: 22px;
}}
QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus {{
    border-color: {ACCENT_PINK};
}}
QComboBox::drop-down {{
    border: none;
    width: 20px;
}}
QComboBox QAbstractItemView {{
    background-color: {BG_INPUT};
    color: {TEXT_PRIMARY};
    selection-background-color: {ACCENT_PURPLE};
}}
QPushButton {{
    background-color: {ACCENT_PINK};
    color: white;
    border: none;
    border-radius: 4px;
    padding: 8px 20px;
    font-weight: bold;
    font-size: 13px;
}}
QPushButton:hover {{
    background-color: #ff88bb;
}}
QPushButton:pressed {{
    background-color: #dd5599;
}}
QPushButton:disabled {{
    background-color: #555566;
    color: #888899;
}}
QPushButton#cancelBtn {{
    background-color: #aa4455;
}}
QPushButton#cancelBtn:hover {{
    background-color: #cc5566;
}}
QPushButton#downloadBtn {{
    background-color: {ACCENT_PURPLE};
}}
QPushButton#downloadBtn:hover {{
    background-color: #9977dd;
}}
QTableWidget {{
    background-color: {BG_PANEL};
    color: {TEXT_PRIMARY};
    border: 1px solid {BORDER};
    border-radius: 4px;
    gridline-color: {BORDER};
    font-size: 12px;
    selection-background-color: {ACCENT_PURPLE};
}}
QTableWidget::item {{
    padding: 4px;
}}
QHeaderView::section {{
    background-color: {BG_INPUT};
    color: {ACCENT_PINK};
    border: 1px solid {BORDER};
    padding: 4px 8px;
    font-weight: bold;
    font-size: 11px;
}}
QProgressBar {{
    background-color: {BG_INPUT};
    border: 1px solid {BORDER};
    border-radius: 4px;
    text-align: center;
    color: {TEXT_PRIMARY};
    font-size: 11px;
    min-height: 20px;
}}
QProgressBar::chunk {{
    background-color: {ACCENT_PINK};
    border-radius: 3px;
}}
QStatusBar {{
    background-color: {BG_PANEL};
    color: {TEXT_DIM};
    font-size: 11px;
}}
"""


class SearchWorker(QThread):
    """Runs search + PP filtering on a background thread."""
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
                            ))
                    else:
                        for bm in hit.beatmaps:
                            pp = pp_cache.get(bm.id)
                            if pp is not None and _pp_in_range(pp, pp_min, pp_max):
                                passed.append(BeatmapsetHit(
                                    id=hit.id, artist=hit.artist, title=hit.title,
                                    stars=bm.difficulty_rating, beatmaps=[bm],
                                ))
                                break

                checked = min(batch_start + BATCH_SIZE, total)
                self.pp_progress.emit(checked, total)
                self.progress.emit(f"PP check: {checked}/{total} sets checked, {len(passed)} passed")

        return passed[:target_count]


class DownloadWorker(QThread):
    """Downloads .osz files on a background thread."""
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

        for i, hit in enumerate(self.hits):
            if self._cancelled:
                break

            if registry.is_downloaded(hit.id):
                skipped += 1
                self.progress.emit(i + 1, len(self.hits),
                                   f"Skipped (dupe): {hit.artist} - {hit.title}")
                continue

            self.progress.emit(i + 1, len(self.hits),
                               f"Downloading: {hit.artist} - {hit.title}")
            try:
                path = download_beatmapset(hit.id)
                registry.record(hit.id, stars=hit.stars,
                                artist=hit.artist, title=hit.title)
                size_kb = path.stat().st_size / 1024
                downloaded += 1
                self.single_done.emit(hit.id, f"{path.name} ({size_kb:.0f} KB)")
                if i < len(self.hits) - 1:
                    polite_delay()
            except DownloadError as e:
                self.error_single.emit(hit.id, str(e))

        registry.close()
        self.finished.emit(downloaded, skipped)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("osu! Beatmap Fetcher")
        self.setMinimumSize(820, 700)
        self.resize(900, 780)

        self.search_worker = None
        self.download_worker = None
        self.current_hits = []

        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        layout.setSpacing(6)
        layout.setContentsMargins(12, 12, 12, 8)

        layout.addWidget(self._build_filters())
        layout.addWidget(self._build_actions())
        layout.addWidget(self._build_results(), stretch=1)
        layout.addWidget(self._build_progress())

        self.statusBar().showMessage("Ready — configure filters and click Search.")
        self.setStyleSheet(STYLESHEET)

    def _build_filters(self) -> QGroupBox:
        group = QGroupBox("Filters")
        grid = QGridLayout()
        grid.setSpacing(8)
        row = 0

        # Mode
        grid.addWidget(QLabel("Mode:"), row, 0)
        self.mode_combo = QComboBox()
        self.mode_combo.addItems(["mania", "osu", "taiko", "catch"])
        self.mode_combo.currentTextChanged.connect(self._on_mode_changed)
        grid.addWidget(self.mode_combo, row, 1)

        # Keys
        grid.addWidget(QLabel("Keys:"), row, 2)
        self.keys_combo = QComboBox()
        self.keys_combo.addItems(["Any", "4K", "7K"])
        grid.addWidget(self.keys_combo, row, 3)
        row += 1

        # Stars
        grid.addWidget(QLabel("Stars min:"), row, 0)
        self.star_min = QDoubleSpinBox()
        self.star_min.setRange(0, 20)
        self.star_min.setValue(4.0)
        self.star_min.setSingleStep(0.1)
        self.star_min.setDecimals(2)
        grid.addWidget(self.star_min, row, 1)

        grid.addWidget(QLabel("Stars max:"), row, 2)
        self.star_max = QDoubleSpinBox()
        self.star_max.setRange(0, 20)
        self.star_max.setValue(5.0)
        self.star_max.setSingleStep(0.1)
        self.star_max.setDecimals(2)
        grid.addWidget(self.star_max, row, 3)
        row += 1

        # BPM
        grid.addWidget(QLabel("BPM min:"), row, 0)
        self.bpm_min = QSpinBox()
        self.bpm_min.setRange(0, 9999)
        self.bpm_min.setSpecialValueText("—")
        grid.addWidget(self.bpm_min, row, 1)

        grid.addWidget(QLabel("BPM max:"), row, 2)
        self.bpm_max = QSpinBox()
        self.bpm_max.setRange(0, 9999)
        self.bpm_max.setSpecialValueText("—")
        grid.addWidget(self.bpm_max, row, 3)
        row += 1

        # Length
        grid.addWidget(QLabel("Length min (s):"), row, 0)
        self.length_min = QSpinBox()
        self.length_min.setRange(0, 99999)
        self.length_min.setSpecialValueText("—")
        grid.addWidget(self.length_min, row, 1)

        grid.addWidget(QLabel("Length max (s):"), row, 2)
        self.length_max = QSpinBox()
        self.length_max.setRange(0, 99999)
        self.length_max.setSpecialValueText("—")
        grid.addWidget(self.length_max, row, 3)
        row += 1

        # PP
        grid.addWidget(QLabel("PP min:"), row, 0)
        self.pp_min = QSpinBox()
        self.pp_min.setRange(0, 99999)
        self.pp_min.setSpecialValueText("—")
        grid.addWidget(self.pp_min, row, 1)

        grid.addWidget(QLabel("PP max:"), row, 2)
        self.pp_max = QSpinBox()
        self.pp_max.setRange(0, 99999)
        self.pp_max.setSpecialValueText("—")
        grid.addWidget(self.pp_max, row, 3)
        row += 1

        # Keyword
        grid.addWidget(QLabel("Keyword:"), row, 0)
        self.keyword_input = QLineEdit()
        self.keyword_input.setPlaceholderText("e.g. jumpstream, chordjack, tech")
        grid.addWidget(self.keyword_input, row, 1, 1, 2)

        # Count
        grid.addWidget(QLabel("Count:"), row, 3)
        row += 1

        # Keyword hint + count spinner on next row
        hint = QLabel("Matches tags & difficulty names — depends on how maps are labelled.")
        hint.setStyleSheet(f"color: {TEXT_DIM}; font-size: 10px; font-style: italic;")
        grid.addWidget(hint, row, 0, 1, 3)

        self.count_spin = QSpinBox()
        self.count_spin.setRange(1, 500)
        self.count_spin.setValue(10)
        grid.addWidget(self.count_spin, row - 1, 3)

        # Move count label alignment
        row += 1

        group.setLayout(grid)
        return group

    def _build_actions(self) -> QWidget:
        w = QWidget()
        h = QHBoxLayout(w)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(8)

        self.search_btn = QPushButton("Search")
        self.search_btn.clicked.connect(self._on_search)
        h.addWidget(self.search_btn)

        self.download_btn = QPushButton("Download Selected")
        self.download_btn.setObjectName("downloadBtn")
        self.download_btn.setEnabled(False)
        self.download_btn.clicked.connect(self._on_download)
        h.addWidget(self.download_btn)

        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.setObjectName("cancelBtn")
        self.cancel_btn.setEnabled(False)
        self.cancel_btn.clicked.connect(self._on_cancel)
        h.addWidget(self.cancel_btn)

        h.addStretch()
        return w

    def _build_results(self) -> QGroupBox:
        group = QGroupBox("Results")
        v = QVBoxLayout()

        self.results_table = QTableWidget()
        self.results_table.setColumnCount(4)
        self.results_table.setHorizontalHeaderLabels(["ID", "Artist", "Title", "Stars"])
        self.results_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.results_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.results_table.setAlternatingRowColors(True)

        header = self.results_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)

        self.results_table.verticalHeader().setVisible(False)

        v.addWidget(self.results_table)
        group.setLayout(v)
        return group

    def _build_progress(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(4)

        self.progress_label = QLabel("")
        self.progress_label.setStyleSheet(f"color: {TEXT_DIM}; font-size: 11px;")
        v.addWidget(self.progress_label)

        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 0)
        self.progress_bar.setVisible(False)
        v.addWidget(self.progress_bar)

        return w

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
            "status": "ranked",
        }

        if params["star_min"] > params["star_max"]:
            params["star_min"], params["star_max"] = params["star_max"], params["star_min"]

        keys_text = self.keys_combo.currentText()
        if keys_text != "Any" and params["mode"] == "mania":
            params["keys"] = int(keys_text.replace("K", ""))

        if self.bpm_min.value() > 0:
            params["bpm_min"] = float(self.bpm_min.value())
        if self.bpm_max.value() > 0:
            params["bpm_max"] = float(self.bpm_max.value())
        if self.length_min.value() > 0:
            params["length_min"] = self.length_min.value()
        if self.length_max.value() > 0:
            params["length_max"] = self.length_max.value()
        if self.pp_min.value() > 0:
            params["pp_min"] = float(self.pp_min.value())
        if self.pp_max.value() > 0:
            params["pp_max"] = float(self.pp_max.value())

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
        self.results_table.setRowCount(0)
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
        self._populate_table(hits)
        self._set_busy(False)

        n = len(hits)
        msg = f"Found {n} map{'s' if n != 1 else ''}. Select rows and click Download, or download all."
        self.progress_label.setText(msg)
        self.statusBar().showMessage(msg)
        self.search_worker = None

    def _on_search_error(self, msg: str):
        self._set_busy(False)
        self.progress_label.setText(f"Error: {msg}")
        self.statusBar().showMessage(f"Search failed: {msg}")
        QMessageBox.critical(self, "Search Error", msg)
        self.search_worker = None

    def _populate_table(self, hits: list[BeatmapsetHit]):
        self.results_table.setRowCount(len(hits))
        for row, hit in enumerate(hits):
            self.results_table.setItem(row, 0, QTableWidgetItem(str(hit.id)))
            self.results_table.setItem(row, 1, QTableWidgetItem(hit.artist))
            self.results_table.setItem(row, 2, QTableWidgetItem(hit.title))

            stars_item = QTableWidgetItem(f"{hit.stars:.2f}")
            stars_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.results_table.setItem(row, 3, stars_item)

        self.results_table.selectAll()

    def _on_download(self):
        selected_rows = set()
        for idx in self.results_table.selectedIndexes():
            selected_rows.add(idx.row())

        if not selected_rows:
            QMessageBox.information(self, "No selection", "Select maps to download first.")
            return

        hits_to_download = [self.current_hits[r] for r in sorted(selected_rows)
                            if r < len(self.current_hits)]

        if not hits_to_download:
            return

        self._set_busy(True)
        total = len(hits_to_download)
        self.progress_bar.setRange(0, total)
        self.progress_bar.setValue(0)
        self.progress_label.setText(f"Downloading 0/{total}...")

        self.download_worker = DownloadWorker(hits_to_download)
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
        msg = f"Done — {downloaded} downloaded, {skipped} skipped (dupes). Registry: {total} maps total."
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
